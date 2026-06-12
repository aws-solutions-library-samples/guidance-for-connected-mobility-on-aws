#!/usr/bin/env python3
"""
Comprehensive cleanup script for old data in CMS DynamoDB tables.
Manages storage costs by removing old records based on configurable retention policies.
"""

import boto3
import time
from datetime import datetime, timedelta
from decimal import Decimal
import argparse

# Table configurations with default retention periods
TABLE_CONFIGS = {
    'cms-dev-storage-telemetry': {
        'retention_days': 30,
        'timestamp_field': 'timestamp',
        'description': 'Vehicle telemetry data'
    },
    'cms-dev-storage-trips': {
        'retention_days': 90,
        'timestamp_field': 'startTime',
        'description': 'Trip records'
    },
    'cms-dev-storage-safety-events': {
        'retention_days': 365,
        'timestamp_field': 'timestamp',
        'description': 'Safety events (kept longer for compliance)'
    },
    'cms-dev-storage-maintenance-alerts': {
        'retention_days': 180,
        'timestamp_field': 'timestamp',
        'description': 'Maintenance alerts'
    }
}

def cleanup_table_data(table_name, config, days_to_keep=None, batch_size=25, dry_run=False):
    """
    Remove old records from a specific table.
    
    Args:
        table_name: DynamoDB table name
        config: Table configuration dict
        days_to_keep: Override default retention period
        batch_size: Number of items to delete per batch (max 25)
        dry_run: If True, only show what would be deleted
    """
    dynamodb = boto3.resource('dynamodb')
    
    try:
        table = dynamodb.Table(table_name)
    except Exception as e:
        print(f"Error accessing table {table_name}: {e}")
        return 0
    
    # Use provided days or default from config
    retention_days = days_to_keep or config['retention_days']
    timestamp_field = config['timestamp_field']
    
    # Calculate cutoff timestamp (days ago in milliseconds)
    cutoff_date = datetime.now() - timedelta(days=retention_days)
    cutoff_timestamp = int(cutoff_date.timestamp() * 1000)
    
    print(f"\n{'DRY RUN: ' if dry_run else ''}Cleaning {table_name}")
    print(f"  Description: {config['description']}")
    print(f"  Retention: {retention_days} days")
    print(f"  Cutoff: {cutoff_date.strftime('%Y-%m-%d %H:%M:%S')}")
    
    deleted_count = 0
    scanned_count = 0
    
    # Get table key schema to identify primary key attributes
    table_info = table.meta.client.describe_table(TableName=table_name)
    key_schema = table_info['Table']['KeySchema']
    
    # Build projection expression for keys + timestamp
    key_attrs = [key['AttributeName'] for key in key_schema]
    if timestamp_field not in key_attrs:
        key_attrs.append(timestamp_field)
    
    projection_expr = ', '.join([f'#{attr}' if attr == 'timestamp' else attr for attr in key_attrs])
    expr_attr_names = {'#timestamp': 'timestamp'} if 'timestamp' in key_attrs else {}
    
    # Scan table for old records
    scan_kwargs = {
        'FilterExpression': boto3.dynamodb.conditions.Attr(timestamp_field).lt(cutoff_timestamp),
        'ProjectionExpression': projection_expr
    }
    if expr_attr_names:
        scan_kwargs['ExpressionAttributeNames'] = expr_attr_names
    
    try:
        while True:
            response = table.scan(**scan_kwargs)
            items = response.get('Items', [])
            scanned_count += len(items)
            
            if not items:
                break
                
            if dry_run:
                deleted_count += len(items)
                if len(items) > 0:
                    sample = items[0]
                    if timestamp_field in sample:
                        sample_date = datetime.fromtimestamp(int(sample[timestamp_field]) / 1000)
                        print(f"    Found {len(items)} old records (sample: {sample_date})")
                    else:
                        print(f"    Found {len(items)} old records")
            else:
                # Delete items in batches
                for i in range(0, len(items), batch_size):
                    batch = items[i:i + batch_size]
                    
                    with table.batch_writer() as batch_writer:
                        for item in batch:
                            # Build delete key from primary key attributes
                            delete_key = {attr: item[attr] for attr in key_attrs if attr in item}
                            batch_writer.delete_item(Key=delete_key)
                            deleted_count += 1
                    
                    print(f"    Deleted {deleted_count} records...")
                    time.sleep(0.1)  # Rate limiting
            
            # Handle pagination
            if 'LastEvaluatedKey' not in response:
                break
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
    
    except Exception as e:
        print(f"  Error processing {table_name}: {e}")
        return 0
    
    action = "Would delete" if dry_run else "Deleted"
    print(f"  Result: {action} {deleted_count} old records")
    return deleted_count

def main():
    parser = argparse.ArgumentParser(description='Cleanup old data from CMS tables')
    parser.add_argument('--table', help='Specific table to clean (default: all configured tables)')
    parser.add_argument('--days', type=int, help='Override retention days for all tables')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be deleted')
    parser.add_argument('--list-tables', action='store_true', help='List configured tables and exit')
    
    args = parser.parse_args()
    
    if args.list_tables:
        print("Configured tables:")
        for table_name, config in TABLE_CONFIGS.items():
            print(f"  {table_name}: {config['description']} ({config['retention_days']} days)")
        return
    
    total_deleted = 0
    
    if args.table:
        # Clean specific table
        if args.table in TABLE_CONFIGS:
            config = TABLE_CONFIGS[args.table]
            deleted = cleanup_table_data(args.table, config, args.days, dry_run=args.dry_run)
            total_deleted += deleted
        else:
            print(f"Error: Table {args.table} not in configuration")
            return
    else:
        # Clean all configured tables
        print(f"{'DRY RUN: ' if args.dry_run else ''}Starting cleanup of all configured tables...")
        
        for table_name, config in TABLE_CONFIGS.items():
            deleted = cleanup_table_data(table_name, config, args.days, dry_run=args.dry_run)
            total_deleted += deleted
    
    action = "Would delete" if args.dry_run else "Deleted"
    print(f"\nTotal: {action} {total_deleted} records across all tables")

if __name__ == "__main__":
    main()
