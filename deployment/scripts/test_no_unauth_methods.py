#!/usr/bin/env python3
"""
RED-phase regression test: every AWS::ApiGateway::Method in every synthesized
template must have AuthorizationType in {COGNITO_USER_POOLS, AWS_IAM},
UNLESS the HttpMethod is OPTIONS (CORS preflight — allowed to be NONE).

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
ALLOWED_AUTH = {"COGNITO_USER_POOLS", "AWS_IAM"}


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
        print(f"\n[FAIL] {tpl.stem}: {len(fails)} unauth method(s)")
        for logical_id, method, auth in fails:
            print(f"  - {logical_id}  HttpMethod={method}  AuthorizationType={auth}")

    if not any_fails:
        print("All ApiGateway methods have valid authorization. No failures.")
        return 0

    print(f"\nTotal unauthorized methods (non-OPTIONS): {total_fail}")
    return 1


def main():
    ensure_synth()
    sys.exit(check_templates())


if __name__ == "__main__":
    main()
