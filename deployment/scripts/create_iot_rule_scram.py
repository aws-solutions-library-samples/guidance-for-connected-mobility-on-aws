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
    """Create IoT rule with SCRAM authentication matching working configuration"""
    iot = boto3.client('iot', region_name='us-east-1')
    cf = boto3.client('cloudformation', region_name='us-east-1')
    
    try:
        # Get MSK cluster info and secret ARN from CloudFormation
        stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
        stack_name = f"cms-{stage}-msk"
        
        stack_outputs = cf.describe_stacks(StackName=stack_name)['Stacks'][0]['Outputs']
        secret_arn = next(o['OutputValue'] for o in stack_outputs if o['OutputKey'] == 'IoTUserSecretArn')
        cluster_arn = next(o['OutputValue'] for o in stack_outputs if o['OutputKey'] == 'MSKClusterArn')
        
        # Get bootstrap servers
        kafka = boto3.client('kafka', region_name='us-east-1')
        bootstrap_servers = kafka.get_bootstrap_brokers(ClusterArn=cluster_arn)['BootstrapBrokerStringSaslScram']
        
        # Delete existing rule if it exists
        rule_name = f"cms_{stage}_iot_msk_rule"
        try:
            iot.get_topic_rule(ruleName=rule_name)
            print(f"Deleting existing rule: {rule_name}")
            iot.delete_topic_rule(ruleName=rule_name)
            time.sleep(2)
        except:
            pass
        
        # Create rule matching working configuration
        rule_payload = {
            'sql': "SELECT *",
            'description': 'Rule to forward MQTT messages to MSK with SCRAM and S3 backup',
            'actions': [
                {
                    'kafka': {
                        'destinationArn': destination_arn,
                        'topic': 'cms-telemetry-raw',
                        'clientProperties': {
                            'bootstrap.servers': bootstrap_servers,
                            'security.protocol': 'SASL_SSL',
                            'sasl.mechanism': 'SCRAM-SHA-512',
                            'sasl.scram.username': f'${{get_secret("{secret_arn}", "SecretString", "username", "arn:aws:iam::{boto3.client("sts").get_caller_identity()["Account"]}:role/service-role/IoTCreateVpcENIRole-{stage}")}}',
                            'sasl.scram.password': f'${{get_secret("{secret_arn}", "SecretString", "password", "arn:aws:iam::{boto3.client("sts").get_caller_identity()["Account"]}:role/service-role/IoTCreateVpcENIRole-{stage}")}}'
                        }
                    }
                },
                {
                    's3': {
                        'roleArn': f'arn:aws:iam::{boto3.client("sts").get_caller_identity()["Account"]}:role/service-role/IoTCreateVpcENIRole-{stage}',
                        'bucketName': f'cms-{stage}-telemetry-backup-{boto3.client("sts").get_caller_identity()["Account"]}',
                        'key': 'raw-telemetry/year=${timestamp("yyyy")}/month=${timestamp("MM")}/day=${timestamp("dd")}/hour=${timestamp("HH")}/${clientId()}-${timestamp()}.json'
                    }
                }
            ],
            'errorAction': {
                'cloudwatchLogs': {
                    'roleArn': f'arn:aws:iam::{boto3.client("sts").get_caller_identity()["Account"]}:role/service-role/IoTCreateVpcENIRole-{stage}',
                    'logGroupName': '/aws/iot/rule/errors'
                }
            },
            'ruleDisabled': False
        }
        
        iot.create_topic_rule(
            ruleName=rule_name,
            topicRulePayload=rule_payload
        )
        
        print(f"✅ Created IoT rule: {rule_name}")
        print("📊 Configuration:")
        print(f"   Bootstrap servers: {bootstrap_servers}")
        print(f"   Security protocol: SASL_SSL")
        print(f"   SASL mechanism: SCRAM-SHA-512")
        print(f"   S3 backup: {rule_payload['actions'][1]['s3']['bucketName']}")
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
