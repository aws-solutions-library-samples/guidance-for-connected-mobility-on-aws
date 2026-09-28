#!/usr/bin/env bash
# validate-bedrock-model.sh — Pre-deploy guardrail for Bedrock model IDs
#
# Validates that ${MODEL_ID} (env) resolves to either:
#   1. An ACTIVE Bedrock inference profile in ${REGION}, OR
#   2. An ACTIVE Bedrock foundation model in ${REGION} (LEGACY → WARN, not FAIL)
#
# Used by `make staging-deploy` and `make prod-deploy` as a prerequisite to
# catch hallucinated, typo'd, or LEGACY model IDs BEFORE CloudFormation/CDK
# touches the agent infrastructure.
#
# Exit codes:
#   0 — OK (inference profile ACTIVE, foundation model ACTIVE, or LEGACY warning)
#   1 — FAIL (model ID not found in either catalog, or inference profile not ACTIVE)
#   2 — usage error (missing MODEL_ID, AWS CLI missing, etc.)
#
# Inputs (env vars):
#   MODEL_ID  Required. e.g. "us.anthropic.claude-sonnet-4-6" or
#             "anthropic.claude-sonnet-4-20250514-v1:0"
#   REGION    Optional. Defaults to us-east-1.
#   PROFILE   Optional. Defaults to "default".
#
# Source of truth on WARN-vs-FAIL semantics: spec
# .kiro/specs/2026-06-01-cms-prod-model-id-fix/spec.md (user-approved Q2 default
# 2026-06-01 = WARN on LEGACY, not FAIL).

set -euo pipefail

REGION="${REGION:-us-east-1}"
PROFILE="${PROFILE:-default}"

usage() {
  cat <<'EOF'
Usage: validate-bedrock-model.sh

Pre-deploy guardrail for AWS Bedrock model IDs. Validates that MODEL_ID
resolves to an ACTIVE inference profile or foundation model in the target
region. WARNs (does not fail) on LEGACY foundation models.

Required env vars:
  MODEL_ID           e.g. us.anthropic.claude-sonnet-4-6

Optional env vars:
  REGION             AWS region (default: us-east-1)
  PROFILE            AWS profile (default: default)

Examples:
  MODEL_ID=us.anthropic.claude-sonnet-4-6 REGION=us-east-1 \
    deployment/scripts/validate-bedrock-model.sh

  # As a Makefile prerequisite:
  staging-deploy: validate-bedrock-model
          cdk deploy ...

Exit codes:
  0  OK or WARN-LEGACY
  1  FAIL (model not found / not ACTIVE)
  2  Usage error
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if [[ -z "${MODEL_ID:-}" ]]; then
  echo "[FAIL] MODEL_ID env var is required" >&2
  usage >&2
  exit 2
fi

if ! command -v aws >/dev/null 2>&1; then
  echo "[FAIL] aws CLI not found on PATH" >&2
  exit 2
fi

# --- Step 1: try inference-profile catalog -----------------------------------
# Inference profiles are the cross-region routing facade and are the canonical
# resource type for `us.*` and `eu.*` prefixed model IDs.
ip_status="$(
  aws bedrock list-inference-profiles \
    --region "$REGION" \
    --profile "$PROFILE" \
    --no-cli-pager \
    --no-paginate \
    --query "inferenceProfileSummaries[?inferenceProfileId=='$MODEL_ID'].status" \
    --output text 2>/dev/null || true
)"

if [[ -n "$ip_status" && "$ip_status" != "None" ]]; then
  if [[ "$ip_status" == "ACTIVE" ]]; then
    echo "[OK] $MODEL_ID is ACTIVE inference profile in $REGION"
    exit 0
  fi
  echo "[FAIL] $MODEL_ID inference profile exists in $REGION but status=$ip_status" >&2
  exit 1
fi

# --- Step 2: fall back to foundation-model catalog ---------------------------
# Foundation models are direct model invocations (no cross-region facade).
# `modelLifecycle.status` is ACTIVE or LEGACY.
fm_lifecycle="$(
  aws bedrock list-foundation-models \
    --region "$REGION" \
    --profile "$PROFILE" \
    --no-cli-pager \
    --no-paginate \
    --query "modelSummaries[?modelId=='$MODEL_ID'].modelLifecycle.status" \
    --output text 2>/dev/null || true
)"

if [[ -z "$fm_lifecycle" || "$fm_lifecycle" == "None" ]]; then
  echo "[FAIL] $MODEL_ID not found in inference-profile or foundation-model catalog in $REGION" >&2
  exit 1
fi

case "$fm_lifecycle" in
  ACTIVE)
    echo "[OK] $MODEL_ID is ACTIVE foundation model in $REGION"
    exit 0
    ;;
  LEGACY)
    echo "[WARN] $MODEL_ID resolves to LEGACY foundation model in $REGION — consider upgrading to a current-generation model"
    exit 0
    ;;
  *)
    echo "[FAIL] $MODEL_ID foundation model in $REGION has unexpected lifecycle: $fm_lifecycle" >&2
    exit 1
    ;;
esac
