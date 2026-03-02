"""
Flink Stack - Stream processing applications with MSK integration
"""

from aws_cdk import (
    Stack,
    aws_iam as iam,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_dynamodb as dynamodb,
    aws_ec2 as ec2,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_kinesisanalyticsv2 as kinesisanalytics,
    CfnOutput,
    RemovalPolicy,
    Duration,
    Fn
)
import aws_cdk.aws_kinesisanalytics_flink_alpha as flink
from constructs import Construct
from typing import Dict

class FlinkStack(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, 
                 storage_tables: Dict[str, dynamodb.Table], 
                 msk_stack=None, 
                 msk_cluster_arn: str = None,
                 msk_vpc_id: str = None,
                 msk_security_group_id: str = None,
                 msk_subnet_ids: list = None,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Get VPC - prioritize hardcoded MSK values, then MSK stack, then default VPC
        if msk_vpc_id and msk_security_group_id and msk_subnet_ids:
            # Use hardcoded MSK VPC values (highest priority)
            self.vpc = ec2.Vpc.from_lookup(self, "DefaultVPC", is_default=True)  # For stack resources
            subnets = self.vpc.private_subnets if self.vpc.private_subnets else self.vpc.public_subnets
            msk_security_group = None  # Not needed for hardcoded approach
            msk_available = True
            print(f"✅ Using hardcoded MSK VPC: {msk_vpc_id}")
        elif msk_stack:
            # MSK stack passed as parameter (full deployment)
            self.vpc = msk_stack.vpc
            subnets = self.vpc.private_subnets if self.vpc.private_subnets else self.vpc.public_subnets
            msk_security_group = msk_stack.msk_security_group
            msk_available = True
        elif msk_cluster_arn:
            # MSK cluster ARN provided - use CloudFormation imports for VPC config
            stage = construct_id.split('-')[1]  # Extract 'dev' from 'cms-dev-flink'
            
            # Use default VPC for the Flink stack itself, but configure applications with MSK VPC
            self.vpc = ec2.Vpc.from_lookup(self, "DefaultVPC", is_default=True)
            subnets = self.vpc.private_subnets if self.vpc.private_subnets else self.vpc.public_subnets
            
            # Create a placeholder security group (won't be used in VPC config)
            msk_security_group = ec2.SecurityGroup(
                self, "FlinkSecurityGroup",
                vpc=self.vpc,
                description="Security group for Flink applications",
                allow_all_outbound=True
            )
            
            # Store MSK configuration for use in application VPC config
            self.msk_vpc_id = Fn.import_value(f"cms-{stage}-msk-vpc-id")
            self.msk_sg_id = Fn.import_value(f"cms-{stage}-msk-security-group-id")
            self.msk_subnet_ids = Fn.split(",", Fn.import_value(f"cms-{stage}-msk-private-subnet-ids"))
            
            msk_available = True
        else:
            # No MSK configuration - use default VPC
            self.vpc = ec2.Vpc.from_lookup(self, "DefaultVPC", is_default=True)
            subnets = self.vpc.private_subnets if self.vpc.private_subnets else self.vpc.public_subnets
            # Create a basic security group for Flink
            msk_security_group = ec2.SecurityGroup(
                self, "FlinkSecurityGroup",
                vpc=self.vpc,
                description="Security group for Flink applications",
                allow_all_outbound=True
            )
            msk_available = False
        
        if len(subnets) < 2:
            subnets = self.vpc.public_subnets + self.vpc.private_subnets
        
        # S3 bucket for Flink JARs
        self.jar_bucket = s3.Bucket(
            self, "FlinkJarBucket",
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True
        )
        
        # Upload real Flink JAR file
        jar_s3_key = "jars/cms-telemetry-processor-1.0.0.zip"
        
        s3deploy.BucketDeployment(
            self, "FlinkJarDeployment",
            sources=[s3deploy.Source.asset("../modules/flink/target", exclude=["**", "!cms-telemetry-processor-1.0.0.zip"])],
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
                iam.ManagedPolicy.from_aws_managed_policy_name("SecretsManagerReadWrite"),
                iam.ManagedPolicy.from_aws_managed_policy_name("CloudWatchAgentServerPolicy")
            ]
        )
        
        # Add comprehensive MSK access policy (matches FlinkMSKAccess from target account)
        self.flink_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "kafka-cluster:Connect",
                    "kafka-cluster:AlterCluster",
                    "kafka-cluster:DescribeCluster",
                    "kafka-cluster:CreateTopic",
                    "kafka-cluster:DeleteTopic", 
                    "kafka-cluster:DescribeTopic",
                    "kafka-cluster:AlterTopic",
                    "kafka-cluster:DescribeTopicDynamicConfiguration",
                    "kafka-cluster:AlterTopicDynamicConfiguration",
                    "kafka-cluster:WriteData", 
                    "kafka-cluster:ReadData",
                    "kafka-cluster:AlterGroup",
                    "kafka-cluster:DescribeGroup",
                    "kafka:DescribeCluster",
                    "kafka:DescribeClusterV2",
                    "kafka:GetBootstrapBrokers",
                    "kafka:ListClusters"
                ],
                resources=["*"]
            )
        )
        
        # Add enhanced VPC access policy (matches EnhancedFlinkVPCAccess from target account)
        self.flink_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "ec2:CreateNetworkInterface",
                    "ec2:DeleteNetworkInterface", 
                    "ec2:DescribeNetworkInterfaces",
                    "ec2:DescribeVpcs",
                    "ec2:DescribeSubnets",
                    "ec2:DescribeSecurityGroups",
                    "ec2:DescribeDhcpOptions",
                    "ec2:CreateNetworkInterfacePermission",
                    "ec2:AttachNetworkInterface",
                    "ec2:DetachNetworkInterface"
                ],
                resources=["*"]
            )
        )
        
        # Add CloudWatch logs permissions for Flink applications
        self.flink_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogGroups",
                    "logs:DescribeLogStreams"
                ],
                resources=[
                    f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/kinesis-analytics/*"
                ]
            )
        )
        
        # Add S3 delete permissions for checkpoint cleanup - restricted to JAR bucket only
        self.flink_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3:DeleteObject",
                    "s3:DeleteObjectVersion"
                ],
                resources=[f"{self.jar_bucket.bucket_arn}/*"]
            )
        )

        
        # Add specific MSK cluster permissions if MSK is available
        if msk_available:
            try:
                cluster_arn = Fn.import_value(f"cms-{construct_id.split('-')[1]}-msk-cluster-arn")
                self.flink_role.add_to_policy(
                    iam.PolicyStatement(
                        effect=iam.Effect.ALLOW,
                        actions=[
                            "kafka-cluster:Connect",
                            "kafka-cluster:AlterCluster",
                            "kafka-cluster:DescribeCluster",
                            "kafka-cluster:CreateTopic",
                            "kafka-cluster:DeleteTopic", 
                            "kafka-cluster:DescribeTopic",
                            "kafka-cluster:AlterTopic",
                            "kafka-cluster:DescribeTopicDynamicConfiguration",
                            "kafka-cluster:AlterTopicDynamicConfiguration",
                            "kafka-cluster:WriteData",
                            "kafka-cluster:ReadData", 
                            "kafka-cluster:AlterGroup",
                            "kafka-cluster:DescribeGroup"
                        ],
                        resources=[
                            cluster_arn,
                            f"{cluster_arn}/topic/*",
                            f"{cluster_arn}/group/*"
                        ]
                    )
                )
            except Exception as e:
                print(f"Could not import MSK cluster ARN: {e}")
        
        # Add DynamoDB permissions for all tables
        for table in storage_tables.values():
            if hasattr(table, 'grant_read_write_data'):
                table.grant_read_write_data(self.flink_role)
        
        # Add explicit DynamoDB permissions for storage tables
        self.flink_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:PutItem",
                    "dynamodb:GetItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:Query",
                    "dynamodb:Scan",
                    "dynamodb:BatchGetItem",
                    "dynamodb:BatchWriteItem"
                ],
                resources=[
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-*-storage-*",
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-*-storage-*/index/*"
                ]
            )
        )
        
        # Add S3 permissions for JAR bucket
        self.jar_bucket.grant_read(self.flink_role)
        
        # Add S3 write permissions for datalake bucket
        datalake_bucket_name = storage_tables.get('datalake_bucket_name')
        if datalake_bucket_name:
            self.flink_role.add_to_policy(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["s3:PutObject", "s3:PutObjectAcl"],
                    resources=[f"arn:aws:s3:::{datalake_bucket_name}/*"]
                )
            )
        
        # CloudWatch Log Groups for all Flink applications
        self.event_driven_telemetry_log_group = logs.LogGroup(
            self, "EventDrivenTelemetryProcessorLogGroup",
            log_group_name=f"/aws/kinesis-analytics/{construct_id}-event-driven-telemetry-processor",
            removal_policy=RemovalPolicy.RETAIN
        )
        
        self.oem_telemetry_log_group = logs.LogGroup(
            self, "OEMTelemetryProcessorLogGroup",
            log_group_name=f"/aws/kinesis-analytics/{construct_id}-oem-telemetry-processor",
            removal_policy=RemovalPolicy.RETAIN
        )

        self.fw_telemetry_log_group = logs.LogGroup(
            self, "FWTelemetryProcessorLogGroup",
            log_group_name=f"/aws/kinesis-analytics/{construct_id}-fw-telemetry-processor",
            removal_policy=RemovalPolicy.DESTROY,
            retention=logs.RetentionDays.TWO_WEEKS,
        )
        
        self.simulator_preprocessor_log_group = logs.LogGroup(
            self, "SimulatorPreprocessorLogGroup",
            log_group_name=f"/aws/kinesis-analytics/{construct_id}-simulator-preprocessor",
            removal_policy=RemovalPolicy.DESTROY,
            retention=logs.RetentionDays.TWO_WEEKS,
        )
        
        self.telemetry_enhanced_log_group = logs.LogGroup(
            self, "TelemetryEnhancedLogGroup",
            log_group_name=f"/aws/kinesis-analytics/{construct_id}-telemetry-enhanced-final",
            removal_policy=RemovalPolicy.RETAIN
        )
        
        self.trip_log_group = logs.LogGroup(
            self, "TripProcessorLogGroup", 
            log_group_name=f"/aws/kinesis-analytics/{construct_id}-trip-processor",
            removal_policy=RemovalPolicy.RETAIN
        )
        
        self.safety_log_group = logs.LogGroup(
            self, "SafetyProcessorLogGroup",
            log_group_name=f"/aws/kinesis-analytics/{construct_id}-safety-processor", 
            removal_policy=RemovalPolicy.RETAIN
        )
        
        self.maintenance_log_group = logs.LogGroup(
            self, "MaintenanceProcessorLogGroup",
            log_group_name=f"/aws/kinesis-analytics/{construct_id}-maintenance-processor",
            removal_policy=RemovalPolicy.RETAIN
        )
        
        # Create log streams for each log group
        for log_group in [self.event_driven_telemetry_log_group, self.oem_telemetry_log_group,
                         self.fw_telemetry_log_group, self.simulator_preprocessor_log_group,
                         self.telemetry_enhanced_log_group, 
                         self.trip_log_group, self.safety_log_group, self.maintenance_log_group]:
            logs.LogStream(
                self, f"{log_group.node.id}Stream",
                log_group=log_group,
                log_stream_name="kinesis-analytics-log-stream",
                removal_policy=RemovalPolicy.RETAIN
            )
        
        # Create VPC configuration once for all applications (BEFORE the nested function)
        vpc_configuration_for_apps = None
        if msk_available:
            if msk_stack:
                # Use MSK stack subnets and security group
                vpc_configuration_for_apps = {
                    "SubnetIds": [subnet.subnet_id for subnet in subnets[:2]],
                    "SecurityGroupIds": [msk_security_group.security_group_id]
                }
            elif hasattr(self, 'msk_vpc_id'):
                # Use imported MSK VPC configuration
                vpc_configuration_for_apps = {
                    "SubnetIds": [
                        Fn.select(0, self.msk_subnet_ids),
                        Fn.select(1, self.msk_subnet_ids)
                    ],
                    "SecurityGroupIds": [self.msk_sg_id]
                }
        
        # Common application configuration (matching flinkSetup.md requirements)
        def create_flink_app_config(processor_type: str, additional_properties: Dict[str, str] = None):
            base_properties = {
                "PROCESSOR_TYPE": processor_type,
                "auto.offset.reset": "latest",
                "enable.auto.commit": "false",
                "aws.region": self.region
            }
            
            # Add MSK configuration only if MSK is available
            if msk_available:
                base_properties.update({
                    # Bootstrap servers will be resolved at runtime from MSK cluster
                    "msk.cluster.arn": Fn.import_value(f"{construct_id.replace('-flink', '-msk')}-cluster-arn"),
                    "security.protocol": "SASL_SSL",
                    "sasl.mechanism": "SCRAM-SHA-512",
                    "sasl.username": "iot-user-fixed",
                    "secret.arn": Fn.import_value(f"{construct_id.replace('-flink', '-msk')}-iot-user-secret-arn"),
                    "input.topic": "cms-telemetry-raw",
                    "group.id": f"cms-{construct_id.split('-')[1]}-flink-consumer-group"
                })
            
            if additional_properties:
                base_properties.update(additional_properties)
                
            app_config = {
                "ApplicationCodeConfiguration": {
                    "CodeContent": {
                        "S3ContentLocation": {
                            "BucketARN": self.jar_bucket.bucket_arn,
                            "FileKey": jar_s3_key
                        }
                    },
                    "CodeContentType": "ZIPFILE"
                },
                "FlinkApplicationConfiguration": {
                    "CheckpointConfiguration": {
                        "ConfigurationType": "CUSTOM",
                        "CheckpointingEnabled": True,
                        "CheckpointInterval": 60000,
                        "MinPauseBetweenCheckpoints": 5000
                    },
                    "MonitoringConfiguration": {
                        "ConfigurationType": "CUSTOM",
                        "MetricsLevel": "APPLICATION", 
                        "LogLevel": "DEBUG"
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
            
            # Add VPC configuration for MSK connectivity
            vpc_config = None
            if msk_available:
                if msk_stack:
                    # Use MSK stack subnets and security group
                    vpc_config = {
                        "SubnetIds": [subnet.subnet_id for subnet in subnets[:2]],
                        "SecurityGroupIds": [msk_security_group.security_group_id]
                    }
                elif hasattr(self, 'msk_vpc_id'):
                    # Use imported MSK VPC configuration
                    vpc_config = {
                        "SubnetIds": [
                            Fn.select(0, self.msk_subnet_ids),
                            Fn.select(1, self.msk_subnet_ids)
                        ],
                        "SecurityGroupIds": [self.msk_sg_id]
                    }
            
            # Add VPC configuration to ALL applications
            if vpc_configuration_for_apps:
                app_config["VpcConfigurations"] = [vpc_configuration_for_apps]
            
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
        
        # Create VPC configuration for applications
        vpc_configuration = None
        if msk_vpc_id and msk_security_group_id and msk_subnet_ids:
            # Use hardcoded MSK VPC values
            vpc_configuration = {
                "SubnetIds": msk_subnet_ids[:2],  # Use first 2 subnets
                "SecurityGroupIds": [msk_security_group_id]
            }
        elif msk_stack:
            # Use MSK stack subnets and security group
            vpc_configuration = {
                "SubnetIds": [subnet.subnet_id for subnet in subnets[:2]],
                "SecurityGroupIds": [msk_security_group.security_group_id]
            }

        # 1. Event-Driven Telemetry Router (reads preprocessed, routes to domain topics)
        app_config = {
            "EnvironmentPropertyDescriptions": {
                "PropertyGroupDescriptions": [{
                    "PropertyGroupId": "consumer.config.0",
                    "PropertyMap": {
                        "PROCESSOR_TYPE": "EventDrivenTelemetryProcessor",
                        "auto.offset.reset": "earliest",
                        "enable.auto.commit": "false",
                        "aws.region": self.region,
                        "KAFKA_TOPIC": "cms-telemetry-preprocessed",
                        "group.id": f"{construct_id}-event-driven-telemetry-consumer",
                        "TABLE_NAME": storage_tables['vehicles'].table_name,
                    }
                }]
            }
        }
        
        if vpc_configuration:
            app_config["VpcConfigurations"] = [vpc_configuration]
            
        self.event_driven_telemetry_processor = kinesisanalytics.CfnApplication(
            self, "EventDrivenTelemetryProcessor",
            application_name=f"{construct_id}-event-driven-telemetry-processor",
            runtime_environment="FLINK-1_18",
            service_execution_role=self.flink_role.role_arn,
            application_configuration=app_config,
            application_description="Event-driven telemetry processor with MSK integration"
        )
        
        # Add CloudWatch logging to event-driven telemetry processor
        kinesisanalytics.CfnApplicationCloudWatchLoggingOption(
            self, "EventDrivenTelemetryLogging",
            application_name=self.event_driven_telemetry_processor.ref,
            cloud_watch_logging_option=kinesisanalytics.CfnApplicationCloudWatchLoggingOption.CloudWatchLoggingOptionProperty(
                log_stream_arn=f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.event_driven_telemetry_log_group.log_group_name}:log-stream:kinesis-analytics-log-stream"
            )
        )
        
        # 1b. OEM Telemetry Processor (transforms OEM data to CMS format)
        oem_app_config = create_flink_app_config(
            "OEMTelemetryProcessor",
            {
                "group.id": "oem-telemetry-processor",
                "KAFKA_TOPIC": "cms-telemetry-oem",
                "S3_MANIFEST_BUCKET": f"{construct_id.replace('-flink', '')}-oem-manifests"
            }
        )
        
        self.oem_telemetry_processor = kinesisanalytics.CfnApplication(
            self, "OEMTelemetryProcessor",
            application_name=f"{construct_id}-oem-telemetry-processor",
            runtime_environment="FLINK-1_18",
            service_execution_role=self.flink_role.role_arn,
            application_configuration=oem_app_config,
            application_description="OEM telemetry transformer (Ford/GM/Stellantis to CMS format)"
        )
        
        # Add CloudWatch logging to OEM telemetry processor
        kinesisanalytics.CfnApplicationCloudWatchLoggingOption(
            self, "OEMTelemetryLogging",
            application_name=self.oem_telemetry_processor.ref,
            cloud_watch_logging_option=kinesisanalytics.CfnApplicationCloudWatchLoggingOption.CloudWatchLoggingOptionProperty(
                log_stream_arn=f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.oem_telemetry_log_group.log_group_name}:log-stream:kinesis-analytics-log-stream"
            )
        )
        
        # 1c. FW Telemetry Processor (decodes FleetWise protobuf → cms-telemetry-preprocessed)
        self.fw_telemetry_processor = kinesisanalytics.CfnApplication(
            self, "FWTelemetryProcessor",
            application_name=f"{construct_id}-fw-telemetry-processor",
            runtime_environment="FLINK-1_18",
            service_execution_role=self.flink_role.role_arn,
            application_configuration=create_flink_app_config(
                "FWTelemetryProcessor",
                {
                    "group.id": "fw-telemetry-processor",
                    "input.topic": "fw-telemetry-raw",
                    "output.topic": "cms-telemetry-preprocessed",
                    "VEHICLES_TABLE": storage_tables['vehicles'].table_name,
                    "DECODER_TABLE": f"{construct_id.replace('-flink', '')}-decoder-manifest",
                }
            ),
            application_description="FleetWise protobuf decoder (FWE binary → CMS JSON)"
        )

        kinesisanalytics.CfnApplicationCloudWatchLoggingOption(
            self, "FWTelemetryLogging",
            application_name=self.fw_telemetry_processor.ref,
            cloud_watch_logging_option=kinesisanalytics.CfnApplicationCloudWatchLoggingOption.CloudWatchLoggingOptionProperty(
                log_stream_arn=f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.fw_telemetry_log_group.log_group_name}:log-stream:kinesis-analytics-log-stream"
            )
        )

        # 1d. Simulator Preprocessor (decodes gzip+base64 from simulator → cms-telemetry-preprocessed)
        self.simulator_preprocessor = kinesisanalytics.CfnApplication(
            self, "SimulatorPreprocessor",
            application_name=f"{construct_id}-simulator-preprocessor",
            runtime_environment="FLINK-1_18",
            service_execution_role=self.flink_role.role_arn,
            application_configuration=create_flink_app_config(
                "SimulatorPreprocessor",
                {
                    "group.id": "simulator-preprocessor",
                }
            ),
            application_description="Simulator preprocessor (gzip+base64 → CMS JSON)"
        )

        kinesisanalytics.CfnApplicationCloudWatchLoggingOption(
            self, "SimulatorPreprocessorLogging",
            application_name=self.simulator_preprocessor.ref,
            cloud_watch_logging_option=kinesisanalytics.CfnApplicationCloudWatchLoggingOption.CloudWatchLoggingOptionProperty(
                log_stream_arn=f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.simulator_preprocessor_log_group.log_group_name}:log-stream:kinesis-analytics-log-stream"
            )
        )

        # 2. Telemetry Enhanced Final Processor (matches cms-telemetry-enhanced-final)
        self.telemetry_enhanced_processor = kinesisanalytics.CfnApplication(
            self, "TelemetryEnhancedProcessor",
            application_name=f"{construct_id}-telemetry-enhanced-final",
            application_description="Enhanced telemetry processor with advanced analytics",
            runtime_environment="FLINK-1_18",
            service_execution_role=self.flink_role.role_arn,
            application_configuration=create_flink_app_config(
                "TelemetryDataProcessor",
                {
                    "group.id": "cms-enhanced-telemetry-processor-consumer",
                    "TABLE_NAME": storage_tables['telemetry'].table_name,
                    "TELEMETRY_TABLE_NAME": storage_tables['telemetry'].table_name,
                    "S3_DATALAKE_BUCKET": storage_tables.get('datalake_bucket_name', f"{construct_id.replace('-flink', '-storage')}-datalake")
                }
            )
        )
        
        # Add CloudWatch logging to telemetry enhanced processor
        kinesisanalytics.CfnApplicationCloudWatchLoggingOption(
            self, "TelemetryEnhancedLogging",
            application_name=self.telemetry_enhanced_processor.ref,
            cloud_watch_logging_option=kinesisanalytics.CfnApplicationCloudWatchLoggingOption.CloudWatchLoggingOptionProperty(
                log_stream_arn=f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.telemetry_enhanced_log_group.log_group_name}:log-stream:kinesis-analytics-log-stream"
            )
        )
        
        # 3. Trip Processor (matches cms-trip-processor)
        self.trip_processor = kinesisanalytics.CfnApplication(
            self, "TripProcessor",
            application_name=f"{construct_id}-trip-processor", 
            application_description="Trip data processor with DynamoDB integration",
            runtime_environment="FLINK-1_18",
            service_execution_role=self.flink_role.role_arn,
            application_configuration=create_flink_app_config(
                "TripProcessor",
                {
                    "group.id": "trip-processor-consumer-fixed",
                    "TRIPS_TABLE_NAME": storage_tables['trips'].table_name
                }
            )
        )
        
        # Add CloudWatch logging to trip processor
        kinesisanalytics.CfnApplicationCloudWatchLoggingOption(
            self, "TripProcessorLogging",
            application_name=self.trip_processor.ref,
            cloud_watch_logging_option=kinesisanalytics.CfnApplicationCloudWatchLoggingOption.CloudWatchLoggingOptionProperty(
                log_stream_arn=f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.trip_log_group.log_group_name}:log-stream:kinesis-analytics-log-stream"
            )
        )
        
        # 4. Safety Processor (matches cms-safety-processor)
        self.safety_processor = kinesisanalytics.CfnApplication(
            self, "SafetyProcessor",
            application_name=f"{construct_id}-safety-processor",
            application_description="Safety events processor with DynamoDB integration", 
            runtime_environment="FLINK-1_18",
            service_execution_role=self.flink_role.role_arn,
            application_configuration=create_flink_app_config(
                "SafetyProcessor",
                {
                    "group.id": "safety-processor-consumer",
                    "SAFETY_EVENTS_TABLE_NAME": storage_tables['safety_events'].table_name
                }
            )
        )
        
        # Add CloudWatch logging to safety processor
        kinesisanalytics.CfnApplicationCloudWatchLoggingOption(
            self, "SafetyProcessorLogging",
            application_name=self.safety_processor.ref,
            cloud_watch_logging_option=kinesisanalytics.CfnApplicationCloudWatchLoggingOption.CloudWatchLoggingOptionProperty(
                log_stream_arn=f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.safety_log_group.log_group_name}:log-stream:kinesis-analytics-log-stream"
            )
        )
        
        # 5. Maintenance Processor (matches cms-maintenance-processor-template)
        self.maintenance_processor = kinesisanalytics.CfnApplication(
            self, "MaintenanceProcessor",
            application_name=f"{construct_id}-maintenance-processor",
            application_description="Maintenance processor with UniversalProcessor entry point",
            runtime_environment="FLINK-1_18",
            service_execution_role=self.flink_role.role_arn,
            application_configuration=create_flink_app_config(
                "MaintenanceProcessor",
                {
                    "group.id": "cms-maintenance-processor-template-consumer",
                    "MAINTENANCE_TABLE_NAME": storage_tables['maintenance_events'].table_name
                }
            )
        )
        
        # Add CloudWatch logging to maintenance processor
        kinesisanalytics.CfnApplicationCloudWatchLoggingOption(
            self, "MaintenanceProcessorLogging",
            application_name=self.maintenance_processor.ref,
            cloud_watch_logging_option=kinesisanalytics.CfnApplicationCloudWatchLoggingOption.CloudWatchLoggingOptionProperty(
                log_stream_arn=f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.maintenance_log_group.log_group_name}:log-stream:kinesis-analytics-log-stream"
            )
        )
        
        # Store applications for easy access (removed auto-start custom resources)
        # Note: Applications will be started manually after JAR upload
        self.applications = {
            'simulator_preprocessor': self.simulator_preprocessor,
            'event_driven_telemetry_processor': self.event_driven_telemetry_processor,
            'telemetry_enhanced_processor': self.telemetry_enhanced_processor,
            'trip_processor': self.trip_processor, 
            'safety_processor': self.safety_processor,
            'maintenance_processor': self.maintenance_processor,
            'fw_telemetry_processor': self.fw_telemetry_processor,
        }
        
        # Outputs
        CfnOutput(
            self, "FlinkJarBucketOutput",
            value=self.jar_bucket.bucket_name,
            export_name=f"{construct_id}-jar-bucket"
        )
        
        CfnOutput(
            self, "FlinkJarS3Key",
            value=jar_s3_key,
            export_name=f"{construct_id}-jar-s3-key"
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
