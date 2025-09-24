"""
Storage Stack - DynamoDB tables matching existing target-account schema exactly
"""

from aws_cdk import (
    Stack,
    aws_dynamodb as dynamodb,
    CfnOutput,
    RemovalPolicy
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
            time_to_live_attribute="ttl"
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
            time_to_live_attribute="ttl"
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
            time_to_live_attribute="ttl"
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
        
        # Maintenance Events Table - matches cms-631ca2-591631-maintenance-alerts-new
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
            time_to_live_attribute="ttl"
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
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True)
        )
        
        # Vehicles Table - matches cms-631ca2-591631-vehicles
        self.tables['vehicles'] = dynamodb.Table(
            self, "VehiclesTable",
            table_name=f"{construct_id}-vehicles",
            partition_key=dynamodb.Attribute(
                name="vehicleId",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True)
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
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True)
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
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True)
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
            time_to_live_attribute="ttl"
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
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True)
        )
        
        # Outputs for each table
        for table_name, table in self.tables.items():
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
