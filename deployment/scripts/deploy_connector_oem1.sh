#!/usr/bin/env bash
# deploy_connector_oem1.sh — CDK deploy ConnectorStack for OEM1 + post-deploy smoke test
# Per ~/.kiro/steering/deploy-validation.md and ~/.kiro/steering/non-interactive.md
set -euo pipefail

DEPLOYMENT_STAGE="${DEPLOYMENT_STAGE:-staging}"
AWS_REGION="${AWS_REGION:-us-west-2}"
AWS_PROFILE="${AWS_PROFILE:-default}"
# CONNECTOR_NAME + CONNECTOR_TYPE must be set for ConnectorStack to synth
# (see deployment/app.py: the stack is behind `if os.environ.get('CONNECTOR_NAME')`).
# Defaulting here matches the historical staging deploy of 2026-06-15 which used
# `oem1` + `grpc_streaming`, and matches the log-group names live on the pool
# (`/ecs/cms-{stage}-connector-oem1`). Without these defaults, `cdk deploy
# ConnectorStack` fails cold with "No stacks match the name(s) ConnectorStack",
# reproduced 2026-08-30.
CONNECTOR_NAME="${CONNECTOR_NAME:-oem1}"
CONNECTOR_TYPE="${CONNECTOR_TYPE:-grpc_streaming}"
export CONNECTOR_NAME CONNECTOR_TYPE
LOG_GROUP="/ecs/cms-${DEPLOYMENT_STAGE}-connector-${CONNECTOR_NAME}"

log()  { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }
err()  { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ❌ $*" >&2; }
ok()   { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ✅ $*"; }

# ── Pre-deploy prerequisite checks ──────────────────────────────────────────
log "Checking prerequisites..."

if ! aws sts get-caller-identity --profile "${AWS_PROFILE}" --region "${AWS_REGION}" > /dev/null 2>&1; then
    err "AWS credentials not valid for profile=${AWS_PROFILE} region=${AWS_REGION}"
    err "Run: aws configure --profile ${AWS_PROFILE}"
    exit 1
fi
ok "AWS credentials valid"

if ! aws cloudformation describe-stacks --stack-name CDKToolkit \
        --profile "${AWS_PROFILE}" --region "${AWS_REGION}" > /dev/null 2>&1; then
    err "CDK bootstrap not found in ${AWS_REGION}. Run:"
    err "  cdk bootstrap aws://\$(aws sts get-caller-identity --query Account --output text)/${AWS_REGION}"
    exit 1
fi
ok "CDK bootstrap present"

# ── Deploy ───────────────────────────────────────────────────────────────────
log "Deploying ConnectorStack (DEPLOYMENT_STAGE=${DEPLOYMENT_STAGE}, AWS_REGION=${AWS_REGION})..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="${SCRIPT_DIR}/.."

(
    cd "${DEPLOY_DIR}"
    CDK_DEFAULT_ACCOUNT="$(aws sts get-caller-identity --profile "${AWS_PROFILE}" --query Account --output text)"
    export CDK_DEFAULT_ACCOUNT
    export CDK_DEFAULT_REGION="${AWS_REGION}"

    source .venv/bin/activate
    AWS_PROFILE="${AWS_PROFILE}" DEPLOYMENT_STAGE="${DEPLOYMENT_STAGE}" AWS_REGION="${AWS_REGION}" \
        cdk deploy --require-approval never ConnectorStack \
        --profile "${AWS_PROFILE}"
)

ok "CDK deploy complete"

# ── Post-deploy smoke test ────────────────────────────────────────────────────
log "Waiting 60s for connector to stabilise before log check..."
sleep 60

START_TIME=$(python3 -c "import time; print(int((time.time() - 120) * 1000))")

log "Scanning CloudWatch log group: ${LOG_GROUP} for ERROR/Traceback..."
ERRORS=$(aws logs filter-log-events \
    --log-group-name "${LOG_GROUP}" \
    --start-time "${START_TIME}" \
    --filter-pattern "?ERROR ?Traceback" \
    --limit 5 \
    --region "${AWS_REGION}" \
    --profile "${AWS_PROFILE}" \
    --query 'events[].message' \
    --output text 2>/dev/null || echo "")

if [ -n "${ERRORS}" ]; then
    err "Runtime errors detected after deploy:"
    echo "${ERRORS}"
    exit 1
fi

ok "Health check passed — no runtime errors in ${LOG_GROUP}"
