"""pytest fixtures for CMS eval pipeline."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml


@pytest.fixture(scope="session")
def stage_endpoint() -> str:
    """Return STAGE_ENDPOINT or skip the test."""
    val = os.environ.get("STAGE_ENDPOINT")
    if not val:
        pytest.skip("STAGE_ENDPOINT env var not set — skipping eval test")
    return val


@pytest.fixture(scope="session")
def stage_endpoint_wss(stage_endpoint: str) -> str:
    """Return STAGE_ENDPOINT_WSS or derive from STAGE_ENDPOINT."""
    val = os.environ.get("STAGE_ENDPOINT_WSS")
    if val:
        return val
    return stage_endpoint.replace("https://", "wss://")


@pytest.fixture(scope="session")
def cms_jwt(stage_endpoint: str) -> str:
    """Return a Cognito JWT for the eval user, or skip if env vars unset.

    Required env vars: ``CMS_EVAL_USERNAME``, ``CMS_EVAL_PASSWORD``,
    ``COGNITO_CLIENT_ID``. The non-admin ``initiate_auth`` endpoint used by
    ``_auth.get_jwt`` does not require ``COGNITO_USER_POOL_ID``.
    """
    required = ("CMS_EVAL_USERNAME", "CMS_EVAL_PASSWORD", "COGNITO_CLIENT_ID")
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        pytest.skip(f"Auth env vars not set ({', '.join(missing)}) — skipping JWT-authenticated test")

    from evals.runner._auth import get_jwt_from_env
    return get_jwt_from_env(stage_endpoint)


@pytest.fixture(scope="session", autouse=True)
def _autopopulate_cms_jwt() -> None:
    """Auto-populate ``CMS_JWT`` env var from Cognito credentials.

    Tier 3 test functions read ``os.environ['CMS_JWT']`` directly rather than
    consuming the ``cms_jwt`` fixture (kept simple so the same code path runs
    under both pytest and ``_run_tier`` invocations). This autouse fixture
    fetches the JWT once per session if credentials are available, so callers
    only need to supply ``CMS_EVAL_USERNAME`` / ``CMS_EVAL_PASSWORD`` /
    ``COGNITO_*`` rather than pre-fetching a token themselves.

    No-op if ``CMS_JWT`` is already set or if the auth env vars are missing —
    individual cases that need auth will still fail loudly downstream.
    """
    if os.environ.get("CMS_JWT"):
        return
    required = ("CMS_EVAL_USERNAME", "CMS_EVAL_PASSWORD", "COGNITO_CLIENT_ID")
    if any(not os.environ.get(v) for v in required):
        return
    try:
        from evals.runner._auth import get_jwt_from_env
        os.environ["CMS_JWT"] = get_jwt_from_env(os.environ.get("STAGE_ENDPOINT", ""))
    except Exception:
        # Don't crash the entire suite on auth failure — let cases that need
        # auth fail with a meaningful per-case error instead.
        pass


@pytest.fixture(scope="session")
def case_fixtures() -> dict[str, Any]:
    """Load placeholder substitution values from evals/cases/e2e/_fixtures.yaml.

    Returns an empty dict if the file doesn't exist (created by task 2c).
    """
    fixtures_path = Path(__file__).parent / "cases" / "e2e" / "_fixtures.yaml"
    if not fixtures_path.exists():
        return {}
    with open(fixtures_path) as f:
        return yaml.safe_load(f) or {}
