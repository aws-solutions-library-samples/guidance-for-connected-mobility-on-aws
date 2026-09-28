#!/usr/bin/env python3
"""Validate oem1-transform.json against schema and upload to S3 manifest bucket.

Reads AWS_PROFILE, DEPLOYMENT_STAGE, AWS_REGION from env (same pattern as
deployment/scripts/seed_signal_catalog.py).

Bucket name resolved from CloudFormation stack cms-{stage}-data-processing
output key ManifestsBucketName. Falls back to cms-{stage}-storage-manifests
if the stack output is not found.

Usage:
    AWS_PROFILE=default DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \\
        python3 seed_manifest.py
"""
import json
import os
import sys
from pathlib import Path

import boto3
import jsonschema

# ── Configuration ──────────────────────────────────────────────────────────
PROFILE = os.environ.get("AWS_PROFILE", "default")
STAGE = os.environ.get("DEPLOYMENT_STAGE", "staging")
REGION = os.environ.get("AWS_REGION", "us-west-2")

# Paths relative to project root (two levels up from services/connectors/oem1/)
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[2]

MANIFEST_PATH = _PROJECT_ROOT / "services" / "data_processing" / "manifests" / "oem1-transform.json"
SCHEMA_PATH = _PROJECT_ROOT / "services" / "data_processing" / "transform-manifest-schema.json"

CFN_STACK = f"cms-{STAGE}-data-processing"
CFN_OUTPUT_KEY = "ManifestsBucketName"
FALLBACK_BUCKET = f"cms-{STAGE}-storage-manifests"
S3_KEY = "oem1-transform.json"


def _resolve_bucket(cf_client) -> str:
    """Resolve the manifests S3 bucket name from CloudFormation outputs."""
    try:
        resp = cf_client.describe_stacks(StackName=CFN_STACK)
        outputs = resp["Stacks"][0].get("Outputs", [])
        for o in outputs:
            if o["OutputKey"] == CFN_OUTPUT_KEY:
                return o["OutputValue"]
    except Exception as exc:
        print(f"⚠️  Could not resolve bucket from CloudFormation ({exc}); using fallback.")
    return FALLBACK_BUCKET


def main() -> None:
    # Load manifest
    if not MANIFEST_PATH.exists():
        print(f"❌ Manifest not found: {MANIFEST_PATH}")
        sys.exit(1)
    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)

    # Load schema and validate
    if not SCHEMA_PATH.exists():
        print(f"❌ Schema not found: {SCHEMA_PATH}")
        sys.exit(1)
    with open(SCHEMA_PATH) as f:
        schema = json.load(f)

    print(f"Validating {MANIFEST_PATH.name} against {SCHEMA_PATH.name}...")
    jsonschema.validate(instance=manifest, schema=schema)
    print("✅ Manifest valid")

    # Resolve bucket and upload
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    cf = session.client("cloudformation")
    s3 = session.client("s3")

    bucket = _resolve_bucket(cf)
    print(f"Uploading to s3://{bucket}/{S3_KEY}...")
    with open(MANIFEST_PATH, "rb") as f:
        s3.put_object(
            Bucket=bucket,
            Key=S3_KEY,
            Body=f.read(),
            ContentType="application/json",
        )
    print(f"✅ Uploaded s3://{bucket}/{S3_KEY}")


def _build_parser() -> "argparse.ArgumentParser":
    import argparse
    p = argparse.ArgumentParser(
        description=(
            "Validate services/data_processing/manifests/oem1-transform.json "
            "against transform-manifest-schema.json and upload to the "
            "cms-{stage} manifests S3 bucket."
        ),
    )
    return p


if __name__ == "__main__":
    _build_parser().parse_args()
    main()
