#!/usr/bin/env python3
"""
Enable VPC connectivity on MSK cluster (async background task)
"""

import boto3
import sys
import time
import os

def get_cluster_arn(deployment_stage: str):
    """Get MSK cluster ARN from CloudFormation stack"""
    cf = boto3.client('cloudformation')
    try:
        response = cf.describe_stacks(StackName=f"cms-{deployment_stage}-msk")
        outputs = response['Stacks'][0]['Outputs']
        for output in outputs:
            if output['OutputKey'] == 'MSKClusterArn':
                return output['OutputValue']
    except Exception as e:
        print(f"❌ Error getting cluster ARN: {e}")
    return None

def get_secret_arn(deployment_stage: str):
    """Get IoT user secret ARN from CloudFormation stack"""
    cf = boto3.client('cloudformation')
    try:
        response = cf.describe_stacks(StackName=f"cms-{deployment_stage}-msk")
        outputs = response['Stacks'][0]['Outputs']
        for output in outputs:
            if output['OutputKey'] == 'IoTUserSecretArn':
                return output['OutputValue']
    except Exception as e:
        print(f"❌ Error getting secret ARN: {e}")
    return None

def associate_secret_with_cluster(cluster_arn: str, secret_arn: str):
    """Associate secret with MSK cluster for SCRAM authentication"""
    kafka = boto3.client('kafka')
    
    try:
        print(f"🔐 Associating secret with cluster...")
        
        kafka.batch_associate_scram_secret(
            ClusterArn=cluster_arn,
            SecretArnList=[secret_arn]
        )
        
        print("✅ Secret associated with cluster")
        return True
        
    except Exception as e:
        print(f"❌ Error associating secret: {e}")
        return False

def enable_vpc_connectivity(cluster_arn: str):
    """Enable VPC connectivity IAM on MSK cluster"""
    kafka = boto3.client('kafka')
    
    try:
        print(f"🔧 Enabling VPC connectivity for cluster: {cluster_arn}")
        
        # Wait for cluster to be ACTIVE
        print("⏳ Waiting for MSK cluster to be ACTIVE...")
        print("   This typically takes 8-12 minutes for new clusters")
        for attempt in range(20):  # Wait up to 60 minutes (20 attempts * 3 minutes)
            response = kafka.describe_cluster_v2(ClusterArn=cluster_arn)
            cluster_info = response['ClusterInfo']
            state = cluster_info['State']
            
            if state == 'ACTIVE':
                print("✅ Cluster is ACTIVE and ready for VPC connectivity")
                break
            elif state in ['FAILED', 'DELETING']:
                print(f"❌ Cluster in {state} state")
                return False
            
            elapsed_minutes = attempt * 3
            print(f"⏱️  Attempt {attempt + 1}/20: Cluster state is {state}")
            print(f"   Elapsed time: {elapsed_minutes} minutes. Checking again in 3 minutes...")
            time.sleep(180)  # Wait 3 minutes between attempts
        
        if state != 'ACTIVE':
            print(f"❌ Cluster not ACTIVE after 60 minutes: {state}")
            print("   This may indicate a cluster provisioning issue. Check AWS Console for details.")
            return False
        
        # Check if VPC connectivity IAM is already enabled
        vpc_connectivity = cluster_info.get('Provisioned', {}).get('ConnectivityInfo', {}).get('VpcConnectivity', {})
        iam_enabled = vpc_connectivity.get('ClientAuthentication', {}).get('Sasl', {}).get('Iam', {}).get('Enabled', False)
        
        print(f"🔍 VPC connectivity check: IAM enabled = {iam_enabled}")
        
        if iam_enabled:
            print("✅ VPC connectivity IAM already enabled")
            return True
        
        # Enable VPC connectivity IAM
        print("🔧 Enabling VPC connectivity IAM authentication...")
        print("   This allows IoT Core to connect to MSK through VPC endpoints")
        current_version = cluster_info['CurrentVersion']
        
        try:
            kafka.update_connectivity(
                ClusterArn=cluster_arn,
                CurrentVersion=current_version,
                ConnectivityInfo={
                    'VpcConnectivity': {
                        'ClientAuthentication': {
                            'Sasl': {
                                'Iam': {'Enabled': True}
                            }
                        }
                    }
                }
            )
            
            print("✅ VPC connectivity IAM update initiated")
            print("   MSK is now configuring VPC connectivity...")
            
        except Exception as update_error:
            error_str = str(update_error)
            if "BadRequestException" in error_str and "identical to the current value" in error_str:
                print("✅ VPC connectivity IAM is already enabled (no changes needed)")
                return True
            else:
                raise update_error
        
        # Wait for update to complete
        print("⏳ Waiting for VPC connectivity configuration to complete...")
        print("   This typically takes 10-15 minutes as MSK updates cluster networking")
        for attempt in range(10):  # Wait up to 30 minutes (10 attempts * 3 minutes)
            response = kafka.describe_cluster_v2(ClusterArn=cluster_arn)
            state = response['ClusterInfo']['State']
            
            if state == 'ACTIVE':
                # Check if VPC connectivity is now enabled
                vpc_connectivity = response['ClusterInfo'].get('Provisioned', {}).get('ConnectivityInfo', {}).get('VpcConnectivity', {})
                iam_enabled = vpc_connectivity.get('ClientAuthentication', {}).get('Sasl', {}).get('Iam', {}).get('Enabled', False)
                
                if iam_enabled:
                    print("✅ VPC connectivity IAM successfully enabled")
                    print("   IoT Core can now connect to MSK through VPC endpoints")
                    return True
            
            elapsed_minutes = attempt * 3
            print(f"⏱️  Update attempt {attempt + 1}/10: Cluster state is {state}")
            print(f"   Elapsed time: {elapsed_minutes} minutes. VPC connectivity still configuring...")
            time.sleep(180)  # Wait 3 minutes between attempts
        
        print("⚠️ VPC connectivity update may still be in progress after 30 minutes")
        print("   Check AWS MSK Console to monitor the cluster update status")
        return True
        
    except Exception as e:
        error_str = str(e)
        if "BadRequestException" in error_str and "identical to the current value" in error_str:
            print("✅ VPC connectivity IAM is already enabled (no changes needed)")
            return True
        else:
            print(f"❌ Error enabling VPC connectivity: {e}")
            return False

def main():
    if len(sys.argv) != 3:
        print("Usage: python3 enable_vpc_connectivity.py <deployment_stage> <aws_profile>")
        sys.exit(1)
    
    deployment_stage = sys.argv[1]
    aws_profile = sys.argv[2]
    
    # Set AWS profile
    os.environ['AWS_PROFILE'] = aws_profile
    
    print(f"🚀 VPC Connectivity Enabler - Stage: {deployment_stage}, Profile: {aws_profile}")
    
    # Get cluster ARN
    cluster_arn = get_cluster_arn(deployment_stage)
    if not cluster_arn:
        print("❌ Could not get MSK cluster ARN")
        sys.exit(1)
    
    # Get secret ARN
    secret_arn = get_secret_arn(deployment_stage)
    if not secret_arn:
        print("❌ Could not get IoT user secret ARN")
        sys.exit(1)
    
    # Enable VPC connectivity
    vpc_success = enable_vpc_connectivity(cluster_arn)
    
    # Associate secret with cluster
    secret_success = associate_secret_with_cluster(cluster_arn, secret_arn)
    
    if vpc_success and secret_success:
        print("🎉 VPC connectivity and secret association completed successfully")
        sys.exit(0)
    else:
        print("❌ VPC connectivity enablement or secret association failed")
        sys.exit(1)

if __name__ == "__main__":
    main()
