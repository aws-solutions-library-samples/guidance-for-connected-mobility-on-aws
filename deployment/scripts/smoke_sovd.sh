#!/usr/bin/env bash
#
# smoke_sovd.sh — Post-deploy smoke test for the CMS SOVD (Remote Diagnostics) feature.
#
# Usage:
#   smoke_sovd.sh <stage>
#
#   <stage>  staging | prod
#
# Exit codes:
#   0  — all checks passed
#   1  — argument / pre-flight failure
#   2  — Lambda freshness check failed (stale deploy)
#   3  — sidecar IAM grant missing
#   4  — Lambda invoke or commandId extraction failed
#   5  — DDB status did not reach SUCCEEDED within 45 s
#
# Non-interactive: no confirmation prompts, no `read -p`. Assumes AWS
# credentials + region are in the environment or ~/.aws/config.
# Per ~/.kiro/steering/non-interactive.md.
#
# Idempotent: each run generates a fresh correlation_id. Cleanup is on EXIT
# trap and is safe to run even if the test row never existed.
#
set -euo pipefail
export AWS_PAGER=""

# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  cat <<'EOF'
Usage: smoke_sovd.sh <stage>

  <stage>   staging or prod

The script:
  1. Checks the commands-api Lambda was deployed within the last 24 h.
  2. Checks the sidecar ECS task role has the SOVD s3:PutObject IAM grant.
  3. Fires a SOVD read_dtcs command on synthetic vehicle VEH-STAGING-SIM-001.
  4. Polls DynamoDB for SUCCEEDED status (up to 45 s, 3 s cadence).
  5. Cleans up the test row from commands + dtc-history tables on EXIT.

AWS credentials and region must be available via environment or ~/.aws/config.
EOF
  exit 0
fi

STAGE="${1:-}"

if [ -z "$STAGE" ]; then
  echo "ERROR: stage argument is required (staging or prod)" >&2
  echo "Usage: smoke_sovd.sh <stage>" >&2
  exit 1
fi

if [ "$STAGE" != "staging" ] && [ "$STAGE" != "prod" ]; then
  echo "ERROR: unrecognized stage '${STAGE}' — must be 'staging' or 'prod'" >&2
  echo "Usage: smoke_sovd.sh <stage>" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Prod guard — script-level protection regardless of invocation path.
# The Makefile's smoke-sovd-prod target also checks this, but operators may
# invoke the script directly (bypassing the Makefile).  Both guards are
# needed: Makefile guard provides the user-friendly error before incurring
# any AWS API calls; script guard closes the bypass path.
# FG4.2 / Cycle 8 Warning remediation.
# ---------------------------------------------------------------------------
if [ "$STAGE" = "prod" ] && [ "${ALLOW_PROD:-0}" != "1" ]; then
  echo "ERROR: Running smoke_sovd.sh against prod requires ALLOW_PROD=1." >&2
  echo "  Direct:   ALLOW_PROD=1 bash deployment/scripts/smoke_sovd.sh prod" >&2
  echo "  Makefile: ALLOW_PROD=1 make smoke-sovd-prod" >&2
  exit 1
fi

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

err()  { echo "${_RED}✗ [smoke-sovd]${_NC} $*" >&2; }
ok()   { echo "${_GREEN}✓ [smoke-sovd]${_NC} $*"; }
log()  { echo "${_BLUE}  [smoke-sovd]${_NC} $*"; }
warn() { echo "${_YELLOW}⚠ [smoke-sovd]${_NC} $*"; }

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REGION="${AWS_REGION:-us-west-2}"
LAMBDA_FUNCTION="cms-${STAGE}-commands-api"
COMMANDS_TABLE="cms-${STAGE}-storage-commands"
DTC_HISTORY_TABLE="cms-${STAGE}-storage-dtc-history"
VEHICLES_TABLE="cms-${STAGE}-storage-vehicles"

# Target vehicle resolution priority:
#   1. $SOVD_SMOKE_VEHICLE_ID env override (operator-supplied)
#   2. Auto-discover a vehicle with connectionStatus='connected' from
#      cms-${STAGE}-storage-vehicles. Fail-fast with a clear message if none
#      is found — an SOVD read against a disconnected vehicle publishes to
#      MQTT but the sidecar never answers, and the DDB row never transitions
#      to SUCCEEDED (misleading timeout at Step 4).
#
# Historical note: v1 of this script hardcoded a synthetic vehicleId
# ("VEH-STAGING-SIM-001") that never existed on any staging seed. Fixed
# 2026-09-02 during first live smoke run — see spec dir Fix Group 5.
TEST_VEHICLE_ID="${SOVD_SMOKE_VEHICLE_ID:-}"

# Per-run unique IDs (fresh each invocation → idempotent repeated runs).
CORRELATION_ID=$(python3 -c "import uuid; print(uuid.uuid4().hex)")
COMMAND_ID=""

# ---------------------------------------------------------------------------
# Cleanup on EXIT — idempotent (safe if rows never existed)
# ---------------------------------------------------------------------------

cleanup() {
  local exit_code=$?
  if [ -n "$COMMAND_ID" ]; then
    log "Cleanup: deleting test row commandId=${COMMAND_ID} from ${COMMANDS_TABLE}…"
    aws dynamodb delete-item \
      --table-name "$COMMANDS_TABLE" \
      --key "{\"commandId\":{\"S\":\"${COMMAND_ID}\"}}" \
      --region "$REGION" \
      --no-cli-pager \
      2>/dev/null || true

    log "Cleanup: scanning for dtc-history rows with correlationId=${CORRELATION_ID}…"
    # Use a FilterExpression scan to find any dtc-history rows this run created.
    # Idempotent: delete-item on a non-existent key is a no-op.
    DTC_KEYS=$(python3 - <<PYEOF
import boto3, json
ddb = boto3.client('dynamodb', region_name='${REGION}')
paginator = ddb.get_paginator('scan')
pages = paginator.paginate(
    TableName='${DTC_HISTORY_TABLE}',
    FilterExpression='correlationId = :cid',
    ExpressionAttributeValues={':cid': {'S': '${CORRELATION_ID}'}},
    ProjectionExpression='vehicleId, dtcCode',
)
keys = []
for page in pages:
    for item in page.get('Items', []):
        keys.append(item)
print(json.dumps(keys))
PYEOF
)
    # Delete each matching row.
    echo "$DTC_KEYS" | python3 - <<PYEOF 2>/dev/null || true
import boto3, json, sys
ddb = boto3.client('dynamodb', region_name='${REGION}')
keys = json.load(sys.stdin)
for key in keys:
    try:
        ddb.delete_item(TableName='${DTC_HISTORY_TABLE}', Key=key)
        print(f"  deleted dtc-history row: {key}")
    except Exception as e:
        print(f"  warning: delete_item failed for {key}: {e}", file=sys.stderr)
PYEOF
  fi
  exit $exit_code
}

trap cleanup EXIT

# ---------------------------------------------------------------------------
# Step (a): Check Lambda LastModified is within 24 hours
# ---------------------------------------------------------------------------

log "Step 1/5: Checking Lambda '${LAMBDA_FUNCTION}' LastModified is within 24 h…"

LAST_MODIFIED=$(aws lambda get-function \
  --function-name "$LAMBDA_FUNCTION" \
  --query 'Configuration.LastModified' \
  --output text \
  --region "$REGION" \
  --no-cli-pager \
  2>&1) || {
  err "Failed to describe Lambda '${LAMBDA_FUNCTION}'. Check credentials and function name."
  err "AWS error: ${LAST_MODIFIED}"
  exit 2
}

# Use python3 for the date math — avoids GNU/BSD date incompatibilities.
FRESHNESS_OK=$(python3 - "$LAST_MODIFIED" <<'PYEOF'
import sys
from datetime import datetime, timezone, timedelta

last_modified_str = sys.argv[1].strip()
# AWS returns ISO-8601 with timezone info, e.g. "2026-09-01T10:00:00.000+0000"
# Handle both Z and +0000 suffixes.
last_modified_str = last_modified_str.replace('+0000', '+00:00')
if last_modified_str.endswith('Z'):
    last_modified_str = last_modified_str[:-1] + '+00:00'
try:
    last_modified = datetime.fromisoformat(last_modified_str)
except ValueError:
    # Fallback for formats like "2026-09-01T10:00:00.000+0000"
    from email.utils import parsedate_to_datetime
    last_modified = parsedate_to_datetime(last_modified_str)

now = datetime.now(timezone.utc)
age = now - last_modified
print("OK" if age <= timedelta(hours=24) else f"STALE:{int(age.total_seconds()//3600)}h")
PYEOF
)

if [[ "$FRESHNESS_OK" == STALE:* ]]; then
  STALE_HOURS="${FRESHNESS_OK#STALE:}"
  err "Lambda '${LAMBDA_FUNCTION}' LastModified is ${STALE_HOURS} hours old — deploy first:"
  err "  make deploy-commands DEPLOYMENT_STAGE=${STAGE}"
  exit 2
fi

ok "Lambda '${LAMBDA_FUNCTION}' is fresh (LastModified=${LAST_MODIFIED})"

# ---------------------------------------------------------------------------
# Step (b): Check sidecar ECS task role has SOVD IAM grant (s3:PutObject)
# ---------------------------------------------------------------------------

log "Step 2/5: Checking sidecar IAM role has SOVD s3:PutObject grant…"

# Discover the sidecar task role by listing IAM roles matching the simulation stack pattern.
SIDECAR_ROLE=$(aws iam list-roles \
  --no-cli-pager \
  --query "Roles[?contains(RoleName, \`cms-${STAGE}-simulation\`)].RoleName" \
  --output text \
  2>&1) || {
  err "Failed to list IAM roles. Check credentials."
  exit 3
}

if [ -z "$SIDECAR_ROLE" ]; then
  warn "No IAM role found matching 'cms-${STAGE}-simulation' — sidecar may not be deployed."
  warn "Skipping sidecar IAM check. Deploy the simulation stack first if needed."
else
  # Check each matching role for an s3:PutObject grant covering sovd-responses.
  GRANT_FOUND=0
  for ROLE in $SIDECAR_ROLE; do
    # List all inline + attached policies on the role, then check for s3:PutObject.
    INLINE_POLICIES=$(aws iam list-role-policies \
      --role-name "$ROLE" \
      --output text \
      --no-cli-pager \
      --query 'PolicyNames' \
      2>/dev/null || echo "")

    for POLICY in $INLINE_POLICIES; do
      POLICY_DOC=$(aws iam get-role-policy \
        --role-name "$ROLE" \
        --policy-name "$POLICY" \
        --query 'PolicyDocument' \
        --output text \
        --no-cli-pager \
        2>/dev/null || echo "")
      if echo "$POLICY_DOC" | grep -q "s3:PutObject"; then
        GRANT_FOUND=1
        ok "Sidecar role '${ROLE}' has s3:PutObject grant (policy: ${POLICY})"
        break 2
      fi
    done
  done

  if [ "$GRANT_FOUND" -eq 0 ]; then
    err "No s3:PutObject grant found on any IAM role matching 'cms-${STAGE}-simulation'"
    err "This means the sidecar cannot upload large SOVD responses to S3."
    err "Fix: deploy the commands stack with SOVD support:"
    err "  make deploy-commands DEPLOYMENT_STAGE=${STAGE}"
    err "  (This wires the sidecar task role grant via Task 5.2 in spec 2026-09-01-cms-remote-diagnostics-sovd)"
    exit 3
  fi
fi

# ---------------------------------------------------------------------------
# Step (c): Resolve target vehicle + verify connectionStatus='connected'
# ---------------------------------------------------------------------------
#
# The smoke test's SUCCEEDED-within-15s assertion is END-TO-END: cloud publish
# → sidecar SOVD subscribe → UDS response → sidecar re-publish → response
# handler → DDB status update. Every hop requires the sidecar to be alive and
# subscribed. A run against a disconnected vehicle publishes to a topic no
# one has SUBACK'd, and Step 4 will time out at 15s — hiding the true root
# cause (sidecar not connected).
#
# Precondition check: resolve TEST_VEHICLE_ID to an actual connectionStatus.
# If SOVD_SMOKE_VEHICLE_ID env var is set, use it and validate. Otherwise,
# auto-discover the first vehicle where connectionStatus='connected'. Fail
# with an actionable error if none found — the operator should click
# "Start Agent" on any vehicle in the UI first, or supply
# SOVD_SMOKE_VEHICLE_ID directly.

log "Step 3/5: Resolving target vehicle + verifying connectionStatus='connected'…"

if [ -n "$TEST_VEHICLE_ID" ]; then
  log "  Using SOVD_SMOKE_VEHICLE_ID='${TEST_VEHICLE_ID}' (operator-supplied)"
  RESOLVED_STATUS=$(aws dynamodb get-item \
    --table-name "$VEHICLES_TABLE" \
    --key "{\"vehicleId\":{\"S\":\"${TEST_VEHICLE_ID}\"}}" \
    --projection-expression "connectionStatus" \
    --region "$REGION" \
    --no-cli-pager \
    --output text \
    --query 'Item.connectionStatus.S' \
    2>/dev/null || echo "MISSING")
  if [ "$RESOLVED_STATUS" = "None" ] || [ "$RESOLVED_STATUS" = "MISSING" ]; then
    err "Vehicle '${TEST_VEHICLE_ID}' does not exist in ${VEHICLES_TABLE}"
    err "Set SOVD_SMOKE_VEHICLE_ID to a real staging vehicleId, or unset it to auto-discover."
    exit 6
  fi
  if [ "$RESOLVED_STATUS" != "connected" ]; then
    err "Vehicle '${TEST_VEHICLE_ID}' is not connected (connectionStatus=${RESOLVED_STATUS})"
    err "SOVD end-to-end requires a live sidecar. Click 'Start Agent' on this vehicle in the UI,"
    err "wait for it to connect, then re-run this smoke test."
    exit 6
  fi
else
  log "  Auto-discovering a connected vehicle (SOVD_SMOKE_VEHICLE_ID unset)…"
  TEST_VEHICLE_ID=$(aws dynamodb scan \
    --table-name "$VEHICLES_TABLE" \
    --filter-expression "connectionStatus = :c" \
    --expression-attribute-values '{":c":{"S":"connected"}}' \
    --projection-expression "vehicleId" \
    --limit 1 \
    --region "$REGION" \
    --no-cli-pager \
    --output text \
    --query 'Items[0].vehicleId.S' \
    2>/dev/null || echo "")
  if [ -z "$TEST_VEHICLE_ID" ] || [ "$TEST_VEHICLE_ID" = "None" ]; then
    err "No vehicles with connectionStatus='connected' found in ${VEHICLES_TABLE}"
    err "Click 'Start Agent' on any vehicle in the UI, wait for it to connect,"
    err "then re-run this smoke test. Or supply a specific vehicle:"
    err "  SOVD_SMOKE_VEHICLE_ID=<vehicleId> make smoke-sovd-${STAGE}"
    exit 6
  fi
fi

ok "Target vehicle: ${TEST_VEHICLE_ID} (connectionStatus=connected)"

# ---------------------------------------------------------------------------
# Step (d): Fire SOVD read_dtcs via Lambda invoke
# ---------------------------------------------------------------------------

log "Step 4/5: Firing SOVD read_dtcs command on vehicle '${TEST_VEHICLE_ID}'…"

INVOKE_TMPFILE=$(mktemp /tmp/smoke_sovd_invoke_XXXXXX.json)
# Ensure temp file is cleaned up even on unexpected exits.
trap 'rm -f "$INVOKE_TMPFILE"; cleanup' EXIT

# Build the JSON payload for the Lambda invocation.
# The Lambda handles POST /api/commands/{vehicleId}, so we encode the
# API Gateway event shape (simplified — the Lambda's handler reads body + pathParameters).
PAYLOAD=$(python3 - "$TEST_VEHICLE_ID" "$CORRELATION_ID" <<'PYEOF'
import json, sys
vehicle_id, correlation_id = sys.argv[1], sys.argv[2]
event = {
    "httpMethod": "POST",
    "path": f"/api/commands/{vehicle_id}",
    "pathParameters": {"vehicleId": vehicle_id},
    "headers": {
        "Content-Type": "application/json",
        "x-smoke-test": "smoke_sovd",
    },
    # Synthetic platform-admin claims so _extract_caller / _authorize_per_vin
    # accept the request without a real Cognito token. Mirrors the shape used
    # by services/commands/tests/test_sovd_e2e.py::_make_api_gw_event. No real
    # user credentials embedded — the smoke test runs under the operator's IAM
    # role invoking the Lambda directly, and the Lambda enforces authz on the
    # claims payload it receives.
    "requestContext": {
        "authorizer": {
            "claims": {
                "cognito:groups": "platform-admin",
                "custom:fleetIds": "",
                "email": "smoke-sovd@example.com",
                "sub": "smoke-sovd-test-sub-000",
            }
        }
    },
    "body": json.dumps({
        "command_type": "read_dtcs",
        "components": ["*"],
        "include_freeze_frame": True,
        "correlation_id": correlation_id,
    }),
}
print(json.dumps(event))
PYEOF
)

# aws lambda invoke writes the response to a file; status code in the return.
INVOKE_STATUS=$(aws lambda invoke \
  --function-name "$LAMBDA_FUNCTION" \
  --cli-binary-format raw-in-base64-out \
  --payload "$PAYLOAD" \
  --region "$REGION" \
  --no-cli-pager \
  --query 'StatusCode' \
  --output text \
  "$INVOKE_TMPFILE" 2>&1) || {
  err "aws lambda invoke failed: ${INVOKE_STATUS}"
  exit 4
}

if [ "$INVOKE_STATUS" != "200" ]; then
  err "Lambda invoke returned HTTP ${INVOKE_STATUS} (expected 200)"
  err "Response body:"
  cat "$INVOKE_TMPFILE" >&2 || true
  exit 4
fi

# Extract commandId from the response body.
COMMAND_ID=$(python3 - "$INVOKE_TMPFILE" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    raw = f.read().strip()
# The Lambda may return a JSON-encoded body string (API Gateway proxy integration).
try:
    outer = json.loads(raw)
    # API Gateway proxy: outer has statusCode + body (string).
    if isinstance(outer, dict) and 'body' in outer:
        inner = json.loads(outer['body'])
    else:
        inner = outer
except (json.JSONDecodeError, TypeError):
    inner = {}
cid = inner.get('commandId') or inner.get('command_id') or inner.get('correlationId') or ''
print(cid.strip())
PYEOF
)

rm -f "$INVOKE_TMPFILE"
trap 'cleanup' EXIT  # restore simplified trap after temp file removed

if [ -z "$COMMAND_ID" ]; then
  err "Could not extract commandId from Lambda response."
  err "Response body: $(cat "$INVOKE_TMPFILE" 2>/dev/null || echo '(already deleted)')"
  err "Ensure the commands Lambda is deployed with SOVD support."
  exit 4
fi

ok "Lambda invoked — commandId=${COMMAND_ID}"

# ---------------------------------------------------------------------------
# Step (d): Poll DynamoDB for SUCCEEDED within 15 s
# ---------------------------------------------------------------------------

log "Step 5/5: Polling DynamoDB '${COMMANDS_TABLE}' for commandId=${COMMAND_ID} → SUCCEEDED (timeout 45 s)…"

POLL_DEADLINE=$(python3 -c "import time; print(int(time.time()) + 45)")
STATUS=""
ELAPSED=0

while true; do
  NOW=$(python3 -c "import time; print(int(time.time()))")
  if [ "$NOW" -ge "$POLL_DEADLINE" ]; then
    err "Timeout: commandId=${COMMAND_ID} did not reach SUCCEEDED within 45 s."
    err "Last observed status: '${STATUS:-<not found>}'"
    err "Debug: check CloudWatch logs for cms-${STAGE}-commands-api and cms-${STAGE}-command-response-handler"
    exit 5
  fi

  STATUS=$(aws dynamodb get-item \
    --table-name "$COMMANDS_TABLE" \
    --key "{\"commandId\":{\"S\":\"${COMMAND_ID}\"}}" \
    --query 'Item.status.S' \
    --output text \
    --region "$REGION" \
    --no-cli-pager \
    2>/dev/null || echo "")

  # DynamoDB returns "None" (literal string) when the attribute exists but is null,
  # and empty string / "null" when the item is not found yet.
  if [ "$STATUS" = "SUCCEEDED" ]; then
    ELAPSED=$(python3 -c "import time; print(int(time.time()) - ($POLL_DEADLINE - 15))")
    ok "commandId=${COMMAND_ID} → SUCCEEDED (elapsed ~${ELAPSED}s)"
    break
  fi

  if [ "$STATUS" = "FAILED" ]; then
    err "commandId=${COMMAND_ID} → FAILED"
    err "The sidecar returned an error. Check CloudWatch logs for cms-${STAGE}-command-response-handler."
    exit 5
  fi

  log "  status='${STATUS:-<pending>}', retrying in 3 s…"
  sleep 3
done

ok "Smoke test PASSED — SOVD read_dtcs end-to-end verified on stage='${STAGE}'"
exit 0
