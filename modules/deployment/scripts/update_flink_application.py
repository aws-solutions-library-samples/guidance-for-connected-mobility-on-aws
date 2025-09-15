#!/usr/bin/env python3
import boto3
import sys
import time

def get_flink_app_name(profile=None):
    """Get Flink application name from CloudFormation stack outputs"""
    session = boto3.Session(profile_name=profile if profile else None)
    cf_client = session.client('cloudformation')
    
    try:
        response = cf_client.describe_stacks(StackName='cms-telemetry-pipeline')
        outputs = response['Stacks'][0]['Outputs']
        
        for output in outputs:
            if output['OutputKey'] == 'FlinkAppName':
                return output['OutputValue']
        
        raise Exception("FlinkAppName output not found in stack")
    except Exception as e:
        raise Exception(f"Could not get Flink app name from stack: {e}")

def update_flink_application(jar_hash, profile=None):
    session = boto3.Session(profile_name=profile if profile else None)
    kinesis_client = session.client('kinesisanalyticsv2')

    try:
        app_name = get_flink_app_name(profile)
        print(f"🔄 Updating Flink application {app_name} (JAR hash: {jar_hash})")

        # Get current application version
        response = kinesis_client.describe_application(ApplicationName=app_name)
        current_version = response['ApplicationDetail']['ApplicationVersionId']
        app_status = response['ApplicationDetail']['ApplicationStatus']
        
        print(f"📋 Current application version: {current_version}, Status: {app_status}")

        # Update application with new JAR
        kinesis_client.update_application(
            ApplicationName=app_name,
            CurrentApplicationVersionId=current_version,
            ApplicationConfigurationUpdate={
                'ApplicationCodeConfigurationUpdate': {
                    'CodeContentUpdate': {
                        'S3ContentLocationUpdate': {
                            'FileKeyUpdate': f'{jar_hash}.jar'
                        }
                    }
                }
            }
        )

        print(f"✅ Application updated with JAR: {jar_hash}")
        
        # Start application if it's not running
        if app_status == 'READY':
            print("🚀 Starting Flink application...")
            kinesis_client.start_application(
                ApplicationName=app_name,
                RunConfiguration={
                    'FlinkRunConfiguration': {
                        'AllowNonRestoredState': True
                    },
                    'ApplicationRestoreConfiguration': {
                        'ApplicationRestoreType': 'SKIP_RESTORE_FROM_SNAPSHOT'
                    }
                }
            )
            print("✅ Flink application started")
        
    except Exception as e:
        print(f"❌ Error updating Flink application: {e}")
        sys.exit(1)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 update_flink_application.py <jar_hash> [profile]")
        sys.exit(1)

    jar_hash = sys.argv[1]
    profile = sys.argv[2] if len(sys.argv) > 2 else None
    update_flink_application(jar_hash, profile)
