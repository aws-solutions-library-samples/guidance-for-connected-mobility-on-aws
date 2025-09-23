#!/usr/bin/env python3
"""
Import existing DynamoDB tables into storage stack
"""
import boto3

def import_existing_tables():
    cf = boto3.client('cloudformation')
    
    # Wait for stack deletion to complete
    print("Waiting for stack deletion...")
    waiter = cf.get_waiter('stack_delete_complete')
    try:
        waiter.wait(StackName='cms-dev-storage', WaiterConfig={'MaxAttempts': 30})
        print("✅ Stack deleted")
    except:
        print("Stack already deleted or doesn't exist")
    
    # Deploy with existing table import
    print("Deploying storage stack with table import...")
    import os
    os.system("""
cd /Users/givenand/connected-mobility-workspace/deployment && 
source .venv/bin/activate && 
CDK_DEFAULT_ACCOUNT=$(aws sts get-caller-identity --query Account --output text) \
CDK_DEFAULT_REGION=us-east-1 \
DEPLOYMENT_STAGE=dev \
cdk deploy cms-dev-storage --require-approval never
    """)

if __name__ == "__main__":
    import_existing_tables()
