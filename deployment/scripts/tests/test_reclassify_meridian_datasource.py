"""Tests for reclassify_meridian_datasource.py

Uses botocore.stub.Stubber to mock DynamoDB calls; no network access.

Test coverage:
  1. Dry-run with 3 Meridian vehicles -> prints 3 planned updates, DDB not called for writes.
  2. Real run with 3 vehicles, none previously cloud-telemetry -> 3 update_item calls, all succeed.
  3. Real run where 1 vehicle already cloud-telemetry -> only 2 update_item calls (idempotent skip).
  4. Real run with 1 update failure -> script continues, reports error in summary.
"""
import io
import sys
import os
import unittest
import unittest.mock as mock

import boto3
from botocore.stub import Stubber

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import reclassify_meridian_datasource as reclassify  # noqa: E402

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

TABLE = "cms-staging-storage-vehicles"
TARGET_VALUE = "cloud-telemetry"

# DynamoDB FilterExpression used by _scan_meridian_vehicles —
# must match the kwargs the stubber sees.
_SCAN_KWARGS = {
    "TableName": TABLE,
    "FilterExpression": "#mk = :meridian",
    "ExpressionAttributeNames": {"#mk": "make"},
    "ExpressionAttributeValues": {":meridian": {"S": "Meridian"}},
}


def _make_vehicle(vehicle_id, datasource=None):
    item = {
        "vehicleId": {"S": vehicle_id},
        "make": {"S": "Meridian"},
    }
    if datasource is not None:
        item["dataSource"] = {"S": datasource}
    return item


def _scan_response(items):
    return {
        "Items": items,
        "Count": len(items),
        "ScannedCount": len(items),
        "ResponseMetadata": {"RequestId": "x", "HTTPStatusCode": 200, "HTTPHeaders": {}},
    }


def _update_ok():
    return {
        "ResponseMetadata": {"RequestId": "u", "HTTPStatusCode": 200, "HTTPHeaders": {}}
    }


def _update_kwargs(vehicle_id, new_value):
    return {
        "TableName": TABLE,
        "Key": {"vehicleId": {"S": vehicle_id}},
        "UpdateExpression": "SET dataSource = :new_val",
        "ConditionExpression": "dataSource <> :new_val OR attribute_not_exists(dataSource)",
        "ExpressionAttributeValues": {":new_val": {"S": new_value}},
    }


# ──────────────────────────────────────────────────────────────────────────────
# Test cases
# ──────────────────────────────────────────────────────────────────────────────

class TestReclassifyMeridianDatasource(unittest.TestCase):

    # ──────────────────────────────────────────────────────────────────────────
    # Test 1: dry-run — 3 Meridian vehicles, DDB NOT called for writes
    # ──────────────────────────────────────────────────────────────────────────
    def test_dry_run_prints_planned_updates_no_ddb_writes(self):
        """Dry-run: 3 vehicles, all need updating -> 3 planned, 0 DDB writes."""
        items = [
            _make_vehicle("VEH-MRDN-0001", "vehicle-telemetry"),
            _make_vehicle("VEH-MRDN-0002", "vehicle-telemetry"),
            _make_vehicle("VEH-MRDN-0003"),  # no dataSource attribute
        ]

        session = boto3.Session(region_name="us-west-2")
        ddb = session.client("dynamodb")

        with Stubber(ddb) as stubber:
            # Only a single scan — NO update_item registered.
            # Stubber raises an error on any unexpected call, so a write would fail.
            stubber.add_response("scan", _scan_response(items), _SCAN_KWARGS)

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                with mock.patch("reclassify_meridian_datasource.boto3") as mock_boto3:
                    mock_session = mock.MagicMock()
                    mock_boto3.Session.return_value = mock_session
                    mock_session.client.return_value = ddb

                    rc = reclassify.run(
                        stage="staging",
                        region="us-west-2",
                        profile=None,
                        dry_run=True,
                        datasource_value=TARGET_VALUE,
                    )

        output = captured.getvalue()
        self.assertEqual(rc, 0)

        # All 3 vehicles should appear as [planned]
        self.assertIn("[planned] VEH-MRDN-0001", output)
        self.assertIn("[planned] VEH-MRDN-0002", output)
        self.assertIn("[planned] VEH-MRDN-0003", output)

        # Summary line must mention 3 planned updates
        self.assertIn("3 update(s) planned", output)

        # Stubber's __exit__ validates no unexpected calls were made — i.e., no writes.

    # ──────────────────────────────────────────────────────────────────────────
    # Test 2: real run, 3 vehicles, none previously cloud-telemetry -> 3 updates
    # ──────────────────────────────────────────────────────────────────────────
    def test_real_run_all_vehicles_need_update(self):
        """Real run: 3 vehicles with 'vehicle-telemetry' -> 3 update_item calls, all succeed."""
        items = [
            _make_vehicle("VEH-MRDN-0001", "vehicle-telemetry"),
            _make_vehicle("VEH-MRDN-0002", "vehicle-telemetry"),
            _make_vehicle("VEH-MRDN-0003", "vehicle-telemetry"),
        ]

        session = boto3.Session(region_name="us-west-2")
        ddb = session.client("dynamodb")

        with Stubber(ddb) as stubber:
            stubber.add_response("scan", _scan_response(items), _SCAN_KWARGS)
            stubber.add_response("update_item", _update_ok(), _update_kwargs("VEH-MRDN-0001", TARGET_VALUE))
            stubber.add_response("update_item", _update_ok(), _update_kwargs("VEH-MRDN-0002", TARGET_VALUE))
            stubber.add_response("update_item", _update_ok(), _update_kwargs("VEH-MRDN-0003", TARGET_VALUE))

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                with mock.patch("reclassify_meridian_datasource.boto3") as mock_boto3:
                    mock_session = mock.MagicMock()
                    mock_boto3.Session.return_value = mock_session
                    mock_session.client.return_value = ddb

                    rc = reclassify.run(
                        stage="staging",
                        region="us-west-2",
                        profile=None,
                        dry_run=False,
                        datasource_value=TARGET_VALUE,
                    )

        output = captured.getvalue()
        self.assertEqual(rc, 0)

        # All three vehicles should be marked as updated
        self.assertIn("[updated] VEH-MRDN-0001", output)
        self.assertIn("[updated] VEH-MRDN-0002", output)
        self.assertIn("[updated] VEH-MRDN-0003", output)

        # Summary counts
        self.assertIn("updated=3", output)
        self.assertIn("already_correct=0", output)
        self.assertIn("errors=0", output)

        # Stubber verifies all 3 update_item calls were made (no unexpected extras).

    # ──────────────────────────────────────────────────────────────────────────
    # Test 3: real run, 1 vehicle already cloud-telemetry -> 2 update_item calls only
    # ──────────────────────────────────────────────────────────────────────────
    def test_real_run_one_already_correct_skipped(self):
        """Real run: 1 of 3 vehicles already has cloud-telemetry -> only 2 update_item calls."""
        items = [
            _make_vehicle("VEH-MRDN-0001", "vehicle-telemetry"),
            _make_vehicle("VEH-MRDN-0002", TARGET_VALUE),  # already correct
            _make_vehicle("VEH-MRDN-0003", "onboard-fwe"),
        ]

        session = boto3.Session(region_name="us-west-2")
        ddb = session.client("dynamodb")

        with Stubber(ddb) as stubber:
            stubber.add_response("scan", _scan_response(items), _SCAN_KWARGS)
            # Only 2 updates — VEH-MRDN-0002 must be skipped in the cheap path
            stubber.add_response("update_item", _update_ok(), _update_kwargs("VEH-MRDN-0001", TARGET_VALUE))
            stubber.add_response("update_item", _update_ok(), _update_kwargs("VEH-MRDN-0003", TARGET_VALUE))
            # If a 3rd update_item is called, Stubber raises StubAssertionError.

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                with mock.patch("reclassify_meridian_datasource.boto3") as mock_boto3:
                    mock_session = mock.MagicMock()
                    mock_boto3.Session.return_value = mock_session
                    mock_session.client.return_value = ddb

                    rc = reclassify.run(
                        stage="staging",
                        region="us-west-2",
                        profile=None,
                        dry_run=False,
                        datasource_value=TARGET_VALUE,
                    )

        output = captured.getvalue()
        self.assertEqual(rc, 0)

        self.assertIn("[updated] VEH-MRDN-0001", output)
        self.assertIn("[skip]    VEH-MRDN-0002", output)
        self.assertIn("[updated] VEH-MRDN-0003", output)

        self.assertIn("updated=2", output)
        self.assertIn("already_correct=1", output)
        self.assertIn("errors=0", output)

    # ──────────────────────────────────────────────────────────────────────────
    # Test 4: real run, 1 update failure -> script continues, errors reported
    # ──────────────────────────────────────────────────────────────────────────
    def test_real_run_one_failure_continues_and_reports_error(self):
        """Real run: 1 update_item raises a non-conditional error -> continues, rc=1."""
        items = [
            _make_vehicle("VEH-MRDN-0001", "vehicle-telemetry"),
            _make_vehicle("VEH-MRDN-0002", "vehicle-telemetry"),
        ]

        session = boto3.Session(region_name="us-west-2")
        ddb = session.client("dynamodb")

        with Stubber(ddb) as stubber:
            stubber.add_response("scan", _scan_response(items), _SCAN_KWARGS)
            # First update succeeds.
            stubber.add_response("update_item", _update_ok(), _update_kwargs("VEH-MRDN-0001", TARGET_VALUE))
            # Second update fails with a ProvisionedThroughputExceededException.
            stubber.add_client_error(
                "update_item",
                service_error_code="ProvisionedThroughputExceededException",
                http_status_code=400,
                expected_params=_update_kwargs("VEH-MRDN-0002", TARGET_VALUE),
            )

            captured_out = io.StringIO()
            captured_err = io.StringIO()
            with mock.patch("sys.stdout", captured_out), mock.patch("sys.stderr", captured_err):
                with mock.patch("reclassify_meridian_datasource.boto3") as mock_boto3:
                    mock_session = mock.MagicMock()
                    mock_boto3.Session.return_value = mock_session
                    mock_session.client.return_value = ddb

                    rc = reclassify.run(
                        stage="staging",
                        region="us-west-2",
                        profile=None,
                        dry_run=False,
                        datasource_value=TARGET_VALUE,
                    )

        output = captured_out.getvalue()
        stderr = captured_err.getvalue()

        # Script returns non-zero on errors
        self.assertEqual(rc, 1)

        # First vehicle succeeded
        self.assertIn("[updated] VEH-MRDN-0001", output)

        # Second vehicle failure reported on stderr
        self.assertIn("VEH-MRDN-0002", stderr)

        # Summary shows 1 error, 1 success
        self.assertIn("updated=1", output)
        self.assertIn("errors=1", output)


if __name__ == "__main__":
    unittest.main()
