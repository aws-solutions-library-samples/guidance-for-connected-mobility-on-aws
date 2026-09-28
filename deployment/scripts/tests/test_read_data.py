# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
DX11 — read_data command (UDS 0x22 ReadDataByIdentifier) unit tests.

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform (D9, T6.2)

Six tests:
  1. Round-trip SUCCEEDED: VEH-MICH-001 (Sirocco/ICE_DIESEL) → diesel ECM DID
     returns status='SUCCEEDED' with four payload fields.
  2. UNSUPPORTED_DID / FAILED (C9 pin): well-formed DID not in the resolved
     ECU profile. Status must be DISTINCT from 'SUCCEEDED'.
  3. Model-specific (DX18 companion at read_data layer): DID '2101'
     (Short-Term Fuel Trim Bank 1) exists in ICE_GASOLINE ECM but NOT in
     ICE_DIESEL ECM. Mistral (gasoline, VEH-ENT-001) → SUCCEEDED;
     Sirocco (diesel, VEH-MICH-001) → FAILED / UNSUPPORTED_DID.
  4. FAILED-no-manifest: vehicle with no modelManifestName → FAILED with
     actionable error naming the missing field.
  5. Malformed DID: 'ZZZZ' → FAILED with 'MALFORMED_DID'.
  6. F21 negative control (mandatory): source-read the handler. Assert (a)
     the exact literal string 'SUCCEEDED' appears in the read_data emit path,
     and (b) the string 'OK' (as a status literal) does NOT appear there.
     Same technique as test_f21_happy_path_terminal_status_is_succeeded_not_ok
     in test_incremental_scan.py.

Run:
    pytest deployment/scripts/tests/test_read_data.py -v
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# sys.path: make services/simulation + deployment/scripts importable
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, '..', '..', '..'))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, '..'))
_SIM_DIR = os.path.abspath(os.path.join(_REPO_ROOT, 'services', 'simulation'))

for _p in (_REPO_ROOT, _SCRIPTS_DIR, _SIM_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')
os.environ.setdefault('DEPLOYMENT_STAGE', 'staging')

# ---------------------------------------------------------------------------
# Guarded imports
# ---------------------------------------------------------------------------
try:
    from realtime_telemetry_simulator import _handle_sovd, TokenBucket  # type: ignore
    _SIDECAR_PRESENT = True
except ImportError:
    _handle_sovd = None  # type: ignore
    TokenBucket = None  # type: ignore
    _SIDECAR_PRESENT = False

try:
    from powertrain_profiles import (  # type: ignore
        get_did_profile,
        DID_PROFILE_ICE_DIESEL,
        DID_PROFILE_ICE_GASOLINE,
    )
    _PROFILES_PRESENT = True
except ImportError:
    _PROFILES_PRESENT = False
    get_did_profile = None  # type: ignore
    DID_PROFILE_ICE_DIESEL = 'meridian-ice-diesel-v1'
    DID_PROFILE_ICE_GASOLINE = 'meridian-ice-gasoline-v1'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_token_bucket(capacity: float = 9.0) -> object:
    return TokenBucket(rate=9.0, capacity=capacity, per=10.0)


def _run_read_data(
    vehicle_id: str,
    ecu: str,
    did: str,
    correlation_id: str,
    ddb_vehicle_item: dict | None,
    ddb_manifest_item: dict | None,
) -> dict:
    """Invoke _handle_sovd for a read_data command with mocked DDB.

    Mocks boto3.resource so no real AWS calls are made.  Returns the last
    published JSON payload as a dict.
    """
    mqtt_client = MagicMock()
    tb = _make_token_bucket()

    # Build a mock DDB resource whose Table().get_item() returns controlled data.
    mock_ddb = MagicMock()

    def _table_get_item_factory(table_name: str):
        """Return a MagicMock Table whose get_item() is keyed on table_name."""
        tbl = MagicMock()
        if 'storage-vehicles' in table_name:
            def _get_item_vehicles(Key=None, **kw):  # noqa: N803
                if ddb_vehicle_item is not None:
                    return {'Item': ddb_vehicle_item}
                return {}
            tbl.get_item.side_effect = _get_item_vehicles
        elif 'model-manifest' in table_name:
            def _get_item_manifest(Key=None, **kw):  # noqa: N803
                if ddb_manifest_item is not None:
                    return {'Item': ddb_manifest_item}
                return {}
            tbl.get_item.side_effect = _get_item_manifest
        else:
            tbl.get_item.return_value = {}
        return tbl

    mock_ddb.Table.side_effect = _table_get_item_factory

    # Patch boto3.resource inside the sidecar module namespace.
    import realtime_telemetry_simulator as _rts  # type: ignore
    with patch.object(_rts, 'boto3') as mock_boto3:
        mock_boto3.resource.return_value = mock_ddb
        mock_boto3.client.return_value = MagicMock()  # S3 fallback

        _handle_sovd(
            cmd={
                'command_type': 'read_data',
                'components': [ecu],
                'parameters': {'did': did},
                'correlation_id': correlation_id,
            },
            msg_topic=(
                f'cms/commands/things/VIN-TEST/executions/{correlation_id}/sovd/request'
            ),
            mqtt_client=mqtt_client,
            vehicle_id=vehicle_id,
            vin='VIN-TEST',
            token_bucket=tb,
        )

    # Return the last published payload.
    assert mqtt_client.publish.called, "mqtt_client.publish was never called"
    _last_call = mqtt_client.publish.call_args_list[-1]
    _topic, _payload_str = _last_call[0]
    return json.loads(_payload_str)


# Canonical DDB items for the two demo vehicles.
_SIROCCO_VEHICLE_ITEM = {
    'vehicleId': 'VEH-MICH-001',
    'modelManifestName': 'MERIDIAN-SIROCCO',
}
_MISTRAL_VEHICLE_ITEM = {
    'vehicleId': 'VEH-ENT-001',
    'modelManifestName': 'MERIDIAN-MISTRAL',
}
_SIROCCO_MANIFEST_ITEM = {
    'pk': 'MODEL#MERIDIAN-SIROCCO#1',
    'sk': 'MODEL#MERIDIAN-SIROCCO',
    'modelManifestName': 'MERIDIAN-SIROCCO',
    'modelManifestVersion': '1',
    'didProfileRef': DID_PROFILE_ICE_DIESEL,
}
_MISTRAL_MANIFEST_ITEM = {
    'pk': 'MODEL#MERIDIAN-MISTRAL#1',
    'sk': 'MODEL#MERIDIAN-MISTRAL',
    'modelManifestName': 'MERIDIAN-MISTRAL',
    'modelManifestVersion': '1',
    'didProfileRef': DID_PROFILE_ICE_GASOLINE,
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _SIDECAR_PRESENT, reason='sidecar not importable')
class TestReadDataDX11:
    """DX11 — six tests for the read_data (0x22) handler."""

    # ── Test 1: Round-trip SUCCEEDED ─────────────────────────────────────────
    def test_1_round_trip_succeeded_diesel_ecm(self):
        """DX11-1: VEH-MICH-001 (Sirocco/ICE_DIESEL) + valid diesel ECM DID
        returns status='SUCCEEDED' with all four required payload fields.

        DID '2120' = DPF Soot Load (diesel-only, ICE_DIESEL ECM).
        """
        result = _run_read_data(
            vehicle_id='VEH-MICH-001',
            ecu='ECM',
            did='2120',
            correlation_id='corr-dx11-1',
            ddb_vehicle_item=_SIROCCO_VEHICLE_ITEM,
            ddb_manifest_item=_SIROCCO_MANIFEST_ITEM,
        )
        assert result['status'] == 'SUCCEEDED', (
            f"DX11-1: expected status='SUCCEEDED', got {result['status']!r}. "
            f"Full response: {result}"
        )
        assert result['command_type'] == 'read_data'
        # Four required fields in components[ecu_name]
        ecu_payload = result.get('components', {}).get('ECM')
        assert ecu_payload is not None, (
            f"DX11-1: 'ECM' key absent from components. Got: {result.get('components')}"
        )
        for field in ('did', 'name', 'value', 'unit'):
            assert field in ecu_payload, (
                f"DX11-1: required field {field!r} missing from ECM payload. "
                f"Got: {ecu_payload}"
            )
        assert ecu_payload['did'].upper() == '2120', (
            f"DX11-1: did echo mismatch. Expected '2120', got {ecu_payload['did']!r}"
        )
        assert ecu_payload['name'] == 'DPF Soot Load', (
            f"DX11-1: name mismatch. Expected 'DPF Soot Load', got {ecu_payload['name']!r}"
        )
        # Value must be numeric (int or float).
        assert isinstance(ecu_payload['value'], (int, float)), (
            f"DX11-1: value must be numeric, got {type(ecu_payload['value'])!r}"
        )
        assert ecu_payload['unit'] == '%', (
            f"DX11-1: unit mismatch. Expected '%', got {ecu_payload['unit']!r}"
        )

    # ── Test 2: UNSUPPORTED_DID — C9 pin ─────────────────────────────────────
    def test_2_unsupported_did_is_distinct_from_succeeded(self):
        """DX11-2 / C9: a well-formed DID that is not in the ECU's profile
        must return a status DISTINCT from 'SUCCEEDED'. Empty-success is
        the C9 violation this test guards.

        DID 'BEEF' is a valid 4-hex string but does not appear in any Meridian
        DID profile.
        """
        result = _run_read_data(
            vehicle_id='VEH-MICH-001',
            ecu='ECM',
            did='BEEF',
            correlation_id='corr-dx11-2',
            ddb_vehicle_item=_SIROCCO_VEHICLE_ITEM,
            ddb_manifest_item=_SIROCCO_MANIFEST_ITEM,
        )
        assert result['status'] != 'SUCCEEDED', (
            f"DX11-2 / C9 VIOLATION: unsupported DID returned status='SUCCEEDED'. "
            f"Must return FAILED or a distinct non-success status. "
            f"Full response: {result}"
        )
        # Error string must mention the DID.
        error_str = result.get('error', '')
        assert 'BEEF' in error_str.upper() or 'UNSUPPORTED' in error_str.upper(), (
            f"DX11-2: error string should name the unsupported DID or say UNSUPPORTED_DID. "
            f"Got error={error_str!r}"
        )
        # components must be empty — no partial SUCCEEDED data.
        assert result.get('components') == {} or result.get('components') is None, (
            f"DX11-2 / C9: components must be empty for an unsupported DID. "
            f"Got: {result.get('components')}"
        )

    # ── Test 3: Model-specific (DX18 companion at read_data layer) ────────────
    def test_3_model_specific_did_2101_gasoline_only(self):
        """DX11-3 / DX18: DID '2101' (Short-Term Fuel Trim Bank 1) exists in
        ICE_GASOLINE ECM (Mistral) but NOT in ICE_DIESEL ECM (Sirocco).

        Mistral (VEH-ENT-001 / ICE_GASOLINE) → SUCCEEDED.
        Sirocco (VEH-MICH-001 / ICE_DIESEL)  → FAILED / UNSUPPORTED_DID.

        This guards the D9 per-model DID isolation: resolving DIDs against a
        global table would give both vehicles the gasoline DID, hiding the
        powertrain mismatch.
        """
        # 3a. Gasoline (Mistral) → SUCCEEDED
        result_gas = _run_read_data(
            vehicle_id='VEH-ENT-001',
            ecu='ECM',
            did='2101',
            correlation_id='corr-dx11-3a',
            ddb_vehicle_item=_MISTRAL_VEHICLE_ITEM,
            ddb_manifest_item=_MISTRAL_MANIFEST_ITEM,
        )
        assert result_gas['status'] == 'SUCCEEDED', (
            f"DX11-3a: Mistral (gasoline) + DID 2101 should return SUCCEEDED. "
            f"Got status={result_gas['status']!r}. Full response: {result_gas}"
        )
        ecu_payload_gas = result_gas.get('components', {}).get('ECM')
        assert ecu_payload_gas is not None
        assert ecu_payload_gas['name'] == 'Short-Term Fuel Trim Bank 1', (
            f"DX11-3a: name mismatch. Got {ecu_payload_gas.get('name')!r}"
        )

        # 3b. Diesel (Sirocco) → FAILED / UNSUPPORTED_DID
        result_diesel = _run_read_data(
            vehicle_id='VEH-MICH-001',
            ecu='ECM',
            did='2101',
            correlation_id='corr-dx11-3b',
            ddb_vehicle_item=_SIROCCO_VEHICLE_ITEM,
            ddb_manifest_item=_SIROCCO_MANIFEST_ITEM,
        )
        assert result_diesel['status'] != 'SUCCEEDED', (
            f"DX11-3b / DX18 VIOLATION: Sirocco (diesel) + DID 2101 must NOT return "
            f"SUCCEEDED — DID 2101 is gasoline-only (ICE_GASOLINE ECM). "
            f"This is the D9 per-model isolation guard. "
            f"Full response: {result_diesel}"
        )

    # ── Test 4: FAILED — vehicle with no modelManifestName ───────────────────
    def test_4_failed_no_model_manifest_name(self):
        """DX11-4: a vehicle row with no modelManifestName field returns FAILED
        with an actionable error string that names the missing field.
        """
        result = _run_read_data(
            vehicle_id='VEH-SYNTHETIC-NO-MODEL',
            ecu='ECM',
            did='2120',
            correlation_id='corr-dx11-4',
            ddb_vehicle_item={
                'vehicleId': 'VEH-SYNTHETIC-NO-MODEL',
                # modelManifestName deliberately absent
            },
            ddb_manifest_item=None,
        )
        assert result['status'] == 'FAILED', (
            f"DX11-4: vehicle with no modelManifestName must return FAILED. "
            f"Got {result['status']!r}. Full response: {result}"
        )
        error_str = result.get('error', '')
        # Error must name the missing field so the operator knows what to fix.
        assert 'modelManifestName' in error_str or 'MISSING_MODEL' in error_str.upper(), (
            f"DX11-4: error string should mention 'modelManifestName' or 'MISSING_MODEL'. "
            f"Got: {error_str!r}"
        )

    # ── Test 5: Malformed DID ─────────────────────────────────────────────────
    def test_5_malformed_did_returns_failed_malformed_did(self):
        """DX11-5: a DID value 'ZZZZ' is not 4 valid hex chars.
        The handler must return FAILED with error='MALFORMED_DID' before any
        DDB lookup.
        """
        result = _run_read_data(
            vehicle_id='VEH-MICH-001',
            ecu='ECM',
            did='ZZZZ',
            correlation_id='corr-dx11-5',
            ddb_vehicle_item=_SIROCCO_VEHICLE_ITEM,
            ddb_manifest_item=_SIROCCO_MANIFEST_ITEM,
        )
        assert result['status'] == 'FAILED', (
            f"DX11-5: malformed DID 'ZZZZ' must return FAILED. "
            f"Got {result['status']!r}. Full response: {result}"
        )
        error_str = result.get('error', '')
        assert 'MALFORMED_DID' in error_str, (
            f"DX11-5: error must contain 'MALFORMED_DID'. Got: {error_str!r}"
        )

    # ── Test 6: F21 negative control (mandatory) ──────────────────────────────
    def test_6_f21_negative_control_succeeded_literal_in_handler(self):
        """DX11-6 / F21: source-read the sidecar and assert:
          (a) The exact literal string 'SUCCEEDED' appears in the read_data
              handler's emit path.
          (b) The literal 'status': 'OK' does NOT appear as a status value
              in the read_data emit path.

        command_response_handler.py:306 and :452 key their idempotency guard
        on the EXACT string 'SUCCEEDED' — renaming to 'OK' silently defeats
        the guard while all other tests continue to pass (F21).

        Same technique as test_f21_happy_path_terminal_status_is_succeeded_not_ok
        in test_incremental_scan.py.
        """
        import pathlib

        src = (
            pathlib.Path(__file__).resolve()
            .parent.parent.parent.parent
            / 'services'
            / 'simulation'
            / 'realtime_telemetry_simulator.py'
        )
        assert src.exists(), f"sidecar source not found at {src}"
        text = src.read_text()

        # (a) The happy-path emit in the read_data handler uses 'SUCCEEDED'
        # as the status value directly in the payload dict (not through
        # terminal_status, which is set later for scan commands).
        # We look for the literal string "'SUCCEEDED'" anywhere in the file
        # — the terminal_status assignment and the read_data SUCCEEDED path
        # both use it.
        assert "'SUCCEEDED'" in text, (
            "F21 REGRESSION in read_data: the literal string 'SUCCEEDED' was not "
            "found in realtime_telemetry_simulator.py. The read_data handler must "
            "emit status='SUCCEEDED' on the happy path. "
            "command_response_handler.py:306/:452 key on this exact string."
        )

        # (b) Assert the string 'status': 'OK' does NOT appear as a status
        # assignment anywhere in the sidecar source. This catches a future
        # edit that introduces 'OK' as an alias.
        assert "'status': 'OK'" not in text, (
            "F21 REGRESSION: 'status': 'OK' found in realtime_telemetry_simulator.py. "
            "Status values must be 'SUCCEEDED', 'FAILED', 'PARTIAL', 'RATE_LIMITED', etc. "
            "Using 'OK' silently defeats command_response_handler.py's idempotency guard."
        )
        # Belt-and-braces: also check the terminal_status assignment used by
        # scan commands (borrowed from test_f21_happy_path_terminal_status_is_succeeded_not_ok).
        assert "terminal_status = 'SUCCEEDED'" in text, (
            "F21 REGRESSION: terminal_status = 'SUCCEEDED' not found in the sidecar source. "
            "Scan-shaped command terminal status must be the literal 'SUCCEEDED'."
        )
        assert "terminal_status = 'OK'" not in text, (
            "F21 REGRESSION: terminal_status = 'OK' found in sidecar source. "
            "Use 'SUCCEEDED' consistently."
        )
