#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate golden-fixture files for the Meridian EV end-to-end Java test (spec T3.1 / D5).

Writes two committed artifacts:

  modules/flink/src/test/resources/fixtures/meridian-ev-telemetry.raw.json
      Pretty-printed ``generate_telemetry_data()`` output — reviewable diffs.

  modules/flink/src/test/resources/fixtures/meridian-ev-telemetry.b64.txt
      ``compress_telemetry()`` of the same payload, i.e. exactly the bytes the
      offboard path publishes via MQTT Basic Ingest.

Both are deterministic: ``random`` is seeded explicitly before the call so two
consecutive runs with the same seed produce byte-identical output.

The Java test reads ``.b64.txt`` (proving ``normalizeIngress`` works on real wire
bytes); the regeneration guard (T3.2) compares the current simulator's key set
against ``.raw.json``'s top-level keys.

Usage::

    python3 scripts/gen_meridian_telemetry_fixture.py          # write with default seed
    python3 scripts/gen_meridian_telemetry_fixture.py --seed 0  # explicit seed

Spec: ``.kiro/specs/2026-09-20-transform-manifest-contract-guards/`` T3.1.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import random
import sys
import unittest.mock
from pathlib import Path
from typing import TYPE_CHECKING

# ── Deterministic hash seed (FG2.C3, S11/S1 gated to __main__) ────────────────
# Python 3.14 randomises string-hash iteration order per process. That randomises
# dict/set traversal, which in turn flips at least one `random.choice`/`random.sample`
# outcome inside `generate_telemetry_data()` — observed empirically by review
# Cycle 3 (see .kiro/specs/2026-09-20-transform-manifest-contract-guards/review.md
# Cycle 3 C3). Setting PYTHONHASHSEED=0 pins the iteration order without touching
# the simulator itself, so this generator is deterministic under `random.seed()`
# regardless of the caller's environment.
#
# `setdefault` respects a caller who deliberately runs with a different seed
# (e.g. to reproduce an old fixture); the CI wire and the default run both
# get 0.
#
# Gated behind `__name__ == "__main__"` (review Cycle 4 S11 / security Cycle 2 S1):
# if the module is imported (e.g. by a future pytest conftest.py or another script),
# the un-gated version replaced the importing Python process with a re-exec of THIS
# script — a repo-wide test bypass reachable via one import line. Not currently
# reachable (no consumer imports this module today) but the trap belongs closed.
if __name__ == "__main__":
    os.environ.setdefault("PYTHONHASHSEED", "0")

    # If PYTHONHASHSEED was set by us AFTER the interpreter started, re-exec once
    # so the setting takes effect. Otherwise the setdefault above only affects
    # child processes we spawn, not this one — Python reads PYTHONHASHSEED before
    # `main()` runs.
    if os.environ.get("PYTHONHASHSEED") == "0" and not os.environ.get("_FIXTURE_GEN_HASHSEED_APPLIED"):
        os.environ["_FIXTURE_GEN_HASHSEED_APPLIED"] = "1"
        os.execvpe(sys.executable, [sys.executable, *sys.argv], os.environ)

# ── Repository layout ──────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SIMULATOR_PATH = _REPO_ROOT / "services" / "simulation" / "realtime_telemetry_simulator.py"
_FIXTURES_DIR = (
    _REPO_ROOT
    / "modules"
    / "flink"
    / "src"
    / "test"
    / "resources"
    / "fixtures"
)
_RAW_JSON = _FIXTURES_DIR / "meridian-ev-telemetry.raw.json"
_B64_TXT = _FIXTURES_DIR / "meridian-ev-telemetry.b64.txt"

# Vehicle used to generate the fixture.  VIN matches the Meridian fleet seed.
_VEHICLE = {"vehicleId": "VEH-MRDN-0001", "model": "Meridian EV", "is_ev": True}

# Default random seed — change only deliberately, then regenerate and commit both files.
_DEFAULT_SEED = 42


def _load_simulator_module():
    """Import ``realtime_telemetry_simulator`` with the AWS / CAN SDK stubs it needs.

    The module imports ``boto3``, ``can_encoder``, and ``can_bus_writer`` at the
    module level (used only in the CAN-mode branch of ``__init__``, which this
    generator never triggers).  Stub them so the module loads without credentials
    or hardware.  The stubs affect module-load scope only; the actual
    ``generate_telemetry_data`` / ``compress_telemetry`` call paths do not touch
    them.
    """
    import importlib.util

    # Patch boto3 before the module is loaded.  The module does ``import boto3``
    # at the top level; replace the real module with a MagicMock so the import
    # succeeds without credentials.
    boto3_mock = unittest.mock.MagicMock()
    with unittest.mock.patch.dict(
        "sys.modules",
        {"boto3": boto3_mock, "boto3.session": boto3_mock.session},
    ):
        spec = importlib.util.spec_from_file_location(
            "realtime_telemetry_simulator", _SIMULATOR_PATH
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(
                f"Cannot load {_SIMULATOR_PATH} — file not found or not importable"
            )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


def _build_simulator(mod):
    """Build a minimal ``RealtimeTelemetrySimulator`` instance without calling ``__init__``.

    ``__init__`` calls ``boto3.Session``, ``DynamoDB``, IoT endpoints, and
    ``self.account_id`` — all of which require live AWS credentials.  We bypass
    it via ``object.__new__`` and set exactly the attributes that
    ``generate_telemetry_data`` reads.  Any attribute access not covered here
    would raise ``AttributeError`` at call time, which is the deliberate
    fail-fast behaviour for an incomplete mock.
    """
    sim = object.__new__(mod.RealtimeTelemetrySimulator)

    # Routing / generation configuration
    sim.route_length = 20
    sim.city_lat = 47.6062   # Seattle — distinct from the default New York coords
    sim.city_lng = -122.3321
    sim.telemetry_interval = 15

    # Alert injection — all off for the fixture
    sim.force_tire_blowout = False
    sim.force_engine_overheat = False
    sim.force_battery_critical = False
    sim.force_brake_failure = False
    sim.force_oil_pressure_low = False
    sim.force_hv_battery_degradation = False
    sim.force_safety_event = None
    sim.safety_rate = 1.0
    sim.progressive_degradation = True
    sim.tire_slow_leak = False
    sim.tire_pressure_imbalance = False

    # Identity — used in logging paths only
    sim.profile_name = "default"
    sim.region = "us-west-2"

    # Driver selection — bypass the DynamoDB scan entirely
    sim.drivers_loaded = True
    sim.real_drivers = [
        {
            "driverId": "DRV-MRDN-001",
            "name": "Fixture Driver",
            "assignedVehicleId": _VEHICLE["vehicleId"],
        }
    ]
    sim.driver_selection_mode = "assigned"
    sim.specific_driver_id = None

    # Safety thresholds
    sim.HARD_BRAKING_THRESHOLD = 8.0
    sim.RAPID_ACCELERATION_THRESHOLD = 4.0
    sim.ENGINE_CRITICAL_TEMP = 240

    return sim


def generate(seed: int = _DEFAULT_SEED) -> tuple[dict, str]:
    """Return ``(raw_dict, b64_str)`` from a seeded call to the real simulator.

    Seeds ``random`` explicitly, replaces ``datetime`` in the simulator module's
    namespace with a subclass whose ``now()`` always returns a fixed instant, and
    patches ``uuid.uuid4`` to produce deterministic values.  Together these make
    the fixture byte-identical across runs with the same seed.

    The fixture is committed, not regenerated per test run; this call is only
    invoked by this script and by T3.2's parity check.
    """
    from datetime import datetime, timezone
    import uuid as _uuid

    mod = _load_simulator_module()
    random.seed(seed)
    sim = _build_simulator(mod)

    # Fix datetime.now() inside the simulator module so the emitted timestamp is
    # constant across runs.  Patch the class reference in the module's own namespace
    # (imported as `from datetime import datetime, timezone, timedelta`).
    _FIXED_NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)

    # Build a subclass of the module's datetime class (which is datetime.datetime)
    # so isinstance checks inside the simulator still pass.
    _OrigDatetime = mod.datetime  # type: ignore[attr-defined]

    class _FixedDatetime(_OrigDatetime):  # type: ignore[misc]
        """datetime subclass whose now() always returns the fixed instant."""
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return _FIXED_NOW

    # Also make uuid.uuid4 deterministic by seeding a fixed counter.
    _uuid_counter = [0]
    _orig_uuid4 = _uuid.uuid4

    def _seeded_uuid4() -> _uuid.UUID:  # type: ignore[return]
        _uuid_counter[0] += 1
        # Produce a v4-format UUID from the seed counter
        return _uuid.UUID(int=(_uuid_counter[0] | (seed << 32)), version=4)

    # Temporarily replace the module-level `datetime` name and the uuid module's uuid4
    mod.datetime = _FixedDatetime  # type: ignore[attr-defined]
    mod.uuid.uuid4 = _seeded_uuid4  # type: ignore[attr-defined]
    try:
        raw_dict = sim.generate_telemetry_data(_VEHICLE)
    finally:
        mod.datetime = _OrigDatetime  # type: ignore[attr-defined]
        mod.uuid.uuid4 = _orig_uuid4  # type: ignore[attr-defined]

    b64_str = sim.compress_telemetry(raw_dict)
    return raw_dict, b64_str


def _verify_round_trip(raw_dict: dict, b64_str: str) -> None:
    """Assert that ``b64_str`` round-trips to the same bytes as ``raw_dict`` serialised.

    ``compress_telemetry`` uses ``json.dumps(data, separators=(',', ':'))``
    (compact JSON, no spaces), then gzip, then base64.  Round-trip:
    base64-decode → gunzip → JSON-parse → compare dicts.
    """
    gzip_bytes = base64.b64decode(b64_str)
    recovered_json = gzip.decompress(gzip_bytes).decode("utf-8")
    recovered_dict = json.loads(recovered_json)
    if recovered_dict != raw_dict:
        raise RuntimeError(
            "Round-trip mismatch: b64.txt does not decode back to raw.json's content. "
            "This is a bug in gen_meridian_telemetry_fixture.py."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--seed",
        type=int,
        default=_DEFAULT_SEED,
        help=f"Random seed (default: {_DEFAULT_SEED}). Must stay constant to keep the "
             "fixture byte-identical across runs.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Regenerate in memory, verify round-trip, but do NOT write files.",
    )
    args = parser.parse_args(argv)

    print(f"Generating Meridian EV fixture (seed={args.seed}) …")
    raw_dict, b64_str = generate(args.seed)

    key_count = len(raw_dict)
    print(f"  Emitted {key_count} keys from generate_telemetry_data()")
    if key_count < 100:
        print("❌ Key count below the anti-vacuity floor of 100 — fixture is too thin.")
        return 1
    if "vehicleId" not in raw_dict or "timestamp" not in raw_dict:
        print("❌ Missing structural keys (vehicleId or timestamp).")
        return 1

    _verify_round_trip(raw_dict, b64_str)
    print("  Round-trip OK: b64.txt decodes back to raw.json content")

    if args.verify_only:
        print("  --verify-only: files not written.")
        return 0

    _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    raw_json_str = json.dumps(raw_dict, indent=2, sort_keys=False) + "\n"
    _RAW_JSON.write_text(raw_json_str, encoding="utf-8")
    print(f"  Wrote {_RAW_JSON.relative_to(_REPO_ROOT)}")

    _B64_TXT.write_text(b64_str + "\n", encoding="utf-8")
    print(f"  Wrote {_B64_TXT.relative_to(_REPO_ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
