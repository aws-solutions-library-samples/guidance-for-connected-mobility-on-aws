#!/usr/bin/env python3

import boto3
import json
import time
import random

def generate_maintenance_alert():
    """Generate a realistic maintenance alert"""
    
    # Maintenance alert types with realistic scenarios
    maintenance_scenarios = [
        {
            "alertType": "OIL_CHANGE_DUE",
            "severity": "HIGH", 
            "message": "Oil change due soon: 15% remaining",
            "component": "Engine Oil",
            "priority": "High",
            "estimatedCost": 75.00,
            "recommendedAction": "Schedule oil change within 500 miles"
        },
        {
            "alertType": "BRAKE_WEAR_HIGH", 
            "severity": "MEDIUM",
            "message": "Brake pads showing significant wear: 25% remaining",
            "component": "Brake Pads",
            "priority": "Medium", 
            "estimatedCost": 250.00,
            "recommendedAction": "Inspect brake pads within 1000 miles"
        },
        {
            "alertType": "TIRE_PRESSURE_LOW",
            "severity": "MEDIUM",
            "message": "Front left tire pressure low: 28 PSI (recommended: 35 PSI)",
            "component": "Tire Pressure",
            "priority": "Medium",
            "estimatedCost": 0.00,
            "recommendedAction": "Check and inflate tire to proper pressure"
        },
        {
            "alertType": "FILTER_REPLACEMENT_DUE",
            "severity": "LOW",
            "message": "Air filter replacement due: 85% clogged",
            "component": "Air Filter", 
            "priority": "Low",
            "estimatedCost": 45.00,
            "recommendedAction": "Replace air filter at next service"
        }
    ]
    
    return random.choice(maintenance_scenarios)

def create_maintenance_alert_for_vehicle(vehicle_id, trip_id=None):
    """Create a maintenance alert for a specific vehicle"""
    
    dynamodb = boto3.client('dynamodb', region_name='us-east-1')
    
    alert = generate_maintenance_alert()
    timestamp = int(time.time() * 1000)
    
    # Create maintenance alert record
    alert_item = {
        'alertId': {'S': f"{alert['alertType']}-{timestamp}-{vehicle_id}"},
        'vehicleId': {'S': vehicle_id},
        'timestamp': {'N': str(timestamp)},
        'alertType': {'S': alert['alertType']},
        'severity': {'S': alert['severity']},
        'message': {'S': alert['message']},
        'component': {'S': alert['component']},
        'priority': {'S': alert['priority']},
        'estimatedCost': {'N': str(alert['estimatedCost'])},
        'recommendedAction': {'S': alert['recommendedAction']},
        'status': {'S': 'ACTIVE'},
        'createdBy': {'S': 'MaintenanceSimulator'}
    }
    
    # Add trip context if provided
    if trip_id:
        alert_item['tripId'] = {'S': trip_id}
    
    # Store in DynamoDB
    try:
        dynamodb.put_item(
            TableName='cms-dev-storage-maintenance-alerts',
            Item=alert_item
        )
        
        print(f"✅ Created maintenance alert: {alert['alertType']} for vehicle {vehicle_id}")
        return alert_item
        
    except Exception as e:
        print(f"❌ Failed to create maintenance alert: {e}")
        return None

if __name__ == "__main__":
    # Create a maintenance alert for the test vehicle
    vehicle_id = "VEH-1759246434"
    trip_id = "VEH-1759246434-1759257091581-39912607"
    
    print(f"🔧 Generating maintenance alert for vehicle {vehicle_id}")
    alert = create_maintenance_alert_for_vehicle(vehicle_id, trip_id)
    
    if alert:
        print(f"📋 Alert Details:")
        print(f"  Type: {alert['alertType']['S']}")
        print(f"  Severity: {alert['severity']['S']}")
        print(f"  Message: {alert['message']['S']}")
        print(f"  Component: {alert['component']['S']}")
        print(f"  Estimated Cost: ${alert['estimatedCost']['N']}")
