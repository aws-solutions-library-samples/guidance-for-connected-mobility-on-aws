#!/usr/bin/env python3
"""
Import existing DynamoDB tables into CloudFormation stack
"""

import boto3
import json
import sys
import os

def main():
    if len(sys.argv) != 3:
        print("Usage: python3 import_existing_tables.py <deployment_stage> <aws_profile>")
        sys.exit(1)
    
    deployment_stage = sys.argv[1]
    aws_profile = sys.argv[2]
    
    # Set AWS profile
    os.environ['AWS_PROFILE'] = aws_profile
    
    stack_name = f"cms-{deployment_stage}-storage"
    
    # Table mappings: logical_id -> physical_table_name
    tables = {
        "TelemetryTableB87F4322": f"cms-{deployment_stage}-storage-telemetry",
        "TripsTable02533942": f"cms-{deployment_stage}-storage-trips", 
        "SafetyEventsTable845EC70B": f"cms-{deployment_stage}-storage-safety-events",
        "MaintenanceEventsTable1D545074": f"cms-{deployment_stage}-storage-maintenance-alerts",
        "FleetsTableB2320739": f"cms-{deployment_stage}-storage-fleets",
        "VehiclesTable2BED75CE": f"cms-{deployment_stage}-storage-vehicles",
        "VehicleCertificatesTable40A6247D": f"cms-{deployment_stage}-storage-vehicle-certificates",
        "UserPreferencesTableA0B50479": f"cms-{deployment_stage}-storage-user-preferences",
        "DashboardMetricsCacheTableB0BD379E": f"cms-{deployment_stage}-storage-dashboard-metrics-cache",
        "DriversTable8BD16CC9": f"cms-{deployment_stage}-storage-drivers"
    }
    
    cf = boto3.client('cloudformation')
    
    # Create resource import list
    resources_to_import = []
    for logical_id, physical_id in tables.items():
        resources_to_import.append({
            'ResourceType': 'AWS::DynamoDB::Table',
            'LogicalResourceId': logical_id,
            'ResourceIdentifier': {
                'TableName': physical_id
            }
        })
    
    print(f"🔄 Importing {len(resources_to_import)} existing tables into {stack_name}")
    
    try:
        # Create change set for import
        response = cf.create_change_set(
            StackName=stack_name,
            ChangeSetName=f"import-tables-{int(time.time())}",
            ChangeSetType='IMPORT',
            ResourcesToImport=resources_to_import,
            Capabilities=['CAPABILITY_IAM']
        )
        
        change_set_id = response['Id']
        print(f"✅ Created import change set: {change_set_id}")
        print("📋 Execute this change set in AWS Console or use AWS CLI:")
        print(f"aws cloudformation execute-change-set --change-set-name {change_set_id}")
        
    except Exception as e:
        print(f"❌ Error creating import change set: {e}")
        return False
    
    return True

if __name__ == "__main__":
    import time
    main()
