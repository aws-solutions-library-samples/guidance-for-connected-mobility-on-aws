#!/usr/bin/env bash
#
# test_auth_flows.sh — Verify that previously-unauthenticated routes accept
# a valid Cognito JWT and do NOT return 401/403.
#
# Usage:
#   CMS_TEST_JWT=<cognito-id-token> bash test_auth_flows.sh <stage> [region] [profile]
#
# Arguments:
#   <stage>     CFN stage (e.g., staging, prod). Default: staging.
#   [region]    AWS region. Default: us-west-2.
#   [profile]   AWS CLI profile. Default: unset (uses ambient credentials).
#
# Required env:
#   CMS_TEST_JWT   A valid Cognito ID token for the target User Pool (platform-admin
#                  or fleet-operator role). Obtain via the seed_driver_users.py admin
#                  path or via aws cognito-idp initiate-auth.
#
# Exit codes:
#   0   All 25 probes passed (none returned 401/403).
#   1   One or more probes returned 401/403 (listed in output).
#   2   Pre-flight failure (missing env, stack not found, etc.).
#
# Coverage: 25 routes — simulation (14) + commands (6) + predictive-agent (5).
# Each probe asserts the response status is NOT 401 or 403 (auth rejection).
# POST endpoints for new resources may legitimately return 4xx (e.g., 400 for
# missing required fields, 404 for unknown IDs) — those are NOT failures here.
#
# Spec: .kiro/specs/2026-06-11-cms-api-authorizer-template-fix/

set -euo pipefail
export AWS_PAGER=""

# ── Pre-flight ────────────────────────────────────────────────────────────────

if [ -z "${CMS_TEST_JWT:-}" ]; then
  echo "ERROR: CMS_TEST_JWT is not set." >&2
  echo "  Obtain a token with:" >&2
  echo "    aws cognito-idp initiate-auth --auth-flow USER_PASSWORD_AUTH \\" >&2
  echo "      --auth-parameters USERNAME=<user>,PASSWORD=<pass> \\" >&2
  echo "      --client-id <pool-client-id> | jq -r '.AuthenticationResult.IdToken'" >&2
  exit 2
fi

STAGE="${1:-staging}"
REGION="${2:-us-west-2}"
PROFILE_ARGS=()
if [ -n "${3:-}" ]; then
  PROFILE_ARGS=(--profile "$3")
fi

CFN_ARGS=(--region "$REGION" "${PROFILE_ARGS[@]}" --no-cli-pager --output text)

# ── Helper: get a CFN stack output value ─────────────────────────────────────

cfn_output() {
  local stack_name="$1" output_key="$2"
  aws cloudformation describe-stacks \
    --stack-name "$stack_name" \
    --query "Stacks[0].Outputs[?OutputKey==\`${output_key}\`].OutputValue" \
    "${CFN_ARGS[@]}" 2>/dev/null || true
}

# ── Resolve endpoint base URLs ────────────────────────────────────────────────

SIM_BASE=$(cfn_output "cms-${STAGE}-simulation" "SimulationApiUrl")
CMD_BASE=$(cfn_output "cms-${STAGE}-commands"   "CommandsApiUrl")
PA_BASE=$(cfn_output  "cms-${STAGE}-predictive-agent" "PredictiveAgentApiUrl")

if [ -z "$SIM_BASE" ]; then
  echo "ERROR: SimulationApiUrl not found in cms-${STAGE}-simulation stack." >&2
  exit 2
fi
if [ -z "$CMD_BASE" ]; then
  echo "ERROR: CommandsApiUrl not found in cms-${STAGE}-commands stack." >&2
  exit 2
fi

# Strip trailing slash
SIM_BASE="${SIM_BASE%/}"
CMD_BASE="${CMD_BASE%/}"
[ -n "$PA_BASE" ] && PA_BASE="${PA_BASE%/}"

AUTH_HEADER="Authorization: Bearer ${CMS_TEST_JWT}"

FAILED=()

# ── probe: curl and assert NOT 401/403 ───────────────────────────────────────

probe() {
  local label="$1" method="$2" url="$3"
  local status
  status=$(curl -s -o /dev/null -w "%{http_code}" -X "$method" \
    -H "$AUTH_HEADER" -H "Content-Type: application/json" \
    --max-time 10 "$url")
  if [ "$status" = "401" ] || [ "$status" = "403" ]; then
    echo "  FAIL [$status]  $method $label"
    FAILED+=("$method $label ($status)")
  else
    echo "  PASS [$status]  $method $label"
  fi
}

# ── Simulation routes (14) ────────────────────────────────────────────────────

echo "==> Simulation (${SIM_BASE})"
probe "/api/simulation"                              GET  "${SIM_BASE}/api/simulation"
probe "/api/simulation/start"                        POST "${SIM_BASE}/api/simulation/start"
probe "/api/simulation/status/test-id"               GET  "${SIM_BASE}/api/simulation/status/test-id"
probe "/api/simulation/stop/test-id"                 POST "${SIM_BASE}/api/simulation/stop/test-id"
probe "/api/simulation/list"                         GET  "${SIM_BASE}/api/simulation/list"
probe "/api/simulation/health"                       GET  "${SIM_BASE}/api/simulation/health"
probe "/api/simulation/drivers"                      GET  "${SIM_BASE}/api/simulation/drivers"
probe "/api/simulation/presets"                      GET  "${SIM_BASE}/api/simulation/presets"
probe "/api/simulation/campaigns"                    GET  "${SIM_BASE}/api/simulation/campaigns"
probe "/api/simulation/discover-iot-endpoint"        GET  "${SIM_BASE}/api/simulation/discover-iot-endpoint"
probe "/api/simulation/agent/start"                  POST "${SIM_BASE}/api/simulation/agent/start"
probe "/api/simulation/agent/stop"                   POST "${SIM_BASE}/api/simulation/agent/stop"
probe "/api/simulation/agent/status"                 GET  "${SIM_BASE}/api/simulation/agent/status"
probe "/api/simulation/agent/logs/test-vin"          GET  "${SIM_BASE}/api/simulation/agent/logs/test-vin"

# ── Commands routes (6) ───────────────────────────────────────────────────────

echo "==> Commands (${CMD_BASE})"
probe "/api/commands/catalog"              GET    "${CMD_BASE}/api/commands/catalog"
probe "/api/commands/{vehicleId} POST"     POST   "${CMD_BASE}/api/commands/test-vehicle"
probe "/api/commands/{vehicleId} GET"      GET    "${CMD_BASE}/api/commands/test-vehicle"
probe "/api/geofences POST"                POST   "${CMD_BASE}/api/geofences"
probe "/api/geofences/{vehicleId} GET"     GET    "${CMD_BASE}/api/geofences/test-vehicle"
probe "/api/geofences/{vehicleId} DELETE"  DELETE "${CMD_BASE}/api/geofences/test-vehicle"

# ── Predictive-agent routes (5) ───────────────────────────────────────────────

if [ -z "$PA_BASE" ]; then
  echo "==> Predictive-agent: SKIP (PredictiveAgentApiUrl not found in cms-${STAGE}-predictive-agent — stack may not be deployed)"
else
  echo "==> Predictive-agent (${PA_BASE})"
  probe "/vehicles/{vehicle_id}/analysis POST"  POST "${PA_BASE}/vehicles/test-vehicle/analysis"
  probe "/vehicles/{vehicle_id}/analysis GET"   GET  "${PA_BASE}/vehicles/test-vehicle/analysis"
  probe "/fleet/analysis"                       POST "${PA_BASE}/fleet/analysis"
  probe "/maintenance/schedule POST"            POST "${PA_BASE}/maintenance/schedule"
  probe "/maintenance/schedule GET"             GET  "${PA_BASE}/maintenance/schedule"
fi

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
if [ "${#FAILED[@]}" -eq 0 ]; then
  echo "PASS — all probes returned non-401/403."
  exit 0
else
  echo "FAIL — ${#FAILED[@]} route(s) returned 401/403 (auth rejected):"
  for f in "${FAILED[@]}"; do
    echo "  - $f"
  done
  exit 1
fi
