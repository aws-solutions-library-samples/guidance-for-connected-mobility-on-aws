"""
Task 3.1 tests: Event-family decode path in _kafka_raw_payload.

Covers:
  (a) one test per dispatched suffix asserting MessageToJson round-trip vs fixture
  (b) regression: unknown-suffix routes to _raw_hex
  (c) regression: _DISCARD_SUFFIXES payloads never reach the Event branch

Fixtures in tests/fixtures/event_samples/*.bin are built from real proto classes
with vehicleId redacted. See decisions.md § Phase A.3.
"""
import struct
import sys
import time
from pathlib import Path

import pytest

_OEM1_DIR = Path(__file__).parent.parent
_GEN = _OEM1_DIR / "_generated"
for _p in (str(_OEM1_DIR), str(_GEN)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_FIXTURES = _OEM1_DIR / "tests" / "fixtures" / "event_samples"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ref_bytes(seq: int = 42) -> bytes:
    return struct.pack(">Q", seq)


def _feed_event_from_bytes(raw_bytes: bytes, type_url: str, shard_key: str = "aui:asset:vehicle/<vehicleId>"):
    """Wrap raw proto bytes as a FeedEvent.typed_data Any with the given type_url."""
    from autonomic.ext.feed.consumer import consumer_pb2
    from google.protobuf import any_pb2

    a = any_pb2.Any()
    a.type_url = type_url
    a.value = raw_bytes

    evt = consumer_pb2.FeedEvent()
    evt.reference = _ref_bytes()
    evt.timestamp.seconds = int(time.time())
    evt.shard_key = shard_key
    evt.typed_data.CopyFrom(a)
    return evt


def _raw(raw_bytes: bytes, type_url: str):
    from connector import OEM1Connector
    evt = _feed_event_from_bytes(raw_bytes, type_url)
    return OEM1Connector._kafka_raw_payload(evt)


def _load_fixture(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


# ---------------------------------------------------------------------------
# (a) One test per dispatched suffix — MessageToJson round-trip vs fixture
# ---------------------------------------------------------------------------

class TestEventSuffixDispatch:
    """Verify each dispatched Event-family suffix decodes to a non-raw-hex dict."""

    def test_event_motion_decodes_to_triggered_event_json(self):
        """Event suffix: outer Event wrapping MOTION_EVENT TriggeredEvent roundtrips correctly."""
        raw = _load_fixture("motion_event.bin")
        type_url = "type.googleapis.com/autonomic.ext.event.Event"
        out = _raw(raw, type_url)

        assert out is not None
        value = out["typedData"]["value"]
        assert "_raw_hex" not in value, f"Expected decoded JSON, got _raw_hex: {value}"
        # After Unpack, the JSON is the TriggeredEvent fields (camelCase)
        assert value.get("wellKnownLabel") == "MOTION_EVENT"
        assert "conditions" in value
        assert len(value["conditions"]) >= 1
        assert value["conditions"][0]["condition"] == "VEHICLE_MOVEMENT_STARTED"

    def test_event_harsh_acceleration_decodes(self):
        """Event suffix: HARSH_ACCELERATION_EVENT TriggeredEvent roundtrips correctly."""
        raw = _load_fixture("harsh_acceleration_event.bin")
        type_url = "type.googleapis.com/autonomic.ext.event.Event"
        out = _raw(raw, type_url)

        assert out is not None
        value = out["typedData"]["value"]
        assert "_raw_hex" not in value
        assert value.get("wellKnownLabel") == "HARSH_ACCELERATION_EVENT"
        assert value["conditions"][0]["condition"] == "HARSH_ACCELERATION_STARTED"

    def test_event_harsh_braking_decodes(self):
        """Event suffix: HARSH_BRAKING_EVENT TriggeredEvent roundtrips correctly."""
        raw = _load_fixture("harsh_braking_event.bin")
        type_url = "type.googleapis.com/autonomic.ext.event.Event"
        out = _raw(raw, type_url)

        assert out is not None
        value = out["typedData"]["value"]
        assert "_raw_hex" not in value
        assert value.get("wellKnownLabel") == "HARSH_BRAKING_EVENT"
        assert value["conditions"][0]["condition"] == "HARSH_BRAKING_STARTED"

    def test_triggered_event_suffix_decodes_directly(self):
        """TriggeredEvent suffix at top-level: decode directly without outer Event Unpack."""
        from autonomic.ext.event.event_pb2 import TriggeredEvent
        from autonomic.ext.event.well_known_events_pb2 import WellKnownEvent
        from autonomic.ext.event.well_known_conditions_pb2 import WellKnownEventCondition

        te = TriggeredEvent()
        te.well_known_label = WellKnownEvent.Value("MOTION_EVENT")
        cond = te.conditions.add()
        cond.condition = WellKnownEventCondition.Value("VEHICLE_MOVEMENT_STARTED")
        raw = te.SerializeToString()
        type_url = "type.googleapis.com/autonomic.ext.event.TriggeredEvent"

        out = _raw(raw, type_url)
        assert out is not None
        value = out["typedData"]["value"]
        assert "_raw_hex" not in value
        assert value.get("wellKnownLabel") == "MOTION_EVENT"

    def test_state_transition_suffix_decodes_directly(self):
        """StateTransition suffix at top-level: decode directly (no inner Unpack)."""
        from autonomic.ext.event.event_pb2 import StateTransition
        st = StateTransition()
        # string_fsm_name is a oneof-covered field (deprecated) — set wk_fsm_name instead
        # Just verify it serializes/deserializes without error
        raw = st.SerializeToString()
        type_url = "type.googleapis.com/autonomic.ext.event.StateTransition"

        out = _raw(raw, type_url)
        assert out is not None
        value = out["typedData"]["value"]
        # An empty StateTransition serializes to {} (no _raw_hex)
        assert "_raw_hex" not in value

    def test_geofence_event_suffix_decodes_directly(self):
        """GeofenceEvent suffix at top-level: decode directly."""
        from autonomic.ext.event.event_pb2 import GeofenceEvent
        ge = GeofenceEvent()
        ge.type = GeofenceEvent.ENTER
        raw = ge.SerializeToString()
        type_url = "type.googleapis.com/autonomic.ext.event.GeofenceEvent"

        out = _raw(raw, type_url)
        assert out is not None
        value = out["typedData"]["value"]
        assert "_raw_hex" not in value
        assert value.get("type") == "ENTER"


# ---------------------------------------------------------------------------
# (b) Regression: unknown suffix → _raw_hex
# ---------------------------------------------------------------------------

class TestUnknownSuffixFallback:
    """Unknown-suffix payloads must still emit _raw_hex (prior behavior preserved)."""

    def test_unknown_suffix_routes_to_raw_hex(self):
        """A completely unknown type_url suffix falls back to _raw_hex."""
        raw = b"\x08\x01\x10\x02"  # arbitrary bytes
        type_url = "type.googleapis.com/autonomic.ext.telemetry.event.SomeNewUnknownType"
        out = _raw(raw, type_url)

        assert out is not None
        value = out["typedData"]["value"]
        assert "_raw_hex" in value, f"Expected _raw_hex fallback, got: {value}"
        assert value["_raw_hex"] == raw.hex()

    def test_error_metric_suffix_routes_to_raw_hex(self):
        """ErrorMetric is accepted but not decoded — still falls back to _raw_hex."""
        raw = b"\x08\x01"
        type_url = "type.googleapis.com/oem1.ext.telemetry.ErrorMetric"
        out = _raw(raw, type_url)

        assert out is not None
        value = out["typedData"]["value"]
        assert "_raw_hex" in value

    def test_string_label_event_decodes_full_triggered_event(self):
        """Event with vha-diagnostics string_label TriggeredEvent decodes to full JSON (not _raw_hex).

        RED PHASE: fails until connector.py:384-386 string_label→_raw_hex branch is removed (Group 2).
        Fixture: tests/fixtures/event_samples/vha_diagnostics_processed_event.bin (from real DLQ sample).
        """
        raw = _load_fixture("vha_diagnostics_processed_event.bin")
        type_url = "type.googleapis.com/autonomic.ext.event.Event"
        out = _raw(raw, type_url)

        assert out is not None
        value_dict = out["typedData"]["value"]
        assert "stringLabel" in value_dict, f"Expected stringLabel in decoded value, got: {list(value_dict.keys())}"
        assert value_dict["stringLabel"].endswith(":custom:vha-diagnostics-processed-event"), (
            f"Expected stringLabel ending with :custom:vha-diagnostics-processed-event, got: {value_dict['stringLabel']}"
        )
        assert value_dict["metrics"][0]["signal"]["wksSignal"] == "INDICATOR_LIGHT", (
            f"Expected metrics[0].signal.wksSignal == INDICATOR_LIGHT, got: {value_dict['metrics'][0]['signal']}"
        )
        assert value_dict["metrics"][0]["metrics"][0]["dtcValue"]["rawValue"] == "B124D", (
            f"Expected dtcValue.rawValue == B124D, got: {value_dict['metrics'][0]['metrics'][0]}"
        )
        assert value_dict["metrics"][0]["indicatorValue"]["wellKnownIndicator"] == "TIRE_PRESSURE_MONITOR_SYSTEM_WARNING", (
            f"Expected wellKnownIndicator == TIRE_PRESSURE_MONITOR_SYSTEM_WARNING"
        )
        severity_tags = [
            t for t in value_dict["metrics"][0]["tags"]
            if t.get("name", {}).get("stringName") == "Severity"
        ]
        assert len(severity_tags) >= 1 and severity_tags[0]["value"]["stringValue"] == "URGENT", (
            f"Expected Severity tag == URGENT, tags: {value_dict['metrics'][0]['tags']}"
        )

    def test_unknown_string_label_event_decodes_as_triggered_event(self):
        """Event with unrecognized string_label still decodes as TriggeredEvent (label content is manifest concern).

        RED PHASE: fails until connector.py:384-386 string_label→_raw_hex branch is removed (Group 2).
        Fixture: tests/fixtures/event_samples/string_label_custom_event.bin (synthetic unknown label).
        """
        raw = _load_fixture("string_label_custom_event.bin")
        type_url = "type.googleapis.com/autonomic.ext.event.Event"
        out = _raw(raw, type_url)

        assert out is not None
        value_dict = out["typedData"]["value"]
        assert "stringLabel" in value_dict, (
            f"Expected stringLabel in decoded value (not _raw_hex), got: {list(value_dict.keys())}"
        )
        assert "_raw_hex" not in value_dict, (
            "Connector must not route string_label events to _raw_hex — label filtering is manifest's job"
        )


# ---------------------------------------------------------------------------
# (c) Regression: _DISCARD_SUFFIXES never reach the Event branch
# ---------------------------------------------------------------------------

class TestDiscardSuffixes:
    """Payloads with discard-suffix type_urls must return None — never reach Event branch."""

    @pytest.mark.parametrize("suffix", [
        "BootstrapSummaryEvent",
        "BindingChangeEvent",
        "DataValidationEvent",
    ])
    def test_discard_suffix_returns_none(self, suffix):
        """_DISCARD_SUFFIXES payloads return None, not a decoded message."""
        raw = b"\x08\x01"
        type_url = f"type.googleapis.com/oem1.ext.telemetry.{suffix}"
        out = _raw(raw, type_url)
        assert out is None, f"Expected None for discard suffix {suffix}, got {out}"

    @pytest.mark.parametrize("suffix", [
        "BootstrapSummaryEvent",
        "BindingChangeEvent",
        "DataValidationEvent",
    ])
    def test_discard_suffix_vendor_namespace_returns_none(self, suffix):
        """Vendor-namespace discard suffixes also return None."""
        raw = b"\x08\x01"
        type_url = f"type.googleapis.com/autonomic.ext.telemetry.event.{suffix}"
        out = _raw(raw, type_url)
        assert out is None, f"Expected None for vendor discard suffix {suffix}, got {out}"


# ---------------------------------------------------------------------------
# Way B invariants preserved by Event branch (D5 regression guard)
# ---------------------------------------------------------------------------

class TestWayBInvariantsPreserved:
    """Event-branch output preserves oem_source and reference_hex (parent-spec D5 invariants)."""

    def test_oem_source_is_oem1_for_event(self):
        """D5 invariant: oem_source=oem1 preserved on Event-decoded messages."""
        raw = _load_fixture("motion_event.bin")
        type_url = "type.googleapis.com/autonomic.ext.event.Event"
        out = _raw(raw, type_url)
        assert out["oem_source"] == "oem1"

    def test_reference_hex_is_16_chars_for_event(self):
        """D5 invariant: reference_hex is 16-char hex on Event-decoded messages."""
        raw = _load_fixture("motion_event.bin")
        type_url = "type.googleapis.com/autonomic.ext.event.Event"
        out = _raw(raw, type_url)
        assert isinstance(out["reference_hex"], str)
        assert len(out["reference_hex"]) == 16

    def test_resolve_event_proto_returns_none_for_unknown(self):
        """_resolve_event_proto returns None for unknown suffix."""
        from connector import OEM1Connector
        assert OEM1Connector._resolve_event_proto("SomeUnknownSuffix") is None
        assert OEM1Connector._resolve_event_proto("DeepSleepPreclusion") is None
        assert OEM1Connector._resolve_event_proto("") is None

    def test_resolve_event_proto_returns_classes_for_known(self):
        """_resolve_event_proto returns (module, class) for all four dispatched suffixes."""
        from connector import OEM1Connector
        from autonomic.ext.event.event_pb2 import Event, TriggeredEvent, StateTransition, GeofenceEvent

        for suffix, expected_cls in [
            ("Event", Event),
            ("TriggeredEvent", TriggeredEvent),
            ("StateTransition", StateTransition),
            ("GeofenceEvent", GeofenceEvent),
        ]:
            result = OEM1Connector._resolve_event_proto(suffix)
            assert result is not None, f"Expected result for suffix {suffix!r}"
            _, cls = result
            assert cls is expected_cls, f"Expected {expected_cls} for {suffix!r}, got {cls}"


# ---------------------------------------------------------------------------
# Task 1.1: Inner-type dispatch tests
# ---------------------------------------------------------------------------

def _make_state_transition_event() -> bytes:
    """Synthetic COMMAND_PRECLUSION_STATE StateTransition wrapped in outer Event.
    Placeholder vehicle — no real-vehicle data.
    """
    from autonomic.ext.event.event_pb2 import StateTransition, Event
    from google.protobuf import any_pb2

    st = StateTransition()
    wk_fsm = StateTransition.DESCRIPTOR.fields_by_name["wk_fsm_name"].enum_type
    cp_state = StateTransition.DESCRIPTOR.fields_by_name["command_preclusion_from_state"].enum_type
    cp_trigger = StateTransition.DESCRIPTOR.fields_by_name["command_preclusion_trigger"].enum_type

    st.wk_fsm_name = wk_fsm.values_by_name["COMMAND_PRECLUSION_STATE"].number
    st.command_preclusion_from_state = cp_state.values_by_name["COMMANDS_PERMITTED"].number
    st.command_preclusion_to_state = cp_state.values_by_name["COMMANDS_PRECLUDED"].number
    st.command_preclusion_trigger = cp_trigger.values_by_name["NEW_PRECLUSION_INTRODUCED"].number

    outer = Event()
    a = any_pb2.Any()
    a.Pack(st)
    outer.payload.CopyFrom(a)
    return outer.SerializeToString()


def _make_geofence_event() -> bytes:
    """Synthetic minimal GeofenceEvent (ENTER) wrapped in outer Event.
    Placeholder vehicle — no real-vehicle data.
    """
    from autonomic.ext.event.event_pb2 import GeofenceEvent, Event
    from google.protobuf import any_pb2

    ge = GeofenceEvent()
    ge.type = GeofenceEvent.ENTER

    outer = Event()
    a = any_pb2.Any()
    a.Pack(ge)
    outer.payload.CopyFrom(a)
    return outer.SerializeToString()


_EVENT_TYPE_URL = "type.googleapis.com/autonomic.ext.event.Event"


class TestInnerTypeDispatch:
    """New inner-type dispatch: StateTransition and GeofenceEvent decode correctly."""

    def test_state_transition_decodes_wk_fsm_name(self):
        """StateTransition inner type: wkFsmName field present in output JSON."""
        raw = _make_state_transition_event()
        out = _raw(raw, _EVENT_TYPE_URL)

        assert out is not None
        value = out["typedData"]["value"]
        assert "_raw_hex" not in value, f"Expected decoded JSON, got _raw_hex: {value}"
        assert value.get("wkFsmName") == "COMMAND_PRECLUSION_STATE"

    def test_state_transition_fixture_file_decodes(self):
        """state_transition_event.bin fixture decodes with wkFsmName field."""
        raw = _load_fixture("state_transition_event.bin")
        out = _raw(raw, _EVENT_TYPE_URL)

        assert out is not None
        value = out["typedData"]["value"]
        assert "_raw_hex" not in value
        assert value.get("wkFsmName") == "COMMAND_PRECLUSION_STATE"

    def test_geofence_event_decodes_type_field(self):
        """GeofenceEvent inner type: type field present in output JSON."""
        raw = _make_geofence_event()
        out = _raw(raw, _EVENT_TYPE_URL)

        assert out is not None
        value = out["typedData"]["value"]
        assert "_raw_hex" not in value, f"Expected decoded JSON, got _raw_hex: {value}"
        assert value.get("type") == "ENTER"

    def test_geofence_event_fixture_file_decodes(self):
        """geofence_event.bin fixture decodes with type field."""
        raw = _load_fixture("geofence_event.bin")
        out = _raw(raw, _EVENT_TYPE_URL)

        assert out is not None
        value = out["typedData"]["value"]
        assert "_raw_hex" not in value
        assert value.get("type") == "ENTER"

    def test_triggered_event_well_known_label_still_decodes(self):
        """Regression: TriggeredEvent + well_known_label still decodes (motion fixture)."""
        raw = _load_fixture("motion_event.bin")
        out = _raw(raw, _EVENT_TYPE_URL)

        assert out is not None
        value = out["typedData"]["value"]
        assert "_raw_hex" not in value
        assert value.get("wellKnownLabel") == "MOTION_EVENT"

    def test_triggered_event_string_label_decodes_to_json(self):
        """Updated contract (Group 2): TriggeredEvent + string_label decodes to JSON (not _raw_hex).
        Label filtering is the manifest engine's job; connector passes all TriggeredEvents through.
        """
        raw = _load_fixture("string_label_custom_event.bin")
        out = _raw(raw, _EVENT_TYPE_URL)

        assert out is not None
        value = out["typedData"]["value"]
        assert "_raw_hex" not in value, (
            "string_label TriggeredEvent must decode to JSON after Group 2 fix"
        )
        assert "stringLabel" in value

    def test_unknown_inner_suffix_still_raw_hex(self):
        """Regression: Event with an unknown inner payload type_url → _raw_hex."""
        from autonomic.ext.event.event_pb2 import Event
        from google.protobuf import any_pb2

        # Build an outer Event whose payload.type_url has an unknown suffix
        outer = Event()
        a = any_pb2.Any()
        a.type_url = "type.googleapis.com/autonomic.ext.event.SomeNewUnknownInnerType"
        a.value = b"\x08\x01"
        outer.payload.CopyFrom(a)
        raw = outer.SerializeToString()

        out = _raw(raw, _EVENT_TYPE_URL)

        assert out is not None
        assert "_raw_hex" in out["typedData"]["value"]

    def test_inner_dispatch_dict_has_all_three_entries(self):
        """_INNER_PAYLOAD_DISPATCH contains all three required keys."""
        from connector import OEM1Connector
        dispatch = OEM1Connector._INNER_PAYLOAD_DISPATCH
        assert "TriggeredEvent" in dispatch
        assert "StateTransition" in dispatch
        assert "GeofenceEvent" in dispatch
        # All values are (module_path, class_name) tuples
        for key, val in dispatch.items():
            assert isinstance(val, tuple) and len(val) == 2, f"Bad entry for {key}: {val}"
