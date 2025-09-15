#!/bin/bash

echo "=== Fixing Flink to MSK Connectivity ==="

# The issue: Flink application has no VPC configuration
# MSK cluster is in VPC: vpc-06b8e7969b467b593
# MSK subnets: subnet-0dbd978b605f5efef, subnet-09ebb6d0054c45fa4  
# MSK security group: sg-0c5536f3dfcb18130

echo "Current Flink application has no VPC configuration."
echo "MSK cluster requires VPC connectivity."
echo ""
echo "SOLUTION: You need to recreate the Flink application with VPC configuration."
echo ""
echo "Steps to fix:"
echo "1. Stop current application:"
echo "   aws kinesisanalyticsv2 stop-application --profile target-account --application-name cms-telemetry-processor-final"
echo ""
echo "2. Delete current application:"
echo "   aws kinesisanalyticsv2 delete-application --profile target-account --application-name cms-telemetry-processor-final"
echo ""
echo "3. Recreate with VPC configuration using AWS Console or CDK"
echo "   - VPC: vpc-06b8e7969b467b593"
echo "   - Subnets: subnet-0dbd978b605f5efef, subnet-09ebb6d0054c45fa4"
echo "   - Security Group: sg-0c5536f3dfcb18130"
echo ""
echo "4. Configure environment properties:"
echo "   bootstrap.servers: b-1.cmstelemetryclustersas.7v7vwf.c7.kafka.us-east-1.amazonaws.com:9096"
echo "   sasl.mechanism: SCRAM-SHA-512"
echo "   security.protocol: SASL_SSL"
echo "   sasl.jaas.config: Use secret AmazonMSK_cms_telemetry_925868"
echo ""
echo "The IAM role already has the correct permissions."
