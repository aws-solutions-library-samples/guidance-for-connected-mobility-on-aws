#!/usr/bin/env python3
"""
Setup and run FleetWise Edge Agent using the official AWS container image.
No compilation or image building needed.

Image: public.ecr.aws/aws-iot-fleetwise-edge/aws-iot-fleetwise-edge

Usage:
    # Generate config only
    python3 setup_fwe.py --vehicle-id 5YJ3E1EA1PF721240 --profile givenand-CMS

    # Run FWE container
    python3 setup_fwe.py --vehicle-id 5YJ3E1EA1PF721240 --profile givenand-CMS --run
"""

import argparse
import boto3
import os
import sys

REGION = 'us-east-1'
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fwe_config')
FWE_IMAGE = 'public.ecr.aws/aws-iot-fleetwise-edge/aws-iot-fleetwise-edge'
TOPIC_PREFIX = 'cms/fleetwise/'


def get_iot_endpoint(session):
    return session.client('iot', region_name=REGION).describe_endpoint(
        endpointType='iot:Data-ATS')['endpointAddress']


def get_vehicle_certificate(session, vehicle_id):
    table = session.resource('dynamodb', region_name=REGION).Table(f'cms-{os.environ.get('DEPLOYMENT_STAGE', 'dev')}-storage-vehicle-certificates')
    resp = table.get_item(Key={'vehicleId': vehicle_id})
    if 'Item' not in resp:
        print(f"❌ No certificate found for {vehicle_id}")
        sys.exit(1)
    return resp['Item']


def write_certificates(cert_data, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    cert_file = os.path.join(output_dir, 'certificate.pem')
    key_file = os.path.join(output_dir, 'private-key.key')
    with open(cert_file, 'w') as f:
        f.write(cert_data['certificatePem'])
    with open(key_file, 'w') as f:
        f.write(cert_data['privateKey'])
    return cert_file, key_file


def run_fwe(endpoint, thing_name, cert_file, key_file, topic_prefix):
    """Run FWE using the official AWS container image."""
    persistency_dir = os.path.join(OUTPUT_DIR, 'persistency')

    cmd = [
        'docker', 'run', '--rm', '-ti',
        '--name', 'cms-fwe',
        '--network', 'host',
        '-v', f'{cert_file}:/etc/aws-iot-fleetwise/certificate.pem',
        '-v', f'{key_file}:/etc/aws-iot-fleetwise/private-key.key',
        '-v', f'{persistency_dir}:/var/aws-iot-fleetwise/',
        '--env', f'VEHICLE_NAME={thing_name}',
        '--env', f'ENDPOINT_URL={endpoint}',
        '--env', f'IOT_FLEETWISE_TOPIC_PREFIX={topic_prefix}',
        '--env', 'CAN_BUS0=vcan0',
        '--env', 'LOG_LEVEL=Info',
        FWE_IMAGE,
    ]

    print(f"🚀 Starting FWE container")
    print(f"   Image:    {FWE_IMAGE}")
    print(f"   Thing:    {thing_name}")
    print(f"   Endpoint: {endpoint}")
    print(f"   Publish:  {topic_prefix}vehicles/{thing_name}/signals")
    print(f"   CAN:      vcan0")
    print(f"   Stop:     docker stop cms-fwe")
    print()

    os.execvp('docker', cmd)


def main():
    parser = argparse.ArgumentParser(description='Setup and run FleetWise Edge Agent')
    parser.add_argument('--vehicle-id', required=True, help='Vehicle ID in DDB')
    parser.add_argument('--profile', default='default', help='AWS profile')
    parser.add_argument('--topic-prefix', default=TOPIC_PREFIX, help='MQTT topic prefix')
    parser.add_argument('--run', action='store_true', help='Run FWE container')
    parser.add_argument('--output-dir', default=OUTPUT_DIR, help='Output directory for certs')
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile)

    print("🔧 FleetWise Edge Agent Setup")
    print("=" * 50)

    endpoint = get_iot_endpoint(session)
    cert_data = get_vehicle_certificate(session, args.vehicle_id)
    thing_name = cert_data.get('thingName', args.vehicle_id)
    cert_file, key_file = write_certificates(cert_data, args.output_dir)

    print(f"✅ Endpoint:  {endpoint}")
    print(f"✅ Vehicle:   {args.vehicle_id} → Thing: {thing_name}")
    print(f"✅ Certs:     {args.output_dir}")
    print(f"✅ Topics:    {args.topic_prefix}vehicles/{thing_name}/*")
    print(f"✅ Image:     {FWE_IMAGE}")

    if args.run:
        run_fwe(endpoint, thing_name, cert_file, key_file, args.topic_prefix)
    else:
        print()
        print("📋 To run:")
        print(f"   python3 setup_fwe.py --vehicle-id {args.vehicle_id} --profile {args.profile} --run")
        print()
        print("   Or manually:")
        print(f"   docker run --rm -ti --network host \\")
        print(f"     -v {cert_file}:/etc/aws-iot-fleetwise/certificate.pem \\")
        print(f"     -v {key_file}:/etc/aws-iot-fleetwise/private-key.key \\")
        print(f"     --env VEHICLE_NAME={thing_name} \\")
        print(f"     --env ENDPOINT_URL={endpoint} \\")
        print(f"     --env IOT_FLEETWISE_TOPIC_PREFIX={args.topic_prefix} \\")
        print(f"     --env CAN_BUS0=vcan0 \\")
        print(f"     {FWE_IMAGE}")


if __name__ == '__main__':
    main()
