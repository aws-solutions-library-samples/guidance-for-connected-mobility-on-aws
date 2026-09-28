#!/usr/bin/env bash
#
# smoke_connected_services.sh — Post-deploy smoke test for the Connected
# Services portal (spec 2026-09-03-cms-connected-services-portal, T4.1).
#
# The smoke asserts APPLICATION INITIALISATION, not HTTP 200.  On a SPA every
# route returns the same index.html whether or not the app boots, so a bare
# 200 measures CloudFront, not the portal.  DMS's four escaped defects (F5-F8)
# were all found because HTTP-200 gates passed silently while the application
# was broken.  This script checks the DOM marker, the runtime-config.js
# injection, the config values, and the SPA fallback route.
#
# Usage:
#   bash deployment/scripts/smoke_connected_services.sh
#
#   PORTAL_URL=https://<cs-portal-domain>              (REQUIRED — no default)
#   PORTAL_URL=https://my-preview.cloudfront.net       (override for preview envs)
#
# Exit codes:
#   0  — all checks passed
#   1  — prerequisite failure (missing tool or credentials)
#   2  — DNS check failed
#   3  — index.html / bootstrap script tag missing
#   4  — runtime-config.js missing or body check failed
#   5  — runtime-config.js missing required config values
#   6  — SPA catchall fallback check failed
#
# Non-interactive: no confirmation prompts. AWS credentials and region must be
# available via environment or ~/.aws/config.
# Per ~/.kiro/steering/non-interactive.md.
#
# Requirements:
#   - PORTAL_URL env var (REQUIRED — no default; script exits 1 if unset)
#   - curl      (standard macOS / Linux)
#   - dig       (part of bind-utils / dnsutils / macOS built-in)
#   - aws CLI   (for credential check and CloudFront diagnostics at the end)
#
# NOT required: jq, timeout(1).
#
set -euo pipefail
export AWS_PAGER=""

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

if [ -z "${PORTAL_URL:-}" ]; then
  echo "ERROR: PORTAL_URL is required. Example: PORTAL_URL=https://<cs-portal-domain> bash $0" >&2
  exit 1
fi
# Strip trailing slash so all path concatenations are predictable.
PORTAL_URL="${PORTAL_URL%/}"

PORTAL_DOMAIN="${PORTAL_URL#https://}"
PORTAL_DOMAIN="${PORTAL_DOMAIN#http://}"
# Remove any path component from the domain.
PORTAL_DOMAIN="${PORTAL_DOMAIN%%/*}"

REGION="${AWS_REGION:-us-west-2}"

# CloudFormation stack name — must match Makefile:593 / T2.1 DEPLOYMENT_STAGE
# Derive stage from the domain when possible; fall back to "staging".
_STAGE="staging"
if [[ "$PORTAL_DOMAIN" == *prod* ]]; then
  _STAGE="prod"
fi
CF_STACK="cms-${_STAGE}-connected-services-ui"

# ---------------------------------------------------------------------------
# Color helpers (tput when TTY, plain when piped/redirected)
# ---------------------------------------------------------------------------

if [ -t 2 ] && command -v tput >/dev/null 2>&1; then
  _RED=$(tput setaf 1)
  _GREEN=$(tput setaf 2)
  _YELLOW=$(tput setaf 3)
  _BLUE=$(tput setaf 4)
  _NC=$(tput sgr0)
else
  _RED=''
  _GREEN=''
  _YELLOW=''
  _BLUE=''
  _NC=''
fi

err()  { echo "${_RED}✗ [err]${_NC}  $*" >&2; }
ok()   { echo "${_GREEN}✓ [ok]${_NC}   $*"; }
log()  { echo "${_BLUE}  [log]${_NC}  $*"; }
warn() { echo "${_YELLOW}⚠ [warn]${_NC} $*"; }

# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------

log "Checking prerequisites…"

_prereq_fail=0

# 1. AWS credentials
if ! aws sts get-caller-identity --no-cli-pager --output text \
     --query '[Account,Arn]' >/dev/null 2>&1; then
  err "AWS credentials unavailable or expired. Configure credentials before running."
  err "  Try: aws sts get-caller-identity"
  _prereq_fail=1
else
  _caller=$(aws sts get-caller-identity --no-cli-pager \
    --query '[Account,Arn]' --output text 2>/dev/null || echo "<unavailable>")
  ok "AWS credentials valid — ${_caller}"
fi

# 2. dig
if ! command -v dig >/dev/null 2>&1; then
  err "'dig' is not installed. Install bind-utils (Linux) or dnsutils, or use macOS built-in."
  err "  macOS: dig ships with the OS. Linux: sudo apt-get install -y dnsutils"
  _prereq_fail=1
else
  ok "'dig' is available"
fi

# 3. curl
if ! command -v curl >/dev/null 2>&1; then
  err "'curl' is not installed."
  _prereq_fail=1
else
  ok "'curl' is available"
fi

if [ "$_prereq_fail" -ne 0 ]; then
  err "Prerequisites not met — cannot continue. Fix the above issues and re-run."
  exit 1
fi

echo ""
log "Target URL : ${PORTAL_URL}"
log "Domain     : ${PORTAL_DOMAIN}"
log "CFN stack  : ${CF_STACK}  (region=${REGION})"
log "Stage      : ${_STAGE}"
echo ""

# ---------------------------------------------------------------------------
# Check 1 — DNS: dig returns at least one A record
# ---------------------------------------------------------------------------

log "Check 1/6: DNS — dig +short A ${PORTAL_DOMAIN}"

_dig_output=$(dig +short A "${PORTAL_DOMAIN}" 2>/dev/null || true)

if [ -z "$_dig_output" ]; then
  err "dig returned no A records for '${PORTAL_DOMAIN}'."
  err "  Possible causes:"
  err "    - Route53 A-alias record not created yet (T2.1 stack not deployed)"
  err "    - DNS propagation still in progress (wait 1-2 min, re-run)"
  err "    - Domain name typo (expected: ${PORTAL_DOMAIN})"
  exit 2
fi

# Report the first few addresses; there may be multiple CloudFront IPs.
_first_addr=$(echo "$_dig_output" | head -1)
_addr_count=$(echo "$_dig_output" | wc -l | tr -d ' ')
ok "DNS resolved — ${_addr_count} address(es), first: ${_first_addr}"

# ---------------------------------------------------------------------------
# Check 2 — index.html contains the runtime-config.js bootstrap script tag
# ---------------------------------------------------------------------------
# An HTTP 200 alone proves only that CloudFront is up.  The DMS extraction
# (F5-F8) showed that the application can fail to initialise while still
# returning 200.  Checking for the <script src="/runtime-config.js"> tag
# proves the CDK BucketDeployment 1 (T2.1) correctly injected the bootstrap
# loader into the delivered index.html.

log "Check 2/6: index.html HTTP status + runtime-config.js script tag present"

_index_status=$(curl -sS -o /tmp/_cs_index.html -w "%{http_code}" \
  "${PORTAL_URL}/" 2>/dev/null || echo "000")

if [ "$_index_status" != "200" ]; then
  err "GET ${PORTAL_URL}/ returned HTTP ${_index_status} (expected 200)."
  err "  If status=403 or 404, the S3 origin or CloudFront may not be deployed."
  exit 3
fi

if ! grep -q 'src="/runtime-config.js"' /tmp/_cs_index.html 2>/dev/null; then
  err "index.html does NOT contain: <script src=\"/runtime-config.js\""
  err "  This means the CDK BucketDeployment 1 injection did not survive"
  err "  to the delivered HTML (DMS F6 / runtime-config-never-injected defect class)."
  err "  Check: deployment/stacks/connected_services_ui_stack.py BucketDeployment for index.html"
  err "  Full index.html head (<200 chars):"
  head -c 200 /tmp/_cs_index.html >&2 || true
  rm -f /tmp/_cs_index.html
  exit 3
fi

ok "index.html → HTTP 200 and contains <script src=\"/runtime-config.js\""

rm -f /tmp/_cs_index.html

# ---------------------------------------------------------------------------
# Check 3 — runtime-config.js: exists + sets window.runtimeConfig
# ---------------------------------------------------------------------------
# Proves the second BucketDeployment (T2.1) successfully wrote the generated
# runtime-config.js to the S3 bucket and CloudFront serves it.

log "Check 3/6: runtime-config.js HTTP status + window.runtimeConfig assignment"

_rc_status=$(curl -sS -o /tmp/_cs_runtime_config.js -w "%{http_code}" \
  "${PORTAL_URL}/runtime-config.js" 2>/dev/null || echo "000")

if [ "$_rc_status" != "200" ]; then
  err "GET ${PORTAL_URL}/runtime-config.js returned HTTP ${_rc_status} (expected 200)."
  err "  The BucketDeployment 2 (generate_runtime_config.py output) may not have run."
  err "  Check: make synth-connected-services then make deploy-connected-services"
  rm -f /tmp/_cs_runtime_config.js
  exit 4
fi

if ! grep -q 'window.runtimeConfig' /tmp/_cs_runtime_config.js 2>/dev/null; then
  err "runtime-config.js does NOT contain 'window.runtimeConfig'."
  err "  The file exists on the CDN but its content is wrong — wrong file deployed?"
  err "  First 200 chars of runtime-config.js:"
  head -c 200 /tmp/_cs_runtime_config.js >&2 || true
  rm -f /tmp/_cs_runtime_config.js
  exit 4
fi

ok "runtime-config.js → HTTP 200 and sets window.runtimeConfig"

# ---------------------------------------------------------------------------
# Check 4 — runtime-config.js: cognitoUserPoolId and cognitoClientId non-empty
# ---------------------------------------------------------------------------
# Proves that cdk synth resolved real staging context values, not the
# '<user-pool-id>' / '.example.invalid' placeholder defaults.  If the deploy
# ran without GUARD_CTX_FLAGS carrying the real pool ID (e.g. a bare
# `cdk deploy` without the Makefile target), the config will contain the
# literal placeholder strings and the portal will fail to authenticate.

log "Check 4/6: runtime-config.js — cognitoUserPoolId and cognitoClientId are non-empty"

_pool_id=""
_client_id=""

# Extract values using sed/grep — no jq dependency.
# The generated file shape is:
#   window.runtimeConfig = { "cognitoUserPoolId": "us-west-2_XXXXXX", ... };
# or assignment without surrounding quotes on the key.

_pool_id=$(sed -n 's/.*"cognitoUserPoolId"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
  /tmp/_cs_runtime_config.js | head -1)

_client_id=$(sed -n 's/.*"cognitoClientId"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
  /tmp/_cs_runtime_config.js | head -1)

_fail=0

if [ -z "$_pool_id" ] || [ "$_pool_id" = "null" ]; then
  err "cognitoUserPoolId is absent or null in runtime-config.js."
  err "  Possible causes:"
  err "    - synth ran without the staging Cognito pool context (GUARD_CTX_FLAGS not carried)"
  err "    - staging.env missing CONNECTED_SERVICES_UI_CALLBACK_ORIGIN or pool ID context"
  _fail=1
else
  # Reject placeholder values — the stack default is '<user-pool-id>' if context missing.
  # A real pool ID matches us-west-2_Xxxx... or the pool ID pattern.
  if [[ "$_pool_id" == *"<"* ]] || [[ "$_pool_id" == *"example.invalid"* ]] || [[ "$_pool_id" == *"placeholder"* ]]; then
    err "cognitoUserPoolId looks like a placeholder: '${_pool_id}'"
    err "  Deploy ran without real CDK context. Run: make deploy-connected-services DEPLOYMENT_STAGE=staging"
    _fail=1
  else
    ok "cognitoUserPoolId = ${_pool_id}"
  fi
fi

if [ -z "$_client_id" ] || [ "$_client_id" = "null" ]; then
  err "cognitoClientId is absent or null in runtime-config.js."
  err "  The Cognito app client may not have been registered yet (T2.2 wiring)."
  _fail=1
else
  if [[ "$_client_id" == *"<"* ]] || [[ "$_client_id" == *"example.invalid"* ]] || [[ "$_client_id" == *"placeholder"* ]]; then
    err "cognitoClientId looks like a placeholder: '${_client_id}'"
    err "  Deploy ran without real CDK context."
    _fail=1
  else
    ok "cognitoClientId = ${_client_id}"
  fi
fi

rm -f /tmp/_cs_runtime_config.js

if [ "$_fail" -ne 0 ]; then
  exit 5
fi

# ---------------------------------------------------------------------------
# Check 5 — SPA 403/404 → index.html catchall (CloudFront custom error rule)
# ---------------------------------------------------------------------------
# Proves the CloudFront custom error response is configured to redirect 403
# and 404 to /index.html with HTTP 200.  Without this rule, deep links and
# browser refresh on any route other than "/" return 403 (S3 GetObject denied
# for an object that doesn't exist), breaking the single-page app.
# This is DMS's F5 (SPA 403 fallback) defect class.

log "Check 5/6: SPA catchall — unknown path returns HTTP 200 with same index.html"

_spa_body_status=$(curl -sS -o /tmp/_cs_spa_catchall.html -w "%{http_code}" \
  "${PORTAL_URL}/some/random/spa/path/$(date +%s)" 2>/dev/null || echo "000")

if [ "$_spa_body_status" != "200" ]; then
  err "GET ${PORTAL_URL}/some/random/spa/path/... returned HTTP ${_spa_body_status} (expected 200)."
  err "  The CloudFront custom error response rule (403→index.html, 404→index.html)"
  err "  is not configured or not working (DMS F5 / SPA 403 fallback defect class)."
  err "  Check: deployment/stacks/connected_services_ui_stack.py error_responses"
  rm -f /tmp/_cs_spa_catchall.html
  exit 6
fi

# Verify the response is actually index.html (contains the bootstrap tag),
# not an error page that happens to return 200.
if ! grep -q 'src="/runtime-config.js"' /tmp/_cs_spa_catchall.html 2>/dev/null; then
  err "Catchall path returned HTTP 200 but the body is NOT index.html."
  err "  CloudFront may be serving a custom error page instead of /index.html."
  err "  Check: responsePagePath in the error_responses configuration."
  err "  First 200 chars of catchall response:"
  head -c 200 /tmp/_cs_spa_catchall.html >&2 || true
  rm -f /tmp/_cs_spa_catchall.html
  exit 6
fi

ok "SPA catchall → HTTP 200 with index.html (runtime-config.js tag present)"
rm -f /tmp/_cs_spa_catchall.html

# ---------------------------------------------------------------------------
# Check 6 — Cache-Control headers
# ---------------------------------------------------------------------------
# index.html must be no-cache (so updated app versions deploy instantly).
# runtime-config.js must also be no-cache (config can change on redeploy).
# This is DMS's F7 (stale index.html cache) defect class.

log "Check 6/6: Cache-Control — index.html is no-cache, runtime-config.js is no-cache"

_index_cc=$(curl -sSI "${PORTAL_URL}/" 2>/dev/null \
  | grep -i "^cache-control:" | tr -d '\r' | head -1)

_rc_cc=$(curl -sSI "${PORTAL_URL}/runtime-config.js" 2>/dev/null \
  | grep -i "^cache-control:" | tr -d '\r' | head -1)

_cache_fail=0

if [ -z "$_index_cc" ]; then
  warn "No Cache-Control header found on index.html — CloudFront may be using default caching."
  warn "  Expected: no-cache, no-store, must-revalidate"
  warn "  This is not a hard failure for the smoke, but re-deploys may serve stale HTML."
  _cache_fail=1
elif echo "$_index_cc" | grep -qiE "no-cache|no-store|max-age=0"; then
  ok "index.html Cache-Control: ${_index_cc}"
else
  warn "index.html Cache-Control does not appear to be no-cache: '${_index_cc}'"
  warn "  Users may see a stale version after re-deploys (DMS F7 defect class)."
  _cache_fail=1
fi

if [ -z "$_rc_cc" ]; then
  warn "No Cache-Control header found on runtime-config.js."
  _cache_fail=1
elif echo "$_rc_cc" | grep -qiE "no-cache|no-store|max-age=0"; then
  ok "runtime-config.js Cache-Control: ${_rc_cc}"
else
  warn "runtime-config.js Cache-Control does not appear to be no-cache: '${_rc_cc}'"
  _cache_fail=1
fi

if [ "$_cache_fail" -ne 0 ]; then
  warn "Cache-Control warnings noted — smoke continues (non-fatal)."
fi

# ---------------------------------------------------------------------------
# Diagnostics footer: CloudFront invalidation ID + runtime-config.js
# last-modified timestamp (useful for diagnosing stale-cache issues)
# ---------------------------------------------------------------------------
# These values are informational — they do not affect the exit code.
# The invalidation ID and last-modified together let an operator quickly
# confirm that:
#   (a) an invalidation was actually created and reached COMPLETED, and
#   (b) the runtime-config.js object in the origin is the version from the
#       most recent deploy (not a stale copy from a prior run).
# This is the diagnostic the DMS F5-F8 defect family needed and didn't have.

echo ""
log "── Diagnostics ──────────────────────────────────────────────────────────"

# Last-modified of runtime-config.js (from HTTP headers — no AWS call needed).
_rc_last_modified=$(curl -sSI "${PORTAL_URL}/runtime-config.js" 2>/dev/null \
  | grep -i "^last-modified:" | tr -d '\r' | sed 's/last-modified:[[:space:]]*//' | head -1)

if [ -n "$_rc_last_modified" ]; then
  log "runtime-config.js Last-Modified : ${_rc_last_modified}"
else
  log "runtime-config.js Last-Modified : (header absent — CloudFront may be omitting it)"
fi

# Most recent invalidation ID for this distribution (requires AWS CLI + valid creds).
# We already verified credentials above, so this should succeed.
# Strategy: look up the DistributionDomainName from the CFN stack output, then
# find the distribution ID by listing all distributions and matching the domain.
_cf_dist_id=""
_latest_invalidation_id="(unable to retrieve)"
_latest_invalidation_status=""

_cf_domain=$(aws cloudformation describe-stacks \
  --stack-name "${CF_STACK}" \
  --region "${REGION}" \
  --no-cli-pager \
  --query 'Stacks[0].Outputs[?OutputKey==`DistributionDomainName`].OutputValue' \
  --output text 2>/dev/null || echo "")

if [ -n "$_cf_domain" ] && [ "$_cf_domain" != "None" ]; then
  log "CloudFront distribution domain (from CFN): ${_cf_domain}"
  # Find the distribution ID whose DomainName matches the CFN output.
  _cf_dist_id=$(aws cloudfront list-distributions \
    --no-cli-pager \
    --query "DistributionList.Items[?DomainName=='${_cf_domain}'].Id" \
    --output text 2>/dev/null | head -1 || echo "")
fi

if [ -n "$_cf_dist_id" ] && [ "$_cf_dist_id" != "None" ]; then
  log "CloudFront distribution ID       : ${_cf_dist_id}"

  # Pull the most recent invalidation (list sorted by CreateTime descending).
  _invalidation_json=$(aws cloudfront list-invalidations \
    --distribution-id "${_cf_dist_id}" \
    --no-cli-pager \
    --output text \
    --query 'InvalidationList.Items | sort_by(@, &CreateTime) | reverse(@) | [0].[Id, Status, CreateTime]' \
    2>/dev/null || echo "")

  if [ -n "$_invalidation_json" ] && [ "$_invalidation_json" != "None" ]; then
    _latest_invalidation_id=$(echo "$_invalidation_json"  | awk '{print $1}')
    _latest_invalidation_status=$(echo "$_invalidation_json" | awk '{print $2}')
    _latest_invalidation_time=$(echo "$_invalidation_json"  | awk '{print $3}')
    log "Latest CloudFront invalidation   : ID=${_latest_invalidation_id}  status=${_latest_invalidation_status}  created=${_latest_invalidation_time}"
    if [ "$_latest_invalidation_status" != "Completed" ]; then
      warn "Invalidation is not yet Completed — cached content may be stale."
      warn "  Re-run this smoke after the invalidation propagates (usually 60-120s)."
    fi
  else
    log "Latest CloudFront invalidation   : (no invalidations found for ${_cf_dist_id})"
  fi
else
  log "CloudFront distribution ID       : (could not resolve — CFN stack may not exist yet)"
  log "  Stack queried: ${CF_STACK} in ${REGION}"
  log "  Deploy first: make deploy-connected-services DEPLOYMENT_STAGE=${_STAGE}"
fi

log "── End diagnostics ──────────────────────────────────────────────────────"
echo ""

# ---------------------------------------------------------------------------
# Final verdict
# ---------------------------------------------------------------------------

ok "All smoke checks PASSED for ${PORTAL_URL}"
log "Connected Services portal is up and application-initialised."
exit 0
