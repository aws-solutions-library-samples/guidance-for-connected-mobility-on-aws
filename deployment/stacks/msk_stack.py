"""
MSK Stack - Simplified Kafka cluster with SCRAM authentication
"""

import json
from aws_cdk import (
    Stack,
    aws_msk as msk,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_secretsmanager as secretsmanager,
    aws_logs as logs,
    aws_lambda as lambda_,
    aws_kms as kms,
    CustomResource,
    CfnOutput,
    RemovalPolicy,
    Duration,
    SecretValue
)
from constructs import Construct

class MSKStack(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Create dedicated VPC for MSK with private subnets
        print("Creating dedicated VPC for MSK with private subnets")
        self.vpc = ec2.Vpc(
            self, "MSKVpc",
            max_azs=2,
            cidr="10.0.0.0/16",
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="MSKPrivate",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="MSKPublic", 
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24
                )
            ],
            nat_gateways=1  # One NAT gateway for cost optimization
        )
        
        # Use private subnets for MSK cluster
        subnets = self.vpc.private_subnets[:2]  # Take first 2 private subnets for MSK
        
        # Security group for MSK
        self.msk_security_group = ec2.SecurityGroup(
            self, "MSKSecurityGroup",
            vpc=self.vpc,
            description="Security group for MSK cluster with IoT Core direct access",
            allow_all_outbound=True
        )
        
        # Kafka ports
        self.msk_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4("10.0.0.0/8"),
            connection=ec2.Port.tcp(9092),
            description="Kafka PLAINTEXT"
        )
        
        self.msk_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4("10.0.0.0/8"),
            connection=ec2.Port.tcp(9094),
            description="Kafka TLS"
        )
        
        self.msk_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4("10.0.0.0/8"),
            connection=ec2.Port.tcp(9096),
            description="Kafka SASL_SCRAM"
        )
        
        self.msk_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4("10.0.0.0/8"),
            connection=ec2.Port.tcp(9098),
            description="Kafka SASL_IAM"
        )
        
        # Generate unique suffix for resource names to avoid conflicts
        unique_suffix = self.node.addr[:8]  # Use first 8 chars of CDK node address
        
        # CloudWatch log group for MSK - use unique name to avoid conflicts
        self.msk_log_group = logs.LogGroup(
            self, "MSKLogGroup",
            log_group_name=f"/aws/msk/{construct_id}-{unique_suffix}",
            removal_policy=RemovalPolicy.DESTROY,
            retention=logs.RetentionDays.ONE_WEEK
        )
        
        # Create customer-managed KMS key for MSK secrets
        self.msk_kms_key = kms.Key(
            self, "MSKSecretsKey",
            description="Customer-managed key for MSK SCRAM secrets"
        )
        
        # Add policy to allow root account access
        self.msk_kms_key.add_to_resource_policy(
            iam.PolicyStatement(
                sid="Enable IAM User Permissions",
                effect=iam.Effect.ALLOW,
                principals=[iam.AccountRootPrincipal()],
                actions=["kms:*"],
                resources=["*"]
            )
        )
        
        # Create SCRAM secret for iot-user with proper naming and KMS key
        self.iot_user_secret = secretsmanager.Secret(
            self, "IoTUserSecret",
            secret_name=f"AmazonMSK_{construct_id}_iot_user_credentials",
            description="SCRAM credentials for IoT user to access MSK",
            encryption_key=self.msk_kms_key,
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"username": "iot-user"}',
                generate_string_key="password",
                exclude_characters=' "%@/\\'
            )
        )
        
        # MSK Cluster Configuration - use unique name to avoid conflicts
        cluster_config = msk.CfnConfiguration(
            self, "MSKClusterConfig",
            name=f"{construct_id}-config-{unique_suffix}",
            description="MSK cluster configuration for CMS",
            kafka_versions_list=["3.8.x"],
            server_properties="""
auto.create.topics.enable=true
default.replication.factor=2
num.partitions=3
allow.everyone.if.no.acl.found=false
"""
        )
        
        # MSK Cluster - use m5.large for VPC connectivity support
        instance_type = "kafka.m5.large"  # VPC connectivity requires m5.large or larger
        volume_size = 20 if construct_id.endswith("-dev") else 100
        
        self.cluster = msk.CfnCluster(
            self, "MSKCluster",
            cluster_name=f"{construct_id}-cluster",
            kafka_version="3.8.x",
            number_of_broker_nodes=2,
            broker_node_group_info=msk.CfnCluster.BrokerNodeGroupInfoProperty(
                instance_type=instance_type,
                client_subnets=[subnet.subnet_id for subnet in subnets[:2]],
                security_groups=[self.msk_security_group.security_group_id],
                storage_info=msk.CfnCluster.StorageInfoProperty(
                    ebs_storage_info=msk.CfnCluster.EBSStorageInfoProperty(
                        volume_size=volume_size
                    )
                ),
                connectivity_info=msk.CfnCluster.ConnectivityInfoProperty(
                    vpc_connectivity=msk.CfnCluster.VpcConnectivityProperty(
                        client_authentication=msk.CfnCluster.VpcConnectivityClientAuthenticationProperty(
                            sasl=msk.CfnCluster.VpcConnectivitySaslProperty(
                                iam=msk.CfnCluster.VpcConnectivityIamProperty(enabled=False),
                                scram=msk.CfnCluster.VpcConnectivityScramProperty(enabled=False)
                            )
                        )
                    )
                )
            ),
            configuration_info=msk.CfnCluster.ConfigurationInfoProperty(
                arn=cluster_config.attr_arn,
                revision=1
            ),
            encryption_info=msk.CfnCluster.EncryptionInfoProperty(
                encryption_in_transit=msk.CfnCluster.EncryptionInTransitProperty(
                    client_broker="TLS",
                    in_cluster=True
                )
            ),
            # SCRAM + IAM authentication
            client_authentication=msk.CfnCluster.ClientAuthenticationProperty(
                sasl=msk.CfnCluster.SaslProperty(
                    scram=msk.CfnCluster.ScramProperty(enabled=True),
                    iam=msk.CfnCluster.IamProperty(enabled=True)
                ),
                tls=msk.CfnCluster.TlsProperty(
                    enabled=False  # Disable mTLS for simplicity
                )
            ),
            logging_info=msk.CfnCluster.LoggingInfoProperty(
                broker_logs=msk.CfnCluster.BrokerLogsProperty(
                    cloud_watch_logs=msk.CfnCluster.CloudWatchLogsProperty(
                        enabled=True,
                        log_group=self.msk_log_group.log_group_name
                    )
                )
            )
        )
        
        # Store cluster ARN for other stacks
        self.cluster_arn = self.cluster.attr_arn
        
        # Create Lambda function for SCRAM secret association
        secret_association_function = lambda_.Function(
            self, "MSKSecretAssociationFunction",
            function_name=f"cfn-msk-secret-association-{construct_id}",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="index.lambda_handler",
            timeout=Duration.seconds(30),
            code=lambda_.Code.from_inline("""
import json
import logging
import cfnresponse
import boto3

client = boto3.client('kafka')
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def associate(ClusterArn, SecretArn):
    response = client.batch_associate_scram_secret(
        ClusterArn=ClusterArn,
        SecretArnList=[SecretArn]
    )
    logger.info(response)
    logger.info('Secret associated!')
    return response['ResponseMetadata']['HTTPStatusCode']

def disassociate(ClusterArn, SecretArn):
    response = client.batch_disassociate_scram_secret(
        ClusterArn=ClusterArn,
        SecretArnList=[SecretArn]
    )
    logger.info(response)
    logger.info('Secret disassociated!')
    return response['ResponseMetadata']['HTTPStatusCode']
    
def lambda_handler(event, context):
    logger.info(event)
    responseStatus = cfnresponse.FAILED
    try:
        ClusterArn = event['ResourceProperties']['ClusterArn']
        SecretArn  = event['ResourceProperties']['SecretArn']
        if event['RequestType'] == 'Create':
            if (associate(ClusterArn, SecretArn) == 200):
                responseStatus = cfnresponse.SUCCESS
        elif event['RequestType'] == 'Delete':
            if (disassociate(ClusterArn, SecretArn) == 200):
                responseStatus = cfnresponse.SUCCESS
        elif event['RequestType'] == 'Update':
            OldClusterArn = event['OldResourceProperties']['ClusterArn']
            OldSecretArn  = event['OldResourceProperties']['SecretArn']
            if (disassociate(OldClusterArn, OldSecretArn) == 200):
                if (associate(ClusterArn, SecretArn) == 200):
                    responseStatus = cfnresponse.SUCCESS
        else:
            logger.error('Unsupported RequestType %s. Signaling failure to CloudFormation.', event['RequestType'])
            
    except Exception:
        logger.exception('Signaling failure to CloudFormation.')
    
    cfnresponse.send(event, context, responseStatus, {})
    return
""")
        )
        
        # Grant Lambda permissions to associate/disassociate SCRAM secrets
        secret_association_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "kafka:BatchAssociateScramSecret",
                    "kafka:BatchDisassociateScramSecret"
                ],
                resources=[self.cluster.attr_arn]
            )
        )
        
        # Grant Lambda permissions to create KMS grants
        secret_association_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["kms:CreateGrant"],
                resources=[self.msk_kms_key.key_arn]
            )
        )
        
        # Create custom resource to associate SCRAM secret with cluster
        secret_association = CustomResource(
            self, "SecretAssociation",
            service_token=secret_association_function.function_arn,
            properties={
                "ClusterArn": self.cluster.attr_arn,
                "SecretArn": self.iot_user_secret.secret_arn
            }
        )
        
        # Ensure secret association happens after cluster is created
        secret_association.node.add_dependency(self.cluster)
        secret_association.node.add_dependency(self.iot_user_secret)
        
        # Add EC2 instance to create Kafka topics
        # Create IAM role for topic creation instance
        topic_creator_role = iam.Role(
            self, "TopicCreatorRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore")
            ],
            inline_policies={
                "KafkaTopicCreation": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=[
                                "kafka-cluster:Connect",
                                "kafka-cluster:CreateTopic",
                                "kafka-cluster:DescribeTopic",
                                "kafka:GetBootstrapBrokers"
                            ],
                            resources=["*"]
                        )
                    ]
                )
            }
        )
        
        # User data script to create topics
        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "yum update -y",
            "yum install -y java-11-amazon-corretto",
            "cd /opt",
            "wget https://downloads.apache.org/kafka/2.8.2/kafka_2.13-2.8.2.tgz",
            "tar -xzf kafka_2.13-2.8.2.tgz",
            "cd kafka_2.13-2.8.2",
            f"BOOTSTRAP_SERVERS=$(aws kafka get-bootstrap-brokers --cluster-arn {self.cluster.attr_arn} --region {self.region} --query 'BootstrapBrokerStringSaslIam' --output text)",
            "cat > client.properties << EOF",
            "security.protocol=SASL_SSL",
            "sasl.mechanism=AWS_MSK_IAM", 
            "sasl.jaas.config=software.amazon.msk.auth.iam.IAMLoginModule required;",
            "sasl.client.callback.handler.class=software.amazon.msk.auth.iam.IAMClientCallbackHandler",
            "EOF",
            "echo 'Creating topics...'",
            "bin/kafka-topics.sh --bootstrap-server $BOOTSTRAP_SERVERS --command-config client.properties --create --topic cms-telemetry-raw --partitions 3 --replication-factor 2 || true",
            "bin/kafka-topics.sh --bootstrap-server $BOOTSTRAP_SERVERS --command-config client.properties --create --topic cms-telemetry-processed --partitions 3 --replication-factor 2 || true", 
            "bin/kafka-topics.sh --bootstrap-server $BOOTSTRAP_SERVERS --command-config client.properties --create --topic cms-telemetry-maintenance --partitions 3 --replication-factor 2 || true",
            "bin/kafka-topics.sh --bootstrap-server $BOOTSTRAP_SERVERS --command-config client.properties --create --topic cms-trip-events --partitions 3 --replication-factor 2 || true",
            "bin/kafka-topics.sh --bootstrap-server $BOOTSTRAP_SERVERS --command-config client.properties --create --topic cms-safety-events --partitions 3 --replication-factor 2 || true",
            "bin/kafka-topics.sh --bootstrap-server $BOOTSTRAP_SERVERS --command-config client.properties --create --topic cms-maintenance-events --partitions 3 --replication-factor 2 || true",
            "echo 'Topics created successfully'",
            "shutdown -h now"  # Terminate instance after creating topics
        )
        
        # Create EC2 instance for topic creation
        topic_creator_instance = ec2.Instance(
            self, "TopicCreatorInstance",
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MICRO),
            machine_image=ec2.AmazonLinuxImage(generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2),
            vpc=self.vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            role=topic_creator_role,
            user_data=user_data,
            security_group=self.msk_security_group
        )
        
        # Ensure instance starts after cluster is ready
        topic_creator_instance.node.add_dependency(self.cluster)
        
        # Outputs for other stacks to reference
        CfnOutput(
            self, "MSKClusterArn",
            value=self.cluster.attr_arn,
            export_name=f"{construct_id}-cluster-arn"
        )
        
        CfnOutput(
            self, "IoTUserSecretArn",
            value=self.iot_user_secret.secret_arn,
            export_name=f"{construct_id}-iot-user-secret-arn"
        )
        
        CfnOutput(
            self, "MSKSecurityGroupId",
            value=self.msk_security_group.security_group_id,
            export_name=f"{construct_id}-security-group-id"
        )
        
        CfnOutput(
            self, "MSKVpcId",
            value=self.vpc.vpc_id,
            export_name=f"{construct_id}-vpc-id"
        )
        
        CfnOutput(
            self, "MSKPrivateSubnetIds",
            value=",".join([subnet.subnet_id for subnet in self.vpc.private_subnets]),
            export_name=f"{construct_id}-private-subnet-ids"
        )
        
        CfnOutput(
            self, "MSKPublicSubnetIds",
            value=",".join([subnet.subnet_id for subnet in self.vpc.public_subnets]),
            export_name=f"{construct_id}-public-subnet-ids"
        )
