"""
Flink Stack - Stream processing applications with MSK integration
"""

from aws_cdk import (
    Stack,
    aws_kinesisanalytics as kinesisanalytics,
    aws_iam as iam,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_dynamodb as dynamodb,
    aws_ec2 as ec2,
    aws_logs as logs,
    aws_lambda as lambda_,
    CustomResource,
    CfnOutput,
    RemovalPolicy,
    Duration
)
from constructs import Construct
from typing import Dict

class FlinkStack(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, 
                 storage_tables: Dict[str, dynamodb.Table], 
                 msk_stack=None, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Get VPC - use default VPC (same as MSK stack)
        self.vpc = ec2.Vpc.from_lookup(self, "DefaultVPC", is_default=True)
        
        # Use private subnets if available, otherwise public subnets
        subnets = self.vpc.private_subnets if self.vpc.private_subnets else self.vpc.public_subnets
        if len(subnets) < 2:
            subnets = self.vpc.public_subnets + self.vpc.private_subnets
        
        # S3 bucket for Flink JARs
        self.jar_bucket = s3.Bucket(
            self, "FlinkJarBucket",
            bucket_name=f"{construct_id}-flink-jars-{self.account}",
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY
        )
        
        # Use existing pre-built JAR directly
        self.jar_deployment = s3deploy.BucketDeployment(
            self, "FlinkJarDeployment",
            sources=[
                s3deploy.Source.asset("../modules/flink/target/cms-telemetry-processor-1.0.0.jar")
            ],
            destination_bucket=self.jar_bucket,
            destination_key_prefix="jars/"
        )
        
        # IAM role for Flink applications (matches working target account)
        self.flink_role = iam.Role(
            self, "FlinkExecutionRole",
            assumed_by=iam.ServicePrincipal("kinesisanalytics.amazonaws.com"),
            managed_policies=[
                # Use the same managed policies as working target account
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonKinesisAnalyticsFullAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonVPCFullAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name("SecretsManagerReadWrite")
            ]
        )
        
        # Add permissions for MSK access (conditional)
        if msk_stack:
            self.flink_role.add_to_policy(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "kafka-cluster:Connect",
                        "kafka-cluster:AlterCluster", 
                        "kafka-cluster:DescribeCluster",
                        "kafka-cluster:*Topic*",
                        "kafka-cluster:WriteData",
                        "kafka-cluster:ReadData",
                        "kafka-cluster:AlterGroup",
                        "kafka-cluster:DescribeGroup"
                    ],
                    resources=[
                        msk_stack.cluster_arn,
                        f"{msk_stack.cluster_arn}/topic/*",
                        f"{msk_stack.cluster_arn}/group/*"
                    ]
                )
            )
            
            # Add permissions for MSK cluster metadata
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
        
        # Add DynamoDB permissions for all tables
        for table in storage_tables.values():
            table.grant_read_write_data(self.flink_role)
        
        # Add S3 permissions for JAR bucket
        self.jar_bucket.grant_read(self.flink_role)
        
        # CloudWatch Log Groups for all Flink applications (must be created before apps)
        self.event_driven_telemetry_log_group = logs.LogGroup(
            self, "EventDrivenTelemetryProcessorLogGroup",
            log_group_name=f"/aws/kinesis-analytics/{construct_id}-event-driven-telemetry-processor",
            removal_policy=RemovalPolicy.DESTROY
        )
        
        self.telemetry_enhanced_log_group = logs.LogGroup(
            self, "TelemetryEnhancedLogGroup",
            log_group_name=f"/aws/kinesis-analytics/{construct_id}-telemetry-enhanced-final",
            removal_policy=RemovalPolicy.DESTROY
        )
        
        self.trip_log_group = logs.LogGroup(
            self, "TripProcessorLogGroup", 
            log_group_name=f"/aws/kinesis-analytics/{construct_id}-trip-processor",
            removal_policy=RemovalPolicy.DESTROY
        )
        
        self.safety_log_group = logs.LogGroup(
            self, "SafetyProcessorLogGroup",
            log_group_name=f"/aws/kinesis-analytics/{construct_id}-safety-processor", 
            removal_policy=RemovalPolicy.DESTROY
        )
        
        self.maintenance_log_group = logs.LogGroup(
            self, "MaintenanceProcessorLogGroup",
            log_group_name=f"/aws/kinesis-analytics/{construct_id}-maintenance-processor",
            removal_policy=RemovalPolicy.DESTROY
        )
        
        # Create log streams for each log group (required by flinkSetup.md)
        for log_group in [self.event_driven_telemetry_log_group, self.telemetry_enhanced_log_group, 
                         self.trip_log_group, self.safety_log_group, self.maintenance_log_group]:
            logs.LogStream(
                self, f"{log_group.node.id}Stream",
                log_group=log_group,
                log_stream_name="kinesis-analytics-log-stream",
                removal_policy=RemovalPolicy.DESTROY
            )
        
        # Common application configuration (matching flinkSetup.md requirements)
        def create_flink_app_config(processor_type: str, log_group: logs.LogGroup, additional_properties: Dict[str, str] = None):
            base_properties = {
                "PROCESSOR_TYPE": processor_type,
                "auto.offset.reset": "earliest",
                "enable.auto.commit": "false",
                "aws.region": self.region
            }
            
            # Add MSK configuration only if MSK stack is available
            if msk_stack:
                base_properties.update({
                    "bootstrap.servers": msk_stack.bootstrap_servers,
                    "security.protocol": "SASL_SSL",
                    "sasl.mechanism": "AWS_MSK_IAM",
                    "sasl.jaas.config": "software.amazon.msk.auth.iam.IAMLoginModule required;",
                    "sasl.client.callback.handler.class": "software.amazon.msk.auth.iam.IAMClientCallbackHandler",
                    "sasl.login.callback.handler.class": "software.amazon.msk.auth.iam.IAMClientCallbackHandler"
                })
            
            if additional_properties:
                base_properties.update(additional_properties)
                
            return {
                "ApplicationCodeConfiguration": {
                    "CodeContent": {
                        "S3ContentLocation": {
                            "BucketARN": self.jar_bucket.bucket_arn,
                            "FileKey": "jars/cms-telemetry-processor.jar"
                        }
                    },
                    "CodeContentType": "ZIPFILE"
                },
                "FlinkApplicationConfiguration": {
                    "CheckpointConfiguration": {
                        "ConfigurationType": "DEFAULT"
                    },
                    "MonitoringConfiguration": {
                        "ConfigurationType": "CUSTOM",
                        "MetricsLevel": "APPLICATION", 
                        "LogLevel": "INFO"
                    },
                    "ParallelismConfiguration": {
                        "ConfigurationType": "DEFAULT",
                        "Parallelism": 1,
                        "ParallelismPerKPU": 1,
                        "AutoScalingEnabled": True
                    }
                },
                "EnvironmentProperties": {
                    "PropertyGroups": [{
                        "PropertyGroupId": "consumer.config.0",
                        "PropertyMap": base_properties
                    }]
                }
            }
            
            # Add VPC configuration only if MSK stack is available
            if msk_stack:
                app_config["VpcConfigurations"] = [{
                    "SubnetIds": [subnet.subnet_id for subnet in subnets[:2]],
                    "SecurityGroupIds": [msk_stack.msk_security_group.security_group_id]
                }]
            
            return app_config
        
        # Custom resource to auto-start Flink applications
        flink_starter_role = iam.Role(
            self, "FlinkStarterRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ],
            inline_policies={
                "FlinkAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "kinesisanalytics:StartApplication",
                                "kinesisanalytics:DescribeApplication"
                            ],
                            resources=["*"]
                        )
                    ]
                )
            }
        )

        flink_starter_fn = lambda_.Function(
            self, "FlinkStarter",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="index.lambda_handler",
            role=flink_starter_role,
            timeout=Duration.minutes(5),
            code=lambda_.Code.from_inline("""
import json
import boto3
import cfnresponse
import logging
import time

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    logger.info(f"Event: {json.dumps(event)}")
    
    try:
        request_type = event['RequestType']
        app_name = event['ResourceProperties']['ApplicationName']
        
        client = boto3.client('kinesisanalyticsv2')
        
        if request_type == 'Create':
            # Wait for application to be ready, then start it
            max_attempts = 30  # 5 minutes
            for attempt in range(max_attempts):
                try:
                    response = client.describe_application(ApplicationName=app_name)
                    status = response['ApplicationDetail']['ApplicationStatus']
                    
                    logger.info(f"Attempt {attempt + 1}: Application {app_name} status is {status}")
                    
                    if status == 'READY':
                        logger.info(f"Starting application {app_name}")
                        client.start_application(
                            ApplicationName=app_name,
                            RunConfiguration={
                                'ApplicationRestoreConfiguration': {
                                    'ApplicationRestoreType': 'SKIP_RESTORE_FROM_SNAPSHOT'
                                }
                            }
                        )
                        logger.info(f"Successfully started application {app_name}")
                        cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
                        return
                    elif status == 'RUNNING':
                        logger.info(f"Application {app_name} is already running")
                        cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
                        return
                    elif status in ['DELETING', 'STOPPING']:
                        raise Exception(f"Application {app_name} is in failed state: {status}")
                    
                    time.sleep(10)
                    
                except Exception as e:
                    if attempt == max_attempts - 1:
                        raise e
                    logger.warning(f"Attempt {attempt + 1} failed: {e}")
                    time.sleep(10)
            
            raise Exception(f"Timeout waiting for application {app_name} to be ready")
            
        else:
            # For Update/Delete, just return success
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
            
    except Exception as e:
        logger.error(f"Error: {str(e)}", exc_info=True)
        cfnresponse.send(event, context, cfnresponse.FAILED, {}, str(e))
            """)
        )
        
        # 1. Event-Driven Telemetry Processor (matches cms-event-driven-telemetry-processor)
        self.event_driven_telemetry_processor = kinesisanalytics.CfnApplicationV2(
            self, "EventDrivenTelemetryProcessor",
            application_name=f"{construct_id}-event-driven-telemetry-processor",
            runtime_environment="FLINK-1_18",
            service_execution_role=self.flink_role.role_arn,
            application_configuration=create_flink_app_config(
                "EventDrivenTelemetryProcessor",
                self.event_driven_telemetry_log_group,
                {
                    "KAFKA_TOPIC": "cms-telemetry-raw",
                    "group.id": f"{construct_id}-event-driven-telemetry-consumer"
                }
            ),
            application_description="Event-driven telemetry processor with MSK integration"
        )
        # Ensure JAR is deployed before application is created
        self.event_driven_telemetry_processor.node.add_dependency(self.jar_deployment)
        
        # Add CloudWatch logging to event-driven telemetry processor
        kinesisanalytics.CfnApplicationCloudWatchLoggingOptionV2(
            self, "EventDrivenTelemetryLogging",
            application_name=self.event_driven_telemetry_processor.ref,
            cloud_watch_logging_option=kinesisanalytics.CfnApplicationCloudWatchLoggingOptionV2.CloudWatchLoggingOptionProperty(
                log_stream_arn=f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.event_driven_telemetry_log_group.log_group_name}:log-stream:kinesis-analytics-log-stream"
            )
        )
        
        # 2. Telemetry Enhanced Final Processor (matches cms-telemetry-enhanced-final)
        self.telemetry_enhanced_processor = kinesisanalytics.CfnApplicationV2(
            self, "TelemetryEnhancedProcessor",
            application_name=f"{construct_id}-telemetry-enhanced-final",
            application_description="Enhanced telemetry processor with advanced analytics",
            runtime_environment="FLINK-1_18",
            service_execution_role=self.flink_role.role_arn,
            application_configuration=create_flink_app_config(
                "TelemetryDataProcessor",  # Matches target account
                self.telemetry_enhanced_log_group,
                {
                    "KAFKA_TOPIC": "cms-telemetry-processed",
                    "TELEMETRY_TABLE_NAME": storage_tables["telemetry"].table_name,
                    "group.id": f"{construct_id}-telemetry-enhanced-consumer"
                }
            )
        )
        # Ensure JAR is deployed before application is created
        self.telemetry_enhanced_processor.node.add_dependency(self.jar_deployment)
        
        # Add CloudWatch logging to telemetry enhanced processor
        kinesisanalytics.CfnApplicationCloudWatchLoggingOptionV2(
            self, "TelemetryEnhancedLogging",
            application_name=self.telemetry_enhanced_processor.ref,
            cloud_watch_logging_option=kinesisanalytics.CfnApplicationCloudWatchLoggingOptionV2.CloudWatchLoggingOptionProperty(
                log_stream_arn=f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.telemetry_enhanced_log_group.log_group_name}:log-stream:kinesis-analytics-log-stream"
            )
        )
        
        # 3. Trip Processor (matches cms-trip-processor)
        self.trip_processor = kinesisanalytics.CfnApplicationV2(
            self, "TripProcessor",
            application_name=f"{construct_id}-trip-processor", 
            application_description="Trip data processor with DynamoDB integration",
            runtime_environment="FLINK-1_18",
            service_execution_role=self.flink_role.role_arn,
            application_configuration=create_flink_app_config(
                "TripProcessor",
                self.trip_log_group,
                {
                    "group.id": "trip-processor-consumer-fixed",
                    "TRIPS_TABLE_NAME": storage_tables['trips'].table_name
                }
            )
        )
        # Ensure JAR is deployed before application is created
        self.trip_processor.node.add_dependency(self.jar_deployment)
        
        # Add CloudWatch logging to trip processor
        kinesisanalytics.CfnApplicationCloudWatchLoggingOptionV2(
            self, "TripProcessorLogging",
            application_name=self.trip_processor.ref,
            cloud_watch_logging_option=kinesisanalytics.CfnApplicationCloudWatchLoggingOptionV2.CloudWatchLoggingOptionProperty(
                log_stream_arn=f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.trip_log_group.log_group_name}:log-stream:kinesis-analytics-log-stream"
            )
        )
        
        # 4. Safety Processor (matches cms-safety-processor)
        self.safety_processor = kinesisanalytics.CfnApplicationV2(
            self, "SafetyProcessor",
            application_name=f"{construct_id}-safety-processor",
            application_description="Safety events processor with DynamoDB integration", 
            runtime_environment="FLINK-1_18",
            service_execution_role=self.flink_role.role_arn,
            application_configuration=create_flink_app_config(
                "SafetyProcessor",
                self.safety_log_group,
                {
                    "group.id": "safety-processor-consumer",
                    "SAFETY_EVENTS_TABLE_NAME": storage_tables['safety_events'].table_name
                }
            )
        )
        # Ensure JAR is deployed before application is created
        self.safety_processor.node.add_dependency(self.jar_deployment)
        
        # Add CloudWatch logging to safety processor
        kinesisanalytics.CfnApplicationCloudWatchLoggingOptionV2(
            self, "SafetyProcessorLogging",
            application_name=self.safety_processor.ref,
            cloud_watch_logging_option=kinesisanalytics.CfnApplicationCloudWatchLoggingOptionV2.CloudWatchLoggingOptionProperty(
                log_stream_arn=f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.safety_log_group.log_group_name}:log-stream:kinesis-analytics-log-stream"
            )
        )
        
        # 5. Maintenance Processor (matches cms-maintenance-processor-template)
        self.maintenance_processor = kinesisanalytics.CfnApplicationV2(
            self, "MaintenanceProcessor",
            application_name=f"{construct_id}-maintenance-processor",
            application_description="Maintenance processor with UniversalProcessor entry point",
            runtime_environment="FLINK-1_18",
            service_execution_role=self.flink_role.role_arn,
            application_configuration=create_flink_app_config(
                "MaintenanceProcessor",
                self.maintenance_log_group,
                {
                    "group.id": "cms-maintenance-processor-template-consumer",
                    "MAINTENANCE_TABLE_NAME": storage_tables['maintenance_events'].table_name
                }
            )
        )
        # Ensure JAR is deployed before application is created
        self.maintenance_processor.node.add_dependency(self.jar_deployment)
        
        # Add CloudWatch logging to maintenance processor
        kinesisanalytics.CfnApplicationCloudWatchLoggingOptionV2(
            self, "MaintenanceProcessorLogging",
            application_name=self.maintenance_processor.ref,
            cloud_watch_logging_option=kinesisanalytics.CfnApplicationCloudWatchLoggingOptionV2.CloudWatchLoggingOptionProperty(
                log_stream_arn=f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.maintenance_log_group.log_group_name}:log-stream:kinesis-analytics-log-stream"
            )
        )
        
        # Auto-start all Flink applications after they're created        
        CustomResource(
            self, "StartEventDrivenTelemetry",
            service_token=flink_starter_fn.function_arn,
            properties={"ApplicationName": self.event_driven_telemetry_processor.ref}
        )
        
        CustomResource(
            self, "StartTelemetryEnhanced", 
            service_token=flink_starter_fn.function_arn,
            properties={"ApplicationName": self.telemetry_enhanced_processor.ref}
        )
        
        CustomResource(
            self, "StartTripProcessor",
            service_token=flink_starter_fn.function_arn,
            properties={"ApplicationName": self.trip_processor.ref}
        )
        
        CustomResource(
            self, "StartSafetyProcessor",
            service_token=flink_starter_fn.function_arn,
            properties={"ApplicationName": self.safety_processor.ref}
        )
        
        CustomResource(
            self, "StartMaintenanceProcessor",
            service_token=flink_starter_fn.function_arn,
            properties={"ApplicationName": self.maintenance_processor.ref}
        )
        
        # Store applications for easy access
        self.applications = {
            'event_driven_telemetry_processor': self.event_driven_telemetry_processor,
            'telemetry_enhanced_processor': self.telemetry_enhanced_processor,
            'trip_processor': self.trip_processor, 
            'safety_processor': self.safety_processor,
            'maintenance_processor': self.maintenance_processor
        }
        
        # Outputs
        CfnOutput(
            self, "FlinkJarBucketOutput",
            value=self.jar_bucket.bucket_name,
            export_name=f"{construct_id}-jar-bucket"
        )
        
        CfnOutput(
            self, "FlinkRoleArn",
            value=self.flink_role.role_arn,
            export_name=f"{construct_id}-flink-role-arn"
        )
        
        for app_name, app in self.applications.items():
            # Replace underscores with hyphens for export names
            export_name = app_name.replace('_', '-')
            CfnOutput(
                self, f"{app_name.title().replace('_', '')}AppName",
                value=app.application_name,
                export_name=f"{construct_id}-{export_name}-name"
            )
