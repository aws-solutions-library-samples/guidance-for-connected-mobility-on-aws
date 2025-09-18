#!/usr/bin/env python3
"""
Update Flink applications to use VPC connectivity bootstrap servers
"""

import boto3
import json
import os
import time

def get_vpc_connectivity_bootstrap_servers(cluster_arn: str):
    """Get VPC connectivity bootstrap servers for IAM authentication"""
    kafka = boto3.client('kafka')
    try:
        response = kafka.get_bootstrap_brokers(ClusterArn=cluster_arn)
        vpc_bootstrap = response.get('BootstrapBrokerStringVpcConnectivitySaslIam')
        if vpc_bootstrap:
            print(f"✅ VPC Connectivity Bootstrap Servers: {vpc_bootstrap}")
            return vpc_bootstrap
        else:
            print("❌ VPC connectivity not available yet")
            return None
    except Exception as e:
        print(f"Error getting VPC bootstrap servers: {e}")
        return None

def update_flink_application_bootstrap_servers(app_name: str, bootstrap_servers: str):
    """Update Flink application with VPC connectivity bootstrap servers"""
    flink = boto3.client('kinesisanalyticsv2')
    
    try:
        # Get current application configuration
        response = flink.describe_application(ApplicationName=app_name)
        app_detail = response['ApplicationDetail']
        
        if app_detail['ApplicationStatus'] != 'READY':
            print(f"⚠️ Application {app_name} is not in READY state: {app_detail['ApplicationStatus']}")
            return False
        
        current_version = app_detail['ApplicationVersionId']
        
        # Get current environment properties
        env_props = app_detail['ApplicationConfigurationDescription']['EnvironmentPropertyDescriptions']['PropertyGroupDescriptions'][0]['PropertyMap']
        
        # Update bootstrap servers to VPC connectivity
        env_props['bootstrap.servers'] = bootstrap_servers
        
        # Update application
        flink.update_application(
            ApplicationName=app_name,
            CurrentApplicationVersionId=current_version,
            ApplicationConfigurationUpdate={
                'EnvironmentPropertyUpdates': {
                    'PropertyGroups': [
                        {
                            'PropertyGroupId': 'consumer.config.0',
                            'PropertyMap': env_props
                        }
                    ]
                }
            }
        )
        
        print(f"✅ Updated {app_name} with VPC connectivity bootstrap servers")
        return True
        
    except Exception as e:
        print(f"❌ Error updating {app_name}: {e}")
        return False

def main():
    deployment_stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
    cluster_arn = f"arn:aws:kafka:us-east-1:195026230833:cluster/cms-{deployment_stage}-msk-cluster/f6af3ef9-60c4-4ce8-a69d-fc56630fa140-23"
    
    print(f"🔄 Updating Flink applications for VPC connectivity - {deployment_stage}")
    
    # Wait for VPC connectivity to be available
    print("⏳ Waiting for VPC connectivity to be enabled...")
    vpc_bootstrap = None
    for attempt in range(30):  # Wait up to 15 minutes
        vpc_bootstrap = get_vpc_connectivity_bootstrap_servers(cluster_arn)
        if vpc_bootstrap:
            break
        print(f"Attempt {attempt + 1}/30 - waiting 30 seconds...")
        time.sleep(30)
    
    if not vpc_bootstrap:
        print("❌ VPC connectivity bootstrap servers not available after 15 minutes")
        return False
    
    # List of Flink applications to update
    applications = [
        f"cms-{deployment_stage}-flink-event-driven-telemetry-processor",
        f"cms-{deployment_stage}-flink-trip-processor", 
        f"cms-{deployment_stage}-flink-safety-processor",
        f"cms-{deployment_stage}-flink-maintenance-processor",
        f"cms-{deployment_stage}-flink-telemetry-enhanced-final"
    ]
    
    success_count = 0
    for app_name in applications:
        if update_flink_application_bootstrap_servers(app_name, vpc_bootstrap):
            success_count += 1
    
    print(f"🎉 Updated {success_count}/{len(applications)} Flink applications")
    return success_count == len(applications)

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
