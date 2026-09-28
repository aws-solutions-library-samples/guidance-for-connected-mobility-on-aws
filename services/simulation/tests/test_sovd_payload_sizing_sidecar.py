# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Sidecar sizing-helper invariant tests.
#
# Per decisions.md "Group 3 sizing helper": the sidecar carries a local copy
# of the sizing helper rather than importing from services/commands/.  These
# tests enforce that:
#   1. The local constant has the correct value (81 920 bytes).
#   2. encoded_size_bytes({'a': 1}) equals 7 (known-good UTF-8 wire size).
#   3. The sidecar module produces the same result as the commands sibling for
#      a representative multi-ECU fixture — so any future divergence between
#      the two copies is caught immediately.
#
# Run: python3 -m pytest services/simulation/tests/test_sovd_payload_sizing_sidecar.py -v

import json
import sys
import os

# Allow import from services/ root when run from repo root or the test dir.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from services.simulation.sovd_payload_sizing import (  # noqa: E402
    SIZE_THRESHOLD_BYTES as SIDECAR_THRESHOLD,
    encoded_size_bytes as sidecar_encoded_size,
)
from services.commands.sovd_payload_sizing import (  # noqa: E402
    SIZE_THRESHOLD_BYTES as COMMANDS_THRESHOLD,
    encoded_size_bytes as commands_encoded_size,
    make_synthetic_response,
)


def test_sidecar_threshold_is_81920():
    """The sidecar's SIZE_THRESHOLD_BYTES must exactly equal 80 * 1024 = 81 920 bytes.

    Both the cloud Lambda and the sidecar use this constant to decide whether
    to upload to S3.  An off-by-one divergence between the two copies would
    result in the sidecar inlining a payload the Lambda would reject (or vice
    versa), breaking the response path in a way that is hard to reproduce.
    """
    assert SIDECAR_THRESHOLD == 81920, (
        f"SIZE_THRESHOLD_BYTES must be 81920; got {SIDECAR_THRESHOLD}"
    )


def test_sidecar_encoded_size_trivial_dict():
    """encoded_size_bytes({'a': 1}) must return 7.

    The compact JSON of {'a': 1} with separators=(',', ':') is the 7-character
    string '{"a":1}'.  This pins the implementation against sys.getsizeof()-
    style off-by-one or whitespace errors that would produce a different count.
    """
    payload = {"a": 1}
    size = sidecar_encoded_size(payload)
    # Verify the expected value independently so the test is self-documenting.
    expected = len(json.dumps(payload, separators=(',', ':')).encode('utf-8'))
    assert expected == 7, f"Test fixture assumption broken: expected 7, got {expected}"
    assert size == 7, (
        f"encoded_size_bytes({{'a': 1}}) must return 7; got {size}"
    )


def test_sidecar_and_commands_produce_identical_results_for_representative_fixture():
    """The sidecar and commands modules must produce the same byte count for
    a representative multi-ECU fixture.

    Uses make_synthetic_response (from the commands module — the sidecar does
    not need its own factory) to build a 3-ECU / 5-DTC / 4-signal payload and
    compares both modules' encoded_size_bytes output.  A mismatch here means
    the two copies have diverged (e.g. different separator settings or a
    stray .encode('ascii') change) and would route differently at the 80 KB
    threshold.
    """
    fixture = make_synthetic_response(
        ecu_count=3,
        dtcs_per_ecu=5,
        signals_per_ff=4,
    )
    sidecar_size = sidecar_encoded_size(fixture)
    commands_size = commands_encoded_size(fixture)
    assert sidecar_size == commands_size, (
        f"Sidecar and commands sizing helpers diverged for the same fixture: "
        f"sidecar={sidecar_size}, commands={commands_size}.  "
        f"Both copies of sovd_payload_sizing.py MUST produce identical results."
    )
    # Sanity: the fixture should be well under the threshold (it's a small payload).
    assert sidecar_size < SIDECAR_THRESHOLD, (
        f"Test fixture unexpectedly large: {sidecar_size} >= {SIDECAR_THRESHOLD}.  "
        f"Reduce ecu_count/dtcs_per_ecu/signals_per_ff."
    )
    # Also confirm constants match — belt-and-braces.
    assert SIDECAR_THRESHOLD == COMMANDS_THRESHOLD, (
        f"SIZE_THRESHOLD_BYTES differs between modules: "
        f"sidecar={SIDECAR_THRESHOLD}, commands={COMMANDS_THRESHOLD}"
    )
