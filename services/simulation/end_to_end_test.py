#!/usr/bin/env python3
"""
End-to-End Fleet Management Test
Creates test fleet, vehicles, certificates and sends telemetry data
"""

import boto3
import json
import time
import uuid
from datetime import datetime, timezone
import random
import gzip
import base64
import paho.mqtt.client as mqtt

# Configuration
AWS_REGION = 'us-east-1'
AWS_PROFILE = 'givenand-CMS'
IOT_ENDPOINT = 'a3m15yqfy6j3pe-ats.iot.us-east-1.amazonaws.com'

# Test data
TEST_FLEET_ID = f"test-fleet-{int(time.time())}"
TEST_VEHICLE_ID = f"TEST-VEHICLE-{int(time.time())}"
TEST_TRIP_ID = f"TRIP-{TEST_VEHICLE_ID}-{int(time.time())}"

def setup_aws_clients():
    """Setup AWS clients with profile"""
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    return {
        'dynamodb': session.client('dynamodb'),
        'iot': session.client('iot'),
        'iot_data': session.client('iot-data', endpoint_url=f'https://{IOT_ENDPOINT}')
    }

def create_test_fleet(dynamodb_client):
    """Create test fleet in DynamoDB"""
    print(f"🚛 Creating test fleet: {TEST_FLEET_ID}")
    
    try:
        dynamodb_client.put_item(
            TableName='cms-dev-storage-fleets',
            Item={
                'fleetId': {'S': TEST_FLEET_ID},
                'fleetName': {'S': 'End-to-End Test Fleet'},
                'description': {'S': 'Automated test fleet for E2E validation'},
                'createdAt': {'S': datetime.now(timezone.utc).isoformat()},
                'status': {'S': 'ACTIVE'},
                'vehicleCount': {'N': '1'}
            }
        )
        print(f"✅ Fleet created: {TEST_FLEET_ID}")
        return True
    except Exception as e:
        print(f"❌ Failed to create fleet: {e}")
        return False

def create_test_vehicle(dynamodb_client):
    """Create test vehicle in DynamoDB"""
    print(f"🚗 Creating test vehicle: {TEST_VEHICLE_ID}")
    
    try:
        dynamodb_client.put_item(
            TableName='cms-dev-storage-vehicles',
            Item={
                'vehicleId': {'S': TEST_VEHICLE_ID},
                'fleetId': {'S': TEST_FLEET_ID},
                'make': {'S': 'Tesla'},
                'model': {'S': 'Model 3'},
                'year': {'N': '2023'},
                'vin': {'S': f'TEST{TEST_VEHICLE_ID[-10:]}'},
                'status': {'S': 'ACTIVE'},
                'createdAt': {'S': datetime.now(timezone.utc).isoformat()},
                'lastSeen': {'S': datetime.now(timezone.utc).isoformat()}
            }
        )
        print(f"✅ Vehicle created: {TEST_VEHICLE_ID}")
        return True
    except Exception as e:
        print(f"❌ Failed to create vehicle: {e}")
        return False

def create_vehicle_certificate(iot_client):
    """Create IoT certificate for vehicle"""
    print(f"🔐 Creating certificate for vehicle: {TEST_VEHICLE_ID}")
    
    try:
        # Create certificate
        cert_response = iot_client.create_keys_and_certificate(setAsActive=True)
        cert_arn = cert_response['certificateArn']
        cert_id = cert_response['certificateId']
        
        # Create policy
        policy_name = f"TestVehiclePolicy-{TEST_VEHICLE_ID}"
        policy_doc = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["iot:Connect", "iot:Publish"],
                    "Resource": "*"
                }
            ]
        }
        
        iot_client.create_policy(
            policyName=policy_name,
            policyDocument=json.dumps(policy_doc)
        )
        
        # Attach policy to certificate
        iot_client.attach_policy(
            policyName=policy_name,
            target=cert_arn
        )
        
        # Save certificate files
        cert_file = f"{TEST_VEHICLE_ID}-certificate.pem.crt"
        key_file = f"{TEST_VEHICLE_ID}-private.pem.key"
        
        with open(cert_file, 'w') as f:
            f.write(cert_response['certificatePem'])
        
        with open(key_file, 'w') as f:
            f.write(cert_response['keyPair']['PrivateKey'])
        
        print(f"✅ Certificate created: {cert_id}")
        return cert_file, key_file
        
    except Exception as e:
        print(f"❌ Failed to create certificate: {e}")
        return None, None

def generate_telemetry_data():
    """Generate realistic telemetry data"""
    base_lat, base_lon = 40.7128, -74.0060  # NYC coordinates
    
    return {
        "messageType": "TELEMETRY",
        "vehicleId": TEST_VEHICLE_ID,
        "tripId": TEST_TRIP_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "location": {
            "latitude": base_lat + random.uniform(-0.01, 0.01),
            "longitude": base_lon + random.uniform(-0.01, 0.01),
            "altitude": random.uniform(0, 100)
        },
        "speed": random.uniform(0, 60),
        "heading": random.uniform(0, 360),
        "odometer": random.uniform(10000, 50000),
        "fuelLevel": random.uniform(0.1, 1.0),
        "engineRpm": random.uniform(800, 3000),
        "engineTemp": random.uniform(80, 110),
        "batteryVoltage": random.uniform(12.0, 14.4),
        "diagnostics": {
            "engineStatus": "NORMAL",
            "transmissionStatus": "NORMAL",
            "brakeStatus": "NORMAL"
        },
        "driverBehavior": {
            "harshAcceleration": False,
            "harshBraking": False,
            "rapidTurns": False
        }
    }

def compress_and_encode(data):
    """Compress and base64 encode data for IoT Basic Ingest"""
    json_str = json.dumps(data)
    compressed = gzip.compress(json_str.encode('utf-8'))
    encoded = base64.b64encode(compressed).decode('utf-8')
    return encoded

def send_telemetry_via_mqtt(cert_file, key_file, num_messages=10):
    """Send telemetry data via MQTT"""
    print(f"📡 Sending {num_messages} telemetry messages via MQTT...")
    
    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            print("✅ Connected to IoT Core")
        else:
            print(f"❌ Failed to connect: {rc}")
    
    def on_publish(client, userdata, mid):
        print(f"📤 Message {mid} published successfully")
    
    try:
        client = mqtt.Client(client_id=TEST_VEHICLE_ID)
        client.on_connect = on_connect
        client.on_publish = on_publish
        
        # Configure TLS
        client.tls_set(ca_certs="AmazonRootCA1.pem", 
                      certfile=cert_file, 
                      keyfile=key_file)
        
        # Connect
        client.connect(IOT_ENDPOINT, 8883, 60)
        client.loop_start()
        
        time.sleep(2)  # Wait for connection
        
        # Send telemetry messages
        for i in range(num_messages):
            telemetry = generate_telemetry_data()
            encoded_payload = compress_and_encode(telemetry)
            
            result = client.publish("$aws/rules/cms_dev_iot_msk_rule", encoded_payload)
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                print(f"📡 {TEST_VEHICLE_ID}: Published message {i+1} to IoT rule")
            else:
                print(f"❌ {TEST_VEHICLE_ID}: Failed to publish message {i+1}")
            
            time.sleep(2)  # Wait between messages
        
        client.loop_stop()
        client.disconnect()
        print(f"✅ Trip {TEST_TRIP_ID} completed - {num_messages} messages sent")
        
    except Exception as e:
        print(f"❌ MQTT error: {e}")

def verify_data_in_tables(dynamodb_client):
    """Check if data was written to DynamoDB tables"""
    print("🔍 Verifying data in DynamoDB tables...")
    
    # Check telemetry table
    try:
        response = dynamodb_client.scan(
            TableName='cms-dev-storage-telemetry',
            FilterExpression='vehicleId = :vid',
            ExpressionAttributeValues={':vid': {'S': TEST_VEHICLE_ID}},
            Limit=5
        )
        
        telemetry_count = response['Count']
        print(f"📊 Telemetry records found: {telemetry_count}")
        
        if telemetry_count > 0:
            print("✅ Telemetry data successfully written to DynamoDB!")
            # Show sample record
            sample = response['Items'][0]
            print(f"   Sample record: vehicleId={sample.get('vehicleId', {}).get('S', 'N/A')}, "
                  f"timestamp={sample.get('timestamp', {}).get('N', 'N/A')}")
        else:
            print("⚠️  No telemetry records found yet (may still be processing)")
            
    except Exception as e:
        print(f"❌ Error checking telemetry table: {e}")
    
    # Check trips table
    try:
        response = dynamodb_client.scan(
            TableName='cms-dev-storage-trips',
            FilterExpression='tripId = :tid',
            ExpressionAttributeValues={':tid': {'S': TEST_TRIP_ID}},
            Limit=5
        )
        
        trip_count = response['Count']
        print(f"🚗 Trip records found: {trip_count}")
        
    except Exception as e:
        print(f"❌ Error checking trips table: {e}")

def main():
    """Main test execution"""
    print("🚀 Starting End-to-End Fleet Management Test")
    print("=" * 50)
    
    # Setup AWS clients
    clients = setup_aws_clients()
    
    # Step 1: Create test fleet
    if not create_test_fleet(clients['dynamodb']):
        return
    
    # Step 2: Create test vehicle
    if not create_test_vehicle(clients['dynamodb']):
        return
    
    # Step 3: Create vehicle certificate
    cert_file, key_file = create_vehicle_certificate(clients['iot'])
    if not cert_file:
        return
    
    # Step 4: Send telemetry data
    send_telemetry_via_mqtt(cert_file, key_file, num_messages=15)
    
    # Step 5: Wait for processing
    print("⏳ Waiting 60 seconds for data processing...")
    time.sleep(60)
    
    # Step 6: Verify data
    verify_data_in_tables(clients['dynamodb'])
    
    print("\n🎉 End-to-End test completed!")
    print(f"Test Fleet ID: {TEST_FLEET_ID}")
    print(f"Test Vehicle ID: {TEST_VEHICLE_ID}")
    print(f"Test Trip ID: {TEST_TRIP_ID}")

if __name__ == "__main__":
    main()
