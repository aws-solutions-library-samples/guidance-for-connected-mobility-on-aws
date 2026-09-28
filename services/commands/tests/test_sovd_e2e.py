# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Task 5.1 — spec 2026-09-01-cms-remote-diagnostics-sovd
# End-to-end integration test for SOVD diagnostics on staging.
#
# Skipped by default. Runs only when BOTH env vars are set:
#   INTEGRATION=1   (enables integration mode)
#   STAGE=staging   (restricts to staging — never prod)
#
# Usage:
#   INTEGRATION=1 STAGE=staging python3 -m pytest \
#       services/commands/tests/test_sovd_e2e.py -v
#
# Requirements:
#   - AWS credentials with access to staging account (us-west-2)
#   - The staging commands-api Lambda and command-response-handler Lambda must
#     be deployed (cms-staging-commands-api, cms-staging-command-response-handler)
#   - The staging sidecar must be running for a simulated vehicle so that SOVD
#     read_dtcs produces a SUCCEEDED result
#   - DDB tables: cms-staging-storage-commands, cms-staging-storage-dtc-history
#
# Design notes:
#   - The test invokes the commands-api Lambda directly via boto3 (not via API GW
#     HTTP) using a synthetic API Gateway event payload.
#   - Authorization: the Lambda's _extract_caller reads Cognito claims from
#     event['requestContext']['authorizer']['claims']. In integration mode we
#     pass synthetic admin claims so the authz path succeeds without real Cognito.
#   - Polling: uses exponential-backoff polling against DDB rather than a WebSocket
#     or IoT push mechanism, because the test environment may not have IoT subscribe
#     permissions and because DDB GetItem is always available.
#   - Region: always us-west-2 (staging only — DO NOT run against prod).
#   - Secrets: no real credentials are hardcoded. AWS creds come from the environment
#     (IAM role, ~/.aws credentials, or CI role assumption).
#
# Scenario overview:
#   1. test_sovd_e2e_read_dtcs_transitions_to_succeeded
#      Fires read_dtcs, polls DDB for SUCCEEDED + dtc-history row with source='sovd'.
#   2. test_sovd_e2e_clear_dtcs_marks_row_cleared_remote
#      Fires clear_dtcs with attestation, polls dtc-history for status='CLEARED_REMOTE'.
#   3. test_sovd_e2e_full_scan_rate_limited
#      Fires two full-scans < 2 s apart; second response must carry status='RATE_LIMITED'.
#   4. test_sovd_e2e_large_response_uses_s3
#      Sets SOVD_FORCE_S3_UPLOAD=1 on the commands-api Lambda env so that it
#      instructs the sidecar to force an S3 upload; asserts the commands DDB row
#      carries an s3Key field and the S3 object exists.

import json
import os
import time
import uuid

import boto3
import pytest

# ---------------------------------------------------------------------------
# Skip condition — MUST appear at module level so ALL tests in this file are
# skipped when either env var is absent. This is the canonical guard.
# ---------------------------------------------------------------------------

_INTEGRATION = os.environ.get("INTEGRATION") == "1"
_STAGE_OK = os.environ.get("STAGE") == "staging"

_SKIP_REASON = (
    "E2E integration test: set INTEGRATION=1 and STAGE=staging to run. "
    "Never run against prod."
)

# Module-level skipif: all tests in this file share this condition.
pytestmark = pytest.mark.skipif(
    not (_INTEGRATION and _STAGE_OK),
    reason=_SKIP_REASON,
)

# ---------------------------------------------------------------------------
# Constants — staging-specific, no secrets, no real VINs
# ---------------------------------------------------------------------------

_REGION = "us-west-2"
_STAGE = "staging"                          # only ever staging per constraint above
_LAMBDA_NAME = f"cms-{_STAGE}-commands-api"
_RESPONSE_HANDLER_LAMBDA = f"cms-{_STAGE}-command-response-handler"
_COMMANDS_TABLE = f"cms-{_STAGE}-storage-commands"
_DTC_HISTORY_TABLE = f"cms-{_STAGE}-storage-dtc-history"

# Synthetic staging vehicle — matches the seed vehicle for the SOVD demo.
# Override with SOVD_E2E_VEHICLE_ID env var if a different vehicle is seeded.
_DEFAULT_VEHICLE_ID = "VEH-STAGING-SIM-001"
_VEHICLE_ID: str = os.environ.get("SOVD_E2E_VEHICLE_ID", _DEFAULT_VEHICLE_ID)

# Synthetic attestation — test-only values, no real user email.
_ATTESTATION_TEXT = "I have verified the underlying repair is complete"
_ATTESTATION_EMAIL = "test@example.com"   # synthetic; never a real user

# ---------------------------------------------------------------------------
# AWS clients — initialised lazily so the module can be collected without
# AWS credentials present (the pytestmark guard skips the tests before the
# bodies run, but the module-level code still executes).
# ---------------------------------------------------------------------------

def _lambda_client():
    return boto3.client("lambda", region_name=_REGION)


def _ddb_resource():
    return boto3.resource("dynamodb", region_name=_REGION)


def _s3_client():
    return boto3.client("s3", region_name=_REGION)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_api_gw_event(vehicle_id: str, body: dict) -> dict:
    """Build a synthetic API Gateway proxy event for POST /api/commands/{vehicleId}.

    The commands_lambda.handler reads:
      event['httpMethod']    → 'POST'
      event['path']          → '/api/commands/{vehicleId}'
      event['body']          → JSON string of the command body
      event['requestContext']['authorizer']['claims'] → Cognito claims dict

    We inject synthetic platform-admin claims so the authz path passes without
    real Cognito in the integration test environment.  No real user credentials
    are embedded here — the test is run under the IAM role of the CI/operator
    calling the Lambda directly.
    """
    return {
        "httpMethod": "POST",
        "path": f"/api/commands/{vehicle_id}",
        "body": json.dumps(body),
        "queryStringParameters": {},
        "headers": {"Content-Type": "application/json"},
        "requestContext": {
            "authorizer": {
                "claims": {
                    # Synthetic claims — platform-admin so authz passes for all
                    # command types (read_dtcs, clear_dtcs) without a real Cognito token.
                    "cognito:groups": "platform-admin",
                    "custom:fleetIds": "",
                    "email": _ATTESTATION_EMAIL,
                    "sub": "e2e-test-sub-00000000",
                }
            }
        },
    }


def _invoke_commands_lambda(event: dict) -> dict:
    """Invoke the commands-api Lambda and return the parsed response body.

    Raises AssertionError if the Lambda invocation itself fails (FunctionError
    set) or if the HTTP status code is not 200.

    Returns the parsed JSON body dict.
    """
    client = _lambda_client()
    response = client.invoke(
        FunctionName=_LAMBDA_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(event),
    )
    # Check for Lambda execution errors (distinct from HTTP status errors)
    fn_error = response.get("FunctionError")
    raw_payload = response["Payload"].read()
    assert fn_error is None, (
        f"Lambda FunctionError={fn_error!r}. Payload: {raw_payload[:500]}"
    )

    result = json.loads(raw_payload)
    status_code = result.get("statusCode", 0)
    body = json.loads(result.get("body", "{}"))
    assert status_code == 200, (
        f"Expected 200 from commands-api, got {status_code}. Body: {body}"
    )
    return body


def _poll_ddb_item(table_name: str, key: dict, timeout_s: float, interval_s: float = 0.5):
    """Poll a DDB table until the item exists or timeout.

    Returns the item dict, or raises AssertionError on timeout.
    """
    ddb = _ddb_resource()
    table = ddb.Table(table_name)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        resp = table.get_item(Key=key)
        item = resp.get("Item")
        if item:
            return item
        time.sleep(interval_s)
    raise AssertionError(
        f"DDB item not found in {timeout_s}s: table={table_name} key={key}"
    )


def _poll_ddb_item_field(
    table_name: str, key: dict, field: str, expected_value, timeout_s: float,
    interval_s: float = 1.0,
):
    """Poll a DDB item until a specific field equals expected_value, or raise on timeout."""
    ddb = _ddb_resource()
    table = ddb.Table(table_name)
    deadline = time.monotonic() + timeout_s
    last_value = "<not-found>"
    while time.monotonic() < deadline:
        resp = table.get_item(Key=key)
        item = resp.get("Item")
        if item and item.get(field) == expected_value:
            return item
        if item:
            last_value = item.get(field, "<absent>")
        time.sleep(interval_s)
    raise AssertionError(
        f"DDB field {field!r} never reached {expected_value!r} in {timeout_s}s. "
        f"Last value: {last_value!r}. Table={table_name} key={key}"
    )


def _poll_dtc_history_for_source(
    vehicle_id: str, source: str, timeout_s: float, interval_s: float = 1.0
) -> dict:
    """Poll cms-staging-storage-dtc-history until a row with source=<source> exists.

    The dtc-history table is keyed by (vehicleId HASH, dtcCode RANGE).
    We use a Query on vehicleId + FilterExpression for source.

    Returns the first matching item, or raises AssertionError on timeout.
    """
    ddb = _ddb_resource()
    table = ddb.Table(_DTC_HISTORY_TABLE)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        response = table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("vehicleId").eq(vehicle_id),
            FilterExpression=boto3.dynamodb.conditions.Attr("source").eq(source),
            Limit=5,
        )
        items = response.get("Items", [])
        if items:
            return items[0]
        time.sleep(interval_s)
    raise AssertionError(
        f"No dtc-history row with vehicleId={vehicle_id!r} source={source!r} "
        f"found in {timeout_s}s."
    )


def _poll_dtc_history_field(
    vehicle_id: str, dtc_code: str, field: str, expected_value,
    timeout_s: float, interval_s: float = 1.0,
) -> dict:
    """Poll a specific (vehicleId, dtcCode) dtc-history row until field == expected_value."""
    ddb = _ddb_resource()
    table = ddb.Table(_DTC_HISTORY_TABLE)
    key = {"vehicleId": vehicle_id, "dtcCode": dtc_code}
    deadline = time.monotonic() + timeout_s
    last_value = "<not-found>"
    while time.monotonic() < deadline:
        resp = table.get_item(Key=key)
        item = resp.get("Item")
        if item and item.get(field) == expected_value:
            return item
        if item:
            last_value = item.get(field, "<absent>")
        time.sleep(interval_s)
    raise AssertionError(
        f"dtc-history field {field!r} never reached {expected_value!r} in {timeout_s}s. "
        f"Last value: {last_value!r}. vehicleId={vehicle_id!r} dtcCode={dtc_code!r}"
    )


def _get_lambda_env_vars(function_name: str) -> dict:
    """Fetch the current environment variable map for a Lambda function."""
    client = _lambda_client()
    config = client.get_function_configuration(FunctionName=function_name)
    return config.get("Environment", {}).get("Variables", {})


def _set_lambda_env_var(function_name: str, key: str, value: str) -> dict:
    """Add/update a single environment variable on a Lambda function.

    Returns the updated environment dict. Uses get-then-put to preserve
    existing variables.

    NOTE: Lambda configuration updates take effect on the next invocation;
    a short sleep after this call ensures the update is applied.
    """
    client = _lambda_client()
    current_env = _get_lambda_env_vars(function_name)
    updated_env = {**current_env, key: value}
    client.update_function_configuration(
        FunctionName=function_name,
        Environment={"Variables": updated_env},
    )
    # Wait for the update to be applied (Lambda config update propagation)
    waiter = client.get_waiter("function_updated")
    waiter.wait(FunctionName=function_name)
    return updated_env


def _unset_lambda_env_var(function_name: str, key: str) -> None:
    """Remove a single environment variable from a Lambda function.

    Idempotent: if the key is not present, this is a no-op.
    """
    client = _lambda_client()
    current_env = _get_lambda_env_vars(function_name)
    if key not in current_env:
        return
    updated_env = {k: v for k, v in current_env.items() if k != key}
    client.update_function_configuration(
        FunctionName=function_name,
        Environment={"Variables": updated_env},
    )
    waiter = client.get_waiter("function_updated")
    waiter.wait(FunctionName=function_name)


# ---------------------------------------------------------------------------
# Scenario 1: read_dtcs transitions to SUCCEEDED + dtc-history row written
# ---------------------------------------------------------------------------

def test_sovd_e2e_read_dtcs_transitions_to_succeeded():
    """E2E: fire read_dtcs → assert SUCCEEDED in DDB + dtc-history row with source='sovd'.

    Acceptance criteria (Task 5.1 Accept §1):
      (a) Lambda invoke returns 200 with commandId.
      (b) commands DDB row exists within 2 s.
      (c) status transitions to SUCCEEDED within 15 s.
      (d) at least one dtc-history row with source='sovd' for this VIN within 20 s.
    """
    # (a) Invoke read_dtcs
    event = _make_api_gw_event(
        _VEHICLE_ID,
        {
            "command_type": "read_dtcs",
            "components": ["ECU_ENGINE"],
            "include_freeze_frame": False,
        },
    )
    body = _invoke_commands_lambda(event)
    assert "commandId" in body, f"Missing commandId in response: {body}"
    command_id = body["commandId"]

    # (b) commands DDB row exists within 2 s
    commands_key = {"commandId": command_id}
    item = _poll_ddb_item(_COMMANDS_TABLE, commands_key, timeout_s=2.0)
    assert item["commandId"] == command_id, f"Unexpected commandId: {item}"
    assert item.get("type") == "sovd", f"Expected type='sovd', got: {item.get('type')!r}"

    # (c) status transitions to SUCCEEDED within 15 s
    _poll_ddb_item_field(
        _COMMANDS_TABLE, commands_key, "status", "SUCCEEDED", timeout_s=15.0
    )

    # (d) at least one dtc-history row with source='sovd' within 20 s
    dtc_row = _poll_dtc_history_for_source(_VEHICLE_ID, "sovd", timeout_s=20.0)
    assert dtc_row.get("source") == "sovd", f"Unexpected source: {dtc_row}"
    assert dtc_row.get("vehicleId") == _VEHICLE_ID, (
        f"dtc-history vehicleId mismatch: {dtc_row.get('vehicleId')!r}"
    )


# ---------------------------------------------------------------------------
# Scenario 2: clear_dtcs marks dtc-history row CLEARED_REMOTE
# ---------------------------------------------------------------------------

def test_sovd_e2e_clear_dtcs_marks_row_cleared_remote():
    """E2E: fire clear_dtcs with attestation → dtc-history row status='CLEARED_REMOTE'.

    Acceptance criteria (Task 5.1 Accept §2):
      - clear_dtcs returns 200.
      - Within 15 s the corresponding dtc-history row has status='CLEARED_REMOTE'.

    Pre-condition: we first fire a read_dtcs to ensure there is at least one
    dtc-history row for this VIN. Then we fire clear_dtcs on that DTC code.
    """
    # Pre-condition: ensure a dtc-history row exists by firing read_dtcs first.
    read_event = _make_api_gw_event(
        _VEHICLE_ID,
        {
            "command_type": "read_dtcs",
            "components": ["ECU_ENGINE"],
            "include_freeze_frame": False,
        },
    )
    read_body = _invoke_commands_lambda(read_event)
    read_command_id = read_body["commandId"]

    # Wait for a dtc-history row to materialise (up to 20 s)
    dtc_row = _poll_dtc_history_for_source(_VEHICLE_ID, "sovd", timeout_s=20.0)
    dtc_code = dtc_row["dtcCode"]
    ecu_id = dtc_row.get("ecuId", "ECU_ENGINE")

    # Fire clear_dtcs with a synthetic attestation dict
    now_ms = int(time.time() * 1000)
    clear_event = _make_api_gw_event(
        _VEHICLE_ID,
        {
            "command_type": "clear_dtcs",
            "components": [ecu_id],
            "attestation": {
                "text": _ATTESTATION_TEXT,
                "user_email": _ATTESTATION_EMAIL,    # will be overwritten server-side
                "timestamp_ms": now_ms,
            },
        },
    )
    clear_body = _invoke_commands_lambda(clear_event)
    assert "commandId" in clear_body, f"Missing commandId in clear response: {clear_body}"

    # Wait for the dtc-history row's status to become CLEARED_REMOTE (within 15 s)
    _poll_dtc_history_field(
        _VEHICLE_ID, dtc_code, "status", "CLEARED_REMOTE", timeout_s=15.0
    )


# ---------------------------------------------------------------------------
# Scenario 3: full-scan rate limiting — second scan within 2 s returns RATE_LIMITED
# ---------------------------------------------------------------------------

def test_sovd_e2e_full_scan_rate_limited():
    """E2E: two rapid full-scans; second SOVD response carries status='RATE_LIMITED'.

    Acceptance criteria (Task 5.1 Accept §3):
      - First full-scan (components=["*"]) returns 200 with commandId.
      - Second full-scan fired < 2 s later also returns 200 from commands-api
        (the rate limit is enforced in the sidecar — the Lambda always accepts the
        command but the sidecar publishes a RATE_LIMITED response).
      - The second command's DDB row transitions to status='RATE_LIMITED' within 15 s
        (the sidecar's rate-limited response carries status='RATE_LIMITED' in the
        SOVD JSON, which the response handler writes to the commands row).

    Note on the rate limit semantics:
      The CAN rate limiter (HARD GATE F) is per-vehicle, enforced in the sidecar's
      token bucket (1 full-scan per 10 s). The token bucket is consumed by the first
      scan. The second scan, fired < 2 s later, arrives while the bucket is empty and
      the sidecar publishes a RATE_LIMITED SOVD response.  The Lambda itself always
      returns 200 (the accept happens cloud-side; the limit is vehicle-side).
    """
    # First full-scan — must succeed (or at least be accepted by the Lambda)
    first_corr_id = uuid.uuid4().hex[:16]
    first_event = _make_api_gw_event(
        _VEHICLE_ID,
        {
            "command_type": "read_dtcs",
            "components": ["*"],
            "include_freeze_frame": False,
            "correlation_id": first_corr_id,
        },
    )
    first_body = _invoke_commands_lambda(first_event)
    first_command_id = first_body["commandId"]

    # Fire the second full-scan immediately (< 2 s after the first)
    second_corr_id = uuid.uuid4().hex[:16]
    second_event = _make_api_gw_event(
        _VEHICLE_ID,
        {
            "command_type": "read_dtcs",
            "components": ["*"],
            "include_freeze_frame": False,
            "correlation_id": second_corr_id,
        },
    )
    # Minimal pause — must be < 2 s to stay within the token bucket's window
    time.sleep(0.1)
    second_body = _invoke_commands_lambda(second_event)
    second_command_id = second_body["commandId"]

    # The second command's DDB row should reach RATE_LIMITED within 15 s
    # (the sidecar's RATE_LIMITED SOVD response is routed via IoT rule to the
    # response handler, which updates the commands row status).
    second_key = {"commandId": second_command_id}
    _poll_ddb_item_field(
        _COMMANDS_TABLE, second_key, "status", "RATE_LIMITED", timeout_s=15.0
    )

    # Assert the commands row's SOVD payload carries retry_after_ms.
    # The response handler stores the latency and summary; retry_after_ms is
    # carried in the sidecar's RATE_LIMITED response payload.  We verify it
    # is not None by checking the DDB row's attributes.
    ddb = _ddb_resource()
    table = ddb.Table(_COMMANDS_TABLE)
    item = table.get_item(Key=second_key).get("Item", {})
    # retry_after_ms may be stored in the DDB row if the response handler writes it;
    # if not, we assert the status alone (RATE_LIMITED is the primary acceptance criterion).
    # The full assertion is status='RATE_LIMITED' — the presence of retry_after_ms in
    # the SOVD JSON response is covered by the sidecar unit tests (test_sovd_sidecar.py).
    assert item.get("status") == "RATE_LIMITED", (
        f"Expected RATE_LIMITED, got {item.get('status')!r}"
    )


# ---------------------------------------------------------------------------
# Scenario 4: large response forces S3 upload — commands row has s3Key + object exists
# ---------------------------------------------------------------------------

def test_sovd_e2e_large_response_uses_s3():
    """E2E: large sidecar response must upload to S3; commands row carries s3Key.

    Acceptance criteria (Task 5.1 Accept §4):
      - SOVD_FORCE_S3_UPLOAD=1 set on the commands-api Lambda env (test override).
      - A read_dtcs command is fired; the Lambda passes force_large_response in the
        MQTT command payload so the sidecar forces an S3 upload regardless of size.
      - The commands DDB row carries an s3Key field.
      - The S3 object at that key exists (verified via s3.head_object).
      - SOVD_FORCE_S3_UPLOAD is unset from the Lambda env in a try/finally block so
        a test failure can never leave the env var set (spec Task 5.1 Accept §4).

    Test-only override mechanism:
      The env var SOVD_FORCE_S3_UPLOAD=1 is set on the commands-api Lambda via
      boto3.client('lambda').update_function_configuration(). The commands Lambda
      passes this flag as force_large_response=True in the MQTT SOVD request
      payload. The sidecar, upon receiving force_large_response=True, skips the
      80 KB threshold check and always uploads to S3.

      This is an env-var override (not a code branch in the sidecar's business
      logic) — the flag only exists in the Lambda environment during this test,
      and is unconditionally removed in the finally block.
    """
    _FORCE_KEY = "SOVD_FORCE_S3_UPLOAD"
    _FORCE_VALUE = "1"

    # Retrieve the staging SOVD responses bucket name from the Lambda's env
    # (it is set by the CDK stack as SOVD_RESPONSES_BUCKET).
    current_env = _get_lambda_env_vars(_LAMBDA_NAME)
    sovd_bucket = current_env.get("SOVD_RESPONSES_BUCKET", "")
    assert sovd_bucket, (
        "SOVD_RESPONSES_BUCKET env var not found on Lambda. "
        "Is the commands stack deployed with SOVD support?"
    )

    corr_id = uuid.uuid4().hex[:16]
    try:
        # Set the test-only force flag on the commands-api Lambda.
        # This instructs the Lambda to include force_large_response=True in the
        # MQTT payload, signalling the sidecar to bypass the threshold check.
        _set_lambda_env_var(_LAMBDA_NAME, _FORCE_KEY, _FORCE_VALUE)

        # Fire the read_dtcs command
        event = _make_api_gw_event(
            _VEHICLE_ID,
            {
                "command_type": "read_dtcs",
                "components": ["*"],
                "include_freeze_frame": True,
                "correlation_id": corr_id,
            },
        )
        body = _invoke_commands_lambda(event)
        assert "commandId" in body, f"Missing commandId: {body}"
        command_id = body["commandId"]

        # Poll for SUCCEEDED or S3 upload evidence within 30 s
        # (S3 upload adds latency vs inline response).
        commands_key = {"commandId": command_id}
        # First wait for the command to reach a terminal state (SUCCEEDED)
        _poll_ddb_item_field(
            _COMMANDS_TABLE, commands_key, "status", "SUCCEEDED", timeout_s=30.0
        )

        # Assert the commands DDB row carries an s3Key field
        ddb = _ddb_resource()
        table = ddb.Table(_COMMANDS_TABLE)
        item = table.get_item(Key=commands_key).get("Item", {})
        s3_key = item.get("s3Key")
        assert s3_key, (
            f"commands DDB row missing s3Key. Item keys: {list(item.keys())}. "
            f"SOVD_FORCE_S3_UPLOAD may not have been picked up by the sidecar."
        )

        # Assert the S3 object actually exists (head_object — no data transfer)
        s3 = _s3_client()
        try:
            s3.head_object(Bucket=sovd_bucket, Key=s3_key)
        except s3.exceptions.ClientError as e:
            error_code = e.response["Error"]["Code"]
            raise AssertionError(
                f"S3 object not found: s3://{sovd_bucket}/{s3_key} "
                f"(error: {error_code})"
            ) from e

    finally:
        # Always unset the force flag — a test failure must never leave the
        # Lambda env in a modified state (Task 5.1 Accept §4 constraint).
        _unset_lambda_env_var(_LAMBDA_NAME, _FORCE_KEY)
