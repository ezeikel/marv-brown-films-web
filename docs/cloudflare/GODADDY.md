# GoDaddy: changing the nameservers of marvbrownfilms.com

Written 31 October 2026. This is the one step only the owner can do. It changes
who answers DNS questions for the domain (from Vercel to Cloudflare). It does
not move the website and does not touch the mail: both DNS providers already
give the same answers for the mail records, which is checked below before you
start. Nothing here has been done.

## What the registry shows today

Looked up on 31 October 2026 (`dig NS marvbrownfilms.com @a.gtld-servers.net`
and `whois`):

- Registrar: GoDaddy.com, LLC. Domain expires 2027-04-30.
- Nameservers GoDaddy has published: `b.zeit-world.co.uk`, `d.zeit-world.org`,
  `e.zeit-world.net`, `f.zeit-world.com`. These are Vercel's older server
  names (Vercel's own record list calls `ns1.vercel-dns.com` and
  `ns2.vercel-dns.com` the intended ones). All six give identical answers for
  this domain, so either spelling is Vercel DNS. **Expect to see the four
  `zeit-world` names in GoDaddy, not `ns1`/`ns2.vercel-dns.com`.**
- DNSSEC: not signed, no DS record at the registry. Nothing to switch off.
- Registry status codes: `clientUpdateProhibited`, `clientTransferProhibited`,
  `clientDeleteProhibited`, `clientRenewProhibited`. That is a protection lock
  that GoDaddy sets. I have not seen your GoDaddy screen, so this is an
  expectation and not something I checked: GoDaddy may ask for a confirmation
  code (or ask you to switch a protection setting off) when you save the
  nameservers. Do what GoDaddy asks for that one save and switch it back on
  afterwards.

## The two nameservers to enter

```
maria.ns.cloudflare.com
scott.ns.cloudflare.com
```

Exactly these two, lower case, no trailing dot, no spaces. Cloudflare's own
warning: if the names are not copied exactly, DNS will not resolve correctly.

## Before you change anything

1. Check that the mail records are identical on the new nameservers (read only):

   ```sh
   python3 ~/Development/Personal/marv-brown-films-cf/docs/cloudflare/dns/verify-dns.py \
     --auth maria.ns.cloudflare.com --auth scott.ns.cloudflare.com \
     --auth ns1.vercel-dns.com --auth ns2.vercel-dns.com \
     --resolver 1.1.1.1 --resolver 8.8.8.8
   ```

   It must end with `mail records identical on every server`. On 31 October
   2026 it did, for all of Cloudflare's two, Vercel's two, the four `zeit-world`
   servers and three public resolvers (evidence in `dns/evidence-2026-10-31.txt`).
2. Pick a time when you can also run `cutover.sh` (see `CUTOVER.md`), ideally
   the same day. Vercel serves this site with a wildcard certificate that
   expires on 2 December 2026 and can only renew a wildcard while its own
   nameservers are in use (Vercel's docs: the DNS challenge), and its renewal
   window opens about now. Leaving the domain "on Cloudflare DNS, website still
   on Vercel" for weeks is the one thing to avoid.
3. Tell Marv the day. Nothing should change for him or his visitors, but it is
   his mail.

## The change

(GoDaddy's screens change; the path below is from GoDaddy's help page
"Change my domain nameservers" as quoted on 31 October 2026. If a label has
moved, the thing you want is the domain's DNS area, then Nameservers, then the
option for your own nameservers.)

1. Sign in to GoDaddy with the account that owns `marvbrownfilms.com`.
2. Open **Domain Portfolio**, click `marvbrownfilms.com` to reach its domain
   settings, choose **DNS**, then **Nameservers**.
3. **Write down or screenshot the four nameservers shown now.** They are the
   rollback.
4. Choose **Change nameservers** and the option **I'll use my own
   nameservers** (not "GoDaddy default" and not "Enter my own DNS records").
5. Remove the four old entries. Enter `maria.ns.cloudflare.com` and
   `scott.ns.cloudflare.com`.
6. **Save**, then **Continue** if GoDaddy asks. Complete any verification it
   asks for.

## What NOT to touch

- GoDaddy's **DNS Records** page for this domain. After the change it is not
  used, and leaving it as it is keeps the rollback simple.
- Domain lock, privacy, auto-renew, contacts, and the **Domain Protection**
  setting (except the single confirmation above).
- Any **Email**, **Microsoft 365** or **Workspace** product shown on the
  domain, and **Forwarding**.
- DNSSEC. It is off and stays off.
- Anything GoDaddy suggests as "fix" or "switch back to GoDaddy DNS".
- Everything on Vercel (see `CUTOVER.md`, "After").

## After you saved

1. Cloudflare emails the account owner when the zone becomes active, usually
   within minutes and at most within a day. You can ask it to look again:
   dashboard, `marvbrownfilms.com`, Overview, **Re-check now**. Or check from
   the terminal:

   ```sh
   bash ~/Development/Personal/marv-brown-films-cf/docs/cloudflare/cutover.sh status
   ```

   It is read only. You are waiting for `zone ... active` and
   `.com registry delegates marvbrownfilms.com to: maria... scott...`.
   (Resolvers keep the old nameservers for up to a day; that is normal and
   harmless because the old and the new nameservers answer the same.)
2. **Mail test, with Marv, as soon as the status shows the zone active.** This is
   the check that the friend's email still works. The script never sends mail;
   people do:
   - **You** send one message from a personal address (not `@marvbrownfilms.com`)
     to `info@marvbrownfilms.com` (the address printed on the site; ask Marv
     whether it is a mailbox, a group or an alias, and which mailbox receives
     it). Subject: `DNS move test 1`.
   - **Marv** (or whoever owns that mailbox) tells you whether it arrived, in
     the inbox or in Spam, and replies from his `@marvbrownfilms.com` mailbox.
     **You** tell him whether the reply arrived.
   - Do it again the next day (`DNS move test 2`), after the website move.
   - If either direction fails: change nothing else, send the bounce text to
     Claude, and if you want it undone, do the rollback below. (The mail records
     are identical on both sides, so a failure would point at Google Workspace
     or a typo in the nameserver entry, but that is not something I can see.)
3. Then run the cutover script:

   ```sh
   ! bash ~/Development/Personal/marv-brown-films-cf/docs/cloudflare/cutover.sh
   ```

## Rollback

At GoDaddy, repeat the change and enter the four old nameservers exactly:

```
b.zeit-world.co.uk
d.zeit-world.org
e.zeit-world.net
f.zeit-world.com
```

(`ns1.vercel-dns.com` and `ns2.vercel-dns.com` also work: they serve the same
zone.) Do this only if the cutover script has been rolled back first or never
run, because it puts the whole domain, website included, back on Vercel DNS. The
Vercel records are untouched, so the website and the mail come back as they
were once resolvers pick up the change (minutes to a day).
