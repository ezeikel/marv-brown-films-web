#!/usr/bin/env python3
"""
tlscheck.py: does a server IP present a VALID certificate for a hostname? One TLS handshake per hostname, nothing
is sent over it. The certificate is checked the way a browser does (trusted chain, name matches, not expired).

    python3 tlscheck.py IP HOSTNAME [HOSTNAME ...]

cutover.sh uses it twice: before the DNS move, against a Cloudflare edge address, to see that Cloudflare has already
issued the certificate for the zone (so that visitors do not meet a handshake error when the hostnames move); and
after the move, against the address the zone now answers with.
Prints one line per hostname. Exit status 0 when every hostname has a valid certificate, 1 otherwise.
"""
import socket
import ssl
import sys
import datetime


def check(ip, host, timeout=15):
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((ip, 443), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as s:
                cert = s.getpeercert()
                issuer = dict(x[0] for x in cert.get("issuer", ()))
                exp = datetime.datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
                names = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]
                return True, f"valid, issuer {issuer.get('organizationName', '?')} / {issuer.get('commonName', '?')}, expires {exp:%Y-%m-%d}, covers {', '.join(names[:4])}"
    except ssl.SSLCertVerificationError as e:
        return False, f"certificate not valid for this name ({e.verify_message})"
    except ssl.SSLError as e:
        return False, f"TLS handshake failed ({e.reason or e})"
    except OSError as e:
        return False, f"cannot connect ({e})"


def main():
    if len(sys.argv) < 3:
        sys.exit("usage: tlscheck.py IP HOSTNAME [HOSTNAME ...]")
    ip, hosts = sys.argv[1], sys.argv[2:]
    bad = 0
    for h in hosts:
        ok, msg = check(ip, h)
        print(f"  {'ok  ' if ok else 'FAIL'} {h}: {msg}")
        bad += 0 if ok else 1
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
