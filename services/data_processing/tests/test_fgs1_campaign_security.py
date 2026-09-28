"""
FGS1 campaign security fixes — properties and mutation anchors.
Spec: .kiro/specs/2026-09-15-cms-cs-campaign-ownership/
Fix Group S1 (FGS1.1–FGS1.3).  Mutation records in decisions.md.

Properties covered:
  FGS1.1 — VIN-fleet membership check in assign_campaign:
    P1. A fleet-operator assigning to a cross-fleet VIN is refused (403).
    P2. The check is per-VIN, not per-request: a MIXED list (one own, one
        foreign) is refused even though the first VIN passes (mutation: check
        only the first VIN → a mixed-list test MUST FAIL).
    P3. A fleet-operator assigning to a fleetId outside its own set is refused.
    P4. An unknown VIN (lookup returns None) is refused, not silently passed.
    P5. platform-admin and connected-services are unrestricted.
    P6. A fleet-operator assigning its own VINs (lookup matches caller's fleet)
        is admitted.

  FGS1.1 (CDK grant scope):
    P7. The IAM grant covers only the vin-index ARN and the table ARN — not a
        wildcard (mutation: widen to table/* → test MUST FAIL).

  FGS1.2 — validate-all-then-delete in unassign_campaign:
    P8. A refused mixed batch performs ZERO delete_item calls (mutation:
        restore interleaved order → delete_item IS called, FAIL).

  FGS1.3 — fail-closed on unknown HTTP method:
    P9. An unrecognised method (PATCH) is classified as a write — fleet-viewer
        is refused (mutation: reclassify PATCH back to READ → fleet-viewer
        admitted, FAIL).

All ownership probes go through handler() — the real dispatcher.  Calling
route functions directly hides authz bypasses (see decisions.md § "handler()
not direct calls").
"""

import json
import os
import sys
import textwrap
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
    'VEHICLES_TABLE': 'test-storage-vehicles',
    'DEPLOYMENT_STAGE': 'test',
}):
    import data_processing_api as api  # noqa: E402


# ---------------------------------------------------------------------------
# Event builders (re-uses the same helpers as test_fg13_campaign_guard.py)
# ---------------------------------------------------------------------------

def _base_claims(groups, fleet_ids=None, sub='test-sub'):
    claims = {'cognito:groups': ','.join(groups), 'sub': sub}
    if fleet_ids is not None:
        claims['custom:fleetIds'] = fleet_ids
    return claims


def _event(method, path, groups, body=None, query=None, fleet_ids=None, sub='test-sub'):
    return {
        'httpMethod': method,
        'path': path,
        'queryStringParameters': query or {},
        'body': json.dumps(body) if body is not None else None,
        'requestContext': {
            'authorizer': {
                'claims': _base_claims(groups, fleet_ids=fleet_ids, sub=sub)
            }
        },
    }


def _fleet_op(method, path, fleet_ids, body=None, query=None, sub='test-sub'):
    return _event(method, path, groups=['fleet-operator'],
                  fleet_ids=fleet_ids, body=body, query=query, sub=sub)


def _admin(method, path, body=None, query=None):
    return _event(method, path, groups=['platform-admin'],
                  body=body, query=query)


def _viewer(method, path, body=None, query=None, fleet_ids=None):
    return _event(method, path, groups=['fleet-viewer'],
                  body=body, query=query, fleet_ids=fleet_ids)


def _cs(method, path, body=None, query=None):
    return _event(method, path, groups=['connected-services'],
                  body=body, query=query)


# ---------------------------------------------------------------------------
# Vehicles mock factory
# ---------------------------------------------------------------------------

def _vehicles_mock(vin_to_fleet: dict) -> MagicMock:
    """Return a mock _vehicles_ddb that resolves VINs according to vin_to_fleet.

    If a VIN is absent from the dict, query returns no Items (VIN unknown).
    query() is called with vin in ExpressionAttributeValues[':v'].
    get_item() is called with vehicleId in Key.
    scan() returns no Items (no VINs in fleet by default).
    """
    mock = MagicMock()

    def fake_query(**kwargs):
        vin = kwargs.get('ExpressionAttributeValues', {}).get(':v', '')
        if vin in vin_to_fleet:
            return {'Items': [{'vehicleId': f'vehicle-{vin}', 'vin': vin}]}
        return {'Items': []}

    def fake_get_item(**kwargs):
        vehicle_id = kwargs.get('Key', {}).get('vehicleId', '')
        # Reverse-map vehicleId → vin (vehicle-{vin} pattern above)
        if vehicle_id.startswith('vehicle-'):
            vin = vehicle_id[len('vehicle-'):]
            fleet_id = vin_to_fleet.get(vin)
            if fleet_id:
                return {'Item': {'vehicleId': vehicle_id, 'fleetId': fleet_id}}
        return {}

    def fake_scan(**kwargs):
        # Return no items; no LastEvaluatedKey so the scan loop terminates.
        return {'Items': []}

    mock.query.side_effect = fake_query
    mock.get_item.side_effect = fake_get_item
    mock.scan.side_effect = fake_scan
    return mock


# ---------------------------------------------------------------------------
# Base mock setup
# ---------------------------------------------------------------------------

class _Base(unittest.TestCase):
    """Base class: patches campaigns_table and _vehicles_ddb."""

    # Subclasses override to supply a vin→fleet mapping for the vehicles mock.
    # Defaults to an empty map (all VINs unknown).
    _VIN_TO_FLEET: dict = {}

    def setUp(self):
        self.mock_table = MagicMock()
        self.mock_table.scan.return_value = {'Items': []}
        self.mock_table.query.return_value = {'Items': []}
        self.mock_table.get_item.return_value = {}
        self.mock_table.put_item.return_value = {}
        self.mock_table.update_item.return_value = {}
        self.mock_table.delete_item.return_value = {}
        self.mock_table.meta = MagicMock()
        self.mock_table.meta.client.exceptions.ConditionalCheckFailedException = type(
            'ConditionalCheckFailedException', (Exception,), {}
        )
        self.patcher_campaigns = patch.object(api, 'campaigns_table', self.mock_table)
        self.patcher_campaigns.start()

        self.mock_vehicles = _vehicles_mock(self._VIN_TO_FLEET)
        self.patcher_vehicles = patch.object(api, '_vehicles_ddb', self.mock_vehicles)
        self.patcher_vehicles.start()

    def tearDown(self):
        self.patcher_campaigns.stop()
        self.patcher_vehicles.stop()

    def _template_row(self, name='c'):
        return {
            'campaignId': name,
            'campaignName': name,
            'targetArn': 'template',
            'status': 'ACTIVE',
            'owner': 'oem',
        }

    def _delete_item_count(self):
        return self.mock_table.delete_item.call_count


# ===========================================================================
# FGS1.1 — P1: Cross-fleet VIN assign is refused
# ===========================================================================

class TestAssignCrossFleetVINRefused(_Base):
    """P1: fleet-A operator assigning to a fleet-B VIN must be refused (403).

    MUTATION TARGET (a): remove the VIN-fleet check entirely → the cross-fleet
    assign succeeds (200), FAIL.
    Record in decisions.md under FGS1.1 mutation (a).
    """

    _VIN_TO_FLEET = {
        'VIN-B1': 'fleet-B',  # caller is fleet-A; this VIN belongs to fleet-B
    }

    def test_cross_fleet_vin_assign_refused(self):
        """fleet-A assigns a fleet-B VIN → 403.

        MUTATION TARGET (a): remove the _lookup_vin_fleet_id check →
        put_item is called and 200 is returned, FAIL.
        """
        self.mock_table.get_item.return_value = {'Item': self._template_row()}
        ev = _fleet_op(
            'POST', '/campaigns/assign', 'fleet-A',
            body={'campaignName': 'c', 'vehicles': ['VIN-B1']},
        )
        resp = api.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 403,
            "fleet-A assigning a fleet-B VIN must be refused (403). "
            "MUTATION (a): remove VIN-fleet check → 200, FAIL.",
        )
        self.mock_table.put_item.assert_not_called()

    def test_cross_fleet_vin_performs_no_write(self):
        """Refusal on a cross-fleet VIN must perform zero put_item calls."""
        self.mock_table.get_item.return_value = {'Item': self._template_row()}
        ev = _fleet_op(
            'POST', '/campaigns/assign', 'fleet-A',
            body={'campaignName': 'c', 'vehicles': ['VIN-B1']},
        )
        api.handler(ev, {})
        self.mock_table.put_item.assert_not_called()


# ===========================================================================
# FGS1.1 — P2: per-VIN check, not per-request (mixed-list test)
# ===========================================================================

class TestAssignMixedVINListRefused(_Base):
    """P2: a MIXED VIN list (own VIN + foreign VIN) is refused, not partially accepted.

    This is the critical per-VIN vs per-request distinction.  A uniform list
    (all own or all foreign) passes under both implementations.  Only a MIXED
    list distinguishes "check only the first VIN" from "check every VIN".

    MUTATION TARGET (b): check only the first VIN (break after first check) →
    the mixed-list test MUST FAIL (200 returned, put_item called for VIN-A1).
    Record in decisions.md under FGS1.1 mutation (b).
    """

    _VIN_TO_FLEET = {
        'VIN-A1': 'fleet-A',  # caller's fleet — passes if checked
        'VIN-B1': 'fleet-B',  # foreign VIN — must be caught
    }

    def test_mixed_list_refused_whole_request(self):
        """fleet-A + [own VIN, foreign VIN] → 403, zero put_item calls.

        MUTATION TARGET (b): check only the first VIN → 200 returned and
        put_item is called for VIN-A1 (the own VIN), FAIL.
        """
        self.mock_table.get_item.return_value = {'Item': self._template_row()}
        ev = _fleet_op(
            'POST', '/campaigns/assign', 'fleet-A',
            body={'campaignName': 'c', 'vehicles': ['VIN-A1', 'VIN-B1']},
        )
        resp = api.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 403,
            "A mixed VIN list must refuse the whole request, not partially accept. "
            "MUTATION (b): check only the first VIN → 200, FAIL.",
        )
        self.mock_table.put_item.assert_not_called()

    def test_uniform_own_list_admitted(self):
        """All VINs in caller's fleet → admitted (distinguishes the mutation)."""
        self.mock_table.get_item.return_value = {'Item': self._template_row()}
        ev = _fleet_op(
            'POST', '/campaigns/assign', 'fleet-A',
            body={'campaignName': 'c', 'vehicles': ['VIN-A1']},
        )
        resp = api.handler(ev, {})
        self.assertNotEqual(
            resp['statusCode'], 403,
            "All-own VIN list must be admitted.",
        )


# ===========================================================================
# FGS1.1 — P3: fleetId path scoped to caller's fleet set
# ===========================================================================

class TestAssignFleetIdScopeCheck(_Base):
    """P3: a fleetId-only body on the per-vehicle endpoint returns 400.

    The vehicles table has no fleetId GSI and a Scan would be O(table size) on
    a hot path.  The per-vehicle endpoint rejects fleetId-only requests and
    directs callers to POST /api/v1/fleet-campaigns/assign instead
    (decisions.md § "FGS2.1: Scan deleted").

    MUTATION TARGETS for S2.1 (recorded in decisions.md):
    (a) restore the scan call → test asserting _vehicles_ddb.scan never called FAILS.
    (b) return 200 instead of 400 → this test FAILS.
    """

    _VIN_TO_FLEET = {}

    def test_fleet_id_only_returns_400(self):
        """fleetId-only body (no vehicles) → 400 naming the fleet-wide endpoint.

        S2.1 MUTATION (b): return 200 instead of 400 → FAIL.
        """
        self.mock_table.get_item.return_value = {'Item': self._template_row()}
        ev = _fleet_op(
            'POST', '/campaigns/assign', 'fleet-A',
            body={'campaignName': 'c', 'fleetId': 'fleet-A'},
        )
        resp = api.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 400,
            "fleetId-only body on the per-vehicle endpoint must return 400. "
            "S2.1 MUTATION (b): return 200 instead of 400 → FAIL.",
        )
        body = json.loads(resp['body'])
        self.assertIn(
            '/api/v1/fleet-campaigns/assign', body.get('error', ''),
            "400 error message must name the fleet-wide endpoint.",
        )
        self.mock_table.put_item.assert_not_called()

    def test_scan_never_called_on_assign(self):
        """_vehicles_ddb.scan must never be called on any assign path.

        S2.1 MUTATION (a): restore the scan call → this assertion FAILS.
        Validates that no Scan grant is needed (decisions.md § "FGS2.1: Scan deleted").
        """
        self.mock_table.get_item.return_value = {'Item': self._template_row()}
        # Probe via fleetId-only (the deleted path).
        ev_fleet = _fleet_op(
            'POST', '/campaigns/assign', 'fleet-A',
            body={'campaignName': 'c', 'fleetId': 'fleet-A'},
        )
        api.handler(ev_fleet, {})
        self.mock_vehicles.scan.assert_not_called(
        )

    def test_vehicles_plus_fleet_id_honours_vehicles(self):
        """When both vehicles and fleetId are supplied, vehicles is honoured.

        The fleetId field is ignored (no scan, no 400) when a vehicles list
        is also present — the caller gets per-vehicle semantics.
        S2.1 choice: treat vehicles-present as the vehicles path.
        """
        self.mock_vehicles.query.return_value = {
            'Items': [{'vehicleId': 'vehicle-VIN-A1', 'vin': 'VIN-A1'}]
        }
        self.mock_vehicles.get_item.side_effect = None
        self.mock_vehicles.get_item.return_value = {
            'Item': {'vehicleId': 'vehicle-VIN-A1', 'fleetId': 'fleet-A'}
        }
        self.mock_table.get_item.return_value = {'Item': self._template_row()}
        ev = _fleet_op(
            'POST', '/campaigns/assign', 'fleet-A',
            body={'campaignName': 'c', 'vehicles': ['VIN-A1'], 'fleetId': 'fleet-A'},
        )
        resp = api.handler(ev, {})
        self.assertNotEqual(
            resp['statusCode'], 400,
            "vehicles-present path must not return 400 even when fleetId is also supplied.",
        )
        self.mock_vehicles.scan.assert_not_called()


# ===========================================================================
# FGS1.1 — P4: unknown VIN is refused (fail-closed)
# ===========================================================================

class TestAssignUnknownVINRefused(_Base):
    """P4: a VIN that returns None from _lookup_vin_fleet_id is refused.

    An unknown VIN must not be an implicit pass.  The lookup returns None when
    the vin-index query returns no items.

    MUTATION TARGET (d): treat a None result as unrestricted (permit the
    assign) → 200 returned and put_item called, FAIL.
    Record in decisions.md under FGS1.1 mutation (d).
    """

    _VIN_TO_FLEET = {}  # empty map → all VINs unknown

    def test_unknown_vin_refused(self):
        """VIN with no vehicles-table entry → 403 (fail closed).

        MUTATION TARGET (d): treat None as unrestricted → 200, FAIL.
        """
        self.mock_table.get_item.return_value = {'Item': self._template_row()}
        ev = _fleet_op(
            'POST', '/campaigns/assign', 'fleet-A',
            body={'campaignName': 'c', 'vehicles': ['VIN-UNKNOWN']},
        )
        resp = api.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 403,
            "An unknown VIN must be refused (fail closed). "
            "MUTATION (d): treat None as unrestricted → 200, FAIL.",
        )
        self.mock_table.put_item.assert_not_called()


# ===========================================================================
# FGS1.1 — P5 & P6: unrestricted callers and own-fleet admits
# ===========================================================================

class TestAssignUnrestrictedAndOwnFleet(_Base):
    """P5: admin/CS callers bypass the VIN-fleet check.
    P6: a fleet-operator assigning its own VINs is admitted.
    """

    _VIN_TO_FLEET = {
        'VIN-A1': 'fleet-A',
        'VIN-A2': 'fleet-A',
    }

    def test_admin_unrestricted(self):
        """platform-admin may assign any VIN without a VIN-fleet check."""
        self.mock_table.get_item.return_value = {'Item': self._template_row()}
        ev = _admin(
            'POST', '/campaigns/assign',
            body={'campaignName': 'c', 'vehicles': ['VIN-A1']},
        )
        resp = api.handler(ev, {})
        self.assertNotEqual(resp['statusCode'], 403,
                            "platform-admin must not be refused on assign.")
        # The VIN-FLEET check must not run for an admin caller. Asserted via
        # get_item, which is step 2 of `_lookup_vin_fleet_id` (vehicleId → fleetId)
        # and is reached ONLY by the fleet check.
        #
        # This was `query.assert_not_called()` until 2026-09-20. That is no longer
        # the right assertion: the VIN-EXISTENCE guard added for
        # issues/2026-09-20-cs-quick-assign-writes-vehicleid-keyed-campaign-row/
        # queries `vin-index` for EVERY caller, unrestricted included — deliberately,
        # because the mis-keyed row that motivated it was written by an unrestricted
        # connected-services caller. Both checks share the `query` mock, so
        # `assert_not_called` could no longer distinguish "fleet check skipped" from
        # "no lookup at all". get_item can.
        self.mock_vehicles.get_item.assert_not_called()
        # And the one query that DID run must be the bounded existence probe, not a
        # Scan or an unbounded read — the availability concern the original
        # assertion was standing in for.
        self.assertEqual(self.mock_vehicles.query.call_count, 1)
        _kw = self.mock_vehicles.query.call_args.kwargs
        self.assertEqual(_kw.get('IndexName'), 'vin-index')
        self.assertEqual(_kw.get('Limit'), 2)
        # The probe must be for the VIN we sent, not merely well-shaped.
        self.assertEqual(
            _kw.get('ExpressionAttributeValues', {}).get(':v'), 'VIN-A1')
        self.mock_vehicles.scan.assert_not_called()

    def test_connected_services_unrestricted(self):
        """connected-services may assign any VIN without a VIN-fleet check."""
        self.mock_table.get_item.return_value = {'Item': self._template_row()}
        ev = _cs(
            'POST', '/campaigns/assign',
            body={'campaignName': 'c', 'vehicles': ['VIN-A1']},
        )
        resp = api.handler(ev, {})
        self.assertNotEqual(resp['statusCode'], 403,
                            "connected-services must not be refused on assign.")
        # See test_admin_unrestricted for why this is get_item and not query.
        self.mock_vehicles.get_item.assert_not_called()
        self.assertEqual(self.mock_vehicles.query.call_count, 1)
        _kw = self.mock_vehicles.query.call_args.kwargs
        self.assertEqual(_kw.get('IndexName'), 'vin-index')
        self.assertEqual(_kw.get('Limit'), 2)
        self.assertEqual(
            _kw.get('ExpressionAttributeValues', {}).get(':v'), 'VIN-A1')
        self.mock_vehicles.scan.assert_not_called()

    def test_fleet_operator_own_vin_admitted(self):
        """fleet-A assigning its own VINs is admitted."""
        self.mock_table.get_item.return_value = {'Item': self._template_row()}
        ev = _fleet_op(
            'POST', '/campaigns/assign', 'fleet-A',
            body={'campaignName': 'c', 'vehicles': ['VIN-A1', 'VIN-A2']},
        )
        resp = api.handler(ev, {})
        self.assertNotEqual(resp['statusCode'], 403,
                            "fleet-A assigning its own VINs must be admitted.")


# ===========================================================================
# FGS1.1 — P7 (CDK grant scope): IAM must NOT use a table-level wildcard
# ===========================================================================

class TestCDKGrantScope(unittest.TestCase):
    """P7: the IAM grant for vehicles lookup must be scoped to the vin-index
    and the table, never to a wildcard resource (table/*).

    This tests the CDK TEMPLATE, not the runtime code.  We read the synth
    output and verify the PolicyDocument resources do not contain a wildcard.

    MUTATION: widen the resource to 'table/*' in data_processing_stack.py →
    the assertion that no resource ends with '/vin-index' fails.
    Record in decisions.md under FGS1.1 mutation (grant-scope).
    """

    def _synth_resources(self):
        """Return the list of IAM statement resources from the data-processing
        stack's synth output (cached cdk.out directory).

        Uses the staging template specifically because the add_to_role_policy
        statements with self.region/self.account resolve to literal ARN strings
        only when a concrete environment (account + region) is known.  The
        test/dev templates use CDK tokens (Fn::Sub etc.) which don't carry
        'vin-index' as a human-readable string.
        """
        import glob

        # Prefer the staging template (where env-specific ARNs are literal).
        staging_template = os.path.join(
            os.path.dirname(__file__),
            '..', '..', '..', 'deployment', 'cdk.out',
            'cms-staging-data-processing.template.json',
        )
        if not os.path.exists(staging_template):
            # FAIL, do not skip. A guard that silently disappears on a fresh
            # clone or in CI is not a guard — and skipping on a missing artifact
            # is the same shape as the vacuous-pass defects this spec has already
            # shipped twice (FG9.3's fabricated mock, FG11.1's circular guard).
            # The IAM scope this asserts is the property that keeps a
            # cross-fleet VIN lookup from becoming table-wide read access, so
            # its absence must be loud.
            self.fail(
                'cms-staging-data-processing.template.json not found, so the IAM '
                'grant-scope assertions cannot run. Regenerate it:\n'
                '  cd deployment && ( set -a; . config/staging.env; set +a; \\\n'
                '    DEPLOYMENT_STAGE=staging DRIVER_SELF_GUARD_ENABLED=true \\\n'
                '    CLIENT_EXTRA_IDPS=AmazonFederate FEDERATE_CLIENT_ID=x \\\n'
                '    FEDERATE_CLIENT_SECRET=x \\\n'
                '    COGNITO_DOMAIN_PREFIX=connected-mobility-staging \\\n'
                '    cdk synth cms-staging-data-processing \\\n'
                '      -c uiCustomDomain=staging.fleet.example.com \\\n'
                '      -c uiCustomDomainCertArn=<cert-arn> )\n'
                'The placeholder Federate creds and cert ARN are synth-only — this '
                'template is for shape assertions and is never deployed. A bare '
                '`cdk synth` WILL fail: app.py instantiates every stack, so '
                "ui_stack's Federate and uiCustomDomain guards fire without those "
                'values. That is those guards working, not a problem with this test.'
            )
        with open(staging_template) as f:
            tpl = json.load(f)

        resources = []
        for logical_id, resource in tpl.get('Resources', {}).items():
            if resource.get('Type') != 'AWS::IAM::Policy':
                continue
            for stmt in (resource.get('Properties', {})
                         .get('PolicyDocument', {})
                         .get('Statement', [])):
                actions = stmt.get('Action', [])
                if isinstance(actions, str):
                    actions = [actions]
                res = stmt.get('Resource', [])
                if isinstance(res, str):
                    res = [res]
                for r in res:
                    resources.append((logical_id, actions, r))
        return resources

    @staticmethod
    def _resource_str(r) -> str:
        """Serialize a resource value (may be a CFN intrinsic) to a string
        for pattern matching."""
        return json.dumps(r) if not isinstance(r, str) else r

    def test_query_grant_scoped_to_vin_index(self):
        """dynamodb:Query grant must target the vin-index ARN, not a wildcard.

        The CDK template uses Fn::Join to construct ARNs; resources appear as
        {'Fn::Join': ['', [..., '/index/vin-index']]} rather than literal strings.
        We stringify the resource value (JSON-encode intrinsics) and search for
        'vin-index' in the serialised form.

        MUTATION (grant-scope): change the resource to
        'arn:...table/cms-staging-storage-vehicles/*' →
        the assertion that some Query resource contains 'vin-index' FAILS.
        Record in decisions.md.
        """
        resources = self._synth_resources()
        query_resources = [
            r for _, actions, r in resources
            if 'dynamodb:Query' in actions
        ]
        has_vin_index = any(
            'vin-index' in self._resource_str(r)
            for r in query_resources
        )
        self.assertTrue(
            has_vin_index,
            "dynamodb:Query must target the vin-index ARN specifically. "
            "MUTATION (grant-scope): widen to table wildcard → FAIL. "
            f"Query resources found: {[self._resource_str(r) for r in query_resources]}",
        )

    def test_no_wildcard_vehicle_resource(self):
        """No IAM statement resource for the vehicles table must be a wildcard.

        The CDK template uses Fn::Join to construct ARNs; a wildcard resource
        would end with '/*' in the serialised Fn::Join form.

        MUTATION (grant-scope): change the resource to 'table/*' →
        this assertion FAILS.
        Record in decisions.md.
        """
        resources = self._synth_resources()
        wildcard_vehicle_resources = [
            r for _, _, r in resources
            if 'storage-vehicles' in self._resource_str(r)
               and self._resource_str(r).rstrip('"').endswith('/*')
        ]
        self.assertEqual(
            wildcard_vehicle_resources, [],
            "No vehicles-table IAM statement must use a wildcard resource. "
            "MUTATION (grant-scope): widen to table/* → FAIL. "
            f"Found wildcard resources: {[self._resource_str(r) for r in wildcard_vehicle_resources]}",
        )


# ===========================================================================
# FGS1.2 — P8: validate-all-then-delete in unassign_campaign
# ===========================================================================

class TestUnassignValidateAllThenDelete(_Base):
    """P8: unassign_campaign validates all rows BEFORE deleting any.

    A single unauthorized row in a mixed batch must refuse the whole operation
    with ZERO delete_item calls.

    MUTATION TARGET (e): restore the interleaved (delete-then-validate) order →
    delete_item IS called for the authorised VIN before the refused VIN is
    reached, FAIL.
    Record in decisions.md under FGS1.2 mutation (e).
    """

    _VIN_TO_FLEET = {}

    def test_mixed_batch_zero_deletes(self):
        """[own VIN, oem-owned VIN] in one batch → 403 + 0 delete_item calls.

        Setup: fleet-A caller, two VINs:
          - VIN-A1: owned by fleet:fleet-A (would pass individually)
          - VIN-OEM: owned by oem (unauthorised)

        Validate-all-then-delete: no delete_item is made before the
        validation pass completes.

        MUTATION TARGET (e): delete rows during iteration (before validation
        completes) → delete_item IS called for VIN-A1, FAIL.
        """
        # Return the VIN-A1 row then the VIN-OEM row in sequence.
        rows = {
            'c-VIN-A1': {'campaignId': 'c-VIN-A1', 'campaignName': 'c',
                         'owner': 'fleet:fleet-A', 'targetArn': 'vehicle:VIN-A1'},
            'c-VIN-OEM': {'campaignId': 'c-VIN-OEM', 'campaignName': 'c',
                          'owner': 'oem', 'targetArn': 'vehicle:VIN-OEM'},
        }

        def _get_item(**kwargs):
            key = kwargs.get('Key', {}).get('campaignId', '')
            item = rows.get(key)
            return {'Item': item} if item else {}

        self.mock_table.get_item.side_effect = _get_item

        ev = _fleet_op(
            'DELETE', '/campaigns/assign', 'fleet-A',
            body={'campaignName': 'c', 'vehicles': ['VIN-A1', 'VIN-OEM']},
        )
        resp = api.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 403,
            "Mixed batch with an oem-owned VIN must return 403.",
        )
        self.assertEqual(
            self._delete_item_count(), 0,
            "A refused unassign batch must perform ZERO delete_item calls. "
            "MUTATION (e): interleaved delete-before-validate → delete_item "
            "IS called for VIN-A1, FAIL.",
        )

    def test_all_own_batch_deletes_all(self):
        """All own-fleet VINs → 200 + all VINs deleted.

        Validates the positive path (a fully owned batch still works).
        """
        rows = {
            'c-VIN-A1': {'campaignId': 'c-VIN-A1', 'campaignName': 'c',
                         'owner': 'fleet:fleet-A', 'targetArn': 'vehicle:VIN-A1'},
            'c-VIN-A2': {'campaignId': 'c-VIN-A2', 'campaignName': 'c',
                         'owner': 'fleet:fleet-A', 'targetArn': 'vehicle:VIN-A2'},
        }

        def _get_item(**kwargs):
            key = kwargs.get('Key', {}).get('campaignId', '')
            item = rows.get(key)
            return {'Item': item} if item else {}

        self.mock_table.get_item.side_effect = _get_item

        ev = _fleet_op(
            'DELETE', '/campaigns/assign', 'fleet-A',
            body={'campaignName': 'c', 'vehicles': ['VIN-A1', 'VIN-A2']},
        )
        resp = api.handler(ev, {})
        self.assertEqual(resp['statusCode'], 200,
                         "All own-fleet VINs must be unassigned (200).")
        self.assertEqual(self._delete_item_count(), 2,
                         "Two owned VINs → two delete_item calls.")

    def test_admin_mixed_batch_admits_all(self):
        """platform-admin unassigns a mixed-owner batch without restriction."""
        rows = {
            'c-VIN-A1': {'campaignId': 'c-VIN-A1', 'owner': 'fleet:fleet-A'},
            'c-VIN-OEM': {'campaignId': 'c-VIN-OEM', 'owner': 'oem'},
        }

        def _get_item(**kwargs):
            key = kwargs.get('Key', {}).get('campaignId', '')
            item = rows.get(key)
            return {'Item': item} if item else {}

        self.mock_table.get_item.side_effect = _get_item

        ev = _admin(
            'DELETE', '/campaigns/assign',
            body={'campaignName': 'c', 'vehicles': ['VIN-A1', 'VIN-OEM']},
        )
        resp = api.handler(ev, {})
        self.assertNotEqual(resp['statusCode'], 403,
                            "platform-admin must not be refused on mixed unassign.")
        self.assertEqual(self._delete_item_count(), 2,
                         "Admin must delete both rows.")


# ===========================================================================
# FGS1.3 — P9: fail-closed on unknown HTTP method
# ===========================================================================

class TestUnknownMethodFailClosed(_Base):
    """P9: an unrecognised HTTP method (PATCH, HEAD, OPTIONS, …) is treated
    as a write — fleet-viewer is refused.

    MUTATION TARGET (f): reclassify PATCH back to READ → fleet-viewer is
    admitted on PATCH /campaigns, FAIL.
    Record in decisions.md under FGS1.3 mutation (f).
    """

    _VIN_TO_FLEET = {}

    def test_patch_refused_for_fleet_viewer(self):
        """PATCH /campaigns is refused for fleet-viewer (classified as write).

        MUTATION TARGET (f): classify PATCH as READ → fleet-viewer admitted →
        FAIL.
        """
        ev = _viewer('PATCH', '/campaigns',
                     body={'campaignId': 'c', 'status': 'ACTIVE'})
        resp = api.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 403,
            "fleet-viewer must not be admitted on PATCH /campaigns. "
            "MUTATION (f): classify PATCH as READ → fleet-viewer admitted, FAIL.",
        )
        # No DynamoDB calls should have been made before the authz gate.
        self.mock_table.get_item.assert_not_called()
        self.mock_table.update_item.assert_not_called()

    def test_patch_admitted_for_fleet_operator(self):
        """PATCH /campaigns is NOT refused at the authz gate for fleet-operator.

        fleet-operator is in _CAMPAIGN_WRITE_GROUPS.  PATCH is now classified
        as a write, so the gate admits the caller.  The route dispatcher has
        no PATCH handler inside /campaigns, so the function falls through
        (returns None); the important property is that the gate does NOT return
        403.  We test this by verifying _require_campaign_access does not raise.
        """
        # Drive directly through _require_campaign_access without going through
        # handler() — because the route dispatcher returns None (no route for PATCH),
        # we can't inspect handler()'s return value.  Testing the gate function
        # directly is appropriate here: the property being tested is "the gate
        # does not refuse fleet-operator on an unrecognised method", not "the
        # route dispatcher handles PATCH".
        ev = _fleet_op('PATCH', '/campaigns', 'fleet-A',
                       body={'campaignId': 'c', 'status': 'ACTIVE'})
        try:
            api._require_campaign_access(ev)
            # No exception raised — gate admitted the caller. Good.
        except api._Unauthorized as exc:
            self.fail(
                f"fleet-operator must not be refused by the authz gate on PATCH; "
                f"got _Unauthorized: {exc}"
            )

    def test_head_refused_for_fleet_viewer(self):
        """HEAD /campaigns is also refused for fleet-viewer (fail-closed)."""
        ev = _viewer('HEAD', '/campaigns')
        resp = api.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 403,
            "fleet-viewer must be refused on an unrecognised method (HEAD). "
            "Fail-closed on unknown methods.",
        )

    def test_get_still_admits_fleet_viewer(self):
        """GET /campaigns continues to admit fleet-viewer (regression guard).

        Verifies that the fail-closed logic does not accidentally reclassify
        GET as a write.
        """
        self.mock_table.scan.return_value = {'Items': []}
        ev = _viewer('GET', '/campaigns')
        resp = api.handler(ev, {})
        self.assertNotEqual(
            resp['statusCode'], 403,
            "GET /campaigns must still admit fleet-viewer. "
            "Fail-closed logic must not reclassify GET.",
        )



# ===========================================================================
# FGS2.2 — outer handler does not reflect raw AWS errors to caller
# ===========================================================================

class TestOuterHandlerDoesNotLeakARN(_Base):
    """S2.2 (promoted): the outer except handler must not reflect ClientError
    messages (carrying account id / role ARN) back in the response body.

    Spec: decisions.md § "FGS2.2: generic 500 body".
    MUTATION: restore str(e) in the client body → assertion that body contains
    neither 'arn:aws:' nor a 12-digit account id FAILS.
    """

    _VIN_TO_FLEET = {}

    def _client_error(self, account_id='123456789012', role_arn=None):
        """Construct a real botocore ClientError with an ARN-bearing message.

        A bare Exception('arn:...') proves less than the shape the service
        actually returns — botocore wraps errors in a specific structure whose
        string representation embeds the account id and assumed-role ARN.
        """
        from botocore.exceptions import ClientError
        if role_arn is None:
            role_arn = f'arn:aws:sts::{account_id}:assumed-role/cms-staging-data-processing-role/session'
        error_response = {
            'Error': {
                'Code': 'AccessDeniedException',
                'Message': (
                    f'User: {role_arn} is not authorized to perform: '
                    f'dynamodb:Scan on resource: arn:aws:dynamodb:us-west-2:'
                    f'{account_id}:table/cms-staging-storage-vehicles'
                )
            }
        }
        return ClientError(error_response, 'Scan')

    def test_client_error_body_is_generic(self):
        """ClientError from a granted table must not reach the response body.

        S2.2 MUTATION: restore str(e) in the response body → the assertion
        that the body contains neither 'arn:aws:' nor the 12-digit account id
        FAILS.  Uses a real botocore.ClientError, not a bare Exception.
        """
        # Arrange: make campaigns_table.get_item raise a ClientError (simulating
        # any granted-table error reaching the handler's outer try/except).
        account_id = '123456789012'
        self.mock_table.get_item.side_effect = self._client_error(account_id)
        ev = _admin(
            'POST', '/campaigns/assign',
            body={'campaignName': 'c', 'vehicles': ['VIN-A1']},
        )

        # Act
        resp = api.handler(ev, {})

        # Assert: status 500, but body must not carry the ARN or account id.
        self.assertEqual(resp['statusCode'], 500)
        body_text = resp['body']
        self.assertNotIn(
            'arn:aws:', body_text,
            "Response body must not contain an AWS ARN. "
            "S2.2 MUTATION: restore str(e) → body exposes ARN, FAIL.",
        )
        self.assertNotIn(
            account_id, body_text,
            "Response body must not contain the AWS account id. "
            "S2.2 MUTATION: restore str(e) → body exposes account id, FAIL.",
        )
        # Correlation id must be present (aids operator debugging).
        body = json.loads(body_text)
        self.assertIn(
            'correlationId', body,
            "Response body must include a correlationId for operator log lookup.",
        )

    def test_generic_error_message_present(self):
        """Response body must contain a generic error key (not blank)."""
        self.mock_table.get_item.side_effect = self._client_error()
        ev = _admin(
            'POST', '/campaigns/assign',
            body={'campaignName': 'c', 'vehicles': ['VIN-A1']},
        )
        resp = api.handler(ev, {})
        body = json.loads(resp['body'])
        self.assertIn('error', body, "Response body must have an 'error' key.")
        self.assertTrue(body['error'], "Error message must not be empty.")


# ===========================================================================
# FGS2.3 — ambiguous VIN (duplicate entry) is refused
# ===========================================================================

class TestAmbiguousVINRefused(_Base):
    """S2.3: _lookup_vin_fleet_id with two entries for the same VIN is ambiguous.

    The authorization answer would depend on index ordering, so both cases
    are treated as unknown (None return) and refused, matching _caller_fleet_id's
    treatment of an ambiguous fleet claim.

    Spec: decisions.md § "FGS2.3: ambiguous VIN".
    MUTATION: return the first match instead of refusing → two-entry test FAILS.
    """

    _VIN_TO_FLEET = {}

    def setUp(self):
        super().setUp()
        # Override the vehicles mock: vin-index query for 'VIN-DUP' returns
        # TWO entries (two vehicles sharing the same VIN).
        # VIN-DUP-1 maps to fleet-A (caller's fleet) — if the code takes the
        # first match, the assignment would be admitted; if it refuses on
        # ambiguity (correct), it is refused.
        def fake_query_dup(**kwargs):
            vin = kwargs.get('ExpressionAttributeValues', {}).get(':v', '')
            if vin == 'VIN-DUP':
                return {
                    'Items': [
                        {'vehicleId': 'vehicle-VIN-DUP-1'},
                        {'vehicleId': 'vehicle-VIN-DUP-2'},
                    ]
                }
            return {'Items': []}

        def fake_get_item_dup(**kwargs):
            vehicle_id = kwargs.get('Key', {}).get('vehicleId', '')
            if vehicle_id == 'vehicle-VIN-DUP-1':
                return {'Item': {'vehicleId': 'vehicle-VIN-DUP-1', 'fleetId': 'fleet-A'}}
            if vehicle_id == 'vehicle-VIN-DUP-2':
                return {'Item': {'vehicleId': 'vehicle-VIN-DUP-2', 'fleetId': 'fleet-B'}}
            return {}

        self.mock_vehicles.query.side_effect = fake_query_dup
        self.mock_vehicles.get_item.side_effect = fake_get_item_dup

    def test_duplicate_vin_assign_refused(self):
        """Two-entry VIN query → 403 (ambiguous → fail closed).

        S2.3 MUTATION: return first match instead of refusing → 200 returned,
        FAIL.  (decisions.md § "S2.3 mutation").
        """
        self.mock_table.get_item.return_value = {'Item': self._template_row()}
        ev = _fleet_op(
            'POST', '/campaigns/assign', 'fleet-A',
            body={'campaignName': 'c', 'vehicles': ['VIN-DUP']},
        )
        resp = api.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 403,
            "A VIN with two index entries is ambiguous — must be refused (403). "
            "S2.3 MUTATION: return first match → 200, FAIL.",
        )
        self.mock_table.put_item.assert_not_called()

    def test_single_entry_vin_still_works(self):
        """A VIN with exactly one index entry continues to resolve normally."""
        # Override: VIN-A1 has exactly one entry pointing to fleet-A.
        def fake_query_single(**kwargs):
            vin = kwargs.get('ExpressionAttributeValues', {}).get(':v', '')
            if vin == 'VIN-A1':
                return {'Items': [{'vehicleId': 'vehicle-VIN-A1'}]}
            return {'Items': []}

        self.mock_vehicles.query.side_effect = fake_query_single
        self.mock_vehicles.get_item.side_effect = None
        self.mock_vehicles.get_item.return_value = {
            'Item': {'vehicleId': 'vehicle-VIN-A1', 'fleetId': 'fleet-A'}
        }
        self.mock_table.get_item.return_value = {'Item': self._template_row()}
        ev = _fleet_op(
            'POST', '/campaigns/assign', 'fleet-A',
            body={'campaignName': 'c', 'vehicles': ['VIN-A1']},
        )
        resp = api.handler(ev, {})
        self.assertNotEqual(
            resp['statusCode'], 403,
            "Single-entry VIN must still resolve normally — regression guard.",
        )


# ===========================================================================
# FGS2.4 — S5: explicit 405 for unmapped methods on /campaigns
# ===========================================================================

class TestCampaigns405ForUnmappedMethods(_Base):
    """S2.4 / S5: unmapped HTTP methods on /campaigns return explicit 405.

    Previously a write-group caller on an unmapped method would fall through
    to an implicit None → the outer handler returned 500 (None has no
    statusCode).  S2.4 adds an explicit 405 so the failure mode is named.
    """

    _VIN_TO_FLEET = {}

    def test_patch_campaigns_returns_405_for_fleet_operator(self):
        """PATCH /campaigns → 405 for fleet-operator (admitted past authz gate, no route).

        The authz gate admits fleet-operator on PATCH (classified as write).
        The route dispatcher has no PATCH case → must return 405, not 500 or None.
        """
        ev = _fleet_op('PATCH', '/campaigns', 'fleet-A',
                       body={'campaignId': 'c', 'status': 'ACTIVE'})
        resp = api.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 405,
            "fleet-operator on PATCH /campaigns must receive 405 (not 500 / None).",
        )
