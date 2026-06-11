"""Shared VIN→fleet resolution utilities. See spec § 2 (2026-06-09-cms-fleet-manager-cognito-role)."""
import os

import boto3

_ddb_client = None


def _get_ddb():
    global _ddb_client
    if _ddb_client is None:
        _ddb_client = boto3.client("dynamodb")
    return _ddb_client


def resolve_vins_to_fleets(
    vins: list[str],
    ddb_client=None,
    table_name: str | None = None,
) -> dict[str, str]:
    """Per-VIN GSI Query on vehicleId-index; returns {vin: fleetId} for found VINs only.

    Domain invariant: one fleet per VIN. Uses eventual consistency (GSI constraint).
    See spec § 2 (2026-06-09-cms-fleet-manager-cognito-role).
    """
    client = ddb_client or _get_ddb()
    tbl = table_name or os.environ["FLEET_ENROLLMENT_TABLE_NAME"]
    out = {}
    for vin in vins:
        resp = client.query(
            TableName=tbl,
            IndexName="vehicleId-index",
            KeyConditionExpression="vehicleId = :v",
            ExpressionAttributeValues={":v": {"S": vin}},
            Limit=1,
        )
        if resp.get("Items"):
            out[vin] = resp["Items"][0]["fleetId"]["S"]
    return out


def parse_fleet_ids(claims: dict) -> set[str]:
    """Return set of fleetIds from comma-separated custom:fleetIds claim; empty set if absent/blank."""
    raw = claims.get("custom:fleetIds", "")
    if not raw:
        return set()
    return {f.strip() for f in raw.split(",") if f.strip()}
