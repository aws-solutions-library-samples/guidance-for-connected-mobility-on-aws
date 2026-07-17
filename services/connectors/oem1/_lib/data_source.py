# Spec: 2026-06-09-cms-data-source-model-refactor
# Dual-read transitional helper — accepts both old and new data_source enum strings.

_CLOUD_TELEMETRY_VALUES = frozenset({"cloud-telemetry", "cloud-oem1"})
_VEHICLE_TELEMETRY_VALUES = frozenset({"vehicle-telemetry", "onboard-fwe"})


def is_cloud_telemetry_fleet(fleet_item: dict) -> bool:
    """Return True if the fleet is configured for cloud-fed telemetry.

    Dual-read transitional helper — accepts both old and new enum
    strings. Missing data_source defaults to vehicle-telemetry (False).
    """
    raw = fleet_item.get("data_source", {}).get("S", "vehicle-telemetry")
    return raw in _CLOUD_TELEMETRY_VALUES
