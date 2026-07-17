"""
Unit tests for ws_authorizer.py.

Covers:
  (a) missing token → Unauthorized
  (b) invalid signature → Unauthorized
  (c) expired token → Unauthorized
  (d) wrong audience → Unauthorized
  (e) valid token → Allow policy with correct ARN + principalId
"""
import importlib
import json
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# Generate a self-signed RSA key pair for test fixture tokens.
_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_RSA_PRIVATE_PEM = _RSA_KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
_RSA_PUBLIC_KEY = _RSA_KEY.public_key()

REGION = "us-west-2"
POOL_ID = "us-west-2_TestPool123"
CLIENT_ID = "testclientid123"
EXPECTED_ISS = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}"
METHOD_ARN = f"arn:aws:execute-api:{REGION}:123456789012:abcdef/live/$connect"


def _make_token(
    sub: str = "user-sub-abc",
    groups: list | None = None,
    fleet_ids: str = "fleet1,fleet2",
    audience: str = CLIENT_ID,
    issuer: str = EXPECTED_ISS,
    exp_offset: int = 3600,
    key=_RSA_KEY,
) -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + exp_offset,
        "cognito:groups": groups or ["fleet-operator"],
        "custom:fleetIds": fleet_ids,
    }
    return jwt.encode(payload, key, algorithm="RS256")


def _signing_key_mock(token: str):
    """Return a mock signing key that wraps the test RSA public key."""
    mock_key = MagicMock()
    mock_key.key = _RSA_PUBLIC_KEY
    return mock_key


def _make_event(token: str | None, method_arn: str = METHOD_ARN) -> dict:
    event: dict = {"methodArn": method_arn}
    if token is not None:
        event["queryStringParameters"] = {"token": token}
    return event


class TestWSAuthorizer(unittest.TestCase):
    def _load_module(self):
        """Import ws_authorizer with env vars patched."""
        with patch.dict(
            "os.environ",
            {
                "AWS_REGION": REGION,
                "USER_POOL_ID": POOL_ID,
                "USER_POOL_CLIENT_ID": CLIENT_ID,
            },
        ):
            # Force re-import to pick up env vars and create fresh _jwk_client.
            if "ws_authorizer" in sys.modules:
                del sys.modules["ws_authorizer"]
            mod = importlib.import_module("ws_authorizer")
            return mod

    def setUp(self):
        self.mod = self._load_module()

    def _patch_jwks(self):
        return patch.object(self.mod._jwk_client, "get_signing_key_from_jwt", side_effect=_signing_key_mock)

    # (a) Missing token
    def test_missing_token_raises_unauthorized(self):
        with self.assertRaises(Exception) as ctx:
            self.mod.handler(_make_event(None), None)
        self.assertEqual(str(ctx.exception), "Unauthorized")

    # (b) Invalid signature
    def test_invalid_signature_raises_unauthorized(self):
        # Tamper with a valid token to break signature
        token = _make_token()
        parts = token.split(".")
        parts[2] = "invalidsignature"
        bad_token = ".".join(parts)
        with self._patch_jwks():
            with self.assertRaises(Exception) as ctx:
                self.mod.handler(_make_event(bad_token), None)
        self.assertEqual(str(ctx.exception), "Unauthorized")

    # (c) Expired token
    def test_expired_token_raises_unauthorized(self):
        token = _make_token(exp_offset=-3600)  # expired 1h ago
        with self._patch_jwks():
            with self.assertRaises(Exception) as ctx:
                self.mod.handler(_make_event(token), None)
        self.assertEqual(str(ctx.exception), "Unauthorized")

    # (d) Wrong audience
    def test_wrong_audience_raises_unauthorized(self):
        token = _make_token(audience="wrong-client-id")
        with self._patch_jwks():
            with self.assertRaises(Exception) as ctx:
                self.mod.handler(_make_event(token), None)
        self.assertEqual(str(ctx.exception), "Unauthorized")

    # (e) Valid token → Allow policy
    def test_valid_token_returns_allow_policy(self):
        token = _make_token(
            sub="user-sub-xyz",
            groups=["fleet-operator"],
            fleet_ids="fleet1,fleet2",
        )
        with self._patch_jwks():
            result = self.mod.handler(_make_event(token), None)

        self.assertEqual(result["principalId"], "user-sub-xyz")
        stmt = result["policyDocument"]["Statement"][0]
        self.assertEqual(stmt["Effect"], "Allow")
        self.assertEqual(stmt["Action"], "execute-api:Invoke")
        self.assertEqual(stmt["Resource"], METHOD_ARN)
        ctx = result["context"]
        self.assertEqual(ctx["sub"], "user-sub-xyz")
        self.assertIn("fleet-operator", ctx["cognito:groups"])
        self.assertEqual(ctx["custom:fleetIds"], "fleet1,fleet2")


class TestPerReasonDenyLogging(unittest.TestCase):
    """Regression tests for issue 2026-07-16-prod-ws-connect-unauthorized-live-user.

    Prior to 2026-07-16 every rejection path went through a single bare
    ``raise Exception("Unauthorized")`` with no CloudWatch signal. Now each
    reason emits a distinct ``[WS-AUTH][DENY] reason=<slug>`` log line
    BEFORE the raise so operators can distinguish JWKS-fetch failures from
    audience mismatches from expirations without a rebuild.

    We assert on the log-line reason slug so a future refactor cannot silently
    collapse the taxonomy again.
    """

    def _load_module(self):
        with patch.dict(
            "os.environ",
            {
                "AWS_REGION": REGION,
                "USER_POOL_ID": POOL_ID,
                "USER_POOL_CLIENT_ID": CLIENT_ID,
            },
        ):
            if "ws_authorizer" in sys.modules:
                del sys.modules["ws_authorizer"]
            mod = importlib.import_module("ws_authorizer")
            return mod

    def setUp(self):
        self.mod = self._load_module()

    def _patch_jwks(self):
        return patch.object(
            self.mod._jwk_client,
            "get_signing_key_from_jwt",
            side_effect=_signing_key_mock,
        )

    def _assert_deny_with_reason(self, event, expected_reason: str, jwks_patched=True):
        cm = self.assertLogs(self.mod.logger, level="INFO")
        ctx = self._patch_jwks() if jwks_patched else patch.object(
            self.mod._jwk_client,
            "get_signing_key_from_jwt",
            side_effect=Exception("simulated jwks failure"),
        )
        with cm as log_ctx, ctx:
            with self.assertRaises(Exception) as raise_ctx:
                self.mod.handler(event, None)
        self.assertEqual(str(raise_ctx.exception), "Unauthorized")
        combined = "\n".join(log_ctx.output)
        self.assertIn(f"reason={expected_reason}", combined)

    def test_deny_reason_missing_token(self):
        self._assert_deny_with_reason(
            _make_event(None), "missing_token", jwks_patched=False
        )

    def test_deny_reason_jwks_signing_key_failure(self):
        # Any token payload — the JWKS lookup itself is being made to fail.
        token = _make_token()
        self._assert_deny_with_reason(
            _make_event(token), "jwks_signing_key_failure", jwks_patched=False
        )

    def test_deny_reason_token_expired(self):
        token = _make_token(exp_offset=-3600)
        self._assert_deny_with_reason(_make_event(token), "token_expired")

    def test_deny_reason_audience_mismatch(self):
        token = _make_token(audience="wrong-client-id")
        self._assert_deny_with_reason(_make_event(token), "audience_mismatch")

    def test_deny_reason_signature_invalid(self):
        # Tampered signature raises jwt.InvalidSignatureError inside decode
        token = _make_token()
        parts = token.split(".")
        parts[2] = "invalidsignature"
        bad_token = ".".join(parts)
        self._assert_deny_with_reason(_make_event(bad_token), "signature_invalid")

    def test_deny_reason_issuer_mismatch(self):
        token = _make_token(issuer="https://cognito-idp.us-west-2.amazonaws.com/other-pool")
        self._assert_deny_with_reason(_make_event(token), "issuer_mismatch")

    def test_deny_reason_missing_method_arn(self):
        token = _make_token()
        event = _make_event(token)
        del event["methodArn"]
        self._assert_deny_with_reason(event, "missing_method_arn")

    def test_allow_path_logs_allow_line(self):
        """Successful auth emits an ``[WS-AUTH][ALLOW]`` line so operators
        can measure success rate + see admin-shape logins."""
        token = _make_token(sub="admin-sub", groups=["admin"], fleet_ids="")
        with self.assertLogs(self.mod.logger, level="INFO") as log_ctx:
            with self._patch_jwks():
                result = self.mod.handler(_make_event(token), None)
        self.assertEqual(result["principalId"], "admin-sub")
        self.assertTrue(any("[WS-AUTH][ALLOW]" in line for line in log_ctx.output))


if __name__ == "__main__":
    unittest.main()
