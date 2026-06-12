"""
Test skeletons for typed_data_decoder.py (RED phase — B1.1).

Encodes behaviors from spec § Design § Architecture:
- Dispatches on @type URL
- 8 accepted types: Metric, ErrorMetric, Event, TriggeredEvent, StateTransition,
  GeofenceEvent, DeepSleepPreclusion, BatchedTelemetry
- 3 discard types skipped: BootstrapSummaryEvent, BindingChangeEvent, DataValidationEvent
- Unknown @type → parse-error metric + drop

Tests import TypedDataDecoder inside each test body; pytest collects them all
but every test FAILS (ImportError) until typed_data_decoder.py lands in B1.2.
"""
import sys
from pathlib import Path

import pytest

_OEM1_DIR = Path(__file__).parent.parent
if str(_OEM1_DIR) not in sys.path:
    sys.path.insert(0, str(_OEM1_DIR))

# Base URL for @type dispatch — uses cleansed oem1.ext namespace (see decisions.md A2.3 pivot)
_TYPE_BASE = "type.googleapis.com/oem1.ext.telemetry."

ACCEPTED_TYPES = [
    "Metric",
    "ErrorMetric",
    "Event",
    "TriggeredEvent",
    "StateTransition",
    "GeofenceEvent",
    "DeepSleepPreclusion",
    "BatchedTelemetry",
]

DISCARD_TYPES = [
    "BootstrapSummaryEvent",
    "BindingChangeEvent",
    "DataValidationEvent",
]


def _make_any(type_suffix: str) -> dict:
    """Build a minimal typed_data dict with a given @type URL."""
    return {"@type": f"{_TYPE_BASE}{type_suffix}", "value": "dGVzdA=="}


# ---------------------------------------------------------------------------
# Accepted types: decode and return a result
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("type_suffix", ACCEPTED_TYPES)
def test_accepted_type_returns_decode_result(type_suffix, monkeypatch):
    """Each of the 8 accepted @type values must produce a DecodeResult (not None/dropped)."""
    from typed_data_decoder import TypedDataDecoder, DecodeResult

    monkeypatch.setattr(
        TypedDataDecoder,
        "_unmarshal",
        lambda _self, type_url, payload: {"type": type_suffix, "decoded": True},
    )

    decoder = TypedDataDecoder()
    result = decoder.decode(_make_any(type_suffix))

    assert result is not None, f"@type {type_suffix} should produce a DecodeResult, got None"
    assert isinstance(result, DecodeResult), f"Expected DecodeResult, got {type(result)}"
    assert result.dropped is False, f"@type {type_suffix} must NOT be dropped"


# ---------------------------------------------------------------------------
# Discard types: silently skipped (no error, no parse-error metric)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("type_suffix", DISCARD_TYPES)
def test_discard_type_is_dropped_without_parse_error(type_suffix, monkeypatch):
    """Each of the 3 discard @type values must be silently dropped (no parse-error metric)."""
    from typed_data_decoder import TypedDataDecoder, DecodeResult

    metrics_emitted = []
    monkeypatch.setattr(
        TypedDataDecoder,
        "_emit_metric",
        lambda _self, name, **kwargs: metrics_emitted.append(name),
    )

    decoder = TypedDataDecoder()
    result = decoder.decode(_make_any(type_suffix))

    assert result is None or (isinstance(result, DecodeResult) and result.dropped is True), (
        f"@type {type_suffix} should be discarded"
    )
    assert not any("parse-error" in m for m in metrics_emitted), (
        f"@type {type_suffix} must NOT emit a parse-error metric (it is a known discard type)"
    )


# ---------------------------------------------------------------------------
# Unknown @type: parse-error metric + drop
# ---------------------------------------------------------------------------

def test_unknown_type_emits_parse_error_metric(monkeypatch):
    """An unknown @type URL must emit a parse-error metric."""
    from typed_data_decoder import TypedDataDecoder

    metrics_emitted = []
    monkeypatch.setattr(
        TypedDataDecoder,
        "_emit_metric",
        lambda _self, name, **kwargs: metrics_emitted.append(name),
    )

    decoder = TypedDataDecoder()
    result = decoder.decode({"@type": f"{_TYPE_BASE}UnknownFutureType", "value": "dGVzdA=="})

    assert any("parse" in m.lower() for m in metrics_emitted), (
        "Unknown @type must emit a parse-error metric (ParseErrorRate)"
    )


def test_unknown_type_drops_message(monkeypatch):
    """An unknown @type URL results in the message being dropped."""
    from typed_data_decoder import TypedDataDecoder, DecodeResult

    monkeypatch.setattr(TypedDataDecoder, "_emit_metric", lambda _self, name, **kwargs: None)

    decoder = TypedDataDecoder()
    result = decoder.decode({"@type": f"{_TYPE_BASE}SomeBrandNewType", "value": "dGVzdA=="})

    assert result is None or (isinstance(result, DecodeResult) and result.dropped is True), (
        "Unknown @type must drop the message"
    )


# ---------------------------------------------------------------------------
# Dispatch is on @type URL field
# ---------------------------------------------------------------------------

def test_dispatch_on_type_url_field(monkeypatch):
    """Decoder reads the @type field to select the decode path."""
    from typed_data_decoder import TypedDataDecoder

    dispatched = []
    monkeypatch.setattr(
        TypedDataDecoder,
        "_unmarshal",
        lambda _self, type_url, payload: dispatched.append(type_url) or {"decoded": True},
    )

    decoder = TypedDataDecoder()
    decoder.decode(_make_any("Metric"))

    assert any("Metric" in url for url in dispatched), (
        "Decoder must dispatch on the @type URL value"
    )
