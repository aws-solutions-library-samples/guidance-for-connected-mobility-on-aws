#!/bin/bash

# Run Updated Historical Data Injection WITHOUT Backups
# For faster execution when backups are not needed

echo "🚀 Starting Updated Historical Data Injection (No Backup)..."
echo "📅 Injecting 2 weeks of trip data (200 vehicles)"
echo "⚠️  Including abnormal safety events (lane departures, hard braking)"
echo "🇩🇪 Using Munich fleet patterns"
echo "⚠️  Skipping table backup for faster execution"
echo ""

# Run the updated injector without backups
python3 updated_historical_data_injector.py \
    --profile target-account \
    --region us-east-1 \
    --days 14 \
    --no-backup

echo ""
echo "✅ Injection completed!"
echo "📊 Data injected into:"
echo "   • cms-631ca2-591631-trips-new"
echo "   • cms-631ca2-591631-safety-events-new"
