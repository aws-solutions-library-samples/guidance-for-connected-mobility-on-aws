# SPDX-License-Identifier: Apache-2.0
"""
Body-VIN bypass + geofence route authorization.

issues/2026-09-26-commands-api-body-vin-bypasses-fleet-check/

Two properties pinned here (each with a recorded mutation that MUST fail its
test — see the docstring on each test):

  1. POST /api/commands/{vehicleId}: the FleetWise topic VIN is resolved
     server-side from the URL vehicleId for every non-driver caller.
     A body `vin` that differs from the resolved value is 400. A matching
     body vin is accepted. An omitted body vin is the normal path. Applies
     equally to fleet-operator, admin, and dms-technician branches.

  2. Geofence CRUD: every /api/geofences route runs per-vehicle authz.
     Writes (POST, DELETE) require write rights; reads (GET) require read
     rights. Driver-self is refused everywhere (`_authorize_per_vin`
     denies driver-self at the top; geofence CRUD is not in
     `_DRIVER_SELF_COMMANDS`).

The test file is deliberately separate from `test_authz_dms_technician.py`
and `test_driver_self_commands.py`: it exercises the SAME `_authorize_per_vin`
helper from a different call site (the geofence handler dispatch) plus a new
policy in `_send_command`.
"""
import importlib
import json
import os
import sys
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

_STAGE = "test"
_REGION = "us-west-2"
_COMMANDS_TABLE = f"cms-{_STAGE}-storage-commands"
_VEHICLES_TABLE = f"cms-{_STAGE}-storage-vehicles"
_ENROLLMENT_TABLE = f"cms-{_STAGE}-fleet-enrollment"
_GEOFENCES_TABLE = f"cms-{_STAGE}-storage-geofences"
_DRIVERS_TABLE = f"cms-{_STAGE}-storage-drivers"
_CATALOG_TABLE = f"cms-{_STAGE}-signal-catalog"
_RO_TABLE = f"dms-{_STAGE}-repair-orders"

os.environ.setdefault("AWS_DEFAULT_REGION", _REGION)
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ["DEPLOYMENT_STAGE"] = _STAGE
os.environ["COMMANDS_TABLE"] = _COMMANDS_TABLE
os.environ["FLEET_ENROLLMENT_TABLE_NAME"] = _ENROLLMENT_TABLE
os.environ["DMS_REPAIR_ORDERS_TABLE"] = _RO_TABLE
os.environ["GEOFENCES_TABLE"] = _GEOFENCES_TABLE
os.environ["DRIVERS_TABLE"] = _DRIVERS_TABLE
os.environ["SIGNAL_CATALOG_TABLE"] = _CATALOG_TABLE

# Two vehicles across two fleets. Cross-fleet body-VIN is the P1 bypass.
FLEET_ALPHA = "fleet-alpha"
FLEET_BRAVO = "fleet-bravo"
VEH_A = "VEH-ALPHA-001"
VIN_A = "ALPHA00000000001"
VEH_B = "VEH-BRAVO-001"
VIN_B = "BRAVO00000000002"

# DMS technician + a dealer that has an active RO for VEH-A only.
DEALER_DENVER = "dealer-denver"

# Driver for driver-self tests
DRIVER_ID = "DRV-GEO-001"


def _table(name):
    return boto3.resource("dynamodb", region_name=_REGION).Table(name)


def _bootstrap_tables():
    ddb = boto3.client("dynamodb", region_name=_REGION)
    existing = set(ddb.list_tables().get("TableNames", []))

    if _COMMANDS_TABLE not in existing:
        ddb.create_table(
            TableName=_COMMANDS_TABLE,
            KeySchema=[{"AttributeName": "commandId", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "commandId", "AttributeType": "S"},
                {"AttributeName": "vehicleId", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "vehicleId-index",
                "KeySchema": [{"AttributeName": "vehicleId", "KeyType": "HASH"}],
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

    if _GEOFENCES_TABLE not in existing:
        ddb.create_table(
            TableName=_GEOFENCES_TABLE,
            KeySchema=[{"AttributeName": "geofenceId", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "geofenceId", "AttributeType": "S"},
                {"AttributeName": "vehicleId", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "vehicleId-index",
                "KeySchema": [{"AttributeName": "vehicleId", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )

    if _DRIVERS_TABLE not in existing:
        ddb.create_table(
            TableName=_DRIVERS_TABLE,
            KeySchema=[{"AttributeName": "driverId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "driverId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

    if _CATALOG_TABLE not in existing:
        ddb.create_table(
            TableName=_CATALOG_TABLE,
            KeySchema=[{"AttributeName": "signal_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "signal_id", "AttributeType": "N"}],
            BillingMode="PAY_PER_REQUEST",
        )

    if _RO_TABLE not in existing:
        ddb.create_table(
            TableName=_RO_TABLE,
            KeySchema=[{"AttributeName": "ro_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "ro_id", "AttributeType": "S"},
                {"AttributeName": "vehicle_vin", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "vin-index",
                "KeySchema": [{"AttributeName": "vehicle_vin", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )


def _seed():
    """Two vehicles across two fleets; a Denver technician with an RO on VEH_A."""
    _table(_VEHICLES_TABLE).put_item(
        Item={"vehicleId": VEH_A, "vin": VIN_A, "fuelType": "gasoline"}
    )
    _table(_VEHICLES_TABLE).put_item(
        Item={"vehicleId": VEH_B, "vin": VIN_B, "fuelType": "gasoline"}
    )
    _table(_ENROLLMENT_TABLE).put_item(Item={"fleetId": FLEET_ALPHA, "vehicleId": VEH_A})
    _table(_ENROLLMENT_TABLE).put_item(Item={"fleetId": FLEET_BRAVO, "vehicleId": VEH_B})
    _table(_RO_TABLE).put_item(Item={
        "ro_id": "RO-A", "vehicle_vin": VIN_A,
        "dealer_id": DEALER_DENVER, "status": "InProgress",
    })
    _table(_DRIVERS_TABLE).put_item(
        Item={"driverId": DRIVER_ID, "assignedVehicleId": VEH_A, "status": "ACTIVE"}
    )
    _table(_CATALOG_TABLE).put_item(Item={
        "signal_id": 101, "json_field": "flash_hazards", "signal_name": "flash_hazards",
        "actuator": {"commandName": "flash_hazards", "label": "Flash hazards", "category": "security"},
    })


@pytest.fixture
def cl():
    with mock_aws():
        _bootstrap_tables()
        _seed()
        sys.modules.pop("commands_lambda", None)
        import commands_lambda as mod  # noqa: E402
        importlib.reload(mod)
        mod.iot_data = MagicMock()
        yield mod


# ── Claims helpers ────────────────────────────────────────────────────────────

def _operator(fleet_ids: str) -> dict:
    return {
        "cognito:groups": "fleet-operator",
        "email": f"op-{fleet_ids}@example.com",
        "custom:fleetIds": fleet_ids,
    }


def _viewer(fleet_ids: str) -> dict:
    return {
        "cognito:groups": "fleet-viewer",
        "email": f"viewer-{fleet_ids}@example.com",
        "custom:fleetIds": fleet_ids,
    }


def _admin() -> dict:
    return {
        "cognito:groups": "platform-admin",
        "email": "admin@example.com",
    }


def _tech(dealer_ids: str) -> dict:
    return {
        "cognito:groups": "dms-technician",
        "email": f"tech-{dealer_ids}@example.com",
        "custom:fleetIds": "",
        "custom:dealerIds": dealer_ids,
    }


def _driver() -> dict:
    return {"custom:driverId": DRIVER_ID, "email": "driver@example.com"}


def _event(method, path, claims, body=None):
    return {
        "path": path,
        "httpMethod": method,
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {"authorizer": {"claims": claims}},
    }


def _send_cmd(veh_id, body, claims):
    return _event("POST", f"/api/commands/{veh_id}", claims, body=body)


# ═══════════════════════════════════════════════════════════════════════════════
# Part 1 — POST /api/commands/{vehicleId} body-VIN policy
# ═══════════════════════════════════════════════════════════════════════════════


class TestBodyVinPolicyFleetOperator:
    """The P1 bypass: fleet-operator authorized on the URL vehicleId but publishing
    to a body VIN.
    """

    # -------------------------------------------------------------------
    def test_operator_cross_fleet_body_vin_is_refused_400_and_does_not_publish(self, cl):
        """PROPERTY: a fleet-operator authorized for VEH_A (FLEET_ALPHA) cannot
        publish to VIN_B (FLEET_BRAVO) by putting VIN_B in the body.

        This is the actual bypass named in the issue. Under the vulnerable
        code, this test would receive 200 and the FWE topic would carry VIN_B.

        MUTATION that MUST break this test:
          - Drop the body-vin check: `if body_vin and resolved_vin and body_vin != resolved_vin:`
            removed. `vin = body.get('vin', '')` still fetched later. Test
            fails: statusCode 200, topic contains VIN_B.
        """
        resp = cl.handler(
            _send_cmd(VEH_A, {
                "commandName": "flash_hazards", "value": "1",
                "vin": VIN_B,   # cross-fleet body VIN — the bypass attempt
            }, _operator(FLEET_ALPHA)),
            None,
        )
        assert resp["statusCode"] == 400, resp
        body = json.loads(resp["body"])
        assert "body vin" in body.get("error", "").lower()
        cl.iot_data.publish.assert_not_called()

    def test_operator_matching_body_vin_is_accepted(self, cl):
        """A body vin that matches the server-resolved VIN is accepted.

        Defence-in-depth: if a future client echoes the field back, we do not
        break it — the property protected is only "no publish to a DIFFERENT
        VIN than the URL authorizes".
        """
        resp = cl.handler(
            _send_cmd(VEH_A, {
                "commandName": "flash_hazards", "value": "1",
                "vin": VIN_A,   # matches the server-resolved value
            }, _operator(FLEET_ALPHA)),
            None,
        )
        assert resp["statusCode"] == 200, resp
        topics = [c.kwargs["topic"] for c in cl.iot_data.publish.call_args_list]
        assert any(f"things/{VIN_A}/" in t for t in topics), topics

    def test_operator_omitted_body_vin_resolves_from_server(self, cl):
        """The normal path: no vin in the body. Every client today omits it.

        MUTATION: swap `resolved_vin` for `body.get('vin')` — the code emits
        the URL vehicleId as the FWE topic addressee since the body has no vin
        and the fallback fires. Assertion fails.
        """
        resp = cl.handler(
            _send_cmd(VEH_A, {
                "commandName": "flash_hazards", "value": "1",
                # body has no `vin` — the common client shape today
            }, _operator(FLEET_ALPHA)),
            None,
        )
        assert resp["statusCode"] == 200, resp
        topics = [c.kwargs["topic"] for c in cl.iot_data.publish.call_args_list]
        assert any(f"things/{VIN_A}/" in t for t in topics), topics
        # And crucially, the topic does NOT contain VIN_B (proof no bleed).
        assert not any(f"things/{VIN_B}/" in t for t in topics), topics

    def test_operator_same_fleet_command_still_works(self, cl):
        """Regression guard: an operator sending to their own fleet still succeeds.

        Complement of the bypass test — closing the bypass must not break the
        legitimate case.
        """
        resp = cl.handler(
            _send_cmd(VEH_B, {"commandName": "flash_hazards", "value": "1"},
                      _operator(FLEET_BRAVO)),
            None,
        )
        assert resp["statusCode"] == 200, resp


class TestBodyVinPolicyDmsTechnician:
    """The DMS technician branch used body-VIN for its RO lookup pre-fix.
    A technician with an RO for VEH_A could authorize against VIN_A and
    publish to VIN_B by putting VIN_B in the body. This is a narrower version
    of the operator bypass: the authorization check and the publish addressee
    both need to be the same vehicle.
    """

    def test_technician_cross_vin_body_is_refused_400(self, cl):
        """PROPERTY: a Denver technician with an RO on VIN_A cannot publish to
        VIN_B by putting VIN_B in the body.

        Under the vulnerable code, `_authorize_per_vin(caller, body_vin=VIN_B,
        vehicle_id=VEH_A, ...)` runs the technician branch with body_vin
        — checks the RO on VIN_B (none), and denies. But if the technician
        DID have an RO on VIN_B (a real cross-dealer scenario in the wild),
        publish would still go to VIN_B while the URL says VEH_A. The 400 here
        codifies the URL as the source of truth.

        MUTATION that MUST break this: drop the body-vin != resolved_vin
        check — Denver's tech with an RO on VIN_A would then send commands to
        VEH_A over the topic addressed to VIN_B (falls through to the fleet
        branch which finds no fleet for a technician, and returns some other
        error). Assertion `statusCode == 400 & "body vin"` fails either way.
        """
        resp = cl.handler(
            _send_cmd(VEH_A, {
                "commandName": "flash_hazards", "value": "1",
                "vin": VIN_B,
            }, _tech(DEALER_DENVER)),
            None,
        )
        assert resp["statusCode"] == 400, resp
        body = json.loads(resp["body"])
        assert "body vin" in body.get("error", "").lower()

    def test_technician_matching_body_vin_uses_ro_branch(self, cl):
        """Regression guard: a Denver technician with an active RO on VIN_A,
        sending with body vin = VIN_A, still authorises through the RO branch.
        """
        resp = cl.handler(
            _send_cmd(VEH_A, {
                "commandName": "flash_hazards", "value": "1",
                "vin": VIN_A,
            }, _tech(DEALER_DENVER)),
            None,
        )
        assert resp["statusCode"] == 200, resp


class TestBodyVinPolicyAdmin:
    def test_admin_cross_fleet_body_vin_is_still_refused_400(self, cl):
        """PROPERTY: even a platform-admin cannot publish to a VIN different from
        the URL. Admin is a bypass on the *fleet* check, not on VIN consistency.

        MUTATION that MUST break this: gate the body-vin check on `not
        is_admin`. Assertion fails because a mismatched body vin now goes
        through as admin.
        """
        resp = cl.handler(
            _send_cmd(VEH_A, {
                "commandName": "flash_hazards", "value": "1",
                "vin": VIN_B,
            }, _admin()),
            None,
        )
        assert resp["statusCode"] == 400, resp


class TestBodyVinDriverSelfUnchanged:
    def test_driver_body_vin_still_ignored_and_server_vin_used(self, cl):
        """Regression: 0e4f3862's driver-self policy (body vin ignored) still
        holds. The driver's assigned vehicle is VEH_A. A body vin of VIN_B
        must not appear on any published topic.
        """
        resp = cl.handler(
            _send_cmd(VEH_A, {
                "commandName": "flash_hazards", "value": "1",
                "vin": VIN_B,   # driver-supplied; must be ignored
            }, _driver()),
            None,
        )
        assert resp["statusCode"] == 200, resp
        topics = [c.kwargs["topic"] for c in cl.iot_data.publish.call_args_list]
        assert not any(VIN_B in t for t in topics), topics
        assert any(f"things/{VIN_A}/" in t for t in topics), topics


# ═══════════════════════════════════════════════════════════════════════════════
# Part 2 — Geofence route authorization
# ═══════════════════════════════════════════════════════════════════════════════


def _seed_geofence(gf_id: str, vehicle_id: str = "ALL"):
    _table(_GEOFENCES_TABLE).put_item(Item={
        "geofenceId": gf_id,
        "vehicleId": vehicle_id,
        "name": f"Geofence {gf_id}",
        "active": True,
    })


class TestGeofencePostAuthz:
    """POST /api/geofences — create. Requires write rights on the target
    vehicle. `vehicleId: 'ALL'` in the body is admin-only.
    """

    _BODY_A = {"vehicleId": VEH_A, "name": "A", "centerLat": 39.7, "centerLng": -105.0, "radiusKm": 1}
    _BODY_B = {"vehicleId": VEH_B, "name": "B", "centerLat": 39.7, "centerLng": -105.0, "radiusKm": 1}
    _BODY_ALL = {"vehicleId": "ALL", "name": "Global", "centerLat": 39.7, "centerLng": -105.0, "radiusKm": 1}

    def test_operator_cross_fleet_create_is_refused(self, cl):
        """PROPERTY: an operator authorized for FLEET_ALPHA cannot create a
        geofence for VEH_B (FLEET_BRAVO).

        MUTATION that MUST break this: remove `_authorize_per_vin(...)` from
        the POST branch of the geofence dispatch. Test would get 200 back.
        """
        resp = cl.handler(_event("POST", "/api/geofences", _operator(FLEET_ALPHA), body=self._BODY_B), None)
        assert resp["statusCode"] == 403, resp

    def test_operator_same_fleet_create_succeeds(self, cl):
        """Regression: an operator authorized for FLEET_ALPHA can create a
        geofence for VEH_A.
        """
        resp = cl.handler(_event("POST", "/api/geofences", _operator(FLEET_ALPHA), body=self._BODY_A), None)
        assert resp["statusCode"] == 200, resp

    def test_viewer_write_is_refused(self, cl):
        """Read-only role cannot create geofences (write route).

        MUTATION: pass write_route=False to _authorize_per_vin in the POST
        branch. Test now returns 200. Assertion fails.
        """
        resp = cl.handler(_event("POST", "/api/geofences", _viewer(FLEET_ALPHA), body=self._BODY_A), None)
        assert resp["statusCode"] == 403, resp

    def test_driver_create_is_refused(self, cl):
        """Driver-self is refused on geofence CRUD by the design of
        _DRIVER_SELF_COMMANDS (0e4f3862): geofence is not in the allowlist,
        and _authorize_per_vin denies driver-self at the top.

        MUTATION: skip _authorize_per_vin entirely for the POST branch. Test
        fails because a driver token now succeeds.
        """
        resp = cl.handler(_event("POST", "/api/geofences", _driver(), body=self._BODY_A), None)
        assert resp["statusCode"] == 403, resp

    def test_operator_all_scope_is_refused(self, cl):
        """vehicleId=ALL is broad-scope; a fleet-operator cannot create it.

        MUTATION: gate ALL-scope on `_authorize_per_vin(write_route=True)`
        instead of `_authorize_admin_only`. An operator now passes fleet-scope
        against no vehicle. Test fails.
        """
        resp = cl.handler(_event("POST", "/api/geofences", _operator(FLEET_ALPHA), body=self._BODY_ALL), None)
        assert resp["statusCode"] == 403, resp

    def test_admin_all_scope_succeeds(self, cl):
        """Regression: admin can create ALL-scope geofences (the platform-wide
        rules are theirs by construction).
        """
        resp = cl.handler(_event("POST", "/api/geofences", _admin(), body=self._BODY_ALL), None)
        assert resp["statusCode"] == 200, resp

    def test_technician_without_ro_is_refused_on_write(self, cl):
        """Technician at a dealer with no RO for VEH_B is refused on write."""
        resp = cl.handler(_event("POST", "/api/geofences", _tech(DEALER_DENVER), body=self._BODY_B), None)
        assert resp["statusCode"] == 403, resp

    def test_technician_with_ro_can_write_that_vehicles_geofence(self, cl):
        """Technician at Denver has an InProgress RO on VIN_A. They can
        create a geofence for VEH_A on a write route (matches the DMS branch
        semantics from `test_authz_dms_technician.py`).
        """
        resp = cl.handler(_event("POST", "/api/geofences", _tech(DEALER_DENVER), body=self._BODY_A), None)
        assert resp["statusCode"] == 200, resp


class TestGeofenceGetAuthz:
    def test_operator_cross_fleet_read_is_refused(self, cl):
        """PROPERTY: read is per-vehicle-scoped.

        MUTATION: remove _authorize_per_vin from the GET branch. Test fails.
        """
        resp = cl.handler(_event("GET", f"/api/geofences/{VEH_B}", _operator(FLEET_ALPHA)), None)
        assert resp["statusCode"] == 403, resp

    def test_viewer_same_fleet_read_succeeds(self, cl):
        """Regression: a viewer with matching fleet claim reads the vehicle's
        geofences.

        MUTATION: pass write_route=True on the GET branch — the viewer is
        refused. Test fails.
        """
        resp = cl.handler(_event("GET", f"/api/geofences/{VEH_A}", _viewer(FLEET_ALPHA)), None)
        assert resp["statusCode"] == 200, resp

    def test_driver_read_is_refused(self, cl):
        """Driver-self on a read-through-authz-helper — refused.

        MUTATION: skip _authorize_per_vin on GET. Test fails.
        """
        resp = cl.handler(_event("GET", f"/api/geofences/{VEH_A}", _driver()), None)
        assert resp["statusCode"] == 403, resp

    def test_admin_read_succeeds(self, cl):
        resp = cl.handler(_event("GET", f"/api/geofences/{VEH_B}", _admin()), None)
        assert resp["statusCode"] == 200, resp


class TestGeofenceDeleteAuthz:
    def test_operator_cross_fleet_delete_is_refused(self, cl):
        """DELETE takes only the geofenceId in the URL, so the vehicleId is
        looked up server-side from the geofence row, then authorized.

        MUTATION: skip the geofence lookup, gate on nothing. Test fails.
        """
        _seed_geofence("GF-B-1", vehicle_id=VEH_B)
        resp = cl.handler(_event("DELETE", "/api/geofences/GF-B-1", _operator(FLEET_ALPHA)), None)
        assert resp["statusCode"] == 403, resp

    def test_operator_same_fleet_delete_succeeds(self, cl):
        _seed_geofence("GF-A-1", vehicle_id=VEH_A)
        resp = cl.handler(_event("DELETE", "/api/geofences/GF-A-1", _operator(FLEET_ALPHA)), None)
        assert resp["statusCode"] == 200, resp

    def test_viewer_delete_is_refused(self, cl):
        """Viewer cannot delete (write route).

        MUTATION: pass write_route=False on DELETE branch. Test fails.
        """
        _seed_geofence("GF-A-2", vehicle_id=VEH_A)
        resp = cl.handler(_event("DELETE", "/api/geofences/GF-A-2", _viewer(FLEET_ALPHA)), None)
        assert resp["statusCode"] == 403, resp

    def test_driver_delete_is_refused(self, cl):
        _seed_geofence("GF-A-3", vehicle_id=VEH_A)
        resp = cl.handler(_event("DELETE", "/api/geofences/GF-A-3", _driver()), None)
        assert resp["statusCode"] == 403, resp

    def test_unknown_geofence_is_404(self, cl):
        """An unknown geofenceId must not probe the auth surface with an
        arbitrary lookup — 404, no fleet-claim disclosure.

        MUTATION: fall through to _delete_geofence without the existence check.
        Test fails (returns 200 with an empty update).
        """
        resp = cl.handler(_event("DELETE", "/api/geofences/GF-NEVER", _admin()), None)
        assert resp["statusCode"] == 404, resp

    def test_delete_of_all_scope_geofence_requires_admin(self, cl):
        """A geofence with vehicleId='ALL' is platform-wide; deletion is
        admin-only regardless of the operator's fleet claim.

        MUTATION: treat ALL as an ordinary vehicleId (route through
        _authorize_per_vin). The operator with SOME fleet claim would fail on
        `resolve_vins_to_fleets(['ALL'])` returning empty (unknown), returning
        404 — the test expects 403 (an operator IS authenticated but not
        broad-scope authorized), so it still catches the mutation.
        """
        _seed_geofence("GF-ALL-1", vehicle_id="ALL")
        resp = cl.handler(_event("DELETE", "/api/geofences/GF-ALL-1",
                                 _operator(FLEET_ALPHA)), None)
        assert resp["statusCode"] == 403, resp

    def test_admin_deletes_all_scope_geofence(self, cl):
        _seed_geofence("GF-ALL-2", vehicle_id="ALL")
        resp = cl.handler(_event("DELETE", "/api/geofences/GF-ALL-2", _admin()), None)
        assert resp["statusCode"] == 200, resp
