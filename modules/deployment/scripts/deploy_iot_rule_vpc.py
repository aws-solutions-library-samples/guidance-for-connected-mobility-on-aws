#!/usr/bin/env python3
"""
Deploy IoT rule with VPC destination following AWS blog approach
"""

import boto3
import json
import os

def deploy_iot_rule_vpc():
    """Deploy IoT rule using VPC destination for MSK"""
    
    # Initialize session with target account
    # Use environment variable or default profile
    profile_name = os.environ.get('AWS_PROFILE', 'default')
    session = boto3.Session(profile_name=profile_name)
    
    # Initialize clients
    iot_client = session.client('iot', region_name='us-east-1')
    kafka_client = session.client('kafka', region_name='us-east-1')
    iam_client = session.client('iam', region_name='us-east-1')
    ec2_client = session.client('ec2', region_name='us-east-1')
    
    # Get MSK cluster details
    clusters = kafka_client.list_clusters()['ClusterInfoList']
    msk_cluster = next((c for c in clusters if 'cost-optimized-msk' in c['ClusterName']), None)
    
    if not msk_cluster:
        print("❌ MSK cluster not found")
        return
    
    msk_cluster_arn = msk_cluster['ClusterArn']
    print(f"✅ Found MSK cluster: {msk_cluster_arn}")
    
    # Get VPC and subnets from MSK cluster
    cluster_details = kafka_client.describe_cluster(ClusterArn=msk_cluster_arn)
    client_subnets = cluster_details['ClusterInfo']['BrokerNodeGroupInfo']['ClientSubnets']
    security_groups = cluster_details['ClusterInfo']['BrokerNodeGroupInfo']['SecurityGroups']
    
    # Get VPC ID from subnet
    subnet_details = ec2_client.describe_subnets(SubnetIds=[client_subnets[0]])
    vpc_id = subnet_details['Subnets'][0]['VpcId']
    
    print(f"✅ VPC: {vpc_id}")
    print(f"✅ Subnets: {client_subnets}")
    print(f"✅ Security Groups: {security_groups}")
    
    # Create IAM role for IoT rule
    role_name = "IoTMSKVPCRole"
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "iot.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }
        ]
    }
    
    try:
        role_response = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Role for IoT Core VPC destination to MSK"
        )
        role_arn = role_response['Role']['Arn']
        print(f"✅ Created IAM role: {role_arn}")
    except iam_client.exceptions.EntityAlreadyExistsException:
        role_response = iam_client.get_role(RoleName=role_name)
        role_arn = role_response['Role']['Arn']
        print(f"✅ Using existing IAM role: {role_arn}")
    
    # Add VPC and MSK permissions
    policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "kafka:DescribeCluster",
                    "kafka:DescribeClusterV2", 
                    "kafka:GetBootstrapBrokers",
                    "kafka-cluster:Connect",
                    "kafka-cluster:AlterCluster",
                    "kafka-cluster:DescribeCluster",
                    "kafka-cluster:*Topic*",
                    "kafka-cluster:WriteData",
                    "kafka-cluster:ReadData"
                ],
                "Resource": [
                    msk_cluster_arn,
                    f"{msk_cluster_arn}/topic/*"
                ]
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ec2:CreateNetworkInterface",
                    "ec2:DescribeNetworkInterfaces",
                    "ec2:CreateNetworkInterfacePermission",
                    "ec2:DeleteNetworkInterface",
                    "ec2:DescribeSubnets",
                    "ec2:DescribeVpcs",
                    "ec2:DescribeSecurityGroups"
                ],
                "Resource": "*"
            }
        ]
    }
    
    try:
        iam_client.put_role_policy(
            RoleName=role_name,
            PolicyName="MSKVPCAccess",
            PolicyDocument=json.dumps(policy_document)
        )
        print("✅ Added MSK VPC permissions to role")
    except Exception as e:
        print(f"⚠️ Policy update: {e}")
    
    # Create VPC destination configuration
    vpc_dest_name = "cms-msk-vpc-destination"
    
    try:
        # Delete existing VPC destination if it exists
        try:
            iot_client.delete_topic_rule_destination(destinationName=vpc_dest_name)
            print(f"🗑️ Deleted existing VPC destination: {vpc_dest_name}")
        except iot_client.exceptions.ResourceNotFoundException:
            pass
        
        # Create VPC destination
        vpc_dest_response = iot_client.create_topic_rule_destination(
            destinationName=vpc_dest_name,
            destinationConfiguration={
                'vpcDestinationConfiguration': {
                    'subnetIds': client_subnets,
                    'securityGroups': security_groups,
                    'vpcId': vpc_id,
                    'roleArn': role_arn
                }
            }
        )
        
        vpc_dest_arn = vpc_dest_response['destinationArn']
        print(f"✅ Created VPC destination: {vpc_dest_arn}")
        
    except Exception as e:
        print(f"❌ Failed to create VPC destination: {e}")
        return
    
    # Get bootstrap servers for client properties
    bootstrap_response = kafka_client.get_bootstrap_brokers(ClusterArn=msk_cluster_arn)
    bootstrap_servers = bootstrap_response.get('BootstrapBrokerStringTls')
    print(f"✅ Bootstrap servers: {bootstrap_servers}")
    
    # Create IoT rule with VPC destination
    rule_name = "cms_telemetry_to_msk_vpc"
    
    # Delete existing rule if it exists
    try:
        iot_client.delete_topic_rule(ruleName=rule_name)
        print(f"🗑️ Deleted existing rule: {rule_name}")
    except iot_client.exceptions.ResourceNotFoundException:
        pass
    
    # Create new rule
    rule_payload = {
        'sql': "SELECT * FROM 'cms/telemetry/vehicle/+'",
        'description': 'Route CMS telemetry to MSK via VPC destination',
        'actions': [
            {
                'kafka': {
                    'destinationArn': vpc_dest_arn,
                    'topic': 'cms-telemetry-raw',
                    'key': '${topic(3)}',  # Use vehicle ID as partition key
                    'clientProperties': {
                        'bootstrap.servers': bootstrap_servers,
                        'security.protocol': 'SSL'
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
        print(f"   VPC destination: {vpc_dest_arn}")
        print(f"   MSK topic: cms-telemetry-raw")
        print(f"   Partition key: Vehicle ID")
        
    except Exception as e:
        print(f"❌ Failed to create IoT rule: {e}")
        return
    
    print("\n🎉 IoT rule with VPC destination deployed successfully!")
    print("Telemetry data will now flow: IoT Core → VPC Destination → MSK → Flink")

if __name__ == "__main__":
    deploy_iot_rule_vpc()
