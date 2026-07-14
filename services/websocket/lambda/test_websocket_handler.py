"""
Unit tests for websocket_handler.py.

Covers:
  - $connect with authorizer context (fleet membership check)
  - $connect anonymous fallback (no authorizer context)
  - $disconnect
  - $default
  - missing fleetId → 400
  - non-admin user accessing foreign fleet → 403
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Stub out boto3 before importing the handler so no real DDB calls are made.
mock_table = MagicMock()
mock_dynamodb = MagicMock()
mock_dynamodb.Table.return_value = mock_table

with patch.dict("os.environ", {"WS_CONNECTIONS_TABLE": "test-table"}):
    with patch("boto3.resource", return_value=mock_dynamodb):
        import websocket_handler as handler_module


def _connect_event(
    connection_id: str = "conn-abc",
    fleet_id: str = "fleet1",
    auth_ctx: dict | None = None,
) -> dict:
    event: dict = {
        "requestContext": {
            "routeKey": "$connect",
            "connectionId": connection_id,
        },
        "queryStringParameters": {"fleetId": fleet_id},
    }
    if auth_ctx is not None:
        event["requestContext"]["authorizer"] = auth_ctx
    return event


class TestConnectWithAuthContext(unittest.TestCase):
    def setUp(self):
        mock_table.reset_mock()

    def test_authorised_user_own_fleet(self):
        auth_ctx = {
            "sub": "user-sub-123",
            "cognito:groups": "fleet-operator",
            "custom:fleetIds": "fleet1,fleet2",
        }
        resp = handler_module.handler(_connect_event(auth_ctx=auth_ctx), None)
        self.assertEqual(resp["statusCode"], 200)
        mock_table.put_item.assert_called_once()
        item = mock_table.put_item.call_args[1]["Item"]
        self.assertEqual(item["userId"], "user-sub-123")
        self.assertEqual(item["fleetId"], "fleet1")

    def test_platform_admin_can_access_any_fleet(self):
        auth_ctx = {
            "sub": "admin-sub",
            "cognito:groups": "platform-admin",
            "custom:fleetIds": "",
        }
        resp = handler_module.handler(_connect_event(auth_ctx=auth_ctx), None)
        self.assertEqual(resp["statusCode"], 200)

    def test_non_admin_foreign_fleet_denied(self):
        auth_ctx = {
            "sub": "user-sub-xyz",
            "cognito:groups": "fleet-operator",
            "custom:fleetIds": "fleet99",
        }
        resp = handler_module.handler(_connect_event(auth_ctx=auth_ctx), None)
        self.assertEqual(resp["statusCode"], 403)
        mock_table.put_item.assert_not_called()

    def test_anonymous_fallback_no_auth_context(self):
        """When authorizer is absent (opt-in anonymous mode), connect with no userId."""
        resp = handler_module.handler(_connect_event(auth_ctx=None), None)
        self.assertEqual(resp["statusCode"], 200)
        mock_table.put_item.assert_called_once()
        item = mock_table.put_item.call_args[1]["Item"]
        self.assertNotIn("userId", item)


class TestOtherRoutes(unittest.TestCase):
    def setUp(self):
        mock_table.reset_mock()

    def test_connect_missing_fleet_id(self):
        event = {
            "requestContext": {"routeKey": "$connect", "connectionId": "conn-1"},
            "queryStringParameters": {},
        }
        resp = handler_module.handler(event, None)
        self.assertEqual(resp["statusCode"], 400)

    def test_disconnect(self):
        event = {
            "requestContext": {"routeKey": "$disconnect", "connectionId": "conn-1"},
        }
        resp = handler_module.handler(event, None)
        self.assertEqual(resp["statusCode"], 200)
        mock_table.delete_item.assert_called_once_with(Key={"connectionId": "conn-1"})

    def test_default_route(self):
        event = {"requestContext": {"routeKey": "$default", "connectionId": "conn-1"}}
        resp = handler_module.handler(event, None)
        self.assertEqual(resp["statusCode"], 200)


class TestAllFleetAdmin(unittest.TestCase):
    """Option 2 (spec 2026-06-16-cms-ui-realtime-websocket-wiring)."""

    def setUp(self):
        mock_table.reset_mock()

    def test_admin_no_fleet_connects_all_fleet(self):
        event = {
            "requestContext": {
                "routeKey": "$connect",
                "connectionId": "conn-admin",
                "authorizer": {
                    "sub": "admin-sub",
                    "cognito:groups": "platform-admin",
                    "custom:fleetIds": "",
                },
            },
            "queryStringParameters": {},
        }
        resp = handler_module.handler(event, None)
        self.assertEqual(resp["statusCode"], 200)
        item = mock_table.put_item.call_args[1]["Item"]
        self.assertEqual(item["fleetId"], "*")
        self.assertTrue(item.get("isAdmin"))

    def test_non_admin_all_fleet_denied(self):
        event = {
            "requestContext": {
                "routeKey": "$connect",
                "connectionId": "conn-x",
                "authorizer": {
                    "sub": "u",
                    "cognito:groups": "fleet-operator",
                    "custom:fleetIds": "fleet1",
                },
            },
            "queryStringParameters": {"fleetId": "*"},
        }
        resp = handler_module.handler(event, None)
        self.assertEqual(resp["statusCode"], 403)
        mock_table.put_item.assert_not_called()


if __name__ == "__main__":
    unittest.main()
