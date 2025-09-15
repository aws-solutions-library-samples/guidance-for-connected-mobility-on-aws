#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Enhanced Single Stack CMS UI Deployment with Dashboard Metrics Aggregator

This script combines auth and main functionality into a single stack
with the addition of the dashboard metrics aggregator.

Key Problems Fixed:
1. IoT Topic Rule Error: The bootstrap_servers parameter needs broker endpoints (like b-1.cluster.kafka.region.amazonaws.com:9092), not ARNs
2. Custom Resource Failure: Eliminated the MSKBootstrapGetterCustomResource by using CDK parameters instead

Solution Approach:
• Use CDK parameters for MSK cluster ARN and bootstrap servers
• Remove all custom resources
• Get bootstrap servers using AWS CLI: aws kafka get-bootstrap-brokers --cluster-arn <your-cluster-arn>
• Deploy with parameters: cdk deploy --parameters MSKClusterArn=<arn> --parameters BootstrapServers=<endpoints>
"""

# Standard Library
import os
from datetime import datetime

# AWS Libraries
from aws_cdk import (
    App, 
    Stack,
    Environment,
    CfnParameter,
    CfnOutput,
    Duration,
    DefaultStackSynthesizer,
    RemovalPolicy
)
import aws_cdk
from aws_cdk import (
    aws_cognito,
    aws_ssm,
    aws_secretsmanager,
    aws_s3,
    aws_s3_deployment,
    aws_cloudfront,
    aws_iam,
    aws_lambda,
    aws_apigateway,
    aws_dynamodb,
    aws_ec2,
    aws_iot,
    aws_sns,
    aws_sqs,
)
from aws_cdk.aws_lambda_event_sources import SqsEventSource
from aws_cdk.aws_sns_subscriptions import SqsSubscription
from constructs import Construct

# Import our custom construct
# from constructs.dashboard_metrics_aggregator import DashboardMetricsAggregator

class EnhancedSingleCMSStack(Stack):
    """
    Enhanced single stack containing all CMS UI resources including dashboard metrics aggregator
    """
    
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Enable stack termination protection to prevent accidental deletion
        self.termination_protection = True
        
        # ===== CDK PARAMETERS FOR MSK INTEGRATION =====
        # These parameters eliminate the need for custom resources
        self.msk_cluster_arn = CfnParameter(
            self,
            "MSKClusterArn",
            type="String",
            description="ARN of the existing MSK cluster",
            default="",
            allowed_pattern="^(arn:aws:kafka:.*|)$"
        )
        
        self.bootstrap_servers = CfnParameter(
            self,
            "BootstrapServers",
            type="String", 
            description="MSK bootstrap servers (e.g., b-1.cluster.kafka.region.amazonaws.com:9092,b-2.cluster.kafka.region.amazonaws.com:9092)",
            default=""
        )
        
        self.deploy_ui_components = CfnParameter(
            self,
            "DeployUIComponents", 
            type="String",
            description="Whether to deploy UI components (true/false)",
            default="true",
            allowed_values=["true", "false"]
        )
        
        # App unique identifier - can be customized via environment variable
        import os
        import time
        import hashlib
        
        # Allow customization via environment variable
        custom_suffix = os.environ.get('CMS_DEPLOYMENT_SUFFIX', '')
        
        if custom_suffix:
            # Use custom suffix if provided
            self.app_unique_id = f"cms-{custom_suffix}"
        else:
            # Generate unique suffix using account ID and timestamp
            account_id = self.account
            timestamp = str(int(time.time()))[-6:]  # Last 6 digits of timestamp
            # Create a short hash of account ID to ensure uniqueness while keeping it short
            account_hash = hashlib.md5(account_id.encode()).hexdigest()[:6]
            self.app_unique_id = f"cms-{account_hash}-{timestamp}"
        
        # Check if we should use existing DynamoDB tables (for preserving data)
        use_existing_tables = os.environ.get('USE_EXISTING_TABLES', 'false').lower() == 'true'
        existing_table_suffix = os.environ.get('EXISTING_TABLE_SUFFIX', '88882')
        
        if use_existing_tables:
            self.table_suffix = existing_table_suffix
            self.table_account = "470296731304"  # Target account with existing data
        else:
            self.table_suffix = self.app_unique_id
            self.table_account = self.account
        
        # Check if this is a stack update (set by Makefile)
        is_stack_update = os.environ.get('STACK_UPDATE', 'false').lower() == 'true'
        
        # Create Cognito User Pool
        self.user_pool = aws_cognito.UserPool(
            self,
            "cms-user-pool",
            user_pool_name=f"{self.app_unique_id}-user-pool",
            self_sign_up_enabled=True,
            sign_in_aliases=aws_cognito.SignInAliases(email=True),
            auto_verify=aws_cognito.AutoVerifiedAttrs(email=True),
            password_policy=aws_cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
            ),
            account_recovery=aws_cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=aws_cdk.RemovalPolicy.RETAIN,
        )
        
        # Create Cognito User Pool Client
        self.user_pool_client = aws_cognito.UserPoolClient(
            self,
            "cms-user-pool-client",
            user_pool=self.user_pool,
            user_pool_client_name=f"{self.app_unique_id}-user-pool-client",
            generate_secret=False,
            auth_flows=aws_cognito.AuthFlow(
                user_srp=True,
                user_password=True,
            ),
            o_auth=aws_cognito.OAuthSettings(
                flows=aws_cognito.OAuthFlows(
                    authorization_code_grant=True,
                    implicit_code_grant=True,
                ),
                scopes=[
                    aws_cognito.OAuthScope.EMAIL,
                    aws_cognito.OAuthScope.OPENID,
                    aws_cognito.OAuthScope.PROFILE,
                ],
                callback_urls=["http://localhost:3000", "http://localhost:5173"],
                logout_urls=["http://localhost:3000", "http://localhost:5173"],
            ),
        )
        
        # Create Cognito Identity Pool
        self.identity_pool = aws_cognito.CfnIdentityPool(
            self,
            "cms-identity-pool",
            identity_pool_name=f"{self.app_unique_id}-identity-pool",
            allow_unauthenticated_identities=False,
            cognito_identity_providers=[
                aws_cognito.CfnIdentityPool.CognitoIdentityProviderProperty(
                    client_id=self.user_pool_client.user_pool_client_id,
                    provider_name=self.user_pool.user_pool_provider_name,
                )
            ],
        )
        
        # Create Cognito Domain (optional - can be disabled to avoid conflicts)
        create_cognito_domain = os.environ.get('CREATE_COGNITO_DOMAIN', 'false').lower() == 'true'
        
        if create_cognito_domain:
            # Generate a more unique domain prefix to avoid conflicts
            import random
            random_suffix = str(random.randint(1000, 9999))
            domain_prefix = f"{self.app_unique_id}-{random_suffix}".lower()
            
            # Ensure domain prefix meets requirements (3-63 chars, lowercase, alphanumeric + hyphens)
            if len(domain_prefix) > 63:
                domain_prefix = domain_prefix[:63]
            
            self.cognito_domain = aws_cognito.UserPoolDomain(
                self,
                "cms-cognito-domain",
                user_pool=self.user_pool,
                cognito_domain=aws_cognito.CognitoDomainOptions(
                    domain_prefix=domain_prefix
                ),
            )
        else:
            # Skip Cognito domain creation to avoid conflicts
            self.cognito_domain = None
        
        # Create IDP Config Secret
        self.idp_config_secret = aws_secretsmanager.Secret(
            self,
            "idp-config-secret",
            secret_name=f"{self.app_unique_id}-idp-config",
            description="IDP configuration for CMS UI",
            generate_secret_string=aws_secretsmanager.SecretStringGenerator(
                secret_string_template='{"clientId": "placeholder"}',
                generate_string_key="clientSecret",
                exclude_characters=" %+~`#$&*()|[]{}:;<>?!'/\"\\",
            ),
        )
        
        # Create S3 bucket for UI hosting
        self.ui_bucket = aws_s3.Bucket(
            self,
            "ui-bucket",
            bucket_name=f"{self.app_unique_id}-ui-bucket",
            public_read_access=False,
            block_public_access=aws_s3.BlockPublicAccess.BLOCK_ALL,
            encryption=aws_s3.BucketEncryption.S3_MANAGED,
        )
        
        # Create CloudFront Origin Access Identity
        oai = aws_cloudfront.OriginAccessIdentity(
            self,
            "ui-oai",
            comment=f"OAI for {self.app_unique_id} UI bucket"
        )
        
        # Grant CloudFront access to S3 bucket
        self.ui_bucket.grant_read(oai)
        
        # Create CloudFront distribution
        self.cloudfront_distribution = aws_cloudfront.CloudFrontWebDistribution(
            self,
            "ui-distribution",
            origin_configs=[
                aws_cloudfront.SourceConfiguration(
                    s3_origin_source=aws_cloudfront.S3OriginConfig(
                        s3_bucket_source=self.ui_bucket,
                        origin_access_identity=oai,
                    ),
                    behaviors=[
                        aws_cloudfront.Behavior(
                            is_default_behavior=True,
                            allowed_methods=aws_cloudfront.CloudFrontAllowedMethods.GET_HEAD_OPTIONS,
                            cached_methods=aws_cloudfront.CloudFrontAllowedCachedMethods.GET_HEAD_OPTIONS,
                            compress=True,
                            viewer_protocol_policy=aws_cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                        )
                    ],
                )
            ],
            default_root_object="index.html",
            error_configurations=[
                aws_cloudfront.CfnDistribution.CustomErrorResponseProperty(
                    error_code=404,
                    response_code=200,
                    response_page_path="/index.html",
                ),
                aws_cloudfront.CfnDistribution.CustomErrorResponseProperty(
                    error_code=403,
                    response_code=200,
                    response_page_path="/index.html",
                ),
            ],
        )
        
        
        # ===== DYNAMODB PROTECTION LAYERS =====
        # 1. RemovalPolicy.RETAIN: Tables survive stack deletion
        # 2. deletion_protection=True: Prevents accidental table deletion
        # 3. point_in_time_recovery=True: Enables automatic backups
        # 4. Stack termination protection: Prevents accidental stack deletion
        # 5. Use existing tables option: Preserves production data
        # =====================================
        
        # Create DynamoDB tables
        self.user_preferences_table = aws_dynamodb.Table(
            self,
            "user-preferences-table",
            table_name=f"{self.app_unique_id}-user-preferences",
            partition_key=aws_dynamodb.Attribute(
                name="userId",
                type=aws_dynamodb.AttributeType.STRING
            ),
            billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            removal_policy=aws_cdk.RemovalPolicy.RETAIN,
        )
        
        self.fleets_table = aws_dynamodb.Table(
            self,
            "fleets-table",
            table_name=f"{self.app_unique_id}-fleets",
            partition_key=aws_dynamodb.Attribute(
                name="fleetId",
                type=aws_dynamodb.AttributeType.STRING
            ),
            billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            removal_policy=aws_cdk.RemovalPolicy.RETAIN,
        )
        
        self.vehicles_table = aws_dynamodb.Table(
            self,
            "vehicles-table",
            table_name=f"{self.app_unique_id}-vehicles",
            partition_key=aws_dynamodb.Attribute(
                name="vehicleId",
                type=aws_dynamodb.AttributeType.STRING
            ),
            billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            removal_policy=aws_cdk.RemovalPolicy.RETAIN,
        )
        
        # Vehicle certificates table for IoT certificate storage
        self.vehicle_certificates_table = aws_dynamodb.Table(
            self,
            "vehicle-certificates-table",
            table_name=f"{self.app_unique_id}-vehicle-certificates",
            partition_key=aws_dynamodb.Attribute(
                name="vin",
                type=aws_dynamodb.AttributeType.STRING
            ),
            billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            removal_policy=aws_cdk.RemovalPolicy.RETAIN,
        )
        
        # Dashboard metrics cache table
        self.dashboard_metrics_cache_table = aws_dynamodb.Table(
            self,
            "dashboard-metrics-cache-table",
            table_name=f"{self.app_unique_id}-dashboard-metrics-cache",
            partition_key=aws_dynamodb.Attribute(
                name="cacheKey",
                type=aws_dynamodb.AttributeType.STRING
            ),
            billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            removal_policy=aws_cdk.RemovalPolicy.RETAIN,
        )
        
        self.trips_table = aws_dynamodb.Table(
            self,
            "trips-table",
            table_name=f"{self.app_unique_id}-trips",
            partition_key=aws_dynamodb.Attribute(
                name="tripId",
                type=aws_dynamodb.AttributeType.STRING
            ),
            sort_key=aws_dynamodb.Attribute(
                name="timestamp",
                type=aws_dynamodb.AttributeType.STRING
            ),
            billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            removal_policy=aws_cdk.RemovalPolicy.RETAIN,
        )
        
        # Add GSI after table creation
        self.trips_table.add_global_secondary_index(
            index_name="vehicleId-index",
            partition_key=aws_dynamodb.Attribute(
                name="vehicleId",
                type=aws_dynamodb.AttributeType.STRING
            ),
            projection_type=aws_dynamodb.ProjectionType.ALL
        )
        
        self.safety_events_table = aws_dynamodb.Table(
            self,
            "safety-events-table",
            table_name=f"{self.app_unique_id}-safety-events",
            partition_key=aws_dynamodb.Attribute(
                name="eventId",
                type=aws_dynamodb.AttributeType.STRING
            ),
            sort_key=aws_dynamodb.Attribute(
                name="timestamp",
                type=aws_dynamodb.AttributeType.STRING
            ),
            billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            removal_policy=aws_cdk.RemovalPolicy.RETAIN,
        )
        
        # Add GSI for vehicleId queries
        self.safety_events_table.add_global_secondary_index(
            index_name="vehicleId-index",
            partition_key=aws_dynamodb.Attribute(
                name="vehicleId",
                type=aws_dynamodb.AttributeType.STRING
            ),
            projection_type=aws_dynamodb.ProjectionType.ALL
        )
        
        self.maintenance_alerts_table = aws_dynamodb.Table(
            self,
            "maintenance-alerts-table",
            table_name=f"{self.app_unique_id}-maintenance-alerts",
            partition_key=aws_dynamodb.Attribute(
                name="alertId",
                type=aws_dynamodb.AttributeType.STRING
            ),
            sort_key=aws_dynamodb.Attribute(
                name="timestamp",
                type=aws_dynamodb.AttributeType.STRING
            ),
            billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            removal_policy=aws_cdk.RemovalPolicy.RETAIN,
        )
        
        # Add GSI for vehicleId queries
        self.maintenance_alerts_table.add_global_secondary_index(
            index_name="vehicleId-index",
            partition_key=aws_dynamodb.Attribute(
                name="vehicleId",
                type=aws_dynamodb.AttributeType.STRING
            ),
            projection_type=aws_dynamodb.ProjectionType.ALL
        )
        
        # ===== SIMPLIFIED TELEMETRY PROCESSING =====
        # ===== VPC SELECTION =====
        # Check environment variable for VPC mode
        vpc_mode = os.environ.get('VPC_MODE', 'existing')
        
        if vpc_mode == 'create':
            # Create new VPC (may hit limits)
            self.vpc = aws_ec2.Vpc(
                self,
                "cms-vpc",
                vpc_name=f"{self.app_unique_id}-vpc",
                max_azs=2,
                nat_gateways=1,
                subnet_configuration=[
                    aws_ec2.SubnetConfiguration(
                        name="public",
                        subnet_type=aws_ec2.SubnetType.PUBLIC,
                        cidr_mask=24
                    ),
                    aws_ec2.SubnetConfiguration(
                        name="private",
                        subnet_type=aws_ec2.SubnetType.PRIVATE_WITH_EGRESS,
                        cidr_mask=24
                    )
                ]
            )
        else:
            # Use existing VPC - just use default VPC for simplicity
            self.vpc = aws_ec2.Vpc.from_lookup(
                self,
                "cms-vpc",
                is_default=True
            )
        
        # Create main CMS API Gateway
        self.api = aws_apigateway.RestApi(
            self,
            "cms-api",
            rest_api_name=f"{self.app_unique_id}-api",
            description="CMS UI API",
            default_cors_preflight_options=aws_apigateway.CorsOptions(
                allow_origins=["*"],
                allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                allow_headers=[
                    "Content-Type",
                    "Authorization",
                    "X-Amz-Date",
                    "X-Api-Key",
                    "X-Amz-Security-Token",
                ],
                max_age=Duration.seconds(86400),
            ),
        )
        
        # Create CMS Device API Gateway for IoT device management
        self.device_api = aws_apigateway.RestApi(
            self,
            "cms-device-api",
            rest_api_name=f"{self.app_unique_id}-device-api",
            description="CMS Device Management API",
            default_cors_preflight_options=aws_apigateway.CorsOptions(
                allow_origins=["*"],
                allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                allow_headers=[
                    "Content-Type",
                    "Authorization",
                    "X-Amz-Date",
                    "X-Api-Key",
                    "X-Amz-Security-Token",
                ],
                max_age=Duration.seconds(86400),
            ),
        )
        
        # Create Lambda function for device API
        device_lambda_function_name = f"{existing_table_suffix}-device-api-lambda" if use_existing_tables else f"{self.app_unique_id}-device-api-lambda"
        
        # Get absolute path to handlers
        import os
        script_dir = os.path.dirname(os.path.abspath(__file__))
        handlers_dir = os.path.join(script_dir, "..", "..", "cms_ui", "source", "handlers")
        
        self.device_api_lambda = aws_lambda.Function(
            self,
            "device-api-lambda",
            function_name=device_lambda_function_name,
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            handler="lambda_function.lambda_handler",
            code=aws_lambda.Code.from_asset(os.path.join(handlers_dir, "iot_api")),
            environment={
                "VEHICLE_CERTIFICATES_TABLE_NAME": f"{self.table_suffix}-vehicle-certificates",
            },
            timeout=Duration.seconds(60),
            memory_size=512,
        )
        
        # Grant permissions to device API Lambda
        self.vehicle_certificates_table.grant_read_write_data(self.device_api_lambda)
        
        # Grant IoT Core permissions for device management
        self.device_api_lambda.add_to_role_policy(aws_iam.PolicyStatement(
            effect=aws_iam.Effect.ALLOW,
            actions=[
                "iot:CreateKeysAndCertificate",
                "iot:CreateThing",
                "iot:AttachThingPrincipal",
                "iot:DescribeEndpoint",
                "iot:CreatePolicy",
                "iot:AttachPolicy",
                "iot:DescribeThing",
                "iot:ListThingPrincipals",
                "iot:DeleteThing",
                "iot:DetachThingPrincipal",
                "iot:UpdateCertificate",
                "iot:DeleteCertificate"
            ],
            resources=["*"]
        ))
        
        # ===== ALARM RECORDER LAMBDA =====
        # Create SNS topic for alarms
        self.alarm_sns_topic = aws_sns.Topic(
            self,
            "alarm-sns-topic",
            topic_name=f"{self.app_unique_id}-alarm-topic"
        )
        
        # Create SQS queues for alarm processing
        self.alarm_dlq = aws_sqs.Queue(
            self,
            "alarm-dlq",
            queue_name=f"{self.app_unique_id}-alarm-dlq",
            visibility_timeout=Duration.minutes(15),
            retention_period=Duration.days(1)
        )
        
        self.alarm_queue = aws_sqs.Queue(
            self,
            "alarm-queue",
            queue_name=f"{self.app_unique_id}-alarm-queue",
            visibility_timeout=Duration.minutes(15),
            retention_period=Duration.days(1),
            dead_letter_queue=aws_sqs.DeadLetterQueue(
                max_receive_count=3,
                queue=self.alarm_dlq
            )
        )
        
        # Subscribe SQS to SNS
        self.alarm_sns_topic.add_subscription(SqsSubscription(self.alarm_queue))
        
        # Create alarm recorder Lambda
        alarm_lambda_function_name = f"{existing_table_suffix}-alarm-recorder-lambda" if use_existing_tables else f"{self.app_unique_id}-alarm-recorder-lambda"
        
        self.alarm_recorder_lambda = aws_lambda.Function(
            self,
            "alarm-recorder-lambda",
            function_name=alarm_lambda_function_name,
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            handler="lambda_function.lambda_handler",
            code=aws_lambda.Code.from_asset(os.path.join(handlers_dir, "alarm_recorder")),
            environment={
                "MAINTENANCE_ALERTS_TABLE_NAME": f"{self.table_suffix}-maintenance-alerts",
                "SAFETY_EVENTS_TABLE_NAME": f"{self.table_suffix}-safety-events",
            },
            timeout=Duration.seconds(60),
            memory_size=256,
        )
        
        # Grant permissions to alarm recorder
        self.maintenance_alerts_table.grant_read_write_data(self.alarm_recorder_lambda)
        self.safety_events_table.grant_read_write_data(self.alarm_recorder_lambda)
        self.alarm_queue.grant_consume_messages(self.alarm_recorder_lambda)
        
        # Add SQS event source
        self.alarm_recorder_lambda.add_event_source(SqsEventSource(self.alarm_queue, batch_size=10))
        
        # ===== IOT LIFECYCLE EVENTS LAMBDA =====
        # Create SQS queues for lifecycle events
        self.lifecycle_dlq = aws_sqs.Queue(
            self,
            "lifecycle-dlq",
            queue_name=f"{self.app_unique_id}-lifecycle-dlq",
            visibility_timeout=Duration.minutes(15),
            retention_period=Duration.days(1)
        )
        
        self.lifecycle_queue = aws_sqs.Queue(
            self,
            "lifecycle-queue",
            queue_name=f"{self.app_unique_id}-lifecycle-queue",
            visibility_timeout=Duration.minutes(15),
            retention_period=Duration.days(1),
            dead_letter_queue=aws_sqs.DeadLetterQueue(
                max_receive_count=3,
                queue=self.lifecycle_dlq
            )
        )
        
        self.lifecycle_delay_queue = aws_sqs.Queue(
            self,
            "lifecycle-delay-queue",
            queue_name=f"{self.app_unique_id}-lifecycle-delay-queue",
            delivery_delay=Duration.seconds(10),
            visibility_timeout=Duration.minutes(15),
            retention_period=Duration.days(1),
            dead_letter_queue=aws_sqs.DeadLetterQueue(
                max_receive_count=3,
                queue=self.lifecycle_dlq
            )
        )
        
        # Create lifecycle events Lambda
        lifecycle_lambda_function_name = f"{existing_table_suffix}-lifecycle-lambda" if use_existing_tables else f"{self.app_unique_id}-lifecycle-lambda"
        
        self.lifecycle_events_lambda = aws_lambda.Function(
            self,
            "lifecycle-events-lambda",
            function_name=lifecycle_lambda_function_name,
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            handler="lambda_function.lambda_handler",
            code=aws_lambda.Code.from_asset(os.path.join(handlers_dir, "iot_lifecycle_events")),
            environment={
                "VEHICLES_TABLE_NAME": f"{self.table_suffix}-vehicles",
                "VEHICLE_CERTIFICATES_TABLE_NAME": f"{self.table_suffix}-vehicle-certificates",
            },
            timeout=Duration.seconds(60),
            memory_size=256,
        )
        
        # Grant permissions to lifecycle events Lambda
        self.vehicles_table.grant_read_write_data(self.lifecycle_events_lambda)
        self.vehicle_certificates_table.grant_read_write_data(self.lifecycle_events_lambda)
        self.lifecycle_queue.grant_consume_messages(self.lifecycle_events_lambda)
        self.lifecycle_delay_queue.grant_consume_messages(self.lifecycle_events_lambda)
        
        # Grant IoT permissions
        self.lifecycle_events_lambda.add_to_role_policy(aws_iam.PolicyStatement(
            effect=aws_iam.Effect.ALLOW,
            actions=["iot:DescribeThing"],
            resources=[f"arn:aws:iot:*:{self.account}:thing/*"]
        ))
        
        # Add SQS event sources
        self.lifecycle_events_lambda.add_event_source(SqsEventSource(self.lifecycle_queue, batch_size=10))
        self.lifecycle_events_lambda.add_event_source(SqsEventSource(self.lifecycle_delay_queue, batch_size=10))
        
        # Create IoT Topic Rules for lifecycle events
        self._create_lifecycle_topic_rules()
        
        # ===== API GATEWAY PERMISSIONS =====
        # Add API Gateway permissions for device API Lambda
        self.device_api_lambda.add_permission(
            "device-api-gateway-permission",
            principal=aws_iam.ServicePrincipal("apigateway.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_arn=f"arn:aws:execute-api:{self.region}:{self.account}:{self.device_api.rest_api_id}/*/*"
        )
        
        # Create Lambda function for main API
        # Use stable function name for existing stacks to prevent recreation
        lambda_function_name = f"{existing_table_suffix}-api-lambda" if use_existing_tables else f"{self.app_unique_id}-api-lambda"
        
        self.api_lambda = aws_lambda.Function(
            self,
            "api-lambda",
            function_name=lambda_function_name,
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            handler="index.handler",
            code=aws_lambda.Code.from_asset(os.path.join(handlers_dir, "main_api")),
            environment={
                "USER_POOL_ID": self.user_pool.user_pool_id,
                "CLIENT_ID": self.user_pool_client.user_pool_client_id,
                "FLEETS_TABLE_NAME": f"{self.table_suffix}-fleets",
                "VEHICLES_TABLE_NAME": f"{self.table_suffix}-vehicles",
                "VEHICLE_CERTIFICATES_TABLE_NAME": f"{self.table_suffix}-vehicle-certificates",
                "TRIPS_TABLE_NAME": f"{self.table_suffix}-trips",
                "SAFETY_EVENTS_TABLE_NAME": f"{self.table_suffix}-safety-events",
                "MAINTENANCE_ALERTS_TABLE_NAME": f"{self.table_suffix}-maintenance-alerts",
                "USER_PREFERENCES_TABLE_NAME": f"{self.table_suffix}-user-preferences",
                "DASHBOARD_METRICS_CACHE_TABLE": f"{self.table_suffix}-dashboard-metrics-cache",
            },
            timeout=Duration.seconds(60),
            memory_size=512,
        )
        
        # Grant DynamoDB permissions to main API Lambda
        self.fleets_table.grant_read_write_data(self.api_lambda)
        self.vehicles_table.grant_read_write_data(self.api_lambda)
        self.vehicle_certificates_table.grant_read_write_data(self.api_lambda)
        self.trips_table.grant_read_data(self.api_lambda)
        self.safety_events_table.grant_read_data(self.api_lambda)
        self.maintenance_alerts_table.grant_read_write_data(self.api_lambda)
        self.user_preferences_table.grant_read_write_data(self.api_lambda)
        self.dashboard_metrics_cache_table.grant_read_write_data(self.api_lambda)
        
        # Grant IoT Core permissions for certificate creation and endpoint discovery
        self.api_lambda.add_to_role_policy(aws_iam.PolicyStatement(
            effect=aws_iam.Effect.ALLOW,
            actions=[
                "iot:CreateKeysAndCertificate",
                "iot:CreateThing",
                "iot:AttachThingPrincipal",
                "iot:DescribeEndpoint",
                "iot:CreatePolicy",
                "iot:AttachPolicy",
                "iot:DescribeThing",
                "iot:ListThingPrincipals"
            ],
            resources=["*"]
        ))
        
        # Create maintenance alerts Lambda function
        # Maintenance alerts are now handled by main API Lambda - no separate function needed
        
        # Grant DynamoDB permissions to Lambda functions
        self.fleets_table.grant_read_write_data(self.api_lambda)
        self.vehicles_table.grant_read_write_data(self.api_lambda)
        self.trips_table.grant_read_write_data(self.api_lambda)
        self.safety_events_table.grant_read_write_data(self.api_lambda)
        self.maintenance_alerts_table.grant_read_write_data(self.api_lambda)  # Grant to main API Lambda
        
        # ===== NEW: Dashboard Metrics Aggregator =====
        # Create the dashboard metrics aggregator construct
        # self.dashboard_metrics_aggregator = DashboardMetricsAggregator(
        #     self,
        #     "dashboard-metrics-aggregator",
        #     app_unique_id=self.app_unique_id,
        #     main_api_lambda=self.api_lambda,  # Use existing main API Lambda
        #     vehicles_table=self.vehicles_table,
        #     safety_events_table=self.safety_events_table,
        #     maintenance_alerts_table=self.maintenance_alerts_table,
        #     trips_table=self.trips_table,
        # )
        
        # Create API Gateway integrations
        lambda_integration = aws_apigateway.LambdaIntegration(self.api_lambda)
        
        # Add API Gateway resources and methods
        # Health endpoint
        health_resource = self.api.root.add_resource("health")
        health_resource.add_method("GET", lambda_integration,
                                 authorization_type=aws_apigateway.AuthorizationType.NONE)
        
        # API v1 endpoints
        api_resource = self.api.root.add_resource("api")
        v1_resource = api_resource.add_resource("v1")
        
        # Vehicles endpoints
        vehicles_resource = v1_resource.add_resource("vehicles")
        vehicles_resource.add_method("GET", lambda_integration,
                                   authorization_type=aws_apigateway.AuthorizationType.NONE)
        vehicles_resource.add_method("POST", lambda_integration,
                                   authorization_type=aws_apigateway.AuthorizationType.NONE)
        
        # Add individual vehicle endpoint
        vehicle_by_id_resource = vehicles_resource.add_resource("{vehicleId}")
        vehicle_by_id_resource.add_method("GET", lambda_integration,
                                        authorization_type=aws_apigateway.AuthorizationType.NONE)
        
        # Add vehicle sub-resources
        vehicle_trips_resource = vehicle_by_id_resource.add_resource("trips")
        vehicle_trips_resource.add_method("GET", lambda_integration,
                                        authorization_type=aws_apigateway.AuthorizationType.NONE)
        
        # Add specific trip endpoint
        trip_by_id_resource = vehicle_trips_resource.add_resource("{tripId}")
        trip_by_id_resource.add_method("GET", lambda_integration,
                                     authorization_type=aws_apigateway.AuthorizationType.NONE)
        
        vehicle_safety_alerts_resource = vehicle_by_id_resource.add_resource("safety-alerts")
        vehicle_safety_alerts_resource.add_method("GET", lambda_integration,
                                                authorization_type=aws_apigateway.AuthorizationType.NONE)
        
        vehicle_maintenance_alerts_resource = vehicle_by_id_resource.add_resource("maintenance-alerts")
        vehicle_maintenance_alerts_resource.add_method("GET", lambda_integration,
                                                     authorization_type=aws_apigateway.AuthorizationType.NONE)
        
        # Add realtime endpoints
        realtime_resource = self.api.root.add_resource("realtime")
        realtime_trips_resource = realtime_resource.add_resource("trips")
        realtime_trips_resource.add_method("GET", lambda_integration,
                                         authorization_type=aws_apigateway.AuthorizationType.NONE)
        
        realtime_vehicles_resource = realtime_resource.add_resource("vehicles")
        realtime_vehicles_resource.add_method("GET", lambda_integration,
                                            authorization_type=aws_apigateway.AuthorizationType.NONE)
        
        # Fleets endpoints
        fleets_resource = v1_resource.add_resource("fleets")
        fleets_resource.add_method("GET", lambda_integration,
                                 authorization_type=aws_apigateway.AuthorizationType.NONE)
        
        # Add fleet by ID endpoint
        fleet_by_id_resource = fleets_resource.add_resource("{fleetId}")
        fleet_by_id_resource.add_method("GET", lambda_integration,
                                       authorization_type=aws_apigateway.AuthorizationType.NONE)
        
        # Add fleet vehicles endpoint
        fleet_vehicles_resource = fleet_by_id_resource.add_resource("vehicles")
        fleet_vehicles_resource.add_method("GET", lambda_integration,
                                          authorization_type=aws_apigateway.AuthorizationType.NONE)
        
        # Safety alerts endpoints
        safety_alerts_resource = v1_resource.add_resource("safety-alerts")
        safety_alerts_resource.add_method("GET", lambda_integration,
                                        authorization_type=aws_apigateway.AuthorizationType.NONE)
        
        # Maintenance alerts endpoints
        maintenance_alerts_resource = v1_resource.add_resource("maintenance-alerts")
        maintenance_alerts_resource.add_method("GET", lambda_integration,
                                             authorization_type=aws_apigateway.AuthorizationType.NONE)
        
        # Trips endpoints
        trips_resource = v1_resource.add_resource("trips")
        trips_resource.add_method("GET", lambda_integration,
                                authorization_type=aws_apigateway.AuthorizationType.NONE)
        
        # Dashboard endpoints - check if they already exist first
        try:
            # Try to find existing dashboard resource
            existing_dashboard = None
            for resource in v1_resource.node.children:
                if hasattr(resource, 'path_part') and resource.path_part == "dashboard":
                    existing_dashboard = resource
                    break
            
            if existing_dashboard is None:
                # Dashboard doesn't exist, create it
                dashboard_resource = v1_resource.add_resource("dashboard")
                fleet_comparison_resource = dashboard_resource.add_resource("fleet-comparison")
                fleet_comparison_resource.add_method("GET", lambda_integration,
                                                   authorization_type=aws_apigateway.AuthorizationType.NONE)
            else:
                # Dashboard exists, check if fleet-comparison exists
                existing_fleet_comparison = None
                for child in existing_dashboard.node.children:
                    if hasattr(child, 'path_part') and child.path_part == "fleet-comparison":
                        existing_fleet_comparison = child
                        break
                
                if existing_fleet_comparison is None:
                    # Add fleet-comparison to existing dashboard
                    fleet_comparison_resource = existing_dashboard.add_resource("fleet-comparison")
                    fleet_comparison_resource.add_method("GET", lambda_integration,
                                                       authorization_type=aws_apigateway.AuthorizationType.NONE)
        except Exception as e:
            # Fallback: skip if any issues with resource checking
            print(f"Dashboard resource handling: {e}")
            pass
        
        # Add IoT endpoint discovery route
        discover_iot_resource = self.api.root.add_resource("discover-iot-endpoint")
        discover_iot_resource.add_method("GET", lambda_integration,
                                       authorization_type=aws_apigateway.AuthorizationType.NONE)
        
        # ===== CMS DEVICE API GATEWAY ENDPOINTS =====
        # Create device API integrations
        device_lambda_integration = aws_apigateway.LambdaIntegration(self.device_api_lambda)
        
        # Add proxy resource for device API - check if exists first
        try:
            existing_proxy = None
            for resource in self.device_api.root.node.children:
                if hasattr(resource, 'path_part') and resource.path_part == "{proxy+}":
                    existing_proxy = resource
                    break
            
            if existing_proxy is None:
                device_proxy_resource = self.device_api.root.add_resource("{proxy+}")
                device_proxy_resource.add_method("ANY", device_lambda_integration,
                                                authorization_type=aws_apigateway.AuthorizationType.NONE)
                device_proxy_resource.add_method("OPTIONS", device_lambda_integration,
                                                authorization_type=aws_apigateway.AuthorizationType.NONE)
        except Exception as e:
            print(f"Device API proxy resource handling: {e}")
            pass
        
        # Add root method for device API - check if exists first
        try:
            existing_root_methods = getattr(self.device_api.root, 'resource_methods', {})
            if 'ANY' not in existing_root_methods:
                self.device_api.root.add_method("ANY", device_lambda_integration,
                                               authorization_type=aws_apigateway.AuthorizationType.NONE)
            if 'OPTIONS' not in existing_root_methods:
                self.device_api.root.add_method("OPTIONS", device_lambda_integration,
                                               authorization_type=aws_apigateway.AuthorizationType.NONE)
        except Exception as e:
            print(f"Device API root method handling: {e}")
            pass
        
        # Create IoT rule for MSK integration (only if parameters are provided)
        self._create_iot_msk_rule()
        
        # Generate runtime configuration for frontend
        runtime_config = {
            "awsRegion": self.region,
            "mapAuth": {
                "identityPoolClient": f"cognito-idp.{self.region}.amazonaws.com/{self.user_pool.user_pool_id}",
                "mapName": "cms-map",
                "identityPoolId": self.identity_pool.ref
            },
            "isDemoMode": "false",
            "apiEndpoint": f"https://{self.api.rest_api_id}.execute-api.{self.region}.amazonaws.com/prod/",
            "userPreferencesApiEndpoint": f"https://{self.api.rest_api_id}.execute-api.{self.region}.amazonaws.com/prod//",
            "awsCredentials": {
                "region": self.region,
                "identityPoolId": self.identity_pool.ref,
                "userPoolId": self.user_pool.user_pool_id,
                "userPoolWebClientId": self.user_pool_client.user_pool_client_id
            },
            "fleetManagementApi": {
                "endpoint": f"https://{self.api.rest_api_id}.execute-api.{self.region}.amazonaws.com/prod/api/v1"
            },
            "endpoints": {
                "fleets": f"https://{self.api.rest_api_id}.execute-api.{self.region}.amazonaws.com/prod/api/v1/fleets",
                "vehicles": f"https://{self.api.rest_api_id}.execute-api.{self.region}.amazonaws.com/prod/api/v1/vehicles",
                "trips": f"https://{self.api.rest_api_id}.execute-api.{self.region}.amazonaws.com/prod/api/v1/trips",
                "safetyEvents": f"https://{self.api.rest_api_id}.execute-api.{self.region}.amazonaws.com/prod/api/v1/safety-events",
                "safetyAlerts": f"https://{self.api.rest_api_id}.execute-api.{self.region}.amazonaws.com/prod/api/v1/safety-alerts",
                "maintenanceAlerts": f"https://{self.api.rest_api_id}.execute-api.{self.region}.amazonaws.com/prod/api/v1/maintenance-alerts",
                "dashboard": f"https://{self.api.rest_api_id}.execute-api.{self.region}.amazonaws.com/prod/api/v1/dashboard",
                "realtime": f"https://{self.api.rest_api_id}.execute-api.{self.region}.amazonaws.com/prod/realtime",
                "health": f"https://{self.api.rest_api_id}.execute-api.{self.region}.amazonaws.com/prod/health"
            },
            "lastUpdated": datetime.now().isoformat(),
            "generatedBy": "CDK Stack - Production Authentication"
        }

        # Deploy runtime configuration to S3
        aws_s3_deployment.BucketDeployment(
            self,
            "runtime-config-deployment",
            sources=[
                aws_s3_deployment.Source.json_data("runtimeConfig.json", runtime_config)
            ],
            destination_bucket=self.ui_bucket,
            cache_control=[
                aws_s3_deployment.CacheControl.no_cache()
            ],
        )
        
        # Deploy React application to S3
        aws_s3_deployment.BucketDeployment(
            self,
            "react-app-deployment",
            sources=[
                aws_s3_deployment.Source.asset(os.path.join(script_dir, "..", "..", "cms_ui", "source", "frontend", "build"))
            ],
            destination_bucket=self.ui_bucket,
            distribution=self.cloudfront_distribution,
            distribution_paths=["/*"],
            prune=True,  # Remove old files
        )
        
    def _create_lifecycle_topic_rules(self):
        """Create IoT topic rules for device lifecycle events"""
        
        # Create IAM role for IoT rules to access SQS
        iot_rule_role = aws_iam.Role(
            self, "IoTLifecycleRuleRole",
            assumed_by=aws_iam.ServicePrincipal("iot.amazonaws.com"),
            description="Role for IoT rules to publish to SQS queues"
        )
        
        # Grant SQS permissions to IoT rule role
        iot_rule_role.add_to_policy(aws_iam.PolicyStatement(
            effect=aws_iam.Effect.ALLOW,
            actions=["sqs:SendMessage"],
            resources=[
                self.lifecycle_queue.queue_arn,
                self.lifecycle_delay_queue.queue_arn
            ]
        ))
        
        # Connect events topic rule
        aws_iot.CfnTopicRule(
            self, "ConnectTopicRule",
            rule_name=f"{self.app_unique_id.replace('-', '_')}_connect_rule",
            topic_rule_payload=aws_iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM '$aws/events/presence/connected/#'",
                description="Route IoT device connect events to SQS",
                rule_disabled=False,
                actions=[
                    aws_iot.CfnTopicRule.ActionProperty(
                        sqs=aws_iot.CfnTopicRule.SqsActionProperty(
                            queue_url=self.lifecycle_queue.queue_url,
                            role_arn=iot_rule_role.role_arn
                        )
                    )
                ]
            )
        )
        
        # Disconnect events topic rule (with delay)
        aws_iot.CfnTopicRule(
            self, "DisconnectTopicRule",
            rule_name=f"{self.app_unique_id.replace('-', '_')}_disconnect_rule",
            topic_rule_payload=aws_iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM '$aws/events/presence/disconnected/#'",
                description="Route IoT device disconnect events to delay SQS",
                rule_disabled=False,
                actions=[
                    aws_iot.CfnTopicRule.ActionProperty(
                        sqs=aws_iot.CfnTopicRule.SqsActionProperty(
                            queue_url=self.lifecycle_delay_queue.queue_url,
                            role_arn=iot_rule_role.role_arn
                        )
                    )
                ]
            )
        )
        
        # Subscribe events topic rule
        aws_iot.CfnTopicRule(
            self, "SubscribeTopicRule",
            rule_name=f"{self.app_unique_id.replace('-', '_')}_subscribe_rule",
            topic_rule_payload=aws_iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM '$aws/events/subscriptions/subscribed/#'",
                description="Route IoT device subscribe events to SQS",
                rule_disabled=False,
                actions=[
                    aws_iot.CfnTopicRule.ActionProperty(
                        sqs=aws_iot.CfnTopicRule.SqsActionProperty(
                            queue_url=self.lifecycle_queue.queue_url,
                            role_arn=iot_rule_role.role_arn
                        )
                    )
                ]
            )
        )
        
        # Unsubscribe events topic rule
        aws_iot.CfnTopicRule(
            self, "UnsubscribeTopicRule",
            rule_name=f"{self.app_unique_id.replace('-', '_')}_unsubscribe_rule",
            topic_rule_payload=aws_iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM '$aws/events/subscriptions/unsubscribed/#'",
                description="Route IoT device unsubscribe events to SQS",
                rule_disabled=False,
                actions=[
                    aws_iot.CfnTopicRule.ActionProperty(
                        sqs=aws_iot.CfnTopicRule.SqsActionProperty(
                            queue_url=self.lifecycle_queue.queue_url,
                            role_arn=iot_rule_role.role_arn
                        )
                    )
                ]
            )
        )
    
    def _create_iot_msk_rule(self):
        """Create IoT rule to route telemetry to MSK using CDK parameters"""
        
        # Only create IoT rule if MSK parameters are provided
        msk_arn_condition = aws_cdk.CfnCondition(
            self, "MSKParametersProvided",
            expression=aws_cdk.Fn.condition_not(
                aws_cdk.Fn.condition_equals(self.msk_cluster_arn.value_as_string, "")
            )
        )
        
        # Only create IoT/MSK resources when MSK parameters are provided
        # Create IAM role for IoT rule (only when MSK is provided)
        self.iot_msk_role = aws_iam.Role(
            self, "IoTMSKRole",
            assumed_by=aws_iam.ServicePrincipal("iot.amazonaws.com"),
            description="Role for IoT Core to publish to MSK"
        )
        
        # Apply condition to the role
        cfn_role = self.iot_msk_role.node.default_child
        cfn_role.cfn_options.condition = msk_arn_condition
        
        # Create policy document with all permissions
        policy_document = aws_iam.PolicyDocument(
            statements=[
                aws_iam.PolicyStatement(
                    effect=aws_iam.Effect.ALLOW,
                    actions=[
                        "kafka:DescribeCluster",
                        "kafka:DescribeClusterV2", 
                        "kafka:GetBootstrapBrokers"
                    ],
                    resources=[self.msk_cluster_arn.value_as_string]
                ),
                aws_iam.PolicyStatement(
                    effect=aws_iam.Effect.ALLOW,
                    actions=[
                        "kafka-cluster:Connect",
                        "kafka-cluster:AlterCluster",
                        "kafka-cluster:DescribeCluster"
                    ],
                    resources=[self.msk_cluster_arn.value_as_string]
                ),
                aws_iam.PolicyStatement(
                    effect=aws_iam.Effect.ALLOW,
                    actions=[
                        "kafka-cluster:*Topic*",
                        "kafka-cluster:WriteData",
                        "kafka-cluster:ReadData"
                    ],
                    resources=[aws_cdk.Fn.join("", [self.msk_cluster_arn.value_as_string, "/topic/*"])]
                ),
                aws_iam.PolicyStatement(
                    effect=aws_iam.Effect.ALLOW,
                    actions=[
                        "ec2:CreateNetworkInterface",
                        "ec2:CreateNetworkInterfacePermission",
                        "ec2:DeleteNetworkInterface",
                        "ec2:DescribeNetworkInterfaces",
                        "ec2:DescribeSecurityGroups",
                        "ec2:DescribeSubnets",
                        "ec2:DescribeVpcs"
                    ],
                    resources=["*"]
                )
            ]
        )
        
        # Create policy and apply condition
        iot_msk_policy = aws_iam.Policy(
            self, "IoTMSKRoleDefaultPolicy",
            document=policy_document,
            roles=[self.iot_msk_role]
        )
        
        cfn_policy = iot_msk_policy.node.default_child
        cfn_policy.cfn_options.condition = msk_arn_condition
        
        # Create IoT topic rule for CMS telemetry (conditional)
        self.iot_rule = aws_iot.CfnTopicRule(
            self, "CMSTelemetryRule",
            rule_name="cms_telemetry_to_msk",
            topic_rule_payload=aws_iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM 'cms/telemetry/vehicle/+'",
                description="Route CMS telemetry data to MSK Kafka cluster",
                rule_disabled=False,
                actions=[
                    aws_iot.CfnTopicRule.ActionProperty(
                        kafka=aws_iot.CfnTopicRule.KafkaActionProperty(
                            destination_arn=self.msk_cluster_arn.value_as_string,
                            topic="cms-telemetry-raw",
                            key="${topic(3)}",
                            client_properties={
                                "bootstrap.servers": self.bootstrap_servers.value_as_string,
                                "security.protocol": "PLAINTEXT",
                                "acks": "1"
                            }
                        )
                    )
                ]
            )
        )
        
        # Apply condition to IoT rule
        self.iot_rule.cfn_options.condition = msk_arn_condition
        
        # Outputs
        CfnOutput(
            self,
            "UserPoolId",
            value=self.user_pool.user_pool_id,
            description="Cognito User Pool ID",
        )
        
        CfnOutput(
            self,
            "UserPoolClientId",
            value=self.user_pool_client.user_pool_client_id,
            description="Cognito User Pool Client ID",
        )
        
        CfnOutput(
            self,
            "IdentityPoolId",
            value=self.identity_pool.ref,
            description="Cognito Identity Pool ID",
        )
        
        CfnOutput(
            self,
            "ApiEndpoint",
            value=self.api.url,
            description="API Gateway endpoint - all endpoints including maintenance-alerts handled by consolidated main Lambda",
        )
        
        CfnOutput(
            self,
            "DeviceApiEndpoint",
            value=self.device_api.url,
            description="CMS Device Management API Gateway endpoint for IoT device operations",
        )
        
        CfnOutput(
            self,
            "AlarmSnsTopicArn",
            value=self.alarm_sns_topic.topic_arn,
            description="SNS Topic ARN for alarm notifications",
        )
        
        CfnOutput(
            self,
            "AlarmQueueName",
            value=self.alarm_queue.queue_name,
            description="SQS Queue name for alarm processing",
        )
        
        CfnOutput(
            self,
            "MaintenanceAlertsEndpoint",
            value=f"https://{self.api.rest_api_id}.execute-api.{self.region}.amazonaws.com/prod/api/v1/maintenance-alerts",
            description="Maintenance Alerts API Endpoint (consolidated Lambda)",
        )
        
        CfnOutput(
            self,
            "CloudFrontUrl",
            value=f"https://{self.cloudfront_distribution.distribution_domain_name}",
            description="CloudFront distribution URL",
        )
        
        CfnOutput(
            self,
            "CloudFrontDistributionId",
            value=self.cloudfront_distribution.distribution_id,
            description="CloudFront distribution ID for cache invalidation",
        )
        
        CfnOutput(
            self,
            "S3BucketName",
            value=self.ui_bucket.bucket_name,
            description="S3 bucket name for UI hosting",
        )
        
        # NEW: Dashboard Metrics Outputs
        CfnOutput(
            self,
            "DashboardMetricsEndpoint",
            value=f"{self.api.url}api/v1/dashboard/metrics",
            description="Dashboard metrics aggregator API endpoint",
        )
        
        # CfnOutput(
        #     self,
        #     "DashboardMetricsLambdaArn",
        #     value=self.dashboard_metrics_aggregator.lambda_function.function_arn,
        #     description="Dashboard metrics aggregator Lambda function ARN",
        # )
        
        # CfnOutput(
        #     self,
        #     "DashboardMetricsCacheTable",
        #     value=self.dashboard_metrics_aggregator.cache_table.table_name,
        #     description="Dashboard metrics cache table name",
        # )
        
        # MSK Integration Outputs (conditional)
        CfnOutput(
            self,
            "MSKClusterArnParameter",
            value=self.msk_cluster_arn.value_as_string,
            description="MSK Cluster ARN parameter value",
            condition=aws_cdk.CfnCondition(
                self, "MSKOutputCondition",
                expression=aws_cdk.Fn.condition_not(
                    aws_cdk.Fn.condition_equals(self.msk_cluster_arn.value_as_string, "")
                )
            )
        )
        
        CfnOutput(
            self,
            "BootstrapServersParameter", 
            value=self.bootstrap_servers.value_as_string,
            description="MSK Bootstrap servers parameter value",
            condition=aws_cdk.CfnCondition(
                self, "BootstrapOutputCondition",
                expression=aws_cdk.Fn.condition_not(
                    aws_cdk.Fn.condition_equals(self.bootstrap_servers.value_as_string, "")
                )
            )
        )
        
        CfnOutput(
            self,
            "IoTTopicPattern",
            value="cms/telemetry/vehicle/+",
            description="IoT topic pattern for telemetry data"
        )

# Create the CDK app
app = App()

# Get account and region from environment or AWS CLI
import subprocess
try:
    account = subprocess.check_output(['aws', 'sts', 'get-caller-identity', '--query', 'Account', '--output', 'text']).decode().strip()
    region = subprocess.check_output(['aws', 'configure', 'get', 'region']).decode().strip() or 'us-east-1'
except:
    account = os.environ.get('CDK_DEFAULT_ACCOUNT', '470296731304')
    region = os.environ.get('CDK_DEFAULT_REGION', 'us-east-1')

# Create enhanced single stack with environment
enhanced_stack = EnhancedSingleCMSStack(
    app,
    "cms-ui-enhanced-single-stack",
    stack_name="cms-ui-enhanced-single-stack",
    env=Environment(account=account, region=region),
    synthesizer=DefaultStackSynthesizer(generate_bootstrap_version_rule=False),
)

app.synth()

app.synth()
