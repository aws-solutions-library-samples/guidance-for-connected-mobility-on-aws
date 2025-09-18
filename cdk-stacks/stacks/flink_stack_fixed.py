"""
Fixed Flink Stack - Matches working target account configuration
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
    RemovalPolicy
)
from constructs import Construct
from typing import Dict

class FlinkStackFixed(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, 
                 storage_tables: Dict[str, dynamodb.Table], **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Get default VPC (same as MSK)
        self.vpc = ec2.Vpc.from_lookup(self, "DefaultVPC", is_default=True)
        subnets = self.vpc.private_subnets if self.vpc.private_subnets else self.vpc.public_subnets
        
        # Get MSK security group
        msk_sg = ec2.SecurityGroup.from_lookup_by_name(
            self, "MSKSecurityGroup", 
            "MSKSecurityGroup", 
            self.vpc
        )
        
        # S3 bucket for JARs
        self.jar_bucket = s3.Bucket(
            self, "FlinkJarBucket",
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY
        )
        
        # IAM role with MSK IAM permissions (matches target account)
        self.flink_role = iam.Role(
            self, "FlinkExecutionRole",
            assumed_by=iam.ServicePrincipal("kinesisanalytics.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("CloudWatchLogsFullAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonVPCFullAccess")
            ]
        )
        
        # MSK IAM permissions (exact format from documentation)
        self.flink_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "kafka-cluster:Connect",
                    "kafka-cluster:AlterCluster", 
                    "kafka-cluster:DescribeCluster"
                ],
                resources=[f"arn:aws:kafka:{self.region}:{self.account}:cluster/*"]
            )
        )
        
        self.flink_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "kafka-cluster:*Topic*",
                    "kafka-cluster:WriteData",
                    "kafka-cluster:ReadData"
                ],
                resources=[f"arn:aws:kafka:{self.region}:{self.account}:topic/*/*"]
            )
        )
        
        self.flink_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "kafka-cluster:AlterGroup",
                    "kafka-cluster:DescribeGroup"
                ],
                resources=[f"arn:aws:kafka:{self.region}:{self.account}:group/*/*"]
            )
        )
        
        self.flink_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "kafka:DescribeCluster",
                    "kafka:DescribeClusterV2", 
                    "kafka:GetBootstrapBrokers"
                ],
                resources=["*"]
            )
        )
        
        # DynamoDB permissions (ARN-based since we don't have table objects)
        self.flink_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:PutItem", 
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:Query",
                    "dynamodb:Scan"
                ],
                resources=[f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-*"]
            )
        )
        
        # S3 permissions
        self.jar_bucket.grant_read(self.flink_role)
        
        # Create applications with proper configuration
        processors = [
            {
                "name": "event-driven-telemetry-processor",
                "processor_type": "EventDrivenTelemetryProcessor",
                "group_id": "cms-raw-telemetry-processor-v2-consumer"
            },
            {
                "name": "trip-processor", 
                "processor_type": "TripProcessor",
                "group_id": "trip-processor-consumer-fixed",
                "trips_table": storage_tables.get("trips", {}).table_name if storage_tables.get("trips") else "cms-dev-trips"
            },
            {
                "name": "safety-processor",
                "processor_type": "SafetyProcessor", 
                "group_id": "safety-processor-consumer"
            },
            {
                "name": "maintenance-processor",
                "processor_type": "MaintenanceProcessor",
                "group_id": "maintenance-processor-consumer"
            },
            {
                "name": "telemetry-enhanced-final",
                "processor_type": "TelemetryEnhancedProcessor",
                "group_id": "telemetry-enhanced-consumer"
            }
        ]
        
        for processor in processors:
            self._create_flink_application(processor, subnets[:2], [msk_sg.security_group_id])
    
    def _create_flink_application(self, processor_config: dict, subnets: list, security_groups: list):
        app_name = f"cms-dev-flink-{processor_config['name']}"
        
        # CloudWatch logging (log group created automatically)
        log_group_name = f"/aws/kinesis-analytics/{app_name}"
        
        # Environment properties (matches target account format)
        env_properties = {
            "PROCESSOR_TYPE": processor_config["processor_type"],
            "auto.offset.reset": "earliest",
            "aws.region": self.region,
            "bootstrap.servers": "PLACEHOLDER_WILL_BE_UPDATED_BY_SCRIPT",
            "enable.auto.commit": "false", 
            "group.id": processor_config["group_id"],
            "sasl.client.callback.handler.class": "software.amazon.msk.auth.iam.IAMClientCallbackHandler",
            "sasl.jaas.config": "software.amazon.msk.auth.iam.IAMLoginModule required;",
            "sasl.mechanism": "AWS_MSK_IAM",
            "security.protocol": "SASL_SSL"
        }
        
        # Add table names for processors that need them
        if "trips_table" in processor_config:
            env_properties["TRIPS_TABLE_NAME"] = processor_config["trips_table"]
        
        # Create Flink application
        app = kinesisanalytics.CfnApplication(
            self, f"FlinkApp{processor_config['name'].replace('-', '')}",
            application_name=app_name,
            application_description=f"{processor_config['processor_type']} with MSK IAM integration",
            runtime_environment="FLINK-1_18",
            service_execution_role=self.flink_role.role_arn,
            application_configuration=kinesisanalytics.CfnApplication.ApplicationConfigurationProperty(
                application_code_configuration=kinesisanalytics.CfnApplication.ApplicationCodeConfigurationProperty(
                    code_content=kinesisanalytics.CfnApplication.CodeContentProperty(
                        s3_content_location=kinesisanalytics.CfnApplication.S3ContentLocationProperty(
                            bucket_arn=self.jar_bucket.bucket_arn,
                            file_key="jars/cms-telemetry-processor-1.0.0.jar"
                        )
                    ),
                    code_content_type="ZIPFILE"
                ),
                flink_application_configuration=kinesisanalytics.CfnApplication.FlinkApplicationConfigurationProperty(
                    monitoring_configuration=kinesisanalytics.CfnApplication.MonitoringConfigurationProperty(
                        configuration_type="CUSTOM",
                        metrics_level="APPLICATION",
                        log_level="INFO"
                    ),
                    parallelism_configuration=kinesisanalytics.CfnApplication.ParallelismConfigurationProperty(
                        configuration_type="DEFAULT"
                    )
                ),
                vpc_configurations=[
                    kinesisanalytics.CfnApplication.VpcConfigurationProperty(
                        subnet_ids=[subnet.subnet_id for subnet in subnets],
                        security_group_ids=security_groups
                    )
                ],
                environment_properties=kinesisanalytics.CfnApplication.EnvironmentPropertiesProperty(
                    property_groups=[
                        kinesisanalytics.CfnApplication.PropertyGroupProperty(
                            property_group_id="consumer.config.0",
                            property_map=env_properties
                        )
                    ]
                )
            )
        )
        
        # Add CloudWatch logging (required at creation time)
        app.add_property_override("CloudWatchLoggingOptions", [
            {
                "LogStreamARN": f"arn:aws:logs:{self.region}:{self.account}:log-group:{log_group_name}:log-stream:kinesis-analytics-log-stream"
            }
        ])
        
        # Outputs
        CfnOutput(
            self, f"{processor_config['name']}AppName",
            value=app_name,
            export_name=f"cms-dev-flink-{processor_config['name']}-name"
        )
