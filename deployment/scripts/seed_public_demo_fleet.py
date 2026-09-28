#!/usr/bin/env python3
"""Seed FLEET-DEMO-PUBLIC — the single fleet a self-registered external user can see.

READ THIS BEFORE USING THE VEHICLE HALF OF THIS SCRIPT
-----------------------------------------------------
Vehicles written here are **not IoT-enrolled**, and "Start Agent" will refuse them with
`400 No certificate found for <vehicleId>`. Learned on prod 2026-08-11: rows in the
vehicles table are not the same thing as an enrolled vehicle. Enrollment additionally needs
an IoT certificate, an IoT Thing named by VIN, the `cms-device-policy` attachment, and a row
in `cms-<stage>-storage-vehicle-certificates` — none of which a DynamoDB write produces.
The 2026-06-22 issue `veh-mich-001-missing-iot-cert-campaign` predicted exactly this in its
Prevention section: *"run the full vehicle seeding/enrollment flow for all demo VINs."*

The enrollment path is `POST /api/v1/vehicles` on `main_api` with a body of
`{"entry": {..., "modelManifestName": "<name>"}}`, which mints the cert and Thing.
The caller-supplied cert flag is retired (spec 2026-08-28-cms-cert-follows-model)
— cert issuance is now unconditional and derived from the assigned model.
Two things to know about the API path: it **generates its own** `vehicleId` as
`VEH-{int(time.time())}` and ignores any you supply, so calls less than a second apart
collide and silently overwrite (there is no ConditionExpression); and it does **not** write
`fleet-enrollment` rows, which a fleet-scoped read needs — write those separately.

So for a fleet whose vehicles must actually report telemetry, use this script for the FLEET
record and enroll the vehicles through the API. The vehicle half here remains useful only
where no telemetry is required (rendering non-empty counts on a fresh deploy).

Getting from "enrolled" to actual signals needs one more step still: a RUNNING campaign per
VIN. Without one the agent connects, checks in, and publishes nothing —
`CollectionSchemeManager` logs `[Enabled: Idle: ]`. Copying the live
`cms-fleet-gps-10s-<VIN>` record shape (same `decoderManifestId`, `collectionScheme`,
`signalsToCollect`, `status=RUNNING`, `targetArn=vehicle:<VIN>`) flips it to
`[Enabled: cms-fleet-gps-10s Idle: ]`, after which the API's live overlay reports the
vehicle `connected`. Verified on prod 2026-08-11.

WHY THIS FLEET EXISTS
---------------------
Phase B assigns self-registered users the `fleet-guest` group, which is SCOPED: it reads
only the fleets named in `custom:fleetIds`, and the provisioning trigger sets that to
`EXTERNAL_SELF_SIGNUP_FLEET_IDS` (= `FLEET-DEMO-PUBLIC`). Two consequences:

* Without this fleet, every guest account is correctly fail-closed and sees **nothing** —
  a registration flow that produces useless accounts.
* The synth guard `_require_external_signup_config()` checks that the config *names* a
  fleet, not that the fleet exists. So this gap cannot be caught at synth; it is caught by
  a confused external user, or by this script having been run.

A PURPOSE-BUILT fleet, not an existing one. `issues/2026-08-10-cms-demo-external-exposure/`
found 144 rows carrying a customer brand name in `tenantId`, and the scrub has not run — so
pointing strangers at an existing fleet would walk them into exactly the data that audit
flagged. A dedicated fleet also makes "what a stranger can see" a property of five seeded
records rather than an emergent consequence of fleet membership.

HONEST CONNECTION STATE
-----------------------
Vehicles are seeded `connectionStatus='disconnected'` with **no** `lastSeenAt`, because at
seed time no telemetry has been received and there is no honest "last seen" value to
record. Writing `connected` here would be the exact defect fixed twice already
(`issues/2026-05-28-cms-vehicle-fake-connected-status/`,
`issues/2026-07-31-fake-connected-status-regression/`) and would be caught by the
cross-repo lint `scripts/tests/test_seed_scripts_no_fake_connected.py`. Real state arrives
from the live-state overlay once FWE agents publish — see § REAL TELEMETRY below.

REAL TELEMETRY (a separate step, deliberately)
---------------------------------------------
Seeding rows does not produce telemetry. To make these five vehicles genuinely live, start
one FWE agent per VIN against the simulation service:

    POST {simulation-api}/api/simulation/agent/start   {"vin": "<vin>", "vehicleId": "<id>"}

That runs an ECS task per VIN on the `cms-<stage>-simulation` cluster and will scale the
ASG up if no container instance is available (returning 503 `retryable` while it does). It
is left as an explicit operator action rather than folded in here, because it provisions
compute and therefore cost, and because a data seed that silently starts five containers
is not a data seed.

Idempotent — `ConditionExpression` on every first write; `--force` overwrites. Dry-run is
the DEFAULT and `--apply` is required, which diverges from `seed_generic_fleets.py`
deliberately: that script seeds a fresh deploy, this one targets an environment that is
publicly reachable.

Usage:
  DEPLOYMENT_STAGE=prod AWS_REGION=us-east-1 python3 seed_public_demo_fleet.py
  DEPLOYMENT_STAGE=prod AWS_REGION=us-east-1 python3 seed_public_demo_fleet.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

STAGE = os.environ.get("DEPLOYMENT_STAGE", "prod")
REGION = os.environ.get("AWS_REGION", "us-east-1")

# Must match EXTERNAL_SELF_SIGNUP_FLEET_IDS in config/<stage>.env exactly — the guest
# group's scope is a string comparison, so a rename here silently empties every guest
# account.
FLEET_ID = "FLEET-DEMO-PUBLIC"
VEHICLE_COUNT = 5

NOW = datetime.now(timezone.utc).isoformat()

# Synthetic make/model only. Real-world manufacturer or customer names in a seed payload
# are a publish-scanner canary and, worse, a brand attribution nobody authorised.
#
# Energy state is per-vehicle rather than a shared constant, because a single
# hardcoded value for the whole fleet is how this seed previously wrote
# `fuelLevel: 0` onto three EVs and two hybrids alike. `soc` is state of charge
# for a BEV and tank level for a hybrid — the platform carries both on
# `fuelLevel` (the backend aliases the Redis `ev_soc` signal onto it, and the iOS
# surfaces relabel the row from `fuelType`). `soh` and `kwh` apply to BEVs only.
_MODELS = [
    ("DemoMotors", "Voyager", "sedan", "electric", {"soc": 64, "soh": 96, "kwh": 82}),
    ("DemoMotors", "Voyager", "sedan", "electric", {"soc": 47, "soh": 93, "kwh": 82}),
    ("AcmeAuto", "Carrier", "van", "electric", {"soc": 81, "soh": 97, "kwh": 110}),
    ("AcmeAuto", "Carrier", "van", "hybrid", {"soc": 63}),
    ("DemoMotors", "Trailhead", "suv", "hybrid", {"soc": 55}),
]

_EV_FUEL_TYPES = {"electric", "bev", "ev"}

# Austin, TX — same neutral demo geography as seed_generic_fleets.py's first fleet.
_LAT, _LON = 30.2672, -97.7431


def fleet_item() -> dict:
    return {
        "fleetId": FLEET_ID,
        "name": "Public Demo Fleet",
        "fleetName": "Public Demo Fleet",
        "description": (
            "Read-only demonstration fleet. This is the only fleet visible to "
            "self-registered external users, who receive the scoped fleet-guest role. "
            "All vehicles and telemetry are synthetic."
        ),
        "fleetType": "demo",
        "tenantType": "external",
        "status": "active",
        "operationalCity": "Austin",
        "region": "US-Central",
        "numActiveCampaigns": 0,
        "numTotalCampaigns": 0,
        # snake_case on purpose: _lib/data_source.py reads `data_source`, not `dataSource`.
        # Set explicitly rather than relying on the dual-read default so the routing is
        # stated in the record instead of inferred from its absence.
        "data_source": "vehicle-telemetry",
        "attributes": {
            "primaryUse": "public-demo",
            "isDemoFleet": True,
            "isPubliclyVisible": True,
        },
        "totalVehicles": VEHICLE_COUNT,
        "vehicleCount": VEHICLE_COUNT,
        # Honest: nothing is connected until an agent publishes.
        "connectedVehicles": 0,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


def vehicle_items() -> list[dict]:
    out = []
    for seq in range(1, VEHICLE_COUNT + 1):
        make, model, vtype, fuel, energy = _MODELS[seq - 1]
        vehicle_id = f"VEH-DEMO-PUB-{seq:03d}"
        mileage = 8000 + seq * 1350
        is_ev = fuel in _EV_FUEL_TYPES
        # Combustion-only signals. Omitted entirely for a BEV rather than written
        # as 0: the app cannot distinguish "no engine" from "engine reading zero",
        # so a zero here rendered as "Engine 0°F" on an electric vehicle — the
        # same class of unevidenced claim this module's docstring rejects for
        # `connectionStatus`. A missing optional decodes to an em dash; a zero
        # decodes to a fault.
        engine_fields = {} if is_ev else {"engineTemp": 0, "engineRPM": 0}
        battery_fields = (
            {"batterySoh": energy["soh"], "batteryCapacityKwh": energy["kwh"]}
            if is_ev
            else {}
        )
        out.append(
            {
                "vehicleId": vehicle_id,
                # 4 + 13 = 17 chars, matching seed_generic_fleets.py's synthetic format.
                # Deliberately encodes no real-world WMI.
                "vin": f"DEMO{seq:013d}",
                "fleetId": FLEET_ID,
                "name": f"{make} {model} #{seq:03d}",
                "make": make,
                "model": model,
                "year": 2025,
                "vehicleType": vtype,
                "fuelType": fuel,
                "status": "active",
                # NOT 'connected'. See the module docstring — a seed must never assert
                # live state it has no evidence for.
                "connectionStatus": "disconnected",
                # lastSeenAt is deliberately ABSENT for the same reason.
                "enrollmentStatus": "ACTIVE",
                "color": ["Demo Silver", "Demo Blue", "Demo White"][seq % 3],
                "licensePlate": f"DEMO-{seq:03d}",
                "mileage": mileage,
                "odometer": mileage,
                **engine_fields,
                # State of charge for a BEV, tank level for a hybrid.
                "fuelLevel": energy["soc"],
                **battery_fields,
                "lastSpeed": 0,
                "totalTrips": 0,
                "lastLatitude": round(_LAT + seq * 0.004, 6),
                "lastLongitude": round(_LON - seq * 0.004, 6),
                "attributes": {
                    "fleetType": "demo",
                    "fuelType": fuel,
                    "operationalCity": "Austin",
                    "primaryUse": "public-demo",
                },
                "tenantType": "external",
                "isDemoVehicle": True,
                "vehicleEnvironment": "demo",
                "telemetryTier": "standard",
                # producer: authoritative identity of who built this vehicle.
                # Spec: 2026-09-14-cs-portal-data-model-backend § T0.1.
                # These are CMS-native demo vehicles (fictional makes DemoMotors/AcmeAuto,
                # not enrolled via the OEM1 path or the Meridian pipeline).
                "producer": "cms-native",
                "createdAt": NOW,
                "updatedAt": NOW,
                # Model manifest fields — required by spec 2026-08-28-cms-cert-follows-model § D5.
                # The lint scans any seed*.py that references `storage-vehicles` for
                # `modelManifestName`; this script writes directly to DDB (not via the API)
                # so the fields must be present here to satisfy the lint contract.
                # Note: these placeholder vehicles are NOT IoT-enrolled (see module docstring).
                "modelManifestName":    "CMS-Fleet-Default",  # per spec 2026-08-28-cms-cert-follows-model
                "modelManifestVersion": "1",
                "decoderManifestRef":   "cms-fleet-v3",
                "dataSource":           "vehicle-telemetry",
            }
        )
    return out


def _convert_floats(obj):
    """DynamoDB rejects Python floats — the resource client raises
    `TypeError: Float types are not supported. Use Decimal types instead.`

    Hit on the first real `--apply` against prod: the lat/lon values are floats. Same
    helper as `seed_generic_fleets.py`, which learned this first. Applied inside `_put` so
    no caller can forget it.
    """
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _convert_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_floats(x) for x in obj]
    return obj


def _put(table, item, key_attr: str, force: bool) -> str:
    kwargs = {"Item": _convert_floats(item)}
    if not force:
        kwargs["ConditionExpression"] = f"attribute_not_exists({key_attr})"
    try:
        table.put_item(**kwargs)
        return "written"
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return "exists (skipped)"
        raise


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--force", action="store_true", help="overwrite existing items")
    args = ap.parse_args()

    fleet = fleet_item()
    vehicles = vehicle_items()

    print(f"stage={STAGE} region={REGION} fleet={FLEET_ID} vehicles={len(vehicles)}")
    print(f"  fleet: {fleet['name']!r} data_source={fleet['data_source']}")
    for v in vehicles:
        print(
            f"  {v['vehicleId']}  vin={v['vin']}  {v['make']} {v['model']}"
            f"  connectionStatus={v['connectionStatus']}"
            f"  lastSeenAt={'ABSENT' if 'lastSeenAt' not in v else 'PRESENT(!)'}"
        )

    # Guard against the one mistake that matters in a seed script.
    for v in vehicles:
        assert v["connectionStatus"] != "connected", v["vehicleId"]
        assert "lastSeenAt" not in v, v["vehicleId"]

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        print(
            "After applying, telemetry is still absent: start one FWE agent per VIN via "
            "POST {sim-api}/api/simulation/agent/start — see the module docstring."
        )
        return 0

    ddb = boto3.resource("dynamodb", region_name=REGION)
    fleets = ddb.Table(f"cms-{STAGE}-storage-fleets")
    vehicles_t = ddb.Table(f"cms-{STAGE}-storage-vehicles")
    enrollment = ddb.Table(f"cms-{STAGE}-storage-fleet-enrollment")

    print(f"\nfleet  {FLEET_ID}: {_put(fleets, fleet, 'fleetId', args.force)}")
    for v in vehicles:
        print(f"vehicle {v['vehicleId']}: {_put(vehicles_t, v, 'vehicleId', args.force)}")
        enrollment_item = {
            "PK": f"FLEET#{FLEET_ID}",
            "SK": f"VEHICLE#{v['vehicleId']}",
            "fleetId": FLEET_ID,
            "vehicleId": v["vehicleId"],
            "vin": v["vin"],
            "enrollmentStatus": "ACTIVE",
            "enrolledAt": NOW,
        }
        status = _put(enrollment, enrollment_item, "PK", args.force)
        print(f"   enrollment {v['vehicleId']}: {status}")

    print("\nDONE. Vehicles are seeded DISCONNECTED — that is correct and honest.")
    print("Next: start an FWE agent per VIN to produce real telemetry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
