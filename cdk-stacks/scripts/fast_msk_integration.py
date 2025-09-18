#!/usr/bin/env python3
"""
Fast MSK Integration Script - Updates both IoT and Flink configurations in parallel
"""

import boto3
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
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

def get_msk_bootstrap_servers(cluster_arn: str) -> str:
    """Get SCRAM bootstrap servers from MSK cluster"""
    kafka = boto3.client('kafka')
    try:
        response = kafka.get_bootstrap_brokers(ClusterArn=cluster_arn)
        if 'BootstrapBrokerStringSaslScram' in response:
            return response['BootstrapBrokerStringSaslScram']
        elif 'BootstrapBrokerString' in response:
            return response['BootstrapBrokerString']
        else:
            raise Exception("No bootstrap servers found")
    except Exception as e:
        print(f"Error getting bootstrap servers: {e}")
        raise

def update_iot_rules(msk_config: Dict[str, str]) -> bool:
    """Update IoT rules with MSK configuration"""
    try:
        print("🔗 Updating IoT rules with MSK configuration...")
        iot = boto3.client('iot')
        
        # Get existing topic rules
        rules_response = iot.list_topic_rules()
        
        for rule in rules_response.get('rules', []):
            rule_name = rule['ruleName']
            if 'telemetry' in rule_name.lower() or 'msk' in rule_name.lower():
                print(f"  Updating rule: {rule_name}")
                
                # Get rule details
                rule_details = iot.get_topic_rule(ruleName=rule_name)
                rule_payload = rule_details['rule']
                
                # Update actions to include MSK destination
                actions = rule_payload.get('actions', [])
                
                # Add MSK action if not exists
                msk_action_exists = any('kafka' in str(action).lower() for action in actions)
                if not msk_action_exists:
                    actions.append({
                        'kafka': {
                            'destinationArn': msk_config['cluster_arn'],
                            'topic': 'telemetry-data',
                            'clientProperties': {
                                'bootstrap.servers': msk_config['bootstrap_servers'],
                                'security.protocol': 'SASL_SSL',
                                'sasl.mechanism': 'SCRAM-SHA-512'
                            }
                        }
                    })
                
                # Update the rule
                iot.replace_topic_rule(
                    ruleName=rule_name,
                    topicRulePayload={
                        'sql': rule_payload['sql'],
                        'actions': actions,
                        'ruleDisabled': rule_payload.get('ruleDisabled', False)
                    }
                )
        
        print("✅ IoT rules updated successfully")
        return True
    except Exception as e:
        print(f"❌ Error updating IoT rules: {e}")
        return False

def update_flink_application(msk_config: Dict[str, str], flink_app_name: str) -> bool:
    """Update Flink application with MSK configuration"""
    try:
        print("⚡ Updating Flink application with MSK configuration...")
        kinesisanalytics = boto3.client('kinesisanalyticsv2')
        
        # Get current application configuration
        app_response = kinesisanalytics.describe_application(ApplicationName=flink_app_name)
        app_detail = app_response['ApplicationDetail']
        
        current_version = app_detail['ApplicationVersionId']
        
        # Update environment properties
        env_properties = []
        
        # Keep existing properties
        if 'ApplicationConfigurationDescription' in app_detail:
            config_desc = app_detail['ApplicationConfigurationDescription']
            if 'EnvironmentPropertyDescriptions' in config_desc:
                for prop_group in config_desc['EnvironmentPropertyDescriptions']['PropertyGroupDescriptions']:
                    if prop_group['PropertyGroupId'] != 'kafka.config':
                        env_properties.append({
                            'PropertyGroupId': prop_group['PropertyGroupId'],
                            'PropertyMap': prop_group['PropertyMap']
                        })
        
        # Add updated MSK configuration
        env_properties.append({
            'PropertyGroupId': 'kafka.config',
            'PropertyMap': {
                'bootstrap.servers': msk_config['bootstrap_servers'],
                'security.protocol': 'SASL_SSL',
                'sasl.mechanism': 'SCRAM-SHA-512',
                'sasl.username': '${secretsmanager:' + msk_config['secret_arn'] + ':username}',
                'sasl.password': '${secretsmanager:' + msk_config['secret_arn'] + ':password}',
                'topic.name': 'telemetry-data'
            }
        })
        
        # Update application
        kinesisanalytics.update_application(
            ApplicationName=flink_app_name,
            CurrentApplicationVersionId=current_version,
            ApplicationConfigurationUpdate={
                'EnvironmentPropertyUpdates': {
                    'PropertyGroups': env_properties
                }
            }
        )
        
        print("✅ Flink application updated successfully")
        return True
    except Exception as e:
        print(f"❌ Error updating Flink application: {e}")
        return False

def main():
    import os
    
    # Get deployment stage from environment
    deployment_stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
    
    print(f"🚀 Fast MSK Integration for stage: {deployment_stage}")
    
    # Get stack outputs
    msk_stack_name = f"cms-{deployment_stage}-msk"
    flink_stack_name = f"cms-{deployment_stage}-flink"
    
    print("📊 Getting stack information...")
    msk_outputs = get_stack_outputs(msk_stack_name)
    flink_outputs = get_stack_outputs(flink_stack_name)
    
    if not msk_outputs.get('MSKClusterArn'):
        print("❌ MSK stack not found or not deployed")
        return False
    
    if not flink_outputs.get('FlinkApplicationName'):
        print("❌ Flink stack not found or not deployed")
        return False
    
    # Get MSK configuration
    cluster_arn = msk_outputs['MSKClusterArn']
    secret_arn = msk_outputs.get('IoTUserSecretArn', '')
    
    print("🔍 Getting MSK bootstrap servers...")
    bootstrap_servers = get_msk_bootstrap_servers(cluster_arn)
    
    msk_config = {
        'cluster_arn': cluster_arn,
        'bootstrap_servers': bootstrap_servers,
        'secret_arn': secret_arn
    }
    
    print(f"✅ MSK Configuration: {bootstrap_servers}")
    
    # Run updates in parallel
    print("🔄 Running parallel updates...")
    
    with ThreadPoolExecutor(max_workers=2) as executor:
        # Submit both tasks
        iot_future = executor.submit(update_iot_rules, msk_config)
        flink_future = executor.submit(update_flink_application, msk_config, flink_outputs['FlinkApplicationName'])
        
        # Wait for completion
        iot_success = iot_future.result()
        flink_success = flink_future.result()
    
    if iot_success and flink_success:
        print("🎉 Fast MSK integration completed successfully!")
        return True
    else:
        print("❌ Some integrations failed")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
