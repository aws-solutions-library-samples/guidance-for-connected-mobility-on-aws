"""
UI Stack - Frontend, API Gateway, and Cognito authentication
"""

import os
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
    aws_location as location,
    custom_resources as custom_resource,
    CfnOutput,
    Duration,
    Size
)
from constructs import Construct
from typing import Dict
import time
import json

class UIStack(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, 
                 storage_tables: Dict[str, dynamodb.Table] = None,
                 redis_endpoint: str = None,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Amazon Location Services resources
        self.map = location.CfnMap(
            self, "CMSVehicleMap",
            map_name="cms-vehicle-map",
            configuration=location.CfnMap.MapConfigurationProperty(
                style="VectorEsriStreets"
            ),
            description="Map for Connected Mobility Solution vehicle tracking",
            pricing_plan="RequestBasedUsage"
        )
        
        self.place_index = location.CfnPlaceIndex(
            self, "CMSPlaceIndex",
            index_name="cms-place-index",
            data_source="Esri",
            description="Place index for Connected Mobility Solution",
            pricing_plan="RequestBasedUsage"
        )
        
        # Route calculator - reference existing one instead of creating new
        # The simulator expects 'cms-route-calculator' to exist
        self.route_calculator_name = "cms-route-calculator"
        
        # Use actual table names from storage stack (with suffixes)
        table_names = {
            'fleets': storage_tables['fleets'].table_name,
            'vehicles': storage_tables['vehicles'].table_name,
            'trips': storage_tables['trips'].table_name,
            'telemetry': storage_tables['telemetry'].table_name,
            'safety_events': storage_tables['safety_events'].table_name,
            'maintenance_events': storage_tables['maintenance_events'].table_name,
            'user_preferences': storage_tables['user_preferences'].table_name,
            'dashboard_metrics_cache': storage_tables['dashboard_metrics_cache'].table_name,
            'vehicle_certificates': storage_tables['vehicle_certificates'].table_name,
            'drivers': storage_tables['drivers'].table_name,
            'service_history': storage_tables['service_history'].table_name
        }
        
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
            allow_unauthenticated_identities=True,
            cognito_identity_providers=[
                cognito.CfnIdentityPool.CognitoIdentityProviderProperty(
                    client_id=self.user_pool_client.user_pool_client_id,
                    provider_name=self.user_pool.user_pool_provider_name
                )
            ]
        )
        
        # IAM role for unauthenticated users to access Location Services
        unauthenticated_role = iam.Role(
            self, "CognitoUnauthenticatedRole",
            assumed_by=iam.FederatedPrincipal(
                "cognito-identity.amazonaws.com",
                {
                    "StringEquals": {
                        "cognito-identity.amazonaws.com:aud": self.identity_pool.ref
                    },
                    "ForAnyValue:StringLike": {
                        "cognito-identity.amazonaws.com:amr": "unauthenticated"
                    }
                },
                "sts:AssumeRoleWithWebIdentity"
            ),
            inline_policies={
                "LocationServicesPolicy": iam.PolicyDocument(
                    statements=[
                        # New geo-maps actions for maps
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "geo-maps:GetTile",
                                "geo-maps:GetStaticMap"
                            ],
                            resources=[
                                "arn:aws:geo-maps:us-east-1::provider/default",
                                "arn:aws:geo-maps:us-east-1::provider/default/*"
                            ]
                        ),
                        # Legacy geo actions for backward compatibility
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "geo:GetMap*",
                                "geo:DescribeMap"
                            ],
                            resources=[
                                self.map.attr_arn
                            ]
                        )
                    ]
                )
            }
        )
        
        # IAM role for authenticated users to access Location Services
        authenticated_role = iam.Role(
            self, "CognitoAuthenticatedRole",
            assumed_by=iam.FederatedPrincipal(
                "cognito-identity.amazonaws.com",
                {
                    "StringEquals": {
                        "cognito-identity.amazonaws.com:aud": self.identity_pool.ref
                    },
                    "ForAnyValue:StringLike": {
                        "cognito-identity.amazonaws.com:amr": "authenticated"
                    }
                },
                "sts:AssumeRoleWithWebIdentity"
            ),
            inline_policies={
                "LocationServicesPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "geo:GetMap*",
                                "geo:DescribeMap",
                                "geo:SearchPlaceIndex*",
                                "geo:GetPlace",
                                "geo:CalculateRoute*"
                            ],
                            resources=[
                                f"arn:aws:geo:{self.region}:{self.account}:map/*",
                                f"arn:aws:geo:{self.region}:{self.account}:place-index/*",
                                f"arn:aws:geo:{self.region}:{self.account}:route-calculator/*"
                            ]
                        )
                    ]
                )
            }
        )
        
        # Attach roles to identity pool
        cognito.CfnIdentityPoolRoleAttachment(
            self, "IdentityPoolRoleAttachment",
            identity_pool_id=self.identity_pool.ref,
            roles={
                "authenticated": authenticated_role.role_arn,
                "unauthenticated": unauthenticated_role.role_arn
            }
        )
        
        # Private S3 bucket (secure)
        self.frontend_bucket = s3.Bucket(
            self, "FrontendBucket",
            bucket_name=f"{construct_id}-frontend-{self.account}-{int(time.time())}",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            public_read_access=False
        )
        
        # Origin Access Control for secure CloudFront access
        # Generate unique OAC name with stack ID suffix
        import hashlib
        stack_hash = hashlib.md5(self.stack_id.encode() if hasattr(self, 'stack_id') else construct_id.encode()).hexdigest()[:8]
        oac = cloudfront.CfnOriginAccessControl(
            self, "FrontendOAC",
            origin_access_control_config=cloudfront.CfnOriginAccessControl.OriginAccessControlConfigProperty(
                name=f"{construct_id}-oac-{stack_hash}",
                origin_access_control_origin_type="s3",
                signing_behavior="always",
                signing_protocol="sigv4"
            )
        )
        
        # CloudFront distribution with OAC
        self.distribution = cloudfront.Distribution(
            self, "FrontendDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin(
                    self.frontend_bucket,
                    origin_access_control_id=oac.attr_id
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED
            ),
            default_root_object="index.html",
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html"
                ),
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html"
                )
            ]
        )
        
        # Bucket policy to allow CloudFront OAC access
        bucket_policy = iam.PolicyDocument(
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    principals=[iam.ServicePrincipal("cloudfront.amazonaws.com")],
                    actions=["s3:GetObject"],
                    resources=[f"{self.frontend_bucket.bucket_arn}/*"],
                    conditions={
                        "StringEquals": {
                            "AWS:SourceArn": f"arn:aws:cloudfront::{self.account}:distribution/{self.distribution.distribution_id}"
                        }
                    }
                )
            ]
        )
        
        s3.CfnBucketPolicy(
            self, "FrontendBucketPolicy",
            bucket=self.frontend_bucket.bucket_name,
            policy_document=bucket_policy
        )
        
        # Lambda execution role with minimal permissions
        lambda_role = iam.Role(
            self, "LambdaExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ],
            inline_policies={
                "DynamoDBAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "dynamodb:GetItem",
                                "dynamodb:PutItem", 
                                "dynamodb:UpdateItem",
                                "dynamodb:DeleteItem",
                                "dynamodb:Query",
                                "dynamodb:Scan"
                            ],
                            resources=[
                                f"arn:aws:dynamodb:{self.region}:{self.account}:table/*"
                            ]
                        )
                    ]
                ),
                "IoTAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "iot:CreateThing",
                                "iot:CreateKeysAndCertificate",
                                "iot:CreatePolicy",
                                "iot:AttachThingPrincipal",
                                "iot:AttachPrincipalPolicy"
                            ],
                            resources=["*"]
                        )
                    ]
                )
            }
        )
        
        # API Lambda functions
        self.api_functions = {}
        
        # Fleet management API
        self.api_functions['fleet'] = lambda_.Function(
            self, "FleetAPIFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="index.handler",
            role=lambda_role,
            code=lambda_.Code.from_asset("../modules/cms_ui/source/handlers/main_api"),
            environment={
                'FLEETS_TABLE_NAME': table_names['fleets'],
                'VEHICLES_TABLE_NAME': table_names['vehicles'],
                'TRIPS_TABLE_NAME': table_names['trips'],
                'TELEMETRY_TABLE_NAME': table_names['telemetry'],
                'SAFETY_EVENTS_TABLE_NAME': table_names['safety_events'],
                'MAINTENANCE_ALERTS_TABLE_NAME': table_names['maintenance_events'],
                'USER_PREFERENCES_TABLE_NAME': table_names['user_preferences'],
                'DASHBOARD_METRICS_CACHE_TABLE': table_names['dashboard_metrics_cache'],
                'VEHICLE_CERTIFICATES_TABLE_NAME': table_names['vehicle_certificates'],
                'DRIVERS_TABLE_NAME': table_names['drivers'],
                'SERVICE_HISTORY_TABLE_NAME': storage_tables['service_history'].table_name,
                'USER_POOL_ID': self.user_pool.user_pool_id,
                'CLIENT_ID': self.user_pool_client.user_pool_client_id,
                'REDIS_ENDPOINT': redis_endpoint if redis_endpoint else ''  # Use parameter or empty
            },
            timeout=Duration.seconds(60),
            memory_size=512
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
        
        # API resources - match target-account structure
        api_resource = self.api.root.add_resource("api")
        v1_resource = api_resource.add_resource("v1")
        
        # Health endpoint
        health_resource = self.api.root.add_resource("health")
        health_resource.add_method("GET", apigateway.LambdaIntegration(self.api_functions['fleet']))
        
        # Realtime endpoints
        realtime_resource = self.api.root.add_resource("realtime")
        realtime_vehicles = realtime_resource.add_resource("vehicles")
        realtime_vehicles.add_method("GET", apigateway.LambdaIntegration(self.api_functions['fleet']))
        realtime_trips = realtime_resource.add_resource("trips")
        realtime_trips.add_method("GET", apigateway.LambdaIntegration(self.api_functions['fleet']))
        
        # IoT endpoint
        iot_endpoint = self.api.root.add_resource("discover-iot-endpoint")
        iot_endpoint.add_method("GET", apigateway.LambdaIntegration(self.api_functions['fleet']))
        
        # Fleets endpoints
        fleets_resource = v1_resource.add_resource("fleets")
        fleets_resource.add_method("GET", apigateway.LambdaIntegration(self.api_functions['fleet']))
        fleets_resource.add_method("POST", apigateway.LambdaIntegration(self.api_functions['fleet']))
        
        # Fleet by ID endpoint
        fleet_id_resource = fleets_resource.add_resource("{fleetId}")
        fleet_id_resource.add_method("GET", apigateway.LambdaIntegration(self.api_functions['fleet']))
        
        # Fleet vehicles endpoint
        fleet_vehicles = fleet_id_resource.add_resource("vehicles")
        fleet_vehicles.add_method("GET", apigateway.LambdaIntegration(self.api_functions['fleet']))
        
        # Vehicles endpoints
        vehicles_resource = v1_resource.add_resource("vehicles")
        vehicles_resource.add_method("GET", apigateway.LambdaIntegration(self.api_functions['fleet']))
        vehicles_resource.add_method("POST", apigateway.LambdaIntegration(self.api_functions['fleet']))
        
        # Vehicle locations
        vehicle_locations = vehicles_resource.add_resource("locations")
        vehicle_locations.add_method("GET", apigateway.LambdaIntegration(self.api_functions['fleet']))
        
        # Vehicle by ID endpoints
        vehicle_id_resource = vehicles_resource.add_resource("{vehicleId}")
        vehicle_id_resource.add_method("GET", apigateway.LambdaIntegration(self.api_functions['fleet']))
        
        # Vehicle trips
        vehicle_trips = vehicle_id_resource.add_resource("trips")
        vehicle_trips.add_method("GET", apigateway.LambdaIntegration(self.api_functions['fleet']))
        
        # Vehicle trip by ID
        trip_id_resource = vehicle_trips.add_resource("{tripId}")
        trip_id_resource.add_method("GET", apigateway.LambdaIntegration(self.api_functions['fleet']))
        
        # Vehicle safety alerts
        vehicle_safety = vehicle_id_resource.add_resource("safety-alerts")
        vehicle_safety.add_method("GET", apigateway.LambdaIntegration(self.api_functions['fleet']))
        
        # Vehicle maintenance alerts
        vehicle_maintenance = vehicle_id_resource.add_resource("maintenance-alerts")
        vehicle_maintenance.add_method("GET", apigateway.LambdaIntegration(self.api_functions['fleet']))
        
        # Global trips endpoint
        trips_resource = v1_resource.add_resource("trips")
        trips_resource.add_method("GET", apigateway.LambdaIntegration(self.api_functions['fleet']))
        
        # Global safety alerts
        safety_alerts = v1_resource.add_resource("safety-alerts")
        safety_alerts.add_method("GET", apigateway.LambdaIntegration(self.api_functions['fleet']))
        
        # Global maintenance alerts
        maintenance_alerts = v1_resource.add_resource("maintenance-alerts")
        maintenance_alerts.add_method("GET", apigateway.LambdaIntegration(self.api_functions['fleet']))
        
        # Dashboard endpoints
        dashboard_resource = v1_resource.add_resource("dashboard")
        dashboard_metrics = dashboard_resource.add_resource("metrics")
        dashboard_metrics.add_method("GET", apigateway.LambdaIntegration(self.api_functions['fleet']))
        dashboard_comparison = dashboard_resource.add_resource("fleet-comparison")
        dashboard_comparison.add_method("GET", apigateway.LambdaIntegration(self.api_functions['fleet']))
        
        # Proxy for any other endpoints
        proxy_resource = self.api.root.add_resource("{proxy+}")
        proxy_resource.add_method("ANY", apigateway.LambdaIntegration(self.api_functions['fleet']))
        
        # Create dynamic runtime config after API is created
        runtime_config = {
            "awsRegion": "us-east-1",
            "mapAuth": {
                "identityPoolClient": f"cognito-idp.us-east-1.amazonaws.com/{self.user_pool.user_pool_id}",
                "mapName": self.map.map_name,
                "identityPoolId": self.identity_pool.ref
            },
            "locationServices": {
                "mapName": self.map.map_name,
                "placeIndexName": self.place_index.index_name, 
                "routeCalculatorName": self.route_calculator_name,
                "region": "us-east-1",
                "enabled": True
            },
            "isDemoMode": "false",
            "apiEndpoint": self.api.url,
            "userPreferencesApiEndpoint": self.api.url,
            "awsCredentials": {
                "region": "us-east-1",
                "identityPoolId": self.identity_pool.ref,
                "userPoolId": self.user_pool.user_pool_id,
                "userPoolWebClientId": self.user_pool_client.user_pool_client_id
            }
        }
        
        # Deploy frontend assets from the built React app
        frontend_deployment = s3deploy.BucketDeployment(
            self, "FrontendDeployment",
            sources=[
                s3deploy.Source.asset("../modules/cms_ui/source/frontend/build"),
                s3deploy.Source.json_data("runtimeConfig.json", runtime_config)
            ],
            destination_bucket=self.frontend_bucket,
            distribution=self.distribution,
            distribution_paths=["/*"],
            memory_limit=512,
            ephemeral_storage_size=Size.mebibytes(1024)
        )
        
        # Create default user with custom resource
        default_user_email = "FleetManager@example.com"
        default_password = "FleetManager123!"
        
        default_user_resource = custom_resource.AwsCustomResource(
            self, "DefaultUserResource",
            on_create=custom_resource.AwsSdkCall(
                service="CognitoIdentityServiceProvider",
                action="adminCreateUser",
                parameters={
                    "UserPoolId": self.user_pool.user_pool_id,
                    "Username": default_user_email,
                    "MessageAction": "SUPPRESS",
                    "TemporaryPassword": default_password,
                    "UserAttributes": [
                        {"Name": "email", "Value": default_user_email},
                        {"Name": "email_verified", "Value": "true"}
                    ]
                },
                physical_resource_id=custom_resource.PhysicalResourceId.of("default-user-create")
            ),
            policy=custom_resource.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    actions=[
                        "cognito-idp:AdminCreateUser",
                        "cognito-idp:AdminSetUserPassword"
                    ],
                    resources=[self.user_pool.user_pool_arn]
                )
            ])
        )
        
        # Set permanent password after user creation
        set_password_resource = custom_resource.AwsCustomResource(
            self, "SetPermanentPasswordResource",
            on_create=custom_resource.AwsSdkCall(
                service="CognitoIdentityServiceProvider",
                action="adminSetUserPassword",
                parameters={
                    "UserPoolId": self.user_pool.user_pool_id,
                    "Username": default_user_email,
                    "Password": default_password,
                    "Permanent": True
                },
                physical_resource_id=custom_resource.PhysicalResourceId.of("default-user-password")
            ),
            policy=custom_resource.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    actions=["cognito-idp:AdminSetUserPassword"],
                    resources=[self.user_pool.user_pool_arn]
                )
            ])
        )
        
        # Ensure password is set after user creation
        set_password_resource.node.add_dependency(default_user_resource)
        
        # Location Services resources are created above
        
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
        
        # Default user credentials
        CfnOutput(
            self, "DefaultUserEmail",
            value=default_user_email,
            description="Default user email for Fleet Manager login"
        )
        
        CfnOutput(
            self, "DefaultUserPassword", 
            value=default_password,
            description="Default user password for Fleet Manager login"
        )
        
        CfnOutput(
            self, "RouteCalculatorName",
            value=self.route_calculator_name,
            description="Location Services route calculator name for telemetry simulation"
        )
        
        # Location Services outputs
        CfnOutput(
            self, "LocationServicesMapName",
            value=self.map.map_name,
            description="Amazon Location Services Map name",
            export_name=f"{construct_id}-map-name"
        )
        
        CfnOutput(
            self, "LocationServicesPlaceIndexName", 
            value=self.place_index.index_name,
            description="Amazon Location Services Place Index name",
            export_name=f"{construct_id}-place-index-name"
        )
