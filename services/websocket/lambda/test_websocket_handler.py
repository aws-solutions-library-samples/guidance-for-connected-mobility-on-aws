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


class TestAdminGroupEquivalence(unittest.TestCase):
    """Regression tests for issue 2026-07-16-prod-ws-connect-unauthorized-live-user.

    The Cognito pool has BOTH ``platform-admin`` (canonical) AND ``admin``
    (created 2026-05-07, has members). Prior to 2026-07-16 the handler only
    recognized ``platform-admin``, causing every user in ``admin`` (but not
    ``platform-admin``) to hit 400 "Missing fleetId" on the admin all-fleet
    connect path — because the frontend treats them as admin (via the
    ``@amazon.com`` email shortcut in useAuth.ts) and omits ``fleetId``, but
    the server-side handler didn't recognize the same admin authority.
    """

    def setUp(self):
        mock_table.reset_mock()

    def test_admin_group_alone_connects_all_fleet(self):
        """`admin` group (no `platform-admin`) → admin all-fleet connect."""
        event = {
            "requestContext": {
                "routeKey": "$connect",
                "connectionId": "conn-admin-only",
                "authorizer": {
                    "sub": "amzn-admin-sub",
                    "cognito:groups": "admin",
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

    def test_admin_group_plus_federate_connects_all_fleet(self):
        """Real-world case from the 2026-07-16 incident: `admin` +
        AmazonFederate-linked auto-group. Both are non-`platform-admin`;
        together the user IS admin via the `admin` membership."""
        event = {
            "requestContext": {
                "routeKey": "$connect",
                "connectionId": "conn-federate-admin",
                "authorizer": {
                    "sub": "federate-admin-sub",
                    "cognito:groups": "admin,us-east-1_bBYmSyvM5_AmazonFederate",
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

    def test_federate_only_no_admin_group_denied_missing_fleet(self):
        """Federate auto-group alone (no admin/platform-admin, no fleetIds
        claim, no ?fleetId=) → 400. Prevents a Federate-only user
        accidentally getting admin authority — admin must be granted via
        Cognito group membership, not by identity provider alone."""
        event = {
            "requestContext": {
                "routeKey": "$connect",
                "connectionId": "conn-federate-only",
                "authorizer": {
                    "sub": "federate-user-sub",
                    "cognito:groups": "us-east-1_bBYmSyvM5_AmazonFederate",
                    "custom:fleetIds": "",
                },
            },
            "queryStringParameters": {},
        }
        resp = handler_module.handler(event, None)
        self.assertEqual(resp["statusCode"], 400)
        mock_table.put_item.assert_not_called()

    def test_admin_group_all_fleet_wildcard_allowed(self):
        """`admin` group user explicitly requesting `?fleetId=*` succeeds
        (previously would 403 because only `platform-admin` was recognized)."""
        event = {
            "requestContext": {
                "routeKey": "$connect",
                "connectionId": "conn-admin-star",
                "authorizer": {
                    "sub": "admin-sub",
                    "cognito:groups": "admin",
                    "custom:fleetIds": "",
                },
            },
            "queryStringParameters": {"fleetId": "*"},
        }
        resp = handler_module.handler(event, None)
        self.assertEqual(resp["statusCode"], 200)
        item = mock_table.put_item.call_args[1]["Item"]
        self.assertEqual(item["fleetId"], "*")


if __name__ == "__main__":
    unittest.main()
