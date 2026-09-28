#!/usr/bin/env python3
"""Inline unittest for `status` field validation on driver POST/PUT.

Added 2026-05-29 per spec
`2026-05-29-staging-drivers-simulator-cognito-parity` Task 2.5. The
main_api handler module has no pre-existing test harness; per the task
constraint we do not introduce a new test framework — this file is a
standalone unittest script runnable via:

    cd /path/to/repo && python3 modules/cms_ui/source/handlers/main_api/test_status_validation.py

Or via the standard unittest runner:

    python3 -m unittest modules.cms_ui.source.handlers.main_api.test_status_validation

The test imports the handler module directly via `importlib`, stubs out
the AWS clients and the auth helpers it depends on, then invokes the
`handler(event, context)` entrypoint with synthetic API Gateway events.

What's covered:
  - POST /api/v1/drivers WITHOUT `status` → 400
  - POST /api/v1/drivers WITH `status='inactive'` (not in enum) → 400
  - POST /api/v1/drivers WITH `status='active'` → 201 (happy path unchanged)
  - PUT  /api/v1/drivers/D1 WITHOUT `status` → not 400 on validation grounds
  - PUT  /api/v1/drivers/D1 WITH `status='terminated'` → 200 happy path

The tests stub the auth surface so they never depend on Cognito tokens.
"""
import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[5]
HANDLER_PATH = Path(__file__).resolve().parent / "index.py"


def _load_handler_module():
    """Load the main_api handler module with stubbed env + boto3.

    The real handler reads env vars and constructs DynamoDB clients at
    module-load time (harmless under `boto3` defaults but slow). The
    test patches in stubs before exec_module so the import is fast and
    deterministic.
    """
    # Set required env vars BEFORE import — many module-level constants
    # are read directly from os.environ.
    os.environ.setdefault("DRIVERS_TABLE_NAME", "test-drivers")
    os.environ.setdefault("VEHICLES_TABLE_NAME", "test-vehicles")
    os.environ.setdefault("FLEETS_TABLE_NAME", "test-fleets")
    os.environ.setdefault("SAFETY_EVENTS_TABLE_NAME", "test-safety-events")
    os.environ.setdefault("DASHBOARD_METRICS_CACHE_TABLE", "test-dashboard-metrics")
    os.environ.setdefault("SERVICE_HISTORY_TABLE_NAME", "test-service-history")
    os.environ.setdefault("AWS_REGION", "us-west-2")
    os.environ.setdefault("CMS_USER_POOL_ID", "us-west-2_test")
    os.environ.setdefault("VSA_USER_POOL_ID", "us-west-2_EXAMPLE")

    spec = importlib.util.spec_from_file_location("main_api_handler", str(HANDLER_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StatusValidationTests(unittest.TestCase):
    """Black-box validation tests against the handler's POST/PUT routes."""

    @classmethod
    def setUpClass(cls):
        # Boto3 dependency is heavy at import time. The patches below replace
        # the dynamodb resource the handler holds at module scope so calls
        # don't hit AWS.
        cls.module = _load_handler_module()

    def _stub_drivers_table(self):
        """Return a fake table that records calls without writing to AWS."""
        table = mock.MagicMock()
        # POST happy-path goes through put_item; PUT happy-path goes
        # through update_item; both should succeed silently.
        table.put_item.return_value = {}
        table.update_item.return_value = {"Attributes": {"driverId": "D1", "status": "terminated"}}
        # PUT also calls get_item to confirm the driver exists.
        table.get_item.return_value = {"Item": {"driverId": "D1", "status": "active"}}
        # Vehicle ownership scan returns no displaced holders for these tests.
        table.scan.return_value = {"Items": []}
        return table

    def _admin_event(self, method: str, path: str, body: dict) -> dict:
        """Construct a minimal API Gateway event with an admin Cognito identity.

        The handler's `_deny_viewer` and `_check_fleet_access` checks read
        from the requestContext claims; an admin identity bypasses both.
        """
        return {
            "httpMethod": method,
            "path": path,
            "headers": {"content-type": "application/json"},
            "body": json.dumps(body),
            "queryStringParameters": None,
            "requestContext": {
                "authorizer": {
                    "claims": {
                        "cognito:groups": "admin",
                        "sub": "test-admin",
                    }
                }
            },
        }

    def _invoke(self, event):
        """Invoke handler with patched dynamodb resource."""
        with mock.patch.object(self.module, "dynamodb") as ddb_mock:
            ddb_mock.Table.return_value = self._stub_drivers_table()
            return self.module.handler(event, None)

    def test_post_missing_status_returns_400(self):
        event = self._admin_event(
            "POST",
            "/api/v1/drivers",
            {"firstName": "Test", "lastName": "User", "email": "t@example.com",
             "licenseNumber": "DL-X", "fleetId": ""},
        )
        resp = self._invoke(event)
        self.assertEqual(resp["statusCode"], 400, msg=f"got {resp}")
        self.assertIn("status field is required", resp["body"])

    def test_post_invalid_status_returns_400(self):
        event = self._admin_event(
            "POST",
            "/api/v1/drivers",
            {"firstName": "Test", "lastName": "User", "email": "t@example.com",
             "licenseNumber": "DL-X", "fleetId": "", "status": "inactive"},
        )
        resp = self._invoke(event)
        self.assertEqual(resp["statusCode"], 400, msg=f"got {resp}")
        self.assertIn("active|on_leave|terminated", resp["body"])

    def test_post_valid_status_returns_201(self):
        event = self._admin_event(
            "POST",
            "/api/v1/drivers",
            {"firstName": "Test", "lastName": "User", "email": "t@example.com",
             "licenseNumber": "DL-X", "fleetId": "", "status": "active"},
        )
        resp = self._invoke(event)
        self.assertEqual(resp["statusCode"], 201, msg=f"got {resp}")

    def test_put_missing_status_does_not_validate_status(self):
        # Should NOT 400 due to missing status — that's the spec's
        # only-if-present rule. Other failures (e.g. 500 from a deep
        # downstream code path) are acceptable for this assertion;
        # we're proving validation does not fire on missing status.
        event = self._admin_event(
            "PUT",
            "/api/v1/drivers/D1",
            {"firstName": "Test"},
        )
        resp = self._invoke(event)
        self.assertNotEqual(
            resp["statusCode"], 400,
            msg=f"PUT without status field should not 400 on validation; got {resp}",
        )

    def test_put_valid_status_returns_200(self):
        event = self._admin_event(
            "PUT",
            "/api/v1/drivers/D1",
            {"status": "terminated"},
        )
        resp = self._invoke(event)
        self.assertEqual(resp["statusCode"], 200, msg=f"got {resp}")

    def test_put_invalid_status_returns_400(self):
        event = self._admin_event(
            "PUT",
            "/api/v1/drivers/D1",
            {"status": "inactive"},
        )
        resp = self._invoke(event)
        self.assertEqual(resp["statusCode"], 400, msg=f"got {resp}")
        self.assertIn("active|on_leave|terminated", resp["body"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
