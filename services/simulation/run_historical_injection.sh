#!/bin/bash
# Quick script to run historical data injection

echo "🚀 Running Historical Data Injection..."
echo "This will populate your CMS UI with 30 days of test data"
echo ""

python3 historical_data_injector.py --profile target-account --days 30

echo ""
echo "✅ Historical data injection completed!"
echo "You can now view the data in your CMS UI at:"
echo "https://d9s69vesngdsu.cloudfront.net"
