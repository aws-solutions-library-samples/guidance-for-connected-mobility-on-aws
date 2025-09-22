from aws_cdk import (
    Stack,
    aws_iam as iam,
    aws_s3 as s3,
    aws_ec2 as ec2,
    Fn
)
import aws_cdk.aws_kinesisanalytics_flink_alpha as flink
from constructs import Construct

class TestFlinkStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Use hardcoded MSK VPC configuration (tokens don't work with from_lookup)
        msk_vpc_id = "vpc-0eb8b1b390253821c"  # MSK VPC ID
        msk_sg_id = "sg-034fb14daaaea4023"   # MSK Security Group ID
        
        # Import VPC and security group
        vpc = ec2.Vpc.from_lookup(self, "MSKVpc", vpc_id=msk_vpc_id)
        security_group = ec2.SecurityGroup.from_security_group_id(self, "MSKSecurityGroup", msk_sg_id)
        
        # Create S3 bucket for JAR files
        jar_bucket = s3.Bucket(
            self, "TestFlinkJarBucket",
            versioned=True
        )
        
        # Create IAM role for Flink
        flink_role = iam.Role(
            self, "TestFlinkRole",
            assumed_by=iam.ServicePrincipal("kinesisanalytics.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonKinesisAnalyticsFullAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonVPCFullAccess")
            ]
        )
        
        # Grant S3 access
        jar_bucket.grant_read(flink_role)
        
        # Use existing JAR file from main Flink stack
        main_flink_bucket = s3.Bucket.from_bucket_name(
            self, "MainFlinkBucket", 
            "cms-dev-flink-flinkjarbucketd8dc3634-5prl383xmtkl"
        )
        
        # Create Flink application with VPC configuration using alpha module
        flink_app = flink.Application(
            self, "TestFlinkApp",
            application_name=f"{construct_id}-alpha-test-app",  # Different name
            code=flink.ApplicationCode.from_bucket(main_flink_bucket, "jars/cms-telemetry-processor-1.0.0.jar"),
            runtime=flink.Runtime.FLINK_1_18,
            vpc=vpc,
            security_groups=[security_group],
            role=flink_role
        )
