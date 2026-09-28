#!/usr/bin/env bash
# load-federate-creds.sh — fetch Amazon Federate OIDC creds from Secrets Manager
# and export them so downstream `cdk synth` includes the AmazonFederateIdP resource.
#
# Intended usage: sourced (not executed) after config/<stage>.env is loaded
# with `set -a; . config/<stage>.env; set +a`, from staging-deploy/prod-deploy
# Makefile recipes. Requires AWS_REGION + AWS_PROFILE + FEDERATE_OIDC_SECRET_ID
# in env; a no-op if FEDERATE_OIDC_SECRET_ID is unset (e.g. a stage that
# deliberately runs Cognito-only).
#
# Prevents the 2026-08-30 outage-repro class: `data-processing` and any target
# that re-synths `cms-<stage>-ui` as a CDK dependency stack drops the
# AmazonFederateIdP resource from the template when `_federate_creds_present`
# is False in ui_stack.py — CloudFormation then deletes the pool-level IdP,
# and every Federate user loses access. See
# `issues/2026-08-30-federate-idp-guard-trust-without-cred/summary.md`.
#
# Provisioning-guard cross-check: `aspects/provisioning_guards.py` also fails
# synth when AmazonFederate is trusted but creds are absent (commit 18d437b3),
# so a Makefile change that forgets to run this helper trips the guard rather
# than silently deleting the IdP. That guard is the safety net; this script is
# the plumbing.

set -euo pipefail

# No-op if the secret id isn't set — genuine cognito-only stages don't need creds.
if [ -z "${FEDERATE_OIDC_SECRET_ID:-}" ]; then
    return 0 2>/dev/null || exit 0
fi

if [ -z "${AWS_REGION:-}" ]; then
    echo "❌ load-federate-creds.sh: AWS_REGION unset; source config/<stage>.env first" >&2
    return 1 2>/dev/null || exit 1
fi

_profile_flag=()
if [ -n "${AWS_PROFILE:-}" ]; then
    _profile_flag=(--profile "$AWS_PROFILE")
fi

# NOTE: `${arr[@]+"${arr[@]}"}` not `"${arr[@]}"`.
# This script runs under `set -u` (line 24), and on bash 3.2 — which is what
# macOS ships as /bin/bash (3.2.57) — expanding an EMPTY array as "${arr[@]}"
# errors with `_profile_flag[@]: unbound variable`. bash 4.4+ tolerates it.
# Invisible via `make`, which always passes AWS_PROFILE so the array is never
# empty; it only bites an operator sourcing this script directly with
# AWS_PROFILE unset. See
# issues/2026-09-01-deploy-simulation-missing-stage-config-preamble/.
_fed_json=$(aws secretsmanager get-secret-value \
    --secret-id "$FEDERATE_OIDC_SECRET_ID" \
    --region "$AWS_REGION" \
    ${_profile_flag[@]+"${_profile_flag[@]}"} \
    --query SecretString --output text 2>/dev/null || true)

if [ -z "$_fed_json" ]; then
    echo "❌ load-federate-creds.sh: FEDERATE_OIDC_SECRET_ID=$FEDERATE_OIDC_SECRET_ID set but unreadable." >&2
    echo "   Refusing to proceed: ui_stack.py drops the AmazonFederateIdP resource when creds" >&2
    echo "   are absent, and CloudFormation deletes the IdP. This is the 2026-08-11 outage." >&2
    return 1 2>/dev/null || exit 1
fi

# Extract the three fields ui_stack.py reads via os.environ.
FEDERATE_CLIENT_ID=$(printf '%s' "$_fed_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('client_id',''))")
FEDERATE_CLIENT_SECRET=$(printf '%s' "$_fed_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('client_secret',''))")
FEDERATE_OIDC_ISSUER=$(printf '%s' "$_fed_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('oidc_issuer',''))")

if [ -z "$FEDERATE_CLIENT_ID" ] || [ -z "$FEDERATE_CLIENT_SECRET" ]; then
    echo "❌ load-federate-creds.sh: secret $FEDERATE_OIDC_SECRET_ID is missing client_id or client_secret." >&2
    return 1 2>/dev/null || exit 1
fi

export FEDERATE_CLIENT_ID FEDERATE_CLIENT_SECRET FEDERATE_OIDC_ISSUER
echo "🔐 Federate OIDC creds loaded from Secrets Manager ($FEDERATE_OIDC_SECRET_ID)"
