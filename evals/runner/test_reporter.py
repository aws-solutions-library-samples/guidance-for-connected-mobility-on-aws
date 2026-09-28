"""Tests for evals/runner/reporter.py.

Covers:
1. Clean run — results match baseline, no regressions → exit 0, 0 regressed in Markdown.
2. Regression A — passing baseline case now failed → exit 1, case listed.
3. Regression B — p99 latency increased >20% → exit 1.
4. --update-baseline — rewrites baseline file, exit 0.
5. Missing baseline — treated as first run, exit 0.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.runner.reporter import detect_regressions, build_markdown, main


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_results(
    tier: int = 1,
    cases: list[dict] | None = None,
    p99: float = 50.0,
    passed: int = 1,
    failed: int = 0,
) -> dict:
    """Create a mock tier results dict for testing the reporter."""
    if cases is None:
        cases = [
            {
                "id": "case-001",
                "yaml_path": "evals/cases/tools/triage/case-001.yaml",
                "outcome": "passed",
                "latency_ms": 42.5,
                "expected_tool_calls": ["triage"],
                "actual_tool_calls": ["triage"],
                "failure_reason": None,
            }
        ]
    return {
        "tier": tier,
        "schema_version": 1,
        "generated_at": "2026-05-25T15:00:00Z",
        "cases": cases,
        "summary": {
            "total": passed + failed,
            "passed": passed,
            "failed": failed,
            "skipped": 0,
            "latency_p50_ms": 25.0,
            "latency_p99_ms": p99,
            "duration_ms": 1234,
        },
    }


def _make_baseline(
    cases: list[dict] | None = None,
    p99: float = 50.0,
) -> dict:
    """Create a mock baseline dict for testing the reporter."""
    if cases is None:
        cases = [
            {
                "id": "case-001",
                "yaml_path": "evals/cases/tools/triage/case-001.yaml",
                "outcome": "passed",
                "latency_ms": 40.0,
                "expected_tool_calls": ["triage"],
                "actual_tool_calls": ["triage"],
                "failure_reason": None,
            }
        ]
    return {
        "tier": 1,
        "schema_version": 1,
        "generated_at": "2026-05-24T15:00:00Z",
        "cases": cases,
        "summary": {
            "total": len(cases),
            "passed": len(cases),
            "failed": 0,
            "skipped": 0,
            "latency_p50_ms": 20.0,
            "latency_p99_ms": p99,
            "duration_ms": 1000,
        },
    }


# ---------------------------------------------------------------------------
# Test 1: Clean run — no regressions
# ---------------------------------------------------------------------------

def test_clean_run_no_regressions(tmp_path: Path) -> None:
    """Results match baseline exactly → exit 0, Markdown shows 0 regressed."""
    results = _make_results()
    baseline = _make_baseline()

    results_file = tmp_path / "results.json"
    baseline_file = tmp_path / "baseline.json"
    output_file = tmp_path / "report.md"

    results_file.write_text(json.dumps(results))
    baseline_file.write_text(json.dumps(baseline))

    exit_code = main([
        "--tier", "1",
        "--results", str(results_file),
        "--baseline", str(baseline_file),
        "--output", str(output_file),
    ])

    assert exit_code == 0, "Expected exit 0 for clean run"
    md = output_file.read_text()
    assert "## Eval Report" in md
    # Table row should show 0 regressed
    assert "| 1    | 1    | 1     | 0      | 0         |" in md


# ---------------------------------------------------------------------------
# Test 2: Regression A — passing case now failed
# ---------------------------------------------------------------------------

def test_regression_a_exit_1(tmp_path: Path) -> None:
    """Baseline has case-001 passed; results has it failed → exit 1, case listed."""
    baseline = _make_baseline()
    results = _make_results(
        cases=[
            {
                "id": "case-001",
                "yaml_path": "evals/cases/tools/triage/case-001.yaml",
                "outcome": "failed",
                "latency_ms": 42.5,
                "expected_tool_calls": ["triage"],
                "actual_tool_calls": [],
                "failure_reason": "tool not called",
            }
        ],
        passed=0,
        failed=1,
    )

    results_file = tmp_path / "results.json"
    baseline_file = tmp_path / "baseline.json"
    output_file = tmp_path / "report.md"

    results_file.write_text(json.dumps(results))
    baseline_file.write_text(json.dumps(baseline))

    exit_code = main([
        "--tier", "1",
        "--results", str(results_file),
        "--baseline", str(baseline_file),
        "--output", str(output_file),
    ])

    assert exit_code == 1, "Expected exit 1 for Regression A"
    md = output_file.read_text()
    assert "case-001" in md
    assert "Type A" in md


# ---------------------------------------------------------------------------
# Test 3: Regression B — p99 latency increased >20%
# ---------------------------------------------------------------------------

def test_regression_b_exit_1(tmp_path: Path) -> None:
    """Baseline p99=100ms, results p99=130ms (>20%) → exit 1."""
    baseline = _make_baseline(p99=100.0)
    results = _make_results(p99=130.0)

    results_file = tmp_path / "results.json"
    baseline_file = tmp_path / "baseline.json"
    output_file = tmp_path / "report.md"

    results_file.write_text(json.dumps(results))
    baseline_file.write_text(json.dumps(baseline))

    exit_code = main([
        "--tier", "1",
        "--results", str(results_file),
        "--baseline", str(baseline_file),
        "--output", str(output_file),
    ])

    assert exit_code == 1, "Expected exit 1 for Regression B"
    md = output_file.read_text()
    assert "Type B" in md


# ---------------------------------------------------------------------------
# Test 4: --update-baseline rewrites baseline file, exit 0
# ---------------------------------------------------------------------------

def test_update_baseline(tmp_path: Path) -> None:
    """--update-baseline copies results over baseline and exits 0."""
    results = _make_results(p99=200.0)
    baseline = _make_baseline(p99=100.0)

    results_file = tmp_path / "results.json"
    baseline_file = tmp_path / "baseline.json"

    results_file.write_text(json.dumps(results))
    baseline_file.write_text(json.dumps(baseline))

    exit_code = main([
        "--tier", "1",
        "--results", str(results_file),
        "--baseline", str(baseline_file),
        "--update-baseline",
    ])

    assert exit_code == 0, "Expected exit 0 after --update-baseline"
    updated = json.loads(baseline_file.read_text())
    assert updated["summary"]["latency_p99_ms"] == 200.0, (
        "Baseline should be overwritten with results content"
    )


# ---------------------------------------------------------------------------
# Test 5: Missing baseline — treated as first run, exit 0
# ---------------------------------------------------------------------------

def test_missing_baseline_first_run(tmp_path: Path) -> None:
    """Non-existent --baseline path → first run, no regressions, exit 0."""
    results = _make_results()
    results_file = tmp_path / "results.json"
    results_file.write_text(json.dumps(results))

    nonexistent_baseline = str(tmp_path / "does_not_exist.json")

    exit_code = main([
        "--tier", "1",
        "--results", str(results_file),
        "--baseline", nonexistent_baseline,
    ])

    assert exit_code == 0, "Expected exit 0 when baseline is missing (first run)"


# ---------------------------------------------------------------------------
# Additional unit tests for detect_regressions
# ---------------------------------------------------------------------------

def test_flaky_case_suppresses_regression_a() -> None:
    """Baseline case with flaky=true suppresses Regression A."""
    baseline = _make_baseline(
        cases=[{
            "id": "flaky-001",
            "outcome": "passed",
            "flaky": True,
            "expected_tool_calls": ["triage"],
            "actual_tool_calls": ["triage"],
        }]
    )
    results = _make_results(
        cases=[{
            "id": "flaky-001",
            "outcome": "failed",
            "expected_tool_calls": ["triage"],
            "actual_tool_calls": [],
            "failure_reason": "intermittent",
        }],
        passed=0,
        failed=1,
    )
    reg = detect_regressions(results, baseline)
    assert "flaky-001" not in reg["type_a"], "Flaky case should not trigger Regression A"


def test_regression_b_exactly_20_percent_is_not_regression() -> None:
    """Exactly 20% increase is NOT a regression (must be strictly >20%)."""
    baseline = _make_baseline(p99=100.0)
    results = _make_results(p99=120.0)  # exactly 20%
    reg = detect_regressions(results, baseline)
    assert not reg["type_b"], "Exactly 20% increase should not be a regression"


def test_regression_c_tool_sequence_diverged() -> None:
    """Passed case with diverged tool sequence triggers Regression C."""
    baseline = _make_baseline(
        cases=[{
            "id": "case-001",
            "outcome": "passed",
            "expected_tool_calls": ["triage"],
            "actual_tool_calls": ["triage"],
        }]
    )
    results = _make_results(
        cases=[{
            "id": "case-001",
            "outcome": "passed",
            "latency_ms": 42.5,
            "expected_tool_calls": ["triage"],
            "actual_tool_calls": ["book"],  # diverged
            "failure_reason": None,
        }]
    )
    reg = detect_regressions(results, baseline)
    assert "case-001" in reg["type_c"], "Diverged tool sequence should trigger Regression C"
    assert reg["has_regression"]
