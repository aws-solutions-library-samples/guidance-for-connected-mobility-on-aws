"""Test Ford gRPC connection"""
import boto3
import json

# Get Ford config from DynamoDB
dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
table = dynamodb.Table('cms-dev-data-source-configs')
response = table.get_item(Key={'source_id': 'ford'})

if 'Item' in response:
    config = response['Item']['config']
    print("Ford Configuration:")
    print(f"  Endpoint: {config['grpc_endpoint']}")
    print(f"  Flow: {config['flow_name']}")
    print(f"  Shards: {config['shard_count']}")
    print(f"  OAuth2 Token Endpoint: {config['oauth2']['token_endpoint']}")
    print(f"  Client ID: {config['oauth2']['client_id']}")
    print(f"  Status: {response['Item']['status']}")
    print("\nConfiguration loaded successfully!")
    print("\nNote: gRPC connection requires grpcio library compiled for Lambda environment.")
    print("Recommendation: Use AWS Lambda Docker container image or Lambda Layer for gRPC.")
else:
    print("Ford config not found!")
