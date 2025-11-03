"""Lambda handler for Ford gRPC consumer"""
import json
import os
import time
from consumers.grpc_consumer import GRPCConsumer
from msk_writer import MSKWriter

def lambda_handler(event, context):
    """Lambda handler - consumes from Ford gRPC and writes to MSK"""
    start_time = time.time()
    max_runtime = context.get_remaining_time_in_millis() / 1000 - 30  # Leave 30s buffer
    
    try:
        # Initialize MSK writer
        kafka_writer = MSKWriter(
            bootstrap_servers=os.environ['MSK_BOOTSTRAP_SERVERS'],
            topic=os.environ.get('MSK_TOPIC', 'cms-telemetry-oem')
        )
        
        # Ford gRPC config
        config = {
            'endpoint': os.environ['FORD_GRPC_ENDPOINT'],
            'flow_name': os.environ['FORD_FLOW_NAME'],
            'auth': {
                'token_endpoint': os.environ['FORD_TOKEN_ENDPOINT'],
                'client_id': os.environ['FORD_CLIENT_ID'],
                'client_secret': os.environ['FORD_CLIENT_SECRET'],
                'resource_id': os.environ.get('FORD_RESOURCE_ID', '')
            }
        }
        
        # Get shard assignment from event or default to first 3 shards
        assigned_shards = event.get('shards', [0, 1, 2])
        
        # Initialize consumer
        consumer = GRPCConsumer(config, kafka_writer, assigned_shards)
        
        # Consume with timeout
        messages_processed = 0
        for shard_index in assigned_shards:
            if time.time() - start_time > max_runtime:
                break
            consumer._consume_shard(shard_index)
            messages_processed += 1
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'messages_processed': messages_processed,
                'runtime_seconds': time.time() - start_time
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
