#!/bin/bash

# Install Java and Kafka
sudo yum update -y
sudo yum install -y java-1.8.0-openjdk

# Download and extract Kafka
cd /home/ec2-user
wget https://archive.apache.org/dist/kafka/2.6.2/kafka_2.12-2.6.2.tgz
tar -xzf kafka_2.12-2.6.2.tgz
cd kafka_2.12-2.6.2

# Create the topic using ZooKeeper connection string
bin/kafka-topics.sh --create \
  --zookeeper z-1.cmstelemetrycluster925.1j6hkj.c7.kafka.us-east-1.amazonaws.com:2181,z-2.cmstelemetrycluster925.1j6hkj.c7.kafka.us-east-1.amazonaws.com:2181,z-3.cmstelemetrycluster925.1j6hkj.c7.kafka.us-east-1.amazonaws.com:2181 \
  --topic cms-telemetry-raw \
  --partitions 3 \
  --replication-factor 2

# List topics to verify creation
bin/kafka-topics.sh --list \
  --zookeeper z-1.cmstelemetrycluster925.1j6hkj.c7.kafka.us-east-1.amazonaws.com:2181,z-2.cmstelemetrycluster925.1j6hkj.c7.kafka.us-east-1.amazonaws.com:2181,z-3.cmstelemetrycluster925.1j6hkj.c7.kafka.us-east-1.amazonaws.com:2181

echo "Topic creation completed!"
