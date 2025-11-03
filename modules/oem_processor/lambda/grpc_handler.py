"""Generic Lambda handler for OEM gRPC consumer"""
import json
import os
import boto3
from msk_writer import MSKWriter

s3 = boto3.client('s3')

def lambda_handler(event, context):
    """Lambda handler - consumes from OEM gRPC endpoint and writes to MSK"""
    try:
        # Get OEM name from event
        oem_name = event.get('oem_name')
        if not oem_name:
            raise Exception("oem_name required in event")
        
        # Load OEM config from S3 manifest
        manifest_bucket = os.environ.get('MANIFEST_BUCKET', 'cms-oem-manifests')
        manifest_key = f"manifests/{oem_name}/connection.json"
        
        response = s3.get_object(Bucket=manifest_bucket, Key=manifest_key)
        oem_config = json.loads(response['Body'].read())
        
        # Initialize MSK writer
        kafka_writer = MSKWriter(
            bootstrap_servers=os.environ['MSK_BOOTSTRAP_SERVERS'],
            topic=os.environ.get('MSK_TOPIC', 'cms-telemetry-oem'),
            client_id=f'{oem_name}-consumer-lambda'
        )
        
        # Build gRPC config from manifest
        config = {
            'endpoint': oem_config['connection']['endpoint'],
            'flow_name': oem_config['connection'].get('flow_name'),
            'auth': oem_config['connection'].get('auth', {})
        }
        
        # Get shard assignment from event or use default
        assigned_shards = event.get('shards', [0, 1, 2])
        
        # Import gRPC consumer dynamically
        # This allows different OEMs to use different proto files
        from grpc_consumer import GRPCConsumer
        
        # Initialize and run consumer
        consumer = GRPCConsumer(config, kafka_writer, assigned_shards, oem_name)
        messages_processed = consumer.consume()
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'oem': oem_name,
                'messages_processed': messages_processed,
                'status': 'success'
            })
        }
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
