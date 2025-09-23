"""
Test Flink Stack - For testing without MSK dependency
"""

from aws_cdk import (
    Stack,
    aws_kinesisanalyticsv2 as kinesisanalytics,
    aws_iam as iam,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_dynamodb as dynamodb,
    aws_ec2 as ec2,
    aws_logs as logs,
    aws_lambda as lambda_,
    CfnOutput,
    RemovalPolicy,
    Duration
)
from constructs import Construct
from typing import Dict

class FlinkTestStack(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, 
                 storage_tables: Dict[str, dynamodb.Table], **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Mock MSK values for testing
        mock_bootstrap_servers = "localhost:9092"
        mock_security_group_id = "sg-12345678"
        
        # Get VPC and subnets
        self.vpc = ec2.Vpc.from_lookup(self, "DefaultVPC", is_default=True)
        subnets = self.vpc.public_subnets if len(self.vpc.private_subnets) == 0 else self.vpc.private_subnets
        if len(subnets) < 2:
            subnets = self.vpc.public_subnets + self.vpc.private_subnets
        
        # S3 bucket for Flink JARs
        self.jar_bucket = s3.Bucket(
            self, "FlinkJarBucket",
            bucket_name=f"{construct_id}-flink-jars-{self.account}",
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY
        )
        
        # Build and upload Flink JAR using CDK bundling
        self.jar_deployment = s3deploy.BucketDeployment(
            self, "FlinkJarDeployment",
            sources=[
                s3deploy.Source.asset("../modules/flink", bundling={
                    "image": lambda_.Runtime.JAVA_11.bundling_image,
                    "command": [
                        "bash", "-c", 
                        """
                        echo "Building Flink JAR with Java 11..."
                        mvn clean package -DskipTests -q
                        
                        # Find the built JAR and copy with consistent name
                        JAR_FILE=$(find target -name "*.jar" -not -name "*-sources.jar" | head -1)
                        if [ -f "$JAR_FILE" ]; then
                            cp "$JAR_FILE" /asset-output/cms-universal-processor.jar
                            echo "JAR built successfully: $JAR_FILE -> cms-universal-processor.jar"
                        else
                            echo "ERROR: No JAR file found in target directory"
                            exit 1
                        fi
                        """
                    ],
                    "user": "root"
                })
            ],
            destination_bucket=self.jar_bucket,
            destination_key_prefix="jars/"
        )
        
        # IAM role for Flink applications
        self.flink_role = iam.Role(
            self, "FlinkExecutionRole",
            assumed_by=iam.ServicePrincipal("kinesisanalytics.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/KinesisAnalyticsServiceRole")
            ]
        )
        
        # Add DynamoDB permissions for all tables
        for table in storage_tables.values():
            table.grant_read_write_data(self.flink_role)
        
        # Add S3 permissions for JAR bucket
        self.jar_bucket.grant_read(self.flink_role)
        
        # Test log group
        self.test_log_group = logs.LogGroup(
            self, "TestProcessorLogGroup",
            log_group_name=f"/aws/kinesis-analytics/{construct_id}-test-processor",
            removal_policy=RemovalPolicy.DESTROY
        )
        
        # Test application (just one for testing)
        self.test_processor = kinesisanalytics.CfnApplication(
            self, "TestProcessor",
            application_name=f"{construct_id}-test-processor",
            application_description="Test Flink processor without MSK dependency",
            runtime_environment="FLINK-1_18",
            service_execution_role=self.flink_role.role_arn,
            application_configuration={
                "ApplicationCodeConfiguration": {
                    "CodeContent": {
                        "S3ContentLocation": {
                            "BucketARN": self.jar_bucket.bucket_arn,
                            "FileKey": "jars/cms-universal-processor.jar"
                        }
                    },
                    "CodeContentType": "ZIPFILE"
                },
                "FlinkApplicationConfiguration": {
                    "MonitoringConfiguration": {
                        "ConfigurationType": "CUSTOM",
                        "MetricsLevel": "APPLICATION", 
                        "LogLevel": "INFO"
                    },
                    "ParallelismConfiguration": {
                        "ConfigurationType": "CUSTOM",
                        "Parallelism": 1,
                        "ParallelismPerKPU": 1,
                        "AutoScalingEnabled": True
                    }
                },
                "EnvironmentProperties": {
                    "PropertyGroups": [{
                        "PropertyGroupId": "consumer.config.0",
                        "PropertyMap": {
                            "PROCESSOR_TYPE": "TestProcessor",
                            "bootstrap.servers": mock_bootstrap_servers,
                            "aws.region": self.region
                        }
                    }]
                }
            }
        )
        
        # Outputs
        CfnOutput(
            self, "TestProcessorName",
            value=self.test_processor.application_name,
            export_name=f"{construct_id}-test-processor-name"
        )
