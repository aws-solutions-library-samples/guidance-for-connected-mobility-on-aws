#!/usr/bin/env python3
"""
Deploy IoT rule to route telemetry to MSK
"""

import boto3
import json
import os

def deploy_iot_rule():
    """Deploy the IoT rule to route cms/telemetry/vehicle/+ to MSK"""
    
    # Initialize session with target account
    # Use environment variable or default profile
    profile_name = os.environ.get('AWS_PROFILE', 'default')
    session = boto3.Session(profile_name=profile_name)
    
    # Initialize clients
    iot_client = session.client('iot', region_name='us-east-1')
    kafka_client = session.client('kafka', region_name='us-east-1')
    iam_client = session.client('iam', region_name='us-east-1')
    
    # Get MSK cluster ARN
    clusters = kafka_client.list_clusters()['ClusterInfoList']
    msk_cluster = next((c for c in clusters if 'cost-optimized-msk' in c['ClusterName']), None)
    
    if not msk_cluster:
        print("❌ MSK cluster not found")
        return
    
    msk_cluster_arn = msk_cluster['ClusterArn']
    print(f"✅ Found MSK cluster: {msk_cluster_arn}")
    
    # Get bootstrap servers
    bootstrap_response = kafka_client.get_bootstrap_brokers(ClusterArn=msk_cluster_arn)
    bootstrap_servers = bootstrap_response.get('BootstrapBrokerString') or bootstrap_response.get('BootstrapBrokerStringTls')
    print(f"✅ Bootstrap servers: {bootstrap_servers}")
    
    # Create IAM role for IoT rule
    role_name = "IoTMSKTelemetryRole"
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
            Description="Role for IoT Core to publish to MSK"
        )
        role_arn = role_response['Role']['Arn']
        print(f"✅ Created IAM role: {role_arn}")
    except iam_client.exceptions.EntityAlreadyExistsException:
        role_response = iam_client.get_role(RoleName=role_name)
        role_arn = role_response['Role']['Arn']
        print(f"✅ Using existing IAM role: {role_arn}")
    
    # Add MSK permissions to role
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
            PolicyName="MSKAccess",
            PolicyDocument=json.dumps(policy_document)
        )
        print("✅ Added MSK permissions to role")
    except Exception as e:
        print(f"⚠️ Policy update: {e}")
    
    # Create IoT rule
    rule_name = "cms_telemetry_to_msk"
    
    # Try to create new rule (skip delete)
    rule_payload = {
        'sql': "SELECT * FROM 'cms/telemetry/vehicle/+'",
        'description': 'Route CMS telemetry data to MSK Kafka cluster',
        'actions': [
            {
                'kafka': {
                    'destinationArn': msk_cluster_arn,
                    'topic': 'cms-telemetry-raw',
                    'key': '${topic(3)}',  # Use vehicle ID as partition key
                    'clientProperties': {
                        'bootstrap.servers': bootstrap_servers,
                        'security.protocol': 'SSL',
                        'acks': '1'
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
        print(f"   MSK topic: cms-telemetry-raw")
        print(f"   Partition key: Vehicle ID")
        
    except Exception as e:
        print(f"❌ Failed to create IoT rule: {e}")
        return
    
    print("\n🎉 IoT rule deployment completed!")
    print("Telemetry data will now flow: IoT Core → MSK → Flink")

if __name__ == "__main__":
    deploy_iot_rule()
