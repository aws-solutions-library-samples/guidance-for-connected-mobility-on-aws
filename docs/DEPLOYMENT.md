# CMS Deployment Guide

This guide covers deployment procedures for the Connected Mobility System (CMS).

> ## ⚠️ Read this before trusting any `prod` / `us-east-1` instruction in this document
>
> **CMS has been a single environment since 2026-09-04.** The `cms-prod-*`
> deployment in `us-east-1` was torn down per spec
> `.kiro/specs/2026-09-04-cms-prod-teardown/` (portfolio initiative
> `2026-09-02-env-collapse`, step 3g), along with `cms-prod` resources in
> `us-east-2`, `us-west-1` and `us-west-2`. The live environment is **`staging` in
> `us-west-2`**, which serves the staging domain configured via `UI_CUSTOM_DOMAIN`
> in `deployment/config/staging.env`, via a CloudFront distribution (id
> `E<distribution-id>`, resolved from the `cms-staging-ui` stack outputs).
>
> **Consequence for the rest of this guide:** every reference to a *prod
> environment*, a `cms-prod-*` stack, a `cms-prod-*` table/bucket/pool, or
> "`us-east-1` prod" is **historical**. Those resources do not exist. Individual
> sections carry their own ARCHIVED banners where the procedure is substantial, but
> this notice scopes the whole document — rather than annotating every one of the
> ~11 remaining inline mentions, which would leave the next reader trusting whichever
> one was missed.
>
> **What is still correct and is NOT about prod:** CloudFront ACM certificates must
> live in `us-east-1` (a CloudFront requirement, not an environment); ADP's staging
> Knowledge Base genuinely is in `us-east-1`; and the README cost table uses
> `us-east-1` as a pricing baseline. Do not "fix" those.
>
> To re-create a prod environment, the `DEPLOYMENT_STAGE=prod` parameter and its
> Makefile targets still exist and the archived procedures below are the reference —
> but nothing prod-targeted in this guide currently points at live infrastructure.
> <!-- verify: aws cloudformation list-stacks --query 'StackSummaries[?starts_with(StackName,`cms-prod-`) && StackStatus!=`DELETE_COMPLETE`].StackName' --region us-east-1 --output text -->

## Local pre-commit guard (run this once per clone)

```bash
make install-hooks     # from the repo root, not deployment/
make verify-hooks      # confirms all four moving parts
```

This points **this repository's** `core.hooksPath` at `.githooks/`, which scans the
files you have staged for publish-scanner findings before the commit lands. It is
repo-local; no other repository is affected.

**Read this before assuming you can skip it.** `core.hooksPath` is set globally to
Amazon's git-defender hooks directory, so anything in `.git/hooks/` never executes.
Taking over `core.hooksPath` therefore makes `.githooks/pre-commit` responsible for
invoking git-defender — which it does **first**, and it **fails closed** if the
git-defender binary is missing rather than skipping it. If you would rather not take
that on, `git config --unset core.hooksPath` restores the default and drops the
publish scan.

The scan is **staged-files-only** deliberately. The authoritative whole-tree gate is
`make verify-publish`, which takes ~87 seconds — too slow to run on every commit, and
a gate that slow gets disabled. The staged scan runs in ~1s and catches the failure
mode that actually bites: committing a new or modified file that **ships** and
contains a finding.

<!-- verify: grep -n 'install-hooks:' Makefile -->

It cannot catch a leak that only appears when a staged file interacts with an
unstaged one, and it does not re-scan files you did not touch. Those stay with:

| Gate | Scope | When |
|---|---|---|
| `.githooks/pre-commit` | staged files that ship | every commit, ~1s |
| `make verify-publish` | whole publishable tree | on demand, ~87s |
| CI `verify-publish-archive-clean` | whole publishable tree | every push |
| `scripts/publish-to-github.sh` | whole publishable tree | release |

If it blocks you, **fix the content**. Do not add a `scan_exclude` entry — a file
that is `scan_exclude`d but not `.publish-exclude`d ships *unexamined*, which is
strictly worse, and `~/.kiro/steering/public-mirror-publish.md` records four
exposures caused by exactly that. For a genuine false positive (a 12-digit run
inside a float, say) reword the content so the run does not appear.

## Environment Overview

The CMS uses a single-account, two-region deployment model:

| Environment | Region | Purpose |
|-------------|--------|---------|
| **Staging** | `us-west-2` | Validate changes end-to-end before prod |
| **Prod** | `us-west-2` | Customer-facing. **Same region and same infrastructure as staging** since the 2026-09-02 environment collapse — see below. |

> **CMS is a single environment as of 2026-09-04.** The former `cms-prod-*` deployment
> in `us-east-1` was torn down per spec
> `.kiro/specs/2026-09-04-cms-prod-teardown/` (portfolio initiative
> `2026-09-02-env-collapse`, step 3g). The staging domain configured via
> `UI_CUSTOM_DOMAIN` is served by a CloudFront distribution (id
> `E<distribution-id>`) off a `us-west-2` origin. The two rows above are
> retained because the `DEPLOYMENT_STAGE` parameter still exists in the Makefile, but
> **`DEPLOYMENT_STAGE=prod` no longer has a deployed target** — do not expect
> `make prod-deploy` to reach a live environment without first re-creating it.
> `cms-prod` resources were also removed from `us-east-2`, `us-west-1` and
> `us-west-2` during the same teardown.
> <!-- verify: aws cloudformation list-stacks --query 'StackSummaries[?starts_with(StackName,`cms-prod-`) && StackStatus!=`DELETE_COMPLETE`].StackName' --region us-east-1 -->

Note that many `us-east-1` references elsewhere in this document remain correct and
are **not** about the prod environment: CloudFront ACM certificates must live in
`us-east-1` (a CloudFront requirement), ADP's staging Knowledge Base is in
`us-east-1`, and the cost table uses `us-east-1` as a pricing baseline.

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

### External UI Callback Registry

The CMS Cognito User Pool Client maintains a centralized registry of callback and logout URLs for external frontends (DMS, Connected Services, etc.). This registry lives in `deployment/stacks/ui_stack.py` as the `EXTERNAL_UI_CALLBACK_CONTEXT_KEYS` list, where each entry corresponds to a context-key name (e.g., `"dmsUiCallbackOrigin"`) that can be provided at deployment time. When a frontend's callback URL is set via context, it is automatically appended to the User Pool Client's callback/logout URLs in a loop, enforcing `https://` validation per the constant's docstring. To onboard a new frontend, add one string to the registry list — no copy-paste if-block necessary.

### Connected Services portal (`ConnectedServicesUiStack`)

The Connected Services portal is a standalone CloudFront + S3 site whose custom
domain is configured via `CS_UI_DOMAIN_NAME` in `config/<stage>.env` (an internal
Amazon-network URL on staging — the concrete value lives in `config/`, not repeated
here per `~/.kiro/steering/public-mirror-publish.md`). It reuses the CMS Cognito
user pool via the External UI Callback Registry above, and attaches the same
CloudFront WebACL as `cms-<stage>-ui`.

#### Prerequisites (in this order)

1. **CMS phase1 has run at least once for the stage** — supplies the WAF ARN in SSM
   (`/cms/<stage>/ui-waf/web-acl-arn`) and `WAF_WEB_ACL_ARN` in `config/<stage>.env`.
   The CS stack reuses the same `wafWebAclArn` context key that `ui_stack.py:1787-1790`
   consumes; there is deliberately no CS-specific WAF key.
2. **The callback origin is registered on the shared pool client.** Add the entry
   `"connectedServicesUiCallbackOrigin"` to `EXTERNAL_UI_CALLBACK_CONTEXT_KEYS` in
   `deployment/stacks/ui_stack.py`, and set the value in `deployment/Makefile`'s
   `GUARD_CTX_FLAGS` for the CMS UI deploy targets. The CS deploy does **not**
   register its own callback — the pool client is owned by `cms-<stage>-ui`.
3. **CS Cognito + domain values in `config/<stage>.env`**: `COGNITO_USER_POOL_ID`,
   `COGNITO_CLIENT_ID`, `COGNITO_REGION`, `COGNITO_DOMAIN`, and the CS custom-domain
   variables. The stack passes all of these plus `wafWebAclArn` as context to synth.

#### Deploy

```bash
cd deployment
make deploy-connected-services DEPLOYMENT_STAGE=staging
```

This target **rebuilds the frontend bundle first** (`npm run build`). That is not
optional convenience: the stack ships `Source.asset(".../connected_services_ui/dist")`
and `dist/` is gitignored, so without the build step the deploy publishes whatever
bundle happens to be on your disk, corresponding to no particular commit. That is not
hypothetical — see `issues/2026-09-13-cs-ui-deploy-ships-stale-dist-no-build-step/`.

**Verifying a CS UI deploy actually took.** HTTP 200 on the portal measures CloudFront,
not the application — on a SPA every route returns the same `index.html` whether or not
the app can initialise. Compare the bundle hash instead, and remember screen components
are **code-split**, so a feature string lives in its own chunk rather than `index-*.js`:

```bash
# Hostnames come from config/<stage>.env ($CONNECTED_SERVICES_UI_CUSTOM_DOMAIN,
# $UI_CUSTOM_DOMAIN) -- this file SHIPS to the public mirror, so do not hardcode
# internal portal hostnames here. The pre-commit scanner blocks it, and naming the
# forbidden domain even in a warning comment trips the same check.
# 1. served index.html must reference the SAME hash the local build produced
grep -oE '/assets/index-[A-Za-z0-9_-]+\.js' modules/connected_services_ui/dist/index.html
curl -s "https://$CONNECTED_SERVICES_UI_CUSTOM_DOMAIN/" | grep -oE '/assets/index-[A-Za-z0-9_-]+\.js'

# 2. then assert a string from YOUR change in the relevant chunk
ls modules/connected_services_ui/dist/assets/ | grep -i <ScreenName>
curl -s "https://$CONNECTED_SERVICES_UI_CUSTOM_DOMAIN/assets/<ScreenName>-<hash>.js" | grep -c '<your string>'
```

<!-- verify: grep -n 'deploy-connected-services:' deployment/Makefile -->

### Subscription plane (`cms-{stage}-subscriptions`)

Carries the subscription tables plus the `/simulate/vehicles` and `/simulate/start`
Lambdas. Opt-in at synth via `DEPLOY_SUBSCRIPTIONS=true`, which the target sets.

```bash
cd deployment
make diff-subscriptions   DEPLOYMENT_STAGE=staging   # dry-run first
make deploy-subscriptions DEPLOYMENT_STAGE=staging
```

Added 2026-09-20. Before that this stack had **no** Makefile target despite being
deployed, so every deploy was a bespoke `cdk deploy` incantation — the hand-waved path
that `~/.kiro/steering/deploy-validation.md` § *Deploy Discovery* exists to prevent.

Two flags in the recipe are load-bearing. Do not remove either:

- **`--exclusively`** — without it CDK also deploys dependency stacks, and those *do*
  read context keys this target does not thread. That is the mechanism behind the
  2026-08-11 outage and its 2026-09-15 staging repeat: a narrow deploy silently
  reaching `cms-{stage}-ui` without its Federate/WAF/custom-domain context and dropping
  the pool-level IdP. It does **not** break cross-stack references — CFN exports resolve
  from the already-deployed stack's live outputs.
- **`. scripts/load-federate-creds.sh`** — `cdk deploy X` still *synthesises the whole
  app*, so `ui_stack`'s `_require_client_idp_config` guard runs even though only the
  subscription plane is being deployed. Without the creds it fails closed and the target
  cannot run at all. The helper is a no-op when `FEDERATE_OIDC_SECRET_ID` is unset, so
  cognito-only stages are unaffected.

> Do **not** resolve that guard failure with `CLIENT_EXTRA_IDPS=cognito-only`. Staging
> genuinely has Federate; asserting otherwise is precisely how the IdP gets deleted.

`subscriptions_stack.py` itself reads **no** `try_get_context` keys, so it needs none of
its own — `GUARD_CTX_FLAGS` is passed only to satisfy app.py's synth-time guards.

<!-- verify: grep -n 'deploy-subscriptions:\|diff-subscriptions:' deployment/Makefile -->

#### Simulation is Meridian-only

`GET /simulate/vehicles` returns only `producer == meridian` vehicles, and
`POST /simulate/start` independently refuses any other producer with
`reason: not_simulatable_producer:<producer>`. Both halves exist because the picker and
the start path do not share a code path; filtering only the list would leave the start
route accepting a hand-built body.

CS is Meridian's own portal and there is no oem1 or tesla simulation path — simulating a
`cloud-telemetry` vehicle publishes over MQTT basic-ingest to the CS product rule and
does **not** drive that vehicle's real ingest route. Restoring a wider picker requires
writing a real per-producer simulation path first, not deleting the filter. This
reverses spec decision T6.1; see
`.kiro/specs/2026-09-19-cs-trip-simulator-parity/decisions.md`.

<!-- verify: grep -n '_SIMULATABLE_PRODUCER' services/connectors/subscriptions/simulate_vehicle/handler.py -->

The stack reads `wafWebAclArn` context; when unset it synths cleanly and warns rather
than attaches. `stacks/tests/test_connected_services_waf.py` pins both the attach case
and the absent case. See `stacks/connected_services_ui_stack.py:241-243`.

#### Enabled state is IaC-declared, not implicit

`ConnectedServicesUiStack` defaults the CloudFront distribution's `Enabled` property to
**`false`** (`stacks/connected_services_ui_stack.py:273-274`). Only the exact string
`"true"` on `CONNECTED_SERVICES_UI_ENABLED` enables it; anything else fails closed.
`test_anything_other_than_true_fails_closed` (8-way parametrize) pins this.

This is deliberate. Talos filed a Critical against the pre-auth build (finding
`2026-09-04-connected-services-no-cloudfront-gate`) and the distribution was disabled
out-of-band as the mitigation. IaC-declared `Enabled=false` converts that mitigation
from drift-vs-template into declared state, so a routine `cdk deploy` after remediation
does not silently reopen the finding. A re-enable is a separate, explicitly authorized
step per § D7 of the auth-integration spec.

#### Smoke test post-deploy

```bash
aws cloudfront get-distribution-config --id <cs-distribution-id> \
  --query 'DistributionConfig.[Enabled,WebACLId]'
# expected: [true, "<cms-<stage>-ui-waf ARN>"]

aws cognito-idp describe-user-pool-client \
  --user-pool-id <pool-id> --client-id <client-id> \
  --query 'UserPoolClient.CallbackURLs'
# expected: contains "https://<cs-portal-domain>/auth/callback"

curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' \
  "https://${CS_UI_DOMAIN_NAME}/"
# expected: HTTP 200 (SPA shell). The Federate redirect is client-side JS —
# HTTP 200 measures CloudFront, not the application; use the UAT checklist for
# application-layer observations.
```

The UAT checklist at
`.kiro/specs/2026-09-05-cms-connected-services-auth-integration/uat-checklist.md`
covers the application-layer observations no automated test can reach.

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

## Fleet Intelligence prerequisites

Fleet Intelligence provides cost-per-mile (CPM) and lifecycle analysis by reading curated cost data from the Automotive Data Platform (ADP) via cross-account Athena queries. This section documents the prerequisites and environment variable configuration required for the `/api/v1/fleet-intelligence/*` routes to function.

See `services/fleet_intelligence/README.md` for architecture and design details.

### ADP Side: Lake Formation Grants

Before deploying CMS Fleet Intelligence, ADP must grant the Fleet Intelligence Lambda's role read access to the ADP lake. The grants live in ADP's `adp-{stage}-foundation-governance` stack, which reads the CMS account ID and the role ARN from environment variables. Deploy CMS first, since the role must exist, then run this from the ADP repo:

```bash
cd ~/automotive-data-platform-on-aws/platform-foundation
ACCT=$(aws sts get-caller-identity --query Account --output text)
# list-stack-resources pages through all resources; describe-stack-resources
# stops at 100, and cms-staging-ui has more than that.
ROLE_NAME=$(aws cloudformation list-stack-resources --stack-name cms-staging-ui --region us-west-2 --output json \
  | python3 -c 'import json,sys; r=[x["PhysicalResourceId"] for x in json.load(sys.stdin)["StackResourceSummaries"] if x["ResourceType"]=="AWS::IAM::Role" and x["LogicalResourceId"].startswith("FleetIntelligenceRole")]; assert len(r)==1, r; print(r[0])')
test -n "$ROLE_NAME" && echo "role: $ROLE_NAME" || echo "ERROR: FleetIntelligenceRole not found; stop here" >&2
# Braces matter: in zsh, $ACCT:role would apply the :r modifier.
export ADP_CMS_ACCOUNT_ID=${ACCT} ADP_CMS_CONSUMER_ROLE_ARN=arn:aws:iam::${ACCT}:role/${ROLE_NAME}
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 CDK_DEFAULT_REGION=us-east-1 CDK_DEFAULT_ACCOUNT=${ACCT}
.venv/bin/cdk diff adp-staging-foundation-governance -c stage=staging --exclusively
.venv/bin/cdk deploy adp-staging-foundation-governance -c stage=staging --exclusively --require-approval never
```

**Parameters**:
- **`ADP_CMS_ACCOUNT_ID`**: the CMS AWS account ID (12 digits). Deploying without it removes the CMS `:root` grants and, unless a CVX or DMS account ID is set, the lake bucket's Lake Formation registration.
- **`ADP_CMS_CONSUMER_ROLE_ARN`**: the Fleet Intelligence Lambda's execution role, `cms-{stage}-ui-FleetIntelligenceRole<suffix>`. The suffix is generated by CDK, so read it from the stack as above. Deploying without it deletes the role's grants. The stack rejects an ARN with an empty role name.

The same stack also carries the CVX and DMS shares (`ADP_KB_CVX_ACCOUNT_ID`, `ADP_KB_DMS_ACCOUNT_ID`). Set them only if the deployed stack already has those grants. Read the `cdk diff` before deploying: it should add or change only what you intend, and delete nothing.

**Without these ADP-side grants**, every FI API call returns HTTP 500 `AthenaCursorError` when attempting to query the curated tables.

The grants cover the five curated products plus one dimension table. The ADP-wide lifecycle rollup (`refresh-adp-rollup`) joins `adp_{stage}_dimensions.vins` for model and model year, so the role gets DESCRIBE on the `adp_{stage}_dimensions` database and SELECT + DESCRIBE on `vins` only. That database also holds `customers`, which contains PII, so the grant never uses a table wildcard there. The matching IAM statements in `ui_stack.py` name `table/adp_{stage}_dimensions/vins` and `dimensions/vins/*` for the same reason. <!-- verify: cd deployment && pytest stacks/tests/test_ui_stack_fleet_intelligence_adp_grants.py -k "dimensions or every_adp_table or customers" -->

A grant is verified only by a query that runs as the Fleet Intelligence role, so invoke the deployed Lambda. `{"fleetIntelligenceTask": "refresh-lifecycle-cache"}` reads the five products, and `{"fleetIntelligenceTask": "refresh-adp-rollup"}` reads `service_records`, `charging_sessions`, `energy_usage` and `dimensions.vins`. `LIVE_TESTS=1` runs as the operator's own principal, which is usually a Lake Formation admin. Those tests prove the SQL, not the grants.

List the role's Lake Formation grants on the dimensions database and on `vins` (`list-permissions` requires a resource when a principal is given):

```bash
for RES in '{"Database":{"Name":"adp_staging_dimensions"}}' \
           '{"Table":{"DatabaseName":"adp_staging_dimensions","Name":"vins"}}'; do
  aws lakeformation list-permissions --region us-east-1 \
    --principal DataLakePrincipalIdentifier=$ADP_CMS_CONSUMER_ROLE_ARN --resource "$RES" \
    --query 'PrincipalResourcePermissions[].Permissions' --output text
done
```

Expected output: `DESCRIBE` for the database, then `DESCRIBE` and `SELECT` for `vins`, one permission per line, in either order.

### CMS Side: Fleet Intelligence Lambda Environment Variables

The CMS ui_stack automatically threads the following 9 environment variables into the Fleet Intelligence Lambda. All are required; missing any variable causes the Lambda to raise at cold start:

| Variable | Value | Purpose |
|---|---|---|
| `ADP_STAGE` | `staging` or `prod` | Which ADP database to query (validated fail-closed) |
| `ADP_REGION` | `us-east-1` | AWS region where ADP Athena workgroups live (usually us-east-1) |
| `ATHENA_WORKGROUP` | `cms-staging-analytics` | Athena workgroup name for Fleet Intelligence queries |
| `ATHENA_OUTPUT_LOC` | `s3://cms-<stage>-athena-results-<account-id>-us-east-1/fleet-intelligence/` | S3 URI for Athena query results. **Do not copy this literally** — the bucket name is composed at synth time and includes the account id and region for global-namespace uniqueness. Read the deployed value rather than authoring one. |
| `ADP_DATA_PROVENANCE` | `simulated` (staging) or `measured` (prod) | Provenance label attached to every cost row; flip signal between demo data and live |
| `FI_WINDOW_MONTHS` | `12` | Default rolling-window size (months) for cost-per-mile routes when the handler does not override |
| `FI_LIFECYCLE_WINDOW_MONTHS` | `36` | Rolling-window size (months) for lifecycle analysis (`/lifecycle` route) across both CMS-fleet and ADP-wide scopes. Independent from `FI_WINDOW_MONTHS`. <!-- verify: grep -n "FI_LIFECYCLE_WINDOW_MONTHS" deployment/stacks/ui_stack.py | head -1 --> |
| `VEHICLES_TABLE_NAME` | `cms-staging-storage-vehicles` | DynamoDB table name; used for vin → vehicleId/fleetId map |
| `PM_SCHEDULES_TABLE_NAME` | `cms-staging-pm-schedules` | DynamoDB preventive maintenance schedules table name |

These values are resolved and injected at CDK synth time. Post-deploy, verify they are set on the deployed Lambda function.

The construct sets no explicit `function_name`, so the physical name is
CloudFormation-generated (`cms-<stage>-ui-FleetIntelligenceFunction<hash>-<suffix>`)
and differs per deployment. Discover it rather than hardcoding it:

```bash
# Region is parameterised: CMS staging runs in us-west-2, but read it from your
# environment rather than assuming, so the block works for prod and other regions.
CMS_REGION="${CMS_REGION:-us-west-2}"

FI_FN=$(aws lambda list-functions --region "$CMS_REGION" \
  --query "Functions[?contains(FunctionName,'FleetIntelligence')].FunctionName" \
  --output text)

aws lambda get-function-configuration \
  --function-name "$FI_FN" \
  --region "$CMS_REGION" \
  --query 'Environment.Variables' --output table
```

The output MUST contain all 9 keys. <!-- verify: cd deployment && pytest stacks/tests/test_ui_stack_fleet_intelligence_adp_grants.py -k test_env_keys_are_exactly_the_nine_the_code_reads --> Missing any key causes API calls to return HTTP 500 at the Lambda handler init phase.

### ADP Scope: Platform-Admin Lifecycle Rollup

`GET /api/v1/fleet-intelligence/lifecycle?scope=adp` provides a lifecycle summary across all ADP vehicles (about 4.7M VINs in staging) and is available only to `platform-admin` users. The rollup is precomputed hourly via EventBridge rule `FleetAdpRollupRefreshRule` and read-only from a cached artifact to avoid API Gateway's 29-second timeout.

**Refresh Schedule:** EventBridge rule `FleetAdpRollupRefreshRule` <!-- verify: grep -n "FleetAdpRollupRefreshRule" deployment/stacks/ui_stack.py | head -1 --> triggers every hour (rate = 60 minutes) with zero retries. The Lambda has a 900-second timeout to complete 4 concurrent Athena queries and poll them at 4-second intervals. The artifact is written to `{ATHENA_OUTPUT_LOC}_cache/adp-lifecycle-rollup-v1.json` with a `computedAt` timestamp.

**Artifact freshness:** If the rollup is missing or older than 26 hours, `GET /lifecycle?scope=adp` returns HTTP 503 with the last known `computedAt`.

**Parameters:** The window is configured via `FI_LIFECYCLE_WINDOW_MONTHS` (shared with CMS-fleet lifecycle). The default is 36 months; a `horizonMonths` query parameter other than 36 returns HTTP 400 when `scope=adp`.

**Assumptions:** The rollup uses a 60,000 USD purchase price (ADP provides no vehicle price) and 120-month straight-line depreciation. These are included in the response under `assumptions`.

---

### Troubleshooting

**`AthenaCursorError` on every `/api/v1/fleet-intelligence/cpm` request:**
- Check ADP-side Lake Formation grants (above) are present and correct.
- Verify `ATHENA_WORKGROUP` name matches the actual workgroup in the ADP account.
- Confirm the CMS Lambda execution role ARN matches `ADP_CMS_CONSUMER_ROLE_ARN` passed to ADP deployment.

**`KeyError: 'ADP_DATA_PROVENANCE'` or similar at Lambda init:**
- Verify all 9 environment variables are set on the Lambda function (see verification query above).
- Re-deploy the CMS ui_stack: `cd deployment && cdk deploy CmsUiStack --require-approval never`

**Fleet-scoped queries return empty list but portal-wide queries work:**
- Verify the requested `fleet_id` exists in `cms-{stage}-storage-vehicles` table.
- Check that at least one vehicle in that fleet has a `vin` attribute (required for Athena join).

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

## Simulation & DTC Injection

The simulation stack provides two surfaces for testing DTC collection on a parked vehicle:

### Trip Simulator campaign attachment (`2026-09-01-cms-campaign-follows-enrollment`)

Every vehicle-telemetry (onboard) vehicle must carry a `RUNNING` FleetWise campaign row in `cms-{stage}-campaigns` (with `targetArn=vehicle:{vin}`) before the Trip Simulator will run against it. Absent one, `POST /api/simulation/start` returns 400 with an actionable message pointing the operator at the recovery paths below.

**Attachment paths, in order of preference:**
1. **At create time (recommended)** — the Create Vehicle wizard exposes an optional "FleetWise campaign" dropdown. Selecting a template writes the per-vehicle campaign row inline in the 201 response from `POST /api/v1/vehicles`. Idempotent via `campaignId=f"{template}-{vin}"` + `attribute_not_exists`.
2. **Retroactively via operator override** — `python3 deployment/scripts/deploy_vehicle_campaign.py --vehicle-name <thingName> --template <templateRef>` writes the same campaign row on-demand for pre-existing vehicles or vehicles whose FWE `VEHICLE_NAME` disagrees with the cert's `thingName` (e.g. `VEH-VO-001`). Preserved as-is post spec ship; the docstring documents this scope.

**Cloud-telemetry vehicles skip the gate entirely** — they don't use FleetWise campaigns, and `hasCampaign` in the vehicle-detail projection is always `false` by construction.

**Detection**: the vehicle-detail view surfaces `hasCampaign: false && classification: 'onboard'` as a warning banner reading *"This vehicle has no active FleetWise campaign. Telemetry will not be collected..."* — attach a campaign before running any simulation against that vehicle.

### Parked Vehicle Fault Injection (`/api/simulation/vehicle/{vehicleId}/faults`)

Inject and clear diagnostic trouble codes (DTCs) on a parked FleetWise Edge vehicle without running a trip simulator.

**Endpoint pair:**
- `PUT /api/simulation/vehicle/{vehicleId}/faults` — Set faults
- `GET /api/simulation/vehicle/{vehicleId}/faults` — Retrieve current state and injectable set

**Request body (PUT)**:
```json
{
  "maintenance_scenarios": [
    "maintenance.catalyst_efficiency_low",
    "maintenance.brake_system_fault"
  ]
}
```

**Response (PUT 200 OK)**:
```json
{
  "success": true,
  "ecus": {
    "2": {"req": "0x7E1", "resp": "0x7E9", "dtcs": ["P0420"]},
    "1": {"req": "0x7E0", "resp": "0x7E8", "dtcs": ["C0035"]}
  },
  "appliesWithinSeconds": 40,
  "clearGuidance": "..."
}
```

**What it does:**
1. Validates event IDs against the event catalog and resolves them to ECUs via SAE prefix derivation (P→ECU2, C→ECU1, U→ECU5, B→ECU9, with 8-entry exception table for non-default subsystems).
2. Writes a `faultState` attribute to the vehicle's DDB record with the ECU map.
3. On the next idle tick (~9–10 s), the presence loop reconciles the UDS responder: if the map changed, the old responder is terminated and a new one spawned; if empty, the responder is terminated and stays stopped.
4. FWE fires `DTC_QUERY` on each ECU every ~30 s (governed by the standing `uds-dtc-polling-<vin>` campaign).
5. Within ~75 s, a `source=fwe-uds-dtc` row appears in `cms-{stage}-storage-dtc-history` with `status=ACTIVE`.

**Prerequisites:**
- Vehicle must have a **standing campaign** `uds-dtc-polling-<vin>` in `RUNNING` state (assign via Campaigns tab using template `uds-dtc-polling`)
- Vehicle must be FWE-class (not MQTT-Direct); returns 400 otherwise
- Vehicle's FWE agent task must be running

**Clearing Faults (two-step, order-dependent):**
1. **Bus clear**: `PUT /faults []` with empty list → responder terminates → ECU stops answering → `occurrenceCount` stops advancing (~40 s)
2. **Record clear**: Wait 40 s, then click "Mark Cleared" in UI → sets `clearedDate` / `clearedBy`, removes `activeCode` from GSI

If you mark cleared while the ECU still reports, the next poll resurrects a new ACTIVE row. The correct order is enforced by the response's `clearGuidance` field.

**EC2 Instance Capacity:**
- Responder subprocess: ~31–36 MiB RSS (measured on local development)
- Sidecar memory limit: 256 MiB hard / 128 MiB soft
- Combined parent + responder: ~70–95 MiB estimated, well within limits

**SIM_IMAGE_MODE requirement:**
Resolved as of `cms-sim-service:v0.2.9` (published 2026-08-19 from commit `a918ce35`). Published mode — the default — now carries the sidecar-side presence-loop reconciler (`ensure_uds_responder`), so no override is needed:
```bash
make deploy-simulation DEPLOYMENT_STAGE=staging
```
Previously this required `SIM_IMAGE_MODE=asset` because published mode shipped `cms-sim-service:v0.2.6` / `v0.2.8`, both of which predated the reconciler. That same staleness is what broke the `vehicle-ecu` presence loop — see `issues/2026-08-19-cms-vehicle-ecu-presence-not-resident/`. A synth-time guard now fails closed instead of silently deploying a stale image; see § *Simulation deployment → Published-image provenance guard*.

**Fleet Authorization & Deployment Notes (2026-08-31):**

As of 2026-08-31, the simulation Lambda enforces fleet-scoped authorization on all 12 routes. When deploying the updated sim stack, verify three requirements:

1. **New IAM grant**: The sim Lambda role now requires `dynamodb:Query` permission on the `vehicleId-index` GSI of the fleet enrollment table. This is added automatically by the CDK stack (`FLEET_ENROLLMENT_TABLE_NAME` env var + IAM policy statement in `simulation_stack.py`). Verify post-deploy:
   ```bash
   aws lambda get-function-configuration --function-name cms-staging-simulation-SimulationApiFunction-<hash> \
     --region us-west-2 --query 'Environment.Variables.FLEET_ENROLLMENT_TABLE_NAME'
   # expected output: cms-staging-storage-fleet-enrollment (or similar, varies by region)
   ```

2. **Environment variable**: `FLEET_ENROLLMENT_TABLE_NAME` must be set on the Lambda. It is set by the CDK stack from the storage-stack export. If the deploy output does not include this env var, the authorization gates will fail at runtime with "table not found."

3. **SIM_IMAGE_ALLOW_STALE gotcha**: If **only** the Lambda handler code changed (not the ECS simulator image), the ECS image may be flagged as stale by the provisioner guard. To force a redeployment when the handler is the only change:
   ```bash
   make deploy-simulation DEPLOYMENT_STAGE=staging SIM_IMAGE_ALLOW_STALE=1
   ```
   This bypasses the staleness check and redeploys the Lambda's new authorization code. Without this flag, a `make deploy-simulation` that sees the existing image still in use may skip the image, leaving old handler code running.

4. **`regenerate-runtime-config` may fail at the end — usually harmlessly.** The target's
   final step refuses to run when `COGNITO_DOMAIN` is unset, because writing an empty
   `cognitoDomain` would silently remove the Federate login button
   (`issues/2026-09-14-regenerate-runtime-config-strips-cognito-domain/`). The Makefile
   treats it as a warning and still exits 0, so **the deploy itself succeeded.**

   Decide whether it mattered rather than assuming either way: it only matters if the
   simulation API endpoint actually changed. Compare the live config against the stack
   output — the live file, not the local one:

   ```bash
   aws cloudformation describe-stacks --stack-name cms-staging-simulation --region us-west-2 \
     --query "Stacks[0].Outputs[?contains(OutputKey,'ndpoint')].OutputValue | [0]" --output text
   curl -s "https://$UI_CUSTOM_DOMAIN/runtimeConfig.json" | python3 -m json.tool | grep simulationApiEndpoint
   ```

   If they match, nothing is owed. If they differ:
   `source deployment/config/staging.env && make regenerate-runtime-config DEPLOYMENT_STAGE=staging`.

   > **Known trap.** `modules/cms_ui/source/frontend/public/runtimeConfig.json` is a
   > *generated* artifact and is currently stale — as of 2026-09-20 it names a
   > **us-east-1** `simulationApiEndpoint` while staging runs in **us-west-2**. The
   > deployed config is correct; the local file is not. A UI deploy that ships that file
   > without regenerating it would push a wrong-region endpoint. Regenerate before any UI
   > deploy rather than trusting the checked-in copy.

**Post-deploy verification (authorization changes):**

A green deploy is not evidence the guard works. Exercise the refusal against the live
endpoint, and assert **which** refusal fires — a scoping check and a fail-closed guard
both return 403, so a bare status assertion cannot tell them apart:

```bash
# fleet-operator token WITH a non-empty custom:fleetIds, or "Caller has no fleet
# assignments" masks the guard under test and the check passes for the wrong reason.
API=$(aws cloudformation describe-stacks --stack-name cms-staging-simulation --region us-west-2 \
  --query "Stacks[0].Outputs[?contains(OutputKey,'ndpoint')].OutputValue | [0]" --output text)
curl -s -X POST -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{}' "${API}api/simulation/start"
# expect 403 "vehicles must be a non-empty list of vehicleIds for this route."

# anti-vacuity: a resolvable vehicleId in ANOTHER fleet must produce a DIFFERENT 403
# ("Caller not authorized for fleet of vehicle(s): [...]"), proving the guard is
# specific rather than refusing everything.
```

<!-- verify: grep -n 'require_resolved_vehicle_ids' services/simulation/lambda/simulation_lambda.py -->

**FWE Smoke-Gate Validation:**
After injection, verify the agent remains responsive:
```bash
# Check agentConnected AND telemetry freshness, never just task count
aws ecs describe-container-instances \
  --cluster cms-staging-simulation \
  --region us-west-2 \
  --query 'containerInstances[?agentConnected==`true`].{
    agentConnected,
    ec2InstanceId,
    runningCount: runningTasksCount}'
```

**Detailed ops guide:** See `docs/FWE_UDS_DTC.md` § *Parked Vehicle — Injection and Clearing*.

---

## Discover Phase: Retail Lead Capture + Shopping Guidance

The Discover phase introduces a voice-first entry into the Buy flow on MeridianMotorsCompanion, with catalog-grounded shopping guidance, lead capture, and a natural handoff to the configurator. This section documents deployment of the new backend infrastructure (the `vsa-acquire-leads` DDB table, KB source category, and env-var wiring).

### Architecture Overview

Discover is a standalone entry point to the Buy journey, implemented as a DiscoverFlow in the iOS app. It reads from the existing `vsa-acquire-catalog` DDB table and Bedrock KB (`vehicle_shopping_guide` source category), and writes lead records to the new `vsa-acquire-leads` table. The AgentCore text runtime (`vsa_supervisor_text_staging`) powers voice and narration via the `/assistant/chat` endpoint reused from the CMS web UI.

**New tables and resources:**
- `vsa-acquire-leads` DDB table: stores lead contact info + consent records + STAR envelope with discovered preferences
- `vehicle_shopping_guide` KB source category: 10 docs grounding Discover-phase catalog-query, catalog-compare, and offers-lookup tools
- Environment variables: `VSA_DISCOVER_KB_SOURCE_CATEGORY`, `VSA_DISCOVER_GUARDRAILS_ID`, `VSA_ACQUIRE_LEADS_TABLE`

**Shared resources (existing):**
- `vsa-acquire-catalog` DDB table (pre-seeded by Acquire phase)
- `vehicle_manufacturing` KB source category (12 docs, renamed from a former vehicle-type-specific category)
- AgentCore text runtime: `vsa_supervisor_text_staging` (existing infrastructure)

### Prerequisites

Before deploying Discover, verify:
1. The Acquire phase is deployed: `vsa-acquire-catalog` and `vsa-acquire-orders` tables exist in DynamoDB
2. The Meridian KB corpus is ingested into ADP staging KB (`YULHRAUMNU`, us-east-1):
   - `vehicle_manufacturing` source category (12 docs) — ingested by the closed `2026-07-31-zone2-meridian-kb-corpus` spec
   - `vehicle_shopping_guide` source category (10 docs) — ingested after this spec's Group 3 Task 3

### CDK Synth Diff

The `vsa-acquire-stack` remains unchanged from the Acquire phase (it created both `vsa-acquire-orders` and `vsa-acquire-leads`). Verify:

```bash
cd deployment && npx cdk synth vsa-staging-acquire 2>&1 | grep -c "AWS::DynamoDB::Table"
# expected: 3 (catalog, orders, leads)

npx cdk synth vsa-staging-acquire 2>&1 | grep "LogicalId.*AcquireLeads"
# expected: at least one match showing vsa-acquire-leads table definition
```

### Seed Procedure: `vehicle_shopping_guide` KB Source Category

After deploying the CDK stack, seed the new `vehicle_shopping_guide` source category into the Bedrock KB:

```bash
cd deployment

# 1. Verify the KB staging S3 lake location
aws s3 ls s3://adp-staging-foundation-lake-<account-id>-us-east-1/knowledge/vehicle_knowledge_base/sources/ \
  --region us-east-1 | grep -E vehicle-shopping-guide

# expected: if empty, the corpus has not been ingested yet (proceed to step 2)
# if "vehicle-shopping-guide/" is listed, ingestion is complete (skip to env-var wiring)

# 2. Upload the corpus and start an ingestion job. There is no wrapper script —
#    run the two AWS commands directly from the CVX repo, which owns the corpus.
#    Note the deliberate convention: S3 prefixes are HYPHENATED, source_category
#    values are UNDERSCORED. Retrieval filters on the sidecar value, not the prefix.
cd ../guidance-for-connected-vehicle-experience-on-aws

B=adp-staging-foundation-lake-$(aws sts get-caller-identity --query Account --output text)-us-east-1
P=knowledge/vehicle_knowledge_base/sources

# Use `cp --recursive`, never `sync --delete` — the same prefix holds other
# categories that must survive untouched.
aws s3 cp corpora/vehicle_shopping_guide/ "s3://$B/$P/vehicle-shopping-guide/" \
  --recursive --region us-east-1 \
  --exclude "*" --include "*.md" --include "*.md.metadata.json"

aws bedrock-agent start-ingestion-job \
  --knowledge-base-id YULHRAUMNU --data-source-id QZL8HLXQ1H \
  --region us-east-1 --description "vehicle_shopping_guide ingest"

# Poll until COMPLETE, then confirm numberOfDocumentsFailed == 0:
aws bedrock-agent get-ingestion-job \
  --knowledge-base-id YULHRAUMNU --data-source-id QZL8HLXQ1H \
  --ingestion-job-id <job-id> --region us-east-1 \
  --query 'ingestionJob.{status:status,stats:statistics}'
```

**The AOSS index lags ingestion COMPLETE by roughly 50–90 seconds.** Wait ≥90s
before smoke-testing, or a retrieve immediately after will return nothing and
produce a false failure.

**Note on corpus retirement**: The `vehicle_shopping_guide` corpus replaces the deprecated `bike_shopping_guide` corpus (10 two-wheeler docs, retired by this spec's Group 3). The `auto_manufacturing` corpus was renamed to `vehicle_manufacturing` in the same update. If you see references to those deprecated categories in your logs, verify your KB data source is pointing to the current staging lake bucket and re-run the ingestion script above.

### Environment Variable Wiring: `agentcore configure` + `agentcore deploy`

The AgentCore runtime uses three new environment variables for Discover grounding:

| Variable | Value | Purpose |
|----------|-------|---------|
| `VSA_DISCOVER_KB_SOURCE_CATEGORY` | `vehicle_shopping_guide` | Scope KB retrievals in Discover phase to shopping-guide docs only |
| `VSA_ACQUIRE_KB_SOURCE_CATEGORY` | `vehicle_manufacturing` | Scope KB retrievals in Order+Build phases (manufacturing_explain tool) |
| `VSA_DISCOVER_GUARDRAILS_ID` | `discover-offers-guardrails-v1` | Placeholder Bedrock Guardrails identifier for offers narration (policy authored in a follow-on spec) |
| `VSA_ACQUIRE_LEADS_TABLE` | `vsa-acquire-leads` | DDB table name for storing discovered leads |

Wire these when configuring and deploying the text runtime:

```bash
cd ~/guidance-for-connected-vehicle-experience-on-aws

# `agentcore configure` is a ONE-TIME local registration of the entrypoint; it
# does not take --env or --stage. Environment variables are supplied at deploy
# time, and the target runtime is selected with -a.
agentcore configure --entrypoint agents/supervisor/agentcore_app.py

# Deploy, threading the env vars. Repeat with -a vsa_supervisor_bidi_staging
# for the voice runtime.
agentcore deploy -a vsa_supervisor_text_staging --auto-update-on-conflict \
  --env VSA_DISCOVER_KB_SOURCE_CATEGORY=vehicle_shopping_guide \
  --env VSA_ACQUIRE_KB_SOURCE_CATEGORY=vehicle_manufacturing \
  --env VSA_DISCOVER_GUARDRAILS_ID=discover-offers-guardrails-v1 \
  --env VSA_ACQUIRE_LEADS_TABLE=vsa-staging-acquire-leads
```

Prefer the Makefile target below over hand-running these — it threads the full
env-var set to both runtimes and is the path Group 3 Task 3.4 updated.

**Makefile shorthand** (already wired in `Makefile`):
```bash
make deploy-staging  # wires all four env vars automatically
```

### Smoke Test: Discover Entry Point

Post-deploy, verify the Discover-phase tools are accessible via `/assistant/chat`:

```bash
# 1. Acquire a staging JWT for the fleet_driver persona
export JWT=$(aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=samantha.carter@example.com,PASSWORD=<demo-password> \
  --client-id <staging-ui-client-id> \
  --region us-west-2 \
  --query 'AuthenticationResult.IdToken' \
  --output text)

# 2. Query the catalog via the agent's catalog_query tool
curl -X POST https://<cvx-staging-api-endpoint>/assistant/chat \
  --header "Authorization: Bearer $JWT" \
  --header "Content-Type: application/json" \
  --data '{
    "message": "Help me pick a vehicle for daily commuting",
    "runtimeSessionId": "test-session-'$(uuidgen)'",
    "sessionId": "test-'$(uuidgen)'"
  }' \
  | jq '.assistantMessage | select(contains("commute") or contains("Compact") or contains("model"))'

# expected: agent narration mentioning vehicle categories or model suggestions,
# with inline catalog cards visible in a real iOS client
```

**Expected flow:**
1. User enters DiscoverFlow (Buy tab tap, anonymous or signed-in)
2. Agent narrates greeting from `AcquireConfig.discover.discoverGreeting` (configurable per tenant)
3. User voices a catalog query, agent calls `catalog_query()` tool
4. Tool returns top-3 matching models; agent narrates with inline cards
5. On convergence (CTA tap), agent calls `lead_capture()`, writes to `vsa-acquire-leads`, fires handoff to ConfiguratorFlow
6. ConfiguratorFlow opens with pre-selected category/model per the DiscoverHandoff STAR envelope

### Smoke Test: Offers & Disclosure

Verify the Discover-phase offers-lookup tool returns required disclosure text:

```bash
# 1. Query offers for a Meridian model
curl -X POST https://<cvx-staging-api-endpoint>/assistant/chat \
  --header "Authorization: Bearer $JWT" \
  --header "Content-Type: application/json" \
  --data '{
    "message": "Show me current offers on the Windrose",
    "runtimeSessionId": "test-session-'$(uuidgen)'",
    "sessionId": "test-'$(uuidgen)'"
  }' \
  | jq '.assistantMessage | select(contains("offer") or contains("$") or contains("deposit"))'

# expected: agent narration including required `disclosure_txt` verbatim
# (no paraphrase, no "or similar")
```

### Smoke Test: Lead Capture Idempotency

Verify lead-capture idempotency via DDB:

```bash
# 1. Capture a lead (POST /acquire/leads)
CONTACT_INFO='{"emailNormalized":"test@example.com","phoneE164":"+12025551234"}'
aws dynamodb query \
  --table-name vsa-acquire-leads \
  --index-name by-tenant-and-contact \
  --key-condition-expression "tenantId = :t AND begins_with(contactHash, :h)" \
  --expression-attribute-values '{
    ":t": {"S": "meridian"},
    ":h": {"S": "'$(echo -n "meridian.test@example.com.+12025551234" | sha256sum | cut -d' ' -f1)}'
  }' \
  --region us-west-2

# expected before capture: 0 items
# expected after capture: 1 item with leadId + envelope + createdAt

# 2. Tap "Let's build this" again (rapid duplicate)
# expected: same leadId returned (idempotent write via GSI lookup + ConditionExpression)
```

### Rollback

To revert Discover phase:

**Option A: Soft rollback** (leave infrastructure, flip env vars):
```bash
# There is NO corpus to roll back TO. The former two-wheeler shopping-guide
# corpus was deleted from the repo AND its S3 prefix was removed from the
# knowledge base (Group 3 Task 3.4, ingestion job TPFM87PFR3 deleted 34
# documents). Pointing this variable at the retired category would retrieve
# zero documents, which is worse than the current state.
#
# The only meaningful soft rollback is to re-deploy with the same category and
# let the Discover tools degrade to `{available: false}` if the KB is
# unreachable — the tools already handle that path.

agentcore deploy -a vsa_supervisor_text_staging --auto-update-on-conflict \
  --env VSA_DISCOVER_KB_SOURCE_CATEGORY=vehicle_shopping_guide
```

**Option B: Hard rollback** (remove infrastructure):
```bash
# Remove the vsa-acquire-leads table (destructive)
aws dynamodb delete-table \
  --table-name vsa-acquire-leads \
  --region us-west-2

# Verify it is gone
aws dynamodb list-tables --region us-west-2 | grep vsa-acquire-leads
# expected: no output
```

**Note**: Lead records (if any exist) are lost in Option B. Backup to S3 if needed before deletion.

---

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

### iOS demo app (MeridianMotorsCompanion): rebuild cadence

The iOS Simulator demo used by `docs/runbooks/ios-connect-demo.md` is
distributed as a prebuilt, unsigned `.app` bundle so presenters can
install and launch it without opening Xcode or configuring a signing
team. Two scripts under `clients/ios/scripts/`:

- `build_ios_simulator_app.sh` — developer runs this after any change
  to Swift source or `Staging.xcconfig`. Produces
  `clients/ios/build/MeridianMotorsCompanion-ios-sim-<version>-<date>-<sha>.app.zip`
  in ~30-90 s. No code signing required (Simulator builds are
  unsigned). Fully non-interactive.
- `install_ios_sim_demo_app.sh` — presenter runs this before each demo.
  Boots a simulator if none is booted, unpacks the newest `.app.zip`,
  installs via `xcrun simctl install`, launches via
  `xcrun simctl launch`. Under 5 s if the simulator is already booted.

Cadence: rebuild only when iOS source or `Staging.xcconfig` /
`Staging.local.xcconfig` changes. The zip is reusable across as many
demos as needed. Both scripts live under `clients/ios/scripts/` (which
is developer tooling, not part of the deployed guidance) and produce
artifacts under `clients/ios/build/` (gitignored). See
`docs/runbooks/ios-connect-demo.md` Step 2 for the presenter-facing
invocation.

**First-time setup on a fresh clone**: `build_ios_simulator_app.sh`
bakes xcconfig values into the produced `.app.zip` at build time. If
`Staging.local.xcconfig` is missing, the zip is built against the
tracked-file placeholders (`<REPLACE_WITH_USER_POOL_ID>` /
`YOUR_COGNITO_APP_CLIENT_ID`) and the installed app fails at Cognito
sign-in with `ResourceNotFoundException`. Bootstrap the local xcconfig
per `clients/ios/README.md § Configuration` before the first
`build_ios_simulator_app.sh` on a new checkout.

## Post-deploy validation

After running `deploy-all` + `bootstrap-demo` (or `seed-all-demo-data`), use the **publish-gate validator** to confirm the deploy is healthy before sharing the environment, cutting a release tag, or running customer demos. *(Retired 2026-09-06: `deploy-bedrock-agents` is now a no-op stub — the VFO Bedrock Agents stack was removed per spec `2026-09-05-cms-vfo-teardown`.)*

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

## OEM Telemetry Processor — committed-offsets check before C1 deploy

For the **first deploy** of `OEMTelemetryProcessor` after spec `2026-09-12-cs-simulator-oem2-manifest-path`'s C1 change (KafkaSource starting-offset strategy moved from `OffsetsInitializer.latest()` to `OffsetsInitializer.committedOffsets(OffsetResetStrategy.EARLIEST)`), follow the dedicated runbook: [OEM Telemetry Processor — C1 Deploy](runbooks/oem-telemetry-processor-c1-deploy.md).

Skipping the pre-deploy committed-offsets check on a consumer group with no committed offsets replays the **entire Kafka retention** of `cms-telemetry-oem` into `cms-telemetry-preprocessed`, manufacturing duplicate trip/safety/maintenance/geofence events downstream. The check takes 30 seconds. Also covers the C2/C3 deploy-order dependency (C1 must stabilize before pattern subscription and topic-derived identity activate) and the R7 negative-control smoke test.

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
8. **CMS_DEMO_DEFAULT_PASSWORD** — Optional. The env var is no longer required by the CDK stack; demo credentials are now managed in Secrets Manager. Set this only if you are using the staging demo-login buttons, which read it from the frontend bundle.
9. **Git working tree** — Verifies no uncommitted changes (clean state required for reproducible CDK).
10. **CDK synth** — Runs a dry-run synth to catch structural errors early.

**Common fixes:**
- Bootstrap missing: `make -C deployment bootstrap-staging`
- VPC quota low: Increase via AWS Service Quotas console, or delete unused VPCs in us-west-2
- Bedrock model error "Legacy model… 30-day inactivity": The model needs to be re-enabled. Bump `BEDROCK_AGENT_MODEL` in `deployment/Makefile` to current Sonnet (check AWS Bedrock docs for the latest ID).
- Docker not running: Start Docker Desktop **or** use a daemonless drop-in (finch / podman) — see [Daemonless container builder](#daemonless-container-builder-finch--podman)
- Missing env var: `CMS_DEMO_DEFAULT_PASSWORD` is no longer required for deploy; set it only if using staging demo-login buttons. Use `aws secretsmanager get-secret-value --secret-id cms-<stage>-demo-user-password --query SecretString --output text` to retrieve the deployed credential.

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
# Validate the Makefile default region
# (deployment/Makefile:46 falls back to us-west-2 when the profile sets no region)
MODEL_ID=us.anthropic.claude-sonnet-4-6 REGION=us-west-2 PROFILE=default \
  deployment/scripts/validate-bedrock-model.sh

# Or via the dedicated Make target
make -C deployment validate-bedrock-model \
  BEDROCK_AGENT_MODEL=us.anthropic.claude-sonnet-4-6 \
  AWS_REGION=us-west-2 AWS_PROFILE=default
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

As of 2026-08-05, `CMS_DEMO_DEFAULT_PASSWORD` is no longer required for synth or deploy. The demo credential is now managed in Secrets Manager (`cms-<stage>-demo-user-password`).

If you need to retrieve the current demo password for any reason:

```bash
aws secretsmanager get-secret-value --secret-id cms-staging-demo-user-password \
  --region us-west-2 --query SecretString --output text
```

This is an IAM-gated, CloudTrail-audited operation — the preferred way to access the credential after deployment.

**Historical note (2026-06-03):** Before the rotation, unsetting `CMS_DEMO_DEFAULT_PASSWORD` caused spurious password-property changes in `cdk diff` output. This is no longer an issue.


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
| prod    | *(n/a — archived)*     | no prod environment exists since 2026-09-04 |

When `uiCustomDomainRegion` is UNSET, behavior is exactly as today
(region-agnostic attach when the pair is set). This preserved the historical
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
- Wrong region/account used because `AWS_REGION` / `DEPLOYMENT_STAGE` defaulted

**Canonical inline-prefix pattern** (recommended for one-shot invocations):

```bash
DEPLOYMENT_STAGE=staging \
AWS_REGION=us-west-2 \
CMS_DEMO_DEFAULT_PASSWORD='your-staging-password' \
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

### DMS Fleet Authorization Integration (R2b)

`GET`/`POST /api/v1/service-history` and `GET /api/v1/dealers` reach DMS server-side and
forward **the caller's own Cognito token**, so DMS authorizes the end user and CMS makes no
fleet-scope decision of its own (spec D2/D3 — a CMS-side scope decision is the shape of the
2026-08-05 fail-open P0). CMS keeps its existing checks only on its own cache table, which
DMS never sees. There are no `/dms/*` routes in CMS — spec `2026-08-31-dms-standalone-ui`
moved that UI to the standalone DMS console.

Three points on the current state, each with its source of truth:

1. **An unset `dmsApiEndpoint` leaves the integration INERT, not fail-closed** — CMS's
   `service-history` table stays the primary store and no provenance envelope is emitted.
   Only a non-https value fails closed (503). Nothing throws at startup. This is what makes
   the change deployable before the backfill runs, and the state most easily mistaken for
   "working".
   <!-- verify: grep -n "LEGACY path — DMS_API_ENDPOINT is unset" modules/cms_ui/source/handlers/main_api/index.py -->
2. **The backfill's `--apply` has NOT been run** in any environment; it is a manual operator
   step and dry-run is the default.
   <!-- verify: grep -n -- "--apply" ../guidance-for-dealer-management-system-on-aws/deployment/scripts/backfill_service_history.py -->
3. **Backfilled ROs are invisible to non-admin callers.** They are stored with
   `vehicle_vin = vehicleId` while DMS's fleet-scoped list resolves scope through a projection
   keyed on the real VIN. Open finding `F-G5a`, not designed behaviour. CMS tolerates it by
   including its own cache rows and labelling the response `dataSource: "mixed"`, so the label
   is also a backfill-completeness signal.
   <!-- verify: grep -n 'vehicle_vin.*vehicle_id' ../guidance-for-dealer-management-system-on-aws/deployment/scripts/backfill_service_history.py -->

**Links from CMS into the DMS console.** A dispatched diagnostic session's **View RO**, and
the dispatch confirmation, open DMS's read-only fleet repair-order page
(`<DMS origin>/fleet/repair-orders/<roId>`) in a new tab. CMS learns the DMS origin from
`runtimeConfig.dmsUiOrigin`, which `make regenerate-runtime-config` writes from
`DMS_UI_CALLBACK_ORIGIN`: the environment first, then `deployment/config/<stage>.env`, the
same key that adds DMS to the Cognito callback allowlist. The value must be a bare `https://`
origin; anything else fails the target before it uploads. When the key is absent, CMS shows
the repair-order id as plain text rather than a link.
<!-- verify: grep -n "dmsUiOrigin" deployment/Makefile modules/cms_ui/source/frontend/src/utils/dmsLinks.ts -->

#### Response envelope (spec 2026-09-10-service-history-read-path-correctness)

`GET /api/v1/service-history` emits a three-value `dataSource` enum, plus
`cacheOnlyCount` and per-row `provenance`:

- `dataSource: "live"` — DMS answered, no cache-only rows.
  **`asOf` is omitted from the envelope entirely** (not `null`).
  `cacheOnlyCount: 0`.
- `dataSource: "cache"` — DMS unreachable, response served from cache only.
  `asOf` = oldest cache row.  `cacheOnlyCount` = row count.
- `dataSource: "mixed"` — DMS answered AND at least one cache-only row exists
  in the response.  `asOf` = **oldest** cache-only row's timestamp (worst-case
  staleness).  `cacheOnlyCount` > 0.

Each `serviceRecords[]` entry carries `provenance: "dms" | "cache"`.  Callers
that need to distinguish per-row freshness (e.g., a UI badge on stale rows)
read this instead of comparing timestamps to `asOf`.

**Vehicle-scoped GETs route through the `vin-index` GSI at DMS.**  When a
`vehicleId` resolves to a VIN, CMS appends `?vehicle_vin=<vin>` to the DMS
URL; DMS's `list_fleet_repair_orders` queries the GSI once for that VIN
instead of scanning 100-of-43,200 rows.  A client-side VIN filter on the
CMS side no longer exists — DMS filters server-side and CMS trusts the
answer.  This closes the "a fresh booking is invisible on the next GET"
defect (issue
`2026-09-10-scoped-service-history-get-reads-one-fixed-page-of-43200-dms-repair-orders`).
<!-- verify: grep -n "_dms_query = '?vehicle_vin=" modules/cms_ui/source/handlers/main_api/index.py -->


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
| ~~`cms-{stage}-vfo-knowledge-base-{region}-{account}`~~ | ~~`bedrock_agents_stack.py`~~ | (Retired 2026-09-06 per spec `2026-09-05-cms-vfo-teardown` — bucket deleted, stack removed) |

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
| ~~`cms-prod-ui-frontend-123456789012-us-east-1`~~ | `ui_stack.py:498` | **N/A — bucket and stack deleted 2026-09-04.** The pending-replacement warning below is moot: `cms-prod-ui` no longer exists, and the live bucket it referred to was emptied and deleted during the teardown. |

> **⚠️ The remainder of this section is ARCHIVED.** It documents an operator
> procedure against `cms-prod-ui` in `us-east-1`, a stack torn down on 2026-09-04
> per `.kiro/specs/2026-09-04-cms-prod-teardown/`. **CMS is single-environment;
> there is no prod stack to rename a bucket in.** The procedure is retained because
> the *pattern* — orphan-and-replace on a globally-named bucket — still applies to
> `cms-staging-*` and to any future re-created environment. Read the commands as
> illustrative, not runnable: every `--stack-name cms-prod-ui --region us-east-1`
> in what follows targets something that no longer exists.
> <!-- verify: aws cloudformation describe-stacks --stack-name cms-prod-ui --region us-east-1 -->

### Renaming a UI FrontendBucket (EXECUTED for prod — 2026-08-03/04)

**Status: complete. Retained here as the reference procedure, not as pending work.**

The prod rename fired on 2026-06-19 under the staging-to-prod promotion runbook; the
live bucket is `cms-prod-ui-frontend-<account-id>-us-east-1` (deterministic,
region-suffixed per `cross-region-namespace.md`). The pre-rename bucket
`cms-prod-ui-frontend-<account-id>-1777830107` — whose `1777830107` suffix was the
`int(time.time())` from the old non-deterministic naming — was the orphan left behind,
and it was deleted on 2026-08-04 after confirming it was unreferenced by any prod
CloudFormation stack and was not a CloudFront origin. It held 19 objects / ~30 MB of a
superseded frontend build; a manifest was captured before deletion.

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

## Customer Rebrand Migration (Prod Only)

**Status (2026-08-03):** The CMS engineering-persona demo was rebranded from real customer/OEM names to fictional equivalents per spec `2026-08-03-customer-rebrand`. The **visible-label rebrand is complete** (all UI, documentation, seed scripts updated). The **production DynamoDB migration has been applied and verified** (225/283 vehicles, 2/8 fleets).

### Rebrand Mapping

The following **visible display strings** were renamed to fictional equivalents:

| Display label used | Where |
|--------------------|-------|
| **Meridian** | All UI labels, seed scripts, documentation (EV OEM persona) |
| **Windrose** | Fleet names, vehicle model labels, documentation |
| **Trailwind** | Fleet names, vehicle model labels, documentation |
| **Cascadia** | CVX agent config, display strings ("Cascadia Fleet Services") |
| **Halcyon** (display only) | UI dealership labels ("AutoNation Halcyon"), vehicle model ("Halcyon Stratus") |

### What Remains Unchanged (Intentional)

The following **machine identifiers and factual data** are NOT renamed — renaming them would break live systems or falsify public-domain data:

- **Tenant slugs:** `cascadia`, `ford` remain as Cognito/DDB keys (renaming requires live claim rewrites — out of scope)
- **Fleet/Vehicle IDs:** `be6-prod-cohort-001`, `be07-test-fleet-001`, `VEH-BE6-*`, `VEH-BE07-*` VIN prefixes (225 prod vehicles reference these)
- **Model manifests:** `BE6-V12-PROD`, `BE07-V13-DEV` ECU config names (loaded by live Flink processors)
- **NHTSA regulatory data:** `nhtsaRecallData.ts` retains real Ford/Chevy/Toyota vehicle makes (public-domain regulatory records, not customer canaries)
- **Historical audit trail:** `.kiro/specs/**` and `issues/**` retain original names for version-control history

These are recorded as architectural decisions in spec `2026-08-03-customer-rebrand/spec.md` § Decision, not oversights.

### Production Migration Script

> **⚠️ ARCHIVED — this migration can no longer run as written.** It targets
> `cms-prod-storage-vehicles` and `cms-prod-storage-fleets` in `us-east-1`, both
> **deleted 2026-09-04** with the rest of the prod environment (spec
> `.kiro/specs/2026-09-04-cms-prod-teardown/`). CMS is single-environment; the
> equivalent live tables are `cms-staging-storage-*` in `us-west-2`. Retained
> because the rebrand mapping above and the `--dry-run`/`--apply` discipline are
> still the reference pattern — but **substitute the staging table names and region
> before running anything below.**
> <!-- verify: aws dynamodb list-tables --region us-east-1 --query 'TableNames[?starts_with(@,`cms-prod-storage-`)]' -->

The prod DynamoDB migration was applied via `deployment/scripts/rebrand_visible_brand_values.py`:

**Purpose:** Update vehicle and fleet name fields in prod DynamoDB tables (`cms-prod-storage-vehicles`, `cms-prod-storage-fleets`) from legacy display values to Meridian/Windrose/Trailwind equivalents.

**Usage (dry-run, safe to preview):**

```bash
# Preview changes to vehicles table
DEPLOYMENT_STAGE=prod AWS_REGION=us-east-1 \
  python3 deployment/scripts/rebrand_visible_brand_values.py \
  --table cms-prod-storage-vehicles --region us-east-1 --dry-run

# Preview changes to fleets table
DEPLOYMENT_STAGE=prod AWS_REGION=us-east-1 \
  python3 deployment/scripts/rebrand_visible_brand_values.py \
  --table cms-prod-storage-fleets --region us-east-1 --dry-run
```

**Usage (apply changes — REQUIRES EXPLICIT `--apply` flag):**

```bash
# Apply to vehicles table
DEPLOYMENT_STAGE=prod AWS_REGION=us-east-1 \
  python3 deployment/scripts/rebrand_visible_brand_values.py \
  --table cms-prod-storage-vehicles --region us-east-1 --apply

# Apply to fleets table
DEPLOYMENT_STAGE=prod AWS_REGION=us-east-1 \
  python3 deployment/scripts/rebrand_visible_brand_values.py \
  --table cms-prod-storage-fleets --region us-east-1 --apply
```

**Behavior:**
- **Dry-run (default):** prints a table showing which rows WOULD be updated, with before/after values. No DDB writes.
- **Apply mode:** issues `update_item` calls for each eligible row. Before/after records are written to `rebrand_migration_<table>_<timestamp>.json` for audit/reversal.
- **Idempotent:** rows already at target values are skipped. Safe to re-run.
- **Attribute allowlist:** only `make` and `name` (vehicles table) or `name` (fleets table) are modified. Partition/sort keys, `fleetId`, `vehicleId`, `ecuConfigId` are never touched.

**Applied 2026-08-03:**
- `cms-prod-storage-vehicles`: 225 rows rebranded (name: "Legacy EV #NNNN" → "Meridian Windrose #NNNN", make: "<prior-value>" → "Meridian")
- `cms-prod-storage-fleets`: 2 rows rebranded (name: "BE 6 Production Cohort" → "Windrose Production Cohort", "BE.07 Validation Fleet" → "Trailwind Validation Fleet")
- Before/after records: `deployment/scripts/rebrand-migration-records/` (publish-excluded)

**Pre-deploy checklist (if re-running the migration):**

```bash
# Verify script syntax
python3 -c "import ast; ast.parse(open('deployment/scripts/rebrand_visible_brand_values.py').read()); print('✓ syntax OK')"

# Always dry-run first
DEPLOYMENT_STAGE=prod AWS_REGION=us-east-1 \
  python3 deployment/scripts/rebrand_visible_brand_values.py \
  --table cms-prod-storage-vehicles --region us-east-1 --dry-run

# Confirm output looks reasonable before --apply
```

### Public-Mirror Implications

**The public-mirror audit prerequisite is NOW SATISFIED** for the visible-label half. The CMS UI may now be deployed to a publicly-reachable CloudFront URL without customer brand exposure in user-facing labels. See `docs/SECURITY-AUDIT-FINDINGS.md` § "Remaining customer-specific surface" for the updated disposition.

**Scanner guards:** The `.publish-secrets-scan.yml` forbidden-strings entries are reintroduction guards for legacy display names. The fictional names (Meridian, Cascadia, Halcyon, Windrose, Trailwind) are public-safe and must NOT be added to the forbidden list.

---

## Deploy Commands

### ⚠️ Narrow make targets still synth the WHOLE app — read before picking a target

**Choosing a narrow target to limit blast radius does the opposite.** `deployment/app.py`
instantiates *every* stack regardless of which target you invoke, so any target —
`data-processing`, `deploy-simulation`, `deploy-connected-services`, anything — evaluates
`ui_stack.py`'s guards **and can deploy `cms-<stage>-ui` as a dependency**.

**On 2026-09-15 `make data-processing DEPLOYMENT_STAGE=staging` deleted six Federate
auto-provisioning resources** from `cms-staging-ui` — the provisioning Lambda, its role, policy
and alarm, and *both* the `PostConfirmation` and `PostAuthentication` Cognito triggers — leaving
`UserPool.LambdaConfig` empty for ~25 minutes on the public production surface. Sign-in kept
working; auto-provisioning of new Federate users did not. Full write-up:
`issues/2026-09-15-narrow-deploy-target-deleted-federate-auto-provisioning/`.

**Sourcing `config/<stage>.env` is necessary but NOT sufficient.** Three separate inputs are
required, and only `phase1` supplies all three:

| Input | How `ui_stack.py` reads it | Supplied by |
|---|---|---|
| `CLIENT_EXTRA_IDPS` | environment variable | `set -a; . config/<stage>.env; set +a` |
| `FEDERATE_CLIENT_ID` / `FEDERATE_CLIENT_SECRET` | environment variables | `. scripts/load-federate-creds.sh` (resolves `FEDERATE_OIDC_SECRET_ID` via Secrets Manager) |
| `CMS_ENABLE_INTERNAL_AUTO_PROVISIONING` | **dotted CDK context key**, not the env var | **`phase1` only** — it translates env→context at `Makefile:765` |

That third row is the trap. The value *is* correctly set at `config/staging.env:87`, and
`set -a` exports it — but `ui_stack.py` reads a **CDK context key**, so nothing consumes the
exported variable and the provisioning resources synth away.

**Therefore: prefer `phase1` over any narrow target that can touch the ui stack.** It is the
wider deploy, and it is the safe one. If you must run a narrow target, use the full form:

```bash
cd deployment && ( set -a; . config/staging.env; set +a
  . scripts/load-federate-creds.sh
  make <target> DEPLOYMENT_STAGE=staging )
```

…and understand this still does **not** protect the provisioning resources. Verify afterwards:

```bash
aws cognito-idp describe-user-pool --user-pool-id <pool-id> --region us-west-2 \
  --query 'UserPool.LambdaConfig'
```

A non-empty `LambdaConfig` with both triggers means they survived. Empty means they were
deleted — restore with `make phase1`, which may first require clearing the orphaned log group
per `issues/2026-09-13-phase1-blocked-orphaned-provisioning-log-group/summary.md`.

#### Better than either: `--exclusively`, gated on a dependency-stack `cdk diff`

Added 2026-09-16. The advice above — "prefer `phase1`" — trades a 7-resource deletion for a much
larger blast radius, which is the wrong trade when all you want is one Lambda and two IAM
statements. There is a third option that is both narrow and safe.

**Why the trap exists at all**: CDK deploys not only the stacks you name but every stack they
depend on. `data_processing_stack.py` imports the CMS user-pool ARN via
`Fn.import_value(f'cms-{stage}-ui-user-pool-arn')`, so `cdk deploy cms-staging-data-processing`
also deploys `cms-staging-ui` — synthesised with only *that* target's context flags.
`--exclusively` (`-e`) tells CDK to deploy the named stack and **ignore dependency stacks**. Safe
whenever the dependency's exports already exist in the deployed stack, which is the normal case for
an update.

**The pre-flight is what makes this a decision rather than a hope.** Before any narrow deploy, diff
the dependency stack, not the one you are deploying:

```bash
cd deployment
CTX="$(...)"   # the full phase1 flag set, or just run the diff twice and compare
( set -a; . config/staging.env; set +a
  . scripts/load-federate-creds.sh
  cdk diff cms-staging-ui $CTX --profile default ) | grep -E '^\[-\]'
```

Any `[-]` line is a resource your narrow deploy would **delete**. Zero `[-]` lines means the
dependency stack is unaffected and the deploy is safe to scope narrowly.

> **If that diff dies with `FileExistsError: ... deployment/stacks/.build/<name>`, do not delete the
> directory.** Two concurrent CDK invocations share that asset-staging path regardless of `--output`,
> so the likeliest cause is another session mid-synth, and deleting it corrupts their run. Check
> first: `ps aux | grep -E 'cdk|app\.py' | grep -v grep`. If a live process appears, wait. Filed as
> `issues/2026-09-16-concurrent-cdk-synth-collides-on-shared-build-dir/`.

Worked example, 2026-09-16, deploying only the campaign-ownership guard:

| Invocation | `cms-staging-ui` diff |
|---|---|
| `make data-processing` (with `staging.env` + Federate creds — the documented form above) | **7 removals** — both Cognito triggers, ProvisioningLambda, its role, policy, alarm, plus an orphaned log group |
| same, plus the full 13-flag `phase1` context set | 0 removals, but **3 unwanted modifications** — a `FrontendDeployment` replacement that would overwrite whatever `ui-quick-deploy` last shipped, an unrelated Lambda's code asset from a concurrent change, and a cosmetic MSK output reorder |
| `cdk deploy cms-staging-data-processing --exclusively $CTX` | **untouched** |

The third row is the one to copy:

```bash
cd deployment && ( set -a; . config/staging.env; set +a
  . scripts/load-federate-creds.sh
  cdk deploy cms-<stage>-<stack> --exclusively $CTX \
    --require-approval never --profile default )
```

Confirm the changeset is what you expect *before* running it — for that deploy it was 0 removals,
0 new resources, 4 modifications (two IAM statements, one env var, the code asset).

**Then verify the artifact, not the exit code.** A clean `cdk deploy` says CloudFormation
succeeded, not that your change is live. Check the deployed thing directly — Lambda
`LastModified` moved, the new env var is present, the new IAM statement appears in the role's
inline policy, an unauthenticated probe still returns 401 so the authorizer survived, and
CloudWatch is clean for ten minutes after. See
`~/.kiro/steering/deploy-validation.md` § "Committed, deployed, and live-verified are three
different claims".

**Related known gap**: the guard meant to catch context-threading defects
(`deployment/stacks/tests/test_cdk_context_key_threading.py`) covers 3 of the 22 Makefile targets
that run `cdk deploy` and models no dependency-stack deploys, so it is green for every case in the
table above. Filed as `issues/2026-09-16-context-key-lint-covers-3-of-19-deploy-targets/` — the
issue slug says 19 because that was the count when it was filed; the denominator has since grown
to 22 and will keep growing, which is the point. Adding `--exclusively` to narrow targets would
make that guard's model true as a side effect.

<!-- verify: cd deployment && awk '/^[a-zA-Z0-9_.-]+:/ && $1 != ".PHONY:" {t=$1} /cdk deploy/{if(t!="")print t}' Makefile | sort -u | wc -l -->


### Staging (us-west-2)

```bash
bash deployment/scripts/preflight-staging.sh  # ~30s, read-only
make -C deployment staging-deploy            # ~45 min, real AWS resources
```

**Expected outcome:**
- All 12 CMS staging stacks (`data-processing`, `storage`, `iot`, `ui`, `msk`, `telemetry-integration`, `flink`, `fleetwise`, `simulation`, `commands`, `ws-fanout`, `tco`) in `CREATE_COMPLETE` or `UPDATE_COMPLETE` state in us-west-2
- CloudWatch logs show no `ERROR` or `Traceback` in the last 2 minutes
- Estimated daily cost while running: ~$50–$150/day (MSK ~$15–$30, Flink ~$10–$20, 3x NAT Gateway ~$4, rest negligible)

**Deploy ordering and cautions** (per spec `2026-06-18-cms-fwe-decoder-manifest-bucket-resolution`):
- **FWE decoder manifest is now stack-managed:** the manifest (`DecoderManifest.bin`) is committed at `deployment/fwe-config/DecoderManifest.bin` and deployed into the Flink jar bucket via CDK `BucketDeployment` on every `cms-<stage>-flink` deploy. To regenerate: `DRY_RUN=1 python3 deployment/scripts/generate_decoder_manifest.py` to validate, then remove `DRY_RUN=1` to commit the new `.bin`, then commit to git. **Do NOT use out-of-band manual uploads** — they cause bucket drift (the 2026-06-18 staging outage).
- **Deploy ordering:** always deploy `cms-staging-flink` (carries the manifest and Java processor changes) **before** `cms-staging-simulation` (uses the same manifest). As of 2026-09-25 (`2026-09-25-cms-fleetwise-consolidation`), `cms-staging-fleetwise` deploys unconditionally and should be deployed before or with `cms-staging-simulation` to ensure the FWE decoder is available to agents before they launch.
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

### Demo credential rotation

As of 2026-08-05, demo Cognito credentials (`FleetManager@example.com`, `service-advisor@example.com`, etc.) are managed in AWS Secrets Manager, not CloudFormation. CMS is single-environment, so there is one secret:

- **Staging** (the only environment): `cms-staging-demo-user-password` (us-west-2)

> The former prod counterpart `cms-prod-demo-user-password` (us-east-1) no longer
> exists — the prod environment was torn down per spec
> `.kiro/specs/2026-09-04-cms-prod-teardown/`. The only `cms-prod` secrets that
> outlived the teardown are `cms-prod-demo-persona-passwords` and
> `cms-prod-federate-oidc`, both scheduled for deletion on 2026-10-05.
>
> <!-- verify: aws secretsmanager list-secrets --region us-east-1 --include-planned-deletion --query 'SecretList[?starts_with(Name,`cms-prod`)].[Name,DeletedDate]' --output text -->

**Retrieving the current credential:**

```bash
aws secretsmanager get-secret-value --secret-id cms-staging-demo-user-password \
  --region us-west-2 --query SecretString --output text
```

**Rotating to a new credential:**

```bash
# Generate a new random password (24 chars, satisfies Cognito pool policy)
NEW_PASSWORD=$(aws secretsmanager get-random-password \
  --password-length 24 --require-each-included-type \
  --query SecretString --output text)

# Update the secret (creates a new version)
aws secretsmanager put-secret-value \
  --secret-id cms-staging-demo-user-password \
  --secret-string "$NEW_PASSWORD" \
  --region us-west-2

# Trigger a stack update to apply the password
# The custom resource is keyed to the secret version ID, so a new version
# causes CloudFormation to send an Update event and re-set the password.
make -C deployment staging-deploy DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2
```

**Confirming the rotation:**

The rotation is complete when `UserLastModifiedDate` for `FleetManager@example.com` changes:

```bash
# Before rotation, note the initial timestamp
aws cognito-idp admin-get-user --user-pool-id "<user-pool-id>" \
  --username FleetManager@example.com --region us-west-2 \
  --query UserLastModifiedDate --output text

# After the stack update completes, this timestamp will be newer
aws cognito-idp admin-get-user --user-pool-id "<user-pool-id>" \
  --username FleetManager@example.com --region us-west-2 \
  --query UserLastModifiedDate --output text
# expected: timestamp later than the baseline
```

**Troubleshooting: "already scheduled for deletion"**

If a teardown + redeploy cycle is attempted while a secret is in the deletion blackout window (Secrets Manager deletes named secrets after 7–30 days):

```
InvalidRequestException: Secret … already scheduled for deletion
```

**Resolution:**
- Option 1: Wait out the deletion window (typically 7 days; check the console for the deletion date).
- Option 2: Force-delete the secret before redeploying (this is irreversible and is appropriate only for demo credentials):

```bash
aws secretsmanager delete-secret --secret-id cms-staging-demo-user-password \
  --force-delete-without-recovery --region us-west-2
```

Then redeploy:

```bash
make -C deployment staging-deploy DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2
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

### Prod (us-east-1) — ARCHIVED, no such environment

> `make prod-deploy` has no live target. See the notice at the top of this guide.
> Retained as the reference procedure for re-creating a prod environment.

Prod deploys are manual and require stricter approval:

```bash
make -C deployment prod-deploy  # Prompts for confirmation unless CI=true
```

On laptop: you'll be prompted to confirm. In CI, set `CI=true` to skip the prompt (this is forward-looking — when GitLab CI is wired up).

**Key differences from staging:**
- Deploys to `us-east-1` instead of us-west-2
- Uses a separate IAM role scoped to us-east-1 (provisioned when GitLab CI lands)
- Manual approval required (today: human running `make prod-deploy` is the gate; future: GitLab CI environment protection)
- Tier 3 eval suite runs as a smoke-test only (subset of cases, tighter latency budgets)

## Account provisioning

This section documents the account provisioning and lifecycle management strategy for CMS, covering identity model setup, user group assignment, and the transition to external self-service registration.

### Phase A: Federate auto-provisioning (shipped, staging + prod ready)

Phase A delivers zero-touch account provisioning for Amazon internal users via AmazonFederate federation.

#### Feature gate

The internal auto-provisioning trigger is **opt-in and disabled by default**. To enable:

**Set the context flag during deploy:**
```bash
cd deployment
make phase1 DEPLOYMENT_STAGE=staging -c cms.enable_internal_auto_provisioning=true
```

Or configure per-stage:

**Staging** (`deployment/config/staging.env`, publish-excluded):
```
CMS_ENABLE_INTERNAL_AUTO_PROVISIONING=true
INTERNAL_IDP_PROVIDER_NAME=AmazonFederate
INTERNAL_AUTO_ASSIGN_GROUP=platform-admin
```

**Prod** (`deployment/config/prod.env`, publish-excluded):
```
# Phase A: Federate users are auto-assigned platform-admin.
# Commented out by default; uncomment after fail-open fix is deployed
# and Phase A de-privilege runbook completes (see § "Phase A prod actions" below).
# CMS_ENABLE_INTERNAL_AUTO_PROVISIONING=true
INTERNAL_IDP_PROVIDER_NAME=AmazonFederate
INTERNAL_AUTO_ASSIGN_GROUP=platform-admin
```

#### How it works

When enabled, the provisioning Lambda is invoked on **two Cognito triggers**:

1. **`PostConfirmation_ConfirmSignUp`** — fires on the first federated sign-in. At this point, the user has no groups yet. The trigger invokes `AdminAddUserToGroup` to assign `INTERNAL_AUTO_ASSIGN_GROUP` (default: `platform-admin`) **before** the token is issued.

2. **`PostAuthentication_Authentication`** — fires on **every subsequent sign-in**, including password-based and federated. For existing Federate users (like the 9 Amazon-domain admins already in the pool), this trigger reasserts the group so it persists if an administrator has removed it.

**Why both triggers are necessary** (see `spec.md` § Decisions, 2026-08-10):

- Post-confirmation alone cannot reach existing federated users on their next sign-in; post-authentication can.
- Post-authentication alone cannot provision a brand-new federated user on their first sign-in (post-authentication does not fire until after the user record exists, which is too late for the first token).

Only users matched via `INTERNAL_IDP_PROVIDER_NAME` receive the group assignment. The `custom:provisionedVia` attribute is set to `"federate"` to record the provisioning source.

#### Group assignment policy

The current prod configuration assigns `platform-admin` to any Amazon employee who authenticates via AmazonFederate. This is the **reference template default** and is **appropriate for a demo/poc environment** where the internal user population and trust boundary are known. For production deployments:

- Customers deploying this template should override `INTERNAL_AUTO_ASSIGN_GROUP` to a narrower group (e.g., `fleet-viewer` or a customer-defined group).
- An organization-wide allowlist via `INTERNAL_ALLOWED_EMAIL_DOMAINS` is available in the template but is not used by Amazon (Federate itself gates the population).

#### Audit: custom:provisionedVia attribute

The `custom:provisionedVia` custom attribute records how a user was provisioned and is **immutable after first set**:

```
custom:provisionedVia values:
- "federate" — user provisioned via the internal Federate trigger (Phase A)
- "self-service" — user provisioned via external self-signup (Phase B)
- (unset) — user predates provisioning, or was admin-created manually
```

**Audit query example** — list all Federate-provisioned users:

```bash
aws cognito-idp list-users \
  --user-pool-id <user-pool-id> \
  --filter 'custom:provisionedVia = "federate"' \
  --region us-east-1 \
  --query 'Users[].[Username, Attributes[?Name==`email`].Value|[0]]' \
  --output table
```

#### Fail-closed synthesis guards

Two CDK synth-time guards prevent misconfiguration:

1. **`_require_internal_provisioning_config()`** — fails if `cms.enable_internal_auto_provisioning=true` but `INTERNAL_AUTO_ASSIGN_GROUP` is unset. A groupless trigger is a defect.

2. **`_require_provisioning_guards()`** — comprehensive Phase B precondition gate (see below), including the source-tree check that the fail-open authz fix is present.

Run `cdk synth` with the flag to verify the guards fire:

```bash
cd deployment && set -a && . config/staging.env && set +a && \
  npx cdk synth cms-staging-ui -c cms.enable_internal_auto_provisioning=true
```

If the synth succeeds, the config is valid.

### Phase B: External self-signup (not yet available — gate currently RED)

Phase B enables self-service registration for external users with email verification and pre-sign-up controls (denylist, WAF rate limiting).

**Status: Phase B implementation has not begun.** The blocker gate is currently RED with 4 outstanding preconditions:

| # | Blocker | Current Status | What it means |
|---|---------|----------------|---------------|
| #1 | Fail-open authz fix deployed to prod | RED | Deployed Lambda is stale (commit `d235fb31` is in the tree but not yet deployed to prod). Remediation: `make phase1 DEPLOYMENT_STAGE=prod` after fail-open fix is confirmed deployed. |
| #2 | Prod CloudFront WAF WebACL attached | RED | Prod CloudFront distribution has no WAF (Group 7's WAF stack not yet deployed). Remediation: deploy cms-<stage>-ui-waf stack. |
| #3 | `cms.allow_self_signup=true` in cdk.context.json | RED | Context flag is unset (defaults false). Remediation: set after all other blockers are GREEN and the operator deliberately enables external signup. |
| #4 | Brand/PII audit complete (`Demo external exposure`) | RED | Runtime audit for prod has not been performed. Remediation: complete the backlog row `Demo external exposure`, create `issues/2026-08-DD-cms-demo-external-exposure/summary.md`, and mark `Status: RESOLVED`. |

**How to check blocker status:**

```bash
cd deployment
python3 scripts/phase_b_blocker_gate.py --stage prod
```

Output format:

```
Overall gate: RED
| # | Check | Status | Notes |
|---|-------|--------|-------|
| #1 | Source-tree (committed) | GREEN | ...
| #1 | Deployed Lambda (runtime) | RED | ...
| #2 | Prod CloudFront WAF WebACL | RED | ...
| #3 | cdk.context.json cms.allow_self_signup | RED | ...
| #4 | Brand/PII audit (issues dir) | RED | ...

Blocker Details:
...

Gate Conclusion: Gate is RED. Phase B implementation cannot proceed until all blockers are GREEN.
```

**When the gate turns GREEN:**
1. All four blockers must be satisfied (source-tree check GREEN, deployed Lambda check GREEN, context flag GREEN, audit complete GREEN).
2. A fifth precondition (not yet automated) must also be satisfied: the Cognito `UserPoolClient` `write_attributes` must be restricted to a minimal allowlist (at most `email`) — see `spec.md` § Decisions "Phase B gains a fifth precondition" for details.

**Phase B design (not yet deployed):**
- Self-signup is restricted to the UI's signup form (`PreSignUp_SignUp` trigger path only).
- Pre-sign-up validation checks a managed denylist of disposable mail providers to prevent registration of temporary email accounts.
- Signup is rate-limited by the **user-pool** WAF (`cms-<stage>-pool-waf`), not the CloudFront one — see § "Cognito user-pool WAF". Registration never traverses CloudFront, so the distribution's `/signup*` rule cannot match it.
- Post-confirmation assigns new self-service users to the **`fleet-guest`** group, scoped to `EXTERNAL_SELF_SIGNUP_FLEET_IDS`. Not `fleet-viewer`: despite its name, `fleet-viewer` is an unscoped global-read role in `main_api` (`has_unscoped_access = is_admin or is_viewer`), so assigning it to self-registered users would grant read access to every fleet. See `decisions.md` 2026-08-10 "Phase B assigns `fleet-guest`, not `fleet-viewer`".
- SNS alerts notify operators of signup rate anomalies.

When Phase B ships, external users will be able to self-register with email verification. The opt-in gate is `cms.allow_self_signup=true`.

### WAF and rate limiting

There are **two** web ACLs, on two different surfaces. Both are needed and neither
substitutes for the other:

| Web ACL | Scope | Region | Protects |
|---|---|---|---|
| `cms-<stage>-ui-waf` | `CLOUDFRONT` | always us-east-1 | the SPA distribution (static assets) |
| `cms-<stage>-pool-waf` | `REGIONAL` | the pool's region | managed login, hosted UI, and user-pool API — i.e. sign-in and **signup** |

**Staging:** managed rule groups run in COUNT on both ACLs (metrics only, no blocking);
rate-based rules BLOCK.

**Prod:** the CloudFront ACL is deployed and attached, with all three managed groups in
BLOCK. The pool ACL blocks the IP-reputation and known-bad-inputs groups and keeps the
OWASP `CommonRuleSet` in COUNT — see § "Cognito user-pool WAF" for why that divergence is
deliberate.

To inspect either ACL's rules:

```bash
# CloudFront-scoped ACL (always us-east-1)
aws wafv2 describe-web-acl --name cms-<stage>-ui-waf --scope CLOUDFRONT \
  --region us-east-1 --id <acl-id> \
  --query 'WebACL.Rules[].[Name, Action, OverrideAction]'

# Pool-scoped ACL (the pool's region: us-west-2 staging, us-east-1 prod)
aws wafv2 list-web-acls --scope REGIONAL --region <pool-region> \
  --query "WebACLs[?Name=='cms-<stage>-pool-waf']"
```

Changing a rule's mode is a CloudFormation update: edit `stacks/waf_stack.py`
(CloudFront) or `stacks/cognito_pool_waf.py` (pool) and redeploy via the Make target.

### Prod edge-gate posture

Refer to § "Prod Edge-Gate Posture" for the current prod identity-model posture and compensating controls. Phase A and Phase B account provisioning work within Option B (prod stays publicly reachable, with compensating controls including WAF, MFA, and verified email signup).

### SNS operator subscription

The provisioning triggers alert an operator topic that is created **on every stage, whether
or not either trigger gate is enabled** — subscription is a manual step, and a topic that
only appears when Phase B is turned on is a topic nobody is subscribed to on the day Phase B
is turned on.

The topic is named after the UI stack, so it is `cms-<stage>-ui-security-operators`:

```bash
aws sns subscribe \
  --topic-arn arn:aws:sns:<region>:<account-id>:cms-<stage>-ui-security-operators \
  --protocol email \
  --notification-endpoint <operator-email> \
  --region <region>
```

Confirm the subscription from the email AWS sends. Note the region: the topic lives in the
UI stack's region (us-west-2 staging, us-east-1 prod), not necessarily us-east-1.

Three alarms publish to it, all with `TreatMissingData=notBreaching` because no invocations
is the normal state on a quiet demo stage:

| Alarm | Threshold | Why |
|---|---|---|
| `cms-<stage>-ui-provisioning-errors` | Errors > 0 in 5 min | Priority. Federated users are not getting their group, so new sign-ins land groupless — a lockout on every `main_api` route. One error is enough; there is no acceptable rate of failed authentication. |
| `cms-<stage>-ui-pre-signup-errors` | Errors > 0 in 5 min | Priority, and worse than it looks: that one trigger slot also serves first federated sign-in and admin account creation, so a failure blocks registration, employee federation and operator provisioning together. |
| `cms-<stage>-ui-pre-signup-denials` | Sum > 100 in 5 min | Informational. Denials are expected as a trickle; a spike suggests scripted signups or denylist enumeration. |

**Alarms exist only when their trigger Lambda does.** An alarm on an absent function would
sit in `INSUFFICIENT_DATA` forever while telling an operator that coverage exists. The
invariant that matters — *every Cognito trigger Lambda has an Errors alarm, and no alarm has
empty `AlarmActions`* — is enforced by `stacks/test_provisioning_alarms.py` against the
synthesised template, so adding a fourth trigger without monitoring fails the build.

The denials metric comes from a CloudWatch **metric filter** on the pre-sign-up log group
matching the bare token `presignup_denied`, not a JSON path. Metric filters read raw log
text and the Lambda Python runtime prefixes logger output, so a `{ $.outcome = "denied" }`
filter would rest on an unverified assumption about log formatting. The handler pins that
token and its shape with tests, because if the two sides drift the alarm goes quiet while
continuing to report OK.

## CloudFront WAF (`cms-<stage>-ui-waf`)

Closes Phase B blocker #2. Also worth deploying independently of Phase B: the prod
distribution is publicly reachable and currently has no WAF at all.

**Why this is a separate stack.** A `CLOUDFRONT`-scoped WAFv2 WebACL must live in
`us-east-1`, regardless of the stage's primary region. `WafStack` pins that itself, so it
is safe to instantiate from an app configured for any region.

**Why the ARN travels by context, not a CFN export.** CloudFormation has no cross-region
exports, and for staging the WebACL (us-east-1) and the UI stack (us-west-2) are in
different regions. The ARN is therefore passed as `-c wafWebAclArn=...`, the same idiom
already used for `UI_CUSTOM_DOMAIN_CERT_ARN` (also a us-east-1 resource consumed by this
distribution). The stack additionally publishes the ARN to SSM for discoverability.

### First deploy is two-phase

```bash
# 1. Deploy the WebACL (us-east-1, regardless of stage region)
cd deployment
make phase1 DEPLOYMENT_STAGE=<stage>        # or: cdk deploy cms-<stage>-ui-waf <ctx flags>

# 2. Read the ARN
aws ssm get-parameter \
  --name /cms/<stage>/ui-waf/web-acl-arn \
  --region us-east-1 --query Parameter.Value --output text

# 3. Put it in config/<stage>.env
#      WAF_WEB_ACL_ARN=arn:aws:wafv2:us-east-1:<account>:global/webacl/<name>/<id>

# 4. Re-deploy the UI stack so CloudFront picks up the attachment
make phase1 DEPLOYMENT_STAGE=<stage>
```

With `WAF_WEB_ACL_ARN` empty, phase1 prints a warning and **no WAF is attached**. That is
why the Phase B blocker gate queries the live distribution rather than trusting synth.

### Rule set and action modes

| Priority | Rule | staging | prod |
|---|---|---|---|
| 0 | `AWSManagedRulesAmazonIpReputationList` | COUNT | BLOCK |
| 1 | `AWSManagedRulesKnownBadInputsRuleSet` | COUNT | BLOCK |
| 2 | `AWSManagedRulesCommonRuleSet` (OWASP) | COUNT | BLOCK |
| 10 | Rate limit, 100 req / 5 min per IP, scoped to `/signup*` + `/confirm-signup*` | BLOCK | BLOCK |

**A stage running COUNT is not protected.** Staging starts in COUNT so false positives
surface in CloudWatch before prod blocks anything; the rate-based rule blocks on both
stages because rate-limiting a signup path has negligible false-positive cost. Metrics and
sampled requests are enabled on every rule including the COUNT ones — a COUNT rule with
metrics off teaches nothing, which defeats the purpose.

To graduate staging to BLOCK, review the CloudWatch metrics for each rule, then change the
stage's action mode in `stacks/waf_stack.py` and redeploy.

Bot Control (`AWSManagedRulesBotControlRuleSet`) is deliberately **not** included — it is a
paid managed rule group, deferred to v1.1 (backlog `WAF rows (Phase B)`).

## Cognito user-pool WAF (`cms-<stage>-pool-waf`)

The web ACL that actually sees registration and sign-in traffic.

**Why the CloudFront ACL is not enough.** Its rate-based rule is scoped to `/signup*` on
the UI distribution, and no signup request ever arrives there: the frontend has no signup
route, the auth model redirects to Cognito's own managed-login domain, and the distribution
has a single S3 origin with no API behaviour. So "prod has a WAF attached" was true while
the endpoint that claim was made about stayed unprotected. A WAF rule is only as real as
the association it hangs off. See `decisions.md` 2026-08-10 § "WAF is deployed and
attached, but the signup rate-limit rule is on the wrong surface".

Associating a web ACL with a user pool protects the classic hosted UI, managed login, and
the Amazon Cognito API service endpoints
(https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-waf.html).

**Why it is not a separate stack.** A `REGIONAL` web ACL and its association must live in
the **pool's** region (us-west-2 staging, us-east-1 prod), whereas `WafStack` is pinned to
us-east-1 for CloudFront. `ui_stack` already owns the pool and is already in that region,
so the ACL is a construct inside it (`stacks/cognito_pool_waf.py`): no cross-region context
plumbing, no ARN to copy into `config/<stage>.env`, no two-phase first deploy. Nothing
depends on the association, so it cannot recreate the Cognito trigger dependency cycle.

It deploys with the UI stack — there is no separate enable flag:

```bash
cd deployment && make phase1 DEPLOYMENT_STAGE=<stage>
```

Verify the association from the live service rather than from synth:

```bash
aws wafv2 get-web-acl-for-resource \
  --resource-arn arn:aws:cognito-idp:<pool-region>:<account>:userpool/<pool-id> \
  --region <pool-region> --query 'WebACL.Name'
```

An empty response means **no ACL is associated** and the pool is unprotected.

### Rule set and action modes

| Priority | Rule | staging | prod |
|---|---|---|---|
| 0 | `AWSManagedRulesAmazonIpReputationList` | COUNT | BLOCK |
| 1 | `AWSManagedRulesKnownBadInputsRuleSet` | COUNT | BLOCK |
| 2 | `AWSManagedRulesCommonRuleSet` (OWASP) | COUNT | **COUNT** |
| 10 | `SignupRateLimit` — 100 req / 5 min per IP, scoped to signup | BLOCK | BLOCK |
| 11 | `AuthRateLimit` — 2000 req / 5 min per IP, whole pool | BLOCK | BLOCK |

**`CommonRuleSet` stays in COUNT on prod, unlike the CloudFront ACL.** Cognito forwards the
request **body** for user-pool API calls (it does not for managed login), so the OWASP body
rules — `SizeRestrictions_BODY`, `CrossSiteScripting_BODY`, `GenericRFI_BODY` — would
inspect JSON carrying passwords and JWTs. A false positive there is not a blocked scanner,
it is a user who cannot sign in, and there is no traffic baseline for this surface yet. The
two low-false-positive groups go straight to BLOCK on the evidence that the same groups
have been blocking on the prod distribution since 2026-08-10 with prod still serving 200s,
so the same client IPs already clear them.

To graduate `CommonRuleSet` to BLOCK, review a week of its CloudWatch COUNT metrics, then
flip `block_on_prod=True` for that rule in `stacks/cognito_pool_waf.py` and redeploy. The
unit test `test_prod_common_ruleset_stays_in_count` fails when you do, by design — the
graduation should be a deliberate edit, not a drift.

**`SignupRateLimit` matches two routes**, because a registration can arrive either way:

* managed-login paths `/signup*` and `/confirm*` (the latter also covers `/confirmUser`
  and `/confirmforgotPassword`, the same abuse shape);
* user-pool API calls, matched on the `x-amz-target` and `x-amzn-cognito-operation-name`
  headers containing `signup` (case-folded), which covers `SignUp` and `ConfirmSignUp`
  issued by an SDK.

**`AuthRateLimit` is unscoped on purpose.** Credential stuffing lands on `/login` and
`InitiateAuth`, which a signup-scoped rule never sees. The 2000/5min limit (≈6.7 rps from
one address) sits well above interactive use because corporate NAT egress puts many
legitimate users behind a single IP; distributed stuffing is out of reach of any IP-based
limit and is covered by the reputation list instead.

### Two service constraints this ACL must respect

Both are asserted by tests in `stacks/test_cognito_pool_waf.py`, as absences, because
neither can be caught by exercising a working configuration:

* **No `AWSManagedRulesATPRuleSet`.** AWS refuses to associate a web ACL that uses Fraud
  Control ATP with a Cognito user pool. Adding it would synth cleanly and fail the
  association at deploy, leaving the pool unprotected.
* **No CAPTCHA action on any rule.** A CAPTCHA in a pool-associated web ACL causes an
  unrecoverable error in managed-login TOTP registration.

Capacity is 983 WCU (verified against the live service with
`aws wafv2 check-capacity --scope REGIONAL`), inside the 1500 default for a REGIONAL web
ACL. Adding a rule group is a capacity decision as well as a security one.

**One known CDK trap.** `wafv2.CfnWebACL.SingleHeaderProperty(name=...)` renders a
lowercase `name` key, and CloudFormation requires `Name`; cfn-lint rejects the typed form
(E3003 + E3002), so the construct passes a raw `{"Name": ...}` dict. Verified 2026-08-11 on
aws-cdk-lib 2.257.0. A template-shape test keyed on the header *value* would pass either
way, so the test asserts the *key*.

## Account provisioning — Phase A prod actions

This section documents the sequence to deprivilege the `FleetManager@example.com` demonstration account on prod after the fail-open authorization defaults have been remediated.

**Ordering rationale:** The de-privilege operation must execute AFTER the fail-open fix is confirmed deployed to prod (otherwise a `fleet-operator` with an empty `custom:fleetIds` still receives unscoped cross-fleet access via the fleetless-fallback defect, making the privilege reduction ineffective). The de-privilege must complete BEFORE internal auto-provisioning is enabled on prod, to establish the correct permissions for auto-provisioned platform-admin users before production traffic depends on them.

**Dry-run-only policy:** This runbook documents the operational sequence at authoring time. The `--apply` step is authorized solely by the platform owner and only after the fail-open fix has been verified as deployed to the prod environment.

### Prerequisites

- Prod fail-open fix deployed: verify by running `deployment/scripts/check_prod_fail_open_deployed.py` (see Step 1 below)
- Cognito `<user-pool-id>` (prod pool) contains `FleetManager@example.com` with current membership in `platform-admin` group
- The environment variable `$PROD_FLEETMANAGER_SUB_ID` is set to the account's sub ID (stored in `deployment/config/prod.env`, publish-excluded)

### Deprivilege Sequence

1. **Verify fail-open fix deployed to prod:**
   ```bash
   cd deployment
   python3 scripts/check_prod_fail_open_deployed.py
   ```
   This script confirms the prod `FleetAPIFunction` Lambda was last modified at or after the fix commit timestamp. If the check fails, the fix has not yet been deployed—halt and reschedule after the infrastructure update completes.

2. **Preview the de-privilege changes (dry-run):**
   ```bash
   cd deployment
   DEPLOYMENT_STAGE=prod AWS_REGION=us-east-1 \
     python3 scripts/deprivilege_demo_personas.py --stage prod --confirm-prod --diff
   ```
   This outputs a before/after summary WITHOUT mutating Cognito. Expected output shows `FleetManager@example.com` transitioning from `platform-admin` to `fleet-operator`.

3. **Verify the diff matches expectations:**
   - Persona changed: `FleetManager@example.com` only (not other demo personas)
   - Groups removed: `platform-admin`
   - Groups added: `fleet-operator`
   - No other changes to the pool membership

4. **Apply the de-privilege:**
   ```bash
   cd deployment
   DEPLOYMENT_STAGE=prod AWS_REGION=us-east-1 \
     python3 scripts/deprivilege_demo_personas.py --stage prod --confirm-prod --apply
   ```
   This mutates the prod Cognito pool. The script updates the group membership and records before/after state in a timestamped JSON audit file (location output to stdout).

5. **Verify post-action group membership:**
   ```bash
   aws cognito-idp admin-list-groups-for-user \
     --user-pool-id <user-pool-id> \
     --username FleetManager@example.com \
     --region us-east-1
   ```
   Expected output: the user is a member of exactly `fleet-operator` (and no longer `platform-admin`).

6. **Record the result:**
   Create a file at `issues/2026-08-DD-fleetmanager-deprivilege/summary.md` (where DD is the current day) with the following:
   - Date the de-privilege was executed
   - Confirmation that fail-open fix was verified deployed
   - Output from Step 5 (post-action group membership)
   - Mark status as RESOLVED

### Rollback

If the de-privilege needs to be reversed before phase B is deployed:

```bash
DEPLOYMENT_STAGE=prod AWS_REGION=us-east-1 \
  python3 scripts/deprivilege_demo_personas.py --stage prod --confirm-prod --diff --only fleet-manager --restore
```

This restores `FleetManager@example.com` to `platform-admin`. Rollback requires the same `--confirm-prod` safety gate.

### Post-Deployment Configuration

After successful de-privilege, no further action is required on staging or prod until Phase B account provisioning is ready to deploy. The `enable_internal_auto_provisioning` context flag on prod remains `false` until Phase B operators explicitly enable it as part of the broader account-provisioning enablement sequence (see `2026-08-07-cms-account-provisioning-model` spec for sequencing).

Refer to § "Prod Edge-Gate Posture" for the current prod identity-model posture and compensating controls.

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
- **`CMS_DEMO_DEFAULT_PASSWORD` is optional** (as of 2026-08-05). The env var
  is no longer required by the CDK stack — demo credentials are now managed in
  Secrets Manager. The `ui_stack.py` synth gate was removed and replaced by a
  stricter template-level guard that prevents plaintext credentials from reaching
  CloudFormation. If you are using the staging demo-login buttons (which read
  from the frontend bundle), set this env var. Otherwise it is not needed:
  ```bash
  # Optional: set only if using demo-login buttons
  export CMS_DEMO_DEFAULT_PASSWORD='<staging-password>'
  ```
  The deployed demo credential is retrieved via
  `aws secretsmanager get-secret-value --secret-id cms-staging-demo-user-password`.
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
| ~~`deploy_bedrock_agents`~~ | (Retired 2026-09-06 per spec `2026-09-05-cms-vfo-teardown`. `make deploy-bedrock-agents` is now a no-op stub — the VFO Bedrock Agents stack was removed.) | — |
| `seed_demo_data` | `make seed-all-demo-data`. | FAIL — seed script crashed; usually a missing CFN output. |
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
| ~~S12~~ | (Retired 2026-09-06 per spec `2026-09-05-cms-vfo-teardown` — Bedrock Agents stack removed; VFO agents no longer deployed by CMS) | — |
| ~~S13~~ | (Retired 2026-09-06 — VFO knowledge-base S3 bucket deleted) | — |
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

**`deploy_all` FAIL — `InvalidRequestException: You can't create this secret because a secret with this name is already scheduled for deletion`**:
Secrets Manager does not delete a named secret immediately.  After a
`teardown_region.py` run (or any stack deletion that removes
`cms-{stage}-demo-user-password` or `cms-{stage}-eval-runner-password`),
there is a 7-to-30 day blackout window during which the same name cannot
be reused.  CDK assigns explicit `secretName` values to these secrets, so
a redeploy inside the window fails at the `AWS::SecretsManager::Secret`
resource.

Two resolutions:

1. **Wait out the window** — the secret is permanently deleted after the
   scheduled deletion date (visible in the Secrets Manager console or via
   `aws secretsmanager describe-secret --secret-id <name>`).  Redeploy
   after that date.

2. **Force-delete immediately before redeploying** — run the following for
   each affected secret, then redeploy:

   ```bash
   aws secretsmanager delete-secret \
     --secret-id cms-<stage>-demo-user-password \
     --force-delete-without-recovery \
     --region <region>

   aws secretsmanager delete-secret \
     --secret-id cms-<stage>-eval-runner-password \
     --force-delete-without-recovery \
     --region <region>
   ```

   Replace `<stage>` and `<region>` with the target values (e.g. `staging`
   and `us-west-2`).  `--force-delete-without-recovery` is irreversible —
   only use it for these two regenerable demo/eval secrets.
   **Do not apply it to `cms-{stage}-connector-oem1-credentials`** or any
   other secret: those hold real credentials that cannot be regenerated from
   CDK.

   `teardown_region.py` runs this automatically for the two demo/eval
   secrets as part of its Step 2d cleanup, so manual intervention is only
   needed when the stack was deleted outside the teardown script (e.g., via
   the CloudFormation console or `teardown_region_force.py`).

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

## Demo Identity Model (Demo Personas and Credentials)

The CMS ships with four demo personas for staging (and one admin account for prod) that enable quick login and feature exploration without operator setup of individual Cognito accounts. All demo passwords are **generated server-side, never shared with the client, and rotated via a dedicated CLI tool** — not via password field in the UI or the environment.

### Demo Personas and Minimum Groups

Each persona is assigned the narrowest Cognito group that permits them to demonstrate their intended screens:

| Persona | Email | Cognito Groups (staging) | Cognito Groups (prod) | Demo Screens |
|---------|-------|---|---|---|
| **Fleet Manager** | `FleetManager@example.com` | `fleet-operator` | `platform-admin`, `fleet-manager` | Fleet creation, vehicle management, driver management, OEM1 enroll/unenroll |
| **Connect Agent** | `agent1@cms-fleet.io` | `connect-agent`, `agent` | `agent` | Amazon Connect CCP integration widget, agent workspace |
| **Product Engineer** | `engineer@example.com` | `product-engineer` | `product-engineer` | Engineering Insights, Investigation Workspace, digital thread, test/prod fleet views |
| **Fleet Dispatcher** | `kevin.dispatch@example.com` | `dispatcher` | `dispatcher` | Vehicle map, fleet dashboard, driver monitoring, service alerts (read-only) |

**Key authorization points**:
- **Backend authorization** in `main_api/index.py` recognizes exactly three groups: `platform-admin` (cross-fleet read+write, admin operations), `fleet-operator` (per-fleet read+write), `fleet-viewer` (read-only). UI-only groups (`product-engineer`, `dispatcher`, `connect-agent`) do not grant backend privilege — they gate UI component rendering only.
- **`FleetManager@example.com` on staging** holds `fleet-operator` (narrowest group that permits fleet CRUD and OEM1 enroll/unenroll). Admin-only operations (cross-fleet read, user management, fleet create/delete, system config) go to a separately-named admin account not listed here.
- **`FleetManager@example.com` on prod** holds `platform-admin` + `fleet-manager` and requires explicit **user authorization** to change. See § "Prod edge-gate posture" below.

### AVX Standing Groups and `custom:customerId` (staging)

The pool also carries two groups and one attribute that CMS itself never reads. CVX's AVX findings Lambda consumes them to decide who may see and approve a vehicle's Findings. The contract is CVX `docs/identity-and-standing-contract.md`.

| Pool element | Value | CMS backend effect | Used by |
|---|---|---|---|
| Group `vehicle-owner` | admin-assigned | none (not in `_OPERATOR_GROUPS`) | CVX owner standing, paired with `custom:customerId` |
| Group `fleet-driver` | admin-assigned | none (not in `_OPERATOR_GROUPS`) | CVX driver standing, paired with `custom:vehicleId` |
| Attribute `custom:customerId` | `CUST-` + 8 hex, mutable | none | CVX owner standing; must equal the vehicle row's `sold_to` |

- **Not client-writable.** Every app client's `WriteAttributes` is `["email","name"]`, so a user cannot set `custom:customerId`, `custom:vehicleId` or `custom:fleetIds` on their own account. `AttributesRequireVerificationBeforeUpdate = ["email"]`. Guards: `deployment/stacks/test_standing_claims_pool.py`, `deployment/stacks/test_client_write_attributes.py`.
- **Provisioned by script, not by the UI.** `deployment/scripts/assign_standing_claims.py` is staging-only. It checks the registry (`sold_to` must match) before writing, and a second run is a no-op:
  ```bash
  cd deployment
  python3 scripts/assign_standing_claims.py --stage staging          # dry run
  python3 scripts/assign_standing_claims.py --stage staging --apply
  ```
  It currently provisions one principal: `meridian.driver@example.com` gets `custom:customerId = CUST-004000DD` and `vehicle-owner` (applied 2026-09-24). Its existing `custom:vehicleId` is checked, not written.
- **CVX trusts these claims only behind an attestation.** `/cvx/staging/avx-{customer-id,vehicle-id,fleet-ids}-claim-trusted` are all `true` on staging since 2026-09-24. Before changing any client's `WriteAttributes`, run CVX `scripts/check-standing-claim-pools.sh`; a custom claim on any client invalidates the attestation.

### Credential Retrieval for Operators

Demo credentials are stored in AWS Secrets Manager (per-stage, not per-environment-variable). To retrieve and manually sign in:

```bash
# Set the stage and region
DEPLOYMENT_STAGE=staging
REGION=us-west-2

# Retrieve FleetManager@example.com password
aws secretsmanager get-secret-value \
  --secret-id "cms-${DEPLOYMENT_STAGE}-demo-user-password" \
  --region "$REGION" \
  --query 'SecretString' \
  --output text

# Retrieve agent1, engineer, dispatcher passwords (JSON object keyed by email)
aws secretsmanager get-secret-value \
  --secret-id "cms-${DEPLOYMENT_STAGE}-demo-persona-passwords" \
  --region "$REGION" \
  --query 'SecretString' \
  --output text | jq -r '.["agent1@cms-fleet.io"]'
```

These commands return the plaintext password. Use them only in a secure context (never share in chat, logs, or screenshots); rotation is the remediation if a password is exposed.

### Credential Rotation

Rotate demo persona passwords without a CDK redeploy:

```bash
cd deployment

# Dry-run (see what would change)
python3 scripts/rotate_demo_login.py --stage staging

# Rotate all four personas (generate new passwords server-side)
python3 scripts/rotate_demo_login.py --stage staging --apply

# Rotate one persona with a custom password (via CMS_DEMO_NEW_PASSWORD env var)
export CMS_DEMO_NEW_PASSWORD='YourNewPassword@2026'
python3 scripts/rotate_demo_login.py --stage staging --user agent1@cms-fleet.io --apply
unset CMS_DEMO_NEW_PASSWORD

# Prod requires explicit confirmation (hard-refused otherwise)
python3 scripts/rotate_demo_login.py --stage prod --apply --confirm-prod
```

All personas are rotated by default (no `--user` flag); pass `--user <email>` to rotate one. Dry-run is the default; `--apply` is required for any mutation. Prod requires an additional `--confirm-prod` flag as a safety guard.



### One-Click Login on Staging (Path A)

On staging, the quick-login buttons on the login page perform one-click sign-in: click a persona, and the browser automatically submits the password obtained from `runtimeConfig.demoPasswords`. This eliminates the step of manually copying a password from `aws secretsmanager get-secret-value`.

**Design**:
- `runtimeConfig.demoPasswords` is a map keyed by email: `{ "FleetManager@example.com": "<password>", ... }` (staging only)
- The file itself is behind CloudFront + SSO trusted-key-group, so only SSO-authenticated Amazon employees can fetch it
- Once fetched, the password authenticates against `cognito-idp:InitiateAuth` (a public AWS endpoint) from anywhere, indefinitely, until rotation
- Therefore: **the CloudFront gate protects the file, not the identity**. Any cached copy (devtools, screenshot, proxy cache) can authenticate from outside the gate

**Accepted risks** (from spec decisions.md 2026-08-07):
1. "A shared demo password is fetchable from `runtimeConfig.json` by any SSO-authenticated Amazon employee. Once fetched, it authenticates against `cognito-idp:InitiateAuth` (a public AWS endpoint) from anywhere, indefinitely, until rotation."
2. "The mitigation is rotation via `deployment/scripts/rotate_demo_login.py`, which is a 1-command operator action."
3. "The threat surface is Amazon employees only (reaching `runtimeConfig.json` requires past-SSO). Post-departure abuse is time-bounded to next rotation."
4. "Staging bundle-to-prod cross-contamination cannot re-introduce the value into the prod distribution — `runtimeConfig.json` is generated per stage at deploy time, and the built-asset guard refuses plaintext credential in the bundle. `showDemoButtons` remains false on prod."

**Deploy workflow**:

```bash
# 1. Deploy the infrastructure (includes persona-password Secrets Manager adoption)
DEPLOYMENT_STAGE=staging make staging-deploy

# 2. Regenerate runtimeConfig.json with passwords injected
#    (At this step, demoPasswords is added on staging, absent on prod)
DEPLOYMENT_STAGE=staging make regenerate-runtime-config

# Between steps 1 and 2, runtimeConfig.json exists but has NO demoPasswords key.
# This is defined behavior (not a bug): the quick-login buttons fall back to
# email-prefill-only until regenerate-runtime-config runs. Users can still
# manually paste a password from aws secretsmanager get-secret-value until the
# regenerate step completes.
```

**Intermediate state (after `cdk deploy`, before `make regenerate-runtime-config`)**:
- `runtimeConfig.showDemoButtons` is `true` (staging only)
- `runtimeConfig.demoPasswords` is **absent** (not yet injected)
- Clicking a persona button prefills the email only; Cognito SDK is not invoked
- This is the defined degrade-safe path (same fallback prod uses)

**Rotation**: Use the existing `rotate_demo_login.py` tool (unchanged). After rotation, `make regenerate-runtime-config` picks up the new value from Secrets Manager. The memorable staging password (set 2026-08-05) will be rotated back to a generated value once Path B (edge-gate on the API) is deployed as hardening, per the spec's follow-on.

### Auto-Sign-In on Landing (Group E — staging only)

Added 2026-08-10. When `runtimeConfig.demoPasswords` is present (staging, after `make regenerate-runtime-config`) and `runtimeConfig.showDemoButtons === true`, the app automatically signs in on a fresh page load without requiring the user to click a button.

**Persona selection**:
- If `localStorage['cms.lastPersona']` is set to an email present in `demoPasswords`, that persona is used.
- Otherwise the default is `FleetManager@example.com`.
- If the stored persona email is not in `demoPasswords` (e.g., a persona was renamed), falls through to `FleetManager@example.com`.

**Sign-out opt-out**:
- Sign-out sets `sessionStorage['cms.suppressAutoSignIn'] = '1'`.
- Auto-sign-in is skipped while this flag is set (current tab only).
- Closing the tab and reopening clears the flag — auto-sign-in resumes.
- This is a UX affordance, not a security control.

**Prod**: `demoPasswords` is absent from prod's `runtimeConfig.json`, so auto-sign-in never triggers on prod.

**Loading state**: The app shows a "Signing in…" spinner rather than briefly flashing the login form while the Cognito request is in-flight.

### Persona Switcher (Fix Group E — account dropdown, staging only, authenticated)

Persona-switch items are integrated directly into the account dropdown (`profileActions` in
`App.tsx`). They appear at the top of the user menu (the account button in the top-nav
header) when `showDemoButtons=true` AND `demoPasswords` is a non-empty object AND the user
is authenticated. Conditions for rendering: all three must hold simultaneously.

**Why the dropdown, not a separate overlay**: The original Group E implementation placed the
switcher as an absolute-positioned overlay inside the header wrapper (a workaround because
Cloudscape's `TopNavigation.utilities` does not accept custom React children). This appeared
as three vertical dots to the user. In Fix Group E the persona items were moved into the
existing `profileActions` array — no separate component, no overlay, no extra icon.

**Behaviour**:
- Shows four persona items at the top of the account dropdown: 🚛 Fleet Manager, 🎧 Agent,
  🔬 Product Engineer, 📋 Dispatcher (followed by a divider, then Preferences/Support/Sign out).
- The currently-signed-in persona appears as a disabled item with "(current)" appended.
- Clicking a different persona calls `simpleAuth.login(email, password, false)` via Path A
  (same credential path as the login-page buttons). Reads the password from
  `runtimeConfig.demoPasswords[email]` at click time — never stored.
- Writes `cms.lastPersona = email` (email only — no password) to localStorage at click time.
- On a failed switch, the current session is preserved (no logout).
- Does NOT clear `sessionStorage['cms.suppressAutoSignIn']` — the switcher is an explicit
  swap, not a sign-out/sign-in cycle.

**Opt out of auto-sign-in**: use the account dropdown → Sign out. That sets
`suppressAutoSignIn` and shows the login form.

**Prod**: `demoPasswords` absent → `personaItems` array is empty → persona section is
entirely absent from the account dropdown — identical to pre-Group-E behaviour.

### Credential Guard Behavior

**Built-asset credential assertion** (`assert_no_credential_in_assets.py`):
- Runs automatically before every `cdk deploy` and `ui-quick-deploy`
- Reads the stage's persona secrets from Secrets Manager at runtime (never bakes a literal)
- Scans all built JS/HTML/CSS assets for any password value
- **Fails closed** (exits non-zero) if any credential is found; redact as `len=<N> sha256=<16-hex>...` (never echoes the value)
- Fails on unrecognized stage (`DEPLOYMENT_STAGE` not in `{staging, prod}`)
- Tolerates missing secrets on new environments (`ResourceNotFoundException` → silent skip; any other error → loud exit)

**Template plaintext-credential assertion** (`_assert_no_plaintext_credential_in_template()`):
- Runs during `cdk synth` — deployment will not produce a template if this assertion fails
- Walks every CloudFormation resource for any plaintext credential shape (inline `SecretString`, plain `Password` property, etc.)
- Fails closed if any plaintext is found (synth exits 1, stack not generated)
- Prevents silent regressions where a future CDK edit re-introduces the credential into the template

### Why Edge Gates Are NOT Credential Boundaries

**Staging has a CloudFront SSO gate** — `/` and `/assets/*` return 403 unauthenticated. This protects *assets*, not *identity*. The CMS's Cognito user pool ID, app client ID, and the `InitiateAuth` API endpoint are all published in `runtimeConfig.json` and accessible from an unauthenticated request (the 403 is HTTP-level only, not API-level). Any copy of a demo password (stored in browser dev tools, a screenshot, cached by a proxy) can authenticate to that endpoint from anywhere, indefinitely, regardless of the CloudFront gate. Therefore:

- The gate is not relied upon to protect credentials.
- Credentials must not be worth publishing, because they will be published.
- All credentials are server-generated, short-lived (rotated regularly), and kept in Secrets Manager (not the bundle, not `runtimeConfig.json`, never logged or returned verbatim in any response).

See spec `.kiro/specs/2026-08-05-cms-demo-identity-model/spec.md` § "The load-bearing insight" for the full reasoning.

### Prod Edge-Gate Posture

**Status** (resolved 2026-08-07): Prod stays publicly reachable. This posture question is folded into the in-flight initiative **`2026-08-07-cms-account-provisioning-model`**, which rewrites prod's identity model (SSO auto-provisioning for Amazon users, MFA + email-verification signup for external users). An edge-gate would answer a shallower question than the account-provisioning spec does, and would be immediately re-touched by that work.

For the compensating-controls picture on prod *today* (post 2026-08-07 close-out of the demo-identity-model spec):

- All persona passwords are CDK-generated into Secrets Manager. No shared demo password ships in any template, bundle, or asset. `_assert_no_plaintext_credential_in_template()` refuses plaintext credential in synth; `assert_no_credential_in_assets.py` refuses plaintext credential in built assets (wired to `phase1` and `ui-quick-deploy`).
- `showDemoButtons` is `false` on prod; no demo affordance is rendered.
- The fail-open authorization defaults at `main_api/index.py:{736,773,793,801}` are closed in-repo (commit `d235fb31`).
- `FleetManager@example.com` still holds `platform-admin` in prod at time of writing. The `deprivilege_demo_personas.py` script is deliberately staging-locked at argparse; unlocking and running against prod is scoped into `2026-08-07-cms-account-provisioning-model`.
- Prod pool has `MfaConfiguration: OFF`. This is a separate compensating-controls decision (`Prod MFA off` P2 on the portfolio backlog); MFA becomes structural under the account-provisioning spec.

If prod exposure needs to change before that spec lands (e.g. a customer engagement demands it), the `2026-08-05-cms-demo-identity-model` close-out was explicit that Option A (SSO-gate prod) remains available as a stopgap. It is not the recommended path.

## Simulation deployment

The simulation service (`cms-{stage}-simulation` stack) deploys two ARM64 container images for the simulator and FleetWise Edge agent. By default, `make deploy-simulation` pulls pre-built images from public ECR — **no local container builder required**.

### Default deployment (published images)

```bash
make -C deployment deploy-simulation DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2
```

This pulls:
- `public.ecr.aws/o0q5e8r2/cms-sim-service:<version>` (simulator service)
- `public.ecr.aws/o0q5e8r2/cms-fwe-agent:<version>` (FleetWise Edge agent)

Version is pinned by `SIM_IMAGE_VERSION` in `deployment/stacks/_sim_image_config.py` (currently v0.2.9). The deployment works in any region without additional infrastructure or builders.

> **Source the stage env file — and know that it is NOT sufficient on its own.**
> `make deploy-simulation` does *not* source `config/<stage>.env`, but `stacks/ui_stack.py`
> reads `CLIENT_EXTRA_IDPS` from the environment, so a bare invocation trips the federated-IdP
> guard (`aspects/provisioning_guards.py`) even when the value is set in the config file. The
> full form is:
>
> ```bash
> cd deployment && ( set -a; . config/staging.env; set +a
>   . scripts/load-federate-creds.sh
>   make deploy-simulation DEPLOYMENT_STAGE=staging )
> ```
>
> `load-federate-creds.sh` is required too: past the `CLIENT_EXTRA_IDPS` check the guard next
> demands `FEDERATE_CLIENT_ID` / `FEDERATE_CLIENT_SECRET`, which the config file does not carry
> — it carries `FEDERATE_OIDC_SECRET_ID`, and only that script resolves it.
>
> The guard is correct to fire — it exists because of the 2026-08-11 prod outage where a
> deploy silently removed federated sign-in. Do not "fix" it by editing `CLIENT_EXTRA_IDPS`;
> supply the value the config file already declares.
>
> ⚠️ **This note used to stop here, and that was the bug.** Even with both of the above, a
> narrow target still synths the whole app and can delete the Federate **auto-provisioning**
> resources, because `CMS_ENABLE_INTERNAL_AUTO_PROVISIONING` is read as a **CDK context key**
> that only `phase1` translates. That is not simulation-specific and it caused a ~25-minute
> production regression on 2026-09-15 via `make data-processing`. See
> § "Narrow make targets still synth the WHOLE app" under **Deploy Commands** before running
> any narrow target, and prefer `phase1`.
>
> `deploy-simulation` additionally needs `SIM_IMAGE_MODE=asset` whenever
> `services/simulation/` has moved ahead of the pinned published tag — see
> § "Published-image provenance guard" immediately below. Requires a running Docker daemon.

### Published-image provenance guard

`published` mode pulls a **pinned tag**. If simulation source moves ahead of that tag, the
container runs old code while the deploy reports success. That failure shipped on 2026-08-13
and silently disabled the `vehicle-ecu` presence loop for six days
(`issues/2026-08-19-cms-vehicle-ecu-presence-not-resident/`).

Two constants must therefore be bumped **together**, in the same commit as any publish:

| Constant | Meaning |
|---|---|
| `SIM_IMAGE_VERSION` | the tag published and pulled |
| `SIM_IMAGE_SOURCE_COMMIT` | the commit those images were built from |

Two gates enforce this:

- **At publish** — `make publish-public-ecr VERSION=vX.Y.Z` refuses to push unless `VERSION`
  equals `SIM_IMAGE_VERSION`, before any build, scan or registry login.
- **At synth** — published mode compares `services/simulation` against
  `SIM_IMAGE_SOURCE_COMMIT` and **fails closed** on drift, listing the drifted paths.

When the synth gate fires you have three options, in order of preference:

```bash
# 1. Build from this checkout (dev inner loop)
SIM_IMAGE_MODE=asset make deploy-simulation DEPLOYMENT_STAGE=staging

# 2. Republish (bump BOTH constants first, then publish)
make publish-public-ecr VERSION=v0.3.0
#    Narrow to one image only when its build inputs are provably unchanged, and retag the
#    omitted one so every image exists at the new tag:
make publish-public-ecr VERSION=v0.3.0 PUBLISH_IMAGES=cms-sim-service

# 3. Accept knowingly (loud warning, no gate)
SIM_IMAGE_ALLOW_STALE=1 make deploy-simulation DEPLOYMENT_STAGE=staging
```

The gate **degrades to a warning** — never a failure — when git metadata is unavailable (a
release archive with no `.git`, a shallow clone, or an unknown commit). That is deliberate:
the zero-builder fresh-customer path is the reason published mode exists.

Verify locally with `python3 deployment/scripts/test_sim_image_resolution.py`. The test
`test_real_repo_is_in_sync_after_republish` is a tripwire — it fails as soon as simulation
source outruns the pinned image.

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

### Per-image local build override

To iterate on a single simulation image (e.g., the Python simulator) without rebuilding FleetWise Edge from source (which takes 10+ minutes), use per-image mode overrides:

```bash
# Use published FWE image, but build simulator from local source
SIM_IMAGE_MODE=asset SIM_IMAGE_MODE_CMS_FWE_AGENT=published \
  CDK_DOCKER=finch make -C deployment deploy-simulation DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2
```

Valid `SIM_IMAGE_MODE_<IMAGE>` overrides:
- `SIM_IMAGE_MODE_CMS_FWE_AGENT=published` — pull from public ECR (v0.2.8 by default)
- `SIM_IMAGE_MODE_CMS_FWE_AGENT=asset` — build from local `services/simulation/Dockerfile.fwe`
- `SIM_IMAGE_MODE_CMS_SIM_SERVICE=published` — pull from public ECR
- `SIM_IMAGE_MODE_CMS_SIM_SERVICE=asset` — build from local `services/simulation/Dockerfile`

### Capacity and instance recycling

The FWE ASG runs EC2 instances that host persistent agent tasks and per-trip simulator tasks. Instance type and recycling parameters are configured in `deployment/stacks/simulation_stack.py`:

```python
# Instance type and sizing
instance_type = "t4g.medium"  # was t4g.small; medium accommodates agent + sidecar + per-trip simulator
# desired_capacity is deliberately NOT set — see the WS3 comment in
# simulation_stack.py. Setting it makes `cdk deploy cms-<stage>-simulation`
# reset the ASG on every deploy, bouncing running FWE agents and silently
# killing telemetry. Live desired count is owned by ECS managed scaling.
min_capacity = 1              # max=3; ECS managed scaling owns desired
```

**MaxInstanceLifetime** is set to **7 days**. This recycles instances periodically to prevent kernel-level resource accumulation (slab bloat). On deploy, the ASG replaces instances older than 7 days in a rolling fashion. Monitor CloudWatch `GroupDesiredCapacity` and `GroupTerminatingInstances` to confirm the refresh completes cleanly. A stalled recycle can indicate insufficient cluster capacity to drain tasks.

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
  CMS_DEMO_DEFAULT_PASSWORD='<demo-password>' \
  DEPLOY_SIMULATION=true \
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

## One-off backfill scripts

This section documents idempotent backfill utilities for correcting specific data-state issues discovered post-deployment.

### VEH-VO-001 Certificate Provisioning (`backfill_veh_vo_001_cert.py`)

**When to use**: A vehicle row exists in `cms-{stage}-storage-vehicles` but lacks an IoT certificate provisioning (either missing `certificateId` or missing the derived fields `modelManifestName`, `decoderManifestRef`, `ecuConfigId`). This can occur when:
- A vehicle was inserted before spec 2026-08-28 and the model-manifest requirement was deployed
- The cert provisioning step failed mid-operation and left the vehicle row in a partially updated state

**Prerequisites**:
- Cognito user with `platform-admin` group membership
- AWS credentials for the target region/account
- The vehicle row must exist and have a valid `vehicleId`
- The `CMS-Fleet-Default` model manifest must be ACTIVE in `cms-{stage}-model-manifest` table (seed it via `deployment/scripts/seed_model_manifests.py` if missing)
- The vehicle's current data must be readable from DynamoDB (no hard deletes)

**Usage**:

```bash
cd ~/connected-mobility-guidance-on-aws/deployment/scripts

# Dry-run (print plan, no changes applied):
python3 backfill_veh_vo_001_cert.py --vin <VIN> --vehicle-id <VEHICLE_ID>

# Apply for real:
python3 backfill_veh_vo_001_cert.py --vin <VIN> --vehicle-id <VEHICLE_ID> --apply
```

**Arguments**:
- `--vin <VIN>` — the vehicle's VIN (e.g., `1G1FY6S07N4100001`). Required.
- `--vehicle-id <VEHICLE_ID>` — the DynamoDB primary key `vehicleId` (e.g., `VEH-VO-001`). Required.
- `--apply` — perform the update. Omit to dry-run. Default: dry-run only (no changes).

**Environment variables** (all optional):
- `AWS_REGION` — target AWS region. Default: `us-west-2`.
- `AWS_PROFILE` — AWS credentials profile. Default: `default`.
- `DEPLOYMENT_STAGE` — environment stage. Default: `staging`.

**Expected output (dry-run)**:
```
Plan (dry-run, no changes):
  • Load model manifest: CMS-Fleet-Default
  • Mint IoT certificate + Thing for VIN: 1G1FY6S07N4100001
  • Attach policy: CMS-Vehicle-IoT-Policy
  • Update vehicle row with: modelManifestName=CMS-Fleet-Default, certificateId=<new>, ...
```

**Expected output (apply)**:
```
Applying...
  ✓ Certificate provisioned: arn:aws:iot:us-west-2:...:cert/...
  ✓ Vehicle row updated: vehicleId=VEH-VO-001
Done.
```

**Failure modes**:
- `ModelManifestNotFound`: `CMS-Fleet-Default` is missing or not ACTIVE. Run `seed_model_manifests.py` first.
- `DuplicateCertificate`: The vehicle already has a `certificateId` in the row. To re-issue, the old cert must be deactivated manually first (operator responsibility, not handled by this script).
- `VehicleNotFound`: The `vehicleId` does not exist in DynamoDB. Verify the key spelling and region.

---

## Simulation lifecycle

The CMS simulator service uses **two distinct ECS task families** with different lifetimes. Understanding the pairing model is essential when debugging missing telemetry, mis-routed CAN traffic, or "phantom frame loss" symptoms.

### The pairing model

| Task family                | Lifetime       | Purpose                                                                 |
|----------------------------|----------------|-------------------------------------------------------------------------|
| `cms-{stage}-fwe-agent`    | **Persistent** | One per VIN. Runs two containers: (1) AWS IoT FleetWise Edge agent binary, and (2) `vehicle-ecu` sidecar that owns the command subscription, manages vehicle presence state, and emits current state to the vcan device (vcan0, vcan1, …) on an idle cadence. Vehicle is commandable whenever this task is running. |
| `cms-{stage}-fwe-simulator`| **Ephemeral**  | One per trip. Reads the paired agent's `CAN_BUS0` from ECS containerOverrides, signals the vehicle-ecu via DynamoDB `tripIntent`, writes simulated CAN frames to that vcan device, and exits when the trip is complete. |

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

### FWE smoke-gate validation (post-deploy and per-trip)

After deploying simulation infrastructure or starting a new FWE vehicle simulation, verify that the agent is healthy and connected before sending commands. The gate MUST check both agent connection status AND telemetry freshness, not just task count.

**Correct gate logic** (what a proper smoke test does):

```bash
VEHICLE_ID="VEH-MICH-001"
REGION="us-west-2"
CLUSTER="cms-staging-simulation"

# Check 1: agentConnected on the ECS CONTAINER INSTANCE.
#
# This is a field returned by describe-container-instances — NOT a container
# environment variable, and NOT available from describe-tasks. An earlier draft
# of this runbook queried
#   describe-tasks → overrides.containerOverrides[0].environment[?name=='agentConnected']
# which can never match anything: the query returns empty, the comparison against
# "true" fails, and the gate reports failure for a healthy fleet — or, if the
# comparison is inverted, passes unconditionally. A gate that cannot observe the
# thing it gates on is worse than no gate.
#
# agentConnected is the ONLY signal that was truthful during the 2026-08-04
# incident: task lastStatus stayed RUNNING and healthStatus stayed HEALTHY while
# the container was dead and the ECS agent had lost the control plane.
ALL_CONNECTED=$(aws ecs list-container-instances \
  --cluster "$CLUSTER" --status ACTIVE --region "$REGION" \
  --query 'containerInstanceArns' --output text \
  | tr '\t' '\n' | while read -r CI; do
      [ -z "$CI" ] && continue
      aws ecs describe-container-instances \
        --cluster "$CLUSTER" --container-instances "$CI" --region "$REGION" \
        --query 'containerInstances[0].agentConnected' --output text
    done | sort -u | tr '\n' ' ' | tr '[:upper:]' '[:lower:]')
# NOTE: `aws --output text` renders booleans as Python reprs (`True`/`False`),
# so this is lower-cased before comparison. Comparing against "true" without
# normalising silently fails on a healthy fleet.
# "true" alone means every ACTIVE instance is connected.

# Check 2: telemetry freshness — QUERY the key schema, do not scan.
# The table is cms-{stage}-storage-telemetry with vehicleId (HASH) +
# timestamp (RANGE), so the newest row is one query with Limit=1. A filtered
# scan is both slower and non-deterministic on a large table.
LATEST_TELEMETRY_TS=$(aws dynamodb query \
  --table-name "cms-staging-storage-telemetry" \
  --key-condition-expression "vehicleId = :vid" \
  --expression-attribute-values "{\":vid\":{\"S\":\"$VEHICLE_ID\"}}" \
  --no-scan-index-forward --limit 1 \
  --region "$REGION" \
  --query "Items[0].timestamp.N" --output text)

NOW_MS=$(( $(date +%s) * 1000 ))
if [ -z "$LATEST_TELEMETRY_TS" ] || [ "$LATEST_TELEMETRY_TS" = "None" ]; then
  echo "❌ Gate failed: no telemetry rows for $VEHICLE_ID at all"
  exit 1
fi
AGE_S=$(( (NOW_MS - ${LATEST_TELEMETRY_TS%.*}) / 1000 ))

if [[ "$ALL_CONNECTED" == "true " ]] && [[ $AGE_S -lt 60 ]]; then
  echo "✅ Gate passed: all instances agentConnected, telemetry age=${AGE_S}s"
  exit 0
else
  echo "❌ Gate failed: agentConnected=[$ALL_CONNECTED], telemetry age=${AGE_S}s (need all true, age<60s)"
  exit 1
fi
```

Poll this for at least 5 minutes rather than sampling once — the 2026-08-04 failure took
roughly 12 minutes to manifest after a deploy, and a single sample immediately post-deploy
would have passed.

**Incorrect gate logic** (what fails silently):

```bash
# WRONG: checking runningCount == desiredCount reports success even with dead containers
RUNNING=$(aws ecs list-tasks --cluster cms-staging-simulation --family cms-staging-fwe-agent --region us-west-2 --query 'length(taskArns)' --output text)
if [[ $RUNNING -eq 1 ]]; then
  echo "✅ Agent running"; exit 0  # WRONG: ECS may report RUNNING while container is dead
fi
```

The 2026-08-04 defect: a dead simulator container left the agent task in ECS `RUNNING` / `HEALTHY` state with `agentConnected=false`, so the count-based gate reported success while commands were timing out. Always gate on `agentConnected && freshness`, never on task count alone.

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

The CMS UI custom-domain alias (`<your-cloudfront-domain>` for
staging, `<your-cloudfront-domain>` for prod), the Cognito
unauthenticated-identity path / SpaRewriteFunction, and (staging only) the
edge auth gate are **conditional on the `uiCustomDomain` /
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

The MeridianMotorsCompanion iOS app lets a driver who has **no assigned vehicle** claim one
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
It is persisted in **both `deployment/config/staging.env` and
`deployment/config/prod.env`** and passed through by the phase1 deploy target.

**As of 2026-08-04 an unset value no longer silently disables the guard on a
deployed stage — it fails synth.** `_require_driver_self_guard()` in
`deployment/stacks/ui_stack.py` raises unless the value is truthy or
`DEPLOYMENT_STAGE` is one of `{"", dev, development, local, test}`. Dev stages may
still deploy with it off.

That guard exists because the previous silent `false` default caused a real
18-day production exposure. `DRIVER_SELF_GUARD_ENABLED=true` was committed to
`config/prod.env` on 2026-07-16 (`bd75a5c`, "closes P0 security gap in v0.2.7
sync") and prod was deployed on 2026-07-29 — yet the deployed Lambda still read
`false` on 2026-08-03, because that deploy bypassed the env-extracting Make
target. The value is read from the *deploying shell's* environment, so a bare
`cdk deploy` shipped the control off with no error and an `UPDATE_COMPLETE`
stack. Full account: `issues/2026-08-03-prod-driver-self-guard-inert/`.
Remediated 2026-08-03; prod now reports `true`.

Practical consequence: deploy `cms-<stage>-ui` via the canonical Make target
(`make prod-deploy`, `make phase1 DEPLOYMENT_STAGE=<stage>`), which exports the
value from `config/<stage>.env`. For a bare `cdk deploy`, export it yourself:

```bash
export DRIVER_SELF_GUARD_ENABLED=true
```

In the consolidated pool a disabled guard would let driver tokens hit the
no-groups admin default on the CMS API. The iOS side reads
`VSA_CMS_REST_API_URL` (Staging.xcconfig); it is empty in Release until the prod
CMS API + pool trust are wired, which hides the claim affordance in prod.

### Post-deploy smoke (verifies the guard is actually ON)

Invoke the Fleet API lambda with synthetic API-GW events and assert:
- driver token (`custom:driverId`, no group) `GET /api/v1/users` → **403**
- driver token `GET /api/v1/vehicles` → **200** (fleet-scoped)
- driver token `PUT /api/v1/drivers/<other>` → **403**
- operator token (`platform-admin`) `GET /api/v1/users` → **200** (no lockout)
## Publishing to the Public GitHub Mirror

CMS releases to the public AWS Solutions Library mirror via the canonical publish toolkit. This section describes the sync, drift-check, and publish flow.

### Before You Publish

1. **Sync the toolkit** (if not already synced):
   ```bash
   ~/.kiro/publish-toolkit/sync.sh ~/connected-mobility-guidance-on-aws
   git add scripts/
   git commit -m "chore(publish): sync canonical toolkit"
   git push
   ```

2. **Verify the sync anchor is clean**:
   ```bash
   bash scripts/lib/verify-publish-toolkit-sync.sh .
   # Exit 0 means the vendored files match the anchor (no drift)
   ```

3. **Test a dry-run**:
   ```bash
   bash scripts/publish-to-github.sh --tag v0.2.8 --dry-run
   # Review the file count and scanner output
   ```

### The Publish Flow

1. **Drift check** (step 0) — verifies the publish toolkit hasn't been edited in this repo
2. **Tag validation** — confirms the tag exists and working tree is clean
3. **Staging** — clones at the tag into a temp dir
4. **Strip** — removes files via `.publish-exclude` (including `scripts/lib/.publish-toolkit-sync`)
5. **Scan** — runs the secret scanner + `.publish-secrets-scan.yml` forbidden patterns
6. **SIM_IMAGE_VERSION preflight** — validates `SIM_IMAGE_VERSION` matches the release tag (CMS-specific; see "CMS-Specific Preflight" section in `~/.kiro/publish-toolkit/README.md`)
7. **Publish** — triggers the GitLab `publish_to_github` manual job (view progress in Pipelines)

For full details, see `~/.kiro/publish-toolkit/README.md`, including the pre-publish checklist, drift-check CI gate, and troubleshooting guide.

### SIM_IMAGE_VERSION Version Pinning

Before publishing, verify that `deployment/stacks/_sim_image_config.py` has `SIM_IMAGE_VERSION` matching the release tag:

```python
SIM_IMAGE_VERSION = "v0.2.8"  # Must match the tag you're about to publish
```

If they diverge, update the constant and re-run `make publish-public-ecr VERSION=v0.2.8` to push the sim images to the public registry, then publish the mirror. For details, see the backlog row `Sim image version pin` (P2).

To override and publish anyway (e.g., for a docs-only release), use:
```bash
PUBLISH_SKIP_SIM_IMAGE_PREFLIGHT=1 bash scripts/publish-to-github.sh --tag v0.2.8 --dry-run
```
This outputs a 4-line WARN block naming the image tag that will actually be pulled.

---

## SOVD (Remote Diagnostics) deployment

Added by spec `2026-09-01-cms-remote-diagnostics-sovd`. Deploys on-demand DTC read + clear
commands over the existing MQTT transport, with ASAM SOVD-aligned JSON and an S3 fallback
path for large payloads.

### Prerequisites

1. **Commands stack deployed** at least once (any prior `make deploy-commands` run).
   Verify: `aws cloudformation describe-stacks --stack-name cms-<stage>-commands --query 'Stacks[0].StackStatus'` returns `CREATE_COMPLETE` or `UPDATE_COMPLETE`.

2. **Sidecar container built ≥ 2026-09-01** with SOVD support (Groups 3 of the spec). If
   using published ECR images (`SIM_IMAGE_MODE=published`), ensure the tag in
   `deployment/stacks/_sim_image_config.py` reflects the SOVD-capable build. If building
   locally (`SIM_IMAGE_MODE=asset`), the current working tree is sufficient.

3. **IAM**: the sidecar ECS task role gains `s3:PutObject` on the SOVD responses bucket via
   the Task 5.2 CDK change in `deployment/stacks/simulation_stack.py`. This is wired
   automatically by `make deploy-simulation`; no manual IAM action required.

4. **DTC history table**: `cms-<stage>-storage-dtc-history` DynamoDB table must exist
   (created by the storage stack in normal `make deploy-all` flow).

### Deploy commands (staging)

Run in the `deployment/` directory:

```bash
# 1. Deploy the updated commands Lambda + IoT rule + SOVD responses S3 bucket
make deploy-commands DEPLOYMENT_STAGE=staging

# 2. Deploy the updated simulation service (sidecar with SOVD subscribe + UDS handlers)
make deploy-simulation SIM_IMAGE_MODE=asset DEPLOYMENT_STAGE=staging
```

For prod, substitute `DEPLOYMENT_STAGE=prod` and read **§ Sequencing Constraint** below
before applying.

### Expected outcome

After both deploys complete:

- **2 CFN stacks** `cms-<stage>-commands` and `cms-<stage>-simulation` are
  `CREATE_COMPLETE` or `UPDATE_COMPLETE`.
  <!-- verify: aws cloudformation describe-stacks --stack-name cms-staging-commands --query 'Stacks[0].StackStatus' -->
  <!-- verify: aws cloudformation describe-stacks --stack-name cms-staging-simulation --query 'Stacks[0].StackStatus' -->

- **SOVD responses S3 bucket** `cms-<stage>-storage-sovd-responses-<region>-<account>`
  exists with a 30-day lifecycle policy (spec § D4: objects expire after 30 days; the bucket
  itself is `RemovalPolicy.RETAIN` so it survives stack deletion).
  <!-- verify: aws s3api list-buckets --query "Buckets[?contains(Name, 'sovd-responses')]" -->

- **IoT rule** `<prefix>_sovd_response_rule` exists, routing
  `cms/commands/things/+/executions/+/sovd/response` to the `command_response_handler`
  Lambda.
  <!-- verify: aws iot list-topic-rules --query "rules[?contains(ruleName, 'sovd_response_rule')]" -->

### Post-deploy smoke test

```bash
make smoke-sovd-staging
```

This target (delivered by Task 5.4) invokes an SOVD `read_dtcs` on a synthetic staging
vehicle, asserts the `commands` DDB row transitions to `SUCCEEDED` within 15 s, and exits
non-zero on any failure. Run after every deploy that touches the commands stack or
simulation sidecar.

### ⚠️ Sequencing Constraint — prod deploy

> "Deploying commands to prod also ships the pending fail-open fix `142763db` for 4
> fleet-aggregate endpoints. Read the CFN diff before applying."
> — spec `2026-09-01-cms-remote-diagnostics-sovd` § Sequencing Constraint

The `142763db` commit (2026-08-23) fixes a P0 fail-open on four fleet-aggregate Lambda
endpoints that is **not yet live in prod** (the commands Lambda and the FleetAPI function
share the same CDK app and the asset hash for `FleetAPIFunction` will change alongside the
`CommandsApi` asset hash when you deploy).

Before running `make deploy-commands DEPLOYMENT_STAGE=prod`, review the full CFN changeset:

```bash
cd deployment && cdk diff cms-prod-commands --profile <profile> --region <region>
```

The changeset will include SOVD additions **plus** the `142763db` fail-open fix. The four
affected endpoints are:

- `fleet-health`
- `daily-briefing`
- `fleet-actions`
- `decision-journal`

None of those endpoints are modified by this SOVD spec, but they will ship together.
Confirm the diff is understood before approving.

### Rollback

**Commands stack** — no `ROLLBACK=1` Makefile target exists (confirmed by `grep -n ROLLBACK deployment/Makefile`
returning no matches). Roll back via CFN directly:

```bash
# Option A: redeploy from a prior git tag
git checkout <prior-tag>
make deploy-commands DEPLOYMENT_STAGE=staging

# Option B: CFN stack rollback to the previous template
aws cloudformation rollback-stack \
  --stack-name cms-staging-commands \
  --profile <profile> --region <region>
```

**Simulation sidecar** — roll back to the previous image tag:

```bash
SIM_IMAGE_VERSION=<prev-tag> make deploy-simulation SIM_IMAGE_MODE=asset DEPLOYMENT_STAGE=staging
```

Replace `<prev-tag>` with the value from `deployment/stacks/_sim_image_config.py` before
the SOVD update (check `git log -- deployment/stacks/_sim_image_config.py`).



## Meridian CS-mediated telemetry deployment — REMOVED 2026-09-19

**This section documented infrastructure that no longer exists.** The
`cms-{stage}-meridian-ingestion` stack, its DynamoDB table, 3 Lambdas
(ingest/puller/generator), API Gateway, and EventBridge Scheduler were
removed per `docs/data-products-design.md` §6.3's explicit "No side-loading"
rule — this pipeline wrote telemetry directly into
`cms-{stage}-storage-telemetry`, bypassing `cms-telemetry-preprocessed`
entirely, which produces no trips, no safety events, no maintenance alerts,
and no geofence hits, silently. See
`issues/2026-09-19-remove-meridian-side-loading-pipeline/` for the removal
record.

**Deploying the correct Meridian pipeline** requires no separate runbook
entry here — it is Kafka-native (MQTT → IoT rule → real MSK topic
`cs-product-meridian-ev` → the generic `OEMTelemetryProcessor`) and needs no
dedicated CDK stack of its own. See `docs/data-products-design.md` and
`docs/cs-portal-simulator-path-b.md` for its full design and deploy/demo
runbook, including the documented demo target `VEH-MRDN-0011` and its
three-write provisioning recipe.

## Connected Services consumer — CMS-side feed cache (opt-in)

Spec `2026-09-10-cms-connected-services-consumer` T1.5 / T2.0b. This is the
**consumer** half of the Connected Services pair: the producer
(`cms-{stage}-subscriptions`) serves the subscription API; CMS subscribes to it
as one ordinary subscriber and caches the pulled feed here.

The stack is **one DynamoDB table and nothing else** — no Lambda, no container,
no asset. That is worth knowing before you run it: unlike most targets in this
runbook, it cannot pick up uncommitted source from your working tree, so it is
safe to run while other work is in flight.

### Prerequisites

- Feature flag `DEPLOY_CONNECTED_SERVICES_CONSUMER=true` (see `app.py`). Absent
  the flag the stack is not synthesized at all.
- Nothing else. The table has no dependency on the producer stack — CMS's proxy
  routes reach the producer over HTTPS at runtime, not via `Fn.import_value`,
  so the two stacks deploy in either order.

### Deploy

```bash
cd deployment && make deploy-connected-services-consumer DEPLOYMENT_STAGE=staging AWS_PROFILE=default
```

The target carries the same stage-config + Federate preamble as
`deploy-commands`, and for the same reason: `cdk deploy <one-stack>` synthesizes
the **whole** app, and `ui_stack`'s provisioning guards fail closed at synth
without `CLIENT_EXTRA_IDPS` + `FEDERATE_CLIENT_ID`/`SECRET` in the environment.

<!-- verify: grep -n 'deploy-connected-services-consumer:' deployment/Makefile -->

### Expected outcome

- Stack `cms-{stage}-connected-services-consumer` at `CREATE_COMPLETE`
  (~25 seconds — it is one table).
- Table `cms-{stage}-storage-cs-feed-cache-{region}-{account}`, `ACTIVE`,
  TTL `ENABLED` on the `ttl` attribute, PITR on, `DeletionPolicy: Retain`.
- Two stack outputs: `FeedCacheTableName`, `FeedCacheTableArn`.

Staging, verified 2026-09-12: `CREATE_COMPLETE` in 23.6s; table `ACTIVE` with
`TTL=ENABLED`.

### Smoke test

Built into the make target, which exits non-zero if the table is missing, not
`ACTIVE`, or has TTL disabled. TTL is asserted rather than assumed because spec
D3 relies on TTL expiry for cache invalidation — a table that deployed
successfully with TTL off would serve stale feed data indefinitely, which is a
silent failure rather than a visible one.

### Tear-down

```bash
cdk destroy cms-staging-connected-services-consumer --profile default
```

The table's `RETAIN` policy means it **survives** the destroy, deliberately —
it holds cached subscription feed data. Delete it explicitly with
`aws dynamodb delete-table` if you actually mean to dispose of it.

### Troubleshooting

| Symptom | Cause |
|---|---|
| Stack not found in `cdk list` | `DEPLOY_CONNECTED_SERVICES_CONSUMER=true` not set — `app.py` skips the import entirely |
| Synth fails in `ui_stack` provisioning guards | Missing stage-config/Federate preamble; use the make target rather than a bare `cdk deploy` |
| Table exists but the Vehicle Detail card shows nothing | Expected until spec T2.3 lands. The table has no reader or writer yet; deploying it alone is intentionally a no-op |

### What this does NOT include

The three CMS proxy routes that read and write this cache are spec T2.3, which
is blocked on the producer side. Deploying this table is a prerequisite, not a
feature — the flag existed from T1.5 with no make target and no runbook entry,
so the table T2.3 depends on had never been created and appeared in no task.


---

## v1.5 SOVD Diagnostic Sessions — staging smoke test

Added by spec `2026-09-10-cms-dms-sovd-diagnostic-sessions-v1.5` (T6.6). This
playbook also **absorbs T5.1's end-to-end dispatch walkthrough** — see § "Why this
is a playbook and not an integration test" below for that decision.

Exercises the full cross-platform flow: a CMS fleet operator runs a remote routine,
dispatches the vehicle to a dealer, and a DMS technician picks up the same
diagnostic session and adds to it.

**Scope: staging only.** v1.5 ships staging-MVP per its PRD. Do not run the write
steps (4, 5, 10) against prod. Prod readiness is tracked as backlog row
`DMS prod deploy readiness` (P2).

### Prerequisites

| # | Requirement | Check |
|---|---|---|
| 1 | CMS commands stack deployed with v1.5 code | `aws cloudformation describe-stacks --stack-name cms-staging-commands --query 'Stacks[0].LastUpdatedTime'` is after the v1.5 commands commit |
| 2 | CMS UI deployed with the Diagnostics tab + DispatchModal | Step 2 below renders the Routines list |
| 3 | DMS API + UI deployed with the Diagnostics tab + notes endpoint | Step 8 below renders a Diagnostics tab |
| 4 | **Fleet-membership projection is fresh** | see § "The 404 that is not a 404" — this is the most common cause of a failed step 4 |
| 5 | Two credentials: a `fleet-operator` CMS user, and a `dms-technician` whose `custom:dealerIds` includes the dealer chosen in step 5 | `aws cognito-idp admin-get-user` on each |

### Acquire the two JWTs

The API Gateway Cognito authorizer requires the **IdToken**, not the AccessToken
(the AccessToken carries `client_id` but no `aud` claim and yields 401).

```bash
export AWS_REGION=us-west-2

# Resolve the staging UI app client id (do not hardcode it — it changes on pool rebuild)
export CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name cms-staging-ui \
  --query "Stacks[0].Outputs[?contains(OutputKey,'ClientId')].OutputValue | [0]" \
  --output text --region "$AWS_REGION")

# Fleet operator
export JWT_FLEETOP=$(aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=<fleet-operator-email>,PASSWORD=<password> \
  --client-id "$CLIENT_ID" --region "$AWS_REGION" \
  --query 'AuthenticationResult.IdToken' --output text)

# DMS technician
export JWT_TECH=$(aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=<technician-email>,PASSWORD=<password> \
  --client-id "$CLIENT_ID" --region "$AWS_REGION" \
  --query 'AuthenticationResult.IdToken' --output text)
```

Resolve the two table names once (used in the verification steps):

```bash
export COMMANDS_TABLE=$(aws lambda get-function-configuration \
  --function-name $(aws cloudformation describe-stack-resources \
      --stack-name cms-staging-commands \
      --query "StackResources[?ResourceType=='AWS::Lambda::Function'].PhysicalResourceId | [0]" \
      --output text --region "$AWS_REGION") \
  --query 'Environment.Variables.COMMANDS_TABLE' --output text --region "$AWS_REGION")

export RO_TABLE=dms-staging-repair-orders
```

### The 10 steps

| # | Action | Exact URL / command | Expected result |
|---|--------|---------------------|-----------------|
| 1 | Log in to CMS as the fleet operator | `<cms-staging-url>` (staging CloudFront distribution; operator to substitute the concrete URL from `cdk.context.json` / `uiCustomDomain`) | Fleet list renders. The account must be in group `fleet-operator` and carry `custom:fleetIds` covering the target vehicle, or step 4 fails closed. |
| 2 | Open a connected vehicle → **Diagnostics** tab, run an **INERT** routine | Vehicle detail → Diagnostics → Run on an INERT row | Routine transitions to `SUCCEEDED`. STATIONARY rows (inside **What a technician can run at the shop**, which starts collapsed; expand it) render **disabled**, under one line for the group, "Not available for remote fleet operation." (once per group since 2026-09-25, not under each routine) — that is correct, not a defect. |
| 3 | Verify the session log | same tab | The run appears with its routine id and verdict. Note the `sessionId` minted on tab mount — every later step correlates on it. Confirm in DDB: `aws dynamodb scan --table-name "$COMMANDS_TABLE" --filter-expression 'attribute_exists(sessionId)' --max-items 5 --region "$AWS_REGION"` |
| 4 | Click **Dispatch to service** | same tab, `POST /api/dispatch` | Modal opens with a dealer picker. A **404 here is usually a stale fleet projection, not a missing vehicle** — see the section below before debugging anything else. |
| 5 | Pick a dealer and confirm | dispatch modal | Success. The dealer chosen here MUST be one of the technician's `custom:dealerIds`, or step 10 is denied. |
| 6 | Verify the DMS repair order exists | `aws dynamodb scan --table-name "$RO_TABLE" --filter-expression 'attribute_exists(evidence)' --max-items 5 --region "$AWS_REGION"` | A new RO with `initiated_by = "cms_booking"`, `evidence.sessionId` equal to step 3's session, and `evidence.routinesRun[0].routineId` equal to the routine from step 2. |
| 7 | Log in to DMS as the technician | `<dms-ui-url>` (dealer-management CloudFront distribution; operator to substitute the concrete URL from `dmsUiCallbackOrigin` context, without the `https://` scheme) | Service module renders. |
| 8 | Open that RO's detail | Service → the RO from step 6 | **It opens on the Diagnostics tab, not Overview** (T5.2). If it opens on Overview, check that the RO carries BOTH `initiated_by = cms_booking` and `evidence.sessionId` — either alone is intentionally not enough. |
| 9 | Verify the CMS session is visible | Diagnostics tab | Context header shows VIN / dealer / DTC / dispatched-by. Session log shows the routine run in step 2 — proving the session crossed platforms. |
| 10 | Run a **STATIONARY** routine as the technician, then re-check both surfaces | DMS Diagnostics → Invoke | Succeeds for the technician (it is refused for a fleet operator by design — D7 physical-presence). Session log now shows **both** entries under one `sessionId`. Returning to the CMS Diagnostics tab shows the technician's run too. |

### The 404 that is not a 404

Dispatch depends on the fleet-membership projection from spec
`2026-09-02-cms-dms-service-convergence`. DMS's
`source/handlers/fleet_membership.py` denies authorization when the sync-health
marker is **absent or older than `FLEET_SYNC_MAX_AGE`** (default **3600 s**, bounded
to `[60, 86400]`; an out-of-range or unparseable value fails closed with
`fleet_sync_max_age_invalid` rather than silently reverting to the default).

A stale projection returns **HTTP 404 `{"message": "Not found"}`** — deliberately
identical to a genuine not-found so the endpoint cannot be used to enumerate VINs.
The two are distinguishable **only in the logs**, by the distinct denial reason:

```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/<dms-fleet-ro-function> \
  --filter-pattern 'fleet_projection_stale' \
  --start-time $(python3 -c "import time; print(int((time.time()-900)*1000))") \
  --region "$AWS_REGION"
```

If `fleet_projection_stale` appears, the dispatch flow is not broken — the sync is.
Re-run the projection sync and repeat step 4. Treat a step-4 404 as
"check the projection" before treating it as a code defect.

### Why this is a playbook and not an integration test

T5.1 offered an automated `tests/integration/test_dispatch_flow.py` **or** a manual
staging walkthrough. The manual path was chosen deliberately: the automated version
needs CI wiring, live JWT acquisition for two personas, and a cross-repo test runner
spanning the CMS and DMS repositories — infrastructure that a single end-to-end case
does not earn, and that would most likely land shelved and rotting. A captured,
copy-paste-runnable playbook that a human actually executes is worth more than an
automated test nobody runs. The per-leg behavior it would have asserted **is** covered
by unit tests: T5.2 pins the tab-selection predicate (3 cases, all mutation-verified),
and T6.1/T6.2 pin the persona render contracts on each side.

Decision recorded in the spec's `decisions.md` (2026-09-12).


## v1 routine-result-contracts — staging smoke test

Added by spec `2026-09-10-cms-sovd-routine-result-contracts` (T6.2). Verifies that
the six pilot routines return a schema-valid `result` plus a server-computed
`verdict`, that each renders through its own custom renderer rather than the raw
JSON drawer, and that both fields survive into the DynamoDB command row.

> **PARTIALLY UNBLOCKED 2026-09-13 — one gate cleared, one remains.**
>
> 1. ~~**T2.3 has not run.**~~ **DONE 2026-09-13.** The sidecar was rebuilt from this
>    checkout and deployed: task-def `cms-staging-fwe-agent` is at **rev 30**, with
>    `vehicle-ecu` on the private CDK asset digest `c4421f6b…` (`fwe-agent` deliberately
>    stays on public `v0.3.2`). The rebuild found and fixed a defect that would have made
>    this entire playbook fail at step A1 — `routine_sims.py` was missing from the
>    Dockerfile's COPY list, so every routine returned
>    `FAILED / "schema validation failed: No module named 'services'"`, naming the wrong
>    subsystem (commit `89ac5f0c`,
>    `issues/2026-09-13-routine-sims-omitted-from-sidecar-image/`). T2.3's criterion is
>    verified **against the deployed image**, pulled from ECR by rev 30's exact digest:
>    `verdict == 'out_of_spec'`, `result.lamps[2] == 'open_circuit'` on `VEH-MRDN-0001`.
> 2. **Deploys are paused portfolio-wide** by operator instruction until the active specs
>    finish. Unchanged.
>
> **Also expect 0 running fwe-agent tasks before you start.** There are no ECS services in
> `cms-staging-simulation`; tasks are launched on demand by `simulation_lambda.run_task`, so
> the post-drain state is 0 and that is normal. Step A1 starting a simulation is what
> launches a task on rev 30 — which is also the step that completes T2.3's own live
> verification, so running this playbook closes both.

### Why this is 12 verifications, and why they are not 12 invocations

The task text reads "6 routines × 2 personas = 12 verifications", which is the right
count but not the right composition. The six pilots do not share a safety class, and
safety class decides who may invoke:

| Routine | Safety class | Powertrain profiles | Fleet operator (CMS) | Technician (DMS) |
|---|---|---|---|---|
| `lamp_self_check` | INERT | all four | invoke | invoke |
| `pack_isolation_test` | INERT | EV, HYBRID | invoke | invoke |
| `cell_balance_check` | INERT | EV, HYBRID | invoke | invoke |
| `o2_heater_check` | STATIONARY | ICE_GASOLINE, HYBRID | **disabled** | invoke |
| `evap_leak_test` | STATIONARY | ICE_GASOLINE, HYBRID | **disabled** | invoke |
| `abs_pump_cycle` | STATIONARY | all four | **disabled** | invoke |

A fleet operator cannot invoke a STATIONARY routine — that is spec D7
physical-presence enforcement, enforced in the UI and refused server-side. So the 12
break down as **3 CMS invocations + 3 CMS disabled-state checks + 6 DMS invocations**.
The disabled-state checks are real verifications, not filler: a STATIONARY routine
that becomes invocable for a fleet operator is a safety regression.

Values in the table above are code-derived, not transcribed. Re-derive with:

```bash
cd services && python3 -c "
import sys; sys.path.insert(0,'.')
from _shared.routine_catalog import get_routines_for_profile
from _shared.routine_result_schemas import ROUTINE_RESULT_SCHEMAS as S
seen={}
for p in ('ICE_GASOLINE','ICE_DIESEL','EV','HYBRID'):
    for e in get_routines_for_profile(p):
        if e['routine_id'] in S:
            seen.setdefault(e['routine_id'],[e.get('safety_class'),[]])[1].append(p)
for r,(sc,ps) in sorted(seen.items()): print(f'{r:22s} {sc:11s} {\",\".join(ps)}')
"
```

### Pick a HYBRID vehicle, or two of the six will be absent

Routine listing is filtered by the vehicle's powertrain profile
(`services/commands/commands_lambda.py:1084`). **HYBRID is the only profile carrying
all six pilots** — `o2_heater_check` and `evap_leak_test` are gasoline-side, while
`pack_isolation_test` and `cell_balance_check` are HV-pack-side. On a pure EV or a
pure ICE vehicle two pilots are legitimately missing from the list, which reads like
a bug and is not one.

Resolve a HYBRID demo VIN rather than hardcoding one — the demo fleet is reseeded and
was rebranded mid-flight (`decisions.md` 2026-09-13):

```bash
export AWS_REGION=us-west-2
export VEHICLES_TABLE=cms-staging-storage-vehicles

aws dynamodb scan --table-name "$VEHICLES_TABLE" \
  --projection-expression 'vehicleId,fuelType,powertrainClass' \
  --max-items 40 --region "$AWS_REGION" \
  --query 'Items[?powertrainClass.S==`HYBRID`].[vehicleId.S,fuelType.S]' --output table
```

If `powertrainClass` is absent on the rows, the model-manifest backfill has not run
for that vehicle (backlog row `Onboard model backfill`); fall back to `fuelType` and
map it through `deployment/scripts/powertrain_profiles.py`.

### Prerequisites

| # | Requirement | Check |
|---|---|---|
| 1 | ~~**T2.3 complete**~~ — **DONE 2026-09-13**, sidecar image carries this spec's sim code | `aws ecs describe-task-definition --task-definition cms-staging-fwe-agent --query 'taskDefinition.revision'` returns **30**. Do NOT also expect a running task on it beforehand — tasks are on-demand (see the banner above); step A1 is what launches one |
| 2 | CMS commands stack deployed with this spec's `_shared/` overlay | `response.result` is non-empty on a fresh run (step A1) |
| 3 | CMS UI deployed with `routine-renderers/` wired into the panel | commit `151b0548` or later in the deployed bundle |
| 4 | DMS UI deployed with the mirrored `routine-renderers/` + panel wiring | DMS T4.2 landed |
| 5 | A **HYBRID** vehicle, connected | § above |
| 6 | Two credentials: a `fleet-operator` CMS user and a `dms-technician` whose `custom:dealerIds` covers the dealer | `aws cognito-idp admin-get-user` on each |

**JWTs and table names**: reuse the setup block from § "v1.5 SOVD Diagnostic Sessions
— staging smoke test" above (`$JWT_FLEETOP`, `$JWT_TECH`, `$COMMANDS_TABLE`). Do not
duplicate it; the app client id changes on pool rebuild and one copy is enough.

### Derive the expected verdicts before you start

The sim is deterministic per `(routine_id, vehicle_id)` (spec D7), so the exact
verdict each routine will produce for your chosen VIN is computable in advance.
Do this first and keep the output beside you — it turns every step below from
"does something appear" into "does the expected value appear":

```bash
cd services && VIN=<your-hybrid-vin> python3 -c "
import os, sys; sys.path.insert(0,'.')
from simulation.routine_sims import produce_result
from _shared.routine_result_schemas import ROUTINE_RESULT_SCHEMAS as S
vin=os.environ['VIN']
for rid, schema in S.items():
    res=produce_result(rid,vin)
    print(f'{rid:22s} -> {schema[\"verdict_from_result\"](res):12s} {res}')
"
```

Most vehicles return `in_spec` on most routines by design. The seeded storytelling
overrides (`decisions.md` 2026-09-13) are the ones that show colour:
`VEH-MRDN-0001` → `lamp_self_check` `out_of_spec` (L-brake `open_circuit`),
`VEH-MRDN-0002` → `o2_heater_check` `marginal`, `VEH-MRDN-0015` →
`cell_balance_check` `out_of_spec`. Those three VINs are the demo path; check their
powertrain against § above before assuming a given routine is listed for them.

### Leg A — CMS fleet operator (verifications A1–A6)

Log in to CMS as the fleet operator, open the HYBRID vehicle → **Diagnostics** tab.
The `sessionId` minted on tab mount correlates every row.

| # | Routine | Action | Expected |
|---|---|---|---|
| A1 | `lamp_self_check` | Run | `lamp-self-check-renderer` appears: 8 lamp cells in a 4×2 grid, each a StatusIndicator; ambient lux below. Verdict banner matches the derived value. A red cell must correspond to `open_circuit`/`short`, amber to `dim`/`flicker`. |
| A2 | `pack_isolation_test` | Run | `pack-isolation-renderer`: measured resistance vs threshold, plus the "higher is safer" note. Banner matches derived value. |
| A3 | `cell_balance_check` | Run | `cell-balance-check-renderer`: one bar per cell (count matches `cell_voltages` length), max delta in mV above. Banner matches derived value. |
| A4 | `o2_heater_check` | **Do not run — inspect the row** (expand **What a technician can run at the shop** first; it starts collapsed) | Row renders **disabled**, under the STATIONARY group's single line "Not available for remote fleet operation." (shown once for the group since 2026-09-25, not under each routine). An enabled row here is a **safety regression** — stop and file it. |
| A5 | `evap_leak_test` | **Do not run — inspect the row** | Same disabled state, under the same group line. |
| A6 | `abs_pump_cycle` | **Do not run — inspect the row** | Same disabled state, under the same group line. |

For A1–A3, confirm the raw JSON drawer is **not** what rendered. Seeing
`session-log-response-drawer-*` instead of the named renderer means either the
registry did not resolve (`rendererFor` fell back) or the response failed schema
validation — check `response.result` in DDB before assuming a UI bug.

### Leg B — DMS technician (verifications B1–B6)

Dispatch the vehicle to the technician's dealer (steps 4–5 of the v1.5 playbook),
then log in to DMS and open the resulting repair order. It opens on the Diagnostics
tab. Run each of the six:

| # | Routine | Expected |
|---|---|---|
| B1 | `lamp_self_check` | `lamp-self-check-renderer`, as A1 |
| B2 | `pack_isolation_test` | `pack-isolation-renderer`, as A2 |
| B3 | `cell_balance_check` | `cell-balance-check-renderer`, as A3 |
| B4 | `o2_heater_check` | **Succeeds here** (technician is physically present): `o2-heater-check-renderer`, two columns — response ms and manufacturer threshold |
| B5 | `evap_leak_test` | `evap-leak-test-renderer`: system pressure and leak rate. Pressure is **negative** (a vacuum-decay test) — that is correct, not a sign error |
| B6 | `abs_pump_cycle` | `abs-pump-cycle-renderer`: observed/expected as a fraction |

B4–B6 are the verifications that A4–A6 could not perform. Together they prove the
same routine is refused for one persona and available to the other — which is the
whole point of the safety class.

### DDB verification — both fields, verbatim

For each invocation, confirm the row carries `response.verdict` **and**
`response.result`. A row with `verdict` but no `result` means the sidecar produced a
verdict from a payload it then dropped:

```bash
aws dynamodb scan --table-name "$COMMANDS_TABLE" \
  --filter-expression 'attribute_exists(#r.verdict)' \
  --expression-attribute-names '{"#r":"response"}' \
  --max-items 12 --region "$AWS_REGION" \
  --query 'Items[].{routine:response.M.routine_id.S,verdict:response.M.verdict.S,hasResult:response.M.result.M!=`null`}' \
  --output table
```

Expected: one row per invocation (9 across both legs — A1–A3 and B1–B6), each with a
non-null `verdict` and `hasResult` true. A4–A6 produce no rows, by design.

The `.M` path segments above are DynamoDB's raw type descriptors. They resolve under
**any** `--output` mode — `--query` is JMESPath evaluated against the parsed API
response, and `--output` only renders the result of that query. What `--output text`
does change is readability: it drops the projection's keys, tab-separates the values,
and orders them alphabetically by key, so the row above arrives as
`lamp_self_check<TAB>True<TAB>out_of_spec` — `hasResult` between the two you asked for
by name. Keep `--output table` (as written) or `--output json` when reading this by eye;
`--output text` is fine for piping, provided you rely on that alphabetical order rather
than the order written in the projection.

Cross-check one row against the schema rather than eyeballing it:

```bash
cd services && python3 -c "
import sys, json; sys.path.insert(0,'.')
from _shared.routine_result_schemas import validate_result, ROUTINE_RESULT_SCHEMAS as S
rid, result = '<routine_id>', json.loads('''<the result map as JSON>''')
errs = validate_result(rid, result)
print('schema errors:', errs or 'none')
print('verdict recomputed:', S[rid]['verdict_from_result'](result))
"
```

The recomputed verdict must equal the stored one. A mismatch means the sidecar's
schema copy has drifted from the deployed `_shared/` overlay — that is a T2.3
packaging bug, not a UI bug.

### What this playbook deliberately does not cover

It exercises whichever verdict the sim produces for the chosen VIN — typically
`in_spec` on most routines. It does **not** walk all three verdict bands per
renderer. That coverage is unit-level and already exists: T6.1's
`routine-renderers-verdict.test.tsx` runs 6 renderers × 3 verdicts = 18 cases in both
repos, with every payload and expected verdict generated from
`ROUTINE_RESULT_SCHEMAS` by `services/_shared/export_verdict_fixtures.py`. Trying to
reproduce 18 verdict combinations on staging would need per-band seed overrides for
every routine, which buys nothing the unit suite does not already assert and adds
demo-data noise.

What staging adds over the unit suite is the part unit tests structurally cannot
reach: that the sidecar actually ships the schema module, that the response survives
the MQTT → handler → DynamoDB path with both fields intact, and that the deployed
bundle resolves the registry.
