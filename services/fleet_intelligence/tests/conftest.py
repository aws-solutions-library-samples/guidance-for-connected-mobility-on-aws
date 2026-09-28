"""
conftest.py — extend sys.path so test modules can import from the repo root.

This mirrors the pattern used in services/connectors/oem1/conftest.py.
The repo root is added so 'from services.fleet_intelligence.<module> import ...'
resolves when running pytest from the repository root.
"""
import os
import sys

import pytest

# Add the repo root (four levels up from this conftest) to sys.path so that
# `from services.fleet_intelligence.provenance import ...` resolves.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "no_default_admin_claims: opt out of the conftest default-admin-claims "
        "fixture (issue 2026-09-25-fleet-intelligence-routes-trust-caller-fleet-id). "
        "Tests that specifically exercise the fleet-scope authz gate mark themselves "
        "with this so they can set their own claim shapes.",
    )


# ---------------------------------------------------------------------------
# Fleet-scope authz gate — default-admin-claims fixture for legacy tests
#
# Issue: 2026-09-25-fleet-intelligence-routes-trust-caller-fleet-id — every FI
# route now runs `_authorize_or_403` before dispatch, which reads Cognito
# claims from `event.requestContext.authorizer.claims`. Every pre-fix test in
# this directory constructs API Gateway proxy events without those keys, so
# without this fixture the authz check fires on empty claims and returns 403,
# masking the dispatch behaviour those tests exist to exercise.
#
# The default is platform-admin (cross-fleet) so a pre-fix test sees exactly
# the behaviour it was written against. Tests that specifically exercise the
# authz gate (test_authorization.py) opt out via `@pytest.mark.no_default_admin_claims`
# and set their own claim shapes.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _default_admin_claims_for_legacy_tests(request, monkeypatch):
    if "no_default_admin_claims" in request.keywords:
        return

    # Patch the seam the handler uses (services.fleet_intelligence._auth.
    # claims_from_event) rather than mutating every test event dict. If the
    # event already carries claims, honour them; otherwise return an admin
    # shape so `_authorize_or_403` allows cross-fleet reads.
    try:
        from services.fleet_intelligence import _auth as _auth_module
    except ModuleNotFoundError:  # pragma: no cover
        # Repo-root import not on path yet in a rare edge case — skip; the
        # test will build its own claims.
        return

    _real = _auth_module.claims_from_event

    def _admin_default(event):
        got = _real(event) or {}
        if got:
            return got
        return {"cognito:groups": "platform-admin"}

    monkeypatch.setattr(_auth_module, "claims_from_event", _admin_default)
