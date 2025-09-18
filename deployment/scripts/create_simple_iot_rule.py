#!/usr/bin/env python3
"""
Create simple IoT rule with SCRAM authentication (no VPC destination)
"""

import boto3
import time
import json

def get_msk_info():
    """Get MSK cluster information"""
    kafka = boto3.client('kafka', region_name='us-east-1')
    
    clusters = kafka.list_clusters_v2()
    if not clusters['ClusterInfoList']:
        print("❌ No MSK clusters found")
        return None
    
    cluster = clusters['ClusterInfoList'][0]
    cluster_arn = cluster['ClusterArn']
    
    # Get bootstrap brokers
    brokers = kafka.get_bootstrap_brokers(ClusterArn=cluster_arn)
    
    return {
        'cluster_arn': cluster_arn,
        'scram_brokers': brokers.get('BootstrapBrokerStringSaslScram', ''),
        'vpc_scram_brokers': brokers.get('BootstrapBrokerStringVpcConnectivitySaslScram', ''),
        'iam_brokers': brokers.get('BootstrapBrokerStringSaslIam', '')
    }

def create_simple_iot_rule():
    """Create simple IoT rule with SCRAM authentication"""
    iot = boto3.client('iot', region_name='us-east-1')
    
    # Get MSK info
    msk_info = get_msk_info()
    if not msk_info:
        return False
    
    try:
        # Delete existing rule if it exists
        try:
            iot.get_topic_rule(ruleName="cms_telemetry_to_msk_simple")
            print("Deleting existing rule...")
            iot.delete_topic_rule(ruleName="cms_telemetry_to_msk_simple")
            time.sleep(2)
        except:
            pass
        
        # Create simple rule with SCRAM authentication (no VPC destination)
        rule_payload = {
            'sql': "SELECT * FROM 'topic/telemetry'",
            'description': 'Route telemetry data to MSK cluster with SCRAM auth (simple)',
            'actions': [{
                'kafka': {
                    'destinationArn': msk_info['cluster_arn'],
                    'topic': 'cms-telemetry-raw',
                    'clientProperties': {
                        'bootstrap.servers': msk_info['scram_brokers'],
                        'security.protocol': 'SASL_SSL',
                        'sasl.mechanism': 'SCRAM-SHA-512'
                    }
                }
            }],
            'ruleDisabled': False
        }
        
        iot.create_topic_rule(
            ruleName="cms_telemetry_to_msk_simple",
            topicRulePayload=rule_payload
        )
        
        print("✅ Created IoT rule: cms_telemetry_to_msk_simple")
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
    print("🔧 Creating simple IoT rule with SCRAM authentication")
    print("🎯 Using givenand-CMS profile MSK cluster")
    
    # Create IoT rule with SCRAM
    success = create_simple_iot_rule()
    
    if success:
        print("\n🎉 IoT-MSK SCRAM integration complete!")
        
        # Test the rule
        print("\n🧪 Testing IoT rule...")
        test_success = test_iot_rule()
        
        if test_success:
            print("\n✅ Test message published successfully!")
            print("💡 Check MSK topic 'cms-telemetry-raw' for the message")
            print("📋 Use the generated test script on EC2 to verify message delivery")
        
        return True
    else:
        print("❌ Integration failed")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
