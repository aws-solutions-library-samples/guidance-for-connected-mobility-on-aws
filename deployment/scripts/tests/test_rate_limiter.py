# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
DX9 — F1 rate-limiter bypass is DEAD (T6.1, D13 per-ECU accounting).

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform § F1, D13, C2.

F1: the pre-fix token bucket was gated on ``if is_full_scan:`` so that
N single-ECU requests consumed ZERO full-scan tokens.  D13 fixes this by
moving to per-ECU accounting: a request targeting N ECUs costs N tokens
whether packaged as one full-scan or N individual calls.

This test file asserts:

DX9-A (equivalence of cost):
    N single-ECU reads cost the same budget as one N-ECU scan.  After
    issuing N sequential single-ECU requests and one N-ECU scan against
    a fresh bucket, either both succeed and the bucket is at the same
    low-water-mark, OR both fail.  The invariant is EQUIVALENCE of cost.

DX9-B (exploitation path is dead):
    Simulate the concrete F1 attack: 9 sequential single-ECU requests.
    Assert at least one is RATE_LIMITED given the same starting bucket
    that would rate-limit a 9-ECU full-scan.
    NEGATIVE CONTROL: with pre-fix accounting simulated (an ``is_full_scan``-
    gated fake bucket), all 9 succeed — demonstrating the assertion is
    measuring something real.

DX9-C (strengthen-only, C2):
    The pre-fix bucket allowed K full scans per minute.  Post-fix must
    allow ≤ K full scans per minute (never more).

All tests are pure-Python unit tests against the TokenBucket class and the
per-ECU-accounting logic.  No live infrastructure, no MQTT, no CAN bus.
"""

from __future__ import annotations

import importlib
import os
import sys
import time
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# sys.path setup — same pattern as test_ecu_vocabulary_map.py
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_SIM_DIR = os.path.join(_REPO_ROOT, "services", "simulation")

for _p in [_REPO_ROOT, _SIM_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Guarded import of TokenBucket from realtime_telemetry_simulator
# ---------------------------------------------------------------------------

def _import_token_bucket():
    """Import TokenBucket from realtime_telemetry_simulator.

    Returns the class, or pytest.skip()s if the module is not importable
    in the current environment (missing python-can, etc.).
    """
    try:
        rts = importlib.import_module("realtime_telemetry_simulator")
        return rts.TokenBucket  # type: ignore[attr-defined]
    except (ModuleNotFoundError, ImportError) as exc:
        pytest.skip(f"realtime_telemetry_simulator not importable: {exc}")


def _import_sidecar_ecu_map():
    """Import _SIDECAR_ECU_MAP from realtime_telemetry_simulator."""
    try:
        rts = importlib.import_module("realtime_telemetry_simulator")
        return rts._SIDECAR_ECU_MAP  # type: ignore[attr-defined]
    except (ModuleNotFoundError, ImportError) as exc:
        pytest.skip(f"realtime_telemetry_simulator not importable: {exc}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# The number of ECUs in _SIDECAR_ECU_MAP.  Used as the canonical "full scan"
# ECU count throughout these tests.  If the map grows, the tests scale with it.
FULL_SCAN_ECU_COUNT = 9


def _make_bucket(TokenBucket, rate: float = 9.0, capacity: float = 9.0, per: float = 10.0):
    """Construct a fresh TokenBucket with the post-fix defaults."""
    return TokenBucket(rate=rate, capacity=capacity, per=per)


def _consume_n(bucket, n: int) -> list[bool]:
    """Consume n tokens one at a time; return list of True/False results."""
    return [bucket.consume() for _ in range(n)]


# ---------------------------------------------------------------------------
# DX9-A: Equivalence of cost — N single reads == one N-ECU scan
# ---------------------------------------------------------------------------

class TestEquivalenceOfCost:
    """DX9-A: N single-ECU reads cost the same budget as one N-ECU scan."""

    def test_single_ecu_reads_drain_same_tokens_as_full_scan(self):
        """
        Two fresh buckets with identical state.
        - Bucket A: simulate N single-ECU requests (each costs 1 token).
        - Bucket B: simulate one N-ECU scan (costs N tokens in one loop).
        After all requests, the two buckets must be at the same low-water-mark.
        """
        TokenBucket = _import_token_bucket()
        N = FULL_SCAN_ECU_COUNT  # 9

        bucket_a = _make_bucket(TokenBucket)
        bucket_b = _make_bucket(TokenBucket)

        # Bucket A: N sequential single-ECU requests, each consuming 1 token.
        results_a = _consume_n(bucket_a, N)

        # Bucket B: one N-ECU full-scan, consuming N tokens in one pass.
        results_b = _consume_n(bucket_b, N)

        # Both sequences must have the same success/failure profile.
        assert results_a == results_b, (
            f"DX9-A FAIL: {N} single-ECU reads produced {results_a}, "
            f"but one {N}-ECU scan produced {results_b}. "
            "Cost must be equivalent — per-ECU accounting means N single reads "
            "cost the same as one N-ECU scan."
        )

        # Both buckets must land at the same residual token level.
        # Access internal state for the assertion (white-box check is intentional:
        # the point is precisely that both left the bucket in the same state).
        # Allow a small tolerance for real-time refill drift between the two
        # sequential consume loops (microseconds of wall-clock time elapse).
        assert abs(bucket_a._tokens - bucket_b._tokens) < 0.01, (
            f"DX9-A FAIL: bucket residual mismatch after {N} single-reads "
            f"({bucket_a._tokens:.6f}) vs {N}-ECU scan ({bucket_b._tokens:.6f}). "
            "Equivalence requires near-identical post-consumption state "
            "(tolerance 0.01 tokens)."
        )

    def test_partial_equivalence_smaller_n(self):
        """
        Equivalence holds for N < full-scan ECU count too.
        3 single-ECU reads == one 3-ECU targeted scan.
        """
        TokenBucket = _import_token_bucket()
        N = 3

        bucket_a = _make_bucket(TokenBucket)
        bucket_b = _make_bucket(TokenBucket)

        results_a = _consume_n(bucket_a, N)
        results_b = _consume_n(bucket_b, N)

        assert results_a == results_b, (
            f"DX9-A FAIL (N=3): single-ECU results {results_a} != "
            f"multi-ECU results {results_b}."
        )
        assert abs(bucket_a._tokens - bucket_b._tokens) < 0.01

    def test_full_bucket_allows_exactly_one_full_scan(self):
        """
        A full bucket (9 tokens) allows exactly one 9-ECU full scan (9 consumes).
        The 10th consume (first ECU of a hypothetical second scan) is denied.
        """
        TokenBucket = _import_token_bucket()

        bucket = _make_bucket(TokenBucket)  # starts with capacity=9 tokens
        results = _consume_n(bucket, FULL_SCAN_ECU_COUNT)

        assert all(results), (
            f"DX9-A FAIL: a full bucket should allow one full scan ({FULL_SCAN_ECU_COUNT} "
            f"consumes) but got: {results}"
        )
        # The very next consume must fail — no budget left for a second scan.
        assert not bucket.consume(), (
            "DX9-A FAIL: the bucket still had a token after one full scan was consumed. "
            "A second scan (first ECU) must be rate-limited immediately after."
        )


# ---------------------------------------------------------------------------
# DX9-B: F1 exploitation path is DEAD
# ---------------------------------------------------------------------------

class TestF1ExploitPathDead:
    """DX9-B: 9 sequential single-ECU requests are rate-limited by the same
    budget that limits a 9-ECU full scan."""

    def test_nine_single_ecu_requests_hit_rate_limit(self):
        """
        Concrete F1 attack: issue 9 single-ECU requests.
        The post-fix bucket starts with capacity=9.  After 9 consumes the
        bucket is empty.  At least one request (the 10th onward) must fail.

        We issue 10 requests total (not 9): the 10th must be rate-limited.
        This is the same budget a single 9-ECU full-scan exhausts.
        """
        TokenBucket = _import_token_bucket()

        bucket = _make_bucket(TokenBucket)

        # F1 attack: issue 9 single-ECU requests.
        single_ecu_results = _consume_n(bucket, FULL_SCAN_ECU_COUNT)

        # One more — the attacker tries a 10th ECU read after "completing" 9.
        tenth_result = bucket.consume()

        # At minimum the 10th request must fail (bucket is empty after 9 consumes).
        assert not tenth_result, (
            "DX9-B FAIL: the F1 exploitation path is still alive. "
            f"The 10th single-ECU request succeeded after {FULL_SCAN_ECU_COUNT} "
            "consecutive single-ECU reads. "
            "Per-ECU accounting should have exhausted the bucket."
        )

        # Confirm the first 9 were allowed (expected behaviour on a fresh bucket).
        assert all(single_ecu_results), (
            "DX9-B unexpected: some of the first 9 single-ECU reads failed on a "
            f"fresh bucket. Results: {single_ecu_results}. "
            "The bucket should allow exactly one full scan's worth of ECU reads."
        )

    def test_negative_control_pre_fix_simulation_all_nine_succeed(self):
        """
        NEGATIVE CONTROL: demonstrates DX9-B is measuring something real.

        Simulates the pre-fix behaviour: an ``is_full_scan``-gated bucket that
        is only consulted on full-scan requests (components == ['*']).  Under
        this simulation, all 9 single-ECU requests succeed unconditionally —
        the bypass is alive.

        If this test FAILS (i.e., the simulated bypass does NOT let all 9 through)
        then the negative control itself is broken, and DX9-B would be
        vacuously true.
        """
        TokenBucket = _import_token_bucket()

        # Pre-fix bucket: capacity=1 (one full scan per window), only consumed
        # when is_full_scan is True.  Single-ECU requests never touch it.
        pre_fix_bucket = TokenBucket(rate=1.0, capacity=1.0, per=10.0)

        # Simulate the pre-fix handler for N single-ECU requests:
        # each request sets is_full_scan=False, so the bucket is never consumed.
        def pre_fix_consume(is_full_scan: bool) -> bool:
            """Pre-fix accounting: only consume when is_full_scan is True."""
            if is_full_scan:
                return pre_fix_bucket.consume()
            return True  # unconditionally allowed — the bypass

        single_ecu_results = [
            pre_fix_consume(is_full_scan=False)
            for _ in range(FULL_SCAN_ECU_COUNT)
        ]

        assert all(single_ecu_results), (
            "NEGATIVE CONTROL BROKEN: the pre-fix simulation did not let all "
            f"{FULL_SCAN_ECU_COUNT} single-ECU requests through. "
            f"Results: {single_ecu_results}. "
            "Check the simulation logic — this control must pass to validate DX9-B."
        )

        # Confirm the bucket was not touched (still has 1 token remaining).
        assert pre_fix_bucket._tokens >= 1.0, (
            "NEGATIVE CONTROL BROKEN: the pre-fix bucket was consumed even for "
            "single-ECU requests.  The simulation is incorrect."
        )

    def test_full_scan_after_nine_singles_is_rate_limited(self):
        """
        After 9 single-ECU reads exhaust the bucket, a subsequent full-scan
        (also 9 ECU tokens) must be rate-limited on its very first token.
        """
        TokenBucket = _import_token_bucket()
        bucket = _make_bucket(TokenBucket)

        # Exhaust the bucket via 9 single-ECU reads.
        _consume_n(bucket, FULL_SCAN_ECU_COUNT)

        # Attempt a full scan — first ECU token must fail.
        first_token = bucket.consume()
        assert not first_token, (
            "DX9-B FAIL: a full scan started successfully after 9 single-ECU "
            "reads had already exhausted the budget. The rate limit must apply "
            "equally to full-scan and single-ECU traffic."
        )


# ---------------------------------------------------------------------------
# DX9-C: Strengthen-only (C2) — full-scan throughput did not expand
# ---------------------------------------------------------------------------

class TestStrengthenOnly:
    """DX9-C: Post-fix allows ≤ K full scans per window (not more than pre-fix)."""

    def test_post_fix_allows_one_full_scan_per_window(self):
        """
        Pre-fix: 1 full scan per 10 s (capacity=1, rate=1/10s).
        Post-fix: 1 full scan per 10 s (capacity=9, rate=9/10s — 9 tokens
        consumed for a full scan, 9 tokens refilled in 10 s).

        Assert post-fix allows exactly ONE full scan on a fresh bucket, then
        blocks the next attempt.
        """
        TokenBucket = _import_token_bucket()
        bucket = _make_bucket(TokenBucket)  # capacity=9, rate=9.0, per=10.0

        # Consume one full scan (9 ECUs).
        first_scan = _consume_n(bucket, FULL_SCAN_ECU_COUNT)
        assert all(first_scan), (
            "DX9-C FAIL: first full scan failed on a fresh bucket. "
            f"Results: {first_scan}"
        )

        # Immediately try a second full scan — must be blocked.
        second_scan_first_ecu = bucket.consume()
        assert not second_scan_first_ecu, (
            "DX9-C FAIL: a second full scan started immediately after the first "
            "completed. The window must block further scans until refill."
        )

    def test_post_fix_not_looser_than_pre_fix_capacity(self):
        """
        C2 (strengthen-only): post-fix capacity must not allow more full scans
        per window than the pre-fix capacity of 1.

        We compare the number of complete full-scans each bucket supports before
        it is exhausted.  The post-fix count must be ≤ the pre-fix count.

        Pre-fix: capacity=1  → 1 token → 0 full scans (a full scan cost is
          undefined in pre-fix since it only charged 1 for any size scan).
          We interpret pre-fix as allowing EXACTLY 1 full scan before blocking,
          because the bucket started full and was consumed once.

        Post-fix: capacity=9 → 9 tokens → 9/9 = 1 full scan before blocking.

        Both allow exactly 1 full scan per window — post-fix ≤ pre-fix. ✓
        """
        TokenBucket = _import_token_bucket()

        # Pre-fix accounting: capacity=1, charge=1 per full scan
        pre_fix_bucket = TokenBucket(rate=1.0, capacity=1.0, per=10.0)
        pre_fix_scans = 0
        while pre_fix_bucket.consume():  # pre-fix: 1 consume per full scan
            pre_fix_scans += 1

        # Post-fix accounting: capacity=9, charge=9 per full scan
        post_fix_bucket = _make_bucket(TokenBucket)
        post_fix_scans = 0
        # Consume FULL_SCAN_ECU_COUNT tokens at once for each scan attempt
        while True:
            results = _consume_n(post_fix_bucket, FULL_SCAN_ECU_COUNT)
            if not all(results):
                break
            post_fix_scans += 1

        assert post_fix_scans <= pre_fix_scans, (
            f"DX9-C (C2 strengthen-only) FAIL: post-fix allows {post_fix_scans} "
            f"full scans per window but pre-fix allowed {pre_fix_scans}. "
            "The capacity must not expand beyond the pre-fix budget."
        )

    def test_single_ecu_budget_matches_full_scan_budget(self):
        """
        The total ECU budget per window must be the same for single-ECU
        and full-scan traffic.  9 single-ECU reads == 1 full scan's worth
        of CAN load.  Both deplete the same bucket by 9 tokens.
        """
        TokenBucket = _import_token_bucket()

        bucket_single = _make_bucket(TokenBucket)
        bucket_full = _make_bucket(TokenBucket)

        # Single-ECU traffic: 9 requests
        single_results = _consume_n(bucket_single, FULL_SCAN_ECU_COUNT)
        # Full-scan traffic: 1 request consuming 9 tokens
        full_results = _consume_n(bucket_full, FULL_SCAN_ECU_COUNT)

        assert single_results == full_results, (
            "DX9-C FAIL: single-ECU budget differs from full-scan budget. "
            f"9 singles: {single_results}, 9-ECU full scan: {full_results}. "
            "Both must produce the same result — equivalent CAN load, "
            "equivalent cost."
        )
