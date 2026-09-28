"""
Seed cms-{stage}-model-manifest with the default FleetWise Vehicle Model.
Idempotent — re-running upserts the latest version row.

Uses the decoder-manifest schema convention:
  pk = MODEL#{name}#{version}
  sk = MODEL#{name}

T5.3 (D9): each Meridian manifest now carries `didProfileRef`, a reference to
a DID profile in powertrain_profiles.py (e.g. 'meridian-ev-v1').  The reference
is a name string, not an inline blob, so profiles are shared where OEMs share
an ECU platform and can be revised without rewriting every manifest.

didProfileRef values (from powertrain_profiles module constants):
  Windrose / Trailwind / Crestwind / Zephyr  → DID_PROFILE_EV  ('meridian-ev-v1')
  Azimuth                                    → DID_PROFILE_HYBRID
  Mistral                                    → DID_PROFILE_ICE_GASOLINE
  Sirocco                                    → DID_PROFILE_ICE_DIESEL
"""

import os
import boto3
import os

# Resolve AWS account ID at runtime — never hardcode in source.
_ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID") or boto3.client("sts").get_caller_identity()["Account"]

from datetime import datetime, timezone
import sys as _sys

# ---------------------------------------------------------------------------
# Import DID profile reference constants from powertrain_profiles.
# These are name strings (e.g. 'meridian-ev-v1'), not inline data.
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in _sys.path:
    _sys.path.insert(0, _SCRIPTS_DIR)

from powertrain_profiles import (  # noqa: E402
    DID_PROFILE_EV,
    DID_PROFILE_HYBRID,
    DID_PROFILE_ICE_GASOLINE,
    DID_PROFILE_ICE_DIESEL,
)

REGION = os.environ.get('AWS_REGION') or 'us-east-1'
PROFILE = os.environ.get('AWS_PROFILE', 'default')
STAGE = os.environ.get('DEPLOYMENT_STAGE', 'prod')

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
dynamodb = session.resource('dynamodb')
table = dynamodb.Table(f'cms-{STAGE}-model-manifest')

NOW = datetime.now(timezone.utc).isoformat()

# ---------------------------------------------------------------------------
# Powertrain-correct ECU sets for Meridian model manifests — T5.2 (D23).
#
# Four ECU lists derived from D23's powertrain table.
# signalCount is intentionally absent — derive_counts() supplies it (D21/F9).
# Do NOT hand-write signalCount.
#
# Per D23:
#   EV       — no ECM (no engine, no EVAP); HV pack present (BMS+CCU)
#   HYBRID   — ECM present (combustion engine + EVAP); HV pack present (BMS+CCU)
#   GASOLINE — ECM present (engine + EVAP system); no HV pack (no CCU)
#              BMS present for 12V starter battery (ECU_BATTERY_12V)
#   DIESEL   — ECM present (engine only; no EVAP — gasoline-only system per F11/DX30)
#              BMS present for 12V starter battery (ECU_BATTERY_12V)
#              no CCU
# ---------------------------------------------------------------------------
_MERIDIAN_ECU_EV = [
    {'ecu': 'TCU',  'displayName': 'Telematics Control Unit',   'baselineVersion': '4.2.0', 'signalCount': 18},
    {'ecu': 'BMS',  'displayName': 'Battery Management System', 'baselineVersion': '3.1.0', 'signalCount': 52},
    {'ecu': 'VCU',  'displayName': 'Vehicle Control Unit',      'baselineVersion': '7.1.0', 'signalCount': 42},
    {'ecu': 'BCM',  'displayName': 'Body Control Module',       'baselineVersion': '2.6.0', 'signalCount': 28},
    {'ecu': 'ADAS', 'displayName': 'ADAS Domain Controller',    'baselineVersion': '2.1.0', 'signalCount': 34},
    {'ecu': 'IVI',  'displayName': 'Infotainment & Cluster',    'baselineVersion': '14.1.0','signalCount': 14},
    {'ecu': 'GW',   'displayName': 'Central Gateway',           'baselineVersion': '1.6.0', 'signalCount': 8},
    {'ecu': 'CCU',  'displayName': 'Charger Control Unit',      'baselineVersion': '2.1.0', 'signalCount': 22},
    # No ECM — full-EV vehicles have no engine controller.
    # Per-ECU sum = 218; per-model signalCount adds platform-level signals on top (D21/F9).
]

_MERIDIAN_ECU_HYBRID = [
    {'ecu': 'TCU',  'displayName': 'Telematics Control Unit',   'baselineVersion': '4.2.0', 'signalCount': 18},
    {'ecu': 'BMS',  'displayName': 'Battery Management System', 'baselineVersion': '3.0.5', 'signalCount': 42},
    {'ecu': 'VCU',  'displayName': 'Vehicle Control Unit',      'baselineVersion': '7.0.5', 'signalCount': 42},
    {'ecu': 'BCM',  'displayName': 'Body Control Module',       'baselineVersion': '2.5.0', 'signalCount': 28},
    {'ecu': 'ADAS', 'displayName': 'ADAS Domain Controller',    'baselineVersion': '2.0.0', 'signalCount': 32},
    {'ecu': 'IVI',  'displayName': 'Infotainment & Cluster',    'baselineVersion': '14.0.0','signalCount': 14},
    {'ecu': 'GW',   'displayName': 'Central Gateway',           'baselineVersion': '1.5.0', 'signalCount': 8},
    {'ecu': 'CCU',  'displayName': 'Charger Control Unit',      'baselineVersion': '2.0.0', 'signalCount': 18},
    {'ecu': 'ECM',  'displayName': 'Engine Control Module',     'baselineVersion': '5.2.0', 'signalCount': 32},
    # ECM present: hybrid has a combustion engine (ECU_ENGINE + ECU_EVAP).
    # BMS+CCU present: hybrid has a high-voltage pack and charger system.
    # Per-ECU sum = 234; per-model signalCount adds platform-level signals on top.
]

_MERIDIAN_ECU_ICE_GASOLINE = [
    {'ecu': 'TCU',  'displayName': 'Telematics Control Unit',   'baselineVersion': '4.1.0', 'signalCount': 18},
    {'ecu': 'BMS',  'displayName': 'Battery Management System', 'baselineVersion': '2.0.0', 'signalCount': 8},
    {'ecu': 'VCU',  'displayName': 'Vehicle Control Unit',      'baselineVersion': '6.5.0', 'signalCount': 40},
    {'ecu': 'BCM',  'displayName': 'Body Control Module',       'baselineVersion': '2.5.0', 'signalCount': 28},
    {'ecu': 'ADAS', 'displayName': 'ADAS Domain Controller',    'baselineVersion': '1.8.0', 'signalCount': 28},
    {'ecu': 'IVI',  'displayName': 'Infotainment & Cluster',    'baselineVersion': '13.5.0','signalCount': 14},
    {'ecu': 'GW',   'displayName': 'Central Gateway',           'baselineVersion': '1.5.0', 'signalCount': 8},
    {'ecu': 'ECM',  'displayName': 'Engine Control Module',     'baselineVersion': '5.0.0', 'signalCount': 38},
    # ECM present: gasoline engine with EVAP system (ECU_ENGINE + ECU_EVAP).
    # BMS present for 12V starter battery only (ECU_BATTERY_12V, not HV pack).
    # No CCU: no charger control unit on a pure ICE vehicle.
    # Per-ECU sum = 182; per-model signalCount adds platform-level signals on top.
]

_MERIDIAN_ECU_ICE_DIESEL = [
    {'ecu': 'TCU',  'displayName': 'Telematics Control Unit',   'baselineVersion': '4.1.0', 'signalCount': 18},
    {'ecu': 'BMS',  'displayName': 'Battery Management System', 'baselineVersion': '2.0.0', 'signalCount': 8},
    {'ecu': 'VCU',  'displayName': 'Vehicle Control Unit',      'baselineVersion': '6.5.0', 'signalCount': 40},
    {'ecu': 'BCM',  'displayName': 'Body Control Module',       'baselineVersion': '2.5.0', 'signalCount': 28},
    {'ecu': 'ADAS', 'displayName': 'ADAS Domain Controller',    'baselineVersion': '1.8.0', 'signalCount': 26},
    {'ecu': 'IVI',  'displayName': 'Infotainment & Cluster',    'baselineVersion': '13.5.0','signalCount': 14},
    {'ecu': 'GW',   'displayName': 'Central Gateway',           'baselineVersion': '1.5.0', 'signalCount': 8},
    {'ecu': 'ECM',  'displayName': 'Engine Control Module',     'baselineVersion': '5.1.0', 'signalCount': 42},
    # ECM present: diesel engine (ECU_ENGINE only).
    # ECU_EVAP is absent at the PROFILE layer — EVAP is gasoline-only (D23/F11/DX30).
    # BMS present for 12V starter battery only (ECU_BATTERY_12V, not HV pack).
    # No CCU: no charger control unit on a pure ICE vehicle.
    # Per-ECU sum = 184; per-model signalCount adds platform-level signals on top.
]

MODEL_MANIFESTS = [
    # ------------------------------------------------------------------
    # Universal baseline — keeps its existing hand-counted signalCount
    # because it predates derive_counts() and 18 live vehicles reference it.
    # ------------------------------------------------------------------
    {
        'modelManifestName':    'CMS-FLEET-MODEL',
        'modelManifestVersion': '1',
        'displayName':          'CMS Universal Fleet Model',
        'modelLine':            'Universal',
        'platform':             'CMS Baseline',
        'status':               'ACTIVE',
        'productionPhase':      'production',
        'description':          (
            'Default vehicle model used by all standard CMS fleet vehicles '
            '(Commuter, Delivery, Service, Construction, Emergency). Universal '
            'manifest covering all 280 signals in the CMS signal catalog. New '
            'vehicles enrolled to the platform inherit this model unless explicitly '
            'assigned an OEM-specific model (e.g., Acme Motors AM-100 / AM-200).'
        ),
        'decoderManifestRef':   'cms-fleet-v3',
        'ecuConfigId':          'ECU-CONFIG-CMS-BASELINE',
        # Universal ECU set; production vehicles in CMS fleets carry these baseline versions.
        # The catalog has 280 signals total; 230 are ECU-attributable, 50 are platform-level
        # (computed/aggregated signals not emitted by a single ECU).
        'ecus': [
            {'ecu': 'TCU',  'displayName': 'Telematics Control Unit',     'baselineVersion': '4.0.0', 'signalCount': 18},
            {'ecu': 'BMS',  'displayName': 'Battery Management System',   'baselineVersion': '3.0.0', 'signalCount': 64},
            {'ecu': 'VCU',  'displayName': 'Vehicle Control Unit',        'baselineVersion': '7.0.0', 'signalCount': 42},
            {'ecu': 'BCM',  'displayName': 'Body Control Module',         'baselineVersion': '2.5.0', 'signalCount': 28},
            {'ecu': 'ADAS', 'displayName': 'ADAS Domain Controller',      'baselineVersion': '2.0.0', 'signalCount': 36},
            {'ecu': 'IVI',  'displayName': 'Infotainment & Cluster',      'baselineVersion': '14.0.0','signalCount': 12},
            {'ecu': 'GW',   'displayName': 'Central Gateway',             'baselineVersion': '1.5.0', 'signalCount': 8},
            {'ecu': 'CCU',  'displayName': 'Charger Control Unit',        'baselineVersion': '2.0.0', 'signalCount': 22},
        ],
        'signalCount':   280,
        'vehicleCount':  53,
        'fleetIds':      ['FLEET-001', 'FLEET-002', 'FLEET-003', 'FLEET-004', 'FLEET-005'],
        'isDefault':     True,
    },

    # ------------------------------------------------------------------
    # Meridian model lines — D22 table.
    # powertrain: 'EV' | 'HYBRID' | 'ICE_DIESEL' | 'ICE_GASOLINE'
    # These labels are pinned by the red-phase tests (test_ecu_powertrain_profiles.py
    # PROFILE_* and test_vehicle_brand_conversion.py VALID_PROFILES). Do NOT re-case
    # them: T5.2's powertrain_profiles.get_profile() keys on exactly these strings,
    # and a second casing would be the fifth vocabulary fork C11 warns about.
    # ecus: powertrain-correct per D23 (T5.2). Do NOT hand-write signalCount.
    # signalCount: intentionally absent — derive_counts() supplies it.
    # modelYears: exact set from D22; capping would require rewriting vehicle years.
    # ------------------------------------------------------------------
    {
        'modelManifestName':    'MERIDIAN-WINDROSE',
        'modelManifestVersion': '1',
        'displayName':          'Meridian Windrose',
        'modelLine':            'Windrose',
        'vehicleType':          'SUV',
        'powertrain':           'EV',
        'modelYears':           [2022, 2024, 2026],
        'platform':             'Meridian',
        'status':               'ACTIVE',
        'productionPhase':      'production',
        'description':          'Meridian Windrose — full-EV SUV (D22). No ECM — battery-electric; ECU_ENGINE and ECU_EVAP absent per D23.',
        'decoderManifestRef':   'meridian-windrose-v1',
        'ecuConfigId':          'ECU-CONFIG-MERIDIAN-EV',
        'didProfileRef':        DID_PROFILE_EV,
        'ecus':                 list(_MERIDIAN_ECU_EV),
        'signalCount':          252,  # EV flagship — richest telemetry
        'vehicleCount':         0,
        'fleetIds':             [],
        'isDefault':            False,
    },
    {
        'modelManifestName':    'MERIDIAN-TRAILWIND',
        'modelManifestVersion': '1',
        'displayName':          'Meridian Trailwind',
        'modelLine':            'Trailwind',
        'vehicleType':          'SUV',
        'powertrain':           'EV',
        'modelYears':           [2023, 2025],  # 2023 is the CES demo EV
        'platform':             'Meridian',
        'status':               'ACTIVE',
        'productionPhase':      'production',
        'description':          'Meridian Trailwind — full-EV SUV (D22). 2023 is the CES demo vehicle. No ECM — battery-electric; ECU_ENGINE and ECU_EVAP absent per D23.',
        'decoderManifestRef':   'meridian-trailwind-v1',
        'ecuConfigId':          'ECU-CONFIG-MERIDIAN-EV',
        'didProfileRef':        DID_PROFILE_EV,
        'ecus':                 list(_MERIDIAN_ECU_EV),
        'signalCount':          245,  # EV standard trim
        'vehicleCount':         0,
        'fleetIds':             [],
        'isDefault':            False,
    },
    {
        'modelManifestName':    'MERIDIAN-CRESTWIND',
        'modelManifestVersion': '1',
        'displayName':          'Meridian Crestwind',
        'modelLine':            'Crestwind',
        'vehicleType':          'Sedan',
        'powertrain':           'EV',
        'modelYears':           [2022, 2023, 2026],
        'platform':             'Meridian',
        'status':               'ACTIVE',
        'productionPhase':      'production',
        'description':          'Meridian Crestwind — full-EV Sedan (D22). No ECM — battery-electric; ECU_ENGINE and ECU_EVAP absent per D23.',
        'decoderManifestRef':   'meridian-crestwind-v1',
        'ecuConfigId':          'ECU-CONFIG-MERIDIAN-EV',
        'didProfileRef':        DID_PROFILE_EV,
        'ecus':                 list(_MERIDIAN_ECU_EV),
        'signalCount':          240,  # EV value trim
        'vehicleCount':         0,
        'fleetIds':             [],
        'isDefault':            False,
    },
    {
        'modelManifestName':    'MERIDIAN-ZEPHYR',
        'modelManifestVersion': '1',
        'displayName':          'Meridian Zephyr',
        'modelLine':            'Zephyr',
        'vehicleType':          'Van',
        'powertrain':           'EV',
        'modelYears':           [2024, 2025],
        'platform':             'Meridian',
        'status':               'ACTIVE',
        'productionPhase':      'production',
        'description':          'Meridian Zephyr — full-EV Van (D22). No ECM — battery-electric; ECU_ENGINE and ECU_EVAP absent per D23.',
        'decoderManifestRef':   'meridian-zephyr-v1',
        'ecuConfigId':          'ECU-CONFIG-MERIDIAN-EV',
        'didProfileRef':        DID_PROFILE_EV,
        'ecus':                 list(_MERIDIAN_ECU_EV),
        'signalCount':          248,  # EV sport trim
        'vehicleCount':         0,
        'fleetIds':             [],
        'isDefault':            False,
    },
    {
        'modelManifestName':    'MERIDIAN-AZIMUTH',
        'modelManifestVersion': '1',
        'displayName':          'Meridian Azimuth',
        'modelLine':            'Azimuth',
        'vehicleType':          'Pickup',
        'powertrain':           'HYBRID',
        'modelYears':           [2022, 2025, 2026],
        'platform':             'Meridian',
        'status':               'ACTIVE',
        'productionPhase':      'production',
        'description':          'Meridian Azimuth — hybrid Pickup (D22). ECM + BMS + CCU: superset per D23. Carries both EV and powertrain signal groups.',
        'decoderManifestRef':   'meridian-azimuth-v1',
        'ecuConfigId':          'ECU-CONFIG-MERIDIAN-HYBRID',
        'didProfileRef':        DID_PROFILE_HYBRID,
        'ecus':                 list(_MERIDIAN_ECU_HYBRID),
        'signalCount':          232,  # Hybrid — ECM adds coverage; some EV telemetry richness lost
        'vehicleCount':         0,
        'fleetIds':             [],
        'isDefault':            False,
    },
    {
        'modelManifestName':    'MERIDIAN-SIROCCO',
        'modelManifestVersion': '1',
        'displayName':          'Meridian Sirocco',
        'modelLine':            'Sirocco',
        'vehicleType':          'Pickup',
        'powertrain':           'ICE_DIESEL',
        'modelYears':           [2023, 2024],
        'platform':             'Meridian',
        'status':               'ACTIVE',
        'productionPhase':      'production',
        'description':          'Meridian Sirocco — ICE-diesel Pickup (D22). ECM present (ECU_ENGINE). ECU_EVAP absent — EVAP is gasoline-only per D23/F11/DX30. DPF regen and reductant dosing apply.',
        'decoderManifestRef':   'meridian-sirocco-v1',
        'ecuConfigId':          'ECU-CONFIG-MERIDIAN-ICE-DIESEL',
        'didProfileRef':        DID_PROFILE_ICE_DIESEL,
        'ecus':                 list(_MERIDIAN_ECU_ICE_DIESEL),
        'signalCount':          215,  # ICE-diesel — no HV pack, no CCU, no EVAP
        'vehicleCount':         0,
        'fleetIds':             [],
        'isDefault':            False,
    },
    {
        'modelManifestName':    'MERIDIAN-MISTRAL',
        'modelManifestVersion': '1',
        'displayName':          'Meridian Mistral',
        'modelLine':            'Mistral',
        'vehicleType':          'Sedan',
        'powertrain':           'ICE_GASOLINE',
        'modelYears':           [2023, 2024],
        'platform':             'Meridian',
        'status':               'ACTIVE',
        'productionPhase':      'production',
        'description':          'Meridian Mistral — ICE-gasoline Sedan (D22). ECM present (ECU_ENGINE + ECU_EVAP). EVAP purge applies; diesel-only procedures absent.',
        'decoderManifestRef':   'meridian-mistral-v1',
        'ecuConfigId':          'ECU-CONFIG-MERIDIAN-ICE-GASOLINE',
        'didProfileRef':        DID_PROFILE_ICE_GASOLINE,
        'ecus':                 list(_MERIDIAN_ECU_ICE_GASOLINE),
        'signalCount':          210,  # ICE-gasoline entry trim — smallest signal set
        'vehicleCount':         0,
        'fleetIds':             [],
        'isDefault':            False,
    },
]


def put_model(m):
    name = m['modelManifestName']
    version = m['modelManifestVersion']
    item = {
        'pk':                    f'MODEL#{name}#{version}',
        'sk':                    f'MODEL#{name}',
        'modelManifestName':     name,
        'modelManifestVersion':  version,
        'displayName':           m['displayName'],
        'modelLine':             m['modelLine'],
        'platform':              m['platform'],
        'status':                m['status'],
        'productionPhase':       m['productionPhase'],
        'description':           m['description'],
        'decoderManifestRef':    m['decoderManifestRef'],
        'ecuConfigId':           m['ecuConfigId'],
        'ecus':                  m['ecus'],
        'vehicleCount':          m['vehicleCount'],
        'fleetIds':              m['fleetIds'],
        'isDefault':             m.get('isDefault', False),
        'createTimestamp':       NOW,
        'updateTimestamp':       NOW,
    }
    # signalCount is optional: Meridian placeholder manifests omit it so that
    # T5.2's derive_counts() is the single source of truth (D21/F9).
    # The universal CMS-FLEET-MODEL retains its pre-computed value.
    if 'signalCount' in m:
        item['signalCount'] = m['signalCount']
    # Optional per-manifest fields (Meridian-specific)
    for key in ('powertrain', 'vehicleType', 'modelYears', 'didProfileRef'):
        if key in m:
            item[key] = m[key]
    table.put_item(Item=item)
    return name, version


def main():
    print(f"Seeding cms-{STAGE}-model-manifest...")
    for m in MODEL_MANIFESTS:
        name, version = put_model(m)
        print(f"  ✅ {name} v{version}")
    print(f"Done — {len(MODEL_MANIFESTS)} model manifests seeded.")


if __name__ == '__main__':
    main()
