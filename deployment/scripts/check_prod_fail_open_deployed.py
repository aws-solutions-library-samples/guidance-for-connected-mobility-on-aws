#!/usr/bin/env python3
"""Pre-deploy hook to verify the fail-open authz fix is deployed to prod Lambda.

This script addresses the distinction between committed and deployed state. The
fail-open authz fix (commit d235fb31, authored 2026-08-05T22:12:17Z) can be
present in git while the prod Lambda remains stale. As of 2026-08-07, prod's
FleetAPIFunction was ~43 minutes older than the fix commit, while engineering
believed the fix was live. This hook closes that window by checking deployed state.

MECHANISM
---------
  1. Queries aws lambda list-functions for all cms-prod-ui-FleetAPIFunction* matches
  2. Picks the most recent function (CDK function-name hashing may produce multiple)
  3. Reads its LastModified timestamp
  4. Compares against the fix commit's authored time (2026-08-05T22:12:17Z)
  5. Exits 0 if deployed >= authored time
  6. Exits non-zero if stale
  7. Escape hatch: if the pending CloudFormation change set includes an
     AWS::Lambda::Function modification on the FleetAPI resource, the deploy IS
     the fix — exit 0 with escape-hatch message

SPEC
----
  Spec: .kiro/specs/2026-08-07-cms-account-provisioning-model/ (Group 4, task 2)
  Decision: decisions.md § 2026-08-07 "Phase B fail-open blocker checks deployed
     state, not commit state"

USAGE
-----
  # Check if prod Lambda is current (exit 0 if yes, non-zero if stale or error)
  python3 deployment/scripts/check_prod_fail_open_deployed.py

  # With optional change-set inspection (recommended for pre-deploy gates)
  python3 deployment/scripts/check_prod_fail_open_deployed.py --check-change-set

CREDENTIALS
-----------
  Uses the operator's AWS_DEFAULT_REGION + boto3 session credentials.
  Requires lambda:GetFunction and optional cloudformation:DescribeChangeSet.
"""
from __future__ import annotations

import argparse
import json
import os
import io
import zipfile
import urllib.request
import sys
from datetime import datetime, timezone
from typing import Optional

import boto3
from botocore.exceptions import ClientError


# see decisions.md 2026-08-07 "deployed state not commit state"
FIX_COMMIT_HASH = "d235fb31"
FIX_COMMIT_AUTHORED_TIME = datetime(2026, 8, 5, 22, 12, 17, tzinfo=timezone.utc)

# Region the prod stack is deployed into.
PROD_REGION = "us-east-1"


def get_latest_fleet_api_function() -> Optional[tuple[str, datetime]]:
    """
    Query Lambda for cms-prod-ui-FleetAPIFunction* functions.
    Return (function_name, LastModified datetime) of the most recent, or None if not found.

    MUST paginate. `list_functions()` returns at most 50 functions per page, and the prod
    account held 91 Lambdas when this was written, with the FleetAPIFunction at position
    82 — i.e. NOT on the first page. An unpaginated call returned zero candidates, so this
    function reported "no matching function found", the caller exited non-zero, and the
    pre-deploy hook would have permanently blocked a legitimate prod deploy while
    appearing to be a working safety check.

    Every unit test for this module stubs the Lambda client with a single page, so all of
    them passed against the unpaginated version. Found by review 2026-08-10 and confirmed
    against live AWS; see review.md Cycle 1. This is the "a stub cannot fail the way a
    service fails" failure mode recorded in ~/.kiro/steering/agentic-tiers.md.
    """
    candidates: list[dict] = []
    try:
        lambda_client = boto3.client("lambda", region_name=PROD_REGION)
        paginator = lambda_client.get_paginator("list_functions")
        for page in paginator.paginate():
            candidates.extend(
                f for f in page.get("Functions", [])
                if f["FunctionName"].startswith("cms-prod-ui-FleetAPIFunction")
            )
    except ClientError as e:
        print(f"ERROR: Failed to list Lambda functions: {e.response['Error']['Code']} — {e.response['Error']['Message']}", file=sys.stderr)
        return None

    if not candidates:
        print("ERROR: No cms-prod-ui-FleetAPIFunction* function found in prod", file=sys.stderr)
        return None

    # Pick the most recent by LastModified
    most_recent = max(candidates, key=lambda f: f["LastModified"])
    func_name = most_recent["FunctionName"]
    last_modified_str = most_recent["LastModified"]

    # Lambda returns an ISO-8601 string; tolerate a pre-parsed datetime too.
    if isinstance(last_modified_str, str):
        last_modified = datetime.fromisoformat(last_modified_str)
    else:
        last_modified = last_modified_str

    return (func_name, last_modified)


def check_pending_change_set() -> bool:
    """
    Query CloudFormation for a pending change set that includes an AWS::Lambda::Function
    modification on the FleetAPI resource. Return True if such a change exists (escape hatch).
    
    Note: A real deployment will have a change set ID. For now, we check if there's any
    pending change set; a more precise implementation would need the change set ID.
    Return False if any error or no change set found.
    """
    try:
        cfn_client = boto3.client("cloudformation", region_name=PROD_REGION)
        
        # List change sets for the cms-prod-ui stack
        response = cfn_client.list_change_sets(StackName="cms-prod-ui")
        
        change_sets = response.get("Summaries", [])
        if not change_sets:
            return False
        
        # Check the most recent (or any CREATE_PENDING/CREATE_IN_PROGRESS) change set
        for cs in change_sets:
            if cs["Status"] not in ("CREATE_PENDING", "CREATE_IN_PROGRESS", "CREATE_COMPLETE"):
                continue
            
            cs_name = cs["ChangeSetName"]
            # Describe the change set to see its changes
            try:
                cs_detail = cfn_client.describe_change_set(
                    ChangeSetName=cs_name,
                    StackName="cms-prod-ui"
                )
            except ClientError:
                continue
            
            # Check if any change is an AWS::Lambda::Function for FleetAPI
            for change in cs_detail.get("Changes", []):
                change_detail = change.get("ResourceChange", {})
                if (change_detail.get("ResourceType") == "AWS::Lambda::Function" and
                    "FleetAPI" in change_detail.get("LogicalResourceId", "")):
                    return True
        
        return False
    except ClientError as e:
        # If we can't query change sets (permissions, no stack, etc.), we don't use escape hatch
        return False




def get_deployed_fail_open_verdict(func_name: str) -> Optional[bool]:
    """Download the DEPLOYED Lambda artifact and report whether the fix is present.

    This is the only check here that actually answers the question the spec asks —
    "is the fail-open fix live in prod?" — because it reads the running code rather
    than a proxy for it. Returns True (fix present), False (fix absent), or None
    (could not determine, which callers must treat as failure, never as success).

    Two proxies were tried first and both were wrong, in opposite directions:

      * `LastModified >= fix_commit_time` produced a FALSE GREEN. A CloudFormation
        rollback bumps LastModified while restoring old code, so a deploy that
        tried and failed to ship the fix reported success. Observed live
        2026-08-10T17:01Z.
      * Comparing the stack's declared S3Key against the artifact the source tree
        synthesises produced a FALSE RED. Any unrelated edit to main_api changes
        the hash, so the check failed while the fix was demonstrably deployed.
        Observed the same day, immediately after an unrelated projection change.

    Both proxies conflate identity with content. The artifact hash answers "is the
    deployed build the same build as my tree", which is a different and stricter
    question than "does the deployed build contain this fix". Read the code.
    """
    try:
        lam = boto3.client("lambda", region_name=PROD_REGION)
        meta = lam.get_function(FunctionName=func_name)
        url = meta.get("Code", {}).get("Location")
        if not url:
            print("ERROR: no code location returned for deployed function", file=sys.stderr)
            return None

        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
            blob = resp.read()

        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names = [n for n in zf.namelist() if n.endswith("index.py")]
            if not names:
                print("ERROR: index.py not found in deployed artifact", file=sys.stderr)
                return None
            source = zf.read(sorted(names, key=len)[0]).decode("utf-8", "replace")
    except (ClientError, OSError, zipfile.BadZipFile, ValueError) as e:
        print(f"ERROR: could not inspect deployed artifact: {type(e).__name__}", file=sys.stderr)
        return None

    # The two fail-open defects, as they appeared before d235fb31. Comments are
    # stripped first so a future explanatory comment quoting the old code cannot
    # make a patched Lambda look unpatched.
    code_only = "\n".join(
        line.split("#", 1)[0] for line in source.splitlines()
    )
    offenders = [p for p in ("or not user_groups", "or not user_fleet_ids") if p in code_only]
    if offenders:
        print(
            f"✗ Deployed code STILL CONTAINS fail-open pattern(s): {offenders}",
            file=sys.stderr,
        )
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify fail-open authz fix is deployed to prod Lambda"
    )
    parser.add_argument(
        "--check-change-set",
        action="store_true",
        help="Also check for pending CloudFormation change set with Lambda modification (escape hatch)"
    )
    args = parser.parse_args()

    # Query the most recent prod FleetAPIFunction Lambda
    result = get_latest_fleet_api_function()
    if result is None:
        return 1

    func_name, last_modified = result

    print(f"Found function: {func_name}")
    print(f"  LastModified: {last_modified.isoformat()}")
    print(f"  Fix commit:   {FIX_COMMIT_AUTHORED_TIME.isoformat()}")

    # Check escape hatch: if pending change set will replace the Lambda, the deploy IS the fix
    if args.check_change_set and check_pending_change_set():
        print(f"ESCAPE HATCH: Pending CloudFormation change set includes Lambda modification on FleetAPI resource")
        print(f"Exiting 0 — the deploy will provide the fix")
        return 0

    # Main check, part 1: LastModified >= fix time.
    #
    # NECESSARY BUT NOT SUFFICIENT, and kept only as a cheap early exit. A
    # CloudFormation ROLLBACK reverts the Lambda's code while still bumping
    # LastModified to the rollback time, so a stack that tried and failed to ship
    # the fix reports a timestamp NEWER than the fix commit while serving PRE-FIX
    # code. Observed live 2026-08-10T17:01Z. Timestamps say WHEN a function
    # changed, never WHAT it contains — part 2 is the real check.
    if last_modified < FIX_COMMIT_AUTHORED_TIME:
        delta = FIX_COMMIT_AUTHORED_TIME - last_modified
        print(f"✗ Deployed Lambda is stale by {delta.total_seconds():.0f} seconds", file=sys.stderr)
        print("  Deploy required before Phase B enablement", file=sys.stderr)
        return 1

    # Main check, part 2: read the DEPLOYED code and look for the defect itself.
    # Immune to both failure modes above — a rollback cannot fake absent patterns,
    # and an unrelated source edit cannot introduce them.
    verdict = get_deployed_fail_open_verdict(func_name)
    if verdict is None:
        print(
            "✗ Could not determine whether the deployed code contains the fix. "
            "Refusing to report success on a timestamp alone.",
            file=sys.stderr,
        )
        return 1
    if verdict is False:
        print("  Deploy required before Phase B enablement", file=sys.stderr)
        return 1

    print("✓ Deployed code does NOT contain either fail-open pattern — fix is live")
    return 0


if __name__ == "__main__":
    sys.exit(main())
