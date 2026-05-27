#!/bin/bash
set -e

echo "🏗️  Building React frontend..."

# Generate runtime config from stack outputs
echo "🔧 Generating runtime config..."
cd "$(dirname "$0")"
python3 generate_runtime_config.py

# Navigate to frontend directory from cdk-stacks/scripts
cd ../../modules/cms_ui/source/frontend

# Install dependencies if needed
if [ ! -d "node_modules" ]; then
    echo "📦 Installing dependencies..."
    npm install
fi

# Build the frontend
echo "🔨 Building frontend..."
npm run build

echo "✅ Frontend build completed!"
