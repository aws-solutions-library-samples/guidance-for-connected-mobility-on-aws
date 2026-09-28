#!/usr/bin/env python3
"""Tests for ``backfill_dtc_dedup``.

The mock DDB table returns resource-deserialized items (plain dicts, Decimal
for numeric attributes) — matching what ``boto3.resource('dynamodb').Table()``
actually yields in production.

Run from ``deployment/scripts/``::

    python3 -m unittest test_backfill_dtc_dedup.py -v
"""
from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from unittest.mock import MagicMock

import backfill_dtc_dedup

PROCESSOR_SOURCE = "flink-maintenance-processor"
T1, T2, T3 = Decimal("1000"), Decimal("2000"), Decimal("3000")


def _row(vehicle_id, code, source, status="ACTIVE", first_seen=None, ts=None):
    """Build a resource-deserialized DDB item (plain dict, Decimal numerics)."""
    r = {
        "vehicleId": vehicle_id,
        "code": code,
        "status": status,
        "timestamp": ts if ts is not None else T1,
    }
    if source is not None:
        r["source"] = source
    if first_seen is not None:
        r["firstSeenAt"] = first_seen
    return r


class TestDryRun(unittest.TestCase):
    def test_dry_run_emits_no_writes(self):
        """Dry-run: 3 dup ACTIVE rows → no DDB writes, plan in stdout."""
        import io
        rows = [
            _row("VEH-1", "P0217", PROCESSOR_SOURCE, first_seen=T1, ts=T1),
            _row("VEH-1", "P0217", PROCESSOR_SOURCE, first_seen=T2, ts=T2),
            _row("VEH-1", "P0217", PROCESSOR_SOURCE, first_seen=T3, ts=T3),
        ]
        table_mock = MagicMock()
        table_mock.scan.return_value = {"Items": rows, "Count": len(rows)}
        out = io.StringIO()

        backfill_dtc_dedup.run(table=table_mock, apply=False, out=out)

        table_mock.update_item.assert_not_called()
        table_mock.delete_item.assert_not_called()
        self.assertRegex(out.getvalue(), r"would update|would delete")


class TestApply(unittest.TestCase):
    def test_apply_collapses_duplicates(self):
        """--apply: winner = earliest firstSeenAt; 1 update + 2 deletes; correct attrs."""
        rows = [
            _row("VEH-1", "P0217", PROCESSOR_SOURCE, first_seen=T1, ts=T1),
            _row("VEH-1", "P0217", PROCESSOR_SOURCE, first_seen=T2, ts=T2),
            _row("VEH-1", "P0217", PROCESSOR_SOURCE, first_seen=T3, ts=T3),
        ]
        table_mock = MagicMock()
        table_mock.scan.return_value = {"Items": rows, "Count": len(rows)}

        backfill_dtc_dedup.run(table=table_mock, apply=True)

        self.assertEqual(table_mock.update_item.call_count, 1)
        self.assertEqual(table_mock.delete_item.call_count, 2)

        eav = table_mock.update_item.call_args[1].get("ExpressionAttributeValues", {})
        occ = next((v for k, v in eav.items() if "occ" in k.lower() or k == ":oc"), None)
        self.assertIsNotNone(occ, "occurrenceCount missing from update")
        self.assertEqual(int(str(occ)), 3)

    def test_idempotent_on_clean_table(self):
        """Already-collapsed table (one row per group) → zero writes."""
        rows = [_row("VEH-1", "P0217", PROCESSOR_SOURCE, first_seen=T1, ts=T1)]
        table_mock = MagicMock()
        table_mock.scan.return_value = {"Items": rows, "Count": 1}

        backfill_dtc_dedup.run(table=table_mock, apply=True)

        table_mock.update_item.assert_not_called()
        table_mock.delete_item.assert_not_called()


class TestFiltering(unittest.TestCase):
    def test_skips_non_processor_sources(self):
        """Legacy rows (no source) are not collapsed even when code matches."""
        rows = [
            _row("VEH-1", "P0217", PROCESSOR_SOURCE, first_seen=T1, ts=T1),
            _row("VEH-1", "P0217", PROCESSOR_SOURCE, first_seen=T2, ts=T2),
            _row("VEH-1", "P0217", source=None, ts=T1),
            _row("VEH-1", "P0217", source=None, ts=T2),
        ]
        table_mock = MagicMock()
        table_mock.scan.return_value = {"Items": rows, "Count": len(rows)}

        backfill_dtc_dedup.run(table=table_mock, apply=True)

        # Only the 2 processor-source dups collapse
        self.assertEqual(table_mock.update_item.call_count, 1)
        self.assertEqual(table_mock.delete_item.call_count, 1)

    def test_skips_cleared_rows(self):
        """CLEARED rows are not updated or deleted."""
        rows = [
            _row("VEH-1", "P0217", PROCESSOR_SOURCE, status="ACTIVE", first_seen=T1, ts=T1),
            _row("VEH-1", "P0217", PROCESSOR_SOURCE, status="ACTIVE", first_seen=T2, ts=T2),
            _row("VEH-1", "P0217", PROCESSOR_SOURCE, status="CLEARED", first_seen=T3, ts=T3),
        ]
        table_mock = MagicMock()
        table_mock.scan.return_value = {"Items": rows, "Count": len(rows)}

        backfill_dtc_dedup.run(table=table_mock, apply=True)

        self.assertEqual(table_mock.update_item.call_count, 1)
        self.assertEqual(table_mock.delete_item.call_count, 1)
        deleted_ts = table_mock.delete_item.call_args[1]["Key"]["timestamp"]
        self.assertNotEqual(int(deleted_ts), int(T3), "CLEARED row must not be deleted")


class TestErrorHandling(unittest.TestCase):
    def test_continues_on_per_row_error(self):
        """One failing DeleteItem → script continues, errors > 0."""
        from botocore.exceptions import ClientError

        rows = [
            _row("VEH-1", "P0001", PROCESSOR_SOURCE, first_seen=T1, ts=T1),
            _row("VEH-1", "P0001", PROCESSOR_SOURCE, first_seen=T2, ts=T2),
            _row("VEH-2", "P0002", PROCESSOR_SOURCE, first_seen=T1, ts=T1),
            _row("VEH-2", "P0002", PROCESSOR_SOURCE, first_seen=T2, ts=T2),
        ]
        table_mock = MagicMock()
        table_mock.scan.return_value = {"Items": rows, "Count": 4}
        table_mock.delete_item.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "fail"}}, "DeleteItem"
        )

        result = backfill_dtc_dedup.run(table=table_mock, apply=True)

        self.assertGreater(result.get("errors", 0), 0)
        self.assertEqual(table_mock.update_item.call_count, 2)


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(unittest.TestLoader().loadTestsFromName(__name__))
    sys.exit(0 if result.wasSuccessful() else 1)
