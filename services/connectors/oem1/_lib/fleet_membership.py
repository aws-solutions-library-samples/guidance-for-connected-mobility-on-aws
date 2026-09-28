"""Shared VIN→fleet resolution utilities. See spec § 2 (2026-06-09-cms-fleet-manager-cognito-role)."""
import os
from typing import Tuple

import boto3

# Cognito groups that denote a CMS operator principal (never a driver-self principal).
# Deliberately 2 members: fleet-viewer is read-only and is not an "operator" for the
# purpose of driver-self classification (a viewer cannot write, so driver-self suppression
# is immaterial there).  See spec 2026-08-31-cms-sim-api-fleet-scoping § R1.
_OPERATOR_GROUPS = {"platform-admin", "fleet-operator"}

_ddb_client = None


def _get_ddb():
    global _ddb_client
    if _ddb_client is None:
        _ddb_client = boto3.client("dynamodb")
    return _ddb_client


def resolve_vins_to_fleets(
    vehicle_ids: list[str],
    ddb_client=None,
    table_name: str | None = None,
) -> dict[str, str]:
    """Per-vehicleId GSI Query on vehicleId-index; returns {vehicleId: fleetId}
    for found vehicleIds only.

    Despite the function's name (kept for backward compatibility — every call
    site across simulation_lambda.py, commands_lambda.py, and the OEM1 admin
    handlers references it positionally), the values this function looks up
    are `vehicleId`s, NOT VINs. `cms-{stage}-storage-fleet-enrollment`'s
    `vehicleId-index` GSI is keyed on the table's `vehicleId` attribute, and
    that table carries no `vin` attribute at all — confirmed empty via
    `attribute_exists(vin)` scan on staging.

    Corrected 2026-09-18 per
    issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/ — for the
    ~21 of 69 staging vehicles where `vehicleId != vin` (tracked in
    issues/2026-09-05-vehicleid-diverges-from-vin/), every caller that first
    resolved a vehicleId to a VIN and THEN called this function was silently
    failing every fleet-membership check for those vehicles, because the real
    VIN never matches the vehicleId-keyed index. Every caller was fixed to
    pass the vehicleId it already had, skipping the now-unnecessary VIN
    resolution step entirely. `platform-admin` callers were never affected —
    every `_authorize_per_vin` short-circuits on `is_admin` before reaching
    this function.

    Domain invariant: one fleet per vehicleId. Uses eventual consistency (GSI
    constraint). See spec § 2 (2026-06-09-cms-fleet-manager-cognito-role).
    """
    client = ddb_client or _get_ddb()
    tbl = table_name or os.environ["FLEET_ENROLLMENT_TABLE_NAME"]
    out = {}
    for vehicle_id in vehicle_ids:
        resp = client.query(
            TableName=tbl,
            IndexName="vehicleId-index",
            KeyConditionExpression="vehicleId = :v",
            ExpressionAttributeValues={":v": {"S": vehicle_id}},
            Limit=1,
        )
        if resp.get("Items"):
            out[vehicle_id] = resp["Items"][0]["fleetId"]["S"]
    return out


def parse_fleet_ids(claims: dict) -> set[str]:
    """Return set of fleetIds from comma-separated custom:fleetIds claim; empty set if absent/blank."""
    raw = claims.get("custom:fleetIds", "")
    if not raw:
        return set()
    return {f.strip() for f in raw.split(",") if f.strip()}


def classify_driver_self(
    claims: dict,
    driver_self_enabled: bool = True,
) -> Tuple[bool, str]:
    """Return (is_driver_self, driver_self_id).

    Claim-based classification (NOT pool-id based): a caller is a "driver-self"
    principal when the guard is enabled, the token carries a ``custom:driverId``,
    and the token is NOT a member of any operator group.  This mirrors the
    semantics of ``_classify_driver_self`` in ``main_api/index.py:1123-1150``.

    Key difference from the main_api version: the guard-enabled flag is passed
    **explicitly** rather than read from an env-var.  Env-var wiring is per-Lambda
    and is not the shared lib's concern — each Lambda wraps this call and supplies
    the flag from its own ``DRIVER_SELF_GUARD_ENABLED`` env read.

    Safety properties (inherited verbatim from main_api docstring):
      - ``custom:driverId`` is immutable in the pool (Mutable:false), so a driver
        cannot spoof another driver's id to defeat the self-scope.
      - operator group membership is Cognito-managed (not user-settable), so a
        driver cannot escape the guard by claiming a group.
      - operators are never classified as driver-self (they hold a group, and/or
        carry no ``custom:driverId``), so they keep full admin.
      - a no-group token WITHOUT a ``custom:driverId`` (e.g. a demo/service
        account) is unaffected.

    Args:
        claims: Cognito authorizer claims dict from the API Gateway event.
        driver_self_enabled: Whether the driver-self guard is active.  Defaults
            to ``True`` so the shared lib is fail-safe; callers that want the
            legacy permissive behaviour must explicitly pass ``False``.

    Returns:
        ``(True, driver_id)`` when the caller is classified as driver-self.
        ``(False, '')`` in all other cases.
    """
    if not driver_self_enabled:
        return False, ""
    driver_id = (claims.get("custom:driverId") or "").strip()
    if not driver_id:
        return False, ""
    groups = [g.strip() for g in (claims.get("cognito:groups") or "").split(",") if g.strip()]
    if any(g in _OPERATOR_GROUPS for g in groups):
        return False, ""
    return True, driver_id
