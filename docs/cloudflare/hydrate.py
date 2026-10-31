#!/usr/bin/env python3
"""
hydrate.py: render the live page and its staged twin in headless Chrome (its own throw-away profile, JavaScript
executed, up to 15 virtual seconds) and compare what the browser ends up with. The server HTML of this site has three
EMPTY video slots: the YouTube players only exist after the page's JavaScript bundles have loaded and run, so this
catches a bundle that fails to load or crashes after the move, which plain HTML fetches cannot.

    python3 hydrate.py https://marvbrownfilms.com https://marv-brown-films-web.chewybytes.workers.dev [more pairs]

Compared: the visible text of the resulting DOM, and the list of <iframe> sources with the page's own origin
replaced (the YouTube embed URL carries the origin of the page that asked for it).
"""
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parity import Page  # noqa: E402

CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def dump(url):
    with tempfile.TemporaryDirectory() as prof:
        r = subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", f"--user-data-dir={prof}", "--virtual-time-budget=15000", "--dump-dom", url],
            capture_output=True, text=True, timeout=180,
        )
    return r.stdout


def summary(dom, origin):
    p = Page()
    p.feed(dom)
    host = urllib.parse.urlsplit(origin).netloc
    frames = []
    for src in re.findall(r"<iframe[^>]*\ssrc=\"([^\"]+)\"", dom):
        src = src.replace("&amp;", "&")
        src = src.replace(urllib.parse.quote(origin, safe=""), "{origin}").replace(origin, "{origin}").replace(host, "{host}")
        frames.append(src)
    return p.visible(), frames


def main():
    if not os.path.exists(CHROME):
        sys.exit(f"Chrome not found at {CHROME} (set CHROME=...)")
    ok = True
    for live, stage in zip(sys.argv[1::2], sys.argv[2::2]):
        (a, fa), (b, fb) = summary(dump(live), live), summary(dump(stage), stage)
        same = a == b and len(a) > 40 and fa == fb and len(fa) > 0
        ok &= same
        print(f"{'same     ' if same else 'DIFFERENT'} text {len(a)}/{len(b)} chars, iframes {len(fa)}/{len(fb)}  {live}  vs  {stage}")
        for f in fa:
            print("   live  iframe:", f[:110])
        for f in fb:
            print("   stage iframe:", f[:110])
        if a != b:
            n = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))
            print("   first text difference at", n, "\n   live :", repr(a[max(0, n - 60):n + 80]), "\n   stage:", repr(b[max(0, n - 60):n + 80]))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
