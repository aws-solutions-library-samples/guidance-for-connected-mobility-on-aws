#!/usr/bin/env python3
"""Integration test: Identity Pool must reject unauthenticated (guest) credential requests.

Reads the Identity Pool ID from the CFN ui stack outputs, then calls
cognito-identity:GetId without any logins map (anonymous caller).

With default config (allow_unauthenticated_identities=False), GetId must fail
with NotAuthorizedException, ResourceNotFoundException, or InvalidParameterException.

Exit 0 — GetId failed as expected (guest creds are blocked).
Exit 1 — GetId succeeded (guest creds are NOT blocked — security defect).

Usage:
    DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 python3 deployment/scripts/test_guest_creds_blocked.py
"""
from __future__ import annotations

import os
import sys

import boto3
from botocore.exceptions import ClientError

STAGE = os.environ.get("DEPLOYMENT_STAGE", "staging")
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"

# Errors that confirm unauthenticated identities are blocked
EXPECTED_ERROR_CODES = {
    "NotAuthorizedException",
    "ResourceNotFoundException",
    "InvalidParameterException",
}


def get_identity_pool_id() -> str:
    """Read IdentityPoolId from the deployed ui stack outputs."""
    cf = boto3.client("cloudformation", region_name=REGION)
    stack_name = f"cms-{STAGE}-ui"
    resp = cf.describe_stacks(StackName=stack_name)
    outputs = {o["OutputKey"]: o["OutputValue"] for o in resp["Stacks"][0].get("Outputs", [])}
    pool_id = outputs.get("IdentityPoolId")
    if not pool_id:
        raise KeyError(f"IdentityPoolId not found in {stack_name} outputs. Keys: {list(outputs)}")
    return pool_id


def main() -> None:
    identity_pool_id = get_identity_pool_id()
    print(f"Identity Pool: {identity_pool_id}")

    # No credentials needed — the call is itself unauthenticated (no logins map)
    client = boto3.client("cognito-identity", region_name=REGION)

    try:
        resp = client.get_id(AccountId=boto3.client("sts").get_caller_identity()["Account"],
                             IdentityPoolId=identity_pool_id)
        # GetId succeeded — guest credentials are NOT blocked
        identity_id = resp.get("IdentityId", "<unknown>")
        print(f"FAIL  GetId succeeded (IdentityId={identity_id}); "
              "allow_unauthenticated_identities must be False.")
        sys.exit(1)
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in EXPECTED_ERROR_CODES:
            print(f"PASS  GetId rejected with {code} — guest credentials are blocked.")
            sys.exit(0)
        raise


if __name__ == "__main__":
    main()
