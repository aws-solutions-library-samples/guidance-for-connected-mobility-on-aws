"""Reporter and regression detector for the eval pipeline.

Consumes per-tier JSON results (emitted by tier1_tools, tier2_conversation,
tier3_e2e runners) and compares them against a committed baseline to detect
regressions.

CLI usage::

    python3 -m evals.runner.reporter \\
        --tier {1|2|3} \\
        --results <path> \\
        --baseline <path> \\
        [--output <path>] \\
        [--update-baseline] \\
        [--flaky-window 7]

Exit codes:
    0 — no regressions detected (or --update-baseline succeeded)
    1 — one or more regressions detected (Type A, B, or C)
    2 — argument or file error

Regression definitions
----------------------
A. Any case that was ``passed`` in baseline is now ``failed``.
   Suppressed for cases where the baseline marks ``flaky: true``.
B. Tier-level p99 latency increased by >20% vs baseline.
C. Any *passed* case where ``actual_tool_calls`` diverges from
   ``expected_tool_calls``.

--flaky-window N
    Accepted but flakiness analysis is forward-work (requires a history
    store). For this version, Regression A is suppressed only for cases
    that the *baseline* already marks as ``flaky: true``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json(path: str) -> dict[str, Any]:
    """Load and return a JSON file, or return an empty baseline on missing file."""
    p = Path(path)
    if not p.exists():
        return {}
    with p.open() as fh:
        return json.load(fh)


def _cases_by_id(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index cases list by case id."""
    return {c["id"]: c for c in data.get("cases", [])}


def _fmt_duration(ms: float) -> str:
    """Format milliseconds as human-readable duration."""
    if ms < 1000:
        return f"{ms:.0f}ms"
    s = ms / 1000
    if s < 60:
        return f"{s:.1f}s"
    m = int(s // 60)
    s_rem = s - m * 60
    return f"{m}m {s_rem:.0f}s"


# ---------------------------------------------------------------------------
# Regression detection
# ---------------------------------------------------------------------------


def detect_regressions(
    results: dict[str, Any],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    """Compare results against baseline and return regression report.

    Args:
        results: Latest run JSON (the agreed contract schema).
        baseline: Committed reference JSON. May be empty (first run).

    Returns:
        Dict with keys:
            ``type_a``: list of case ids — passed in baseline, now failed.
            ``type_b``: bool — p99 latency increased >20%.
            ``type_b_detail``: dict with baseline_p99, results_p99.
            ``type_c``: list of case ids — tool sequence diverged (passed cases only).
            ``has_regression``: bool — any regression detected.
    """
    baseline_cases = _cases_by_id(baseline)
    results_cases = _cases_by_id(results)

    # --- Regression A: passed → failed ---
    type_a: list[str] = []
    for case_id, base_case in baseline_cases.items():
        if base_case.get("outcome") != "passed":
            continue
        if base_case.get("flaky"):
            continue  # suppressed for flaky cases
        result_case = results_cases.get(case_id)
        if result_case and result_case.get("outcome") == "failed":
            type_a.append(case_id)

    # --- Regression B: p99 latency increased >20% ---
    base_p99 = (baseline.get("summary") or {}).get("latency_p99_ms", 0.0)
    res_p99 = (results.get("summary") or {}).get("latency_p99_ms", 0.0)
    type_b = bool(base_p99 and res_p99 > base_p99 * 1.20)
    type_b_detail = {"baseline_p99_ms": base_p99, "results_p99_ms": res_p99}

    # --- Regression C: tool sequence diverged (passed cases only) ---
    type_c: list[str] = []
    for case_id, res_case in results_cases.items():
        if res_case.get("outcome") != "passed":
            continue
        base_case = baseline_cases.get(case_id)
        if not base_case:
            continue
        expected = res_case.get("expected_tool_calls") or []
        actual = res_case.get("actual_tool_calls") or []
        if expected != actual:
            type_c.append(case_id)

    has_regression = bool(type_a or type_b or type_c)
    return {
        "type_a": type_a,
        "type_b": type_b,
        "type_b_detail": type_b_detail,
        "type_c": type_c,
        "has_regression": has_regression,
    }


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------


def _tier_status_line(tier: int, summary: dict[str, Any]) -> str:
    """Render the bold tier status line, e.g. '**Tier 1:** ✅ 47/47 passed (1.2s)'."""
    total = summary.get("total", 0)
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    duration_ms = summary.get("duration_ms", 0)
    icon = "✅" if failed == 0 else "⚠️ "
    return f"**Tier {tier}:** {icon} {passed}/{total} passed ({_fmt_duration(duration_ms)})"


def build_markdown(
    tier: int,
    results: dict[str, Any],
    regressions: dict[str, Any],
) -> str:
    """Build the Markdown eval report.

    Format matches the spec.md Design section exactly so that 5b's Makefile
    targets and CI can parse it predictably.

    Args:
        tier: Tier number (1, 2, or 3).
        results: Latest run JSON.
        regressions: Output of detect_regressions().

    Returns:
        Markdown string.
    """
    summary = results.get("summary") or {}
    total = summary.get("total", 0)
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    skipped = summary.get("skipped", 0)

    type_a = regressions["type_a"]
    type_b = regressions["type_b"]
    type_c = regressions["type_c"]
    regressed_count = len(type_a) + (1 if type_b else 0) + len(type_c)

    lines: list[str] = ["## Eval Report", ""]
    lines.append(_tier_status_line(tier, summary))
    lines.append("")

    # Summary table
    lines.append("| Tier | Total | Passed | Failed | Regressed |")
    lines.append("|------|-------|--------|--------|-----------|")
    lines.append(f"| {tier}    | {total}    | {passed}     | {failed}      | {regressed_count}         |")
    lines.append("")

    # Failed / regressed cases section
    failed_cases = [c for c in results.get("cases", []) if c.get("outcome") == "failed"]
    if failed_cases or type_b or type_c:
        lines.append("### Failed cases")
        for c in failed_cases:
            reason = c.get("failure_reason") or "unknown"
            lines.append(f"- {c['id']}: {reason}")
        if type_b:
            b = regressions["type_b_detail"]
            lines.append(
                f"- tier{tier}/latency: p99 {b['results_p99_ms']:.0f}ms "
                f"(baseline {b['baseline_p99_ms']:.0f}ms, >20% increase)"
            )
        for case_id in type_c:
            lines.append(f"- {case_id}: tool sequence diverged from baseline")
        lines.append("")

    # Regression summary
    if regressions["has_regression"]:
        lines.append("### Regressions detected")
        if type_a:
            lines.append(f"- **Type A** (passed → failed): {', '.join(type_a)}")
        if type_b:
            b = regressions["type_b_detail"]
            lines.append(
                f"- **Type B** (p99 latency): "
                f"{b['results_p99_ms']:.0f}ms vs baseline {b['baseline_p99_ms']:.0f}ms"
            )
        if type_c:
            lines.append(f"- **Type C** (tool sequence): {', '.join(type_c)}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the reporter."""
    parser = argparse.ArgumentParser(
        description="Eval reporter and regression detector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--tier", type=int, choices=[1, 2, 3], required=True)
    parser.add_argument("--results", required=True, help="Path to latest run JSON")
    parser.add_argument("--baseline", required=True, help="Path to committed baseline JSON")
    parser.add_argument("--output", help="Write Markdown to this path (default: stdout)")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Copy --results over --baseline and exit 0",
    )
    parser.add_argument(
        "--flaky-window",
        type=int,
        default=7,
        metavar="N",
        help=(
            "Rolling window (days) for flakiness analysis. "
            "NOTE: full flakiness tracking is forward-work. "
            "Currently, Regression A is suppressed only for cases "
            "where the baseline marks flaky=true."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns exit code."""
    try:
        args = _parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code != 0 else 0

    # --update-baseline: copy results → baseline, exit 0
    if args.update_baseline:
        results_path = Path(args.results)
        if not results_path.exists():
            print(f"ERROR: --results file not found: {args.results}", file=sys.stderr)
            return 2
        shutil.copy2(str(results_path), args.baseline)
        return 0

    # Load results (required)
    results_path = Path(args.results)
    if not results_path.exists():
        print(f"ERROR: --results file not found: {args.results}", file=sys.stderr)
        return 2
    try:
        results = _load_json(args.results)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: Cannot read --results: {exc}", file=sys.stderr)
        return 2

    # Load baseline (missing = first run, treat as empty)
    try:
        baseline = _load_json(args.baseline)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: Cannot read --baseline: {exc}", file=sys.stderr)
        return 2

    # Detect regressions
    regressions = detect_regressions(results, baseline)

    # Build and emit Markdown
    md = build_markdown(args.tier, results, regressions)
    if args.output:
        Path(args.output).write_text(md)
    else:
        print(md, end="")

    return 1 if regressions["has_regression"] else 0


if __name__ == "__main__":
    sys.exit(main())
