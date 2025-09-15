import json
import boto3
import subprocess
import tempfile
import os

def lambda_handler(event, context):
    """Lambda function to create Kafka topics in MSK cluster"""
    
    if event['RequestType'] == 'Delete':
        return {'Status': 'SUCCESS', 'PhysicalResourceId': 'kafka-topics'}
    
    try:
        # Get parameters
        bootstrap_servers = event['ResourceProperties']['BootstrapServers']
        topics = event['ResourceProperties']['Topics']
        
        # Create temporary directory for Kafka tools
        with tempfile.TemporaryDirectory() as temp_dir:
            # Download Kafka
            subprocess.run([
                'wget', '-q', 'https://archive.apache.org/dist/kafka/2.6.2/kafka_2.12-2.6.2.tgz',
                '-O', f'{temp_dir}/kafka.tgz'
            ], check=True)
            
            subprocess.run([
                'tar', '-xzf', f'{temp_dir}/kafka.tgz', '-C', temp_dir
            ], check=True)
            
            kafka_dir = f'{temp_dir}/kafka_2.12-2.6.2'
            
            # Create each topic
            for topic_config in topics:
                topic_name = topic_config['name']
                partitions = topic_config.get('partitions', 3)
                replication = topic_config.get('replication', 2)
                
                # Create topic using bootstrap servers (no auth for your setup)
                cmd = [
                    f'{kafka_dir}/bin/kafka-topics.sh',
                    '--create',
                    '--bootstrap-server', bootstrap_servers,
                    '--topic', topic_name,
                    '--partitions', str(partitions),
                    '--replication-factor', str(replication)
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True)
                
                if result.returncode != 0 and 'already exists' not in result.stderr:
                    print(f"Error creating topic {topic_name}: {result.stderr}")
                    raise Exception(f"Failed to create topic {topic_name}")
                else:
                    print(f"Topic {topic_name} created successfully")
        
        return {
            'Status': 'SUCCESS',
            'PhysicalResourceId': 'kafka-topics',
            'Data': {'TopicsCreated': [t['name'] for t in topics]}
        }
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'Status': 'FAILED',
            'PhysicalResourceId': 'kafka-topics',
            'Reason': str(e)
        }
