"""
CDK Stack: OEM Connector Service

ECS Fargate task that ingests telemetry from an OEM source and writes
clean JSON to the cms-telemetry-oem Kafka topic.

Supports three connection types (configured via CONNECTOR_TYPE env var):
  - rest_polling:      Poll-sleep loop against OEM REST API
  - grpc_streaming:    Long-lived gRPC client (e.g., Ford Feed Service)
  - websocket_inbound: Accept inbound WebSocket connections (adds ALB + TLS)

Usage:
  make deploy-connector CONNECTOR_NAME=ford-feed CONNECTOR_TYPE=grpc_streaming
"""
from aws_cdk import (
    Stack, Duration, Fn, CfnOutput,
    aws_ecs as ecs,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_logs as logs,
    aws_secretsmanager as sm,
    aws_elasticloadbalancingv2 as elbv2,
)
from constructs import Construct
import os


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

        # ── Container ──────────────────────────────────────────────────
        log_group = logs.LogGroup(self, "Logs",
            log_group_name=f"/ecs/cms-{stage}-connector-{connector_name}",
            retention=logs.RetentionDays.TWO_WEEKS,
        )

        container = task_def.add_container("Connector",
            image=ecs.ContainerImage.from_asset(f"../services/connectors/{connector_name}"),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="connector", log_group=log_group),
            environment={
                "CONNECTOR_NAME": connector_name,
                "CONNECTOR_TYPE": connector_type,
                "OEM_SOURCE": connector_name,
                "KAFKA_TOPIC": "cms-telemetry-oem",
                "DEPLOYMENT_STAGE": stage,
                "AWS_REGION": self.region,
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

        # ── Outputs ────────────────────────────────────────────────────
        CfnOutput(self, "ServiceName", value=service.service_name)
        CfnOutput(self, "ClusterName", value=cluster.cluster_name)
        CfnOutput(self, "ConnectorType", value=connector_type)
