"""
sovd_payload_sizing.py — Response-size utilities for SOVD diagnostics (sidecar copy).

SIDECAR-LOCAL MODULE — copied verbatim from services/commands/sovd_payload_sizing.py.

This module is intentionally duplicated rather than cross-imported. The sim
image build does not reach into services/commands/ for source; coupling the two
services via a shared Python module would tie the sim image build to every
commands/ change and risk silent degradation if the copy path ever drifted.

The sizing threshold is anchored in spec § D4 (80 KB inline limit). Both files
are downstream of the same spec paragraph. Any future edit that changes
SIZE_THRESHOLD_BYTES MUST update BOTH this file AND
services/commands/sovd_payload_sizing.py in the same commit.

Executable invariant: services/simulation/tests/test_sovd_payload_sizing_sidecar.py
asserts that the local constant equals 81 920 and that encoded_size_bytes()
produces the same result as the commands sibling for a representative fixture.

See: services/commands/sovd_payload_sizing.py (sibling, cloud-side copy)
See: .kiro/specs/2026-09-01-cms-remote-diagnostics-sovd/decisions.md
     "Group 3 sizing helper: duplicate the constant sidecar-side, don't cross-import"
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
