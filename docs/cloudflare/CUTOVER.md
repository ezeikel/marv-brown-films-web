# Cutover: Marv Brown Films (marvbrownfilms.com)

Prepared 31 October 2026 on branch `wip/cloudflare-static-20261031`. **The
cutover script has not been run.** The Worker `marv-brown-films-web` is staged at
https://marv-brown-films-web.chewybytes.workers.dev. The Cloudflare zone exists
but is **pending**: it answers no real traffic until the nameservers are changed
at GoDaddy. Vercel and GoDaddy are untouched; GitHub only has this branch.

What the site is: one landing page (Next 8 on Vercel lambdas, built in 2019:
three YouTube players, a contact address, four social links). Its output does
not depend on the request, so it is captured byte for byte and served as static
files; see `PARITY.md`. The domain also carries Marv's Google Workspace mail,
which is the part that must not break.

## The order, and why

| step | who | what | effect on visitors / mail |
| --- | --- | --- | --- |
| 1 | done | Zone created in the owner's Cloudflare account (pending). Mail records copied from Vercel DNS and proved identical. Temporary records keep the website on Vercel. | none, the zone is not live |
| 2 | **owner** | Change the nameservers at GoDaddy: `GODADDY.md` | none by design: Cloudflare and Vercel answer the same for mail, and Cloudflare's temporary records still send the website to Vercel |
| 3 | owner + Marv | Mail test (`GODADDY.md`, "After you saved") | the check that mail works |
| 4 | **owner** | `! bash docs/cloudflare/cutover.sh` (this file) | the website moves to the Worker, seconds of gap per hostname |
| 5 | owner + Marv | Mail test again the next day | |
| 6 | owner, a week later | Delete the Vercel side (printed at the end of the script) | |

Do steps 2 to 4 on the same day. The certificate Vercel serves for the domain
is a wildcard that expires on **2 December 2026**; Vercel's documentation says
it can issue wildcards only while its own nameservers are used (DNS challenge)
and renews 14 to 30 days before expiry, so the window opens about now.

## What the zone holds now

Zone `marvbrownfilms.com`, id `0fbe8edbb466ccc22e058f5ebc107cb4`, Free plan,
nameservers `maria.ns.cloudflare.com` and `scott.ns.cloudflare.com`. Records
(`dns/records.json`; every one was read from Vercel's six authoritative servers
and from `vercel dns ls`):

| type | name | value | ttl | kept after the cutover |
| --- | --- | --- | --- | --- |
| MX | marvbrownfilms.com | 1 aspmx.l.google.com | 60 | yes, never touched |
| MX | marvbrownfilms.com | 5 alt1.aspmx.l.google.com | 60 | yes |
| MX | marvbrownfilms.com | 5 alt2.aspmx.l.google.com | 60 | yes |
| MX | marvbrownfilms.com | 10 alt3.aspmx.l.google.com | 60 | yes |
| MX | marvbrownfilms.com | 10 alt4.aspmx.l.google.com | 60 | yes |
| CAA | marvbrownfilms.com | 0 issue "letsencrypt.org" | 60 | yes |
| CAA | marvbrownfilms.com | 0 issue "pki.goog" | 60 | yes |
| CAA | marvbrownfilms.com | 0 issue "sectigo.com" | 60 | yes |
| CNAME | marvbrownfilms.com | c054198f4ffd3454.vercel-dns-016.com (DNS only) | 300 | **deleted**, the Worker takes the name |
| CNAME | www.marvbrownfilms.com | cname.vercel-dns-016.com (DNS only) | 300 | **deleted**, the Worker takes the name |
| CNAME | *.marvbrownfilms.com | cname.vercel-dns-016.com (DNS only) | 300 | **deleted** |

Notes:

- There is no TXT record anywhere on Vercel (no SPF, DKIM, DMARC or
  verification record), so there is none here. Adding SPF/DKIM/DMARC for
  Google Workspace is a separate, optional improvement for Marv; it is not part
  of this move (`HANDOVER.md`).
- The three CNAMEs are Vercel's own `ALIAS` targets (apex: the project-specific
  `c054198f4ffd3454.vercel-dns-016.com`; everything else: the wildcard
  `cname.vercel-dns-016.com`), so the website resolves to the same Vercel
  addresses as before (216.150.x.x). The addresses Vercel documents for new
  setups, `76.76.21.21` and `cname.vercel-dns.com`, serve the same site too
  (tested with `curl --resolve`: apex 200 and www 307 on 76.76.21.21 and on two
  216.150.x.x addresses); they are the fallback if the mirrored targets ever
  stop working.
- `_domainconnect` (a CNAME to `_domainconnect.vercel-dns.com`) is not copied:
  Vercel adds it to every zone it hosts, it is not in the account's record list,
  and it would advertise Vercel as the DNS provider after Vercel stops hosting
  the zone. It has nothing to do with mail or the website.
- Cloudflare adds its own certificate authorities to a zone that has CAA records
  (their documentation says so; `dig` shows 11 CAA records on its nameservers
  against Vercel's 3). Every authority Vercel allowed may still issue ordinary
  certificates. Cloudflare's added `issuewild` records name five authorities and not
  Sectigo, which matters only for wildcard certificates from Sectigo; nothing here
  uses one (Vercel's is from Let's Encrypt).
- Zone setting changed from the default: **Email Address Obfuscation is off**.
  Cloudflare turns it on for new zones; it rewrites visible email addresses to
  `[email protected]`, and this page's only call to action is the address
  `info@marvbrownfilms.com`. Cloudflare's documentation does not say whether it
  applies to static assets served by a Worker, and a pending zone cannot be
  tested, so it is off, and the script checks the served page byte for byte.
  To revert: `PATCH /zones/<id>/settings/email_obfuscation {"value":"on"}`.

## Before you run it

- Nameservers changed at GoDaddy and the mail test done (`GODADDY.md`).
- `bash docs/cloudflare/cutover.sh status` (read only) shows the zone `active`
  and the registry delegating to `maria...` and `scott...`.
- `git -C ~/Development/Personal/marv-brown-films-cf status` prints nothing and
  the branch is pushed (the script checks both).
- Nothing on Vercel has been paused, aliased or deleted.

## Run it

```sh
! bash ~/Development/Personal/marv-brown-films-cf/docs/cloudflare/cutover.sh
```

`!` commands stop waiting after 120 seconds but carry on; the log is
`~/.cutover/marv-brown-films-web/cutover.log` and the rollback file (the
temporary records, no secrets) is `~/.cutover/marv-brown-films-web/dns-rollback.json`.
The script never touches the registrar, Vercel, GitHub or 1Password and never
sends mail. It reads the Cloudflare token from `~/Development/docs/keys/cloudflare.env`.

| step | what it does | stops with |
| --- | --- | --- |
| 0 preflight | read only. Tools; branch pushed and clean; token active; the zone is **active** (it asks Cloudflare to re-check and waits up to 5 minutes); the **.com registry** delegates to Cloudflare's two nameservers; the three public resolvers (informational); **MX, TXT and CAA on both Cloudflare nameservers and the resolvers equal `dns/records.json`**; Cloudflare already has a **valid certificate** for the apex and www (a TLS handshake against a Cloudflare edge address, waits up to 10 minutes); the hostnames are not on another Worker; the temporary records are there; a snapshot of what the live site answers today (16 paths and 5 redirect URLs) and a check that the snapshot really came from Vercel | nothing changed |
| 1 deploy | install, `node --test worker.test.mjs`, `wrangler deploy` to workers.dev, and the staged Worker must answer every path like the live site (status, type, bytes, redirects) | nothing changed on DNS |
| 2 DNS | saves the three temporary records to the rollback file **before any change**, then for the apex and for www: deletes the temporary record, attaches the Worker as a custom domain (retried); finally deletes the temporary wildcard. A failure restores everything by itself. | DNS restored automatically |
| 3 live | (a) against **Cloudflare's own edge address** (needs no propagation): every page, asset and redirect equals the Vercel snapshot, the answer comes from Cloudflare and not Vercel, the contact address is intact and not rewritten, the certificates are valid; retried for up to 5 minutes. (b) through DNS over HTTPS, as a visitor's resolver sees it, retried for about a minute; if a resolver still answers from Vercel (old record TTL 5 minutes, old nameservers up to a day) it says so and lists a follow-up. (c) the **mail records again**: MX and TXT on every server identical to before step 2 | rollback printed, Vercel untouched |
| 4 summary | prints the mail test and what not to touch on Vercel yet | |

The one moment of risk is step 2: a Worker custom domain cannot be attached while
a CNAME sits on the name, so each hostname is without an address for the seconds
between deleting the temporary record and Cloudflare publishing the Worker's own
(on the pending zone, new records took one to three minutes to appear on
Cloudflare's nameservers, and the script's step 3 waits for exactly that). A
visitor's resolver that happens to ask in that window may remember "no such host"
for up to about 30 minutes (the zone's negative-cache time is 1800 seconds). The
site gets very little traffic, so pick a quiet moment, and the script's own checks
go to Cloudflare's addresses directly so they are not affected.

Single commands: `status` (read only, any time), `verify` (the live checks again),
`mail` (the mail records against the snapshot), `rollback`.

## If it stops

- Before step 2 nothing has changed: fix the cause named in the message and run
  it again.
- During step 2 the script puts the records back by itself and says so.
- After step 2 (live checks failed): the hostnames may already be served by the
  Worker. Run `verify` after a few minutes, or roll back. A second full run is
  refused while the rollback file exists.

## Roll back

```sh
! bash ~/Development/Personal/marv-brown-films-cf/docs/cloudflare/cutover.sh rollback
```

Detaches the Worker domains from the apex and www and re-creates the three
temporary records from the saved file; the website is served by Vercel again,
**as long as the Vercel alias still exists and its certificate is valid** (until
2 December 2026, see above). It checks the mail records at the end; it never
touches them. Safe to run twice. Cloudflare keeps the certificates it issued for
the Worker domains; delete them under SSL/TLS, Edge Certificates, if you want.

Last resort, if Cloudflare itself is the problem (not the records): at GoDaddy
put the four old nameservers back (`GODADDY.md`, "Rollback"). That moves the
whole domain, mail included, back to Vercel DNS, which is untouched.

Also available by hand, in the dashboard: DNS, Records, add `CNAME @ ->
c054198f4ffd3454.vercel-dns-016.com` and `CNAME www -> cname.vercel-dns-016.com`
(DNS only) after removing the Worker domains under Workers, `marv-brown-films-web`,
Settings, Domains and Routes.

## Dry run

`python3 docs/cloudflare/dry-run/run.py` runs the real script against stubbed
`curl`, `dig`, `git`, `npx`, `node`, `sleep` and TLS probe and a simulated
Cloudflare, registry, resolvers, Vercel and Worker. It never touches the
network. Scenarios (the result of the run on 31 October 2026 is in `PARITY.md`):
the happy path; a zone that is pending, then active; a zone that never becomes
active; the registry still on the old nameservers; a certificate that is missing,
then late; a missing MX record, an extra TXT record and a wrong MX priority on
Cloudflare; a staged Worker that differs from the live site; failing tests;
dirty tree, unpushed branch, wrong branch, inactive token; a hostname already on
another Worker (or already ours); an attach that fails halfway; a delete that
fails halfway; a wildcard that cannot be deleted; an edge that answers slowly,
then a refused rerun and two rollbacks; wrong answers after the move (www
redirect status, rewritten contact address) followed by a rollback; an MX record
that disappears during the move; a resolver still on the old nameservers; and the
`status`, `verify`, `mail` and `rollback` commands. Every run also fails if the
script calls `vercel`, `gh` or `op`, creates, edits or deletes an MX, CAA or TXT
record, makes a non-GET request to a website, or prints the token.

## For you to decide

- **Universal Analytics tags.** The page loads `gtag.js` for `UA-131531376-1` and
  configures `UA-134442589-1`. Those are Universal Analytics properties, which
  Google stopped processing in 2023 (my knowledge, not checked today), and the two
  IDs differ. They are carried over unchanged. Marv may want a GA4 property or
  Cloudflare Web Analytics instead (the account is at its Web Analytics site
  limit, see the fleet skill).
- **`/static/favicon.ico` is a 404 on Vercel today** (the page links to it, the
  repo has no file). Carried over as it is; Marv can supply a favicon.
- **`/robots.txt`.** Vercel answers 404. The workers.dev address answers 200 with
  Cloudflare's managed robots.txt (only its comment preamble about content signals,
  1,248 bytes). Whether the proxied hostname does too depends on a zone setting that
  this token cannot read. For comparison, the fleet zone ezeikelpemberton.photography
  serves a longer managed file with `Content-Signal: search=yes,ai-train=no,use=reference`
  and `Disallow: /` for AI training crawlers (GPTBot, ClaudeBot and others): a policy
  statement made on the site owner's behalf. The script allows this difference and
  reports what it saw. If Marv does not want it, switch it off in the dashboard
  (Security, Settings, or the AI Crawl Control page).
- **Development bundles are not carried over.** The 2019 deployment also serves
  163 unreferenced development files, all answering 200 on Vercel today (7.85 MB
  in total): 148 webpack hot-update files, 8 development bundles (one is a 1.1 MB
  development `_app.js`) and 7 source maps (one is 913 KB). No page links to
  them. They answer 404 on the Worker (`capture-manifest.json` lists every one
  with its size). Say so if you want them.
- **Query strings.** Next writes the query into `__NEXT_DATA__` on the server;
  the static copy always has `"query":{}`. The visible page is identical;
  nothing reads it.
- **Other methods.** Vercel answered `POST` and `OPTIONS` on `/` with the page; the
  Worker answers 405. Nobody posts to a landing page.
- **Who pays and owns what.** The Worker, the zone and any Cloudflare certificate
  are in the owner's account (Free-plan zone; I made no purchase). Cloudflare's
  documentation describes the certificate it issues for a Worker custom domain as
  an Advanced Certificate generated on your behalf; I could not check whether that
  shows on a bill (the token cannot read certificates), so glance at the billing
  page after the cutover. The domain is registered with GoDaddy until 2027-04-30.

## After

- Leave Vercel alone for a week (resolvers that cached the old nameservers keep
  asking it; it is also the rollback). Then, yourself:
  ```sh
  vercel alias ls --scope chewy-bytes                   # check the names first
  vercel alias rm marvbrownfilms.com --scope chewy-bytes
  echo y | vercel project rm marv-brown-films-web --scope chewy-bytes
  vercel domains rm marvbrownfilms.com --yes --scope chewy-bytes
  ```
  The last command removes Vercel's copy of the DNS zone: only after a week of
  working mail and website, and after the mail test has passed twice.
- Open https://marvbrownfilms.com in a browser: the three videos should load
  and the address should be a working mail link.
- Handover to Marv: `HANDOVER.md`.
