# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# T1.6 — spec 2026-09-10-cms-dms-sovd-diagnostic-sessions-v1.5
# RED-PHASE test skeletons for the DMS-technician authz predicate.
#
# These tests FAIL today because the technician branch does not exist yet.
# Every failure must name the missing branch, not a framework error.
#
# Implementation lands in T2.2.  Do NOT implement the branch until this file
# is green-gated by T2.2's Verify command:
#   cd ~/connected-mobility-guidance-on-aws/services
#   python3 -m pytest commands/tests/ -q
#
# No expected pass-count is written here on purpose.  commands_lambda.py is shared
# with the in-flight spec 2026-09-02-cms-diagnostics-platform, and the baseline moved
# 65 -> 70 during this spec's Group 1 alone (F16).  Measure the count on the working
# tree immediately before committing and assert post == measured + 7.  A number
# committed here would be wrong by the next concurrent commit.
#
# Ground truth for this file (F-findings from tasks.md Group 1):
#   F1  — GSI name is 'vin-index' (HASH vehicle_vin, ProjectionType ALL, ACTIVE)
#   F2  — Active-status set: {Draft, InProgress, AwaitingParts} (PascalCase)
#          Draft MUST be active: create_repair_order() writes status="Draft"
#   F3  — Claim is custom:dealerIds (comma-separated), NOT custom:dealershipId
#   F7  — Technician hits THREE denials today:
#          (1) _authorize_per_vin :206 "Requires fleet-operator, fleet-viewer, or platform-admin."
#          (2) run_routine role check :396 "run_routine requires fleet-operator…"
#          (3) SERVICE_ONLY :444 (separate F8 decision — left hard-rejected in v1)
#
# Command-type discipline:
#   Tests 1 and 7 ("allowed") use run_routine + attestation to exercise the full
#   admit path through the role check.
#   Tests 2, 3, 4, 5 ("denied") use read_dtcs to isolate each denial to the
#   single property the test names.  Using run_routine for these would make them
#   pass vacuously today (denied at the run_routine role check before the branch
#   is reached) AND would still pass after T2.2 if the specific filter were
#   removed, because the run_routine role check would deny again.  read_dtcs
#   reaches _authorize_per_vin directly and makes each assertion load-bearing.
#   Test 6 (fleet-operator regression) uses read_dtcs for the same reason.
#
# Each assertion checks the PROPERTY named in the test.
# A test named 'wrong_dealer_denied' must fail if the dealer filter is removed —
# verified by the mutation note in each test's docstring.

import importlib
import json
import os
import sys

import boto3
import pytest
from moto import mock_aws

# ── Test environment ──────────────────────────────────────────────────────────
_STAGE = "test"
_REGION = "us-west-2"
_COMMANDS_TABLE = f"cms-{_STAGE}-storage-commands"
_VEHICLES_TABLE = f"cms-{_STAGE}-storage-vehicles"
_ENROLLMENT_TABLE = f"cms-{_STAGE}-fleet-enrollment"
# env var T2.2 reads; must be set before commands_lambda is imported
_RO_TABLE = f"dms-{_STAGE}-repair-orders"

os.environ.setdefault("AWS_DEFAULT_REGION", _REGION)
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ["DEPLOYMENT_STAGE"] = _STAGE
os.environ["COMMANDS_TABLE"] = _COMMANDS_TABLE
os.environ["FLEET_ENROLLMENT_TABLE_NAME"] = _ENROLLMENT_TABLE
# T2.2 must read DMS_REPAIR_ORDERS_TABLE (never hardcode the table name).
os.environ["DMS_REPAIR_ORDERS_TABLE"] = _RO_TABLE


# ── DDB bootstrap helpers ─────────────────────────────────────────────────────

def _bootstrap_tables():
    """Create all DDB tables required by these tests; idempotent."""
    ddb = boto3.client("dynamodb", region_name=_REGION)
    existing = set(ddb.list_tables().get("TableNames", []))

    if _COMMANDS_TABLE not in existing:
        ddb.create_table(
            TableName=_COMMANDS_TABLE,
            KeySchema=[{"AttributeName": "commandId", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "commandId", "AttributeType": "S"},
                {"AttributeName": "vehicleId", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "N"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "vehicleId-index",
                "KeySchema": [
                    {"AttributeName": "vehicleId", "KeyType": "HASH"},
                    {"AttributeName": "timestamp", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )

    if _VEHICLES_TABLE not in existing:
        ddb.create_table(
            TableName=_VEHICLES_TABLE,
            KeySchema=[{"AttributeName": "vehicleId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "vehicleId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

    if _ENROLLMENT_TABLE not in existing:
        ddb.create_table(
            TableName=_ENROLLMENT_TABLE,
            KeySchema=[
                {"AttributeName": "fleetId", "KeyType": "HASH"},
                {"AttributeName": "vehicleId", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "fleetId", "AttributeType": "S"},
                {"AttributeName": "vehicleId", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "vehicleId-index",
                "KeySchema": [{"AttributeName": "vehicleId", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )

    # DMS repair-orders table — mirrors dms-staging-repair-orders.
    # PK is `ro_id` — verified against BOTH the live table and the IaC declaration,
    # not inferred. An earlier revision of this fixture used `repair_order_id`, which
    # exists nowhere: review Cycle 1 caught it as Critical because a coder reading this
    # fixture as the authoritative schema would have written `_active_ro_exists` against
    # an attribute the table does not have. The fixture IS the schema contract for every
    # test below, so getting it wrong here propagates silently into the implementation.
    # <!-- verify: aws dynamodb describe-table --table-name dms-staging-repair-orders --region us-west-2 --query "Table.KeySchema" -->
    # <!-- verify: grep -n '"pk": ("ro_id"' ~/guidance-for-dealer-management-system-on-aws/deployment/stacks/dms_storage_stack.py -->
    # GSI 'vin-index' (HASH vehicle_vin, ProjectionType ALL) per F1.
    if _RO_TABLE not in existing:
        ddb.create_table(
            TableName=_RO_TABLE,
            KeySchema=[{"AttributeName": "ro_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "ro_id", "AttributeType": "S"},
                {"AttributeName": "vehicle_vin", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "vin-index",  # F1: real index name
                "KeySchema": [{"AttributeName": "vehicle_vin", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )


def _seed_vehicle(vehicle_id: str, vin: str, fuel_type: str = "gasoline"):
    """Insert a vehicle row with VIN.  fuel_type defaults to gasoline so run_routine
    powertrain resolution succeeds when the test exercises the admit path.
    """
    table = boto3.resource("dynamodb", region_name=_REGION).Table(_VEHICLES_TABLE)
    table.put_item(Item={"vehicleId": vehicle_id, "vin": vin, "fuelType": fuel_type})


def _seed_repair_order(ro_id: str, vehicle_vin: str, dealer_id: str, status: str):
    """Insert a repair order into the DMS repair-orders table.

    Args:
        ro_id:       primary key — the attribute is named `ro_id` (live table +
                     dms_storage_stack.py:85). Not `repair_order_id`; that name
                     exists in no schema.
        vehicle_vin: the VIN — indexed by 'vin-index' GSI (F1)
        dealer_id:   the dealer that owns this RO
        status:      PascalCase per F2 (e.g. 'InProgress', 'Draft', 'Closed')
    """
    table = boto3.resource("dynamodb", region_name=_REGION).Table(_RO_TABLE)
    table.put_item(Item={
        "ro_id": ro_id,
        "vehicle_vin": vehicle_vin,
        "dealer_id": dealer_id,
        "status": status,
    })


def _seed_enrollment(fleet_id: str, vehicle_id: str):
    """Insert a fleet-enrollment row mapping vehicleId -> fleetId.

    Despite the parameter name at every historical call site ("vin"), this
    table is keyed on vehicleId, NOT VIN --
    cms-{stage}-fleet-enrollment has no `vin` attribute at all, and
    resolve_vins_to_fleets() queries its vehicleId-index GSI. See
    issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/.
    """
    table = boto3.resource("dynamodb", region_name=_REGION).Table(_ENROLLMENT_TABLE)
    table.put_item(Item={"fleetId": fleet_id, "vehicleId": vehicle_id})


# ── Event / claims helpers ────────────────────────────────────────────────────

def _sovd_event(vehicle_id: str, body: dict, claims: dict) -> dict:
    """Build a POST /api/commands/{vehicleId} event with given claims."""
    return {
        "path": f"/api/commands/{vehicle_id}",
        "httpMethod": "POST",
        "body": json.dumps(body),
        "requestContext": {"authorizer": {"claims": claims}},
    }


def _tech_claims(dealer_ids: str | None = "dealer-denver") -> dict:
    """Cognito claims for a DMS technician.

    custom:dealerIds is the established claim per F3 (NOT custom:dealershipId).
    It is a comma-separated string, matching the shape of custom:fleetIds.
    When dealer_ids is None the key is entirely absent — used by test 5.
    """
    claims = {
        "cognito:groups": "dms-technician",
        "email": "tech@dealer-denver.example.com",
        "custom:fleetIds": "",  # technicians have no fleet assignment
    }
    if dealer_ids is not None:
        claims["custom:dealerIds"] = dealer_ids
    return claims


def _operator_claims(fleet_id: str) -> dict:
    """Fleet-operator claims (existing path — used by regression guard test 6)."""
    return {
        "cognito:groups": "fleet-operator",
        "email": "operator@example.com",
        "custom:fleetIds": fleet_id,
    }


# ── Command body constants ────────────────────────────────────────────────────

# run_routine body with valid attestation — used for ADMIT tests (1, 7) so the
# full path through the role check is exercised.
# attestation.user_email is overwritten server-side (FG2.1); value here is discarded.
_RUN_ROUTINE_BODY = {
    "command_type": "run_routine",
    "routine_id": "o2_heater_check",
    "attestation": {
        "text": "I confirm the vehicle is stationary and safe to run this routine.",
        "user_email": "tech@dealer-denver.example.com",
        "timestamp_ms": 1735000000000,
    },
}

# read_dtcs body — used for DENY tests (2, 3, 4, 5) and the regression guard (6).
#
# Why read_dtcs for denial tests?
#   Using run_routine for denial tests would make them pass vacuously:
#     - Today: technician denied at "Requires fleet-operator/viewer/admin" (before the branch)
#     - After T2.2: technician reaches the branch, but if the specific filter (dealer,
#       status, existence) were removed, the run_routine role check at :396 would
#       deny again — so the test would still pass, and the mutation would go NOT-CAUGHT.
#   read_dtcs bypasses the extra role check, so the denial is driven only by the
#   property the test names (dealer set, status set, RO existence, missing claim).
_READ_DTCS_BODY = {
    "command_type": "read_dtcs",
    "components": ["ECU_ENGINE"],
}


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def commands_lambda():
    """Import commands_lambda under moto with all required tables bootstrapped."""
    with mock_aws():
        _bootstrap_tables()
        sys.modules.pop("commands_lambda", None)
        import commands_lambda as mod
        importlib.reload(mod)
        yield mod


# ── Red-phase tests ───────────────────────────────────────────────────────────
#
# Expected failure contract (pre-T2.2):
#   Tests 1 and 7: fail at 403 "Requires fleet-operator, fleet-viewer, or platform-admin."
#                  (first denial site in _authorize_per_vin — the technician is not
#                   an operator/viewer/admin so it falls into the else branch at :204)
#   Tests 2, 3, 4, 5: also fail at 403 — the technician is denied at the same site.
#                  After T2.2 these will be denied inside the new branch (correct reason).
#   Test 6: currently fails because _seed_vehicle used to omit fuelType; now that
#           fuelType is seeded the fleet-operator path should succeed.

class TestDmsTechnicianAuthz:
    """T1.6 red-phase: DMS technician invoke authz.

    All seven tests FAIL today because the technician branch is absent from
    commands_lambda.py.  When T2.2 ships, all seven must go green.
    """

    VEHICLE_ID = "VEH-TECH-001"
    VIN = "1TECH00000Denver1"
    DEALER_ID = "dealer-denver"
    OTHER_DEALER_ID = "dealer-boston"

    def _setup_vehicle(self):
        # fuelType='gasoline' so run_routine powertrain resolution works for tests 1 & 7
        _seed_vehicle(self.VEHICLE_ID, self.VIN, fuel_type="gasoline")
        # Intentionally NO fleet enrollment — technicians have no custom:fleetIds

    # ------------------------------------------------------------------
    # Test 1: tech + dealer matches RO + status InProgress → ALLOWED
    # ------------------------------------------------------------------
    def test_tech_dealer_matches_ro_status_inprogress_allowed(self, commands_lambda):
        """A technician whose custom:dealerIds includes the RO's dealer_id
        and whose VIN has an InProgress RO must receive 200.

        PROPERTY: technician is admitted when dealer matches AND status is active.
        Mutation that MUST break this: remove the technician branch → technician denied
        at "Requires fleet-operator, fleet-viewer, or platform-admin." → 403, assertion fails.
        """
        from unittest.mock import MagicMock
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()
        _seed_repair_order("RO-001", self.VIN, self.DEALER_ID, "InProgress")

        resp = commands_lambda.handler(
            _sovd_event(self.VEHICLE_ID, _RUN_ROUTINE_BODY, _tech_claims(self.DEALER_ID)),
            None,
        )
        assert resp["statusCode"] == 200, (
            f"EXPECTED 200 — technician with matching dealer + InProgress RO should be admitted. "
            f"Got {resp['statusCode']}: {resp.get('body', '')}. "
            f"Fails because the technician branch is not yet implemented in "
            f"_authorize_per_vin / run_routine role check (F7, T2.2)."
        )
        mock_iot.publish.assert_called_once()

    # ------------------------------------------------------------------
    # Test 2: tech + RO status Closed → DENIED
    # ------------------------------------------------------------------
    def test_tech_ro_status_closed_denied(self, commands_lambda):
        """A technician whose VIN has only a Closed RO must receive 403.

        Closed is a terminal status — the car is no longer in the bay.
        PROPERTY: status membership in the active set is enforced (not just existence).
        Mutation that MUST break this: add 'Closed' to RO_ACTIVE_STATUSES → _active_ro_exists
        returns True → 200 returned, assertion fails.

        Uses read_dtcs so the denial is driven by the RO-status check, not the
        run_routine role check at :396.  See "Command-type discipline" in the module header.

        F2: active set = {Draft, InProgress, AwaitingParts}.  Closed is excluded.
        """
        from unittest.mock import MagicMock
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()
        _seed_repair_order("RO-002", self.VIN, self.DEALER_ID, "Closed")

        resp = commands_lambda.handler(
            _sovd_event(self.VEHICLE_ID, _READ_DTCS_BODY, _tech_claims(self.DEALER_ID)),
            None,
        )
        assert resp["statusCode"] == 403, (
            f"EXPECTED 403 — technician with a Closed RO must be denied. "
            f"Got {resp['statusCode']}: {resp.get('body', '')}. "
            f"Closed is outside the active-status set {{Draft, InProgress, AwaitingParts}} (F2). "
            f"Technician branch with status filter is not yet implemented (T2.2)."
        )
        # The denial reason must name the RO-status predicate, not the generic group check.
        # This assertion fails today because the current 403 says "Requires fleet-operator…"
        # — which is the denial BEFORE the technician branch is reached.
        # After T2.2 the message must say something like "no active repair order" or
        # reference the status, so the caller knows what to fix.
        body = json.loads(resp["body"])
        error_text = body.get("error", "").lower()
        assert "fleet-operator" not in error_text, (
            f"EXPECTED denial reason specific to RO status, not the generic group check. "
            f"Got error: {body.get('error')!r}. "
            f"This means the technician branch has not been entered yet (F7, T2.2)."
        )
        mock_iot.publish.assert_not_called()

    # ------------------------------------------------------------------
    # Test 3: tech + no RO for VIN → DENIED
    # ------------------------------------------------------------------
    def test_tech_no_ro_for_vin_denied(self, commands_lambda):
        """A technician whose VIN has no RO at all must receive 403.

        PROPERTY: presence of an active RO is required; the predicate is not vacuously true.
        Mutation that MUST break this: skip the _active_ro_exists() call → 200 returned,
        assertion fails.

        Uses read_dtcs so the denial is driven by _active_ro_exists() returning False
        (vin-index Count=0), not the run_routine role check.
        """
        from unittest.mock import MagicMock
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()
        # No repair order seeded for this VIN.

        resp = commands_lambda.handler(
            _sovd_event(self.VEHICLE_ID, _READ_DTCS_BODY, _tech_claims(self.DEALER_ID)),
            None,
        )
        assert resp["statusCode"] == 403, (
            f"EXPECTED 403 — technician with no RO for the VIN must be denied. "
            f"Got {resp['statusCode']}: {resp.get('body', '')}. "
            f"_active_ro_exists() must return False when vin-index returns Count=0 (F1, F7). "
            f"Technician branch is not yet implemented (T2.2)."
        )
        # The denial reason must name the RO-existence check, not the generic group check.
        body = json.loads(resp["body"])
        error_text = body.get("error", "").lower()
        assert "fleet-operator" not in error_text, (
            f"EXPECTED denial reason specific to missing RO, not the generic group check. "
            f"Got error: {body.get('error')!r}. "
            f"This means the technician branch has not been entered yet (F7, T2.2)."
        )
        mock_iot.publish.assert_not_called()

    # ------------------------------------------------------------------
    # Test 4: tech + RO exists at a DIFFERENT dealer → DENIED
    # ------------------------------------------------------------------
    def test_tech_wrong_dealer_denied(self, commands_lambda):
        """A technician whose custom:dealerIds does NOT include the RO's dealer_id
        must receive 403, even when a matching-VIN active RO exists.

        PROPERTY: the dealer set intersection is enforced; membership is required.
        Mutation that MUST break this: remove dealer_id from the DDB FilterExpression
        → _active_ro_exists finds the RO by VIN alone → 200 returned, assertion fails.

        Uses read_dtcs so the denial is driven by the dealer check alone.
        F3: claim is custom:dealerIds (comma-separated).
        """
        from unittest.mock import MagicMock
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()
        # RO belongs to OTHER_DEALER_ID; tech's claim lists DEALER_ID only
        _seed_repair_order("RO-004", self.VIN, self.OTHER_DEALER_ID, "InProgress")

        resp = commands_lambda.handler(
            _sovd_event(self.VEHICLE_ID, _READ_DTCS_BODY, _tech_claims(self.DEALER_ID)),
            None,
        )
        assert resp["statusCode"] == 403, (
            f"EXPECTED 403 — technician at '{self.DEALER_ID}' must be denied when the "
            f"only matching RO belongs to '{self.OTHER_DEALER_ID}'. "
            f"Got {resp['statusCode']}: {resp.get('body', '')}. "
            f"Dealer-set filter in _active_ro_exists() is not yet implemented (F3, T2.2)."
        )
        # The denial reason must be specific to the dealer check, not the generic group check.
        body = json.loads(resp["body"])
        error_text = body.get("error", "").lower()
        assert "fleet-operator" not in error_text, (
            f"EXPECTED denial reason specific to dealer mismatch, not the generic group check. "
            f"Got error: {body.get('error')!r}. "
            f"This means the technician branch has not been entered yet (F7, T2.2)."
        )
        mock_iot.publish.assert_not_called()

    # ------------------------------------------------------------------
    # Test 5: tech with custom:dealerIds absent → DENIED, reason names the claim
    # ------------------------------------------------------------------
    def test_tech_missing_dealer_ids_claim_denied_with_named_reason(self, commands_lambda):
        """A technician token with NO custom:dealerIds claim must receive 403,
        and the error body must name the missing claim.

        PROPERTY: fail-closed on absent claim; the error message identifies it by name.
        This mirrors DMS auth.py's fail-closed shape for empty dealerIds (F3).
        Mutation that MUST break this: treat absent claim as 'all dealers' → 200 returned,
        or error body omits 'dealerIds' → string assertion fails.

        Uses read_dtcs for the same reason as tests 2-4.
        """
        from unittest.mock import MagicMock
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()
        _seed_repair_order("RO-005", self.VIN, self.DEALER_ID, "InProgress")

        # dealer_ids=None → key absent from claims dict entirely
        claims = _tech_claims(dealer_ids=None)
        assert "custom:dealerIds" not in claims, (
            "Fixture error: custom:dealerIds must be absent to test this case"
        )

        resp = commands_lambda.handler(
            _sovd_event(self.VEHICLE_ID, _READ_DTCS_BODY, claims),
            None,
        )
        assert resp["statusCode"] == 403, (
            f"EXPECTED 403 — technician with no custom:dealerIds must be denied. "
            f"Got {resp['statusCode']}: {resp.get('body', '')}. "
            f"Fail-closed on absent dealerIds claim is not yet implemented (F3, T2.2)."
        )
        body = json.loads(resp["body"])
        # The error message must name the missing claim so the caller knows why they
        # were denied.  Assert the PLURAL 'dealerids', not 'dealerid' (security review
        # Suggestion 3): the singular substring also matches the invented
        # 'custom:dealershipId' that F3 rejected, so an implementation naming the WRONG
        # claim would have passed the test whose entire purpose is to pin the claim name.
        error_text = body.get("error", "").lower()
        assert "dealerids" in error_text, (
            f"EXPECTED error message to name the missing 'custom:dealerIds' claim (plural). "
            f"Got error: {body.get('error')!r}. "
            f"The named-reason is not yet implemented in the technician branch (F3, T2.2). "
            f"Note: 'custom:dealershipId' does NOT satisfy this — that attribute does not exist."
        )
        mock_iot.publish.assert_not_called()

    # ------------------------------------------------------------------
    # Test 5b/5c: empty and unparseable claim (security review Suggestion 4)
    # ------------------------------------------------------------------
    @pytest.mark.parametrize(
        "claim_value,label",
        [
            ("", "empty string"),
            ("   ", "whitespace only"),
            (",,,", "separators only — parses to zero non-empty ids"),
        ],
    )
    def test_tech_unusable_dealer_ids_claim_denied(self, commands_lambda, claim_value, label):
        """A technician whose custom:dealerIds is present but unusable must be denied.

        PROPERTY: fail-closed on empty/whitespace/separator-only claims, not only on an
        ABSENT one. F3 records that DMS's get_granted_dealer_ids returns the empty set for
        absent, empty AND unparseable values — but test 5 pinned only the absent case, so
        the other two were one keystroke away from a fail-open and nothing would have caught it.

        A present-but-empty claim is the more dangerous shape of the two: code that guards
        with `if 'custom:dealerIds' not in claims` passes the absent test and admits this one.

        Mutation that MUST break this: guard on key presence rather than on parsed content.
        """
        from unittest.mock import MagicMock
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()
        _seed_repair_order("RO-005b", self.VIN, self.DEALER_ID, "InProgress")

        claims = _tech_claims(dealer_ids=claim_value)
        assert claims.get("custom:dealerIds") == claim_value, (
            "Fixture error: the claim must be PRESENT and unusable, not absent — "
            "absence is already covered by test 5"
        )

        resp = commands_lambda.handler(
            _sovd_event(self.VEHICLE_ID, _READ_DTCS_BODY, claims),
            None,
        )
        assert resp["statusCode"] == 403, (
            f"EXPECTED 403 — technician with an unusable custom:dealerIds ({label}) must be "
            f"denied. Got {resp['statusCode']}: {resp.get('body', '')}. "
            f"Zero parsed dealer grants must mean zero dealers, never all dealers (F3, T2.2)."
        )
        # Denied for the RIGHT reason. Without this, the test passes TODAY on the generic
        # pre-branch denial ("Requires fleet-operator...") — the correct status for the wrong
        # reason — and would keep passing against a technician branch that guards on key
        # presence rather than parsed content, which is the exact fail-open this case exists
        # to catch. Same discipline as tests 2-4.
        error_text = json.loads(resp["body"]).get("error", "").lower()
        assert "fleet-operator" not in error_text, (
            f"Denied by the generic pre-branch check, not by the technician branch's "
            f"claim-parsing guard. The technician branch must be reached and must reject an "
            f"unusable claim on its parsed content ({label}). Got: {error_text!r}"
        )
        mock_iot.publish.assert_not_called()

    # ------------------------------------------------------------------
    # Test 5d: 'Ready' is NOT an active status (security review Suggestion 5)
    # ------------------------------------------------------------------
    def test_tech_ro_status_ready_denied(self, commands_lambda):
        """An RO in 'Ready' must NOT admit the technician.

        PROPERTY: 'Ready' is excluded from RO_ACTIVE_STATUSES. It is the nearest EXCLUDED
        neighbour of the active set — work complete, awaiting customer pickup — and therefore
        the member most likely to be added to the active set by a well-meaning edit ("the car
        is still on our lot, why can't the tech scan it?").

        Why this case and not just the Closed one: T6.5's mutation battery catches wholesale
        replacement of the status set and catches DROPPING Draft, but nothing catches ADDING
        Ready. A widening mutation is invisible to a suite that only pins the members it
        already excludes at the far end of the lifecycle.

        Mutation that MUST break this: add 'Ready' to RO_ACTIVE_STATUSES.
        """
        from unittest.mock import MagicMock
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()
        _seed_repair_order("RO-005d", self.VIN, self.DEALER_ID, "Ready")

        resp = commands_lambda.handler(
            _sovd_event(self.VEHICLE_ID, _READ_DTCS_BODY, _tech_claims()),
            None,
        )
        assert resp["statusCode"] == 403, (
            f"EXPECTED 403 — 'Ready' means work complete, so it is NOT in "
            f"RO_ACTIVE_STATUSES {{Draft, InProgress, AwaitingParts}} (F2). "
            f"Got {resp['statusCode']}: {resp.get('body', '')}."
        )
        error_text = json.loads(resp["body"]).get("error", "").lower()
        assert "fleet-operator" not in error_text, (
            f"Denied for the WRONG reason — this is the generic pre-branch denial, not the "
            f"status-filter denial this test exists to pin. Got: {error_text!r}"
        )
        mock_iot.publish.assert_not_called()

    # ------------------------------------------------------------------
    # Test 6: fleet-operator → existing path unchanged (regression guard)
    # ------------------------------------------------------------------
    def test_fleet_operator_existing_path_unchanged(self, commands_lambda):
        """fleet-operator with an on-fleet vehicle must still receive 200.

        PROPERTY: adding the technician branch must not disturb the fleet-operator path.
        Mutation that MUST break this: accidentally gate the fleet-operator on
        _active_ro_exists() too → no RO seeded → 403 returned, assertion fails.

        Uses read_dtcs to avoid the run_routine powertrain/routine-id machinery,
        which is not what this test is guarding.
        """
        from unittest.mock import MagicMock
        fleet_id = "fleet-regression"
        vehicle_id = "VEH-REG-001"
        vin = "1REG00000REGRESS1"

        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        # seed vehicle + fleet enrollment; NO repair order needed for operators
        _seed_vehicle(vehicle_id, vin, fuel_type="gasoline")
        _seed_enrollment(fleet_id, vehicle_id)

        resp = commands_lambda.handler(
            _sovd_event(vehicle_id, _READ_DTCS_BODY, _operator_claims(fleet_id)),
            None,
        )
        assert resp["statusCode"] == 200, (
            f"EXPECTED 200 — fleet-operator's existing path must be unaffected by the "
            f"technician branch. "
            f"Got {resp['statusCode']}: {resp.get('body', '')}. "
            f"The technician branch must only ADD a path, not gate the existing one."
        )
        mock_iot.publish.assert_called_once()

    # ------------------------------------------------------------------
    # Test 7: tech + RO status Draft → ALLOWED (dispatch creates ROs as Draft)
    # ------------------------------------------------------------------
    def test_tech_ro_status_draft_allowed(self, commands_lambda):
        """A technician whose VIN has a Draft RO must receive 200.

        F2 (compounding): create_repair_order() writes status='Draft'.
        If Draft were excluded from the active set, the dispatch->diagnose flow
        would be permanently broken: the tech dispatches the job, the system
        creates a Draft RO, and the tech is immediately denied because no
        active RO exists.

        PROPERTY: Draft is in the active set — hard pin, not a style choice.
        Mutation that MUST break this: remove 'Draft' from RO_ACTIVE_STATUSES →
        _active_ro_exists returns False → 403 returned, assertion fails.
        """
        from unittest.mock import MagicMock
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()
        _seed_repair_order("RO-007", self.VIN, self.DEALER_ID, "Draft")

        resp = commands_lambda.handler(
            _sovd_event(self.VEHICLE_ID, _RUN_ROUTINE_BODY, _tech_claims(self.DEALER_ID)),
            None,
        )
        assert resp["statusCode"] == 200, (
            f"EXPECTED 200 — technician with a Draft RO must be admitted. "
            f"Draft is the initial status written by create_repair_order() (F2). "
            f"Excluding it breaks the dispatch->diagnose flow. "
            f"Got {resp['statusCode']}: {resp.get('body', '')}. "
            f"Technician branch with Draft in active set is not yet implemented (F2, T2.2)."
        )
        mock_iot.publish.assert_called_once()



# ══════════════════════════════════════════════════════════════════════════════
# SERVICE_ONLY invoke gate (F8 lift) — added after security review G2
#
# WHY THIS CLASS EXISTS
# --------------------
# Group 2 shipped the F8 lift with ZERO tests on the SERVICE_ONLY *invoke* path. Only the
# catalog listing was covered. Security review G2 then found two Warnings in that untested
# gate, and both were reachable in production:
#
#   W2 — a caller holding BOTH platform-admin AND dms-technician got SERVICE_ONLY on any
#        VIN with no repair order existing anywhere. _authorize_per_vin returns None at
#        `if caller['is_admin']` BEFORE its technician branch, so the RO predicate was never
#        evaluated, and a gate written as `not is_technician` then admitted them. The code
#        carried a comment asserting "a technician without an active RO never reaches this
#        point" — true for pure technicians, false for the composite. Nothing prevents the
#        combination: the pool has ~10 platform-admins and the seed script assigns
#        dms-technician.
#
#   W1 — an active RO proved paperwork, not presence. The dealer-scoped
#        create_repair_order authorizes the *dealer* and never the VIN, and writes status
#        "Draft" unconditionally. So a service-advisor could mint an RO naming any VIN and
#        hand every technician at that dealer a SERVICE_ONLY capability on it.
#
# The lesson is not the two holes. It is that a safety-class boundary was lifted and the
# lift itself had no invoke-path test — so both defects were invisible to a green suite.
# These are the tests that would have caught them.
# ══════════════════════════════════════════════════════════════════════════════

_SERVICE_ONLY_ROUTINE = "dpf_regeneration"   # SERVICE_ONLY, ICE_DIESEL only
_STATIONARY_ROUTINE = "glow_plug_test"       # STATIONARY, and present on ICE_DIESEL
#
# Both routine ids are verified against the ICE_DIESEL profile rather than assumed. The first
# draft used `evap_purge`, which is STATIONARY but is NOT in the diesel catalog, so the test
# failed with "This routine is not available for this vehicle" — a fixture error that looked
# exactly like the behaviour regression it was written to rule out. Verify with:
#   python3 -c "import sys;sys.path.insert(0,'.');from _shared.routine_catalog import \
#     get_routines_for_profile;print([(e['routine_id'],e['safety_class']) \
#     for e in get_routines_for_profile('ICE_DIESEL')])"

_SERVICE_ONLY_BODY = {
    "command_type": "run_routine",
    "routine_id": _SERVICE_ONLY_ROUTINE,
    "attestation": {
        "text": "I confirm I am at the vehicle and it is in service.",
        "user_email": "tech@dealer-denver.example.com",
        "timestamp_ms": 1735000000000,
    },
}

_STATIONARY_BODY = {
    "command_type": "run_routine",
    "routine_id": _STATIONARY_ROUTINE,
    "attestation": {
        "text": "I confirm the vehicle is stationary and safe to run this routine.",
        "user_email": "tech@dealer-denver.example.com",
        "timestamp_ms": 1735000000000,
    },
}


def _admin_technician_claims(dealer_ids: str = "dealer-denver") -> dict:
    """The composite caller from W2 — holds platform-admin AND dms-technician."""
    return {
        "cognito:groups": "platform-admin,dms-technician",
        "email": "admin-tech@example.com",
        "custom:dealerIds": dealer_ids,
        "custom:fleetIds": "",
    }


def _plain_admin_claims() -> dict:
    return {
        "cognito:groups": "platform-admin",
        "email": "admin@example.com",
        "custom:fleetIds": "",
    }


class TestServiceOnlyInvokeGate:
    """The F8 lift must require a PRESENT technician — not a group claim, not paperwork."""

    VEHICLE_ID = "VEH-DIESEL-SO-001"
    VIN = "1DIESEL0000000SO1"
    DEALER_ID = "dealer-denver"

    def _setup_diesel(self):
        # diesel so the ICE_DIESEL profile carries dpf_regeneration (SERVICE_ONLY)
        _seed_vehicle(self.VEHICLE_ID, self.VIN, fuel_type="diesel")

    def _invoke(self, lam, body, claims):
        from unittest.mock import MagicMock
        lam.iot_data = MagicMock()
        return lam.handler(_sovd_event(self.VEHICLE_ID, body, claims), None)

    # ── The lift works when presence is real ──────────────────────────────────

    def test_technician_with_work_underway_may_invoke_service_only(self, commands_lambda):
        """The lift's happy path: InProgress RO at the caller's dealer → permitted.

        This is the only state in which SERVICE_ONLY becomes invocable, and it must keep
        working — a fix for the two Warnings that also broke this would have removed the
        feature rather than secured it.
        """
        self._setup_diesel()
        _seed_repair_order("RO-SO-001", self.VIN, self.DEALER_ID, "InProgress")

        resp = self._invoke(commands_lambda, _SERVICE_ONLY_BODY, _tech_claims(self.DEALER_ID))
        assert resp["statusCode"] == 200, (
            f"a technician with work underway on this vehicle must be able to invoke "
            f"SERVICE_ONLY (F8). Got {resp['statusCode']}: {resp.get('body')}"
        )

    # ── W1: paperwork is not presence ─────────────────────────────────────────

    def test_technician_with_only_a_draft_ro_is_denied_service_only(self, commands_lambda):
        """W1: a Draft RO must NOT unlock SERVICE_ONLY.

        Draft is what the dealer-scoped create_repair_order writes unconditionally, and that
        endpoint never authorizes the VIN — so a service-advisor can mint a Draft RO for any
        vehicle. If Draft unlocked SERVICE_ONLY, that fabrication would hand every technician
        at the dealer a safety-class-lifted capability on an arbitrary VIN.

        Mutation that MUST break this: pass _RO_ACTIVE_STATUSES instead of
        _RO_PRESENCE_STATUSES at the SERVICE_ONLY gate.
        """
        self._setup_diesel()
        _seed_repair_order("RO-SO-002", self.VIN, self.DEALER_ID, "Draft")

        resp = self._invoke(commands_lambda, _SERVICE_ONLY_BODY, _tech_claims(self.DEALER_ID))
        assert resp["statusCode"] == 403, (
            f"a Draft-only repair order must NOT unlock SERVICE_ONLY — Draft proves "
            f"paperwork, not that the vehicle is in the bay. Got {resp['statusCode']}: "
            f"{resp.get('body')}"
        )
        error = json.loads(resp["body"]).get("error", "").lower()
        assert "underway" in error or "in service" in error, (
            f"the denial should explain that work must be underway, so a technician "
            f"understands the RO needs advancing rather than that they lack permission. "
            f"Got: {error!r}"
        )

    def test_draft_ro_still_permits_stationary(self, commands_lambda):
        """The narrower presence set must apply ONLY to SERVICE_ONLY.

        The CMS dispatch flow creates ROs as Draft (F2), so INERT and STATIONARY must remain
        invocable immediately on dispatch. If the W1 fix had narrowed the shared active-status
        set instead of adding a separate one, this test would fail and the spec's headline
        dispatch→diagnose flow would be broken.
        """
        self._setup_diesel()
        _seed_repair_order("RO-SO-003", self.VIN, self.DEALER_ID, "Draft")

        resp = self._invoke(commands_lambda, _STATIONARY_BODY, _tech_claims(self.DEALER_ID))
        assert resp["statusCode"] == 200, (
            f"a Draft repair order must still permit STATIONARY — narrowing the shared "
            f"active-status set would break the dispatch flow. Got {resp['statusCode']}: "
            f"{resp.get('body')}"
        )

    # ── W2: the composite caller ──────────────────────────────────────────────

    def test_admin_technician_composite_denied_service_only(self, commands_lambda):
        """W2: platform-admin + dms-technician must NOT get SERVICE_ONLY.

        AN ACTIVE RO IS SEEDED DELIBERATELY, and that detail is the whole test.

        The first version of this test seeded no repair order and asserted `!= 200`. It
        passed — and it passed under a mutation that removed the admin exclusion entirely,
        because with no RO the *presence* check (the W1 fix) denied the request instead. The
        assertion was satisfied by a different guard than the one it names, so it could not
        observe the W2 fix at all. That is the same defect shape as the claim-shape tests
        earlier in this file: a denial test that accepts any denial cannot tell which guard
        fired.

        With an InProgress RO at the composite caller's own dealership, the presence check
        PASSES. The only thing left standing between this caller and a SERVICE_ONLY actuation
        is the admin exclusion, so a failure here is unambiguous.

        Mutation that MUST break this: drop `caller.get('is_admin') or` from the gate.
        """
        self._setup_diesel()
        # Presence deliberately satisfied — otherwise the W1 guard would deny first and this
        # test would pass without ever exercising the admin exclusion.
        _seed_repair_order("RO-SO-006", self.VIN, self.DEALER_ID, "InProgress")

        resp = self._invoke(
            commands_lambda, _SERVICE_ONLY_BODY, _admin_technician_claims(self.DEALER_ID)
        )
        assert resp["statusCode"] != 200, (
            f"a platform-admin holding dms-technician must NOT invoke SERVICE_ONLY. The "
            f"admin short-circuit in _authorize_per_vin means presence was never established "
            f"for this caller by the authz path, so the gate must exclude admins explicitly. "
            f"Got {resp['statusCode']}: {resp.get('body')}"
        )
        # And it must be refused by the ADMIN exclusion, not by the presence check — the
        # presence check is satisfied above, so if the error mentions work being underway then
        # the seeding is wrong and this test has stopped isolating W2.
        error = json.loads(resp["body"]).get("error", "").lower()
        assert "underway" not in error, (
            f"denied by the presence check rather than the admin exclusion — the seeded "
            f"InProgress RO should have satisfied presence. Got: {error!r}"
        )

    def test_plain_admin_denied_service_only(self, commands_lambda):
        """F8 scope item 2 for the ordinary admin, with an active RO present.

        Seeds an InProgress RO so the denial cannot be attributed to a missing RO — the
        refusal must be about the caller not being a present technician.
        """
        self._setup_diesel()
        _seed_repair_order("RO-SO-004", self.VIN, self.DEALER_ID, "InProgress")

        resp = self._invoke(commands_lambda, _SERVICE_ONLY_BODY, _plain_admin_claims())
        assert resp["statusCode"] != 200, (
            f"platform-admin must not invoke SERVICE_ONLY even when the vehicle has work "
            f"underway — admin is not at the vehicle. Got {resp['statusCode']}: "
            f"{resp.get('body')}"
        )

    # ── Unchanged for everyone else ───────────────────────────────────────────

    def test_fleet_operator_still_denied_service_only(self, commands_lambda):
        """D17 is unchanged for fleet-operators — the lift is technician-only."""
        self._setup_diesel()
        _seed_repair_order("RO-SO-005", self.VIN, self.DEALER_ID, "InProgress")

        resp = self._invoke(
            commands_lambda, _SERVICE_ONLY_BODY, _operator_claims("fleet-test")
        )
        assert resp["statusCode"] != 200, (
            f"the F8 lift must not widen SERVICE_ONLY beyond technicians. "
            f"Got {resp['statusCode']}: {resp.get('body')}"
        )



# ══════════════════════════════════════════════════════════════════════════════
# Catalog/invoke agreement — added after security review G2 Cycle 2 (Critical C1)
#
# WHY A PAIRED TEST, AND WHY THE SINGLE-SIDED ONES MISSED THIS
# -----------------------------------------------------------
# C1: a technician whose only RO on a vehicle is Draft could read the catalog (the read gate
# admits Draft, per _RO_ACTIVE_STATUSES) and was told `invocableByCaller: True` plus
# "Invocable by you now" — then the invoke returned 403, because the SERVICE_ONLY lift
# requires _RO_PRESENCE_STATUSES and Draft is not in it.
#
# Every existing test passed. The catalog tests seeded InProgress; the invoke tests asserted
# a 403 on Draft. Each side was correct in isolation, and no test asserted the two AGREE on
# one fixture — so the contradiction lived exactly in the gap between two green test files.
#
# The deeper cause is worth stating: fixing W1 (adding _RO_PRESENCE_STATUSES) invalidated the
# PREMISE of the S1 fix, which had derived invocability from "the read gate already enforced
# the predicate". Nothing in S1's code changed. Nothing flagged it. A control whose
# correctness rests on two things being equal has to be re-examined whenever either moves.
#
# These tests assert the invariant directly — catalog and invoke, same fixture, same caller —
# so any future divergence fails regardless of which side moves.
# ══════════════════════════════════════════════════════════════════════════════


class TestCatalogAndInvokeAgree:
    """`invocableByCaller` must equal what the invoke path actually does. Every shape."""

    VEHICLE_ID = "VEH-DIESEL-AGREE-1"
    VIN = "1DIESEL0000AGREE1"
    DEALER_ID = "dealer-denver"

    def _setup_diesel(self):
        _seed_vehicle(self.VEHICLE_ID, self.VIN, fuel_type="diesel")

    def _catalog_says_invocable(self, lam, claims) -> bool:
        resp = lam.handler(
            {
                "path": f"/api/commands/{self.VEHICLE_ID}/routines",
                "httpMethod": "GET",
                "queryStringParameters": {},
                "requestContext": {"authorizer": {"claims": claims}},
            },
            None,
        )
        assert resp["statusCode"] == 200, f"catalog read failed: {resp}"
        routines = json.loads(resp["body"])["routines"]
        service_only = [r for r in routines if r["safetyClass"] == "SERVICE_ONLY"]
        assert service_only, "diesel profile must carry SERVICE_ONLY routines"
        return all(r["invocableByCaller"] for r in service_only)

    def _invoke_succeeds(self, lam, claims) -> bool:
        from unittest.mock import MagicMock
        lam.iot_data = MagicMock()
        resp = lam.handler(_sovd_event(self.VEHICLE_ID, _SERVICE_ONLY_BODY, claims), None)
        return resp["statusCode"] == 200

    def test_draft_only_ro_catalog_and_invoke_agree(self, commands_lambda):
        """THE C1 CASE. Draft-only RO: the catalog must say False and the invoke must refuse.

        Before the fix the catalog said True and the invoke returned 403 — and both the
        catalog test (which seeded InProgress) and the invoke test (which asserted 403 on
        Draft) passed, because neither compared the two.
        """
        self._setup_diesel()
        _seed_repair_order("RO-AGREE-DRAFT", self.VIN, self.DEALER_ID, "Draft")
        claims = _tech_claims(self.DEALER_ID)

        catalog = self._catalog_says_invocable(commands_lambda, claims)
        invoke = self._invoke_succeeds(commands_lambda, claims)

        assert catalog is False, (
            "catalog reported SERVICE_ONLY invocable for a technician whose only repair "
            "order is Draft, but the SERVICE_ONLY lift requires work underway "
            "(_RO_PRESENCE_STATUSES). This is C1."
        )
        assert invoke is False, "invoke should refuse a Draft-only RO for SERVICE_ONLY"
        assert catalog == invoke, (
            f"catalog and invoke disagree: invocableByCaller={catalog}, invoke_ok={invoke}. "
            f"The field exists so a UI can render the button state correctly; a disagreement "
            f"means the UI is lying to the technician."
        )

    def test_work_underway_catalog_and_invoke_agree(self, commands_lambda):
        """The positive shape: InProgress RO → catalog True and invoke succeeds."""
        self._setup_diesel()
        _seed_repair_order("RO-AGREE-INPROG", self.VIN, self.DEALER_ID, "InProgress")
        claims = _tech_claims(self.DEALER_ID)

        catalog = self._catalog_says_invocable(commands_lambda, claims)
        invoke = self._invoke_succeeds(commands_lambda, claims)

        assert catalog is True, "a technician with work underway must see invocable=True"
        assert invoke is True, "a technician with work underway must be able to invoke"
        assert catalog == invoke

    def test_composite_admin_technician_catalog_and_invoke_agree(self, commands_lambda):
        """Composite admin+technician: both sides must refuse (W2 + S1 together).

        Presence is satisfied so neither side can refuse for the wrong reason — the only
        thing that should deny is the admin exclusion, and it must deny on BOTH sides.
        """
        self._setup_diesel()
        _seed_repair_order("RO-AGREE-COMP", self.VIN, self.DEALER_ID, "InProgress")
        claims = _admin_technician_claims(self.DEALER_ID)

        catalog = self._catalog_says_invocable(commands_lambda, claims)
        invoke = self._invoke_succeeds(commands_lambda, claims)

        assert catalog is False, (
            "catalog reported SERVICE_ONLY invocable for a platform-admin holding "
            "dms-technician; the invoke path excludes admins (W2), so this would lie."
        )
        assert invoke is False
        assert catalog == invoke

    def test_fleet_operator_catalog_and_invoke_agree(self, commands_lambda):
        """A fleet-operator must see False and be refused — the lift is technician-only."""
        self._setup_diesel()
        _seed_repair_order("RO-AGREE-OP", self.VIN, self.DEALER_ID, "InProgress")
        _seed_enrollment("fleet-agree", self.VEHICLE_ID)
        claims = _operator_claims("fleet-agree")

        catalog = self._catalog_says_invocable(commands_lambda, claims)
        invoke = self._invoke_succeeds(commands_lambda, claims)

        assert catalog is False
        assert invoke is False
        assert catalog == invoke



    def test_composite_viewer_technician_catalog_and_invoke_agree(self, commands_lambda):
        """Composite fleet-viewer + dms-technician: both sides must refuse.

        Review Cycle 3 S1. This is C1's shape on a different axis. The catalog read calls
        `_authorize_per_vin(..., write_route=False)`, which does not apply the read-only
        denial; the invoke path calls it with write_route=True, which denies a viewer BEFORE
        the technician branch is reached. So without the viewer exclusion the catalog said
        invocable and the invoke refused.

        The reviewer graded it a Suggestion because no user in the pool holds both groups. It
        is pinned here anyway: "no user has this combination" is a fact about seed data, and
        this invariant should be a property of the code. Presence is satisfied so neither side
        can refuse for an unrelated reason.

        Mutation that MUST break this: drop `and not is_viewer_caller` from
        technician_may_invoke.
        """
        self._setup_diesel()
        _seed_repair_order("RO-AGREE-VIEW", self.VIN, self.DEALER_ID, "InProgress")
        claims = {
            "cognito:groups": "fleet-viewer,dms-technician",
            "email": "viewer-tech@example.com",
            "custom:dealerIds": self.DEALER_ID,
            "custom:fleetIds": "",
        }

        catalog = self._catalog_says_invocable(commands_lambda, claims)
        invoke = self._invoke_succeeds(commands_lambda, claims)

        assert catalog is False, (
            "catalog reported SERVICE_ONLY invocable for a fleet-viewer holding "
            "dms-technician, but the invoke path denies viewers on write routes before the "
            "technician branch — so this advertises a capability the caller does not have."
        )
        assert invoke is False
        assert catalog == invoke, (
            f"catalog and invoke disagree for viewer+technician: "
            f"invocableByCaller={catalog}, invoke_ok={invoke}"
        )



# ── M4: Pagination guard ──────────────────────────────────────────────────────
# T6.5 mutation 4 requires a test case where the ONLY active RO for a VIN lives
# on the *second* DynamoDB page. The real moto mock returns all rows in one page,
# so this test uses unittest.mock to inject a two-page response, then asserts that
# removing the LastEvaluatedKey loop (mutation M4) would silently deny a legitimate
# technician whose RO is on page 2.

class TestPaginationGuard:
    """Pagination: active RO on page 2 must still be found.

    Mutation that MUST break this (M4): replace the while-True pagination loop in
    _check_technician_ro_access with a single table.query() call that ignores
    LastEvaluatedKey.  Under that mutation the page-2 RO is never read and the
    technician is denied with _TECH_DENY_NO_RO/WRONG_STATUS instead of _TECH_ALLOW.
    """

    def test_active_ro_on_page_two_is_found(self):
        """If the only InProgress RO for the VIN is on page 2, the tech must be admitted.

        Arrange: first page returns one Closed RO; LastEvaluatedKey signals more;
                 second page returns one InProgress RO at the correct dealer.
        Act:     call _check_technician_ro_access.
        Assert:  return is _TECH_ALLOW (not a denial code).
        """
        from unittest.mock import MagicMock, patch
        import importlib
        import sys

        vin = "1PAGINATED0000001"
        dealer_id = "dealer-denver"
        dealer_ids = {dealer_id}

        # Build a fake DynamoDB Table that returns two pages.
        page1 = {
            "Items": [
                {"ro_id": "RO-PAGE1", "vehicle_vin": vin, "dealer_id": dealer_id,
                 "status": "Closed"},
            ],
            "LastEvaluatedKey": {"ro_id": {"S": "RO-PAGE1"}},  # signals more pages
        }
        page2 = {
            "Items": [
                {"ro_id": "RO-PAGE2", "vehicle_vin": vin, "dealer_id": dealer_id,
                 "status": "InProgress"},
            ],
            # No LastEvaluatedKey — end of results
        }
        call_count = {"n": 0}

        def fake_query(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return page1
            return page2

        mock_table = MagicMock()
        mock_table.query.side_effect = fake_query
        mock_ddb_resource = MagicMock()
        mock_ddb_resource.Table.return_value = mock_table

        # Reload commands_lambda so the env var is honoured under the mock.
        import os
        os.environ["DMS_REPAIR_ORDERS_TABLE"] = "dms-test-repair-orders"

        sys.modules.pop("commands_lambda", None)
        with patch("boto3.resource"):
            import commands_lambda as mod
            importlib.reload(mod)

        result = mod._check_technician_ro_access(
            vin=vin,
            dealer_ids=dealer_ids,
            ddb_resource=mock_ddb_resource,
        )

        assert result == mod._TECH_ALLOW, (
            f"Expected _TECH_ALLOW when the only active RO is on page 2; got {result!r}. "
            "Mutation M4 (drop LastEvaluatedKey loop) would produce this failure."
        )
        assert call_count["n"] == 2, (
            f"Expected exactly 2 DDB queries (page 1 + page 2), got {call_count['n']}. "
            "The pagination loop must follow LastEvaluatedKey until it is absent."
        )
