"""
Test skeletons for compound_splitter.py (RED phase — B1.1).

Encodes behaviors from spec § Design § Architecture connector responsibilities:
- TIRE_PRESSURE: 1 compound message → 4 individual messages (FL/FR/RL/RR per wheel position)
- ACCELERATION: 1 compound message → 2 individual messages (longitudinal/lateral)

Tests import CompoundSplitter inside each test body; pytest collects them all
but every test FAILS (ImportError) until compound_splitter.py lands in B1.2.
"""
import sys
from pathlib import Path

import pytest

_OEM1_DIR = Path(__file__).parent.parent
if str(_OEM1_DIR) not in sys.path:
    sys.path.insert(0, str(_OEM1_DIR))


def _tire_pressure_message() -> dict:
    """Minimal compound TIRE_PRESSURE message with all four wheel readings."""
    return {
        "signal_type": "TIRE_PRESSURE",
        "vehicle_id": "VIN-TEST-001",
        "timestamp": "2026-06-02T12:00:00Z",
        "wheels": {
            "FL": {"value": 32.1, "unit": "PSI"},
            "FR": {"value": 32.5, "unit": "PSI"},
            "RL": {"value": 31.8, "unit": "PSI"},
            "RR": {"value": 31.9, "unit": "PSI"},
        },
    }


def _acceleration_message() -> dict:
    """Minimal compound ACCELERATION message with longitudinal and lateral readings."""
    return {
        "signal_type": "ACCELERATION",
        "vehicle_id": "VIN-TEST-001",
        "timestamp": "2026-06-02T12:00:00Z",
        "components": {
            "longitudinal": {"value": 0.15, "unit": "g"},
            "lateral": {"value": -0.05, "unit": "g"},
        },
    }


# ---------------------------------------------------------------------------
# TIRE_PRESSURE split: 1 → 4 messages (FL/FR/RL/RR)
# ---------------------------------------------------------------------------

def test_tire_pressure_splits_into_four_messages():
    """A single TIRE_PRESSURE compound message must be split into exactly 4 messages."""
    from compound_splitter import CompoundSplitter

    splitter = CompoundSplitter()
    messages = splitter.split(_tire_pressure_message())

    assert len(messages) == 4, (
        f"TIRE_PRESSURE must produce 4 messages (FL/FR/RL/RR), got {len(messages)}"
    )


def test_tire_pressure_split_contains_all_wheel_positions():
    """Each split message must correspond to one distinct wheel position."""
    from compound_splitter import CompoundSplitter

    splitter = CompoundSplitter()
    messages = splitter.split(_tire_pressure_message())

    positions = {msg.get("wheel_position") for msg in messages}
    assert positions == {"FL", "FR", "RL", "RR"}, (
        f"Expected positions {{FL, FR, RL, RR}}, got {positions}"
    )


def test_tire_pressure_fl_message_has_correct_value():
    """FL split message must carry the FL wheel's pressure value."""
    from compound_splitter import CompoundSplitter

    splitter = CompoundSplitter()
    messages = splitter.split(_tire_pressure_message())

    fl = next(m for m in messages if m.get("wheel_position") == "FL")
    assert fl.get("value") == pytest.approx(32.1), "FL wheel pressure value must match input"


def test_tire_pressure_split_preserves_vehicle_id():
    """Each split message must carry the original vehicle_id."""
    from compound_splitter import CompoundSplitter

    splitter = CompoundSplitter()
    messages = splitter.split(_tire_pressure_message())

    for msg in messages:
        assert msg.get("vehicle_id") == "VIN-TEST-001", (
            "vehicle_id must be preserved in each split message"
        )


# ---------------------------------------------------------------------------
# ACCELERATION split: 1 → 2 messages (longitudinal/lateral)
# ---------------------------------------------------------------------------

def test_acceleration_splits_into_two_messages():
    """A single ACCELERATION compound message must be split into exactly 2 messages."""
    from compound_splitter import CompoundSplitter

    splitter = CompoundSplitter()
    messages = splitter.split(_acceleration_message())

    assert len(messages) == 2, (
        f"ACCELERATION must produce 2 messages (longitudinal/lateral), got {len(messages)}"
    )


def test_acceleration_split_contains_longitudinal_and_lateral():
    """Split messages must cover longitudinal and lateral components."""
    from compound_splitter import CompoundSplitter

    splitter = CompoundSplitter()
    messages = splitter.split(_acceleration_message())

    components = {msg.get("component") for msg in messages}
    assert components == {"longitudinal", "lateral"}, (
        f"Expected {{longitudinal, lateral}}, got {components}"
    )


def test_acceleration_longitudinal_has_correct_value():
    """Longitudinal split message must carry the correct value."""
    from compound_splitter import CompoundSplitter

    splitter = CompoundSplitter()
    messages = splitter.split(_acceleration_message())

    lng = next(m for m in messages if m.get("component") == "longitudinal")
    assert lng.get("value") == pytest.approx(0.15)


def test_acceleration_split_preserves_vehicle_id():
    """Each acceleration split message must carry the original vehicle_id."""
    from compound_splitter import CompoundSplitter

    splitter = CompoundSplitter()
    messages = splitter.split(_acceleration_message())

    for msg in messages:
        assert msg.get("vehicle_id") == "VIN-TEST-001"
