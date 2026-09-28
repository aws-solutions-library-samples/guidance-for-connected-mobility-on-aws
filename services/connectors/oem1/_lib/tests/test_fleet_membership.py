"""Unit tests for _lib/fleet_membership.py — 6 cases per spec T1.3 Accept."""
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("FLEET_ENROLLMENT_TABLE_NAME", "cms-staging-storage-fleet-enrollment")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")

from _lib.fleet_membership import classify_driver_self, parse_fleet_ids, resolve_vins_to_fleets  # noqa: E402


def _mock_ddb(items_by_vehicle_id: dict) -> MagicMock:
    """Return a DDB mock whose query() returns items based on the :v ExpressionAttributeValue."""
    ddb = MagicMock()

    def _query(**kwargs):
        vehicle_id = kwargs["ExpressionAttributeValues"][":v"]["S"]
        items = items_by_vehicle_id.get(vehicle_id, [])
        return {"Items": items}

    ddb.query.side_effect = _query
    return ddb


# ---------------------------------------------------------------------------
# resolve_vins_to_fleets
#
# Despite the function's name, it resolves `vehicleId`s (the vehicleId-index
# GSI's partition key), NOT VINs — cms-{stage}-storage-fleet-enrollment has no
# `vin` attribute at all. Corrected 2026-09-18 per
# issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/.
#
# The three tests below were pre-existing and used VIN-shaped strings
# (`"1FTFW1ET0EKE00001"`) as their fixture values — technically correct
# (the function doesn't care what the string LOOKS like, only that it matches
# the vehicleId-index), but the naming reinforced the wrong mental model this
# whole issue is about. Renamed to `vehicle_id` throughout. A fourth test below
# uses a deliberately non-VIN-shaped vehicleId (`VEH-MRDN-0001`, matching the
# real divergent-vehicle naming convention) to make the point explicit rather
# than leave it implicit in a VIN-shaped string that happens to still work.
# ---------------------------------------------------------------------------

def test_resolve_returns_mapping_for_found_vehicle_ids():
    vehicle_id = "1FTFW1ET0EKE00001"
    ddb = _mock_ddb({vehicle_id: [{"fleetId": {"S": "FLEET-A"}, "vehicleId": {"S": vehicle_id}}]})
    result = resolve_vins_to_fleets([vehicle_id], ddb_client=ddb)
    assert result == {vehicle_id: "FLEET-A"}


def test_resolve_returns_mapping_for_a_non_vin_shaped_vehicle_id():
    """The real defect: a vehicleId that does NOT look like a VIN (matching the
    VEH-MRDN-* / VEH-DEMO-PUB-* divergent-vehicle naming convention from
    issues/2026-09-05-vehicleid-diverges-from-vin/) must resolve correctly.
    Before the 2026-09-18 fix, every caller passed the vehicle's VIN here
    instead of its vehicleId, which would never match this row."""
    vehicle_id = "VEH-MRDN-0001"
    ddb = _mock_ddb({vehicle_id: [{"fleetId": {"S": "FLEET-MERIDIAN"}, "vehicleId": {"S": vehicle_id}}]})
    result = resolve_vins_to_fleets([vehicle_id], ddb_client=ddb)
    assert result == {vehicle_id: "FLEET-MERIDIAN"}
    # The real VIN for this same vehicle must NOT resolve — proves the function
    # is genuinely keyed on vehicleId, not on VIN-shaped-ness or any fallback.
    unresolved = resolve_vins_to_fleets(["MRDN0000000000001"], ddb_client=ddb)
    assert unresolved == {}


def test_resolve_omits_not_found_vehicle_ids():
    vehicle_id = "1FTFW1ET0EKE99999"
    ddb = _mock_ddb({})  # no items for any vehicleId
    result = resolve_vins_to_fleets([vehicle_id], ddb_client=ddb)
    assert result == {}


def test_resolve_100_vehicle_id_batch():
    vehicle_ids = [f"VIN{str(i).zfill(6)}" for i in range(100)]
    items = {v: [{"fleetId": {"S": "FLEET-B"}, "vehicleId": {"S": v}}] for v in vehicle_ids}
    ddb = _mock_ddb(items)
    result = resolve_vins_to_fleets(vehicle_ids, ddb_client=ddb)
    assert len(result) == 100
    assert all(result[v] == "FLEET-B" for v in vehicle_ids)


# ---------------------------------------------------------------------------
# parse_fleet_ids
# ---------------------------------------------------------------------------

def test_parse_fleet_ids_populated():
    result = parse_fleet_ids({"custom:fleetIds": "FLEET-A,FLEET-B"})
    assert result == {"FLEET-A", "FLEET-B"}


def test_parse_fleet_ids_empty_string():
    result = parse_fleet_ids({"custom:fleetIds": ""})
    assert result == set()


def test_parse_fleet_ids_missing_claim():
    result = parse_fleet_ids({})
    assert result == set()


# ---------------------------------------------------------------------------
# classify_driver_self
# ---------------------------------------------------------------------------

def test_classify_driver_self_returns_id():
    """A driver-only token with custom:driverId is classified as driver-self and the id is returned."""
    claims = {"custom:driverId": "driver-42", "cognito:groups": ""}
    is_driver, driver_id = classify_driver_self(claims, driver_self_enabled=True)
    assert is_driver is True
    assert driver_id == "driver-42"


def test_classify_driver_self_excludes_operators():
    """A token that holds an operator group is NOT classified as driver-self, even with custom:driverId."""
    for group in ("platform-admin", "fleet-operator"):
        claims = {"custom:driverId": "driver-99", "cognito:groups": group}
        is_driver, driver_id = classify_driver_self(claims, driver_self_enabled=True)
        assert is_driver is False, f"Expected not driver-self for group {group!r}"
        assert driver_id == ""


def test_classify_driver_self_empty_when_disabled():
    """When driver_self_enabled=False the function always returns (False, '')."""
    claims = {"custom:driverId": "driver-7", "cognito:groups": ""}
    is_driver, driver_id = classify_driver_self(claims, driver_self_enabled=False)
    assert is_driver is False
    assert driver_id == ""


def test_classify_driver_self_returns_empty_when_no_driver_id():
    """A token without custom:driverId (e.g. a demo/service account) is not driver-self."""
    claims = {"cognito:groups": ""}
    is_driver, driver_id = classify_driver_self(claims, driver_self_enabled=True)
    assert is_driver is False
    assert driver_id == ""
