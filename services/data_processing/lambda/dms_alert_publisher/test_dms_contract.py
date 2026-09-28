"""Cross-repo contract test: the DMS alert publisher's payload must satisfy DMS's schema.

T13.5 option c1 works *because* DMS's `telematics_event_v1.json` already accepts an
alert-shaped event — `additionalProperties: true`, only `vin` and `last_updated`
required, and an omitted `dtcs` explicitly documented as valid. That is the entire
reason c1 needed no DMS-side change.

That agreement is invisible from either repo alone. CMS's handler tests assert the
payload's shape against CMS's own expectations; DMS's tests validate inbound events
against its schema using DMS's own fixtures. Neither can notice if the producer and
the schema drift apart — which is the same blind spot that let a Lambda go undeployed
for three months and a spec's Accept criterion go half-implemented, both in this same
initiative.

So this test builds the real payload with the real producer and validates it against
the real schema in the sibling repo.

It SKIPS when the DMS repo or `jsonschema` is unavailable, deliberately: a developer
with only CMS checked out should not see a red test for a repo they do not have. The
skip is loud enough to notice and the CI that runs both repos is where it bites.

Set `DMS_REPO_PATH` to override sibling-repo discovery.

Run with:
  deployment/.venv/bin/python -m pytest \
    services/data_processing/lambda/dms_alert_publisher/test_dms_contract.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib

import pytest

_HERE = pathlib.Path(__file__).resolve()
_CMS_ROOT = _HERE.parents[4]

_SCHEMA_REL = pathlib.Path("source/schemas/telematics_event_v1.json")


def _dms_schema_path() -> pathlib.Path | None:
    override = os.environ.get("DMS_REPO_PATH")
    candidates = [pathlib.Path(override)] if override else []
    candidates.append(_CMS_ROOT.parent / "guidance-for-dealer-management-system-on-aws")
    for root in candidates:
        candidate = root / _SCHEMA_REL
        if candidate.is_file():
            return candidate
    return None


def _load_handler():
    spec = importlib.util.spec_from_file_location(
        "dms_alert_publisher_handler", _HERE.parent / "handler.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BASE_ALERT = {
    "alertId": "alert-0001",
    "alertType": "maintenance.catalyst_efficiency_low",
    "category": "CORRECTIVE",
    "severity": "HIGH",
    "status": "OPEN",
    "triggerCondition": "catalystEfficiency < 0.75",
    "triggerField": "catalystEfficiency",
    "currentValue": 0.71,
    "thresholdValue": 0.75,
    "estimatedCost": 1200,
    "timestamp": 1781543707254,
    "createdDate": 1781798262442,
    "vehicleId": "VEH-CONTRACT-1",
}

# A synthetic 17-character VIN. Not a real vehicle.
_VIN = "1XXXX11XX1XX11111"


@pytest.fixture(scope="module")
def schema() -> dict:
    path = _dms_schema_path()
    if path is None:
        pytest.skip(
            "DMS repo not found alongside CMS; set DMS_REPO_PATH to run the "
            "cross-repo contract test"
        )
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def handler_module():
    os.environ.setdefault("DMS_EVENT_BUS_NAME", "dms-contract-test-events")
    os.environ.setdefault("MAINTENANCE_ALERTS_TABLE", "contract-alerts")
    os.environ.setdefault("VEHICLES_TABLE", "contract-vehicles")
    return _load_handler()


def _validate(detail: dict, schema: dict) -> None:
    jsonschema = pytest.importorskip(
        "jsonschema", reason="jsonschema not installed in this environment"
    )
    jsonschema.validate(detail, schema)


@pytest.mark.parametrize(
    "dtc_code,expect_dtcs",
    [
        ("P0420", True),   # ~67% of live alerts carry a code
        ("P0300", True),
        ("C1234", True),   # chassis prefix — still J2012
        ("B1004", True),   # body prefix
        ("", False),       # threshold-only alert, e.g. system_voltage_low_minor
        (None, False),     # attribute absent entirely
    ],
)
def test_payload_validates_against_dms_schema(
    handler_module, schema, dtc_code, expect_dtcs
) -> None:
    alert = dict(_BASE_ALERT)
    if dtc_code is None:
        alert.pop("dtcCode", None)
    else:
        alert["dtcCode"] = dtc_code

    detail = handler_module._build_detail(alert, _VIN)

    assert ("dtcs" in detail) is expect_dtcs, (
        f"dtcCode={dtc_code!r} should {'produce' if expect_dtcs else 'omit'} dtcs. "
        "An empty dtcs entry would fail the schema's per-item required [code, "
        "protocol]; an empty ARRAY would read as 'no active faults', which is a "
        "different claim than 'this alert has no DTC'."
    )
    _validate(detail, schema)


def test_required_fields_are_present(handler_module, schema) -> None:
    """`vin` and `last_updated` are the schema's only required fields."""
    detail = handler_module._build_detail(dict(_BASE_ALERT, dtcCode="P0420"), _VIN)
    for field in schema["required"]:
        assert field in detail, f"schema requires {field!r}, producer omits it"


def test_every_dtc_entry_carries_a_protocol(handler_module, schema) -> None:
    """The schema requires `protocol` per entry; assert the producer always sets it.

    Checked separately from the validation above because a future change could add a
    second DTC source and forget the tag, and `additionalProperties: true` means the
    schema would only catch it via the per-item `required` list.
    """
    detail = handler_module._build_detail(dict(_BASE_ALERT, dtcCode="P0420"), _VIN)
    assert detail["dtcs"], "expected at least one dtc entry"
    for entry in detail["dtcs"]:
        assert entry.get("code"), f"dtc entry missing code: {entry}"
        assert entry.get("protocol") in {"j2012", "j1939"}, (
            f"dtc entry protocol must be j2012 or j1939, got {entry.get('protocol')!r}"
        )


def test_producer_does_not_emit_an_empty_dtcs_array(handler_module) -> None:
    """Absence and emptiness are different claims; the producer must not conflate them.

    An empty `dtcs: []` is valid per the schema and means "no active faults". A
    threshold alert with no DTC is not the same statement, so the field is omitted.
    """
    detail = handler_module._build_detail(dict(_BASE_ALERT, dtcCode=""), _VIN)
    assert detail.get("dtcs") != [], (
        "producer emitted `dtcs: []`, which asserts 'no active faults' rather than "
        "'this alert carries no DTC'. Omit the field instead."
    )
