"""
OEM1 TokenSupplier — OAuth 2.0 client_credentials with in-process-only cache.

Reads credentials from AWS Secrets Manager secret `cms-staging-connector-oem1-credentials`.
Caches Bearer token in process memory ONLY (no disk / DDB / Redis).
Refreshes proactively at expires_in - 120 seconds (28 min on 30-min TTL).
On 401: fetch once and return new token; does NOT recurse.
"""
import json
import os
import time
import urllib.parse
import urllib.request

import boto3

_REFRESH_BUFFER_SECONDS = 120


class TokenSupplier:
    def __init__(self, secret_name: str = "cms-staging-connector-oem1-credentials"):
        self._secret_name = secret_name
        self._cached_token: str | None = None
        self._cached_token_expiry: float = 0.0
        self._secret_cache: dict | None = None

    def get_token(self) -> str:
        # Proactive refresh: if remaining TTL <= _REFRESH_BUFFER_SECONDS, refresh now
        remaining = self._cached_token_expiry - time.monotonic()
        if self._cached_token is not None and remaining > _REFRESH_BUFFER_SECONDS:
            return self._cached_token
        return self._refresh()

    def handle_401(self) -> str:
        """Fetch a new token once after a 401 response. Does NOT recurse."""
        return self._refresh()

    def get_metadata(self) -> tuple:
        """Return gRPC metadata tuple for Bearer auth."""
        return ("authorization", f"Bearer {self.get_token()}")

    def _refresh(self) -> str:
        # Always read secret first (makes _get_secret testable independently)
        self._get_secret()
        response = self._fetch_token()
        self._cached_token = response["access_token"]
        expires_in = int(response.get("expires_in", 1800))
        self._cached_token_expiry = time.monotonic() + expires_in
        return self._cached_token

    def _get_secret(self) -> dict:
        if self._secret_cache is None:
            region = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
            client = boto3.client("secretsmanager", region_name=region)
            resp = client.get_secret_value(SecretId=self._secret_name)
            self._secret_cache = json.loads(resp["SecretString"])
        return self._secret_cache

    def _fetch_token(self) -> dict:
        secret = self._get_secret()
        data = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": secret["client_id"],
            "client_secret": secret["client_secret"],
            "resource": secret.get("resource_id", ""),
        }).encode()
        req = urllib.request.Request(secret["token_endpoint"], data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
