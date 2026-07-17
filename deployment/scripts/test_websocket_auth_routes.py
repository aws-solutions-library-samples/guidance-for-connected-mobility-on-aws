#!/usr/bin/env python3
"""
WebSocket auth-mode test — two-pass synth.

Pass 1 (default context):
  - $connect route has AuthorizationType=CUSTOM and non-null AuthorizerId
  - $disconnect and $default routes have no AuthorizationType (default NONE)

Pass 2 (opt-in context, --context cms.allow_unauth_websocket=true):
  - All three routes lack AuthorizationType

Today (pre-fix) BOTH passes FAIL because no authorizer exists.

Spec: .kiro/specs/2026-06-15-cms-websocket-api-auth-gap/

Run:
    cd deployment && DEPLOYMENT_STAGE=staging CMS_DEMO_DEFAULT_PASSWORD=dummy \\
        python3 scripts/test_websocket_auth_routes.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEPLOYMENT_DIR = Path(__file__).parent.parent

CONNECT_ROUTE_KEYS = {"$connect", "$disconnect", "$default"}


def _find_cdk() -> str:
    cdk = os.environ.get("CDK_BIN") or shutil.which("cdk")
    if not cdk:
        raise RuntimeError(
            "CDK binary not found. Set CDK_BIN env var or ensure 'cdk' is on PATH."
        )
    return cdk


def _base_env() -> dict:
    stage = os.environ.get("DEPLOYMENT_STAGE", "staging")
    return {
        **os.environ,
        "DEPLOYMENT_STAGE": stage,
        "CMS_DEMO_DEFAULT_PASSWORD": os.environ.get(
            "CMS_DEMO_DEFAULT_PASSWORD", "dummy-for-synth"
        ),
        "CDK_DEFAULT_ACCOUNT": os.environ.get("CDK_DEFAULT_ACCOUNT", ""),
    }


def _resolve_stack_name() -> str:
    stage = os.environ.get("DEPLOYMENT_STAGE", "staging")
    for candidate in [f"cms-{stage}-ui", "cms-dev-ui"]:
        print(f"  Trying stack name: {candidate}")
        return candidate
    raise RuntimeError("Cannot resolve UI stack name")


def _synth(extra_context: list[str], tmpdir: str) -> dict:
    """Run cdk synth and return the parsed template."""
    cdk = _find_cdk()
    stack_name = _resolve_stack_name()
    cmd = [cdk, "synth", stack_name, "--output", tmpdir, "--quiet"] + extra_context
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(DEPLOYMENT_DIR),
        env=_base_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"cdk synth failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout[-2000:]}\n"
            f"stderr: {result.stderr[-2000:]}"
        )
    template_path = Path(tmpdir) / f"{stack_name}.template.json"
    if not template_path.exists():
        available = list(Path(tmpdir).glob("*.template.json"))
        raise FileNotFoundError(
            f"Expected {template_path}, got: {[str(p.name) for p in available]}"
        )
    return json.loads(template_path.read_text())


def _get_ws_routes(template: dict) -> dict[str, dict]:
    """Return {RouteKey: props} for all AWS::ApiGatewayV2::Route resources."""
    routes: dict[str, dict] = {}
    for _lid, resource in template.get("Resources", {}).items():
        if resource.get("Type") == "AWS::ApiGatewayV2::Route":
            props = resource.get("Properties", {})
            key = props.get("RouteKey", _lid)
            routes[key] = {"logical_id": _lid, **props}
    return routes


def _check_pass1(template: dict) -> list[str]:
    """
    Pass 1 (default context) assertions:
    - $connect: AuthorizationType=CUSTOM AND non-null AuthorizerId
    - $disconnect, $default: no AuthorizationType field present
    """
    routes = _get_ws_routes(template)
    failures: list[str] = []

    for rk in CONNECT_ROUTE_KEYS:
        if rk not in routes:
            failures.append(f"Route {rk!r} not found in template")
            continue

    if failures:
        return failures

    # $connect must have CUSTOM + AuthorizerId
    connect = routes["$connect"]
    auth_type = connect.get("AuthorizationType")
    auth_id = connect.get("AuthorizerId")
    if auth_type != "CUSTOM":
        failures.append(
            f"$connect: expected AuthorizationType=CUSTOM, got {auth_type!r}"
        )
    if not auth_id:
        failures.append(
            "$connect: expected non-null AuthorizerId, got none"
        )

    # $disconnect and $default must NOT have AuthorizationType
    for rk in ("$disconnect", "$default"):
        auth = routes[rk].get("AuthorizationType")
        if auth is not None:
            failures.append(
                f"{rk}: expected no AuthorizationType (should inherit NONE), got {auth!r}"
            )

    return failures


def _check_pass2(template: dict) -> list[str]:
    """
    Pass 2 (opt-in context) assertions:
    - All three routes lack AuthorizationType
    """
    routes = _get_ws_routes(template)
    failures: list[str] = []

    for rk in CONNECT_ROUTE_KEYS:
        if rk not in routes:
            failures.append(f"Route {rk!r} not found in template")
            continue
        auth = routes[rk].get("AuthorizationType")
        if auth is not None:
            failures.append(
                f"{rk}: expected no AuthorizationType with opt-in flag, got {auth!r}"
            )

    return failures


def main() -> int:
    results: dict[str, list[str]] = {}

    # ── Pass 1: default context ────────────────────────────────────────────────
    print("\n=== Pass 1: default context (cms.allow_unauth_websocket=false) ===")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            template = _synth([], tmpdir)
        failures = _check_pass1(template)
    except Exception as exc:
        failures = [f"Synth/parse error: {exc}"]
    results["Pass 1 (default)"] = failures
    if failures:
        print(f"  FAIL — {len(failures)} assertion(s):")
        for f in failures:
            print(f"    - {f}")
    else:
        print("  PASS")

    # ── Pass 2: opt-in context ─────────────────────────────────────────────────
    print("\n=== Pass 2: opt-in context (cms.allow_unauth_websocket=true) ===")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            template = _synth(
                ["--context", "cms.allow_unauth_websocket=true"],
                tmpdir,
            )
        failures = _check_pass2(template)
    except Exception as exc:
        failures = [f"Synth/parse error: {exc}"]
    results["Pass 2 (opt-in)"] = failures
    if failures:
        print(f"  FAIL — {len(failures)} assertion(s):")
        for f in failures:
            print(f"    - {f}")
    else:
        print("  PASS")

    # ── Summary ────────────────────────────────────────────────────────────────
    print("\n=== Summary ===")
    all_pass = True
    for name, failures in results.items():
        status = "PASS" if not failures else "FAIL"
        print(f"  {name}: {status}")
        if failures:
            all_pass = False

    if all_pass:
        print("\nAll WebSocket auth-route assertions passed.")
        return 0
    else:
        print("\nSome assertions failed — see above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
