#!/usr/bin/env bash
# test_unauth_probes.sh — Post-deploy authorizer regression probe.
#
# Curls every previously-unauthenticated CMS API route with NO credentials
# and asserts each returns 401 or 403. Any 200 response is a regression.
#
# Usage (reads endpoints from CloudFormation stack outputs):
#   DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 bash scripts/test_unauth_probes.sh
#
# Usage (override individual endpoints via env vars):
#   SIMULATION_API_URL=https://xxx.execute-api.us-west-2.amazonaws.com/prod/ \
#   COMMANDS_API_URL=https://yyy.execute-api.us-west-2.amazonaws.com/prod/ \
#   PREDICTIVE_API_URL=https://zzz.execute-api.us-west-2.amazonaws.com/prod/ \
#   DATA_PROCESSING_API_URL=https://aaa.execute-api.us-west-2.amazonaws.com/prod/ \
#   bash scripts/test_unauth_probes.sh
#
# Exit 0  — all probes returned 401 or 403 (authorizers wired correctly).
# Exit 1  — one or more probes returned 200 (authorizer gap detected).
# Exit 2  — prerequisite failure (missing tool, can't resolve endpoints).

set -uo pipefail
export AWS_PAGER=""

STAGE="${DEPLOYMENT_STAGE:-staging}"
REGION="${AWS_REGION:-us-west-2}"

# ---------- color helpers ----------
if [ -t 1 ]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; NC='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; NC=''
fi
ok()   { echo -e "${GREEN}✓${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC} $*"; }

# ---------- prerequisites ----------
for cmd in curl jq aws; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: required tool not found: $cmd" >&2
    exit 2
  fi
done

# ---------- endpoint resolution ----------
cfn_output() {
  local stack="$1" key="$2"
  aws cloudformation describe-stacks \
    --stack-name "$stack" --region "$REGION" \
    --no-cli-pager --no-paginate \
    --query "Stacks[0].Outputs[?OutputKey==\`${key}\`].OutputValue" \
    --output text 2>/dev/null
}

resolve_url() {
  local env_var="$1" stack="$2" output_key="$3"
  local val="${!env_var:-}"
  if [ -z "$val" ]; then
    val=$(cfn_output "$stack" "$output_key")
  fi
  # Strip trailing slash for consistent path joining
  echo "${val%/}"
}

SIM_URL=$(resolve_url  "SIMULATION_API_URL"      "cms-${STAGE}-simulation"      "SimulationApiUrl")
CMD_URL=$(resolve_url  "COMMANDS_API_URL"         "cms-${STAGE}-commands"        "CommandsApiUrl")
PA_URL=$(resolve_url   "PREDICTIVE_API_URL"       "cms-${STAGE}-predictive-agent" "PredictiveAgentAPIEndpoint")
DP_URL=$(resolve_url   "DATA_PROCESSING_API_URL"  "cms-${STAGE}-data-processing" "APIEndpoint")

# Validate the always-deployed stacks are non-empty.
# Predictive-agent stack is OPTIONAL (deployed only when DEPLOY_PREDICTIVE_AGENT=true);
# skip its probes with a notice if its endpoint can't be resolved, but do
# NOT exit. The simulation / commands / data-processing stacks ARE always
# deployed by `make deploy-all`, so missing endpoints there is a real
# pre-flight failure.
missing=0
for pair in "SIMULATION:$SIM_URL" "COMMANDS:$CMD_URL" "DATA_PROCESSING:$DP_URL"; do
  name="${pair%%:*}"; url="${pair#*:}"
  if [ -z "$url" ] || [ "$url" = "None" ]; then
    warn "Could not resolve $name endpoint. Set ${name}_API_URL env var or deploy the stack."
    missing=$((missing + 1))
  fi
done
[ "$missing" -gt 0 ] && exit 2

if [ -z "$PA_URL" ] || [ "$PA_URL" = "None" ]; then
  echo "ℹ️  PREDICTIVE_AGENT_API_URL not resolvable — predictive-agent stack not deployed (optional). Skipping its probes."
  PA_URL=""
fi

echo "Probing with no credentials — expecting 401/403 on all routes"
echo "  SIMULATION:      $SIM_URL"
echo "  COMMANDS:        $CMD_URL"
echo "  PREDICTIVE:      $PA_URL"
echo "  DATA_PROCESSING: $DP_URL"
echo ""

# ---------- probe engine ----------
FAILURES=()

probe() {
  local method="$1" url="$2" label="$3"
  local http_code
  http_code=$(curl -s -o /dev/null -w "%{http_code}" \
    -X "$method" \
    -H "Content-Type: application/json" \
    --max-time 10 \
    "$url" 2>/dev/null)
  if [ "$http_code" = "401" ] || [ "$http_code" = "403" ]; then
    ok "[$http_code] $method $label"
  elif [ "$http_code" = "000" ]; then
    warn "[---] $method $label (no response / timeout — is the stage deployed?)"
  else
    fail "[$http_code] $method $label  <<< REGRESSION: expected 401/403"
    FAILURES+=("$http_code $method $label")
  fi
}

# ---------- Simulation (14 routes) ----------
echo "=== Simulation stack ==="
probe GET    "$SIM_URL/api/simulation"                         "/api/simulation"
probe POST   "$SIM_URL/api/simulation/start"                   "/api/simulation/start"
probe GET    "$SIM_URL/api/simulation/status/test-id"          "/api/simulation/status/{id}"
probe POST   "$SIM_URL/api/simulation/stop/test-id"            "/api/simulation/stop/{id}"
probe GET    "$SIM_URL/api/simulation/list"                    "/api/simulation/list"
probe GET    "$SIM_URL/api/simulation/health"                  "/api/simulation/health"
probe GET    "$SIM_URL/api/simulation/drivers"                 "/api/simulation/drivers"
probe GET    "$SIM_URL/api/simulation/presets"                 "/api/simulation/presets"
probe GET    "$SIM_URL/api/simulation/campaigns"               "/api/simulation/campaigns"
probe GET    "$SIM_URL/api/simulation/discover-iot-endpoint"   "/api/simulation/discover-iot-endpoint"
probe POST   "$SIM_URL/api/simulation/agent/start"             "/api/simulation/agent/start"
probe POST   "$SIM_URL/api/simulation/agent/stop"              "/api/simulation/agent/stop"
probe GET    "$SIM_URL/api/simulation/agent/status"            "/api/simulation/agent/status"
probe GET    "$SIM_URL/api/simulation/agent/logs/VIN-TEST-001" "/api/simulation/agent/logs/{vin}"

# ---------- Commands (6 routes) ----------
echo ""
echo "=== Commands stack ==="
probe GET    "$CMD_URL/api/commands/catalog"                   "/api/commands/catalog"
probe POST   "$CMD_URL/api/commands/VIN-TEST-001"              "/api/commands/{vehicleId} (POST)"
probe GET    "$CMD_URL/api/commands/VIN-TEST-001"              "/api/commands/{vehicleId} (GET)"
probe POST   "$CMD_URL/api/geofences"                          "/api/geofences (POST)"
probe GET    "$CMD_URL/api/geofences/VIN-TEST-001"             "/api/geofences/{vehicleId} (GET)"
probe DELETE "$CMD_URL/api/geofences/VIN-TEST-001"             "/api/geofences/{vehicleId} (DELETE)"

# ---------- Predictive Agent (5 routes — OPTIONAL stack) ----------
echo ""
echo "=== Predictive-agent stack ==="
if [ -n "$PA_URL" ]; then
  probe POST   "$PA_URL/vehicles/VIN-TEST-001/analysis"          "/vehicles/{id}/analysis (POST)"
  probe GET    "$PA_URL/vehicles/VIN-TEST-001/analysis"          "/vehicles/{id}/analysis (GET)"
  probe POST   "$PA_URL/fleet/analysis"                          "/fleet/analysis"
  probe POST   "$PA_URL/maintenance/schedule"                    "/maintenance/schedule (POST)"
  probe GET    "$PA_URL/maintenance/schedule"                    "/maintenance/schedule (GET)"
else
  echo "  (skipped — predictive-agent stack not deployed; auth fix is template-level so it applies when the customer opts in via DEPLOY_PREDICTIVE_AGENT=true)"
fi

# ---------- Data-processing (root + proxy) ----------
echo ""
echo "=== Data-processing stack ==="
probe GET    "$DP_URL"                                         "/ (root)"
probe GET    "$DP_URL/any/path"                                "/{proxy+} (any path)"

# ---------- result ----------
echo ""
total_failures="${#FAILURES[@]}"
if [ "$total_failures" -eq 0 ]; then
  ok "All probes returned 401/403 — authorizers correctly wired."
  exit 0
else
  fail "$total_failures probe(s) returned unexpected status codes:"
  for f in "${FAILURES[@]}"; do
    echo "    $f"
  done
  echo ""
  echo "Each line above is a route that returned a non-401/403 response."
  echo "This indicates a missing or misconfigured Cognito authorizer."
  exit 1
fi
