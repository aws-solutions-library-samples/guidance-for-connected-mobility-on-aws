#!/usr/bin/env python3
"""
Regenerate the FWE DecoderManifest.bin protobuf and upload to S3.

What this produces
------------------
A FleetWise `DecoderManifest` protobuf, serialized to bytes, containing:

1. All CAN signals from the `cms-<stage>-decoder-manifest` DDB table
   (the authoritative source seeded by
   `deployment/scripts/seed_decoder_and_campaign.py`). These use
   `interface_id="1"` (the CAN network interface defined in FWE's
   static config).

2. 9 `CustomDecodingSignal` entries for `Vehicle.ECU1.DTC_INFO` through
   `Vehicle.ECU9.DTC_INFO`, each:
     - `interface_id="UDS_DTC"` (matches the exampleUDSInterface entry
       that `simulation_stack.py` / `simulation_lambda.py` inject into
       FWE's static config in CP6)
     - `custom_decoding_id="ECU<N>"` (the ECU name — FWE passes this
       to the UDS interface when it fires a DTC_QUERY for this signal)
     - `primitive_type=STRING` (DTC responses come back as a JSON
       envelope string that `FWTelemetryProcessor` parses in CP5)
     - Signal IDs 901-909 — above the existing CAN signal range (max=287)
       so there's no collision risk.

The file is uploaded to
`s3://cms-<stage>-flink-flinkjarbucketd8dc3634-<suffix>/fwe-config/DecoderManifest.bin`
and the `CampaignSyncProcessor` Flink app reads it from there and publishes
it via MQTT to each FWE agent on checkin (topic
`cms/fleetwise/vehicles/{vin}/decoder_manifests`).

Usage
-----
    python3 deployment/scripts/generate_decoder_manifest.py
    DEPLOYMENT_STAGE=prod python3 deployment/scripts/generate_decoder_manifest.py

Environment variables
---------------------
    DEPLOYMENT_STAGE    defaults to 'prod'
    AWS_PROFILE         defaults to 'default'
    AWS_REGION          defaults to 'us-east-1'
    DRY_RUN             if set ('1'/'true'), writes output to
                        ./DecoderManifest.bin locally but does not upload
                        to S3. Useful for diffing.

Rollback
--------
S3 object is versioned. Prior version IDs are visible via
    aws s3api list-object-versions --bucket <bucket> --prefix fwe-config/
and can be restored via `copy-object --copy-source ...?versionId=<id>`.
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time


# Defaults used by the rest of the CMS stack — these match what the
# archived generate_fwe_persistency.py and seed_decoder_and_campaign.py
# use, so the decoder manifest we emit is interchangeable with theirs.
DECODER_NAME = "cms-fleet-v3"
DECODER_VERSION = "1"

# UDS-DTC interface — must match the interfaceId we put into FWE's static
# config in CP6 (deployment/stacks/simulation_stack.py) and the one our
# UDS responder binds to in CP2 (services/simulation/uds_dtc_responder.py).
UDS_INTERFACE_ID = "UDS_DTC"

# 9 virtual ECUs. These names match the ECU groupings in the handoff
# (ECU_BRAKE, ECU_ENGINE, ECU_POWERTRAIN, ECU_PCM, ECU_COMM,
# ECU_BATTERY_HV, ECU_BATTERY_12V, ECU_EVAP, ECU_BODY). We use ECU1..ECU9
# as generic slots; the mapping from DTC prefix → ECU is done at runtime
# in CP8 (simulation_lambda.py) when it builds UDS_DTC_MAP.
UDS_ECU_FQNS = [f"Vehicle.ECU{i}.DTC_INFO" for i in range(1, 10)]

# Signal IDs for the 9 DTC_INFO signals. Chosen as 901..909 — well above
# the current CAN signal ID range (max=287) so there's no collision now
# or after future CAN signal additions.
UDS_SIGNAL_IDS = list(range(901, 910))


def compile_proto_if_needed(proto_src: str, out_dir: str) -> None:
    """Compile decoder_manifest.proto → decoder_manifest_pb2.py into out_dir."""
    pb2_file = os.path.join(out_dir, "decoder_manifest_pb2.py")
    if os.path.exists(pb2_file):
        return
    os.makedirs(out_dir, exist_ok=True)
    proto_dir = os.path.dirname(proto_src)
    subprocess.check_call([
        "protoc",
        f"--python_out={out_dir}",
        f"--proto_path={proto_dir}",
        proto_src,
    ])


def load_can_signals_from_ddb(session, stage: str) -> list:
    """Fetch CAN signal rows from cms-<stage>-decoder-manifest.

    Returns list of (fqn, signal_id, interface_id, can_params_dict) tuples
    sorted by fqn for stable output.

    Also pulls signal_ids from cms-<stage>-signal-catalog so they match
    what CampaignSyncProcessor and FWTelemetryProcessor expect.
    """
    import zstandard

    ddb = session.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    dm_table = ddb.Table(f"cms-{stage}-decoder-manifest")
    cat_table = ddb.Table(f"cms-{stage}-signal-catalog")

    # Query all SIGNAL_DECODER#... rows under our decoder pk.
    pk = f"DECODER#{DECODER_NAME}#{DECODER_VERSION}"
    resp = dm_table.query(
        KeyConditionExpression="pk = :pk AND begins_with(sk, :prefix)",
        ExpressionAttributeValues={":pk": pk, ":prefix": "SIGNAL_DECODER#"},
    )
    rows = resp["Items"]
    # Handle pagination if we ever exceed 1MB of DDB results.
    while "LastEvaluatedKey" in resp:
        resp = dm_table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :prefix)",
            ExpressionAttributeValues={":pk": pk, ":prefix": "SIGNAL_DECODER#"},
            ExclusiveStartKey=resp["LastEvaluatedKey"],
        )
        rows.extend(resp["Items"])

    # Signal catalog is the authoritative signal_id source.
    cat_scan = cat_table.scan(ProjectionExpression="vss_path, signal_id")
    cat_rows = cat_scan.get("Items", [])
    while "LastEvaluatedKey" in cat_scan:
        cat_scan = cat_table.scan(
            ProjectionExpression="vss_path, signal_id",
            ExclusiveStartKey=cat_scan["LastEvaluatedKey"],
        )
        cat_rows.extend(cat_scan.get("Items", []))
    catalog_ids = {r["vss_path"]: int(r["signal_id"])
                   for r in cat_rows if "signal_id" in r and "vss_path" in r}

    decompressor = zstandard.ZstdDecompressor()
    out = []
    for i, row in enumerate(sorted(rows, key=lambda r: r.get("fullyQualifiedName", ""))):
        fqn = row.get("fullyQualifiedName", "")
        payload_b64 = row.get("signalDecoderPayload", "")
        if isinstance(payload_b64, str):
            compressed = base64.b64decode(payload_b64)
        else:
            compressed = bytes(payload_b64)
        can_params = json.loads(decompressor.decompress(compressed))
        signal_id = catalog_ids.get(fqn, i + 1)
        interface_id = row.get("interfaceId", "1")
        out.append((fqn, signal_id, interface_id, can_params))

    return out


def build_manifest(dm_pb2, can_rows: list):
    """Construct a DecoderManifest protobuf object ready to serialize."""
    m = dm_pb2.DecoderManifest()
    m.sync_id = DECODER_NAME

    for fqn, signal_id, interface_id, can_params in can_rows:
        s = m.can_signals.add()
        s.signal_id = signal_id
        s.interface_id = interface_id
        s.message_id = can_params["messageId"]
        s.is_big_endian = can_params.get("isBigEndian", False)
        s.is_signed = can_params.get("isSigned", False)
        s.start_bit = can_params["startBit"]
        s.offset = can_params.get("offset", 0.0)
        s.factor = can_params.get("factor", 1.0)
        s.length = can_params["length"]
        s.primitive_type = dm_pb2.FLOAT64

    # Append the 9 DTC_INFO CustomDecodingSignal entries. These are the
    # UDS-DTC injection point — FWE's ExampleUDSInterface will fire DTC
    # queries for these signals when a campaign's signalsToFetch
    # references them with the DTC_QUERY custom function.
    for ecu_fqn, sig_id in zip(UDS_ECU_FQNS, UDS_SIGNAL_IDS):
        cds = m.custom_decoding_signals.add()
        cds.signal_id = sig_id
        cds.interface_id = UDS_INTERFACE_ID
        # See UDS_ECU_FQNS comment above: custom_decoding_id must be the
        # FQN string, not just "ECU1" etc.
        cds.custom_decoding_id = ecu_fqn
        cds.primitive_type = dm_pb2.STRING

    return m


def upsert_decoder_manifest_ddb_rows(session, stage: str) -> int:
    """Upsert the 9 Vehicle.ECUx.DTC_INFO rows into cms-<stage>-decoder-manifest.

    Why this is separate from the .bin write:
      FWE consumes DecoderManifest.bin (protobuf) from S3 via CampaignSyncProcessor.
      FWTelemetryProcessor consumes the DDB mirror of the manifest (cms-prod-decoder-manifest)
      to build its signal_id → fullyQualifiedName lookup. If DDB is missing the 9
      UDS-DTC rows, FWTelemetryProcessor receives a STRING-valued CapturedSignal
      with signal_id=901 but maps it to the fallback "signal_901" name — which
      doesn't end in ".DTC_INFO", so CP5's handleUdsDtcInfo() never runs, and
      no row lands in dtc-history.

      Both places (.bin + DDB) must agree.

    Idempotent. Safe to re-run.
    """
    ddb = session.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    table = ddb.Table(f"cms-{stage}-decoder-manifest")
    pk = f"DECODER#{DECODER_NAME}#{DECODER_VERSION}"
    written = 0
    for ecu_fqn, sig_id in zip(UDS_ECU_FQNS, UDS_SIGNAL_IDS):
        table.put_item(Item={
            "pk": pk,
            "sk": f"SIGNAL_DECODER#{ecu_fqn}",
            "decoderManifestName": DECODER_NAME,
            "decoderManifestVersion": DECODER_VERSION,
            "fullyQualifiedName": ecu_fqn,
            "signalId": sig_id,
            "interfaceId": UDS_INTERFACE_ID,
            "signalDecoderType": "CUSTOM_DECODING_SIGNAL",
            "dataType": "STRING",
            "customDecodingId": ecu_fqn,  # must match custom_decoding_id in DecoderManifest.bin
        })
        written += 1
    return written


def upload_to_s3(session, stage: str, manifest_bytes: bytes) -> str:
    """Upload to the flink-jar bucket under fwe-config/DecoderManifest.bin."""
    region = os.environ.get("AWS_REGION", "us-east-1")
    cfn = session.client("cloudformation", region_name=region)
    resp = cfn.describe_stacks(StackName=f"cms-{stage}-flink")
    bucket = next(
        o["OutputValue"] for o in resp["Stacks"][0]["Outputs"]
        if o["OutputKey"] == "FlinkJarBucketOutput"
    )
    s3 = session.client("s3", region_name=region)
    put = s3.put_object(
        Bucket=bucket,
        Key="fwe-config/DecoderManifest.bin",
        Body=manifest_bytes,
        ContentType="application/octet-stream",
    )
    return f"s3://{bucket}/fwe-config/DecoderManifest.bin (VersionId={put.get('VersionId', '-')})"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "default"))
    p.add_argument("--stage", default=os.environ.get("DEPLOYMENT_STAGE", "prod"))
    p.add_argument("--dry-run", action="store_true",
                   default=os.environ.get("DRY_RUN", "").lower() in ("1", "true"),
                   help="Build but do not upload to S3; write locally as "
                        "./DecoderManifest.bin")
    p.add_argument("--out", default="./DecoderManifest.bin",
                   help="Local output path (default: ./DecoderManifest.bin)")
    args = p.parse_args(argv)

    import boto3
    session = boto3.Session(profile_name=args.profile)

    # Compile the proto at runtime — no manual setup required.
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    proto_src = os.path.join(repo_root, "modules", "flink", "src", "main", "proto",
                              "decoder_manifest.proto")
    if not os.path.exists(proto_src):
        print(f"ERROR: proto source not found at {proto_src}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmpdir:
        compile_proto_if_needed(proto_src, tmpdir)
        sys.path.insert(0, tmpdir)
        import decoder_manifest_pb2 as dm_pb2  # noqa: E402

        print(f"Building decoder manifest for stage={args.stage}")
        can_rows = load_can_signals_from_ddb(session, args.stage)
        print(f"  CAN signals from DDB: {len(can_rows)}")

        manifest = build_manifest(dm_pb2, can_rows)
        print(f"  CAN signals in manifest: {len(manifest.can_signals)}")
        print(f"  Custom decoding signals: {len(manifest.custom_decoding_signals)}")
        for cds in manifest.custom_decoding_signals:
            print(f"    signal_id={cds.signal_id} interface_id={cds.interface_id!r} "
                  f"custom_decoding_id={cds.custom_decoding_id!r} "
                  f"primitive_type={dm_pb2.PrimitiveType.Name(cds.primitive_type)}")

        manifest_bytes = manifest.SerializeToString()
        print(f"  Serialized: {len(manifest_bytes)} bytes")

        with open(args.out, "wb") as f:
            f.write(manifest_bytes)
        print(f"  Wrote: {args.out}")

        if args.dry_run:
            print("  DRY RUN — skipping S3 upload.")
            return 0

        url = upload_to_s3(session, args.stage, manifest_bytes)
        print(f"  Uploaded: {url}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
