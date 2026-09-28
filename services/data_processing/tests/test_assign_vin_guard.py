"""POST /campaigns/assign — VIN-existence guard and unambiguous outcome lists.

Covers the two defects in
``issues/2026-09-20-cs-quick-assign-writes-vehicleid-keyed-campaign-row/``:

1. A caller passing a **vehicleId** where a VIN is required produced a row keyed
   ``vehicle:<vehicleId>`` that the VIN-keyed telemetry-campaign check can never
   find — silently, with a 200.
2. ``assigned: []`` meant both "already assigned" (idempotent success) and
   "nothing matched" (failure). The CS UI read the ambiguity as a hard error and
   advised a retry that could never clear.

Every test here carries its mutation in the docstring. Per
``~/.kiro/steering/testing.md``, each mutation was APPLIED and the named test
OBSERVED failing — reading the note is not evidence.
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

LAMBDA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'lambda'
)
if LAMBDA_DIR not in sys.path:
    sys.path.insert(0, LAMBDA_DIR)

# Module-scope env is required at import time (the module reads os.environ[...]
# at top level). Same pattern as tests/test_fgs1_campaign_security.py.
with patch.dict(os.environ, {
    'SIGNAL_CATALOG_TABLE': 'test-signal-catalog',
    'DATA_SOURCE_CONFIGS_TABLE': 'test-data-sources',
    'MANIFESTS_BUCKET': 'test-manifests',
    'CAMPAIGNS_TABLE': 'test-campaigns',
    'VEHICLES_TABLE': 'test-storage-vehicles',
    'DEPLOYMENT_STAGE': 'test',
}):
    import data_processing_api as api  # noqa: E402


def _cs_event(vehicles, campaign_name='c'):
    """A connected-services POST /campaigns/assign event.

    connected-services is deliberately the caller under test: it is
    ``_CALLER_FLEET_UNRESTRICTED``, so it skips the VIN-FLEET check — and it is
    the identity that actually wrote the mis-keyed staging row. A guard that
    exempted unrestricted callers would not have prevented the defect.
    """
    return {
        'httpMethod': 'POST',
        'path': '/campaigns/assign',
        'body': json.dumps({'campaignName': campaign_name, 'vehicles': vehicles}),
        'requestContext': {
            'authorizer': {
                'claims': {
                    'sub': 'test-sub',
                    'cognito:groups': 'connected-services',
                }
            }
        },
    }


def _template_row(name='c'):
    return {
        'campaignId': name,
        'campaignName': name,
        'targetArn': 'template',
        'status': 'ACTIVE',
        'owner': 'oem',
        'decoderManifestId': 'cms-fleet-v3',
    }


class _Base(unittest.TestCase):
    """Patches campaigns_table and _vehicles_ddb.

    ``RESOLVABLE`` is the set of values the vehicles mock will resolve. Anything
    else returns no Items, i.e. an unknown VIN.
    """

    RESOLVABLE: set = set()

    def setUp(self):
        self.mock_table = MagicMock()
        self.mock_table.get_item.return_value = {'Item': _template_row()}
        self.mock_table.put_item.return_value = {}
        self.mock_table.scan.return_value = {'Items': []}
        self.mock_table.query.return_value = {'Items': []}
        self.mock_table.meta = MagicMock()
        self.CCFE = type('ConditionalCheckFailedException', (Exception,), {})
        self.mock_table.meta.client.exceptions.ConditionalCheckFailedException = self.CCFE
        self.p_campaigns = patch.object(api, 'campaigns_table', self.mock_table)
        self.p_campaigns.start()

        resolvable = self.RESOLVABLE

        def fake_query(**kwargs):
            v = kwargs.get('ExpressionAttributeValues', {}).get(':v', '')
            if v in resolvable:
                return {'Items': [{'vehicleId': f'vehicle-{v}', 'vin': v}]}
            return {'Items': []}

        self.mock_vehicles = MagicMock()
        self.mock_vehicles.query.side_effect = fake_query
        self.p_vehicles = patch.object(api, '_vehicles_ddb', self.mock_vehicles)
        self.p_vehicles.start()

    def tearDown(self):
        self.p_campaigns.stop()
        self.p_vehicles.stop()

    def _body(self, resp):
        self.assertEqual(resp['statusCode'], 200, resp.get('body'))
        return json.loads(resp['body'])

    def _written_target_arns(self):
        return [
            c.kwargs['Item']['targetArn']
            for c in self.mock_table.put_item.call_args_list
        ]


class TestVehicleIdRejected(_Base):
    """A vehicleId must be refused, not written as if it were a VIN.

    MUTATION: delete the `_resolve_vin_to_vehicle_id(vin) is None` branch in
    `assign_campaign` so every entry falls through to `resolved_vins`.
    -> test_vehicleid_is_rejected and test_vehicleid_row_is_never_written FAIL.
    APPLIED 2026-09-20, both OBSERVED failing.
    """

    # The VIN resolves; the vehicleId does not. This mirrors staging, where
    # VEH-CS-DEMO-0003's VIN is CSPR0000000000003.
    RESOLVABLE = {'CSPR0000000000003'}

    def test_vehicleid_is_rejected(self):
        body = self._body(api.assign_campaign(_cs_event(['VEH-CS-DEMO-0003'])))
        self.assertEqual(body['assigned'], [])
        self.assertEqual(len(body['rejected']), 1)
        self.assertEqual(body['rejected'][0]['value'], 'VEH-CS-DEMO-0003')
        # The reason must name the actual confusion, so an operator reading the
        # API response learns what to send instead.
        self.assertIn('vehicleId', body['rejected'][0]['reason'])

    def test_vehicleid_row_is_never_written(self):
        """The load-bearing assertion: no row, not merely a warning.

        Presence of `rejected` is not enough — the defect was a WRITE. This
        asserts the write did not happen.
        """
        api.assign_campaign(_cs_event(['VEH-CS-DEMO-0003']))
        self.mock_table.put_item.assert_not_called()

    def test_resolvable_vin_in_same_request_is_still_written(self):
        """A bad entry must not poison the whole request.

        Per-entry rejection, not all-or-nothing — the fleet check above is
        all-or-nothing because it is an AUTHORIZATION decision; this is a data
        -shape decision and partial progress is safe and more useful.
        """
        body = self._body(
            api.assign_campaign(_cs_event(['VEH-CS-DEMO-0003', 'CSPR0000000000003']))
        )
        self.assertEqual(body['assigned'], ['CSPR0000000000003'])
        self.assertEqual([r['value'] for r in body['rejected']], ['VEH-CS-DEMO-0003'])
        self.assertEqual(self._written_target_arns(), ['vehicle:CSPR0000000000003'])


class TestGuardIsExistenceNotShape(_Base):
    """VINs that fail a strict VIN charset must still be accepted if they exist.

    This is the anti-regression test for the design decision. 8 of 155 staging
    vehicles carry a `vin` that a strict 17-char VIN regex rejects: 7 `DEMO…`
    values contain the letter `O` (excluded from the real VIN alphabet) and
    `VEH-ENT-001`'s VIN `4T1B11HK0LU98765` is 16 characters.

    MUTATION: replace the existence check with
    `re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', vin)`.
    -> both tests here FAIL (DEMO0000000000005 on the `O`, 4T1B11HK0LU98765 on
       length), while TestVehicleIdRejected still passes — which is exactly the
       trap: a shape guard looks correct until it meets real data.
    APPLIED 2026-09-20, both OBSERVED failing.
    """

    RESOLVABLE = {'DEMO0000000000005', '4T1B11HK0LU98765'}

    def test_vin_containing_letter_o_is_accepted(self):
        body = self._body(api.assign_campaign(_cs_event(['DEMO0000000000005'])))
        self.assertEqual(body['assigned'], ['DEMO0000000000005'])
        self.assertEqual(body['rejected'], [])

    def test_sixteen_char_vin_is_accepted(self):
        body = self._body(api.assign_campaign(_cs_event(['4T1B11HK0LU98765'])))
        self.assertEqual(body['assigned'], ['4T1B11HK0LU98765'])
        self.assertEqual(body['rejected'], [])


class TestAmbiguousVinRejected(_Base):
    """Two vehicles sharing a VIN is ambiguous — fail closed, consistent with
    `_lookup_vin_fleet_id`'s treatment of the same condition.

    MUTATION: change `_resolve_vin_to_vehicle_id`'s `len(items) != 1` to
    `len(items) < 1`. -> this test FAILS.
    APPLIED 2026-09-20, OBSERVED failing.
    """

    def setUp(self):
        super().setUp()
        self.mock_vehicles.query.side_effect = None
        self.mock_vehicles.query.return_value = {
            'Items': [
                {'vehicleId': 'vehicle-a', 'vin': 'DUPVIN00000000001'},
                {'vehicleId': 'vehicle-b', 'vin': 'DUPVIN00000000001'},
            ]
        }

    def test_ambiguous_vin_is_rejected(self):
        body = self._body(api.assign_campaign(_cs_event(['DUPVIN00000000001'])))
        self.assertEqual(body['assigned'], [])
        self.assertEqual(len(body['rejected']), 1)
        self.mock_table.put_item.assert_not_called()


class TestLookupFailureFailsClosed(_Base):
    """A vehicles-table lookup error must reject, never admit.

    A guard that admits on its own failure is not a guard. If the index read
    raises, we do not know the value is a VIN, and writing the row is the exact
    defect being prevented.

    MUTATION: change `_resolve_vin_to_vehicle_id`'s `except Exception: return None`
    to `return vin`. -> this test FAILS.
    APPLIED 2026-09-20, OBSERVED failing.
    """

    def setUp(self):
        super().setUp()
        self.mock_vehicles.query.side_effect = RuntimeError('index unavailable')

    def test_lookup_error_rejects_and_writes_nothing(self):
        body = self._body(api.assign_campaign(_cs_event(['CSPR0000000000003'])))
        self.assertEqual(body['assigned'], [])
        self.assertEqual(len(body['rejected']), 1)
        self.mock_table.put_item.assert_not_called()


class TestAlreadyAssignedIsSuccess(_Base):
    """An existing row is idempotent success, reported in its own list.

    MUTATION: in the `ConditionalCheckFailedException` handler, replace
    `already_assigned.append(vin)` with `pass` (the pre-fix behaviour).
    -> test_already_assigned_is_reported FAILS.
    APPLIED 2026-09-20, OBSERVED failing.
    """

    RESOLVABLE = {'CSPR0000000000003'}

    def setUp(self):
        super().setUp()
        self.mock_table.put_item.side_effect = self.CCFE('exists')

    def test_already_assigned_is_reported(self):
        body = self._body(api.assign_campaign(_cs_event(['CSPR0000000000003'])))
        self.assertEqual(body['assigned'], [])
        self.assertEqual(body['alreadyAssigned'], ['CSPR0000000000003'])
        self.assertEqual(body['rejected'], [])

    def test_already_assigned_is_distinguishable_from_rejection(self):
        """The whole point: a client can now tell success from failure.

        Before the fix both produced `assigned: []` and nothing else, so the CS
        UI could not distinguish them and chose the reading that produced an
        unclearable error.
        """
        body = self._body(api.assign_campaign(_cs_event(['CSPR0000000000003'])))
        self.assertTrue(
            bool(body['alreadyAssigned']) and not body['rejected'],
            'already-assigned must be positively distinguishable from rejected',
        )


class TestEmptyVehicleIdIsUnresolved(_Base):
    """A row whose `vehicleId` is falsy must not admit the write.

    The caller tests `is None`, so returning `''` would pass the guard. Unreachable
    today (`vehicleId` is the table PK) — asserted so it stays unreachable.

    MUTATION: drop the `if not vehicle_id: return None` check so the resolver
    returns `''`. -> this test FAILS.
    APPLIED 2026-09-20, OBSERVED failing.
    """

    def setUp(self):
        super().setUp()
        self.mock_vehicles.query.side_effect = None
        self.mock_vehicles.query.return_value = {
            'Items': [{'vehicleId': '', 'vin': 'CSPR0000000000003'}]
        }

    def test_empty_vehicle_id_rejects_and_writes_nothing(self):
        body = self._body(api.assign_campaign(_cs_event(['CSPR0000000000003'])))
        self.assertEqual(body['assigned'], [])
        self.assertEqual(len(body['rejected']), 1)
        self.mock_table.put_item.assert_not_called()


class TestAssignedKeepsItsMeaning(_Base):
    """`assigned` must remain "rows NEWLY written".

    Measured 2026-09-20: three cms_ui sites POST this route, and exactly ONE reads
    `assigned` — `CampaignViewer.tsx:145` reports `data.assigned?.length`. Folding
    already-assigned into `assigned` would silently change that number.
    (`FleetDetailsPage.tsx:644` posts the different `/api/v1/fleet-campaigns/assign`
    route and is not affected.)

    MUTATION: append to `assigned` instead of `already_assigned` in the
    conditional-check handler. -> this test FAILS.
    APPLIED 2026-09-20, OBSERVED failing.
    """

    RESOLVABLE = {'VIN0000000000000A', 'VIN0000000000000B'}

    def test_assigned_excludes_already_assigned(self):
        calls = {'n': 0}

        def put(**kwargs):
            calls['n'] += 1
            if calls['n'] == 2:
                raise self.CCFE('exists')
            return {}

        self.mock_table.put_item.side_effect = put
        body = self._body(
            api.assign_campaign(_cs_event(['VIN0000000000000A', 'VIN0000000000000B']))
        )
        self.assertEqual(body['assigned'], ['VIN0000000000000A'])
        self.assertEqual(body['alreadyAssigned'], ['VIN0000000000000B'])


if __name__ == '__main__':
    unittest.main()
