"""Lambda handler for Ford gRPC consumer"""
import json
import os
import sys
import boto3

# Force fresh imports - clear any cached modules
if 'consumers.ford_grpc_consumer' in sys.modules:
    del sys.modules['consumers.ford_grpc_consumer']
if 'msk_writer' in sys.modules:
    del sys.modules['msk_writer']

from consumers.ford_grpc_consumer import GRPCConsumer
from msk_writer import MSKWriter

def lambda_handler(event, context):
    """Lambda handler - consumes from Ford gRPC and writes to MSK"""
    try:
        # Load Ford config from DynamoDB
        dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
        table = dynamodb.Table('cms-dev-data-source-configs')
        response = table.get_item(Key={'source_id': 'ford'})
        
        if 'Item' not in response:
            raise Exception("Ford config not found in DynamoDB")
        
        ford_config = response['Item']['config']
        
        # Initialize MSK writer
        kafka_writer = MSKWriter(
            bootstrap_servers=os.environ['MSK_BOOTSTRAP_SERVERS'],
            topic=os.environ.get('MSK_TOPIC', 'cms-telemetry-oem')
        )
        
        # Build gRPC config
        config = {
            'endpoint': ford_config['grpc_endpoint'],
            'flow_name': ford_config['flow_name'],
            'auth': {
                'token_endpoint': ford_config['oauth2']['token_endpoint'],
                'client_id': ford_config['oauth2']['client_id'],
                'client_secret': ford_config['oauth2']['client_secret'],
                'resource_id': ford_config['oauth2']['resource_id']
            }
        }
        
        # Get shard assignment from event or default to first 3 shards
        assigned_shards = event.get('shards', [0, 1, 2])
        
        # Initialize and run consumer
        consumer = GRPCConsumer(config, kafka_writer, assigned_shards)
        consumer.consume()
        
        return {
            'statusCode': 200,
            'body': json.dumps({'status': 'success'})
        }
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
