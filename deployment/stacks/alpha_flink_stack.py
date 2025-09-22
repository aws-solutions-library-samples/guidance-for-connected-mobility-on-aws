from aws_cdk import (
    Stack,
    aws_iam as iam,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_ec2 as ec2,
    CfnOutput,
    Fn
)
import aws_cdk.aws_kinesisanalytics_flink_alpha as flink
from constructs import Construct

class AlphaFlinkStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Create S3 bucket for JAR files
        self.jar_bucket = s3.Bucket(
            self, "FlinkJarBucket",
            versioned=True
        )
        
        # Deploy JAR file to S3 (use existing JAR from flink-processor)
        jar_deployment = s3deploy.BucketDeployment(
            self, "FlinkJarDeployment",
            sources=[s3deploy.Source.asset("../modules/flink/target")],
            destination_bucket=self.jar_bucket,
            destination_key_prefix="jars/"
        )
        
        # Create IAM role for Flink applications
        self.flink_role = iam.Role(
            self, "FlinkExecutionRole",
            assumed_by=iam.ServicePrincipal("kinesisanalytics.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonKinesisAnalyticsFullAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonVPCFullAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name("SecretsManagerReadWrite")
            ]
        )
        
        # Grant S3 access
        self.jar_bucket.grant_read(self.flink_role)
        
        # Get MSK VPC and security group from stack exports
        vpc_id = Fn.import_value("cms-dev-msk-vpc-id")
        security_group_id = Fn.import_value("cms-dev-msk-security-group-id")
        subnet_ids = Fn.split(",", Fn.import_value("cms-dev-msk-private-subnet-ids"))
        
        # Create VPC reference using the imported values (2 subnets in 2 AZs)
        vpc = ec2.Vpc.from_vpc_attributes(self, "MSKVpc",
            vpc_id=vpc_id,
            availability_zones=["us-east-1a", "us-east-1b"],
            private_subnet_ids=subnet_ids,
            private_subnet_route_table_ids=["dummy1", "dummy2"]  # Required but not used
        )
        security_group = ec2.SecurityGroup.from_security_group_id(self, "MSKSecurityGroup", security_group_id)
        
        # Create Flink applications with MSK VPC configuration
        self.event_driven_telemetry_processor = flink.Application(
            self, "EventDrivenTelemetryProcessor",
            application_name=f"{construct_id}-event-driven-telemetry-processor",
            code=flink.ApplicationCode.from_bucket(self.jar_bucket, "jars/cms-telemetry-processor-1.0.0.jar"),
            runtime=flink.Runtime.FLINK_1_18,
            vpc=vpc,
            security_groups=[security_group],
            role=self.flink_role
        )
        self.event_driven_telemetry_processor.node.add_dependency(jar_deployment)

        self.telemetry_enhanced_processor = flink.Application(
            self, "TelemetryEnhancedProcessor", 
            application_name=f"{construct_id}-telemetry-enhanced-final",
            code=flink.ApplicationCode.from_bucket(self.jar_bucket, "jars/cms-telemetry-processor-1.0.0.jar"),
            runtime=flink.Runtime.FLINK_1_18,
            vpc=vpc,
            security_groups=[security_group],
            role=self.flink_role
        )
        self.telemetry_enhanced_processor.node.add_dependency(jar_deployment)

        self.trip_processor = flink.Application(
            self, "TripProcessor",
            application_name=f"{construct_id}-trip-processor",
            code=flink.ApplicationCode.from_bucket(self.jar_bucket, "jars/cms-telemetry-processor-1.0.0.jar"),
            runtime=flink.Runtime.FLINK_1_18,
            vpc=vpc,
            security_groups=[security_group],
            role=self.flink_role
        )
        self.trip_processor.node.add_dependency(jar_deployment)

        self.safety_processor = flink.Application(
            self, "SafetyProcessor",
            application_name=f"{construct_id}-safety-processor", 
            code=flink.ApplicationCode.from_bucket(self.jar_bucket, "jars/cms-telemetry-processor-1.0.0.jar"),
            runtime=flink.Runtime.FLINK_1_18,
            vpc=vpc,
            security_groups=[security_group],
            role=self.flink_role
        )
        self.safety_processor.node.add_dependency(jar_deployment)

        self.maintenance_processor = flink.Application(
            self, "MaintenanceProcessor",
            application_name=f"{construct_id}-maintenance-processor",
            code=flink.ApplicationCode.from_bucket(self.jar_bucket, "jars/cms-telemetry-processor-1.0.0.jar"),
            runtime=flink.Runtime.FLINK_1_18,
            vpc=vpc,
            security_groups=[security_group],
            role=self.flink_role
        )
        self.maintenance_processor.node.add_dependency(jar_deployment)
        
        # Outputs
        CfnOutput(self, "FlinkJarBucketOutput", value=self.jar_bucket.bucket_name)
        CfnOutput(self, "FlinkRoleArn", value=self.flink_role.role_arn)
        CfnOutput(self, "EventDrivenTelemetryProcessorAppName", value=self.event_driven_telemetry_processor.application_name)
        CfnOutput(self, "TelemetryEnhancedProcessorAppName", value=self.telemetry_enhanced_processor.application_name)
        CfnOutput(self, "TripProcessorAppName", value=self.trip_processor.application_name)
        CfnOutput(self, "SafetyProcessorAppName", value=self.safety_processor.application_name)
        CfnOutput(self, "MaintenanceProcessorAppName", value=self.maintenance_processor.application_name)
