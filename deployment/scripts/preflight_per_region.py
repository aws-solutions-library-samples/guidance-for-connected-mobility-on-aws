#!/usr/bin/env python3
"""Per-region pre-flight: verify a target AWS region has the services,
quota headroom, and Bedrock inference profile needed for a fresh CMS
clean-deploy.

Spec: .kiro/specs/2026-06-01-clean-deploy-integration-tests/spec.md
PRD:  ~/.kiro/portfolio/initiatives/2026-06-01-clean-deploy-integration-tests/prd.md
Sibling: deployment/scripts/preflight_region_clean.py (account-global
         orphan check; this script is the per-region counterpart).

Three modes:

  - Default (no flag)    : human-readable report + exit 0/1.
  - --emit-env           : KEY=VALUE lines on stdout for shell `eval`.
                           Exit 0 only if all checks pass + the
                           inference profile resolves cleanly. The
                           orchestrator wraps this as
                              eval $(preflight_per_region.py --emit-env ...)
                           to capture BEDROCK_INFERENCE_PROFILE_ID.
  - --dry-run            : do NOT call AWS; print the planned check
                           list and the constants. Useful for pipeline
                           wiring tests where credentials may be absent.

Usage:
    python3 preflight_per_region.py --region ap-northeast-1 --stage staging
    eval $(python3 preflight_per_region.py --region ap-northeast-1 --stage staging --emit-env)
    python3 preflight_per_region.py --region ap-northeast-1 --stage staging --dry-run

Constraints (from spec.md tasks.md Group 2.2):

  - Region-agnostic — the --region flag is the sole region input. No
    hardcoded region anywhere in the body.
  - Floors are CONSTANTS at the top, with citations to docs/tech.md.
  - Read-only — never call request-service-quota-increase. Surfacing
    a gap is the fix; raising the quota is operator follow-up.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# CMS-pinned model. Must match deployment/Makefile:549 (BEDROCK_AGENT_MODEL)
# and deployment/scripts/bedrock_agents_snapshot/*.json (foundationModel).
# Source: docs/tech.md § Clean-Deploy Region Verification §1.
PINNED_MODEL = "us.anthropic.claude-sonnet-4-6"
PINNED_MODEL_FAMILY = "claude-sonnet-4-6"  # geo-prefix-agnostic substring

# Inference-profile geo-prefix preference per region. Order matters.
# Source: docs/tech.md § Clean-Deploy Region Verification §1.
REGION_GEO_PREFIXES: dict[str, list[str]] = {
    "ap-northeast-1": ["jp.", "apac.", "global."],
    "ap-northeast-2": ["apac.", "global."],
    "ap-northeast-3": ["jp.", "apac.", "global."],
    "ap-southeast-1": ["apac.", "global."],
    "ap-southeast-2": ["apac.", "global."],
    "ap-south-1":     ["apac.", "global."],
    "eu-west-1":      ["eu.", "global."],
    "eu-west-2":      ["eu.", "global."],
    "eu-central-1":   ["eu.", "global."],
    "eu-north-1":     ["eu.", "global."],
    "us-east-1":      ["us.", "global."],
    "us-east-2":      ["us.", "global."],
    "us-west-1":      ["us.", "global."],
    "us-west-2":      ["us.", "global."],
}
# Fallback for regions not enumerated above. global.* exists in all
# Bedrock-enabled regions per the model card.
DEFAULT_GEO_PREFIXES: list[str] = ["global."]

# Quota floors. Each entry = (display_name, service_code, quota_code, floor).
# Source: docs/tech.md § Clean-Deploy Region Verification §3.
# Read-only check — never call request-service-quota-increase.
QUOTA_FLOORS: list[tuple[str, str, str, int]] = [
    ("Lambda concurrent executions",     "lambda",       "L-B99A9384", 200),
    ("EC2-VPC Elastic IPs",              "ec2",          "L-0263D0A3", 5),
    ("Fargate On-Demand vCPU",           "fargate",      "L-3032A538", 16),
    ("MSK brokers per cluster",          "kafka",        "L-FAB9E493", 3),
    ("MSK brokers per account",          "kafka",        "L-EDD31C36", 3),
    ("Bedrock Sonnet 4.6 RPM",           "bedrock",      "L-F6E116D7", 1000),
    ("Cognito user pools per account",   "cognito-idp",  "L-66E6DF30", 1),
]

# Service availability probes. Each entry = (display_name, client_name,
# probe_callable_lambda). The probe is region-scoped via the boto3
# client; we only care that the API responds (any response — including
# empty list — proves the service is accessible in the region).
# AccessDenied is treated as "service is available, just no permissions
# for the probe" — that's a credential issue, not a service-availability
# issue, and the harness logs it for operator follow-up.
SERVICE_PROBES: list[tuple[str, str, str]] = [
    ("Amazon Bedrock",                            "bedrock",            "list_inference_profiles"),
    ("AWS IoT Core",                              "iot",                "list_things"),
    ("Amazon MSK",                                "kafka",              "list_clusters_v2"),
    ("Amazon Managed Service for Apache Flink",   "kinesisanalyticsv2", "list_applications"),
    ("Amazon Cognito (IDP)",                      "cognito-idp",        "list_user_pools"),
    ("AWS Fargate (ECS)",                         "ecs",                "list_clusters"),
    ("Amazon Location Service",                   "location",           "list_maps"),
    ("AWS Lambda",                                "lambda",             "list_functions"),
]
# Bounds for probe calls — keep the API calls cheap (single page, max 1
# item where the API supports it).
SERVICE_PROBE_KWARGS: dict[str, dict] = {
    "list_things":            {"maxResults": 1},
    "list_clusters_v2":       {"MaxResults": 1},
    "list_applications":      {"Limit": 1},
    "list_user_pools":        {"MaxResults": 1},
    "list_clusters":          {"maxResults": 1},
    "list_maps":              {"MaxResults": 1},
    "list_functions":         {"MaxItems": 1},
    "list_inference_profiles": {"maxResults": 1, "typeEquals": "SYSTEM_DEFINED"},
}


# ---------------------------------------------------------------------------
# Output helpers (stderr for human-readable, stdout for --emit-env eval)
# ---------------------------------------------------------------------------

# When --emit-env is set we redirect human-readable output to stderr so
# stdout contains ONLY KEY=VALUE pairs the orchestrator can `eval`.
_HUMAN_STREAM = sys.stderr  # default; toggled by main()


def _print(msg: str) -> None:
    print(msg, file=_HUMAN_STREAM)


def banner(msg: str) -> None:
    _print("\n" + "═" * 64)
    _print(f"  {msg}")
    _print("═" * 64)


def ok(label: str) -> None:
    _print(f"  ✅ {label}")


def warn(label: str, hint: str = "") -> None:
    suffix = f"  ← {hint}" if hint else ""
    _print(f"  ⚠️  {label}{suffix}")


def fail(label: str, hint: str = "") -> None:
    suffix = f"  ← {hint}" if hint else ""
    _print(f"  ❌ {label}{suffix}")


# ---------------------------------------------------------------------------
# Bedrock inference-profile resolution
# ---------------------------------------------------------------------------

def resolve_inference_profile(region: str) -> Optional[str]:
    """Return the SYSTEM_DEFINED inference-profile ID for the pinned
    model in the given region, choosing the geo-prefix that matches
    the region's geography. Falls back to global.* if no geo match.
    Returns None if no profile matches at all (BLOCKING for harness)."""
    bedrock = boto3.client("bedrock", region_name=region)
    try:
        resp = bedrock.list_inference_profiles(typeEquals="SYSTEM_DEFINED")
    except ClientError as e:
        warn(f"Bedrock list_inference_profiles failed in {region}: {e}",
             "service may not be enabled in this region")
        return None
    candidates = [
        p["inferenceProfileId"]
        for p in resp.get("inferenceProfileSummaries", [])
        if PINNED_MODEL_FAMILY in p.get("inferenceProfileId", "")
        and p.get("status") == "ACTIVE"
    ]
    if not candidates:
        return None
    # Select by region's geo-prefix preference order.
    prefixes = REGION_GEO_PREFIXES.get(region, DEFAULT_GEO_PREFIXES)
    for prefix in prefixes:
        for cand in candidates:
            if cand.startswith(prefix):
                return cand
    # No geo-prefix match; return the first candidate (deterministic
    # fallback).
    return candidates[0]


# ---------------------------------------------------------------------------
# Quota check
# ---------------------------------------------------------------------------

def check_quota(region: str, service_code: str, quota_code: str, floor: int) -> tuple[bool, str]:
    """Return (passes, message) for a single quota."""
    sq = boto3.client("service-quotas", region_name=region)
    try:
        resp = sq.get_service_quota(ServiceCode=service_code, QuotaCode=quota_code)
    except ClientError as e:
        # NoSuchResource = quota not surfaced for this service in this
        # region. Try get_aws_default_service_quota as fallback.
        code = e.response.get("Error", {}).get("Code", "")
        if code == "NoSuchResourceException":
            try:
                resp = sq.get_aws_default_service_quota(
                    ServiceCode=service_code, QuotaCode=quota_code,
                )
            except ClientError as e2:
                return False, f"unavailable ({e2.response.get('Error', {}).get('Code', e2)})"
        else:
            return False, f"error ({code or e})"
    value = resp.get("Quota", {}).get("Value")
    if value is None:
        return False, "no value returned"
    return value >= floor, f"current={value:.0f} floor={floor}"


# ---------------------------------------------------------------------------
# Service-availability probe
# ---------------------------------------------------------------------------

def probe_service(region: str, client_name: str, op_name: str) -> tuple[bool, str]:
    """Return (available, message). available=True if the API returned
    any response (including empty list) or AccessDenied (which means
    the service IS reachable, just our credentials lack the probe
    permission — operator follow-up, not a region-availability issue)."""
    try:
        client = boto3.client(client_name, region_name=region)
    except Exception as e:
        return False, f"client init failed ({e})"
    op = getattr(client, op_name, None)
    if op is None:
        return False, f"client has no operation {op_name}"
    kwargs = SERVICE_PROBE_KWARGS.get(op_name, {})
    try:
        op(**kwargs)
        return True, "available"
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation"):
            return True, "available (probe AccessDenied — operator IAM follow-up)"
        if code in ("EndpointConnectionError", "InvalidEndpointError"):
            return False, f"endpoint unreachable ({code})"
        if code in ("OptInRequired",):
            return False, "service requires opt-in for this region"
        # Any other ClientError is treated as service-not-available
        # for this region (or transient). Caller can re-run.
        return False, f"unexpected error ({code or e})"
    except (BotoCoreError, NoCredentialsError) as e:
        return False, f"botocore error ({e})"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Per-region pre-flight for CMS clean-deploy harness.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--region", required=True, help="AWS region to check")
    ap.add_argument("--stage", default="staging",
                    help="Deployment stage (default: staging)")
    ap.add_argument("--emit-env", action="store_true",
                    help="On stdout, emit KEY=VALUE lines for shell eval. "
                         "Human report goes to stderr.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Do NOT call AWS; print the planned checks and exit 0.")
    args = ap.parse_args()

    # --emit-env: human report → stderr; KEY=VALUE → stdout.
    # Default: human report → stdout (override _HUMAN_STREAM module-global).
    global _HUMAN_STREAM
    if not args.emit_env:
        _HUMAN_STREAM = sys.stdout

    # Region sanity. We don't enforce a strict whitelist (fresh region
    # support is a feature) but we reject obviously malformed inputs.
    if not re.match(r"^[a-z]{2,3}-[a-z]+-\d+$", args.region):
        fail(f"--region {args.region!r} does not look like an AWS region code",
             "expected e.g. ap-northeast-1, eu-west-2, us-east-1")
        return 1

    banner(f"PER-REGION PRE-FLIGHT: {args.region} (stage={args.stage})")

    if args.dry_run:
        _print(f"  --dry-run: skipping AWS calls.")
        _print(f"  Pinned model:    {PINNED_MODEL}")
        _print(f"  Quota floors:    {len(QUOTA_FLOORS)} entries")
        _print(f"  Service probes:  {len(SERVICE_PROBES)} entries")
        _print(f"  Geo-prefix order for {args.region}: "
               f"{REGION_GEO_PREFIXES.get(args.region, DEFAULT_GEO_PREFIXES)}")
        if args.emit_env:
            print("PREFLIGHT_DRY_RUN=1")
        return 0

    failures: list[str] = []

    # Service availability ----------------------------------------------
    banner("Service availability")
    for label, client_name, op_name in SERVICE_PROBES:
        avail, msg = probe_service(args.region, client_name, op_name)
        if avail:
            ok(f"{label}: {msg}")
        else:
            fail(f"{label}: {msg}")
            failures.append(f"service:{label}:{msg}")

    # Quota check -------------------------------------------------------
    banner("Quota floors")
    for label, service_code, quota_code, floor in QUOTA_FLOORS:
        passes, msg = check_quota(args.region, service_code, quota_code, floor)
        if passes:
            ok(f"{label}: {msg}")
        else:
            warn(f"{label}: {msg}",
                 "raise via service-quotas console; do NOT auto-request")
            failures.append(f"quota:{label}:{msg}")

    # Bedrock inference profile ----------------------------------------
    banner("Bedrock inference-profile resolution")
    profile_id = resolve_inference_profile(args.region)
    if profile_id:
        ok(f"Resolved: {profile_id}  (model={PINNED_MODEL_FAMILY})")
    else:
        fail(f"No SYSTEM_DEFINED profile matching '{PINNED_MODEL_FAMILY}' "
             f"in {args.region}",
             "the model is not available in this region — surface to operator")
        failures.append("inference-profile:no-match")

    # Summary -----------------------------------------------------------
    _print("")
    if failures:
        fail(f"{len(failures)} pre-flight check(s) failed:")
        for f in failures:
            _print(f"      - {f}")
        # Even in --emit-env mode we emit what we resolved (so the
        # orchestrator can log it before deciding to halt).
        if args.emit_env and profile_id:
            print(f"BEDROCK_INFERENCE_PROFILE_ID={profile_id}")
        if args.emit_env:
            print(f"PREFLIGHT_FAILURES={len(failures)}")
        return 1

    ok(f"All pre-flight checks passed for {args.region}")
    if args.emit_env:
        # Emit the resolved env to stdout. Each line is a single
        # KEY=VALUE pair safe for `eval`.
        print(f"BEDROCK_INFERENCE_PROFILE_ID={profile_id}")
        print(f"AWS_REGION={args.region}")
        print(f"DEPLOYMENT_STAGE={args.stage}")
        print(f"PREFLIGHT_FAILURES=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
