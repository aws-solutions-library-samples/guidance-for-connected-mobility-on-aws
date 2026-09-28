"""Authorization regression tests for POST /api/v1/fleet-campaigns/{assign,status}.

Issue: 2026-09-04-fleet-campaigns-post-routes-missing-authz.

Third member of the same family, and the first to be scoped per-fleet rather
than admin-only:

    :7632  GET  /api/v1/fleet-campaigns          — read
    :7663  POST /api/v1/fleet-campaigns/assign   — this test
    :7738  POST /api/v1/fleet-campaigns/status   — this test

Both POSTs previously ran route-match -> body presence check -> DynamoDB write
with **no authorization call at all**. `/assign` writes a fleet-level campaign
record for a caller-supplied `fleetId`, then fans out one vehicle-level record
per vehicle in that fleet with `status=RUNNING`. `/status` sets an arbitrary
status on any `campaignId` and every child record under it. Enumerating
`fleetId` is trivial — the sibling GET returns fleet lists.

## Why per-fleet, not admin-only

The sibling `fleet-actions` approve/reject fix (issue
2026-09-03-fleet-actions-approve-reject-missing-authz) chose `is_admin` because
the action-queue schema could not support scoping: recall and rebalancing rows
carry no `vehicleId`, and no row carries `fleetId`.

That constraint does not apply here, which is why this fix is different:

  - `/assign` takes `fleetId` **in the request body**.
  - `/status` derives the fleet from the campaign id via the same
    `campaign_id.split('-fleet:')` the route already uses for its child query,
    so the authz check and the business logic agree by construction rather
    than by a second, drifting convention.

So a `fleet-operator` may drive campaigns on fleets in their own
`custom:fleetIds` and nowhere else. Admin remains unscoped.

## Fail-closed properties asserted here

  - groupless token                                  -> 403 (fail-open closure, index.py:1230)
  - fleet-viewer / fleet-guest (read-only per :1281)  -> 403
  - fleet-operator on ANOTHER fleet                   -> 403  <- the cross-fleet write
  - fleet-operator with NO custom:fleetIds            -> 403  <- absent claim must not widen
  - campaignId with no derivable fleet, non-admin     -> 403  <- underivable != permitted
  - fleet-operator on their OWN fleet                 -> NOT 403 (positive control)
  - platform-admin                                    -> NOT 403 (positive control)

The two positive controls are what stop this from being a test that would pass
against a blanket deny. Without them, `return 403` at the top of both routes
would be green.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch


# Import-time env stubbing so index.py's module-init DDB/clients don't need AWS.
# Mirrors test_fleet_actions_approve_reject_authz.py.
os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('REDIS_ENDPOINT', '')
os.environ.setdefault('VEHICLES_TABLE_NAME', 'cms-test-storage-vehicles')
os.environ.setdefault('MAINTENANCE_ALERTS_TABLE_NAME', 'cms-test-storage-maintenance-alerts')
os.environ.setdefault('SAFETY_EVENTS_TABLE_NAME', 'cms-test-storage-safety-events')
os.environ.setdefault('FLEETS_TABLE_NAME', 'cms-test-storage-fleets')
os.environ.setdefault('FLEET_ENROLLMENT_TABLE_NAME', 'cms-test-storage-fleet-enrollment')
os.environ.setdefault('SERVICE_HISTORY_TABLE_NAME', 'cms-test-storage-service-history')
os.environ.setdefault('DASHBOARD_METRICS_CACHE_TABLE', 'cms-test-storage-dashboard-metrics-cache')
os.environ.setdefault('DRIVERS_TABLE_NAME', 'cms-test-storage-drivers')
os.environ.setdefault('DEPLOYMENT_STAGE', 'test')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ASSIGN = '/api/v1/fleet-campaigns/assign'
STATUS = '/api/v1/fleet-campaigns/status'

# The fleet the caller legitimately holds, and one they do not.
OWN_FLEET = 'FLEET#OWNED'
OTHER_FLEET = 'FLEET#SOMEONE-ELSE'


def _event(path: str, claims: dict, body: dict | None) -> dict:
    return {
        'httpMethod': 'POST',
        'path': path,
        'queryStringParameters': None,
        'body': json.dumps(body) if body is not None else None,
        'requestContext': {'authorizer': {'claims': claims}},
    }


def _claims(groups: str = '', fleet_ids: str = '') -> dict:
    """Minimal Cognito claims. Omit `groups` for a groupless token."""
    c: dict = {'sub': 'test-user', 'email': 'user@example.com', 'custom:tenantId': 'test'}
    if groups:
        c['cognito:groups'] = groups
    if fleet_ids:
        c['custom:fleetIds'] = fleet_ids
    return c


class _Base(unittest.TestCase):
    def setUp(self):
        if 'index' in sys.modules:
            del sys.modules['index']
        import index
        self.index = index

        # Empty query/scan so an authorized caller reaches a clean terminal
        # response rather than a 500 — /assign 404s on the missing template,
        # /status completes with zero children. Either proves the guard did
        # not fire.
        def mock_table(_name):
            t = MagicMock()
            t.get_item.return_value = {}
            t.put_item.return_value = {}
            t.update_item.return_value = {}
            t.scan.return_value = {'Items': []}
            t.query.return_value = {'Items': []}
            return t

        p = patch.object(self.index, 'dynamodb')
        m = p.start()
        m.Table.side_effect = mock_table
        m.meta.client.scan.return_value = {'Count': 0}
        self.addCleanup(p.stop)

    def _assign(self, claims: dict, fleet_id: str = OWN_FLEET) -> dict:
        return self.index.handler(
            _event(ASSIGN, claims, {'fleetId': fleet_id, 'campaignName': 'camp-a'}), None
        )

    def _status(self, claims: dict, campaign_id: str = f'camp-a-fleet:{OWN_FLEET}') -> dict:
        return self.index.handler(
            _event(STATUS, claims, {'campaignId': campaign_id, 'status': 'SUSPENDED'}), None
        )


class AssignAuthzTests(_Base):
    """POST /fleet-campaigns/assign — per-fleet scoped write."""

    def test_groupless_denied(self):
        resp = self._assign(_claims())
        self.assertEqual(resp['statusCode'], 403,
                         f"groupless token must not assign; got {resp.get('statusCode')}: {resp.get('body')}")

    def test_fleet_viewer_denied(self):
        """fleet-viewer is read-only (is_read_only, index.py:1281)."""
        resp = self._assign(_claims('fleet-viewer', OWN_FLEET))
        self.assertEqual(resp['statusCode'], 403,
                         f"fleet-viewer is read-only; got {resp.get('statusCode')}: {resp.get('body')}")

    def test_fleet_guest_denied(self):
        resp = self._assign(_claims('fleet-guest', OWN_FLEET))
        self.assertEqual(resp['statusCode'], 403,
                         f"fleet-guest must not assign; got {resp.get('statusCode')}: {resp.get('body')}")

    def test_operator_on_another_fleet_denied(self):
        """The cross-fleet write. Operator holds OWN_FLEET, targets OTHER_FLEET."""
        resp = self._assign(_claims('fleet-operator', OWN_FLEET), fleet_id=OTHER_FLEET)
        self.assertEqual(resp['statusCode'], 403,
                         f"operator must not assign into a fleet they do not hold; "
                         f"got {resp.get('statusCode')}: {resp.get('body')}")

    def test_operator_without_fleet_ids_denied(self):
        """Absent custom:fleetIds must not widen scope — CMS P0 shape 2."""
        resp = self._assign(_claims('fleet-operator'))
        self.assertEqual(resp['statusCode'], 403,
                         f"operator with no custom:fleetIds must be denied; "
                         f"got {resp.get('statusCode')}: {resp.get('body')}")

    def test_operator_on_own_fleet_allowed(self):
        """Positive control — scoping permits, it does not blanket-deny."""
        resp = self._assign(_claims('fleet-operator', OWN_FLEET))
        self.assertNotEqual(resp['statusCode'], 403,
                            f"operator must be allowed on their own fleet; got 403: {resp.get('body')}")
        self.assertEqual(resp['statusCode'], 404,
                         f"with no template in DDB, expected 404 Template not found; "
                         f"got {resp.get('statusCode')}: {resp.get('body')}")

    def test_platform_admin_allowed_any_fleet(self):
        """Positive control — admin is unscoped."""
        resp = self._assign(_claims('platform-admin'), fleet_id=OTHER_FLEET)
        self.assertNotEqual(resp['statusCode'], 403,
                            f"platform-admin must not 403 on assign; got: {resp.get('body')}")


class StatusAuthzTests(_Base):
    """POST /fleet-campaigns/status — fleet derived from the campaign id."""

    def test_groupless_denied(self):
        resp = self._status(_claims())
        self.assertEqual(resp['statusCode'], 403,
                         f"groupless token must not change campaign status; "
                         f"got {resp.get('statusCode')}: {resp.get('body')}")

    def test_fleet_viewer_denied(self):
        resp = self._status(_claims('fleet-viewer', OWN_FLEET))
        self.assertEqual(resp['statusCode'], 403,
                         f"fleet-viewer is read-only; got {resp.get('statusCode')}: {resp.get('body')}")

    def test_operator_on_another_fleet_denied(self):
        resp = self._status(_claims('fleet-operator', OWN_FLEET),
                            campaign_id=f'camp-a-fleet:{OTHER_FLEET}')
        self.assertEqual(resp['statusCode'], 403,
                         f"operator must not restatus another fleet's campaign; "
                         f"got {resp.get('statusCode')}: {resp.get('body')}")

    def test_underivable_fleet_denied_for_non_admin(self):
        """A campaignId with no '-fleet:' segment yields no fleet.

        Underivable must mean denied, not permitted. Without this the guard
        could be bypassed by passing a bare campaign id.
        """
        resp = self._status(_claims('fleet-operator', OWN_FLEET), campaign_id='camp-a')
        self.assertEqual(resp['statusCode'], 403,
                         f"a campaignId with no derivable fleet must be denied for non-admins; "
                         f"got {resp.get('statusCode')}: {resp.get('body')}")

    def test_operator_on_own_fleet_allowed(self):
        """Positive control."""
        resp = self._status(_claims('fleet-operator', OWN_FLEET))
        self.assertNotEqual(resp['statusCode'], 403,
                            f"operator must be allowed on their own fleet's campaign; got 403: {resp.get('body')}")

    def test_platform_admin_allowed(self):
        """Positive control — admin is unscoped, including underivable ids."""
        resp = self._status(_claims('platform-admin'), campaign_id='camp-a')
        self.assertNotEqual(resp['statusCode'], 403,
                            f"platform-admin must not 403 on status; got: {resp.get('body')}")

    def test_decoy_fleet_segment_uses_last_occurrence(self):
        """rsplit guard: a campaignId like 'camp-a-fleet:DECOY-fleet:REAL' must authorise
        against REAL (the last '-fleet:' segment), not DECOY (the first).

        FG7.3 fix: split('-fleet:') would extract the decoy; rsplit('-fleet:', 1)
        extracts the real trailing fleet.
        """
        decoy = 'FLEET#DECOY'
        real_fleet = OWN_FLEET  # the fleet the caller actually holds

        # campaignId built as if campaign_name contains a decoy '-fleet:' substring
        campaign_id_with_decoy = f'camp-a-fleet:{decoy}-fleet:{real_fleet}'

        # Caller holds only OWN_FLEET — should be ALLOWED because the LAST '-fleet:'
        # segment is OWN_FLEET.
        resp_own = self._status(_claims('fleet-operator', real_fleet),
                                campaign_id=campaign_id_with_decoy)
        self.assertNotEqual(resp_own['statusCode'], 403,
                            f"operator should be authorised against the last '-fleet:' segment "
                            f"(their own fleet); got 403: {resp_own.get('body')}")

        # Caller holds only DECOY — should be DENIED because DECOY is not the last segment.
        resp_decoy = self._status(_claims('fleet-operator', decoy),
                                  campaign_id=campaign_id_with_decoy)
        self.assertEqual(resp_decoy['statusCode'], 403,
                         f"operator holding only the decoy fleet should be denied; "
                         f"got {resp_decoy.get('statusCode')}: {resp_decoy.get('body')}")


if __name__ == '__main__':
    unittest.main()
