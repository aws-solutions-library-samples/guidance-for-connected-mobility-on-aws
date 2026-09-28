"""
Unit tests for dms_alert_publisher.handler.

Run:
    deployment/.venv/bin/python -m pytest services/data_processing/lambda/dms_alert_publisher/test_handler.py -v

Coverage:
  1. INSERT with a dtcCode produces exactly one dtcs entry with code + protocol="j2012"
  2. INSERT without dtcCode omits dtcs entirely (no field, not empty list)
  3. MODIFY records are ignored (no publish)
  4. REMOVE records are ignored (no publish)
  5. Missing VIN (vehicle not found) skips without publishing
  6. last_updated is a valid ISO 8601 UTC string ending in Z
  7. A batch with one bad record still publishes the good ones and reports
     only the bad record in batchItemFailures
  8. Unset DMS_EVENT_BUS_NAME env var — handler returns empty batchItemFailures
     without calling EventBridge
  9. No VIN appears in any log output (log sanitisation test)
 10. INSERT with empty-string dtcCode omits dtcs (empty-string is treated as absent)
 11. VIN resolved via get_item — correct DDB projection key used
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from decimal import Decimal
from unittest.mock import MagicMock, call

import pytest

# ── Path setup: make the Lambda package importable without altering the
#   project-wide sys.path.  Same pattern used by test_simulation_lambda.py.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Set required env vars BEFORE importing the module so module-level globals
# initialise without real AWS calls.
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("VEHICLES_TABLE_NAME", "cms-test-storage-vehicles")
# DMS_EVENT_BUS_NAME is intentionally NOT set globally — individual tests
# set it (or leave it unset) via monkeypatch.

import handler as h  # noqa: E402  (must follow env var setup)


# ── Helpers ───────────────────────────────────────────────────────────────────

_ISO8601_UTC_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]00:00)$"
)


def _ddb_str(value: str) -> dict:
    return {"S": value}


def _ddb_num(value) -> dict:
    return {"N": str(value)}


def _make_alert_image(
    *,
    vehicle_id: str = "VEH-001",
    alert_id: str = "ALT-abc-123",
    alert_type: str = "maintenance.catalyst_efficiency_low",
    category: str = "engine",
    severity: str = "LOW",
    status: str = "OPEN",
    dtc_code: str | None = "P0420",
    trigger_condition: str = "batteryVoltage < 12.4",
    trigger_field: str = "batteryVoltage",
    current_value: str = "12.1",
    threshold_value: str = "12.4",
    estimated_cost: str = "150.00",
    timestamp_ms: int = 1_700_000_000_000,
) -> dict:
    """Build a DDB Stream NewImage dict for an alert record."""
    image = {
        "vehicleId": _ddb_str(vehicle_id),
        "alertId": _ddb_str(alert_id),
        "alertType": _ddb_str(alert_type),
        "category": _ddb_str(category),
        "severity": _ddb_str(severity),
        "status": _ddb_str(status),
        "triggerCondition": _ddb_str(trigger_condition),
        "triggerField": _ddb_str(trigger_field),
        "currentValue": _ddb_str(current_value),
        "thresholdValue": _ddb_str(threshold_value),
        "estimatedCost": _ddb_str(estimated_cost),
        "timestamp": _ddb_num(timestamp_ms),
        "createdDate": _ddb_num(timestamp_ms),
    }
    if dtc_code is not None:
        image["dtcCode"] = _ddb_str(dtc_code)
    return image


def _make_insert_record(
    new_image: dict,
    sequence_number: str = "SEQ-001",
) -> dict:
    return {
        "eventName": "INSERT",
        "dynamodb": {
            "SequenceNumber": sequence_number,
            "NewImage": new_image,
        },
    }


def _make_event(records: list[dict]) -> dict:
    return {"Records": records}


def _mock_vehicle_found(vin: str = "1HGBH41JXMN109186") -> MagicMock:
    """Return a fake DDB client whose get_item responds with a vehicle item."""
    mock = MagicMock()
    mock.get_item.return_value = {
        "Item": {"vin": {"S": vin}},
    }
    return mock


def _mock_vehicle_not_found() -> MagicMock:
    """Return a fake DDB client whose get_item responds with an empty item."""
    mock = MagicMock()
    mock.get_item.return_value = {}
    return mock


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_globals(monkeypatch):
    """Reset module-level client singletons between tests."""
    monkeypatch.setattr(h, "_ddb_client", None)
    monkeypatch.setattr(h, "_events_client", None)
    yield


@pytest.fixture
def fake_ddb(monkeypatch):
    mock = _mock_vehicle_found()
    monkeypatch.setattr(h, "_ddb_client", mock)
    return mock


@pytest.fixture
def fake_events(monkeypatch):
    mock = MagicMock()
    # Model the REAL PutEvents response. A bare MagicMock returns a truthy Mock
    # from `.get("FailedEntryCount", 0)`, which is a value the actual API can never
    # produce — so a mock without this makes every successful publish look failed,
    # and a mock that omitted the field entirely would have hidden the missing
    # FailedEntryCount check that review cycle 7 raised as W1. The fixture asserts
    # the API's shape, not just its callability.
    mock.put_events.return_value = {"FailedEntryCount": 0, "Entries": [{"EventId": "e-1"}]}
    monkeypatch.setattr(h, "_events_client", mock)
    return mock


@pytest.fixture
def fake_events_rejecting(monkeypatch):
    """PutEvents returning HTTP 200 with a per-entry failure.

    EventBridge does this for a nonexistent bus, a throttle, or an oversized
    detail. The handler must treat it as a retryable failure rather than success.
    """
    mock = MagicMock()
    mock.put_events.return_value = {
        "FailedEntryCount": 1,
        "Entries": [{"ErrorCode": "InternalException", "ErrorMessage": "throttled"}],
    }
    monkeypatch.setattr(h, "_events_client", mock)
    return mock


@pytest.fixture
def with_bus(monkeypatch):
    """Set the DMS bus name so publishing is not skipped."""
    monkeypatch.setattr(h, "_DMS_BUS_NAME", "dms-test-events")
    yield "dms-test-events"


# ── Test 1: INSERT with dtcCode → one dtcs entry ──────────────────────────────

class TestInsertWithDtcCode:
    def test_dtcs_entry_has_code_and_j2012_protocol(self, fake_ddb, fake_events, with_bus):
        image = _make_alert_image(dtc_code="P0420")
        result = h.handler(_make_event([_make_insert_record(image)]), None)

        assert result["batchItemFailures"] == []
        fake_events.put_events.assert_called_once()
        entries = fake_events.put_events.call_args.kwargs["Entries"]
        detail = json.loads(entries[0]["Detail"])

        assert "dtcs" in detail
        assert len(detail["dtcs"]) == 1
        assert detail["dtcs"][0]["code"] == "P0420"
        assert detail["dtcs"][0]["protocol"] == "j2012"

    def test_source_is_cms_telematics(self, fake_ddb, fake_events, with_bus):
        image = _make_alert_image()
        h.handler(_make_event([_make_insert_record(image)]), None)

        entries = fake_events.put_events.call_args.kwargs["Entries"]
        assert entries[0]["Source"] == "cms.telematics"

    def test_event_bus_name_used(self, fake_ddb, fake_events, with_bus):
        h.handler(_make_event([_make_insert_record(_make_alert_image())]), None)
        entries = fake_events.put_events.call_args.kwargs["Entries"]
        assert entries[0]["EventBusName"] == "dms-test-events"

    def test_detail_type_is_cms_maintenance_alert(self, fake_ddb, fake_events, with_bus):
        h.handler(_make_event([_make_insert_record(_make_alert_image())]), None)
        entries = fake_events.put_events.call_args.kwargs["Entries"]
        assert entries[0]["DetailType"] == "CMS Maintenance Alert"

    def test_alert_context_fields_in_detail(self, fake_ddb, fake_events, with_bus):
        image = _make_alert_image(
            alert_id="ALT-xyz",
            alert_type="maintenance.oil_pressure_low",
            severity="HIGH",
            category="engine",
            trigger_condition="oilPressure < 15",
        )
        h.handler(_make_event([_make_insert_record(image)]), None)
        entries = fake_events.put_events.call_args.kwargs["Entries"]
        detail = json.loads(entries[0]["Detail"])

        assert detail["alert_id"] == "ALT-xyz"
        assert detail["alert_type"] == "maintenance.oil_pressure_low"
        assert detail["severity"] == "HIGH"
        assert detail["category"] == "engine"
        assert detail["trigger_condition"] == "oilPressure < 15"


# ── Test 2: INSERT without dtcCode → dtcs field absent ───────────────────────

class TestInsertWithoutDtcCode:
    def test_dtcs_field_absent_when_no_dtc_code(self, fake_ddb, fake_events, with_bus):
        image = _make_alert_image(dtc_code=None)
        h.handler(_make_event([_make_insert_record(image)]), None)

        entries = fake_events.put_events.call_args.kwargs["Entries"]
        detail = json.loads(entries[0]["Detail"])
        assert "dtcs" not in detail

    def test_empty_string_dtc_code_also_omits_dtcs(self, fake_ddb, fake_events, with_bus):
        """An empty-string dtcCode is treated as absent — dtcs must not appear."""
        image = _make_alert_image(dtc_code="")
        h.handler(_make_event([_make_insert_record(image)]), None)

        entries = fake_events.put_events.call_args.kwargs["Entries"]
        detail = json.loads(entries[0]["Detail"])
        assert "dtcs" not in detail

    def test_publish_called_once(self, fake_ddb, fake_events, with_bus):
        image = _make_alert_image(dtc_code=None)
        result = h.handler(_make_event([_make_insert_record(image)]), None)

        assert result["batchItemFailures"] == []
        fake_events.put_events.assert_called_once()


# ── Tests 3 & 4: MODIFY and REMOVE are ignored ───────────────────────────────

class TestNonInsertRecordsIgnored:
    def test_modify_not_published(self, fake_ddb, fake_events, with_bus):
        record = {
            "eventName": "MODIFY",
            "dynamodb": {
                "SequenceNumber": "SEQ-MODIFY",
                "NewImage": _make_alert_image(),
            },
        }
        result = h.handler(_make_event([record]), None)

        assert result["batchItemFailures"] == []
        fake_events.put_events.assert_not_called()

    def test_remove_not_published(self, fake_ddb, fake_events, with_bus):
        record = {
            "eventName": "REMOVE",
            "dynamodb": {
                "SequenceNumber": "SEQ-REMOVE",
                "OldImage": _make_alert_image(),
                # REMOVE has no NewImage
            },
        }
        result = h.handler(_make_event([record]), None)

        assert result["batchItemFailures"] == []
        fake_events.put_events.assert_not_called()

    def test_mixed_batch_only_insert_published(self, fake_ddb, fake_events, with_bus):
        """A batch of INSERT + MODIFY + REMOVE → only the INSERT is published."""
        records = [
            _make_insert_record(_make_alert_image(alert_id="INSERT-1"), "SEQ-1"),
            {
                "eventName": "MODIFY",
                "dynamodb": {"SequenceNumber": "SEQ-2", "NewImage": _make_alert_image()},
            },
            {
                "eventName": "REMOVE",
                "dynamodb": {"SequenceNumber": "SEQ-3"},
            },
        ]
        result = h.handler(_make_event(records), None)

        assert result["batchItemFailures"] == []
        # Only 1 publish for the INSERT
        fake_events.put_events.assert_called_once()


# ── Test 5: Missing VIN skips without publishing ──────────────────────────────

class TestMissingVinSkipped:
    def test_vehicle_not_found_skips_without_publish(self, monkeypatch, fake_events, with_bus):
        fake_ddb = _mock_vehicle_not_found()
        monkeypatch.setattr(h, "_ddb_client", fake_ddb)

        image = _make_alert_image(vehicle_id="GHOST-VEHICLE")
        result = h.handler(_make_event([_make_insert_record(image)]), None)

        assert result["batchItemFailures"] == []
        fake_events.put_events.assert_not_called()

    def test_vehicle_found_but_vin_empty_skips(self, monkeypatch, fake_events, with_bus):
        """Item exists but vin attribute is empty string → skip."""
        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {"Item": {"vin": {"S": ""}}}
        monkeypatch.setattr(h, "_ddb_client", fake_ddb)

        image = _make_alert_image()
        result = h.handler(_make_event([_make_insert_record(image)]), None)

        assert result["batchItemFailures"] == []
        fake_events.put_events.assert_not_called()

    def test_missing_vin_permanent_not_a_batch_failure(self, monkeypatch, fake_events, with_bus):
        """A missing VIN (vehicle not found) is a permanent skip, NOT a retry-able failure."""
        fake_ddb = _mock_vehicle_not_found()
        monkeypatch.setattr(h, "_ddb_client", fake_ddb)

        image = _make_alert_image()
        result = h.handler(_make_event([_make_insert_record(image)]), None)

        assert result["batchItemFailures"] == []

    def test_transient_ddb_error_is_batch_failure(self, monkeypatch, fake_events, with_bus):
        """A transient DDB error during VIN lookup IS a batch failure — it should be retried."""
        fake_ddb = MagicMock()
        fake_ddb.get_item.side_effect = RuntimeError("DDB throttle")
        monkeypatch.setattr(h, "_ddb_client", fake_ddb)

        image = _make_alert_image()
        result = h.handler(_make_event([_make_insert_record(image, "SEQ-ERR")]), None)

        assert result["batchItemFailures"] == [{"itemIdentifier": "SEQ-ERR"}]
        fake_events.put_events.assert_not_called()


# ── Test 6: last_updated is ISO 8601 UTC ─────────────────────────────────────

class TestLastUpdatedFormat:
    def test_last_updated_is_iso8601_utc(self, fake_ddb, fake_events, with_bus):
        image = _make_alert_image(timestamp_ms=1_700_000_000_000)
        h.handler(_make_event([_make_insert_record(image)]), None)

        entries = fake_events.put_events.call_args.kwargs["Entries"]
        detail = json.loads(entries[0]["Detail"])
        last_updated = detail["last_updated"]

        assert _ISO8601_UTC_RE.match(last_updated), (
            f"last_updated={last_updated!r} does not match ISO 8601 UTC pattern"
        )

    def test_last_updated_ends_in_z(self, fake_ddb, fake_events, with_bus):
        """Verify the Z-suffix specifically (common source of schema failures)."""
        image = _make_alert_image(timestamp_ms=1_700_000_000_000)
        h.handler(_make_event([_make_insert_record(image)]), None)

        entries = fake_events.put_events.call_args.kwargs["Entries"]
        detail = json.loads(entries[0]["Detail"])
        assert detail["last_updated"].endswith("Z")


# ── Test 7: Batch safety — bad record doesn't lose good ones ─────────────────

class TestBatchSafety:
    def test_good_record_published_despite_bad_record(
        self, monkeypatch, fake_ddb, fake_events, with_bus
    ):
        """One record that raises an unexpected exception must not prevent the
        other records in the batch from being published.

        The 'bad' record here has put_events raise on the first call so the
        error occurs inside the try block and hits batchItemFailures.
        """
        call_count = {"n": 0}

        def put_events_side_effect(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("EventBridge transient error")
            return {}

        fake_events.put_events.side_effect = put_events_side_effect

        bad_record = _make_insert_record(_make_alert_image(alert_id="BAD"), "SEQ-BAD")
        good_record = _make_insert_record(_make_alert_image(alert_id="GOOD"), "SEQ-GOOD")

        result = h.handler(_make_event([bad_record, good_record]), None)

        # Good record was published (second call succeeded)
        assert fake_events.put_events.call_count == 2

        # Bad record's sequence number is in batchItemFailures
        assert result["batchItemFailures"] == [{"itemIdentifier": "SEQ-BAD"}]

    def test_only_failed_sequence_in_batch_failures(
        self, monkeypatch, fake_ddb, fake_events, with_bus
    ):
        """batchItemFailures contains ONLY the failed record, not the good ones."""
        call_count = {"n": 0}

        def put_events_side_effect(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("boom")
            return {}

        fake_events.put_events.side_effect = put_events_side_effect

        records = [
            _make_insert_record(_make_alert_image(), "SEQ-FAIL"),
            _make_insert_record(_make_alert_image(), "SEQ-PASS-1"),
            _make_insert_record(_make_alert_image(), "SEQ-PASS-2"),
        ]
        result = h.handler(_make_event(records), None)

        failures = [f["itemIdentifier"] for f in result["batchItemFailures"]]
        assert "SEQ-FAIL" in failures
        assert "SEQ-PASS-1" not in failures
        assert "SEQ-PASS-2" not in failures
        # 2 successful publishes
        assert fake_events.put_events.call_count == 3

    def test_transient_vin_error_is_retried(self, monkeypatch, fake_events, with_bus):
        """A transient DDB error during VIN lookup causes the record to be
        included in batchItemFailures for retry."""
        fake_ddb = MagicMock()
        fake_ddb.get_item.side_effect = RuntimeError("throttle")
        monkeypatch.setattr(h, "_ddb_client", fake_ddb)

        result = h.handler(_make_event([_make_insert_record(_make_alert_image(), "SEQ-ERR")]), None)
        assert result["batchItemFailures"] == [{"itemIdentifier": "SEQ-ERR"}]
        fake_events.put_events.assert_not_called()


# ── Test 8: Unset DMS_EVENT_BUS_NAME → no-op ─────────────────────────────────

class TestNoOpWhenBusUnset:
    def test_no_publish_when_bus_name_unset(self, monkeypatch, fake_ddb, fake_events):
        """When DMS_EVENT_BUS_NAME is empty/unset, handler returns immediately."""
        monkeypatch.setattr(h, "_DMS_BUS_NAME", "")

        image = _make_alert_image()
        result = h.handler(_make_event([_make_insert_record(image)]), None)

        assert result["batchItemFailures"] == []
        fake_events.put_events.assert_not_called()
        # Vehicle lookup should also not happen — we exited before processing
        fake_ddb.get_item.assert_not_called()

    def test_no_op_returns_empty_failures(self, monkeypatch, fake_ddb, fake_events):
        monkeypatch.setattr(h, "_DMS_BUS_NAME", "")
        result = h.handler(_make_event([_make_insert_record(_make_alert_image())]), None)
        assert result == {"batchItemFailures": []}


# ── Test 9: No VIN in log output ─────────────────────────────────────────────

class TestNoVinInLogs:
    """Ensure the full VIN never appears in any log message."""

    VIN = "1HGBH41JXMN109186"  # 17-char test VIN

    def _collect_log_messages(self, monkeypatch, fake_ddb, fake_events, with_bus, image):
        """Run the handler and return all captured log record messages."""
        log_messages: list[str] = []

        class _Capture(logging.Handler):
            def emit(self, record):
                log_messages.append(self.format(record))

        capture_handler = _Capture()
        capture_handler.setLevel(logging.DEBUG)
        h.logger.addHandler(capture_handler)
        try:
            h.handler(_make_event([_make_insert_record(image)]), None)
        finally:
            h.logger.removeHandler(capture_handler)
        return log_messages

    def test_full_vin_not_in_info_log(self, monkeypatch, fake_events, with_bus):
        fake_ddb = _mock_vehicle_found(vin=self.VIN)
        monkeypatch.setattr(h, "_ddb_client", fake_ddb)

        image = _make_alert_image(dtc_code="P0420")
        log_messages = self._collect_log_messages(monkeypatch, fake_ddb, fake_events, with_bus, image)

        for msg in log_messages:
            assert self.VIN not in msg, (
                f"Full VIN {self.VIN!r} found in log: {msg!r}"
            )

    def test_redacted_vin_present_in_logs(self, monkeypatch, fake_events, with_bus):
        """The last-6 suffix IS expected in logs for correlation."""
        fake_ddb = _mock_vehicle_found(vin=self.VIN)
        monkeypatch.setattr(h, "_ddb_client", fake_ddb)

        image = _make_alert_image(dtc_code="P0420")
        log_messages = self._collect_log_messages(monkeypatch, fake_ddb, fake_events, with_bus, image)

        # At least one log line should contain the redacted suffix
        redacted = h._redact_vin(self.VIN)
        assert any(redacted in msg for msg in log_messages), (
            f"Expected redacted VIN {redacted!r} in logs but found none.  "
            f"Messages: {log_messages}"
        )


# ── Test 11: VIN lookup uses correct projection key ──────────────────────────

class TestVinLookupProjection:
    def test_get_item_called_with_vehicle_id_key(self, fake_ddb, fake_events, with_bus):
        image = _make_alert_image(vehicle_id="VEH-999")
        h.handler(_make_event([_make_insert_record(image)]), None)

        fake_ddb.get_item.assert_called_once()
        call_kwargs = fake_ddb.get_item.call_args.kwargs
        assert call_kwargs["Key"] == {"vehicleId": {"S": "VEH-999"}}
        assert call_kwargs["TableName"] == h._VEHICLES_TABLE

    def test_get_item_projects_vin_only(self, fake_ddb, fake_events, with_bus):
        """We must project only 'vin' to avoid fetching unnecessary attributes."""
        image = _make_alert_image()
        h.handler(_make_event([_make_insert_record(image)]), None)

        call_kwargs = fake_ddb.get_item.call_args.kwargs
        assert call_kwargs.get("ProjectionExpression") == "vin"


class TestPutEventsPartialFailure:
    """W1 (review cycle 7): PutEvents can return HTTP 200 with FailedEntryCount > 0.

    Treating any non-exception return as success would log "Published" while DMS
    received nothing — the same looks-fine-does-nothing shape as the env-var
    mismatch this handler shipped once (C1, same cycle).
    """

    def test_failed_entry_count_is_reported_as_a_batch_failure(
        self, fake_ddb, fake_events_rejecting, with_bus
    ):
        image = _make_alert_image(dtc_code="P0420")
        result = h.handler(_make_event([_make_insert_record(image)]), None)
        assert result["batchItemFailures"], (
            "PutEvents reported FailedEntryCount=1 but the handler returned no "
            "batch failure, so the alert is silently dropped and never retried."
        )

    def test_successful_publish_reports_no_batch_failure(
        self, fake_ddb, fake_events, with_bus
    ):
        """Positive control — without it the assertion above passes if ALL publishes fail."""
        image = _make_alert_image(dtc_code="P0420")
        result = h.handler(_make_event([_make_insert_record(image)]), None)
        assert result["batchItemFailures"] == []
