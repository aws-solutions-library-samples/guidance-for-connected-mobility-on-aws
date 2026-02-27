"""
FleetWise Integration Stack
Separate topics and rules for FleetWise telemetry and heartbeat
"""

from aws_cdk import (
    Stack,
    aws_iot as iot,
    aws_iam as iam,
    aws_msk as msk,
    CfnOutput,
    Fn
)
from constructs import Construct

class FleetWiseStack(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        deployment_stage = construct_id.split('-')[1] if '-' in construct_id else 'dev'
        msk_stack_name = f"cms-{deployment_stage}-msk"
        
        # Import MSK cluster details
        msk_cluster_arn = Fn.import_value(f"{msk_stack_name}-cluster-arn")
        msk_bootstrap_servers = Fn.import_value(f"{msk_stack_name}-bootstrap-servers")
        msk_vpc_id = Fn.import_value(f"{msk_stack_name}-vpc-id")
        msk_security_group_id = Fn.import_value(f"{msk_stack_name}-security-group-id")
        
        # IAM Role for IoT Rules to write to Kafka
        self.iot_kafka_role = iam.Role(
            self, "IoTKafkaRole",
            assumed_by=iam.ServicePrincipal("iot.amazonaws.com"),
            description="Role for IoT Rules to write to MSK"
        )
        
        # Grant Kafka write permissions
        self.iot_kafka_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "kafka:DescribeCluster",
                    "kafka:GetBootstrapBrokers",
                    "kafka-cluster:Connect",
                    "kafka-cluster:WriteData",
                    "kafka-cluster:DescribeTopic"
                ],
                resources=[msk_cluster_arn]
            )
        )
        
        # Grant VPC permissions for IoT Rules
        self.iot_kafka_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ec2:CreateNetworkInterface",
                    "ec2:DescribeNetworkInterfaces",
                    "ec2:DeleteNetworkInterface",
                    "ec2:DescribeSubnets",
                    "ec2:DescribeSecurityGroups",
                    "ec2:DescribeVpcs"
                ],
                resources=["*"]
            )
        )
        
        # Rule 1: FleetWise Telemetry → Kafka (cms-telemetry-fw)
        iot.CfnTopicRule(
            self, "FleetWiseTelemetryRule",
            rule_name=f"cms_{deployment_stage}_fw_telemetry_to_kafka",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="""
                    SELECT 
                        topic(3) as vehicleId,
                        timestamp() as timestamp,
                        * as payload
                    FROM '$aws/things/+/signals'
                """,
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        kafka=iot.CfnTopicRule.KafkaActionProperty(
                            destination_arn=msk_cluster_arn,
                            topic="cms-telemetry-fw",
                            client_properties={
                                "bootstrap.servers": msk_bootstrap_servers
                            }
                        )
                    )
                ],
                description="Route FleetWise telemetry to Kafka cms-telemetry-fw topic",
                error_action=iot.CfnTopicRule.ActionProperty(
                    cloudwatch_logs=iot.CfnTopicRule.CloudwatchLogsActionProperty(
                        log_group_name=f"/aws/iot/rules/cms-{deployment_stage}-fw-telemetry",
                        role_arn=self.iot_kafka_role.role_arn
                    )
                )
            )
        )
        
        # Rule 2: FleetWise Checkin → Kafka (cms-heartbeat-fw)
        iot.CfnTopicRule(
            self, "FleetWiseCheckinRule",
            rule_name=f"cms_{deployment_stage}_fw_checkin_to_kafka",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="""
                    SELECT 
                        topic(3) as vehicleId,
                        timestamp,
                        activeCollectionSchemes,
                        telemetry
                    FROM '$aws/things/+/checkin'
                """,
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        kafka=iot.CfnTopicRule.KafkaActionProperty(
                            destination_arn=msk_cluster_arn,
                            topic="cms-heartbeat-fw",
                            client_properties={
                                "bootstrap.servers": msk_bootstrap_servers
                            }
                        )
                    )
                ],
                description="Route FleetWise checkins to Kafka cms-heartbeat-fw topic",
                error_action=iot.CfnTopicRule.ActionProperty(
                    cloudwatch_logs=iot.CfnTopicRule.CloudwatchLogsActionProperty(
                        log_group_name=f"/aws/iot/rules/cms-{deployment_stage}-fw-checkin",
                        role_arn=self.iot_kafka_role.role_arn
                    )
                )
            )
        )
        
        # Rule 3: Custom Heartbeat → Kafka (cms-heartbeat-custom)
        iot.CfnTopicRule(
            self, "CustomHeartbeatRule",
            rule_name=f"cms_{deployment_stage}_custom_heartbeat_to_kafka",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="""
                    SELECT 
                        vehicleId,
                        timestamp,
                        activeCampaigns,
                        telemetry
                    FROM 'vehicle/+/heartbeat'
                """,
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        kafka=iot.CfnTopicRule.KafkaActionProperty(
                            destination_arn=msk_cluster_arn,
                            topic="cms-heartbeat-custom",
                            client_properties={
                                "bootstrap.servers": msk_bootstrap_servers
                            }
                        )
                    )
                ],
                description="Route custom heartbeats to Kafka cms-heartbeat-custom topic",
                error_action=iot.CfnTopicRule.ActionProperty(
                    cloudwatch_logs=iot.CfnTopicRule.CloudwatchLogsActionProperty(
                        log_group_name=f"/aws/iot/rules/cms-{deployment_stage}-custom-heartbeat",
                        role_arn=self.iot_kafka_role.role_arn
                    )
                )
            )
        )
        
        # Outputs
        CfnOutput(self, "FleetWiseTelemetryTopic", 
                  value="$aws/things/{vehicleId}/signals",
                  description="FleetWise telemetry topic")
        
        CfnOutput(self, "FleetWiseCheckinTopic", 
                  value="$aws/things/{vehicleId}/checkin",
                  description="FleetWise checkin topic")
        
        CfnOutput(self, "FleetWiseCollectionSchemesTopic", 
                  value="$aws/things/{vehicleId}/collectionSchemes",
                  description="FleetWise collection schemes topic")
        
        CfnOutput(self, "KafkaTelemetryTopic", 
                  value="cms-telemetry-fw",
                  description="Kafka topic for FleetWise telemetry")
        
        CfnOutput(self, "KafkaHeartbeatFWTopic", 
                  value="cms-heartbeat-fw",
                  description="Kafka topic for FleetWise heartbeats")
        
        CfnOutput(self, "KafkaHeartbeatCustomTopic", 
                  value="cms-heartbeat-custom",
                  description="Kafka topic for custom heartbeats")
