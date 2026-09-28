"""
Simulation Stack — Lambda API + ECS Fargate workers for cloud simulation.

Architecture:
  - Lambda function handles /api/simulation/* routes via API Gateway
  - ECS Cluster with Fargate for on-demand sim-worker tasks
  - Lambda calls ecs:RunTask to spawn workers, ecs:StopTask to kill them
  - Simulation state tracked in DDB (not in-memory)
"""

import os
import shutil
from aws_cdk import (
    Stack, Duration, RemovalPolicy, CfnOutput, Size, Fn,
    aws_cognito as cognito,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecr_assets as ecr_assets,
    aws_iam as iam,
    aws_kms as kms,
    aws_logs as logs,
    aws_lambda as lambda_,
    aws_apigateway as apigateway,
    aws_dynamodb as dynamodb,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_events as events,
    aws_events_targets as events_targets,
    aws_sns as sns,
)
from constructs import Construct

from stacks._sim_image_config import (
    FWE_AGENT_IMAGE_NAME,
    SIM_IMAGE_MODE_ASSET,
    SIM_SERVICE_IMAGE_NAME,
    assert_published_image_fresh,
    get_sim_image_mode,
    resolve_sim_image_ref,
)


def _resolve_sim_container_image(image_name: str, dockerfile: str) -> ecs.ContainerImage:
    """Resolve a simulation container image per ``SIM_IMAGE_MODE``.

    ``published`` (default): pull the prebuilt AWS Solutions public-ECR image
    via ``ecs.ContainerImage.from_registry`` — no local container builder
    required (fresh-customer / release path).

    ``asset``: build locally from the staged build context (``_stage_sim_service_context``)
    via ``from_asset`` (Option A dev inner-loop escape; requires a builder + ``CDK_DOCKER``).
    The staged context includes the ``_shared/`` overlay so the image ships with
    ``_shared.routine_catalog`` importable.

    ARM64 is preserved on both paths (Graviton task runtime; the published
    images are arm64). Registry/tag resolution + the hard-fail-on-empty guard
    live in ``stacks._sim_image_config.resolve_sim_image_ref``.
    """
    if get_sim_image_mode(os.environ, image_name) == SIM_IMAGE_MODE_ASSET:
        # Use the staged build context so _shared/ is available inside the image.
        # The staged dir is a sibling of the Dockerfile, so file=dockerfile still
        # resolves correctly relative to the build context root.
        return ecs.ContainerImage.from_asset(
            _sim_service_context_path,
            file=dockerfile,
            platform=ecr_assets.Platform.LINUX_ARM64,
        )
    # Guard 2: refuse to reference a pinned image that predates the simulation
    # source in this checkout. Only consulted on the published branch — asset
    # builds are by definition current. See
    # issues/2026-08-19-cms-vehicle-ecu-presence-not-resident/.
    assert_published_image_fresh(image_name, os.environ)
    return ecs.ContainerImage.from_registry(
        resolve_sim_image_ref(image_name, os.environ)
    )


# ── Simulation Lambda asset bundling ──────────────────────────────────────────
# The simulation Lambda lives in `services/simulation/lambda/` but depends on
# `_lib/fleet_membership.py` from the connector package. A naive
# `Code.from_asset("../../services/simulation/lambda")` would miss `_lib/`,
# causing a cold-start `ModuleNotFoundError`.
#
# This helper stages `services/simulation/lambda/` into a clean build dir and
# overlays `services/connectors/oem1/_lib/` at `_lib/`, mirroring the
# `_bundle_oem1_lambda()` pattern in `connector_stack.py:66-101`.
# Source-of-truth for `_lib/` remains the connector location — do NOT copy
# `_lib/` into `services/simulation/lambda/` directly.
_SIM_LAMBDA_SRC_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "services", "simulation", "lambda",
))
_CONNECTOR_LIB_SRC_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "services", "connectors", "oem1", "_lib",
))
# Source-of-truth for _shared/ is services/_shared/ — do NOT copy files out of it.
# See services/_shared/__init__.py for the packaging rationale (F28 root cause).
_SHARED_SRC_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "services", "_shared",
))
_SIM_LAMBDA_BUILD_DIR = os.path.join(
    os.path.dirname(__file__), ".build", "sim_lambda",
)

# ── Simulation service image build context ────────────────────────────────────
# The sidecar image (cms-sim-service) runs realtime_telemetry_simulator.py and
# must import `_shared.routine_catalog` for the F2.3 fail-closed safety_class check.
# `_shared/` lives at `services/_shared/`, outside the Docker build context
# (`services/simulation/`), so it must be copied into a staged build context at
# synth time — the same .build/ mechanism used for Lambda bundles.
#
# In `published` mode (default), the image is pulled from public ECR, so the
# staged context is only used in `asset` mode (local dev / CI). The assertion
# below verifies the staged dir regardless of mode, so the test gates both paths.
_SIM_SERVICE_SRC_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "services", "simulation",
))
_SIM_SERVICE_BUILD_DIR = os.path.join(
    os.path.dirname(__file__), ".build", "sim_service",
)


def _stage_sim_service_context() -> str:
    """Stage the sim-service Docker build context with the ``_shared/`` overlay.

    Copies ``services/simulation/`` into the build dir and overlays
    ``services/_shared/`` at ``_shared/``. Re-stages on every synth so source
    edits are picked up. Returns the absolute path to the staged build context.

    This is the Docker equivalent of ``_bundle_commands_lambda()`` for Lambdas.
    In ``published`` mode the staged context is not handed to Docker, but the
    assertion in ``TestSharedModuleBundled`` verifies the dir regardless — an
    import that works locally proves nothing about what ships.

    Raises ``FileNotFoundError`` if the ``_shared/`` source directory is missing,
    matching the Lambda bundler's behaviour.

    Source-of-truth discipline (F28 lesson):
    - ``_shared/`` → services/_shared/ — do NOT copy modules into services/simulation/.
    A duplicated safety table is the drift that caused F27.
    """
    if not os.path.isdir(_SHARED_SRC_DIR):
        raise FileNotFoundError(
            f"Sim service _shared source dir not found: {_SHARED_SRC_DIR}. "
            "services/_shared/ is source-of-truth; do NOT copy modules out of it."
        )
    dst = _SIM_SERVICE_BUILD_DIR
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(
        _SIM_SERVICE_SRC_DIR,
        dst,
        ignore=shutil.ignore_patterns(
            "__pycache__", "tests", "*.pyc", ".pytest_cache", ".build",
        ),
    )

    # Overlay _shared/ — source-of-truth for runtime safety modules (F28).
    # The Dockerfile COPYs it into /app/_shared/ so `import _shared.routine_catalog`
    # resolves from /app/ (the WORKDIR).
    shutil.copytree(
        _SHARED_SRC_DIR,
        os.path.join(dst, "_shared"),
        ignore=shutil.ignore_patterns("__pycache__", "tests", "*.pyc", ".pytest_cache"),
    )

    return os.path.abspath(dst)


# Stage the sim-service Docker build context at module import time so that:
# 1. The staged context exists when _resolve_sim_container_image is called.
# 2. test_simulation_stack_sovd.py can assert presence of _shared/routine_catalog.py
#    at synth time (the template fixture imports this module, triggering the call).
# This is the same eager-staging pattern as _bundle_commands_lambda() being called
# in CommandsStack.__init__(), but at module scope so _resolve_sim_container_image
# can reference it as a module-level constant rather than repeating the staging.
_sim_service_context_path: str = _stage_sim_service_context()


def _bundle_sim_lambda() -> str:
    """Stage the simulation Lambda asset with the ``_lib/`` and ``_shared/`` overlays.

    Copies ``services/simulation/lambda/`` into the build dir and overlays
    ``services/connectors/oem1/_lib/`` at ``_lib/``, and ``services/_shared/``
    at ``_shared/``. Re-stages on every synth so source edits are picked up.
    Returns the absolute path to the staged asset directory.

    Raises ``FileNotFoundError`` if either overlay source directory is missing.

    Source-of-truth discipline (F28 lesson):
    - ``_lib/``    → services/connectors/oem1/_lib/ — do NOT copy into services/simulation/lambda/
    - ``_shared/`` → services/_shared/              — do NOT copy into services/simulation/lambda/
    A duplicated safety table (e.g. routine_catalog.py) is the drift that caused F27.
    """
    if not os.path.isdir(_CONNECTOR_LIB_SRC_DIR):
        raise FileNotFoundError(
            f"Sim Lambda _lib source dir not found: {_CONNECTOR_LIB_SRC_DIR}. "
            "The connector _lib is source-of-truth; do NOT copy it into services/simulation/lambda/."
        )
    if not os.path.isdir(_SHARED_SRC_DIR):
        raise FileNotFoundError(
            f"Sim Lambda _shared source dir not found: {_SHARED_SRC_DIR}. "
            "services/_shared/ is source-of-truth; do NOT copy modules out of it."
        )
    dst = _SIM_LAMBDA_BUILD_DIR
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(
        _SIM_LAMBDA_SRC_DIR,
        dst,
        ignore=shutil.ignore_patterns(
            "__pycache__", "tests", "*.pyc", ".pytest_cache",
        ),
    )
    shutil.copytree(
        _CONNECTOR_LIB_SRC_DIR,
        os.path.join(dst, "_lib"),
        ignore=shutil.ignore_patterns("__pycache__", "tests", "*.pyc", ".pytest_cache"),
    )

    # Overlay _shared/ from services/_shared/ — source-of-truth for runtime modules
    # shared across more than one deployment unit (F28: routine_catalog must ship in
    # the sim Lambda bundle, not only in deployment/scripts/).
    # imported as `_shared.routine_catalog` by sidecar imports that are exercised
    # via the Lambda (F2.2/F2.3).
    shutil.copytree(
        _SHARED_SRC_DIR,
        os.path.join(dst, "_shared"),
        ignore=shutil.ignore_patterns("__pycache__", "tests", "*.pyc", ".pytest_cache"),
    )

    return os.path.abspath(dst)


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

        # ── Container image: published public-ECR by default; asset for dev ──
        image = _resolve_sim_container_image(SIM_SERVICE_IMAGE_NAME, "Dockerfile")

        # ── Log group for workers ────────────────────────────────────────
        worker_log_group = logs.LogGroup(self, "WorkerLogs",
            log_group_name=f"/ecs/{prefix}/sim-worker",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY)

        # ── Worker execution role (ECR pull, CW logs) ────────────────────
        exec_role = iam.Role(self, "ExecRole",
            role_name=f"{prefix}-simulation-exec-role-{self.region}",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"),
            ])

        # ── Worker task role (IoT publish, DDB read) ─────────────────────
        worker_task_role = iam.Role(self, "WorkerTaskRole",
            role_name=f"{prefix}-simulation-worker-task-role-{self.region}",
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

        # ── SOVD S3 write grant (spec 2026-09-01-cms-remote-diagnostics-sovd § Interfaces)
        # The sidecar's _handle_sovd uploads oversized payloads (> 80 KB) to the SOVD
        # responses bucket via task-role credentials. Without this grant, boto3 raises
        # AccessDenied and the sidecar falls back to inline MQTT — which then exceeds the
        # MQTT payload limit for large full-scan responses.
        #
        # ARN is constructed by string pattern (same as commands_stack.py) to avoid a
        # cross-stack reference that would risk a cyclic dependency between SimulationStack
        # and CommandsStack. The bucket may not exist at simulation stack deploy time if
        # CommandsStack hasn't been deployed yet — that's fine; the grant is definitional
        # and s3:PutObject on a non-existent ARN is a no-op until the bucket is created.
        #
        # Path-restriction: the spec prefers ${aws:PrincipalTag/VIN}/* scoping. This stack
        # does NOT tag ECS tasks with a VIN attribute (tasks are shared-pool workers; VIN
        # is only known at request dispatch time, not at task launch). Therefore the grant
        # scopes to the full bucket (prefix/*). The security control remains HARD GATE 1
        # (task role, no long-lived access keys) — the task role is the boundary; without
        # it the sidecar cannot reach S3 at all. VIN-scoped path restriction is recorded
        # as a follow-on improvement; see decisions.md § 2026-09-01 Task 5.2.
        _sovd_responses_bucket_name = (
            f"{prefix}-storage-sovd-responses-{self.region}-{self.account}"
        )
        worker_task_role.add_to_policy(iam.PolicyStatement(
            actions=["s3:PutObject"],
            resources=[
                f"arn:aws:s3:::{_sovd_responses_bucket_name}/*",
            ]))

        # ── Worker Task Definition ───────────────────────────────────────
        worker_task_def = ecs.FargateTaskDefinition(self, "WorkerTaskDef",
            family=f"{prefix}-sim-worker",
            cpu=512, memory_limit_mib=1024,
            task_role=worker_task_role, execution_role=exec_role,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ))

        worker_task_def.add_container("worker",
            image=image,
            command=["python3", "realtime_telemetry_simulator.py"],
            logging=ecs.LogDrivers.aws_logs(stream_prefix="worker",
                                            log_group=worker_log_group),
            environment={
                "AWS_REGION": self.region,
                "DEPLOYMENT_STAGE": stage,
                "ROUTE_CALCULATOR_NAME": f"{prefix}-ui-route-calculator",
                # SOVD: bucket name so the sidecar can construct the S3 key at runtime.
                # The sidecar uses task-role credentials for PutObject (no access keys).
                "SOVD_RESPONSES_BUCKET": _sovd_responses_bucket_name,
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
            # Patch 3: newer AL2023 6.1 kernels (seen on 6.1.186-228.374) dropped `skbcnt`
            # from struct can_skb_priv, so the driver's one assignment to it no longer
            # compiles and the module is never built. Delete that assignment only when this
            # kernel's header lacks the field. Issue
            # 2026-09-26-fwe-host-can-isotp-build-fails-uds-dtc-injection-dead.
            "grep -q skbcnt \"/usr/src/kernels/$(uname -r)/include/linux/can/skb.h\" || "
            "sed -i '/can_skb_prv(skb)->skbcnt = 0;/d' /opt/can-isotp/net/can/isotp.c || true",
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
            # ── Reboot-persistence (issue 2026-06-19-cms-prod-fwe-host-vcan-missing) ──
            # cloud-init user-data is one-shot (first boot only). Without this, an
            # in-place reboot of the FWE host loses vcan0-9 and FWE hangs at
            # "waiting for vcan0". Install a modules-load.d drop-in (loads the CAN
            # modules on every boot) + a systemd oneshot unit (recreates vcan0-9 on
            # every boot). The can-isotp .ko built above persists in
            # /lib/modules/.../extra, so modules-load.d can reload it after reboot.
            "printf 'can\\ncan_raw\\nvcan\\ncan-isotp\\n' > /etc/modules-load.d/cms-can.conf",
            "printf '%s\\n' "
            "'[Unit]' "
            "'Description=CMS create vcan0-9 virtual CAN interfaces' "
            "'After=systemd-modules-load.service' "
            "'Wants=systemd-modules-load.service' "
            "'[Service]' "
            "'Type=oneshot' "
            "'RemainAfterExit=yes' "
            "'ExecStart=/usr/bin/sh -c \"for i in 0 1 2 3 4 5 6 7 8 9; do ip link add dev vcan$i type vcan 2>/dev/null || true; ip link set vcan$i up 2>/dev/null || true; done\"' "
            "'[Install]' "
            "'WantedBy=multi-user.target' "
            "> /etc/systemd/system/cms-vcan.service",
            "systemctl daemon-reload 2>/dev/null || true",
            "systemctl enable --now cms-vcan.service 2>/dev/null || true",
        )

        # Instance sizing — issue 2026-08-04-fwe-remote-commands-not-actuating § D5 +
        # spec 2026-08-04-cms-vehicle-trip-lifecycle-split § Capacity:
        #
        # The t4g.small (1846 MB) wedged with ~1.2 GB unreclaimable kernel slab (CAN/vcan
        # caches from 47 days of accumulated leaked interfaces), leaving only 89 MB
        # available. Container RSS was trivial (~200 MB total for 3 agents + ecs-agent).
        #
        # This spec adds a vehicle-ecu sidecar per agent, so the target headcount is
        # N agents + N sidecars.  Observed RSS: fwe-agent 27–145 MB; sidecar (Python
        # presence loop, unmeasured) estimated ~128 MB conservatively.  Per-vehicle pair
        # soft reservation = 256 MB; hard limit = 512 MB.
        #
        # t4g.medium: 2 vCPU, ~3686 MB OS-reported (EC2 reserves ~410 MB from the
        #   4096 MB nominal for EFI, ACPI tables, and DMA; t4g.small reports 1846 MB
        #   out of 2048 nominal — same ~90% ratio extrapolated to medium)
        #   3 vehicle pairs × 256 MB reservation   =   768 MB
        #   ecs-agent                               ≈    22 MB
        #   kernel baseline (non-slab)              ≈   300 MB
        #   headroom for slab accumulation          ≈  2596 MB  (> 2× the observed 1.2 GB)
        #
        # t4g.small (1846 MB) is too small even before sidecars once slab starts growing;
        # t4g.large (8 GB) over-provisions for 3 staging vehicles — medium is the fit.
        fwe_launch_template = ec2.LaunchTemplate(self, "FweLaunchTemplate",
            instance_type=ec2.InstanceType("t4g.medium"),
            machine_image=ecs.EcsOptimizedImage.amazon_linux2023(hardware_type=ecs.AmiHardwareType.ARM),
            security_group=worker_sg,
            user_data=fwe_user_data,
            role=iam.Role(self, "FweInstanceRole",
                role_name=f"{prefix}-simulation-fwe-instance-role-{self.region}",
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
            # desired_capacity is intentionally omitted: setting it causes CloudFormation
            # to reset the ASG to 1 on every `cdk deploy cms-staging-simulation`, bouncing
            # any running FWE agent tasks and silently killing telemetry. Managed scaling
            # (AsgCapacityProvider) owns the live desired count; min_capacity=1 keeps a
            # warm instance without resetting it. See spec
            # 2026-06-18-cms-fwe-decoder-manifest-bucket-resolution WS3.
            #
            # Host recycling — issue 2026-08-04-fwe-remote-commands-not-actuating § D5:
            # The staging host accumulated ~1.2 GB of unreclaimable kernel slab over 47 days
            # (CAN/vcan slab caches, not container memory) and wedged with only 89 MB
            # available. No code in this stack recycles the host autonomously, so it can
            # drift indefinitely. MaxInstanceLifetime forces a drain-and-replace cycle so
            # the kernel footprint cannot accumulate past this bound. 7 days is short enough
            # to self-heal weekly while being long enough that the AsgCapacityProvider can
            # drain and replace without disrupting active agent tasks.
            max_instance_lifetime=Duration.days(7),
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

        # FWE agent image. DEFAULT (SIM_IMAGE_MODE=published): pulled from the
        # AWS Solutions public ECR registry (cms-fwe-agent) — no local builder.
        # SIM_IMAGE_MODE=asset: built from services/simulation/Dockerfile.fwe as a
        # CDK container asset so Dockerfile.fwe changes (e.g. --with-uds-dtc-example)
        # propagate to the task def atomically on cdk deploy. ARM64 on both paths
        # (FweASG uses t4g.medium / Graviton). See docs/FWE_UDS_DTC_BUILD.md.
        fwe_agent_image = _resolve_sim_container_image(
            FWE_AGENT_IMAGE_NAME, "Dockerfile.fwe"
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

        # ── vehicle-ecu sidecar — presence loop on the fwe-agent task ──────
        #
        # Spec: 2026-08-04-cms-vehicle-trip-lifecycle-split § Decision alternative E.
        # Goal: "if the agent is running, the vehicle is commandable" — 1:1
        # lifecycle by construction.  The sidecar runs the PresenceLoop
        # (realtime_telemetry_simulator.py --mode can --skip-mqtt --commands-mqtt
        # --trips 0) on the same task, sharing HOST networking and the same
        # $CAN_BUS0 vcan interface allocated to the agent.
        #
        # ── essential= choice: False ──────────────────────────────────────
        # A presence crash must NOT take down real telemetry decode.  A crash
        # that kills the fwe-agent binary would be worse than a missed command
        # subscription — the agent's FleetWise telemetry pipeline is the
        # primary reason the task exists.  Setting essential=False prevents
        # ECS from propagating an ECU container exit to a task stop.
        #
        # 1:1 lifecycle is preserved from the other direction: fwe-agent is
        # essential=True, so when the agent exits (graceful or not), the task
        # stops and the sidecar exits with it.  The invariant is asymmetric
        # by design: agent failure is always terminal; ECU failure is
        # recoverable via the restart policy below.
        #
        # The sidecar being non-essential is NOT invisible: enable_restart_policy=True
        # causes ECS to restart the sidecar on crash.  Repeated restart failures
        # are surfaced by the AgentCounterErrorsAlarm (the agent-counter Lambda
        # tracks task-definition health).  For persistent sidecar death without
        # agent impact, add a dedicated CloudWatch alarm on the vehicle-ecu
        # container's exit count in a follow-on spec (P3).
        #
        # ── Credentials — HARD GATE 1 ────────────────────────────────────
        # PRIVATE_KEY and CERTIFICATE are NOT duplicated into this container.
        # They are already passed as plaintext task overrides on the fwe-agent
        # container (readable via ecs:DescribeTasks — pre-existing exposure per
        # issues/2026-08-04-iot-private-keys-in-ecs-task-overrides/) and the
        # security review made non-duplication a hard gate.
        #
        # The presence loop obtains device credentials via the ECS task IAM
        # role: get_vehicle_certificate() in realtime_telemetry_simulator.py
        # calls dynamodb:Scan on cms-{stage}-storage-vehicle-certificates (already
        # in worker_task_role's {prefix}-* DynamoDB grant), plus iot:DescribeEndpoint
        # (already granted).  No new credential surface.
        #
        # ── IAM — HARD GATE 3 ────────────────────────────────────────────
        # ECS reads for vcan_teardown.get_claimed_tasks() follow the
        # agent_counter_lambda:657-660 pattern: ecs:ListTasks + ecs:DescribeTasks
        # on "*" with an ecs:cluster ArnEquals condition.
        #
        # IMPORTANT: ECS EC2 tasks do NOT support per-container IAM roles
        # (that is a Fargate-only feature via the task metadata credential
        # endpoint per container).  All containers in this task share
        # worker_task_role.  The narrower per-container role noted in the task
        # constraints is architecturally unachievable on EC2/HOST.  The existing
        # {prefix}-* DynamoDB wildcard on worker_task_role covers ~15 tables;
        # the tighter ecs:cluster condition on the ECS read grant below is the
        # best achievable scope reduction here.  A follow-on spec can migrate
        # to Fargate (which supports per-container roles) if the blast radius
        # is deemed unacceptable in a security review.
        #
        # ── vcan teardown — HARD GATE 2 ──────────────────────────────────
        # The sidecar does NOT call teardown_vcan from its own stop hook.
        # get_claimed_tasks refuses while the calling task is still RUNNING,
        # so teardown must be driven by an external caller on task-STOPPED.
        # That caller (drain_stale_fwe_agents.sh or an EventBridge rule on
        # ECS task state-change) must pass _metadata_cluster="cms-{stage}-simulation"
        # (non-empty) to preserve the cluster cross-check introduced by Fix
        # Group 3.  Never pass '' — that disables the guard and reinstates the
        # wrong-cluster fail-open.
        #
        # ── Restart policy ───────────────────────────────────────────────
        # restart_ignored_exit_codes=[0]: a graceful shutdown (SIGTERM from ECS
        # on task-STOPPED) exits 0 and must not trigger a restart loop.
        # restart_attempt_period=5min: ECS counts exits within this window to
        # detect a crash loop and back off automatically.
        #
        # ── Startup dependency ────────────────────────────────────────────
        # No add_container_dependencies call: both containers can start
        # concurrently.  The presence loop waits for the vcan interface to be
        # UP (same pattern as the fwe-agent command in the startup script),
        # so it is naturally self-synchronising on CAN_BUS0 availability.

        # Log group for the vehicle-ecu sidecar
        vehicle_ecu_log_group = logs.LogGroup(self, "VehicleEcuLogs",
            log_group_name=f"/ecs/{prefix}/vehicle-ecu",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY)

        # vehicle-ecu sidecar image — same sim-service image as the fwe-simulator
        # container (cms-sim-service).  The presence loop lives in
        # realtime_telemetry_simulator.py which ships in that image; the
        # fwe-agent image (cms-fwe-agent) carries only the FWE binary.
        vehicle_ecu_image = _resolve_sim_container_image(
            SIM_SERVICE_IMAGE_NAME, "Dockerfile"
        )

        fwe_agent_task_def.add_container("vehicle-ecu",
            image=vehicle_ecu_image,
            # essential=False: ECU crash must not kill fwe-agent telemetry
            # decode.  See the reasoning block above.
            essential=False,
            memory_reservation_mib=128,
            memory_limit_mib=256,
            # Restart the sidecar on crash without cycling the task.
            # Clean exit (exitCode 0, e.g. SIGTERM) is not retried.
            enable_restart_policy=True,
            restart_ignored_exit_codes=[0],
            restart_attempt_period=Duration.minutes(5),
            stop_timeout=Duration.seconds(30),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="vehicle-ecu",
                log_group=vehicle_ecu_log_group,
            ),
            # --mode can: presence loop emits CAN frames onto $CAN_BUS0 and
            #   handles command subscriptions; CAN/vcan is shared with the
            #   fwe-agent container via HOST networking.
            # --skip-mqtt: no telemetry MQTT publishes; FWE agent handles
            #   telemetry ingest via the FleetWise pipeline.
            # --commands-mqtt: establishes the command subscription even with
            #   --skip-mqtt set (applies_command → mutates VehicleState →
            #   idle_emit picks up the change on the next CAN tick).
            # --trips 0: run only the presence/idle loop; do not start any
            #   bounded trip phase.  Trips are driven by the fwe-simulator
            #   task via the DDB control channel.
            # --vehicles 1: single-vehicle mode; VEHICLE_NAME is set via
            #   container override at run_task time (injected by
            #   simulation_lambda._start alongside the fwe-agent override).
            command=[
                "python3", "realtime_telemetry_simulator.py",
                "--mode", "can",
                "--skip-mqtt",
                "--commands-mqtt",
                "--trips", "0",
                "--vehicles", "1",
            ],
            environment={
                "AWS_REGION": self.region,
                "DEPLOYMENT_STAGE": stage,
                # ECS_CLUSTER is consumed by vcan_teardown.get_claimed_tasks
                # (called from external teardown callers, not from within this
                # sidecar — HARD GATE 2 forbids in-sidecar teardown).  Wired
                # here so it is available if the sidecar ever needs it for
                # ownership introspection, and as a reference for operators.
                "ECS_CLUSTER": f"{prefix}-simulation",
                # CAN_BUS0 and VEHICLE_NAME are per-vehicle and are injected
                # as container overrides at run_task time (alongside the
                # fwe-agent overrides in simulation_lambda._start).
                # The static default below is overridden at launch time;
                # it exists only to satisfy CDK's type requirement for
                # the environment dict (an empty string is explicitly
                # forbidden by the image's startup logic).
                "ROUTE_CALCULATOR_NAME": f"{prefix}-ui-route-calculator",
                # SOVD: bucket name for oversized payload upload (> 80 KB).
                # _handle_sovd() reads this env var to construct the S3 key at
                # runtime and uploads via the shared worker_task_role PutObject
                # grant.  Without this, large SOVD responses silently fall back
                # to inline MQTT, which exceeds the 128 KB MQTT limit.
                # This must be on the vehicle-ecu container — it is the ONLY
                # container that runs --commands-mqtt and calls _handle_sovd.
                # Fix Group 4 / Cycle 8 Critical finding (FG4.1).
                "SOVD_RESPONSES_BUCKET": _sovd_responses_bucket_name,
            },
        )

        # ── IAM: ECS read grant for vcan_teardown / ownership introspection ──
        # Pattern: agent_counter_lambda:657-660 (ecs:ListTasks + ecs:DescribeTasks
        # on * with an ecs:cluster ArnEquals condition).
        # worker_task_role is the shared task role for all containers in the
        # fwe-agent task, so this grant is available to both fwe-agent and
        # vehicle-ecu without widening the role unconditionally.
        # Scope rationale: ecs:ListTasks and ecs:DescribeTasks do not support
        # resource-level ARN scoping (the IAM service-auth reference lists their
        # resource type column as empty), so "*" is mandatory; the ArnEquals
        # condition restricts evaluation to this cluster only.
        worker_task_role.add_to_policy(iam.PolicyStatement(
            sid="FweAgentEcsReadForVcanOwnership",
            actions=["ecs:ListTasks", "ecs:DescribeTasks"],
            resources=["*"],
            conditions={"ArnEquals": {"ecs:cluster": cluster.cluster_arn}},
        ))

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

        # ── Fleet Enrollment table (from storage stack, same name the connector uses) ──
        fleet_enrollment_table = dynamodb.Table.from_table_name(
            self, "FleetEnrollmentTable",
            f"{prefix}-storage-fleet-enrollment",
        )

        # ── Lambda function ──────────────────────────────────────────────
        sim_lambda = lambda_.Function(self, "SimulationApi",
            function_name=f"{prefix}-simulation-api",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="simulation_lambda.handler",
            code=lambda_.Code.from_asset(_bundle_sim_lambda()),
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
                "FLEET_ENROLLMENT_TABLE_NAME": fleet_enrollment_table.table_name,
                "DEPLOYMENT_STAGE": stage,
                "AWS_REGION_NAME": self.region,
            })

        # Lambda permissions
        sim_table.grant_read_write_data(sim_lambda)
        # Fleet enrollment GSI — needed by _lib/fleet_membership.resolve_vins_to_fleets()
        sim_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[
                f"arn:aws:dynamodb:{self.region}:{self.account}:table/{fleet_enrollment_table.table_name}/index/vehicleId-index",
            ]))
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
                # vehicle-ecu holds ALL simulator output on the intent/presence
                # path — the PresenceLoop runs the trip inside this sidecar and no
                # separate fwe-simulator task is spawned. Omitting it meant
                # _get_worker_logs got AccessDenied, swallowed it (that except
                # block is now logged), returned an empty sim_logs, and the UI's
                # SimulationLogViewer sat on "Waiting for simulator output..."
                # forever while the trip ran normally. See
                # issues/2026-09-01-simulator-output-invisible-vehicle-ecu-logs-ungranted/.
                vehicle_ecu_log_group.log_group_arn + ":*",
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

        # Cognito authorizer — imports User Pool ARN from ui_stack via cross-stack ref
        ui_construct_id = f'cms-{stage}-ui'
        user_pool_arn = Fn.import_value(f'{ui_construct_id}-user-pool-arn')
        user_pool = cognito.UserPool.from_user_pool_arn(self, 'ImportedUserPool', user_pool_arn)
        cognito_authorizer = apigateway.CognitoUserPoolsAuthorizer(
            self, 'SimulationCognitoAuthorizer',
            cognito_user_pools=[user_pool],
            authorizer_name=f'{construct_id}-cognito-auth'
        )
        auth_kwargs = dict(authorizer=cognito_authorizer,
                           authorization_type=apigateway.AuthorizationType.COGNITO)

        integration = apigateway.LambdaIntegration(sim_lambda)

        # /api/simulation/*
        api_res = sim_api.root.add_resource("api")
        sim_res = api_res.add_resource("simulation")
        sim_res.add_method("GET", integration, **auth_kwargs)  # list / health

        start = sim_res.add_resource("start")
        start.add_method("POST", integration, **auth_kwargs)

        status = sim_res.add_resource("status")
        status_id = status.add_resource("{simulationId}")
        status_id.add_method("GET", integration, **auth_kwargs)

        stop = sim_res.add_resource("stop")
        stop_id = stop.add_resource("{simulationId}")
        stop_id.add_method("POST", integration, **auth_kwargs)

        list_res = sim_res.add_resource("list")
        list_res.add_method("GET", integration, **auth_kwargs)

        health = sim_res.add_resource("health")
        health.add_method("GET", integration, **auth_kwargs)

        drivers = sim_res.add_resource("drivers")
        drivers.add_method("GET", integration, **auth_kwargs)

        presets = sim_res.add_resource("presets")
        presets.add_method("GET", integration, **auth_kwargs)

        campaigns = sim_res.add_resource("campaigns")
        campaigns.add_method("GET", integration, **auth_kwargs)

        discover = sim_res.add_resource("discover-iot-endpoint")
        discover.add_method("GET", integration, **auth_kwargs)

        # /api/simulation/agent/*
        agent_res = sim_res.add_resource("agent")
        agent_start = agent_res.add_resource("start")
        agent_start.add_method("POST", integration, **auth_kwargs)
        agent_stop = agent_res.add_resource("stop")
        agent_stop.add_method("POST", integration, **auth_kwargs)
        agent_status = agent_res.add_resource("status")
        agent_status.add_method("GET", integration, **auth_kwargs)
        agent_logs = agent_res.add_resource("logs")
        agent_logs_vin = agent_logs.add_resource("{vin}")
        agent_logs_vin.add_method("GET", integration, **auth_kwargs)

        # /api/simulation/vehicle/{vehicleId}/faults
        vehicle_res = sim_res.add_resource("vehicle")
        vehicle_id_res = vehicle_res.add_resource("{vehicleId}")
        faults_res = vehicle_id_res.add_resource("faults")
        faults_res.add_method("PUT", integration, **auth_kwargs)
        faults_res.add_method("GET", integration, **auth_kwargs)

        # ── Agent-counter Lambda + alarms (lifecycle hardening Phase 2) ──
        # Spec: .kiro/specs/2026-05-30-cms-sim-lifecycle-hardening/
        # Component 2: scheduled Lambda publishing FWE/Cluster metrics.
        # Component 3: SNS topic + 3 alarms.

        # SNS topic — new dedicated topic per decisions.md Decision 4
        # (no reusable platform-alarms topic exists in the account).
        # CMK encryption via AWS-managed `alias/aws/sns` key mirrors the
        # FlinkAlarmsTopic / FweManifestAlarmTopic pattern in this repo
        # and satisfies cdk-nag rule AwsSolutions-SNS2 (security-review.md
        # Cycle 1 Warning, addressed in Cycle 2 fix-pass 2026-06-22).
        sim_alarms_topic = sns.Topic(self, "SimulationAlarmsTopic",
            topic_name=f"{prefix}-simulation-alarms",
            display_name=f"CMS {stage} simulation alarms",
            master_key=kms.Alias.from_alias_name(
                self, "SimulationAlarmsSnsKey", "alias/aws/sns"))

        # Lambda — module bundled from services/simulation/agent_counter/
        agent_counter_lambda = lambda_.Function(self, "AgentCounterLambda",
            function_name=f"{prefix}-fwe-agent-counter",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="agent_counter.handler",
            code=lambda_.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "../../services/simulation/agent_counter")),
            timeout=Duration.seconds(30),
            memory_size=128,
            log_retention=logs.RetentionDays.TWO_WEEKS,
            environment={
                "ECS_CLUSTER": cluster.cluster_name,
                "FWE_AGENT_FAMILY": f"{prefix}-fwe-agent",
                # Decisions.md Decision 1: corrected name (no `-storage`)
                "SIMULATIONS_TABLE": sim_table.table_name,
                "STAGE": stage,
            })

        # IAM — least privilege per spec Component 2.
        # ECS list/describe — cluster-scoped via condition key.
        agent_counter_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["ecs:ListTasks", "ecs:DescribeTasks"],
            resources=["*"],
            conditions={"ArnEquals": {"ecs:cluster": cluster.cluster_arn}}))
        # ecs:DescribeTaskDefinition does NOT support resource-level
        # permissions per the AWS service-authorization reference
        # (the action's "Resource types (*required)" column is empty,
        # which means: "you must specify all resources ('*')"). A
        # family-level ARN here never matches at evaluation time and
        # the API call fails with AccessDeniedException at runtime
        # (`...not authorized to perform: ecs:DescribeTaskDefinition
        # on resource: *`). The condition keys column is also empty,
        # so we cannot scope via Condition either.
        # Verified 2026-06-16 against
        # https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazonelasticcontainerservice.html
        # The blast radius of this grant is bounded by the read-only
        # access level of the action and the Lambda role's narrow
        # invoker (EventBridge schedule, no public ingress).
        # Cycle 2 fix-pass (security-review.md 2026-06-22 Warning):
        # cap blast radius to the stack's region via the
        # `aws:RequestedRegion` global-condition key. The Lambda only
        # ever calls describe_task_definition for a family in its own
        # region, so this constraint is functionally a no-op for the
        # legitimate code path while cutting cross-region read potential
        # in a supply-chain-compromise scenario.
        agent_counter_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["ecs:DescribeTaskDefinition"],
            resources=["*"],
            conditions={"StringEquals": {
                "aws:RequestedRegion": Stack.of(self).region}}))
        # DDB scan on the simulations table only.
        sim_table.grant(agent_counter_lambda, "dynamodb:Scan")
        # CloudWatch put-metric scoped to FWE/Cluster namespace.
        agent_counter_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["cloudwatch:PutMetricData"],
            resources=["*"],
            conditions={"StringEquals": {"cloudwatch:namespace": "FWE/Cluster"}}))

        # EventBridge — invoke every 5 minutes
        events.Rule(self, "AgentCounterSchedule",
            rule_name=f"{prefix}-fwe-agent-counter",
            description=f"Invoke {prefix}-fwe-agent-counter every 5 minutes",
            schedule=events.Schedule.rate(Duration.minutes(5)),
            targets=[events_targets.LambdaFunction(agent_counter_lambda)])

        # Alarms — three per spec Component 3.
        # Helper: build a metric on FWE/Cluster with the Stage dimension.
        def _fwe_metric(metric_name: str) -> cloudwatch.Metric:
            return cloudwatch.Metric(
                namespace="FWE/Cluster",
                metric_name=metric_name,
                dimensions_map={"Stage": stage},
                period=Duration.minutes(5),
                statistic="Maximum")

        # 1. OrphanAgentCount > 0 for 2 of 2 datapoints (~10 min sustained)
        orphan_alarm = cloudwatch.Alarm(self, "OrphanAgentAlarm",
            alarm_name=f"{prefix}-fwe-orphan-agent",
            alarm_description=("FWE agents running with no matching active "
                               "simulation row — see runbook in DEPLOYMENT.md"),
            metric=_fwe_metric("OrphanAgentCount"),
            threshold=0,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            evaluation_periods=2,
            datapoints_to_alarm=2,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING)
        orphan_alarm.add_alarm_action(cw_actions.SnsAction(sim_alarms_topic))
        orphan_alarm.add_ok_action(cw_actions.SnsAction(sim_alarms_topic))

        # 2. StaleRevisionAgentCount > 0 for 1 of 1 datapoint (5 min)
        stale_alarm = cloudwatch.Alarm(self, "StaleRevisionAgentAlarm",
            alarm_name=f"{prefix}-fwe-stale-revision-agent",
            alarm_description=("FWE agent running on a task-definition revision "
                               "below the latest active — drain script may have "
                               "skipped a deploy. See DEPLOYMENT.md runbook."),
            metric=_fwe_metric("StaleRevisionAgentCount"),
            threshold=0,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            evaluation_periods=1,
            datapoints_to_alarm=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING)
        stale_alarm.add_alarm_action(cw_actions.SnsAction(sim_alarms_topic))
        stale_alarm.add_ok_action(cw_actions.SnsAction(sim_alarms_topic))

        # 3. Errors > 0 on the AgentCounterLambda for 2 of 2 datapoints
        errors_alarm = cloudwatch.Alarm(self, "AgentCounterErrorsAlarm",
            alarm_name=f"{prefix}-fwe-agent-counter-errors",
            alarm_description=("Agent-counter Lambda failed on 2 consecutive "
                               "scheduled invocations. Check Lambda logs."),
            metric=agent_counter_lambda.metric_errors(
                period=Duration.minutes(5), statistic="Sum"),
            threshold=0,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            evaluation_periods=2,
            datapoints_to_alarm=2,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING)
        errors_alarm.add_alarm_action(cw_actions.SnsAction(sim_alarms_topic))
        errors_alarm.add_ok_action(cw_actions.SnsAction(sim_alarms_topic))

        # ── Outputs ──────────────────────────────────────────────────────
        CfnOutput(self, "SimulationApiUrl", value=sim_api.url,
                  description="Simulation API endpoint - add to runtimeConfig.json as simulationApiEndpoint")
        CfnOutput(self, "ClusterName", value=cluster.cluster_name)
        CfnOutput(self, "WorkerTaskDefArn", value=worker_task_def.task_definition_arn)
        CfnOutput(self, "FweTaskDefArn", value=fwe_task_def.task_definition_arn)
        CfnOutput(self, "SimulationsTableName", value=sim_table.table_name)
        CfnOutput(self, "SimulationAlarmsTopicArn",
                  value=sim_alarms_topic.topic_arn,
                  description="SNS topic for FWE cluster alarms — operators subscribe out-of-band")
        CfnOutput(self, "AgentCounterFunctionName",
                  value=agent_counter_lambda.function_name,
                  description="Scheduled Lambda publishing FWE/Cluster metrics")
