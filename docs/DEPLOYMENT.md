# CMS Deployment Guide

This guide covers deployment procedures for the Connected Mobility System (CMS) staging and production environments.

## Environment Overview

The CMS uses a single-account, two-region deployment model:

| Environment | Region | Purpose |
|-------------|--------|---------|
| **Staging** | `us-west-2` | Validate changes end-to-end before prod |
| **Prod** | `us-east-1` | Customer-facing (deployed when ready) |

Both environments run in a single AWS account (set in deployment/config/staging.env and prod.env) with isolation enforced via region separation, stack name prefixes, and per-region IAM roles.

## Security context flags

The CMS template ships with two CDK context flags that gate optional
demo-permissive behavior. Both default to `false` per the AWS
Solutions Library reference-architecture security threat model.

| Flag | Default | What it controls |
|---|---|---|
| `cms.allow_self_signup` | `false` | Cognito User Pool `self_sign_up_enabled`. When `true`, anyone with an email can self-register and obtain a JWT for `/api/v1/*` fleet-management routes. **NOT recommended for production.** |
| `cms.allow_unauth_map_auth` | `false` | Identity Pool `allow_unauthenticated_identities`. When `true`, the `CognitoUnauthenticatedRole` is created with Location Services permissions, enabling anonymous map UI. The role is appropriately scoped to map tiles only — but the issuance itself is a defect for production deployments. **Opt in only for demos that need anonymous map preview.** |
| `cms.allow_unauth_websocket` | `false` | WebSocket API `$connect` authorization. When `false` (default), `$connect` requires a Cognito JWT (`?token=<jwt>` on the upgrade URL), validated by a Lambda REQUEST authorizer against the User Pool JWKS; anonymous upgrades get **HTTP 401**. When `true`, all WebSocket routes are anonymous (`NONE`). **Opt in only for demos that need anonymous WebSocket.** |

### Opting in

Demo deployments override at synth/deploy time:

```bash
cdk synth --context cms.allow_self_signup=true --context cms.allow_unauth_map_auth=true
```

Or persist in your local `cdk.context.json` (gitignored). Do NOT modify
`cdk.json` to flip the defaults — that file is checked-in and represents
the published reference behavior.

### Verifying enforcement

Post-deploy, run:

```bash
cd deployment
bash scripts/test_unauth_probes.sh   # all unauth probes → 401/403
python3 scripts/test_self_signup_blocked.py   # SignUp → NotAuthorizedException
python3 scripts/test_guest_creds_blocked.py   # GetId → error
bash scripts/test_websocket_probes.sh   # WS $connect → 401 unauth / 401 bad-token / 101 valid
```

> The WebSocket valid-token (101) check needs a Cognito id-token. Provide
> `CMS_TEST_JWT=<id-token>`, or `CMS_TEST_USER` + `CMS_TEST_PASSWORD` (the script
> acquires one via `USER_PASSWORD_AUTH`). Without a credential, probes 1–2 (the
> 401 negative checks) still run and the 101 check is skipped.

### Realtime UI (WebSocket telemetry)

The CMS UI consumes live fleet telemetry over the secured WebSocket API. The
endpoint is published to the frontend as `runtimeConfig.wsEndpoint` (from the
`WebSocketEndpoint` CFN output). The Fleet Vehicle Map (`FleetVehicleMapView`)
connects WS-primary with REST polling as fallback — if the WebSocket can't
connect, the map still loads via REST.

- **Per-fleet users** connect with `?token=<jwt>&fleetId=<fleet>`; the `$connect`
  Lambda authorizer verifies the JWT and the handler enforces fleet membership.
- **`platform-admin` users** connect **all-fleet** (no `fleetId`); the connection is
  stored under `'*'` and the `ws-fanout` consumer delivers every fleet's telemetry
  to it. Admin status is taken from the authorizer-verified `cognito:groups` — never
  client-asserted.
- Live telemetry requires the `ws-fanout` service running (`make deploy-ws-fanout`)
  and a producer publishing to `cms-fleet-<id>-telemetry` (e.g. the simulator).

## Phase 3: OEM1 Fleet Lifecycle Management

Phase 3 introduces admin-driven fleet lifecycle operations (bulk enroll, bulk unenroll, status sync, quota management) on top of the Phase 1 connector and Phase 2 single-vehicle admin tooling. This section documents the operational runbook for deploying, configuring, and troubleshooting Phase 3 fleet lifecycle resources.

### Configuration

#### `cdk.json` context additions

After Phase 3 deployment, verify the following context keys are present in `deployment/cdk.json`:

```json
{
  "context": {
    "oem1ProductCatalog": ["SKU-X", "SKU-Y"],
    "oem1StatusSyncCadenceMinutes": 15,
    "oem1EnrollmentPollerCadenceMinutes": 1,
    "oem1BulkEnrollMaxVins": 500
  }
}
```

- **`oem1ProductCatalog`**: list of valid subscription SKUs for fleet enrollment (e.g., `["Apex", "Optimize"]`). Consumed by the UI fleet creation form (M2 in PRD). Update this list when new SKUs become available from OEM1 without requiring a code re-deploy.
- **`oem1StatusSyncCadenceMinutes`**: interval (minutes) at which the `admin_status_sync` Lambda polls OEM1 for vehicle status updates. Default: 15 min. Decrease to increase sync frequency (at higher OEM1 API quota cost); increase to reduce cost but tolerate staler status.
- **`oem1EnrollmentPollerCadenceMinutes`**: interval (minutes) for the `admin_enrollment_poller` to poll OEM1 for enrollment request status. Default: 1 min. Per spec § 4.2, the poller backs off exponentially when no change is detected, so the 1-min cadence is the fastest initial poll, not a constant rate.
- **`oem1BulkEnrollMaxVins`**: maximum VIN count per bulk-enroll request. Default: 500. Constrained by OEM1's `/enrollment/v2/enroll` API limit; do not exceed 500 without OEM1 verification.

**Tuning guidance**: The default 15-min sync cadence is suitable for large fleets (1000+ vehicles). For smaller fleets (<100 vehicles) where near-real-time status is critical, reduce `oem1StatusSyncCadenceMinutes` to 5. For cost-sensitive deployments, increase to 30–60 min (users initiate manual refresh via the UI's "Refresh now" button for urgent status checks).

#### Role grants (v1)

Phase 3 introduces six new admin routes:

| Route | Handler | Auth | Scope |
|-------|---------|------|-------|
| `POST /admin/oem1/bulk-enroll` | `admin_bulk_enroll` | Cognito `platform-admin` group | Enroll one to N vehicles across OEM1 |
| `POST /admin/oem1/bulk-unenroll` | `admin_bulk_unenroll` | Cognito `platform-admin` group | Unenroll one to N vehicles from OEM1 |
| `POST /admin/oem1/refresh-status` | `admin_refresh_vehicle_status` | Cognito `platform-admin` group | Refresh OEM1 enrollment/readiness status for one or more vehicles |
| `GET /admin/oem1/enroll-quota` | `admin_enroll_quota` | Cognito `platform-admin` group | Query remaining hourly enrollment quota |
| `POST /admin/oem1/preflight` | `admin_preflight` | Cognito `platform-admin` group | Check vehicle capability for OEM1 enrollment |
| `GET /admin/oem1/list-enrolled` | `admin_list_enrolled` (if T5.7 deployed) | Cognito `platform-admin` group | List vehicles currently enrolled in OEM1 |

**Authorization model**: `/admin/oem1/*` routes accept two Cognito groups: `platform-admin` (cross-fleet authority, same as v1 baseline) and `fleet-operator` (per-fleet authority via `custom:fleetIds` claim). Pre-enrollment routes (preflight, bulk-enroll, enroll-quota) require `target_fleet_id` in request body OR re-use the existing `fleet_id` field as the auth signal (admin_bulk_enroll collapses both per security-review cycle 2 to eliminate divergence bypass class); post-enrollment routes (bulk-unenroll, refresh-status, list-enrolled) derive fleet via the `vehicleId-index` GSI on the fleet-enrollment table. See `.kiro/specs/2026-06-09-cms-fleet-manager-cognito-role/spec.md` § 1 for the per-route gate matrix.

To add a user to the `platform-admin` group:

```bash
USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name cms-staging-ui \
  --region us-west-2 \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)

aws cognito-idp admin-add-user-to-group \
  --user-pool-id "$USER_POOL_ID" \
  --username <user-email> \
  --group-name platform-admin \
  --region us-west-2
```

### OEM1 API quota and rate limits

OEM1 enforces a **4 requests per hour** per-customer quota on the `/enrollment/v2/enroll` endpoint (as stated in the spec § 1.1 M3). This is a hard ceiling: requests beyond 4 per hour receive HTTP 429 with a `Retry-After` header.

**Operator awareness**: When bulk-enrolling large fleets (e.g., 2000 vehicles across two requests of 1000 each), the second request may receive a 429 if submitted within the same hour. The CMS passthrough returns HTTP 429 to the UI; the user must wait (typically 1 hour from the first request) before re-submitting. Plan enrollment operations to avoid hour-boundary surprises by initiating them at least 1 hour apart.

The quota counter is exposed via the `GET /admin/oem1/enroll-quota` endpoint. The UI's enroll wizard polls this endpoint every 30 seconds and disables the Submit button when `remaining == 0`.

**Quota reset timing**: OEM1 resets the quota at the top of each hour (UTC). The API response includes `next_quota_reset_at` (ISO-8601 timestamp).

### CloudWatch Logs and Audit Trail

Phase 3 emits structured CloudWatch logs in place of a dedicated audit log table (that is deferred to a future cross-cutting audit initiative). Every write-path Lambda emits an INFO-level log line via `aws_lambda_powertools` Logger with the following fields:

```
@timestamp: ISO-8601 time
actor: Cognito user ID (subject claim)
fleet_id: Fleet ID from request
action: ENROLL | UN_ENROLL | REFRESH | (etc.)
vin_count: Number of VINs in request
oem1_request_id: OEM1's request ID (if applicable)
pre_flight_failure_count: Number of pre-flight rejections (enroll only)
accepted_count: Number of VINs accepted by OEM1 (enroll/unenroll only)
client_request_id: Client-supplied idempotency UUID (if present)
idempotency_replay: Boolean, true if this was a cached-response replay
```

**Example CloudWatch Log Insights queries**:

```
# All enroll attempts (successful + failed)
fields @timestamp, actor, fleet_id, action, vin_count, oem1_request_id
| filter action = 'ENROLL'
| stats count() as total_enrolls by actor

# Failed preflight checks (vehicles not capable of OEM1)
fields @timestamp, fleet_id, vin_count, pre_flight_failure_count
| filter action = 'ENROLL' and pre_flight_failure_count > 0
| stats avg(pre_flight_failure_count) as avg_failures by fleet_id

# Unenroll hard-delete operations
fields @timestamp, actor, fleet_id, vin_count, action
| filter action = 'UN_ENROLL' and hard_delete = true

# Idempotency replay detection (duplicate client requests)
fields @timestamp, client_request_id, idempotency_replay
| filter idempotency_replay = true
| stats count() as replayed_requests
```

Run these queries against the log groups:
- `/aws/lambda/cms-{stage}-oem1-admin-bulk-enroll*`
- `/aws/lambda/cms-{stage}-oem1-admin-bulk-unenroll*`
- `/aws/lambda/cms-{stage}-oem1-admin-refresh-status*`
- `/aws/lambda/cms-{stage}-oem1-admin-status-sync*`

These logs serve as the operational audit trail for all OEM1 fleet lifecycle actions in v1.

### Pre-deploy prerequisite checks

Before deploying Phase 3 (connector stack with the 6 new Lambda functions), run the standard preflight checks:

```bash
bash deployment/scripts/preflight-staging.sh
```

Additionally, verify the new DynamoDB tables will be created with correct region-suffixed names:

```bash
cd deployment && source .venv/bin/activate
DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \
  python3 -c "
import json
from stacks.connector_stack import CmsConnectorStack
from aws_cdk import Stack, App

app = App()
# Load context (including oem1* keys)
ctx = json.load(open('cdk.json'))['context']
stack = CmsConnectorStack(app, 'cms-staging-connector', env={...}, context=ctx)
print('✓ Stack synth OK — tables will be created')
"
```

Confirm the new enrollment-requests table will be created:

```bash
cd deployment && cdk synth CmsConnectorStack 2>&1 | grep -E 'oem1-enrollment-requests|GlobalSecondaryIndex'
# expected: table definition with 4 GSIs (submitted_by/submitted_at, customer_id/submitted_at, fleet_id/submitted_at, client_request_id HASH-only sparse)
```

### Smoke test post-deploy

After `cdk deploy CmsConnectorStack` completes successfully, verify the new Lambda functions are accessible and quota-tracking works:

```bash
# Invoke admin_enroll_quota Lambda with a test customer_id
aws lambda invoke \
  --function-name cms-staging-oem1-admin-enroll-quota \
  --region us-west-2 \
  --payload '{"stage":"staging"}' \
  /tmp/quota-resp.json && cat /tmp/quota-resp.json

# expected: HTTP 200 response with JSON body:
# {
#   "remaining": 4,
#   "submissions_in_last_hour": 0,
#   "next_quota_reset_at": "2026-06-07T20:00:00Z"
# }
```

If the response is HTTP 403 or 500, check:
1. Cognito User Pool ID is correctly set via `CMS_USER_POOL_ID` env var on the Lambda
2. The Lambda's IAM role has `dynamodb:Query` permission on the new `cms-{stage}-storage-oem1-enrollment-requests-{region}-{account}` table + the `(customer_id, submitted_at)` GSI

### UI stack deployment order

Phase 3 requires the UI stack to deploy **before** the connector stack re-deployment (with `CMS_USER_POOL_ID` environment variable set). If you are deploying Phase 3 for the first time:

1. **Deploy UI stack first** (if not already deployed):
   ```bash
   cd deployment && cdk deploy CmsUiStack --require-approval never
   ```

2. **Capture the Cognito User Pool ID**:
   ```bash
   USER_POOL_ID=$(aws cloudformation describe-stacks \
     --stack-name cms-staging-ui \
     --region us-west-2 \
     --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)
   echo "User Pool ID: $USER_POOL_ID"
   ```

3. **Deploy the connector stack with `CMS_USER_POOL_ID` set**:
   ```bash
   cd deployment && \
     CMS_USER_POOL_ID="$USER_POOL_ID" \
     cdk deploy CmsConnectorStack --require-approval never
   ```

Failure to set `CMS_USER_POOL_ID` before deploying the connector stack will cause the new admin routes to fail with HTTP 500 `UnsetVariableException` at runtime.

---

## OEM1 Staging Deploy

Deploy the OEM1 gRPC streaming connector to staging for end-to-end telemetry ingestion from real OEM1 vehicles.

### Prerequisites

Before starting the deploy, verify:
1. **SSM parameter `/cms/staging/connectors/oem1/flow`** is populated with the staging flow UUID provided by OEM1 operations.
2. **AWS Secrets Manager secret `cms-staging-connector-oem1-credentials`** (us-west-2) contains `client_id`, `client_secret`, `token_endpoint`, and `resource_id`.
3. **OEM1-side IAM grants** are in place on the customer's resources: `feed-reader` scope on the flow URI; access to Enrollment Status, vehicleData, and vehicleState API endpoints.
4. **≥ 5 real OEM1 vehicles** are actively producing telemetry on the staging flow.
5. **Sandbox vs. Production Feed endpoint** is confirmed with OEM1 (typically `api.` for sandbox, `feed.` for production). Set via `OEM1_FEED_HOST` environment variable on the connector ECS task.

### Deploy Commands

All commands run from the `deployment/` directory with `DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 AWS_PROFILE=default`:

```bash
cd ~/connected-mobility-guidance-on-aws/deployment

# 1. Seed vehicles from OEM1 enrollment APIs
make seed-vehicles-oem1 DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 AWS_PROFILE=default

# 2. Seed the transform manifest to S3
make seed-manifest-oem1 DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 AWS_PROFILE=default

# 3. Deploy the connector ECS service and run post-deploy smoke test
make deploy-connector-oem1 DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 AWS_PROFILE=default
```

### Expected Outcome

After successful deployment:
1. **Connector ECS service** (`cms-staging-oem1-connector`) enters `RUNNING` state with 1 task.
2. **CloudWatch dashboard** `cms-staging-oem1-connector` is populated with metrics:
   - Messages per minute by shard
   - Parse/transform error rates
   - Message age (modem timestamp vs. ingestion)
   - Token refresh count
   - GetFlow last-received age
3. **Canonical OEM1 messages** appear on MSK topic `cms-telemetry-preprocessed` with `oem_source=oem1` within 5 minutes of connector startup.
4. **Vehicles appear in DynamoDB** table `cms-staging-storage-vehicles` with `oem_source=oem1` and `last_seen_at` timestamps.

### Smoke Test

The post-deploy smoke test (embedded in `deploy_connector_oem1.sh`) performs:
- 60-second CloudWatch log tail on `cms-staging-oem1-connector` log group
- Exit code 1 if any line contains `ERROR` or `Traceback`
- Exit code 0 if clean

```bash
# Manual smoke test after deploy
aws logs tail /aws/ecs/cms-staging-oem1-connector --since 1m --follow \
  --log-stream-names $(aws ecs list-tasks --cluster cms-staging --service-name cms-staging-oem1-connector --region us-west-2 --query 'taskArns[0]' --output text | awk -F'/' '{print $NF}')
```

### Tear-Down

To remove the OEM1 connector stack from staging (destructive):

```bash
cd ~/connected-mobility-guidance-on-aws/deployment
cdk destroy ConnectorStack --force
```

To optionally clean up test vehicles from the DynamoDB table (CAUTION — will delete all OEM1 vehicles):

```bash
aws dynamodb scan \
  --table-name cms-staging-storage-vehicles \
  --filter-expression "oem_source = :s" \
  --expression-attribute-values '{":s":{"S":"oem1"}}' \
  --projection-expression "vehicleId" \
  --region us-west-2 \
  --query 'Items[].vehicleId.S' \
  --output text | \
xargs -I {} aws dynamodb delete-item \
  --table-name cms-staging-storage-vehicles \
  --key "{\"vehicleId\":{\"S\":\"{}\"}}" \
  --region us-west-2
```

### Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Connector task crashes with `401 Unauthorized` | Token supplier failed to authenticate with OEM1 | Verify secret `cms-staging-connector-oem1-credentials` in Secrets Manager is correct and token endpoint is reachable |
| Connector task crashes with `UNAVAILABLE` on `GetFlow` | Connector cannot reach OEM1 feed service | Verify VPC routing and security groups allow egress to OEM1 endpoint; confirm `OEM1_FEED_HOST` env var is correct |
| Connector task crashes with `UnknownTopicOrPartitionException` | MSK topic `cms-telemetry-oem` does not exist | Run `make configure-msk-topics DEPLOYMENT_STAGE=staging` to create the topic (pre-existing staging infrastructure gap; see portfolio backlog "OEM Flink topic gap") |
| Zero messages on `cms-telemetry-preprocessed` after 5 minutes | No OEM1 vehicles producing data on the flow; or connector not consuming from flow | Verify ≥5 vehicles on OEM1 staging flow; verify flow UUID in SSM parameter matches OEM1 value |

## Iterating on the UI quickly (~30s loop)

For UI-only changes (label edits, component tweaks, mock-data adjustments),
use `make ui-quick-deploy` instead of the full CDK round-trip:

```bash
DEPLOYMENT_STAGE=staging make -C deployment ui-quick-deploy
```

This runs `yarn build` → `aws s3 sync` → `aws cloudfront create-invalidation`
in ~30 seconds. **Do not** use this for infrastructure changes (auth,
S3 bucket policies, CloudFront config) — those still require
`make staging-deploy`.

Pre-sync safety: the target greps `build/` for internal hostnames
(`<internal-corp-domains>` (configured in `.publish-secrets-scan.yml`))
and refuses to deploy if any are found. If the scan trips, the most
common cause is a polluted `.env.local` that crept into the build
environment — move dev-only env vars to `.env.development.local`
(Vite skips it for production builds).

### iOS demo app (VSACompanion): rebuild cadence

The iOS Simulator demo used by `docs/runbooks/ios-connect-demo.md` is
distributed as a prebuilt, unsigned `.app` bundle so presenters can
install and launch it without opening Xcode or configuring a signing
team. Two scripts under `clients/ios/scripts/`:

- `build_ios_simulator_app.sh` — developer runs this after any change
  to Swift source or `Staging.xcconfig`. Produces
  `clients/ios/build/VSACompanion-ios-sim-<version>-<date>-<sha>.app.zip`
  in ~30-90 s. No code signing required (Simulator builds are
  unsigned). Fully non-interactive.
- `install_ios_sim_demo_app.sh` — presenter runs this before each demo.
  Boots a simulator if none is booted, unpacks the newest `.app.zip`,
  installs via `xcrun simctl install`, launches via
  `xcrun simctl launch`. Under 5 s if the simulator is already booted.

Cadence: rebuild only when iOS source or `Staging.xcconfig` changes.
The zip is reusable across as many demos as needed. Both scripts live
under `clients/ios/scripts/` (which is developer tooling, not part of
the deployed guidance) and produce artifacts under `clients/ios/build/`
(gitignored). See `docs/runbooks/ios-connect-demo.md` Step 2 for the
presenter-facing invocation.

## Post-deploy validation

After running `deploy-all` + `deploy-bedrock-agents` + `bootstrap-demo` (or `seed-all-demo-data`), use the **publish-gate validator** to confirm the deploy is healthy before sharing the environment, cutting a release tag, or running customer demos.

```bash
AWS_PROFILE=default AWS_REGION=us-west-2 DEPLOYMENT_STAGE=staging \
  bash deployment/scripts/validate_staging_publish_gate.sh
```

The script is read-only and idempotent — it never mutates AWS state, and is safe to re-run.

It runs 7 checks against the live deploy:

| # | Check | What it validates |
|---|---|---|
| 1 | CFN stack states | Every `cms-{stage}-*` stack is in a `*_COMPLETE` state (no rollback / failure / in-progress) |
| 2 | Flink apps RUNNING | All 9 `cms-{stage}-flink-*` Kinesis Analytics apps are `RUNNING` (not `UPDATING` or `STOPPING`) |
| 3 | Flink MSK auth correctness | Each Flink app's PropertyMap has the IAM auth keys (`bootstrap.servers`, `sasl.mechanism=AWS_MSK_IAM`, `sasl.jaas.config`, `sasl.client.callback.handler.class`) and no SCRAM residue (closes the v0.2.3 Flink CDK migration regression class) |
| 4 | fw-telemetry consuming | CloudWatch `numRecordsInPerSecond > 0` over the last 10 min, proving FWE → preprocessed → trip path |
| 5 | Trip materialization | A fresh trip row appears in `cms-{stage}-storage-trips` with `startTime` in the last 30 min |
| 6 | Auth-fix runtime gate | Every CMS API route that requires Cognito JWT returns 401/403 to anonymous callers (closes HackerOne 3775026 § Pattern A) |
| 7 | No critical errors | CloudWatch Logs grep for structured `ERROR`-classified entries in the trip-path Flink apps + auth-fix Lambdas over the last 10 min |

**Exit codes**: `0` = all 7 PASS, environment is publish-ready; `N` = number of failed checks.

**Note on Check 5**: trip materialization requires simulator traffic to PASS. On a fresh deploy with no simulator running, expect Check 5 to FAIL — start a simulator (see [Running the Fleet Simulator](../README.md#running-the-fleet-simulator) in the README) and re-run the validator.

**Note on Check 6**: this check tolerates absence of optional stacks — if `predictive-agent` is not deployed (`DEPLOY_PREDICTIVE_AGENT=true` not set), its routes are skipped rather than counted as failures.

## Flink pipeline scaling & alarms

The Flink domain-consumer tier (`cms-{stage}-flink-*` KDA apps) is horizontally scalable with a per-app parallelism dial + a CloudWatch alarm tier (shipped 2026-06-18, spec `2026-06-17-oem1-event-driven-pipeline-scale`):

- **Per-app parallelism dial** — `create_flink_app_config(..., parallelism=N)` in `deployment/stacks/flink_stack.py` (default `1`). The trip-processor runs `parallelism=3` to match the 3-partition `cms-telemetry-trips` source. Domain topics (`cms-telemetry-{processed,trips,safety,maintenance}`) are `vehicleId`-keyed at the sole producer (`EventDrivenTelemetryProcessor`), so `parallelism>1` preserves per-vehicle affinity (one vehicle → one partition → one subtask) **without** Flink `keyBy`/keyed-state. Raise another consumer's parallelism only when its `records_lag_max`/CPU warrants; keep `ParallelismConfiguration.ConfigurationType=CUSTOM` (a `DEFAULT` UPDATE with custom values is rejected by KDA).
- **Alarm tier** — standalone `aws_cloudwatch.Alarm` constructs (never inline `monitoring_configuration` — silent-drop trap) for {oem-telemetry, event-driven, trip, safety, maintenance, telemetry-data} on `records_lag_max`, `downtime`, `fullRestarts`, `numberOfFailedCheckpoints`, `containerCPUUtilization`, wired to the KMS-encrypted SNS topic `cms-{stage}-flink-alarms`.
- **⚠️ Subscriptions are NOT auto-created.** After deploy the alarm topic has zero subscribers → alarms fire but page nobody. An operator must subscribe an oncall endpoint per `docs/runbooks/oem1-pipeline-scale-cutover.md` § 6.
- **Scaling / cutover procedure** — `docs/runbooks/oem1-pipeline-scale-cutover.md`: JAR build from `main` → `cdk deploy cms-{stage}-flink` → consumer-group reset to `latest` → smoke (lag → 0 + fresh multi-point trip). Prod is a separately-gated step (§ 5).

## Publishing a new release to GitHub

For releasing a new sanitized version to the public mirror at
`aws-solutions-library-samples/guidance-for-connected-mobility-on-aws`,
see the dedicated runbook:

```
docs/PUBLISHING.md
```

Standard flow: tag with semver → push tag to GitLab → click "play"
on the `publish_to_github` manual CI job in GitLab. The job strips
internal-only paths and runs the secret scanner before force-pushing
to GitHub `main`.

## Flink prod deploy after config-keys fix

For the **first production deployment** after spec `2026-06-08-cms-flink-cfn-config-keys-fix` lands, follow the dedicated runbook: [Flink Prod Deploy After Config-Keys Fix](runbooks/flink-config-keys-prod-deploy.md).

This first deploy replaces runtime-override-populated state with CDK-source state in a single CloudFormation change-set. The runbook provides gated procedures for snapshotting current prod state, diffing against CDK source, and executing via change-set with operator review gates at each step. Subsequent prod Flink deployments after this spec are normal (`make deploy-prod` or equivalent).

## Future CI/CD

CMS has no automated CI/CD pipeline today — all deploys are manual via the `make` targets documented below.

The `.github/workflows/{deploy,evals}.yml` files in source are **design references** for the desired pipeline shape (validate → staging-deploy → tier3-eval → prod-deploy with approval gates, SHA-pinned actions, OIDC). They do not execute anywhere:
- GitHub Actions is intentionally not used. CMS's repo topology is GitLab = internal source-of-truth + CI home; GitHub = sanitized, versioned-release-only mirror. See `.kiro/specs/2026-05-26-cms-production-foundation/decisions.md` → "Repository topology".
- GitLab CI hosts only a manually-triggered `sync_to_github` mirror job today (paused while the publish-mirror flow is built — see issue `2026-05-26-public-mirror-leaked-ci-workflows`).

Porting the design to GitLab CI (`.gitlab-ci.yml` jobs targeting our internal AWS account) is the work of a follow-up spec. Until that lands, run staging/prod deploys manually as documented below.

## Pre-flight Checks

Always run pre-flight checks before any staging deployment. The `deployment/scripts/preflight-staging.sh` script verifies 10 prerequisites in read-only mode (~30s):

```bash
bash deployment/scripts/preflight-staging.sh
```

The script checks:

1. **AWS account** — Confirms you're logged into your staging account (the staging account).
2. **CDK bootstrap** — Verifies `CDKToolkit` stack exists in us-west-2. If missing, run `make -C deployment bootstrap-staging`.
3. **VPC quota** — Ensures ≥3 VPC slots available (CMS uses ~1 VPC; headroom for future). If low, delete unused VPCs or request quota increase.
4. **Bedrock model availability** — Tests that the current `BEDROCK_AGENT_MODEL` (default: `us.anthropic.claude-sonnet-4-6`) is invocable in us-west-2. If error says "Legacy model", update `BEDROCK_AGENT_MODEL` in `deployment/Makefile` to the current Sonnet version.
5. **Container builder** — Confirms a container builder (docker, finch, or podman) is running. CDK uses it for ECS image-asset builds (sim-service, fwe-agent) and Lambda asset bundling. See [Daemonless container builder](#daemonless-container-builder-finch--podman) below for non-Docker options.
6. **Node.js + Python versions** — Verifies Node 18+ and Python 3.9+ available.
7. **Python venv** — Checks `.venv/` exists and is activated.
8. **CMS_DEMO_DEFAULT_PASSWORD** — Ensures the env var is set (used to seed Cognito demo users).
9. **Git working tree** — Verifies no uncommitted changes (clean state required for reproducible CDK).
10. **CDK synth** — Runs a dry-run synth to catch structural errors early.

**Common fixes:**
- Bootstrap missing: `make -C deployment bootstrap-staging`
- VPC quota low: Increase via AWS Service Quotas console, or delete unused VPCs in us-west-2
- Bedrock model error "Legacy model… 30-day inactivity": The model needs to be re-enabled. Bump `BEDROCK_AGENT_MODEL` in `deployment/Makefile` to current Sonnet (check AWS Bedrock docs for the latest ID).
- Docker not running: Start Docker Desktop **or** use a daemonless drop-in (finch / podman) — see [Daemonless container builder](#daemonless-container-builder-finch--podman)
- Missing env var: `export CMS_DEMO_DEFAULT_PASSWORD='your-password'`

### Bedrock model-ID validation guardrail

`deployment/scripts/validate-bedrock-model.sh` is a pre-deploy guardrail that catches hallucinated, typo'd, or LEGACY Bedrock model IDs **before** CloudFormation/CDK touches the agent infrastructure. It is wired as a recipe step in both `make staging-deploy` and `make prod-deploy`, and is invoked from Check 4 of `preflight-staging.sh` and `preflight-prod.sh`.

**What it checks** (against the live AWS Bedrock catalog in the target region):

1. Looks up the model ID in `bedrock list-inference-profiles`. If found and `status==ACTIVE`, exits 0 with `[OK]`.
2. Falls back to `bedrock list-foundation-models`. If found:
   - `modelLifecycle.status==ACTIVE` → exits 0 with `[OK]`.
   - `modelLifecycle.status==LEGACY` → exits 0 with `[WARN]` (does **not** block the deploy — operators are notified to upgrade).
3. If neither catalog matches → exits 1 with `[FAIL]` and the deploy is aborted.

**When it runs:**

- Automatically as the first recipe step of `make staging-deploy` (after `config/staging.env` is sourced).
- Automatically as the first recipe step of `make prod-deploy` (after the `[y/N]` confirmation gate, before `deploy-all`).
- As Check 4a of both `preflight-staging.sh` and `preflight-prod.sh` (before the `bedrock-runtime invoke-model` live probe).

**Standalone invocation** (override knobs via env vars):

```bash
# Validate the Makefile default in us-east-1
MODEL_ID=us.anthropic.claude-sonnet-4-6 REGION=us-east-1 PROFILE=default \
  deployment/scripts/validate-bedrock-model.sh

# Or via the dedicated Make target
make -C deployment validate-bedrock-model \
  BEDROCK_AGENT_MODEL=us.anthropic.claude-sonnet-4-6 \
  AWS_REGION=us-east-1 AWS_PROFILE=default
```

**Exit codes:**

| Code | Meaning |
|------|---------|
| 0    | OK (inference profile ACTIVE, foundation model ACTIVE) |
| 0    | WARN (foundation model LEGACY — does **not** block deploy) |
| 1    | FAIL (model ID not found in catalog, or inference profile not ACTIVE) |
| 2    | Usage error (missing `MODEL_ID`, AWS CLI missing) |

The WARN-on-LEGACY (rather than FAIL-on-LEGACY) policy is intentional: it surfaces the divergence to operators without blocking emergency redeploys of the existing model when an upgrade is not yet safe.

### `cdk diff` environment-variable hygiene (architect-side)

Before running `cdk diff` from a developer workstation to inspect proposed
changes against staging or prod, ensure `CMS_DEMO_DEFAULT_PASSWORD` is set
to the **real** value (the one used to seed Cognito demo users on the
target deploy), not the synth-time placeholder `synth-placeholder-not-used`.

The placeholder ends up in synth output when the env var is unset, so
`cdk diff` then reports a spurious password-property change against the
deployed state. This is cosmetic only — `cdk diff` never deploys this
drift, since real deploys all run through the documented
`make staging-deploy` / `make prod-deploy` chains, which set the env var
explicitly. But it adds review noise.

Set the env var before running `cdk diff` and the diff output is clean:

```bash
export CMS_DEMO_DEFAULT_PASSWORD='<the-real-staging-password>'
cdk diff <stack-name>
```

Surfaced 2026-06-03 by FrontendBucket spec Cycle 2 review. Tracked at
backlog row "`cdk diff` env-var hygiene" (closed 2026-06-05 by this
addition).

### Staging edge auth gate — staging only

Staging sits behind an external SSO gate at the CloudFront edge.
Before any `cdk deploy cms-staging-ui` (or `make staging-deploy`),
confirm `deployment/cdk.context.json` has `stagingGateKeyGroupId` set:

```bash
python3 -c "import json; print(json.load(open('deployment/cdk.context.json'))['stagingGateKeyGroupId'])"
# expected: a CloudFront Key Group ID (e.g. abc12345-1234-5678-90ab-cdef12345678).
# If KeyError or "no such key": the gate is currently OFF. CDK will deploy
# the staging distribution WITHOUT the trusted-key-groups binding and
# print a [ui_stack] warning. See the internal staging-gate runbook to
# reinitialize.
```

If `stagingGateKeyGroupId` is missing, the deploy still succeeds but
staging is unprotected. Operator MUST follow the internal staging-gate
runbook end-to-end before the gate can be considered active. The
runbook is internal-only and is not shipped to the public mirror (it
is excluded via `.publish-exclude`).

### UI custom domain — and the cross-region guard

The CloudFront distribution behind `cms-{stage}-ui` can attach an
operator-owned custom domain (CNAME) when both keys are set in
`deployment/cdk.context.json`:

```jsonc
{
  "uiCustomDomain":          "staging.example.com",
  "uiCustomDomainCertArn":   "arn:aws:acm:us-east-1:<account>:certificate/<id>",
  "uiCustomDomainManageDns": false,             // optional; default true. set false for delegated zones.
  "uiCustomDomainRegion":    "us-west-2"        // RECOMMENDED. see "Cross-region guard" below.
}
```

The cert MUST live in `us-east-1` (CloudFront requirement) and must
already be ISSUED. Without the pair, the distribution behaves exactly
as before: CloudFront default cert, default `<dist-id>.cloudfront.net`
URL only.

**Cross-region guard (`uiCustomDomainRegion`)**: CloudFront aliases
(CNAMEs) are partition-global — the same alias cannot be attached to
two distributions in two regions. A manual deploy of `cms-staging-ui`
to a second region (e.g., harness-driven Tokyo clean-deploy) reading
the same `cdk.context.json` would inherit the primary region's
`uiCustomDomain` and trigger a CloudFront `409 CNAMEAlreadyExists`
against the home-region distribution. The 2026-06-15 Tokyo deploy
broke on exactly this; see
[`issues/2026-06-15-cms-xregion-ui-domain-guard/`](../issues/2026-06-15-cms-xregion-ui-domain-guard/).

When `uiCustomDomainRegion` is set AND it does NOT equal
`Stack.of(self).region`, the UI stack will SKIP attaching the domain,
the certificate, the Route53 A-record alias, and the
`CustomDomainURL` CFN output. The distribution falls back to the
default `*.cloudfront.net` URL in that region. A clear stderr
warning is emitted naming both the configured home region and the
active stack region:

```
  [ui_stack] uiCustomDomainRegion='us-west-2' != Stack.region='ap-northeast-1' for cms-staging-ui;
    SKIPPING custom domain attachment (domain='staging.example.com'). ...
```

Recommended values per stage:

| Stage   | `uiCustomDomainRegion` | Home distribution            |
| ------- | ---------------------- | ---------------------------- |
| staging | `us-west-2`            | (your home CloudFront)       |
| prod    | `us-east-1`            | (prod CloudFront)            |

When `uiCustomDomainRegion` is UNSET, behavior is exactly as today
(region-agnostic attach when the pair is set). This preserves
us-west-2 staging + us-east-1 prod deploys byte-for-byte and is the
backward-compatible default — but operators who may deploy the same
stage to a second region (clean-deploy harness, second-region
disaster-recovery probes, secondary-region bring-up) are STRONGLY
advised to add `uiCustomDomainRegion` to their persisted staging
context. Your organization-specific staging-gate runbook should set
this as part of the standard staging context-persistence path.

Verifying the guard fired in a synth:

```bash
DEPLOYMENT_STAGE=staging AWS_REGION=ap-northeast-1 \
CMS_DEMO_DEFAULT_PASSWORD=dummy \
cdk synth cms-staging-ui \
  -c uiCustomDomain=staging.example.com \
  -c uiCustomDomainCertArn=arn:aws:acm:us-east-1:<account>:certificate/<id> \
  -c uiCustomDomainRegion=us-west-2 \
  -c uiCustomDomainManageDns=false \
  -o /tmp/cdk-out 2>&1 | grep ui_stack
# expected: "[ui_stack] uiCustomDomainRegion='us-west-2' != Stack.region='ap-northeast-1' ... SKIPPING ..."

python3 -c "
import json
t = json.load(open('/tmp/cdk-out/cms-staging-ui.template.json'))
dist = next(v for v in t['Resources'].values() if v.get('Type')=='AWS::CloudFront::Distribution')
print('Aliases =', dist['Properties']['DistributionConfig'].get('Aliases', '<absent>'))
"
# expected: Aliases = <absent>
```

### Environment variables and `cdk` subprocesses

`make staging-deploy` and standalone `cdk deploy` invocations run the CDK CLI as a child process, which means env vars must be **exported** (or inlined on the command line) to be visible to CDK's Python entry point. Sourcing a plain `KEY=value` env file with `. config/staging.env` (no `export`) makes the values available to the current shell only — CDK reads `os.environ` in a fresh subprocess and sees nothing.

Symptoms when this is wrong:

- `ui_stack` raises `ValueError: CMS_DEMO_DEFAULT_PASSWORD must be set`
- Demo login buttons missing in the deployed bundle (the Vite gate in `build-ui` short-circuits)
- `cms-staging-fleetwise` stack skipped because `DEPLOY_FLEETWISE` is unset
- Wrong region/account used because `AWS_REGION` / `DEPLOYMENT_STAGE` defaulted

**Canonical inline-prefix pattern** (recommended for one-shot invocations):

```bash
DEPLOYMENT_STAGE=staging \
AWS_REGION=us-west-2 \
CMS_DEMO_DEFAULT_PASSWORD='your-staging-password' \
DEPLOY_FLEETWISE=true \
DEPLOY_SIMULATION=true \
cdk deploy cms-staging-<stack> --require-approval never --profile default
```

Each `KEY=value` prefix on the same line as the command is exported into the child process's environment automatically — no explicit `export` needed.

**Alternative: `set -a` env-file sourcing** (for repeated commands in the same shell):

```bash
set -a              # mark all subsequent assignments for export
. config/staging.env
set +a              # restore default behavior

cdk deploy ...      # now sees every var from staging.env
make staging-deploy # ditto
```

`set -a` (a.k.a. `set -o allexport`) makes every assignment until `set +a` exported automatically, so a plain `KEY=value` env file behaves as if every line was `export KEY=value`.

**What does NOT work:**

```bash
. config/staging.env       # vars in shell only, NOT in subprocess
cdk deploy ...             # cdk sees os.environ without staging.env values
```

If you cannot edit `config/staging.env` to add `export` keywords (e.g., it is shared with another tool), use one of the two patterns above.

## Storage stack bucket naming convention

Captured 2026-06-03 from spec `2026-06-03-cms-storage-bucket-region-suffix/`.

S3 bucket names are GLOBALLY unique. Any CDK-declared bucket whose
physical name does not include a region/account suffix WILL collide
on deploy if the same `cms-{stage}` is ever deployed in two regions
of the same account (this surfaced as a clean-deploy harness
`BucketAlreadyExists` failure on 2026-06-03 against
`ap-northeast-1` while the live staging deploy held the bucket in
`us-west-2`).

### Required pattern

Globally-named buckets in `deployment/stacks/*.py` MUST suffix the
physical name with `-{self.region}-{self.account}`:

```python
# Wrong — collides cross-region in the same account
self.invoice_bucket = s3.Bucket(
    self, "ServiceInvoiceBucket",
    bucket_name=f"{construct_id}-service-invoices",
    ...
)

# Right — region+account suffix prevents global-namespace collision
self.invoice_bucket = s3.Bucket(
    self, "ServiceInvoiceBucket",
    bucket_name=f"{construct_id}-service-invoices-{self.region}-{self.account}",
    ...
)
```

This matches the existing convention in
`deployment/stacks/data_processing_stack.py:215` for
`cms-{stage}-transform-manifests-{region}-{account}`.

### Current buckets and their pattern

| Bucket | Stack | Suffix? |
|---|---|---|
| `cms-{stage}-storage-service-invoices-{region}-{account}` | `storage_stack.py:118` | ✓ (post 2026-06-03) |
| `cms-{stage}-transform-manifests-{region}-{account}` | `data_processing_stack.py:215` | ✓ |
| `cms-{stage}-storage-datalakebucket*` (CDK auto-name) | `storage_stack.py` | ✓ — CDK auto-name includes stack hash |
| `cms-{stage}-vfo-knowledge-base-{region}-{account}` | `bedrock_agents_stack.py:279-292` | ✓ (post 2026-06-04, spec `2026-06-04-cms-vfo-kb-bucket-region-suffix`, commit `5d97a41`) |

### Renaming a globally-named bucket (post-deploy of new pattern)

CFN treats `BucketName` as an immutable property; changing it requires
a resource replacement. The migration plan tested on 2026-06-03:

1. **Confirm bucket is empty** (or sync data to the new bucket first):
   ```bash
   aws s3 ls --summarize --recursive s3://<old-bucket-name> --region <region>
   aws s3api list-object-versions --bucket <old-bucket-name> --region <region>
   ```
   If non-empty: HALT, plan a sync step before deploy.
2. **Confirm `RemovalPolicy.RETAIN`** is set on the bucket (so CFN
   replaces, doesn't delete during update).
3. **Run `cdk diff`** to confirm exactly the BucketName property
   change is queued:
   ```bash
   cd deployment && \
     DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 CDK_DEFAULT_ACCOUNT=<account> \
     cdk diff cms-staging-storage --no-cli-pager
   ```
4. **Deploy non-interactively**:
   ```bash
   cd deployment && \
     DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 CDK_DEFAULT_ACCOUNT=<account> \
     cdk deploy cms-staging-storage --require-approval never --no-cli-pager 2>&1 | \
     tee ~/.cms/storage-bucket-rename/<run-id>/deploy-staging.log
   aws cloudformation wait stack-update-complete --stack-name cms-staging-storage --region us-west-2
   ```
5. **Post-deploy smoke check** (per `~/.kiro/steering/deploy-validation.md`):
   ```bash
   NEW_BUCKET=cms-staging-storage-service-invoices-us-west-2-<account>
   aws s3api head-bucket --bucket "$NEW_BUCKET" --region us-west-2
   aws s3api get-bucket-encryption --bucket "$NEW_BUCKET" --region us-west-2
   aws s3api get-bucket-versioning --bucket "$NEW_BUCKET" --region us-west-2
   aws s3api get-public-access-block --bucket "$NEW_BUCKET" --region us-west-2
   aws s3api get-bucket-lifecycle-configuration --bucket "$NEW_BUCKET" --region us-west-2
   # Confirm CFN export reflects new value:
   aws cloudformation describe-stacks --stack-name cms-staging-storage --region us-west-2 \
     --query "Stacks[0].Outputs[?OutputKey=='ServiceInvoiceBucketName'].OutputValue" --output text
   # Confirm old bucket is reachable as orphan (RETAIN honored):
   aws s3api head-bucket --bucket cms-staging-storage-service-invoices --region us-west-2
   # CloudWatch error smoke (5–10 min):
   aws logs filter-log-events --log-group-name "/aws/lambda/cms-staging-<lambda-name>" \
     --filter-pattern '"NoSuchBucket"' --region us-west-2 \
     --start-time $(python3 -c "import time; print(int((time.time()-300)*1000))")
   ```
   Non-zero exit on any failed check.
6. **Cleanup of the orphaned old bucket** — ONLY after the new
   bucket is confirmed healthy AND the old bucket re-confirmed empty:
   ```bash
   aws s3api delete-bucket --bucket cms-staging-storage-service-invoices --region us-west-2
   aws s3api head-bucket --bucket cms-staging-storage-service-invoices --region us-west-2
   # expected: 404 / NoSuchBucket
   ```

### Rollback

If a hidden consumer surfaces post-deploy (e.g., `NoSuchBucket` errors
in CloudWatch), prefer **fix-forward**: identify the consumer, point
it at the new bucket name, redeploy. A `git revert` is NOT a clean
rollback because the orphaned old bucket exists and CFN cannot
re-adopt it without manual `cdk import`. See
`.kiro/specs/2026-06-03-cms-storage-bucket-region-suffix/spec.md`
"Rollback path" for the full decision matrix.

## UI stack bucket naming convention

Same architectural defect class as storage-stack. The
`FrontendBucket` declared at `deployment/stacks/ui_stack.py:494-505`
historically used a 3-tier name resolution (per-stack context pin →
legacy global pin → `{construct_id}-frontend-{account}-{timestamp}`
fallback). The fallback omitted region; pinned context keys were
region-agnostic; cross-region deploys collided on S3's global
namespace.

Resolved 2026-06-03 by spec
`.kiro/specs/2026-06-03-cms-ui-frontend-bucket-region-suffix/`
(commit `d2eef32`).

### Required pattern

```python
# After (ui_stack.py post-spec)
self.frontend_bucket = s3.Bucket(
    self, "FrontendBucket",
    bucket_name=f"{construct_id}-frontend-{self.account}-{self.region}",
    block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
    public_read_access=False
)
```

The deterministic `(construct_id, account, region)` tuple guarantees
global uniqueness; the timestamp + pin-back operator workflow is
removed. RemovalPolicy is left as CDK's `s3.Bucket` default
(`RETAIN`) so a future rename produces an orphan-and-replace
migration consistent with storage-stack.

### Current bucket inventory (post-rename)

| Bucket physical name | Defined in | Suffixed `-{account}-{region}`? |
|---|---|---|
| `cms-staging-ui-frontend-123456789012-us-west-2` | `ui_stack.py:498` | ✓ (post 2026-06-03) |
| `cms-prod-ui-frontend-123456789012-us-east-1` (PENDING) | `ui_stack.py:498` | ⚠ — code change shipped; **next operator-initiated `cdk deploy cms-prod-ui` will trigger REPLACEMENT** of the live prod FrontendBucket (`cms-prod-ui-frontend-123456789012-1777830107`). User-driven timing. |

### Renaming a UI FrontendBucket (run for prod when ready)

Same shape as the storage-stack runbook above, with FrontendBucket
choreography:

1. **Pre-deploy guards**:
   ```bash
   # Confirm staging UAT smoke (or prod-equivalent) is steady
   curl -sSf -o /dev/null -w '%{http_code}\n' https://staging.YOUR-CLOUDFRONT-DOMAIN.example.com
   # Confirm stack is in a stable state
   aws cloudformation describe-stacks --stack-name cms-prod-ui --region us-east-1 \
     --query 'Stacks[0].StackStatus' --output text
   ```

2. **`cdk diff cms-{stage}-ui`** — confirm the FrontendBucket
   replacement cascade (BucketName property change, BucketPolicy
   replacement, OAC name regen, Distribution origin update,
   FrontendDeployment custom-resource invocation). HALT if scope
   exceeds expectations beyond already-merged-but-pending resources.

3. **Deploy**:
   ```bash
   export RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)
   mkdir -p ~/.cms/ui-bucket-rename/${RUN_ID}
   cd deployment && source .venv/bin/activate && \
     DEPLOYMENT_STAGE=prod AWS_REGION=us-east-1 \
     cdk deploy cms-prod-ui --require-approval never --exclusively --no-cli-pager \
     2>&1 | tee ~/.cms/ui-bucket-rename/${RUN_ID}/deploy-prod.log
   aws cloudformation wait stack-update-complete --stack-name cms-prod-ui --region us-east-1
   ```

   **Expected duration**: ~2-15 min (dominated by CloudFront origin
   update propagation; observed 2 min on staging on 2026-06-03).

4. **Post-deploy validation** (smoke test per
   `~/.kiro/steering/deploy-validation.md`):

   ```bash
   NEW_BUCKET="cms-prod-ui-frontend-123456789012-us-east-1"
   aws s3api head-bucket --bucket "$NEW_BUCKET" --region us-east-1
   aws s3api get-public-access-block --bucket "$NEW_BUCKET" --region us-east-1
   aws s3 ls s3://${NEW_BUCKET}/ --recursive --summarize --region us-east-1 | tail -3
   aws s3api head-object --bucket "$NEW_BUCKET" --key runtimeConfig.json --region us-east-1
   # 60s sleep then CloudFront URL poll x2 (replace with prod-equivalent URL)
   sleep 60
   # CloudWatch NoSuchBucket scan across cms-prod-ui-* Lambda log groups (last 5 min)
   ```

5. **Cleanup of orphaned old bucket**:
   ```bash
   OLD_BUCKET="cms-prod-ui-frontend-123456789012-1777830107"
   # Drain UI assets (versioning OFF on FrontendBucket, so no version-list step)
   aws s3 rm s3://${OLD_BUCKET}/ --recursive --region us-east-1
   aws s3api delete-bucket --bucket "$OLD_BUCKET" --region us-east-1
   sleep 5
   aws s3api head-bucket --bucket "$OLD_BUCKET" --region us-east-1  # expect 404
   ```

### Staging-deploy precedent (2026-06-03 evidence)

The staging deploy of this convention (run-id `20260603T194307Z`) was
near-trivial because operator action had already deleted the
`cms-staging-ui-frontend-123456789012-1780338216` bucket prior to the
deploy (CFN had drift: stack template referenced a name that S3 said
didn't exist). The CFN REPLACEMENT created the new bucket and ended
the drift in a single operation; the FrontendDeployment uploaded UI
assets (21 objects, ~30 MB); CloudFront origin auto-repointed; total
duration ~2 min. CloudFront URL pre/post-deploy: 403 (auth-gated;
steady state — actual broken-origin state pre-deploy was invisible to
UAT because the gate fronts the response).

### Rollback

Same posture as storage-stack: prefer fix-forward. UI assets are a
build artifact, not the source of truth, so even a `git revert` +
re-deploy will produce a fresh upload to whatever bucket the construct
declares — no data loss risk. The orphaned old bucket (if any) is
cleanup-pending, not blocking.

## Deploy Commands

### Staging (us-west-2)

```bash
export CMS_DEMO_DEFAULT_PASSWORD='your-staging-password'
bash deployment/scripts/preflight-staging.sh  # ~30s, read-only
make -C deployment staging-deploy            # ~45 min, real AWS resources
```

**Expected outcome:**
- All 12 CMS staging stacks (`data-processing`, `storage`, `iot`, `ui`, `msk`, `telemetry-integration`, `flink`, `fleetwise`, `simulation`, `commands`, `ws-fanout`, `tco`) in `CREATE_COMPLETE` or `UPDATE_COMPLETE` state in us-west-2
- CloudWatch logs show no `ERROR` or `Traceback` in the last 2 minutes
- Estimated daily cost while running: ~$50–$150/day (MSK ~$15–$30, Flink ~$10–$20, 3x NAT Gateway ~$4, rest negligible)

**Deploy ordering and cautions** (per spec `2026-06-18-cms-fwe-decoder-manifest-bucket-resolution`):
- **FWE decoder manifest is now stack-managed:** the manifest (`DecoderManifest.bin`) is committed at `deployment/fwe-config/DecoderManifest.bin` and deployed into the Flink jar bucket via CDK `BucketDeployment` on every `cms-<stage>-flink` deploy. To regenerate: `DRY_RUN=1 python3 deployment/scripts/generate_decoder_manifest.py` to validate, then remove `DRY_RUN=1` to commit the new `.bin`, then commit to git. **Do NOT use out-of-band manual uploads** — they cause bucket drift (the 2026-06-18 staging outage).
- **Deploy ordering:** always deploy `cms-staging-flink` (carries the manifest and Java processor changes) **before** `cms-staging-simulation` (uses the same manifest). If deploying with FleetWise enabled, also deploy `cms-staging-fleetwise` with `DEPLOY_FLEETWISE=true` before or with `cms-staging-simulation` to ensure the FWE decoder is available to agents before they launch.
- **ASG no-reset:** the `FweASG` in `cms-staging-simulation` no longer has an explicit `desired_capacity`, so `cdk deploy cms-staging-simulation` will NOT reset the ASG and bounce agents. Live desired count is managed by ECS managed scaling (min=1, max=3).
- **FWE sim + agent co-location:** simulator and agent tasks now co-locate on the same EC2 instance via ECS placement constraint (same vcan bus = same host). If placing the simulator fails (agent instance full), the Lambda logs a retry-able error and does NOT silently separate them to different instances.
- **UI domain alias warning:** do NOT `cdk deploy cms-staging-ui` without the domain context set in `cdk.context.json` (keys `uiCustomDomain`, `uiCustomDomainCertArn`). Omitting both keys drops the staging alias and CloudFront reverts to the default `*.cloudfront.net` URL, breaking the external staging gate. See § "UI custom domain — and the cross-region guard" below for the full context.

**Eval-user stack is provisioned automatically** as part of `make deploy-all` (and therefore `make staging-deploy`). The chain runs the dedicated `deploy-eval-user` Makefile target after `phase-services`; on non-staging stages it prints a skip line and is a no-op (the stack itself raises if `DEPLOYMENT_STAGE != 'staging'`, and the Make target is `ifeq`-gated so prod-deploy never invokes `cdk deploy` for it).

After staging-deploy completes you should see:

```
🔐 Deploying cms-staging-eval-user (Tier-3 eval pipeline user)
✅  cms-staging-eval-user
Promoting cms-eval-runner password to permanent...
✅ Eval-user password promoted to permanent
✅ Eval-user stack deployed and password promoted to permanent
```

Cognito does not allow setting permanent passwords at create time via CloudFormation, so the post-deploy `aws cognito-idp admin-set-user-password --permanent` call is owned by the `set-eval-user-password` target. It is idempotent (PUT semantics) — safe to re-run on every deploy. The auto-generated Secrets Manager value (`cms-staging-eval-runner-password`) is the source of truth.

To re-promote the password manually after a secret rotation or out-of-band reset, run:

```bash
cd deployment && make set-eval-user-password \
  DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 AWS_PROFILE=default
```

Tier 3 evals will authenticate cleanly. The eval runner does NOT need AWS credentials — it uses the public app client ID via `cognito-idp:initiate-auth`.

### Post-deploy WebSocket smoke test (staging)

The staging UI WebSocket API (`cms-staging-ui-ws`, route `/live`) is critical for the iOS companion app and live-state dashboards. Confirm it works after every staging deploy — the iOS UAT session 2026-05-28 surfaced an IAM gap (issue `2026-05-28-cms-staging-ws-502-iam-gap`) that returned HTTP 502 silently for 2 days because nobody backend-tested the WS Lambda.

```bash
WS_LOG_GROUP=/aws/lambda/cms-staging-ui-WSHandler1D31D9FC-m4XzbMJs3ZoV
START_MS=$(python3 -c "import time; print(int((time.time()-120)*1000))")

# After 60s of normal traffic, the WS Lambda should have zero ERROR/Traceback events.
sleep 60
aws logs filter-log-events \
  --log-group-name "$WS_LOG_GROUP" \
  --start-time "$START_MS" \
  --filter-pattern '?ERROR ?Traceback ?AccessDenied' \
  --region us-west-2 --limit 5 \
  --query 'events[].message' --output text
# expected: empty output. Anything else = the Lambda is failing — investigate before declaring deploy successful.
```

If the deploy modified `cms-staging-ui` resources, also confirm the existence of the DynamoDB grants on the WS handler role (CDK should always synth them; this is a guardrail against IAM drift):

```bash
aws iam get-role-policy --role-name $(aws iam list-roles \
  --query 'Roles[?starts_with(RoleName,`cms-staging-ui-WSHandlerServiceRole`)].RoleName | [0]' --output text) \
  --policy-name $(aws iam list-role-policies --role-name $(aws iam list-roles \
    --query 'Roles[?starts_with(RoleName,`cms-staging-ui-WSHandlerServiceRole`)].RoleName | [0]' --output text) \
    --query 'PolicyNames[0]' --output text) \
  --query 'PolicyDocument.Statement[?Action[?contains(@,`dynamodb:PutItem`)]]' --output table
# expected: one row with the dynamodb actions on the cms-staging-storage-ws-connections table.
```

### Driver and VSA Cognito seeding (staging)

After a fresh staging deploy (or whenever drivers/Cognito need to be re-seeded
to match the current vehicle fleet), follow this sequence. Established
2026-05-30 by spec
`.kiro/specs/2026-05-29-staging-drivers-simulator-cognito-parity/`.

**Prereqs:**

- VSA user pool `us-west-2_YOUR_POOL_ID` exists in account `123456789012`,
  us-west-2. The pool is provisioned externally to CMS (in CVX); CMS only
  consumes it via CDK context `vsaUserPoolId` (see `deployment/cdk.json`).
- The pool's three required custom attributes (`custom:driverId`,
  `custom:tenantId`, `custom:vehicleId`) all exist by name. `custom:driverId`
  and `custom:tenantId` are **immutable** — see "Operational rule: VSA pool
  immutability" below.
- `cms-staging-storage-vehicles` table is non-empty. The seed script
  scales `NUM_DRIVERS` to `vehicle_count + ceil(0.20 * vehicle_count)`
  (active + 20% bench).

**Commands (run in order):**

```bash
# 1. (Optional) Audit phantom drivers from any prior simulator runs.
#    Phantoms match the legacy `^DRV-\d{10}-[A-Z0-9]{4}$` pattern that the
#    pre-2026-05-29 simulator produced when the drivers table was empty.
DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \
  python3 deployment/scripts/cleanup_phantom_drivers.py --dry-run

# 2. (If step 1 reported phantoms) delete them.
DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \
  python3 deployment/scripts/cleanup_phantom_drivers.py --apply

# 3. Seed driver records into `cms-staging-storage-drivers`. Vehicle-aware
#    mode draws `assignedVehicleId` from the real vehicles table (no
#    `VEH-NNNN` synthetic IDs). Idempotent for a fixed RANDOM_SEED.
make -C deployment seed-drivers \
  DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2

# 4. Sync active drivers into the VSA pool. The Makefile target reads
#    `vsaUserPoolId` from `deployment/cdk.json` and exports it as
#    `VSA_USER_POOL_ID` for the seed script.
make -C deployment seed-driver-users \
  DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2

# 5. (Optional) Reconcile any pre-existing trips against the new
#    drivers table. Always dry-run first.
DRIVERS_TABLE=cms-staging-storage-drivers \
TRIPS_TABLE=cms-staging-storage-trips \
SAFETY_EVENTS_TABLE=cms-staging-storage-safety-events \
AWS_REGION=us-west-2 \
  python3 deployment/scripts/reconcile_trip_driver_ids.py --all --dry-run

# 6. (If step 5's diff is reasonable) apply the reconciliation.
DRIVERS_TABLE=cms-staging-storage-drivers \
TRIPS_TABLE=cms-staging-storage-trips \
SAFETY_EVENTS_TABLE=cms-staging-storage-safety-events \
AWS_REGION=us-west-2 \
  python3 deployment/scripts/reconcile_trip_driver_ids.py --all
```

**Verify each step:**

```bash
# After step 3 — driver count should equal NUM_DRIVERS target
aws dynamodb scan --table-name cms-staging-storage-drivers --select COUNT \
  --region us-west-2 --query 'Count'

# After step 3 — every active driver has assignedVehicleId pointing
# at a real staging vehicle
aws dynamodb scan --table-name cms-staging-storage-drivers \
  --projection-expression 'driverId,assignedVehicleId,#s' \
  --expression-attribute-names '{"#s":"status"}' \
  --region us-west-2 --output table

# After step 4 — Cognito user count ≥ active driver count
aws cognito-idp list-users --user-pool-id us-west-2_YOUR_POOL_ID \
  --region us-west-2 --query 'Users | length(@)'

# After step 4 — sample one user's custom attributes
aws cognito-idp admin-get-user --user-pool-id us-west-2_YOUR_POOL_ID \
  --username <email> --region us-west-2 \
  --query 'UserAttributes[?starts_with(Name,`custom:`)]'
```

**Operational rule: VSA pool immutability.** `custom:driverId` and
`custom:tenantId` are immutable on the staging pool (see decisions log in
`.kiro/specs/2026-05-29-staging-drivers-simulator-cognito-parity/decisions.md`,
"Decision A — Option 3 accepted"). The seed script populates these
attributes on user **creation** only; in-place updates of these fields are
not possible. If a driver's `driverId` mapping changes (rare — would
require both a `RANDOM_SEED` change and a vehicle-pool change), the
operator must **delete the affected pool user manually first**, then re-run
`make seed-driver-users`. `custom:vehicleId` is mutable and supports
in-place driver-swaps-vehicle.

**Troubleshooting:**

- **`Invalid length for parameter UserPoolId, value: 0`** from the
  seed-driver-users script or from the CMS UI account-status Lambda →
  `VSA_USER_POOL_ID` env var is empty. Confirm `deployment/cdk.json`
  context block contains `"vsaUserPoolId": "us-west-2_YOUR_POOL_ID"`. After
  edits to `cdk.json`, re-deploy `cms-staging-ui` to refresh the Lambda's
  environment.
- **Schema mismatch on the VSA pool** (custom-attribute missing or wrong
  mutability) → file an issue. The pool is owned externally to CMS.
  Mutability cannot be flipped in-place; resolution is either
  add-mutable-companion-attributes or delete-and-recreate the pool. Both
  are out of scope for the standard seed flow.
- **Empty vehicles table** → `seed-drivers` falls back to its synthetic
  `VEH-NNNN` pool (with a warning) and the Cognito sync becomes
  meaningless because `custom:vehicleId` won't match any real vehicle.
  Run `make seed-vehicles DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2`
  first.

### Demo fleet seeding (generic vs customer-tenant)

`make seed-all-demo-data` runs `seed-generic-fleets` as the very first
step in the chain. It writes a small obviously-synthetic fleet hierarchy
to `cms-{stage}-storage-fleets`, `cms-{stage}-storage-vehicles`, and
`cms-{stage}-storage-fleet-enrollment` so the UI renders fleet pages on
first load and `tests/e2e/test_clean_deploy.py::test_S6` (≥ 1 row in
each of those tables) sees the expected demo content after a clean
deploy.

The default seeder ships ~3 fleets and ~18 vehicles with non-branded
names (e.g., `Demo Logistics Co.`, `Reference Fleet Demo`,
`Sample Fleet Operations`) and synthetic make/model strings (e.g.,
`DemoMotors Voyager 1000`, `AcmeAuto Hauler 350`). Vehicle and fleet
IDs use the distinct `FLT-DEMO-*` / `VEH-DEMO-*` prefixes so the seed
co-exists cleanly with any customer-tenant fleet seed scripts on
internal staging without ConditionExpression collisions. The script
mirrors the shape of any internal customer-specific fleet seeder
(`seed_engineering_fleets.py` is the model: same DDB tables, same
`put_fleet` / `put_vehicle` / `put_enrollment` helpers, same idempotent
ConditionExpression pattern, customer-specific brand and IDs only).

Customers may replace `seed_generic_fleets.py` with their own
customer-tenant fleet seed; the generic version is the public-mirror
default. Two common adoption patterns:

1. **Replace in place** — edit `deployment/scripts/seed_generic_fleets.py`
   to ship customer fleets/vehicles. Re-running `make seed-generic-fleets`
   is idempotent (ConditionExpression on first writes; `--force` flag for
   explicit overwrite). Best for customers who don't need to keep the
   generic content.
2. **Add a sibling seeder** — author a new
   `deployment/scripts/seed_<customer>_fleets.py` mirroring the
   shape of `seed_generic_fleets.py`, add a Makefile target
   (`seed-<customer>-fleets`), and chain it into `seed-all-demo-data`
   alongside the generic seeder. Use distinct ID prefixes (the generic
   seeder reserves `FLT-DEMO-*` and `VEH-DEMO-*`). Best for customers
   who want both demo and customer-tenant content side-by-side, or who
   need to keep customer content out of public-mirror'd source via
   `.publish-exclude`.

**Run manually (idempotent):**

```bash
DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \
  python3 deployment/scripts/seed_generic_fleets.py --dry-run

DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \
  python3 deployment/scripts/seed_generic_fleets.py
```

**Verify after seed:**

```bash
aws dynamodb scan --table-name cms-staging-storage-fleets \
  --region us-west-2 --select COUNT --query 'Count'   # → 3 (or more if other fleet seeders ran)
aws dynamodb scan --table-name cms-staging-storage-vehicles \
  --region us-west-2 --select COUNT --query 'Count'   # → ≥ 18
aws dynamodb scan --table-name cms-staging-storage-fleet-enrollment \
  --region us-west-2 --select COUNT --query 'Count'   # → ≥ 18
```

### Prod (us-east-1)

Prod deploys are manual and require stricter approval:

```bash
export CMS_DEMO_DEFAULT_PASSWORD='your-prod-password'
make -C deployment prod-deploy  # Prompts for confirmation unless CI=true
```

On laptop: you'll be prompted to confirm. In CI, set `CI=true` to skip the prompt (this is forward-looking — when GitLab CI is wired up).

**Key differences from staging:**
- Deploys to `us-east-1` instead of us-west-2
- Uses a separate IAM role scoped to us-east-1 (provisioned when GitLab CI lands)
- Manual approval required (today: human running `make prod-deploy` is the gate; future: GitLab CI environment protection)
- Tier 3 eval suite runs as a smoke-test only (subset of cases, tighter latency budgets)

### Tear Down

```bash
make -C deployment tear-down-staging  # Prompts: type 'destroy-staging' to confirm
```

Takes ~15 min. Fully idempotent — safe to re-run. Deletes all staging resources, releasing all cost.

**Reminder:** This is irreversible. Confirm before running.

## Clean-deploy integration test

**What it is**: an operator-triggered, end-to-end harness that
performs a first-time deployment into a fresh AWS region, runs setup-
layer + telemetry assertions against the deployed stacks, and then
tears the region back down. The harness is **not** a CI gate — it is
operator-run before promoting a CMS architecture change to a new
region or before publishing a new release tag.

**Initiative**: `.kiro/specs/2026-06-01-clean-deploy-integration-tests/`
(spec, tasks, decisions). The harness is **demo-app scope**, not a
production integration test.

### Prereqs

- AWS account with:
  - Credentials in the standard chain (env vars / shared credentials
    / IAM role) with permissions for CloudFormation, S3, IoT Core,
    MSK, Kinesis Analytics for Apache Flink, Cognito, ECS Fargate,
    Location Service, Bedrock, Bedrock Agents, Secrets Manager.
  - Service quotas at or above the floors documented in
    [`docs/tech.md` § Clean-Deploy Region Verification](./tech.md#clean-deploy-region-verification-ap-northeast-1)
    (MSK brokers ≥ 3, EIPs ≥ 5, Lambda concurrency ≥ 200, ECS vCPU
    ≥ 16, Cognito pools ≥ 1, Bedrock invocation TPM ≥ 1000).
- AWS CLI v2 installed and `aws sts get-caller-identity` returns the
  expected staging account.
- Node.js + AWS CDK installed (CDK bootstrap is performed by the
  harness on first run; idempotent on warm runs).
- Python 3.11+ with the project's `deployment/.venv` activated and
  `tests/e2e/.venv` provisioned (auto-created on first run).
- **`CMS_DEMO_DEFAULT_PASSWORD` exported** in the shell before invoking
  the harness. `deployment/stacks/ui_stack.py:~1239` reads this env
  var to seed the staging Cognito demo user (`FleetManager@example.com`)
  and raises `ValueError` at synth time if unset, which crashes
  `cdk bootstrap` with `Subprocess exited with error 1` and short-
  circuits every downstream phase. The orchestrator's `preflight_env`
  phase fails loudly with a clear actionable error when this is
  missing — but only after sourcing `clean-deploy.env`, so set it
  in your shell first:
  ```bash
  export CMS_DEMO_DEFAULT_PASSWORD='<your-staging-password>'
  ```
  Pull from your secret store / 1password / vault. Do NOT commit a
  literal value to `clean-deploy.env` — it is operator-supplied per
  `~/.kiro/steering/secrets-handling.md`. See
  `issues/2026-06-03-clean-deploy-env-var-gate/` for the gap that
  motivated the preflight gate.
- The target region is **clean** — no leftover `cms-staging-*`
  CloudFormation stacks, MSK clusters, Cognito user pools, Bedrock
  agents, agent aliases, or knowledge bases. The harness's
  `audit_region_orphans.py` enumerates these; the harness's
  `preflight_region_clean.py --strict` blocks deploys into a dirty
  region.
- **Default region**: `ap-northeast-1`. Override per-run with
  `make clean-deploy-test REGION=<code>`. Sourcing
  `deployment/config/clean-deploy.env` resolves the default.

### Deploy commands

```bash
# Default — first-time deploy into ap-northeast-1, run all tests,
# tear down, audit, emit report.json.
make -C deployment clean-deploy-test

# Override region (any AWS region with Bedrock + the dependency
# stack supported — see docs/tech.md § Service-availability matrix).
make -C deployment clean-deploy-test REGION=eu-west-2
```

The Makefile target sources `deployment/config/clean-deploy.env`,
forwards `REGION=<code>` to the orchestrator
(`deployment/scripts/run_clean_deploy_test.sh`), and runs the harness
to completion. **Trap-driven teardown ensures a failed phase still
runs `teardown_region_force.py` + `audit_region_orphans.py` on EXIT**,
so a botched run does not leave residual `cms-staging-*` resources
in the target region.

Run logs and artefacts land under `~/.cms/clean-deploy/<run-id>/`:

| Path | Contents |
|---|---|
| `<phase>.log` | Stdout+stderr of each orchestrator phase (one file per phase) |
| `report.json` | Per-phase verdict map (PASS / FAIL / SKIP), final aggregate verdict |
| `audit.json` | Region-orphan audit report (zero on a clean teardown) |
| `flink-logs.txt` | Last 10 min of CloudWatch logs from `cms-{stage}-flink-*` log groups (best-effort) |

### Expected outcome

A successful run emits `~/.cms/clean-deploy/<run-id>/report.json` with
PASS for every in-scope phase. Phase list, in order:

| Phase | Description | Failure mode |
|---|---|---|
| `preflight_per_region` | Service availability + quota check + Bedrock inference-profile resolution. Emits `BEDROCK_INFERENCE_PROFILE_ID` for downstream phases. | FAIL — surfaces the failing service / quota / profile lookup. |
| `preflight_env` | Validates required operator-supplied env vars (currently `CMS_DEMO_DEFAULT_PASSWORD`) before `cdk bootstrap` walks `app.py`. | FAIL — required env var unset; error names the var and how to set it. |
| `bootstrap_region` | `cdk bootstrap aws://<account>/<region>`. Idempotent. | FAIL — bootstrap CFN stack errored (rare; usually IAM). |
| `bootstrap_us_east_1` | No-op in v1 default mode (UI uses `*.cloudfront.net`, no us-east-1 cert needed). Recorded as SKIP. | SKIP — counts as PASS for aggregate verdict. |
| `preflight_strict` | `preflight_region_clean.py --strict` against the (now-bootstrapped) region. Surfaces residual `cms-staging-*` resources. | FAIL — residual stacks / agents / knowledge bases / pools blocking a fresh deploy. |
| `deploy_all` | `make deploy-all` (12 CMS stacks). | FAIL — any stack stuck in CREATE_FAILED. |
| `deploy_bedrock_agents` | `make deploy-bedrock-agents` with the resolved profile in env. | FAIL — Bedrock agent CFN stack errored. |
| `seed_demo_data` | `make seed-all-demo-data` + bedrock-agents KB content seed. | FAIL — seed script crashed; usually a missing CFN output. |
| `tests_e2e` | `pytest tests/e2e/test_clean_deploy.py -m e2e` — 14 setup-layer assertions (S1–S14) + 1 telemetry assertion (`test_trip_materializes`). | FAIL — pytest non-zero exit; per-test detail in `tests_e2e.log`. |
| `teardown` (trap) | `teardown_region_force.py --region <code> --stage staging`. Always runs. | FAIL — teardown surfaced a stuck delete (rare). |
| `audit` (trap) | `audit_region_orphans.py --region <code> --stage staging --report-path ~/.cms/clean-deploy/<run-id>/audit.json`. Always runs. | FAIL — region not clean after teardown; check `audit.json` for the residual resource. |

The aggregate verdict in `report.json` is **PASS only when every
non-SKIP phase is PASS**. Per-test S1–S14 detail is in
`tests_e2e.log`.

### Smoke test

The smoke test for this harness is the harness itself — `tests_e2e`
phase runs the 15 deployed-infra assertions:

| Assertion | What it checks | Backed by |
|---|---|---|
| S1 | Cognito user pool exists with `cms-{stage}-*` name | `cognito-idp.list_user_pools` |
| S2 | ≥ 1 driver user in pool with permanent password | `cognito-idp.list_users` |
| S3 | Driver auth round-trip: `USER_PASSWORD_AUTH` returns IdToken | `cognito-idp.initiate_auth` |
| S4 | UI runtime config endpoint returns 200 with required keys | `requests.get(${cf_url}/runtimeConfig.json)` |
| S5 | Eval-user authenticates via `ADMIN_USER_PASSWORD_AUTH` | `cognito-idp.admin_initiate_auth` + Secrets Manager |
| S6 | Demo data seeded: signal/event catalogs + fleets/vehicles/drivers row floors | `dynamodb.scan(Select=COUNT)` |
| S7 | Decoder manifest in S3 (`<flink-bucket>/fwe-config/DecoderManifest.bin`) | `s3.head_object` |
| S8 | FleetWise CAN campaign + safety templates exist | `dynamodb.scan(cms-{stage}-campaigns)` |
| S9 | IoT device policy + `cms_{stage}_iot_*` topic rules present | `iot.list_policies` + `iot.list_topic_rules` |
| S10 | MSK cluster ACTIVE | `kafka.describe_cluster` |
| S11 | All `cms-{stage}-flink-*` Kinesis Analytics apps RUNNING | `kinesisanalyticsv2.describe_application` |
| S12 | Bedrock agents deployed: cost / maintenance / rebalancing / recall-warranty / virtual-fleet-operator | `bedrock-agent.list_agents` |
| S13 | VFO knowledge-base S3 prefixes have ≥ 1 object each (S13.a only — Decision A) | `s3.list_objects_v2` |
| S14 | Resolved Bedrock inference profile is callable (one-token Converse) | `bedrock-runtime.converse(maxTokens=1)` |
| `test_trip_materializes` | Simulated vehicle trip materializes in `cms-{stage}-storage-trips` DDB within 10 min | Simulation API + `dynamodb.query(vehicleId-index)` |

Per `decisions.md` 2026-06-02 Decision A, S13 is implemented as
**S13.a only** (S3-presence check). The S13.b end-to-end agent-runtime
probe is deferred to v1.1.

### Tear-down

The orchestrator's EXIT trap **always** runs the teardown phase, even
on a phase failure. The trap:

1. Fires `teardown_region_force.py --region <code> --stage staging`.
2. Fires `audit_region_orphans.py --region <code> --stage staging
   --report-path ~/.cms/clean-deploy/<run-id>/audit.json`.
3. Emits `report.json` with the per-phase verdict map.

**Operator-disk state isolation:** the harness automatically isolates from
your operator-persisted `deployment/cdk.context.json` by relocating it before
the deploy and restoring it on exit (via the trap, regardless of exit reason).
This prevents cross-region resource collisions caused by persisted domain names
or certificate ARNs from prior deploys. For full details, see
`docs/RUNBOOK_clean_region_deploy.md` § Operator-disk state isolation.

Manual teardown (in case the trap was somehow bypassed — e.g.,
`SIGKILL`):

```bash
cd ~/connected-mobility-guidance-on-aws
deployment/.venv/bin/python deployment/scripts/teardown_region_force.py \
  --region <code> --stage staging

deployment/.venv/bin/python deployment/scripts/audit_region_orphans.py \
  --region <code> --stage staging \
  --report-path /tmp/manual-audit.json
```

`teardown_region_force.py` is read-only by default (dry-run); pass
no flags for the destructive path. `audit_region_orphans.py` is
always read-only.

### Troubleshooting

**`preflight_per_region` FAIL — Bedrock inference profile not found**:
the pinned model (`us.anthropic.claude-sonnet-4-6`) does not have a
SYSTEM_DEFINED profile in the target region. Check
[`docs/tech.md` § Bedrock SYSTEM_DEFINED inference-profile resolution](./tech.md#1-bedrock-system_defined-inference-profile-resolution)
for the per-region resolution decision tree. Either pick a region
where the profile exists (any of: ap-northeast-1, us-east-1,
us-west-2, eu-west-1) or extend the spec's resolution decision tree
to add a fallback profile for the new region.

**`preflight_strict` FAIL after `bootstrap_region` PASS**: the region
has residual `cms-staging-*` resources from a prior failed run. Read
the strict-mode output for the resource list. Common culprits:
- A `cms-staging-*` CFN stack stuck in `DELETE_FAILED` (manually
  delete via `aws cloudformation delete-stack --stack-name
  <name> --retain-resources <stuck-resource>`).
- Bedrock agents or knowledge bases left over from a prior bedrock-
  agents deploy. The `--strict` mode now enumerates these (per
  Group 2.3); delete via the AWS console or `bedrock-agent` CLI.
- An MSK cluster stuck in `DELETING` state — wait it out (~10 min)
  or escalate.

**`deploy_all` FAIL on first stack**: usually a missing CDK bootstrap
prerequisite (rare after Group 3.1's reordering — the harness now
runs bootstrap before the strict gate). Check `deploy_all.log` for
the failing stack name and CFN error.

**`tests_e2e` FAIL on `test_S14`**: `BEDROCK_INFERENCE_PROFILE_ID` is
either unset or malformed. The orchestrator's
`preflight_per_region.py --emit-env` populates it; if the test fails,
re-run `preflight_per_region.py --region <code> --emit-env` directly
and inspect the output.

**`tests_e2e` FAIL on `test_trip_materializes`**: the simulated trip
did not materialize in `cms-{stage}-storage-trips` within 10 min.
Check `flink-logs.txt` for Flink job errors; check the simulation
API status endpoint for the simulation_id. If the simulation never
emitted ignition-off (R6 risk), the polling loop times out cleanly.

**`audit` reports orphans after `teardown` PASS**: a teardown step
returned PASS but the audit found residuals. Most common cause:
CloudFront distributions in `delete-pending` state — the audit allows
a 20-min grace window. Re-run `audit_region_orphans.py` after 20 min;
if orphans persist, manually delete via the AWS console.

**Operator-triggered, NOT a CI gate.** This harness is run before
release-tag promotion or before validating a new region target. Per
PRD decision #2, it is not wired into the CI pipeline; that
guarantees fresh-region cost is operator-controlled.

## Tier 3 Eval Pipeline

CMS uses a single tier of integration tests (Tier 3) that validate deployed REST/WebSocket endpoints end-to-end.

### Architecture

- `evals/runner/tier3_e2e.py` — pytest-parameterized runner, auto-skips if `STAGE_ENDPOINT` unset
- `evals/runner/reporter.py` — ported from CVX, generates regression reports (Type A: passed→failed, Type B: latency regress, Type C: tool sequence diverge)
- `evals/cases/e2e/*.yaml` — ≥5 test cases (REST + WebSocket), validated against JSON schema
- `evals/baselines/tier3.json` — golden baseline captured after fresh staging deploy in Group 5
- `evals/conftest.py` — pytest fixtures for JWT auth, endpoint resolution

### Running Locally

1. **Set environment variables:**

```bash
export STAGE_ENDPOINT='https://<api-id>.execute-api.us-west-2.amazonaws.com/prod/'
export STAGE_ENDPOINT_WSS='wss://<ws-api-id>.execute-api.us-west-2.amazonaws.com/live'
export CMS_EVAL_USERNAME='cms-eval-runner@example.invalid'  # email-as-username pool
export CMS_EVAL_PASSWORD='<from AWS Secrets Manager — see step 2>'
export COGNITO_CLIENT_ID='xxxxxxxxxxxxxxxxxxxxxxxxxx'
export AWS_REGION='us-west-2'
```

Note: the eval runner uses Cognito's non-admin `initiate_auth` endpoint and only needs the public app client ID (not the user pool ID, not AWS credentials).

2. **Capture values from staging deploy:**

```bash
# After `make staging-deploy` + cdk deploy cms-staging-eval-user complete:
STACK_NAME=cms-staging-ui
aws cloudformation describe-stacks --stack-name $STACK_NAME --region us-west-2 \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text

# STAGE_ENDPOINT (REST API):
aws cloudformation describe-stacks --stack-name $STACK_NAME --region us-west-2 \
  --query 'Stacks[0].Outputs[?OutputKey==`APIEndpoint`].OutputValue' --output text

# STAGE_ENDPOINT_WSS (WebSocket API):
aws cloudformation describe-stacks --stack-name $STACK_NAME --region us-west-2 \
  --query 'Stacks[0].Outputs[?OutputKey==`WebSocketEndpoint`].OutputValue' --output text

# Eval user password (auto-generated 24-char string, plain text — not JSON):
aws secretsmanager get-secret-value --secret-id cms-staging-eval-runner-password \
  --region us-west-2 --query SecretString --output text
```

3. **Run the eval suite:**

```bash
# Activate venv
source .venv/bin/activate

# Run all Tier 3 cases (expects all to pass)
.venv/bin/python -m evals.runner._run_tier --tier 3 --output /tmp/eval-tier3.json

# Generate report
.venv/bin/python -m evals.runner.reporter --tier 3 \
  --results /tmp/eval-tier3.json \
  --baseline evals/baselines/tier3.json
```

### Interpreting the Report

The reporter generates a Markdown summary table with three regression types:

- **Type A (failed)**: Case passed in baseline, failed in current run → blocker, investigate immediately
- **Type B (latency)**: Case p99 latency increased >20% vs baseline → potential performance degradation
- **Type C (diverge)**: Tool sequence or response structure changed → check if intentional

Example report:

```
## Regression Summary
| Type | Count | Severity |
|------|-------|----------|
| Failed (A) | 0 | CRITICAL |
| Latency (B) | 1 | WARNING |
| Diverge (C) | 0 | INFO |

Total: 1 warning (1 latency regression)
├─ rest-vehicles-list (p99: 1800ms → 2400ms, +33%)
│  └─ Still within budget (2500ms); monitor on next run
```

### Updating the Baseline

After a successful deploy, regenerate the baseline if all cases pass:

```bash
source .venv/bin/activate
.venv/bin/python -m evals.runner._run_tier --tier 3 --output /tmp/eval-tier3.json
cp /tmp/eval-tier3.json evals/baselines/tier3.json
git add evals/baselines/tier3.json
```

This captures the current results as the new golden baseline. Commit to git.

**When to update vs investigate:**
- Update if: all cases pass, latency increases are explained (e.g., higher fleet size in demo data)
- Investigate if: any Type A failures, or Type B latency >30% regression

## Triaging Failures

### `staging-deploy` fails

1. **Pre-flight** → Run `bash deployment/scripts/preflight-staging.sh` first. Fix any blockers.
2. **Stack failure event** → Check CloudFormation console:
   ```bash
   aws cloudformation describe-stack-events --stack-name cms-staging-<stack-name> \
     --region us-west-2 --query 'StackEvents[0:5]'
   ```
3. **Lambda/ECS logs** → Check CloudWatch for the failed service:
   ```bash
   aws logs tail /aws/lambda/cms-staging-<function> --region us-west-2 --follow
   ```
4. **Common failures:**
   - **`ui_stack` rollback with "No export named cms-prod-bedrock-agents-…"** → `deployment/cdk.context.json` (gitignored, per-developer state) is polluted with prod-specific overrides like `bedrockAgentsStackName`, `uiCustomDomain`, `uiCustomDomainCertArn` from a prior prod-targeted deploy. CDK reads these as defaults for any context not explicitly passed. Surgically remove the offending keys with a small Python snippet (`json.load → ctx.pop('bedrockAgentsStackName', None) → json.dump`) and re-run staging-deploy. The deploy is idempotent. **Note:** the clean-deploy integration test harness automatically isolates from this state — see `docs/RUNBOOK_clean_region_deploy.md` § Operator-disk state isolation and `~/.kiro/specs/2026-06-03-cms-clean-deploy-context-isolation/spec.md` for the full architecture.
   - **`cms-staging-<stack>` in `ROLLBACK_COMPLETE`** → CDK won't overwrite a stack in this terminal state. Delete it first: `aws cloudformation delete-stack --stack-name cms-staging-<stack> --region us-west-2 && aws cloudformation wait stack-delete-complete …`. Then re-run staging-deploy.
   - **`bedrock_agents` stack** → Bedrock model not invocable. Check `BEDROCK_AGENT_MODEL` in Makefile; may need to bump to current Sonnet.
   - **`msk` stack** → VPC quota exhausted. Request quota increase or delete unused VPCs.
   - **`ui_stack` ValueError on synth** → `CMS_DEMO_DEFAULT_PASSWORD` not set. Export the env var: `export CMS_DEMO_DEFAULT_PASSWORD='...'`.
   - **`fleetwise` stack** → `DEPLOY_FLEETWISE` flag not set. Add to staging.env or manually: `make deploy-fleetwise DEPLOYMENT_STAGE=staging`.

### Tier 3 eval suite fails

1. **Check endpoint reachable:**
   ```bash
   curl -I $STAGE_ENDPOINT/api/v1/health
   ```
   Should return HTTP 200. If 404, stack not fully deployed yet; wait 2–3 min.

2. **Check JWT acquisition:**
   ```bash
   python3 -c "from evals.runner._auth import get_jwt; print(get_jwt('$STAGE_ENDPOINT', '$CMS_EVAL_USERNAME', '$CMS_EVAL_PASSWORD'))"
   ```
   Should print a JWT (eyJ... prefix). If auth fails, check Cognito user pool exists and eval user is provisioned.

3. **Single case vs all cases:**
   - If only 1–2 cases fail: likely endpoint-specific issue (missing data, misconfigured route)
   - If all cases fail: likely auth/connectivity issue (invalid endpoint, JWT broken, security group blocking)

4. **WebSocket cases only fail:**
   - Check `STAGE_ENDPOINT_WSS` format: `wss://` prefix, not `https://`
   - Test: `python3 -c "import websockets; websockets.sync.client.connect('$STAGE_ENDPOINT_WSS')" 2>&1 | head -5`

### Prod deploy differs from staging

1. **Verify AWS credentials** (the human running `make prod-deploy`):
   - You must be authenticated to your prod account with permissions to deploy to us-east-1
   - `aws sts get-caller-identity` should match the expected prod-deploy identity
   - `aws-vault` or equivalent is recommended to scope the prod credentials to the deploy session

2. **Regional differences:**
   - Some services may not be available in us-east-1 (Bedrock models, Connect)
   - Check CloudFormation stack events for service-specific errors

3. **Stack name prefix:**
   - Staging: `cms-staging-*`
   - Prod: `cms-prod-*`
   - If seeing wrong prefix, check `DEPLOYMENT_STAGE` env var is set correctly

## Simulation deployment

The simulation service (`cms-{stage}-simulation` stack) deploys two ARM64 container images for the simulator and FleetWise Edge agent. By default, `make deploy-simulation` pulls pre-built images from public ECR — **no local container builder required**.

### Default deployment (published images)

```bash
make -C deployment deploy-simulation DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2
```

This pulls:
- `public.ecr.aws/o0q5e8r2/cms-sim-service:<version>` (simulator service)
- `public.ecr.aws/o0q5e8r2/cms-fwe-agent:<version>` (FleetWise Edge agent)

Version is pinned by `SIM_IMAGE_VERSION` in `deployment/stacks/_sim_image_config.py` (currently v0.2.6). The deployment works in any region without additional infrastructure or builders.

### Custom image registry (operator override)

To use images from your own ECR or custom registry:

```bash
PUBLIC_ECR_REGISTRY=<your-registry> PUBLIC_ECR_TAG=<your-tag> \
  make -C deployment deploy-simulation DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2
```

Example: `PUBLIC_ECR_REGISTRY=123456789012.dkr.ecr.us-west-2.amazonaws.com PUBLIC_ECR_TAG=v1.2.3` points to your private registry and tag.

### Local development (custom builds)

For active development of `services/simulation/Dockerfile` or `Dockerfile.fwe`, use local container builds:

```bash
# Requires a local container builder (docker, finch, or podman)
SIM_IMAGE_MODE=asset CDK_DOCKER=finch make -C deployment deploy-simulation \
  DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2
```

See [Daemonless container builder (finch / podman)](#daemonless-container-builder-finch--podman) below for setup options.

### Publishing images to public ECR (maintainer only)

To publish new simulation images to the AWS Solutions public ECR registry (release-only, requires `ecr-public:*` permissions in us-east-1):

```bash
# Prerequisite: ensure the public ECR repos exist
# Create: public.ecr.aws/o0q5e8r2/cms-sim-service
#         public.ecr.aws/o0q5e8r2/cms-fwe-agent
# (manual one-time action per CMS public-mirror setup)

# Then publish (stages build contexts, scans for secrets, builds ARM64, logs in to ECR Public, pushes)
make -C deployment publish-public-ecr VERSION=v0.2.7
```

The publisher:
1. Stages `services/simulation/` into `deployment/ecr/cms-sim-service/` and `deployment/ecr/cms-fwe-agent/`
2. Scans layers for secrets/canaries (`.publish-secrets-scan.yml`)
3. Builds both images with `--platform linux/arm64`
4. Logs into ECR Public (us-east-1 only)
5. Tags and pushes `public.ecr.aws/o0q5e8r2/<name>:VERSION`
6. Prunes untagged images

**Manual prerequisites for first-time publish:**
- Create the two public ECR repositories (`cms-sim-service` and `cms-fwe-agent`) under `public.ecr.aws/o0q5e8r2/`
- Ensure the publishing role holds `ecr-public:*` permissions in us-east-1
- Version must follow semver format (e.g., `v0.2.7`)

### Daemonless container builder (finch / podman)

CMS deploys two ARM64 container images via CDK image assets at synth/deploy time
(`deployment/stacks/simulation_stack.py` — sim-service ~line 56, fwe-agent
~line 249, both `ecs.ContainerImage.from_asset(..., platform=ecr_assets.Platform.LINUX_ARM64)`).
CDK shells out to a local container builder named by the `CDK_DOCKER` env var
(default: `docker`). You do **not** need Docker Desktop — CDK officially supports
[Finch](https://runfinch.com) (AWS-supported, rootless, Apache-2.0, no licensing)
and Podman (community-tested) as drop-in replacements.

Reference: <https://docs.aws.amazon.com/cdk/v2/guide/build-containers.html>
(AWS CDK v2 Developer Guide → "Build and deploy container image assets in CDK apps").

**On macOS arm64 (Apple Silicon) with Finch:**

```bash
# One-time install
brew install --cask finch
finch vm init           # downloads the Finch VM image
finch vm start          # starts the rootless build VM

# Per-shell (or add to ~/.zshrc / ~/.bashrc)
export CDK_DOCKER=finch

# Now run any deploy that builds an image asset
make deploy-simulation DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2
# or
make deploy-all DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2
```

The `check-docker` Makefile prereq detects `CDK_DOCKER` and validates the
chosen executable (`finch info`); the `deploy-simulation` recipe inherits
`CDK_DOCKER` from your shell env, so CDK invokes `finch build` for the
ARM64 images. ARM64 cross-build is native on Apple Silicon (no QEMU emulation),
so build times match docker.

**With Podman:**

```bash
brew install podman
podman machine init
podman machine start
export CDK_DOCKER=podman
# Podman additionally requires DOCKER_HOST to point at its socket:
export DOCKER_HOST=$(podman machine inspect --format 'unix://{{.ConnectionInfo.PodmanSocket.Path}}')

make deploy-simulation DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2
```

**Verification — confirm the right builder is being used:**

```bash
make -C deployment check-docker
# ✅ Container builder 'finch' is ready (CDK_DOCKER override)
```

If `make check-docker` reports `docker not available, but 'finch' is ready`,
you have finch installed but `CDK_DOCKER` isn't set — re-run with
`CDK_DOCKER=finch make ...` or `export CDK_DOCKER=finch` in your shell rc.

**Notes & limitations:**

- CDK does not check which Docker replacement you use; any executable that
  satisfies the docker CLI surface (`info`, `build`, `push`, `tag`, `inspect`)
  works. Both finch and podman are docker-CLI-compatible.
- BuildKit-specific flags (e.g. `DOCKER_BUILDKIT=0` workaround documented
  below) are docker-only. Finch uses nerdctl+containerd; podman uses Buildah.
  Neither needs the BuildKit workaround for `public.ecr.aws/ubuntu/ubuntu`.
- For **prod** deploys via CI/CD, prefer remote-build alternatives (CodeBuild,
  GitHub Actions, etc.) rather than depending on a local builder at all —
  see the open initiative for a `from_ecr_repository` migration.



### Docker daemon / BuildKit auth issues (Apple Silicon)

> **If Docker Desktop is unstable or BuildKit auth fails, prefer the
> [daemonless drop-in](#daemonless-container-builder-finch--podman)
> (Finch / Podman) over the Docker Desktop recovery steps below.**

These bite during any deploy that builds a Docker image asset (`cms-staging-simulation`, `cms-staging-data-processing`, anything with a `DockerImageAsset`/`PythonFunction`).

**Symptom 1: Docker Desktop crashes on launch**

Tray error like:
```
opening tray: starting electron: sending file descriptors: broken pipe
```
in `~/Library/Containers/com.docker.docker/Data/log/host/com.docker.backend.log.*`.

**Recovery**: switch to Colima with the `vz` (Virtualization.framework) vmType. It's much faster on Apple Silicon than qemu emulation and avoids the Electron tray bug entirely.

```bash
brew install qemu     # only needed once; satisfies the colima dep chain
colima delete -f
colima start --vm-type vz --cpu 4 --memory 8 --disk 60
docker info >/dev/null   # sanity
```

The first start persists `vmType: vz` to `~/.colima/default/colima.yaml`, so subsequent `colima start` invocations pick the right vmType automatically.

**Symptom 2: `docker pull` of `public.ecr.aws/...` returns 403 inside `docker build` even after a successful `docker login public.ecr.aws`**

```
ERROR: failed to solve: public.ecr.aws/ubuntu/ubuntu:22.04: failed to do request: ... 403 Forbidden
```

Affects only BuildKit's metadata `HEAD` request path. A direct `docker pull public.ecr.aws/ubuntu/ubuntu:22.04` succeeds with the same auth.

**Workaround**: disable BuildKit for this deploy.

```bash
DOCKER_BUILDKIT=0 \
  DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \
  CMS_DEMO_DEFAULT_PASSWORD='Demo-1234' \
  DEPLOY_FLEETWISE=true DEPLOY_SIMULATION=true \
  cdk deploy cms-staging-simulation --require-approval never
```

The legacy builder finds the locally-cached image and proceeds. Subsequent BuildKit-enabled builds work once the layer is cached locally.

**Long-term fix**: switch `services/simulation/Dockerfile.fwe`'s `FROM public.ecr.aws/ubuntu/ubuntu:22.04` to either an ECR-private mirror or `ubuntu:22.04` from Docker Hub. Both eliminate the auth dance. Tracked in the backlog.

## Cost & Tear Down

### Cost Reference

Running staging deployment 24/7 incurs:

| Component | Daily Cost |
|-----------|-----------|
| Amazon MSK | ~$15–$30 |
| Kinesis Data Analytics (Flink) | ~$10–$20 |
| NAT Gateway (3x) | ~$4 |
| DynamoDB (on-demand, idle) | <$1 |
| ElastiCache Redis | ~$1 |
| IoT Core, API Gateway, Lambda | ~$1–$2 |
| **Total (24/7)** | **~$50–$150/day** |

**Monthly estimate if left running:** ~$1500–$4500/month

**Recommendation:** Tear down staging when not actively developing. Deploy only when needed (pre-demo, regression testing, etc.).

### Tear Down Procedure

```bash
make -C deployment tear-down-staging
```

When prompted: type `destroy-staging` to confirm (forces the destructive action).

**What happens:**
- All 18 CMS stacks deleted from us-west-2
- DynamoDB tables dropped (data lost permanently)
- MSK cluster terminated
- All associated resources (security groups, VPCs, IAM roles, etc.) removed
- Cost drops to $0 immediately

**Idempotency:** Safe to re-run. If stacks already deleted, command exits cleanly.

## Adding a New Eval Case

Tier 3 cases are YAML files in `evals/cases/e2e/*.yaml`. To add a new case:

1. **Copy an existing case** (e.g., `rest-health-check.yaml`) and rename:
   ```bash
   cp evals/cases/e2e/rest-health-check.yaml evals/cases/e2e/rest-fleets-list.yaml
   ```

2. **Edit the new file** and update:
   - `id`: Unique case identifier (e.g., `rest-fleets-list-001`)
   - `description`: What the case tests
   - `input.path`: API endpoint path (must start with `/api/v1/` or `/ws/`)
   - `input.method`: HTTP method (GET, POST, etc.) for REST cases
   - `input.subscribe`: Subscribe payload for WebSocket cases
   - `expected.status_code`: Expected HTTP status for REST
   - `expected.events.min_count`: Expected min events for WebSocket
   - `latency_budget_ms`: Max acceptable latency (default 5000ms for REST, 12000ms for WebSocket)

3. **Validate the case:**
   ```bash
   python3 -c "
   import yaml
   from evals.runner.schema import EvalCase
   with open('evals/cases/e2e/rest-fleets-list.yaml') as f:
     case = EvalCase.model_validate(yaml.safe_load(f))
   print(f'✓ Case {case.id} valid')
   "
   ```

4. **Commit and test:**
   ```bash
   git add evals/cases/e2e/rest-fleets-list.yaml
   ```
   Next time you run Tier 3, the new case is auto-collected and tested.

## Forward Work (Spec 2 & 3)

This spec establishes the foundation. Planned carry-over items:

### Spec 2: CMS Observability + Broader Tests

- **Tier 1 evals**: Lambda handler unit tests (pytest)
- **Tier 2 evals**: Workflow integration tests (e.g., end-to-end vehicle telemetry pipeline)
- **Tier 3 WebSocket eval**: Case 04 (`vehicle-live-state-stream`) is currently `KNOWN-FAILING` — server returns HTTP 400 because the eval runner does not yet substitute query params (`?fleetId=&token=<jwt>`) into the WebSocket URL. Add `ws_query_params` to the EvalCase schema, wire substitution into `_run_websocket`, and audit the `$connect` Lambda's exact auth contract.
- **Structured logging**: Adopt JSON-structured logs (replace print statements)
- **CloudWatch dashboards**: Fleet overview, per-vehicle health, Flink processor metrics, MSK topic lag
- **Runbooks**: Incident response (high latency, data loss, Flink restart procedures)
- **Makefile targets**: Add `eval-tier3` and `eval-update-baseline` targets for easier eval invocation

### Spec 3: CMS Tech Debt

- **Dependency scans**: `npm audit` (CMS UI has 14 CRITICAL/HIGH vulns in vite, serve, ajv), `pip-audit` (Python)
- **Code quality**: eslint, TypeScript strict mode, pylint
- **Customer sanitization**: Acme Motors references in frontend code (currently placeholder `Acme Motors` in mock data, but needs frontend label cleanup)
- **IAM least privilege**: Current deploy roles use `PowerUserAccess`; tighten to minimum required
- **Two-reviewer approval**: Prod deployments require two GitHub approvers
- **Post-deploy automation**: Admin user password reset runbook (currently manual)

### Publish-Mirror Flow (Separate Spec)

When ready to publish CMS to public GitHub mirror:
- `.publish-exclude` must strip all files flagged in `docs/SECURITY-AUDIT-FINDINGS.md` "Public mirror strip targets" section
- Secrets scanner config (expanded patterns from audit) runs pre-publish
- Commit hashes + signatures verified

---

## Simulation lifecycle

The CMS simulator service uses **two distinct ECS task families** with different lifetimes. Understanding the pairing model is essential when debugging missing telemetry, mis-routed CAN traffic, or "phantom frame loss" symptoms.

### The pairing model

| Task family                | Lifetime       | Purpose                                                                 |
|----------------------------|----------------|-------------------------------------------------------------------------|
| `cms-{stage}-fwe-agent`    | **Persistent** | One per VIN. Runs the AWS IoT FleetWise Edge agent. Owns one vcan device (`vcan0`, `vcan1`, …) on the host. Receives CAN frames, decodes them per the FleetWise decoder manifest, publishes to MQTT. |
| `cms-{stage}-fwe-simulator`| **Ephemeral**  | One per trip. Reads the paired agent's `CAN_BUS0` from ECS containerOverrides, writes simulated CAN frames to that vcan device, exits when the trip is complete. |

A simulator task without a running agent for the same VIN produces zero telemetry — no agent means no decoder means no MQTT publish.

### Lookup mechanism (`simulation_lambda.py`)

When `_start(config)` is called for `mode=fwe`:

1. **`_check_running_tasks(vin)`** scans the cluster for tasks where:
   - `taskDefinitionArn` contains the literal string `"fwe-agent"`, AND
   - `lastStatus` is `RUNNING`/`PENDING`/`PROVISIONING`, AND
   - `containerOverrides[*].environment.VEHICLE_NAME` starts with the requested VIN.

   Returns the agent's `taskArn`, or `None`. **Simulator tasks for the same VIN are intentionally ignored** — they may carry stale `CAN_BUS0` overrides from a previous run.

2. **If an agent was found**: `_resolve_agent_vcan(task_arn)` reads the agent's `CAN_BUS0` env var via `ecs.describe_tasks(...)`. On any failure mode (throttle, missing overrides, missing env var) it raises `ValueError` and `_start` returns HTTP 500 with a diagnostic. **There is no silent fallback to `vcan0`** — historically that fallback caused simulators to write to a vcan device no agent was listening on, producing zero-frame trips with no log signal.

3. **If no agent was found**: `_next_vcan_index()` reserves the lowest unused vcan number, then `ecs.run_task(cms-{stage}-fwe-agent, env={CAN_BUS0: vcanN, …})` starts a new persistent agent. The simulator task is then started with `CAN_BUS0=vcanN` to match.

4. **The DDB row** for the simulation persists both `taskArn` (simulator) and `agentTaskArn` (agent) so `_stop(sim_id)` can release the agent at trip-end. Without this, agents leak — one orphan per unique VIN ever simulated, each holding a vcan slot.

### How to verify the right vcan is being used

When telemetry looks wrong, **never** read raw `/ecs/cms-{stage}/fwe-agent` CloudWatch logs without first identifying which task ARN they belong to — multiple agents can be running concurrently and their log streams interleave by task ID.

Confirmed-correct procedure:

```bash
# 1. List all FWE agent tasks (after Bug-1 fix lands, this should be one
#    per actively-simulated VIN — no orphans).
aws ecs list-tasks \
  --cluster cms-staging-simulation \
  --family cms-staging-fwe-agent \
  --desired-status RUNNING \
  --region us-west-2

# 2. Describe with overrides to read the per-task VEHICLE_NAME and CAN_BUS0.
aws ecs describe-tasks \
  --cluster cms-staging-simulation \
  --tasks <taskArn-from-step-1> \
  --include OVERRIDES \
  --region us-west-2 \
  --query 'tasks[].overrides.containerOverrides[].environment[?name==`VEHICLE_NAME` || name==`CAN_BUS0`]'

# 3. Cross-check what the API thinks is the right pairing.
curl -s "$STAGE_ENDPOINT/api/simulation/agent/status" | jq '.agents[] | {taskArn, vin, status}'

# 4. Read THIS agent's logs only (replace <task-id> with the value from step 1).
aws logs tail "/ecs/cms-staging/fwe-agent" \
  --log-stream-name-prefix "fwe/fwe-agent/<task-id>" \
  --region us-west-2 --since 5m
```

If step 2 and step 3 disagree on the vcan binding for a VIN, the lookup logic is broken — file an issue and capture both outputs.

### Hot-fixing `simulation_lambda.py` between full deploys

The `cms-staging-simulation-api` Lambda function is asset-bundled directly from `services/simulation/lambda/simulation_lambda.py`. For Lambda-only changes (no infrastructure delta), use `aws lambda update-function-code` — ~30 second turnaround vs ~10 minutes for a full `make staging-deploy`:

```bash
# From repo root:
cd services/simulation/lambda
zip -j /tmp/simulation_lambda.zip simulation_lambda.py
aws lambda update-function-code \
  --function-name cms-staging-simulation-api \
  --zip-file fileb:///tmp/simulation_lambda.zip \
  --region us-west-2
```

⚠ **The hot-fix is overwritten by the next full `make staging-deploy`.** Always commit the change to `main` before running the hot-fix recipe — otherwise a teammate's full deploy will revert your Lambda update without warning.

For multi-file changes (anything beyond the single Lambda module), run `make staging-deploy` instead. The hot-fix recipe is for single-file Lambda iteration only.

### Deploy-time agent drain

When `cdk deploy cms-{stage}-simulation` bumps the `cms-{stage}-fwe-agent` task definition revision, ECS does **NOT** auto-replace previously-launched task instances. The agents are launched via one-shot `run_task` calls (not service-managed), so the old container persists `Up (unhealthy)` after the revision bump, holding every ISO-TP socket binding on its assigned vcan. The new task launches but `ExampleUDSInterface::openCANChannelPort()` fails with `Cannot allocate memory (ENOMEM)` because the kernel `can_isotp` socket pool is starved.

The `make deploy-simulation` target runs `bash deployment/scripts/drain_stale_fwe_agents.sh` automatically after `cdk deploy` returns, stopping every RUNNING `cms-{stage}-fwe-agent` task whose `taskDefinitionArn` revision is below the family's latest active revision.

**To run the drain manually** (e.g., to clean up an orphan discovered via the OrphanAgent alarm):

```bash
make -C deployment drain-stale-fwe-agents \
    DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2

# Preview only (no stop-task calls):
make -C deployment drain-stale-fwe-agents \
    DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \
    DRAIN_FLAGS="--dry-run"
```

**What disruption to expect**: an in-flight trip whose paired agent is on a stale revision will lose the last few seconds of telemetry when the agent is stopped. The next `_start` call automatically launches a new agent on the latest revision and the simulator resumes against it. Demos with explicit fwe-agent-uptime SLAs should be paused before deploys; otherwise the disruption is bounded to ≤ telemetry batch interval.

**Idempotency**: running drain twice in a row on a fully-current cluster exits 0 with `all N tasks on latest rev=X (no drain needed)` and no side effects. Safe to invoke ad-hoc.

**Edge cases**:

- `InvalidParameterException` on `stop-task` — treated as benign (the task transitioned to STOPPED on its own between list and stop, a benign race).
- `--timeout-seconds N` (default 60) governs the wait for stopped tasks to reach `lastStatus=STOPPED`. A timeout warns but does not fail the drain.
- 0 RUNNING tasks → exit 0 with `no fwe-agent tasks to drain`.

The script source-of-truth is `deployment/scripts/drain_stale_fwe_agents.sh` with shell-shim unit tests at `deployment/scripts/test_drain_stale_fwe_agents.sh` (5 cases, all green on macOS bash 3.2).

### Alarm runbook: orphan or stale-revision FWE agents

The simulation stack publishes three CloudWatch metrics under namespace **`FWE/Cluster`** (dimension `Stage={stage}`) every 5 minutes via the `cms-{stage}-fwe-agent-counter` Lambda:

| Metric                      | Meaning                                                                                                                              | Steady-state value |
|-----------------------------|--------------------------------------------------------------------------------------------------------------------------------------|--------------------|
| `AgentCount`                | Total RUNNING `cms-{stage}-fwe-agent` tasks in the cluster.                                                                          | Equals number of active simulations (one agent per VIN). |
| `OrphanAgentCount`          | RUNNING fwe-agent tasks whose `taskArn` is not referenced by any active sim row in `cms-{stage}-simulations` DDB (status `running` / `starting`). | **0** in steady state. |
| `StaleRevisionAgentCount`   | RUNNING fwe-agent tasks whose `taskDefinitionArn` revision is below the family's latest active revision.                            | **0** in steady state. |

The Lambda's CloudWatch Errors metric also has its own alarm (the lifecycle observability is itself observable).

The three alarms (all wired to SNS topic `cms-{stage}-simulation-alarms`):

| Alarm                                          | Threshold                                       | Sensitivity        |
|------------------------------------------------|-------------------------------------------------|--------------------|
| `cms-{stage}-fwe-orphan-agent`                 | `OrphanAgentCount > 0` for 2 of 2 datapoints    | ~10-minute window  |
| `cms-{stage}-fwe-stale-revision-agent`         | `StaleRevisionAgentCount > 0` for 1 of 1        | ~5-minute window   |
| `cms-{stage}-fwe-agent-counter-errors`         | Lambda `Errors > 0` for 2 of 2 datapoints       | ~10-minute window  |

Operators subscribe to the SNS topic out-of-band:

```bash
aws sns subscribe \
  --topic-arn arn:aws:sns:us-west-2:123456789012:cms-staging-simulation-alarms \
  --protocol email --notification-endpoint <ops-email>
```

**First-response runbook**:

- **`OrphanAgentCount > 0` for >10 min** — a Bug-1-class regression. Run `make -C deployment drain-stale-fwe-agents DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 DRAIN_FLAGS=--dry-run` first to identify the orphan ARN, then run live without `--dry-run` to reap it. If the alarm does not clear within one full eval cycle (~10 min after drain), file a P2 issue with the orphan ARN, the offending VIN (`VEHICLE_NAME` env override), and the `agentTaskArn` value of any DDB sim rows that referenced that ARN. Likely root cause: `_stop` failed to call `ecs.stop_task(agentTaskArn)` — review `services/simulation/lambda/simulation_lambda.py` `_stop()` against `test_simulation_lambda.py`.
- **`StaleRevisionAgentCount > 0` for >5 min** — a deploy-time leak. The post-deploy drain hook either did not run or did not complete. Run `make -C deployment drain-stale-fwe-agents DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2` to clear it. If the alarm does not clear, check `aws ecs describe-tasks` for the offending ARN and inspect `lastStatus` — a task stuck in `DEPROVISIONING` may need manual `aws ecs stop-task` with `--reason "manual-clear"`.
- **`cms-{stage}-fwe-agent-counter-errors`** — the counter Lambda itself is failing. Check `aws logs tail /aws/lambda/cms-{stage}-fwe-agent-counter --since 30m` for `Traceback` or AWS CLI `ClientError` messages. Likely causes: IAM drift (verify the lambda role has `ecs:ListTasks`, `ecs:DescribeTasks`, `ecs:DescribeTaskDefinition`, `dynamodb:Scan` on the simulations table, and `cloudwatch:PutMetricData` on namespace `FWE/Cluster`), DDB scan throttling (rare; the counter handles `ProvisionedThroughputExceededException` gracefully), or container packaging regression (re-run `cdk synth cms-{stage}-simulation` to confirm asset bundling).

**Verifying alarm wiring after a deploy**:

```bash
aws cloudwatch describe-alarms \
  --region us-west-2 \
  --alarm-names cms-staging-fwe-orphan-agent cms-staging-fwe-stale-revision-agent cms-staging-fwe-agent-counter-errors \
  --query 'MetricAlarms[].{Name:AlarmName,State:StateValue,Threshold:Threshold}'

aws cloudwatch list-metrics \
  --region us-west-2 --namespace FWE/Cluster

aws lambda invoke \
  --region us-west-2 \
  --function-name cms-staging-fwe-agent-counter \
  /tmp/counter-resp.json && cat /tmp/counter-resp.json
```

The `aws lambda invoke` ad-hoc invocation forces an immediate metric publish (instead of waiting up to 5 minutes for the schedule).

---

**Last updated:** 2026-05-31

---

## UI custom-domain alias — synth-time guard (staging + prod)

The CMS UI custom-domain alias (`staging.connected-mobility.awsi.aws.dev` for
staging, `connected-mobility.awsi.aws.dev` for prod), the Cognito
unauthenticated-identity path / SpaRewriteFunction, and (staging only) the
Midway edge auth gate are **conditional on the `uiCustomDomain` /
`uiCustomDomainCertArn` CDK context**. A `cdk deploy cms-<stage>-ui` synthesized
**without** that context silently drops them (issue
`2026-06-18-cms-ui-domain-alias-context-conditional-deploy-risk`).

A synth-time guard (`deployment/aspects/domain_alias_guard.py ::
enforce_ui_domain_alias`, wired in `app.py` via `UI_CUSTOM_DOMAIN_BY_STAGE`) now
**aborts synth** if a home-region deploy (staging in `us-west-2`, prod in
`us-east-1`) would synthesize without its alias — so a context-less deploy fails
fast with a clear message instead of dropping the domain. Cross-region
clean-deploys (region != the stage's home region) are exempt (they intentionally
skip the alias per cross-region-namespace discipline).

**Operator note:** the canonical UI deploy supplies the context automatically —
`UI_DOMAIN_CTX_FLAGS` in the `Makefile` builds `-c uiCustomDomain=… -c
uiCustomDomainCertArn=…` from `config/<stage>.env` (both `staging.env` and
`prod.env` now carry the committed domain + cert). If you ever hit the guard's
`RuntimeError`, you deployed from an environment missing those values: source
the stage's `config/<stage>.env` (or pass the `-c` flags) before retrying. Do
**not** work around the guard by removing it — that reintroduces the silent-drop
footgun.

## Driver self-vehicle-claim guard (iOS) — `DRIVER_SELF_GUARD_ENABLED`

Spec: `.kiro/specs/2026-06-19-cms-ios-driver-self-vehicle-claim/`.

The VSACompanion iOS app lets a driver who has **no assigned vehicle** claim one
from their fleet, reusing the CMS Fleet API (`GET /api/v1/vehicles` +
`PUT /api/v1/drivers/{id}`) with their Cognito id-token. Because staging runs a
**single consolidated Cognito pool** (the iOS app and the Fleet UI share
`cms-<stage>-ui-users`), `main_api` distinguishes a driver from an operator by
**claims**, not pool id:

- A token is treated as **driver-self** when `DRIVER_SELF_GUARD_ENABLED` is true
  AND it carries `custom:driverId` AND it is **not** in an operator group
  (`platform-admin` / `fleet-operator` / `fleet-viewer`).
- Driver-self tokens are forced non-admin and constrained to a deny-by-default
  allowlist: `GET /api/v1/vehicles` (fleet-scoped to the driver's own fleet;
  **fails closed** with 403 if the fleet can't be resolved) and
  `PUT /api/v1/drivers/{ownDriverId}` with body keys ⊆ `{assignedVehicleId}`.
  Everything else returns 403.
- Operators (group present) and no-driverId service accounts are unaffected.

### Deploy requirement

`DRIVER_SELF_GUARD_ENABLED=true` MUST be set on every `cms-<stage>-ui` deploy.
It is persisted in `deployment/config/staging.env` and passed through by the
phase1 deploy target. **If unset it defaults to `false`, which DISABLES the guard**
— in the consolidated pool that would let driver tokens hit the no-groups admin
default on the CMS API. The iOS side reads `VSA_CMS_REST_API_URL` (Staging.xcconfig);
it is empty in Release until the prod CMS API + pool trust are wired, which hides
the claim affordance in prod.

### Post-deploy smoke (verifies the guard is actually ON)

Invoke the Fleet API lambda with synthetic API-GW events and assert:
- driver token (`custom:driverId`, no group) `GET /api/v1/users` → **403**
- driver token `GET /api/v1/vehicles` → **200** (fleet-scoped)
- driver token `PUT /api/v1/drivers/<other>` → **403**
- operator token (`platform-admin`) `GET /api/v1/users` → **200** (no lockout)
