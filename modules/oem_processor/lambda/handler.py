"""
Generic OEM Data Ingestion Lambda
Connects to OEM endpoints (gRPC/REST/WebSocket), retrieves telemetry, and publishes to MSK
Configuration loaded from S3 manifest
"""
import json
import os
import boto3
from kafka import KafkaProducer
from kafka.errors import KafkaError

s3 = boto3.client('s3')

class OEMIngestionHandler:
    def __init__(self):
        self.bootstrap_servers = os.environ['MSK_BOOTSTRAP_SERVERS']
        self.topic = os.environ.get('MSK_TOPIC', 'cms-telemetry-oem')
        self.manifest_bucket = os.environ.get('MANIFEST_BUCKET', 'cms-oem-manifests')
        
        self.producer = KafkaProducer(
            bootstrap_servers=self.bootstrap_servers.split(','),
            security_protocol='SASL_SSL',
            sasl_mechanism='AWS_MSK_IAM',
            sasl_client_callback_handler_class='aws_msk_iam_sasl_signer.MSKAuthTokenProvider',
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )
    
    def load_manifest(self, oem_name):
        """Load OEM connection manifest from S3"""
        try:
            key = f"manifests/{oem_name}/connection.json"
            response = s3.get_object(Bucket=self.manifest_bucket, Key=key)
            return json.loads(response['Body'].read())
        except Exception as e:
            print(f"Error loading manifest for {oem_name}: {e}")
            return None
    
    def connect_grpc(self, config):
        """Connect to gRPC endpoint and stream data"""
        import grpc
        from google.protobuf.json_format import MessageToDict
        
        endpoint = config['endpoint']
        flow_name = config.get('flow_name')
        
        # Dynamic import based on OEM proto files
        # Proto files should be in S3 or packaged with Lambda
        print(f"Connecting to gRPC endpoint: {endpoint}")
        
        # TODO: Implement gRPC streaming based on manifest
        return []
    
    def connect_rest(self, config):
        """Poll REST API endpoint for data"""
        import requests
        
        endpoint = config['endpoint']
        headers = config.get('headers', {})
        
        response = requests.get(endpoint, headers=headers)
        response.raise_for_status()
        
        return response.json()
    
    def connect_websocket(self, config):
        """Connect to WebSocket endpoint and stream data"""
        # TODO: Implement WebSocket connection
        return []
    
    def publish_to_msk(self, messages, oem_name):
        """Publish messages to MSK topic"""
        success_count = 0
        error_count = 0
        
        for msg in messages:
            try:
                # Add OEM metadata
                msg['oem_source'] = oem_name
                msg['ingestion_timestamp'] = int(time.time() * 1000)
                
                future = self.producer.send(self.topic, value=msg)
                future.get(timeout=10)
                success_count += 1
            except KafkaError as e:
                print(f"Kafka error: {e}")
                error_count += 1
        
        self.producer.flush()
        return success_count, error_count

def lambda_handler(event, context):
    """
    Lambda handler - can be triggered by:
    1. EventBridge schedule (periodic polling)
    2. API Gateway (webhook from OEM)
    3. Manual invocation
    """
    handler = OEMIngestionHandler()
    
    # Get OEM name from event
    oem_name = event.get('oem_name') or event.get('pathParameters', {}).get('oem')
    
    if not oem_name:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'oem_name required'})
        }
    
    # Load manifest
    manifest = handler.load_manifest(oem_name)
    if not manifest:
        return {
            'statusCode': 404,
            'body': json.dumps({'error': f'Manifest not found for {oem_name}'})
        }
    
    # Connect based on connection type
    connection_type = manifest.get('connection_type', 'rest')
    
    try:
        if connection_type == 'grpc':
            messages = handler.connect_grpc(manifest['connection'])
        elif connection_type == 'rest':
            messages = handler.connect_rest(manifest['connection'])
        elif connection_type == 'websocket':
            messages = handler.connect_websocket(manifest['connection'])
        else:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': f'Unknown connection type: {connection_type}'})
            }
        
        # Publish to MSK
        success, errors = handler.publish_to_msk(messages, oem_name)
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'oem': oem_name,
                'messages_processed': success,
                'errors': errors
            })
        }
    
    except Exception as e:
        print(f"Error processing OEM data: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
