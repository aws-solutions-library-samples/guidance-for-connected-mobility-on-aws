#!/usr/bin/env python3
"""
Complete Telemetry Pipeline Construct for CMS
IoT Core → MSK → Flink → DynamoDB
"""

from aws_cdk import (
    aws_ec2 as ec2,
    aws_dynamodb as dynamodb,
    CfnOutput
)
from constructs import Construct

from .msk_construct import MSKConstruct
from .flink_construct import FlinkConstruct

class TelemetryPipelineConstruct(Construct):
    """Complete telemetry processing pipeline for CMS"""
    
    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.Vpc, trips_table: dynamodb.Table, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        self.vpc = vpc
        self.trips_table = trips_table
        
        # Create MSK cluster for message streaming
        self.msk_construct = MSKConstruct(
            self, "MSK",
            vpc=self.vpc
        )
        
        # Create Flink application for trip processing
        self.flink_construct = FlinkConstruct(
            self, "Flink",
            vpc=self.vpc,
            msk_cluster_arn=self.msk_construct.msk_cluster.attr_arn,
            trips_table_name=self.trips_table.table_name
        )
        
        # Add dependency
        self.flink_construct.node.add_dependency(self.msk_construct)
        
        # Create outputs
        self._create_outputs()
    
    def _create_outputs(self):
        """Create CloudFormation outputs for the complete pipeline"""
        
        CfnOutput(
            self, "TelemetryPipelineStatus",
            value="DEPLOYED",
            description="Status of the complete telemetry pipeline"
        )
        
        CfnOutput(
            self, "PipelineComponents",
            value="IoT Core → MSK → Flink → DynamoDB",
            description="Components in the telemetry processing pipeline"
        )
        
        CfnOutput(
            self, "MSKClusterArn",
            value=self.msk_construct.msk_cluster.attr_arn,
            description="ARN of the MSK cluster"
        )
        
        CfnOutput(
            self, "FlinkApplicationName",
            value=self.flink_construct.flink_application.application_name,
            description="Name of the Flink trip processing application"
        )
        
        CfnOutput(
            self, "IoTTopicPattern",
            value="cms/telemetry/vehicle/+",
            description="IoT topic pattern for telemetry data"
        )
        
        CfnOutput(
            self, "KafkaTopics",
            value="cms-telemetry-raw, cms-trips, cms-safety-events, cms-vehicle-status",
            description="Kafka topics created for telemetry processing"
        )
