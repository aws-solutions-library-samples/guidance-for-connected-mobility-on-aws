#!/bin/bash
# Quick script to run real-time telemetry simulation

echo "🚗 Starting Real-time Telemetry Simulation..."
echo "This will simulate live vehicle data for 30 minutes"
echo ""

python3 realtime_telemetry_simulator.py --profile target-account --duration 30 --vehicles 10

echo ""
echo "✅ Real-time simulation completed!"
echo "Check your CMS UI for live vehicle updates"
