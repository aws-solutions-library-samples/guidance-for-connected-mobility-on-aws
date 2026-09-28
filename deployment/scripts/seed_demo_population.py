#!/usr/bin/env python3
"""
Seed the CS portal demo population.

WHAT THIS SCRIPT DOES
---------------------
1. Adds ``telemetryCapability`` to every Meridian model manifest.
   - 6 non-Mistral lines → ["onboard-fwe"]   (Windrose, Trailwind, Crestwind, Zephyr, Azimuth, Sirocco)
   - MERIDIAN-MISTRAL      → ["cloud-to-cloud"]
   The capability is stated on the MODEL, never inferred from ``powertrain``
   (spec § "Demo population + telemetry capability", constraint: "Do NOT infer").

2. Resolves the 4 year-mismatches found in live staging data (spec requirement):
   - VEH-DEMO-PUB-001: Crestwind year 2025 → 2023  (manifest years: [2022,2023,2026])
   - VEH-DEMO-PUB-002: Crestwind year 2025 → 2023
   - VEH-MRDN-0001:    Windrose  year 2023 → 2022  (manifest years: [2022,2024,2026])
   - VEH-MRDN-0011:    Windrose  year 2023 → 2024

3. Reclassifies the 21 existing Meridian vehicles from cloud-telemetry to
   vehicle-telemetry (onboard). These are Meridian non-Mistral vehicles;
   their model's telemetryCapability now includes "onboard-fwe", so the
   dataSource is valid after this change.

4. Adds 69 new onboard (vehicle-telemetry) vehicles (VEH-CS-DEMO-0001..0069)
   spanning the 6 non-Mistral Meridian model lines and their declared model years.

5. Adds 10 new cloud-only (cloud-telemetry) Meridian Mistral vehicles
   (VEH-CS-DEMO-0070..0079) using model years from the MERIDIAN-MISTRAL manifest.

After this script the table holds:
   Meridian onboard  = 90  (21 reclassified + 69 new)
   Meridian cloud    = 10  (10 new Mistral)
   Ford cloud        = 48  (unchanged)
   cms-native        =  1  (unchanged)
   Total             = 149

IDEMPOTENCY
-----------
- Manifest updates: unconditional put_item with the full item (safe; pk/sk
  are composite and unique per manifest name+version).
- Year-fix updates: update_item, no condition — idempotent for the same value.
- Reclassify updates: update_item, no condition — idempotent.
- New vehicles: put_item with ConditionExpression attribute_not_exists(vehicleId).
  Re-running skips already-present rows.

CONSTRAINTS ENFORCED HERE
--------------------------
- Every new vehicle sets ``producer='meridian'``.
- No vehicle's ``dataSource`` contradicts its model's ``telemetryCapability``:
  "onboard-fwe" models accept vehicle-telemetry only;
  "cloud-to-cloud" models accept cloud-telemetry only.
  Checked in ``_assert_datasource_valid()`` — the write is REJECTED, not defaulted.
- ``vehicleCount`` is NOT read or written on manifests — derive counts (spec constraint).
- ``sold_to`` is NOT written (belongs to T0.3 / seed_vehicle_sold_to.py only).

Usage:
    # Dry-run (default — prints plan, writes nothing):
    DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \\
        python3 deployment/scripts/seed_demo_population.py

    # Apply:
    DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \\
        python3 deployment/scripts/seed_demo_population.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

STAGE   = os.environ.get("DEPLOYMENT_STAGE", "prod")
REGION  = os.environ.get("AWS_REGION", "us-east-1")
PROFILE = os.environ.get("AWS_PROFILE", "default")

session  = boto3.Session(profile_name=PROFILE, region_name=REGION)
dynamodb = session.resource("dynamodb")

vehicles_table = dynamodb.Table(f"cms-{STAGE}-storage-vehicles")
manifests_table = dynamodb.Table(f"cms-{STAGE}-model-manifest")
fleets_table = dynamodb.Table(f"cms-{STAGE}-storage-fleets")

NOW = datetime.now(timezone.utc).isoformat()

# ---------------------------------------------------------------------------
# telemetryCapability per model manifest
# Capability is stated on the model, never inferred from powertrain.
# 6 non-Mistral Meridian lines → onboard-fwe
# MERIDIAN-MISTRAL → cloud-to-cloud
# ---------------------------------------------------------------------------

# (modelManifestName, modelManifestVersion): telemetryCapability
_MANIFEST_CAPABILITIES: dict[tuple[str, str], list[str]] = {
    ("MERIDIAN-WINDROSE",  "1"): ["onboard-fwe"],
    ("MERIDIAN-TRAILWIND", "1"): ["onboard-fwe"],
    ("MERIDIAN-CRESTWIND", "1"): ["onboard-fwe"],
    ("MERIDIAN-ZEPHYR",    "1"): ["onboard-fwe"],
    ("MERIDIAN-AZIMUTH",   "1"): ["onboard-fwe"],
    ("MERIDIAN-SIROCCO",   "1"): ["onboard-fwe"],
    ("MERIDIAN-MISTRAL",   "1"): ["cloud-to-cloud"],
}

# Which dataSource values each capability permits.
# "onboard-fwe"   → vehicle-telemetry (data arrives via FWE agent over MQTT)
# "cloud-to-cloud" → cloud-telemetry  (data arrives via producer feed / ingest API)
_CAPABILITY_TO_DATASOURCE: dict[str, str] = {
    "onboard-fwe":    "vehicle-telemetry",
    "cloud-to-cloud": "cloud-telemetry",
}

# Inverse: which capability a given dataSource implies.
_DATASOURCE_TO_CAPABILITY: dict[str, str] = {
    v: k for k, v in _CAPABILITY_TO_DATASOURCE.items()
}

def _assert_datasource_valid(model_name: str, data_source: str) -> None:
    """Raise ValueError if data_source contradicts the model's telemetryCapability.

    This is the write-time enforcement required by the Accept criterion.
    A contradicting vehicle write is REJECTED, not defaulted.
    """
    # Look up capability for this model (version 1).
    caps = _MANIFEST_CAPABILITIES.get((model_name, "1"))
    if caps is None:
        # Unknown model — pass through; only Meridian models are enforced here.
        return
    # Determine which capability the data_source requires.
    required_cap = _DATASOURCE_TO_CAPABILITY.get(data_source)
    if required_cap is None:
        raise ValueError(
            f"Unknown dataSource value {data_source!r}. "
            "Valid values: 'vehicle-telemetry', 'cloud-telemetry'."
        )
    if required_cap not in caps:
        raise ValueError(
            f"dataSource={data_source!r} contradicts model {model_name!r}: "
            f"model telemetryCapability={caps!r} requires dataSource "
            f"{_CAPABILITY_TO_DATASOURCE[caps[0]]!r}."
        )


# ---------------------------------------------------------------------------
# Year mismatches to fix in staging
# (vehicleId → corrected_year)
# Spec: "The 4 vehicles whose model year their manifest does not declare
#        must be resolved explicitly."
#
# Resolution rationale:
#   VEH-DEMO-PUB-001 / VEH-DEMO-PUB-002: Crestwind 2025
#     Manifest modelYears: [2022, 2023, 2026]. Nearest declared year is 2023.
#     Resolution: correct vehicle year to 2023 (the manifest is not changed).
#
#   VEH-MRDN-0001: Windrose 2023
#     Manifest modelYears: [2022, 2024, 2026]. 2022 and 2024 are equidistant.
#     Resolution: correct vehicle year to 2022 (the earliest declared year for
#     this vehicle, which is the oldest in the flt-meridian-range-001 cohort).
#
#   VEH-MRDN-0011: Windrose 2023
#     Manifest modelYears: [2022, 2024, 2026]. Same distance to 2022 and 2024.
#     Resolution: correct vehicle year to 2024 (kept distinct from VEH-MRDN-0001
#     so the fleet retains year variety; 2024 is the Extended-Range spec year).
# ---------------------------------------------------------------------------
_YEAR_FIXES: dict[str, int] = {
    "VEH-DEMO-PUB-001": 2023,   # Crestwind 2025 → 2023 (manifest: [2022,2023,2026])
    "VEH-DEMO-PUB-002": 2023,   # Crestwind 2025 → 2023 (manifest: [2022,2023,2026])
    "VEH-MRDN-0001":    2022,   # Windrose  2023 → 2022 (manifest: [2022,2024,2026])
    "VEH-MRDN-0011":    2024,   # Windrose  2023 → 2024 (manifest: [2022,2024,2026])
}

# ---------------------------------------------------------------------------
# Model lines for new vehicles
# (model, vehicleType, fuelType, dataSource, telemetryCapabilityNeeded, modelYears)
# ---------------------------------------------------------------------------
_ONBOARD_MODELS = [
    ("Windrose",  "SUV",    "electric", "vehicle-telemetry", [2022, 2024, 2026]),
    ("Trailwind", "SUV",    "electric", "vehicle-telemetry", [2023, 2025]),
    ("Crestwind", "Sedan",  "electric", "vehicle-telemetry", [2022, 2023, 2026]),
    ("Zephyr",    "Van",    "electric", "vehicle-telemetry", [2024, 2025]),
    ("Azimuth",   "Pickup", "hybrid",   "vehicle-telemetry", [2022, 2025, 2026]),
    ("Sirocco",   "Pickup", "diesel",   "vehicle-telemetry", [2023, 2024]),
]

_MISTRAL_MODEL = ("Mistral", "Sedan", "gasoline", "cloud-telemetry", [2023, 2024])

# Fleet id for new CS demo vehicles
_CS_DEMO_FLEET_ID = "flt-cs-demo-001"

# The fleet row this script's vehicles point at. Until 2026-09-15 this script
# stamped _CS_DEMO_FLEET_ID onto 79 vehicles and never created the fleet, so the
# vehicle detail page rendered "unknown fleet" for every one of them — a dangling
# foreign key by construction. See
# issues/2026-09-15-seed-demo-population-creates-dangling-fleet-references/.
#
# Shape matches the sibling demo fleets in cms-{stage}-storage-fleets. Both
# `fleetName` and `name` are set because different readers use different keys.
# `data_source` is `vehicle-telemetry` — the majority of this cohort is onboard;
# the 10 Mistral cloud-telemetry vehicles are the documented exception and the
# field describes the fleet's primary path, not a per-vehicle constraint.
_CS_DEMO_FLEET_ITEM: dict = {
    "fleetId": _CS_DEMO_FLEET_ID,
    "fleetName": "CS Demo Fleet",
    "name": "CS Demo Fleet",
    "fleetType": "cs-demo",
    "description": (
        "Connected Services demo fleet — Meridian vehicles used by the CS portal "
        "demo flows. Distinct from flt-meridian-range-001, which is the "
        "model-range demo fleet."
    ),
    "data_source": "vehicle-telemetry",
    "operationalCity": "San Francisco",
    "region": "US-West",
    "status": "active",
    "tenantType": "external",
    "numActiveCampaigns": 0,
    "numTotalCampaigns": 0,
    "attributes": {
        "isDemoFleet": True,
        "manufacturer": "Meridian",
        "primaryUse": "cs-demo",
    },
}


def _ensure_cs_demo_fleet(dry_run: bool) -> str:
    """Create the CS demo fleet row if absent. Returns 'created', 'exists' or 'dry-run'.

    Idempotent via ``attribute_not_exists(fleetId)``, matching
    ``seed_engineering_fleets.py``'s ``put_fleet``. Never overwrites an existing
    fleet — an operator may have edited its name or description, and this script
    has no business reverting that.

    Must run BEFORE vehicles are written, so the reference is never dangling even
    transiently.
    """
    if dry_run:
        return "dry-run"
    now = datetime.now(timezone.utc).isoformat()
    item = {**_CS_DEMO_FLEET_ITEM, "createdAt": now, "updatedAt": now}
    try:
        fleets_table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(fleetId)",
        )
        return "created"
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return "exists"
        raise

BASE_LAT, BASE_LON = 37.7749, -122.4194   # San Francisco


def _new_onboard_vehicles(count: int) -> list[dict]:
    """Generate ``count`` new onboard (vehicle-telemetry) Meridian vehicles.

    IDs: VEH-CS-DEMO-0001 .. VEH-CS-DEMO-{count:04d}
    """
    vehicles: list[dict] = []
    model_cycle = _ONBOARD_MODELS
    for i in range(count):
        seq = i + 1
        m_idx = i % len(model_cycle)
        model, v_type, fuel, data_source, years = model_cycle[m_idx]
        year = years[i % len(years)]
        mileage = 2000 + (seq * 1_973) % 60_000
        # Validate data_source vs model capability before generating.
        manifest_name = f"MERIDIAN-{model.upper()}"
        _assert_datasource_valid(manifest_name, data_source)
        vehicles.append({
            "vehicleId":           f"VEH-CS-DEMO-{seq:04d}",
            "vin":                 f"CSPR{seq:013d}",
            "fleetId":             _CS_DEMO_FLEET_ID,
            "name":                f"Meridian {model} #{seq:04d}",
            "make":                "Meridian",
            "model":               model,
            "year":                year,
            "vehicleType":         v_type,
            "fuelType":            fuel,
            "status":              "active",
            "connectionStatus":    "disconnected",
            "enrollmentStatus":    "ACTIVE",
            "color":               ["CS Slate", "CS Mist", "CS Dune"][seq % 3],
            "licensePlate":        f"CS-{seq:04d}",
            "mileage":             mileage,
            "odometer":            mileage,
            "fuelLevel":           45 + (seq * 7) % 50,
            "batterySoh":          85 + (seq * 3) % 14 if fuel in ("electric", "hybrid") else None,
            "lastSpeed":           0.0,
            "totalTrips":          10 + (seq * 13) % 200,
            "lastLatitude":        round(BASE_LAT + ((seq * 41) % 100 - 50) / 500.0, 6),
            "lastLongitude":       round(BASE_LON + ((seq * 59) % 100 - 50) / 500.0, 6),
            "dataSource":          data_source,
            # modelManifestName / modelManifestVersion: required invariant per
            # spec 2026-08-28-cms-cert-follows-model § D5.
            "modelManifestName":    manifest_name,
            "modelManifestVersion": "1",
            # producer: mandatory per T0.1. All CS demo vehicles are Meridian.
            "producer":            "meridian",
            "attributes": {
                "fleetType":       "cs-demo",
                "fuelType":        fuel,
                "operationalCity": "San Francisco",
                "primaryUse":      "cs-demo",
            },
            "tenantType":         "external",
            "isDemoVehicle":      True,
            "vehicleEnvironment": "demo",
            "telemetryTier":      "standard",
            "createdAt":          NOW,
            "updatedAt":          NOW,
        })
    # Strip None values (batterySoh absent for non-EV/hybrid)
    for v in vehicles:
        if v.get("batterySoh") is None:
            del v["batterySoh"]
    return vehicles


def _new_mistral_vehicles(count: int) -> list[dict]:
    """Generate ``count`` new cloud-only (cloud-telemetry) MERIDIAN-MISTRAL vehicles.

    IDs: VEH-CS-DEMO-{70:04d} .. VEH-CS-DEMO-{79:04d}
    """
    model, v_type, fuel, data_source, years = _MISTRAL_MODEL
    manifest_name = f"MERIDIAN-{model.upper()}"
    vehicles: list[dict] = []
    for i in range(count):
        # Sequences 70..79
        seq = 70 + i
        year = years[i % len(years)]
        _assert_datasource_valid(manifest_name, data_source)
        vehicles.append({
            "vehicleId":           f"VEH-CS-DEMO-{seq:04d}",
            "vin":                 f"CSPR{seq:013d}",
            "fleetId":             _CS_DEMO_FLEET_ID,
            "name":                f"Meridian {model} #{seq:04d}",
            "make":                "Meridian",
            "model":               model,
            "year":                year,
            "vehicleType":         v_type,
            "fuelType":            fuel,
            "status":              "active",
            "connectionStatus":    "disconnected",
            "enrollmentStatus":    "ACTIVE",
            "color":               ["CS Graphite", "CS Pearl", "CS Onyx"][seq % 3],
            "licensePlate":        f"CS-{seq:04d}",
            "mileage":             15_000 + (seq * 1_337) % 40_000,
            "odometer":            15_000 + (seq * 1_337) % 40_000,
            "fuelLevel":           35 + (seq * 11) % 55,
            "lastSpeed":           0.0,
            "totalTrips":          80 + (seq * 7) % 300,
            "lastLatitude":        round(BASE_LAT + ((seq * 43) % 100 - 50) / 500.0, 6),
            "lastLongitude":       round(BASE_LON + ((seq * 61) % 100 - 50) / 500.0, 6),
            "dataSource":          data_source,
            # modelManifestName / modelManifestVersion: required invariant per
            # spec 2026-08-28-cms-cert-follows-model § D5.
            "modelManifestName":    manifest_name,
            "modelManifestVersion": "1",
            # producer: mandatory per T0.1. All CS demo vehicles are Meridian.
            "producer":            "meridian",
            "attributes": {
                "fleetType":       "cs-demo",
                "fuelType":        fuel,
                "operationalCity": "San Francisco",
                "primaryUse":      "cs-demo",
            },
            "tenantType":         "external",
            "isDemoVehicle":      True,
            "vehicleEnvironment": "demo",
            "telemetryTier":      "standard",
            "createdAt":          NOW,
            "updatedAt":          NOW,
        })
    return vehicles


# ---------------------------------------------------------------------------
# DynamoDB helpers
# ---------------------------------------------------------------------------

def _to_decimal(obj):
    """Recursively convert float → Decimal for the DynamoDB resource API."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_decimal(x) for x in obj]
    return obj


def _put_vehicle(v: dict, dry_run: bool, force: bool) -> str:
    """Write a vehicle row; return 'written', 'exists', or 'dry-run'.

    The ``Item`` argument leads with a literal ``"producer": "meridian"`` key so
    that the repo-wide producer write guard (test_vehicle_producer_write_guard.py)
    can verify the field is present via AST inspection.  The ``**item`` spread
    carries the rest of the row (all new vehicles in this module are Meridian,
    so the literal value matches).
    """
    if dry_run:
        return "dry-run"
    item = _to_decimal(v)
    # Safety: the literal "producer": "meridian" must agree with the dict value.
    assert item.get("producer") == "meridian", (
        f"BUG: vehicle {v.get('vehicleId')} has producer={item.get('producer')!r}, expected 'meridian'"
    )
    try:
        if force:
            vehicles_table.put_item(Item={"producer": "meridian", **item})
        else:
            vehicles_table.put_item(
                Item={"producer": "meridian", **item},
                ConditionExpression="attribute_not_exists(vehicleId)",
            )
        return "written"
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return "exists"
        raise


def _update_vehicle_year(vehicle_id: str, new_year: int, dry_run: bool) -> None:
    """Update the year attribute on an existing vehicle row."""
    if dry_run:
        return
    vehicles_table.update_item(
        Key={"vehicleId": vehicle_id},
        UpdateExpression="SET #yr = :y, updatedAt = :ua",
        ExpressionAttributeNames={"#yr": "year"},
        ExpressionAttributeValues={
            ":y":  new_year,
            ":ua": NOW,
        },
    )


def _reclassify_vehicle_datasource(vehicle_id: str, new_ds: str, dry_run: bool) -> None:
    """Update the dataSource attribute on an existing vehicle row."""
    if dry_run:
        return
    vehicles_table.update_item(
        Key={"vehicleId": vehicle_id},
        UpdateExpression="SET dataSource = :ds, updatedAt = :ua",
        ExpressionAttributeValues={
            ":ds": new_ds,
            ":ua": NOW,
        },
    )


def _update_manifest_capability(
    name: str, version: str, capability: list[str], dry_run: bool
) -> str:
    """Add telemetryCapability to a model manifest row via update_item."""
    if dry_run:
        return "dry-run"
    manifests_table.update_item(
        Key={
            "pk": f"MODEL#{name}#{version}",
            "sk": f"MODEL#{name}",
        },
        UpdateExpression="SET telemetryCapability = :tc, updateTimestamp = :ua",
        ExpressionAttributeValues={
            ":tc": capability,
            ":ua": NOW,
        },
    )
    return "updated"


# ---------------------------------------------------------------------------
# Build plan
# ---------------------------------------------------------------------------

def build_plan():
    """Return all operations this script will perform.

    Returns:
        manifest_ops  — list of (name, version, capability)
        year_fix_ops  — list of (vehicle_id, old_year, new_year)
        reclassify_ops — list of (vehicle_id, old_ds, new_ds)
        new_vehicles  — list of vehicle dicts to insert
    """
    manifest_ops = [
        (name, ver, cap)
        for (name, ver), cap in sorted(_MANIFEST_CAPABILITIES.items())
    ]

    year_fix_ops = [
        (vid, "<live>", new_year)
        for vid, new_year in sorted(_YEAR_FIXES.items())
    ]

    # All 21 existing Meridian non-Mistral vehicles will move from cloud to onboard.
    # We can't know the full ID list at build time without a live scan, so the
    # reclassify pass reads them lazily at apply time.
    # For dry-run we print a summary.
    reclassify_ops = "all 21 existing Meridian vehicles: cloud-telemetry → vehicle-telemetry"

    new_onboard   = _new_onboard_vehicles(69)   # VEH-CS-DEMO-0001..0069
    new_mistral   = _new_mistral_vehicles(10)   # VEH-CS-DEMO-0070..0079
    new_vehicles  = new_onboard + new_mistral

    return manifest_ops, year_fix_ops, reclassify_ops, new_vehicles


def _backfill_manifest_name(dry_run: bool) -> None:
    """Backfill modelManifestName + modelManifestVersion on VEH-CS-DEMO-* rows
    that were created before spec 2026-08-28-cms-cert-follows-model § D5 made
    those fields required.

    Uses update_item with a condition so already-correct rows are skipped
    (idempotent: re-running is safe).

    The manifest name is derived deterministically from the vehicle's ``model``
    attribute: ``MERIDIAN-{model.upper()}``.  All VEH-CS-DEMO rows have Meridian
    models; the derivation matches the literal values stored on VEH-MRDN-* and
    VEH-DEMO-PUB-* rows (observed in live staging: e.g. Trailwind →
    MERIDIAN-TRAILWIND, Azimuth → MERIDIAN-AZIMUTH).
    """
    print("\n=== Step 5: backfill modelManifestName on existing VEH-CS-DEMO-* rows ===")

    # Scan for VEH-CS-DEMO-* rows that are missing the field.
    kwargs: dict = {
        "FilterExpression": (
            "begins_with(vehicleId, :p) AND attribute_not_exists(modelManifestName)"
        ),
        "ExpressionAttributeValues": {":p": "VEH-CS-DEMO-"},
    }
    result = vehicles_table.scan(**kwargs)
    items: list[dict] = list(result["Items"])
    while result.get("LastEvaluatedKey"):
        result = vehicles_table.scan(
            **kwargs, ExclusiveStartKey=result["LastEvaluatedKey"]
        )
        items.extend(result["Items"])

    print(f"  Found {len(items)} VEH-CS-DEMO-* rows missing modelManifestName")

    if not items:
        print("  [ok] Nothing to backfill.")
        return

    if dry_run:
        print("  [dry-run] Would backfill the following (first 10):")
        for v in sorted(items, key=lambda x: x["vehicleId"])[:10]:
            manifest = f"MERIDIAN-{v.get('model', '').upper()}"
            print(f"    {v['vehicleId']}: model={v.get('model')} → {manifest}")
        if len(items) > 10:
            print(f"    ... and {len(items) - 10} more")
        return

    written = 0
    failed = 0
    for v in items:
        vid = v["vehicleId"]
        model_name = v.get("model", "")
        manifest_name = f"MERIDIAN-{model_name.upper()}"
        try:
            vehicles_table.update_item(
                Key={"vehicleId": vid},
                UpdateExpression=(
                    "SET modelManifestName = :mn, "
                    "modelManifestVersion = :mv, "
                    "updatedAt = :ua"
                ),
                ConditionExpression="attribute_not_exists(modelManifestName)",
                ExpressionAttributeValues={
                    ":mn": manifest_name,
                    ":mv": "1",
                    ":ua": NOW,
                },
            )
            written += 1
        except Exception as exc:
            from botocore.exceptions import ClientError as _CE
            if isinstance(exc, _CE) and exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                # Another process beat us; idempotent.
                pass
            else:
                print(
                    f"  [ERROR] backfill failed for {vid}: {exc}",
                    file=sys.stderr,
                )
                failed += 1

    print(f"  backfilled={written}  failed={failed}")
    if failed:
        sys.exit(1)


def _scan_meridian_vehicles() -> list[dict]:
    kwargs: dict = {
        "FilterExpression": "make = :m",
        "ExpressionAttributeValues": {":m": "Meridian"},
    }
    result = vehicles_table.scan(**kwargs)
    items = list(result["Items"])
    while result.get("LastEvaluatedKey"):
        result = vehicles_table.scan(
            **kwargs, ExclusiveStartKey=result["LastEvaluatedKey"]
        )
        items.extend(result["Items"])
    return items


# ---------------------------------------------------------------------------
# Verify phase (post-apply scan)
# ---------------------------------------------------------------------------

def verify_counts(dry_run: bool) -> None:
    """Print a post-run population report and assert targets are met."""
    if dry_run:
        print("\n[dry-run] Skipping post-apply verification scan.")
        return

    print("\nPost-apply population scan …")
    kwargs: dict = {}
    result = vehicles_table.scan(**kwargs)
    items = list(result["Items"])
    while result.get("LastEvaluatedKey"):
        result = vehicles_table.scan(ExclusiveStartKey=result["LastEvaluatedKey"])
        items.extend(result["Items"])

    meridian = [v for v in items if v.get("producer") == "meridian"]
    onboard  = [v for v in meridian if v.get("dataSource") == "vehicle-telemetry"]
    cloud    = [v for v in meridian if v.get("dataSource") == "cloud-telemetry"]
    ford_cloud = [v for v in items if v.get("producer") == "oem1"]
    cms_native = [v for v in items if v.get("producer") == "cms-native"]

    print(f"  Meridian onboard  = {len(onboard)}")
    print(f"  Meridian cloud    = {len(cloud)}")
    print(f"  Ford cloud        = {len(ford_cloud)}")
    print(f"  cms-native        = {len(cms_native)}")
    print(f"  Total             = {len(items)}")

    errors: list[str] = []
    if len(meridian) != 100:
        errors.append(f"Expected 100 Meridian, got {len(meridian)}")
    if len(onboard) != 90:
        errors.append(f"Expected 90 Meridian onboard, got {len(onboard)}")
    if len(cloud) != 10:
        errors.append(f"Expected 10 Meridian cloud, got {len(cloud)}")
    if len(ford_cloud) != 48:
        errors.append(f"Expected 48 Ford cloud, got {len(ford_cloud)}")
    if len(cms_native) != 1:
        errors.append(f"Expected 1 cms-native, got {len(cms_native)}")

    if errors:
        print("\nVERIFICATION ERRORS:")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    else:
        print("\n  ✅ All population counts match targets.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="Seed Meridian demo population for CS portal (T0.6)."
    )
    p.add_argument(
        "--apply", action="store_true",
        help="Write to staging. Default is dry-run (prints plan, writes nothing).",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Overwrite existing vehicle rows (skips ConditionExpression).",
    )
    args = p.parse_args()
    dry_run = not args.apply

    manifest_ops, year_fix_ops, reclassify_summary, new_vehicles = build_plan()

    print(f"Region={REGION}  Stage={STAGE}  Profile={PROFILE}")
    print(f"dry_run={dry_run}\n")

    # ── Step 0: the fleet the vehicles below point at ────────────────────
    # Runs first so the fleetId reference is never dangling, even transiently.
    print("=== Step 0: CS demo fleet row ===")
    fleet_status = _ensure_cs_demo_fleet(dry_run)
    print(f"  [{fleet_status}] {_CS_DEMO_FLEET_ID} → 'CS Demo Fleet'")

    # ── Step 1: manifest capability updates ─────────────────────────────
    print("\n=== Step 1: telemetryCapability on model manifests ===")
    for name, ver, cap in manifest_ops:
        status = _update_manifest_capability(name, ver, cap, dry_run)
        print(f"  [{status}] {name} v{ver} → telemetryCapability={cap}")

    # ── Step 2: year-mismatch fixes ──────────────────────────────────────
    print("\n=== Step 2: year-mismatch fixes ===")
    for vid, _, new_year in year_fix_ops:
        if dry_run:
            print(f"  [dry-run] {vid} → year={new_year}")
        else:
            _update_vehicle_year(vid, new_year, dry_run=False)
            print(f"  [updated] {vid} → year={new_year}")

    # ── Step 3: reclassify existing Meridian vehicles ────────────────────
    print("\n=== Step 3: reclassify existing Meridian vehicles to vehicle-telemetry ===")
    if dry_run:
        print(f"  [dry-run] {reclassify_summary}")
        print("  (21 update_item calls, cloud-telemetry → vehicle-telemetry)")
    else:
        meridian_existing = _scan_meridian_vehicles()
        reclassify_count = 0
        skip_count = 0
        for v in meridian_existing:
            vid = v["vehicleId"]
            current_ds = v.get("dataSource", "<none>")
            model = v.get("model", "<none>")
            manifest_name = f"MERIDIAN-{model.upper()}"
            # Only reclassify non-Mistral vehicles (onboard-fwe models).
            if manifest_name in {k[0] for k, cap in _MANIFEST_CAPABILITIES.items() if "onboard-fwe" in cap}:
                if current_ds == "vehicle-telemetry":
                    skip_count += 1
                else:
                    _reclassify_vehicle_datasource(vid, "vehicle-telemetry", dry_run=False)
                    reclassify_count += 1
            else:
                skip_count += 1
        print(f"  reclassified={reclassify_count}  already_onboard_or_skipped={skip_count}")

    # ── Step 4: new vehicles ─────────────────────────────────────────────
    print(f"\n=== Step 4: new vehicles ({len(new_vehicles)} total) ===")
    onboard_new   = [v for v in new_vehicles if v["dataSource"] == "vehicle-telemetry"]
    cloud_new     = [v for v in new_vehicles if v["dataSource"] == "cloud-telemetry"]
    print(f"  New onboard  = {len(onboard_new)}  (VEH-CS-DEMO-0001..{len(onboard_new):04d})")
    print(f"  New cloud    = {len(cloud_new)}   (VEH-CS-DEMO-0070..0079)")

    written = 0
    exists_count = 0
    for v in new_vehicles:
        status = _put_vehicle(v, dry_run, args.force)
        if status == "written":
            written += 1
        elif status == "exists":
            exists_count += 1
        # dry-run: no output per vehicle, summary printed after

    if dry_run:
        print(f"\n  [dry-run] would write {len(new_vehicles)} vehicles "
              f"({len(onboard_new)} onboard + {len(cloud_new)} cloud-only Mistral)")
    else:
        print(f"  written={written}  already_present={exists_count}")

    # ── Step 5: backfill modelManifestName on rows that pre-date D5 ─────
    _backfill_manifest_name(dry_run)

    # ── Summary ──────────────────────────────────────────────────────────
    print("\n=== Summary ===")
    print(f"  Manifest updates        : {len(manifest_ops)}")
    print(f"  Year-mismatch fixes     : {len(year_fix_ops)}")
    print(f"  Reclassify (existing)   : 21 Meridian vehicles → vehicle-telemetry")
    print(f"  New onboard vehicles    : {len(onboard_new)}")
    print(f"  New cloud-only Mistrals : {len(cloud_new)}")
    print(f"  ─────────────────────────────────────────────")
    print(f"  Target after apply:")
    print(f"    Meridian onboard  = 90  (21 reclassified + 69 new)")
    print(f"    Meridian cloud    = 10  (10 new Mistral)")
    print(f"    Ford cloud        = 48  (unchanged)")
    print(f"    cms-native        =  1  (unchanged)")

    if dry_run:
        print("\n--dry-run: nothing written. Pass --apply to execute.")
    else:
        verify_counts(dry_run=False)

    return 0


if __name__ == "__main__":
    sys.exit(main())
