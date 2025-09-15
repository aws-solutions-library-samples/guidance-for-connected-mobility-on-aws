# Direct IoT Core → MSK Kafka CDK Infrastructure

## 🎯 Overview

This CDK stack creates a **direct integration** between AWS IoT Core and Amazon MSK (Kafka), **eliminating Firehose** for real-time telemetry processing.

## 🏗️ Architecture

```
MQTT 5 Simulator → IoT Core Rule → VPC Destination → MSK Kafka → Real-time Processing
```

**No Firehose** = Sub-second latency for telemetry data!

## 📦 Components Created

### 1. **MSK Cluster**
- **Name**: `msk-iot-kafka-direct`
- **Instance Type**: `kafka.m5.large` (2 brokers)
- **Authentication**: SASL/SCRAM-SHA-512
- **Encryption**: TLS in transit
- **Topics**: `cms-telemetry-raw` (auto-created)

### 2. **VPC Topic Rule Destination**
- **Purpose**: Bridge between IoT Core and VPC resources
- **Subnet**: Single subnet with NAT gateway (internet access)
- **Security**: MSK security group with Kafka ports

### 3. **IoT Core Rule**
- **Name**: `cms_data_kafka_direct_cdk`
- **SQL**: `SELECT *, timestamp() as iot_timestamp, topic(3) as vehicle_id FROM 'cms/data/vehicle/+'`
- **Action**: Direct Kafka publish (no Firehose)

### 4. **Security Components**
- **SSL Certificates**: Stored in AWS Secrets Manager
- **SASL Credentials**: SCRAM-SHA-512 authentication
- **IAM Role**: IoT Core permissions for MSK and Secrets Manager

## 🚀 Deployment

### Prerequisites
```bash
# Install CDK
npm install -g aws-cdk

# Install Python dependencies
pip install aws-cdk-lib constructs
```

### Deploy
```bash
# Make executable
chmod +x deploy-iot-kafka-direct.sh

# Deploy infrastructure
./deploy-iot-kafka-direct.sh
```

### Generate Real SSL Certificates (Production)
```bash
python generate_ssl_certificates.py
```

## 📊 Monitoring

### CloudWatch Logs
- **MSK Logs**: `/aws/msk/iot-kafka-direct`
- **IoT Core**: AWS Console → IoT Core → Rules

### Kafka Topics
```bash
# List topics
aws kafka list-clusters --query 'ClusterInfoList[0].ClusterArn'

# Monitor with Kafka tools
kafka-topics.sh --bootstrap-server <bootstrap-servers> --list
```

## 🔧 Configuration

### Environment Variables
```bash
export CDK_DEFAULT_ACCOUNT=195026230833
export CDK_DEFAULT_REGION=us-east-1
```

### Customization
- **Instance Types**: Modify `kafka.m5.large` in CDK stack
- **Retention**: Adjust log retention in MSK configuration
- **Security Groups**: Update ports in `_create_msk_security_group()`

## 🎯 Data Flow

### Input
- **MQTT Topic**: `cms/data/vehicle/{vehicle_id}`
- **Message Format**: Fleet telemetry JSON

### Processing
- **IoT Core Rule**: Adds timestamp and vehicle_id
- **Direct Kafka**: Real-time publish to MSK
- **No Buffering**: Sub-second latency

### Output
- **Kafka Topic**: `cms-telemetry-raw`
- **Format**: Enhanced telemetry with IoT metadata

## 🔐 Security

### Authentication
- **SASL/SCRAM-SHA-512**: Username/password authentication
- **SSL/TLS**: Encrypted communication
- **IAM Roles**: Least privilege access

### Secrets Management
- **SSL Certificates**: AWS Secrets Manager
- **SASL Credentials**: AWS Secrets Manager
- **get_secret()**: IoT Core function for secure access

## 🚨 Troubleshooting

### VPC Destination Issues
```bash
# Check destination status
aws iot get-topic-rule-destination --arn <destination-arn>

# Common issues:
# - Subnet without internet access (needs NAT gateway)
# - Missing IAM permissions
# - Security group restrictions
```

### MSK Connectivity
```bash
# Test from EC2 instance in same VPC
kafka-console-producer.sh --bootstrap-server <bootstrap-servers> --topic test
```

### IoT Core Rule
```bash
# Check rule status
aws iot get-topic-rule --rule-name cms_data_kafka_direct_cdk

# Test with MQTT client
aws iot-data publish --topic "cms/data/vehicle/test123" --payload '{"test": true}'
```

## 📈 Performance

### Expected Throughput
- **MSK**: 1000+ messages/second per broker
- **IoT Core**: 20,000+ messages/second
- **Latency**: Sub-second end-to-end

### Scaling
- **Horizontal**: Add more MSK brokers
- **Vertical**: Increase instance types
- **Partitions**: Scale Kafka topic partitions

## 💰 Cost Optimization

### MSK Cluster
- **Development**: `kafka.t3.small` (cheaper)
- **Production**: `kafka.m5.large` (performance)
- **Storage**: Adjust EBS volume sizes

### VPC Destination
- **Single Subnet**: Reduces ENI costs
- **Shared Security Groups**: Reuse existing groups

## 🔄 Updates

### Modify Stack
```bash
# Update CDK code
vim cms_iot_kafka_direct_stack.py

# Deploy changes
cdk deploy -a "python app_iot_kafka_direct.py"
```

### Certificate Rotation
```bash
# Generate new certificates
python generate_ssl_certificates.py

# Update Secrets Manager
aws secretsmanager update-secret --secret-id <ssl-secret-arn> --secret-string file://ssl_certificates.json
```

## 🎉 Success Metrics

### Deployment Success
- ✅ MSK Cluster: `ACTIVE`
- ✅ VPC Destination: `ENABLED`
- ✅ IoT Rule: Active and processing
- ✅ Kafka Topic: Receiving messages

### Data Flow Verification
```bash
# Publish test message
aws iot-data publish --topic "cms/data/vehicle/test123" --payload '{"speed": 65, "location": {"lat": 47.6, "lon": -122.3}}'

# Verify in Kafka
kafka-console-consumer.sh --bootstrap-server <bootstrap-servers> --topic cms-telemetry-raw --from-beginning
```

## 📚 References

- [AWS IoT Core Kafka Action](https://docs.aws.amazon.com/iot/latest/developerguide/kafka-rule-action.html)
- [Amazon MSK Documentation](https://docs.aws.amazon.com/msk/)
- [CDK Python Reference](https://docs.aws.amazon.com/cdk/api/v2/python/)
- [AWS Blog: IoT Core to MSK](https://aws.amazon.com/blogs/architecture/field-notes-deliver-messages-using-an-iot-rule-action-to-amazon-managed-streaming-for-apache-kafka/)

---

**🚀 Ready for real-time telemetry processing with direct IoT Core → Kafka integration!**
