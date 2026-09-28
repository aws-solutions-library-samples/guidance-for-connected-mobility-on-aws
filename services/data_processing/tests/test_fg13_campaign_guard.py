"""
FG13 campaign ownership guard — properties and mutation anchors.
Spec: .kiro/specs/2026-09-15-cms-cs-campaign-ownership/
Fix Group 13 (FG13.1–FG13.4).  Mutation records in decisions.md.

All ownership probes go through handler() — the real dispatcher — not by
calling route functions directly.  Calling functions directly is what allowed
the original bypass to survive a green suite (FG13.3).

Properties covered (eight, matching the spec task's Verify list):
  P1. Each malformed claim shape (absent, "", "   ", ",,,") is refused on
      DELETE /campaigns and PUT /campaigns.
  P2. A multi-fleet caller ('A,B') may act on fleet:A AND fleet:B rows,
      and not on fleet:C.
  P3. platform-admin and connected-services remain unrestricted.
  P4. No fleet-<id> Cognito group grants fleet identity.
  P5. unassign_campaign (DELETE /campaigns/assign) refuses a fleet caller
      on an owner=="oem" row.
  P6. delete_campaign returns 404 and deletes NOTHING when get_item finds
      no row.
  P7. A refused mixed batch performs ZERO delete_item calls
      (validate-all-then-delete).
  P8. A two-page scan guards rows on page 2 using a real LastEvaluatedKey
      continuation.

Mutation targets (six) are listed in each test's docstring and recorded in
decisions.md.
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

def _base_claims(groups, fleet_ids=None, sub='test-sub'):
    claims = {'cognito:groups': ','.join(groups), 'sub': sub}
    if fleet_ids is not None:
        claims['custom:fleetIds'] = fleet_ids
    return claims


def _event(method, path, groups, body=None, query=None, fleet_ids=None, sub='test-sub'):
    """API-GW-shaped event; fleet_ids is omitted entirely if None."""
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
    """fleet-operator event with custom:fleetIds set to fleet_ids string."""
    return _event(method, path, groups=['fleet-operator'],
                  fleet_ids=fleet_ids, body=body, query=query, sub=sub)


def _admin(method, path, body=None, query=None):
    return _event(method, path, groups=['platform-admin'],
                  body=body, query=query)


def _cs(method, path, body=None, query=None):
    return _event(method, path, groups=['connected-services'],
                  body=body, query=query)


# ---------------------------------------------------------------------------
# Base mock setup
# ---------------------------------------------------------------------------

class _Base(unittest.TestCase):
    def setUp(self):
        self.mock_table = MagicMock()
        # Safe defaults — tests override as needed.
        self.mock_table.scan.return_value = {'Items': []}
        self.mock_table.query.return_value = {'Items': []}
        self.mock_table.get_item.return_value = {}
        self.mock_table.put_item.return_value = {}
        self.mock_table.update_item.return_value = {}
        self.mock_table.delete_item.return_value = {}
        # meta.client.exceptions needed by update_campaign's ConditionCheckFailed path
        self.mock_table.meta = MagicMock()
        self.mock_table.meta.client.exceptions.ConditionalCheckFailedException = type(
            'ConditionalCheckFailedException', (Exception,), {}
        )
        self.patcher = patch.object(api, 'campaigns_table', self.mock_table)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def _delete_item_keys(self):
        return [c[1]['Key']['campaignId']
                for c in self.mock_table.delete_item.call_args_list]

    def _delete_item_count(self):
        return self.mock_table.delete_item.call_count


# ===========================================================================
# P1 — Malformed claim shapes are refused on DELETE and PUT
# ===========================================================================

class TestMalformedFleetIdRefused(_Base):
    """P1: fleet-operator callers with a malformed custom:fleetIds are refused.

    Malformed means absent, "", "   " (whitespace-only), or ",,," (only
    delimiters).  The guard must raise before any DynamoDB call is made.

    MUTATION TARGET (a): make _caller_fleet_id return None (unrestricted)
    instead of raising for a malformed claim → every subtest in this class
    MUST FAIL (403 expected; 200 or another non-403 received).
    Record in decisions.md under mutation (a).
    """

    _MALFORMED_SHAPES = [
        ('absent',         None),           # custom:fleetIds key omitted entirely
        ('empty_string',   ''),             # custom:fleetIds = ""
        ('whitespace',     '   '),          # custom:fleetIds = "   "
        ('commas_only',    ',,,'),          # custom:fleetIds = ",,,"
    ]

    def _fleet_op_malformed(self, method, path, fleet_ids_value, body=None, query=None):
        """fleet-operator event where fleet_ids_value is the raw claims value.

        fleet_ids_value=None omits the key entirely; any other value (including
        '') sets it explicitly.
        """
        return _event(method, path, groups=['fleet-operator'],
                      fleet_ids=fleet_ids_value, body=body, query=query)

    def test_malformed_claim_refused_on_delete(self):
        """Each malformed shape returns 403 on DELETE /campaigns.

        MUTATION TARGET (a): return None instead of raising → FAIL.
        """
        # Provide a row so the guard has something to read.  The 403 must come
        # from fleet identity resolution, not from a missing row.
        self.mock_table.get_item.return_value = {
            'Item': {
                'campaignId': 'owned-by-oem',
                'owner': 'oem',
            }
        }
        for label, fleet_ids_val in self._MALFORMED_SHAPES:
            with self.subTest(shape=label):
                ev = self._fleet_op_malformed(
                    'DELETE', '/campaigns',
                    fleet_ids_value=fleet_ids_val,
                    query={'campaignId': 'owned-by-oem'},
                )
                resp = api.handler(ev, {})
                self.assertEqual(
                    resp['statusCode'], 403,
                    f"Malformed claim shape '{label}' must be refused (403); "
                    f"got {resp['statusCode']}. "
                    "MUTATION (a): returning None (unrestricted) instead of "
                    "raising should flip this to 200.",
                )
                self.mock_table.reset_mock()

    def test_malformed_claim_refused_on_put(self):
        """Each malformed shape returns 403 on PUT /campaigns.

        MUTATION TARGET (a): return None instead of raising → FAIL.
        """
        self.mock_table.get_item.return_value = {
            'Item': {
                'campaignId': 'owned-by-oem',
                'owner': 'oem',
            }
        }
        for label, fleet_ids_val in self._MALFORMED_SHAPES:
            with self.subTest(shape=label):
                ev = self._fleet_op_malformed(
                    'PUT', '/campaigns',
                    fleet_ids_value=fleet_ids_val,
                    body={'campaignId': 'owned-by-oem', 'status': 'ACTIVE'},
                )
                resp = api.handler(ev, {})
                self.assertEqual(
                    resp['statusCode'], 403,
                    f"Malformed claim shape '{label}' must be refused on PUT "
                    f"(403); got {resp['statusCode']}. "
                    "MUTATION (a): returning None instead of raising → FAIL.",
                )
                self.mock_table.reset_mock()


# ===========================================================================
# P1 (truthiness trap) — empty frozenset must not bypass the check
# ===========================================================================

class TestEmptyFleetSetRefused(_Base):
    """P1b: an empty fleet-id set must not be treated as unrestricted.

    The truthiness trap: if the guard writes `if not unrestricted and fleet_ids`
    an empty frozenset (falsy) silently disables the check, granting the caller
    access to every row regardless of ownership.  _caller_fleet_id avoids this
    by raising for an empty set rather than returning it.

    This test drives the same malformed-claim path but probes the specific
    truthiness shortcut.

    MUTATION TARGET (b): treat an empty fleet-id set as unrestricted
    (replace `raise _Unauthorized(...)` with `return _CALLER_FLEET_UNRESTRICTED`)
    → this test MUST FAIL (gets 200 on an OEM-owned row when it should be 403).
    Record in decisions.md under mutation (b).
    """

    def test_empty_fleet_set_not_unrestricted_on_delete(self):
        """fleet-operator with only commas must not get unrestricted access.

        MUTATION TARGET (b): treat empty set as unrestricted → FAIL.
        """
        self.mock_table.get_item.return_value = {
            'Item': {'campaignId': 'c', 'owner': 'oem'}
        }
        ev = _event('DELETE', '/campaigns', groups=['fleet-operator'],
                    fleet_ids=',,,', query={'campaignId': 'c'})
        resp = api.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 403,
            "Comma-only fleet claim must refuse, not grant unrestricted access. "
            "MUTATION (b): empty set treated as unrestricted → FAIL.",
        )

    def test_empty_fleet_set_not_unrestricted_on_put(self):
        """fleet-operator with empty string must not get unrestricted access.

        MUTATION TARGET (b): treat empty set as unrestricted → FAIL.
        """
        self.mock_table.get_item.return_value = {
            'Item': {'campaignId': 'c', 'owner': 'oem'}
        }
        ev = _event('PUT', '/campaigns', groups=['fleet-operator'],
                    fleet_ids='', body={'campaignId': 'c', 'status': 'ACTIVE'})
        resp = api.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 403,
            "Empty fleet_ids must refuse, not grant unrestricted access. "
            "MUTATION (b): empty set treated as unrestricted → FAIL.",
        )


# ===========================================================================
# P2 — Multi-fleet caller may act on fleet:A AND fleet:B, not fleet:C
# ===========================================================================

class TestMultiFleetMembership(_Base):
    """P2: multi-fleet callers use membership, not equality.

    A caller with custom:fleetIds='fleet-A,fleet-B' must be allowed to act
    on rows owned by fleet:A and fleet:B, and refused on fleet:C.

    MUTATION TARGET (c): change the membership test to equality against the
    first fleet id only (`owner == f'fleet:{sorted(fleet_ids)[0]}'`) → the
    fleet:B case MUST FAIL.
    Record in decisions.md under mutation (c).
    """

    _CALLER_FLEET_IDS = 'fleet-A,fleet-B'

    def _delete_ev(self, campaign_id, owner):
        self.mock_table.get_item.return_value = {
            'Item': {'campaignId': campaign_id, 'owner': owner}
        }
        return _fleet_op(
            'DELETE', '/campaigns', self._CALLER_FLEET_IDS,
            query={'campaignId': campaign_id},
        )

    def test_multi_fleet_caller_allowed_on_fleet_a_row(self):
        """fleet-A,B caller may delete a fleet:A row (200).

        MUTATION TARGET (c): equality against first id only → still 200 if
        'fleet-A' is the first id, so test may not catch the mutation.
        The fleet:B test is the one that fails under mutation (c).
        """
        ev = self._delete_ev('camp-a', 'fleet:fleet-A')
        resp = api.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 200,
            "Multi-fleet caller must be allowed to delete a fleet:A row.",
        )

    def test_multi_fleet_caller_allowed_on_fleet_b_row(self):
        """fleet-A,B caller may delete a fleet:B row (200).

        MUTATION TARGET (c): equality against first id → fleet:B is not first
        → this test MUST FAIL (gets 403 instead of 200).
        Record: MUTATION (c) catches this test.
        """
        ev = self._delete_ev('camp-b', 'fleet:fleet-B')
        resp = api.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 200,
            "Multi-fleet caller must be allowed to delete a fleet:B row. "
            "MUTATION (c): equality against first id → fleet:B case fails.",
        )

    def test_multi_fleet_caller_refused_on_fleet_c_row(self):
        """fleet-A,B caller must be refused on a fleet:C row (403)."""
        ev = self._delete_ev('camp-c', 'fleet:fleet-C')
        resp = api.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 403,
            "Multi-fleet caller must be refused on a fleet:C row.",
        )


# ===========================================================================
# P3 — platform-admin and connected-services are unrestricted
# ===========================================================================

class TestAdminAndCSUnrestricted(_Base):
    """P3: admin and CS callers bypass ownership restriction.

    They may act on OEM-owned, platform-owned, fleet-owned, and absent-owner
    rows without receiving 403.

    Both DELETE and PUT are exercised so any per-method divergence is caught.
    """

    _OEM_ROW = {'campaignId': 'c', 'owner': 'oem'}
    _FLEET_ROW = {'campaignId': 'c', 'owner': 'fleet:some-fleet'}
    _PLATFORM_ROW = {'campaignId': 'c', 'owner': 'platform'}
    _NO_OWNER_ROW = {'campaignId': 'c'}  # legacy, no owner key

    def _assert_admitted(self, event, label):
        resp = api.handler(event, {})
        self.assertNotEqual(
            resp['statusCode'], 403,
            f"{label}: admin/CS caller must not be refused; got 403.",
        )

    def test_platform_admin_unrestricted_on_oem_row(self):
        self.mock_table.get_item.return_value = {'Item': self._OEM_ROW}
        ev = _admin('DELETE', '/campaigns', query={'campaignId': 'c'})
        self._assert_admitted(ev, 'platform-admin on oem row')

    def test_platform_admin_unrestricted_on_fleet_row(self):
        self.mock_table.get_item.return_value = {'Item': self._FLEET_ROW}
        ev = _admin('DELETE', '/campaigns', query={'campaignId': 'c'})
        self._assert_admitted(ev, 'platform-admin on fleet row')

    def test_platform_admin_unrestricted_on_platform_row(self):
        self.mock_table.get_item.return_value = {'Item': self._PLATFORM_ROW}
        ev = _admin('DELETE', '/campaigns', query={'campaignId': 'c'})
        self._assert_admitted(ev, 'platform-admin on platform row')

    def test_platform_admin_put_unrestricted_on_oem_row(self):
        self.mock_table.get_item.return_value = {'Item': self._OEM_ROW}
        ev = _admin('PUT', '/campaigns', body={'campaignId': 'c', 'status': 'ACTIVE'})
        self._assert_admitted(ev, 'platform-admin PUT on oem row')

    def test_connected_services_unrestricted_on_oem_row(self):
        self.mock_table.get_item.return_value = {'Item': self._OEM_ROW}
        ev = _cs('DELETE', '/campaigns', query={'campaignId': 'c'})
        self._assert_admitted(ev, 'connected-services on oem row')

    def test_connected_services_unrestricted_on_fleet_row(self):
        self.mock_table.get_item.return_value = {'Item': self._FLEET_ROW}
        ev = _cs('DELETE', '/campaigns', query={'campaignId': 'c'})
        self._assert_admitted(ev, 'connected-services on fleet row')

    def test_connected_services_put_unrestricted_on_oem_row(self):
        self.mock_table.get_item.return_value = {'Item': self._OEM_ROW}
        ev = _cs('PUT', '/campaigns', body={'campaignId': 'c', 'status': 'ACTIVE'})
        self._assert_admitted(ev, 'connected-services PUT on oem row')


# ===========================================================================
# P4 — No fleet-<id> Cognito group grants fleet identity (removed pattern)
# ===========================================================================

class TestFleetGroupPatternRemoved(_Base):
    """P4: fleet-<id> as a Cognito group is no longer a valid fleet identity.

    Pattern 2 ('fleet-<id>' Cognito group) was removed; the group is in
    neither _CAMPAIGN_WRITE_GROUPS nor _CAMPAIGN_READ_GROUPS.  The dispatcher
    must refuse it before any route handler is entered — it is NOT a way to
    reach delete_campaign or update_campaign.

    A caller carrying only 'fleet-alpha' as a group must get 403 on every
    write route (refused at the authz gate, before ownership logic).

    NOTE: this test drives through handler() so the dispatcher fires.  A test
    calling delete_campaign() directly would bypass the dispatcher and could
    create a false positive.
    """

    def test_fleet_group_refused_on_delete(self):
        """fleet-alpha group (not fleet-operator) is refused by the dispatcher.

        The 403 must come from the authz gate (no DynamoDB calls), not from
        the ownership guard (which only fires after the gate admits the caller).
        """
        ev = _event('DELETE', '/campaigns', groups=['fleet-alpha'],
                    query={'campaignId': 'c'})
        resp = api.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 403,
            "fleet-alpha group is not in _CAMPAIGN_WRITE_GROUPS; "
            "dispatcher must refuse before reaching ownership logic.",
        )
        self.mock_table.delete_item.assert_not_called()
        self.mock_table.get_item.assert_not_called()

    def test_fleet_group_refused_on_put(self):
        ev = _event('PUT', '/campaigns', groups=['fleet-alpha'],
                    body={'campaignId': 'c', 'status': 'ACTIVE'})
        resp = api.handler(ev, {})
        self.assertEqual(resp['statusCode'], 403)
        self.mock_table.update_item.assert_not_called()
        self.mock_table.get_item.assert_not_called()

    def test_fleet_group_combined_with_fleet_operator_still_needs_fleet_ids(self):
        """fleet-<id> alongside fleet-operator does not supply fleet identity.

        fleet-operator is in _CAMPAIGN_WRITE_GROUPS so the authz gate admits
        the caller.  But _caller_fleet_id resolves identity from
        custom:fleetIds, not from any 'fleet-<id>' group.  Without
        custom:fleetIds the identity is unresolvable → 403 from ownership.
        """
        self.mock_table.get_item.return_value = {
            'Item': {'campaignId': 'c', 'owner': 'oem'}
        }
        ev = _event('DELETE', '/campaigns',
                    groups=['fleet-operator', 'fleet-alpha'],
                    # custom:fleetIds intentionally absent
                    query={'campaignId': 'c'})
        resp = api.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 403,
            "fleet-<id> group must not supply fleet identity; "
            "caller without custom:fleetIds must be refused.",
        )


# ===========================================================================
# P5 — unassign_campaign refuses a fleet caller on an owner=="oem" row
# ===========================================================================

class TestUnassignOwnershipGuard(_Base):
    """P5: unassign_campaign applies the ownership guard per VIN.

    A fleet caller must not be able to unassign an 'oem'-owned assignment row.
    The guard reads each VIN's row and checks ownership before deleting.

    MUTATION TARGET (d): remove the ownership check from unassign_campaign
    → this test MUST FAIL (gets 200 instead of 403, and delete_item is called).
    Record in decisions.md under mutation (d).
    """

    def test_fleet_caller_refused_on_oem_unassign(self):
        """fleet:A unassigning an oem-owned VIN row must return 403.

        MUTATION TARGET (d): remove the _check_campaign_ownership call from
        unassign_campaign → FAIL (200 returned, delete_item called).
        """
        self.mock_table.get_item.return_value = {
            'Item': {
                'campaignId': 'oem-campaign-VIN001',
                'campaignName': 'oem-campaign',
                'owner': 'oem',
                'targetArn': 'vehicle:VIN001',
            }
        }
        ev = _fleet_op(
            'DELETE', '/campaigns/assign', 'fleet-alpha',
            body={'campaignName': 'oem-campaign', 'vehicles': ['VIN001']},
        )
        resp = api.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 403,
            "fleet caller must not be able to unassign an oem-owned row. "
            "MUTATION (d): removing the guard → 200, FAIL.",
        )
        self.mock_table.delete_item.assert_not_called()

    def test_fleet_caller_allowed_to_unassign_own_vin(self):
        """fleet:A may unassign a VIN row it owns (fleet:fleet-alpha)."""
        self.mock_table.get_item.return_value = {
            'Item': {
                'campaignId': 'fleet-campaign-VIN001',
                'campaignName': 'fleet-campaign',
                'owner': 'fleet:fleet-alpha',
                'targetArn': 'vehicle:VIN001',
            }
        }
        ev = _fleet_op(
            'DELETE', '/campaigns/assign', 'fleet-alpha',
            body={'campaignName': 'fleet-campaign', 'vehicles': ['VIN001']},
        )
        resp = api.handler(ev, {})
        self.assertEqual(resp['statusCode'], 200,
                         "fleet caller must be allowed to unassign its own VIN.")
        self.mock_table.delete_item.assert_called_once()

    def test_platform_admin_may_unassign_oem_row(self):
        """platform-admin must be allowed to unassign any row (unrestricted)."""
        self.mock_table.get_item.return_value = {
            'Item': {
                'campaignId': 'oem-c-VIN001',
                'campaignName': 'oem-c',
                'owner': 'oem',
                'targetArn': 'vehicle:VIN001',
            }
        }
        ev = _admin(
            'DELETE', '/campaigns/assign',
            body={'campaignName': 'oem-c', 'vehicles': ['VIN001']},
        )
        resp = api.handler(ev, {})
        self.assertNotEqual(resp['statusCode'], 403,
                            "platform-admin must not be refused on unassign.")


# ===========================================================================
# P6 — delete_campaign returns 404 and deletes NOTHING when no row found
# ===========================================================================

class TestDeleteCampaignMissingRow(_Base):
    """P6: delete_campaign fails closed on a missing template row.

    When get_item returns no Item, the call must return 404 and make zero
    delete_item calls.  A missing row cannot have its ownership verified, so
    the only sound choice is to refuse.

    MUTATION TARGET (a, partial): restore the old `if template_row is not None`
    skip that called delete_item anyway → this test MUST FAIL.
    This is the same mutation (a) at a different site; see decisions.md.
    """

    def test_missing_row_returns_404_no_delete(self):
        """get_item returns no Item → 404, zero delete_item calls.

        MUTATION TARGET: skip the None guard and call delete_item anyway →
        FAIL (deletes a row it cannot verify exists, and returns 200 not 404).
        """
        # get_item returns {} — .get('Item') → None
        self.mock_table.get_item.return_value = {}
        ev = _fleet_op(
            'DELETE', '/campaigns', 'fleet-alpha',
            query={'campaignId': 'nonexistent-campaign'},
        )
        resp = api.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 404,
            "A missing template row must return 404, not attempt a delete. "
            "MUTATION: skip None guard → FAIL (200 + delete called).",
        )
        self.mock_table.delete_item.assert_not_called()

    def test_missing_row_404_for_admin_too(self):
        """Even platform-admin should 404 when the row does not exist."""
        self.mock_table.get_item.return_value = {}
        ev = _admin('DELETE', '/campaigns', query={'campaignId': 'ghost'})
        resp = api.handler(ev, {})
        self.assertEqual(resp['statusCode'], 404)
        self.mock_table.delete_item.assert_not_called()


# ===========================================================================
# P7 — A refused mixed batch performs ZERO delete_item calls
# ===========================================================================

class TestRefusedBatchZeroDeletes(_Base):
    """P7: validate-all-then-delete — a refused batch deletes nothing.

    The prior implementation deleted rows before checking ownership of later
    ones (delete-then-check order).  The correct implementation collects all
    assignment rows first, validates every row's ownership, and only then
    deletes.  A single unauthorized row refuses the whole operation with zero
    deletes.

    This is distinct from TestMixedBatchDeleteRefusedPerRow
    (in test_campaign_ownership.py) which verifies the 403 status.  This test
    checks the ZERO delete_item invariant through handler().

    MUTATION TARGET (e): restore delete-before-validate ordering → this test
    MUST FAIL (delete_item IS called for the rows processed before the
    unauthorized row is reached).
    Record in decisions.md under mutation (e).
    """

    def test_refused_mixed_batch_zero_deletes(self):
        """A batch containing an OEM-owned assignment returns 403 + 0 deletes.

        Template row: fleet:fleet-alpha (caller may own it).
        Scan returns two assignment rows: first fleet:fleet-alpha (ok),
        second oem (unauthorized).

        Validate-all-then-delete: no delete_item call is made before the
        validation pass completes.  The OEM row fails validation → 403 + 0 deletes.

        MUTATION TARGET (e): delete rows during iteration (before validation pass
        completes) → delete_item IS called for at least the first row, FAIL.
        """
        self.mock_table.get_item.return_value = {
            'Item': {
                'campaignId': 'fleet-campaign',
                'owner': 'fleet:fleet-alpha',
            }
        }
        self.mock_table.scan.return_value = {
            'Items': [
                {'campaignId': 'fleet-campaign-VIN001', 'owner': 'fleet:fleet-alpha'},
                {'campaignId': 'fleet-campaign-VIN002', 'owner': 'oem'},  # triggers refusal
            ]
        }
        ev = _fleet_op(
            'DELETE', '/campaigns', 'fleet-alpha',
            query={'campaignId': 'fleet-campaign'},
        )
        resp = api.handler(ev, {})
        self.assertEqual(
            resp['statusCode'], 403,
            "Mixed batch must return 403 on the OEM-owned assignment.",
        )
        self.assertEqual(
            self._delete_item_count(), 0,
            "A refused batch must perform ZERO delete_item calls. "
            "MUTATION (e): delete-before-validate → delete_item IS called, FAIL.",
        )


# ===========================================================================
# P8 — Two-page scan guards rows on page 2
# ===========================================================================

class TestTwoPageScanGuardsPage2(_Base):
    """P8: the scan loop follows LastEvaluatedKey and guards page-2 rows.

    A truncated scan (no pagination) would only examine page 1.  If an
    unauthorized row appears on page 2, an unpaginated implementation would
    silently approve the whole batch.

    The LastEvaluatedKey continuation must be a dict (a real DynamoDB
    pagination key shape), not a mock attribute.  Using a mock attribute would
    let the scan loop treat any object as truthy and loop forever; using a real
    dict proves the 'while True' loop actually processes both pages.

    MUTATION TARGET (f): truncate the scan to one page (never pass
    ExclusiveStartKey to the second scan call) → this test MUST FAIL
    (page-2 OEM row is never checked → 200 instead of 403, and delete_item
    may be called).
    Record in decisions.md under mutation (f).
    """

    def test_page_2_oem_row_causes_403_zero_deletes(self):
        """Page 2 contains an OEM-owned row; the batch must be refused.

        Template row (page 0): owned by fleet-alpha.
        Scan page 1: two fleet-alpha rows + a LastEvaluatedKey.
        Scan page 2: one OEM-owned row; no further LastEvaluatedKey.

        MUTATION TARGET (f): stop after page 1 → page-2 OEM row is never
        seen → batch approved → 200 + delete_item called → FAIL.
        """
        # Template row — ownership check on this passes.
        self.mock_table.get_item.return_value = {
            'Item': {
                'campaignId': 'fleet-campaign',
                'owner': 'fleet:fleet-alpha',
            }
        }

        # Real LastEvaluatedKey — a dict with the primary-key schema.
        # Using a plain dict proves the loop condition `if not last_key: break`
        # evaluates a truthy mapping, not a mock attribute.
        page1_last_key = {'campaignId': 'fleet-campaign-VIN002'}

        # Side effect: first scan call returns two fleet rows + a continuation;
        # second scan call returns one OEM row with no continuation.
        page1_response = {
            'Items': [
                {'campaignId': 'fleet-campaign-VIN001', 'owner': 'fleet:fleet-alpha'},
                {'campaignId': 'fleet-campaign-VIN002', 'owner': 'fleet:fleet-alpha'},
            ],
            'LastEvaluatedKey': page1_last_key,
        }
        page2_response = {
            'Items': [
                {'campaignId': 'fleet-campaign-VIN003', 'owner': 'oem'},  # page 2, OEM
            ],
            # No LastEvaluatedKey → loop terminates after this page.
        }
        self.mock_table.scan.side_effect = [page1_response, page2_response]

        ev = _fleet_op(
            'DELETE', '/campaigns', 'fleet-alpha',
            query={'campaignId': 'fleet-campaign'},
        )
        resp = api.handler(ev, {})

        # The second scan call must have been made with the continuation key.
        self.assertEqual(
            self.mock_table.scan.call_count, 2,
            "scan must be called twice — once per page. "
            "MUTATION (f): stopping after page 1 → only 1 scan call.",
        )
        second_scan_kwargs = self.mock_table.scan.call_args_list[1][1]
        self.assertEqual(
            second_scan_kwargs.get('ExclusiveStartKey'), page1_last_key,
            "Second scan call must pass the LastEvaluatedKey from page 1. "
            "MUTATION (f): omitting ExclusiveStartKey → page 2 never fetched.",
        )

        self.assertEqual(
            resp['statusCode'], 403,
            "Page-2 OEM row must trigger 403. "
            "MUTATION (f): truncate to one page → 200, FAIL.",
        )
        self.assertEqual(
            self._delete_item_count(), 0,
            "No delete_item calls must occur when the batch is refused.",
        )


if __name__ == '__main__':
    unittest.main()
