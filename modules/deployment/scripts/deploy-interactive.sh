#!/bin/bash
set -e

echo "🚀 CMS Interactive Deployment Script"
echo "===================================="

# Check current AWS account
CURRENT_ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "unknown")
echo "📍 Current AWS Account: $CURRENT_ACCOUNT"

# Account selection
echo ""
echo "Select target account:"
echo "1) Current account ($CURRENT_ACCOUNT)"
echo "2) Target account (470296731304)"
read -p "Choose account [1-2]: " account_choice

case $account_choice in
    1)
        AWS_PROFILE_ARG=""
        echo "✅ Deploying to current account"
        ;;
    2)
        AWS_PROFILE_ARG="AWS_PROFILE=$PROFILE"
        echo "✅ Deploying to target account (470296731304)"
        ;;
    *)
        echo "❌ Invalid choice. Exiting."
        exit 1
        ;;
esac

# Component selection
echo ""
echo "Select components to deploy:"
echo "1) UI Only (no telemetry)"
echo "2) Telemetry Only"
echo "3) Both UI and Telemetry"
read -p "Choose components [1-3]: " component_choice

case $component_choice in
    1)
        DEPLOY_MODE="ui-only"
        echo "✅ Deploying UI components only"
        ;;
    2)
        DEPLOY_MODE="telemetry-only"
        echo "✅ Deploying telemetry components only"
        ;;
    3)
        DEPLOY_MODE="both"
        echo "✅ Deploying both UI and telemetry"
        ;;
    *)
        echo "❌ Invalid choice. Exiting."
        exit 1
        ;;
esac

# Navigate to source directory
cd source
source ../.venv/bin/activate

# Deploy based on selection
case $DEPLOY_MODE in
    "ui-only")
        echo "🎯 Deploying UI-only stack..."
        eval "$AWS_PROFILE_ARG cdk deploy cms-ui-enhanced-single-stack --require-approval never --parameters MSKClusterArn='' --parameters BootstrapServers=''"
        ;;
    "telemetry-only")
        echo "🎯 Deploying telemetry stack..."
        echo "❌ Telemetry-only deployment not yet implemented"
        exit 1
        ;;
    "both")
        echo "🎯 Deploying full stack with telemetry..."
        read -p "Enter MSK Cluster ARN (or press Enter to skip): " msk_arn
        read -p "Enter Bootstrap Servers (or press Enter to skip): " bootstrap_servers
        
        if [[ -z "$msk_arn" ]]; then
            msk_arn=""
        fi
        if [[ -z "$bootstrap_servers" ]]; then
            bootstrap_servers=""
        fi
        
        eval "$AWS_PROFILE_ARG cdk deploy cms-ui-enhanced-single-stack --require-approval never --parameters MSKClusterArn='$msk_arn' --parameters BootstrapServers='$bootstrap_servers'"
        ;;
esac

echo ""
echo "✅ Deployment completed successfully!"
echo "📋 Next steps:"
echo "   1. Update your localhost runtime config with the new API endpoint"
echo "   2. Test the authentication flow"
echo "   3. Verify data connectivity"
