#!/bin/bash

# Add VPC configuration to Flink application
aws kinesisanalyticsv2 update-application \
  --profile target-account \
  --application-name cms-telemetry-processor-final \
  --current-application-version-id 1 \
  --application-configuration-update '{
    "VpcConfigurationUpdates": [
      {
        "VpcConfigurationId": "1.0",
        "SubnetIdUpdates": ["subnet-0dbd978b605f5efef", "subnet-09ebb6d0054c45fa4"],
        "SecurityGroupIdUpdates": ["sg-0c5536f3dfcb18130"]
      }
    ]
  }'

echo "VPC configuration added. Restart the application:"
echo "aws kinesisanalyticsv2 stop-application --profile target-account --application-name cms-telemetry-processor-final"
echo "aws kinesisanalyticsv2 start-application --profile target-account --application-name cms-telemetry-processor-final"
