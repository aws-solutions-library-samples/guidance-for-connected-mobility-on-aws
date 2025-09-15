#!/usr/bin/env python3
"""
Telemetry Pipeline App with MSK Serverless
Complete pipeline: IoT Core → MSK Serverless → Flink → DynamoDB
"""

from aws_cdk import App, Stack, CfnOutput, CfnParameter
from constructs import Construct
import sys
sys.path.append("..")
from telemetry_pipeline.msk_serverless_construct import MSKServerlessConstruct
import sys
sys.path.append("..")
from telemetry_pipeline.flink_construct import FlinkConstruct
import aws_cdk.aws_ec2 as ec2

class TelemetryPipelineServerlessStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Add parameters for existing table names
        trips_table_param = CfnParameter(
            self, "TripsTableName",
            type="String",
            description="Name of existing trips DynamoDB table",
            default="cms-trips"
        )
        
        safety_events_table_param = CfnParameter(
            self, "SafetyEventsTableName", 
            type="String",
            description="Name of existing safety events DynamoDB table",
            default="cms-safety-events"
        )
        
        maintenance_alerts_table_param = CfnParameter(
            self, "MaintenanceAlertsTableName",
            type="String", 
            description="Name of existing maintenance alerts DynamoDB table",
            default="cms-maintenance-alerts"
        )
        
        # Create minimal VPC for MSK Serverless
        vpc = ec2.Vpc(
            self, "TelemetryVPC",
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24
                )
            ]
        )
        
        # Create MSK Serverless construct
        self.msk_construct = MSKServerlessConstruct(
            self, "MSKServerless",
            vpc=vpc
        )
        
        # Create Flink processor with dynamic table names
        self.flink_processor = FlinkConstruct(
            self, "FlinkProcessor",
            vpc=vpc,
            msk_cluster_arn=self.msk_construct.msk_cluster.attr_arn,
            trips_table_name=trips_table_param.value_as_string,
            safety_events_table_name=safety_events_table_param.value_as_string,
            maintenance_alerts_table_name=maintenance_alerts_table_param.value_as_string
        )
        
        # Create outputs
        CfnOutput(
            self, "MSKServerlessClusterArn",
            value=self.msk_construct.msk_cluster.attr_arn,
            description="MSK Serverless cluster ARN"
        )

app = App()
TelemetryPipelineServerlessStack(app, "cms-telemetry-pipeline")
app.synth()
