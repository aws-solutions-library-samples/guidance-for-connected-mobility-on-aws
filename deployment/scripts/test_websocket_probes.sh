#!/usr/bin/env bash
#
# test_websocket_probes.sh — post-deploy WebSocket auth smoke probes.
#
# Resolves the deployed WebSocketEndpoint from the cms-<stage>-ui CFN stack and
# checks the upgrade-response HTTP status for three cases:
#   1. no token        → expect 401 (authorizer rejects)
#   2. garbage token    → expect 401/403
#   3. valid Cognito JWT → expect 101 Switching Protocols
#
# Probe 3 needs a JWT. Provide one of:
#   - CMS_TEST_JWT=<id-token>                               (pre-acquired), OR
#   - CMS_TEST_USER + CMS_TEST_PASSWORD                     (admin-initiate-auth)
# If neither is provided, probes 1-2 run and probe 3 is SKIPPED (non-fatal).
#
# Deps: aws CLI, curl, openssl. Reads the endpoint from CFN every run (no hardcoding).
# Spec: .kiro/specs/2026-06-15-cms-websocket-api-auth-gap/  (Group 3)
set -euo pipefail

STAGE="${DEPLOYMENT_STAGE:-staging}"
REGION="${AWS_REGION:-us-west-2}"
STACK="cms-${STAGE}-ui"

log() { printf '\033[0;34m[ws-probe]\033[0m %s\n' "$*"; }
ok()  { printf '\033[0;32m[ws-probe]\033[0m %s\n' "$*"; }
err() { printf '\033[0;31m[ws-probe]\033[0m %s\n' "$*" >&2; }

WSS=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='WebSocketEndpoint'].OutputValue" --output text)
if [ -z "$WSS" ] || [ "$WSS" = "None" ]; then
  err "WebSocketEndpoint output not found on $STACK ($REGION)"; exit 1
fi
# curl speaks https for the upgrade; map wss:// → https://
HTTPS="https://${WSS#wss://}"
log "endpoint: $WSS"

probe() {  # $1=label  $2=query-suffix  $3=expected-csv
  local label="$1" qs="$2" expect="$3"
  local code
  code=$(curl -sS -o /dev/null -w '%{http_code}' --http1.1 --max-time 12 \
    -H "Connection: Upgrade" -H "Upgrade: websocket" \
    -H "Sec-WebSocket-Version: 13" \
    -H "Sec-WebSocket-Key: $(openssl rand -base64 16)" \
    "${HTTPS}${qs}" || true)
  if printf '%s' ",$expect," | grep -q ",$code,"; then
    ok "PASS $label → HTTP $code (expected $expect)"; return 0
  fi
  err "FAIL $label → HTTP $code (expected $expect)"; return 1
}

rc=0
# All probes carry fleetId (the $connect handler requires it); auth is the only
# variable, so a 401 unambiguously means the authorizer rejected.
probe "unauth (no token)"   "?fleetId=smoke-test"               "401" || rc=1
probe "invalid token"       "?token=garbage&fleetId=smoke-test" "401,403" || rc=1

# Acquire a JWT for the positive probe, if possible.
JWT="${CMS_TEST_JWT:-}"
if [ -z "$JWT" ] && [ -n "${CMS_TEST_USER:-}" ] && [ -n "${CMS_TEST_PASSWORD:-}" ]; then
  CLIENT=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?contains(OutputKey,'ClientId')].OutputValue | [0]" --output text 2>/dev/null || true)
  if [ -n "$CLIENT" ] && [ "$CLIENT" != "None" ]; then
    log "acquiring id-token via initiate-auth USER_PASSWORD_AUTH (client=$CLIENT)"
    JWT=$(aws cognito-idp initiate-auth --region "$REGION" \
      --client-id "$CLIENT" --auth-flow USER_PASSWORD_AUTH \
      --auth-parameters "USERNAME=${CMS_TEST_USER},PASSWORD=${CMS_TEST_PASSWORD}" \
      --query 'AuthenticationResult.IdToken' --output text 2>/dev/null || true)
  fi
fi

if [ -n "$JWT" ] && [ "$JWT" != "None" ]; then
  probe "valid token" "?token=${JWT}&fleetId=smoke-test" "101" || rc=1
else
  log "SKIP valid-token probe — set CMS_TEST_JWT or CMS_TEST_USER/CMS_TEST_PASSWORD to enable (101 check)"
fi

[ "$rc" -eq 0 ] && ok "websocket auth probes OK" || err "websocket auth probes FAILED"
exit "$rc"
