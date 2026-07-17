#!/usr/bin/env python3
"""
Cleanup old telemetry data from DynamoDB table.
Removes records older than specified days to manage storage costs.
"""

import boto3
import time
from datetime import datetime, timedelta
from decimal import Decimal

def cleanup_old_telemetry(table_name, days_to_keep=30, batch_size=25, dry_run=False):
    """
    Remove telemetry records older than specified days.
    
    Args:
        table_name: DynamoDB table name
        days_to_keep: Keep records newer than this many days (default: 30)
        batch_size: Number of items to delete per batch (max 25)
        dry_run: If True, only show what would be deleted
    """
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(table_name)
    
    # Calculate cutoff timestamp (days ago in milliseconds)
    cutoff_date = datetime.now() - timedelta(days=days_to_keep)
    cutoff_timestamp = int(cutoff_date.timestamp() * 1000)
    
    print(f"{'DRY RUN: ' if dry_run else ''}Cleaning up telemetry data older than {days_to_keep} days")
    print(f"Cutoff timestamp: {cutoff_timestamp} ({cutoff_date})")
    print(f"Current timestamp: {int(time.time() * 1000)} ({datetime.now()})")
    
    deleted_count = 0
    scanned_count = 0
    
    # Scan table for old records
    scan_kwargs = {
        'FilterExpression': boto3.dynamodb.conditions.Attr('timestamp').lt(cutoff_timestamp),
        'ProjectionExpression': 'vehicleId, #ts',
        'ExpressionAttributeNames': {'#ts': 'timestamp'}
    }
    
    while True:
        response = table.scan(**scan_kwargs)
        items = response.get('Items', [])
        scanned_count += len(items)
        
        if not items:
            break
            
        print(f"Found {len(items)} old records in this batch")
        
        if dry_run:
            # Just count and show sample
            deleted_count += len(items)
            if len(items) > 0:
                sample = items[0]
                sample_date = datetime.fromtimestamp(int(sample['timestamp']) / 1000)
                print(f"  Sample record: vehicleId={sample['vehicleId']}, timestamp={sample['timestamp']} ({sample_date})")
        else:
            # Delete items in batches
            for i in range(0, len(items), batch_size):
                batch = items[i:i + batch_size]
                
                with table.batch_writer() as batch_writer:
                    for item in batch:
                        batch_writer.delete_item(
                            Key={
                                'vehicleId': item['vehicleId'],
                                'timestamp': item['timestamp']
                            }
                        )
                        deleted_count += 1
                
                print(f"Deleted {deleted_count} records...")
                time.sleep(0.1)  # Rate limiting
        
        # Handle pagination
        if 'LastEvaluatedKey' not in response:
            break
        scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
    
    action = "Would delete" if dry_run else "Deleted"
    print(f"Cleanup complete. {action} {deleted_count} old records out of {scanned_count} scanned.")
    return deleted_count

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Cleanup old telemetry data')
    parser.add_argument('--table', default='cms-dev-storage-telemetry', 
                       help='DynamoDB table name')
    parser.add_argument('--days', type=int, default=30,
                       help='Keep records newer than this many days')
    parser.add_argument('--dry-run', action='store_true',
                       help='Show what would be deleted without deleting')
    
    args = parser.parse_args()
    
    cleanup_old_telemetry(args.table, args.days, dry_run=args.dry_run)
