"""
OEM1 TypedDataDecoder — dispatches on google.protobuf.Any @type URL.

8 accepted types, 3 discard types, unknown → parse-error metric + drop.

TYPE-URL ADAPTER: the real OEM1 server sends vendor namespace URLs
(type.googleapis.com/autonomic.ext.telemetry.signal.Signal). The cleansed
proto bindings use the oem1.ext.* namespace. This file maps BOTH vendor
and cleansed URLs to local types.

*** THIS FILE IS IN scan_exclude BY DESIGN ***
The dispatch dict keys contain vendor type-URL literals because that is
what the production server sends. See decisions.md A2.3 carry-forward
Suggestion #2 for the rationale.
"""
from dataclasses import dataclass

from metrics import emit_parse_error


@dataclass
class DecodeResult:
    type_url: str
    payload: dict
    dropped: bool = False


# Accepted type URL suffixes (both cleansed oem1.ext.* and vendor autonomic.ext.* namespaces).
_ACCEPTED_SUFFIXES = {
    "Metric",
    "ErrorMetric",
    "Event",
    "TriggeredEvent",
    "StateTransition",
    "GeofenceEvent",
    "DeepSleepPreclusion",
    "BatchedTelemetry",
}

# Discard type URL suffixes — silently dropped, no parse-error metric.
_DISCARD_SUFFIXES = {
    "BootstrapSummaryEvent",
    "BindingChangeEvent",
    "DataValidationEvent",
}

# Vendor → local class mapping (type-URL adapter per decisions.md A2.3).
# Keys use the autonomic.* vendor namespace that the real OEM1 server sends.
# Values are the cleansed oem1.* type-URL suffix for local dispatch.
URL_TO_SUFFIX: dict[str, str] = {
    # Cleansed oem1.ext.* namespace (mock server + unit tests)
    "type.googleapis.com/oem1.ext.telemetry.Metric": "Metric",
    "type.googleapis.com/oem1.ext.telemetry.ErrorMetric": "ErrorMetric",
    "type.googleapis.com/oem1.ext.telemetry.Event": "Event",
    "type.googleapis.com/oem1.ext.telemetry.TriggeredEvent": "TriggeredEvent",
    "type.googleapis.com/oem1.ext.telemetry.StateTransition": "StateTransition",
    "type.googleapis.com/oem1.ext.telemetry.GeofenceEvent": "GeofenceEvent",
    "type.googleapis.com/oem1.ext.telemetry.DeepSleepPreclusion": "DeepSleepPreclusion",
    "type.googleapis.com/oem1.ext.telemetry.BatchedTelemetry": "BatchedTelemetry",
    "type.googleapis.com/oem1.ext.telemetry.BootstrapSummaryEvent": "BootstrapSummaryEvent",
    "type.googleapis.com/oem1.ext.telemetry.BindingChangeEvent": "BindingChangeEvent",
    "type.googleapis.com/oem1.ext.telemetry.DataValidationEvent": "DataValidationEvent",
    # Vendor namespace (real OEM1 server) — autonomic.ext.* → cleansed suffix
    "type.googleapis.com/autonomic.ext.telemetry.signal.Signal": "Metric",
    "type.googleapis.com/autonomic.ext.telemetry.signal.ErrorMetric": "ErrorMetric",
    "type.googleapis.com/autonomic.ext.telemetry.event.Event": "Event",
    "type.googleapis.com/autonomic.ext.telemetry.event.TriggeredEvent": "TriggeredEvent",
    "type.googleapis.com/autonomic.ext.telemetry.event.StateTransition": "StateTransition",
    "type.googleapis.com/autonomic.ext.telemetry.event.GeofenceEvent": "GeofenceEvent",
    "type.googleapis.com/autonomic.ext.telemetry.event.DeepSleepPreclusion": "DeepSleepPreclusion",
    "type.googleapis.com/autonomic.ext.telemetry.signal.BatchedTelemetry": "BatchedTelemetry",
    "type.googleapis.com/autonomic.ext.telemetry.event.BootstrapSummaryEvent": "BootstrapSummaryEvent",
    "type.googleapis.com/autonomic.ext.telemetry.event.BindingChangeEvent": "BindingChangeEvent",
    "type.googleapis.com/autonomic.ext.telemetry.event.DataValidationEvent": "DataValidationEvent",
}


class TypedDataDecoder:
    def decode(self, typed_data: dict) -> DecodeResult | None:
        type_url: str = typed_data.get("@type", "")
        suffix = URL_TO_SUFFIX.get(type_url) or _suffix_from_url(type_url)

        if suffix in _DISCARD_SUFFIXES:
            return DecodeResult(type_url=type_url, payload={}, dropped=True)

        if suffix in _ACCEPTED_SUFFIXES:
            payload = self._unmarshal(type_url, typed_data)
            return DecodeResult(type_url=type_url, payload=payload, dropped=False)

        # Unknown type
        self._emit_metric("ParseErrorRate", type_url=type_url)
        return None

    def _unmarshal(self, type_url: str, payload: dict) -> dict:
        """Unmarshal the typed_data payload. Decodes proto bytes into a JSON-friendly dict.

        D2: real Metric proto decode. Other accepted types (Event, TriggeredEvent, etc.)
        currently fall through to a defensive passthrough; add dispatches as needed.
        """
        value = payload.get("value")
        if not isinstance(value, bytes):
            return dict(payload)

        try:
            from google.protobuf.json_format import MessageToDict

            if type_url.endswith(".Metric"):
                from autonomic.ext.telemetry.metric_pb2 import Metric
                msg = Metric()
                msg.ParseFromString(value)
                return MessageToDict(msg, preserving_proto_field_name=True)
            # Future: add Event, TriggeredEvent, etc. dispatches here.
        except Exception as exc:
            return {"_decode_error": f"{type(exc).__name__}: {exc}", "type_url": type_url}

        # Fallback for unhandled accepted types — keep the raw bytes hex-encoded so
        # downstream JSON serialization doesn't fail. Non-Metric Accepted types will
        # land here until their proto dispatch is wired.
        return {"type_url": type_url, "value_hex": value.hex(), "_decode_pending": True}

    def _emit_metric(self, name: str, **kwargs) -> None:
        emit_parse_error()


def _suffix_from_url(type_url: str) -> str | None:
    """Extract the simple class name from a type URL for unknown types."""
    if "/" in type_url:
        return type_url.rsplit("/", 1)[-1].rsplit(".", 1)[-1]
    return None
