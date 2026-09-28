# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
RED-PHASE tests: Vehicle model-assignment backfill guards — DX19, DX23, DX24.

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform
  DX19 — a vehicle with no modelManifestName, or one resolving to a model with
          an empty ecus array, renders a distinct named state ("model not assigned")
          rather than empty success or "no data". Guards F5 (0 of 69 staging
          vehicles resolve to a model with ECU data). The product must surface the
          gap, not hide it.
  DX23 — an offboard vehicle is NEVER assigned a model manifest; it renders as
          "not diagnosable — cloud-fed vehicle". An unknown-classification vehicle
          fails CLOSED to "classification not determined", never to onboard. Guards
          C12 (classification order-of-operations) and the Fake-connected regression
          pattern: unknown ≠ onboard, full stop.
  DX24 — the model backfill script REFUSES to run on a fleet where the
          'classification' field is absent, rather than silently treating absent
          as onboard. C12 is explicit: 'unknown classification is NOT treated as
          onboard — it fails closed'. Running assignment before classification
          produces offboard vehicles with model manifests, which asserts
          diagnosability that the cloud path cannot deliver.

WHY RED:
  These tests import:
    - `backfill_vehicle_models` (produced in T5.4, does not exist yet)
  The module-absent guard matches the diagnostics-copy-lint.test.ts pattern:
  one explicit "module not found" assertion, remaining tests skip cleanly.

TURNS GREEN:
  DX19 — T7.1 (Stage 2 surface renders named states per the § Design chain)
          AND the supporting backfill logic in T5.4
  DX23 — T4.1 (classification backfill) + T5.4 (model assignment, onboard only)
  DX24 — T5.4 (backfill_vehicle_models refuses to run on absent classification)

NOTE on PytestUnknownMarkWarning:
  @pytest.mark.integration marks tests requiring live DynamoDB. The mark is
  registered in conftest.py (Fix 1 / 2026-09-03 corrections).

C8 COMPLIANCE:
  No VINs, brand names, or account IDs in fixtures. Synthetic IDs only.
"""

from __future__ import annotations

import importlib
import os
import sys
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# sys.path
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# Mirror of main_api._classify_vehicle for fixture derivation.
# Source: deployment/scripts/backfill_vehicle_classification._classify_vehicle_local
# Imported rather than copied so the two derivation paths cannot drift independently.
# decisions.md 2026-09-03: 'classification' is never stored; it is always derived
# from 'dataSource' (and 'oem_source' as a legacy fallback) at read time.
try:
    from backfill_vehicle_classification import _classify_vehicle_local as _derive_classification
except ImportError:  # pragma: no cover — only absent if backfill_vehicle_classification.py is missing
    _derive_classification = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Red-phase import guard
# ---------------------------------------------------------------------------
_MODULE_NAME = "backfill_vehicle_models"


def _load_module(name: str) -> Optional[Any]:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        return None


_bvm = _load_module(_MODULE_NAME)
_MODULE_PRESENT = _bvm is not None

# ---------------------------------------------------------------------------
# Synthetic fixtures — no real brand names, no real IDs (C8)
# ---------------------------------------------------------------------------
_STAGE = "staging"

_VEH_NO_MODEL = "VEH-TEST-NOMODEL-001"
_VEH_EMPTY_ECUS = "VEH-TEST-EMPTYECUS-001"
_VEH_OFFBOARD = "VEH-TEST-OFFBOARD-001"
_VEH_UNKNOWN_CLASS = "VEH-TEST-UNKNOWN-001"
_VEH_ONBOARD_OK = "VEH-TEST-ONBOARD-001"


def _vehicle_item(
    vehicle_id: str,
    data_source: Optional[str] = None,
    model_manifest_name: Optional[str] = None,
    make: Optional[str] = "TestMake",
    oem_source: Optional[str] = None,
) -> dict:
    """Minimal DDB item for a vehicle row (AttributeValue-wrapped).

    Per decisions.md 2026-09-03: 'classification' is NEVER a stored attribute.
    Classification is derived at read time from 'dataSource' (primary) or
    'oem_source' (legacy fallback) by _classify_vehicle_local.  Do NOT add a
    'classification' key here — that would create a second source of truth for
    a fact 'dataSource' already carries, and is explicitly prohibited.
    """
    item: dict = {"vehicleId": {"S": vehicle_id}}
    if model_manifest_name is not None:
        item["modelManifestName"] = {"S": model_manifest_name}
    if make is not None:
        item["make"] = {"S": make}
    if data_source is not None:
        item["dataSource"] = {"S": data_source}
    if oem_source is not None:
        item["oem_source"] = {"S": oem_source}
    return item


def _scan_response(items: list) -> dict:
    return {
        "Items": items,
        "Count": len(items),
        "ScannedCount": len(items),
        "ResponseMetadata": {"RequestId": "test", "HTTPStatusCode": 200, "HTTPHeaders": {}},
    }


# ===========================================================================
# DX19 — "model not assigned" is a named state, never empty success
# ===========================================================================

def test_dx19_module_exists():
    """DX19/DX23/DX24: The backfill_vehicle_models module exists.

    RED PHASE: T5.4 has not run; the module does not exist.
    GREEN PHASE: T5.4 creates backfill_vehicle_models.py.
    """
    assert _MODULE_PRESENT, (
        f"Module '{_MODULE_NAME}' not found at {_SCRIPTS_DIR!r}. "
        "Create it in T5.4 (model assignment + Meridian conversion, onboard only). "
        "This module is the target for DX19, DX23, and DX24 assertions."
    )


def test_dx19_vehicle_without_model_manifest_returns_named_reason():
    """DX19: A vehicle with no modelManifestName resolves to a distinct named
    reason, not None, not empty string, not a generic "no data".

    Spec: 'vehicle.modelManifestName → absent? "model not assigned"'
    Guards F5: 0 of 69 staging vehicles resolve to a model with ECU data.
    The gap must be visible, not silent.

    Expected: the backfill module exposes a `resolve_diagnosis_state(vehicle_row)`
    function that returns a dict with 'state' = 'model_not_assigned' or a string
    equal to 'model not assigned' (case-insensitive match accepted) when
    modelManifestName is absent.

    RED PHASE: module does not exist yet (T5.4).
    GREEN PHASE: T5.4 + T7.1.
    """
    if not _MODULE_PRESENT:
        return

    vehicle_row = _vehicle_item(_VEH_NO_MODEL, data_source="vehicle-telemetry")
    result = _bvm.resolve_diagnosis_state(vehicle_row)  # type: ignore[union-attr]

    # The result must NOT be a success state or empty.
    assert result is not None, (
        "DX19 FAILED: resolve_diagnosis_state returned None for a vehicle with no "
        "modelManifestName. It must return a named reason."
    )

    # Accept either a dict with 'state' key or a plain string.
    state_value = (
        result.get("state", "") if isinstance(result, dict) else str(result)
    )
    assert state_value.lower() not in ("", "healthy", "ok", "success", "no_data", "no data"), (
        f"DX19 FAILED: resolve_diagnosis_state returned a non-descriptive state "
        f"{state_value!r} for a vehicle with no modelManifestName. "
        "Must return a named reason such as 'model_not_assigned', not a generic success."
    )
    assert "model" in state_value.lower() or "not_assigned" in state_value.lower() or "not assigned" in state_value.lower(), (
        f"DX19 FAILED: state {state_value!r} does not reference 'model' or 'assigned'. "
        "The operator must understand WHY there is no diagnostic data — 'model not assigned' "
        "is distinct from 'vehicle offline' or 'ECU not found'."
    )


def test_dx19_vehicle_with_empty_ecus_returns_named_reason():
    """DX19: A vehicle whose model manifest has an empty ecus array must also
    return a distinct named state — not empty success.

    Spec: 'model manifest record → ecus[] empty? "model has no ECU set"'
    Guards F5 second case: CMS-Fleet-Default has 18 referents but an empty ecus[].
    Per-model feature on that model correctly returns nothing — but it must say so
    rather than rendering a blank panel.

    RED PHASE: module does not exist yet (T5.4).
    GREEN PHASE: T5.4 + T7.1.
    """
    if not _MODULE_PRESENT:
        return

    # Provide a manifest inline — the model exists but has no ECUs.
    vehicle_row = _vehicle_item(
        _VEH_EMPTY_ECUS,
        data_source="vehicle-telemetry",
        model_manifest_name="TEST-MODEL-EMPTY-ECUS",
    )
    empty_manifest = {"modelManifestName": "TEST-MODEL-EMPTY-ECUS", "ecus": []}

    result = _bvm.resolve_diagnosis_state(vehicle_row, manifest=empty_manifest)  # type: ignore[union-attr]

    assert result is not None, (
        "DX19 FAILED: resolve_diagnosis_state returned None for a vehicle with empty ecus[]."
    )
    state_value = (
        result.get("state", "") if isinstance(result, dict) else str(result)
    )
    assert state_value.lower() not in ("", "healthy", "ok", "success"), (
        f"DX19 FAILED: empty-ecus vehicle returned non-descriptive state {state_value!r}. "
        "Must return a named reason such as 'model_has_no_ecu_set'."
    )


# ===========================================================================
# DX23 — offboard never assigned; unknown fails closed
# ===========================================================================

def test_dx23_offboard_vehicle_never_assigned_model():
    """DX23: An offboard vehicle must not be assigned a model manifest.

    Spec: 'An offboard vehicle is cloud-fed, has no FWE agent and therefore no ECU
    sidecar to query, so a model manifest on it would assert diagnosability that
    does not exist.' (C14, C12)

    The backfill script must skip the vehicle and NOT call DDB update_item for it.
    This is a direct parallel to the Fake-connected regression: assigning a model
    to an offboard vehicle would assert diagnostic capability the platform cannot
    deliver.

    RED PHASE: backfill_vehicle_models module does not exist (T5.4).
    GREEN PHASE: T5.4 (model assignment, onboard only).
    """
    if not _MODULE_PRESENT:
        return

    offboard_row = _vehicle_item(
        _VEH_OFFBOARD,
        make="TestMake",
        data_source="cloud-telemetry",
        # Classification is derived: _classify_vehicle_local(offboard_row) → "offboard"
        # because "cloud-telemetry" is in _CLOUD_TELEMETRY_VALUES.  Never stored.
    )

    mock_ddb = MagicMock()
    mock_ddb.scan.return_value = _scan_response([offboard_row])
    mock_ddb.update_item.return_value = {}
    session_mock = MagicMock()
    session_mock.client.return_value = mock_ddb

    with patch("boto3.Session", return_value=session_mock):
        with patch.dict(os.environ, {
            "VEHICLES_TABLE_NAME": f"cms-{_STAGE}-storage-vehicles",
            "MODEL_MANIFEST_TABLE_NAME": f"cms-{_STAGE}-model-manifest",
        }):
            args = MagicMock()
            args.stage = _STAGE
            args.apply = True
            args.confirm_prod = False

            rc = _bvm.run(args)  # type: ignore[union-attr]

    assert rc == 0, f"Expected exit 0, got {rc}"

    assert not mock_ddb.update_item.called, (
        "DX23 FAILED: update_item was called for an offboard vehicle. "
        "Offboard vehicles are cloud-fed and have no ECU sidecar — assigning a model "
        "manifest to them asserts diagnosability that does not exist. "
        "This is the Fake-connected regression pattern applied to model assignment."
    )


def test_dx23_resolve_offboard_returns_cloud_fed_reason():
    """DX23: resolve_diagnosis_state on an offboard vehicle returns the
    'not diagnosable — cloud-fed vehicle' named state, not empty/success.

    This is the UI-side assertion: the Diagnostics tab must explain WHY the vehicle
    is not diagnosable, not show a blank panel or a generic 'no data'.

    RED PHASE: module does not exist (T5.4/T7.1).
    GREEN PHASE: T7.1 (Stage 2 surface renders all 8 named states).
    """
    if not _MODULE_PRESENT:
        return

    offboard_row = _vehicle_item(
        _VEH_OFFBOARD,
        data_source="cloud-telemetry",
        # Classification is derived: _classify_vehicle_local(offboard_row) → "offboard".
        # Never stored.
    )

    result = _bvm.resolve_diagnosis_state(offboard_row)  # type: ignore[union-attr]

    assert result is not None, "DX23 FAILED: resolve_diagnosis_state returned None for offboard vehicle"
    state_value = (
        result.get("state", "") if isinstance(result, dict) else str(result)
    )
    # Must reference 'offboard', 'cloud', or 'not diagnosable'
    assert any(
        kw in state_value.lower()
        for kw in ("offboard", "cloud", "not_diagnosable", "not diagnosable")
    ), (
        f"DX23 FAILED: offboard vehicle returned state {state_value!r}. "
        "Must return a named reason referencing 'offboard' or 'cloud-fed' per spec "
        "§ Design (DX23 row): 'not diagnosable — cloud-fed vehicle'."
    )


def test_dx23_unknown_classification_fails_closed_not_onboard():
    """DX23: A vehicle whose classification cannot be determined fails closed —
    it is NEVER treated as onboard.

    Spec C12: 'unknown classification is NOT treated as onboard: it fails closed
    to "not assigned" and is reported, because guessing here produces exactly the
    fake-capability defect.' Specifically — resolving unknown as onboard and
    assigning a model would assert diagnosability we cannot verify.

    Per decisions.md 2026-09-03: 'classification' is never stored.  The
    "unknown" / unclassifiable case is the path where _classify_vehicle_local
    RAISES ValueError — absent/unrecognised dataSource with no oem_source fallback.
    C12's 'unknown fails closed' maps onto this raises path, not a literal
    'classification: unknown' attribute.

    RED PHASE: module does not exist (T5.4).
    GREEN PHASE: T5.4.
    """
    if not _MODULE_PRESENT:
        return

    # Unclassifiable row: no dataSource, no oem_source → _classify_vehicle_local raises.
    # This is the reachable "unknown" state per decisions.md 2026-09-03.
    unknown_row = _vehicle_item(
        _VEH_UNKNOWN_CLASS,
        # No data_source, no oem_source — _classify_vehicle_local cannot classify this row.
    )

    # Verify assignment skips the vehicle
    mock_ddb = MagicMock()
    mock_ddb.scan.return_value = _scan_response([unknown_row])
    mock_ddb.update_item.return_value = {}
    session_mock = MagicMock()
    session_mock.client.return_value = mock_ddb

    with patch("boto3.Session", return_value=session_mock):
        with patch.dict(os.environ, {
            "VEHICLES_TABLE_NAME": f"cms-{_STAGE}-storage-vehicles",
            "MODEL_MANIFEST_TABLE_NAME": f"cms-{_STAGE}-model-manifest",
        }):
            args = MagicMock()
            args.stage = _STAGE
            args.apply = True
            args.confirm_prod = False

            rc = _bvm.run(args)  # type: ignore[union-attr]

    assert rc in (0, 2), f"Expected exit 0 or 2, got {rc}"
    # F19 (2026-09-03): DX23 originally asserted `rc == 0`, contradicting DX24's
    # `rc != 0` on an identical unclassifiable-row fixture. Weakened to (0, 2) so
    # DX24's pre-flight-abort implementation satisfies both. The invariant DX23
    # actually guards — `not mock_ddb.update_item.called` — is unchanged.
    # Docstring says `rc == 0`; that doc-drift is intentional per decisions.md F19.

    assert not mock_ddb.update_item.called, (
        "DX23 FAILED: update_item was called for an unclassifiable vehicle. "
        "A vehicle whose classification cannot be determined (absent/unrecognised "
        "dataSource, no oem_source) must not be treated as onboard — it fails closed. "
        "Assigning a model to a vehicle whose diagnosability is unverified is the "
        "Fake-connected regression pattern."
    )


def test_dx23_resolve_unknown_returns_classification_not_determined():
    """DX23: resolve_diagnosis_state on an unclassifiable vehicle returns
    'classification not determined', not a success state.

    Complements test_dx23_unknown_classification_fails_closed: the UI side must
    surface the gap explicitly, not silently omit the vehicle.

    Per decisions.md 2026-09-03: the "unknown" case is the raises path of
    _classify_vehicle_local (absent/unrecognised dataSource, no oem_source).

    RED PHASE: module does not exist (T5.4/T7.1).
    GREEN PHASE: T7.1.
    """
    if not _MODULE_PRESENT:
        return

    # Unclassifiable: no dataSource, no oem_source — raises path.
    unknown_row = _vehicle_item(_VEH_UNKNOWN_CLASS)
    result = _bvm.resolve_diagnosis_state(unknown_row)  # type: ignore[union-attr]

    assert result is not None, "DX23 FAILED: resolve_diagnosis_state returned None for unclassifiable vehicle"
    state_value = (
        result.get("state", "") if isinstance(result, dict) else str(result)
    )
    assert any(
        kw in state_value.lower()
        for kw in ("unknown", "classification", "not_determined", "not determined", "unclassifiable")
    ), (
        f"DX23 FAILED: unclassifiable vehicle returned state {state_value!r}. "
        "Must return 'classification not determined' per spec § Design DX23 row."
    )


# ===========================================================================
# DX24 — model backfill refuses to run when classification is absent
# ===========================================================================

def test_dx24_backfill_refuses_to_run_when_classification_absent():
    """DX24: The model backfill script REFUSES to run when the fleet contains
    vehicles whose classification cannot be derived.

    Per decisions.md 2026-09-03: 'classification' is never stored; it is derived
    from 'dataSource' (and 'oem_source' as legacy fallback).  "Absent classification"
    is the state where _classify_vehicle_local would RAISE — i.e. a vehicle with
    no recognised dataSource and no oem_source.

    Spec C12: 'Classification precedes model assignment; the order is strict.'
    Spec: 'An offboard vehicle is … correctly offboard with no model manifest …
    Running the model backfill first would assign models to offboard vehicles
    and then require a second corrective pass.'

    If even one vehicle is unclassifiable, the backfill must exit non-zero with a
    message that classification must run first — never silently assume the vehicle
    is onboard.

    Why this matters (the Fake-connected regression lesson):
      The 'Fake-connected regression' (2026-07-31) arose because a seed script
      wrote connectionStatus='connected' without verifying the field was
      appropriate. This is the same class of defect: a backfill assuming absent
      means onboard would assert diagnosability on cloud-fed vehicles that have
      no ECU sidecar. The spec is explicit: 'unknown classification is the
      fake-capability defect this repo booked in Fake-connected regression.'

    RED PHASE: backfill_vehicle_models module does not exist (T5.4).
    GREEN PHASE: T5.4 adds a preflight check that aborts if any vehicle is
    unclassifiable (raises from _classify_vehicle_local derivation).
    """
    if not _MODULE_PRESENT:
        return

    # Unclassifiable row: no dataSource, no oem_source.
    # _classify_vehicle_local would raise ValueError on this row — the
    # backfill must detect this before writing any model assignments.
    unclassified_row = _vehicle_item(_VEH_NO_MODEL, make="TestMake")
    # Confirm no dataSource or oem_source is present (invariant: _vehicle_item
    # never stores 'classification', so this row has neither the derived key
    # nor either input field).
    assert "dataSource" not in unclassified_row, (
        "Test setup error: unclassified_row must have no dataSource"
    )

    mock_ddb = MagicMock()
    mock_ddb.scan.return_value = _scan_response([unclassified_row])
    mock_ddb.update_item.return_value = {}
    session_mock = MagicMock()
    session_mock.client.return_value = mock_ddb

    import io
    import contextlib

    output = io.StringIO()
    rc = None

    with patch("boto3.Session", return_value=session_mock):
        with patch.dict(os.environ, {
            "VEHICLES_TABLE_NAME": f"cms-{_STAGE}-storage-vehicles",
            "MODEL_MANIFEST_TABLE_NAME": f"cms-{_STAGE}-model-manifest",
        }):
            args = MagicMock()
            args.stage = _STAGE
            args.apply = True
            args.confirm_prod = False

            with contextlib.redirect_stdout(output):
                rc = _bvm.run(args)  # type: ignore[union-attr]

    # The backfill must exit non-zero when a vehicle is unclassifiable
    assert rc != 0, (
        "DX24 FAILED: backfill_vehicle_models exited 0 even though one vehicle "
        "has no dataSource (classification cannot be derived). "
        "It must exit non-zero and refuse to proceed when any vehicle is unclassifiable. "
        "Running model assignment before classification backfill produces offboard "
        "vehicles with model manifests, asserting diagnosability that does not exist."
    )

    # update_item must NOT have been called — the script must abort, not partially write
    assert not mock_ddb.update_item.called, (
        "DX24 FAILED: update_item was called despite an unclassifiable vehicle. "
        "The script must abort before writing any model assignments."
    )

    # The output must explain the reason
    out = output.getvalue()
    assert any(
        kw in out.lower()
        for kw in ("classification", "backfill", "absent", "required", "precondition", "unclassifiable")
    ), (
        f"DX24 FAILED: output does not explain why the script refused. "
        "The operator must see a message referencing 'classification' or 'backfill'. "
        f"Actual output:\n{out}"
    )


def test_dx24_backfill_proceeds_when_all_vehicles_are_classified():
    """DX24 positive control: the model backfill proceeds normally when all vehicles
    can be classified via the derived 'dataSource' field.

    Ensures DX24's guard is a precondition check, not a blanket refusal. The backfill
    must succeed when every vehicle has a recognised dataSource.

    Per decisions.md 2026-09-03: 'classification' is never stored.  A vehicle is
    "classified" when _classify_vehicle_local can derive its class from dataSource
    (or oem_source).  'vehicle-telemetry' → onboard — the case this test exercises.

    RED PHASE: module does not exist (T5.4).
    GREEN PHASE: T5.4.
    """
    if not _MODULE_PRESENT:
        return

    onboard_row = _vehicle_item(
        _VEH_ONBOARD_OK,
        make="TestMake",
        data_source="vehicle-telemetry",
        # Classification derived: _classify_vehicle_local(onboard_row) → "onboard".
        # Never stored.
    )

    mock_ddb = MagicMock()
    mock_ddb.scan.return_value = _scan_response([onboard_row])
    # Simulate update_item succeeding
    mock_ddb.update_item.return_value = {}
    session_mock = MagicMock()
    session_mock.client.return_value = mock_ddb

    with patch("boto3.Session", return_value=session_mock):
        with patch.dict(os.environ, {
            "VEHICLES_TABLE_NAME": f"cms-{_STAGE}-storage-vehicles",
            "MODEL_MANIFEST_TABLE_NAME": f"cms-{_STAGE}-model-manifest",
        }):
            args = MagicMock()
            args.stage = _STAGE
            args.apply = True
            args.confirm_prod = False

            rc = _bvm.run(args)  # type: ignore[union-attr]

    assert rc == 0, (
        f"DX24 FAILED (positive control): backfill_vehicle_models exited {rc} when "
        "all vehicles are classified. The preflight check should pass and the "
        "script should proceed normally."
    )




# ---------------------------------------------------------------------------
# F20 regression guards (2026-09-03)
#
# Two defects surfaced by the live-apply of T5.4 — reasoning in decisions.md
# F20. Both were invisible to every existing test but visible to an independent
# post-apply scan. These are the assertions that would have flipped red before
# the write.
# ---------------------------------------------------------------------------


def test_f20_meridian_windrose_stays_windrose_not_trailwind():
    """assign_model_line preserves an existing Meridian model line.

    Pre-F20, `assign_model_line({make: 'Meridian', model: 'Windrose', ...})`
    returned `MERIDIAN-TRAILWIND` because the body+powertrain map collapsed
    every `(suv, EV)` onto Trailwind. That is F7 in miniature at the
    within-powertrain layer — model-year sets, iOS assets, and eventually
    `didProfileRef` diverge per line even when the ECU set does not.
    """
    if not _MODULE_PRESENT:
        return
    result = _bvm.assign_model_line({  # type: ignore[union-attr]
        "vehicleId": "VEH-TEST-MRDN-WINDROSE",
        "make": "Meridian",
        "model": "Windrose",
        "fuelType": "electric",
        "vehicleType": "SUV",
    })
    assert result == "MERIDIAN-WINDROSE", (
        f"F20a FAILED: existing Meridian Windrose SUV collapsed onto {result!r}. "
        "assign_model_line must preserve an existing Meridian model line before "
        "falling back to body+powertrain derivation."
    )


def test_f20_meridian_crestwind_stays_crestwind_not_collapsed():
    """Sedan-EV positive control — Crestwind must not collapse onto another sedan-EV line.

    There is currently only one EV sedan line, so this test is a placeholder for
    future collisions. If a second EV sedan line is added, this assertion catches
    a within-powertrain collapse.
    """
    if not _MODULE_PRESENT:
        return
    result = _bvm.assign_model_line({  # type: ignore[union-attr]
        "vehicleId": "VEH-TEST-MRDN-CRESTWIND",
        "make": "Meridian",
        "model": "Crestwind",
        "fuelType": "electric",
        "vehicleType": "Sedan",
    })
    assert result == "MERIDIAN-CRESTWIND", (
        f"F20b FAILED: existing Meridian Crestwind Sedan resolved to {result!r}."
    )


def test_f20_non_meridian_still_uses_body_powertrain():
    """Fallback preserved: a non-Meridian make still uses body+powertrain.

    The F20 fix must not extend to arbitrary makes — `make='DemoMotors'` with
    `model='Trailhead'` is a D24 override case handled by
    `_VEH_ID_TO_MANIFEST`, not by generic model-name matching. This test
    covers a synthetic non-D24 case to guarantee the body+powertrain path is
    still reachable.
    """
    if not _MODULE_PRESENT:
        return
    result = _bvm.assign_model_line({  # type: ignore[union-attr]
        "vehicleId": "VEH-TEST-SYNTH-001",   # not in _VEH_ID_TO_MANIFEST
        "make": "SomeOtherOEM",
        "model": "SomeModel",  # not a Meridian line
        "fuelType": "hybrid",
        "vehicleType": "Pickup",
    })
    assert result == "MERIDIAN-AZIMUTH", (
        f"F20c FAILED: non-Meridian make with unknown model should hit "
        f"body+powertrain (hybrid pickup → Azimuth), got {result!r}."
    )


def test_f20_case_insensitive_meridian_match():
    """Existing-line preservation is case-insensitive on both make and model."""
    if not _MODULE_PRESENT:
        return
    result = _bvm.assign_model_line({  # type: ignore[union-attr]
        "vehicleId": "VEH-TEST-CASING",
        "make": "MERIDIAN",  # unusual casing
        "model": "windrose",  # lowercase
        "fuelType": "electric",
        "vehicleType": "SUV",
    })
    assert result == "MERIDIAN-WINDROSE"
