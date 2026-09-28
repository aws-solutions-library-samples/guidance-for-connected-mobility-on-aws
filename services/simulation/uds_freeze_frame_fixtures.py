# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# UDS Freeze-Frame Fixtures
# =========================
# Demo-grade freeze-frame data for the 6 DTC codes the CVX KB ships.
# Values are physically plausible for each fault condition — they are
# NOT real vehicle telemetry, NOT VINs, NOT customer data.
# They are synthetic demo constants for the staging simulator.
#
# ⚠️  SIM-RESPONDER INPUT ONLY — NEVER imported by clients.
# ──────────────────────────────────────────────────────────
# This file is consumed ONLY by:
#   • services/simulation/uds_dtc_responder.py  (UDS 0x19 04 on-CAN binary responder)
#   • services/simulation/realtime_telemetry_simulator.py  (SOVD sidecar read_dtcs path)
#
# The public symbol is intentionally prefixed with underscore (_SIM_INPUT_ONLY_*)
# to signal its narrow scope.  Client code (services/commands/, main_api/, UI)
# MUST NOT import from this file.  Clients obtain freeze-frame data by issuing a
# 'read_freeze_frames' SOVD command to the sidecar and reading its response.
#
# Spec: .kiro/specs/2026-09-01-cms-remote-diagnostics-sovd/spec.md § Design § 3
# Task: 3.2 — UDS responder extension for Clear and Freeze Frame
#       6.3 — read_freeze_frames command (retired from client-side use)
#
# Public-mirror clean: no VINs, no brand names, no account IDs.

from __future__ import annotations

from typing import Dict, Tuple, Union

# Type alias: signal_name → (value, unit)
_SignalValue = Tuple[Union[int, float], str]
_FreezeFrameEntry = Dict[str, _SignalValue]

# ── Per-DTC freeze-frame data ─────────────────────────────────────────────
#
# Signals covered:
#   engineRpm       — engine speed (rpm)
#   coolantTemp     — engine coolant temperature (degC)
#   vehicleSpeed    — vehicle speed at fault moment (km/h)
#   engineLoad      — calculated engine load (%)
#   throttlePosition — throttle plate opening (%)
#   fuelTrim        — short-term fuel trim — bank 1 (%)
#
# Design rationale (physically plausible values per fault):
#
#   P0420 — Catalyst System Efficiency Below Threshold (Bank 1)
#     Engine warmed up, moderate highway cruise, fuelTrim slightly elevated
#     because the O2 sensor after the cat is reacting; catalyst borderline.
#
#   P0300 — Random/Multiple Cylinder Misfire Detected
#     Rough idle, RPM low/unsteady, load high (fighting misfire), fuelTrim
#     positive (ECU adding fuel to compensate for lean cylinders).
#
#   C0035 — Left Front Wheel Speed Sensor Circuit (Chassis)
#     Driving at moderate speed; ABS sensor fault; most engine params normal.
#
#   U0100 — Lost Communication with ECM/PCM "A" (Network)
#     Bus fault captured mid-drive; values are last-known-good readings
#     from the secondary module that logged the U code.
#
#   P0171 — System Too Lean (Bank 1)
#     Lean mixture: fuelTrim high-positive, engine compensating; elevated RPM
#     suggesting fuel starvation at cruise.
#
#   B0001 — Driver Frontal Stage 1 Deployment (Body)
#     Impact-triggered deployment event; speed at capture was highway speed,
#     engine off-cycle (airbag fired); RPM falling toward 0 at capture point.

# Renamed from FREEZE_FRAME_BY_DTC → _SIM_INPUT_ONLY_FREEZE_FRAME_BY_DTC (T6.3).
# The old public name is intentionally absent so any client-side import raises
# ImportError at import time, surfacing the defect immediately.
_SIM_INPUT_ONLY_FREEZE_FRAME_BY_DTC: Dict[str, _FreezeFrameEntry] = {
    "P0420": {
        # Catalyst efficiency low — warm engine, highway cruise
        "engineRpm":        (2350,   "rpm"),
        "coolantTemp":      (93,     "degC"),    # slightly elevated
        "vehicleSpeed":     (88,     "km/h"),
        "engineLoad":       (42.4,   "%"),
        "throttlePosition": (18.0,   "%"),
        "fuelTrim":         (2.3,    "%"),        # borderline lean correction
    },
    "P0300": {
        # Random misfire — rough idle, high load, positive fuelTrim
        "engineRpm":        (680,    "rpm"),      # unstable idle
        "coolantTemp":      (86,     "degC"),
        "vehicleSpeed":     (0,      "km/h"),     # stationary at idle
        "engineLoad":       (78.4,   "%"),        # high — fighting misfire
        "throttlePosition": (5.5,    "%"),
        "fuelTrim":         (12.5,   "%"),        # ECU adding fuel for lean cylinders
    },
    "C0035": {
        # Front wheel-speed sensor fault — normal driving, chassis code
        "engineRpm":        (1850,   "rpm"),
        "coolantTemp":      (89,     "degC"),
        "vehicleSpeed":     (62,     "km/h"),
        "engineLoad":       (38.8,   "%"),
        "throttlePosition": (14.5,   "%"),
        "fuelTrim":         (0.8,    "%"),        # nearly stoichiometric
    },
    "U0100": {
        # Lost ECM/PCM communication — last-known-good values at fault moment
        "engineRpm":        (1420,   "rpm"),
        "coolantTemp":      (91,     "degC"),
        "vehicleSpeed":     (45,     "km/h"),
        "engineLoad":       (31.2,   "%"),
        "throttlePosition": (10.0,   "%"),
        "fuelTrim":         (0.0,    "%"),        # no trim data available
    },
    "P0171": {
        # System lean bank 1 — high positive fuel trim, elevated RPM
        "engineRpm":        (2100,   "rpm"),
        "coolantTemp":      (87,     "degC"),
        "vehicleSpeed":     (72,     "km/h"),
        "engineLoad":       (44.0,   "%"),
        "throttlePosition": (16.5,   "%"),
        "fuelTrim":         (21.9,   "%"),        # significantly lean
    },
    "B0001": {
        # Airbag deployment event — captured at moment of impact
        "engineRpm":        (850,    "rpm"),      # engine decelerating post-impact
        "coolantTemp":      (88,     "degC"),
        "vehicleSpeed":     (104,    "km/h"),     # highway speed at impact
        "engineLoad":       (12.0,   "%"),        # throttle released
        "throttlePosition": (0.0,    "%"),
        "fuelTrim":         (0.0,    "%"),        # fuel cut active post-impact
    },
}

# Ensure every entry covers the 6 UI-known signals.
_REQUIRED_SIGNALS = frozenset({
    "engineRpm", "coolantTemp", "vehicleSpeed",
    "engineLoad", "throttlePosition", "fuelTrim",
})

for _dtc, _signals in _SIM_INPUT_ONLY_FREEZE_FRAME_BY_DTC.items():
    _missing = _REQUIRED_SIGNALS - set(_signals)
    if _missing:
        raise ValueError(
            f"uds_freeze_frame_fixtures: DTC {_dtc!r} is missing required "
            f"signals: {sorted(_missing)}"
        )
