#!/usr/bin/env python3
"""
Patch estimatedCost on existing maintenance-alerts rows.

Why this exists
---------------
The Flink MaintenanceProcessor at modules/flink/.../MaintenanceProcessor.java
populates `estimatedCost` from a switch on `alertType`. Several alert types
emitted by the current event catalog (e.g. maintenance.coolant_critical_overheat,
maintenance.system_voltage_low_minor, maintenance.turbo_underboost) are not
in that switch, so they fall through to `default: return 200.0;`. Result:
every OPEN alert row in DDB has estimatedCost = 200, regardless of severity
or actual repair scope, which made the Maintenance Alerts table on
/alerts/maintenance look broken — every row showed $200.

This script patches the existing rows with deterministic, more realistic
estimates. The right long-term fix is to extend the Flink switch and
redeploy the job, but that's a heavier deploy; this script unblocks the
UI immediately.

Behaviour
---------
* Idempotent: cost is a pure function of (alertType, severity). Re-running
  the script produces the same values, so it's safe to re-run after new
  alerts come in.
* Conservative: only updates rows where the **current** estimatedCost is
  the suspicious 200.0 default OR is missing. Rows where someone has
  already set a different cost (e.g. dispatcher updated after a quote)
  are left alone.
* Dry-run by default. Pass `--apply` to actually write.

Usage
-----
    python3 deployment/scripts/fix_maintenance_alert_costs.py            # preview
    python3 deployment/scripts/fix_maintenance_alert_costs.py --apply    # write
    python3 deployment/scripts/fix_maintenance_alert_costs.py --apply --table cms-dev-storage-maintenance-alerts

Cost table
----------
Costs are domain-realistic ranges for the stated alert. If an alertType is
unknown to this script, it falls back to a severity-only estimate so the
operator at least sees a non-default number scaled to urgency. Update the
COST_BY_ALERT_TYPE map as new event-catalog entries are added.
"""

import argparse
import os
import sys
from decimal import Decimal
from typing import Dict, Optional, Tuple

import boto3
from botocore.config import Config

DEFAULT_TABLE = os.environ.get("MAINTENANCE_ALERTS_TABLE", "cms-prod-storage-maintenance-alerts")

# Per-alertType cost (USD). Numbers are deliberately rough — the column is
# labelled "Est. Cost" and the operator uses it for triage, not invoicing.
# When you add a new event_id to deployment/scripts/seed_vsa_demo_events.py
# (or wherever event catalogs are extended), add a corresponding entry
# here, otherwise the alert will fall through to the severity fallback.
COST_BY_ALERT_TYPE: Dict[str, float] = {
    # P0 — stop-driving / safety-critical
    "maintenance.brake_system_fault": 750.0,
    "maintenance.coolant_critical_overheat": 1500.0,
    "maintenance.transmission_failure": 3500.0,
    "maintenance.ev_battery_thermal_event": 5000.0,
    # P1 — urgent, service within 48h
    "maintenance.low_brake_fluid": 320.0,
    "maintenance.pcm_processor_fault": 1500.0,
    "maintenance.traction_control_fault": 600.0,
    "maintenance.lost_comm_pcm": 1200.0,
    "maintenance.engine_misfire_severe": 750.0,
    "maintenance.turbo_underboost": 1200.0,
    # P2 — schedule within a week
    "maintenance.camshaft_sensor_fault": 350.0,
    "maintenance.catalyst_efficiency_low": 1500.0,
    "maintenance.wheel_speed_sensor_lf": 280.0,
    "maintenance.wheel_speed_sensor_rf": 280.0,
    "maintenance.lean_fuel_mixture": 400.0,
    "maintenance.invalid_data_from_ecm": 250.0,
    # P3 — minor, schedule at next service
    "maintenance.small_evap_leak": 180.0,
    "maintenance.system_voltage_low_minor": 180.0,
    "maintenance.ecu_internal_flag": 220.0,
    # Pre-existing entries from the Flink switch (kept here for the same
    # idempotent fallback path; values mirror MaintenanceProcessor.java).
    "maintenance.tire_pressure": 35.0,
    "maintenance.tire_rotation_due": 60.0,
    "maintenance.tire_tread_low": 680.0,
    "maintenance.tire_replacement_critical": 800.0,
    "maintenance.brake_wear": 350.0,
    "maintenance.brake_replacement_critical": 550.0,
    "maintenance.high_engine_temp": 1200.0,
    "maintenance.coolant_flush_due": 120.0,
    "maintenance.low_oil_pressure": 250.0,
    "maintenance.oil_change_due": 75.0,
    "maintenance.oil_life_low": 75.0,
    "maintenance.engine_overspeed": 500.0,
    "maintenance.spark_plug_replacement": 200.0,
    "maintenance.battery_replacement": 180.0,
    "maintenance.low_battery": 150.0,
    "maintenance.alternator_failure": 650.0,
    "maintenance.starter_motor_failure": 500.0,
    "maintenance.diagnostic_codes_active": 120.0,
    "maintenance.filter_replacement": 45.0,
    "maintenance.fuel_filter_clogged": 95.0,
    "maintenance.transmission_service_due": 250.0,
    "maintenance.def_system_fault": 600.0,
    "maintenance.motor_overheating": 2500.0,
    "maintenance.hv_battery_cooling_overtemp": 3500.0,
    "maintenance.suspension_wear": 800.0,
    "maintenance.wheel_bearing_wear": 400.0,
    "maintenance.ac_compressor_failure": 900.0,
    "maintenance.excessive_idle": 0.0,
}

# Severity-only fallback used when alertType isn't in the table above. Better
# than $200 for everything, still scales with urgency.
COST_BY_SEVERITY: Dict[str, float] = {
    "CRITICAL": 1000.0,
    "HIGH": 500.0,
    "MEDIUM": 250.0,
    "LOW": 100.0,
}

# Don't touch rows whose existing cost looks deliberate — only patch the
# default-fallback value or missing field.
SUSPECT_COSTS = {Decimal("200"), Decimal("200.0"), Decimal("200.00")}


def estimate_cost(alert_type: Optional[str], severity: Optional[str]) -> float:
    if alert_type and alert_type in COST_BY_ALERT_TYPE:
        return COST_BY_ALERT_TYPE[alert_type]
    sev = (severity or "").upper()
    return COST_BY_SEVERITY.get(sev, 200.0)


def should_update(item: dict) -> Tuple[bool, str]:
    current = item.get("estimatedCost")
    if current is None:
        return True, "missing"
    # boto3 returns DDB N values as Decimal
    if isinstance(current, Decimal) and current in SUSPECT_COSTS:
        return True, "default-200"
    return False, "preserved"


def scan_all(table) -> list:
    items = []
    kwargs: dict = {"ProjectionExpression": "alertId, alertType, severity, estimatedCost"}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        last = resp.get("LastEvaluatedKey")
        if not last:
            return items
        kwargs["ExclusiveStartKey"] = last


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--table", default=DEFAULT_TABLE, help=f"DDB table name (default: {DEFAULT_TABLE})")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--apply", action="store_true", help="Actually write updates (default: dry run)")
    parser.add_argument("--max-preview", type=int, default=10, help="How many rows to preview in dry-run output")
    args = parser.parse_args()

    cfg = Config(retries={"max_attempts": 5, "mode": "standard"})
    ddb = boto3.resource("dynamodb", region_name=args.region, config=cfg)
    table = ddb.Table(args.table)

    print(f"Scanning {args.table} in {args.region}…")
    items = scan_all(table)
    print(f"Found {len(items)} alert rows.\n")

    plan = []
    skipped_preserved = 0
    for item in items:
        update, reason = should_update(item)
        if not update:
            skipped_preserved += 1
            continue
        new_cost = estimate_cost(item.get("alertType"), item.get("severity"))
        plan.append((item, new_cost, reason))

    print(f"Plan: update {len(plan)} rows, leave {skipped_preserved} alone (non-default cost preserved).\n")

    # Show a sample so the operator can sanity-check before applying.
    if plan:
        print(f"Preview (first {min(args.max_preview, len(plan))} rows):")
        print(f"  {'alertType':45} {'sev':10} {'old':>9}  →  {'new':>9}  why")
        for item, new_cost, reason in plan[: args.max_preview]:
            old = item.get("estimatedCost")
            old_s = "—" if old is None else f"${float(old):.2f}"
            print(f"  {(item.get('alertType') or ''):45} {(item.get('severity') or ''):10} {old_s:>9}  →  ${new_cost:8.2f}  {reason}")
        print()

    if not args.apply:
        print("Dry run complete. Re-run with --apply to write the changes.")
        return 0

    if not plan:
        print("Nothing to update.")
        return 0

    print(f"Applying {len(plan)} updates…")
    written = 0
    for item, new_cost, _reason in plan:
        try:
            table.update_item(
                Key={"alertId": item["alertId"]},
                UpdateExpression="SET estimatedCost = :c",
                ExpressionAttributeValues={":c": Decimal(f"{new_cost:.2f}")},
            )
            written += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ! Failed alertId={item.get('alertId')}: {e}", file=sys.stderr)
    print(f"Done. Updated {written} / {len(plan)} rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
