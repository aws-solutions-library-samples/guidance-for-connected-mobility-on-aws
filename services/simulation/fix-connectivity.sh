#!/bin/bash

echo "Fixing Managed Flink to MSK connectivity..."

# 1. Attach cross-account policy to Flink execution role
aws iam attach-role-policy \
  --role-name cms-telemetry-pipeline-FlinkProcessorFlinkExecution-FZn3SLHFy6JA \
  --policy-arn arn:aws:iam::195026230833:policy/CrossAccountMSKAccess

# 2. Create secret for cross-account MSK credentials
aws secretsmanager create-secret \
  --name "msk-cross-account-credentials" \
  --description "Cross-account MSK SASL credentials" \
  --secret-string '{"username":"flink-user","password":"SecurePassword123!"}'

# 3. Update Flink application configuration
aws kinesisanalyticsv2 update-application \
  --application-name cms-telemetry-processor-089917 \
  --current-application-version-id 1 \
  --application-configuration-update '{
    "EnvironmentPropertyUpdates": {
      "PropertyGroups": [
        {
          "PropertyGroupId": "consumer.config.0",
          "PropertyMap": {
            "bootstrap.servers": "b-1.cms-telemetry-cluster-sasl.a6fbf8.c23.kafka.us-east-1.amazonaws.com:9096",
            "sasl.jaas.config": "org.apache.kafka.common.security.scram.ScramLoginModule required username=\"flink-user\" password=\"${get_secret(\"msk-cross-account-credentials\", \"SecretString\", \"password\")}\";",
            "sasl.mechanism": "SCRAM-SHA-512",
            "security.protocol": "SASL_SSL"
          }
        }
      ]
    }
  }'

echo "Configuration updated. You need to:"
echo "1. Set up VPC peering between accounts"
echo "2. Configure MSK cluster policy in target account (470296731304)"
echo "3. Restart Flink application"
