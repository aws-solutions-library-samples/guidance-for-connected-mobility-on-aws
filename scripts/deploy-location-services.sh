#!/bin/bash

# Deploy UI Stack with Amazon Location Services
# This script deploys the UI stack which now includes Location Services resources

set -e

echo "🗺️  Deploying UI Stack with Amazon Location Services..."

# Set deployment stage
DEPLOYMENT_STAGE=${DEPLOYMENT_STAGE:-dev}

# Navigate to deployment directory
cd "$(dirname "$0")/../deployment"

# Deploy UI stack with integrated location services
echo "📦 Deploying cms-${DEPLOYMENT_STAGE}-ui stack with integrated location services..."
cdk deploy cms-${DEPLOYMENT_STAGE}-ui --require-approval never

echo "✅ UI Stack with Amazon Location Services deployment complete!"
echo ""
echo "📋 Resources created in UI stack:"
echo "   - Map: cms-vehicle-map"
echo "   - Place Index: cms-place-index" 
echo "   - Route Calculator: cms-route-calculator"
echo ""
echo "🌐 Frontend now uses Amazon Location Services for mapping!"
echo "   Visit your CloudFront URL to see the updated maps."
