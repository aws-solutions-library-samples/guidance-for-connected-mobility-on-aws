#!/bin/bash
# Test Ford consumer writing to MSK locally

# 1. Get MSK bootstrap servers
echo "Getting MSK bootstrap servers..."
MSK_CLUSTER_ARN=$(aws kafka list-clusters --region us-east-1 --query 'ClusterInfoList[0].ClusterArn' --output text)
MSK_BOOTSTRAP=$(aws kafka get-bootstrap-brokers --cluster-arn $MSK_CLUSTER_ARN --region us-east-1 --query 'BootstrapBrokerStringSaslIam' --output text)

echo "MSK Bootstrap: $MSK_BOOTSTRAP"

# 2. Set environment variables
export MSK_BOOTSTRAP_SERVERS="$MSK_BOOTSTRAP"
export MSK_TOPIC="cms-telemetry-oem"
export AWS_REGION="us-east-1"

# 3. Run consumer
echo "Starting Ford consumer..."
python main.py
