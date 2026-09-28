# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Task 2.1 — spec 2026-09-01-cms-remote-diagnostics-sovd
# Fleet-scoped authz tests for SOVD commands (HARD GATE E).

import importlib
import json
import os
import sys
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

# ── Test environment ──────────────────────────────────────────────────────────
_STAGE = "test"
_REGION = "us-west-2"
_COMMANDS_TABLE = f"cms-{_STAGE}-storage-commands"
_VEHICLES_TABLE = f"cms-{_STAGE}-storage-vehicles"
_ENROLLMENT_TABLE = f"cms-{_STAGE}-fleet-enrollment"

os.environ.setdefault("AWS_DEFAULT_REGION", _REGION)
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ["DEPLOYMENT_STAGE"] = _STAGE
os.environ["COMMANDS_TABLE"] = _COMMANDS_TABLE
os.environ["FLEET_ENROLLMENT_TABLE_NAME"] = _ENROLLMENT_TABLE


# ── DDB bootstrap helpers ─────────────────────────────────────────────────────

def _bootstrap_tables():
    """Create the DDB tables required by the test; idempotent."""
    ddb = boto3.client("dynamodb", region_name=_REGION)
    existing = {t for t in ddb.list_tables().get("TableNames", [])}

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


def _seed_vehicle(vehicle_id: str, vin: str):
    """Insert a vehicle row with VIN."""
    table = boto3.resource("dynamodb", region_name=_REGION).Table(_VEHICLES_TABLE)
    table.put_item(Item={"vehicleId": vehicle_id, "vin": vin})


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


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def commands_lambda():
    """Import commands_lambda under moto with DDB tables bootstrapped."""
    with mock_aws():
        _bootstrap_tables()
        sys.modules.pop("commands_lambda", None)
        import commands_lambda as mod
        importlib.reload(mod)
        yield mod


def _event(vehicle_id: str, body: dict, claims: dict) -> dict:
    """Build a POST /api/commands/{vehicleId} event with given claims."""
    return {
        "path": f"/api/commands/{vehicle_id}",
        "httpMethod": "POST",
        "body": json.dumps(body),
        "requestContext": {"authorizer": {"claims": claims}},
    }


# ── Caller claim shapes ───────────────────────────────────────────────────────

def _groupless_claims():
    return {"cognito:groups": "", "email": "nobody@example.com", "custom:fleetIds": ""}

def _admin_claims():
    return {"cognito:groups": "platform-admin", "email": "admin@example.com", "custom:fleetIds": ""}

def _operator_claims(fleet_id: str):
    return {
        "cognito:groups": "fleet-operator",
        "email": "operator@example.com",
        "custom:fleetIds": fleet_id,
    }

def _viewer_claims(fleet_id: str):
    return {
        "cognito:groups": "fleet-viewer",
        "email": "viewer@example.com",
        "custom:fleetIds": fleet_id,
    }

def _driver_self_claims(driver_id: str = "driver-001"):
    return {
        "cognito:groups": "",
        "email": "driver@example.com",
        "custom:fleetIds": "",
        "custom:driverId": driver_id,
    }


_VALID_ATTESTATION = {
    "text": "I have verified the underlying repair is complete",
    "user_email": "operator@example.com",
    "timestamp_ms": 1735000000000,
}

_READ_DTCS_BODY = {
    "command_type": "read_dtcs",
    "components": ["ECU_ENGINE"],
    "include_freeze_frame": False,
}

_CLEAR_DTCS_BODY = {
    "command_type": "clear_dtcs",
    "components": ["ECU_ENGINE"],
    "attestation": _VALID_ATTESTATION,
}


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_no_fail_open_pattern_in_commands_lambda():
    """HARD GATE E: commands_lambda.py must never contain 'or not user_groups' (fail-open authz pattern).

    Source-scan invariant — mirrors test_no_fail_open_invariant in
    services/simulation/lambda/test_simulation_lambda.py.
    """
    lambda_src = os.path.join(
        os.path.dirname(__file__), "..", "commands_lambda.py"
    )
    with open(lambda_src) as f:
        source = f.read()
    forbidden = "or not user_groups"
    assert forbidden not in source, (
        f"Found forbidden fail-open pattern '{forbidden}' in commands_lambda.py. "
        "This pattern was the P0 fail-open; it must not reappear. "
        "See commit d235fb31 and spec 2026-08-05-main-api-fail-open-authz-defaults."
    )


class TestSovdReadDtcsRouteAuthz:
    """HARD GATE E: per-VIN fleet-scoped authz for read_dtcs."""

    VEHICLE_ID = "VEH-AUTHZ-001"
    VIN = "1TEST00000AUTHZ001"
    FLEET_ID = "fleet-alpha"
    OTHER_FLEET_ID = "fleet-beta"

    def _setup_vehicle(self):
        """Seed vehicle + enrollment for this test class."""
        _seed_vehicle(self.VEHICLE_ID, self.VIN)
        _seed_enrollment(self.FLEET_ID, self.VEHICLE_ID)

    # ── Groupless caller is denied ──
    def test_groupless_denied(self, commands_lambda):
        """Groupless caller (no cognito:groups) must receive 403 — no fail-open."""
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()

        resp = commands_lambda.handler(
            _event(self.VEHICLE_ID, _READ_DTCS_BODY, _groupless_claims()), None
        )
        assert resp["statusCode"] == 403, resp
        mock_iot.publish.assert_not_called()

    # ── Driver-self denied ──
    def test_driver_self_denied(self, commands_lambda):
        """Driver-self token must receive 403 on SOVD routes."""
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()

        resp = commands_lambda.handler(
            _event(self.VEHICLE_ID, _READ_DTCS_BODY, _driver_self_claims()), None
        )
        assert resp["statusCode"] == 403, resp
        mock_iot.publish.assert_not_called()

    # ── Off-fleet operator is denied ──
    def test_off_fleet_operator_denied(self, commands_lambda):
        """Operator whose fleet does not own this VIN must receive 403."""
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()

        resp = commands_lambda.handler(
            _event(self.VEHICLE_ID, _READ_DTCS_BODY, _operator_claims(self.OTHER_FLEET_ID)), None
        )
        assert resp["statusCode"] == 403, resp
        mock_iot.publish.assert_not_called()

    # ── On-fleet operator is admitted ──
    def test_on_fleet_operator_admitted(self, commands_lambda):
        """Operator whose fleet owns this VIN must receive 200."""
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()

        resp = commands_lambda.handler(
            _event(self.VEHICLE_ID, _READ_DTCS_BODY, _operator_claims(self.FLEET_ID)), None
        )
        assert resp["statusCode"] == 200, resp
        mock_iot.publish.assert_called_once()

    # ── Admin bypasses fleet check ──
    def test_admin_bypass_regardless_of_fleet(self, commands_lambda):
        """platform-admin must receive 200 regardless of fleet assignment."""
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()

        resp = commands_lambda.handler(
            _event(self.VEHICLE_ID, _READ_DTCS_BODY, _admin_claims()), None
        )
        assert resp["statusCode"] == 200, resp
        mock_iot.publish.assert_called_once()


class TestSovdClearDtcsRouteAuthz:
    """HARD GATE E + HARD GATE I: clear_dtcs authz — viewer denied, attestation required."""

    VEHICLE_ID = "VEH-CLEAR-001"
    VIN = "1TEST00000CLEAR001"
    FLEET_ID = "fleet-gamma"
    OTHER_FLEET_ID = "fleet-delta"

    def _setup_vehicle(self):
        _seed_vehicle(self.VEHICLE_ID, self.VIN)
        _seed_enrollment(self.FLEET_ID, self.VEHICLE_ID)

    # ── Missing attestation → 400 before authz ──
    def test_missing_attestation_returns_400(self, commands_lambda):
        """clear_dtcs without attestation must return 400 — before authz gate."""
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()

        body = {"command_type": "clear_dtcs", "components": ["ECU_ENGINE"]}
        # Use admin claims so authz wouldn't block — we want to confirm it's a 400 not 403
        resp = commands_lambda.handler(_event(self.VEHICLE_ID, body, _admin_claims()), None)
        assert resp["statusCode"] == 400, resp
        body_parsed = json.loads(resp["body"])
        assert "attestation" in body_parsed["error"].lower()
        mock_iot.publish.assert_not_called()

    # ── Malformed attestation (empty object) → 400 ──
    def test_malformed_attestation_returns_400(self, commands_lambda):
        """clear_dtcs with empty attestation object must return 400."""
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()

        body = {"command_type": "clear_dtcs", "components": ["ECU_ENGINE"], "attestation": {}}
        resp = commands_lambda.handler(_event(self.VEHICLE_ID, body, _admin_claims()), None)
        assert resp["statusCode"] == 400, resp
        mock_iot.publish.assert_not_called()

    # ── Groupless caller is denied ──
    def test_groupless_denied(self, commands_lambda):
        """Groupless caller must receive 403 — no fail-open."""
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()

        resp = commands_lambda.handler(
            _event(self.VEHICLE_ID, _CLEAR_DTCS_BODY, _groupless_claims()), None
        )
        assert resp["statusCode"] == 403, resp
        mock_iot.publish.assert_not_called()

    # ── Driver-self denied ──
    def test_driver_self_denied(self, commands_lambda):
        """Driver-self token must receive 403 on clear_dtcs."""
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()

        resp = commands_lambda.handler(
            _event(self.VEHICLE_ID, _CLEAR_DTCS_BODY, _driver_self_claims()), None
        )
        assert resp["statusCode"] == 403, resp
        mock_iot.publish.assert_not_called()

    # ── On-fleet viewer is denied on write route ──
    def test_on_fleet_viewer_denied_on_clear(self, commands_lambda):
        """fleet-viewer must receive 403 on clear_dtcs even when on-fleet — viewers are read-only."""
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()

        resp = commands_lambda.handler(
            _event(self.VEHICLE_ID, _CLEAR_DTCS_BODY, _viewer_claims(self.FLEET_ID)), None
        )
        assert resp["statusCode"] == 403, resp
        mock_iot.publish.assert_not_called()

    # ── Off-fleet operator is denied ──
    def test_off_fleet_operator_denied(self, commands_lambda):
        """Operator whose fleet does not own this VIN must receive 403."""
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()

        resp = commands_lambda.handler(
            _event(self.VEHICLE_ID, _CLEAR_DTCS_BODY, _operator_claims(self.OTHER_FLEET_ID)), None
        )
        assert resp["statusCode"] == 403, resp
        mock_iot.publish.assert_not_called()

    # ── On-fleet operator is admitted ──
    def test_on_fleet_operator_admitted(self, commands_lambda):
        """fleet-operator whose fleet owns this VIN must receive 200."""
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()

        resp = commands_lambda.handler(
            _event(self.VEHICLE_ID, _CLEAR_DTCS_BODY, _operator_claims(self.FLEET_ID)), None
        )
        assert resp["statusCode"] == 200, resp
        mock_iot.publish.assert_called_once()

    # ── Admin bypasses fleet check ──
    def test_admin_bypass_regardless_of_fleet(self, commands_lambda):
        """platform-admin must receive 200 regardless of fleet assignment."""
        mock_iot = MagicMock()
        commands_lambda.iot_data = mock_iot
        self._setup_vehicle()

        resp = commands_lambda.handler(
            _event(self.VEHICLE_ID, _CLEAR_DTCS_BODY, _admin_claims()), None
        )
        assert resp["statusCode"] == 200, resp
        mock_iot.publish.assert_called_once()


# ── FG2.1 test: attestation.user_email overwrite ─────────────────────────────

def test_sovd_clear_dtcs_overwrites_client_attestation_email(commands_lambda):
    """FG2.1 / HARD GATE I: client-supplied attestation.user_email is discarded;
    Cognito-derived caller email replaces it before DDB PutItem and MQTT publish.

    Alice (fleet-operator) submits clear_dtcs with attestation.user_email='attacker@example.com'.
    Her Cognito claim has email='real@example.com'.
    The DDB row and MQTT payload must carry 'real@example.com', NEVER 'attacker@example.com'.
    """
    _VEHICLE_ID = "VEH-ATTEST-001"
    _VIN = "1TEST00000ATTEST01"
    _FLEET_ID = "fleet-attest"

    mock_iot = MagicMock()
    commands_lambda.iot_data = mock_iot
    _seed_vehicle(_VEHICLE_ID, _VIN)
    _seed_enrollment(_FLEET_ID, _VEHICLE_ID)

    # Operator claims — Cognito email is the authoritative identity
    caller_claims = {
        "cognito:groups": "fleet-operator",
        "email": "real@example.com",
        "custom:fleetIds": _FLEET_ID,
    }

    body = {
        "command_type": "clear_dtcs",
        "components": ["ECU_ENGINE"],
        "attestation": {
            "text": "I have verified the underlying repair is complete",
            "user_email": "attacker@example.com",   # forged — should be discarded
            "timestamp_ms": 1735000000000,
        },
    }

    resp = commands_lambda.handler(
        _event(_VEHICLE_ID, body, caller_claims), None
    )
    assert resp["statusCode"] == 200, resp
    resp_body = json.loads(resp["body"])
    correlation_id = resp_body["correlationId"]

    # (a) DDB row must store the Cognito-derived email, not the attacker-supplied one
    ddb_table = boto3.resource("dynamodb", region_name=_REGION).Table(_COMMANDS_TABLE)
    item = ddb_table.get_item(Key={"commandId": correlation_id}).get("Item", {})
    assert item, f"No DDB row found for commandId={correlation_id!r}"
    attestation_on_row = item.get("attestation", {})
    assert attestation_on_row.get("user_email") == "real@example.com", (
        f"DDB attestation.user_email must be the Cognito caller email 'real@example.com', "
        f"got: {attestation_on_row.get('user_email')!r}"
    )

    # (b) MQTT publish payload must also carry the Cognito-derived email
    mock_iot.publish.assert_called_once()
    call_kwargs = mock_iot.publish.call_args.kwargs
    published = json.loads(call_kwargs["payload"])
    attestation_in_publish = published.get("attestation", {})
    assert attestation_in_publish.get("user_email") == "real@example.com", (
        f"MQTT payload attestation.user_email must be 'real@example.com', "
        f"got: {attestation_in_publish.get('user_email')!r}"
    )

    # (c) attacker@example.com must NOT appear anywhere in the mocked call arguments
    all_call_args_str = str(mock_iot.publish.call_args_list)
    assert "attacker@example.com" not in all_call_args_str, (
        "attacker@example.com must not appear in any MQTT publish call; "
        "found in mocked calls: " + all_call_args_str
    )
    assert "attacker@example.com" not in str(item), (
        "attacker@example.com must not appear in the DDB row"
    )
