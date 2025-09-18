#!/usr/bin/env python3
"""
Working Integration Script - Updates Flink applications with MSK configuration
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
        return None

def update_flink_app(app_name: str, bootstrap_servers: str, secret_arn: str):
    """Update Flink application with MSK configuration"""
    kinesisanalytics = boto3.client('kinesisanalyticsv2')
    
    try:
        # Get current application
        response = kinesisanalytics.describe_application(ApplicationName=app_name)
        app_detail = response['ApplicationDetail']
        
        if app_detail['ApplicationStatus'] != 'READY':
            print(f"⏳ App {app_name} not ready (status: {app_detail['ApplicationStatus']})")
            return False
        
        current_version = app_detail['ApplicationVersionId']
        
        # Update only kafka configuration
        env_properties = [
            {
                'PropertyGroupId': 'kafka.config',
                'PropertyMap': {
                    'bootstrap.servers': bootstrap_servers,
                    'security.protocol': 'SASL_SSL',
                    'sasl.mechanism': 'SCRAM-SHA-512',
                    'topic.name': 'cms-telemetry-raw',
                    'group.id': f'{app_name}-consumer'
                }
            }
        ]
        
        # Update application
        kinesisanalytics.update_application(
            ApplicationName=app_name,
            CurrentApplicationVersionId=current_version,
            ApplicationConfigurationUpdate={
                'EnvironmentPropertyUpdates': {
                    'PropertyGroups': env_properties
                }
            }
        )
        
        print(f"✅ Updated {app_name}")
        return True
        
    except Exception as e:
        print(f"❌ Error updating {app_name}: {e}")
        return False

def main():
    deployment_stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
    
    print(f"🚀 Working Integration for stage: {deployment_stage}")
    
    # Get stack outputs
    msk_outputs = get_stack_outputs(f"cms-{deployment_stage}-msk")
    flink_outputs = get_stack_outputs(f"cms-{deployment_stage}-flink")
    
    if not msk_outputs.get('MSKClusterArn'):
        print("❌ MSK stack not found")
        return False
    
    if not flink_outputs:
        print("❌ Flink stack not found")
        return False
    
    cluster_arn = msk_outputs['MSKClusterArn']
    secret_arn = msk_outputs.get('IoTUserSecretArn', '')
    
    print("🔍 Getting MSK bootstrap servers...")
    bootstrap_servers = get_msk_bootstrap_servers(cluster_arn)
    
    if not bootstrap_servers:
        print("❌ Could not get bootstrap servers")
        return False
    
    print(f"✅ Bootstrap servers: {bootstrap_servers}")
    
    # Update all Flink applications
    apps_to_update = [
        'cms-dev-flink-event-driven-telemetry-processor',
        'cms-dev-flink-telemetry-enhanced-final',
        'cms-dev-flink-trip-processor',
        'cms-dev-flink-safety-processor',
        'cms-dev-flink-maintenance-processor'
    ]
    
    success_count = 0
    for app_name in apps_to_update:
        if update_flink_app(app_name, bootstrap_servers, secret_arn):
            success_count += 1
    
    print(f"🎉 Updated {success_count}/{len(apps_to_update)} Flink applications")
    return success_count > 0

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
