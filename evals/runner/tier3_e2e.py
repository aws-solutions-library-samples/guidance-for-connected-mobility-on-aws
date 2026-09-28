"""Tier 3 end-to-end eval runner — CMS REST + WebSocket endpoints.

Tests CMS API endpoints and WebSocket streams against a deployed staging environment.
Auto-skips when STAGE_ENDPOINT env var is unset (safe for PR CI).

Path traversal guard (non-negotiable):
  REST paths must match: ^/api/v[0-9]+/[A-Za-z0-9_/.-]*$
  WebSocket paths must match: ^/ws/[A-Za-z0-9_/.-]*$
  Paths with '..' or absolute URLs are rejected.

CLI usage::

    # Dry-run (no real connection):
    STAGE_ENDPOINT=https://example.invalid python3 -m evals.runner.tier3_e2e --dry-run

    # Run a specific case:
    STAGE_ENDPOINT=<url> python3 -m evals.runner.tier3_e2e --case evals/cases/e2e/foo.yaml

    # Write JSON trace:
    STAGE_ENDPOINT=<url> python3 -m evals.runner.tier3_e2e --case evals/cases/e2e/foo.yaml --output /tmp/trace.json
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from evals.runner.schema import EvalCase

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REST_LATENCY_BUDGET_MS = 5_000
WS_LATENCY_BUDGET_MS = 12_000

# Path traversal allowlists
_REST_PATH_RE = re.compile(r"^/api/v[0-9]+/[A-Za-z0-9_/.-]*$")
_WS_PATH_RE = re.compile(r"^/ws/[A-Za-z0-9_/.-]*$")

# ---------------------------------------------------------------------------
# Built-in sample case — used when no --case given or file missing
# ---------------------------------------------------------------------------

_SAMPLE_CASE: dict[str, Any] = {
    "id": "health-check-001",
    "description": "Built-in sample case for --dry-run self-test",
    "tier": 3,
    "persona": "fleet-operator",
    "input": {
        "type": "rest",
        "method": "GET",
        "path": "/api/v1/health",
    },
    "expected": {
        "status_code": 200,
        "tool_calls": [],
        "response": {},
    },
    "latency_budget_ms": REST_LATENCY_BUDGET_MS,
}

# ---------------------------------------------------------------------------
# Path traversal guard
# ---------------------------------------------------------------------------


def _validate_path(path: str, input_type: str) -> None:
    """Validate a REST or WebSocket path against the allowlist.

    Rejects paths containing '..', absolute URLs, or anything outside the
    allowlist regex.

    Args:
        path: The raw path string from the eval case.
        input_type: 'rest' or 'websocket'.

    Raises:
        ValueError: If the path fails validation.
    """
    if ".." in path:
        raise ValueError(f"Path traversal rejected: '..' in {path!r}")
    if re.match(r"^https?://", path):
        raise ValueError(f"Path traversal rejected: absolute URL {path!r}")

    if input_type == "rest":
        if not _REST_PATH_RE.match(path):
            raise ValueError(
                f"Path traversal rejected for {path!r}: "
                f"REST paths must match ^/api/v[0-9]+/[A-Za-z0-9_/.-]*$"
            )
    elif input_type == "websocket":
        if not _WS_PATH_RE.match(path):
            raise ValueError(
                f"Path traversal rejected for {path!r}: "
                f"WebSocket paths must match ^/ws/[A-Za-z0-9_/.-]*$"
            )


# ---------------------------------------------------------------------------
# REST runner
# ---------------------------------------------------------------------------


def _run_rest(case: EvalCase, stage_endpoint: str) -> dict[str, Any]:
    """Execute a REST eval case against the staging endpoint.

    Args:
        case: Validated EvalCase with type == 'rest'.
        stage_endpoint: Base URL (e.g. https://abc.execute-api.us-west-2.amazonaws.com/prod).

    Returns:
        Result dict with keys: passed, assertions, elapsed_ms.
    """
    import requests

    inp = case.input
    path = inp.path or ""

    # Load case fixtures (placeholder defaults like fleet_id, vehicle_id) for substitution.
    # Cases can override per-case via input.path_params or input.query_params explicit values.
    fixtures: dict[str, Any] = {}
    fixtures_path = "evals/cases/e2e/_fixtures.yaml"
    if os.path.exists(fixtures_path):
        try:
            with open(fixtures_path) as fh:
                fixtures = yaml.safe_load(fh) or {}
        except Exception:  # noqa: BLE001
            fixtures = {}

    def _substitute(value: Any) -> Any:
        """Replace {placeholder} tokens in a string value with case path_params or fixtures.

        Recurses into dicts/lists. Non-string values pass through unchanged.
        Resolution order: per-case path_params first, then _fixtures.yaml defaults.
        """
        if isinstance(value, str):
            for key, val in (inp.path_params or {}).items():
                value = value.replace(f"{{{key}}}", str(val))
            for key, val in fixtures.items():
                value = value.replace(f"{{{key}}}", str(val))
            return value
        if isinstance(value, dict):
            return {k: _substitute(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_substitute(v) for v in value]
        return value

    # Substitute path params + fixture defaults into path
    path = _substitute(path)

    url = stage_endpoint.rstrip("/") + path

    # Substitute placeholders in query_params and body too (covers Cycle 2 Warning 2)
    substituted_query = _substitute(inp.query_params) if inp.query_params else None
    substituted_body = _substitute(inp.body) if inp.body else None

    # Per-case auth decision: anonymous personas (e.g. unauthenticated 401 cases)
    # MUST NOT send a JWT; all other personas attach the JWT from CMS_JWT if set.
    # Without this guard, an authenticated case suite would inject Authorization
    # into the 401 case and turn the expected 401 into a 200.
    persona = (case.persona or "").lower()
    jwt = os.environ.get("CMS_JWT") if persona != "anonymous" else None
    headers: dict[str, str] = {}
    if jwt:
        headers["Authorization"] = f"Bearer {jwt}"

    method = (inp.method or "GET").upper()
    budget_ms = case.latency_budget_ms or REST_LATENCY_BUDGET_MS

    t0 = time.monotonic()
    resp = requests.request(
        method,
        url,
        params=substituted_query,
        json=substituted_body if method in ("POST", "PUT", "PATCH") else None,
        headers=headers,
        timeout=budget_ms / 1000.0 + 5,
    )
    elapsed_ms = (time.monotonic() - t0) * 1000

    assertions: dict[str, Any] = {}
    passed = True

    # Status code
    expected_status = case.expected.status_code
    assertions["status_code"] = {
        "expected": expected_status,
        "actual": resp.status_code,
        "ok": expected_status is None or resp.status_code == expected_status,
    }
    if expected_status is not None and resp.status_code != expected_status:
        passed = False

    # Response keys
    must_contain_keys: list[str] = []
    if case.expected.response:
        must_contain_keys = case.expected.response.get("must_contain_keys", [])
    if must_contain_keys:
        try:
            body = resp.json()
        except Exception:
            body = {}
        missing = [k for k in must_contain_keys if k not in body]
        assertions["must_contain_keys"] = {"required": must_contain_keys, "missing": missing, "ok": not missing}
        if missing:
            passed = False

    # Latency
    assertions["elapsed_ms"] = {"value": elapsed_ms, "budget": budget_ms, "ok": elapsed_ms <= budget_ms}
    if elapsed_ms > budget_ms:
        passed = False

    return {"passed": passed, "assertions": assertions, "elapsed_ms": elapsed_ms}


# ---------------------------------------------------------------------------
# WebSocket runner
# ---------------------------------------------------------------------------


def _run_websocket(case: EvalCase, stage_endpoint_wss: str) -> dict[str, Any]:
    """Execute a WebSocket eval case against the staging endpoint.

    Args:
        case: Validated EvalCase with type == 'websocket'.
        stage_endpoint_wss: WebSocket base URL (wss://...).

    Returns:
        Result dict with keys: passed, assertions, elapsed_ms, events.
    """
    from evals.runner import ws_client

    inp = case.input
    path = inp.path or ""
    url = stage_endpoint_wss.rstrip("/") + path if path else stage_endpoint_wss

    jwt = os.environ.get("CMS_JWT")
    budget_ms = case.latency_budget_ms or WS_LATENCY_BUDGET_MS
    duration_ms = inp.duration_ms or budget_ms

    t0 = time.monotonic()
    events = asyncio.run(
        ws_client.connect_and_subscribe(
            url=url,
            subscribe_msg=inp.subscribe or {},
            duration_ms=duration_ms,
            jwt=jwt,
        )
    )
    elapsed_ms = (time.monotonic() - t0) * 1000

    assertions: dict[str, Any] = {}
    passed = True

    # Event count
    exp_events = case.expected.events or {}
    min_count = exp_events.get("min_count", 0)
    assertions["event_count"] = {"min": min_count, "actual": len(events), "ok": len(events) >= min_count}
    if len(events) < min_count:
        passed = False

    # Event types
    must_contain_types: list[str] = exp_events.get("must_contain_one_of_types", [])
    if must_contain_types:
        actual_types = {e.get("type") for e in events}
        found = any(t in actual_types for t in must_contain_types)
        assertions["event_types"] = {
            "must_contain_one_of": must_contain_types,
            "actual_types": list(actual_types),
            "ok": found,
        }
        if not found:
            passed = False

    # Latency
    assertions["elapsed_ms"] = {"value": elapsed_ms, "budget": budget_ms, "ok": elapsed_ms <= budget_ms}
    if elapsed_ms > budget_ms:
        passed = False

    return {"passed": passed, "assertions": assertions, "elapsed_ms": elapsed_ms, "events": events}


# ---------------------------------------------------------------------------
# pytest test
# ---------------------------------------------------------------------------


def _discover_cases() -> list[str]:
    pattern = str(Path(__file__).parent.parent / "cases" / "e2e" / "**" / "*.yaml")
    return [p for p in glob.glob(pattern, recursive=True) if not Path(p).name.startswith("_")]


@pytest.mark.parametrize("case_path", _discover_cases())
def test_e2e_case(case_path: str) -> None:
    """Tier 3 end-to-end eval test.

    Auto-skips when STAGE_ENDPOINT env var is unset (PR CI safe).

    When ``EVAL_RESULTS_FILE`` is set (the orchestrator path used by
    ``_run_tier``), a JSON record is appended for each case with the
    logical ``id`` from the YAML, a relative ``yaml_path``, the
    outcome, the measured ``latency_ms``, and the failure reason if
    any. This is the source of truth for the ``evals/baselines/*.json``
    contract — pytest stdout parsing is intentionally avoided so that
    case identifiers are portable across machines / CI checkouts.
    """
    if not os.environ.get("STAGE_ENDPOINT"):
        pytest.skip("STAGE_ENDPOINT env var not set — skipping Tier 3 test")

    with open(case_path) as f:
        raw = yaml.safe_load(f)
    case = EvalCase.model_validate(raw)

    # Resolve a stable, repo-relative yaml_path for the baseline. Falls back
    # to the absolute path if the case file is somehow outside the repo root
    # (defensive — should never happen with _discover_cases()).
    repo_root = Path(__file__).resolve().parent.parent.parent
    try:
        rel_yaml_path = str(Path(case_path).resolve().relative_to(repo_root))
    except ValueError:
        rel_yaml_path = case_path

    results_file = os.environ.get("EVAL_RESULTS_FILE")

    def _emit(outcome: str, latency_ms: float, failure_reason: str | None) -> None:
        """Append one JSON record to EVAL_RESULTS_FILE if set."""
        if not results_file:
            return
        record = {
            "id": case.id,
            "yaml_path": rel_yaml_path,
            "outcome": outcome,
            "latency_ms": float(latency_ms),
            "expected_tool_calls": list(case.expected.tool_calls or []),
            "actual_tool_calls": [],
            "failure_reason": failure_reason,
        }
        try:
            with open(results_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError:
            # Don't let result-file IO problems mask a real test failure.
            pass

    if case.tier != 3:
        _emit("skipped", 0.0, f"case tier={case.tier}, not 3")
        pytest.skip(f"Case tier={case.tier}, not 3 — skipping")

    # Path traversal guard
    inp = case.input
    if inp.type == "rest" and inp.path:
        try:
            _validate_path(inp.path, "rest")
        except ValueError as exc:
            pytest.fail(f"Path traversal rejected for {inp.path!r}: {exc}")
    elif inp.type == "websocket" and inp.path:
        try:
            _validate_path(inp.path, "websocket")
        except ValueError as exc:
            pytest.fail(f"Path traversal rejected for {inp.path!r}: {exc}")

    stage_endpoint = os.environ["STAGE_ENDPOINT"]

    failure_reason: str | None = None
    elapsed_ms: float = 0.0
    try:
        if inp.type == "rest":
            result = _run_rest(case, stage_endpoint)
        elif inp.type == "websocket":
            stage_wss = os.environ.get(
                "STAGE_ENDPOINT_WSS",
                stage_endpoint.replace("https://", "wss://"),
            )
            result = _run_websocket(case, stage_wss)
        else:
            _emit("skipped", 0.0, f"unsupported input type {inp.type!r}")
            pytest.skip(f"Unsupported input type {inp.type!r} for Tier 3")
            return

        elapsed_ms = float(result.get("elapsed_ms", 0.0))

        # Run assertions; capture the FIRST failure for the baseline record
        # but still raise so pytest sees the failure.
        if not result["assertions"].get("status_code", {}).get("ok", True):
            failure_reason = (
                f"Status code mismatch: expected "
                f"{result['assertions']['status_code']['expected']}, "
                f"got {result['assertions']['status_code']['actual']}"
            )
            _emit("failed", elapsed_ms, failure_reason)
            raise AssertionError(failure_reason)
        if not result["assertions"].get("must_contain_keys", {}).get("ok", True):
            failure_reason = (
                f"Response missing keys: "
                f"{result['assertions']['must_contain_keys']['missing']}"
            )
            _emit("failed", elapsed_ms, failure_reason)
            raise AssertionError(failure_reason)
        if not result["assertions"].get("event_count", {}).get("ok", True):
            failure_reason = (
                f"Event count {result['assertions']['event_count']['actual']} < "
                f"min {result['assertions']['event_count']['min']}"
            )
            _emit("failed", elapsed_ms, failure_reason)
            raise AssertionError(failure_reason)
        if not result["assertions"].get("event_types", {}).get("ok", True):
            failure_reason = (
                f"No event of required types: "
                f"{result['assertions']['event_types']['must_contain_one_of']}"
            )
            _emit("failed", elapsed_ms, failure_reason)
            raise AssertionError(failure_reason)
        if not result["assertions"]["elapsed_ms"]["ok"]:
            failure_reason = (
                f"Latency {result['assertions']['elapsed_ms']['value']:.0f}ms "
                f"> budget {result['assertions']['elapsed_ms']['budget']}ms"
            )
            _emit("failed", elapsed_ms, failure_reason)
            raise AssertionError(failure_reason)

        # All assertions passed.
        _emit("passed", elapsed_ms, None)
    except AssertionError:
        # Already emitted by the assertion block; re-raise for pytest.
        raise
    except Exception as exc:  # noqa: BLE001 — broad on purpose
        # Connection errors, library bugs, etc. Emit a failure record so the
        # baseline captures the failure rather than silently dropping the case.
        failure_reason = f"{type(exc).__name__}: {exc}"
        _emit("failed", elapsed_ms, failure_reason)
        raise


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _dry_run(case: EvalCase) -> None:
    """Print the planned interaction without connecting.

    Substitutes path/query/body placeholders ({fleet_id}, etc.) using the
    same logic as the real-run path so dry-run output reflects what would
    actually be sent.
    """
    inp = case.input
    print(f"[dry-run] Case: {case.id}")
    print(f"[dry-run] Description: {case.description}")
    print(f"[dry-run] Tier: {case.tier}")
    print()

    # Load case fixtures (mirrors _run_rest_case). Used for placeholder substitution.
    fixtures: dict[str, Any] = {}
    fixtures_path = "evals/cases/e2e/_fixtures.yaml"
    if os.path.exists(fixtures_path):
        try:
            with open(fixtures_path) as fh:
                fixtures = yaml.safe_load(fh) or {}
        except Exception:  # noqa: BLE001
            fixtures = {}

    def _substitute(value: Any) -> Any:
        if isinstance(value, str):
            for key, val in (inp.path_params or {}).items():
                value = value.replace(f"{{{key}}}", str(val))
            for key, val in fixtures.items():
                value = value.replace(f"{{{key}}}", str(val))
            return value
        if isinstance(value, dict):
            return {k: _substitute(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_substitute(v) for v in value]
        return value

    if inp.type == "rest":
        stage = os.environ.get("STAGE_ENDPOINT", "<STAGE_ENDPOINT not set>")
        path = _substitute(inp.path or "")
        url = stage.rstrip("/") + path
        print(f"[dry-run] REST {inp.method} {url}")
        if inp.query_params:
            print(f"[dry-run] Query params: {_substitute(inp.query_params)}")
        if inp.body:
            print(f"[dry-run] Body: {json.dumps(_substitute(inp.body))}")
        print(f"[dry-run] Expected status: {case.expected.status_code}")
    elif inp.type == "websocket":
        stage_wss = os.environ.get("STAGE_ENDPOINT_WSS", "<STAGE_ENDPOINT_WSS not set>")
        path = _substitute(inp.path or "")
        url = stage_wss.rstrip("/") + path if path else stage_wss
        print(f"[dry-run] WebSocket {url}")
        print(f"[dry-run] Subscribe: {json.dumps(_substitute(inp.subscribe))}")
        print(f"[dry-run] Duration: {inp.duration_ms}ms")

    print(f"[dry-run] Latency budget: {case.latency_budget_ms or REST_LATENCY_BUDGET_MS}ms")


def main() -> None:
    """CLI entry point for tier3_e2e."""
    parser = argparse.ArgumentParser(description="CMS Tier 3 end-to-end eval runner")
    parser.add_argument("--case", help="Path to eval case YAML")
    parser.add_argument("--dry-run", action="store_true", help="Print planned interaction, do not connect")
    parser.add_argument("--output", help="Write JSON result to this path")
    args = parser.parse_args()

    # Load case
    if args.case and Path(args.case).exists():
        with open(args.case) as f:
            raw = yaml.safe_load(f)
        case = EvalCase.model_validate(raw)
    else:
        # Fall back to built-in sample case
        if args.case:
            print(f"[info] Case file {args.case!r} not found — using built-in sample case", file=sys.stderr)
        case = EvalCase.model_validate(_SAMPLE_CASE)

    # Path traversal guard (always, even in dry-run)
    inp = case.input
    if inp.type == "rest" and inp.path:
        try:
            _validate_path(inp.path, "rest")
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
    elif inp.type == "websocket" and inp.path:
        try:
            _validate_path(inp.path, "websocket")
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)

    if args.dry_run:
        _dry_run(case)
        sys.exit(0)

    # Real mode — requires STAGE_ENDPOINT
    stage_endpoint = os.environ.get("STAGE_ENDPOINT")
    if not stage_endpoint:
        print(
            "ERROR: STAGE_ENDPOINT env var is required. "
            "Set STAGE_ENDPOINT=<url> or use --dry-run.",
            file=sys.stderr,
        )
        sys.exit(1)

    if inp.type == "rest":
        result = _run_rest(case, stage_endpoint)
    elif inp.type == "websocket":
        stage_wss = os.environ.get(
            "STAGE_ENDPOINT_WSS",
            stage_endpoint.replace("https://", "wss://"),
        )
        result = _run_websocket(case, stage_wss)
    else:
        print(f"ERROR: Unsupported input type {inp.type!r}", file=sys.stderr)
        sys.exit(1)

    output = json.dumps(result, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(output)
        print(f"Result written to {args.output}")
    else:
        print(output)

    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
