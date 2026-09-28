# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Tests for the SOVD branch of services/commands/command_response_handler.py
# Spec: 2026-09-01-cms-remote-diagnostics-sovd, Task 2.3
#
# DDB tables used:
#   cms-test-storage-commands   — keyed by commandId (string)
#   cms-test-storage-dtc-history — keyed by vehicleId (HASH) + dtcCode (RANGE)
#
# All AWS calls use moto==5.0.10 (matching test_command_response_handler.py).

import importlib
import os
import sys

import boto3
import pytest
from moto import mock_aws

# ---------------------------------------------------------------------------
# Environment bootstrap (must happen before the handler module imports boto3)
# ---------------------------------------------------------------------------

_STAGE = "test"
_REGION = "us-west-2"
_COMMANDS_TABLE = f"cms-{_STAGE}-storage-commands"
_DTC_HISTORY_TABLE = f"cms-{_STAGE}-storage-dtc-history"
_SOVD_BUCKET = f"cms-{_STAGE}-storage-sovd-responses"

os.environ.setdefault("AWS_DEFAULT_REGION", _REGION)
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ["DEPLOYMENT_STAGE"] = _STAGE
os.environ["COMMANDS_TABLE"] = _COMMANDS_TABLE
os.environ["DTC_HISTORY_TABLE"] = _DTC_HISTORY_TABLE

# Ensure the commands dir is on sys.path so `import command_response_handler` works
_COMMANDS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_PACKAGE_DIR = os.path.join(_COMMANDS_DIR, "package")
for p in (_PACKAGE_DIR, _COMMANDS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def aws_mocked():
    """Activate moto mocking for the full test."""
    with mock_aws():
        yield


@pytest.fixture
def tables(aws_mocked):
    """Create both DDB tables and return (commands_table, dtc_history_table)."""
    client = boto3.client("dynamodb", region_name=_REGION)

    client.create_table(
        TableName=_COMMANDS_TABLE,
        KeySchema=[{"AttributeName": "commandId", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "commandId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )

    client.create_table(
        TableName=_DTC_HISTORY_TABLE,
        KeySchema=[
            {"AttributeName": "vehicleId", "KeyType": "HASH"},
            {"AttributeName": "dtcCode", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "vehicleId", "AttributeType": "S"},
            {"AttributeName": "dtcCode", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    ddb = boto3.resource("dynamodb", region_name=_REGION)
    yield ddb.Table(_COMMANDS_TABLE), ddb.Table(_DTC_HISTORY_TABLE)


@pytest.fixture
def handler_mod(tables):
    """Reload the handler module inside the moto context with both tables live."""
    sys.modules.pop("command_response_handler", None)
    import command_response_handler as mod  # noqa: E402
    importlib.reload(mod)
    yield mod


def _seed_command(commands_table, command_id: str, status: str = "SENT") -> None:
    commands_table.put_item(Item={
        "commandId": command_id,
        "vehicleId": "VEH-TEST-001",
        "commandType": "read_dtcs",
        "status": status,
        "timestamp": 1785854347000,
    })


def _make_sovd_read_event(
    command_id: str,
    vehicle_id: str = "VEH-TEST-001",
    storage_uri=None,
    components=None,
) -> dict:
    """Build an IoT rule event for a sovd_read_dtcs response."""
    if components is None:
        components = {
            "ECU_ENGINE": {
                "id": "ECU_ENGINE",
                "protocol": "ISO 15765-4",
                "dtcs": [
                    {
                        "code": "P0420",
                        "status": "confirmed",
                        "occurrence_count": 1,
                        "first_seen_ms": 1735000000000,
                        "last_seen_ms": 1735000000000,
                        "freeze_frame": {
                            "engineRpm": {"value": 2800, "unit": "rpm",
                                         "timestamp": "2026-08-31T00:00:00Z"},
                            "coolantTemp": {"value": 88, "unit": "degC",
                                           "timestamp": "2026-08-31T00:00:00Z"},
                        },
                    }
                ],
            }
        }

    event = {
        "topic": "cms/commands/things/1FT8W3DT5MEC55401/executions/corr-001/sovd/response",
        "vehicleId": vehicle_id,
        "commandId": command_id,
        "correlationId": "corr-001",
        "commandType": "sovd_read_dtcs",
        "status": "SUCCEEDED",
        "components": components,
        "storage_uri": storage_uri,
        "latency_ms": 1400,
    }
    return event


def _make_sovd_clear_event(
    command_id: str,
    vehicle_id: str = "VEH-TEST-001",
    dtc_codes: list = None,
) -> dict:
    """Build an IoT rule event for a sovd_clear_dtcs response."""
    if dtc_codes is None:
        dtc_codes = ["P0420"]

    components = {
        "ECU_ENGINE": {
            "id": "ECU_ENGINE",
            "dtcs": [{"code": code} for code in dtc_codes],
        }
    }

    return {
        "topic": "cms/commands/things/1FT8W3DT5MEC55401/executions/corr-002/sovd/response",
        "vehicleId": vehicle_id,
        "commandId": command_id,
        "correlationId": "corr-002",
        "commandType": "sovd_clear_dtcs",
        "status": "SUCCEEDED",
        "components": components,
        "attestation": {
            "text": "I have verified the underlying repair is complete",
            "user_email": "operator@example.com",
            "timestamp_ms": 1735000000000,
        },
    }


# ---------------------------------------------------------------------------
# Test: below-threshold — inline components, no S3 key
# ---------------------------------------------------------------------------

def test_sovd_response_below_threshold_inlines(tables, handler_mod):
    """HARD GATE G: S3 branch does NOT fire when response payload is under 80 KB.

    When storage_uri is None (inline payload), the handler must:
    - Write dtc-history rows for each DTC in each component.
    - NOT write an s3Key field to the commands row.
    """
    commands_table, dtc_history_table = tables
    _seed_command(commands_table, "cmd-inline-1")

    event = _make_sovd_read_event("cmd-inline-1", storage_uri=None)
    handler_mod.handler(event, None)

    # Verify dtc-history row was written
    row = dtc_history_table.get_item(
        Key={"vehicleId": "VEH-TEST-001", "dtcCode": "P0420"}
    ).get("Item")
    assert row is not None, "dtc-history row should be written for inline response"
    assert row["source"] == "sovd"

    # Verify NO s3Key on commands row
    cmd_row = commands_table.get_item(Key={"commandId": "cmd-inline-1"}).get("Item", {})
    assert "s3Key" not in cmd_row, (
        "commands row must NOT carry s3Key when storage_uri is None (no S3 upload)"
    )


# ---------------------------------------------------------------------------
# Test: over-threshold — S3 key stored, dtc-history still written from summary
# ---------------------------------------------------------------------------

def test_sovd_response_over_threshold_uses_s3(tables, handler_mod):
    """HARD GATE G: response handler writes S3 key (not URL) to commands row when storage_uri present.

    When the sidecar uploads the full payload to S3 and provides storage_uri,
    the handler must:
    - Extract the S3 object KEY from the URI and store it on the commands row.
    - NOT store the full pre-signed URL or the s3:// URI.
    - Still write dtc-history rows from the payload's components (summary).
    """
    commands_table, dtc_history_table = tables
    _seed_command(commands_table, "cmd-s3-1")

    storage_uri = "s3://cms-test-storage-sovd-responses/VEH-TEST-001/corr-s3-1.json"
    event = _make_sovd_read_event("cmd-s3-1", storage_uri=storage_uri)
    handler_mod.handler(event, None)

    # Verify commands row has s3Key — the KEY portion only, not the full URI
    cmd_row = commands_table.get_item(Key={"commandId": "cmd-s3-1"}).get("Item", {})
    assert "s3Key" in cmd_row, "commands row must carry s3Key when storage_uri is present"
    s3_key = cmd_row["s3Key"]
    # Key should be the path component, not the full s3:// URI
    assert not s3_key.startswith("s3://"), (
        f"s3Key must be the object key, not the full URI. Got: {s3_key!r}"
    )
    assert "VEH-TEST-001/corr-s3-1.json" in s3_key, (
        f"s3Key should contain the object key path. Got: {s3_key!r}"
    )

    # dtc-history rows should still be written from the inline components summary
    row = dtc_history_table.get_item(
        Key={"vehicleId": "VEH-TEST-001", "dtcCode": "P0420"}
    ).get("Item")
    assert row is not None, "dtc-history row should be written even for S3-path response"


# ---------------------------------------------------------------------------
# Test: no pre-signed URL in any DDB row
# ---------------------------------------------------------------------------

def test_history_row_has_no_presigned_url(tables, handler_mod):
    """R2: dtc-history and commands DDB rows store the S3 object key, never a pre-signed URL.

    Pre-signed URLs contain query-string parameters like X-Amz-Signature and
    X-Amz-Expires.  Storing them would embed a bearer token with a fixed expiry
    in the audit record.  The handler must never write such strings.
    """
    commands_table, dtc_history_table = tables
    _seed_command(commands_table, "cmd-nopresign-1")

    storage_uri = "s3://cms-test-storage-sovd-responses/VEH-TEST-001/corr-nopresign.json"
    event = _make_sovd_read_event("cmd-nopresign-1", storage_uri=storage_uri)
    handler_mod.handler(event, None)

    # Check commands row
    cmd_row = commands_table.get_item(Key={"commandId": "cmd-nopresign-1"}).get("Item", {})
    for field_name, field_value in cmd_row.items():
        value_str = str(field_value)
        assert "X-Amz-Signature" not in value_str, (
            f"commands row field '{field_name}' contains X-Amz-Signature — "
            f"pre-signed URL must not be stored in DDB"
        )
        assert "X-Amz-Expires" not in value_str, (
            f"commands row field '{field_name}' contains X-Amz-Expires — "
            f"pre-signed URL must not be stored in DDB"
        )

    # Check dtc-history row
    history_row = dtc_history_table.get_item(
        Key={"vehicleId": "VEH-TEST-001", "dtcCode": "P0420"}
    ).get("Item", {})
    for field_name, field_value in history_row.items():
        value_str = str(field_value)
        assert "X-Amz-Signature" not in value_str, (
            f"dtc-history row field '{field_name}' contains X-Amz-Signature"
        )
        assert "X-Amz-Expires" not in value_str, (
            f"dtc-history row field '{field_name}' contains X-Amz-Expires"
        )


# ---------------------------------------------------------------------------
# Test: clear_dtcs marks CLEARED_REMOTE, never DeleteItem
# ---------------------------------------------------------------------------

def test_clear_dtcs_marks_history_row_cleared_remote(tables, handler_mod):
    """§ Design § 7: clear_dtcs response triggers UpdateItem setting status='CLEARED_REMOTE'.

    The handler must:
    - Set status='CLEARED_REMOTE' on matching dtc-history rows.
    - Set clearedAt, clearedBy (from attestation.user_email), clearedAttestation.
    - NEVER call DeleteItem — audit trail is write-once.
    """
    commands_table, dtc_history_table = tables
    _seed_command(commands_table, "cmd-clear-1")

    # Pre-seed a dtc-history row to be cleared
    dtc_history_table.put_item(Item={
        "vehicleId": "VEH-TEST-001",
        "dtcCode": "P0420",
        "source": "sovd",
        "commandId": "cmd-read-1",
        "firstSeenAt": 1735000000000,
        "lastSeenAt": 1735000000000,
        "occurrenceCount": 2,
    })

    event = _make_sovd_clear_event("cmd-clear-1", dtc_codes=["P0420"])
    handler_mod.handler(event, None)

    # Verify the history row was updated, not deleted
    row = dtc_history_table.get_item(
        Key={"vehicleId": "VEH-TEST-001", "dtcCode": "P0420"}
    ).get("Item")
    assert row is not None, (
        "dtc-history row must NOT be deleted — audit trail is write-once"
    )
    assert row.get("status") == "CLEARED_REMOTE", (
        f"status must be CLEARED_REMOTE, got: {row.get('status')!r}"
    )
    assert "clearedAt" in row, "clearedAt must be set"
    assert row.get("clearedBy") == "operator@example.com", (
        f"clearedBy must be attestation.user_email, got: {row.get('clearedBy')!r}"
    )
    assert "clearedAttestation" in row, "clearedAttestation sub-map must be set"
    attestation = row["clearedAttestation"]
    assert "text" in attestation, "clearedAttestation must carry attestation text"
    assert "user_email" in attestation, "clearedAttestation must carry user_email"


# ---------------------------------------------------------------------------
# Test: every dtc-history write carries source='sovd'
# ---------------------------------------------------------------------------

def test_sovd_dtc_history_write_carries_source_sovd(tables, handler_mod):
    """§ Design § 7: every dtc-history row written by SOVD response handler has source='sovd'.

    The source discriminator is critical for:
    - Filtering dtc-history by source in the UI.
    - Preventing SOVD rows from interfering with FWE-campaign rows.
    - Downstream dedup service routing (2026-06-17-dtc-dedup-first-last-seen).
    """
    commands_table, dtc_history_table = tables

    # Build multi-ECU, multi-DTC event
    components = {
        "ECU_ENGINE": {
            "id": "ECU_ENGINE",
            "protocol": "ISO 15765-4",
            "dtcs": [
                {"code": "P0420", "status": "confirmed",
                 "freeze_frame": {"engineRpm": {"value": 2800, "unit": "rpm",
                                                "timestamp": "2026-08-31T00:00:00Z"}}},
                {"code": "P0300", "status": "confirmed", "freeze_frame": {}},
            ],
        },
        "ECU_BODY": {
            "id": "ECU_BODY",
            "protocol": "ISO 15765-4",
            "dtcs": [
                {"code": "B0001", "status": "confirmed", "freeze_frame": {}},
            ],
        },
    }

    _seed_command(commands_table, "cmd-source-1")
    event = _make_sovd_read_event(
        "cmd-source-1",
        components=components,
        storage_uri=None,
    )
    handler_mod.handler(event, None)

    # All three DTC rows must carry source='sovd'
    for dtc_code in ["P0420", "P0300", "B0001"]:
        row = dtc_history_table.get_item(
            Key={"vehicleId": "VEH-TEST-001", "dtcCode": dtc_code}
        ).get("Item")
        assert row is not None, f"Expected dtc-history row for {dtc_code}"
        assert row.get("source") == "sovd", (
            f"dtc-history row for {dtc_code} has source={row.get('source')!r}, "
            f"expected 'sovd'"
        )


# ---------------------------------------------------------------------------
# Test: dedup window increments occurrenceCount
# ---------------------------------------------------------------------------

def test_sovd_dedup_window_increments_occurrence_count(tables, handler_mod):
    """D3: second SOVD read within 24 h window increments occurrenceCount in place.

    Dedup window anchors on firstSeenAt (NOT lastSeenAt).
    First call: PutItem succeeds, row created with occurrenceCount=1.
    Second call (within 24 h): PutItem fails ConditionalCheckFailed,
    UpdateItem increments occurrenceCount to 2 and updates lastSeenAt.
    """
    commands_table, dtc_history_table = tables

    # --- First scan (fresh row) ---
    _seed_command(commands_table, "cmd-dedup-1")
    event1 = _make_sovd_read_event("cmd-dedup-1", storage_uri=None)
    handler_mod.handler(event1, None)

    row_after_first = dtc_history_table.get_item(
        Key={"vehicleId": "VEH-TEST-001", "dtcCode": "P0420"}
    ).get("Item")
    assert row_after_first is not None, "Row must exist after first scan"
    assert int(row_after_first["occurrenceCount"]) == 1, (
        f"occurrenceCount should be 1 after first scan, got {row_after_first['occurrenceCount']}"
    )

    # --- Second scan (duplicate within window) ---
    # The second event comes in quickly (within 24 h window).
    # The handler should detect the existing row's firstSeenAt is recent and
    # increment occurrenceCount instead of inserting a new row.
    _seed_command(commands_table, "cmd-dedup-2")
    event2 = _make_sovd_read_event("cmd-dedup-2", storage_uri=None)
    handler_mod.handler(event2, None)

    row_after_second = dtc_history_table.get_item(
        Key={"vehicleId": "VEH-TEST-001", "dtcCode": "P0420"}
    ).get("Item")
    assert row_after_second is not None, "Row must still exist after second scan"
    assert int(row_after_second["occurrenceCount"]) == 2, (
        f"occurrenceCount should be 2 after second scan within 24 h window, "
        f"got {row_after_second['occurrenceCount']}"
    )

    # Verify there is still only ONE row (dedup — not two separate rows)
    all_items = dtc_history_table.scan(
        FilterExpression=boto3.dynamodb.conditions.Attr("dtcCode").eq("P0420")
        & boto3.dynamodb.conditions.Attr("vehicleId").eq("VEH-TEST-001")
    ).get("Items", [])
    assert len(all_items) == 1, (
        f"Dedup should result in exactly 1 row; found {len(all_items)}"
    )


# ---------------------------------------------------------------------------
# FG2.2: _extract_s3_key_from_uri hardening tests
# ---------------------------------------------------------------------------

def test_extract_s3_key_rejects_https_presigned_url(tables, handler_mod):
    """FG2.2 / SR Cycle 2 Warning #2: https:// pre-signed URL is rejected; DDB row has no X-Amz-* params.

    A rogue sidecar could supply a pre-signed URL as storage_uri to inject a bearer
    token into the DDB audit record. _extract_s3_key_from_uri must return None for
    any non-s3:// URI, and the handler must NOT store the string in the DDB s3Key field.
    """
    commands_table, dtc_history_table = tables
    _seed_command(commands_table, "cmd-presign-reject-1")

    # Simulate a rogue sidecar providing a pre-signed URL instead of an s3:// URI
    presigned_url = (
        "https://cms-staging-storage-sovd-responses-us-west-2-123456789012"
        ".s3.amazonaws.com/VIN-TEST/abc.json"
        "?X-Amz-Signature=deadbeef&X-Amz-Expires=900"
    )

    # Direct test of the helper function
    from command_response_handler import _extract_s3_key_from_uri
    result = _extract_s3_key_from_uri(presigned_url)
    assert result is None, (
        f"_extract_s3_key_from_uri must return None for https:// URI; got {result!r}"
    )

    # End-to-end: feed the presigned URL through the handler and check the DDB row
    event = _make_sovd_read_event("cmd-presign-reject-1", storage_uri=presigned_url)
    handler_mod.handler(event, None)

    cmd_row = commands_table.get_item(Key={"commandId": "cmd-presign-reject-1"}).get("Item", {})
    # The DDB row must NOT contain X-Amz-Signature or X-Amz-Expires anywhere
    row_str = str(cmd_row)
    assert "X-Amz-Signature" not in row_str, (
        "DDB commands row must not contain X-Amz-Signature — "
        "pre-signed URL was incorrectly stored"
    )
    assert "X-Amz-Expires" not in row_str, (
        "DDB commands row must not contain X-Amz-Expires — "
        "pre-signed URL was incorrectly stored"
    )
    # s3Key field must be absent
    assert "s3Key" not in cmd_row, (
        f"DDB commands row must not have s3Key when a non-s3:// URI was supplied; "
        f"row: {cmd_row}"
    )


def test_extract_s3_key_accepts_s3_scheme(handler_mod):
    """FG2.2: s3:// URI is accepted; returns the object key."""
    from command_response_handler import _extract_s3_key_from_uri

    s3_uri = "s3://cms-staging-storage-sovd-responses-us-west-2-123456789012/VIN-TEST/abc.json"
    result = _extract_s3_key_from_uri(s3_uri)
    assert result == "VIN-TEST/abc.json", (
        f"_extract_s3_key_from_uri must return 'VIN-TEST/abc.json' for s3:// URI; "
        f"got {result!r}"
    )



# ---------------------------------------------------------------------------
# Group 2A T2A.1: response Map persistence (decisions.md 2026-09-13)
# ---------------------------------------------------------------------------

def test_response_payload_persisted(tables, handler_mod):
    """G2A T2A.1: inline SOVD response is written as `response` Map on the command row.

    When no storage_uri is present (payload below the 80 KB sidecar threshold),
    the full parsed SOVD JSON payload must be persisted as a `response` Map attribute
    on the DDB command row alongside status, respondedAt, and latencyMs.
    """
    commands_table, _dtc_history_table = tables
    _seed_command(commands_table, "cmd-resp-inline-1")

    event = _make_sovd_read_event("cmd-resp-inline-1", storage_uri=None)
    handler_mod.handler(event, None)

    cmd_row = commands_table.get_item(Key={"commandId": "cmd-resp-inline-1"}).get("Item", {})
    assert "response" in cmd_row, (
        "commands row must carry `response` Map when payload is inline (no storage_uri)"
    )
    # The response attribute must be a dict (DynamoDB Map)
    resp = cmd_row["response"]
    assert isinstance(resp, dict), (
        f"`response` must be a Map, got {type(resp).__name__}"
    )
    # It must carry identifying fields from the payload
    assert resp.get("commandId") == "cmd-resp-inline-1" or resp.get("commandType") == "sovd_read_dtcs", (
        f"`response` Map should contain payload fields, got keys: {list(resp.keys())}"
    )

    # Existing fields must still be present (additive, not replacing)
    assert cmd_row.get("status") == "SUCCEEDED"
    assert "respondedAt" in cmd_row


def test_response_not_persisted_when_storage_uri_present(tables, handler_mod):
    """G2A T2A.1: when storage_uri is present, the payload is in S3 and `response` is NOT inlined.

    The sidecar has already offloaded the payload to S3 and stored the key in s3Key.
    Inlining would duplicate data; the client reads from S3 via the s3Key pointer.
    """
    commands_table, _dtc_history_table = tables
    _seed_command(commands_table, "cmd-resp-s3-path-1")

    storage_uri = "s3://cms-test-storage-sovd-responses/VEH-TEST-001/corr-s3-path.json"
    event = _make_sovd_read_event("cmd-resp-s3-path-1", storage_uri=storage_uri)
    handler_mod.handler(event, None)

    cmd_row = commands_table.get_item(Key={"commandId": "cmd-resp-s3-path-1"}).get("Item", {})
    assert "response" not in cmd_row, (
        "commands row must NOT carry `response` when storage_uri is present "
        "(payload lives in S3, accessed via s3Key)"
    )
    # s3Key must still be written
    assert "s3Key" in cmd_row, (
        "commands row must carry s3Key when storage_uri is present"
    )


def test_oversized_response_payload_status_still_succeeds(tables, handler_mod):
    """G2A T2A.1 CRITICAL INVARIANT: oversized payload must NOT cause the status write to fail.

    The `run_routine` sim path bypasses the sidecar's 80 KB size guard
    (storage_uri=None, early-return before threshold check). If an oversized body
    were written as `response` in the same update_item call as status/respondedAt,
    DDB would reject the whole call and the command would LOSE its terminal status —
    worse than today's discard behaviour.

    This test verifies that when the payload exceeds _RESPONSE_SIZE_THRESHOLD_BYTES:
    - status is still written as SUCCEEDED (the status write succeeded)
    - respondedAt is still written
    - `response` is NOT written (guard correctly discarded the oversized body)
    """
    commands_table, _dtc_history_table = tables
    _seed_command(commands_table, "cmd-resp-oversized-1")

    # Build a payload that exceeds 80 KB. Use a large components dict.
    big_components = {
        "ECU_ENGINE": {
            "id": "ECU_ENGINE",
            "protocol": "ISO 15765-4",
            # Pad with a large blob to exceed the threshold
            "raw_data": "x" * (handler_mod._RESPONSE_SIZE_THRESHOLD_BYTES + 1000),
            "dtcs": [{"code": "P0420", "status": "confirmed", "freeze_frame": {}}],
        }
    }

    event = {
        "topic": "cms/commands/things/1FT8W3DT5MEC55401/executions/corr-oversized-1/sovd/response",
        "vehicleId": "VEH-TEST-001",
        "commandId": "cmd-resp-oversized-1",
        "correlationId": "corr-oversized-1",
        "commandType": "sovd_read_dtcs",
        "status": "SUCCEEDED",
        "components": big_components,
        "storage_uri": None,
    }
    handler_mod.handler(event, None)

    cmd_row = commands_table.get_item(Key={"commandId": "cmd-resp-oversized-1"}).get("Item", {})

    # Critical: the terminal status MUST be written even when the payload is too large
    assert cmd_row.get("status") == "SUCCEEDED", (
        "CRITICAL: oversized response payload must NOT prevent the status write. "
        f"Got status={cmd_row.get('status')!r}. "
        "The `response` write is a separate, best-effort enrichment."
    )
    assert "respondedAt" in cmd_row, (
        "respondedAt must be written even when the response payload is too large to inline"
    )

    # The oversized payload must NOT be written as `response`
    assert "response" not in cmd_row, (
        "commands row must NOT carry `response` Map when payload exceeds "
        f"{handler_mod._RESPONSE_SIZE_THRESHOLD_BYTES} bytes"
    )


def test_progress_branch_does_not_write_response(tables, handler_mod):
    """G2A T2A.1: PROGRESS messages must not trigger the response-payload write.

    PROGRESS is not a terminal status and must only update the progress sub-map,
    not the status/respondedAt/response fields.
    """
    commands_table, _dtc_history_table = tables
    _seed_command(commands_table, "cmd-resp-progress-1")

    event = {
        "topic": "cms/commands/things/1FT8W3DT5MEC55401/executions/corr-prog-1/sovd/response",
        "vehicleId": "VEH-TEST-001",
        "commandId": "cmd-resp-progress-1",
        "correlationId": "corr-prog-1",
        "commandType": "sovd_read_dtcs",
        "status": "PROGRESS",
        "progress": {"ecu_name": "ECU_ENGINE", "ecu_index": 1, "ecu_total": 3},
        "components": {},
        "storage_uri": None,
    }
    handler_mod.handler(event, None)

    cmd_row = commands_table.get_item(Key={"commandId": "cmd-resp-progress-1"}).get("Item", {})
    # PROGRESS must not write `response`
    assert "response" not in cmd_row, (
        "PROGRESS message must not write `response` Map — only terminal statuses do"
    )
    # PROGRESS must not change the status field either
    assert cmd_row.get("status") == "SENT", (
        "PROGRESS must not change the row's status from SENT to PROGRESS"
    )
