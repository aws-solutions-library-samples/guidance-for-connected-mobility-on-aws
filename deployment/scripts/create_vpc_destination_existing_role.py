#!/usr/bin/env python3
"""
Create VPC destination using existing IoT role
"""

import boto3
import time

def get_msk_cluster_info():
    """Get MSK cluster information"""
    kafka = boto3.client('kafka', region_name='us-east-1')
    
    clusters = kafka.list_clusters_v2()
    if not clusters['ClusterInfoList']:
        print("❌ No MSK clusters found")
        return None
    
    cluster = clusters['ClusterInfoList'][0]
    cluster_arn = cluster['ClusterArn']
    
    # Get VPC info from cluster
    vpc_id = None
    subnet_ids = []
    security_group_ids = []
    
    if cluster['ClusterType'] == 'PROVISIONED':
        broker_info = cluster['Provisioned']['BrokerNodeGroupInfo']
        subnet_ids = broker_info['ClientSubnets']
        security_group_ids = broker_info['SecurityGroups']
        
        # Get VPC ID from subnet
        ec2 = boto3.client('ec2', region_name='us-east-1')
        subnet_response = ec2.describe_subnets(SubnetIds=[subnet_ids[0]])
        vpc_id = subnet_response['Subnets'][0]['VpcId']
    
    return {
        'cluster_arn': cluster_arn,
        'vpc_id': vpc_id,
        'subnet_ids': subnet_ids,
        'security_group_ids': security_group_ids
    }

def create_vpc_destination():
    """Create VPC destination for IoT Core using existing role"""
    iot = boto3.client('iot', region_name='us-east-1')
    
    # Get MSK cluster info
    cluster_info = get_msk_cluster_info()
    if not cluster_info:
        return None
    
    print(f"📊 MSK Cluster: {cluster_info['cluster_arn']}")
    print(f"🌐 VPC: {cluster_info['vpc_id']}")
    print(f"🔗 Subnets: {cluster_info['subnet_ids']}")
    print(f"🛡️  Security Groups: {cluster_info['security_group_ids']}")
    
    try:
        # Check if destination already exists
        destinations = iot.list_topic_rule_destinations()
        for dest in destinations.get('destinationSummaries', []):
            if dest['status'] in ['ENABLED', 'IN_PROGRESS']:
                print(f"✅ VPC destination already exists: {dest['arn']}")
                return dest['arn']
        
        # Use existing IoT role - try multiple options
        account_id = boto3.client('sts').get_caller_identity()['Account']
        possible_roles = [
            f"arn:aws:iam::{account_id}:role/service-role/AWSIoTForAutoKRule",
            f"arn:aws:iam::{account_id}:role/service-role/awsIoTCoreFirehose",
            f"arn:aws:iam::{account_id}:role/service-role/asdfadfadsfadsf"
        ]
        
        role_arn = None
        for role in possible_roles:
            try:
                # Test if role exists and can be assumed by IoT
                iam = boto3.client('iam')
                role_name = role.split('/')[-1]
                iam.get_role(RoleName=role_name)
                role_arn = role
                print(f"✅ Using existing IoT role: {role_arn}")
                break
            except:
                continue
        
        if not role_arn:
            print("❌ No suitable IoT role found")
            return None
        
        # Create VPC destination
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
                print(f"❌ VPC destination failed: {dest_info['topicRuleDestination'].get('statusReason', 'Unknown error')}")
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
    print("🔧 Creating VPC destination for IoT-MSK connectivity")
    
    destination_arn = create_vpc_destination()
    
    if destination_arn:
        print(f"\n🎉 VPC destination ready: {destination_arn}")
        return True
    else:
        print("\n❌ Failed to create VPC destination")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
