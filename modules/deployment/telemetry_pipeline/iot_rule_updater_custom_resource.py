#!/usr/bin/env python3
"""
Custom Resource for updating IoT rule with MSK bootstrap servers
"""

from aws_cdk import (
    CustomResource,
    Duration,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_logs as logs,
    CfnOutput
)
from constructs import Construct
import json

class IoTRuleUpdaterCustomResource(Construct):
    """Custom resource to update IoT rule with MSK bootstrap servers when cluster is ready"""
    
    def __init__(self, scope: Construct, construct_id: str, 
                 msk_cluster_arn: str, 
                 iot_rule_name: str,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        self.msk_cluster_arn = msk_cluster_arn
        self.iot_rule_name = iot_rule_name
        
        # Create the Lambda function for the custom resource
        self.lambda_function = self._create_lambda_function()
        
        # Create the custom resource
        self.custom_resource = self._create_custom_resource()
        
        # Create outputs
        self._create_outputs()
    
    def _create_lambda_function(self) -> lambda_.Function:
        """Create Lambda function to handle IoT rule updates"""
        
        # Create IAM role for Lambda
        lambda_role = iam.Role(
            self, "IoTRuleUpdaterRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonMSKReadOnlyAccess")
            ],
            inline_policies={
                "IoTRuleUpdatePolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "iot:GetTopicRule",
                                "iot:ReplaceTopicRule"
                            ],
                            resources=[f"arn:aws:iot:*:*:rule/{self.iot_rule_name}"]
                        )
                    ]
                )
            }
        )
        
        # Lambda function code with embedded cfnresponse
        lambda_code = f'''
import boto3
import json
import time
import urllib3
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Embedded cfnresponse module
SUCCESS = "SUCCESS"
FAILED = "FAILED"

def send_response(event, context, response_status, response_data, physical_resource_id=None, no_echo=False):
    response_url = event['ResponseURL']
    
    response_body = {{
        'Status': response_status,
        'Reason': f'See CloudWatch Log Stream: {{context.log_stream_name}}',
        'PhysicalResourceId': physical_resource_id or context.log_stream_name,
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'NoEcho': no_echo,
        'Data': response_data
    }}
    
    json_response_body = json.dumps(response_body)
    
    headers = {{
        'content-type': '',
        'content-length': str(len(json_response_body))
    }}
    
    try:
        http = urllib3.PoolManager()
        response = http.request('PUT', response_url, body=json_response_body, headers=headers)
        logger.info(f"Status code: {{response.status}}")
    except Exception as e:
        logger.error(f"send_response failed: {{e}}")

def lambda_handler(event, context):
    logger.info(f"Event: {{json.dumps(event)}}")
    
    request_type = event['RequestType']
    
    try:
        if request_type in ['Create', 'Update']:
            result = update_iot_rule_with_retry()
            send_response(event, context, SUCCESS, result)
        else:  # Delete
            send_response(event, context, SUCCESS, {{
                "Status": "DELETED",
                "BootstrapServers": "N/A",
                "RuleName": "N/A",
                "UpdatedAt": "N/A"
            }})
            
    except Exception as e:
        logger.error(f"Error: {{str(e)}}")
        # Always return required attributes even on failure
        send_response(event, context, FAILED, {{
            "Error": str(e),
            "Status": "FAILED",
            "BootstrapServers": "FAILED",
            "RuleName": "{self.iot_rule_name}",
            "UpdatedAt": str(int(time.time()))
        }})

def update_iot_rule_with_retry():
    kafka_client = boto3.client('kafka')
    iot_client = boto3.client('iot')
    
    msk_arn = "{self.msk_cluster_arn}"
    rule_name = "{self.iot_rule_name}"
    
    logger.info(f"Updating IoT rule {{rule_name}} with MSK cluster {{msk_arn}}")
    
    # Try up to 10 times with exponential backoff
    for attempt in range(10):
        try:
            logger.info(f"Attempt {{attempt + 1}}: Checking MSK bootstrap servers...")
            
            # Get bootstrap servers
            response = kafka_client.get_bootstrap_brokers(ClusterArn=msk_arn)
            bootstrap_servers = response.get('BootstrapBrokerString')
            
            if bootstrap_servers and bootstrap_servers != 'None':
                logger.info(f"✅ Bootstrap servers ready: {{bootstrap_servers}}")
                
                # Get current IoT rule
                rule_response = iot_client.get_topic_rule(ruleName=rule_name)
                rule = rule_response['rule']
                
                # Update bootstrap servers in Kafka action
                updated = False
                for action in rule['actions']:
                    if 'kafka' in action:
                        old_servers = action['kafka']['clientProperties'].get('bootstrap.servers', 'None')
                        action['kafka']['clientProperties']['bootstrap.servers'] = bootstrap_servers
                        updated = True
                        logger.info(f"Updated bootstrap servers from {{old_servers}} to {{bootstrap_servers}}")
                
                if updated:
                    # Replace the rule
                    iot_client.replace_topic_rule(
                        ruleName=rule_name,
                        topicRulePayload=rule
                    )
                    
                    logger.info(f"✅ Successfully updated IoT rule {{rule_name}}")
                    return {{
                        "Status": "SUCCESS",
                        "BootstrapServers": bootstrap_servers,
                        "RuleName": rule_name,
                        "UpdatedAt": str(int(time.time()))
                    }}
                else:
                    logger.error("❌ No Kafka action found in IoT rule")
                    return {{
                        "Status": "NO_KAFKA_ACTION",
                        "Error": "No Kafka action found in IoT rule",
                        "BootstrapServers": bootstrap_servers,
                        "RuleName": rule_name,
                        "UpdatedAt": str(int(time.time()))
                    }}
            else:
                logger.info(f"⏳ Bootstrap servers not ready yet (attempt {{attempt + 1}}/10)")
                if attempt < 9:  # Don't sleep on last attempt
                    sleep_time = min(30 * (2 ** attempt), 300)  # Exponential backoff, max 5 minutes
                    logger.info(f"Waiting {{sleep_time}} seconds before next attempt...")
                    time.sleep(sleep_time)
                
        except Exception as e:
            logger.error(f"❌ Error on attempt {{attempt + 1}}: {{str(e)}}")
            if attempt < 9:
                time.sleep(30)
    
    # If we get here, all attempts failed
    logger.error("❌ Failed to update IoT rule after 10 attempts")
    return {{
        "Status": "FAILED", 
        "Error": "Bootstrap servers not ready after multiple attempts",
        "Attempts": 10,
        "BootstrapServers": "NOT_READY",
        "RuleName": rule_name,
        "UpdatedAt": str(int(time.time()))
    }}
'''
        
        # Create Lambda function
        lambda_function = lambda_.Function(
            self, "IoTRuleUpdaterFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="index.lambda_handler",
            code=lambda_.Code.from_inline(lambda_code),
            role=lambda_role,
            timeout=Duration.minutes(15),  # Allow time for retries
            log_retention=logs.RetentionDays.ONE_WEEK,
            description=f"Update IoT rule {self.iot_rule_name} with MSK bootstrap servers"
        )
        
        return lambda_function
    
    def _create_custom_resource(self) -> CustomResource:
        """Create the custom resource"""
        
        custom_resource = CustomResource(
            self, "IoTRuleUpdaterCustomResource",
            service_token=self.lambda_function.function_arn,
            properties={
                "MSKClusterArn": self.msk_cluster_arn,
                "IoTRuleName": self.iot_rule_name,
                "Timestamp": str(int(time.time()))  # Force update on each deployment
            }
        )
        
        return custom_resource
    
    def _create_outputs(self):
        """Create CloudFormation outputs"""
        
        CfnOutput(
            self, "IoTRuleUpdaterStatus",
            value=self.custom_resource.get_att_string("Status"),
            description="Status of IoT rule bootstrap server update"
        )
        
        CfnOutput(
            self, "IoTRuleBootstrapServers", 
            value=self.custom_resource.get_att_string("BootstrapServers"),
            description="Bootstrap servers configured in IoT rule"
        )
        
        CfnOutput(
            self, "IoTRuleUpdateTimestamp",
            value=self.custom_resource.get_att_string("UpdatedAt"),
            description="Timestamp when IoT rule was last updated"
        )

import time  # Add this import at the top
