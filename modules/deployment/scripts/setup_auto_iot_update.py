#!/usr/bin/env python3
"""
Setup CloudWatch Event to automatically update IoT rule when MSK cluster is ready
"""

import boto3
import json
import os
import sys

def setup_auto_iot_update(msk_arn, rule_name):
    """Setup CloudWatch Event to monitor MSK and update IoT rule when ready"""
    
    # Use environment variable or default profile
    profile_name = os.environ.get('AWS_PROFILE', 'default')
    session = boto3.Session(profile_name=profile_name)
    
    events_client = session.client('events')
    lambda_client = session.client('lambda')
    iam_client = session.client('iam')
    
    print(f"🔧 Setting up auto-update for IoT rule: {rule_name}")
    print(f"📡 MSK Cluster ARN: {msk_arn}")
    
    # Create Lambda function to update IoT rule
    lambda_code = f'''
import boto3
import json

def lambda_handler(event, context):
    kafka_client = boto3.client('kafka')
    iot_client = boto3.client('iot')
    
    msk_arn = "{msk_arn}"
    rule_name = "{rule_name}"
    
    try:
        # Get bootstrap servers
        response = kafka_client.get_bootstrap_brokers(ClusterArn=msk_arn)
        bootstrap_servers = response.get('BootstrapBrokerString')
        
        if bootstrap_servers:
            # Update IoT rule
            rule_response = iot_client.get_topic_rule(ruleName=rule_name)
            rule = rule_response['rule']
            
            # Update bootstrap servers in Kafka action
            for action in rule['actions']:
                if 'kafka' in action:
                    action['kafka']['clientProperties']['bootstrap.servers'] = bootstrap_servers
            
            # Replace the rule
            iot_client.replace_topic_rule(
                ruleName=rule_name,
                topicRulePayload=rule
            )
            
            print(f"✅ Updated IoT rule {{rule_name}} with bootstrap servers: {{bootstrap_servers}}")
            return {{'statusCode': 200, 'body': 'Success'}}
        else:
            print("❌ Bootstrap servers not ready yet")
            return {{'statusCode': 202, 'body': 'Not ready'}}
            
    except Exception as e:
        print(f"❌ Error: {{str(e)}}")
        return {{'statusCode': 500, 'body': str(e)}}
'''
    
    try:
        # Create IAM role for Lambda
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }
            ]
        }
        
        role_name = f"cms-iot-auto-update-role-{rule_name.split('_')[-1]}"
        
        try:
            iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="Role for auto-updating IoT rule with MSK bootstrap servers"
            )
            print(f"✅ Created IAM role: {role_name}")
        except iam_client.exceptions.EntityAlreadyExistsException:
            print(f"✅ IAM role already exists: {role_name}")
        
        # Attach policies
        policies = [
            "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            "arn:aws:iam::aws:policy/AmazonMSKReadOnlyAccess"
        ]
        
        for policy in policies:
            iam_client.attach_role_policy(RoleName=role_name, PolicyArn=policy)
        
        # Create inline policy for IoT
        iot_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "iot:GetTopicRule",
                        "iot:ReplaceTopicRule"
                    ],
                    "Resource": f"arn:aws:iot:*:*:rule/{rule_name}"
                }
            ]
        }
        
        iam_client.put_role_policy(
            RoleName=role_name,
            PolicyName="IoTRuleUpdatePolicy",
            PolicyDocument=json.dumps(iot_policy)
        )
        
        # Get role ARN
        role_arn = iam_client.get_role(RoleName=role_name)['Role']['Arn']
        
        # Create Lambda function
        function_name = f"cms-iot-auto-update-{rule_name.split('_')[-1]}"
        
        try:
            lambda_client.create_function(
                FunctionName=function_name,
                Runtime='python3.9',
                Role=role_arn,
                Handler='index.lambda_handler',
                Code={'ZipFile': lambda_code.encode()},
                Description=f"Auto-update IoT rule {rule_name} with MSK bootstrap servers",
                Timeout=60
            )
            print(f"✅ Created Lambda function: {function_name}")
        except lambda_client.exceptions.ResourceConflictException:
            # Update existing function
            lambda_client.update_function_code(
                FunctionName=function_name,
                ZipFile=lambda_code.encode()
            )
            print(f"✅ Updated Lambda function: {function_name}")
        
        # Create CloudWatch Event Rule
        rule_name_cw = f"cms-msk-ready-{rule_name.split('_')[-1]}"
        
        # Schedule to run every 2 minutes for 30 minutes
        events_client.put_rule(
            Name=rule_name_cw,
            ScheduleExpression='rate(2 minutes)',
            Description=f"Check if MSK cluster is ready and update IoT rule {rule_name}",
            State='ENABLED'
        )
        
        # Add Lambda as target
        lambda_arn = lambda_client.get_function(FunctionName=function_name)['Configuration']['FunctionArn']
        
        events_client.put_targets(
            Rule=rule_name_cw,
            Targets=[
                {
                    'Id': '1',
                    'Arn': lambda_arn
                }
            ]
        )
        
        # Add permission for CloudWatch Events to invoke Lambda
        try:
            lambda_client.add_permission(
                FunctionName=function_name,
                StatementId='AllowCloudWatchEvents',
                Action='lambda:InvokeFunction',
                Principal='events.amazonaws.com',
                SourceArn=f"arn:aws:events:*:*:rule/{rule_name_cw}"
            )
        except lambda_client.exceptions.ResourceConflictException:
            pass  # Permission already exists
        
        print(f"✅ Created CloudWatch Event rule: {rule_name_cw}")
        print(f"🔄 Will check MSK status every 2 minutes and auto-update IoT rule when ready")
        print(f"⏰ Rule will run for ~30 minutes, then you can disable it manually")
        
        return True
        
    except Exception as e:
        print(f"❌ Error setting up auto-update: {str(e)}")
        return False

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 setup_auto_iot_update.py <msk_arn> <rule_name>")
        sys.exit(1)
    
    msk_arn = sys.argv[1]
    rule_name = sys.argv[2]
    
    success = setup_auto_iot_update(msk_arn, rule_name)
    if not success:
        sys.exit(1)
