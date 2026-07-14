"""
Flink Stack - Stream processing applications with MSK integration
"""

import os
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

        # One-shot BOOTSTRAP_SERVERS warning — fires ONCE per FlinkStack
        # construction (i.e. once per `cdk deploy <stack>` regardless of which
        # stack is being deployed) when MSK is available but the env var is
        # unset. Previously this printed inside create_flink_app_config which
        # fires 9× per cdk synth (once per Flink app) — across `make deploy-all`
        # that compounded to ~80 prints. Operators who hit a real misconfig
        # still see the breadcrumb, but the noise floor is dramatically lower.
        if msk_available and not os.environ.get("BOOTSTRAP_SERVERS"):
            print(
                f"⚠️  {construct_id}: BOOTSTRAP_SERVERS unset; Flink apps will "
                "render with empty bootstrap.servers. Use `make phase4` / "
                "`make deploy-flink` (auto-resolves). See "
                "issues/2026-06-11-flink-stack-deploy-blockers."
            )

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

        # Upload the committed DecoderManifest.bin into fwe-config/ of the SAME jar bucket
        # that the CampaignSyncProcessor reads (fwe.config.bucket = this bucket's name).
        # prune=False: do not delete other keys when deploying; retain_on_delete=True: keep
        # the manifest in S3 on stack deletion so FWE agents can still fetch it.
        # To regenerate: run `DRY_RUN=1 python3 deployment/scripts/generate_decoder_manifest.py`
        # and commit the result to deployment/fwe-config/DecoderManifest.bin.
        self.fwe_manifest_deployment = s3deploy.BucketDeployment(
            self, "FweManifestDeployment",
            sources=[s3deploy.Source.asset("fwe-config")],
            destination_bucket=self.jar_bucket,
            destination_key_prefix="fwe-config/",
            prune=False,
            retain_on_delete=True,
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

        # Add S3 PutObject permission for OEM transform DLQ — scoped to dlq/oem/* prefix only.
        # Way B refactor (spec 2026-06-05-cms-oem1-connector-flink-shape-mismatch): the
        # OEMTelemetryProcessor.writeToS3DLQ writes transform-failure objects under
        # `dlq/oem/<timestamp>-<uuid>.json` to the transform-manifests bucket. Without this
        # grant the put fails with `not authorized to perform: s3:PutObject`, swallowing
        # transform-failure visibility.
        _deployment_stage_for_dlq = construct_id.replace('-flink', '').split('-', 1)[-1]
        self.flink_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["s3:PutObject"],
                resources=[
                    f"arn:aws:s3:::cms-{_deployment_stage_for_dlq}-transform-manifests-{self.region}-{self.account}/dlq/oem/*"
                ]
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
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-*-storage-*/index/*",
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-*-event-catalog",
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-*-event-catalog/index/*",
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-*-signal-catalog",
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-*-signal-catalog/index/*",
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-*-campaigns",
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-*-campaigns/index/*",
                    # FW + Maintenance processors write to the VFO action
                    # queue when a CRITICAL/HIGH-severity DTC is detected —
                    # operator sees it on the Fleet Command Center Pending
                    # Actions card. Table name is cms-<stage>-vfo-action-
                    # queue (no "-storage-" prefix, so it doesn't match the
                    # cms-*-storage-* wildcard above).
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-*-vfo-action-queue",
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-*-vfo-action-queue/index/*",
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

        # campaign-sync-processor is managed by the fleetwise stack
        
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
            elif msk_vpc_id and msk_security_group_id and msk_subnet_ids:
                # Standalone-deploy path: app.py propagated MSK_VPC_ID /
                # MSK_SECURITY_GROUP_ID / MSK_SUBNET_IDS env vars (from
                # `make deploy-flink` resolving `aws cloudformation
                # describe-stacks cms-{stage}-msk` outputs). Without this
                # branch, FlinkStack's first __init__ branch (line ~38)
                # sets msk_available=True from the env vars but the apps
                # render with `vpc_configurations=None` — Flink jobs come
                # up healthy in CFN's view but get IMMEDIATELY DISCONNECTED
                # from MSK brokers (port 9098 reachable from default VPC,
                # but TLS/IAM handshake fails or the connection is dropped
                # mid-handshake) → SourceCoordinator triggers job failover
                # in a tight loop. Hit on 2026-06-12 12:30 EDT during the
                # v0.2.3 publish-gate cycle (issues/2026-06-11-flink-stack-deploy-blockers
                # § "VPC config stripped on deploy-flink path").
                vpc_configuration_for_apps = {
                    "SubnetIds": msk_subnet_ids[:2],
                    "SecurityGroupIds": [msk_security_group_id]
                }
        
        # Common application configuration (matching flinkSetup.md requirements)
        def create_flink_app_config(processor_type: str, additional_properties: Dict[str, str] = None, parallelism: int = 1):
            """
            Construct a typed ApplicationConfigurationProperty for Flink apps.
            
            Returns a kinesisanalytics.CfnApplication.ApplicationConfigurationProperty instance.
            All 9 Flink apps use this helper: 8 directly (OEM, FW, SimulatorPreprocessor, 
            TelemetryEnhanced, Trip, Safety, Maintenance, Geofence) and EventDriven via 
            additional_properties override of auto.offset.reset + KAFKA_TOPIC + group.id + TABLE_NAME.
            
            Type-safety prevents silent-drop regression (PascalCase dict keys silently dropped by jsii).
            Canonical typed-property pattern: see docs/tech.md § (c.1).
            Spec history: 2026-06-08-cms-flink-cfn-config-keys-fix.
            """
            base_properties = {
                "PROCESSOR_TYPE": processor_type,
                "auto.offset.reset": "latest",
                "enable.auto.commit": "false",
                "aws.region": self.region,
                # When deploying against an existing MSK cluster (MSK_CLUSTER_ARN set),
                # app.py leaves msk_stack=None, so fall back to the REDIS_ENDPOINT env
                # var. Without this, REDIS_ENDPOINT renders "" and CFN rejects the
                # change set (minLength:1) for every Flink app — which is why staging's
                # flink stack could never be redeployed to pick up DECODER_TABLE.
                "REDIS_ENDPOINT": (msk_stack.redis_endpoint if msk_stack
                                   else os.environ.get("REDIS_ENDPOINT", "")),
            }
            
            # Add MSK configuration only if MSK is available
            if msk_available:
                # bootstrap.servers must be resolved at deploy time. AWS::MSK::Cluster
                # does not expose broker endpoints as a CFN attribute (only via the
                # `kafka:GetBootstrapBrokers` API), so we receive it from the
                # Makefile, which resolves it via:
                #   aws kafka get-bootstrap-brokers --cluster-arn $MSK_CLUSTER_ARN \
                #     --query 'BootstrapBrokerStringSaslIam' --output text
                # and exports it for both `phase4` (deploy-all path) and
                # `deploy-flink` (standalone path). Mirrors the REDIS_ENDPOINT
                # pattern above (line ~409). Fail-loud if missing — silently
                # rendering "" here ships a Flink app that crash-loops on the
                # `localhost:9092` Java fallback (see issue
                # 2026-06-11-flink-stack-deploy-blockers, which is exactly the
                # outage this port-from-history is fixing).
                bootstrap_servers = os.environ.get("BOOTSTRAP_SERVERS", "")
                # If unset, render bootstrap.servers="" — Flink apps will
                # crash on the Java-code localhost:9092 fallback at startup
                # (TripProcessor.java:165 + EventDriven:90 throw on localhost
                # per issue 2026-06-11-flink-stack-deploy-blockers). Single
                # one-shot warning fires at FlinkStack construction time
                # (see __init__ above); no per-app print to keep deploy
                # output clean.
                # IAM auth ported from pre-fe42393 deployment/Makefile § configure-flink
                # (the per-app PropertyMap loop). The IAM permissions
                # (kafka-cluster:Connect/ReadData/WriteData/AlterGroup/etc.)
                # are already attached to flink_role above (see line ~219).
                #
                # Two distinct MSK auth surfaces in this codebase:
                #  - IoT Rule  → SCRAM (BootstrapBrokerStringSaslScram, port 9096)
                #                 wired in telemetry_integration_stack.py
                #  - Flink app → IAM   (BootstrapBrokerStringSaslIam,   port 9098)
                #                 wired here
                # Do NOT change the IoT Rule path — only Flink is IAM.
                #
                # group.id is intentionally NOT in this base block: every call
                # site overrides it per-app via additional_properties, matching
                # the pre-fe42393 configure-flink per-app GROUP_ID semantics
                # (each Flink app needs its own consumer group so they don't
                # compete for partitions on shared topics).
                base_properties.update({
                    "bootstrap.servers": bootstrap_servers,
                    "security.protocol": "SASL_SSL",
                    "sasl.mechanism": "AWS_MSK_IAM",
                    "sasl.jaas.config": "software.amazon.msk.auth.iam.IAMLoginModule required;",
                    "sasl.client.callback.handler.class": "software.amazon.msk.auth.iam.IAMClientCallbackHandler",
                    # Default input topic for apps that don't override
                    # (SimulatorPreprocessor, TelemetryEnhanced); FW overrides
                    # to fw-telemetry-raw, EventDriven uses KAFKA_TOPIC instead.
                    "input.topic": "cms-telemetry-raw",
                })
            
            if additional_properties:
                base_properties.update(additional_properties)
                
            # Typed ApplicationConfigurationProperty — PascalCase dict keys are silently
            # dropped by CDK's jsii layer. Typed objects fail loudly on unknown kwargs.
            # See docs/tech.md § (c.1) for the canonical pattern and spec
            # 2026-06-08-cms-flink-cfn-config-keys-fix for the root cause.
            # Do NOT add CloudWatch alarm authoring inline here — use standalone
            # aws_cloudwatch.Alarm constructs. See spec
            # 2026-06-08-cms-flink-cfn-config-keys-fix § Constraints (alarm-authoring
            # trap carry-forward).
            app_config = kinesisanalytics.CfnApplication.ApplicationConfigurationProperty(
                application_code_configuration=kinesisanalytics.CfnApplication.ApplicationCodeConfigurationProperty(
                    code_content=kinesisanalytics.CfnApplication.CodeContentProperty(
                        s3_content_location=kinesisanalytics.CfnApplication.S3ContentLocationProperty(
                            bucket_arn=self.jar_bucket.bucket_arn,
                            file_key=jar_s3_key,
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
                    # Do NOT add CloudWatch alarm authoring inline in monitoring_configuration
                    # — use standalone aws_cloudwatch.Alarm constructs (see existing alarms
                    # at the bottom of this stack). The dict-form silent-drop class
                    # (spec 2026-06-08-cms-flink-cfn-config-keys-fix § Constraints —
                    # alarm-authoring trap) means a future regression to PascalCase OR a
                    # nested alarm-config in monitoring_configuration would silently disable
                    # the alarm with no synth error.
                    monitoring_configuration=kinesisanalytics.CfnApplication.MonitoringConfigurationProperty(
                        configuration_type="CUSTOM",
                        metrics_level="APPLICATION",
                        log_level="INFO",
                    ),
                    parallelism_configuration=kinesisanalytics.CfnApplication.ParallelismConfigurationProperty(
                        # Must be CUSTOM because we supply explicit parallelism values.
                        # With DEFAULT, KDA rejects the UPDATE: "trying to provide custom
                        # values for the parallelism configuration ... update using
                        # ConfigurationTypeUpdate as CUSTOM" — which rolled back the whole
                        # flink stack (issues/2026-06-11-flink-stack-deploy-blockers).
                        configuration_type="CUSTOM",
                        parallelism=parallelism,
                        parallelism_per_kpu=1,
                        auto_scaling_enabled=True,
                    ),
                ),
                environment_properties=kinesisanalytics.CfnApplication.EnvironmentPropertiesProperty(
                    property_groups=[kinesisanalytics.CfnApplication.PropertyGroupProperty(
                        property_group_id="consumer.config.0",
                        property_map=base_properties,
                    )],
                ),
                vpc_configurations=(
                    [kinesisanalytics.CfnApplication.VpcConfigurationProperty(
                        subnet_ids=vpc_configuration_for_apps["SubnetIds"],
                        security_group_ids=vpc_configuration_for_apps["SecurityGroupIds"],
                    )]
                    if vpc_configuration_for_apps else None
                ),
            )

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
        # EventDrivenTelemetryProcessor uses the same helper as the other 8 Flink
        # apps; auto.offset.reset='earliest' is the intentional override for
        # backfill-from-start semantics (spec 2026-06-08-cms-flink-cfn-config-keys-fix § Design item 2).
        #
        # FLEET_ENROLLMENT_TABLE / DEPLOYMENT_STAGE are passed to defeat
        # the "stage=prod" Java default at EventDrivenTelemetryProcessor.java:82
        # (`String stage = params.get("DEPLOYMENT_STAGE", "prod")`). Without
        # those keys, Java's enrollmentTable falls back to
        # `cms-prod-storage-fleet-enrollment` on every Flink app — a table
        # that doesn't exist outside the prod account → `Requested resource
        # not found` on every fleetId enrichment lookup. Same bug class as
        # 851addd (FW VEHICLES_TABLE) + a7d03f0 (Trip TRIPS_TABLE_NAME) —
        # historical configure-flink Makefile set FLEET_ENROLLMENT_TABLE
        # explicitly per-app (EXTRA_PROPS); fe42393's CDK migration dropped
        # it. Surfaced 2026-06-12 12:55 EDT during publish-gate validation.
        deployment_stage = construct_id.replace("-flink", "").split("-", 1)[-1]
        event_driven_app_config = create_flink_app_config("EventDrivenTelemetryProcessor", {
            "auto.offset.reset": "earliest",  # backfill from start; overrides helper default "latest"
            "KAFKA_TOPIC": "cms-telemetry-preprocessed",
            "group.id": f"{construct_id}-event-driven-telemetry-consumer",
            "TABLE_NAME": storage_tables['vehicles'].table_name,
            "DEPLOYMENT_STAGE": deployment_stage,
            "FLEET_ENROLLMENT_TABLE": storage_tables['fleet_enrollment'].table_name,
        })

        self.event_driven_telemetry_processor = kinesisanalytics.CfnApplication(
            self, "EventDrivenTelemetryProcessor",
            application_name=f"{construct_id}-event-driven-telemetry-processor",
            runtime_environment="FLINK-1_18",
            service_execution_role=self.flink_role.role_arn,
            application_configuration=event_driven_app_config,
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
                "S3_MANIFEST_BUCKET": f"cms-{construct_id.replace('-flink', '').split('-', 1)[-1]}-transform-manifests-{self.region}-{self.account}"
            }
        )
        
        self.oem_telemetry_processor = kinesisanalytics.CfnApplication(
            self, "OEMTelemetryProcessor",
            application_name=f"{construct_id}-oem-telemetry-processor",
            runtime_environment="FLINK-1_18",
            service_execution_role=self.flink_role.role_arn,
            application_configuration=oem_app_config,
            application_description="OEM telemetry transformer (OEM1/GM/Stellantis to CMS format)"
        )
        
        # Add CloudWatch logging to OEM telemetry processor
        kinesisanalytics.CfnApplicationCloudWatchLoggingOption(
            self, "OEMTelemetryLogging",
            application_name=self.oem_telemetry_processor.ref,
            cloud_watch_logging_option=kinesisanalytics.CfnApplicationCloudWatchLoggingOption.CloudWatchLoggingOptionProperty(
                log_stream_arn=f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.oem_telemetry_log_group.log_group_name}:log-stream:kinesis-analytics-log-stream"
            )
        )
        
        # 1c. FW Telemetry Processor (decodes FleetWise protobuf -> cms-telemetry-preprocessed)
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
                    # Java reads `TABLE_NAME` (FWTelemetryProcessor.java:121) — NOT
                    # `VEHICLES_TABLE`. The prior key name was silently ignored, so
                    # vehiclesTable would fall back to the cms-prod-* default on a
                    # CDK redeploy. Likewise DECODER_TABLE (read at :122) must be set
                    # or it defaults to cms-prod-decoder-manifest — which mislabels
                    # every signal via prod's divergent signal_id->FQN map and breaks
                    # trip creation. See issues/2026-06-11-fw-telemetry-decoder-table-prod-default.
                    "TABLE_NAME": storage_tables['vehicles'].table_name,
                    "DECODER_TABLE": f"{construct_id.replace('-flink', '')}-decoder-manifest",
                }
            ),
            application_description="FleetWise protobuf decoder (FWE binary -> CMS JSON)"
        )

        kinesisanalytics.CfnApplicationCloudWatchLoggingOption(
            self, "FWTelemetryLogging",
            application_name=self.fw_telemetry_processor.ref,
            cloud_watch_logging_option=kinesisanalytics.CfnApplicationCloudWatchLoggingOption.CloudWatchLoggingOptionProperty(
                log_stream_arn=f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.fw_telemetry_log_group.log_group_name}:log-stream:kinesis-analytics-log-stream"
            )
        )

        # 1d. Simulator Preprocessor (decodes gzip+base64 from simulator -> cms-telemetry-preprocessed)
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
            application_description="Simulator preprocessor (gzip+base64 -> CMS JSON)"
        )

        kinesisanalytics.CfnApplicationCloudWatchLoggingOption(
            self, "SimulatorPreprocessorLogging",
            application_name=self.simulator_preprocessor.ref,
            cloud_watch_logging_option=kinesisanalytics.CfnApplicationCloudWatchLoggingOption.CloudWatchLoggingOptionProperty(
                log_stream_arn=f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.simulator_preprocessor_log_group.log_group_name}:log-stream:kinesis-analytics-log-stream"
            )
        )

        # Cold-region race fix (2026-06-15): gate first KDA app on BucketDeployment JAR upload; rest of chain inherits transitively via line ~1019 serialize chain.
        self.simulator_preprocessor.node.add_dependency(self.node.find_child("FlinkJarDeployment"))

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
                    # Java reads `TABLE_NAME` (TripProcessor.java:154 —
                    # `applicationProperties.get("TABLE_NAME", "cms-dev-storage-trips")`).
                    # The prior `TRIPS_TABLE_NAME` key was silently ignored, so
                    # tripsTableName fell through to the hardcoded `cms-dev-storage-trips`
                    # default — which does not exist in staging/prod, producing
                    # `Requested resource not found (Service: DynamoDb)` on every
                    # `getActiveTripForVehicle` query (TripProcessor.java:688) and
                    # blocking ACTIVE-trip lookups stack-wide.
                    # Same bug class as 851addd fix(cdk) for FWTelemetryProcessor
                    # (VEHICLES_TABLE → TABLE_NAME) — Java is the source of truth
                    # for env-var keys.
                    "TABLE_NAME": storage_tables['trips'].table_name,
                },
                parallelism=3,
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
        # Derive stage from construct_id (e.g. cms-prod-flink -> prod)
        deployment_stage = construct_id.replace("-flink", "").split("-", 1)[-1]
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
                    "TABLE_NAME": storage_tables['safety_events'].table_name,
                    "safety.table.name": storage_tables['safety_events'].table_name,
                    "trips.table.name": storage_tables['trips'].table_name,
                    "event.catalog.table": f"cms-{deployment_stage}-event-catalog",
                    "signal.catalog.table": f"cms-{deployment_stage}-signal-catalog",
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
                    "MAINTENANCE_TABLE_NAME": storage_tables['maintenance_events'].table_name,
                    "maintenance.table.name": storage_tables['maintenance_events'].table_name,
                    "trips.table.name": storage_tables['trips'].table_name,
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
        
        # 6. Campaign Sync Processor - managed by fleetwise stack (not flink stack)

        # 7. Geofence Processor (evaluates vehicle positions against active geofences)
        self.geofence_log_group = logs.LogGroup(
            self, "GeofenceProcessorLogGroup",
            log_group_name=f"/aws/kinesis-analytics/{construct_id}-geofence-processor",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY
        )

        self.geofence_processor = kinesisanalytics.CfnApplication(
            self, "GeofenceProcessor",
            application_name=f"{construct_id}-geofence-processor",
            application_description="Geofence processor - evaluates vehicle positions against active geofences",
            runtime_environment="FLINK-1_18",
            service_execution_role=self.flink_role.role_arn,
            application_configuration=create_flink_app_config(
                "GeofenceProcessor",
                {
                    "group.id": "geofence-processor-group",
                    "GEOFENCE_TABLE": f"cms-{deployment_stage}-storage-geofences",
                    "geofence.table.name": f"cms-{deployment_stage}-storage-geofences",
                    "SAFETY_TABLE": storage_tables['safety_events'].table_name,
                    "safety.table.name": storage_tables['safety_events'].table_name,
                }
            )
        )

        kinesisanalytics.CfnApplicationCloudWatchLoggingOption(
            self, "GeofenceProcessorLogging",
            application_name=self.geofence_processor.ref,
            cloud_watch_logging_option=kinesisanalytics.CfnApplicationCloudWatchLoggingOption.CloudWatchLoggingOptionProperty(
                log_stream_arn=f"arn:aws:logs:{self.region}:{self.account}:log-group:{self.geofence_log_group.log_group_name}:log-stream:kinesis-analytics-log-stream"
            )
        )

        # ── Serialize Flink app UPDATE/CREATE via DependsOn chain ──
        #
        # KDA's UpdateApplication API has a per-account-region control-plane
        # rate limit (~30-60 RPM, undocumented). When CFN issues UpdateApplication
        # for all 9 apps in parallel during a stack UPDATE, the bucket is
        # exhausted and several apps fail with:
        #   "Rate exceeded (Service: KinesisAnalyticsV2, Status Code: 400)
        #    HandlerErrorCode: GeneralServiceException"
        # after the SDK's 3 internal retries. The whole stack rolls back.
        # Repro: 2026-06-11 14:00 + 2026-06-12 09:46 + 2026-06-12 09:52 staging
        # deploys all hit this; see issues/2026-06-11-flink-stack-deploy-blockers
        # § "KDA UpdateApplication rate limit".
        #
        # Fix: chain DependsOn between every Flink app so CFN serializes the
        # UPDATE operations. Adds ~60s/app to deploy time (9 apps = ~9 min total
        # for the Flink portion) but makes deploys deterministic. Net cost is
        # acceptable for a reference template that customers deploy from the
        # public mirror — they would hit the same rate limit on first deploy
        # without this chain.
        #
        # Order is data-flow-aligned for readability (preprocessor → enhanced
        # processors → trip/safety/maintenance/geofence outputs) but the chain
        # is purely about serialization, not topological correctness.
        flink_app_chain = [
            self.simulator_preprocessor,
            self.event_driven_telemetry_processor,
            self.oem_telemetry_processor,
            self.fw_telemetry_processor,
            self.telemetry_enhanced_processor,
            self.trip_processor,
            self.safety_processor,
            self.maintenance_processor,
            self.geofence_processor,
        ]
        for prev, curr in zip(flink_app_chain, flink_app_chain[1:]):
            curr.add_dependency(prev)

        # ── CloudWatch Alarms for Flink processor health ──
        from aws_cdk import aws_cloudwatch as cloudwatch

        critical_apps = [
            ("fw-telemetry-processor", self.fw_telemetry_processor),
            ("trip-processor", self.trip_processor),
            ("safety-processor", self.safety_processor),
            ("simulator-preprocessor", self.simulator_preprocessor),
            ("event-driven-telemetry-processor", self.event_driven_telemetry_processor),
            ("maintenance-processor", self.maintenance_processor),
            ("geofence-processor", self.geofence_processor),
        ]

        for short_name, app_resource in critical_apps:
            full_name = f"{construct_id}-{short_name}"
            # Downtime alarm - fires if app has >1 min downtime in 5 min window
            cloudwatch.Alarm(
                self, f"Alarm-{short_name}-down",
                alarm_name=f"{full_name}-down",
                alarm_description=f"Flink app {short_name} has been down for >1 min",
                metric=cloudwatch.Metric(
                    namespace="AWS/KinesisAnalytics",
                    metric_name="downtime",
                    dimensions_map={"Application": full_name},
                    statistic="Maximum",
                    period=Duration.minutes(5),
                ),
                threshold=60000,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )

        # Idle processing alarms for data-path processors
        for short_name in ["fw-telemetry-processor", "trip-processor"]:
            full_name = f"{construct_id}-{short_name}"
            cloudwatch.Alarm(
                self, f"Alarm-{short_name}-idle",
                alarm_name=f"{full_name}-idle",
                alarm_description=f"Flink app {short_name} processed 0 records in 10 min",
                metric=cloudwatch.Metric(
                    namespace="AWS/KinesisAnalytics",
                    metric_name="numRecordsInPerSecond",
                    dimensions_map={"Application": full_name},
                    statistic="Sum",
                    period=Duration.minutes(10),
                ),
                threshold=0,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
            )

        # ── Spec 2026-06-17-oem1-event-driven-pipeline-scale: OEM1 path + domain-tier alarm tier ──
        # Standalone aws_cloudwatch.Alarm constructs per spec § Design 4 + Constraint 5.
        # SNS topic is account-region scoped — safe for cross-region deploys.
        from aws_cdk import aws_sns as sns
        from aws_cdk import aws_cloudwatch_actions as cw_actions
        from aws_cdk import aws_kms as kms

        # Encrypt at rest with the AWS-managed SNS CMK (free; clears cdk-nag
        # AwsSolutions-SNS2). Payload is operational-metadata only, but a
        # brand-new resource should not ship a known, free-to-fix finding.
        flink_alarms_topic = sns.Topic(
            self, "FlinkAlarmsTopic",
            topic_name=f"cms-{deployment_stage}-flink-alarms",
            display_name=f"CMS {deployment_stage} Flink Alarms",
            master_key=kms.Alias.from_alias_name(
                self, "FlinkAlarmsSnsKey", "alias/aws/sns"
            ),
        )

        # Six domain-tier + OEM1-path processors to monitor
        domain_tier_apps = [
            ("oem-telemetry", f"{construct_id}-oem-telemetry-processor"),
            ("event-driven", f"{construct_id}-event-driven-telemetry-processor"),
            ("trip", f"{construct_id}-trip-processor"),
            ("safety", f"{construct_id}-safety-processor"),
            ("maintenance", f"{construct_id}-maintenance-processor"),
            ("telemetry-data", f"{construct_id}-telemetry-enhanced-final"),
        ]

        for short_key, app_full_name in domain_tier_apps:
            # records_lag_max > 10000 for 3 consecutive 5-min periods
            lag_alarm = cloudwatch.Alarm(
                self, f"FlinkAlarm-{short_key}-lag",
                alarm_name=f"{app_full_name}-lag-high",
                alarm_description=f"{short_key}: records_lag_max sustained >10k — consumer falling behind",
                metric=cloudwatch.Metric(
                    namespace="AWS/KinesisAnalytics",
                    metric_name="records_lag_max",
                    dimensions_map={"Application": app_full_name},
                    statistic="Maximum",
                    period=Duration.minutes(5),
                ),
                threshold=10000,
                evaluation_periods=3,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            lag_alarm.add_alarm_action(cw_actions.SnsAction(flink_alarms_topic))

            # downtime > 0 for 1×1min
            down_alarm = cloudwatch.Alarm(
                self, f"FlinkAlarm-{short_key}-down",
                alarm_name=f"{app_full_name}-down-new",
                alarm_description=f"{short_key}: downtime > 0 — app not running",
                metric=cloudwatch.Metric(
                    namespace="AWS/KinesisAnalytics",
                    metric_name="downtime",
                    dimensions_map={"Application": app_full_name},
                    statistic="Maximum",
                    period=Duration.minutes(1),
                ),
                threshold=0,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            down_alarm.add_alarm_action(cw_actions.SnsAction(flink_alarms_topic))

            # fullRestarts > 2 in 15min (3 periods of 5min)
            restart_alarm = cloudwatch.Alarm(
                self, f"FlinkAlarm-{short_key}-restarts",
                alarm_name=f"{app_full_name}-restarts",
                alarm_description=f"{short_key}: fullRestarts > 2 in 15min — crash loop",
                metric=cloudwatch.Metric(
                    namespace="AWS/KinesisAnalytics",
                    metric_name="fullRestarts",
                    dimensions_map={"Application": app_full_name},
                    statistic="Sum",
                    period=Duration.minutes(5),
                ),
                threshold=2,
                evaluation_periods=3,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            restart_alarm.add_alarm_action(cw_actions.SnsAction(flink_alarms_topic))

            # numberOfFailedCheckpoints > 0 for 1×5min
            checkpoint_alarm = cloudwatch.Alarm(
                self, f"FlinkAlarm-{short_key}-failed-checkpoints",
                alarm_name=f"{app_full_name}-failed-checkpoints",
                alarm_description=f"{short_key}: numberOfFailedCheckpoints > 0 — state/sink health issue",
                metric=cloudwatch.Metric(
                    namespace="AWS/KinesisAnalytics",
                    metric_name="numberOfFailedCheckpoints",
                    dimensions_map={"Application": app_full_name},
                    statistic="Sum",
                    period=Duration.minutes(5),
                ),
                threshold=0,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            checkpoint_alarm.add_alarm_action(cw_actions.SnsAction(flink_alarms_topic))

            # containerCPUUtilization > 75% for 3×5min
            cpu_alarm = cloudwatch.Alarm(
                self, f"FlinkAlarm-{short_key}-cpu",
                alarm_name=f"{app_full_name}-cpu-high",
                alarm_description=f"{short_key}: containerCPUUtilization > 75% — capacity planning signal",
                metric=cloudwatch.Metric(
                    namespace="AWS/KinesisAnalytics",
                    metric_name="containerCPUUtilization",
                    dimensions_map={"Application": app_full_name},
                    statistic="Maximum",
                    period=Duration.minutes(5),
                ),
                threshold=75,
                evaluation_periods=3,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            cpu_alarm.add_alarm_action(cw_actions.SnsAction(flink_alarms_topic))

        # Store applications for easy access
        self.applications = {
            'simulator_preprocessor': self.simulator_preprocessor,
            'event_driven_telemetry_processor': self.event_driven_telemetry_processor,
            'telemetry_enhanced_processor': self.telemetry_enhanced_processor,
            'trip_processor': self.trip_processor, 
            'safety_processor': self.safety_processor,
            'maintenance_processor': self.maintenance_processor,
            'fw_telemetry_processor': self.fw_telemetry_processor,
            'oem_telemetry_processor': self.oem_telemetry_processor,
            'geofence_processor': self.geofence_processor,
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
