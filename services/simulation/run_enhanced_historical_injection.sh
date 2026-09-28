#!/bin/bash

# Enhanced Historical Data Injection with Amazon Location Services
# This script generates realistic fleet management data with real routes

echo "🌍 Enhanced Historical Data Injection with Amazon Location Services"
echo "=================================================================="
echo ""
echo "This will generate:"
echo "  ✅ 5 fleets with different operational patterns"
echo "  ✅ 50 vehicles matched to fleet types"
echo "  ✅ Realistic trips with Amazon Location Services routing"
echo "  ✅ Safety events based on trip patterns"
echo "  ✅ Maintenance alerts based on vehicle usage"
echo "  ✅ Real street-level routes across major cities"
echo ""

# Default values
PROFILE="target-account"
DAYS=30
REGION="us-east-1"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --profile)
            PROFILE="$2"
            shift 2
            ;;
        --days)
            DAYS="$2"
            shift 2
            ;;
        --region)
            REGION="$2"
            shift 2
            ;;
        --help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --profile PROFILE    AWS profile name (default: target-account)"
            echo "  --days DAYS         Number of days of data (default: 30)"
            echo "  --region REGION     AWS region (default: us-east-1)"
            echo "  --help              Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

echo "🔧 Configuration:"
echo "   AWS Profile: $PROFILE"
echo "   Days of Data: $DAYS"
echo "   AWS Region: $REGION"
echo ""

# Check if AWS profile exists
if ! aws configure list-profiles | grep -q "^$PROFILE$"; then
    echo "❌ AWS profile '$PROFILE' not found"
    echo "Available profiles:"
    aws configure list-profiles
    exit 1
fi

# Check if Python script exists
if [ ! -f "enhanced_historical_data_injector.py" ]; then
    echo "❌ enhanced_historical_data_injector.py not found"
    echo "Please run this script from the simulation directory"
    exit 1
fi

# Install requirements if needed
echo "📦 Installing requirements..."
pip3 install -r requirements.txt --break-system-packages --quiet

# Run the enhanced data injection
echo "🚀 Starting enhanced data injection..."
echo ""

python3 enhanced_historical_data_injector.py \
    --profile "$PROFILE" \
    --days "$DAYS" \
    --region "$REGION"

if [ $? -eq 0 ]; then
    echo ""
    echo "🎉 Enhanced historical data injection completed successfully!"
    echo ""
    echo "📊 What was generated:"
    echo "   • Fleet data with operational patterns (commuter, delivery, service, emergency, construction)"
    echo "   • Vehicle data matched to fleet types and cities"
    echo "   • Trip data with real routes calculated via Amazon Location Services"
    echo "   • Safety events with realistic probabilities based on trip types"
    echo "   • Maintenance alerts based on vehicle age, mileage, and usage patterns"
    echo ""
    echo "🌐 Test your CMS UI at: https://d9s69vesngdsu.cloudfront.net"
    echo "🔗 API endpoints now have realistic data with actual street routes!"
else
    echo ""
    echo "❌ Enhanced data injection failed"
    echo "Check the error messages above for details"
    exit 1
fi
