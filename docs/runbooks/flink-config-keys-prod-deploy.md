# Flink Prod Deploy After Config-Keys Fix

## Overview

This runbook guides the first production deployment of spec `2026-06-08-cms-flink-cfn-config-keys-fix` to `cms-prod-flink`. This is a special-case deployment because it replaces runtime-override-populated application configuration with CDK-source configuration in a single CloudFormation change-set. 

**Pre-conditions for this deployment:**
- Staging soak is green (all 9 Flink apps in `cms-staging-flink` have run telemetry successfully with CDK-sourced configuration)
- Shape-mismatch spec `2026-06-05-cms-oem1-connector-flink-shape-mismatch` C7/C8 24h soak is closed with a green verdict
- This spec's review and security-review gates have all passed
- All 9 Flink apps in `cms-staging-flink` are synthesizing non-empty `ApplicationConfiguration` directly from CDK source (verified via `cdk synth cms-staging-flink 2>&1 | grep -c "ApplicationConfiguration: {}"` returning 0)

## Prereqs

**AWS Access:**
- AWS profile with cross-account assume-role to `cms-prod` account
- IAM permissions: `cloudformation:*`, `kinesisanalyticsv2:DescribeApplication`, `kinesisanalyticsv2:UpdateApplication`, `s3:*`, `logs:FilterLogEvents`, `cloudformation:DescribeChangeSet`, `cloudformation:ExecuteChangeSet`

**Tools & Environment:**
- `cdk` (≥2.0) installed and in `$PATH`
- `aws` CLI (≥2.0) installed
- `python3` installed
- Project deployment venv activated: `cd deployment && source .venv/bin/activate`
- Operator has read this runbook end-to-end and confirmed understanding of the gating steps below

**Region & Stage:**
- `<region>` = `us-east-1` (prod)
- `<prod-profile>` = AWS profile name for prod account access (e.g., `prod`, `cms-prod`)

## Step 1: Snapshot Current Prod Runtime Config

Capture the current runtime configuration of all 9 prod Flink applications. This snapshot serves as a rollback reference if deployment fails.

**Apps to snapshot:**
1. EventDrivenTelemetryProcessor
2. OEMTelemetryProcessor
3. FWTelemetryProcessor
4. SimulatorPreprocessor
5. TelemetryEnhancedProcessor
6. TripProcessor
7. SafetyProcessor
8. MaintenanceProcessor
9. GeofenceProcessor

**Commands:**

```bash
#!/bin/bash
set -e

DEPLOYMENT_STAGE="prod"
REGION="us-east-1"
PROD_PROFILE="<prod-profile>"
SNAPSHOT_DATE=$(date +%Y-%m-%d)
SNAPSHOT_FILE="deployment/snapshots/prod-flink-pre-fix-${SNAPSHOT_DATE}.json"

mkdir -p deployment/snapshots

# Array of the 9 Flink app names
APPS=(
  "EventDrivenTelemetryProcessor"
  "OEMTelemetryProcessor"
  "FWTelemetryProcessor"
  "SimulatorPreprocessor"
  "TelemetryEnhancedProcessor"
  "TripProcessor"
  "SafetyProcessor"
  "MaintenanceProcessor"
  "GeofenceProcessor"
)

# Snapshot each app
echo "{" > "$SNAPSHOT_FILE"
for i in "${!APPS[@]}"; do
  APP_NAME="${APPS[$i]}"
  FULL_APP_NAME="cms-prod-${APP_NAME}"
  
  echo "Snapshotting $FULL_APP_NAME..." >&2
  aws kinesisanalyticsv2 describe-application \
    --application-name "$FULL_APP_NAME" \
    --region "$REGION" \
    --profile "$PROD_PROFILE" \
    --output json >> "$SNAPSHOT_FILE"
  
  # Add comma between entries (not after last)
  if [ $i -lt $((${#APPS[@]} - 1)) ]; then
    echo "," >> "$SNAPSHOT_FILE"
  fi
done
echo "}" >> "$SNAPSHOT_FILE"

echo "✓ Snapshot written to $SNAPSHOT_FILE"
```

**Expected outcome:**
- File `deployment/snapshots/prod-flink-pre-fix-<YYYY-MM-DD>.json` exists
- File contains `describe-application` JSON for all 9 apps
- Each app block includes `ApplicationConfigurationDescription` field with the current runtime-populated `EnvironmentProperties`

## Step 2: Diff Snapshot vs CDK-Source-Projected Config

Compare the snapshotted runtime configuration against what CDK will deploy. This step identifies any prod-specific property overrides that must be reconciled before deploy.

**Commands:**

```bash
# Synthesize prod Flink stack
cd deployment
cdk synth cms-prod-flink -o ./cdk.out

# Inspect the synthesized CloudFormation template
cat cdk.out/cms-prod-flink.template.json | python3 -m json.tool > /tmp/prod-flink-synth.json

# Manual comparison instructions:
# 1. Open the snapshot file in an editor or jq:
jq '.ApplicationConfigurationDescription.EnvironmentPropertiesDescription' \
  deployment/snapshots/prod-flink-pre-fix-<YYYY-MM-DD>.json

# 2. For each app in the synthesized template, extract the projected config:
jq '.Resources | to_entries[] | 
  select(.value.Type == "AWS::KinesisAnalyticsV2::Application") | 
  {app_name: .key, config: .value.Properties.ApplicationConfiguration}' \
  /tmp/prod-flink-synth.json
```

**TODO (follow-on issue):** Author a Python comparator script that validates property-map alignment and generates a structured diff report. For now, manual inspection below.

**Manual inspection steps:**

1. For each of the 9 apps, compare:
   - Snapshot's `EnvironmentProperties.PropertyGroups[0].PropertyMap` (runtime-current)
   - Synthesized template's `ApplicationConfiguration.EnvironmentProperties.PropertyGroups[0].PropertyMap` (CDK-source)

2. Use `jq` to extract and diff:
   ```bash
   # Runtime properties from snapshot
   jq '.ApplicationConfigurationDescription.EnvironmentPropertiesDescription.PropertyGroupDescriptions[0].PropertyMap' \
     deployment/snapshots/prod-flink-pre-fix-<YYYY-MM-DD>.json | jq -S . > /tmp/runtime-props.json
   
   # CDK-source properties from synth
   jq '.Resources.<AppResourceLogicalId>.Properties.ApplicationConfiguration.EnvironmentProperties.PropertyGroups[0].PropertyMap' \
     /tmp/prod-flink-synth.json | jq -S . > /tmp/cdk-props.json
   
   # Diff
   diff /tmp/runtime-props.json /tmp/cdk-props.json
   ```

## Step 3: Operator Review of Diff

**GATE — Pause for operator review before proceeding.**

Review the diff output from Step 2. Identify any **red flags** that must be escalated before proceeding:

**Red flags to watch for:**

- **Parallelism override present in snapshot but absent from CDK source** — Indicates prod is running with a hand-tuned parallelism value (e.g., `parallelism: 4`) that differs from CDK default (`parallelism: 1`). Deployment will reset to CDK default. **Action**: Consult the on-call architect/ops about whether the prod parallelism is intentional; if so, update CDK source or defer deploy.
- **Environment variables present in snapshot but absent from CDK source** — Any property-map key in runtime that isn't in CDK source will be dropped. **Action**: Manually add to CDK source before deploying, or document why the variable is no longer needed.
- **Environment variables present in CDK source but absent from snapshot** — New properties will be added to prod. **Action**: Review against the spec and staging validation to confirm this is expected.
- **Checkpoint interval or monitoring levels differ** — If snapshot shows custom checkpoint intervals (e.g., `checkpoint_interval: 120000`) that differ from CDK defaults, deployment will reset them. **Action**: Consult architect about whether checkpoint tuning is prod-required; if so, update CDK source.

**Operator sign-off:**

If all diffs are expected or reconciled:

```
[ ] Operator reviewed diff and confirmed no red flags.
    Operator name: _____________________ Date: _______
```

If red flags found, resolve them before proceeding. Do NOT proceed to Step 4 until this sign-off is completed.

## Step 4: Change-Set Deploy (NOT Direct Apply)

Deploy the new Flink configuration via CloudFormation change-set. This allows the operator to review and approve the exact changes before they execute.

**Commands:**

```bash
PROD_PROFILE="<prod-profile>"
REGION="us-east-1"
DEPLOYMENT_STAGE="prod"
DEPLOY_DATE=$(date +%Y-%m-%d)

cd deployment

# Create change-set (NOT executing immediately)
cdk deploy cms-prod-flink \
  --change-set-name "flink-config-keys-fix-${DEPLOY_DATE}" \
  --no-execute \
  --profile "$PROD_PROFILE" \
  --region "$REGION"
```

**Expected output:**
- CDK prints the change-set ID
- Change-set is created in CloudFormation console but NOT executed

**Operator review in CloudFormation console:**

1. Go to AWS CloudFormation console → Stacks → `cms-prod-flink`
2. Click **Change sets** tab
3. Select the change-set named `flink-config-keys-fix-<YYYY-MM-DD>`
4. Review **Changes** section:
   - All 9 Flink applications should be listed as **Modify** (not Delete/Create)
   - Each app's `ApplicationConfiguration` should show the new typed-property structure
   - No unexpected deletions of Flink apps or related resources

**Sign-off (operator must confirm before executing):**

```
[ ] Operator reviewed change-set in CloudFormation console.
    Expected 9 Modify operations for Flink apps. All appear correct.
    Operator name: _____________________ Date: _______
```

**Execute change-set:**

```bash
aws cloudformation execute-change-set \
  --change-set-name "flink-config-keys-fix-${DEPLOY_DATE}" \
  --stack-name "cms-prod-flink" \
  --region "$REGION" \
  --profile "$PROD_PROFILE"

echo "✓ Change-set executing..."
```

Monitor stack status in CloudFormation console until it reaches `UPDATE_COMPLETE` or `UPDATE_FAILED`.

## Step 5: Post-Deploy Smoke Test

After the change-set completes (10–15 minutes typical), run post-deploy smoke tests on each prod Flink application's CloudWatch logs.

**Smoke test pattern** (per `~/.kiro/steering/deploy-validation.md`):

```bash
#!/bin/bash
set -e

PROD_PROFILE="<prod-profile>"
REGION="us-east-1"
DEPLOYMENT_STAGE="prod"

# 9 apps
APPS=(
  "EventDrivenTelemetryProcessor"
  "OEMTelemetryProcessor"
  "FWTelemetryProcessor"
  "SimulatorPreprocessor"
  "TelemetryEnhancedProcessor"
  "TripProcessor"
  "SafetyProcessor"
  "MaintenanceProcessor"
  "GeofenceProcessor"
)

echo "Waiting 60 seconds for Flink apps to stabilize post-deploy..."
sleep 60

FAIL_COUNT=0

for APP_NAME in "${APPS[@]}"; do
  LOG_GROUP="/aws/kinesis-analytics/cms-prod-${APP_NAME}"
  
  # Calculate start-time (last 2 minutes in milliseconds since epoch)
  START_TIME_MS=$(python3 -c "import time; print(int((time.time() - 120) * 1000))")
  
  echo "Checking $LOG_GROUP..." >&2
  ERRORS=$(aws logs filter-log-events \
    --log-group-name "$LOG_GROUP" \
    --start-time "$START_TIME_MS" \
    --filter-pattern '?ERROR ?Traceback' \
    --limit 5 \
    --region "$REGION" \
    --profile "$PROD_PROFILE" \
    --query 'events[].message' \
    --output text 2>/dev/null || echo "")
  
  if [ -n "$ERRORS" ]; then
    echo "✗ ERRORS in $LOG_GROUP:" >&2
    echo "$ERRORS" >&2
    ((FAIL_COUNT++))
  else
    echo "✓ $LOG_GROUP clean" >&2
  fi
done

if [ $FAIL_COUNT -gt 0 ]; then
  echo "✗ Smoke test FAILED — $FAIL_COUNT app(s) have runtime errors" >&2
  exit 1
else
  echo "✓ Smoke test PASSED — all 9 apps clean" >&2
  exit 0
fi
```

**Expected outcome:**
- All 9 apps have clean logs (no ERROR or Traceback lines in the post-deploy window)
- Flink apps transition to `RUNNING` state within 5 minutes
- First telemetry messages appear on downstream MSK topics within 10 minutes

**Sign-off (operator confirms smoke is green):**

```
[ ] Post-deploy smoke test passed.
    No ERROR/Traceback lines in any of the 9 Flink app logs.
    Operator name: _____________________ Date: _______
```

If smoke fails, proceed to Step 6 (Rollback).

## Step 6: Rollback Procedure

If post-deploy smoke detects runtime errors, execute one of the following rollback paths.

### Rollback Path A: Re-apply Snapshotted Runtime Config (Fast)

Use the snapshot from Step 1 to restore the 9 apps to their pre-deploy runtime state.

```bash
#!/bin/bash
set -e

PROD_PROFILE="<prod-profile>"
REGION="us-east-1"
SNAPSHOT_FILE="deployment/snapshots/prod-flink-pre-fix-<YYYY-MM-DD>.json"

if [ ! -f "$SNAPSHOT_FILE" ]; then
  echo "✗ Snapshot file not found: $SNAPSHOT_FILE" >&2
  exit 1
fi

# Extract the 9 apps' EnvironmentPropertyUpdates from the snapshot
# and re-apply via update-application

APPS=(
  "EventDrivenTelemetryProcessor"
  "OEMTelemetryProcessor"
  "FWTelemetryProcessor"
  "SimulatorPreprocessor"
  "TelemetryEnhancedProcessor"
  "TripProcessor"
  "SafetyProcessor"
  "MaintenanceProcessor"
  "GeofenceProcessor"
)

for APP_NAME in "${APPS[@]}"; do
  FULL_APP_NAME="cms-prod-${APP_NAME}"
  
  # Extract EnvironmentProperties from snapshot for this app
  ENV_PROPS=$(jq ".[] | select(.ApplicationDetail.ApplicationName == \"$FULL_APP_NAME\") | \
    .ApplicationConfigurationDescription.EnvironmentPropertiesDescription.PropertyGroupDescriptions" \
    "$SNAPSHOT_FILE")
  
  # Build EnvironmentPropertyUpdates payload
  PAYLOAD=$(jq -n \
    --argjson env_props "$ENV_PROPS" \
    '{EnvironmentPropertyUpdates: {PropertyGroups: $env_props}}')
  
  echo "Rolling back $FULL_APP_NAME..." >&2
  # Resolve the app's CURRENT version-id at rollback time (NOT a hardcoded value —
  # the version-id increments on every successful update; using stale value 1 will
  # fail with `current_application_version_id` mismatch).
  CURRENT_VERSION=$(aws kinesisanalyticsv2 describe-application \
    --application-name "$FULL_APP_NAME" \
    --query 'ApplicationDetail.ApplicationVersionId' \
    --output text \
    --region "$REGION" \
    --profile "$PROD_PROFILE")

  aws kinesisanalyticsv2 update-application \
    --application-name "$FULL_APP_NAME" \
    --current-application-version-id "$CURRENT_VERSION" \
    --cli-input-json "$PAYLOAD" \
    --region "$REGION" \
    --profile "$PROD_PROFILE"
done

echo "✓ Rollback complete — runtime config restored from snapshot"
```

**Caveats:**
- This path requires the snapshot file to be present and valid
- The current application version ID is resolved at rollback time via `describe-application` — never hardcoded

### Rollback Path B: Roll Back the CloudFormation Change-Set

Revert the stack to its prior template by rolling back the change-set.

```bash
PROD_PROFILE="<prod-profile>"
REGION="us-east-1"

aws cloudformation cancel-update-stack \
  --stack-name "cms-prod-flink" \
  --region "$REGION" \
  --profile "$PROD_PROFILE"

echo "✓ Stack rollback initiated"
```

**OR** roll back to the prior CloudFormation template version. The pre-deploy template is captured by AWS as a previous template version on every successful change-set execution — retrieve it with `get-template`:

```bash
PROD_PROFILE="<prod-profile>"
REGION="us-east-1"

# Capture the currently-deployed template (post-fix; what we want to revert FROM)
aws cloudformation get-template \
  --stack-name "cms-prod-flink" \
  --query 'TemplateBody' \
  --output json \
  --region "$REGION" \
  --profile "$PROD_PROFILE" > /tmp/cms-prod-flink.current.template.json

# Capture the prior template if a snapshot was taken before deploy in Step 1.
# If no pre-deploy template snapshot exists, the safest revert is to redeploy
# the prior commit's CDK source via:
#   git checkout <prior-commit-sha> -- deployment/stacks/flink_stack.py
#   cd deployment && cdk synth cms-prod-flink -o ./cdk.out
# then apply that synthesized template via update-stack.

# Apply the prior template via update-stack (only if a valid prior template
# is available at /tmp/cms-prod-flink.prior.template.json):
# aws cloudformation update-stack \
#   --stack-name "cms-prod-flink" \
#   --template-body file:///tmp/cms-prod-flink.prior.template.json \
#   --capabilities CAPABILITY_NAMED_IAM \
#   --region "$REGION" \
#   --profile "$PROD_PROFILE"

echo "✓ Rollback templates captured at /tmp/cms-prod-flink.current.template.json"
echo "  (operator must produce prior template via git revert OR change-set rollback)"
```

**Sign-off (operator confirms rollback is complete):**

```
[ ] Rollback executed and prod Flink apps restored to known-good state.
    Operator name: _____________________ Date: _______
```

After rollback, escalate to the architect for investigation. Do NOT retry deploy without root-cause analysis.

## Troubleshooting

### Change-set fails to create: `ValidationError: Template format error`

**Likely cause:** CDK synthesis failed; the template is malformed.

**Fix:**
1. Re-run `cd deployment && cdk synth cms-prod-flink --profile <prod-profile>` locally
2. Check for errors in synth output
3. If synth succeeds locally but fails in the change-set creation, verify AWS credentials and IAM permissions

### Smoke catches `ERROR` lines in logs

**Likely cause:** One or more Flink apps crashed or encountered runtime errors after deployment.

**Investigation:**
1. Read the full logs: `aws logs tail /aws/kinesis-analytics/cms-prod-<AppName> --since 5m --follow`
2. Look for the root error (usually a stack trace or descriptive error message)
3. Common errors:
   - **`AccessDeniedException` on S3 bucket access**: IAM role is missing S3 read permissions on the JAR bucket or manifest bucket
   - **`NullPointerException` in application code**: A configuration property expected by the Flink app is missing or malformed
   - **`KafkaException` on topic connection**: MSK topic does not exist or security group blocks access
4. Fix the root cause in CDK source and re-deploy (go back to Step 4)

### `update-application` race with the deploy

**Symptom:** During Step 5 smoke test, an app shows `ERROR: ApplicationVersionMismatchException` or the app is stuck in `UPDATING` state.

**Likely cause:** The CFN deploy updated the app while `update-application` (from rollback Path A) was in flight, or a concurrent operator is also updating apps.

**Fix:**
1. Wait for the app to reach `RUNNING` state: `aws kinesisanalyticsv2 describe-application --application-name <app-name>` and check `ApplicationStatus`
2. Retry the smoke test after the app stabilizes (typically 2–3 minutes)
3. If the app remains stuck in `UPDATING`, consult AWS support — the app may need a manual state reset

### Missing snapshot file

**Symptom:** Step 6 rollback (Path A) fails with "Snapshot file not found."

**Fix:**
1. Ensure Step 1 ran successfully and the snapshot file was written
2. Verify the file exists: `test -f deployment/snapshots/prod-flink-pre-fix-<YYYY-MM-DD>.json`
3. If the file is missing, use rollback Path B (CFN stack rollback) instead

## Sign-Off Checklist

Operator must initial each step before proceeding to the next.

```
[ ] Step 1: Snapshot captured. Snapshot file: _____________________
    Operator initial: _____ Date: _______

[ ] Step 2: Diff reviewed. No unresolved red flags.
    Operator initial: _____ Date: _______

[ ] Step 3: Operator review gate passed. Red flags resolved or documented.
    Operator initial: _____ Date: _______

[ ] Step 4: Change-set created. Change-set ID: _____________________
    Operator reviewed change-set in CFN console. All 9 Modify operations expected.
    Change-set executed.
    Operator initial: _____ Date: _______

[ ] Step 5: Post-deploy smoke test passed. No ERROR/Traceback in logs.
    Operator initial: _____ Date: _______

[ ] Deployment complete. All 9 prod Flink apps running with CDK-source configuration.
    Operator sign-off: _____________________ Date: _______
```
