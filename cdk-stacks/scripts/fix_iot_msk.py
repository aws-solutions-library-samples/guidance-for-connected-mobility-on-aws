#!/usr/bin/env python3
"""
Fix IoT-MSK Integration - Create VPC destination and IoT rule
"""

import boto3
import json
import os
import time

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

def get_vpc_info():
    """Get default VPC information"""
    ec2 = boto3.client('ec2')
    
    # Get default VPC
    vpcs = ec2.describe_vpcs(Filters=[{'Name': 'is-default', 'Values': ['true']}])
    if not vpcs['Vpcs']:
        print("❌ No default VPC found")
        return None, [], []
    
    vpc_id = vpcs['Vpcs'][0]['VpcId']
    
    # Get subnets
    subnets = ec2.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
    subnet_ids = [subnet['SubnetId'] for subnet in subnets['Subnets']]
    
    # Get MSK security group
    sgs = ec2.describe_security_groups(
        Filters=[
            {'Name': 'vpc-id', 'Values': [vpc_id]},
            {'Name': 'group-name', 'Values': ['*MSK*']}
        ]
    )
    
    if not sgs['SecurityGroups']:
        # Get default security group as fallback
        sgs = ec2.describe_security_groups(
            Filters=[
                {'Name': 'vpc-id', 'Values': [vpc_id]},
                {'Name': 'group-name', 'Values': ['default']}
            ]
        )
    
    sg_ids = [sg['GroupId'] for sg in sgs['SecurityGroups']]
    
    return vpc_id, subnet_ids[:2], sg_ids  # Use first 2 subnets

def create_vpc_destination(vpc_id: str, subnet_ids: list, sg_ids: list, role_arn: str):
    """Create VPC destination for IoT rules"""
    iot = boto3.client('iot')
    
    try:
        # Check if destination already exists
        destinations = iot.list_topic_rule_destinations()
        for dest in destinations.get('destinationSummaries', []):
            if 'vpcConfiguration' in dest.get('destinationConfiguration', {}):
                vpc_config = dest['destinationConfiguration']['vpcConfiguration']
                if vpc_config.get('vpcId') == vpc_id:
                    print(f"✅ VPC destination already exists: {dest['arn']}")
                    return dest['arn']
        
        # Create new VPC destination
        response = iot.create_topic_rule_destination(
            destinationConfiguration={
                'vpcConfiguration': {
                    'vpcId': vpc_id,
                    'subnetIds': subnet_ids,
                    'securityGroups': sg_ids,
                    'roleArn': role_arn
                }
            }
        )
        
        destination_arn = response.get('destinationArn')
        if not destination_arn:
            print(f"❌ No destination ARN in response: {response}")
            return None
        print(f"✅ Created VPC destination: {destination_arn}")
        return destination_arn
        
    except Exception as e:
        print(f"❌ Error creating VPC destination: {e}")
        return None

def create_iot_rule_with_destination(rule_name: str, destination_arn: str, cluster_arn: str, bootstrap_servers: str, secret_arn: str, role_arn: str):
    """Create IoT rule with VPC destination"""
    iot = boto3.client('iot')
    
    try:
        # Delete existing rule if it exists
        try:
            iot.get_topic_rule(ruleName=rule_name)
            print(f"Deleting existing rule: {rule_name}")
            iot.delete_topic_rule(ruleName=rule_name)
            time.sleep(2)
        except:
            pass
        
        # Create rule with VPC destination
        rule_payload = {
            'sql': "SELECT * FROM 'topic/telemetry'",
            'description': 'Route telemetry data to MSK cluster via VPC',
            'actions': [{
                'kafka': {
                    'destinationArn': destination_arn,
                    'topic': 'cms-telemetry-raw',
                    'clientProperties': {
                        'bootstrap.servers': bootstrap_servers,
                        'security.protocol': 'SASL_SSL',
                        'sasl.mechanism': 'SCRAM-SHA-512',
                        'sasl.scram.username': f'${{get_secret("{secret_arn}", "SecretString", "username", "{role_arn}")}}',
                        'sasl.scram.password': f'${{get_secret("{secret_arn}", "SecretString", "password", "{role_arn}")}}'
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
    
    print(f"🔧 Fixing IoT-MSK Integration for stage: {deployment_stage}")
    
    # Get stack outputs
    msk_outputs = get_stack_outputs(f"cms-{deployment_stage}-msk")
    iot_outputs = get_stack_outputs(f"cms-{deployment_stage}-iot")
    
    if not all([msk_outputs.get('MSKClusterArn'), iot_outputs.get('IoTRoleArn')]):
        print("❌ Required stacks not found")
        return False
    
    cluster_arn = msk_outputs['MSKClusterArn']
    secret_arn = msk_outputs.get('IoTUserSecretArn', '')
    role_arn = iot_outputs['IoTRoleArn']
    
    # Get MSK bootstrap servers
    kafka = boto3.client('kafka')
    try:
        response = kafka.get_bootstrap_brokers(ClusterArn=cluster_arn)
        bootstrap_servers = response.get('BootstrapBrokerStringSaslScram', response.get('BootstrapBrokerString'))
    except Exception as e:
        print(f"❌ Error getting bootstrap servers: {e}")
        return False
    
    print(f"✅ Bootstrap servers: {bootstrap_servers}")
    
    # Get VPC information
    print("🔍 Getting VPC information...")
    vpc_id, subnet_ids, sg_ids = get_vpc_info()
    
    if not all([vpc_id, subnet_ids, sg_ids]):
        print("❌ Could not get VPC information")
        return False
    
    print(f"✅ VPC: {vpc_id}, Subnets: {subnet_ids}, Security Groups: {sg_ids}")
    
    # Create VPC destination
    print("🔗 Creating VPC destination...")
    destination_arn = create_vpc_destination(vpc_id, subnet_ids, sg_ids, role_arn)
    
    if not destination_arn:
        return False
    
    # Create IoT rule
    print("📡 Creating IoT rule...")
    success = create_iot_rule_with_destination(
        rule_name="cms_telemetry_to_msk",
        destination_arn=destination_arn,
        cluster_arn=cluster_arn,
        bootstrap_servers=bootstrap_servers,
        secret_arn=secret_arn,
        role_arn=role_arn
    )
    
    if success:
        print("🎉 IoT-MSK integration fixed!")
        return True
    else:
        print("❌ Integration failed")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
