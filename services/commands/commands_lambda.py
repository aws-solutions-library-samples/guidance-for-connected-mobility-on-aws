"""
Remote Commands API Lambda — send commands to vehicles via IoT Core MQTT.
Routes:
  POST /api/commands/{vehicleId}     — send a command
  GET  /api/commands/{vehicleId}     — list command history for vehicle
  GET  /api/commands/catalog         — list available actuatable commands
"""
import json, os, re, time, uuid, boto3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from _lib.fleet_membership import classify_driver_self, resolve_vins_to_fleets
from _shared.routine_catalog import (
    get_routines_for_profile,
    profile_for_fuel_type,
    resolve_routine,
)

iot_data = boto3.client('iot-data')
ddb = boto3.resource('dynamodb')
STAGE = os.environ.get('DEPLOYMENT_STAGE', 'prod')
COMMANDS_TABLE = ddb.Table(os.environ.get('COMMANDS_TABLE', f'cms-{STAGE}-storage-commands'))
CATALOG_TABLE = ddb.Table(os.environ.get('SIGNAL_CATALOG_TABLE', f'cms-{STAGE}-signal-catalog'))
GEOFENCES_TABLE = ddb.Table(os.environ.get('GEOFENCES_TABLE', f'cms-{STAGE}-storage-geofences'))

CORS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type,Authorization',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
    'Content-Type': 'application/json',
}


def _resp(code, body):
    def default(o):
        if isinstance(o, Decimal): return int(o) if o % 1 == 0 else float(o)
        if isinstance(o, datetime): return o.isoformat()
        raise TypeError
    return {'statusCode': code, 'headers': CORS, 'body': json.dumps(body, default=default)}


# ── Authorization helpers — lifted verbatim from ─────────────────────────────
# services/simulation/lambda/simulation_lambda.py:77-260
# Single-source-of-truth for the fleet-scoped authz pattern.
# NO divergence — copy-paste per spec § Design § 6.

_OPERATOR_GROUPS = {'platform-admin', 'fleet-operator'}

# Every route whose helper above refuses driver-self tokens returns this. The helpers
# were copied from the simulation API, whose wording ("may not call the simulation
# API") leaked into this one.
_DRIVER_SELF_DENIED = 'Driver accounts may not call this route.'


# ── Driver-self remote commands ──────────────────────────────────────────────
# A driver signed in to the iOS app (custom:driverId, no operator group) may run the
# consumer remote controls on their OWN vehicle and read back only the commands they
# issued. Every other route on this API stays closed to them through the helpers
# below, which deny driver-self outright.
#
# Policy (user decisions 2026-09-26, issue
# 2026-09-26-ios-controls-403-driver-self-commands-api):
#   - commands: the six the app offers, remote_start included
#   - "own vehicle": `assignedVehicleId` on the driver's ACTIVE record in the
#     drivers table, read per request. Not the token's custom:vehicleId claim, which
#     stays valid for up to an hour after a reassignment.
#   - history: only rows this driver issued (`issuedByDriverId`)
#
# Known residual risk, accepted with the decision: main_api lets a driver reassign
# themselves (PUT /api/v1/drivers/{self}); the fleet-match check there is skipped
# when the driver row has no fleetId. That rule is therefore the boundary on which
# vehicle a driver can command.
_DRIVER_SELF_COMMANDS = frozenset({
    'lock_all_doors',
    'remote_start',
    'start_preconditioning',
    'flash_hazards',
    'find_my_vehicle',
    'open_charge_door',
})
# Each driver command is a boolean "do it"; the app always sends "1". Anything else
# is refused — "0" on lock_all_doors could mean unlock.
_DRIVER_SELF_VALUES = frozenset({'1', '1.0', 'true'})

DRIVERS_TABLE = ddb.Table(os.environ.get('DRIVERS_TABLE', f'cms-{STAGE}-storage-drivers'))


def _driver_assigned_vehicle(driver_id: str) -> Optional[str]:
    """Return the vehicleId assigned to an ACTIVE driver, else None.

    Fails closed: a missing row, a missing or non-active status (e.g. "on_leave"),
    no assignment, or a read error all return None.
    """
    if not driver_id:
        return None
    try:
        item = DRIVERS_TABLE.get_item(Key={'driverId': driver_id}).get('Item') or {}
    except Exception as e:
        print(f"⚠ _driver_assigned_vehicle({driver_id!r}): {type(e).__name__}: {e} — denying")
        return None
    if str(item.get('status') or '').strip().lower() != 'active':
        return None
    return str(item.get('assignedVehicleId') or '').strip() or None


def _authorize_driver_self_vehicle(caller: dict, vehicle_id: str) -> Optional[dict]:
    """None when the driver-self caller's active assignment is `vehicle_id`, else 403."""
    assigned = _driver_assigned_vehicle(caller.get('driver_self_id') or '')
    if not assigned or assigned != vehicle_id:
        return _resp(403, {'error': 'Drivers may only use their own assigned vehicle.'})
    return None


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
    # custom:dealerIds — same split-on-comma shape as custom:fleetIds above (F3).
    # Absent, empty, or separator-only values all yield the empty set (fail-closed).
    # Zero parsed grants means zero dealers, never all dealers — mirrors the P0
    # fail-open fix in main_api's fleet claim handling.
    user_dealer_ids = set(
        did.strip()
        for did in (claims.get('custom:dealerIds') or '').split(',')
        if did.strip()
    )

    is_driver_self, driver_id = classify_driver_self(claims, driver_self_enabled=True)
    # NO fail-open: is_admin is strictly 'platform-admin' in user_groups.
    # The P0 fail-open pattern has been removed — that was the authz bypass.
    is_admin = 'platform-admin' in user_groups
    is_viewer = 'fleet-viewer' in user_groups and 'fleet-operator' not in user_groups
    is_operator = 'fleet-operator' in user_groups
    is_guest = 'fleet-guest' in user_groups
    is_technician = 'dms-technician' in user_groups
    user_email = claims.get('email') or claims.get('cognito:username') or 'operator'

    return {
        'claims': claims,
        'user_groups': user_groups,
        'user_fleet_ids': user_fleet_ids,
        'user_dealer_ids': user_dealer_ids,
        'is_admin': is_admin,
        'is_viewer': is_viewer,
        'is_operator': is_operator,
        'is_guest': is_guest,
        'is_technician': is_technician,
        'is_driver_self': is_driver_self,
        'driver_self_id': driver_id,
        'user_email': user_email,
    }


# RO_ACTIVE_STATUSES: PascalCase values from DMS repair_orders.py (F2).
# Source of truth: guidance-for-dealer-management-system-on-aws/source/handlers/repair_orders.py
# Derive via: grep -n 'RO_ACTIVE_STATUSES\|status.*=.*"Draft"' \
#             ~/guidance-for-dealer-management-system-on-aws/source/handlers/repair_orders.py
# Active set rationale (decisions.md F2):
#   Draft       — initial status written by create_repair_order(); MUST be active or the
#                 dispatch→diagnose flow breaks before the technician can scan anything.
#   InProgress  — car currently in the bay (canonical active work state).
#   AwaitingParts — work paused but vehicle still on-site.
# Excluded:
#   Ready       — work complete, awaiting customer pickup (no longer in the bay).
#   Delivered   — vehicle handed back; tech is done with it.
#   Closed      — administratively closed; no active work.
#   Invoiced    — billing finalized; vehicle is gone.
_RO_ACTIVE_STATUSES = frozenset({"Draft", "InProgress", "AwaitingParts"})

# A STRICTER subset, used ONLY to justify the SERVICE_ONLY lift (F8).
#
# Security review G2/W1 established that an active RO proves paperwork, not presence. The
# dealer-scoped `create_repair_order` (DMS repair_orders.py) authorizes the *dealer* and never
# the VIN — no roster check, no fleet check — and unconditionally writes status "Draft". So a
# service-advisor at dealer D can create an RO naming ANY VIN, and every dms-technician at D
# would then inherit SERVICE_ONLY on that vehicle. `Draft` is precisely the state a
# paperwork-only RO sits in.
#
# `InProgress` / `AwaitingParts` mean a human at that dealership has actually begun or paused
# work on the vehicle, which is a materially stronger claim about the car being in the bay.
#
# Draft REMAINS in _RO_ACTIVE_STATUSES above, deliberately: the CMS dispatch flow creates ROs
# as Draft and the INERT/STATIONARY paths must keep working the moment a vehicle is dispatched
# (F2). Only the safety-class lift gets the narrower set. Two sets because there are two
# questions — "is this vehicle at your dealership" and "is work underway on it" — and F8's
# justification rests on the second.
_RO_PRESENCE_STATUSES = frozenset({"InProgress", "AwaitingParts"})

_DMS_REPAIR_ORDERS_TABLE = os.environ.get("DMS_REPAIR_ORDERS_TABLE", "")

# Denial reason codes returned by _check_technician_ro_access — distinct per T2.2 S1.
# Three separate codes because a single message "no active repair order" would make a
# dealer-filter bug indistinguishable from a status-filter bug in production logs.
_TECH_DENY_NO_RO = "no_ro_for_vin"          # VIN not found in vin-index at all
_TECH_DENY_WRONG_DEALER = "ro_dealer_mismatch"  # RO exists but not at caller's dealer(s)
_TECH_DENY_WRONG_STATUS = "ro_status_inactive"  # RO exists at dealer but status is terminal
# Distinct from _TECH_DENY_NO_RO for LOGS only — the caller-facing body is unchanged
# (security review G2/S3). Collapsing "the table env var is unset" into "no repair order
# exists" makes an unwired deployment indistinguishable from a legitimate denial in
# production logs, which is the opposite of the distinct-reasons discipline the other three
# codes exist to provide. Mirrors authorize_fleet_scope's `fleet_projection_unwired`.
_TECH_DENY_TABLE_UNCONFIGURED = "ro_table_unconfigured"
_TECH_ALLOW = "allowed"


def _check_technician_ro_access(
    vin: str, dealer_ids: set, ddb_resource, statuses: frozenset = None
) -> str:
    """Check whether a technician has an active RO for the given VIN at their dealership.

    `statuses` selects which RO states count as qualifying; defaults to
    `_RO_ACTIVE_STATUSES` ("this vehicle is at your dealership"). The SERVICE_ONLY gate
    passes `_RO_PRESENCE_STATUSES` instead ("work is actually underway on it") — see the
    comment on that constant for why the two questions differ and why the stricter one is
    required to justify lifting a safety class.

    Returns one of five reason codes (see _TECH_DENY_* constants above):
        _TECH_ALLOW           — active RO found at the caller's dealer
        _TECH_DENY_NO_RO      — no RO rows exist for this VIN at all
        _TECH_DENY_WRONG_DEALER — RO(s) exist for the VIN but none at the caller's dealer
        _TECH_DENY_WRONG_STATUS — RO at the caller's dealer exists but all are terminal
        _TECH_DENY_TABLE_UNCONFIGURED — the table env var is unset (deployment fault)

    Distinct codes matter for production diagnostics: T2.2 S1 (tasks.md) states a single
    generic message would make a dealer-filter bug indistinguishable from a status-filter bug.
    The same argument extends to the unconfigured case (G2/S3): an unwired deployment and a
    legitimate denial must not read identically in logs.

    Queries GSI 'vin-index' (F1: real index name, HASH vehicle_vin, ProjectionType ALL,
    ACTIVE).  Paginates via LastEvaluatedKey — a VIN with a long service history can exceed
    one DynamoDB page, and a single-page read would silently deny a legitimate technician.

    ALL failure modes are deny. The distinction between them is for the log, never for the
    caller: a misconfigured environment must never admit a technician, and must not tell an
    unauthenticated-adjacent caller which of the several reasons applied.

    Args:
        vin:         vehicle VIN (vehicle_vin attribute, the GSI hash key)
        dealer_ids:  set of dealer IDs from the caller's custom:dealerIds claim
        ddb_resource: boto3.resource('dynamodb') instance (injected for testability)
    """
    if not _DMS_REPAIR_ORDERS_TABLE:
        print(
            "⚠ _check_technician_ro_access: DMS_REPAIR_ORDERS_TABLE env var not set — "
            "denying. This is a DEPLOYMENT fault, not a caller fault: every technician "
            "invoke will fail closed until the Lambda's environment is wired. Distinct from "
            "no_ro_for_vin so the two do not read identically in logs (G2/S3)."
        )
        return _TECH_DENY_TABLE_UNCONFIGURED
    try:
        table = ddb_resource.Table(_DMS_REPAIR_ORDERS_TABLE)
        from boto3.dynamodb.conditions import Key
        # First pass: all ROs for this VIN (no filter — to distinguish no-ro from wrong-dealer)
        kwargs = {
            "IndexName": "vin-index",
            "KeyConditionExpression": Key("vehicle_vin").eq(vin),
        }
        all_items = []
        while True:
            resp = table.query(**kwargs)
            all_items.extend(resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key

        if not all_items:
            return _TECH_DENY_NO_RO

        # ROs exist for this VIN. Now check dealer membership.
        at_dealer = [item for item in all_items if item.get("dealer_id") in dealer_ids]
        if not at_dealer:
            return _TECH_DENY_WRONG_DEALER

        # ROs exist at the caller's dealer. Check whether any are in a qualifying status.
        qualifying = statuses if statuses is not None else _RO_ACTIVE_STATUSES
        active = [item for item in at_dealer if item.get("status") in qualifying]
        if not active:
            return _TECH_DENY_WRONG_STATUS

        return _TECH_ALLOW
    except Exception as e:
        print(f"⚠ _check_technician_ro_access({vin!r}): {type(e).__name__}: {e}")
        return _TECH_DENY_NO_RO


def _authorize_admin_only(caller: dict) -> Optional[dict]:
    """Return 403 unless caller is platform-admin.  Driver-self always 403.

    Used by /agent/stop (broad operation — all FWE agent tasks).
    """
    if caller['is_driver_self']:
        return _resp(403, {'error': _DRIVER_SELF_DENIED})
    if not caller['is_admin']:
        return _resp(403, {'error': 'Requires platform-admin role.'})
    return None


def _authorize_authenticated(caller: dict) -> Optional[dict]:
    """Return 403 for driver-self only.  All non-driver-self authenticated callers pass.

    Used by /presets and /discover-iot-endpoint (read-only, no fleet scope needed).
    Groupless callers are denied because they carry no group claim — not a driver-self,
    but also not an authenticated operator/viewer.
    """
    if caller['is_driver_self']:
        return _resp(403, {'error': _DRIVER_SELF_DENIED})
    if (
        not caller['is_admin']
        and not caller['is_operator']
        and not caller['is_viewer']
        and not caller['is_guest']
        and not caller['is_technician']
    ):
        return _resp(403, {'error': 'Requires fleet-operator, fleet-viewer, or platform-admin.'})
    return None


def _authorize_listing(caller: dict) -> Optional[dict]:
    """Return 403 for driver-self or unauthenticated (groupless) callers.

    Used by /list, /drivers, /campaigns — listing routes that return filtered results
    for operator/viewer but full results for admin.  Driver-self is always denied.
    """
    if caller['is_driver_self']:
        return _resp(403, {'error': _DRIVER_SELF_DENIED})
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
        return None


def _resolve_vehicle_fuel_type(vehicle_id: str) -> Optional[str]:
    """Look up ``fuelType`` for a vehicleId from the vehicles table.

    Returns the raw string, or None if the vehicle does not exist, has no fuelType,
    or the read fails. Callers MUST treat None as a refusal — see
    `_shared.routine_catalog.profile_for_fuel_type`, which will not guess a profile.

    Reads the same table and key as `_resolve_vehicle_id_to_vin`, so it needs no new
    IAM grant.
    """
    try:
        vehicles_table = ddb.Table(f"cms-{STAGE}-storage-vehicles")
        resp = vehicles_table.get_item(Key={"vehicleId": vehicle_id})
        item = resp.get("Item")
        if not item:
            return None
        return item.get("fuelType") or None
    except Exception as e:
        print(f"⚠ _resolve_vehicle_fuel_type({vehicle_id!r}): {type(e).__name__}: {e}")
        return None


def _authorize_per_vin(
    caller: dict, vin: Optional[str], vehicle_id: Optional[str], write_route: bool = False
) -> Optional[dict]:
    """Return None if authorized, else a 403/404 _resp().

    Per spec § R2:
      - driver-self  → always 403
      - admin        → unconditional bypass
      - dms-technician → admitted when active RO exists for the VIN at one of the
                         caller's dealerships (D7 / T2.2).  Checked BEFORE fleet claims
                         because a technician legitimately has no custom:fleetIds.
      - operator/viewer with matching fleet → admitted (viewer denied on write routes)
      - no fleet ids / non-matching fleet  → 403

    IMPORTANT — this function has TWO distinct internal consumers that key on
    TWO distinct identifiers, which is why it takes both `vin` and `vehicle_id`
    rather than one value under either name (corrected 2026-09-18, see
    issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/):
      - the DMS-technician branch calls `_check_technician_ro_access(vin, ...)`,
        which queries `dms-{stage}-repair-orders`'s `vin-index` GSI — genuinely
        keyed on the real VIN (`vehicle_vin` attribute).
      - the fleet-operator branch calls `resolve_vins_to_fleets(...)`, which
        queries `cms-{stage}-storage-fleet-enrollment`'s `vehicleId-index`
        GSI — keyed on `vehicleId`, NOT vin. That table has no `vin` attribute
        at all. Before this fix, every call site resolved a VIN and passed it
        to both branches, which silently failed the fleet check for the ~21 of
        69 staging vehicles where `vehicleId != vin`
        (issues/2026-09-05-vehicleid-diverges-from-vin/). `platform-admin`
        callers were never affected — `is_admin` short-circuits before either
        branch runs.

    Args:
        caller: caller dict from _extract_caller()
        vin: the vehicle's real VIN, or None if unresolvable. Used ONLY by the
            dms-technician branch (repair-order lookup).
        vehicle_id: the vehicle's vehicleId, or None if unresolvable. Used ONLY
            by the fleet-membership branch (fleet-enrollment lookup).
        write_route: if True, fleet-viewer is denied (viewers are read-only)
    """
    if caller['is_driver_self']:
        return _resp(403, {'error': _DRIVER_SELF_DENIED})
    if caller['is_admin']:
        return None
    if write_route and caller['is_viewer']:
        return _resp(403, {'error': 'Read-only (fleet-viewer) tokens may not call write routes.'})

    # ── DMS technician branch (T2.2) ──────────────────────────────────────────
    # Checked BEFORE the fleet-claim gate: technicians have no custom:fleetIds and
    # would otherwise fail at "Caller has no fleet assignments." before reaching here.
    # Presence is established by an active repair order on the VIN at one of the
    # caller's dealerships.  Fail-closed on absent/empty/unparseable dealer claim.
    if caller['is_technician']:
        dealer_ids = caller.get('user_dealer_ids') or set()
        if not dealer_ids:
            # Missing claim: fail-closed.  Name the PLURAL claim ('custom:dealerIds') —
            # test 5 pins the plural substring to guard against an impl naming the
            # non-existent singular 'custom:dealershipId' (F3).
            return _resp(403, {
                'error': (
                    'dms-technician callers require the custom:dealerIds claim '
                    '(absent or empty).'
                )
            })
        # `vin` may be None before resolution; treat that as no VIN to check.
        if not vin:
            return _resp(403, {'error': 'No VIN available to verify technician presence.'})

        reason = _check_technician_ro_access(vin, dealer_ids, ddb)
        if reason == _TECH_ALLOW:
            return None

        # Distinct denial messages per T2.2 S1 / tasks.md:
        # A single "no active RO" message makes a dealer-filter bug indistinguishable
        # from a status-filter bug in production logs — which is where they are diagnosed.
        if reason == _TECH_DENY_NO_RO:
            return _resp(403, {
                'error': (
                    'Access denied: no repair order exists for this vehicle in the '
                    'DMS repair-orders table.'
                )
            })
        if reason == _TECH_DENY_WRONG_DEALER:
            return _resp(403, {
                'error': (
                    'Access denied: repair order(s) found for this vehicle but none '
                    'is assigned to your dealership(s) (custom:dealerIds mismatch).'
                )
            })
        if reason == _TECH_DENY_WRONG_STATUS:
            return _resp(403, {
                'error': (
                    'Access denied: repair order found at your dealership for this vehicle '
                    'but it is not in an active status '
                    '(must be Draft, InProgress, or AwaitingParts).'
                )
            })
        # Every remaining reason is a deny, including _TECH_DENY_TABLE_UNCONFIGURED.
        #
        # Explicit rather than positional. Until G2/S3 added a fourth deny code, the
        # WRONG_STATUS message WAS the trailing fallthrough — so the new code inherited
        # "repair order found at your dealership but it is not in an active status", which is
        # false when the table env var is simply unset, and would have sent an operator
        # hunting a data problem that does not exist. A positional fallthrough is safe only
        # while nobody adds a case, which is not a property.
        #
        # The caller-facing text stays deliberately non-specific: the deployment fault is
        # named in the log (see _check_technician_ro_access), not in the response.
        return _resp(403, {
            'error': (
                'Access denied: could not verify a repair order for this vehicle at your '
                'dealership.'
            )
        })
    # ── End DMS technician branch ─────────────────────────────────────────────

    if not caller['is_operator'] and not caller['is_viewer']:
        return _resp(403, {'error': 'Requires fleet-operator, fleet-viewer, or platform-admin.'})
    if not caller['user_fleet_ids']:
        return _resp(403, {'error': 'Caller has no fleet assignments.'})
    # Fleet-membership check is keyed on vehicleId, not vin — see the docstring
    # above and issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/.
    clean_vehicle_ids = [vehicle_id] if vehicle_id else []
    if not clean_vehicle_ids:
        # No resolvable vehicleId — let the handler return its own error shape
        return None
    vehicle_id_to_fleet = resolve_vins_to_fleets(clean_vehicle_ids)
    unresolved = [v for v in clean_vehicle_ids if v not in vehicle_id_to_fleet]
    if unresolved:
        return _resp(404, {'error': f'Unknown vehicle(s): {unresolved}'})
    off_fleet = [v for v, f in vehicle_id_to_fleet.items() if f not in caller['user_fleet_ids']]
    if off_fleet:
        return _resp(403, {'error': f'Caller not authorized for fleet of vehicle(s): {off_fleet}'})
    return None


def _filter_to_caller_fleets(caller: dict, rows: list, row_to_vehicle_id_fn) -> list:
    """Filter a list of rows to those whose vehicles belong to the caller's fleets.

    Used by /list, /campaigns, /drivers listing routes.
    Admin callers receive the full list unmodified.
    Non-admin callers receive only rows whose vehicleIds resolve to their fleet(s).

    `row_to_vehicle_id_fn(row) -> list[str]` extracts the vehicleIds from a
    single row. Despite the historical parameter/callback name ("vin"), the
    fleet-membership check underneath (`resolve_vins_to_fleets`) resolves
    vehicleIds, not VINs — see
    issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/. As of
    2026-09-18 this function has no call sites in commands_lambda.py; kept
    (and corrected) rather than removed since it is a shared authz-shaping
    helper that a future listing route may reasonably need.
    """
    if caller['is_admin']:
        return rows
    if not caller['user_fleet_ids']:
        return []
    result = []
    for row in rows:
        vehicle_ids = row_to_vehicle_id_fn(row)
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
    path = event.get('path', '')
    method = event.get('httpMethod', '')

    if method == 'OPTIONS':
        return _resp(200, {})

    # GET /api/commands/catalog — list actuatable signals
    if path.endswith('/catalog') and method == 'GET':
        caller = _extract_caller(event)
        return _get_catalog(caller)

    # Geofence CRUD: /api/geofences and /api/geofences/{vehicleId}
    #
    # Authorized per-vehicle like every other route on this Lambda — see
    # issues/2026-09-26-commands-api-body-vin-bypasses-fleet-check/. Prior to
    # 2026-09-26 these three routes ran no `_extract_caller` and no
    # `_authorize_*` helper at all: any authenticated token (including a
    # driver-self token) could create, list, or delete geofences on any
    # vehicle. Driver-self is refused everywhere by design — geofence CRUD is
    # not in `_DRIVER_SELF_COMMANDS`; `_authorize_per_vin` denies them at
    # the top of the helper.
    if '/geofences' in path:
        caller = _extract_caller(event)
        if method == 'POST' and path.endswith('/geofences'):
            body = json.loads(event.get('body', '{}'))
            gf_vehicle_id = body.get('vehicleId') or 'ALL'
            # A geofence with vehicleId 'ALL' addresses every vehicle on the
            # platform. Broad-scope creates are admin-only regardless of the
            # caller's fleet claim — a fleet-operator has no legitimate reason
            # to create rules that shadow other operators' vehicles, and this
            # matches the pattern the listing routes (/list, /campaigns) use
            # via `_filter_to_caller_fleets`.
            if gf_vehicle_id == 'ALL':
                denied = _authorize_admin_only(caller)
                if denied:
                    return denied
            else:
                vin = _resolve_vehicle_id_to_vin(gf_vehicle_id)
                denied = _authorize_per_vin(caller, vin, gf_vehicle_id, write_route=True)
                if denied:
                    return denied
            return _create_geofence(body)
        if method == 'GET' and '/geofences/' in path:
            vid = path.split('/geofences/')[-1].split('/')[0]
            # `_get_geofences(vid)` returns rules keyed on both `vid` and 'ALL'.
            # Read authz is per-vehicle; the 'ALL' rows shipped alongside are a
            # server-side implementation detail, not a separate scope the
            # caller elected.
            vin = _resolve_vehicle_id_to_vin(vid)
            denied = _authorize_per_vin(caller, vin, vid, write_route=False)
            if denied:
                return denied
            return _get_geofences(vid)
        if method == 'DELETE' and '/geofences/' in path:
            gf_id = path.split('/geofences/')[-1].split('/')[0]
            # Look up the geofence to find its vehicleId, then authorize as a
            # write on that vehicle. Missing geofence → 404 (no fleet claim
            # can be probed with a random geofenceId).
            try:
                gf_item = GEOFENCES_TABLE.get_item(
                    Key={'geofenceId': gf_id}
                ).get('Item') or {}
            except Exception as e:
                print(f"⚠ geofence lookup for {gf_id!r}: {type(e).__name__}: {e}")
                return _resp(500, {'error': 'geofence lookup failed'})
            if not gf_item:
                return _resp(404, {'error': f'Unknown geofenceId: {gf_id}'})
            gf_vehicle_id = str(gf_item.get('vehicleId') or '').strip() or 'ALL'
            if gf_vehicle_id == 'ALL':
                denied = _authorize_admin_only(caller)
                if denied:
                    return denied
            else:
                vin = _resolve_vehicle_id_to_vin(gf_vehicle_id)
                denied = _authorize_per_vin(caller, vin, gf_vehicle_id, write_route=True)
                if denied:
                    return denied
            return _delete_geofence(gf_id)

    # POST /api/commands/{vehicleId} — send command
    if method == 'POST' and '/commands/' in path:
        vehicle_id = path.split('/commands/')[-1].split('/')[0]
        body = json.loads(event.get('body', '{}'))
        caller = _extract_caller(event)

        # Dispatch on command_type.
        # SOVD commands: read_dtcs, clear_dtcs, read_identity → _send_sovd_command
        # Actuation commands: run_routine → _send_sovd_command (D14 safety-class model)
        # Actuator commands (unset command_type) → _send_command (existing FWE protobuf path)
        # Unknown non-SOVD command_type → 400 BEFORE VIN resolution
        command_type = body.get('command_type')
        # Drivers never reach the diagnostics paths, whatever the body says. Refused
        # here, before `_send_sovd_command` validates the body, so a driver gets 403
        # rather than a 400 that reads as "fix your request".
        if command_type is not None and caller['is_driver_self']:
            return _resp(403, {'error': _DRIVER_SELF_DENIED})
        if command_type in ('read_dtcs', 'clear_dtcs', 'read_identity', 'read_freeze_frames', 'read_data',
                            'run_routine'):
            return _send_sovd_command(vehicle_id, body, caller, event)
        elif command_type is not None:
            # Unknown command_type value — reject 400 before any VIN resolution
            return _resp(400, {'error': f'Unknown command_type: {command_type!r}. '
                                       f'Valid SOVD types: read_dtcs, clear_dtcs, read_identity, read_freeze_frames, read_data, run_routine. '
                                       f'For actuator commands, omit command_type.'})
        else:
            return _send_command(vehicle_id, body, caller, event)

    # GET /api/commands/{vehicleId}/routines — routines applicable to this vehicle
    #
    # MUST be matched BEFORE the command-history block below. That block matches any
    # GET with '/commands/' in the path and parses the vehicleId as the first segment
    # after it, so `/api/commands/VEH-1/routines` would resolve to a history lookup
    # for VEH-1 and this route would be unreachable. Same ordering reason `/catalog`
    # is matched near the top of this handler.
    if method == 'GET' and '/commands/' in path and path.endswith('/routines'):
        vehicle_id = path.split('/commands/')[-1].split('/')[0]
        caller = _extract_caller(event)
        return _get_routines(vehicle_id, caller, event)

    # GET /api/commands/{vehicleId} — command history
    if method == 'GET' and '/commands/' in path:
        vehicle_id = path.split('/commands/')[-1].split('/')[0]
        caller = _extract_caller(event)
        params = event.get('queryStringParameters') or {}
        return _get_history(
            vehicle_id,
            int(params.get('limit', '20')),
            caller,
            event,
            type_filter=params.get('type'),
            session_filter=params.get('sessionId'),
        )

    return _resp(404, {'error': f'Unknown route: {method} {path}'})


def _send_sovd_command(vehicle_id, body, caller, event):
    """Handle SOVD diagnostic commands: read_dtcs and clear_dtcs.

    Validates the body, enforces fleet-scoped authz (HARD GATE E), resolves the
    VIN, publishes a JSON command to the SOVD sub-path topic, and persists an
    initial DDB row with type='sovd'.

    For clear_dtcs: additionally requires is_admin OR is_operator (HARD GATE I +
    spec § Design § 1 — viewers explicitly denied even after _authorize_per_vin passes).

    Topic: cms/commands/things/{vin}/executions/{correlation_id}/sovd/request
    """
    command_type = body.get('command_type')  # already validated by dispatcher

    # ── Attestation gate for clear_dtcs — required BEFORE authz (400 before 403) ──
    if command_type == 'clear_dtcs':
        attestation = body.get('attestation')
        if not attestation or not isinstance(attestation, dict):
            return _resp(400, {'error': 'attestation is required for clear_dtcs and must be an object.'})
        required_attestation_fields = ('text', 'user_email', 'timestamp_ms')
        missing = [f for f in required_attestation_fields if not attestation.get(f)]
        if missing:
            return _resp(400, {'error': f'attestation missing required fields: {missing}'})

    # ── Attestation gate for run_routine — required BEFORE authz (400 before 403) (D16) ──
    # Mirrors the clear_dtcs pattern at :291. 400 must precede 403 so the caller knows
    # what to fix before any authz decision is revealed.
    if command_type == 'run_routine':
        attestation = body.get('attestation')
        if not attestation or not isinstance(attestation, dict):
            return _resp(400, {'error': 'attestation is required for run_routine and must be an object.'})
        required_attestation_fields = ('text', 'user_email', 'timestamp_ms')
        missing = [f for f in required_attestation_fields if not attestation.get(f)]
        if missing:
            return _resp(400, {'error': f'attestation missing required fields: {missing}'})

    # ── Validate components (scan/read commands) or routine_id (run_routine) ──
    if command_type == 'run_routine':
        # run_routine uses routine_id, not components (D14 § Stage 3)
        routine_id = body.get('routine_id')
        if not routine_id or not isinstance(routine_id, str):
            return _resp(400, {'error': 'routine_id is required and must be a non-empty string.'})
        components = None  # not used by run_routine path
    else:
        components = body.get('components')
        if not components or not isinstance(components, list) or len(components) == 0:
            return _resp(400, {'error': 'components is required and must be a non-empty list.'})

    # ── FG2.3 validate or generate correlation_id (SR Cycle 2 Warning #3) — fail-fast BEFORE VIN resolution ──
    # Client-supplied correlation_id must match [a-zA-Z0-9-]{1,64} to prevent:
    #   (a) topic injection ('/'-containing IDs produce extra MQTT topic levels)
    #   (b) DDB primary-key overwrite (upsert semantics would overwrite attestation records)
    # Moved above VIN resolution 2026-09-01 per reviewer C4 Suggestion 1 / SR C3 Suggestion 3
    # — malformed IDs no longer trigger a wasted DDB Query before returning 400.
    correlation_id = body.get('correlation_id')
    if correlation_id:
        if not re.fullmatch(r'[a-zA-Z0-9\-]{1,64}', correlation_id):
            return _resp(400, {'error': 'invalid correlation_id'})
    else:
        # No client-supplied ID — generate a fresh UUID4 hex server-side
        correlation_id = uuid.uuid4().hex

    # ── VIN resolution ──
    vin = _resolve_vehicle_id_to_vin(vehicle_id)
    if not vin:
        return _resp(404, {'error': f'Vehicle {vehicle_id!r} not found or has no VIN.'})

    # ── Fleet-scoped authz (HARD GATE E) — write route ──
    # vehicleId powers the fleet-membership check; vin powers the technician
    # repair-order check — see the docstring on _authorize_per_vin.
    denied = _authorize_per_vin(caller, vin, vehicle_id, write_route=True)
    if denied:
        return denied

    # ── Additional role check for clear_dtcs: viewers denied even on write-route ──
    if command_type == 'clear_dtcs':
        if not caller['is_admin'] and not caller['is_operator']:
            return _resp(403, {'error': 'clear_dtcs requires fleet-operator or platform-admin role.'})

    # ── Additional role check for run_routine: viewers denied (same as clear_dtcs) ──
    if command_type == 'run_routine':
        if not caller['is_admin'] and not caller['is_operator'] and not caller['is_technician']:
            return _resp(403, {'error': 'run_routine requires fleet-operator or platform-admin role.'})

        # ── Routine authority: derive class AND applicability from the catalog ──
        #
        # F28: this block previously read `body.get('safety_class')` — the caller's
        # own declaration — and a comment claimed both it and the catalog were
        # checked, and that "a later sidecar step would catch it". Neither was true.
        # The sidecar trusted the same field and defaulted a missing value to INERT,
        # so omitting one field ran dpf_regeneration on a moving vehicle.
        #
        # The class now comes from the catalog and the caller's value is discarded,
        # the same treatment FG2.1 below applies to attestation.user_email versus the
        # Cognito claim. `resolve_routine` answers class and applicability together:
        # a routine absent from THIS vehicle's powertrain profile has no class for
        # this vehicle. That is the only place DX29 can be enforced at runtime — the
        # sidecar union-scans all four profiles because it does not know the
        # vehicle's powertrain, so it cannot tell a BEV from a diesel.
        routine_id = body.get('routine_id')
        fuel_type = _resolve_vehicle_fuel_type(vehicle_id)
        profile = profile_for_fuel_type(fuel_type)
        if profile is None:
            # Unknown or unreadable powertrain — refuse rather than assume a profile.
            # Assuming one would pick a routine set for a vehicle whose powertrain we
            # could not establish, which is the F28 shape again.
            return _resp(400, {
                'error': 'Cannot determine this vehicle\'s powertrain, so no routine '
                         'can be authorised for it. Ensure the vehicle record carries '
                         'a fuelType of electric, hybrid, gasoline or diesel.',
                'routine_id': routine_id,
            })

        routine = resolve_routine(routine_id, profile)
        if routine is None:
            # Unknown routine, or known but not applicable to this powertrain. Both
            # are one refusal on purpose: distinguishing them would let a caller
            # enumerate the routine catalog of a vehicle it does not own.
            return _resp(400, {
                'error': 'This routine is not available for this vehicle.',
                'routine_id': routine_id,
            })

        # Authoritative from here. Overwrite whatever the caller declared so no
        # downstream reader — including the sidecar, via the published payload — can
        # be steered by the request.
        safety_class = routine['safety_class']
        body['safety_class'] = safety_class

        if safety_class == 'SERVICE_ONLY':
            # The condition became CHECKABLE rather than the class being relaxed.
            # D25 classified these routines SERVICE_ONLY because their preconditions
            # include facts the cloud cannot verify — specifically, that a qualified
            # technician is physically present at the vehicle.  The rejection was a
            # stand-in for "we have no way to establish presence."  D7's active-RO
            # predicate in _active_ro_exists() is exactly that way: it proves the caller
            # is at the vehicle via a current repair order at their dealership.  This
            # gate does NOT make SERVICE_ONLY remotely invocable; it makes it invocable
            # by a caller the system can prove is physically present.  A dms-technician
            # whose active-RO predicate fails is denied as before.
            #
            # platform-admin is EXCLUDED deliberately (decisions.md F8 scope item 2):
            # platform-admin bypasses _authorize_per_vin but is not at the vehicle, and
            # physical presence is the entire justification for the lift.  Do NOT gate
            # on "admin or technician" — an admin inheriting a presence-justified
            # permission would be a safety regression, not a safety-preserving change.
            #
            # THE COMPOSITE CALLER (security review G2/W2). `is_admin` is tested here
            # explicitly, and it must be. _authorize_per_vin returns None at
            # `if caller['is_admin']` BEFORE reaching its technician branch, so a caller
            # holding BOTH platform-admin AND dms-technician never has the RO predicate
            # evaluated — and a gate written only as `not is_technician` then admits them.
            # That yielded SERVICE_ONLY on any VIN, with no RO existing anywhere and no
            # custom:dealerIds required. Nothing prevents that combination: the pool has
            # ~10 platform-admins and the seed script assigns dms-technician.
            #
            # Denying admin here is not the "admin or technician" pattern the paragraph
            # above warns against — that would ADMIT admin. This DENIES it, which is what
            # F8 scope item 2 decided.
            #
            # PRESENCE, NOT PAPERWORK (security review G2/W1). An active RO is necessary
            # but was not sufficient: the dealer-scoped create_repair_order authorizes the
            # dealer and never the VIN, and writes status "Draft" unconditionally, so a
            # service-advisor could mint an RO for any VIN and hand every technician at
            # that dealer a SERVICE_ONLY capability on it. The lift therefore requires
            # _RO_PRESENCE_STATUSES (InProgress / AwaitingParts) rather than the broader
            # active set — work actually underway, not paperwork filed. INERT and
            # STATIONARY keep the broader set so the CMS dispatch flow still works on a
            # freshly-created Draft RO.
            if caller.get('is_admin') or not caller.get('is_technician'):
                return _resp(400, {
                    'error': 'SERVICE_ONLY routines are not remotely invocable in v1 (D17). '
                             'This routine requires a physically present technician.',
                    'safety_class': safety_class,
                    'routine_id': routine_id,
                    'reason': routine.get('reason', ''),
                })

            # Technician confirmed. Now require the STRICTER presence predicate.
            if _check_technician_ro_access(
                vin,
                caller.get('user_dealer_ids') or set(),
                ddb,
                statuses=_RO_PRESENCE_STATUSES,
            ) != _TECH_ALLOW:
                return _resp(403, {
                    'error': 'SERVICE_ONLY routines require a repair order with work '
                             'underway (InProgress or AwaitingParts) for this vehicle at '
                             'your dealership. A newly-created or draft repair order is '
                             'not sufficient — this routine cannot be verified remotely, '
                             'so the vehicle must actually be in service.',
                    'safety_class': safety_class,
                    'routine_id': routine_id,
                    'reason': routine.get('reason', ''),
                })

    # ── FG2.1: Overwrite attestation.user_email with the Cognito-derived caller email ──
    # The client-supplied attestation.user_email MUST NOT be trusted — an operator
    # could forge another user's identity in the audit trail. The Cognito claim is
    # the authoritative identity source; replace the body field before any DDB PutItem
    # or MQTT publish. HARD GATE I audit integrity depends on this.
    #
    # Suggestion cleanup 2026-09-01 (SR C3 Suggestion 1): also server-clock
    # attestation.timestamp_ms alongside the existing server-side issuedAt.
    # Defense in depth — the audit record already anchors on issuedAt, but a
    # client-supplied timestamp is trivially forgeable and there is no legitimate
    # reason to trust it. Overwrite with server time in the same block.
    if command_type == 'clear_dtcs':
        body['attestation']['user_email'] = caller['user_email']
        body['attestation']['timestamp_ms'] = int(datetime.now(timezone.utc).timestamp() * 1000)

    # ── Server-clock attestation for run_routine (D16 / FG2.1 pattern) ──
    if command_type == 'run_routine':
        body['attestation']['user_email'] = caller['user_email']
        body['attestation']['timestamp_ms'] = int(datetime.now(timezone.utc).timestamp() * 1000)

    # ── Build and publish SOVD command ──
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)

    sovd_topic = f'cms/commands/things/{vin}/executions/{correlation_id}/sovd/request'
    command_payload = {
        'command_type': command_type,
        'correlation_id': correlation_id,
        'vehicle_id': vehicle_id,
        'vin': vin,
        'components': components,
        'include_freeze_frame': body.get('include_freeze_frame', False),
        'issued_at_ms': now_ms,
        'issued_by': caller['user_email'],
    }
    if command_type == 'clear_dtcs':
        command_payload['attestation'] = body['attestation']
    if command_type == 'run_routine':
        command_payload['routine_id'] = body.get('routine_id')
        command_payload['safety_class'] = body['safety_class']
        command_payload['attestation'] = body['attestation']
        # F34 (Fix Group 4, 2026-09-10 UAT): drop the None `components` key from
        # the run_routine payload. Wave 3 sets `components = None` earlier in
        # this function (see 'not used by run_routine path' branch) and then
        # `command_payload` above carries it through. The sidecar's
        # `_handle_sovd` reads `cmd.get('components', ['*'])` — but dict.get
        # returns the explicit None value, not the default — and then
        # unconditionally iterates it at line 1089 of realtime_telemetry_simulator.py
        # (`ecu_names = [c for c in components_req if c in _ecu_source]`),
        # crashing with TypeError before the run_routine dispatch at line 1741.
        # The sidecar also needs to defensively guard this (filed separately),
        # but for run_routine the Lambda should not send a field that isn't
        # semantically applicable.
        del command_payload['components']

    try:
        iot_data.publish(
            topic=sovd_topic,
            qos=1,
            payload=json.dumps(command_payload),
        )
    except Exception as e:
        return _resp(500, {'error': f'Failed to publish SOVD command: {str(e)}'})

    # ── Persist initial DDB row ──
    item = {
        'commandId': correlation_id,
        'vehicleId': vehicle_id,
        'type': 'sovd',
        'commandType': command_type,
        'status': 'SENT',
        'correlationId': correlation_id,
        'components': components,
        'issuedAt': now.isoformat(),
        'timestamp': now_ms,
        'topic': sovd_topic,
        'issuedBy': caller['user_email'],
        'ttl': int(time.time()) + 7 * 86400,  # 7 day TTL
    }
    if command_type == 'clear_dtcs':
        item['attestation'] = body['attestation']
    if command_type == 'run_routine':
        item['attestation'] = body['attestation']
        item['routineId'] = body.get('routine_id')
        item['safetyClass'] = body['safety_class']
    if body.get('include_freeze_frame'):
        item['includeFreezeFrame'] = True

    # ── T2.4: optional session_id — client-supplied diagnostic session correlation ──
    # Persisted as `session_id` on the DDB row so the diagnostics panel can filter
    # GET /api/commands/{vehicleId}?sessionId=<id> to a single session's activity.
    # - Client-supplied; NOT validated as UUID (any opaque identifier is acceptable).
    # - Rejected if > 64 chars (prevents excessively long sort keys / log strings).
    # - Additive attribute only — rows written before this change have no session_id
    #   and continue to render as prior activity (T2.4 constraint).
    session_id = body.get('sessionId')
    if session_id is not None:
        if not isinstance(session_id, str) or len(session_id) > 64:
            return _resp(400, {
                'error': 'sessionId must be a string of at most 64 characters.',
            })
        item['session_id'] = session_id

    # FG2.3: ConditionExpression prevents DDB upsert from overwriting an existing
    # attestation record when two callers collide on the same correlation_id.
    # ConditionalCheckFailedException → 409 Conflict.
    import botocore.exceptions
    try:
        COMMANDS_TABLE.put_item(
            Item=item,
            ConditionExpression='attribute_not_exists(commandId)',
        )
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return _resp(409, {'error': 'correlation_id already used'})
        raise

    return _resp(200, {
        'success': True,
        'commandId': correlation_id,
        'correlationId': correlation_id,
        'status': 'SENT',
        'topic': sovd_topic,
        'commandType': command_type,
    })


def _send_command(vehicle_id, body, caller, event):
    """Send an actuator command (FWE protobuf path). SOVD is dispatched via _send_sovd_command.

    Enforces fleet-scoped authz (HARD GATE E) on the VIN resolved from the body or
    vehicles table.
    """
    command_name = body.get('commandName')
    value = body.get('value')
    if not command_name:
        return _resp(400, {'error': 'commandName is required'})

    # Driver-self: authorize up front, and never take the VIN or signalId from the
    # body. A body VIN would address another vehicle's topic; a body signalId would
    # let an allowed commandName carry a different actuator's signal.
    driver_self = caller is not None and caller['is_driver_self']
    if driver_self:
        if command_name not in _DRIVER_SELF_COMMANDS:
            return _resp(403, {'error': f'{command_name!r} is not available to drivers.'})
        if str(value).strip().lower() not in _DRIVER_SELF_VALUES:
            return _resp(403, {'error': 'Driver commands accept only the value "1".'})
        denied = _authorize_driver_self_vehicle(caller, vehicle_id)
        if denied:
            return denied

    command_id = str(uuid.uuid4())[:12]
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)

    # Look up signal_id from catalog for this command
    signal_id = None if driver_self else body.get('signalId')
    if not signal_id:
        # Try to find by commandName in catalog actuators
        try:
            resp = CATALOG_TABLE.scan(
                FilterExpression='attribute_exists(actuator)',
                ProjectionExpression='signal_id, actuator',
            )
            for item in resp.get('Items', []):
                act = item.get('actuator', {})
                if act.get('commandName') == command_name:
                    signal_id = int(item['signal_id'])
                    break
        except Exception:
            pass

    # Look up VIN for this vehicle.
    #
    # The FWE agent's MQTT clientId is the VIN (ECS task override VEHICLE_NAME),
    # and FWE derives its command-request subscription from it:
    #   <commandsTopicPrefix>things/<clientId>/executions/+/request/protobuf
    # (aws-iot-fleetwise-edge v1.3.2, include/aws/iotfleetwise/TopicConfig.h:71,83).
    # So publishing with vehicleId in the VIN position lands on a topic no agent
    # subscribes to. Do NOT silently fall back to vehicle_id without saying so —
    # that fallback hid a missing IAM grant for six days and made the protobuf
    # path 100% dead while every health signal stayed green.
    # See issues/2026-08-04-fwe-remote-commands-not-actuating/ § D1.
    # VIN is authoritative from vehicles-table keyed on the URL vehicleId — never
    # from the request body. The prior contract (`vin = body.get('vin', '')` for
    # non-drivers, falling back to the vehicles-table lookup only when omitted)
    # was the P1 bypass in issues/2026-09-26-commands-api-body-vin-bypasses-fleet-check/:
    # `_authorize_per_vin` runs on the URL `vehicle_id` for the fleet branch, then
    # the topic is published to `body.vin`. A fleet-operator authorized for VEH-A
    # in fleet F1 could publish to a VIN in fleet F2 by putting that VIN in the
    # body.
    #
    # Policy (decided 2026-09-26 with the issue owner, "your call, documented"):
    #   - body `vin` differing from the server-resolved VIN → 400 (client
    #     contract error). No client sends `vin` in the body today — verified
    #     against clients/ios/…/RemoteCommandModels.swift `SendCommandRequest`,
    #     modules/cms_ui/.../RemoteCommandsPanel.tsx `sendCommand`, and the
    #     simulation API's command paths. 400 rather than 403 to avoid leaking
    #     authorization state (a 403 would say "the URL was authorized but the
    #     body wasn't", which discloses the shape of the check).
    #   - body `vin` matching the resolved VIN → accept (defence-in-depth for a
    #     future caller that echoes the field; not a required contract).
    #   - body omits `vin` → resolve server-side (the common path today).
    #
    # Driver-self ignored body vin before this change and continues to; the
    # policy above never runs for them because a driver's body vin is
    # discarded before this block. The resolve-inline (not via
    # _resolve_vehicle_id_to_vin) is deliberate — this path needs to
    # distinguish "lookup failed" from "row present but no vin attribute" in
    # its log line, and the helper collapses both to None (a design fine for
    # authz callers that care only about True/False, but not for the CloudWatch
    # signal that took six days to notice in the 2026-08-04 incident).
    resolved_vin = ''
    try:
        vehicles_table = ddb.Table(f'cms-{STAGE}-storage-vehicles')
        v = vehicles_table.get_item(Key={'vehicleId': vehicle_id}).get('Item') or {}
        resolved_vin = v.get('vin') or ''
        if not resolved_vin:
            print(f"⚠️  VIN lookup: no 'vin' attribute on vehicle {vehicle_id!r} — "
                  f"FWE protobuf topic will be addressed with the vehicleId and "
                  f"will NOT reach an FWE agent.")
    except Exception as e:
        print(f"⚠️  VIN lookup FAILED for {vehicle_id!r}: {type(e).__name__}: {e} — "
              f"falling back to vehicleId. The FWE protobuf topic will NOT reach "
              f"an FWE agent. Check the commands-api role's dynamodb:GetItem grant "
              f"on cms-{STAGE}-storage-vehicles.")

    if not driver_self:
        body_vin = body.get('vin')
        if body_vin and resolved_vin and body_vin != resolved_vin:
            return _resp(400, {
                'error': 'body vin does not match the vehicle at this URL. '
                         'Omit the vin field; the commands API resolves it '
                         'server-side from vehicleId.'
            })
    vin = resolved_vin
    vin_resolved = bool(vin)
    if not vin_resolved:
        # No VIN resolvable — fall through to the vehicleId as topic addressee.
        # Preserved as a diagnostic path from
        # issues/2026-08-04-fwe-remote-commands-not-actuating/ § D1.
        vin = vehicle_id

    # ── Fleet-scoped authz (HARD GATE E) — write route ──
    # vehicleId powers the fleet-membership check; vin powers the technician
    # repair-order check — see the docstring on _authorize_per_vin.
    # Driver-self callers were authorized at the top of this function;
    # _authorize_per_vin denies driver-self outright by design.
    if not driver_self:
        denied = _authorize_per_vin(caller, vin, vehicle_id, write_route=True)
        if denied:
            return denied

    # Build protobuf payload for FWE agent
    try:
        import command_request_pb2 as cmd_pb
        req = cmd_pb.CommandRequest()
        req.command_id = command_id
        req.timeout_ms = body.get('timeout', 10000)
        req.issued_timestamp_ms = now_ms
        if signal_id:
            req.actuator_command.signal_id = int(signal_id)
            req.actuator_command.decoder_manifest_sync_id = 'cms-fleet-v3'
            if isinstance(value, bool):
                req.actuator_command.boolean_value = value
            elif isinstance(value, (int, float)):
                req.actuator_command.double_value = float(value)
            elif isinstance(value, str):
                req.actuator_command.string_value = value
            else:
                req.actuator_command.double_value = float(value) if value is not None else 0.0
        proto_payload = req.SerializeToString()
    except Exception as e:
        print(f"Protobuf build error: {e}")
        proto_payload = None

    # Publish protobuf to FWE commands topic (matches FWE commandsTopicPrefix pattern)
    execution_id = command_id
    fwe_topic = f'cms/commands/things/{vin}/executions/{execution_id}/request/protobuf'
    legacy_topic = f'cms/commands/{vehicle_id}/request'
    try:
        if proto_payload:
            iot_data.publish(topic=fwe_topic, qos=1, payload=proto_payload)
        # Also publish JSON to legacy topic for MQTT direct simulators
        legacy_payload = json.dumps({
            'commandId': command_id, 'commandName': command_name,
            'vehicleId': vehicle_id, 'value': value,
            'issuedAt': now.isoformat(), 'issuedAtMs': now_ms,
            'timeout': body.get('timeout', 10000),
        })
        iot_data.publish(topic=legacy_topic, qos=1, payload=legacy_payload)
    except Exception as e:
        return _resp(500, {'error': f'Failed to publish command: {str(e)}'})

    # The primary destination is the FWE actuator topic when the protobuf
    # payload built successfully; otherwise only the legacy simulator topic
    # was reached.  Echo the reachable topic so callers and the DDB row agree.
    primary_topic = fwe_topic if proto_payload else legacy_topic

    # Store in DDB
    item = {
        'commandId': command_id,
        'vehicleId': vehicle_id,
        'commandName': command_name,
        'value': str(value) if value is not None else '',
        'status': 'SENT',
        'issuedAt': now.isoformat(),
        'timestamp': now_ms,
        'topic': primary_topic,
        'ttl': int(time.time()) + 7 * 86400,  # 7 day TTL
    }
    if body.get('label'):
        item['label'] = body['label']
    if body.get('category'):
        item['category'] = body['category']
    if driver_self:
        # What the driver's history view filters on. Taken from the token claim
        # (custom:driverId is immutable in the pool), never from the body.
        item['issuedByDriverId'] = caller['driver_self_id']

    COMMANDS_TABLE.put_item(Item=item)

    return _resp(200, {
        'success': True,
        'commandId': command_id,
        'status': 'SENT',
        'topic': primary_topic,
    })


def _get_routines(vehicle_id, caller=None, event=None):
    """Return the routines this vehicle may be offered, grouped-ready for the UI.

    Powertrain-scoped, not a catalog dump. Resolution is the same path `run_routine`
    uses at invoke time (`_resolve_vehicle_fuel_type` → `profile_for_fuel_type` →
    per-profile lookup), so the list an operator sees and the set the platform will
    actually accept cannot drift apart. A BEV is never offered `evap_purge` — it has
    no evaporative-emissions system (D23, F11, DX29).

    `SERVICE_ONLY` entries ARE included, flagged `invocable: False` and carrying their
    reason. Filtering them out would be the natural-looking thing to do and would
    defeat D17: the operator is meant to learn what the platform *will not* do
    remotely, and why, rather than being unable to tell "unavailable" from
    "unsupported".

    T2.6 — `?dtc=<code>` powertrain-aware DTC suggestion:
    When the ``dtc`` query parameter is supplied, routines suggested for that fault
    code (via ``dtc_suggestions.suggest_routines_for_dtc``) are prioritised and
    flagged with ``dtcSuggested: True``.  The suggestion is intersected with the
    vehicle's powertrain profile — a flat DTC map would suggest an ICE emissions
    routine on an EV (F6).  Routines not suggested are included after the suggested
    ones, all flagged ``dtcSuggested: False``.

    F8 — `invocableByCaller` field:
    ``invocable`` remains the static per-routine property (``safety_class != 'SERVICE_ONLY'``),
    unchanged for existing consumers (decisions.md F8 scope item 1).  A new
    ``invocableByCaller`` field reflects whether THIS caller may invoke the routine:
    technicians may invoke SERVICE_ONLY (presence established via active RO); all
    other authenticated callers follow the existing rule.

    Read-only, so any authenticated caller in scope may call it — a viewer can read
    the routine list without being able to invoke anything. Invocation authz is
    enforced separately in `_send_sovd_command` (fleet scope, then role, then
    attestation, then the sidecar's precondition re-check).
    """
    if caller is not None:
        denied = _authorize_authenticated(caller)
        if denied:
            return denied

    vin = _resolve_vehicle_id_to_vin(vehicle_id)
    if not vin:
        return _resp(404, {'error': f'Unknown vehicle: {vehicle_id}'})

    if caller is not None:
        # vehicleId powers the fleet-membership check; vin powers the technician
        # repair-order check — see the docstring on _authorize_per_vin.
        denied = _authorize_per_vin(caller, vin, vehicle_id)
        if denied:
            return denied

    fuel_type = _resolve_vehicle_fuel_type(vehicle_id)
    profile = profile_for_fuel_type(fuel_type)
    if profile is None:
        # Fail closed, same shape as the invoke path. Returning "all routines" or a
        # default profile for a vehicle whose powertrain we could not establish is
        # the F28 failure mode: a missing input resolving to a permissive outcome.
        return _resp(400, {
            'error': 'Cannot determine this vehicle\'s powertrain, so no routines '
                     'can be listed for it. Ensure the vehicle record carries a '
                     'fuelType of electric, hybrid, gasoline or diesel.',
            'vehicleId': vehicle_id,
        })

    # ── T2.6: optional DTC suggestion filter ──────────────────────────────────
    # Extract and normalise the DTC query parameter.  Absent/empty → no suggestion.
    # Normalised server-side: clients may send 'p0420' or 'P0420' interchangeably.
    params = {}
    if event is not None:
        params = event.get('queryStringParameters') or {}
    dtc_param = params.get('dtc') or None
    dtc_code: Optional[str] = dtc_param.upper().strip() if dtc_param else None

    # Determine the set of suggested routine IDs for this DTC, intersected with the
    # vehicle's powertrain profile (F6).  Without the intersection, suggest_routines_
    # for_dtc('P0420') would include o2_heater_check even on an EV — the EV profile
    # simply does not carry that routine, so the intersection removes it.
    suggested_ids: set = set()
    if dtc_code:
        from _shared.dtc_suggestions import suggest_routines_for_dtc
        profile_routine_ids = {e['routine_id'] for e in get_routines_for_profile(profile)}
        # Intersection: DTC map ∩ this vehicle's powertrain profile.
        #
        # HONEST LABEL: this is DEFENCE-IN-DEPTH, not the control. T2.6's mutation battery
        # removed this intersection and the EV test still passed (verdict NOT-CAUGHT, and the
        # verdict was right). The response below is built by iterating
        # get_routines_for_profile(profile), so a suggested id outside this vehicle's profile
        # is dropped by that loop whether or not this line runs. The loop is the control.
        #
        # Kept rather than deleted because suggested_ids is consumed twice — for membership
        # here and for ordering below — and an unintersected set would order ids that are not
        # in the response. But it must not be read as the powertrain guarantee.
        #
        # The real invariant — no DTC maps across incompatible powertrains — is enforced
        # exhaustively and DERIVED FROM THE CATALOG in _shared/tests/test_dtc_suggestions.py
        # (test_routines_under_one_dtc_share_a_common_profile). Before that test existed the
        # invariant rested on one spot-check against a hardcoded ICE-only list, which could
        # not have noticed a newly-added catalog routine.
        suggested_ids = set(suggest_routines_for_dtc(dtc_code)) & profile_routine_ids

    # Human-readable, class-level preconditions. Sent from the server so the client
    # renders rather than derives — the client must not maintain a second copy of the
    # safety vocabulary (F28's lesson), and must not evaluate these itself (D15: the
    # server owns vehicle-state preconditions).
    #
    # F8 (decisions.md): SERVICE_ONLY is now conditionally invocable by a technician
    # with an active RO.  The precondition text is therefore caller-aware rather than
    # a static string: serving "Not available remotely" to a technician who just
    # invoked a SERVICE_ONLY routine contradicts the response.
    is_technician_caller = caller is not None and caller.get('is_technician', False)

    # Can this technician invoke SERVICE_ONLY on this vehicle? If we are executing this line
    # for a technician, the answer is already yes — and that is worth spelling out, because it
    # is not obvious and it was got wrong twice.
    #
    # `_authorize_per_vin` ran above, and for a technician its vehicle-scope test IS the
    # active-RO predicate (T2.2). A technician without an active RO at their dealership never
    # reaches this point; they receive 403 from the read itself. So:
    #
    #   - The first implementation computed this from `is_technician_caller` (group
    #     membership). That was right by coincidence, not by construction — it agreed with the
    #     predicate only because the read gate had already enforced the predicate.
    #   - The second implementation re-queried DynamoDB here to "compute the real predicate".
    #     Also correct, and a redundant Query on every catalog fetch: the read gate had
    #     already established the same fact from the same index. Flagged at review Cycle 2.
    #
    # What it is now: derived from the fact that authorization passed, with the reason stated.
    # No second query.
    #
    # Can this technician invoke SERVICE_ONLY on this vehicle right now?
    #
    # THE READ GATE AND THE INVOKE GATE NO LONGER SHARE ONE PREDICATE. They did until
    # security review G2/W1, and an earlier version of this block said so and derived the
    # answer from "authorization already passed" with no query. That reasoning was correct
    # then and is wrong now: W1 introduced _RO_PRESENCE_STATUSES for the SERVICE_ONLY lift
    # while the read gate kept the broader _RO_ACTIVE_STATUSES (which includes Draft, because
    # CMS dispatch creates ROs as Draft and INERT/STATIONARY must work immediately — F2).
    #
    # So the two gates now call the same function with DIFFERENT `statuses`, and a technician
    # whose only RO is Draft passes the read and fails the SERVICE_ONLY invoke. Deriving this
    # value from "the read let me in" told exactly that caller `invocableByCaller: True` and
    # "Invocable by you now", and the invoke then returned 403 — the
    # served-value-contradicts-behaviour failure this field exists to prevent, caught as
    # Critical C1 in review Cycle 2.
    #
    # WORTH RECORDING, because it is the more general trap: fixing W1 invalidated the PREMISE
    # of the S1 fix. Nothing about S1's code changed and nothing flagged it. A control whose
    # correctness rests on two things being equal needs re-examining whenever either moves,
    # and a comment asserting the equality is not a test of it.
    #
    # Hence the DDB Query is back, deliberately, for technicians only. It was removed earlier
    # as redundant with the read gate; W1 made it non-redundant by giving the two gates
    # different questions to answer.
    # Viewers are excluded for the same reason, on a different axis (review Cycle 3 S1). The
    # catalog read runs `_authorize_per_vin(..., write_route=False)`, which does NOT apply the
    # read-only denial; the invoke path runs it with write_route=True, which denies a viewer
    # BEFORE the technician branch. So a fleet-viewer holding dms-technician would read the
    # catalog, be told invocable, and be refused on invoke — C1's shape again, via write_route
    # asymmetry rather than status asymmetry.
    #
    # That composite does not exist in the deployed pool today, and the reviewer graded it a
    # Suggestion for that reason. It is closed here anyway because the fix needs no semantic
    # decision about whether viewer+technician SHOULD exist — excluding viewers costs one
    # condition and makes the catalog/invoke invariant hold for every constructible caller
    # rather than for every caller that happens to be seeded. "No user has this combination"
    # is a fact about seed data; the invariant should be a property of the code.
    is_viewer_caller = caller is not None and caller.get('is_viewer', False)
    is_admin_caller = caller is not None and caller.get('is_admin', False)
    technician_may_invoke = False
    if is_technician_caller and not is_admin_caller and not is_viewer_caller:
        technician_may_invoke = (
            _check_technician_ro_access(
                vin,
                caller.get('user_dealer_ids') or set(),
                ddb,
                statuses=_RO_PRESENCE_STATUSES,
            ) == _TECH_ALLOW
        )

    # The precondition text follows the same fact. Serving "Not available remotely" to a
    # technician who can invoke it right now contradicts the response.
    service_only_precondition = (
        'Requires service visit. Invocable by you now — this vehicle has an active '
        'repair order at your dealership.'
        if technician_may_invoke
        else 'Requires service visit. Not available remotely.'
    )
    precondition_text = {
        'INERT': 'Read-only self-test. Requires the vehicle to be connected.',
        'STATIONARY': 'Requires the vehicle to be stationary, in park or neutral, '
                      'with ignition on. Checked again at the vehicle immediately '
                      'before the routine runs.',
        'SERVICE_ONLY': service_only_precondition,
    }

    all_routines = get_routines_for_profile(profile)

    # Build suggested list first (in suggestion order), then the remainder.
    if dtc_code and suggested_ids:
        from _shared.dtc_suggestions import suggest_routines_for_dtc
        ordered_suggested = [rid for rid in suggest_routines_for_dtc(dtc_code) if rid in suggested_ids]
        suggested_set = set(ordered_suggested)
        suggested_entries = [e for e in all_routines if e['routine_id'] in suggested_set]
        # Sort suggested_entries by the order in ordered_suggested
        suggested_order = {rid: i for i, rid in enumerate(ordered_suggested)}
        suggested_entries.sort(key=lambda e: suggested_order.get(e['routine_id'], 999))
        remaining_entries = [e for e in all_routines if e['routine_id'] not in suggested_set]
        ordered_entries = suggested_entries + remaining_entries
    else:
        ordered_entries = all_routines

    routines = []
    for entry in ordered_entries:
        safety_class = entry['safety_class']
        dtc_suggested = entry['routine_id'] in suggested_ids if dtc_code else None
        # `invocable`: static property — unchanged (decisions.md F8 scope item 1).
        # Existing consumers (CMS fleet-op UI) depend on this field's current semantics.
        invocable_static = safety_class != 'SERVICE_ONLY'
        # `invocableByCaller`: per-caller computed field (decisions.md F8 scope item 1).
        # Gated on the REAL predicate (active RO at the caller's dealership), not on group
        # membership — see the technician_may_invoke comment above. A technician with no
        # active RO for this vehicle gets False here and a 403 on invoke, which agree.
        invocable_by_caller = (
            True if technician_may_invoke else invocable_static
        )
        routine_entry = {
            'routineId': entry['routine_id'],
            'safetyClass': safety_class,
            'invocable': invocable_static,
            'invocableByCaller': invocable_by_caller,
            'precondition': precondition_text.get(safety_class, ''),
            'reason': entry.get('reason', ''),
        }
        if dtc_code is not None:
            routine_entry['dtcSuggested'] = bool(dtc_suggested)
        routines.append(routine_entry)

    resp_body = {
        'vehicleId': vehicle_id,
        'powertrainProfile': profile,
        'routines': routines,
    }
    if dtc_code is not None:
        resp_body['dtc'] = dtc_code
    return _resp(200, resp_body)


def _get_history(vehicle_id, limit=20, caller=None, event=None, type_filter=None, session_filter=None):
    """Return command history for a vehicle. Enforces fleet-scoped authz (HARD GATE E).

    Sort + pagination discipline (fix for
    `issues/2026-09-10-sovd-command-poll-latest-sort-broken/`):

    The GSI `vehicleId-index` is hash-only — DDB cannot return "latest N" from
    the query itself, and `ScanIndexForward` is a no-op without a sort key.
    Prior code applied `Limit=limit` at the DDB level THEN Python-sorted the
    result. With `limit=1` (which the client's poll uses), that returned an
    arbitrary partition-hash-order item, not the newest. The Python sort of a
    1-item list was a no-op. `?type=sovd` was ignored — mixed non-SOVD rows
    could occupy the sole slot.

    Fix: fetch up to `_HISTORY_FETCH_CAP` items (>> typical per-vehicle
    volume), filter by `type` when requested, sort by `timestamp` (with
    `updatedAt` fallback for legacy rows), then slice to `limit`. Cast to int
    inside the sort key so mixed Decimal / int / string values compare cleanly.

    T2.4 — session_filter (session_id):
    When ``session_filter`` is provided (from ``?sessionId=<id>``), rows are
    further narrowed to those whose ``session_id`` attribute matches.  The filter
    COMPOSES with ``type_filter`` — both predicates apply.  Rows without a
    ``session_id`` attribute (written before this change) are excluded only when a
    session filter is active; they continue to render as prior activity when no
    filter is supplied (T2.4 constraint: "rows without session_id must still be
    returned as prior activity").
    """
    # Resolve VIN for authz check
    issuer_filter = None
    if caller is not None and caller['is_driver_self']:
        # Drivers: own vehicle only, and only the commands they issued.
        denied = _authorize_driver_self_vehicle(caller, vehicle_id)
        if denied:
            return denied
        issuer_filter = caller['driver_self_id']
    elif caller is not None:
        vin = _resolve_vehicle_id_to_vin(vehicle_id)
        # vehicleId powers the fleet-membership check; vin powers the technician
        # repair-order check — see the docstring on _authorize_per_vin.
        #
        # Deliberate behavior change 2026-09-18: vehicle_id is passed here even
        # when vin fails to resolve. Previously an empty vin starved BOTH
        # branches (`[vin] if vin else []` -> `[]`), which meant a fleet-
        # operator reading history for a vehicle with a missing `vin`
        # attribute was silently admitted regardless of fleet -- a missing
        # data-quality attribute should never widen who may read a vehicle's
        # command history. vehicle_id is always available here (it is this
        # function's own parameter), so the fleet check now runs unconditionally.
        denied = _authorize_per_vin(caller, vin, vehicle_id, write_route=False)
        if denied:
            return denied

    _HISTORY_FETCH_CAP = 500  # safety valve; typical per-vehicle count is <50
    try:
        resp = COMMANDS_TABLE.query(
            IndexName='vehicleId-index',
            KeyConditionExpression='vehicleId = :v',
            ExpressionAttributeValues={':v': vehicle_id},
            Limit=_HISTORY_FETCH_CAP,
        )
        commands = resp.get('Items', [])

        # Driver-self: only rows this driver issued. Applied before sort and
        # slice, so `limit=N` returns the driver's N newest commands.
        if issuer_filter:
            commands = [c for c in commands if c.get('issuedByDriverId') == issuer_filter]

        # `?type=sovd` filter — the client's diagnostics-panel poll uses this
        # to scope to SOVD rows only. When omitted, all types are returned.
        if type_filter:
            commands = [c for c in commands if c.get('type') == type_filter]

        # `?sessionId=<id>` filter (T2.4) — narrows to a single diagnostic session.
        # COMPOSES with type_filter: both predicates apply when both are present.
        # Rows written before T2.4 (no session_id attribute) are excluded here
        # when a session filter is active — that is expected and correct.  Without
        # a session filter they are returned as prior activity (T2.4 constraint).
        if session_filter:
            commands = [c for c in commands if c.get('session_id') == session_filter]

        # Sort by timestamp desc. Cast to int to normalise Decimal (returned
        # by boto3 for DDB N type), int (older rows), and str (defensive) into
        # a common orderable type. Missing values sort to the bottom.
        def _sort_key(row):
            v = row.get('timestamp') or row.get('updatedAt') or 0
            try:
                return int(v)
            except (TypeError, ValueError):
                return 0

        commands.sort(key=_sort_key, reverse=True)

        # Slice to `limit` AFTER the sort — the DDB-level Limit is only a
        # safety cap on partition size, not a "return newest N" mechanism.
        commands = commands[:limit]
        return _resp(200, {'commands': commands, 'count': len(commands)})
    except Exception as e:
        return _resp(500, {'error': str(e)})


def _get_catalog(caller=None):
    """Return all actuatable signals grouped by category. Enforces authenticated authz.

    Driver-self callers are allowed and see only `_DRIVER_SELF_COMMANDS`.
    """
    driver_self = caller is not None and caller['is_driver_self']
    if caller is not None and not driver_self:
        denied = _authorize_authenticated(caller)
        if denied:
            return denied

    try:
        actuators = []
        resp = CATALOG_TABLE.scan(
            FilterExpression='attribute_exists(actuator)',
            ProjectionExpression='json_field, signal_name, vss_path, actuator, signal_group',
        )
        actuators.extend(resp.get('Items', []))
        while 'LastEvaluatedKey' in resp:
            resp = CATALOG_TABLE.scan(
                FilterExpression='attribute_exists(actuator)',
                ProjectionExpression='json_field, signal_name, vss_path, actuator, signal_group',
                ExclusiveStartKey=resp['LastEvaluatedKey'],
            )
            actuators.extend(resp.get('Items', []))

        if driver_self:
            actuators = [a for a in actuators
                         if (a.get('actuator') or {}).get('commandName') in _DRIVER_SELF_COMMANDS]

        # Group by category
        by_category = {}
        for a in actuators:
            act = a.get('actuator', {})
            cat = act.get('category', 'other')
            by_category.setdefault(cat, []).append({
                'commandName': act.get('commandName'),
                'label': act.get('label'),
                'category': cat,
                'valueType': act.get('valueType', 'boolean'),
                'min': act.get('min'),
                'max': act.get('max'),
                'unit': act.get('unit', ''),
                'options': act.get('options'),
                # str() deliberately: the wire type must be invariant. Catalog
                # items store this as a DDB string ("3000"), but this default
                # fired as a raw int, so an item MISSING the attribute emitted a
                # JSON number while an item having it emitted a string. The iOS
                # client decodes String? and DecodingError.typeMismatch on a
                # present-but-numeric value fails the WHOLE catalog response, not
                # just that entry — one missing attribute emptied the entire
                # command sheet. Absent-from-payload is handled gracefully by
                # clients; a wrong-typed value is not, so the default must match
                # the catalog's type. See
                # issues/2026-08-19-cms-command-catalog-response-timeout-contract/.
                'responseTimeout': str(act.get('responseTimeout', 5000)),
                'signalField': a.get('json_field'),
                'signalName': a.get('signal_name'),
                'vssPath': a.get('vss_path'),
                'signalGroup': a.get('signal_group'),
            })

        return _resp(200, {'actuators': by_category, 'totalCount': len(actuators)})
    except Exception as e:
        return _resp(500, {'error': str(e)})


def _create_geofence(body):
    gf_id = str(uuid.uuid4())[:12]
    now = datetime.now(timezone.utc).isoformat()
    vehicle_id = body.get('vehicleId', 'ALL')

    required = ['centerLat', 'centerLng', 'radiusKm']
    for f in required:
        if f not in body:
            return _resp(400, {'error': f'{f} is required'})

    item = {
        'geofenceId': gf_id,
        'vehicleId': vehicle_id,
        'name': body.get('name', f'Geofence {gf_id}'),
        'centerLat': Decimal(str(body['centerLat'])),
        'centerLng': Decimal(str(body['centerLng'])),
        'radiusKm': Decimal(str(body['radiusKm'])),
        'type': body.get('type', 'CIRCLE'),
        'action': body.get('action', 'ALERT'),
        'active': True,
        'createdAt': now,
        'ttl': int(time.time()) + 90 * 86400,  # 90 day TTL
    }
    GEOFENCES_TABLE.put_item(Item=item)

    return _resp(200, {'success': True, 'geofenceId': gf_id, 'geofence': item})


def _get_geofences(vehicle_id):
    try:
        # Get vehicle-specific + ALL geofences
        all_gf = []
        for vid in [vehicle_id, 'ALL']:
            resp = GEOFENCES_TABLE.query(
                IndexName='vehicleId-index',
                KeyConditionExpression='vehicleId = :v',
                ExpressionAttributeValues={':v': vid},
            )
            all_gf.extend(resp.get('Items', []))
        all_gf.sort(key=lambda x: x.get('createdAt', ''), reverse=True)
        return _resp(200, {'geofences': all_gf, 'count': len(all_gf)})
    except Exception as e:
        return _resp(500, {'error': str(e)})


def _delete_geofence(geofence_id):
    try:
        GEOFENCES_TABLE.update_item(
            Key={'geofenceId': geofence_id},
            UpdateExpression='SET active = :f',
            ExpressionAttributeValues={':f': False},
        )
        return _resp(200, {'success': True})
    except Exception as e:
        return _resp(500, {'error': str(e)})
