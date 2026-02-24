#!/usr/bin/env python3
"""
Setup script for FleetWise Edge Agent.
Generates config, writes certificates, and optionally builds FWE.

Usage:
    python3 setup_fwe.py --vehicle-id VEH-1771335115 --profile givenand-CMS
    python3 setup_fwe.py --vehicle-id VEH-1771335115 --profile givenand-CMS --build
"""

import argparse
import boto3
import json
import os
import sys
import subprocess

REGION = 'us-east-1'
FWE_DIR = os.environ.get('FWE_DIR',
    os.path.join(os.path.dirname(__file__), '..', '..', 'aws-iot-fleetwise-edge'))
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'fwe_config')


def get_iot_endpoint(session):
    iot = session.client('iot', region_name=REGION)
    return iot.describe_endpoint(endpointType='iot:Data-ATS')['endpointAddress']


def get_vehicle_certificate(session, vehicle_id):
    ddb = session.resource('dynamodb', region_name=REGION)
    table = ddb.Table('cms-dev-storage-vehicle-certificates')
    resp = table.get_item(Key={'vehicleId': vehicle_id})
    if 'Item' not in resp:
        print(f"❌ No certificate found for {vehicle_id}")
        sys.exit(1)
    return resp['Item']


def write_certificates(cert_data, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    cert_file = os.path.join(output_dir, 'certificate.pem.crt')
    key_file = os.path.join(output_dir, 'private.pem.key')

    with open(cert_file, 'w') as f:
        f.write(cert_data['certificatePem'])
    with open(key_file, 'w') as f:
        f.write(cert_data['privateKey'])

    # Download Amazon Root CA
    ca_file = os.path.join(output_dir, 'AmazonRootCA1.pem')
    if not os.path.exists(ca_file):
        import urllib.request
        urllib.request.urlretrieve(
            'https://www.amazontrust.com/repository/AmazonRootCA1.pem', ca_file)

    print(f"✅ Certificates written to {output_dir}")
    return cert_file, key_file, ca_file


def generate_config(endpoint, thing_name, cert_file, key_file, output_dir,
                    topic_prefix='$aws/rules/fw_dev_iot_msk_rule/'):
    config = {
        "version": "1.0",
        "networkInterfaces": [
            {
                "canInterface": {
                    "interfaceName": "vcan0",
                    "protocolName": "CAN",
                    "protocolVersion": "2.0A"
                },
                "interfaceId": "1",
                "type": "canInterface"
            }
        ],
        "staticConfig": {
            "bufferSizes": {
                "dtcBufferSize": 100,
                "decodedSignalsBufferSize": 10000,
                "rawCANFrameBufferSize": 10000
            },
            "threadIdleTimes": {
                "inspectionThreadIdleTimeMs": 50,
                "socketCANThreadIdleTimeMs": 50,
                "canDecoderThreadIdleTimeMs": 50
            },
            "persistency": {
                "persistencyPath": os.path.join(output_dir, "persistency"),
                "persistencyPartitionMaxSize": 524288,
                "persistencyUploadRetryIntervalMs": 10000
            },
            "internalParameters": {
                "readyToPublishDataBufferSize": 10000,
                "systemWideLogLevel": "Info",
                "maximumAwsSdkHeapMemoryBytes": 10000000
            },
            "publishToCloudParameters": {
                "maxPublishMessageCount": 1000,
                "collectionSchemeManagementCheckinIntervalMs": 30000
            },
            "mqttConnection": {
                "connectionType": "iotCore",
                "endpointUrl": endpoint,
                "clientId": thing_name,
                "keepAliveIntervalSeconds": 60,
                "certificateFilename": cert_file,
                "privateKeyFilename": key_file,
                "iotFleetWiseTopicPrefix": topic_prefix
            }
        }
    }

    os.makedirs(os.path.join(output_dir, "persistency"), exist_ok=True)
    config_file = os.path.join(output_dir, 'static-config.json')
    with open(config_file, 'w') as f:
        json.dump(config, f, indent=2)

    print(f"✅ Config written to {config_file}")
    print(f"   Endpoint: {endpoint}")
    print(f"   Client ID: {thing_name}")
    print(f"   Topic prefix: {topic_prefix}")
    return config_file


def check_fwe_binary():
    """Check if FWE is built."""
    binary = os.path.join(FWE_DIR, 'build', 'src', 'executionmanagement',
                          'aws-iot-fleetwise-edge')
    if os.path.exists(binary):
        print(f"✅ FWE binary found: {binary}")
        return binary

    # Also check common install locations
    for path in ['/usr/local/bin/aws-iot-fleetwise-edge',
                 os.path.expanduser('~/aws-iot-fleetwise-edge/build/src/executionmanagement/aws-iot-fleetwise-edge')]:
        if os.path.exists(path):
            print(f"✅ FWE binary found: {path}")
            return path

    print("❌ FWE binary not found")
    return None


def build_fwe():
    """Build FWE from source (Linux only)."""
    import platform
    if platform.system() != 'Linux':
        print("❌ FWE can only be built on Linux (requires SocketCAN)")
        print("   Options:")
        print("   1. Build on an EC2 instance (Amazon Linux 2 / Ubuntu)")
        print("   2. Use Docker: docker run -it ubuntu:22.04")
        print(f"   3. Cross-compile from {FWE_DIR}/tools/build-fwe-native.sh")
        return None

    print("🔧 Building FWE from source...")
    deps_script = os.path.join(FWE_DIR, 'tools', 'install-deps-native.sh')
    build_script = os.path.join(FWE_DIR, 'tools', 'build-fwe-native.sh')

    subprocess.run(['sudo', deps_script], check=True)
    subprocess.run([build_script], check=True, cwd=FWE_DIR)

    return check_fwe_binary()


def main():
    parser = argparse.ArgumentParser(description='Setup FleetWise Edge Agent')
    parser.add_argument('--vehicle-id', required=True, help='Vehicle ID in DDB (e.g. VEH-1771335115)')
    parser.add_argument('--profile', default='default', help='AWS profile')
    parser.add_argument('--topic-prefix', default='cms/fleetwise/',
                        help='MQTT topic prefix for FWE (non-reserved CMS topic)')
    parser.add_argument('--build', action='store_true', help='Build FWE from source')
    parser.add_argument('--output-dir', default=OUTPUT_DIR, help='Output directory for config/certs')
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile)

    print("🔧 FleetWise Edge Agent Setup")
    print("=" * 50)

    # 1. Get IoT endpoint
    endpoint = get_iot_endpoint(session)
    print(f"✅ IoT endpoint: {endpoint}")

    # 2. Get vehicle certificate
    cert_data = get_vehicle_certificate(session, args.vehicle_id)
    thing_name = cert_data.get('thingName', args.vehicle_id)
    print(f"✅ Vehicle: {args.vehicle_id}, Thing: {thing_name}")

    # 3. Write certificates
    cert_file, key_file, ca_file = write_certificates(cert_data, args.output_dir)

    # 4. Generate config
    config_file = generate_config(endpoint, thing_name, cert_file, key_file,
                                  args.output_dir, args.topic_prefix)

    # 5. Check/build FWE binary
    binary = check_fwe_binary()
    if not binary and args.build:
        binary = build_fwe()

    print()
    print("=" * 50)
    if binary:
        print("🚀 To run FWE:")
        print(f"   {binary} {config_file}")
    else:
        print("⚠️  FWE binary not found. Build it on Linux with:")
        print(f"   cd {FWE_DIR}")
        print(f"   sudo tools/install-deps-native.sh")
        print(f"   tools/build-fwe-native.sh")
        print()
        print(f"   Then run: ./build/src/executionmanagement/aws-iot-fleetwise-edge {config_file}")

    print()
    print("📋 Config summary:")
    print(f"   Config:     {config_file}")
    print(f"   Cert:       {cert_file}")
    print(f"   Key:        {key_file}")
    print(f"   Endpoint:   {endpoint}")
    print(f"   Thing:      {thing_name}")
    print(f"   Topic:      {args.topic_prefix}")
    print(f"   CAN:        vcan0")


if __name__ == '__main__':
    main()
