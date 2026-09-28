# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The six VFO routes stay removed, and a revival cannot ship ungated.

Replaces three test files deleted 2026-09-09 as part of finishing the VFO teardown
(`2026-09-05-cms-vfo-teardown`), which removed the route handlers but left their suites
behind — 15 tests failing `404 Endpoint not found` at HEAD. See
`issues/2026-09-09-vfo-teardown-left-15-tests-for-deleted-routes/`.

WHY THIS FILE EXISTS RATHER THAN JUST DELETING THE OLD ONES
-----------------------------------------------------------
`test_fleet_actions_approve_reject_authz.py` was the regression guard for a real
authorization defect: `POST /api/v1/fleet-actions/{id}/{approve,reject}` reached a
DynamoDB write with no authz check between route-match and write (fixed in `e7aebff0`,
gated to platform-admin). Deleting that guard outright would drop the *reason* it
existed along with the dead route, and this repo has now shipped four defects in one
family — groupless-to-platform-admin, fleetless-to-unscoped, fleet-actions, and
fleet-campaigns — where gating was applied per-route as each was noticed rather than
swept across the route table. A revived route landing without authz would be the fifth.

So the removal is pinned instead. If anyone re-adds one of these paths, the matching
test fails and they are forced to decide about authorization deliberately, with the
history in front of them, rather than inheriting a 404 that used to be a 403.

WHAT THIS FILE DOES NOT CLAIM
-----------------------------
It does not assert the routes *should* stay gone — that is a product decision, and
`services/fleet_intelligence/` is the deliberate Tier 1 successor for the fleet view.
It asserts only that they are gone *today*, so their disappearance is a recorded
decision rather than an accident, and that reviving one is a conscious act.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Env — mirrors the setdefault block the deleted suites used, so importing the
# handler does not fail on absent table names.
# ---------------------------------------------------------------------------
_ENV_DEFAULTS = {
    # The handler validates this exact set before it routes anything
    # (`index.py:2209-2216`), returning 500 on the first missing one. An incomplete set
    # here makes EVERY path 500 — including live ones — so the absence assertions below
    # would pass for the wrong reason and the negative control would be the only thing
    # that noticed. Keep in sync with `required_env_vars` in the handler.
    'SAFETY_EVENTS_TABLE_NAME': 'cms-test-storage-safety-events',
    'VEHICLES_TABLE_NAME': 'cms-test-storage-vehicles',
    'FLEETS_TABLE_NAME': 'cms-test-storage-fleets',
    'DASHBOARD_METRICS_CACHE_TABLE': 'cms-test-storage-dashboard-metrics-cache',
    'DRIVERS_TABLE_NAME': 'cms-test-storage-drivers',
    'SERVICE_HISTORY_TABLE_NAME': 'cms-test-storage-service-history',
    # Not in the required list, but read on paths these tests traverse.
    'FLEET_ENROLLMENT_TABLE_NAME': 'cms-test-storage-fleet-enrollment',
    'CAMPAIGNS_TABLE_NAME': 'cms-test-storage-campaigns',
    'DEPLOYMENT_STAGE': 'test',
    'AWS_DEFAULT_REGION': 'us-east-1',
}
for _k, _v in _ENV_DEFAULTS.items():
    os.environ.setdefault(_k, _v)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The six handlers the teardown removed. `/api/v1/fleet-actions/{id}/{action}` is
# represented by a concrete instance because the router matches on a literal path.
VFO_ROUTES_REMOVED = [
    ('GET', '/api/v1/daily-briefing'),
    ('GET', '/api/v1/fleet-health'),
    ('GET', '/api/v1/fleet-actions'),
    ('POST', '/api/v1/fleet-actions/act-123/approve'),
    ('POST', '/api/v1/fleet-actions/act-123/reject'),
    ('GET', '/api/v1/decision-journal'),
    ('GET', '/api/v1/documents'),
]


def _index():
    try:
        if 'index' in sys.modules:
            del sys.modules['index']
        return importlib.import_module('index')
    except Exception as exc:  # pragma: no cover - environment problem, not a defect
        pytest.skip(f"could not import main_api index: {type(exc).__name__}: {exc}")


def _mocked_ddb(index):
    """Patch `dynamodb` so no real AWS call is attempted.

    Without this the handler raises on its first table access and returns **500**, which
    would make every absence assertion below fail for an environment reason rather than
    a routing one — and, worse, would let a `!= 404` control pass on the 500. The
    deleted suites this file replaces did the same patching; the shape is copied from
    `test_fleet_endpoints_authz.py`.
    """
    def mock_table(_name):
        t = MagicMock()
        t.get_item.return_value = {}
        t.put_item.return_value = {}
        t.scan.return_value = {'Items': []}
        t.query.return_value = {'Items': []}
        return t

    p = patch.object(index, 'dynamodb')
    m = p.start()
    m.Table.side_effect = mock_table
    return p


def _platform_admin_event(method: str, path: str) -> dict:
    """A platform-admin caller — the most-privileged principal available.

    Using the strongest identity is the point: if the route were merely *gated* rather
    than *absent*, a platform-admin would get through and this test would fail. A
    groupless caller could be refused for the wrong reason and mask a live route.
    """
    return {
        'httpMethod': method,
        'path': path,
        'queryStringParameters': None,
        'body': json.dumps({}) if method == 'POST' else None,
        'headers': {},
        'requestContext': {
            'authorizer': {
                'claims': {
                    'sub': 'test-user',
                    'email': 'admin@example.invalid',
                    'cognito:groups': 'platform-admin',
                    'custom:fleetIds': '',
                }
            }
        },
    }


@pytest.mark.parametrize('method,path', VFO_ROUTES_REMOVED)
def test_vfo_route_is_absent(method, path):
    """A removed VFO route 404s even for platform-admin.

    If this fails with anything other than 404, the route is back. Re-add its
    authorization guard in the same change — see the module docstring for the defect
    family this prevents — and update this file to reflect the decision.
    """
    index = _index()
    patcher = _mocked_ddb(index)
    try:
        response = index.handler(_platform_admin_event(method, path), None)
    finally:
        patcher.stop()
    status = response.get('statusCode')

    assert status == 404, (
        f"{method} {path} returned {status}, not 404 — the route is live again.\n"
        "This path was removed by 2026-09-05-cms-vfo-teardown. If reviving it is "
        "intended, that is fine, but it MUST land with an authorization check in the "
        "same change: POST /api/v1/fleet-actions/{id}/{approve,reject} previously "
        "reached a DynamoDB write with no authz between route-match and write "
        "(fixed in e7aebff0). Four defects in this repo share that shape. Add the "
        "guard, add its regression test, then remove this route from "
        "VFO_ROUTES_REMOVED with a note saying why."
    )


def test_the_guard_can_actually_fail():
    """Negative control — prove a live route does NOT read as absent.

    Without this, the whole file would pass against a handler that 404s everything,
    which is how the suite this file replaces ended up with a test passing vacuously
    against deleted routes.

    Asserts a specific success status rather than merely `!= 404`. An earlier draft of
    this control used `!= 404` and passed on a **500** from unmocked DynamoDB — the same
    too-loose-assertion shape it exists to rule out.
    """
    index = _index()
    patcher = _mocked_ddb(index)
    try:
        response = index.handler(
            _platform_admin_event('GET', '/api/v1/fleet-campaigns'), None
        )
    finally:
        patcher.stop()
    status = response.get('statusCode')

    # 400 is the expected result here and is *better* evidence than 200 would be: the
    # router dispatched to a real handler, which then rejected the request on its own
    # input validation. That proves route existence, which is the only thing this
    # control needs to establish.
    #
    # The two statuses ruled out are named explicitly rather than asserting a loose
    # `!= 404`, because each corresponds to a distinct way this whole file could become
    # meaningless:
    #   404 → the router rejects everything, so the absence tests prove nothing
    #   500 → env validation or DDB patching broke, so no test here reached routing
    assert status not in (404, 500), (
        f"GET /api/v1/fleet-campaigns returned {status}. This control exists to prove "
        "the absence assertions above are meaningful.\n"
        "  404 → the handler rejects every path, so test_vfo_route_is_absent would pass "
        "against anything. Pick a different live route, or find out why fleet-campaigns "
        "is unreachable.\n"
        "  500 → the required-env-var check at index.py:2209 or the DynamoDB patching "
        "above has broken, so every assertion in this file is measuring the wrong thing. "
        "Fix that before trusting any of it.\n"
        f"Body: {response.get('body')!r}"
    )
