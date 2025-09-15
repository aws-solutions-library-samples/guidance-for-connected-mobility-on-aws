import json
import boto3
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal

def lambda_handler(event, context):
    """Process IoT lifecycle events and update vehicle connection status"""
    
    dynamodb = boto3.resource('dynamodb')
    vehicles_table_name = os.environ.get('VEHICLES_TABLE_NAME')
    
    if not vehicles_table_name:
        print("❌ VEHICLES_TABLE_NAME environment variable not set")
        return {'statusCode': 400, 'body': 'Missing environment variable'}
    
    vehicles_table = dynamodb.Table(vehicles_table_name)
    
    try:
        # Process SQS messages from IoT lifecycle events
        for record in event.get('Records', []):
            if record.get('eventSource') == 'aws:sqs':
                # Parse SQS message body
                message_body = json.loads(record['body'])
                
                # Extract IoT event details
                client_id = message_body.get('clientId', '')
                event_type = message_body.get('eventType', '')
                timestamp = message_body.get('timestamp', '')
                
                print(f"📡 Processing IoT event: {event_type} for {client_id}")
                
                # Extract vehicle ID from client ID (format: vehicle-VIN or VIN)
                if client_id.startswith('vehicle-'):
                    vin = client_id[8:]  # Remove 'vehicle-' prefix
                else:
                    vin = client_id
                
                # Find vehicle by VIN
                vehicle_id = find_vehicle_by_vin(vehicles_table, vin)
                if not vehicle_id:
                    print(f"⚠️ Vehicle not found for VIN: {vin}")
                    continue
                
                # Update vehicle connection status
                now = datetime.now(timezone.utc)
                
                if event_type == 'connected':
                    # Vehicle connected
                    update_vehicle_status(vehicles_table, vehicle_id, {
                        'connectionStatus': 'connected',
                        'activityStatus': 'active',
                        'lastConnected': now.isoformat(),
                        'updatedAt': now.isoformat()
                    })
                    print(f"✅ Updated {vehicle_id} to connected and active")
                    
                elif event_type == 'disconnected':
                    # Vehicle disconnected
                    update_vehicle_status(vehicles_table, vehicle_id, {
                        'connectionStatus': 'disconnected',
                        'activityStatus': 'inactive',
                        'lastDisconnected': now.isoformat(),
                        'updatedAt': now.isoformat()
                    })
                    print(f"🔌 Updated {vehicle_id} to disconnected and inactive")
        
        return {'statusCode': 200, 'body': 'Successfully processed IoT lifecycle events'}
        
    except Exception as e:
        print(f"❌ Error processing IoT lifecycle events: {str(e)}")
        return {'statusCode': 500, 'body': f'Error: {str(e)}'}

def find_vehicle_by_vin(table, vin):
    """Find vehicle ID by VIN"""
    try:
        # Scan for vehicle with matching VIN
        response = table.scan(
            FilterExpression='vin = :vin',
            ExpressionAttributeValues={':vin': vin},
            ProjectionExpression='vehicleId'
        )
        
        items = response.get('Items', [])
        if items:
            return items[0]['vehicleId']
        return None
        
    except Exception as e:
        print(f"❌ Error finding vehicle by VIN {vin}: {e}")
        return None

def update_vehicle_status(table, vehicle_id, updates):
    """Update vehicle connection status"""
    try:
        # Build update expression
        update_expr = "SET "
        expr_values = {}
        expr_names = {}
        
        for key, value in updates.items():
            if key == 'connectionStatus':
                update_expr += "#cs = :cs, "
                expr_names['#cs'] = 'connectionStatus'
                expr_values[':cs'] = value
            elif key == 'activityStatus':
                update_expr += "#as = :as, "
                expr_names['#as'] = 'activityStatus'
                expr_values[':as'] = value
            else:
                update_expr += f"{key} = :{key}, "
                expr_values[f":{key}"] = value
        
        update_expr = update_expr.rstrip(', ')
        
        table.update_item(
            Key={'vehicleId': vehicle_id},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values,
            ExpressionAttributeNames=expr_names if expr_names else None
        )
        
    except Exception as e:
        print(f"❌ Error updating vehicle {vehicle_id}: {e}")
        raise
