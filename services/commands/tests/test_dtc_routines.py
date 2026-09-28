"""
T2.6 — powertrain-aware DTC suggestion endpoint + F8 catalog contract.

Tests:
  1. GET /routines?dtc=P0420 on a gasoline vehicle includes o2_heater_check
     with dtcSuggested=True.
  2. GET /routines?dtc=P0420 on an EV (VEH-MRDN-0015 proxy, fuelType=electric)
     does NOT include o2_heater_check (ICE emissions routine — F6 intersection).
  3. GET /routines?dtc=P0420 on an EV returns no ICE emissions routine at all.
  4. GET /routines without ?dtc= returns routines with no dtcSuggested key.
  5. GET /routines?dtc=UNKNOWN (no suggestions) returns all profile routines,
     all dtcSuggested=False.
  6. DTC param is uppercase-normalised server-side (p0420 == P0420).
  7. Unknown fuelType → 400 (profile_for_fuel_type returns None → refusal).
  8. F8 — invocable is the static field (SERVICE_ONLY → False for all callers).
  9. F8 — invocableByCaller is True for SERVICE_ONLY only when caller is a technician.
 10. F8 — precondition_text for SERVICE_ONLY is caller-aware.
 11. Suggested routines appear before non-suggested in the response list.
"""
import importlib
import json
import os
import sys

import boto3
import pytest
from moto import mock_aws

_STAGE = "test"
_REGION = "us-west-2"
_COMMANDS_TABLE = f"cms-{_STAGE}-storage-commands"
_VEHICLES_TABLE = f"cms-{_STAGE}-storage-vehicles"
_ENROLLMENT_TABLE = f"cms-{_STAGE}-fleet-enrollment"
_RO_TABLE = f"dms-{_STAGE}-repair-orders"

os.environ.setdefault("AWS_DEFAULT_REGION", _REGION)
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ["DEPLOYMENT_STAGE"] = _STAGE
os.environ["COMMANDS_TABLE"] = _COMMANDS_TABLE
os.environ["FLEET_ENROLLMENT_TABLE_NAME"] = _ENROLLMENT_TABLE
os.environ["DMS_REPAIR_ORDERS_TABLE"] = _RO_TABLE


@pytest.fixture
def commands_lambda():
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=_REGION)
        ddb.create_table(
            TableName=_COMMANDS_TABLE,
            KeySchema=[{"AttributeName": "commandId", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "commandId", "AttributeType": "S"},
                {"AttributeName": "vehicleId", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "vehicleId-index",
                    "KeySchema": [{"AttributeName": "vehicleId", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.create_table(
            TableName=_VEHICLES_TABLE,
            KeySchema=[{"AttributeName": "vehicleId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "vehicleId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        # Fleet enrollment table (needed by resolve_vins_to_fleets for fleet-op authz)
        # Schema: fleetId (HASH), vehicleId (RANGE) -- vehicleId stores the
        # real vehicleId here, NOT the VIN. GSI: vehicleId-index HASH=vehicleId.
        # See issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/.
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
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "vehicleId-index",
                    "KeySchema": [{"AttributeName": "vehicleId", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        # DMS repair-orders table (needed by _check_technician_ro_access)
        ddb.create_table(
            TableName=_RO_TABLE,
            KeySchema=[{"AttributeName": "ro_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "ro_id", "AttributeType": "S"},
                {"AttributeName": "vehicle_vin", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "vin-index",
                    "KeySchema": [{"AttributeName": "vehicle_vin", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        ddb_res = boto3.resource("dynamodb", region_name=_REGION)
        tbl = ddb_res.Table(_VEHICLES_TABLE)
        # Gasoline demo vehicle — enrolled in fleet-test
        tbl.put_item(Item={
            "vehicleId": "VEH-GAS-001",
            "vin": "GASVIN0000000001",
            "fuelType": "gasoline",
        })
        # EV demo vehicle (proxy for VEH-MRDN-0015 / VEH-VO-001)
        tbl.put_item(Item={
            "vehicleId": "VEH-EV-001",
            "vin": "EVVIN00000000001",
            "fuelType": "electric",
        })
        # Unknown fuel type vehicle
        tbl.put_item(Item={
            "vehicleId": "VEH-UNKNOWN-001",
            "vin": "UNKNOWNVIN000001",
            "fuelType": "hydrogen",  # not in the catalog
        })
        # Diesel vehicle
        tbl.put_item(Item={
            "vehicleId": "VEH-DIESEL-001",
            "vin": "DIESELVIN0000001",
            "fuelType": "diesel",
        })

        # Enroll vehicles in fleet-test for fleet-operator tests
        # Enrollment is keyed on vehicleId, NOT vin -- cms-{stage}-fleet-enrollment
        # has no `vin` attribute at all; resolve_vins_to_fleets() queries the
        # vehicleId-index GSI. See
        # issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/.
        enroll_tbl = ddb_res.Table(_ENROLLMENT_TABLE)
        for vehicle_id in ["VEH-GAS-001", "VEH-EV-001",
                           "VEH-UNKNOWN-001", "VEH-DIESEL-001"]:
            enroll_tbl.put_item(Item={"fleetId": "fleet-test", "vehicleId": vehicle_id})

        # Seed an active RO for the diesel vehicle for technician tests
        ro_tbl = ddb_res.Table(_RO_TABLE)
        ro_tbl.put_item(Item={
            "ro_id": "RO-DIESEL-001",
            "vehicle_vin": "DIESELVIN0000001",
            "dealer_id": "dealer-test",
            "status": "InProgress",
        })

        sys.modules.pop("commands_lambda", None)
        import commands_lambda as mod
        importlib.reload(mod)
        yield mod


def _admin_claims():
    return {
        "cognito:groups": "platform-admin",
        "email": "test-admin@example.com",
        "custom:fleetIds": "",
    }


def _tech_claims():
    return {
        "cognito:groups": "dms-technician",
        "email": "tech@dealer.example.com",
        "custom:dealerIds": "dealer-test",
    }


def _routines_event(vehicle_id: str, qs: dict | None = None, claims: dict | None = None) -> dict:
    return {
        "path": f"/api/commands/{vehicle_id}/routines",
        "httpMethod": "GET",
        "queryStringParameters": qs or {},
        "requestContext": {"authorizer": {"claims": claims or _admin_claims()}},
    }


def _get_routines(resp) -> list:
    assert resp["statusCode"] == 200, resp
    return json.loads(resp["body"])["routines"]


# ─── Test 1: gasoline vehicle with P0420 includes o2_heater_check ────────────

def test_p0420_gasoline_includes_o2_heater_check(commands_lambda):
    resp = commands_lambda.handler(
        _routines_event("VEH-GAS-001", {"dtc": "P0420"}), None
    )
    routines = _get_routines(resp)
    by_id = {r["routineId"]: r for r in routines}
    assert "o2_heater_check" in by_id, (
        f"o2_heater_check should be present on gasoline P0420; got: {list(by_id)}"
    )
    assert by_id["o2_heater_check"]["dtcSuggested"] is True, (
        "o2_heater_check must be dtcSuggested=True for P0420"
    )


# ─── Test 2 + 3: EV with P0420 must NOT include o2_heater_check ──────────────

def test_p0420_ev_does_not_include_o2_heater_check(commands_lambda):
    """F6: the result must be the intersection of DTC map with the EV profile.
    o2_heater_check is ICE_GASOLINE-only — absent from the EV profile."""
    resp = commands_lambda.handler(
        _routines_event("VEH-EV-001", {"dtc": "P0420"}), None
    )
    routines = _get_routines(resp)
    ice_emissions = ["o2_heater_check", "evap_leak_test", "evap_purge",
                     "injector_balance_test", "glow_plug_test",
                     "dpf_regeneration", "reductant_dosing_cycle"]
    by_id = {r["routineId"]: r for r in routines}
    suggested_ids = [r["routineId"] for r in routines if r.get("dtcSuggested")]
    for ice_routine in ice_emissions:
        if ice_routine in by_id:
            # If present (shouldn't be for ICE-only), must not be suggested
            assert not by_id[ice_routine].get("dtcSuggested"), (
                f"ICE-only routine {ice_routine!r} must not be dtcSuggested on an EV"
            )
    assert "o2_heater_check" not in by_id, (
        f"o2_heater_check must be absent on EV profile entirely; got: {list(by_id)}"
    )


def test_p0420_ev_returns_no_ice_emissions_routine(commands_lambda):
    """Explicit: EV response contains ZERO ICE emissions routines."""
    resp = commands_lambda.handler(
        _routines_event("VEH-EV-001", {"dtc": "P0420"}), None
    )
    routines = _get_routines(resp)
    by_id = {r["routineId"]: r for r in routines}
    ice_only = {"o2_heater_check", "evap_purge", "evap_leak_test",
                "injector_balance_test", "glow_plug_test",
                "dpf_regeneration", "reductant_dosing_cycle"}
    present_ice = ice_only & set(by_id.keys())
    assert not present_ice, (
        f"EV vehicle must have no ICE emissions routines; found: {present_ice}"
    )


# ─── Test 4: no ?dtc= → no dtcSuggested key in entries ──────────────────────

def test_no_dtc_param_returns_routines_without_dtc_suggested_key(commands_lambda):
    resp = commands_lambda.handler(_routines_event("VEH-GAS-001"), None)
    routines = _get_routines(resp)
    assert len(routines) > 0
    for r in routines:
        assert "dtcSuggested" not in r, (
            f"dtcSuggested key must not be present when no ?dtc= supplied; "
            f"got {r!r}"
        )


# ─── Test 5: unknown DTC → all routines with dtcSuggested=False ──────────────

def test_unknown_dtc_returns_all_routines_all_not_suggested(commands_lambda):
    resp = commands_lambda.handler(
        _routines_event("VEH-GAS-001", {"dtc": "P9999"}), None
    )
    routines = _get_routines(resp)
    assert len(routines) > 0
    for r in routines:
        assert r.get("dtcSuggested") is False, (
            f"All routines must have dtcSuggested=False for unknown DTC; got {r!r}"
        )


# ─── Test 6: DTC param is uppercase-normalised ────────────────────────────────

def test_dtc_lowercase_normalised_to_uppercase(commands_lambda):
    resp_lower = commands_lambda.handler(
        _routines_event("VEH-GAS-001", {"dtc": "p0420"}), None
    )
    resp_upper = commands_lambda.handler(
        _routines_event("VEH-GAS-001", {"dtc": "P0420"}), None
    )
    body_lower = json.loads(resp_lower["body"])
    body_upper = json.loads(resp_upper["body"])
    # The response dtc field must be normalised
    assert body_lower.get("dtc") == "P0420", (
        f"Expected normalised DTC 'P0420'; got {body_lower.get('dtc')!r}"
    )
    # Suggestion lists must match
    suggested_lower = {r["routineId"] for r in body_lower["routines"] if r.get("dtcSuggested")}
    suggested_upper = {r["routineId"] for r in body_upper["routines"] if r.get("dtcSuggested")}
    assert suggested_lower == suggested_upper, (
        f"lowercase and UPPERCASE DTC must produce same suggestions; "
        f"lower={suggested_lower!r}, upper={suggested_upper!r}"
    )


# ─── Test 7: Unknown fuelType → 400 ──────────────────────────────────────────

def test_unknown_fuel_type_returns_400(commands_lambda):
    resp = commands_lambda.handler(_routines_event("VEH-UNKNOWN-001"), None)
    assert resp["statusCode"] == 400, (
        f"Unknown fuelType must return 400; got {resp['statusCode']}: {resp['body']}"
    )
    err = json.loads(resp["body"])
    assert "powertrain" in err.get("error", "").lower(), (
        f"Error must mention powertrain; got: {err}"
    )


# ─── Test 8: F8 — invocable is static (SERVICE_ONLY → False) ─────────────────

def test_invocable_is_static_field_service_only_false(commands_lambda):
    """invocable must remain the static property — SERVICE_ONLY is always False here,
    regardless of caller. Existing consumers depend on this."""
    resp = commands_lambda.handler(_routines_event("VEH-DIESEL-001"), None)
    routines = _get_routines(resp)
    service_only = [r for r in routines if r["safetyClass"] == "SERVICE_ONLY"]
    assert service_only, "Diesel vehicle must have SERVICE_ONLY routines (dpf_regeneration, etc.)"
    for r in service_only:
        assert r["invocable"] is False, (
            f"invocable must be False for SERVICE_ONLY even on technician path; "
            f"got {r['routineId']}: invocable={r['invocable']!r}"
        )


# ─── Test 9: F8 — invocableByCaller for technician vs fleet-operator ─────────

def test_invocable_by_caller_true_for_technician_service_only(commands_lambda):
    """A technician caller gets invocableByCaller=True for SERVICE_ONLY routines."""
    resp = commands_lambda.handler(
        _routines_event("VEH-DIESEL-001", claims=_tech_claims()), None
    )
    routines = _get_routines(resp)
    service_only = [r for r in routines if r["safetyClass"] == "SERVICE_ONLY"]
    assert service_only, "Diesel vehicle must have SERVICE_ONLY routines"
    for r in service_only:
        assert r["invocableByCaller"] is True, (
            f"invocableByCaller must be True for technician on SERVICE_ONLY; "
            f"got {r['routineId']}: {r['invocableByCaller']!r}"
        )
        # Static invocable field must still be False
        assert r["invocable"] is False, (
            f"invocable (static) must remain False for SERVICE_ONLY; "
            f"got {r['routineId']}: {r['invocable']!r}"
        )


def test_invocable_by_caller_false_for_technician_without_active_ro(commands_lambda):
    """A technician with no active RO at their dealership is refused the READ, not misled.

    This test pins the load-bearing consequence of sharing one predicate between the catalog
    read and the invoke path, and it exists because two successive implementations of
    `invocableByCaller` got that relationship wrong in opposite directions:

      1. Computed from group membership. Right answer, wrong reason — it agreed with the
         predicate only because `_authorize_per_vin` had already enforced the predicate.
      2. Re-queried DynamoDB to "compute the real predicate". Also right, and a redundant
         Query on every catalog fetch, because the read gate had already established the
         same fact from the same index.

    The actual contract is stronger than either: a technician who cannot invoke cannot even
    READ the catalog for that vehicle, because for a technician `_authorize_per_vin`'s
    vehicle-scope test IS the active-RO predicate. So there is no state in which the response
    advertises a routine the caller may not invoke — the "enabled button that 403s" is
    unreachable by construction rather than prevented by a computed field.

    WHY THIS MATTERS BEYOND TODAY: it means `invocableByCaller` cannot be False for a
    technician who received a 200. If someone later opens the catalog read to technicians
    outside their bay — a reasonable thing to want, since the payload is only routine names
    and safety classes — then that field silently starts lying and the removed DDB query has
    to come back. This test fails the moment that gate is relaxed, which is the point.
    """
    other_dealer_tech = {
        "cognito:groups": "dms-technician",
        "email": "tech@other-dealer.example.com",
        # Valid technician, valid claim shape — but a dealership with no RO for this VIN.
        "custom:dealerIds": "dealer-nowhere",
    }
    resp = commands_lambda.handler(
        _routines_event("VEH-DIESEL-001", claims=other_dealer_tech), None
    )

    assert resp["statusCode"] == 403, (
        f"a technician whose dealership has no active RO for this vehicle must be refused "
        f"the catalog read, so no response can advertise routines they cannot invoke. "
        f"Got {resp['statusCode']}: {resp.get('body')}"
    )
    error = json.loads(resp["body"]).get("error", "")
    assert "dealership" in error.lower(), (
        f"the denial should name the dealer-scope reason so the cause is diagnosable; "
        f"got {error!r}"
    )
    # Specifically NOT the generic pre-branch denial — that would mean the technician branch
    # was never reached and this test would be passing for an unrelated reason.
    assert "fleet-operator" not in error.lower(), (
        f"denied by the generic group check rather than the technician RO predicate; "
        f"got {error!r}"
    )


def test_invocable_by_caller_false_for_fleet_op_service_only(commands_lambda):
    """A fleet-operator gets invocableByCaller=False for SERVICE_ONLY routines."""
    fleet_op_claims = {
        "cognito:groups": "fleet-operator",
        "email": "op@fleet.example.com",
        "custom:fleetIds": "fleet-test",
    }
    resp = commands_lambda.handler(
        _routines_event("VEH-DIESEL-001", claims=fleet_op_claims), None
    )
    routines = _get_routines(resp)
    service_only = [r for r in routines if r["safetyClass"] == "SERVICE_ONLY"]
    assert service_only
    for r in service_only:
        assert r["invocableByCaller"] is False, (
            f"invocableByCaller must be False for fleet-operator on SERVICE_ONLY; "
            f"got {r['routineId']}: {r['invocableByCaller']!r}"
        )


# ─── Test 10: F8 — precondition_text is caller-aware for SERVICE_ONLY ─────────

def test_precondition_text_service_only_technician_caller(commands_lambda):
    """Technician must NOT see 'Not available remotely' for SERVICE_ONLY — that
    string contradicts a response that authorises them to invoke it."""
    resp = commands_lambda.handler(
        _routines_event("VEH-DIESEL-001", claims=_tech_claims()), None
    )
    routines = _get_routines(resp)
    service_only = [r for r in routines if r["safetyClass"] == "SERVICE_ONLY"]
    assert service_only
    for r in service_only:
        precond = r.get("precondition", "")
        assert "not available remotely" not in precond.lower(), (
            f"Technician must not see 'Not available remotely' precondition; "
            f"got {r['routineId']}: {precond!r}"
        )
        assert "technician" in precond.lower() or "repair order" in precond.lower(), (
            f"Technician precondition text must reference technician or repair order; "
            f"got {r['routineId']}: {precond!r}"
        )


def test_precondition_text_service_only_fleet_op_caller(commands_lambda):
    """Fleet-operator must see 'Not available remotely' for SERVICE_ONLY."""
    fleet_op_claims = {
        "cognito:groups": "fleet-operator",
        "email": "op@fleet.example.com",
        "custom:fleetIds": "fleet-test",
    }
    resp = commands_lambda.handler(
        _routines_event("VEH-DIESEL-001", claims=fleet_op_claims), None
    )
    routines = _get_routines(resp)
    service_only = [r for r in routines if r["safetyClass"] == "SERVICE_ONLY"]
    assert service_only
    for r in service_only:
        precond = r.get("precondition", "")
        assert "not available remotely" in precond.lower(), (
            f"Fleet-operator must see 'Not available remotely' for SERVICE_ONLY; "
            f"got {r['routineId']}: {precond!r}"
        )


# ─── Test 11: Suggested routines appear before non-suggested ─────────────────

def test_dtc_suggested_routines_ordered_first(commands_lambda):
    resp = commands_lambda.handler(
        _routines_event("VEH-GAS-001", {"dtc": "P0420"}), None
    )
    routines = _get_routines(resp)
    # All suggested rows must come before any non-suggested row.
    seen_non_suggested = False
    for r in routines:
        if r.get("dtcSuggested") is False:
            seen_non_suggested = True
        if seen_non_suggested and r.get("dtcSuggested") is True:
            ids = [rr["routineId"] for rr in routines]
            pytest.fail(
                f"Non-suggested row appeared before suggested row — "
                f"suggested routines must be ordered first. Order: {ids}"
            )
