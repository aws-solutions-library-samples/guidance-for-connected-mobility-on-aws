"""Wrapper that runs a tier's pytest suite and emits a JSON report.

The JSON schema is shared with `evals/runner/reporter.py` and
`evals/baselines/*.json`. Schema:

    {
      "tier": int,
      "schema_version": 1,
      "generated_at": ISO-8601 string,
      "cases": [{"id", "yaml_path", "outcome", "latency_ms",
                 "expected_tool_calls", "actual_tool_calls", "failure_reason"}, ...],
      "summary": {"total", "passed", "failed", "skipped",
                  "latency_p50_ms", "latency_p99_ms", "duration_ms"}
    }

CMS scope: only Tier 3 (REST + WebSocket integration tests) is supported in
Spec 1. Tier 1/2 are deferred to Spec 2 (CMS observability + broader tests).

Usage:
    python3 -m evals.runner._run_tier --tier 3 --output /tmp/eval-tier3.json
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

TIER_TO_RUNNER = {
    3: "evals/runner/tier3_e2e.py",
}

TIER_TO_CASES_GLOB: dict[int, str | list[str]] = {
    3: "evals/cases/e2e/**/*.yaml",
}


def collect_case_files(tier: int) -> list[str]:
    """Discover and return all case YAML files for a given tier."""
    pat = TIER_TO_CASES_GLOB[tier]
    files: list[str] = []
    if isinstance(pat, str):
        files = glob.glob(pat, recursive=True)
    else:
        for p in pat:
            files.extend(glob.glob(p, recursive=True))
    return sorted(files)


def parse_pytest_output(stdout: str) -> list[dict]:
    """LEGACY parser kept for fallback when EVAL_RESULTS_FILE was not produced.

    Matches lines like:
        evals/runner/tier3_e2e.py::test_e2e_case[case-id] PASSED [  5%]

    The case_id extracted here is whatever pytest's parametrize emits — usually
    the absolute case_path — so it is NOT portable across machines / CI
    checkouts. Prefer the JSONL records emitted by tier3_e2e.py via
    EVAL_RESULTS_FILE; this function is only invoked as a degraded fallback.
    """
    cases: list[dict] = []
    for line in stdout.splitlines():
        for outcome in ("PASSED", "FAILED", "SKIPPED"):
            marker = f" {outcome}"
            if marker not in line or "::" not in line:
                continue
            pre, _, _ = line.partition(marker)
            test_id = pre.split("::", 1)[1] if "::" in pre else pre
            test_id = test_id.strip()
            if "[" in test_id and test_id.endswith("]"):
                case_id = test_id[test_id.index("[") + 1 : -1]
            else:
                case_id = test_id
            cases.append(
                {
                    "id": case_id,
                    "yaml_path": "",
                    "outcome": outcome.lower(),
                    "latency_ms": 0.0,
                    "expected_tool_calls": [],
                    "actual_tool_calls": [],
                    "failure_reason": None if outcome == "PASSED" else "see pytest log",
                }
            )
            break
    return cases


def read_jsonl_results(path: str) -> list[dict]:
    """Read per-case results from the JSONL side-channel produced by tier3_e2e.

    Each line is a single JSON object matching the schema documented at the
    top of this module. Malformed lines are skipped (defensive); cases is
    deduplicated by (id, yaml_path) keeping the LAST emission, since
    tier3_e2e emits at the first matching assertion failure.
    """
    if not Path(path).exists():
        return []
    cases: dict[tuple[str, str], dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = (rec.get("id", ""), rec.get("yaml_path", ""))
            cases[key] = rec
    return list(cases.values())


def _percentile(values: list[float], pct: float) -> float:
    """Compute a simple percentile (0-100) without numpy.

    Returns 0.0 for empty input. Uses linear interpolation.
    """
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    rank = (pct / 100.0) * (len(s) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(s) - 1)
    frac = rank - lower
    return float(s[lower] * (1 - frac) + s[upper] * frac)


def main() -> int:
    """Orchestrate tier-specific test runner (pytest wrapper)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", type=int, choices=[1, 2, 3], required=True)
    parser.add_argument("--output", required=True, help="Path to write JSON report")
    args = parser.parse_args()

    if args.tier in (1, 2):
        print(
            "Tier 1/2 not yet supported in CMS; deferred to Spec 2 "
            "(CMS observability + broader tests).",
            file=sys.stderr,
        )
        return 1

    runner_path = TIER_TO_RUNNER[args.tier]
    if not Path(runner_path).exists():
        print(f"Runner not found: {runner_path}", file=sys.stderr)
        return 2

    # Side-channel JSONL file the runner appends to per-case. Lives in /tmp
    # so it doesn't leak into the working tree if the run is interrupted.
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w",
        prefix=f"eval-tier{args.tier}-",
        suffix=".jsonl",
        delete=False,
    ) as tmp:
        results_file = tmp.name

    start = time.perf_counter()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            runner_path,
            "-v",
            "--tb=short",
            "--no-header",
            "--color=no",  # Disable ANSI codes so the parser can match PASSED/FAILED/SKIPPED literally.
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "NO_COLOR": "1", "EVAL_RESULTS_FILE": results_file},
    )
    duration_ms = (time.perf_counter() - start) * 1000.0

    # Prefer the JSONL side-channel; fall back to stdout parsing if the file
    # is empty (e.g., pytest collected zero matching cases or skipped them all).
    cases = read_jsonl_results(results_file)
    if not cases:
        cases = parse_pytest_output(proc.stdout)

    # Clean up the temp results file — its contents are now in `cases`.
    try:
        Path(results_file).unlink()
    except OSError:
        pass

    latencies = [c["latency_ms"] for c in cases if c["outcome"] == "passed" and c.get("latency_ms")]
    summary = {
        "total": len(cases),
        "passed": sum(1 for c in cases if c["outcome"] == "passed"),
        "failed": sum(1 for c in cases if c["outcome"] == "failed"),
        "skipped": sum(1 for c in cases if c["outcome"] == "skipped"),
        "latency_p50_ms": _percentile(latencies, 50),
        "latency_p99_ms": _percentile(latencies, 99),
        "duration_ms": duration_ms,
    }

    report = {
        "tier": args.tier,
        "schema_version": 1,
        "generated_at": datetime.datetime.now(datetime.UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "cases": cases,
        "summary": summary,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))

    print(
        f"Tier {args.tier}: {summary['passed']}/{summary['total']} passed, "
        f"{summary['failed']} failed, {summary['skipped']} skipped, "
        f"{duration_ms:.0f}ms",
        file=sys.stderr,
    )

    if proc.returncode in (0, 5):
        return 0
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
