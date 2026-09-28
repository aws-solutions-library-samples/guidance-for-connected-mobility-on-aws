#!/usr/bin/env python3
"""
Seed the Meridian demo fleet — one fleet, vehicles across model years 2022-2026.

Why this exists
---------------
The two existing Meridian cohorts in `seed_engineering_fleets.py` are
model-specific and single-year (`Windrose Production Cohort` 2025,
`Trailwind Validation Fleet` 2026), which makes every demo screen show one make,
one model line and one year. This adds a fleet that spans the Meridian model
range and five model years, so list views, filters, and the iOS surfaces show
variety.

Brand fiction
-------------
`Meridian` is the approved fictional make. The canonical pre-rebrand -> fiction
mapping lives in `deployment/scripts/rebrand_visible_brand_values.py` and is
deliberately NOT restated here: that file is `.publish-exclude`d, this one is not,
and a comment that explains a rebrand by naming the brand it replaced re-embeds
exactly what the rebrand removed. (Learned the hard way — the publish scanner
flagged this docstring's first draft as a critical finding. Same failure mode as
the `.gitignore`/scan-config exposures described in
`~/.kiro/steering/public-mirror-publish.md`: a denylist written in terms of the
secret embeds the secret.)

The model names used here are the ones already in the iOS client's asset
catalogue (`MeridianWindrose`, `MeridianTrailwind`, `MeridianCrestwind`,
`MeridianAzimuth`) plus `Zephyr`, so imagery resolves rather than falling back.

Nothing here carries a real make, model, colour name, plate region or VIN WMI.
That is deliberate: the internal engineering seeder still encodes the pre-rebrand
brand in its *identifiers* — an assigned manufacturer WMI in its VIN prefix,
model-coded vehicle ids and ECU config ids, a region-coded plate prefix, and the
original paint names. Those are invisible in the UI and that file does not ship,
so they are a consistency wart rather than a compliance problem — but there is no
reason for a new seeder to inherit them.

Trims
-----
`trim` is a NEW field. As of 2026-08-19 nothing in the API or either client reads
it, so it is written for completeness and surfaced in `name` (e.g.
"Meridian Windrose Performance #0003") so the value is visible somewhere rather
than being data no screen can show.

Tables written
--------------
  cms-{stage}-storage-fleets
  cms-{stage}-storage-vehicles
  cms-{stage}-storage-fleet-enrollment

Distinct ID prefixes (`flt-meridian-*`, `VEH-MRDN-*`) so this co-exists with both
the generic and engineering seeders without ConditionExpression collisions.

Idempotent — ConditionExpression on first writes; `--force` overwrites.
`--dry-run` prints the plan without writing.

Usage:
  DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \\
      python3 deployment/scripts/seed_meridian_fleet.py --dry-run
  DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \\
      python3 deployment/scripts/seed_meridian_fleet.py

Environment:
  AWS_REGION       — defaults to us-east-1
  DEPLOYMENT_STAGE — defaults to prod
  AWS_PROFILE      — defaults to default
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

REGION = os.environ.get('AWS_REGION', 'us-east-1')
STAGE = os.environ.get('DEPLOYMENT_STAGE', 'prod')
PROFILE = os.environ.get('AWS_PROFILE', 'default')

session = boto3.Session(region_name=REGION)
dynamodb = session.resource('dynamodb')
fleets_table = dynamodb.Table(f'cms-{STAGE}-storage-fleets')
vehicles_table = dynamodb.Table(f'cms-{STAGE}-storage-vehicles')
enrollment_table = dynamodb.Table(f'cms-{STAGE}-storage-fleet-enrollment')

NOW = datetime.now(timezone.utc).isoformat()

FLEET_ID = 'flt-meridian-range-001'

MERIDIAN_FLEET = {
    'fleetId':            FLEET_ID,
    'name':               'Meridian Range Fleet',
    'fleetName':          'Meridian Range Fleet',
    'description': (
        'Meridian demo fleet spanning model years 2022-2026 across the Windrose, '
        'Trailwind, Crestwind, Azimuth and Zephyr model lines. Exists so fleet '
        'views, filters and model-year comparisons show a realistic spread rather '
        'than a single model and year.'
    ),
    'fleetType':          'demo',
    'tenantType':         'external',
    'status':             'active',
    'operationalCity':    'Portland',
    'region':             'US-West',
    'numActiveCampaigns': 0,
    'numTotalCampaigns':  0,
    'attributes': {
        'primaryUse':   'demo-model-range',
        'isDemoFleet':  True,
        'manufacturer': 'Meridian',
    },
    'createdAt': NOW,
    'updatedAt': NOW,
}

# (model, vehicleType, fuelType, trim, year)
#
# Trims are plain descriptors rather than invented proprietary names, so they
# cannot collide with a real manufacturer's trim badge.
MERIDIAN_VEHICLES = [
    ('Windrose',  'SUV',    'electric', 'Standard',       2022),
    ('Windrose',  'SUV',    'electric', 'Extended Range', 2024),
    ('Windrose',  'SUV',    'electric', 'Performance',    2026),
    ('Trailwind', 'SUV',    'electric', 'Standard',       2023),
    ('Trailwind', 'SUV',    'electric', 'Extended Range', 2025),
    ('Crestwind', 'Sedan',  'electric', 'Standard',       2023),
    ('Crestwind', 'Sedan',  'electric', 'Performance',    2026),
    ('Azimuth',   'Pickup', 'hybrid',   'Standard',       2022),
    ('Azimuth',   'Pickup', 'hybrid',   'Extended Range', 2025),
    ('Zephyr',    'Van',    'electric', 'Standard',       2024),
]

COLORS = ('Meridian Slate', 'Meridian Mist', 'Meridian Dune')

# Usable pack size in kWh, keyed by (model, trim). Sized by body style and trim —
# a van carries more than a sedan, Extended Range more than Standard — so the
# range-across-model-years story has real numbers behind the percentages.
#
# Required rather than cosmetic: the iOS battery card renders its "X of Y kWh"
# line only when `batteryCapacityKwh` is present and non-zero, so an EV row
# without it shows a charge percentage with no energy behind it. Hybrids are
# absent from this map deliberately — they have a traction battery but no pack
# size worth advertising against an EV's.
PACK_KWH = {
    ('Windrose',  'Standard'):       79,
    ('Windrose',  'Extended Range'): 94,
    ('Windrose',  'Performance'):    94,
    ('Trailwind', 'Standard'):       84,
    ('Trailwind', 'Extended Range'): 99,
    ('Crestwind', 'Standard'):       72,
    ('Crestwind', 'Performance'):    82,
    ('Zephyr',    'Standard'):      110,
}

# Portland-ish spread, deterministic per index so re-seeding is stable.
BASE_LAT, BASE_LON = 45.5152, -122.6784


def gen_vehicle(seq, spec):
    model, vehicle_type, fuel_type, trim, year = spec
    mileage = 1_200 + (seq * 2_137) % 48_000
    return {
        'vehicleId':        f'VEH-MRDN-{seq:04d}',
        # 4-char synthetic prefix + 13 digits = 17 chars. Deliberately NOT a real
        # WMI: `seed_engineering_fleets.py` uses `MA1…`, which is an assigned
        # manufacturer WMI, and there is no reason to repeat that here.
        'vin':              f'MRDN{seq:013d}',
        'fleetId':          FLEET_ID,
        'name':             f'Meridian {model} {trim} #{seq:04d}',
        'make':             'Meridian',
        'model':            model,
        'trim':             trim,
        'year':             year,
        'vehicleType':      vehicle_type,
        'status':           'active',
        # NEVER 'connected' on a seeded row. There is no MQTT session behind a
        # seeded vehicle, and claiming one is the defect behind
        # issues/2026-07-31-fake-connected-status-regression/ (four staging
        # vehicles advertising a connection they did not have) and the
        # 2026-08-19 presence work. Asserted in verify_plan() below.
        'connectionStatus': 'disconnected',
        'enrollmentStatus': 'ACTIVE',
        'color':            COLORS[seq % len(COLORS)],
        'licensePlate':     f'MRD-{seq:04d}',
        'fuelType':         fuel_type,
        'mileage':          mileage,
        'odometer':         mileage,
        'batterySoh':       86 + (seq * 3) % 12,
        # Present for EVs only, from PACK_KWH. Absent rather than 0 for hybrids:
        # a zero would render as a real pack size of nothing.
        **({'batteryCapacityKwh': PACK_KWH[(model, trim)]}
           if fuel_type == 'electric' and (model, trim) in PACK_KWH else {}),
        'fuelLevel':        48 + (seq * 7) % 45,
        'lastSpeed':        0.0,
        'totalTrips':       60 + (seq * 17) % 380,
        'lastLatitude':     round(BASE_LAT + ((seq * 37) % 100 - 50) / 400.0, 6),
        'lastLongitude':    round(BASE_LON + ((seq * 53) % 100 - 50) / 400.0, 6),
        # engineTemp / engineRPM deliberately absent for the electric rows: a 0 is
        # worse than an absent field because the UI renders it as a reading. Same
        # reasoning as the comment in seed_engineering_fleets.py.
        # lastSeenAt deliberately absent: with no real telemetry event there is no
        # honest "last seen" value, and the UI renders N/A, which is correct.
        'attributes': {
            'fleetType':       'demo',
            'fuelType':        fuel_type,
            'operationalCity': MERIDIAN_FLEET['operationalCity'],
            'primaryUse':      'demo-model-range',
            'modelLine':       model,
            'trim':            trim,
        },
        'tenantType':         'external',
        'isDemoVehicle':      True,
        'vehicleEnvironment': 'demo',
        'telemetryTier':      'standard',
        # producer: authoritative identity of who built this vehicle.
        # Spec: 2026-09-14-cs-portal-data-model-backend § T0.1.
        # Meridian is the fictional manufacturer; all vehicles in this seeder
        # are Meridian-branded, so the value is the literal string 'meridian'.
        'producer':           'meridian',
        'createdAt':          NOW,
        'updatedAt':          NOW,
    }


def build_plan():
    vehicles = [gen_vehicle(i + 1, spec) for i, spec in enumerate(MERIDIAN_VEHICLES)]
    enrollments = [
        {
            # cms-{stage}-storage-fleet-enrollment is keyed PK (HASH) + SK (RANGE),
            # not flat fleetId/vehicleId — the first run of this script failed with
            # "Missing the key PK in the item" after the vehicles had already been
            # written. Composite shape matches seed_generic_fleets.py.
            'PK':               f'FLEET#{FLEET_ID}',
            'SK':               f'VEHICLE#{v["vehicleId"]}',
            'fleetId':          FLEET_ID,
            'vehicleId':        v['vehicleId'],
            'enrollmentStatus': 'ACTIVE',
            'enrolledAt':       NOW,
            'createdAt':        NOW,
            'updatedAt':        NOW,
        }
        for v in vehicles
    ]
    return [MERIDIAN_FLEET], vehicles, enrollments


def verify_plan(vehicles):
    """Fail before writing rather than after."""
    for v in vehicles:
        assert v['connectionStatus'] != 'connected', (
            f"{v['vehicleId']} would claim connected — seeded rows must not "
            "advertise an MQTT session they do not have"
        )
        assert 'lastSeenAt' not in v, f"{v['vehicleId']} carries a synthetic lastSeenAt"
        assert len(v['vin']) == 17, f"{v['vehicleId']} VIN is {len(v['vin'])} chars, expected 17"
    ids = [v['vehicleId'] for v in vehicles]
    assert len(ids) == len(set(ids)), 'duplicate vehicleId in plan'
    years = {v['year'] for v in vehicles}
    assert len(years) >= 4, f'expected a spread of model years, got {sorted(years)}'


def _convert_floats(obj):
    """Recursively convert floats to Decimal.

    The DynamoDB resource API rejects Python floats outright
    ("Float types are not supported. Use Decimal types instead."), which is how
    the first run of this script failed after writing the fleet row but before any
    vehicle. Same helper shape as `seed_generic_fleets.py` /
    `seed_engineering_fleets.py` — duplicated deliberately, per the note in those
    files, so the seeders stay independently runnable.
    """
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _convert_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_floats(x) for x in obj]
    return obj


def put(table, item, key_names, force):
    kwargs = {'Item': _convert_floats(item)}
    if not force:
        cond = ' AND '.join(f'attribute_not_exists({k})' for k in key_names)
        kwargs['ConditionExpression'] = cond
    try:
        table.put_item(**kwargs)
        return 'written'
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return 'exists'
        raise


def main():
    p = argparse.ArgumentParser(description='Seed the Meridian range demo fleet.')
    p.add_argument('--dry-run', action='store_true',
                   help='print the plan without writing')
    p.add_argument('--force', action='store_true',
                   help='overwrite existing rows')
    args = p.parse_args()

    fleets, vehicles, enrollments = build_plan()
    verify_plan(vehicles)

    print(f'Region={REGION} Stage={STAGE} Profile={PROFILE}')
    print(f'Fleet: {FLEET_ID} ({MERIDIAN_FLEET["name"]})')
    print(f'Vehicles: {len(vehicles)} across model years '
          f'{min(v["year"] for v in vehicles)}-{max(v["year"] for v in vehicles)}')
    for v in vehicles:
        print(f'  {v["vehicleId"]}  {v["year"]}  {v["name"]:<44} '
              f'{v["vehicleType"]:<7} {v["fuelType"]}')

    if args.dry_run:
        print('\n--dry-run: nothing written.')
        return 0

    counts = {'written': 0, 'exists': 0}
    for f in fleets:
        counts[put(fleets_table, f, ['fleetId'], args.force)] += 1
    for v in vehicles:
        counts[put(vehicles_table, v, ['vehicleId'], args.force)] += 1
    for e in enrollments:
        counts[put(enrollment_table, e, ['PK', 'SK'], args.force)] += 1

    print(f'\nwritten={counts["written"]} already-present={counts["exists"]}')
    print('Re-run with --force to overwrite existing rows.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
