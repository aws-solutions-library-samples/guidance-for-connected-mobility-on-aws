# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
DX12 — read_freeze_frames command tests (T6.3)

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform/spec.md

Tests:
  1. OUT-OF-FIXTURE DTC       — a DTC in the seeded catalog but NOT in the original
                                 fixture's 6 codes returns SUCCEEDED with synthesised values.
  2. Monotonic timestamps      — two sequential reads of the same DTC produce different
                                 (increasing) timestamp_ms values.
  3. Powertrain-correct frame  — diesel ECU_ENGINE returns DPF Soot Load / Reductant Level
                                 and NOT Catalyst Monitor Readiness; gasoline ECU_ENGINE
                                 returns Short-Term Fuel Trim Bank 1 / Catalyst Monitor
                                 Readiness and NOT DPF Soot Load.
  4. NEGATIVE CONTROL          — 'FREEZE_FRAME_BY_DTC' is unreachable from the fixture module
                                  (ImportError).  Guarantees the fixture is no longer
                                  client-consulted.
  5. FAILED path               — a DTC not in the catalog returns FAILED with a message
                                  naming the DTC.

Run:
    python3 -m pytest deployment/scripts/tests/test_freeze_frames.py -v
    (from repo root)
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from unittest.mock import MagicMock

# ── path setup ──────────────────────────────────────────────────────────────
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
_SIDECAR_DIR = os.path.join(_REPO_ROOT, 'services', 'simulation')
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
if _SIDECAR_DIR not in sys.path:
    sys.path.insert(0, _SIDECAR_DIR)

os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')

from realtime_telemetry_simulator import (  # noqa: E402
    TokenBucket,
    _handle_sovd,
    _synthesise_frame,
    _SIDECAR_ECU_MAP,
)

# ── constants ───────────────────────────────────────────────────────────────

# DTC in the seeded catalog (seed_dtc_catalog_gap_fill.py T2A.4) but NOT
# in the original fixture's 6 codes (P0420, P0300, C0035, U0100, P0171, B0001).
# P0520 = "Oil Pressure Sensor/Switch Circuit" — P1 severity, ECM-domain.
_OUT_OF_FIXTURE_DTC = 'P0520'

# DTC absent from both fixture and gap-fill catalog — triggers FAILED response.
_UNKNOWN_DTC = 'P9999'

# Vehicle IDs: VEH-001 maps to diesel (hash % 2 == 0), VEH-002 maps to gasoline.
# Verified deterministically:
#   import hashlib; int(hashlib.md5('VEH-001:fuel'.encode()).hexdigest(),16) % 2 == 0  → True
#   import hashlib; int(hashlib.md5('VEH-002:fuel'.encode()).hexdigest(),16) % 2 == 0  → False
_DIESEL_VEHICLE_ID = 'VEH-001'
_GASOLINE_VEHICLE_ID = 'VEH-002'

_VIN = 'VIN-DX12'
_CORR_ID = 'dx12-corr-001'
_MSG_TOPIC = f'cms/commands/things/{_VIN}/executions/{_CORR_ID}/sovd/request'

# ECU to target for engine-domain DTC tests.
_ECU_ENGINE = 'ECU_ENGINE'

# DID names from T5.3 DID profiles (verbatim 'name' strings).
_DIESEL_INDICATOR_DID  = 'DPF Soot Load'
_DIESEL_EXCLUSIVE_DID  = 'Reductant Level'        # diesel-only
_GAS_EXCLUSIVE_DID     = 'Catalyst Monitor Readiness'  # gasoline-only
_GAS_FUEL_TRIM_DID     = 'Short-Term Fuel Trim Bank 1'


def _full_token_bucket(capacity: float = 20.0) -> TokenBucket:
    """Return a bucket with plenty of tokens for the test."""
    return TokenBucket(rate=1.0, capacity=capacity, per=10.0)


def _run_read_freeze_frames(
    vehicle_id: str,
    dtc_code: str,
    ecu: str = _ECU_ENGINE,
    correlation_id: str = _CORR_ID,
    token_bucket: "TokenBucket | None" = None,
) -> tuple[MagicMock, list[dict]]:
    """Execute a single-ECU read_freeze_frames and return mqtt mock + parsed payloads."""
    mqtt_client = MagicMock()
    tb = token_bucket or _full_token_bucket()

    cmd = {
        'command_type': 'read_freeze_frames',
        'components': [ecu],
        'parameters': {'dtc_code': dtc_code},
        'correlation_id': correlation_id,
    }

    _handle_sovd(cmd, _MSG_TOPIC, mqtt_client, vehicle_id, _VIN, tb)

    payloads = [
        json.loads(call_args[0][1])
        for call_args in mqtt_client.publish.call_args_list
        if call_args[0]
    ]
    return mqtt_client, payloads


class TestDX12OutOfFixtureDTC(unittest.TestCase):
    """DX12.1 — An out-of-fixture DTC that IS in the catalog returns SUCCEEDED."""

    def test_out_of_fixture_dtc_returns_succeeded(self):
        """P0520 is in the catalog (gap_fill T2A.4) but not in the original 6-DTC fixture.

        The response must be SUCCEEDED, not FAILED or UNSUPPORTED.
        """
        _, payloads = _run_read_freeze_frames(
            vehicle_id=_DIESEL_VEHICLE_ID,
            dtc_code=_OUT_OF_FIXTURE_DTC,
        )
        # Single-ECU → exactly one terminal message (no PROGRESS for single-ECU).
        terminal = next(
            (p for p in payloads if p.get('status') in ('SUCCEEDED', 'FAILED', 'PARTIAL')),
            None,
        )
        self.assertIsNotNone(terminal, "No terminal message published")
        self.assertEqual(
            terminal['status'], 'SUCCEEDED',
            f"Expected SUCCEEDED for catalog DTC {_OUT_OF_FIXTURE_DTC!r}, got {terminal['status']!r}. "
            "If FAILED: the DTC may have been removed from _CATALOG_DTC_CODES in the handler."
        )

    def test_out_of_fixture_dtc_frame_is_not_static_blob(self):
        """Response frame for P0520 must not be the old static fixture blob.

        The original fixture used legacy camelCase signal names (engineRpm, fuelTrim, ...).
        The new sidecar handler uses T5.3 DID names ('DPF Soot Load', etc.).
        """
        _, payloads = _run_read_freeze_frames(
            vehicle_id=_DIESEL_VEHICLE_ID,
            dtc_code=_OUT_OF_FIXTURE_DTC,
        )
        terminal = next(
            (p for p in payloads if p.get('status') == 'SUCCEEDED'), None
        )
        self.assertIsNotNone(terminal, "No SUCCEEDED terminal message")
        component = terminal['components'].get(_ECU_ENGINE, {})
        frame = component.get('frame', {})

        # Must NOT contain the old fixture signal names.
        self.assertNotIn(
            'fuelTrim', frame,
            "Frame contains legacy fixture field 'fuelTrim' — client is still reading the fixture."
        )
        self.assertNotIn(
            'engineRpm', frame,
            "Frame contains legacy fixture field 'engineRpm' — client is still reading the fixture."
        )


class TestDX12MonotonicTimestamps(unittest.TestCase):
    """DX12.2 — Two sequential reads of the same DTC produce different, increasing timestamp_ms."""

    def test_sequential_reads_produce_different_timestamps(self):
        """Two sequential reads of the same DTC must have increasing timestamp_ms."""
        _, payloads1 = _run_read_freeze_frames(
            vehicle_id=_DIESEL_VEHICLE_ID,
            dtc_code=_OUT_OF_FIXTURE_DTC,
            correlation_id='dx12-mono-001',
        )
        # Small sleep to ensure monotonic clock advances.
        time.sleep(0.005)
        _, payloads2 = _run_read_freeze_frames(
            vehicle_id=_DIESEL_VEHICLE_ID,
            dtc_code=_OUT_OF_FIXTURE_DTC,
            correlation_id='dx12-mono-002',
        )

        def _get_timestamp(payloads: list) -> int:
            terminal = next(
                (p for p in payloads if p.get('status') == 'SUCCEEDED'), None
            )
            if terminal is None:
                return -1
            return terminal['components'].get(_ECU_ENGINE, {}).get('timestamp_ms', -1)

        ts1 = _get_timestamp(payloads1)
        ts2 = _get_timestamp(payloads2)

        self.assertGreater(ts1, 0, "First read has no valid timestamp_ms")
        self.assertGreater(ts2, 0, "Second read has no valid timestamp_ms")
        self.assertGreater(
            ts2, ts1,
            f"Second timestamp ({ts2}) is not greater than first ({ts1}). "
            "Timestamps must be monotonically increasing across reads."
        )


class TestDX12PowertrainCorrectFrame(unittest.TestCase):
    """DX12.3 — Diesel gets DPF/reductant fields; gasoline gets fuel trim/catalyst.

    Vehicle ID 'VEH-001' maps deterministically to diesel (hash % 2 == 0).
    Vehicle ID 'VEH-002' maps deterministically to gasoline (hash % 2 == 1).
    """

    def _terminal_frame(self, vehicle_id: str, dtc_code: str) -> dict:
        """Helper: return the frame dict for ECU_ENGINE from a SUCCEEDED response."""
        _, payloads = _run_read_freeze_frames(
            vehicle_id=vehicle_id,
            dtc_code=dtc_code,
        )
        terminal = next(
            (p for p in payloads if p.get('status') == 'SUCCEEDED'), None
        )
        if terminal is None:
            return {}
        return terminal['components'].get(_ECU_ENGINE, {}).get('frame', {})

    def test_diesel_vehicle_frame_contains_dpf_soot_load(self):
        """Diesel ECU_ENGINE frame must contain 'DPF Soot Load'."""
        # Use P0520 (Oil Pressure — ECM domain, valid for diesel engine ECU).
        frame = self._terminal_frame(_DIESEL_VEHICLE_ID, _OUT_OF_FIXTURE_DTC)
        self.assertIn(
            _DIESEL_INDICATOR_DID, frame,
            f"Diesel frame missing '{_DIESEL_INDICATOR_DID}'. Frame keys: {list(frame.keys())}"
        )

    def test_diesel_vehicle_frame_does_not_contain_catalyst_monitor(self):
        """Diesel ECU_ENGINE frame must NOT contain 'Catalyst Monitor Readiness'.

        Catalyst Monitor is a gasoline-only concept. Presence here means the
        handler returned a gasoline frame for a diesel vehicle — DX31 violation.
        """
        frame = self._terminal_frame(_DIESEL_VEHICLE_ID, _OUT_OF_FIXTURE_DTC)
        self.assertNotIn(
            _GAS_EXCLUSIVE_DID, frame,
            f"Diesel frame contains gasoline-exclusive DID '{_GAS_EXCLUSIVE_DID}'. "
            f"Frame keys: {list(frame.keys())}"
        )

    def test_gasoline_vehicle_frame_contains_short_term_fuel_trim(self):
        """Gasoline ECU_ENGINE frame must contain 'Short-Term Fuel Trim Bank 1'."""
        frame = self._terminal_frame(_GASOLINE_VEHICLE_ID, _OUT_OF_FIXTURE_DTC)
        self.assertIn(
            _GAS_FUEL_TRIM_DID, frame,
            f"Gasoline frame missing '{_GAS_FUEL_TRIM_DID}'. Frame keys: {list(frame.keys())}"
        )

    def test_gasoline_vehicle_frame_contains_catalyst_monitor(self):
        """Gasoline ECU_ENGINE frame must contain 'Catalyst Monitor Readiness'."""
        frame = self._terminal_frame(_GASOLINE_VEHICLE_ID, _OUT_OF_FIXTURE_DTC)
        self.assertIn(
            _GAS_EXCLUSIVE_DID, frame,
            f"Gasoline frame missing '{_GAS_EXCLUSIVE_DID}'. Frame keys: {list(frame.keys())}"
        )

    def test_gasoline_vehicle_frame_does_not_contain_dpf_soot_load(self):
        """Gasoline ECU_ENGINE frame must NOT contain 'DPF Soot Load'.

        DPF Soot Load is diesel-only. Presence here means the handler returned
        a diesel frame for a gasoline vehicle — powertrain cross-contamination.
        """
        frame = self._terminal_frame(_GASOLINE_VEHICLE_ID, _OUT_OF_FIXTURE_DTC)
        self.assertNotIn(
            _DIESEL_INDICATOR_DID, frame,
            f"Gasoline frame contains diesel-exclusive DID '{_DIESEL_INDICATOR_DID}'. "
            f"Frame keys: {list(frame.keys())}"
        )


class TestDX12NegativeControlFixtureUnreachable(unittest.TestCase):
    """DX12.4 — The original 'FREEZE_FRAME_BY_DTC' symbol is gone from the fixture module.

    Guarantees that no client can accidentally import the old fixture name and
    bypass the sidecar's bus-read semantics.
    """

    def test_old_symbol_name_raises_import_error(self):
        """'FREEZE_FRAME_BY_DTC' (old public name) must not be importable.

        The symbol was renamed to '_SIM_INPUT_ONLY_FREEZE_FRAME_BY_DTC' in T6.3.
        Attempting to import the old name must raise ImportError or AttributeError,
        or the module-level attribute must be absent.
        """
        # Attempt 1: try direct import — should raise ImportError.
        raised = False
        try:
            from services.simulation.uds_freeze_frame_fixtures import FREEZE_FRAME_BY_DTC  # noqa: F401
        except (ImportError, AttributeError):
            raised = True

        if not raised:
            # Attempt 2: check via getattr on the module.
            import services.simulation.uds_freeze_frame_fixtures as _fixture_mod
            old_attr = getattr(_fixture_mod, 'FREEZE_FRAME_BY_DTC', None)
            self.assertIsNone(
                old_attr,
                "FREEZE_FRAME_BY_DTC is still accessible as a module attribute. "
                "The rename to _SIM_INPUT_ONLY_FREEZE_FRAME_BY_DTC did not remove it. "
                "Client-side code can still reach the fixture — T6.3 retirement is incomplete."
            )

    def test_new_private_symbol_exists(self):
        """The renamed private symbol must still exist for sidecar use."""
        import services.simulation.uds_freeze_frame_fixtures as _fixture_mod
        new_attr = getattr(_fixture_mod, '_SIM_INPUT_ONLY_FREEZE_FRAME_BY_DTC', None)
        self.assertIsNotNone(
            new_attr,
            "_SIM_INPUT_ONLY_FREEZE_FRAME_BY_DTC is missing from the fixture module. "
            "The uds_dtc_responder and realtime_telemetry_simulator read_dtcs path will fail."
        )
        self.assertIsInstance(new_attr, dict, "_SIM_INPUT_ONLY_FREEZE_FRAME_BY_DTC must be a dict")
        self.assertGreater(len(new_attr), 0, "_SIM_INPUT_ONLY_FREEZE_FRAME_BY_DTC must not be empty")


class TestDX12FailedPath(unittest.TestCase):
    """DX12.5 — A DTC not in the catalog returns FAILED with an actionable error."""

    def test_unknown_dtc_returns_failed(self):
        """P9999 is not in the seeded catalog — must return FAILED, not SUCCEEDED."""
        _, payloads = _run_read_freeze_frames(
            vehicle_id=_DIESEL_VEHICLE_ID,
            dtc_code=_UNKNOWN_DTC,
        )
        terminal = next(
            (p for p in payloads if p.get('status') in ('SUCCEEDED', 'FAILED', 'PARTIAL')),
            None,
        )
        self.assertIsNotNone(terminal, "No terminal message published for unknown DTC")
        self.assertEqual(
            terminal['status'], 'FAILED',
            f"Expected FAILED for unknown DTC {_UNKNOWN_DTC!r}, got {terminal['status']!r}."
        )

    def test_unknown_dtc_error_names_the_dtc(self):
        """FAILED response must include the DTC code in the error message."""
        _, payloads = _run_read_freeze_frames(
            vehicle_id=_DIESEL_VEHICLE_ID,
            dtc_code=_UNKNOWN_DTC,
        )
        terminal = next(
            (p for p in payloads if p.get('status') == 'FAILED'), None
        )
        self.assertIsNotNone(terminal, "No FAILED message published for unknown DTC")
        error = terminal.get('error', '')
        self.assertIn(
            _UNKNOWN_DTC, error,
            f"FAILED error message does not name the DTC {_UNKNOWN_DTC!r}: {error!r}"
        )


class TestDX12SampleDieselResponse(unittest.TestCase):
    """Acceptance test: diesel Sirocco DTC P0520 produces a DPF-containing frame."""

    def test_diesel_sirocco_dtc_sample_response_shape(self):
        """The sample response for a diesel ECU_ENGINE DTC must include DPF Soot Load.

        This is the DX12 sample response check from the T6.3 spec, verifying
        end-to-end that the full handler path produces the correct shape.
        """
        _, payloads = _run_read_freeze_frames(
            vehicle_id=_DIESEL_VEHICLE_ID,
            dtc_code='P0520',
        )
        terminal = next(
            (p for p in payloads if p.get('status') == 'SUCCEEDED'), None
        )
        self.assertIsNotNone(terminal, "No SUCCEEDED response for diesel Sirocco DTC P0520")

        # Verify overall response shape.
        self.assertIn('components', terminal)
        self.assertIn(_ECU_ENGINE, terminal['components'])

        component = terminal['components'][_ECU_ENGINE]
        self.assertEqual(component.get('dtc_code'), 'P0520')
        self.assertIsInstance(component.get('timestamp_ms'), int)
        self.assertGreater(component.get('timestamp_ms', 0), 0)

        frame = component.get('frame', {})
        self.assertIsInstance(frame, dict)
        self.assertGreater(len(frame), 0, "Frame must not be empty for diesel ECU_ENGINE DTC")

        # DPF Soot Load is the canonical diesel indicator DID.
        self.assertIn(
            'DPF Soot Load', frame,
            f"Diesel sample response frame missing 'DPF Soot Load'. "
            f"Frame keys: {sorted(frame.keys())}"
        )


class TestDX12CommandsLambdaAllowList(unittest.TestCase):
    """Verify 'read_freeze_frames' is admitted by the commands_lambda allow-list (line 256)."""

    def test_read_freeze_frames_in_allow_list(self):
        """commands_lambda.py must accept 'read_freeze_frames' as a valid command_type."""
        import pathlib
        src = pathlib.Path(_REPO_ROOT) / 'services' / 'commands' / 'commands_lambda.py'
        self.assertTrue(src.exists(), f"commands_lambda.py not found at {src}")
        text = src.read_text()
        self.assertIn(
            "'read_freeze_frames'", text,
            "commands_lambda.py does not contain 'read_freeze_frames' in its allow-list. "
            "Add it alongside read_dtcs, clear_dtcs, read_identity."
        )


if __name__ == '__main__':
    unittest.main()
