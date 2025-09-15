#!/usr/bin/env python3
"""
Test telemetry pipeline deployment
"""

import boto3
import subprocess
import sys
import time

def wait_for_stack_deletion():
    """Wait for stack to be completely deleted"""
    # Use environment variable or default profile
    profile_name = os.environ.get('AWS_PROFILE', 'default')
    session = boto3.Session(profile_name=profile_name)
    cf_client = session.client('cloudformation', region_name='us-east-1')
    
    print("🔄 Waiting for stack deletion to complete...")
    
    while True:
        try:
            response = cf_client.describe_stacks(StackName='cms-telemetry-pipeline')
            status = response['Stacks'][0]['StackStatus']
            print(f"   Stack status: {status}")
            
            if status in ['DELETE_COMPLETE', 'DELETE_FAILED']:
                break
                
            time.sleep(30)
            
        except cf_client.exceptions.ClientError as e:
            if 'does not exist' in str(e):
                print("✅ Stack deleted successfully")
                break
            else:
                print(f"❌ Error checking stack: {e}")
                break

def test_deployment():
    """Test the telemetry pipeline deployment"""
    
    print("🚀 Testing telemetry pipeline deployment...")
    
    # Wait for stack deletion
    wait_for_stack_deletion()
    
    # Test CDK deployment
    try:
        result = subprocess.run([
            'make', '_deploy-telemetry', 'PROFILE=target-account'
        ], cwd='/Users/givenand/connected-mobility-workspace/modules/cms_ui/deployment', 
        capture_output=True, text=True, timeout=1800)  # 30 minute timeout
        
        if result.returncode == 0:
            print("✅ Telemetry pipeline deployment successful!")
            print(result.stdout)
        else:
            print("❌ Telemetry pipeline deployment failed!")
            print(result.stderr)
            
    except subprocess.TimeoutExpired:
        print("❌ Deployment timed out after 30 minutes")
    except Exception as e:
        print(f"❌ Deployment error: {e}")

if __name__ == "__main__":
    test_deployment()
