"""
Event Catalog Driver — reads event definitions from DynamoDB and manipulates
telemetry signals to trigger specific events during simulation.

The event catalog is the source of truth. The simulator doesn't need hardcoded
flags for each event type — it reads the catalog and knows how to produce
the conditions that trigger each event.
"""

import boto3
import random
from typing import Dict, List, Optional


class EventCatalogDriver:
    """Loads event catalog from DynamoDB and drives telemetry signals to trigger events."""

    def __init__(self, region: str, stage: str = "prod", profile: str = None):
        if profile:
            session = boto3.Session(profile_name=profile, region_name=region)
        else:
            session = boto3.Session(region_name=region)
        self.ddb = session.resource("dynamodb", region_name=region)
        self.table_name = f"cms-{stage}-event-catalog"
        self.events: Dict[str, dict] = {}
        self.active_events: List[str] = []
        self._load_catalog()

    def _load_catalog(self):
        """Load all events from the catalog table."""
        try:
            table = self.ddb.Table(self.table_name)
            resp = table.scan()
            items = resp.get("Items", [])
            while "LastEvaluatedKey" in resp:
                resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
                items.extend(resp.get("Items", []))
            for item in items:
                eid = item.get("event_id", "")
                self.events[eid] = {
                    "event_id": eid,
                    "category": item.get("category", ""),
                    "description": item.get("description", ""),
                    "trigger_signal": item.get("trigger_signal", ""),
                    "json_fields": [f if isinstance(f, str) else f for f in item.get("json_fields", [])],
                    "threshold_operator": item.get("threshold_operator", "<"),
                    "threshold_value": float(item.get("threshold_value", 0)),
                    "condition_type": item.get("condition_type", "simple"),
                    "composite_condition": item.get("composite_condition"),
                    "severity": int(item.get("severity", 1)),
                    "duration_ms": int(item.get("duration_ms", 0)),
                }
            print(f"📋 Loaded {len(self.events)} events from {self.table_name}")
        except Exception as e:
            print(f"⚠️  Failed to load event catalog: {e}")

    def set_active_events(self, event_ids: List[str]):
        """Set which events should be actively triggered during simulation."""
        valid = [eid for eid in event_ids if eid in self.events]
        unknown = [eid for eid in event_ids if eid not in self.events]
        if unknown:
            print(f"⚠️  Unknown event IDs (not in catalog): {unknown}")
        self.active_events = valid
        for eid in valid:
            evt = self.events[eid]
            print(f"  🎯 {eid}: {evt['description']} "
                  f"(signal={evt['json_fields']}, {evt['threshold_operator']}{evt['threshold_value']})")

    def list_events(self, category: Optional[str] = None) -> List[dict]:
        """List available events, optionally filtered by category."""
        events = list(self.events.values())
        if category:
            events = [e for e in events if e["category"] == category]
        return sorted(events, key=lambda e: e["event_id"])

    def apply_to_telemetry(self, telemetry: dict, tick: int = 0) -> dict:
        """DEPRECATED — use compute_degradation_targets() instead.
        Kept for backward compatibility."""
        return telemetry

    def compute_degradation_targets(self) -> dict:
        """Compute degradation targets for all active events.
        
        Returns: {field_name: target_value}
        
        For each active event, computes a target value that will cross the threshold:
          - operator '<': target = threshold * 0.7 (well below)
          - operator '>': target = threshold * 1.15 (well above)  
          - operator '=': target = exact value
        """
        targets = {}
        for eid in self.active_events:
            evt = self.events[eid]
            if evt["condition_type"] == "simple":
                op = evt["threshold_operator"]
                threshold = evt["threshold_value"]
                for field in evt["json_fields"]:
                    if op in ("<", "<="):
                        targets[field] = threshold * 0.7
                    elif op in (">", ">="):
                        targets[field] = threshold * 1.15
                    elif op == "=":
                        targets[field] = threshold
            elif evt["condition_type"] == "composite":
                comp = evt.get("composite_condition", {})
                for cond in comp.get("conditions", []):
                    op = cond.get("operator", "=")
                    value = float(cond.get("value", 0))
                    fields = cond.get("json_fields", [cond.get("signal", "")])
                    for field in fields:
                        if isinstance(field, str) and field:
                            if op in ("<", "<="):
                                targets[field] = value * 0.7
                            elif op in (">", ">="):
                                targets[field] = value * 1.15
                            elif op == "=":
                                targets[field] = value
        return targets

    def _apply_simple(self, telemetry: dict, evt: dict, tick: int) -> dict:
        """Force a simple threshold event by manipulating the signal."""
        fields = evt["json_fields"]
        op = evt["threshold_operator"]
        threshold = evt["threshold_value"]

        for field in fields:
            if field not in telemetry:
                continue
            current = float(telemetry[field])
            target = self._compute_target(current, op, threshold, tick)
            telemetry[field] = round(target, 1)
        return telemetry

    def _apply_composite(self, telemetry: dict, evt: dict, tick: int) -> dict:
        """Force a composite (AND/OR) event by satisfying all conditions."""
        comp = evt.get("composite_condition", {})
        conditions = comp.get("conditions", [])
        for cond in conditions:
            signal = cond.get("signal", "")
            value = float(cond.get("value", 0))
            op = cond.get("operator", "=")
            fields = cond.get("json_fields", [signal])
            for field in fields:
                if isinstance(field, str) and field in telemetry:
                    telemetry[field] = self._compute_target(
                        float(telemetry[field]), op, value, tick
                    )
        return telemetry

    def _compute_target(self, current: float, op: str, threshold: float, tick: int) -> float:
        """Compute a target value that crosses the threshold progressively.
        
        Progressive: values degrade over ticks rather than jumping instantly,
        making the telemetry stream look realistic.
        """
        # How aggressively to approach the threshold (more ticks = closer)
        progress = min(1.0, tick / max(1, 20))  # Full effect after ~20 ticks
        noise = random.uniform(-0.5, 0.5)

        if op == "<":
            # Need value to go below threshold
            target = threshold - (threshold * 0.3 * progress) + noise
            return min(current, target) if progress > 0.3 else current - (current * 0.05 * progress)
        elif op == ">":
            # Need value to go above threshold
            target = threshold + (threshold * 0.3 * progress) + noise
            return max(current, target) if progress > 0.3 else current + (current * 0.05 * progress)
        elif op == "=":
            # Need value to equal threshold
            if progress > 0.5:
                return threshold
            return current
        elif op == ">=":
            target = threshold + abs(noise)
            return max(current, target) if progress > 0.3 else current
        elif op == "<=":
            target = threshold - abs(noise)
            return min(current, target) if progress > 0.3 else current
        return current


    def get_safe_ranges(self) -> dict:
        """Build a map of json_field → (safe_min, safe_max) using signal catalog ranges
        and event catalog thresholds. The safe range is the zone that won't trigger any event.
        
        Returns: {field_name: (safe_min, safe_max, unit)}
        """
        # Load signal catalog for min/max ranges
        sig_ranges = {}
        try:
            sig_table_name = self.table_name.replace("event-catalog", "signal-catalog")
            table = self.ddb.Table(sig_table_name)
            resp = table.scan()
            items = resp.get("Items", [])
            while "LastEvaluatedKey" in resp:
                resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
                items.extend(resp.get("Items", []))
            for item in items:
                jf = item.get("json_field", "")
                if jf:
                    sig_ranges[jf] = {
                        "min": float(item.get("min_value", 0)),
                        "max": float(item.get("max_value", 100)),
                        "unit": item.get("unit", ""),
                    }
        except Exception as e:
            print(f"⚠️  Failed to load signal catalog: {e}")

        # Build threshold boundaries from event catalog
        # For each field, find the closest threshold and stay away from it
        safe = {}

        for field, sig in sig_ranges.items():
            sig_min = sig["min"]
            sig_max = sig["max"]
            safe_min = sig_min
            safe_max = sig_max

            # Find all thresholds for this field and stay on the safe side
            for eid, evt in self.events.items():
                if field not in evt.get("json_fields", []):
                    continue
                op = evt["threshold_operator"]
                threshold = evt["threshold_value"]
                # Use 10% of threshold value as margin (minimum 1.0)
                margin = max(1.0, abs(threshold) * 0.10)

                if op in ("<", "<="):
                    # Alert fires when value < threshold → safe is above
                    safe_min = max(safe_min, threshold + margin)
                elif op in (">", ">="):
                    # Alert fires when value > threshold → safe is below
                    safe_max = min(safe_max, threshold - margin)

            # Ensure valid range
            if safe_min >= safe_max:
                mid = (sig_min + sig_max) / 2
                safe_min = mid * 0.9
                safe_max = mid * 1.1

            safe[field] = (round(safe_min, 2), round(safe_max, 2), sig.get("unit", ""))

        return safe
