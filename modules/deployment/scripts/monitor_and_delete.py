#!/usr/bin/env python3
"""
Monitor CloudFormation stack and auto-delete when rollback completes
"""
import boto3
import time
import sys

def monitor_and_delete_stack():
    """Monitor stack and delete when rollback is complete"""
    
    stack_name = "cms-telemetry-pipeline"
    client = boto3.client('cloudformation', region_name='us-east-1')
    
    print(f"🔍 Monitoring stack: {stack_name}")
    
    while True:
        try:
            response = client.describe_stacks(StackName=stack_name)
            stack = response['Stacks'][0]
            status = stack['StackStatus']
            
            print(f"⏳ Current status: {status}")
            
            if status == 'ROLLBACK_COMPLETE':
                print("✅ Rollback complete! Deleting stack...")
                client.delete_stack(StackName=stack_name)
                print("🗑️  Stack deletion initiated")
                
                # Wait for deletion to complete
                while True:
                    try:
                        response = client.describe_stacks(StackName=stack_name)
                        delete_status = response['Stacks'][0]['StackStatus']
                        print(f"🗑️  Deletion status: {delete_status}")
                        
                        if delete_status == 'DELETE_COMPLETE':
                            print("✅ Stack successfully deleted!")
                            return True
                        elif 'FAILED' in delete_status:
                            print(f"❌ Deletion failed: {delete_status}")
                            return False
                            
                    except client.exceptions.ClientError as e:
                        if 'does not exist' in str(e):
                            print("✅ Stack successfully deleted!")
                            return True
                        else:
                            print(f"❌ Error checking deletion: {e}")
                            return False
                    
                    time.sleep(10)
                    
            elif 'FAILED' in status and status != 'ROLLBACK_FAILED':
                print(f"❌ Stack in failed state: {status}")
                return False
                
        except client.exceptions.ClientError as e:
            if 'does not exist' in str(e):
                print("✅ Stack already deleted!")
                return True
            else:
                print(f"❌ Error: {e}")
                return False
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            return False
            
        time.sleep(30)  # Check every 30 seconds

if __name__ == "__main__":
    success = monitor_and_delete_stack()
    sys.exit(0 if success else 1)
