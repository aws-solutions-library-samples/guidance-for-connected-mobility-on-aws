#!/usr/bin/env python3
import boto3
import sys

def rename_table(old_name, new_name, region='us-east-1'):
    dynamodb = boto3.resource('dynamodb', region_name=region)
    client = boto3.client('dynamodb', region_name=region)
    
    old_table = dynamodb.Table(old_name)
    
    # Get table schema
    response = client.describe_table(TableName=old_name)
    table_def = response['Table']
    
    # Create new table with same schema
    create_params = {
        'TableName': new_name,
        'KeySchema': table_def['KeySchema'],
        'AttributeDefinitions': table_def['AttributeDefinitions'],
        'BillingMode': table_def.get('BillingModeSummary', {}).get('BillingMode', 'PAY_PER_REQUEST')
    }
    
    client.create_table(**create_params)
    print(f"Created table: {new_name}")
    
    # Wait for table to be active
    waiter = client.get_waiter('table_exists')
    waiter.wait(TableName=new_name)
    
    # Copy data
    new_table = dynamodb.Table(new_name)
    scan_response = old_table.scan()
    
    with new_table.batch_writer() as batch:
        for item in scan_response['Items']:
            batch.put_item(Item=item)
    
    print(f"Copied {len(scan_response['Items'])} items")
    print(f"Update your application to use: {new_name}")
    print(f"Then delete old table: aws dynamodb delete-table --table-name {old_name}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python rename-table.py OLD_TABLE_NAME NEW_TABLE_NAME")
        sys.exit(1)
    
    rename_table(sys.argv[1], sys.argv[2])
