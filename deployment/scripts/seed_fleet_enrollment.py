"""
Seed fleet enrollment table — enrolls all existing vehicles that have a fleetId
into the fleet enrollment table for indexed lookups.
"""
import boto3
import os
from datetime import datetime, timezone

STAGE = os.environ.get('DEPLOYMENT_STAGE', 'dev')
PROFILE = os.environ.get('AWS_PROFILE', 'default')
REGION = os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION') or 'us-east-1'

# Explicit region so the script works regardless of the profile's default
# region (profile-level region can differ from the stack's deployment region).
session = boto3.Session(profile_name=PROFILE, region_name=REGION)
dynamodb = session.resource('dynamodb')

vehicles_table = dynamodb.Table(f'cms-{STAGE}-storage-vehicles')
enrollment_table = dynamodb.Table(f'cms-{STAGE}-storage-fleet-enrollment')

def seed():
    print(f"📋 Scanning vehicles table for fleet assignments...")
    print(f"   Stage={STAGE}, Region={REGION}")
    enrolled = 0
    scan_kwargs = {}

    while True:
        response = vehicles_table.scan(**scan_kwargs)
        for vehicle in response['Items']:
            fleet_id = vehicle.get('fleetId')
            vehicle_id = vehicle['vehicleId']
            if fleet_id:
                enrollment_table.put_item(Item={
                    'PK': f'FLEET#{fleet_id}',
                    'SK': f'VEHICLE#{vehicle_id}',
                    'fleetId': fleet_id,
                    'vehicleId': vehicle_id,
                    'enrolledAt': datetime.now(timezone.utc).isoformat(),
                })
                enrolled += 1
                print(f"  ✅ {vehicle_id} → {fleet_id}")

        if 'LastEvaluatedKey' not in response:
            break
        scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']

    print(f"\n🎉 Enrolled {enrolled} vehicles into fleet enrollment table")

if __name__ == '__main__':
    seed()
