#!/usr/bin/env python3
"""
Import existing DynamoDB tables into CloudFormation stack
"""
import boto3
import json

def create_import_template():
    """Create CloudFormation import template for existing tables"""
    
    table_mappings = {
        "TelemetryTableB87F4322": "cms-dev-storage-telemetry",
        "TripsTable02533942": "cms-dev-storage-trips", 
        "SafetyEventsTable845EC70B": "cms-dev-storage-safety-events",
        "MaintenanceEventsTable1D545074": "cms-dev-storage-maintenance-alerts",
        "FleetsTableB2320739": "cms-dev-storage-fleets",
        "VehiclesTable2BED75CE": "cms-dev-storage-vehicles",
        "VehicleCertificatesTable40A6247D": "cms-dev-storage-vehicle-certificates",
        "UserPreferencesTableA0B50479": "cms-dev-storage-user-preferences",
        "DashboardMetricsCacheTableB0BD379E": "cms-dev-storage-dashboard-metrics-cache",
        "DriversTable8BD16CC9": "cms-dev-storage-drivers"
    }
    
    resources_to_import = []
    for logical_id, table_name in table_mappings.items():
        resources_to_import.append({
            "ResourceType": "AWS::DynamoDB::Table",
            "LogicalResourceId": logical_id,
            "ResourceIdentifier": {
                "TableName": table_name
            }
        })
    
    return resources_to_import

def import_tables():
    """Import existing tables into CloudFormation stack"""
    cf = boto3.client('cloudformation')
    
    resources_to_import = create_import_template()
    
    # Read the synthesized template and modify it for import
    with open('cdk.out/cms-dev-storage.template.json', 'r') as f:
        template = json.load(f)
    
    # Remove CDKMetadata resource and Outputs for import
    if 'CDKMetadata' in template.get('Resources', {}):
        del template['Resources']['CDKMetadata']
    
    if 'Outputs' in template:
        del template['Outputs']
    
    template_body = json.dumps(template)
    
    try:
        response = cf.create_change_set(
            StackName='cms-dev-storage',
            ChangeSetName='import-existing-tables',
            ChangeSetType='IMPORT',
            ResourcesToImport=resources_to_import,
            TemplateBody=template_body
        )
        
        print(f"Created import change set: {response['Id']}")
        print("Execute with: aws cloudformation execute-change-set --change-set-name import-existing-tables --stack-name cms-dev-storage")
        
    except Exception as e:
        print(f"Error creating import change set: {e}")

if __name__ == "__main__":
    import_tables()
