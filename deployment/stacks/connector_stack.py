"""
CDK Stack: OEM Connector Service

ECS Fargate task that ingests telemetry from an OEM source and writes
clean JSON to the cms-telemetry-oem Kafka topic.

Supports three connection types (configured via CONNECTOR_TYPE env var):
  - rest_polling:      Poll-sleep loop against OEM REST API
  - grpc_streaming:    Long-lived gRPC client (e.g., OEM1 Feed Service)
  - websocket_inbound: Accept inbound WebSocket connections (adds ALB + TLS)

Usage:
  make deploy-connector CONNECTOR_NAME=oem1-feed CONNECTOR_TYPE=grpc_streaming
"""
from aws_cdk import (
    Stack, Duration, Fn, CfnOutput,
    aws_ecs as ecs,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_logs as logs,
    aws_secretsmanager as sm,
    aws_elasticloadbalancingv2 as elbv2,
    aws_cloudwatch as cw,
    aws_lambda as lambda_,
    aws_apigateway as apigw,
    aws_cognito as cognito,
    aws_ecr_assets as ecr_assets,
    aws_events as events,
    aws_events_targets as events_targets,
)
from constructs import Construct
import os
import subprocess
import sys
import shutil

# ── OEM1 Connector alarm thresholds (namespace: CMS/OEM1Connector) ────────────
# Match spec § Phase C dashboard alarm thresholds exactly.
OEM1_GET_FLOW_AGE_WARN_SECONDS = 600       # 10 min — "OEM1 not sending data"
OEM1_GET_FLOW_AGE_CRITICAL_SECONDS = 86400  # 24 h — "approaching 7-day data loss"
OEM1_STALE_REF_RECOVERED_THRESHOLD = 0      # > 0 triggers info alarm


# ── OEM1 admin-Lambda asset bundling ──────────────────────────────────────────
# The OEM1 admin Lambdas live in `services/connectors/oem1/<lambda_subdir>/`
# and `import token_supplier` from the sibling `services/connectors/oem1/
# token_supplier.py`. A naive `Code.from_asset("../services/connectors/oem1/
# <lambda_subdir>")` packages only the per-Lambda dir, so `token_supplier.py`
# is missing from the deployment zip and the handler crashes on cold-start
# with `Runtime.ImportModuleError: No module named 'token_supplier'`
# (issue 2026-06-08-oem1-admin-lambdas-missing-token-supplier-module).
#
# This helper stages the per-Lambda dir + the shared `token_supplier.py`
# (plus any other shared modules in the future) into a clean synth-time
# build dir under `deployment/stacks/.build/oem1_lambdas/<lambda_subdir>/`
# and returns its absolute path for use with `Code.from_asset(...)`.
_OEM1_SRC_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "services", "connectors", "oem1",
))
_OEM1_LAMBDA_BUILD_ROOT = os.path.join(
    os.path.dirname(__file__), ".build", "oem1_lambdas",
)
_OEM1_LAMBDA_SHARED_MODULES = ("token_supplier.py",)


def _bundle_oem1_lambda(lambda_subdir: str) -> str:
    """Stage a per-Lambda asset that includes shared OEM1 modules.

    Copies `services/connectors/oem1/<lambda_subdir>/` into the build dir
    and overlays the shared modules listed in `_OEM1_LAMBDA_SHARED_MODULES`
    at the asset root. Re-stages on every synth so source edits are picked up.
    Returns the absolute path to the staged asset directory.
    """
    src_lambda_dir = os.path.join(_OEM1_SRC_DIR, lambda_subdir)
    if not os.path.isdir(src_lambda_dir):
        raise FileNotFoundError(
            f"OEM1 lambda source dir not found: {src_lambda_dir}"
        )
    dst = os.path.join(_OEM1_LAMBDA_BUILD_ROOT, lambda_subdir)
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(
        src_lambda_dir,
        dst,
        ignore=shutil.ignore_patterns(
            "__pycache__", "tests", "*.pyc", ".pytest_cache",
        ),
    )
    for shared in _OEM1_LAMBDA_SHARED_MODULES:
        src_shared = os.path.join(_OEM1_SRC_DIR, shared)
        if not os.path.isfile(src_shared):
            raise FileNotFoundError(
                f"OEM1 shared module not found: {src_shared}"
            )
        shutil.copy2(src_shared, dst)
    _lib_src = os.path.join(_OEM1_SRC_DIR, "_lib")
    if os.path.isdir(_lib_src):
        shutil.copytree(
            _lib_src,
            os.path.join(dst, "_lib"),
            ignore=shutil.ignore_patterns("__pycache__", "tests", "*.pyc", ".pytest_cache"),
        )
    return dst


class ConnectorStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        stage = os.environ.get("DEPLOYMENT_STAGE", "dev")
        connector_name = os.environ.get("CONNECTOR_NAME", "generic")
        connector_type = os.environ.get("CONNECTOR_TYPE", "rest_polling")
        msk_stack = f"cms-{stage}-msk"

        # ── Import VPC + MSK from existing stacks ──────────────────────
        vpc_id = Fn.import_value(f"{msk_stack}-vpc-id")
        subnet_ids_joined = Fn.import_value(f"{msk_stack}-private-subnet-ids")
        vpc = ec2.Vpc.from_vpc_attributes(self, "Vpc",
            vpc_id=vpc_id,
            availability_zones=self.availability_zones[:2],
            private_subnet_ids=[
                Fn.select(0, Fn.split(",", subnet_ids_joined)),
                Fn.select(1, Fn.split(",", subnet_ids_joined)),
            ],
        )
        msk_sg_id = Fn.import_value(f"{msk_stack}-security-group-id")
        msk_sg = ec2.SecurityGroup.from_security_group_id(self, "MskSg", msk_sg_id)

        # ── ECS Cluster ────────────────────────────────────────────────
        cluster = ecs.Cluster(self, "Cluster", vpc=vpc,
            cluster_name=f"cms-{stage}-connector-{connector_name}")

        # ── Task Definition ────────────────────────────────────────────
        task_def = ecs.FargateTaskDefinition(self, "TaskDef",
            memory_limit_mib=1024,
            cpu=512,
        )

        # Grant MSK access
        task_def.task_role.add_to_principal_policy(iam.PolicyStatement(
            actions=[
                "kafka-cluster:Connect",
                "kafka-cluster:WriteData",
                "kafka-cluster:DescribeTopic",
                "kafka-cluster:CreateTopic",
                "kafka-cluster:DescribeCluster",
            ],
            resources=["*"],
        ))

        # Grant Secrets Manager access (for OEM credentials)
        task_def.task_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:cms-{stage}-connector-*"],
        ))

        # Grant S3 access (for transform manifests — connector may need config)
        task_def.task_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["s3:GetObject"],
            resources=[f"arn:aws:s3:::cms-{stage}-*-manifests/*"],
        ))

        # Grant DynamoDB access (for checkpoint storage)
        task_def.task_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"],
            resources=[f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-connector-checkpoints"],
        ))

        # ── Pre-existing IAM gaps surfaced 2026-06-04 by
        # `issues/2026-06-04-oem1-connector-arch-mismatch-crash-loop/` (the arch
        # crash masked these AccessDenied errors). The connector code requires:
        #   - ssm:GetParameter for the flow URI (main.py:_get_flow_uri)
        #   - dynamodb:Get/Put/UpdateItem on storage-vehicles (auto_register.py)
        #   - dynamodb:PutItem on storage-fleet-enrollment (auto_register.py)
        #   - cloudwatch:PutMetricData for the connector namespace (metrics.py)
        # Least-privilege scoped to specific resource ARNs / namespace.
        task_def.task_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[
                f"arn:aws:ssm:{self.region}:{self.account}:parameter/cms/{stage}/connectors/oem1/*"
            ],
        ))
        task_def.task_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"],
            resources=[f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-storage-vehicles"],
        ))
        task_def.task_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:PutItem"],
            resources=[f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-storage-fleet-enrollment"],
        ))
        task_def.task_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["cloudwatch:PutMetricData"],
            resources=["*"],  # PutMetricData has no resource-level granularity
            conditions={
                "StringEquals": {"cloudwatch:namespace": "CMS/OEM1Connector"}
            },
        ))

        # D4: SSM parameter read for flow URI
        task_def.task_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[
                f"arn:aws:ssm:{self.region}:{self.account}:parameter/cms/{stage}/connectors/oem1/*"
            ],
        ))
        # D4: Resolve MSK bootstrap brokers from cluster ARN at runtime (control-plane API)
        task_def.task_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["kafka:GetBootstrapBrokers", "kafka:DescribeCluster"],
            resources=[Fn.import_value(f"{msk_stack}-cluster-arn")],
        ))

        # ── Container ──────────────────────────────────────────────────
        log_group = logs.LogGroup(self, "Logs",
            log_group_name=f"/ecs/cms-{stage}-connector-{connector_name}",
            retention=logs.RetentionDays.TWO_WEEKS,
        )

        # Derive the services directory name: strip known suffixes like "-feed"
        connector_dir = connector_name.split("-")[0]  # "oem1-feed" → "oem1"
        connector_asset_path = f"../services/connectors/{connector_dir}"
        has_dockerfile = os.path.isfile(f"{connector_asset_path}/Dockerfile")
        if has_dockerfile:
            # Force linux/amd64 platform regardless of build host architecture.
            # Without this, building on Apple Silicon (arm64) produces an arm64
            # image that crashes on the default amd64 Fargate task with
            # `exec format error`. Fix per issue
            # `2026-06-04-oem1-connector-arch-mismatch-crash-loop`.
            container_image = ecs.ContainerImage.from_asset(
                connector_asset_path,
                platform=ecr_assets.Platform.LINUX_AMD64,
            )
        else:
            # Fall back to a placeholder image for synth when Dockerfile is absent
            container_image = ecs.ContainerImage.from_registry("amazon/amazon-ecs-sample")

        container = task_def.add_container("Connector",
            image=container_image,
            logging=ecs.LogDrivers.aws_logs(stream_prefix="connector", log_group=log_group),
            environment={
                "CONNECTOR_NAME": connector_name,
                "CONNECTOR_TYPE": connector_type,
                "OEM_SOURCE": connector_name,
                "KAFKA_TOPIC": "cms-telemetry-oem",
                "DEPLOYMENT_STAGE": stage,
                "AWS_REGION": self.region,
                # D4: OEM1 connector runtime config
                "OEM1_GRPC_ENDPOINT": "feed.autonomic.ai:443",
                "OEM1_FLOW_PARAMETER": f"/cms/{stage}/connectors/oem1/flow",
                "OEM1_EMIT_TARGET": "kafka",
                "OEM1_KAFKA_TOPIC": "cms-telemetry-oem",
                "OEM1_FEED_CREDENTIALS_SECRET": f"cms-{stage}-connector-oem1-feed-credentials",
                "OEM1_START_MODE": "earliest",
                "MSK_CLUSTER_ARN": Fn.import_value(f"{msk_stack}-cluster-arn"),
            },
        )

        # ── Security Group ─────────────────────────────────────────────
        connector_sg = ec2.SecurityGroup(self, "ConnectorSg",
            vpc=vpc,
            description=f"Connector {connector_name}",
            allow_all_outbound=True,
        )
        # Allow connector to talk to MSK
        msk_sg.add_ingress_rule(connector_sg, ec2.Port.tcp_range(9092, 9098),
            f"Connector {connector_name} to MSK")

        # ── Fargate Service ────────────────────────────────────────────
        service_props = dict(
            cluster=cluster,
            task_definition=task_def,
            desired_count=1,
            security_groups=[connector_sg, msk_sg],
            assign_public_ip=False,
            service_name=f"cms-{stage}-connector-{connector_name}",
        )

        # WebSocket inbound needs ALB for public endpoint
        if connector_type == "websocket_inbound":
            container.add_port_mappings(ecs.PortMapping(container_port=443))
            connector_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "Inbound WebSocket")

            service = ecs.FargateService(self, "Service", **service_props,
                assign_public_ip=True)

            # ALB for public TLS endpoint
            alb = elbv2.ApplicationLoadBalancer(self, "ALB",
                vpc=vpc, internet_facing=True,
                load_balancer_name=f"cms-{stage}-conn-{connector_name}"[:32],
            )
            listener = alb.add_listener("TLS", port=443,
                certificates=[],  # Add ACM cert ARN via env var or parameter
            )
            listener.add_targets("Target", port=443,
                targets=[service],
                health_check=elbv2.HealthCheck(path="/status", port="443"),
            )

            CfnOutput(self, "ALBEndpoint",
                value=alb.load_balancer_dns_name,
                description="Public endpoint for inbound WebSocket connections")
        else:
            service = ecs.FargateService(self, "Service", **service_props)

        # ── OEM1 CloudWatch Alarms ─────────────────────────────────────
        # Only added when this is the OEM1 gRPC streaming connector.
        if connector_name == "oem1-feed" or connector_type == "grpc_streaming":
            oem1_ns = "CMS/OEM1Connector"

            # 1. GetFlowLastReceivedAge > 600s → warn ("OEM1 not sending data")
            alarm_flow_warn = cw.Alarm(self, "Oem1GetFlowAgeWarn",
                alarm_name=f"cms-{stage}-oem1-get-flow-age-warn",
                alarm_description="OEM1 not sending data — GetFlowLastReceivedAge > 600s",
                metric=cw.Metric(
                    namespace=oem1_ns,
                    metric_name="GetFlowLastReceivedAge",
                    statistic="Maximum",
                    period=Duration.minutes(5),
                ),
                threshold=OEM1_GET_FLOW_AGE_WARN_SECONDS,
                evaluation_periods=1,
                datapoints_to_alarm=1,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.MISSING,
            )

            # 2. GetFlowLastReceivedAge > 86400s → critical ("approaching 7-day data loss")
            alarm_flow_critical = cw.Alarm(self, "Oem1GetFlowAgeCritical",
                alarm_name=f"cms-{stage}-oem1-get-flow-age-critical",
                alarm_description="OEM1 approaching 7-day data loss — GetFlowLastReceivedAge > 86400s",
                metric=cw.Metric(
                    namespace=oem1_ns,
                    metric_name="GetFlowLastReceivedAge",
                    statistic="Maximum",
                    period=Duration.minutes(5),
                ),
                threshold=OEM1_GET_FLOW_AGE_CRITICAL_SECONDS,
                evaluation_periods=1,
                datapoints_to_alarm=1,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.MISSING,
            )

            # 3. Oem1StaleReferenceRecovered > 0 → info (re-sharding event detected)
            alarm_stale_ref = cw.Alarm(self, "Oem1StaleReferenceRecovered",
                alarm_name=f"cms-{stage}-oem1-stale-reference-recovered",
                alarm_description="OEM1 re-sharding event detected — Oem1StaleReferenceRecovered > 0",
                metric=cw.Metric(
                    namespace=oem1_ns,
                    metric_name="Oem1StaleReferenceRecovered",
                    statistic="Sum",
                    period=Duration.minutes(5),
                ),
                threshold=OEM1_STALE_REF_RECOVERED_THRESHOLD,
                evaluation_periods=1,
                datapoints_to_alarm=1,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            )

            # ── OEM1 Connector Dashboard (C3.1 — all 6 widgets + alarm-status) ──
            dashboard = cw.Dashboard(self, "Oem1Dashboard",
                dashboard_name=f"cms-{stage}-oem1-connector",
                default_interval=Duration.hours(3),
            )
            # Row 1: alarm status
            dashboard.add_widgets(
                cw.AlarmStatusWidget(
                    title="OEM1 Connector Alarms",
                    alarms=[alarm_flow_warn, alarm_flow_critical, alarm_stale_ref],
                    width=24, height=2,
                ),
            )
            # Row 2: throughput + errors
            dashboard.add_widgets(
                cw.GraphWidget(
                    title="MessagesPerMinuteByShard (count/min)",
                    left=[cw.Metric(
                        namespace=oem1_ns,
                        metric_name="MessagesPerMinuteByShard",
                        statistic="Sum",
                        period=Duration.minutes(1),
                    )],
                    width=8, height=6,
                ),
                cw.GraphWidget(
                    title="ParseErrorRate (count)",
                    left=[cw.Metric(
                        namespace=oem1_ns,
                        metric_name="ParseErrorRate",
                        statistic="Sum",
                        period=Duration.minutes(5),
                    )],
                    width=8, height=6,
                ),
                cw.GraphWidget(
                    title="TransformErrorRate (count)",
                    left=[cw.Metric(
                        namespace=oem1_ns,
                        metric_name="TransformErrorRate",
                        statistic="Sum",
                        period=Duration.minutes(5),
                    )],
                    width=8, height=6,
                ),
            )
            # Row 3: latency + auth + connectivity
            dashboard.add_widgets(
                cw.GraphWidget(
                    title="MessageAgeSeconds (p50 / p95 / p99)",
                    left=[
                        cw.Metric(
                            namespace=oem1_ns,
                            metric_name="MessageAgeSeconds",
                            statistic="p50",
                            period=Duration.minutes(5),
                            label="p50",
                        ),
                        cw.Metric(
                            namespace=oem1_ns,
                            metric_name="MessageAgeSeconds",
                            statistic="p95",
                            period=Duration.minutes(5),
                            label="p95",
                        ),
                        cw.Metric(
                            namespace=oem1_ns,
                            metric_name="MessageAgeSeconds",
                            statistic="p99",
                            period=Duration.minutes(5),
                            label="p99",
                        ),
                    ],
                    width=8, height=6,
                ),
                cw.GraphWidget(
                    title="TokenRefreshCount (count)",
                    left=[cw.Metric(
                        namespace=oem1_ns,
                        metric_name="TokenRefreshCount",
                        statistic="Sum",
                        period=Duration.minutes(5),
                    )],
                    width=8, height=6,
                ),
                cw.GraphWidget(
                    title="GetFlowLastReceivedAge (s)",
                    left=[cw.Metric(
                        namespace=oem1_ns,
                        metric_name="GetFlowLastReceivedAge",
                        statistic="Maximum",
                        period=Duration.minutes(5),
                    )],
                    width=8, height=6,
                ),
            )

        # ── C2.2: Vehicle-state proxy Lambda + admin API Gateway route ─────────
        # Admin-IAM-protected only — no public-internet access.
        proxy_role = iam.Role(self, "VehicleStateProxyRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        # Least-privilege: Secrets Manager scoped to OEM1 credentials secret only
        proxy_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[
                f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:cms-{stage}-connector-oem1-credentials*"
            ],
        ))
        # CloudWatch Logs basic permissions
        proxy_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/cms-{stage}-vehicle-state-proxy*"],
        ))

        # ── Shared OEM1 requests Lambda layer (Phase 1/2/3) ───────────────────
        # All OEM1 admin/proxy Lambdas import `requests` for HTTP calls to the
        # OEM1 feed. `_bundle_oem1_lambda(...)` only stages source files (it
        # does NOT pip-install deps), so each Lambda picks `requests` up via
        # this shared layer instead of inline-bundling per Lambda. Same
        # pip-to-.build pattern as commands_stack.py. Created once at the
        # top of the OEM1 Lambda section and reused by every OEM1 Lambda below.
        _layer_build_dir = os.path.join(
            os.path.dirname(__file__), ".build", "requests_layer", "python"
        )
        os.makedirs(_layer_build_dir, exist_ok=True)
        subprocess.run(
            [sys.executable, "-m", "pip", "install",
             "requests==2.34.2", "-t", _layer_build_dir, "-q", "--upgrade"],
            check=True,
        )
        _requests_layer = lambda_.LayerVersion(
            self, "RequestsLayer",
            code=lambda_.Code.from_asset(
                os.path.join(os.path.dirname(__file__), ".build", "requests_layer")
            ),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            description="requests==2.34.2 for OEM1 Phase 1/2/3 Lambdas",
        )

        proxy_fn = lambda_.Function(self, "VehicleStateProxyFunction",
            function_name=f"cms-{stage}-vehicle-state-proxy",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(_bundle_oem1_lambda("vehicle_state_proxy")),
            role=proxy_role,
            timeout=Duration.seconds(30),
            layers=[_requests_layer],
            environment={
                "OEM1_FEED_HOST": os.environ.get("OEM1_FEED_HOST", "oem1-feed.example.local"),
                "SECRETS_NAME": f"cms-{stage}-connector-oem1-credentials",
                "DEPLOYMENT_STAGE": stage,
            },
        )

        # Admin REST API (IAM auth — no public access)
        admin_api = apigw.RestApi(self, "AdminApi",
            rest_api_name=f"cms-{stage}-admin-api",
            description="CMS admin API — IAM-protected internal endpoints",
            deploy_options=apigw.StageOptions(stage_name=stage),
        )
        admin_resource = admin_api.root.add_resource("admin")
        oem1_resource = admin_resource.add_resource("oem1")
        vehicle_state_resource = oem1_resource.add_resource("vehicle-state")
        vehicle_id_resource = vehicle_state_resource.add_resource("{vehicleId}")
        vehicle_id_resource.add_method(
            "POST",
            apigw.LambdaIntegration(proxy_fn),
            authorization_type=apigw.AuthorizationType.IAM,
        )

        # ── Spec 2026-06-04-cms-ui-vehicle-type-separation: ──────────────────
        # OEM1 admin add-vehicle Lambda + Cognito User Pool authorizer.
        #
        # Reuses C2.2 vehicle_state_proxy shape (TokenSupplier, sanitized error
        # envelope, IAM-scoped Secrets Manager access) and adds:
        #   - DDB write permissions (vehicles + fleet-enrollment tables)
        #   - SSM read on engineering-fleet-ids parameter (R6 fail-open in handler)
        #   - Cognito User Pool authorizer on a NEW /admin/oem1/add-vehicle route
        #
        # R5: the existing /admin/oem1/vehicle-state/{vehicleId} route stays on
        # AuthorizationType.IAM. Only ADDITIVE changes here — the existing route
        # above is untouched. Auth-mode unification is filed as a P3 follow-up
        # row (`AdminApi auth-mode unification`).
        admin_add_vehicle_role = iam.Role(self, "OEM1AdminAddVehicleRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        # Secrets Manager — OEM1 credentials (mirrors C2.2 pattern)
        admin_add_vehicle_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[
                f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:cms-{stage}-connector-oem1-credentials*"
            ],
        ))
        # DynamoDB — vehicle row write (PutItem on insert; UpdateItem on collision)
        admin_add_vehicle_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:PutItem", "dynamodb:UpdateItem"],
            resources=[
                f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-storage-vehicles"
            ],
        ))
        # DynamoDB — fleet-enrollment row write (idempotent PutItem)
        admin_add_vehicle_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:PutItem"],
            resources=[
                f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-storage-fleet-enrollment"
            ],
        ))
        # DynamoDB — fleets table read for M3 data_source check (per spec
        # 2026-06-09-cms-data-source-model-refactor — admin_add_vehicle now
        # validates fleet.data_source via is_cloud_telemetry_fleet helper
        # before allowing OEM1 vehicle enrollment). Without this grant the
        # M3 check raises AccessDeniedException → outer try/except returns
        # 500 → operator sees "Internal server error" instead of the proper
        # 400 "Fleet is not configured for cloud-fed telemetry" rejection.
        # See issue 2026-06-09-admin-add-vehicle-missing-fleets-getitem-iam.
        admin_add_vehicle_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem"],
            resources=[
                f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-storage-fleets"
            ],
        ))
        # SSM — engineering fleet IDs (Lambda fails OPEN on ParameterNotFound per spec R6)
        admin_add_vehicle_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[
                f"arn:aws:ssm:{self.region}:{self.account}:parameter/cms/{stage}/engineering-fleet-ids"
            ],
        ))
        # CloudWatch Logs (mirrors C2.2)
        admin_add_vehicle_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[
                f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/cms-{stage}-oem1-admin-add-vehicle*"
            ],
        ))

        admin_add_vehicle_fn = lambda_.Function(self, "OEM1AdminAddVehicleFunction",
            function_name=f"cms-{stage}-oem1-admin-add-vehicle",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(_bundle_oem1_lambda("admin_add_vehicle")),
            role=admin_add_vehicle_role,
            timeout=Duration.seconds(60),  # 5-page OEM1 pagination + 2 DDB writes
            layers=[_requests_layer],
            environment={
                "OEM1_FEED_HOST": os.environ.get("OEM1_FEED_HOST", "oem1-feed.example.local"),
                "SECRETS_NAME": f"cms-{stage}-connector-oem1-credentials",
                "DEPLOYMENT_STAGE": stage,
                "VEHICLES_TABLE_NAME": f"cms-{stage}-storage-vehicles",
                "FLEET_ENROLLMENT_TABLE_NAME": f"cms-{stage}-storage-fleet-enrollment",
                "ENGINEERING_FLEET_IDS_PARAM": f"/cms/{stage}/engineering-fleet-ids",
            },
        )

        # Cognito User Pool authorizer for the new /admin/oem1/add-vehicle route
        # only. Conditional on CMS_USER_POOL_ID per the bootstrap pattern in
        # data_processing_stack.py (lines 265-290): when the env var is unset
        # (first-time deploy before UI stack publishes the pool), we skip
        # creating the route. The Makefile auto-detects the pool ID after the
        # UI stack lands and re-runs the connector stack with CMS_USER_POOL_ID
        # set, flipping the route on. Existing C2.2 vehicle-state route is
        # unaffected by this gating.
        cms_user_pool_id = os.environ.get("CMS_USER_POOL_ID", "").strip()
        if cms_user_pool_id:
            admin_authorizer = apigw.CognitoUserPoolsAuthorizer(
                self, "AdminApiCognitoAuthorizer",
                cognito_user_pools=[
                    cognito.UserPool.from_user_pool_id(
                        self, "AdminAuthzUserPool", cms_user_pool_id
                    )
                ],
                authorizer_name=f"cms-{stage}-admin-cognito-authorizer",
                identity_source="method.request.header.Authorization",
            )
            add_vehicle_resource = oem1_resource.add_resource("add-vehicle")
            add_vehicle_resource.add_method(
                "POST",
                apigw.LambdaIntegration(admin_add_vehicle_fn),
                authorization_type=apigw.AuthorizationType.COGNITO,
                authorizer=admin_authorizer,
            )

        # ── Phase 3: OEM1 fleet bulk management Lambdas (spec 2026-06-05) ──────
        # The shared `_requests_layer` is created near the top of this method
        # (above the Phase 1 vehicle_state_proxy Lambda) and reused here.

        # Common environment variables shared by all Phase 3 Lambdas (spec C13)
        # Table name resolves at synth time using self.region + self.account.
        _enrollment_requests_table = (
            f"cms-{stage}-storage-oem1-enrollment-requests-{self.region}-{self.account}"
        )
        _oem1_common_env = {
            "OEM1_FEED_HOST": os.environ.get("OEM1_FEED_HOST", "oem1-feed.example.local"),
            "SECRETS_NAME": f"cms-{stage}-connector-oem1-credentials",
            "DEPLOYMENT_STAGE": stage,
            "VEHICLES_TABLE_NAME": f"cms-{stage}-storage-vehicles",
            "FLEET_ENROLLMENT_TABLE_NAME": f"cms-{stage}-storage-fleet-enrollment",
            "ENROLLMENT_REQUESTS_TABLE_NAME": _enrollment_requests_table,
        }

        _oem1_secret_arn = (
            f"arn:aws:secretsmanager:{self.region}:{self.account}"
            f":secret:cms-{stage}-connector-oem1-credentials*"
        )
        _vehicles_table_arn = (
            f"arn:aws:dynamodb:{self.region}:{self.account}"
            f":table/cms-{stage}-storage-vehicles"
        )
        _enrollment_requests_table_arn = (
            f"arn:aws:dynamodb:{self.region}:{self.account}"
            f":table/{_enrollment_requests_table}"
        )
        _fleet_enrollment_table_arn = (
            f"arn:aws:dynamodb:{self.region}:{self.account}"
            f":table/cms-{stage}-storage-fleet-enrollment"
        )

        # ── 2.7 admin_preflight — POST /admin/oem1/preflight ──────────────────
        # Role: cms-{stage}-oem1-preflight-role (R16; len check: staging+ap-ne-1 = 45 ≤ 64)
        _preflight_role = iam.Role(
            self, "OEM1AdminPreflightRole",
            role_name=f"cms-{stage}-oem1-preflight-role-{self.region}",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        _preflight_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[_oem1_secret_arn],
        ))
        _preflight_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/cms-{stage}-oem1-admin-preflight*"],
        ))

        _preflight_fn = lambda_.Function(
            self, "OEM1AdminPreflightFunction",
            function_name=f"cms-{stage}-oem1-admin-preflight",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(_bundle_oem1_lambda("admin_preflight")),
            role=_preflight_role,
            timeout=Duration.seconds(60),
            layers=[_requests_layer],
            environment=_oem1_common_env,
        )

        # ── 2.2 admin_enroll_quota — GET /admin/oem1/enroll-quota ─────────────
        # Role: cms-{stage}-oem1-enroll-quota-role (staging+ap-ne-1 = 50 ≤ 64)
        _enroll_quota_role = iam.Role(
            self, "OEM1AdminEnrollQuotaRole",
            role_name=f"cms-{stage}-oem1-enroll-quota-role-{self.region}",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        _enroll_quota_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[
                _enrollment_requests_table_arn,
                f"{_enrollment_requests_table_arn}/index/*",
            ],
        ))
        _enroll_quota_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-storage-fleet-enrollment/index/vehicleId-index"],
        ))
        _enroll_quota_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/cms-{stage}-oem1-admin-enroll-quota*"],
        ))

        _enroll_quota_fn = lambda_.Function(
            self, "OEM1AdminEnrollQuotaFunction",
            function_name=f"cms-{stage}-oem1-admin-enroll-quota",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(_bundle_oem1_lambda("admin_enroll_quota")),
            role=_enroll_quota_role,
            timeout=Duration.seconds(15),
            environment=_oem1_common_env,
        )

        # ── 2.3 admin_refresh_vehicle_status — POST /admin/oem1/refresh-status ─
        # Role: cms-{stage}-oem1-refresh-status-role (staging+ap-ne-1 = 52 ≤ 64)
        _refresh_status_role = iam.Role(
            self, "OEM1AdminRefreshStatusRole",
            role_name=f"cms-{stage}-oem1-refresh-status-role-{self.region}",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        _refresh_status_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[_oem1_secret_arn],
        ))
        _refresh_status_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:UpdateItem"],
            resources=[_vehicles_table_arn],
        ))
        _refresh_status_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-storage-fleet-enrollment/index/vehicleId-index"],
        ))
        _refresh_status_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/cms-{stage}-oem1-admin-refresh-status*"],
        ))

        _refresh_status_fn = lambda_.Function(
            self, "OEM1AdminRefreshStatusFunction",
            function_name=f"cms-{stage}-oem1-admin-refresh-status",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(_bundle_oem1_lambda("admin_refresh_vehicle_status")),
            role=_refresh_status_role,
            timeout=Duration.seconds(60),
            layers=[_requests_layer],
            environment=_oem1_common_env,
        )

        # ── 2.4 admin_enrollment_poller — EventBridge schedule ────────────────
        # Role: cms-{stage}-oem1-poller-role (abbreviated per R16;
        #       staging+ap-ne-1 = 47 ≤ 64; full "admin-enrollment-poller" > 64)
        _poller_role = iam.Role(
            self, "OEM1AdminEnrollmentPollerRole",
            role_name=f"cms-{stage}-oem1-poller-role-{self.region}",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        _poller_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[_oem1_secret_arn],
        ))
        _poller_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:Scan", "dynamodb:UpdateItem"],
            resources=[_enrollment_requests_table_arn],
        ))
        _poller_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:UpdateItem", "dynamodb:DeleteItem"],
            resources=[_vehicles_table_arn],
        ))
        _poller_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:DeleteItem"],
            resources=[_fleet_enrollment_table_arn],
        ))
        _poller_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["events:PutEvents"],
            resources=["*"],
        ))
        _poller_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["cloudwatch:PutMetricData"],
            resources=["*"],
            conditions={"StringEquals": {"cloudwatch:namespace": "cms/oem1/enrollment_poller"}},
        ))
        _poller_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/cms-{stage}-oem1-admin-poller*"],
        ))

        _poller_fn = lambda_.Function(
            self, "OEM1AdminEnrollmentPollerFunction",
            function_name=f"cms-{stage}-oem1-admin-poller",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(_bundle_oem1_lambda("admin_enrollment_poller")),
            role=_poller_role,
            timeout=Duration.seconds(300),
            reserved_concurrent_executions=1,  # spec 2.4: concurrency=1 (serial, idempotent)
            layers=[_requests_layer],
            environment=_oem1_common_env,
        )

        # ── 2.5 admin_status_sync — EventBridge schedule ──────────────────────
        # Role: cms-{stage}-oem1-status-sync-role (staging+ap-ne-1 = 51 ≤ 64)
        _status_sync_role = iam.Role(
            self, "OEM1AdminStatusSyncRole",
            role_name=f"cms-{stage}-oem1-status-sync-role-{self.region}",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        _status_sync_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[_oem1_secret_arn],
        ))
        _status_sync_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:Scan", "dynamodb:UpdateItem"],
            resources=[_vehicles_table_arn],
        ))
        _status_sync_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["events:PutEvents"],
            resources=["*"],
        ))
        _status_sync_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["cloudwatch:PutMetricData"],
            resources=["*"],
            conditions={"StringEquals": {"cloudwatch:namespace": "cms/oem1/status_sync"}},
        ))
        _status_sync_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/cms-{stage}-oem1-admin-status-sync*"],
        ))

        _status_sync_fn = lambda_.Function(
            self, "OEM1AdminStatusSyncFunction",
            function_name=f"cms-{stage}-oem1-admin-status-sync",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(_bundle_oem1_lambda("admin_status_sync")),
            role=_status_sync_role,
            timeout=Duration.seconds(900),
            layers=[_requests_layer],
            environment=_oem1_common_env,
        )

        # ── EventBridge schedule rules (spec 2.4 + 2.5) ───────────────────────
        # Cadences from cdk.json context (defaults: poller=1min, sync=15min).
        _poller_cadence = self.node.try_get_context("oem1EnrollmentPollerCadenceMinutes") or 1
        _sync_cadence = self.node.try_get_context("oem1StatusSyncCadenceMinutes") or 15

        events.Rule(
            self, "OEM1EnrollmentPollerSchedule",
            rule_name=f"cms-{stage}-oem1-enrollment-poller",
            schedule=events.Schedule.rate(Duration.minutes(int(_poller_cadence))),
            targets=[events_targets.LambdaFunction(_poller_fn)],
        )

        events.Rule(
            self, "OEM1StatusSyncSchedule",
            rule_name=f"cms-{stage}-oem1-status-sync",
            schedule=events.Schedule.rate(Duration.minutes(int(_sync_cadence))),
            targets=[events_targets.LambdaFunction(_status_sync_fn)],
        )

        # ── 4 new API Gateway routes — Cognito User Pool authorizer (decision 006) ─
        # Gated on CMS_USER_POOL_ID (R20): skipped on first-pass deploy;
        # enabled on second-pass after UI stack publishes the pool ID.
        # The existing C2.2 IAM-auth vehicle-state route is NOT modified.
        if cms_user_pool_id:
            # Reuse admin_authorizer already created above for add-vehicle.
            # GET /admin/oem1/enroll-quota → admin_enroll_quota
            enroll_quota_resource = oem1_resource.add_resource("enroll-quota")
            enroll_quota_resource.add_method(
                "GET",
                apigw.LambdaIntegration(_enroll_quota_fn),
                authorization_type=apigw.AuthorizationType.COGNITO,
                authorizer=admin_authorizer,
            )

            # POST /admin/oem1/preflight → admin_preflight
            preflight_resource = oem1_resource.add_resource("preflight")
            preflight_resource.add_method(
                "POST",
                apigw.LambdaIntegration(_preflight_fn),
                authorization_type=apigw.AuthorizationType.COGNITO,
                authorizer=admin_authorizer,
            )

            # POST /admin/oem1/refresh-status → admin_refresh_vehicle_status
            refresh_status_resource = oem1_resource.add_resource("refresh-status")
            refresh_status_resource.add_method(
                "POST",
                apigw.LambdaIntegration(_refresh_status_fn),
                authorization_type=apigw.AuthorizationType.COGNITO,
                authorizer=admin_authorizer,
            )

            # GET /admin/oem1/list-enrolled → admin_list_enrolled (T5.7)
            # Role: cms-{stage}-oem1-list-enrolled-role-{region}
            # staging+ap-northeast-1 = 52 chars ≤ 64 ✓
            _list_enrolled_role = iam.Role(
                self, "OEM1AdminListEnrolledRole",
                role_name=f"cms-{stage}-oem1-list-enrolled-role-{self.region}",
                assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            )
            _list_enrolled_role.add_to_principal_policy(iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[_oem1_secret_arn],
            ))
            # DynamoDB: scan vehicles table to cross-reference CMS rows (read-only)
            _list_enrolled_role.add_to_principal_policy(iam.PolicyStatement(
                actions=["dynamodb:Scan"],
                resources=[_vehicles_table_arn],
            ))
            _list_enrolled_role.add_to_principal_policy(iam.PolicyStatement(
                actions=["dynamodb:Query"],
                resources=[f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-storage-fleet-enrollment/index/vehicleId-index"],
            ))
            # SSM: rate-limit timestamp (C19)
            _list_enrolled_role.add_to_principal_policy(iam.PolicyStatement(
                actions=["ssm:GetParameter", "ssm:PutParameter"],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter/cms/{stage}/connectors/oem1/list-enrolled-last-call/*"
                ],
            ))
            _list_enrolled_role.add_to_principal_policy(iam.PolicyStatement(
                actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[
                    f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/cms-{stage}-oem1-admin-list-enrolled*"
                ],
            ))

            _list_enrolled_fn = lambda_.Function(
                self, "OEM1AdminListEnrolledFunction",
                function_name=f"cms-{stage}-oem1-admin-list-enrolled",
                runtime=lambda_.Runtime.PYTHON_3_12,
                handler="handler.lambda_handler",
                code=lambda_.Code.from_asset(_bundle_oem1_lambda("admin_list_enrolled")),
                role=_list_enrolled_role,
                timeout=Duration.seconds(60),
                layers=[_requests_layer],
                environment={
                    **_oem1_common_env,
                },
            )

            list_enrolled_resource = oem1_resource.add_resource("list-enrolled")
            list_enrolled_resource.add_method(
                "GET",
                apigw.LambdaIntegration(_list_enrolled_fn),
                authorization_type=apigw.AuthorizationType.COGNITO,
                authorizer=admin_authorizer,
            )

            # POST /admin/oem1/command → admin_oem1_command (Ford Pro Command API proxy)
            # Role name: cms-staging-oem1-command-role-us-west-2 = 39 chars ≤ 64 ✓
            _command_role = iam.Role(
                self, "OEM1AdminCommandRole",
                role_name=f"cms-{stage}-oem1-command-role-{self.region}",
                assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            )
            _command_role.add_to_principal_policy(iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[_oem1_secret_arn],
            ))
            _command_role.add_to_principal_policy(iam.PolicyStatement(
                actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                resources=["arn:aws:logs:*:*:*"],
            ))
            _command_fn = lambda_.Function(
                self, "OEM1AdminCommandFunction",
                function_name=f"cms-{stage}-oem1-admin-command",
                runtime=lambda_.Runtime.PYTHON_3_12,
                handler="handler.lambda_handler",
                code=lambda_.Code.from_asset(_bundle_oem1_lambda("admin_oem1_command")),
                role=_command_role,
                timeout=Duration.seconds(30),
                layers=[_requests_layer],
                environment={
                    "SECRETS_NAME": f"cms-{stage}-connector-oem1-credentials",
                    "DEPLOYMENT_STAGE": stage,
                },
            )
            command_resource = oem1_resource.add_resource("command")
            command_resource.add_method(
                "POST",
                apigw.LambdaIntegration(_command_fn),
                authorization_type=apigw.AuthorizationType.COGNITO,
                authorizer=admin_authorizer,
            )

        # ── T3.6: Write-path Lambdas — admin_bulk_enroll + admin_bulk_unenroll ──
        # spec 2026-06-05-cms-oem1-fleet-bulk-management § 2.1 / 2.2 IAM tables
        # Role name length check (C6): cms-staging-oem1-bulk-enroll-role-ap-northeast-1 = 49 chars ≤ 64 ✓
        #                              cms-staging-oem1-bulk-unenroll-role-ap-northeast-1 = 51 chars ≤ 64 ✓

        _fleets_table_arn = (
            f"arn:aws:dynamodb:{self.region}:{self.account}"
            f":table/cms-{stage}-storage-fleets"
        )
        # rev 3.1 decision 014: Query scoped to GSI ARN only (not base table)
        _enrollment_requests_gsi_arn = (
            f"arn:aws:dynamodb:*:*"
            f":table/cms-{stage}-storage-oem1-enrollment-requests-*/index/ClientRequestIdIndex"
        )
        _engineering_fleet_ids_param = (
            f"arn:aws:ssm:{self.region}:{self.account}"
            f":parameter/cms/{stage}/engineering-fleet-ids"
        )

        # ── 2.1 admin_bulk_enroll ─────────────────────────────────────────────
        _bulk_enroll_role = iam.Role(
            self, "OEM1AdminBulkEnrollRole",
            role_name=f"cms-{stage}-oem1-bulk-enroll-role-{self.region}",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        _bulk_enroll_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[_oem1_secret_arn],
        ))
        _bulk_enroll_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:PutItem", "dynamodb:UpdateItem"],
            resources=[_vehicles_table_arn],
        ))
        _bulk_enroll_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:PutItem"],
            resources=[_fleet_enrollment_table_arn, _enrollment_requests_table_arn],
        ))
        _bulk_enroll_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem"],
            resources=[_fleets_table_arn],
        ))
        # rev 3.1 decision 014: Query scoped to GSI ARN (not base table) — least-privilege
        _bulk_enroll_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[_enrollment_requests_gsi_arn],
        ))
        _bulk_enroll_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[_engineering_fleet_ids_param],
        ))
        # logs:* — carries structured CloudWatch audit log (rev 3 C9 pivot)
        _bulk_enroll_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[
                f"arn:aws:logs:{self.region}:{self.account}"
                f":log-group:/aws/lambda/cms-{stage}-oem1-admin-bulk-enroll*"
            ],
        ))

        _bulk_enroll_fn = lambda_.Function(
            self, "OEM1AdminBulkEnrollFunction",
            function_name=f"cms-{stage}-oem1-admin-bulk-enroll",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(_bundle_oem1_lambda("admin_bulk_enroll")),
            role=_bulk_enroll_role,
            timeout=Duration.seconds(300),
            layers=[_requests_layer],
            environment={
                **_oem1_common_env,
                "FLEETS_TABLE_NAME": f"cms-{stage}-storage-fleets",
            },
        )

        # ── 2.2 admin_bulk_unenroll ───────────────────────────────────────────
        _bulk_unenroll_role = iam.Role(
            self, "OEM1AdminBulkUnenrollRole",
            role_name=f"cms-{stage}-oem1-bulk-unenroll-role-{self.region}",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        _bulk_unenroll_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[_oem1_secret_arn],
        ))
        _bulk_unenroll_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:UpdateItem"],
            resources=[_vehicles_table_arn],
        ))
        _bulk_unenroll_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:PutItem"],
            resources=[_enrollment_requests_table_arn],
        ))
        # unenroll needs BatchGetItem on vehicles (spec § 2.2 IAM table)
        _bulk_unenroll_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:BatchGetItem"],
            resources=[_vehicles_table_arn],
        ))
        # rev 3.1 decision 014: Query scoped to GSI ARN (not base table) — least-privilege
        _bulk_unenroll_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[_enrollment_requests_gsi_arn],
        ))
        _bulk_unenroll_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-{stage}-storage-fleet-enrollment/index/vehicleId-index"],
        ))
        # NO events:PutEvents on unenroll (spec § 2.2 IAM table — enroll-only)
        # NO dynamodb:DeleteItem — poller owns deletes (T3.2 constraint)
        _bulk_unenroll_role.add_to_principal_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[
                f"arn:aws:logs:{self.region}:{self.account}"
                f":log-group:/aws/lambda/cms-{stage}-oem1-admin-bulk-unenroll*"
            ],
        ))

        _bulk_unenroll_fn = lambda_.Function(
            self, "OEM1AdminBulkUnenrollFunction",
            function_name=f"cms-{stage}-oem1-admin-bulk-unenroll",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(_bundle_oem1_lambda("admin_bulk_unenroll")),
            role=_bulk_unenroll_role,
            timeout=Duration.seconds(300),
            layers=[_requests_layer],
            environment={
                **_oem1_common_env,
                "FLEETS_TABLE_NAME": f"cms-{stage}-storage-fleets",
            },
        )

        # ── 2 new API Gateway routes for write-path Lambdas (rev 3 A2) ────────
        # Gated on CMS_USER_POOL_ID (R20) — same pattern as T2.6 routes above.
        if cms_user_pool_id:
            # POST /admin/oem1/bulk-enroll → admin_bulk_enroll
            bulk_enroll_resource = oem1_resource.add_resource("bulk-enroll")
            bulk_enroll_resource.add_method(
                "POST",
                apigw.LambdaIntegration(_bulk_enroll_fn),
                authorization_type=apigw.AuthorizationType.COGNITO,
                authorizer=admin_authorizer,
            )

            # POST /admin/oem1/bulk-unenroll → admin_bulk_unenroll
            bulk_unenroll_resource = oem1_resource.add_resource("bulk-unenroll")
            bulk_unenroll_resource.add_method(
                "POST",
                apigw.LambdaIntegration(_bulk_unenroll_fn),
                authorization_type=apigw.AuthorizationType.COGNITO,
                authorizer=admin_authorizer,
            )

        CfnOutput(self, "AdminApiUrl",
            value=admin_api.url,
            description="Admin API base URL (IAM-protected)")

        # ── Outputs ────────────────────────────────────────────────────
        CfnOutput(self, "ServiceName", value=service.service_name)
        CfnOutput(self, "ClusterName", value=cluster.cluster_name)
        CfnOutput(self, "ConnectorType", value=connector_type)
