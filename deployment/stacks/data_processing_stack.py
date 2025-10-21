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
            point_in_time_recovery=True
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
            point_in_time_recovery=True
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
            bucket_name=f'cms-{deployment_stage}-transform-manifests-{self.account}',
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
                'MANIFESTS_BUCKET': self.manifests_bucket.bucket_name
            },
            timeout=Duration.seconds(30),
            memory_size=512
        )
        
        # Grant permissions
        self.signal_catalog_table.grant_read_write_data(self.api_lambda)
        self.data_source_configs_table.grant_read_write_data(self.api_lambda)
        self.manifests_bucket.grant_read_write(self.api_lambda)
        
        # ===================================================================
        # 6. API Gateway REST API
        # ===================================================================
        self.api = apigw.LambdaRestApi(
            self, 'DataProcessingRestAPI',
            rest_api_name=f'cms-{deployment_stage}-data-processing-api',
            handler=self.api_lambda,
            proxy=True,
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=apigw.Cors.ALL_METHODS,
                allow_headers=['Content-Type', 'Authorization']
            )
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
