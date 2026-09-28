#!/usr/bin/env python3
"""
RED-phase regression test: every AWS::ApiGateway::Method in every synthesized
template must have AuthorizationType in {COGNITO_USER_POOLS, AWS_IAM},
UNLESS the HttpMethod is OPTIONS (CORS preflight — allowed to be NONE).

Also checks AWS::ApiGatewayV2::Route resources:
The $connect route must have AuthorizationType in {AWS_IAM, JWT, CUSTOM}
OR a non-null AuthorizerId, UNLESS cms.allow_unauth_websocket=true in cdk.json.
$disconnect and $default are intentionally exempt: they run only on already-
authenticated connections that passed the $connect authorizer, matching the
AWS-recommended WebSocket API pattern.

Run:
    cd deployment && python3 scripts/test_no_unauth_methods.py

If cdk.out is empty, run `cdk synth --all` first (requires env to be set).
For the predictive-agent stack: `DEPLOY_PREDICTIVE_AGENT=true cdk synth --all`.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

CDK_OUT = Path(__file__).parent.parent / "cdk.out"
CDK_JSON = Path(__file__).parent.parent / "cdk.json"
ALLOWED_AUTH = {"COGNITO_USER_POOLS", "AWS_IAM"}
ALLOWED_V2_AUTH = {"AWS_IAM", "JWT", "CUSTOM"}

# Expected templates that must be present before evaluating WS routes.
EXPECTED_UI_TEMPLATES = {"cms-staging-ui", "cms-dev-ui"}

# Post-handshake WS routes that intentionally carry no authorizer.
WS_POST_HANDSHAKE_ROUTES = {"$disconnect", "$default"}


def _stack_name(tpl_path: Path) -> str:
    """cms-staging-ui.template.json → cms-staging-ui"""
    return tpl_path.stem.removesuffix(".template")


def load_cdk_context() -> dict:
    """Load context from cdk.json."""
    try:
        data = json.loads(CDK_JSON.read_text())
        return data.get("context", {})
    except Exception:
        return {}


def ensure_synth():
    """Re-synth if cdk.out has no templates at all."""
    templates = list(CDK_OUT.glob("*.template.json"))
    if templates:
        return
    print("cdk.out empty — running cdk synth --all ...")
    subprocess.run(["cdk", "synth", "--all"], check=True, cwd=CDK_OUT.parent)


def check_templates():
    templates = sorted(CDK_OUT.glob("*.template.json"))
    total_fail = 0
    any_fails = False

    # ── REST method check (existing H1 check, unchanged) ──────────────────────
    pa_found = any("predictive" in t.name for t in templates)
    if not pa_found:
        print(
            "WARNING: No predictive-agent template in cdk.out. "
            "Re-synth with DEPLOY_PREDICTIVE_AGENT=true to include it. "
            "Expected 5 additional failures."
        )

    for tpl in templates:
        data = json.loads(tpl.read_text())
        resources = data.get("Resources", {})
        fails = []
        for logical_id, resource in resources.items():
            if resource.get("Type") != "AWS::ApiGateway::Method":
                continue
            props = resource.get("Properties", {})
            auth = props.get("AuthorizationType", "NONE")
            method = props.get("HttpMethod", "")
            if method == "OPTIONS":
                continue  # CORS preflight — allowed NONE
            if auth not in ALLOWED_AUTH:
                fails.append((logical_id, method, auth))

        if not fails:
            continue

        any_fails = True
        total_fail += len(fails)
        print(f"\n[FAIL] {tpl.stem}: {len(fails)} unauth REST method(s)")
        for logical_id, method, auth in fails:
            print(f"  - {logical_id}  HttpMethod={method}  AuthorizationType={auth}")

    # ── WebSocket V2 route check ───────────────────────────────────────────────
    context = load_cdk_context()
    allow_unauth_ws = context.get("cms.allow_unauth_websocket") in (True, "true", "1")

    if allow_unauth_ws:
        print("INFO: cms.allow_unauth_websocket=true — WebSocket route auth check skipped.")
    else:
        # Assert that the UI stack template is present before evaluating WS routes.
        stack_names = {_stack_name(t) for t in templates}
        ui_template_found = bool(stack_names & EXPECTED_UI_TEMPLATES)
        if not ui_template_found:
            print(
                f"\n[FAIL] No UI stack template found in cdk.out "
                f"(expected one of: {sorted(EXPECTED_UI_TEMPLATES)}). "
                "Re-synth with DEPLOYMENT_STAGE=staging/dev before running this check."
            )
            any_fails = True
            total_fail += 1
        else:
            for tpl in templates:
                if _stack_name(tpl) not in EXPECTED_UI_TEMPLATES:
                    continue
                data = json.loads(tpl.read_text())
                resources = data.get("Resources", {})
                ws_fails = []
                for logical_id, resource in resources.items():
                    if resource.get("Type") != "AWS::ApiGatewayV2::Route":
                        continue
                    props = resource.get("Properties", {})
                    auth = props.get("AuthorizationType")
                    authorizer_id = props.get("AuthorizerId")
                    route_key = props.get("RouteKey", logical_id)
                    # $disconnect and $default are post-handshake routes: they run
                    # only on connections already authenticated via $connect.
                    # This mirrors the OPTIONS exemption for REST methods.
                    if route_key in WS_POST_HANDSHAKE_ROUTES:
                        continue
                    # Pass if auth type is known-good OR authorizer_id is set
                    if auth in ALLOWED_V2_AUTH or authorizer_id:
                        continue
                    ws_fails.append((logical_id, route_key, auth))

                if not ws_fails:
                    continue

                any_fails = True
                total_fail += len(ws_fails)
                print(f"\n[FAIL] {_stack_name(tpl)}: {len(ws_fails)} unauth WebSocket route(s)")
                for logical_id, route_key, auth in ws_fails:
                    print(
                        f"  - {logical_id}  RouteKey={route_key}  "
                        f"AuthorizationType={auth or 'NONE (absent)'}"
                    )

    if not any_fails:
        print("All ApiGateway methods and WebSocket routes have valid authorization. No failures.")
        return 0

    print(f"\nTotal unauthorized resources: {total_fail}")
    return 1


def main():
    ensure_synth()
    sys.exit(check_templates())


if __name__ == "__main__":
    main()
