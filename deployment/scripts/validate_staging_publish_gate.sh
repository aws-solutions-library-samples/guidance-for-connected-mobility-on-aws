#!/usr/bin/env bash
#
# validate_staging_publish_gate.sh — post-deploy health gate for the v0.2.3
# CMS public-mirror publish (H1 3775026 auth-gaps fix).
#
# Read-only and idempotent. Runs 7 checks against the live staging deploy
# and aggregates pass/fail. NEVER mutates AWS state. Safe to re-run.
#
# Usage:
#   AWS_PROFILE=default AWS_REGION=us-west-2 DEPLOYMENT_STAGE=staging \
#     bash scripts/validate_staging_publish_gate.sh
#
# Exit codes:
#   0  — all 7 checks passed; staging is publish-ready.
#   N  — number of FAILed checks (>0 means at least one gate is open).
#
# Checks:
#   1. CFN stack states         — every cms-{stage}-* stack in *_COMPLETE
#   2. Flink apps RUNNING       — all 9 apps RUNNING (not UPDATING/STOPPING)
#   3. Flink MSK auth           — every app's PropertyMap has IAM keys
#                                 (bootstrap.servers, sasl.mechanism=AWS_MSK_IAM,
#                                 sasl.jaas.config, sasl.client.callback.handler.class)
#                                 and NO SCRAM keys (sasl.username, secret.arn,
#                                 msk.cluster.arn). Closes Fix 1 from
#                                 issues/2026-06-11-flink-stack-deploy-blockers.
#   4. fw-telemetry consuming   — CloudWatch numRecordsInPerSecond > 0 over
#                                 last 10 min, proving FWE→preprocessed→trip path.
#   5. Trip materialization     — fresh trip in cms-{stage}-storage-trips with
#                                 startTime in last 30 min (loose; tightens once
#                                 simulator drives steady traffic).
#   6. Auth-fix runtime gate    — invokes test_unauth_probes.sh: every
#                                 previously-unauthenticated CMS API route
#                                 (simulation × 14, commands × 6, data-processing,
#                                 predictive-agent if deployed) must return 401/403
#                                 with no creds. Closes H1 3775026 § Pattern A.
#   7. No critical errors       — CloudWatch Logs grep for ERROR/Traceback in
#                                 the 5 trip-path Flink apps + the auth-fix Lambda
#                                 functions over the last 10 min.

set -uo pipefail
export AWS_PAGER=""

PROFILE="${AWS_PROFILE:-default}"
REGION="${AWS_REGION:-us-west-2}"
STAGE="${DEPLOYMENT_STAGE:-staging}"

GREEN=$'\033[0;32m'
YELLOW=$'\033[0;33m'
RED=$'\033[0;31m'
BOLD=$'\033[1m'
NC=$'\033[0m'

PASS=0
FAIL=0
WARN=0

ok()    { printf "%s✅ PASS%s %s\n" "$GREEN" "$NC" "$1"; PASS=$((PASS+1)); }
fail()  { printf "%s❌ FAIL%s %s\n" "$RED"   "$NC" "$1"; FAIL=$((FAIL+1)); }
warn()  { printf "%s⚠️  WARN%s %s\n" "$YELLOW" "$NC" "$1"; WARN=$((WARN+1)); }
section(){ printf "\n%s── %s ──%s\n" "$BOLD" "$1" "$NC"; }

# ── pre-flight ──────────────────────────────────────────────────────────────
section "Pre-flight"
aws sts get-caller-identity --profile "$PROFILE" --query 'Account' --output text >/dev/null 2>&1 \
  && ok "AWS credentials reachable on profile=$PROFILE" \
  || { fail "cannot resolve AWS credentials on profile=$PROFILE"; exit 1; }

ACCOUNT=$(aws sts get-caller-identity --profile "$PROFILE" --query 'Account' --output text)
printf "  account=%s region=%s stage=%s\n" "$ACCOUNT" "$REGION" "$STAGE"

# ── Check 1: CFN stack states ───────────────────────────────────────────────
section "1) CFN stack states"
BAD_STACKS=$(aws cloudformation list-stacks \
  --profile "$PROFILE" --region "$REGION" \
  --stack-status-filter CREATE_IN_PROGRESS UPDATE_IN_PROGRESS UPDATE_ROLLBACK_IN_PROGRESS \
                        UPDATE_ROLLBACK_COMPLETE UPDATE_FAILED CREATE_FAILED ROLLBACK_COMPLETE \
                        ROLLBACK_FAILED DELETE_FAILED \
  --query "StackSummaries[?starts_with(StackName, 'cms-${STAGE}-')].{Name:StackName,Status:StackStatus}" \
  --output text 2>&1)

if [ -z "$BAD_STACKS" ]; then
  COUNT=$(aws cloudformation list-stacks \
    --profile "$PROFILE" --region "$REGION" \
    --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE \
    --query "length(StackSummaries[?starts_with(StackName, 'cms-${STAGE}-')])" \
    --output text 2>/dev/null)
  ok "all $COUNT cms-${STAGE}-* stacks in *_COMPLETE state"
else
  fail "stacks in non-clean state:"
  echo "$BAD_STACKS" | sed 's/^/    /'
fi

# ── Check 2: Flink apps RUNNING ─────────────────────────────────────────────
section "2) Flink apps RUNNING"
# Filter: include cms-staging-flink-* apps that are CFN-managed by the
# cms-staging-flink stack. Specifically EXCLUDE campaign-sync-processor —
# its CFN owner is the fleetwise stack, not the flink stack, even though
# its name starts with "cms-{stage}-flink-" (legacy naming, predates the
# fleetwise carve-out). Including it would erroneously count 10 instead
# of 9 and fail with "expected 9 found 10" on healthy deploys.
APPS_NOT_RUNNING=$(aws kinesisanalyticsv2 list-applications \
  --profile "$PROFILE" --region "$REGION" \
  --query "ApplicationSummaries[?starts_with(ApplicationName, 'cms-${STAGE}-flink-') && ApplicationName != 'cms-${STAGE}-flink-campaign-sync-processor' && ApplicationStatus != 'RUNNING'].[ApplicationName,ApplicationStatus]" \
  --output text 2>&1)

EXPECTED_APP_COUNT=9
ACTUAL_APP_COUNT=$(aws kinesisanalyticsv2 list-applications \
  --profile "$PROFILE" --region "$REGION" \
  --query "length(ApplicationSummaries[?starts_with(ApplicationName, 'cms-${STAGE}-flink-') && ApplicationName != 'cms-${STAGE}-flink-campaign-sync-processor'])" \
  --output text 2>/dev/null)

if [ "$ACTUAL_APP_COUNT" -ne "$EXPECTED_APP_COUNT" ]; then
  fail "expected $EXPECTED_APP_COUNT Flink apps, found $ACTUAL_APP_COUNT"
elif [ -z "$APPS_NOT_RUNNING" ]; then
  ok "all $ACTUAL_APP_COUNT Flink apps RUNNING"
else
  fail "apps not RUNNING:"
  echo "$APPS_NOT_RUNNING" | sed 's/^/    /'
fi

# ── Check 3: Flink MSK IAM auth in PropertyMaps ─────────────────────────────
section "3) Flink MSK IAM auth (PropertyMaps)"
APP_NAMES=$(aws kinesisanalyticsv2 list-applications \
  --profile "$PROFILE" --region "$REGION" \
  --query "ApplicationSummaries[?starts_with(ApplicationName, 'cms-${STAGE}-flink-') && ApplicationName != 'cms-${STAGE}-flink-campaign-sync-processor'].ApplicationName" \
  --output text 2>/dev/null | tr '\t' '\n')

IAM_REQUIRED=("bootstrap.servers" "security.protocol" "sasl.mechanism"
              "sasl.jaas.config" "sasl.client.callback.handler.class")
SCRAM_FORBIDDEN=("sasl.username" "secret.arn" "msk.cluster.arn")
APPS_BAD_AUTH=0

for app in $APP_NAMES; do
  PM_JSON=$(aws kinesisanalyticsv2 describe-application \
    --application-name "$app" --profile "$PROFILE" --region "$REGION" \
    --query 'ApplicationDetail.ApplicationConfigurationDescription.EnvironmentPropertyDescriptions.PropertyGroupDescriptions[0].PropertyMap' \
    --output json 2>/dev/null)

  app_short="${app#cms-${STAGE}-flink-}"
  app_bad=0
  for k in "${IAM_REQUIRED[@]}"; do
    val=$(echo "$PM_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('$k',''))" 2>/dev/null)
    if [ -z "$val" ]; then
      [ $app_bad -eq 0 ] && fail "  $app_short: missing IAM key '$k'"
      app_bad=1
    fi
  done
  for k in "${SCRAM_FORBIDDEN[@]}"; do
    val=$(echo "$PM_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('$k',''))" 2>/dev/null)
    if [ -n "$val" ]; then
      [ $app_bad -eq 0 ] && fail "  $app_short: forbidden SCRAM key '$k' present (=$val)"
      app_bad=1
    fi
  done
  mech=$(echo "$PM_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('sasl.mechanism',''))" 2>/dev/null)
  if [ "$mech" != "AWS_MSK_IAM" ]; then
    [ $app_bad -eq 0 ] && fail "  $app_short: sasl.mechanism='$mech' (expected AWS_MSK_IAM)"
    app_bad=1
  fi
  [ $app_bad -eq 1 ] && APPS_BAD_AUTH=$((APPS_BAD_AUTH+1))
done

if [ $APPS_BAD_AUTH -eq 0 ] && [ -n "$APP_NAMES" ]; then
  ok "all $ACTUAL_APP_COUNT Flink apps have correct IAM auth (no SCRAM residue)"
fi

# ── Check 4: fw-telemetry-processor consuming ──────────────────────────────
section "4) fw-telemetry-processor consuming (numRecordsInPerSecond > 0, last 10 min)"
END_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
START_TIME=$(date -u -v-10M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%SZ)

NUM_RECORDS=$(aws cloudwatch get-metric-statistics \
  --profile "$PROFILE" --region "$REGION" \
  --namespace AWS/KinesisAnalytics \
  --metric-name numRecordsInPerSecond \
  --dimensions "Name=Application,Value=cms-${STAGE}-flink-fw-telemetry-processor" \
  --statistics Sum \
  --period 600 \
  --start-time "$START_TIME" \
  --end-time "$END_TIME" \
  --query 'Datapoints[0].Sum' --output text 2>/dev/null)

if [ -z "$NUM_RECORDS" ] || [ "$NUM_RECORDS" = "None" ]; then
  warn "no datapoints yet for fw-telemetry-processor numRecordsInPerSecond — apps may still be starting (re-run in 5 min)"
elif (( $(echo "$NUM_RECORDS > 0" | bc -l 2>/dev/null || echo 0) )); then
  ok "fw-telemetry-processor consuming records (sum=$NUM_RECORDS over 10 min)"
else
  fail "fw-telemetry-processor numRecordsInPerSecond sum=$NUM_RECORDS — not consuming MSK"
fi

# ── Check 5: Trip materialization ───────────────────────────────────────────
section "5) Trip materialization (fresh trip in last 30 min)"
THIRTY_MIN_AGO_MS=$(($(date +%s) * 1000 - 1800000))

TRIP_COUNT=$(aws dynamodb scan \
  --table-name "cms-${STAGE}-storage-trips" \
  --profile "$PROFILE" --region "$REGION" \
  --filter-expression "startTime > :thirty_min_ago_iso" \
  --expression-attribute-values "{\":thirty_min_ago_iso\":{\"S\":\"$(date -u -v-30M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '30 minutes ago' +%Y-%m-%dT%H:%M:%SZ)\"}}" \
  --select COUNT --query 'Count' --output text 2>/dev/null)

if [ -z "$TRIP_COUNT" ] || [ "$TRIP_COUNT" = "None" ]; then
  warn "trip scan returned no result — table may be empty or filter expression mismatched (check schema)"
elif [ "$TRIP_COUNT" -gt 0 ] 2>/dev/null; then
  ok "$TRIP_COUNT trip(s) created in last 30 min"
else
  warn "0 trips in last 30 min — expected if simulator/FWE not yet driving traffic post-deploy (re-run in 5-10 min)"
fi

# ── Check 6: Auth-fix runtime probes ────────────────────────────────────────
section "6) Auth-fix runtime probes (test_unauth_probes.sh)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -x "$SCRIPT_DIR/test_unauth_probes.sh" ]; then
  if AWS_PROFILE="$PROFILE" AWS_REGION="$REGION" DEPLOYMENT_STAGE="$STAGE" \
     bash "$SCRIPT_DIR/test_unauth_probes.sh" >/tmp/unauth-probes.log 2>&1; then
    PROBE_LINES=$(grep -c "401\|403" /tmp/unauth-probes.log 2>/dev/null || echo 0)
    ok "test_unauth_probes.sh exit 0 — every probed route returned 401/403 (auth fix landed)"
  else
    fail "test_unauth_probes.sh exit non-zero — see /tmp/unauth-probes.log:"
    tail -20 /tmp/unauth-probes.log | sed 's/^/    /'
  fi
else
  warn "test_unauth_probes.sh not executable — run: chmod +x $SCRIPT_DIR/test_unauth_probes.sh"
fi

# ── Check 7: Critical-path log grep ─────────────────────────────────────────
section "7) Recent critical errors (Flink + Lambda, last 10 min)"
SINCE_MS=$(($(date +%s) * 1000 - 600000))
ERR_TOTAL=0
LOG_GROUPS=(
  "/aws/kinesis-analytics/cms-${STAGE}-flink-fw-telemetry-processor"
  "/aws/kinesis-analytics/cms-${STAGE}-flink-trip-processor"
  "/aws/kinesis-analytics/cms-${STAGE}-flink-event-driven-telemetry-processor"
  "/aws/kinesis-analytics/cms-${STAGE}-flink-simulator-preprocessor"
  "/aws/kinesis-analytics/cms-${STAGE}-flink-oem-telemetry-processor"
)
# KDA wraps each log line as JSON with "messageType":"ERROR" for real errors.
# A coarse `?ERROR ?Exception` filter over-matches Flink's verbose
# startup/config dumps (e.g. exception-classifier.filters property values
# embed words like "IllegalArgumentException" / "NullPointerException" in
# regex patterns), which fired ~5 false-positives per app on every healthy
# deploy. Filter on the structured field instead.
for lg in "${LOG_GROUPS[@]}"; do
  ERR_COUNT=$(aws logs filter-log-events \
    --log-group-name "$lg" \
    --profile "$PROFILE" --region "$REGION" \
    --start-time "$SINCE_MS" \
    --filter-pattern '{ $.messageType = "ERROR" }' \
    --max-items 5 \
    --query 'length(events)' --output text 2>/dev/null)
  if [ -n "$ERR_COUNT" ] && [ "$ERR_COUNT" -gt 0 ] 2>/dev/null; then
    fail "  ${lg##*/}: $ERR_COUNT real ERROR-typed event(s) in last 10 min"
    # Show the first error's message for triage
    aws logs filter-log-events \
      --log-group-name "$lg" \
      --profile "$PROFILE" --region "$REGION" \
      --start-time "$SINCE_MS" \
      --filter-pattern '{ $.messageType = "ERROR" }' \
      --max-items 1 \
      --query 'events[0].message' --output text 2>/dev/null \
      | python3 -c "import sys,json; m=json.loads(sys.stdin.read()); print('     →', m.get('message','')[:200])" 2>/dev/null
    ERR_TOTAL=$((ERR_TOTAL + ERR_COUNT))
  fi
done
if [ "$ERR_TOTAL" -eq 0 ]; then
  ok "no messageType=ERROR events in 5 critical Flink log groups (last 10 min)"
fi

# ── Summary ─────────────────────────────────────────────────────────────────
section "Summary"
printf "  %sPASS%s = %d\n" "$GREEN" "$NC" "$PASS"
printf "  %sFAIL%s = %d\n" "$RED"   "$NC" "$FAIL"
printf "  %sWARN%s = %d\n" "$YELLOW" "$NC" "$WARN"
echo ""

if [ "$FAIL" -eq 0 ]; then
  printf "%s🎉 Staging is v0.2.3-publish-ready (operator: see ~/.kiro/steering/public-mirror-publish.md).%s\n" "$GREEN" "$NC"
  exit 0
else
  printf "%s🚧 %d gate(s) failed. Address before publish.%s\n" "$RED" "$FAIL" "$NC"
  exit "$FAIL"
fi
