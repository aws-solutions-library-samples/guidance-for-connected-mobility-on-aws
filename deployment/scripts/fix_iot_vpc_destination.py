#!/usr/bin/env python3
"""
Fix IoT VPC destination by creating proper IAM role and updating destination
"""

import boto3
import json
import time

def create_iot_vpc_role():
    """Create IAM role for IoT VPC destination with required permissions"""
    iam = boto3.client('iam', region_name='us-east-1')
    
    role_name = "cms-iot-vpc-destination-role"
    
    # Trust policy for IoT service
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Service": "iot.amazonaws.com"
                },
                "Action": "sts:AssumeRole"
            }
        ]
    }
    
    # Permissions policy for VPC destination
    vpc_permissions_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "ec2:CreateNetworkInterface",
                    "ec2:DescribeNetworkInterfaces",
                    "ec2:DescribeSecurityGroups",
                    "ec2:DescribeSubnets",
                    "ec2:DescribeVpcs",
                    "ec2:DeleteNetworkInterface",
                    "ec2:AttachNetworkInterface",
                    "ec2:DetachNetworkInterface"
                ],
                "Resource": "*"
            },
            {
                "Effect": "Allow",
                "Action": [
                    "kafka:DescribeCluster",
                    "kafka:GetBootstrapBrokers"
                ],
                "Resource": "*"
            }
        ]
    }
    
    try:
        # Check if role exists
        try:
            role = iam.get_role(RoleName=role_name)
            print(f"✅ Role already exists: {role['Role']['Arn']}")
            return role['Role']['Arn']
        except iam.exceptions.NoSuchEntityException:
            pass
        
        # Create role
        response = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="IoT VPC destination role for MSK connectivity"
        )
        
        role_arn = response['Role']['Arn']
        print(f"✅ Created IAM role: {role_arn}")
        
        # Create and attach policy
        policy_name = "cms-iot-vpc-destination-policy"
        
        try:
            iam.create_policy(
                PolicyName=policy_name,
                PolicyDocument=json.dumps(vpc_permissions_policy),
                Description="Permissions for IoT VPC destination"
            )
            print(f"✅ Created policy: {policy_name}")
        except iam.exceptions.EntityAlreadyExistsException:
            print(f"✅ Policy already exists: {policy_name}")
        
        # Attach policy to role
        account_id = boto3.client('sts').get_caller_identity()['Account']
        policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"
        
        iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn=policy_arn
        )
        print(f"✅ Attached policy to role")
        
        # Wait for role to propagate
        print("⏳ Waiting for role to propagate...")
        time.sleep(10)
        
        return role_arn
        
    except Exception as e:
        print(f"❌ Error creating role: {e}")
        return None

def get_msk_cluster_info():
    """Get MSK cluster VPC information"""
    kafka = boto3.client('kafka', region_name='us-east-1')
    
    clusters = kafka.list_clusters_v2()
    if not clusters['ClusterInfoList']:
        return None
    
    cluster = clusters['ClusterInfoList'][0]
    
    if cluster['ClusterType'] == 'PROVISIONED':
        broker_info = cluster['Provisioned']['BrokerNodeGroupInfo']
        subnet_ids = broker_info['ClientSubnets']
        security_group_ids = broker_info['SecurityGroups']
        
        # Get VPC ID from subnet
        ec2 = boto3.client('ec2', region_name='us-east-1')
        subnet_response = ec2.describe_subnets(SubnetIds=[subnet_ids[0]])
        vpc_id = subnet_response['Subnets'][0]['VpcId']
        
        return {
            'cluster_arn': cluster['ClusterArn'],
            'vpc_id': vpc_id,
            'subnet_ids': subnet_ids,
            'security_group_ids': security_group_ids
        }
    
    return None

def fix_vpc_destination():
    """Fix or create VPC destination with proper role"""
    iot = boto3.client('iot', region_name='us-east-1')
    
    # Create proper IAM role
    role_arn = create_iot_vpc_role()
    if not role_arn:
        return None
    
    # Get MSK cluster info
    cluster_info = get_msk_cluster_info()
    if not cluster_info:
        print("❌ Could not get MSK cluster information")
        return None
    
    print(f"📊 MSK VPC: {cluster_info['vpc_id']}")
    print(f"🔗 Subnets: {cluster_info['subnet_ids']}")
    print(f"🛡️  Security Groups: {cluster_info['security_group_ids']}")
    
    try:
        # Delete existing broken destination
        destinations = iot.list_topic_rule_destinations()
        for dest in destinations.get('destinationSummaries', []):
            if dest['status'] == 'ERROR':
                print(f"🗑️  Deleting broken destination: {dest['arn']}")
                iot.delete_topic_rule_destination(arn=dest['arn'])
                time.sleep(5)
        
        # Create new VPC destination with proper role
        response = iot.create_topic_rule_destination(
            destinationConfiguration={
                'vpcConfiguration': {
                    'subnetIds': cluster_info['subnet_ids'],
                    'securityGroups': cluster_info['security_group_ids'],
                    'vpcId': cluster_info['vpc_id'],
                    'roleArn': role_arn
                }
            }
        )
        
        destination_arn = response['topicRuleDestination']['arn']
        print(f"✅ Created VPC destination: {destination_arn}")
        
        # Wait for destination to be ready
        print("⏳ Waiting for VPC destination to be ready...")
        for i in range(30):
            dest_info = iot.get_topic_rule_destination(arn=destination_arn)
            status = dest_info['topicRuleDestination']['status']
            
            if status == 'ENABLED':
                print("✅ VPC destination is ready!")
                return destination_arn
            elif status == 'ERROR':
                reason = dest_info['topicRuleDestination'].get('statusReason', 'Unknown error')
                print(f"❌ VPC destination failed: {reason}")
                return None
            else:
                print(f"⏳ Status: {status} ({i+1}/30)")
                time.sleep(10)
        
        print("❌ VPC destination creation timed out")
        return None
        
    except Exception as e:
        print(f"❌ Error creating VPC destination: {e}")
        return None

def main():
    print("🔧 Fixing IoT VPC destination permissions")
    
    destination_arn = fix_vpc_destination()
    
    if destination_arn:
        print(f"\n🎉 VPC destination fixed: {destination_arn}")
        print("\n📋 Next steps:")
        print("1. VPC destination is now ready for IoT rules")
        print("2. Run create_iot_rule_scram.py to create the IoT rule")
        print("3. Test with IoT message publishing")
        return True
    else:
        print("\n❌ Failed to fix VPC destination")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
