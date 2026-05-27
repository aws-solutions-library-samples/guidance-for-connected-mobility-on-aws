#!/usr/bin/env python3
"""Seed cms-<stage>-vfo-action-queue with realistic cross-domain actions.

Creates ~40 actions spanning the last year: recall grounding plans,
warranty filings, rebalancing proposals, cost anomalies, maintenance
escalations. Status mix: ~35% PENDING, ~50% APPROVED, ~15% REJECTED
to look like an actively-operated fleet.

Usage:
    DEPLOYMENT_STAGE=prod AWS_REGION=us-east-1 AWS_PROFILE=default \\
        python3 deployment/scripts/seed_vfo_actions.py
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


# Canonical severity vocabulary (per docs/SEVERITY_VOCABULARY.md).
# Normalizer accepts canonical words (any case), SAE DTC hints (P0-P3),
# numeric 1-4, and NHTSA title-case (Critical/High/Medium/Low) — the
# four forms currently present in the ecosystem's DDB tables.
_SAE_TO_CANONICAL = {'P0': 'CRITICAL', 'P1': 'HIGH', 'P2': 'MEDIUM', 'P3': 'LOW'}
_NUM_TO_CANONICAL = {'4': 'CRITICAL', '3': 'HIGH', '2': 'MEDIUM', '1': 'LOW'}
_CANONICAL = {'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'}


def _normalize_severity(raw):
    """Map any severity-ish input to canonical CRITICAL/HIGH/MEDIUM/LOW.

    Mirrors the helper in modules/cms_ui/source/handlers/main_api/index.py —
    keep both in sync. See docs/SEVERITY_VOCABULARY.md for the mapping.
    """
    if raw is None:
        return 'MEDIUM'
    s = str(raw).strip().upper()
    if s in _CANONICAL:
        return s
    if s in _SAE_TO_CANONICAL:
        return _SAE_TO_CANONICAL[s]
    if s in _NUM_TO_CANONICAL:
        return _NUM_TO_CANONICAL[s]
    return 'MEDIUM'


def _scan_all(table_name, limit=None):
    t = ddb.Table(table_name)
    items = []
    resp = t.scan(Limit=limit) if limit else t.scan()
    items.extend(resp.get('Items', []))
    while 'LastEvaluatedKey' in resp and (limit is None or len(items) < limit):
        resp = t.scan(ExclusiveStartKey=resp['LastEvaluatedKey'])
        items.extend(resp.get('Items', []))
    return items[:limit] if limit else items


def _pick_status():
    """Weighted status picker. ~35% PENDING, 50% APPROVED, 15% REJECTED."""
    return random.choices(
        ['PENDING', 'APPROVED', 'REJECTED'],
        weights=[35, 50, 15],
    )[0]


def _created_at(days_ago_max=365):
    """ISO timestamp between now and days_ago_max ago. Recent-biased."""
    # Exponential distribution favours recent: most actions in last 60 days.
    days_ago = int(random.expovariate(1 / 30))
    days_ago = min(days_ago, days_ago_max)
    ts = datetime.now(timezone.utc) - timedelta(
        days=days_ago, hours=random.randint(0, 23), minutes=random.randint(0, 59)
    )
    return ts.isoformat()


def build_recall_actions(vehicles, recalls, count=10):
    """Recall grounding plan actions. Each references real campaign + vehicles."""
    actions = []
    # Group recalls by campaignNumber so we can reference "N vehicles affected"
    by_campaign = {}
    for r in recalls:
        cn = r.get('campaignNumber')
        if cn:
            by_campaign.setdefault(cn, []).append(r)

    top_campaigns = sorted(by_campaign.items(), key=lambda kv: -len(kv[1]))[:count]
    for campaign, recs in top_campaigns:
        first = recs[0]
        component = first.get('component', 'unknown')[:60]
        severity = first.get('severity', 'Medium')
        affected_count = len(recs)
        status = _pick_status()
        severity_canon = _normalize_severity(severity)
        priority = 'HIGH' if severity_canon in ('CRITICAL', 'HIGH') else 'MEDIUM'

        # Back-estimate cost: $150/vehicle service + $50/vehicle transport
        est_cost = affected_count * random.randint(150, 350)

        resp = (
            f"Recall {campaign} affects {affected_count} vehicle"
            f"{'s' if affected_count != 1 else ''} ({component}). "
            f"Severity: {severity}. "
            f"Recommend: ground affected vehicles, schedule service at nearest OEM-certified shop, "
            f"file warranty claims for covered components. "
            f"Estimated service cost: ${est_cost:,}. "
            f"Affected VINs and schedule attached."
        )
        actions.append({
            'actionId': str(uuid.uuid4()),
            'createdAt': _created_at(),
            'domain': 'Recall',
            'priority': priority,
            'severity': severity_canon,  # canonical form, see docs/SEVERITY_VOCABULARY.md
            'status': status,
            'agentResponse': resp,
            'campaignNumber': campaign,
            'affectedVehicleCount': Decimal(str(affected_count)),
            'estimatedCost': Decimal(str(est_cost)),
            'resolvedAt': _created_at() if status != 'PENDING' else '',
            'resolvedBy': 'FleetManager@example.com' if status != 'PENDING' else '',
        })
    return actions


def build_warranty_actions(warranty_claims, count=10):
    """Warranty claim filing recommendations."""
    actions = []
    # Prefer OPEN/UNDER_REVIEW claims as pending action candidates
    candidates = [c for c in warranty_claims if c.get('status') in ('OPEN', 'UNDER_REVIEW')]
    random.shuffle(candidates)
    for claim in candidates[:count]:
        amount = int(float(claim.get('claimAmount', 0)))
        component = claim.get('component', 'unknown')
        vid = claim.get('vehicleId', 'unknown')
        dtc = claim.get('failureCode', '')

        resp = (
            f"Vehicle {vid} shows {dtc} consistent with {component} failure. "
            f"Component is under warranty (limit: {claim.get('warrantyLimit', 'N/A')}). "
            f"Recommend: file claim #{claim.get('claimId', 'N/A')} with OEM. "
            f"Expected recovery: ${amount:,}. "
            f"Confidence score: {claim.get('confidence', 'N/A')}%."
        )
        actions.append({
            'actionId': str(uuid.uuid4()),
            'createdAt': _created_at(),
            'domain': 'Warranty',
            'priority': 'MEDIUM' if amount < 1500 else 'HIGH',
            'severity': 'MEDIUM' if amount < 1500 else 'HIGH',  # canonical, see docs/SEVERITY_VOCABULARY.md
            'status': _pick_status(),
            'agentResponse': resp,
            'vehicleId': vid,
            'claimId': claim.get('claimId', ''),
            'estimatedRecovery': Decimal(str(amount)),
        })
    return actions


def build_rebalancing_actions(locations, count=5):
    """Fleet rebalancing proposals."""
    actions = []
    # Pick pairs of surplus/deficit locations
    surplus = [l for l in locations if l.get('status') == 'surplus']
    deficit = [l for l in locations if l.get('status') == 'deficit']
    pairs = min(count, min(len(surplus), len(deficit)))
    if pairs == 0:
        # Even if all are surplus (no deficit), create synthetic proposals
        # using the two highest-utilization locations as "need more" targets
        by_util = sorted(locations, key=lambda l: -float(l.get('utilizationPercent', 0)))
        if len(by_util) >= 2:
            surplus = by_util[-count:]
            deficit = by_util[:count]
            pairs = min(count, len(surplus), len(deficit))

    for i in range(pairs):
        src = surplus[i % len(surplus)]
        dst = deficit[i % len(deficit)]
        move_count = random.randint(2, 5)
        est_cost = move_count * random.randint(200, 450)

        resp = (
            f"Utilization imbalance detected: {src.get('locationId', 'src')} at "
            f"{src.get('utilizationPercent', 0)}% ({src.get('activeVehicles', 0)}/"
            f"{src.get('totalVehicles', 0)} active), while "
            f"{dst.get('locationId', 'dst')} is at {dst.get('utilizationPercent', 0)}% "
            f"({dst.get('activeVehicles', 0)}/{dst.get('totalVehicles', 0)} active). "
            f"Recommend moving {move_count} vehicles from {src.get('locationId', 'src')} "
            f"to {dst.get('locationId', 'dst')}. "
            f"Transfer cost: ${est_cost:,}. "
            f"Projected utilization uplift at destination: "
            f"{round(move_count/max(1, int(dst.get('totalVehicles', 10))) * 100, 1)}%."
        )
        actions.append({
            'actionId': str(uuid.uuid4()),
            'createdAt': _created_at(),
            'domain': 'Rebalancing',
            'priority': 'MEDIUM',
            'severity': 'MEDIUM',  # canonical, see docs/SEVERITY_VOCABULARY.md
            'status': _pick_status(),
            'agentResponse': resp,
            'sourceLocation': src.get('locationId', ''),
            'destinationLocation': dst.get('locationId', ''),
            'vehiclesToMove': Decimal(str(move_count)),
            'estimatedCost': Decimal(str(est_cost)),
        })
    return actions


def build_cost_actions(vehicles, tco_rollups, count=8):
    """Cost anomaly investigations - vehicles with outlier cost/mile."""
    actions = []
    # Find recent rollups with cost/mile > 1.5x fleet avg
    if not tco_rollups:
        return actions
    cpm_values = [float(r.get('costPerMile', 0)) for r in tco_rollups if float(r.get('costPerMile', 0)) > 0]
    if not cpm_values:
        return actions
    avg_cpm = sum(cpm_values) / len(cpm_values)
    outliers = [
        r for r in tco_rollups
        if float(r.get('costPerMile', 0)) > avg_cpm * 1.5
        and float(r.get('distanceMiles', 0)) > 100
    ]
    random.shuffle(outliers)

    for roll in outliers[:count]:
        vid = roll.get('vehicleId', 'unknown')
        cpm = float(roll.get('costPerMile', 0))
        ym = roll.get('yearMonth', '')
        veh = next((v for v in vehicles if v.get('vehicleId') == vid), {})
        make = veh.get('make', '')
        model = veh.get('model', '')

        resp = (
            f"Vehicle {vid} ({make} {model}) cost-per-mile for {ym}: "
            f"${cpm:.2f} vs fleet average ${avg_cpm:.2f} ({round(cpm/avg_cpm * 100 - 100, 0):.0f}% above). "
            f"Contributing factors: elevated maintenance spend "
            f"(${float(roll.get('maintenanceCost', 0)):,.0f}) + low utilization "
            f"({int(float(roll.get('tripCount', 0)))} trips). "
            f"Recommend: review DTC history for deferred repairs, "
            f"evaluate assignment, consider retirement if trend continues."
        )
        actions.append({
            'actionId': str(uuid.uuid4()),
            'createdAt': _created_at(),
            'domain': 'Cost',
            'priority': 'MEDIUM',
            'severity': 'MEDIUM',  # canonical, see docs/SEVERITY_VOCABULARY.md
            'status': _pick_status(),
            'agentResponse': resp,
            'vehicleId': vid,
            'costPerMile': Decimal(str(round(cpm, 4))),
            'fleetAvgCostPerMile': Decimal(str(round(avg_cpm, 4))),
        })
    return actions


def build_maintenance_actions(maintenance, vehicles, count=7):
    """Maintenance escalations - overdue alerts."""
    actions = []
    # Filter to HIGH/CRITICAL with OPEN/scheduled status
    candidates = [
        m for m in maintenance
        if m.get('severity') in ('HIGH', 'CRITICAL')
    ]
    random.shuffle(candidates)

    for m in candidates[:count]:
        vid = m.get('vehicleId', 'unknown')
        alert_type = m.get('alertType', 'MAINTENANCE_DUE')
        severity = m.get('severity', 'MEDIUM')
        veh = next((v for v in vehicles if v.get('vehicleId') == vid), {})
        make = veh.get('make', '')
        model = veh.get('model', '')

        resp = (
            f"Vehicle {vid} ({make} {model}) has open {severity.lower()} maintenance alert: "
            f"{alert_type.replace('_', ' ').title()}. "
            f"Recommend scheduling service in next 7 days to prevent escalation. "
            f"Nearest certified shop: per fleet service agreement. "
            f"Estimated downtime: 4-8 hours."
        )
        actions.append({
            'actionId': str(uuid.uuid4()),
            'createdAt': _created_at(),
            'domain': 'Maintenance',
            'priority': 'HIGH' if severity == 'CRITICAL' else 'MEDIUM',
            'severity': _normalize_severity(severity),  # canonical, see docs/SEVERITY_VOCABULARY.md
            'status': _pick_status(),
            'agentResponse': resp,
            'vehicleId': vid,
            'alertType': alert_type,
        })
    return actions


def main():
    print(f"Seeding cms-{STAGE}-vfo-action-queue (region={REGION})...")

    vehicles = _scan_all(f'cms-{STAGE}-storage-vehicles')
    print(f"  loaded {len(vehicles)} vehicles")
    recalls = _scan_all(f'cms-{STAGE}-storage-recalls')
    print(f"  loaded {len(recalls)} recall records")
    warranty = _scan_all(f'cms-{STAGE}-storage-warranty-claims')
    print(f"  loaded {len(warranty)} warranty claims")
    locations = _scan_all(f'cms-{STAGE}-storage-location-snapshots', limit=100)
    print(f"  loaded {len(locations)} location snapshots (sample)")
    tco = _scan_all(f'cms-{STAGE}-storage-vehicle-costs', limit=500)
    print(f"  loaded {len(tco)} TCO rollups (sample)")
    maintenance = _scan_all(f'cms-{STAGE}-storage-maintenance-alerts')
    print(f"  loaded {len(maintenance)} maintenance alerts")

    all_actions = []
    all_actions += build_recall_actions(vehicles, recalls)
    all_actions += build_warranty_actions(warranty)
    all_actions += build_rebalancing_actions(locations)
    all_actions += build_cost_actions(vehicles, tco)
    all_actions += build_maintenance_actions(maintenance, vehicles)
    print(f"  generated {len(all_actions)} actions")

    table = ddb.Table(f'cms-{STAGE}-vfo-action-queue')
    with table.batch_writer() as batch:
        for a in all_actions:
            batch.put_item(Item=a)
    print(f"  wrote {len(all_actions)} actions to cms-{STAGE}-vfo-action-queue")

    # Summary
    from collections import Counter
    status_counts = Counter(a['status'] for a in all_actions)
    domain_counts = Counter(a['domain'] for a in all_actions)
    print(f"\nStatus breakdown: {dict(status_counts)}")
    print(f"Domain breakdown: {dict(domain_counts)}")


if __name__ == '__main__':
    main()
