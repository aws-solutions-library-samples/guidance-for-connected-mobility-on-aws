#!/usr/bin/env python3
"""
Create IoT rule with existing VPC destination
"""

import boto3
import time
import os

def wait_for_vpc_destination():
    """Wait for VPC destination to be ready"""
    iot = boto3.client('iot')
    
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

def create_simple_iot_rule(destination_arn: str):
    """Create simple IoT rule"""
    iot = boto3.client('iot')
    
    try:
        # Delete existing rule if it exists
        try:
            iot.get_topic_rule(ruleName="cms_telemetry_to_msk")
            print("Deleting existing rule...")
            iot.delete_topic_rule(ruleName="cms_telemetry_to_msk")
            time.sleep(2)
        except:
            pass
        
        # Create simple rule
        rule_payload = {
            'sql': "SELECT * FROM 'topic/telemetry'",
            'description': 'Route telemetry data to MSK cluster',
            'actions': [{
                'kafka': {
                    'destinationArn': destination_arn,
                    'topic': 'cms-telemetry-raw',
                    'clientProperties': {
                        'bootstrap.servers': 'b-2.cmsdevmskcluster.m9bu3v.c23.kafka.us-east-1.amazonaws.com:9096,b-1.cmsdevmskcluster.m9bu3v.c23.kafka.us-east-1.amazonaws.com:9096',
                        'security.protocol': 'SASL_SSL',
                        'sasl.mechanism': 'SCRAM-SHA-512'
                    }
                }
            }],
            'ruleDisabled': False
        }
        
        iot.create_topic_rule(
            ruleName="cms_telemetry_to_msk",
            topicRulePayload=rule_payload
        )
        
        print("✅ Created IoT rule: cms_telemetry_to_msk")
        return True
        
    except Exception as e:
        print(f"❌ Error creating IoT rule: {e}")
        return False

def main():
    print("🔧 Creating IoT rule with VPC destination")
    
    # Wait for VPC destination to be ready
    destination_arn = wait_for_vpc_destination()
    
    if not destination_arn:
        return False
    
    # Create IoT rule
    success = create_simple_iot_rule(destination_arn)
    
    if success:
        print("🎉 IoT-MSK integration complete!")
        return True
    else:
        print("❌ Integration failed")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
