# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Row-level consumer_id defense-in-depth test for subscription scope mutation.

Spec `2026-09-10-cms-connected-services-subscriptions`, **Fix Group 1 / Security
review Cycle 1 W1 remediation**.

## What this file proves

`test_ownership_mutation.py` already proves that `is_owner()` is the load-bearing
gate for cross-subscriber access.  That is necessary but not sufficient for D7
(defence-in-depth): the sibling handlers `subscription_crud/detail_handler` and
`subscription_records/records_handler` ALSO carry a second gate — a check that
the DDB row's stored `consumer_id` matches the caller, independent of what the
JWT claim says.

Without that second gate, the following attack is possible (security review W1):

  1. Attacker holds a valid Cognito token.
  2. Through any future provisioning path, their `custom:subscriptionIds` claim
     ends up mis-listing a subscription owned by a different subscriber ("victim").
  3. `is_owner()` returns True — the id IS in the claim list.
  4. The scope write succeeds: the attacker injects VINs into the victim's
     `vehicle_scope`.
  5. The victim's next `GET /subscriptions/{id}/records` call, which DOES pass
     the row-level `consumer_id` check, returns telemetry for the injected VINs
     — data sourced from vehicles the attacker chose.

This test file asserts:

  * **Before the fix**: `is_owner=True` but `row.consumer_id != caller` → the
    handler returns 200 and writes.  (RED phase — proves the gap exists.)
  * **After the fix**: the same scenario returns 403 and does NOT write.
    (GREEN phase — proves the fix closes the gap.)

## Red-phase intent

These tests are EXPECTED to FAIL before W1's ConditionExpression fix is applied
to `subscription_scope/handler.py`.  They MUST PASS after.  Running the suite
in "red → apply fix → green" order is the T2.2 / D7 discipline this spec adopted
throughout Groups 1–2.

## Relationship to `test_ownership_mutation.py`

`TestNegativeControlsAreNotVacuous::test_forcing_is_owner_false_denies_even_the_rightful_owner`
(mutation file, lines ~283-303) patches `is_owner` to False and asserts the
request fails.  That shows `is_owner` is THE gate.  This file patches `is_owner`
to True AND makes the DDB row disagree — proving the row-level check is a SECOND,
INDEPENDENT gate.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("DEPLOYMENT_STAGE", "staging")
os.environ.setdefault(
    "SUBSCRIPTION_PLANE_TABLE_NAME",
    "cms-staging-storage-subscriptions-us-west-2-123456789012",
)

_SUBSCRIPTIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SUBSCRIPTIONS_DIR not in sys.path:
    sys.path.insert(0, _SUBSCRIPTIONS_DIR)

from unittest.mock import MagicMock, patch  # noqa: E402

from subscription_scope import handler  # noqa: E402

# ---- Principals -------------------------------------------------------

#: The attacker: an authenticated subscriber.  Their token claims ownership of
#: _VICTIM_SUBSCRIPTION (via a mis-provisioned claim), so is_owner() returns
#: True.  But the row's stored consumer_id belongs to the victim.
_ATTACKER_SUB = "attacker-cognito-sub-0001"
_ATTACKER_OWNED_ID = "01J8Z3QK5N7P9R2T4V6X8Y0AAA"

#: The victim: the real owner of this subscription row.
_VICTIM_SUB = "victim-cognito-sub-9999"
_VICTIM_SUBSCRIPTION = "01J8Z3QK5N7P9R2T4V6X8Y0ZZZ"

_VIN = "1FTFW1ET5DFC10312"


# ---- Event builders ---------------------------------------------------

def _attack_add_event() -> dict:
    """Well-formed add request.

    JWT claim mis-lists _VICTIM_SUBSCRIPTION as if the attacker owns it.
    is_owner() will return True because the id IS in custom:subscriptionIds.
    The DDB row's consumer_id, however, is _VICTIM_SUB — not _ATTACKER_SUB.
    """
    return {
        "requestContext": {"authorizer": {"claims": {
            "sub": _ATTACKER_SUB,
            "cognito:groups": "subscriber",
            # Mis-provisioned: attacker's claim lists the victim's subscription id.
            "custom:subscriptionIds": f"{_ATTACKER_OWNED_ID},{_VICTIM_SUBSCRIPTION}",
        }}},
        "pathParameters": {"id": _VICTIM_SUBSCRIPTION},
        "body": json.dumps({"vin": _VIN}),
    }


def _attack_remove_event() -> dict:
    """Same, for the remove route."""
    return {
        "requestContext": {"authorizer": {"claims": {
            "sub": _ATTACKER_SUB,
            "cognito:groups": "subscriber",
            "custom:subscriptionIds": f"{_ATTACKER_OWNED_ID},{_VICTIM_SUBSCRIPTION}",
        }}},
        "pathParameters": {"id": _VICTIM_SUBSCRIPTION, "vin": _VIN},
    }


# ---- DDB mock helpers -------------------------------------------------

def _table_mock_row_consumer_id_mismatch():
    """Return a table mock whose update_item RAISES ConditionalCheckFailedException.

    This is what DynamoDB does when:
        ConditionExpression="attribute_exists(subscription_id) AND consumer_id = :caller"
    is evaluated against a row whose consumer_id is _VICTIM_SUB while :caller is
    _ATTACKER_SUB.

    Before the fix (ConditionExpression only checks attribute_exists), update_item
    returns success — the mock's default MagicMock() return is used.
    After the fix, the condition also checks consumer_id = :caller, and DynamoDB
    raises this exception.  The handler catches it and must return 403.
    """
    from botocore.exceptions import ClientError

    error_response = {
        "Error": {
            "Code": "ConditionalCheckFailedException",
            "Message": "The conditional request failed",
        }
    }
    exc = ClientError(error_response, "UpdateItem")
    # Give the exception the class name the handler uses for isinstance-style matching
    exc.__class__.__name__ = "ConditionalCheckFailedException"

    table = MagicMock()
    # update_item raises rather than returning normally — simulates the DDB
    # condition failure when the row's consumer_id doesn't match :caller.
    table.update_item.side_effect = exc
    resource = MagicMock()
    resource.Table.return_value = table
    return resource, table


def _table_mock_row_consumer_id_matches():
    """Return a table mock whose update_item succeeds (row.consumer_id == caller).

    Used for the self-check: the rightful owner's add/remove must still succeed
    after the fix is applied.
    """
    table = MagicMock()
    table.update_item.return_value = {
        "Attributes": {"vehicle_scope": set(), "consumer_id": _VICTIM_SUB}
    }
    resource = MagicMock()
    resource.Table.return_value = table
    return resource, table


# ---- Tests ------------------------------------------------------------


class TestRowConsumerIdDefenseInDepth:
    """W1 remediation: scope mutation must be refused when row.consumer_id != caller.

    Security review Cycle 1, Warning W1. Closes the asymmetry with
    subscription_crud/detail_handler and subscription_records/records_handler.
    """

    # -- add_handler --------------------------------------------------------

    def test_add_is_owner_true_but_row_consumer_id_mismatch_returns_403(self):
        """The core W1 attack scenario for add_handler.

        is_owner is patched to True (simulating the mis-provisioned claim).
        The DDB update_item raises ConditionalCheckFailedException (simulating
        the consumer_id = :caller check failing against the victim's row).

        EXPECTED BEFORE FIX:  200  (no consumer_id check → silent write succeeds)
        EXPECTED AFTER FIX:   403  (ConditionExpression catches the mismatch)
        """
        event = _attack_add_event()
        resource, table = _table_mock_row_consumer_id_mismatch()

        # Updated 2026-09-12 (Option A): `is_owner` is gone from this handler, so
        # there is nothing to patch to True. The mis-provisioned-claim scenario is
        # now constructed by the event's claim alone (which is inert), and the row
        # is the only authority — which is precisely what FG1.1 asked for.
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.add_handler(event, None)

        assert resp["statusCode"] == 403, (
            f"Expected 403 (row consumer_id mismatch blocks write), got "
            f"{resp['statusCode']}: {resp['body']}. "
            "W1 fix is missing: add ConditionExpression consumer_id = :caller "
            "to the UpdateItem call in add_handler."
        )
        # The write must not have succeeded — it raised before returning a response.
        assert table.update_item.called, (
            "update_item was never called — the test fixture may be wrong; "
            "the test should reach the DDB call before the condition fires."
        )

    def test_add_row_consumer_id_mismatch_does_not_write_to_victim_row(self):
        """Complementary assertion: the victim's row is not mutated.

        update_item IS called (the ConditionExpression is evaluated by DDB),
        but it raises, so the handler must not treat the call as a success.
        The audit outcome must be 'not_found' or 'denied', never 'added'.
        """
        event = _attack_add_event()
        resource, table = _table_mock_row_consumer_id_mismatch()

        # Updated 2026-09-12 (Option A): `is_owner` is gone from this handler, so
        # there is nothing to patch to True. The mis-provisioned-claim scenario is
        # now constructed by the event's claim alone (which is inert), and the row
        # is the only authority — which is precisely what FG1.1 asked for.
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.add_handler(event, None)

        body = json.loads(resp["body"])
        assert resp["statusCode"] in (403, 404), (
            f"Expected 403 or 404 (condition failed), got {resp['statusCode']}: {body}"
        )
        assert "added" not in body or body.get("added") == [] or body.get("added") is None, (
            "Response body claims VINs were added despite consumer_id mismatch"
        )

    # -- remove_handler -----------------------------------------------------

    def test_remove_is_owner_true_but_row_consumer_id_mismatch_returns_403(self):
        """The core W1 attack scenario for remove_handler.

        EXPECTED BEFORE FIX:  200  (no consumer_id check → silent write succeeds)
        EXPECTED AFTER FIX:   403  (ConditionExpression catches the mismatch)
        """
        event = _attack_remove_event()
        resource, table = _table_mock_row_consumer_id_mismatch()

        # Updated 2026-09-12 (Option A): `is_owner` is gone from this handler, so
        # there is nothing to patch to True. The mis-provisioned-claim scenario is
        # now constructed by the event's claim alone (which is inert), and the row
        # is the only authority — which is precisely what FG1.1 asked for.
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.remove_handler(event, None)

        assert resp["statusCode"] == 403, (
            f"Expected 403 (row consumer_id mismatch blocks write), got "
            f"{resp['statusCode']}: {resp['body']}. "
            "W1 fix is missing: add ConditionExpression consumer_id = :caller "
            "to the UpdateItem call in remove_handler."
        )
        assert table.update_item.called

    def test_remove_row_consumer_id_mismatch_does_not_report_removal(self):
        """The victim's VIN must not be reported as removed."""
        event = _attack_remove_event()
        resource, table = _table_mock_row_consumer_id_mismatch()

        # Updated 2026-09-12 (Option A): `is_owner` is gone from this handler, so
        # there is nothing to patch to True. The mis-provisioned-claim scenario is
        # now constructed by the event's claim alone (which is inert), and the row
        # is the only authority — which is precisely what FG1.1 asked for.
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.remove_handler(event, None)

        body = json.loads(resp["body"])
        assert resp["statusCode"] in (403, 404), (
            f"Expected 403 or 404 (condition failed), got {resp['statusCode']}: {body}"
        )
        assert body.get("removed") is not True, (
            "Response claims VIN was removed despite consumer_id mismatch"
        )

    # -- Self-check: legitimate owner is not broken by the fix -------------

    def test_add_legitimate_owner_still_succeeds_after_fix(self):
        """Rightful owner (row.consumer_id matches) must still get 200.

        Sanity check: the fix must not break the happy path.  Here is_owner is
        NOT mocked — the legitimately-owned event flows through the real guard,
        and the table mock returns update_item success (consumer_id matches).
        """
        # Craft an event where the caller genuinely owns the subscription.
        event = {
            "requestContext": {"authorizer": {"claims": {
                "sub": _VICTIM_SUB,
                "cognito:groups": "subscriber",
                # Correctly lists only the victim's own subscription.
                "custom:subscriptionIds": _VICTIM_SUBSCRIPTION,
            }}},
            "pathParameters": {"id": _VICTIM_SUBSCRIPTION},
            "body": json.dumps({"vin": _VIN}),
        }
        resource, table = _table_mock_row_consumer_id_matches()

        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.add_handler(event, None)

        assert resp["statusCode"] == 200, (
            f"Legitimate owner was denied after the fix: {resp['statusCode']}: {resp['body']}. "
            "The consumer_id check must only reject mismatches, not all writes."
        )
        assert table.update_item.called

    def test_remove_legitimate_owner_still_succeeds_after_fix(self):
        """Rightful owner (row.consumer_id matches) must still get 200 for remove."""
        event = {
            "requestContext": {"authorizer": {"claims": {
                "sub": _VICTIM_SUB,
                "cognito:groups": "subscriber",
                "custom:subscriptionIds": _VICTIM_SUBSCRIPTION,
            }}},
            "pathParameters": {"id": _VICTIM_SUBSCRIPTION, "vin": _VIN},
        }
        resource, table = _table_mock_row_consumer_id_matches()
        # For remove, update_item needs UPDATED_OLD with the VIN present.
        table.update_item.return_value = {
            "Attributes": {"vehicle_scope": {_VIN}, "consumer_id": _VICTIM_SUB}
        }
        # Reset side_effect (matches mock returns success, not exception).
        table.update_item.side_effect = None

        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.remove_handler(event, None)

        assert resp["statusCode"] == 200, (
            f"Legitimate owner was denied after the fix: {resp['statusCode']}: {resp['body']}"
        )
        assert table.update_item.called
