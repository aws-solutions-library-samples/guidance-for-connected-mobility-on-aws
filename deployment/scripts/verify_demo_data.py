#!/usr/bin/env python3
"""Post-bootstrap data verification.

Scans DDB tables and S3 buckets to confirm bootstrap-demo wrote real data.
Prints a green/red checklist and exits non-zero if any table is empty.

Usage:
    AWS_PROFILE=default AWS_REGION=us-east-1 DEPLOYMENT_STAGE=prod \\
        python3 deployment/scripts/verify_demo_data.py

The 'min_rows' thresholds are conservative lower bounds that account for:
    - 5 fleets x 10 vehicles = 50 vehicles (NUM_FLEETS x VEHICLES_PER_FLEET)
    - 730 days of trips (INJECT_DAYS default)
    - ~0.5 safety events per trip
    - ~15% of services trigger warranties
"""
import os
import sys
import boto3


STAGE = os.environ.get('DEPLOYMENT_STAGE', 'prod')
REGION = os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION') or 'us-east-1'
PROFILE = os.environ.get('AWS_PROFILE', 'default')

GREEN = '\033[0;32m'
RED = '\033[0;31m'
YELLOW = '\033[0;33m'
BOLD = '\033[1m'
BLUE = '\033[0;34m'
RESET = '\033[0m'


# Table name -> (minimum rows, description for human-readable output)
TABLE_EXPECTATIONS = {
    f'cms-{STAGE}-storage-fleets':               (3,      '3+ fleets'),
    f'cms-{STAGE}-storage-vehicles':             (30,     '30+ vehicles'),
    f'cms-{STAGE}-storage-drivers':              (30,     '30+ drivers'),
    f'cms-{STAGE}-storage-vehicle-certificates': (30,     '30+ IoT certificates'),
    f'cms-{STAGE}-storage-trips':                (1000,   '1000+ trips'),
    f'cms-{STAGE}-storage-safety-events':        (500,    '500+ safety events'),
    f'cms-{STAGE}-storage-maintenance-alerts':   (50,     '50+ maintenance alerts'),
    f'cms-{STAGE}-storage-service-history':      (300,    '300+ service records'),
    f'cms-{STAGE}-storage-warranty-claims':      (20,     '20+ warranty claims'),
    f'cms-{STAGE}-storage-dtc-history':          (200,    '200+ DTC records'),
    f'cms-{STAGE}-storage-recalls':              (50,     '50+ NHTSA recall matches'),
    f'cms-{STAGE}-storage-vehicle-costs':        (100,    '100+ monthly TCO rollups'),
    f'cms-{STAGE}-storage-charging-sessions':    (1,      '1+ charging sessions (BEVs in fleet)'),
    f'cms-{STAGE}-storage-location-snapshots':   (100,    '100+ location snapshots'),
    f'cms-{STAGE}-storage-fleet-enrollment':     (30,     '30+ vehicle enrollments'),
    f'cms-{STAGE}-signal-catalog':               (50,     '50+ CAN signals'),
    f'cms-{STAGE}-event-catalog':                (10,     '10+ event definitions'),
    f'cms-{STAGE}-decoder-manifest':             (100,    '100+ decoder manifest rows'),
    f'cms-{STAGE}-campaigns':                    (1,      '1+ FWE campaigns'),
    f'cms-{STAGE}-vfo-action-queue':             (15,     '15+ cross-domain action recommendations'),
    f'cms-{STAGE}-decision-journal':             (50,     '50+ autonomous decisions'),
}


# S3 bucket -> [(key-prefix, min-object-count, description)]
# Bucket name suffixed with -{region}-{account} per spec
# `2026-06-04-cms-vfo-kb-bucket-region-suffix`. Account is filled in at
# check_s3() time from the boto3 session's STS caller-identity (because
# this script may be invoked without AWS_ACCOUNT_ID set).
ACCOUNT = os.environ.get('AWS_ACCOUNT_ID', '{ACCOUNT}')

S3_EXPECTATIONS = {
    f'cms-{STAGE}-vfo-knowledge-base-{REGION}-{ACCOUNT}': [
        ('service-invoices/',  50,  'service invoice PDFs'),
        ('work-orders/',       10,  'work order PDFs'),
        ('warranty-claims/',   10,  'warranty claim PDFs'),
        ('parts-listings/',    5,   'parts listing PDFs'),
        ('fleet-context/',     3,   'fleet context markdown docs'),
    ],
    # Flink jar bucket resolved dynamically below
}


def count_rows(ddb_client, table_name):
    """Returns total row count by paginating through Scan with Select=COUNT."""
    total = 0
    resp = ddb_client.scan(TableName=table_name, Select='COUNT')
    total += resp.get('Count', 0)
    while 'LastEvaluatedKey' in resp:
        resp = ddb_client.scan(TableName=table_name, Select='COUNT',
                               ExclusiveStartKey=resp['LastEvaluatedKey'])
        total += resp.get('Count', 0)
    return total


def check_ddb(session):
    """Scan each expected table and compare against its minimum."""
    print(f'\n{BOLD}{BLUE}── DynamoDB tables ──{RESET}')
    ddb = session.client('dynamodb')
    failures = []
    for table_name, (min_rows, desc) in TABLE_EXPECTATIONS.items():
        try:
            actual = count_rows(ddb, table_name)
            if actual >= min_rows:
                print(f'  {GREEN}✅{RESET} {table_name}: {actual} rows ({desc})')
            else:
                print(f'  {RED}❌{RESET} {table_name}: {actual} rows (expected {desc})')
                failures.append(table_name)
        except ddb.exceptions.ResourceNotFoundException:
            print(f'  {RED}❌{RESET} {table_name}: NOT FOUND')
            failures.append(table_name)
        except Exception as e:
            print(f'  {YELLOW}⚠️ {RESET} {table_name}: {e}')
            failures.append(table_name)
    return failures


def check_s3(session):
    """Confirm expected S3 key prefixes have enough objects."""
    print(f'\n{BOLD}{BLUE}── S3 knowledge base ──{RESET}')
    s3 = session.client('s3')
    failures = []

    # Resolve {ACCOUNT} placeholder if AWS_ACCOUNT_ID env var was unset.
    # Spec: 2026-06-04-cms-vfo-kb-bucket-region-suffix.
    if any('{ACCOUNT}' in b for b in S3_EXPECTATIONS):
        sts = session.client('sts')
        resolved_account = sts.get_caller_identity()['Account']
        resolved = {b.replace('{ACCOUNT}', resolved_account): v for b, v in S3_EXPECTATIONS.items()}
        S3_EXPECTATIONS.clear()
        S3_EXPECTATIONS.update(resolved)

    # Add Flink jar bucket expectation (dynamic name)
    cfn = session.client('cloudformation')
    try:
        resp = cfn.describe_stacks(StackName=f'cms-{STAGE}-flink')
        for out in resp['Stacks'][0].get('Outputs', []):
            if out['OutputKey'] == 'FlinkJarBucketOutput':
                S3_EXPECTATIONS[out['OutputValue']] = [
                    ('fwe-config/DecoderManifest.bin', 1, 'DecoderManifest.bin'),
                ]
                break
    except Exception:
        pass

    for bucket, prefixes in S3_EXPECTATIONS.items():
        for prefix, min_count, desc in prefixes:
            try:
                paginator = s3.get_paginator('list_objects_v2')
                count = 0
                for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                    count += len(page.get('Contents', []))
                if count >= min_count:
                    print(f'  {GREEN}✅{RESET} s3://{bucket}/{prefix}: {count} objects ({desc})')
                else:
                    print(f'  {RED}❌{RESET} s3://{bucket}/{prefix}: {count} objects (expected {min_count}+ {desc})')
                    failures.append(f'{bucket}/{prefix}')
            except Exception as e:
                print(f'  {RED}❌{RESET} s3://{bucket}/{prefix}: {e}')
                failures.append(f'{bucket}/{prefix}')
    return failures


def check_api_health(session):
    """Smoke-test critical Fleet API endpoints by invoking the Lambda directly."""
    print(f'\n{BOLD}{BLUE}── Fleet API endpoints ──{RESET}')
    lam = session.client('lambda')
    import json as _json

    # Find the Fleet API Lambda (name has a random suffix)
    prefix = f'cms-{STAGE}-ui-FleetAPIFunction'
    fn_name = None
    try:
        paginator = lam.get_paginator('list_functions')
        for page in paginator.paginate():
            for fn in page.get('Functions', []):
                if fn['FunctionName'].startswith(prefix):
                    fn_name = fn['FunctionName']
                    break
            if fn_name:
                break
    except Exception as e:
        print(f'  {RED}❌{RESET} list_functions failed: {e}')
        return ['list_functions']

    if not fn_name:
        print(f'  {RED}❌{RESET} Fleet API Lambda not found ({prefix}*)')
        return ['lambda']

    endpoints = [
        '/api/v1/fleet-health',
        '/api/v1/dashboard/fleet-comparison',
        '/api/v1/tco/summary',
        '/api/v1/charging/summary',
        '/api/v1/rebalancing/locations',
        '/api/v1/warranty-claims',
        '/api/v1/trips/count',
        '/api/v1/vehicles',
        '/api/v1/fleets',
    ]
    failures = []
    for path in endpoints:
        payload = _json.dumps({'path': path, 'httpMethod': 'GET', 'queryStringParameters': None})
        try:
            resp = lam.invoke(FunctionName=fn_name, Payload=payload.encode())
            body_bytes = resp['Payload'].read()
            body = _json.loads(body_bytes)
            status = body.get('statusCode', 0)
            if status == 200:
                print(f'  {GREEN}✅{RESET} {path}: 200')
            else:
                err = _json.loads(body.get('body', '{}')).get('error', '(no body)')
                print(f'  {RED}❌{RESET} {path}: {status} - {err[:80]}')
                failures.append(path)
        except Exception as e:
            print(f'  {RED}❌{RESET} {path}: {e}')
            failures.append(path)
    return failures


def main():
    print(f'{BOLD}CMS Demo Data Verification{RESET}')
    print(f'  {PROFILE=!s} {REGION=!s} {STAGE=!s}')

    session = boto3.Session(profile_name=PROFILE, region_name=REGION)

    ddb_failures = check_ddb(session)
    s3_failures = check_s3(session)
    api_failures = check_api_health(session)

    print(f'\n{BOLD}Summary{RESET}')
    total_fail = len(ddb_failures) + len(s3_failures) + len(api_failures)
    if total_fail == 0:
        print(f'  {GREEN}{BOLD}✅ All verification checks passed. Demo is ready.{RESET}')
        sys.exit(0)
    print(f'  {RED}{BOLD}❌ {total_fail} checks failed:{RESET}')
    for f in ddb_failures:
        print(f'    • DDB: {f}')
    for f in s3_failures:
        print(f'    • S3: {f}')
    for f in api_failures:
        print(f'    • API: {f}')
    print(f'\nCommon fixes:')
    print(f'  • Missing DDB rows:  re-run the relevant seed, e.g.:')
    print(f'      make inject-everything AWS_PROFILE={PROFILE} DEPLOYMENT_STAGE={STAGE}')
    print(f'  • Missing PDFs:      python3 services/simulation/generate_kb_data.py --region {REGION}')
    print(f'  • API failures:      check CloudWatch logs for the Fleet API Lambda')
    sys.exit(1)


if __name__ == '__main__':
    main()
