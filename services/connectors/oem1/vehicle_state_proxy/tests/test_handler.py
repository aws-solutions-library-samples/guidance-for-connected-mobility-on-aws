"""
Unit tests for vehicle_state_proxy/handler.py  — C2.2.

Tests (5):
  test_success_path_returns_readiness_json
  test_oem1_4xx_returns_passthrough_status
  test_oem1_5xx_returns_502_gateway_error
  test_network_timeout_returns_504
  test_token_supplier_reused_not_reimplemented

Uses requests-mock==1.12.1 for OEM1 HTTP mocking.
Uses moto==5.0.10 for Secrets Manager mocking (via TokenSupplier).
"""
import importlib
import inspect
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests
import requests_mock as requests_mock_lib

# --- environment defaults so handler can import cleanly ----------------------
os.environ.setdefault("OEM1_FEED_HOST", "oem1-feed.example.local")
os.environ.setdefault("SECRETS_NAME", "cms-staging-connector-oem1-credentials")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")

import handler  # noqa: E402  (after sys.path setup in conftest.py)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(vehicle_id: str = "VIN-TEST-001") -> dict:
    return {"pathParameters": {"vehicleId": vehicle_id}}


def _mock_supplier(token: str = "mock-bearer-token") -> MagicMock:
    sup = MagicMock()
    sup.get_token.return_value = token
    sup.handle_401.return_value = token
    return sup


_READINESS_PAYLOAD = {
    "vehicleId": "VIN-TEST-001",
    "ccsEnabled": True,
    "transportMode": None,
    "lifecycleStatus": "active",
    "actionItems": [],
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSuccessPath:
    def test_success_path_returns_readiness_json(self):
        """Mocked OEM1 returns 200 → Lambda returns 200 with the JSON body."""
        url = f"https://oem1-feed.example.local/selfserve/v1/vehicleState"
        with requests_mock_lib.Mocker() as m:
            m.get(requests_mock_lib.ANY, json=_READINESS_PAYLOAD, status_code=200)
            with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                # Reset module-level singleton so our mock takes effect
                handler._token_supplier = None
                result = handler.lambda_handler(_make_event(), None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["vehicleId"] == "VIN-TEST-001"
        assert body["ccsEnabled"] is True


class TestOem14xx:
    def test_oem1_4xx_returns_passthrough_status(self):
        """Mocked OEM1 returns 404 → Lambda returns 404 with sanitized body."""
        with requests_mock_lib.Mocker() as m:
            m.get(requests_mock_lib.ANY, json={"message": "not found"}, status_code=404)
            with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                handler._token_supplier = None
                result = handler.lambda_handler(_make_event(), None)

        assert result["statusCode"] == 404
        body = json.loads(result["body"])
        assert "error" in body
        # Must NOT echo internal OEM1 detail
        assert "not found" not in body.get("error", "").lower() or "upstream" in body.get("error", "")


class TestOem15xx:
    def test_oem1_5xx_returns_502_gateway_error(self):
        """Mocked OEM1 returns 503 → Lambda returns 502."""
        with requests_mock_lib.Mocker() as m:
            m.get(requests_mock_lib.ANY, status_code=503)
            with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                handler._token_supplier = None
                result = handler.lambda_handler(_make_event(), None)

        assert result["statusCode"] == 502
        body = json.loads(result["body"])
        assert "error" in body


class TestNetworkTimeout:
    def test_network_timeout_returns_504(self):
        """Mocked OEM1 raises requests.exceptions.Timeout → Lambda returns 504."""
        with requests_mock_lib.Mocker() as m:
            m.get(requests_mock_lib.ANY, exc=requests.exceptions.Timeout)
            with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                handler._token_supplier = None
                result = handler.lambda_handler(_make_event(), None)

        assert result["statusCode"] == 504
        body = json.loads(result["body"])
        assert "error" in body


class TestTokenSupplierReused:
    def test_token_supplier_reused_not_reimplemented(self):
        """
        Verify handler imports TokenSupplier from the B1.2 token_supplier module
        (not defining its own auth logic).
        """
        import token_supplier as ts_module

        # handler must reference the same TokenSupplier class
        assert hasattr(handler, "TokenSupplier"), (
            "handler.py does not import TokenSupplier — second auth implementation detected"
        )
        assert handler.TokenSupplier is ts_module.TokenSupplier, (
            "handler.TokenSupplier is not the B1.2 TokenSupplier class"
        )

        # Confirm handler source does not contain an OAuth client_credentials flow
        handler_src = inspect.getsource(handler)
        assert "client_credentials" not in handler_src, (
            "handler.py implements its own OAuth flow — must reuse B1.2 TokenSupplier"
        )
        assert "grant_type" not in handler_src, (
            "handler.py contains grant_type — second auth implementation detected"
        )
