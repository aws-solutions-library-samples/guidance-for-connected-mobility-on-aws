#!/usr/bin/env python3
"""
Script to add MSK integration to IoT stack after MSK deployment
This implements the sequential deployment strategy
"""

import boto3
import json
import time
from typing import Dict, Any

def get_stack_outputs(stack_name: str) -> Dict[str, str]:
    """Get CloudFormation stack outputs"""
    cf = boto3.client('cloudformation')
    
    try:
        response = cf.describe_stacks(StackName=stack_name)
        stack = response['Stacks'][0]
        
        outputs = {}
        if 'Outputs' in stack:
            for output in stack['Outputs']:
                outputs[output['OutputKey']] = output['OutputValue']
        
        return outputs
    except Exception as e:
        print(f"Error getting stack outputs for {stack_name}: {e}")
        return {}

def create_vpc_destination(vpc_id: str, subnet_ids: list, security_group_ids: list, role_arn: str) -> str:
    """Create VPC destination for IoT rules"""
    iot = boto3.client('iot')
    
    try:
        # Check if VPC destination already exists
        destinations = iot.list_vpc_destinations()
        for dest in destinations.get('destinations', []):
            if dest['vpcId'] == vpc_id:
                print(f"VPC destination already exists: {dest['arn']}")
                return dest['arn']
        
        # Create new VPC destination
        response = iot.create_vpc_destination(
            vpcDestinationConfiguration={
                'vpcId': vpc_id,
                'subnetIds': subnet_ids,
                'securityGroups': security_group_ids,
                'roleArn': role_arn
            }
        )
        
        destination_arn = response['vpcDestination']['arn']
        print(f"Created VPC destination: {destination_arn}")
        return destination_arn
        
    except Exception as e:
        print(f"Error creating VPC destination: {e}")
        raise

def create_iot_rule(rule_name: str, destination_arn: str, bootstrap_servers: str, secret_arn: str, role_arn: str):
    """Create IoT rule to route telemetry to MSK"""
    iot = boto3.client('iot')
    
    try:
        # Check if rule already exists
        try:
            iot.get_topic_rule(ruleName=rule_name)
            print(f"IoT rule {rule_name} already exists, updating...")
            
            # Delete existing rule
            iot.delete_topic_rule(ruleName=rule_name)
            time.sleep(2)  # Wait for deletion
            
        except iot.exceptions.ResourceNotFoundException:
            print(f"Creating new IoT rule: {rule_name}")
        
        # Create the rule
        rule_payload = {
            'sql': "SELECT * FROM 'topic/telemetry'",
            'description': 'Route telemetry data to MSK cluster',
            'actions': [{
                'kafka': {
                    'destinationArn': destination_arn,
                    'topic': 'cms-telemetry-raw',
                    'key': 'basic-ingest',
                    'clientProperties': {
                        'acks': '1',
                        'bootstrap.servers': bootstrap_servers,
                        'security.protocol': 'SASL_SSL',
                        'sasl.mechanism': 'SCRAM-SHA-512',
                        'sasl.scram.username': f'${{get_secret("{secret_arn}", "SecretString", "username", "{role_arn}")}}',
                        'sasl.scram.password': f'${{get_secret("{secret_arn}", "SecretString", "password", "{role_arn}")}}'
                    }
                }
            }],
            'ruleDisabled': False,
            'errorAction': {
                'cloudwatchLogs': {
                    'logGroupName': '/aws/iot/rule/errors',
                    'roleArn': role_arn
                }
            }
        }
        
        iot.create_topic_rule(
            ruleName=rule_name,
            topicRulePayload=rule_payload
        )
        
        print(f"Created IoT rule: {rule_name}")
        
    except Exception as e:
        print(f"Error creating IoT rule: {e}")
        raise

def update_iot_role_policy(role_name: str, cluster_arn: str, secret_arn: str):
    """Update IoT role with MSK permissions"""
    iam = boto3.client('iam')
    
    policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "kafka-cluster:Connect",
                    "kafka-cluster:AlterCluster",
                    "kafka-cluster:DescribeCluster"
                ],
                "Resource": cluster_arn
            },
            {
                "Effect": "Allow", 
                "Action": [
                    "kafka-cluster:*Topic*",
                    "kafka-cluster:WriteData",
                    "kafka-cluster:ReadData"
                ],
                "Resource": [
                    f"{cluster_arn}/topic/*"
                ]
            },
            {
                "Effect": "Allow",
                "Action": [
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:DescribeSecret"
                ],
                "Resource": secret_arn
            }
        ]
    }
    
    try:
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName='MSKIntegrationPolicy',
            PolicyDocument=json.dumps(policy_document)
        )
        print(f"Updated IAM role {role_name} with MSK permissions")
        
    except Exception as e:
        print(f"Error updating IAM role: {e}")
        raise

def main():
    """Main function to integrate MSK with IoT"""
    
    # Stack names (adjust as needed)
    msk_stack_name = "cms-dev-msk"
    iot_stack_name = "cms-dev-iot"
    
    print("Starting MSK-IoT integration...")
    
    # Get MSK stack outputs
    print("Getting MSK stack outputs...")
    msk_outputs = get_stack_outputs(msk_stack_name)
    
    if not msk_outputs:
        print("ERROR: Could not get MSK stack outputs. Make sure MSK stack is deployed.")
        return
    
    # Get IoT stack outputs  
    print("Getting IoT stack outputs...")
    iot_outputs = get_stack_outputs(iot_stack_name)
    
    if not iot_outputs:
        print("ERROR: Could not get IoT stack outputs. Make sure IoT stack is deployed.")
        return
    
    # Extract required values
    cluster_arn = msk_outputs.get('MSKClusterArn')
    bootstrap_servers = msk_outputs.get('BootstrapServers')
    secret_arn = msk_outputs.get('IoTUserSecretArn')
    iot_role_arn = iot_outputs.get('IoTRoleArn')
    
    if not all([cluster_arn, bootstrap_servers, secret_arn, iot_role_arn]):
        print("ERROR: Missing required stack outputs:")
        print(f"  Cluster ARN: {cluster_arn}")
        print(f"  Bootstrap Servers: {bootstrap_servers}")
        print(f"  Secret ARN: {secret_arn}")
        print(f"  IoT Role ARN: {iot_role_arn}")
        return
    
    # Extract role name from ARN
    role_name = iot_role_arn.split('/')[-1]
    
    # Get VPC info (assuming default VPC)
    ec2 = boto3.client('ec2')
    vpcs = ec2.describe_vpcs(Filters=[{'Name': 'is-default', 'Values': ['true']}])
    
    if not vpcs['Vpcs']:
        print("ERROR: No default VPC found")
        return
    
    vpc_id = vpcs['Vpcs'][0]['VpcId']
    
    # Get subnets
    subnets = ec2.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
    subnet_ids = [subnet['SubnetId'] for subnet in subnets['Subnets'][:2]]  # Use first 2 subnets
    
    # Get security groups (use default for now)
    security_groups = ec2.describe_security_groups(
        Filters=[
            {'Name': 'vpc-id', 'Values': [vpc_id]},
            {'Name': 'group-name', 'Values': ['default']}
        ]
    )
    security_group_ids = [sg['GroupId'] for sg in security_groups['SecurityGroups']]
    
    print(f"Using VPC: {vpc_id}")
    print(f"Using subnets: {subnet_ids}")
    print(f"Using security groups: {security_group_ids}")
    
    # Step 1: Update IoT role with MSK permissions
    print("\nStep 1: Updating IoT role permissions...")
    update_iot_role_policy(role_name, cluster_arn, secret_arn)
    
    # Step 2: Create VPC destination
    print("\nStep 2: Creating VPC destination...")
    destination_arn = create_vpc_destination(
        vpc_id=vpc_id,
        subnet_ids=subnet_ids,
        security_group_ids=security_group_ids,
        role_arn=iot_role_arn
    )
    
    # Step 3: Create IoT rule
    print("\nStep 3: Creating IoT rule...")
    rule_name = f"{msk_stack_name.replace('-', '_')}_telemetry_to_msk"
    create_iot_rule(
        rule_name=rule_name,
        destination_arn=destination_arn,
        bootstrap_servers=bootstrap_servers,
        secret_arn=secret_arn,
        role_arn=iot_role_arn
    )
    
    print(f"\nMSK-IoT integration completed successfully!")
    print(f"IoT Rule: {rule_name}")
    print(f"VPC Destination: {destination_arn}")

if __name__ == "__main__":
    main()
