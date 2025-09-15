#!/bin/bash

# Update Flink application to connect to target MSK cluster
aws kinesisanalyticsv2 update-application \
  --application-name cms-telemetry-processor-089917 \
  --current-application-version-id 1 \
  --application-configuration-update '{
    "EnvironmentPropertyUpdates": {
      "PropertyGroups": [
        {
          "PropertyGroupId": "consumer.config.0",
          "PropertyMap": {
            "bootstrap.servers": "b-1.cms-telemetry-cluster-sasl.a6fbf8.c23.kafka.us-east-1.amazonaws.com:9096,b-2.cms-telemetry-cluster-sasl.a6fbf8.c23.kafka.us-east-1.amazonaws.com:9096",
            "sasl.jaas.config": "org.apache.kafka.common.security.scram.ScramLoginModule required username=\"flink-user\" password=\"${get_secret(\"msk-cross-account-credentials\", \"SecretString\", \"password\")}\";",
            "sasl.mechanism": "SCRAM-SHA-512",
            "security.protocol": "SASL_SSL"
          }
        }
      ]
    }
  }'
