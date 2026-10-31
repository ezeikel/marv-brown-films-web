#!/usr/bin/env python3
"""
apply-records.py: put the records of records.json into a Cloudflare zone. Creates what is missing, never edits or
deletes anything, and says so when a record exists with a different value.

    set -a; . ~/Development/docs/keys/cloudflare.env; set +a           # CLOUDFLARE_API_TOKEN (never printed)
    python3 apply-records.py --zone-id ZONE_ID                          # dry run: what would be created
    python3 apply-records.py --zone-id ZONE_ID --apply                  # create the missing ones
    python3 apply-records.py --zone-id ZONE_ID --set permanent --apply # mail + CAA only (a new account's zone)

--set permanent|temporary|all (default all). The permanent set is the Google Workspace mail (MX) and the CAA records;
the temporary set keeps the website on Vercel until cutover.sh moves it to the Worker.
Exit status 0 when every wanted record exists afterwards (or would be created in a dry run), 1 otherwise.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.cloudflare.com/client/v4"


def call(method, path, body=None):
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not token:
        sys.exit("CLOUDFLARE_API_TOKEN is not set (source ~/Development/docs/keys/cloudflare.env)")
    req = urllib.request.Request(API + path, method=method, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"success": False, "errors": [{"message": f"HTTP {e.code}"}]}


def fqdn(domain, name):
    return domain if name == "@" else f"{name}.{domain}"


def same(want, have):
    """Does the existing Cloudflare record `have` equal the wanted record?"""
    t = want["type"]
    if t == "MX":
        return have["content"].rstrip(".").lower() == want["content"].rstrip(".").lower() and have.get("priority") == want["priority"]
    if t == "CAA":
        d = have.get("data") or {}
        return (d.get("flags"), d.get("tag"), d.get("value")) == (want["data"]["flags"], want["data"]["tag"], want["data"]["value"])
    return have["content"].rstrip(".").lower() == want["content"].rstrip(".").lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zone-id", required=True)
    ap.add_argument("--set", choices=["permanent", "temporary", "all"], default="all")
    ap.add_argument("--apply", action="store_true", help="create the missing records (default: only report)")
    ap.add_argument("--records", default=os.path.join(HERE, "records.json"))
    a = ap.parse_args()
    spec = json.load(open(a.records))
    domain = spec["domain"]
    z = call("GET", f"/zones/{a.zone_id}")
    if not z.get("success"):
        sys.exit(f"cannot read zone {a.zone_id}: {z.get('errors')}")
    if z["result"]["name"] != domain:
        sys.exit(f"zone {a.zone_id} is {z['result']['name']}, not {domain}")
    print(f"zone {domain} ({z['result']['status']}), name servers: {', '.join(z['result']['name_servers'])}")

    wanted = []
    if a.set in ("permanent", "all"):
        wanted += [("permanent", r) for r in spec["permanent"]]
    if a.set in ("temporary", "all"):
        wanted += [("temporary", r) for r in spec["temporary"]]

    bad = 0
    for kind, r in wanted:
        name = fqdn(domain, r["name"])
        have = call("GET", f"/zones/{a.zone_id}/dns_records?type={r['type']}&name={urllib.parse.quote(name)}&per_page=100")
        if not have.get("success"):
            print(f"  ERROR  {r['type']:<5} {name}: cannot read {have.get('errors')}")
            bad += 1
            continue
        label = f"{r['type']:<5} {name:<26} " + (f"{r['priority']:>2} {r['content']}" if r["type"] == "MX" else f"{r['data']['flags']} {r['data']['tag']} \"{r['data']['value']}\"" if r["type"] == "CAA" else r["content"])
        if any(same(r, h) for h in have["result"]):
            print(f"  ok      [{kind}] {label}")
            continue
        if have["result"] and r["type"] == "CNAME":
            print(f"  DIFFERS [{kind}] {label}: the name already has {[h['content'] for h in have['result']]}")
            bad += 1
            continue
        if not a.apply:
            print(f"  would create [{kind}] {label}")
            continue
        body = {"type": r["type"], "name": name, "ttl": r["ttl"], "comment": r.get("comment", "")}
        if r["type"] == "MX":
            body.update(content=r["content"], priority=r["priority"])
        elif r["type"] == "CAA":
            body.update(data=r["data"])
        else:
            body.update(content=r["content"], proxied=r.get("proxied", False))
        res = call("POST", f"/zones/{a.zone_id}/dns_records", body)
        if res.get("success"):
            print(f"  created [{kind}] {label}  (id {res['result']['id']})")
        else:
            print(f"  ERROR   [{kind}] {label}: {res.get('errors')}")
            bad += 1
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
