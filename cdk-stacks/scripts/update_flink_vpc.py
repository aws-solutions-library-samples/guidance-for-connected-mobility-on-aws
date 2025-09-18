#!/usr/bin/env python3
"""
Update Flink applications with VPC configuration for MSK connectivity
"""
import boto3
import os
import sys

def update_flink_vpc():
    profile = os.environ.get('AWS_PROFILE', 'default')
    stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
    
    session = boto3.Session(profile_name=profile)
    flink_client = session.client('kinesisanalyticsv2', region_name='us-east-1')
    cf_client = session.client('cloudformation', region_name='us-east-1')
    
    # Get MSK VPC configuration from CloudFormation exports
    try:
        msk_stack = cf_client.describe_stacks(StackName=f'cms-{stage}-msk')
        outputs = msk_stack['Stacks'][0]['Outputs']
        
        vpc_id = None
        security_group_id = None
        subnet_ids = None
        
        for output in outputs:
            if output['OutputKey'] == 'MSKVpcId':
                vpc_id = output['OutputValue']
            elif output['OutputKey'] == 'MSKSecurityGroupId':
                security_group_id = output['OutputValue']
            elif output['OutputKey'] == 'MSKPrivateSubnetIds':
                subnet_ids = output['OutputValue'].split(',')
        
        if not all([vpc_id, security_group_id, subnet_ids]):
            print("❌ Could not get MSK VPC configuration")
            return False
            
        print(f"✅ Found MSK VPC: {vpc_id}")
        print(f"✅ Found Security Group: {security_group_id}")
        print(f"✅ Found Subnets: {subnet_ids}")
        
    except Exception as e:
        print(f"❌ Error getting MSK configuration: {e}")
        return False
    
    # List of Flink applications to update
    applications = [
        f'cms-{stage}-flink-event-driven-telemetry-processor',
        f'cms-{stage}-flink-telemetry-enhanced-final',
        f'cms-{stage}-flink-trip-processor',
        f'cms-{stage}-flink-safety-processor',
        f'cms-{stage}-flink-maintenance-processor'
    ]
    
    for app_name in applications:
        try:
            print(f"\n🔧 Updating {app_name}...")
            
            # Get current application configuration
            response = flink_client.describe_application(ApplicationName=app_name)
            app_detail = response['ApplicationDetail']
            
            # Check if VPC is already configured
            if 'VpcConfigurationDescriptions' in app_detail:
                print(f"  ✅ {app_name} already has VPC configuration")
                continue
            
            # Update application with VPC configuration
            flink_client.update_application(
                ApplicationName=app_name,
                CurrentApplicationVersionId=app_detail['ApplicationVersionId'],
                ApplicationConfigurationUpdate={
                    'VpcConfigurationUpdates': [{
                        'SubnetIds': subnet_ids[:2],  # Use first 2 subnets
                        'SecurityGroupIds': [security_group_id]
                    }]
                }
            )
            
            print(f"  ✅ Updated {app_name} with VPC configuration")
            
        except Exception as e:
            print(f"  ❌ Error updating {app_name}: {e}")
    
    print(f"\n✅ VPC configuration update completed!")
    return True

if __name__ == "__main__":
    update_flink_vpc()
