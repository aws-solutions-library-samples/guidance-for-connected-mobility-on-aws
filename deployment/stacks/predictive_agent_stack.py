"""
Predictive Maintenance Agent CDK Stack

Deploys the predictive maintenance agent infrastructure including:
- Lambda functions for agent processing
- SageMaker endpoints for ML models
- EventBridge rules for integration
- API Gateway for external access
"""

import os
from aws_cdk import (
    Fn,
    Stack,
    Duration,
    aws_cognito as cognito,
    aws_lambda as _lambda,
    aws_apigateway as apigateway,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_sagemaker as sagemaker,
    aws_s3 as s3,
    aws_logs as logs,
    RemovalPolicy
)
from constructs import Construct
from typing import Dict, Any


class PredictiveAgentStack(Stack):
    """
    CDK Stack for Predictive Maintenance Agent
    """
    
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self._construct_id = construct_id
        self._deployment_stage = os.environ.get("DEPLOYMENT_STAGE", "dev")

        # Create IAM role for agent Lambda functions
        self.agent_role = self._create_agent_role()
        
        # Create Lambda functions
        self.agent_lambda = self._create_agent_lambda()
        self.scheduler_lambda = self._create_scheduler_lambda()
        
        # Create API Gateway
        self.api = self._create_api_gateway()
        
        # Create EventBridge rules
        self._create_eventbridge_rules()
        
        # Create CloudWatch log groups
        self._create_log_groups()
        
        # Create S3 bucket for model artifacts
        self.model_bucket = self._create_model_bucket()
    
    def _create_agent_role(self) -> iam.Role:
        """Create IAM role for agent Lambda functions"""
        
        role = iam.Role(
            self, "PredictiveAgentRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ]
        )
        
        # Add permissions for CMS platform integration
        role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "dynamodb:GetItem",
                "dynamodb:Query",
                "dynamodb:Scan",
                "dynamodb:UpdateItem",
                "dynamodb:PutItem"
            ],
            resources=[
                f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-*-storage-*",
                f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-*-storage-*/index/*"
            ]
        ))
        
        # Add permissions for S3 data lake access
        role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "s3:GetObject",
                "s3:PutObject",
                "s3:ListBucket"
            ],
            resources=[
                f"arn:aws:s3:::cms-*-datalake",
                f"arn:aws:s3:::cms-*-datalake/*"
            ]
        ))
        
        # Add permissions for EventBridge
        role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "events:PutEvents"
            ],
            resources=[
                f"arn:aws:events:{self.region}:{self.account}:event-bus/*"
            ]
        ))
        
        # Add permissions for SageMaker inference
        role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "sagemaker:InvokeEndpoint"
            ],
            resources=[
                f"arn:aws:sagemaker:{self.region}:{self.account}:endpoint/predictive-agent-*"
            ]
        ))
        
        return role
    
    def _create_agent_lambda(self) -> _lambda.Function:
        """Create main agent Lambda function"""
        
        agent_lambda = _lambda.Function(
            self, "PredictiveAgentFunction",
            runtime=_lambda.Runtime.PYTHON_3_9,
            handler="agent_handler.lambda_handler",
            code=_lambda.Code.from_asset("../modules/predictive_agent"),
            role=self.agent_role,
            timeout=Duration.minutes(15),
            memory_size=1024,
            environment={
                "ENVIRONMENT": "dev",
                "LOG_LEVEL": "INFO",
            },
            description="Predictive Maintenance Agent - Main Processing Function"
        )
        
        return agent_lambda
    
    def _create_scheduler_lambda(self) -> _lambda.Function:
        """Create maintenance scheduler Lambda function"""
        
        scheduler_lambda = _lambda.Function(
            self, "MaintenanceSchedulerFunction",
            runtime=_lambda.Runtime.PYTHON_3_9,
            handler="scheduler_handler.lambda_handler",
            code=_lambda.Code.from_asset("../modules/predictive_agent"),
            role=self.agent_role,
            timeout=Duration.minutes(10),
            memory_size=512,
            environment={
                "ENVIRONMENT": "dev",
                "LOG_LEVEL": "INFO",
            },
            description="Predictive Maintenance Agent - Scheduler Function"
        )
        
        return scheduler_lambda
    
    def _create_api_gateway(self) -> apigateway.RestApi:
        """Create API Gateway for agent access"""
        
        api = apigateway.RestApi(
            self, "PredictiveAgentAPI",
            rest_api_name="Predictive Maintenance Agent API",
            description="API for predictive maintenance agent operations",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=apigateway.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "Authorization"]
            )
        )

        # Cognito authorizer — cross-stack import from ui_stack export
        ui_stack_construct_id = f"cms-{self._deployment_stage}-ui"
        user_pool = cognito.UserPool.from_user_pool_arn(
            self, "ImportedUserPool",
            user_pool_arn=Fn.import_value(f"{ui_stack_construct_id}-user-pool-arn"),
        )
        cognito_authorizer = apigateway.CognitoUserPoolsAuthorizer(
            self, "PredictiveAgentCognitoAuthorizer",
            cognito_user_pools=[user_pool],
            authorizer_name=f"{self._construct_id}-cognito-auth",
        )
        auth_kwargs = {
            "authorizer": cognito_authorizer,
            "authorization_type": apigateway.AuthorizationType.COGNITO,
        }

        # Create API resources and methods
        
        # /vehicles/{vehicle_id}/analysis
        vehicles_resource = api.root.add_resource("vehicles")
        vehicle_resource = vehicles_resource.add_resource("{vehicle_id}")
        analysis_resource = vehicle_resource.add_resource("analysis")
        
        analysis_integration = apigateway.LambdaIntegration(
            self.agent_lambda,
            request_templates={
                "application/json": '{"vehicle_id": "$input.params(\'vehicle_id\')", "action": "analyze"}'
            }
        )
        
        analysis_resource.add_method("POST", analysis_integration, **auth_kwargs)
        analysis_resource.add_method("GET", analysis_integration, **auth_kwargs)
        
        # /fleet/analysis
        fleet_resource = api.root.add_resource("fleet")
        fleet_analysis_resource = fleet_resource.add_resource("analysis")
        
        fleet_integration = apigateway.LambdaIntegration(
            self.agent_lambda,
            request_templates={
                "application/json": '{"action": "analyze_fleet"}'
            }
        )
        
        fleet_analysis_resource.add_method("POST", fleet_integration, **auth_kwargs)
        
        # /maintenance/schedule
        maintenance_resource = api.root.add_resource("maintenance")
        schedule_resource = maintenance_resource.add_resource("schedule")
        
        schedule_integration = apigateway.LambdaIntegration(
            self.scheduler_lambda,
            request_templates={
                "application/json": '{"action": "schedule"}'
            }
        )
        
        schedule_resource.add_method("POST", schedule_integration, **auth_kwargs)
        schedule_resource.add_method("GET", schedule_integration, **auth_kwargs)
        
        return api
    
    def _create_eventbridge_rules(self):
        """Create EventBridge rules for agent triggers"""
        
        # Rule for scheduled fleet analysis
        fleet_analysis_rule = events.Rule(
            self, "FleetAnalysisSchedule",
            description="Trigger fleet analysis every 6 hours",
            schedule=events.Schedule.rate(Duration.hours(6))
        )
        
        fleet_analysis_rule.add_target(
            targets.LambdaFunction(
                self.agent_lambda,
                event=events.RuleTargetInput.from_object({
                    "action": "scheduled_fleet_analysis",
                    "source": "eventbridge_schedule"
                })
            )
        )
        
        # Rule for high-priority vehicle monitoring
        priority_monitoring_rule = events.Rule(
            self, "PriorityVehicleMonitoring",
            description="Monitor high-priority vehicles every 30 minutes",
            schedule=events.Schedule.rate(Duration.minutes(30))
        )
        
        priority_monitoring_rule.add_target(
            targets.LambdaFunction(
                self.agent_lambda,
                event=events.RuleTargetInput.from_object({
                    "action": "priority_vehicle_analysis",
                    "source": "eventbridge_schedule"
                })
            )
        )
        
        # Rule for responding to CMS telemetry events
        telemetry_rule = events.Rule(
            self, "TelemetryEventRule",
            description="Process new telemetry data",
            event_pattern=events.EventPattern(
                source=["cms.telemetry"],
                detail_type=["Vehicle Telemetry Update"]
            )
        )
        
        telemetry_rule.add_target(
            targets.LambdaFunction(
                self.agent_lambda,
                event=events.RuleTargetInput.from_object({
                    "action": "process_telemetry_event",
                    "source": "cms_telemetry"
                })
            )
        )
    
    def _create_log_groups(self):
        """Create CloudWatch log groups"""
        
        # Agent Lambda log group
        logs.LogGroup(
            self, "AgentLogGroup",
            log_group_name=f"/aws/lambda/{self.agent_lambda.function_name}",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY
        )
        
        # Scheduler Lambda log group
        logs.LogGroup(
            self, "SchedulerLogGroup",
            log_group_name=f"/aws/lambda/{self.scheduler_lambda.function_name}",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY
        )
        
        # Agent application log group
        logs.LogGroup(
            self, "AgentApplicationLogGroup",
            log_group_name="/aws/predictive-agent/application",
            retention=logs.RetentionDays.THREE_MONTHS,
            removal_policy=RemovalPolicy.DESTROY
        )
    
    def _create_model_bucket(self) -> s3.Bucket:
        """Create S3 bucket for ML model artifacts"""
        
        bucket = s3.Bucket(
            self, "ModelArtifactsBucket",
            bucket_name=f"predictive-agent-models-{self.account}-{self.region}",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )
        
        # Grant agent Lambda access to model bucket
        bucket.grant_read_write(self.agent_role)
        
        return bucket