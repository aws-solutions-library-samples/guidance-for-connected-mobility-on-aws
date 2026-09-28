"""Tests for backfill_data_source_enum.py

Uses botocore.stub.Stubber to mock DynamoDB calls without any network access.
"""
import sys
import os
import unittest

import boto3
from botocore.stub import Stubber

# Add deployment/scripts to path so we can import the script directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import backfill_data_source_enum as backfill  # noqa: E402


def _make_scan_response(items):
    """Build a DDB client-style scan response for the given items."""
    return {
        "Items": items,
        "Count": len(items),
        "ScannedCount": len(items),
        "ResponseMetadata": {"RequestId": "x", "HTTPStatusCode": 200, "HTTPHeaders": {}},
    }


def _make_fleet(fleet_id, data_source=None):
    item = {"fleetId": {"S": fleet_id}}
    if data_source is not None:
        item["data_source"] = {"S": data_source}
    return item


TABLE = "cms-staging-storage-fleets-us-west-2-123456789012"


class TestBackfillDataSourceEnum(unittest.TestCase):

    # (a) Dry-run: correct proposed set, zero DDB writes
    def test_dry_run_no_writes(self):
        session = boto3.Session(region_name="us-west-2")
        ddb = session.client("dynamodb")

        items = [
            _make_fleet("fleet-1", "onboard-fwe"),
            _make_fleet("fleet-2", "cloud-oem1"),
            _make_fleet("fleet-3", "vehicle-telemetry"),  # already new — skip
        ]

        with Stubber(ddb) as stubber:
            # Scan only — no UpdateItem registered; Stubber raises on unexpected calls
            stubber.add_response("scan", _make_scan_response(items), {"TableName": TABLE})

            all_items = backfill._scan_all(ddb, TABLE)
            # Categorize proposed changes (mirrors run() dry-run logic)
            proposed = []
            stats = {"scanned": len(all_items), "rewritten": 0, "already_new": 0, "no_attr": 0, "failed": 0}
            for item in all_items:
                raw_ds = item.get("data_source", {}).get("S", "")
                if not raw_ds:
                    stats["no_attr"] += 1
                    continue
                new_val = backfill.REWRITE_MAP.get(raw_ds)
                if new_val is None:
                    stats["already_new"] += 1
                    continue
                proposed.append((item["fleetId"]["S"], raw_ds, new_val))

        # Assert proposed set is correct (after Stubber context exits cleanly)
        self.assertEqual(len(proposed), 2)
        fleet_ids = {p[0] for p in proposed}
        self.assertIn("fleet-1", fleet_ids)
        self.assertIn("fleet-2", fleet_ids)
        self.assertNotIn("fleet-3", fleet_ids)
        self.assertEqual(stats["already_new"], 1)
        self.assertEqual(stats["no_attr"], 0)
        # No UpdateItem was queued in the Stubber — Stubber.__exit__ verifies no unexpected calls

    # (b) --apply rewrites exactly the OLD-string rows
    def test_apply_rewrites_old_string_rows(self):
        session = boto3.Session(region_name="us-west-2")
        ddb = session.client("dynamodb")

        items = [
            _make_fleet("fleet-a", "onboard-fwe"),
            _make_fleet("fleet-b", "cloud-oem1"),
        ]

        with Stubber(ddb) as stubber:
            stubber.add_response("scan", _make_scan_response(items), {"TableName": TABLE})
            # UpdateItem for fleet-a
            stubber.add_response(
                "update_item",
                {"ResponseMetadata": {"RequestId": "r1", "HTTPStatusCode": 200, "HTTPHeaders": {}}},
                {
                    "TableName": TABLE,
                    "Key": {"fleetId": {"S": "fleet-a"}},
                    "UpdateExpression": "SET data_source = :new_val",
                    "ConditionExpression": "attribute_exists(data_source) AND data_source IN (:o1, :o2)",
                    "ExpressionAttributeValues": {
                        ":o1":      {"S": "onboard-fwe"},
                        ":o2":      {"S": "cloud-oem1"},
                        ":new_val": {"S": "vehicle-telemetry"},
                    },
                },
            )
            # UpdateItem for fleet-b
            stubber.add_response(
                "update_item",
                {"ResponseMetadata": {"RequestId": "r2", "HTTPStatusCode": 200, "HTTPHeaders": {}}},
                {
                    "TableName": TABLE,
                    "Key": {"fleetId": {"S": "fleet-b"}},
                    "UpdateExpression": "SET data_source = :new_val",
                    "ConditionExpression": "attribute_exists(data_source) AND data_source IN (:o1, :o2)",
                    "ExpressionAttributeValues": {
                        ":o1":      {"S": "onboard-fwe"},
                        ":o2":      {"S": "cloud-oem1"},
                        ":new_val": {"S": "cloud-telemetry"},
                    },
                },
            )

            all_items = backfill._scan_all(ddb, TABLE)
            stats = {"scanned": len(all_items), "rewritten": 0, "already_new": 0, "no_attr": 0, "failed": 0}
            for item in all_items:
                fleet_id = item["fleetId"]["S"]
                raw_ds = item.get("data_source", {}).get("S", "")
                if not raw_ds:
                    stats["no_attr"] += 1
                    continue
                new_val = backfill.REWRITE_MAP.get(raw_ds)
                if new_val is None:
                    stats["already_new"] += 1
                    continue
                backfill._apply_update(ddb, TABLE, fleet_id, new_val, stats)

            self.assertEqual(stats["rewritten"], 2)
            self.assertEqual(stats["already_new"], 0)
            self.assertEqual(stats["failed"], 0)

    # (c) Idempotency: second --apply run produces 0 rewrites (ConditionalCheckFailed)
    def test_idempotency_second_run_zero_rewrites(self):
        session = boto3.Session(region_name="us-west-2")
        ddb = session.client("dynamodb")

        # Simulate DDB rows that still contain the OLD string (as scanned),
        # but UpdateItem returns ConditionalCheckFailedException (row was concurrently updated)
        items = [_make_fleet("fleet-x", "onboard-fwe")]

        with Stubber(ddb) as stubber:
            stubber.add_response("scan", _make_scan_response(items), {"TableName": TABLE})
            stubber.add_client_error(
                "update_item",
                service_error_code="ConditionalCheckFailedException",
                http_status_code=400,
            )

            all_items = backfill._scan_all(ddb, TABLE)
            stats = {"scanned": len(all_items), "rewritten": 0, "already_new": 0, "no_attr": 0, "failed": 0}
            for item in all_items:
                fleet_id = item["fleetId"]["S"]
                raw_ds = item.get("data_source", {}).get("S", "")
                if not raw_ds:
                    stats["no_attr"] += 1
                    continue
                new_val = backfill.REWRITE_MAP.get(raw_ds)
                if new_val is None:
                    stats["already_new"] += 1
                    continue
                backfill._apply_update(ddb, TABLE, fleet_id, new_val, stats)

            self.assertEqual(stats["rewritten"], 0)
            self.assertEqual(stats["already_new"], 1)
            self.assertEqual(stats["failed"], 0)

    # (d) Missing-attribute rows are skipped, not rewritten
    def test_missing_attribute_rows_skipped(self):
        session = boto3.Session(region_name="us-west-2")
        ddb = session.client("dynamodb")

        items = [
            _make_fleet("fleet-legacy"),           # no data_source at all
            _make_fleet("fleet-normal", "cloud-oem1"),
        ]

        with Stubber(ddb) as stubber:
            stubber.add_response("scan", _make_scan_response(items), {"TableName": TABLE})
            # Only one UpdateItem — for fleet-normal
            stubber.add_response(
                "update_item",
                {"ResponseMetadata": {"RequestId": "r1", "HTTPStatusCode": 200, "HTTPHeaders": {}}},
                {
                    "TableName": TABLE,
                    "Key": {"fleetId": {"S": "fleet-normal"}},
                    "UpdateExpression": "SET data_source = :new_val",
                    "ConditionExpression": "attribute_exists(data_source) AND data_source IN (:o1, :o2)",
                    "ExpressionAttributeValues": {
                        ":o1":      {"S": "onboard-fwe"},
                        ":o2":      {"S": "cloud-oem1"},
                        ":new_val": {"S": "cloud-telemetry"},
                    },
                },
            )

            all_items = backfill._scan_all(ddb, TABLE)
            stats = {"scanned": len(all_items), "rewritten": 0, "already_new": 0, "no_attr": 0, "failed": 0}
            for item in all_items:
                fleet_id = item["fleetId"]["S"]
                raw_ds = item.get("data_source", {}).get("S", "")
                if not raw_ds:
                    stats["no_attr"] += 1
                    continue
                new_val = backfill.REWRITE_MAP.get(raw_ds)
                if new_val is None:
                    stats["already_new"] += 1
                    continue
                backfill._apply_update(ddb, TABLE, fleet_id, new_val, stats)

            self.assertEqual(stats["no_attr"], 1)
            self.assertEqual(stats["rewritten"], 1)

    # (e) ConditionalCheckFailedException counted as 'already_new'
    def test_conditional_check_failed_counted_as_already_new(self):
        session = boto3.Session(region_name="us-west-2")
        ddb = session.client("dynamodb")

        items = [_make_fleet("fleet-race", "cloud-oem1")]

        with Stubber(ddb) as stubber:
            stubber.add_response("scan", _make_scan_response(items), {"TableName": TABLE})
            stubber.add_client_error(
                "update_item",
                service_error_code="ConditionalCheckFailedException",
                http_status_code=400,
            )

            all_items = backfill._scan_all(ddb, TABLE)
            stats = {"scanned": len(all_items), "rewritten": 0, "already_new": 0, "no_attr": 0, "failed": 0}
            for item in all_items:
                fleet_id = item["fleetId"]["S"]
                raw_ds = item.get("data_source", {}).get("S", "")
                if not raw_ds:
                    stats["no_attr"] += 1
                    continue
                new_val = backfill.REWRITE_MAP.get(raw_ds)
                if new_val is None:
                    stats["already_new"] += 1
                    continue
                backfill._apply_update(ddb, TABLE, fleet_id, new_val, stats)

            # ConditionalCheckFailedException must be counted as already_new, NOT failed
            self.assertEqual(stats["already_new"], 1)
            self.assertEqual(stats["failed"], 0)
            self.assertEqual(stats["rewritten"], 0)

    # (f) Table name uses no region/account suffix — matches deployed reality
    def test_table_name_format(self):
        """run() must construct TableName as cms-{stage}-storage-fleets (no suffix)."""
        import unittest.mock as mock

        # Build a real boto3 Session but stub the DynamoDB client it returns
        session = boto3.Session(region_name="us-west-2")
        ddb = session.client("dynamodb")

        with Stubber(ddb) as stubber:
            # Expect scan with the correct (no-suffix) table name
            expected_table = "cms-staging-storage-fleets"
            stubber.add_response(
                "scan",
                _make_scan_response([]),
                {"TableName": expected_table},
            )

            # Patch boto3.Session inside the module so run() uses our stubbed client
            with mock.patch("backfill_data_source_enum.boto3") as mock_boto3:
                mock_session = mock.MagicMock()
                mock_boto3.Session.return_value = mock_session
                mock_session.client.return_value = ddb

                backfill.run(stage="staging", region="us-west-2", profile=None, dry_run=True)

            # Stubber validates that scan was called with exactly expected_table;
            # any unexpected TableName raises StubResponseError on __exit__


if __name__ == "__main__":
    unittest.main()
