#!/usr/bin/env python3
"""
verify-dns.py: ask nameservers (and resolvers) for the records that matter and compare them with records.json.
Read only: it only runs `dig`.

    python3 verify-dns.py --auth maria.ns.cloudflare.com --auth scott.ns.cloudflare.com \
        --auth ns1.vercel-dns.com --auth ns2.vercel-dns.com --resolver 1.1.1.1 --resolver 8.8.8.8

    --auth NS       an authoritative server: MX and TXT must match exactly, CAA must contain every wanted record,
                    and the MX TTL must be the recorded one (60 s)
    --resolver IP   a recursive resolver: same comparison without the TTL (a resolver counts it down)
    --expect-ns A,B (with --resolver) the NS set the resolver must report for the domain (after the nameserver change)
    --web           also print what the apex and www answer (information only)
    --json FILE     write the normalised answers (the cutover script keeps this as its mail snapshot)

MX and TXT are the mail records: any difference is a failure. CAA may be a superset: Cloudflare adds its own
certificate authorities to a zone that has CAA records, so the Cloudflare answer lists more than Vercel's three.
Exit status 0 when every server agrees, 1 otherwise, 2 when dig could not be run.
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def dig(server, name, rtype, recurse):
    cmd = ["dig", f"@{server}", name, rtype, "+noall", "+answer", "+time=5", "+tries=2"]
    if not recurse:
        cmd.append("+norecurse")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 and not r.stdout:
        return None
    rows = []
    for line in r.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) == 5 and parts[2] == "IN":
            rows.append((parts[0].rstrip(".").lower(), int(parts[1]), parts[3], parts[4].strip()))
    return rows


def norm_mx(rows):
    out = set()
    for _, _, t, rd in rows:
        if t == "MX":
            pri, host = rd.split(None, 1)
            out.add((int(pri), host.rstrip(".").lower()))
    return out


def norm_txt(rows):
    out = set()
    for _, _, t, rd in rows:
        if t == "TXT":
            out.add("".join(re.findall(r'"((?:[^"\\]|\\.)*)"', rd)))
    return out


def norm_caa(rows):
    out = set()
    for _, _, t, rd in rows:
        if t == "CAA":
            m = re.match(r'(\d+)\s+(\w+)\s+"(.*)"$', rd)
            if m:
                out.add((int(m.group(1)), m.group(2).lower(), m.group(3)))
    return out


def caa_has(actual, want):
    """A wanted CAA record is present if the server lists it, or its Cloudflare spelling ('pki.goog; cansignhttpexchanges=yes')."""
    flags, tag, value = want
    return any(a[0] == flags and a[1] == tag and (a[2] == value or a[2].split(";")[0].strip() == value) for a in actual)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", default=os.path.join(HERE, "records.json"))
    ap.add_argument("--auth", action="append", default=[])
    ap.add_argument("--resolver", action="append", default=[])
    ap.add_argument("--expect-ns", help="comma separated NS names a --resolver must report")
    ap.add_argument("--web", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    spec = json.load(open(a.records))
    domain = spec["domain"]
    want_mx = {(r["priority"], r["content"].lower()) for r in spec["permanent"] if r["type"] == "MX"}
    want_mx_ttl = {r["ttl"] for r in spec["permanent"] if r["type"] == "MX"}
    want_caa = {(r["data"]["flags"], r["data"]["tag"], r["data"]["value"]) for r in spec["permanent"] if r["type"] == "CAA"}
    want_txt = set(spec.get("expect_txt", []))
    want_ns = set(x.strip().lower().rstrip(".") for x in a.expect_ns.split(",")) if a.expect_ns else None

    bad = 0
    report = {}
    servers = [(s, False) for s in a.auth] + [(s, True) for s in a.resolver]
    for server, recurse in servers:
        mx, txt, caa = dig(server, domain, "MX", recurse), dig(server, domain, "TXT", recurse), dig(server, domain, "CAA", recurse)
        if mx is None or txt is None or caa is None:
            print(f"{server}: dig could not reach it")
            sys.exit(2)
        got_mx, got_txt, got_caa = norm_mx(mx), norm_txt(txt), norm_caa(caa)
        problems = []
        if got_mx != want_mx:
            problems.append(f"MX differs: missing {sorted(want_mx - got_mx)} extra {sorted(got_mx - want_mx)}")
        if got_txt != want_txt:
            problems.append(f"TXT differs: missing {sorted(want_txt - got_txt)} extra {sorted(got_txt - want_txt)}")
        missing_caa = [w for w in want_caa if not caa_has(got_caa, w)]
        if missing_caa:
            problems.append(f"CAA missing {sorted(missing_caa)}")
        if not recurse:
            ttls = {ttl for (_, ttl, t, _) in mx if t == "MX"}
            if ttls and ttls != want_mx_ttl:
                problems.append(f"MX TTL {sorted(ttls)} instead of {sorted(want_mx_ttl)}")
        ns_note = ""
        if recurse and want_ns is not None:
            ns = dig(server, domain, "NS", True) or []
            got_ns = {rd.rstrip(".").lower() for (_, _, t, rd) in ns if t == "NS"}
            ns_note = f"  NS {sorted(got_ns)}"
            if got_ns != want_ns:
                problems.append(f"NS is {sorted(got_ns)}, wanted {sorted(want_ns)}")
        kind = "resolver" if recurse else "auth    "
        status = "ok  " if not problems else "DIFF"
        extra_caa = len(got_caa) - len([1 for w in want_caa if caa_has(got_caa, w)])
        print(f"{status} {kind} {server:<26} MX {len(got_mx)} (" + ", ".join(f"{p} {h.split('.')[0]}" for p, h in sorted(got_mx)) + f")  TXT {len(got_txt)}  CAA {len(got_caa)}" + (f" (+{extra_caa} extra)" if extra_caa > 0 else "") + ns_note)
        for p in problems:
            print(f"       {p}")
            bad += 1
        report[server] = {"mx": sorted(got_mx), "txt": sorted(got_txt), "caa": sorted(got_caa), "problems": problems}
        if a.web:
            for label, name, rt in (("apex A", domain, "A"), ("www CNAME", f"www.{domain}", "CNAME"), ("www A", f"www.{domain}", "A")):
                rows = dig(server, name, rt, recurse) or []
                print(f"       {label:<9} " + (", ".join(f"{rd} (ttl {ttl})" for (_, ttl, t, rd) in rows if t in ("A", "CNAME")) or "no answer"))
    if a.json:
        json.dump(report, open(a.json, "w"), indent=1)
    print("mail records identical on every server" if not bad else f"{bad} problem(s)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
