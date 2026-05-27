"""
Data Processing Stack
Provides Signal Catalog, Transform Manifests, and Data Source Configuration
"""

from aws_cdk import (
    Stack,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_lambda as lambda_,
    aws_apigateway as apigw,
    aws_cognito as cognito,
    aws_iam as iam,
    RemovalPolicy,
    Duration,
    CfnOutput
)
from constructs import Construct
import os

class DataProcessingStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        
        deployment_stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')

        # Optional Cognito lock-down for the data-processing API.
        #
        # When `CMS_USER_POOL_ID` is set in the deploy env, we attach a
        # Cognito User Pool authorizer to the API Gateway and require
        # every method to authenticate. When unset (first-time deploy,
        # before the UI stack has created the pool), the API stays
        # open — that's the bootstrap path. The Makefile's
        # `data-processing` target auto-detects the UI stack's pool ID
        # after first deploy and re-runs the data-processing stack
        # with the env var set, flipping auth on.
        #
        # `CMS_EXTRA_USER_POOL_IDS` (comma-separated) lets future iOS-
        # only or partner pools also access the API — same pattern
        # as the VSA stack's `EXTRA_USER_POOL_IDS`.
        cms_user_pool_id = os.environ.get('CMS_USER_POOL_ID', '').strip()
        cms_extra_pool_ids = [
            p.strip() for p in os.environ.get('CMS_EXTRA_USER_POOL_IDS', '').split(',')
            if p.strip()
        ]
        
        # ===================================================================
        # 1. Signal Catalog Table
        # ===================================================================
        self.signal_catalog_table = dynamodb.Table(
            self, 'SignalCatalog',
            table_name=f'cms-{deployment_stage}-signal-catalog',
            partition_key=dynamodb.Attribute(
                name='signal_group',
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name='signal_name',
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
        )
        
        # Add GSIs
        self.signal_catalog_table.add_global_secondary_index(
            index_name='signal-name-index',
            partition_key=dynamodb.Attribute(
                name='signal_name',
                type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL
        )
        
        self.signal_catalog_table.add_global_secondary_index(
            index_name='status-index',
            partition_key=dynamodb.Attribute(
                name='status',
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name='signal_name',
                type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL
        )
        
        # ===================================================================
        # 1b. Event Catalog Table
        # ===================================================================
        self.event_catalog_table = dynamodb.Table(
            self, 'EventCatalog',
            table_name=f'cms-{deployment_stage}-event-catalog',
            partition_key=dynamodb.Attribute(
                name='event_id',
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )
        
        # ===================================================================
        # 1c. Decoder Manifest Table
        # ===================================================================
        self.decoder_manifest_table = dynamodb.Table(
            self, 'DecoderManifest',
            table_name=f'cms-{deployment_stage}-decoder-manifest',
            partition_key=dynamodb.Attribute(
                name='pk',
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name='sk',
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # ===================================================================
        # 1c2. Model Manifest Table (FleetWise Vehicle Model)
        #
        # A vehicle model defines which signals a given vehicle platform emits.
        # Mirrors the decoder-manifest schema (pk=MODEL#{name}#{version},
        # sk=MODEL#{name}). Created out-of-band 2026-05-20 for the Acme Motors
        # demo and reconciled into CDK here. RETAIN policy preserves seeded
        # data through stack updates — the cms-prod-data-processing stack does
        # not own the demo seed lifecycle.
        # ===================================================================
        self.model_manifest_table = dynamodb.Table(
            self, 'ModelManifest',
            table_name=f'cms-{deployment_stage}-model-manifest',
            partition_key=dynamodb.Attribute(
                name='pk',
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name='sk',
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )
        
        # ===================================================================
        # 1d. Campaigns Table
        # ===================================================================
        self.campaigns_table = dynamodb.Table(
            self, 'Campaigns',
            table_name=f'cms-{deployment_stage}-campaigns',
            partition_key=dynamodb.Attribute(
                name='campaignId',
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )
        self.campaigns_table.add_global_secondary_index(
            index_name='status-index',
            partition_key=dynamodb.Attribute(
                name='status',
                type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL
        )
        self.campaigns_table.add_global_secondary_index(
            index_name='targetArn-index',
            partition_key=dynamodb.Attribute(
                name='targetArn',
                type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL
        )
        self.campaigns_table.add_global_secondary_index(
            index_name='sourceFleetId-index',
            partition_key=dynamodb.Attribute(
                name='sourceFleetId',
                type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL
        )
        
        # ===================================================================
        # 2. Data Source Configurations Table
        # ===================================================================
        self.data_source_configs_table = dynamodb.Table(
            self, 'DataSourceConfigs',
            table_name=f'cms-{deployment_stage}-data-source-configs',
            partition_key=dynamodb.Attribute(
                name='source_id',
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
        )
        
        # Add GSI
        self.data_source_configs_table.add_global_secondary_index(
            index_name='source-type-index',
            partition_key=dynamodb.Attribute(
                name='source_type',
                type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL
        )
        
        # ===================================================================
        # 3. Transform Manifests S3 Bucket
        # ===================================================================
        self.manifests_bucket = s3.Bucket(
            self, 'TransformManifests',
            bucket_name=f'cms-{deployment_stage}-transform-manifests-{self.region}-{self.account}',
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    noncurrent_version_expiration=Duration.days(90)
                )
            ]
        )
        
        # ===================================================================
        # 4. Upload Default Manifests to S3
        # ===================================================================
        s3deploy.BucketDeployment(
            self, 'DefaultManifests',
            sources=[
                s3deploy.Source.asset('../services/data_processing/manifests')
            ],
            destination_bucket=self.manifests_bucket,
            destination_key_prefix='manifests/'
        )
        
        # ===================================================================
        # 5. Data Processing API Lambda
        # ===================================================================
        self.api_lambda = lambda_.Function(
            self, 'DataProcessingAPI',
            function_name=f'cms-{deployment_stage}-data-processing-api',
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler='data_processing_api.handler',
            code=lambda_.Code.from_asset('../services/data_processing/lambda'),
            environment={
                'SIGNAL_CATALOG_TABLE': self.signal_catalog_table.table_name,
                'DATA_SOURCE_CONFIGS_TABLE': self.data_source_configs_table.table_name,
                'MANIFESTS_BUCKET': self.manifests_bucket.bucket_name,
                'DECODER_MANIFEST_TABLE': self.decoder_manifest_table.table_name,
                'MODEL_MANIFEST_TABLE': self.model_manifest_table.table_name,
                'CAMPAIGNS_TABLE': self.campaigns_table.table_name,
            },
            timeout=Duration.seconds(30),
            memory_size=512
        )
        
        # Grant permissions
        self.signal_catalog_table.grant_read_write_data(self.api_lambda)
        self.data_source_configs_table.grant_read_write_data(self.api_lambda)
        self.decoder_manifest_table.grant_read_write_data(self.api_lambda)
        self.model_manifest_table.grant_read_write_data(self.api_lambda)
        self.campaigns_table.grant_read_write_data(self.api_lambda)
        self.manifests_bucket.grant_read_write(self.api_lambda)
        
        # ===================================================================
        # 6. API Gateway REST API
        # ===================================================================
        # Build a Cognito authorizer when a user pool ID was provided.
        # Both the immediate call sites (CMS UI campaigns/manifests/etc)
        # and any partner pools listed in CMS_EXTRA_USER_POOL_IDS get
        # trusted by the same authorizer instance.
        default_method_options = None
        api_authorizer = None
        if cms_user_pool_id:
            authorized_pools = [
                cognito.UserPool.from_user_pool_id(
                    self, f'AuthzUserPool{i}', pid
                )
                for i, pid in enumerate([cms_user_pool_id, *cms_extra_pool_ids])
            ]
            api_authorizer = apigw.CognitoUserPoolsAuthorizer(
                self, 'DataProcessingAuthz',
                authorizer_name=f'cms-{deployment_stage}-data-processing-authz',
                cognito_user_pools=authorized_pools,
            )
            default_method_options = apigw.MethodOptions(
                authorization_type=apigw.AuthorizationType.COGNITO,
                authorizer=api_authorizer,
            )

        self.api = apigw.LambdaRestApi(
            self, 'DataProcessingRestAPI',
            rest_api_name=f'cms-{deployment_stage}-data-processing-api',
            handler=self.api_lambda,
            proxy=True,
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=apigw.Cors.ALL_METHODS,
                allow_headers=['Content-Type', 'Authorization']
            ),
            default_method_options=default_method_options,
        )

        CfnOutput(
            self, 'AuthorizationMode',
            value='COGNITO_USER_POOLS' if cms_user_pool_id else 'NONE',
            description='Whether the API is locked down to Cognito users'
        )
        
        # ===================================================================
        # Outputs
        # ===================================================================
        CfnOutput(
            self, 'SignalCatalogTableName',
            value=self.signal_catalog_table.table_name,
            description='Signal Catalog DynamoDB Table'
        )
        
        CfnOutput(
            self, 'DataSourceConfigsTableName',
            value=self.data_source_configs_table.table_name,
            description='Data Source Configurations Table'
        )
        
        CfnOutput(
            self, 'ManifestsBucketName',
            value=self.manifests_bucket.bucket_name,
            description='Transform Manifests S3 Bucket'
        )
        
        CfnOutput(
            self, 'APIEndpoint',
            value=self.api.url,
            description='Data Processing API Endpoint'
        )
        
        # Export for other stacks
        self.signal_catalog_table_name = self.signal_catalog_table.table_name
        self.data_source_configs_table_name = self.data_source_configs_table.table_name
        self.manifests_bucket_name = self.manifests_bucket.bucket_name
        self.api_endpoint = self.api.url
