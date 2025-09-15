#!/usr/bin/env python3
"""
Flink Construct for CMS Trip Processing
Adapted from fleet_telemetry_final/cdk/stacks/flink_stack_simple.py
"""

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_kinesisanalyticsv2 as kinesisanalytics,
    aws_iam as iam,
    aws_s3 as s3,
    aws_s3_deployment as s3_deployment,
    aws_s3_assets as s3_assets,
    aws_logs as logs,
    aws_ec2 as ec2,
    CfnOutput
)
from constructs import Construct
import aws_cdk.aws_kinesisanalytics_flink_alpha as flink  # L2 Construct for Managed Apache Flink
import json

class FlinkConstruct(Construct):
    """Flink application for processing telemetry and generating trips"""
    
    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.Vpc, msk_cluster_arn: str, 
                 trips_table_name: str, safety_events_table_name: str = None, 
                 maintenance_alerts_table_name: str = None, sasl_secret_name: str = None, 
                 msk_security_group_id: str = None, bootstrap_servers: str = None, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        self.vpc = vpc
        self.msk_cluster_arn = msk_cluster_arn
        self.trips_table_name = trips_table_name
        self.safety_events_table_name = safety_events_table_name or "cms-safety-events"
        self.maintenance_alerts_table_name = maintenance_alerts_table_name or "cms-maintenance-alerts"
        self.sasl_secret_name = sasl_secret_name
        self.msk_security_group_id = msk_security_group_id
        self.bootstrap_servers = bootstrap_servers or "placeholder"
        
        # Create IAM role for Flink
        self._create_flink_role()
        
        # Create Flink application with proper dependency management
        self._create_flink_application()
        
        # Create outputs
        self._create_outputs()
    
    def _create_flink_role(self):
        """Create IAM role for Flink application"""
        
        # Get account and region from the stack
        stack = Stack.of(self)
        account = stack.account
        region = stack.region
        
        self.flink_role = iam.Role(
            self, "FlinkExecutionRole",
            assumed_by=iam.ServicePrincipal("kinesisanalytics.amazonaws.com"),
            description="Execution role for CMS Flink trip processing application",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole")
            ]
        )
        
        # Add permissions for MSK access
        self.flink_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "kafka:DescribeCluster",
                    "kafka:GetBootstrapBrokers",
                    "kafka:DescribeClusterV2"
                ],
                resources=[self.msk_cluster_arn]
            )
        )
        
        self.flink_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "kafka-cluster:Connect",
                    "kafka-cluster:AlterCluster",
                    "kafka-cluster:DescribeCluster"
                ],
                resources=[self.msk_cluster_arn]
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
                resources=[f"{self.msk_cluster_arn}/topic/*"]
            )
        )
        
        # Add permissions for DynamoDB access
        self.flink_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:GetItem",
                    "dynamodb:Query",
                    "dynamodb:Scan"
                ],
                resources=[
                    f"arn:aws:dynamodb:{region}:{account}:table/cms-*-trips",
                    f"arn:aws:dynamodb:{region}:{account}:table/cms-*-safety-events",
                    f"arn:aws:dynamodb:{region}:{account}:table/cms-*-maintenance-alerts",
                    f"arn:aws:dynamodb:{region}:{account}:table/cms-telemetry-*"
                ]
            )
        )
        
        # Add permissions for DynamoDB access
        self.flink_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:PutItem",
                    "dynamodb:GetItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:Query",
                    "dynamodb:Scan"
                ],
                resources=["*"]  # Tighten this in production
            )
        )
        
        # Add permissions for CloudWatch logs
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
                resources=["*"]
            )
        )
        
        # Add permissions for VPC access
        self.flink_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "ec2:CreateNetworkInterface",
                    "ec2:CreateNetworkInterfacePermission",
                    "ec2:DescribeNetworkInterfaces",
                    "ec2:DeleteNetworkInterface",
                    "ec2:AttachNetworkInterface",
                    "ec2:DetachNetworkInterface",
                    "ec2:DescribeSubnets",
                    "ec2:DescribeVpcs",
                    "ec2:DescribeSecurityGroups",
                    "ec2:DescribeDhcpOptions"
                ],
                resources=["*"]
            )
        )
        
        # Add permissions for CloudWatch logs
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
                    f"arn:aws:logs:{region}:{account}:log-group:/aws/kinesis-analytics/*",
                    f"arn:aws:logs:{region}:{account}:log-group:/aws/kinesis-analytics/*:*"
                ]
            )
        )
        
        # Add VPC managed policy for additional VPC permissions
        self.flink_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole")
        )
        
        # Add Kinesis Analytics managed policy
        self.flink_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonKinesisAnalyticsFullAccess")
        )
        
        # Add permissions for Secrets Manager (SASL credentials)
        if self.sasl_secret_name:
            self.flink_role.add_to_policy(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "secretsmanager:GetSecretValue",
                        "secretsmanager:DescribeSecret"
                    ],
                    resources=[f"arn:aws:secretsmanager:{region}:{account}:secret:{self.sasl_secret_name}*"]
                )
            )
            
            # Add DynamoDB permissions (Critical)
            self.flink_role.add_to_policy(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "dynamodb:PutItem",
                        "dynamodb:GetItem", 
                        "dynamodb:UpdateItem",
                        "dynamodb:DeleteItem"
                    ],
                    resources=[
                        f"arn:aws:dynamodb:{region}:{account}:table/cms-*-trips",
                        f"arn:aws:dynamodb:{region}:{account}:table/cms-*-safety-events",
                        f"arn:aws:dynamodb:{region}:{account}:table/cms-*-maintenance-alerts",
                        f"arn:aws:dynamodb:{region}:{account}:table/cms-telemetry-*"
                    ]
                )
            )
            
            # Add KMS permissions for decrypting secrets
            self.flink_role.add_to_policy(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "kms:Decrypt",
                        "kms:DescribeKey"
                    ],
                    resources=["*"]  # KMS key ARN not available here
                )
            )
    
    def _create_flink_application(self):
        """Create Flink application using real telemetry processor JAR"""
        
        # Use the real Flink JAR from the flink directory
        import os
        jar_path = os.path.join(os.path.dirname(__file__), "..", "..", "flink", "target", "cms-telemetry-processor-1.0.0.jar")
        
        # Check if JAR exists, if not use fallback
        if not os.path.exists(jar_path):
            jar_path = os.path.join(os.path.dirname(__file__), "..", "..", "flink", "target", "telemetry-processor-1.0.0.jar")
        if not os.path.exists(jar_path):
            print(f"⚠️  Real JAR not found at {jar_path}, using dummy JAR")
            jar_path = os.path.join(os.path.dirname(__file__), "flink-jar", "dummy-flink-app.jar")
        else:
            print(f"✅ Using real JAR at {jar_path}")
        
        self.flink_asset = s3_assets.Asset(
            self, "FlinkAsset",
            path=jar_path
        )
        
        # Grant Flink role access to the asset (like first AWS sample)
        self.flink_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "s3:GetObject",
                "s3:GetObjectVersion"
            ],
            resources=[
                f"arn:aws:s3:::{self.flink_asset.s3_bucket_name}/{self.flink_asset.s3_object_key}",
                f"arn:aws:s3:::{self.flink_asset.s3_bucket_name}/*.jar"  # Allow access to any JAR file
            ]
        ))
        
        # Create Flink application exactly like first AWS sample
        import time
        unique_suffix = str(int(time.time()))[-6:]  # Last 6 digits of timestamp
        
        # Create CloudWatch log group for Flink
        self.log_group = logs.LogGroup(
            self, "FlinkLogGroup",
            log_group_name=f"/aws/kinesis-analytics/cms-telemetry-processor-{unique_suffix}",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY
        )
        
        # Build application configuration with VPC and logging
        # Use available subnets (private if available, otherwise public)
        available_subnets = self.vpc.private_subnets if self.vpc.private_subnets else self.vpc.public_subnets
        
        app_config = {
            "vpcConfigurations": [
                {
                    "securityGroupIds": [self.msk_security_group_id] if self.msk_security_group_id else [],
                    "subnetIds": [subnet.subnet_id for subnet in available_subnets]
                }
            ],
            "applicationCodeConfiguration": {
                "codeContent": {
                    "s3ContentLocation": {
                        "bucketArn": f"arn:aws:s3:::{self.flink_asset.s3_bucket_name}",
                        "fileKey": self.flink_asset.s3_object_key
                    }
                },
                "codeContentType": "ZIPFILE"
            },
            "flinkApplicationConfiguration": {
                "checkpointConfiguration": {
                    "configurationType": "CUSTOM",
                    "checkpointingEnabled": True,
                    "checkpointInterval": 60000
                },
                "monitoringConfiguration": {
                    "configurationType": "CUSTOM",
                    "logLevel": "DEBUG",
                    "metricsLevel": "TASK"
                }
            },
            "applicationSnapshotConfiguration": {
                "snapshotsEnabled": False  # Prevents snapshot-related errors
            },
            "monitoringConfiguration": {
                "configurationType": "CUSTOM",
                "logLevel": "INFO",
                "metricsLevel": "APPLICATION"
            },
            "environmentProperties": {
                "propertyGroups": [
                    {
                        "propertyGroupId": "consumer.config.0",
                        "propertyMap": {
                            "bootstrap.servers": self.bootstrap_servers,
                            "sasl.mechanism": "SCRAM-SHA-512",
                            "security.protocol": "SASL_SSL",
                            "sasl.jaas.config": f'org.apache.kafka.common.security.scram.ScramLoginModule required username="iot-user" password="${{get_secret(\"{self.sasl_secret_name}\", \"SecretString\", \"password\")}}";',
                            "group.id": "flink-telemetry-consumer",
                            "auto.offset.reset": "latest",
                            "TRIPS_TABLE_NAME": self.trips_table_name,
                            "SAFETY_EVENTS_TABLE_NAME": self.safety_events_table_name,
                            "MAINTENANCE_ALERTS_TABLE_NAME": self.maintenance_alerts_table_name
                        }
                    }
                ]
            }
        }
        
        self.flink_application = kinesisanalytics.CfnApplication(
            self, "FlinkApplication",
            application_configuration=app_config,
            application_name=f"cms-telemetry-processor-{unique_suffix}",
            runtime_environment="FLINK-1_18",
            service_execution_role=self.flink_role.role_arn
        )
        
        # Add dependencies exactly like first AWS sample
        self.flink_application.node.add_dependency(self.flink_asset)
        self.flink_application.node.add_dependency(self.flink_role)
    
    def _create_outputs(self):
        """Create CloudFormation outputs"""
        
        CfnOutput(
            self, "FlinkRoleArn",
            value=self.flink_role.role_arn,
            description="ARN of the Flink execution role"
        )
        
        CfnOutput(
            self, "FlinkLogGroupName",
            value=self.log_group.log_group_name,
            description="CloudWatch log group for Flink application"
        )
        
        CfnOutput(
            self, "FlinkAppName",
            value=self.flink_application.application_name,
            description="Name of the Flink application"
        )
        
        CfnOutput(
            self, "FlinkS3Bucket",
            value=self.flink_asset.s3_bucket_name,
            description="S3 bucket containing Flink JAR"
        )
