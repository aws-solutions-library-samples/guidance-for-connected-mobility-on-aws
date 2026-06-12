"""Tests for transform-manifest-schema.json v2.1.0 additive extensions.

Cases required by spec A2.1:
  (i)   v2.0.0 manifest validates against v2.1.0 schema unchanged.
  (ii)  v2.1.0 manifest with all new fields validates.
  (iii) event_mappings missing → lenient default (no event routing, still valid).
  (iv)  timestamp.modem_field missing → falls back to top-level timestamp_field (still valid).
"""
import json
import os
import pytest
import jsonschema

_HERE = os.path.dirname(__file__)
_SCHEMA_PATH = os.path.join(_HERE, "..", "transform-manifest-schema.json")
_MANIFESTS_DIR = os.path.join(_HERE, "..", "manifests")


@pytest.fixture(scope="module")
def schema():
    with open(_SCHEMA_PATH) as f:
        return json.load(f)


def validate(instance, schema):
    """Raise jsonschema.ValidationError if invalid."""
    jsonschema.validate(instance, schema)


# ---------------------------------------------------------------------------
# (i) v2.0.0 manifests validate against v2.1.0 schema unchanged
# ---------------------------------------------------------------------------

def test_existing_grpc_manifest_validates(schema):
    """Existing grpc-streaming-sample-transform.json (v2.0.0) must validate unchanged."""
    path = os.path.join(_MANIFESTS_DIR, "grpc-streaming-sample-transform.json")
    with open(path) as f:
        manifest = json.load(f)
    validate(manifest, schema)


def test_existing_rest_manifest_validates(schema):
    """Existing rest-polling-sample-transform.json (v2.0.0) must validate unchanged."""
    path = os.path.join(_MANIFESTS_DIR, "rest-polling-sample-transform.json")
    with open(path) as f:
        manifest = json.load(f)
    validate(manifest, schema)


def test_existing_oem_template_validates(schema):
    """Existing oem-transform-template.json (v1.0.0) must validate against v2.1.0 schema."""
    path = os.path.join(_MANIFESTS_DIR, "oem-transform-template.json")
    with open(path) as f:
        manifest = json.load(f)
    validate(manifest, schema)


# ---------------------------------------------------------------------------
# (ii) v2.1.0 manifest with all new fields validates
# ---------------------------------------------------------------------------

_V21_FULL = {
    "manifest_version": "2.1.0",
    "transform_type": "cloud_to_cloud",
    "source_name": "oem1",
    "source_format": "json",
    "connection": {"type": "grpc_streaming"},
    "authentication": {
        "type": "oauth2",
        "credentials_secret_arn": "arn:aws:secretsmanager:us-west-2:123456789012:secret:cms-staging-connector-oem1-credentials",
        "token_endpoint": "https://api.oem1.example/oauth2/token"
    },
    "vehicle_id_extraction": {
        "strategy": "json_path",
        "path": "shard_key",
        "transform": "substring_after_last_slash"
    },
    "timestamp_field": "timestamp",
    "timestamp_format": "iso8601",
    "timestamp": {
        "modem_field": "typedData.modemUtc",
        "ingestion_field": "timestamp",
        "primary": "modem"
    },
    "message_type_routing": {
        "field": "typedData.@type",
        "telemetry_patterns": ["Metric", "ErrorMetric", "RawTelemetry", "BatchedTelemetry"],
        "event_patterns": ["Event", "TriggeredEvent", "StateTransition", "GeofenceEvent", "DeepSleepPreclusion"],
        "discard_patterns": ["BootstrapSummaryEvent", "BindingChangeEvent", "DataValidationEvent"]
    },
    "event_mappings": [
        {
            "source_event_type_url": "type.googleapis.com/oem1.feed.v1.VehicleHealthAlert",
            "cms_event_type": "diagnostic_warning",
            "match": {"indicator_state": "ON"},
            "extraction": {
                "indicator": "metrics[0].indicatorValue.wellKnownIndicator",
                "dtc_raw": "metrics[0].metrics[0].dtcValue.rawValue",
                "dtc_system": "metrics[0].metrics[0].dtcValue.system"
            },
            "tag_aliases": {"severity": "Severity", "symptom_key": "symptomKey"},
            "severity_map": {
                "default": {"HIGH": "HIGH", "MEDIUM": "MEDIUM", "LOW": "LOW"},
                "rules": [
                    {
                        "if": {"severity": "HIGH", "dtc_present": True, "dtc_system_in": ["ENGINE", "BRAKE", "SAFETY"]},
                        "then": "CRITICAL"
                    }
                ]
            },
            "uniqueness_key": ["indicator", "dtc_raw", "symptom_key", "customer_action_key"]
        }
    ],
    "signal_mappings": [
        {"cms_field": "speed", "source_path": "speed", "data_type": "float", "unit_conversion": "mps_to_mph"}
    ],
    "metadata": {
        "created_by": "oem-integration-wizard",
        "created_at": "2026-06-02T00:00:00Z",
        "version": "2.1.0",
        "description": "OEM1 staging manifest",
        "deferred_signals": [
            {"source_signal": "DOOR_AJAR_STATUS", "reason": "no catalog match"}
        ],
        "subscription_tier": "Premium"
    }
}


def test_v21_full_manifest_validates(schema):
    """v2.1.0 manifest with all new fields (message_type_routing, event_mappings,
    dual-source timestamp, deferred_signals, subscription_tier) must validate."""
    validate(_V21_FULL, schema)


# ---------------------------------------------------------------------------
# (iii) event_mappings missing → lenient default (still valid)
# ---------------------------------------------------------------------------

def test_missing_event_mappings_is_valid(schema):
    """Manifest without event_mappings is valid (lenient default: no event routing)."""
    manifest = {k: v for k, v in _V21_FULL.items() if k != "event_mappings"}
    assert "event_mappings" not in manifest
    validate(manifest, schema)


# ---------------------------------------------------------------------------
# (iv) timestamp.modem_field missing → falls back to top-level timestamp_field (still valid)
# ---------------------------------------------------------------------------

def test_missing_timestamp_modem_field_is_valid(schema):
    """Manifest where timestamp block omits modem_field is valid
    (processor falls back to top-level timestamp_field)."""
    import copy
    manifest = copy.deepcopy(_V21_FULL)
    manifest["timestamp"] = {"ingestion_field": "timestamp", "primary": "ingestion"}
    assert "modem_field" not in manifest["timestamp"]
    validate(manifest, schema)


# ---------------------------------------------------------------------------
# Extra: schema version field reflects the bump
# ---------------------------------------------------------------------------

def test_schema_version_is_2_1_0(schema):
    """Schema document must declare version 2.1.0."""
    assert schema["version"] == "2.1.0"
