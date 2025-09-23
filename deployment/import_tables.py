#!/usr/bin/env python3
"""
Import existing DynamoDB tables into CloudFormation stack
"""
import boto3
import json

def import_tables():
    cf = boto3.client('cloudformation')
    
    # Table mappings: LogicalId -> PhysicalId
    table_mappings = {
        'DashboardMetricsCacheTableB0BD379E': 'cms-dev-storage-dashboard-metrics-cache',
        'TelemetryTableB87F4322': 'cms-dev-storage-telemetry',
        'VehicleCertificatesTable40A6247D': 'cms-dev-storage-vehicle-certificates',
        'SafetyEventsTable845EC70B': 'cms-dev-storage-safety-events',
        'DriversTable8BD16CC9': 'cms-dev-storage-drivers',
        'MaintenanceEventsTable1D545074': 'cms-dev-storage-maintenance-alerts',
        'VehiclesTable2BED75CE': 'cms-dev-storage-vehicles',
        'TripsTable02533942': 'cms-dev-storage-trips',
        'FleetsTableB2320739': 'cms-dev-storage-fleets',
        'UserPreferencesTableA0B50479': 'cms-dev-storage-user-preferences'
    }
    
    # Create resources to import
    resources_to_import = []
    for logical_id, physical_id in table_mappings.items():
        resources_to_import.append({
            'ResourceType': 'AWS::DynamoDB::Table',
            'LogicalResourceId': logical_id,
            'ResourceIdentifier': {'TableName': physical_id}
        })
    
    print(f"Importing {len(resources_to_import)} tables into cms-dev-storage stack...")
    
    try:
        # Create import changeset
        response = cf.create_change_set(
            StackName='cms-dev-storage',
            ChangeSetName='import-existing-tables',
            ChangeSetType='IMPORT',
            ResourcesToImport=resources_to_import,
            TemplateURL='https://cdk-hnb659fds-assets-195026230833-us-east-1.s3.amazonaws.com/latest-template.json'  # Use latest template
        )
        
        print(f"✅ Created import changeset: {response['Id']}")
        print("Execute with: aws cloudformation execute-change-set --change-set-name import-existing-tables --stack-name cms-dev-storage")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    import_tables()
