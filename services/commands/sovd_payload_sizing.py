"""
sovd_payload_sizing.py — Response-size utilities for SOVD diagnostics (Task 1.2).

Provides:
  - encoded_size_bytes(payload)   : byte length of the payload as UTF-8 JSON (wire size).
  - SIZE_THRESHOLD_BYTES          : 80 KB inline threshold; above this the sidecar uploads
                                    to S3 and publishes a summary MQTT response instead.
  - make_synthetic_response(...)  : factory for SOVD-shaped response payloads used in sizing
                                    tests and Group 3 / Group 5 test helpers.

Sizing rationale (spec § Design § 5):
  Full-scan worst case: 9 ECUs × 10 DTCs × 12 signals × ~90 bytes ≈ 95 KB decoded JSON.
  AWS IoT MQTT publish soft limit is 128 KB; QoS 1 envelope overhead trims usable payload
  to ~120 KB.  80 KB gives ≈ 40 KB of headroom for the envelope + retry budget.

CRITICAL: encoded_size_bytes uses json.dumps + .encode('utf-8') — NOT sys.getsizeof().
sys.getsizeof() measures the Python object graph (CPython overhead, pointer sizes, etc.)
and bears no relation to the wire size.  Only the JSON-serialised UTF-8 byte count
accurately predicts what the MQTT broker and S3 will receive.
"""
from __future__ import annotations

import json

# ---------------------------------------------------------------------------
# Public constant
# ---------------------------------------------------------------------------

SIZE_THRESHOLD_BYTES: int = 80 * 1024  # 81 920 bytes


# ---------------------------------------------------------------------------
# Core sizing function
# ---------------------------------------------------------------------------

def encoded_size_bytes(payload: dict) -> int:
    """Return the byte length of *payload* when serialised as compact UTF-8 JSON.

    This is the **wire size** — the number of bytes that would appear on the
    MQTT bus or in the S3 object.  Separators ``(',', ':')`` produce the most
    compact JSON (no extra whitespace), which is also what the sidecar emits.

    Args:
        payload: Any JSON-serialisable dict.

    Returns:
        Integer byte count.
    """
    return len(json.dumps(payload, separators=(',', ':')).encode('utf-8'))


# ---------------------------------------------------------------------------
# Synthetic response factory
# ---------------------------------------------------------------------------

def make_synthetic_response(
    ecu_count: int,
    dtcs_per_ecu: int,
    signals_per_ff: int,
) -> dict:
    """Build an SOVD-shaped response payload for sizing analysis.

    Produces a structurally valid payload matching the response shape defined
    in spec § Design § 1 (ISO 17978-3 style).  Signal values and DTC codes are
    synthetic — suitable for testing only.

    Args:
        ecu_count:       Number of ECU entries in ``components``.
        dtcs_per_ecu:    Number of DTC records inside each ECU entry.
        signals_per_ff:  Number of freeze-frame signal entries per DTC.

    Returns:
        Dict matching the SOVD response schema::

            {
              "correlation_id": "...",
              "status": "SUCCEEDED",
              "components": {
                "ECU_0": {
                  "id": "ECU_0",
                  "protocol": "ISO 15765-4",
                  "dtcs": [
                    {
                      "code": "P0100",
                      "status": "confirmed",
                      "occurrence_count": 1,
                      "first_seen_ms": 1735000000000,
                      "last_seen_ms": 1735000000000,
                      "freeze_frame": {
                        "signal_0": {
                          "value": 0,
                          "unit": "rpm",
                          "timestamp": "2026-01-24T00:00:00Z"
                        },
                        ...
                      }
                    },
                    ...
                  ]
                },
                ...
              },
              "latency_ms": 1000,
              "storage_uri": null
            }
    """
    components: dict = {}

    for ecu_idx in range(ecu_count):
        ecu_id = f"ECU_{ecu_idx}"
        dtcs = []

        for dtc_idx in range(dtcs_per_ecu):
            # Synthetic DTC code — P + 4 hex digits, padded to ensure uniqueness per ECU.
            dtc_code = f"P{(ecu_idx * 100 + dtc_idx):04d}"

            # Build freeze-frame signals.
            freeze_frame: dict = {}
            for sig_idx in range(signals_per_ff):
                signal_name = f"signal_{sig_idx}"
                freeze_frame[signal_name] = {
                    "value": sig_idx * 10,
                    "unit": "rpm",
                    "timestamp": "2026-01-24T00:00:00Z",
                }

            dtcs.append({
                "code": dtc_code,
                "status": "confirmed",
                "occurrence_count": 1,
                "first_seen_ms": 1735000000000,
                "last_seen_ms": 1735000000000,
                "freeze_frame": freeze_frame,
            })

        components[ecu_id] = {
            "id": ecu_id,
            "protocol": "ISO 15765-4",
            "dtcs": dtcs,
        }

    return {
        "correlation_id": "00000000-0000-0000-0000-000000000000",
        "status": "SUCCEEDED",
        "components": components,
        "latency_ms": 1000,
        "storage_uri": None,
    }
