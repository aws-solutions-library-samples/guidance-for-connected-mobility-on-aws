#!/usr/bin/env python3
"""Preflight check for make bootstrap-demo.

Validates that everything bootstrap-demo depends on is in place BEFORE
kicking off a 45-minute injector run. Fails loudly with actionable
messages so operators don't discover broken prerequisites an hour in.

Exit codes:
    0 - all checks passed, safe to proceed
    1 - one or more hard prerequisites failed
    2 - soft warnings only (script still exits 0 in this case)

Usage:
    AWS_PROFILE=default AWS_REGION=us-east-1 DEPLOYMENT_STAGE=prod \\
        python3 deployment/scripts/preflight_demo.py
"""
import os
import sys
import importlib
import boto3
import botocore.exceptions


STAGE = os.environ.get('DEPLOYMENT_STAGE', 'prod')
REGION = os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION') or 'us-east-1'
PROFILE = os.environ.get('AWS_PROFILE', 'default')

# Colors
GREEN = '\033[0;32m'
RED = '\033[0;31m'
YELLOW = '\033[0;33m'
BLUE = '\033[0;34m'
BOLD = '\033[1m'
RESET = '\033[0m'


def ok(msg):
    print(f'  {GREEN}✅{RESET} {msg}')


def fail(msg):
    print(f'  {RED}❌{RESET} {msg}')


def warn(msg):
    print(f'  {YELLOW}⚠️ {RESET} {msg}')


def header(text):
    print(f'\n{BOLD}{BLUE}── {text} ──{RESET}')


def check_python_deps():
    """Required Python packages for the full bootstrap flow."""
    header('Python dependencies')
    required = {
        'boto3': 'boto3>=1.28',
        'botocore': 'botocore',
        'cantools': 'cantools (for DBC parsing in seed_decoder_and_campaign.py)',
        'zstandard': 'zstandard (for FWE decoder compression)',
        'reportlab': 'reportlab (for PDF generation in generate_kb_data.py)',
    }
    failures = []
    for mod, desc in required.items():
        try:
            importlib.import_module(mod)
            ok(f'{desc}')
        except ImportError:
            fail(f'{desc} - missing. Install with: pip3 install {mod}')
            failures.append(mod)
    return len(failures) == 0


def check_aws_creds(session):
    """Validate AWS credentials and account identity."""
    header('AWS credentials')
    try:
        sts = session.client('sts')
        ident = sts.get_caller_identity()
        ok(f"Account: {ident['Account']}")
        ok(f"Caller:  {ident['Arn']}")
        ok(f"Region:  {REGION}")
        ok(f"Stage:   {STAGE}")
        ok(f"Profile: {PROFILE}")
        return ident['Account']
    except botocore.exceptions.NoCredentialsError:
        fail('No AWS credentials found. Run `aws configure` or set AWS_PROFILE.')
        return None
    except Exception as e:
        fail(f'sts:GetCallerIdentity failed: {e}')
        return None


def check_stacks(session):
    """Required CloudFormation stacks for demo seeding."""
    header('CloudFormation stacks')
    cfn = session.client('cloudformation')
    required = {
        f'cms-{STAGE}-data-processing': 'signal catalog, transform manifests',
        f'cms-{STAGE}-storage': 'all demo DDB tables',
        f'cms-{STAGE}-iot': 'IoT Core + vehicle certificates',
        f'cms-{STAGE}-ui': 'Cognito + Fleet API + Location Services',
        f'cms-{STAGE}-msk': 'MSK + VPC + Redis',
        f'cms-{STAGE}-simulation': 'simulation API (seed_decoder_and_campaign needs its bucket)',
        f'cms-{STAGE}-fleetwise': 'FWE rules + CampaignSyncProcessor',
    }
    missing = []
    for stack, desc in required.items():
        try:
            resp = cfn.describe_stacks(StackName=stack)
            status = resp['Stacks'][0]['StackStatus']
            if status.endswith('_COMPLETE'):
                ok(f'{stack} ({status})')
            else:
                warn(f'{stack} in state {status} - may cause issues')
        except botocore.exceptions.ClientError as e:
            if 'does not exist' in str(e):
                fail(f'{stack} not deployed - needed for {desc}')
                missing.append(stack)
            else:
                fail(f'{stack}: {e}')
                missing.append(stack)
    return len(missing) == 0


def check_tables(session):
    """DDB tables the injector and seeds write to."""
    header('DynamoDB tables')
    ddb = session.client('dynamodb')
    required = [
        f'cms-{STAGE}-storage-fleets',
        f'cms-{STAGE}-storage-vehicles',
        f'cms-{STAGE}-storage-drivers',
        f'cms-{STAGE}-storage-trips',
        f'cms-{STAGE}-storage-safety-events',
        f'cms-{STAGE}-storage-maintenance-alerts',
        f'cms-{STAGE}-storage-vehicle-costs',
        f'cms-{STAGE}-storage-charging-sessions',
        f'cms-{STAGE}-storage-location-snapshots',
        f'cms-{STAGE}-storage-service-history',
        f'cms-{STAGE}-storage-warranty-claims',
        f'cms-{STAGE}-storage-dtc-history',
        f'cms-{STAGE}-storage-recalls',
        f'cms-{STAGE}-storage-fleet-enrollment',
        f'cms-{STAGE}-storage-vehicle-certificates',
        f'cms-{STAGE}-event-catalog',
        f'cms-{STAGE}-signal-catalog',
        f'cms-{STAGE}-campaigns',
        f'cms-{STAGE}-decoder-manifest',
        f'cms-{STAGE}-vfo-action-queue',
        f'cms-{STAGE}-decision-journal',
    ]
    missing = []
    for t in required:
        try:
            resp = ddb.describe_table(TableName=t)
            status = resp['Table']['TableStatus']
            if status == 'ACTIVE':
                ok(t)
            else:
                warn(f'{t} is {status}')
        except ddb.exceptions.ResourceNotFoundException:
            fail(f'{t} missing - redeploy cms-{STAGE}-storage')
            missing.append(t)
    return len(missing) == 0


def check_location_services(session):
    """Location Services resources used by the injector's real-route path."""
    header('Amazon Location Services')
    loc = session.client('location')
    prefix = f'cms-{STAGE}-ui'
    resources = [
        ('map', f'{prefix}-vehicle-map', 'describe_map', 'MapName'),
        ('place-index', f'{prefix}-place-index', 'describe_place_index', 'IndexName'),
        ('route-calculator', f'{prefix}-route-calculator', 'describe_route_calculator', 'CalculatorName'),
    ]
    missing = []
    for kind, name, method, arg in resources:
        try:
            getattr(loc, method)(**{arg: name})
            ok(f'{kind}: {name}')
        except loc.exceptions.ResourceNotFoundException:
            fail(f'{kind} {name} missing - deployed by cms-{STAGE}-ui')
            missing.append(name)
        except Exception as e:
            warn(f'{kind} {name}: {e}')
    return len(missing) == 0


def check_s3_buckets(session, account):
    """S3 buckets that seeds and the injector write to."""
    header('S3 buckets')
    s3 = session.client('s3')
    required = [
        (f'cms-{STAGE}-vfo-knowledge-base', 'VFO knowledge base - PDFs and markdown docs'),
        (f'cms-{STAGE}-transform-manifests-{account}', 'transform manifests + campaign collection schemes'),
    ]
    # Flink jar bucket name is dynamic - look it up from CFN
    cfn = session.client('cloudformation')
    try:
        resp = cfn.describe_stacks(StackName=f'cms-{STAGE}-flink')
        for out in resp['Stacks'][0].get('Outputs', []):
            if out['OutputKey'] == 'FlinkJarBucketOutput':
                required.append((out['OutputValue'], 'Flink JARs + fwe-config/DecoderManifest.bin'))
                break
    except Exception:
        warn('Could not resolve Flink jar bucket from cms-{STAGE}-flink stack outputs')

    missing = []
    for bucket, desc in required:
        try:
            s3.head_bucket(Bucket=bucket)
            ok(f'{bucket} ({desc})')
        except botocore.exceptions.ClientError as e:
            code = e.response.get('Error', {}).get('Code', '')
            if code in ('404', 'NoSuchBucket'):
                fail(f'{bucket} missing')
                missing.append(bucket)
            elif code == '403':
                warn(f'{bucket} exists but no permission to head_bucket (may still work for writes)')
            else:
                warn(f'{bucket}: {e}')
    return len(missing) == 0


def check_fleet_api(session):
    """Fleet API Lambda should exist before seeding."""
    header('Fleet API Lambda')
    lam = session.client('lambda')
    # Lambda name is auto-generated - list functions and find by prefix
    prefix = f'cms-{STAGE}-ui-FleetAPIFunction'
    try:
        paginator = lam.get_paginator('list_functions')
        found = False
        for page in paginator.paginate():
            for fn in page.get('Functions', []):
                if fn['FunctionName'].startswith(prefix):
                    ok(f"{fn['FunctionName']} ({fn.get('Runtime', 'unknown')})")
                    found = True
                    break
            if found:
                break
        if not found:
            fail(f'No Lambda matching {prefix}* - redeploy cms-{STAGE}-ui')
            return False
    except Exception as e:
        warn(f'list_functions failed: {e}')
    return True


def main():
    print(f"{BOLD}CMS Demo Bootstrap Preflight{RESET}")
    print(f"  {PROFILE=!s} {REGION=!s} {STAGE=!s}")

    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    results = {}

    results['python_deps'] = check_python_deps()
    account = check_aws_creds(session)
    results['creds'] = account is not None
    if not results['creds']:
        print(f"\n{RED}{BOLD}HARD FAIL - cannot continue without credentials{RESET}")
        sys.exit(1)

    results['stacks'] = check_stacks(session)
    results['tables'] = check_tables(session)
    results['location'] = check_location_services(session)
    results['s3'] = check_s3_buckets(session, account)
    results['lambda'] = check_fleet_api(session)

    # Summary
    print(f"\n{BOLD}Summary{RESET}")
    all_passed = True
    for check, passed in results.items():
        status = f'{GREEN}PASS{RESET}' if passed else f'{RED}FAIL{RESET}'
        print(f'  [{status}] {check}')
        if not passed:
            all_passed = False

    if all_passed:
        print(f"\n{GREEN}{BOLD}✅ All preflight checks passed. Safe to run bootstrap-demo.{RESET}")
        sys.exit(0)
    else:
        print(f"\n{RED}{BOLD}❌ Preflight checks failed. Fix the items above before running bootstrap-demo.{RESET}")
        print(f"\nCommon fixes:")
        print(f"  • Missing stacks:     make deploy-all AWS_PROFILE={PROFILE} DEPLOYMENT_STAGE={STAGE}")
        print(f"  • Missing tables:     redeploy cms-{STAGE}-storage")
        print(f"  • Missing buckets:    redeploy the owning stack")
        print(f"  • Missing Python pkg: pip3 install boto3 cantools zstandard reportlab")
        sys.exit(1)


if __name__ == '__main__':
    main()
