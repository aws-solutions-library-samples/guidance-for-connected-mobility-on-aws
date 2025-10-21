"""
Data Processing Stack
Provides Signal Catalog, Transform Manifests, and Data Source Configuration
"""

from aws_cdk import (
    Stack,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
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
            point_in_time_recovery=True,
            
            # GSI for querying by signal name across all groups
            global_secondary_indexes=[
                dynamodb.GlobalSecondaryIndex(
                    index_name='signal-name-index',
                    partition_key=dynamodb.Attribute(
                        name='signal_name',
                        type=dynamodb.AttributeType.STRING
                    ),
                    projection_type=dynamodb.ProjectionType.ALL
                ),
                dynamodb.GlobalSecondaryIndex(
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
            ]
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
            point_in_time_recovery=True,
            
            # GSI for querying by source type
            global_secondary_indexes=[
                dynamodb.GlobalSecondaryIndex(
                    index_name='source-type-index',
                    partition_key=dynamodb.Attribute(
                        name='source_type',
                        type=dynamodb.AttributeType.STRING
                    ),
                    projection_type=dynamodb.ProjectionType.ALL
                )
            ]
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
        
        # Export for other stacks
        self.signal_catalog_table_name = self.signal_catalog_table.table_name
        self.data_source_configs_table_name = self.data_source_configs_table.table_name
        self.manifests_bucket_name = self.manifests_bucket.bucket_name
