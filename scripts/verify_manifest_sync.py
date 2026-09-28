#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify the git-tracked transform manifests match the ones actually deployed to S3.

Spec: `.kiro/specs/2026-09-20-transform-manifest-contract-guards/spec.md` T2.2 (D4).

## Why this exists

`OEMTelemetryProcessor` loads manifests from
``s3://cms-{stage}-transform-manifests-{region}-{account}/manifests/``. Code review sees
the copies in ``services/data_processing/manifests/``. Those are two different artifacts,
and the only thing connecting them is a human remembering to run
``make sync-manifests`` — which appears nowhere in ``.github/workflows/``.

So a reviewed manifest change can sit in git, unpublished, while Flink keeps applying the
old mapping; or an emergency S3 edit can diverge from git with nothing to notice. Neither
state produces an error anywhere.

## Why this is NOT wired into blocking CI

It needs AWS credentials. A guard that fails in CI because credentials are absent gets
disabled, and a disabled guard protects nothing — so this ships as ``make verify-manifests``
plus an opt-in test, the same posture as the ``LIVE_TESTS=1`` gate in
``~/.kiro/steering/spec-workflow.md``.

That makes the exit codes load-bearing: **"cannot reach S3" must never be confusable with
"drift found"**, or the first person to wire this into CI will see red, assume drift, and
either chase a phantom or switch it off.

  * ``0`` — every tracked manifest matches its deployed copy, and the sets agree.
  * ``1`` — real finding: a digest differs, or a manifest exists on one side only.
  * ``2`` — could not evaluate: no credentials, bucket missing, access denied. This is
    **NOT a drift finding** — the same wording the runtime messages use, so a reader who
    sees one and greps for the other finds this. Distinct so automation can treat it as
    "skipped" rather than "failed".
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_DIR = _REPO_ROOT / "services" / "data_processing" / "manifests"

#: Only these are compared. `README.md` lives in the same directory and is synced to S3 by
#: `make sync-manifests`, but it is documentation, not a contract — a prose edit pending
#: publication is not a finding worth failing on.
MANIFEST_GLOB = "*-transform.json"

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_CANNOT_EVALUATE = 2


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_bucket(stage: str, region: str, account: str) -> str:
    """Mirror the name `flink_stack.py` builds for `S3_MANIFEST_BUCKET`."""
    return f"cms-{stage}-transform-manifests-{region}-{account}"


def verify(stage: str, region: str, bucket_override: str | None = None
           ) -> tuple[int, list[str]]:
    lines: list[str] = []

    if not MANIFEST_DIR.is_dir():
        return EXIT_FINDING, [f"❌ manifest directory not found: {MANIFEST_DIR}"]

    local = {p.name: p.read_bytes() for p in sorted(MANIFEST_DIR.glob(MANIFEST_GLOB))}
    if not local:
        return EXIT_FINDING, [
            f"❌ no '{MANIFEST_GLOB}' files in {MANIFEST_DIR} — refusing to report "
            "'in sync' against an empty local set"
        ]

    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
    except ImportError:
        return EXIT_CANNOT_EVALUATE, [
            "⊘ boto3 unavailable — cannot evaluate. This is NOT a drift finding."
        ]

    try:
        # F1.5: None means "no override provided" (argparse default); "" means "override
        # provided but empty" — that is a caller error. The truthiness test `if bucket_override:`
        # silently falls through to STS derivation when bucket_override=="", which is exactly
        # the second-source-of-truth outcome decisions.md ruled out.
        if bucket_override is not None and bucket_override == "":
            return EXIT_CANNOT_EVALUATE, [
                "⊘ bucket override was an empty string — cannot evaluate. "
                "This is NOT a drift finding."
            ]
        if bucket_override is not None:
            bucket = bucket_override
        else:
            sts = boto3.client("sts", region_name=region)
            account = sts.get_caller_identity()["Account"]
            bucket = resolve_bucket(stage, region, account)
    except (NoCredentialsError, BotoCoreError, ClientError) as exc:
        return EXIT_CANNOT_EVALUATE, [
            f"⊘ cannot resolve the AWS account ({type(exc).__name__}) — cannot evaluate. "
            "This is NOT a drift finding."
        ]

    # F1.4: wrap S3 client construction in the same exception scope.
    # A ValueError from an empty/malformed region raises out to main() and exits 1 without
    # this guard — the exact inversion the three-code design exists to prevent.
    # Broad ValueError catch is justified here: nothing unexpected raised by boto3.client()
    # at construction time is evidence of drift; it is always an evaluation problem.
    try:
        s3 = boto3.client("s3", region_name=region)
    except (NoCredentialsError, BotoCoreError, ClientError, ValueError) as exc:
        return EXIT_CANNOT_EVALUATE, [
            f"⊘ cannot construct S3 client ({type(exc).__name__}: {exc}) — "
            "cannot evaluate. This is NOT a drift finding."
        ]

    lines.append(f"bucket: s3://{bucket}/manifests/")

    # List the deployed set first, so a one-sided manifest is reported as such rather than
    # surfacing as a confusing per-file 404.
    try:
        remote_names: set[str] = set()
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix="manifests/"):
            for obj in page.get("Contents", []):
                name = obj["Key"].rsplit("/", 1)[-1]
                if name.endswith("-transform.json"):
                    remote_names.add(name)
    except (NoCredentialsError, BotoCoreError, ClientError) as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        return EXIT_CANNOT_EVALUATE, lines + [
            f"⊘ cannot list s3://{bucket}/manifests/ ({code or type(exc).__name__}) — "
            "cannot evaluate. This is NOT a drift finding."
        ]

    failed = False

    only_local = set(local) - remote_names
    only_remote = remote_names - set(local)
    if only_local:
        lines.append(
            "❌ tracked in git but NOT deployed: " + ", ".join(sorted(only_local))
        )
        lines.append("   Flink is still applying the previous mapping. Run `make sync-manifests`.")
        failed = True
    if only_remote:
        lines.append(
            "❌ deployed but NOT tracked in git: " + ", ".join(sorted(only_remote))
        )
        lines.append(
            "   An unreviewed manifest is live. Either commit it or remove it — "
            "`make sync-manifests` runs with --delete and would remove it on the next sync."
        )
        failed = True

    for name in sorted(set(local) & remote_names):
        try:
            body = s3.get_object(Bucket=bucket, Key=f"manifests/{name}")["Body"].read()
        except (NoCredentialsError, BotoCoreError, ClientError) as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            return EXIT_CANNOT_EVALUATE, lines + [
                f"⊘ cannot read manifests/{name} ({code or type(exc).__name__}) — "
                "cannot evaluate. This is NOT a drift finding."
            ]
        g, s = _sha256(local[name]), _sha256(body)
        if g == s:
            lines.append(f"✅ {name} in sync ({g[:16]}…)")
        else:
            lines.append(f"❌ {name} DRIFT — git={g[:16]}… s3={s[:16]}…")
            lines.append(
                f"   {len(local[name])} bytes in git vs {len(body)} in S3. The deployed "
                "copy is what Flink applies; the git copy is what was reviewed."
            )
            failed = True

    return (EXIT_FINDING if failed else EXIT_OK), lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", default=os.environ.get("DEPLOYMENT_STAGE", "staging"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    parser.add_argument(
        "--bucket", default=None,
        help="override the derived bucket name (testing / non-standard deployments)",
    )
    args = parser.parse_args(argv)

    # F1.4: belt-and-suspenders wrapper around verify(). The specific boto3.client
    # ValueError fix above covers the known case; this catches anything else that
    # slips through so an unexpected exception never exits 1 (drift) instead of 2.
    # Does NOT catch SystemExit or KeyboardInterrupt — except Exception excludes both.
    try:
        code, lines = verify(args.stage, args.region, args.bucket)
    except Exception as exc:  # noqa: BLE001
        print(
            f"⊘ unexpected error ({type(exc).__name__}: {exc}) — "
            "cannot evaluate. This is NOT a drift finding."
        )
        return EXIT_CANNOT_EVALUATE

    for line in lines:
        print(line)
    if code == EXIT_CANNOT_EVALUATE:
        print("exit 2 = could not evaluate (not a drift finding)")
    return code


if __name__ == "__main__":
    sys.exit(main())
