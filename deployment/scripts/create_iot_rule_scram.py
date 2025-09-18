#!/usr/bin/env python3
"""
Create IoT rule with SCRAM authentication for givenand-CMS profile
"""

import boto3
import time
import json
import os

def wait_for_vpc_destination():
    """Wait for VPC destination to be ready"""
    iot = boto3.client('iot', region_name='us-east-1')
    
    for i in range(30):  # Wait up to 5 minutes
        destinations = iot.list_topic_rule_destinations()
        for dest in destinations.get('destinationSummaries', []):
            if dest['status'] == 'ENABLED':
                print(f"✅ VPC destination ready: {dest['arn']}")
                return dest['arn']
            elif dest['status'] == 'IN_PROGRESS':
                print(f"⏳ VPC destination still creating... ({i+1}/30)")
                time.sleep(10)
                break
        else:
            print("❌ No VPC destination found")
            return None
    
    print("❌ VPC destination creation timed out")
    return None

def create_iot_rule_with_scram(destination_arn: str):
    """Create IoT rule with SCRAM authentication"""
    iot = boto3.client('iot', region_name='us-east-1')
    
    try:
        # Delete existing rule if it exists
        try:
            iot.get_topic_rule(ruleName="cms_telemetry_to_msk_scram")
            print("Deleting existing rule...")
            iot.delete_topic_rule(ruleName="cms_telemetry_to_msk_scram")
            time.sleep(2)
        except:
            pass
        
        # Create rule with SCRAM authentication using VPC connectivity endpoints
        rule_payload = {
            'sql': "SELECT * FROM 'topic/telemetry'",
            'description': 'Route telemetry data to MSK cluster with SCRAM auth',
            'actions': [{
                'kafka': {
                    'destinationArn': destination_arn,
                    'topic': 'cms-telemetry-raw',
                    'clientProperties': {
                        'bootstrap.servers': os.environ.get('MSK_BOOTSTRAP_SERVERS', 'localhost:9092'),
                        'security.protocol': 'SASL_SSL',
                        'sasl.mechanism': 'SCRAM-SHA-512'
                    }
                }
            }],
            'ruleDisabled': False
        }
        
        iot.create_topic_rule(
            ruleName="cms_telemetry_to_msk_scram",
            topicRulePayload=rule_payload
        )
        
        print("✅ Created IoT rule with SCRAM: cms_telemetry_to_msk_scram")
        print("📊 Configuration:")
        print(f"   Bootstrap servers: {rule_payload['actions'][0]['kafka']['clientProperties']['bootstrap.servers']}")
        print(f"   Security protocol: {rule_payload['actions'][0]['kafka']['clientProperties']['security.protocol']}")
        print(f"   SASL mechanism: {rule_payload['actions'][0]['kafka']['clientProperties']['sasl.mechanism']}")
        return True
        
    except Exception as e:
        print(f"❌ Error creating IoT rule: {e}")
        return False

def test_iot_rule():
    """Test the IoT rule by publishing a message"""
    iot_data = boto3.client('iot-data', region_name='us-east-1')
    
    try:
        test_message = {
            "timestamp": int(time.time()),
            "vehicle_id": "test-vehicle-001",
            "location": {"lat": 40.7128, "lon": -74.0060},
            "speed": 45.5,
            "fuel_level": 0.75,
            "test": True
        }
        
        iot_data.publish(
            topic='topic/telemetry',
            qos=1,
            payload=json.dumps(test_message)
        )
        
        print("✅ Published test message to topic/telemetry")
        print(f"📨 Message: {json.dumps(test_message, indent=2)}")
        return True
        
    except Exception as e:
        print(f"❌ Error publishing test message: {e}")
        return False

def main():
    print("🔧 Creating IoT rule with SCRAM authentication")
    print("🎯 Using givenand-CMS profile MSK cluster")
    
    # Wait for VPC destination to be ready
    destination_arn = wait_for_vpc_destination()
    
    if not destination_arn:
        print("❌ No VPC destination available. Please deploy MSK stack first.")
        return False
    
    # Create IoT rule with SCRAM
    success = create_iot_rule_with_scram(destination_arn)
    
    if success:
        print("\n🎉 IoT-MSK SCRAM integration complete!")
        
        # Test the rule
        print("\n🧪 Testing IoT rule...")
        test_success = test_iot_rule()
        
        if test_success:
            print("\n✅ Test message published successfully!")
            print("💡 Check MSK topic 'cms-telemetry-raw' for the message")
        
        return True
    else:
        print("❌ Integration failed")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
