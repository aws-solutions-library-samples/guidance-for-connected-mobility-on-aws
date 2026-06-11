#!/usr/bin/env python3
"""
Seed generic demo fleets/vehicles/enrollment.

Public-mirror default fleet seeder. Ships ~3 fleets of obviously-synthetic
demo vehicles so a fresh CMS deploy renders fleet hierarchy + non-empty
vehicle counts in the UI on first load and so `test_S6` (clean-deploy
demo-data assertion) sees ≥ 1 row in `cms-{stage}-storage-fleets` /
`cms-{stage}-storage-vehicles`.

Customers may replace this seed with their own customer-tenant fleet seed
script (the in-tree internal `seed_engineering_fleets.py` is the model:
same DDB tables, same helper shape, customer-specific brand and IDs;
internal-only, kept out of the public mirror via `.publish-exclude`).

Tables written:
  cms-{stage}-storage-fleets
  cms-{stage}-storage-vehicles
  cms-{stage}-storage-fleet-enrollment

Distinct ID prefixes (`FLT-DEMO-*`, `VEH-DEMO-*`) so this seed can
co-exist with the engineering seeder on internal staging without
ConditionExpression collisions.

Idempotent — uses ConditionExpression on first writes. Use `--force` to
overwrite. `--dry-run` prints the plan (counts + sample) without writing.

Usage:
  DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \\
      python3 deployment/scripts/seed_generic_fleets.py
  DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \\
      python3 deployment/scripts/seed_generic_fleets.py --dry-run
  DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \\
      python3 deployment/scripts/seed_generic_fleets.py --force

Environment:
  AWS_REGION       — defaults to us-east-1
  DEPLOYMENT_STAGE — defaults to prod
  AWS_PROFILE      — defaults to default
"""

import argparse
import os
import random
import sys
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

STAGE = os.environ.get('DEPLOYMENT_STAGE', 'prod')
PROFILE = os.environ.get('AWS_PROFILE', 'default')
REGION = os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION') or 'us-east-1'

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
dynamodb = session.resource('dynamodb')

fleets_table = dynamodb.Table(f'cms-{STAGE}-storage-fleets')
vehicles_table = dynamodb.Table(f'cms-{STAGE}-storage-vehicles')
enrollment_table = dynamodb.Table(f'cms-{STAGE}-storage-fleet-enrollment')

# Deterministic randomness so re-runs (with the same code) produce the same
# rows; combined with ConditionExpression this makes the seed safely
# re-runnable. Seed value is unrelated to any internal date — picked once
# and frozen.
random.seed(20260608)
NOW = datetime.now(timezone.utc).isoformat()

# -----------------------------------------------------------------------------
# Synthetic fleet definitions — non-branded, obviously-demo content.
# Vehicle make/model strings are intentionally synthetic ("DemoMotors",
# "AcmeAuto") rather than real-world OEM names so the file is
# canary-clean against `.publish-secrets-scan.yml` (no real-world
# manufacturer or customer names appear anywhere in the seed payload).
# -----------------------------------------------------------------------------

GENERIC_FLEETS = [
    {
        'fleetId':           'flt-demo-logistics-001',
        'name':              'Demo Logistics Co.',
        'fleetName':         'Demo Logistics Co.',
        'description':       'Synthetic demo fleet — light-duty sedans and SUVs for last-mile delivery. Obviously-non-real demo data shipped with the CMS accelerator.',
        'fleetType':         'demo',
        'tenantType':        'external',
        'status':            'active',
        'operationalCity':   'Austin',
        'region':            'US-Central',
        'numActiveCampaigns': 0,
        'numTotalCampaigns':  0,
        'attributes': {
            'primaryUse':   'demo-delivery',
            'isDemoFleet':  True,
        },
        'createdAt': NOW,
        'updatedAt': NOW,
    },
    {
        'fleetId':           'flt-reference-fleet-002',
        'name':              'Reference Fleet Demo',
        'fleetName':         'Reference Fleet Demo',
        'description':       'Synthetic demo fleet — pickup-truck reference fleet. Obviously-non-real demo data shipped with the CMS accelerator.',
        'fleetType':         'demo',
        'tenantType':        'external',
        'status':            'active',
        'operationalCity':   'Denver',
        'region':            'US-West',
        'numActiveCampaigns': 0,
        'numTotalCampaigns':  0,
        'attributes': {
            'primaryUse':   'demo-utility',
            'isDemoFleet':  True,
        },
        'createdAt': NOW,
        'updatedAt': NOW,
    },
    {
        'fleetId':           'flt-sample-ops-003',
        'name':              'Sample Fleet Operations',
        'fleetName':         'Sample Fleet Operations',
        'description':       'Synthetic demo fleet — service-van fleet for sample operational scenarios. Obviously-non-real demo data shipped with the CMS accelerator.',
        'fleetType':         'demo',
        'tenantType':        'external',
        'status':            'active',
        'operationalCity':   'Chicago',
        'region':            'US-Midwest',
        'numActiveCampaigns': 0,
        'numTotalCampaigns':  0,
        'attributes': {
            'primaryUse':   'demo-service',
            'isDemoFleet':  True,
        },
        'createdAt': NOW,
        'updatedAt': NOW,
    },
]

# Per-fleet vehicle plans — (count, make, model, vehicleType, fuelType,
# licensePrefix). Make/model strings are deliberately synthetic.
FLEET_VEHICLE_PLANS = {
    'flt-demo-logistics-001': {
        'count': 8,
        'make': 'DemoMotors',
        'model': 'Voyager 1000',
        'vehicleType': 'Sedan',
        'fuelType': 'gasoline',
        'licensePrefix': 'DEMO-LOG',
        'lat_range': (30.20, 30.40),  # Austin-ish
        'lon_range': (-97.85, -97.65),
    },
    'flt-reference-fleet-002': {
        'count': 5,
        'make': 'AcmeAuto',
        'model': 'Hauler 350',
        'vehicleType': 'Pickup',
        'fuelType': 'gasoline',
        'licensePrefix': 'DEMO-REF',
        'lat_range': (39.65, 39.85),  # Denver-ish
        'lon_range': (-105.10, -104.85),
    },
    'flt-sample-ops-003': {
        'count': 5,
        'make': 'AcmeAuto',
        'model': 'Transporter',
        'vehicleType': 'Van',
        'fuelType': 'diesel',
        'licensePrefix': 'DEMO-SMP',
        'lat_range': (41.78, 41.95),  # Chicago-ish
        'lon_range': (-87.75, -87.55),
    },
}

# -----------------------------------------------------------------------------
# Vehicle generation
# -----------------------------------------------------------------------------

def gen_vehicle(seq, fleet_id, plan):
    """Build a single synthetic vehicle row.

    Notes:
      - VIN format: `DEMO` (4) + 13-char zero-padded sequence = 17 chars.
        Synthetic; intentionally does not encode a real-world WMI.
      - `connectionStatus = 'disconnected'` — seed should never lie about
        live state. The `_build_live_vehicle_state` API overlay flips this
        based on real Redis telemetry. Same convention as
        `seed_engineering_fleets.py` post-2026-05-28 fix
        (`issues/2026-05-28-cms-vehicle-fake-connected-status/`).
      - `lastSeenAt` is intentionally absent — no real telemetry has been
        received, so there is no honest "last seen" timestamp to record.
    """
    mileage = 5000 + (seq * 53) % 35000
    lat_lo, lat_hi = plan['lat_range']
    lon_lo, lon_hi = plan['lon_range']
    lat = lat_lo + random.random() * (lat_hi - lat_lo)
    lon = lon_lo + random.random() * (lon_hi - lon_lo)
    vehicle_id = f'VEH-DEMO-{seq:04d}'
    vin = f'DEMO{seq:013d}'  # 4 + 13 = 17 chars
    return {
        'vehicleId':       vehicle_id,
        'vin':             vin,
        'fleetId':         fleet_id,
        'name':            f"{plan['make']} {plan['model']} #{seq:04d}",
        'make':            plan['make'],
        'model':           plan['model'],
        'year':            2024,
        'vehicleType':     plan['vehicleType'],
        'status':          'active',
        'connectionStatus': 'disconnected',
        'enrollmentStatus': 'ACTIVE',
        'color':           'Demo Silver' if seq % 3 == 0 else 'Demo Blue' if seq % 3 == 1 else 'Demo White',
        'licensePlate':    f"{plan['licensePrefix']}-{seq:04d}",
        'fuelType':        plan['fuelType'],
        'mileage':         mileage,
        'odometer':        mileage,
        'engineTemp':      0,
        'engineRPM':       0,
        'fuelLevel':       0,
        'lastSpeed':       0.0,
        'totalTrips':      50 + (seq * 11) % 200,
        'lastLatitude':    round(lat, 6),
        'lastLongitude':   round(lon, 6),
        'attributes': {
            'fleetType':       'demo',
            'fuelType':        plan['fuelType'],
            'operationalCity': fleet_lookup(fleet_id)['operationalCity'],
            'primaryUse':      fleet_lookup(fleet_id)['attributes']['primaryUse'],
        },
        'tenantType':        'external',
        'isDemoVehicle':     True,
        'vehicleEnvironment': 'demo',
        'telemetryTier':     'standard',
        'createdAt':         NOW,
        'updatedAt':         NOW,
    }


def fleet_lookup(fleet_id):
    for f in GENERIC_FLEETS:
        if f['fleetId'] == fleet_id:
            return f
    raise KeyError(f'unknown fleetId: {fleet_id}')


def build_all_vehicles():
    """Generate all vehicles across all fleets with stable per-fleet sequences."""
    vehicles = []
    seq = 0
    for fleet in GENERIC_FLEETS:
        plan = FLEET_VEHICLE_PLANS[fleet['fleetId']]
        for _ in range(plan['count']):
            seq += 1
            vehicles.append(gen_vehicle(seq, fleet['fleetId'], plan))
    return vehicles


# -----------------------------------------------------------------------------
# DynamoDB write helpers — mirror seed_engineering_fleets.py shape so future
# refactors can lift the helpers into a shared module without API drift.
# -----------------------------------------------------------------------------

def _convert_floats(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _convert_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_floats(x) for x in obj]
    return obj


def put_fleet(item, force=False):
    item = _convert_floats(item)
    kwargs = {'Item': item}
    if not force:
        kwargs['ConditionExpression'] = 'attribute_not_exists(fleetId)'
    try:
        fleets_table.put_item(**kwargs)
        return 'created'
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return 'exists'
        raise


def put_vehicle(item, force=False):
    item = _convert_floats(item)
    kwargs = {'Item': item}
    if not force:
        kwargs['ConditionExpression'] = 'attribute_not_exists(vehicleId)'
    try:
        vehicles_table.put_item(**kwargs)
        return 'created'
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return 'exists'
        raise


def put_enrollment(fleet_id, vehicle_id, force=False):
    item = {
        'PK':         f'FLEET#{fleet_id}',
        'SK':         f'VEHICLE#{vehicle_id}',
        'fleetId':    fleet_id,
        'vehicleId':  vehicle_id,
        'enrolledAt': NOW,
    }
    kwargs = {'Item': item}
    if not force:
        kwargs['ConditionExpression'] = 'attribute_not_exists(PK)'
    try:
        enrollment_table.put_item(**kwargs)
        return 'created'
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return 'exists'
        raise


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n', 2)[1])
    p.add_argument('--force', action='store_true',
                   help='Overwrite existing items (default: skip via ConditionExpression)')
    p.add_argument('--dry-run', action='store_true',
                   help='Print the plan + a sample row without writing')
    args = p.parse_args()

    print('=== Generic demo fleets seed ===')
    print(f'  Stage:   {STAGE}')
    print(f'  Region:  {REGION}')
    print(f'  Profile: {PROFILE}')
    print()

    all_vehicles = build_all_vehicles()
    print('Plan:')
    print(f'  Fleets:        {len(GENERIC_FLEETS)}')
    for f in GENERIC_FLEETS:
        plan = FLEET_VEHICLE_PLANS[f['fleetId']]
        print(f"    - {f['fleetId']:30s} {f['name']!r:32s} ({plan['count']} {plan['make']} {plan['model']})")
    print(f'  Vehicles:      {len(all_vehicles)}')
    print(f'  Enrollments:   {len(all_vehicles)}')
    print()

    if args.dry_run:
        import json
        def _ser(o):
            return float(o) if isinstance(o, Decimal) else str(o)
        print('Sample vehicle:')
        print(json.dumps(_convert_floats(all_vehicles[0]), indent=2, default=_ser))
        return 0

    # Fleets
    print('→ Writing fleets...')
    for fleet in GENERIC_FLEETS:
        result = put_fleet(fleet, force=args.force)
        marker = '✅' if result == 'created' else ('🔁' if args.force else '↪️ ')
        print(f"  {marker} {fleet['fleetId']}: {result}")
    print()

    # Vehicles
    print(f'→ Writing {len(all_vehicles)} vehicles...')
    created = exists = 0
    for i, v in enumerate(all_vehicles):
        result = put_vehicle(v, force=args.force)
        if result == 'created':
            created += 1
        else:
            exists += 1
        if (i + 1) % 5 == 0 or i == len(all_vehicles) - 1:
            print(f'  ... {i + 1}/{len(all_vehicles)}  ({created} written, {exists} skipped)')
    print()

    # Enrollment
    print(f'→ Writing {len(all_vehicles)} enrollment records...')
    created = exists = 0
    for i, v in enumerate(all_vehicles):
        result = put_enrollment(v['fleetId'], v['vehicleId'], force=args.force)
        if result == 'created':
            created += 1
        else:
            exists += 1
        if (i + 1) % 5 == 0 or i == len(all_vehicles) - 1:
            print(f'  ... {i + 1}/{len(all_vehicles)}  ({created} written, {exists} skipped)')
    print()

    print('✅ Generic fleets seed complete.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
