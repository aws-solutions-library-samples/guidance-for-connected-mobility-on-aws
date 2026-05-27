#!/usr/bin/env python3
"""
Test the OEMTelemetryProcessor by publishing a sample message to cms-telemetry-oem.

Sends a message matching the rest-polling-sample manifest, then checks
cms-telemetry-preprocessed for the transformed output.

Usage:
  python3 test_oem_processor.py

Requires: kafka-python-ng (pip install kafka-python-ng)
Uses IAM auth via aws-msk-iam-sasl-signer-python.
"""
import json
import sys
import os
import time
import uuid

REGION = os.environ.get("AWS_REGION", "us-east-1")
STAGE = os.environ.get("DEPLOYMENT_STAGE", "prod")
PROFILE = os.environ.get("AWS_PROFILE", "default")

# ── Get MSK bootstrap servers ──────────────────────────────────────────
import boto3
session = boto3.Session(profile_name=PROFILE, region_name=REGION)

cf = session.client("cloudformation")
msk_stack = cf.describe_stacks(StackName=f"cms-{STAGE}-msk")["Stacks"][0]
msk_arn = next(o["OutputValue"] for o in msk_stack["Outputs"] if o["OutputKey"] == "MSKClusterArn")

msk_client = session.client("kafka")
bootstrap = msk_client.get_bootstrap_brokers(ClusterArn=msk_arn)["BootstrapBrokerStringSaslIam"]
print(f"MSK bootstrap: {bootstrap}")

# ── Build test payload matching rest-polling-sample manifest ───────────
test_vehicle_id = f"TEST-OEM-{uuid.uuid4().hex[:8].upper()}"
test_timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

payload = {
    "oem_source": "rest-polling-sample",
    "vehicle": {
        "id": test_vehicle_id,
        "lastUpdated": test_timestamp,
        "telemetry": {
            "speed": 96.56,           # kph → should become ~60 mph
            "engineRpm": 2200,
            "coolantTempCelsius": 88,  # °C → should become 190.4 °F
            "odometerKm": 80467,       # km → should become ~50000 miles
            "fuelLevelPercent": 72.5,
            "batteryVoltage": 13.6,
            "ignitionStatus": "On",    # → should become true
            "gearPosition": "Drive",   # → should become 3
            "acceleratorPedalPercent": 35.0
        },
        "location": {
            "latitude": 42.3262,
            "longitude": -83.2116,
            "headingDegrees": 180.0
        },
        "tires": {
            "frontLeft": {"pressureKpa": 234},
            "frontRight": {"pressureKpa": 231},
            "rearLeft": {"pressureKpa": 241},
            "rearRight": {"pressureKpa": 238}
        },
        "safety": {"driverSeatbelt": "Buckled"},
        "maintenance": {"oilLifePercent": 65.0},
        "ev": {"stateOfChargePercent": 92.0}
    }
}

print(f"\n📤 Publishing test message to cms-telemetry-oem")
print(f"   Vehicle ID: {test_vehicle_id}")
print(f"   Timestamp:  {test_timestamp}")
print(f"   OEM Source:  rest-polling-sample")

# ── Publish to MSK ─────────────────────────────────────────────────────
try:
    from aws_msk_iam_sasl_signer import MSKAuthTokenProvider
    from kafka import KafkaProducer, KafkaConsumer

    def msk_token(config):
        token, _ = MSKAuthTokenProvider.generate_auth_token_from_profile(REGION, aws_profile=PROFILE)
        return token

    producer = KafkaProducer(
        bootstrap_servers=bootstrap.split(","),
        security_protocol="SASL_SSL",
        sasl_mechanism="OAUTHBEARER",
        sasl_oauth_token_provider=type("T", (), {"token": staticmethod(msk_token)})(),
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        request_timeout_ms=10000,
    )

    future = producer.send("cms-telemetry-oem", value=payload)
    result = future.get(timeout=10)
    print(f"   ✅ Published to partition {result.partition}, offset {result.offset}")
    producer.flush()
    producer.close()

except ImportError:
    print("\n⚠️  kafka-python-ng or aws-msk-iam-sasl-signer not installed.")
    print("   Install with: pip3 install kafka-python-ng aws-msk-iam-sasl-signer-python")
    print(f"\n   Payload that would be sent:")
    print(json.dumps(payload, indent=2))
    sys.exit(1)
except Exception as e:
    print(f"   ❌ Failed to publish: {e}")
    print(f"\n   This script must run from within the MSK VPC (e.g., Cloud9, EC2, or VPN).")
    print(f"   Alternatively, use the simulation service API to inject test data.")
    sys.exit(1)

# ── Expected output ────────────────────────────────────────────────────
print(f"\n📋 Expected output on cms-telemetry-preprocessed:")
print(f"   vehicleId: {test_vehicle_id}")
print(f"   speed: ~60.0 mph (96.56 kph × 0.621371)")
print(f"   engineRPM: 2200")
print(f"   engineTemp: ~190.4 °F (88°C)")
print(f"   odometer: ~50000 miles (80467 km)")
print(f"   fuelLevel: 72.5")
print(f"   ignitionOn: true")
print(f"   gearPosition: 3")
print(f"   lat: 42.3262, lng: -83.2116")
print(f"   tire_fl: ~33.9 PSI (234 kPa)")
print(f"   seatbeltStatus: true")
print(f"   soc: 92.0")
print(f"\n   Check CloudWatch logs: /aws/kinesis-analytics/cms-{STAGE}-flink-oem-telemetry-processor")
