"""
CDK Stack: WebSocket Fanout Service

ECS Fargate task that consumes from per-fleet Kafka topics
and pushes telemetry to connected WebSocket clients.
"""
import os
import sys

from aws_cdk import (
    Stack, Duration, Fn, CfnOutput,
    aws_ecs as ecs,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_logs as logs,
)
from constructs import Construct


def _resolve_bootstrap_servers(stage: str, region: str) -> str:
    """Resolve the MSK SASL/IAM bootstrap brokers at synth time so the task-def
    env carries the real value automatically (no manual post-deploy set step).

    Order:
      1. explicit ``BOOTSTRAP_SERVERS`` env override (lets ops pin without a redeploy)
      2. synth-time lookup: ``cms-<stage>-msk`` CFN ``MSKClusterArn`` output →
         ``kafka:GetBootstrapBrokers`` (``BootstrapBrokerStringSaslIam``)
      3. ``"PLACEHOLDER"`` + a loud stderr warning when the cluster isn't reachable
         (e.g. MSK not yet deployed at synth).
    """
    val = os.environ.get("BOOTSTRAP_SERVERS", "").strip()
    if val:
        return val
    try:
        import boto3
        cf = boto3.client("cloudformation", region_name=region)
        outputs = cf.describe_stacks(StackName=f"cms-{stage}-msk")["Stacks"][0].get("Outputs", [])
        arn = next((o["OutputValue"] for o in outputs if o["OutputKey"] == "MSKClusterArn"), None)
        if arn:
            brokers = boto3.client("kafka", region_name=region).get_bootstrap_brokers(
                ClusterArn=arn
            ).get("BootstrapBrokerStringSaslIam", "").strip()
            if brokers:
                return brokers
    except Exception as e:  # synth-time best-effort lookup
        print(f"⚠️  [ws-fanout] MSK bootstrap-broker lookup failed at synth: {e}",
              file=sys.stderr, flush=True)
    print("⚠️  [ws-fanout] BOOTSTRAP_SERVERS unresolved — using PLACEHOLDER. Ensure "
          "cms-<stage>-msk is deployed, or set BOOTSTRAP_SERVERS explicitly.",
          file=sys.stderr, flush=True)
    return "PLACEHOLDER"


class WsFanoutStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        stage = construct_id.split('-')[1]  # e.g. "prod" from "cms-prod-ws-fanout"
        msk_stack = f"cms-{stage}-msk"
        ui_stack = f"cms-{stage}-ui"
        storage_prefix = f"cms-{stage}-storage"

        # Import VPC from MSK stack
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

        ws_endpoint = Fn.import_value(f"{ui_stack}-ws-endpoint")

        # ECS Cluster (reuse simulation cluster or create dedicated)
        cluster = ecs.Cluster(self, "FanoutCluster",
            vpc=vpc,
            cluster_name=f"cms-{stage}-ws-fanout",
        )

        # Task definition
        task_def = ecs.FargateTaskDefinition(self, "FanoutTask",
            cpu=256,
            memory_limit_mib=512,
        )

        # IAM permissions
        task_def.task_role.add_to_policy(iam.PolicyStatement(
            actions=["kafka-cluster:Connect", "kafka-cluster:DescribeGroup",
                     "kafka-cluster:AlterGroup", "kafka-cluster:DescribeTopic",
                     "kafka-cluster:ReadData", "kafka-cluster:DescribeClusterDynamicConfiguration"],
            resources=["*"],
        ))
        task_def.task_role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:Query", "dynamodb:DeleteItem"],
            resources=[
                f"arn:aws:dynamodb:{self.region}:{self.account}:table/{storage_prefix}-ws-connections",
                f"arn:aws:dynamodb:{self.region}:{self.account}:table/{storage_prefix}-ws-connections/index/*",
            ],
        ))
        task_def.task_role.add_to_policy(iam.PolicyStatement(
            actions=["execute-api:ManageConnections"],
            resources=[f"arn:aws:execute-api:{self.region}:{self.account}:*/@connections/*"],
        ))

        # Container
        task_def.add_container("FanoutContainer",
            image=ecs.ContainerImage.from_asset("../services/ws-fanout"),
            environment={
                "BOOTSTRAP_SERVERS": _resolve_bootstrap_servers(stage, self.region),
                "WS_CONNECTIONS_TABLE": f"{storage_prefix}-ws-connections",
                "WS_API_ENDPOINT": ws_endpoint,
                "AWS_REGION": self.region,
                "GROUP_ID": f"cms-{stage}-ws-fanout-consumer",
            },
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="ws-fanout",
                log_retention=logs.RetentionDays.ONE_WEEK,
            ),
        )

        # Fargate service — single task, no ALB needed
        service = ecs.FargateService(self, "FanoutService",
            cluster=cluster,
            task_definition=task_def,
            desired_count=1,
            security_groups=[msk_sg],
            assign_public_ip=False,
        )

        CfnOutput(self, "ServiceArn", value=service.service_arn,
            export_name=f"{construct_id}-service-arn")
