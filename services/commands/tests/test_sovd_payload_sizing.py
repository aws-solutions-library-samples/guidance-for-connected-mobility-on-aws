"""
test_sovd_payload_sizing.py — Unit tests for sovd_payload_sizing helpers (Task 1.2).

Three tests, all real (no skeletons):
  1. test_encoded_size_matches_utf8_len  — verifies encoded_size_bytes returns the same
     count as manually computing len(json.dumps(...).encode('utf-8')).
  2. test_threshold_is_80kb             — verifies SIZE_THRESHOLD_BYTES == 80 * 1024 == 81920.
  3. test_synthetic_full_scan_worst_case_exceeds_threshold — builds the worst-case payload
     (9 ECUs × 10 DTCs × 12 signals) and asserts it exceeds SIZE_THRESHOLD_BYTES.
"""
import json

import pytest

# conftest.py already inserts services/commands onto sys.path.
from sovd_payload_sizing import (
    SIZE_THRESHOLD_BYTES,
    encoded_size_bytes,
    make_synthetic_response,
)


class TestEncodedSizeMatchesUtf8Len:
    """encoded_size_bytes must return the UTF-8 byte count of compact JSON — not
    sys.getsizeof() or any other measure of Python object size."""

    def test_encoded_size_matches_utf8_len(self) -> None:
        """For an arbitrary dict, encoded_size_bytes == len(json.dumps(payload, separators=(',',':')).encode('utf-8'))."""
        payload = {
            "correlation_id": "abc-123",
            "status": "SUCCEEDED",
            "components": {
                "ECU_ENGINE": {
                    "id": "ECU_ENGINE",
                    "protocol": "ISO 15765-4",
                    "dtcs": [
                        {
                            "code": "P0420",
                            "status": "confirmed",
                            "occurrence_count": 3,
                            "first_seen_ms": 1734000000000,
                            "last_seen_ms": 1735000000000,
                            "freeze_frame": {
                                "engineRpm": {"value": 2800, "unit": "rpm", "timestamp": "2026-08-31T00:00:00Z"},
                                "coolantTemp": {"value": 88, "unit": "degC", "timestamp": "2026-08-31T00:00:00Z"},
                            },
                        }
                    ],
                }
            },
            "latency_ms": 3400,
            "storage_uri": None,
        }

        expected = len(json.dumps(payload, separators=(',', ':')).encode('utf-8'))
        assert encoded_size_bytes(payload) == expected

    def test_encoded_size_small_dict(self) -> None:
        """Minimal sanity: encoded_size_bytes of a one-key dict equals its wire representation."""
        payload = {"k": "v"}
        expected = len(json.dumps(payload, separators=(',', ':')).encode('utf-8'))
        assert encoded_size_bytes(payload) == expected
        # '{"k":"v"}' with compact separators = 9 bytes (curly braces, quotes, colon, values)
        assert encoded_size_bytes(payload) == 9

    def test_encoded_size_empty_dict(self) -> None:
        """Empty dict encodes to '{}' — 2 bytes."""
        assert encoded_size_bytes({}) == 2

    def test_encoded_size_unicode_is_utf8(self) -> None:
        """Unicode characters must be counted in their UTF-8 encoded form, not as code points."""
        payload = {"key": "café"}  # 'é' is 2 bytes in UTF-8
        raw = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        assert encoded_size_bytes(payload) == len(raw)


class TestThresholdIs80Kb:
    """SIZE_THRESHOLD_BYTES must equal exactly 80 * 1024 = 81 920."""

    def test_threshold_is_80kb(self) -> None:
        assert SIZE_THRESHOLD_BYTES == 80 * 1024
        assert SIZE_THRESHOLD_BYTES == 81_920


class TestSyntheticFullScanWorstCaseExceedsThreshold:
    """A 9-ECU × 10-DTC × 12-signal payload must exceed SIZE_THRESHOLD_BYTES.

    Spec § Design § 5 analysis:
        9 ECUs × 10 DTCs/ECU × 12 signals/freeze-frame × ~90 bytes/signal ≈ 95 KB
    This test makes that empirical rather than asserted by prose.
    """

    def test_synthetic_full_scan_worst_case_exceeds_threshold(self) -> None:
        """9 ECUs × 10 DTCs × 12 signals must produce a payload > 80 KB (81 920 bytes)."""
        payload = make_synthetic_response(ecu_count=9, dtcs_per_ecu=10, signals_per_ff=12)
        size = encoded_size_bytes(payload)
        assert size > SIZE_THRESHOLD_BYTES, (
            f"Expected worst-case payload ({size} bytes) to exceed SIZE_THRESHOLD_BYTES "
            f"({SIZE_THRESHOLD_BYTES} bytes). The 80 KB fallback path would never fire."
        )

    def test_synthetic_single_ecu_single_dtc_below_threshold(self) -> None:
        """A minimal payload (1 ECU × 1 DTC × 6 signals) must be well below the threshold."""
        payload = make_synthetic_response(ecu_count=1, dtcs_per_ecu=1, signals_per_ff=6)
        size = encoded_size_bytes(payload)
        assert size < SIZE_THRESHOLD_BYTES, (
            f"Expected minimal payload ({size} bytes) to be below SIZE_THRESHOLD_BYTES "
            f"({SIZE_THRESHOLD_BYTES} bytes)."
        )

    def test_make_synthetic_response_shape(self) -> None:
        """make_synthetic_response produces a structurally valid SOVD response shape."""
        payload = make_synthetic_response(ecu_count=2, dtcs_per_ecu=3, signals_per_ff=4)

        # Top-level keys present.
        assert "correlation_id" in payload
        assert "status" in payload
        assert "components" in payload
        assert "latency_ms" in payload
        assert "storage_uri" in payload

        assert payload["status"] == "SUCCEEDED"
        assert payload["storage_uri"] is None

        # Correct number of components.
        assert len(payload["components"]) == 2

        # Each component has the right structure.
        for ecu_id, ecu_data in payload["components"].items():
            assert "id" in ecu_data
            assert "protocol" in ecu_data
            assert "dtcs" in ecu_data
            assert len(ecu_data["dtcs"]) == 3

            for dtc in ecu_data["dtcs"]:
                assert "code" in dtc
                assert "status" in dtc
                assert "occurrence_count" in dtc
                assert "first_seen_ms" in dtc
                assert "last_seen_ms" in dtc
                assert "freeze_frame" in dtc
                assert len(dtc["freeze_frame"]) == 4
