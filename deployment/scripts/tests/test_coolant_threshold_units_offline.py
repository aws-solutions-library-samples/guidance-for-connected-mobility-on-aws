"""Offline guard: event-catalog rules on `coolant_temp` use the signal's unit (°F).

Issue: issues/2026-09-25-coolant-overheat-threshold-unit-mismatch/

The two P0 coolant rules (P0217 "stop driving", B0001_FIRE "exit vehicle
immediately") were seeded with °C thresholds (125, 200) copied from the retired
VFO classifier. The signal catalog, the DBC, the simulators and
MaintenanceProcessor all carry `coolant_temp` in °F, so normal readings
(180-210 °F) raised both alerts on healthy vehicles.

The guard reads the seeders' static data and the signal catalog seed, so it
fails at commit time without AWS credentials. Producers are discovered, not
enumerated (same design as test_seed_catalog_dtc_uniqueness_offline.py): a new
collection that adds a coolant rule is covered automatically.
"""
import json
import os
import sys

import pytest

_SCRIPTS = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _SCRIPTS)
import seed_event_catalog  # noqa: E402
import seed_vsa_demo_events  # noqa: E402
import seed_dtc_catalog_gap_fill  # noqa: E402

_MODULES = (
    (seed_event_catalog, "seed_event_catalog"),
    (seed_vsa_demo_events, "seed_vsa_demo_events"),
    (seed_dtc_catalog_gap_fill, "seed_dtc_catalog_gap_fill"),
)

_FIELD = "coolant_temp"

# Water boils at 212 °F at sea level. A P0 overheat or fire threshold on a °F
# coolant signal below this sits inside the normal operating band (the
# simulators emit 180-210 °F), so it would fire on healthy engines.
_MIN_P0_THRESHOLD_F = 212.0

# event_catalog_driver._compute_target drives a forced ">" event toward
# threshold * 1.3. That target must stay inside the signal's encodable range,
# or a forced demo event overflows the 12-bit CAN signal instead of firing.
_FORCED_TARGET_FACTOR = 1.3


def _coolant_rules() -> list:
    """(producer, entry) for every rule whose json_fields include coolant_temp."""
    found = []
    for mod, modname in _MODULES:
        for name, val in vars(mod).items():
            if not (isinstance(val, list) and val and isinstance(val[0], dict)):
                continue
            for entry in val:
                if _FIELD in (entry.get("json_fields") or []):
                    found.append((f"{modname}.{name}", entry))
    return found


def _signal() -> dict:
    with open(os.path.join(_SCRIPTS, "signal_catalog_seed.json")) as fh:
        rows = json.load(fh)
    matches = [r for r in rows if r.get("json_field") == _FIELD]
    assert len(matches) == 1, f"expected one {_FIELD} signal, found {len(matches)}"
    return matches[0]


def test_discovery_finds_both_p0_coolant_rules():
    ids = {e["event_id"] for _, e in _coolant_rules()}
    assert {"maintenance.coolant_critical_overheat", "maintenance.thermal_runaway"} <= ids, ids


def test_signal_unit_is_fahrenheit():
    # The thresholds below are only correct while the signal is °F. If this
    # changes, convert every coolant_temp threshold in the same commit.
    assert _signal()["unit"] == "\u00b0F"


@pytest.mark.parametrize("producer,entry", _coolant_rules(),
                         ids=lambda v: v if isinstance(v, str) else v.get("event_id"))
def test_p0_coolant_threshold_is_above_normal_operating_range(producer, entry):
    if entry.get("severity_hint") != "P0" or entry.get("threshold_operator") not in (">", ">="):
        pytest.skip("not a P0 upper-bound rule")
    threshold = float(entry["threshold_value"])
    assert threshold > _MIN_P0_THRESHOLD_F, (
        f"{producer}:{entry['event_id']} threshold {threshold} is inside the normal "
        f"°F operating band — is it still in °C?"
    )


@pytest.mark.parametrize("producer,entry", _coolant_rules(),
                         ids=lambda v: v if isinstance(v, str) else v.get("event_id"))
def test_forced_event_target_fits_signal_range(producer, entry):
    if entry.get("threshold_operator") not in (">", ">="):
        pytest.skip("not an upper-bound rule")
    ceiling = float(_signal()["max_value"])
    target = float(entry["threshold_value"]) * _FORCED_TARGET_FACTOR
    assert target <= ceiling, (
        f"{producer}:{entry['event_id']} forced target {target:.1f} exceeds the "
        f"signal ceiling {ceiling}; the event could not be forced on a CAN vehicle"
    )


def test_producers_agree_on_each_coolant_threshold():
    # Whichever seeder runs last wins, so two producers writing different values
    # for the same event_id make the live threshold depend on run order.
    by_id: dict = {}
    for producer, e in _coolant_rules():
        by_id.setdefault(e["event_id"], set()).add(float(e["threshold_value"]))
    disagree = {k: v for k, v in by_id.items() if len(v) > 1}
    assert not disagree, disagree


def test_fire_threshold_is_above_stop_driving_threshold():
    t = {e["event_id"]: float(e["threshold_value"]) for _, e in _coolant_rules()}
    assert t["maintenance.thermal_runaway"] > t["maintenance.coolant_critical_overheat"]
