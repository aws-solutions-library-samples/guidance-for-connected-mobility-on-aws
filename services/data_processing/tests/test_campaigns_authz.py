"""
Authorization tests for the data-processing /campaigns routes.
Spec: .kiro/specs/2026-09-15-cms-cs-campaign-ownership/ Task FG1.1

Two-tier group policy:
  WRITE (POST, PUT, DELETE):  platform-admin, fleet-operator, connected-services
  READ  (GET):                the write set + fleet-viewer

Every test below named  *_must_pass_*  asserts a group IS admitted.
Every test named         *_must_403_*  asserts a group IS refused.

Three named MUTATION TARGETS (see FG1.1 Verify):
  MUTATION 1: drop 'platform-admin' from _CAMPAIGN_WRITE_GROUPS
              => test_platform_admin_must_pass_write_* will FAIL
  MUTATION 2: add 'fleet-viewer' to _CAMPAIGN_WRITE_GROUPS
              => test_fleet_viewer_must_403_write_* will FAIL
  MUTATION 3: add 'fleet-guest' to _CAMPAIGN_READ_GROUPS
              => test_fleet_guest_must_403_read_* will FAIL
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
LAMBDA_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'lambda')
)
if LAMBDA_DIR not in sys.path:
    sys.path.insert(0, LAMBDA_DIR)

with patch.dict(os.environ, {
    'SIGNAL_CATALOG_TABLE': 'test-signal-catalog',
    'DATA_SOURCE_CONFIGS_TABLE': 'test-data-sources',
    'MANIFESTS_BUCKET': 'test-manifests',
    'CAMPAIGNS_TABLE': 'test-campaigns',
    'DEPLOYMENT_STAGE': 'test',
}):
    import data_processing_api as api  # noqa: E402


# ---------------------------------------------------------------------------
# Event builders
# ---------------------------------------------------------------------------

def _event(method: str, path: str, groups: list, body=None, query=None,
           fleet_ids: str = '') -> dict:
    """API-GW-shaped event carrying the given Cognito groups.

    ``fleet_ids`` is forwarded as ``custom:fleetIds`` when non-empty.  Pass it
    for ``fleet-operator`` callers so ``_caller_fleet_id`` can resolve their
    fleet identity; without it the guard raises and the caller is refused.
    """
    claims: dict = {
        'cognito:groups': ','.join(groups),
        'sub': 'test-sub-' + method,
    }
    if fleet_ids:
        claims['custom:fleetIds'] = fleet_ids
    return {
        'httpMethod': method,
        'path': path,
        'queryStringParameters': query or {},
        'body': json.dumps(body) if body else None,
        'requestContext': {
            'authorizer': {
                'claims': claims,
            }
        },
    }


def _no_claims_event(method: str, path: str) -> dict:
    """No requestContext at all — must fail closed."""
    return {
        'httpMethod': method,
        'path': path,
        'queryStringParameters': {},
        'body': None,
    }


def _empty_groups_event(method: str, path: str) -> dict:
    """cognito:groups is an empty string — must still fail closed."""
    return {
        'httpMethod': method,
        'path': path,
        'queryStringParameters': {},
        'body': None,
        'requestContext': {
            'authorizer': {
                'claims': {
                    'cognito:groups': '',
                    'sub': 'test-sub',
                }
            }
        },
    }


# ---------------------------------------------------------------------------
# Route/method matrix
# ---------------------------------------------------------------------------
#: (label, method, path, body, query)
#:
#: Used ONLY for refusal assertions (see `_assert_403_no_ddb`), where authorization is
#: decided before the handler parses a body — so these shapes do not affect the outcome.
#: They are nonetheless kept faithful to the real routes, because a matrix that misstates a
#: route's shape is how a later reader reuses it for a happy-path test and gets a silent
#: pass. `DELETE /campaigns/assign` in particular reads `campaignName` + `vehicles` from the
#: BODY, not from query params: see `unassign_campaign` in `data_processing_api.py`, and the
#: already-correct `_WRITE_ROUTES_WELLFORMED` below. This row said
#: `query={'campaignId':..., 'vehicleId':...}` until 2026-09-20, contradicting both.
WRITE_ROUTES = [
    ("POST /campaigns",          "POST",   "/campaigns",        {"campaignName": "c"},                          None),
    ("PUT /campaigns",           "PUT",    "/campaigns",        {"campaignId": "c"},                            None),
    ("DELETE /campaigns",        "DELETE", "/campaigns",        None,                                           {"campaignId": "c"}),
    ("POST /campaigns/assign",   "POST",   "/campaigns/assign", {"campaignName": "c", "vehicles": ["V1"]},     None),
    ("DELETE /campaigns/assign", "DELETE", "/campaigns/assign", {"campaignName": "c", "vehicles": ["V1"]},     None),
]

READ_ROUTES = [
    ("GET /campaigns",                    "GET", "/campaigns",                    None, None),
    ("GET /campaigns/collection-scheme",  "GET", "/campaigns/collection-scheme",  None, {"campaignId": "c"}),
]

ALL_ROUTES = WRITE_ROUTES + READ_ROUTES


# ---------------------------------------------------------------------------
# Base class: mock DynamoDB, provide safe defaults
# ---------------------------------------------------------------------------

class _AuthzBase(unittest.TestCase):
    def setUp(self):
        self.mock_table = MagicMock()
        self.mock_table.scan.return_value = {'Items': []}
        self.mock_table.query.return_value = {'Items': []}
        self.mock_table.get_item.return_value = {'Item': {}}
        self.mock_table.put_item.return_value = {}
        self.mock_table.update_item.return_value = {}
        self.mock_table.delete_item.return_value = {}
        self.patcher = patch.object(api, 'campaigns_table', self.mock_table)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def _ddb_call_count(self):
        return sum(
            getattr(self.mock_table, m).call_count
            for m in ('scan', 'query', 'get_item', 'put_item', 'update_item', 'delete_item')
        )

    def _assert_403_no_ddb(self, event, label):
        resp = api.handler(event, {})
        self.assertEqual(
            resp['statusCode'], 403,
            f"{label}: expected 403, got {resp['statusCode']}",
        )
        self.assertEqual(
            self._ddb_call_count(), 0,
            f"{label}: DynamoDB must not be called on the 403 path",
        )
        self.mock_table.reset_mock()

    def _assert_admitted(self, event, label):
        resp = api.handler(event, {})
        self.assertNotEqual(
            resp['statusCode'], 403,
            f"{label}: group should be admitted but got 403",
        )
        self.mock_table.reset_mock()


# ---------------------------------------------------------------------------
# Fail-closed baseline: no credentials at all
# ---------------------------------------------------------------------------

class TestFailClosed(_AuthzBase):
    """No claims / empty group list => 403 with no DDB calls on every route."""

    def test_no_claims_all_routes(self):
        for label, method, path, body, query in ALL_ROUTES:
            with self.subTest(route=label):
                self._assert_403_no_ddb(_no_claims_event(method, path), label)

    def test_empty_groups_all_routes(self):
        for label, method, path, body, query in ALL_ROUTES:
            with self.subTest(route=label):
                self._assert_403_no_ddb(_empty_groups_event(method, path), label)

    def test_unknown_group_all_routes(self):
        for label, method, path, body, query in ALL_ROUTES:
            with self.subTest(route=label):
                ev = _event(method, path, ['fleet-alpha'], body=body, query=query)
                self._assert_403_no_ddb(ev, label)

    def test_fleet_guest_all_routes(self):
        """fleet-guest is not in any allowed set."""
        for label, method, path, body, query in ALL_ROUTES:
            with self.subTest(route=label):
                ev = _event(method, path, ['fleet-guest'], body=body, query=query)
                self._assert_403_no_ddb(ev, label)

    # ---- FG2.1: sub-claim gate (W1) ----------------------------------------
    # These two tests reach the 'sub' gate by passing a valid write group so
    # the group check succeeds, then presenting a missing or empty 'sub' claim.
    # Because all existing fail-closed tests carry no requestContext at all, the
    # group gate fires before the sub gate and the sub gate was never exercised.
    #
    # MUTATION TARGET (FG2.1): delete the 'sub' check from
    # _require_campaign_access (lines ~669-671 in data_processing_api.py) and
    # every test in this block MUST FAIL.  If they stay green the test is
    # asserting presence not the sub-gate property.
    # -----------------------------------------------------------------------

    def test_valid_group_missing_sub_all_routes(self):
        """A caller with a valid write group but NO 'sub' claim must be refused.

        MUTATION TARGET (FG2.1): delete the sub-check from
        _require_campaign_access => this test MUST FAIL.
        """
        for label, method, path, body, query in ALL_ROUTES:
            with self.subTest(route=label):
                # Build an event that legitimately passes the group check
                # (platform-admin is in _CAMPAIGN_WRITE_GROUPS and
                # _CAMPAIGN_READ_GROUPS), but omits 'sub' from the claims dict.
                ev = {
                    'httpMethod': method,
                    'path': path,
                    'queryStringParameters': query or {},
                    'body': None,
                    'requestContext': {
                        'authorizer': {
                            'claims': {
                                'cognito:groups': 'platform-admin',
                                # 'sub' intentionally absent
                            }
                        }
                    },
                }
                self._assert_403_no_ddb(ev, f"missing-sub {label}")

    def test_valid_group_empty_sub_all_routes(self):
        """A caller with a valid write group but an EMPTY 'sub' must be refused.

        MUTATION TARGET (FG2.1): delete the sub-check from
        _require_campaign_access => this test MUST FAIL.
        """
        for label, method, path, body, query in ALL_ROUTES:
            with self.subTest(route=label):
                ev = {
                    'httpMethod': method,
                    'path': path,
                    'queryStringParameters': query or {},
                    'body': None,
                    'requestContext': {
                        'authorizer': {
                            'claims': {
                                'cognito:groups': 'platform-admin',
                                'sub': '',   # present but empty
                            }
                        }
                    },
                }
                self._assert_403_no_ddb(ev, f"empty-sub {label}")

    # ---- FG3.1: sub-gate isolation — well-formed write bodies ----------------
    # FG2.1's subtests send body=None for write routes.  When the sub check is
    # deleted the production code calls json.loads(None) and raises TypeError,
    # so the subtest fails with 500, not because the 403 gate is gone.  That
    # is an accidental catch: a future refactor making body-parsing tolerant of
    # None would silently flip those subtests from "catches the mutation" to
    # "passes with the gate deleted".
    #
    # These two tests send a WELL-FORMED body for each write route so the
    # handler reaches a clean 200/201 when the sub check is absent.  They
    # therefore assert the sub gate BY ASSERTION (403 expected, 200/201
    # received under mutation) rather than by accident (500 from a parse
    # crash).
    #
    # MUTATION TARGET (FG3.1): delete the sub check from
    # _require_campaign_access (line ~962).
    # Under mutation every new subtest MUST fail with:
    #   "403 expected, 200/201 received"
    # If any fails with 500 the body is still malformed — fix it and retry.
    # -----------------------------------------------------------------------

    #: Write routes with well-formed bodies that produce 200/201 once the
    #: caller passes auth.  Used exclusively by the FG3.1 subtests below.
    #:
    #: Shapes:
    #:   POST /campaigns         body={'campaignName':...}          → 201
    #:   PUT  /campaigns         body={'campaignId':..., ...}       → 200
    #:   DELETE /campaigns       query={'campaignId':...} body=None → 200
    #:   POST /campaigns/assign  body={'campaignName':..., 'vehicles':[...]}
    #:                           + get_item mock returns template    → 200
    #:   DELETE /campaigns/assign body={'campaignName':..., 'vehicles':[...]}
    #:                            (unassign reads body, NOT query)  → 200
    _WRITE_ROUTES_WELLFORMED = [
        (
            "POST /campaigns",
            "POST", "/campaigns",
            {"campaignName": "fg31-campaign"},
            None,
        ),
        (
            "PUT /campaigns",
            "PUT", "/campaigns",
            {"campaignId": "fg31-campaign", "status": "ACTIVE"},
            None,
        ),
        (
            "DELETE /campaigns",
            "DELETE", "/campaigns",
            None,
            {"campaignId": "fg31-campaign"},
        ),
        (
            "POST /campaigns/assign",
            "POST", "/campaigns/assign",
            {"campaignName": "fg31-campaign", "vehicles": ["FG31-VIN"]},
            None,
        ),
        (
            "DELETE /campaigns/assign",
            "DELETE", "/campaigns/assign",
            # unassign_campaign reads campaignName + vehicles from body, not
            # from queryStringParameters — so the body must be well-formed.
            {"campaignName": "fg31-campaign", "vehicles": ["FG31-VIN"]},
            None,
        ),
    ]

    def _sub_absent_event(self, method, path, body, query):
        """Event with a valid write group but 'sub' absent from claims."""
        return {
            'httpMethod': method,
            'path': path,
            'queryStringParameters': query or {},
            'body': json.dumps(body) if body is not None else None,
            'requestContext': {
                'authorizer': {
                    'claims': {
                        'cognito:groups': 'platform-admin',
                        # 'sub' intentionally absent
                    }
                }
            },
        }

    def _sub_empty_event(self, method, path, body, query):
        """Event with a valid write group but 'sub' present and empty."""
        return {
            'httpMethod': method,
            'path': path,
            'queryStringParameters': query or {},
            'body': json.dumps(body) if body is not None else None,
            'requestContext': {
                'authorizer': {
                    'claims': {
                        'cognito:groups': 'platform-admin',
                        'sub': '',   # present but empty / whitespace
                    }
                }
            },
        }

    def test_missing_sub_write_routes_wellformed(self):
        """Write routes: valid group + well-formed body + NO sub → 403 + 0 DDB.

        FG3.1 MUTATION TARGET: delete the sub check from
        _require_campaign_access.  Every subtest MUST FAIL with:
            "403 expected, 200/201 received"
        (NOT with a 500 body-parse crash).

        POST /campaigns/assign additionally requires get_item to return a
        campaign template so the handler can proceed past its own validation.
        """
        for label, method, path, body, query in self._WRITE_ROUTES_WELLFORMED:
            with self.subTest(route=label):
                if method == 'POST' and '/assign' in path:
                    self.mock_table.get_item.return_value = {
                        'Item': {
                            'campaignId': 'fg31-campaign',
                            'campaignName': 'fg31-campaign',
                            'targetArn': 'template',
                            'status': 'ACTIVE',
                        }
                    }
                ev = self._sub_absent_event(method, path, body, query)
                self._assert_403_no_ddb(ev, f"missing-sub wellformed {label}")

    def test_empty_sub_write_routes_wellformed(self):
        """Write routes: valid group + well-formed body + EMPTY sub → 403 + 0 DDB.

        FG3.1 MUTATION TARGET: delete the sub check from
        _require_campaign_access.  Every subtest MUST FAIL with:
            "403 expected, 200/201 received"
        (NOT with a 500 body-parse crash).

        POST /campaigns/assign additionally requires get_item to return a
        campaign template so the handler can proceed past its own validation.
        """
        for label, method, path, body, query in self._WRITE_ROUTES_WELLFORMED:
            with self.subTest(route=label):
                if method == 'POST' and '/assign' in path:
                    self.mock_table.get_item.return_value = {
                        'Item': {
                            'campaignId': 'fg31-campaign',
                            'campaignName': 'fg31-campaign',
                            'targetArn': 'template',
                            'status': 'ACTIVE',
                        }
                    }
                ev = self._sub_empty_event(method, path, body, query)
                self._assert_403_no_ddb(ev, f"empty-sub wellformed {label}")


# ---------------------------------------------------------------------------
# MUTATION TARGET 1 (FG1.1 Verify step 2):
#   drop 'platform-admin' from _CAMPAIGN_WRITE_GROUPS
#   => every test in this class MUST FAIL
#
# Tests that assert platform-admin is admitted on writes.
# The CMS wizard/Campaign-Viewer/VehicleCampaignsTable surfaces use this group.
# ---------------------------------------------------------------------------

class TestPlatformAdminWrites(_AuthzBase):
    """platform-admin must be admitted for every write method.

    MUTATION TARGET 1: remove 'platform-admin' from _CAMPAIGN_WRITE_GROUPS and
    every test here MUST FAIL.  If any stay green after the mutation, the guard
    is asserting presence rather than write-set membership — fix it.
    """

    def test_platform_admin_must_pass_write_post_campaigns(self):
        ev = _event('POST', '/campaigns', ['platform-admin'], body={'campaignName': 'c'})
        self._assert_admitted(ev, 'platform-admin POST /campaigns')

    def test_platform_admin_must_pass_write_put_campaigns(self):
        ev = _event('PUT', '/campaigns', ['platform-admin'], body={'campaignId': 'c'})
        self._assert_admitted(ev, 'platform-admin PUT /campaigns')

    def test_platform_admin_must_pass_write_delete_campaigns(self):
        ev = _event('DELETE', '/campaigns', ['platform-admin'], query={'campaignId': 'c'})
        self._assert_admitted(ev, 'platform-admin DELETE /campaigns')

    def test_platform_admin_must_pass_write_post_assign(self):
        self.mock_table.get_item.return_value = {
            'Item': {'campaignId': 'c', 'campaignName': 'c', 'targetArn': 'template', 'status': 'ACTIVE'}
        }
        ev = _event('POST', '/campaigns/assign', ['platform-admin'],
                    body={'campaignName': 'c', 'vehicles': ['V1']})
        self._assert_admitted(ev, 'platform-admin POST /campaigns/assign')

    def test_platform_admin_must_pass_write_delete_assign(self):
        ev = _event('DELETE', '/campaigns/assign', ['platform-admin'],
                    query={'campaignId': 'c', 'vehicleId': 'V1'})
        self._assert_admitted(ev, 'platform-admin DELETE /campaigns/assign')


# ---------------------------------------------------------------------------
# fleet-operator must be admitted on writes (Decision 3)
# FG2.2 (S1): extended to all five write route/method pairs to match the
# coverage TestPlatformAdminWrites already has.
# ---------------------------------------------------------------------------

class TestFleetOperatorWrites(_AuthzBase):
    """fleet-operator must be admitted for all five write routes.

    FG2.2: originally covered 3 of 5 write routes; now extended to POST/DELETE
    on /campaigns/assign so the coverage matches TestPlatformAdminWrites.
    Because the guard is pre-branch, this is safe today regardless, but the
    reviewer's reasoning holds: if the guard ever moves per-arm the omitted
    routes would silently lose coverage.

    Every fleet-operator event carries ``custom:fleetIds='fo-fleet'`` so that
    ``_caller_fleet_id`` can resolve the caller's fleet identity.  Without it
    the guard raises (malformed claim → refused) and the 403 is indistinguishable
    from an authz-gate refusal — the test would pass for the wrong reason.

    PUT /campaigns and DELETE /campaigns both call the ownership guard, which
    reads the row from DynamoDB before acting.  The mock is configured to return
    a fleet-owned row so the guard permits the operation; the test is checking
    that the authz gate admits fleet-operator, not that the caller owns the row.
    """

    _FLEET_ID = 'fo-fleet'

    def setUp(self):
        super().setUp()
        # PUT /campaigns and DELETE /campaigns read the row first via get_item.
        # Return a row owned by _FLEET_ID so the ownership guard permits the
        # call.  Tests here cover the authz gate; ownership semantics are in
        # test_campaign_ownership.py.
        self.mock_table.get_item.return_value = {
            'Item': {
                'campaignId': 'c',
                'campaignName': 'c',
                'owner': f'fleet:{self._FLEET_ID}',
                'status': 'ACTIVE',
            }
        }

    def test_fleet_operator_must_pass_write_post_campaigns(self):
        ev = _event('POST', '/campaigns', ['fleet-operator'],
                    fleet_ids=self._FLEET_ID, body={'campaignName': 'c'})
        self._assert_admitted(ev, 'fleet-operator POST /campaigns')

    def test_fleet_operator_must_pass_write_put_campaigns(self):
        # PUT /campaigns reads the row and checks ownership; mock returns a
        # fleet-owned row so the guard passes.
        ev = _event('PUT', '/campaigns', ['fleet-operator'],
                    fleet_ids=self._FLEET_ID, body={'campaignId': 'c'})
        self._assert_admitted(ev, 'fleet-operator PUT /campaigns')

    def test_fleet_operator_must_pass_write_delete_campaigns(self):
        # DELETE /campaigns reads the row and checks ownership; same setup.
        ev = _event('DELETE', '/campaigns', ['fleet-operator'],
                    fleet_ids=self._FLEET_ID, query={'campaignId': 'c'})
        self._assert_admitted(ev, 'fleet-operator DELETE /campaigns')

    def test_fleet_operator_must_pass_write_post_assign(self):
        """FG2.2: fleet-operator POST /campaigns/assign (was missing).

        The VIN-fleet ownership check (FGS1.1) is satisfied by mocking
        _vehicles_ddb to return the caller's own fleet for 'V1'.
        """
        self.mock_table.get_item.return_value = {
            'Item': {'campaignId': 'c', 'campaignName': 'c', 'targetArn': 'template', 'status': 'ACTIVE'}
        }
        mock_veh = MagicMock()
        mock_veh.query.return_value = {'Items': [{'vehicleId': 'vehicle-v1', 'vin': 'V1'}]}
        mock_veh.get_item.return_value = {'Item': {'vehicleId': 'vehicle-v1', 'fleetId': self._FLEET_ID}}
        with patch.object(api, '_vehicles_ddb', mock_veh):
            ev = _event('POST', '/campaigns/assign', ['fleet-operator'],
                        fleet_ids=self._FLEET_ID,
                        body={'campaignName': 'c', 'vehicles': ['V1']})
            self._assert_admitted(ev, 'fleet-operator POST /campaigns/assign')

    def test_fleet_operator_must_pass_write_delete_assign(self):
        """FG2.2: fleet-operator DELETE /campaigns/assign (was missing).

        unassign_campaign reads campaignName + vehicles from the body.  The
        per-VIN row (key 'c-V1') is not in the mock, so get_item returns no
        Item; unassign skips that VIN (fail-closed) and returns 200 with
        removed=[].  _assert_admitted passes on any non-403.
        """
        # Override so the per-VIN get_item returns no Item (missing row → skip).
        self.mock_table.get_item.return_value = {}
        ev = _event('DELETE', '/campaigns/assign', ['fleet-operator'],
                    fleet_ids=self._FLEET_ID,
                    body={'campaignName': 'c', 'vehicles': ['V1']})
        self._assert_admitted(ev, 'fleet-operator DELETE /campaigns/assign')


# ---------------------------------------------------------------------------
# connected-services must still be admitted on writes (CS portal path; Task 5.1)
# FG2.2 (S1): extended to all five write route/method pairs.
# ---------------------------------------------------------------------------

class TestConnectedServicesWrites(_AuthzBase):
    """connected-services must be admitted for all five write routes (CS portal).

    FG2.2: originally covered 2 of 5 write routes (POST /campaigns and
    POST /campaigns/assign); now extended to PUT, DELETE /campaigns and
    DELETE /campaigns/assign.
    """

    def test_connected_services_must_pass_write_post_campaigns(self):
        ev = _event('POST', '/campaigns', ['connected-services'], body={'campaignName': 'c'})
        self._assert_admitted(ev, 'connected-services POST /campaigns')

    def test_connected_services_must_pass_write_put_campaigns(self):
        """FG2.2: connected-services PUT /campaigns (was missing)."""
        ev = _event('PUT', '/campaigns', ['connected-services'], body={'campaignId': 'c'})
        self._assert_admitted(ev, 'connected-services PUT /campaigns')

    def test_connected_services_must_pass_write_delete_campaigns(self):
        """FG2.2: connected-services DELETE /campaigns (was missing)."""
        ev = _event('DELETE', '/campaigns', ['connected-services'], query={'campaignId': 'c'})
        self._assert_admitted(ev, 'connected-services DELETE /campaigns')

    def test_connected_services_must_pass_write_post_assign(self):
        self.mock_table.get_item.return_value = {
            'Item': {'campaignId': 'c', 'campaignName': 'c', 'targetArn': 'template', 'status': 'ACTIVE'}
        }
        ev = _event('POST', '/campaigns/assign', ['connected-services'],
                    body={'campaignName': 'c', 'vehicles': ['V1']})
        self._assert_admitted(ev, 'connected-services POST /campaigns/assign')

    def test_connected_services_must_pass_write_delete_assign(self):
        """FG2.2: connected-services DELETE /campaigns/assign (was missing)."""
        ev = _event('DELETE', '/campaigns/assign', ['connected-services'],
                    query={'campaignId': 'c', 'vehicleId': 'V1'})
        self._assert_admitted(ev, 'connected-services DELETE /campaigns/assign')

    def test_connected_services_must_pass_read_get_campaigns(self):
        ev = _event('GET', '/campaigns', ['connected-services'])
        self._assert_admitted(ev, 'connected-services GET /campaigns')


# ---------------------------------------------------------------------------
# fleet-viewer: read OK, write refused
# ---------------------------------------------------------------------------

class TestFleetViewerReadOnly(_AuthzBase):
    """fleet-viewer is admitted for reads, refused for writes."""

    def test_fleet_viewer_must_pass_read_get_campaigns(self):
        ev = _event('GET', '/campaigns', ['fleet-viewer'])
        self._assert_admitted(ev, 'fleet-viewer GET /campaigns')

    def test_fleet_viewer_must_pass_read_get_collection_scheme(self):
        ev = _event('GET', '/campaigns/collection-scheme', ['fleet-viewer'],
                    query={'campaignId': 'c'})
        self._assert_admitted(ev, 'fleet-viewer GET /campaigns/collection-scheme')

    # ---- MUTATION TARGET 2 -------------------------------------------------
    # FG1.1 Verify step 3: add 'fleet-viewer' to _CAMPAIGN_WRITE_GROUPS
    # => every test_fleet_viewer_must_403_write_* test MUST FAIL
    # -------------------------------------------------------------------------

    def test_fleet_viewer_must_403_write_post(self):
        """fleet-viewer must NOT be admitted on POST.

        MUTATION TARGET 2: add 'fleet-viewer' to _CAMPAIGN_WRITE_GROUPS and
        this test MUST FAIL.
        """
        ev = _event('POST', '/campaigns', ['fleet-viewer'], body={'campaignName': 'c'})
        self._assert_403_no_ddb(ev, 'fleet-viewer POST /campaigns')

    def test_fleet_viewer_must_403_write_put(self):
        ev = _event('PUT', '/campaigns', ['fleet-viewer'], body={'campaignId': 'c'})
        self._assert_403_no_ddb(ev, 'fleet-viewer PUT /campaigns')

    def test_fleet_viewer_must_403_write_delete(self):
        ev = _event('DELETE', '/campaigns', ['fleet-viewer'], query={'campaignId': 'c'})
        self._assert_403_no_ddb(ev, 'fleet-viewer DELETE /campaigns')

    def test_fleet_viewer_must_403_write_post_assign(self):
        ev = _event('POST', '/campaigns/assign', ['fleet-viewer'],
                    body={'campaignName': 'c', 'vehicles': ['V1']})
        self._assert_403_no_ddb(ev, 'fleet-viewer POST /campaigns/assign')

    def test_fleet_viewer_must_403_write_delete_assign(self):
        ev = _event('DELETE', '/campaigns/assign', ['fleet-viewer'],
                    query={'campaignId': 'c', 'vehicleId': 'V1'})
        self._assert_403_no_ddb(ev, 'fleet-viewer DELETE /campaigns/assign')


# ---------------------------------------------------------------------------
# MUTATION TARGET 3 (FG1.1 Verify step 4):
#   add 'fleet-guest' to _CAMPAIGN_READ_GROUPS
#   => every test in this class MUST FAIL
#
# fleet-guest is not in any allowed set on any method.
# ---------------------------------------------------------------------------

class TestFleetGuestRefused(_AuthzBase):
    """fleet-guest must be refused on every route and method.

    MUTATION TARGET 3: add 'fleet-guest' to _CAMPAIGN_READ_GROUPS and every
    test here MUST FAIL.
    """

    def test_fleet_guest_must_403_read_all(self):
        """fleet-guest must be refused on GET routes.

        MUTATION TARGET 3: add 'fleet-guest' to _CAMPAIGN_READ_GROUPS and
        this test MUST FAIL.
        """
        for label, method, path, body, query in READ_ROUTES:
            with self.subTest(route=label):
                ev = _event(method, path, ['fleet-guest'], body=body, query=query)
                self._assert_403_no_ddb(ev, f"fleet-guest {label}")

    def test_fleet_guest_must_403_write_all(self):
        for label, method, path, body, query in WRITE_ROUTES:
            with self.subTest(route=label):
                ev = _event(method, path, ['fleet-guest'], body=body, query=query)
                self._assert_403_no_ddb(ev, f"fleet-guest {label}")


if __name__ == '__main__':
    unittest.main()
