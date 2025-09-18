"""
Telemetry Integration Stack - IoT rules and VPC destinations for MSK connectivity
Based on proven CloudFormation pattern
"""

from aws_cdk import (
    Stack,
    aws_iot as iot,
    aws_iam as iam,
    CfnOutput
)
from constructs import Construct

class TelemetryIntegrationStack(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, 
                 msk_cluster_arn: str,
                 msk_vpc_id: str,
                 msk_subnet_ids: list,
                 msk_security_group_id: str,
                 msk_secret_arn: str,
                 msk_bootstrap_servers: str,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Extract deployment stage from construct_id
        deployment_stage = construct_id.split('-')[1] if '-' in construct_id else 'dev'
        
        # 1. Create VPC ENI role (exactly like CloudFormation)
        self.vpc_eni_role = iam.Role(
            self, "IoTCreateVpcENIRole",
            role_name=f"IoTCreateVpcENIRole-{deployment_stage}",
            path="/service-role/",
            assumed_by=iam.ServicePrincipal("iot.amazonaws.com")
        )
        
        # 2. Create VPC destination policy (exactly like CloudFormation)
        vpc_policy = iam.ManagedPolicy(
            self, "IoTVpcDestinationPolicy",
            managed_policy_name=f"IoTVpcDestinationPolicy-{deployment_stage}",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "ec2:CreateNetworkInterface",
                        "ec2:DescribeNetworkInterfaces", 
                        "ec2:DescribeVpcs",
                        "ec2:DeleteNetworkInterface",
                        "ec2:DescribeSubnets",
                        "ec2:DescribeVpcAttribute",
                        "ec2:DescribeSecurityGroups",
                        "ec2:CreateNetworkInterfacePermission",
                        "ec2:CreateTags"
                    ],
                    resources=["*"]
                )
            ],
            roles=[self.vpc_eni_role]
        )
        
        # 3. Create MSK secret access role (match CloudFormation pattern)
        self.msk_secret_role = iam.Role(
            self, "IoTMSKSecretRuleRole",
            role_name=f"IoT-Rule-MSK-Role-{deployment_stage}",
            description="Role for the AWS IoT Rules engine to use when accessing Amazon MSK credentials in AWS Secrets Manager",
            assumed_by=iam.ServicePrincipal("iot.amazonaws.com")
        )
        
        # 4. Create MSK secret policy (reference existing secret)
        secret_policy = iam.ManagedPolicy(
            self, "IoTMSKSecretPolicy", 
            managed_policy_name=f"IoTMSKSecretPolicy-{deployment_stage}",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "secretsmanager:GetSecretValue",
                        "secretsmanager:DescribeSecret"
                    ],
                    resources=[msk_secret_arn]
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "kms:Encrypt",
                        "kms:Decrypt",
                        "kms:ReEncrypt*", 
                        "kms:GenerateDataKey*",
                        "kms:DescribeKey"
                    ],
                    resources=[f"arn:aws:kms:{self.region}:{self.account}:key/*"]
                )
            ],
            roles=[self.msk_secret_role]
        )
        
        # 5. Create VPC destination (with explicit dependency)
        self.vpc_destination = iot.CfnTopicRuleDestination(
            self, "TopicRuleVpcDestination",
            vpc_properties=iot.CfnTopicRuleDestination.VpcDestinationPropertiesProperty(
                vpc_id=msk_vpc_id,
                subnet_ids=msk_subnet_ids,
                security_groups=[msk_security_group_id],
                role_arn=self.vpc_eni_role.role_arn
            )
        )
        
        # Explicit dependency on policy
        self.vpc_destination.node.add_dependency(vpc_policy)
        
        # 6. Create IoT rule (exactly like CloudFormation)
        self.msk_rule = iot.CfnTopicRule(
            self, "MSKRule",
            rule_name=f"IoT_MSK_Rule_{deployment_stage}",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM 'topic/telemetry'",
                description="Rule to forward MQTT messages to MSK with SCRAM",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        kafka=iot.CfnTopicRule.KafkaActionProperty(
                            destination_arn=self.vpc_destination.ref,
                            topic="cms-telemetry-raw",
                            client_properties={
                                "sasl.mechanism": "SCRAM-SHA-512",
                                "security.protocol": "SASL_SSL", 
                                "bootstrap.servers": msk_bootstrap_servers,
                                "sasl.scram.username": f"${{get_secret(\"{msk_secret_arn}\", \"SecretString\", \"username\", \"{self.msk_secret_role.role_arn}\")}}",
                                "sasl.scram.password": f"${{get_secret(\"{msk_secret_arn}\", \"SecretString\", \"password\", \"{self.msk_secret_role.role_arn}\")}}"
                            }
                        )
                    )
                ]
            )
        )
        
        # Outputs
        CfnOutput(
            self, "VpcDestinationArn",
            value=self.vpc_destination.ref,
            export_name=f"{construct_id}-vpc-destination-arn"
        )
        
        CfnOutput(
            self, "IoTRuleName", 
            value=self.msk_rule.ref,
            export_name=f"{construct_id}-iot-rule-name"
        )
