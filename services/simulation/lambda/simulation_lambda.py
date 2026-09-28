"""
Simulation API Lambda — thin orchestrator that manages ECS Fargate sim workers.
Routes: /api/simulation/{start,stop,status,list,health,drivers,presets,discover-iot-endpoint}
"""
import json, os, time, uuid, boto3
from boto3.dynamodb.conditions import Key
from datetime import datetime, timezone
from typing import Optional

from _lib.fleet_membership import classify_driver_self, resolve_vins_to_fleets

ecs = boto3.client("ecs")
ddb = boto3.resource("dynamodb")
iot = boto3.client("iot")
logs_client = boto3.client("logs")

CLUSTER = os.environ["ECS_CLUSTER"]
# Strip revision number so ECS always uses latest active revision
def _task_family(arn_or_name):
    # "arn:aws:ecs:...:task-definition/family:33" → "arn:aws:ecs:...:task-definition/family"
    # "family:33" → "family"
    if "/" in arn_or_name:
        base, name_rev = arn_or_name.rsplit("/", 1)
        return base + "/" + name_rev.split(":")[0]
    return arn_or_name.split(":")[0]

TASK_DEF = _task_family(os.environ["WORKER_TASK_DEF"])
SUBNETS = os.environ["WORKER_SUBNETS"].split(",")
SG = os.environ["WORKER_SECURITY_GROUP"]
SIM_TABLE = ddb.Table(os.environ["SIMULATIONS_TABLE"])
STAGE = os.environ.get("DEPLOYMENT_STAGE", "dev")
REGION = os.environ.get("AWS_REGION_NAME", os.environ.get("AWS_REGION", "us-east-1"))
WORKER_LOG_GROUP = f"/ecs/cms-{STAGE}/sim-worker"

# ── Console log-read window (issues/2026-09-23-sim-logs-pane-empty-heartbeat-
#    crowds-out-run-output/) ─────────────────────────────────────────────────
# `_get_worker_logs` post-filters MQTT keepalive lines, so the CloudWatch limit
# must be large enough that the filter has material left after it runs, and the
# read must be BOUNDED TO THE RUN or a long-lived idle agent's keepalive traffic
# is all that a stream-relative "newest N" ever returns.
#
# Measured on the defect: 100 of the vehicle-ecu stream's latest 100 events were
# keepalive noise and 0 survived the filter, so `output` came back empty while
# the run's CAN-frame lines sat in CloudWatch inside the run window.
LOG_EVENT_FETCH_LIMIT = 1000
# `get_log_events` treats endTime as exclusive; a completed run's final lines can
# land just after the recorded endTime, so extend the window past it.
LOG_WINDOW_TAIL_GRACE_MS = 120_000

# ── Connected-services fleet allowlist (T7.5) ─────────────────────────────
# The Meridian Range Demo fleet that connected-services callers are allowed to
# start/stop/status simulations for.  Env-var-driven so staging and prod can
# differ without a code change; default matches the seeded demo fleet.
CS_ALLOWED_FLEET_ID = os.environ.get("CS_ALLOWED_FLEET_ID", "flt-meridian-range-001")

# ── Allowlisted MSK rule names (T7.6) ────────────────────────────────────────
# A caller-supplied rule_name is a caller-chosen data destination.  Only the
# known product rules may be forwarded into the ECS task argv — an unvalidated
# routing primitive reachable from a browser must never route telemetry to an
# attacker-controlled MSK topic.  The CMS-native default is always the fallback.
# Both names are stage-parameterised at use time (see _start()).
_ALLOWED_RULE_SUFFIXES = (
    "iot_msk_rule",                 # CMS-native: cms_{STAGE}_iot_msk_rule
    "cs_product_meridian_ev_rule",  # Connected-services product: cms_{STAGE}_cs_product_meridian_ev_rule
)

# Template campaign whose signal list + collection scheme define the baseline
# telemetry a vehicle needs to produce ANY data on the FWE path. See
# _ensure_telemetry_campaign.
_TELEMETRY_TEMPLATE = "cms-fleet-gps-10s"
# Maximum pages to follow when querying the targetArn-index GSI.
# A client returning a non-advancing LastEvaluatedKey would otherwise spin
# forever inside the deployed Lambda's timeout.  20 pages × 1 MB = 20 MB of
# campaigns data, far beyond any realistic table size for a single targetArn.
_GSI_QUERY_MAX_PAGES = 20

# ── Parked-vehicle DTC timing constants ──────────────────────────────────────
# Live-measured end-to-end latency breakdown (2026-08-05):
#   ~9 s  presence idle tick (ceiling 10 s, by design)
#   ~N s  waiting for next DTC_QUERY (executionFrequencyMs)
#   ~N s  FWE batching + MSK + Flink pipeline (collectionScheme.periodMs)
# These two constants represent the fixed/measured contributions so that
# _set_faults can derive appliesWithinSeconds from the campaign's cadence
# fields rather than a magic number that drifts when cadences change.
_PRESENCE_IDLE_TICK_S = 10    # 10s ceiling; observed ~9s in live measurement
_PIPELINE_ALLOWANCE_S = 10    # ~10s for FWE batching + MSK + Flink overhead
_APPLIES_WITHIN_FALLBACK_S = 60
# Lower bound on a DERIVED value.  Guards against a campaign row with an
# implausibly small cadence promising the operator a latency the pipeline
# cannot meet; the ~10 s MSK+Flink leg alone makes anything under this
# unachievable.  Distinct from the fallback, which covers derivation failure.
_APPLIES_WITHIN_MINIMUM_S = 25  # conservative default when campaign fields are absent
# ─────────────────────────────────────────────────────────────────────────────

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Content-Type": "application/json",
}

def _resp(code, body):
    return {"statusCode": code, "headers": CORS, "body": json.dumps(body, default=str)}


# ── Authorization helpers — Task 3.1 ─────────────────────────────────────────
# Identity extraction and per-route gate helpers.  All follow the post-fix
# main_api/index.py pattern (commit d235fb31): NO `or not user_groups`
# fallback.  Groupless callers are denied, not promoted to admin.

_OPERATOR_GROUPS = {'platform-admin', 'fleet-operator'}


def _extract_caller(event: dict) -> dict:
    """Extract and classify caller identity from the API Gateway authorizer claims.

    SINGLE QUOTES around 'platform-admin' to match test_no_fail_open_invariant.
    The fail-open pattern is absent: no fallback grants groupless callers admin.

    Mirrors main_api/index.py:1241-1262 (post-fix shape).
    """
    claims = (event.get('requestContext') or {}).get('authorizer', {}).get('claims', {})
    groups_raw = claims.get('cognito:groups', '') or ''
    user_groups = [g.strip() for g in groups_raw.split(',') if g.strip()]
    user_fleet_ids = set(
        fid.strip()
        for fid in (claims.get('custom:fleetIds') or '').split(',')
        if fid.strip()
    )

    is_driver_self, driver_id = classify_driver_self(claims, driver_self_enabled=True)
    # NO fail-open: is_admin is strictly 'platform-admin' in user_groups.
    # Removed `or not user_groups` — that was the P0 fail-open.
    is_admin = 'platform-admin' in user_groups
    is_viewer = 'fleet-viewer' in user_groups and 'fleet-operator' not in user_groups
    is_operator = 'fleet-operator' in user_groups
    is_guest = 'fleet-guest' in user_groups
    user_email = claims.get('email') or claims.get('cognito:username') or 'operator'

    return {
        'claims': claims,
        'user_groups': user_groups,
        'user_fleet_ids': user_fleet_ids,
        'is_admin': is_admin,
        'is_viewer': is_viewer,
        'is_operator': is_operator,
        'is_guest': is_guest,
        'is_driver_self': is_driver_self,
        'driver_self_id': driver_id,
        'user_email': user_email,
    }


def _authorize_admin_only(caller: dict) -> Optional[dict]:
    """Return 403 unless caller is platform-admin.  Driver-self always 403.

    Used by /agent/stop (broad operation — all FWE agent tasks).
    """
    if caller['is_driver_self']:
        return _resp(403, {'error': 'Driver-self tokens may not call the simulation API.'})
    if not caller['is_admin']:
        return _resp(403, {'error': 'Requires platform-admin role.'})
    return None


def _authorize_authenticated(caller: dict, allow_connected_services: bool = False) -> Optional[dict]:
    """Return 403 for driver-self only.  All non-driver-self authenticated callers pass.

    Used by /presets and /discover-iot-endpoint (read-only, no fleet scope needed).
    Groupless callers are denied because they carry no group claim — not a driver-self,
    but also not an authenticated operator/viewer.

    `allow_connected_services` is opt-in per call site, mirroring
    `_authorize_per_vin`. Default False so /presets and /discover-iot-endpoint keep
    their existing posture; only /agent/status passes True, and it SCOPES the
    response it returns. A connected-services group satisfies none of the four
    flags below, so without this it was refused outright — see
    issues/2026-09-23-agent-routes-do-not-authorize-connected-services-callers/.
    """
    if caller['is_driver_self']:
        return _resp(403, {'error': 'Driver-self tokens may not call the simulation API.'})
    if allow_connected_services and 'connected-services' in caller['user_groups']:
        return None
    if (
        not caller['is_admin']
        and not caller['is_operator']
        and not caller['is_viewer']
        and not caller['is_guest']
    ):
        return _resp(403, {'error': 'Requires fleet-operator, fleet-viewer, or platform-admin.'})
    return None


def _authorize_listing(caller: dict) -> Optional[dict]:
    """Return 403 for driver-self or unauthenticated (groupless) callers.

    Used by /list, /drivers, /campaigns — listing routes that return filtered results
    for operator/viewer but full results for admin.  Driver-self is always denied.
    """
    if caller['is_driver_self']:
        return _resp(403, {'error': 'Driver-self tokens may not call the simulation API.'})
    if (
        not caller['is_admin']
        and not caller['is_operator']
        and not caller['is_viewer']
    ):
        return _resp(403, {'error': 'Requires fleet-operator, fleet-viewer, or platform-admin.'})
    return None


def _resolve_vehicle_id_to_vin(vehicle_id: str) -> Optional[str]:
    """Look up the VIN for a vehicleId from the vehicles table.

    Reads cms-{STAGE}-storage-vehicles for the `vin` attribute.
    Returns the VIN string, or None if the vehicle does not exist or has no VIN.

    IAM: sim Lambda role already has read access to cms-{STAGE}-storage-vehicles
    via the existing _set_faults and _get_faults paths (same table, same key).
    No new IAM grants required.
    """
    try:
        vehicles_table = ddb.Table(f"cms-{STAGE}-storage-vehicles")
        resp = vehicles_table.get_item(Key={"vehicleId": vehicle_id})
        item = resp.get("Item")
        if not item:
            return None
        return item.get("vin") or None
    except Exception as e:
        print(f"⚠ _resolve_vehicle_id_to_vin({vehicle_id!r}): {type(e).__name__}: {e}")


#: Mirrors main_api/index.py's `_VALID_DATA_SOURCES` / `_is_cloud_telemetry` /
#: `_classify_vehicle` exactly — same enum, same precedence
#: (dataSource first, oem_source == 'oem1' as legacy fallback). Duplicated
#: rather than imported: this Lambda is bundled and deployed separately
#: from main_api, and a cross-module import across two independently
#: deployed Lambdas would couple their release cadence for no benefit.
#: If the source-of-truth enum in main_api/index.py ever changes, this
#: copy must be updated alongside it — same discipline this repo already
#: applies to `_lib/source_dispatch.py`'s own single-source-of-truth
#: comment, just without a shared import path available here.
_SIM_VALID_DATA_SOURCES = frozenset({
    'vehicle-telemetry', 'cloud-telemetry',
    'onboard-fwe', 'cloud-oem1',
})


def _sim_is_cloud_telemetry(data_source: str) -> bool:
    return data_source in {'cloud-telemetry', 'cloud-oem1'}


def _vehicle_is_onboard(vehicle_id: str) -> Optional[bool]:
    """Return whether *vehicle_id* is classified Onboard, or None if its
    classification cannot be determined.

    Backs the `/agent/start` route's classification gate —
    issues/2026-09-18-start-agent-button-shown-for-offboard-vehicles/.
    Starting the onboard FWE agent for an Offboard-classified vehicle would
    write onboard-shaped telemetry into the canonical table for a vehicle
    whose data is supposed to come from a cloud producer, recreating the
    exact shape-mixing defect
    issues/2026-09-18-meridian-vehicles-misclassified-onboard-generator-died/
    fixed. The frontend already hides the "Start Agent" button for
    non-Onboard vehicles (`VehicleDetailView.tsx`'s
    `canUseOnboardAgentActions`) — this is the backend half of that same
    fix, because a hidden button is not a security boundary; a direct API
    call must be denied independently.

    None (not False) covers both "vehicle not found" and "no dataSource
    and no oem_source hint" — an unclassifiable vehicle's onboard status is
    unknown by definition, and the caller must deny on None just as it
    denies on False. Collapsing None into False here would read as a
    confident "this vehicle is Offboard" when the honest answer is "cannot
    tell", which is the same false-precision issue commit history in this
    file has flagged for the campaign-follows-enrollment banner's own
    three-state (`true | false | null`) design.
    """
    try:
        vehicles_table = ddb.Table(f"cms-{STAGE}-storage-vehicles")
        resp = vehicles_table.get_item(Key={"vehicleId": vehicle_id})
        item = resp.get("Item")
    except Exception as e:
        print(f"⚠ _vehicle_is_onboard({vehicle_id!r}): {type(e).__name__}: {e}")
        return None
    if not item:
        return None
    data_source = item.get("dataSource")
    if isinstance(data_source, str) and data_source in _SIM_VALID_DATA_SOURCES:
        return not _sim_is_cloud_telemetry(data_source)
    if item.get("oem_source") == "oem1":
        return False
    return None


def _authorize_per_vin(
    caller: dict,
    vehicle_ids: list,
    write_route: bool = False,
    allow_connected_services: bool = False,
    *,
    require_resolved_vehicle_ids: bool = False,
) -> Optional[dict]:
    """Return None if authorized, else a 403/404 _resp().

    Per spec § R2:
      - driver-self  → always 403
      - admin        → unconditional bypass
      - operator/viewer with matching fleet → admitted (viewer denied on write routes)
      - no fleet ids / non-matching fleet  → 403
      - empty/unresolvable vehicleId list on a single-phase route → 403
        (`require_resolved_vehicle_ids=True`; see that arg's docstring)

    IMPORTANT — despite this parameter's original name (`vins`, renamed to
    `vehicle_ids` 2026-09-18) and the T7.5 history below still saying "VIN"
    throughout, the fleet-membership check resolves `vehicleId`s, NOT VINs.
    `resolve_vins_to_fleets` queries `cms-{stage}-storage-fleet-enrollment`'s
    `vehicleId-index` GSI, and that table carries no `vin` attribute at all.
    Every caller of this function was passing a real VIN (silently failing
    for the ~21 of 69 staging vehicles where vehicleId != vin) until
    issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/ fixed each
    call site to pass vehicleId instead. `platform-admin` callers were never
    affected — the `is_admin` bypass below fires before this list is used.
    The T7.5-era `require_resolved_vehicle_ids` keyword name and its "REAL VINs"
    phrasing are kept verbatim below because they document real security
    history (T7.5a/b/c) that predates and is independent of this correction —
    read "VINs" there as "vehicleIds" throughout; the *shape* of the
    single-phase-vs-two-phase distinction they describe is unchanged by this
    fix, only the value being checked is.

    allow_connected_services: when True (set explicitly at /start, /stop/, /status/
      dispatch sites — never inside this function unconditionally), a caller whose
      Cognito group is exactly 'connected-services' is admitted provided every
      resolved vehicleId belongs to CS_ALLOWED_FLEET_ID.  Driver-self is still denied
      first.  The flag is NOT passed at /agent/start or /agent/logs/, so those routes
      are unaffected by the widening (T7.5 constraint).

    require_resolved_vehicle_ids: keyword-only; when True, a caller on EITHER the
      connected-services branch or the fleet-operator/viewer branch that presents an
      empty or non-resolvable vehicleId list is DENIED (403) rather than admitted via
      the fast-path.  Pass True at every call site that carries REAL
      vehicleIds — that is POST /start (single-phase, no downstream re-check) and the
      SECOND-phase calls of /stop and /status (after vehicleIds are read from
      SIM_TABLE).  Do NOT pass it on the FIRST call of /stop or /status: those
      intentionally pass [] as an early group/viewer check before paying for a
      SIM_TABLE read, and must remain admitted via the fast-path.
      History, because getting this split wrong is the whole reason the flag exists:
      T7.5b introduced it for /start only, and the identical bypass survived on the
      two-phase routes' second call — a persisted row whose config yields no
      extractable vehicleIds also produces sim_vins == []. T7.5c closed that. The
      rule is "real vehicleIds required", not "/start only".
      Renamed from `cs_require_resolved_vins` 2026-09-20: T7.5a-c applied it to the
      connected-services branch ONLY, and the operator/viewer branch below kept its
      own admit-on-empty fast-path — a fleet-scoping bypass reachable by omitting
      `vehicles` from a POST /start body, or sending a non-list value, which makes
      `_start` fall through to its `config.get("vehicles", 10)` default and simulate
      10 vehicles the caller never named and holds no fleet authority over.  Closed by
      issues/2026-09-13-simulation-start-empty-vehicles-bypasses-fleet-scoping/.  The
      `cs_` prefix was dropped because a flag governing both branches must not read as
      CS-specific — the old name is exactly why the operator branch was overlooked for
      five days.

    Args:
        caller: caller dict from _extract_caller()
        vehicle_ids: list of vehicleIds to check fleet membership for
        write_route: if True, fleet-viewer is denied (viewers are read-only)
        allow_connected_services: if True, admit connected-services on the allowlisted fleet
        require_resolved_vehicle_ids: if True, deny (403) ANY non-admin caller whose
            vehicleId list is empty — both the CS and the operator/viewer branches
    """
    if caller['is_driver_self']:
        return _resp(403, {'error': 'Driver-self tokens may not call the simulation API.'})
    if caller['is_admin']:
        return None
    # ── connected-services path (T7.5) — only when explicitly enabled per dispatch ──
    # fleet-viewer is read-only; deny on write routes even in the CS branch (T7.5a).
    if (allow_connected_services and 'connected-services' in caller['user_groups']
            and not (write_route and caller['is_viewer'])):
        clean_vehicle_ids = [v for v in vehicle_ids if v]
        if not clean_vehicle_ids:
            # Fast-path for two-phase routes (/stop, /status): the caller passes an
            # empty vehicleId list on the pre-check, and the actual fleet check fires
            # on the second call after vehicleIds are read from SIM_TABLE.
            # /start is single-phase — it has NO downstream re-check — so passing
            # require_resolved_vehicle_ids=True at that dispatch site makes this path
            # deny instead of admit, closing the fleet-allowlist bypass (C1).
            if require_resolved_vehicle_ids:
                return _resp(403, {'error': 'connected-services: vehicles must be a non-empty list of vehicleIds for this route.'})
            return None
        vehicle_id_to_fleet = resolve_vins_to_fleets(clean_vehicle_ids)
        unresolved = [v for v in clean_vehicle_ids if v not in vehicle_id_to_fleet]
        if unresolved:
            return _resp(404, {'error': f'Unknown vehicle(s): {unresolved}'})
        off_fleet = [v for v, f in vehicle_id_to_fleet.items() if f != CS_ALLOWED_FLEET_ID]
        if off_fleet:
            return _resp(403, {'error': f'connected-services caller not authorized for fleet of vehicle(s): {off_fleet}'})
        return None
    # ── end connected-services path ──────────────────────────────────────────
    if write_route and caller['is_viewer']:
        return _resp(403, {'error': 'Read-only (fleet-viewer) tokens may not call write routes.'})
    if not caller['is_operator'] and not caller['is_viewer']:
        return _resp(403, {'error': 'Requires fleet-operator, fleet-viewer, or platform-admin.'})
    if not caller['user_fleet_ids']:
        return _resp(403, {'error': 'Caller has no fleet assignments.'})
    clean_vehicle_ids = [v for v in vehicle_ids if v]
    if not clean_vehicle_ids:
        # Fast-path for two-phase routes (/stop, /status): their FIRST call
        # deliberately passes [] as an early group/viewer screen before paying for a
        # SIM_TABLE read, and the real fleet check fires on the SECOND call once
        # vehicleIds are known. Those sites must stay admitted here.
        #
        # Single-phase write routes have NO downstream re-check, so admitting on an
        # empty list means NO fleet-membership check runs at all. POST /start is the
        # reachable case: omitting `vehicles` from the body, or sending a non-list
        # value, yields [] at the dispatch site (:487-497) — and `_start` then reads
        # `config.get("vehicles", 10)` on an ABSENT key, defaults to the integer 10,
        # and simulates 10 vehicles the caller never named and holds no fleet
        # authority over. The comment that used to sit here ("let the handler return
        # its own error shape") was the bug: the handler returns no error for this
        # shape, it picks vehicles.
        #
        # This mirrors the connected-services branch above, which has denied the
        # identical shape since T7.5b/c. The operator branch was left out of that fix
        # and the flag's old `cs_`-prefixed name is why — see the docstring.
        # Closes issues/2026-09-13-simulation-start-empty-vehicles-bypasses-fleet-scoping/.
        if require_resolved_vehicle_ids:
            return _resp(403, {'error': 'vehicles must be a non-empty list of vehicleIds for this route.'})
        return None
    vehicle_id_to_fleet = resolve_vins_to_fleets(clean_vehicle_ids)
    unresolved = [v for v in clean_vehicle_ids if v not in vehicle_id_to_fleet]
    if unresolved:
        return _resp(404, {'error': f'Unknown vehicle(s): {unresolved}'})
    off_fleet = [v for v, f in vehicle_id_to_fleet.items() if f not in caller['user_fleet_ids']]
    if off_fleet:
        return _resp(403, {'error': f'Caller not authorized for fleet of vehicle(s): {off_fleet}'})
    return None


def _filter_to_caller_fleets(caller: dict, rows: list, row_to_vehicle_ids_fn) -> list:
    """Filter a list of rows to those whose vehicles belong to the caller's fleets.

    Used by /list, /campaigns, /drivers listing routes.
    Admin callers receive the full list unmodified.
    Non-admin callers receive only rows whose vehicleIds resolve to their fleet(s).

    `row_to_vehicle_ids_fn(row) -> list[str]` extracts the vehicleIds from a
    single row. Despite the historical parameter/callback name ("vin"), the
    fleet-membership check underneath (`resolve_vins_to_fleets`) resolves
    vehicleIds, not VINs — see
    issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/.
    """
    if caller['is_admin']:
        return rows
    if not caller['user_fleet_ids']:
        return []
    result = []
    for row in rows:
        vehicle_ids = row_to_vehicle_ids_fn(row)
        if not vehicle_ids:
            continue
        vehicle_id_to_fleet = resolve_vins_to_fleets(vehicle_ids)
        if not vehicle_id_to_fleet:
            continue  # unresolvable row — fail-closed for non-admin
        if all(f in caller['user_fleet_ids'] for f in vehicle_id_to_fleet.values()):
            result.append(row)
    return result

# ── End authorization helpers ─────────────────────────────────────────────────


def handler(event, context):
    try:
        return _handle(event)
    except Exception as e:
        print(f"Unhandled error: {e}")
        return _resp(500, {"error": str(e)})


def _handle(event):
    path = event.get("path", "")
    method = event.get("httpMethod", "")

    if method == "OPTIONS":
        return _resp(200, {})

    if path.endswith("/health"):
        return _resp(200, {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()})

    # ── Identity extraction — once at dispatch entry per spec § R1 ────────────
    # All routes below this point share the same caller dict.  OPTIONS and
    # /health are unauthenticated and are handled above.
    caller = _extract_caller(event)

    if path.endswith("/agent/start") and method == "POST":
        config = json.loads(event.get("body", "{}"))
        # Fleet-membership check is keyed on vehicleId, not vin — see
        # issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/.
        denied = _authorize_per_vin(caller, [config.get("vehicleId")], write_route=True,
                                    allow_connected_services=True,
                                    require_resolved_vehicle_ids=True)
        if denied:
            return denied
        # Classification gate — issues/2026-09-18-start-agent-button-shown-
        # for-offboard-vehicles/. Deny on both False (confirmed Offboard)
        # and None (unresolvable/unclassifiable) — fail closed, same
        # direction _cs_resolve_vin_to_vehicle_id() and this route's own
        # fleet-membership check already take on an unresolvable input.
        if _vehicle_is_onboard(config.get("vehicleId")) is not True:
            return _resp(403, {
                "error": "Starting the onboard FWE agent is only available "
                         "for vehicles classified Onboard.",
            })
        return _agent_start(config)

    if path.endswith("/agent/stop") and method == "POST":
        stop_config = json.loads(event.get("body", "{}"))
        # Two scopes, two gates. A vehicle-scoped stop is a per-vehicle write and
        # is available to a connected-services caller on the allowlisted fleet; the
        # untargeted form stops EVERY agent task in the cluster and stays
        # admin-only. require_resolved_vehicle_ids=True because this route is
        # single-phase — there is no downstream re-check, so admitting on an
        # unresolvable target would mean no fleet check ran at all.
        # See issues/2026-09-23-agent-routes-do-not-authorize-connected-services-callers/.
        if stop_config.get("vehicleId"):
            denied = _authorize_per_vin(caller, [stop_config.get("vehicleId")],
                                        write_route=True,
                                        allow_connected_services=True,
                                        require_resolved_vehicle_ids=True)
        else:
            denied = _authorize_admin_only(caller)
        if denied:
            return denied
        return _agent_stop(stop_config)

    if path.endswith("/agent/status") and method == "GET":
        # Connected-services callers are admitted here but SCOPED. The
        # unrestricted response enumerates every running agent's VIN across every
        # fleet; a CS caller is confined to one fleet everywhere else in this API,
        # so returning the full list would trade a 403 for a cross-fleet leak.
        # Admin/operator/viewer keep the unrestricted view.
        denied = _authorize_authenticated(caller, allow_connected_services=True)
        if denied:
            return denied
        cs_scoped = (
            'connected-services' in caller['user_groups']
            and not caller['is_admin']
            and not caller['is_operator']
            and not caller['is_viewer']
        )
        return _agent_status(restrict_to_fleet=CS_ALLOWED_FLEET_ID if cs_scoped else None)

    if "/agent/logs/" in path and method == "GET":
        vin = path.split("/agent/logs/")[-1].split("?")[0]
        # The route is keyed by VIN but fleet membership resolves on vehicleId —
        # a VIN never matches the vehicleId-keyed index, so authorizing on the raw
        # VIN would 404 every caller. Resolve first, then authorize on the
        # vehicleId. An unresolvable VIN yields [] which, with
        # require_resolved_vehicle_ids=True, denies rather than admits.
        # See issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/.
        logs_vehicle_id = _resolve_vin_to_vehicle_id(vin)
        denied = _authorize_per_vin(caller,
                                    [logs_vehicle_id] if logs_vehicle_id else [],
                                    allow_connected_services=True,
                                    require_resolved_vehicle_ids=True)
        if denied:
            return denied
        return _agent_logs(vin)

    # NOTE on the `vehicles` payload shape, for the /start route below.
    # It arrives in two shapes from two different UI callers, and this boundary
    # must tolerate both:
    #   FleetSimulationPanel.tsx  -> [{"vin": ..., "vehicleId": ...}, ...]
    #   TripSimulatorModal.tsx    -> ["VIN", ...]   (bare strings)
    # The original extraction was `v.get("vin") or v`, which cannot work: Python
    # evaluates `.get()` before the `or`, so a str element raised
    # `'str' object has no attribute 'get'` and every Trip Simulator run 500'd.
    # See issues/2026-09-01-trip-simulator-str-has-no-attribute-get/.
    # Keep the extraction and the _authorize_per_vin call tight together —
    # TestAuthzSourceInvariants::test_route_coverage_invariant requires an
    # _authorize_* call within 10 lines of each route dispatch.
    if path.endswith("/start") and method == "POST":
        config = json.loads(event.get("body", "{}"))
        vehicles = config.get("vehicles", [])
        # Fleet-membership checks are keyed on vehicleId, NOT vin — see
        # issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/.
        # cms-{stage}-storage-fleet-enrollment's vehicleId-index has no `vin`
        # attribute at all; passing a VIN here silently fails the fleet check
        # for the ~21 of 69 staging vehicles where vehicleId != vin (a
        # platform-admin caller never notices — _authorize_per_vin short-
        # circuits on is_admin before this list is ever used).
        vehicle_ids_for_authz = [
            (v.get("vehicleId") if isinstance(v, dict) else v) for v in vehicles
        ] if isinstance(vehicles, list) else []
        denied = _authorize_per_vin(caller, vehicle_ids_for_authz, write_route=True, allow_connected_services=True, require_resolved_vehicle_ids=True)
        if denied:
            return denied
        return _start(config)

    if "/stop/" in path and method == "POST":
        sim_id = path.split("/stop/")[-1]
        # Early deny: check group membership + viewer exclusion before reading SIM_TABLE.
        # _authorize_per_vin with empty vins returns None for admin/operator-with-fleet,
        # 403 for driver-self, viewer (write_route), groupless, or no-fleet-ids.
        # Invariant: for a CS caller the fleet check fires only on the SECOND call
        # (below), and only when sim_vins is non-empty (require_resolved_vehicle_ids=True).
        # This pre-check intentionally passes [] — it screens out unauthorized groups
        # before we pay the cost of reading SIM_TABLE.
        pre_check = _authorize_per_vin(caller, [], write_route=True, allow_connected_services=True)
        if pre_check:
            return pre_check
        # Read sim row to extract VINs for per-fleet authorization; 404 if nonexistent.
        sim_raw = SIM_TABLE.get_item(Key={"simulationId": sim_id})
        sim_item = sim_raw.get("Item") if isinstance(sim_raw, dict) else None
        if not sim_item or not isinstance(sim_item, dict):
            return _resp(404, {"error": f"Simulation {sim_id} not found."})
        try:
            sim_config = json.loads(sim_item.get("config", "{}") or "{}")
        except (TypeError, ValueError):
            sim_config = {}
        sim_vehicles = sim_config.get("vehicles", [])
        # T7.5a: resolve_vins_to_fleets queries vehicleId-index on vehicleId.
        # Persisted sim rows carry {"vehicleId": ..., "vin": ...}; use vehicleId
        # when present, fall back to vin for legacy rows that carry only one key.
        sim_vins = [
            v.get("vehicleId") or v.get("vin")
            for v in sim_vehicles
            if isinstance(v, dict) and (v.get("vehicleId") or v.get("vin"))
        ]
        denied = _authorize_per_vin(caller, sim_vins, write_route=True, allow_connected_services=True, require_resolved_vehicle_ids=True)
        if denied:
            return denied
        return _stop(sim_id)

    if "/status/" in path and method == "GET":
        sim_id = path.split("/status/")[-1]
        # Early deny: check group membership before reading SIM_TABLE.
        # Invariant: for a CS caller the fleet check fires only on the SECOND call
        # (below), and only when sim_vins is non-empty (require_resolved_vehicle_ids=True).
        # This pre-check intentionally passes [] — it screens out unauthorized groups
        # before we pay the cost of reading SIM_TABLE.
        pre_check = _authorize_per_vin(caller, [], allow_connected_services=True)
        if pre_check:
            return pre_check
        # Read sim row to extract VINs for per-fleet authorization; 404 if nonexistent.
        sim_raw = SIM_TABLE.get_item(Key={"simulationId": sim_id})
        sim_item = sim_raw.get("Item") if isinstance(sim_raw, dict) else None
        if not sim_item or not isinstance(sim_item, dict):
            return _resp(404, {"error": f"Simulation {sim_id} not found."})
        try:
            sim_config = json.loads(sim_item.get("config", "{}") or "{}")
        except (TypeError, ValueError):
            sim_config = {}
        sim_vehicles = sim_config.get("vehicles", [])
        # T7.5a: same vehicleId-first extraction as /stop (see above).
        sim_vins = [
            v.get("vehicleId") or v.get("vin")
            for v in sim_vehicles
            if isinstance(v, dict) and (v.get("vehicleId") or v.get("vin"))
        ]
        denied = _authorize_per_vin(caller, sim_vins, allow_connected_services=True, require_resolved_vehicle_ids=True)
        if denied:
            return denied
        return _status(sim_id)

    if path.endswith("/list"):
        denied = _authorize_listing(caller)
        if denied:
            return denied
        list_resp = _list()
        if not caller["is_admin"] and list_resp.get("statusCode") == 200:
            body = json.loads(list_resp["body"])
            sims = body.get("simulations", [])
            def _sim_to_vehicle_ids(sim):
                # Fleet-membership check is keyed on vehicleId, not vin — see
                # issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/.
                vehicles = sim.get("config", {}).get("vehicles", [])
                if isinstance(vehicles, list):
                    return [v.get("vehicleId") for v in vehicles if isinstance(v, dict) and v.get("vehicleId")]
                return []
            filtered = _filter_to_caller_fleets(caller, sims, _sim_to_vehicle_ids)
            body["simulations"] = filtered
            body["count"] = len(filtered)
            list_resp = _resp(200, body)
        return list_resp

    if path.endswith("/drivers"):
        denied = _authorize_listing(caller)
        if denied:
            return denied
        drivers_resp = _drivers()
        if not caller["is_admin"] and drivers_resp.get("statusCode") == 200:
            body = json.loads(drivers_resp["body"])
            drivers = body.get("drivers", [])
            # Filter drivers to those assigned to the caller's fleets.
            # Drivers with no fleetId visible to the caller are excluded.
            def _driver_to_fleets(driver):
                fleet_id = driver.get("fleetId") or driver.get("fleet_id") or ""
                return [fleet_id] if fleet_id else []
            if caller["user_fleet_ids"]:
                filtered = [
                    d for d in drivers
                    if any(
                        fid in caller["user_fleet_ids"]
                        for fid in _driver_to_fleets(d)
                    )
                ]
            else:
                filtered = []
            body["drivers"] = filtered
            body["count"] = len(filtered)
            drivers_resp = _resp(200, body)
        return drivers_resp

    if path.endswith("/presets"):
        denied = _authorize_authenticated(caller)
        if denied:
            return denied
        return _presets()

    if path.endswith("/discover-iot-endpoint"):
        denied = _authorize_authenticated(caller)
        if denied:
            return denied
        return _discover_iot()

    if path.endswith("/campaigns") and method == "GET":
        denied = _authorize_listing(caller)
        if denied:
            return denied
        campaigns_resp = _campaigns()
        if not caller["is_admin"] and campaigns_resp.get("statusCode") == 200:
            body = json.loads(campaigns_resp["body"])
            campaigns = body.get("campaigns", {})
            # campaigns is a dict keyed by vin/broadcast; filter to caller fleets
            if caller["user_fleet_ids"] and isinstance(campaigns, dict):
                vin_keys = [k for k in campaigns if k != "_broadcast"]
                vin_to_fleet = resolve_vins_to_fleets(vin_keys) if vin_keys else {}
                filtered = {
                    k: v for k, v in campaigns.items()
                    if k == "_broadcast"
                    or vin_to_fleet.get(k) in caller["user_fleet_ids"]
                }
                body["campaigns"] = filtered
                campaigns_resp = _resp(200, body)
            else:
                body["campaigns"] = {}
                campaigns_resp = _resp(200, body)
        return campaigns_resp

    # ── Fault injection routes ────────────────────────────────────────────
    # PUT/GET /api/simulation/vehicle/{vehicleId}/faults
    # Retrofitted per spec § R2 + Constraints 2 (Group 3.3) and fully
    # completed per Task 3.5.1:
    #   - Removed the fail-open `or not _user_groups` (closed in Group 3.3).
    #   - Removed the per-faults-only auth helper (no dead code per Task 3.5.1).
    #   - Added _resolve_vehicle_id_to_vin: vehicleId → VIN lookup so
    #     _authorize_per_vin can enforce per-fleet scoping on this route.
    #   - Returns 404 (before authz check) if vehicle_id resolves to no VIN.
    #   - GET /faults: authenticated callers (operator/viewer/admin) who
    #     belong to the vehicle's fleet are admitted; driver-self 403;
    #     groupless 403.
    if "/vehicle/" in path and path.endswith("/faults"):
        vehicle_id = path.split("/vehicle/")[-1].split("/faults")[0]
        # Resolve vehicleId → VIN for per-fleet authorization.
        # 404 before the authz check if vehicleId is unknown.
        resolved_vin = _resolve_vehicle_id_to_vin(vehicle_id)
        if resolved_vin is None:
            return _resp(404, {"error": f"Vehicle {vehicle_id!r} not found."})
        if method == "PUT":
            # Fault injection is per-fleet: deny driver-self, viewer (write_route=True),
            # groupless, and operators whose fleet does not include the vehicle's fleet.
            denied = _authorize_per_vin(caller, [resolved_vin], write_route=True)
            if denied:
                return denied
            body = json.loads(event.get("body") or "{}")
            return _set_faults(vehicle_id, body, caller["user_email"])
        if method == "GET":
            # GET /faults is a read — viewer admitted if in fleet.
            denied = _authorize_per_vin(caller, [resolved_vin])
            if denied:
                return denied
            return _get_faults(vehicle_id)

    return _resp(404, {"error": f"Unknown route: {method} {path}"})


def _write_trip_intent(vehicle_id: str, sim_id: str, trips_count: int,
                       agent_task_arn: str, config: dict) -> None:
    """Write a trip-intent record to the vehicle's DDB item.

    The presence loop running inside the vehicle-ecu sidecar polls its own
    vehicle's item on each idle tick.  On finding a ``tripIntent`` attribute
    it performs a conditional remove (prevents double-trigger) and starts the
    trip phase.

    Intent record shape (written as a map attribute on the vehicles item).
    **Nine keys, in three groups.** The grouping is load-bearing and is enforced by
    ``scripts/check_trip_intent_contract.py``, which fails the build if a key is added
    here without a reader in ``realtime_telemetry_simulator.py``:

    Consumed by the presence loop today:
    {
        "simulationId":  str,   -- used for SIM_TABLE correlation
        "tripsCount":    int,   -- how many trips the presence loop should run
    }

    Per-trip parameters — WRITTEN BUT NOT YET READ (see the comment at the write site,
    and issues/2026-09-20-trip-intent-reuse-path-ignores-city-route-length-scenarios/).
    **Note the absent-input sentinels: these are NOT the argv defaults.** An omitted
    parameter is written as ``""`` / ``0``, whereas the fresh-launch argv path defaults to
    ``seattle`` / ``20``. A reader must map the sentinel to "absent" BEFORE clamping —
    ``max(5, min(60, 0))`` is 5, not 20:
    {
        "city":                 str,        -- target city; "" means absent
        "routeLength":          int,        -- GPS points per trip; 0 means absent.
                                               Effective range when present: [5, 60]
        "safetyScenarios":      list[str],  -- safety event ids to force; [] means none
        "maintenanceScenarios": list[str],  -- maintenance conditions to force; [] means none
    }

    Write-only by design; no reader is expected:
    {
        "requestedAt":   str,   -- ISO-8601 UTC; for observability + TTL reasoning
        "agentTaskArn":  str,   -- ARN of the vehicle-ecu/fwe-agent task that owns
                                   this vehicle; written for observability and future
                                   anti-misrouting logic (not currently validated by
                                   the presence loop — per-vehicle DDB scoping bounds
                                   the impact of a misrouted intent to the vehicle's
                                   own presence loop)
        "uds_dtc_map":   str,   -- always "" today; the UDS path goes via env on
                                   fresh-agent launch, and the reuse path carries it
                                   separately if needed
    }

    Authentication: the Lambda uses its own execution role; the presence loop
    uses the ECS task role.  Both have DDB access on ``{prefix}-storage-*``.
    Neither is a device certificate, so neither route is gated by the
    unscoped ``cms/*`` IoT device policy.

    See decisions.md AMENDMENT (2026-08-04) for why DDB was chosen over the
    MQTT control topic despite both policies permitting it.
    """
    vehicles_table = ddb.Table(f"cms-{STAGE}-storage-vehicles")
    now_ts = datetime.now(timezone.utc).isoformat()
    vehicles_table.update_item(
        Key={"vehicleId": vehicle_id},
        UpdateExpression="SET tripIntent = :i",
        ExpressionAttributeValues={
            ":i": {
                "simulationId": sim_id,
                "tripsCount": trips_count,
                "requestedAt": now_ts,
                "agentTaskArn": agent_task_arn,
                # Per-trip parameters. The presence loop's city/route length are
                # otherwise fixed at CONTAINER START, so a per-trip choice in the UI
                # was silently ignored: a request for Atlanta ran in NYC.
                # See issues/2026-09-01-trip-simulation-zero-telemetry-no-campaign/
                # § "Second, separate defect".
                #
                # ⚠️  THESE FOUR KEYS HAVE NO READER YET. Writing them was staged ahead
                # of the read on a forward-compatibility argument (older sim-service
                # images ignore unknown intent keys, so the write is harmless). The
                # image that reads them was never published, and the result was a
                # SILENT user-visible defect: on a warm onboard agent the reuse path
                # accepts city/route_length/scenarios, returns 200 success, and runs
                # the container's original argv. Measured 2026-09-20 — requested
                # Munich/60, ran Seattle/20.
                # See issues/2026-09-20-trip-intent-reuse-path-ignores-city-route-length-scenarios/
                # and spec .kiro/specs/2026-09-20-trip-intent-param-contract/.
                # `scripts/check_trip_intent_contract.py` now fails the build if a
                # FIFTH such key is added, and forces this comment to shrink as
                # readers land.
                #
                # Note on the argv default, corrected 2026-09-20: `--city` does default
                # to 'nyc' in the simulator's argparse, but an earlier version of this
                # comment also claimed "the task command passes no --city", which is
                # false — `_start()` in this module passes it explicitly when building
                # the ECS task argv. The fresh-launch path is therefore fine; only the
                # reuse path drops these.
                #
                # Cited by function name rather than line number on purpose: the first
                # version of this correction cited a line, and this comment block's own
                # insertion moved that line in the same commit — reproducing, in the fix,
                # the stale-reference defect the fix existed to correct.
                #
                # ⚠️  ABSENT-INPUT SENTINELS DIFFER FROM THE ARGV DEFAULTS. `or ""` / `or 0`
                # below mean an omitted parameter is written as "" / 0, NOT as the argv
                # defaults ('seattle' / 20). A reader must treat those two values as
                # "absent" BEFORE any range clamp: clamping a written 0 into [5, 60]
                # yields 5, silently giving a caller who omitted route length the
                # shortest possible trip rather than the container default.
                "city": config.get("city") or "",
                "routeLength": config.get("route_length") or 0,
                "safetyScenarios": config.get("safety_scenarios") or [],
                "maintenanceScenarios": config.get("maintenance_scenarios") or [],
                "uds_dtc_map": "",   # UDS path goes via env on fresh-agent launch;
                                     # reuse path carries it separately if needed
            },
        },
    )
    print(f"✅ Trip intent written for {vehicle_id}: sim={sim_id}, trips={trips_count}")


def _check_running_tasks(vin):
    """Return task ARN of the FWE *agent* task currently running for this VIN.

    Returns None if no agent task matches. Simulator tasks for the same VIN
    are intentionally ignored — callers (notably _start) want the persistent
    agent's task ARN so they can read its CAN_BUS0 binding, not an in-flight
    simulator's overrides which may carry a stale value from a previous run.

    Filter rationale (issues/2026-05-29-cms-sim-fwe-lifecycle-and-lookup-hardening):
    iteration order over describe_tasks() is non-deterministic. Without the
    'fwe-agent' family scope, a recently-started simulator task whose
    VEHICLE_NAME matches the VIN can be returned ahead of the actual agent.
    Downstream code then misroutes by reading the simulator's CAN_BUS0.
    """
    try:
        task_arns = ecs.list_tasks(cluster=CLUSTER)["taskArns"]
        if not task_arns:
            return None
        tasks = ecs.describe_tasks(cluster=CLUSTER, tasks=task_arns)["tasks"]
        for t in tasks:
            # Scope strictly to FWE agent tasks. The substring match is robust
            # across stage prefixes (cms-staging-fwe-agent, cms-prod-fwe-agent)
            # and across task-definition revisions (...:N).
            if "fwe-agent" not in t.get("taskDefinitionArn", ""):
                continue
            if t["lastStatus"] in ("RUNNING", "PENDING", "PROVISIONING"):
                for c in t.get("overrides", {}).get("containerOverrides", []):
                    for env in c.get("environment", []):
                        if env.get("name") == "VEHICLE_NAME" and env.get("value", "").startswith(vin):
                            return t["taskArn"]
    except Exception:
        pass
    return None


def _resolve_agent_ec2_instance_id(task_arn):
    """Return the EC2 instance id hosting the given ECS task, or None on any failure.

    Flow: ecs.describe_tasks → containerInstanceArn →
          ecs.describe_container_instances → ec2InstanceId.

    Returns None (not raises) so the caller can fall back gracefully per WS4 spec.
    """
    try:
        tasks = ecs.describe_tasks(cluster=CLUSTER, tasks=[task_arn]).get("tasks", [])
        if not tasks:
            return None
        ci_arn = tasks[0].get("containerInstanceArn")
        if not ci_arn:
            return None
        cis = ecs.describe_container_instances(
            cluster=CLUSTER, containerInstances=[ci_arn]
        ).get("containerInstances", [])
        if not cis:
            return None
        return cis[0].get("ec2InstanceId") or None
    except Exception as e:
        print(f"⚠ _resolve_agent_ec2_instance_id({task_arn}): {type(e).__name__}: {e} — proceeding without placement constraint")
        return None


def _resolve_agent_vcan(task_arn):
    """Return the CAN_BUS0 vcan name (e.g., 'vcan2') used by an existing FWE
    agent task, or raise ValueError with a diagnostic message.

    Replaces the prior pattern (issues/2026-05-29-cms-sim-fwe-lifecycle-and-
    lookup-hardening, Bug 3) of a silent fallback to 'vcan0' when discovery
    failed. A misrouted simulator looks healthy but writes to a vcan no
    agent is listening on, producing zero-frame trips with no log signal.

    Failure modes that MUST raise:
        - ecs.describe_tasks() exception (throttle, IAM, transient)
        - tasks list empty (agent stopped between list_tasks and describe_tasks)
        - no containerOverrides on the task
        - no CAN_BUS0 env var in any containerOverride
    """
    try:
        resp = ecs.describe_tasks(cluster=CLUSTER, tasks=[task_arn])
    except Exception as e:
        raise ValueError(
            f"FWE agent vcan discovery failed for task {task_arn}: "
            f"ecs.describe_tasks raised {type(e).__name__}: {e}"
        ) from e

    tasks = resp.get("tasks", [])
    if not tasks:
        failures = resp.get("failures", [])
        raise ValueError(
            f"FWE agent vcan discovery failed for task {task_arn}: "
            f"describe_tasks returned no tasks (failures={failures})"
        )

    overrides = tasks[0].get("overrides", {}).get("containerOverrides", [])
    if not overrides:
        raise ValueError(
            f"FWE agent vcan discovery failed for task {task_arn}: "
            "task has no containerOverrides — cannot determine CAN_BUS0"
        )

    for c in overrides:
        for env in c.get("environment", []):
            if env.get("name") == "CAN_BUS0":
                val = env.get("value", "")
                if val:
                    return val

    raise ValueError(
        f"FWE agent vcan discovery failed for task {task_arn}: "
        "no CAN_BUS0 env var found in any containerOverride"
    )


def _get_used_vcan_indices():
    """Return set of vcan indices in use by running FWE agent tasks."""
    used = set()
    try:
        task_arns = ecs.list_tasks(cluster=CLUSTER)["taskArns"]
        if not task_arns:
            return used
        tasks = ecs.describe_tasks(cluster=CLUSTER, tasks=task_arns)["tasks"]
        for t in tasks:
            if t["lastStatus"] not in ("RUNNING", "PENDING", "PROVISIONING"):
                continue
            for c in t.get("overrides", {}).get("containerOverrides", []):
                for env in c.get("environment", []):
                    if env.get("name") == "CAN_BUS0":
                        val = env.get("value", "")
                        if val.startswith("vcan"):
                            try:
                                used.add(int(val[4:]))
                            except ValueError:
                                pass
    except Exception:
        pass
    return used


def _next_vcan_index():
    """Return the next available vcan index."""
    used = _get_used_vcan_indices()
    idx = 0
    while idx in used:
        idx += 1
    return idx


# ── UDS-DTC support (CP8) ────────────────────────────────────────────
#
# The DTC-prefix → ECU mapping matches the handoff's 9-ECU grouping.
# Each of the 19 demo DTCs in cms-<stage>-event-catalog is routed to
# exactly one of the 9 virtual ECUs wired into FWE's static config
# (CP6) and the decoder manifest (CP3).
#
# This map is *authoritative*: it agrees with (a) the 9 ECUs in the
# FWE static config (simulation_stack.py), (b) the 9 Vehicle.ECUx.DTC_INFO
# signals in the decoder manifest (generate_decoder_manifest.py) and
# signal catalog (signal_catalog_seed.json), and (c) the CAN IDs the
# UDS responder listens on (uds_dtc_responder.py short-form).
#
# If we add ECU-specific DTCs to the event catalog, add the mapping
# here AND extend the _ECU_BY_CODE dict below.
_ECU_BY_NUMBER = {
    1: {"name": "ECU_BRAKE",      "req": "0x7E0", "resp": "0x7E8", "target": 1},
    2: {"name": "ECU_ENGINE",     "req": "0x7E1", "resp": "0x7E9", "target": 2},
    3: {"name": "ECU_POWERTRAIN", "req": "0x7E2", "resp": "0x7EA", "target": 3},
    4: {"name": "ECU_PCM",        "req": "0x7E3", "resp": "0x7EB", "target": 4},
    5: {"name": "ECU_COMM",       "req": "0x7E4", "resp": "0x7EC", "target": 5},
    6: {"name": "ECU_BATTERY_HV", "req": "0x7E5", "resp": "0x7ED", "target": 6},
    7: {"name": "ECU_BATTERY_12V","req": "0x7E6", "resp": "0x7EE", "target": 7},
    8: {"name": "ECU_EVAP",       "req": "0x7E7", "resp": "0x7EF", "target": 8},
    9: {"name": "ECU_BODY",       "req": "0x18DA09F1", "resp": "0x18DAF109", "target": 9},
}

# ── ECU derivation (decisions.md § AMENDMENT: derive the ECU from the DTC prefix) ──
#
# ECU number is derived from the SAE DTC prefix (P/C/B/U) plus a small override
# table for codes whose real subsystem differs from the prefix default.
#
# Prefix defaults:
#   C → ECU1  (ECU_BRAKE)
#   P → ECU2  (ECU_ENGINE)
#   U → ECU5  (ECU_COMM)
#   B → ECU9  (ECU_BODY)
#
# Eight overrides replace the former 27-entry exhaustive dict.  Every future
# catalog code with a standard P/C/B/U prefix resolves without a code change.
# The three now-unreachable entries (C0710, C1213, P0118) are deliberately
# NOT ported — they had no catalog event_id and could never be injected.

_ECU_PREFIX_DEFAULT = {
    "C": 1,  # Chassis  → ECU_BRAKE
    "P": 2,  # Powertrain → ECU_ENGINE (default; many P-codes override below)
    "U": 5,  # Network/comms → ECU_COMM
    "B": 9,  # Body → ECU_BODY
}

# Overrides — codes whose real subsystem differs from the prefix default.
# Exactly 8 entries per decisions.md, each paired with a sibling for legibility.
_ECU_CODE_OVERRIDE = {
    "P0700": 3,  # transmission, distinct from engine → ECU_POWERTRAIN
    "P0606": 4,  # PCM processor fault → ECU_PCM
    "P0607": 4,  # pairs P0606 — both control-module faults → ECU_PCM
    "P0A80": 6,  # HV battery thermal → ECU_BATTERY_HV
    "P0562": 7,  # 12V battery low → ECU_BATTERY_12V
    "P0620": 7,  # pairs P0562 — generator/charge circuit → ECU_BATTERY_12V
    "P0442": 8,  # EVAP small leak → ECU_EVAP
    "P205B": 8,  # pairs P0442 — DEF/SCR emissions aftertreatment → ECU_EVAP
}


def _resolve_ecu_from_code(dtc_code: str) -> int:
    """Return the ECU number for a DTC code using prefix derivation + override table.

    Derivation (decisions.md § AMENDMENT: derive the ECU from the DTC prefix):
      1. Check the 8-entry override table — explicit mapping wins.
      2. Look up the SAE prefix (first character) in _ECU_PREFIX_DEFAULT.
      3. Raise ValueError/KeyError if the prefix is not P/C/B/U.

    Args:
        dtc_code: a raw DTC string, e.g. "P0420" or "C1234".

    Returns:
        ECU number (1-9).

    Raises:
        ValueError or KeyError for an unknown or unsupported prefix.
    """
    if dtc_code in _ECU_CODE_OVERRIDE:
        return _ECU_CODE_OVERRIDE[dtc_code]
    prefix = dtc_code[0].upper() if dtc_code else ""
    if prefix not in _ECU_PREFIX_DEFAULT:
        raise ValueError(
            f"DTC code {dtc_code!r} has unsupported prefix {prefix!r}; "
            f"expected one of P, C, B, U"
        )
    return _ECU_PREFIX_DEFAULT[prefix]


# Guard the uds_dtc_responder import: the Lambda environment ships only
# the Python source; can/isotp C extensions may be absent in some test
# environments.  The import is attempted once at module load; callers that
# need encode_dtc use _is_dtc_encodable(), which handles ImportError.
import sys as _sys
import os as _os
_RESPONDER_DIR = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
if _RESPONDER_DIR not in _sys.path:
    _sys.path.insert(0, _RESPONDER_DIR)

try:
    import uds_dtc_responder as _udr_module
    _encode_dtc_fn = _udr_module.encode_dtc
except ImportError:
    _encode_dtc_fn = None


def _is_dtc_encodable(dtc_code: str) -> bool:
    """Return True iff uds_dtc_responder.encode_dtc() accepts the code.

    This is the API-boundary gate for injectable codes.  It MUST call
    encode_dtc() rather than restate its rule — a test enforces this coupling
    so the Lambda gate and the responder cannot drift independently.

    When uds_dtc_responder is unavailable (missing can/isotp in test env),
    falls back to the minimal 5-char rule that encode_dtc itself checks first.
    The coupling test skips its encode_dtc half in that environment; the
    _is_dtc_encodable half still runs and will agree because the fallback
    mirrors the function's first guard.
    """
    if _encode_dtc_fn is not None:
        try:
            _encode_dtc_fn(dtc_code)
            return True
        except (ValueError, Exception):
            return False
    # Fallback when can/isotp are absent: mirror encode_dtc's first check.
    # This is a best-effort approximation — if encode_dtc ever adds more
    # validation beyond length+charset, this fallback may diverge.  The
    # real function is always used in production (where can/isotp are present).
    if not dtc_code or len(dtc_code) != 5:
        return False
    letter = dtc_code[0].upper()
    hex_part = dtc_code[1:]
    try:
        int(hex_part, 16)
    except ValueError:
        return False
    return letter in ("P", "C", "B", "U")

# Signal IDs 901..909 matching CP3 (decoder manifest) and CP7 (catalog).
_ECU_SIGNAL_ID = {n: 900 + n for n in range(1, 10)}


def _build_uds_dtc_map(maintenance_scenarios):
    """Build the UDS_DTC_MAP JSON and the campaign signalsToFetch list
    from a list of maintenance scenario event_ids.

    Args:
        maintenance_scenarios: list of event_id strings like
            ["maintenance.brake_system_fault", "maintenance.coolant_critical_overheat"]

    Returns:
        (uds_dtc_map, signals_to_fetch, ecus_in_play) tuple where:
        - uds_dtc_map: dict ready to serialize as UDS_DTC_MAP env var
          for uds_dtc_responder.py. Keyed by "ECU1".."ECU9".
        - signals_to_fetch: list of dicts in the shape CampaignSyncProcessor's
          buildFetchInformation() expects (signalId, functionName, params).
          Only ECUs that have at least one DTC get a fetch entry — no
          point querying an empty ECU.
        - ecus_in_play: set of ECU numbers that will be queried. Used
          for the campaign's signalsToCollect so FWE knows to collect
          the resulting DTC_INFO STRING signals.
    """
    if not maintenance_scenarios:
        return {}, [], set()

    # Look up dtc_code for each selected event from cms-<stage>-event-catalog
    event_table = ddb.Table(f"cms-{STAGE}-event-catalog")
    dtcs_by_ecu = {}  # ECU# → [DTC codes]
    missing = []
    for event_id in maintenance_scenarios:
        try:
            item = event_table.get_item(Key={"event_id": event_id}).get("Item", {})
        except Exception as e:
            print(f"event_catalog lookup failed for {event_id}: {e}")
            continue
        dtc = item.get("dtc_code")
        if not dtc:
            # Event has no DTC (some maintenance events are threshold-only) — skip
            continue
        try:
            ecu_num = _resolve_ecu_from_code(dtc)
        except (ValueError, KeyError):
            missing.append((event_id, dtc))
            continue
        dtcs_by_ecu.setdefault(ecu_num, []).append(dtc)

    if missing:
        print(f"⚠️ DTCs with unresolvable ECU prefix: {missing}")

    # Build UDS_DTC_MAP in long form (with explicit req/resp IDs).
    # uds_dtc_responder.py accepts both short form (auto-assigns IDs from index)
    # and long form; using long form here is more explicit + forward-compatible
    # if the ECU numbering ever changes.
    uds_dtc_map = {}
    for ecu_num, codes in sorted(dtcs_by_ecu.items()):
        ecu_cfg = _ECU_BY_NUMBER[ecu_num]
        uds_dtc_map[f"ECU{ecu_num}"] = {
            "req": ecu_cfg["req"],
            "resp": ecu_cfg["resp"],
            "dtcs": codes,
        }

    # Build signalsToFetch — one entry per ECU being queried.
    # Params are [targetAddress, subfunction, statusMask]:
    # - targetAddress = ECU number (int). FWE's RemoteDiagnosticDataSource
    #   passes this to ExampleUDSInterface.findTargetAddress.
    # - subfunction = 2 → UDS 0x19 0x02 reportDTCByStatusMask
    # - statusMask = -1 → any status (FWE treats -1 specially)
    signals_to_fetch = []
    for ecu_num in sorted(dtcs_by_ecu.keys()):
        signals_to_fetch.append({
            "signalId": _ECU_SIGNAL_ID[ecu_num],
            "functionName": "DTC_QUERY",
            "params": [ecu_num, 2, -1],
            # Fire every 10s — matches the uds-dtc-polling campaign cadence.
            "executionFrequencyMs": 30_000,
            "maxExecutionCount": 0,
        })

    return uds_dtc_map, signals_to_fetch, set(dtcs_by_ecu.keys())


def _ensure_telemetry_campaign(vin):
    """Ensure the vehicle has a RUNNING telemetry campaign before a trip starts.

    ``_ensure_uds_campaign`` states the assumption this function exists to
    guarantee: *"The existing RUNNING campaign continues to drive normal
    telemetry collection."* That assumption silently fails for any vehicle that
    was never given a baseline campaign — FleetWise Edge collects only signals an
    active collection scheme asks for, so with none it reads the CAN bus and
    discards every frame while reporting ``with 0 documents: []`` in its checkin.

    The observable failure is a trip that emits CAN frames, reaches
    ``status: completed``, and materialises zero telemetry and zero trip rows,
    with no error at any layer. See
    ``issues/2026-09-01-trip-simulation-zero-telemetry-no-campaign/``.

    Coverage is satisfied by ANY of:
      * a RUNNING campaign targeting ``vehicle:{vin}``
      * a RUNNING campaign targeting ``all`` (broadcast)
    Fleet-scoped campaigns (``fleet:{id}``) are deliberately NOT counted: the
    caller does not resolve the vehicle's fleet here, and treating an unverified
    fleet campaign as coverage would reintroduce the silent failure this guard
    exists to remove. Worst case we create a redundant per-vehicle row, which is
    harmless and idempotent.

    Returns:
        (campaign_id, created) on success — ``created`` False when coverage
        already existed. ``(None, False)`` if no campaign could be ensured, which
        the caller MUST surface as an error rather than starting a doomed trip.
    """
    camp_table = ddb.Table(f"cms-{STAGE}-campaigns")

    # ── 1. Already covered? ───────────────────────────────────────────────
    # Query the targetArn-index GSI rather than scanning — scan is unpaginated
    # and will miss coverage once the table exceeds one 1 MB page.
    # Coverage predicate: status == "RUNNING" AND targetArn in (vehicle:{vin}, "all").
    # Templates carry status="ACTIVE" — they do NOT count as coverage.
    # SUSPENDED does NOT count — only a live (RUNNING) campaign covers a vehicle.
    # (See decisions.md § "Coverage requires an ACTIVE campaign, which in this schema
    # means status == 'RUNNING'".)
    # Two queries: one for the per-vehicle assignment, one for the broadcast "all".
    try:
        def _query_gsi_running(target_arn):
            """Return the first RUNNING item for target_arn, following pagination.

            Bounded to _GSI_QUERY_MAX_PAGES pages to prevent an infinite loop when
            a client (or a stubbed test) returns a non-advancing LastEvaluatedKey.
            On exhausting the cap, raises RuntimeError so the caller's fail-closed
            except branch refuses the trip start rather than treating the outcome as
            coverage-absent (which would create a baseline instead).
            """
            kwargs = {
                "IndexName": "targetArn-index",
                "KeyConditionExpression": "targetArn = :t",
                "FilterExpression": "#s = :running",
                "ExpressionAttributeNames": {"#s": "status"},
                "ExpressionAttributeValues": {
                    ":t": target_arn,
                    ":running": "RUNNING",
                },
            }
            for page_num in range(1, _GSI_QUERY_MAX_PAGES + 1):
                resp = camp_table.query(**kwargs)
                item = next(iter(resp.get("Items", [])), None)
                if item is not None:
                    return item
                last_key = resp.get("LastEvaluatedKey")
                if not last_key:
                    return None
                kwargs["ExclusiveStartKey"] = last_key
            # Page cap exhausted — coverage undetermined.  Raise so the except
            # branch (not the absent-coverage branch) handles this.
            raise RuntimeError(
                f"_query_gsi_running: page cap ({_GSI_QUERY_MAX_PAGES}) exhausted "
                f"for target_arn={target_arn!r}; coverage undetermined"
            )

        # Check per-vehicle assignment first, then the broadcast "all" row.
        found = _query_gsi_running(f"vehicle:{vin}") or _query_gsi_running("all")
        if found:
            cid = found.get("campaignId", "?")
            print(f"✓ Telemetry campaign already covers {vin}: {cid}")
            return cid, False
    except Exception as e:
        # A query failure must not silently start a doomed trip.
        print(f"⚠ _ensure_telemetry_campaign: coverage query failed for {vin}: "
              f"{type(e).__name__}: {e}")
        return None, False

    # ── 2. Derive from the template, else fall back to the known-good shape ──
    signals_to_collect = None
    scheme = {"type": "TIME_BASED", "periodMs": 30000}
    decoder = "cms-fleet-v3"
    try:
        t = camp_table.scan(
            FilterExpression="campaignName = :n AND targetArn = :t",
            ExpressionAttributeValues={
                ":n": _TELEMETRY_TEMPLATE, ":t": "template"},
        ).get("Items", [])
        if t:
            tpl = t[0]
            signals_to_collect = tpl.get("signalsToCollect")
            scheme = tpl.get("collectionScheme", scheme)
            decoder = tpl.get("decoderManifestId", decoder)
    except Exception as e:
        print(f"⚠ _ensure_telemetry_campaign: template lookup failed: "
              f"{type(e).__name__}: {e}")

    if not signals_to_collect:
        # No template. Refuse rather than invent a signal list — a campaign
        # asking for signals the decoder manifest does not define collects
        # nothing, which is the same silent failure wearing a different hat.
        print(f"⚠ _ensure_telemetry_campaign: no '{_TELEMETRY_TEMPLATE}' template "
              f"row in cms-{STAGE}-campaigns; cannot ensure coverage for {vin}")
        return None, False

    campaign_id = f"{_TELEMETRY_TEMPLATE}-{vin}"
    item = {
        "campaignId": campaign_id,
        "campaignName": _TELEMETRY_TEMPLATE,
        "targetArn": f"vehicle:{vin}",
        "status": "RUNNING",
        "decoderManifestId": decoder,
        "collectionScheme": scheme,
        "signalsToCollect": signals_to_collect,
        "signalCount": len(signals_to_collect),
        "category": "telemetry",
        "description": (
            f"Baseline telemetry campaign auto-ensured at trip start for {vin}"
        ),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "source": "_ensure_telemetry_campaign",
        "owner": "platform",
    }
    try:
        camp_table.put_item(Item=item)
        print(f"✓ Ensured baseline telemetry campaign {campaign_id} for {vin} "
              f"({len(signals_to_collect)} signals, decoder={decoder})")
        return campaign_id, True
    except Exception as e:
        print(f"⚠ Failed to ensure telemetry campaign {campaign_id}: "
              f"{type(e).__name__}: {e}")
        return None, False


def _ensure_uds_campaign(vin, signals_to_fetch, ecus_in_play, sim_id):
    """Upsert an ephemeral per-vehicle campaign row that adds the UDS
    signalsToFetch to what CampaignSyncProcessor will deliver to FWE.

    We don't modify the user's existing RUNNING campaign — instead we
    create a second, trip-specific campaign row with the same decoder
    manifest but augmented with the DTC fetch actions. The existing
    RUNNING campaign continues to drive normal telemetry collection.

    Args:
        vin: vehicle VIN (the FWE thing name).
        signals_to_fetch: list of dicts from _build_uds_dtc_map.
        ecus_in_play: set of ECU numbers being queried.
        sim_id: simulation UUID (used to tag the ephemeral campaign
            for future cleanup).

    Returns:
        The campaign_id (string) that was created.
    """
    if not signals_to_fetch:
        return None

    camp_table = ddb.Table(f"cms-{STAGE}-campaigns")
    campaign_id = f"uds-dtc-{vin[:12]}-{sim_id}"

    # signalsToCollect: the 9 DTC_INFO signals that this campaign fetches.
    # Without these, FWE will fire the DTC_QUERY actions but won't actually
    # collect the resulting string values into captured_signals.
    signals_to_collect = [_ECU_SIGNAL_ID[n] for n in sorted(ecus_in_play)]

    # Match the shape CampaignSyncProcessor.buildScheme expects.
    # Reuse the same decoder manifest name as the default fleet campaign
    # so FWE matches the decoder_sync_id correctly.
    item = {
        "campaignId": campaign_id,
        "targetArn": f"vehicle:{vin}",
        "status": "RUNNING",
        "decoderManifestId": "cms-fleet-v3",
        "campaignName": campaign_id,
        "collectionScheme": {"type": "TIME_BASED", "periodMs": 30000},
        "signalsToCollect": signals_to_collect,
        "signalsToFetch": signals_to_fetch,
        "signalCount": len(signals_to_collect),
        "description": f"Ephemeral UDS-DTC campaign for sim {sim_id}, ECUs={sorted(ecus_in_play)}",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "simulationId": sim_id,  # for future cleanup in _stop
        "owner": "platform",
    }
    try:
        camp_table.put_item(Item=item)
        print(f"✓ Ephemeral UDS campaign: {campaign_id} "
              f"(ECUs={sorted(ecus_in_play)}, fetches={len(signals_to_fetch)})")
        return campaign_id
    except Exception as e:
        print(f"⚠ Failed to write ephemeral UDS campaign {campaign_id}: {e}")
        return None


def _delete_uds_campaigns_for_sim(sim_id):
    """Delete ephemeral UDS-DTC campaign rows tagged with this sim_id.

    Cleans up the per-trip campaigns created by ``_ensure_uds_campaign`` in
    ``_start``. Not calling this leaks campaign rows that continue polling
    forever against ``DataFetchManager``, worsening dispatch contention and
    silently pinning UDS poll cadence for the affected vehicle (backlog row
    "Ephemeral campaign leak", filed 2026-08-06 from the parked-DTC
    injection closeout — 4 leaked rows found in staging, oldest from
    2026-06-23).

    Failures here MUST NOT break the user-visible stop response, mirroring
    the existing agent-stop_task error handling in ``_stop``. Logged and
    swallowed.

    Args:
        sim_id: simulation UUID whose ephemeral campaigns to delete.
    """
    camp_table = ddb.Table(f"cms-{STAGE}-campaigns")
    deleted = 0
    scan_kwargs = {
        "FilterExpression": "simulationId = :sid",
        "ExpressionAttributeValues": {":sid": sim_id},
        "ProjectionExpression": "campaignId",
    }
    try:
        while True:
            resp = camp_table.scan(**scan_kwargs)
            for entry in resp.get("Items", []):
                cid = entry.get("campaignId")
                if not cid:
                    continue
                try:
                    camp_table.delete_item(Key={"campaignId": cid})
                    print(f"✓ Deleted ephemeral UDS campaign: {cid}")
                    deleted += 1
                except Exception as e:
                    print(f"⚠ Failed to delete campaign {cid}: "
                          f"{type(e).__name__}: {e}")
            last = resp.get("LastEvaluatedKey")
            if not last:
                break
            scan_kwargs["ExclusiveStartKey"] = last
    except Exception as e:
        # Scan or paginate failed. Log and return — do not let campaign
        # cleanup break the user-visible stop response.
        print(f"_delete_uds_campaigns_for_sim({sim_id}): scan failed: "
              f"{type(e).__name__}: {e}")
        return

    if deleted == 0:
        print(f"_delete_uds_campaigns_for_sim({sim_id}): no ephemeral "
              "UDS campaigns found (mqtt_direct sims or no DTC injection).")


def _resolve_assigned_driver(vehicle_id):
    """Return the driverId currently assigned to vehicle_id, or None.

    Authoritative source is `drivers.assignedVehicleId` — a driver is
    "assigned" when their row points at this vehicle. Mirrors the reverse-
    scan the UI's /api/v1/vehicles/{id} route does (see
    modules/cms_ui/source/handlers/main_api/index.py :: fetch_assigned_driver).

    When multiple drivers point at the same vehicle (data quirk; shouldn't
    normally happen), pick the one with highest safetyScore. This matches
    the UI tiebreak exactly so the CMS UI and the simulator agree on the
    "primary" driver.

    Returns None if no driver is assigned OR if the drivers table isn't
    readable from this Lambda for any reason — callers should fall back to
    their configured selection mode in that case.

    NOTE: we paginate the full scan rather than using a Limit. DDB's
    FilterExpression evaluates AFTER the page-size limit is applied, so a
    `Limit=50` scan on a 75-row table may only check the first 50 rows
    and miss a matching driver that happens to live in the second page.
    Drivers table is small (~75 rows), so a single paginated pass is fast.
    """
    if not vehicle_id:
        return None
    try:
        drivers_tbl = ddb.Table(f"cms-{STAGE}-storage-drivers")
        items = []
        kwargs = {
            "FilterExpression": "assignedVehicleId = :vid",
            "ExpressionAttributeValues": {":vid": vehicle_id},
            "ProjectionExpression": "driverId, safetyScore",
        }
        resp = drivers_tbl.scan(**kwargs)
        items.extend(resp.get("Items", []))
        # Bound pagination defensively — >10 rounds on a drivers table this
        # small would indicate something is very wrong, and we'd rather
        # return a partial answer than spin forever.
        for _ in range(10):
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
            resp = drivers_tbl.scan(**kwargs)
            items.extend(resp.get("Items", []))

        if not items:
            return None

        def _score(it):
            try:
                return float(it.get("safetyScore", 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        best = max(items, key=_score)
        return best.get("driverId")
    except Exception as e:
        print(f"_resolve_assigned_driver({vehicle_id!r}): {type(e).__name__}: {e}")
        return None


def _build_fwe_container_overrides(vehicle_id, vin, can_iface):
    """Return the (fwe_agent_override, vehicle_ecu_override) pair for a new fwe-agent task.

    Both launch paths that start an fwe-agent task — _start (fresh-agent branch) and
    _agent_start — must supply BOTH overrides so the vehicle-ecu sidecar has an explicit
    vehicle identity and the correct vcan interface.  Without the vehicle-ecu override the
    sidecar falls through to "Getting active vehicles from database"
    (realtime_telemetry_simulator.py) and binds to an arbitrary vehicle, corrupting
    telemetry and misrouting commands (Fix Group 8 / Fix Group 9,
    spec 2026-08-04-cms-vehicle-trip-lifecycle-split).

    The caller is responsible for supplying cert_pem, private_key, iot_endpoint, and the
    fwe-agent-specific env vars (CERTIFICATE, PRIVATE_KEY, ENDPOINT_URL, TOPIC_PREFIX) in
    the returned fwe_agent_override — those are per-path and should NOT be added here.

    HARD GATE: do NOT add CERTIFICATE or PRIVATE_KEY to the vehicle_ecu_override here.
    The presence loop obtains credentials via the ECS task IAM role (DynamoDB Scan on
    vehicle-certificates + iot:DescribeEndpoint).  See security review Cycle 1, Warning 2.

    Args:
        vehicle_id: The vehicleId string (e.g. "VEH-MICH-001").
        vin:        The VIN string used as the fwe-agent VEHICLE_NAME.
        can_iface:  The vcan interface name (e.g. "vcan2").  MUST already be resolved by
                    the caller — this helper never defaults to vcan0 on failure (Bug 3 of
                    issues/2026-05-29-cms-sim-fwe-lifecycle-and-lookup-hardening).

    Returns:
        (fwe_agent_base_override, vehicle_ecu_override) — two dicts ready to append to
        containerOverrides.  The fwe_agent_base_override carries only the identity env vars
        (VEHICLE_NAME, CAN_BUS0); the caller must merge in the credential/endpoint env vars.
    """
    vehicle_ecu_vehicle_config = json.dumps(
        [{"vehicleId": vehicle_id, "vin": vin}]
    )
    vehicle_ecu_override = {
        "name": "vehicle-ecu",
        "command": [
            "python3", "realtime_telemetry_simulator.py",
            "--mode", "can",
            "--skip-mqtt",
            "--commands-mqtt",
            "--trips", "0",
            "--vehicles", "1",
            "--vehicle-config", vehicle_ecu_vehicle_config,
        ],
        "environment": [
            {"name": "CAN_BUS0", "value": can_iface},
            # HARD GATE: CERTIFICATE and PRIVATE_KEY must NOT be listed here.
        ],
    }
    fwe_agent_base_override = {
        "name": "fwe-agent",
        "environment": [
            {"name": "VEHICLE_NAME", "value": vin},
            {"name": "CAN_BUS0", "value": can_iface},
            # Caller appends: ENDPOINT_URL, CERTIFICATE, PRIVATE_KEY, TOPIC_PREFIX
        ],
    }
    return fwe_agent_base_override, vehicle_ecu_override


def _resolve_rule_name(config: dict) -> str:
    """Return the MSK rule name to pass to the sim worker.

    Follows the config.get(<key>, <default>) pattern used for trips / city /
    route_length immediately above in _start().  A caller-supplied rule_name is
    validated against _ALLOWED_RULE_SUFFIXES (T7.6 constraint: known product
    rule names only, not a regex or prefix).  Any value not on the allowlist —
    including arbitrary attacker strings — falls back to the CMS-native default.

    The CMS-native default IS the regression guard (spec R6): changing it here
    without updating the test baseline would break test_a immediately.
    """
    default = f"cms_{STAGE}_iot_msk_rule"
    raw = config.get("rule_name", None)
    if not isinstance(raw, str) or not raw:
        return default
    # Allowlist check: rule_name must be exactly cms_{STAGE}_{suffix} for a
    # known suffix.  No substring / regex / prefix matching — full equality only.
    allowed = {f"cms_{STAGE}_{suffix}" for suffix in _ALLOWED_RULE_SUFFIXES}
    if raw in allowed:
        return raw
    return default


def _start(config):
    sim_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()

    # Route length — how many GPS points the simulator samples per trip.
    # Clamped to [5, 60] so a bad client value can't hang an ECS task
    # indefinitely. Default 20 (~5 min at 15s interval) matches the
    # simulator's internal default. See realtime_telemetry_simulator.py
    # self.route_length for the underlying knob.
    try:
        _route_len = int(config.get("route_length", 20))
    except (TypeError, ValueError):
        _route_len = 20
    _route_len = max(5, min(60, _route_len))

    args = ["python3", "realtime_telemetry_simulator.py",
            "--region", REGION,
            "--trips", str(config.get("trips", 3)),
            "--route-length", str(_route_len),
            "--city", config.get("city", "seattle"),
            "--mode", "can" if config.get("mode") == "fwe" else "mqtt_direct",
            "--rule-name", _resolve_rule_name(config),
            "--table-suffix", f"{STAGE}-storage"]
    if config.get("mode") == "fwe":
        args.append("--skip-mqtt")
        # Still need MQTT for remote commands — pass IoT endpoint
        args.extend(["--commands-mqtt"])

    vehicles = config.get("vehicles", 10)
    if isinstance(vehicles, list):
        # Normalize: convert string vehicle IDs to objects with vin lookup
        normalized = []
        for v in vehicles:
            if isinstance(v, str):
                # Look up vin from vehicleId
                try:
                    veh = ddb.Table(f"cms-{STAGE}-storage-vehicles").get_item(Key={"vehicleId": v}).get("Item", {})
                    normalized.append({"vehicleId": v, "vin": veh.get("vin", v)})
                except:
                    normalized.append({"vehicleId": v, "vin": v})
            else:
                normalized.append(v)
        vehicles = normalized
        config["vehicles"] = normalized  # Update config so FWE mode can access normalized vehicles
        args += ["--vehicles", str(len(vehicles)), "--vehicle-config", json.dumps(vehicles)]
    else:
        args += ["--vehicles", str(vehicles)]

    if config.get("safety_rate") is not None:
        args += ["--safety-rate", str(config["safety_rate"])]
    for flag in ("force_tire_blowout", "force_engine_overheat", "force_battery_critical",
                 "force_brake_failure", "force_oil_pressure_low"):
        if config.get(flag):
            args.append(f"--{flag.replace('_', '-')}")
    if config.get("force_maintenance_alert"):
        args.append("--force-maintenance-alert")
    if config.get("force_safety_event") and config["force_safety_event"] not in (None, "None", "none", ""):
        args += ["--force-safety-event", config["force_safety_event"]]
    # Catalog-driven events from UI multi-select
    all_events = (config.get("safety_scenarios") or []) + (config.get("maintenance_scenarios") or [])
    if all_events:
        args += ["--events", ",".join(all_events)]
    # Resolve the vehicle's assigned driver when the caller didn't pass an
    # explicit driver_id. Mirrors the "authoritative source" logic the UI
    # uses on /api/v1/vehicles/{id}: scan drivers by assignedVehicleId,
    # pick highest safetyScore as tiebreak. Only fires for single-vehicle
    # sims since fleet-wide sims should keep the caller's random/
    # consistent semantics (picking one driver for 10 vehicles would be
    # nonsensical).
    explicit_driver_id = config.get("driver_id")
    if (not explicit_driver_id or explicit_driver_id in ("None", "none", "")) \
            and isinstance(vehicles, list) and len(vehicles) == 1:
        resolved = _resolve_assigned_driver(vehicles[0].get("vehicleId"))
        if resolved:
            config["driver_id"] = resolved  # Persisted in DDB sim row for audit
            args += ["--driver-id", resolved,
                     "--driver-selection", "specific"]
            print(f"Resolved assigned driver {resolved} for "
                  f"{vehicles[0].get('vehicleId')}; overriding "
                  f"driver_selection={config.get('driver_selection')!r} → 'specific'")
        else:
            # No assigned driver for this vehicle — fall through to the
            # caller's requested selection mode (random/consistent).
            if config.get("driver_selection"):
                args += ["--driver-selection", config["driver_selection"]]
    else:
        # Explicit driver_id or fleet-wide sim — honor the caller's config.
        if config.get("driver_selection"):
            args += ["--driver-selection", config["driver_selection"]]
        if explicit_driver_id and explicit_driver_id not in ("None", "none", ""):
            args += ["--driver-id", explicit_driver_id]
    if not config.get("cleanup", True):
        args.append("--no-cleanup")

    env_overrides = [
        {"name": "SIM_ID", "value": sim_id},
        {"name": "DEPLOYMENT_STAGE", "value": STAGE},
        {"name": "AWS_REGION", "value": REGION},
        {"name": "ROUTE_CALCULATOR_NAME", "value": f"cms-{STAGE}-ui-route-calculator"},
    ]

    # Choose task def and launch config based on mode
    mode = config.get("mode", "mqtt_direct")
    if mode == "fwe":
        fwe_agent_task_def = _task_family(os.environ.get("FWE_TASK_DEF", TASK_DEF))
        fwe_sim_task_def = _task_family(os.environ.get("FWE_SIM_TASK_DEF", TASK_DEF))
        fwe_cap_provider = os.environ.get("FWE_CAPACITY_PROVIDER", "")

        vin = "SIM-VEHICLE"
        cert_pem = ""
        private_key = ""
        if isinstance(config.get("vehicles"), list) and config["vehicles"]:
            v = config["vehicles"][0]
            vin = v.get("vin", v.get("vehicleId", "SIM-VEHICLE"))
            vehicle_id = v.get("vehicleId", vin)

            # Detect whether a RUNNING campaign with a non-empty
            # signalsToCollect exists for this vehicle. Historically this
            # was a mandatory gate: absence returned 400 and refused to
            # start the FWE agent, on the theory that an agent without a
            # collection scheme would transmit zero signals (the
            # 2026-05-04 empty-signalsToCollect regression referenced in
            # deployment/scripts/seed_decoder_and_campaign.py).
            #
            # Per user direct instruction (2026-07-16), that is now a
            # WARNING rather than a BLOCKER — the operator may
            # legitimately want to start the agent before assigning a
            # campaign (e.g., to validate connectivity, to warm the vcan
            # slot, to demo an idle-checkin path). We keep the detection
            # so the response carries an advisory that the frontend can
            # surface as an informational flash, and log a distinctive
            # warning to CloudWatch so the risk is visible in ops
            # tooling. See issues/2026-07-16-fwe-agent-start-mandatory-
            # campaign-gate/ for full context.
            camp_table = ddb.Table(f"cms-{STAGE}-campaigns")
            vehicles_table = ddb.Table(f"cms-{STAGE}-storage-vehicles")
            has_campaign = False
            # Check vehicle-level and broadcast campaigns
            targets = [f"vehicle:{vin}", "all"]
            # Also check fleet-level campaigns
            try:
                veh = vehicles_table.get_item(Key={"vehicleId": vehicle_id or vin}).get("Item", {})
                fleet_id = veh.get("fleetId")
                if fleet_id:
                    targets.append(f"fleet:{fleet_id}")
            except Exception:
                pass
            for target in targets:
                try:
                    cr = camp_table.query(
                        IndexName="targetArn-index",
                        KeyConditionExpression="targetArn = :t",
                        FilterExpression="#s = :r",
                        ExpressionAttributeNames={"#s": "status"},
                        ExpressionAttributeValues={":t": target, ":r": "RUNNING"},
                    )
                    for c in cr.get("Items", []):
                        if c.get("signalsToCollect"):
                            has_campaign = True
                            break
                except Exception:
                    pass
                if has_campaign:
                    break
            if not has_campaign:
                # Refuse — a trip that runs with no campaign transmits nothing,
                # with no error anywhere for the operator to see. Previously this
                # only warned ("Warn (but do NOT abort) — surfaces the risk
                # without blocking the operator"); changed 2026-09-18 per
                # issues/2026-09-18-cs-simulate-no-campaign-gate-and-picker-labels/
                # — the frontend's `no_telemetry_campaign` branch in
                # `describeStartFailure()` already existed for exactly this
                # reason token and was dead code until this change, because the
                # backend never actually refused.
                campaign_reason = "no_telemetry_campaign"
                campaign_error = (
                    f"Cannot start simulation: {vin} has no active telemetry "
                    "campaign, so the FWE agent would start but transmit "
                    "nothing. Assign a campaign and retry."
                )
                print(f"⚠️ CAMPAIGN-REFUSED: {campaign_error}")
                return _resp(409, {
                    "error": campaign_error,
                    "reason": campaign_reason,
                    "vin": vin,
                })

            try:
                cert_table = ddb.Table(f"cms-{STAGE}-storage-vehicle-certificates")
                cert_resp = cert_table.get_item(Key={"vehicleId": vehicle_id})
                if "Item" in cert_resp:
                    cert_pem = cert_resp["Item"].get("certificatePem", "")
                    private_key = cert_resp["Item"].get("privateKey", "")
                    # Use IoT thing name from cert table
                    vin = cert_resp["Item"].get("vin", vin)
                elif vin and vin != vehicle_id:
                    # Fallback: try by VIN in case cert was stored under the VIN string
                    cert_resp2 = cert_table.get_item(Key={"vehicleId": vin})
                    if "Item" in cert_resp2:
                        cert_pem = cert_resp2["Item"].get("certificatePem", "")
                        private_key = cert_resp2["Item"].get("privateKey", "")
                        vin = cert_resp2["Item"].get("vin", vin)
                    else:
                        # Scan by vin attribute to find the right record
                        scan_resp = cert_table.scan(
                            FilterExpression="vin = :v",
                            ExpressionAttributeValues={":v": vin}
                        )
                        if scan_resp.get("Items"):
                            item = scan_resp["Items"][0]
                            cert_pem = item.get("certificatePem", "")
                            private_key = item.get("privateKey", "")
                            vin = item.get("vin", vin)
            except Exception as e:
                print(f"Cert lookup failed: {e}")

            if not cert_pem:
                return _resp(400, {"success": False, "error": f"No certificate found for {vehicle_id} (vin={vin}). Check vehicle-certificates table."})

        iot_endpoint = iot.describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
        cap_strategy = [{"capacityProvider": fwe_cap_provider, "weight": 1, "base": 1}] if fwe_cap_provider else []

        # ── CP8: UDS-DTC wiring ──────────────────────────────────────
        # If the user selected DTC-producing maintenance_scenarios, build
        # the UDS_DTC_MAP env var (consumed by uds_dtc_responder.py inside
        # the fwe-simulator task) and write an ephemeral campaign row with
        # signalsToFetch so CampaignSyncProcessor emits DTC_QUERY actions
        # to the FWE agent. Events without a dtc_code in the event catalog
        # flow through the regular threshold-based path (MaintenanceProcessor),
        # unchanged.
        # ── Baseline telemetry campaign (pre-flight) ─────────────────
        # FWE collects only what a RUNNING campaign asks for. Without one the
        # trip runs, emits CAN frames, completes, and produces NOTHING — with no
        # error anywhere. Ensure coverage before launching rather than letting
        # the operator discover it from an empty trips table.
        _tele_campaign, _tele_created = _ensure_telemetry_campaign(vin)
        if not _tele_campaign:
            return _resp(409, {
                "success": False,
                "error": (
                    f"No telemetry campaign covers vehicle {vin}, and one could "
                    f"not be created automatically. The trip would run and "
                    f"produce zero telemetry. Deploy a campaign with: "
                    f"python3 deployment/scripts/deploy_vehicle_campaign.py "
                    f"--vehicle-name {vin} --apply"
                ),
                "vin": vin,
                "reason": "no_telemetry_campaign",
            })

        uds_dtc_map, signals_to_fetch, ecus_in_play = _build_uds_dtc_map(
            config.get("maintenance_scenarios") or []
        )
        if signals_to_fetch:
            _ensure_uds_campaign(vin, signals_to_fetch, ecus_in_play, sim_id)
        uds_dtc_map_json = json.dumps(uds_dtc_map) if uds_dtc_map else ""

        # 1. Start FWE agent task if not already running
        existing_agent = _check_running_tasks(vin)
        agent_task_arn = existing_agent
        # Per issues/2026-05-29-cms-sim-fwe-lifecycle-and-lookup-hardening Bug 3:
        # do NOT default can_iface to "vcan0". Either we start a new agent
        # (and assign a deterministic vcan from _next_vcan_index) or we
        # discover the existing agent's vcan via _resolve_agent_vcan, which
        # raises ValueError on any failure mode rather than falling through.
        can_iface = None
        if not existing_agent:
            # Assign next available vcan interface
            vcan_idx = _next_vcan_index()
            can_iface = f"vcan{vcan_idx}"
            try:
                # Build both container overrides via the shared helper so the
                # fwe-agent/vehicle-ecu pairing is structural — no call site can
                # accidentally omit the sidecar override (Fix Group 8 / Fix Group 9,
                # spec 2026-08-04-cms-vehicle-trip-lifecycle-split).
                fwe_agent_base_override, vehicle_ecu_override = (
                    _build_fwe_container_overrides(vehicle_id, vin, can_iface)
                )
                # Merge the credential/endpoint env vars into the base override.
                # HARD GATE: these go on fwe-agent ONLY, never on vehicle-ecu.
                fwe_agent_override = dict(fwe_agent_base_override)
                fwe_agent_override["environment"] = (
                    fwe_agent_base_override["environment"] + [
                        {"name": "ENDPOINT_URL", "value": iot_endpoint},
                        {"name": "CERTIFICATE", "value": cert_pem},
                        {"name": "PRIVATE_KEY", "value": private_key},
                        {"name": "TOPIC_PREFIX", "value": "cms/fleetwise/"},
                    ]
                )
                agent_resp = ecs.run_task(
                    cluster=CLUSTER,
                    taskDefinition=fwe_agent_task_def,
                    capacityProviderStrategy=cap_strategy,
                    overrides={"containerOverrides": [
                        fwe_agent_override,
                        vehicle_ecu_override,
                    ]},
                )
                agent_task_arn = agent_resp.get("tasks", [{}])[0].get("taskArn")
                print(f"Started FWE agent task: {agent_task_arn} on {can_iface}")
            except Exception as e:
                print(f"FWE agent run_task error: {e}")
        else:
            # Existing presence process (vehicle-ecu sidecar or presence loop on the
            # agent task) is running.  DDB-poll control channel: write a trip-intent
            # record to the vehicle's DDB item so the presence loop picks it up on
            # its next idle tick.  Do NOT spawn a second fwe-simulator task — that
            # would create a second state owner (VehicleState + command subscriber) on
            # the same vehicle, which is the defect this spec exists to eliminate.
            #
            # Per issues/2026-05-29-cms-sim-fwe-lifecycle-and-lookup-hardening Bug 3:
            # even in the reuse path, _resolve_agent_vcan is called so the SIM_TABLE
            # row carries the correct can_iface.  A failure here returns 500; we do
            # NOT fall back to vcan0 (that is the hard constraint).
            try:
                can_iface = _resolve_agent_vcan(existing_agent)
            except ValueError as e:
                msg = str(e)
                print(f"⚠ {msg} — refusing to start simulator on vcan0 fallback")
                return _resp(500, {"success": False, "error": msg})

            # Count of trips from the args already built above.
            trips_count = 1
            for i, arg in enumerate(args):
                if arg == "--trips" and i + 1 < len(args):
                    try:
                        trips_count = int(args[i + 1])
                    except (ValueError, IndexError):
                        pass
                    break
            # Clamp to [1, MAX] so a bad client value can't hang an ECS task
            # indefinitely.  Same doctrine as _route_len above.  99 is the
            # ceiling because MQTT-Direct is launched with --trips 99 today;
            # a lower cap would break an existing path.
            #
            # NOTE: this ceiling is duplicated as _MAX_TRIPS_PER_INTENT in
            # services/simulation/realtime_telemetry_simulator.py (PresenceLoop.run).
            # The two live in different deployment units — a Lambda bundle and a
            # container image — with no shared module to import from, so a single
            # source of truth is not available here. Named on both sides and
            # cross-referenced so the duplication is at least visible; if either
            # changes, change both. Security review Cycle 5, Suggestion 2.
            _MAX_TRIPS_PER_INTENT = 99
            trips_count = max(1, min(_MAX_TRIPS_PER_INTENT, trips_count))

            try:
                _write_trip_intent(
                    vehicle_id=vehicle_id,
                    sim_id=sim_id,
                    trips_count=trips_count,
                    agent_task_arn=existing_agent,
                    config=config,
                )
            except Exception as e:
                print(f"⚠ trip-intent write failed: {e}")
                return _resp(500, {"success": False, "error": f"Failed to write trip intent: {e}"})

            # Record the intent in the simulations table so /status and /stop work
            # the same way regardless of whether a fresh task or the presence loop
            # services the trip.  taskArn is the agent's ARN; no separate sim task.
            config["_agent_task_arn"] = existing_agent
            now_intent = datetime.now(timezone.utc).isoformat()
            # Baseline for zero-data detection: capture the vehicle's trip count
            # BEFORE the run. A simulation that reaches `completed` without this
            # number advancing produced no data — which is exactly the silent
            # failure in issues/2026-09-01-trip-simulation-zero-telemetry-no-campaign/,
            # where a trip emitted 800 CAN frames, completed, and materialised
            # nothing. Without a baseline there is no way for /status to tell the
            # difference between a good run and a doomed one.
            _trips_at_start = None
            try:
                _vrow = ddb.Table(f"cms-{STAGE}-storage-vehicles").get_item(
                    Key={"vehicleId": vehicle_id}).get("Item") or {}
                if _vrow.get("totalTrips") is not None:
                    _trips_at_start = int(_vrow["totalTrips"])
            except Exception as e:
                print(f"⚠ tripsAtStart baseline unavailable for {vehicle_id}: "
                      f"{type(e).__name__}: {e}")

            _item = {
                "simulationId": sim_id,
                "taskArn": existing_agent,       # presence loop's host task
                "agentTaskArn": existing_agent,
                "status": "intent_pending",      # transitions to 'running' when the
                                                  # presence loop picks up the intent
                "config": json.dumps(config),
                "startTime": now_intent,
                "ttl": int(time.time()) + 86400,
            }
            if _trips_at_start is not None:
                _item["tripsAtStart"] = _trips_at_start
                _item["vehicleIdForTrips"] = vehicle_id
            SIM_TABLE.put_item(Item=_item)

            resp_body = {"success": True, "simulation_id": sim_id, "task_arn": existing_agent}
            return _resp(200, resp_body)

        # 2. No existing presence process — start a new simulator task for this trip
        # (fresh-agent path: the agent was just spawned above, so there is no
        # presence loop yet; the old task-per-trip model applies until Group 4
        # wires the vehicle-ecu sidecar into the fwe-agent task definition).
        # Pass UDS_DTC_MAP so the simulator can spawn uds_dtc_responder.py
        # as a subprocess. Empty string means no DTC responder needed.
        sim_env = env_overrides + [
            {"name": "CAN_BUS0", "value": can_iface},
            {"name": "UDS_DTC_MAP", "value": uds_dtc_map_json},
        ]

        # WS4: pin the simulator to the same EC2 instance as the agent so
        # both tasks share the same physical vcan bus. Fall back without
        # constraint if instance resolution fails — do NOT hard-fail the sim.
        placement_constraints = []
        if agent_task_arn:
            ec2_id = _resolve_agent_ec2_instance_id(agent_task_arn)
            if ec2_id:
                placement_constraints = [
                    {"type": "memberOf", "expression": f"ec2InstanceId == {ec2_id}"}
                ]
            else:
                print(f"⚠ WS4: could not resolve EC2 instance for agent {agent_task_arn} — starting simulator without placement constraint")

        try:
            container_overrides = [
                {"name": "fwe-simulator", "command": args, "environment": sim_env},
            ]
            run_task_kwargs = dict(
                cluster=CLUSTER,
                taskDefinition=fwe_sim_task_def,
                capacityProviderStrategy=cap_strategy,
                overrides={"containerOverrides": container_overrides},
            )
            if placement_constraints:
                run_task_kwargs["placementConstraints"] = placement_constraints
            resp = ecs.run_task(**run_task_kwargs)
        except Exception as e:
            print(f"FWE sim run_task error: {e}")
            return _resp(500, {"success": False, "error": str(e)})
        config["_agent_task_arn"] = agent_task_arn or ""
    else:
        # Extract vehicle ID for tagging
        vehicle_id = ""
        if isinstance(vehicles, list) and len(vehicles) > 0:
            v = vehicles[0]
            vehicle_id = v.get("vin", v.get("vehicleId", "")) if isinstance(v, dict) else str(v)

        resp = ecs.run_task(
            cluster=CLUSTER,
            taskDefinition=TASK_DEF,
            launchType="FARGATE",
            networkConfiguration={"awsvpcConfiguration": {
                "subnets": SUBNETS,
                "securityGroups": [SG],
                "assignPublicIp": "DISABLED",
            }},
            overrides={"containerOverrides": [{
                "name": "worker",
                "command": args,
                "environment": env_overrides,
            }]},
            tags=[
                {"key": "SimulationId", "value": sim_id},
                {"key": "VehicleId", "value": vehicle_id},
            ],
            group=f"sim:{vehicle_id}" if vehicle_id else f"sim:{sim_id}",
        )

    task_arn = resp["tasks"][0]["taskArn"] if resp.get("tasks") else None
    if not task_arn:
        return _resp(500, {"success": False, "error": f"ECS RunTask failed: {resp.get('failures', [])}"})

    SIM_TABLE.put_item(Item={
        "simulationId": sim_id,
        "taskArn": task_arn,
        "agentTaskArn": config.get("_agent_task_arn", ""),
        "status": "running",
        "config": json.dumps(config),
        "startTime": now,
        "ttl": int(time.time()) + 86400,
    })

    resp_body = {"success": True, "simulation_id": sim_id, "task_arn": task_arn}
    return _resp(200, resp_body)


def _stop(sim_id):
    item = SIM_TABLE.get_item(Key={"simulationId": sim_id}).get("Item")
    if not item:
        return _resp(404, {"success": False, "error": "Simulation not found"})

    task_arn = item.get("taskArn")
    if task_arn:
        try:
            ecs.stop_task(cluster=CLUSTER, task=task_arn, reason="User stopped simulation")
        except Exception:
            pass

    # Per issues/2026-05-29-cms-sim-fwe-lifecycle-and-lookup-hardening Bug 1:
    # also stop the paired FWE agent task so we don't leak one persistent
    # agent (and its vcan slot) per unique VIN ever simulated. agentTaskArn
    # is set by _start when mode=fwe; mqtt_direct sims have no agent and
    # this branch is a no-op.
    agent_task_arn = item.get("agentTaskArn") or ""
    if agent_task_arn:
        try:
            ecs.stop_task(cluster=CLUSTER, task=agent_task_arn,
                          reason=f"User stopped sim {sim_id}; releasing paired FWE agent")
        except Exception as e:
            # Stale or already-stopped agent ARN must not break the
            # user-visible stop. Log and continue.
            print(f"_stop({sim_id}): agent stop_task({agent_task_arn}) failed: "
                  f"{type(e).__name__}: {e}")

    # Clean up ephemeral UDS-DTC campaigns tagged with this sim_id.
    # See _delete_uds_campaigns_for_sim docstring for the leak history.
    # Failures logged and swallowed — must not break the stop response.
    _delete_uds_campaigns_for_sim(sim_id)

    SIM_TABLE.update_item(
        Key={"simulationId": sim_id},
        UpdateExpression="SET #s = :s, endTime = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "stopped", ":t": datetime.now(timezone.utc).isoformat()})

    return _resp(200, {"success": True})


def _trips_since(vehicle_id, vin, since_ms):
    """Count trip rows created for this vehicle at/after ``since_ms``.

    Uses the ``vehicleId-startTime-index`` GSI, so this is a bounded Query — NOT
    a scan of the ~93k-row trips table. That matters because /status is polled.

    **Checks BOTH identifiers on purpose.** The trips table is inconsistent about
    what goes in ``vehicleId``: most rows carry a VIN-shaped value
    (e.g. ``1FMUK8KH6SGB16760``) but VEH-VO-001's row carries the *vehicleId*
    (``VEH-VO-001``). Querying only one identifier makes a successful trip look
    like a total failure — which is exactly what happened during the 2026-09-01
    investigation, twice. Until that drift is fixed
    (issues/2026-09-01-trip-vehicleid-identifier-drift/), check both.

    Returns the count, or None if it could not be determined — callers must treat
    None as "unknown", never as zero.
    """
    if not since_ms:
        return None
    table = ddb.Table(f"cms-{STAGE}-storage-trips")
    total = 0
    found_any = False
    for ident in {v for v in (vehicle_id, vin) if v}:
        try:
            resp = table.query(
                IndexName="vehicleId-startTime-index",
                KeyConditionExpression=(
                    Key("vehicleId").eq(ident) & Key("startTime").gte(int(since_ms))
                ),
                Select="COUNT",
            )
            total += int(resp.get("Count", 0))
            found_any = True
        except Exception as e:
            print(f"⚠ _trips_since: query failed for vehicleId={ident!r}: "
                  f"{type(e).__name__}: {e}")
    return total if found_any else None


def _status(sim_id):
    item = SIM_TABLE.get_item(Key={"simulationId": sim_id}).get("Item")
    if not item:
        return _resp(404, {"error": "Simulation not found"})

    task_arn = item.get("taskArn")
    ecs_status = None
    if task_arn and item.get("status") == "running":
        try:
            resp = ecs.describe_tasks(cluster=CLUSTER, tasks=[task_arn])
            tasks = resp.get("tasks", [])
            failures = resp.get("failures", [])
            if tasks:
                ecs_status = tasks[0]["lastStatus"]
                if ecs_status == "STOPPED":
                    item["status"] = "completed"
                    SIM_TABLE.update_item(
                        Key={"simulationId": sim_id},
                        UpdateExpression="SET #s = :s, endTime = :t",
                        ExpressionAttributeNames={"#s": "status"},
                        ExpressionAttributeValues={":s": "completed", ":t": datetime.now(timezone.utc).isoformat()})
            elif failures:
                # Task expired from ECS — mark as completed
                ecs_status = "GONE"
                item["status"] = "completed"
                SIM_TABLE.update_item(
                    Key={"simulationId": sim_id},
                    UpdateExpression="SET #s = :s, endTime = :t",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":s": "completed", ":t": datetime.now(timezone.utc).isoformat()})
        except Exception:
            pass

    # ── Zero-data detection ───────────────────────────────────────────────
    # A simulation that reaches `completed` without the vehicle's trip count
    # advancing produced NO data. That is a silent failure the operator cannot
    # otherwise see: the run looks successful at every layer. Surfacing it here
    # means the status the UI already polls carries the verdict.
    # See issues/2026-09-01-trip-simulation-zero-telemetry-no-campaign/.
    # CORRECTED 2026-09-01. The first version of this check compared the
    # vehicle's `totalTrips` counter against a baseline, and that produced a FALSE
    # POSITIVE: sim 4b33add3 materialised a real COMPLETED trip (distance 3.74,
    # a real GPS fix) while `totalTrips` stayed at 210, because trip completion
    # does not update the vehicle aggregate. The counter answers a different
    # question than "did this run produce data".
    #
    # Authoritative signal = trip ROWS created since the run started.
    _zero_data = None
    # Materialised trip count. Computed for EVERY status, not only `completed`.
    #
    # It was previously computed inside the `completed` branch, used once for the
    # zero-data verdict, and then discarded — so the one real measure of whether a
    # run is producing data existed server-side and reached no caller. Meanwhile the
    # UI's two data rows read `message_count` and `last_message_at`, which this
    # endpoint has never returned, and rendered a permanent em-dash.
    # See issues/2026-09-22-cs-simulate-session-panel-reads-fields-that-never-existed/.
    #
    # Running it on every poll is deliberate and cheap: `_trips_since` is a bounded
    # COUNT Query against `vehicleId-startTime-index` (never a table scan) — its own
    # docstring notes it is shaped that way precisely because /status is polled.
    _since = item.get("runStart") or item.get("startTime")
    _since_ms = None
    if _since:
        try:
            _since_ms = int(
                datetime.fromisoformat(str(_since)).timestamp() * 1000)
        except Exception:
            _since_ms = None
    _trips_materialised = _trips_since(
        item.get("vehicleIdForTrips"),
        (json.loads(item.get("config", "{}")) or {}).get("_vin"),
        _since_ms)
    if item.get("status") == "completed":
        if _trips_materialised is not None:
            # None means "could not determine" and must never read as zero — the
            # zero-data verdict is only meaningful on a real count.
            _zero_data = (_trips_materialised == 0)
            print(f"ℹ sim={sim_id} trips materialised since run start: {_trips_materialised}")

    config = json.loads(item.get("config", "{}"))
    vehicles = config.get("vehicles", config.get("vehicles", 10))
    vehicle_count = len(vehicles) if isinstance(vehicles, list) else vehicles
    total_trips = config.get("trips", 3) * vehicle_count

    # Fetch logs from CloudWatch
    mode = config.get("mode", "mqtt_direct")
    # Bound the console read to this run. `_since_ms` is already parsed above for
    # `_trips_since`; reuse it rather than re-parsing. The tail bound is applied
    # ONLY once the run is no longer producing output — for a running sim the
    # newest events in [runStart, now) are exactly what the live console wants.
    # See issues/2026-09-23-sim-logs-pane-empty-heartbeat-crowds-out-run-output/.
    _log_end_ms = None
    if item.get("status") != "running" and item.get("endTime"):
        try:
            _log_end_ms = int(
                datetime.fromisoformat(str(item.get("endTime"))).timestamp() * 1000
            ) + LOG_WINDOW_TAIL_GRACE_MS
        except Exception:
            _log_end_ms = None
    sim_logs, fwe_logs = _get_worker_logs(
        task_arn, mode,
        agent_task_arn=item.get("agentTaskArn"),
        start_time_ms=_since_ms,
        end_time_ms=_log_end_ms,
    )
    _data_warning = None
    if _zero_data:
        _data_warning = (
            "Simulation completed but NO trip was materialised for this vehicle. "
            "The most common cause is no RUNNING FleetWise campaign covering it, "
            "so the agent reads the CAN bus and discards every frame. Check: "
            "python3 deployment/scripts/deploy_vehicle_campaign.py "
            f"--vehicle-name <the agent's VEHICLE_NAME, not the cert thingName>"
        )
        print(f"⚠ ZERO-DATA: sim={sim_id} completed with no trip-count advance")

    # Surface the zero-data verdict on the payload the UI already polls.
    _extra = {'dataWarning': _data_warning} if _data_warning else {}
    return _resp(200, {
        **_extra,
        "id": sim_id,
        "status": item.get("status"),
        "start_time": item.get("startTime"),
        "end_time": item.get("endTime"),
        "task_arn": task_arn,
        "ecs_status": ecs_status,
        "config": config,
        # Last telemetry timestamp for the vehicle this run drives.
        #
        # The UI has always read `last_message_at` and this endpoint has never sent
        # it. The value is real and live — `lastSeenAt` on the vehicle row, written
        # by the telemetry path — it simply was never plumbed through.
        #
        # `None` when unknown, never a placeholder string: the caller renders its own
        # em-dash, and a fabricated timestamp would be worse than a blank.
        "last_message_at": _vehicle_last_seen_at(item.get("vehicleIdForTrips")),
        "trips": {
            # Requested, not achieved: config.trips x vehicle_count. Kept under its
            # original name so existing callers are unaffected, but it is an echo of
            # the request and must not be read as progress.
            "total": total_trips,
            # Achieved. The real count of trip rows created since this run started,
            # or None when it could not be determined ("unknown", never zero).
            "materialised": _trips_materialised,
            # Placeholders from the original implementation, never populated. Left in
            # place because removing a key is a breaking change for any caller that
            # reads it; verified 2026-09-22 that nothing does. Prefer `materialised`.
            "completed": 0,
            "progress": 0,
        },
        "output": sim_logs,
        "fwe_logs": fwe_logs,
    })


def _vehicle_last_seen_at(vehicle_id):
    """Return a vehicle's last telemetry timestamp (``lastSeenAt``), or None.

    Best-effort and fail-soft by design: /status is polled, and a status response
    that 500s because a supplementary field could not be read would take the log
    panes down with it. Every failure path yields None, which the caller renders as
    "unknown" rather than as a zero or a stale value.

    One GetItem on the primary key per poll. The same table is already read
    elsewhere in this handler, so this adds no IAM surface.
    """
    if not vehicle_id:
        return None
    try:
        row = ddb.Table(f"cms-{STAGE}-storage-vehicles").get_item(
            Key={"vehicleId": vehicle_id}).get("Item") or {}
    except Exception:
        return None
    seen = row.get("lastSeenAt")
    return str(seen) if seen else None


def _get_worker_logs(task_arn, mode="mqtt_direct", agent_task_arn=None,
                     start_time_ms=None, end_time_ms=None):
    """Fetch this run's logs from CloudWatch. Returns (sim_logs, fwe_logs).

    `start_time_ms` / `end_time_ms` bound the read to THE RUN rather than to the
    stream. They protect DIFFERENT properties — established by mutation testing,
    not by reasoning — see
    issues/2026-09-23-sim-logs-pane-empty-heartbeat-crowds-out-run-output/:

      * `end_time_ms` protects OUTPUT PRESENCE on a completed run. Without it the
        window runs to `now`; `startFromHead=False` then lands on the newest
        events, which are the keepalive the long-lived agent has emitted since —
        the filter below drops all of them and the console shows "Waiting for
        simulator output..." for a run that produced output normally. A 17-hour-old
        run accrues tens of thousands of keepalive events after its last real line,
        so no value of `limit` rescues an unbounded upper edge.
      * `start_time_ms` protects OUTPUT ATTRIBUTION. It does NOT affect presence:
        keepalive is filtered regardless, so dropping the lower bound leaves the
        pane populated. What it prevents is an EARLIER run's real output appearing
        as this run's, because the agent outlives runs and one stream spans several.

    Pass `end_time_ms=None` for a RUNNING sim: the window is then [runStart, now),
    whose newest events are what a live console wants.

    FWE mode reads three sources:
      [0] /ecs/cms-{STAGE}/fwe-simulator — fresh-path sim task stdout
      [1] /ecs/cms-{STAGE}/fwe-agent     — C++ agent binary stdout (returned as fwe_logs)
      [2] /ecs/cms-{STAGE}/vehicle-ecu   — PresenceLoop stdout inside the agent's
                                            vehicle-ecu sidecar (reuse-path sim output)

    Reuse-path sims (spec 2026-08-04-cms-vehicle-trip-lifecycle-split) drive trips from
    PresenceLoop.run_trips() inside the persistent agent's vehicle-ecu sidecar, not from
    a separately-spawned fwe-simulator task. That output lives in the vehicle-ecu log
    group. Fresh-path sims spawn a real fwe-simulator task, so source [0] is populated
    and source [2] returns empty. At most one of the two is non-empty per sim; sorting
    by timestamp keeps interleaved lines correct if both ever coexist.
    """
    if not task_arn:
        return [], []
    task_id = task_arn.split("/")[-1]
    if mode == "fwe":
        agent_task_id = agent_task_arn.split("/")[-1] if agent_task_arn else task_id
        sources = [
            (f"/ecs/cms-{STAGE}/fwe-simulator", f"sim/fwe-simulator/{task_id}"),
            (f"/ecs/cms-{STAGE}/fwe-agent", f"fwe/fwe-agent/{agent_task_id}"),
            # vehicle-ecu sidecar shares the agent's task ID (same-task sidecar).
            (f"/ecs/cms-{STAGE}/vehicle-ecu", f"vehicle-ecu/vehicle-ecu/{agent_task_id}"),
        ]
    else:
        sources = [(WORKER_LOG_GROUP, f"worker/worker/{task_id}")]
    results = []
    for log_group, stream_name in sources:
        entries = []
        try:
            # Window the read to the run. The noise filter below runs AFTER the
            # limit, so it can only ever shrink an already-crowded window —
            # bounding the window is what gives the filter something to keep.
            read_kwargs = {
                "logGroupName": log_group,
                "logStreamName": stream_name,
                "limit": LOG_EVENT_FETCH_LIMIT,
                "startFromHead": False,
            }
            if start_time_ms is not None:
                read_kwargs["startTime"] = int(start_time_ms)
            if end_time_ms is not None:
                read_kwargs["endTime"] = int(end_time_ms)
            resp = logs_client.get_log_events(**read_kwargs)
            for ev in resp.get("events", []):
                msg = ev.get("message", "").strip()
                if not msg:
                    continue
                for line in msg.split("\t"):
                    line = line.strip()
                    if line and not line.startswith("🔍 MQTT LOG") and not line.startswith("🔍 Socket"):
                        entries.append({
                            "timestamp": datetime.fromtimestamp(ev["timestamp"] / 1000, tz=timezone.utc).isoformat(),
                            "message": line,
                        })
        except Exception as e:
            # NEVER swallow this silently. An AccessDenied here is
            # indistinguishable from "no output yet" to the caller, and that is
            # exactly how a missing IAM grant on /ecs/{stage}/vehicle-ecu
            # presented as the UI waiting forever on "Waiting for simulator
            # output..." while the trip ran normally. A log line is the only
            # thing that makes an unreadable log group observable.
            # See issues/2026-09-01-simulator-output-invisible-vehicle-ecu-logs-ungranted/.
            print(f"⚠ _get_worker_logs: cannot read {log_group} "
                  f"stream={stream_name}: {type(e).__name__}: {e}")
        results.append(entries[-50:])
    # Merge fwe-simulator (fresh-path) + vehicle-ecu (reuse-path) into sim_logs so both
    # paths surface through d.output without a UI change. fwe-agent stays as fwe_logs.
    sim_entries = results[0] if results else []
    fwe_entries = results[1] if len(results) > 1 else []
    ecu_entries = results[2] if len(results) > 2 else []
    sim_logs = sorted(sim_entries + ecu_entries, key=lambda e: e["timestamp"])[-100:]
    return sim_logs, fwe_entries


def _list():
    resp = SIM_TABLE.scan(Limit=50)
    items = resp.get("Items", [])

    # Lazily reconcile stale "running" rows before returning. Mirrors the
    # logic in _status(): when a row claims status=running but the
    # referenced ECS task is either STOPPED or gone (failure), update the
    # row to status=completed. Without this, the /simulation/list endpoint
    # accumulates stale "running" rows whenever an ECS task terminates
    # without a user hitting /stop (e.g., natural completion, container
    # crash, instance refresh), which pollutes the UI's live-sim view.
    #
    # Batched: one ECS DescribeTasks call per cluster for all running rows,
    # rather than one per row. Up to 100 tasks per call — we bound the scan
    # at 50 above so one call is always enough.
    running_items = [i for i in items if i.get("status") == "running" and i.get("taskArn")]
    ecs_status_by_arn = {}
    if running_items:
        task_arns = [i["taskArn"] for i in running_items]
        try:
            resp_ecs = ecs.describe_tasks(cluster=CLUSTER, tasks=task_arns)
            for t in resp_ecs.get("tasks", []):
                ecs_status_by_arn[t["taskArn"]] = t.get("lastStatus")
            # Tasks that ECS couldn't find (aged out / never existed) come
            # back as failures with a .arn field. Treat as GONE.
            for f in resp_ecs.get("failures", []):
                arn = f.get("arn")
                if arn:
                    ecs_status_by_arn[arn] = "GONE"
        except Exception as e:
            # Don't let an ECS hiccup break the list endpoint — just
            # return rows as-is and let _status() reconcile them later.
            print(f"_list reconciliation: ecs.describe_tasks failed: {e}")

    now_iso = datetime.now(timezone.utc).isoformat()
    for item in running_items:
        ecs_status = ecs_status_by_arn.get(item["taskArn"])
        if ecs_status in ("STOPPED", "GONE"):
            item["status"] = "completed"
            item["endTime"] = now_iso
            try:
                SIM_TABLE.update_item(
                    Key={"simulationId": item["simulationId"]},
                    UpdateExpression="SET #s = :s, endTime = :t",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":s": "completed", ":t": now_iso},
                )
            except Exception as e:
                # Best-effort — if the DDB write fails we still return the
                # corrected in-memory view for THIS response; next read
                # will retry.
                print(f"_list reconciliation: DDB update failed for "
                      f"{item['simulationId']}: {e}")

    sims = []
    for item in items:
        config = json.loads(item.get("config", "{}"))
        vehicles = config.get("vehicles", 10)
        vehicle_count = len(vehicles) if isinstance(vehicles, list) else vehicles
        sims.append({
            "id": item["simulationId"],
            "status": item.get("status"),
            "start_time": item.get("startTime"),
            "end_time": item.get("endTime"),
            "task_arn": item.get("taskArn"),
            "config": config,
            "trips": {
                "total": config.get("trips", 3) * vehicle_count,
                "completed": 0,
                "progress": 0,
            },
        })
    sims.sort(key=lambda s: s.get("start_time", ""), reverse=True)
    return _resp(200, {"simulations": sims, "count": len(sims)})


def _drivers():
    try:
        drivers_table = ddb.Table(f"cms-{STAGE}-storage-drivers")
        resp = drivers_table.scan(
            FilterExpression="attribute_exists(driverId)",
            ProjectionExpression="driverId, firstName, lastName, email, #s",
            ExpressionAttributeNames={"#s": "status"})
        drivers = [{"driverId": d["driverId"],
                     "name": f"{d.get('firstName', '')} {d.get('lastName', '')}".strip(),
                     "email": d.get("email", ""),
                     "status": d.get("status", "active")}
                    for d in resp.get("Items", [])]
        return _resp(200, {"success": True, "drivers": drivers, "count": len(drivers)})
    except Exception as e:
        return _resp(200, {"success": False, "drivers": [], "count": 0, "error": str(e)})


def _presets():
    return _resp(200, {"presets": [
        {"id": "quick", "name": "Quick Test", "description": "1 vehicle, 1 trip",
         "config": {"trips": 1, "vehicles": 1, "city": "seattle", "safety_rate": 0.1}},
        {"id": "fleet", "name": "Fleet Demo", "description": "5 vehicles, 3 trips each",
         "config": {"trips": 3, "vehicles": 5, "city": "seattle", "safety_rate": 0.15}},
        {"id": "stress", "name": "Stress Test", "description": "10 vehicles, 5 trips, high safety events",
         "config": {"trips": 5, "vehicles": 10, "city": "nyc", "safety_rate": 0.5}},
    ]})




def _vehicle_vin(vehicle_id):
    """VIN for a vehicleId, or None. A GetItem — the table is keyed on vehicleId."""
    if not vehicle_id:
        return None
    try:
        row = ddb.Table(f"cms-{STAGE}-storage-vehicles").get_item(
            Key={"vehicleId": vehicle_id},
            ProjectionExpression="vin",
        ).get("Item") or {}
        vin = row.get("vin")
        return str(vin) if vin else None
    except Exception as e:
        print(f"⚠️ _vehicle_vin({vehicle_id}) failed: {type(e).__name__}: {e}")
        return None


def _resolve_vin_to_vehicle_id(vin):
    """vehicleId for a VIN, or None.

    Paginated scan, deliberately WITHOUT Limit. There is no VIN GSI on the
    vehicles table (verified), and DynamoDB applies Limit BEFORE the
    FilterExpression — so `scan(FilterExpression="vin = :v", Limit=1)` examines
    exactly one item and filters it, returning 0 matches unless the target VIN
    happens to be scanned first. Measured 2026-08-19 on the 59-row staging table:
    Limit=1 -> ScannedCount 1 / Count 0; unlimited -> ScannedCount 59 / Count 1.

    Extracted from the identical block inlined in `_agent_stop`, which carried
    that whole explanation and was the only copy. Callers that authorize on the
    result need one shared implementation, not two.
    """
    if not vin:
        return None
    try:
        table = ddb.Table(f"cms-{STAGE}-storage-vehicles")
        scan_kwargs = {
            "FilterExpression": "vin = :v",
            "ExpressionAttributeValues": {":v": vin},
            "ProjectionExpression": "vehicleId",
        }
        while True:
            resp = table.scan(**scan_kwargs)
            items = resp.get("Items") or []
            if items:
                return items[0]["vehicleId"]
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                return None
            scan_kwargs["ExclusiveStartKey"] = lek
    except Exception as e:
        print(f"⚠️ _resolve_vin_to_vehicle_id({vin}) failed: {type(e).__name__}: {e}")
        return None


def _agent_start(config):
    """Start FWE agent only (no simulator) for a specific VIN."""
    vin = config.get("vin", "")
    vehicle_id = config.get("vehicleId", "")
    if not vin:
        return _resp(400, {"error": "vin is required"})

    # Check if there's a healthy container instance before attempting to run
    try:
        ci_resp = ecs.list_container_instances(cluster=CLUSTER, status="ACTIVE")
        if not ci_resp.get("containerInstanceArns"):
            # No instances at all — scale up the ASG
            try:
                import boto3 as _b3
                asg_client = _b3.client("autoscaling", region_name=REGION)
                # Find the ASG by cluster tag
                asgs = asg_client.describe_auto_scaling_groups()
                for g in asgs.get("AutoScalingGroups", []):
                    if any(t.get("Key") == "aws:cloudformation:stack-name" and STAGE in t.get("Value", "") for t in g.get("Tags", [])):
                        if "simulation" in g["AutoScalingGroupName"].lower():
                            asg_client.set_desired_capacity(
                                AutoScalingGroupName=g["AutoScalingGroupName"],
                                DesiredCapacity=1
                            )
                            print(f"Scaled up ASG {g['AutoScalingGroupName']} to 1")
                            break
            except Exception as scale_err:
                print(f"ASG scale-up failed: {scale_err}")
            return _resp(503, {"error": "No ECS instances available. Scaling up — try again in 3 minutes.", "retryable": True})
        
        ci_details = ecs.describe_container_instances(cluster=CLUSTER, containerInstances=ci_resp["containerInstanceArns"])
        connected = [ci for ci in ci_details.get("containerInstances", []) if ci.get("agentConnected")]
        if not connected:
            # Instances exist but agent disconnected — terminate and replace
            try:
                import boto3 as _b3
                ec2_client = _b3.client("ec2", region_name=REGION)
                for ci in ci_details.get("containerInstances", []):
                    ec2_id = ci.get("ec2InstanceId")
                    if ec2_id:
                        ec2_client.reboot_instances(InstanceIds=[ec2_id])
                        print(f"Rebooted stale instance {ec2_id}")
            except Exception as reboot_err:
                print(f"Auto-reboot failed: {reboot_err}")
            
            return _resp(503, {
                "error": "ECS agent disconnected. Instance is being rebooted — try again in 2 minutes.",
                "retryable": True
            })
    except Exception as e:
        print(f"Container instance check failed: {e}")
    except Exception as e:
        print(f"Container instance check failed: {e}")

    existing = _check_running_tasks(vin)
    if existing:
        try:
            ecs.stop_task(cluster=CLUSTER, task=existing, reason=f"Replaced by new agent start for {vin}")
        except Exception:
            pass

    fwe_task_def = _task_family(os.environ.get("FWE_TASK_DEF", TASK_DEF))
    fwe_cap_provider = os.environ.get("FWE_CAPACITY_PROVIDER", "")

    # Get cert
    cert_pem = ""
    private_key = ""
    try:
        cert_table = ddb.Table(f"cms-{STAGE}-storage-vehicle-certificates")
        lookup_key = vehicle_id or vin
        cert_resp = cert_table.get_item(Key={"vehicleId": lookup_key})
        if "Item" in cert_resp:
            cert_pem = cert_resp["Item"].get("certificatePem", "")
            private_key = cert_resp["Item"].get("privateKey", "")
        else:
            # Fallback: scan by vin attribute
            scan_resp = cert_table.scan(
                FilterExpression="vin = :v",
                ExpressionAttributeValues={":v": vin or lookup_key}
            )
            if scan_resp.get("Items"):
                item = scan_resp["Items"][0]
                cert_pem = item.get("certificatePem", "")
                private_key = item.get("privateKey", "")
    except Exception as e:
        return _resp(400, {"error": f"Cert lookup failed: {e}"})

    if not cert_pem:
        return _resp(400, {"error": f"No certificate found for {vehicle_id}"})

    iot_endpoint = iot.describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
    can_iface = f"vcan{_next_vcan_index()}"

    # Build BOTH container overrides via the shared helper so the
    # fwe-agent/vehicle-ecu pairing is structural — no call site can
    # accidentally omit the sidecar override (Fix Group 8 / Fix Group 9,
    # spec 2026-08-04-cms-vehicle-trip-lifecycle-split).
    fwe_agent_base_override, vehicle_ecu_override = _build_fwe_container_overrides(
        vehicle_id or vin, vin, can_iface
    )
    # Merge the credential/endpoint env vars into the base override.
    # HARD GATE: these go on fwe-agent ONLY, never on vehicle-ecu.
    fwe_agent_override = dict(fwe_agent_base_override)
    fwe_agent_override["environment"] = (
        fwe_agent_base_override["environment"] + [
            {"name": "ENDPOINT_URL", "value": iot_endpoint},
            {"name": "CERTIFICATE", "value": cert_pem},
            {"name": "PRIVATE_KEY", "value": private_key},
            {"name": "TOPIC_PREFIX", "value": "cms/fleetwise/"},
        ]
    )

    try:
        resp = ecs.run_task(
            cluster=CLUSTER,
            taskDefinition=fwe_task_def,
            capacityProviderStrategy=[{
                "capacityProvider": fwe_cap_provider, "weight": 1, "base": 1,
            }] if fwe_cap_provider else [],
            overrides={"containerOverrides": [
                fwe_agent_override,
                vehicle_ecu_override,
            ]},
        )
    except Exception as e:
        return _resp(500, {"error": str(e)})

    task_arn = resp["tasks"][0]["taskArn"] if resp.get("tasks") else None
    if not task_arn:
        return _resp(500, {"error": f"RunTask failed: {resp.get('failures', [])}"})

    return _resp(200, {"success": True, "task_arn": task_arn, "vin": vin})


def _agent_stop(config):
    """Stop FWE agent tasks and mark the affected vehicles disconnected.

    Two scopes, and the difference is an authorization boundary, not a
    convenience:

      * `config['vehicleId']` present -> stop ONLY that vehicle's agent task.
        This is the form a connected-services caller may use, gated at the
        dispatch site by the CS fleet allowlist.
      * no target -> stop EVERY agent task in the cluster. Fleet-wide, and
        therefore admin-only at the dispatch site.

    Both scopes are implemented here rather than in two functions so the
    disconnect-marking below cannot drift between them.
    See issues/2026-09-23-agent-routes-do-not-authorize-connected-services-callers/.
    """
    target_vehicle_id = (config or {}).get("vehicleId")
    scope = "vehicle" if target_vehicle_id else "cluster"
    stopped = 0
    vins = set()

    if target_vehicle_id:
        target_vin = _vehicle_vin(target_vehicle_id)
        if not target_vin:
            return _resp(404, {
                "error": f"Unknown vehicle {target_vehicle_id}, or it has no VIN.",
            })
        task_arn = _check_running_tasks(target_vin)
        if not task_arn:
            # Idempotent: nothing running is the desired end state, not a failure.
            return _resp(200, {
                "success": True, "stopped": 0, "scope": scope,
                "vehicleId": target_vehicle_id,
                "detail": "No FWE agent task was running for this vehicle.",
            })
        tasks = [task_arn]
        vins.add(target_vin)
    else:
        tasks = ecs.list_tasks(cluster=CLUSTER)["taskArns"]
        if tasks:
            details = ecs.describe_tasks(cluster=CLUSTER, tasks=tasks)["tasks"]
            for t in details:
                for c in t.get("overrides", {}).get("containerOverrides", []):
                    for env in c.get("environment", []):
                        if env.get("name") == "VEHICLE_NAME":
                            vins.add(env["value"].split("-")[0])  # Strip timestamp suffix

    for arn in tasks:
        try:
            ecs.stop_task(cluster=CLUSTER, task=arn)
            stopped += 1
        except Exception as e:
            # Was `except: pass`. A bare except also swallows KeyboardInterrupt and
            # SystemExit, and silence here means an un-stopped task looks stopped.
            print(f"⚠️ stop_task failed for {arn}: {type(e).__name__}: {e}")
    # Mark vehicles as disconnected
    for vin in vins:
        vehicle_id = _resolve_vin_to_vehicle_id(vin)
        if not vehicle_id:
            print(f"⚠️ stop: no vehicle row found for vin={vin}; "
                  "connectionStatus not updated")
            continue
        try:
            ddb.Table(f"cms-{STAGE}-storage-vehicles").update_item(
                Key={"vehicleId": vehicle_id},
                UpdateExpression="SET connectionStatus = :cs",
                ExpressionAttributeValues={":cs": "disconnected"}
            )
        except Exception as e:
            print(f"⚠️ stop: failed to mark vin={vin} disconnected: "
                  f"{type(e).__name__}: {e}")
    result = {"success": True, "stopped": stopped, "scope": scope}
    if target_vehicle_id:
        result["vehicleId"] = target_vehicle_id
    return _resp(200, result)



def _agent_logs(vin):
    """Get recent FWE agent logs from CloudWatch for the specified VIN's task only."""
    try:
        # Find the running task for this VIN
        task_arn = _check_running_tasks(vin)
        if task_arn:
            task_id = task_arn.split("/")[-1]
            try:
                resp = logs_client.get_log_events(
                    logGroupName=f"/ecs/cms-{STAGE}/fwe-agent",
                    logStreamName=f"fwe/fwe-agent/{task_id}",
                    limit=100, startFromHead=False,
                )
                lines = [e["message"] for e in resp.get("events", [])]
                return _resp(200, {"logs": lines if lines else ["Agent starting, waiting for logs..."], "vin": vin})
            except logs_client.exceptions.ResourceNotFoundException:
                return _resp(200, {"logs": ["Agent starting, waiting for logs..."], "vin": vin})
        # No running task for this VIN
        return _resp(200, {"logs": [f"No FWE agent running for {vin}"], "vin": vin})
    except Exception as e:
        return _resp(200, {"logs": [f"No logs available: {e}"], "vin": vin})

def _agent_status(restrict_to_fleet=None):
    """Get running FWE agent tasks with VIN info.

    `restrict_to_fleet` limits the returned agents to vehicles enrolled in that
    fleet. Passed for a connected-services caller, which is what makes admitting
    them to this route safe: the unrestricted response enumerates every running
    agent's VIN across every fleet, and a CS caller is scoped to one fleet
    everywhere else in this API. Admitting them without scoping would have traded
    a 403 for a cross-fleet information leak.
    See issues/2026-09-23-agent-routes-do-not-authorize-connected-services-callers/.
    """
    # Check cluster health first
    cluster_healthy = True
    try:
        ci_resp = ecs.list_container_instances(cluster=CLUSTER, status="ACTIVE")
        if ci_resp.get("containerInstanceArns"):
            ci_details = ecs.describe_container_instances(cluster=CLUSTER, containerInstances=ci_resp["containerInstanceArns"])
            connected = [ci for ci in ci_details.get("containerInstances", []) if ci.get("agentConnected")]
            cluster_healthy = len(connected) > 0
        else:
            cluster_healthy = False
    except Exception:
        pass

    tasks = ecs.list_tasks(cluster=CLUSTER)["taskArns"]
    agents = []
    if tasks:
        details = ecs.describe_tasks(cluster=CLUSTER, tasks=tasks)["tasks"]
        for t in details:
            containers = {c["name"]: c for c in t["containers"]}
            fwe = containers.get("fwe-agent", {})
            vin = ""
            for co in t.get("overrides", {}).get("containerOverrides", []):
                for env in co.get("environment", []):
                    if env.get("name") == "VEHICLE_NAME":
                        vin = env["value"]
            agents.append({
                "taskArn": t["taskArn"],
                "status": t["lastStatus"],
                "health": fwe.get("healthStatus", "UNKNOWN"),
                "container": fwe.get("runtimeId", ""),
                "vin": vin,
                "vehicleName": vin,
            })
    if restrict_to_fleet:
        # Fail CLOSED: an agent whose VIN cannot be resolved to a vehicle, or
        # whose vehicle cannot be resolved to a fleet, is withheld rather than
        # shown. An unresolvable row is exactly the case where we do not know
        # whether the caller is entitled to see it.
        scoped = []
        for a in agents:
            vehicle_id = _resolve_vin_to_vehicle_id(a.get("vin"))
            if not vehicle_id:
                continue
            try:
                if resolve_vins_to_fleets([vehicle_id]).get(vehicle_id) == restrict_to_fleet:
                    scoped.append(a)
            except Exception as e:
                print(f"⚠️ _agent_status: fleet resolution failed for "
                      f"{vehicle_id}: {type(e).__name__}: {e}")
        agents = scoped
    return _resp(200, {"agents": agents, "clusterHealthy": cluster_healthy})

def _campaigns():
    """Return vehicles with active FWE campaigns (RUNNING + signalsToCollect)."""
    try:
        camp_table = ddb.Table(f"cms-{STAGE}-campaigns")
        resp = camp_table.scan(
            FilterExpression="#s = :r",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":r": "RUNNING"},
            ProjectionExpression="campaignId, targetArn, decoderManifestId, signalsToCollect",
        )
        result = {}
        for c in resp.get("Items", []):
            target = c.get("targetArn", "")
            has_signals = bool(c.get("signalsToCollect"))
            if target.startswith("vehicle:"):
                vin = target.split(":", 1)[1]
                result[vin] = {"campaignId": c["campaignId"], "hasSignals": has_signals, "target": "vehicle"}
            elif target == "all":
                result["_broadcast"] = {"campaignId": c["campaignId"], "hasSignals": has_signals, "target": "broadcast"}
        return _resp(200, {"campaigns": result})
    except Exception as e:
        return _resp(500, {"error": str(e)})

def _derives_applies_within_seconds(campaign: dict | None) -> int:
    """Derive appliesWithinSeconds from the vehicle's standing UDS campaign.

    The end-to-end latency for a fault to appear after _set_faults writes
    faultState has three measured components:
      _PRESENCE_IDLE_TICK_S   — presence check idle tick (~9 s, 10 s ceiling)
      fetch_interval          — time until the next DTC_QUERY fires
                                (signalsToFetch[0].executionFrequencyMs / 1000)
      collect_interval        — FWE batching window
                                (collectionScheme.periodMs / 1000)
      _PIPELINE_ALLOWANCE_S   — MSK + Flink processing overhead (~10 s)

    If the campaign row is absent or either cadence field is missing /
    unparseable, returns _APPLIES_WITHIN_FALLBACK_S (a conservative 60 s).

    The fallback applies ONLY when the derivation cannot run.  It is NOT a
    floor: an earlier revision returned ``max(derived, fallback)``, which made
    the whole derivation dead code because the 60 s fallback always exceeded
    the 40 s derived value — reinstating the hardcoded number this helper
    exists to remove, and pinning it with a test that asserted the dead
    behaviour.  Clamped only at the low end against an absurd reading, so a
    campaign claiming a 0.5 s cadence cannot promise the operator 21 s.
    """
    try:
        signals = campaign.get("signalsToFetch") or []
        fetch_ms = int(signals[0]["executionFrequencyMs"]) if signals else None
        period_ms = int(
            (campaign.get("collectionScheme") or {}).get("periodMs", 0) or 0
        )
        if not fetch_ms or not period_ms:
            return _APPLIES_WITHIN_FALLBACK_S
        derived = (
            _PRESENCE_IDLE_TICK_S
            + fetch_ms // 1000
            + period_ms // 1000
            + _PIPELINE_ALLOWANCE_S
        )
        # Floor only against an implausibly fast campaign, not against the
        # fallback — see the docstring.
        return max(derived, _APPLIES_WITHIN_MINIMUM_S)
    except Exception:
        return _APPLIES_WITHIN_FALLBACK_S


def _set_faults(vehicle_id: str, body: dict, user: str):
    """PUT /api/simulation/vehicle/{vehicleId}/faults

    Full-replacement write of faultState on the vehicle's DDB item.
    An empty maintenance_scenarios list clears all faults (REMOVE).

    Validation (fail-fast; converts _build_uds_dtc_map's silent-skip paths
    into explicit 400s):
      - Vehicle must exist and be FWE-class (vehicle-telemetry data source).
      - Standing uds-dtc-polling-<vin> campaign must be RUNNING → 409 otherwise.
      - Every event_id must exist in the catalog → 400.
      - Every catalog entry must carry a dtc_code → 400.
      - Every dtc_code must be resolvable to an ECU (valid P/C/B/U prefix) → 400.
      - Every dtc_code must be accepted by uds_dtc_responder.encode_dtc() → 400.
      - Must NOT call _ensure_uds_campaign (spec § Campaign side).
    """
    vehicles_table = ddb.Table(f"cms-{STAGE}-storage-vehicles")

    # --- vehicle class gate ---
    try:
        veh_resp = vehicles_table.get_item(Key={"vehicleId": vehicle_id})
    except Exception as e:
        return _resp(500, {"error": f"Vehicle lookup failed: {e}"})

    veh_item = veh_resp.get("Item")
    if not veh_item:
        return _resp(404, {"error": f"Vehicle {vehicle_id!r} not found"})

    # Determine vehicle class via the data-source field convention.
    # "cloud-telemetry" is MQTT-Direct; "vehicle-telemetry" is FWE.
    # Both the new canonical name and legacy alias are checked.
    #
    # A vehicle item lacking BOTH keys defaults to "vehicle-telemetry"
    # (FWE-class). This matches the doctrine in
    # services/connectors/oem1/_lib/data_source.py::is_cloud_telemetry_fleet,
    # whose docstring states: "Missing data_source defaults to
    # vehicle-telemetry". Staging seed data does not populate this
    # attribute on FWE vehicles (e.g. VEH-MICH-001), so this default is
    # required for the shipping code path to work — see Fix Group 2 of
    # 2026-08-05-cms-parked-vehicle-dtc-injection/tasks.md.
    data_source = (
        veh_item.get("dataSource")
        or veh_item.get("data_source")
        or "vehicle-telemetry"
    )
    _FWE_SOURCES = {"vehicle-telemetry", "onboard-fwe"}
    if data_source not in _FWE_SOURCES:
        return _resp(400, {
            "error": (
                f"Vehicle {vehicle_id!r} uses data source {data_source!r} "
                f"and does not support UDS DTC injection. "
                f"UDS/CAN is only available for FWE vehicles."
            )
        })

    maintenance_scenarios = body.get("maintenance_scenarios", [])

    # ── Input validation (Fix Group 1, Task 2 — Suggestion 1) ────────────
    # Type-validate and cap maintenance_scenarios before any DDB calls to
    # prevent malformed shapes from reaching the catalog loop and to bound
    # the per-invocation DDB call count.
    # Cap is 100: above 9 ECUs × 10 codes (= 90 max injectable) and well
    # below anything a legitimate caller would send.
    if not isinstance(maintenance_scenarios, list):
        return _resp(400, {"error": "maintenance_scenarios must be a list"})
    if len(maintenance_scenarios) > 100:
        return _resp(400, {"error": "maintenance_scenarios exceeds cap of 100"})
    if any(not isinstance(x, str) for x in maintenance_scenarios):
        return _resp(400, {"error": "maintenance_scenarios must contain strings"})
    # ── End input validation ──────────────────────────────────────────────

    # --- REMOVE path: empty list clears faultState ---
    if not maintenance_scenarios:
        try:
            vehicles_table.update_item(
                Key={"vehicleId": vehicle_id},
                UpdateExpression="REMOVE faultState",
            )
        except Exception as e:
            return _resp(500, {"error": f"Failed to clear faultState: {e}"})
        return _resp(200, {"success": True, "cleared": True})

    # --- validate: standing campaign precondition ---
    vin = veh_item.get("vin", vehicle_id)
    camp_table = ddb.Table(f"cms-{STAGE}-campaigns")
    has_running = False
    standing_campaign = None
    try:
        cr = camp_table.query(
            IndexName="targetArn-index",
            KeyConditionExpression="targetArn = :t",
            FilterExpression="#s = :r",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":t": f"vehicle:{vin}",
                ":r": "RUNNING",
            },
        )
        for c in cr.get("Items", []):
            cid = c.get("campaignId", "") or c.get("campaignName", "")
            if "uds-dtc-polling" in cid:
                has_running = True
                standing_campaign = c
                break
    except Exception as e:
        print(f"_set_faults: campaign query failed for {vin}: {e}")

    if not has_running:
        return _resp(409, {
            "error": (
                f"No standing 'uds-dtc-polling-{vin}' campaign is RUNNING for "
                f"vehicle {vehicle_id!r}. Assign the 'uds-dtc-polling' template "
                f"from the Campaigns tab, then retry."
            )
        })

    # --- validate and resolve each event_id → ECU ---
    event_table = ddb.Table(f"cms-{STAGE}-event-catalog")
    dtcs_by_ecu: dict = {}   # ECU# → [dtc_code, ...]
    all_event_ids = []

    for event_id in maintenance_scenarios:
        # Look up catalog entry
        try:
            item = event_table.get_item(Key={"event_id": event_id}).get("Item")
        except Exception as e:
            return _resp(500, {"error": f"Event catalog lookup failed for {event_id!r}: {e}"})

        if not item:
            return _resp(400, {"error": f"Unknown event_id {event_id!r}"})

        dtc = item.get("dtc_code")
        if not dtc:
            return _resp(400, {
                "error": (
                    f"Event {event_id!r} has no dtc_code in the catalog "
                    f"and cannot be injected via UDS."
                )
            })

        # Resolve ECU via prefix derivation (raises on unknown prefix)
        try:
            ecu_num = _resolve_ecu_from_code(dtc)
        except (ValueError, KeyError):
            return _resp(400, {
                "error": (
                    f"DTC code {dtc!r} (from event {event_id!r}) cannot be resolved "
                    f"to an ECU — the prefix is not one of P, C, B, U."
                )
            })

        # Gate on encodability — must call encode_dtc, not restate its rule
        if not _is_dtc_encodable(dtc):
            return _resp(400, {
                "error": (
                    f"DTC code {dtc!r} (from event {event_id!r}) is a synthetic code "
                    f"and is not UDS-encodable."
                )
            })

        dtcs_by_ecu.setdefault(ecu_num, []).append(dtc)
        all_event_ids.append(event_id)

    # --- build ecus map (long-form, matching uds_dtc_responder._parse_map) ---
    ecus: dict = {}
    for ecu_num, codes in sorted(dtcs_by_ecu.items()):
        ecu_cfg = _ECU_BY_NUMBER[ecu_num]
        ecus[str(ecu_num)] = {
            "req": ecu_cfg["req"],
            "resp": ecu_cfg["resp"],
            "dtcs": codes,
        }

    now_ts = datetime.now(timezone.utc).isoformat()
    request_id = str(uuid.uuid4())[:8]
    fault_state = {
        "ecus": ecus,
        "eventIds": all_event_ids,
        "setAt": now_ts,
        "setBy": user,
        "requestId": request_id,
    }

    try:
        vehicles_table.update_item(
            Key={"vehicleId": vehicle_id},
            UpdateExpression="SET faultState = :fs",
            ExpressionAttributeValues={":fs": fault_state},
        )
    except Exception as e:
        return _resp(500, {"error": f"Failed to write faultState: {e}"})

    applies_s = _derives_applies_within_seconds(standing_campaign)
    return _resp(200, {
        "success": True,
        "ecus": ecus,
        "appliesWithinSeconds": applies_s,
        "clearGuidance": (
            f"To clear: (1) Remove codes from faultState via PUT /faults with "
            f"maintenance_scenarios=[], then wait ~{applies_s} s for the ECU to stop "
            f"reporting. (2) Then mark cleared in the UI (Mark Cleared button). "
            f"Do NOT clear the UI row while the fault is still injected — "
            f"it will resurrect on the next FWE poll."
        ),
    })


def _get_faults(vehicle_id: str):
    """GET /api/simulation/vehicle/{vehicleId}/faults

    Returns the current faultState and the injectable set.

    injectable: [{eventId, dtcCode, ecu, description}] — catalog codes that:
      1. carry a dtc_code
      2. resolve to an ECU via _resolve_ecu_from_code()
      3. are accepted by _is_dtc_encodable()

    This is the intersection that PUT /faults will accept.  Returned
    server-side so the UI shows exactly what can be injected without
    a separate derive step.
    """
    vehicles_table = ddb.Table(f"cms-{STAGE}-storage-vehicles")
    try:
        veh_resp = vehicles_table.get_item(Key={"vehicleId": vehicle_id})
    except Exception as e:
        return _resp(500, {"error": f"Vehicle lookup failed: {e}"})

    veh_item = veh_resp.get("Item")
    if not veh_item:
        return _resp(404, {"error": f"Vehicle {vehicle_id!r} not found"})

    fault_state = veh_item.get("faultState")
    vin = veh_item.get("vin", vehicle_id)

    # Check standing campaign status
    camp_table = ddb.Table(f"cms-{STAGE}-campaigns")
    campaign_running = False
    try:
        cr = camp_table.query(
            IndexName="targetArn-index",
            KeyConditionExpression="targetArn = :t",
            FilterExpression="#s = :r",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":t": f"vehicle:{vin}",
                ":r": "RUNNING",
            },
        )
        for c in cr.get("Items", []):
            cid = c.get("campaignId", "") or c.get("campaignName", "")
            if "uds-dtc-polling" in cid:
                campaign_running = True
                break
    except Exception as e:
        print(f"_get_faults: campaign query failed for {vin}: {e}")

    # Build injectable set: scan event catalog, filter by resolvability + encodability
    event_table = ddb.Table(f"cms-{STAGE}-event-catalog")
    injectable = []
    try:
        scan_resp = event_table.scan()
        for entry in scan_resp.get("Items", []):
            dtc = entry.get("dtc_code")
            if not dtc:
                continue  # no DTC — not injectable via UDS
            try:
                ecu_num = _resolve_ecu_from_code(dtc)
            except (ValueError, KeyError):
                continue  # unknown prefix
            if not _is_dtc_encodable(dtc):
                continue  # synthetic/malformed code
            injectable.append({
                "eventId": entry.get("event_id", ""),
                "dtcCode": dtc,
                "ecu": ecu_num,
                "description": entry.get("description", ""),
            })
    except Exception as e:
        print(f"_get_faults: event catalog scan failed: {e}")

    return _resp(200, {
        "faultState": fault_state,
        "campaignRunning": campaign_running,
        "injectable": injectable,
    })


def _discover_iot():
    try:
        endpoint = iot.describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
        sts = boto3.client("sts")
        account = sts.get_caller_identity()["Account"]
        return _resp(200, {"success": True, "endpoint": endpoint, "region": REGION, "account": account})
    except Exception as e:
        return _resp(500, {"success": False, "error": str(e)})
