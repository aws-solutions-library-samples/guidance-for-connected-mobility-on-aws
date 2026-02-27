"""
FleetWise Integration Stack
IoT rules for FWE telemetry + checkin, VPC endpoints for IoT Data Plane,
CampaignSyncProcessor Flink app, and IAM policies.
"""

from aws_cdk import (
    Stack,
    aws_iot as iot,
    aws_iam as iam,
    aws_ec2 as ec2,
    aws_logs as logs,
    aws_kinesisanalyticsv2 as kinesisanalytics,
    aws_lambda as lambda_,
    CustomResource,
    CfnOutput,
    Duration,
    Fn,
    RemovalPolicy,
)
from constructs import Construct


class FleetWiseStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        stage = construct_id.split('-')[1] if '-' in construct_id else 'dev'
        msk_stack = f"cms-{stage}-msk"
        flink_stack = f"cms-{stage}-flink"
        storage_stack = f"cms-{stage}-storage"
        telemetry_stack = f"cms-{stage}-telemetry-integration"

        # ── Imports from other stacks ─────────────────────────────────────
        msk_cluster_arn = Fn.import_value(f"{msk_stack}-cluster-arn")
        msk_vpc_id = Fn.import_value(f"{msk_stack}-vpc-id")
        msk_subnet_ids = Fn.split(",", Fn.import_value(f"{msk_stack}-private-subnet-ids"))
        msk_sg_id = Fn.import_value(f"{msk_stack}-security-group-id")
        msk_secret_arn = Fn.import_value(f"{msk_stack}-iot-user-secret-arn")
        msk_bootstrap = Fn.import_value(f"{msk_stack}-bootstrap-servers")
        flink_role_arn = Fn.import_value(f"{flink_stack}-flink-role-arn")
        flink_jar_bucket = Fn.import_value(f"{flink_stack}-jar-bucket-name")
        flink_jar_key = Fn.import_value(f"{flink_stack}-jar-s3-key")
        vpc_destination_arn = Fn.import_value(f"{telemetry_stack}-vpc-destination-arn")
        iot_role_arn = Fn.import_value(f"{telemetry_stack}-iot-role-arn")

        vpc = ec2.Vpc.from_lookup(self, "Vpc", vpc_id=Fn.import_value(f"{msk_stack}-vpc-id").to_string()) if False else None
        # We use Fn references instead of lookup for cross-stack compatibility

        # ── Discover IoT ATS endpoint via Lambda ──────────────────────────
        iot_endpoint_role = iam.Role(
            self, "IoTEndpointRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ],
            inline_policies={
                "IoTDescribe": iam.PolicyDocument(statements=[
                    iam.PolicyStatement(actions=["iot:DescribeEndpoint"], resources=["*"])
                ])
            }
        )

        iot_endpoint_fn = lambda_.Function(
            self, "IoTEndpointFn",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="index.handler",
            role=iot_endpoint_role,
            timeout=Duration.seconds(30),
            code=lambda_.Code.from_inline("""
import boto3, cfnresponse
def handler(event, context):
    try:
        endpoint = boto3.client('iot').describe_endpoint(endpointType='iot:Data-ATS')['endpointAddress']
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {'Endpoint': endpoint})
    except Exception as e:
        cfnresponse.send(event, context, cfnresponse.FAILED, {'Error': str(e)})
""")
        )

        iot_endpoint_cr = CustomResource(self, "IoTEndpointCR",
            service_token=iot_endpoint_fn.function_arn)
        iot_ats_endpoint = iot_endpoint_cr.get_att_string("Endpoint")

        # ── VPC Endpoint: IoT Data Plane ──────────────────────────────────
        # Needed for Flink (in VPC) to publish to IoT Core
        iot_data_vpce = ec2.CfnVPCEndpoint(
            self, "IoTDataVpcEndpoint",
            vpc_id=Fn.import_value(f"{msk_stack}-vpc-id"),
            service_name=f"com.amazonaws.{self.region}.iot.data",
            vpc_endpoint_type="Interface",
            subnet_ids=[Fn.select(0, msk_subnet_ids)],  # Use first subnet (AZ-a)
            security_group_ids=[msk_sg_id],
            private_dns_enabled=False,
        )

        # ── VPC Endpoint: S3 Gateway ──────────────────────────────────────
        # Needed for Flink to read campaign configs from S3
        # Note: Gateway endpoints need route table IDs. We create via CfnVPCEndpoint.
        s3_vpce = ec2.CfnVPCEndpoint(
            self, "S3GatewayEndpoint",
            vpc_id=Fn.import_value(f"{msk_stack}-vpc-id"),
            service_name=f"com.amazonaws.{self.region}.s3",
            vpc_endpoint_type="Gateway",
        )

        # ── Security Group: allow HTTPS from Flink to VPC endpoints ───────
        sg_https_rule = ec2.CfnSecurityGroupIngress(
            self, "FlinkToVpceHttps",
            group_id=msk_sg_id,
            ip_protocol="tcp",
            from_port=443,
            to_port=443,
            source_security_group_id=msk_sg_id,
            description="Allow HTTPS from Flink to VPC endpoints (IoT Data Plane)"
        )

        # ── IoT Rule: FWE Telemetry → MSK fw-telemetry-raw ───────────────
        fw_telemetry_rule = iot.CfnTopicRule(
            self, "FWTelemetryRule",
            rule_name=f"fw_{stage}_iot_msk_rule",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                aws_iot_sql_version="2016-03-23",
                sql="SELECT encode(*, 'base64') AS data, topic(4) AS vehicleId, timestamp() AS ts FROM 'cms/fleetwise/vehicles/+/signals'",
                description="Route FWE protobuf telemetry to MSK fw-telemetry-raw",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        kafka=iot.CfnTopicRule.KafkaActionProperty(
                            destination_arn=vpc_destination_arn,
                            topic="fw-telemetry-raw",
                            client_properties={
                                "bootstrap.servers": msk_bootstrap,
                                "sasl.mechanism": "SCRAM-SHA-512",
                                "security.protocol": "SASL_SSL",
                                "sasl.scram.username": f"${{get_secret(\"{msk_secret_arn}\", \"SecretString\", \"username\", \"{iot_role_arn}\")}}",
                                "sasl.scram.password": f"${{get_secret(\"{msk_secret_arn}\", \"SecretString\", \"password\", \"{iot_role_arn}\")}}"
                            }
                        )
                    )
                ]
            )
        )

        # ── IoT Rule: FWE Checkin → MSK fw-checkin ────────────────────────
        fw_checkin_rule = iot.CfnTopicRule(
            self, "FWCheckinRule",
            rule_name=f"fw_{stage}_checkin_rule",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                aws_iot_sql_version="2016-03-23",
                sql="SELECT encode(*, 'base64') AS data, clientid() AS thingName, timestamp() AS ts FROM 'cms/fleetwise/vehicles/+/checkins'",
                description="Route FWE checkin protobuf to MSK fw-checkin",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        kafka=iot.CfnTopicRule.KafkaActionProperty(
                            destination_arn=vpc_destination_arn,
                            topic="fw-checkin",
                            client_properties={
                                "bootstrap.servers": msk_bootstrap,
                                "sasl.mechanism": "SCRAM-SHA-512",
                                "security.protocol": "SASL_SSL",
                                "sasl.scram.username": f"${{get_secret(\"{msk_secret_arn}\", \"SecretString\", \"username\", \"{iot_role_arn}\")}}",
                                "sasl.scram.password": f"${{get_secret(\"{msk_secret_arn}\", \"SecretString\", \"password\", \"{iot_role_arn}\")}}"
                            }
                        )
                    )
                ]
            )
        )

        # ── IAM: Flink CampaignSync permissions ──────────────────────────
        campaign_sync_policy = iam.CfnManagedPolicy(
            self, "FlinkCampaignSyncPolicy",
            managed_policy_name=f"FlinkCampaignSyncAccess-{stage}",
            roles=[Fn.select(1, Fn.split("/", flink_role_arn))],  # Extract role name from ARN
            policy_document={
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "iot:Publish",
                        "Resource": [
                            f"arn:aws:iot:{self.region}:{self.account}:topic/cms/fleetwise/vehicles/*/decoder_manifests",
                            f"arn:aws:iot:{self.region}:{self.account}:topic/cms/fleetwise/vehicles/*/collection_schemes"
                        ]
                    },
                    {
                        "Effect": "Allow",
                        "Action": ["s3:GetObject"],
                        "Resource": f"arn:aws:s3:::cms-{stage}-transform-manifests-{self.account}/campaigns/*"
                    },
                    {
                        "Effect": "Allow",
                        "Action": ["dynamodb:Query", "dynamodb:GetItem", "dynamodb:Scan"],
                        "Resource": [
                            f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-decoder-manifest",
                            f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-decoder-manifest/index/*",
                            f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-signal-catalog",
                            f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-storage-vehicles",
                            f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-storage-vehicles/index/*"
                        ]
                    }
                ]
            }
        )

        # ── Flink App: CampaignSyncProcessor ─────────────────────────────
        sync_log_group = logs.LogGroup(
            self, "CampaignSyncLogGroup",
            log_group_name=f"/aws/kinesis-analytics/cms-{stage}-flink-campaign-sync-processor",
            removal_policy=RemovalPolicy.DESTROY,
            retention=logs.RetentionDays.TWO_WEEKS,
        )
        sync_log_stream = logs.LogStream(
            self, "CampaignSyncLogStream",
            log_group=sync_log_group,
            log_stream_name="flink-log-stream"
        )

        # Bootstrap servers for IAM auth (port 9098)
        # The MSK export uses SCRAM port; we need IAM port for Flink
        # Flink apps use IAM auth, so we construct the IAM bootstrap from the SCRAM one
        # by replacing port 9096 with 9098
        iam_bootstrap = Fn.join("", [
            Fn.select(0, Fn.split(":9096", Fn.select(0, Fn.split(",", msk_bootstrap)))),
            ":9098,",
            Fn.select(0, Fn.split(":9096", Fn.select(1, Fn.split(",", msk_bootstrap)))),
            ":9098"
        ])

        campaign_sync_app = kinesisanalytics.CfnApplication(
            self, "CampaignSyncProcessor",
            application_name=f"cms-{stage}-flink-campaign-sync-processor",
            runtime_environment="FLINK-1_18",
            service_execution_role=flink_role_arn,
            application_description="Processes FWE checkins and delivers decoder manifest + collection schemes via IoT Core",
            application_configuration={
                "ApplicationCodeConfiguration": {
                    "CodeContent": {
                        "S3ContentLocation": {
                            "BucketARN": f"arn:aws:s3:::{flink_jar_bucket}",
                            "FileKey": flink_jar_key
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
                    "ParallelismConfiguration": {
                        "ConfigurationType": "CUSTOM",
                        "Parallelism": 1,
                        "ParallelismPerKPU": 1
                    }
                },
                "EnvironmentProperties": {
                    "PropertyGroups": [{
                        "PropertyGroupId": "consumer.config.0",
                        "PropertyMap": {
                            "PROCESSOR_TYPE": "CampaignSyncProcessor",
                            "bootstrap.servers": iam_bootstrap,
                            "security.protocol": "SASL_SSL",
                            "sasl.mechanism": "AWS_MSK_IAM",
                            "sasl.jaas.config": "software.amazon.msk.auth.iam.IAMLoginModule required;",
                            "sasl.client.callback.handler.class": "software.amazon.msk.auth.iam.IAMClientCallbackHandler",
                            "group.id": f"cms-{stage}-campaign-sync-processor",
                            "auto.offset.reset": "latest",
                            "aws.region": self.region,
                            "input.topic": "fw-checkin",
                            "topic.prefix": "cms/fleetwise/",
                            "decoder.table": f"cms-{stage}-decoder-manifest",
                            "campaign.bucket": f"cms-{stage}-transform-manifests-{self.account}",
                            "iot.endpoint": iot_ats_endpoint,
                        }
                    }]
                },
                "VpcConfigurations": [{
                    "SubnetIds": msk_subnet_ids,
                    "SecurityGroupIds": [msk_sg_id]
                }]
            }
        )

        # CloudWatch logging for CampaignSyncProcessor
        kinesisanalytics.CfnApplicationCloudWatchLoggingOption(
            self, "CampaignSyncLogging",
            application_name=campaign_sync_app.ref,
            cloud_watch_logging_option=kinesisanalytics.CfnApplicationCloudWatchLoggingOption.CloudWatchLoggingOptionProperty(
                log_stream_arn=f"arn:aws:logs:{self.region}:{self.account}:log-group:{sync_log_group.log_group_name}:log-stream:flink-log-stream"
            )
        )

        # ── Outputs ───────────────────────────────────────────────────────
        CfnOutput(self, "FWTelemetryRuleName", value=fw_telemetry_rule.ref,
                  export_name=f"{construct_id}-fw-telemetry-rule")
        CfnOutput(self, "FWCheckinRuleName", value=fw_checkin_rule.ref,
                  export_name=f"{construct_id}-fw-checkin-rule")
        CfnOutput(self, "IoTDataVpceId", value=iot_data_vpce.ref,
                  export_name=f"{construct_id}-iot-data-vpce-id")
        CfnOutput(self, "IoTATSEndpoint", value=iot_ats_endpoint,
                  export_name=f"{construct_id}-iot-ats-endpoint")
        CfnOutput(self, "CampaignSyncAppName", value=campaign_sync_app.ref,
                  export_name=f"{construct_id}-campaign-sync-app")
