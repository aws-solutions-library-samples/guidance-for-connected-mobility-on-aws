"""
Campaign Manager Stack - Send data collection campaigns to vehicles via IoT Core
Cost-optimized for 5M vehicles using MQTT pub/sub
"""

from aws_cdk import (
    Stack,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    aws_iot as iot,
    aws_s3 as s3,
    aws_iam as iam,
    aws_events as events,
    aws_events_targets as targets,
    aws_sqs as sqs,
    Duration,
    RemovalPolicy,
    CfnOutput
)
from constructs import Construct

class CampaignManagerStack(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Campaign Definitions Storage (S3)
        self.campaign_bucket = s3.Bucket(
            self, "CampaignBucket",
            bucket_name=f"{construct_id}-campaigns",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    expiration=Duration.days(90),
                    noncurrent_version_expiration=Duration.days(30)
                )
            ]
        )
        
        # Campaign State Table
        self.campaigns_table = dynamodb.Table(
            self, "CampaignsTable",
            table_name=f"{construct_id}-campaigns",
            partition_key=dynamodb.Attribute(
                name="campaignId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="version",
                type=dynamodb.AttributeType.NUMBER
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
            removal_policy=RemovalPolicy.RETAIN
        )
        
        # GSI for active campaigns
        self.campaigns_table.add_global_secondary_index(
            index_name="status-startTime-index",
            partition_key=dynamodb.Attribute(
                name="status",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="startTime",
                type=dynamodb.AttributeType.NUMBER
            )
        )
        
        # Vehicle Campaign Enrollment Table
        self.vehicle_campaigns_table = dynamodb.Table(
            self, "VehicleCampaignsTable",
            table_name=f"{construct_id}-vehicle-campaigns",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="campaignId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN
        )
        
        # GSI for campaign-to-vehicles lookup
        self.vehicle_campaigns_table.add_global_secondary_index(
            index_name="campaignId-enrollmentTime-index",
            partition_key=dynamodb.Attribute(
                name="campaignId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="enrollmentTime",
                type=dynamodb.AttributeType.NUMBER
            )
        )
        
        # Campaign Acknowledgment Queue (for vehicle confirmations)
        self.ack_queue = sqs.Queue(
            self, "CampaignAckQueue",
            queue_name=f"{construct_id}-campaign-acks",
            visibility_timeout=Duration.seconds(300),
            retention_period=Duration.days(7)
        )
        
        # Lambda: Campaign Publisher
        self.publisher_lambda = lambda_.Function(
            self, "CampaignPublisher",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="index.handler",
            timeout=Duration.minutes(15),
            memory_size=1024,
            environment={
                "CAMPAIGNS_TABLE": self.campaigns_table.table_name,
                "VEHICLE_CAMPAIGNS_TABLE": self.vehicle_campaigns_table.table_name,
                "CAMPAIGN_BUCKET": self.campaign_bucket.bucket_name,
                "IOT_ENDPOINT": f"iot.{self.region}.amazonaws.com"
            },
            code=lambda_.Code.from_inline("""
import json
import boto3
import os
from datetime import datetime

dynamodb = boto3.resource('dynamodb')
iot_data = boto3.client('iot-data')
s3 = boto3.client('s3')

campaigns_table = dynamodb.Table(os.environ['CAMPAIGNS_TABLE'])
vehicle_campaigns_table = dynamodb.Table(os.environ['VEHICLE_CAMPAIGNS_TABLE'])

def handler(event, context):
    campaign_id = event['campaignId']
    target_type = event.get('targetType', 'broadcast')  # broadcast, individual, group
    
    # Get campaign definition
    campaign = campaigns_table.get_item(
        Key={'campaignId': campaign_id, 'version': event.get('version', 1)}
    )['Item']
    
    # Get campaign payload from S3
    payload_obj = s3.get_object(
        Bucket=os.environ['CAMPAIGN_BUCKET'],
        Key=f"{campaign_id}/definition.json"
    )
    campaign_payload = json.loads(payload_obj['Body'].read())
    
    # Publish based on target type
    if target_type == 'broadcast':
        # Publish to all vehicles
        topic = 'fleet/campaigns/broadcast'
        iot_data.publish(
            topic=topic,
            qos=1,
            payload=json.dumps({
                'campaignId': campaign_id,
                'type': campaign['type'],
                'priority': campaign.get('priority', 'normal'),
                'expiresAt': campaign.get('expiresAt'),
                'payload': campaign_payload
            })
        )
        
    elif target_type == 'individual':
        # Publish to specific vehicles
        vehicle_ids = event.get('vehicleIds', [])
        for vehicle_id in vehicle_ids:
            topic = f'fleet/campaigns/{vehicle_id}'
            iot_data.publish(
                topic=topic,
                qos=1,
                payload=json.dumps({
                    'campaignId': campaign_id,
                    'vehicleId': vehicle_id,
                    'type': campaign['type'],
                    'priority': campaign.get('priority', 'normal'),
                    'expiresAt': campaign.get('expiresAt'),
                    'payload': campaign_payload
                })
            )
            
            # Record enrollment
            vehicle_campaigns_table.put_item(
                Item={
                    'vehicleId': vehicle_id,
                    'campaignId': campaign_id,
                    'enrollmentTime': int(datetime.now().timestamp()),
                    'status': 'sent',
                    'sentAt': int(datetime.now().timestamp())
                }
            )
    
    elif target_type == 'group':
        # Publish to vehicle group
        group_id = event.get('groupId')
        topic = f'fleet/campaigns/group/{group_id}'
        iot_data.publish(
            topic=topic,
            qos=1,
            payload=json.dumps({
                'campaignId': campaign_id,
                'groupId': group_id,
                'type': campaign['type'],
                'priority': campaign.get('priority', 'normal'),
                'expiresAt': campaign.get('expiresAt'),
                'payload': campaign_payload
            })
        )
    
    # Update campaign status
    campaigns_table.update_item(
        Key={'campaignId': campaign_id, 'version': event.get('version', 1)},
        UpdateExpression='SET #status = :status, publishedAt = :publishedAt',
        ExpressionAttributeNames={'#status': 'status'},
        ExpressionAttributeValues={
            ':status': 'published',
            ':publishedAt': int(datetime.now().timestamp())
        }
    )
    
    return {'statusCode': 200, 'campaignId': campaign_id}
            """)
        )
        
        # Grant permissions
        self.campaigns_table.grant_read_write_data(self.publisher_lambda)
        self.vehicle_campaigns_table.grant_read_write_data(self.publisher_lambda)
        self.campaign_bucket.grant_read(self.publisher_lambda)
        
        self.publisher_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iot:Publish"],
                resources=[f"arn:aws:iot:{self.region}:{self.account}:topic/fleet/campaigns/*"]
            )
        )
        
        # Lambda: Campaign Acknowledgment Processor
        self.ack_processor_lambda = lambda_.Function(
            self, "CampaignAckProcessor",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="index.handler",
            timeout=Duration.seconds(60),
            environment={
                "VEHICLE_CAMPAIGNS_TABLE": self.vehicle_campaigns_table.table_name
            },
            code=lambda_.Code.from_inline("""
import json
import boto3
import os
from datetime import datetime

dynamodb = boto3.resource('dynamodb')
vehicle_campaigns_table = dynamodb.Table(os.environ['VEHICLE_CAMPAIGNS_TABLE'])

def handler(event, context):
    for record in event['Records']:
        message = json.loads(record['body'])
        
        vehicle_campaigns_table.update_item(
            Key={
                'vehicleId': message['vehicleId'],
                'campaignId': message['campaignId']
            },
            UpdateExpression='SET #status = :status, acknowledgedAt = :ackTime',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'acknowledged',
                ':ackTime': int(datetime.now().timestamp())
            }
        )
    
    return {'statusCode': 200}
            """)
        )
        
        self.vehicle_campaigns_table.grant_read_write_data(self.ack_processor_lambda)
        self.ack_processor_lambda.add_event_source(
            lambda_.SqsEventSource(self.ack_queue, batch_size=10)
        )
        
        # IoT Rule: Route acknowledgments to SQS
        ack_rule_role = iam.Role(
            self, "AckRuleRole",
            assumed_by=iam.ServicePrincipal("iot.amazonaws.com")
        )
        self.ack_queue.grant_send_messages(ack_rule_role)
        
        iot.CfnTopicRule(
            self, "CampaignAckRule",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM 'fleet/campaigns/ack'",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        sqs=iot.CfnTopicRule.SqsActionProperty(
                            queue_url=self.ack_queue.queue_url,
                            role_arn=ack_rule_role.role_arn
                        )
                    )
                ]
            )
        )
        
        # EventBridge Rule: Schedule campaign checks
        events.Rule(
            self, "CampaignScheduler",
            schedule=events.Schedule.rate(Duration.minutes(5)),
            targets=[targets.LambdaFunction(self.publisher_lambda)]
        )
        
        # Outputs
        CfnOutput(self, "CampaignBucketName", value=self.campaign_bucket.bucket_name)
        CfnOutput(self, "CampaignsTableName", value=self.campaigns_table.table_name)
        CfnOutput(self, "PublisherLambdaArn", value=self.publisher_lambda.function_arn)
