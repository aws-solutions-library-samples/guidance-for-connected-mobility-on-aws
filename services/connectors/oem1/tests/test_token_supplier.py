"""
Test skeletons for token_supplier.py (RED phase — B1.1).

Encodes behaviors from spec § Constraints "Authentication and credential handling":
- 28-min in-process cache (expires_in - 120s proactive refresh)
- 401 → fetch + retry once
- Reads from Secrets Manager secret name `cms-staging-connector-oem1-credentials`
- In-process-only cache (no disk/DDB/Redis)

Tests import TokenSupplier inside each test body; pytest collects them all
but every test FAILS (ImportError) until token_supplier.py lands in B1.2.
"""
import sys
from pathlib import Path

import pytest

_OEM1_DIR = Path(__file__).parent.parent
if str(_OEM1_DIR) not in sys.path:
    sys.path.insert(0, str(_OEM1_DIR))

SECRET_NAME = "cms-staging-connector-oem1-credentials"

# Synthetic credential payload — never use real-looking tokens here.
FAKE_SECRET = {
    "client_id": "test-client-id",
    "client_secret": "test-client-secret",
    "token_endpoint": "https://login.example.com/token",
    "resource_id": "00000000-0000-0000-0000-000000000001",
}

FAKE_TOKEN_RESPONSE = {
    "access_token": "fake-access-token-value",
    "token_type": "Bearer",
    "expires_in": 1800,  # 30-min TTL → proactive refresh at 1680s (28 min)
}


# ---------------------------------------------------------------------------
# Cache-hit: second call within TTL must NOT call the token endpoint again
# ---------------------------------------------------------------------------

def test_cache_hit_does_not_refetch(monkeypatch):
    """Second call within the cache window returns the cached token without HTTP."""
    from token_supplier import TokenSupplier

    call_count = {"n": 0}

    def mock_fetch(_self):
        call_count["n"] += 1
        return FAKE_TOKEN_RESPONSE.copy()

    monkeypatch.setattr(TokenSupplier, "_fetch_token", mock_fetch)
    monkeypatch.setattr(TokenSupplier, "_get_secret", lambda _self: FAKE_SECRET.copy())

    supplier = TokenSupplier(secret_name=SECRET_NAME)
    t1 = supplier.get_token()
    t2 = supplier.get_token()

    assert t1 == t2
    assert call_count["n"] == 1, "Token endpoint must only be called once on cache hit"


# ---------------------------------------------------------------------------
# Cache-miss: first call always fetches
# ---------------------------------------------------------------------------

def test_cache_miss_fetches_token(monkeypatch):
    """First call always fetches from the token endpoint."""
    from token_supplier import TokenSupplier

    call_count = {"n": 0}

    def mock_fetch(_self):
        call_count["n"] += 1
        return FAKE_TOKEN_RESPONSE.copy()

    monkeypatch.setattr(TokenSupplier, "_fetch_token", mock_fetch)
    monkeypatch.setattr(TokenSupplier, "_get_secret", lambda _self: FAKE_SECRET.copy())

    supplier = TokenSupplier(secret_name=SECRET_NAME)
    token = supplier.get_token()

    assert token == FAKE_TOKEN_RESPONSE["access_token"]
    assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# Proactive refresh: token is refreshed at expires_in - 120 seconds
# ---------------------------------------------------------------------------

def test_proactive_refresh_at_expires_in_minus_120(monkeypatch):
    """Token is proactively refreshed when remaining TTL <= 120 seconds."""
    import time
    from token_supplier import TokenSupplier

    tokens = ["first-token", "second-token"]
    call_count = {"n": 0}

    def mock_fetch(_self):
        t = tokens[min(call_count["n"], len(tokens) - 1)]
        call_count["n"] += 1
        return {"access_token": t, "token_type": "Bearer", "expires_in": 1800}

    monkeypatch.setattr(TokenSupplier, "_fetch_token", mock_fetch)
    monkeypatch.setattr(TokenSupplier, "_get_secret", lambda _self: FAKE_SECRET.copy())

    supplier = TokenSupplier(secret_name=SECRET_NAME)
    supplier.get_token()  # first fetch

    # Force the cached token to appear nearly expired (119s remaining < 120s threshold)
    supplier._cached_token_expiry = time.monotonic() + 119

    token2 = supplier.get_token()

    assert call_count["n"] == 2, "Should have proactively refreshed"
    assert token2 == "second-token"


# ---------------------------------------------------------------------------
# 401 handling: fetch + retry exactly once
# ---------------------------------------------------------------------------

def test_401_triggers_fetch_and_retry_once(monkeypatch):
    """On 401, supplier fetches a new token and retries; does NOT retry more than once."""
    from token_supplier import TokenSupplier

    call_count = {"n": 0}

    def mock_fetch(_self):
        call_count["n"] += 1
        return FAKE_TOKEN_RESPONSE.copy()

    monkeypatch.setattr(TokenSupplier, "_fetch_token", mock_fetch)
    monkeypatch.setattr(TokenSupplier, "_get_secret", lambda _self: FAKE_SECRET.copy())

    supplier = TokenSupplier(secret_name=SECRET_NAME)
    supplier.get_token()  # prime the cache

    new_token = supplier.handle_401()

    assert call_count["n"] == 2, "Exactly one re-fetch after 401"
    assert new_token == FAKE_TOKEN_RESPONSE["access_token"]


def test_401_does_not_retry_infinitely(monkeypatch):
    """handle_401 returns the newly fetched token without looping."""
    from token_supplier import TokenSupplier

    fetches = {"n": 0}

    def mock_fetch(_self):
        fetches["n"] += 1
        return FAKE_TOKEN_RESPONSE.copy()

    monkeypatch.setattr(TokenSupplier, "_fetch_token", mock_fetch)
    monkeypatch.setattr(TokenSupplier, "_get_secret", lambda _self: FAKE_SECRET.copy())

    supplier = TokenSupplier(secret_name=SECRET_NAME)
    supplier.get_token()
    supplier.handle_401()

    # Total fetches = 1 (initial) + 1 (after 401) = 2
    assert fetches["n"] == 2


# ---------------------------------------------------------------------------
# Secrets Manager scoping: reads from the exact secret name
# ---------------------------------------------------------------------------

def test_reads_from_correct_secret_name(monkeypatch):
    """TokenSupplier reads credentials from the expected Secrets Manager secret name."""
    from token_supplier import TokenSupplier

    read_names = []

    def mock_get_secret(_self):
        read_names.append(_self._secret_name)
        return FAKE_SECRET.copy()

    monkeypatch.setattr(TokenSupplier, "_get_secret", mock_get_secret)
    monkeypatch.setattr(TokenSupplier, "_fetch_token", lambda _self: FAKE_TOKEN_RESPONSE.copy())

    supplier = TokenSupplier(secret_name=SECRET_NAME)
    supplier.get_token()

    assert SECRET_NAME in read_names


# ---------------------------------------------------------------------------
# In-process-only cache: cache is stored in instance attributes, not files/DDB
# ---------------------------------------------------------------------------

def test_cache_is_in_process_only(monkeypatch):
    """Cached token lives on the instance; no disk, DDB, or Redis calls."""
    from token_supplier import TokenSupplier

    monkeypatch.setattr(TokenSupplier, "_fetch_token", lambda _self: FAKE_TOKEN_RESPONSE.copy())
    monkeypatch.setattr(TokenSupplier, "_get_secret", lambda _self: FAKE_SECRET.copy())

    supplier = TokenSupplier(secret_name=SECRET_NAME)
    supplier.get_token()

    # Cache must live on the instance object itself
    assert hasattr(supplier, "_cached_token") or hasattr(supplier, "_access_token"), (
        "Cached token must be stored as an instance attribute"
    )

    # A second independent instance must not share the cache
    supplier2 = TokenSupplier(secret_name=SECRET_NAME)
    is_cold = (
        getattr(supplier2, "_cached_token", None) is None
        and getattr(supplier2, "_access_token", None) is None
    )
    assert is_cold, "New TokenSupplier instance must start with an empty cache"
