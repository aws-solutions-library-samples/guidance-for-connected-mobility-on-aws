import boto3
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal

def calculate_vehicle_statuses():
    """Calculate and update vehicle activity statuses based on connection history"""
    
    dynamodb = boto3.resource('dynamodb')
    vehicles_table_name = os.environ.get('VEHICLES_TABLE_NAME')
    
    if not vehicles_table_name:
        raise Exception("VEHICLES_TABLE_NAME environment variable not set")
    
    vehicles_table = dynamodb.Table(vehicles_table_name)
    now = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)
    
    try:
        # Scan all vehicles
        response = vehicles_table.scan()
        vehicles = response.get('Items', [])
        
        updated_count = 0
        
        for vehicle in vehicles:
            vehicle_id = vehicle['vehicleId']
            connection_status = vehicle.get('connectionStatus', 'unknown')
            last_connected_str = vehicle.get('lastConnected')
            
            # Determine activity status
            activity_status = 'inactive'  # Default
            
            if connection_status == 'connected':
                activity_status = 'connected'
            elif last_connected_str:
                try:
                    last_connected = datetime.fromisoformat(last_connected_str.replace('Z', '+00:00'))
                    if last_connected >= thirty_days_ago:
                        activity_status = 'active'
                except:
                    pass  # Keep as inactive if date parsing fails
            
            # Update vehicle if activity status changed
            current_activity = vehicle.get('activityStatus', 'inactive')
            if current_activity != activity_status:
                vehicles_table.update_item(
                    Key={'vehicleId': vehicle_id},
                    UpdateExpression='SET activityStatus = :status, updatedAt = :updated',
                    ExpressionAttributeValues={
                        ':status': activity_status,
                        ':updated': now.isoformat()
                    }
                )
                updated_count += 1
                print(f"📊 Updated {vehicle_id}: {current_activity} → {activity_status}")
        
        print(f"✅ Updated {updated_count} vehicle statuses")
        return updated_count
        
    except Exception as e:
        print(f"❌ Error calculating vehicle statuses: {e}")
        raise

def lambda_handler(event, context):
    """Lambda handler for scheduled status calculation"""
    try:
        updated_count = calculate_vehicle_statuses()
        return {
            'statusCode': 200,
            'body': f'Successfully updated {updated_count} vehicle statuses'
        }
    except Exception as e:
        return {
            'statusCode': 500,
            'body': f'Error: {str(e)}'
        }
