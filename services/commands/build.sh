#!/usr/bin/env bash
set -euo pipefail
# Build the commands Lambda deployment package
DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$DIR/.build"

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# Install dependencies
pip install -r "$DIR/requirements.txt" -t "$BUILD_DIR" --quiet

# Copy Lambda source
cp "$DIR"/commands_lambda.py "$DIR"/command_request_pb2.py \
   "$DIR"/command_response_pb2.py "$DIR"/command_response_handler.py \
   "$BUILD_DIR"/

echo "Build complete: $BUILD_DIR"
