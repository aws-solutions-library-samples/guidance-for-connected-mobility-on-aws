#!/usr/bin/env python3
"""
Setup and run FleetWise Edge Agent as a Docker container.
No compilation needed — uses the FWE Dockerfile from the source repo.

Usage:
    # First time: build container image
    python3 setup_fwe.py --vehicle-id VEH-1771335115 --profile givenand-CMS --build-image

    # Run FWE
    python3 setup_fwe.py --vehicle-id VEH-1771335115 --profile givenand-CMS --run

    # Just generate config (no Docker)
    python3 setup_fwe.py --vehicle-id VEH-1771335115 --profile givenand-CMS
"""

import argparse
import boto3
import json
import os
import subprocess
import sys

REGION = 'us-east-1'
FWE_DIR = os.environ.get('FWE_DIR',
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'aws-iot-fleetwise-edge')))
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fwe_config')
IMAGE_NAME = 'cms-fwe-edge'
TOPIC_PREFIX = 'cms/fleetwise/'


def get_iot_endpoint(session):
    return session.client('iot', region_name=REGION).describe_endpoint(
        endpointType='iot:Data-ATS')['endpointAddress']


def get_vehicle_certificate(session, vehicle_id):
    table = session.resource('dynamodb', region_name=REGION).Table('cms-dev-storage-vehicle-certificates')
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
    print(f"✅ Certificates written to {output_dir}")
    return cert_file, key_file


def build_image():
    """Build FWE Docker image from source repo."""
    if not os.path.exists(FWE_DIR):
        print(f"❌ FWE source not found at {FWE_DIR}")
        print(f"   Clone it: git clone https://github.com/aws/aws-iot-fleetwise-edge.git")
        return False

    print(f"🔧 Building FWE native binary in Docker...")
    # Build the binary using the native build script inside a container
    build_cmd = [
        'docker', 'build',
        '-t', f'{IMAGE_NAME}-builder',
        '-f', '-',
        FWE_DIR
    ]

    dockerfile = f"""
FROM public.ecr.aws/ubuntu/ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y sudo git
COPY . /fwe
WORKDIR /fwe
RUN sudo tools/install-deps-native.sh
RUN tools/build-fwe-native.sh
"""
    result = subprocess.run(build_cmd, input=dockerfile.encode(), capture_output=True)
    if result.returncode != 0:
        print(f"❌ Build failed: {result.stderr.decode()[-500:]}")
        return False

    # Extract binary and build the runtime image
    print("📦 Building runtime image...")
    runtime_dockerfile = f"""
FROM public.ecr.aws/ubuntu/ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates iproute2 jq && rm -rf /var/lib/apt/lists/*
COPY --from={IMAGE_NAME}-builder /fwe/build/src/executionmanagement/aws-iot-fleetwise-edge /usr/bin/
COPY --from={IMAGE_NAME}-builder /fwe/tools/configure-fwe.sh /usr/bin/
COPY --from={IMAGE_NAME}-builder /fwe/tools/container/start-fwe.sh /usr/bin/
COPY --from={IMAGE_NAME}-builder /fwe/configuration/static-config.json /usr/share/aws-iot-fleetwise/
RUN chmod +x /usr/bin/start-fwe.sh /usr/bin/configure-fwe.sh
ENTRYPOINT ["/usr/bin/start-fwe.sh"]
"""
    result = subprocess.run(
        ['docker', 'build', '-t', IMAGE_NAME, '-f', '-', '.'],
        input=runtime_dockerfile.encode(), capture_output=True, cwd=FWE_DIR)
    if result.returncode != 0:
        print(f"❌ Runtime image build failed: {result.stderr.decode()[-500:]}")
        return False

    print(f"✅ Docker image built: {IMAGE_NAME}")
    return True


def check_image():
    """Check if FWE Docker image exists."""
    result = subprocess.run(['docker', 'images', '-q', IMAGE_NAME],
                            capture_output=True, text=True)
    return bool(result.stdout.strip())


def run_fwe(endpoint, thing_name, cert_file, key_file, topic_prefix):
    """Run FWE as a Docker container."""
    # Read certs as strings for passing as env vars
    with open(cert_file) as f:
        cert_pem = f.read()
    with open(key_file) as f:
        private_key = f.read()

    cmd = [
        'docker', 'run', '--rm',
        '--name', 'cms-fwe',
        '--network', 'host',  # Needed for vcan0 access
        '--privileged',       # Needed for CAN socket access
        '-e', f'ENDPOINT_URL={endpoint}',
        '-e', f'VEHICLE_NAME={thing_name}',
        '-e', f'CERTIFICATE={cert_pem}',
        '-e', f'PRIVATE_KEY={private_key}',
        '-e', f'IOT_FLEETWISE_TOPIC_PREFIX={topic_prefix}',
        '-e', 'CAN_BUS0=vcan0',
        '-e', 'LOG_LEVEL=Info',
        IMAGE_NAME,
        '--endpoint-url', endpoint,
        '--vehicle-name', thing_name,
        '--certificate', cert_pem,
        '--private-key', private_key,
        '--topic-prefix', topic_prefix,
        '--can-bus0', 'vcan0',
    ]

    print(f"🚀 Starting FWE container...")
    print(f"   Thing: {thing_name}")
    print(f"   Endpoint: {endpoint}")
    print(f"   Topics: {topic_prefix}vehicles/{thing_name}/signals")
    print(f"   CAN: vcan0")
    print(f"   Stop with: docker stop cms-fwe")
    print()

    os.execvp('docker', cmd)


def main():
    parser = argparse.ArgumentParser(description='Setup and run FleetWise Edge Agent')
    parser.add_argument('--vehicle-id', required=True, help='Vehicle ID in DDB')
    parser.add_argument('--profile', default='default', help='AWS profile')
    parser.add_argument('--topic-prefix', default=TOPIC_PREFIX, help='MQTT topic prefix')
    parser.add_argument('--build-image', action='store_true', help='Build Docker image')
    parser.add_argument('--run', action='store_true', help='Run FWE container')
    parser.add_argument('--output-dir', default=OUTPUT_DIR, help='Output directory')
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile)

    print("🔧 FleetWise Edge Agent Setup")
    print("=" * 50)

    # Get endpoint and certs
    endpoint = get_iot_endpoint(session)
    cert_data = get_vehicle_certificate(session, args.vehicle_id)
    thing_name = cert_data.get('thingName', args.vehicle_id)
    cert_file, key_file = write_certificates(cert_data, args.output_dir)

    print(f"✅ Endpoint: {endpoint}")
    print(f"✅ Vehicle: {args.vehicle_id} → Thing: {thing_name}")
    print(f"✅ Topic prefix: {args.topic_prefix}")

    # Build image if requested
    if args.build_image:
        if not build_image():
            sys.exit(1)

    # Check if image exists
    has_image = check_image()
    if has_image:
        print(f"✅ Docker image: {IMAGE_NAME}")
    else:
        print(f"⚠️  Docker image not found. Build with: python3 setup_fwe.py --vehicle-id {args.vehicle_id} --profile {args.profile} --build-image")

    # Run if requested
    if args.run:
        if not has_image:
            print("❌ Cannot run — Docker image not built")
            sys.exit(1)
        run_fwe(endpoint, thing_name, cert_file, key_file, args.topic_prefix)
    else:
        print()
        print("📋 To run FWE:")
        if has_image:
            print(f"   python3 setup_fwe.py --vehicle-id {args.vehicle_id} --profile {args.profile} --run")
        else:
            print(f"   1. python3 setup_fwe.py --vehicle-id {args.vehicle_id} --profile {args.profile} --build-image")
            print(f"   2. python3 setup_fwe.py --vehicle-id {args.vehicle_id} --profile {args.profile} --run")


if __name__ == '__main__':
    main()
