"""Tests for owner inheritance on the fleet fan-out campaign writes.

Task 2.3 — spec `2026-09-15-cms-cs-campaign-ownership`.

Design (spec § Design, "Ownership model"):
  | Writer                   | owner value         |
  | main_api fleet fan-out   | inherit from template row, default "oem" |

Both put_item calls in POST /api/v1/fleet-campaigns/assign must carry a
non-empty `owner` field.  The value is `template.get('owner', 'oem')`, so:

  - template carries `owner = "oem"`     → both records get "oem"
  - template carries no `owner`          → both records default to "oem"
  - template carries `owner = "fleet:X"` → both records get "fleet:X"

Mutation target: removing the inheritance (passing no `owner` to either
`put_item`) must make test_assign_inherits_owner_from_template_explicit FAIL.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, call, patch

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
FLEET_ID = 'FLEET#ALPHA'
CAMPAIGN_NAME = 'campaign-road-safety'
VIN = 'VIN001'


def _event(claims: dict, fleet_id: str = FLEET_ID, campaign_name: str = CAMPAIGN_NAME) -> dict:
    return {
        'httpMethod': 'POST',
        'path': ASSIGN,
        'queryStringParameters': None,
        'body': json.dumps({'fleetId': fleet_id, 'campaignName': campaign_name}),
        'requestContext': {'authorizer': {'claims': claims}},
    }


def _admin_claims() -> dict:
    return {'sub': 'admin-user', 'email': 'admin@example.com',
            'cognito:groups': 'platform-admin', 'custom:tenantId': 'test'}


class FleetCampaignOwnerTests(unittest.TestCase):

    def setUp(self):
        if 'index' in sys.modules:
            del sys.modules['index']
        import index
        self.index = index

    def _run_assign(self, template_row: dict, vehicles: list[dict] | None = None) -> tuple[dict, MagicMock]:
        """Call the assign route and return (response, campaigns_table_mock).

        campaigns_table_mock.put_item.call_args_list holds every put_item call
        in order: [0] = fleet record, [1..n] = vehicle records.
        """
        if vehicles is None:
            vehicles = [{'vin': VIN, 'vehicleId': VIN, 'fleetId': FLEET_ID}]

        camp_table = MagicMock()
        camp_table.put_item.return_value = {}
        camp_table.query.return_value = {'Items': [template_row]}

        vehicles_table = MagicMock()
        vehicles_table.scan.return_value = {'Items': vehicles}

        def mock_table(name: str):
            if 'vehicle' in name.lower():
                return vehicles_table
            return camp_table

        with patch.object(self.index, 'dynamodb') as m:
            m.Table.side_effect = mock_table
            m.meta.client.scan.return_value = {'Count': 0}
            resp = self.index.handler(_event(_admin_claims()), None)

        return resp, camp_table

    def test_assign_inherits_owner_from_template_explicit(self):
        """Template row carrying owner="oem" → both written rows get owner="oem"."""
        resp, camp_table = self._run_assign(
            template_row={'campaignName': CAMPAIGN_NAME, 'owner': 'oem',
                          'targetArn': 'template', 'status': 'ACTIVE'}
        )
        self.assertEqual(resp['statusCode'], 200,
                         f"expected 200 but got {resp.get('statusCode')}: {resp.get('body')}")

        put_calls = camp_table.put_item.call_args_list
        self.assertEqual(len(put_calls), 2,
                         f"expected 2 put_item calls (fleet + 1 vehicle), got {len(put_calls)}")

        fleet_item = put_calls[0].kwargs.get('Item') or put_calls[0].args[0] if put_calls[0].args else put_calls[0].kwargs['Item']
        vehicle_item = put_calls[1].kwargs.get('Item') or put_calls[1].args[0] if put_calls[1].args else put_calls[1].kwargs['Item']

        self.assertEqual(fleet_item.get('owner'), 'oem',
                         f"fleet record must carry owner='oem'; got {fleet_item.get('owner')!r}")
        self.assertEqual(vehicle_item.get('owner'), 'oem',
                         f"vehicle fan-out record must carry owner='oem'; got {vehicle_item.get('owner')!r}")

    def test_assign_defaults_to_oem_when_template_has_no_owner(self):
        """Template row with NO owner → both written rows default to "oem"."""
        resp, camp_table = self._run_assign(
            template_row={'campaignName': CAMPAIGN_NAME,
                          'targetArn': 'template', 'status': 'ACTIVE'}
            # Note: deliberately no 'owner' key
        )
        self.assertEqual(resp['statusCode'], 200,
                         f"expected 200 but got {resp.get('statusCode')}: {resp.get('body')}")

        put_calls = camp_table.put_item.call_args_list
        self.assertEqual(len(put_calls), 2,
                         f"expected 2 put_item calls (fleet + 1 vehicle), got {len(put_calls)}")

        for i, put_call in enumerate(put_calls):
            item = put_call.kwargs.get('Item') or (put_call.args[0] if put_call.args else put_call.kwargs['Item'])
            self.assertEqual(item.get('owner'), 'oem',
                             f"put_item call {i}: expected owner='oem' when template has none; "
                             f"got {item.get('owner')!r}")

    def test_assign_inherits_custom_owner_from_template(self):
        """Template row carrying owner="fleet:X" → both written rows get "fleet:X"."""
        resp, camp_table = self._run_assign(
            template_row={'campaignName': CAMPAIGN_NAME, 'owner': 'fleet:FLEET#X',
                          'targetArn': 'template', 'status': 'ACTIVE'}
        )
        self.assertEqual(resp['statusCode'], 200,
                         f"expected 200 but got {resp.get('statusCode')}: {resp.get('body')}")

        put_calls = camp_table.put_item.call_args_list
        for i, put_call in enumerate(put_calls):
            item = put_call.kwargs.get('Item') or (put_call.args[0] if put_call.args else put_call.kwargs['Item'])
            self.assertEqual(item.get('owner'), 'fleet:FLEET#X',
                             f"put_item call {i}: expected owner='fleet:FLEET#X'; "
                             f"got {item.get('owner')!r}")

    def test_owner_non_empty_for_all_vehicle_fan_out_rows(self):
        """With three vehicles, all three fan-out records carry a non-empty owner."""
        vehicles = [
            {'vin': 'VIN001', 'vehicleId': 'VIN001', 'fleetId': FLEET_ID},
            {'vin': 'VIN002', 'vehicleId': 'VIN002', 'fleetId': FLEET_ID},
            {'vin': 'VIN003', 'vehicleId': 'VIN003', 'fleetId': FLEET_ID},
        ]
        resp, camp_table = self._run_assign(
            template_row={'campaignName': CAMPAIGN_NAME, 'owner': 'oem',
                          'targetArn': 'template', 'status': 'ACTIVE'},
            vehicles=vehicles
        )
        self.assertEqual(resp['statusCode'], 200,
                         f"expected 200; got {resp.get('statusCode')}: {resp.get('body')}")

        put_calls = camp_table.put_item.call_args_list
        # 1 fleet record + 3 vehicle records
        self.assertEqual(len(put_calls), 4,
                         f"expected 4 put_item calls; got {len(put_calls)}")

        for i, put_call in enumerate(put_calls):
            item = put_call.kwargs.get('Item') or (put_call.args[0] if put_call.args else put_call.kwargs['Item'])
            owner = item.get('owner')
            self.assertTrue(owner and len(owner) > 0,
                            f"put_item call {i}: owner must be non-empty; got {owner!r}")


if __name__ == '__main__':
    unittest.main()
