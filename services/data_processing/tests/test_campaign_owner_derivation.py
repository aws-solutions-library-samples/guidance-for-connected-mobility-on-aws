"""
Tests for the ``_derive_owner`` helper and the three campaign write sites.
Spec: .kiro/specs/2026-09-15-cms-cs-campaign-ownership/ Task 2.1

Owner derivation table (decisions.md § "owner derivation from caller identity"):

| Condition                                              | owner               |
|--------------------------------------------------------|---------------------|
| connected-services in groups                          | "oem"               |
| platform-admin in groups                              | "oem"               |
| fleet-operator + exactly one custom:fleetIds entry    | "fleet:<that id>"   |
| fleet-operator + zero or >1 fleets, no fleetId body   | REJECT (400)        |

platform-admin wins when both platform-admin and fleet-operator are present.
custom:fleetIds is a comma-separated string.

MUTATION TARGET (Task 2.1 Verify):
  Delete the `'owner': owner` assignment in assign_campaign's per-vehicle item
  dict and confirm that test_assign_campaign_sets_owner_on_each_row FAILS.
  Restore from /tmp/data_processing_api_task21_backup.py (NOT git checkout).
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch, call

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

def _event(method: str, path: str, groups: list, fleet_ids: str = '',
           sub: str = 'test-sub', body=None, query=None) -> dict:
    """API-GW-shaped event with the specified Cognito groups."""
    claims: dict = {
        'cognito:groups': ','.join(groups),
        'sub': sub,
    }
    if fleet_ids:
        claims['custom:fleetIds'] = fleet_ids
    return {
        'httpMethod': method,
        'path': path,
        'queryStringParameters': query or {},
        'body': json.dumps(body) if body else None,
        'requestContext': {'authorizer': {'claims': claims}},
    }


def _oem_event(method='POST', path='/campaigns', body=None, query=None):
    return _event(method, path, groups=['connected-services'], body=body, query=query)


def _admin_event(method='POST', path='/campaigns', body=None, query=None):
    return _event(method, path, groups=['platform-admin'], body=body, query=query)


def _fleet_event(method='POST', path='/campaigns', fleet_id='fleet-abc',
                 body=None, query=None):
    return _event(method, path, groups=['fleet-operator'],
                  fleet_ids=fleet_id, body=body, query=query)


def _admin_and_fleet_event(method='POST', path='/campaigns', fleet_id='fleet-abc',
                            body=None, query=None):
    """Token carrying BOTH platform-admin and fleet-operator."""
    return _event(method, path, groups=['platform-admin', 'fleet-operator'],
                  fleet_ids=fleet_id, body=body, query=query)


# ---------------------------------------------------------------------------
# Helper: shared mock table for campaign writes
# ---------------------------------------------------------------------------
def _make_mock_table(template_item=None):
    mock_table = MagicMock()
    mock_table.get_item.return_value = {
        'Item': template_item or {
            'campaignId': 'test-campaign',
            'campaignName': 'test-campaign',
            'targetArn': 'template',
            'status': 'ACTIVE',
        }
    }
    mock_table.put_item.return_value = {}
    mock_table.update_item.return_value = {}
    mock_table.scan.return_value = {'Items': []}
    return mock_table


# ===========================================================================
# 1.  _derive_owner unit tests — test the derivation function directly
# ===========================================================================

class TestDeriveOwnerUnit(unittest.TestCase):
    """Unit tests for the _derive_owner helper in isolation."""

    def test_connected_services_returns_oem(self):
        event = _event('POST', '/campaigns', groups=['connected-services'])
        self.assertEqual(api._derive_owner(event), 'oem')

    def test_platform_admin_returns_oem(self):
        event = _event('POST', '/campaigns', groups=['platform-admin'])
        self.assertEqual(api._derive_owner(event), 'oem')

    def test_fleet_operator_single_fleet_returns_fleet_id(self):
        event = _event('POST', '/campaigns', groups=['fleet-operator'],
                       fleet_ids='fleet-abc')
        self.assertEqual(api._derive_owner(event), 'fleet:fleet-abc')

    def test_fleet_operator_trims_whitespace_in_fleet_id(self):
        """custom:fleetIds may carry spaces around the comma; should still work."""
        event = _event('POST', '/campaigns', groups=['fleet-operator'],
                       fleet_ids=' fleet-abc ')
        self.assertEqual(api._derive_owner(event), 'fleet:fleet-abc')

    def test_platform_admin_wins_over_fleet_operator(self):
        """When both groups present, platform-admin takes precedence → 'oem'."""
        event = _admin_and_fleet_event(fleet_id='fleet-abc')
        self.assertEqual(api._derive_owner(event), 'oem',
                         "platform-admin must win when both groups present — "
                         "result must be 'oem', not 'fleet:fleet-abc'")

    def test_connected_services_wins_over_fleet_operator(self):
        """connected-services + fleet-operator → 'oem'."""
        event = _event('POST', '/campaigns',
                       groups=['connected-services', 'fleet-operator'],
                       fleet_ids='fleet-abc')
        self.assertEqual(api._derive_owner(event), 'oem')

    def test_fleet_operator_zero_fleets_raises(self):
        """Fleet operator with no custom:fleetIds must be rejected."""
        event = _event('POST', '/campaigns', groups=['fleet-operator'], fleet_ids='')
        with self.assertRaises(api._Unauthorized):
            api._derive_owner(event)

    def test_fleet_operator_multiple_fleets_raises(self):
        """Fleet operator with >1 custom:fleetIds must be rejected (ambiguous ownership)."""
        event = _event('POST', '/campaigns', groups=['fleet-operator'],
                       fleet_ids='fleet-abc,fleet-def')
        with self.assertRaises(api._Unauthorized):
            api._derive_owner(event)

    def test_unknown_group_raises(self):
        """A group outside the derivation table must raise _Unauthorized."""
        event = _event('POST', '/campaigns', groups=['fleet-viewer'])
        with self.assertRaises(api._Unauthorized):
            api._derive_owner(event)


# ===========================================================================
# 2.  create_campaign — owner persisted on the written item
# ===========================================================================

class TestCreateCampaignSetsOwner(unittest.TestCase):
    """create_campaign must persist 'owner' on every written row."""

    def setUp(self):
        self.mock_table = _make_mock_table()
        self.patcher = patch.object(api, 'campaigns_table', self.mock_table)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def _written_item(self):
        self.mock_table.put_item.assert_called_once()
        return self.mock_table.put_item.call_args[1]['Item']

    def test_connected_services_owner_is_oem(self):
        resp = api.create_campaign(_oem_event(body={'campaignName': 'cs-campaign'}))
        self.assertEqual(resp['statusCode'], 201)
        item = self._written_item()
        self.assertEqual(item.get('owner'), 'oem')

    def test_platform_admin_owner_is_oem(self):
        resp = api.create_campaign(_admin_event(body={'campaignName': 'admin-campaign'}))
        self.assertEqual(resp['statusCode'], 201)
        item = self._written_item()
        self.assertEqual(item.get('owner'), 'oem')

    def test_fleet_operator_single_fleet_owner_is_fleet_id(self):
        resp = api.create_campaign(
            _fleet_event(fleet_id='fleet-xyz', body={'campaignName': 'fleet-campaign'})
        )
        self.assertEqual(resp['statusCode'], 201)
        item = self._written_item()
        self.assertEqual(item.get('owner'), 'fleet:fleet-xyz')

    def test_fleet_operator_multi_fleet_returns_400(self):
        """Ambiguous fleet ownership → 400, no DynamoDB write."""
        event = _event('POST', '/campaigns', groups=['fleet-operator'],
                       fleet_ids='fleet-a,fleet-b', body={'campaignName': 'bad'})
        resp = api.create_campaign(event)
        self.assertEqual(resp['statusCode'], 400)
        self.mock_table.put_item.assert_not_called()

    def test_owner_is_never_absent_or_empty(self):
        """Invariant: owner must be a non-empty string on the written item."""
        resp = api.create_campaign(_admin_event(body={'campaignName': 'check-owner'}))
        self.assertEqual(resp['statusCode'], 201)
        item = self._written_item()
        self.assertIn('owner', item, "owner key must be present on written item")
        self.assertTrue(item['owner'], "owner must not be empty")


# ===========================================================================
# 3.  update_campaign — owner included in the UpdateExpression
# ===========================================================================

class TestUpdateCampaignDoesNotWriteOwner(unittest.TestCase):
    """update_campaign must NOT write 'owner'.

    REVERSED by FG7.1 after the Group 2 security review found C1. Task 2.1's
    Accept originally asked update_campaign to set `owner`, and that WAS the
    ownership-hijack vector: a fleet-operator PUTting any campaignId had the
    row's owner rewritten to their own fleet, which post-Task-4.2 defeats the
    delete guard one call earlier.

    `owner` is attribution established at creation. A routine status/scheme
    update has no business rewriting it, and this spec provides no transfer
    operation. See decisions.md and tasks.md Fix Group 7.

    These three cases previously asserted the vulnerable behaviour. They are
    rewritten rather than deleted so the reversal stays visible to the next
    reader.
    """

    def setUp(self):
        self.mock_table = _make_mock_table()
        self.patcher = patch.object(api, 'campaigns_table', self.mock_table)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def _update_kwargs(self):
        self.mock_table.update_item.assert_called_once()
        return self.mock_table.update_item.call_args[1]

    def _assert_no_owner_written(self, kwargs):
        attr_values = kwargs.get('ExpressionAttributeValues', {})
        attr_names = kwargs.get('ExpressionAttributeNames', {})
        update_expr = kwargs.get('UpdateExpression', '')
        self.assertNotIn(':own', attr_values,
                         "update_campaign must not write owner (FG7.1 / security C1)")
        self.assertNotIn('owner', attr_names.values(),
                         "update_campaign must not name owner in the update")
        self.assertNotIn('#own', update_expr,
                         "update_campaign's UpdateExpression must not SET owner")

    def test_oem_caller_update_does_not_write_owner(self):
        resp = api.update_campaign(
            _oem_event(method='PUT', body={'campaignId': 'some-campaign', 'status': 'ACTIVE'})
        )
        self.assertEqual(resp['statusCode'], 200)
        self._assert_no_owner_written(self._update_kwargs())

    def test_fleet_operator_update_does_not_write_owner(self):
        """The hijack case: a fleet caller must not stamp their fleet onto the row.

        Task 4.2's ownership guard now refuses fleet callers on rows they don't own
        (403) before update_item is ever reached.  We test both paths:
        - fleet updating an OEM row: 403 (guard fires, no write at all)
        - fleet updating its own row: 200 and no ':own' in the SET expression
        """
        # Sub-case 1: fleet updating an OEM-owned row → 403 (guard fires first)
        resp_oem = api.update_campaign(
            _fleet_event(method='PUT', fleet_id='fleet-xyz',
                         body={'campaignId': 'fleet-campaign', 'status': 'ACTIVE'})
        )
        self.assertEqual(resp_oem['statusCode'], 403,
                         "fleet caller on a non-owned row must get 403 from the ownership guard")

        # Sub-case 2: fleet updating its OWN row → 200 and no ':own' written
        self.mock_table.get_item.return_value = {
            'Item': {
                'campaignId': 'fleet-xyz-campaign',
                'owner': 'fleet:fleet-xyz',
                'status': 'ACTIVE',
            }
        }
        self.mock_table.update_item.reset_mock()
        resp_own = api.update_campaign(
            _fleet_event(method='PUT', fleet_id='fleet-xyz',
                         body={'campaignId': 'fleet-xyz-campaign', 'status': 'SUSPENDED'})
        )
        self.assertEqual(resp_own['statusCode'], 200,
                         "fleet caller on its OWN row must be allowed (200)")
        self._assert_no_owner_written(self._update_kwargs())

    def test_multi_fleet_caller_no_longer_blocked_on_owner_derivation(self):
        """Multi-fleet ambiguity was only ever a problem BECAUSE update wrote owner.

        With owner no longer derived here, an ambiguous fleet claim is irrelevant
        to a status update and must not 400 on that basis. Whether such a caller
        may update the row at all is Task 4.2's per-row guard, not this path's.

        Ownership guard: _caller_fleet_id returns frozenset({'fleet-a', 'fleet-b'})
        for a multi-fleet caller.  The row must be owned by one of those fleet ids
        (here 'fleet:fleet-a') so the guard permits the update — this confirms that
        a multi-fleet caller is not blocked by the ownership check on a row they
        co-own.
        """
        # Row is owned by fleet-a, which is in the multi-fleet caller's set.
        self.mock_table.get_item.return_value = {
            'Item': {
                'campaignId': 'ambiguous',
                'owner': 'fleet:fleet-a',
                'status': 'ACTIVE',
            }
        }
        event = _event('PUT', '/campaigns', groups=['fleet-operator'],
                       fleet_ids='fleet-a,fleet-b',
                       body={'campaignId': 'ambiguous', 'status': 'ACTIVE'})
        resp = api.update_campaign(event)
        self.assertEqual(resp['statusCode'], 200,
                         "multi-fleet is no longer a blocker once owner is not derived here")
        self._assert_no_owner_written(self._update_kwargs())


# ===========================================================================
# 4.  assign_campaign — owner set on every per-vehicle row
# ===========================================================================

class TestAssignCampaignSetsOwner(unittest.TestCase):
    """assign_campaign must set 'owner' on every per-vehicle put_item call.

    MUTATION TARGET: This class's assertions are what must FAIL when the
    'owner': owner line is deleted from assign_campaign's per-vehicle item dict.
    """

    def setUp(self):
        self.mock_table = _make_mock_table()
        self.patcher = patch.object(api, 'campaigns_table', self.mock_table)
        self.patcher.start()
        # FGS1.1: mock _vehicles_ddb so fleet-operator assign tests pass the
        # VIN-fleet membership check.  The VIN 'VIN-3' belongs to 'fleet-abc'
        # (the caller's fleet) in test_assign_campaign_fleet_owner_on_each_row.
        # Admin tests (test_assign_campaign_sets_owner_on_each_row) are
        # unrestricted and never call _lookup_vin_fleet_id.
        self.mock_vehicles = MagicMock()
        self.mock_vehicles.query.return_value = {
            'Items': [{'vehicleId': 'vehicle-vin3', 'vin': 'VIN-3'}]
        }
        self.mock_vehicles.get_item.return_value = {
            'Item': {'vehicleId': 'vehicle-vin3', 'fleetId': 'fleet-abc'}
        }
        self.patcher_veh = patch.object(api, '_vehicles_ddb', self.mock_vehicles)
        self.patcher_veh.start()

    def tearDown(self):
        self.patcher.stop()
        self.patcher_veh.stop()

    def test_assign_campaign_sets_owner_on_each_row(self):
        """Every per-vehicle item written by assign_campaign must carry owner='oem'."""
        resp = api.assign_campaign(
            _admin_event(method='POST', path='/campaigns/assign',
                         body={'campaignName': 'test-campaign', 'vehicles': ['VIN-1', 'VIN-2']})
        )
        self.assertEqual(resp['statusCode'], 200)
        # Two VINs → two put_item calls
        self.assertEqual(self.mock_table.put_item.call_count, 2)
        for i, call_args in enumerate(self.mock_table.put_item.call_args_list):
            item = call_args[1]['Item']
            self.assertIn('owner', item,
                          f"put_item call #{i + 1}: 'owner' key must be present")
            self.assertEqual(item['owner'], 'oem',
                             f"put_item call #{i + 1}: owner must be 'oem' for platform-admin caller")

    def test_assign_campaign_fleet_owner_on_each_row(self):
        """Fleet-operator assign must set owner='fleet:<id>' on each row."""
        resp = api.assign_campaign(
            _fleet_event(method='POST', path='/campaigns/assign',
                         fleet_id='fleet-abc',
                         body={'campaignName': 'test-campaign', 'vehicles': ['VIN-3']})
        )
        self.assertEqual(resp['statusCode'], 200)
        self.mock_table.put_item.assert_called_once()
        item = self.mock_table.put_item.call_args[1]['Item']
        self.assertEqual(item.get('owner'), 'fleet:fleet-abc')

    def test_assign_campaign_multi_fleet_returns_400_no_write(self):
        """Ambiguous fleet (>1 fleetIds) must be rejected before any put_item."""
        event = _event('POST', '/campaigns/assign', groups=['fleet-operator'],
                       fleet_ids='fleet-a,fleet-b',
                       body={'campaignName': 'test-campaign', 'vehicles': ['VIN-5']})
        resp = api.assign_campaign(event)
        self.assertEqual(resp['statusCode'], 400,
                         "Multi-fleet fleet-operator must get 400, not write any row")
        self.mock_table.put_item.assert_not_called()

    def test_assign_campaign_zero_fleet_ids_returns_400(self):
        """Fleet-operator with no custom:fleetIds must be rejected before any write."""
        event = _event('POST', '/campaigns/assign', groups=['fleet-operator'],
                       fleet_ids='',
                       body={'campaignName': 'test-campaign', 'vehicles': ['VIN-6']})
        resp = api.assign_campaign(event)
        self.assertEqual(resp['statusCode'], 400)
        self.mock_table.put_item.assert_not_called()

    def test_assign_campaign_owner_is_never_empty(self):
        """Invariant: owner must be a non-empty string on every written row."""
        resp = api.assign_campaign(
            _oem_event(method='POST', path='/campaigns/assign',
                       body={'campaignName': 'test-campaign', 'vehicles': ['VIN-7']})
        )
        self.assertEqual(resp['statusCode'], 200)
        item = self.mock_table.put_item.call_args[1]['Item']
        self.assertIn('owner', item)
        self.assertTrue(item['owner'], "owner must not be falsy/empty")


if __name__ == '__main__':
    unittest.main()
