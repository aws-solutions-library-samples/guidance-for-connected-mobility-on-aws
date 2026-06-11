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
else
    echo "❌ Build failed - JAR not found"
    exit 1
fi

echo "🎯 Ready for deployment!"
