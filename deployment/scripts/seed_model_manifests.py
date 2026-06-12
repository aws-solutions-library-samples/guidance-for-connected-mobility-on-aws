"""
Seed cms-prod-model-manifest with the two FleetWise Vehicle Models for the
Engineering persona demo. Idempotent — re-running upserts the latest version row.

Uses the decoder-manifest schema convention:
  pk = MODEL#{name}#{version}
  sk = MODEL#{name}
"""

import os
import boto3
import os

# Resolve AWS account ID at runtime — never hardcode in source.
_ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID") or boto3.client("sts").get_caller_identity()["Account"]

from datetime import datetime, timezone

REGION = os.environ.get('AWS_REGION') or 'us-east-1'
PROFILE = os.environ.get('AWS_PROFILE', 'default')
STAGE = os.environ.get('DEPLOYMENT_STAGE', 'prod')

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
dynamodb = session.resource('dynamodb')
table = dynamodb.Table(f'cms-{STAGE}-model-manifest')

NOW = datetime.now(timezone.utc).isoformat()

# ECU baseline data for each model — matches mock-data-provider/engineering/ecus.ts
BE6_V12_ECUS = [
    {'ecu': 'TCU',  'displayName': 'Telematics Control Unit',     'baselineVersion': '4.1.0',  'signalCount': 18},
    {'ecu': 'BMS',  'displayName': 'Battery Management System',   'baselineVersion': '3.2.1',  'signalCount': 64},
    {'ecu': 'VCU',  'displayName': 'Vehicle Control Unit',        'baselineVersion': '7.4.2',  'signalCount': 42},
    {'ecu': 'BCM',  'displayName': 'Body Control Module',         'baselineVersion': '2.8.5',  'signalCount': 28},
    {'ecu': 'ADAS', 'displayName': 'ADAS Domain Controller',      'baselineVersion': '2.1.3',  'signalCount': 36},
    {'ecu': 'IVI',  'displayName': 'Infotainment & Cluster',      'baselineVersion': '14.2.0', 'signalCount': 12},
    {'ecu': 'GW',   'displayName': 'Central Gateway',             'baselineVersion': '1.5.0',  'signalCount': 8},
    {'ecu': 'CCU',  'displayName': 'Charger Control Unit',        'baselineVersion': '2.3.1',  'signalCount': 22},
]

BE07_V13_ECUS = [
    {'ecu': 'TCU',  'displayName': 'Telematics Control Unit',     'baselineVersion': '4.2.0-rc1',  'signalCount': 18},
    {'ecu': 'BMS',  'displayName': 'Battery Management System',   'baselineVersion': '3.3.0-rc2',  'signalCount': 66},
    {'ecu': 'VCU',  'displayName': 'Vehicle Control Unit',        'baselineVersion': '7.5.0-rc3',  'signalCount': 44},
    {'ecu': 'BCM',  'displayName': 'Body Control Module',         'baselineVersion': '2.9.0-beta', 'signalCount': 30},
    {'ecu': 'ADAS', 'displayName': 'ADAS Domain Controller',      'baselineVersion': '3.0.0-rc1',  'signalCount': 38},
    {'ecu': 'IVI',  'displayName': 'Infotainment & Cluster',      'baselineVersion': '15.0.0-rc2', 'signalCount': 14},
    {'ecu': 'GW',   'displayName': 'Central Gateway',             'baselineVersion': '1.6.0-rc1',  'signalCount': 10},
    {'ecu': 'CCU',  'displayName': 'Charger Control Unit',        'baselineVersion': '2.4.0-rc2',  'signalCount': 24},
]

MODEL_MANIFESTS = [
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
        'signalCatalogArn':     'arn:aws:iotfleetwise:us-east-1:{}:signal-catalog/cms-prod-vss',
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
    {
        'modelManifestName':    'BE6-V12-PROD',
        'modelManifestVersion': '12',
        'displayName':          'BE 6 — Production v12',
        'modelLine':            'BE 6',
        'platform':             'INGLO',
        'status':               'ACTIVE',
        'productionPhase':      'production',
        'description':          (
            'Production vehicle model for the in-market BE 6 cohort. Manifests every '
            'signal emitted by 200 production vehicles operating across India. Updated '
            '2026-05-19 by Build #4823 — added 2 BMS-produced signals '
            '(ThermalCompensationFactor, DerateActiveSeconds) for thermal-derate observability.'
        ),
        'decoderManifestRef':   'cms-prod-decoder-manifest-v17',
        'signalCatalogArn':     'arn:aws:iotfleetwise:us-east-1:{}:signal-catalog/cms-prod-vss',
        'ecuConfigId':          'ECU-CONFIG-BE6-V12-PROD',
        'ecus':                 BE6_V12_ECUS,
        'signalCount':          sum(e['signalCount'] for e in BE6_V12_ECUS),
        'vehicleCount':         200,
        'fleetIds':             ['be6-prod-cohort-001'],
    },
    {
        'modelManifestName':    'BE07-V13-DEV',
        'modelManifestVersion': '13',
        'displayName':          'BE.07 — Validation v13',
        'modelLine':            'BE.07',
        'platform':             'INGLO',
        'status':               'DRAFT',
        'productionPhase':      'validation',
        'description':          (
            'Engineering / validation vehicle model for the BE.07 prototype fleet. '
            'Includes additional development-only signals (instrumented telemetry tier) '
            'not present in the production BE 6 model. Used by the 25-vehicle Pune R&D '
            'validation fleet. Iterates with each rc1/rc2/rc3 firmware release.'
        ),
        'decoderManifestRef':   'cms-be07-decoder-manifest-v23',
        'signalCatalogArn':     'arn:aws:iotfleetwise:us-east-1:{}:signal-catalog/cms-prod-vss',
        'ecuConfigId':          'ECU-CONFIG-BE07-V13-DEV',
        'ecus':                 BE07_V13_ECUS,
        'signalCount':          sum(e['signalCount'] for e in BE07_V13_ECUS),
        'vehicleCount':         25,
        'fleetIds':             ['be07-test-fleet-001'],
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
        'signalCatalogArn':      m['signalCatalogArn'],
        'ecuConfigId':           m['ecuConfigId'],
        'ecus':                  m['ecus'],
        'signalCount':           m['signalCount'],
        'vehicleCount':          m['vehicleCount'],
        'fleetIds':              m['fleetIds'],
        'isDefault':             m.get('isDefault', False),
        'createTimestamp':       NOW,
        'updateTimestamp':       NOW,
    }
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
