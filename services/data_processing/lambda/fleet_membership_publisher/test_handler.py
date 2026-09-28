"""Unit tests for fleet_membership_publisher.handler.

Spec: DMS `2026-09-05-dms-fleet-membership-projection`, T4.2.

The tests exercise the four event paths (INSERT, MODIFY, REMOVE, MODIFY-no-change)
against stubbed boto3 clients. Per spec R3 and CMS review Cycle 7's lesson: a stub
cannot fail the way a service fails, so a live verification step (DMS T5.1) is the
final gate. These tests catch structural / logical defects, not service integration.

Run:
  cd services/data_processing/lambda/fleet_membership_publisher
  python -m pytest test_handler.py -v
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Import fixture — reload the handler module with env vars set so module-level
# constants (_DMS_BUS_NAME, _VEHICLES_TABLE) pick up the test config.
# ---------------------------------------------------------------------------

@pytest.fixture
def handler_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("DMS_EVENT_BUS_NAME", "dms-test-events")
    monkeypatch.setenv("VEHICLES_TABLE_NAME", "cms-test-storage-vehicles")
    monkeypatch.setenv("AWS_REGION", "us-west-2")

    # Ensure the local package path is importable and reload if already loaded.
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)

    if "handler" in sys.modules:
        del sys.modules["handler"]
    module = importlib.import_module("handler")
    return module


# ---------------------------------------------------------------------------
# Stub clients — used to intercept boto3 calls
# ---------------------------------------------------------------------------

class _FakeDdb:
    """Minimal DDB stub — get_item returns whatever the fixture wires up."""

    def __init__(self, id_to_vin: dict[str, str]):
        self._id_to_vin = id_to_vin
        self.get_item_calls: list[dict] = []

    def get_item(self, **kwargs: Any) -> dict:
        self.get_item_calls.append(kwargs)
        vid = kwargs["Key"]["vehicleId"]["S"]
        vin = self._id_to_vin.get(vid)
        if vin is None:
            return {}
        return {"Item": {"vin": {"S": vin}}}


class _FakeEvents:
    """PutEvents stub. `failed_count` and `error_code` shape the response."""

    def __init__(
        self,
        failed_count: int = 0,
        error_code: str | None = None,
        raise_exc: type[Exception] | None = None,
    ):
        self.failed_count = failed_count
        self.error_code = error_code
        self.raise_exc = raise_exc
        self.put_events_calls: list[dict] = []

    def put_events(self, **kwargs: Any) -> dict:
        self.put_events_calls.append(kwargs)
        if self.raise_exc is not None:
            raise self.raise_exc("boom")
        entries = kwargs.get("Entries", [])
        resp_entries = [{} for _ in entries]
        if self.failed_count and resp_entries:
            resp_entries[0] = {
                "ErrorCode": self.error_code or "InternalFailure",
                "ErrorMessage": "test failure",
            }
        return {
            "FailedEntryCount": self.failed_count,
            "Entries": resp_entries,
        }


def _wire(monkeypatch: pytest.MonkeyPatch, mod: Any, ddb: Any, events: Any) -> None:
    """Replace the module's lazy client getters with fixtures."""
    monkeypatch.setattr(mod, "_get_ddb", lambda: ddb)
    monkeypatch.setattr(mod, "_get_events", lambda: events)


# ---------------------------------------------------------------------------
# Stream record builders — mimic AWS DDB Streams shape.
# ---------------------------------------------------------------------------

def _ddbjson(d: dict[str, str]) -> dict:
    return {k: {"S": v} for k, v in d.items()}


def _record_insert(fleet_id: str, vehicle_id: str, seq: str = "seq-ins-1") -> dict:
    return {
        "eventName": "INSERT",
        "dynamodb": {
            "SequenceNumber": seq,
            "NewImage": _ddbjson(
                {
                    "PK": f"FLEET#{fleet_id}",
                    "SK": f"VEHICLE#{vehicle_id}",
                    "fleetId": fleet_id,
                    "vehicleId": vehicle_id,
                }
            ),
        },
    }


def _record_remove(fleet_id: str, vehicle_id: str, seq: str = "seq-rem-1") -> dict:
    return {
        "eventName": "REMOVE",
        "dynamodb": {
            "SequenceNumber": seq,
            "OldImage": _ddbjson(
                {
                    "PK": f"FLEET#{fleet_id}",
                    "SK": f"VEHICLE#{vehicle_id}",
                    "fleetId": fleet_id,
                    "vehicleId": vehicle_id,
                }
            ),
        },
    }


def _record_modify_fleet_change(
    old_fleet: str, new_fleet: str, vehicle_id: str, seq: str = "seq-mod-1"
) -> dict:
    return {
        "eventName": "MODIFY",
        "dynamodb": {
            "SequenceNumber": seq,
            "OldImage": _ddbjson(
                {
                    "PK": f"FLEET#{old_fleet}",
                    "SK": f"VEHICLE#{vehicle_id}",
                    "fleetId": old_fleet,
                    "vehicleId": vehicle_id,
                }
            ),
            "NewImage": _ddbjson(
                {
                    "PK": f"FLEET#{new_fleet}",
                    "SK": f"VEHICLE#{vehicle_id}",
                    "fleetId": new_fleet,
                    "vehicleId": vehicle_id,
                }
            ),
        },
    }


def _record_modify_no_fleet_change(
    fleet_id: str, vehicle_id: str, seq: str = "seq-mod-2"
) -> dict:
    """MODIFY that changes a non-fleet attribute. Must produce no event."""
    return {
        "eventName": "MODIFY",
        "dynamodb": {
            "SequenceNumber": seq,
            "OldImage": _ddbjson(
                {
                    "PK": f"FLEET#{fleet_id}",
                    "SK": f"VEHICLE#{vehicle_id}",
                    "fleetId": fleet_id,
                    "vehicleId": vehicle_id,
                    "note": "before",
                }
            ),
            "NewImage": _ddbjson(
                {
                    "PK": f"FLEET#{fleet_id}",
                    "SK": f"VEHICLE#{vehicle_id}",
                    "fleetId": fleet_id,
                    "vehicleId": vehicle_id,
                    "note": "after",
                }
            ),
        },
    }


# ---------------------------------------------------------------------------
# Tests — event shape (contract with DMS ingest)
# ---------------------------------------------------------------------------

_VIN_MRDN = "MRDN0000000000005"
_VIN_DEMO = "DEMO0000000000005"
_VID_MRDN = "VEH-MRDN-0005"
_VID_DEMO = "VEH-DEMO-PUB-005"


def test_insert_publishes_enrolled_with_resolved_vin(
    handler_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    ddb = _FakeDdb({_VID_MRDN: _VIN_MRDN})
    events = _FakeEvents()
    _wire(monkeypatch, handler_module, ddb, events)

    result = handler_module.handler(
        {"Records": [_record_insert("fleet-alpha-001", _VID_MRDN)]}, None
    )
    assert result == {"batchItemFailures": []}
    assert len(events.put_events_calls) == 1
    entries = events.put_events_calls[0]["Entries"]
    assert len(entries) == 1
    detail = json.loads(entries[0]["Detail"])
    assert entries[0]["Source"] == "cms.fleet"
    assert entries[0]["DetailType"] == "CMS Fleet Membership Changed"
    assert entries[0]["EventBusName"] == "dms-test-events"
    assert detail["vin"] == _VIN_MRDN
    assert detail["fleet_id"] == "fleet-alpha-001"
    assert detail["action"] == "enrolled"
    assert detail["occurred_at"].endswith("Z")


def test_remove_publishes_unenrolled_from_old_image(
    handler_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    ddb = _FakeDdb({_VID_MRDN: _VIN_MRDN})
    events = _FakeEvents()
    _wire(monkeypatch, handler_module, ddb, events)

    result = handler_module.handler(
        {"Records": [_record_remove("fleet-alpha-001", _VID_MRDN)]}, None
    )
    assert result == {"batchItemFailures": []}
    entries = events.put_events_calls[0]["Entries"]
    detail = json.loads(entries[0]["Detail"])
    assert detail["action"] == "unenrolled"
    assert detail["vin"] == _VIN_MRDN


def test_modify_with_fleet_change_publishes_unenroll_old_plus_enroll_new(
    handler_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The security-critical case per spec D3.

    A projection that only ever grows is a projection that leaks. A MODIFY that
    swaps a vehicle from fleet A to fleet B must generate two events; if only
    the enroll fires, the vehicle stays visible to fleet A's callers forever.
    """
    ddb = _FakeDdb({_VID_MRDN: _VIN_MRDN})
    events = _FakeEvents()
    _wire(monkeypatch, handler_module, ddb, events)

    result = handler_module.handler(
        {
            "Records": [
                _record_modify_fleet_change("fleet-alpha", "fleet-beta", _VID_MRDN)
            ]
        },
        None,
    )
    assert result == {"batchItemFailures": []}
    entries = events.put_events_calls[0]["Entries"]
    assert len(entries) == 2, "MODIFY with fleet change must emit BOTH events"
    details = [json.loads(e["Detail"]) for e in entries]
    actions = {d["action"]: d["fleet_id"] for d in details}
    assert actions == {"unenrolled": "fleet-alpha", "enrolled": "fleet-beta"}
    # Both entries share the same VIN, resolved once per pair.
    assert all(d["vin"] == _VIN_MRDN for d in details)


def test_modify_with_no_fleet_change_publishes_nothing(
    handler_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    ddb = _FakeDdb({_VID_MRDN: _VIN_MRDN})
    events = _FakeEvents()
    _wire(monkeypatch, handler_module, ddb, events)

    result = handler_module.handler(
        {"Records": [_record_modify_no_fleet_change("fleet-alpha", _VID_MRDN)]},
        None,
    )
    assert result == {"batchItemFailures": []}
    assert events.put_events_calls == [], "MODIFY without fleet change must be silent"


def test_batch_of_mixed_events(
    handler_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    ddb = _FakeDdb({_VID_MRDN: _VIN_MRDN, _VID_DEMO: _VIN_DEMO})
    events = _FakeEvents()
    _wire(monkeypatch, handler_module, ddb, events)

    result = handler_module.handler(
        {
            "Records": [
                _record_insert("fleet-alpha", _VID_MRDN, "seq-1"),
                _record_remove("fleet-beta", _VID_DEMO, "seq-2"),
                _record_modify_no_fleet_change("fleet-gamma", _VID_MRDN, "seq-3"),
            ]
        },
        None,
    )
    assert result == {"batchItemFailures": []}
    # 2 records produced events, 1 was silent — expect 2 PutEvents calls.
    assert len(events.put_events_calls) == 2


# ---------------------------------------------------------------------------
# Tests — resilience (spec D3: skip unresolvable, retry transient)
# ---------------------------------------------------------------------------

def test_unresolvable_vehicle_id_is_skipped_without_batch_failure(
    handler_module, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    ddb = _FakeDdb({})  # empty — every lookup misses
    events = _FakeEvents()
    _wire(monkeypatch, handler_module, ddb, events)

    result = handler_module.handler(
        {"Records": [_record_insert("fleet-alpha-001", "VEH-DOES-NOT-EXIST")]}, None
    )
    assert result == {"batchItemFailures": []}
    assert events.put_events_calls == []


def test_ddb_error_during_vin_resolution_adds_batch_failure(
    handler_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _AngryDdb:
        def get_item(self, **_: Any) -> dict:
            raise RuntimeError("throttled")

    events = _FakeEvents()
    _wire(monkeypatch, handler_module, _AngryDdb(), events)

    result = handler_module.handler(
        {"Records": [_record_insert("fleet-alpha-001", _VID_MRDN, "seq-boom")]}, None
    )
    assert result == {"batchItemFailures": [{"itemIdentifier": "seq-boom"}]}
    assert events.put_events_calls == [], "should not have attempted PutEvents"


def test_putevents_failed_entry_count_adds_batch_failure(
    handler_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CMS review Cycle 7 lesson: FailedEntryCount>0 with HTTP 200 must retry."""
    ddb = _FakeDdb({_VID_MRDN: _VIN_MRDN})
    events = _FakeEvents(failed_count=1, error_code="ThrottlingException")
    _wire(monkeypatch, handler_module, ddb, events)

    result = handler_module.handler(
        {"Records": [_record_insert("fleet-alpha-001", _VID_MRDN, "seq-thr")]}, None
    )
    assert result == {"batchItemFailures": [{"itemIdentifier": "seq-thr"}]}


def test_putevents_exception_adds_batch_failure(
    handler_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    ddb = _FakeDdb({_VID_MRDN: _VIN_MRDN})
    events = _FakeEvents(raise_exc=RuntimeError)
    _wire(monkeypatch, handler_module, ddb, events)

    result = handler_module.handler(
        {"Records": [_record_insert("fleet-alpha-001", _VID_MRDN, "seq-x")]}, None
    )
    assert result == {"batchItemFailures": [{"itemIdentifier": "seq-x"}]}


# ---------------------------------------------------------------------------
# Tests — configuration (spec D6 gate)
# ---------------------------------------------------------------------------

def test_noop_when_bus_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """No DMS bus → return immediately, no DDB calls, no publishing.

    This is a defense-in-depth path — the CDK gate already prevents the Lambda
    from being created without ``dmsEventBusName``, so in production this branch
    is unreachable. But if someone deploys the Lambda by hand (e.g. debugging),
    the handler must not crash.
    """
    monkeypatch.delenv("DMS_EVENT_BUS_NAME", raising=False)
    monkeypatch.setenv("VEHICLES_TABLE_NAME", "cms-test-storage-vehicles")

    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    if "handler" in sys.modules:
        del sys.modules["handler"]
    module = importlib.import_module("handler")

    # Wire clients that would explode if called.
    def _boom():
        raise AssertionError("client should not be constructed when bus unset")

    monkeypatch.setattr(module, "_get_ddb", _boom)
    monkeypatch.setattr(module, "_get_events", _boom)

    result = module.handler({"Records": [_record_insert("f", "v")]}, None)
    assert result == {"batchItemFailures": []}


# ---------------------------------------------------------------------------
# Tests — privacy (spec Constraints: publisher payload carries no PII)
# ---------------------------------------------------------------------------

def test_full_vin_never_appears_in_logs(
    handler_module, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    ddb = _FakeDdb({_VID_MRDN: _VIN_MRDN})
    events = _FakeEvents()
    _wire(monkeypatch, handler_module, ddb, events)

    with caplog.at_level("INFO"):
        handler_module.handler(
            {"Records": [_record_insert("fleet-alpha-001", _VID_MRDN)]}, None
        )
    for record in caplog.records:
        assert _VIN_MRDN not in record.getMessage(), (
            f"Full VIN leaked into log: {record.getMessage()}"
        )


def test_detail_body_carries_only_the_four_spec_keys(
    handler_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec Constraints: publisher payload carries no PII — VIN and fleet id only."""
    ddb = _FakeDdb({_VID_MRDN: _VIN_MRDN})
    events = _FakeEvents()
    _wire(monkeypatch, handler_module, ddb, events)

    handler_module.handler(
        {"Records": [_record_insert("fleet-alpha-001", _VID_MRDN)]}, None
    )
    detail = json.loads(events.put_events_calls[0]["Entries"][0]["Detail"])
    assert set(detail.keys()) == {"vin", "fleet_id", "action", "occurred_at"}
