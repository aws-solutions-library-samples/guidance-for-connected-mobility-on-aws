#!/bin/bash

# CMS Telemetry Processor Build Script
# Builds the universal processor JAR with all dependencies

set -e

echo "🔨 Building CMS Telemetry Processor..."

# Set Java home if not set
if [ -z "$JAVA_HOME" ]; then
    export JAVA_HOME=/opt/homebrew/opt/openjdk@11
    echo "📍 Using Java: $JAVA_HOME"
fi

# Clean and build
echo "🧹 Cleaning previous build..."
mvn clean

echo "📦 Building JAR with dependencies..."
mvn package -q

# Check if build was successful
if [ -f "target/cms-telemetry-processor-1.0.0.jar" ]; then
    echo "✅ Build successful!"
    echo "📊 JAR size: $(ls -lh target/cms-telemetry-processor-1.0.0.jar | awk '{print $5}')"
    echo "📁 JAR location: target/cms-telemetry-processor-1.0.0.jar"

    # Stage the .zip asset for CDK BucketDeployment.
    #
    # The flink CDK BucketDeployment in deployment/stacks/flink_stack.py:103
    # uses `s3deploy.Source.asset("../modules/flink/target", exclude=["**", "!cms-telemetry-processor-1.0.0.zip"])`
    # — i.e. it globs ONLY the .zip filename. KDA Application config also
    # references `jars/cms-telemetry-processor-1.0.0.zip` (CodeContentType
    # ZIPFILE). The fat JAR produced by `mvn package` IS a valid zip
    # archive, so a literal copy with a .zip extension satisfies both.
    #
    # JAR-prune hazard: the BucketDeployment defaults to prune=True. If the
    # .zip is absent at deploy time, CDK syncs an empty jars/ prefix and
    # prunes the live JAR — every Flink app then crash-loops on next
    # restart with NoClassDefFoundError. Staging this here makes the build
    # self-sufficient (no manual `cp` step in operator runbooks).
    cp target/cms-telemetry-processor-1.0.0.jar target/cms-telemetry-processor-1.0.0.zip
    echo "📦 Staged target/cms-telemetry-processor-1.0.0.zip ($(ls -lh target/cms-telemetry-processor-1.0.0.zip | awk '{print $5}')) for CDK BucketDeployment"
else
    echo "❌ Build failed - JAR not found"
    exit 1
fi

echo "🎯 Ready for deployment!"
