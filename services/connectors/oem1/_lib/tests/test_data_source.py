import pytest
from _lib.data_source import is_cloud_telemetry_fleet


def _item(value):
    return {"data_source": {"S": value}}


@pytest.mark.parametrize("fleet_item,expected", [
    (_item("cloud-telemetry"), True),   # (a) new cloud-telemetry value
    (_item("cloud-oem1"), True),        # (b) legacy cloud-oem1 value (dual-read)
    (_item("vehicle-telemetry"), False),  # (c) new vehicle-telemetry value
    (_item("onboard-fwe"), False),      # (d) legacy onboard-fwe value (dual-read)
    ({}, False),                        # (e) missing data_source attribute
    (_item("unknown-string"), False),   # (f) unknown string — defensive default
])
def test_is_cloud_telemetry_fleet(fleet_item, expected):
    assert is_cloud_telemetry_fleet(fleet_item) is expected
