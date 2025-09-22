from aws_cdk import (
    Stack,
    aws_kinesisanalytics as kinesisanalytics,
    aws_iam as iam,
    aws_s3 as s3,
    aws_logs as logs,
    Fn
)
from constructs import Construct

class SimpleFlinkStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Get MSK VPC configuration from exports
        msk_vpc_id = Fn.import_value("cms-dev-msk-vpc-id")
        msk_subnet_ids = Fn.split(",", Fn.import_value("cms-dev-msk-private-subnet-ids"))
        msk_sg_id = Fn.import_value("cms-dev-msk-security-group-id")
        
        # Create S3 bucket for JAR files
        jar_bucket = s3.Bucket(
            self, "SimpleFlinkJarBucket",
            bucket_name=f"{construct_id}-jar-bucket-{self.account}",
            versioned=True
        )
        
        # Create IAM role for Flink
        flink_role = iam.Role(
            self, "SimpleFlinkRole",
            assumed_by=iam.ServicePrincipal("kinesisanalytics.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonKinesisAnalyticsFullAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonVPCFullAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name("SecretsManagerReadWrite")
            ]
        )
        
        # Grant S3 access
        jar_bucket.grant_read(flink_role)
        
        # Create CloudWatch log group
        log_group = logs.LogGroup(
            self, "SimpleFlinkLogGroup",
            log_group_name=f"/aws/kinesis-analytics/{construct_id}-simple-app"
        )
        
        # Create log stream
        log_stream = logs.LogStream(
            self, "SimpleFlinkLogStream",
            log_group=log_group,
            log_stream_name="kinesis-analytics-log-stream"
        )
        
        # Create Flink application with VPC configuration
        flink_app = kinesisanalytics.CfnApplicationV2(
            self, "SimpleFlinkApp",
            application_name=f"{construct_id}-simple-app",
            runtime_environment="FLINK-1_18",
            service_execution_role=flink_role.role_arn,
            application_description="Simple Flink app to test VPC configuration",
            application_configuration={
                "ApplicationCodeConfiguration": {
                    "CodeContent": {
                        "S3ContentLocation": {
                            "BucketARN": jar_bucket.bucket_arn,
                            "FileKey": "jars/cms-telemetry-processor-1.0.0.jar"
                        }
                    },
                    "CodeContentType": "ZIPFILE"
                },
                "VpcConfigurations": [{
                    "SubnetIds": [
                        Fn.select(0, msk_subnet_ids),
                        Fn.select(1, msk_subnet_ids)
                    ],
                    "SecurityGroupIds": [msk_sg_id]
                }]
            }
        )
        
        # Add CloudWatch logging
        kinesisanalytics.CfnApplicationCloudWatchLoggingOptionV2(
            self, "SimpleFlinkLogging",
            application_name=flink_app.application_name,
            cloud_watch_logging_option=kinesisanalytics.CfnApplicationCloudWatchLoggingOptionV2.CloudWatchLoggingOptionProperty(
                log_stream_arn=f"arn:aws:logs:{self.region}:{self.account}:log-group:{log_group.log_group_name}:log-stream:{log_stream.log_stream_name}"
            )
        )
        
        # Outputs
        self.jar_bucket_name = jar_bucket.bucket_name
        self.app_name = flink_app.application_name
