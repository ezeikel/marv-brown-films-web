#!/usr/bin/env python3
"""
Dry run of docs/cloudflare/cutover.sh. Every external tool is replaced by a shim on PATH (curl, dig, git, npx, node,
sleep, tlscheck; vercel, gh and op record a violation if they are ever called), so nothing real is contacted and nothing
real is changed. The REAL script runs, under the same /bin/bash the owner will use, and so do its helpers (check.py,
dns/verify-dns.py) on the real python3, against a small simulated world:

  * Cloudflare: the token, the zone (pending or active) with its mail records (5 MX, 3 CAA) and the temporary Vercel
    records (apex and www CNAME, wildcard), Worker custom domains that refuse to attach while a record still sits on
    the name (as the real API does), and the authoritative answers that follow from all that, with a propagation lag
  * the .com registry and three public resolvers, each with the delegation they currently show
  * Vercel's six nameservers and its web edge, Cloudflare's edge (with or without a certificate yet) and the Worker
  * git (branch, clean tree, pushed) and the build tools

    python3 docs/cloudflare/dry-run/run.py                  # every scenario (KEEP=1 keeps the temporary worlds for a look)
    python3 docs/cloudflare/dry-run/run.py attach           # scenarios whose name contains "attach"
    CUTOVER_SCRIPT=/path/mutated.sh python3 .../run.py      # mutation checks: the run must then FAIL

The same file is the shim: the wrappers in the world's bin/ call `run.py --shim <tool> <args>`.
"""
import atexit
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("CUTOVER_SCRIPT") or os.path.join(HERE, "..", "cutover.sh")
CF_TOKEN = "tok-dry-run-cf-1234567890"
ACCOUNT = "acc00000000000000000000000000001"
SECRETS = (CF_TOKEN,)
SHA = "a" * 40
RESOLVERS = ("1.1.1.1", "8.8.8.8", "9.9.9.9")
CREATED = []          # the temporary worlds this run made; removed at exit unless KEEP=1 (they hold no secrets)


@atexit.register
def _cleanup():
    if not os.environ.get("KEEP"):
        for d in CREATED:
            shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------------------- shared state
class World:
    def __init__(self, path=None):
        self.path = path or os.path.join(os.environ["WORLD"], "state.json")
        with open(self.path) as f:
            self.st = json.load(f)

    def save(self):
        with open(self.path, "w") as f:
            json.dump(self.st, f)

    def log(self, *a):
        self.st["calls"].append(" ".join(str(x) for x in a))


def new_state(cfg, scenario):
    recs, nid = [], 1

    def add(**r):
        nonlocal nid
        recs.append({"id": f"rec{nid}", **r})
        nid += 1

    for prio, host in cfg["mx"]:
        add(type="MX", name=cfg["apex"], content=host, priority=prio, ttl=60, proxied=False, comment="")
    for v in cfg["caa"]:
        add(type="CAA", name=cfg["apex"], content=f'0 issue "{v}"', data={"flags": 0, "tag": "issue", "value": v}, ttl=60, proxied=False, comment="")
    for t in cfg["temp_records"]:
        add(**copy.deepcopy(t))
    return {
        "scenario": scenario,
        "calls": [],
        "clock": 0,
        "violations": [],
        "git": {"branch": cfg["branch"], "dirty": False, "head": SHA, "origin_branch": SHA},
        "cf": {"token": "active", "zone_status": "active", "records": recs, "next_id": nid, "domains": [], "attach_fail": None, "delete_fail": None,
               "conflict_service": None, "activation_checks": 0, "zone_reads": 0, "active_after_reads": 0, "managed_robots": False,
               "snapshot_before_first_delete": None, "first_delete_seen": False},
        "net": {"registry_ns": list(cfg["cf_ns"]), "resolver_ns": {r: list(cfg["cf_ns"]) for r in RESOLVERS}, "worker_deployed": True, "worker_mutation": None, "custom_mutation": None,
                "tls_ready": True, "tls_checks": 0, "tls_ready_after": 0, "lag": 3, "lag_left": None, "stale": {}, "mx_drop_on_attach": False},
        "tools": {"installs": 0, "deploys": 0, "tests_fail": False, "deploy_fails": False, "tests": 0},
    }


# --------------------------------------------------------------------------------------- DNS model
def dom_attached(st, cfg, host):
    return any(d["hostname"] == host and d["service"] == cfg["worker"] for d in st["cf"]["domains"])


def web_state(st, cfg, host):
    """What the zone's nameservers say about HOST right now: worker | temp | none. A host that has just moved keeps
    answering its old (temporary) record for `lag` lookups, like a nameserver that has not heard of the change yet."""
    if dom_attached(st, cfg, host):
        left = st["net"]["lag_left"]
        if left is not None and left > 0:
            st["net"]["lag_left"] = left - 1
            return "temp" if host in st["net"]["stale"] else "worker"
        return "worker"
    if any(r["name"] == host and r["type"] in ("A", "AAAA", "CNAME") for r in st["cf"]["records"]):
        return "temp"
    return "none"


def cname_of(st, host):
    r = next((r for r in st["cf"]["records"] if r["name"] == host and r["type"] == "CNAME"), None)
    if r:
        return r["content"]
    return st["net"]["stale"].get(host)


def fq(n):
    return n.rstrip(".").lower()


def cf_auth(st, cfg, name, rtype):
    """-> list of (name, ttl, type, rdata) from Cloudflare's nameservers."""
    n, apex, www = fq(name), cfg["apex"], cfg["www"]
    recs = st["cf"]["records"]
    out = []
    if n == apex:
        if rtype == "NS":
            out = [(apex, 86400, "NS", h + ".") for h in cfg["cf_ns"]]
        elif rtype == "MX":
            out = [(apex, r["ttl"], "MX", f"{r['priority']} {r['content']}.") for r in recs if r["type"] == "MX" and r["name"] == apex]
        elif rtype == "TXT":
            out = [(apex, r["ttl"], "TXT", f'"{r["content"]}"') for r in recs if r["type"] == "TXT" and r["name"] == apex]
        elif rtype == "CAA":
            own = [r for r in recs if r["type"] == "CAA" and r["name"] == apex]
            out = [(apex, r["ttl"], "CAA", f'{r["data"]["flags"]} {r["data"]["tag"]} "{r["data"]["value"]}"') for r in own]
            if own:
                out += [(apex, 60, "CAA", f'{f} {t} "{v}"') for f, t, v in cfg["caa_extra_by_cloudflare"]]
        elif rtype == "A":
            s = web_state(st, cfg, apex)
            ips = cfg["cf_edge_ips"] if s == "worker" else cfg["vercel_ips"] if s == "temp" else []
            out = [(apex, 300, "A", ip) for ip in ips]
    elif n == www and rtype in ("A", "CNAME"):
        s = web_state(st, cfg, www)
        if s == "worker" and rtype == "A":
            out = [(www, 300, "A", ip) for ip in cfg["cf_edge_ips"]]
        elif s == "temp":
            out = [(www, 300, "CNAME", cname_of(st, www) + ".")]
    elif n.endswith("." + apex) and rtype in ("A", "CNAME") and n != www:
        w = cname_of(st, "*." + apex)
        if w:
            out = [(n, 300, "CNAME", w + ".")]
    return out


def vercel_auth(cfg, name, rtype):
    n, apex = fq(name), cfg["apex"]
    if n == apex:
        if rtype == "NS":
            return [(apex, 86400, "NS", "ns1.vercel-dns.com."), (apex, 86400, "NS", "ns2.vercel-dns.com.")]
        if rtype == "MX":
            return [(apex, 60, "MX", f"{p} {h}.") for p, h in cfg["mx"]]
        if rtype == "CAA":
            return [(apex, 60, "CAA", f'0 issue "{v}"') for v in cfg["caa"]]
        if rtype == "A":
            return [(apex, 1800, "A", ip) for ip in cfg["vercel_ips"]]
    elif n.endswith("." + apex) and rtype == "A":
        return [(n, 1800, "A", ip) for ip in cfg["vercel_ips"]]
    return []


def dig_answer(st, cfg, server, name, rtype):
    """-> (answer lines, authority lines) as (name, ttl, type, rdata)."""
    server, n = fq(server), fq(name)
    if server == "a.gtld-servers.net":
        if n == cfg["apex"] and rtype == "NS":
            return [], [(cfg["apex"], 172800, "NS", h + ".") for h in st["net"]["registry_ns"]]
        return [], []
    if n == f"{cfg['worker']}.chewybytes.workers.dev":
        return [(n, 300, "A", cfg["cf_edge_ips"][0])], []
    if server in cfg["cf_ns"]:
        return cf_auth(st, cfg, n, rtype), []
    if server in cfg["vercel_ns"]:
        return vercel_auth(cfg, n, rtype), []
    if server in RESOLVERS:
        if n == cfg["apex"] and rtype == "NS":
            return [(cfg["apex"], 3600, "NS", h + ".") for h in st["net"]["resolver_ns"][server]], []
        if st["net"]["resolver_ns"][server] == cfg["cf_ns"]:
            return cf_auth(st, cfg, n, rtype), []
        return vercel_auth(cfg, n, rtype), []
    return [], []


def shim_dig(w, cfg, args):
    server, rest = None, []
    for a in args:
        if a.startswith("@"):
            server = a[1:]
        elif not a.startswith("+"):
            rest.append(a)
    flags = [a for a in args if a.startswith("+")]
    name, rtype = rest[0], (rest[1] if len(rest) > 1 else "A")
    w.log("dig", server, name, rtype)
    answer, authority = dig_answer(w.st, cfg, server, name, rtype)
    if "+short" in flags:
        for _, _, _, rd in answer:
            print(rd)
        return 0
    show_auth = "+authority" in flags
    for lines in (answer, authority if show_auth else []):
        for nm, ttl, t, rd in lines:
            print(f"{nm}.\t{ttl}\tIN\t{t}\t{rd}")
    return 0


# --------------------------------------------------------------------------------------- web model
def page_for(cfg, key):
    p = cfg["pages"].get(key)
    if p and "same_as" in p:
        base = cfg["pages"][p["same_as"]]
        return {**base, **{k: v for k, v in p.items() if k != "same_as"}}
    return p


def site(st, cfg, backend, host, scheme, rawpath):
    """The answer of Vercel / the Worker for a request. -> (status, headers, body bytes)."""
    path, _, q = rawpath.partition("?")
    qs = ("?" + q) if q else ""
    worker = backend in ("cf_edge", "workerdev")
    mut = st["net"]["worker_mutation"] if worker else None
    if backend == "cf_edge" and st["net"]["custom_mutation"]:
        mut = st["net"]["custom_mutation"]
    srv = {"server": "cloudflare", "cf-ray": "dryrun-LHR"} if worker else {"server": "Vercel", "x-vercel-id": "lhr1::dry"}
    hsts = {"strict-transport-security": "max-age=63072000"}
    cache = {"cache-control": "public, max-age=0, must-revalidate"}
    if scheme == "http":
        return 308, {**srv, "location": f"https://{host}{rawpath}", "content-type": "text/plain"}, b"Redirecting..."
    if "//" in path:
        return 308, {**srv, **hsts, **cache, "location": re.sub(r"/{2,}", "/", path) + qs, "content-type": "text/plain"}, b"Redirecting...\n"
    if host == cfg["www"]:
        return (302 if mut == "wrong_redirect" else 307), {**srv, **hsts, **cache, "location": f"https://{cfg['apex']}{path}{qs}", "content-type": "text/plain"}, b"Redirecting...\n"
    key = path.rstrip("/") or "/"
    pg = page_for(cfg, key) if key != "/index.html" else None
    if key == "/robots.txt" and (backend == "workerdev" or (backend == "cf_edge" and st["cf"]["managed_robots"])):
        return 200, {**srv, **hsts, **cache, "content-type": "text/plain; charset=utf-8"}, b"# Cloudflare managed robots.txt"
    if pg:
        ctype = pg["type"]
        text = pg["text"]
        if ctype == "text/html":
            if mut == "text":
                text += " EXTRA"
            mail = '<a href="mailto:info@marvbrownfilms.com">info@marvbrownfilms.com</a>'
            if mut == "email":
                mail = '<a href="/cdn-cgi/l/email-protection#ab12">[email protected]</a>'
            body = f"<!DOCTYPE html><html><head><title>t</title></head><body><main>{text}</main>{mail}</body></html>".encode()
            return 200, {**srv, **hsts, **cache, "content-type": "text/html; charset=utf-8"}, body
        return 200, {**srv, **hsts, **cache, "content-type": ctype + ("; charset=utf-8" if "javascript" in ctype else "")}, text.encode()
    rid = "cf-ray-1234" if worker else "lhr1::abcde-1793430000000-0123456789ab"
    return 404, {**srv, **hsts, **cache, "content-type": "text/plain; charset=utf-8"}, f"The page could not be found\n\nNOT_FOUND\n\n{rid}\n".encode()


def resolve_host(w, cfg, host, resolve_map, doh):
    """-> ip the request would go to, or None."""
    st = w.st
    if host == f"{cfg['worker']}.chewybytes.workers.dev":
        return cfg["cf_edge_ips"][0]
    for port in (443, 80):
        if (host, port) in resolve_map:
            return resolve_map[(host, port)]
    if doh:
        ans, _ = dig_answer(st, cfg, "1.1.1.1", host, "A")
        for _, _, t, rd in ans:
            if t == "A":
                return rd
            if t == "CNAME":
                return cfg["vercel_ips"][0]            # the resolver follows the temporary CNAME to Vercel
        return None
    return None


def backend_for(w, cfg, host, ip):
    st = w.st
    if host == f"{cfg['worker']}.chewybytes.workers.dev":
        return "workerdev" if st["net"]["worker_deployed"] else None
    if ip in cfg["vercel_ips"]:
        return "vercel"
    if ip in cfg["cf_edge_ips"]:
        if not st["net"]["tls_ready"] and not dom_attached(st, cfg, host):
            return None                                     # no certificate and no Worker domain: the handshake fails
        return "cf_edge" if dom_attached(st, cfg, host) else "cf_unattached"
    return None


# --------------------------------------------------------------------------------------- shims
def shim_curl(w, cfg, args):
    for a in args:
        if any(s in a for s in SECRETS):
            w.st["violations"].append("a secret value is in a curl argument")
    opts = {"-X": None, "--data": None, "-o": None, "-D": None, "-w": None, "--max-time": None, "--doh-url": None}
    headers, resolve_map, i, url = [], {}, 0, None
    while i < len(args):
        a = args[i]
        if a in opts:
            opts[a] = args[i + 1]
            i += 2
            continue
        if a == "-H":
            headers.append(args[i + 1])
            i += 2
            continue
        if a == "--resolve":
            h, p, ip = args[i + 1].split(":")
            resolve_map[(h, int(p))] = ip
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        url = a
        i += 1
    u = urllib.parse.urlsplit(url)
    method = opts["-X"] or "GET"
    if u.netloc == "api.cloudflare.com":
        authed = False
        for h in headers:
            if h.startswith("@"):
                authed = open(h[1:]).read().strip() == f"Authorization: Bearer {CF_TOKEN}"
        if not authed:
            sys.stdout.write(json.dumps({"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]}))
            return 0
        sys.stdout.write(json.dumps(cf_api(w, cfg, method, u.path.replace("/client/v4", ""), urllib.parse.parse_qs(u.query), opts["--data"])))
        return 0
    if method != "GET":
        w.st["violations"].append(f"a non-GET request to a website: {method} {url}")
    host = u.netloc.lower()
    ip = resolve_host(w, cfg, host, resolve_map, bool(opts["--doh-url"]))
    backend = backend_for(w, cfg, host, ip) if ip else None
    w.log("http", method, url, f"-> {backend}")
    status, hdrs, body = 0, {}, b""
    if backend == "cf_unattached":
        status, hdrs, body = 404, {"server": "cloudflare", "cf-ray": "dryrun-LHR", "content-type": "text/plain"}, b"error code: 1001"
    elif backend:
        status, hdrs, body = site(w.st, cfg, backend, host, u.scheme, u.path + (("?" + u.query) if u.query else ""))
    head = (f"HTTP/2 {status}\r\n" if status else "") + "".join(f"{k}: {v}\r\n" for k, v in hdrs.items()) + "\r\n"
    if opts["-o"] and opts["-o"] != "/dev/null":
        open(opts["-o"], "wb").write(body)
    if opts["-D"]:
        if opts["-D"] == "-":
            sys.stdout.write(head)
        else:
            open(opts["-D"], "w").write(head)
    if opts["-w"] and "%{http_code}" in opts["-w"]:
        sys.stdout.write(f"{status:03d}")
    if not opts["-o"] and not opts["-D"] and not opts["-w"]:
        sys.stdout.buffer.write(body)
    return 0


def cf_api(w, cfg, method, path, q, data):
    cf = w.st["cf"]
    w.log("cf", method, path + (("?" + urllib.parse.urlencode({k: v[0] for k, v in q.items()})) if q else ""))
    body = json.loads(data) if data else {}
    ok = lambda r=None: {"success": True, "errors": [], "result": r}
    bad = lambda code, msg: {"success": False, "errors": [{"code": code, "message": msg}], "result": None}
    if path == "/user/tokens/verify":
        return ok({"id": "t", "status": cf["token"]})
    zone = cfg["zone"]
    if path == f"/zones/{zone}" and method == "GET":
        cf["zone_reads"] += 1
        if cf["zone_status"] == "pending" and 0 < cf["active_after_reads"] <= cf["zone_reads"]:
            cf["zone_status"] = "active"
        return ok({"id": zone, "name": cfg["domain"], "status": cf["zone_status"], "name_servers": cfg["cf_ns"]})
    if path == f"/zones/{zone}/activation_check" and method == "PUT":
        cf["activation_checks"] += 1
        return ok({"id": zone})
    if path == f"/zones/{zone}/dns_records":
        if method == "GET":
            name, typ = q.get("name", [None])[0], q.get("type", [None])[0]
            return ok([r for r in cf["records"] if (not name or r["name"] == name) and (not typ or r["type"] == typ)])
        if method == "POST":
            if body["type"] in ("MX", "CAA", "TXT"):
                w.st["violations"].append(f"the script created a {body['type']} record")
            rec = {"id": f"rec{cf['next_id']}", "name": body["name"], "type": body["type"], "content": body["content"], "proxied": body["proxied"], "ttl": body["ttl"], "comment": body.get("comment", "")}
            cf["next_id"] += 1
            cf["records"].append(rec)
            return ok(rec)
    m = re.fullmatch(rf"/zones/{zone}/dns_records/([^/]+)", path)
    if m and method == "DELETE":
        if not cf["first_delete_seen"]:
            cf["first_delete_seen"] = True
            cf["snapshot_before_first_delete"] = os.path.exists(os.path.join(os.environ["WORLD"], "state", "dns-rollback.json"))
        rec = next((r for r in cf["records"] if r["id"] == m.group(1)), None)
        if rec and rec["type"] in ("MX", "CAA", "TXT"):
            w.st["violations"].append(f"the script deleted a {rec['type']} record")
        if cf["delete_fail"] and rec and rec["name"] == cf["delete_fail"]:
            return bad(9999, "simulated delete failure")
        if not rec:
            return bad(81044, "record not found")
        cf["records"].remove(rec)
        w.st["net"]["stale"][rec["name"]] = rec["content"]
        return ok({"id": rec["id"]})
    m = re.fullmatch(rf"/accounts/{ACCOUNT}/workers/domains", path)
    if m and method == "GET":
        h, s = q.get("hostname", [None])[0], q.get("service", [None])[0]
        rows = [d for d in cf["domains"] if (not h or d["hostname"] == h) and (not s or d["service"] == s)]
        if cf["conflict_service"] and h == cfg["apex"] and not s and not rows:
            rows = [{"id": "foreign", "hostname": h, "service": cf["conflict_service"], "zone_id": zone}]
        return ok(rows)
    if m and method == "PUT":
        h = body["hostname"]
        if cf["attach_fail"] == h:
            return bad(10000, "simulated attach failure")
        if body["zone_id"] != zone or body["service"] != cfg["worker"] or body.get("environment") != "production":
            return bad(10000, "unexpected attach parameters")
        if any(r["name"] == h and r["type"] in ("A", "AAAA", "CNAME") for r in cf["records"]):
            return bad(100117, "Hostname already has externally managed DNS records")
        d = {"id": f"dom{cf['next_id']}", "hostname": h, "service": body["service"], "zone_id": body["zone_id"]}
        cf["next_id"] += 1
        cf["domains"].append(d)
        if w.st["net"]["lag_left"] is None:
            w.st["net"]["lag_left"] = w.st["net"]["lag"]
        if w.st["net"]["mx_drop_on_attach"]:
            w.st["net"]["mx_drop_on_attach"] = False
            cf["records"] = [r for r in cf["records"] if not (r["type"] == "MX" and r["priority"] == 10 and r["content"].startswith("alt4"))]
        return ok(d)
    m = re.fullmatch(rf"/accounts/{ACCOUNT}/workers/domains/([^/]+)", path)
    if m and method == "DELETE":
        before = len(cf["domains"])
        gone = [d for d in cf["domains"] if d["id"] == m.group(1)]
        cf["domains"] = [d for d in cf["domains"] if d["id"] != m.group(1)]
        for d in gone:
            w.st["net"]["stale"].pop(d["hostname"], None)
        return ok({}) if len(cf["domains"]) < before else bad(10007, "domain not found")
    w.st["violations"].append(f"unexpected Cloudflare API call {method} {path}")
    return bad(9109, "unexpected call in the dry run")


def shim_git(w, cfg, args):
    g = w.st["git"]
    a = list(args)
    if a[:1] == ["-C"]:
        a = a[2:]
    w.log("git", *a)
    if a[:2] == ["branch", "--show-current"]:
        print(g["branch"])
    elif a[:2] == ["status", "--porcelain"]:
        print(" M file" if g["dirty"] else "", end="")
    elif a[:1] == ["fetch"]:
        pass
    elif a[0] == "rev-parse":
        print(g["head"] if a[1] == "HEAD" else g["origin_branch"])
    else:
        w.st["violations"].append(f"unexpected git call {a}")
        return 1
    return 0


def shim_tool(w, cfg, tool, args):
    t = w.st["tools"]
    w.log(tool, *args)
    if tool == "sleep":
        w.st["clock"] += float(args[0])
        return 0
    if tool == "npx":
        joined = " ".join(args)
        if "install" in args:
            t["installs"] += 1
        if "wrangler" in joined and "deploy" in joined:
            if t["deploy_fails"]:
                return 1
            t["deploys"] += 1
            w.st["net"]["worker_deployed"] = True
        return 0
    if tool == "node":
        t["tests"] += 1
        return 1 if t["tests_fail"] else 0
    if tool == "tlscheck":
        n = w.st["net"]
        n["tls_checks"] += 1
        ready = n["tls_ready"] and n["tls_checks"] > n["tls_ready_after"]
        for h in args[1:]:
            print(f"  {'ok  ' if ready else 'FAIL'} {h}: " + ("valid, issuer Google Trust Services / WE1, expires 2027-01-18" if ready else "TLS handshake failed (SSLV3_ALERT_HANDSHAKE_FAILURE)"))
        return 0 if ready else 1
    if tool in ("vercel", "gh", "op"):
        w.st["violations"].append(f"{tool} was called: the script must not touch Vercel, GitHub or 1Password")
        return 1
    return 0


def shim_main(tool, args):
    w = World()
    cfg = json.load(open(os.path.join(os.environ["WORLD"], "world.json")))
    try:
        if tool == "curl":
            rc = shim_curl(w, cfg, args)
        elif tool == "dig":
            rc = shim_dig(w, cfg, args)
        elif tool == "git":
            rc = shim_git(w, cfg, args)
        else:
            rc = shim_tool(w, cfg, tool, args)
    finally:
        w.save()
    sys.stdout.flush()
    sys.exit(rc or 0)


# --------------------------------------------------------------------------------------- scenarios
def mail_records(st):
    return sorted((r["type"], r["name"], r["content"], r.get("priority")) for r in st["cf"]["records"] if r["type"] in ("MX", "CAA", "TXT"))


def all_records(st):
    return sorted((r["name"], r["type"], r["content"], r["proxied"], r["ttl"]) for r in st["cf"]["records"])


class Run:
    def __init__(self, cfg, name, mutate=None, args=(), env=None):
        self.cfg, self.name = cfg, name
        self.dir = tempfile.mkdtemp(prefix=f"cutover-dry-{name}-")
        CREATED.append(self.dir)
        d = self.dir
        for sub in ("bin", "home", "repo", "repo/cloudflare", "state"):
            os.makedirs(os.path.join(d, sub), exist_ok=True)
        json.dump(cfg, open(os.path.join(d, "world.json"), "w"))
        st = new_state(cfg, name)
        if mutate:
            mutate(st)
        self.initial_all, self.initial_mail = all_records(st), mail_records(st)
        json.dump(st, open(os.path.join(d, "state.json"), "w"))
        open(os.path.join(d, "cloudflare.env"), "w").write(f"CLOUDFLARE_API_TOKEN={CF_TOKEN}\nCLOUDFLARE_ACCOUNT_ID={ACCOUNT}\n")
        me = os.path.abspath(__file__)
        for tool in ("curl", "dig", "git", "npx", "node", "vercel", "gh", "op", "tlscheck"):
            p = os.path.join(d, "bin", tool)
            open(p, "w").write(f'#!/bin/sh\nexec python3 "{me}" --shim {tool} "$@"\n')
            os.chmod(p, 0o755)
        p = os.path.join(d, "bin", "sleep")      # sleep never sleeps: it adds up the seconds (plain sh: no Python start-up)
        open(p, "w").write(f'#!/bin/sh\necho "$1" >> "{d}/sleep.log"\n')
        os.chmod(p, 0o755)
        self.env = {
            **os.environ,
            "WORLD": d,
            "HOME": os.path.join(d, "home"),
            "R": os.path.join(d, "repo"),
            "S": os.path.join(d, "state"),
            "CF_ENV_FILE": os.path.join(d, "cloudflare.env"),
            "NODE_BIN": os.path.join(d, "bin"),
            "TLS_CHECK": os.path.join(d, "bin", "tlscheck"),
            "PATH": os.path.join(d, "bin") + os.pathsep + os.environ["PATH"],
            "WAIT_ACTIVE": "60", "WAIT_TLS": "60", "WAIT_FLIP": "60", "WAIT_VERIFY": "20", "POLL": "15",
            **(env or {}),
        }
        self.args = list(args)
        self.out, self.rc, self.log = "", None, ""

    def run(self, *args):
        a = list(args) if args else self.args
        r = subprocess.run(["/bin/bash", SCRIPT, *a], env=self.env, capture_output=True, text=True, timeout=600)
        self.out, self.rc = r.stdout + r.stderr, r.returncode
        lp = os.path.join(self.dir, "state", "cutover.log")
        self.log = open(lp).read() if os.path.exists(lp) else ""
        return self

    @property
    def st(self):
        st = json.load(open(os.path.join(self.dir, "state.json")))
        sl = os.path.join(self.dir, "sleep.log")
        st["clock"] = sum(float(x) for x in open(sl).read().split()) if os.path.exists(sl) else 0
        return st

    def edit(self, fn):
        st = json.load(open(os.path.join(self.dir, "state.json")))
        fn(st)
        json.dump(st, open(os.path.join(self.dir, "state.json"), "w"))

    def calls(self):
        return self.st["calls"]

    def mutating(self):
        return [c for c in self.calls() if c.startswith(("cf DELETE", "cf POST")) or c.startswith("cf PUT /accounts") or (c.startswith("cf PUT") and "activation" not in c)]


class Fail(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


def common(r, expect_rc=0):
    st = r.st
    check(r.rc == expect_rc, f"exit {r.rc}, wanted {expect_rc}\n{r.out[-2200:]}")
    check(not st["violations"], f"violations: {st['violations']}")
    for s in SECRETS:
        check(s not in r.out and s not in r.log, f"secret value {s[:6]}... was printed or logged")


def untouched(r, why):
    """Nothing was changed on Cloudflare DNS, no Worker domain attached, no deploy."""
    st = r.st
    check(not r.mutating(), f"{why}: something was changed: {r.mutating()}")
    check(st["cf"]["domains"] == [], f"{why}: a Worker domain is attached")
    check(all_records(st) == r.initial_all, f"{why}: DNS records differ")


def sc_happy():
    r = Run(copy.deepcopy(CFG), "happy").run()
    st, cfg = r.st, CFG
    common(r)
    check(st["tools"]["deploys"] == 1 and st["tools"]["tests"] >= 1 and st["tools"]["installs"] >= 1, f"deploy/test/install counts: {st['tools']}")
    names = {cfg["apex"], cfg["www"]}
    check({d["hostname"] for d in st["cf"]["domains"]} == names, "Worker domains do not match the hostnames")
    check(mail_records(st) == r.initial_mail, "the mail records (MX, CAA) were changed")
    left = [(x["name"], x["type"]) for x in st["cf"]["records"] if x["type"] in ("A", "AAAA", "CNAME")]
    check(not left, f"temporary records left: {left}")
    check(st["cf"]["snapshot_before_first_delete"] is True, "the rollback snapshot did not exist before the first record was deleted")
    snap = json.load(open(os.path.join(r.dir, "state", "dns-rollback.json")))
    check(sorted(x["name"] for x in snap) == sorted([cfg["apex"], cfg["www"], "*." + cfg["domain"]]), f"rollback file holds {snap}")
    calls = r.calls()
    idx = lambda pred: next(i for i, c in enumerate(calls) if pred(c))
    i_deploy = idx(lambda c: c.startswith("npx") and "deploy" in c)
    i_del = idx(lambda c: c.startswith("cf DELETE /zones"))
    i_put = [i for i, c in enumerate(calls) if c.startswith("cf PUT /accounts")]
    i_dels = [i for i, c in enumerate(calls) if c.startswith("cf DELETE /zones")]
    check(i_deploy < i_del, "the Worker must be deployed before any DNS change")
    check(len(i_put) == 2 and len(i_dels) == 3, f"expected 2 attaches and 3 deletes, got {len(i_put)} and {len(i_dels)}")
    check(i_dels[0] < i_put[0] < i_dels[1] < i_put[1] < i_dels[2], "order must be: delete apex, attach apex, delete www, attach www, delete wildcard")
    check("MAIL CHECK" in r.out and "AFTER THIS" in r.out and "DONE" in r.out, "no summary with the mail check list")
    check("MX and TXT identical to the snapshot taken before the move" in r.out, "no mail comparison after the move")
    check(any("tlscheck" in c or "dig" in c for c in calls), "no TLS/DNS checks")
    check(st["clock"] > 0, "the script never waited")
    return "ok"


def sc_pending_then_active():
    r = Run(copy.deepcopy(CFG), "pending_then_active", mutate=lambda st: st["cf"].update(zone_status="pending", active_after_reads=4)).run()
    common(r)
    st = r.st
    check(st["cf"]["activation_checks"] >= 1, "the activation check was never requested")
    check("waiting for Cloudflare to mark the zone active" in r.out, "the script did not wait for the zone")
    check({d["hostname"] for d in st["cf"]["domains"]} == {CFG["apex"], CFG["www"]}, "the move did not finish once the zone was active")
    check(mail_records(st) == r.initial_mail, "mail records changed")
    return "ok"


def sc_zone_pending_forever():
    r = Run(copy.deepcopy(CFG), "zone_pending", mutate=lambda st: st["cf"].update(zone_status="pending", active_after_checks=0), env={"WAIT_ACTIVE": "30"}).run()
    common(r, expect_rc=1)
    check("still 'pending'" in r.out and "GoDaddy" in r.out, f"no clear message\n{r.out[-800:]}")
    check(r.st["cf"]["activation_checks"] >= 1, "the activation check was never requested")
    check(r.st["tools"]["deploys"] == 0, "deployed anyway")
    untouched(r, "zone pending")
    check("Nothing was changed" in r.out, "no reassurance that nothing changed")
    return "ok"


def sc_registry_old():
    r = Run(copy.deepcopy(CFG), "registry_old", mutate=lambda st: st["net"].update(registry_ns=list(CFG["old_ns"]))).run()
    common(r, expect_rc=1)
    check("registry delegates" in r.out and "zeit-world" in r.out, f"no clear message\n{r.out[-800:]}")
    check(r.st["tools"]["deploys"] == 0, "deployed anyway")
    untouched(r, "registry still old")
    return "ok"


def sc_tls():
    r = Run(copy.deepcopy(CFG), "tls_missing", mutate=lambda st: st["net"].update(tls_ready=False), env={"WAIT_TLS": "30"}).run()
    common(r, expect_rc=1)
    check("no valid certificate" in r.out, f"no clear message\n{r.out[-800:]}")
    check(r.st["tools"]["deploys"] == 0, "deployed anyway")
    untouched(r, "no certificate")
    r2 = Run(copy.deepcopy(CFG), "tls_later", mutate=lambda st: st["net"].update(tls_ready_after=2)).run()
    common(r2)
    check({d["hostname"] for d in r2.st["cf"]["domains"]} == {CFG["apex"], CFG["www"]}, "did not wait for the certificate and then move")
    return "ok"


def sc_mail_differs():
    def drop_mx(st):
        st["cf"]["records"] = [x for x in st["cf"]["records"] if not (x["type"] == "MX" and x["content"].startswith("alt2"))]

    def extra_txt(st):
        st["cf"]["records"].append({"id": "recX", "name": CFG["apex"], "type": "TXT", "content": "v=spf1 -all", "proxied": False, "ttl": 60, "comment": ""})

    def wrong_priority(st):
        for x in st["cf"]["records"]:
            if x["type"] == "MX" and x["content"] == "aspmx.l.google.com":
                x["priority"] = 2

    for name, mut in (("mx_missing", drop_mx), ("txt_extra", extra_txt), ("mx_priority", wrong_priority)):
        r = Run(copy.deepcopy(CFG), name, mutate=mut).run()
        common(r, expect_rc=1)
        check("mail records differ" in r.out, f"{name}: no clear message\n{r.out[-800:]}")
        check(r.st["tools"]["deploys"] == 0, f"{name}: deployed anyway")
        check(not r.mutating(), f"{name}: something was changed")
    return "ok"


def sc_worker_differs():
    r = Run(copy.deepcopy(CFG), "worker_differs", mutate=lambda st: st["net"].update(worker_mutation="text")).run()
    common(r, expect_rc=1)
    check("staged Worker differs" in r.out, f"no clear message\n{r.out[-800:]}")
    untouched(r, "worker differs")
    return "ok"


def sc_tests_fail():
    r = Run(copy.deepcopy(CFG), "tests_fail", mutate=lambda st: st["tools"].update(tests_fail=True)).run()
    common(r, expect_rc=1)
    check("install, test or deploy failed" in r.out, "no clear message")
    check(r.st["tools"]["deploys"] == 0, "deployed although the tests failed")
    untouched(r, "tests fail")
    return "ok"


def sc_refusals():
    for name, mut, msg in (
        ("dirty_tree", lambda st: st["git"].update(dirty=True), "uncommitted"),
        ("not_pushed", lambda st: st["git"].update(origin_branch="c" * 40), "not pushed"),
        ("wrong_branch", lambda st: st["git"].update(branch="master"), "is not on"),
        ("token_inactive", lambda st: st["cf"].update(token="expired"), "not active"),
    ):
        r = Run(copy.deepcopy(CFG), name, mutate=mut).run()
        common(r, expect_rc=1)
        check(msg in r.out, f"{name}: no clear message ({msg})")
        check(r.st["tools"]["deploys"] == 0 and not r.mutating(), f"{name}: something changed")
    return "ok"


def sc_foreign_and_own_domain():
    r = Run(copy.deepcopy(CFG), "foreign_worker_domain", mutate=lambda st: st["cf"].update(conflict_service="someone-elses-worker")).run()
    common(r, expect_rc=1)
    check("another Worker" in r.out, "no clear message")
    check(r.st["tools"]["deploys"] == 0, "deployed anyway")
    r = Run(copy.deepcopy(CFG), "already_ours", mutate=lambda st: st["cf"]["domains"].append({"id": "d1", "hostname": CFG["apex"], "service": CFG["worker"], "zone_id": CFG["zone"]})).run()
    common(r, expect_rc=1)
    check("already attached" in r.out, "no clear message")
    return "ok"


def sc_attach_fails():
    r = Run(copy.deepcopy(CFG), "attach_fails", mutate=lambda st: st["cf"].update(attach_fail=CFG["www"])).run()
    common(r, expect_rc=1)
    st = r.st
    check(all_records(st) == r.initial_all, f"DNS not restored.\nwanted {r.initial_all}\ngot    {all_records(st)}")
    check(st["cf"]["domains"] == [], "a Worker domain is still attached")
    check("restoring the records" in r.out, "no message about restoring")
    check(mail_records(st) == r.initial_mail, "mail records changed")
    return "ok"


def sc_delete_fails():
    r = Run(copy.deepcopy(CFG), "delete_fails", mutate=lambda st: st["cf"].update(delete_fail=CFG["www"])).run()
    common(r, expect_rc=1)
    st = r.st
    check(all_records(st) == r.initial_all, f"DNS not restored.\nwanted {r.initial_all}\ngot    {all_records(st)}")
    check(st["cf"]["domains"] == [], "a Worker domain is still attached")
    return "ok"


def sc_wildcard_delete_fails():
    r = Run(copy.deepcopy(CFG), "wildcard_stays", mutate=lambda st: st["cf"].update(delete_fail="*." + CFG["domain"])).run()
    common(r)
    check("NEEDS YOU" in r.out and "wildcard" in r.out, f"no follow-up message\n{r.out[-900:]}")
    check({d["hostname"] for d in r.st["cf"]["domains"]} == {CFG["apex"], CFG["www"]}, "the move should still have finished")
    return "ok"


def rollback_cycle(r):
    """The run stopped after the DNS move: a new run is refused, rollback restores everything, twice is fine."""
    st = r.st
    check("cutover.sh rollback" in r.out, "the rollback command was not printed")
    check(os.path.exists(os.path.join(r.dir, "state", "dns-rollback.json")), "no rollback file")
    check(mail_records(st) == r.initial_mail, "mail records changed")
    r.run()
    check(r.rc == 1 and "an earlier run reached the DNS step" in r.out, f"a second run was not refused\n{r.out[-600:]}")
    r.run("rollback")
    st = r.st
    check(r.rc == 0, f"rollback exit {r.rc}\n{r.out[-1500:]}")
    check(all_records(st) == r.initial_all, f"records differ after rollback.\nwanted {r.initial_all}\ngot    {all_records(st)}")
    check(st["cf"]["domains"] == [], "Worker domains remain after rollback")
    check(not st["violations"], f"violations: {st['violations']}")
    r.run("rollback")
    check(r.rc == 0 and "already restored" in r.out, f"second rollback not clean: rc {r.rc}\n{r.out[-800:]}")
    check(not r.st["violations"], f"violations: {r.st['violations']}")
    for sec in SECRETS:
        check(sec not in r.out and sec not in r.log, "a secret was printed")


def sc_checks_fail_then_rollback():
    for label, mut, needle in (
        ("wrong_redirect", "wrong_redirect", "307 -> 302"),
        ("email_rewritten", "email", "contact address was rewritten"),
    ):
        r = Run(copy.deepcopy(CFG), label, mutate=lambda st, m=mut: st["net"].update(custom_mutation=m), env={"WAIT_FLIP": "30"}).run()
        common(r, expect_rc=1)
        check(needle in r.out, f"{label}: the problem was not named ({needle})\n{r.out[-1400:]}")
        check("live checks failed" in r.out, f"{label}: no clear message")
        rollback_cycle(r)
    return "ok"


def sc_lag_and_rollback():
    r = Run(copy.deepcopy(CFG), "lag_too_long", mutate=lambda st: st["net"].update(lag=50), env={"WAIT_FLIP": "30"}).run()
    common(r, expect_rc=1)
    check("live checks failed" in r.out, f"no clear message\n{r.out[-900:]}")
    rollback_cycle(r)
    return "ok"


def sc_mail_breaks_during_move():
    r = Run(copy.deepcopy(CFG), "mail_breaks", mutate=lambda st: st["net"].update(mx_drop_on_attach=True)).run()
    common(r, expect_rc=1)
    check("MAIL RECORDS CHANGED" in r.out or "mail records differ" in r.out, f"the lost MX record was not caught\n{r.out[-1200:]}")
    check("cutover.sh rollback" in r.out, "no rollback instruction")
    return "ok"


def sc_stale_resolver():
    def stale(st):
        st["net"]["resolver_ns"]["1.1.1.1"] = list(CFG["old_ns"])
    r = Run(copy.deepcopy(CFG), "stale_resolver", mutate=stale).run()
    common(r)
    check("still caches the old delegation" in r.out, "no note about the stale resolver")
    check("answered by: Vercel" in r.out and "NEEDS YOU" in r.out, f"a stale resolver should be reported, not fail the run\n{r.out[-1200:]}")
    check({d["hostname"] for d in r.st["cf"]["domains"]} == {CFG["apex"], CFG["www"]}, "the move should have finished")
    return "ok"


def sc_status_and_subcommands():
    r = Run(copy.deepcopy(CFG), "status").run("status")
    common(r)
    check("zone 0fbe" in r.out and "registry delegates" in r.out and "mail records" in r.out, f"status output incomplete\n{r.out[-900:]}")
    check(not r.mutating(), "status changed something")
    r = Run(copy.deepcopy(CFG), "verify_without_run").run("verify")
    check(r.rc == 1 and "no baseline" in r.out, f"verify without a run should say so\n{r.out[-500:]}")
    r = Run(copy.deepcopy(CFG), "subcommands").run()
    common(r)
    r.run("verify")
    check(r.rc == 0 and "live checks passed" in r.out, f"verify failed after a good run\n{r.out[-900:]}")
    r.run("mail")
    check(r.rc == 0 and "mail records identical" in r.out, f"mail failed\n{r.out[-600:]}")
    r.run("rollback")
    check(r.rc == 0 and all_records(r.st) == r.initial_all, "rollback after a good run did not restore the records")
    common(r)
    return "ok"


SCENARIOS = [
    ("happy path", sc_happy),
    ("zone pending, then active", sc_pending_then_active),
    ("zone pending forever", sc_zone_pending_forever),
    ("registry still delegates to the old nameservers", sc_registry_old),
    ("certificate missing, then late", sc_tls),
    ("mail records differ (MX missing, extra TXT, wrong priority)", sc_mail_differs),
    ("staged Worker differs from live", sc_worker_differs),
    ("tests fail", sc_tests_fail),
    ("preflight refusals", sc_refusals),
    ("hostname on another Worker / already ours", sc_foreign_and_own_domain),
    ("attach fails mid-flip", sc_attach_fails),
    ("delete fails mid-flip", sc_delete_fails),
    ("temporary wildcard cannot be deleted", sc_wildcard_delete_fails),
    ("edge slow to answer, rerun refused, rollback twice", sc_lag_and_rollback),
    ("answers wrong after the move (www status, rewritten contact address), rollback", sc_checks_fail_then_rollback),
    ("an MX record disappears during the move", sc_mail_breaks_during_move),
    ("resolver still on the old nameservers", sc_stale_resolver),
    ("status, verify, mail and rollback commands", sc_status_and_subcommands),
]

CFG = json.load(open(os.path.join(HERE, "world.json")))


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--shim":
        shim_main(sys.argv[2], sys.argv[3:])
    want = sys.argv[1] if len(sys.argv) > 1 else ""
    bad = 0
    print(f"cutover dry run for {CFG['slug']}: {os.path.abspath(SCRIPT)}", flush=True)
    for name, fn in SCENARIOS:
        if want and want not in name:
            continue
        try:
            res = fn()
            print(f"  PASS  {name}" + (f"  [{res}]" if res != "ok" else ""), flush=True)
        except Fail as e:
            bad += 1
            print(f"  FAIL  {name}: {e}", flush=True)
        except subprocess.TimeoutExpired:
            bad += 1
            print(f"  FAIL  {name}: the script hung")
    print("all scenarios passed" if not bad else f"{bad} scenario(s) FAILED")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
