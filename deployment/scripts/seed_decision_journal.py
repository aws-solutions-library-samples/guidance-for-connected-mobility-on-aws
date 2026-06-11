#!/usr/bin/env python3
"""Seed cms-<stage>-decision-journal with realistic VFO autonomous decisions.

Generates ~150 decision records that look like the VFO supervisor has
been running for a year - scheduling services, reassigning vehicles,
filing warranty claims, deferring non-urgent maintenance.

Usage:
    DEPLOYMENT_STAGE=prod AWS_REGION=us-east-1 AWS_PROFILE=default \\
        python3 deployment/scripts/seed_decision_journal.py
"""
import os
import random
import uuid
import boto3
from datetime import datetime, timezone, timedelta
from decimal import Decimal

STAGE = os.environ.get('DEPLOYMENT_STAGE', 'prod')
REGION = os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION') or 'us-east-1'
PROFILE = os.environ.get('AWS_PROFILE', 'default')

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
ddb = session.resource('dynamodb')


def _scan_all(table_name, limit=None):
    t = ddb.Table(table_name)
    items = []
    kwargs = {'Limit': limit} if limit else {}
    resp = t.scan(**kwargs)
    items.extend(resp.get('Items', []))
    while 'LastEvaluatedKey' in resp and (limit is None or len(items) < limit):
        resp = t.scan(ExclusiveStartKey=resp['LastEvaluatedKey'])
        items.extend(resp.get('Items', []))
    return items[:limit] if limit else items


def _recent_ts(max_days_ago=365):
    """Epoch-ms timestamp weighted toward recent dates."""
    days_ago = int(random.expovariate(1 / 45))  # mean ~45 days
    days_ago = min(days_ago, max_days_ago)
    ts = datetime.now(timezone.utc) - timedelta(
        days=days_ago, hours=random.randint(0, 23), minutes=random.randint(0, 59)
    )
    return int(ts.timestamp() * 1000), ts.isoformat()


def build_schedule_service(vehicles, dtcs, count=45):
    """SCHEDULE_SERVICE decisions - triggered by DTC patterns."""
    decisions = []
    # Prefer HIGH/CRITICAL DTCs as triggers
    triggers = [d for d in dtcs if d.get('severity') in ('HIGH', 'CRITICAL')]
    if not triggers:
        triggers = dtcs
    random.shuffle(triggers)

    for d in triggers[:count]:
        vid = d.get('vehicleId', 'VEH-0001')
        code = d.get('code', 'P0000')
        desc = d.get('description', '')
        veh = next((v for v in vehicles if v.get('vehicleId') == vid), {})
        make = veh.get('make', '')
        model = veh.get('model', '')
        ts_ms, ts_iso = _recent_ts()
        cost = random.randint(150, 800)

        decisions.append({
            'decisionId': str(uuid.uuid4()),
            'vehicleId': vid,
            'timestamp': Decimal(str(ts_ms)),
            'decisionAt': ts_iso,
            'decision': 'SCHEDULE_SERVICE',
            'category': 'Maintenance',
            'reasoning': (
                f"DTC {code} ({desc[:60]}) detected on {make} {model} {vid}. "
                f"Historical pattern suggests 48h escalation to breakdown if untreated. "
                f"Scheduled preventive service at nearest certified shop."
            ),
            'estimated_cost': Decimal(str(cost)),
            'trigger_event': f'DTC:{code}',
            'outcome': random.choice(['COMPLETED', 'COMPLETED', 'COMPLETED', 'PENDING', 'DEFERRED']),
        })
    return decisions


def build_reassign_vehicle(vehicles, fleets, count=35):
    """REASSIGN_VEHICLE decisions - utilization rebalancing."""
    decisions = []
    if len(fleets) < 2:
        return decisions

    for _ in range(count):
        veh = random.choice(vehicles)
        vid = veh['vehicleId']
        src_fleet = veh.get('fleetId', 'FLEET-001')
        candidate_fleets = [f['fleetId'] for f in fleets if f['fleetId'] != src_fleet]
        if not candidate_fleets:
            continue
        dst_fleet = random.choice(candidate_fleets)
        ts_ms, ts_iso = _recent_ts()
        moves = random.randint(1, 3)

        decisions.append({
            'decisionId': str(uuid.uuid4()),
            'vehicleId': vid,
            'timestamp': Decimal(str(ts_ms)),
            'decisionAt': ts_iso,
            'decision': 'REASSIGN_VEHICLE',
            'category': 'Utilization',
            'reasoning': (
                f"Utilization on {src_fleet} trending above 90% over past 7 days; "
                f"{dst_fleet} shows 15% capacity headroom. "
                f"Reassigned {vid} to balance load. "
                f"Historical driver-route compatibility score: "
                f"{random.randint(78, 98)}%."
            ),
            'estimated_cost': Decimal('0'),
            'trigger_event': 'UTILIZATION_IMBALANCE',
            'sourceFleet': src_fleet,
            'destinationFleet': dst_fleet,
            'outcome': random.choice(['COMPLETED', 'COMPLETED', 'PENDING']),
        })
    return decisions


def build_file_warranty(warranty, vehicles, count=25):
    """FILE_WARRANTY_CLAIM decisions - triggered by component failure + in-warranty check."""
    decisions = []
    # Prefer paid/approved claims (realistic outcome)
    candidates = [c for c in warranty if c.get('status') in ('PAID', 'UNDER_REVIEW', 'OPEN')]
    random.shuffle(candidates)

    for claim in candidates[:count]:
        vid = claim.get('vehicleId', 'VEH-0001')
        component = claim.get('component', 'component')
        dtc = claim.get('failureCode', '')
        amount = int(float(claim.get('claimAmount', 500)))
        ts_ms, ts_iso = _recent_ts()

        decisions.append({
            'decisionId': str(uuid.uuid4()),
            'vehicleId': vid,
            'timestamp': Decimal(str(ts_ms)),
            'decisionAt': ts_iso,
            'decision': 'FILE_WARRANTY_CLAIM',
            'category': 'Warranty',
            'reasoning': (
                f"{component} failure on {vid} (code {dtc}). "
                f"Vehicle within warranty limit ({claim.get('warrantyLimit', 'N/A')}). "
                f"Confidence score: {claim.get('confidence', 85)}%. "
                f"Filed claim #{claim.get('claimId', 'N/A')} with OEM."
            ),
            'estimated_cost': Decimal(str(amount)),
            'trigger_event': f'COMPONENT_FAIL:{component}',
            'claimId': claim.get('claimId', ''),
            'outcome': 'COMPLETED' if claim.get('status') == 'PAID' else 'PENDING',
        })
    return decisions


def build_defer_maintenance(maintenance, vehicles, count=20):
    """DEFER_MAINTENANCE decisions - low-severity alerts rescheduled."""
    decisions = []
    candidates = [m for m in maintenance if m.get('severity') in ('LOW', 'MEDIUM')]
    random.shuffle(candidates)

    for m in candidates[:count]:
        vid = m.get('vehicleId', 'VEH-0001')
        alert_type = m.get('alertType', 'MAINTENANCE_DUE')
        ts_ms, ts_iso = _recent_ts()
        days_deferred = random.randint(7, 30)

        decisions.append({
            'decisionId': str(uuid.uuid4()),
            'vehicleId': vid,
            'timestamp': Decimal(str(ts_ms)),
            'decisionAt': ts_iso,
            'decision': 'DEFER_MAINTENANCE',
            'category': 'Maintenance',
            'reasoning': (
                f"Low-severity alert {alert_type.replace('_', ' ').title()} on {vid}. "
                f"Current utilization high; deferring by {days_deferred} days "
                f"to align with next scheduled service window. "
                f"Risk assessment: acceptable (no safety-critical impact)."
            ),
            'estimated_cost': Decimal('0'),
            'trigger_event': f'ALERT:{alert_type}',
            'deferralDays': Decimal(str(days_deferred)),
            'outcome': random.choice(['COMPLETED', 'COMPLETED', 'PENDING']),
        })
    return decisions


def build_issue_recall_notice(recalls, count=25):
    """ISSUE_RECALL_NOTICE decisions - automated recall correlation."""
    decisions = []
    seen_keys = set()
    random.shuffle(recalls)

    for r in recalls:
        vid = r.get('vehicleId', '')
        campaign = r.get('campaignNumber', '')
        key = (vid, campaign)
        if key in seen_keys or not vid or not campaign:
            continue
        seen_keys.add(key)
        if len(decisions) >= count:
            break

        severity = r.get('severity', 'Medium')
        component = r.get('component', 'component')[:50]
        ts_ms, ts_iso = _recent_ts()

        decisions.append({
            'decisionId': str(uuid.uuid4()),
            'vehicleId': vid,
            'timestamp': Decimal(str(ts_ms)),
            'decisionAt': ts_iso,
            'decision': 'ISSUE_RECALL_NOTICE',
            'category': 'Recall',
            'reasoning': (
                f"NHTSA recall {campaign} matched to {vid} "
                f"(component: {component}, severity: {severity}). "
                f"Sent notification to fleet manager + operator. "
                f"Vehicle flagged for service at earliest operational window."
            ),
            'estimated_cost': Decimal('0'),
            'trigger_event': f'RECALL:{campaign}',
            'recallCampaign': campaign,
            'outcome': random.choice(['COMPLETED', 'COMPLETED', 'COMPLETED', 'PENDING']),
        })
    return decisions


def main():
    print(f"Seeding cms-{STAGE}-decision-journal (region={REGION})...")

    vehicles = _scan_all(f'cms-{STAGE}-storage-vehicles')
    print(f"  loaded {len(vehicles)} vehicles")
    fleets = _scan_all(f'cms-{STAGE}-storage-fleets')
    print(f"  loaded {len(fleets)} fleets")
    dtcs = _scan_all(f'cms-{STAGE}-storage-dtc-history', limit=300)
    print(f"  loaded {len(dtcs)} DTCs (sample)")
    warranty = _scan_all(f'cms-{STAGE}-storage-warranty-claims')
    print(f"  loaded {len(warranty)} warranty claims")
    maintenance = _scan_all(f'cms-{STAGE}-storage-maintenance-alerts')
    print(f"  loaded {len(maintenance)} maintenance alerts")
    recalls = _scan_all(f'cms-{STAGE}-storage-recalls')
    print(f"  loaded {len(recalls)} recalls")

    all_decisions = []
    all_decisions += build_schedule_service(vehicles, dtcs)
    all_decisions += build_reassign_vehicle(vehicles, fleets)
    all_decisions += build_file_warranty(warranty, vehicles)
    all_decisions += build_defer_maintenance(maintenance, vehicles)
    all_decisions += build_issue_recall_notice(recalls)
    print(f"  generated {len(all_decisions)} decisions")

    table = ddb.Table(f'cms-{STAGE}-decision-journal')
    with table.batch_writer() as batch:
        for d in all_decisions:
            batch.put_item(Item=d)
    print(f"  wrote {len(all_decisions)} decisions to cms-{STAGE}-decision-journal")

    from collections import Counter
    decision_counts = Counter(d['decision'] for d in all_decisions)
    outcome_counts = Counter(d['outcome'] for d in all_decisions)
    print(f"\nDecision types: {dict(decision_counts)}")
    print(f"Outcomes:       {dict(outcome_counts)}")


if __name__ == '__main__':
    main()
