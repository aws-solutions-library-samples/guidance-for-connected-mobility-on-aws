#!/usr/bin/env python3
import boto3
import json
import sys
from pathlib import Path

# Add config to path
sys.path.append(str(Path(__file__).parent.parent / ".config"))
from table_config import TableConfig

def update_flink_permissions():
    """Update Flink IAM role with correct DynamoDB table permissions"""
    
    session = boto3.Session(profile_name="target-account")
    iam = session.client('iam', region_name='us-east-1')
    
    role_name = "cms-telemetry-pipeline-FlinkProcessorFlinkExecution-oGHO8IAKRlTE"
    policy_name = "FlinkProcessorFlinkExecutionRoleDefaultPolicy860328E0"
    
    # Get current table names from config
    config = TableConfig()
    tables = config.get_all_tables("prod")
    
    # Build table ARNs
    table_arns = []
    for table_name in tables.values():
        table_arns.append(f"arn:aws:dynamodb:us-east-1:470296731304:table/{table_name}")
    
    print("Adding permissions for tables:")
    for arn in table_arns:
        print(f"  {arn}")
    
    # Get current policy
    response = iam.get_role_policy(RoleName=role_name, PolicyName=policy_name)
    policy_doc = response['PolicyDocument']
    
    # Update DynamoDB statement
    for statement in policy_doc['Statement']:
        if 'dynamodb:PutItem' in statement.get('Action', []):
            if 'Resource' in statement and isinstance(statement['Resource'], list):
                # Add new table ARNs to existing ones
                existing_arns = set(statement['Resource'])
                new_arns = set(table_arns)
                statement['Resource'] = list(existing_arns.union(new_arns))
                break
    
    # Update the policy
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=policy_name,
        PolicyDocument=json.dumps(policy_doc)
    )
    
    print(f"\n✅ Updated IAM policy: {policy_name}")
    print("Flink processor now has permissions for all configured tables")

if __name__ == "__main__":
    update_flink_permissions()
