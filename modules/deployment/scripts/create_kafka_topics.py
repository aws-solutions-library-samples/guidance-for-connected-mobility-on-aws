#!/usr/bin/env python3
"""
Create required Kafka topics in MSK cluster
"""

import sys
import subprocess
import tempfile
import os

def create_kafka_topics(bootstrap_servers, aws_profile=None):
    """Create required Kafka topics"""
    
    topics = [
        {"name": "cms-telemetry-raw", "partitions": 3, "replication": 2},
        {"name": "cms-safety-events", "partitions": 3, "replication": 2},
        {"name": "cms-maintenance-alerts", "partitions": 3, "replication": 2}
    ]
    
    print(f"📦 Downloading Kafka tools...")
    
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
        
        print(f"🔧 Creating topics using bootstrap servers: {bootstrap_servers}")
        
        # Create each topic
        for topic_config in topics:
            topic_name = topic_config['name']
            partitions = topic_config['partitions']
            replication = topic_config['replication']
            
            print(f"   Creating topic: {topic_name} (partitions={partitions}, replication={replication})")
            
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
            
            if result.returncode != 0:
                if 'already exists' in result.stderr:
                    print(f"   ✅ Topic {topic_name} already exists")
                else:
                    print(f"   ❌ Error creating topic {topic_name}: {result.stderr}")
                    return False
            else:
                print(f"   ✅ Topic {topic_name} created successfully")
        
        # List topics to verify
        print(f"\n📋 Verifying topics...")
        list_cmd = [
            f'{kafka_dir}/bin/kafka-topics.sh',
            '--list',
            '--bootstrap-server', bootstrap_servers
        ]
        
        result = subprocess.run(list_cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print("   Available topics:")
            for topic in result.stdout.strip().split('\n'):
                if topic.strip():
                    print(f"     - {topic.strip()}")
        
        return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 create_kafka_topics.py <bootstrap_servers> [aws_profile]")
        sys.exit(1)
    
    bootstrap_servers = sys.argv[1]
    aws_profile = sys.argv[2] if len(sys.argv) > 2 else os.environ.get('AWS_PROFILE')
    
    try:
        success = create_kafka_topics(bootstrap_servers, aws_profile)
        if success:
            print("\n🎉 All Kafka topics created successfully!")
            sys.exit(0)
        else:
            print("\n❌ Failed to create some topics")
            sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        sys.exit(1)
