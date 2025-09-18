"""
Optimized Flink Stack - Deploys independently of MSK, connects later
"""

from aws_cdk import (
    Stack,
    aws_kinesisanalytics as kinesisanalytics,
    aws_iam as iam,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
    aws_ec2 as ec2,
    aws_logs as logs,
    CfnOutput,
    RemovalPolicy,
    Duration
)
from constructs import Construct
from typing import Dict, Optional

class FlinkStackOptimized(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, 
                 storage_tables: Dict[str, dynamodb.Table], 
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Get VPC
        self.vpc = ec2.Vpc.from_lookup(self, "DefaultVPC", is_default=True)
        subnets = self.vpc.private_subnets if self.vpc.private_subnets else self.vpc.public_subnets
        
        # S3 bucket for Flink JARs
        self.jar_bucket = s3.Bucket(
            self, "FlinkJarBucket",
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY
        )
        
        # IAM role with MSK permissions (even if MSK doesn't exist yet)
        self.flink_role = iam.Role(
            self, "FlinkExecutionRole",
            assumed_by=iam.ServicePrincipal("kinesisanalytics.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/KinesisAnalyticsServiceRole-ApplicationV2"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonMSKReadOnlyAccess")
            ]
        )
        
        # Add MSK permissions proactively
        self.flink_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "kafka:DescribeCluster",
                "kafka:GetBootstrapBrokers",
                "kafka:ListClusters",
                "kafka-cluster:Connect",
                "kafka-cluster:AlterCluster",
                "kafka-cluster:DescribeCluster"
            ],
            resources=["*"]  # Will be restricted later via integration script
        ))
        
        # Add Secrets Manager permissions for SCRAM
        self.flink_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "secretsmanager:GetSecretValue",
                "secretsmanager:DescribeSecret"
            ],
            resources=[f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:*msk*"]
        ))
        
        # DynamoDB permissions
        for table in storage_tables.values():
            table.grant_read_write_data(self.flink_role)
        
        # S3 permissions
        self.jar_bucket.grant_read(self.flink_role)
        
        # CloudWatch log group
        self.log_group = logs.LogGroup(
            self, "FlinkLogGroup",
            log_group_name=f"/aws/kinesis-analytics/{construct_id}",
            removal_policy=RemovalPolicy.DESTROY,
            retention=logs.RetentionDays.ONE_WEEK
        )
        
        # Create Flink application with placeholder configuration
        # MSK configuration will be updated later via integration script
        self.flink_app = kinesisanalytics.CfnApplicationV2(
            self, "FlinkApplication",
            runtime_environment="FLINK-1_18",
            service_execution_role=self.flink_role.role_arn,
            application_name=f"{construct_id}-telemetry-processor",
            application_description="CMS Telemetry Processing Application",
            application_configuration=kinesisanalytics.CfnApplicationV2.ApplicationConfigurationProperty(
                application_code_configuration=kinesisanalytics.CfnApplicationV2.ApplicationCodeConfigurationProperty(
                    code_content=kinesisanalytics.CfnApplicationV2.CodeContentProperty(
                        s3_content_location=kinesisanalytics.CfnApplicationV2.S3ContentLocationProperty(
                            bucket_arn=self.jar_bucket.bucket_arn,
                            file_key="jars/cms-telemetry-processor-1.0.0.jar"
                        )
                    ),
                    code_content_type="ZIPFILE"
                ),
                environment_properties=kinesisanalytics.CfnApplicationV2.EnvironmentPropertiesProperty(
                    property_groups=[
                        kinesisanalytics.CfnApplicationV2.PropertyGroupProperty(
                            property_group_id="kinesis.analytics.flink.run.options",
                            property_map={
                                "python.fn-execution.bundle.time": "1000",
                                "python.fn-execution.bundle.size": "5000"
                            }
                        ),
                        # Placeholder for MSK configuration - will be updated by integration script
                        kinesisanalytics.CfnApplicationV2.PropertyGroupProperty(
                            property_group_id="kafka.config",
                            property_map={
                                "bootstrap.servers": "placeholder-will-be-updated",
                                "security.protocol": "SASL_SSL",
                                "sasl.mechanism": "SCRAM-SHA-512"
                            }
                        )
                    ]
                ),
                flink_application_configuration=kinesisanalytics.CfnApplicationV2.FlinkApplicationConfigurationProperty(
                    checkpoint_configuration=kinesisanalytics.CfnApplicationV2.CheckpointConfigurationProperty(
                        configuration_type="CUSTOM",
                        checkpointing_enabled=True,
                        checkpoint_interval=60000,
                        min_pause_between_checkpoints=5000
                    ),
                    monitoring_configuration=kinesisanalytics.CfnApplicationV2.MonitoringConfigurationProperty(
                        configuration_type="CUSTOM",
                        log_level="INFO",
                        metrics_level="APPLICATION"
                    ),
                    parallelism_configuration=kinesisanalytics.CfnApplicationV2.ParallelismConfigurationProperty(
                        configuration_type="CUSTOM",
                        parallelism=2,
                        parallelism_per_kpu=1,
                        auto_scaling_enabled=True
                    )
                )
            )
        )
        
        # Outputs
        CfnOutput(
            self, "FlinkApplicationName",
            value=self.flink_app.ref,
            export_name=f"{construct_id}-app-name"
        )
        
        CfnOutput(
            self, "FlinkJarBucketOutput",
            value=self.jar_bucket.bucket_name,
            export_name=f"{construct_id}-jar-bucket"
        )
        
        CfnOutput(
            self, "FlinkRoleArn",
            value=self.flink_role.role_arn,
            export_name=f"{construct_id}-role-arn"
        )
