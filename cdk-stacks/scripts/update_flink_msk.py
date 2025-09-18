#!/usr/bin/env python3
"""
Script to update Flink applications with MSK configuration
This implements the sequential deployment strategy
"""

import boto3
import json
import time
from typing import Dict, List

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

def list_flink_applications() -> List[Dict]:
    """List all Flink applications"""
    flink = boto3.client('kinesisanalyticsv2')
    
    try:
        response = flink.list_applications()
        return response.get('ApplicationSummaries', [])
    except Exception as e:
        print(f"Error listing Flink applications: {e}")
        return []

def update_flink_application(app_name: str, bootstrap_servers: str, vpc_config: Dict):
    """Update Flink application with MSK configuration"""
    flink = boto3.client('kinesisanalyticsv2')
    
    try:
        # Get current application details
        response = flink.describe_application(ApplicationName=app_name)
        app_detail = response['ApplicationDetail']
        
        current_version = app_detail['ApplicationVersionId']
        
        # Prepare MSK configuration
        msk_properties = {
            "bootstrap.servers": bootstrap_servers,
            "security.protocol": "SASL_SSL",
            "sasl.mechanism": "AWS_MSK_IAM",
            "sasl.jaas.config": "software.amazon.msk.auth.iam.IAMLoginModule required;",
            "sasl.client.callback.handler.class": "software.amazon.msk.auth.iam.IAMClientCallbackHandler",
            "sasl.login.callback.handler.class": "software.amazon.msk.auth.iam.IAMClientCallbackHandler"
        }
        
        # Get existing property groups
        existing_properties = {}
        if 'ApplicationConfigurationDescription' in app_detail:
            config = app_detail['ApplicationConfigurationDescription']
            if 'EnvironmentPropertyDescriptions' in config:
                env_props = config['EnvironmentPropertyDescriptions']
                if 'PropertyGroupDescriptions' in env_props:
                    for group in env_props['PropertyGroupDescriptions']:
                        if group['PropertyGroupId'] == 'consumer.config.0':
                            existing_properties = group['PropertyMap']
                            break
        
        # Merge MSK properties with existing properties
        updated_properties = {**existing_properties, **msk_properties}
        
        # Update application configuration
        update_request = {
            'ApplicationName': app_name,
            'CurrentApplicationVersionId': current_version,
            'ApplicationConfigurationUpdate': {
                'EnvironmentPropertyUpdates': {
                    'PropertyGroups': [{
                        'PropertyGroupId': 'consumer.config.0',
                        'PropertyMap': updated_properties
                    }]
                }
            }
        }
        
        # Add VPC configuration if provided
        if vpc_config:
            update_request['ApplicationConfigurationUpdate']['VpcConfigurationUpdates'] = [{
                'VpcConfigurationId': vpc_config.get('VpcConfigurationId', 'new'),
                'SubnetIdUpdates': vpc_config['SubnetIds'],
                'SecurityGroupIdUpdates': vpc_config['SecurityGroupIds']
            }]
        
        print(f"Updating application {app_name} with MSK configuration...")
        flink.update_application(
            **update_request
        )
        
        print(f"Successfully updated {app_name}")
        
        # Wait for update to complete
        print(f"Waiting for {app_name} update to complete...")
        waiter = flink.get_waiter('application_updated')
        waiter.wait(
            ApplicationName=app_name,
            WaiterConfig={'Delay': 30, 'MaxAttempts': 20}
        )
        
        print(f"Update completed for {app_name}")
        
    except Exception as e:
        print(f"Error updating Flink application {app_name}: {e}")
        raise

def update_flink_role_permissions(role_name: str, cluster_arn: str):
    """Update Flink role with MSK permissions"""
    iam = boto3.client('iam')
    
    policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "kafka-cluster:Connect",
                    "kafka-cluster:AlterCluster",
                    "kafka-cluster:DescribeCluster",
                    "kafka-cluster:*Topic*",
                    "kafka-cluster:WriteData",
                    "kafka-cluster:ReadData",
                    "kafka-cluster:AlterGroup",
                    "kafka-cluster:DescribeGroup"
                ],
                "Resource": [
                    cluster_arn,
                    f"{cluster_arn}/topic/*",
                    f"{cluster_arn}/group/*"
                ]
            },
            {
                "Effect": "Allow",
                "Action": [
                    "kafka:DescribeCluster",
                    "kafka:DescribeClusterV2",
                    "kafka:GetBootstrapBrokers"
                ],
                "Resource": "*"
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
    """Main function to update Flink applications with MSK configuration"""
    
    # Stack names (adjust as needed)
    msk_stack_name = "cms-dev-msk"
    flink_stack_name = "cms-dev-flink"
    
    print("Starting Flink-MSK integration...")
    
    # Get MSK stack outputs
    print("Getting MSK stack outputs...")
    msk_outputs = get_stack_outputs(msk_stack_name)
    
    if not msk_outputs:
        print("ERROR: Could not get MSK stack outputs. Make sure MSK stack is deployed.")
        return
    
    # Get Flink stack outputs
    print("Getting Flink stack outputs...")
    flink_outputs = get_stack_outputs(flink_stack_name)
    
    if not flink_outputs:
        print("ERROR: Could not get Flink stack outputs. Make sure Flink stack is deployed.")
        return
    
    # Extract required values
    cluster_arn = msk_outputs.get('MSKClusterArn')
    bootstrap_servers = msk_outputs.get('BootstrapServers')
    flink_role_arn = flink_outputs.get('FlinkRoleArn')
    
    if not all([cluster_arn, bootstrap_servers, flink_role_arn]):
        print("ERROR: Missing required stack outputs:")
        print(f"  Cluster ARN: {cluster_arn}")
        print(f"  Bootstrap Servers: {bootstrap_servers}")
        print(f"  Flink Role ARN: {flink_role_arn}")
        return
    
    # Extract role name from ARN
    role_name = flink_role_arn.split('/')[-1]
    
    # Get VPC configuration from MSK stack
    vpc_config = None
    if 'MSKSecurityGroupId' in msk_outputs:
        # Get VPC info
        ec2 = boto3.client('ec2')
        vpcs = ec2.describe_vpcs(Filters=[{'Name': 'is-default', 'Values': ['true']}])
        
        if vpcs['Vpcs']:
            vpc_id = vpcs['Vpcs'][0]['VpcId']
            subnets = ec2.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
            subnet_ids = [subnet['SubnetId'] for subnet in subnets['Subnets'][:2]]
            
            vpc_config = {
                'SubnetIds': subnet_ids,
                'SecurityGroupIds': [msk_outputs['MSKSecurityGroupId']]
            }
    
    # Step 1: Update Flink role permissions
    print("\nStep 1: Updating Flink role permissions...")
    update_flink_role_permissions(role_name, cluster_arn)
    
    # Step 2: List and update Flink applications
    print("\nStep 2: Getting Flink applications...")
    applications = list_flink_applications()
    
    if not applications:
        print("No Flink applications found to update")
        return
    
    print(f"Found {len(applications)} Flink applications")
    
    # Step 3: Update each application
    for app in applications:
        app_name = app['ApplicationName']
        app_status = app['ApplicationStatus']
        
        print(f"\nProcessing application: {app_name} (Status: {app_status})")
        
        if app_status == 'RUNNING':
            print(f"Stopping {app_name} for configuration update...")
            flink = boto3.client('kinesisanalyticsv2')
            flink.stop_application(ApplicationName=app_name)
            
            # Wait for application to stop
            waiter = flink.get_waiter('application_stopped')
            waiter.wait(ApplicationName=app_name)
            print(f"{app_name} stopped")
        
        # Update application configuration
        update_flink_application(app_name, bootstrap_servers, vpc_config)
        
        # Restart application
        print(f"Starting {app_name}...")
        flink = boto3.client('kinesisanalyticsv2')
        flink.start_application(
            ApplicationName=app_name,
            RunConfiguration={}
        )
        
        print(f"{app_name} started with MSK configuration")
    
    print(f"\nFlink-MSK integration completed successfully!")
    print(f"Updated {len(applications)} applications with MSK configuration")

if __name__ == "__main__":
    main()
