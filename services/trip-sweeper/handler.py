"""
trip-sweeper — scheduled Lambda that auto-closes stuck ACTIVE trips.

Background (2026-05-04):
    The Flink TripProcessor closes trips when it sees an ignition-off
    telemetry frame OR a 30-minute timeout fires. In the real world
    those paths sometimes fail:

      1. Simulator / FWE agent dies without emitting ignition-off
      2. Flink job restarts (deploy, failure, scaling) mid-trip, losing
         the in-memory activeTrips HashMap that gates the timeout path
      3. Telemetry stream stops for a vehicle without any indicator

    In any of those cases the trip stays `status=ACTIVE` forever, which
    clutters the UI, confuses drivers, and makes downstream analytics
    (aggregated mileage, duration, driver score) incorrect.

    TripProcessor fixes #1 and #3 (deployed alongside this Lambda) add
    DDB-fallback for the timeout path and stale-trip detection on new
    ignition-on. Both require fresh telemetry to kick in. This sweeper
    is the belt-and-suspenders third layer: runs on a schedule, scans
    for ACTIVE trips with no telemetry in >2h, closes them.

What it does
------------
    - Scans cms-<stage>-storage-trips for status=ACTIVE.
    - For each row whose (now - lastUpdated) exceeds STUCK_THRESHOLD_MS,
      update-item status=COMPLETED + endTime + durationMs + audit fields.
    - Emits CloudWatch metric `TripsClosed` under namespace `CMS/TripSweeper`.
    - Returns a small summary dict for EventBridge visibility.

Environment variables
---------------------
    TRIPS_TABLE_NAME       (required)  e.g. cms-prod-storage-trips
    STUCK_THRESHOLD_MS     (optional, default 7200000 = 2h)
    DRY_RUN                (optional, "true" to log-only)

IAM
---
    dynamodb:Scan   on TRIPS_TABLE_NAME
    dynamodb:UpdateItem on TRIPS_TABLE_NAME
    cloudwatch:PutMetricData (best-effort; failure doesn't abort)

Invocation
----------
    Scheduled hourly via EventBridge rule cms-trip-sweeper-hourly.
    Can be invoked manually for smoke tests; event payload is ignored.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr

REGION = os.environ.get("AWS_REGION", "us-east-1")
TRIPS_TABLE = os.environ["TRIPS_TABLE_NAME"]
STUCK_THRESHOLD_MS = int(os.environ.get("STUCK_THRESHOLD_MS", str(2 * 60 * 60 * 1000)))
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

_ddb = boto3.resource("dynamodb", region_name=REGION)
_cw = boto3.client("cloudwatch", region_name=REGION)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _scan_active_trips() -> list[dict[str, Any]]:
    """Scan the trips table for every row where status=ACTIVE.

    We intentionally scan (not query) because the trips table's primary
    key is tripId (HASH only) and there's no GSI on status. The ACTIVE
    population is tiny in steady state (zero, hopefully) so scan cost
    is trivial. A persistent high count here is itself a useful signal
    that the upstream Flink fixes aren't covering something.
    """
    table = _ddb.Table(TRIPS_TABLE)
    items: list[dict[str, Any]] = []
    kwargs = {
        "FilterExpression": Attr("status").eq("ACTIVE"),
        "ProjectionExpression": "tripId, vehicleId, startTime, lastUpdated",
    }
    resp = table.scan(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
    return items


def _close_trip(trip: dict[str, Any], now_ms: int, threshold_ms: int) -> dict[str, Any]:
    """Close a single stuck trip. Returns a summary entry for the caller."""
    tid = trip.get("tripId")
    start = int(trip.get("startTime", 0))
    last_upd = int(trip.get("lastUpdated", start))
    age_since_upd_ms = now_ms - last_upd

    # End time = lastUpdated, since that's the last time telemetry
    # actually moved for this trip — any later "end time" would be
    # fabricated. Duration uses the same anchor so totals are
    # self-consistent.
    end_time = last_upd
    duration = max(0, end_time - start)

    summary = {
        "tripId": tid,
        "vehicleId": trip.get("vehicleId"),
        "startTime": start,
        "lastUpdated": last_upd,
        "ageSinceLastUpdateMs": age_since_upd_ms,
        "durationMs": duration,
        "action": "dry-run" if DRY_RUN else "closed",
    }
    if DRY_RUN:
        return summary

    table = _ddb.Table(TRIPS_TABLE)
    table.update_item(
        Key={"tripId": tid},
        UpdateExpression=(
            "SET #s = :c, endTime = :e, completedAt = :n, durationMs = :d, "
            "closedBy = :cb, closedReason = :cr, closedAt = :n"
        ),
        # Status is a reserved word.
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":c": "COMPLETED",
            ":e": end_time,
            ":n": now_ms,
            ":d": duration,
            ":cb": "trip-sweeper",
            ":cr": (
                f"stuck ACTIVE with no telemetry for "
                f"{age_since_upd_ms // 60000} min "
                f"(threshold {threshold_ms // 60000} min); "
                f"auto-closed by trip-sweeper Lambda"
            ),
        },
    )
    return summary


def _emit_metric(value: int) -> None:
    try:
        _cw.put_metric_data(
            Namespace="CMS/TripSweeper",
            MetricData=[
                {
                    "MetricName": "TripsClosed",
                    "Value": value,
                    "Unit": "Count",
                }
            ],
        )
    except Exception as e:  # noqa: BLE001
        print(f"WARN: failed to emit TripsClosed metric: {e}")


def handler(event, context):  # noqa: ARG001  — EventBridge event unused
    now_ms = _now_ms()
    active = _scan_active_trips()

    # Partition into stuck vs. still-fresh. Definition of "stuck" is
    # "lastUpdated older than STUCK_THRESHOLD_MS". We lean conservative
    # (2h default) because any shorter threshold risks closing trips
    # that are legitimately on a long drive with sparse telemetry.
    stuck = []
    still_active = []
    for t in active:
        last_upd = int(t.get("lastUpdated", t.get("startTime", 0)))
        if (now_ms - last_upd) > STUCK_THRESHOLD_MS:
            stuck.append(t)
        else:
            still_active.append(t)

    closed = []
    for t in stuck:
        try:
            closed.append(_close_trip(t, now_ms, STUCK_THRESHOLD_MS))
        except Exception as e:  # noqa: BLE001
            print(f"ERROR closing {t.get('tripId')}: {e}")

    _emit_metric(len(closed))

    summary = {
        "scanned": len(active),
        "stillActive": len(still_active),
        "closed": len(closed),
        "dryRun": DRY_RUN,
        "thresholdMinutes": STUCK_THRESHOLD_MS // 60000,
        "closedTrips": closed,
    }
    # Log in a single JSON line so CloudWatch Logs Insights can query it.
    print(json.dumps({"tripSweeperSummary": summary}, default=str))
    return summary
