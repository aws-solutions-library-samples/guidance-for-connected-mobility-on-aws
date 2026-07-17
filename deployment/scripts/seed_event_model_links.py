"""
Tag each event in cms-prod-event-catalog with `applicableModels`: which
vehicle models can emit this event.

Heuristics:
  - ICE-only events (engine misfire, oil/fuel-mixture, transmission, etc.):
    only the CMS-FLEET-MODEL (mixed fleet, contains ICE vehicles).
  - EV-specific events (ev_battery_*): all 3 models (CMS, BE6, BE07).
  - Generic events (tires, brakes, harsh-driving, AEB, collision, etc.):
    all 3 models.

Idempotent — re-running updates each event in place.
"""

import os
import boto3
from datetime import datetime, timezone

REGION = os.environ.get('AWS_REGION') or 'us-east-1'
PROFILE = os.environ.get('AWS_PROFILE', 'default')
STAGE = os.environ.get('DEPLOYMENT_STAGE', 'prod')

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
dynamodb = session.resource('dynamodb')
events_table = dynamodb.Table(f'cms-{STAGE}-event-catalog')

ALL_MODELS = ['CMS-FLEET-MODEL', 'BE6-V12-PROD', 'BE07-V13-DEV']
CMS_ONLY = ['CMS-FLEET-MODEL']

# Substrings that mark ICE-only events (won't apply to BE 6 / BE.07 EVs).
ICE_ONLY_PATTERNS = [
    'engine_misfire',
    'engine_overspeed',
    'high_engine_temp',
    'oil_life',
    'oil_pressure',
    'low_fuel',
    'transmission_',
    'lean_fuel',
    'fuel_mixture',
    'turbo_',
    'pcm_',
    'lost_comm_pcm',
    'catalyst_',
    'small_evap',
    'evap_',
    'ecm',
    'camshaft',
    'invalid_data_from_ecm',
]


def applicable_models(event_id: str) -> list[str]:
    if any(p in event_id for p in ICE_ONLY_PATTERNS):
        return CMS_ONLY
    return ALL_MODELS


def main():
    print(f"Tagging events in cms-{STAGE}-event-catalog with applicableModels...")
    updated = 0
    scan_kwargs: dict = {}
    while True:
        resp = events_table.scan(**scan_kwargs)
        for item in resp.get('Items', []):
            event_id = item.get('event_id')
            if not event_id:
                continue
            models = applicable_models(event_id)
            events_table.update_item(
                Key={'event_id': event_id},
                UpdateExpression='SET applicableModels = :m, applicableModelsUpdatedAt = :ts',
                ExpressionAttributeValues={
                    ':m': models,
                    ':ts': datetime.now(timezone.utc).isoformat(),
                },
            )
            scope = 'CMS-only' if models == CMS_ONLY else 'all 3 models'
            print(f"  ✅ {event_id:50s} → {scope}")
            updated += 1
        if 'LastEvaluatedKey' not in resp:
            break
        scan_kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
    print(f"\nDone — {updated} events tagged.")


if __name__ == '__main__':
    main()
