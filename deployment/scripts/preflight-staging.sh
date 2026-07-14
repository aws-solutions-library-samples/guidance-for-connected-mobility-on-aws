#!/usr/bin/env bash
#
# Pre-flight checks for CMS staging deploy (us-west-2).
# Runs idempotently, makes only read-only AWS calls.
# Exit 0 = staging deploy is safe to start. Non-zero = a blocker found; FIX and re-run.

set -euo pipefail
export AWS_PAGER=""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${BLUE}[preflight] $*${NC}"; }
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC} $*"; }
err()  { echo -e "${RED}✗${NC} $*" >&2; }

# Expected staging account ID. Override with EXPECTED_ACCOUNT env var to
# adapt for your own deployment. The default is a placeholder; set this
# to your actual staging account in your local environment or via
# deployment/config/staging.env.
EXPECTED_ACCOUNT="${EXPECTED_ACCOUNT:-123456789012}"
STAGING_REGION="${STAGING_REGION:-us-west-2}"

# Resolve repo root once so all checks can use it (Check 4 reads Makefile, Check 7 reads staging.env, Check 10 cd's into deployment/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

failures=0

# 1. AWS account check
log "Check 1: AWS account is the staging target ($EXPECTED_ACCOUNT)"
actual_acct=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo 'UNKNOWN')
if [ "$actual_acct" = "$EXPECTED_ACCOUNT" ]; then
  ok "  account = $actual_acct"
else
  err "  expected $EXPECTED_ACCOUNT, got $actual_acct. Switch your AWS_PROFILE or aws-vault."
  failures=$((failures+1))
fi

# 2. CDK bootstrap check
log "Check 2: CDK Toolkit (bootstrap) exists in $STAGING_REGION"
if aws cloudformation describe-stacks --stack-name CDKToolkit --region "$STAGING_REGION" >/dev/null 2>&1; then
  ok "  CDKToolkit stack present"
else
  warn "  CDKToolkit stack NOT FOUND in $STAGING_REGION"
  warn "  Run: cd deployment && AWS_REGION=$STAGING_REGION cdk bootstrap aws://$actual_acct/$STAGING_REGION"
  warn "  OR:  make bootstrap-staging"
  failures=$((failures+1))
fi

# 3. VPC quota (need ~3 VPCs total across stacks)
log "Check 3: VPC quota in $STAGING_REGION"
vpc_quota=$(aws service-quotas get-service-quota --service-code vpc --quota-code L-F678F1CE --region "$STAGING_REGION" --query 'Quota.Value' --output text 2>/dev/null || echo '0')
vpc_count=$(aws ec2 describe-vpcs --region "$STAGING_REGION" --query 'length(Vpcs)' --output text 2>/dev/null || echo '0')
available=$(awk -v q="$vpc_quota" -v c="$vpc_count" 'BEGIN{print int(q-c)}')
if [ "$available" -ge 3 ]; then
  ok "  VPC headroom = $available (quota=$vpc_quota, used=$vpc_count)"
else
  err "  VPC headroom = $available (need >=3). Quota: $vpc_quota; existing: $vpc_count"
  err "  Increase quota or delete unused VPCs in $STAGING_REGION."
  failures=$((failures+1))
fi

# 4. Bedrock model access in us-west-2 (relevant for bedrock_agents_stack)
# Tests the current default model from the Makefile (BEDROCK_AGENT_MODEL).
# Note: AWS retired the manual Model Access page (May 2026); foundation models
# are now auto-enabled on first invoke. Test by actually invoking max_tokens=1.
log "Check 4: Bedrock available in $STAGING_REGION"
if aws bedrock list-foundation-models --region "$STAGING_REGION" --query 'modelSummaries[0].modelId' --output text >/dev/null 2>&1; then
  ok "  Bedrock list-foundation-models succeeded"
  # Read the current model from the Makefile (handles future bumps).
  bedrock_model=$(awk -F' ?= ' '/^BEDROCK_AGENT_MODEL \?=/{print $2}' "$REPO_ROOT/deployment/Makefile" 2>/dev/null | head -1)
  bedrock_model=${bedrock_model:-us.anthropic.claude-sonnet-4-6}
  # 4a. Catalog guardrail: validate the model ID resolves to ACTIVE (or LEGACY-warn)
  # before we attempt the live invoke. Catches hallucinated/typo'd IDs early.
  if MODEL_ID="$bedrock_model" REGION="$STAGING_REGION" PROFILE="${AWS_PROFILE:-default}" \
     "$REPO_ROOT/deployment/scripts/validate-bedrock-model.sh"; then
    : # validate-bedrock-model already printed [OK] / [WARN]
  else
    err "  $bedrock_model failed catalog validation in $STAGING_REGION (see [FAIL] above)"
    failures=$((failures+1))
  fi
  if aws bedrock-runtime invoke-model --region "$STAGING_REGION" \
     --model-id "$bedrock_model" \
     --cli-binary-format raw-in-base64-out \
     --body '{"anthropic_version":"bedrock-2023-05-31","max_tokens":1,"messages":[{"role":"user","content":"x"}]}' \
     --content-type 'application/json' /tmp/preflight-bedrock-out.json >/dev/null 2>&1; then
    ok "  $bedrock_model is invocable in $STAGING_REGION"
    rm -f /tmp/preflight-bedrock-out.json
  else
    err "  $bedrock_model is NOT invocable in $STAGING_REGION"
    err "  Run for full error: aws bedrock-runtime invoke-model --region $STAGING_REGION \\"
    err "    --model-id $bedrock_model --cli-binary-format raw-in-base64-out \\"
    err "    --body '{\"anthropic_version\":\"bedrock-2023-05-31\",\"max_tokens\":1,\"messages\":[{\"role\":\"user\",\"content\":\"x\"}]}' \\"
    err "    --content-type application/json /tmp/out.json"
    err "  If 'Legacy model' error: bump BEDROCK_AGENT_MODEL in deployment/Makefile to current Sonnet."
    err "  If 'AccessDenied': check IAM bedrock:InvokeModel permission on your AWS profile."
    failures=$((failures+1))
  fi
else
  err "  Bedrock service NOT reachable in $STAGING_REGION (or no IAM permission)"
  failures=$((failures+1))
fi

# 5. Container builder for CDK image-asset builds
#    Accepts docker (default) or finch/podman via CDK_DOCKER. AWS-supported drop-ins
#    per https://docs.aws.amazon.com/cdk/v2/guide/build-containers.html.
log "Check 5: Container builder (docker / finch / podman) is ready"
if [ -n "${CDK_DOCKER:-}" ]; then
  if command -v "$CDK_DOCKER" >/dev/null 2>&1 && "$CDK_DOCKER" info >/dev/null 2>&1; then
    ok "  $CDK_DOCKER info OK (CDK_DOCKER override)"
  else
    err "  CDK_DOCKER='$CDK_DOCKER' set but '$CDK_DOCKER info' failed."
    failures=$((failures+1))
  fi
elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  ok "  docker info OK (CDK default)"
elif command -v finch >/dev/null 2>&1 && finch info >/dev/null 2>&1; then
  err "  docker not available, but finch is ready. Re-run with: CDK_DOCKER=finch <cmd>"
  failures=$((failures+1))
elif command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1; then
  err "  docker not available, but podman is ready. Re-run with: CDK_DOCKER=podman <cmd>"
  err "  Also export DOCKER_HOST (see docs/DEPLOYMENT.md → 'Daemonless container builder')."
  failures=$((failures+1))
else
  err "  No container builder running. Start Docker Desktop, finch ('finch vm start'), or podman ('podman machine start')."
  failures=$((failures+1))
fi

# 6. Node.js + Python versions
log "Check 6: tooling versions"
if command -v node >/dev/null 2>&1; then
  node_ver=$(node --version)
  ok "  node = $node_ver"
  case "$node_ver" in
    v18.*|v20.*|v22.*) ;;
    *) warn "  node $node_ver is unusual; CDK 2.x prefers 18/20/22." ;;
  esac
else
  err "  node not found"
  failures=$((failures+1))
fi
if command -v python3 >/dev/null 2>&1; then
  py_ver=$(python3 --version)
  ok "  $py_ver"
else
  err "  python3 not found"
  failures=$((failures+1))
fi

# 7. Configuration files present
log "Check 7: deployment/config/staging.env present"
if [ -f "$REPO_ROOT/deployment/config/staging.env" ]; then
  ok "  staging.env present"
  if grep -q '^DEPLOY_FLEETWISE=true' "$REPO_ROOT/deployment/config/staging.env"; then
    ok "  DEPLOY_FLEETWISE=true (FWE telemetry stack will deploy)"
  else
    warn "  DEPLOY_FLEETWISE not set to true — FWE telemetry stack will be SKIPPED."
  fi
else
  err "  deployment/config/staging.env missing. Run Group 3a first."
  failures=$((failures+1))
fi

# 8. Demo password env var must be set (ui_stack requires it)
log "Check 8: CMS_DEMO_DEFAULT_PASSWORD env var set"
if [ -n "${CMS_DEMO_DEFAULT_PASSWORD:-}" ]; then
  ok "  CMS_DEMO_DEFAULT_PASSWORD is set (length: ${#CMS_DEMO_DEFAULT_PASSWORD})"
else
  err "  CMS_DEMO_DEFAULT_PASSWORD env var is NOT set. ui_stack will refuse to deploy."
  err "  Set it: export CMS_DEMO_DEFAULT_PASSWORD='YourSecretPassword123!'"
  failures=$((failures+1))
fi

# 9. Working tree clean
log "Check 9: git working tree is clean"
if [ -z "$(git -C "$REPO_ROOT" status --porcelain)" ]; then
  ok "  working tree clean"
else
  warn "  uncommitted changes:"
  git -C "$REPO_ROOT" status --short | head
  warn "  Consider committing first so any deploy issue is reproducible."
fi

# 10. cdk synth dry-run
log "Check 10: cdk synth (dry-run, no AWS calls)"
if (cd "$REPO_ROOT/deployment" && DEPLOY_FLEETWISE=true DEPLOYMENT_STAGE=staging \
   CMS_DEMO_DEFAULT_PASSWORD="${CMS_DEMO_DEFAULT_PASSWORD:-preflight-placeholder}" \
   CDK_DEFAULT_ACCOUNT="$actual_acct" CDK_DEFAULT_REGION="$STAGING_REGION" \
   npx cdk synth --quiet >/dev/null 2>&1); then
  ok "  cdk synth succeeded for staging"
else
  err "  cdk synth FAILED. Re-run without --quiet to see the error."
  failures=$((failures+1))
fi

echo
if [ "$failures" -eq 0 ]; then
  echo -e "${GREEN}All pre-flight checks passed.${NC} Safe to run: make staging-deploy"
  exit 0
else
  echo -e "${RED}$failures pre-flight check(s) failed.${NC} Fix before running staging-deploy."
  exit 1
fi
