#!/usr/bin/env bash
# Targeted deploy of cms-<stage>-subscriptions.
#
# WHY THIS EXISTS
# ---------------
# `cms-{stage}-subscriptions` is gated on DEPLOY_SUBSCRIPTIONS=true and has NO
# Makefile target, so there is no canonical way to deploy it on its own. The
# flags were set ad-hoc by whoever deployed it last, which means a naive
# redeploy silently drops resources:
#
#   * DEPLOY_SUBSCRIPTIONS unset  -> the whole stack leaves the app; CFN deletes it.
#   * DEPLOY_MERIDIAN unset       -> subscriptions_stack.py drops CS_SOURCE_TABLE_NAME
#                                    and its IAM grant from the records Lambda, so the
#                                    stack deploys but cannot dispatch Meridian reads.
#                                    Both gates MUST agree (app.py ~line 383).
#
# The live records Lambda HAS CS_SOURCE_TABLE_NAME, so the deployed state is
# DEPLOY_SUBSCRIPTIONS=true + DEPLOY_MERIDIAN=true. Both are pinned below.
#
# It also replicates what `staging-deploy` does per recipe line, because several
# fail-closed synth guards fire APP-WIDE regardless of deploy target
# (Makefile:77): Federate creds from Secrets Manager, and the UI custom-domain /
# gate / WAF context threaded as CDK -c flags. Omitting them does not fail
# safely -- it has previously deleted the pool-level Federate IdP and dropped a
# custom-domain cert (issues/2026-08-11-*, 2026-08-30-federate-idp-guard-*).
#
# Usage:
#   ./scripts/deploy-subscriptions-stack.sh diff     # non-destructive, default
#   ./scripts/deploy-subscriptions-stack.sh deploy
set -euo pipefail

ACTION="${1:-diff}"
STAGE="${DEPLOYMENT_STAGE:-staging}"
CFG="config/${STAGE}.env"

cd "$(dirname "$0")/.."

[ -f "$CFG" ] || { echo "✗ $CFG missing"; exit 1; }

# shellcheck disable=SC1090
set -a; . "$CFG"; set +a
# shellcheck disable=SC1091
. scripts/load-federate-creds.sh

# Rebuild the UI context flags exactly as the Makefile does (lines ~700-740).
CTX=()
_dom="$(sed -n 's/^UI_CUSTOM_DOMAIN=//p' "$CFG")"
_cert="$(sed -n 's/^UI_CUSTOM_DOMAIN_CERT_ARN=//p' "$CFG")"
_creg="$(sed -n 's/^UI_CUSTOM_DOMAIN_REGION=//p' "$CFG")"
_dns="$(sed -n 's/^UI_CUSTOM_DOMAIN_MANAGE_DNS=//p' "$CFG")"
_kg="$(sed -n 's/^STAGING_GATE_KEY_GROUP_ID=//p' "$CFG")"
_eiap="$(sed -n 's/^CMS_ENABLE_INTERNAL_AUTO_PROVISIONING=//p' "$CFG")"
_eess="$(sed -n 's/^CMS_ENABLE_EXTERNAL_SELF_SIGNUP=//p' "$CFG")"
_waf="$(sed -n 's/^WAF_WEB_ACL_ARN=//p' "$CFG")"
_csep="$(sed -n 's/^CS_PRODUCER_API_ENDPOINT=//p' "$CFG")"
_cssid="$(sed -n 's/^CS_SUBSCRIPTION_ID=//p' "$CFG")"

if [ -n "$_dom" ] && [ -n "$_cert" ]; then
  CTX+=(-c "uiCustomDomain=$_dom" -c "uiCustomDomainCertArn=$_cert"
        -c "uiCustomDomainRegion=${_creg:-$AWS_REGION}"
        -c "uiCustomDomainManageDns=${_dns:-false}")
fi
[ -n "$_kg" ]    && CTX+=(-c "stagingGateKeyGroupId=$_kg")
[ -n "$_eiap" ]  && CTX+=(-c "cms.enable_internal_auto_provisioning=$_eiap")
[ -n "$_eess" ]  && CTX+=(-c "cms.enable_external_self_signup=$_eess")
[ -n "$_waf" ]   && CTX+=(-c "wafWebAclArn=$_waf")
[ -n "$_csep" ]  && CTX+=(-c "csProducerApiEndpoint=$_csep")
[ -n "$_cssid" ] && CTX+=(-c "csSubscriptionId=$_cssid")

echo "→ stage=$STAGE  region=$AWS_REGION  action=$ACTION  ctx_flags=${#CTX[@]}"

# Both gates pinned: inferred from the live records Lambda carrying
# CS_SOURCE_TABLE_NAME. Do not drop either.
export DEPLOY_SUBSCRIPTIONS=true
export DEPLOY_MERIDIAN=true

case "$ACTION" in
  diff)   cdk diff   "cms-${STAGE}-subscriptions" "${CTX[@]}" ;;
  deploy) cdk deploy "cms-${STAGE}-subscriptions" "${CTX[@]}" --require-approval never ;;
  *)      echo "✗ unknown action: $ACTION (want: diff|deploy)"; exit 1 ;;
esac
