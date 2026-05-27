# CMS Deployment Guide

This guide covers deployment procedures for the Connected Mobility System (CMS) staging and production environments.

## Environment Overview

The CMS uses a single-account, two-region deployment model:

| Environment | Region | Purpose |
|-------------|--------|---------|
| **Staging** | `us-west-2` | Validate changes end-to-end before prod |
| **Prod** | `us-east-1` | Customer-facing (deployed when ready) |

Both environments run in a single AWS account (set in deployment/config/staging.env and prod.env) with isolation enforced via region separation, stack name prefixes, and per-region IAM roles.

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
5. **Docker daemon** — Confirms Docker is running (CDK uses it for Lambda asset bundling).
6. **Node.js + Python versions** — Verifies Node 18+ and Python 3.9+ available.
7. **Python venv** — Checks `.venv/` exists and is activated.
8. **CMS_DEMO_DEFAULT_PASSWORD** — Ensures the env var is set (used to seed Cognito demo users).
9. **Git working tree** — Verifies no uncommitted changes (clean state required for reproducible CDK).
10. **CDK synth** — Runs a dry-run synth to catch structural errors early.

**Common fixes:**
- Bootstrap missing: `make -C deployment bootstrap-staging`
- VPC quota low: Increase via AWS Service Quotas console, or delete unused VPCs in us-west-2
- Bedrock model error "Legacy model… 30-day inactivity": The model needs to be re-enabled. Bump `BEDROCK_AGENT_MODEL` in `deployment/Makefile` to current Sonnet (check AWS Bedrock docs for the latest ID).
- Docker not running: Start Docker Desktop or your container runtime
- Missing env var: `export CMS_DEMO_DEFAULT_PASSWORD='your-password'`

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

**Eval-user stack is provisioned separately** — `make staging-deploy` runs the `deploy-all` chain which does NOT include `cms-staging-eval-user`. After staging-deploy completes:

```bash
DEMO_PW=$(aws cloudformation describe-stacks --stack-name cms-staging-ui --region us-west-2 \
  --query 'Stacks[0].Outputs[?OutputKey==`DefaultUserPassword`].OutputValue' --output text)

cd deployment && source .venv/bin/activate
DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \
CMS_DEMO_DEFAULT_PASSWORD="$DEMO_PW" \
cdk deploy cms-staging-eval-user --require-approval never --profile default
```

Then promote the eval user's password from `FORCE_CHANGE_PASSWORD` to permanent (Cognito does not allow setting permanent passwords at create time via CloudFormation):

```bash
EVAL_PW=$(aws secretsmanager get-secret-value \
  --secret-id cms-staging-eval-runner-password --region us-west-2 \
  --query SecretString --output text)
USER_POOL_ID=$(aws cloudformation describe-stacks --stack-name cms-staging-ui --region us-west-2 \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)
aws cognito-idp admin-set-user-password \
  --user-pool-id "$USER_POOL_ID" \
  --username cms-eval-runner@example.invalid \
  --password "$EVAL_PW" --permanent --region us-west-2
```

Tier 3 evals will now authenticate cleanly. The eval runner does NOT need AWS credentials — it uses the public app client ID via `cognito-idp:initiate-auth`.

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
   - **`ui_stack` rollback with "No export named cms-prod-bedrock-agents-…"** → `deployment/cdk.context.json` (gitignored, per-developer state) is polluted with prod-specific overrides like `bedrockAgentsStackName`, `uiCustomDomain`, `uiCustomDomainCertArn` from a prior prod-targeted deploy. CDK reads these as defaults for any context not explicitly passed. Surgically remove the offending keys with a small Python snippet (`json.load → ctx.pop('bedrockAgentsStackName', None) → json.dump`) and re-run staging-deploy. The deploy is idempotent.
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

**Last updated:** 2026-05-26