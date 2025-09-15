#!/usr/bin/env python3
"""
Create IoT rule with SSL configuration for MSK
"""

import os
import boto3
import json

def create_iot_rule_with_ssl():
    """Create IoT rule with SSL configuration"""
    
    profile_name = os.environ.get('AWS_PROFILE', 'default')
    session = boto3.Session(profile_name=profile_name)
    iot_client = session.client('iot', region_name='us-east-1')
    
    # Configuration
    vpc_destination_arn = "arn:aws:iot:us-east-1:470296731304:ruledestination/vpc/ffb5172a-e981-4c67-82be-852a80451dab"
    ssl_secret_name = "cms-msk-ssl-certificates"
    iot_role_arn = "arn:aws:iam::470296731304:role/IoTMSKVPCRole"
    rule_name = "cms_telemetry_to_msk_ssl"
    
    # Check VPC destination status
    try:
        dest_response = iot_client.get_topic_rule_destination(arn=vpc_destination_arn)
        status = dest_response['topicRuleDestination']['status']
        print(f"VPC destination status: {status}")
        
        if status != 'ENABLED':
            print("❌ VPC destination not ready yet. Please wait and try again.")
            return
            
    except Exception as e:
        print(f"❌ Failed to check VPC destination: {e}")
        return
    
    # Delete existing rule if it exists
    try:
        iot_client.delete_topic_rule(ruleName=rule_name)
        print(f"🗑️ Deleted existing rule: {rule_name}")
    except (iot_client.exceptions.ResourceNotFoundException, iot_client.exceptions.UnauthorizedException):
        print(f"ℹ️ Rule {rule_name} doesn't exist or no permission to delete, creating new one")
    
    # Create IoT rule with SSL configuration
    rule_payload = {
        'sql': "SELECT * FROM 'cms/telemetry/vehicle/+'",
        'description': 'Route CMS telemetry to MSK with SSL',
        'actions': [
            {
                'kafka': {
                    'destinationArn': vpc_destination_arn,
                    'topic': 'cms-telemetry-raw',
                    'key': '${topic(3)}',  # Use vehicle ID as partition key
                    'clientProperties': {
                        'bootstrap.servers': 'b-1.costoptimizedmsk.o2holf.c7.kafka.us-east-1.amazonaws.com:9094,b-2.costoptimizedmsk.o2holf.c7.kafka.us-east-1.amazonaws.com:9094',
                        'security.protocol': 'SSL',
                        'ssl.keystore': f"${{get_secret('{ssl_secret_name}', 'SecretString', 'keystore', '{iot_role_arn}')}}",
                        'ssl.keystore.password': f"${{get_secret('{ssl_secret_name}', 'SecretString', 'keystore_password', '{iot_role_arn}')}}",
                        'ssl.truststore': f"${{get_secret('{ssl_secret_name}', 'SecretString', 'truststore', '{iot_role_arn}')}}",
                        'ssl.truststore.password': f"${{get_secret('{ssl_secret_name}', 'SecretString', 'truststore_password', '{iot_role_arn}')}}"
                    }
                }
            }
        ],
        'ruleDisabled': False,
        'awsIotSqlVersion': '2016-03-23'
    }
    
    try:
        iot_client.create_topic_rule(
            ruleName=rule_name,
            topicRulePayload=rule_payload
        )
        print(f"✅ Created IoT rule: {rule_name}")
        print(f"   Topic pattern: cms/telemetry/vehicle/+")
        print(f"   VPC destination: {vpc_destination_arn}")
        print(f"   MSK topic: cms-telemetry-raw")
        print(f"   SSL configuration: Enabled")
        
    except Exception as e:
        print(f"❌ Failed to create IoT rule: {e}")
        return
    
    print("\n🎉 IoT rule with SSL created successfully!")
    print("Telemetry data will now flow: IoT Core → VPC Destination → MSK (SSL)")

def check_vpc_destination_status():
    """Check VPC destination status"""
    
    profile_name = os.environ.get('AWS_PROFILE', 'default')
    session = boto3.Session(profile_name=profile_name)
    iot_client = session.client('iot', region_name='us-east-1')
    
    vpc_destination_arn = "arn:aws:iot:us-east-1:470296731304:ruledestination/vpc/ffb5172a-e981-4c67-82be-852a80451dab"
    
    try:
        dest_response = iot_client.get_topic_rule_destination(arn=vpc_destination_arn)
        destination = dest_response['topicRuleDestination']
        
        print(f"VPC Destination Status: {destination['status']}")
        print(f"Status Reason: {destination.get('statusReason', 'N/A')}")
        print(f"Created: {destination.get('createdAt', 'N/A')}")
        print(f"Last Updated: {destination.get('lastUpdatedAt', 'N/A')}")
        
        if destination['status'] == 'ENABLED':
            print("✅ VPC destination is ready! You can create the IoT rule now.")
        else:
            print("⏳ VPC destination is still being created. Please wait...")
            
    except Exception as e:
        print(f"❌ Failed to check VPC destination: {e}")

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == 'status':
        check_vpc_destination_status()
    else:
        create_iot_rule_with_ssl()
