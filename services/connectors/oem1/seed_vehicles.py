#!/usr/bin/env python3
"""Seed cms-{stage}-storage-vehicles and cms-{stage}-storage-fleet-enrollment from OEM1 Enrollment Status API.

Reads OEM1_FEED_HOST env var (default oem1-feed.example.local).
Calls /enrollment/v2/status/latest (paginated, statuses=COMPLETED),
enriches via /selfserve/v1/vehicleData?categories=modelInfo,
runs readiness diagnostic via /selfserve/v1/vehicleState,
then writes idempotently to DynamoDB.

NOTE: Live execution requires Phase C user prerequisites (OEM1 IAM grants,
real vehicles, secret deposited in Secrets Manager). If OEM1_FEED_HOST is
unreachable, the script exits cleanly with an instructional message.

Usage:
    AWS_PROFILE=default DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \\
        python3 seed_vehicles.py
"""
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import boto3
import requests
from requests.exceptions import ConnectionError as RequestsConnectionError

from token_supplier import TokenSupplier

# ── Configuration ──────────────────────────────────────────────────────────
PROFILE = os.environ.get("AWS_PROFILE", "default")
STAGE = os.environ.get("DEPLOYMENT_STAGE", "staging")
REGION = os.environ.get("AWS_REGION", "us-west-2")
OEM1_FEED_HOST = os.environ.get("OEM1_FEED_HOST", "oem1-feed.example.local")

# Application-Id header per the customer-issued Postman collection; required by the upstream API gateway. Override via OEM1_APPLICATION_ID env if the customer rotates the value.
_APPLICATION_ID = os.environ.get(
    "OEM1_APPLICATION_ID",
    "DFC7BB0A-649D-4873-9368-00AEF0E7024D",
)

_PAGE_SIZE = 100

VEHICLES_TABLE = f"cms-{STAGE}-storage-vehicles"
FLEET_ENROLLMENT_TABLE = f"cms-{STAGE}-storage-fleet-enrollment"
DEFAULT_FLEET_ID = "oem1-staging-fleet"

ENROLLMENT_URL = f"https://{OEM1_FEED_HOST}/enrollment/v2/status/latest"
VEHICLE_DATA_URL = f"https://{OEM1_FEED_HOST}/selfserve/v1/vehicleData"
VEHICLE_STATE_URL = f"https://{OEM1_FEED_HOST}/selfserve/v1/vehicleState"

# ── Token supplier (lazy singleton) ────────────────────────────────────────
_TOKEN_SUPPLIER: TokenSupplier | None = None


def _get_token_supplier() -> TokenSupplier:
    global _TOKEN_SUPPLIER
    if _TOKEN_SUPPLIER is None:
        secret_name = os.environ.get(
            "OEM1_CREDENTIALS_SECRET",
            f"cms-{STAGE}-connector-oem1-credentials",
        )
        _TOKEN_SUPPLIER = TokenSupplier(secret_name=secret_name)
    return _TOKEN_SUPPLIER


def _request_with_retry(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    """Request with one-shot 401 retry using TokenSupplier."""
    caller_headers = kwargs.pop("headers", {})
    headers = {"Authorization": f"Bearer {_get_token_supplier().get_token()}"}
    headers.update(caller_headers)
    resp = session.request(method, url, headers=headers, **kwargs)
    if resp.status_code == 401:
        _get_token_supplier().handle_401()
        headers["Authorization"] = f"Bearer {_get_token_supplier().get_token()}"
        resp = session.request(method, url, headers=headers, **kwargs)
    return resp


def _ddb():
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    return session.client("dynamodb")


def _fetch_enrolled_vehicles(session: requests.Session) -> list[dict]:
    """Paginate /enrollment/v2/status/latest (POST) and return list of vehicle records."""
    vehicles = []
    page_number = 1
    while True:
        body = {
            "statuses": ["COMPLETED"],
            "page_size": _PAGE_SIZE,
            "page_number": page_number,
            "order_by": "DESC",
        }
        resp = _request_with_retry(
            session,
            "POST",
            ENROLLMENT_URL,
            json=body,
            headers={"Application-Id": _APPLICATION_ID},
            timeout=30,
        )
        # TC1105: no enrollment records found — treat as empty (accounts-not-yet-enrolled state)
        if not resp.ok:
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            if isinstance(data, dict):
                errors = data.get("errors", [])
                if any(str(e.get("code")) == "1105" for e in errors if isinstance(e, dict)):
                    break
            resp.raise_for_status()
        data = resp.json() if resp.ok else {}
        # Defensive: accept array under any of these top-level keys, or direct array
        page_arr = None
        if isinstance(data, list):
            page_arr = data
        else:
            for cand in ("data", "vehicles", "enrollments", "items", "results"):
                candidate = data.get(cand) if isinstance(data, dict) else None
                if isinstance(candidate, list):
                    page_arr = candidate
                    break
        if page_arr is None:
            page_arr = []
        vehicles.extend(page_arr)
        if len(page_arr) < _PAGE_SIZE:
            break
        page_number += 1
    return vehicles


def _enrich_vehicle(session: requests.Session, vehicle_id: str) -> dict:
    """Fetch model info for a single vehicle. Returns {} on error."""
    try:
        resp = _request_with_retry(
            session,
            "GET",
            VEHICLE_DATA_URL,
            params={"vehicleId": vehicle_id, "categories": "modelInfo"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def _readiness_diagnostic(session: requests.Session, vehicle_id: str) -> dict:
    """Fetch vehicleState readiness diagnostic. Returns {} on error."""
    try:
        resp = _request_with_retry(
            session,
            "GET",
            VEHICLE_STATE_URL,
            params={"vehicleId": vehicle_id},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def _write_vehicle(
    ddb_client,
    vehicle_id: str,
    model_info: dict,
    now: str,
    sku: str | None = None,
    request_id: str | None = None,
) -> str:
    """Write to cms-{stage}-storage-vehicles with conditional put (attribute_not_exists(vehicleId)).

    Phase 3 (spec § 1.2) — when sku + request_id are supplied, populates the
    8 M-MGR fields:
      oem1_active_sku, oem1_request_id, oem1_enrollment_status='IN_PROGRESS',
      and the remaining 5 status fields as NULL (poller fills those later).
    """
    item = {
        "vehicleId": {"S": vehicle_id},
        "oem_source": {"S": "oem1"},
        "last_seen_at": {"S": now},
        "enrolled_at": {"S": now},
        # `status: "Active"` lifecycle invariant (added 2026-06-04 per UAT
        # bug-batch). Vehicle is enrolled but not yet sending telemetry;
        # auto_register.py flips this to "Connected" on the first packet.
        # Without this field the Vehicles list Status column renders
        # "unknown" via its default StatusIndicator branch.
        "status": {"S": "Active"},
    }
    if model_info:
        vehicle_data = model_info.get("vehicleData", {})
        model = vehicle_data.get("modelInfo", {})
        if model.get("make"):
            item["make"] = {"S": model["make"]}
        if model.get("model"):
            item["model"] = {"S": model["model"]}
        if model.get("year"):
            item["year"] = {"N": str(model["year"])}
    # oem1_shard_uuid is NULL at seed time (populated by connector on first event)
    item["oem1_shard_uuid"] = {"NULL": True}

    # ── M-MGR fields (spec § 1.2) ──────────────────────────────────────────
    # Populated when --sku is supplied; absent/null otherwise so legacy callers
    # that omit --sku produce the same schema as before (no breaking change).
    if sku is not None:
        item["oem1_active_sku"] = {"S": sku}
    if request_id is not None:
        item["oem1_request_id"] = {"S": request_id}
    if sku is not None or request_id is not None:
        # enrollment_status set to IN_PROGRESS at seed time; poller updates
        # to terminal state once OEM1 confirms activation.
        item["oem1_enrollment_status"] = {"S": "IN_PROGRESS"}
        # The remaining 5 status fields are absent until the poller writes them.
        # NULL placeholders are omitted (DDB is schemaless; absence == NULL).

    try:
        ddb_client.put_item(
            TableName=VEHICLES_TABLE,
            Item=item,
            ConditionExpression="attribute_not_exists(vehicleId)",
        )
        return "inserted"
    except ddb_client.exceptions.ConditionalCheckFailedException:
        # Already exists — refresh last_seen_at AND backfill status if missing.
        # `if_not_exists` guards `Connected` (set by auto_register.py on
        # telemetry) from being downgraded back to `Active`.
        ddb_client.update_item(
            TableName=VEHICLES_TABLE,
            Key={"vehicleId": {"S": vehicle_id}},
            UpdateExpression="SET last_seen_at = :t, #s = if_not_exists(#s, :a)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":t": {"S": now},
                ":a": {"S": "Active"},
            },
        )
        return "updated"


def _write_fleet_enrollment(ddb_client, vehicle_id: str, now: str) -> str:
    """Write to cms-{stage}-storage-fleet-enrollment with conditional put on (PK, SK)."""
    try:
        ddb_client.put_item(
            TableName=FLEET_ENROLLMENT_TABLE,
            Item={
                "PK": {"S": f"FLEET#{DEFAULT_FLEET_ID}"},
                "SK": {"S": f"VEHICLE#{vehicle_id}"},
                "fleetId": {"S": DEFAULT_FLEET_ID},
                "vehicleId": {"S": vehicle_id},
                "enrolledAt": {"S": now},
            },
            ConditionExpression="attribute_not_exists(PK)",
        )
        return "enrolled"
    except ddb_client.exceptions.ConditionalCheckFailedException:
        return "already_enrolled"


def main(sku: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    # Fresh request_id per CLI invocation — used as the M-MGR oem1_request_id for
    # all vehicles written in this run. No re-use across invocations (spec T3.4).
    request_id = str(uuid.uuid4()) if sku is not None else None

    print(f"Seeding OEM1 vehicles → {VEHICLES_TABLE}, {FLEET_ENROLLMENT_TABLE}")
    print(f"OEM1 endpoint: {OEM1_FEED_HOST}")
    if sku:
        print(f"SKU: {sku}  request_id: {request_id}")

    try:
        http = requests.Session()
        vehicles = _fetch_enrolled_vehicles(http)
    except RequestsConnectionError as exc:
        print(
            f"\n⚠️  OEM1_FEED_HOST ({OEM1_FEED_HOST}) is unreachable: {exc}\n"
            "Live execution of seed-vehicles-oem1 requires Phase C user prerequisites:\n"
            "  1. OEM1 IAM grants on customer enrollment/vehicleData/vehicleState endpoints\n"
            "  2. Secret deposited in cms-staging-connector-oem1-credentials\n"
            "  3. Real OEM1 vehicles enrolled on the staging flow\n"
            "Set OEM1_FEED_HOST to the actual endpoint and re-run when prereqs are met.\n"
        )
        sys.exit(0)

    if not vehicles:
        print("No COMPLETED-enrolled vehicles found. Exiting.")
        sys.exit(0)

    ddb = _ddb()
    inserted = updated = enrolled = already_enrolled = 0

    for v in vehicles:
        vid = v.get("vehicleId") or v.get("vin")
        if not vid:
            continue
        model_info = _enrich_vehicle(http, vid)
        _readiness_diagnostic(http, vid)  # diagnostic run; result logged but not stored at seed time
        v_status = _write_vehicle(ddb, vid, model_info, now, sku=sku, request_id=request_id)
        e_status = _write_fleet_enrollment(ddb, vid, now)
        if v_status == "inserted":
            inserted += 1
        else:
            updated += 1
        if e_status == "enrolled":
            enrolled += 1
        else:
            already_enrolled += 1

    print(
        f"✅ Done. vehicles: {inserted} inserted, {updated} updated. "
        f"fleet-enrollment: {enrolled} enrolled, {already_enrolled} already enrolled."
    )


def unseed() -> None:
    """
    Reverse `main()` — delete all OEM1 vehicle rows from cms-{stage}-storage-vehicles
    and their corresponding cms-{stage}-storage-fleet-enrollment entries.

    Safe to run if telemetry never materialises and the operator wants to
    reset staging before escalating to OEM1. Idempotent: re-running on an
    empty fleet is a no-op.

    Authored 2026-06-04 per UAT bug-batch as the documented backout for
    bulk-seed runs (see `issues/2026-06-04-oem1-vehicle-missing-enrichment-on-list/`).
    """
    print(f"Unseeding OEM1 vehicles from {VEHICLES_TABLE} + {FLEET_ENROLLMENT_TABLE}")
    ddb = _ddb()

    # 1) Find all OEM1 vehicleIds. Scan with filter — staging table is small.
    vehicle_ids: list[str] = []
    paginator = ddb.get_paginator("scan")
    for page in paginator.paginate(
        TableName=VEHICLES_TABLE,
        FilterExpression="oem_source = :s",
        ExpressionAttributeValues={":s": {"S": "oem1"}},
        ProjectionExpression="vehicleId",
    ):
        for item in page.get("Items", []):
            vid = item.get("vehicleId", {}).get("S")
            if vid:
                vehicle_ids.append(vid)

    if not vehicle_ids:
        print("✅ No OEM1 vehicles in DDB. Nothing to unseed.")
        return

    print(f"  Found {len(vehicle_ids)} OEM1 vehicles to delete")

    # 2) Delete from vehicles table
    deleted_vehicles = 0
    for vid in vehicle_ids:
        ddb.delete_item(
            TableName=VEHICLES_TABLE,
            Key={"vehicleId": {"S": vid}},
        )
        deleted_vehicles += 1

    # 3) Delete corresponding fleet-enrollment rows. Scan + filter on
    #    SK pattern; deletes whichever fleet the row landed in (admin
    #    add-vehicle path may have used a non-default fleet).
    enrollment_keys: list[dict] = []
    sk_set = {f"VEHICLE#{vid}" for vid in vehicle_ids}
    paginator = ddb.get_paginator("scan")
    for page in paginator.paginate(
        TableName=FLEET_ENROLLMENT_TABLE,
        ProjectionExpression="PK, SK",
    ):
        for item in page.get("Items", []):
            sk = item.get("SK", {}).get("S")
            pk = item.get("PK", {}).get("S")
            if sk and sk in sk_set and pk:
                enrollment_keys.append({"PK": {"S": pk}, "SK": {"S": sk}})

    deleted_enrollments = 0
    for key in enrollment_keys:
        ddb.delete_item(TableName=FLEET_ENROLLMENT_TABLE, Key=key)
        deleted_enrollments += 1

    print(
        f"✅ Done. vehicles: {deleted_vehicles} deleted. "
        f"fleet-enrollment: {deleted_enrollments} deleted."
    )


def _build_parser() -> "argparse.ArgumentParser":
    import argparse
    p = argparse.ArgumentParser(
        description=(
            "Seed cms-{stage}-storage-vehicles + cms-{stage}-storage-fleet-enrollment from "
            "OEM1 Enrollment Status API. Live execution requires Phase C "
            "user prerequisites (see services/connectors/README.md)."
        ),
    )
    p.add_argument(
        "--unseed",
        action="store_true",
        help=(
            "Backout: delete all OEM1 rows from vehicles + fleet-enrollment "
            "tables. Use when telemetry never materialises and you need to "
            "reset staging before escalating to OEM1."
        ),
    )
    p.add_argument(
        "--sku",
        default=None,
        metavar="SKU",
        help=(
            "OEM1 product SKU to record as oem1_active_sku on each seeded "
            "vehicle row. When supplied, also sets oem1_enrollment_status='IN_PROGRESS' "
            "and oem1_request_id to a fresh UUID for this run. "
            "Omit to seed without M-MGR fields (legacy behaviour)."
        ),
    )
    p.add_argument(
        "--driver-id",
        default=None,
        metavar="DRIVER_ID",
        help=(
            "Driver ID to associate with seeded vehicles (reserved for future use; "
            "accepted but not yet persisted to the vehicle row)."
        ),
    )
    return p


if __name__ == "__main__":
    args = _build_parser().parse_args()  # exits 0 on --help, raises SystemExit on bad args
    if args.unseed:
        unseed()
    else:
        main(sku=args.sku)
