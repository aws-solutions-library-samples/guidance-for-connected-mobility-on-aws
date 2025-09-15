"""
UI Stack - Frontend, API Gateway, and Cognito authentication
"""

from aws_cdk import (
    Stack,
    aws_cognito as cognito,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_apigateway as apigateway,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_dynamodb as dynamodb,
    CfnOutput,
    Duration
)
from constructs import Construct
from typing import Dict

class UIStack(Stack):
    
    def __init__(self, scope: Construct, construct_id: str,
                 storage_tables: Dict[str, dynamodb.Table], **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Cognito User Pool
        self.user_pool = cognito.UserPool(
            self, "CMSUserPool",
            user_pool_name=f"{construct_id}-users",
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True
            )
        )
        
        # User Pool Client
        self.user_pool_client = cognito.UserPoolClient(
            self, "CMSUserPoolClient",
            user_pool=self.user_pool,
            generate_secret=False,
            auth_flows=cognito.AuthFlow(
                user_password=True,
                user_srp=True
            )
        )
        
        # Identity Pool
        self.identity_pool = cognito.CfnIdentityPool(
            self, "CMSIdentityPool",
            identity_pool_name=f"{construct_id}-identity",
            allow_unauthenticated_identities=False,
            cognito_identity_providers=[
                cognito.CfnIdentityPool.CognitoIdentityProviderProperty(
                    client_id=self.user_pool_client.user_pool_client_id,
                    provider_name=self.user_pool.user_pool_provider_name
                )
            ]
        )
        
        # S3 bucket for frontend hosting
        self.frontend_bucket = s3.Bucket(
            self, "FrontendBucket",
            bucket_name=f"{construct_id}-frontend-{self.account}",
            website_index_document="index.html",
            website_error_document="error.html",
            public_read_access=True,
            block_public_access=s3.BlockPublicAccess(
                block_public_acls=False,
                block_public_policy=False,
                ignore_public_acls=False,
                restrict_public_buckets=False
            )
        )
        
        # CloudFront distribution
        self.distribution = cloudfront.Distribution(
            self, "FrontendDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin(self.frontend_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED
            ),
            default_root_object="index.html",
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html"
                )
            ]
        )
        
        # Lambda execution role
        lambda_role = iam.Role(
            self, "LambdaExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ]
        )
        
        # Grant DynamoDB access to Lambda
        for table in storage_tables.values():
            table.grant_read_write_data(lambda_role)
        
        # API Lambda functions
        self.api_functions = {}
        
        # Fleet management API
        self.api_functions['fleet'] = lambda_.Function(
            self, "FleetAPIFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="index.handler",
            role=lambda_role,
            code=lambda_.Code.from_inline("""
import json
import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource('dynamodb')

def handler(event, context):
    # Basic fleet API implementation
    method = event['httpMethod']
    path = event['path']
    
    if method == 'GET' and path == '/fleet':
        # Return fleet data
        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'vehicles': []})
        }
    
    return {
        'statusCode': 404,
        'body': json.dumps({'error': 'Not found'})
    }
            """),
            environment={
                'FLEETS_TABLE': storage_tables['fleets'].table_name,
                'TELEMETRY_TABLE': storage_tables['telemetry'].table_name
            },
            timeout=Duration.seconds(30)
        )
        
        # API Gateway
        self.api = apigateway.RestApi(
            self, "CMSAPI",
            rest_api_name=f"{construct_id}-api",
            description="CMS API Gateway",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=apigateway.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "Authorization"]
            )
        )
        
        # API resources
        fleet_resource = self.api.root.add_resource("fleet")
        fleet_resource.add_method(
            "GET",
            apigateway.LambdaIntegration(self.api_functions['fleet'])
        )
        
        # Outputs
        CfnOutput(
            self, "UserPoolId",
            value=self.user_pool.user_pool_id,
            export_name=f"{construct_id}-user-pool-id"
        )
        
        CfnOutput(
            self, "UserPoolClientId",
            value=self.user_pool_client.user_pool_client_id,
            export_name=f"{construct_id}-user-pool-client-id"
        )
        
        CfnOutput(
            self, "IdentityPoolId",
            value=self.identity_pool.ref,
            export_name=f"{construct_id}-identity-pool-id"
        )
        
        CfnOutput(
            self, "CloudFrontURL",
            value=f"https://{self.distribution.distribution_domain_name}",
            export_name=f"{construct_id}-cloudfront-url"
        )
        
        CfnOutput(
            self, "APIEndpoint",
            value=self.api.url,
            export_name=f"{construct_id}-api-endpoint"
        )
