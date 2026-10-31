#!/usr/bin/env python3
"""
Mirror the live Marv Brown Films site (GET only, polite) into a directory and prove, file by file, that the
mirrored bytes equal the bytes of the Vercel deployment dpl_BVUuDkJNzLpwNfNTzy2S2kh5kzzP.

  # the deployment's file list (read only; the digest of every build-output file is in it):
  #   (cd /tmp && vercel api /v6/deployments/dpl_BVUuDkJNzLpwNfNTzy2S2kh5kzzP/files --scope chewy-bytes) > deploy-files.raw.json
  python3 capture.py --site https://marvbrownfilms.com --files deploy-files.raw.json --out cloudflare/site \
      --manifest docs/cloudflare/capture-manifest.json \
      --extra /_next/static/YRiwSN3fskDON_2D4EVQd/pages/_error.js --extra /static/logos/logo-white.png

* The HTML of / is server-rendered by a lambda (no file in the deployment), so it is the one thing that cannot be
  matched to a deployment digest: it is fetched three times and must be byte-identical every time.
* Every first-party asset the page references is fetched; its digest must equal the one Vercel lists for the
  deployment file under /out (the "uid" of a build-output entry is the SHA-256 of the content, 64 hex characters;
  source-upload entries carry a SHA-1).
* Nothing is written for paths the page does not reference (the 2019 development bundles that are also served); they are
  listed in the manifest with the status and size Vercel answers for them today.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse


def digest_matches(body, want):
    """The deployment lists a 40-character digest (SHA-1, source upload) or a 64-character one (SHA-256, build output)."""
    if not want:
        return False
    algo = {40: hashlib.sha1, 64: hashlib.sha256}.get(len(want))
    return bool(algo) and algo(body).hexdigest() == want


def fetch(url):
    with tempfile.TemporaryDirectory() as d:
        b, h = os.path.join(d, "b"), os.path.join(d, "h")
        r = subprocess.run(["curl", "-s", "--max-time", "60", "-o", b, "-D", h, "-w", "%{http_code}", url], capture_output=True, text=True)
        status = int((r.stdout or "0").strip() or 0)
        headers = {}
        for line in open(h, errors="replace").read().splitlines():
            if ":" in line and not line.startswith("HTTP/"):
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        return status, headers, open(b, "rb").read()


def load_deployment_files(path):
    t = open(path).read()
    i = min(x for x in (t.find("["), t.find("{")) if x >= 0)
    d = json.loads(t[i:])
    out = {}

    def walk(nodes, prefix=""):
        for n in nodes:
            p = prefix + "/" + n["name"]
            if n.get("type") == "directory":
                walk(n.get("children", []), p)
            else:
                out[p] = n.get("uid")

    walk(d if isinstance(d, list) else d.get("files", d))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--files", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest")
    ap.add_argument("--extra", action="append", default=[], help="a first-party path the page does not reference but the site serves on purpose")
    a = ap.parse_args()
    site = a.site.rstrip("/")
    dep = load_deployment_files(a.files)

    # 1. the HTML, three times
    bodies = []
    for _ in range(3):
        st, hd, body = fetch(site + "/")
        if st != 200:
            sys.exit(f"GET / answered {st}")
        bodies.append(body)
        time.sleep(0.5)
    if len({hashlib.sha1(b).hexdigest() for b in bodies}) != 1:
        sys.exit("the HTML of / differs between requests: not static, stop")
    html = bodies[0]
    os.makedirs(a.out, exist_ok=True)
    open(os.path.join(a.out, "index.html"), "wb").write(html)
    manifest = [{"path": "/index.html", "served_at": ["/", "/index"], "bytes": len(html), "sha1": hashlib.sha1(html).hexdigest(),
                 "deployment_digest": None, "note": "server-rendered by a lambda on Vercel; identical in 3 consecutive GETs"}]

    # 2. first-party references of the HTML
    text = html.decode("utf-8")
    refs = set(re.findall(r'(?:src|href)="(/[^"]*)"', text))
    refs = {r for r in refs if not r.startswith("//")}
    refs |= set(a.extra)
    bad = 0
    for ref in sorted(refs):
        path = urllib.parse.urlsplit(ref).path
        st, hd, body = fetch(site + ref)
        sha1 = hashlib.sha1(body).hexdigest()
        want = dep.get("/out" + path)
        if st == 404 and want is None:
            # a reference the live site itself cannot resolve (the favicon): the Worker must answer 404 as well
            print(f"keep 404 {st} {ref}  [dangling on live too: nothing is copied, the Worker answers 404]")
            manifest.append({"path": path, "served_at": [], "dangling_reference_on_live": True, "live_status": 404})
            continue
        ok = st == 200 and digest_matches(body, want)
        if st != 200:
            note = f"live answers {st}"
        elif want is None:
            note = "not in the deployment's /out listing"
        elif not digest_matches(body, want):
            note = f"digest differs: deployment {want}"
        else:
            note = f"bytes equal the deployment file (sha{len(want) * 4})"
        print(f"{'ok  ' if ok else 'FAIL'} {st} {len(body):>8} {sha1[:12]} {ref}  [{note}]")
        if not ok:
            bad += 1
            continue
        dest = os.path.join(a.out, path.lstrip("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, "wb").write(body)
        manifest.append({"path": path, "served_at": [path], "bytes": len(body), "sha1": sha1, "deployment_digest": want,
                         "content_type_on_vercel": hd.get("content-type"), "cache_control_on_vercel": hd.get("cache-control")})

    # 3. the other files the deployment serves from /out (not referenced by the page): list, do not copy
    referenced = {urllib.parse.urlsplit(r).path for r in refs}
    served_but_unreferenced = []
    for p, uid in sorted(dep.items()):
        if not p.startswith("/out/") or p == "/out/index":
            continue
        url_path = p[len("/out"):]
        if url_path in referenced:
            continue
        st_, _, body_ = fetch(site + url_path)       # does Vercel really serve it today? (GET, polite)
        served_but_unreferenced.append({"path": url_path, "digest": uid, "live_status": st_, "live_bytes": len(body_)})
        time.sleep(0.15)
    json.dump({"files": manifest, "served_by_vercel_but_not_referenced": served_but_unreferenced}, open(a.manifest or os.path.join(a.out, "..", "manifest.json"), "w"), indent=1)
    print(f"\n{len(manifest)} files written, {len(served_but_unreferenced)} unreferenced files left out, {bad} failures")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
