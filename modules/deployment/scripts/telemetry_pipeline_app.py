#!/usr/bin/env python3
"""
Telemetry Pipeline App with IoT Integration
Complete pipeline: IoT Core → MSK → Flink → DynamoDB
"""

import os
import sys
# Add the deployment directory to Python path so we can import telemetry_pipeline
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aws_cdk import App, Stack, RemovalPolicy, aws_dynamodb as dynamodb, CfnOutput, CfnParameter, Environment
import aws_cdk.aws_ec2 as ec2
from constructs import Construct
from telemetry_pipeline.msk_construct import MSKConstruct
from telemetry_pipeline.flink_construct import FlinkConstruct

class TelemetryPipelineStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Parameters
        trips_table_name = CfnParameter(self, "TripsTableName", 
                                      type="String", 
                                      default="cms-telemetry-trips")
        safety_events_table_name = CfnParameter(self, "SafetyEventsTableName", 
                                               type="String", 
                                               default="cms-telemetry-safety-events")
        maintenance_alerts_table_name = CfnParameter(self, "MaintenanceAlertsTableName", 
                                                    type="String", 
                                                    default="cms-telemetry-maintenance-alerts")
        
        # Use existing VPC instead of creating new one
        vpc = ec2.Vpc.from_lookup(self, "ExistingVPC", is_default=True)
        
        # Create MSK cluster
        msk_construct = MSKConstruct(self, "MSKCluster", vpc=vpc)
        
        # Create Flink processing
        flink_construct = FlinkConstruct(
            self, "FlinkProcessor",
            vpc=vpc,
            msk_cluster_arn=msk_construct.msk_cluster.attr_arn,
            msk_security_group_id=msk_construct.msk_security_group.security_group_id,
            bootstrap_servers="",  # Will be updated after deployment
            trips_table_name=trips_table_name.value_as_string,
            safety_events_table_name=safety_events_table_name.value_as_string,
            maintenance_alerts_table_name=maintenance_alerts_table_name.value_as_string
        )
        
        # Outputs
        CfnOutput(self, "MSKClusterArn", value=msk_construct.msk_cluster.attr_arn)
        CfnOutput(self, "MSKBootstrapServers", value=msk_construct.bootstrap_getter.get_response_field("BootstrapBrokerStringSaslScram"))
        CfnOutput(self, "FlinkS3Bucket", value=flink_construct.flink_asset.s3_bucket_name)

app = App()
TelemetryPipelineStack(app, "cms-telemetry-pipeline", 
                      env=Environment(
                          account=os.environ.get('CDK_DEFAULT_ACCOUNT'),
                          region=os.environ.get('CDK_DEFAULT_REGION', 'us-east-1')
                      ))
app.synth()
