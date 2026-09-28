"""
Regression tests for services/commands/command_response_handler.py — issue
2026-08-04-fwe-remote-commands-not-actuating.

Covers the two defects fixed there plus the race the first fix exposed:

  - § D2  the protobuf branch must actually decode (it used to raise
          ModuleNotFoundError: No module named 'google' because the Lambda was
          bundled from the raw source dir instead of the `.build` asset). Here
          that surfaces simply as "the protobuf path parses and updates DDB".

  - § D6  a non-SUCCEEDED response must NOT move a command out of SUCCEEDED.
          On an FWE-path vehicle two responders answer the same command and
          disagree: the fwe-simulator (which actually actuates) says SUCCEEDED
          over JSON, and the FWE agent says FAILED/reasonCode 3 over protobuf
          because FWE-native actuation is unwired (§ D3). The agent is slower,
          so last-write-wins reported a failure for a door that did lock.

  - reasonCode is persisted. FWE rejects with a populated numeric reason_code
    and an EMPTY reason_description, so dropping the code left a bare FAILED
    with nothing to diagnose from.

Uses moto==5.0.10, matching test_commands_lambda.py conventions.
"""
import importlib
import os
import sys

import boto3
import pytest
from moto import mock_aws

_STAGE = "test"
_REGION = "us-west-2"
_COMMANDS_TABLE = f"cms-{_STAGE}-storage-commands"

os.environ.setdefault("AWS_DEFAULT_REGION", _REGION)
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ["DEPLOYMENT_STAGE"] = _STAGE
os.environ["COMMANDS_TABLE"] = _COMMANDS_TABLE

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


@pytest.fixture
def handler_mod():
    """Import the handler inside a moto context with a fresh commands table."""
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=_REGION)
        ddb.create_table(
            TableName=_COMMANDS_TABLE,
            KeySchema=[{"AttributeName": "commandId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "commandId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        sys.modules.pop("command_response_handler", None)
        import command_response_handler as mod  # noqa: E402
        importlib.reload(mod)
        yield mod


def _table():
    return boto3.resource("dynamodb", region_name=_REGION).Table(_COMMANDS_TABLE)


def _seed(command_id: str, status: str = "SENT") -> None:
    _table().put_item(Item={
        "commandId": command_id,
        "vehicleId": "VEH-MICH-001",
        "commandName": "lock_all_doors",
        "status": status,
        "timestamp": 1785854347000,
    })


def _row(command_id: str) -> dict:
    return _table().get_item(Key={"commandId": command_id}).get("Item", {})


def _protobuf_response(command_id: str, status: int, reason_code: int = 0,
                       reason: str = "") -> bytes:
    import command_response_pb2 as resp_pb
    r = resp_pb.CommandResponse()
    r.command_id = command_id
    r.status = status
    r.reason_code = reason_code
    r.reason_description = reason
    return r.SerializeToString()


def _b64_event(payload: bytes, vehicle_id: str = "1FT8W3DT5MEC55401") -> dict:
    import base64
    return {"b64_payload": base64.b64encode(payload).decode(), "vehicleId": vehicle_id}


# ---------------------------------------------------------------------------
# § D2 — the protobuf branch parses at all
# ---------------------------------------------------------------------------


def test_protobuf_response_parses_and_updates_status(handler_mod):
    """FWE protobuf FAILED (status enum 4) lands on a SENT row."""
    _seed("cmd-pb-1", status="SENT")
    handler_mod.handler(_b64_event(_protobuf_response("cmd-pb-1", 4, reason_code=3)), None)

    row = _row("cmd-pb-1")
    assert row["status"] == "FAILED"
    assert "respondedAt" in row


def test_protobuf_reason_code_is_persisted(handler_mod):
    """reason_code must be stored — FWE sends it with an EMPTY description."""
    _seed("cmd-pb-2", status="SENT")
    handler_mod.handler(_b64_event(_protobuf_response("cmd-pb-2", 4, reason_code=3, reason="")), None)

    row = _row("cmd-pb-2")
    assert int(row["reasonCode"]) == 3, (
        "reasonCode 3 == REASON_CODE_NO_DECODING_RULES_FOUND; without it a bare "
        "FAILED carries no diagnosis"
    )
    assert "reason" not in row  # empty description is not written


# ---------------------------------------------------------------------------
# § D6 — terminal SUCCEEDED must not be clobbered
# ---------------------------------------------------------------------------


def test_failed_does_not_overwrite_succeeded(handler_mod):
    """The slower FWE-agent FAILED must not overwrite the simulator's SUCCEEDED."""
    _seed("cmd-race-1", status="SUCCEEDED")
    handler_mod.handler(_b64_event(_protobuf_response("cmd-race-1", 4, reason_code=3)), None)

    row = _row("cmd-race-1")
    assert row["status"] == "SUCCEEDED", (
        "a non-success response must not move a command out of SUCCEEDED — the "
        "simulator is the component that actually actuates"
    )
    assert "reasonCode" not in row, "the losing response must not mutate the row at all"


def test_timeout_does_not_overwrite_succeeded(handler_mod):
    """Same guard applies to TIMEOUT (status enum 2), not just FAILED."""
    _seed("cmd-race-2", status="SUCCEEDED")
    handler_mod.handler(_b64_event(_protobuf_response("cmd-race-2", 2)), None)

    assert _row("cmd-race-2")["status"] == "SUCCEEDED"


def test_succeeded_does_overwrite_failed(handler_mod):
    """Order-independence: a SUCCEEDED arriving after a FAILED must win."""
    _seed("cmd-race-3", status="FAILED")
    handler_mod.handler(
        {"commandId": "cmd-race-3", "status": "SUCCEEDED", "vehicleId": "VEH-MICH-001"},
        None,
    )

    assert _row("cmd-race-3")["status"] == "SUCCEEDED", (
        "the guard is one-directional — success must still be able to land"
    )


def test_json_branch_still_updates_normally(handler_mod):
    """MQTT-direct JSON path (the working reference) is unaffected by the guard."""
    _seed("cmd-json-1", status="SENT")
    handler_mod.handler(
        {"commandId": "cmd-json-1", "status": "SUCCEEDED", "vehicleId": "VEH-1780081115",
         "reason": ""},
        None,
    )

    row = _row("cmd-json-1")
    assert row["status"] == "SUCCEEDED"
    assert int(row["latencyMs"]) > 0


def test_missing_command_id_is_a_noop(handler_mod):
    """Guards the early-return branch."""
    handler_mod.handler({"status": "SUCCEEDED"}, None)  # must not raise



# ---------------------------------------------------------------------------
# T2.2 — verdict + result survive into DDB response Map via _persist_response_payload
# (spec: 2026-09-10-cms-sovd-routine-result-contracts, Group 2 T2.2)
# ---------------------------------------------------------------------------

@pytest.fixture
def handler_mod_with_dtc(tmp_path):
    """Import the handler inside a moto context with commands + dtc-history tables.

    T2.2 sends a SOVD-topic event, which routes through _handle_sovd_response.
    That function does NOT write dtc-history for run_routine, but moto still
    requires the table to exist because the module-level ddb.Table() call
    references it at import time.
    """
    _dtc_table = f"cms-{_STAGE}-storage-dtc-history"
    os.environ["DTC_HISTORY_TABLE"] = _dtc_table
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=_REGION)
        # commands table
        ddb.create_table(
            TableName=_COMMANDS_TABLE,
            KeySchema=[{"AttributeName": "commandId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "commandId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        # dtc-history table (needed because the module references it at import)
        ddb.create_table(
            TableName=_dtc_table,
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
        sys.modules.pop("command_response_handler", None)
        import command_response_handler as mod  # noqa: E402
        importlib.reload(mod)
        yield mod


def _sovd_run_routine_event(command_id: str, verdict: str, result: dict) -> dict:
    """Build a minimal SOVD run_routine response event that exercises _persist_response_payload.

    The event includes the /sovd/response topic suffix so the handler routes
    to _handle_sovd_response.  The payload has no storage_uri so
    _persist_response_payload is called (not the S3 path).

    All JSON payload fields are inlined into the event dict (matching the
    IoT rule SELECT * behaviour described in command_response_handler.py).
    """
    return {
        "topic": f"cms/commands/things/VIN-TEST-001/executions/{command_id}/sovd/response",
        "vehicleId": "VEH-MRDN-0001",
        # Inlined payload fields
        "correlation_id": command_id,
        "commandId": command_id,
        "status": "SUCCEEDED",
        "command_type": "run_routine",
        "commandType": "run_routine",
        "components": {"routine_id": "lamp_self_check"},
        "result": result,
        "verdict": verdict,
        "storage_uri": None,  # explicit None → _persist_response_payload is called
        "latency_ms": 42,
    }


def test_verdict_and_result_persisted(handler_mod_with_dtc):
    """T2.2 — verdict + result survive into DDB response Map.

    Sends a synthetic sidecar message with verdict='out_of_spec' and a
    result dict, verifies both fields appear in the DDB row's response Map.

    This test confirms the G2A _persist_response_payload write path (v1.5
    Group 2A) carries new fields unchanged — NOT re-implementing the write.
    """
    cmd_id = "run-routine-t22-001"
    expected_verdict = "out_of_spec"
    expected_result = {
        "lamps": ["ok", "ok", "open_circuit", "ok", "ok", "ok", "ok", "ok"],
        "ambient_lux": 3141,
    }

    _table().put_item(Item={
        "commandId": cmd_id,
        "vehicleId": "VEH-MRDN-0001",
        "commandName": "run_routine",
        "status": "SENT",
        "timestamp": 1785854347000,
    })

    event = _sovd_run_routine_event(cmd_id, expected_verdict, expected_result)
    handler_mod_with_dtc.handler(event, None)

    row = _row(cmd_id)

    # Terminal status must be written
    assert row.get("status") == "SUCCEEDED", (
        f"expected status SUCCEEDED, got {row.get('status')!r}"
    )

    # response Map must exist (written by _persist_response_payload)
    response_map = row.get("response")
    assert response_map is not None, (
        "DDB row must have a 'response' Map attribute — "
        "_persist_response_payload was not called or failed"
    )

    # verdict must survive unchanged
    assert response_map.get("verdict") == expected_verdict, (
        f"response.verdict={response_map.get('verdict')!r}, "
        f"expected {expected_verdict!r}. "
        "verdict field must ride _persist_response_payload unchanged."
    )

    # result must survive unchanged
    assert response_map.get("result") == expected_result, (
        f"response.result={response_map.get('result')!r}, "
        f"expected {expected_result!r}. "
        "result field must ride _persist_response_payload unchanged."
    )



# ---------------------------------------------------------------------------
# Float payloads must persist — issues/2026-09-23-iot-rule-destroys-arrays-in-
# sovd-responses/ (the second defect, unmasked by fixing the IoT rule)
#
# DynamoDB's resource API rejects Python floats, and _persist_response_payload's
# except-clause is deliberately non-fatal — so a single float anywhere in the
# payload used to drop the ENTIRE response attribute with only a WARNING,
# leaving a SUCCEEDED row carrying no result.
#
# 3 of the 6 pilot routines emit floats (cell_balance_check, evap_leak_test,
# pack_isolation_test), so none of them had ever had a response persisted. It
# stayed hidden because the SOVD IoT rule was independently stripping the
# float-bearing arrays before they reached the handler.
#
# test_verdict_and_result_persisted above uses int-only values (ambient_lux:
# 3141), which is precisely why it never caught this.
# ---------------------------------------------------------------------------

def test_float_payload_persists_scalars_and_arrays(handler_mod_with_dtc):
    """A float-bearing result must land in DDB, at scalar AND array depth.

    Mirrors cell_balance_check's real shape: an array of floats plus a float
    scalar. Pre-fix this produced a SUCCEEDED row with NO response attribute.
    """
    cmd_id = "run-routine-float-001"
    expected_verdict = "in_spec"
    expected_result = {
        "cell_voltages": [3.912, 3.907, 3.915, 3.911],   # floats inside a list
        "max_delta_mv": 8.0,                              # float scalar
        "module_count": 4,                                # int must still work
    }

    _table().put_item(Item={
        "commandId": cmd_id,
        "vehicleId": "VEH-MRDN-0001",
        "commandName": "run_routine",
        "status": "SENT",
        "timestamp": 1785854347000,
    })

    event = _sovd_run_routine_event(cmd_id, expected_verdict, expected_result)
    handler_mod_with_dtc.handler(event, None)

    row = _row(cmd_id)

    assert row.get("status") == "SUCCEEDED", (
        f"expected status SUCCEEDED, got {row.get('status')!r}"
    )

    # The headline regression: the attribute existed at all.
    response_map = row.get("response")
    assert response_map is not None, (
        "DDB row has NO 'response' attribute for a float-bearing payload. "
        "This is the pre-fix behaviour: boto3 raises 'Float types are not "
        "supported. Use Decimal types instead.', _persist_response_payload "
        "swallows it as a non-fatal enrichment failure, and the whole result "
        "is lost while the row still reads SUCCEEDED. Convert floats to "
        "Decimal before update_item."
    )

    result = response_map.get("result")
    assert result is not None, "response.result missing entirely"

    # Floats come back as Decimal (DDB's only numeric type). Compare by value,
    # not identity: Decimal('3.912') != the float 3.912 under ==, because the
    # float is really 3.91200000000000014...
    cells = result.get("cell_voltages")
    assert cells is not None, (
        "response.result.cell_voltages is missing — the float ARRAY was dropped "
        "even though the attribute was written. Conversion must recurse into lists."
    )
    assert len(cells) == 4, f"expected 4 cell voltages, got {len(cells)}"
    assert [float(c) for c in cells] == expected_result["cell_voltages"], (
        f"cell_voltages round-tripped as {[float(c) for c in cells]}, "
        f"expected {expected_result['cell_voltages']}"
    )

    assert float(result.get("max_delta_mv")) == expected_result["max_delta_mv"], (
        f"max_delta_mv round-tripped as {result.get('max_delta_mv')!r}, "
        f"expected {expected_result['max_delta_mv']}"
    )

    # Ints must not regress while fixing floats.
    assert int(result.get("module_count")) == expected_result["module_count"], (
        f"module_count round-tripped as {result.get('module_count')!r}, "
        f"expected {expected_result['module_count']}"
    )


def test_float_only_scalar_payload_persists(handler_mod_with_dtc):
    """A single float scalar, no arrays — evap_leak_test / pack_isolation_test shape.

    Separate from the array case so a conversion that handles only lists (or
    only top-level scalars) cannot pass both.
    """
    cmd_id = "run-routine-float-002"
    expected_result = {"system_pressure_kpa": 101.3, "leak_rate_ccm": 0.42}

    _table().put_item(Item={
        "commandId": cmd_id,
        "vehicleId": "VEH-MRDN-0001",
        "commandName": "run_routine",
        "status": "SENT",
        "timestamp": 1785854347000,
    })

    handler_mod_with_dtc.handler(
        _sovd_run_routine_event(cmd_id, "in_spec", expected_result), None
    )

    response_map = _row(cmd_id).get("response")
    assert response_map is not None, (
        "float-scalar payload produced no 'response' attribute — see "
        "test_float_payload_persists_scalars_and_arrays for the mechanism"
    )
    result = response_map.get("result") or {}
    for key, want in expected_result.items():
        got = result.get(key)
        assert got is not None, f"response.result.{key} missing"
        assert float(got) == want, f"{key} round-tripped as {got!r}, expected {want}"
