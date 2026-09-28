"""Tests for oem1-transform.json manifest.

B2.1 of spec 2026-06-01-cms-oem1-transform-manifest-staging-e2e.
"""
import json
import os
import pytest

MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "../manifests/oem1-transform.json")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "../transform-manifest-schema.json")

PRD_MUST_HAVE_SIGNALS = [
    "speed", "odometer", "lat", "lng", "heading", "ignitionOn", "engineRPM",
    "engineTemp", "fuelLevel", "batteryVoltage", "tire_fl", "tire_fr",
    "tire_rl", "tire_rr", "seatbeltStatus", "gearPosition", "acceleration",
    "lateralG", "throttle", "oil_life", "engine_hours_total", "traction_control",
]

REQUIRED_UNIT_CONVERSIONS = [
    "mps_to_mph", "km_to_miles", "C_to_F", "kpa_to_psi", "mps2_to_g", "seconds_to_hours",
]

REQUIRED_ENUM_MAPS = [
    "IGNITION_STATUS", "GEAR_LEVER_POSITION", "SEAT_BELT_STATUS", "TRACTION_CONTROL_STATUS",
]

VHA_EVENT_TYPES = [
    "diagnostic_warning", "diagnostic_warning_cleared", "dtc_set", "dtc_cleared",
]


@pytest.fixture(scope="module")
def manifest():
    with open(MANIFEST_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def schema():
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def test_manifest_validates_against_schema(manifest, schema):
    """Schema validation must succeed."""
    import jsonschema
    jsonschema.validate(manifest, schema)


def test_all_must_have_signals_present(manifest):
    """All 22 PRD MUST-HAVE signals have a signal_mappings entry."""
    mapped = {m["cms_field"] for m in manifest["signal_mappings"]}
    missing = [s for s in PRD_MUST_HAVE_SIGNALS if s not in mapped]
    assert not missing, f"Missing PRD MUST-HAVE signals: {missing}"


def test_all_unit_conversions_present(manifest):
    """All 6 required unit_conversions are declared in the manifest."""
    uc = manifest.get("unit_conversions", {})
    missing = [k for k in REQUIRED_UNIT_CONVERSIONS if k not in uc]
    assert not missing, f"Missing unit_conversions: {missing}"
    assert len(uc) >= 6


def test_all_enum_maps_present(manifest):
    """All 4 required enum maps are present."""
    em = manifest.get("enum_maps", {})
    missing = [k for k in REQUIRED_ENUM_MAPS if k not in em]
    assert not missing, f"Missing enum_maps: {missing}"
    assert len(em) >= 4


def test_event_mappings_for_4_vha_types(manifest):
    """4 event_mappings entries exist; each has a uniqueness_key and severity_map with rules."""
    mappings = manifest.get("event_mappings", [])
    cms_types = [m["cms_event_type"] for m in mappings]
    for vha_type in VHA_EVENT_TYPES:
        assert vha_type in cms_types, f"Missing event_mapping for cms_event_type={vha_type}"

    for m in mappings:
        if m["cms_event_type"] in VHA_EVENT_TYPES:
            assert "uniqueness_key" in m, f"Missing uniqueness_key on {m['cms_event_type']}"
            assert len(m["uniqueness_key"]) >= 4, (
                f"uniqueness_key must have ≥4 fields, got {m['uniqueness_key']}"
            )
            assert "severity_map" in m, f"Missing severity_map on {m['cms_event_type']}"
            rules = m["severity_map"].get("rules", [])
            assert len(rules) >= 1, f"severity_map.rules empty on {m['cms_event_type']}"


def test_severity_rule_high_plus_dtc_on_engine_brake_safety_promotes_to_critical(manifest):
    """Severity rules: HIGH+DTC on ENGINE/BRAKE/SAFETY -> CRITICAL; otherwise HIGH."""
    # Collect all rules across all event_mappings with VHA types
    critical_rules = []
    for m in manifest.get("event_mappings", []):
        if m["cms_event_type"] not in VHA_EVENT_TYPES:
            continue
        for rule in m.get("severity_map", {}).get("rules", []):
            cond = rule.get("if", {})
            if (
                cond.get("severity") == "HIGH"
                and cond.get("dtc_present") is True
                and "dtc_system_in" in cond
                and rule.get("then") == "CRITICAL"
            ):
                critical_rules.append(rule)

    assert critical_rules, "No CRITICAL promotion rule found across event_mappings"

    # Verify dtc_system_in contains the exact OEM1 values
    for rule in critical_rules:
        systems = rule["if"]["dtc_system_in"]
        assert "ENGINE" in systems, "ENGINE missing from dtc_system_in"
        assert "BRAKE" in systems, "BRAKE missing from dtc_system_in"
        assert "SAFETY" in systems, "SAFETY missing from dtc_system_in"

    # Verify default mapping preserves HIGH as HIGH (not CRITICAL)
    for m in manifest.get("event_mappings", []):
        if m["cms_event_type"] not in VHA_EVENT_TYPES:
            continue
        default = m.get("severity_map", {}).get("default", {})
        assert default.get("HIGH") == "HIGH", (
            f"Default HIGH->HIGH passthrough missing on {m['cms_event_type']}"
        )


def test_canonical_trip_report_mapping_present(manifest):
    """cms.trip_report mapping exists in event_mappings."""
    mappings = manifest.get("event_mappings", [])
    trip_mappings = [m for m in mappings if m["cms_event_type"] == "cms.trip_report"]
    assert trip_mappings, "No cms.trip_report event_mapping found"
    trip = trip_mappings[0]
    extraction = trip.get("extraction", {})
    assert "trip_id" in extraction, "trip_report extraction missing trip_id"
    assert "start_time" in extraction, "trip_report extraction missing start_time"
    assert "end_time" in extraction, "trip_report extraction missing end_time"


def test_deferred_signals_nonempty(manifest):
    """metadata.deferred_signals is a non-empty list."""
    deferred = manifest.get("metadata", {}).get("deferred_signals", [])
    assert isinstance(deferred, list), "deferred_signals must be a list"
    assert len(deferred) > 0, "deferred_signals must not be empty"
    # Each entry must have source_signal and reason
    for entry in deferred:
        assert "source_signal" in entry, f"deferred entry missing source_signal: {entry}"
        assert "reason" in entry, f"deferred entry missing reason: {entry}"


def test_created_by_audit_trail(manifest):
    """metadata.created_by has all 4 wizard audit-trail fields."""
    created_by = manifest.get("metadata", {}).get("created_by", {})
    assert isinstance(created_by, dict), "created_by must be an object"
    for field in ("user_id", "timestamp", "wizard_version", "manifest_version"):
        assert field in created_by, f"created_by missing field: {field}"
        assert created_by[field], f"created_by.{field} must be non-empty"
    assert created_by["manifest_version"] == "2.1.0"


# ---------------------------------------------------------------------------
# B2 Way B — source_path format and compound-row tests
# ---------------------------------------------------------------------------

WAY_B_SIGNAL_PATH = {
    "SPEED":                    "[?signal.wksSignal=SPEED].speedValue.speed",
    "ODOMETER":                 "[?signal.wksSignal=ODOMETER].doubleValue",
    "POSITION_LAT":             "[?signal.wksSignal=POSITION].positionValue.location.latitude",
    "POSITION_LNG":             "[?signal.wksSignal=POSITION].positionValue.location.longitude",
    "HEADING":                  "[?signal.wksSignal=HEADING].headingValue.heading",
    "IGNITION_STATUS":          "[?signal.wksSignal=IGNITION_STATUS].enumValue.ignitionStatus",
    "ENGINE_SPEED":             "[?signal.wksSignal=ENGINE_SPEED].int64Value",
    "ENGINE_COOLANT_TEMP":      "[?signal.wksSignal=ENGINE_COOLANT_TEMP].doubleValue",
    "FUEL_LEVEL":               "[?signal.wksSignal=FUEL_LEVEL].doubleValue",
    "BATTERY_VOLTAGE":          "[?signal.wksSignal=BATTERY_VOLTAGE].doubleValue",
    "TIRE_PRESSURE_FL":         "[?signal.wksSignal=TIRE_PRESSURE][?tags[?name.wktName=VEHICLE_WHEEL].value.wheelTagValue=FRONT_LEFT].doubleValue",
    "TIRE_PRESSURE_FR":         "[?signal.wksSignal=TIRE_PRESSURE][?tags[?name.wktName=VEHICLE_WHEEL].value.wheelTagValue=FRONT_RIGHT].doubleValue",
    "TIRE_PRESSURE_RL":         "[?signal.wksSignal=TIRE_PRESSURE][?tags[?name.wktName=VEHICLE_WHEEL].value.wheelTagValue=REAR_LEFT].doubleValue",
    "TIRE_PRESSURE_RR":         "[?signal.wksSignal=TIRE_PRESSURE][?tags[?name.wktName=VEHICLE_WHEEL].value.wheelTagValue=REAR_RIGHT].doubleValue",
    "SEAT_BELT_STATUS":         "[?signal.wksSignal=SEAT_BELT_STATUS].enumValue.seatbeltStatus",
    "GEAR_LEVER_POSITION":      "[?signal.wksSignal=GEAR_LEVER_POSITION].enumValue.gearPosition",
    "ACCELERATION_LONGITUDINAL":"[?signal.wksSignal=ACCELERATION].threeAxisValue.x",
    "ACCELERATION_LATERAL":     "[?signal.wksSignal=ACCELERATION].threeAxisValue.y",
    "THROTTLE_POSITION":        "[?signal.wksSignal=THROTTLE_POSITION].doubleValue",
    "OIL_LIFE_REMAINING":       "[?signal.wksSignal=OIL_LIFE_REMAINING].doubleValue",
    "TOTAL_ENGINE_TIME":        "[?signal.wksSignal=TOTAL_ENGINE_TIME].doubleValue",
    "TRACTION_CONTROL_STATUS":  "[?signal.wksSignal=TRACTION_CONTROL_STATUS].enumValue.offOnStatus",
}


def _signal_map(manifest):
    return {r["source_signal"]: r for r in manifest["signal_mappings"]}


def test_way_b_all_source_paths_match_table(manifest):
    """Every signal_mapping source_path matches the architect-signed-off Way B table exactly."""
    by_signal = _signal_map(manifest)
    for source_signal, expected_path in WAY_B_SIGNAL_PATH.items():
        row = by_signal.get(source_signal)
        assert row is not None, f"Missing signal_mapping for source_signal={source_signal}"
        assert row["source_path"] == expected_path, (
            f"{source_signal}: expected source_path\n  {expected_path!r}\n"
            f"  got {row['source_path']!r}"
        )


def test_way_b_tire_pressure_compound_rows_have_distinct_tag_predicates(manifest):
    """4 TIRE_PRESSURE compound rows each have a distinct VEHICLE_WHEEL tag predicate."""
    tire_rows = [r for r in manifest["signal_mappings"] if r["source_signal"].startswith("TIRE_PRESSURE")]
    assert len(tire_rows) == 4, f"Expected 4 TIRE_PRESSURE rows, got {len(tire_rows)}"
    wheel_values = {"FRONT_LEFT", "FRONT_RIGHT", "REAR_LEFT", "REAR_RIGHT"}
    for row in tire_rows:
        path = row["source_path"]
        assert "VEHICLE_WHEEL" in path, f"VEHICLE_WHEEL tag predicate missing in {row['source_signal']}: {path}"
        # Each path must end with .doubleValue
        assert path.endswith(".doubleValue"), f"Expected .doubleValue at end: {path}"
    # All 4 wheel values present across the 4 paths
    found = set()
    for row in tire_rows:
        for wv in wheel_values:
            if wv in row["source_path"]:
                found.add(wv)
    assert found == wheel_values, f"Missing wheel tag values: {wheel_values - found}"


def test_way_b_acceleration_compound_rows_use_three_axis_subfield(manifest):
    """ACCELERATION_LONGITUDINAL uses threeAxisValue.x; ACCELERATION_LATERAL uses threeAxisValue.y."""
    by_signal = _signal_map(manifest)
    long_row = by_signal["ACCELERATION_LONGITUDINAL"]
    lat_row = by_signal["ACCELERATION_LATERAL"]
    assert long_row["source_path"].endswith(".threeAxisValue.x"), (
        f"LONGITUDINAL must use .threeAxisValue.x, got: {long_row['source_path']}"
    )
    assert lat_row["source_path"].endswith(".threeAxisValue.y"), (
        f"LATERAL must use .threeAxisValue.y, got: {lat_row['source_path']}"
    )
    # Both share the same signal selector
    assert "[?signal.wksSignal=ACCELERATION]" in long_row["source_path"]
    assert "[?signal.wksSignal=ACCELERATION]" in lat_row["source_path"]


def test_way_b_position_uses_position_value_sub_fields(manifest):
    """POSITION_LAT and POSITION_LNG use positionValue.location.latitude/longitude (P-POS pattern)."""
    by_signal = _signal_map(manifest)
    lat_row = by_signal["POSITION_LAT"]
    lng_row = by_signal["POSITION_LNG"]
    assert "positionValue.location.latitude" in lat_row["source_path"]
    assert "positionValue.location.longitude" in lng_row["source_path"]
    # Both share the same signal selector
    assert "[?signal.wksSignal=POSITION]" in lat_row["source_path"]
    assert "[?signal.wksSignal=POSITION]" in lng_row["source_path"]


def test_way_b_enum_rows_use_per_signal_sub_fields(manifest):
    """Enum-valued rows reference the per-signal enumValue sub-field (P-ENUM pattern)."""
    expected = {
        "IGNITION_STATUS":       "enumValue.ignitionStatus",
        "SEAT_BELT_STATUS":      "enumValue.seatbeltStatus",
        "GEAR_LEVER_POSITION":   "enumValue.gearPosition",
        "TRACTION_CONTROL_STATUS": "enumValue.offOnStatus",
    }
    by_signal = _signal_map(manifest)
    for source_signal, sub_field in expected.items():
        row = by_signal[source_signal]
        assert sub_field in row["source_path"], (
            f"{source_signal}: expected {sub_field!r} in source_path, got {row['source_path']!r}"
        )


def test_way_b_total_engine_time_uses_named_unit_conversion(manifest):
    """TOTAL_ENGINE_TIME uses unit_conversion: 'seconds_to_hours' (no inline conversion)."""
    by_signal = _signal_map(manifest)
    row = by_signal["TOTAL_ENGINE_TIME"]
    assert row.get("unit_conversion") == "seconds_to_hours", (
        f"Expected unit_conversion='seconds_to_hours', got {row.get('unit_conversion')!r}"
    )
    assert "conversion" not in row or row.get("conversion") is None, (
        "Inline 'conversion' field must not be present; use named unit_conversion"
    )


def test_way_b_no_inline_conversion_fields_anywhere(manifest):
    """No signal_mapping row uses the legacy inline 'conversion' object (all use named unit_conversion)."""
    for row in manifest["signal_mappings"]:
        assert "conversion" not in row, (
            f"Inline 'conversion' found on {row['source_signal']}; use named unit_conversion instead"
        )
