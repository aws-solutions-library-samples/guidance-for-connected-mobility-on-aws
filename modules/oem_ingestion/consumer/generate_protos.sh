#!/bin/bash
# Generate Python code from Ford FCS proto files

echo "🔨 Generating Python proto code..."

python -m grpc_tools.protoc \
  --proto_path=protos \
  --python_out=. \
  --grpc_python_out=. \
  protos/autonomic/ext/feed/consumer/consumer.proto \
  protos/autonomic/ext/telemetry/metric.proto \
  protos/autonomic/ext/telemetry/signal.proto \
  protos/autonomic/ext/telemetry/well_known_signals.proto \
  protos/autonomic/ext/event/event.proto \
  protos/autonomic/ext/asset/asset.proto \
  protos/autonomic/ext/common/*.proto \
  protos/google/protobuf/*.proto \
  protos/google/type/*.proto

echo "✓ Proto generation complete"
