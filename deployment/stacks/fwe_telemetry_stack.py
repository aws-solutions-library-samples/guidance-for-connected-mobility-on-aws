"""FWE Telemetry Stack — CMS-native processing of FleetWise Edge agent data.

This stack does NOT depend on the AWS IoT FleetWise managed service (which
is closed to new customers as of April 30, 2026; AWS recommends this repo
as the replacement). It receives telemetry sent by the open-source AWS
IoT FleetWise Edge (FWE) agent over MQTT to IoT Core, decodes the protobuf
format in CMS's own Flink CampaignSyncProcessor, and stores campaigns +
decoder manifests in DynamoDB.

All services used (IoT Core, KDA / Apache Flink, EC2 VPC, CloudWatch Logs,
IAM) are universally available — including us-west-2.

Key components:
- IoT rules for FWE protobuf telemetry + checkin
- VPC endpoints for IoT Data Plane
- CampaignSyncProcessor Flink app (resolves active campaigns from DynamoDB,
  pushes decoder manifests + collection schemes to FWE agents via MQTT)
- IAM policies

Deployment: opt-in via DEPLOY_FLEETWISE=true env var (preserved for backward
compat; future rename to DEPLOY_FWE_TELEMETRY may follow).
"""

from aws_cdk import (
    Stack,
    aws_iot as iot,
    aws_iam as iam,
    aws_ec2 as ec2,
    aws_logs as logs,
    aws_logs_destinations as logs_destinations,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_kinesisanalyticsv2 as kinesisanalytics,
    aws_sns as sns,
    aws_kms as kms,
    custom_resources as cr,
    CfnOutput,
    Duration,
    Fn,
    RemovalPolicy,
)
from constructs import Construct


class FweTelemetryStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        stage = construct_id.split('-')[1] if '-' in construct_id else 'dev'
        msk_stack = f"cms-{stage}-msk"
        flink_stack = f"cms-{stage}-flink"
        telemetry_stack = f"cms-{stage}-telemetry-integration"

        # ── Imports from other stacks ─────────────────────────────────────
        msk_vpc_id = Fn.import_value(f"{msk_stack}-vpc-id")
        msk_subnet_ids = Fn.split(",", Fn.import_value(f"{msk_stack}-private-subnet-ids"))
        msk_sg_id = Fn.import_value(f"{msk_stack}-security-group-id")
        msk_secret_arn = Fn.import_value(f"{msk_stack}-iot-user-secret-arn")
        msk_route_table_ids = Fn.split(",", Fn.import_value(f"{msk_stack}-private-route-table-ids"))
        msk_bootstrap = Fn.import_value(f"{telemetry_stack}-bootstrap-servers")
        flink_role_arn = Fn.import_value(f"{flink_stack}-flink-role-arn")
        flink_jar_bucket = Fn.import_value(f"{flink_stack}-jar-bucket")
        flink_jar_key = Fn.import_value(f"{flink_stack}-jar-s3-key")
        vpc_destination_arn = Fn.import_value(f"{telemetry_stack}-vpc-destination-arn")
        iot_role_arn = Fn.import_value(f"{telemetry_stack}-iot-role-arn")

        # ── Discover IoT ATS endpoint via SDK call ─────────────────────────
        iot_endpoint_cr = cr.AwsCustomResource(
            self, "IoTEndpointCR",
            on_create=cr.AwsSdkCall(
                service="IoT",
                action="describeEndpoint",
                parameters={"endpointType": "iot:Data-ATS"},
                physical_resource_id=cr.PhysicalResourceId.of("IoTEndpoint"),
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(
                resources=cr.AwsCustomResourcePolicy.ANY_RESOURCE,
            ),
        )
        iot_ats_endpoint = iot_endpoint_cr.get_response_field("endpointAddress")

        # ── VPC Endpoint: IoT Data Plane ──────────────────────────────────
        # Needed for Flink (in VPC) to publish to IoT Core
        iot_data_vpce = ec2.CfnVPCEndpoint(
            self, "IoTDataVpcEndpoint",
            vpc_id=msk_vpc_id,
            service_name=f"com.amazonaws.{self.region}.iot.data",
            vpc_endpoint_type="Interface",
            subnet_ids=[Fn.select(1, msk_subnet_ids)],  # Use 2nd subnet (1st may be in unsupported AZ for iot.data)
            security_group_ids=[msk_sg_id],
            private_dns_enabled=False,
        )

        # S3 and DynamoDB VPC endpoints are created in the MSK stack (single VPC architecture)

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
                sql="SELECT encode(*, 'base64') AS data, topic(4) AS thingName, timestamp() AS ts FROM 'cms/fleetwise/vehicles/+/checkins'",
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
            managed_policy_name=f"FlinkCampaignSyncAccess-{stage}-{self.region}",
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
                        "Resource": f"arn:aws:s3:::cms-{stage}-transform-manifests-{self.region}-{self.account}/campaigns/*"
                    },
                    {
                        "Effect": "Allow",
                        "Action": ["dynamodb:Query", "dynamodb:GetItem", "dynamodb:Scan"],
                        "Resource": [
                            f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-decoder-manifest",
                            f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-decoder-manifest/index/*",
                            f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-campaigns",
                            f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-campaigns/index/*",
                            f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-signal-catalog",
                            f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-storage-vehicles",
                            f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-storage-vehicles/index/*"
                        ]
                    },
                    {
                        "Effect": "Allow",
                        "Action": ["s3:GetObject"],
                        "Resource": [
                            f"arn:aws:s3:::cms-{stage}-transform-manifests-{self.region}-{self.account}/campaigns/*",
                            f"arn:aws:s3:::cms-{stage}-flink-*-*/fwe-config/*"
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
            # Typed ApplicationConfigurationProperty (snake_case). DO NOT pass a raw
            # dict with PascalCase top-level keys — CDK's jsii Python layer SILENTLY
            # DROPS unknown PascalCase keys at this level, leaving
            # ApplicationConfiguration: {} in the synthesized template. The KDA app
            # then creates with empty config — JobManager comes up RUNNING but the
            # job graph crash-loops on Kafka metadata fetch (no VpcConfigurations,
            # no env properties → no MSK connectivity, no input.topic).
            # Confirmed root cause of the 2026-06-15 Tokyo "campaigns Pending sync"
            # bug — see issues/2026-06-16-fleetwise-cdk-pascalcase-app-config-dropped/
            # for the live capture. Same trap documented at flink_stack.py § "Typed
            # ApplicationConfigurationProperty — PascalCase dict keys are silently
            # dropped by CDK's jsii layer. Typed objects fail loudly on unknown
            # kwargs." (spec 2026-06-08-cms-flink-cfn-config-keys-fix § Constraints).
            application_configuration=kinesisanalytics.CfnApplication.ApplicationConfigurationProperty(
                application_code_configuration=kinesisanalytics.CfnApplication.ApplicationCodeConfigurationProperty(
                    code_content=kinesisanalytics.CfnApplication.CodeContentProperty(
                        s3_content_location=kinesisanalytics.CfnApplication.S3ContentLocationProperty(
                            bucket_arn=Fn.join("", ["arn:aws:s3:::", flink_jar_bucket]),
                            file_key=flink_jar_key,
                        ),
                    ),
                    code_content_type="ZIPFILE",
                ),
                flink_application_configuration=kinesisanalytics.CfnApplication.FlinkApplicationConfigurationProperty(
                    checkpoint_configuration=kinesisanalytics.CfnApplication.CheckpointConfigurationProperty(
                        configuration_type="CUSTOM",
                        checkpointing_enabled=True,
                        checkpoint_interval=60000,
                        min_pause_between_checkpoints=5000,
                    ),
                    parallelism_configuration=kinesisanalytics.CfnApplication.ParallelismConfigurationProperty(
                        configuration_type="CUSTOM",
                        parallelism=1,
                        parallelism_per_kpu=1,
                    ),
                ),
                environment_properties=kinesisanalytics.CfnApplication.EnvironmentPropertiesProperty(
                    property_groups=[kinesisanalytics.CfnApplication.PropertyGroupProperty(
                        property_group_id="consumer.config.0",
                        property_map={
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
                            "campaigns.table": f"cms-{stage}-campaigns",
                            "fwe.config.bucket": flink_jar_bucket,
                            "campaign.bucket": f"cms-{stage}-transform-manifests-{self.region}-{self.account}",
                            "iot.endpoint": iot_ats_endpoint,
                            "REDIS_ENDPOINT": Fn.import_value(f"{msk_stack}-redis-endpoint"),
                        },
                    )],
                ),
                vpc_configurations=[kinesisanalytics.CfnApplication.VpcConfigurationProperty(
                    subnet_ids=msk_subnet_ids,
                    security_group_ids=[msk_sg_id],
                )],
            ),
        )

        # CloudWatch logging for CampaignSyncProcessor
        kinesisanalytics.CfnApplicationCloudWatchLoggingOption(
            self, "CampaignSyncLogging",
            application_name=campaign_sync_app.ref,
            cloud_watch_logging_option=kinesisanalytics.CfnApplicationCloudWatchLoggingOption.CloudWatchLoggingOptionProperty(
                log_stream_arn=f"arn:aws:logs:{self.region}:{self.account}:log-group:{sync_log_group.log_group_name}:log-stream:flink-log-stream"
            )
        )

        # ── WS2: DecoderManifestFetchFailed alarm ─────────────────────────
        # Java CampaignSyncProcessor logs the token `DecoderManifestFetchFailed` at
        # ERROR level when getManifestBinary() returns null or throws. A MetricFilter
        # turns that log token into a custom metric so we get a page instead of silent
        # telemetry darkness (root cause of the 2026-06-18 staging outage).
        manifest_metric_filter = logs.MetricFilter(
            self, "DecoderManifestFetchFailedFilter",
            log_group=sync_log_group,
            filter_pattern=logs.FilterPattern.literal("DecoderManifestFetchFailed"),
            metric_namespace="CMS/FWE",
            metric_name="DecoderManifestFetchFailed",
            metric_value="1",
            default_value=0,
        )

        # SNS topic for the alarm action (reuse existing flink-alarms topic if in same
        # account/region; create a minimal one here since this is a separate stack).
        manifest_alarm_topic = sns.Topic(
            self, "FweManifestAlarmTopic",
            topic_name=f"cms-{stage}-fwe-manifest-alarms",
            master_key=kms.Alias.from_alias_name(
                self, "FweManifestAlarmSnsKey", "alias/aws/sns"
            ),
        )

        manifest_alarm = cloudwatch.Alarm(
            self, "DecoderManifestFetchFailedAlarm",
            alarm_name=f"cms-{stage}-flink-campaign-sync-decoder-manifest-fetch-failed",
            alarm_description=(
                "CampaignSyncProcessor failed to fetch DecoderManifest.bin from S3. "
                "FWE agents will receive collection schemes without a decoder manifest "
                "and will decode nothing. Check fwe.config.bucket in the app config."
            ),
            metric=cloudwatch.Metric(
                namespace="CMS/FWE",
                metric_name="DecoderManifestFetchFailed",
                statistic="Sum",
                period=Duration.minutes(5),
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        manifest_alarm.add_alarm_action(cw_actions.SnsAction(manifest_alarm_topic))

        # campaign_sync_app inherits the flink-stack ordering via CFN cross-stack
        # references (flink_jar_bucket / flink_jar_key Fn.import_value). The FWE
        # manifest BucketDeployment in flink_stack (FweManifestDeployment) runs as
        # part of cms-<stage>-flink, which CloudFormation fully completes before
        # cms-<stage>-fleetwise can create. No explicit node.add_dependency needed.

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
