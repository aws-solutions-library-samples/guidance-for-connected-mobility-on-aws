#!/usr/bin/env python3
"""
Simplified IoT-MSK Integration - Direct rule creation without VPC destination
"""

import boto3
import json
import os

def get_stack_outputs(stack_name: str):
    cf = boto3.client('cloudformation')
    try:
        response = cf.describe_stacks(StackName=stack_name)
        outputs = {}
        if 'Outputs' in response['Stacks'][0]:
            for output in response['Stacks'][0]['Outputs']:
                outputs[output['OutputKey']] = output['OutputValue']
        return outputs
    except Exception as e:
        print(f"Error getting stack outputs: {e}")
        return {}

def get_msk_bootstrap_servers(cluster_arn: str) -> str:
    kafka = boto3.client('kafka')
    try:
        response = kafka.get_bootstrap_brokers(ClusterArn=cluster_arn)
        return response.get('BootstrapBrokerStringSaslScram', response.get('BootstrapBrokerString'))
    except Exception as e:
        print(f"Error getting bootstrap servers: {e}")
        raise

def create_iot_rule_direct(rule_name: str, cluster_arn: str, bootstrap_servers: str, secret_arn: str):
    """Create IoT rule with direct MSK integration (no VPC destination needed)"""
    iot = boto3.client('iot')
    
    try:
        # Delete existing rule if it exists
        try:
            iot.get_topic_rule(ruleName=rule_name)
            print(f"Deleting existing rule: {rule_name}")
            iot.delete_topic_rule(ruleName=rule_name)
        except:
            pass
        
        # Create new rule
        rule_payload = {
            'sql': "SELECT * FROM 'topic/telemetry'",
            'description': 'Route telemetry data to MSK cluster',
            'actions': [{
                'kafka': {
                    'destinationArn': cluster_arn,
                    'topic': 'cms-telemetry-raw',
                    'clientProperties': {
                        'bootstrap.servers': bootstrap_servers,
                        'security.protocol': 'SASL_SSL',
                        'sasl.mechanism': 'SCRAM-SHA-512'
                    }
                }
            }],
            'ruleDisabled': False
        }
        
        iot.create_topic_rule(
            ruleName=rule_name,
            topicRulePayload=rule_payload
        )
        
        print(f"✅ Created IoT rule: {rule_name}")
        return True
        
    except Exception as e:
        print(f"❌ Error creating IoT rule: {e}")
        return False

def main():
    deployment_stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
    
    print(f"🚀 Simple IoT-MSK Integration for stage: {deployment_stage}")
    
    # Get stack outputs
    msk_outputs = get_stack_outputs(f"cms-{deployment_stage}-msk")
    
    if not msk_outputs.get('MSKClusterArn'):
        print("❌ MSK stack not found")
        return False
    
    cluster_arn = msk_outputs['MSKClusterArn']
    secret_arn = msk_outputs.get('IoTUserSecretArn', '')
    
    print("🔍 Getting MSK bootstrap servers...")
    bootstrap_servers = get_msk_bootstrap_servers(cluster_arn)
    print(f"✅ Bootstrap servers: {bootstrap_servers}")
    
    # Create IoT rule
    success = create_iot_rule_direct(
        rule_name="cms_telemetry_to_msk",
        cluster_arn=cluster_arn,
        bootstrap_servers=bootstrap_servers,
        secret_arn=secret_arn
    )
    
    if success:
        print("🎉 IoT-MSK integration completed!")
        return True
    else:
        print("❌ Integration failed")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
