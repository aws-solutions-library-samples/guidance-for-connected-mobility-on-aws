#!/usr/bin/env python3
import sys
sys.path.append('.')
from generate_90day_reduced import ReducedHistoricalDataGenerator
from datetime import datetime, timezone, timedelta

# Test with minimal data
generator = ReducedHistoricalDataGenerator()

# Create one test vehicle
test_vehicle = {
    'vehicleId': 'test-batch-001',
    'assignedDriver': 'driver-001',
    'city': 'seattle',
    'status': 'active'
}

# Generate one day of data for one vehicle
start_date = datetime.now(timezone.utc) - timedelta(days=1)
trips, telemetry, safety_alerts, maintenance_alerts = generator.generate_trip_batch([test_vehicle], start_date, 1)

print(f"Generated: {len(trips)} trips, {len(telemetry)} telemetry records")

# Test inserting telemetry
if telemetry:
    try:
        generator.insert_data_batch('cms-0a0e68e9-telemetry', telemetry[:5])  # Just 5 records
        print("✅ Batch telemetry insertion successful")
    except Exception as e:
        print(f"❌ Batch error: {e}")
        print(f"Sample record: {telemetry[0]}")
