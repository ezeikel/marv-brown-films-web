#!/bin/bash
# Marv Brown Films (marvbrownfilms.com): Vercel -> Cloudflare Worker, STEP 2 OF 2. OWNER RUNS THIS:   ! bash docs/cloudflare/cutover.sh
# Prepared 31 October 2026 from fleet-cloudflare-migration/templates/scripts/cutover.sh and the Krispy Meme script.
# NOT RUN. Dry run of every branch against stubbed tools:   python3 docs/cloudflare/dry-run/run.py
#
# Step 1 was done before this script existed and by hand: the zone marvbrownfilms.com was created in the owner's
# Cloudflare account (PENDING) with the Google Workspace mail records copied from Vercel DNS and TEMPORARY DNS-only
# records that keep the website on Vercel. Then the owner changes the nameservers at GoDaddy (docs/cloudflare/GODADDY.md).
# This script is for AFTER that change. It never touches the registrar, Vercel, GitHub or 1Password, and sends no mail.
#
#   0 preflight   read only: tools, branch pushed and clean, Cloudflare token, the zone is ACTIVE, the .com registry
#                 delegates to Cloudflare's two nameservers, the mail records (MX, TXT, CAA) on those nameservers equal
#                 the saved snapshot (dns/records.json), Cloudflare already has a valid certificate for the apex and
#                 www, the hostnames are not on another Worker, the temporary records are there, and a snapshot of what
#                 the live site (still Vercel) answers today
#   1 deploy      build/test/deploy the committed Worker to workers.dev and require it to answer like the live site
#   2 DNS         snapshot the temporary records to the rollback file BEFORE any change, then per hostname (apex, www):
#                 delete its temporary record and attach the Worker as a custom domain (a failure restores everything by
#                 itself); then delete the temporary wildcard
#   3 live        checks against Cloudflare's own edge address (no DNS propagation needed) and through DNS over HTTPS,
#                 retried; then the mail records again, which must be exactly what they were before step 2
#   4 summary     the mail test the owner and Marv do by hand, and what NOT to touch on Vercel yet
# Rollback after step 2:   bash docs/cloudflare/cutover.sh rollback
# Other commands:          status (read only, safe any time) | verify | mail | rollback
# Never prints a token or any other secret value.
set -euo pipefail

########## CONFIG ##########
TITLE="Marv Brown Films (marvbrownfilms.com)"
SLUG=marv-brown-films-web
WORKER=marv-brown-films-web
BRANCH=wip/cloudflare-static-20261031
R=${R:-$HOME/Development/Personal/marv-brown-films-cf}   # clean worktree on $BRANCH
APPDIR=$R/cloudflare                                      # the directory holding wrangler.jsonc
DOMAIN=marvbrownfilms.com
APEX=marvbrownfilms.com                                   # the canonical host (www redirects to it, 307)
WWW=www.marvbrownfilms.com
ZONE=0fbe8edbb466ccc22e058f5ebc107cb4                     # the owner's Cloudflare zone for the domain
CF_NS=(maria.ns.cloudflare.com scott.ns.cloudflare.com)   # the nameservers Cloudflare assigned to that zone
OLD_NS=(b.zeit-world.co.uk d.zeit-world.org e.zeit-world.net f.zeit-world.com)   # what GoDaddy had before (the four Vercel legacy names)
LIVE_URL=https://marvbrownfilms.com
RESOLVERS=(1.1.1.1 8.8.8.8 9.9.9.9)
# Held to what Vercel answers today (the snapshot is taken in step 0).
PAGES=(
  / /index /index/
  /static/logos/logo-black.png /static/logos/logo-white.png
  /_next/static/YRiwSN3fskDON_2D4EVQd/pages/index.js /_next/static/YRiwSN3fskDON_2D4EVQd/pages/_app.js /_next/static/YRiwSN3fskDON_2D4EVQd/pages/_error.js
  /_next/static/runtime/main-182a4db10b852a4792ee.js /_next/static/runtime/webpack-f0c6edbea5b547edc23f.js /_next/static/chunks/commons.8beb641fac3a66854f49.js
  /nope-404 /index.html /favicon.ico /static/favicon.ico /robots.txt
)
# First hop of the other addresses (not followed): www -> apex 307, http -> https 308, duplicate slash 308.
ALIAS_URLS=(
  "https://www.marvbrownfilms.com/x-check?x=1"
  "https://www.marvbrownfilms.com/"
  "http://marvbrownfilms.com/x-check?x=1"
  "http://www.marvbrownfilms.com/x-check?x=1"
  "https://marvbrownfilms.com//x-check?x=1"
)
ALLOW=("/robots.txt|live has none (404); Cloudflare may answer with its managed robots.txt on a proxied hostname (zone setting)")
EXPECT_TEXT=("mailto:info@marvbrownfilms.com" ">info@marvbrownfilms.com<")    # the contact address must reach visitors as written
FORBID_TEXT=("[email protected]" "cdn-cgi/l/email-protection")                # ... and not as Cloudflare's obfuscation
########## END CONFIG ##########

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
S=${S:-$HOME/.cutover/$SLUG}
CF_ENV_FILE=${CF_ENV_FILE:-$HOME/Development/docs/keys/cloudflare.env}
NODE_BIN=${NODE_BIN:-$HOME/.nvm/versions/node/v22.23.3/bin}
WAIT_ACTIVE=${WAIT_ACTIVE:-300}        # seconds to wait for the zone to become active
WAIT_TLS=${WAIT_TLS:-600}              # ... for Cloudflare's certificate
WAIT_FLIP=${WAIT_FLIP:-300}            # ... for Cloudflare's edge to answer after the move
WAIT_VERIFY=${WAIT_VERIFY:-60}         # ... for the DNS-over-HTTPS view (retried for about a minute)
POLL=${POLL:-15}
TLS_CHECK=${TLS_CHECK:-python3 $HERE/tlscheck.py}
mkdir -p "$S"; chmod 700 "$S"; umask 077
export PATH="$NODE_BIN:$PATH" NEXT_TELEMETRY_DISABLED=1 CI=true
set -a; . "$CF_ENV_FILE"; set +a
: "${CLOUDFLARE_API_TOKEN:?no CLOUDFLARE_API_TOKEN in $CF_ENV_FILE}" "${CLOUDFLARE_ACCOUNT_ID:?no CLOUDFLARE_ACCOUNT_ID in $CF_ENV_FILE}"
API=https://api.cloudflare.com/client/v4
# The token goes to curl through a header file, so it is never in a process list.
printf 'Authorization: Bearer %s\n' "$CLOUDFLARE_API_TOKEN" > "$S/cf.hdr"
CFH=(-H "@$S/cf.hdr")
STAGE_URL="https://$WORKER.chewybytes.workers.dev"
LOG="$S/cutover.log"
SNAP="$S/dns-rollback.json"; SNAP_TXT="$S/dns-rollback.txt"; SNAP_TSV="$S/dns-records.tsv"
BASE="$S/baseline.json"; MAIL_BEFORE="$S/mail-before.json"; MAIL_AFTER="$S/mail-after.json"
CHECK="$HERE/check.py"; VERIFY_DNS="$HERE/dns/verify-dns.py"
say() { echo "$(date +%H:%M:%S) $*" | tee -a "$LOG"; }

DNS_TOUCHED=0     # set before the first record is deleted
FLIP_ACTIVE=0     # 1 while records are being deleted and domains attached: a failure then restores the records
STANDALONE=0      # 1 when a single command (status, verify, mail, rollback) is run on its own
FOLLOWUP=()       # things that did not finish and need a human

cfapi() {         # METHOD PATH [JSON]  ->  the JSON answer on stdout
  local method=$1 path=$2 body=${3:-}
  if [ -n "$body" ]; then
    curl -s --max-time 60 -X "$method" "${CFH[@]}" -H "Content-Type: application/json" --data "$body" "$API$path"
  else
    curl -s --max-time 60 -X "$method" "${CFH[@]}" -H "Content-Type: application/json" "$API$path"
  fi
}
cf_success() { python3 -c "import sys,json;print(json.load(sys.stdin).get('success'))" 2>/dev/null || echo False; }
cleanup() { rm -f "$S/cf.hdr" "$S/verify.out" "$S/compare.out"; }
trap cleanup EXIT
trap 'die "interrupted"' INT HUP TERM

join_sorted() { tr ' ' '\n' | { grep -v '^$' || true; } | sort | tr '\n' ' ' | sed 's/ $//'; }   # words -> sorted, one line
dig_names() {     # dig args... -> the right-hand names of the answer, lower case, no trailing dot, sorted, one line
  { dig "$@" +short 2>/dev/null || true; } | tr 'A-Z' 'a-z' | sed 's/\.$//' | tr '\n' ' ' | join_sorted
}
want_ns() { printf '%s ' "${CF_NS[@]}" | join_sorted; }

rollback_text() {
  cat <<EOF | tee -a "$LOG"
ROLLBACK (puts the website back on Vercel; safe to run twice; mail is not touched by it):   bash $HERE/cutover.sh rollback
  it detaches the Worker domains from $APEX and $WWW and re-creates the temporary DNS records saved in $SNAP
  (readable copy: $SNAP_TXT). The website is then served by Vercel again, as long as the Vercel alias for $APEX still
  exists (do not delete anything on Vercel until the move is settled). Check https://$APEX from outside afterwards.
  Last resort, only if Cloudflare itself is the problem: at GoDaddy put the four old nameservers back (${OLD_NS[*]}).
EOF
}

die() {
  trap - INT HUP TERM
  say "STOP: $*"
  if [ "$FLIP_ACTIVE" = 1 ]; then
    FLIP_ACTIVE=0
    say "The DNS move was interrupted: restoring the records from the snapshot now."
    undo_dns || say "WARNING: DNS is NOT fully restored. Read $SNAP_TXT and fix the records named above by hand."
  elif [ "$DNS_TOUCHED" = 1 ]; then
    say "The hostnames have moved to the Worker (the Vercel alias is untouched)."
    rollback_text
  elif [ "$STANDALONE" = 1 ]; then
    :
  else
    say "Nothing was changed on Cloudflare DNS, at the registrar or on Vercel."
  fi
  exit 1
}

# ---- DNS helpers ------------------------------------------------------------------------------------------------
temp_records() {  # -> one line per A/AAAA/CNAME record at the apex, www or the wildcard: id type name content proxied ttl
  cfapi GET "/zones/$ZONE/dns_records?per_page=100" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if not d.get('success'): sys.exit(3)
want={sys.argv[1], sys.argv[2], '*.'+sys.argv[1]}
for r in d['result']:
    if r['name'] in want and r['type'] in ('A','AAAA','CNAME'):
        print('\t'.join([r['id'],r['type'],r['name'],r['content'],'true' if r['proxied'] else 'false',str(r['ttl']),r.get('comment') or '']))" "$APEX" "$WWW"
}

write_snapshot() {
  temp_records > "$SNAP_TSV" || die "could not read the DNS records of the zone (DNS untouched)"
  python3 - "$SNAP_TSV" "$SNAP" "$SNAP_TXT" "$APEX" "$WWW" > "$S/snapshot.out" <<'PY' || die "could not write the DNS snapshot (DNS untouched)"
import json,sys
recs=[]
for line in open(sys.argv[1]):
    i,t,n,c,p,ttl,cm=(line.rstrip('\n').split('\t')+[''])[:7]
    recs.append({'id':i,'type':t,'name':n,'content':c,'proxied':p=='true','ttl':int(ttl),'comment':cm})
names={r['name'] for r in recs}
if sys.argv[4] not in names or sys.argv[5] not in names:
    raise SystemExit('the apex or www has no A/AAAA/CNAME record: the temporary records are not there (already moved, or the wrong zone). Refusing to continue')
json.dump(recs,open(sys.argv[2],'w'),indent=1)
open(sys.argv[3],'w').write(''.join(f"{r['type']} {r['name']} {r['content']} proxied={r['proxied']} ttl={r['ttl']}\n" for r in recs))
print(f'  snapshot: {len(recs)} records saved')
PY
  say "$(cat "$S/snapshot.out")"
  [ -s "$SNAP" ] || die "the DNS snapshot is empty (DNS untouched)"
}

detach_host() {   # HOST: remove the Worker custom domain (the records Cloudflare made for it go with it)
  local h=$1 ids id ok
  ids=$(cfapi GET "/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/domains?hostname=$h&service=$WORKER" \
    | python3 -c "import sys,json;print(' '.join(d['id'] for d in (json.load(sys.stdin).get('result') or [])))" 2>/dev/null) || ids=''
  if [ -z "$ids" ]; then say "  $h: no Worker domain attached"; return 0; fi
  for id in $ids; do
    ok=$(cfapi DELETE "/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/domains/$id" | cf_success)
    say "  detached $h: $ok"
    [ "$ok" = True ] || return 1
  done
}

restore_records() {   # re-create every snapshot record that is missing; 0 only when all are back
  if [ ! -s "$SNAP" ]; then
    if ls "$SNAP".restored-* >/dev/null 2>&1; then say "  DNS was already restored by an earlier rollback"; return 0; fi
    say "  no DNS snapshot at $SNAP: the DNS step never ran, or the file was lost. Check the records by hand."; return 1
  fi
  python3 - "$SNAP" > "$S/dns-restore.tsv" <<'PY'
import json,sys
for r in json.load(open(sys.argv[1])):
    print('\t'.join([r['type'],r['name'],r['content'],'true' if r['proxied'] else 'false',str(r['ttl']),r.get('comment') or '']))
PY
  local type name content proxied ttl comment cur ok tries failed=0
  while IFS=$'\t' read -r type name content proxied ttl comment; do
    cur=$(cfapi GET "/zones/$ZONE/dns_records?name=$name&type=$type" \
      | python3 -c "import sys,json;print('\n'.join(x['content'] for x in json.load(sys.stdin)['result']))" 2>/dev/null) || cur='?'
    if [ "$cur" = '?' ]; then say "  $type $name: could not read the current records, fix it by hand"; failed=1; continue; fi
    if grep -qxF -- "$content" <<<"$cur"; then say "  $type $name already points at $content"; continue; fi
    # Cloudflare removes the records it made for a detached Worker domain a moment later: retry a failed create.
    ok=False; tries=0
    while [ "$ok" != True ] && [ "$tries" -lt 3 ]; do
      ok=$(cfapi POST "/zones/$ZONE/dns_records" "{\"type\":\"$type\",\"name\":\"$name\",\"content\":\"$content\",\"proxied\":$proxied,\"ttl\":$ttl,\"comment\":\"$comment\"}" | cf_success)
      [ "$ok" = True ] || sleep 5
      tries=$((tries + 1))
    done
    say "  restored $type $name -> $content: $ok"
    [ "$ok" = True ] || failed=1
  done < "$S/dns-restore.tsv"
  rm -f "$S/dns-restore.tsv"
  return $failed
}

undo_dns() {      # detach both Worker domains, put every temporary record back
  local rc=0
  detach_host "$APEX" || rc=1
  detach_host "$WWW" || rc=1
  restore_records || rc=1
  return $rc
}

# ---- reading the state ------------------------------------------------------------------------------------------
zone_line() {     # -> "status<TAB>name<TAB>name servers (sorted, comma separated)"
  cfapi GET "/zones/$ZONE" | python3 -c "
import sys,json
r=json.load(sys.stdin).get('result') or {}
print(r.get('status',''), r.get('name',''), ','.join(sorted(r.get('name_servers') or [])), sep='\t')" 2>/dev/null || printf '\t\t\n'
}

registry_ns() {   # the delegation the .com registry publishes right now (what GoDaddy's setting turned into)
  { dig @a.gtld-servers.net "$DOMAIN" NS +norecurse +noall +authority 2>/dev/null || true; } | awk '$4=="NS"{print tolower($5)}' | sed 's/\.$//' | tr '\n' ' ' | join_sorted
}

edge_ip() {       # a Cloudflare edge address for the TLS probe: the one workers.dev answers with
  dig +short "$WORKER.chewybytes.workers.dev" @1.1.1.1 2>/dev/null | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -1 || true
}

zone_ip() {       # an address the zone's own nameserver answers with for HOST (empty when none)
  dig @"${CF_NS[0]}" "$1" A +norecurse +short 2>/dev/null | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -1 || true
}

mail_check() {    # OUTFILE: MX, TXT and CAA on both Cloudflare nameservers and the public resolvers must be what Vercel served
  local out=$1 args=() ns r
  for ns in "${CF_NS[@]}"; do args+=(--auth "$ns"); done
  for r in "${RESOLVERS[@]}"; do args+=(--resolver "$r"); done
  python3 "$VERIFY_DNS" "${args[@]}" --json "$out"
}

mail_unchanged() {   # BEFORE AFTER: the MX/TXT sets of every server are the same in the two snapshots
  python3 - "$1" "$2" <<'PY'
import json,sys
a,b=json.load(open(sys.argv[1])),json.load(open(sys.argv[2]))
bad=[s for s in a if s in b and (a[s]['mx'],a[s]['txt'])!=(b[s]['mx'],b[s]['txt'])]
for s in bad: print(f'  {s}: MX/TXT before {a[s]["mx"]} {a[s]["txt"]}, after {b[s]["mx"]} {b[s]["txt"]}')
sys.exit(1 if bad else 0)
PY
}

# ---- step 0 -----------------------------------------------------------------------------------------------------
wait_for() {      # SECONDS DESCRIPTION CMD...: poll CMD every $POLL seconds until it succeeds or the time is up
  local max=$1 what=$2 waited=0; shift 2
  while ! "$@"; do
    if [ "$waited" -ge "$max" ]; then return 1; fi
    say "  waiting for $what ($waited of $max s)"
    sleep "$POLL"; waited=$((waited + POLL))
  done
}

zone_is_active() {
  local line st
  line=$(zone_line); st=${line%%$'\t'*}
  [ "$st" = active ]
}
registry_is_cloudflare() { [ "$(registry_ns)" = "$(want_ns)" ]; }
tls_is_ready() {
  local ip; ip=$(edge_ip)
  [ -n "$ip" ] && $TLS_CHECK "$ip" "$APEX" "$WWW" >"$S/tls.out" 2>&1
}

preflight() {
  say "== 0. preflight (read only) for $TITLE"
  [ ! -e "$SNAP" ] || die "$SNAP exists: an earlier run reached the DNS step. Undo it with 'bash $HERE/cutover.sh rollback'; if DNS is already back on Vercel and you are sure, move that file away first"
  local t
  for t in curl python3 dig git node npx; do command -v "$t" >/dev/null || die "missing tool: $t"; done
  [ "$(git -C "$R" branch --show-current)" = "$BRANCH" ] || die "$R is not on $BRANCH"
  [ -z "$(git -C "$R" status --porcelain)" ] || die "$R has uncommitted changes"
  git -C "$R" fetch -q origin || die "git fetch failed"
  [ "$(git -C "$R" rev-parse "origin/$BRANCH")" = "$(git -C "$R" rev-parse HEAD)" ] || die "$BRANCH is not pushed (HEAD differs from origin/$BRANCH)"
  [ "$(cfapi GET /user/tokens/verify | python3 -c "import sys,json;print(json.load(sys.stdin).get('result',{}).get('status'))" 2>/dev/null || echo ?)" = active ] || die "the Cloudflare token in $CF_ENV_FILE is not active"

  # the zone, and whether the nameserver change at GoDaddy has taken
  local line st name ns
  line=$(zone_line); st=${line%%$'\t'*}; name=$(cut -f2 <<<"$line"); ns=$(cut -f3 <<<"$line")
  [ "$name" = "$DOMAIN" ] || die "zone $ZONE is '$name', expected $DOMAIN"
  [ "$ns" = "$(want_ns | tr ' ' ',')" ] || die "the zone's nameservers are $ns, expected $(want_ns | tr ' ' ',') (CONFIG is out of date)"
  if [ "$st" != active ]; then
    say "  the zone is '$st'. Asking Cloudflare to check the nameservers again (the dashboard's 'Re-check now')"
    cfapi PUT "/zones/$ZONE/activation_check" >/dev/null || true
    wait_for "$WAIT_ACTIVE" "Cloudflare to mark the zone active" zone_is_active \
      || die "the zone is still '$st'. Has the nameserver change at GoDaddy been saved (docs/cloudflare/GODADDY.md)? The .com registry shows: $(registry_ns | sed 's/^$/(nothing)/'). Wait for Cloudflare's email, or press 'Re-check now' on the zone's overview page, then run this again. Nothing was changed"
  fi
  say "  zone $DOMAIN is active on $(want_ns)"
  registry_is_cloudflare || die "the .com registry delegates $DOMAIN to: $(registry_ns | sed 's/^$/(nothing)/'); Cloudflare's are $(want_ns). The change at GoDaddy has not reached the registry. Nothing was changed"
  say "  the .com registry delegates $DOMAIN to Cloudflare's nameservers"
  local r seen
  for r in "${RESOLVERS[@]}"; do
    seen=$(dig_names @"$r" "$DOMAIN" NS)
    if [ "$seen" = "$(want_ns)" ]; then say "  resolver $r sees Cloudflare's nameservers"
    else say "  resolver $r still caches the old delegation (${seen:-nothing}): not a problem, it moves on by itself (the old and the new nameservers serve the same mail records)"; fi
  done

  # mail first: the records that must not change
  say "  mail records on Cloudflare's nameservers and the public resolvers, against dns/records.json:"
  mail_check "$MAIL_BEFORE" | sed 's/^/    /' | tee -a "$LOG" || die "the mail records differ from the saved snapshot, or a server could not be asked (see above). Do not go on; nothing was changed. Compare by hand with: python3 $VERIFY_DNS --auth ${CF_NS[0]} --auth ns1.vercel-dns.com"

  say "  Cloudflare's certificate for $APEX and $WWW (TLS handshake against a Cloudflare edge address):"
  wait_for "$WAIT_TLS" "Cloudflare to issue the certificate" tls_is_ready || { cat "$S/tls.out" 2>/dev/null | sed 's/^/    /' | tee -a "$LOG" || true; die "Cloudflare has no valid certificate for the hostnames yet (it issues one after the zone turns active, usually within minutes, sometimes hours; or no Cloudflare edge address could be looked up). Moving now would show visitors a TLS error. Run this again later. Nothing was changed"; }
  sed 's/^/    /' "$S/tls.out" | tee -a "$LOG"

  local h svc
  for h in "$APEX" "$WWW"; do
    svc=$(cfapi GET "/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/domains?hostname=$h" | python3 -c "import sys,json;r=json.load(sys.stdin).get('result') or [];print(r[0].get('service','') if r else '')" 2>/dev/null || echo ?)
    case "$svc" in
      '') ;;
      "$WORKER") die "$h is already attached to $WORKER: an earlier run got past the DNS step. Use 'bash $HERE/cutover.sh verify' or 'rollback'" ;;
      *) die "$h is already a custom domain of another Worker ($svc): resolve that first" ;;
    esac
  done
  say "  the temporary records that will go (A/AAAA/CNAME at the apex, www and the wildcard; MX and CAA stay):"
  write_snapshot
  sed 's/^/    /' "$SNAP_TXT" | tee -a "$LOG"
  rm -f "$SNAP" "$SNAP_TXT"      # the real snapshot is written again at step 2, right before the first change

  say "  what the live site (Vercel) answers today, the baseline every later check is held to:"
  take_baseline | tee -a "$LOG" || die "the live site did not answer every path (nothing changed)"
  python3 - "$BASE" <<'PY' || die "the baseline was not taken from Vercel: the Worker is already answering for these hostnames (nothing changed)"
import json,sys
b=json.load(open(sys.argv[1]))
sys.exit(0 if b['pages']['/']['vercel'] and not b['pages']['/']['cloudflare'] else 1)
PY
}

take_baseline() {
  local a args=()
  for a in ${ALIAS_URLS[@]+"${ALIAS_URLS[@]}"}; do args+=(--alias "$a"); done
  python3 "$CHECK" snapshot --origin "$LIVE_URL" --out "$BASE" --doh --host "$WWW" ${args[@]+"${args[@]}"} "${PAGES[@]}"
}

allow_args() {    # prints the --allow arguments, one per line
  local a
  for a in ${ALLOW[@]+"${ALLOW[@]}"}; do printf '%s\n' --allow "$a"; done
}
text_args() {     # prints the --expect-text / --forbid-text arguments, one per line
  local a
  for a in ${EXPECT_TEXT[@]+"${EXPECT_TEXT[@]}"}; do printf '%s\n' --expect-text "$a"; done
  for a in ${FORBID_TEXT[@]+"${FORBID_TEXT[@]}"}; do printf '%s\n' --forbid-text "$a"; done
}
run_check() {     # run check.py with the --allow list (read line by line: the reasons contain spaces)
  local sub=$1; shift
  local args=() line
  while IFS= read -r line; do args+=("$line"); done < <(allow_args)
  python3 "$CHECK" "$sub" ${args[@]+"${args[@]}"} "$@"
}
run_verify() {    # run_verify EXTRA...: check.py verify against the baseline, with the contact-address checks
  local args=() line
  while IFS= read -r line; do args+=("$line"); done < <(text_args)
  run_check verify --baseline "$BASE" --origin "$LIVE_URL" --host "$WWW" ${args[@]+"${args[@]}"} "$@"
}

# ---- step 1 -----------------------------------------------------------------------------------------------------
deploy_worker() {
  say "== 1. deploy the committed Worker $WORKER (workers.dev only) and compare it with the live site"
  local f
  for f in .env .env.local .env.production .env.production.local; do
    [ ! -e "$APPDIR/$f" ] || die "$f is present: a deploy would bake it in. Remove it"
  done
  ( cd "$APPDIR" \
    && npx -y pnpm@10.25.0 install --frozen-lockfile \
    && node --test worker.test.mjs \
    && npx -y pnpm@10.25.0 exec wrangler deploy ) >> "$LOG" 2>&1 || die "install, test or deploy failed (details: $LOG). DNS untouched"
  sleep 8
  run_check compare --live "$LIVE_URL" --stage "$STAGE_URL" --doh --host "$WWW" "${PAGES[@]}" > "$S/compare.out" 2>&1 || { tee -a "$LOG" < "$S/compare.out"; die "the staged Worker differs from the live site (nothing changed on DNS)"; }
  tee -a "$LOG" < "$S/compare.out"
}

# ---- step 2 -----------------------------------------------------------------------------------------------------
flip_host() {     # HOST: delete its temporary record(s), attach the Worker
  local h=$1 id type name content proxied ttl cm ok resp tries=0
  while IFS=$'\t' read -r id type name content proxied ttl cm; do
    [ "$name" = "$h" ] || continue
    ok=$(cfapi DELETE "/zones/$ZONE/dns_records/$id" | cf_success)
    say "  deleted $type $name -> $content: $ok"
    [ "$ok" = True ] || die "could not delete $type $name"
  done < "$SNAP_TSV"
  ok=False
  while [ "$ok" != True ] && [ "$tries" -lt 3 ]; do
    resp=$(cfapi PUT "/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/domains" \
      "{\"hostname\":\"$h\",\"service\":\"$WORKER\",\"zone_id\":\"$ZONE\",\"environment\":\"production\"}") || resp='{}'
    ok=$(python3 -c "import sys,json;print(json.loads(sys.argv[1]).get('success'))" "$resp" 2>/dev/null || echo False)
    [ "$ok" = True ] || sleep 5
    tries=$((tries + 1))
  done
  say "  attached $h to $WORKER: $ok"
  [ "$ok" = True ] || die "attaching $h failed"
}

delete_wildcard() {   # the temporary wildcard: after both hostnames are on the Worker; a failure here is not fatal
  local id type name content proxied ttl cm ok
  while IFS=$'\t' read -r id type name content proxied ttl cm; do
    [ "$name" = "*.$DOMAIN" ] || continue
    ok=$(cfapi DELETE "/zones/$ZONE/dns_records/$id" | cf_success)
    say "  deleted $type $name -> $content: $ok"
    [ "$ok" = True ] || FOLLOWUP+=("the temporary wildcard record $type $name -> $content could not be deleted: delete it in the dashboard (DNS > Records)")
  done < "$SNAP_TSV"
}

dns_phase() {
  say "== 2. DNS: snapshot first, then $APEX and $WWW move to the Worker"
  write_snapshot
  say "  rollback file written BEFORE any change: $SNAP"
  DNS_TOUCHED=1; FLIP_ACTIVE=1
  flip_host "$APEX"
  flip_host "$WWW"
  delete_wildcard
  FLIP_ACTIVE=0
}

# ---- step 3 -----------------------------------------------------------------------------------------------------
verify_live() {
  say "== 3. live checks"
  local waited=0 ip ok=0
  say "  3a. against Cloudflare's own edge address (needs no DNS propagation), retried for up to $WAIT_FLIP s"
  while :; do
    ip=$(zone_ip "$APEX")
    if [ -n "$ip" ] && run_verify --resolve-ip "$ip" --expect-no-vercel --expect-cloudflare > "$S/verify.out" 2>&1; then ok=1; break; fi
    if [ "$waited" -ge "$WAIT_FLIP" ]; then break; fi
    sleep 10; waited=$((waited + 10))
  done
  if [ -s "$S/verify.out" ]; then tee -a "$LOG" < "$S/verify.out"; fi
  [ $ok = 1 ] || { say "  the zone answered ${ip:-nothing} for $APEX and the checks did not pass"; return 1; }
  say "  $APEX and $WWW answer from Cloudflare ($ip), pages and redirects equal the Vercel baseline"
  $TLS_CHECK "$ip" "$APEX" "$WWW" 2>&1 | tee -a "$LOG" || return 1

  say "  3b. through DNS over HTTPS (what a visitor's resolver sees), retried for about a minute"
  ok=0; waited=0
  while :; do
    if run_verify --doh > "$S/verify.out" 2>&1; then ok=1; break; fi
    if [ "$waited" -ge "$WAIT_VERIFY" ]; then break; fi
    sleep 5; waited=$((waited + 5))
  done
  tee -a "$LOG" < "$S/verify.out"
  [ $ok = 1 ] || { say "  through DNS over HTTPS the site does not answer like the baseline"; return 1; }
  if grep -q 'answered by: Vercel' "$S/verify.out"; then
    say "  visitors' resolvers still get Vercel's answer (the same site): the old records live at most 5 minutes, the old nameservers at most a day"
    FOLLOWUP+=("run 'bash $HERE/cutover.sh verify' again in ten minutes and tomorrow: some resolvers still send visitors to Vercel (the same site)")
  else
    say "  visitors now get the Worker"
  fi
  return 0
}

mail_after() {
  say "== 3c. the mail records again: they must be exactly what they were before step 2"
  mail_check "$MAIL_AFTER" | sed 's/^/    /' | tee -a "$LOG" || return 1
  if [ -f "$MAIL_BEFORE" ]; then
    mail_unchanged "$MAIL_BEFORE" "$MAIL_AFTER" || return 1
    say "    MX and TXT identical to the snapshot taken before the move"
  fi
}

# ---- rollback ---------------------------------------------------------------------------------------------------
do_rollback() {
  local rc=0
  say "== ROLLBACK: Worker domains off, temporary Vercel records back"
  undo_dns || rc=1
  if [ $rc = 0 ] && [ -s "$SNAP" ]; then
    mv "$SNAP" "$SNAP.restored-$(date +%Y%m%dT%H%M%S)"; say "  DNS restored; the snapshot was renamed so that a new run is allowed"
  elif [ $rc != 0 ]; then
    say "  DNS is NOT fully restored: fix the lines above by hand. The snapshot stays at $SNAP (readable copy: $SNAP_TXT)"
  fi
  say "  the mail records, untouched by the rollback:"
  mail_check "$S/mail-rollback.json" | sed 's/^/    /' | tee -a "$LOG" || say "  WARNING: the mail records differ from the snapshot. Do not touch anything else and tell Claude"
  say "== rollback done: open https://$APEX from outside and check it (it is served by Vercel again; a Cloudflare certificate for the Worker domains may remain under SSL/TLS > Edge Certificates and can be deleted there)"
  return $rc
}

# ---- summary ----------------------------------------------------------------------------------------------------
summary() {       # full | part
  local f
  if [ ${#FOLLOWUP[@]} -gt 0 ]; then
    say "NEEDS YOU:"
    for f in "${FOLLOWUP[@]}"; do say "  - $f"; done
  fi
  if [ "${1:-full}" != full ]; then say "step finished (see above)"; return 0; fi
  say "$SLUG DONE. $APEX and $WWW are served by the Worker $WORKER; www redirects to $APEX; the mail records were not touched."
  cat <<EOF | tee -a "$LOG"

MAIL CHECK (do it now, 5 minutes; this script never sends mail):
  1. From a personal address that is NOT @$DOMAIN, send one message to info@$DOMAIN (the address printed on the site; ask Marv
     whether it is a mailbox, a group or an alias and which mailbox receives it). Subject: "DNS move test 1".
  2. Marv (or whoever reads that mailbox) confirms it arrived, in the inbox or in Spam, and replies to it from his
     @$DOMAIN mailbox. You confirm the reply arrived.
  3. Repeat once tomorrow ("DNS move test 2"): some resolvers keep the old nameservers for up to a day, and both ways must work.
  Google's own check of the MX records (optional): https://toolbox.googleapps.com/apps/checkmx/ with $DOMAIN.

AFTER THIS (printed, NOT run):
  - Leave EVERYTHING on Vercel alone for at least a week: resolvers that still hold the old nameservers keep asking Vercel,
    and the Vercel DNS zone and alias are the rollback path. Vercel issues wildcard certificates only with its own nameservers
    (its docs: DNS-01 challenge), and the certificate it serves for $DOMAIN is a wildcard that expires on 2 Dec 2026, so
    rolling back to Vercel is only dependable until then.
  - Then, if all is well, delete the Vercel side yourself (check the names with 'vercel alias ls --scope chewy-bytes' first):
      vercel alias rm $APEX --scope chewy-bytes
      echo y | vercel project rm marv-brown-films-web --scope chewy-bytes
      vercel domains rm $DOMAIN --yes --scope chewy-bytes      # this deletes Vercel's copy of the DNS zone
  - Keep $SNAP_TXT (only DNS records, no secrets) for as long as you want the rollback option.
  - Open https://$APEX in a browser and click the email address and the three videos once.
  - Handover to Marv: docs/cloudflare/HANDOVER.md.
EOF
}

status_cmd() {
  say "== status (read only)"
  local line st
  line=$(zone_line); st=${line%%$'\t'*}
  say "  zone $ZONE: ${st:-unreadable} ($(cut -f2 <<<"$line"), nameservers $(cut -f3 <<<"$line"))"
  say "  .com registry delegates $DOMAIN to: $(registry_ns | sed 's/^$/(nothing)/')   (Cloudflare's: $(want_ns); the old ones: ${OLD_NS[*]})"
  local r
  for r in "${RESOLVERS[@]}"; do say "  resolver $r reports NS: $(dig_names @"$r" "$DOMAIN" NS | sed 's/^$/(nothing)/')"; done
  say "  Worker domains attached:"
  local h svc
  for h in "$APEX" "$WWW"; do
    svc=$(cfapi GET "/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/domains?hostname=$h" | python3 -c "import sys,json;r=json.load(sys.stdin).get('result') or [];print(r[0].get('service','') if r else '(none)')" 2>/dev/null || echo "(unreadable)")
    say "    $h: $svc"
  done
  say "  A/AAAA/CNAME records at the apex, www and wildcard:"
  { temp_records || true; } | awk -F'\t' '{printf "    %s %s -> %s (proxied=%s, ttl %s)\n",$2,$3,$4,$5,$6}' | tee -a "$LOG"
  say "  the website is answered by: $(curl -s --doh-url https://cloudflare-dns.com/dns-query -o /dev/null -D - --max-time 30 "$LIVE_URL/" | tr -d '\r' | awk 'tolower($1)=="x-vercel-id:"{v=1} tolower($1)=="server:"{s=$2} END{print (v?"Vercel":(s?s:"nobody"))}')"
  say "  mail records:"
  mail_check "$S/mail-status.json" | sed 's/^/    /' | tee -a "$LOG" || true
}

case "${1:-all}" in
  status)   STANDALONE=1; status_cmd; exit 0 ;;
  rollback) STANDALONE=1; do_rollback; exit $? ;;
  mail)     STANDALONE=1; mail_check "$S/mail-check.json" | tee -a "$LOG" || die "the mail records differ from the snapshot (see above)"; exit 0 ;;
  verify)   STANDALONE=1
            [ -f "$BASE" ] || die "no baseline at $BASE: the full run never reached step 2 (nothing to verify against)"
            verify_live || die "live checks failed: the hostnames moved but the answers are not what Vercel gave. Roll back, or wait and run this again"
            mail_after || die "the mail records changed (see above)"
            say "live checks passed"; summary part; exit 0 ;;
  all)      ;;
  *)        echo "usage: bash $0 [status|verify|mail|rollback]"; exit 2 ;;
esac

preflight
deploy_worker
dns_phase
verify_live || die "the live checks failed after the DNS move. The Worker is deployed and the hostnames point at it; Vercel is untouched. Fix forward, or roll back"
mail_after || die "THE MAIL RECORDS CHANGED during the move (see above). Roll back and tell Claude"
summary
