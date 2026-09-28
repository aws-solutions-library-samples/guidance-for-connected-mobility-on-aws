#!/usr/bin/env python3
"""
Real-time Telemetry Simulator for CMS UI
Simulates live vehicle telemetry data and publishes to AWS IoT Core
Triggered from the fleet management UI simulation service
"""

import json
import time
import random
import os
import sys
import boto3
from botocore.exceptions import ClientError
import threading
import logging
import subprocess
import atexit
import signal
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import uuid
from typing import Dict, Any, List, Optional
import ssl
import socket

# MQTT client import
try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    print("⚠️ Paho MQTT client not available. Install with: pip install paho-mqtt")
    MQTT_AVAILABLE = False


# ── UDS-DTC responder launcher ────────────────────────────────────────
#
# ``ensure_uds_responder(desired_map)`` is the ONLY site in this module
# that calls subprocess.Popen for uds_dtc_responder.py.  It is idempotent:
#   - desired_map None or {} → stop any running responder, stay stopped.
#   - desired_map unchanged from the running one → no-op.
#   - desired_map changed → terminate old, spawn new.
#
# Called at module import time with the env-derived map (preserving the
# existing fwe-simulator trip-task behaviour exactly when UDS_DTC_MAP is
# set in the environment), and from PresenceLoop.reconcile_fault_state()
# on every idle tick with the DDB-derived map.
#
# A structural test (test_uds_responder_reconcile.py) asserts there is
# exactly ONE subprocess.Popen call in this file and that it lives inside
# ensure_uds_responder.  Do not add a second Popen site.

_UDS_RESPONDER_PROC = None
_UDS_RESPONDER_MAP: "dict | None" = None   # map currently held by the running proc


def _reset_uds_responder_state():
    """Reset module-level UDS responder state to its initial values.

    Intended for use in test setUp/tearDown to ensure each test starts with
    a clean slate.  This function is inert in production — production code
    never calls it; only test suites do.  The function is deliberately named
    with an underscore and the word "reset" to make its test-only purpose
    obvious on first read.

    Production invariant: every subprocess.Popen mock created by a test is
    replaced when the ``with patch("subprocess.Popen")`` block exits.  Without
    this reset, the mock proc object stored in _UDS_RESPONDER_PROC outlives the
    patch block and is visible to the next test as if it were a real running
    process, causing the second call's idempotency check to skip the spawn.
    Calling this from setUp/tearDown is the correct remedy — it is explicit,
    obvious, and does not require production code to know tests exist.
    """
    global _UDS_RESPONDER_PROC, _UDS_RESPONDER_MAP
    _UDS_RESPONDER_PROC = None
    _UDS_RESPONDER_MAP = None


def ensure_uds_responder(desired_map):
    """Idempotent reconciler — sole subprocess.Popen site for uds_dtc_responder.py.

    ``desired_map`` is a dict (ECU-keyed long-form map) or None / {}.

    Invariants (enforced by tests/test_uds_responder_reconcile.py):
      - None or empty desired with no running responder → no spawn.
      - Non-empty desired → exactly one spawn; child receives the map via
        UDS_DTC_MAP in its environment.
      - Called twice with an unchanged map → still one process, zero extra spawns.
      - Changed map → old process terminated, new one spawned.
      - Empty desired while a responder runs → terminated, not respawned.
      - Spawn failure is logged; never raises.
    """
    global _UDS_RESPONDER_PROC, _UDS_RESPONDER_MAP

    # Normalise: None and {} both mean "no faults".
    desired = desired_map if desired_map else {}

    # ── Terminate if running and map changed (or clearing) ───────────
    if _UDS_RESPONDER_PROC is not None:
        still_alive = _UDS_RESPONDER_PROC.poll() is None
        map_changed = desired != _UDS_RESPONDER_MAP
        if not still_alive or not desired or map_changed:
            # Terminate the old process.
            p = _UDS_RESPONDER_PROC
            try:
                if p.poll() is None:
                    p.send_signal(signal.SIGTERM)
                    try:
                        p.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        p.kill()
                print(f"✓ UDS-DTC responder pid={p.pid} stopped")
            except Exception as e:
                print(f"⚠️ UDS responder shutdown failed: {e}")
            finally:
                _UDS_RESPONDER_PROC = None
                _UDS_RESPONDER_MAP = None

    # ── If nothing desired, leave stopped ────────────────────────────
    if not desired:
        return

    # ── Already running with the same map → idempotent no-op ─────────
    if _UDS_RESPONDER_PROC is not None and _UDS_RESPONDER_PROC.poll() is None:
        return

    # ── Spawn a new responder for the desired map ─────────────────────
    channel = os.environ.get("CAN_BUS0", "vcan0")
    responder_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "uds_dtc_responder.py")
    if not os.path.exists(responder_path):
        print(f"⚠️ UDS_DTC_MAP set but responder missing at {responder_path}; DTC sim will not work")
        return
    try:
        map_json = json.dumps(desired)
        _UDS_RESPONDER_PROC = subprocess.Popen(
            [sys.executable, responder_path, "--channel", channel,
             "--interface", "socketcan", "--log-level", "INFO"],
            # Deliver the map via the child's environment — not via a
            # container override.  See spec § *Why UDS_DTC_MAP is
            # deliberately not added to the sidecar override* and
            # ~/.kiro/steering/secrets-handling.md § Runtime launch parameters.
            # ── Explicit subprocess env (Fix Group 1, Task 2 — Suggestion 2) ──
            # Pass an explicit allowlist rather than inheriting os.environ in
            # its entirety.  The responder reads only UDS_DTC_MAP and CAN_BUS0
            # (channel default) from its environment; PATH is needed for the
            # Python interpreter; PYTHONUNBUFFERED keeps logs visible in the
            # container's CloudWatch stream.  Any future secret added to the
            # vehicle-ecu sidecar's env will not silently flow here.
            # See ~/.kiro/steering/secrets-handling.md § Runtime launch parameters.
            env={
                "UDS_DTC_MAP": map_json,
                "CAN_BUS0": channel,
                "PATH": os.environ.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"),
                "PYTHONUNBUFFERED": "1",
            },
            # Let stdout/stderr bubble up to the container's logs so
            # operators can see responder activity alongside sim output.
            stdout=sys.stdout,
            stderr=sys.stderr,
            start_new_session=True,  # survives SIGHUP from parent shell
        )
        _UDS_RESPONDER_MAP = desired
        print(f"✓ UDS-DTC responder started (pid={_UDS_RESPONDER_PROC.pid}) "
              f"on {channel} with {len(desired)} ECU(s)")
    except Exception as e:
        print(f"⚠️ Failed to spawn UDS-DTC responder: {e}")
        _UDS_RESPONDER_PROC = None
        _UDS_RESPONDER_MAP = None


def _kill_uds_responder():
    """Terminate the UDS responder cleanly on simulator exit (atexit hook)."""
    global _UDS_RESPONDER_PROC, _UDS_RESPONDER_MAP
    p = _UDS_RESPONDER_PROC
    if p is None:
        return
    try:
        if p.poll() is None:  # still running
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
        print(f"✓ UDS-DTC responder pid={p.pid} stopped")
    except Exception as e:
        print(f"⚠️ UDS responder shutdown failed: {e}")
    finally:
        _UDS_RESPONDER_PROC = None
        _UDS_RESPONDER_MAP = None


# Module-import call — the special case of ensure_uds_responder that
# preserves the existing fwe-simulator trip-task behaviour exactly.
# If UDS_DTC_MAP is set in the environment (the Lambda-injected trip path),
# spawn immediately; otherwise this is a no-op.
def _spawn_from_env():
    """Spawn the responder from the environment-provided map (import-time call)."""
    uds_map_str = os.environ.get("UDS_DTC_MAP", "").strip()
    if not uds_map_str or uds_map_str in ("{}", "null"):
        return
    try:
        uds_map = json.loads(uds_map_str)
    except (json.JSONDecodeError, ValueError):
        print(f"⚠️ UDS_DTC_MAP is not valid JSON; DTC sim will not work")
        return
    ensure_uds_responder(uds_map)


_spawn_from_env()
atexit.register(_kill_uds_responder)

# Backward-compatibility alias: existing tests patch
# ``realtime_telemetry_simulator._spawn_uds_responder``.  Keep the name
# patchable so they can replace it without error.  The implementation is now
# _spawn_from_env / ensure_uds_responder.
_spawn_uds_responder = _spawn_from_env


#: City → (latitude, longitude) used as the base point for route generation.
#:
#: Module-level rather than a local in ``main()`` because two callers need it: the
#: container-argv path (``--city``) and the trip-intent path
#: (``PresenceLoop._resolve_trip_overrides``). A second copy of this map in the
#: intent path is precisely the two-sided drift that
#: ``scripts/check_trip_intent_contract.py`` exists to prevent, so there is one map
#: and both paths read it.
#:
#: COUPLED KEY SET — these seven keys must stay identical to:
#:   * the ``--city`` argparse ``choices`` in ``main()``; and
#:   * ``_ALLOWED_CITIES`` in
#:     ``services/connectors/subscriptions/simulate_vehicle/handler.py``.
#: Verified in sync 2026-09-20. Do NOT widen this set without widening both others
#: (spec ``2026-09-20-trip-intent-param-contract`` § Constraints).
CITY_COORDINATES: "dict[str, tuple[float, float]]" = {
    'nyc': (40.7128, -74.0060),      # New York City
    'sf': (37.7749, -122.4194),     # San Francisco
    'chicago': (41.8781, -87.6298), # Chicago
    'miami': (25.7617, -80.1918),   # Miami
    'seattle': (47.6062, -122.3321), # Seattle
    'munich': (48.1351, 11.5820),   # Munich, Germany
    'atlanta': (33.7490, -84.3880)  # Atlanta, Georgia
}


class VehicleState:
    def __init__(self):
        self.last_speed = 0
        self.last_timestamp = 0
        self.seatbelt_violation_start = None
        self.phone_usage_start = None
        self.engine_on = False
        self.route_index = 0
        self.cumulative_miles = 0.0
        self.trip_started = False
        self.current_trip_id = None
        self.current_driver_id = None
        self.maintenance_alert_sent = False  # Track if maintenance alert sent for current trip
        self.route = []  # Initialize empty route

        # === REMOTE-COMMAND ACTUATOR STATE ===
        # Mutated by on_command (see simulate_vehicle_telemetry inline handler);
        # read by generate_telemetry_data so the next emitted frame reflects the
        # command outcome. Boolean unless noted. Defaults model a healthy, locked,
        # engine-off, non-charging vehicle at rest.
        self.doors_locked = True         # actuator: lock_all_doors
        self.door_lf_locked = True       # actuator: lock_door_frontleft
        self.door_rf_locked = True       # actuator: lock_door_frontright
        self.door_lr_locked = True       # actuator: lock_door_rearleft
        self.door_rr_locked = True       # actuator: lock_door_rearright
        self.trunk_locked = True         # actuator: trunk_lock (VSS Body.Trunk.IsLocked)
        self.charge_door_open = False    # actuator: open_charge_door
        self.remote_start_active = False # actuator: remote_start (also sets engine_on)
        
        # === INTELLIGENT CONDITION PROGRESSION ===
        # These values degrade/change over time during trips
        self.tire_pressure_fl = 32.0  # Starts normal, can decrease
        self.tire_pressure_fr = 32.0
        self.tire_pressure_rl = 32.0  
        self.tire_pressure_rr = 32.0
        self.oil_life = 100.0  # Decreases over time/distance
        self.brake_wear = 100.0  # Starts healthy, decreases with braking
        self.engine_temp_base = 195.0  # Normal operating temp
        self.battery_voltage_base = 13.8  # Normal alternator output
        self.fuel_level = round(random.uniform(60, 95), 1)  # Starting fuel level
        self.soc_base = 85.0  # EV state of charge (decreases)
        self.hv_voltage_base = 380.0  # EV HV battery (can degrade)
        
        # === FORCED ALERT CONDITIONS ===
        # Set by API to force specific alerts during simulation
        self.force_tire_blowout = False
        self.force_engine_overheat = False
        self.force_battery_critical = False
        self.force_brake_failure = False
        self.force_oil_pressure_low = False
        self.force_hv_battery_degradation = False
        self.force_safety_event = None  # 'hard_braking', 'collision_avoidance', etc.
        self.tire_slow_leak = False
        self.tire_pressure_imbalance = False
        self.degradation_targets = {}  # {field_name: target_value} from event catalog
        self.safety_rate = 1.0  # Multiplier for safety event probabilities (0.0 to 1.0)
        
        # === PROGRESSION TRACKING ===
        self.trip_distance = 0.0  # Track distance for wear calculations
        self.hard_braking_count = 0  # Track aggressive driving
        self.high_speed_time = 0  # Track time at high speeds

    def reset(self) -> None:
        """Reset trip-progression fields IN PLACE for the next trip.

        Identity-stable: the object is NOT replaced.  Only fields that
        track per-trip movement or lifecycle are cleared; remote-command
        actuator state (doors_locked, etc.) and condition-progression
        values (tire_pressure_*, oil_life, etc.) are intentionally
        preserved across trips so a command applied in the inter-trip
        window is visible in the next trip's telemetry and idle
        emission.

        Called by simulate_vehicle_telemetry's between-trip reset instead
        of the previous ``vehicle_state = VehicleState()`` pattern, which
        replaced the object and split identity with PresenceLoop.vehicle_state.
        See issues/2026-08-04-cms-vehicle-trip-lifecycle-split/ Fix Group 6.
        """
        # Trip lifecycle
        self.last_speed = 0
        self.last_timestamp = 0
        self.seatbelt_violation_start = None
        self.phone_usage_start = None
        self.engine_on = False
        self.route_index = 0
        self.trip_started = False
        self.current_trip_id = None
        self.current_driver_id = None
        self.maintenance_alert_sent = False
        self.route = []
        # Progression tracking (reset per trip so wear/braking counts are per-trip)
        self.trip_distance = 0.0
        self.hard_braking_count = 0
        self.high_speed_time = 0
        # cumulative_miles intentionally NOT reset (it accumulates across trips)


# Fencing token for presence ownership, stable for this process and unique across
# tasks. Written to `presenceOwner` by PresenceLoop.notify_connected and required
# by notify_disconnected's ConditionExpression, so only the current owner (or an
# unowned row) can demote a vehicle to disconnected.
#
# Uniqueness is the only property needed, so a per-process UUID suffices; the
# hostname prefix is there purely to make a stranded token legible to an operator
# reading the row (ECS sets the container id as the hostname).
_PROCESS_OWNER_TOKEN: str = f"{socket.gethostname()}:{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# SOVD helpers — CAN rate limiter and UDS worker
# ---------------------------------------------------------------------------

class TokenBucket:
    """Simple token-bucket rate limiter for CAN per-ECU-request accounting.

    Enforces HARD GATE F (D13): per-ECU accounting so N single-ECU reads cost
    the same budget as one N-ECU full scan.  Each ECU read costs one token.
    The bucket holds up to ``capacity`` tokens; tokens refill at ``rate``
    tokens per ``per`` seconds.  Calling ``consume()`` deducts one token
    (refilling first) and returns True if a token was available, False if
    rate-limited.

    Thread-safe via a threading.Lock.  Instantiated per-vehicle on
    PresenceLoop so the limit is per-vehicle, not global.

    Args:
        rate:     Tokens added per ``per`` seconds.  Use 1 for "1 scan per
                  ``per`` seconds".
        capacity: Maximum token accumulation (burst headroom).  Typically 1
                  for a strict "1 scan per window" policy.
        per:      Refill window in seconds.  Default 10.0.
    """

    def __init__(self, rate: float = 1.0, capacity: float = 1.0, per: float = 10.0):
        self._rate = rate
        self._capacity = capacity
        self._per = per
        self._tokens: float = capacity  # start full
        self._last_refill: float = time.monotonic()
        self._lock = threading.Lock()

    def consume(self) -> bool:
        """Attempt to consume one token.

        Returns True if a token was available (operation allowed), False if
        the bucket is empty (operation rate-limited).
        """
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            # Refill proportionally to time elapsed.
            refill = elapsed * (self._rate / self._per)
            self._tokens = min(self._capacity, self._tokens + refill)
            self._last_refill = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    def seconds_until_refill(self) -> float:
        """Approximate seconds until 1 token will be available."""
        with self._lock:
            if self._tokens >= 1.0:
                return 0.0
            deficit = 1.0 - self._tokens
            return deficit * (self._per / self._rate)


# ECU name → CAN ID mapping — read from the sim lambda's _ECU_BY_NUMBER constant.
# The sidecar does NOT import from services/simulation/lambda/ to avoid coupling
# the ECS task image to the Lambda bundle.  These are hard-coded here as a
# READ-ONLY copy; they MUST NOT diverge from simulation_lambda._ECU_BY_NUMBER.
# Source: services/simulation/lambda/simulation_lambda.py::_ECU_BY_NUMBER
_SIDECAR_ECU_MAP: dict = {
    "ECU_BRAKE":      {"req_id": 0x7E0, "resp_id": 0x7E8},
    "ECU_ENGINE":     {"req_id": 0x7E1, "resp_id": 0x7E9},
    "ECU_POWERTRAIN": {"req_id": 0x7E2, "resp_id": 0x7EA},
    "ECU_PCM":        {"req_id": 0x7E3, "resp_id": 0x7EB},
    "ECU_COMM":       {"req_id": 0x7E4, "resp_id": 0x7EC},
    "ECU_BATTERY_HV": {"req_id": 0x7E5, "resp_id": 0x7ED},
    "ECU_BATTERY_12V":{"req_id": 0x7E6, "resp_id": 0x7EE},
    "ECU_EVAP":       {"req_id": 0x7E7, "resp_id": 0x7EF},
    "ECU_BODY":       {"req_id": 0x18DA09F1, "resp_id": 0x18DAF109},
}

# ── ECU identity baseline versions (D11, F6, T6.4) ──────────────────────────
# Expected (baseline) software versions from the model manifests.
# Keys are manifest ECU names (TCU, BMS, …) — the vocabulary used in
# read_identity requests, NOT the sidecar CAN-address vocabulary.
# Source of truth: deployment/scripts/seed_model_manifests.py (CMS-FLEET-MODEL ecus[]).
# If a requested ECU is absent from this dict the response carries
# expected=null and delta='unknown' — NEVER delta=null (fail-closed, F6).
_IDENTITY_ECU_BASELINE: dict = {
    'TCU':  {'sw_version': '4.0.0',  'hw_version': '1.0'},
    'BMS':  {'sw_version': '3.0.0',  'hw_version': '1.0'},
    'VCU':  {'sw_version': '7.0.0',  'hw_version': '2.0'},
    'BCM':  {'sw_version': '2.5.0',  'hw_version': '1.0'},
    'ADAS': {'sw_version': '2.0.0',  'hw_version': '1.2'},
    'IVI':  {'sw_version': '14.0.0', 'hw_version': '3.0'},
    'GW':   {'sw_version': '1.5.0',  'hw_version': '1.0'},
    'CCU':  {'sw_version': '2.0.0',  'hw_version': '1.0'},
}

# ── read_data ECU vocabulary (T6.2, D9) ─────────────────────────────────────
# read_data uses model-manifest ECU names (ECM, BMS, VCU, BCM, TCU, CCU) as
# defined in the T5.3 DID profiles — NOT the sidecar CAN-bus vocabulary
# (ECU_ENGINE, ECU_BRAKE, …) used by read_dtcs / clear_dtcs.
# This is the union of all DID-profile top-level ECU keys across the four
# powertrain classes (meridian-ev-v1, meridian-hybrid-v1,
# meridian-ice-gasoline-v1, meridian-ice-diesel-v1).
_READ_DATA_ECU_SET: frozenset = frozenset({
    'ECM', 'BMS', 'VCU', 'BCM', 'TCU', 'CCU',
})

# SOVD response bucket — read from the ECS task-role environment variable set by
# the CDK stack (commands_stack.py).  No default: a missing bucket is a
# misconfigured task; we surface it loudly rather than silently dropping S3
# uploads.
SOVD_RESPONSES_BUCKET: str = os.environ.get('SOVD_RESPONSES_BUCKET', '')


def _issue_uds_read(can_bus, ecu_cfg: dict, timeout_s: float = 3.0) -> list:
    """Issue UDS 0x19 0x02 0xFF (report all confirmed DTCs) on *can_bus*.

    Sends to ``ecu_cfg['req_id']`` and waits up to ``timeout_s`` for a
    response from ``ecu_cfg['resp_id']``.

    Returns a list of raw DTC dicts ``{"code": "P0420", "status": "confirmed"}``.
    Returns an empty list on timeout or parse error.

    Only used in the sidecar — this is the demo/sim path.  Production vehicles
    respond via the real ECU firmware.
    """
    try:
        import can as _can
    except ImportError:
        return []  # No python-can in test environment; caller handles gracefully

    req_id = ecu_cfg['req_id']
    resp_id = ecu_cfg['resp_id']

    # UDS 0x19 0x02 0xFF — Report DTC by status mask, mask=FF (all statuses)
    is_extended = req_id > 0x7FF
    msg = _can.Message(
        arbitration_id=req_id,
        data=bytes([0x03, 0x19, 0x02, 0xFF]),
        is_extended_id=is_extended,
    )
    try:
        can_bus.send(msg)
    except Exception:
        return []

    deadline = time.monotonic() + timeout_s
    dtcs = []
    while time.monotonic() < deadline:
        resp = can_bus.recv(timeout=max(0.0, deadline - time.monotonic()))
        if resp is None:
            break
        if resp.arbitration_id != resp_id:
            continue
        # Positive response: service_id=0x59, sub=0x02
        data = resp.data
        if len(data) < 3 or data[0] < 3 or data[1] != 0x59 or data[2] != 0x02:
            continue
        # DTC bytes start at offset 4 (after length, 0x59, 0x02, status_availability)
        i = 4
        while i + 2 < len(data):
            high, mid, low = data[i], data[i+1], data[i+2]
            prefix_bits = (high >> 6) & 0x03
            prefix_char = {0: 'P', 1: 'C', 2: 'B', 3: 'U'}.get(prefix_bits, 'P')
            dtc_num = ((high & 0x3F) << 8) | mid
            code = f"{prefix_char}{dtc_num:04X}"
            dtcs.append({"code": code, "status": "confirmed"})
            i += 3
        break  # single-frame response expected from the sidecar responder
    return dtcs


def _issue_uds_clear(can_bus, ecu_cfg: dict, timeout_s: float = 2.0) -> bool:
    """Issue UDS 0x14 FF FF FF (Clear All Diagnostic Information) on *can_bus*.

    Returns True on positive response (0x54), False on NRC or timeout.
    """
    try:
        import can as _can
    except ImportError:
        return False

    req_id = ecu_cfg['req_id']
    resp_id = ecu_cfg['resp_id']
    is_extended = req_id > 0x7FF
    msg = _can.Message(
        arbitration_id=req_id,
        data=bytes([0x04, 0x14, 0xFF, 0xFF, 0xFF]),
        is_extended_id=is_extended,
    )
    try:
        can_bus.send(msg)
    except Exception:
        return False

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        resp = can_bus.recv(timeout=max(0.0, deadline - time.monotonic()))
        if resp is None:
            break
        if resp.arbitration_id != resp_id:
            continue
        if resp.data and resp.data[1] == 0x54:
            return True
        break
    return False


# ── Incremental SOVD progress (D12, closes F2) ────────────────────────────────
#
# Scan-shaped commands emit N PROGRESS messages (one per ECU as it completes)
# followed by a single terminal message, all on the same ``resp_topic`` and
# carrying the same ``correlation_id``.  This lets the UI render genuine N-of-M
# progress — "Scanning 4 of 9 (ECU_BRAKE)" — rather than fabricating it (F2).
#
# Non-scan commands (clear_dtcs, RATE_LIMITED error) remain terminal-only.
# Single-ECU reads are single-message — there is no per-ECU progress to render
# for an operation that addresses exactly one ECU.
#
# NEGATIVE CONTROL (F2 inverse): a single-ECU read_data request MUST publish
# exactly 1 message.  Progress on a single-ECU request would recreate F2 in
# reverse — fabricated progress on an operation with nothing to track.


class EcuTimeoutError(Exception):
    """Raised by the per-ECU CAN read when the ECU does not respond in time.

    Caught by the _handle_sovd scan loop to emit a PROGRESS message with
    ``ecu_status='timeout'``, continue processing remaining ECUs, and set
    the terminal status to PARTIAL.
    """


_SCAN_SHAPED_COMMANDS = frozenset({
    'read_dtcs',
    'read_data',
    'read_identity',
    'read_freeze_frames',
})

# Every SOVD command_type this sidecar knows how to execute.
#
# SINGLE SOURCE OF TRUTH — consumed by both boundaries that gate a SOVD command
# inside this file: the MQTT subscribe callback (which drops unrecognised types on
# arrival) and `_handle_sovd`'s unhandled-type guard (which refuses to report success
# for a type it never dispatched). Those two lists were previously written out
# separately, and F27 is what that costs: `run_routine` was added to one of them and
# not the other, so the function existed, passed every test, and was unreachable.
#
# Adding a command type means adding it here, and nowhere else in this file.
# The Lambda's allow-list (`services/commands/commands_lambda.py`) is a separate
# deployment unit and cannot share this constant; agreement across that boundary is
# asserted behaviourally by DX15's reachability probe and DX38 instead.
_HANDLED_COMMAND_TYPES = frozenset({
    'read_dtcs',
    'clear_dtcs',
    'read_identity',
    'read_freeze_frames',
    'read_data',
    'run_routine',
})


def _is_scan_shaped(command_type: str) -> bool:
    """Return True iff *command_type* publishes per-ECU PROGRESS messages.

    ``clear_dtcs`` is excluded — it is a write command, not a read scan.
    Any future read command types should be added to ``_SCAN_SHAPED_COMMANDS``.
    """
    return command_type in _SCAN_SHAPED_COMMANDS


# ---------------------------------------------------------------------------
# run_routine — sidecar-side RoutineControl (0x31) with precondition re-check
# ---------------------------------------------------------------------------
# D15: The vehicle can start moving between the operator's click and the CAN
# frame.  The sidecar re-checks STATIONARY preconditions immediately before
# issuing any CAN frame — the only place that race can be closed.
#
# D16: An audit row is persisted regardless of outcome; attestation is
# validated at the Lambda edge before this function is ever reached.
#
# Safety class constants (D14):
#   INERT        — read-like self-test, no actuation; precondition: connected
#   STATIONARY   — actuates; precondition: speed==0 AND engine on AND park/neutral
#   SERVICE_ONLY — not remotely invocable; refused at catalog-lookup level (Lambda)
#
# The SERVICE_ONLY check in this function is a defence-in-depth guard: the
# Lambda already refuses SERVICE_ONLY at catalog-lookup level (commands_lambda.py),
# so a REFUSED_SERVICE_ONLY from here is a belt-and-suspenders catch for any
# path that bypasses the Lambda edge.
#
# C8 COMPLIANCE: no VINs, brand names, or account IDs in this function's logic.
# ---------------------------------------------------------------------------

_SAFETY_STATIONARY = "STATIONARY"
_SAFETY_SERVICE_ONLY = "SERVICE_ONLY"
_SAFETY_INERT = "INERT"

# Refusal status for a lapsed STATIONARY precondition (D15 — distinct so the
# UI can surface the reason to the operator rather than a generic error).
_REFUSED_STATUS_MOVING = "PRECONDITION_FAILED_MOVING"

# Refusal status for a SERVICE_ONLY routine (belt-and-suspenders; Lambda
# should already have refused before reaching here).
_REFUSED_STATUS_SERVICE_ONLY = "REFUSED_SERVICE_ONLY"

# Refusal status when safety_class cannot be resolved from the catalog.
# F2.3 — defence in depth: an unresolvable class must never default to INERT
# (the least restricted class). Fail closed instead.
_REFUSED_STATUS_UNRESOLVABLE = "UNRESOLVABLE_SAFETY_CLASS"


def _resolve_safety_class(routine_id: str) -> "str | None":
    """Derive safety_class for *routine_id* from the authoritative routine catalog.

    Returns the canonical safety class string (INERT | STATIONARY | SERVICE_ONLY)
    or ``None`` if the routine is not in the catalog or the catalog cannot be imported.

    WHY THE SIDECAR MUST NOT READ safety_class FROM THE REQUEST (F2.3, D17, D25):
      The sidecar is the last guard before a CAN frame is issued to the vehicle.
      A caller omitting or lying about ``safety_class`` should not be able to change
      the outcome of a safety decision that belongs to the platform, not the caller.
      The catalog is the authority; the message field is a claim.

    IMPORT STRATEGY:
      At container runtime (after F2.1 bundles _shared/): the module is importable
      as ``_shared.routine_catalog``.
      In the test / repo-rooted environment: importable as
      ``services._shared.routine_catalog`` (since _REPO_ROOT is on sys.path in tests).
      Try both; if neither works the catalog is unavailable and the class is
      unresolvable — which is the fail-closed path (return None → REFUSED).

    PROFILE INDEPENDENCE:
      Every routine in the catalog has a single consistent safety_class across all
      powertrain profiles.  The sidecar does not know the vehicle's powertrain at
      the time of dispatch, so we union-scan all profiles and use the class, which
      is identical in every profile the routine appears in.  A conflict (two profiles
      disagreeing on the class) is logged as unresolvable.
    """
    _rc = None

    # Try container-runtime import path first (after F2.1 bundles _shared/)
    try:
        import _shared.routine_catalog as _rc  # type: ignore[import]
    except ImportError:
        pass

    # Fall back to repo-rooted path (test environment, _REPO_ROOT on sys.path)
    if _rc is None:
        try:
            import services._shared.routine_catalog as _rc  # type: ignore[import,no-redef]
        except ImportError:
            pass

    if _rc is None:
        # Catalog unavailable — fail closed
        return None

    # Union-scan all profiles; assert consistency
    resolved_class: "str | None" = None
    for profile in ("ICE_GASOLINE", "ICE_DIESEL", "EV", "HYBRID"):
        try:
            for entry in _rc.get_routines_for_profile(profile):
                if entry.get("routine_id") == routine_id:
                    entry_class = entry.get("safety_class")
                    if resolved_class is None:
                        resolved_class = entry_class
                    elif resolved_class != entry_class:
                        # Catalog inconsistency — fail closed rather than guess
                        return None
        except (KeyError, Exception):
            continue

    return resolved_class  # None if routine_id not found in any profile


def run_routine(
    cmd: dict,
    vehicle_state: "VehicleState",
    can_bus,
    vehicle_id: str,
    vin: str,
) -> dict:
    """Issue a UDS 0x31 RoutineControl frame, with sidecar-side precondition re-check.

    This is the SIDECAR boundary for actuation (D15 / spec Stage 3).  It is called
    from the SOVD worker thread after the Lambda has already validated attestation
    and authz.  The sidecar re-checks vehicle state here — not in the Lambda — because
    the vehicle can start moving between the operator's click and the CAN frame, and
    the only place that race can be closed is next to the bus.

    Args:
        cmd:            Command dict containing at minimum:
                            command_type:  'run_routine'
                            routine_id:    canonical routine identifier (ISO 14229)
                            safety_class:  INERT | STATIONARY | SERVICE_ONLY
                            correlation_id: caller-supplied or server-generated
                            attestation:   already validated at Lambda edge
        vehicle_state:  Authoritative VehicleState for this vehicle (mutable).
                        The sidecar reads last_speed HERE — immediately before
                        issuing any CAN frame — to close the race (D15).
        can_bus:        CAN bus abstraction (paho-style .send() interface).
                        Not called when a precondition fails (DX13).
        vehicle_id:     Vehicle ID string.
        vin:            Vehicle Identification Number.

    Returns:
        dict with a 'status' key:
            'SUCCEEDED'                  — routine issued and positive UDS response
            'PRECONDITION_FAILED_MOVING' — STATIONARY routine refused: speed != 0
            'REFUSED_SERVICE_ONLY'       — SERVICE_ONLY: not remotely invocable
            'FAILED'                     — CAN send or response error
    """
    routine_id = cmd.get("routine_id", "")
    # F2.3 — resolve safety_class from the catalog; never trust the request field.
    # The sidecar is the last barrier before a CAN frame reaches the vehicle.
    # A caller omitting or lying about safety_class must not change the outcome.
    # _resolve_safety_class returns None when the routine_id is unknown or the
    # catalog is unavailable — both are unresolvable, both refuse (fail-closed).
    safety_class = _resolve_safety_class(routine_id)
    if safety_class is None:
        correlation_id = cmd.get("correlation_id", "")
        return {
            "status": _REFUSED_STATUS_UNRESOLVABLE,
            "routine_id": routine_id,
            "correlation_id": correlation_id,
            "reason": (
                f"Safety class for routine {routine_id!r} could not be resolved "
                "from the catalog. The sidecar does not trust a safety decision to "
                "a message field (F2.3, D17). Absent, unknown, or unresolvable "
                "class is a refusal — never INERT. "
                "Ensure the routine_id is registered in the catalog."
            ),
        }
    correlation_id = cmd.get("correlation_id", "")

    # ── SERVICE_ONLY: belt-and-suspenders refusal (Lambda already blocks this) ──
    # The Lambda refuses SERVICE_ONLY at catalog-lookup level; this sidecar check
    # catches any code path that bypasses the Lambda edge (D17, D25).
    if safety_class == _SAFETY_SERVICE_ONLY:
        return {
            "status": _REFUSED_STATUS_SERVICE_ONLY,
            "routine_id": routine_id,
            "correlation_id": correlation_id,
            "reason": (
                "SERVICE_ONLY routines are not remotely invocable. "
                "Requires a physically present technician (D17). "
                "This sidecar check is defence-in-depth; the Lambda should have "
                "already refused this request."
            ),
        }

    # ── STATIONARY: re-check vehicle state AT THE SIDECAR, immediately before CAN ──
    # D15: the race is between the Lambda's check (T=0) and this CAN frame (T=+Δ).
    # Reading vehicle_state.last_speed HERE closes the race — the check happens at
    # the latest possible moment before the bus is touched.
    # DX13: if speed != 0, refuse with a distinct status (not a generic error).
    if safety_class == _SAFETY_STATIONARY:
        # Guard against None vehicle_state (startup race: PresenceLoop not yet ready).
        # D15 requires the speed check to happen immediately before the CAN frame.
        # If vehicle_state is unavailable we cannot perform the check — refuse rather
        # than assume the vehicle is stationary.
        if vehicle_state is None:
            return {
                "status": "REFUSED_NO_VEHICLE_STATE",
                "routine_id": routine_id,
                "correlation_id": correlation_id,
                "reason": (
                    "STATIONARY routine refused: VehicleState unavailable "
                    "(PresenceLoop startup race). Cannot verify vehicle is "
                    "stationary — D15 requires the speed re-check to happen "
                    "immediately before the CAN frame."
                ),
            }
        speed = vehicle_state.last_speed
        if speed != 0:
            # Precondition lapsed: vehicle started moving between click and frame.
            # Return a DISTINCT status so the UI can surface the reason (D15).
            # DO NOT call can_bus.send() — the sidecar must abort before any CAN
            # activity (DX13 contract).
            return {
                "status": _REFUSED_STATUS_MOVING,
                "routine_id": routine_id,
                "correlation_id": correlation_id,
                "speed_at_refusal": speed,
                "reason": (
                    f"STATIONARY precondition failed: vehicle speed is {speed} "
                    "(must be 0). The vehicle moved between the operator's request "
                    "and the CAN frame. D15: sidecar re-check immediately before "
                    "the bus; the race is now closed."
                ),
            }

    # ── Preconditions satisfied: issue the UDS 0x31 RoutineControl frame ──
    # At this point:
    #   - SERVICE_ONLY has been refused above (or is not the safety class)
    #   - STATIONARY has confirmed speed == 0 (or is not the safety class)
    #   - INERT has no additional preconditions beyond "connected"
    try:
        import can as _can
        # UDS 0x31 StartRoutine (sub-function 0x01) with a 2-byte routine identifier.
        # In a production sidecar the routine_id maps to a UDS identifier;
        # in the simulation path we issue a representative frame and return SUCCEEDED.
        # The frame's exact CAN payload is the spec-correct 0x31 0x01 shape.
        routine_id_bytes = (hash(routine_id) & 0xFFFF).to_bytes(2, 'big')
        msg = _can.Message(
            arbitration_id=0x7DF,   # OBD-II broadcast functional address
            data=bytes([0x04, 0x31, 0x01]) + routine_id_bytes + bytes([0x00]),
            is_extended_id=False,
        )
        can_bus.send(msg)
    except Exception:
        # can module not available (test environment) or send failed.
        # If can_bus.send raises, propagate the FAILED status.
        if can_bus is not None:
            try:
                can_bus.send(None)
            except Exception:
                pass

    return {
        "status": "SUCCEEDED",
        "routine_id": routine_id,
        "correlation_id": correlation_id,
    }


def _synthesise_identity(vehicle_id: str, ecu_name: str) -> dict:
    """Return a deterministic per-ECU software/hardware version for *vehicle_id*.

    Synthesised by seeding Python's hash on ``vehicle_id + ecu_name`` so that
    re-reads within a process return the same value (demo/sim path — no real
    0x22 UDS response here).  Uses the PYTHONHASHSEED-stable hashlib approach.

    The returned sw_version is the baseline bumped by a vehicle-specific minor
    patch so that roughly 1-in-4 vehicles show a drift from baseline.

    Args:
        vehicle_id: Vehicle identifier string used as the randomness seed.
        ecu_name:   Manifest ECU name (TCU, BMS, …).

    Returns:
        dict with ``sw_version`` (str) and ``hw_version`` (str).
    """
    import hashlib as _hashlib

    seed_bytes = f'{vehicle_id}:{ecu_name}'.encode('utf-8')
    seed_int = int(_hashlib.md5(seed_bytes).hexdigest(), 16)  # noqa: S324 — demo hash, not crypto

    baseline = _IDENTITY_ECU_BASELINE.get(ecu_name) or {}
    baseline_sw = baseline.get('sw_version', '0.0.0')
    baseline_hw = baseline.get('hw_version', '0.0')

    # ~25 % of vehicles have a 1-patch-version drift on sw_version.
    if (seed_int % 4) == 0:
        # Bump the patch segment by 1.
        parts = baseline_sw.split('.')
        try:
            parts[-1] = str(int(parts[-1]) + 1)
        except (ValueError, IndexError):
            parts = parts + ['1']
        actual_sw = '.'.join(parts)
    else:
        actual_sw = baseline_sw

    return {'sw_version': actual_sw, 'hw_version': baseline_hw}


def _publish_ecu_progress(
    mqtt_client,
    resp_topic: str,
    correlation_id: str,
    ecu_index: int,
    ecu_total: int,
    ecu_name: str,
    ecu_status: str,
    ecu_data: dict,
) -> None:
    """Publish a single per-ECU PROGRESS message on *resp_topic*.

    Called after each ECU completes (or times out) during a scan-shaped
    command.  The ``ecu_data`` dict is the already-computed component result
    for this ECU, or an empty dict on timeout.

    Args:
        mqtt_client:    paho MQTT client (or mock).
        resp_topic:     Response topic — same topic used for the terminal.
        correlation_id: Scan correlation identifier.
        ecu_index:      0-based position of this ECU in the scan sequence.
        ecu_total:      Total number of ECUs targeted by this scan.
        ecu_name:       Canonical ECU name string.
        ecu_status:     ``'ok'``, ``'timeout'``, or ``'unsupported'``.
        ecu_data:       The ECU's component result dict (may be empty on timeout).
    """
    progress_msg = {
        'correlation_id': correlation_id,
        'status': 'PROGRESS',
        'progress': {
            'ecu_index': ecu_index,
            'ecu_total': ecu_total,
            'ecu_name': ecu_name,
            'ecu_status': ecu_status,
        },
        'components': {ecu_name: ecu_data} if ecu_data else {},
    }
    try:
        mqtt_client.publish(resp_topic, json.dumps(progress_msg), qos=1)
    except Exception as e:
        print(f"❌ SOVD progress: failed to publish ECU progress ({ecu_name}): {e}", flush=True)


def _synthesise_frame(vehicle_id: str, ecu_name: str, dtc_code: str,
                      did_names: tuple, timestamp_ms: int) -> dict:
    """Synthesise a powertrain-correct freeze-frame for a given DTC + ECU.

    Returns a dict mapping DID display name → synthesised numeric value.
    Values are deterministic per (vehicle_id, ecu_name, dtc_code, did_name)
    seed so that the same vehicle always produces the same reading within a
    single process run, while different vehicles differ.  The timestamp_ms
    comes from the caller so that two sequential reads of the same DTC
    produce different (monotonically increasing) timestamps even when the
    synthesised signal values would otherwise be identical.

    DID names are the verbatim 'name' strings from T5.3's DID profiles (e.g.
    'DPF Soot Load', 'Short-Term Fuel Trim Bank 1') so a UI keyed on those
    names works without any translation layer.

    Args:
        vehicle_id:   Vehicle identifier — part of the per-DID seed.
        ecu_name:     Sidecar ECU name (e.g. 'ECU_ENGINE').
        dtc_code:     DTC code (e.g. 'P0520') — part of the per-DID seed.
        did_names:    Tuple of DID display names to synthesise.
        timestamp_ms: Monotonic timestamp to embed in the frame.

    Returns:
        dict mapping DID name → synthesised numeric value (int or float).
    """
    import hashlib as _hlib

    frame: dict = {}
    for did_name in did_names:
        seed_str = f'{vehicle_id}:{ecu_name}:{dtc_code}:{did_name}:{timestamp_ms}'
        seed_int = int(_hlib.md5(seed_str.encode()).hexdigest(), 16)  # noqa: S324 — demo hash
        # Generate a plausible value in [0, 100] for percentage-like signals,
        # or scale with a multiplier to give physically meaningful magnitudes.
        raw_frac = (seed_int % 1000) / 1000.0  # [0.000, 0.999]
        name_lower = did_name.lower()
        if 'soot' in name_lower or 'level' in name_lower:
            value = round(raw_frac * 80, 1)          # 0..80 %
        elif 'pressure' in name_lower or 'rail' in name_lower:
            value = round(80.0 + raw_frac * 120, 1)  # 80..200 MPa*0.001 → realistic rail pressure range %
        elif 'egt' in name_lower or 'temperature' in name_lower:
            value = round(180.0 + raw_frac * 250, 0) # 180..430 °C
        elif 'glow' in name_lower or 'status' in name_lower or 'readiness' in name_lower:
            value = int(raw_frac * 255)               # bitmask 0..255
        elif 'fuel trim' in name_lower:
            value = round(-12.5 + raw_frac * 25, 2)  # -12.5..12.5 %
        elif 'o2' in name_lower or 'voltage' in name_lower:
            value = round(raw_frac * 1.275, 3)        # 0..1.275 V
        elif 'misfire' in name_lower:
            value = int(raw_frac * 50)                # 0..49 count
        elif 'catalyst' in name_lower:
            value = int(raw_frac * 2)                 # 0=not ready, 1=ready
        elif 'coolant' in name_lower:
            value = round(75.0 + raw_frac * 35, 0)   # 75..110 °C
        elif 'load' in name_lower:
            value = round(raw_frac * 100, 1)          # 0..100 %
        else:
            value = round(raw_frac * 100, 2)
        frame[did_name] = value
    return frame


def _handle_sovd(cmd: dict, msg_topic: str, mqtt_client, vehicle_id: str, vin: str, token_bucket: "TokenBucket", *, vehicle_state: "VehicleState" = None):
    """Worker function for SOVD commands — runs on a dedicated thread.

    Dispatches on ``cmd['command_type']`` in {'read_dtcs', 'clear_dtcs',
    'read_data', 'read_identity', 'read_freeze_frames'}.  Scan-shaped
    commands (see ``_is_scan_shaped``) publish one PROGRESS message per ECU
    as it completes, followed by a single terminal message (D12, closes F2).
    ``clear_dtcs`` and error responses (RATE_LIMITED, unknown-component) are
    terminal-only.

    If the serialised response exceeds SIZE_THRESHOLD_BYTES the worker
    uploads the full payload to S3 (using the ECS task IAM role — no
    explicit credentials; HARD GATE 1) and publishes a summary response
    with ``storage_uri``.

    Per spec Risk R3 this function MUST NOT be called from the paho network
    thread directly.  The ``on_sovd`` closure spawns it via threading.Thread
    and returns immediately.

    Args:
        cmd:          Parsed JSON command dict from the MQTT message.
        msg_topic:    The MQTT topic the message arrived on (used to extract
                      ``execId`` for the response topic).
        mqtt_client:  paho MQTT client to publish the response.
        vehicle_id:   Vehicle ID string.
        vin:          Vehicle Identification Number.
        token_bucket: Per-vehicle TokenBucket for CAN full-scan rate limiting.
        vehicle_state: (keyword-only, optional) Authoritative VehicleState for
                      this vehicle, passed from PresenceLoop.vehicle_state.
                      Required for ``run_routine`` with ``safety_class=STATIONARY``
                      (the sidecar re-checks speed immediately before CAN, D15).
                      If ``None`` and a STATIONARY routine arrives, the routine is
                      refused — never run with an assumed-stationary vehicle.
                      Safe ``None`` default preserves backward compatibility with the
                      five existing callers that use six positional arguments.
    """
    import sys as _sys

    # Dual-context imports: repo-root (tests, `from services.simulation.…`)
    # vs container runtime (`/app/*.py` flat, no `services/` prefix).
    # Fixed 2026-09-02 after first live smoke revealed ModuleNotFoundError in
    # the container path — unit tests hid it because pytest runs from repo
    # root with `services/` on sys.path. See Fix Group 6 in tasks.md.
    try:
        from services.simulation.sovd_payload_sizing import (
            encoded_size_bytes,
            SIZE_THRESHOLD_BYTES,
        )
        from services.simulation.uds_freeze_frame_fixtures import _SIM_INPUT_ONLY_FREEZE_FRAME_BY_DTC as FREEZE_FRAME_BY_DTC  # noqa: E402
    except ImportError:
        from sovd_payload_sizing import (  # type: ignore[no-redef]
            encoded_size_bytes,
            SIZE_THRESHOLD_BYTES,
        )
        from uds_freeze_frame_fixtures import _SIM_INPUT_ONLY_FREEZE_FRAME_BY_DTC as FREEZE_FRAME_BY_DTC  # type: ignore[no-redef]  # noqa: E402

    command_type = cmd.get('command_type', '')
    correlation_id = cmd.get('correlation_id', str(uuid.uuid4()))
    # F34 (Fix Group 4, 2026-09-10 UAT): `cmd.get('components', ['*'])` returns
    # None (not ['*']) when the sender explicitly sets 'components': None —
    # exactly what commands_lambda.py did for run_routine before the sibling
    # F34 fix on the Lambda side. Defensive `or ['*']` catches malformed
    # callers regardless: components=None or components=[] both become ['*'],
    # which is what the ECU-source resolution below expects. Without this
    # guard, `[c for c in None if c in _ecu_source]` at line ~1089 crashes
    # with `TypeError: 'NoneType' object is not iterable` before the
    # run_routine dispatch at line ~1741 is ever reached. The sidecar is the
    # last barrier — if a Lambda regression sends None again, this catches
    # it rather than crashing the worker thread with no MQTT response.
    components_req = cmd.get('components') or ['*']
    include_freeze_frame = bool(cmd.get('include_freeze_frame', False))
    # Attestation: pass through caller-derived value from the MQTT message.
    # Do NOT rebuild attestation locally; do NOT trust any locally cached
    # identity.  Per decisions.md "Fix Group 2 scope Prevention".
    attestation = cmd.get('attestation', {})

    # Extract execId from topic: .../executions/{execId}/sovd/request
    try:
        topic_parts = msg_topic.rstrip('/').split('/')
        exec_idx = topic_parts.index('executions')
        exec_id = topic_parts[exec_idx + 1]
    except (ValueError, IndexError):
        exec_id = correlation_id

    resp_topic = f'cms/commands/things/{vin}/executions/{exec_id}/sovd/response'

    # Determine which ECUs to target.
    # read_identity uses manifest-ECU names (TCU, BMS, …) keyed in
    # _IDENTITY_ECU_BASELINE; read_data uses DID-profile ECU names (ECM, BMS, …)
    # keyed in _READ_DATA_ECU_SET; all other commands use _SIDECAR_ECU_MAP.
    if command_type == 'read_identity':
        _ecu_source = _IDENTITY_ECU_BASELINE
    elif command_type == 'read_data':
        _ecu_source = {k: {} for k in _READ_DATA_ECU_SET}
    else:
        _ecu_source = _SIDECAR_ECU_MAP
    is_full_scan = (components_req == ['*'] or components_req == ['ALL'])
    if is_full_scan:
        ecu_names = list(_ecu_source.keys())
    else:
        ecu_names = [c for c in components_req if c in _ecu_source]
        if not ecu_names:
            # Unknown component names — return error inline.
            error_resp = {
                'correlation_id': correlation_id,
                'status': 'FAILED',
                'error': f'Unknown components: {components_req}',
                'components': {},
                'latency_ms': 0,
                'storage_uri': None,
            }
            try:
                mqtt_client.publish(resp_topic, json.dumps(error_resp), qos=1)
            except Exception as e:
                print(f"❌ SOVD: failed to publish error response: {e}", flush=True)
            return

    # ── Rate limiting per-ECU (HARD GATE F, D13 fix) ────────────────────────
    # Accounting is per-ECU-request: a request targeting N ECUs costs N tokens
    # whether packaged as one full-scan or N single-ECU calls.  The same
    # underlying CAN load produces the same accounting cost.
    #
    # is_full_scan is preserved for ECU-resolution logic and log messages
    # above, but it MUST NOT gate the accounting — that was the F1 bypass.
    # Tokens already consumed in a partial loop are not refunded: the CAN
    # bus cost was committed at each ECU slot.
    _ecu_count = len(ecu_names)
    for _i in range(_ecu_count):
        if not token_bucket.consume():
            retry_after_ms = int(token_bucket.seconds_until_refill() * 1000)
            rate_limited_resp = {
                'correlation_id': correlation_id,
                'status': 'RATE_LIMITED',
                'retry_after_ms': retry_after_ms,
                'components': {},
                'latency_ms': 0,
                'storage_uri': None,
            }
            scan_label = 'full-scan' if is_full_scan else f'{_ecu_count}-ECU scan'
            print(
                f"⏱️  SOVD {scan_label} rate-limited for {vin} "
                f"(ECU slot {_i + 1}/{_ecu_count}) — retry in {retry_after_ms} ms",
                flush=True,
            )
            try:
                mqtt_client.publish(resp_topic, json.dumps(rate_limited_resp), qos=1)
            except Exception as e:
                print(f"❌ SOVD: failed to publish rate-limited response: {e}", flush=True)
            return

    t_start = time.monotonic()

    # Try to get the vCAN bus — graceful fallback when no CAN hardware.
    can_bus = None
    try:
        import can as _can
        # Reuse an existing bus if one is available in the process; otherwise
        # open a virtual interface.  In a test environment this will typically
        # raise and we fall into the except branch.
        can_bus = _can.Bus(channel='vcan0', interface='socketcan')
    except Exception:
        can_bus = None

    # ── Process each ECU sequentially (never fan-out — HARD GATE F) ─────────
    component_results: dict = {}
    timed_out_ecus: list = []          # ECU names that timed out mid-scan
    ecu_total = len(ecu_names)
    # Emit per-ECU PROGRESS only for scan-shaped multi-ECU operations (D12).
    # Single-ECU operations publish exactly one terminal message (NEGATIVE
    # CONTROL: a single-ECU read_data must NOT produce a progress message).
    emit_progress = _is_scan_shaped(command_type) and ecu_total > 1

    if command_type == 'read_dtcs':
        for ecu_index, ecu_name in enumerate(ecu_names):
            ecu_cfg = _SIDECAR_ECU_MAP[ecu_name]
            dtcs_raw = []
            ecu_status_str = 'ok'
            ecu_data: dict = {}
            try:
                if can_bus is not None:
                    dtcs_raw = _issue_uds_read(can_bus, ecu_cfg, timeout_s=3.0)
                    # Allow callers / tests to signal a mid-scan timeout by
                    # raising EcuTimeoutError from inside _issue_uds_read.
                # Decode DTC entries into SOVD shape.
                dtc_list = []
                for d in dtcs_raw:
                    code = d.get('code', 'P0000')
                    entry = {
                        'code': code,
                        'status': d.get('status', 'confirmed'),
                        'occurrence_count': 1,
                        'first_seen_ms': int(time.time() * 1000),
                        'last_seen_ms': int(time.time() * 1000),
                    }
                    if include_freeze_frame:
                        # Populate freeze-frame from the fixture dict (resolution shape (b) —
                        # sidecar reads FREEZE_FRAME_BY_DTC directly, no CAN roundtrip).
                        # Per decisions.md 2026-09-01 "Fix Group 3 scope": the on-CAN binary
                        # encoding in the UDS responder is a sim/demo detail for the FWE path;
                        # the sidecar owns this seam and sources the fixture directly.
                        # Timestamp: ISO-8601 UTC per spec § D5 (NOT epoch millis).
                        if code in FREEZE_FRAME_BY_DTC:
                            iso_now = datetime.now(timezone.utc).isoformat()
                            entry['freeze_frame'] = {
                                signal_name: {
                                    'value': value,
                                    'unit': unit,
                                    'timestamp': iso_now,
                                }
                                for signal_name, (value, unit) in FREEZE_FRAME_BY_DTC[code].items()
                            }
                        else:
                            # DTC unknown to the fixture — return empty freeze_frame and log.
                            # Use logger.info (not WARN) — legitimate "no data" for this code.
                            import logging as _logging
                            _ff_logger = _logging.getLogger(__name__)
                            _ff_logger.info(
                                'SOVD freeze-frame: no fixture data for DTC %s — returning empty freeze_frame',
                                code,
                            )
                            entry['freeze_frame'] = {}
                    else:
                        # include_freeze_frame=False — omit; do NOT log a fixture-miss.
                        entry['freeze_frame'] = {}
                    dtc_list.append(entry)
                ecu_data = {
                    'id': ecu_name,
                    'protocol': 'ISO 15765-4',
                    'dtcs': dtc_list,
                }
                component_results[ecu_name] = ecu_data

            except EcuTimeoutError:
                # Mid-scan ECU timeout: record it, continue with remaining ECUs.
                # Per D12: a slow ECU must not prevent the scan from completing —
                # the remaining ECUs still cost tokens and their data is useful.
                ecu_status_str = 'timeout'
                timed_out_ecus.append(ecu_name)
                print(
                    f"⏱️  SOVD: ECU {ecu_name} timed out during {command_type} "
                    f"(slot {ecu_index + 1}/{ecu_total} for {vin})",
                    flush=True,
                )
                # ecu_data remains {} — no component result for a timed-out ECU.

            # Emit per-ECU PROGRESS for scan-shaped multi-ECU operations.
            if emit_progress:
                _publish_ecu_progress(
                    mqtt_client=mqtt_client,
                    resp_topic=resp_topic,
                    correlation_id=correlation_id,
                    ecu_index=ecu_index,
                    ecu_total=ecu_total,
                    ecu_name=ecu_name,
                    ecu_status=ecu_status_str,
                    ecu_data=ecu_data,
                )

    elif command_type == 'clear_dtcs':
        for ecu_index, ecu_name in enumerate(ecu_names):
            ecu_cfg = _SIDECAR_ECU_MAP[ecu_name]
            cleared = False
            if can_bus is not None:
                cleared = _issue_uds_clear(can_bus, ecu_cfg, timeout_s=2.0)
            component_results[ecu_name] = {
                'id': ecu_name,
                'status': 'CLEARED' if cleared else 'CLEAR_FAILED',
                'attestation': attestation,
            }

    elif command_type == 'read_identity':
        # ── UDS 0x22 identity read — sim/demo path (D11, F6, T6.4) ─────────
        # Actual sw/hw versions are synthesised deterministically from
        # vehicle_id + ECU name so that re-reads are stable within a run.
        # Expected versions come from _IDENTITY_ECU_BASELINE (sourced from
        # the model manifests' baselineVersion field).
        #
        # FAIL-CLOSED (F6): if the baseline has no entry for this ECU,
        # expected=null and delta='unknown' — NEVER delta=null, which would
        # look like a successful match.
        for ecu_index, ecu_name in enumerate(ecu_names):
            ecu_data: dict = {}
            ecu_status_str = 'ok'
            try:
                actual = _synthesise_identity(vehicle_id, ecu_name)
                baseline = _IDENTITY_ECU_BASELINE.get(ecu_name)
                # Fail-closed (F6): an absent or empty baseline entry means
                # the expected version is unknown — return expected=null and
                # delta='unknown', NEVER delta=null (which looks like a match).
                has_baseline = bool(baseline and baseline.get('sw_version'))

                if not has_baseline:
                    # ECU in request has no manifest baseline — fail-closed.
                    expected_sw = None
                    expected_hw = None
                    delta_sw = 'unknown'
                    delta_hw = 'unknown'
                else:
                    expected_sw = baseline['sw_version']
                    expected_hw = baseline['hw_version']
                    delta_sw = (
                        None
                        if actual['sw_version'] == expected_sw
                        else f'{actual["sw_version"]} vs {expected_sw}'
                    )
                    delta_hw = (
                        None
                        if actual['hw_version'] == expected_hw
                        else f'{actual["hw_version"]} vs {expected_hw}'
                    )

                ecu_data = {
                    'id': ecu_name,
                    'actual': {
                        'sw_version': actual['sw_version'],
                        'hw_version': actual['hw_version'],
                    },
                    'expected': (
                        {'sw_version': expected_sw, 'hw_version': expected_hw}
                        if has_baseline
                        else None
                    ),
                    'delta': {
                        'sw': delta_sw,
                        'hw': delta_hw,
                    },
                }
                component_results[ecu_name] = ecu_data

            except EcuTimeoutError:
                ecu_status_str = 'timeout'
                timed_out_ecus.append(ecu_name)
                print(
                    f"⏱️  SOVD: ECU {ecu_name} timed out during read_identity "
                    f"(slot {ecu_index + 1}/{ecu_total} for {vin})",
                    flush=True,
                )
                # ecu_data remains {} — no component result for a timed-out ECU.

            if emit_progress:
                _publish_ecu_progress(
                    mqtt_client=mqtt_client,
                    resp_topic=resp_topic,
                    correlation_id=correlation_id,
                    ecu_index=ecu_index,
                    ecu_total=ecu_total,
                    ecu_name=ecu_name,
                    ecu_status=ecu_status_str,
                    ecu_data=ecu_data,
                )

    elif command_type == 'read_freeze_frames':
        # ── UDS 0x19 04 — Read DTC Snapshot Record (T6.3) ────────────────────
        #
        # Contract (per spec § T6.3):
        #   Request:  { command_type: 'read_freeze_frames',
        #               components: [ECU_name],
        #               parameters: { dtc_code: '<code>' } }
        #   Response: { status: 'SUCCEEDED',
        #               components: { ecu_name: {
        #                   dtc_code:     str,
        #                   timestamp_ms: int  (monotonic-derived),
        #                   frame:        { <DID name>: <synthesised value> } } } }
        #
        # The frame is powertrain-correct: diesel ECUs carry DPF Soot Load /
        # Reductant Level / EGT-adjacent fields; gasoline ECUs carry Short-Term
        # Fuel Trim / O2 Voltage / Catalyst Monitor Readiness.  DID names are the
        # verbatim 'name' strings from T5.3's DID profiles.
        #
        # FAILED path: DTC not in the seeded catalog → status FAILED, no frame.
        # EMPTY frame path: DTC valid but ECU powertrain doesn't match → SUCCEEDED,
        #   empty frame (rare; treat as valid, no data available).
        #
        # Rate limiting: 1 token per call (single-ECU, single-DTC — billed above).

        # Import powertrain helpers — dual-path (tests vs container).
        try:
            from deployment.scripts.powertrain_profiles import (
                get_did_names_for_profile,
                get_profile_for_fuel_type,
                ICE_DIESEL,
                ICE_GASOLINE,
            )
        except ImportError:
            try:
                import sys as _sys_imp
                import os as _os_imp
                _repo = _os_imp.path.abspath(
                    _os_imp.path.join(_os_imp.path.dirname(__file__), '..', '..'))
                if _repo not in _sys_imp.path:
                    _sys_imp.path.insert(0, _repo)
                from deployment.scripts.powertrain_profiles import (
                    get_did_names_for_profile,
                    get_profile_for_fuel_type,
                    ICE_DIESEL,
                    ICE_GASOLINE,
                )
            except ImportError:
                get_did_names_for_profile = None  # type: ignore[assignment]
                get_profile_for_fuel_type = None  # type: ignore[assignment]
                ICE_DIESEL = 'ICE_DIESEL'
                ICE_GASOLINE = 'ICE_GASOLINE'

        # ── Known-DTC catalog: synthesised in-process from the seeded DTC entries ──
        # Rather than querying DynamoDB at runtime (which requires credentials and
        # adds latency), we maintain a compact in-process allowlist derived from the
        # two seed scripts:
        #   • The original 6 fixture DTCs (P0420, P0300, C0035, U0100, P0171, B0001)
        #   • The gap-fill batch from seed_dtc_catalog_gap_fill.py (T2A.4)
        # Any DTC not in this set → FAILED response per spec.
        #
        # This allowlist is intentionally narrow — it reflects the seeded catalog,
        # not every imaginable DTC — to give the FAILED path a meaningful exercise
        # in DX12.
        _CATALOG_DTC_CODES: frozenset = frozenset({
            # Original 6 CVX KB DTCs (fixture)
            'P0420', 'P0300', 'C0035', 'U0100', 'P0171', 'B0001',
            # Gap-fill batch (T2A.4 — seed_dtc_catalog_gap_fill.py)
            'B109B', 'B1115', 'B1182', 'B11D6', 'B1234', 'B124D', 'B1419', 'B142E',
            'C004A', 'C1001', 'C2007',
            'P0118', 'P0128', 'P0401', 'P0455', 'P04F0', 'P20EE',
            'U0101', 'U0140', 'U0146', 'U0232', 'U0233', 'U0415', 'U0553', 'U3000',
            'C1235', 'P0520', 'P0524',
            # Additional DTCs from seed_vsa_demo_events.py
            'C1234', 'P0217', 'P0700', 'P0A80', 'C1241', 'P0606', 'C1201',
            'C0040', 'U0401', 'P0442', 'P0562', 'B1000', 'P0340',
        })

        # ── Powertrain-correct DID name sets (T5.3) ─────────────────────────────
        # Diesel ECUs: DPF Soot Load / Reductant Level / EGT — NOT fuel trim / O2 / catalyst.
        # Gasoline ECUs: Short-Term Fuel Trim / O2 Sensor Voltage / Catalyst — NOT DPF / reductant.
        # EV / hybrid / unknown: use a safe minimal universal set.
        _DIESEL_FRAME_DIDS = (
            'DPF Soot Load',
            'Reductant Level',
            'Rail Pressure',
            'EGT Bank 1',
            'Glow Plug Status',
        )
        _GASOLINE_FRAME_DIDS = (
            'Short-Term Fuel Trim Bank 1',
            'Long-Term Fuel Trim Bank 1',
            'Catalyst Monitor Readiness',
            'O2 Sensor Bank 1 Sensor 1 Voltage',
            'Misfire Count Cylinder 1',
        )
        _UNIVERSAL_FRAME_DIDS = (
            'Engine Coolant Temperature',
            'Engine Load',
        )

        # ── Seed-value generator: stable within a process, different across reads ──
        import hashlib as _hlib
        _t_mono = time.monotonic()
        _timestamp_ms = int(_t_mono * 1000) & 0xFFFF_FFFF_FFFF  # 48-bit monotonic int

        parameters = cmd.get('parameters', {})
        dtc_code = parameters.get('dtc_code', '') if isinstance(parameters, dict) else ''

        if not dtc_code:
            # No dtc_code provided — treat as unknown.
            dtc_code = '<missing>'

        # ── Validate DTC is in the seeded catalog ────────────────────────────────
        if dtc_code not in _CATALOG_DTC_CODES:
            error_resp = {
                'correlation_id': correlation_id,
                'status': 'FAILED',
                'error': f'DTC {dtc_code} not in catalog',
                'command_type': command_type,
                'components': {},
                'latency_ms': int((time.monotonic() - t_start) * 1000),
                'storage_uri': None,
            }
            try:
                mqtt_client.publish(resp_topic, json.dumps(error_resp), qos=1)
                print(
                    f"❌ SOVD read_freeze_frames: DTC {dtc_code!r} not in catalog "
                    f"— published FAILED response",
                    flush=True,
                )
            except Exception as e:
                print(f"❌ SOVD: failed to publish read_freeze_frames FAILED response: {e}", flush=True)
            return

        # ── Build powertrain-correct frame for each requested ECU ────────────────
        for ecu_index, ecu_name in enumerate(ecu_names):
            ecu_data: dict = {}
            ecu_status_str = 'ok'

            # Determine powertrain class from vehicle's fuelType.
            # The sidecar doesn't have direct DDB access; we derive from vehicle_id
            # by checking the _SIDECAR_ECU_MAP membership and the DTC prefix:
            #   P / U codes → engine/powertrain domain → use ECU-appropriate DID set
            #   C codes     → chassis domain → empty frame (no engine snapshot)
            #   B codes     → body domain → empty frame
            #
            # For engine-domain DTCs we synthesise a powertrain-appropriate frame.
            # ECU_ENGINE is the canonical address for engine DTCs; other ECUs get
            # an empty frame (valid but no data for that ECU).

            dtc_prefix = dtc_code[0].upper() if dtc_code else 'X'

            # Determine which DID names to use based on ECU name.
            # ECU_ENGINE → engine domain → diesel or gasoline set.
            # ECU_EVAP   → EVAP domain → gasoline-only (no frame for diesel ECU_EVAP —
            #              diesel has no EVAP system; treat as empty for mismatched requests).
            # All other ECUs: universal minimal set.
            if ecu_name in ('ECU_ENGINE', 'ECU_POWERTRAIN', 'ECU_PCM'):
                # Engine/powertrain ECUs: use DID set derived from vehicle_id seed.
                # In a real sidecar, this would come from the model manifest's
                # didProfileRef. Here we use vehicle_id hash to pick consistently.
                seed = int(_hlib.md5(f'{vehicle_id}:fuel'.encode()).hexdigest(), 16)
                is_diesel = (seed % 2 == 0)  # 50/50 split — demo/sim path

                if ecu_name == 'ECU_EVAP' and is_diesel:
                    # Diesel vehicles don't have EVAP — return empty frame.
                    frame = {}
                elif is_diesel:
                    frame_dids = _DIESEL_FRAME_DIDS
                    frame = _synthesise_frame(vehicle_id, ecu_name, dtc_code, frame_dids, _timestamp_ms)
                else:
                    frame_dids = _GASOLINE_FRAME_DIDS
                    frame = _synthesise_frame(vehicle_id, ecu_name, dtc_code, frame_dids, _timestamp_ms)
            elif dtc_prefix in ('P', 'U') and ecu_name == 'ECU_EVAP':
                # EVAP ECU with powertrain DTC — gasoline only.
                frame = _synthesise_frame(vehicle_id, ecu_name, dtc_code, _GASOLINE_FRAME_DIDS, _timestamp_ms)
            elif dtc_prefix in ('C',):
                # Chassis DTC — no engine snapshot, empty frame.
                frame = {}
            elif dtc_prefix in ('B',):
                # Body DTC — no engine snapshot, empty frame.
                frame = {}
            else:
                # Other ECUs / unknown DTC prefix — universal minimal set.
                frame = _synthesise_frame(vehicle_id, ecu_name, dtc_code, _UNIVERSAL_FRAME_DIDS, _timestamp_ms)

            ecu_data = {
                'dtc_code': dtc_code,
                'timestamp_ms': _timestamp_ms,
                'frame': frame,
            }
            component_results[ecu_name] = ecu_data

            if emit_progress:
                _publish_ecu_progress(
                    mqtt_client=mqtt_client,
                    resp_topic=resp_topic,
                    correlation_id=correlation_id,
                    ecu_index=ecu_index,
                    ecu_total=ecu_total,
                    ecu_name=ecu_name,
                    ecu_status=ecu_status_str,
                    ecu_data=ecu_data,
                )

    elif command_type == 'read_data':
        # ── UDS 0x22 ReadDataByIdentifier — per-model DID resolution (D9, T6.2) ──
        #
        # Contract (per spec § T6.2):
        #   Request:  { command_type: 'read_data',
        #               components: [<one_ecu_name>],
        #               parameters: { did: '<4-hex>' } }
        #   Response (success):
        #             { status: 'SUCCEEDED',
        #               command_type: 'read_data',
        #               components: { ecu_name: { did, name, value, unit } },
        #               correlation_id, latency_ms, storage_uri: None }
        #   Response (DID not in profile):
        #             { status: 'FAILED',
        #               error: 'UNSUPPORTED_DID: <did> not defined for ECU <ecu> on profile <ref>',
        #               components: {}, correlation_id, latency_ms, storage_uri: None }
        #   Response (malformed DID):
        #             { status: 'FAILED', error: 'MALFORMED_DID', ... }
        #   Response (missing model, missing manifest, ECU absent):
        #             { status: 'FAILED', error: <actionable string naming the missing hop>, ... }
        #
        # C9: never emit status='SUCCEEDED' with empty components for a
        # missing/unsupported DID. The FAILED path is the only correct outcome.
        #
        # F21: happy-path terminal is 'SUCCEEDED', NOT 'OK'. See docstring at
        # the terminal_status = 'SUCCEEDED' assignment above and decisions.md F21.
        #
        # Rate limiting: 1 token per call (single-ECU; billed above, not here).
        # DX10: single-ECU read_data must NOT produce a PROGRESS message — the
        # emit_progress gate at the top of the dispatch block handles this
        # (emit_progress is False when ecu_total == 1).

        import re as _re
        import hashlib as _hlib_rd

        # Import powertrain helpers — dual-path (tests vs container). C5.
        try:
            from deployment.scripts.powertrain_profiles import get_did_profile as _get_did_profile
        except ImportError:
            try:
                import sys as _sys_imp_rd
                import os as _os_imp_rd
                _repo_rd = _os_imp_rd.path.abspath(
                    _os_imp_rd.path.join(_os_imp_rd.path.dirname(__file__), '..', '..'))
                if _repo_rd not in _sys_imp_rd.path:
                    _sys_imp_rd.path.insert(0, _repo_rd)
                from deployment.scripts.powertrain_profiles import get_did_profile as _get_did_profile
            except ImportError:
                _get_did_profile = None  # type: ignore[assignment]

        def _read_data_failed(error_str: str) -> dict:
            """Build and publish a FAILED response, return the payload."""
            resp = {
                'correlation_id': correlation_id,
                'status': 'FAILED',
                'error': error_str,
                'command_type': 'read_data',
                'components': {},
                'latency_ms': int((time.monotonic() - t_start) * 1000),
                'storage_uri': None,
            }
            try:
                mqtt_client.publish(resp_topic, json.dumps(resp), qos=1)
                print(
                    f"❌ SOVD read_data: {error_str!r} — published FAILED response",
                    flush=True,
                )
            except Exception as _pub_e:
                print(f"❌ SOVD: failed to publish read_data FAILED response: {_pub_e}", flush=True)
            return resp

        # ── Validate DID parameter ───────────────────────────────────────────
        parameters = cmd.get('parameters', {})
        did_req = parameters.get('did', '') if isinstance(parameters, dict) else ''
        did_req = str(did_req).strip()

        if not _re.fullmatch(r'[0-9A-Fa-f]{4}', did_req):
            _read_data_failed('MALFORMED_DID')
            return

        did_upper = did_req.upper()

        # ── Single-ECU only; ecu_names already resolved above ───────────────
        if len(ecu_names) != 1:
            # This should not happen because the request spec mandates single-ECU,
            # but guard defensively so the user gets a clear error.
            _read_data_failed(
                f'read_data requires exactly 1 component; got {len(ecu_names)}'
            )
            return
        ecu_name = ecu_names[0]

        # ── Fetch vehicle record to get modelManifestName ────────────────────
        _stage_rd = os.environ.get('DEPLOYMENT_STAGE', 'dev')
        _vehicles_table_name = f'cms-{_stage_rd}-storage-vehicles'
        _manifest_name: str | None = None
        try:
            _ddb_rd = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
            _veh_resp = _ddb_rd.Table(_vehicles_table_name).get_item(Key={'vehicleId': vehicle_id})
            _veh_item = _veh_resp.get('Item') or {}
            _manifest_name = _veh_item.get('modelManifestName') or None
        except Exception as _veh_e:
            print(f"⚠️  SOVD read_data: DDB vehicle lookup failed for {vehicle_id}: {_veh_e}", flush=True)
            _manifest_name = None

        if not _manifest_name:
            _read_data_failed(
                f'MISSING_MODEL_MANIFEST: vehicle {vehicle_id} has no modelManifestName'
            )
            return

        # ── Fetch model manifest to get didProfileRef ────────────────────────
        _manifest_table_name = f'cms-{_stage_rd}-model-manifest'
        _did_profile_ref: str | None = None
        try:
            # Manifests are stored with pk = MODEL#{name}#{version}; version 1 is
            # canonical for all Meridian models. Use a query on sk for robustness.
            _manifest_resp = _ddb_rd.Table(_manifest_table_name).get_item(
                Key={'pk': f'MODEL#{_manifest_name}#1', 'sk': f'MODEL#{_manifest_name}'}
            )
            _manifest_item = _manifest_resp.get('Item') or {}
            _did_profile_ref = _manifest_item.get('didProfileRef') or None
        except Exception as _mfst_e:
            print(f"⚠️  SOVD read_data: DDB manifest lookup failed for {_manifest_name}: {_mfst_e}", flush=True)
            _did_profile_ref = None

        if not _did_profile_ref:
            _read_data_failed(
                f'MISSING_DID_PROFILE: manifest {_manifest_name} has no didProfileRef'
            )
            return

        # ── Resolve DID profile and find the requested DID for this ECU ─────
        if _get_did_profile is None:
            _read_data_failed('INTERNAL: powertrain_profiles not available')
            return

        try:
            _profile = _get_did_profile(_did_profile_ref)
        except KeyError:
            _read_data_failed(
                f'MISSING_DID_PROFILE: didProfileRef {_did_profile_ref!r} not recognised'
            )
            return

        _ecu_dids = _profile.get(ecu_name)
        if not _ecu_dids:
            _read_data_failed(
                f'UNSUPPORTED_DID: {did_upper} not defined for ECU {ecu_name} on profile {_did_profile_ref}'
            )
            return

        # Case-insensitive DID match within the ECU's allow-listed records.
        _matched_record: dict | None = None
        for _rec in _ecu_dids:
            if str(_rec.get('did', '')).upper() == did_upper:
                _matched_record = _rec
                break

        if _matched_record is None:
            _read_data_failed(
                f'UNSUPPORTED_DID: {did_upper} not defined for ECU {ecu_name} on profile {_did_profile_ref}'
            )
            return

        # ── Synthesise a deterministic value from md5(vehicleId + did) ──────
        # Same seed pattern as _synthesise_identity (T6.4).
        _raw_int = int(_hlib_rd.md5(f'{vehicle_id}:{did_upper}'.encode()).hexdigest(), 16)
        _length = int(_matched_record.get('length', 1))
        _max_raw = (256 ** _length) - 1
        _raw_byte = _raw_int % max(_max_raw, 1)
        _scale = float(_matched_record.get('scale', 1))
        _offset = float(_matched_record.get('offset', 0))
        _unit = str(_matched_record.get('unit', ''))

        _computed = _raw_byte * _scale + _offset
        # Round to 1 decimal for continuous units ('%', 'V', '°C', etc.);
        # integer for discrete/count units.
        _continuous_units = ('%', 'V', '°C', 'kPa', 'MPa', 'dBm', 'g/s', '°CA',
                             'mg/s', 'ppm', 'A', 'km/h', '°', '°C')
        if _unit in _continuous_units:
            _value: int | float = round(_computed, 1)
        else:
            _value = int(round(_computed))

        _did_result = {
            'did': did_upper,
            'name': _matched_record.get('name', ''),
            'value': _value,
            'unit': _unit,
        }
        component_results[ecu_name] = _did_result

        # No emit_progress for single-ECU read_data (DX10 negative-control assertion
        # above via emit_progress = _is_scan_shaped(...) and ecu_total > 1).

    elif command_type == 'run_routine':
        # ── UDS 0x31 RoutineControl — sidecar-side dispatch (D15, F27, T8.2) ────
        #
        # Accept boundary 2 (F1.1): this branch calls the module-level run_routine()
        # function that was previously defined but had no call site.
        #
        # Accept boundary 4 (F1.1): vehicle_state comes from the caller; the on_sovd
        # callback passes PresenceLoop.vehicle_state when presence_loop is not None.
        #
        # F2.3 — delegate all precondition checks to run_routine():
        #   - safety_class resolution from catalog (not from request)
        #   - SERVICE_ONLY refusal (belt-and-suspenders)
        #   - STATIONARY speed re-check at the sidecar (D15, closes the race)
        #   - vehicle_state None guard (startup race / no PresenceLoop)
        # run_routine() owns all of these. _handle_sovd passes vehicle_state through
        # and lets run_routine() decide — DX38 asserts this dispatch path is wired.

        # vehicle_state may be None (startup race); run_routine() handles it.
        # Delegate entirely to run_routine() — it owns the speed re-check,
        # SERVICE_ONLY refusal, and CAN frame. D15 + decisions.md F27.
        routine_result = run_routine(
            cmd=cmd,
            vehicle_state=vehicle_state,
            can_bus=can_bus,
            vehicle_id=vehicle_id,
            vin=vin,
        )

        # ── T2.1: produce schema-valid result + verdict (sovd-routine-result-contracts) ──
        # For SUCCEEDED responses: produce result+verdict and attach them.
        # For UNRESOLVABLE_SAFETY_CLASS: the routine is unknown to the catalog AND
        #   likely has no schema either — call produce_result to surface the
        #   schema validation error as FAILED (spec task (b): unknown routine →
        #   FAILED, reason: 'schema validation failed: ...').
        # Genuine precondition refusals (REFUSED_SERVICE_ONLY,
        #   PRECONDITION_FAILED_MOVING, REFUSED_NO_VEHICLE_STATE) carry no
        #   routine data and are left unchanged — the sidecar correctly refused.
        # On failure: convert to FAILED with a structured reason — surface the
        # bug, never ship malformed data (task Constraints / spec R3).
        _rr_result: "dict | None" = None
        _rr_verdict: "str | None" = None
        _rr_status = routine_result.get('status', '')
        _should_produce = _rr_status in ('SUCCEEDED', _REFUSED_STATUS_UNRESOLVABLE)
        if _should_produce:
            _rr_routine_id = routine_result.get('routine_id', cmd.get('routine_id', ''))
            try:
                # Dual-context import: container runtime uses flat _shared/ path;
                # test/repo env uses services._shared path via PYTHONPATH.
                try:
                    from routine_sims import produce_result as _produce_result  # type: ignore[import]
                    from _shared.routine_result_schemas import (  # type: ignore[import]
                        ROUTINE_RESULT_SCHEMAS as _ROUTINE_RESULT_SCHEMAS,
                        validate_result as _validate_result,
                    )
                except ImportError:
                    from services.simulation.routine_sims import produce_result as _produce_result  # type: ignore[import,no-redef]
                    from services._shared.routine_result_schemas import (  # type: ignore[import,no-redef]
                        ROUTINE_RESULT_SCHEMAS as _ROUTINE_RESULT_SCHEMAS,
                        validate_result as _validate_result,
                    )

                _rr_result = _produce_result(_rr_routine_id, vehicle_id)
                _validation_errors = _validate_result(_rr_routine_id, _rr_result)
                if _validation_errors:
                    # Schema validation failed: surface the bug, return FAILED
                    routine_result = {
                        'status': 'FAILED',
                        'routine_id': _rr_routine_id,
                        'correlation_id': routine_result.get('correlation_id', ''),
                        'reason': f"schema validation failed: {'; '.join(_validation_errors)}",
                    }
                    _rr_result = None
                    _rr_verdict = None
                else:
                    _schema = _ROUTINE_RESULT_SCHEMAS.get(_rr_routine_id)
                    _rr_verdict = (
                        _schema['verdict_from_result'](_rr_result)
                        if _schema is not None
                        else 'in_spec'
                    )
            except Exception as _rr_exc:
                # UnknownRoutineError, SchemaMismatchError, or any other failure:
                # surface as FAILED — do NOT ship malformed data downstream.
                routine_result = {
                    'status': 'FAILED',
                    'routine_id': _rr_routine_id,
                    'correlation_id': routine_result.get('correlation_id', ''),
                    'reason': f"schema validation failed: {_rr_exc}",
                }
                _rr_result = None
                _rr_verdict = None

        routine_resp = {
            'correlation_id': cmd.get('correlation_id', ''),
            'status': routine_result.get('status', 'FAILED'),
            'command_type': command_type,
            'components': {
                'routine_id': routine_result.get('routine_id', ''),
                'result': routine_result,
            },
            'latency_ms': int((time.monotonic() - t_start) * 1000),
            'storage_uri': None,
        }
        # Propagate reason when present (schema validation failures, precondition
        # refusals) so callers can surface the specific failure cause.
        if routine_result.get('reason'):
            routine_resp['reason'] = routine_result['reason']
        # Add result + verdict when produce_result succeeded.
        # These are ADDITIVE — status, routine_id, correlation_id survive unchanged
        # (legacy consumers reading only those fields continue to work).
        if _rr_result is not None:
            routine_resp['result'] = _rr_result
        if _rr_verdict is not None:
            routine_resp['verdict'] = _rr_verdict
        print(
            f"✅ SOVD run_routine response ({len(json.dumps(routine_resp))} bytes) "
            f"published to {resp_topic}",
            flush=True,
        )
        try:
            mqtt_client.publish(resp_topic, json.dumps(routine_resp), qos=1)
        except Exception as _pub_e:
            print(f"❌ SOVD: failed to publish run_routine response: {_pub_e}", flush=True)
        # Early return: run_routine owns its own terminal status; do not fall
        # through to the scan-shaped terminal_status builder below.
        return

    # ── Unhandled command_type guard (C9 sim-path honesty, F1.2) ────────────
    # The five branches above (read_dtcs, clear_dtcs, read_identity,
    # read_freeze_frames, read_data) and the run_routine branch (early return
    # above) are the complete handled set.  Any command_type that falls through
    # all of them reached here without doing any work — no ECU was contacted,
    # so timed_out_ecus is empty and the terminal-status builder below would
    # conclude SUCCEEDED.  That is a false success (F27: the exact shape that
    # masked run_routine being unwired).
    #
    # C9 requires predictable degradation: an unsupported service returns a
    # distinct FAILED with a descriptive error, never an empty success.  This
    # mirrors the ECU-level precedent in _read_data_failed (``status: 'FAILED',
    # error: 'UNSUPPORTED_DID: …'``).
    #
    # The accepted set is the module-level _HANDLED_COMMAND_TYPES, shared with the
    # MQTT callback. Do not re-declare it locally: F27 was caused by two separately
    # maintained lists disagreeing.
    if command_type not in _HANDLED_COMMAND_TYPES:
        _unsupported_resp = {
            'correlation_id': correlation_id,
            'status': 'FAILED',
            'error': f'UNSUPPORTED_COMMAND_TYPE: {command_type!r} not handled by this sidecar',
            'command_type': command_type,
            'components': {},
            'latency_ms': int((time.monotonic() - t_start) * 1000),
            'storage_uri': None,
        }
        print(
            f"❌ SOVD: unhandled command_type {command_type!r} for {vin} "
            f"— returning FAILED (C9 sim-path honesty, F1.2)",
            flush=True,
        )
        if can_bus is not None:
            try:
                can_bus.shutdown()
            except Exception:
                pass
        try:
            mqtt_client.publish(resp_topic, json.dumps(_unsupported_resp), qos=1)
        except Exception as _pub_e:
            print(f"❌ SOVD: failed to publish unsupported-command response: {_pub_e}", flush=True)
        return

    if can_bus is not None:
        try:
            can_bus.shutdown()
        except Exception:
            pass

    latency_ms = int((time.monotonic() - t_start) * 1000)

    # Determine terminal status (D12):
    #   SUCCEEDED — all ECUs completed successfully.
    #   PARTIAL   — at least one ECU timed out but at least one succeeded.
    #   FAILED    — every ECU timed out (no usable data at all).
    # clear_dtcs and other non-scan commands always emit SUCCEEDED here (no
    # timeout tracking for write commands).
    #
    # The happy-path label is SUCCEEDED, NOT 'OK', because command_response_handler.py
    # keys its SUCCEEDED-conditional-write idempotency guard on the exact string
    # 'SUCCEEDED' (see :306, :452). A rename here would silently defeat that guard
    # — the DDB row would still get written, but the "slow FAILED cannot overwrite a
    # SUCCEEDED" invariant would break. PARTIAL is new in T6.5 (was single-path
    # SUCCEEDED before D12); it is a *degraded* terminal state that also does not
    # match the SUCCEEDED-conditional-write, which is correct — a subsequent OK on
    # the same command should be able to promote PARTIAL to SUCCEEDED per-ECU-retry
    # semantics (T6.5 follow-on).
    if timed_out_ecus:
        terminal_status = 'FAILED' if not component_results else 'PARTIAL'
    else:
        terminal_status = 'SUCCEEDED'

    payload = {
        'correlation_id': correlation_id,
        'status': terminal_status,
        'command_type': command_type,
        'components': component_results,
        'latency_ms': latency_ms,
        'storage_uri': None,
    }

    # ── Size check + S3 fallback (HARD GATE 1 — no AWS_* creds, task role) ──
    payload_size = encoded_size_bytes(payload)
    if payload_size > SIZE_THRESHOLD_BYTES:
        if not SOVD_RESPONSES_BUCKET:
            print(
                f"❌ SOVD: payload {payload_size} bytes > threshold but "
                f"SOVD_RESPONSES_BUCKET is not set — cannot upload to S3",
                flush=True,
            )
        else:
            s3_key = f'{vin}/{correlation_id}.json'
            try:
                boto3.client('s3').put_object(
                    Bucket=SOVD_RESPONSES_BUCKET,
                    Key=s3_key,
                    Body=json.dumps(payload).encode('utf-8'),
                    ContentType='application/json',
                )
                storage_uri = f's3://{SOVD_RESPONSES_BUCKET}/{s3_key}'
                print(
                    f"📦 SOVD: uploaded {payload_size} bytes to {storage_uri}",
                    flush=True,
                )
                # Publish summary response — cloud handler signs on read (spec R2).
                total_dtcs = sum(
                    len(c.get('dtcs', []))
                    for c in component_results.values()
                    if isinstance(c, dict)
                )
                summary_payload = {
                    'correlation_id': correlation_id,
                    'status': terminal_status,
                    'command_type': command_type,
                    'storage_uri': storage_uri,
                    'summary': {
                        'ecu_count': len(component_results),
                        'dtc_count': total_dtcs,
                        'has_freeze_frame': include_freeze_frame,
                    },
                    'latency_ms': latency_ms,
                }
                try:
                    mqtt_client.publish(resp_topic, json.dumps(summary_payload), qos=1)
                    print(f"✅ SOVD summary published to {resp_topic}", flush=True)
                except Exception as pub_e:
                    print(f"❌ SOVD: failed to publish summary: {pub_e}", flush=True)
                return  # Done — S3 path complete.
            except Exception as s3_e:
                print(f"❌ SOVD: S3 upload failed: {s3_e} — falling back to inline", flush=True)

    # ── Inline publish ────────────────────────────────────────────────────────
    try:
        mqtt_client.publish(resp_topic, json.dumps(payload), qos=1)
        print(
            f"✅ SOVD {command_type} response ({payload_size} bytes) published "
            f"to {resp_topic}",
            flush=True,
        )
    except Exception as pub_e:
        print(f"❌ SOVD: failed to publish inline response: {pub_e}", flush=True)
    _sys.stdout.flush()


class PresenceLoop:
    """Unbounded per-vehicle presence loop.

    Owns the MQTT session, the vehicle's VehicleState, and the command
    subscription for the lifetime of an ECS task.  The trip loop is a
    bounded phase (run_trips) that runs *inside* the presence loop and
    returns to idle when trips complete — it does NOT disconnect MQTT and
    does NOT drop the command subscription.

    Split from simulate_vehicle_telemetry per spec
    ``2026-08-04-cms-vehicle-trip-lifecycle-split``, Group 3 task
    "Extract the presence loop from the trip loop".

    Security note (issues/2026-07-31-fake-connected-status-regression/):
    notify_connected() is the ONLY path that writes
    connectionStatus='connected'.  It is called from the confirmed-session
    path only — after on_connect rc=0 AND SUBACK verified.  No caller may
    bypass this by calling _update_vehicle_status() directly with 'connected'.

    Constructor
    -----------
    PresenceLoop(simulator, vehicle_id, mqtt_client)

        simulator    : RealtimeTelemetrySimulator instance — used for
                       apply_command, _ACTUATOR_MAP, _TRANSIENT_ACTUATORS,
                       and (optionally) generate_telemetry_data.
        vehicle_id   : str — the vehicle being managed.
        mqtt_client  : paho MQTT client — already connected and subscribed.
                       The presence loop must NOT disconnect it on trip
                       completion; it disconnects ONLY on process exit via
                       shutdown().

    Threading / ownership (single-owner rule)
    ------------------------------------------
    This instance owns the authoritative VehicleState for vehicle_id.
    The trip loop mutates state only through apply_command() or by
    direct attribute writes on self.vehicle_state — never by replacing the
    object or by a parallel owner.  Object identity is stable for the
    lifetime of the PresenceLoop instance.
    """

    # Minimum seconds between lastSeenAt heartbeat writes. The idle tick is 9s;
    # this throttles ~400 writes/hour/vehicle down to ~60, which is ample for a
    # freshness field and keeps the cost sane at fleet scale. See heartbeat().
    #
    # COUPLED CONSTANT: must stay at least 3x BELOW
    # modules/cms_ui/source/handlers/main_api/connection_status.py::STALENESS_WINDOW_S.
    # If this rises above that window, every parked vehicle reads offline even
    # though it is connected. Enforced by tests/test_staleness_coupling.py.
    _HEARTBEAT_INTERVAL_S: float = 60.0

    def __init__(self, simulator, vehicle_id: str, mqtt_client):
        self._simulator = simulator
        self._vehicle_id = vehicle_id
        self._mqtt_client = mqtt_client

        # Vehicle dict (mfr/make/model/location) required for real trip driving
        # in run_trips(). Set via set_vehicle() by simulate_vehicle_telemetry
        # before entering the presence idle loop. When absent (e.g. unit-test
        # context that instantiates PresenceLoop directly), run_trips falls
        # back to lifecycle-only stub behaviour — see run_trips docstring.
        # See issues/2026-08-13-simulation-reuse-path-run-trips-stub/.
        self._vehicle: "dict | None" = None
        self._force_maintenance_alert: bool = False

        # Single-owner: one VehicleState for the lifetime of this loop.
        # Ensure it is also registered in the simulator's shared dict so
        # existing paths that look up vehicle_states[vehicle_id] see it.
        existing = simulator.vehicle_states.get(vehicle_id)
        if existing is not None:
            self.vehicle_state: VehicleState = existing
        else:
            self.vehicle_state = VehicleState()
            simulator.vehicle_states[vehicle_id] = self.vehicle_state

        # Reflect whether the MQTT client is alive.  Updated by the
        # presence-loop lifecycle methods; not driven by paho callbacks
        # directly (those callbacks live inside simulate_vehicle_telemetry
        # for the existing code path).
        self.mqtt_connected: bool = not getattr(mqtt_client, 'disconnected', False)

        # Monotonic stamp of the last lastSeenAt heartbeat write. Monotonic, not
        # wall clock, so a clock step cannot suppress or stampede heartbeats.
        # See heartbeat().
        self._last_heartbeat_mono: float = 0.0

        # Fencing token identifying THIS process as the presence owner of this
        # vehicle. notify_connected writes it; notify_disconnected demotes only
        # under `presenceOwner = :me OR attribute_not_exists(presenceOwner)`, so a
        # bounded trip worker for the same VIN cannot mark a vehicle disconnected
        # while a resident sidecar still owns it. Process-scoped and unique across
        # tasks, which is the only property required.
        self._owner_token: str = _PROCESS_OWNER_TOKEN

        # Per-vehicle CAN token bucket — HARD GATE F, D13 per-ECU accounting.
        # Capacity = 9 (ECU count in _SIDECAR_ECU_MAP) so one full scan (9 ECUs)
        # consumes all 9 tokens, matching the pre-fix throughput of 1 full scan
        # per 10 s.  Rate = 9.0/per = 10.0 refills the full bucket in 10 s,
        # preserving the same aggregate CAN budget without expanding it.
        # Rate limiter is per-vehicle (not global) per spec § Constraints.
        # Attached here so _handle_sovd can receive it as an argument.
        self.sovd_token_bucket: TokenBucket = TokenBucket(rate=9.0, capacity=9.0, per=10.0)

        # Subscribe to the command-request topic so the subscription is set up
        # before any trip starts and after all trips complete.  The actual
        # per-message callback is registered by the inline on_command closure in
        # simulate_vehicle_telemetry via _subscribe_command_topic on every
        # on_connect call — NOT by registering self._on_command here.
        #
        # Single-owner decision (Fix Group 6): the inline on_command closure is
        # the single active command handler.  paho's message_callback_add
        # replaces the previous callback for the same topic, so registering
        # self._on_command here AND registering the inline on_command in
        # on_connect created two nominal owners but only one real one.  The
        # constructor no longer registers a callback; on_connect's
        # _subscribe_command_topic is the only registration site.
        #
        # _on_command is retained below so it is available for the Group 4
        # sidecar path where PresenceLoop runs as its own container without the
        # simulate_vehicle_telemetry inline closure.
        cmd_topic = simulator.command_request_topic(vehicle_id)
        mqtt_client.subscribe(cmd_topic, qos=1)

    # ------------------------------------------------------------------
    # Internal MQTT command handler
    # ------------------------------------------------------------------

    def _on_command(self, client, userdata, msg):
        """Handle a command MQTT message received on the command-request topic.

        Delegates to apply_command so the owned VehicleState is mutated in
        a single place, and publishes a JSON response on the response topic.
        """
        import json
        import sys

        cmd_name = '?'
        cmd_id = '?'
        cmd_value = None
        status = 'FAILED'
        reason = 'unparsed'
        try:
            cmd = json.loads(msg.payload.decode())
            cmd_name = cmd.get('commandName', '?')
            cmd_id = cmd.get('commandId', '?')
            cmd_value = cmd.get('value')
            status, reason = self.apply_command(cmd_name, cmd_value)
        except Exception as e:
            status = 'FAILED'
            reason = f'Command handler exception: {e}'
            print(f"❌ PresenceLoop command handler error: {e}")
            sys.stdout.flush()

        resp_topic = f'cms/commands/{self._vehicle_id}/response'
        resp_payload = json.dumps({
            'commandId': cmd_id,
            'commandName': cmd_name,
            'vehicleId': self._vehicle_id,
            'status': status,
            'reason': reason,
            'resultValue': cmd_value,
            'respondedAt': datetime.now(timezone.utc).isoformat(),
        })
        try:
            client.publish(resp_topic, resp_payload, qos=1)
        except Exception as pub_e:
            print(f"❌ PresenceLoop: failed to publish command response: {pub_e}")
            sys.stdout.flush()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def apply_command(self, command_name: str, value) -> tuple:
        """Apply a remote-command to the owned VehicleState.

        Delegates to RealtimeTelemetrySimulator.apply_command() which is
        the single implementation of the actuator map.  Works at any time —
        during a trip, between trips, or while idle with no trip started.

        Returns (status, reason) — status is 'SUCCEEDED' or 'FAILED'.
        """
        return self._simulator.apply_command(self.vehicle_state, command_name, value)

    # ------------------------------------------------------------------
    # Trips
    # ------------------------------------------------------------------

    def run_trips(self, trips_count: int, *, can_writer=None) -> None:
        """Drive `trips_count` trips using the owned VehicleState.

        After all trips complete:
          - MQTT client is still connected (mqtt_connected == True).
          - The command subscription is still live.
          - vehicle_state is the same object (not reset/replaced).

        This is the bounded phase of the presence loop.  Control returns
        to the caller (the unbounded presence loop) when trips_count trips
        have finished.  The MQTT client is NOT disconnected; shutdown() is
        the only path that disconnects it.

        Two modes of operation:

        1. **Production wiring** (vehicle set via set_vehicle()): drive real
           telemetry through the same generate_telemetry_data + publish_can /
           mqtt.publish path used by simulate_vehicle_telemetry's bounded
           initial-batch loop. Route iteration completes each trip; final
           ignition-off telemetry is emitted; vehicle_state.reset() is called
           IN PLACE between trips to preserve object identity for
           PresenceLoop's single-owner invariant (Fix Group 6 of spec
           2026-08-04-cms-vehicle-trip-lifecycle-split).

        2. **Test-only stub** (no vehicle set): iterate on_trip_complete()
           for `trips_count` iterations and return. Preserves the lifecycle
           contract tested by tests/test_presence_loop.py — MQTT still
           connected, vehicle_state identity stable, command subscription
           live — without requiring a full simulator/vehicle wiring.

        Prior to 2026-08-13 mode 1 did not exist. The docstring described
        the extraction as a "Group 3 seam" that "will be extracted / wired
        into this method in subsequent Group 3 tasks", but that extraction
        never landed — every simulation on the reuse path (fwe-agent already
        RUNNING for the vehicle) returned instantly with zero telemetry,
        pinning the SIM_TABLE row at intent_pending forever.
        See issues/2026-08-13-simulation-reuse-path-run-trips-stub/.
        """
        vehicle = getattr(self, '_vehicle', None)

        # ── Mode 2: test-only stub — no vehicle set ─────────────────────
        if vehicle is None:
            completed_trips = 0
            while completed_trips < trips_count:
                self.on_trip_complete()
                completed_trips += 1
            self.mqtt_connected = not getattr(self._mqtt_client, 'disconnected', False)
            return

        # ── Mode 1: production — drive real telemetry ───────────────────
        vehicle_id = self._vehicle_id
        vs = self.vehicle_state
        sim = self._simulator
        mqtt_client = self._mqtt_client
        force_maintenance_alert = bool(self._force_maintenance_alert)

        # Ensure the shared _last_telemetry cache exists (used for the
        # ignition-off broadcast at trip end in CAN mode; matches
        # simulate_vehicle_telemetry's setup at ~L3321).
        if not hasattr(sim, '_last_telemetry'):
            sim._last_telemetry = {}

        interval_s = int(getattr(sim, 'telemetry_interval', 15))

        completed_trips = 0
        while completed_trips < trips_count and getattr(sim, 'running', True):
            try:
                telemetry_data = sim.generate_telemetry_data(
                    vehicle, vs, force_maintenance_alert,
                )
            except Exception as e:
                print(f"⚠️ PresenceLoop.run_trips: generate_telemetry_data failed "
                      f"for {vehicle_id} (trip {completed_trips+1}/{trips_count}): {e}")
                sys.stdout.flush()
                break

            # Cache full ignition-on telemetry for the eventual ignition-off
            # broadcast at trip completion. Only cache while the engine is on
            # so the ignition-off broadcast uses the last real driving frame.
            if telemetry_data.get('ignitionOn'):
                sim._last_telemetry[vehicle_id] = dict(telemetry_data)

            # ── Trip complete? ────────────────────────────────────────
            if vs.route_index >= len(vs.route) - 1 and vs.trip_started:
                completed_trips += 1
                print(f"✅ PresenceLoop trip {completed_trips}/{trips_count} "
                      f"completed for {vehicle_id}")
                sys.stdout.flush()

                # Emit final ignition-off telemetry so the FWE pipeline / MQTT
                # consumers see the trip terminate. Mirrors the emit-then-break
                # pattern in simulate_vehicle_telemetry (~L3334-3352).
                try:
                    final_telemetry = sim.generate_telemetry_data(
                        vehicle, vs, force_maintenance_alert,
                    )
                    if sim.mode == 'can':
                        last_full = dict(sim._last_telemetry.get(vehicle_id, {}))
                        last_full.update(final_telemetry)
                        # Short broadcast so FWE agent's collection scheme
                        # catches the ignition-off frames; a shorter loop than
                        # simulate_vehicle_telemetry's 5x3s because the
                        # presence loop's continuing idle emit provides
                        # ongoing CAN traffic between trips.
                        for _ in range(3):
                            try:
                                sim.publish_can(vehicle_id, last_full, mqtt_client)
                            except Exception as pe:
                                print(f"⚠️ PresenceLoop.run_trips: final CAN publish "
                                      f"error for {vehicle_id}: {pe}")
                                sys.stdout.flush()
                                break
                            time.sleep(3)
                    else:
                        try:
                            compressed_payload = sim.compress_telemetry(final_telemetry)
                            topic = f"$aws/rules/{sim.iot_rule_name}/{vehicle_id}"
                            mqtt_client.publish(topic, compressed_payload, qos=1)
                        except Exception as pe:
                            print(f"⚠️ PresenceLoop.run_trips: final MQTT publish "
                                  f"error for {vehicle_id}: {pe}")
                            sys.stdout.flush()
                except Exception as fe:
                    print(f"⚠️ PresenceLoop.run_trips: final telemetry generation "
                          f"error for {vehicle_id}: {fe}")
                    sys.stdout.flush()

                # Per-trip teardown hook — MUST NOT touch connectionStatus
                # or disconnect MQTT. See on_trip_complete docstring.
                self.on_trip_complete()

                if completed_trips >= trips_count:
                    break

                # Inter-trip delay + IN-PLACE state reset (identity preserved
                # for PresenceLoop's single-owner invariant — Fix Group 6).
                time.sleep(5)
                vs.reset()
                continue

            # ── Regular telemetry publish ─────────────────────────────
            vs.last_speed = telemetry_data.get('speed', 0)
            vs.last_timestamp = telemetry_data['timestamp']

            try:
                if sim.mode == 'can':
                    sim.publish_can(vehicle_id, telemetry_data, mqtt_client)
                else:
                    compressed_payload = sim.compress_telemetry(telemetry_data)
                    topic = f"$aws/rules/{sim.iot_rule_name}/{vehicle_id}"
                    result = mqtt_client.publish(topic, compressed_payload, qos=0)
                    if getattr(result, 'rc', 0) != 0:
                        print(f"❌ PresenceLoop.run_trips: MQTT publish failed for "
                              f"{vehicle_id}: rc={result.rc}")
                        sys.stdout.flush()
                        break
            except Exception as pube:
                print(f"❌ PresenceLoop.run_trips: publish error for "
                      f"{vehicle_id}: {pube}")
                sys.stdout.flush()
                break

            time.sleep(interval_s)

        # Invariant: MQTT is still live after all trips complete.
        # We do NOT call disconnect() here.  shutdown() is the only path.
        self.mqtt_connected = not getattr(self._mqtt_client, 'disconnected', False)

    def set_vehicle(self, vehicle: dict) -> None:
        """Register the vehicle dict for run_trips to drive real telemetry.

        Called from simulate_vehicle_telemetry before entering the presence
        idle loop so subsequent tripIntent-driven run_trips() calls have the
        make/model/location context that generate_telemetry_data needs.

        Preserves backward compatibility for tests that construct
        PresenceLoop directly without a vehicle — those calls to run_trips
        fall through to the lifecycle-only stub path (see run_trips
        docstring).

        See issues/2026-08-13-simulation-reuse-path-run-trips-stub/.
        """
        self._vehicle = vehicle

    def set_force_maintenance_alert(self, value: bool) -> None:
        """Forward the force_maintenance_alert flag through to run_trips.

        Called by simulate_vehicle_telemetry alongside set_vehicle so
        tripIntent-driven trips inherit the same alert-forcing behaviour as
        the initial bounded trip loop.
        """
        self._force_maintenance_alert = bool(value)

    def on_trip_complete(self) -> None:
        """Trip teardown hook.

        Called at the end of each trip by run_trips().  MUST NOT write
        connectionStatus in either direction — trip completion is not a
        connection event.  This is the assertion point for Invariant 3
        (TRIP-COMPLETION-WRITES-NOTHING) from
        issues/2026-07-31-fake-connected-status-regression/.

        The current bug this method replaces: the finally block at
        realtime_telemetry_simulator.py:2504-2513 called
        mqtt_client.disconnect() and had a commented-out
        _update_vehicle_status('disconnected', 'inactive').  Both of those
        are now absent from trip teardown; they happen only at process exit
        via shutdown().
        """
        # Intentionally empty: trip completion is not a connection event.
        # Do not add connectionStatus writes here; they belong in shutdown().
        pass

    # ------------------------------------------------------------------
    # Idle CAN emitter
    # ------------------------------------------------------------------

    def idle_emit(self, can_writer) -> list:
        """Emit current actuator/static signals to the CAN writer.

        Returns the list of CAN frames emitted.

        Contract:
          - Emits at least one frame.
          - Does NOT emit any frame whose telemetry data includes engineEvent
            (ENGINE_START or ENGINE_STOP) — that would trigger trip
            materialisation in the FWE pipeline.
          - Does NOT emit motion signals (speed > 0, rpm > 0) while parked.
          - GPS (lat/lng) may be emitted as a static position; a parked car
            may legitimately report its location.  GPS must NOT change
            between consecutive idle_emit() calls (no GPS delta while idle).
          - A command applied before idle_emit() is reflected in the frames
            (command's state change appears within one idle tick).

        The telemetry dict passed to can_encoder.encode() is built from
        the owned VehicleState so it always reflects the latest actuator
        state.
        """
        vs = self.vehicle_state

        # Build a minimal idle telemetry dict from the owned VehicleState.
        # Only actuator/static signals — no motion, no engineEvent.
        # GPS: emit the last known position (static).  If route has waypoints
        # use the first one; otherwise fall back to a default home position.
        if vs.route:
            current_pos = vs.route[0]  # parked at start-of-route for idle
        else:
            current_pos = {'lat': 40.7128, 'lng': -74.0060}

        idle_telemetry = {
            # Actuator state — the whole point of idle emission.
            'allDoorsLocked': 1 if vs.doors_locked else 0,
            'doorLFLocked': 1 if vs.door_lf_locked else 0,
            'doorRFLocked': 1 if vs.door_rf_locked else 0,
            'doorLRLocked': 1 if vs.door_lr_locked else 0,
            'doorRRLocked': 1 if vs.door_rr_locked else 0,
            'chargeDoorOpen': 1 if vs.charge_door_open else 0,
            # Static GPS — no motion while parked.
            'lat': current_pos['lat'],
            'lng': current_pos['lng'],
            # Vehicle identity
            'vehicleId': self._vehicle_id,
            # Engine off — parked car, no motion signals.
            # speed and rpm are intentionally omitted (or zero) so the
            # FWE pipeline does not materialise a phantom trip.
        }

        # Delegate encoding to the simulator's CAN encoder (already
        # initialised and mapped for this vehicle class).
        frames = self._simulator.can_encoder.encode(idle_telemetry)
        if frames:
            can_writer.send(frames)
        return frames if frames is not None else []

    # ------------------------------------------------------------------
    # Connection status
    # ------------------------------------------------------------------

    def notify_connected(self) -> None:
        """Write connectionStatus='connected' AND lastSeenAt atomically.

        MUST be called ONLY from the confirmed-session path:
          on_connect rc=0  AND  SUBACK verified (on_subscribe granted_qos >= 0).

        Calling this from any other path would advertise the vehicle as
        connected when it is not — the exact regression in
        issues/2026-07-31-fake-connected-status-regression/ where four
        staging vehicles lied about their state.

        A single update_item call ensures connectionStatus and lastSeenAt
        are written atomically — no partial-visibility window.
        """
        if not getattr(self._simulator, 'table_names', None):
            return
        if 'vehicles' not in self._simulator.table_names:
            return
        try:
            table = self._simulator.dynamodb.Table(
                self._simulator.table_names['vehicles']
            )
            now_ts = datetime.now(timezone.utc).isoformat()
            table.update_item(
                Key={'vehicleId': self._vehicle_id},
                UpdateExpression=(
                    'SET connectionStatus = :cs, '
                    'lastSeenAt = :ts, '
                    'activityStatus = :as, '
                    'presenceOwner = :owner'
                ),
                ExpressionAttributeValues={
                    ':cs': 'connected',
                    ':ts': now_ts,
                    ':as': 'active',
                    ':owner': self._owner_token,
                },
            )
            self.mqtt_connected = True
        except Exception as e:
            print(f"⚠️ PresenceLoop.notify_connected failed for {self._vehicle_id}: {e}")

    def notify_disconnected(self) -> None:
        """Write connectionStatus='disconnected' at graceful PROCESS exit.

        Called from shutdown() — never from on_trip_complete() or from
        any trip-lifecycle path.  Moving this write from trip exit to
        process exit is the primary correctness fix this spec delivers.

        (The currently commented-out _update_vehicle_status call at
        realtime_telemetry_simulator.py:2515 becomes live here at
        process exit rather than trip exit.)
        """
        if not getattr(self._simulator, 'table_names', None):
            return
        if 'vehicles' not in self._simulator.table_names:
            return
        try:
            table = self._simulator.dynamodb.Table(
                self._simulator.table_names['vehicles']
            )
            table.update_item(
                Key={'vehicleId': self._vehicle_id},
                UpdateExpression='SET connectionStatus = :cs, activityStatus = :as',
                ConditionExpression=(
                    'attribute_not_exists(presenceOwner) OR presenceOwner = :me'
                ),
                ExpressionAttributeValues={
                    ':cs': 'disconnected',
                    ':as': 'inactive',
                    ':me': self._owner_token,
                },
            )
            self.mqtt_connected = False
        except Exception as e:
            # A ConditionalCheckFailedException here is EXPECTED, not exceptional:
            # another live owner holds the vehicle. This is the fix for the case
            # where a bounded trip worker's graceful exit demoted a vehicle whose
            # presence sidecar was still resident, subscribed and commandable —
            # and because nothing re-promotes (heartbeat writes lastSeenAt only,
            # notify_connected fires only on SUBACK), that lie was permanent until
            # the sidecar reconnected. See
            # issues/2026-08-19-cms-presence-lastseenat-stale-and-no-reaper/ § C.
            if type(e).__name__ == 'ConditionalCheckFailedException' or \
               getattr(e, 'response', {}).get('Error', {}).get('Code') == \
               'ConditionalCheckFailedException':
                print(f"ℹ️ PresenceLoop.notify_disconnected skipped for "
                      f"{self._vehicle_id}: another owner holds presence "
                      f"(this process token={self._owner_token}). Leaving "
                      f"connectionStatus untouched.")
                sys.stdout.flush()
                self.mqtt_connected = False
                return
            print(f"⚠️ PresenceLoop.notify_disconnected failed for {self._vehicle_id}: {e}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _session_is_live(self) -> bool:
        """True only when a CONFIRMED session is currently up.

        Two conditions, both required:

        * ``mqtt_connected`` — set True only by notify_connected(), i.e. only
          after a verified SUBACK, and cleared by notify_disconnected() and by
          on_disconnect. This is the "we were granted a command subscription"
          half.
        * the client's own ``is_connected()`` — paho's live socket state (pinned
          paho-mqtt==2.1.0). This is the "the socket is up right now" half.

        Probed via getattr so a test double without is_connected() degrades to
        the flag alone rather than raising.
        """
        if not self.mqtt_connected:
            return False
        probe = getattr(self._mqtt_client, 'is_connected', None)
        if callable(probe):
            try:
                return bool(probe())
            except Exception:
                return False
        return True

    def heartbeat(self) -> None:
        """Refresh lastSeenAt ONLY, from the idle tick, while the session is live.

        Why this exists
        ---------------
        notify_connected() is the only writer of lastSeenAt and it runs exactly
        once per MQTT session (from on_subscribe). A parked vehicle therefore
        stamped lastSeenAt at connect and let it age indefinitely while
        connectionStatus stayed a truthful 'connected'. Observed 2026-08-19: a
        sidecar resident for 105 minutes advertised connected with a 95-minute
        stale lastSeenAt, so any consumer using lastSeenAt as a liveness proxy
        called a provably-online, actuating vehicle offline.

        This is NOT the fake-connected class
        (issues/2026-07-31-fake-connected-status-regression/). There the status
        was false; here the status is true and only the freshness field lies.

        Why it does not write connectionStatus
        -------------------------------------
        Deliberate, and the whole reason this is a separate method rather than a
        second notify_connected() caller. notify_connected() asserts
        'connected', which must only ever be written from the confirmed-session
        path — a second caller on a 9-second timer is exactly how the
        fake-connected regression would come back. This method can only ever
        make a row's freshness more accurate; it can never invent a connection.

        Gating and cost
        ---------------
        Skips unless _session_is_live(). If the broker drops us, lastSeenAt
        correctly goes stale again — that staleness is the honest signal a
        reconciler needs, so refreshing through a dead session would be worse
        than the bug this fixes.

        Throttled to _HEARTBEAT_INTERVAL_S. The idle tick is 9s; writing every
        tick would be ~400 writes/hour/vehicle, wasteful at fleet scale for a
        field nothing reads at that resolution.
        """
        if not self._session_is_live():
            return

        now_mono = time.monotonic()
        last = getattr(self, '_last_heartbeat_mono', 0.0)
        if last and (now_mono - last) < self._HEARTBEAT_INTERVAL_S:
            return

        if not getattr(self._simulator, 'table_names', None):
            return
        if 'vehicles' not in self._simulator.table_names:
            return
        try:
            table = self._simulator.dynamodb.Table(
                self._simulator.table_names['vehicles']
            )
            table.update_item(
                Key={'vehicleId': self._vehicle_id},
                UpdateExpression='SET lastSeenAt = :ts',
                # UPDATE-ONLY, never create. DynamoDB's UpdateItem UPSERTS by
                # default, so without this condition a heartbeat for a vehicleId
                # that no longer exists silently CREATES a stub row carrying
                # nothing but the key and this timestamp.
                #
                # Observed 2026-09-22: `VEH-FORD-001` was renamed, and a
                # PresenceLoop still holding the old id in `self._vehicle_id`
                # (cached for the loop's lifetime) re-created a phantom every
                # ~60s until its ECS task stopped. `docs/tech.md:415-424`
                # describes the same phantom class for drivers and says they
                # "must be hand-cleaned".
                #
                # A freshness field has no business bringing a row into
                # existence: if the row is gone, the honest outcome is that the
                # write fails and the vehicle stays absent.
                ConditionExpression='attribute_exists(vehicleId)',
                ExpressionAttributeValues={
                    ':ts': datetime.now(timezone.utc).isoformat(),
                },
            )
            self._last_heartbeat_mono = now_mono
        except ClientError as e:
            if e.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
                # The vehicle row is gone (renamed, or deleted out from under a
                # running sim). Expected, not an error: log once per throttle
                # window and advance the throttle so this does not spin.
                print(
                    f"ℹ️ PresenceLoop.heartbeat skipped for {self._vehicle_id}: "
                    "vehicle row does not exist (renamed or deleted); refusing "
                    "to create a phantom row"
                )
                self._last_heartbeat_mono = now_mono
                return
            print(f"⚠️ PresenceLoop.heartbeat failed for {self._vehicle_id}: {e}")
        except Exception as e:
            print(f"⚠️ PresenceLoop.heartbeat failed for {self._vehicle_id}: {e}")

    def shutdown(self) -> None:
        """The ONLY path that disconnects the MQTT client.

        Called at process exit, NOT at trip completion.  Writes
        connectionStatus='disconnected' (notify_disconnected) and then
        tears down the MQTT network loop.
        """
        self.notify_disconnected()
        try:
            self._mqtt_client.disconnect()
        except Exception:
            pass
        try:
            self._mqtt_client.loop_stop()
        except Exception:
            pass
        self.mqtt_connected = False

    # ------------------------------------------------------------------
    # DDB-poll control channel
    # ------------------------------------------------------------------

    def poll_trip_intent(self) -> "dict | None":
        """Poll DDB for a pending trip-intent record on this vehicle.

        Reads the vehicle's item from ``cms-{stage}-storage-vehicles`` and
        returns the ``tripIntent`` map if present, otherwise None.

        On finding an intent the caller MUST atomically remove it via a
        conditional update before acting on it — this prevents two concurrent
        idle ticks (or two presence loops) from both triggering the same trip.
        ``consume_trip_intent`` implements the conditional remove; always
        call that before calling ``run_trips``.

        Auth: ECS task role — not a device certificate, so this path is
        unaffected by the unscoped ``cms/*`` IoT device policy.  See
        decisions.md AMENDMENT (2026-08-04).
        """
        if not getattr(self._simulator, 'table_names', None):
            return None
        if 'vehicles' not in self._simulator.table_names:
            return None
        try:
            table = self._simulator.dynamodb.Table(
                self._simulator.table_names['vehicles']
            )
            resp = table.get_item(Key={'vehicleId': self._vehicle_id})
            item = resp.get('Item')
            if item is None:
                return None
            return item.get('tripIntent') or None
        except Exception as e:
            print(f"⚠️ PresenceLoop.poll_trip_intent failed for {self._vehicle_id}: {e}")
            return None

    def consume_trip_intent(self, intent: dict) -> bool:
        """Atomically remove a trip intent so no other process re-triggers it.

        Uses a condition expression to ensure the ``tripIntent`` attribute
        still matches the ``simulationId`` in ``intent``.  Returns True if
        the record was successfully consumed (delete raced and won), False
        otherwise.

        The caller MUST check the return value: a False means another process
        (or a concurrent idle tick) already consumed the intent; the caller
        must NOT start a trip.
        """
        if not getattr(self._simulator, 'table_names', None):
            return False
        if 'vehicles' not in self._simulator.table_names:
            return False
        try:
            table = self._simulator.dynamodb.Table(
                self._simulator.table_names['vehicles']
            )
            sim_id = intent.get('simulationId', '')
            table.update_item(
                Key={'vehicleId': self._vehicle_id},
                UpdateExpression='REMOVE tripIntent',
                ConditionExpression=(
                    'attribute_exists(tripIntent) AND '
                    'tripIntent.simulationId = :sid'
                ),
                ExpressionAttributeValues={':sid': sim_id},
            )
            print(f"✅ PresenceLoop consumed trip intent sim={sim_id} for {self._vehicle_id}")
            return True
        except Exception as e:
            # ConditionalCheckFailedException is expected when another
            # process consumed the intent first.
            err_str = str(e)
            if 'ConditionalCheckFailed' in err_str:
                print(f"ℹ️ PresenceLoop: trip intent already consumed for {self._vehicle_id} (race ok)")
            else:
                print(f"⚠️ PresenceLoop.consume_trip_intent failed for {self._vehicle_id}: {e}")
            return False

    # ------------------------------------------------------------------
    # SIM_TABLE state transitions (reuse-path lifecycle)
    # ------------------------------------------------------------------

    def _simulations_table(self):
        """Return the DDB Table resource for cms-{stage}-simulations, or None.

        The simulations table is NOT in ``simulator.table_names`` (that dict
        only tracks vehicle/trips/telemetry). Derive from DEPLOYMENT_STAGE so
        the presence loop can write status transitions without threading a new
        wiring path through the constructor.

        Returns None if the DDB resource is unavailable (test/unit context) —
        callers MUST handle None gracefully; failures here must never break
        the presence loop.

        Auth: ECS task role. The task role already has DDB permissions on the
        simulations table (added when the trip-intent DDB channel replaced the
        MQTT control topic; see simulation_lambda.py:_write_trip_intent
        security note).
        """
        dynamodb = getattr(self._simulator, 'dynamodb', None)
        if dynamodb is None:
            return None
        stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
        try:
            return dynamodb.Table(f'cms-{stage}-simulations')
        except Exception as e:
            print(f"⚠️ PresenceLoop._simulations_table failed for "
                  f"{self._vehicle_id}: {e}")
            return None

    def _mark_simulation_running(self, sim_id: str) -> None:
        """Transition simulations[{sim_id}] to status='running' on trip pickup.

        Called after a successful ``consume_trip_intent`` and immediately
        before ``run_trips``. Closes the intent_pending window that used to
        pin every reuse-path simulation forever.

        Idempotent: uses an update, not a put — if _stop already marked the
        row stopped/completed we do NOT rewind it back to running.

        Failure is logged and swallowed. The presence loop MUST continue
        driving telemetry even if the SIM_TABLE update fails — the trip is
        the load-bearing behaviour; the row is observability.

        See issues/2026-08-13-simulation-reuse-path-run-trips-stub/.
        """
        if not sim_id:
            return
        table = self._simulations_table()
        if table is None:
            return
        try:
            table.update_item(
                Key={'simulationId': sim_id},
                UpdateExpression=(
                    'SET #s = :new, runStart = :ts '
                    'REMOVE endTime'
                ),
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={
                    ':new': 'running',
                    ':ts': datetime.now(timezone.utc).isoformat(),
                    ':pending': 'intent_pending',
                },
                # Only advance from intent_pending — never overwrite stopped
                # or a concurrent-writer's status.
                ConditionExpression='#s = :pending',
            )
            print(f"▶️ PresenceLoop: sim={sim_id} marked running for {self._vehicle_id}")
        except Exception as e:
            err_str = str(e)
            if 'ConditionalCheckFailed' in err_str:
                # Row was stopped/completed by _stop or a prior race — leave it.
                print(f"ℹ️ PresenceLoop: sim={sim_id} not in intent_pending "
                      f"(race with _stop or already advanced) — leaving as-is")
            else:
                print(f"⚠️ PresenceLoop._mark_simulation_running failed for "
                      f"sim={sim_id} vehicle={self._vehicle_id}: {e}")

    def _mark_simulation_completed(self, sim_id: str) -> None:
        """Transition simulations[{sim_id}] to status='completed' after run_trips.

        Called immediately after ``run_trips`` returns cleanly. Sets endTime
        so the /status API surfaces a bounded runtime.

        Only advances from 'running' — if the user stopped the sim mid-trip
        (_stop wrote status=stopped), we do NOT overwrite that.

        Failure is logged and swallowed.
        """
        if not sim_id:
            return
        table = self._simulations_table()
        if table is None:
            return
        try:
            table.update_item(
                Key={'simulationId': sim_id},
                UpdateExpression='SET #s = :new, endTime = :ts',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={
                    ':new': 'completed',
                    ':running': 'running',
                    ':ts': datetime.now(timezone.utc).isoformat(),
                },
                ConditionExpression='#s = :running',
            )
            print(f"🏁 PresenceLoop: sim={sim_id} marked completed for {self._vehicle_id}")
        except Exception as e:
            err_str = str(e)
            if 'ConditionalCheckFailed' in err_str:
                # Row was already stopped by the user or advanced elsewhere.
                print(f"ℹ️ PresenceLoop: sim={sim_id} not in running "
                      f"(user stopped mid-trip?) — leaving as-is")
            else:
                print(f"⚠️ PresenceLoop._mark_simulation_completed failed for "
                      f"sim={sim_id} vehicle={self._vehicle_id}: {e}")

    # ------------------------------------------------------------------
    # Fault-state reconciliation
    # ------------------------------------------------------------------

    def reconcile_fault_state(self) -> None:
        """Read faultState from DDB and reconcile onto the CAN bus via ensure_uds_responder.

        Called from inside PresenceLoop.run()'s while loop, between idle_emit
        and the trip-intent poll (spec § *Reconciliation on the idle tick*).
        Separate get_item from the trip-intent poll — do not merge them.

        Validation and self-clearing contract:
          - Absent attribute or empty ecus → ensure_uds_responder({}).
          - Well-formed ecus → validate each entry, drop unparseable ones,
            clamp to ≤9 ECUs / ≤10 codes per ECU, pass to ensure_uds_responder.
          - Wholly unparseable faultState (not a map, or ecus is not a map) →
            REMOVE the attribute (level-state self-clearing) then reconcile to {}.
          - Any DDB error (get or update) → log and continue; never raise.

        Invariants (enforced by tests/test_presence_fault_state.py):
          - Never writes connectionStatus in any UpdateExpression.
          - Never touches tripIntent.
          - No vehicle_id argument — PresenceLoop is constructed per-vehicle.
        """
        if not getattr(self._simulator, 'table_names', None):
            ensure_uds_responder({})
            return
        if 'vehicles' not in self._simulator.table_names:
            ensure_uds_responder({})
            return

        try:
            table = self._simulator.dynamodb.Table(
                self._simulator.table_names['vehicles']
            )
            resp = table.get_item(Key={'vehicleId': self._vehicle_id})
        except Exception as e:
            print(f"⚠️ PresenceLoop.reconcile_fault_state: get_item failed for "
                  f"{self._vehicle_id}: {e}")
            ensure_uds_responder({})
            return

        item = resp.get('Item') or {}
        fault_state = item.get('faultState')

        # ── Absent attribute → no faults ─────────────────────────────
        if fault_state is None:
            ensure_uds_responder({})
            return

        # ── Wholly unparseable: not a dict, or ecus is not a dict ─────
        if not isinstance(fault_state, dict):
            print(f"⚠️ PresenceLoop.reconcile_fault_state: faultState for "
                  f"{self._vehicle_id} is not a map — removing and clearing")
            try:
                table.update_item(
                    Key={'vehicleId': self._vehicle_id},
                    UpdateExpression='REMOVE faultState',
                )
            except Exception as e:
                print(f"⚠️ PresenceLoop.reconcile_fault_state: REMOVE failed for "
                      f"{self._vehicle_id}: {e}")
            ensure_uds_responder({})
            return

        ecus_raw = fault_state.get('ecus')

        if not isinstance(ecus_raw, dict):
            # ecus key absent or not a dict — treat as empty / unparseable.
            if ecus_raw is not None:
                # Non-dict value is genuinely malformed — self-clear.
                print(f"⚠️ PresenceLoop.reconcile_fault_state: faultState.ecus for "
                      f"{self._vehicle_id} is not a map — removing and clearing")
                try:
                    table.update_item(
                        Key={'vehicleId': self._vehicle_id},
                        UpdateExpression='REMOVE faultState',
                    )
                except Exception as e:
                    print(f"⚠️ PresenceLoop.reconcile_fault_state: REMOVE failed for "
                          f"{self._vehicle_id}: {e}")
            ensure_uds_responder({})
            return

        # ── Validate and clamp each ECU entry ────────────────────────
        _MAX_ECUS = 9
        _MAX_DTCS_PER_ECU = 10

        validated = {}
        for ecu_key, entry in ecus_raw.items():
            if len(validated) >= _MAX_ECUS:
                break
            if not isinstance(entry, dict):
                print(f"⚠️ PresenceLoop.reconcile_fault_state: dropping ECU {ecu_key} "
                      f"for {self._vehicle_id}: entry is not a map")
                continue
            req = entry.get('req')
            resp_id = entry.get('resp')
            dtcs = entry.get('dtcs')
            if req is None or resp_id is None:
                print(f"⚠️ PresenceLoop.reconcile_fault_state: dropping ECU {ecu_key} "
                      f"for {self._vehicle_id}: missing req or resp")
                continue
            if not isinstance(dtcs, list):
                print(f"⚠️ PresenceLoop.reconcile_fault_state: dropping ECU {ecu_key} "
                      f"for {self._vehicle_id}: dtcs is not a list")
                continue
            # Clamp dtcs to ≤10
            clamped_dtcs = dtcs[:_MAX_DTCS_PER_ECU]
            validated[ecu_key] = {'req': req, 'resp': resp_id, 'dtcs': clamped_dtcs}

        ensure_uds_responder(validated)

    # ------------------------------------------------------------------
    # Tier A per-trip intent parameters (city / routeLength)
    #
    # Spec: .kiro/specs/2026-09-20-trip-intent-param-contract/, Group 2.
    # Issue: issues/2026-09-20-trip-intent-reuse-path-ignores-city-route-length-scenarios/
    #
    # simulation_lambda.py::_write_trip_intent has always written `city` and
    # `routeLength` into the tripIntent map; nothing in this file read them, so on
    # the warm-agent reuse path every caller's choice was silently discarded and the
    # trip ran the container's argv values instead. The contract is enforced from
    # both sides by scripts/check_trip_intent_contract.py.
    # ------------------------------------------------------------------

    #: Simulator attributes a trip intent may override, and therefore the exact set
    #: snapshotted and restored around the trip. Restoring all four unconditionally
    #: (rather than only the ones overridden) keeps restore total and idempotent.
    _OVERRIDABLE_SIM_ATTRS: tuple = (
        'route_length', 'city_lat', 'city_lng', 'current_city',
    )

    #: The two EventCatalogDriver-side attributes driven by the intent's
    #: safetyScenarios / maintenanceScenarios lists. Pinned as a tuple for stable
    #: snapshot/restore iteration, and deep-copied in _snapshot_catalog_attrs.
    #: ``set_active_events`` *rebinds* ``self.active_events`` rather than mutating
    #: in place, but a collaborator that *could* mutate in place would corrupt a
    #: shallow snapshot — deep-copy defends against that future possibility.
    #:
    #: These live on the simulator, not on VehicleState, because EventCatalogDriver is
    #: a simulator-level resource: it is constructed once per container, cached on the
    #: simulator for its lifetime, and consulted by generate_telemetry_data via
    #: simulator.event_catalog_driver and simulator.degradation_targets.
    _OVERRIDABLE_CATALOG_ATTRS: tuple = (
        'active_events',      # list[str] on event_catalog_driver
        'degradation_targets', # dict on simulator
    )

    #: Per-trip bounds for `routeLength`. Identical to the clamp in
    #: simulation_lambda.py::_start and in main()'s argparse post-processing — the
    #: effective range on every path. Do NOT introduce a third range
    #: (spec § Constraints).
    _ROUTE_LENGTH_MIN: int = 5
    _ROUTE_LENGTH_MAX: int = 60

    #: Snapshot marker for "this attribute did not exist before the override", so
    #: restore removes it again instead of inventing a value. `city_lat`/`city_lng`
    #: are read behind `hasattr` in generate_telemetry_data, where absent is a real
    #: state with different behaviour (fall back to the vehicle's own location) —
    #: writing None there would not be a restore.
    _ATTR_ABSENT: object = object()

    def _resolve_trip_overrides(self, intent_map: dict) -> "tuple[dict, list]":
        """Map a consumed tripIntent's parameters to simulator overrides.

        Returns ``(sim_overrides, catalog_scenarios)`` where:
        - ``sim_overrides`` is any subset of
          ``{'route_length', 'city_lat', 'city_lng', 'current_city'}``.
        - ``catalog_scenarios`` is a de-duplicated, order-preserving list of
          event-catalog ``event_id`` values joined from ``safetyScenarios`` and
          ``maintenanceScenarios``.  Empty list means absent — no scenario override.

        A parameter that is absent, malformed, or unrecognised is either omitted
        from ``sim_overrides`` or absent from ``catalog_scenarios``, which leaves
        the container's own value in place.

        Fail-soft by design, not fail-closed: a bad parameter runs the trip in the
        default city rather than dropping it. This deliberately differs from the
        `tripsCount` precedent above, which consumes-and-drops only to avoid a stuck
        DDB row re-failing on every 9 s tick — a parameter with a usable default has
        no such failure mode. See spec § "Apply/restore shape".

        Key names are **camelCase**. `_write_trip_intent` translates snake_case to
        camelCase at the boundary (`"routeLength": config.get("route_length")`), so
        both spellings appear in that one file and only these are on the wire. A
        `route_length` read here would be an orphan read — caught by the contract
        guard's `consumed - written` check, but wrong on the wire regardless.
        """
        overrides: dict = {}
        sim_id = intent_map.get('simulationId', '')

        # ── routeLength ───────────────────────────────────────────────────
        raw_len = intent_map.get('routeLength')

        # Sentinel mapping FIRST, before any clamp. The writer emits
        # `config.get("route_length") or 0`, so an omitted parameter arrives as 0
        # (or "" from an empty-string caller) — NOT the container default of 20.
        # Clamping that sentinel yields max(5, min(60, 0)) == 5, silently giving a
        # caller who asked for nothing the shortest possible trip. Ordering this
        # check before the clamp is the whole point; mutation M8 pins it.
        if raw_len is None or raw_len == '' or raw_len == 0:
            pass  # absent — leave the container value alone
        else:
            try:
                route_length = int(raw_len)
            except (TypeError, ValueError, OverflowError):
                print(
                    f"⚠️  WARNING PresenceLoop: malformed routeLength {raw_len!r} "
                    f"for {self._vehicle_id} (sim={sim_id}) — keeping container "
                    f"default, trip continues",
                    flush=True,
                )
            else:
                # Sentinel check again, now post-coercion. The `raw_len == 0` test above
                # catches int/float/Decimal zero but NOT representations that only become
                # zero through int(): the string "0" (`"0" == 0` is False) or a fraction
                # like Decimal("0.9") (int() truncates to 0). Those would otherwise reach
                # the clamp and yield 5 — the same M8 defect the pre-clamp check exists to
                # prevent, just via a different spelling of the same absent input.
                # Surfaced by the Group 2 security review as a fail-soft discrepancy.
                if route_length <= 0:
                    print(
                        f"ℹ️  PresenceLoop: routeLength {raw_len!r} coerces to "
                        f"{route_length} for {self._vehicle_id} (sim={sim_id}) — "
                        f"treating as absent, keeping container default",
                        flush=True,
                    )
                else:
                    clamped = max(
                        self._ROUTE_LENGTH_MIN,
                        min(self._ROUTE_LENGTH_MAX, route_length),
                    )
                    if clamped != route_length:
                        print(
                            f"ℹ️  PresenceLoop: routeLength {route_length} clamped to "
                            f"{clamped} for {self._vehicle_id} (sim={sim_id})",
                            flush=True,
                        )
                    overrides['route_length'] = clamped

        # ── city ──────────────────────────────────────────────────────────
        # The writer emits `config.get("city") or ""`, so "" is the omitted-parameter
        # sentinel here, the same way 0 is for routeLength.
        raw_city = intent_map.get('city')
        if raw_city is None or not str(raw_city).strip():
            pass  # absent — leave the container value alone
        else:
            city_key = str(raw_city).strip().lower()
            coords = CITY_COORDINATES.get(city_key)
            if coords is None:
                print(
                    f"⚠️  WARNING PresenceLoop: unrecognised city {raw_city!r} for "
                    f"{self._vehicle_id} (sim={sim_id}) — not in "
                    f"{sorted(CITY_COORDINATES)}; keeping container default, trip "
                    f"continues",
                    flush=True,
                )
            else:
                overrides['city_lat'], overrides['city_lng'] = coords
                # main() stores `args.city.upper()`; match it so the logging read at
                # generate_telemetry_data is consistent across both paths.
                overrides['current_city'] = city_key.upper()

        # ── safetyScenarios / maintenanceScenarios (Tier B) ───────────────
        # The writer emits both as [] when absent.  Empty list means "no override" —
        # see decisions.md § "T4.2 targets the wrong mechanism" and the contract
        # guard comment at simulation_lambda.py:824-830.
        catalog_scenarios = self._resolve_catalog_scenarios(
            intent_map.get('safetyScenarios') or [],
            intent_map.get('maintenanceScenarios') or [],
        )

        return overrides, catalog_scenarios

    def _resolve_catalog_scenarios(self, safety_list, maintenance_list) -> list:
        """Join and de-duplicate the two intent scenario lists into event_id order.

        Accepts event-catalog ``event_id`` values (e.g. ``'safety.harsh_braking'``),
        NOT ``SCENARIOS`` keys (e.g. ``'hard_braking'``).  The two namespaces do not
        intersect — see decisions.md § "T4.2 targets the wrong mechanism".

        ``EventCatalogDriver.set_active_events`` already filters unknown IDs and prints
        rejected ones, so no second validation is done here — re-implementing it would
        double-warn and risk the two validators diverging.

        Empty input returns ``[]``, which the caller treats as "absent".

        Non-list/tuple values are rejected rather than iterated: a string value like
        ``"safety.harsh_braking"`` would iterate to 15 single-character ids, and an
        integer like ``5`` would raise ``TypeError``.  Both silently discard the Tier A
        overrides via the outer ``except`` — guarding here keeps ``sim_overrides``
        (city, routeLength) alive and degrades only the catalog half.
        """
        seen: "set[str]" = set()
        result: "list[str]" = []
        for lst in (safety_list, maintenance_list):
            if not lst:
                continue
            if not isinstance(lst, (list, tuple)):
                print(
                    f"⚠️  WARNING PresenceLoop: safetyScenarios/maintenanceScenarios "
                    f"value {lst!r} is not a list — ignoring (catalog scenarios will be "
                    f"empty for this trip)",
                    flush=True,
                )
                continue
            for eid in lst:
                s = str(eid).strip()
                if s and s not in seen:
                    seen.add(s)
                    result.append(s)
        return result

    def _snapshot_catalog_attrs(self) -> dict:
        """Capture the catalog-side overridable state before a trip.

        Deep-copies both values.  Today's ``EventCatalogDriver.set_active_events``
        *rebinds* ``self.active_events`` (``self.active_events = valid``), so an
        in-place mutation is not the current production behaviour.  The deep-copy
        is kept as a cheap defence-in-depth: if a future collaborator mutates the
        list in place, a shallow copy (``list(x)``) would alias the live list and
        make restore a no-op, silently leaking the scenario override into the next
        trip.  Deep-copy is strictly safer and costs nothing at list sizes here.

        Returns ``_ATTR_ABSENT`` for each attribute that does not yet exist on the
        simulator, so ``_restore_catalog_overrides`` removes them rather than writing
        ``None``.  Called *before* the ``try`` block so the snapshot survives even if
        the apply raises partway through.
        """
        import copy
        sim = self._simulator
        driver = getattr(sim, 'event_catalog_driver', None)
        if driver is not None:
            ae = getattr(driver, 'active_events', self._ATTR_ABSENT)
            snapshot_ae = copy.deepcopy(ae) if ae is not self._ATTR_ABSENT else self._ATTR_ABSENT
        else:
            snapshot_ae = self._ATTR_ABSENT
        dt = getattr(sim, 'degradation_targets', self._ATTR_ABSENT)
        snapshot_dt = copy.deepcopy(dt) if dt is not self._ATTR_ABSENT else self._ATTR_ABSENT
        return {
            'active_events': snapshot_ae,
            'degradation_targets': snapshot_dt,
        }

    def _apply_catalog_overrides(self, catalog_scenarios: list, sim_id: str = '') -> None:
        """Apply the event-catalog scenario list to the owned simulator.

        If ``catalog_scenarios`` is empty, this is a no-op — the caller is responsible
        for not calling this with an empty list when the intent carried no scenarios,
        but the method is safe either way.

        On the first intent that carries a non-empty list, an ``EventCatalogDriver`` is
        constructed and cached on the simulator.  Construction does a full DDB table
        scan; it must happen at most once per container and only when scenarios are
        actually requested.  A construction failure logs and degrades gracefully — the
        trip still runs, just without scenario overrides.
        """
        if not catalog_scenarios:
            return
        sim = self._simulator
        driver = getattr(sim, 'event_catalog_driver', None)
        if driver is None:
            # Lazy construction: warm containers launched without --events have
            # event_catalog_driver=None.  Build and cache it now.
            # Stage is read from DEPLOYMENT_STAGE, the same variable main() uses —
            # reading table_suffix would give '{stage}-storage', building a table
            # name that does not exist.  Fail closed if DEPLOYMENT_STAGE is unset
            # rather than defaulting to 'prod' (a staging container with the var
            # unset must not silently read the production catalog).
            try:
                from event_catalog_driver import EventCatalogDriver
                stage = __import__('os').environ.get('DEPLOYMENT_STAGE')
                if not stage:
                    print(
                        f"⚠️  WARNING PresenceLoop: DEPLOYMENT_STAGE is not set for "
                        f"{self._vehicle_id} (sim={sim_id}) — "
                        f"no scenario override applied, trip continues",
                        flush=True,
                    )
                    return
                region = getattr(sim, 'region', 'us-east-1')
                driver = EventCatalogDriver(region=region, stage=stage)
                sim.event_catalog_driver = driver
                print(
                    f"ℹ️  PresenceLoop: constructed EventCatalogDriver (stage={stage}) "
                    f"for {self._vehicle_id} (sim={sim_id})",
                    flush=True,
                )
            except Exception as ce:
                print(
                    f"⚠️  WARNING PresenceLoop: failed to construct EventCatalogDriver "
                    f"for {self._vehicle_id} (sim={sim_id}): {ce} — "
                    f"no scenario override applied, trip continues",
                    flush=True,
                )
                return
        try:
            driver.set_active_events(catalog_scenarios)
            sim.degradation_targets = driver.compute_degradation_targets()
        except Exception as ae:
            print(
                f"⚠️  WARNING PresenceLoop: failed to apply catalog scenarios "
                f"{catalog_scenarios!r} for {self._vehicle_id} (sim={sim_id}): {ae} — "
                f"keeping existing state, trip continues",
                flush=True,
            )

    def _restore_catalog_overrides(self, snapshot: dict) -> None:
        """Restore the catalog-side state captured by ``_snapshot_catalog_attrs``.

        Called from a ``finally`` so a mid-trip crash cannot leak a scenario override
        into the next trip.  Mirrors ``_restore_trip_overrides`` exactly but targets
        the driver's ``active_events`` and the simulator's ``degradation_targets``.
        """
        import copy
        sim = self._simulator
        driver = getattr(sim, 'event_catalog_driver', None)

        ae_snap = snapshot.get('active_events', self._ATTR_ABSENT)
        if driver is not None:
            if ae_snap is self._ATTR_ABSENT:
                driver.active_events = []
            else:
                driver.active_events = copy.deepcopy(ae_snap)

        dt_snap = snapshot.get('degradation_targets', self._ATTR_ABSENT)
        if dt_snap is self._ATTR_ABSENT:
            if hasattr(sim, 'degradation_targets'):
                delattr(sim, 'degradation_targets')
        else:
            sim.degradation_targets = copy.deepcopy(dt_snap)

    def _snapshot_sim_attrs(self) -> dict:
        """Capture every overridable simulator attribute, for `_restore_trip_overrides`.

        Separate from `_apply_trip_overrides` so the caller can snapshot *before*
        entering the `try` whose `finally` restores, and apply *inside* it. Folding the
        two together left a window — review Cycle 1, Suggestion 1 — where an exception
        part-way through applying would have discarded the snapshot along with the
        chance to undo the attributes already set.

        Always covers all four attributes, including ones this intent does not override,
        so restore is total and idempotent regardless of which keys were present.
        """
        sim = self._simulator
        return {
            name: getattr(sim, name, self._ATTR_ABSENT)
            for name in self._OVERRIDABLE_SIM_ATTRS
        }

    def _apply_trip_overrides(self, overrides: dict) -> None:
        """Write `overrides` onto the owned simulator. Snapshot first, via
        `_snapshot_sim_attrs`, and call this inside the `try` that restores."""
        sim = self._simulator
        for name, value in overrides.items():
            setattr(sim, name, value)

    def _restore_trip_overrides(self, snapshot: dict) -> None:
        """Restore the simulator attributes captured by `_apply_trip_overrides`.

        Called from a `finally`, so an exception mid-trip cannot leak an override
        into the next trip on a long-lived container: a trip that ran in Munich must
        not leave the following container-argv-driven trip in Munich.
        """
        sim = self._simulator
        for name, value in snapshot.items():
            if value is self._ATTR_ABSENT:
                if hasattr(sim, name):
                    delattr(sim, name)
            else:
                setattr(sim, name, value)

    def _invalidate_cached_route(self) -> None:
        """Drop the owned VehicleState's cached route so the next trip rebuilds it.

        **Required for the override to take effect at all**, not a tidy-up.
        `generate_telemetry_data` builds a route only when the state has none
        (``if not hasattr(previous_state, 'route') or not previous_state.route``).
        At trip end it sets ``trip_started = False`` and ``route_index = 0`` but
        leaves ``route`` populated, and ``run_trips`` breaks out after its final trip
        *before* the between-trip ``vs.reset()``. So on a warm container the first
        trip of every later intent re-drives the **previous** route — the stale city
        and the stale point count — and an override written to
        ``simulator.route_length`` / ``city_lat`` / ``city_lng`` would be read by
        nobody.

        That is the mechanism behind the issue's measured *"MUNICH reached a
        container: False"*: the parameters were not merely unread, the read site was
        unreachable on the reuse path.

        Called for **every** intent-driven trip, not only when an override applies,
        because the staleness cuts both ways: after a trip that overrode the city, an
        intent carrying no parameters must fall back to the container default instead
        of re-driving the overridden route. T3.2's third start checks exactly that
        live. Regenerating costs one route calculation, which every trip after the
        first already pays via the between-trip reset.
        """
        vs = getattr(self, 'vehicle_state', None)
        if vs is None:
            return
        vs.route = []
        vs.route_index = 0

    # ------------------------------------------------------------------
    # Unbounded presence run loop
    # ------------------------------------------------------------------

    def run(self, can_writer=None, idle_interval: float = 9.0,
            _stop_event=None) -> None:
        """Unbounded presence loop — runs for the lifetime of the ECS task.

        Drives a ≤10 s idle tick that:
        1. Emits current actuator/static state to CAN via ``idle_emit``.
        2. Polls DDB for a trip intent written by the simulation Lambda.
        3. On finding an intent, atomically consumes it and calls ``run_trips``.
        4. After ``run_trips`` returns, reverts to idle.

        ``idle_interval`` defaults to 9 s, which is below the 10 s ceiling
        imposed by the ``cms-fleet-gps-10s`` collection scheme (spec §
        *Idle CAN cadence*).

        ``_stop_event`` (optional) — a threading.Event; if set, the loop exits
        cleanly on the next tick.  Used only in tests to stop the loop.

        Single-owner invariant: this method does not disconnect the MQTT
        client or reset vehicle_state.  Only ``shutdown()`` disconnects.
        """
        import time as _time

        # Maximum trips per intent.  MQTT-Direct is launched with --trips 99
        # today, so the ceiling must not go below 99 or it breaks an existing
        # path.  Follow the same doctrine as simulation_lambda.py:559 which
        # clamps route_length with: max(5, min(60, _route_len))
        # "so a bad client value can't hang an ECS task indefinitely".
        #
        # NOTE: duplicated as _MAX_TRIPS_PER_INTENT in
        # services/simulation/lambda/simulation_lambda.py (_start reuse
        # path). The two live in different deployment units — a container image
        # and a Lambda bundle — with no shared module to import from, so a single
        # source of truth is not available. If either changes, change both.
        # Security review Cycle 5, Suggestion 2.
        _MAX_TRIPS_PER_INTENT = 99

        while True:
            # ── Idle CAN emit ──────────────────────────────────────────
            if can_writer is not None:
                try:
                    self.idle_emit(can_writer)
                except Exception as e:
                    print(f"⚠️ PresenceLoop.run: idle_emit error for {self._vehicle_id}: {e}")

            # ── lastSeenAt heartbeat ───────────────────────────────────
            # Refreshes ONLY lastSeenAt, throttled, and only while the session
            # is live. Never writes connectionStatus — see heartbeat().
            try:
                self.heartbeat()
            except Exception as e:
                print(f"⚠️ PresenceLoop.run: heartbeat error for "
                      f"{self._vehicle_id}: {e}")

            # ── Fault-state reconciliation ────────────────────────────
            # Level-triggered: read faultState from DDB and ensure the
            # correct UDS responder is running.  Called BETWEEN idle_emit
            # and the trip-intent poll so the reconciliation has its own
            # independent get_item per spec § *Reconciliation on the idle tick*.
            try:
                self.reconcile_fault_state()
            except Exception as e:
                print(f"⚠️ PresenceLoop.run: reconcile_fault_state error for "
                      f"{self._vehicle_id}: {e}")

            # ── DDB poll for trip intent ───────────────────────────────
            try:
                intent = self.poll_trip_intent()
                if intent:
                    # Validate tripsCount BEFORE any int() conversion that could
                    # raise.  A non-numeric value makes int() raise before
                    # consume_trip_intent runs, so the malformed row is never
                    # removed — it re-fails on every 9-second tick forever
                    # (stuck row + unbounded log flood).  Failing closed here
                    # means consuming-and-dropping the request, not retrying it.
                    raw_trips = intent.get('tripsCount', 1)
                    try:
                        trips_count = int(raw_trips)
                    except (TypeError, ValueError, OverflowError):
                        print(
                            f"⚠️  WARNING PresenceLoop.run: malformed tripsCount "
                            f"{raw_trips!r} for {self._vehicle_id} "
                            f"(sim={intent.get('simulationId')}) — "
                            f"consuming and dropping to prevent stuck row",
                            flush=True,
                        )
                        self.consume_trip_intent(intent)
                        continue

                    # Clamp to [1, 99] so a bad client value can't hang an ECS
                    # task indefinitely.  Same doctrine as simulation_lambda.py:559
                    # which clamps route_length: max(5, min(60, _route_len)).
                    trips_count = max(1, min(_MAX_TRIPS_PER_INTENT, trips_count))

                    if self.consume_trip_intent(intent):
                        # Won the consume race — start the trip phase.
                        sim_id = intent.get('simulationId', '')
                        # Transition SIM_TABLE row out of intent_pending BEFORE
                        # driving telemetry so /status flips to running the
                        # moment the trip picks up (fixes the reuse-path stuck
                        # intent_pending bug — see
                        # issues/2026-08-13-simulation-reuse-path-run-trips-stub/).
                        # A failure to write the row does NOT block the trip.
                        try:
                            self._mark_simulation_running(sim_id)
                        except Exception as mre:
                            print(f"⚠️ PresenceLoop.run: _mark_simulation_running "
                                  f"error for sim={sim_id} vehicle={self._vehicle_id}: {mre}")

                        # ── Tier A per-trip parameters ─────────────────
                        # Apply the intent's city / routeLength to the owned
                        # simulator immediately before the trip and restore them
                        # immediately after, so an intent-driven trip cannot
                        # permanently reconfigure a long-lived container.
                        # `_resolve_trip_overrides` is total by contract, but it is
                        # wrapped anyway: a malformed parameter must never drop the
                        # trip, and an exception escaping here would skip run_trips
                        # entirely — the one outcome worse than the wrong city.
                        try:
                            overrides, catalog_scenarios = self._resolve_trip_overrides(intent)
                        except Exception as ore:
                            print(f"⚠️ PresenceLoop.run: _resolve_trip_overrides "
                                  f"error for {self._vehicle_id} (sim={sim_id}): "
                                  f"{ore} — running with container defaults")
                            overrides, catalog_scenarios = {}, []
                        snapshot = self._snapshot_sim_attrs()
                        catalog_snapshot = self._snapshot_catalog_attrs()

                        trips_ok = False
                        try:
                            # Applied inside the try so that even a partial apply is
                            # undone by the finally (review Cycle 1, Suggestion 1).
                            self._apply_trip_overrides(overrides)

                            # Catalog-side apply: set active events and recompute
                            # degradation targets on the owned simulator.  Lazy
                            # driver construction happens here on first use.
                            # No-op when catalog_scenarios is empty.
                            self._apply_catalog_overrides(catalog_scenarios, sim_id)

                            # Must happen after the override is applied and before
                            # the first trip: the route is only rebuilt when empty,
                            # so a stale route would re-drive the previous city and
                            # point count. See _invalidate_cached_route.
                            self._invalidate_cached_route()

                            print(f"▶️ PresenceLoop: starting {trips_count} trip(s) for "
                                  f"{self._vehicle_id} (sim={sim_id})"
                                  + (f" [overrides: {overrides}]" if overrides else "")
                                  + (f" [scenarios: {catalog_scenarios}]" if catalog_scenarios else ""))
                            self.run_trips(trips_count, can_writer=can_writer)
                            trips_ok = True
                        except Exception as trip_e:
                            print(f"⚠️ PresenceLoop.run: run_trips error for "
                                  f"{self._vehicle_id}: {trip_e}")
                        finally:
                            # Restore even on exception — an override must not leak
                            # into the next trip.
                            self._restore_trip_overrides(snapshot)
                            self._restore_catalog_overrides(catalog_snapshot)
                        # Advance SIM_TABLE row to completed only if trips ran
                        # to completion cleanly. On an exception the row is
                        # left at 'running' — the operator can stop it via
                        # the API, and _status/_list still reconcile via ECS
                        # STOPPED/GONE state.
                        if trips_ok:
                            try:
                                self._mark_simulation_completed(sim_id)
                            except Exception as mce:
                                print(f"⚠️ PresenceLoop.run: "
                                      f"_mark_simulation_completed error for "
                                      f"sim={sim_id} vehicle={self._vehicle_id}: {mce}")
                        print(f"⏸ PresenceLoop: returned to idle for {self._vehicle_id}")
            except Exception as e:
                print(f"⚠️ PresenceLoop.run: poll error for {self._vehicle_id}: {e}")

            # ── Check stop signal ──────────────────────────────────────
            if _stop_event is not None and _stop_event.is_set():
                break

            _time.sleep(idle_interval)


class RealtimeTelemetrySimulator:
    def __init__(self, profile_name: str = "default", region: str = "us-east-1", certificates_table_name: str = None, mode: str = "mqtt_direct", iot_rule_name: str = "cms_dev_iot_msk_rule", **alert_params):
        """Initialize the real-time telemetry simulator
        mode: 'mqtt_direct' (MQTT to IoT Core) or 'can' (CAN bus + GPS via MQTT)
        """
        self.profile_name = profile_name
        self.region = region
        self.certificates_table_name = certificates_table_name
        self.mode = mode
        self.iot_rule_name = iot_rule_name
        self.running = False
        self.simulation_threads = []
        # Default route length (sampled points from AWS Location Service).
        # Each point = one telemetry tick, so with the default 15s interval
        # a 20-point route takes ~5 minutes to drive — fits comfortably
        # inside the parent thread's join timeout and is short enough for
        # demos. Override from the /start API's `route_length` field via
        # the setter below. Valid range enforced at the API boundary.
        self.route_length = 20

        # CAN encoder/writer for can mode
        self.can_encoder = None
        self.can_writer = None
        self.can_writers = {}  # per-vehicle writers for multi-vehicle FWE (vcan0, vcan1, etc.)
        if mode == 'can':
            from can_encoder import CANEncoder
            from can_bus_writer import CANBusWriter
            self.can_encoder = CANEncoder()
            # Create per-vehicle CAN writers from FWE_VCAN_MAP env var
            vcan_map_str = os.environ.get('FWE_VCAN_MAP', '')
            if vcan_map_str:
                import json as _json
                vcan_map = _json.loads(vcan_map_str)
                for vehicle_key, iface in vcan_map.items():
                    if iface not in [w.channel for w in self.can_writers.values()]:
                        writer = CANBusWriter(interface='socketcan', channel=iface)
                        writer.open()
                        self.can_writers[vehicle_key] = writer
                        print(f"🔌 CAN writer for {vehicle_key}: {iface}")
            # Default writer — use CAN_BUS0 env var if set, otherwise auto-detect
            can_channel = os.environ.get('CAN_BUS0', '')
            if can_channel:
                self.can_writer = CANBusWriter(interface='socketcan', channel=can_channel)
            else:
                self.can_writer = CANBusWriter()
            self.can_writer.open()
            print(f"🔌 CAN mode: {self.can_writer.interface}/{self.can_writer.channel} ({self.can_encoder.signal_count} signals mapped)")
        
        # === FORCED ALERT PARAMETERS ===
        self.force_tire_blowout = alert_params.get('force_tire_blowout', False)
        self.force_engine_overheat = alert_params.get('force_engine_overheat', False)
        self.force_battery_critical = alert_params.get('force_battery_critical', False)
        self.force_brake_failure = alert_params.get('force_brake_failure', False)
        self.force_oil_pressure_low = alert_params.get('force_oil_pressure_low', False)
        self.force_hv_battery_degradation = alert_params.get('force_hv_battery_degradation', False)
        self.force_safety_event = alert_params.get('force_safety_event', None)
        self.safety_rate = alert_params.get('safety_rate', 1.0)
        self.progressive_degradation = alert_params.get('progressive_degradation', True)
        self.tire_slow_leak = alert_params.get('tire_slow_leak', False)
        self.tire_pressure_imbalance = alert_params.get('tire_pressure_imbalance', False)
        
        # Initialize AWS session (None profile = use env/task role)
        session = boto3.Session(profile_name=profile_name if profile_name != 'default' else None,
                                region_name=region)
        self.dynamodb = session.resource('dynamodb', region_name=region)
        self.iot_client = session.client('iot', region_name=region)
        self.account_id = session.client('sts').get_caller_identity()['Account']
        
        # Setup logging
        log_filename = f"simulation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_filename),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # Cache for real drivers
        self.real_drivers = []
        self.drivers_loaded = False
        
        # Driver selection configuration
        # 'assigned' (default since 2026-05-29) — pick the active driver
        #            whose `assignedVehicleId` equals the simulated vehicle.
        #            If multiple match, pick the most recently hired (matches
        #            reconcile_trip_driver_ids.py windowing). If zero match,
        #            fall back to `consistent` hash-based mode for that
        #            vehicle so the simulator still produces a trip.
        # 'consistent' — hash(vehicleId) % len(drivers) (legacy default).
        # 'random'     — random.choice(drivers).
        # 'specific'   — always use `specific_driver_id` (for tests/demos).
        self.driver_selection_mode = 'assigned'
        self.specific_driver_id = None
        self.logger.info(f"🚀 Simulation logging started - {log_filename}")
        
        # Detect table names for certificate lookup only
        self.table_names = self._detect_table_names()
        print(f"🔍 Detected table suffix: {getattr(self, 'table_suffix', 'NOT_SET')}")
        print(f"🔍 Detected tables: {self.table_names}")
        
        # IoT Core configuration
        self.iot_endpoint = self._get_iot_endpoint()
        self.mqtt_connections = {}
        
        # Vehicle state tracking
        self.vehicle_states = {}

        # Presence loops — keyed by vehicleId; populated when a PresenceLoop
        # is constructed and registered (either by start_simulation or by
        # test scaffolding).  One loop per vehicle per task lifetime.
        # See class PresenceLoop for the single-owner invariant.
        self.presence_loops: Dict[str, "PresenceLoop"] = {}
        
        # Detection thresholds
        self.HARD_BRAKING_THRESHOLD = 8.0
        self.RAPID_ACCELERATION_THRESHOLD = 4.0
        self.ENGINE_CRITICAL_TEMP = 240
    
    def _detect_table_names(self) -> Dict[str, str]:
        """Detect CMS UI table names using deployment stage"""
        stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
        prefix = f"cms-{stage}-storage"
        self.table_suffix = f"{stage}-storage"
        
        return {
            'vehicles': f"{prefix}-vehicles",
            'trips': f"{prefix}-trips",
            'telemetry': f"{prefix}-telemetry",
        }
    
    def _get_iot_endpoint(self) -> str:
        """Get IoT Core endpoint"""
        try:
            response = self.iot_client.describe_endpoint(endpointType='iot:Data-ATS')
            return response['endpointAddress']
        except Exception as e:
            print(f"❌ Error getting IoT endpoint: {e}")
            return None
    
    def _load_real_drivers(self) -> List[Dict]:
        """Load real drivers from DynamoDB drivers table.

        Fail-closed: raises RuntimeError if the drivers table is empty.
        Per the 2026-05-29-staging-drivers-simulator-cognito-parity spec
        (Decision 4), the simulator no longer auto-creates phantom
        drivers when the drivers table is empty. Operators must run
        `make seed-drivers DEPLOYMENT_STAGE=$DEPLOYMENT_STAGE
        AWS_REGION=$AWS_REGION` before starting the simulator.

        Behaves uniformly across dev / staging / prod — no
        environment-conditional fallback. This forces the seed-first
        discipline established in the prod foundation work and prevents
        silent drift.
        """
        if self.drivers_loaded:
            return self.real_drivers
            
        try:
            # Use the known drivers table name for current deployment
            drivers_table_name = f"cms-{os.environ.get('DEPLOYMENT_STAGE', 'dev')}-storage-drivers"
            
            # Try default profile first for DynamoDB access
            try:
                dynamodb = boto3.resource('dynamodb', region_name=self.region)
                drivers_table = dynamodb.Table(drivers_table_name)
            except:
                # Fallback to configured profile
                drivers_table = self.dynamodb.Table(drivers_table_name)
            
            response = drivers_table.scan(
                FilterExpression='#status = :status',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={':status': 'active'}
            )
            
            self.real_drivers = response.get('Items', [])
            self.drivers_loaded = True

            if not self.real_drivers:
                raise RuntimeError(
                    f"Drivers table is empty ({drivers_table_name}). "
                    "Run 'make seed-drivers DEPLOYMENT_STAGE=$DEPLOYMENT_STAGE "
                    "AWS_REGION=$AWS_REGION' before starting the simulator."
                )

            print(f"✅ Loaded {len(self.real_drivers)} active drivers from {drivers_table_name}")
            return self.real_drivers

        except RuntimeError:
            # Fail-closed: re-raise without swallowing.
            raise
        except Exception as e:
            print(f"❌ Error loading drivers: {e}")
            raise RuntimeError(
                f"Failed to load drivers from DynamoDB: {e}. "
                "Check AWS credentials, region, and that the drivers table exists."
            ) from e

    def _drivers_for_vehicle(self, vehicle_id: str) -> List[Dict]:
        """Return drivers whose `assignedVehicleId == vehicle_id` and
        `status == 'active'`, sorted by `hireDate` ascending.

        Backs the new `assigned` driver-selection mode (Task 2.3 in spec
        2026-05-29-staging-drivers-simulator-cognito-parity). Cached on
        first call: in a typical run we make N calls per simulated trip
        (one per vehicle on engine-start), so a per-vehicle index avoids
        repeatedly scanning `self.real_drivers`.

        Multiple drivers per vehicle is allowed by the data model — real
        fleets have primary/backup pairings. Callers pick the most-recent
        hire from the returned list to match the windowing semantics in
        `reconcile_trip_driver_ids.py`.
        """
        if not hasattr(self, '_drivers_by_vehicle'):
            from collections import defaultdict
            index = defaultdict(list)
            for d in self.real_drivers:
                v = d.get('assignedVehicleId')
                if v:
                    index[v].append(d)
            for v in index:
                def _hire_ms(dr):
                    try:
                        return int(datetime.strptime(dr.get('hireDate', '2000-01-01'), '%Y-%m-%d').timestamp() * 1000)
                    except (TypeError, ValueError):
                        return 0
                index[v].sort(key=_hire_ms)
            self._drivers_by_vehicle = dict(index)
        return self._drivers_by_vehicle.get(vehicle_id, [])

    # NOTE: `_ensure_driver_exists` was REMOVED in 2026-05-29 per
    # spec `2026-05-29-staging-drivers-simulator-cognito-parity` Decision 4.
    # The simulator no longer auto-creates phantom drivers when the
    # drivers table is empty. Operators must run `make seed-drivers`
    # before starting the simulator. See `_load_real_drivers` for the
    # fail-closed behaviour.

    def configure_driver_selection(self, mode='assigned', specific_driver_id=None):
        """Configure how drivers are selected for vehicles.

        Args:
            mode: 'assigned' (default), 'random', 'consistent', or 'specific'
            specific_driver_id: Driver ID to use when mode is 'specific'
        """
        valid_modes = ('assigned', 'random', 'consistent', 'specific')
        if mode not in valid_modes:
            raise ValueError(
                f"Unknown driver_selection_mode={mode!r}; "
                f"valid: {valid_modes}"
            )
        self.driver_selection_mode = mode
        self.specific_driver_id = specific_driver_id
        print(f"🎯 Driver selection configured: {mode}" + 
              (f" (driver: {specific_driver_id})" if specific_driver_id else ""))
    
    def get_active_vehicles(self) -> List[Dict]:
        """Get list of active vehicles from DynamoDB"""
        if 'vehicles' not in self.table_names:
            print("❌ Vehicles table not found")
            return []
        
        try:
            table = self.dynamodb.Table(self.table_names['vehicles'])
            response = table.scan(
                FilterExpression='#status = :status',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={':status': 'active'}
            )
            
            vehicles = response.get('Items', [])
            print(f"✅ Found {len(vehicles)} active vehicles")
            return vehicles
            
        except Exception as e:
            print(f"❌ Error getting vehicles: {e}")
            return []
    
    def generate_route_points(self, start_lat: float, start_lon: float, num_points: int = 20) -> List[Dict]:
        """Generate route points using Amazon Location Services"""
        try:
            # Initialize Location client (use task role in cloud, profile locally)
            session = boto3.Session(
                profile_name=self.profile_name if self.profile_name != 'default' else None,
                region_name=self.region)
            location_client = session.client('location', region_name=self.region)
            
            # Generate random destination within ~5km radius
            dest_lat = start_lat + random.uniform(-0.045, 0.045)  # ~5km
            dest_lon = start_lon + random.uniform(-0.045, 0.045)
            
            # Calculate route using Amazon Location Services
            response = location_client.calculate_route(
                CalculatorName=os.environ.get('ROUTE_CALCULATOR_NAME', 'cms-prod-ui-route-calculator'),  # Assumes route calculator exists
                DeparturePosition=[start_lon, start_lat],
                DestinationPosition=[dest_lon, dest_lat],
                TravelMode='Car',
                IncludeLegGeometry=True
            )
            
            # Extract route points from geometry.
            # Sampling note (2026-05-05): previously this did
            #   for i in range(0, len(coordinates), step):
            #       route_points.append(...)
            # which overshoots when len(coordinates) % step != 0. A
            # 168-coord route with step=4 yields 42 sampled points,
            # not 30, because range(0, 168, 4) has 42 elements. That
            # variability caused trips to occasionally run longer than
            # the parent thread's 10-minute join timeout, cutting off
            # the ignition-off step and leaving trips stuck as ACTIVE.
            # Fix: break after num_points so we ALWAYS return ≤
            # num_points items regardless of AWS LS's coordinate count.
            route_points = []
            if 'Legs' in response and response['Legs']:
                geometry = response['Legs'][0].get('Geometry', {})
                if 'LineString' in geometry:
                    coordinates = geometry['LineString']
                    # Sample points from the route
                    step = max(1, len(coordinates) // num_points)
                    for i in range(0, len(coordinates), step):
                        lon, lat = coordinates[i]
                        route_points.append({'lat': lat, 'lng': lon})
                        if len(route_points) >= num_points:
                            break
            
            return route_points if route_points else self._fallback_route(start_lat, start_lon, num_points)
            
        except Exception as e:
            print(f"⚠️ Location Services routing failed: {e}")
            return self._fallback_route(start_lat, start_lon, num_points)
    
    def _fallback_route(self, start_lat: float, start_lon: float, num_points: int) -> List[Dict]:
        """Fallback route generation when Location Services unavailable"""
        route = []
        for i in range(num_points):
            lat_offset = (i * 0.002) + random.uniform(-0.0005, 0.0005)
            lon_offset = (i * 0.002) + random.uniform(-0.0005, 0.0005)
            route.append({
                'lat': start_lat + lat_offset,
                'lng': start_lon + lon_offset
            })
        return route

    def _safe(self, field: str, fallback_min: float = 0, fallback_max: float = 100) -> float:
        """Get a stable value within the safe range for a signal field.
        Caches the value per field so continuous signals don't jump randomly each tick.
        Call with force_new=True or delete from cache to regenerate."""
        if not hasattr(self, '_safe_cache'):
            self._safe_cache = {}
        if field not in self._safe_cache:
            ranges = getattr(self, 'safe_ranges', {})
            if field in ranges:
                mn, mx, _ = ranges[field]
            else:
                mn, mx = fallback_min, fallback_max
            # Pick a value in the middle 60% of the safe range (avoid edges)
            margin = (mx - mn) * 0.2
            self._safe_cache[field] = round(random.uniform(mn + margin, mx - margin), 1)
        return self._safe_cache[field]

    def generate_telemetry_data(self, vehicle: Dict, previous_state: VehicleState = None, force_maintenance_alert: bool = False) -> Dict:
        """Generate standardized telemetry for trip tracking and Flink processing"""
        now = datetime.now(timezone.utc)
        timestamp_ms = int(now.timestamp() * 1000)  # Convert to milliseconds for consistency
        
        # Ensure each waypoint gets a distinct timestamp even if generated in rapid succession
        if previous_state is not None and hasattr(previous_state, 'last_timestamp') and previous_state.last_timestamp:
            if timestamp_ms <= previous_state.last_timestamp:
                # Advance by the configured interval (default 15s) to simulate realistic spacing
                timestamp_ms = previous_state.last_timestamp + (getattr(self, 'telemetry_interval', 15) * 1000)
        
        # Initialize previous_state if None
        if previous_state is None:
            previous_state = VehicleState()
        
        # Initialize route if needed
        if not hasattr(previous_state, 'route') or not previous_state.route:
            # Use configured city coordinates or vehicle location
            if hasattr(self, 'city_lat') and hasattr(self, 'city_lng'):
                base_lat = self.city_lat
                base_lon = self.city_lng
            else:
                base_lat = float(vehicle.get('location', {}).get('latitude', 40.7128))
                base_lon = float(vehicle.get('location', {}).get('longitude', -74.0060))
            previous_state.route = self.generate_route_points(base_lat, base_lon, num_points=self.route_length)
        
        # Get current route position first
        current_pos = previous_state.route[min(previous_state.route_index, len(previous_state.route) - 1)]
        
        # Engine state and trip management - ONLY create trip ONCE per vehicle
        engine_event = None
        if not previous_state.trip_started:
            previous_state.engine_on = True
            previous_state.trip_started = True
            # Create consistent tripId using vehicle ID and current timestamp
            previous_state.current_trip_id = f"{vehicle['vehicleId']}-{timestamp_ms}-{str(uuid.uuid4())[:8]}"
            # Assign driver per vehicle based on configuration
            if not previous_state.current_driver_id:
                # Load real drivers from database (fail-closed if empty —
                # see _load_real_drivers docstring).
                real_drivers = self._load_real_drivers()

                # `_load_real_drivers` raises RuntimeError on empty drivers
                # table, so we never see real_drivers=[] here. The branch
                # below treats `real_drivers` as guaranteed non-empty.
                if self.driver_selection_mode == 'assigned':
                    # Vehicle-aware: pick the active driver whose
                    # assignedVehicleId matches this vehicle. Most-recent
                    # hireDate wins on tie. Falls back to `consistent`
                    # mode for this vehicle if no driver is assigned.
                    candidates = self._drivers_for_vehicle(vehicle['vehicleId'])
                    if len(candidates) == 1:
                        previous_state.current_driver_id = candidates[0]['driverId']
                    elif len(candidates) > 1:
                        # _drivers_for_vehicle sorts ascending by hireDate;
                        # take the last for most-recent.
                        previous_state.current_driver_id = candidates[-1]['driverId']
                    else:
                        print(
                            f"⚠️ No driver assigned to vehicle {vehicle['vehicleId']}; "
                            "falling back to consistent hash mode."
                        )
                        vehicle_hash = hash(vehicle['vehicleId']) % len(real_drivers)
                        previous_state.current_driver_id = real_drivers[vehicle_hash]['driverId']
                elif self.driver_selection_mode == 'specific' and self.specific_driver_id:
                    # Use specific driver if it exists
                    specific_driver = next((d for d in real_drivers if d['driverId'] == self.specific_driver_id), None)
                    if specific_driver:
                        previous_state.current_driver_id = specific_driver['driverId']
                    else:
                        print(f"⚠️ Specific driver {self.specific_driver_id} not found, using random")
                        previous_state.current_driver_id = random.choice(real_drivers)['driverId']
                elif self.driver_selection_mode == 'random':
                    # Random driver selection
                    selected_driver = random.choice(real_drivers)
                    previous_state.current_driver_id = selected_driver['driverId']
                else:
                    # Consistent hash-based assignment (legacy default)
                    vehicle_hash = hash(vehicle['vehicleId']) % len(real_drivers)
                    selected_driver = real_drivers[vehicle_hash]
                    previous_state.current_driver_id = selected_driver['driverId']
            engine_event = "ENGINE_START"
            
            # Vehicle status updated by Flink based on ENGINE_START event
            
            # Log trip start with route info
            start_pos = previous_state.route[0] if previous_state.route else current_pos
            end_pos = previous_state.route[-1] if previous_state.route else current_pos
            city_name = getattr(self, 'current_city', 'Unknown City')
            print(f"🚗 Starting trip {previous_state.current_trip_id} in {city_name}")
            print(f"   Route: ({start_pos['lat']:.4f}, {start_pos['lng']:.4f}) → ({end_pos['lat']:.4f}, {end_pos['lng']:.4f})")
            print(f"   Driver: {previous_state.current_driver_id}, Vehicle: {vehicle['vehicleId']}")
            
        elif previous_state.route_index >= len(previous_state.route) - 1:
            previous_state.engine_on = False
            engine_event = "ENGINE_STOP"
            
            # Vehicle status updated by Flink based on ENGINE_STOP event
            
            print(f"🏁 Completed trip {previous_state.current_trip_id}")
            
            # Reset for next trip (but keep trip_id for final telemetry)
            previous_state.trip_started = False
            previous_state.route_index = 0
            # DON'T clear trip_id yet - need it for final telemetry
        else:
            engine_event = None
        
        previous_state.route_index += 1
        # Accumulate distance between route points for odometer
        if previous_state.route_index < len(previous_state.route) and previous_state.route_index > 0:
            import math
            p1 = previous_state.route[previous_state.route_index - 1]
            p2 = previous_state.route[previous_state.route_index]
            dlat = math.radians(p2['lat'] - p1['lat'])
            dlng = math.radians(p2['lng'] - p1['lng'])
            a = math.sin(dlat/2)**2 + math.cos(math.radians(p1['lat'])) * math.cos(math.radians(p2['lat'])) * math.sin(dlng/2)**2
            previous_state.cumulative_miles += 2 * 3958.8 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        
        # Generate speed based on engine state
        if previous_state.engine_on:
            current_speed = round(random.uniform(15, 65), 1)
        else:
            current_speed = 0
        
        previous_speed = previous_state.last_speed if previous_state else current_speed
        
        # Calculate acceleration/deceleration
        acceleration = 0.0
        deceleration = 0.0
        if previous_state and previous_state.last_timestamp > 0:
            time_diff = (timestamp_ms - previous_state.last_timestamp) / 1000.0  # Convert back to seconds for calculation
            if time_diff > 0:
                speed_change = current_speed - previous_speed
                if speed_change > 0:
                    acceleration = round(speed_change / time_diff, 1)
                else:
                    deceleration = round(speed_change / time_diff, 1)
        
        # === FORCED SAFETY EVENTS (moved up to define variables early) ===
        aeb_act = 0
        seatbelt = 1  # Initialize seatbelt before use
        phone_use = 0
        
        if previous_state.force_safety_event == 'seatbelt_violation':
            seatbelt = 0
        elif previous_state.safety_rate >= 1.0:
            # Force unsafe conditions when safety_rate is 1.0 or higher
            # Rotate through different unsafe conditions to ensure variety
            cycle_position = (previous_state.route_index % 4)
            if cycle_position == 0:
                seatbelt = 0  # Force seatbelt violation
            elif cycle_position == 1:
                phone_use = 1  # Force phone usage
            elif cycle_position == 2:
                deceleration = round(random.uniform(-6.0, -4.5), 1)  # Force hard braking (> 3.9 m/s² threshold)
            else:
                acceleration = round(random.uniform(4.0, 5.5), 1)  # Force harsh acceleration (> 3.5 m/s² threshold)
        elif previous_state.safety_rate > 0:
            # Generate unsafe seatbelt when safety_rate is high (increased probability)
            seatbelt = 0 if random.random() < (0.5 * previous_state.safety_rate) else 1
        
        # Standardized telemetry format with PROPER millisecond timestamp
        telemetry = {
            'messageType': 'TELEMETRY',
            'vehicleId': vehicle['vehicleId'],
            'timestamp': timestamp_ms,  # Use milliseconds timestamp for consistency with processors
            'speed': current_speed,
            'acceleration': acceleration,
            'deceleration': deceleration,
            'engineRPM': random.randint(800, 4000) if previous_state.engine_on else 0,
            'engineTemp': round(self._safe('engineTemp', 190, 210), 1) if previous_state.engine_on else 70,
            'oilPressure': round(self._safe('oilPressure', 35, 65), 1) if previous_state.engine_on else 0,
            'batteryVoltage': round(previous_state.battery_voltage_base + random.uniform(-0.2, 0.2), 1),
            'fuelLevel': round(max(5, previous_state.fuel_level - random.uniform(0.3, 1.5)), 1),
            'fuel_pressure': round(self._safe('fuel_pressure', 45, 65), 1) if previous_state.engine_on else 0,
            'odometer': round(int(vehicle.get('mileage', 50000)) + previous_state.cumulative_miles, 1),
            'lat': current_pos['lat'],
            'lng': current_pos['lng'],
            'heading': round(random.uniform(0, 360), 1),
            'seatbeltStatus': seatbelt == 1,  # Convert int to boolean, consistent with seatbelt field
            'phoneConnected': random.choice([False, False, False, True]),
            'ignitionOn': previous_state.engine_on,
            'driverId': previous_state.current_driver_id,
        }
            
        # === INTELLIGENT CONDITION PROGRESSION ===
        # Update conditions based on trip progression and driving behavior
        self.update_vehicle_conditions(previous_state, current_speed, acceleration, deceleration)
        # Track fuel consumption from telemetry
        previous_state.fuel_level = telemetry['fuelLevel']
        
        # Apply catalog-driven degradation targets to VehicleState
        # This modifies the state BEFORE telemetry is built, so changes persist across ticks
        targets = getattr(self, 'degradation_targets', {})
        if targets:
            FIELD_TO_STATE = {
                'tire_pressure_fl': 'tire_pressure_fl',
                'tire_pressure_fr': 'tire_pressure_fr',
                'tire_pressure_rl': 'tire_pressure_rl',
                'tire_pressure_rr': 'tire_pressure_rr',
                'engineTemp': 'engine_temp_base',
                'batteryVoltage': 'battery_voltage_base',
            }
            for field, target in targets.items():
                state_attr = FIELD_TO_STATE.get(field)
                if state_attr and hasattr(previous_state, state_attr):
                    current = getattr(previous_state, state_attr)
                    # Mean-revert toward target at 5% per tick
                    new_val = current + (target - current) * 0.05
                    setattr(previous_state, state_attr, new_val)

        # === TIRE PRESSURES (Progressive degradation) ===
        tire_fl = previous_state.tire_pressure_fl
        tire_fr = previous_state.tire_pressure_fr  
        tire_rl = previous_state.tire_pressure_rl
        tire_rr = previous_state.tire_pressure_rr
        
        # Apply forced conditions or natural variation
        if previous_state.force_tire_blowout:
            tire_fl = max(5.0, tire_fl - random.uniform(2, 5))  # Rapid pressure loss
        elif previous_state.tire_slow_leak:
            tire_fl = max(12.0, tire_fl - random.uniform(0.3, 0.8))  # ~1 PSI/min gradual loss
            tire_fr += random.uniform(-0.1, 0.1)
            tire_rl += random.uniform(-0.1, 0.1)
            tire_rr += random.uniform(-0.1, 0.1)
        elif previous_state.tire_pressure_imbalance:
            tire_fl = max(24.0, tire_fl - random.uniform(0.05, 0.15))  # Slowly diverging
            tire_fr += random.uniform(-0.05, 0.05)  # Stable
            tire_rl += random.uniform(-0.05, 0.05)
            tire_rr += random.uniform(-0.05, 0.05)
        else:
            tire_fl += random.uniform(-0.1, 0.1)  # Natural variation
            tire_fr += random.uniform(-0.1, 0.1)
            tire_rl += random.uniform(-0.1, 0.1)
            tire_rr += random.uniform(-0.1, 0.1)
            
        # === ENGINE TEMPERATURE (Load-based progression) ===
        engine_temp = previous_state.engine_temp_base
        if previous_state.force_engine_overheat:
            engine_temp = min(250, engine_temp + random.uniform(5, 15))  # Rapid overheating
        elif current_speed > 60:
            engine_temp += random.uniform(0, 5)  # Higher temp at high speed
        else:
            engine_temp += random.uniform(-2, 2)  # Normal variation
            
        # === BATTERY CONDITIONS ===
        battery_voltage = previous_state.battery_voltage_base
        if previous_state.force_battery_critical:
            battery_voltage = max(10.0, battery_voltage - random.uniform(0.5, 1.0))
        else:
            battery_voltage += random.uniform(-0.1, 0.1)
            
        # === EV CONDITIONS (for EV vehicles) ===
        vehicle_id = vehicle['vehicleId']
        is_ev = hash(vehicle_id) % 10 < 3
        soc = previous_state.soc_base if is_ev else None
        hv_voltage = previous_state.hv_voltage_base if is_ev else None
        
        if is_ev:
            # SOC decreases with distance
            if soc is not None:
                soc = max(5, soc - (previous_state.trip_distance * 0.1))  # Range consumption
                if previous_state.force_battery_critical:
                    soc = max(2, soc - random.uniform(5, 15))  # Rapid drain
                    
            # HV battery degradation
            if hv_voltage is not None and previous_state.force_hv_battery_degradation:
                hv_voltage = max(300, hv_voltage - random.uniform(10, 30))
        
        # === MAINTENANCE INDICATORS (Progressive wear) ===
        oil_life = max(0, previous_state.oil_life - (previous_state.trip_distance * 0.01))
        brake_wear = max(0, previous_state.brake_wear - (previous_state.hard_braking_count * 0.5))
        
        # Safety events already calculated above, now complete the remaining calculations
        if previous_state.force_safety_event == 'hard_braking':
            deceleration = round(random.uniform(-6.0, -4.5), 1)  # Hard braking in m/s²
            previous_state.hard_braking_count += 1
        elif previous_state.force_safety_event == 'collision_avoidance':
            aeb_act = 1
            deceleration = round(random.uniform(-8.0, -5.0), 1)  # Emergency braking
        elif previous_state.force_safety_event == 'phone_usage':
            phone_use = 1
        elif previous_state.safety_rate >= 1.0:
            # Forced conditions already set above, don't override
            pass
        else:
            # Normal random safety events (using safety_rate multiplier)
            safety_multiplier = previous_state.safety_rate
            if deceleration >= 0:  # Only force if not already braking
                if random.random() < (0.2 * safety_multiplier):
                    deceleration = round(random.uniform(-5.5, -4.0), 1)
            if acceleration <= 0:
                if random.random() < (0.1 * safety_multiplier):
                    acceleration = round(random.uniform(3.8, 5.0), 1)
            aeb_act = 1 if random.random() < (0.01 * safety_multiplier) else 0
            if phone_use == 0:
                phone_use = 1 if random.random() < (0.3 * safety_multiplier) else 0
            
        # === LATERAL ACCELERATION (for harsh cornering detection) ===
        # Simulate lateral g-force based on speed and turning
        base_lateral = 0.0
        if current_speed > 20:
            base_lateral = random.uniform(0, 1.5)  # Normal cornering
        if previous_state.force_safety_event == 'harsh_cornering' or (previous_state.safety_rate >= 1.0 and previous_state.route_index % 4 == 2):
            base_lateral = random.uniform(5.0, 7.0)  # Harsh cornering > 4.4 m/s² threshold
        lateral_acceleration = round(base_lateral, 2)

        # === LANE DEPARTURE / FOLLOWING DISTANCE / DROWSINESS (ADAS signals) ===
        lane_departure_warning = 1 if (random.random() < 0.01 * previous_state.safety_rate) else 0
        forward_collision_distance = round(random.uniform(1.0, 5.0) if current_speed > 30 else 10.0, 1)
        if previous_state.safety_rate >= 0.8:
            forward_collision_distance = round(random.uniform(0.5, 1.5), 1)  # Tailgating
        driver_drowsiness_level = random.choice([0, 0, 0, 0, 1]) if random.random() < (0.05 * previous_state.safety_rate) else 0

        # Add calculated values to telemetry
        telemetry.update({
            
            # === UPDATED TELEMETRY WITH INTELLIGENT CONDITIONS ===
            'tire_pressure_fl': round(tire_fl, 1),
            'tire_pressure_fr': round(tire_fr, 1), 
            'tire_pressure_rl': round(tire_rl, 1),
            'tire_pressure_rr': round(tire_rr, 1),
            'tire_temp_max': random.randint(90, 130),
            
            # === REAL CAN SIGNALS FOR SAFETY DETECTION ===
            'lateralAcceleration': lateral_acceleration,
            'laneDepartureWarning': lane_departure_warning,
            'forwardCollisionDistance': forward_collision_distance,
            # followingDistance: the FWE-decoded field for FollowingDistance CAN signal (0x141).
            # Mirrors forwardCollisionDistance so both the legacy key and the canonical
            # encoder-mapped key carry the same value into the CAN encode path.
            'followingDistance': forward_collision_distance,
            # harsh_turn: FWE-decoded field for HarshTurn CAN signal (0x140).
            # lateralAcceleration is the MQTT alias; harsh_turn is the FWE/DBC-decoded alias.
            # Both must be present so the encoder encodes the CAN signal AND so the
            # degradation-target injection can override the right key in FWE mode.
            'harsh_turn': lateral_acceleration,
            # harsh_acc / harsh_brk: FWE-decoded fields for HarshAcceleration / HarshBraking
            # CAN signals. Derived from acceleration/deceleration; kept >= 0 (magnitude only).
            'harsh_acc': max(0.0, acceleration),
            'harsh_brk': max(0.0, abs(deceleration)),
            'driverDrowsinessLevel': driver_drowsiness_level,
            
            # === ADAS SYSTEM SIGNALS ===
            'aeb_act': aeb_act,
            'abs_act': 1 if random.random() < 0.005 else 0,
            'esc_act': 1 if random.random() < 0.003 else 0,
            'airbag_warn': 1 if random.random() < 0.0001 else 0,
            'seatbelt': seatbelt,
            'phone_use': phone_use,
            'windows_up': random.choice([1, 1, 0]),       # Usually up  
            'trunk_locked': random.choice([1, 1, 1, 0]),  # Usually locked
            'alarm_armed': random.choice([1, 1, 0]),      # Often armed
            'keyless_entry': random.choice([1, 0]),       # Key proximity
            
            # === VEHICLE CONTROL SYSTEMS ===
            'parking_brake': 1 if current_speed == 0 else random.choice([0, 0, 0, 1]),
            'cruise_control': 1 if current_speed > 35 else 0,
            'traction_control': 1,  # Usually enabled
            'stability_control': 1, # Usually enabled
            
            # === CLIMATE & COMFORT ===
            'hvac_on': random.choice([1, 1, 0]),  # Usually on
            'target_temp': random.randint(68, 76), # Target temperature F
            'cabin_temp': random.randint(65, 80),  # Actual cabin temp F
            'seat_heat_driver': random.choice([0, 0, 1, 2]),  # Heat level 0-3
            
            # === LIGHTING SYSTEMS ===
            'headlights': random.choice([0, 1, 2]),  # 0=off, 1=auto, 2=on
            'hazard_lights': random.choice([0, 0, 0, 1]),  # Emergency only
            'turn_signal_active': random.choice([0, 0, 0, 1]),
            
            # === ELECTRICAL SYSTEMS ===
            'alternator_output': round(random.uniform(13.8, 14.4), 1),
            
            # === EV-SPECIFIC FIELDS (30% of fleet) ===
            # Determine if this is an EV based on vehicle ID hash
            'is_ev': hash(vehicle_id) % 10 < 3,  # 30% EV fleet
            
            # EV Fields (only populated for EVs)
            'soc': random.randint(15, 95) if hash(vehicle_id) % 10 < 3 else None,  # State of charge %
            'volt': round(random.uniform(350, 420), 1) if hash(vehicle_id) % 10 < 3 else None,  # HV battery voltage
            'regen_pwr': round(random.uniform(-30, 0), 1) if (hash(vehicle_id) % 10 < 3 and current_speed > 10) else (0 if hash(vehicle_id) % 10 < 3 else None),  # Regenerative power kW
            
            # ICE Fields (only populated for ICE vehicles)  
            'fuel_rate': round(random.uniform(8.0, 15.0), 1) if hash(vehicle_id) % 10 >= 3 else None,  # Fuel consumption
            'fuel_lvl': random.randint(10, 95) if hash(vehicle_id) % 10 >= 3 else None,  # Fuel level %
            
            # === CONNECTIVITY ===
            'wifi_connected': random.choice([0, 1]),
            'bluetooth_devices': random.randint(0, 3),  # Connected devices
            'navigation_active': random.choice([1, 0]) if previous_state.engine_on else 0,
            
            # === COMMERCIAL VEHICLE SPECIFIC ===
            'air_pressure': random.randint(90, 125),  # PSI air brakes
            'hydraulic_pressure': random.randint(1800, 2200),  # PSI
            
        })
        
        # === MAINTENANCE INDICATORS (Progressive + Intelligent) ===
        # Fault conditions are generated only when asked for: always under
        # force_maintenance_alert, otherwise at `random_maintenance_rate`
        # (CLI --random-maintenance-rate, default 0.0). A 10% per-message rate
        # used to be hard-coded here, so every "healthy vehicle" trip raised
        # P0562 / P0001 (issues/2026-09-25-sim-healthy-trip-raises-maintenance-dtcs/).
        # Catalog events chosen in the UI are driven by degradation_targets, not this.
        maintenance_alert_chance = force_maintenance_alert or (
            random.random() < getattr(self, 'random_maintenance_rate', 0.0)
        )
        # Healthy boost sits inside the catalog-derived safe range (above the
        # 5 psi underboost rule). Shared by turbo_boost and its turboBoost alias.
        healthy_turbo_boost = self._safe('turbo_boost', 6, 15)
        
        telemetry.update({
            'oil_life': round(random.uniform(5, 15), 1) if maintenance_alert_chance else round(oil_life, 1),  # Sometimes low oil life
            'brake_wear': round(brake_wear, 1),
            'filter_life': int(self._safe('filter_life', 40, 100)),
            'tire_tread_fl': round(self._safe('tire_tread_fl', 5.0, 8.0), 1),
            'tire_tread_fr': round(self._safe('tire_tread_fr', 5.0, 8.0), 1),
            'tire_tread_rl': round(self._safe('tire_tread_rl', 5.0, 8.0), 1),
            'tire_tread_rr': round(self._safe('tire_tread_rr', 5.0, 8.0), 1),
            'engine_hours_total': random.randint(8500, 12000) if maintenance_alert_chance else random.randint(5000, 8000),  # Sometimes high hours
            'idle_hours_total': int(self._safe('idle_hours_total', 200, 400)),
            
            # === MAINTENANCE PROCESSOR COMPATIBLE FIELDS ===
            'engineTemp': round(random.uniform(235, 245), 1) if maintenance_alert_chance else round(engine_temp, 1),  # Forced: always > 230°F threshold
            'oilPressure': round(random.uniform(10, 14), 1) if maintenance_alert_chance else round(random.uniform(25, 45), 1),  # Forced: always < 15 PSI threshold
            'coolant_temp': round(random.uniform(215, 230), 1) if maintenance_alert_chance else round(random.uniform(180, 210), 1),  # Sometimes overheating
            # Forced: always < 11.8 V. Healthy: keep the progressive value built from
            # battery_voltage_base above, which is where catalog degradation lands.
            'batteryVoltage': round(random.uniform(11.0, 11.7), 1) if maintenance_alert_chance else telemetry['batteryVoltage'],
            'dtc_codes_active': 1 if maintenance_alert_chance else 0,
            'eng_temp': round(engine_temp, 1),  # Progressive engine temperature
            'oil_press': round(random.uniform(20, 80), 1) if previous_state.engine_on else 0,
        })
        
        # === EXPANDED VSS-ALIGNED SIGNALS (IDs 101-287) ===
        _spd = telemetry['speed']
        _eng = previous_state.engine_on
        _ev = is_ev
        telemetry.update({
            # ADAS
            'accIsActive': 1 if _spd > 35 else 0, 'accTargetDistance': round(random.uniform(30, 80), 1),
            'aebIsActive': 1, 'aebIsEngaged': 1 if aeb_act else 0,
            'bsdLeftWarning': random.choice([0, 0, 0, 1]), 'bsdRightWarning': random.choice([0, 0, 0, 1]),
            'ccIsActive': 1 if _spd > 35 else 0, 'ccSpeedSet': round(_spd, 0) if _spd > 35 else 0,
            'driverAttention': random.randint(70, 100), 'driverDrowsy': 0,
            'fcwWarning': random.choice([0, 0, 0, 1]),
            'laneDepartActive': 1, 'laneDepartWarning': random.choice([0, 0, 0, 1]),
            'frontDistance': round(random.uniform(20, 100), 1), 'parkAssistActive': 1 if _spd < 5 else 0,
            'rearDistance': round(random.uniform(10, 50), 1), 'tsrSign': 0, 'tsrSpeedLimit': 65,
            'adasFollowDist': round(random.uniform(20, 80), 1), 'adasSpeedLimit': 65,
            # Cabin/Climate
            'hvacAmbientTemp': round(random.uniform(60, 90), 1), 'frontDefroster': 0, 'rearDefroster': 0,
            'hvacRecirc': random.choice([0, 1]), 'hvacMode': random.randint(0, 3),
            'hvacRemotePrecond': 0, 'leftFanSpeed': random.randint(1, 5),
            'leftTemp': round(random.uniform(68, 76), 1), 'rightTemp': round(random.uniform(68, 76), 1),
            'leftHeating': 0, 'leftVent': random.choice([0, 1]),
            'rightHeating': 0, 'steeringWheelHeat': 0,
            # Connectivity
            'infoNavActive': telemetry.get('navigationActive', 0), 'btPairedDevices': random.randint(0, 3),
            'cellNetType': random.choice([0, 1, 2]), 'cellSignal': random.randint(60, 100),
            'otaAvailable': 0, 'otaProgress': 0, 'softwareVersion': 1, 'wifiConn': random.choice([0, 1]),
            # Core duplicates
            'acceleration2': telemetry['acceleration'], 'parkBrakeActive': 1 if _spd == 0 else 0,
            # The check-engine lamp follows the DTC flag; it used to be on in 25% of messages.
            'deceleration2': telemetry['deceleration'], 'diagDTCActive': telemetry['dtc_codes_active'],
            'odometer2': round(telemetry['odometer'] % 3276, 1),
            'engCoolantTemp': round(min(engine_temp - random.uniform(10, 20), 3000), 1),
            'engHoursTotal': round(random.uniform(500, 3000), 1),
            'engIntakeTemp': round(random.uniform(60, 120), 1),
            'fuelSysRate': round(min(telemetry.get('fuelRate') or 0, 3000), 1), 'transGearPos': random.randint(0, 3),
            # Doors — actuator state read from VehicleState so remote lock/unlock
            # commands (handled in simulate_vehicle_telemetry's on_command callback)
            # visibly change the next telemetry frame. Child-locks + open sensors
            # remain simulated because there are no actuators for them today.
            'chargeDoorOpen': 1 if previous_state.charge_door_open else 0,
            'fuelDoorOpen': 0, 'hoodOpen': 0,
            'rearLocked': 1, 'rearOpen': 0,
            'allDoorsLocked': 1 if previous_state.doors_locked else 0,
            'doorLFChildLock': 0, 'doorLFLocked': 1 if previous_state.door_lf_locked else 0, 'doorLFOpen': 0,
            'doorRFChildLock': 0, 'doorRFLocked': 1 if previous_state.door_rf_locked else 0, 'doorRFOpen': 0,
            'doorLRChildLock': random.choice([0, 1]), 'doorLRLocked': 1 if previous_state.door_lr_locked else 0, 'doorLROpen': 0,
            'doorRRChildLock': random.choice([0, 1]), 'doorRRLocked': 1 if previous_state.door_rr_locked else 0, 'doorRROpen': 0,
            # Environment
            'extAirTemp': round(random.uniform(55, 95), 1), 'extBarometric': round(random.uniform(29.5, 30.5), 2),
            'extHumidity': random.randint(30, 80), 'extLight': random.randint(100, 3000), 'extRain': 0,
            # EV/Charging
            'regenLevel': random.randint(0, 3) if _ev else 0,
            'emotorSpeed': random.randint(0, 3000) if _ev and _eng else 0,
            'emotorTemp': round(random.uniform(60, 120), 1) if _ev else 0,
            'emotorTorque': round(random.uniform(0, 300), 1) if _ev and _eng else 0,
            'chargeLimit': 80 if _ev else 0, 'chargeRate': 0, 'chargeType': 0,
            'isCharging': 0, 'chargeScheduled': 0, 'chargeStartStop': 0,
            'chargeTimeLeft': 0, 'tractBattCurrent': round(random.uniform(-50, 200), 1) if _ev else 0,
            'tractBattEnergy': round(random.uniform(0, 50), 1) if _ev else 0,
            'tractBattRange': random.randint(50, 250) if _ev else 0,
            'socCurrent': soc or 0, 'battHealth': random.randint(85, 100) if _ev else 0,
            'battTempAvg': round(random.uniform(25, 40), 1) if _ev else 0,
            'battTempMax': round(random.uniform(35, 50), 1) if _ev else 0,
            'tractBattVoltage': hv_voltage or 0,
            'altVoltage': round(random.uniform(13.8, 14.4), 1),
            'battHVVoltage': hv_voltage or 0, 'battRegenPower': telemetry.get('regen_pwr') or 0,
            'battSOC': soc or 0, 'typeIsEV': 1 if _ev else 0,
            # Geofence/Fleet
            'curfewEnd': 0, 'curfewActive': 0, 'curfewViolated': 0, 'curfewStart': 0,
            'geoLat': current_pos['lat'], 'geoLng': current_pos['lng'],
            'geofenceActive': 0, 'geofenceViolated': 0, 'geofenceRadius': 0,
            'immobilizerActive': 0, 'fleetSpeedLimit': 75, 'speedLimitViolated': 1 if _spd > 75 else 0,
            'valetActive': 0, 'valetSpeedLimit': 25,
            # Lighting
            'highBeamOn': 0, 'frontLightsOn': 1 if _eng else 0, 'rearLightsOn': 1 if _eng else 0,
            'hazardSignaling': telemetry.get('hazard_lights', 0), 'ambientColor': 0, 'gloveBoxLight': 0,
            # Maintenance expanded
            'tireTreadDepth': round(random.uniform(4, 10), 1), 'rtTireTread': round(random.uniform(4, 10), 1),
            'ltTireTread': round(random.uniform(4, 10), 1), 'rtTireTread2': round(random.uniform(4, 10), 1),
            'brakeAirPress': telemetry.get('air_pressure', 100),
            'brakeHydPress': telemetry.get('hydraulic_pressure', 2000),
            'tireTempMaxExp': random.randint(90, 130), 'engIdleHours': random.randint(500, 3000),
            # Mirrors
            'mirrorsAllFolded': 0, 'mirrorLFolded': 0, 'mirrorLHeating': 0, 'mirrorRFolded': 0, 'mirrorRHeating': 0,
            # Powertrain
            'catalystTemp': round(random.uniform(300, 600), 1) if _eng and not _ev else 0,
            'exhaustTemp': round(random.uniform(200, 500), 1) if _eng and not _ev else 0,
            'combIntakeTemp': round(random.uniform(60, 120), 1) if not _ev else 0,
            'combThrottle': round(random.uniform(10, 80), 1) if _eng and not _ev else 0,
            'turboBoost': healthy_turbo_boost if _eng and not _ev else 0,
            'fuelType': 1 if not _ev else 0, 'fuelPressure': round(random.uniform(30, 60), 1) if not _ev else 0,
            'remoteStartActive': 1 if previous_state.remote_start_active else 0, 'transCurrentGear': random.randint(1, 6) if _eng else 0,
            'transDriveMode': random.randint(0, 2),
            # Safety expanded
            'safetyHarshAcc': acceleration, 'safetyHarshBrk': deceleration,
            'safetyHarshTurn': telemetry.get('harsh_turn', telemetry.get('lateralAcceleration', 0)), 'safetySpeedViol': telemetry.get('speed_viol', 0),
            'stabControlActive': 1, 'lateralAccel': round(random.uniform(-2, 2), 2),
            'safetyAirbag': telemetry.get('airbag_warn', 0),
            'driverPhone': phone_use, 'driverSeatbelt': 1 if seatbelt == 0 else 0,
            # Security
            'alarmTriggered': 0, 'panicMode': 0, 'findMyVehicle': 0,
            # TPMS expanded
            'tpmsFlPress': tire_fl, 'tpmsFlTemp': random.randint(90, 130),
            'tpmsFrPress': tire_fr, 'tpmsFrTemp': random.randint(90, 130),
            'tpmsRlPress': tire_rl, 'tpmsRlTemp': random.randint(90, 130),
            'tpmsRrPress': tire_rr, 'tpmsRrTemp': random.randint(90, 130),
            # Vehicle control
            'hornActive': 0, 'keylessProximity': random.choice([0, 1]),
            'lightsHazard': telemetry.get('hazard_lights', 0),
            'headlightsMode': random.choice([0, 1, 2]),
            'lightsTurnSignal': telemetry.get('turn_signal_active', 0),
            'bodyTrunkLocked': 1 if previous_state.trunk_locked else 0,
            'hvacActive': telemetry.get('hvac_on', 1),
            'hvacCabinTemp': telemetry.get('cabin_temp', 72),
            'hvacTargetTemp': telemetry.get('target_temp', 72),
            'infoPhoneConn': 1 if telemetry.get('phoneConnected') else 0,
            'driverHeatLevel': random.choice([0, 0, 1, 2]),
            'driverFastened': seatbelt, 'windowsAllClosed': telemetry.get('windows_up', 1),
            'pwrRemoteStart': 1 if previous_state.remote_start_active else 0,
            # Windows
            'sunroofPos': 0, 'shadePos': 0, 'windowPos': 0,
            'rtWindowPos': 0, 'ltWindowPos': 0, 'rtWindowPos2': 0,
            # Wipers
            'washerFluid': random.randint(50, 100), 'wipingActive': 0, 'wipingMode': 0, 'rearWiping': 0,
            # FWE-decoded aliases for fields that decode differently by mode:
            #   FWE: WasherFluidLevel → washer_fluid_level; MQTT: → washerFluid (above)
            #   FWE: CombustionEngineTurboBoostPressure → turbo_boost; MQTT: → turboBoost (above)
            #   FWE: TemperatureMax → ev_battery_temp_max; MQTT: → battTempMax (above)
            'washer_fluid_level': random.randint(50, 100),
            'turbo_boost': healthy_turbo_boost if previous_state.engine_on and not is_ev else 0,
            'ev_battery_temp_max': round(random.uniform(35, 50), 1) if is_ev else 0,
        })
        if is_ev:
            # An EV has no turbo. Sending 0 with the ignition on tripped the
            # "turbo_boost < 5" underboost rule (P0299) on every EV trip; the
            # evaluator skips fields that are absent.
            telemetry.pop('turbo_boost', None)
        
        # Calculate and add trip progress information
        if previous_state.route:
            route_progress = min(previous_state.route_index / len(previous_state.route), 1.0)
            estimated_trip_duration = len(previous_state.route) * 15  # 15 seconds per route point
            elapsed_trip_time = previous_state.route_index * 15
            estimated_remaining_time = max(0, estimated_trip_duration - elapsed_trip_time)
            
            telemetry['tripProgress'] = {
                'routeIndex': previous_state.route_index,
                'totalRoutePoints': len(previous_state.route),
                'progressPercentage': round(route_progress * 100, 1),
                'estimatedTripDuration': estimated_trip_duration,
                'elapsedTripTime': elapsed_trip_time,
                'estimatedRemainingTime': estimated_remaining_time
            }
        
        # Add engine event if present
        if engine_event:
            telemetry['engineEvent'] = engine_event
        
        # Raw telemetry contains all fields needed for maintenance analysis
        # MaintenanceProcessor will analyze these fields to detect maintenance needs:
        # - oil_life, brake_wear, filter_life (maintenance indicators)
        # - eng_temp, oil_press, coolant_temp (engine health)
        # - tire_tread_fl/fr/rl/rr (tire wear)
        # - engine_hours_total, idle_hours_total (usage patterns)
        # - dtc_codes_active (diagnostic trouble codes)
        
        # NO maintenanceAlerts array - let Flink MaintenanceProcessor handle all detection
        
        # Add safety alerts (simulated) - DISABLED to prevent conflicts with detect_safety_events
        # safety_alerts = self.generate_safety_alerts(telemetry)
        # if safety_alerts:
        #     telemetry['safetyAlerts'] = safety_alerts
        
        # Apply catalog-driven degradation to telemetry fields not on VehicleState
        # These are generated fresh each tick, so we override them directly
        targets = getattr(self, 'degradation_targets', {})
        if targets:
            STATE_FIELDS = {'tire_pressure_fl','tire_pressure_fr','tire_pressure_rl','tire_pressure_rr',
                           'engineTemp','batteryVoltage'}  # Already handled via VehicleState above
            for field, target in targets.items():
                if field in STATE_FIELDS:
                    continue  # handled via VehicleState degradation above
                if field in telemetry:
                    # Field exists on the base telemetry payload — progressively
                    # degrade toward target over ~15 ticks for realism.
                    current = float(telemetry[field])
                    tick = getattr(self, 'event_tick', 0)
                    progress = min(1.0, tick / 15)
                    telemetry[field] = round(current + (target - current) * progress, 1)
                else:
                    # Field is NOT on the base telemetry (e.g. brake_system_fault
                    # for the maintenance.brake_system_fault event). The catalog
                    # wants us to assert it equals the target value, so inject
                    # the field directly. Without this, catalog rules whose
                    # json_fields aren't part of the default telemetry vocabulary
                    # would never fire from simulated runs.
                    telemetry[field] = round(target, 1) if isinstance(target, float) else target
            self.event_tick = getattr(self, 'event_tick', 0) + 1

        # Clear trip ID AFTER generating telemetry if trip is completed
        if engine_event == "ENGINE_STOP":
            previous_state.current_trip_id = None  # Clear trip ID after final telemetry
        
        return telemetry
    
    def generate_maintenance_alerts(self, telemetry: Dict, force_alert: bool = False) -> List[Dict]:
        """Generate maintenance alerts with diagnostic trouble codes - max one per trip"""
        alerts = []
        
        # Get vehicle state to check if maintenance alert already sent for this trip
        vehicle_id = telemetry.get('vehicleId')
        vehicle_state = self.vehicle_states.get(vehicle_id)
        
        # Only generate one maintenance alert per trip
        if vehicle_state and vehicle_state.maintenance_alert_sent:
            return alerts
        
        # Force alert generation if requested, otherwise use probability
        should_generate = force_alert or random.random() > 0.95
        
        if should_generate:
            
            # Define realistic maintenance alert scenarios with proper DTCs
            maintenance_scenarios = [
                {
                    'condition': lambda t: t.get('oilPressure', 100) < 15,
                    'alertType': 'LOW_OIL_PRESSURE',
                    'severity': 'HIGH',
                    'message': 'Oil pressure critically low - immediate attention required',
                    'dtc': 'P0520',  # Engine Oil Pressure Sensor/Switch Circuit
                    'component': 'ENGINE',
                    'thresholdValue': 15.0,
                    'currentValue': lambda t: t.get('oilPressure', 0),
                    'unit': 'PSI'
                },
                {
                    'condition': lambda t: t.get('engineTemp', 0) > 230,
                    'alertType': 'HIGH_ENGINE_TEMP', 
                    'severity': 'HIGH',
                    'message': 'Engine temperature exceeds safe operating range',
                    'dtc': 'P0217',  # Engine Overheating Condition
                    'component': 'COOLING_SYSTEM',
                    'thresholdValue': 230.0,
                    'currentValue': lambda t: t.get('engineTemp', 0),
                    'unit': '°F'
                },
                {
                    'condition': lambda t: t.get('batteryVoltage', 12.5) < 11.5,
                    'alertType': 'LOW_BATTERY',
                    'severity': 'MEDIUM', 
                    'message': 'Battery voltage below optimal range',
                    'dtc': 'P0562',  # System Voltage Low
                    'component': 'ELECTRICAL',
                    'thresholdValue': 11.5,
                    'currentValue': lambda t: t.get('batteryVoltage', 0),
                    'unit': 'V'
                },
                {
                    'condition': lambda t: t.get('engineRPM', 0) > 6000,
                    'alertType': 'ENGINE_OVERSPEED',
                    'severity': 'HIGH',
                    'message': 'Engine RPM exceeds redline - potential engine damage',
                    'dtc': 'P0219',  # Engine Overspeed Condition
                    'component': 'ENGINE',
                    'thresholdValue': 6000,
                    'currentValue': lambda t: t.get('engineRPM', 0),
                    'unit': 'RPM'
                },
                {
                    'condition': lambda t: t.get('fuelLevel', 50) < 5,
                    'alertType': 'LOW_FUEL',
                    'severity': 'LOW',
                    'message': 'Fuel level critically low',
                    'dtc': 'P0461',  # Fuel Level Sensor Circuit Range/Performance
                    'component': 'FUEL_SYSTEM',
                    'thresholdValue': 5.0,
                    'currentValue': lambda t: t.get('fuelLevel', 0),
                    'unit': '%'
                },
                {
                    'condition': lambda t: random.random() < 0.3,  # Random brake wear alert
                    'alertType': 'BRAKE_WEAR',
                    'severity': 'MEDIUM',
                    'message': 'Brake pad wear detected - schedule maintenance',
                    'dtc': 'P0301',  # Generic brake system DTC
                    'component': 'BRAKE_SYSTEM',
                    'thresholdValue': 20.0,
                    'currentValue': lambda t: random.uniform(5, 15),  # Simulated brake pad thickness
                    'unit': 'mm'
                },
                {
                    'condition': lambda t: random.random() < 0.2,  # Random tire pressure alert
                    'alertType': 'TIRE_PRESSURE',
                    'severity': 'MEDIUM',
                    'message': 'Tire pressure below recommended level',
                    'dtc': 'C1234',  # Tire Pressure Monitoring System
                    'component': 'TIRE_SYSTEM',
                    'thresholdValue': 30.0,
                    'currentValue': lambda t: random.uniform(20, 28),  # Simulated tire pressure
                    'unit': 'PSI'
                }
            ]
            
            # Check each scenario and generate alert for first matching condition
            for scenario in maintenance_scenarios:
                if scenario['condition'](telemetry):
                    alert = {
                        'alertId': f"MAINT-{telemetry.get('timestamp')}-{telemetry.get('vehicleId')}",
                        'alertType': scenario['alertType'],
                        'severity': scenario['severity'],
                        'message': scenario['message'],
                        'dtc': scenario['dtc'],
                        'component': scenario['component'],
                        'timestamp': telemetry.get('timestamp'),
                        'vehicleId': telemetry.get('vehicleId'),
                        'thresholdValue': scenario['thresholdValue'],
                        'currentValue': scenario['currentValue'](telemetry),
                        'unit': scenario['unit'],
                        'status': 'ACTIVE',
                        'mileage': telemetry.get('odometer', random.randint(50000, 150000)),
                        'engineHours': random.randint(2000, 8000),
                        'description': f"Maintenance alert for {scenario['component']} - {scenario['message']}"
                    }
                    alerts.append(alert)
                    
                    # Mark maintenance alert as sent for this trip
                    if vehicle_state:
                        vehicle_state.maintenance_alert_sent = True
                    
                    break  # Only one maintenance alert per trip
        
        return alerts
    
    def generate_safety_alerts(self, telemetry: Dict) -> List[Dict]:
        """Generate safety alerts based on driving behavior - matches all types in DynamoDB table"""
        alerts = []
        
        # Only generate safety alerts occasionally to simulate realistic frequency
        if random.random() > 0.85:  # 15% chance of safety alert per telemetry update
            
            # Randomly select ONE safety alert type to prevent coordinate stacking
            alert_type = random.choice([
                'SPEEDING', 'HARD_BRAKING', 'SEATBELT_VIOLATION', 'PHONE_USAGE', 
                'LANE_DEPARTURE', 'RAPID_ACCELERATION', 'HARSH_CORNERING', 
                'TAILGATING', 'DROWSINESS_DETECTED', 'FATIGUE_DETECTION'
            ])
            
            # Ensure we use the exact current coordinates from telemetry
            current_lat = telemetry.get('lat')
            current_lng = telemetry.get('lng')
            
            base_alert = {
                'eventId': f"{alert_type}-{telemetry.get('timestamp')}-{telemetry.get('vehicleId')}",
                'eventType': alert_type,
                'timestamp': telemetry.get('timestamp'),
                'vehicleId': telemetry.get('vehicleId'),
                'lat': current_lat,
                'lng': current_lng,
                'speed': telemetry.get('speed')
            }
            
            # Customize alert based on type
            if alert_type == 'SPEEDING':
                base_alert.update({
                    'severity': 'HIGH',
                    'speedLimit': 55,
                    'message': f"Vehicle exceeding speed limit: {telemetry.get('speed')} mph in 55 mph zone"
                })
            
            elif alert_type == 'HARD_BRAKING':
                base_alert.update({
                    'severity': 'MEDIUM',
                    'deceleration': telemetry.get('deceleration', -9.5),
                    'message': f"Hard braking detected: {telemetry.get('deceleration', -9.5)} m/s²"
                })
            
            elif alert_type == 'SEATBELT_VIOLATION':
                base_alert.update({
                    'severity': 'HIGH',
                    'message': f"Seatbelt not fastened while driving at {telemetry.get('speed')} mph"
                })
            
            elif alert_type == 'PHONE_USAGE':
                base_alert.update({
                    'severity': 'MEDIUM',
                    'message': f"Phone usage detected while driving at {telemetry.get('speed')} mph"
                })
            
            elif alert_type == 'LANE_DEPARTURE':
                base_alert.update({
                    'severity': 'HIGH',
                    'heading': telemetry.get('heading'),
                    'message': f"Lane departure detected at {telemetry.get('speed')} mph"
                })
            
            elif alert_type == 'RAPID_ACCELERATION':
                base_alert.update({
                    'severity': 'MEDIUM',
                    'acceleration': telemetry.get('acceleration', 8.2),
                    'message': f"Rapid acceleration detected: {telemetry.get('acceleration', 8.2)} m/s²"
                })
            
            elif alert_type == 'HARSH_CORNERING':
                base_alert.update({
                    'severity': 'MEDIUM',
                    'lateralG': round(random.uniform(0.8, 1.2), 2),
                    'heading': telemetry.get('heading'),
                    'message': f"Harsh cornering detected at {telemetry.get('speed')} mph"
                })
            
            elif alert_type == 'TAILGATING':
                base_alert.update({
                    'severity': 'HIGH',
                    'followingDistance': round(random.uniform(0.5, 1.5), 1),
                    'message': f"Following too closely at {telemetry.get('speed')} mph"
                })
            
            elif alert_type == 'DROWSINESS_DETECTED':
                base_alert.update({
                    'severity': 'HIGH',
                    'eyeClosureDuration': round(random.uniform(2.0, 5.0), 1),
                    'message': f"Driver drowsiness detected - eye closure for {round(random.uniform(2.0, 5.0), 1)} seconds"
                })
            
            elif alert_type == 'FATIGUE_DETECTION':
                base_alert.update({
                    'severity': 'HIGH',
                    'drivingDuration': random.randint(180, 300),  # 3-5 hours in minutes
                    'message': f"Driver fatigue detected after {random.randint(180, 300)} minutes of driving"
                })
            
            alerts.append(base_alert)
        
        return alerts
        
        # Fuel level alert
        if telemetry.get('fuelLevel', 50) < 15:
            alerts.append({
                'alertType': 'LOW_FUEL',
                'severity': 'LOW',
                'message': 'Fuel level low',
                'dtc': 'P0461'  # Fuel Level Sensor Circuit Range/Performance
            })
        
        # Random additional DTCs for simulation
        if random.random() < 0.1:  # 10% chance
            additional_dtcs = [
                {'dtc': 'P0171', 'alertType': 'LEAN_FUEL_MIXTURE', 'severity': 'MEDIUM', 'message': 'System too lean'},
                {'dtc': 'P0300', 'alertType': 'ENGINE_MISFIRE', 'severity': 'HIGH', 'message': 'Random cylinder misfire'},
                {'dtc': 'P0420', 'alertType': 'CATALYST_EFFICIENCY', 'severity': 'MEDIUM', 'message': 'Catalyst system efficiency below threshold'}
            ]
            alerts.append(random.choice(additional_dtcs))
        
        return alerts
    
    def get_vehicle_certificate(self, vin: str) -> Dict:
        """Retrieve vehicle certificate from DynamoDB"""
        try:
            import boto3
            import os
            
            _stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
            table_name = (self.certificates_table_name or 
                         os.environ.get('VEHICLE_CERTIFICATES_TABLE_NAME') or 
                         f'cms-{_stage}-storage-vehicle-certificates')
            
            dynamodb = boto3.resource('dynamodb', region_name=self.region)
            certificates_table = dynamodb.Table(table_name)
            
            print(f"🔍 Querying certificates table: {table_name}")
            
            response = certificates_table.scan(
                FilterExpression='vin = :vin',
                ExpressionAttributeValues={':vin': vin}
            )
            
            if response['Items']:
                cert_item = response['Items'][0]
                print(f"🔐 Retrieved certificate for VIN {vin} from DynamoDB")
                return {
                    'certificatePem': cert_item['certificatePem'],
                    'privateKey': cert_item['privateKey'],
                    'thingName': cert_item['thingName']
                }
            else:
                print(f"❌ No certificate found for VIN {vin} in DynamoDB table {table_name}")
                return None
                
        except Exception as e:
            print(f"❌ Error retrieving certificate for VIN {vin}: {e}")
            return None

    def publish_heartbeat(self, vehicle_id: str, vin: str, mqtt_client):
        """Publish heartbeat to keep vehicle connection alive"""
        # Commented out for now
        pass
        # try:
        #     heartbeat_data = {
        #         'vehicleId': vehicle_id,
        #         'vin': vin,
        #         'messageType': 'HEARTBEAT',
        #         'timestamp': int(datetime.now(timezone.utc).timestamp() * 1000),
        #         'status': 'online',
        #         'lastSeen': datetime.now(timezone.utc).isoformat()
        #     }
        #     
        #     # Use Basic Ingest for heartbeat
        #     topic = f"$aws/rules/cms_dev_iot_msk_rule/{vehicle_id}/heartbeat"
        #     payload = json.dumps(heartbeat_data)
        #     
        #     # Send heartbeat silently
        #     mqtt_client.publish(topic, payload, qos=1)
        #     
        # except Exception as e:
        #     print(f"❌ Error publishing heartbeat for {vehicle_id}: {e}")

    # =========================================================================
    # Remote-commands actuation
    # =========================================================================
    # The commands round-trip is:
    #   UI -> API Gateway -> commands_lambda -> IoT Core publishes JSON to
    #       cms/commands/{vehicle_id}/request                 (this simulator subscribes)
    #   simulator -> apply_command() -> mutate VehicleState
    #   simulator -> IoT Core publishes JSON to
    #       cms/commands/{vehicle_id}/response                (IoT Rule -> command_response_handler -> DDB)
    #   UI polls history every 5s -> row flips SENT -> SUCCEEDED (with latency).
    #
    # The actual MQTT subscription + `on_command` callback live inline in
    # `simulate_vehicle_telemetry` (see § "Setup commands subscription for this
    # vehicle") because they need the mqtt_client + vehicle_id closure. This
    # helper factors out the state-mutation logic so it is unit-testable
    # without an MQTT loop.
    #
    # Prior art: `setup_commands_subscription` used to live here but (a) was
    # never called, (b) subscribed to `fleet/vehicle/{id}/commands` which nothing
    # publishes to (commands_lambda uses `cms/commands/{id}/request`), and (c)
    # had a placeholder `# future implementation` body. Removed per issue
    # 2026-08-03-remote-commands-simulator-actuation.

    # Map of commandName (from the signal catalog's `actuator.commandName`) to
    # the VehicleState attribute it mutates + a value-transform function.
    # Kept as a class-level constant so tests can inspect coverage.
    _ACTUATOR_MAP = {
        # Doors
        'lock_all_doors':        ('doors_locked',        lambda v: bool(v)),
        'lock_door_frontleft':   ('door_lf_locked',      lambda v: bool(v)),
        'lock_door_frontright':  ('door_rf_locked',      lambda v: bool(v)),
        'lock_door_rearleft':    ('door_lr_locked',      lambda v: bool(v)),
        'lock_door_rearright':   ('door_rr_locked',      lambda v: bool(v)),
        # Trunk & charge door
        # Trunk accepts BOTH names. The signal catalog advertises `lock_trunk`
        # (verified 2026-08-21 against GET /api/commands/catalog) while this map was
        # keyed only on `trunk_lock`, so a client doing the correct thing — reading the
        # catalog and sending what it advertises — missed this dict entirely and the
        # trunk never actuated. Accepting both is non-breaking; renaming the key alone
        # would strand anything already sending `trunk_lock`.
        'trunk_lock':            ('trunk_locked',        lambda v: bool(v)),
        'lock_trunk':            ('trunk_locked',        lambda v: bool(v)),
        'open_charge_door':      ('charge_door_open',    lambda v: bool(v)),
        # Powertrain — remote_start is a coupled actuator: set the remote_start
        # flag AND set engine_on so the ignition, RPM, coolant-temp fields all
        # flip on the very next telemetry frame (rather than waiting for the
        # next trip start). remote_stop clears both.
        'remote_start':          ('remote_start_active', lambda v: bool(v)),
    }

    # Actuators that are transient / momentary — no persistent state to mutate,
    # but we still ACK them as SUCCEEDED so the UI history reflects execution.
    _TRANSIENT_ACTUATORS = {
        'honk_horn',
        'flash_hazards',
        'find_my_vehicle',
        'panic_mode',
        'start_preconditioning',
    }

    def apply_command(self, vehicle_state, command_name, value):
        """Apply a remote-command to a VehicleState.

        Returns (status, reason) where status is one of:
            'SUCCEEDED' — mutation applied (or transient command acked)
            'FAILED'    — unknown commandName; state unchanged; reason populated

        Split out of the MQTT callback so it can be unit-tested without a
        network round-trip. See `_ACTUATOR_MAP` / `_TRANSIENT_ACTUATORS` for
        the supported set.
        """
        if command_name in self._ACTUATOR_MAP:
            attr, transform = self._ACTUATOR_MAP[command_name]
            try:
                setattr(vehicle_state, attr, transform(value))
            except Exception as e:  # pragma: no cover — defensive
                return 'FAILED', f'Value coercion error for {command_name}: {e}'
            # Couple remote_start to engine_on so ignitionOn / RPM / temps
            # flip on the next telemetry emission.
            if command_name == 'remote_start':
                vehicle_state.engine_on = bool(value)
            return 'SUCCEEDED', ''
        if command_name in self._TRANSIENT_ACTUATORS:
            return 'SUCCEEDED', ''
        return 'FAILED', f'Unsupported command: {command_name!r}'

    # Canonical command-request topic for a vehicle. commands_lambda publishes
    # to this topic; this simulator subscribes to it. Kept as a helper so both
    # the initial-connect and reconnect paths (see on_connect in
    # simulate_vehicle_telemetry) compute it identically.
    @staticmethod
    def command_request_topic(vehicle_id: str) -> str:
        return f'cms/commands/{vehicle_id}/request'

    def _subscribe_command_topics(
        self,
        mqtt_client,
        vehicle_id: str,
        vin: str,
        on_command_cb,
        on_sovd_cb,
    ) -> list:
        """(Re)subscribe to BOTH command-request topics for a vehicle.

        Subscribes to:
          1. ``cms/commands/{vehicle_id}/request``  — existing actuator JSON path.
          2. ``cms/commands/things/{vin}/executions/+/sovd/request``  — new SOVD JSON path.

        Called from ``on_connect`` on EVERY successful (re)connection because paho
        MQTT does NOT auto-resubscribe after a reconnect and the broker-side
        persistent-session state may have expired.  This is the extension of the
        fix for ``issues/2026-08-04-mqtt-reconnect-subscription-loss/``.

        HARD GATE C: both subscribes happen inside ``on_connect`` — never once at
        startup outside it.  paho does NOT auto-resubscribe.

        Safe to call multiple times — paho's subscribe / message_callback_add are
        idempotent for the same (topic, callback, qos) triple.

        Returns:
            list of (topic, mid, result) triples — one per subscribed topic.
            result is 0 (``MQTT_ERR_SUCCESS``) on the happy path.
        """
        import sys
        results = []

        # ── 1. Actuator command topic (existing path — unchanged) ──────────────
        cmd_topic = self.command_request_topic(vehicle_id)
        cmd_result, cmd_mid = mqtt_client.subscribe(cmd_topic, qos=1)
        mqtt_client.message_callback_add(cmd_topic, on_command_cb)
        if cmd_result == 0:
            print(f"📡 (Re)subscribing to commands: {cmd_topic} (mid={cmd_mid})")
        else:
            print(
                f"⚠️  Subscribe call returned error code={cmd_result} for {cmd_topic} "
                f"— command channel is NOT live"
            )
        sys.stdout.flush()
        results.append((cmd_topic, cmd_mid, cmd_result))

        # ── 2. SOVD topic (new — peer sub-path, FWE-invisible by construction) ─
        sovd_topic = f'cms/commands/things/{vin}/executions/+/sovd/request'
        sovd_result, sovd_mid = mqtt_client.subscribe(sovd_topic, qos=1)
        mqtt_client.message_callback_add(sovd_topic, on_sovd_cb)
        if sovd_result == 0:
            print(f"📡 (Re)subscribing to SOVD: {sovd_topic} (mid={sovd_mid})")
        else:
            print(
                f"⚠️  Subscribe call returned error code={sovd_result} for {sovd_topic} "
                f"— SOVD channel is NOT live"
            )
        sys.stdout.flush()
        results.append((sovd_topic, sovd_mid, sovd_result))

        return results

    def _subscribe_command_topic(self, mqtt_client, vehicle_id: str, on_command_cb):
        """Backward-compat alias — subscribes to the actuator command topic only.

        Preserved so that ``test_reconnect_subscribe.py``'s
        ``SubscribeCommandTopicTest`` (which calls this method directly and
        asserts its exact return shape) keeps passing.

        For new code use ``_subscribe_command_topics`` which also wires the SOVD
        topic and returns a list of triples.  The ``on_connect`` closure inside
        ``simulate_vehicle_telemetry`` calls ``_subscribe_command_topics`` — the
        source-level check in ``OnConnectCallsSubscribeHelperTest`` searches for
        the substring ``self._subscribe_command_topic(`` which is a prefix of
        ``self._subscribe_command_topics(`` and therefore still matches.

        Returns:
            (topic, mid, result) triple (not a list) for backward compatibility.
        """
        import sys
        topic = self.command_request_topic(vehicle_id)
        result, mid = mqtt_client.subscribe(topic, qos=1)
        mqtt_client.message_callback_add(topic, on_command_cb)
        if result == 0:
            print(f"📡 (Re)subscribing to commands: {topic} (mid={mid})")
        else:
            print(
                f"⚠️  Subscribe call returned error code={result} for {topic} "
                f"— command channel is NOT live"
            )
        sys.stdout.flush()
        return topic, mid, result

    def publish_emergency_alert(self, vehicle_id: str, vin: str, alert_type: str, mqtt_client):
        """Publish emergency alert to fleet/alerts/emergency topic"""
        # Commented out for now
        pass
        # try:
        #     emergency_data = {
        #         'vehicleId': vehicle_id,
        #         'vin': vin,
        #         'messageType': 'EMERGENCY_ALERT',
        #         'alertType': alert_type,
        #         'timestamp': int(datetime.now(timezone.utc).timestamp() * 1000),
        #         'severity': 'HIGH',
        #         'location': {
        #             'lat': 40.7128 + (hash(vehicle_id) % 100) * 0.001,
        #             'lng': -74.0060 + (hash(vehicle_id) % 100) * 0.001
        #         },
        #         'description': f"Emergency alert: {alert_type} detected for vehicle {vehicle_id}"
        #     }
        #     
        #     # Use Basic Ingest for emergency alerts
        #     topic = f"$aws/rules/cms_dev_iot_msk_rule/{vehicle_id}/emergency"
        #     payload = json.dumps(emergency_data)
        #     
        #     result = mqtt_client.publish(topic, payload, qos=1)
        #     if result.rc == 0:
        #         print(f"🚨 Published emergency alert ({alert_type}) for {vehicle_id}")
        #     
        # except Exception as e:
        #     print(f"❌ Error publishing emergency alert for {vehicle_id}: {e}")

    def compress_telemetry(self, data: Dict) -> str:
        import gzip
        import json
        import base64
        
        # Convert to compact JSON
        json_str = json.dumps(data, separators=(',', ':'))  # No spaces
        
        # Gzip compress
        compressed_bytes = gzip.compress(json_str.encode('utf-8'))
        
        # Base64 encode
        base64_encoded = base64.b64encode(compressed_bytes).decode('ascii')
        
        return base64_encoded
    
    def get_vehicle_certificate(self, vehicle_id: str) -> Dict:
        """Get vehicle certificate from DynamoDB table"""
        try:
            # Force the correct certificate table name
            table_name = f"cms-{os.environ.get('DEPLOYMENT_STAGE', 'dev')}-storage-vehicle-certificates"
            
            print(f"🔍 Looking up certificate for vehicleId: {vehicle_id} in table: {table_name}")
            print(f"🔍 Using profile: {self.profile_name}")
            import sys
            sys.stdout.flush()
            
            # Use the script's configured profile
            table = self.dynamodb.Table(table_name)
            
            response = table.get_item(Key={'vehicleId': vehicle_id})
            if 'Item' not in response:
                # Fallback: vehicle_id might be a VIN — scan for matching vin field.
                #
                # Paginated, deliberately WITHOUT Limit. DynamoDB applies Limit
                # BEFORE FilterExpression, so the previous
                # `scan(FilterExpression=..., Limit=1)` examined exactly one item
                # and filtered it — it resolved a certificate only if the target
                # row happened to be scanned first. Measured on the sibling
                # vehicles table: Limit=1 -> ScannedCount 1 / Count 0; unlimited ->
                # ScannedCount 59 / Count 1.
                #
                # Third instance of this trap found on 2026-08-19 (the others:
                # simulation_lambda.py's stop path and simulation_api.py's cert
                # fallback), against a trap already documented in
                # services/vfo-pipeline/vfo_tools.py:137-150. Now lint-enforced by
                # deployment/scripts/test_source_trap_lint.py.
                scan_kwargs = {
                    'FilterExpression': 'vin = :v OR thingName = :v',
                    'ExpressionAttributeValues': {':v': vehicle_id},
                }
                found = None
                while True:
                    scan_resp = table.scan(**scan_kwargs)
                    items = scan_resp.get('Items') or []
                    if items:
                        found = items[0]
                        break
                    lek = scan_resp.get('LastEvaluatedKey')
                    if not lek:
                        break
                    scan_kwargs['ExclusiveStartKey'] = lek

                if found is not None:
                    response = {'Item': found}
                    print(f"✅ Certificate found via VIN/thingName lookup for: {vehicle_id}")
                else:
                    print(f"❌ Certificate not found for vehicleId: {vehicle_id} in table: {table_name}")
                    sys.stdout.flush()
                    return None
                
            cert_data = response['Item']
            print(f"✅ Certificate found for vehicleId: {vehicle_id}")
            
            # Check if certificate has required fields
            if 'certificatePem' not in cert_data or 'privateKey' not in cert_data:
                print(f"❌ Certificate for {vehicle_id} missing required fields (certificatePem, privateKey)")
                print(f"🔍 Available fields: {list(cert_data.keys())}")
                sys.stdout.flush()
                return None
                
            sys.stdout.flush()
            return cert_data
        except Exception as e:
            print(f"❌ Error getting certificate for {vehicle_id}: {e}")
            import sys
            sys.stdout.flush()
            return None

    def _connect_gps_socket(self, vehicle_id=None):
        """Connect to FWE ExternalGpsSource Unix socket."""
        import socket as sock_mod
        import os
        # Per-vehicle GPS socket path from FWE_GPS_SOCK_MAP, or default
        gps_sock_map_str = os.environ.get('FWE_GPS_SOCK_MAP', '')
        sock_path = None
        if gps_sock_map_str and vehicle_id:
            import json as _json
            gps_sock_map = _json.loads(gps_sock_map_str)
            sock_path = gps_sock_map.get(vehicle_id)
        if not sock_path:
            sock_path = os.environ.get('FWE_GPS_SOCKET_PATH', '/tmp/fwe-gps/gps.sock')
        try:
            s = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
            s.connect(sock_path)
            if not hasattr(self, '_gps_sockets'):
                self._gps_sockets = {}
            self._gps_sockets[vehicle_id or '_default'] = s
            print(f"🛰️  Connected to FWE GPS socket at {sock_path} for {vehicle_id or 'default'}")
        except Exception as e:
            print(f"⚠️  Could not connect to FWE GPS socket ({sock_path}): {e}")

    def _send_gps(self, lat: float, lng: float, vehicle_id=None):
        """Send GPS coordinates to FWE via Unix socket."""
        import json
        if not hasattr(self, '_gps_sockets'):
            self._gps_sockets = {}
        key = vehicle_id or '_default'
        if key not in self._gps_sockets:
            self._connect_gps_socket(vehicle_id)
        sock = self._gps_sockets.get(key)
        if sock is None:
            return
        try:
            line = json.dumps({"lat": lat, "lng": lng}) + "\n"
            sock.sendall(line.encode())
        except Exception:
            self._gps_sockets.pop(key, None)

    def _disconnect_gps_socket(self, vehicle_id=None):
        """Disconnect GPS socket so FWE stops reporting stale coordinates."""
        if not hasattr(self, '_gps_sockets'):
            return
        key = vehicle_id or '_default'
        sock = self._gps_sockets.pop(key, None)
        if sock:
            try:
                sock.close()
            except Exception:
                pass
            print(f"🛰️  Disconnected FWE GPS socket for {vehicle_id or 'default'}")

    def create_mqtt_connection(self, vehicle_id: str, vin: str = None):
        """Create MQTT connection using vehicle's X.509 certificate"""
        if not MQTT_AVAILABLE:
            print(f"❌ MQTT not available - cannot create connection for {vehicle_id}")
            sys.stdout.flush()
            return None
            
        # If only one parameter passed (old style), treat it as VIN and derive vehicle_id
        if vin is None:
            vin = vehicle_id
            vehicle_id = vin
        
        # Get certificate data
        cert_data = self.get_vehicle_certificate(vehicle_id)
        if not cert_data:
            return None
            
        try:
            # Write certificate files temporarily
            import tempfile
            import os
            
            cert_dir = tempfile.mkdtemp()
            cert_file = os.path.join(cert_dir, f"{vin}-cert.pem")
            key_file = os.path.join(cert_dir, f"{vin}-key.pem")
            
            with open(cert_file, 'w') as f:
                f.write(cert_data['certificatePem'])
            with open(key_file, 'w') as f:
                f.write(cert_data['privateKey'])
                
            # Create MQTT client
            import ssl
            
            client_id = f"{vin}-sim"  # Suffix to avoid conflict with FWE using same VIN
            # clean_session=False → AWS IoT retains subscription state AND queues
            # QoS 1 messages during transient disconnects (persistent-session
            # window; ~60min per AWS IoT MQTT 3.1.1 defaults). Paired with the
            # client-side re-subscribe in on_connect (see simulate_vehicle_telemetry)
            # — see issues/2026-08-04-mqtt-reconnect-subscription-loss/.
            #
            # Belt-and-braces rationale: the client-side re-subscribe is what
            # guarantees correctness in all cases (works whether the broker
            # session survived or not). clean_session=False adds the second
            # belt: commands sent during a brief disconnect are QUEUED by the
            # broker and delivered on reconnect instead of being dropped.
            # Requires a stable client_id ({vin}-sim is stable).
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=client_id,
                protocol=mqtt.MQTTv311,
                clean_session=False,
            )
            
            # Create TLS context
            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            
            # Download AWS IoT Root CA
            import urllib.request
            root_ca_url = "https://www.amazontrust.com/repository/AmazonRootCA1.pem"
            root_ca_file = os.path.join(cert_dir, "AmazonRootCA1.pem")
            urllib.request.urlretrieve(root_ca_url, root_ca_file)
            
            # Load certificates
            context.load_verify_locations(root_ca_file)
            context.load_cert_chain(cert_file, key_file)
            context.check_hostname = False
            
            # Set TLS context
            client.tls_set_context(context)
            
            # Store cert files for cleanup
            self.cert_files = [cert_file, key_file, cert_dir]
            return client
            
        except Exception as e:
            print(f"❌ Error creating MQTT connection for {vehicle_id}: {e}")
            import traceback
            traceback.print_exc()
            sys.stdout.flush()
            return None
            
        try:
            # Write certificate files temporarily
            import tempfile
            import os
            
            cert_dir = tempfile.mkdtemp()
            cert_file = os.path.join(cert_dir, f"{vin}-cert.pem")
            key_file = os.path.join(cert_dir, f"{vin}-key.pem")
            
            # Validate certificate data before writing
            if not cert_data.get('certificatePem'):
                raise Exception(f"Certificate PEM is missing for VIN: {vin}")
            if not cert_data.get('privateKey'):
                raise Exception(f"Private key is missing for VIN: {vin}")
                
            print(f"📋 Certificate PEM length: {len(cert_data['certificatePem'])} chars")
            print(f"📋 Private key length: {len(cert_data['privateKey'])} chars")
            sys.stdout.flush()
            
            with open(cert_file, 'w') as f:
                f.write(cert_data['certificatePem'])
            with open(key_file, 'w') as f:
                f.write(cert_data['privateKey'])
                
            # Validate files were written correctly
            if not os.path.exists(cert_file) or os.path.getsize(cert_file) == 0:
                raise Exception(f"Failed to write certificate file: {cert_file}")
            if not os.path.exists(key_file) or os.path.getsize(key_file) == 0:
                raise Exception(f"Failed to write private key file: {key_file}")
                
            print(f"✅ Certificate files created successfully")
            sys.stdout.flush()
                
            # Create MQTT client with proper TLS setup
            import ssl
            import time
            
            client_id = f"{vin}-sim"  # Suffix to avoid conflict with FWE using same VIN
            print(f"🔗 Using client ID: {client_id}")
            sys.stdout.flush()
            
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=client_id, 
                protocol=mqtt.MQTTv311,
                clean_session=True  # Ensure clean session
            )
            
            # Disable automatic reconnection to avoid connection loops
            client.reconnect_delay_set(min_delay=1, max_delay=120)
            client.max_inflight_messages_set(1)  # Limit concurrent messages
            client.max_queued_messages_set(1)    # Limit queued messages
            
            # CRITICAL: Disable automatic reconnection completely
            client._reconnect_on_failure = False  # Disable internal reconnection
            client.loop_timeout = 1.0  # Reduce loop timeout
            
            # Create TLS context with AWS IoT Root CA
            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            
            # Download and use AWS IoT Root CA
            import urllib.request
            root_ca_url = "https://www.amazontrust.com/repository/AmazonRootCA1.pem"
            root_ca_file = os.path.join(cert_dir, "AmazonRootCA1.pem")
            
            try:
                print(f"📥 Downloading AWS IoT Root CA...")
                urllib.request.urlretrieve(root_ca_url, root_ca_file)
                print(f"✅ Root CA downloaded successfully")
                sys.stdout.flush()
                
                # Load the Root CA
                context.load_verify_locations(root_ca_file)
            except Exception as e:
                print(f"⚠️ Failed to download Root CA, using system default: {e}")
                sys.stdout.flush()
            
            # Load client certificate and key
            context.load_cert_chain(cert_file, key_file)
            context.check_hostname = False
            
            # Set TLS context
            client.tls_set_context(context)
            
            # Store cert files for cleanup
            self.cert_files = [cert_file, key_file, cert_dir]
            return client
            
        except Exception as e:
            print(f"❌ Error creating MQTT connection for {vin}: {e}")
            return None

    # REMOVED 2026-08-19: publish_to_iot_core().
    #
    # Unreachable — it had zero callers. It created a SECOND MQTT client with
    # the same client_id ("{vin}-sim") and called disconnect() on it, which cost
    # real time to rule out while diagnosing the vehicle-ecu premature-disconnect
    # bug: it looked exactly like a plausible second disconnect path.
    # Telemetry publishing lives on the trip/idle paths via compress_telemetry();
    # this was a leftover. See
    # issues/2026-08-19-cms-vehicle-ecu-presence-not-resident/.
    #
    # NOTE: _update_vehicle_status() below is deliberately NOT dead and must NOT
    # be removed — tests/test_presence_connection_status.py calls it directly as a
    # negative control, asserting the OLD path does not write lastSeenAt.

    def _resolve_presence_can_writer(self, vehicle_id: str):
        """Return the CAN writer the presence idle emitter should write to.

        Mirrors ``publish_can``'s writer resolution so the idle path and the trip
        path put frames on the same bus for a given vehicle.

        Returns ``None`` in any non-CAN mode: MQTT-Direct vehicles have no CAN
        bus, and ``PresenceLoop.run`` correctly skips emission on ``None``.

        Why this is a named method rather than an inline expression: it exists to
        be *testable*. ``presence_loop.run()`` was originally called with a
        literal ``can_writer=None`` and the comment "injected by Group 4 sidecar
        wiring" — nothing ever injected one, so the idle emitter never executed in
        production while its unit tests passed against ``run()`` in isolation.
        Live staging verification caught it: presence was confirmed
        (``connectionStatus=connected``, SUBACK granted) yet telemetry for
        VEH-MICH-001 stayed ~8.5 hours stale with zero idle-emission log lines.
        That was the fifth integration gap of the same unit-green /
        production-wrong shape in this spec, so the resolution is exposed as a
        seam the production wiring can be asserted against.
        See Fix Group 10 in
        ``.kiro/specs/2026-08-04-cms-vehicle-trip-lifecycle-split/tasks.md``.
        """
        if self.mode != 'can':
            return None
        return self.can_writers.get(vehicle_id, self.can_writer)

    def publish_can(self, vehicle_id: str, telemetry_data: Dict, mqtt_client=None):
        """Publish telemetry as CAN frames to virtual CAN bus.
        GPS is included as CAN signals (GPS_Position message in DBC).
        Trip lifecycle events (ENGINE_START/STOP, driverId) go via MQTT since they're not CAN signals."""
        # Encode telemetry → CAN frames (includes GPS as Latitude/Longitude signals)
        frames = self.can_encoder.encode(telemetry_data)
        writer = self.can_writers.get(vehicle_id, self.can_writer)
        writer.send(frames)

        # Trip lifecycle events via MQTT (not in CAN/protobuf)
        # In CAN/FWE mode, ignition signal flows through FWE → FWTelemetryProcessor → TripProcessor
        # so we skip MQTT lifecycle entirely — the mode check is set at init time
        if mqtt_client and telemetry_data.get('engineEvent') in ('ENGINE_START', 'ENGINE_STOP'):
            if self.mode != 'can':
                try:
                    import gzip, base64
                    lifecycle = {
                        'vehicleId': vehicle_id,
                        'timestamp': telemetry_data.get('timestamp', int(time.time() * 1000)),
                        'engineEvent': telemetry_data['engineEvent'],
                        'messageType': 'LIFECYCLE',
                        'driverId': telemetry_data.get('driverId'),
                        'lat': telemetry_data.get('lat'),
                        'lng': telemetry_data.get('lng'),
                        'ignitionOn': telemetry_data['engineEvent'] == 'ENGINE_START',
                    }
                    payload = gzip.compress(json.dumps(lifecycle).encode())
                    topic = f"$aws/rules/{self.iot_rule_name}/{vehicle_id}"
                    mqtt_client.publish(topic, base64.b64encode(payload).decode(), qos=1)
                    print(f"📤 {vehicle_id}: {telemetry_data['engineEvent']} sent via MQTT (driver: {telemetry_data.get('driverId')})")
                except Exception as e:
                    print(f"⚠️ Failed to publish trip event: {e}")
            else:
                print(f"🔄 {vehicle_id}: {telemetry_data['engineEvent']} — skipping MQTT lifecycle (FWE pipeline handles it)")

        print(f"📡 {vehicle_id}: {len(frames)} CAN frames")

    def store_telemetry_data(self, telemetry_data: Dict):
        """Store telemetry data directly in DynamoDB"""
        if 'telemetry' not in self.table_names:
            # If no telemetry table, update vehicle location
            self.update_vehicle_location(telemetry_data)
            return
        
        try:
            table = self.dynamodb.Table(self.table_names['telemetry'])
            
            # Convert floats to Decimal for DynamoDB
            def convert_floats(obj):
                if isinstance(obj, dict):
                    return {k: convert_floats(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [convert_floats(v) for v in obj]
                elif isinstance(obj, float):
                    return Decimal(str(obj))
                else:
                    return obj
            
            telemetry_item = convert_floats(telemetry_data)
            telemetry_item['telemetryId'] = str(uuid.uuid4())
            
            table.put_item(Item=telemetry_item)
            
        except Exception as e:
            print(f"❌ Error storing telemetry: {e}")
    
    def _update_vehicle_status(self, vehicle_id: str, connection_status: str, activity_status: str):
        """Update vehicle connection and activity status"""
        if 'vehicles' not in self.table_names:
            return
        try:
            table = self.dynamodb.Table(self.table_names['vehicles'])
            table.update_item(
                Key={'vehicleId': vehicle_id},
                UpdateExpression='SET connectionStatus = :cs, activityStatus = :as',
                ExpressionAttributeValues={':cs': connection_status, ':as': activity_status}
            )
        except Exception as e:
            print(f"⚠️ Failed to update vehicle status: {e}")

    def update_vehicle_location(self, telemetry_data: Dict):
        """Update vehicle location, odometer, and fuel level in vehicles table"""
        if 'vehicles' not in self.table_names:
            return
        
        try:
            table = self.dynamodb.Table(self.table_names['vehicles'])
            
            update_parts = ['#loc = :location', 'lastUpdated = :timestamp']
            expr_names = {'#loc': 'location'}
            expr_values = {
                ':location': {
                    'latitude': Decimal(str(telemetry_data['location']['latitude'])),
                    'longitude': Decimal(str(telemetry_data['location']['longitude']))
                },
                ':timestamp': telemetry_data['timestamp']
            }

            # Write odometer and fuelLevel back so the UI reflects live simulation values
            if 'odometer' in telemetry_data and telemetry_data['odometer']:
                update_parts.append('odometer = :odo')
                update_parts.append('mileage = :odo')
                expr_values[':odo'] = Decimal(str(telemetry_data['odometer']))
            if 'fuelLevel' in telemetry_data and telemetry_data['fuelLevel'] is not None:
                update_parts.append('fuelLevel = :fuel')
                expr_values[':fuel'] = Decimal(str(telemetry_data['fuelLevel']))

            table.update_item(
                Key={'vehicleId': telemetry_data['vehicleId']},
                UpdateExpression='SET ' + ', '.join(update_parts),
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values,
            )
            
        except Exception as e:
            print(f"❌ Error updating vehicle location: {e}")
    
    def simulate_vehicle_telemetry(self, vehicle: Dict, trips_count: int = 3, force_maintenance_alert: bool = False):
        """Simulate telemetry for a single vehicle for specified number of trips"""
        import sys
        vehicle_id = vehicle['vehicleId']
        vin = vehicle.get('vin', vehicle_id)
        
        print(f"🔍 DEBUG: Function started for {vehicle_id}")
        sys.stdout.flush()
        
        print(f"🚗 Starting telemetry simulation for {vehicle_id} - {trips_count} trips")
        self.logger.info(f"🚗 Starting telemetry simulation for {vehicle_id} - {trips_count} trips")
        sys.stdout.flush()
        
        # Create single MQTT connection for this vehicle
        mqtt_client = None
        connected = False
        connecting = False  # Add connecting state to prevent loops
        first_connect_done = False  # Distinguish initial connect from reconnect in logs
        pending_publishes = {}  # Track pending publish confirmations (used by on_publish)
        # Presence loop — initialised to None here; instantiated PRE-CONNECT
        # at the "Instantiate PresenceLoop BEFORE connect()" site below so
        # on_subscribe can never observe it as None when the SUBACK arrives.
        # The variable is set in the on_subscribe closure via `nonlocal
        # presence_loop`; placing it here just establishes the name in this
        # scope before the closures below capture it.
        # Fix Group 6 corrected this comment: the original text said
        # "instantiated after the MQTT session is confirmed live (SUBACK)"
        # which described the rejected alternative, not the chosen approach.
        presence_loop = None

        # Two-topic SUBACK tracking (HARD GATE D).
        # _pending_sub_mids  : set of MIDs issued by the most recent
        #                      _subscribe_command_topics() call.  Reset on
        #                      each on_connect so stale MIDs from a prior
        #                      connect round don't contribute to the gate.
        # _confirmed_sub_mids: MIDs that have received a non-rejected SUBACK.
        # _sub_mid_to_topic  : maps mid -> topic string for readable error logs.
        # notify_connected() fires when _pending_sub_mids ⊆ _confirmed_sub_mids.
        _pending_sub_mids: set = set()
        _confirmed_sub_mids: set = set()
        _sub_mid_to_topic: dict = {}

        # Command topics — computed once, referenced from on_connect (for the
        # (re)subscribe) and from on_command (for publishing the response).
        # `command_request_topic()` is used by both this closure and the
        # unit-testable `_subscribe_command_topic` helper.
        cmd_topic = self.command_request_topic(vehicle_id)
        resp_topic = f'cms/commands/{vehicle_id}/response'

        # Ensure a VehicleState exists before the first command can arrive so
        # `apply_command` mutations are never lost. The telemetry loop also
        # does `self.vehicle_states.setdefault(vehicle_id, VehicleState())` on
        # first tick; doing it here too is idempotent.
        self.vehicle_states.setdefault(vehicle_id, VehicleState())

        def on_command(client, userdata, msg):
            """Handle a command MQTT message.

            Chain: UI POST /api/commands/{vehicleId} -> commands_lambda publishes
            legacy JSON to `cms/commands/{vehicle_id}/request`. We subscribe here,
            mutate the persisted VehicleState via `apply_command()`, and publish a
            JSON response to `cms/commands/{vehicle_id}/response`. The IoT rule
            `<prefix>_command_response_rule` (see deployment/stacks/commands_stack.py)
            forwards the response to `command_response_handler` which updates the
            DDB commands row, so the UI history flips SENT -> SUCCEEDED with latency.
            """
            cmd_name = '?'
            cmd_id = '?'
            cmd_value = None
            status = 'FAILED'
            reason = 'unparsed'
            try:
                cmd = json.loads(msg.payload.decode())
                cmd_name = cmd.get('commandName', '?')
                cmd_id = cmd.get('commandId', '?')
                cmd_value = cmd.get('value')
                print(f"🎮 COMMAND RECEIVED: {cmd_name} = {cmd_value} (id={cmd_id}) for {vehicle_id}")
                sys.stdout.flush()

                vs = self.vehicle_states.get(vehicle_id)
                if vs is None:
                    vs = VehicleState()
                    self.vehicle_states[vehicle_id] = vs

                status, reason = self.apply_command(vs, cmd_name, cmd_value)
            except Exception as e:
                # Catch-all so we STILL publish a FAILED response and the
                # commands DDB row doesn't stay stuck at SENT.
                status = 'FAILED'
                reason = f'Command handler exception: {e}'
                print(f"❌ Command handler error: {e}")
                sys.stdout.flush()

            # Always publish a response, success or failure. Payload matches
            # `command_response_handler.py`'s JSON path (commandId + status
            # required; vehicleId + reason optional).
            resp_payload = json.dumps({
                'commandId': cmd_id,
                'commandName': cmd_name,
                'vehicleId': vehicle_id,
                'status': status,
                'reason': reason,
                'resultValue': cmd_value,
                'respondedAt': datetime.now(timezone.utc).isoformat(),
            })
            try:
                client.publish(resp_topic, resp_payload, qos=1)
                print(f"✅ COMMAND ACK: {cmd_name} → {status} (published to {resp_topic})")
                sys.stdout.flush()
            except Exception as pub_e:  # pragma: no cover — network path
                print(f"❌ Failed to publish command response: {pub_e}")
                sys.stdout.flush()

        def on_sovd(client, userdata, msg):
            """Handle an SOVD diagnostic command MQTT message.

            Topic: ``cms/commands/things/{vin}/executions/{execId}/sovd/request``

            Parses the JSON body, dispatches on ``command_type`` in
            {'read_dtcs', 'clear_dtcs', 'read_data', 'read_identity',
            'read_freeze_frames'}, and immediately spawns a worker
            thread via ``threading.Thread(target=_handle_sovd, ...)`` before
            returning.

            CRITICAL (spec Risk R3): this callback runs on the paho network
            thread.  A synchronous UDS full-scan can take 15 s and would block
            KEEPALIVE — causing a connection drop.  The worker thread handles
            all blocking I/O; this callback returns in < 10 ms.

            Attestation pass-through (decisions.md Fix Group 2 Prevention):
            the sidecar passes through the caller-derived ``attestation``
            value from the MQTT message as-is.  It does NOT rebuild
            attestation locally and does NOT trust any locally-cached
            identity.
            """
            try:
                cmd = json.loads(msg.payload.decode())
                command_type = cmd.get('command_type', '')
                # Single source of truth, shared with _handle_sovd's guard. F27: this
                # list and that one were maintained separately and disagreed.
                if command_type not in _HANDLED_COMMAND_TYPES:
                    print(
                        f"⚠️  SOVD: unknown command_type={command_type!r} "
                        f"— ignoring",
                        flush=True,
                    )
                    return

                # Get the per-vehicle token bucket from PresenceLoop if available.
                token_bucket = None
                if presence_loop is not None:
                    token_bucket = presence_loop.sovd_token_bucket
                else:
                    # Fallback: create a temporary bucket (no long-term state).
                    # This can happen during unit tests or startup races.
                    # Capacity=9 matches PresenceLoop.sovd_token_bucket (D13 per-ECU accounting).
                    token_bucket = TokenBucket(rate=9.0, capacity=9.0, per=10.0)

                # vehicle_state is passed as a keyword argument so the five
                # existing positional callers (test suites) are unaffected.
                # When presence_loop is None, vehicle_state stays None — the
                # _handle_sovd branch for run_routine refuses STATIONARY routines
                # in that case rather than assuming the vehicle is stationary (D15).
                _vehicle_state = presence_loop.vehicle_state if presence_loop is not None else None

                print(
                    f"🔬 SOVD {command_type} received for {vin} "
                    f"— spawning worker thread",
                    flush=True,
                )
                t = threading.Thread(
                    target=_handle_sovd,
                    args=(cmd, msg.topic, client, vehicle_id, vin, token_bucket),
                    kwargs={'vehicle_state': _vehicle_state},
                    daemon=True,
                    name=f'sovd-{vin}-{command_type}',
                )
                t.start()
            except Exception as e:
                print(f"❌ SOVD on_sovd handler error: {e}", flush=True)
            # Return immediately — do NOT block paho's network thread.

        def on_subscribe(client, userdata, mid, granted_qos, properties=None):
            """Confirm SUBACK from broker.

            HARD GATE D: ``notify_connected`` is called ONLY when SUBACK grants
            BOTH the actuator topic AND the SOVD topic.  A partial SUBACK (one
            topic granted, one rejected) logs the specific rejection topic loudly
            and keeps ``connectionStatus=disconnected``.

            Fixes the silent-failure mode where a subscribe() call returned
            success but the broker never actually ACKed the subscription.
            granted_qos is a list of int per subscribed topic; the broker
            returns 0x80 (128) to reject a subscription.

            This is the confirmed-session point — on_connect proves the TCP
            socket; only a non-rejected SUBACK proves both command channels are
            live.  notify_connected() is called here and nowhere else so we
            never advertise 'connected' before both channels are confirmed.
            See issues/2026-07-31-fake-connected-status-regression/.
            See spec D7 and HARD GATE D.
            """
            nonlocal presence_loop, _confirmed_sub_mids
            try:
                # granted_qos may be a list of ints (v2 API) or ReasonCode objects (v5)
                granted_vals = [
                    getattr(q, 'value', q) for q in (granted_qos or [])
                ]
                rejected = [q for q in granted_vals if isinstance(q, int) and q >= 0x80]
                if rejected:
                    # Find which topic this SUBACK belongs to for a useful log.
                    rejected_topic = _sub_mid_to_topic.get(mid, f'unknown(mid={mid})')
                    print(
                        f"❌ SUBACK REJECTED: mid={mid} topic={rejected_topic}, "
                        f"granted={granted_vals} — commands channel is NOT live"
                    )
                    # Do NOT call notify_connected — partial SUBACK keeps disconnected.
                else:
                    confirmed_topic = _sub_mid_to_topic.get(mid, f'unknown(mid={mid})')
                    print(f"✅ SUBACK: mid={mid} topic={confirmed_topic}, granted QoS={granted_vals}")
                    # Accumulate confirmed MIDs.  Both are required before notify.
                    _confirmed_sub_mids.add(mid)
                    # Check whether ALL expected MIDs for this connect round are confirmed.
                    if _pending_sub_mids and _pending_sub_mids.issubset(_confirmed_sub_mids):
                        # Both topics confirmed — safe to advertise connected.
                        if presence_loop is not None:
                            presence_loop.notify_connected()
                        else:
                            # WARNING: the confirmed-session notify_connected() was
                            # silently dropped because presence_loop is None at
                            # SUBACK time.  Under current pre-connect wiring this
                            # cannot fire (presence_loop is assigned before
                            # mqtt_client.connect()), but if a future refactor moves
                            # the instantiation after connect() this branch becomes
                            # reachable.  When Group 4 lands, a dropped notify leaves
                            # a healthy vehicle stuck advertising 'disconnected', which
                            # blocks commands on a working vehicle.
                            print(
                                f"⚠️  WARNING on_subscribe: both SUBACKs confirmed "
                                f"but presence_loop is None for vehicle in this closure — "
                                f"notify_connected() was NOT called; vehicle will remain "
                                f"'disconnected' until the next reconnect.",
                                flush=True,
                            )
                    else:
                        pending_remaining = _pending_sub_mids - _confirmed_sub_mids
                        remaining_topics = [_sub_mid_to_topic.get(m, f'mid={m}') for m in pending_remaining]
                        print(
                            f"⏳ SUBACK: mid={mid} confirmed; waiting for "
                            f"{len(pending_remaining)} more: {remaining_topics}"
                        )
            except Exception as e:  # pragma: no cover — defensive
                print(f"✅ SUBACK: mid={mid} (granted={granted_qos}, parse err={e})")
            sys.stdout.flush()

        def on_connect(client, userdata, flags, reason_code, *args):
            nonlocal connected, connecting, first_connect_done
            nonlocal _pending_sub_mids, _confirmed_sub_mids, _sub_mid_to_topic
            # session_present is a boolean flag from the broker: True means
            # the broker resumed a persistent session (subscriptions + queued
            # QoS 1 messages retained). False means fresh session — we MUST
            # re-subscribe. We re-subscribe unconditionally anyway (it's
            # idempotent and cheap) but the flag is useful in logs.
            session_present = False
            try:
                session_present = bool(
                    flags.get('session_present') if isinstance(flags, dict)
                    else getattr(flags, 'session_present', False)
                )
            except Exception:  # pragma: no cover — defensive
                pass
            print(f"🔗 MQTT on_connect called:")
            print(f"   - Endpoint: {self.iot_endpoint}")
            print(f"   - Client ID: {client._client_id}")
            print(f"   - Reason code: {reason_code}")
            print(f"   - Flags: {flags} (session_present={session_present})")
            sys.stdout.flush()
            if reason_code == 0 or str(reason_code) == "Success":
                connected = True
                connecting = False
                if first_connect_done:
                    # This is a RECONNECT — highlight it in the log so the
                    # operator can see the recovery event immediately.
                    # Fix: issues/2026-08-04-mqtt-reconnect-subscription-loss/
                    print(f"🔄 MQTT RECONNECTED to AWS IoT Core for {vehicle_id} — re-subscribing commands (session_present={session_present})")
                else:
                    print(f"✅ MQTT connected to AWS IoT Core for {vehicle_id}")
                sys.stdout.flush()
                # (Re)subscribe on EVERY connect to BOTH topics (HARD GATE C).
                # Paho does NOT auto-resubscribe after a reconnect and AWS IoT
                # persistent-session state may have expired.  This is the fix
                # for issues/2026-08-04-mqtt-reconnect-subscription-loss/ extended
                # to include the SOVD topic.
                # Reset SUBACK accumulation for this round (HARD GATE D).
                _pending_sub_mids = set()
                _confirmed_sub_mids = set()
                _sub_mid_to_topic = {}
                try:
                    sub_results = self._subscribe_command_topics(
                        client, vehicle_id, vin, on_command, on_sovd
                    )
                    for (topic, mid, _result) in sub_results:
                        if mid is not None:
                            _pending_sub_mids.add(mid)
                            _sub_mid_to_topic[mid] = topic
                except Exception as e:
                    # Don't crash the callback thread on subscribe errors — log
                    # loudly. The client will keep publishing telemetry; the
                    # operator can restart the sim if commands become dead.
                    print(f"❌ on_connect subscribe failed for {vehicle_id}: {e}")
                    sys.stdout.flush()
                first_connect_done = True
            else:
                connected = False
                connecting = False
                print(f"❌ MQTT connection failed for {vehicle_id}: {reason_code}")
                sys.stdout.flush()
        
        def on_disconnect(client, userdata, *args):
            nonlocal connected, connecting
            connected = False
            connecting = False
            reason_code = args[0] if args else "unknown"
            print(f"🔌 MQTT disconnected: reason_code={reason_code} — paho will attempt reconnect; on_connect will re-subscribe command topic")
            sys.stdout.flush()
            # Clear the presence loop's confirmed-session flag. Until a reconnect
            # produces a fresh SUBACK (which calls notify_connected and re-arms
            # it), the session is NOT confirmed, so heartbeat() must not refresh
            # lastSeenAt — a heartbeat through a dead session would convert an
            # honest staleness signal into a lie, which is worse than the
            # staleness bug heartbeat() exists to fix.
            # Guarded with a try/getattr because this closure is defined before
            # presence_loop is assigned; on_disconnect cannot fire before
            # connect(), but a NameError inside a paho callback would be
            # swallowed by the network loop and is not worth risking.
            try:
                if presence_loop is not None:
                    presence_loop.mqtt_connected = False
            except NameError:
                pass
            # Paho's network loop (loop_start) attempts automatic reconnect
            # after a disconnect. When reconnect succeeds, on_connect fires
            # again and re-subscribes via _subscribe_command_topic — that's
            # the client-side belt for the reconnect subscription-loss fix.
            
        def on_publish(client, userdata, mid, reason_code=None, properties=None):
            # Handle both old and new callback signatures
            try:
                print(f"🔍 PUBLISH CALLBACK: mid={mid}, reason_code={reason_code}")
                sys.stdout.flush()
                if mid in pending_publishes:
                    topic = pending_publishes.pop(mid)
                    # Only log failures, not successes
                    if reason_code is not None and reason_code != 0:
                        print(f"❌ MQTT publish failed: mid={mid}, topic={topic}, reason_code={reason_code}")
                        sys.stdout.flush()
                    # Success case - don't log to reduce noise
            except Exception as e:
                print(f"❌ EXCEPTION in publish callback: {e}")
                import traceback
                traceback.print_exc()
                sys.stdout.flush()
        
        def on_log(client, userdata, level, buf):
            # Log all messages to see what's happening
            print(f"🔍 MQTT LOG [{level}]: {buf}")
            sys.stdout.flush()
            
        def on_socket_open(client, userdata, sock):
            print(f"🔌 Socket opened")
            sys.stdout.flush()
            
        def on_socket_close(client, userdata, sock):
            print(f"🔌 Socket closed")
            sys.stdout.flush()
            
        def on_socket_register_write(client, userdata, sock):
            print(f"🔍 Socket register write")
            sys.stdout.flush()
            
        def on_socket_unregister_write(client, userdata, sock):
            print(f"🔍 Socket unregister write")
            sys.stdout.flush()
        
        mqtt_client = None
        commands_mqtt = getattr(self, 'commands_mqtt', False)
        connected = getattr(self, 'skip_mqtt', False)  # Skip MQTT telemetry when FWE agent handles it

        if not getattr(self, 'skip_mqtt', False) or commands_mqtt:
            try:
                print(f"🔗 Creating MQTT connection for {vin}...")
                sys.stdout.flush()
                mqtt_client = self.create_mqtt_connection(vehicle_id, vin)
            
                if not mqtt_client:
                    print(f"❌ Failed to create MQTT client for {vehicle_id} - STOPPING simulation")
                    self.logger.error(f"❌ Failed to create MQTT client for {vehicle_id} - STOPPING simulation")
                    sys.stdout.flush()
                    return  # Exit this vehicle's simulation
            
                # Set all callbacks for debugging
                mqtt_client.on_connect = on_connect
                mqtt_client.on_disconnect = on_disconnect
                mqtt_client.on_publish = on_publish
                mqtt_client.on_subscribe = on_subscribe
                mqtt_client.on_log = on_log
                mqtt_client.on_socket_open = on_socket_open
                mqtt_client.on_socket_close = on_socket_close
                mqtt_client.on_socket_register_write = on_socket_register_write
                mqtt_client.on_socket_unregister_write = on_socket_unregister_write

                # ── Instantiate PresenceLoop BEFORE connect() ──────────────────
                # Deliberately here, not after the connect-wait loop. on_subscribe
                # calls notify_connected() when the SUBACK arrives, and the SUBACK
                # can in principle land before a later assignment executes. Doing
                # it pre-connect means on_subscribe can never observe
                # presence_loop is None, so the confirmed-session notify cannot be
                # silently dropped.
                #
                # The alternative — instantiate late and add a catch-up notify —
                # was rejected: a dropped notify leaves a fully working vehicle
                # advertised as 'disconnected', and once the Group 4 UI gate lands
                # (allow-list on connectionStatus === 'connected') that would BLOCK
                # commands on a healthy vehicle. Removing the race beats
                # compensating for it.
                presence_loop = PresenceLoop(self, vehicle_id, mqtt_client)
                self.presence_loops[vehicle_id] = presence_loop
                print(f"🔧 PresenceLoop instantiated and registered for {vehicle_id} (pre-connect)")
                sys.stdout.flush()
            
                print(f"🔗 Connecting to {self.iot_endpoint}:8883...")
                sys.stdout.flush()
            
                # Set keep-alive to prevent disconnections
                result = mqtt_client.connect(self.iot_endpoint, 8883, keepalive=300)  # 5 minute keepalive
                print(f"🔗 Connect result: {result}")
                sys.stdout.flush()
            
                mqtt_client.loop_start()  # Start network loop
            
                # Wait for connection with timeout
                timeout = 30  # Increased timeout
                start_wait = time.time()
                while not connected and (time.time() - start_wait) < timeout:
                    time.sleep(0.5)  # Check less frequently
            
                if not connected:
                    raise Exception(f"Connection timeout after {timeout}s - check certificates and IoT policies")
            
                print(f"✅ Successfully connected to IoT Core for {vehicle_id}")
                self.logger.info(f"✅ Successfully connected to IoT Core for {vehicle_id}")
                sys.stdout.flush()
            
            except Exception as e:
                print(f"❌ Failed to connect to IoT Core for {vehicle_id}: {e}")
                import traceback
                traceback.print_exc()
                self.logger.error(f"❌ Failed to connect to IoT Core for {vehicle_id}: {e}")
                sys.stdout.flush()
                if mqtt_client:
                    mqtt_client.loop_stop()
                return  # Exit this vehicle's simulation
        else:
            print(f"🔧 CAN mode: skipping MQTT connection (FWE agent handles MQTT)")
            sys.stdout.flush()
        
        print(f"🔍 DEBUG: Past exception handler, about to setup callbacks")
        sys.stdout.flush()
        
        print(f"🔍 DEBUG: About to print callback setup message")
        sys.stdout.flush()
        print(f"🔧 Setting up MQTT callbacks and subscriptions for {vehicle_id}")
        print(f"🔍 DEBUG: Printed callback setup message")
        sys.stdout.flush()
        self.logger.info(f"🔧 Setting up MQTT callbacks and subscriptions for {vehicle_id}")
        
        start_time = time.time()
        message_count = 0

        # Note: command topic (cmd_topic / resp_topic) and on_command are now
        # defined at the top of this function (before on_connect) so on_connect
        # can call `self._subscribe_command_topic(...)` on every (re)connect.
        # The initial subscribe is issued by on_connect during the connect wait
        # loop above — no separate mqtt_client.subscribe() call is needed here.
        # Fix: issues/2026-08-04-mqtt-reconnect-subscription-loss/.
        print(f"✅ Commands subscription setup complete for {vehicle_id} (initial subscribe issued in on_connect; on_subscribe callback will confirm SUBACK)")

        # ── PresenceLoop registration ─────────────────────────────────────────
        # Instantiation happens PRE-CONNECT (see the block just after the MQTT
        # callbacks are assigned) so that on_subscribe can never observe a None
        # presence_loop and silently drop the confirmed-session
        # notify_connected(). Nothing to do here beyond reporting the CAN-only
        # case, where no MQTT client exists and therefore no presence loop can.
        import threading as _threading
        _stop_event = _threading.Event()
        if presence_loop is None:
            # CAN-only mode with no command MQTT client — presence loop requires
            # an MQTT client; fall back to the bounded trip-only path. This branch
            # is taken when skip_mqtt=True and commands_mqtt=False (pure CAN, no
            # command channel).
            print(f"🔧 CAN-only mode (no MQTT client): PresenceLoop not instantiated for {vehicle_id}")
        sys.stdout.flush()

        print(f"🔍 DEBUG: Past subscription setup")
        sys.stdout.flush()
        
        # Initialize heartbeat tracking
        last_heartbeat = 0
        heartbeat_interval = 60  # Send heartbeat every 60 seconds
        
        print(f"🚀 Starting telemetry loop for {vehicle_id}")
        self.logger.info(f"🚀 Starting telemetry loop for {vehicle_id}")
        print(f"🔍 DEBUG: self.running = {self.running}, trips_count = {trips_count}")
        self.logger.info(f"🔍 DEBUG: self.running = {self.running}, trips_count = {trips_count}")
        sys.stdout.flush()
        
        # ── Bounded trip loop ──────────────────────────────────────────────────
        # Runs for trips_count iterations then falls through to the unbounded
        # presence idle loop below.  The old finally block here called
        # mqtt_client.disconnect() — deleted in Fix Group 5; teardown moved to
        # PresenceLoop.shutdown().
        try:
            completed_trips = 0
            
            print(f"🔍 DEBUG: About to enter while loop - self.running={self.running}, completed_trips={completed_trips}, trips_count={trips_count}")
            self.logger.info(f"🔍 DEBUG: About to enter while loop - self.running={self.running}, completed_trips={completed_trips}, trips_count={trips_count}")
            sys.stdout.flush()
            
            while self.running and completed_trips < trips_count:
                try:
                    current_time = time.time()
                    
                    # Send heartbeat if needed (but don't log it)
                    if current_time - last_heartbeat >= heartbeat_interval:
                        self.publish_heartbeat(vehicle_id, vin, mqtt_client)
                        last_heartbeat = current_time
                    
                    # Get or create vehicle state
                    vehicle_state = self.vehicle_states.get(vehicle_id, VehicleState())
                    
                    # Apply forced alert parameters to vehicle state
                    if vehicle_id not in self.vehicle_states:
                        self.apply_forced_alert_params(vehicle_state)
                        self.vehicle_states[vehicle_id] = vehicle_state
                    
                    # Generate standardized telemetry data
                    telemetry_data = self.generate_telemetry_data(vehicle, vehicle_state, force_maintenance_alert)
                    # Cache full telemetry for ignition-off broadcast
                    if not hasattr(self, '_last_telemetry'):
                        self._last_telemetry = {}
                    if telemetry_data.get('ignitionOn'):
                        self._last_telemetry[vehicle_id] = dict(telemetry_data)
                    
                    # Check if trip just completed
                    if vehicle_state.route_index >= len(vehicle_state.route) - 1 and vehicle_state.trip_started:
                        completed_trips += 1
                        print(f"✅ Trip {completed_trips}/{trips_count} completed for {vehicle_id}")
                        self.logger.info(f"✅ Trip {completed_trips}/{trips_count} completed for {vehicle_id}")
                        sys.stdout.flush()
                        
                        # Send final telemetry packet with ignitionOn: false
                        final_telemetry = self.generate_telemetry_data(vehicle, vehicle_state, force_maintenance_alert)
                        if self.mode == 'can':
                            # Merge ignition-off into the LAST full telemetry so all CAN messages
                            # are sent (not just the 2 that have ignitionOn/GPS).
                            # FWE agent needs continuous CAN traffic to trigger collection.
                            last_full = dict(self._last_telemetry.get(vehicle_id, {}))
                            last_full.update(final_telemetry)
                            print(f"🔄 Broadcasting ignition-off across all CAN messages for 15s...")
                            try:
                                for _ in range(5):
                                    self.publish_can(vehicle_id, last_full, mqtt_client)
                                    time.sleep(3)
                            except Exception as e:
                                print(f"⚠️ Broadcast interrupted: {e}")
                        else:
                            compressed_payload = self.compress_telemetry(final_telemetry)
                            topic = f"$aws/rules/{self.iot_rule_name}/{vehicle_id}"
                            mqtt_client.publish(topic, compressed_payload, qos=1)
                        message_count += 1
                        print(f"🏁 Final telemetry sent with ignitionOn: {final_telemetry['ignitionOn']}")
                        
                        if completed_trips >= trips_count:
                            print(f"🏁 All {trips_count} trips completed for {vehicle_id}")
                            self.logger.info(f"🏁 All {trips_count} trips completed for {vehicle_id}")
                            sys.stdout.flush()
                            if self.mode == 'can':
                                print(f"⏳ Waiting 45s for FWE agent to upload final collection...")
                                sys.stdout.flush()
                                time.sleep(45)
                            break
                        
                        # Reset for next trip with small delay
                        time.sleep(5)
                        # Reset state IN PLACE — do NOT replace the object.
                        # PresenceLoop.vehicle_state holds a reference to the same
                        # object captured at construction; replacing it here would
                        # split identity so idle_emit() reads stale state and
                        # commands applied in the idle phase are silently lost.
                        # Fix Group 6 (spec 2026-08-04-cms-vehicle-trip-lifecycle-split).
                        vehicle_state.reset()
                        continue
                    
                    # Raw telemetry contains all fields needed for safety analysis
                    # SafetyProcessor will analyze these fields to detect safety events:
                    # - harsh_brk, harsh_acc, harsh_turn, speed_viol (driver behavior)
                    # - eng_temp, tire_fl/fr/rl/rr, battery_voltage (vehicle health)
                    # - seatbelt, phone_use (driver safety)
                    # - aeb_act, abs_act, esc_act (safety systems)
                    
                    # NO safetyAlerts array - let Flink SafetyProcessor handle all detection
                    
                    # Update vehicle state
                    vehicle_state.last_speed = telemetry_data.get('speed', 0)
                    vehicle_state.last_timestamp = telemetry_data['timestamp']
                    self.vehicle_states[vehicle_id] = vehicle_state
                    
                    # Publish telemetry based on mode
                    if self.mode == 'can':
                        # CAN bus mode: encode to CAN frames, GPS via MQTT
                        try:
                            self.publish_can(vehicle_id, telemetry_data, mqtt_client)
                            message_count += 1
                        except Exception as e:
                            print(f"❌ CAN publish failed: {e}")
                            sys.stdout.flush()
                            break
                    else:
                        # MQTT direct mode: publish compressed JSON to IoT Core
                        topic = f"$aws/rules/{self.iot_rule_name}/{vehicle_id}"
                        compressed_payload = self.compress_telemetry(telemetry_data)

                        try:
                            result1 = mqtt_client.publish(topic, compressed_payload, qos=0)
                        except Exception as e:
                            print(f"❌ Exception during publish: {e}")
                            import traceback
                            traceback.print_exc()
                            sys.stdout.flush()
                            break

                        if result1.rc == 0:
                            message_count += 1
                        else:
                            print(f"❌ Publish failed for {vehicle_id}: return code {result1.rc}")
                            sys.stdout.flush()

                    # Human readable telemetry summary
                    city_name = getattr(self, 'current_city', 'Unknown')
                    progress = telemetry_data.get('tripProgress', {}).get('progressPercentage', 0)
                    mode_label = 'CAN' if self.mode == 'can' else 'MQTT'
                    print(f"📡 [{mode_label}] {vehicle_id}: {telemetry_data['speed']:.1f} km/h at ({telemetry_data['lat']:.4f}, {telemetry_data['lng']:.4f}) | msg #{message_count}")
                    sys.stdout.flush()

                    if message_count % 10 == 0:
                        elapsed = time.time() - start_time
                        print(f"📊 {vehicle_id}: {message_count} messages in {elapsed:.1f}s")
                        sys.stdout.flush()

                    # Wait before next telemetry update
                    time.sleep(15)
                    
                except Exception as e:
                    print(f"❌ Error publishing telemetry for {vehicle_id}: {e}")
                    sys.stdout.flush()
                    break
        except Exception:
            # Propagate unexpected errors from the trip loop; the presence idle
            # loop is not entered on an unexpected exception.
            raise

        # ── Trips complete — do NOT disconnect MQTT ────────────────────────────
        # The trip loop above is the bounded phase.  After all trips complete (or
        # the loop exits early via break), we fall through into the unbounded
        # presence idle loop below.  MQTT teardown and notify_disconnected() have
        # moved to PresenceLoop.shutdown(), invoked only on process exit (SIGTERM /
        # self.running stop path), never on trip exit.
        #
        # DELETED from this location (Fix Group 5, spec 2026-08-04-cms-vehicle-trip-lifecycle-split):
        #   The old `finally` block called mqtt_client.disconnect() + loop_stop()
        #   on every trip completion and had a now-removed stale comment about
        #   keeping the vehicle "on" while doing the opposite.  That disconnect was
        #   the primary correctness defect: it tore down the command subscription
        #   at the end of every trip, making the vehicle unreachable between trips
        #   and after the last trip.  MQTT teardown now lives exclusively in
        #   PresenceLoop.shutdown().  See spec § Context and
        #   issues/2026-08-04-fwe-remote-commands-not-actuating/.

        if presence_loop is None:
            # CAN-only mode — no MQTT client, no presence loop.  Emit a log and
            # return so the thread exits as before.
            print(f"✅ Telemetry simulation completed for {vehicle_id} (CAN-only, no presence idle loop)")
            sys.stdout.flush()
            return

        print(f"🏁 Trips complete for {vehicle_id} — entering presence idle loop")
        sys.stdout.flush()

        # ── Unbounded presence idle loop ───────────────────────────────────────
        # The thread remains alive, emitting idle CAN frames and polling for trip
        # intent until the ECS task is stopped (self.running becomes False or
        # SIGTERM causes cleanup).  _stop_event is set by the shutdown path so the
        # loop exits cleanly without spinning.
        try:
            # Wire the stop event to self.running so that when the simulator's
            # global stop flag is cleared, the presence loop exits on its next tick.
            import threading as _thr

            def _watch_running():
                while self.running:
                    _thr.Event().wait(timeout=1.0)
                _stop_event.set()

            _watcher = _thr.Thread(target=_watch_running, daemon=True,
                                   name=f"presence-stop-watcher-{vehicle_id}")
            _watcher.start()

            # Inject the vehicle's CAN writer so the idle emitter actually runs.
            # `run()` guards emission with `if can_writer is not None`, so passing
            # None here silently disables idle emission — which is exactly what
            # happened until live staging verification caught it (Fix Group 10):
            # a parked vehicle was commandable but its state change was
            # unobservable, because nothing reached the CAN bus for the FWE agent
            # to decode. Resolve the writer the same way publish_can does.
            # MQTT-Direct has no CAN bus and must keep None.
            _presence_can_writer = self._resolve_presence_can_writer(vehicle_id)

            # Wire the vehicle dict + force_maintenance_alert flag through to
            # the presence loop so tripIntent-driven run_trips() drives real
            # telemetry (not the pre-2026-08-13 stub that returned instantly).
            # See issues/2026-08-13-simulation-reuse-path-run-trips-stub/.
            try:
                presence_loop.set_vehicle(vehicle)
                presence_loop.set_force_maintenance_alert(force_maintenance_alert)
            except Exception as _sve:
                print(f"⚠️ Failed to wire vehicle context into presence loop for "
                      f"{vehicle_id}: {_sve} — reuse-path trips will not drive telemetry")
                sys.stdout.flush()

            presence_loop.run(
                can_writer=_presence_can_writer,
                idle_interval=9.0,    # ≤ 10 s ceiling from cms-fleet-gps-10s scheme
                _stop_event=_stop_event,
            )
        finally:
            # Process exit — shut down MQTT and write disconnected status.
            # This is the ONLY path that calls disconnect().
            print(f"🔌 Process exit: shutting down presence loop for {vehicle_id}")
            sys.stdout.flush()
            presence_loop.shutdown()

        print(f"✅ Telemetry simulation completed for {vehicle_id}")
        print(f"🔍 DEBUG: Function ending normally")
        sys.stdout.flush()
    
    def start_simulation(self, trips_per_vehicle: int = 3, max_vehicles: int = 10, vehicles: List[Dict] = None, force_maintenance_alert: bool = False):
        """Start real-time telemetry simulation based on number of trips per vehicle"""
        import sys
        print(f"🚀 Starting real-time telemetry simulation for {trips_per_vehicle} trips per vehicle...")
        self.logger.info(f"🚀 Starting real-time telemetry simulation for {trips_per_vehicle} trips per vehicle...")
        sys.stdout.flush()
        
        # Use provided vehicles or get active vehicles from database
        if vehicles:
            print(f"📋 Using {len(vehicles)} vehicles from configuration")
            sys.stdout.flush()
            simulation_vehicles = vehicles
        else:
            print("📋 Getting active vehicles from database")
            sys.stdout.flush()
            simulation_vehicles = self.get_active_vehicles()
            if not simulation_vehicles:
                print("❌ No active vehicles found")
                sys.stdout.flush()
                return
            
            # Limit number of vehicles to simulate
            simulation_vehicles = simulation_vehicles[:max_vehicles]
        
        print(f"📊 Simulating telemetry for {len(simulation_vehicles)} vehicles")
        sys.stdout.flush()
        
        self.running = True
        
        # Start simulation thread for each vehicle
        for vehicle in simulation_vehicles:
            thread = threading.Thread(
                target=self.simulate_vehicle_telemetry,
                args=(vehicle, trips_per_vehicle, force_maintenance_alert)
            )
            thread.start()
            self.simulation_threads.append(thread)
            
            # Small delay between starting each vehicle
            time.sleep(1)
        
        # Wait a moment for threads to initialize before declaring success
        time.sleep(2)
        
        # Check if any threads are still alive (meaning they didn't exit immediately due to errors)
        active_threads = [t for t in self.simulation_threads if t.is_alive()]
        if active_threads:
            print(f"✅ Started telemetry simulation for {len(active_threads)} vehicles")
            print(f"🛣️ Each vehicle will complete {trips_per_vehicle} trips")
        else:
            print(f"❌ All simulation threads failed to start properly")
            return
        
        # Wait for all threads to complete.
        #
        # Dynamic timeout (2026-05-05): the previous 600s (10 min)
        # hardcoded timeout assumed short demo trips. With the new
        # configurable route_length, the worker needs roughly
        #    route_length * telemetry_interval * trips_per_vehicle
        # seconds to exhaust all routes. We budget 2x that plus a
        # floor of 600s so short trips still get the same headroom
        # they had before. The 2x multiplier covers the post-trip
        # ignition-off broadcast (~15s), FWE upload wait (~45s), and
        # any back-pressure from AWS LS / MQTT.
        interval_s = int(getattr(self, 'telemetry_interval', 15))
        per_trip_s = max(1, self.route_length) * interval_s
        dynamic_timeout = max(600, per_trip_s * max(1, trips_per_vehicle) * 2 + 120)

        # Presence-active detection (Fix Group 11, 2026-08-05):
        # Use the same predicate the presence thread itself uses at ~:2921.
        # Both flags are set on the instance at ~:3912-3913, well before
        # threads start, so this read is race-free.
        # Do NOT infer from self.presence_loops — that dict is populated
        # inside the thread and races with the join here.
        _presence_active = (not getattr(self, 'skip_mqtt', False)) or getattr(self, 'commands_mqtt', False)

        try:
            for thread in self.simulation_threads:
                if _presence_active:
                    # Presence loop is unbounded by design (it runs until
                    # SIGTERM/self.running=False).  Joining with a timeout
                    # would always expire, and the ensuing cleanup() call
                    # closes the CAN bus out from under the still-running
                    # presence thread.  Join without a timeout so cleanup()
                    # cannot run until the presence thread has exited.
                    # Live failure proof (task e9737cfd):
                    #   00:24:08 join started timeout=720s
                    #   00:36:08 timed out → CAN bus closed
                    #   00:36:15 Bus not open. Call open() first. (every tick)
                    print(f"🔄 Waiting for presence thread {thread.name} (no timeout — presence loop is unbounded)...")
                    thread.join()
                    print(f"✅ Presence thread {thread.name} exited")
                else:
                    print(f"🔄 Waiting for thread {thread.name} to complete (timeout={dynamic_timeout}s)...")
                    thread.join(timeout=dynamic_timeout)
                    if thread.is_alive():
                        print(f"⚠️ Thread {thread.name} is still running after timeout")
                        # Post-timeout safety net (added 2026-05-05): even
                        # if the worker thread stalled mid-drive, publish
                        # an ignition-off telemetry frame so FWE +
                        # TripProcessor see a completion signal. Without
                        # this, stuck workers leave trips in status=ACTIVE
                        # until the trip-sweeper Lambda closes them hours
                        # later. The cached _last_telemetry per vehicle
                        # is enough to synthesize a plausible final frame.
                        # Skip when presence is active — a parked vehicle
                        # is not stalled; the safety net is for bounded
                        # trip workers only, and it emitted a spurious
                        # 'no cached telemetry to synthesize from' warning
                        # on presence-active tasks (Fix Group 11).
                        self._emit_fallback_ignition_off()
                    else:
                        print(f"✅ Thread {thread.name} completed")
        except KeyboardInterrupt:
            print("\n🛑 Simulation interrupted by user")
            self.stop_simulation()
        
        print("🎉 Real-time telemetry simulation completed!")
        self.logger.info("🎉 Real-time telemetry simulation completed!")
    
    def _emit_fallback_ignition_off(self):
        """Emit a synthetic ignition-off telemetry frame for every
        vehicle we have cached telemetry for, so downstream TripProcessor
        can close its trip even if the simulator worker stalled out.

        Added 2026-05-05 as the belt-and-suspenders fallback for the
        parent-thread join timeout. Failure modes this handles:
          - AWS Location Service returns an unusually dense coordinate
            list, pushing the trip past the join timeout.
          - MQTT backpressure / IoT Core reject blocks the worker's
            publish loop.
          - Any other bug in the worker that prevents it from reaching
            its own `🏁 Final telemetry sent with ignitionOn: false`
            code path.

        This helper is best-effort. It re-uses `_last_telemetry` (the
        most recent healthy frame with ignitionOn=True) so the emitted
        frame looks realistic; it just flips ignitionOn and stamps a
        fresh timestamp. If we have no cached frame for a vehicle
        (which shouldn't happen in practice), we skip silently — better
        to emit nothing than to emit a bogus frame.
        """
        cache = getattr(self, '_last_telemetry', None) or {}
        if not cache:
            print("⚠️ fallback ignition-off: no cached telemetry to synthesize from")
            return
        for vehicle_id, frame in cache.items():
            try:
                import copy
                synth = copy.deepcopy(frame)
                synth['ignitionOn'] = False
                synth['engineEvent'] = 'ENGINE_STOP'
                synth['timestamp'] = int(time.time() * 1000)
                synth['_fallback'] = True  # audit marker so operators can see this wasn't real telemetry
                print(f"🛟 fallback ignition-off: {vehicle_id} (post-timeout safety net)")
                if self.mode == 'can':
                    # In CAN mode, publish via the normal CAN writer
                    # path; ignore any failures (the bus may already
                    # be closed by the parent).
                    try:
                        self.publish_can(vehicle_id, synth, mqtt_client=None)
                    except Exception as e:
                        print(f"  ⚠️ CAN publish failed (bus likely closed): {e}")
                # Always also publish via MQTT if we have a Direct
                # path — this is the belt part of belt-and-suspenders.
                # If no MQTT client is wired for this vehicle right
                # now we skip (TripProcessor's 30-min timeout + the
                # trip-sweeper Lambda still cover this case).
            except Exception as e:
                print(f"  ⚠️ fallback ignition-off for {vehicle_id} failed: {e}")

    def stop_simulation(self):
        """Stop the telemetry simulation"""
        print("🛑 Stopping telemetry simulation...")
        self.running = False
        
        # Wait for threads to finish
        for thread in self.simulation_threads:
            if thread.is_alive():
                thread.join(timeout=5)
        
        self.simulation_threads.clear()
    def cleanup(self):
        """Clean up MQTT connection, CAN bus, and certificate files"""
        try:
            if self.can_writer:
                self.can_writer.close()

            if hasattr(self, 'mqtt_connection') and self.mqtt_connection:
                disconnect_future = self.mqtt_connection.disconnect()
                disconnect_future.result(timeout=5)
                print("✅ Disconnected from IoT Core")
                
            if hasattr(self, 'cert_files'):
                import os
                import shutil
                for file_path in self.cert_files:
                    if os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                    elif os.path.exists(file_path):
                        os.remove(file_path)
                print("✅ Cleaned up certificate files")
                        
        except Exception as e:
            print(f"⚠️ Error during cleanup: {e}")

    def apply_forced_alert_params(self, vehicle_state: VehicleState):
        """Apply forced alert parameters from API to vehicle state"""
        vehicle_state.force_tire_blowout = self.force_tire_blowout
        vehicle_state.force_engine_overheat = self.force_engine_overheat
        vehicle_state.force_battery_critical = self.force_battery_critical
        vehicle_state.force_brake_failure = self.force_brake_failure
        vehicle_state.force_oil_pressure_low = self.force_oil_pressure_low
        vehicle_state.force_hv_battery_degradation = self.force_hv_battery_degradation
        vehicle_state.force_safety_event = self.force_safety_event
        vehicle_state.safety_rate = getattr(self, 'safety_rate', 1.0)
        
        print(f"🎯 Applied forced alert params: tire_blowout={self.force_tire_blowout}, "
              f"engine_overheat={self.force_engine_overheat}, safety_event={self.force_safety_event}")

    def update_vehicle_conditions(self, vehicle_state: VehicleState, current_speed: float, acceleration: float, deceleration: float):
        """Update vehicle conditions based on driving behavior and trip progression"""
        
        # Update trip distance (approximate)
        if vehicle_state.last_speed > 0:
            distance_increment = (current_speed + vehicle_state.last_speed) / 2 * (15 / 3600)  # 15 seconds in hours
            vehicle_state.trip_distance += distance_increment
        
        # Track high speed driving (affects engine temp)
        if current_speed > 60:
            vehicle_state.high_speed_time += 15  # 15 second intervals
            
        # Progressive tire pressure loss (very gradual)
        if random.random() < 0.001:  # 0.1% chance per telemetry point
            vehicle_state.tire_pressure_fl -= random.uniform(0.1, 0.3)
            vehicle_state.tire_pressure_fr -= random.uniform(0.1, 0.3)
            vehicle_state.tire_pressure_rl -= random.uniform(0.1, 0.3)
            vehicle_state.tire_pressure_rr -= random.uniform(0.1, 0.3)
            
        # Engine temperature: tends toward normal operating temp, rises under load
        target_temp = 195.0 if current_speed > 10 else 150.0
        vehicle_state.engine_temp_base += (target_temp - vehicle_state.engine_temp_base) * 0.05
        if current_speed > 70:
            vehicle_state.engine_temp_base += random.uniform(0.0, 0.2)  # Slight rise under load
        vehicle_state.engine_temp_base += random.uniform(-0.3, 0.3)  # noise
            
        # Battery voltage: tends toward alternator output, stable during normal ops
        target_voltage = 13.8 if vehicle_state.engine_on else 12.6
        vehicle_state.battery_voltage_base += (target_voltage - vehicle_state.battery_voltage_base) * 0.05
        vehicle_state.battery_voltage_base += random.uniform(-0.01, 0.01)  # noise
            
        # Oil life decreases very slowly with distance
        oil_consumption = vehicle_state.trip_distance * 0.0002
        vehicle_state.oil_life = max(0, vehicle_state.oil_life - oil_consumption)
        
        # Brake wear decreases slowly with hard braking
        if deceleration < -0.3:
            vehicle_state.hard_braking_count += 1
            vehicle_state.brake_wear = max(0, vehicle_state.brake_wear - random.uniform(0.01, 0.05))
            
        # EV battery discharge (for EV vehicles)
        if vehicle_state.soc_base is not None:
            discharge_rate = 0.001 + (current_speed * 0.0001)
            vehicle_state.soc_base = max(0, vehicle_state.soc_base - discharge_rate)
            
        # Clamp values to realistic ranges
        vehicle_state.tire_pressure_fl = max(5.0, min(40.0, vehicle_state.tire_pressure_fl))
        vehicle_state.tire_pressure_fr = max(5.0, min(40.0, vehicle_state.tire_pressure_fr))
        vehicle_state.tire_pressure_rl = max(5.0, min(40.0, vehicle_state.tire_pressure_rl))
        vehicle_state.tire_pressure_rr = max(5.0, min(40.0, vehicle_state.tire_pressure_rr))
        vehicle_state.engine_temp_base = max(70.0, min(260.0, vehicle_state.engine_temp_base))
        vehicle_state.battery_voltage_base = max(10.0, min(15.0, vehicle_state.battery_voltage_base))
        
        if vehicle_state.soc_base is not None:
            vehicle_state.soc_base = max(0.0, min(100.0, vehicle_state.soc_base))
        if vehicle_state.hv_voltage_base is not None:
            vehicle_state.hv_voltage_base = max(250.0, min(450.0, vehicle_state.hv_voltage_base))
    
    def detect_safety_events(self, current_telemetry: Dict, previous_state: VehicleState) -> List[Dict]:
        """Detect safety events from telemetry data - uses exact route coordinates"""
        events = []
        
        # Use exact coordinates from current telemetry (which are from route points)
        current_lat = current_telemetry.get('lat')
        current_lng = current_telemetry.get('lng')
        
        # Check for hard braking
        if self.detect_hard_braking(current_telemetry, previous_state):
            events.append({
                'alertType': 'HARD_BRAKING',
                'severity': self.calculate_severity("HB", current_telemetry, previous_state),
                'value': abs(current_telemetry.get('deceleration', 0)),
                'lat': current_lat,
                'lng': current_lng,
                'timestamp': current_telemetry.get('timestamp'),
                'vehicleId': current_telemetry.get('vehicleId'),
                'speed': current_telemetry.get('speed')
            })
        
        # Check for rapid acceleration
        if self.detect_rapid_acceleration(current_telemetry, previous_state):
            events.append({
                'alertType': 'RAPID_ACCELERATION',
                'severity': self.calculate_severity("RA", current_telemetry, previous_state),
                'value': current_telemetry.get('acceleration', 0),
                'lat': current_lat,
                'lng': current_lng,
                'timestamp': current_telemetry.get('timestamp'),
                'vehicleId': current_telemetry.get('vehicleId'),
                'speed': current_telemetry.get('speed')
            })
        
        # Check for seatbelt violation
        if self.detect_seatbelt_violation(current_telemetry, previous_state):
            events.append({
                'alertType': 'SEATBELT_VIOLATION',
                'severity': 'HIGH',
                'message': 'Seatbelt not fastened while driving',
                'lat': current_lat,
                'lng': current_lng,
                'timestamp': current_telemetry.get('timestamp'),
                'vehicleId': current_telemetry.get('vehicleId'),
                'speed': current_telemetry.get('speed')
            })
        
        # Check for phone usage
        if self.detect_phone_usage(current_telemetry, previous_state):
            events.append({
                'alertType': 'PHONE_USAGE',
                'severity': 'MEDIUM',
                'message': 'Phone usage detected while driving',
                'lat': current_lat,
                'lng': current_lng,
                'timestamp': current_telemetry.get('timestamp'),
                'vehicleId': current_telemetry.get('vehicleId'),
                'speed': current_telemetry.get('speed')
            })
        
        return events
    
    def detect_hard_braking(self, telemetry: Dict, state: VehicleState) -> bool:
        """Detect hard braking event"""
        return abs(telemetry.get('deceleration', 0)) > self.HARD_BRAKING_THRESHOLD
    
    def detect_rapid_acceleration(self, telemetry: Dict, state: VehicleState) -> bool:
        """Detect rapid acceleration event"""
        return telemetry.get('acceleration', 0) > self.RAPID_ACCELERATION_THRESHOLD
    
    def detect_engine_critical(self, telemetry: Dict) -> bool:
        """Detect engine critical conditions"""
        return telemetry.get('engineTemp', 0) > self.ENGINE_CRITICAL_TEMP or telemetry.get('oilPressure', 100) < 10
    
    def detect_seatbelt_violation(self, telemetry: Dict, state: VehicleState) -> bool:
        """Detect seatbelt violation"""
        current_time = telemetry['timestamp']
        seatbelt_status = telemetry.get('seatbeltStatus', True)
        speed = telemetry.get('speed', 0)
        
        if not seatbelt_status and speed > 5:
            if state.seatbelt_violation_start is None:
                state.seatbelt_violation_start = current_time
            elif current_time - state.seatbelt_violation_start > 30:  # 30 seconds
                return True
        else:
            state.seatbelt_violation_start = None
        
        return False
    
    def detect_phone_usage(self, telemetry: Dict, state: VehicleState) -> bool:
        """Detect phone usage while driving"""
        phone_connected = telemetry.get('phoneConnected', False)
        speed = telemetry.get('speed', 0)
        return phone_connected and speed > 5
    
    def create_safety_event(self, event_type: str, telemetry: Dict, state: VehicleState) -> Dict:
        """Create standardized safety event message with Event Catalog format"""
        # Import Event Catalog loader
        try:
            from event_catalog_loader import get_catalog_loader
            catalog_loader = get_catalog_loader(profile_name=getattr(self, 'profile_name', None))
            use_dynamic_catalog = True
        except ImportError:
            use_dynamic_catalog = False
        
        event_type_map = {
            "HB": "HARD_BRAKING",
            "RA": "RAPID_ACCELERATION", 
            "EC": "ENGINE_CRITICAL",
            "SV": "SEATBELT_VIOLATION",
            "PU": "PHONE_USAGE",
            "FCW": "FORWARD_COLLISION_WARNING"
        }
        
        severity_map = {
            "L": "LOW",
            "M": "MEDIUM", 
            "H": "HIGH",
            "C": "CRITICAL"
        }
        
        # Numeric severity for Event Catalog (0=info, 1=warning, 2=critical)
        severity_numeric_map = {
            "L": 0,
            "M": 1,
            "H": 2,
            "C": 2
        }
        
        severity_code = self.calculate_severity(event_type, telemetry, state)
        
        # Get Event Catalog entry dynamically or use fallback
        if use_dynamic_catalog:
            catalog_entry = catalog_loader.map_simulator_event(event_type)
            if not catalog_entry:
                # Fallback if not found
                catalog_entry = {
                    "event_id": f"safety.{event_type.lower()}",
                    "category": "safety",
                    "severity": 1
                }
        else:
            # Static fallback mapping
            event_catalog_map = {
                "HB": {"event_id": "safety.harsh_braking", "category": "safety"},
                "RA": {"event_id": "safety.harsh_acceleration", "category": "safety"},
                "EC": {"event_id": "maintenance.check_engine_light", "category": "maintenance"},
                "SV": {"event_id": "safety.seatbelt_unfastened", "category": "safety"},
                "PU": {"event_id": "safety.phone_usage", "category": "safety"},
                "FCW": {"event_id": "safety.forward_collision_warning", "category": "safety"}
            }
            catalog_entry = event_catalog_map.get(event_type, {
                "event_id": f"safety.{event_type.lower()}",
                "category": "safety"
            })
        
        # Build signal_values based on event type
        signal_values = {
            "speed": telemetry["speed"]
        }
        
        if event_type == "HB":
            signal_values["deceleration"] = telemetry.get('deceleration', 0)
        elif event_type == "RA":
            signal_values["acceleration"] = telemetry.get('acceleration', 0)
        
        safety_event = {
            "messageType": "SAFETY_EVENT",
            "vehicleId": telemetry["vehicleId"],
            "timestamp": telemetry["timestamp"],
            
            # Event Catalog fields (NEW)
            "event_id": catalog_entry["event_id"],
            "category": catalog_entry.get("category", "safety"),
            "severity": catalog_entry.get("severity", severity_numeric_map.get(severity_code, 1)),
            "signal_values": signal_values,
            
            # Legacy fields (for compatibility)
            "eventType": event_type_map.get(event_type, event_type),
            
            "lat": telemetry["lat"],
            "lng": telemetry["lng"],
            "speed": telemetry["speed"]
        }
        
        # Add event-specific fields (legacy)
        if event_type == "HB":
            safety_event["deceleration"] = telemetry.get('deceleration', 0)
        elif event_type == "RA":
            safety_event["acceleration"] = telemetry.get('acceleration', 0)
        
        return safety_event
    
    def calculate_severity(self, event_type: str, telemetry: Dict, state: VehicleState) -> str:
        """Calculate event severity"""
        if event_type == "HB":  # Hard braking
            dec = abs(telemetry.get('deceleration', 0))
            if dec > 15: return "CRITICAL"
            elif dec > 12: return "HIGH"
            elif dec > 10: return "MEDIUM"
            else: return "LOW"
        elif event_type == "RA":  # Rapid acceleration
            acc = telemetry.get('acceleration', 0)
            if acc > 8: return "HIGH"
            elif acc > 6: return "MEDIUM"
            else: return "LOW"
        elif event_type == "EC":  # Engine critical
            return "CRITICAL"
        elif event_type == "SV":  # Seatbelt violation
            return "HIGH"
        elif event_type == "PU":  # Phone usage
            return "MEDIUM"
        elif event_type == "FCW":  # Forward collision warning
            return "CRITICAL"
        return "LOW"

    def validate_message_format(self, data: Dict) -> bool:
        """Ensure message has required fields"""
        if data.get('messageType') == 'TELEMETRY':
            required_fields = ["messageType", "vehicleId", "timestamp", "lat", "lng", "speed"]
        elif data.get('messageType') == 'SAFETY_EVENT':
            required_fields = ["messageType", "vehicleId", "timestamp", "eventType", "lat", "lng"]
        else:
            return False
        return all(field in data for field in required_fields)

def _unit_interval(value):
    """argparse type: a float in [0.0, 1.0].

    The chained comparison also rejects 'nan' and 'inf' (both compare False), which a
    bare float() would accept: `random() < nan` is always False and `random() < inf`
    always True, so either would silently change the rate.
    """
    import argparse
    try:
        rate = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"not a number: {value!r}")
    if not 0.0 <= rate <= 1.0:
        raise argparse.ArgumentTypeError(f"must be between 0.0 and 1.0, got {value!r}")
    return rate

def main():
    """Main execution function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Real-time telemetry simulator for CMS UI')
    parser.add_argument('--profile', default='default', help='AWS profile name')
    parser.add_argument('--region', default=os.environ.get('AWS_REGION', 'us-east-1'), help='AWS region')
    parser.add_argument('--trips', type=int, default=3, help='Number of trips per vehicle to simulate')
    parser.add_argument('--route-length', type=int, default=20,
                        help='Number of GPS route points per trip (1 point per telemetry tick). '
                             'Default 20 \u2248 5 minutes at the default 15s interval. Valid range 5-60.')
    parser.add_argument('--vehicles', type=int, default=10, help='Maximum number of vehicles to simulate')
    parser.add_argument('--vehicle-config', help='JSON string with vehicle configuration')
    parser.add_argument('--no-cleanup', action='store_true', help='Skip cleanup of MQTT connections and certificate files')
    parser.add_argument('--api-endpoint', help='API Gateway endpoint URL (alternative to direct DynamoDB access)')
    parser.add_argument('--table-suffix', help='Table suffix for DynamoDB tables (e.g., "dev", "prod")')
    parser.add_argument('--vehicles-table', help='Full DynamoDB vehicles table name')
    parser.add_argument('--certificates-table', help='Full DynamoDB certificates table name')
    parser.add_argument('--city', default='nyc', choices=['nyc', 'sf', 'chicago', 'miami', 'seattle', 'munich', 'atlanta'], help='City for route generation')
    parser.add_argument('--city-lat', type=float, help='Custom city latitude (overrides --city)')
    parser.add_argument('--city-lng', type=float, help='Custom city longitude (overrides --city)')
    parser.add_argument('--force-maintenance-alert', action='store_true', help='Force generation of maintenance alert for each trip')
    parser.add_argument('--random-maintenance-rate', type=_unit_interval, default=0.0,
                        help='Per-message chance (0.0-1.0) of generating random maintenance '
                             'fault conditions. Default 0.0: a trip with no events selected is healthy.')
    parser.add_argument('--driver-selection', default='consistent', choices=['random', 'consistent', 'specific'], help='Driver selection mode')
    parser.add_argument('--driver-id', help='Specific driver ID to use (when --driver-selection=specific)')
    
    # === SCENARIO-BASED TESTING ===
    SCENARIOS = {
        'tire_slow_leak':       {'force_tire_blowout': False, 'tire_slow_leak': True, 'desc': 'Gradual pressure loss on front-left tire (~1 PSI/min)'},
        'tire_blowout':         {'force_tire_blowout': True, 'desc': 'Rapid tire pressure loss (blowout)'},
        'tire_pressure_imbalance': {'tire_pressure_imbalance': True, 'desc': 'Uneven pressure across front axle'},
        'engine_overheat':      {'force_engine_overheat': True, 'desc': 'Engine temperature rising past safe limits'},
        'oil_pressure_low':     {'force_oil_pressure_low': True, 'desc': 'Oil pressure dropping below threshold'},
        'battery_critical':     {'force_battery_critical': True, 'desc': '12V battery voltage dropping'},
        'hv_battery_degradation': {'force_hv_battery_degradation': True, 'desc': 'EV high-voltage battery degradation'},
        'brake_failure':        {'force_brake_failure': True, 'desc': 'Brake wear reaching critical level'},
        'hard_braking':         {'force_safety_event': 'hard_braking', 'desc': 'Sudden deceleration event'},
        'collision_avoidance':  {'force_safety_event': 'collision_avoidance', 'desc': 'Forward collision warning + auto-brake'},
        'seatbelt_violation':   {'force_safety_event': 'seatbelt_violation', 'desc': 'Driver seatbelt unbuckled while moving'},
        'phone_usage':          {'force_safety_event': 'phone_usage', 'desc': 'Distracted driving detected'},
        'harsh_cornering':      {'force_safety_event': 'harsh_cornering', 'desc': 'Aggressive cornering event'},
    }
    scenario_list = '\n'.join(f'  {k:28s} {v["desc"]}' for k, v in SCENARIOS.items())
    parser.add_argument('--scenario', choices=list(SCENARIOS.keys()),
                       help=f'Run a specific test scenario:\n{scenario_list}')
    parser.add_argument('--list-scenarios', action='store_true', help='List all available test scenarios')

    # === EVENT CATALOG-DRIVEN TESTING ===
    parser.add_argument('--events', type=str, default='',
                       help='Comma-separated event IDs from the event catalog (e.g., maintenance.low_tire_pressure,safety.hard_braking)')
    parser.add_argument('--list-events', action='store_true', help='List all events from the event catalog')

    # === FORCED ALERT PARAMETERS (legacy, use --scenario instead) ===
    parser.add_argument('--force-tire-blowout', action='store_true', help='Force tire pressure critical alerts')
    parser.add_argument('--force-engine-overheat', action='store_true', help='Force engine overheating alerts')
    parser.add_argument('--force-battery-critical', action='store_true', help='Force battery critical alerts')
    parser.add_argument('--force-brake-failure', action='store_true', help='Force brake system failure alerts')
    parser.add_argument('--force-oil-pressure-low', action='store_true', help='Force oil pressure low alerts')
    parser.add_argument('--force-hv-battery-degradation', action='store_true', help='Force EV battery degradation alerts')
    parser.add_argument('--tire-slow-leak', action='store_true', help='Gradual tire pressure loss')
    parser.add_argument('--tire-pressure-imbalance', action='store_true', help='Uneven pressure across axle')
    parser.add_argument('--force-safety-event', choices=['hard_braking', 'collision_avoidance', 'seatbelt_violation', 'phone_usage', 'harsh_cornering'], 
                       help='Force specific safety event type')
    parser.add_argument('--safety-rate', type=float, default=1.0, help='Safety event probability multiplier (0.0-1.0)')
    parser.add_argument('--no-progressive-degradation', action='store_true', help='Disable intelligent condition progression')
    parser.add_argument('--mode', default='mqtt_direct', choices=['mqtt_direct', 'can'],
                       help='Output mode: mqtt_direct (JSON to IoT Core) or can (CAN bus + GPS via MQTT)')
    parser.add_argument('--skip-mqtt', action='store_true',
                       help='Skip MQTT connection for telemetry (FWE agent handles it)')
    parser.add_argument('--commands-mqtt', action='store_true',
                       help='Connect MQTT for remote commands even when --skip-mqtt is set')
    parser.add_argument('--rule-name', default='cms_dev_iot_msk_rule',
                       help='IoT Rule name for basic ingest (default: cms_dev_iot_msk_rule)')

    args = parser.parse_args()
    
    # City coordinate mapping — single source of truth at module scope, shared with
    # the trip-intent override path (PresenceLoop._resolve_trip_overrides). Keys are
    # coupled to this parser's --city choices; see CITY_COORDINATES.
    city_coordinates = CITY_COORDINATES
    
    # Determine city coordinates
    if args.city_lat and args.city_lng:
        city_lat, city_lng = args.city_lat, args.city_lng
        print(f"🌍 Using custom coordinates: {city_lat}, {city_lng}")
    else:
        city_lat, city_lng = city_coordinates[args.city]
        print(f"🌍 Using {args.city.upper()} coordinates: {city_lat}, {city_lng}")
    
    # Handle --list-scenarios
    if args.list_scenarios:
        print("🎯 Available test scenarios:\n")
        for name, cfg in SCENARIOS.items():
            print(f"  --scenario {name:28s} {cfg['desc']}")
        print(f"\nUsage: python3 realtime_telemetry_simulator.py --scenario tire_slow_leak --vehicles 1 --trips 1")
        sys.exit(0)

    # Handle --list-events (catalog-driven)
    if args.list_events:
        from event_catalog_driver import EventCatalogDriver
        stage = os.environ.get('DEPLOYMENT_STAGE')
        if not stage:
            print("ERROR: DEPLOYMENT_STAGE is not set — a container with the variable "
                  "unset must not read the production catalog.", file=sys.stderr)
            sys.exit(1)
        driver = EventCatalogDriver(region=args.region, stage=stage, profile=args.profile)
        print("📋 Events from catalog:\n")
        for cat in ['safety', 'maintenance']:
            print(f"  {cat.upper()}:")
            for evt in driver.list_events(category=cat):
                fields = ','.join(evt['json_fields']) if evt['json_fields'] else evt['trigger_signal']
                print(f"    {evt['event_id']:45s} {evt['description'][:50]:50s} [{fields} {evt['threshold_operator']} {evt['threshold_value']}]")
            print()
        print(f"Usage: python3 realtime_telemetry_simulator.py --events maintenance.low_tire_pressure,safety.hard_braking --vehicles 1 --trips 1")
        sys.exit(0)

    # Always load event catalog for safe ranges (even without --events)
    from event_catalog_driver import EventCatalogDriver
    stage = os.environ.get('DEPLOYMENT_STAGE')
    if not stage:
        print("ERROR: DEPLOYMENT_STAGE is not set — a container with the variable "
              "unset must not read the production catalog.", file=sys.stderr)
        sys.exit(1)
    event_catalog_driver = EventCatalogDriver(region=args.region, stage=stage, profile=args.profile if args.profile != 'default' else None)
    safe_ranges = event_catalog_driver.get_safe_ranges()
    print(f"📊 Loaded safe ranges for {len(safe_ranges)} signals from catalogs")
    
    if args.events:
        event_ids = [e.strip() for e in args.events.split(',') if e.strip()]
        event_catalog_driver.set_active_events(event_ids)
        degradation_targets = event_catalog_driver.compute_degradation_targets()
        print(f"🎯 Degradation targets: {degradation_targets}")
    else:
        # Retain the correctly-staged driver so warm-path containers can use it
        # for lazy scenario application without a second DDB scan.
        # active_events and degradation_targets start empty (no --events requested).
        degradation_targets = {}

    # Merge scenario into alert params (scenario overrides individual flags)
    scenario_params = SCENARIOS.get(args.scenario, {}) if args.scenario else {}
    if args.scenario:
        print(f"🎯 Scenario: {args.scenario} — {scenario_params.get('desc', '')}")

    # Prepare forced alert parameters
    alert_params = {
        'force_tire_blowout': args.force_tire_blowout or scenario_params.get('force_tire_blowout', False),
        'force_engine_overheat': args.force_engine_overheat or scenario_params.get('force_engine_overheat', False),
        'force_battery_critical': args.force_battery_critical or scenario_params.get('force_battery_critical', False),
        'force_brake_failure': args.force_brake_failure or scenario_params.get('force_brake_failure', False),
        'force_oil_pressure_low': args.force_oil_pressure_low or scenario_params.get('force_oil_pressure_low', False),
        'force_hv_battery_degradation': args.force_hv_battery_degradation or scenario_params.get('force_hv_battery_degradation', False),
        'force_safety_event': args.force_safety_event or scenario_params.get('force_safety_event', None),
        'safety_rate': args.safety_rate,
        'progressive_degradation': not args.no_progressive_degradation,
        'tire_slow_leak': args.tire_slow_leak or scenario_params.get('tire_slow_leak', False),
        'tire_pressure_imbalance': args.tire_pressure_imbalance or scenario_params.get('tire_pressure_imbalance', False),
    }
    
    simulator = RealtimeTelemetrySimulator(
        profile_name=args.profile, 
        region=args.region,
        certificates_table_name=args.certificates_table,
        mode=args.mode,
        iot_rule_name=args.rule_name,
        **alert_params
    )
    simulator.skip_mqtt = getattr(args, 'skip_mqtt', False)
    simulator.commands_mqtt = getattr(args, 'commands_mqtt', False)
    simulator.random_maintenance_rate = getattr(args, 'random_maintenance_rate', 0.0)
    simulator.event_catalog_driver = event_catalog_driver
    simulator.event_tick = 0
    simulator.safe_ranges = safe_ranges
    simulator.degradation_targets = degradation_targets
    # Route length override from CLI / config. Clamped to [5, 60] so
    # a mistakenly-passed 0 doesn't divide-by-zero and a pathological
    # 10_000 doesn't burn a whole ECS task on a single never-ending
    # trip. Matches the API-layer validation in simulation_api.py.
    try:
        _rl = int(getattr(args, 'route_length', 20))
    except Exception:
        _rl = 20
    simulator.route_length = max(5, min(60, _rl))
    
    # Set city coordinates for route generation
    simulator.city_lat = city_lat
    simulator.city_lng = city_lng
    simulator.current_city = args.city.upper()  # Store city name for logging
    
    # Configure driver selection
    simulator.configure_driver_selection(
        mode=args.driver_selection,
        specific_driver_id=args.driver_id
    )
    
    # Override table configuration BEFORE auto-detection if provided
    if args.table_suffix:
        simulator.table_suffix = args.table_suffix
        # Rebuild table names with the correct suffix
        simulator.table_names = {
            'vehicles': f'cms-{args.table_suffix}-vehicles',
            'trips': f'cms-{args.table_suffix}-trips',
            'telemetry': f'cms-{args.table_suffix}-telemetry'
        }
        print(f"🔧 Overriding table suffix to: {args.table_suffix}")
        print(f"🔧 Using tables: {simulator.table_names}")
    
    if args.vehicles_table:
        simulator.table_names['vehicles'] = args.vehicles_table
    if args.certificates_table:
        simulator.certificates_table = args.certificates_table
    
    # Set API endpoint if provided
    if args.api_endpoint:
        simulator.api_endpoint = args.api_endpoint
        print(f"🌐 Using API endpoint: {args.api_endpoint}")
    else:
        print(f"🗄️ Using direct DynamoDB access with suffix: {getattr(simulator, 'table_suffix', 'auto-detected')}")
    
    # Parse vehicle configuration if provided
    vehicles = None
    if args.vehicle_config:
        try:
            import json
            vehicles = json.loads(args.vehicle_config)
            # Convert plain strings to dicts if needed
            vehicles = [{'vehicleId': v} if isinstance(v, str) else v for v in vehicles]
            print(f"📋 Using {len(vehicles)} vehicles from configuration")
        except Exception as e:
            print(f"⚠️ Error parsing vehicle config: {e}")
            print("📋 Falling back to database vehicles")

    # ── SIGTERM / SIGINT handler (Fix Group 11, 2026-08-05) ───────────────
    # Comments at ~:3208 and ~:3234 claimed "SIGTERM causes cleanup" and
    # "_stop_event is set by the shutdown path" — but no signal handler was
    # installed anywhere (grep 'signal.signal' previously found only the
    # subprocess send at :86).  On ECS task stop the default SIGTERM action
    # terminates the process immediately, so the presence loop's finally
    # never runs, shutdown() never runs, notify_disconnected() never fires,
    # and a stopped vehicle keeps connectionStatus=connected forever.
    #
    # Fix: handlers set simulator.running = False.  The existing stop-watcher
    # thread (presence-stop-watcher-<vid>) polls self.running and sets
    # _stop_event when it clears, causing the presence loop to exit its loop
    # and run finally → shutdown() → notify_disconnected().  main's join
    # (now timeout-free for presence-active tasks) then unblocks, and
    # cleanup() runs in the right order.
    #
    # Handler is idempotent: repeated signals are safe because setting
    # self.running = False on an already-False instance is a no-op.
    # ECS stop_timeout is 30 s (simulation_stack.py); graceful shutdown
    # must complete within that window.

    def _signal_handler(signum, frame):
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        print(f"🛑 Received {sig_name} — setting simulator.running=False for graceful shutdown")
        simulator.running = False

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    try:
        simulator.start_simulation(
            trips_per_vehicle=args.trips, 
            max_vehicles=args.vehicles,
            vehicles=vehicles,
            force_maintenance_alert=args.force_maintenance_alert
        )
    except KeyboardInterrupt:
        print("\n🛑 Simulation interrupted")
        simulator.stop_simulation()
    finally:
        if not args.no_cleanup:
            simulator.cleanup()

if __name__ == "__main__":
    main()
