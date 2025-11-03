"""
Storage Stack - DynamoDB tables matching existing target-account schema exactly
"""

from aws_cdk import (
    Stack,
    aws_dynamodb as dynamodb,
    aws_elasticache as elasticache,
    aws_ec2 as ec2,
    aws_s3 as s3,
    CfnOutput,
    RemovalPolicy,
    Duration,
    Fn
)
from constructs import Construct
from typing import Dict

class StorageStack(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Initialize tables dictionary
        self.tables: Dict[str, dynamodb.Table] = {}
        
        # Vehicle Telemetry Table - matches cms-0a0e68e9-telemetry
        self.tables['telemetry'] = dynamodb.Table(
            self, "TelemetryTable",
            table_name=f"{construct_id}-telemetry",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp",
                type=dynamodb.AttributeType.NUMBER
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
            time_to_live_attribute="ttl",
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        
        # Add GSI for tripId-timestamp queries
        self.tables['telemetry'].add_global_secondary_index(
            index_name="tripId-timestamp-index",
            partition_key=dynamodb.Attribute(
                name="tripId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp",
                type=dynamodb.AttributeType.NUMBER
            )
        )
        
        # Service History Table - Import if exists, create if not
        try:
            # Try to import existing table
            self.tables['service_history'] = dynamodb.Table.from_table_name(
                self, "ServiceHistoryTable",
                table_name=f"{construct_id}-service-history"
            )
        except:
            # Create new table if doesn't exist
            self.tables['service_history'] = dynamodb.Table(
                self, "ServiceHistoryTable",
                table_name=f"{construct_id}-service-history",
                partition_key=dynamodb.Attribute(
                    name="vehicleId",
                    type=dynamodb.AttributeType.STRING
                ),
                sort_key=dynamodb.Attribute(
                    name="serviceDate",
                    type=dynamodb.AttributeType.STRING
                ),
                billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
                removal_policy=RemovalPolicy.RETAIN,
                point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
                stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
                encryption=dynamodb.TableEncryption.AWS_MANAGED
            )
            
            # GSI for service type queries
            self.tables['service_history'].add_global_secondary_index(
                index_name="ServiceTypeIndex",
                partition_key=dynamodb.Attribute(
                    name="serviceType",
                    type=dynamodb.AttributeType.STRING
                ),
                sort_key=dynamodb.Attribute(
                    name="serviceDate",
                    type=dynamodb.AttributeType.STRING
                )
            )
            
            # GSI for dealer queries
            self.tables['service_history'].add_global_secondary_index(
                index_name="DealerIndex",
                partition_key=dynamodb.Attribute(
                    name="dealerId",
                    type=dynamodb.AttributeType.STRING
                ),
                sort_key=dynamodb.Attribute(
                    name="serviceDate",
                    type=dynamodb.AttributeType.STRING
                )
            )
        
        # S3 bucket for service invoices - Import if exists
        try:
            self.invoice_bucket = s3.Bucket.from_bucket_name(
                self, "ServiceInvoiceBucket",
                bucket_name=f"{construct_id}-service-invoices"
            )
        except:
            self.invoice_bucket = s3.Bucket(
                self, "ServiceInvoiceBucket",
                bucket_name=f"{construct_id}-service-invoices",
                encryption=s3.BucketEncryption.S3_MANAGED,
                block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                versioned=True,
                lifecycle_rules=[
                    s3.LifecycleRule(
                        id="ArchiveOldInvoices",
                        transitions=[
                            s3.Transition(
                                storage_class=s3.StorageClass.GLACIER,
                                transition_after=Duration.days(365)
                            )
                        ]
                    )
                ],
                removal_policy=RemovalPolicy.RETAIN
            )
        
        # Outputs
        # ServiceHistoryTableName output removed - generated automatically in loop below
        
        CfnOutput(self, "ServiceInvoiceBucketName",
            value=self.invoice_bucket.bucket_name,
            export_name=f"{construct_id}-service-invoice-bucket"
        )
        
        # Trips Table - matches cms-631ca2-591631-trips-new
        self.tables['trips'] = dynamodb.Table(
            self, "TripsTable",
            table_name=f"{construct_id}-trips",
            partition_key=dynamodb.Attribute(
                name="tripId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
            time_to_live_attribute="ttl",
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        
        # Add GSI for vehicleId queries (REQUIRED for Lambda API)
        self.tables['trips'].add_global_secondary_index(
            index_name="vehicleId-index",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            )
        )
        
        # Safety Events Table - matches cms-631ca2-591631-safety-events-new
        self.tables['safety_events'] = dynamodb.Table(
            self, "SafetyEventsTable",
            table_name=f"{construct_id}-safety-events",
            partition_key=dynamodb.Attribute(
                name="eventId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
            time_to_live_attribute="ttl",
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        
        # Add GSI for vehicleId queries (REQUIRED for Lambda API)
        self.tables['safety_events'].add_global_secondary_index(
            index_name="vehicleId-index",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            )
        )
        
        # Add GSI for tripId queries
        self.tables['safety_events'].add_global_secondary_index(
            index_name="tripId-index",
            partition_key=dynamodb.Attribute(
                name="tripId",
                type=dynamodb.AttributeType.STRING
            )
        )
        
        # Add GSI for vehicleId-timestamp queries (proper design for time range queries)
        self.tables['safety_events'].add_global_secondary_index(
            index_name="vehicleId-timestamp-index",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp",
                type=dynamodb.AttributeType.NUMBER
            )
        )
        
        # Event Catalog GSIs created manually via AWS CLI
        # See: /Users/givenand/connected-mobility-guidance-on-aws/create-gsis.sh
        
        # Maintenance Events Table - Enhanced Schema with Repair Instructions
        # Core: alertId, vehicleId, timestamp, alertType, severity, message, status
        # Management: createdDate, lastUpdated, daysOpen, dueDate, priority, category
        # Cost/Duration: estimatedCost, estimatedDuration
        # Triggers: currentValue, thresholdValue, triggerField, triggerCondition
        # Repair: repairInstructions, manualReference, requiredTools, safetyWarnings
        # Context: currentMileage, driverId, tripId, lat, lng
        # See maintenance_alert_schema.md for complete field documentation
        self.tables['maintenance_events'] = dynamodb.Table(
            self, "MaintenanceEventsTable",
            table_name=f"{construct_id}-maintenance-alerts",
            partition_key=dynamodb.Attribute(
                name="alertId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
            time_to_live_attribute="ttl",
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        
        # Add GSI for vehicleId queries (REQUIRED for Lambda API)
        self.tables['maintenance_events'].add_global_secondary_index(
            index_name="vehicleId-index",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            )
        )
        
        # Add GSI for vehicleId-timestamp queries (proper design for time range queries)
        self.tables['maintenance_events'].add_global_secondary_index(
            index_name="vehicleId-timestamp-index",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp",
                type=dynamodb.AttributeType.NUMBER
            )
        )
        
        # Event Catalog GSIs created manually via AWS CLI
        # See: /Users/givenand/connected-mobility-guidance-on-aws/create-gsis.sh
        
        # Fleet Management Table - matches cms-631ca2-591631-fleets
        self.tables['fleets'] = dynamodb.Table(
            self, "FleetsTable",
            table_name=f"{construct_id}-fleets",
            partition_key=dynamodb.Attribute(
                name="fleetId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        
        # Vehicles Table - matches cms-631ca2-591631-vehicles
        # Schema includes:
        # - vehicleId (PK): VIN or unique identifier
        # - enrollmentStatus: NOT_ENROLLED | PENDING_ACTIVATION | ENROLLED | ACTIVE | INACTIVE
        # - enrolledAt: ISO timestamp when certificate issued
        # - activatedAt: ISO timestamp when first telemetry received
        # - lastSeenAt: ISO timestamp of most recent telemetry
        # - vehicleStatus: UNKNOWN | PARKED | DRIVING | IDLE | CHARGING | MAINTENANCE | OFFLINE
        # - make, model, year, vin, etc.
        self.tables['vehicles'] = dynamodb.Table(
            self, "VehiclesTable",
            table_name=f"{construct_id}-vehicles",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        
        # Vehicle Certificates Table - matches cms-631ca2-591631-vehicle-certificates
        self.tables['vehicle_certificates'] = dynamodb.Table(
            self, "VehicleCertificatesTable",
            table_name=f"{construct_id}-vehicle-certificates",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        
        # User Preferences Table - matches cms-631ca2-591631-user-preferences
        self.tables['user_preferences'] = dynamodb.Table(
            self, "UserPreferencesTable",
            table_name=f"{construct_id}-user-preferences",
            partition_key=dynamodb.Attribute(
                name="userId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        
        # Dashboard Metrics Cache Table - matches cms-631ca2-591631-dashboard-metrics-cache
        self.tables['dashboard_metrics_cache'] = dynamodb.Table(
            self, "DashboardMetricsCacheTable",
            table_name=f"{construct_id}-dashboard-metrics-cache",
            partition_key=dynamodb.Attribute(
                name="metricKey",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            time_to_live_attribute="ttl",
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        
        # Drivers Table - matches cms-631ca2-591631-drivers
        self.tables['drivers'] = dynamodb.Table(
            self, "DriversTable",
            table_name=f"{construct_id}-drivers",
            partition_key=dynamodb.Attribute(
                name="driverId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            encryption=dynamodb.TableEncryption.AWS_MANAGED
        )
        
        # S3 Datalake Bucket for Iceberg Analytics
        self.datalake_bucket = s3.Bucket(
            self, "DatalakeBucket",
            bucket_name=f"{construct_id}-datalake-{self.account}",
            removal_policy=RemovalPolicy.RETAIN,
            versioned=True,
            public_read_access=False,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ArchiveOldData",
                    enabled=True,
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                            transition_after=Duration.days(30)
                        ),
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER,
                            transition_after=Duration.days(90)
                        )
                    ]
                )
            ]
        )
        
        # Add datalake bucket name to tables dictionary for downstream services
        self.tables['datalake_bucket_name'] = self.datalake_bucket.bucket_name
        
        # Outputs for each table
        for table_name, table in self.tables.items():
            # Skip non-table entries (like datalake_bucket_name)
            if not hasattr(table, 'table_name'):
                continue
            # Replace underscores with hyphens for export names
            export_name = table_name.replace('_', '-')
            CfnOutput(
                self, f"{table_name.title().replace('_', '')}TableName",
                value=table.table_name,
                export_name=f"{construct_id}-{export_name}-table-name"
            )
            
            CfnOutput(
                self, f"{table_name.title().replace('_', '')}TableArn",
                value=table.table_arn,
                export_name=f"{construct_id}-{export_name}-table-arn"
            )
        
        # S3 Datalake Bucket Outputs
        CfnOutput(
            self, "DatalakeBucketName",
            value=self.datalake_bucket.bucket_name,
            export_name=f"{construct_id}-datalake-bucket-name"
        )
        
        CfnOutput(
            self, "DatalakeBucketArn",
            value=self.datalake_bucket.bucket_arn,
            export_name=f"{construct_id}-datalake-bucket-arn"
        )
        
        # TODO: Add ElastiCache Redis for real-time vehicle state
        # Will be added to MSK stack for proper VPC co-location
