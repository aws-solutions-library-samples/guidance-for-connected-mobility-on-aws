#!/bin/bash

# Run Updated Historical Data Injection for New Tables
# Injects 2 weeks of Munich trip data with abnormal safety events
# Creates table backup first

echo "🚀 Starting Updated Historical Data Injection..."
echo "📅 Injecting 2 weeks of trip data (200 vehicles)"
echo "⚠️  Including abnormal safety events (lane departures, hard braking)"
echo "🇩🇪 Using Munich fleet patterns"
echo "💾 Creating table backup first"
echo ""

# Run the updated injector with backups
python3 updated_historical_data_injector.py \
    --profile target-account \
    --region us-east-1 \
    --days 14

echo ""
echo "✅ Injection completed!"
echo "📊 Check tables for new data:"
echo "   • cms-631ca2-591631-trips-new"
echo "   • cms-631ca2-591631-safety-events-new"
echo ""
echo "💾 Table backups created with timestamp for rollback if needed"
