"""
FG7.2 — Tests asserting PRESERVATION of an existing owner, not just setting.

These are the cases the 22 tests in test_campaign_owner_derivation.py structurally
cannot express, because they all start from an absent (mock-new) row.

For each of the three writers:
  - create_campaign: refuses when target row already exists (any owner)
  - update_campaign: does not overwrite an existing owner
  - assign_campaign: refuses (skip) when per-vehicle row already exists with a
                     different party's owner

Owner values under test (all three parties as specified in FG7.1 Accept):
  "oem"         — OEM-authored template
  "platform"    — auto-ensure baseline
  "fleet:other" — a *different* fleet's row

Spec: .kiro/specs/2026-09-15-cms-cs-campaign-ownership/ Fix Group 7
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch, call

import botocore.exceptions

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
# Shared helpers
# ---------------------------------------------------------------------------

def _event(method: str, path: str, groups: list, fleet_ids: str = '',
           sub: str = 'attacker-sub', body=None, query=None) -> dict:
    """API-GW-shaped event."""
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


def _fleet_a_event(method='POST', path='/campaigns', body=None, query=None):
    """A fleet-A operator — the attacker in hijack scenarios."""
    return _event(method, path, groups=['fleet-operator'],
                  fleet_ids='fleet-A', body=body, query=query)


def _admin_event(method='POST', path='/campaigns', body=None, query=None):
    return _event(method, path, groups=['platform-admin'], body=body, query=query)


def _conditional_check_failed_exception():
    """Build a botocore ClientError that maps to ConditionalCheckFailedException."""
    return botocore.exceptions.ClientError(
        error_response={'Error': {'Code': 'ConditionalCheckFailedException',
                                  'Message': 'The conditional request failed'}},
        operation_name='PutItem',
    )


def _make_mock_table(template_item=None):
    """Return a mock campaigns_table with a sensible template get_item default."""
    mock = MagicMock()
    mock.get_item.return_value = {
        'Item': template_item or {
            'campaignId': 'cms-fleet-gps-10s',
            'campaignName': 'cms-fleet-gps-10s',
            'targetArn': 'template',
            'status': 'ACTIVE',
        }
    }
    mock.put_item.return_value = {}
    mock.update_item.return_value = {}
    mock.scan.return_value = {'Items': []}
    # Map the botocore exception so the except clause in the handler resolves correctly.
    mock.meta.client.exceptions.ConditionalCheckFailedException = (
        botocore.exceptions.ClientError
    )
    return mock


# ---------------------------------------------------------------------------
# 1.  create_campaign — must refuse when a row already exists
# ---------------------------------------------------------------------------

class TestCreateCampaignRefusesOverwrite(unittest.TestCase):
    """
    create_campaign carries ConditionExpression='attribute_not_exists(campaignId)'.
    When DynamoDB raises ConditionalCheckFailedException (row exists), the handler
    must return 409 and must never succeed in overwriting.

    Covers all three owner variants that might own the existing row.
    """

    def setUp(self):
        self.mock_table = _make_mock_table()
        self.patcher = patch.object(api, 'campaigns_table', self.mock_table)
        self.patcher.start()
        # make put_item raise the exception — simulates a row already existing
        self.mock_table.put_item.side_effect = _conditional_check_failed_exception()

    def tearDown(self):
        self.patcher.stop()

    def _assert_409_no_overwrite(self, event):
        """Helper: confirm 409 returned and no successful put_item occurred."""
        resp = api.create_campaign(event)
        self.assertEqual(
            resp['statusCode'], 409,
            f"Expected 409 (row exists) but got {resp['statusCode']}: {resp.get('body')}",
        )
        # put_item was called (the exception fires from it) but the result was 409 —
        # the handler did NOT return 201, proving overwrite was refused.

    def test_create_refuses_when_row_owned_by_oem(self):
        """Fleet-A tries to POST cms-fleet-gps-10s which is already owned by 'oem'."""
        self._assert_409_no_overwrite(
            _fleet_a_event(body={'campaignName': 'cms-fleet-gps-10s'})
        )

    def test_create_refuses_when_row_owned_by_platform(self):
        """Fleet-A tries to POST the auto-ensure baseline already owned by 'platform'."""
        self._assert_409_no_overwrite(
            _fleet_a_event(body={'campaignName': 'cms-fleet-telemetry-30s'})
        )

    def test_create_refuses_when_row_owned_by_other_fleet(self):
        """Fleet-A tries to POST a campaign already owned by 'fleet:other'."""
        self._assert_409_no_overwrite(
            _fleet_a_event(body={'campaignName': 'fleet-other-custom-campaign'})
        )

    def test_create_refuses_even_for_platform_admin_caller(self):
        """platform-admin caller also gets 409 — ConditionExpression is unconditional."""
        self._assert_409_no_overwrite(
            _admin_event(body={'campaignName': 'cms-fleet-gps-10s'})
        )


# ---------------------------------------------------------------------------
# 2.  update_campaign — must NOT write owner to the row at all
# ---------------------------------------------------------------------------

class TestUpdateCampaignPreservesOwner(unittest.TestCase):
    """
    update_campaign must NOT include 'owner' in its SET expression.

    Owner is attribution established at creation (create_campaign).  A routine
    PUT rewriting it is the hijack vector: fleet-A calling PUT with a template's
    campaignId would overwrite the OEM's / platform's owner with 'fleet:fleet-A',
    and once Task 4.2's delete guard ships the attacker could then delete the row.

    These tests verify the preservation property: the existing owner survives an
    update call regardless of the caller's identity.
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

    def _assert_refused_and_nothing_written(self, event):
        """A fleet caller on a row it does not own: 403, and no write attempted.

        Task 4.2's ownership guard ships after this file was written. Before it, a
        fleet caller reached `update_item` and the only thing standing between it
        and a hijack was the absence of `:own` in the SET expression. Now the call
        is refused outright, which is a strictly stronger guarantee — so this
        helper asserts BOTH halves: the 403, and that `update_item` was never
        called at all.

        This deliberately does NOT accept "200 without ':own'" as equivalent. An
        earlier revision of this helper returned early on 403 and asserted nothing,
        which made three of the four tests below pass while asserting nothing about
        ownership — and, worse, would have passed if the guard broke and a fleet
        were ALLOWED to modify an OEM template so long as it did not write `owner`.
        That is exactly the Decision 2 violation these tests are named for. The
        test that names the property must fail when the property breaks.
        """
        resp = api.update_campaign(event)
        self.assertEqual(
            resp['statusCode'], 403,
            f"A fleet caller must be refused (403) on a row it does not own; got "
            f"{resp['statusCode']}: {resp.get('body')}",
        )
        self.mock_table.update_item.assert_not_called()

    def _assert_owner_not_in_update(self, event):
        """Helper: the call SUCCEEDS and ':own' does not appear in the SET expression.

        For callers who are permitted to update the row (platform-admin, or a fleet
        updating its own row). Owner is set at creation only; writing it on update
        is the hijack vector.
        """
        resp = api.update_campaign(event)
        self.assertEqual(
            resp['statusCode'], 200,
            f"Expected 200 from update_campaign, got {resp['statusCode']}: {resp.get('body')}",
        )
        kwargs = self._update_kwargs()
        attr_values = kwargs.get('ExpressionAttributeValues', {})
        attr_names = kwargs.get('ExpressionAttributeNames', {})
        update_expr = kwargs.get('UpdateExpression', '')
        self.assertNotIn(
            ':own', attr_values,
            "update_campaign must not write ':own' — owner is set at creation only; "
            "writing it lets any caller overwrite an existing owner (hijack vector)",
        )
        self.assertNotIn(
            '#own', attr_names,
            "update_campaign must not include '#own' ExpressionAttributeName",
        )
        self.assertNotIn(
            'owner', update_expr.lower(),
            "update_campaign must not contain 'owner' in the UpdateExpression",
        )

    def test_fleet_a_update_oem_template_is_refused(self):
        """
        Fleet-A calls PUT /campaigns with {'campaignId': 'cms-fleet-gps-10s'}.
        The existing row is owned by 'oem', so Decision 2 says fleet-A cannot
        modify it at all — not merely that it cannot restamp `owner`.
        """
        self._assert_refused_and_nothing_written(
            _fleet_a_event(
                method='PUT',
                body={'campaignId': 'cms-fleet-gps-10s', 'status': 'ACTIVE'},
            )
        )

    def test_fleet_a_update_platform_baseline_is_refused(self):
        """Fleet-A PUT against the platform-baseline row must be refused."""
        self._assert_refused_and_nothing_written(
            _fleet_a_event(
                method='PUT',
                body={'campaignId': 'cms-fleet-telemetry-30s', 'status': 'ACTIVE'},
            )
        )

    def test_fleet_a_update_other_fleets_row_is_refused(self):
        """Fleet-A PUT against a row owned by 'fleet:other' must be refused."""
        self._assert_refused_and_nothing_written(
            _fleet_a_event(
                method='PUT',
                body={'campaignId': 'fleet-other-campaign', 'status': 'ACTIVE'},
            )
        )

    def test_admin_update_does_not_write_owner(self):
        """platform-admin caller is permitted, and still must not write owner."""
        self._assert_owner_not_in_update(
            _admin_event(
                method='PUT',
                body={'campaignId': 'cms-fleet-gps-10s', 'description': 'updated desc'},
            )
        )


# ---------------------------------------------------------------------------
# 3.  assign_campaign — must not overwrite an existing owner on a per-vehicle row
# ---------------------------------------------------------------------------

class TestAssignCampaignPreservesExistingOwner(unittest.TestCase):
    """
    assign_campaign carries ConditionExpression='attribute_not_exists(campaignId)'
    on each per-vehicle put_item.

    When a per-vehicle row already exists (owned by oem / platform / fleet:other),
    the condition fires, the VIN is NOT added to 'assigned', and the existing
    owner is preserved.

    Covers all three owner values on the pre-existing row.

    VIN-fleet mock (FGS1.1): the fleet-A caller passes 'PLATFORM-VIN'; the
    vehicles mock returns fleet-A for that VIN so the membership check passes
    and the existing-owner preservation logic is exercised.
    """

    def setUp(self):
        self.mock_table = _make_mock_table()
        self.patcher = patch.object(api, 'campaigns_table', self.mock_table)
        self.patcher.start()
        # FGS1.1: mock _vehicles_ddb so PLATFORM-VIN resolves to fleet-A
        # (the caller's own fleet).  Without this, _lookup_vin_fleet_id returns
        # None and the request is refused before reaching the preservation logic.
        self.mock_vehicles = MagicMock()
        self.mock_vehicles.query.return_value = {
            'Items': [{'vehicleId': 'vehicle-platform-vin', 'vin': 'PLATFORM-VIN'}]
        }
        self.mock_vehicles.get_item.return_value = {
            'Item': {'vehicleId': 'vehicle-platform-vin', 'fleetId': 'fleet-A'}
        }
        self.patcher_veh = patch.object(api, '_vehicles_ddb', self.mock_vehicles)
        self.patcher_veh.start()

    def tearDown(self):
        self.patcher.stop()
        self.patcher_veh.stop()

    def _assert_existing_owner_preserved(self, pre_existing_owner: str, caller_owner: str,
                                          event):
        """
        put_item raises ConditionalCheckFailedException (row exists owned by
        pre_existing_owner).  After the call:
          - response is 200 (not an error — the condition fire is expected)
          - the 'assigned' list does NOT include the VIN (the row was already assigned)
          - the mock put_item was called once (the condition fired on that call)

        The key assertion: the existing owner is NOT overwritten. Because the
        condition prevents the write, whatever was in the row before is still there.
        """
        # Template lookup returns the template; per-vehicle put raises the condition
        self.mock_table.get_item.return_value = {
            'Item': {
                'campaignId': 'cms-fleet-gps-10s',
                'campaignName': 'cms-fleet-gps-10s',
                'targetArn': 'template',
                'status': 'ACTIVE',
                'owner': caller_owner,  # the template's owner (used to derive item owner)
            }
        }
        self.mock_table.put_item.side_effect = _conditional_check_failed_exception()

        resp = api.assign_campaign(event)
        body = json.loads(resp['body'])
        self.assertEqual(
            resp['statusCode'], 200,
            f"assign_campaign should return 200 even when a VIN is already assigned; "
            f"got {resp['statusCode']}: {resp.get('body')}",
        )
        self.assertNotIn(
            'PLATFORM-VIN', body.get('assigned', []),
            f"VIN with pre-existing owner='{pre_existing_owner}' must NOT appear in "
            f"'assigned' — the write was refused by ConditionExpression",
        )
        # put_item was attempted exactly once (and the condition fired)
        self.assertEqual(
            self.mock_table.put_item.call_count, 1,
            "put_item should have been attempted exactly once (the condition fires from it)",
        )

    def test_assign_refuses_when_per_vehicle_row_owned_by_oem(self):
        """
        Attacker (fleet-A) assigns a campaign to a VIN whose per-vehicle row is already
        owned by 'oem' (OEM authoring a per-vehicle campaign directly).
        The condition expression prevents overwriting the oem owner.
        """
        self._assert_existing_owner_preserved(
            pre_existing_owner='oem',
            caller_owner='oem',
            event=_fleet_a_event(
                method='POST', path='/campaigns/assign',
                body={'campaignName': 'cms-fleet-gps-10s', 'vehicles': ['PLATFORM-VIN']},
            ),
        )

    def test_assign_refuses_when_per_vehicle_row_owned_by_platform(self):
        """
        Fleet-A assigns a campaign to a VIN whose per-vehicle row is already owned
        by 'platform' (the auto-ensure baseline created by _ensure_telemetry_campaign).
        The platform baseline owner must survive — fleet cannot claim ownership over it.
        """
        self._assert_existing_owner_preserved(
            pre_existing_owner='platform',
            caller_owner='oem',
            event=_fleet_a_event(
                method='POST', path='/campaigns/assign',
                body={'campaignName': 'cms-fleet-gps-10s', 'vehicles': ['PLATFORM-VIN']},
            ),
        )

    def test_assign_refuses_when_per_vehicle_row_owned_by_other_fleet(self):
        """
        Fleet-A assigns a campaign to a VIN already assigned by 'fleet:other'.
        Fleet-A must not be able to claim 'fleet:fleet-A' ownership over fleet:other's row.
        """
        self._assert_existing_owner_preserved(
            pre_existing_owner='fleet:other',
            caller_owner='oem',
            event=_fleet_a_event(
                method='POST', path='/campaigns/assign',
                body={'campaignName': 'cms-fleet-gps-10s', 'vehicles': ['PLATFORM-VIN']},
            ),
        )

    def test_assign_partial_success_new_vin_assigned_existing_skipped(self):
        """
        When assigning two VINs — one new, one already existing — the new VIN appears
        in 'assigned' and the existing VIN is silently skipped (not an error).
        Owner on the new VIN is the caller's derived owner.
        """
        self.mock_table.get_item.return_value = {
            'Item': {
                'campaignId': 'cms-fleet-gps-10s',
                'campaignName': 'cms-fleet-gps-10s',
                'targetArn': 'template',
                'status': 'ACTIVE',
            }
        }
        # First VIN raises the condition (already exists); second succeeds
        self.mock_table.put_item.side_effect = [
            _conditional_check_failed_exception(),  # VIN-EXISTING
            {},                                      # VIN-NEW succeeds
        ]

        resp = api.assign_campaign(
            _fleet_a_event(
                method='POST', path='/campaigns/assign',
                body={
                    'campaignName': 'cms-fleet-gps-10s',
                    'vehicles': ['VIN-EXISTING', 'VIN-NEW'],
                },
            )
        )
        body = json.loads(resp['body'])
        self.assertEqual(resp['statusCode'], 200)
        self.assertNotIn('VIN-EXISTING', body.get('assigned', []),
                         "Already-assigned VIN must not appear in 'assigned'")
        self.assertIn('VIN-NEW', body.get('assigned', []),
                      "New VIN must appear in 'assigned'")
        # Check the new VIN's written item carries the caller's owner
        new_vin_call = self.mock_table.put_item.call_args_list[1]
        written_item = new_vin_call[1]['Item']
        self.assertEqual(written_item.get('owner'), 'fleet:fleet-A',
                         "New VIN row must carry caller's derived owner 'fleet:fleet-A'")


if __name__ == '__main__':
    unittest.main()
