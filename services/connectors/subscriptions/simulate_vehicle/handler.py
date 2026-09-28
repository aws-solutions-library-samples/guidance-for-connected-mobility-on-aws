# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""CS simulation entry point — path derived from vehicle dataSource, never chosen.

Spec: `.kiro/specs/2026-09-14-cs-portal-data-model-backend`, Group 6 (T6.1 + T6.2).
Authorization added: Fix Group 2 (FG2.1) — `.kiro/specs/2026-09-14-cs-portal-data-model-backend`.

## Authorization — operator-only (FG2.1)

Both handlers require the caller to be a member of the `connected-services` Cognito
group before any DynamoDB operation is issued.  A caller with no claims, no
`cognito:groups`, an empty group list, or a non-operator group gets 403.

This follows the sibling idiom in `admin_mark_available/handler.py`
(`_require_operator`, `_Unauthorized`, `403 {"error": "Forbidden"}`).

The vehicle picker IS filtered by `producer` as of Group 8 (2026-09-20) — to
`meridian` only, on both the list and the start path.  This reverses the earlier
T6.1 position that the picker should be unfiltered; see
"## Vehicle picker — Meridian-only" below for why, and do NOT restore an
unfiltered picker without first writing a real per-producer simulation path.
Two controls, both required, neither substituting for the other: *who may
simulate* (the operator group check) and *which vehicles can be simulated* (the
producer gate).  Do NOT weaken the operator group check.  `sold_to` is still not
a filter here.

## Route wiring — NOT YET WIRED (FG2.2)

    GET  /simulate/vehicles    and    POST /simulate/start/{vid}

have NO route entry and NO IAM grant in `subscriptions_stack.py` as of this
writing.  The `subscriptionsClient.ts::listVehiclesForSimulation` call already
exists in `SimulateVehicleView`, so adding the route is a single commit away.

**Any commit that wires these routes MUST:**
  1. Preserve `_require_operator` as the first call in both handlers (before
     any DynamoDB operation).  Removing or bypassing this check re-opens the
     cross-customer VIN enumeration defect fixed in FG2.1.
  2. Add an IAM grant scoped narrowly to the vehicles table ARN (Scan for
     list_vehicles_handler; GetItem for simulate_start_handler).
  3. Add an integration test or smoke test confirming a non-operator caller
     receives 403 from the live endpoint.

## Design — path is a consequence, not an input

The two axes that were conflated (`producer` = who made the vehicle,
`dataSource` = how CMS obtains data) are now explicit.  Simulation routing
therefore takes NO path/transport parameter and NO override:

    vehicle-telemetry  →  FWE agent + collection scheme → MQTT → Flink
    cloud-telemetry    →  producer feed → ingest API

The operator picks a vehicle; this resolver reads that vehicle's `dataSource`
and emits the dispatch key.  An override writes data through a transport the
vehicle does NOT use — the table would say cloud while the data arrived
onboard, and nothing downstream can detect the disagreement.  To make a
vehicle behave onboard you change its `dataSource` (a real change to how it is
fed), not an ephemeral simulation flag.

A vehicle with no `dataSource` is REJECTED with an explicit reason, never
defaulted to either path.  The default `item.get('dataSource', 'vehicle-telemetry')`
idiom from the campaign-gate code is the defect this module exists to fix.

## Vehicle picker — Meridian-only (Group 8, 2026-09-20; REVERSES T6.1)

The picker lists only vehicles with `producer == meridian`, and
`resolve_simulation_path` independently refuses to start a simulation for any
other producer.

**T6.1 originally made this picker deliberately unfiltered**, reasoning that a
Meridian data product may legitimately carry data about another OEM's vehicle,
and that the `producer == meridian` filter governing the inventory/subscription
surface (T0.5, vehicles_available/handler.py) therefore did not apply here. That
argument is retained rather than deleted because it is not wrong in general — it
was scoped wrong for this surface.

What it missed: **there is no oem1 or tesla simulation path to exercise.**
Simulating a `cloud-telemetry` vehicle publishes over MQTT basic-ingest to the CS
product rule; it does not drive that vehicle's real ingest route (for oem1, the
connector's Kafka → transform-manifest → Flink path). So listing 48 oem1 and 6
tesla vehicles advertised a capability that does not exist, and a start against
one produced Meridian-shaped telemetry attributed to a vehicle Meridian does not
feed. User decision, 2026-09-20: *"CS is a meridian OEM owned portal, not a tesla
portal."*

Restoring an unfiltered picker requires writing a real per-producer simulation
path first, not just deleting the filter.

## Identity-write guard (T6.2)

No simulation code path in this module writes `producer`, `sold_to`, or
`oem_source` on the subject vehicle.  Producing Meridian-product data *about*
an OEM1 vehicle is legitimate; re-attributing that vehicle is not.  This is
the fabrication failure mode and it is invisible afterwards — the row simply
reads as Meridian-owned with nothing to compare against.

The guard is executable (not comment-level):
  1. This module contains zero writes of those three fields to the vehicles
     table (source-structural — enforced by test_simulation_never_writes_vehicle_identity
     in tests/test_handler.py, which walks this file and confirms no write sites).
  2. Test T6.2 asserts the post-simulation vehicle record is byte-identical on
     those three attributes.

## Endpoints (NOT YET WIRED — see "Route wiring" above)

    GET  /simulate/vehicles        → list_vehicles_handler
    POST /simulate/start/{vid}     → simulate_start_handler

## Env vars

    VEHICLES_TABLE_NAME
    DEPLOYMENT_STAGE, AWS_DEFAULT_REGION

## IAM (to be added when route is wired — see "Route wiring" above)

    dynamodb:Scan            on the vehicles table (list_vehicles_handler)
    dynamodb:GetItem         on the vehicles table (simulate_start_handler)
    logs:CreateLogGroup, logs:CreateLogStream, logs:PutLogEvents
"""
from __future__ import annotations

import json
import logging
import os
import re
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_STAGE = os.environ.get("DEPLOYMENT_STAGE", "staging")

# ---------------------------------------------------------------------------
# Authorization (FG2.1)
# ---------------------------------------------------------------------------
#
# Simulation is a staff-only action (spec § "Simulation falls out of this":
# "the operator picks one").  The same `connected-services` Cognito group that
# gates the sibling admin_mark_available/handler.py is the required group here.
#
# Pattern mirrors admin_mark_available/handler.py exactly — `_require_operator`
# at the top of each handler, `403 {"error": "Forbidden"}` on failure, no
# fall-through to any other group.  A groupless caller must be denied, not
# treated as an admin (portfolio "Fail-open authz" invariant).

#: The operator group name — staff, not subscribers (D6 pattern).
#: Same value as `admin_mark_available/handler.py::_OPERATOR_GROUP`.
_OPERATOR_GROUP = "connected-services"


class _Unauthorized(Exception):
    """Caller is not a member of the operator group.

    Deliberately does NOT fall back to any other group. Per the portfolio's
    `Fail-open authz` finding, a groupless caller must be denied rather than
    treated as an admin.
    """


def _parse_groups(claims: dict) -> list[str]:
    """Normalise `cognito:groups` from either a list or a CSV string.

    API Gateway may render the claim as a JSON array (``["a","b"]``) or as a
    comma-separated string (``"a,b"``).  Both forms are handled here.
    """
    groups_raw = claims.get("cognito:groups", "")
    if isinstance(groups_raw, list):
        return [str(g).strip() for g in groups_raw if str(g).strip()]
    groups_str = str(groups_raw).strip()
    if groups_str.startswith("[") and groups_str.endswith("]"):
        groups_str = groups_str[1:-1]
    return [g.strip() for g in groups_str.split(",") if g.strip()] if groups_str else []


def _require_operator(event: dict) -> str:
    """Raise ``_Unauthorized`` unless the caller holds the operator group.

    Returns the caller's ``sub`` claim on success, for audit logging.

    Fails closed on: no claims, no ``cognito:groups``, empty group list, any
    group list that does not contain ``_OPERATOR_GROUP``, or a missing ``sub``.
    """
    claims = _claims(event)
    groups = _parse_groups(claims)
    if _OPERATOR_GROUP not in groups:
        raise _Unauthorized(f"'{_OPERATOR_GROUP}' group required")
    actor = str(claims.get("sub", "") or "").strip()
    if not actor:
        raise _Unauthorized("token carries no 'sub' claim")
    return actor


# ---------------------------------------------------------------------------
# dataSource dispatch table
# ---------------------------------------------------------------------------
#
# Two recognised values; anything else is an unknown transport.  A row with
# no `dataSource` is neither — it is REJECTED with an explicit reason
# (see spec § "Simulation falls out of this").
#
# NOTE: intentionally NOT a default here.  `item.get('dataSource', 'vehicle-telemetry')`
# is the defect this module replaces — a missing value resolves silently to
# onboard when the vehicle has never been configured for it.

_KNOWN_DATA_SOURCES = frozenset({"vehicle-telemetry", "cloud-telemetry"})

# ---------------------------------------------------------------------------
# Simulatable producer — CS is Meridian's portal (Group 8, reverses T6.1)
# ---------------------------------------------------------------------------
#
# Exact match, and deliberately the SAME definition the inventory surface
# already uses (`_MERIDIAN_PRODUCER` in
# services/connectors/subscriptions/vehicles_available/handler.py:139).  Two
# definitions of "a Meridian vehicle" in one service is how they drift apart.
#
# Why a producer gate exists here at all, given T6.1 argued against one: there
# is no OEM1 or Tesla simulation path to exercise.  Simulating a
# `cloud-telemetry` vehicle publishes over MQTT basic-ingest to the CS product
# rule; it does NOT drive that vehicle's real ingest path (for oem1, the
# connector's Kafka → transform-manifest → Flink route).  So offering an oem1
# or tesla vehicle in the picker advertised a capability that does not exist,
# and produced Meridian-shaped data attributed to a vehicle Meridian does not
# feed.  User decision 2026-09-20: "CS is a meridian OEM owned portal, not a
# tesla portal."
#
# This does NOT relax T6.2's identity-write guard — no code path here writes
# `producer`, `sold_to` or `oem_source`, and narrowing the readable set does
# not change that.
_SIMULATABLE_PRODUCER = "meridian"

_DISPATCH_DESCRIPTION = {
    "vehicle-telemetry": (
        "FWE agent + collection scheme → MQTT → Flink"
    ),
    "cloud-telemetry": (
        "MQTT basic-ingest → CS product rule → MSK"
    ),
}

# `_DISPATCH_DESCRIPTION` describes what a *simulated* start actually does, and is
# returned to the caller under the `dispatch` key on success.  It is NOT the same
# statement as the module docstring's account of how a real `cloud-telemetry`
# vehicle is fed in production ("producer feed → ingest API") — review cycle 1 (W2)
# caught this field claiming the production feed while the dispatch publishes to
# `$aws/rules/cms_{stage}_cs_product_meridian_ev_rule/{vehicleId}`
# (`realtime_telemetry_simulator.py:2327`, `:2360`).  Keep the two apart: this
# field answers "what did this route just start", not "how does this vehicle
# normally receive data".

# ---------------------------------------------------------------------------
# dataSource → the simulation Lambda's two config axes (T6.1)
# ---------------------------------------------------------------------------
#
# The simulation Lambda's `/start` takes TWO independent knobs, and conflating
# them is the trap this table exists to avoid:
#
#   `mode`      — HOW the data is produced.  `_start()` maps it as
#                 `"can" if config.get("mode") == "fwe" else "mqtt_direct"`
#                 (`simulation_lambda.py:1445`), and `mode == "fwe"` additionally
#                 appends `--skip-mqtt --commands-mqtt`.
#   `rule_name` — WHERE the produced data is routed.  Validated against
#                 `_ALLOWED_RULE_SUFFIXES` (`simulation_lambda.py:47-50`), which
#                 holds exactly two entries: the CMS-native rule and the
#                 connected-services product rule.  An unrecognised value falls
#                 back to the CMS-native default rather than erroring, so a typo
#                 here would silently route CS data onto the CMS-native topic.
#
# The mapping below is the only one consistent with this module's stated design
# principle ("path is a consequence, not an input"): each dataSource is dispatched
# down the transport the vehicle *actually* uses.  `vehicle-telemetry` is fed
# onboard and lands on the CMS-native rule; `cloud-telemetry` is not fed onboard,
# so it is published directly and lands on the CS product rule.
#
# On the `mode` axis this AGREES with `simulation_lambda.py:2627`
# ("cloud-telemetry is MQTT-Direct; vehicle-telemetry is FWE").  That comment sits
# in the UDS DTC-injection route, which is FWE-only and rejects `cloud-telemetry`
# outright, and it says nothing about `rule_name` — so there is no contradiction
# between the two files to reconcile, only two axes to keep separate.  An earlier
# draft of this comment claimed `_DISPATCH_DESCRIPTION` and the config emitted here
# "describe the same journey"; that was false for the `cloud-telemetry` arm and is
# corrected above (review cycle 1, W2).
#
# Do NOT collapse this to a single value.  `dataSource` has two recognised values
# and there are two axes; a one-axis derivation would have to pick a default for
# the other, which is the class of defect `_KNOWN_DATA_SOURCES` exists to refuse.

_SIMULATION_DISPATCH = {
    "vehicle-telemetry": {"mode": "fwe", "rule_suffix": "iot_msk_rule"},
    "cloud-telemetry": {"mode": "mqtt_direct", "rule_suffix": "cs_product_meridian_ev_rule"},
}

# The simulation Lambda's own route for a start request.  Its dispatcher matches
# on `path.endswith("/start")` (`simulation_lambda.py:390`) after an earlier arm
# has already claimed `/agent/start`, so this value must end in `/start` and must
# NOT end in `/agent/start`.
_SIMULATION_START_PATH = "/api/simulation/start"

# Trip-parameter allowlist (spec `2026-09-19-cs-trip-simulator-parity`, T2.1).
# Mirrors `modules/cms_ui/source/frontend/src/components/vehicles/vehicle-
# detail/TripSimulatorModal.tsx`'s own `CITIES` array — kept as a duplicated
# constant rather than a cross-language import, with this comment as the
# cross-reference so the two lists are found together on a future edit. An
# unrecognised value is rejected with 400 BEFORE dispatch, not forwarded
# verbatim to the simulation Lambda's shell-invoked worker — same
# defense-in-depth posture as `_resolve_rule_name`'s allowlist in
# `simulation_lambda.py`.
_ALLOWED_CITIES = frozenset({
    "atlanta", "chicago", "miami", "munich", "nyc", "sf", "seattle",
})

# ---------------------------------------------------------------------------
# Readiness check — table names and the fleet the start path enforces
# ---------------------------------------------------------------------------
#
# T5.0: three prerequisites, all measured from the small side (set intersection)
# rather than per-vehicle queries.  None of these widen the authorization surface;
# they are advisory fields on a read-only list response.
#
# Table-name derivation mirrors the simulation Lambda:
#   `cms-{stage}-storage-fleet-enrollment`   — vehicleId → fleetId mapping
#   `cms-{stage}-storage-vehicle-certificates` — VIN → certificate rows
#   `cms-{stage}-campaigns`                  — telemetry campaign rows
#
# The FLEET_ENROLLMENT, VEHICLE_CERTIFICATES, and CAMPAIGNS names are the same
# env-var pattern used across the simulation Lambda and the OEM1 connectors
# (see `services/connectors/oem1/_lib/fleet_membership.py` and
# `services/simulation/lambda/simulation_lambda.py`).  This module uses
# name-derived fallbacks (same as simulation_lambda.py's `CS_ALLOWED_FLEET_ID`
# pattern) so that unit tests work without setting every env var.

_CS_ALLOWED_FLEET_ID = os.environ.get("CS_ALLOWED_FLEET_ID", "flt-meridian-range-001")
_FLEET_ENROLLMENT_TABLE = os.environ.get(
    "FLEET_ENROLLMENT_TABLE_NAME", f"cms-{_STAGE}-storage-fleet-enrollment"
)
_VEHICLE_CERTIFICATES_TABLE = os.environ.get(
    "VEHICLE_CERTIFICATES_TABLE_NAME", f"cms-{_STAGE}-storage-vehicle-certificates"
)
_CAMPAIGNS_TABLE = os.environ.get(
    "CAMPAIGNS_TABLE_NAME", f"cms-{_STAGE}-campaigns"
)


# ---------------------------------------------------------------------------
# DDB singleton
# ---------------------------------------------------------------------------

_ddb_resource = None


def _get_ddb_resource():
    global _ddb_resource
    if _ddb_resource is None:
        _ddb_resource = boto3.resource(
            "dynamodb",
            region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )
    return _ddb_resource


_lambda_client = None


def _get_lambda_client():
    """Lambda client singleton — mirrors `_get_ddb_resource` above.

    Separate from the DDB singleton so a test can stub the invoke path without
    also stubbing the vehicle read, and vice versa.
    """
    global _lambda_client
    if _lambda_client is None:
        _lambda_client = boto3.client(
            "lambda",
            region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )
    return _lambda_client


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is unset. Refusing to guess it from another variable."
        )
    return value


def _dispatch_stage() -> str:
    """The stage component of `rule_name` — fail-closed, unlike `_STAGE`.

    `_STAGE` (module level) defaults to ``"staging"`` so a log line still renders
    on a misconfigured Lambda.  That default is harmless in a log field and
    dangerous in a routing destination, which is what T6.1 promoted the stage into:

      * the callee validates `rule_name` against an allowlist built from ITS OWN
        stage (`simulation_lambda.py:1419`), and
      * silently returns the CMS-native default for anything off that list
        (`:1413`, `:1422`) — no exception, no 4xx.

    So a stage mismatch routes CS-product telemetry onto the CMS-native topic and
    nothing marks it: this handler logs the value it *sent*, not the value the
    callee resolved.  Reading it fail-closed here turns a silent misroute into a
    loud 500.  Found by review cycle 1 (W1); the commit that introduced the
    dispatch guarded `SIMULATION_FUNCTION_NAME` this way and missed this one.
    """
    return _required_env("DEPLOYMENT_STAGE")


# A callee `reason` is only forwarded on a 5xx if it looks like a machine token.
# Anything else — a sentence, an ARN, a stack fragment — is dropped.
# `\Z` not `$`: `$` also matches before a trailing newline, so `"tok\n"` would
# otherwise qualify (review cycle 2, S1).
_REASON_TOKEN = re.compile(r"^[a-z0-9_]{1,64}\Z")

# What a SUCCESSFUL callee body may contribute to the operator's response.
#
# An allowlist, not a denylist: the callee's `/start` success arms are
# `{"success", "simulation_id", "task_arn"}` plus an optional `"warning"`
# (`simulation_lambda.py:1807`, `:1896`, `:1808`, `:1900`), and a denylist naming
# `task_arn` would not cover whatever the next field is.
#
#   simulation_id — required: T6.3 polls `GET /api/simulation/status/{id}` with it
#   success       — a boolean
#   status        — a short state string when present
#   warning       — the campaign advisory, a fixed sentence naming only the VIN the
#                   caller already supplied (`simulation_lambda.py:1592-1596`)
#
# `task_arn` is deliberately absent. It is a full ECS task ARN, so it carries the
# account id and the cluster and task-definition names to a `connected-services`
# operator on every successful start.
_SUCCESS_PASSTHROUGH_KEYS = frozenset({
    "success", "simulation_id", "status", "warning",
})


def _safe_callee_body(sim_body: dict, sim_status: int) -> dict:
    """Decide how much of the callee's body may reach the caller, per status class.

    **5xx — replaced.** The callee's outer catchall is
    ``_resp(500, {"error": str(e)})`` (`simulation_lambda.py:334`, and five more
    sites), so an unhandled boto3 error arrives as a rendered ``ClientError`` —
    which carries the account id, the role ARN, the resource ARN and the action
    name.  A `reason` survives only if it matches `_REASON_TOKEN`, because `reason`
    is not a safe channel by virtue of its name.

    **4xx — passed through.** These are policy statements authored for a caller
    (``Forbidden``, ``no_telemetry_campaign``, ``Vehicle not found``) and the
    operator needs them to act.

    **Everything else, including 2xx — allowlisted.** Both success arms return
    ``task_arn``.  An earlier revision of this function sanitized errors and left
    success untouched, and its docstring claimed the 5xx path was "the one
    asymmetric path out of the module"; that was wrong, and the successful path was
    leaking a task ARN on every start (review cycle 2, W1).  Sub-400 non-2xx
    statuses land here too rather than passing through unexamined.
    """
    if sim_status >= 500:
        safe: dict = {"error": "Simulation service encountered an internal error."}
        reason = sim_body.get("reason")
        if isinstance(reason, str) and _REASON_TOKEN.match(reason):
            safe["reason"] = reason
        return safe

    if sim_status >= 400:
        return dict(sim_body)

    return {k: v for k, v in sim_body.items() if k in _SUCCESS_PASSTHROUGH_KEYS}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _api_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=_json_default),
    }


def _json_default(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def _claims(event: dict) -> dict:
    return (
        (event.get("requestContext") or {})
        .get("authorizer", {})
        .get("claims", {})
    ) or {}


# ---------------------------------------------------------------------------
# Vehicle list — deliberately unfiltered on `producer`
# ---------------------------------------------------------------------------

def _list_all_vehicles(vehicles_table) -> list[dict]:
    """Return every **Meridian** vehicle row.

    Filtered to `producer == meridian` (Group 8, reversing T6.1). CS is
    Meridian's own portal and there is no oem1/tesla simulation path, so
    offering those vehicles advertised capability that does not exist. See
    `_SIMULATABLE_PRODUCER` for the full reasoning.

    Returns `vehicleId`, `vin`, `dataSource`, `producer`, and `make`/`model`/`year`.

    `make`/`model`/`year` are projected for the PICKER LABEL (added 2026-09-20).
    `vehicleId` remains in the payload because it is the functional key — the
    start call and the fleet-membership check both key on it — but the UI no
    longer shows it, because it means nothing to an operator and on a few legacy
    rows it means something actively wrong (`VEH-MRDN-0015` is a Meridian Azimuth).
    Do not drop `vehicleId` from this projection; drop it from the label instead.
    """
    items: list[dict] = []
    exclusive_start_key = None
    while True:
        kwargs: dict = {
            "ProjectionExpression": (
                "vehicleId, vin, dataSource, #p, #mk, #md, #yr"
            ),
            "ExpressionAttributeNames": {
                "#p": "producer",
                # make/model are not reserved words, but #yr IS — `year` is a
                # DynamoDB reserved word and an unaliased projection on it fails
                # the whole scan with ValidationException. Aliasing all three
                # keeps the pattern uniform rather than leaving one bare.
                "#mk": "make",
                "#md": "model",
                "#yr": "year",
            },
            # Server-side filter. NOTE this is applied AFTER each page is read,
            # so a page can come back with zero Items and a LastEvaluatedKey —
            # the loop below must therefore keep going on an empty page and stop
            # only when LastEvaluatedKey is absent. Breaking on `not Items` would
            # silently truncate the picker to whatever survived the first page.
            "FilterExpression": "#p = :producer",
            "ExpressionAttributeValues": {":producer": _SIMULATABLE_PRODUCER},
        }
        if exclusive_start_key:
            kwargs["ExclusiveStartKey"] = exclusive_start_key
        resp = vehicles_table.scan(**kwargs)
        items.extend(resp.get("Items") or [])
        exclusive_start_key = resp.get("LastEvaluatedKey")
        if not exclusive_start_key:
            break
    # Sort for deterministic pagination; `vin` is present on all rows.
    return sorted(items, key=lambda v: str(v.get("vin") or v.get("vehicleId") or ""))


# ---------------------------------------------------------------------------
# Simulation readiness helpers (T5.0 + FG13.T3)
# ---------------------------------------------------------------------------
#
# All three helpers use paginated scans against the **small side** — the sets
# that have 11, 22, and ~31 rows respectively — rather than per-vehicle queries
# (which would be ~300 queries for a 100-vehicle list).  See T5.0 Accept item 4.
#
# Each helper returns Optional[frozenset]:
#   - frozenset on success (may be empty — genuinely empty table)
#   - None on any exception — caller cannot tell "not enrolled" from "scan failed"
#
# When any input is None, _annotate_vehicle_readiness emits
# ``simulation_ready: None`` and ``not_ready_reasons: ["readiness_unavailable"]``
# rather than three false prerequisite claims.  FG13.T3 (W3):
# returning frozenset() on failure conflated "scan failed" with "empty table",
# producing confident false negatives for fully-provisioned vehicles.
#
# Readiness is advisory (Accept item 5) — a fetch failure must not block the
# list response, and the start path's own checks remain authoritative.
#
# Readiness is NOT a filter.  Unready vehicles are listed, just annotated.


def _fetch_enrolled_vehicle_ids(ddb_resource=None) -> frozenset | None:
    """Return the set of vehicleIds enrolled in CS_ALLOWED_FLEET_ID, or None on failure.

    Scans ``cms-{stage}-storage-fleet-enrollment`` filtering to
    ``fleetId == _CS_ALLOWED_FLEET_ID`` and projecting ``vehicleId`` only.

    Returns ``None`` on any exception so callers can distinguish "scan failed"
    from "table is genuinely empty" (frozenset()).  FG13.T3 (W3).

    This is the small side: ~11 rows on staging vs ~100 vehicles listed.
    """
    try:
        resource = ddb_resource or _get_ddb_resource()
        table = resource.Table(_FLEET_ENROLLMENT_TABLE)
        enrolled: set[str] = set()
        kwargs: dict = {
            "ProjectionExpression": "vehicleId",
            "FilterExpression": "fleetId = :fid",
            "ExpressionAttributeValues": {":fid": _CS_ALLOWED_FLEET_ID},
        }
        while True:
            resp = table.scan(**kwargs)
            for item in resp.get("Items") or []:
                vid = item.get("vehicleId")
                if vid:
                    enrolled.add(str(vid))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return frozenset(enrolled)
    except Exception:
        logger.exception(
            "readiness: failed to fetch fleet enrollment from %s; "
            "readiness unavailable for all vehicles",
            _FLEET_ENROLLMENT_TABLE,
        )
        return None


def _fetch_certified_vehicle_ids(ddb_resource=None) -> frozenset | None:
    """Return the set of VINs that have a row in the vehicle-certificates table, or None on failure.

    Scans ``cms-{stage}-storage-vehicle-certificates`` projecting ``vin`` only.

    Returns ``None`` on any exception so callers can distinguish "scan failed"
    from "table is genuinely empty" (frozenset()).  FG13.T3 (W3).

    This is the small side: ~22 rows on staging vs ~100 vehicles listed.
    """
    try:
        resource = ddb_resource or _get_ddb_resource()
        table = resource.Table(_VEHICLE_CERTIFICATES_TABLE)
        # Collect `vehicleId`, the table's PARTITION KEY — not `vin`.
        #
        # This join used to read the `vin` attribute, and `vin` carries no guarantee
        # on this table: describe-table shows KeySchema = [vehicleId HASH] with no
        # GSI, so `vin` is an ordinary attribute with no uniqueness constraint and no
        # writer keeping it in step with cms-{stage}-storage-vehicles. Four of 22
        # staging rows had a stale `vin`, and each one silently disabled its vehicle
        # in the picker with a false "no device certificate" — including VEH-VO-001,
        # which a backlog row had recorded as genuinely certless and declined on that
        # basis. See issues/2026-09-22-cert-readiness-joins-on-unmaintained-vin/.
        #
        # `vehicleId` is what the caller has and what this table is keyed by, so the
        # join is now on the one field both sides guarantee.
        vehicle_ids: set[str] = set()
        kwargs: dict = {"ProjectionExpression": "vehicleId"}
        while True:
            resp = table.scan(**kwargs)
            for item in resp.get("Items") or []:
                vid = item.get("vehicleId")
                if vid:
                    vehicle_ids.add(str(vid))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return frozenset(vehicle_ids)
    except Exception:
        logger.exception(
            "readiness: failed to fetch certificates from %s; "
            "readiness unavailable for all vehicles",
            _VEHICLE_CERTIFICATES_TABLE,
        )
        return None


def _fetch_campaign_covered_target_arns(ddb_resource=None) -> dict | None:
    """Return RUNNING campaign coverage keyed by targetArn, or None on failure.

    **Returns a mapping to a LIST, not a set and not a single campaign** —
    ``{targetArn: [{campaignId, campaignName, signalCount, signalIds}, ...]}``.
    Every existing caller and test uses it for membership (``"vehicle:X" in
    result``), which a dict satisfies identically on its keys, so the enrichment is
    additive. The keys are still exactly the targetArns the name describes.

    **Why a list.** An earlier revision kept one campaign per targetArn via
    ``setdefault``, on the stated reasoning that two RUNNING campaigns claiming the
    same target "is a data problem rather than a case to pick a winner for here".
    That reasoning was wrong. AWS IoT FleetWise runs every matching campaign
    concurrently, each with its own signal set and collection scheme, so multiple
    campaigns per vehicle is the normal shape — 8 targetArns on staging carry more
    than one and ``vehicle:MRDN0000000000015`` carries four. Keeping one meant
    reporting 1 signal for a vehicle collecting 295, via whichever row the scan
    happened to return first, which DynamoDB does not guarantee to be stable between
    calls.

    ``signalIds`` is a ``set`` and is **internal only** — not JSON serialisable, so
    callers must strip it before it reaches a response. It exists so a caller can
    compute the DISTINCT union across the campaigns covering one vehicle, which is
    not derivable from the per-campaign counts: those four campaigns sum to 304
    entries but cover 295 distinct signals.

    A targetArn counts as covered when the campaign has:
      - ``status == 'RUNNING'``
      - non-empty ``signalsToCollect``

    Covers ``vehicle:{vin}``, ``all``, and ``fleet:{fleetId}`` forms.

    Returns ``None`` on any exception so callers can distinguish "scan failed"
    from "no campaigns exist" ({}).  FG13.T3 (W3).

    Scans ``cms-{stage}-campaigns`` filtering to status == RUNNING.
    This is the small side: ~31 rows on staging vs ~100 vehicles listed.
    """
    try:
        resource = ddb_resource or _get_ddb_resource()
        table = resource.Table(_CAMPAIGNS_TABLE)
        covered: dict[str, list[dict]] = {}
        # Scan for RUNNING campaigns; project targetArn, signals and identity.
        kwargs: dict = {
            "ProjectionExpression": (
                "targetArn, signalsToCollect, campaignId, campaignName"
            ),
            "FilterExpression": "#s = :running",
            "ExpressionAttributeNames": {"#s": "status"},
            "ExpressionAttributeValues": {":running": "RUNNING"},
        }
        while True:
            resp = table.scan(**kwargs)
            for item in resp.get("Items") or []:
                # Only count campaigns that actually collect signals —
                # a RUNNING campaign with empty signalsToCollect produces no telemetry.
                signals = item.get("signalsToCollect")
                if not signals:
                    continue
                target = item.get("targetArn")
                if target:
                    covered.setdefault(str(target), []).append({
                        "campaignId": item.get("campaignId"),
                        "campaignName": item.get("campaignName"),
                        # Entry count, deliberately NOT de-duplicated: it is the
                        # number the campaigns list view shows, and the signals
                        # panel reconciles entries against distinct itself. A
                        # different number here would read as a contradiction.
                        "signalCount": len(signals) if hasattr(signals, "__len__") else None,
                        "signalIds": _signal_id_set(signals),
                    })
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return covered
    except Exception:
        logger.exception(
            "readiness: failed to fetch campaigns from %s; "
            "readiness unavailable for all vehicles",
            _CAMPAIGNS_TABLE,
        )
        return None


def _signal_id_set(signals) -> set:
    """Normalise ``signalsToCollect`` to a set of comparable signal ids.

    Rows carry a list of numeric ids. ``Decimal("1")``, ``1`` and ``"1"`` must
    collapse to one member or the union inflates, so everything is coerced to
    ``str``. Unhashable or unstringable elements (a dict-shaped entry, which no
    current row uses) are skipped rather than raised: an inflated count is a smaller
    error than a 500 on the whole vehicle list.
    """
    out: set = set()
    try:
        iterator = iter(signals)
    except TypeError:
        return out
    for element in iterator:
        try:
            out.add(str(element))
        except Exception:  # pragma: no cover - defensive
            continue
    return out


# Most-specific-first. The rank is the ORDERING key for the campaigns list, and
# it is what makes the list stable: DynamoDB scan order is not guaranteed, so
# without an explicit sort the same vehicle can report its four campaigns in a
# different order on each call.
_SCOPE_RANK = {"vehicle": 0, "fleet": 1, "broadcast": 2}


def _resolve_telemetry_campaigns(
    vin: str,
    fleet_id,
    coverage,
) -> tuple[list[dict], int]:
    """Return ``(campaigns, distinct_signal_total)`` for one vehicle.

    ALL matching scopes are collected, not the most specific one — FleetWise runs
    every matching campaign concurrently, so a vehicle with a per-vehicle campaign
    AND a fleet campaign is collecting both. Reporting only the first is what
    produced the 1-of-4 undercount this function replaces.

    Ordering is ``(scope rank, -signalCount, campaignId)``: most specific first,
    largest first within a scope, then id as a total tiebreak so the list cannot
    reorder between calls on scan order alone.

    ``distinct_signal_total`` is the size of the UNION of signal ids, not the sum of
    the per-campaign counts. The two differ — 304 vs 295 for
    ``vehicle:MRDN0000000000015`` — because ids repeat both within one campaign and
    across campaigns, so a caller summing ``signalCount`` would overstate what the
    vehicle actually collects.
    """
    if not coverage:
        return [], 0

    matched: list[dict] = []
    union: set = set()
    for target, scope in (
        (f"vehicle:{vin}", "vehicle"),
        (f"fleet:{fleet_id}" if fleet_id is not None else None, "fleet"),
        ("all", "broadcast"),
    ):
        if target is None:
            continue
        # Membership FIRST, and deliberately not `.get()`: a set-shaped `coverage`
        # (older callers, and the readiness tests that pass a frozenset) has no
        # `.get`, so testing containment via `.get()` silently reported every
        # frozenset-shaped vehicle as uncovered. Membership works for both shapes.
        if target not in coverage:
            continue
        entry = coverage.get(target) if hasattr(coverage, "get") else None
        # Three accepted shapes, so a caller mid-deploy cannot get a false negative:
        #   list of dicts — current, one entry per covering campaign
        #   single dict    — the prior single-campaign shape
        #   absent/None    — set-shaped; scope is known, identity is not, and it
        #                    must NOT be invented
        if isinstance(entry, list):
            details = entry
        elif isinstance(entry, dict):
            details = [entry]
        else:
            details = [{}]
        for detail in details:
            union |= detail.get("signalIds") or set()
            matched.append({
                "scope": scope,
                "target": target,
                "campaignId": detail.get("campaignId"),
                "campaignName": detail.get("campaignName"),
                "signalCount": detail.get("signalCount"),
            })

    matched.sort(
        key=lambda c: (
            _SCOPE_RANK.get(c["scope"], len(_SCOPE_RANK)),
            -(c["signalCount"] or 0),
            str(c["campaignId"] or ""),
        )
    )
    return matched, len(union)


def _annotate_vehicle_readiness(
    vehicles: list[dict],
    enrolled_ids: frozenset | None,
    cert_vehicle_ids: frozenset | None,
    campaign_arns: frozenset | None,
    vehicle_id_to_fleet: dict,
) -> tuple[list[dict], int]:
    """Annotate each vehicle with ``simulation_ready`` and ``not_ready_reasons``.

    Returns ``(annotated_vehicles, ready_count)``.

    **Failure path (FG13.T3 — W3)**: if ANY of the three input sets is None
    (scan failed), this cannot distinguish "not provisioned" from "could not read".
    Rather than emitting three false prerequisite claims, emit:
      - ``simulation_ready: None``   (JSON null — third state, distinct from False)
      - ``not_ready_reasons: ["readiness_unavailable"]``
    Unknowns are NOT counted as ready; ``ready_count`` reflects only vehicles
    whose readiness is definitively known (simulation_ready is True).

    **Healthy path**: when all three sets are present, report as before —
    Rules (Accept item 2 — ALL failing prerequisites are reported):
      - ``not_fleet_enrolled``: vehicleId not in enrolled_ids
      - ``no_certificate``: vehicle's vehicleId not in cert_vehicle_ids
      - ``no_telemetry_campaign``: vehicle-telemetry only; no RUNNING campaign
        covering ``vehicle:{vin}``, ``all``, or ``fleet:{enrolled_fleet_id}``

    cloud-telemetry vehicles skip the campaign check by design (Accept item 3
    second paragraph — that source reads "for `vehicle-telemetry` only").
    """
    # If any input is unknown, every vehicle gets the unavailable sentinel.
    readiness_known = (
        enrolled_ids is not None
        and cert_vehicle_ids is not None
        and campaign_arns is not None
    )

    annotated: list[dict] = []
    ready_count = 0
    for vehicle in vehicles:
        vehicle_copy = dict(vehicle)

        if not readiness_known:
            # Cannot determine readiness — do not emit false prerequisite claims.
            vehicle_copy["simulation_ready"] = None
            vehicle_copy["not_ready_reasons"] = ["readiness_unavailable"]
            annotated.append(vehicle_copy)
            # unknowns are NOT counted as ready
            continue

        vid = vehicle.get("vehicleId") or ""
        vin = vehicle.get("vin") or vid
        reasons: list[str] = []

        if vid not in enrolled_ids:
            reasons.append("not_fleet_enrolled")

        # Certificate check joins on vehicleId (the certs table's partition key),
        # NOT on vin. See _fetch_certified_vehicle_ids for why vin cannot be
        # trusted here.
        if not cert_vehicle_ids or vid not in cert_vehicle_ids:
            reasons.append("no_certificate")

        # Campaign check: vehicle-telemetry ONLY.
        #
        # Resolution is attributed and PLURAL, so the UI can say which campaigns
        # will run and how much they collect, instead of only flagging when none
        # will. `_resolve_telemetry_campaigns` collects every matching scope —
        # FleetWise runs them concurrently — and returns the distinct signal union,
        # which is not the sum of the per-campaign counts.
        #
        # `applicable` distinguishes "no campaign, and that is a problem" from "no
        # campaign, and none is needed" — a cloud-telemetry vehicle reaches MSK via
        # the rule path and never needs a FleetWise campaign, so reporting one as
        # missing for it would be a false alarm.
        data_source = vehicle.get("dataSource") or ""
        campaign_applicable = data_source == "vehicle-telemetry"
        fleet_id = vehicle_id_to_fleet.get(vid)
        resolved_campaigns, signal_total = _resolve_telemetry_campaigns(
            vin, fleet_id, campaign_arns
        )
        if campaign_applicable and not resolved_campaigns:
            reasons.append("no_telemetry_campaign")

        vehicle_copy["telemetry_campaigns"] = resolved_campaigns
        vehicle_copy["telemetry_signal_total"] = signal_total
        vehicle_copy["telemetry_campaign_applicable"] = campaign_applicable

        vehicle_copy["simulation_ready"] = len(reasons) == 0
        vehicle_copy["not_ready_reasons"] = reasons
        annotated.append(vehicle_copy)
        if len(reasons) == 0:
            ready_count += 1

    return annotated, ready_count


# ---------------------------------------------------------------------------
# Dispatch resolution
# ---------------------------------------------------------------------------

def resolve_simulation_path(vehicle: dict) -> tuple[str, str | None]:
    """Return ``(data_source, error_reason)`` for the given vehicle record.

    ``error_reason`` is ``None`` on success and a short string describing why
    the vehicle cannot be simulated otherwise.

    Rules, applied in this order:
      - Missing `dataSource` → rejected with explicit reason, never defaulted.
      - Unknown `dataSource` → rejected.
      - `producer` other than `meridian` → rejected (Group 8).
      - Otherwise → success; ``data_source`` is the canonical value.

    The `dataSource` checks come FIRST on purpose. A row that is both
    non-Meridian and missing its `dataSource` reports `no_data_source`, because
    that is the defect an operator can act on; reporting the producer instead
    would hide a broken row behind a scoping rule. A test pins this ordering.

    The producer check is here rather than only on the picker because the picker
    and the start path do not share a code path — `list_vehicles_handler` calls
    `_list_all_vehicles` directly. A filter applied only to the list would leave
    `POST /simulate/start` accepting an oem1 `vehicleId` from a hand-built body,
    which is the same guard-on-one-path shape as
    issues/2026-09-13-simulation-start-empty-vehicles-bypasses-fleet-scoping/.

    This function deliberately has NO default and NO override path.  See the
    module docstring for why.
    """
    data_source = vehicle.get("dataSource")
    if not data_source:
        return "", "no_data_source"
    if data_source not in _KNOWN_DATA_SOURCES:
        return "", f"unknown_data_source:{data_source}"
    producer = vehicle.get("producer")
    if producer != _SIMULATABLE_PRODUCER:
        # Includes a missing producer: absent attribution is not evidence of
        # Meridian ownership, so it fails closed rather than being admitted.
        return "", f"not_simulatable_producer:{producer or 'unset'}"
    return data_source, None


# ---------------------------------------------------------------------------
# GET /simulate/vehicles
# ---------------------------------------------------------------------------

def list_vehicles_handler(event: dict, context) -> dict:  # noqa: ANN001
    """Return the Meridian vehicles available to the simulation picker.

    Requires the caller to be a member of the ``connected-services`` Cognito
    group (operator-only, FG2.1).  A non-operator caller receives 403 before
    any DynamoDB operation is issued.

    Filtered to `producer == meridian` (Group 8, reversing T6.1). The start path
    enforces the same rule independently in `resolve_simulation_path`, so this
    filter is the UX half and not the control — see `_SIMULATABLE_PRODUCER`.

    ## Readiness annotation (T5.0)

    Each vehicle in the response carries ``simulation_ready: bool`` and
    ``not_ready_reasons: list[str]``.  Three prerequisites are checked for
    ALL failing ones at once (not just the first):

      - ``not_fleet_enrolled``   — vehicleId not in CS_ALLOWED_FLEET_ID fleet
      - ``no_certificate``       — no row in vehicle-certificates for this VIN
      - ``no_telemetry_campaign``— (vehicle-telemetry only) no RUNNING campaign
                                   with non-empty signalsToCollect covering this VIN

    Readiness is advisory.  This handler is NOT an authorization gate; the
    start path's own checks remain authoritative.  A fetch failure degrades
    gracefully: the affected vehicles show the relevant reason rather than
    blocking the list response.

    The response also carries ``ready_count`` — the number of ready vehicles.
    ``count`` retains its existing meaning (total rows returned).
    """
    try:
        _require_operator(event)  # ← FG2.1: fail closed before any DDB call
        vehicles_table = _get_ddb_resource().Table(
            _required_env("VEHICLES_TABLE_NAME")
        )
        vehicles = _list_all_vehicles(vehicles_table)

        # ── Readiness annotation (T5.0) ───────────────────────────────────────
        # Fetch the three small-side sets: enrolled vehicleIds, certified VINs,
        # covered campaign targetArns.  Each fetch is fail-soft (returns empty on
        # error).  The vehicle_id_to_fleet map is derived from the enrollment scan
        # to support fleet-scoped campaign lookups.
        enrolled_ids = _fetch_enrolled_vehicle_ids()
        cert_vehicle_ids = _fetch_certified_vehicle_ids()
        campaign_arns = _fetch_campaign_covered_target_arns()

        # Build vehicleId → fleetId mapping from enrolled set.
        # We need fleet IDs for fleet:{fleetId} campaign matching, so re-use the
        # enrollment scan result.  For enrolled vehicles the fleet is always
        # _CS_ALLOWED_FLEET_ID by construction of enrolled_ids; for unenrolled
        # vehicles there is no fleet to look up.
        vehicle_id_to_fleet = {vid: _CS_ALLOWED_FLEET_ID for vid in (enrolled_ids or frozenset())}

        vehicles, ready_count = _annotate_vehicle_readiness(
            vehicles, enrolled_ids, cert_vehicle_ids, campaign_arns, vehicle_id_to_fleet
        )

        return _api_response(200, {
            "vehicles": vehicles,
            "count": len(vehicles),
            "ready_count": ready_count,
            # Group 8: was hardcoded False alongside the unfiltered scan. Left
            # False it would be an outright false statement about the payload.
            "filtered_on_producer": True,
        })
    except _Unauthorized:
        return _api_response(403, {"error": "Forbidden"})
    except Exception:  # noqa: BLE001
        logger.exception("Internal error in list_vehicles_handler")
        return _api_response(500, {"error": "Internal server error"})


# ---------------------------------------------------------------------------
# POST /simulate/start
# ---------------------------------------------------------------------------

def simulate_start_handler(event: dict, context) -> dict:  # noqa: ANN001
    """Resolve simulation path from vehicle dataSource and dispatch.

    Requires the caller to be a member of the ``connected-services`` Cognito
    group (operator-only, FG2.1).  A non-operator caller receives 403 before
    any DynamoDB operation is issued.

    Body: ``{"vehicle_id": "<vehicleId>"}`` — or ``/simulate/start/{vid}``.

    The body carries NO path/transport parameter and NO override — the path is
    read from the vehicle record.  A vehicle with no `dataSource` is rejected
    with ``400 {"error": "...", "reason": "no_data_source"}``.

    ## Dispatch (T6.1)

    This handler **starts** a simulation.  It resolves `dataSource` to the
    simulation Lambda's two config axes via `_SIMULATION_DISPATCH` (see that
    table's comment for why there are two), invokes that Lambda, and returns its
    result — propagating its status code, so a 403 from its own per-VIN
    authorization reaches the caller as a 403.

    The caller's claims are forwarded verbatim; the simulation Lambda re-authorizes
    the real caller.  `_require_operator` here is the first gate, not the only one.

    Before T6.1 this returned 200 with a resolved-path description and started
    nothing.  Any future change that stops invoking must also stop reporting
    success, or the route becomes a success-shaped failure again.

    ## Identity-write guard (T6.2)

    This handler reads the vehicle record but writes NOTHING back.  The three
    identity fields — `producer`, `sold_to`, `oem_source` — are explicitly
    NOT written.  See test_simulation_never_writes_vehicle_identity for the
    executable form of this invariant.
    """
    try:
        _require_operator(event)  # ← FG2.1: fail closed before any DDB call

        # Parse request body
        raw_body = event.get("body") or "{}"
        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, TypeError):
            return _api_response(400, {"error": "Request body must be valid JSON"})

        vehicle_id = (body.get("vehicle_id") or "").strip()
        if not vehicle_id:
            # The CDK wires POST on BOTH `/simulate/start` (body carries
            # `vehicle_id`, which is what `subscriptionsClient.ts` sends) and
            # `/simulate/start/{vid}`.  Before this task only the `{vid}` form
            # had a method, and this handler never read `pathParameters`, so that
            # route could not succeed for any input — it always 400'd on a missing
            # `vehicle_id`.  Accepting both keeps the deployed surface unchanged
            # while making both forms work.
            vehicle_id = str(
                (event.get("pathParameters") or {}).get("vid") or ""
            ).strip()
        if not vehicle_id:
            return _api_response(400, {"error": "vehicle_id is required"})

        # Look up the vehicle record
        vehicles_table = _get_ddb_resource().Table(
            _required_env("VEHICLES_TABLE_NAME")
        )
        resp = vehicles_table.get_item(Key={"vehicleId": vehicle_id})
        vehicle = resp.get("Item")
        if not vehicle:
            return _api_response(404, {"error": f"Vehicle not found: {vehicle_id!r}"})

        # Derive the simulation path — no default, no override
        data_source, error_reason = resolve_simulation_path(vehicle)
        if error_reason:
            if error_reason == "no_data_source":
                return _api_response(400, {
                    "error": (
                        f"Cannot start simulation: vehicle {vehicle_id!r} has no "
                        "dataSource configured.  Set the vehicle's dataSource to "
                        "'vehicle-telemetry' or 'cloud-telemetry' before simulating."
                    ),
                    "reason": "no_data_source",
                    "vehicle_id": vehicle_id,
                })
            if error_reason.startswith("not_simulatable_producer"):
                # Group 8. Without this branch the refusal fell through to the
                # "unknown dataSource" default below and told the operator to go
                # check a dataSource that is perfectly valid — found by live smoke
                # test, not by the unit tests, which assert the `reason` TOKEN and
                # never read the human sentence. A wrong diagnosis is worse than a
                # terse one: it sends someone to edit the wrong field.
                producer = error_reason.split(":", 1)[1] if ":" in error_reason else "unset"
                return _api_response(400, {
                    "error": (
                        f"Cannot start simulation: vehicle {vehicle_id!r} was produced by "
                        f"{producer!r}, and CS can only simulate Meridian vehicles.  There "
                        "is no simulation path for another producer's vehicle — its "
                        "telemetry would arrive through that producer's own ingest route, "
                        "which the simulator does not drive."
                    ),
                    "reason": error_reason,
                    "vehicle_id": vehicle_id,
                })
            return _api_response(400, {
                "error": (
                    f"Cannot start simulation: vehicle {vehicle_id!r} has an unknown "
                    f"dataSource.  {error_reason}"
                ),
                "reason": error_reason,
                "vehicle_id": vehicle_id,
            })

        dispatch_description = _DISPATCH_DESCRIPTION[data_source]

        # ── Identity-write guard ──────────────────────────────────────────────
        # NOTHING is written back to the vehicle record here.
        # `producer`, `sold_to`, and `oem_source` are NOT touched.
        # Producing Meridian-product data *about* an OEM1 vehicle is legitimate;
        # re-attributing it is the fabrication failure (spec T6.2).
        # The test_simulation_never_writes_vehicle_identity test asserts this
        # source-structurally and behaviourally.

        # ── Dispatch to the simulation Lambda (T6.1) ──────────────────────────
        # Before this task the handler returned 200 with `dispatch_description`
        # and started nothing.  It now invokes the simulation Lambda and returns
        # that Lambda's own result.
        #
        # The caller's claims are forwarded verbatim so the simulation Lambda
        # runs its OWN authorization on the real caller:  its `/start` arm calls
        # `_authorize_per_vin(caller, vins, write_route=True,
        # allow_connected_services=True, cs_require_resolved_vins=True)`
        # (`simulation_lambda.py:390-397`), reading identity from
        # `requestContext.authorizer.claims`.  Inventing a synthetic caller here
        # — or omitting the claims — would make that check deny (no claims) or,
        # if it were ever relaxed to compensate, would turn this handler into an
        # authorization bypass for a deployed route.  `_require_operator` above
        # is the *first* gate, not the only one.

        # ── Trip-parameter validation (T2.1) — before dispatch, not after ────
        # `city` is validated against `_ALLOWED_CITIES` here, ahead of the
        # simulation Lambda invoke below. Validating after a failed invoke
        # would mean an unrecognised value still reached the callee's
        # shell-invoked worker before being rejected — the allowlist's whole
        # point is to gate dispatch, not merely appear somewhere in the
        # response. `trips`/`route_length` are NOT range-clamped here: the
        # callee (`simulation_lambda.py::_start`) already clamps
        # `route_length` to `[5, 60]` and is the authority on range-clamping;
        # this wrapper validates type and applies a generous upper cap (100)
        # before dispatch — see the FG1.T1 validation block below.
        raw_city = body.get("city")
        if raw_city is not None and raw_city not in _ALLOWED_CITIES:
            return _api_response(400, {
                "error": f"Unrecognised city: {raw_city!r}. "
                         f"Allowed: {sorted(_ALLOWED_CITIES)}",
                "reason": "unrecognised_city",
            })

        # ── Shape/range validation (FG1.T1) — before dispatch, not after ─────
        # The callee (`simulation_lambda.py::_start`) passes `trips` through as
        # `str(config.get("trips", 3))` with no cast and no clamp
        # (`simulation_lambda.py:1546`), into a `--trips` argv entry. Not an
        # injection vector (no shell=True), but an operator can post
        # `trips: 1000000000` and start effectively unbounded ECS work. Validate
        # and cap here before dispatching.
        #
        # Cap is 100: above any reasonable operator intent and well below
        # anything that poses a resource-exhaustion risk. In-repo precedent:
        # `simulation_lambda.py:2764-2773` uses the same validate-and-cap shape
        # (with this same rationale comment) for `maintenance_scenarios`.
        #
        # `bool` must NOT pass as an int — `isinstance(True, int)` is True in
        # Python. Guard it explicitly before the int checks on all numeric fields.

        raw_trips = body.get("trips")
        if raw_trips is not None:
            if isinstance(raw_trips, bool) or not isinstance(raw_trips, int):
                return _api_response(400, {
                    "error": "trips must be an integer",
                    "reason": "invalid_trips",
                })
            if raw_trips < 1:
                return _api_response(400, {
                    "error": "trips must be a positive integer",
                    "reason": "invalid_trips",
                })
            if raw_trips > 100:
                return _api_response(400, {
                    "error": "trips exceeds cap of 100",
                    "reason": "invalid_trips",
                })

        raw_route_length = body.get("route_length")
        if raw_route_length is not None:
            if isinstance(raw_route_length, bool) or not isinstance(raw_route_length, int):
                return _api_response(400, {
                    "error": "route_length must be an integer",
                    "reason": "invalid_route_length",
                })
            if raw_route_length < 1:
                return _api_response(400, {
                    "error": "route_length must be a positive integer",
                    "reason": "invalid_route_length",
                })
            if raw_route_length > 100:
                return _api_response(400, {
                    "error": "route_length exceeds cap of 100",
                    "reason": "invalid_route_length",
                })

        raw_safety = body.get("safety_scenarios")
        if raw_safety is not None:
            if not isinstance(raw_safety, list):
                return _api_response(400, {
                    "error": "safety_scenarios must be a list",
                    "reason": "invalid_safety_scenarios",
                })
            if len(raw_safety) > 100:
                return _api_response(400, {
                    "error": "safety_scenarios exceeds cap of 100",
                    "reason": "invalid_safety_scenarios",
                })
            if any(not isinstance(x, str) for x in raw_safety):
                return _api_response(400, {
                    "error": "safety_scenarios must contain strings",
                    "reason": "invalid_safety_scenarios",
                })

        raw_maintenance = body.get("maintenance_scenarios")
        if raw_maintenance is not None:
            if not isinstance(raw_maintenance, list):
                return _api_response(400, {
                    "error": "maintenance_scenarios must be a list",
                    "reason": "invalid_maintenance_scenarios",
                })
            if len(raw_maintenance) > 100:
                return _api_response(400, {
                    "error": "maintenance_scenarios exceeds cap of 100",
                    "reason": "invalid_maintenance_scenarios",
                })
            if any(not isinstance(x, str) for x in raw_maintenance):
                return _api_response(400, {
                    "error": "maintenance_scenarios must contain strings",
                    "reason": "invalid_maintenance_scenarios",
                })
        # ── End shape/range validation ─────────────────────────────────────────

        dispatch = _SIMULATION_DISPATCH[data_source]
        sim_config = {
            "vehicles": [{
                "vehicleId": vehicle_id,
                "vin": vehicle.get("vin") or vehicle_id,
            }],
            # `mode` and `rule_name` are ALWAYS server-derived from `dispatch`.
            # The request body's own `mode`/`rule_name` keys, if a caller sends
            # them, are never read anywhere in this function — not read-and-
            # discarded, simply never referenced. This is the load-bearing
            # property `test_mode_and_rule_name_cannot_be_overridden_by_the_
            # request_body` (test_handler.py) pins: a naive
            # `sim_config.update(body)` here would silently reopen the exact
            # transport-choice gap this wrapper exists to close (spec.md's
            # central Constraint, `2026-09-19-cs-trip-simulator-parity`).
            "mode": dispatch["mode"],
            "rule_name": f"cms_{_dispatch_stage()}_{dispatch['rule_suffix']}",
            "city": raw_city if raw_city is not None else "seattle",
            "trips": raw_trips if raw_trips is not None else 3,
            "route_length": raw_route_length if raw_route_length is not None else 20,
        }
        # Safety/maintenance scenarios are optional and, when present, derive
        # their own rate/degradation companion — mirrors
        # `TripSimulatorModal.tsx`'s own `selectedSafety.length > 0 ? 0.9 :
        # 0.15` rule, reproduced server-side so CS's request shape matches
        # CMS's modal's shape exactly once both call this same route.
        if raw_safety is not None:
            sim_config["safety_scenarios"] = raw_safety
            sim_config["safety_rate"] = 0.9 if raw_safety else 0.15
        if raw_maintenance is not None:
            sim_config["maintenance_scenarios"] = raw_maintenance
            sim_config["progressive_degradation"] = bool(raw_maintenance)
        sim_event = {
            "path": _SIMULATION_START_PATH,
            "httpMethod": "POST",
            "body": json.dumps(sim_config),
            "requestContext": {"authorizer": {"claims": _claims(event)}},
        }

        logger.info(
            "simulate_start dispatching",
            extra={
                "vehicle_id": vehicle_id,
                "data_source": data_source,
                "mode": sim_config["mode"],
                "rule_name": sim_config["rule_name"],
                "stage": _STAGE,
            },
        )

        invoke_result = _get_lambda_client().invoke(
            FunctionName=_required_env("SIMULATION_FUNCTION_NAME"),
            InvocationType="RequestResponse",
            Payload=json.dumps(sim_event).encode("utf-8"),
        )

        # A Lambda-level failure (unhandled exception in the callee) is reported
        # via FunctionError, NOT via an exception here — invoke() itself returns
        # 200 for a function that raised.  Checking only the HTTP status of the
        # invoke would report a crashed simulation as a successful start.
        if invoke_result.get("FunctionError"):
            logger.error(
                "simulation Lambda returned FunctionError",
                extra={"vehicle_id": vehicle_id,
                       "function_error": invoke_result["FunctionError"]},
            )
            return _api_response(502, {
                "error": "Simulation service failed to start the simulation.",
                "reason": "simulation_invoke_failed",
                "vehicle_id": vehicle_id,
            })

        raw_payload = invoke_result.get("Payload")
        payload_text = raw_payload.read().decode("utf-8") if raw_payload is not None else ""
        try:
            sim_response = json.loads(payload_text) if payload_text else {}
        except (json.JSONDecodeError, TypeError):
            logger.error(
                "simulation Lambda returned unparseable payload",
                extra={"vehicle_id": vehicle_id},
            )
            return _api_response(502, {
                "error": "Simulation service returned an unreadable response.",
                "reason": "simulation_response_unparseable",
                "vehicle_id": vehicle_id,
            })

        # The callee is an API-Gateway-shaped handler, so its result carries its
        # own statusCode and JSON-encoded body.  Propagate the status rather than
        # flattening everything to 200 — a 403 from the simulation Lambda's own
        # per-VIN authorization must reach the caller as a 403.
        sim_status = sim_response.get("statusCode", 502)
        try:
            sim_body = json.loads(sim_response.get("body") or "{}")
        except (json.JSONDecodeError, TypeError):
            sim_body = {}

        if sim_status >= 400:
            logger.warning(
                "simulation Lambda refused the start",
                extra={"vehicle_id": vehicle_id, "sim_status": sim_status},
            )
            return _api_response(sim_status, {
                **_safe_callee_body(sim_body, sim_status),
                "vehicle_id": vehicle_id,
                "data_source": data_source,
            })

        return _api_response(sim_status, {
            **_safe_callee_body(sim_body, sim_status),
            "vehicle_id": vehicle_id,
            "data_source": data_source,
            "dispatch": dispatch_description,
        })

    except _Unauthorized:
        return _api_response(403, {"error": "Forbidden"})
    except Exception:  # noqa: BLE001
        logger.exception("Internal error in simulate_start_handler")
        return _api_response(500, {"error": "Internal server error"})
