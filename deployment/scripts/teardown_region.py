#!/usr/bin/env python3
"""
Tear down all CMS stacks from a given region.
Usage: python3 teardown_region.py --region us-east-2 --stage prod
"""

import argparse
import boto3
import time
import sys


def wait_for_delete(cf, stack_name, timeout=1800):
    """Wait for stack deletion, return True if successful."""
    print(f"  ⏳ Waiting for {stack_name}...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = cf.describe_stacks(StackName=stack_name)
            status = resp["Stacks"][0]["StackStatus"]
            if status == "DELETE_COMPLETE":
                return True
            if status == "DELETE_FAILED":
                reason = resp["Stacks"][0].get("StackStatusReason", "unknown")
                print(f"  ❌ {stack_name} DELETE_FAILED: {reason}")
                return False
        except cf.exceptions.ClientError:
            # Stack no longer exists
            return True
        time.sleep(10)
    print(f"  ❌ {stack_name} timed out after {timeout}s")
    return False


def stop_flink_apps(region, stage):
    """Stop all running Flink apps for this stage."""
    ka = boto3.client("kinesisanalyticsv2", region_name=region)
    apps = ka.list_applications()["ApplicationSummaries"]
    prefix = f"cms-{stage}-flink-"
    running = [a for a in apps if a["ApplicationName"].startswith(prefix) and a["ApplicationStatus"] == "RUNNING"]
    if not running:
        print("  No running Flink apps found.")
        return
    for app in running:
        name = app["ApplicationName"]
        print(f"  Stopping {name}...")
        try:
            ka.stop_application(ApplicationName=name, Force=True)
        except Exception as e:
            print(f"  ⚠️  {name}: {e}")
    print("  Waiting 60s for Flink apps to stop...")
    time.sleep(60)


def empty_s3_buckets(cf, region, stacks):
    """Empty S3 buckets in stacks so CloudFormation can delete them."""
    s3 = boto3.resource("s3", region_name=region)
    for stack_name in stacks:
        try:
            resources = cf.list_stack_resources(StackName=stack_name)["StackResourceSummaries"]
        except Exception:
            continue
        for r in resources:
            if r["ResourceType"] == "AWS::S3::Bucket":
                bucket_name = r.get("PhysicalResourceId")
                if bucket_name:
                    print(f"  Emptying bucket {bucket_name}...")
                    try:
                        bucket = s3.Bucket(bucket_name)
                        bucket.object_versions.delete()
                        bucket.objects.delete()
                    except Exception as e:
                        print(f"  ⚠️  {bucket_name}: {e}")


def delete_tier(cf, stack_names):
    """Delete a tier of stacks in parallel, wait for all."""
    existing = []
    for name in stack_names:
        try:
            cf.describe_stacks(StackName=name)
            existing.append(name)
        except cf.exceptions.ClientError:
            print(f"  {name}: not found, skipping")
    if not existing:
        return True

    for name in existing:
        print(f"  🗑️  Deleting {name}...")
        try:
            cf.delete_stack(StackName=name)
        except Exception as e:
            print(f"  ❌ {name}: {e}")

    return all(wait_for_delete(cf, name) for name in existing)


def main():
    parser = argparse.ArgumentParser(description="Tear down all CMS stacks from a region")
    parser.add_argument("--region", required=True, help="AWS region (e.g. us-east-2)")
    parser.add_argument("--stage", default="prod", help="Deployment stage (default: prod)")
    parser.add_argument("--include-cdk-toolkit", action="store_true", help="Also delete CDKToolkit bootstrap stack")
    parser.add_argument("--dry-run", action="store_true", help="List stacks without deleting")
    args = parser.parse_args()

    region, stage = args.region, args.stage
    cf = boto3.client("cloudformation", region_name=region)
    prefix = f"cms-{stage}-"

    # Deletion order: leaf stacks first, then dependencies
    tiers = [
        [f"{prefix}fleetwise", f"{prefix}flink"],
        [f"{prefix}telemetry-integration"],
        [f"{prefix}ui", f"{prefix}iot", f"{prefix}data-processing"],
        [f"{prefix}storage", f"{prefix}msk"],
        [f"{prefix}infrastructure"],
    ]
    all_stacks = [s for tier in tiers for s in tier]

    if args.dry_run:
        print(f"Stacks that would be deleted in {region}:")
        for name in all_stacks:
            try:
                resp = cf.describe_stacks(StackName=name)
                status = resp["Stacks"][0]["StackStatus"]
                print(f"  {name}: {status}")
            except cf.exceptions.ClientError:
                print(f"  {name}: not found")
        return

    print(f"🚨 Tearing down all cms-{stage} stacks in {region}")
    print(f"   This will PERMANENTLY DELETE all resources.\n")

    confirm = input(f"Type '{region}' to confirm: ")
    if confirm != region:
        print("Aborted.")
        return

    # 1. Stop Flink apps
    print("\n📌 Step 1: Stopping Flink applications...")
    stop_flink_apps(region, stage)

    # 2. Empty S3 buckets
    print("\n📌 Step 2: Emptying S3 buckets...")
    empty_s3_buckets(cf, region, all_stacks)

    # 3. Delete stacks tier by tier
    for i, tier in enumerate(tiers):
        print(f"\n📌 Step {i + 3}: Deleting {', '.join(tier)}...")
        if not delete_tier(cf, tier):
            print("⚠️  Some stacks failed to delete. Check the AWS console.")
            print("   You may need to manually delete stuck resources and retry.")
            sys.exit(1)
        print(f"  ✅ Tier complete")

    # 4. Optionally delete CDKToolkit
    if args.include_cdk_toolkit:
        print("\n📌 Deleting CDKToolkit bootstrap stack...")
        empty_s3_buckets(cf, region, ["CDKToolkit"])
        delete_tier(cf, ["CDKToolkit"])

    print(f"\n✅ All cms-{stage} stacks deleted from {region}")


if __name__ == "__main__":
    main()
