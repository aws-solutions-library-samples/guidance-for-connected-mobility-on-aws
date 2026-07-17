"""
Seed cms-{stage}-model-manifest with the default FleetWise Vehicle Model.
Idempotent — re-running upserts the latest version row.

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
