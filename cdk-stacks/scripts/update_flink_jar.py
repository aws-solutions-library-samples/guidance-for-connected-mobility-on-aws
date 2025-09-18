#!/usr/bin/env python3
"""
Update Flink applications with JAR code
"""

import boto3
import os

def update_flink_application_jar(app_name: str, bucket_arn: str, jar_key: str):
    """Update Flink application with JAR code"""
    flink = boto3.client('kinesisanalyticsv2')
    
    try:
        # Get current application configuration
        response = flink.describe_application(ApplicationName=app_name)
        app_detail = response['ApplicationDetail']
        
        if app_detail['ApplicationStatus'] != 'READY':
            print(f"⚠️ Application {app_name} is not in READY state: {app_detail['ApplicationStatus']}")
            return False
        
        current_version = app_detail['ApplicationVersionId']
        
        # Update application with JAR code
        flink.update_application(
            ApplicationName=app_name,
            CurrentApplicationVersionId=current_version,
            ApplicationConfigurationUpdate={
                'ApplicationCodeConfigurationUpdate': {
                    'CodeContentUpdate': {
                        'S3ContentLocationUpdate': {
                            'BucketARNUpdate': bucket_arn,
                            'FileKeyUpdate': jar_key
                        }
                    },
                    'CodeContentTypeUpdate': 'ZIPFILE'
                }
            }
        )
        
        print(f"✅ Updated {app_name} with JAR code")
        return True
        
    except Exception as e:
        print(f"❌ Error updating {app_name}: {e}")
        return False

def main():
    deployment_stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
    bucket_name = "cms-dev-flink-flinkjarbucketd8dc3634-n95gucbssll1"
    bucket_arn = f"arn:aws:s3:::{bucket_name}"
    jar_key = "jars/cms-telemetry-processor-1.0.0.jar"
    
    print(f"🔄 Updating Flink applications with JAR code - {deployment_stage}")
    
    # List of Flink applications to update
    applications = [
        f"cms-{deployment_stage}-flink-event-driven-telemetry-processor",
        f"cms-{deployment_stage}-flink-trip-processor", 
        f"cms-{deployment_stage}-flink-safety-processor",
        f"cms-{deployment_stage}-flink-maintenance-processor",
        f"cms-{deployment_stage}-flink-telemetry-enhanced-final"
    ]
    
    success_count = 0
    for app_name in applications:
        if update_flink_application_jar(app_name, bucket_arn, jar_key):
            success_count += 1
    
    print(f"🎉 Updated {success_count}/{len(applications)} Flink applications with JAR code")
    return success_count == len(applications)

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
