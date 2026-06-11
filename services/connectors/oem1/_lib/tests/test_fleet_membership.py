"""Unit tests for _lib/fleet_membership.py — 6 cases per spec T1.3 Accept."""
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("FLEET_ENROLLMENT_TABLE_NAME", "cms-staging-storage-fleet-enrollment")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")

from _lib.fleet_membership import parse_fleet_ids, resolve_vins_to_fleets  # noqa: E402


def _mock_ddb(items_by_vin: dict) -> MagicMock:
    """Return a DDB mock whose query() returns items based on the :v ExpressionAttributeValue."""
    ddb = MagicMock()

    def _query(**kwargs):
        vin = kwargs["ExpressionAttributeValues"][":v"]["S"]
        items = items_by_vin.get(vin, [])
        return {"Items": items}

    ddb.query.side_effect = _query
    return ddb


# ---------------------------------------------------------------------------
# resolve_vins_to_fleets
# ---------------------------------------------------------------------------

def test_resolve_returns_mapping_for_found_vins():
    vin = "1FTFW1ET0EKE00001"
    ddb = _mock_ddb({vin: [{"fleetId": {"S": "FLEET-A"}, "vehicleId": {"S": vin}}]})
    result = resolve_vins_to_fleets([vin], ddb_client=ddb)
    assert result == {vin: "FLEET-A"}


def test_resolve_omits_not_found_vins():
    vin = "1FTFW1ET0EKE99999"
    ddb = _mock_ddb({})  # no items for any VIN
    result = resolve_vins_to_fleets([vin], ddb_client=ddb)
    assert result == {}


def test_resolve_100_vin_batch():
    vins = [f"VIN{str(i).zfill(6)}" for i in range(100)]
    items = {v: [{"fleetId": {"S": "FLEET-B"}, "vehicleId": {"S": v}}] for v in vins}
    ddb = _mock_ddb(items)
    result = resolve_vins_to_fleets(vins, ddb_client=ddb)
    assert len(result) == 100
    assert all(result[v] == "FLEET-B" for v in vins)


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
