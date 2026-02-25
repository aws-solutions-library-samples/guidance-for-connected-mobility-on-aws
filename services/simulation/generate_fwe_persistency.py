#!/usr/bin/env python3
"""
Generate FWE persistency files (DecoderManifest.bin + CollectionSchemeList.bin)
from our DDB decoder manifest and campaign.

These files go into the FWE persistencyPath so FWE starts collecting
immediately without needing a checkin/sync cycle.

Usage:
    python3 generate_fwe_persistency.py --profile givenand-CMS
    python3 generate_fwe_persistency.py --profile givenand-CMS --output-dir ./fwe_config/persistency
"""

import sys
sys.path.insert(0, '/tmp/fw_proto')

import argparse
import boto3
import json
import os
import time
import base64
import zstandard

import decoder_manifest_pb2 as dm_pb2
import collection_schemes_pb2 as cs_pb2

REGION = 'us-east-1'
DECODER_NAME = 'cms-fleet-v1'
DECODER_VERSION = '1'
CAMPAIGN_NAME = 'cms-fleet-telemetry-30s'
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fwe_config', 'persistency')


def load_decoder_manifest(session):
    """Load decoder manifest signals from DDB and build protobuf."""
    ddb = session.resource('dynamodb', region_name=REGION)
    table = ddb.Table('cms-dev-decoder-manifest')

    # Get all signal decoders
    pk = f'DECODER#{DECODER_NAME}#{DECODER_VERSION}'
    resp = table.query(
        KeyConditionExpression='pk = :pk AND begins_with(sk, :prefix)',
        ExpressionAttributeValues={':pk': pk, ':prefix': 'SIGNAL_DECODER#'}
    )

    manifest = dm_pb2.DecoderManifest()
    manifest.sync_id = DECODER_NAME

    decompressor = zstandard.ZstdDecompressor()

    # Sort signals by FQN to assign consistent signal_ids
    signals = sorted(resp['Items'], key=lambda x: x.get('fullyQualifiedName', ''))

    for i, item in enumerate(signals):
        fqn = item.get('fullyQualifiedName', '')
        payload_b64 = item.get('signalDecoderPayload', '')

        # Decompress ZSTD payload
        if isinstance(payload_b64, str):
            compressed = base64.b64decode(payload_b64)
        else:
            compressed = bytes(payload_b64)
        decompressed = decompressor.decompress(compressed)
        can_params = json.loads(decompressed)

        # Build CAN signal protobuf
        can_signal = manifest.can_signals.add()
        can_signal.signal_id = i + 1  # 1-based
        can_signal.interface_id = item.get('interfaceId', '1')
        can_signal.message_id = can_params['messageId']
        can_signal.is_big_endian = can_params.get('isBigEndian', False)
        can_signal.is_signed = can_params.get('isSigned', False)
        can_signal.start_bit = can_params['startBit']
        can_signal.offset = can_params.get('offset', 0.0)
        can_signal.factor = can_params.get('factor', 1.0)
        can_signal.length = can_params['length']

    # Add GPS named signals (ExternalGpsSource)
    next_id = len(signals) + 1
    for name in ['Vehicle.CurrentLocation.Latitude', 'Vehicle.CurrentLocation.Longitude']:
        gps_sig = manifest.custom_decoding_signals.add()
        gps_sig.signal_id = next_id
        gps_sig.interface_id = 'GPS'
        gps_sig.custom_decoding_id = name
        gps_sig.primitive_type = dm_pb2.FLOAT64
        next_id += 1

    print(f'✅ Decoder manifest: {len(signals)} CAN + 2 GPS signals, sync_id={DECODER_NAME}')
    return manifest


def load_collection_scheme(session):
    """Load campaign collection scheme from S3 and build protobuf."""
    s3 = session.client('s3', region_name=REGION)
    bucket = 'cms-dev-transform-manifests-022035076260'
    key = f'campaigns/{CAMPAIGN_NAME}/v1/collection-scheme.json'

    obj = s3.get_object(Bucket=bucket, Key=key)
    scheme_json = json.loads(obj['Body'].read())

    schemes = cs_pb2.CollectionSchemes()
    schemes.timestamp_ms_epoch = int(time.time() * 1000)

    cs = schemes.collection_schemes.add()
    cs.campaign_sync_id = CAMPAIGN_NAME
    cs.decoder_manifest_sync_id = DECODER_NAME
    cs.start_time_ms_epoch = int(time.time() * 1000)
    cs.expiry_time_ms_epoch = 4102444800000  # 2099-12-31

    # Time-based collection
    period_ms = scheme_json['collectionScheme']['timeBasedCollectionScheme']['periodMs']
    cs.time_based_collection_scheme.time_based_collection_scheme_period_ms = period_ms

    # Add signals to collect
    for i, sig in enumerate(scheme_json['signalsToCollect']):
        si = cs.signal_information.add()
        si.signal_id = i + 1  # 1-based, matches decoder manifest order
        si.sample_buffer_size = sig.get('maxSampleCount', 1)
        si.minimum_sample_period_ms = sig.get('minimumSamplingIntervalMs', 0)
        si.fixed_window_period_ms = 0

    # Add GPS signals (IDs follow CAN signals)
    num_can = len(scheme_json['signalsToCollect'])
    for gps_offset in range(2):  # lat, lng
        si = cs.signal_information.add()
        si.signal_id = num_can + 1 + gps_offset
        si.sample_buffer_size = 1
        si.minimum_sample_period_ms = 0
        si.fixed_window_period_ms = 0

    cs.compress_collected_data = True
    cs.persist_all_collected_data = True
    cs.priority = 0

    total = len(scheme_json['signalsToCollect']) + 2
    print(f'✅ Collection scheme: {total} signals ({num_can} CAN + 2 GPS), period={period_ms}ms, campaign={CAMPAIGN_NAME}')
    return schemes


def main():
    parser = argparse.ArgumentParser(description='Generate FWE persistency files')
    parser.add_argument('--profile', default='default', help='AWS profile')
    parser.add_argument('--output-dir', default=OUTPUT_DIR, help='Output directory')
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile)
    os.makedirs(args.output_dir, exist_ok=True)

    print('🔧 Generating FWE persistency files')
    print('=' * 50)

    # Generate decoder manifest
    manifest = load_decoder_manifest(session)
    manifest_file = os.path.join(args.output_dir, 'DecoderManifest.bin')
    with open(manifest_file, 'wb') as f:
        f.write(manifest.SerializeToString())
    print(f'   Written: {manifest_file} ({os.path.getsize(manifest_file)} bytes)')

    # Generate collection scheme list
    schemes = load_collection_scheme(session)
    schemes_file = os.path.join(args.output_dir, 'CollectionSchemeList.bin')
    with open(schemes_file, 'wb') as f:
        f.write(schemes.SerializeToString())
    print(f'   Written: {schemes_file} ({os.path.getsize(schemes_file)} bytes)')

    print()
    print('📋 Mount this directory as the FWE persistencyPath:')
    print(f'   docker run ... -v {args.output_dir}:/var/aws-iot-fleetwise/ ...')


if __name__ == '__main__':
    main()
