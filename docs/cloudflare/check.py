#!/usr/bin/env python3
"""
check.py: the HTTP checks of cutover.sh, kept out of bash so a dry run can test them.

Every request goes through `curl` (GET only; redirects are never followed, a redirect is compared by its status and
Location). curl can resolve names over DNS over HTTPS (--doh, skips the Mac's cached DNS) or be pointed at one server
address (--resolve-ip, which asks Cloudflare's edge directly and so needs no DNS propagation at all).

    check.py snapshot --origin URL --out FILE [--doh] [--alias URL ...] [--host H ...] PATH...
        Record status, type, a hash of the bytes and one of the visible text (HTML) of each path on ORIGIN, and the first
        hop of each --alias URL. Run against the live site BEFORE anything moves.

    check.py compare --live URL --stage URL [--doh] [--allow 'PATH|reason' ...] [--host H ...] PATH...
        The staged Worker must answer every path like the live site (status, type, bytes, redirects).

    check.py verify --baseline FILE --origin URL [--doh | --resolve-ip IP] [--allow 'PATH|reason' ...] [--host H ...]
                    [--expect-no-vercel] [--expect-cloudflare] [--expect-text S ...] [--forbid-text S ...]
        After the move: ORIGIN must answer every recorded path and alias like the snapshot did. The first path must not
        come from Vercel (--expect-no-vercel), must come from Cloudflare (--expect-cloudflare), must contain every
        --expect-text and none of the --forbid-text (the contact address must not be rewritten).

Exit status 0 when everything matches (an --allow'ed difference is printed, not counted), 1 otherwise.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parity import Page, ctype_base, mask  # noqa: E402  (same visible-text and 404 rules as the parity report)

DOH = "https://cloudflare-dns.com/dns-query"


def fetch(url, doh=False, resolve_ip=None, hosts=(), timeout=60):
    """One GET with curl, no redirect following. Returns (status, headers, body); status 0 = no answer."""
    with tempfile.TemporaryDirectory() as d:
        body_f, head_f = os.path.join(d, "body"), os.path.join(d, "head")
        cmd = ["curl", "-s", "--max-time", str(timeout), "-o", body_f, "-D", head_f, "-w", "%{http_code}"]
        if doh:
            cmd += ["--doh-url", DOH]
        if resolve_ip:
            for h in sorted(hosts):
                cmd += ["--resolve", f"{h}:443:{resolve_ip}", "--resolve", f"{h}:80:{resolve_ip}"]
        cmd.append(url)
        r = subprocess.run(cmd, capture_output=True, text=True)
        out = (r.stdout or "").strip()
        status = int(out) if out.isdigit() else 0
        headers = {}
        if os.path.exists(head_f):
            for line in open(head_f, errors="replace").read().splitlines():
                if ":" in line and not line.startswith("HTTP/"):
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
        body = open(body_f, "rb").read() if os.path.exists(body_f) else b""
    return status, headers, body


def digest(body, headers, hosts):
    """Visible-text hash for HTML, byte hash for everything else (host names normalised)."""
    base = ctype_base(headers)
    if base in ("text/html", "application/xhtml+xml"):
        p = Page()
        p.feed(body.decode("utf-8", "replace"))
        return hashlib.sha256(p.visible().encode()).hexdigest()
    norm = mask(body)
    for h in hosts:
        norm = norm.replace(h.encode(), b"{site}")
    return hashlib.sha256(norm).hexdigest()


def describe(url, hosts, doh, resolve_ip=None, expect=(), forbid=()):
    status, headers, body = fetch(url, doh, resolve_ip, hosts)
    loc = headers.get("location")
    if loc:
        loc = urllib.parse.urljoin(url, loc)
        p = urllib.parse.urlsplit(loc)
        # keep the absolute target of a redirect to ANOTHER host, make same-host redirects host independent
        loc = ("{site}" if p.netloc.lower() in hosts else f"{p.scheme}://{p.netloc}") + p.path + (f"?{p.query}" if p.query else "")
    text = body.decode("utf-8", "replace")
    return {
        "status": status,
        "type": ctype_base(headers),
        "sha": digest(body, headers, hosts) if status and not (300 <= status < 400) else None,
        "full": hashlib.sha256(mask(body)).hexdigest() if status and not (300 <= status < 400) else None,
        "location": loc,
        "vercel": bool(headers.get("x-vercel-id") or headers.get("server", "").lower() == "vercel"),
        "cloudflare": bool(headers.get("cf-ray") or headers.get("server", "").lower() == "cloudflare"),
        "missing_text": [s for s in expect if s not in text],
        "forbidden_text": [s for s in forbid if s in text],
    }


def host_of(url):
    return urllib.parse.urlsplit(url).netloc.lower()


def parse_allow(items):
    out = {}
    for a in items or []:
        k, _, v = a.partition("|")
        out[k] = v or "expected"
    return out


def same(a, b):
    """Why two descriptions differ, or None."""
    if a["status"] != b["status"]:
        return f"status {a['status']} -> {b['status']}"
    if a["location"] != b["location"]:
        return f"location {a['location']} -> {b['location']}"
    if a["type"] != b["type"] and not (300 <= a["status"] < 400):
        return f"type {a['type']} -> {b['type']}"
    if a["sha"] != b["sha"]:
        return "content differs"
    if a["full"] != b["full"]:
        return "bytes differ (the visible text is the same)"
    return None


def report(rows, allow):
    bad = 0
    for label, path, why in rows:
        if why is None:
            print(f"  ok      {label}")
        elif path in allow:
            print(f"  allowed {label}: {why} ({allow[path]})")
        else:
            bad += 1
            print(f"  DIFF    {label}: {why}")
    return bad


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot")
    s.add_argument("--origin", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--doh", action="store_true")
    s.add_argument("--alias", action="append", default=[])
    s.add_argument("--host", action="append", default=[], help="extra hostnames to treat as the same site")
    s.add_argument("paths", nargs="+")

    c = sub.add_parser("compare")
    c.add_argument("--live", required=True)
    c.add_argument("--stage", required=True)
    c.add_argument("--doh", action="store_true")
    c.add_argument("--allow", action="append", default=[])
    c.add_argument("--host", action="append", default=[])
    c.add_argument("paths", nargs="+")

    v = sub.add_parser("verify")
    v.add_argument("--baseline", required=True)
    v.add_argument("--origin", required=True)
    v.add_argument("--doh", action="store_true")
    v.add_argument("--resolve-ip")
    v.add_argument("--allow", action="append", default=[])
    v.add_argument("--host", action="append", default=[])
    v.add_argument("--expect-no-vercel", action="store_true")
    v.add_argument("--expect-cloudflare", action="store_true")
    v.add_argument("--expect-text", action="append", default=[])
    v.add_argument("--forbid-text", action="append", default=[])

    a = ap.parse_args()

    if a.cmd == "snapshot":
        origin = a.origin.rstrip("/")
        hosts = {host_of(origin), *a.host}
        snap = {"origin": origin, "pages": {}, "aliases": {}}
        for p in a.paths:
            snap["pages"][p] = describe(origin + p, hosts, a.doh)
        for u in a.alias:
            snap["aliases"][u] = describe(u, hosts, a.doh)
        bad = [p for p, d in snap["pages"].items() if d["status"] == 0]
        bad += [u for u, d in snap["aliases"].items() if d["status"] == 0]
        json.dump(snap, open(a.out, "w"), indent=1)
        for p, d in snap["pages"].items():
            print(f"  {d['status']} {d['type'] or '-':<26} {p}")
        for u, d in snap["aliases"].items():
            print(f"  {d['status']} {d['location'] or '-':<40} {u}")
        if bad:
            print("  no answer from: " + ", ".join(bad))
            sys.exit(1)
        return

    if a.cmd == "compare":
        live, stage = a.live.rstrip("/"), a.stage.rstrip("/")
        hosts = {host_of(live), host_of(stage), *a.host}
        allow = parse_allow(a.allow)
        rows = []
        for p in a.paths:
            l, s_ = describe(live + p, hosts, a.doh), describe(stage + p, hosts, a.doh)
            rows.append((f"{p:<56} live {l['status']} stage {s_['status']}", p, same(l, s_) if l["status"] else "no answer from live"))
        sys.exit(1 if report(rows, allow) else 0)

    if a.cmd == "verify":
        snap = json.load(open(a.baseline))
        origin = a.origin.rstrip("/")
        hosts = {host_of(snap["origin"]), host_of(origin), *a.host}
        allow = parse_allow(a.allow)
        rows = []
        first = True
        backend = "nobody"
        for p, before in snap["pages"].items():
            now = describe(origin + p, hosts, a.doh, a.resolve_ip, a.expect_text if first else (), a.forbid_text if first else ())
            why = None
            if first:
                backend = "Vercel" if now["vercel"] else "Cloudflare" if now["cloudflare"] else "nobody" if not now["status"] else "somebody else"
            if not now["status"]:
                why = "no answer"
            elif first:
                # the most specific reason first: who answered, and whether the contact address survived
                if a.expect_no_vercel and now["vercel"]:
                    why = "still answered by Vercel (x-vercel-id / server: Vercel)"
                elif a.expect_cloudflare and not now["cloudflare"]:
                    why = "not answered by Cloudflare (no cf-ray / server: cloudflare)"
                elif now["missing_text"] or now["forbidden_text"]:
                    bits = []
                    if now["missing_text"]:
                        bits.append(f"the page no longer contains {now['missing_text']}")
                    if now["forbidden_text"]:
                        bits.append(f"the page now contains {now['forbidden_text']}")
                    why = "; ".join(bits) + " (the contact address was rewritten?)"
            if why is None and now["status"]:
                why = same(before, now)
            first = False
            rows.append((f"{p:<56} {before['status']} -> {now['status']}", p, why))
        for u, before in snap["aliases"].items():
            now = describe(u, hosts, a.doh, a.resolve_ip)
            rows.append((f"{u:<64} {before['status']} -> {now['status']}", u, same(before, now) if now["status"] else "no answer"))
        bad = report(rows, allow)
        print(f"  first page answered by: {backend}")
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
