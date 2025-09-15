#!/usr/bin/env python3
"""
Setup DynamoDB telemetry table for CMS
"""

import boto3
import uuid

def create_telemetry_table(profile_name="target-account", region="us-east-1"):
    """Create DynamoDB telemetry table"""
    
    session = boto3.Session(profile_name=profile_name)
    dynamodb = session.client('dynamodb', region_name=region)
    
    # Generate unique ID for table name
    unique_id = str(uuid.uuid4())[:8]
    table_name = f"cms-{unique_id}-telemetry"
    
    try:
        print(f"📊 Creating DynamoDB table: {table_name}")
        
        response = dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {
                    'AttributeName': 'vehicleId',
                    'KeyType': 'HASH'  # Partition key
                },
                {
                    'AttributeName': 'timestamp',
                    'KeyType': 'RANGE'  # Sort key
                }
            ],
            AttributeDefinitions=[
                {
                    'AttributeName': 'vehicleId',
                    'AttributeType': 'S'
                },
                {
                    'AttributeName': 'timestamp',
                    'AttributeType': 'N'
                },
                {
                    'AttributeName': 'tripId',
                    'AttributeType': 'S'
                }
            ],
            GlobalSecondaryIndexes=[
                {
                    'IndexName': 'tripId-timestamp-index',
                    'KeySchema': [
                        {
                            'AttributeName': 'tripId',
                            'KeyType': 'HASH'
                        },
                        {
                            'AttributeName': 'timestamp',
                            'KeyType': 'RANGE'
                        }
                    ],
                    'Projection': {
                        'ProjectionType': 'ALL'
                    }
                }
            ],
            BillingMode='PAY_PER_REQUEST',
            Tags=[
                {
                    'Key': 'Project',
                    'Value': 'ConnectedMobility'
                },
                {
                    'Key': 'Environment',
                    'Value': 'Simulation'
                }
            ]
        )
        
        print(f"✅ Table created: {table_name}")
        print(f"📋 Table ARN: {response['TableDescription']['TableArn']}")
        
        # Save table name for reference
        with open('telemetry_table_name.txt', 'w') as f:
            f.write(table_name)
        
        return table_name
        
    except Exception as e:
        print(f"❌ Error creating table: {e}")
        return None

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Setup DynamoDB telemetry table')
    parser.add_argument('--profile', default='target-account', help='AWS profile name')
    parser.add_argument('--region', default='us-east-1', help='AWS region')
    
    args = parser.parse_args()
    
    table_name = create_telemetry_table(args.profile, args.region)
    if table_name:
        print(f"🎉 Telemetry table setup completed: {table_name}")
    else:
        print("❌ Telemetry table setup failed!")
