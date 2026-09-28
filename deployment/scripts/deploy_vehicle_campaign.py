#!/usr/bin/env python3
"""Deploy a FleetWise collection-scheme campaign to a single vehicle.

WHY THIS EXISTS
---------------
A vehicle can be fully provisioned — IoT thing created, ACTIVE certificate,
``enrollmentStatus: ACTIVE``, healthy resident FWE agent — and still collect
**nothing**, because FleetWise Edge only collects CAN signals that an active
campaign (collection scheme) asks for. With no campaign the agent reads the bus
and discards every frame, reporting ``0 documents`` in its checkin.

That is a silent failure: a trip simulation runs, emits CAN frames, reaches
``status: completed``, and materialises zero trips and zero telemetry. Nothing
in the UI, the simulations table, or the vehicle record indicates a problem.
Found 2026-09-01 on VEH-VO-001 / MRDN0000000000012; see
``issues/2026-09-01-trip-simulation-zero-telemetry-no-campaign/``.

Campaign deployment is currently a MANUAL step. The v2 assignment design in
``modules/campaign_manager/campaign_assignment_lambda.py`` is **not deployed**
(no campaign Lambdas exist, and the ``VEHICLE_CAMPAIGNS_TABLE`` it requires was
never created), so nothing provisions a campaign when a vehicle is enrolled.
This script is the stopgap until that gap is closed structurally — see backlog
row ``Cert follows model`` P1, which makes the same argument for certificates:
if a vehicle is created, it should be created working.

THE NAME THAT MATTERS
---------------------
``--vehicle-name`` must be the name the FWE agent **registers under**, which is
the ``VEHICLE_NAME`` env var on its ECS task — NOT the ``thingName`` recorded in
``cms-{stage}-storage-vehicle-certificates``. On VEH-VO-001 those disagree:
the agent registers as ``MRDN0000000000012`` while the cert row still says
``1G1FY6S07N4100001``. Targeting the cert's value produces a campaign that is
never delivered, and nothing reports an error. Read the live value with:

    aws ecs describe-tasks --cluster cms-{stage}-simulation --tasks <taskId> \\
      --query 'tasks[0].overrides.containerOverrides[?name==`fwe-agent`]' \\
      --region <region>

Usage
-----
    # Dry-run (default — writes nothing):
    DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \\
      python3 deploy_vehicle_campaign.py --vehicle-name MRDN0000000000012

    # Apply:
    DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \\
      python3 deploy_vehicle_campaign.py --vehicle-name MRDN0000000000012 --apply

Idempotent: re-running with --apply overwrites the same campaignId with the same
content. Safe to re-run.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import boto3

DEFAULT_TEMPLATE = "cms-fleet-gps-10s"


def _tables(stage: str, region: str, profile: str | None):
    session = boto3.Session(profile_name=profile, region_name=region) if profile \
        else boto3.Session(region_name=region)
    ddb = session.resource("dynamodb")
    return (
        ddb.Table(f"cms-{stage}-campaigns"),
        ddb.Table(f"cms-{stage}-storage-vehicles"),
    )


def _find_template(campaigns, name: str) -> dict:
    """Fetch the template row for a campaign name (targetArn == 'template')."""
    resp = campaigns.scan(
        FilterExpression="campaignName = :n AND targetArn = :t",
        ExpressionAttributeValues={":n": name, ":t": "template"},
    )
    items = resp.get("Items", [])
    if not items:
        raise SystemExit(
            f"ERROR: no template campaign named '{name}' (targetArn='template') in "
            f"{campaigns.name}.\nAvailable templates: "
            + ", ".join(sorted({
                i.get("campaignName", "?")
                for i in campaigns.scan().get("Items", [])
                if i.get("targetArn") == "template"
            }))
        )
    return items[0]


def _find_vehicle(vehicles, vehicle_name: str) -> dict | None:
    """Locate the vehicle record by VIN (the FWE VEHICLE_NAME is the VIN)."""
    resp = vehicles.scan(
        FilterExpression="vin = :v",
        ExpressionAttributeValues={":v": vehicle_name},
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--vehicle-name", required=True,
                   help="The FWE agent's VEHICLE_NAME (the VIN), NOT the cert row's thingName.")
    p.add_argument("--template", default=DEFAULT_TEMPLATE,
                   help=f"Template campaign name to derive from (default: {DEFAULT_TEMPLATE})")
    p.add_argument("--apply", action="store_true",
                   help="Write the campaign. Omit for a dry-run.")
    p.add_argument("--force", action="store_true",
                   help="Proceed even if the vehicle's decoderManifestRef does not match "
                        "the template's decoderManifestId.")
    args = p.parse_args()

    stage = os.environ.get("DEPLOYMENT_STAGE", "staging")
    region = os.environ.get("AWS_REGION", "us-west-2")
    profile = os.environ.get("AWS_PROFILE") or None

    campaigns, vehicles = _tables(stage, region, profile)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] stage={stage} region={region} vehicle={args.vehicle_name}")

    template = _find_template(campaigns, args.template)
    signals = template.get("signalsToCollect", [])
    scheme = template.get("collectionScheme", {})
    decoder = template.get("decoderManifestId")
    print(f"  template        : {args.template} (decoder={decoder}, "
          f"{len(signals)} signals, scheme={scheme})")

    # Guard: the campaign's decoder manifest must match the vehicle's, or the
    # agent cannot decode the signals the scheme asks for.
    vehicle = _find_vehicle(vehicles, args.vehicle_name)
    if vehicle is None:
        print(f"  WARNING: no vehicle row with vin={args.vehicle_name} in {vehicles.name}. "
              f"Cannot verify decoder manifest match.")
        if not args.force:
            print("  Refusing without --force: a campaign for an unknown vehicle is "
                  "almost certainly a wrong --vehicle-name (see module docstring).")
            return 1
    else:
        v_decoder = vehicle.get("decoderManifestRef")
        print(f"  vehicle         : {vehicle.get('vehicleId')} "
              f"(fleet={vehicle.get('fleetId')}, decoder={v_decoder})")
        if v_decoder != decoder and not args.force:
            print(f"  ERROR: decoder mismatch — vehicle has '{v_decoder}', template "
                  f"declares '{decoder}'. The agent could not decode the requested "
                  f"signals. Re-run with --force only if you know this is safe.")
            return 1

    campaign_id = f"{args.template}-{args.vehicle_name}"
    target = f"vehicle:{args.vehicle_name}"
    existing = campaigns.get_item(Key={"campaignId": campaign_id}).get("Item")

    item = {
        "campaignId": campaign_id,
        "campaignName": args.template,
        "category": template.get("category", "telemetry"),
        "collectionScheme": scheme,
        "decoderManifestId": decoder,
        "description": template.get("description", ""),
        "owner": template.get("owner", "oem"),
        "signalsToCollect": signals,
        "signalCount": len(signals),
        "status": "RUNNING",
        "syncStatus": "PENDING",
        "targetArn": target,
        "createdAt": (existing or {}).get(
            "createdAt", datetime.now(timezone.utc).isoformat()
        ),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "source": "deploy_vehicle_campaign.py",
    }

    print(f"  campaignId      : {campaign_id}")
    print(f"  targetArn       : {target}")
    print(f"  status          : RUNNING  (syncStatus=PENDING; the campaign-sync "
          f"Flink app sets HEALTHY once delivered)")
    print(f"  already exists  : {'yes — will overwrite identically' if existing else 'no'}")

    if not args.apply:
        print("\nDRY-RUN — nothing written. Re-run with --apply to deploy.")
        return 0

    campaigns.put_item(Item=item)
    print("\n  wrote campaign row.")

    check = campaigns.get_item(Key={"campaignId": campaign_id}).get("Item")
    if not check or check.get("targetArn") != target or check.get("status") != "RUNNING":
        print("  ERROR: post-write verification failed.")
        return 1
    print(f"  verified: {campaign_id} status={check['status']} target={check['targetArn']}")
    print("\nNext: the campaign-sync Flink app pushes the scheme to the agent over MQTT. "
          "Confirm delivery by watching the agent's checkin document count go above zero:")
    print(f"  aws logs filter-log-events --log-group-name /ecs/cms-{stage}/fwe-agent \\\n"
          f"    --region {region} --filter-pattern 'Checkin data' --limit 5")
    print("A checkin reading 'with 0 documents: []' means the scheme has NOT arrived yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
