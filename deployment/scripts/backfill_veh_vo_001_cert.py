#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""One-off idempotent backfill for a single named vehicle row.

Assigns the CMS-Fleet-Default model manifest to the vehicle, mints an
IoT certificate + Thing, attaches the shared CMS-Vehicle-IoT-Policy, and
updates the vehicle row in DynamoDB.

Spec: `.kiro/specs/2026-08-28-cms-cert-follows-model/spec.md` § D3

Usage (dry-run, prints plan only):
    python3 backfill_veh_vo_001_cert.py --vin <VIN> --vehicle-id <VEHICLE_ID>

Usage (apply for real):
    python3 backfill_veh_vo_001_cert.py --vin <VIN> --vehicle-id <VEHICLE_ID> --apply

The VIN and vehicle-id are required at runtime. They are NOT hardcoded in
this script so the file ships to the public mirror without embedding any
customer data. See the operator runbook and the spec dir for the target values.

Environment variables (all optional, mirroring seed_model_manifests.py):
    AWS_REGION          — default us-west-2
    AWS_PROFILE         — default default
    DEPLOYMENT_STAGE    — default staging
"""

import argparse
import json
import os
import sys

import boto3
from botocore.exceptions import ClientError

# ── Configuration ──────────────────────────────────────────────────────────
_DEFAULT_REGION = "us-west-2"
_DEFAULT_PROFILE = "default"
_DEFAULT_STAGE = "staging"
_SHARED_POLICY_NAME = "CMS-Vehicle-IoT-Policy"

# Verbatim copy of shared_policy_document from
# modules/cms_ui/source/handlers/main_api/index.py lines 1701-1740.
# MUST match production byte-for-byte to avoid triggering the 5-LRU
# replacement path when the API runs later.
_SHARED_POLICY_DOCUMENT = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": ["iot:Connect"],
            "Resource": ["arn:aws:iot:*:*:client/*"]
        },
        {
            "Effect": "Allow",
            "Action": ["iot:Publish"],
            "Resource": [
                "arn:aws:iot:*:*:topic/$aws/rules/cms_dev_iot_msk_rule/*",
                "arn:aws:iot:*:*:topic/$aws/rules/cms_staging_iot_msk_rule/*",
                "arn:aws:iot:*:*:topic/$aws/rules/cms_prod_iot_msk_rule/*",
                # Connected Services product delivery path (spec
                # 2026-09-12-cs-simulator-oem2-manifest-path). See the identical
                # block in main_api/index.py — this file MUST stay byte-for-byte
                # equal to it, because _ensure_shared_policy below compares the
                # live policy against this constant and republishes from it on any
                # difference. Editing one copy alone silently reverts the other.
                "arn:aws:iot:*:*:topic/$aws/rules/cms_dev_cs_product_meridian_ev_rule/*",
                "arn:aws:iot:*:*:topic/$aws/rules/cms_staging_cs_product_meridian_ev_rule/*",
                "arn:aws:iot:*:*:topic/$aws/rules/cms_prod_cs_product_meridian_ev_rule/*",
                "arn:aws:iot:*:*:topic/cms/*",
                "arn:aws:iot:*:*:topic/fleet/*",
            ]
        },
        {
            "Effect": "Allow",
            "Action": ["iot:Subscribe"],
            "Resource": [
                "arn:aws:iot:*:*:topicfilter/cms/*",
                "arn:aws:iot:*:*:topicfilter/fleet/*",
            ]
        },
        {
            "Effect": "Allow",
            "Action": ["iot:Receive"],
            "Resource": [
                "arn:aws:iot:*:*:topic/cms/*",
                "arn:aws:iot:*:*:topic/fleet/*",
            ]
        }
    ]
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _load_model_manifest(ddb_resource, stage, model_name):
    """Return the highest ACTIVE version of model_name, or None.

    The model-manifest table has pk=MODEL#{name}#{version} and sk=MODEL#{name}.
    There is no GSI on sk, so we use a scan with a FilterExpression on the sk
    attribute to find all versions of a given model name.
    """
    table = ddb_resource.Table(f"cms-{stage}-model-manifest")
    resp = table.scan(
        FilterExpression="sk = :sk",
        ExpressionAttributeValues={":sk": f"MODEL#{model_name}"},
    )
    items = [
        i for i in resp.get("Items", [])
        if i.get("status") == "ACTIVE"
    ]
    if not items:
        return None
    # Pick highest version (version is a numeric string like "1", "2", ...)
    items.sort(key=lambda i: int(i.get("modelManifestVersion", "0")), reverse=True)
    return items[0]


def _get_vehicle_row(ddb_resource, stage, vehicle_id):
    """Return the vehicle row dict, or None if not found."""
    table = ddb_resource.Table(f"cms-{stage}-storage-vehicles")
    resp = table.get_item(Key={"vehicleId": vehicle_id})
    return resp.get("Item")


def _iot_thing_exists(iot_client, thing_name):
    """Return True if the IoT thing exists."""
    try:
        iot_client.describe_thing(thingName=thing_name)
        return True
    except iot_client.exceptions.ResourceNotFoundException:
        return False
    except ClientError:
        return False


def _ensure_shared_policy(iot_client, dry_run):
    """Create or update CMS-Vehicle-IoT-Policy with 5-LRU version management."""
    desired_doc_str = json.dumps(_SHARED_POLICY_DOCUMENT, sort_keys=True, separators=(",", ":"))
    if dry_run:
        print(f"  [DRY-RUN] Would create/verify IoT policy: {_SHARED_POLICY_NAME}")
        return

    try:
        iot_client.create_policy(
            policyName=_SHARED_POLICY_NAME,
            policyDocument=json.dumps(_SHARED_POLICY_DOCUMENT),
        )
        print(f"  ✅ Created shared IoT policy: {_SHARED_POLICY_NAME}")
    except iot_client.exceptions.ResourceAlreadyExistsException:
        try:
            live = iot_client.get_policy(policyName=_SHARED_POLICY_NAME)
            live_doc_str = json.dumps(
                json.loads(live["policyDocument"]), sort_keys=True, separators=(",", ":")
            )
        except Exception as exc:
            print(f"  ⚠️  Could not read live policy for diff: {exc}")
            live_doc_str = ""

        if live_doc_str == desired_doc_str:
            print(f"  ✅ Shared IoT policy already up-to-date: {_SHARED_POLICY_NAME}")
        else:
            print(f"  🔄 Policy drifted; publishing new version: {_SHARED_POLICY_NAME}")
            try:
                vers = iot_client.list_policy_versions(policyName=_SHARED_POLICY_NAME).get(
                    "policyVersions", []
                )
                non_default = sorted(
                    [v for v in vers if not v.get("isDefaultVersion")],
                    key=lambda v: v.get("createDate"),
                )
                while len(vers) >= 5 and non_default:
                    oldest = non_default.pop(0)
                    iot_client.delete_policy_version(
                        policyName=_SHARED_POLICY_NAME,
                        policyVersionId=oldest["versionId"],
                    )
                    vers = [v for v in vers if v["versionId"] != oldest["versionId"]]
                iot_client.create_policy_version(
                    policyName=_SHARED_POLICY_NAME,
                    policyDocument=json.dumps(_SHARED_POLICY_DOCUMENT),
                    setAsDefault=True,
                )
                print(f"  ✅ New default policy version published")
            except Exception as vers_error:
                print(f"  ⚠️  Failed to publish new policy version: {vers_error}")


def _issue_certificate(iot_client, ddb_resource, stage, vehicle_id, vin, dry_run):
    """Mint an IoT cert + Thing, attach policy, write cert row. Returns cert_id."""
    if dry_run:
        print(f"  [DRY-RUN] Would mint IoT cert for thing: {vin}")
        print(f"  [DRY-RUN] Would write cert row to cms-{stage}-storage-vehicle-certificates")
        return "<dry-run-cert-id>"

    # 1. Create certificate
    cert_response = iot_client.create_keys_and_certificate(setAsActive=True)
    cert_id = cert_response["certificateId"]
    cert_arn = cert_response["certificateArn"]
    print(f"  ✅ Created certificate: {cert_id}")

    # 2. Create IoT Thing (swallow AlreadyExists)
    try:
        iot_client.create_thing(thingName=vin)
        print(f"  ✅ Created IoT Thing: {vin}")
    except iot_client.exceptions.ResourceAlreadyExistsException:
        print(f"  ℹ️  IoT Thing already exists: {vin}")

    # 3. Ensure shared policy exists / is up-to-date
    _ensure_shared_policy(iot_client, dry_run=False)

    # 4. Attach certificate to Thing
    iot_client.attach_thing_principal(thingName=vin, principal=cert_arn)
    print(f"  ✅ Attached certificate to thing: {vin}")

    # 5. Attach policy to certificate
    iot_client.attach_principal_policy(policyName=_SHARED_POLICY_NAME, principal=cert_arn)
    print(f"  ✅ Attached policy to certificate: {_SHARED_POLICY_NAME}")

    # 6. Write certificate row — same shape as index.py:1832-1850
    from datetime import datetime, timezone
    cert_item = {
        "vin": vin,
        "vehicleId": vehicle_id,
        "certificateId": cert_id,
        "certificateArn": cert_arn,
        "certificatePem": cert_response["certificatePem"],
        "publicKey": cert_response["keyPair"]["PublicKey"],
        "privateKey": cert_response["keyPair"]["PrivateKey"],
        "thingName": vin,
        "policyName": _SHARED_POLICY_NAME,
        "status": "ACTIVE",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    cert_table = ddb_resource.Table(f"cms-{stage}-storage-vehicle-certificates")
    cert_table.put_item(Item=cert_item)
    print(f"  ✅ Certificate row written for vin: {vin}")

    return cert_id


def _update_vehicle_row(ddb_resource, stage, vehicle_id, model, cert_id, dry_run):
    """UpdateItem to add model + cert fields without overwriting existing fields."""
    update_parts = [
        "modelManifestName = :mmn",
        "modelManifestVersion = :mmv",
        "decoderManifestRef = :dmr",
        "dataSource = :ds",
        "hasCertificate = :hc",
        "certificateId = :cid",
    ]
    expr_values = {
        ":mmn": model["modelManifestName"],
        ":mmv": model["modelManifestVersion"],
        ":dmr": model["decoderManifestRef"],
        ":ds": "vehicle-telemetry",
        ":hc": True,
        ":cid": cert_id,
    }
    ecu_config_id = model.get("ecuConfigId")
    if ecu_config_id:
        update_parts.append("ecuConfigId = :eci")
        expr_values[":eci"] = ecu_config_id

    update_expression = "SET " + ", ".join(update_parts)

    if dry_run:
        print(f"  [DRY-RUN] Would UpdateItem on cms-{stage}-storage-vehicles:")
        print(f"    Key: {{vehicleId: {vehicle_id}}}")
        print(f"    UpdateExpression: {update_expression}")
        print(f"    Values: {json.dumps(expr_values, default=str, indent=6)}")
        return

    table = ddb_resource.Table(f"cms-{stage}-storage-vehicles")
    table.update_item(
        Key={"vehicleId": vehicle_id},
        UpdateExpression=update_expression,
        ExpressionAttributeValues=expr_values,
    )
    print(f"  ✅ Vehicle row updated: {vehicle_id}")


def _verify(ddb_resource, iot_client, stage, vehicle_id, vin):
    """Post-write verification. Returns True on success."""
    ok = True

    # 1. Re-read vehicle row
    vehicle = _get_vehicle_row(ddb_resource, stage, vehicle_id)
    if not vehicle:
        print(f"  ❌ VERIFY FAIL: vehicle row not found after write: {vehicle_id}")
        ok = False
    elif not vehicle.get("certificateId"):
        print(f"  ❌ VERIFY FAIL: vehicle row missing certificateId after write")
        ok = False
    else:
        print(f"  ✅ Vehicle row re-read OK: certificateId={vehicle.get('certificateId')}")

    # 2. Fetch cert row
    # NOTE: cert-table partition key is `vehicleId`, NOT `vin` — verified against
    # `describe-table` and `deployment/stacks/storage_stack.py`'s VehicleCertificatesTable
    # (partition_key=Attribute(name="vehicleId")). An earlier iteration keyed on
    # `vin` here, which raised ValidationException *after* every write completed
    # — the script exited non-zero on the verify step while all mutations landed.
    # Backlog row `Backfill verify key` P3 (2026-08-29).
    cert_table = ddb_resource.Table(f"cms-{stage}-storage-vehicle-certificates")
    cert_resp = cert_table.get_item(Key={"vehicleId": vehicle_id})
    cert_row = cert_resp.get("Item")
    if not cert_row:
        print(f"  ❌ VERIFY FAIL: certificate row not found for vehicleId: {vehicle_id}")
        ok = False
    else:
        print(f"  ✅ Certificate row found for vehicleId: {vehicle_id} (vin={cert_row.get('vin')})")

    # 3. Describe IoT Thing
    try:
        iot_client.describe_thing(thingName=vin)
        print(f"  ✅ IoT Thing exists: {vin}")
    except iot_client.exceptions.ResourceNotFoundException:
        print(f"  ❌ VERIFY FAIL: IoT Thing not found: {vin}")
        ok = False
    except ClientError as exc:
        print(f"  ❌ VERIFY FAIL: IoT describe_thing error: {exc}")
        ok = False

    return ok


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=(
            "One-off idempotent backfill: assign model manifest + mint IoT cert "
            "for a single named vehicle row. "
            "Spec: .kiro/specs/2026-08-28-cms-cert-follows-model/spec.md § D3"
        )
    )
    parser.add_argument(
        "--vin",
        required=True,
        help="VIN of the target vehicle (used as the IoT Thing name).",
    )
    parser.add_argument(
        "--vehicle-id",
        required=True,
        dest="vehicle_id",
        help="DynamoDB vehicleId (primary key) of the target vehicle row.",
    )
    parser.add_argument(
        "--model-manifest-name",
        default="CMS-Fleet-Default",
        dest="model_manifest_name",
        help="Model manifest name to assign (default: CMS-Fleet-Default).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write to DynamoDB and IoT. Without this flag the script is a dry-run.",
    )

    args = parser.parse_args()

    region = os.environ.get("AWS_REGION", _DEFAULT_REGION)
    profile = os.environ.get("AWS_PROFILE", _DEFAULT_PROFILE)
    stage = os.environ.get("DEPLOYMENT_STAGE", _DEFAULT_STAGE)
    dry_run = not args.apply

    print(f"{'[DRY-RUN] ' if dry_run else ''}backfill_veh_vo_001_cert.py")
    print(f"  stage={stage}  region={region}  profile={profile}")
    print(f"  vehicle_id={args.vehicle_id}  vin={args.vin}")
    print(f"  model_manifest_name={args.model_manifest_name}")
    print()

    session = boto3.Session(profile_name=profile, region_name=region)
    ddb_resource = session.resource("dynamodb")
    iot_client = session.client("iot")

    # ── Step 1: Read vehicle row ──────────────────────────────────────────
    print("Step 1: reading vehicle row...")
    vehicle = _get_vehicle_row(ddb_resource, stage, args.vehicle_id)
    if vehicle is None:
        print(f"  ❌ Vehicle row not found: vehicleId={args.vehicle_id}")
        sys.exit(1)
    print(f"  ℹ️  Current modelManifestName: {vehicle.get('modelManifestName')!r}")
    print(f"  ℹ️  Current certificateId:    {vehicle.get('certificateId')!r}")

    # ── Step 2: Idempotency checks ────────────────────────────────────────
    if vehicle.get("modelManifestName"):
        print(
            f"\n✅ Vehicle already has modelManifestName="
            f"{vehicle.get('modelManifestName')!r}. Nothing to do."
        )
        sys.exit(0)

    if vehicle.get("certificateId") and _iot_thing_exists(iot_client, args.vin):
        print(
            f"\n✅ Vehicle already has certificateId={vehicle.get('certificateId')!r} "
            f"and IoT Thing {args.vin!r} exists. Already backfilled."
        )
        sys.exit(0)

    # ── Step 3: Load model manifest ───────────────────────────────────────
    print(f"\nStep 2: loading model manifest {args.model_manifest_name!r}...")
    model = _load_model_manifest(ddb_resource, stage, args.model_manifest_name)
    if model is None:
        print(
            f"  ❌ Model manifest {args.model_manifest_name!r} not found or not ACTIVE "
            f"in cms-{stage}-model-manifest.\n"
            f"  Run: python3 deployment/scripts/seed_model_manifests.py first."
        )
        sys.exit(1)
    if not model.get("decoderManifestRef"):
        print(
            f"  ❌ Model {args.model_manifest_name!r} has no decoderManifestRef. "
            f"Cannot assign to a vehicle-telemetry vehicle."
        )
        sys.exit(1)
    print(f"  ✅ Model: name={model['modelManifestName']} version={model['modelManifestVersion']} "
          f"decoder={model['decoderManifestRef']}")

    # ── Step 4: Print dry-run plan ─────────────────────────────────────────
    if dry_run:
        print(f"\nStep 3: [DRY-RUN] plan (pass --apply to execute):")
        _ensure_shared_policy(iot_client, dry_run=True)
        _issue_certificate(iot_client, ddb_resource, stage, args.vehicle_id, args.vin, dry_run=True)
        _update_vehicle_row(ddb_resource, stage, args.vehicle_id, model, "<cert-id>", dry_run=True)
        print("\n✅ Dry-run complete. Pass --apply to write changes.")
        sys.exit(0)

    # ── Step 5: Apply ─────────────────────────────────────────────────────
    print(f"\nStep 3: issuing certificate...")
    cert_id = _issue_certificate(
        iot_client, ddb_resource, stage, args.vehicle_id, args.vin, dry_run=False
    )

    print(f"\nStep 4: updating vehicle row...")
    _update_vehicle_row(ddb_resource, stage, args.vehicle_id, model, cert_id, dry_run=False)

    # ── Step 6: Post-write verification ───────────────────────────────────
    print(f"\nStep 5: verifying writes...")
    ok = _verify(ddb_resource, iot_client, stage, args.vehicle_id, args.vin)
    if not ok:
        print("\n❌ Verification failed. Check the errors above.")
        sys.exit(1)

    print(f"\n✅ Backfill complete for vehicleId={args.vehicle_id}, vin={args.vin}")
    sys.exit(0)


if __name__ == "__main__":
    main()
