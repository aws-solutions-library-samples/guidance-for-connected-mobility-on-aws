#!/usr/bin/env python3
"""
Create Flink application after S3 bucket and JAR are ready
"""
import boto3
import sys

def create_flink_application(s3_bucket, role_arn, profile=None):
    """Create Flink application with uploaded JAR"""
    
    try:
        # Create session
        session = boto3.Session(profile_name=profile if profile else None)
        flink_client = session.client('kinesisanalyticsv2', region_name='us-east-1')
        
        app_name = "cms-telemetry-processor"
        
        print(f"🚀 Creating Flink application: {app_name}")
        
        # Create Flink application
        response = flink_client.create_application(
            ApplicationName=app_name,
            ApplicationDescription="CMS telemetry processing application",
            RuntimeEnvironment="FLINK-1_15",
            ServiceExecutionRole=role_arn,
            ApplicationConfiguration={
                'ApplicationCodeConfiguration': {
                    'CodeContent': {
                        'S3ContentLocation': {
                            'BucketARN': f"arn:aws:s3:::{s3_bucket}",
                            'FileKey': 'dummy-flink-app.jar'
                        }
                    },
                    'CodeContentType': 'ZIPFILE'
                },
                'FlinkApplicationConfiguration': {
                    'CheckpointConfiguration': {
                        'ConfigurationType': 'DEFAULT'
                    },
                    'MonitoringConfiguration': {
                        'ConfigurationType': 'CUSTOM',
                        'LogLevel': 'INFO',
                        'MetricsLevel': 'APPLICATION'
                    },
                    'ParallelismConfiguration': {
                        'ConfigurationType': 'DEFAULT'
                    }
                }
            }
        )
        
        print(f"✅ Flink application created: {app_name}")
        print(f"   ARN: {response['ApplicationDetail']['ApplicationARN']}")
        return app_name
        
    except Exception as e:
        print(f"❌ Error creating Flink application: {e}")
        return None

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 create_flink_app.py <s3_bucket> <role_arn> [profile]")
        sys.exit(1)
    
    s3_bucket = sys.argv[1]
    role_arn = sys.argv[2]
    profile = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
    
    app_name = create_flink_application(s3_bucket, role_arn, profile)
    sys.exit(0 if app_name else 1)
