#!/usr/bin/env python3
"""
Test MSK connectivity from EC2 using Kafka tools
"""

import boto3
import time
import json

def get_msk_info():
    """Get MSK cluster information"""
    kafka = boto3.client('kafka', region_name='us-east-1')
    
    clusters = kafka.list_clusters_v2()
    if not clusters['ClusterInfoList']:
        print("❌ No MSK clusters found")
        return None
    
    cluster = clusters['ClusterInfoList'][0]
    cluster_arn = cluster['ClusterArn']
    
    # Get bootstrap brokers
    brokers = kafka.get_bootstrap_brokers(ClusterArn=cluster_arn)
    
    return {
        'cluster_arn': cluster_arn,
        'scram_brokers': brokers.get('BootstrapBrokerStringSaslScram', ''),
        'vpc_scram_brokers': brokers.get('BootstrapBrokerStringVpcConnectivitySaslScram', ''),
        'iam_brokers': brokers.get('BootstrapBrokerStringSaslIam', '')
    }

def get_scram_credentials():
    """Get SCRAM credentials from Secrets Manager"""
    secrets = boto3.client('secretsmanager', region_name='us-east-1')
    
    try:
        response = secrets.get_secret_value(
            SecretId='AmazonMSK_cms-dev-msk_iot_user_credentials'
        )
        
        secret_data = json.loads(response['SecretString'])
        return secret_data['username'], secret_data['password']
        
    except Exception as e:
        print(f"❌ Error getting SCRAM credentials: {e}")
        return None, None

def find_or_create_ec2_instance():
    """Find existing EC2 instance or create one for testing"""
    ec2 = boto3.client('ec2', region_name='us-east-1')
    
    # Look for existing instances
    instances = ec2.describe_instances(
        Filters=[
            {'Name': 'instance-state-name', 'Values': ['running']},
            {'Name': 'tag:Name', 'Values': ['*kafka*', '*msk*', '*test*']}
        ]
    )
    
    for reservation in instances['Reservations']:
        for instance in reservation['Instances']:
            print(f"✅ Found existing EC2 instance: {instance['InstanceId']}")
            return instance['InstanceId']
    
    print("ℹ️  No suitable EC2 instance found for testing")
    print("💡 You can create one manually or use an existing instance")
    return None

def create_kafka_test_script():
    """Create Kafka test script for EC2"""
    username, password = get_scram_credentials()
    if not username or not password:
        return None
    
    msk_info = get_msk_info()
    if not msk_info:
        return None
    
    # Escape special characters in password for shell
    escaped_password = password.replace('`', '\\`').replace('$', '\\$').replace('"', '\\"')
    
    script = f'''#!/bin/bash
echo "🔧 Testing MSK SCRAM connectivity..."

# MSK Configuration
BOOTSTRAP_SERVERS="{msk_info['scram_brokers']}"
VPC_BOOTSTRAP_SERVERS="{msk_info['vpc_scram_brokers']}"
USERNAME="{username}"
PASSWORD="{escaped_password}"
TOPIC="cms-telemetry-raw"

echo "📊 MSK Cluster Info:"
echo "Standard SCRAM: $BOOTSTRAP_SERVERS"
echo "VPC SCRAM: $VPC_BOOTSTRAP_SERVERS"
echo "Username: $USERNAME"

# Create JAAS configuration
cat > /tmp/kafka_client_jaas.conf << EOF
KafkaClient {{
    org.apache.kafka.common.security.scram.ScramLoginModule required
    username="$USERNAME"
    password="$PASSWORD";
}};
EOF

# Create client properties
cat > /tmp/client.properties << EOF
bootstrap.servers=$BOOTSTRAP_SERVERS
security.protocol=SASL_SSL
sasl.mechanism=SCRAM-SHA-512
sasl.jaas.config=org.apache.kafka.common.security.scram.ScramLoginModule required username="$USERNAME" password="$PASSWORD";
EOF

echo ""
echo "🧪 Testing connectivity..."

# Test 1: List topics
echo "1️⃣ Listing topics..."
/opt/kafka/bin/kafka-topics.sh --bootstrap-server $BOOTSTRAP_SERVERS \\
    --command-config /tmp/client.properties \\
    --list

# Test 2: Create topic if it doesn't exist
echo ""
echo "2️⃣ Creating topic '$TOPIC'..."
/opt/kafka/bin/kafka-topics.sh --bootstrap-server $BOOTSTRAP_SERVERS \\
    --command-config /tmp/client.properties \\
    --create --topic $TOPIC --partitions 3 --replication-factor 2 \\
    --if-not-exists

# Test 3: Produce a test message
echo ""
echo "3️⃣ Producing test message..."
echo '{{"timestamp": "'$(date -Iseconds)'", "vehicle_id": "test-vehicle-001", "location": {{"lat": 40.7128, "lon": -74.0060}}, "speed": 45.5, "test": true}}' | \\
/opt/kafka/bin/kafka-console-producer.sh --bootstrap-server $BOOTSTRAP_SERVERS \\
    --topic $TOPIC \\
    --producer.config /tmp/client.properties

# Test 4: Consume messages
echo ""
echo "4️⃣ Consuming messages (will timeout after 10 seconds)..."
timeout 10 /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server $BOOTSTRAP_SERVERS \\
    --topic $TOPIC \\
    --consumer.config /tmp/client.properties \\
    --from-beginning || echo "Consumer timeout (expected)"

echo ""
echo "✅ MSK SCRAM connectivity test complete!"
'''
    
    return script

def main():
    print("🔧 Testing MSK SCRAM connectivity from EC2")
    
    # Get MSK info
    msk_info = get_msk_info()
    if not msk_info:
        return False
    
    print(f"📊 MSK Cluster: {msk_info['cluster_arn']}")
    print(f"🔗 SCRAM Brokers: {msk_info['scram_brokers']}")
    print(f"🔗 VPC SCRAM Brokers: {msk_info['vpc_scram_brokers']}")
    
    # Get credentials
    username, password = get_scram_credentials()
    if not username:
        return False
    
    print(f"👤 SCRAM User: {username}")
    
    # Create test script
    script = create_kafka_test_script()
    if not script:
        return False
    
    # Save script to file
    with open('/tmp/test_msk_connectivity.sh', 'w') as f:
        f.write(script)
    
    print("✅ Created MSK connectivity test script: /tmp/test_msk_connectivity.sh")
    print("")
    print("🚀 Next steps:")
    print("1. Copy the script to your EC2 instance:")
    print("   scp /tmp/test_msk_connectivity.sh ec2-user@<instance-ip>:~/")
    print("")
    print("2. SSH to your EC2 instance and run:")
    print("   chmod +x ~/test_msk_connectivity.sh")
    print("   sudo ~/test_msk_connectivity.sh")
    print("")
    print("💡 Or use Systems Manager Session Manager if available")
    
    return True

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
