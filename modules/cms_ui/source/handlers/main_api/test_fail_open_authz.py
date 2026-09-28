#!/usr/bin/env python3
"""RED tests for fail-open authorization defaults in ``main_api.index``.

Issue: ``cms/issues/2026-08-05-main-api-fail-open-authz-defaults/report.md``
Spec:  ``cms/.kiro/specs/2026-08-05-cms-demo-identity-model/tasks.md`` — Group A0, task 1

Two independent fail-opens must be closed:

  Defect 1 — index.py:736
    ``is_admin = 'platform-admin' in user_groups or not user_groups``
    A token with no groups is promoted to platform-admin.

  Defect 2 — index.py:773/:793/:801
    ``if is_admin or not user_fleet_ids:``
    A non-admin token with no custom:fleetIds receives unscoped (cross-fleet) access
    on the fleet-scoped paths that call ``get_allowed_vehicle_ids()``,
    ``_check_fleet_access()``, and ``_scope_fleet_filter()``.

Expected RED/GREEN split for the CURRENT (unfixed) code:
  Case 1  FAIL — Defect 1: groupless token gets is_admin=True
  Case 2  FAIL — Defect 2: non-admin/fleetless token passes _check_fleet_access
  Case 3  PASS — platform-admin still works (positive regression guard)
  Case 4  PASS — fleet-operator is not viewer (positive regression guard)
  Case 5  PASS — fleet-viewer is read-only (positive regression guard)
  Case 6  PASS — driver-self path unchanged (existing guard, GREEN by design)
  Case 7  FAIL — Defect 2: UI-only group token with no fleetIds passes fleet check

After both defects are fixed ALL cases should pass.

Run from ``modules/cms_ui/source/handlers/main_api/``::

    python3 -m pytest test_fail_open_authz.py -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('REDIS_ENDPOINT', '')
os.environ['DRIVER_SELF_GUARD_ENABLED'] = 'true'
os.environ.setdefault('DRIVERS_TABLE_NAME', 'test-drivers')
os.environ.setdefault('VEHICLES_TABLE_NAME', 'test-vehicles')
os.environ.setdefault('FLEET_ENROLLMENT_TABLE_NAME', 'test-enrollment')
os.environ.setdefault('USER_POOL_ID', 'us-east-2_EXAMPLE')
# These four are required by the env-var guard that precedes Block 2 in index.py.
# Without them the guard fires a 500 before _check_fleet_access can execute,
# which means Cases 2 and 7 would pass through the 500 (not the fleet gate).
# All four are confirmed SET on the deployed Lambda; the tests must mirror that.
os.environ.setdefault('SAFETY_EVENTS_TABLE_NAME', 'test-safety-events')
os.environ.setdefault('FLEETS_TABLE_NAME', 'test-fleets')
os.environ.setdefault('DASHBOARD_METRICS_CACHE_TABLE', 'test-cache')
os.environ.setdefault('SERVICE_HISTORY_TABLE_NAME', 'test-service-history')

# Stub heavy module-level deps before importing index — same pattern as
# test_driver_self_authz.py.
boto3_stub = MagicMock()
boto3_stub.resource = MagicMock(return_value=MagicMock())
boto3_stub.client = MagicMock(return_value=MagicMock())
sys.modules.setdefault('boto3', boto3_stub)

cache_stub = MagicMock()
cache_stub.create_cached_dynamodb_client = MagicMock(return_value=MagicMock())
sys.modules.setdefault('cache_client', cache_stub)

event_catalog_stub = MagicMock()
event_catalog_stub.enrich_event_with_catalog = MagicMock()
event_catalog_stub.normalize_event_response = MagicMock()
sys.modules.setdefault('event_catalog_helper', event_catalog_stub)

import index  # noqa: E402


# ── Fixture builders (reused from test_driver_self_authz.py style) ─────────

def _claims(groups=None, fleet_ids=None, driver_id=None, email='test@example.com'):
    """Build a minimal Cognito claims dict.

    ``groups``    – str (comma-joined) or None (groupless / no cognito:groups attr)
    ``fleet_ids`` – str (comma-joined) or None (no custom:fleetIds attribute)
    ``driver_id`` – str or None (no custom:driverId attribute)
    """
    c: dict = {'email': email, 'custom:tenantId': 'test-tenant'}
    if groups is not None:
        c['cognito:groups'] = groups
    if fleet_ids is not None:
        c['custom:fleetIds'] = fleet_ids
    if driver_id is not None:
        c['custom:driverId'] = driver_id
    return c


def _event(method, path, claims, body=None, query_params=None):
    """Minimal API Gateway proxy event — same shape as test_driver_self_authz.py."""
    return {
        'httpMethod': method,
        'path': path,
        'body': json.dumps(body) if body is not None else None,
        'requestContext': {'authorizer': {'claims': claims}},
        'queryStringParameters': query_params or {},
        'pathParameters': None,
        'headers': {},
    }


# ── Case 1 — no groups must NOT produce is_admin=True ──────────────────────
#
# Defect 1  index.py:736  ``is_admin = 'platform-admin' in user_groups or not user_groups``
# A groupless token has user_groups=[], so ``not user_groups`` is True → is_admin=True.
#
# Observable effect: GET /api/v1/users is gated by ``is_admin``.  A groupless
# caller must be denied (403); currently it is admitted (200).
#
# RED — FAILS against current code.
class Case1_NoGroupsNotAdmin(unittest.TestCase):
    """Case 1 — groupless token must not be promoted to platform-admin.

    RED: currently FAILS because ``or not user_groups`` makes is_admin=True.
    """

    def test_groupless_token_denied_admin_only_path(self):
        """A token with no groups must NOT reach the admin-only GET /api/v1/users path.

        Secure behaviour: 403.
        Current behaviour (defect 1): 200 with user list  →  assertion FAILS (RED).
        """
        ev = _event('GET', '/api/v1/users', _claims())
        resp = index.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 403,
            f"Groupless token was granted admin access to GET /api/v1/users "
            f"(statusCode={resp['statusCode']}). Defect 1 still present: "
            f"``is_admin = ... or not user_groups`` at index.py:736.",
        )


# ── Case 2 — non-admin/fleetless token must not pass _check_fleet_access ────
#
# Defect 2  index.py:793  ``if is_admin or not user_fleet_ids: return None``
# (same shape at :773 get_allowed_vehicle_ids and :801 _scope_fleet_filter).
# A non-admin caller with user_fleet_ids=[] passes the ``not user_fleet_ids``
# branch and receives unscoped fleet access.
#
# To isolate Defect 2 from Defect 1 we use a token that:
#   • has a group   (so user_groups is non-empty → not user_groups=False → Defect 1 doesn't fire)
#   • has no fleets (so user_fleet_ids=[] → not user_fleet_ids=True → Defect 2 fires)
#   • is not in _OPERATOR_GROUPS (so is_viewer=False, is_admin=False via normal path)
#
# POST /api/v1/drivers calls ``_deny_viewer()`` then ``_check_fleet_access(fleet_id)``.
# A token with is_admin=False and user_fleet_ids=[] should be denied on fleet_id='OTHER-FLEET'
# because the caller has no fleet membership at all.  _check_fleet_access currently returns
# None (allow) when user_fleet_ids=[], so the POST proceeds past the fleet gate.
#
# The secure response is 403 from _check_fleet_access.
# Current response (defect 2): _check_fleet_access returns None → POST proceeds.
#
# RED — FAILS against current code.
class Case2_FleetlessNonAdminNoUnscopedAccess(unittest.TestCase):
    """Case 2 — a non-admin token with no fleetIds must not bypass _check_fleet_access.

    Uses a 'fleet-viewer' token to ensure is_admin=False (Defect 1 not involved),
    but strips fleetIds so user_fleet_ids=[].  The viewer's _deny_viewer() would
    gate writes, so we use a fleet-operator token (not viewer) with no fleetIds.

    Actually, fleet-operator is in _OPERATOR_GROUPS so it's eligible for is_viewer=False
    and is_admin=False — a fleet-operator with no fleetIds should be scoped to nothing,
    not to all fleets.

    RED: FAILS because ``not user_fleet_ids`` in _check_fleet_access returns None (allow).
    """

    def test_fleet_operator_no_fleetids_denied_fleet_write(self):
        """A fleet-operator token with no custom:fleetIds must not pass _check_fleet_access.

        _check_fleet_access('OTHER-FLEET') should return 403 for a caller with
        user_fleet_ids=[] (empty fleet set = no fleet scope, not all-fleets).
        Previously it returned None (allow) via ``not user_fleet_ids``.

        POST /api/v1/drivers with a fleetId exercises this path directly.
        The driver POST calls _check_fleet_access(fleet_id) before writing.
        A fleet-operator with no fleetIds should be denied, not allowed to write
        a driver into an arbitrary fleet.

        Secure behaviour: 403 from _check_fleet_access with body
          {'error': 'Access denied to this fleet'}.

        Proof that _check_fleet_access is the source: we assert on the exact
        error message produced by that function. The shim that was removed
        emitted 'Access denied: no fleet membership' — a different string.
        No other code path in the driver POST handler produces this exact message.
        """
        mock_table = MagicMock()
        mock_table.put_item.return_value = {}

        with patch.object(index, 'dynamodb') as mock_dynamo:
            mock_dynamo.Table.return_value = mock_table
            ev = _event(
                'POST', '/api/v1/drivers',
                # fleet-operator with NO fleetIds — is_admin=False, user_fleet_ids=[]
                _claims(groups='fleet-operator'),
                body={
                    'entry': {
                        'firstName': 'Test', 'lastName': 'Driver',
                        'email': 'test@example.com', 'fleetId': 'OTHER-FLEET',
                        'status': 'active',
                    }
                },
            )
            resp = index.handler(ev, {})

        # The fleet gate must fire BEFORE any DB write.  The only acceptable
        # response is 403 with the _check_fleet_access body.
        self.assertEqual(
            resp['statusCode'], 403,
            f"fleet-operator with no fleetIds bypassed _check_fleet_access "
            f"(statusCode={resp['statusCode']} — should be 403). "
            f"Defect 2 still present: ``not user_fleet_ids`` in _check_fleet_access.",
        )
        # Assert the specific error message from _check_fleet_access so this
        # test CANNOT pass via any other code path (shim, env-var guard, etc.).
        body = json.loads(resp['body'])
        self.assertEqual(
            body.get('error'), 'Access denied to this fleet',
            f"403 returned but body was {body!r}. "
            f"Expected error='Access denied to this fleet' — the exact message "
            f"from _check_fleet_access. If the message is different, the 403 came "
            f"from a shim or the env-var guard, not _check_fleet_access.",
        )


# ── Case 3 — platform-admin still gets admin access ────────────────────────
# Positive regression guard: the fix must not break legitimate admins.
# GREEN now, must stay GREEN after the fix.
class Case3_PlatformAdminIsAdmin(unittest.TestCase):
    """Case 3 — a platform-admin token must still pass the is_admin gate.

    GREEN now, must stay GREEN after the fix.
    """

    def test_platform_admin_reaches_admin_path(self):
        """A platform-admin group member must still reach admin-only paths.

        GET /api/v1/users is admin-only.  We stub Cognito list_users to return
        an empty list and assert 200, not 403.
        """
        mock_cognito = MagicMock()
        mock_cognito.list_users.return_value = {'Users': []}

        with patch.object(index, 'boto3') as mock_boto3:
            mock_boto3.client.return_value = mock_cognito
            ev = _event('GET', '/api/v1/users', _claims(groups='platform-admin'))
            resp = index.handler(ev, {})

        self.assertEqual(
            resp['statusCode'], 200,
            f"platform-admin was denied GET /api/v1/users (statusCode={resp['statusCode']}). "
            f"Fixing the fail-opens must not break legitimate admin access.",
        )


# ── Case 4 — fleet-operator still gets scoped write access ─────────────────
# Fleet-operator is not admin but not viewer either — they can write to their
# own fleets.  Assert is_viewer=False from the claim-extraction logic.
# GREEN now, must stay GREEN after the fix.
class Case4_FleetOperatorScopedWrite(unittest.TestCase):
    """Case 4 — fleet-operator must not be treated as viewer (read-only).

    GREEN now, must stay GREEN after the fix.
    """

    def test_fleet_operator_is_not_viewer(self):
        """A fleet-operator token must not be blocked by the is_viewer gate.

        We verify this at the claim-extraction level (pure logic, no I/O):
        is_viewer = 'fleet-viewer' in user_groups and 'fleet-operator' not in user_groups
        For a fleet-operator token, this must be False.
        """
        claims = _claims(groups='fleet-operator', fleet_ids='FLEET-001')
        user_groups = claims.get('cognito:groups', '').split(',') if claims.get('cognito:groups') else []
        is_viewer = 'fleet-viewer' in user_groups and 'fleet-operator' not in user_groups
        self.assertFalse(
            is_viewer,
            "fleet-operator was classified as viewer — is_viewer must be False.",
        )

    def test_fleet_operator_allowed_write_to_own_fleet(self):
        """A fleet-operator scoped to FLEET-001 can POST a driver into FLEET-001."""
        mock_table = MagicMock()
        mock_table.put_item.return_value = {}

        with patch.object(index, 'dynamodb') as mock_dynamo:
            mock_dynamo.Table.return_value = mock_table
            ev = _event(
                'POST', '/api/v1/drivers',
                _claims(groups='fleet-operator', fleet_ids='FLEET-001'),
                body={
                    'entry': {
                        'firstName': 'Test', 'lastName': 'Driver',
                        'email': 'test@example.com', 'fleetId': 'FLEET-001',
                        'status': 'active',
                    }
                },
            )
            resp = index.handler(ev, {})

        self.assertNotEqual(
            resp['statusCode'], 403,
            f"fleet-operator was denied POST into their own fleet FLEET-001 "
            f"(statusCode={resp['statusCode']}). Scoped write access broken.",
        )


# ── Case 5 — fleet-viewer is read-only ─────────────────────────────────────
# GREEN now, must stay GREEN after the fix.
class Case5_FleetViewerReadOnly(unittest.TestCase):
    """Case 5 — a fleet-viewer token must be denied write operations.

    GREEN now, must stay GREEN after the fix.
    """

    def test_fleet_viewer_classification(self):
        """fleet-viewer without fleet-operator in groups: is_viewer must be True."""
        claims = _claims(groups='fleet-viewer', fleet_ids='FLEET-001')
        user_groups = claims.get('cognito:groups', '').split(',')
        is_viewer = ('fleet-viewer' in user_groups) and ('fleet-operator' not in user_groups)
        self.assertTrue(is_viewer)

    def test_fleet_viewer_denied_create_fleet(self):
        """A fleet-viewer must receive 403 on POST /api/v1/fleets (write path)."""
        ev = _event('POST', '/api/v1/fleets', _claims(groups='fleet-viewer', fleet_ids='FLEET-001'),
                    body={'entry': {'name': 'new-fleet'}})
        resp = index.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 403,
            f"fleet-viewer was allowed POST /api/v1/fleets (statusCode={resp['statusCode']}). "
            f"Read-only enforcement broken.",
        )


# ── Case 6 — driver-self token is still constrained by the existing guard ───
# Regression guard: the driver-self path works correctly today.  This PASSES now
# and MUST continue to pass after the fail-open fix.
#
# Note: this is the ONE authz surface that already works correctly.  The fail-open
# fix must not touch _classify_driver_self, _driver_self_guard, or
# _require_driver_self_guard().  The driver-self guard explicitly forces is_admin=False
# (line ~743), which is why it is unaffected by Defect 1.  Defect 2 is also
# bypassed because the driver-self path forces user_fleet_ids to the driver's own
# fleet before any of the :773/:793/:801 checks are reached (line ~768).
#
# GREEN now by design — expected and correct.
class Case6_DriverSelfPathUnchanged(unittest.TestCase):
    """Case 6 — driver-self regression guard.

    GREEN now (driver-self is already correct).  Must stay GREEN after the fix.

    Handback note: this is the one existing authz surface that already works.
    The fail-open fix must not disturb it.
    """

    DRIVER_ID = 'DRV-0054'

    def _driver_claims(self):
        """Driver token: custom:driverId present, no operator group."""
        return _claims(driver_id=self.DRIVER_ID)

    def test_driver_self_classified_correctly(self):
        """_classify_driver_self returns (True, driver_id) for a driver token."""
        is_self, did = index._classify_driver_self(self._driver_claims())
        self.assertTrue(is_self)
        self.assertEqual(did, self.DRIVER_ID)

    def test_driver_self_denied_admin_path(self):
        """A driver-self token must be denied on admin-only paths (GET /api/v1/users)."""
        ev = _event('GET', '/api/v1/users', self._driver_claims())
        resp = index.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 403,
            f"Driver-self token was allowed GET /api/v1/users (statusCode={resp['statusCode']}). "
            f"driver-self guard broken.",
        )

    def test_driver_self_denied_write_other_driver(self):
        """A driver-self token must not PUT another driver's record."""
        ev = _event('PUT', '/api/v1/drivers/DRV-9999', self._driver_claims(),
                    body={'assignedVehicleId': 'VEH-001'})
        resp = index.handler(ev, {})
        self.assertEqual(resp['statusCode'], 403)

    def test_driver_with_operator_group_not_classified_driver_self(self):
        """A token with both custom:driverId and platform-admin is NOT driver-self."""
        claims = _claims(groups='platform-admin', driver_id=self.DRIVER_ID)
        is_self, _ = index._classify_driver_self(claims)
        self.assertFalse(is_self)


# ── Case 7 — UI-only groups must not receive unscoped fleet access ───────────
#
# Defect 2  index.py:793  ``if is_admin or not user_fleet_ids: return None``
#
# Groups like ``agent``, ``dispatcher``, ``product-engineer`` are NOT in
# _OPERATOR_GROUPS = {'platform-admin', 'fleet-operator', 'fleet-viewer'}.
# They are UI-only gates enforced in useUserRole.ts; the backend does not
# recognise them as operator groups.
#
# For a token with group='agent' (or similar) and no fleetIds:
#   is_admin = ('platform-admin' in ['agent']) or not ['agent']
#            = False or False = False    ← Defect 1 does NOT fire here
#   user_fleet_ids = []
#   _check_fleet_access('ANY-FLEET'):
#     if is_admin or not user_fleet_ids:   ← ``not user_fleet_ids`` is True!
#         return None                       ← ALLOW — Defect 2
#
# Observable effect: POST /api/v1/drivers calls _deny_viewer() then
# _check_fleet_access(fleet_id).  An 'agent' token is not viewer, so
# _deny_viewer() returns None.  _check_fleet_access('OTHER-FLEET') returns
# None (allow) due to Defect 2.  The POST proceeds past the fleet gate.
#
# Secure behaviour: 403 — a caller with no fleet membership must not write
# into an arbitrary fleet.
#
# RED — FAILS against current code because Defect 2 is not fixed.
class Case7_UIOnlyGroupNoBackendElevation(unittest.TestCase):
    """Case 7 — a UI-only group token with no fleetIds must not bypass _check_fleet_access.

    Groups 'agent', 'dispatcher', 'product-engineer' are not in _OPERATOR_GROUPS.
    With no fleetIds, the ``not user_fleet_ids`` branch of _check_fleet_access
    returns None (allow) — Defect 2.

    RED: currently FAILS because _check_fleet_access allows the call through.
    """

    def _ui_only_claims(self, group: str) -> dict:
        """Token with a UI-only group and NO fleetIds."""
        return _claims(groups=group)  # no fleet_ids= → user_fleet_ids=[]

    def _post_driver_to_other_fleet(self, group: str):
        """Issue POST /api/v1/drivers into an unowned fleet; return the response."""
        mock_table = MagicMock()
        mock_table.put_item.return_value = {}

        with patch.object(index, 'dynamodb') as mock_dynamo:
            mock_dynamo.Table.return_value = mock_table
            ev = _event(
                'POST', '/api/v1/drivers',
                self._ui_only_claims(group),
                body={
                    'entry': {
                        'firstName': 'Test', 'lastName': 'Driver',
                        'email': f'{group}@example.com',
                        'fleetId': 'UNOWNED-FLEET',
                        'status': 'active',
                    }
                },
            )
            return index.handler(ev, {})

    def test_agent_group_denied_fleet_write(self):
        """An 'agent' group token with no fleetIds must not pass _check_fleet_access.

        POST /api/v1/drivers into an unowned fleet:
          Secure:  403 with body {'error': 'Access denied to this fleet'} from
                   _check_fleet_access — the fleet gate that actually runs in production.
          Defect:  passes _check_fleet_access (None returned) → proceeds → 201 or 500.

        The body assertion proves the 403 came from _check_fleet_access and not from
        a shim (which emitted 'Access denied: no fleet membership') or the env-var guard.
        """
        resp = self._post_driver_to_other_fleet('agent')
        self.assertEqual(
            resp['statusCode'], 403,
            f"'agent' group token (no fleetIds) bypassed _check_fleet_access "
            f"(statusCode={resp['statusCode']}). "
            f"A 500 means the fleet gate returned None (allow) and the handler reached "
            f"the DynamoDB layer before failing — the gate was bypassed. "
            f"Defect 2 still present.",
        )
        body = json.loads(resp['body'])
        self.assertEqual(
            body.get('error'), 'Access denied to this fleet',
            f"403 returned but body was {body!r}. "
            f"Expected error='Access denied to this fleet' from _check_fleet_access.",
        )

    def test_dispatcher_group_denied_fleet_write(self):
        """A 'dispatcher' group token with no fleetIds must not pass _check_fleet_access."""
        resp = self._post_driver_to_other_fleet('dispatcher')
        self.assertEqual(
            resp['statusCode'], 403,
            f"'dispatcher' group token (no fleetIds) bypassed _check_fleet_access "
            f"(statusCode={resp['statusCode']}, expected 403). "
            f"Defect 2 still present (index.py:793).",
        )

    def test_product_engineer_group_denied_fleet_write(self):
        """A 'product-engineer' group token with no fleetIds must not pass _check_fleet_access."""
        resp = self._post_driver_to_other_fleet('product-engineer')
        self.assertEqual(
            resp['statusCode'], 403,
            f"'product-engineer' group token (no fleetIds) bypassed _check_fleet_access "
            f"(statusCode={resp['statusCode']}, expected 403). "
            f"Defect 2 still present (index.py:793).",
        )

    def test_ui_only_group_is_not_admin(self):
        """UI-only group tokens are NOT promoted to admin by Defect 1.

        For these tokens user_groups is non-empty, so ``not user_groups`` is False
        and is_admin=False.  This sub-case confirms Defect 1 doesn't fire here
        and that Defect 2 is the standalone issue for UI-only groups.
        """
        for group in ('agent', 'dispatcher', 'product-engineer'):
            claims = _claims(groups=group)
            user_groups = claims.get('cognito:groups', '').split(',') if claims.get('cognito:groups') else []
            is_admin = 'platform-admin' in user_groups or not user_groups
            self.assertFalse(
                is_admin,
                f"'{group}' group was classified as admin — "
                f"UI-only groups must not be in is_admin path.",
            )


# ── Case 8 — groupless token must get EMPTY collection from GET /api/v1/fleets ─
#
# This is the path proven vulnerable live: a groupless token received HTTP 200
# with full fleet data from GET /api/v1/fleets.
#
# The bug: ``if not is_admin and user_fleet_ids:`` evaluates ``True and [] → False``,
# so the scoped-lookup branch was SKIPPED and execution fell through to the
# admin cache path, which returns ALL fleets with no filter.
#
# The fix: ``if not has_unscoped_access:`` — for a groupless token,
# has_unscoped_access = False (is_admin=False, is_viewer=False), so the
# scoped-lookup branch IS entered, iterates over an empty user_fleet_ids list,
# appends nothing to fleets, and returns {'fleets': [], 'total': 0}.
#
# A 200 with all rows and a 200 with zero rows are both 200 — we assert on the
# response body, not just the status code.
class Case8_GrouplessGetFleetsEmptyNotAll(unittest.TestCase):
    """Case 8 — groupless token must receive an EMPTY fleets collection.

    GET /api/v1/fleets is the PROVEN VULNERABLE path (live HTTP 200 with full
    fleet data issued to a groupless token before the fix).  After the fix,
    the same call must return 200 with {'fleets': [], 'total': 0}, NOT with
    the full fleet list.
    """

    def test_groupless_token_fleets_list_is_empty(self):
        """GET /api/v1/fleets with a groupless token must return an empty fleet list.

        Before the fix: ``not is_admin and user_fleet_ids`` = ``True and []`` = False
        → scoped branch skipped → admin cache path → returns ALL fleets.

        After the fix: ``not has_unscoped_access`` = True
        → scoped branch entered → iterates empty user_fleet_ids → returns [].

        Assertion is on the response body, not just status code, because a 200
        with all rows is as bad as any other auth bypass — we need to prove it is
        empty.
        """
        # Stub the DynamoDB table so get_item would return a fleet if called.
        # If the fix is absent, the handler falls through to the admin path and
        # calls cache_table.get_item + fleets_table.scan, which the mocks would
        # return data from.  With the fix, neither is called because the scoped
        # branch returns immediately after iterating an empty list.
        mock_table = MagicMock()
        mock_table.get_item.return_value = {'Item': {'fleetId': 'FLEET-001', 'name': 'Test Fleet'}}
        mock_table.scan.return_value = {'Items': [{'fleetId': 'FLEET-001', 'name': 'Test Fleet'}], 'Count': 1}

        with patch.object(index, 'dynamodb') as mock_dynamo:
            mock_dynamo.Table.return_value = mock_table
            ev = _event('GET', '/api/v1/fleets', _claims())  # groupless, no fleetIds
            resp = index.handler(ev, {})

        self.assertEqual(resp['statusCode'], 200,
                         f"Expected 200, got {resp['statusCode']}: {resp.get('body', '')}")
        body = json.loads(resp['body'])
        fleets = body.get('fleets', 'MISSING')
        self.assertIsInstance(fleets, list,
                              f"Response body 'fleets' key is not a list: {body!r}")
        self.assertEqual(
            len(fleets), 0,
            f"Groupless token received {len(fleets)} fleet(s) — expected 0. "
            f"Fleet data was returned: {fleets!r}. "
            f"Defect 2 still present on GET /api/v1/fleets: "
            f"``not is_admin and user_fleet_ids`` → False, scoped branch skipped, "
            f"admin cache path returns all rows.",
        )
        total = body.get('total', -1)
        self.assertEqual(
            total, 0,
            f"Groupless token received total={total} — expected 0. Body: {body!r}",
        )


# ── Case 9 — groupless token must get EMPTY collection from GET /api/v1/subscriptions ─
#
# A second read path confirming the scoping fix. GET /api/v1/subscriptions was
# guarded by ``if not is_admin and user_fleet_ids: items = [filter...]`` — same
# fail-open: empty user_fleet_ids → filter skipped → all subscriptions returned.
#
# After the fix (``if not has_unscoped_access:``), the filter IS applied.
# The filter expression is ``item.get('fleetId') in user_fleet_ids`` where
# user_fleet_ids=[] — so ALL items are filtered out → empty list returned.
class Case9_GrouplessGetSubscriptionsEmptyNotAll(unittest.TestCase):
    """Case 9 — groupless token must receive an EMPTY subscriptions list.

    GET /api/v1/subscriptions was scoped by the same ``not is_admin and
    user_fleet_ids`` fail-open guard.  With no fleetIds, the filter was
    skipped and all subscriptions were returned.

    After the fix the filter runs with user_fleet_ids=[] — every item's
    fleetId fails the ``in []`` check — so the result is an empty list.
    """

    def _make_subscription(self, fleet_id: str) -> dict:
        return {
            'vehicleId': f'VEH-{fleet_id}-001',
            'fleetId': fleet_id,
            'tier': 'basic',
            'tierName': 'Basic',
            'signals': ['speed'],
            'status': 'active',
        }

    def test_groupless_token_subscriptions_list_is_empty(self):
        """GET /api/v1/subscriptions with a groupless token must return [] subscriptions.

        We stub subscriptions_table.scan to return two real subscription rows.
        Before the fix, both rows are returned unchanged (filter skipped).
        After the fix, both rows are filtered out (fleetId not in []).

        Assertion is on the body contents — two 200s with different payloads.
        """
        mock_table = MagicMock()
        mock_table.scan.return_value = {
            'Items': [
                self._make_subscription('FLEET-A'),
                self._make_subscription('FLEET-B'),
            ]
        }

        with patch.object(index, 'dynamodb') as mock_dynamo:
            mock_dynamo.Table.return_value = mock_table
            ev = _event('GET', '/api/v1/subscriptions', _claims())  # groupless
            resp = index.handler(ev, {})

        self.assertEqual(resp['statusCode'], 200,
                         f"Expected 200, got {resp['statusCode']}: {resp.get('body', '')}")
        body = json.loads(resp['body'])
        subs = body.get('subscriptions', 'MISSING')
        self.assertIsInstance(subs, list,
                              f"'subscriptions' key is not a list: {body!r}")
        self.assertEqual(
            len(subs), 0,
            f"Groupless token received {len(subs)} subscription(s) — expected 0. "
            f"Subscriptions returned: {subs!r}. "
            f"Defect 2 still present on GET /api/v1/subscriptions.",
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)
