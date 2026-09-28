#!/usr/bin/env python3
"""Seed realistic driver records for fleet operations.

Vehicle-aware mode (default when staging vehicles table is non-empty):
    Reads `cms-{stage}-storage-vehicles` and draws `assignedVehicleId`
    from real vehicle IDs without replacement, preserving the
    one-driver-per-vehicle invariant.

Synthetic mode (fallback for dev/empty staging table):
    Uses the legacy `[VEH-0001 ... VEH-0050]` synthesized pool when
    the real vehicles table is empty or has fewer rows than needed.

Determinism:
    Set `RANDOM_SEED` (default = a per-stage hash so dev/staging/prod
    don't produce the same driver IDs) for reproducible writes. The
    drivers table primary key is `driverId` so re-running with the
    same seed and same vehicles table is idempotent.

Driver-count scaling (Decision 2 Option A in the
2026-05-29-staging-drivers-simulator-cognito-parity spec):
    When reading from a non-empty real vehicles table, default
    `NUM_DRIVERS = vehicle_count + ceil(0.20 * vehicle_count)` (active
    + 20% bench). Operator can override via `NUM_DRIVERS` env var.

Usage:
    DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \\
        python3 deployment/scripts/seed_drivers.py --dry-run

    DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \\
        python3 deployment/scripts/seed_drivers.py

Environment:
    AWS_REGION                — defaults to us-west-2
    DEPLOYMENT_STAGE          — defaults to prod
    NUM_DRIVERS               — override the auto-computed driver count
    VEHICLE_POOL_SIZE         — synthetic-mode fallback pool size (legacy, default 50)
    DRIVER_UNASSIGNED_SHARE   — fraction of drivers without `assignedVehicleId` (default 0.20)
    RANDOM_SEED               — deterministic seed; default = hash("seed_drivers:" + STAGE)
"""
import argparse
import boto3
import hashlib
import json
import math
import os
import random
import sys
from datetime import datetime, timedelta
from decimal import Decimal

REGION = os.environ.get("AWS_REGION", "us-west-2")
STAGE = os.environ.get("DEPLOYMENT_STAGE", "prod")
TABLE = f"cms-{STAGE}-storage-drivers"
VEHICLES_TABLE = f"cms-{STAGE}-storage-vehicles"

FIRST_NAMES = ["James","Maria","Robert","Linda","Michael","Sarah","David","Jennifer","Carlos","Emily",
               "William","Jessica","Daniel","Ashley","Jose","Amanda","Kevin","Stephanie","Brian","Nicole",
               "Marcus","Rachel","Anthony","Michelle","Thomas","Laura","Christopher","Angela","Jason","Megan",
               "Ryan","Brittany","Eric","Samantha","Tyler","Rebecca","Brandon","Katherine","Aaron","Heather",
               "Derek","Christina","Travis","Amber","Cody","Tiffany","Shane","Crystal","Dustin","Vanessa"]
LAST_NAMES = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez","Martinez",
              "Anderson","Taylor","Thomas","Moore","Jackson","Martin","Lee","Thompson","White","Harris",
              "Clark","Lewis","Robinson","Walker","Young","Allen","King","Wright","Scott","Torres",
              "Hill","Green","Adams","Baker","Nelson","Carter","Mitchell","Perez","Roberts","Turner"]
LICENSE_CLASSES = [("CDL-A", 0.3), ("CDL-B", 0.25), ("Class C", 0.35), ("Class D", 0.1)]
CERTIFICATIONS = ["HAZMAT","Tanker","Doubles/Triples","Passenger","School Bus","Air Brake"]


def _resolve_seed() -> int:
    """Return the deterministic seed for this run.

    Defaults to a per-stage hash so dev/staging/prod don't collide. Operator
    can override via the `RANDOM_SEED` env var. Stays an int so
    `random.Random(seed)` is reproducible across Python versions.
    """
    explicit = os.environ.get("RANDOM_SEED")
    if explicit is not None and explicit != "":
        try:
            return int(explicit)
        except ValueError:
            # Non-numeric seed — hash it.
            return int(hashlib.sha256(explicit.encode("utf-8")).hexdigest()[:16], 16)
    h = hashlib.sha256(f"seed_drivers:{STAGE}".encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def _scan_real_vehicle_ids(ddb_resource) -> list:
    """Return all vehicleId values from `cms-{stage}-storage-vehicles`.

    Empty list if the table is empty or unreachable. Caller decides
    whether to fall back to the synthesized pool.
    """
    try:
        table = ddb_resource.Table(VEHICLES_TABLE)
        ids = []
        kwargs = {"ProjectionExpression": "vehicleId"}
        while True:
            resp = table.scan(**kwargs)
            for item in resp.get("Items", []):
                vid = item.get("vehicleId")
                if vid:
                    ids.append(vid)
            token = resp.get("LastEvaluatedKey")
            if not token:
                break
            kwargs["ExclusiveStartKey"] = token
        return ids
    except Exception as e:
        print(f"⚠️  Could not scan {VEHICLES_TABLE}: {e}", file=sys.stderr)
        return []


def _build_vehicle_pool(rng: random.Random, real_vehicle_ids: list,
                       num_drivers: int, num_unassigned: int) -> tuple:
    """Return (vehicle_pool, mode_label).

    Vehicle-aware mode: shuffle real vehicleIds, take the first
    (num_drivers - num_unassigned) so each active driver gets a unique
    real vehicle (no replacement).

    Synthetic fallback: if real_vehicle_ids has fewer rows than needed,
    fall back to the legacy synthesized `VEH-NNNN` pool. This preserves
    backward compatibility for dev environments with empty vehicles
    tables.
    """
    needed = num_drivers - num_unassigned
    if len(real_vehicle_ids) >= needed:
        pool = list(real_vehicle_ids)
        rng.shuffle(pool)
        # Take exactly `needed` so the pop loop below doesn't over-draw.
        return pool[:needed], "vehicle-aware"

    # Synthetic fallback
    pool_size = int(os.environ.get("VEHICLE_POOL_SIZE", "50"))
    pool = [f"VEH-{i:04d}" for i in range(1, pool_size + 1)]
    rng.shuffle(pool)
    print(
        f"⚠️  Real vehicles table has {len(real_vehicle_ids)} rows, need {needed}. "
        f"Falling back to synthesized VEH-NNNN pool (size={pool_size}).",
        file=sys.stderr,
    )
    return pool, "synthetic-fallback"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the planned writes without touching DynamoDB")
    args = parser.parse_args()

    seed = _resolve_seed()
    rng = random.Random(seed)

    ddb = boto3.resource("dynamodb", region_name=REGION)

    # Discover real vehicles for vehicle-aware assignment
    real_vehicle_ids = _scan_real_vehicle_ids(ddb)

    # Compute NUM_DRIVERS: respect explicit override; otherwise scale per
    # Decision 2 Option A (active + 20% bench) when real vehicles exist;
    # else fall back to 75 for dev/synthetic environments.
    explicit_num = os.environ.get("NUM_DRIVERS")
    if explicit_num is not None and explicit_num != "":
        num_drivers = int(explicit_num)
        num_drivers_source = "NUM_DRIVERS env var"
    elif real_vehicle_ids:
        # Active = vehicle_count, bench = ceil(0.20 * vehicle_count)
        vehicle_count = len(real_vehicle_ids)
        bench = max(1, math.ceil(0.20 * vehicle_count))
        num_drivers = vehicle_count + bench
        num_drivers_source = (
            f"auto-computed: {vehicle_count} active + {bench} bench"
        )
    else:
        num_drivers = 75
        num_drivers_source = "legacy default (no real vehicles found)"

    unassigned_share = float(os.environ.get("DRIVER_UNASSIGNED_SHARE", "0.2"))
    num_unassigned = int(num_drivers * unassigned_share)
    assignable = num_drivers - num_unassigned

    vehicle_pool, mode_label = _build_vehicle_pool(
        rng, real_vehicle_ids, num_drivers, num_unassigned
    )

    print(f"Driver seed plan ({'DRY-RUN' if args.dry_run else 'LIVE'})")
    print(f"  region            = {REGION}")
    print(f"  stage             = {STAGE}")
    print(f"  drivers table     = {TABLE}")
    print(f"  vehicles table    = {VEHICLES_TABLE} (count={len(real_vehicle_ids)})")
    print(f"  NUM_DRIVERS       = {num_drivers}  ({num_drivers_source})")
    print(f"  num_unassigned    = {num_unassigned}  (share={unassigned_share})")
    print(f"  assignable active = {assignable}")
    print(f"  assignment mode   = {mode_label}")
    print(f"  random seed       = {seed}")
    print("=" * 72)

    now = datetime.utcnow()
    drivers = []
    for i in range(num_drivers):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        hire_date = now - timedelta(days=rng.randint(180, 2500))
        license_class = rng.choices(
            [c[0] for c in LICENSE_CLASSES], [c[1] for c in LICENSE_CLASSES]
        )[0]
        certs = rng.sample(CERTIFICATIONS, k=rng.randint(0, 3)) if "CDL" in license_class else []
        years_exp = rng.randint(1, 25)
        base_score = rng.gauss(82, 10)

        # Draw vehicle without replacement: only the first `assignable`
        # drivers get vehicles. Bench drivers (the last `num_unassigned`)
        # get no `assignedVehicleId`. When the pool runs out (e.g. real
        # vehicles count below `assignable`), leave it None too — the
        # _build_vehicle_pool fallback handles the more typical case.
        assigned_vehicle = None
        if i < assignable and vehicle_pool:
            assigned_vehicle = vehicle_pool.pop()

        driver = {
            "driverId": f"DRV-{i+1:04d}",
            "firstName": first,
            "lastName": last,
            "email": f"{first.lower()}.{last.lower()}@example.com",
            "phone": f"555-{rng.randint(1000,9999)}",
            "status": rng.choices(["active","active","active","on_leave","terminated"], [50,30,10,7,3])[0],
            "hireDate": hire_date.strftime("%Y-%m-%d"),
            "yearsExperience": years_exp,
            "licenseNumber": f"DL-{license_class.replace(' ','')}-{rng.randint(100000,999999)}",
            "licenseClass": license_class,
            "licenseState": rng.choice(["TX","CA","FL","NY","IL","OH","GA","NC","MI","AZ","NV","WA","OR","CO"]),
            "licenseExpiry": (now + timedelta(days=rng.randint(30, 1200))).strftime("%Y-%m-%d"),
            "certifications": certs,
            "safetyScore": Decimal(str(round(max(40, min(100, base_score)), 1))),
            "totalTrips": rng.randint(50, 5000),
            "totalMiles": rng.randint(5000, 500000),
            "incidentCount": rng.randint(0, 8),
            "lastTripDate": (now - timedelta(days=rng.randint(0, 30))).strftime("%Y-%m-%d"),
            "homeBase": rng.choice(["Dallas","Houston","Phoenix","Atlanta","Chicago","Miami","Denver","Seattle","Portland","Las Vegas"]),
            "assignedVehicleId": assigned_vehicle,
            "createdAt": hire_date.isoformat(),
            "updatedAt": now.isoformat(),
        }
        # Remove None values
        driver = {k: v for k, v in driver.items() if v is not None}
        drivers.append(driver)

    # Stats
    active = sum(1 for d in drivers if d["status"] == "active")
    cdl = sum(1 for d in drivers if "CDL" in d.get("licenseClass",""))
    assigned = sum(1 for d in drivers if d.get("assignedVehicleId"))
    if drivers:
        avg_safety = sum(float(d["safetyScore"]) for d in drivers) / len(drivers)
    else:
        avg_safety = 0.0

    if args.dry_run:
        print("Planned writes (dry run; no DynamoDB calls):")
        for d in drivers[:10]:
            v = d.get("assignedVehicleId", "—")
            print(f"  + {d['driverId']:10s}  status={d['status']:11s}  vehicle={v}")
        if len(drivers) > 10:
            print(f"  ... and {len(drivers) - 10} more")
        print("=" * 72)
    else:
        print(f"Seeding {len(drivers)} drivers to {TABLE} in {REGION}...")
        table = ddb.Table(TABLE)
        with table.batch_writer() as batch:
            for d in drivers:
                batch.put_item(Item=d)
        print(f"✅ {len(drivers)} drivers seeded")

    print(
        f"   Active: {active}, CDL holders: {cdl}, "
        f"Assigned to vehicle: {assigned}, Avg safety score: {avg_safety:.1f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
