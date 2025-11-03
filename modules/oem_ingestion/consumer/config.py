"""
Configuration loader for OEM consumer
Reads from environment variables and S3
"""
import os
import json
import boto3
from typing import Dict, Any

class OEMConfig:
    def __init__(self):
        # Environment variables
        self.oem_name = os.getenv('OEM_NAME', 'ford-fcs')
        self.connection_type = os.getenv('CONNECTION_TYPE', 'grpc')
        self.aws_region = os.getenv('AWS_REGION', 'us-east-1')
        
        # MSK configuration
        self.msk_bootstrap_servers = os.getenv('MSK_BOOTSTRAP_SERVERS')
        self.msk_topic = os.getenv('MSK_TOPIC', 'cms-telemetry-oem')
        
        # S3 configuration
        self.manifests_bucket = os.getenv('MANIFESTS_BUCKET', 'cms-dev-transform-manifests-195026230833')
        self.manifest_key = f'manifests/{self.oem_name}-transform.json'
        
        # Load transform manifest and data source config
        self.transform_manifest = self._load_manifest()
        self.data_source_config = self._load_data_source_config()
        
    def _load_manifest(self) -> Dict[str, Any]:
        """Load transform manifest from S3"""
        try:
            s3 = boto3.client('s3', region_name=self.aws_region)
            response = s3.get_object(Bucket=self.manifests_bucket, Key=self.manifest_key)
            manifest = json.loads(response['Body'].read())
            print(f"✓ Loaded transform manifest for {self.oem_name}")
            return manifest
        except Exception as e:
            print(f"⚠ Failed to load manifest: {e}")
            return {}
    
    def _load_data_source_config(self) -> Dict[str, Any]:
        """Load data source configuration from DynamoDB"""
        try:
            dynamodb = boto3.resource('dynamodb', region_name=self.aws_region)
            table = dynamodb.Table('cms-dev-data-source-configs')
            
            response = table.get_item(Key={'source_id': self.oem_name})
            if 'Item' in response:
                print(f"✓ Loaded data source config for {self.oem_name}")
                return response['Item']
            else:
                print(f"⚠ No data source config found for {self.oem_name}")
                return {}
        except Exception as e:
            print(f"⚠ Failed to load data source config: {e}")
            return {}
    
    def get_connection_config(self) -> Dict[str, Any]:
        """Get connection-specific configuration"""
        config = self.data_source_config.get('config', {})
        
        if self.connection_type == 'grpc':
            return {
                'endpoint': config.get('grpc_endpoint'),
                'flow_name': config.get('flow_name'),
                'shard_count': config.get('shard_count', 24),
                'auth': config.get('oauth2', {})
            }
        elif self.connection_type == 'rest':
            return {
                'endpoint': config.get('api_endpoint'),
                'auth': config.get('oauth2', {})
            }
        else:
            return config
    
    def get_shard_assignment(self) -> list:
        """Get shard assignment for this task"""
        task_index = int(os.getenv('TASK_INDEX', '0'))
        total_tasks = int(os.getenv('TOTAL_TASKS', '8'))
        
        connection_config = self.get_connection_config()
        total_shards = int(connection_config.get('shard_count', 24))
        
        shards_per_task = total_shards // total_tasks
        start_shard = task_index * shards_per_task
        end_shard = start_shard + shards_per_task
        
        assigned_shards = list(range(start_shard, end_shard))
        print(f"✓ Task {task_index} assigned shards: {assigned_shards}")
        return assigned_shards
