#!/usr/bin/env python3
"""
parity.py: compare the LIVE marvbrownfilms.com (still on Vercel) with the STAGED Cloudflare Worker, GET requests only.

    python3 parity.py --live https://marvbrownfilms.com --stage https://marv-brown-films-web.chewybytes.workers.dev \
        [--max 150] [--extra /path ...] [--gone '/path|reason' ...] [--allow '/path|reason' ...] [--out report.json] [--md report.md]

Path list = "/", /robots.txt, /sitemap.xml, /favicon.ico, a made-up 404 path, every <loc> of a live sitemap (there is
none), then a breadth-first crawl of the internal links and asset references found in live HTML (capped at --max),
then --extra. Unlike a rebuilt site, the staged one carries the SAME hashed file names as the live one, so /_next/static/*
is compared byte for byte like everything else.

For each path, on both sides: first-hop status and Location, final status and URL after redirects (host names replaced
by {site}), content type, cache-control, HSTS, and a SHA-256 of the bytes (a 404 page has its request id masked). HTML
also gets a SHA-256 of its visible text, its head fingerprint and the set of references. Then:
  * every internal reference found in the STAGED HTML must answer 200 on the stage host ("stage self-check")
  * --gone 'PATH|reason': a path that answers on live but is deliberately not carried over; the stage must answer 404
  * compression (Accept-Encoding: gzip, br) is compared for one sample per content type
Exit status 0 when nothing differs except what --allow / --gone explain, 1 otherwise.
"""
import argparse
import hashlib
import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36 parity-check"
REDIRECTS = (301, 302, 303, 307, 308)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # noqa: D401
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def get(url, accept_encoding="identity", timeout=60, retries=2):
    """One GET, never following redirects. Returns (status, headers(lowercase), body)."""
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": accept_encoding})
        try:
            r = _opener.open(req, timeout=timeout)
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
        except urllib.error.HTTPError as e:
            return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()
        except Exception as e:  # network hiccup: retry
            last = e
            time.sleep(1.5 * (attempt + 1))
    return 0, {"x-error": repr(last)}, b""


def follow(url, accept_encoding="identity", max_hops=6):
    """GET with manual redirect following. Returns list of hops [(url, status, headers, body)]."""
    hops = []
    cur = url
    for _ in range(max_hops):
        status, headers, body = get(cur, accept_encoding)
        hops.append((cur, status, headers, body))
        loc = headers.get("location")
        if status in REDIRECTS and loc:
            cur = urllib.parse.urljoin(cur, loc)
            continue
        break
    return hops


class Page(HTMLParser):
    """Visible text, head fingerprint and asset references of one HTML document."""

    SKIP = {"script", "style", "template", "noscript"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.skip = 0
        self.title = None
        self.in_title = False
        self.meta = {}
        self.refs = set()
        self.links = set()
        self.lang = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang")
        if tag in self.SKIP:
            self.skip += 1
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            key = a.get("name") or a.get("property") or a.get("http-equiv")
            if key and "content" in a:
                key = key.lower()
                if key in ("description", "robots", "viewport", "theme-color") or key.startswith(("og:", "twitter:")):
                    self.meta[key] = a["content"]
            if "charset" in a or "charSet" in a:
                self.meta["charset"] = a.get("charset") or a.get("charSet")
        if tag == "link" and a.get("rel"):
            rel = a["rel"].lower()
            if rel in ("canonical", "icon", "apple-touch-icon", "shortcut icon", "manifest", "alternate"):
                self.meta["link:" + rel + ":" + (a.get("type") or a.get("hreflang") or a.get("sizes") or "")] = a.get("href", "")
        for k in ("href", "src", "poster", "data-src"):
            if a.get(k):
                self.refs.add(a[k])
                if tag == "a":
                    self.links.add(a[k])
        for k in ("srcset", "imagesrcset"):
            if a.get(k):
                for part in a[k].split(","):
                    u = part.strip().split(" ")[0]
                    if u:
                        self.refs.add(u)

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title = (self.title or "") + data
        elif not self.skip:
            self.text.append(data)

    def visible(self):
        return re.sub(r"\s+", " ", " ".join(self.text)).strip()


def sha(b):
    return hashlib.sha256(b).hexdigest()


# Two spellings of the same type; browsers treat them alike, so they are not a difference.
MIME_SYNONYMS = {
    "image/x-icon": "image/vnd.microsoft.icon",
    "application/javascript": "text/javascript",
    "application/x-javascript": "text/javascript",
    "text/xml": "application/xml",
}


def ctype_base(h):
    t = (h.get("content-type") or "").split(";")[0].strip().lower()
    return MIME_SYNONYMS.get(t, t)


NOT_FOUND_ID = re.compile(rb"(The page could not be found\n\nNOT_FOUND\n\n)[^\n]*(\n?)$")


def mask(body):
    """The 404 page ends with the id of the request that produced it: mask it, it cannot be equal."""
    return NOT_FOUND_ID.sub(rb"\1{request-id}\2", body)


def describe(base, hops, hosts):
    """Summarise a fetch chain for comparison."""
    first_url, st0, h0, _ = hops[0]
    last_url, st, h, body = hops[-1]

    def nrm(u):
        p = urllib.parse.urlsplit(u)
        host = p.netloc.lower()
        path = p.path + ("?" + p.query if p.query else "")
        return ("{site}" if host in hosts else p.scheme + "://" + host) + path

    d = {
        "first_status": st0,
        "first_location": nrm(urllib.parse.urljoin(first_url, h0["location"])) if h0.get("location") else None,
        "hops": len(hops) - 1,
        "status": st,
        "final": nrm(last_url),
        "ctype": h.get("content-type"),
        "ctype_base": ctype_base(h),
        "cache": h.get("cache-control"),
        "hsts": h.get("strict-transport-security"),
        "length": len(body),
        "etag": bool(h.get("etag")),
    }
    norm = mask(body)
    d["body_sha"] = sha(norm)
    if d["ctype_base"] in ("text/html", "application/xhtml+xml"):
        p = Page()
        try:
            p.feed(body.decode("utf-8", "replace"))
        except Exception as e:  # malformed HTML: still compare bytes
            d["parse_error"] = repr(e)
        txt = p.visible()
        d["text_len"] = len(txt)
        d["text_sha"] = sha(txt.encode())
        d["title"] = (p.title or "").strip()
        d["head"] = dict(sorted({**p.meta, "lang": p.lang}.items()))
        d["refs"] = sorted(p.refs)
    return d


def internal_path(ref, base_url, hosts):
    if not ref or ref.startswith(("#", "mailto:", "tel:", "javascript:", "data:", "blob:", "sms:", "whatsapp:")):
        return None
    u = urllib.parse.urlsplit(urllib.parse.urljoin(base_url, ref))
    if u.scheme not in ("http", "https") or u.netloc.lower() not in hosts:
        return None
    return (u.path or "/") + ("?" + u.query if u.query else "")


def parse_pairs(items):
    out = {}
    for a in items:
        k, _, v = a.partition("|")
        out[k] = v or "expected"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--alias", action="append", default=[], help="other live hostnames that count as the same site")
    ap.add_argument("--max", type=int, default=150)
    ap.add_argument("--extra", action="append", default=[])
    ap.add_argument("--gone", action="append", default=[], help="'PATH|reason': served by live, deliberately absent from the stage (must 404)")
    ap.add_argument("--allow", action="append", default=[], help="'PATH|reason': a difference that is expected")
    ap.add_argument("--no-crawl", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--md")
    args = ap.parse_args()
    allow, gone = parse_pairs(args.allow), parse_pairs(args.gone)

    live, stage = args.live.rstrip("/"), args.stage.rstrip("/")
    live_host, stage_host = urllib.parse.urlsplit(live).netloc.lower(), urllib.parse.urlsplit(stage).netloc.lower()
    hosts = {live_host, stage_host, *[x.lower() for x in args.alias]}
    missing = "/nope-404-" + "".join(random.choice("abcdefghij") for _ in range(6))

    paths = ["/", "/robots.txt", "/sitemap.xml", "/favicon.ico", missing, *args.extra, *gone]
    paths = list(dict.fromkeys(paths))

    # crawl: internal links and asset references found in live HTML, breadth first
    if not args.no_crawl:
        queue, i = list(paths), 0
        while i < len(queue) and len(paths) < args.max:
            p = queue[i]
            i += 1
            hops = follow(live + p)
            _, st, h, body = hops[-1]
            if st != 200 or ctype_base(h) != "text/html":
                continue
            page = Page()
            page.feed(body.decode("utf-8", "replace"))
            for ref in sorted(page.refs):
                ip = internal_path(ref, hops[-1][0], hosts)
                if ip and ip not in paths and len(paths) < args.max:
                    paths.append(ip)
                    queue.append(ip)

    rows, stage_refs = [], set()
    for p in paths:
        ld, sd = describe(live, follow(live + p), hosts), describe(stage, follow(stage + p), hosts)
        row = {"path": p, "live": ld, "stage": sd, "diff": []}

        def cmp(name, a, b, severity="DIFF"):
            if a != b:
                row["diff"].append({"field": name, "live": a, "stage": b, "severity": severity})

        if p in gone:
            if sd["status"] != 404:
                row["diff"].append({"field": "gone", "live": ld["status"], "stage": sd["status"], "severity": "DIFF", "reason": "must be absent from the stage"})
            else:
                row["diff"].append({"field": "gone", "live": ld["status"], "stage": 404, "severity": "allowed", "reason": gone[p]})
            rows.append(row)
            continue
        cmp("first_status", ld["first_status"], sd["first_status"])
        cmp("first_location", ld["first_location"], sd["first_location"])
        cmp("status", ld["status"], sd["status"])
        cmp("final_url", ld["final"], sd["final"])
        cmp("content_type", ld["ctype_base"], sd["ctype_base"])
        if ld["ctype"] != sd["ctype"] and ld["ctype_base"] == sd["ctype_base"]:
            row["diff"].append({"field": "content-type header", "live": ld["ctype"], "stage": sd["ctype"], "severity": "minor"})
        cmp("body_sha", ld["body_sha"], sd["body_sha"])
        if ld["ctype_base"] == "text/html" and sd["ctype_base"] == "text/html":
            cmp("visible_text_sha", ld["text_sha"], sd["text_sha"])
            cmp("head", ld.get("head"), sd.get("head"))
            if set(ld.get("refs", [])) != set(sd.get("refs", [])):
                row["diff"].append({"field": "refs", "live_only": sorted(set(ld["refs"]) - set(sd["refs"]))[:8], "stage_only": sorted(set(sd["refs"]) - set(ld["refs"]))[:8], "severity": "DIFF"})
        cmp("cache_control", ld["cache"], sd["cache"], "cache")
        cmp("hsts", ld["hsts"], sd["hsts"], "minor")
        if sd["status"] == 200 and sd["ctype_base"] == "text/html":
            for r in sd.get("refs", []):
                ip = internal_path(r, stage + p, {stage_host})
                if ip:
                    stage_refs.add(ip)
        if p in allow:
            for d in row["diff"]:
                if d["severity"] == "DIFF":
                    d["severity"] = "allowed"
                    d["reason"] = allow[p]
        rows.append(row)

    # every internal reference in the staged HTML must resolve on the stage host
    broken, dangling = [], []
    for ip in sorted(stage_refs):
        st = get(stage + ip)[0]
        if st != 200:
            live_st = get(live + ip)[0]
            # a reference that is just as dead on the live site (here /static/favicon.ico) is carried over as it was
            (dangling if live_st == st else broken).append({"path": ip, "status": st, "live_status": live_st})

    # compression: ask with gzip, br for one sample per type
    comp, samples = {}, {}
    for r in rows:
        if r["stage"]["status"] == 200:
            samples.setdefault(r["stage"]["ctype_base"], r["path"])
    for ctype, p in sorted(samples.items()):
        ls, lh, lb = get(live + p, "gzip, br")
        ss, sh, sb = get(stage + p, "gzip, br")
        comp[ctype] = {"path": p, "live": lh.get("content-encoding"), "stage": sh.get("content-encoding"), "live_bytes": len(lb), "stage_bytes": len(sb)}

    # first hop of the other live addresses (the stage cannot be asked these on workers.dev: worker.test.mjs checks the Worker)
    probes = []
    for url in (
        "https://www.marvbrownfilms.com/x-check?x=1",
        "https://www.marvbrownfilms.com/",
        "http://marvbrownfilms.com/x-check?x=1",
        "http://www.marvbrownfilms.com/x-check?x=1",
        "https://marvbrownfilms.com//x-check?x=1",
        "https://www.marvbrownfilms.com//x-check?x=1",
    ):
        st, hd, body = get(url)
        probes.append({"url": url, "status": st, "location": hd.get("location"), "type": hd.get("content-type"), "cache": hd.get("cache-control"), "hsts": hd.get("strict-transport-security"), "body": body.decode("utf-8", "replace")[:30]})

    hard = [r for r in rows if any(d["severity"] == "DIFF" for d in r["diff"])]
    report = {"live": live, "stage": stage, "paths": len(rows), "paths_with_differences": len(hard), "rows": rows, "stage_self_check": {"internal_refs": len(stage_refs), "broken": broken, "dead_on_live_too": dangling}, "compression": comp, "redirect_probes": probes}
    if args.out:
        json.dump(report, open(args.out, "w"), indent=1)

    lines = [f"Parity: {live}  vs  {stage}  ({len(rows)} paths, {len(hard)} with differences)", "", "| path | status live/stage | final URL same | content-type | bytes | visible text | cache-control live | cache-control stage | notes |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        ld, sd = r["live"], r["stage"]
        notes = []
        for d in r["diff"]:
            if d.get("severity") == "allowed":
                notes.append("expected: " + d["reason"])
            elif d["field"] in ("first_status", "first_location", "status", "final_url", "content_type", "body_sha", "visible_text_sha", "cache_control", "gone"):
                continue
            elif d["field"] == "refs":
                notes.append(f"refs: live-only {d['live_only'][:2]} stage-only {d['stage_only'][:2]}")
            elif d["field"] == "head":
                notes.append("head differs")
            else:
                notes.append(f"{d['field']}: {d['live']!r} vs {d['stage']!r}")
        notes = list(dict.fromkeys(notes))
        sf = "same" if ld["final"] == sd["final"] else f"{ld['final']} vs {sd['final']}"
        ct = ld["ctype_base"] if ld["ctype_base"] == sd["ctype_base"] else f"{ld['ctype_base']} vs {sd['ctype_base']}"
        bs = f"identical ({ld['length']} B)" if ld["body_sha"] == sd["body_sha"] else f"DIFFERENT ({ld['length']} B vs {sd['length']} B)"
        ts = "-" if "text_sha" not in ld or "text_sha" not in sd else ("same" if ld["text_sha"] == sd["text_sha"] else "DIFF")
        st = f"{ld['status']}/{sd['status']}" + (f" (first {ld['first_status']}/{sd['first_status']})" if (ld["first_status"] != ld["status"] or sd["first_status"] != sd["status"]) else "")
        lines.append(f"| {r['path']} | {st} | {sf} | {ct} | {bs} | {ts} | {ld['cache']} | {sd['cache']} | {'; '.join(notes)} |")
    lines += ["", f"Stage self-check: {len(stage_refs)} internal references in the staged HTML, {len(broken)} broken {broken[:10]}; {len(dangling)} answer the same dead status on live (kept as they were) {dangling[:10]}", ""]
    lines.append("Compression (Accept-Encoding: gzip, br), content-encoding live/stage: " + ", ".join(f"{k}: {v['live']}/{v['stage']}" for k, v in comp.items()))
    lines += ["", "Other live addresses, first hop only (what the Worker must reproduce; worker.test.mjs checks the same URLs against the Worker code):"]
    for pr in probes:
        lines.append(f"- {pr['url']} -> {pr['status']} {pr['location'] or ''} ({pr['type']}; body {pr['body']!r})")
    md = "\n".join(lines)
    print(md)
    if args.md:
        open(args.md, "w").write(md + "\n")
    sys.exit(1 if hard or broken else 0)


if __name__ == "__main__":
    main()
