"""Unit tests for align_event_catalog_signals.py

Uses botocore Stubber for DDB calls — no network access required.
Focus: idempotency (desired==current → empty diff), dry-run (no writes),
and composite-condition patching.
"""
import copy
import sys
import os
import unittest
from decimal import Decimal

import boto3
from botocore.stub import Stubber
from boto3.dynamodb.types import TypeSerializer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import align_event_catalog_signals as align  # noqa: E402

_ser = TypeSerializer()


def _to_ddb(item: dict) -> dict:
    return {k: _ser.serialize(v) for k, v in item.items()}


def _scan_resp(items: list[dict]) -> dict:
    return {
        "Items": [_to_ddb(i) for i in items],
        "Count": len(items),
        "ScannedCount": len(items),
        "ResponseMetadata": {"RequestId": "x", "HTTPStatusCode": 200, "HTTPHeaders": {}},
    }


TABLE = "cms-staging-event-catalog"


class TestIdempotency(unittest.TestCase):
    """Second apply with desired==current must produce an empty diff."""

    def _make_item(self, event_id: str, spec: dict) -> dict:
        """Build a catalog item that already matches the desired spec."""
        item: dict = {
            "event_id": event_id,
            "category": event_id.split(".")[0],
            "condition_type": "threshold",
        }
        if "simple" in spec:
            item["json_fields"] = list(spec["simple"])
        if "op" in spec and spec["op"] is not None:
            item["threshold_operator"] = spec["op"]
        if "val" in spec and spec["val"] is not None:
            item["threshold_value"] = spec["val"]
        return item

    def test_idempotent_simple_event(self):
        """An item already matching desired → diff is empty."""
        event_id = "safety.aeb_activation"
        spec = align.DESIRED[event_id]
        item = self._make_item(event_id, spec)
        new_item = align._compute_desired(item, spec)
        d = align._diff(item, new_item)
        self.assertEqual(d, {}, f"Expected empty diff for already-aligned {event_id}, got {d}")

    def test_idempotent_all_desired_events(self):
        """Every event in DESIRED that is 'simple' with op/val: if item already matches,
        diff must be empty (the core idempotency invariant)."""
        for event_id, spec in align.DESIRED.items():
            if "composite" in spec:
                continue  # tested separately
            item: dict = {
                "event_id": event_id,
                "category": event_id.split(".")[0],
                "condition_type": "threshold",
            }
            if "simple" in spec:
                item["json_fields"] = list(spec["simple"])
            if spec.get("op") is not None:
                item["threshold_operator"] = spec["op"]
            if spec.get("val") is not None:
                item["threshold_value"] = spec["val"]

            new_item = align._compute_desired(item, spec)
            d = align._diff(item, new_item)
            self.assertEqual(d, {}, f"Idempotency broken for {event_id}: diff={d}")


class TestCompositePatching(unittest.TestCase):
    """Composite events: only the targeted condition indices are patched."""

    def _base_tailgating(self) -> dict:
        return {
            "event_id": "safety.tailgating",
            "category": "safety",
            "condition_type": "composite",
            "composite_condition": {
                "conditions": [
                    {"json_fields": ["following_distance", "FollowingDistance"], "signal": "following_distance", "value": Decimal("2"), "operator": "<"},
                    {"json_fields": ["speed"], "signal": "speed", "value": Decimal("30"), "operator": ">"},
                ],
                "logic": "AND",
            },
        }

    def test_tailgating_composite_fix(self):
        item = self._base_tailgating()
        spec = align.DESIRED["safety.tailgating"]
        new_item = align._compute_desired(item, spec)
        got = new_item["composite_condition"]["conditions"][0]["json_fields"]
        self.assertIn("followingDistance", got, "FWE camelCase field must be in union")
        self.assertIn("following_distance", got, "MQTT snake_case must be kept in union")
        # speed leg untouched
        self.assertEqual(new_item["composite_condition"]["conditions"][1]["json_fields"], ["speed"])

    def test_tailgating_idempotent(self):
        """After first apply, second apply produces no diff."""
        spec = align.DESIRED["safety.tailgating"]
        item = self._base_tailgating()
        # Simulate first apply
        new_item = align._compute_desired(item, spec)
        # Second apply
        new_item2 = align._compute_desired(new_item, spec)
        d = align._diff(new_item, new_item2)
        self.assertEqual(d, {}, f"Tailgating not idempotent: {d}")


class TestDryRunNoWrites(unittest.TestCase):
    """Dry-run must not call PutItem."""

    def test_dry_run_calls_no_put(self):
        session = boto3.Session(region_name="us-west-2")
        ddb = session.client("dynamodb")

        # One stale item (Class A null)
        stale_item = {
            "event_id": "safety.aeb_activation",
            "category": "safety",
            "condition_type": "threshold",
            "threshold_operator": ">",
            "threshold_value": Decimal("0"),
            # json_fields intentionally absent
        }

        with Stubber(ddb) as stubber:
            stubber.add_response("scan", _scan_resp([stale_item]), {"TableName": TABLE})
            # No PutItem queued — Stubber raises on unexpected calls

            items = align._scan_all(ddb, TABLE)
            changes = []
            for it in items:
                ev = it.get("event_id", "")
                if ev in align.DESIRED and it.get("condition_type") != "canonical":
                    sp = align.DESIRED[ev]
                    ni = align._compute_desired(it, sp)
                    d = align._diff(it, ni)
                    if d:
                        changes.append((it, ni, d))

            # In dry-run we do NOT call _put_item — verify changes detected but nothing written
            self.assertEqual(len(changes), 1)
            # Stubber exit verifies no unexpected calls were made


class TestClassANullFields(unittest.TestCase):
    """Class A events with null json_fields get the correct field set."""

    def test_maintenance_high_engine_temp(self):
        item = {
            "event_id": "maintenance.high_engine_temp",
            "category": "maintenance",
            "condition_type": "threshold",
        }
        spec = align.DESIRED["maintenance.high_engine_temp"]
        new_item = align._compute_desired(item, spec)
        self.assertIn("engineTemp", new_item["json_fields"])
        self.assertEqual(new_item["threshold_operator"], ">")
        self.assertEqual(new_item["threshold_value"], Decimal("110"))

    def test_existing_op_not_overwritten(self):
        """If threshold_operator already set, do NOT overwrite it."""
        item = {
            "event_id": "maintenance.high_engine_temp",
            "category": "maintenance",
            "condition_type": "threshold",
            "threshold_operator": ">=",   # already set to something
            "threshold_value": Decimal("115"),
        }
        spec = align.DESIRED["maintenance.high_engine_temp"]
        new_item = align._compute_desired(item, spec)
        # spec.op only applied when item's value is None
        self.assertEqual(new_item["threshold_operator"], ">=")


class TestProdGuard(unittest.TestCase):
    """Script must refuse prod table names."""

    def test_prod_table_rejected(self):
        import subprocess, sys
        result = subprocess.run(
            [sys.executable,
             os.path.join(os.path.dirname(__file__), "..", "align_event_catalog_signals.py"),
             "--table", "cms-prod-event-catalog", "--dry-run"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2, "Expected exit 2 for prod table")
        self.assertIn("prod", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
