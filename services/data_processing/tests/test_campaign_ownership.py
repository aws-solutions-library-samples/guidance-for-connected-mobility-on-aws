"""
Red-phase tests for the campaign ownership model.
Spec: .kiro/specs/2026-09-15-cms-cs-campaign-ownership/
Task: 1.2

These tests MUST FAIL until Group 4 ships the ownership guard.
They must fail because the guard is missing, NOT because of import errors.

Owner vocabulary (decisions.md):
  "oem"          — CS / OEM-authored campaign
  "fleet:<id>"   — Fleet-authored campaign (CMS operator)
  "platform"     — Auto-ensure baseline written by _ensure_telemetry_campaign
  absent         — Legacy rows (26 live rows) — treated as fail-closed (not fleet-owned)

Five cases:
  (a) every campaign write sets a non-empty `owner`
  (b) fleet caller deleting owner=="oem" row is refused (403)
  (c) fleet caller deleting its own owner=="fleet:<id>" row succeeds (200)
  (d) row with absent `owner` is refused deletion by a fleet caller (fail-closed)
  (e) owner=="platform" row is refused deletion by a fleet caller (403)
"""

import json
import sys
import os
import unittest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Path setup — allow imports from the lambda directory without a package install
# ---------------------------------------------------------------------------
LAMBDA_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'lambda')
)
if LAMBDA_DIR not in sys.path:
    sys.path.insert(0, LAMBDA_DIR)


def _make_event(method, path, body=None, query=None, groups=None):
    """Minimal API-GW-shaped event for the data_processing handler."""
    if groups is None:
        groups = ['platform-admin']
    return {
        'httpMethod': method,
        'path': path,
        'queryStringParameters': query or {},
        'body': json.dumps(body) if body else None,
        'requestContext': {
            'authorizer': {
                'claims': {
                    'cognito:groups': ','.join(groups),
                    'sub': 'test-user-id',
                }
            }
        },
    }


def _fleet_event(method, path, fleet_id, body=None, query=None):
    """Event carrying fleet-operator claims with a valid custom:fleetIds.

    Uses the real dispatcher-admitted caller shape: group='fleet-operator' plus
    custom:fleetIds=fleet_id.  The old pattern ('fleet-<id>' as a Cognito group)
    is in neither _CAMPAIGN_WRITE_GROUPS nor _CAMPAIGN_READ_GROUPS, so it was
    refused by the dispatcher before reaching any route handler — tests that
    called route functions directly with that shape were verifying against a
    caller that can never arrive (FG13.3).
    """
    return _make_event(
        method, path, body=body, query=query,
        groups=['fleet-operator'],
    ) | {
        'requestContext': {
            'authorizer': {
                'claims': {
                    'cognito:groups': 'fleet-operator',
                    'sub': 'test-user-id',
                    'custom:fleetIds': fleet_id,
                }
            }
        }
    }


# ---------------------------------------------------------------------------
# Import the module under test — must succeed or every case is mis-named.
# We patch out all DynamoDB resource calls at import time.
# ---------------------------------------------------------------------------
with patch.dict(os.environ, {
    'SIGNAL_CATALOG_TABLE': 'test-signal-catalog',
    'DATA_SOURCE_CONFIGS_TABLE': 'test-data-sources',
    'MANIFESTS_BUCKET': 'test-manifests',
    'CAMPAIGNS_TABLE': 'test-campaigns',
    'DEPLOYMENT_STAGE': 'test',
}):
    import data_processing_api as api  # noqa: E402  — must follow env patch


# ---------------------------------------------------------------------------
# Case (a): every campaign write sets a non-empty `owner`
# ---------------------------------------------------------------------------
class TestCampaignWriteSetsOwner(unittest.TestCase):
    """
    (a) Every campaign write (create, assign) must persist a non-empty `owner`
    attribute on the DynamoDB row.  Currently neither write path sets `owner`,
    so the captured put_item calls carry no such key and the assertion fails.
    """

    def setUp(self):
        self.mock_table = MagicMock()
        self.mock_table.get_item.return_value = {
            'Item': {
                'campaignId': 'test-campaign',
                'campaignName': 'test-campaign',
                'targetArn': 'template',
                'status': 'ACTIVE',
            }
        }
        self.mock_table.put_item.return_value = {}
        self.mock_table.scan.return_value = {'Items': []}
        self.patcher = patch.object(api, 'campaigns_table', self.mock_table)
        self.patcher.start()

        # `_vehicles_ddb` must be patched for the assign path (added 2026-09-20).
        # `assign_campaign` now resolves every entry in `vehicles` against
        # `vin-index` and REFUSES to write a row for a VIN it cannot resolve —
        # the guard from
        # issues/2026-09-20-cs-quick-assign-writes-vehicleid-keyed-campaign-row/.
        # Without this patch the resolver hits an unconfigured boto3 resource,
        # fails closed, and the assign writes nothing, so the owner assertion
        # below never runs. This test is about `owner`, not about the guard, so
        # the mock simply resolves whatever VIN it is handed.
        self.mock_vehicles = MagicMock()
        self.mock_vehicles.query.return_value = {
            'Items': [{'vehicleId': 'vehicle-VIN001', 'vin': 'VIN001'}]
        }
        self.patcher_vehicles = patch.object(api, '_vehicles_ddb', self.mock_vehicles)
        self.patcher_vehicles.start()

    def tearDown(self):
        self.patcher.stop()
        self.patcher_vehicles.stop()

    def test_every_campaign_write_sets_nonempty_owner(self):
        """
        create_campaign and assign_campaign must both persist a non-empty `owner`.

        Exercises both write paths in one test so there is exactly one test name
        per Accept case.  Fails as soon as the first write is missing `owner`.
        """
        # --- create path ---
        api.create_campaign(_make_event('POST', '/campaigns', body={
            'campaignName': 'my-oem-campaign',
            'type': 'TIME_BASED',
            'periodMs': 30000,
        }))
        self.mock_table.put_item.assert_called_once()
        created_item = self.mock_table.put_item.call_args[1]['Item']
        self.assertIn('owner', created_item,
                      "create_campaign must write an 'owner' attribute — currently missing")
        self.assertTrue(created_item['owner'],
                        "create_campaign must write a non-empty 'owner'")

        # --- assign path ---
        self.mock_table.put_item.reset_mock()
        api.assign_campaign(_make_event('POST', '/campaigns/assign', body={
            'campaignName': 'test-campaign',
            'vehicles': ['VIN001'],
        }))
        self.assertGreater(self.mock_table.put_item.call_count, 0,
                           "assign_campaign must write at least one row")
        for c in self.mock_table.put_item.call_args_list:
            item = c[1]['Item']
            self.assertIn('owner', item,
                          f"Assignment row {item.get('campaignId')} missing 'owner'")
            self.assertTrue(item['owner'],
                            f"Assignment row {item.get('campaignId')} has empty 'owner'")


# ---------------------------------------------------------------------------
# Case (b): fleet caller deleting owner=="oem" row is refused
# ---------------------------------------------------------------------------
class TestFleetCallerCannotDeleteOemCampaign(unittest.TestCase):
    """
    (b) A fleet caller must receive 403 when trying to delete a row whose
    `owner` is "oem".  Currently delete_campaign has no ownership check, so it
    will delete unconditionally and return 200 — the assertion on 403 fails.
    """

    def setUp(self):
        self.mock_table = MagicMock()
        self.mock_table.get_item.return_value = {
            'Item': {
                'campaignId': 'oem-campaign',
                'campaignName': 'oem-campaign',
                'owner': 'oem',
                'targetArn': 'template',
                'status': 'ACTIVE',
            }
        }
        self.mock_table.scan.return_value = {'Items': []}
        self.patcher = patch.object(api, 'campaigns_table', self.mock_table)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_fleet_caller_refused_for_oem_owned_campaign(self):
        """DELETE /campaigns?campaignId=oem-campaign by a fleet caller must return 403."""
        event = _fleet_event(
            'DELETE', '/campaigns', fleet_id='fleet-alpha',
            query={'campaignId': 'oem-campaign'}
        )
        resp = api.delete_campaign(event)
        self.assertEqual(resp['statusCode'], 403,
                         f"Expected 403 but got {resp['statusCode']}; "
                         "ownership guard is not implemented yet")


# ---------------------------------------------------------------------------
# Case (c): fleet caller deleting its own fleet-owned row succeeds
# ---------------------------------------------------------------------------
class TestFleetCallerCanDeleteOwnCampaign(unittest.TestCase):
    """
    (c) A fleet caller must be allowed to delete a row whose `owner` matches
    its own fleet identity ("fleet:<fleetId>").

    The ownership guard must READ the row first (get_item) to verify ownership
    before deleting.  Currently delete_campaign calls delete_item directly
    without any get_item — no ownership check exists at all.

    Red-phase failure: get_item is never called today, so the assertion that
    the guard read the row before permitting the delete fails.  This confirms
    the guard is missing rather than vacuously passing a 200 check.
    """

    def setUp(self):
        self.mock_table = MagicMock()
        self.fleet_id = 'fleet-alpha'
        self.mock_table.get_item.return_value = {
            'Item': {
                'campaignId': 'fleet-campaign',
                'campaignName': 'fleet-campaign',
                'owner': f'fleet:{self.fleet_id}',
                'targetArn': 'template',
                'status': 'ACTIVE',
            }
        }
        self.mock_table.scan.return_value = {'Items': []}
        self.patcher = patch.object(api, 'campaigns_table', self.mock_table)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_fleet_caller_allowed_to_delete_own_campaign(self):
        """
        DELETE of owner=="fleet:<id>" by the matching fleet caller must succeed (200)
        AND the guard must have read the row (get_item) to verify ownership first.

        Red-phase failure: delete_campaign never calls get_item today, so the
        assertion on get_item.call_count fails — confirming the guard is absent.
        """
        event = _fleet_event(
            'DELETE', '/campaigns', fleet_id=self.fleet_id,
            query={'campaignId': 'fleet-campaign'}
        )
        resp = api.delete_campaign(event)
        # The guard must have read the row to check ownership before permitting the delete.
        # Currently get_item is never called — this assertion is what makes the test red.
        self.assertGreater(
            self.mock_table.get_item.call_count, 0,
            "The ownership guard must call get_item to read the row's `owner` "
            "before permitting a fleet delete; currently delete_campaign calls "
            "delete_item without any ownership read — guard is not implemented yet"
        )
        # And the actual delete must succeed for the fleet's own campaign.
        self.assertEqual(resp['statusCode'], 200,
                         f"Fleet caller must be allowed to delete its own campaign; "
                         f"got {resp['statusCode']}")


# ---------------------------------------------------------------------------
# Case (d): absent `owner` is refused deletion by a fleet caller (fail-closed)
# ---------------------------------------------------------------------------
class TestAbsentOwnerRefusedByFleetCaller(unittest.TestCase):
    """
    (d) Rows with no `owner` attribute (the 26 legacy rows) must be treated as
    NOT fleet-owned and refused for deletion by a fleet caller.  This is the
    fail-closed decision: permitting absent-owner would make the migration
    window itself the security hole.

    Currently delete_campaign has no ownership check, so it will delete
    unconditionally and return 200 — the assertion on 403 fails.

    This case MUST NOT be weakened.  decisions.md § "Fail closed on absent
    `owner`" records the explicit decision.
    """

    def setUp(self):
        self.mock_table = MagicMock()
        # Row has no `owner` key — simulates a legacy pre-backfill row
        self.mock_table.get_item.return_value = {
            'Item': {
                'campaignId': 'legacy-campaign',
                'campaignName': 'legacy-campaign',
                'targetArn': 'template',
                'status': 'ACTIVE',
                # Deliberately no 'owner' key
            }
        }
        self.mock_table.scan.return_value = {'Items': []}
        self.patcher = patch.object(api, 'campaigns_table', self.mock_table)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_absent_owner_refused_by_fleet_caller(self):
        """DELETE of a row with no `owner` by a fleet caller must return 403 (fail-closed)."""
        event = _fleet_event(
            'DELETE', '/campaigns', fleet_id='fleet-alpha',
            query={'campaignId': 'legacy-campaign'}
        )
        resp = api.delete_campaign(event)
        self.assertEqual(resp['statusCode'], 403,
                         f"Expected 403 (fail-closed on absent owner) but got "
                         f"{resp['statusCode']}; ownership guard is not implemented yet. "
                         "Per decisions.md this must NOT be weakened.")


# ---------------------------------------------------------------------------
# Case (e): owner=="platform" row is refused deletion by a fleet caller
# ---------------------------------------------------------------------------
class TestPlatformOwnerRefusedByFleetCaller(unittest.TestCase):
    """
    (e) The auto-ensure baseline written by _ensure_telemetry_campaign carries
    `owner == "platform"`.  A fleet caller must not be able to delete it — the
    platform baseline is not fleet-authored and the fleet has no relationship
    to it that grants delete authority.

    Currently delete_campaign has no ownership check, so it will delete
    unconditionally and return 200 — the assertion on 403 fails.
    """

    def setUp(self):
        self.mock_table = MagicMock()
        self.mock_table.get_item.return_value = {
            'Item': {
                'campaignId': 'cms-fleet-telemetry-30s-VIN001',
                'campaignName': 'cms-fleet-telemetry-30s',
                'owner': 'platform',
                'targetArn': 'vehicle:VIN001',
                'status': 'RUNNING',
            }
        }
        self.mock_table.scan.return_value = {'Items': []}
        self.patcher = patch.object(api, 'campaigns_table', self.mock_table)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_platform_owner_refused_by_fleet_caller(self):
        """DELETE of owner=="platform" row by a fleet caller must return 403."""
        event = _fleet_event(
            'DELETE', '/campaigns', fleet_id='fleet-alpha',
            query={'campaignId': 'cms-fleet-telemetry-30s-VIN001'}
        )
        resp = api.delete_campaign(event)
        self.assertEqual(resp['statusCode'], 403,
                         f"Expected 403 but got {resp['statusCode']}; "
                         "ownership guard for 'platform' owner is not implemented yet")


if __name__ == '__main__':
    unittest.main()



# ---------------------------------------------------------------------------
# Case (f): Mixed batch — fleet-owned row followed by OEM-owned row (mutation d)
# ---------------------------------------------------------------------------
class TestMixedBatchDeleteRefusedPerRow(unittest.TestCase):
    """
    Mutation (d) guard — per-row check inside the bulk-delete scan loop.

    A fleet caller deletes a template it owns.  The scan returns two assignment
    rows: the first belongs to the caller's fleet, the second belongs to 'oem'.

    A per-row implementation must refuse on the second row (403).
    A per-batch (pre-loop) implementation would see only the template and
    approve the whole batch — producing a 200 that deletes the OEM assignment.

    This case exists solely to distinguish per-row from per-batch.
    A uniform batch (all fleet-owned or all OEM-owned) cannot make that
    distinction because both implementations give the same answer.

    Mutation target: hoist the scan-loop guard out of the loop so it evaluates
    only the first row (or the template) — this test must FAIL.
    """

    def setUp(self):
        self.mock_table = MagicMock()
        self.fleet_id = 'fleet-alpha'
        # Template row is owned by the fleet caller.
        self.mock_table.get_item.return_value = {
            'Item': {
                'campaignId': 'fleet-campaign',
                'campaignName': 'fleet-campaign',
                'owner': f'fleet:{self.fleet_id}',
                'targetArn': 'template',
                'status': 'ACTIVE',
            }
        }
        # Scan returns two assignment rows: first is fleet-owned, second is OEM-owned.
        self.mock_table.scan.return_value = {
            'Items': [
                {
                    'campaignId': 'fleet-campaign-VIN001',
                    'campaignName': 'fleet-campaign',
                    'owner': f'fleet:{self.fleet_id}',
                    'targetArn': 'vehicle:VIN001',
                    'status': 'RUNNING',
                },
                {
                    'campaignId': 'fleet-campaign-VIN002',
                    'campaignName': 'fleet-campaign',
                    'owner': 'oem',           # ← second row is OEM-owned
                    'targetArn': 'vehicle:VIN002',
                    'status': 'RUNNING',
                },
            ]
        }
        self.patcher = patch.object(api, 'campaigns_table', self.mock_table)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_mixed_batch_refused_on_oem_assignment(self):
        """
        Bulk delete with a mixed batch must refuse on the OEM-owned assignment
        row and return 403.

        Per-row guard: iterates, passes VIN001 (fleet-owned), then refuses
        VIN002 (OEM-owned) → 403.  Only VIN001's delete_item was called before
        the refusal; VIN002's was not.

        Per-batch (hoisted) guard: evaluates only the template row (fleet-owned),
        approves, deletes all three rows, returns 200 → this assertion FAILS.

        This is the mutation (d) target from the Task 4.2 Verify specification.
        """
        event = _fleet_event(
            'DELETE', '/campaigns', fleet_id=self.fleet_id,
            query={'campaignId': 'fleet-campaign'}
        )
        resp = api.delete_campaign(event)
        self.assertEqual(
            resp['statusCode'], 403,
            f"Expected 403 (per-row guard on OEM assignment) but got "
            f"{resp['statusCode']}; if this is 200, the guard is per-batch "
            "(hoisted out of loop) rather than per-row — see mutation (d)."
        )
        # The template itself was deleted (happened before the scan loop),
        # and VIN001 (fleet-owned) was deleted before VIN002 raised 403.
        # VIN002 (OEM-owned) must NOT have been deleted.
        deleted_keys = [
            c[1]['Key']['campaignId']
            for c in self.mock_table.delete_item.call_args_list
        ]
        self.assertNotIn(
            'fleet-campaign-VIN002', deleted_keys,
            "The OEM-owned assignment row must not have been deleted — "
            "the guard must have refused before reaching delete_item for it."
        )



# ---------------------------------------------------------------------------
# Case (g): Fleet-B cannot delete fleet-A's campaign (exact-id check, not prefix)
# ---------------------------------------------------------------------------
class TestCrossFleetDeleteRefused(unittest.TestCase):
    """
    Mutation (b) guard — exact fleet-id match, not just 'fleet:' prefix.

    Fleet-B tries to delete a campaign owned by fleet-A.  Both owners have the
    'fleet:' prefix, so a prefix-only check ('owner.startswith("fleet:")') would
    pass fleet-B — which is the mutation target.

    A correct exact-id check ('owner == "fleet:<caller-id>"') refuses fleet-B
    and returns 403.

    Mutation target: replace `owner != f'fleet:{fleet_id}'` with a prefix-only
    comparison → this test must FAIL (200 instead of 403).
    """

    def setUp(self):
        self.mock_table = MagicMock()
        # Row owned by fleet-A.
        self.mock_table.get_item.return_value = {
            'Item': {
                'campaignId': 'fleet-a-campaign',
                'campaignName': 'fleet-a-campaign',
                'owner': 'fleet:fleet-A',
                'targetArn': 'template',
                'status': 'ACTIVE',
            }
        }
        self.mock_table.scan.return_value = {'Items': []}
        self.patcher = patch.object(api, 'campaigns_table', self.mock_table)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_fleet_b_cannot_delete_fleet_a_campaign(self):
        """Fleet-B DELETE of a campaign owned by fleet-A must return 403."""
        event = _fleet_event(
            'DELETE', '/campaigns', fleet_id='fleet-B',
            query={'campaignId': 'fleet-a-campaign'}
        )
        resp = api.delete_campaign(event)
        self.assertEqual(
            resp['statusCode'], 403,
            f"Expected 403 (cross-fleet delete refused) but got {resp['statusCode']}. "
            "If 200, the guard is prefix-only ('fleet:') rather than exact-id — "
            "see mutation (b)."
        )
