# Handing marvbrownfilms.com to Marv

Written 31 October 2026. Until then the Worker, the Cloudflare zone and the
Cloudflare certificates live in the owner's Cloudflare account (Free plan). The
domain itself is registered at GoDaddy until 2027-04-30; who holds that GoDaddy
account, and who pays the renewal, is between the two of them. The registrar
transfer or a GoDaddy account-to-account move is GoDaddy business and is not
covered here.

## What exists, and what it depends on

| thing | where | notes |
| --- | --- | --- |
| The site | Worker `marv-brown-films-web`, code in `cloudflare/` of this repo | one HTML page and nine static files, **captured from the 2019 Vercel deployment** (`cloudflare/site/`), not built from the Next source |
| Custom domains | `marvbrownfilms.com` and `www.marvbrownfilms.com` attached to that Worker | `www` is answered with a 307 redirect to the bare domain, as it was on Vercel |
| DNS zone | `marvbrownfilms.com` in the owner's Cloudflare account | 5 MX (Google Workspace) and 3 CAA records are the permanent part; Cloudflare creates the website records itself |
| Zone setting | Email Address Obfuscation **off** | otherwise Cloudflare may rewrite the `info@marvbrownfilms.com` address on the page; the cutover script checks the page byte for byte |
| Mail | Google Workspace | not touched by any of this. Marv's own admin account, billing and mailboxes stay where they are |

The Next source in the repo (2019, Next 8, `@now/next`) is kept for history. **I
did not try to rebuild it**: the task was to serve the deployed bytes. Editing
the page means either changing the captured HTML and bundles by hand or
rebuilding the page in a current framework. The three videos are YouTube ids in
the source (`pages/index.js`): Cassandra + Karl `iymAop1vSdk`, Priscilla +
Sellasie `B0svEooXzW8`, Swodei + Emmanuel `QJWgcEnNiyE`.

## Moving it to Marv's own Cloudflare account

Cloudflare's documented way to move a domain between accounts is to add it as
a new site in the destination account, copy the DNS records, change the
nameservers at the registrar and press "Re-check now" (Cloudflare docs, "Move a
domain between Cloudflare accounts"). Certificates do not move; the new account
issues its own. Mail stays up throughout because the mail records are in the new
zone before the nameservers change. The website has a short gap (typically
minutes) between the zone becoming active and the Worker domains being attached,
so do it at a quiet time.

1. **Marv** creates a Cloudflare account (Free plan is enough) and turns on
   two-factor authentication.
2. **Marv** adds the site `marvbrownfilms.com` (Free plan). Cloudflare shows him
   two nameservers. **Do not change the nameservers yet.**
3. Put the permanent records in his zone, either way:
   - dashboard: DNS, Records, Import, upload `dns/marvbrownfilms.com.permanent.bind`
     (8 records; the three temporary Vercel records are deliberately not in it), or
   - script: `CLOUDFLARE_API_TOKEN=... python3 docs/cloudflare/dns/apply-records.py --zone-id <his zone id> --set permanent --apply`
     with a token that has Zone Read and DNS Edit on his zone.
   The CAA records were Vercel's defaults and only limit which authorities may
   issue certificates for the domain; they can be left out. Cloudflare adds its
   own authorities whenever a zone has CAA records.
4. **Switch Email Address Obfuscation off** in his zone (Security, Settings).
   New zones have it on.
5. **Prove the mail records match** before anything goes live:
   ```sh
   python3 docs/cloudflare/dns/verify-dns.py \
     --auth <his-first-nameserver> --auth <his-second-nameserver> \
     --auth maria.ns.cloudflare.com --auth scott.ns.cloudflare.com
   ```
   It must end with `mail records identical on every server`.
6. Deploy the Worker into his account: `cd cloudflare && pnpm install && pnpm exec wrangler login && pnpm exec wrangler deploy`.
   The first deploy asks him to register a `workers.dev` name; the Worker is then
   at `https://marv-brown-films-web.<his-name>.workers.dev`. Check it with
   `python3 docs/cloudflare/parity.py --live https://marvbrownfilms.com --stage <that address>`.
7. At GoDaddy (whoever holds the account), replace `maria.ns.cloudflare.com` and
   `scott.ns.cloudflare.com` with the two nameservers of **his** zone
   (`GODADDY.md` describes the screens). From this moment the website has no
   Worker behind it until step 9.
8. In his dashboard press **Re-check now** on the zone's overview page and wait
   for **Active** and for the Universal certificate (SSL/TLS, Edge Certificates).
9. Attach `marvbrownfilms.com` and `www.marvbrownfilms.com` to his Worker as
   custom domains (Workers & Pages, the Worker, Settings, Domains & Routes; the
   labels move). If a record already sits on a name, Cloudflare refuses: delete
   that record first (there should be none, step 3 did not add any).
10. Check the site in a browser (the three videos load, the address is a working
    mail link, `https://www.marvbrownfilms.com` lands on the bare domain, and
    `curl -sI https://marvbrownfilms.com/` shows `server: cloudflare`) and repeat
    the **mail test** in `GODADDY.md`: one message in, one message out.
11. The **owner** then detaches the two custom domains from his Worker, deletes
    his Worker `marv-brown-films-web`, and deletes the `marvbrownfilms.com` zone
    from his account. (Cloudflare's documentation says a zone whose domain moved
    to another account shows "Moved Away", becomes "Deleted" after seven days and
    is removed seven days after that, so deleting it yourself only tidies up
    sooner.) Delete the leftover certificate for the Worker domains under
    SSL/TLS, Edge Certificates, if one remains.

To undo at any point before step 11: put the owner's two nameservers
(`maria.ns.cloudflare.com`, `scott.ns.cloudflare.com`) back at GoDaddy; his
Worker and zone are still there.

## Things Marv should decide or know

- **Mail authentication is missing.** There is no SPF, DKIM or DMARC record on the
  domain today (checked on all six Vercel servers on 31 October 2026), and the move
  preserves that on purpose: the point was to change nothing about mail.
  Google recommends all three for Workspace; adding them later is a separate,
  small change in Cloudflare DNS plus switching DKIM on in the Google Admin
  console, best done by whoever administers Workspace.
- **Analytics.** The page still loads the old Universal Analytics tag
  (`UA-131531376-1` and `UA-134442589-1`); those IDs are carried over unchanged.
- **Favicon.** The page links `/static/favicon.ico`, which never existed.
- **Cost.** Workers and static assets on the Free plan, a Free-plan zone, no
  purchase. The domain renewal (GoDaddy) is the only recurring cost I know of.
- **Contact address.** `info@marvbrownfilms.com` is printed on the page. I could not
  check whether it is a mailbox, a group or an alias in his Workspace.
