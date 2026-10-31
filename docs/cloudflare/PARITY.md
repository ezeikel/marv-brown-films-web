# Parity with the live site: Marv Brown Films

Run on 31 October 2026 (GET requests only) against the staged Worker
https://marv-brown-films-web.chewybytes.workers.dev. DNS, Vercel, GoDaddy and
GitHub were not touched.

## Classification and capture

Plain static output. The page is rendered by a Vercel lambda (Next 8 with the
`@now/next` builder, 2019, build id `YRiwSN3fskDON_2D4EVQd`), but nothing in it
depends on the request: `/` was fetched three times and was byte-identical, its
`pageProps` are empty, and the only client-side work is hydration and three
YouTube players that the browser creates. So the **deployed bytes** are served
as static files. A 2019 Next toolchain was not rebuilt (not tried).

How: `docs/cloudflare/capture.py` fetched `/` (GET, three times) and every
first-party file the page references, and checked each file against the digest
Vercel lists for the deployment `dpl_BVUuDkJNzLpwNfNTzy2S2kh5kzzP` (read-only
`vercel api /v6/deployments/<id>/files`; the digest of a build-output file is its
SHA-256). Result in `capture-manifest.json`. The single file that has no
deployment digest is the lambda's HTML.

| URL | bytes | SHA-256 (start) | proof |
| --- | ---: | --- | --- |
| `/, /index` | 10,807 | `71090331019fb6a2` | server-rendered by a lambda: identical in 3 consecutive GETs |
| `/_next/static/YRiwSN3fskDON_2D4EVQd/pages/_app.js` | 433,824 | `fba81de98b212e78` | equal to the Vercel deployment file (SHA-256) |
| `/_next/static/YRiwSN3fskDON_2D4EVQd/pages/_error.js` | 4,826 | `337eeea80d1a35cd` | equal to the Vercel deployment file (SHA-256) |
| `/_next/static/YRiwSN3fskDON_2D4EVQd/pages/index.js` | 28,916 | `0597cf1b55adc267` | equal to the Vercel deployment file (SHA-256) |
| `/_next/static/chunks/commons.8beb641fac3a66854f49.js` | 267,934 | `70a4be91366af741` | equal to the Vercel deployment file (SHA-256) |
| `/_next/static/runtime/main-182a4db10b852a4792ee.js` | 16,889 | `21a995c08323b5db` | equal to the Vercel deployment file (SHA-256) |
| `/_next/static/runtime/webpack-f0c6edbea5b547edc23f.js` | 1,486 | `404ae79ad9b03e63` | equal to the Vercel deployment file (SHA-256) |
| `/static/logos/logo-black.png` | 37,322 | `27e5e51bbb4fdba4` | equal to the Vercel deployment file (SHA-256) |
| `/static/logos/logo-white.png` | 76,640 | `ec0fc97cf63937df` | equal to the Vercel deployment file (SHA-256) |

Total 878,644 bytes (858 KiB) in nine files, uploaded to the Worker as static assets.
The page also links `/static/favicon.ico`, which answers 404 on Vercel (it is not in the deployment) and so on the Worker.

No Vercel-only code exists in the page (no `@vercel/analytics`, no speed insights),
so nothing was removed. The Google Analytics (Universal Analytics) snippet is
carried over unchanged.

### Not carried over

The 2019 upload also published its development build output. The deployment lists
163 such files that no page refers to, and **all of them answer 200 on Vercel
today** (7,852,028 bytes in total): 148 webpack hot-update files
(`/_next/static/webpack/**`: 50 `.hot-update.json`, 49 `.hot-update.js`, 49 `.hot-update.js.map`),
8 development bundles (`/_next/static/runtime/main.js`, `/_next/static/runtime/webpack.js`,
`/_next/static/chunks/0.js`, `/_next/static/development/**`, including a 1.1 MB development `_app.js`)
and 7 source maps (the largest is 913 KB). They are not part of `cloudflare/site/` and answer 404
on the Worker. The full list with sizes and digests is `capture-manifest.json`, key
`served_by_vercel_but_not_referenced`.

## Rules the Worker reproduces (`cloudflare/worker.ts`, tested by `cloudflare/worker.test.mjs`)

What Vercel answered on 31 October 2026, and the same URLs against the Worker code
(the `www` and plain-`http` rules cannot be exercised on workers.dev, so the unit
tests do it, and `cutover.sh` checks them on the real hostnames):

| request | Vercel answered | Worker |
| --- | --- | --- |
| `http://marvbrownfilms.com/foo?x=1` and `http://www...` | 308 to the same URL on https, `text/plain` "Redirecting...", no HSTS | same |
| `https://www.marvbrownfilms.com/<path>?<query>` | **307 to `https://marvbrownfilms.com/<path>?<query>`**, `text/plain` "Redirecting...", cache-control and HSTS (the bare domain is canonical, not www) | same |
| a `//` in the path | 308 with a relative `Location` of the collapsed path, on the host asked for (before the www redirect) | same |
| `/`, `/index`, `/index/` | 200 home page | same |
| `/index.html`, `/INDEX`, `/static/`, `/robots.txt`, `/sitemap.xml`, `/favicon.ico`, anything else | 404, `text/plain; charset=utf-8`, body "The page could not be found / NOT_FOUND / request id" | same text, Cloudflare's ray id as the request id |
| a trailing slash on a file (`/static/logos/logo-black.png/`) | 200 | same |
| every response | `cache-control: public, max-age=0, must-revalidate`, `strict-transport-security: max-age=63072000` | same |
| JavaScript | `application/javascript; charset=utf-8` | same (the assets binding says `text/javascript`, the Worker overrides it) |

Differences that remain, none of them visible to a visitor:

- `/?x=1` and any query: Vercel writes the query into `__NEXT_DATA__` (`"query":{"x":"1"}`, 7 bytes);
  the static copy always has `"query":{}`. The visible text is identical.
- `HEAD /` on Vercel answers 200 with `content-length: 0`; `POST` and `OPTIONS` on `/` answer 200 with the page;
  the Worker answers 405 for anything but GET and HEAD.
- Paths with dot segments: `/static/logos/../logos/logo-black.png` and `/static/%2e%2e/index` are 404 on Vercel; Cloudflare resolves the dots before the Worker sees the request, so they serve the file (200). `/%2e%2e/etc/passwd` is 400 on both.
- HTML responses from the Worker carry no `ETag` (assets and Vercel's HTML do), so a browser downloads the 10 KB page again instead of getting a 304.
- `/robots.txt`: Vercel 404. The workers.dev address answers with Cloudflare's managed robots.txt (200, 1,248 bytes, only its comment preamble). Whether
  the proxied hostname does depends on a zone setting that this token cannot read. `cutover.sh` allows it and says what it saw.
- The 404 page's request id is of course a different one.

## Parity run

`docs/cloudflare/parity.py` (GET only). For each path on both sides: first-hop status and `Location`, final status and
URL, content type, cache-control, HSTS, a SHA-256 of the bytes (a 404 page has its request id masked), for HTML also the
visible text and the head fingerprint and the references. Paths: `/`, `/robots.txt`, `/sitemap.xml`, `/favicon.ico`, a
made-up 404, then a breadth-first crawl of the references found in the live HTML (the site has no sitemap and one page), then
the extra URLs listed in the command, plus eight of the development files that must be absent on the Worker.

```
Parity: https://marvbrownfilms.com  vs  https://marv-brown-films-web.chewybytes.workers.dev  (33 paths, 0 with differences)

| path | status live/stage | final URL same | content-type | bytes | visible text | cache-control live | cache-control stage | notes |
|---|---|---|---|---|---|---|---|---|
| / | 200/200 | same | text/html | identical (10807 B) | same | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /robots.txt | 404/200 | same | text/plain | DIFFERENT (79 B vs 1248 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate | expected: live has none (404). Cloudflare answers workers.dev with its managed robots.txt; whether it does so on the proxied hostname depends on the zone (see PARITY.md) |
| /sitemap.xml | 404/404 | same | text/plain | identical (79 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /favicon.ico | 404/404 | same | text/plain | identical (79 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /nope-404-cbeihf | 404/404 | same | text/plain | identical (79 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /index | 200/200 | same | text/html | identical (10807 B) | same | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /index/ | 200/200 | same | text/html | identical (10807 B) | same | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /index.html | 404/404 | same | text/plain | identical (79 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /static/logos/logo-white.png | 200/200 | same | image/png | identical (76640 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /static/logos/logo-black.png/ | 200/200 | same | image/png | identical (37322 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /?x=1 | 200/200 | same | text/html | DIFFERENT (10814 B vs 10807 B) | same | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate | expected: Next writes the query string into __NEXT_DATA__ ("query":{"x":"1"}, 7 bytes); the static copy has "query":{}. Nothing reads it. |
| //x | 404/404 (first 308/308) | same | text/plain | identical (79 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /INDEX | 404/404 | same | text/plain | identical (79 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /static/ | 404/404 | same | text/plain | identical (79 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /static/logos/ | 404/404 | same | text/plain | identical (79 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /x%20y | 404/404 | same | text/plain | identical (79 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /a/b/c/d | 404/404 | same | text/plain | identical (79 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /_next/static/YRiwSN3fskDON_2D4EVQd/pages/_error.js | 200/200 | same | text/javascript | identical (4826 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /_next/static/runtime/main.js | 200/404 | same | text/javascript vs text/plain | DIFFERENT (910377 B vs 57 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate | expected: 2019 development bundle that Vercel also served; not referenced by any page, not carried over |
| /_next/static/runtime/webpack.js | 200/404 | same | text/javascript vs text/plain | DIFFERENT (34649 B vs 57 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate | expected: 2019 development bundle, not carried over |
| /_next/static/runtime/main.js.map | 200/404 | same | application/json vs text/plain | DIFFERENT (913206 B vs 57 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate | expected: source map of a development bundle, not carried over |
| /_next/static/chunks/0.js | 200/404 | same | text/javascript vs text/plain | DIFFERENT (424 B vs 57 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate | expected: 2019 development chunk, not carried over |
| /_next/static/development/pages/index.js | 200/404 | same | text/javascript vs text/plain | DIFFERENT (698984 B vs 57 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate | expected: 2019 development build of the page, not carried over |
| /_next/static/development/pages/_app.js | 200/404 | same | text/javascript vs text/plain | DIFFERENT (1118672 B vs 57 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate | expected: 2019 development build, not carried over |
| /_next/static/development/dll/dll_96236aee5669694a06b4.js | 200/404 | same | text/javascript vs text/plain | DIFFERENT (904326 B vs 57 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate | expected: 2019 development dll bundle, not carried over |
| /_next/static/webpack/08593c232cb9c438426e.hot-update.json | 200/404 | same | application/json vs text/plain | DIFFERENT (75 B vs 57 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate | expected: webpack hot-update file from the 2019 upload (99 of them), not carried over |
| /_next/static/YRiwSN3fskDON_2D4EVQd/pages/_app.js | 200/200 | same | text/javascript | identical (433824 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /_next/static/YRiwSN3fskDON_2D4EVQd/pages/index.js | 200/200 | same | text/javascript | identical (28916 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /_next/static/chunks/commons.8beb641fac3a66854f49.js | 200/200 | same | text/javascript | identical (267934 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /_next/static/runtime/main-182a4db10b852a4792ee.js | 200/200 | same | text/javascript | identical (16889 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /_next/static/runtime/webpack-f0c6edbea5b547edc23f.js | 200/200 | same | text/javascript | identical (1486 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /static/favicon.ico | 404/404 | same | text/plain | identical (79 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |
| /static/logos/logo-black.png | 200/200 | same | image/png | identical (37322 B) | - | public, max-age=0, must-revalidate | public, max-age=0, must-revalidate |  |

Stage self-check: 7 internal references in the staged HTML, 0 broken []; 1 answer the same dead status on live (kept as they were) [{'path': '/static/favicon.ico', 'status': 404, 'live_status': 404}]

Compression (Accept-Encoding: gzip, br), content-encoding live/stage: image/png: None/None, text/html: br/br, text/javascript: br/br, text/plain: None/br

Other live addresses, first hop only (what the Worker must reproduce; worker.test.mjs checks the same URLs against the Worker code):
- https://www.marvbrownfilms.com/x-check?x=1 -> 307 https://marvbrownfilms.com/x-check?x=1 (text/plain; body 'Redirecting...\n')
- https://www.marvbrownfilms.com/ -> 307 https://marvbrownfilms.com/ (text/plain; body 'Redirecting...\n')
- http://marvbrownfilms.com/x-check?x=1 -> 308 https://marvbrownfilms.com/x-check?x=1 (text/plain; body 'Redirecting...')
- http://www.marvbrownfilms.com/x-check?x=1 -> 308 https://www.marvbrownfilms.com/x-check?x=1 (text/plain; body 'Redirecting...')
- https://marvbrownfilms.com//x-check?x=1 -> 308 /x-check?x=1 (text/plain; body 'Redirecting...\n')
- https://www.marvbrownfilms.com//x-check?x=1 -> 308 /x-check?x=1 (text/plain; body 'Redirecting...\n')
```

## Rendered in a browser

`docs/cloudflare/hydrate.py` renders a live page and its staged twin in headless Chrome (its own throw-away profile, JavaScript
executed, up to 15 virtual seconds) and compares the hydrated DOM. The server HTML has three empty video slots; the
YouTube players exist only after the bundles ran, so this proves the moved bundles load and run.

```
same      text 93/93 chars, iframes 3/3  https://marvbrownfilms.com  vs  https://marv-brown-films-web.chewybytes.workers.dev
   live  iframe: https://www.youtube.com/embed/iymAop1vSdk?autoplay=1&showinfo=0&modestbranding=1&enablejsapi=1&origin={origin}
   live  iframe: https://www.youtube.com/embed/B0svEooXzW8?autoplay=1&showinfo=0&modestbranding=1&enablejsapi=1&origin={origin}
   live  iframe: https://www.youtube.com/embed/QJWgcEnNiyE?autoplay=1&showinfo=0&modestbranding=1&enablejsapi=1&origin={origin}
   stage iframe: https://www.youtube.com/embed/iymAop1vSdk?autoplay=1&showinfo=0&modestbranding=1&enablejsapi=1&origin={origin}
   stage iframe: https://www.youtube.com/embed/B0svEooXzW8?autoplay=1&showinfo=0&modestbranding=1&enablejsapi=1&origin={origin}
   stage iframe: https://www.youtube.com/embed/QJWgcEnNiyE?autoplay=1&showinfo=0&modestbranding=1&enablejsapi=1&origin={origin}
exit 0
```

## Worker

```
$ wrangler deploy        (wrangler 4.151.3, 31 Oct 2026, from cloudflare/)
Total Upload: 2.69 KiB / gzip: 1.14 KiB          (the Worker script; the nine static files, 878,644 bytes, are uploaded separately)
Worker Startup Time: 4 ms
Bindings: env.ASSETS (Assets)
Deployed marv-brown-films-web triggers
  https://marv-brown-films-web.chewybytes.workers.dev
Current Version ID: 1b112e54-dc61-4764-9cc7-812f70eeaf90
```

`wrangler.jsonc`: `workers_dev: true`, no `routes`, no custom domain; `html_handling` and `not_found_handling` are `none`
because the Worker decides which files exist; `run_worker_first: true`; observability on.

```
ok 1 - plain http 308s to https on the same host, keeping path and query, with no HSTS header
ok 2 - www 307s to the apex with path and query, as text/plain "Redirecting..."
ok 3 - duplicate slashes 308 to the collapsed path (a relative Location, as Vercel sent it), before the www redirect
ok 4 - the apex serves the home page at /, /index and with a trailing slash or a query
ok 5 - the workers.dev address serves the site itself, without a www or http redirect
ok 6 - every successful response carries Vercel's cache-control and HSTS
ok 7 - JavaScript keeps Vercel's content type; images keep their own
ok 8 - index.html is not a page; neither are directories, other cases, or unknown paths
ok 9 - other methods are refused, GET and HEAD are not
# tests 9
# suites 0
# pass 9
# fail 0
# cancelled 0
# skipped 0
```

## DNS and mail evidence

`dns/evidence-2026-10-31.txt` has the raw answers of all six servers Vercel's zone is published on, of both Cloudflare
nameservers and of three public resolvers, and the comparison `dns/verify-dns.py` makes. Short version: **MX (five records,
priorities and targets) and TXT (none) are identical on every server; Cloudflare's CAA answer is a superset of Vercel's three
records** (Cloudflare adds its own authorities to a zone that has CAA records). The registry delegates to the four
`*.zeit-world.*` servers, there is no DS record, and the domain is not DNSSEC-signed.

## Cutover dry run

```
cutover dry run for marv-brown-films-web: /Users/ezeikel/Development/Personal/marv-brown-films-cf/docs/cloudflare/cutover.sh
  PASS  happy path
  PASS  zone pending, then active
  PASS  zone pending forever
  PASS  registry still delegates to the old nameservers
  PASS  certificate missing, then late
  PASS  mail records differ (MX missing, extra TXT, wrong priority)
  PASS  staged Worker differs from live
  PASS  tests fail
  PASS  preflight refusals
  PASS  hostname on another Worker / already ours
  PASS  attach fails mid-flip
  PASS  delete fails mid-flip
  PASS  temporary wildcard cannot be deleted
  PASS  edge slow to answer, rerun refused, rollback twice
  PASS  answers wrong after the move (www status, rewritten contact address), rollback
  PASS  an MX record disappears during the move
  PASS  resolver still on the old nameservers
  PASS  status, verify, mail and rollback commands
all scenarios passed
exit 0
```

Mutation checks: copies of `cutover.sh` with one safety removed must make the dry run fail.

```
caught     mutant A (the DNS step also deletes the MX, CAA and TXT records of the apex)
           scenario "happy path":   FAIL  happy path: exit 1, wanted 0
caught     mutant B (no automatic restore when a step fails halfway)
           scenario "attach fails":   FAIL  attach fails mid-flip: DNS not restored.
caught     mutant C (no check that Cloudflare already has a certificate)
           scenario "certificate missing":   FAIL  certificate missing, then late: no clear message
caught     mutant D (the mail comparison before the move does not stop it)
           scenario "mail records differ":   FAIL  mail records differ (MX missing, extra TXT, wrong priority): mx_missing: deployed anyway
caught     mutant E (the rollback file is written after the first record is deleted)
           scenario "happy path":   FAIL  happy path: the rollback snapshot did not exist before the first record was deleted
```
