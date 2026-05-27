"""
Simulation Stack — Lambda API + ECS Fargate workers for cloud simulation.

Architecture:
  - Lambda function handles /api/simulation/* routes via API Gateway
  - ECS Cluster with Fargate for on-demand sim-worker tasks
  - Lambda calls ecs:RunTask to spawn workers, ecs:StopTask to kill them
  - Simulation state tracked in DDB (not in-memory)
"""

import os
from aws_cdk import (
    Stack, Duration, RemovalPolicy, CfnOutput, Size,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecr_assets as ecr_assets,
    aws_iam as iam,
    aws_logs as logs,
    aws_lambda as lambda_,
    aws_apigateway as apigateway,
    aws_dynamodb as dynamodb,
)
from constructs import Construct


class SimulationStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, *,
                 msk_stack=None, ui_stack=None, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        stage = os.environ.get("DEPLOYMENT_STAGE", "dev")
        prefix = f"cms-{stage}"

        # ── VPC — look up MSK VPC by tag to avoid cross-stack export lock ──
        # Using from_lookup with tags instead of passing msk_stack directly
        # prevents CDK from creating implicit CloudFormation cross-stack exports
        vpc = ec2.Vpc.from_lookup(self, "MskVpc",
            tags={"Name": f"cms-{stage}-msk/DataVpc"})

        # ── Security Group for ECS tasks ─────────────────────────────────
        worker_sg = ec2.SecurityGroup(self, "WorkerSG", vpc=vpc, allow_all_outbound=True,
                                      description="Simulation worker tasks")

        # ── ECS Cluster ──────────────────────────────────────────────────
        cluster = ecs.Cluster(self, "SimCluster", vpc=vpc,
                              cluster_name=f"{prefix}-simulation")

        # ── Docker image (built from services/simulation/) ───────────────
        image = ecs.ContainerImage.from_asset(
            os.path.join(os.path.dirname(__file__), "../../services/simulation"),
            file="Dockerfile",
            platform=ecr_assets.Platform.LINUX_ARM64,
        )

        # ── Log group for workers ────────────────────────────────────────
        worker_log_group = logs.LogGroup(self, "WorkerLogs",
            log_group_name=f"/ecs/{prefix}/sim-worker",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY)

        # ── Worker execution role (ECR pull, CW logs) ────────────────────
        exec_role = iam.Role(self, "ExecRole",
            role_name=f"{prefix}-simulation-exec-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"),
            ])

        # ── Worker task role (IoT publish, DDB read) ─────────────────────
        worker_task_role = iam.Role(self, "WorkerTaskRole",
            role_name=f"{prefix}-simulation-worker-task-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"))
        worker_task_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan",
                     "dynamodb:BatchGetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
                     "dynamodb:BatchWriteItem"],
            resources=[f"arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-*"]))
        worker_task_role.add_to_policy(iam.PolicyStatement(
            actions=["iot:Publish", "iot:Connect", "iot:DescribeEndpoint"],
            resources=["*"]))
        worker_task_role.add_to_policy(iam.PolicyStatement(
            actions=[
                # Used by live simulation (route+search) and the historical
                # data injector (CreateMap/RouteCalculator/PlaceIndex on first
                # run; Describe* for idempotency; Search* for address lookup).
                "geo:CalculateRoute",
                "geo:SearchPlaceIndexForPosition",
                "geo:SearchPlaceIndexForText",
                "geo:GetMap*",
                "geo:DescribeMap",
                "geo:DescribeRouteCalculator",
                "geo:DescribePlaceIndex",
                "geo:CreateMap",
                "geo:CreateRouteCalculator",
                "geo:CreatePlaceIndex",
                "geo:ListMaps",
                "geo:ListRouteCalculators",
                "geo:ListPlaceIndexes",
            ],
            resources=[f"arn:aws:geo:{self.region}:{self.account}:*"]))
        worker_task_role.add_to_policy(iam.PolicyStatement(
            actions=["sts:GetCallerIdentity"], resources=["*"]))

        # ── Worker Task Definition ───────────────────────────────────────
        worker_task_def = ecs.FargateTaskDefinition(self, "WorkerTaskDef",
            family=f"{prefix}-sim-worker",
            cpu=512, memory_limit_mib=1024,
            task_role=worker_task_role, execution_role=exec_role)

        worker_task_def.add_container("worker",
            image=image,
            command=["python3", "realtime_telemetry_simulator.py"],
            logging=ecs.LogDrivers.aws_logs(stream_prefix="worker",
                                            log_group=worker_log_group),
            environment={
                "AWS_REGION": self.region,
                "DEPLOYMENT_STAGE": stage,
                "ROUTE_CALCULATOR_NAME": f"{prefix}-ui-route-calculator",
            })

        # ── Simulations DDB table (state tracking) ───────────────────────

        # ── EC2 Capacity Provider for FWE mode (needs NET_ADMIN for vcan) ──
        from aws_cdk import aws_autoscaling as autoscaling

        fwe_user_data = ec2.UserData.for_linux()
        fwe_user_data.add_commands(
            "# Install CAN/vcan kernel modules",
            "dnf install -y kernel-modules-extra 2>/dev/null || yum install -y kernel-modules-extra 2>/dev/null || true",
            "modprobe can 2>/dev/null || true",
            "modprobe can_raw 2>/dev/null || true",
            "modprobe vcan 2>/dev/null || true",
            "for i in $(seq 0 9); do ip link add dev vcan$i type vcan 2>/dev/null || true; ip link set vcan$i up 2>/dev/null || true; done",
            # ── Build + load can-isotp kernel module (CP9 requirement) ──
            # FWE's ISOTPOverCANSenderReceiver calls
            # socket(PF_CAN, SOCK_DGRAM, CAN_ISOTP) which requires the Linux
            # can-isotp kernel module. AL2023 for aarch64 does NOT ship it:
            # - The in-tree /net/can/isotp.c exists in the kernel source
            #   since Linux 5.10, but AL2023 builds with CONFIG_CAN_ISOTP=n
            #   so the module is not compiled into /lib/modules.
            # - The hartkopp out-of-tree driver (upstream of the in-tree
            #   version) has a `#error No need to compile this out-of-tree
            #   driver` guard for kernels ≥5.10 that prevents it from
            #   building, and an API drift on `skb_recv_datagram` that
            #   changed signature between 5.18 and 6.1.
            #
            # Workaround: clone the hartkopp driver, patch out the `#error`
            # guard, fix the `skb_recv_datagram` call to match the 6.1
            # signature (3 args instead of 4), then build + load.
            #
            # Without can-isotp, FWE logs
            #   "Failed to create the ISOTP rx id XXX to IF:vcan0 Error: Protocol not supported"
            # every DTC_QUERY cycle and never dispatches any UDS frames.
            # Our Python responder (uds_dtc_responder.py) doesn't need the
            # module — it uses user-space ISO-TP via python-can + can-isotp
            # pip packages — but FWE does.
            #
            # All failures are non-fatal (`|| true`): the instance still
            # joins the ECS cluster, only UDS-DTC sims break.
            "echo '[can-isotp] Installing build prereqs...'",
            "dnf install -y gcc make git \"kernel-devel-$(uname -r)\" 2>&1 | tail -3 || true",
            "echo '[can-isotp] Cloning hartkopp/can-isotp...'",
            "git clone --depth 1 https://github.com/hartkopp/can-isotp /opt/can-isotp 2>&1 | tail -3 || true",
            # Patch 1: remove the #error guard that refuses to compile on kernels ≥5.10.
            # We WANT to compile it because AL2023 built the kernel without CONFIG_CAN_ISOTP.
            "sed -i 's|^#error No need to compile this out-of-tree driver.*$||' /opt/can-isotp/net/can/isotp.c || true",
            # Patch 2: skb_recv_datagram() lost its `noblock` arg after kernel 5.18.
            # Change the 4-arg call to a 3-arg call.
            "sed -i 's|skb_recv_datagram(sk, flags, noblock, &ret)|skb_recv_datagram(sk, flags, \\&ret)|' /opt/can-isotp/net/can/isotp.c || true",
            "echo '[can-isotp] Building module...'",
            "(cd /opt/can-isotp && make) 2>&1 | tail -5 || true",
            "echo '[can-isotp] Installing into /lib/modules...'",
            "mkdir -p \"/lib/modules/$(uname -r)/extra\" && "
            "cp /opt/can-isotp/net/can/can-isotp.ko \"/lib/modules/$(uname -r)/extra/\" 2>&1 || true",
            "depmod -a || true",
            "echo '[can-isotp] Loading module...'",
            "modprobe can-isotp 2>&1 || true",
            "# Final sanity check — print whether the module loaded",
            "if lsmod | grep -q can_isotp; then "
            "    echo '[can-isotp] ✓ can-isotp kernel module loaded successfully'; "
            "else "
            "    echo '[can-isotp] ✗ can-isotp load FAILED — FWE UDS-DTC simulations will not work on this instance'; "
            "fi",
        )

        fwe_launch_template = ec2.LaunchTemplate(self, "FweLaunchTemplate",
            instance_type=ec2.InstanceType("t4g.small"),
            machine_image=ecs.EcsOptimizedImage.amazon_linux2023(hardware_type=ecs.AmiHardwareType.ARM),
            security_group=worker_sg,
            user_data=fwe_user_data,
            role=iam.Role(self, "FweInstanceRole",
                role_name=f"{prefix}-simulation-fwe-instance-role",
                assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AmazonEC2ContainerServiceforEC2Role"),
                ]),
        )

        fwe_asg = autoscaling.AutoScalingGroup(self, "FweASG",
            vpc=vpc,
            launch_template=fwe_launch_template,
            min_capacity=1,
            max_capacity=3,
            desired_capacity=1,  # Keep 1 warm instance — tasks start in seconds
        )

        capacity_provider = ecs.AsgCapacityProvider(self, "FweCapacityProvider",
            auto_scaling_group=fwe_asg,
            enable_managed_scaling=True,
            enable_managed_termination_protection=False,
        )
        cluster.add_asg_capacity_provider(capacity_provider)

        # FWE log groups
        fwe_log_group = logs.LogGroup(self, "FweLogs",
            log_group_name=f"/ecs/{prefix}/fwe-agent",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY)
        fwe_sim_log_group = logs.LogGroup(self, "FweSimLogs",
            log_group_name=f"/ecs/{prefix}/fwe-simulator",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY)

        # ── FWE Agent Task Definition (long-lived, EC2, HOST network) ─────
        fwe_agent_task_def = ecs.Ec2TaskDefinition(self, "FweAgentTaskDef",
            family=f"{prefix}-fwe-agent",
            network_mode=ecs.NetworkMode.HOST,
            task_role=worker_task_role,
            execution_role=exec_role,
        )

        # FWE agent image — built from services/simulation/Dockerfile.fwe as a CDK container
        # asset. Lands in the CDK bootstrap assets ECR repo keyed by content hash. ARM64 because
        # the FweASG uses t4g.small (Graviton). Previously this was a public.ecr.aws image; we
        # moved it under CDK control so Dockerfile.fwe changes (e.g. adding --with-uds-dtc-example)
        # propagate to the task def atomically on cdk deploy. See docs/FWE_UDS_DTC_BUILD.md.
        fwe_agent_image = ecs.ContainerImage.from_asset(
            os.path.join(os.path.dirname(__file__), "../../services/simulation"),
            file="Dockerfile.fwe",
            platform=ecr_assets.Platform.LINUX_ARM64,
        )

        fwe_agent = fwe_agent_task_def.add_container("fwe-agent",
            image=fwe_agent_image,
            memory_limit_mib=512,
            memory_reservation_mib=256,
            essential=True,
            stop_timeout=Duration.seconds(30),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="fwe", log_group=fwe_log_group),
            entry_point=["sh", "-c"],
            command=[
                "echo 'precedence ::ffff:0:0/96 100' > /etc/gai.conf && "
                "if [ -w /proc/sys/net/ipv6/conf/all/disable_ipv6 ]; then echo 1 > /proc/sys/net/ipv6/conf/all/disable_ipv6; fi && "
                "mkdir -p /etc/aws-iot-fleetwise /var/aws-iot-fleetwise && "
                "rm -rf /var/aws-iot-fleetwise/FWE_Persistency && "
                "echo \"$CERTIFICATE\" > /etc/aws-iot-fleetwise/certificate.pem && "
                "echo \"$PRIVATE_KEY\" > /etc/aws-iot-fleetwise/private-key.key && "
                "/usr/bin/configure-fwe.sh "
                "--input-config-file /usr/share/aws-iot-fleetwise/static-config.json "
                "--output-config-file /etc/aws-iot-fleetwise/config-0.json "
                "--vehicle-name \"$VEHICLE_NAME\" "
                "--endpoint-url \"$ENDPOINT_URL\" "
                "--can-bus0 $CAN_BUS0 "
                "--certificate-file /etc/aws-iot-fleetwise/certificate.pem "
                "--private-key-file /etc/aws-iot-fleetwise/private-key.key "
                "--persistency-path /var/aws-iot-fleetwise/ "
                "--log-level Trace && "
                # Set custom topic prefix for FleetWise topics (CampaignSyncProcessor uses this)
                "jq '.staticConfig.mqttConnection.iotFleetWiseTopicPrefix=\"cms/fleetwise/\"' "
                "/etc/aws-iot-fleetwise/config-0.json > /tmp/config.json && "
                "mv /tmp/config.json /etc/aws-iot-fleetwise/config-0.json && "
                # Set custom commands topic prefix (not using IoT Core commands feature)
                "jq '.staticConfig.mqttConnection.commandsTopicPrefix=\"cms/commands/\"' "
                "/etc/aws-iot-fleetwise/config-0.json > /tmp/config.json && "
                "mv /tmp/config.json /etc/aws-iot-fleetwise/config-0.json && "
                # Redirect jobs/shadow prefixes away from $aws/things (not used, prevents errors)
                "jq '.staticConfig.mqttConnection.jobsTopicPrefix=\"cms/jobs/\" | "
                ".staticConfig.mqttConnection.deviceShadowTopicPrefix=\"cms/shadow/\"' "
                "/etc/aws-iot-fleetwise/config-0.json > /tmp/config.json && "
                "mv /tmp/config.json /etc/aws-iot-fleetwise/config-0.json && "
                # Clean sessions (no persistent session)
                "jq '.staticConfig.mqttConnection.sessionExpiryIntervalSeconds=0' "
                "/etc/aws-iot-fleetwise/config-0.json > /tmp/config.json && "
                "mv /tmp/config.json /etc/aws-iot-fleetwise/config-0.json && "
                # Inject exampleUDSInterface with 9 ECUs (CP6). configure-fwe.sh has
                # a --uds-dtc-example-interface flag that produces only 2 ECUs
                # (ECM + TCM); we need 9 for the demo (one per DTC-carrying ECU
                # grouping in the handoff: BRAKE / ENGINE / POWERTRAIN / PCM /
                # COMM / BATTERY_HV / BATTERY_12V / EVAP / BODY).
                #
                # targetAddress values (0x01..0x09) are integers parseable via
                # std::stoi (FWE's ExampleUDSInterface.cpp uses int matching,
                # not string matching). The `custom_decoding_id` on each CP3
                # CustomDecodingSignal is "ECU1".."ECU9" but that's just a
                # label; DTC_QUERY params[0] carries the integer ECU address
                # emitted by CP8's Lambda.
                #
                # physicalRequestID / physicalResponseID match CP2's
                # uds_dtc_responder.py short-form mapping: OBD-II physical-
                # addressing block 0x7E0..0x7E7 (req) paired with 0x7E8..0x7EF
                # (resp). functionalAddress=0x7DF is the standard OBD-II
                # broadcast ID (not used by our CP4 per-ECU DTC_QUERY but
                # FWE expects the field).
                #
                # CAN_IF is read from the first canInterface config block
                # (same interface our simulator writes to — vcan0 by default).
                "CAN_IF=$(jq -r .networkInterfaces[0].canInterface.interfaceName /etc/aws-iot-fleetwise/config-0.json) && "
                "UDS_CFG=$(jq -n --arg canif \"$CAN_IF\" '"
                "{interfaceId:\"UDS_DTC\",type:\"exampleUDSInterface\","
                "exampleUDSInterface:{configs:["
                "{targetAddress:\"0x01\",name:\"ECU1\",can:{interfaceName:$canif,functionalAddress:\"0x7DF\",physicalRequestID:\"0x7E0\",physicalResponseID:\"0x7E8\"}},"
                "{targetAddress:\"0x02\",name:\"ECU2\",can:{interfaceName:$canif,functionalAddress:\"0x7DF\",physicalRequestID:\"0x7E1\",physicalResponseID:\"0x7E9\"}},"
                "{targetAddress:\"0x03\",name:\"ECU3\",can:{interfaceName:$canif,functionalAddress:\"0x7DF\",physicalRequestID:\"0x7E2\",physicalResponseID:\"0x7EA\"}},"
                "{targetAddress:\"0x04\",name:\"ECU4\",can:{interfaceName:$canif,functionalAddress:\"0x7DF\",physicalRequestID:\"0x7E3\",physicalResponseID:\"0x7EB\"}},"
                "{targetAddress:\"0x05\",name:\"ECU5\",can:{interfaceName:$canif,functionalAddress:\"0x7DF\",physicalRequestID:\"0x7E4\",physicalResponseID:\"0x7EC\"}},"
                "{targetAddress:\"0x06\",name:\"ECU6\",can:{interfaceName:$canif,functionalAddress:\"0x7DF\",physicalRequestID:\"0x7E5\",physicalResponseID:\"0x7ED\"}},"
                "{targetAddress:\"0x07\",name:\"ECU7\",can:{interfaceName:$canif,functionalAddress:\"0x7DF\",physicalRequestID:\"0x7E6\",physicalResponseID:\"0x7EE\"}},"
                "{targetAddress:\"0x08\",name:\"ECU8\",can:{interfaceName:$canif,functionalAddress:\"0x7DF\",physicalRequestID:\"0x7E7\",physicalResponseID:\"0x7EF\"}},"
                "{targetAddress:\"0x09\",name:\"ECU9\",can:{interfaceName:$canif,functionalAddress:\"0x7DF\",physicalRequestID:\"0x18DA09F1\",physicalResponseID:\"0x18DAF109\"}}"
                "]}}') && "
                "jq --argjson uds \"$UDS_CFG\" '.networkInterfaces += [$uds]' "
                "/etc/aws-iot-fleetwise/config-0.json > /tmp/config.json && "
                "mv /tmp/config.json /etc/aws-iot-fleetwise/config-0.json && "
                "if [ \"$CAN_IF\" != \"null\" ]; then "
                "while ! ip link show \"$CAN_IF\" up 2>/dev/null | grep -q UP; do echo \"Waiting for $CAN_IF\"; sleep 3; done; fi && "
                "exec /usr/bin/aws-iot-fleetwise-edge /etc/aws-iot-fleetwise/config-0.json"
            ],
            environment={
                "CAN_BUS0": "vcan0",
            },
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "pgrep -f aws-iot-fleetwise-edge > /dev/null"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(30),
            ),
        )

        # ── FWE Simulator Task Definition (per-trip, EC2, HOST network) ──
        fwe_sim_task_def = ecs.Ec2TaskDefinition(self, "FweSimTaskDef",
            family=f"{prefix}-fwe-simulator",
            network_mode=ecs.NetworkMode.HOST,
            task_role=worker_task_role,
            execution_role=exec_role,
        )

        sim_container = fwe_sim_task_def.add_container("fwe-simulator",
            image=image,
            memory_limit_mib=512,
            memory_reservation_mib=256,
            essential=True,
            logging=ecs.LogDrivers.aws_logs(stream_prefix="sim", log_group=fwe_sim_log_group),
            command=["python3", "realtime_telemetry_simulator.py", "--mode", "can"],
            environment={
                "AWS_REGION": self.region,
                "DEPLOYMENT_STAGE": stage,
                "ROUTE_CALCULATOR_NAME": f"{prefix}-ui-route-calculator",
            },
            linux_parameters=ecs.LinuxParameters(self, "FweSimLinuxParams"),
        )

        # NET_ADMIN for vcan0 creation
        cfn_sim_task = fwe_sim_task_def.node.default_child
        cfn_sim_task.add_property_override(
            "ContainerDefinitions.0.LinuxParameters.Capabilities.Add", ["NET_ADMIN"]
        )

        # Keep old combined task def reference for backward compat (Lambda env var)
        fwe_task_def = fwe_agent_task_def

        # ── Simulations DDB table (state tracking) ───────────────────────
        sim_table = dynamodb.Table(self, "SimulationsTable",
            table_name=f"{prefix}-simulations",
            partition_key=dynamodb.Attribute(name="simulationId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl")

        # ── Lambda function ──────────────────────────────────────────────
        sim_lambda = lambda_.Function(self, "SimulationApi",
            function_name=f"{prefix}-simulation-api",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="simulation_lambda.handler",
            code=lambda_.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "../../services/simulation/lambda")),
            timeout=Duration.seconds(30),
            memory_size=256,
            environment={
                "ECS_CLUSTER": cluster.cluster_name,
                "WORKER_TASK_DEF": worker_task_def.task_definition_arn,
                "FWE_TASK_DEF": fwe_agent_task_def.task_definition_arn,
                "FWE_SIM_TASK_DEF": fwe_sim_task_def.task_definition_arn,
                "FWE_CAPACITY_PROVIDER": capacity_provider.capacity_provider_name,
                "WORKER_SUBNETS": ",".join([s.subnet_id for s in vpc.private_subnets]),
                "WORKER_SECURITY_GROUP": worker_sg.security_group_id,
                "SIMULATIONS_TABLE": sim_table.table_name,
                "DEPLOYMENT_STAGE": stage,
                "AWS_REGION_NAME": self.region,
            })

        # Lambda permissions
        sim_table.grant_read_write_data(sim_lambda)
        sim_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["ecs:RunTask", "ecs:StopTask", "ecs:DescribeTasks", "ecs:TagResource"],
            resources=[
                worker_task_def.task_definition_arn,
                f"arn:aws:ecs:{self.region}:{self.account}:task-definition/{prefix}-fwe-agent:*",
                f"arn:aws:ecs:{self.region}:{self.account}:task-definition/{prefix}-fwe-simulator:*",
                f"arn:aws:ecs:{self.region}:{self.account}:task-definition/{prefix}-fwe-vehicle:*",
                f"arn:aws:ecs:{self.region}:{self.account}:task/{prefix}-simulation/*",
            ]))
        sim_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["ecs:ListTasks"],
            resources=["*"]))
        sim_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["ecs:ListContainerInstances", "ecs:DescribeContainerInstances", "ecs:DescribeClusters"],
            resources=["*"]))
        sim_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["autoscaling:DescribeAutoScalingGroups", "autoscaling:SetDesiredCapacity"],
            resources=["*"]))
        sim_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["ec2:RebootInstances", "ec2:DescribeInstances"],
            resources=["*"]))
        sim_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["iot:DescribeEndpoint"],
            resources=["*"]))
        sim_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["iam:PassRole"],
            resources=[worker_task_role.role_arn, exec_role.role_arn]))
        sim_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:UpdateItem", "dynamodb:PutItem"],
            resources=[
                f"arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-storage-*",
                f"arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-campaigns",
                f"arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-campaigns/index/*",
                # CP8: read event_catalog to look up dtc_code per maintenance scenario
                f"arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-event-catalog",
            ]))
        sim_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["iot:DescribeEndpoint"], resources=["*"]))
        sim_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["logs:GetLogEvents", "logs:FilterLogEvents"],
            resources=[
                worker_log_group.log_group_arn + ":*",
                fwe_log_group.log_group_arn + ":*",
                fwe_sim_log_group.log_group_arn + ":*",
            ]))

        # ── API Gateway ──────────────────────────────────────────────────
        sim_api = apigateway.RestApi(self, "SimulationAPI",
            rest_api_name=f"{prefix}-simulation-api",
            description="CMS Simulation Service API",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=apigateway.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "Authorization"],
            ))

        integration = apigateway.LambdaIntegration(sim_lambda)

        # /api/simulation/*
        api_res = sim_api.root.add_resource("api")
        sim_res = api_res.add_resource("simulation")
        sim_res.add_method("GET", integration)  # list / health

        start = sim_res.add_resource("start")
        start.add_method("POST", integration)

        status = sim_res.add_resource("status")
        status_id = status.add_resource("{simulationId}")
        status_id.add_method("GET", integration)

        stop = sim_res.add_resource("stop")
        stop_id = stop.add_resource("{simulationId}")
        stop_id.add_method("POST", integration)

        list_res = sim_res.add_resource("list")
        list_res.add_method("GET", integration)

        health = sim_res.add_resource("health")
        health.add_method("GET", integration)

        drivers = sim_res.add_resource("drivers")
        drivers.add_method("GET", integration)

        presets = sim_res.add_resource("presets")
        presets.add_method("GET", integration)

        campaigns = sim_res.add_resource("campaigns")
        campaigns.add_method("GET", integration)

        discover = sim_res.add_resource("discover-iot-endpoint")
        discover.add_method("GET", integration)

        # /api/simulation/agent/*
        agent_res = sim_res.add_resource("agent")
        agent_start = agent_res.add_resource("start")
        agent_start.add_method("POST", integration)
        agent_stop = agent_res.add_resource("stop")
        agent_stop.add_method("POST", integration)
        agent_status = agent_res.add_resource("status")
        agent_status.add_method("GET", integration)
        agent_logs = agent_res.add_resource("logs")
        agent_logs_vin = agent_logs.add_resource("{vin}")
        agent_logs_vin.add_method("GET", integration)

        # ── Outputs ──────────────────────────────────────────────────────
        CfnOutput(self, "SimulationApiUrl", value=sim_api.url,
                  description="Simulation API endpoint - add to runtimeConfig.json as simulationApiEndpoint")
        CfnOutput(self, "ClusterName", value=cluster.cluster_name)
        CfnOutput(self, "WorkerTaskDefArn", value=worker_task_def.task_definition_arn)
        CfnOutput(self, "FweTaskDefArn", value=fwe_task_def.task_definition_arn)
        CfnOutput(self, "SimulationsTableName", value=sim_table.table_name)
