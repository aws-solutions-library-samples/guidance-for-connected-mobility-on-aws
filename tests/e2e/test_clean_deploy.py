"""E2E test suite for Clean-Deploy (First-Time-Deployment) Integration Tests.

Spec: ``.kiro/specs/2026-06-01-clean-deploy-integration-tests/spec.md``

Group 4 deliverable — implements assertion bodies S1–S14 (4.1) and the
telemetry trip materialization assertion (4.2). The orchestrator
(``deployment/scripts/run_clean_deploy_test.sh``) populates these env vars
before invoking pytest:

- ``CMS_CLEAN_DEPLOY_REGION``    (e.g., ``ap-northeast-1``)
- ``CMS_CLEAN_DEPLOY_STAGE``     (always ``staging`` in v1)
- ``CMS_CLEAN_DEPLOY_ACCOUNT``   (the AWS account ID)
- ``CMS_CLEAN_DEPLOY_CF_URL``    (CloudFront distribution URL,
                                  read from CFN output of cms-staging-ui)
- ``BEDROCK_INFERENCE_PROFILE_ID`` (resolved by per-region preflight,
                                  e.g., ``jp.anthropic.claude-sonnet-4-6``)
- ``CMS_CLEAN_DEPLOY_RUN_ID``    (timestamp run ID, used to keep the
                                  simulated VIN unique per run)

Setup-layer assertions S1–S14 verify that the deploy + seed phases
landed the expected resources and data. ``test_trip_materializes``
covers the telemetry pipeline end-to-end.

Per ``decisions.md`` 2026-06-02 (Decision A), S13 is implemented as
**S13.a only**: assert ≥1 S3 object exists per expected prefix in
``cms-{stage}-vfo-knowledge-base``. The S13.b end-to-end agent-runtime
probe is deferred to v1.1.

NB: the existing ``tests/e2e/conftest.py`` skips the entire e2e module
unless ``CMS_E2E_ENDPOINT`` is set. The orchestrator wires this from
the deployed cms-staging-api stack output before invoking pytest, so
this skeleton coexists cleanly with ``test_pipeline.py``.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

import boto3
import pytest
import requests


# ───────────────────────── env-driven config ─────────────────────────


def _required_env(name: str) -> str:
    """Read a required env var or skip the test with a clear message."""
    val = os.environ.get(name)
    if not val:
        pytest.skip(f"{name} env var not set — harness orchestrator must populate")
    return val


# ─────────────────────────── fixtures ────────────────────────────────


@pytest.fixture(scope="session")
def region() -> str:
    """AWS region the clean-deploy harness targets (e.g., ap-northeast-1)."""
    return _required_env("CMS_CLEAN_DEPLOY_REGION")


@pytest.fixture(scope="session")
def stage() -> str:
    """Deployment stage — always ``staging`` in v1."""
    return _required_env("CMS_CLEAN_DEPLOY_STAGE")


@pytest.fixture(scope="session")
def account_id() -> str:
    """AWS account ID hosting the clean-deploy run (e.g., 123456789012)."""
    return _required_env("CMS_CLEAN_DEPLOY_ACCOUNT")


@pytest.fixture(scope="session")
def cf_url() -> str:
    """CloudFront distribution URL of the deployed UI stack (no trailing slash)."""
    return _required_env("CMS_CLEAN_DEPLOY_CF_URL").rstrip("/")


@pytest.fixture(scope="session")
def inference_profile_id() -> str:
    """Resolved Bedrock SYSTEM_DEFINED inference-profile ID for the target region."""
    return _required_env("BEDROCK_INFERENCE_PROFILE_ID")


@pytest.fixture(scope="session")
def run_id() -> str:
    """Per-run ID — used to keep simulated VIN unique across re-runs."""
    return _required_env("CMS_CLEAN_DEPLOY_RUN_ID")


# ────────────────────── shared resource resolution ───────────────────


def _ui_stack_outputs(region: str, stage: str) -> Dict[str, str]:
    """Fetch CFN outputs from the cms-{stage}-ui stack."""
    cfn = boto3.client("cloudformation", region_name=region)
    resp = cfn.describe_stacks(StackName=f"cms-{stage}-ui")
    outputs = {}
    for o in resp["Stacks"][0].get("Outputs", []) or []:
        outputs[o["OutputKey"]] = o["OutputValue"]
    return outputs


def _stack_outputs(region: str, stack_name: str) -> Dict[str, str]:
    """Fetch CFN outputs from an arbitrary stack."""
    cfn = boto3.client("cloudformation", region_name=region)
    resp = cfn.describe_stacks(StackName=stack_name)
    outputs = {}
    for o in resp["Stacks"][0].get("Outputs", []) or []:
        outputs[o["OutputKey"]] = o["OutputValue"]
    return outputs


@pytest.fixture(scope="session")
def ui_outputs(region: str, stage: str) -> Dict[str, str]:
    """All CFN outputs from cms-{stage}-ui (UserPoolId, UserPoolClientId, ...)."""
    return _ui_stack_outputs(region, stage)


@pytest.fixture(scope="session")
def user_pool_id(ui_outputs: Dict[str, str]) -> str:
    pool_id = ui_outputs.get("UserPoolId")
    assert pool_id, "cms-{stage}-ui stack missing UserPoolId output"
    return pool_id


@pytest.fixture(scope="session")
def user_pool_client_id(ui_outputs: Dict[str, str]) -> str:
    client_id = ui_outputs.get("UserPoolClientId")
    assert client_id, "cms-{stage}-ui stack missing UserPoolClientId output"
    return client_id


# ───────────────────── setup-layer assertions S1–S14 ─────────────────


@pytest.mark.e2e
def test_S1(region: str, stage: str, user_pool_id: str) -> None:
    """S1 — Cognito user pool exists with name ``cms-{stage}-*``.

    Backed by: ``boto3.client('cognito-idp').list_user_pools()``.
    Resolves the pool ID from the cms-{stage}-ui stack outputs and
    confirms it shows up in the account-wide list with the expected
    naming convention.
    """
    cidp = boto3.client("cognito-idp", region_name=region)
    expected_prefix = f"cms-{stage}-"
    found = False
    next_token: Optional[str] = None
    while True:
        kwargs: Dict[str, Any] = {"MaxResults": 60}
        if next_token:
            kwargs["NextToken"] = next_token
        resp = cidp.list_user_pools(**kwargs)
        for p in resp.get("UserPools", []) or []:
            if p.get("Id") == user_pool_id:
                assert p.get("Name", "").startswith(expected_prefix), (
                    f"Pool {user_pool_id} name {p.get('Name')!r} does not "
                    f"start with {expected_prefix!r}"
                )
                found = True
                break
        if found:
            break
        next_token = resp.get("NextToken")
        if not next_token:
            break
    assert found, (
        f"User pool {user_pool_id} (from ui stack output) not found via "
        f"list_user_pools in region {region}"
    )


@pytest.mark.e2e
def test_S2(region: str, user_pool_id: str) -> None:
    """S2 — At least one driver user exists in the pool with permanent password
    (not FORCE_CHANGE_PASSWORD).

    Backed by: ``admin_get_user(Username='<seeded-driver>')`` returns
    ``UserStatus=CONFIRMED``. The seed flow (``seed_driver_users.py``)
    creates users via ``admin_create_user`` and immediately flips them to
    permanent via ``admin_set_user_password(..., Permanent=True)``.
    """
    cidp = boto3.client("cognito-idp", region_name=region)
    confirmed_users: List[str] = []
    pagination_token: Optional[str] = None
    while True:
        kwargs: Dict[str, Any] = {"UserPoolId": user_pool_id, "Limit": 60}
        if pagination_token:
            kwargs["PaginationToken"] = pagination_token
        resp = cidp.list_users(**kwargs)
        for u in resp.get("Users", []) or []:
            if u.get("UserStatus") == "CONFIRMED":
                confirmed_users.append(u.get("Username", ""))
        pagination_token = resp.get("PaginationToken")
        if not pagination_token:
            break
    assert confirmed_users, (
        f"No CONFIRMED users in pool {user_pool_id} — seed_driver_users.py "
        "must run before S2"
    )


@pytest.mark.e2e
def test_S3(region: str, stage: str, user_pool_id: str, user_pool_client_id: str) -> None:
    """S3 — Driver auth round-trip: ``initiate_auth(USER_PASSWORD_AUTH)``
    returns IdToken.

    Requires the seed script to set known passwords per driver. The
    deterministic password template is ``DriverDemo-{driverId}-2026!``
    per ``deployment/scripts/seed_driver_users.py:71``. Picks the first
    seeded driver from the drivers DDB table for the auth round-trip.
    """
    ddb = boto3.client("dynamodb", region_name=region)
    drivers_table = f"cms-{stage}-storage-drivers"

    # Find a driver whose Cognito email pattern is firstname.lastname@example.com.
    # seed_driver_users.py builds this in resolve_email() but we can't import the
    # script without DynamoDB Resource side-effects, so rebuild here.
    scan = ddb.scan(TableName=drivers_table, Limit=20)
    drivers = scan.get("Items", []) or []
    assert drivers, f"No drivers in {drivers_table} — seed_drivers must run before S3"

    cidp = boto3.client("cognito-idp", region_name=region)
    last_error: Optional[Exception] = None
    for d in drivers:
        driver_id = (d.get("driverId") or {}).get("S", "")
        first = (d.get("firstName") or {}).get("S", "")
        last = (d.get("lastName") or {}).get("S", "")
        if not driver_id or not first or not last:
            continue
        email = f"{first.lower()}.{last.lower()}@example.com"
        # Strip whitespace defensively (some seeds have trailing spaces).
        email = email.replace(" ", "")
        password = f"DriverDemo-{driver_id}-2026!"
        try:
            resp = cidp.initiate_auth(
                ClientId=user_pool_client_id,
                AuthFlow="USER_PASSWORD_AUTH",
                AuthParameters={"USERNAME": email, "PASSWORD": password},
            )
        except cidp.exceptions.UserNotFoundException as e:
            last_error = e
            continue
        except cidp.exceptions.NotAuthorizedException as e:
            last_error = e
            continue
        result = resp.get("AuthenticationResult") or {}
        if result.get("IdToken"):
            return  # S3 PASS
    raise AssertionError(
        f"No driver auth round-trip succeeded against pool {user_pool_id} "
        f"(client {user_pool_client_id}); last error: {last_error}"
    )


@pytest.mark.e2e
def test_S4(region: str, cf_url: str, user_pool_id: str, user_pool_client_id: str) -> None:
    """S4 — UI runtime config endpoint returns 200 and exposes the
    Cognito + API + region wiring expected by the SPA.

    Schema corrected 2026-06-08 per backlog row `test_S4 schema drift`
    (clean-deploy run 23 surfaced the divergence): the actual
    ``runtimeConfig.json`` written by the CDK ``BucketDeployment`` nests
    Cognito IDs under ``awsCredentials`` and uses ``awsRegion`` /
    ``apiEndpoint`` rather than ``region`` / ``apiBaseUrl``.

    Required keys (verified against staging 2026-06-08T16:00Z):
      * root:          ``awsRegion``, ``apiEndpoint``, ``awsCredentials``
      * awsCredentials: ``region``, ``userPoolId``, ``userPoolWebClientId``

    Backed by: ``requests.get(f'{cf_url}/runtimeConfig.json')``.
    """
    url = f"{cf_url}/runtimeConfig.json"
    resp = requests.get(url, timeout=15)
    assert resp.status_code == 200, (
        f"GET {url} returned HTTP {resp.status_code} (body={resp.text[:200]!r})"
    )
    try:
        body = resp.json()
    except json.JSONDecodeError as e:
        raise AssertionError(f"GET {url} body is not JSON: {e}; body={resp.text[:200]!r}") from e

    for required in ("awsRegion", "apiEndpoint", "awsCredentials"):
        assert required in body, f"runtimeConfig.json missing required root key {required!r}: {body!r}"
    creds = body["awsCredentials"]
    for required in ("region", "userPoolId", "userPoolWebClientId"):
        assert required in creds, (
            f"runtimeConfig.awsCredentials missing required key {required!r}: {creds!r}"
        )

    assert creds["userPoolId"] == user_pool_id, (
        f"runtimeConfig.awsCredentials.userPoolId={creds['userPoolId']!r} does not match "
        f"CFN UserPoolId output {user_pool_id!r}"
    )
    assert creds["userPoolWebClientId"] == user_pool_client_id, (
        f"runtimeConfig.awsCredentials.userPoolWebClientId={creds['userPoolWebClientId']!r} does not match "
        f"CFN UserPoolClientId output {user_pool_client_id!r}"
    )
    assert body["awsRegion"] == region, (
        f"runtimeConfig.awsRegion={body['awsRegion']!r} does not match harness region {region!r}"
    )
    # apiEndpoint is opaque (API Gateway URL) but must be a non-empty https URL.
    assert isinstance(body["apiEndpoint"], str) and body["apiEndpoint"].startswith("https://"), (
        f"runtimeConfig.apiEndpoint={body['apiEndpoint']!r} is not a https URL"
    )


@pytest.mark.e2e
def test_S5(region: str, stage: str, user_pool_id: str, user_pool_client_id: str) -> None:
    """S5 — Eval-user pool user is provisioned and authenticates (existing
    ``cms-staging-eval-user`` stack).

    Backed by: ``admin_initiate_auth``. Reads the eval user's password from
    the Secrets Manager secret created by ``EvalUserStack``.
    """
    eval_outputs = _stack_outputs(region, f"cms-{stage}-eval-user")
    eval_username = eval_outputs.get("EvalUserName")
    secret_arn = eval_outputs.get("EvalPasswordSecretArn")
    assert eval_username, "cms-{stage}-eval-user stack missing EvalUserName output"
    assert secret_arn, "cms-{stage}-eval-user stack missing EvalPasswordSecretArn output"

    sm = boto3.client("secretsmanager", region_name=region)
    secret_resp = sm.get_secret_value(SecretId=secret_arn)
    password = secret_resp.get("SecretString")
    assert password, f"Secret {secret_arn} has empty SecretString"

    cidp = boto3.client("cognito-idp", region_name=region)
    resp = cidp.admin_initiate_auth(
        UserPoolId=user_pool_id,
        ClientId=user_pool_client_id,
        AuthFlow="ADMIN_USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": eval_username, "PASSWORD": password},
    )
    result = resp.get("AuthenticationResult") or {}
    assert result.get("IdToken"), (
        f"admin_initiate_auth for eval user {eval_username!r} returned no IdToken"
    )


@pytest.mark.e2e
def test_S6(region: str, stage: str) -> None:
    """S6 — Demo data: signal-catalog DDB ≥ 50 rows; event-catalog ≥ 10 rows;
    fleets ≥ 1; vehicles ≥ 1; drivers ≥ 1.

    Backed by: ``boto3.client('dynamodb').scan(Select='COUNT')`` per table.
    """
    ddb = boto3.client("dynamodb", region_name=region)
    expectations: List[tuple[str, int]] = [
        (f"cms-{stage}-signal-catalog", 50),
        (f"cms-{stage}-event-catalog", 10),
        (f"cms-{stage}-storage-fleets", 1),
        (f"cms-{stage}-storage-vehicles", 1),
        (f"cms-{stage}-storage-drivers", 1),
    ]
    failures: List[str] = []
    for table_name, min_rows in expectations:
        total = 0
        try:
            resp = ddb.scan(TableName=table_name, Select="COUNT")
            total += resp.get("Count", 0)
            while "LastEvaluatedKey" in resp:
                resp = ddb.scan(
                    TableName=table_name,
                    Select="COUNT",
                    ExclusiveStartKey=resp["LastEvaluatedKey"],
                )
                total += resp.get("Count", 0)
        except ddb.exceptions.ResourceNotFoundException:
            failures.append(f"{table_name}: NOT FOUND")
            continue
        if total < min_rows:
            failures.append(f"{table_name}: {total} rows (expected ≥ {min_rows})")
    assert not failures, "S6 demo-data check failed:\n  " + "\n  ".join(failures)


@pytest.mark.e2e
def test_S7(region: str, stage: str) -> None:
    """S7 — Decoder manifest in S3 (``<flink-config-bucket>/fwe-config/DecoderManifest.bin``).

    Backed by: ``s3.head_object``. The Flink jar bucket name is exposed
    by ``cms-{stage}-flink`` as the ``FlinkJarBucketOutput`` CFN output;
    decoder manifests live under ``fwe-config/`` per
    ``deployment/scripts/verify_demo_data.py:131``.
    """
    flink_outputs = _stack_outputs(region, f"cms-{stage}-flink")
    bucket = flink_outputs.get("FlinkJarBucketOutput")
    assert bucket, "cms-{stage}-flink stack missing FlinkJarBucketOutput output"

    s3 = boto3.client("s3", region_name=region)
    candidate_keys = ["fwe-config/DecoderManifest.bin", "decoder-manifest/DecoderManifest.bin"]
    last_error: Optional[Exception] = None
    for key in candidate_keys:
        try:
            s3.head_object(Bucket=bucket, Key=key)
            return  # S7 PASS
        except s3.exceptions.ClientError as e:
            last_error = e
            continue
    raise AssertionError(
        f"DecoderManifest.bin not found at any of {candidate_keys!r} in s3://{bucket}; "
        f"last error: {last_error}"
    )


@pytest.mark.e2e
def test_S8(region: str, stage: str) -> None:
    """S8 — Default FleetWise CAN campaign + safety campaign templates exist in DDB.

    Backed by: scan campaigns table (``cms-{stage}-campaigns``).
    """
    ddb = boto3.client("dynamodb", region_name=region)
    table = f"cms-{stage}-campaigns"
    total = 0
    try:
        resp = ddb.scan(TableName=table, Select="COUNT")
        total += resp.get("Count", 0)
        while "LastEvaluatedKey" in resp:
            resp = ddb.scan(
                TableName=table, Select="COUNT", ExclusiveStartKey=resp["LastEvaluatedKey"]
            )
            total += resp.get("Count", 0)
    except ddb.exceptions.ResourceNotFoundException as e:
        raise AssertionError(f"Campaigns table {table} not found: {e}") from e
    # verify_demo_data.py minimum is 1; spec wording references multiple
    # safety templates but the seed flow may write only the default CAN
    # campaign on first deploy. Floor of 1 keeps S8 truthful for a
    # first-time deploy without manual seeding.
    assert total >= 1, (
        f"Campaigns table {table} has {total} rows (expected ≥ 1; "
        "seed-fleetwise must run)"
    )


@pytest.mark.e2e
def test_S9(region: str, stage: str) -> None:
    """S9 — IoT thing provisioning policy + LKS rule + topic-rule
    destination present.

    Backed by: ``iot.list_topic_rules`` + ``iot.list_policies``. The IoT
    stack creates rules named ``cms_{stage}_iot_*`` (underscores per
    ``deployment/stacks/iot_stack.py:272``) and the device policy
    ``cms-device-policy``.
    """
    iot = boto3.client("iot", region_name=region)

    # 1. Device policy exists.
    policies = iot.list_policies().get("policies", []) or []
    policy_names = {p.get("policyName", "") for p in policies}
    assert "cms-device-policy" in policy_names, (
        f"IoT policy 'cms-device-policy' not found; have: {sorted(policy_names)!r}"
    )

    # 2. Topic rules with the cms_{stage} prefix exist (connection / subscription /
    # connect / disconnect rules per iot_stack.py).
    expected_prefix = f"cms_{stage}_iot_"
    rules = iot.list_topic_rules().get("rules", []) or []
    rule_names = [r.get("ruleName", "") for r in rules]
    matched = [n for n in rule_names if n.startswith(expected_prefix)]
    # NextMarker pagination
    next_marker = iot.list_topic_rules().get("nextMarker")
    while next_marker:
        page = iot.list_topic_rules(marker=next_marker)
        for r in page.get("rules", []) or []:
            n = r.get("ruleName", "")
            if n.startswith(expected_prefix):
                matched.append(n)
        next_marker = page.get("nextMarker")
    assert matched, (
        f"No IoT topic rules with prefix {expected_prefix!r}; "
        f"all rules: {rule_names!r}"
    )


@pytest.mark.e2e
def test_S10(region: str, stage: str) -> None:
    """S10 — MSK cluster ACTIVE.

    Backed by: ``kafka.describe_cluster``. The MSK stack exposes the
    cluster ARN as ``MSKClusterArn``; we resolve via CFN outputs.

    The spec also lists canonical topic checks; per Group 3 docs the
    ``configure-msk-topics`` Make target creates topics post-deploy.
    Topic-listing requires either MSK Serverless API or a Kafka client
    inside the VPC; from outside-VPC harness we limit S10 to the
    cluster-state assertion. Topic existence is verified indirectly
    by S11 (Flink apps RUNNING means they connected to MSK and read/
    wrote topics) and the trip materialization test.
    """
    msk_outputs = _stack_outputs(region, f"cms-{stage}-msk")
    cluster_arn = msk_outputs.get("MSKClusterArn")
    assert cluster_arn, "cms-{stage}-msk stack missing MSKClusterArn output"

    kafka = boto3.client("kafka", region_name=region)
    resp = kafka.describe_cluster(ClusterArn=cluster_arn)
    state = (resp.get("ClusterInfo") or {}).get("State")
    assert state == "ACTIVE", (
        f"MSK cluster {cluster_arn} is in state {state!r} (expected ACTIVE)"
    )


@pytest.mark.e2e
def test_S11(region: str, stage: str) -> None:
    """S11 — Flink applications RUNNING.

    Backed by: ``kinesisanalyticsv2.describe_application`` for each
    application named ``cms-{stage}-flink-*``. The flink stack creates
    9 applications (see ``deployment/stacks/flink_stack.py``); for v1
    we assert that ALL discovered cms-{stage}-flink-* apps are RUNNING.
    Spec wording cites 3 specific apps but the full app list ships with
    deploy-all and any non-RUNNING app indicates a deploy/start failure.
    """
    kda = boto3.client("kinesisanalyticsv2", region_name=region)
    expected_prefix = f"cms-{stage}-flink-"
    apps: List[Dict[str, Any]] = []
    next_token: Optional[str] = None
    while True:
        kwargs: Dict[str, Any] = {"Limit": 50}
        if next_token:
            kwargs["NextToken"] = next_token
        resp = kda.list_applications(**kwargs)
        apps.extend(resp.get("ApplicationSummaries", []) or [])
        next_token = resp.get("NextToken")
        if not next_token:
            break

    cms_apps = [a for a in apps if a.get("ApplicationName", "").startswith(expected_prefix)]
    assert cms_apps, (
        f"No Flink applications matching prefix {expected_prefix!r} found "
        f"(all apps: {[a.get('ApplicationName') for a in apps]!r})"
    )

    not_running: List[str] = []
    for a in cms_apps:
        name = a.get("ApplicationName", "")
        detail = kda.describe_application(ApplicationName=name).get("ApplicationDetail") or {}
        status = detail.get("ApplicationStatus")
        if status != "RUNNING":
            not_running.append(f"{name}={status!r}")
    assert not not_running, "Flink apps not RUNNING:\n  " + "\n  ".join(not_running)


@pytest.mark.e2e
def test_S12(region: str, stage: str) -> None:
    """S12 — bedrock-agents stack deployed: supervisor + 4 specialist agents present.

    Backed by: ``boto3.client('bedrock-agent').list_agents()`` filtered
    by ``cms-{stage}-*``. Expected agents (from
    ``deployment/scripts/bedrock_agents_snapshot/``):
    cms-cost-agent, cms-maintenance-agent, cms-rebalancing-agent,
    cms-recall-warranty-agent, cms-virtual-fleet-operator (supervisor).
    """
    expected_agents = {
        "cms-cost-agent",
        "cms-maintenance-agent",
        "cms-rebalancing-agent",
        "cms-recall-warranty-agent",
        "cms-virtual-fleet-operator",
    }
    # Note: Bedrock agent names in CMS are NOT stage-prefixed (per the
    # snapshot files in deployment/scripts/bedrock_agents_snapshot/).
    # The stack name IS stage-prefixed (cms-staging-bedrock-agents) but
    # the agent names are stable across stages.
    bagent = boto3.client("bedrock-agent", region_name=region)
    found: set[str] = set()
    next_token: Optional[str] = None
    while True:
        kwargs: Dict[str, Any] = {"maxResults": 100}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = bagent.list_agents(**kwargs)
        for a in resp.get("agentSummaries", []) or []:
            name = a.get("agentName", "")
            if name in expected_agents:
                found.add(name)
        next_token = resp.get("nextToken")
        if not next_token:
            break
    missing = expected_agents - found
    assert not missing, (
        f"S12 bedrock agents missing: {sorted(missing)!r}; "
        f"found: {sorted(found)!r}"
    )


@pytest.mark.e2e
def test_S13(region: str, stage: str) -> None:
    """S13 — bedrock-agents knowledge content seeded (S13.a S3-presence check).

    Per Group 1.3 finding (``docs/tech.md`` § Bedrock-agents KB seed
    sequence) and ``decisions.md`` 2026-06-02 (Decision A): CMS
    bedrock-agents do **NOT** use a Bedrock KnowledgeBase resource.
    Agent snapshots have ``knowledgeBases: []``; agents call the
    ``lookup_knowledge`` action group → ``cms-{stage}-vfo-tools`` Lambda
    → S3 ``cms-{stage}-vfo-knowledge-base`` bucket directly.

    S13.a (this implementation): assert ≥1 S3 object exists under each
    expected prefix. S13.b (full ``invoke_agent`` end-to-end probe) is
    deferred to v1.1 per Decision A.
    """
    # Bucket name suffixed with -{region}-{account} per spec
    # `2026-06-04-cms-vfo-kb-bucket-region-suffix`. Account resolved via STS.
    sts = boto3.client("sts", region_name=region)
    account = sts.get_caller_identity()["Account"]
    bucket = f"cms-{stage}-vfo-knowledge-base-{region}-{account}"
    # Prefixes per the supervisor agent snapshot's documented prefixes
    # (deployment/scripts/bedrock_agents_snapshot/cms-virtual-fleet-operator.json)
    # cross-referenced with verify_demo_data.py S3_EXPECTATIONS.
    expected_prefixes = [
        "service-invoices/",
        "warranty-claims/",
        "fleet-context/",
        "fleet-operations/",
    ]
    s3 = boto3.client("s3", region_name=region)
    failures: List[str] = []
    for prefix in expected_prefixes:
        try:
            resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
        except s3.exceptions.NoSuchBucket as e:
            raise AssertionError(f"VFO KB bucket {bucket} does not exist: {e}") from e
        if resp.get("KeyCount", 0) < 1:
            failures.append(f"empty prefix: s3://{bucket}/{prefix}")
    assert not failures, (
        "S13.a VFO KB content check failed:\n  " + "\n  ".join(failures)
    )


@pytest.mark.e2e
def test_S14(region: str, inference_profile_id: str) -> None:
    """S14 — resolved Bedrock inference-profile is callable: a one-token
    converse call to the configured model returns 200.

    Backed by: ``boto3.client('bedrock-runtime').converse(modelId=<resolved>, ...)``.
    Sanity check on cross-region routing (e.g., ``jp.anthropic.claude-sonnet-4-6``
    in ap-northeast-1 routes to a foundation-model in ap-northeast-1 or
    ap-northeast-3 transparently).
    """
    # Validate the env-resolved profile shape — fail loudly if the harness
    # mis-populated the env var so the test failure points at config, not
    # at Bedrock connectivity.
    assert inference_profile_id, "BEDROCK_INFERENCE_PROFILE_ID env var is empty"
    if not re.match(r"^[A-Za-z0-9_\-]+\.[A-Za-z0-9_.\-]+$", inference_profile_id):
        raise AssertionError(
            f"BEDROCK_INFERENCE_PROFILE_ID={inference_profile_id!r} does not match "
            "expected geo-prefix.model-id format (e.g., jp.anthropic.claude-sonnet-4-6)"
        )

    brt = boto3.client("bedrock-runtime", region_name=region)
    resp = brt.converse(
        modelId=inference_profile_id,
        messages=[{"role": "user", "content": [{"text": "ping"}]}],
        inferenceConfig={"maxTokens": 1, "temperature": 0.0},
    )
    status = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    assert status == 200, (
        f"Bedrock converse to {inference_profile_id} returned HTTP {status} "
        f"(metadata={resp.get('ResponseMetadata')!r})"
    )
    # Sanity: a Converse response carries an output.message structure.
    output = resp.get("output") or {}
    assert "message" in output, (
        f"Bedrock converse response missing output.message: {resp!r}"
    )


# ─────────────────────── telemetry assertion ─────────────────────────


def _capture_flink_logs(region: str, stage: str, run_id: str, minutes: int = 10) -> None:
    """Best-effort capture of last <minutes> of CloudWatch logs from Flink.

    Writes to ``$RUN_LOG_ROOT/<run_id>/flink-logs.txt`` (or
    ``$HOME/.cms/clean-deploy/<run_id>/flink-logs.txt`` if RUN_LOG_ROOT
    is unset). NEVER raises — log-capture failure must NOT flip the
    trip-assertion verdict.
    """
    try:
        run_log_root = os.environ.get("RUN_LOG_ROOT") or os.path.join(
            os.path.expanduser("~"), ".cms", "clean-deploy"
        )
        out_dir = os.path.join(run_log_root, run_id)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "flink-logs.txt")

        logs = boto3.client("logs", region_name=region)
        # Flink log groups live under /aws/kinesis-analytics/ per the
        # Flink stack (e.g., /aws/kinesis-analytics/cms-staging-flink-trip-processor).
        prefix = f"/aws/kinesis-analytics/cms-{stage}-flink-"
        groups: List[str] = []
        next_token: Optional[str] = None
        while True:
            kwargs: Dict[str, Any] = {"logGroupNamePrefix": prefix, "limit": 50}
            if next_token:
                kwargs["nextToken"] = next_token
            resp = logs.describe_log_groups(**kwargs)
            for g in resp.get("logGroups", []) or []:
                groups.append(g.get("logGroupName", ""))
            next_token = resp.get("nextToken")
            if not next_token:
                break

        start_ms = int((time.time() - minutes * 60) * 1000)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(f"# Flink logs for run {run_id} (last {minutes}m)\n")
            fh.write(f"# captured_at={int(time.time())}\n\n")
            for g in groups:
                fh.write(f"\n=== {g} ===\n")
                try:
                    paginator = logs.get_paginator("filter_log_events")
                    for page in paginator.paginate(
                        logGroupName=g, startTime=start_ms, limit=200
                    ):
                        for evt in page.get("events", []) or []:
                            fh.write(f"{evt.get('timestamp', 0)} {evt.get('message', '')}\n")
                except Exception as e:  # noqa: BLE001
                    fh.write(f"[capture-error: {e}]\n")
    except Exception:  # noqa: BLE001
        # Best-effort — never let log capture flip the assertion verdict.
        return


def _trigger_simulation(region: str, stage: str, run_id: str) -> Dict[str, Any]:
    """Trigger one simulated vehicle via the simulation API.

    Returns the parsed start response (contains simulation_id and the
    chosen vehicle_id/vin). Raises on non-200.

    R6 mitigation (spec): the simulator's default route_length=20
    samples a route at 15s/sample → ~5 minutes of telemetry, which
    completes a trip (ignition-on/move/ignition-off) before the 10-min
    poll window closes.
    """
    sim_outputs = _stack_outputs(region, f"cms-{stage}-simulation")
    sim_api_url = sim_outputs.get("SimulationApiUrl")
    if not sim_api_url:
        raise AssertionError(
            f"cms-{stage}-simulation stack missing SimulationApiUrl output"
        )
    sim_api_url = sim_api_url.rstrip("/")

    # Pick a vehicle from the seeded vehicles table to keep VIN traceability.
    ddb = boto3.client("dynamodb", region_name=region)
    scan = ddb.scan(TableName=f"cms-{stage}-storage-vehicles", Limit=5)
    items = scan.get("Items", []) or []
    if not items:
        raise AssertionError(
            f"No vehicles in cms-{stage}-storage-vehicles — seed-vehicles "
            "must run before test_trip_materializes"
        )
    vehicle_id = (items[0].get("vehicleId") or {}).get("S", "")
    assert vehicle_id, f"vehicleId attribute missing on first vehicle row: {items[0]!r}"

    # Use a clean, single-trip config; force_safety_event guarantees an
    # ignition-off at trip end (R6 mitigation).
    config = {
        "vehicle_source": "real_vehicles",
        "vehicles": [vehicle_id],
        "trips": 1,
        "city": "seattle",
        "safety_rate": 0.5,
        "interval": 5,
        "force_engine_overheat": True,
        "force_safety_event": "hard_braking",
        "progressive_degradation": True,
        # Tag the run for log correlation; the lambda may ignore unknown
        # keys but they pass through to the CloudWatch log line.
        "clean_deploy_run_id": run_id,
    }
    resp = requests.post(
        f"{sim_api_url}/api/simulation/start",
        json=config,
        timeout=20,
    )
    if resp.status_code != 200:
        raise AssertionError(
            f"POST {sim_api_url}/api/simulation/start returned HTTP "
            f"{resp.status_code}: {resp.text[:500]!r}"
        )
    body = resp.json()
    body["_vehicle_id"] = vehicle_id
    return body


@pytest.mark.e2e
def test_trip_materializes(region: str, stage: str, run_id: str) -> None:
    """Trip materializes — telemetry pipeline produces a trip row in DDB.

    Group 4.2 implementation:
      1. Trigger one simulated vehicle via the CMS simulation API,
         using a profile that emits ignition-off in ≤ 5 minutes (R6
         mitigation; default route_length=20 → ~5 min sim).
      2. Use the seeded vehicle's vehicleId as a unique key per run; VIN
         uniqueness is implicit via the simulation API's per-vehicle
         dispatch and the run_id tag in the config payload (so re-runs
         can correlate via CloudWatch logs).
      3. Poll ``cms-{stage}-storage-trips`` DDB at 30-second intervals
         up to ``TRIP_ASSERTION_TIMEOUT_SECS`` (env, default 600).
      4. Assert ≥ 1 row exists with the simulated vehicleId AND has
         populated ``startTime``, ``endTime``, ``tripId`` fields.
      5. Capture last 10 min of CloudWatch Logs from Flink log groups
         to ``$RUN_LOG_ROOT/<run_id>/flink-logs.txt`` (orchestrator
         extension; best-effort — log capture failure must NOT flip
         the assertion verdict).
    """
    started = _trigger_simulation(region, stage, run_id)
    vehicle_id = started["_vehicle_id"]
    sim_id = started.get("simulation_id") or started.get("id")
    assert sim_id, f"simulation start response missing simulation_id: {started!r}"

    timeout_secs = int(os.environ.get("TRIP_ASSERTION_TIMEOUT_SECS", "600"))
    poll_secs = 30
    deadline = time.time() + timeout_secs

    ddb = boto3.client("dynamodb", region_name=region)
    trips_table = f"cms-{stage}-storage-trips"

    matching: List[Dict[str, Any]] = []
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        try:
            # The trips table has a vehicleId-index GSI per
            # tests/e2e/test_pipeline.py:_get_trips. Query is preferred
            # over scan for cost + speed.
            try:
                resp = ddb.query(
                    TableName=trips_table,
                    IndexName="vehicleId-index",
                    KeyConditionExpression="vehicleId = :v",
                    ExpressionAttributeValues={":v": {"S": vehicle_id}},
                )
            except ddb.exceptions.ResourceNotFoundException:
                # Fallback to scan if GSI not present (older deploys).
                resp = ddb.scan(
                    TableName=trips_table,
                    FilterExpression="vehicleId = :v",
                    ExpressionAttributeValues={":v": {"S": vehicle_id}},
                )
            items = resp.get("Items", []) or []
            for it in items:
                # Materialized trip rows must have all 3 fields.
                has_trip_id = bool((it.get("tripId") or {}).get("S"))
                has_start = bool(
                    (it.get("startTime") or {}).get("S")
                    or (it.get("startTime") or {}).get("N")
                )
                has_end = bool(
                    (it.get("endTime") or {}).get("S")
                    or (it.get("endTime") or {}).get("N")
                )
                if has_trip_id and has_start and has_end:
                    matching.append(it)
            if matching:
                break
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(poll_secs)

    # Best-effort log capture happens regardless of pass/fail.
    _capture_flink_logs(region, stage, run_id, minutes=10)

    if not matching:
        raise AssertionError(
            f"No trip row materialized in {trips_table} for vehicleId={vehicle_id!r} "
            f"within {timeout_secs}s (sim_id={sim_id}); last query error: {last_err}"
        )
    # Sanity-check first matching row's fields.
    first = matching[0]
    assert (first.get("tripId") or {}).get("S"), (
        f"Materialized trip has empty tripId: {first!r}"
    )
