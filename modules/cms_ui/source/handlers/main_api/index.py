import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
import boto3
from datetime import datetime, timedelta, timezone
from cache_client import create_cached_dynamodb_client
from connection_status import resolve_connection_status
from decimal import Decimal
from event_catalog_helper import enrich_event_with_catalog, normalize_event_response
# Connected Services proxy (spec 2026-09-10-cms-connected-services-consumer).
# Imported as a module, not `from ... import *`: every reference below is
# qualified so a reader of the route block can tell at a glance which behaviour
# is this file's and which is the proxy's — the proxy deliberately does NOT
# fleet-scope-filter, and that boundary is easier to keep straight when the
# call sites name it.
import connected_services_proxy

# Create cached DynamoDB client
redis_endpoint = os.environ.get('REDIS_ENDPOINT')
dynamodb_client = create_cached_dynamodb_client(redis_endpoint)
dynamodb = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
s3_client = boto3.client('s3')

# ── Fleet-actions response allowlist ──────────────────────────────────────────
# GET /api/v1/fleet-actions returned raw DynamoDB items. `_normalize_action()`
# canonicalises severity/priority and a few other fields but MUTATES IN PLACE and
# returns the whole row — it is a normaliser, not a projection. So every attribute
# on the row egressed.
#
# The prod table carries `tenantId` (a customer brand name) on ~1/3 of rows, which is
# the second half of the 2026-08-10 exposure audit
# (issues/2026-08-10-cms-demo-external-exposure/). The service-history allowlist
# closed 112 of the 144 affected rows; this closes the remaining 32.
#
# It also drops identity and session fields the UI never reads, which the audit did
# not specifically flag but which are the same class of accidental egress:
#   tenantId          customer brand name
#   driverId          an AUTHORIZATION input — main_api's _classify_driver_self
#                     grants driver-scoped access on it; no reason to hand it out
#   driverName        a person's name (e.g. a named demo persona)
#   resolvedBy        an operator identity; carries email addresses on ~1/3 of
#                     resolved rows
#   connectContactId  internal Connect contact id
#   voiceSessionId    internal voice-session id
#   triageSessionId   internal triage-session id
#
# Derived from what the sole consumer reads. `FleetCommandCenter.tsx` accesses exactly
# `actionId, agentResponse, createdAt, domain, priority, severity, status` — verified
# by enumerating every `a.<field>` in that file, and confirmed it does not spread or
# JSON.stringify a raw action. The four additions below are the action's own subject
# (`vehicleId`, `vin`, `actionType`) and its resolution time (`resolvedAt`), which
# `_normalize_action` explicitly sets and which carry no identity.
#
# Allowlist, not denylist: this table is written by FOUR independent producers (see the
# note in the GET route), so a denylist would silently pass the next field any of them
# adds.
_FLEET_ACTION_RESPONSE_FIELDS = frozenset({
    'actionId',
    'actionType',
    'agentResponse',
    'createdAt',
    'domain',
    'priority',
    'resolvedAt',
    'severity',
    'status',
    'vehicleId',
    'vin',
})


def _project_fleet_actions(items):
    """Return fleet-action items reduced to the response allowlist.

    Applied AFTER `_normalize_action()`, so the canonicalised severity/priority values
    survive and only the un-allowlisted attributes are dropped.
    """
    if not items:
        return []
    return [
        {k: v for k, v in item.items() if k in _FLEET_ACTION_RESPONSE_FIELDS}
        for item in items
    ]


# ── Service-history response allowlist ────────────────────────────────────────
# GET /api/v1/service-history previously returned raw DynamoDB items verbatim
# (`'serviceRecords': response.get('Items', [])`). That is how a data-layer value
# became an API-layer exposure: the prod table carries `tenantId` on 112 rows with
# a customer brand name, and the route handed it to every caller even though no UI
# consumer reads it.
#
# Found by the 2026-08-10 prod exposure audit —
# issues/2026-08-10-cms-demo-external-exposure/. Two findings there; this closes
# the *mechanism* half. The other half (`fleet-viewer` grants unscoped cross-fleet
# read, so who can call this is wider than intended) is a separate group-model
# decision tracked as backlog `Viewer scope defect`. This projection is worth
# having regardless of how that lands: it would have contained the brand exposure
# even with the group defect open, which is the argument for defence in depth
# rather than a single choke point.
#
# The allowlist is derived from what the UI actually consumes — every `r.<field>`
# and `serviceDetails.<field>` read in
# `components/alerts/maintenance/ServiceDashboard.tsx` (the sole GET consumer).
# `tenantId` is deliberately absent: it appears nowhere in that consumer.
#
# Allowlist, not denylist, on purpose. A denylist would have to enumerate every
# sensitive field, and would silently pass the next one a writer adds — the same
# reasoning that makes `.gitignore`-style guards the wrong shape for secrets
# (see ~/.kiro/steering/public-mirror-publish.md § the exclusion-mechanism rule).
_SERVICE_HISTORY_RESPONSE_FIELDS = frozenset({
    'alertId',
    'category',
    'cost',
    'createdAt',
    'dealerId',
    'description',
    'estimatedCost',
    'estimatedDuration',
    'notes',
    'provenance',
    'provider',
    'serviceDate',
    'serviceDetails',
    'serviceId',
    'serviceType',
    'status',
    'updatedAt',
    'vehicleId',
    'vin',
})


def _project_service_records(items):
    """Return service-history items reduced to the response allowlist.

    Non-recursive by design: `serviceDetails` is passed through whole because the
    UI reads several keys from it and its contents are written by this same
    Lambda. If a future writer starts putting tenant or account identifiers inside
    `serviceDetails`, this projection will NOT catch them — that is a known limit,
    recorded here rather than left implicit.
    """
    if not items:
        return []
    return [
        {k: v for k, v in item.items() if k in _SERVICE_HISTORY_RESPONSE_FIELDS}
        for item in items
    ]


# ── DMS-owned service fields (D-G5c, D-G5i) ──────────────────────────────────
# When GET /api/v1/service-history merges a DMS RO with a cache row, these keys
# (in CMS camelCase names) are authoritative from DMS. The merge is
# {**cache_extras, **mapped_dms_fields}, so a cache value cannot overwrite a DMS
# value on any of these keys.
#
# Derived from D-G5i's field map:
#   ro_id → serviceId, dealer_id → dealerId, dealer_name → provider,
#   status → status, description → description,
#   opened_at||created_at → serviceDate, created_at → createdAt,
#   updated_at → updatedAt, vehicle_vin → vin (only when ≠ vehicleId),
#   resolved vehicleId → vehicleId.
# NOT DMS-owned (cache supplies): serviceType, category, cost, estimatedCost,
#   estimatedDuration, notes, serviceDetails, alertId.
#
# The frozenset literal is the policy. A test in test_service_history_dms_backed.py
# pins it exactly, so adding a field requires updating both places and produces a
# visible diff rather than a silent widening.
_DMS_OWNED_SERVICE_FIELDS = frozenset({
    'serviceId',
    'vehicleId',
    'vin',
    'dealerId',
    'provider',
    'status',
    'description',
    'serviceDate',
    'createdAt',
    'updatedAt',
})


def _ro_to_service_record(
    dms_ro: dict,
    resolved_vehicle_id: str = '',
) -> dict:
    """Map a DMS narrow-projection RO to a CMS service-history record shape.

    Implements the D-G5i field table exactly.  A key is emitted only when it
    has a non-empty value, so a DMS row missing a field does not blank the
    corresponding cache entry on merge.

    Parameters
    ----------
    dms_ro:
        A single item from the ``{"items": [...]}`` envelope returned by
        DMS ``GET /api/dms/fleet/repair-orders``.  Fields present:
        ``ro_id``, ``vehicle_vin``, ``dealer_id``, ``dealer_name``
        (added by DMS's ``_project_narrow``), ``status``, ``opened_at``,
        ``created_at``, ``updated_at``, ``description``, plus optionally
        ``initiated_by`` and ``recall_id`` (not mapped into CMS allowlist).
    resolved_vehicle_id:
        The vehicleId resolved by the VIN hop (D-G5e).  May differ from
        ``vehicle_vin`` (3 of 8 sampled vehicles).  Empty string when
        unresolvable.
    """
    mapped: dict = {}

    # ro_id → serviceId (D-G5i row 1)
    _ro_id = str(dms_ro.get('ro_id') or '').strip()
    if _ro_id:
        mapped['serviceId'] = _ro_id

    # resolved vehicleId → vehicleId (D-G5i row 10)
    if resolved_vehicle_id:
        mapped['vehicleId'] = resolved_vehicle_id

    # vehicle_vin → vin ONLY when it differs from the resolved vehicleId (D-G5e, D-G5i row 9)
    _veh_vin = str(dms_ro.get('vehicle_vin') or '').strip()
    if _veh_vin and _veh_vin != resolved_vehicle_id:
        mapped['vin'] = _veh_vin

    # dealer_id → dealerId (D-G5i row 2)
    _dealer_id = str(dms_ro.get('dealer_id') or '').strip()
    if _dealer_id:
        mapped['dealerId'] = _dealer_id

    # dealer_name → provider (D-G5i row 3); empty string when unknown roster id
    _dealer_name = dms_ro.get('dealer_name')
    if _dealer_name is not None:
        # Always emit; an empty string signals "not in roster" cleanly (D-G3d)
        mapped['provider'] = str(_dealer_name)

    # status → status (D-G5i row 4); DMS vocabulary ('Draft'), not CMS's 'scheduled'
    _status = str(dms_ro.get('status') or '').strip()
    if _status:
        mapped['status'] = _status

    # description → description (D-G5i row 5)
    _desc = str(dms_ro.get('description') or '').strip()
    if _desc:
        mapped['description'] = _desc

    # opened_at || created_at → serviceDate (D-G5i row 6)
    _service_date = str(
        dms_ro.get('opened_at') or dms_ro.get('created_at') or ''
    ).strip()
    if _service_date:
        mapped['serviceDate'] = _service_date

    # created_at → createdAt (D-G5i row 7)
    _created_at = str(dms_ro.get('created_at') or '').strip()
    if _created_at:
        mapped['createdAt'] = _created_at

    # updated_at → updatedAt (D-G5i row 8)
    _updated_at = str(dms_ro.get('updated_at') or '').strip()
    if _updated_at:
        mapped['updatedAt'] = _updated_at

    # initiated_by and recall_id: NOT in CMS allowlist — deliberately unmapped (D-G5i)

    return mapped


# ── Redirect refusal for token-bearing outbound calls (D-G5f) ─────────────────
# Hoisted from inside GET /api/v1/dealers per D-G5f: T5.2 adds two more
# outbound calls that carry the caller's Cognito token and each needs identical
# treatment. Three copies of a security control is the drift shape; sharing is
# available here and strictly better.
#
# The dealers branch keeps its own response bodies, log lines and endpoint
# validation unchanged — only the class definition moved. The four redirect tests
# in test_dealers_endpoint.py patch HTTPSHandler.https_open / HTTPHandler.http_open
# (below the opener), so they pass identically whether the class is defined inside
# the branch or at module scope.
#
# The endpoint validation (`https://` check and empty check) is NOT merged into
# this class — that check is caller-specific and stays inline. This class only
# refuses the redirect.

class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect on a request that carries the caller's token.

    The caller performs an `https://` check exactly once before the first byte
    is sent. The default ``urlopen`` opener installs ``HTTPRedirectHandler``,
    which re-issues the request to whatever ``Location`` the response names —
    carrying the ``Authorization`` header with it. A compromised or misconfigured
    downstream service could therefore answer ``302 Location: http://…`` and
    receive a live Cognito token that the one-shot scheme check never re-examined.

    Refusing outright rather than re-validating: these routes talk to exactly one
    known service at a configured address, so a redirect is never a legitimate
    outcome. Re-validating would be a policy to keep correct; refusing is a
    property.

    Raising ``HTTPError`` puts the 3xx on the existing error path, which maps
    anything outside (401, 403) to 502.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code,
            'redirect refused on a token-bearing request', headers, fp,
        )


# ── API field normalization (camelCase boundary) ──────────────────────────────
# Codified contract for the /api/v1/vehicles/{id} response shape. DDB writers
# may write snake_case or camelCase (see seed scripts, admin Lambdas, Flink
# processors); this Lambda normalizes on read so UI consumers see camelCase
# only — except for an allowlist of fields preserved as-is by convention
# (oem_source, oem1_*, subscription_service_activation_date,
# assigned_driver_id, enrollment_pending, lat, lng).
#
# Spec: cms/.kiro/specs/2026-06-09-cms-api-field-normalization/spec.md
# Contract: docs/tech.md § "Vehicle API field convention"
#
# Allowlist via omission: any key NOT in _SNAKE_TO_CAMEL passes through
# unchanged. Non-recursive: only top-level dict keys are renamed; nested
# dicts (e.g. inside `currentLocation`) are NOT recursed.
_SNAKE_TO_CAMEL = {
    # Vehicle metadata
    'license_plate':        'licensePlate',
    'vehicle_type':         'vehicleType',
    'fleet_id':             'fleetId',
    'fleet_name':           'fleetName',
    'fuel_type':            'fuelType',
    'fuel_level':           'fuelLevel',
    'battery_level':        'batteryLevel',
    'last_maintenance':     'lastMaintenance',
    'next_maintenance_due': 'nextMaintenanceDue',
    'insurance_expiry':     'insuranceExpiry',
    'registration_expiry':  'registrationExpiry',
    'driver_assigned':      'driverAssigned',
    'auto_registered':      'autoRegistered',
    'has_certificate':      'hasCertificate',
    'last_updated':         'updatedAt',         # legacy alias collapse
    'last_seen_at':         'lastSeenAt',
    'enrolled_at':          'enrolledAt',
    'activated_at':         'activatedAt',
    # Trip-shape (consolidated GET vehicle response 'trips' array)
    'trip_id':              'tripId',
    'driver_name':          'driverName',
    'assigned_driver':      'driverName',        # alias collapse
    'total_distance':       'totalDistance',
    'total_length':         'totalDistance',     # alias collapse
    # MaintenanceAlert-shape ('maintenance' array)
    'alert_type':           'alertType',
    'due_date':             'dueDate',
    'scheduled_date':       'dueDate',           # alias collapse
}


def _camelize(d):
    """Rename known snake_case keys to camelCase per ``_SNAKE_TO_CAMEL``.

    Allowlist via omission: any key not in the map passes through unchanged.
    Non-recursive: nested dicts inside values are not walked. Non-dict input
    (None, list, str, etc.) is returned unchanged so callers can apply this
    safely to optional / mixed-type values.

    Multiple snake keys may map to the same camel target (alias collapse,
    e.g. ``assigned_driver`` and ``driver_name`` both → ``driverName``).
    Behavior is dict-comprehension last-key-wins on collision; in practice
    writers set only one of each alias pair.
    """
    if not isinstance(d, dict):
        return d
    return {_SNAKE_TO_CAMEL.get(k, k): v for k, v in d.items()}


# ── Fleet input validation constants ─────────────────────────────────────────
# Dual-read per 2026-06-09-cms-data-source-model-refactor Phase A
_VALID_DATA_SOURCES = frozenset({
    'vehicle-telemetry', 'cloud-telemetry',
    'onboard-fwe', 'cloud-oem1',
})
_MAX_MANIFEST_ID_LEN = 256
_MAX_DEFAULT_VEHICLE_MODEL_ID_LEN = 256
_MAX_MODEL_MANIFEST_NAME_LEN = 128  # spec 2026-08-28-cms-cert-follows-model § D1
_MAX_CAMPAIGN_TEMPLATE_REF_LEN = 128  # spec 2026-09-01-cms-campaign-follows-enrollment § W1

# Valid `producer` values.  Distinct from `dataSource` (delivery) and
# `oem_source` (frozen legacy).  See spec
# 2026-09-14-cs-portal-data-model-backend § "Vehicle identity — producer vs
# delivery".  `producer` is mandatory on every vehicle write; no default, no
# derivation from other fields.
_VALID_PRODUCERS = frozenset({'meridian', 'oem1', 'cms-native'})


def _normalize_year(raw):
    """Coerce a vehicle `year` to an int, or None when there is no usable value.

    Why this exists: `year` reached DynamoDB as whatever the caller sent, and the
    create path defaulted a missing value to `''` — an empty **String**. One
    String among 154 Numbers made `sortBy=year` raise
    `TypeError: '<' not supported between instances of 'str' and 'decimal.Decimal'`
    and return HTTP 500 for the entire vehicles list. Writing a consistent type
    at the boundary is the actual closure; `_sort_key`'s tolerance is the safety
    net, not the fix. See
    issues/2026-09-23-vehicle-year-mixed-type-breaks-sort/.

    Returns None to mean "omit the attribute", NOT to be written as a value.
    Writing None would store a DynamoDB NULL, which is a *third* type and makes
    the original problem worse rather than better. Absent is honest, and
    `_sort_key` already orders absent predictably. 0 is deliberately not used —
    it would render as a real model year.

    Accepts an int-ish String ("2023", " 2023 ") because callers legitimately
    send form values as text; that is the one conversion worth doing silently.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):  # bool is an int subclass; never a model year
        return None
    if isinstance(raw, (int, float, Decimal)):
        # Handled before the str path because `int("2023.0")` raises, so a JSON
        # float — which `json.loads` produces for `{"year": 2023.0}` — would
        # otherwise be silently dropped. NaN/inf raise here and fall through.
        try:
            return int(raw)
        except (ValueError, OverflowError):
            return None
    if isinstance(raw, str) and not raw.strip():
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


_DRIVER_ID_IDENTITY = re.compile(r'^DRV-[A-Za-z0-9][A-Za-z0-9-]*$')
_DRIVER_ID_LEGACY = re.compile(r'^DRIVER[-_](\d+)$', re.IGNORECASE)


def _canonical_driver_id(raw):
    """Normalise a trip's ``driverId`` to the drivers-table key, or None.

    ONE module-level copy on purpose. This logic previously existed as two
    nested functions inside the request handler — ``_canonical_driver_id_single``
    on the trip-detail path and ``_canonical_driver_id`` on the trip-list path —
    carrying the identical defect, untestable because neither was importable.

    The defect: both accepted only ``DRV-\\d{4}`` (``DRV-0054``) and the legacy
    ``DRIVER-54`` form, and returned None for everything else. Staging's drivers
    table holds 12 rows of which **6** are ``DRV-<TOKEN>-<digits>``
    (``DRV-MRDN-0015``, ``DRV-ENT-001``, ``DRV-SA-001``, ``DRV-TECH-001``, ...),
    a scheme introduced after this function was written. For all six, canonical
    resolution returned None, the batched name lookup never ran, and the caller
    fell through to its ``elif raw_did`` branch and emitted the raw ID as the
    driver's *name*. ``TripsTable.tsx`` then refuses to display a ``DRV-``
    prefixed string and renders **"Unassigned"** — so a trip carrying a correct
    ``driverName`` of "Marcus Reyes" in DynamoDB displayed as unassigned.
    See issues/2026-09-23-driver-id-canonicaliser-rejects-non-numeric-ids/.

    Being permissive is safe here and strictly better than being narrow: an
    unknown key passed to BatchGetItem simply returns no item, and the callers
    already degrade to showing the raw ID when resolution misses. A narrow regex
    does not prevent a bad lookup, it prevents a *good* one.

    Returns the drivers-table key, or None when `raw` is not a driver-ID shape
    at all (so the caller can distinguish "no driver" from "unresolvable driver").
    """
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if _DRIVER_ID_IDENTITY.match(raw):
        # Already a table key — DRV-0054 and DRV-MRDN-0015 alike.
        return raw
    m = _DRIVER_ID_LEGACY.match(raw)
    if m:
        try:
            return f'DRV-{int(m.group(1)):04d}'
        except (TypeError, ValueError):
            return None
    return None


def _looks_like_driver_id(value) -> bool:
    """True when `value` is an ID rather than a person's name.

    Used to decide whether a trip's STORED ``driverName`` is usable. Rows exist
    whose ``driverName`` was written as the ID, and passing one through as a name
    is what the frontend guard exists to catch.
    """
    if not value or not isinstance(value, str):
        return False
    v = value.strip()
    return bool(_DRIVER_ID_IDENTITY.match(v) or _DRIVER_ID_LEGACY.match(v))


_ENERGY_SIGNAL_PREFERENCE = (
    # (telemetry field, basis, is_state_of_charge)
    ('ev_soc', 'ev_state_of_charge', True),
    ('powertrainEVStateOfCharge', 'ev_state_of_charge', True),
    ('soc', 'ev_state_of_charge', True),
    ('fuelLevel', 'fuel_level', False),
)


def _numeric_series(rows, field):
    """Non-null numeric values for `field`, in the order `rows` are given.

    Telemetry messages are HETEROGENEOUS — a FleetWise-style payload carries a
    subset of signals, so a given field is absent from most rows. Measured on
    staging: one vehicle's latest message had none of the energy fields, another
    had only `fuelLevel`. Taking the first and last ROW and reading the field off
    them therefore yields None most of the time; the series has to be built from
    the rows that actually carry it.
    """
    out = []
    for r in rows:
        v = r.get(field)
        if v is None or v == '':
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def compute_trip_energy(rows, battery_capacity_kwh=None):
    """Energy consumed over a trip, or None when no signal reports it.

    `rows` must be telemetry dicts in ascending timestamp order.

    Returning None rather than zero is the whole point. On the Meridian fleet the
    EV signals are present in the schema but flat: `ev_soc`, `soc` and
    `ev_energy_consumed` all read 0 across every row of a trip (`is_ev` is 0 on
    those vehicles), while `fuelLevel` moves 89.5 -> 75.5. Reporting "0 kWh" for
    that trip would state that it consumed no energy, when the truth is that
    nothing reported it — the same class of error as rendering a missing driver
    as "Unassigned".

    A signal is usable only when it has at least two samples AND they are not all
    zero. "All zero" means unreported; values that vary and happen to net to zero
    are kept, because that is a real measurement.

    `usedKwh` is derived ONLY from a state-of-charge basis against a known pack
    capacity. A fuel percentage is not convertible to kWh without tank capacity
    and an energy density, neither of which is on the vehicle record — so the
    fuel basis reports percent only rather than inventing a number.
    """
    for field, basis, is_soc in _ENERGY_SIGNAL_PREFERENCE:
        series = _numeric_series(rows, field)
        if len(series) < 2:
            continue
        if all(v == 0 for v in series):
            continue  # present in the schema, never reported
        start, end = series[0], series[-1]
        used = start - end
        result = {
            'basis': basis,
            'signal': field,
            'startPercent': round(start, 2),
            'endPercent': round(end, 2),
            'usedPercent': round(used, 2),
            'samples': len(series),
        }
        if is_soc and battery_capacity_kwh:
            try:
                cap = float(battery_capacity_kwh)
                if cap > 0:
                    result['usedKwh'] = round(cap * used / 100.0, 2)
                    result['batteryCapacityKwh'] = cap
            except (TypeError, ValueError):
                pass
        return result
    return None


def _is_cloud_telemetry(data_source: str) -> bool:
    return data_source in {'cloud-telemetry', 'cloud-oem1'}

class UnclassifiableVehicleError(ValueError):
    """A vehicle row carries neither a dataSource nor a legacy oem_source
    hint sufficient to determine classification. Callers on the read path
    should catch this and fall back to a 'Unknown' rendering; callers on
    the write path should never see it (the write-path validation ensures
    every fresh row is classifiable).

    Spec: cms/.kiro/specs/2026-08-29-cms-vehicle-classification/spec.md § D1
    """


def _classify_vehicle(vehicle_item: dict) -> str:
    """Return 'onboard' or 'offboard' for a vehicle row.

    Precedence:
      1. vehicle_item['dataSource'] if present and in the closed enum
         (dual-read via _is_cloud_telemetry).
      2. vehicle_item['oem_source'] == 'oem1'  → offboard (legacy fallback).
      3. Raise UnclassifiableVehicleError otherwise.

    NEVER reads 'make'. NEVER reads a brand string. NEVER falls through
    silently to a default classification — that would replicate the
    fail-open pattern this spec exists to close.

    Spec: cms/.kiro/specs/2026-08-29-cms-vehicle-classification/spec.md § D1
    """
    ds = vehicle_item.get('dataSource')
    if isinstance(ds, str) and ds in _VALID_DATA_SOURCES:
        return 'offboard' if _is_cloud_telemetry(ds) else 'onboard'
    oem = vehicle_item.get('oem_source')
    if oem == 'oem1':
        return 'offboard'
    raise UnclassifiableVehicleError(
        f"Vehicle {vehicle_item.get('vehicleId')!r} carries no dataSource "
        "and no legacy oem_source hint. Run "
        "deployment/scripts/backfill_vehicle_classification.py before this "
        "row can be classified."
    )


def _model_manifest_exists(name: str) -> bool:
    """Closed-set check against the model-manifest catalog DDB table.
    Mirrors the access pattern in services/data_processing/lambda/data_processing_api.py:1163.
    Fails-closed when MODEL_MANIFEST_TABLE_NAME env var is not set.
    """
    table_name = os.environ.get('MODEL_MANIFEST_TABLE_NAME')
    if not table_name:
        return False
    table = dynamodb.Table(table_name)
    resp = table.scan(
        FilterExpression='sk = :sk',
        ExpressionAttributeValues={':sk': f'MODEL#{name}'},
    )
    return bool(resp.get('Items'))


def _load_model_manifest(name: str) -> 'dict | None':
    """Return the full model-manifest item dict, picking the highest ACTIVE version.

    Returns None when: MODEL_MANIFEST_TABLE_NAME unset (fail-closed), no rows match
    the sk, or all matching rows are non-ACTIVE. Handler validates ACTIVE + carries
    decoderManifestRef separately — this helper is a raw fetch.

    Spec: cms/.kiro/specs/2026-08-28-cms-cert-follows-model/spec.md § D1
    """
    table_name = os.environ.get('MODEL_MANIFEST_TABLE_NAME')
    if not table_name:
        return None
    table = dynamodb.Table(table_name)
    resp = table.scan(
        FilterExpression='sk = :sk',
        ExpressionAttributeValues={':sk': f'MODEL#{name}'},
    )
    items = resp.get('Items', [])
    active = [i for i in items if i.get('status') == 'ACTIVE']
    if not active:
        return None
    # Pick highest modelManifestVersion; stored as string — int-cast with lex fallback.
    def _version_key(item):
        v = item.get('modelManifestVersion', '0')
        try:
            return (1, int(v))
        except (ValueError, TypeError):
            return (0, str(v))
    return max(active, key=_version_key)


def _load_fleet(fleet_id):
    """Return the full fleet item dict or None.

    Fails-closed when FLEETS_TABLE_NAME env var is not set.
    Used by the vehicle-creation validator to look up the fleet's
    data_source and transform_manifest_id.

    NOTE: This helper is intentionally duplicated from
    services/connectors/oem1/admin_add_vehicle/handler.py — the two Lambda
    bundles are separate and cannot share code. Duplication by intent,
    per spec 2026-08-29-cms-vehicle-classification § D3.

    Spec: cms/.kiro/specs/2026-08-29-cms-vehicle-classification/spec.md § D3
    """
    if not fleet_id:
        return None
    table_name = os.environ.get('FLEETS_TABLE_NAME')
    if not table_name:
        return None
    table = dynamodb.Table(table_name)
    resp = table.get_item(Key={'fleetId': fleet_id})
    return resp.get('Item')


def _issue_vehicle_certificate(iot_client, dynamodb, vehicle_item, model_manifest):
    """Mints an IoT cert + Thing + attaches the shared CMS-Vehicle-IoT-Policy for
    a vehicle-telemetry vehicle. Idempotent on Thing (create-then-catch-exists);
    NOT idempotent on the cert itself (each call mints a fresh cert). The
    caller decides whether to invoke it.

    Behaviour preserved from index.py:1653–1857 (2026-08-28 commit baseline):
      - create_keys_and_certificate(setAsActive=True)
      - create_thing(thingName=vehicle_item['vin']) — swallow AlreadyExists
      - create_policy(...) or diff-and-publish new version (5-version LRU trim)
      - attach_thing_principal + attach_principal_policy
      - put_item into VEHICLE_CERTIFICATES_TABLE_NAME
      - mutate vehicle_item in-place to include hasCertificate=True + certificateId
      - on failure: mutate vehicle_item to hasCertificate=False + certificateError=str(e);
        do NOT re-raise

    The model_manifest argument is accepted for future use and currently unused.

    Spec: cms/.kiro/specs/2026-08-28-cms-cert-follows-model/spec.md § D2
    """
    try:
        # Check if environment variable exists
        cert_table_name = os.environ.get('VEHICLE_CERTIFICATES_TABLE_NAME')
        if not cert_table_name:
            print(f"🔐 ERROR: VEHICLE_CERTIFICATES_TABLE_NAME environment variable not set!")
            raise Exception("VEHICLE_CERTIFICATES_TABLE_NAME environment variable not set")

        print(f"🔐 Using certificates table: {cert_table_name}")

        # Create certificate
        print(f"🔐 Creating IoT certificate...")
        cert_response = iot_client.create_keys_and_certificate(setAsActive=True)
        print(f"🔐 Certificate created: {cert_response['certificateId']}")

        # Create IoT Thing using VIN as thing name
        thing_name = vehicle_item['vin']
        try:
            iot_client.create_thing(thingName=thing_name)
            print(f"🔗 Created IoT Thing: {thing_name}")
        except iot_client.exceptions.ResourceAlreadyExistsException:
            print(f"🔗 IoT Thing already exists: {thing_name}")
        except Exception as thing_error:
            print(f"🔗 Error creating IoT Thing: {str(thing_error)}")
            raise thing_error

        # Use shared IoT Policy for all vehicles
        shared_policy_name = "CMS-Vehicle-IoT-Policy"
        # NOTE: Topic surface is intentionally broad under the
        # `cms/*` and `fleet/*` prefixes (not the reserved
        # `$aws/*` namespace, which Stays narrow). The FWE
        # binary subscribes to several feature-specific
        # subtopics — decoder_manifests, collection_schemes,
        # last_known_states/config, commands/.../request —
        # and previous narrow-by-feature policies caused
        # whack-a-mole MQTT reason-code-135 ("Not authorized")
        # rejections every time a new FWE feature flag was
        # enabled. Broadening to the prefix-level wildcard
        # mirrors `cms-device-policy` (the policy our other
        # working agents already use) and lets the FWE
        # binary roll forward without per-feature policy
        # edits. Tighten only if a future agent runs against
        # an untrusted IoT account where minimum-privilege
        # matters more than operational stability.
        shared_policy_document = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["iot:Connect"],
                    "Resource": ["arn:aws:iot:*:*:client/*"]
                },
                {
                    "Effect": "Allow",
                    "Action": ["iot:Publish"],
                    "Resource": [
                        "arn:aws:iot:*:*:topic/$aws/rules/cms_dev_iot_msk_rule/*",
                        "arn:aws:iot:*:*:topic/$aws/rules/cms_staging_iot_msk_rule/*",
                        "arn:aws:iot:*:*:topic/$aws/rules/cms_prod_iot_msk_rule/*",
                        # Connected Services product delivery path (spec
                        # 2026-09-12-cs-simulator-oem2-manifest-path). Basic ingest
                        # publishes to $aws/rules/<ruleName>/<vehicleId>, and the rule is
                        # selected BY NAME, so a portal-triggered simulation carrying
                        # --rule-name cms_<stage>_cs_product_meridian_ev_rule needs publish
                        # on that prefix explicitly — topic/cms/* does not cover it,
                        # because the topic begins $aws/rules/. Group 3 created the rule,
                        # the topic and the subscriber IAM; without this the PUBLISHING
                        # principal is denied at the broker and no telemetry reaches
                        # cs-product-meridian-ev.
                        # All three stages listed to match the *_iot_msk_rule pattern
                        # above, so prod needs no further policy change.
                        "arn:aws:iot:*:*:topic/$aws/rules/cms_dev_cs_product_meridian_ev_rule/*",
                        "arn:aws:iot:*:*:topic/$aws/rules/cms_staging_cs_product_meridian_ev_rule/*",
                        "arn:aws:iot:*:*:topic/$aws/rules/cms_prod_cs_product_meridian_ev_rule/*",
                        "arn:aws:iot:*:*:topic/cms/*",
                        "arn:aws:iot:*:*:topic/fleet/*",
                    ]
                },
                {
                    "Effect": "Allow",
                    "Action": ["iot:Subscribe"],
                    "Resource": [
                        "arn:aws:iot:*:*:topicfilter/cms/*",
                        "arn:aws:iot:*:*:topicfilter/fleet/*",
                    ]
                },
                {
                    "Effect": "Allow",
                    "Action": ["iot:Receive"],
                    "Resource": [
                        "arn:aws:iot:*:*:topic/cms/*",
                        "arn:aws:iot:*:*:topic/fleet/*",
                    ]
                }
            ]
        }

        # Create or update the shared IoT policy.
        #
        # AWS IoT policies are versioned with a hard cap of 5
        # versions per policy. The previous "create-only" code
        # path silently no-op'd on `ResourceAlreadyExistsException`,
        # which meant any drift between the in-source
        # `shared_policy_document` and the live policy in IoT
        # Core stayed forever — exactly how the demo ended up
        # with a stale policy missing the `cms/fleetwise/.../
        # decoder_manifests` subscribe rule. New agent
        # provisionings attached that stale policy and
        # FleetWise Edge Agent rejected with reason code
        # 135 (Not authorized) on the decoder-manifest
        # subscribe.
        #
        # Now: create on first call, otherwise compare the
        # default version against the in-source document and
        # publish + set-default a new version when they
        # differ. Trim non-default versions if we'd cross the
        # 5-version limit.
        desired_doc_str = json.dumps(shared_policy_document, sort_keys=True, separators=(",", ":"))
        try:
            iot_client.create_policy(
                policyName=shared_policy_name,
                policyDocument=json.dumps(shared_policy_document)
            )
            print(f"🔐 Created shared IoT Policy: {shared_policy_name}")
        except iot_client.exceptions.ResourceAlreadyExistsException:
            # Compare the live default version's document to
            # what the source says. If they match, nothing to
            # do. If not, publish a new version (and prune
            # the oldest non-default if needed).
            try:
                live = iot_client.get_policy(policyName=shared_policy_name)
                live_doc_str = json.dumps(json.loads(live["policyDocument"]), sort_keys=True, separators=(",", ":"))
            except Exception as e:
                print(f"🔐 Could not read live policy for diff: {e}")
                live_doc_str = ""
            if live_doc_str == desired_doc_str:
                print(f"🔐 Shared IoT Policy already up-to-date: {shared_policy_name}")
            else:
                print(f"🔐 Shared IoT Policy drifted; publishing new version: {shared_policy_name}")
                try:
                    # Make room if we're at the 5-version cap.
                    vers = iot_client.list_policy_versions(policyName=shared_policy_name).get("policyVersions", [])
                    non_default = [v for v in vers if not v.get("isDefaultVersion")]
                    # Sort oldest-first by createDate.
                    non_default.sort(key=lambda v: v.get("createDate"))
                    while len(vers) >= 5 and non_default:
                        oldest = non_default.pop(0)
                        iot_client.delete_policy_version(
                            policyName=shared_policy_name,
                            policyVersionId=oldest["versionId"],
                        )
                        vers = [v for v in vers if v["versionId"] != oldest["versionId"]]
                    iot_client.create_policy_version(
                        policyName=shared_policy_name,
                        policyDocument=json.dumps(shared_policy_document),
                        setAsDefault=True,
                    )
                    print(f"🔐 New default policy version published")
                except Exception as vers_error:
                    # Don't fail provisioning if the version
                    # update can't go through — operator can
                    # heal the policy out of band.
                    print(f"🔐 Failed to publish new policy version: {vers_error}")
        except Exception as policy_error:
            print(f"🔐 Error creating shared IoT Policy: {str(policy_error)}")
            raise policy_error

        # Attach certificate to IoT Thing
        try:
            iot_client.attach_thing_principal(
                thingName=thing_name,
                principal=cert_response['certificateArn']
            )
            print(f"🔗 Attached certificate to IoT Thing: {thing_name}")
        except Exception as attach_error:
            print(f"🔗 Error attaching certificate to thing: {str(attach_error)}")
            raise attach_error

        # Attach policy to certificate
        try:
            iot_client.attach_principal_policy(
                policyName=shared_policy_name,
                principal=cert_response['certificateArn']
            )
            print(f"🔐 Attached shared policy to certificate: {shared_policy_name}")
        except Exception as policy_attach_error:
            print(f"🔐 Error attaching policy to certificate: {str(policy_attach_error)}")
            raise policy_attach_error

        # Save certificate to DynamoDB
        certificate_item = {
            'vin': vehicle_item['vin'],
            'vehicleId': vehicle_item['vehicleId'],
            'certificateId': cert_response['certificateId'],
            'certificateArn': cert_response['certificateArn'],
            'certificatePem': cert_response['certificatePem'],
            'publicKey': cert_response['keyPair']['PublicKey'],
            'privateKey': cert_response['keyPair']['PrivateKey'],
            'thingName': vehicle_item['vin'],
            'policyName': shared_policy_name,
            'status': 'ACTIVE',
            'createdAt': datetime.utcnow().isoformat(),
            'updatedAt': datetime.utcnow().isoformat()
        }

        print(f"🔐 Saving certificate to DynamoDB table: {cert_table_name}")
        certificates_table = dynamodb.Table(cert_table_name)
        certificates_table.put_item(Item=certificate_item)
        print(f"🔐 Certificate saved successfully for VIN: {vehicle_item['vin']}")

        # Add certificate info to vehicle response
        vehicle_item['hasCertificate'] = True
        vehicle_item['certificateId'] = cert_response['certificateId']

    except Exception as cert_error:
        print(f"🔐 ERROR creating certificate: {str(cert_error)}")
        print(f"🔐 Certificate error type: {type(cert_error)}")
        import traceback
        print(f"🔐 Certificate error traceback: {traceback.format_exc()}")
        # Don't fail the vehicle creation if certificate fails, but log the error
        vehicle_item['hasCertificate'] = False
        vehicle_item['certificateError'] = str(cert_error)


# ── Redis Helper ──────────────────────────────────────────────────────────────
# Raw socket RESP client — no dependencies needed in Lambda
class _RedisClient:
    """Minimal Redis client using raw sockets. Supports HGETALL, XRANGE, GEOSEARCH."""

    def __init__(self, host, port=6379, timeout=2):
        self.host = host
        self.port = port
        self.timeout = timeout

    def _send(self, *args):
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect((self.host, self.port))
            cmd = f"*{len(args)}\r\n"
            for a in args:
                a = str(a)
                cmd += f"${len(a)}\r\n{a}\r\n"
            s.sendall(cmd.encode())
            buf = b""
            while True:
                try:
                    chunk = s.recv(16384)
                    if not chunk: break
                    buf += chunk
                    # Heuristic: complete when we have enough \r\n for the response
                    # Check if response is complete
                    if b"\r\n" in buf:
                        first = buf.split(b"\r\n")[0]
                        if first.startswith(b"*"):
                            n = int(first[1:])
                            if n <= 0: break  # Empty array *0\r\n
                            if buf.count(b"\r\n") >= 1 + n * 2: break
                        elif first.startswith(b"$") or first.startswith(b"+") or first.startswith(b"-") or first.startswith(b":"):
                            if buf.count(b"\r\n") >= 2: break  # Simple response
                except socket.timeout:
                    break
            s.close()
            return buf
        except Exception:
            try: s.close()
            except: pass
            return b""

    def _parse_resp(self, buf):
        """Parse RESP response into Python objects."""
        if not buf: return None
        parts = buf.split(b"\r\n")
        if not parts: return None
        first = parts[0]
        if first.startswith(b"+"):
            return first[1:].decode()
        if first.startswith(b"-"):
            return None
        if first.startswith(b":"):
            return int(first[1:])
        if first.startswith(b"$"):
            n = int(first[1:])
            if n < 0: return None
            return parts[1].decode('utf-8', errors='ignore') if len(parts) > 1 else None
        if first.startswith(b"*"):
            n = int(first[1:])
            if n <= 0: return []
            values = []
            i = 1
            while i < len(parts) - 1 and len(values) < n:
                if parts[i].startswith(b"$"):
                    i += 1
                    values.append(parts[i].decode('utf-8', errors='ignore') if i < len(parts) else "")
                    i += 1
                else:
                    i += 1
            return values
        return None

    def hgetall(self, key):
        buf = self._send("HGETALL", key)
        vals = self._parse_resp(buf)
        if not vals or not isinstance(vals, list): return {}
        return {vals[i]: vals[i+1] for i in range(0, len(vals)-1, 2)}

    def xrange(self, key, start="-", end="+", count=100):
        buf = self._send("XRANGE", key, start, end, "COUNT", str(count))
        # Stream entries are nested arrays — simplified parse
        vals = self._parse_resp(buf)
        if not vals or not isinstance(vals, list): return []
        # Flatten: entries come as [id, [field, val, field, val, ...], id, ...]
        entries = []
        i = 0
        while i < len(vals):
            entry_id = vals[i]
            i += 1
            fields = {}
            # Collect field-value pairs until next entry ID (contains '-')
            while i < len(vals):
                if i + 1 < len(vals) and '-' not in vals[i]:
                    fields[vals[i]] = vals[i+1]
                    i += 2
                else:
                    break
            entries.append({"id": entry_id, "signals": fields})
        return entries

    def geosearch(self, key, lon, lat, radius_km):
        buf = self._send("GEOSEARCH", key, "FROMLONLAT", str(lon), str(lat),
                         "BYRADIUS", str(radius_km), "km", "WITHCOORD", "ASC", "COUNT", "500")
        vals = self._parse_resp(buf)
        if not vals or not isinstance(vals, list): return []
        # Results: [member, [lng, lat], member, [lng, lat], ...]
        results = []
        i = 0
        while i < len(vals):
            vid = vals[i]
            i += 1
            lng_s = vals[i] if i < len(vals) else "0"
            i += 1
            lat_s = vals[i] if i < len(vals) else "0"
            i += 1
            try:
                results.append({"vehicleId": vid, "lng": float(lng_s), "lat": float(lat_s)})
            except (ValueError, TypeError):
                pass
        return results


_redis_available = None  # None = untested, True/False = cached result
_redis_check_time = 0

def _is_recently_connected(meta, max_age_sec=300):
    """Check if a vehicle's Redis meta shows connection within max_age_sec."""
    lc = meta.get('lastConnectedAt') or meta.get('lastSeenAt') or '0'
    try:
        ts = int(lc) if lc.isdigit() else int(float(lc))
        if ts > 1000000000000:
            ts = ts / 1000
        return ts > 0 and (time.time() - ts) < max_age_sec
    except Exception:
        return False

def _get_redis():
    """Get Redis client if endpoint is configured and reachable. Caches availability for 60s."""
    global _redis_available, _redis_check_time
    ep = os.environ.get('REDIS_ENDPOINT', '')
    if not ep:
        return None
    # Cache availability check for 60s to avoid repeated timeout on unreachable Redis
    now = time.time()
    if _redis_available is False and (now - _redis_check_time) < 300:
        return None
    if _redis_available is None or (now - _redis_check_time) >= 300:
        import socket
        try:
            print(f"🔍 Redis PING check: {ep}:6379")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.5)
            s.connect((ep, 6379))
            s.sendall(b"*1\r\n$4\r\nPING\r\n")
            resp = s.recv(32)
            s.close()
            _redis_available = resp.startswith(b"+PONG")
            _redis_check_time = now
            print(f"🔍 Redis PING result: {resp} → available={_redis_available}")
            if not _redis_available:
                print(f"⚠️ Redis PING failed: {resp}")
        except Exception as e:
            _redis_available = False
            _redis_check_time = now
            print(f"⚠️ Redis unreachable ({ep}:6379): {type(e).__name__}: {e}")
            return None
    if not _redis_available:
        return None
    return _RedisClient(ep)


# Cached signal catalog (loaded once per Lambda cold start)
_signal_catalog = None

def _get_signal_catalog(redis_client):
    """Load signal catalog reverse map from Redis. Returns {signal_id: {name, vss, unit, type}}."""
    global _signal_catalog
    if _signal_catalog is not None:
        return _signal_catalog
    if redis_client is None:
        return {}
    raw = redis_client.hgetall("signal_catalog:reverse")
    catalog = {}
    for sig_id, meta in raw.items():
        parts = meta.split("|")
        catalog[sig_id] = {
            "name": parts[0] if len(parts) > 0 else sig_id,
            "vssPath": parts[1] if len(parts) > 1 else "",
            "unit": parts[2] if len(parts) > 2 else "",
            "dataType": parts[3] if len(parts) > 3 else "string",
        }
    _signal_catalog = catalog
    return catalog


def _build_live_vehicle_state(vehicle_id, redis_client):
    """Build live vehicle state from Redis. Returns dict to overlay on DDB vehicle record."""
    if redis_client is None:
        print(f"🔍 LKS: no redis client for {vehicle_id}")
        return {}

    signals = redis_client.hgetall(f"vehicle:{vehicle_id}:signals")
    timestamps = redis_client.hgetall(f"vehicle:{vehicle_id}:timestamps")
    meta = redis_client.hgetall(f"vehicle:{vehicle_id}:meta")
    print(f"🔍 LKS: {vehicle_id} signals={len(signals)}, meta={len(meta)}")
    catalog = _get_signal_catalog(redis_client)

    if not meta:
        return {}  # No live data — vehicle is disconnected

    now_ms = int(time.time() * 1000)
    # Connectedness is decided in exactly one place — see connection_status.py.
    # This previously inlined the rule and parsed the stamp with
    # `int(lc) if lc.isdigit() else int(float(lc))`, which raises on the ISO-8601
    # lastSeenAt the simulator actually writes. A bare `except: pass` swallowed
    # it, the timestamp defaulted to 0, and every such vehicle was demoted to
    # disconnected regardless of freshness — a healthy, actuating vehicle read as
    # offline. See issues/2026-08-19-cms-presence-lastseenat-stale-and-no-reaper/.
    conn_status = resolve_connection_status(meta, now_ms=now_ms)
    result = {
        "connectionStatus": conn_status,
        "lastConnectedAt": meta.get("lastConnectedAt"),
        "lastSyncedAt": meta.get("lastSyncedAt"),
        "enrollmentStatus": "ACTIVE",
        "currentTripId": meta.get("tripId"),
        "currentDriverId": meta.get("driverId"),
        "telemetrySource": meta.get("source", "unknown"),
    }

    # Build signals array with metadata from catalog
    live_signals = []
    for sig_id, value in signals.items():
        sig_meta = catalog.get(sig_id, {"name": sig_id, "vssPath": "", "unit": "", "dataType": "string"})
        ts = timestamps.get(sig_id, "0")
        live_signals.append({
            "signalId": int(sig_id) if sig_id.isdigit() else 0,
            "name": sig_meta["name"],
            "vssPath": sig_meta["vssPath"],
            "value": value,
            "unit": sig_meta["unit"],
            "dataType": sig_meta["dataType"],
            "timestamp": int(ts) if ts.isdigit() else 0,
            "ageMs": now_ms - (int(ts) if ts.isdigit() else 0),
        })

    result["liveSignals"] = live_signals
    result["lastUpdated"] = meta.get("lastConnectedAt", meta.get("lastSeenAt", "0"))

    # Extract well-known fields for backward compat with UI
    sig_by_name = {s["name"]: s["value"] for s in live_signals}
    if sig_by_name.get("lat") and sig_by_name.get("lng"):
        try:
            result["currentLocation"] = {
                "latitude": float(sig_by_name["lat"]),
                "longitude": float(sig_by_name["lng"]),
                "lastUpdated": int(meta.get("lastConnectedAt", meta.get("lastSeenAt", 0))),
            }
        except ValueError:
            pass
    for field in ["speed", "fuelLevel", "engineTemp", "batteryVoltage", "engineRPM", "odometer", "heading"]:
        if sig_by_name.get(field):
            try: result[field] = float(sig_by_name[field])
            except ValueError: pass
    # Aliases for signal catalog names that differ from UI field names
    aliases = {
        "fuelLevel": ["fuelLevel", "fuel_level", "currentFuelLevel"],
        "odometer": ["odometer", "powertrainOdometer", "odo"],
        "batteryVoltage": ["batteryVoltage", "battery_voltage"],
    }
    for ui_field, candidates in aliases.items():
        if ui_field not in result or result.get(ui_field) is None:
            for candidate in candidates:
                if sig_by_name.get(candidate):
                    try:
                        result[ui_field] = float(sig_by_name[candidate])
                        break
                    except ValueError: pass

    # Keep lastSeenAt as epoch ms — UI handles formatting
    if sig_by_name.get("ignitionOn"):
        result["ignitionOn"] = sig_by_name["ignitionOn"] == "true"

    return result


def _create_service_for_dtc(action_id, vehicle_id, vin, dtc_id, dtc_code,
                             system, severity, resolver, resolved_at_iso,
                             dtc_human_desc=None, notes=None):
    """Write a service-history row for a DTC. Does NOT clear the DTC.

    Returns dict with ``serviceId`` (str) on success, or raises on failure
    (caller decides whether to swallow).
    """
    stage = os.environ.get('DEPLOYMENT_STAGE', 'prod')

    severity_to_priority = {
        'CRITICAL': 'P0',
        'HIGH':     'P1',
        'MEDIUM':   'P2',
        'LOW':      'P3',
    }
    triage_priority = severity_to_priority.get((severity or '').upper(), 'P2')

    human_description = (
        f"DTC {dtc_code}: {dtc_human_desc}"
        if dtc_human_desc
        else f"Service for DTC {dtc_code} ({system} subsystem)"
    )

    service_id = f"SVC-{dtc_id[:8]}-{int(time.time())}"
    approval_note = (
        f"{human_description}. Approved by {resolver} via Fleet "
        f"Command Center pending-action {action_id}."
        if action_id
        else f"{human_description}. Scheduled by {resolver} via Fleet Command Center."
    )
    service_record = {
        'vehicleId': vehicle_id,
        'serviceDate': resolved_at_iso.split('T')[0],
        'serviceType': 'DIAGNOSTIC_REPAIR',
        'serviceId': service_id,
        'status': 'scheduled',
        'description': human_description,
        'provider': 'Fleet Command Center',
        'providerType': 'Operator Approved',
        'triagePriority': triage_priority,
        'requestNumber': f'DTC-{dtc_code}-{int(time.time())}',
        'reportedSymptom': dtc_human_desc or f'{system} subsystem issue',
        'notes': notes or approval_note,
        'category': 'DTC_TRIGGERED',
        'source': 'fleet-command-center',
        'dealerId': 'auto-scheduled',
        'technician': resolver,
        'serviceDetails': {
            'trigger': 'dtc-approved',
            'dtcCode': dtc_code,
            'dtcId': dtc_id,
            'system': system,
            'severity': severity,
            'description': human_description,
        },
        'triggerActionId': action_id,
        'triggerDtcId': dtc_id,
        'triggerDtcCode': dtc_code,
        'createdAt': resolved_at_iso,
        'updatedAt': resolved_at_iso,
    }
    service_history_table = dynamodb.Table(
        os.environ.get('SERVICE_HISTORY_TABLE_NAME', f'cms-{stage}-storage-service-history')
    )
    service_history_table.put_item(Item=service_record)
    return {'serviceId': service_id}


def _approve_dtc_action_followups(action_id, vehicle_id, vin, dtc_id, dtc_code,
                                  system, resolver, resolved_at_iso,
                                  severity='HIGH'):
    """Close the loop when a DTC-critical pending action is approved.

    Does two follow-up writes:

      1. **Schedule service**: creates a row in ``cms-<stage>-storage-
         service-history`` tagging the vehicle for inspection of the DTC's
         subsystem. Row is linked back to the original action via
         ``triggerActionId`` and to the DTC via ``triggerDtcId``.
      2. **Clear the DTC**: updates the matching row in ``cms-<stage>-
         storage-dtc-history`` to ``status=CLEARED``, sets ``clearedDate``
         and ``relatedServiceId``. Also REMOVEs ``activeCode`` so the row
         drops out of the sparse GSI.

    Both writes are best-effort — a failure on either one is logged but
    doesn't fail the approve. Caller returns whatever dict this produces
    so the UI can show a confirmation toast.

    Returns a dict with keys ``serviceScheduled`` (bool) and ``dtcCleared``
    (bool) + IDs of any created rows.
    """
    stage = os.environ.get('DEPLOYMENT_STAGE', 'prod')
    out = {'serviceScheduled': False, 'dtcCleared': False}

    # ── Look up the DTC's human-readable description ────────────────────
    dtc_human_desc = None
    try:
        dtc_table = dynamodb.Table(f'cms-{stage}-storage-dtc-history')
        # Paginated, and deliberately WITHOUT Limit.
        #
        # This previously passed Limit=50 alongside the dtcId FilterExpression.
        # DynamoDB applies Limit BEFORE the filter, so the query read at most 50
        # rows for the vehicle and filtered those — if the target dtcId was not in
        # that page the description came back None and the UI rendered a DTC with
        # no human-readable text. `dtcId` is an opaque per-row id, not the DTC code
        # (`code`), and it is neither a key nor a GSI key on this table
        # (vehicleId HASH + timestamp RANGE; GSI on activeCode), so it cannot be
        # promoted into the KeyConditionExpression — pagination is the fix.
        #
        # Latent rather than live when fixed on 2026-08-20: the busiest vehicle had
        # 32 DTC rows against the 50-row window, so nothing was missing yet. DTC
        # history only accumulates, so it would have started failing silently.
        #
        # ScanIndexForward=False walks newest-first: the DTC being approved is
        # almost always recent, so the match is normally found on the first page.
        # Found by deployment/scripts/test_source_trap_lint.py (trap B); third
        # relative of the instances fixed on 2026-08-19.
        dtc_query = {
            'KeyConditionExpression': 'vehicleId = :v',
            'FilterExpression': 'dtcId = :d',
            'ExpressionAttributeValues': {':v': vehicle_id, ':d': dtc_id},
            'ScanIndexForward': False,
        }
        dtc_item = None
        while True:
            dtc_resp = dtc_table.query(**dtc_query)
            items = dtc_resp.get('Items') or []
            if items:
                dtc_item = items[0]
                break
            lek = dtc_resp.get('LastEvaluatedKey')
            if not lek:
                break
            dtc_query['ExclusiveStartKey'] = lek

        if dtc_item:
            dtc_human_desc = dtc_item.get('description')
    except Exception as e:
        print(f"_approve_dtc_action_followups: DTC description lookup failed: {e}")

    # ── Write service-history row via shared helper ─────────────────────
    try:
        result = _create_service_for_dtc(
            action_id=action_id,
            vehicle_id=vehicle_id,
            vin=vin,
            dtc_id=dtc_id,
            dtc_code=dtc_code,
            system=system,
            severity=severity,
            resolver=resolver,
            resolved_at_iso=resolved_at_iso,
            dtc_human_desc=dtc_human_desc,
        )
        out['serviceScheduled'] = True
        out['serviceId'] = result['serviceId']
    except Exception as e:
        print(f"_approve_dtc_action_followups: service-history write failed: {e}")

    # ── Clear the DTC ───────────────────────────────────────────────────
    try:
        dtc_table = dynamodb.Table(
            os.environ.get('DTC_HISTORY_TABLE_NAME', f'cms-{stage}-storage-dtc-history')
        )
        items = []
        kwargs = {
            'KeyConditionExpression': 'vehicleId = :v',
            'FilterExpression': 'dtcId = :d',
            'ExpressionAttributeValues': {':v': vehicle_id, ':d': dtc_id},
            'ScanIndexForward': False,
            'Limit': 500,
        }
        resp = dtc_table.query(**kwargs)
        items.extend(resp.get('Items', []))
        for _ in range(5):
            if items or 'LastEvaluatedKey' not in resp:
                break
            kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
            resp = dtc_table.query(**kwargs)
            items.extend(resp.get('Items', []))
        items = resp.get('Items') or []
        if items:
            latest = max(items, key=lambda x: int(x.get('timestamp', 0)))
            dtc_table.update_item(
                Key={
                    'vehicleId': latest['vehicleId'],
                    'timestamp': latest['timestamp'],
                },
                UpdateExpression='SET #s = :s, clearedDate = :c, relatedServiceId = :r REMOVE activeCode',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={
                    ':s': 'CLEARED',
                    ':c': resolved_at_iso,
                    ':r': out.get('serviceId', ''),
                },
            )
            out['dtcCleared'] = True
        else:
            print(f"_approve_dtc_action_followups: dtc_id={dtc_id} not found in "
                  f"dtc-history for vehicle={vehicle_id}; skipping clear")
    except Exception as e:
        print(f"_approve_dtc_action_followups: dtc-history update failed: {e}")

    return out


# ──────────────────────────────────────────────────────────────────────────
# Driver-self principal classification + guard
#
# The iOS MeridianMotorsCompanion app reuses CMS capabilities (self-assigning a vehicle) by
# calling this API with its Cognito id-token. In staging the iOS app and the CMS
# Fleet UI share ONE consolidated Cognito pool, so the pool id cannot distinguish
# a driver from an operator — and the CMS API authorizer fronts a root {proxy+}
# ANY route + /api/v1/{proxy+}, so every /api/v1/* route is reachable by any
# trusted token. Driver tokens also carry no `cognito:groups`, which the handler's
# `is_admin = ... or not user_groups` default DID treat as platform-admin until
# 2026-08-05, when that fail-open was removed along with the parallel
# `not user_fleet_ids` one — see
# issues/2026-08-05-main-api-fail-open-authz-defaults/. Driver tokens are now
# unprivileged by default, so this guard is no longer the only thing standing
# between a driver token and admin. It remains necessary regardless: unprivileged
# is not the same as self-scoped, and only the allowlist below constrains a driver
# to their OWN record.
#
# This guard neutralizes that: it identifies driver-self tokens by CLAIMS
# (custom:driverId present + no operator group), gated by DRIVER_SELF_GUARD_ENABLED,
# and constrains them to an explicit, reviewable self-service allowlist — denying
# everything else. Adding a future driver-self capability is one allowlist entry.
# ──────────────────────────────────────────────────────────────────────────

# Body keys a driver is permitted to set on their own driver record. A driver
# may claim/assign a vehicle to themselves; they may NOT change status, fleetId,
# licenseClass, email, or any other field via this path.
_DRIVER_SELF_ALLOWED_PUT_KEYS = {'assignedVehicleId'}


# Cognito groups that denote a CMS operator (never a driver-self principal).
_OPERATOR_GROUPS = {'platform-admin', 'fleet-operator', 'fleet-viewer'}


def _driver_self_enabled():
    """Whether the driver-self guard is active. Off by default so the change is
    inert until a deployment explicitly opts in (DRIVER_SELF_GUARD_ENABLED)."""
    return os.environ.get('DRIVER_SELF_GUARD_ENABLED', '').strip().lower() in ('1', 'true', 'yes', 'on')


# 2026-09-02: `_sso_gated_admin_enabled()` deleted. It promoted a groupless
# caller to platform-admin when the pre-env-collapse edge gate was active — a
# valid design when the gate provided the identity boundary. Post-env-collapse
# the edge gate is gone, so the premise is false and the branch became a latent
# trap. See issues/2026-09-02-cms-sso-gated-admin-branch-removed/.


def _classify_driver_self(claims):
    """Return (is_driver_self, driver_self_id).

    Claim-based classification (NOT pool-id based): a caller is a "driver-self"
    principal when the guard is enabled, the token carries a `custom:driverId`,
    and the token is NOT a member of any operator group. This works whether
    drivers and operators share ONE consolidated Cognito pool (distinguished by
    group/claim) or live in separate pools.

    Safety properties:
      - `custom:driverId` is immutable in the pool (Mutable:false), so a driver
        cannot spoof another driver's id to defeat the self-scope.
      - operator group membership is Cognito-managed (not user-settable), so a
        driver cannot escape the guard by claiming a group.
      - operators are never classified as driver-self (they hold a group, and/or
        carry no `custom:driverId`), so they keep full admin.
      - a no-group token WITHOUT a `custom:driverId` (e.g. a demo/service account
        relying on the legacy no-groups admin default) is unaffected.
    """
    if not _driver_self_enabled():
        return False, ''
    driver_id = (claims.get('custom:driverId') or '').strip()
    if not driver_id:
        return False, ''
    groups = [g.strip() for g in (claims.get('cognito:groups') or '').split(',') if g.strip()]
    if any(g in _OPERATOR_GROUPS for g in groups):
        return False, ''
    return True, driver_id


def _driver_self_forbidden(msg):
    """403 body (headers attached by the caller)."""
    return {'statusCode': 403, 'body': json.dumps({'error': msg})}


def _driver_self_guard(path, method, raw_body, driver_self_id):
    """Deny-by-default allowlist for driver-self callers.

    Allowed:
      - GET /api/v1/vehicles                         (claim picker; fleet-scoped by caller)
      - PUT /api/v1/drivers/{driver_self_id}         body keys ⊆ _DRIVER_SELF_ALLOWED_PUT_KEYS
    Everything else → 403. Returns a 403 dict (no headers) or None if allowed.
    """
    p = (path or '').rstrip('/')

    if method == 'GET' and p == '/api/v1/vehicles':
        return None

    if method == 'PUT' and driver_self_id and p == f'/api/v1/drivers/{driver_self_id}':
        try:
            body = json.loads(raw_body) if raw_body else {}
        except (TypeError, ValueError):
            return _driver_self_forbidden('Malformed request body')
        if not isinstance(body, dict):
            return _driver_self_forbidden('Invalid request body')
        extra = set(body.keys()) - _DRIVER_SELF_ALLOWED_PUT_KEYS
        if extra:
            return _driver_self_forbidden(
                'Drivers may only set '
                f'{sorted(_DRIVER_SELF_ALLOWED_PUT_KEYS)} on their own record; '
                f'rejected keys: {sorted(extra)}'
            )
        return None

    # PUT to another driver's record, or any other route/method.
    return _driver_self_forbidden(
        'Driver tokens are limited to self-service vehicle assignment'
    )


def _lookup_driver_fleet(driver_id):
    """Return the driver's fleetId (or None). Used to force fleet-scoping of the
    vehicle list for a driver-self caller, whose token carries no custom:fleetIds."""
    if not driver_id:
        return None
    table_name = os.environ.get('DRIVERS_TABLE_NAME', '')
    if not table_name:
        return None
    try:
        item = dynamodb.Table(table_name).get_item(Key={'driverId': driver_id}).get('Item') or {}
        return (item.get('fleetId') or '').strip() or None
    except Exception as e:  # noqa: BLE001
        print(f"_lookup_driver_fleet failed for {driver_id}: {e}")
        return None


# ── Connected Services subscription feed — module-level seams ────────────────
#
# Spec `.kiro/specs/2026-09-10-cms-connected-services-consumer/` T2.3.
#
# These live at module level, not inside handler(), for two reasons: the token
# provider and its Secrets Manager read are cached across invocations in the
# execution environment (a per-request Cognito sign-in would add ~200ms to every
# call), and module-level functions are the seam the route tests replace. The
# route dispatch itself is inside handler() with the other routes.

_CS_FEED_ROUTE = '/api/v1/connected-services/subscription-feed'

#: Cached across invocations, per the TokenProvider's own caching contract.
_cs_token_provider_singleton = None
_cs_subscriber_secret_cache = None


def _cs_redact_vin(vin):
    """Return a log-safe suffix of the VIN (last 6 chars, prefixed with ***).

    Deliberately byte-identical in behaviour to `handler()`'s local
    `_redact_vin` — including the `***` prefix and the `len(vin) < 6` boundary —
    so the single grep for `***` that finds every redaction site still finds
    this one. It is a separate function only because that one is a closure
    defined inside `handler()` (index.py:7712), in a region owned by another
    active spec, and therefore unreachable from module scope.
    """
    if not vin or len(vin) < 6:  # noqa: PLR2004
        return '***'
    return f'***{vin[-6:]}'


def _cs_subscriber_credentials():
    """Read CMS's subscriber credential from Secrets Manager, cached.

    The secret's NAME comes from `CS_SUBSCRIBER_SECRET_NAME`, which
    `ui_stack.py` builds from the same expression it uses for the IAM resource
    ARN — so the grant and this read cannot drift. The handler cannot derive the
    name itself: it embeds the account id.

    Returns `{}` on any failure rather than raising, so the caller maps it to a
    502 `config_missing` alongside the other configuration faults. The
    exception TYPE is logged, never the exception — a botocore error can echo
    the request, and this request names a secret.
    """
    global _cs_subscriber_secret_cache
    if _cs_subscriber_secret_cache is not None:
        return _cs_subscriber_secret_cache
    secret_name = os.environ.get('CS_SUBSCRIBER_SECRET_NAME', '').strip()
    if not secret_name:
        print('connected-services: CS_SUBSCRIBER_SECRET_NAME is not set')
        return {}
    try:
        client = boto3.client('secretsmanager')
        raw = client.get_secret_value(SecretId=secret_name)['SecretString']
        parsed = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        print(
            f'connected-services: subscriber secret read failed: '
            f'{type(e).__name__}'
        )
        return {}
    if not isinstance(parsed, dict):
        print('connected-services: subscriber secret is not a JSON object')
        return {}
    _cs_subscriber_secret_cache = parsed
    return parsed


def _cs_token_provider():
    """Build (once) the real TokenProvider for CMS's subscriber account.

    Returns None if the credential or the app-client id is unavailable, which
    the route maps to a 502 `config_missing`. `InitiateAuth` needs no IAM
    permission — see `connected_services_proxy.COGNITO_AUTH_FLOW` for why the
    admin flow was rejected — so the only prerequisites are the secret and
    `CLIENT_ID`.
    """
    global _cs_token_provider_singleton
    if _cs_token_provider_singleton is not None:
        return _cs_token_provider_singleton
    secret = _cs_subscriber_credentials()
    username = (secret.get('username') or '').strip()
    client_id = os.environ.get('CLIENT_ID', '').strip()
    if not username or not secret.get('password') or not client_id:
        return None
    # The provider takes a CALLABLE, not the password, so the value is not held
    # in the dataclass repr for the life of the container. Closing over the one
    # key rather than the whole secret dict is security-review SG2's fix,
    # preserved here.
    password = secret['password']
    _cs_token_provider_singleton = (
        connected_services_proxy.CognitoUserPasswordTokenProvider(
            user_pool_client_id=client_id,
            username=username,
            password_provider=lambda: password,
        )
    )
    return _cs_token_provider_singleton


def _cs_http_client():
    """The real producer transport. Replaced wholesale in the route tests."""
    return connected_services_proxy.UrllibHttpClient()


_cs_feed_cache_singleton = None


def _cs_feed_cache(config):
    """Return a `FeedCache` for `config`, or None when no cache is configured.

    None is a supported answer, not a degraded one: `CS_FEED_CACHE_TABLE_NAME` is
    unset wherever `cms-{stage}-connected-services-consumer` has not been
    deployed, and the feed is then served live exactly as T2.3 shipped it.

    Cached on the module (like `_cs_token_provider_singleton`) so a warm Lambda
    reuses one boto3 client. Keyed on the table name so a config change between
    invocations cannot silently keep pointing at the old table — that costs one
    string comparison and removes a class of "why is it still reading the old
    table" that is very expensive to diagnose from logs.
    """
    global _cs_feed_cache_singleton
    if not config.cache_enabled:
        return None
    if (
        _cs_feed_cache_singleton is not None
        and getattr(_cs_feed_cache_singleton, '_table_name', None)
        == config.feed_cache_table
    ):
        return _cs_feed_cache_singleton
    _cs_feed_cache_singleton = connected_services_proxy.DynamoFeedCache(
        config.feed_cache_table
    )
    return _cs_feed_cache_singleton


def _cs_resolve_vin_to_vehicle_id(vin):
    """Return the vehicleId for `vin`, or None if it cannot be resolved.

    NOT `handler()`'s `_resolve_vin_for_vehicle_id`, and the difference is the
    whole point. That function returns `(vehicle_id_value, None)` for an
    unresolvable value — it echoes its input back as the resolved id — which is
    correct for its own caller (a cache read that still wants the caller's rows)
    and fail-open-shaped for an authorization check: "resolved" and
    "unresolvable" become the same return value, so the deny decision collapses
    into whether the VIN string happens to collide with an allowed vehicleId.
    Since `vin == vehicleId` is a live vehicle shape in this repo
    (issues/2026-09-12-vin-equals-vehicleid-resolver-treats-as-unresolvable/),
    that collision is not hypothetical.

    So: None means "unresolvable", and the caller MUST deny on None.

    Two steps, ordered so the `vin == vehicleId` shape resolves correctly:
      1. Query `vin-index` — the authoritative VIN lookup. ui_stack.py grants
         exactly this one index ARN.
      2. GetItem on `vehicleId = vin` — covers the shape where a VIN IS the
         primary key. Only accepted if the row actually exists.

    No scan fallback: storage_stack.py:498 records that a scan fallback "made a
    MISSING index look merely like a slow one for months".
    """
    if not vin:
        return None
    table_name = os.environ.get('VEHICLES_TABLE_NAME', '')
    if not table_name:
        print('connected-services: VEHICLES_TABLE_NAME is not set')
        return None
    table = dynamodb.Table(table_name)
    # Step 1 — vin-index. A literal KeyConditionExpression with
    # ExpressionAttributeValues rather than a boto3 `Key()` object: the same
    # form index.py already uses for the service-history cache read, and it
    # keeps the queried value inspectable rather than wrapped in a condition
    # object.
    try:
        items = table.query(
            IndexName='vin-index',
            KeyConditionExpression='vin = :vin',
            ExpressionAttributeValues={':vin': vin},
            ProjectionExpression='vehicleId',
        ).get('Items') or []
        if items:
            resolved = (items[0].get('vehicleId') or '').strip()
            if resolved:
                return resolved
    except Exception as e:  # noqa: BLE001
        print(
            f'connected-services: vin-index query failed for '
            f'{_cs_redact_vin(vin)}: {type(e).__name__}'
        )
    # Step 2 — the VIN may itself be the vehicleId.
    try:
        hit = table.get_item(Key={'vehicleId': vin}).get('Item')
        if hit:
            return vin
    except Exception as e:  # noqa: BLE001
        print(
            f'connected-services: vehicleId lookup failed for '
            f'{_cs_redact_vin(vin)}: {type(e).__name__}'
        )
    print(
        f'connected-services: VIN {_cs_redact_vin(vin)} did not resolve to a '
        f'vehicle; denying'
    )
    return None


def _cs_filter_feed(payload, allowed_vehicle_ids):
    """Filter a producer records payload to the caller's fleet scope.

    `allowed_vehicle_ids is None` means unscoped access (platform-admin or
    fleet-viewer, per `get_allowed_vehicle_ids()`'s contract) — return the
    payload untouched.

    Three surfaces get filtered, not one:

    * `records` — on `vehicleId`, because that is what CMS authorizes against.
      A record with NO `vehicleId` is dropped: `vehicleId` is deliberately not
      in the proxy's `_RECORD_REQUIRED_KEYS` (so one odd record cannot black out
      the feed), which makes it a value this filter cannot rely on, and a record
      CMS cannot attribute to a vehicle is one it cannot authorize.
    * `vins_in_scope` — the subscription's whole scope list, which is a superset
      of any single operator's fleet. Passing it through while filtering
      `records` would leak precisely the identifiers the filter withholds, and
      would look correct in the UI because the card renders records.
    * `unresolved_vins` — VINs in scope that resolve to no vehicle. With no
      vehicleId there is nothing to authorize against, so a scoped caller sees
      none of them. Unscoped callers still do.

    `count` is recomputed. Passing the producer's through would make the card
    say "2 records" above a list of one — and the proxy's own validator enforces
    `count == len(records)` on the way in, so the same invariant must hold out.
    """
    if allowed_vehicle_ids is None:
        return payload
    if not isinstance(payload, dict):
        return payload
    filtered = dict(payload)
    records = payload.get('records')
    if isinstance(records, list):
        kept = [
            r for r in records
            if isinstance(r, dict)
            and (r.get('vehicleId') or '') in allowed_vehicle_ids
        ]
        filtered['records'] = kept
        filtered['count'] = len(kept)
        visible_vins = {r.get('vin') for r in kept}
    else:
        visible_vins = set()
    if isinstance(payload.get('vins_in_scope'), list):
        filtered['vins_in_scope'] = [
            v for v in payload['vins_in_scope'] if v in visible_vins
        ]
    if isinstance(payload.get('unresolved_vins'), list):
        # Always emptied for a scoped caller — see the docstring.
        filtered['unresolved_vins'] = []
    return filtered


def handler(event, context):
    method = event.get('httpMethod', '')
    path = event.get('path', '')
    
    cors_headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
        'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
    }

    # ── Centralized VIN→vehicleId resolver ────────────────────────────────
    if '/api/v1/vehicles/' in path:
        parts = path.split('/')
        if len(parts) >= 5:
            candidate = parts[4]
            if candidate and not candidate.startswith('VEH-') and candidate != 'locations':
                try:
                    from boto3.dynamodb.conditions import Attr
                    _vt = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                    _scan = _vt.scan(
                        FilterExpression=Attr('vin').eq(candidate),
                        ProjectionExpression='vehicleId',
                    )
                    _items = _scan.get('Items', [])
                    if _items:
                        parts[4] = _items[0]['vehicleId']
                        path = '/'.join(parts)
                        event['path'] = path
                except Exception as _e:
                    print(f"VIN resolver: failed for {candidate}: {_e}")
    # ── End VIN resolver ──────────────────────────────────────────────────
    
    # Extract Cognito claims from API Gateway authorizer
    claims = (event.get('requestContext', {}).get('authorizer', {}) or {}).get('claims', {})
    user_groups = claims.get('cognito:groups', '').split(',') if claims.get('cognito:groups') else []
    user_fleet_ids = [fid.strip() for fid in claims.get('custom:fleetIds', '').split(',') if fid.strip()]
    user_email = claims.get('email', '')
    is_admin = 'platform-admin' in user_groups
    # 2026-09-02: SSO-gated-admin branch DELETED. It promoted a groupless caller
    # to platform-admin when the pre-env-collapse edge gate was active, on the
    # premise that reaching the API required a SSO-authenticated Amazon identity.
    # Post-env-collapse the edge gate is gone (see the env-collapse initiative
    # 2026-09-02 for the full teardown) so the premise no longer
    # holds. The env-var-guarded branch was disarmed but latent — anyone flipping
    # `CMS_SSO_GATED_ADMIN=true` on the current public URL would grant
    # platform-admin to any groupless internet caller. Deleted to close the trap.
    # See issues/2026-09-02-cms-sso-gated-admin-branch-removed/.
    is_viewer = 'fleet-viewer' in user_groups and 'fleet-operator' not in user_groups
    # ── fleet-guest: scoped read-only, for SELF-REGISTERED external users ────
    #
    # Deliberately a separate group from `fleet-viewer`, which despite its name is
    # an UNSCOPED global-read role (see `has_unscoped_access` below). Spec
    # 2026-08-07-cms-account-provisioning-model originally assigned `fleet-viewer`
    # to every self-registered external user, calling it "the least-privileged
    # backend-recognised group" — true for writes, false for reads, where it is the
    # most permissive non-admin role in the model. That would have given any
    # internet registrant read access to every fleet.
    # See decisions.md 2026-08-10 and issues/2026-08-10-cms-demo-external-exposure/.
    #
    # `fleet-guest` is NEVER unscoped: it reads only the fleets named in its
    # `custom:fleetIds`, which the provisioning trigger sets to a single
    # purpose-built public demo fleet at signup. A guest with no fleetIds sees
    # nothing, which is the correct fail-closed default.
    #
    # A higher group always wins, so an operator who also holds fleet-guest is
    # treated as an operator, not narrowed to guest.
    is_guest = (
        'fleet-guest' in user_groups
        and not is_admin
        and 'fleet-operator' not in user_groups
        and 'fleet-viewer' not in user_groups
    )
    # Unscoped (cross-fleet) access is GRANTED, never inferred. Previously an
    # empty custom:fleetIds claim granted unscoped access to any caller,
    # including non-admins. See issues/2026-08-05-main-api-fail-open-authz-defaults/.
    #
    # is_guest is deliberately ABSENT from this expression. That absence is the
    # entire security property of the group — if a future edit adds it here,
    # self-registered external users regain global read. Test-enforced by
    # test_fleet_guest_authz.py.
    has_unscoped_access = is_admin or is_viewer

    # Single predicate for "may not mutate anything". Both fleet-viewer and
    # fleet-guest are read-only, for different reasons: viewer is a global read-only
    # auditor, guest is a scoped self-registered external user. Mutating routes gate
    # on THIS, never on `is_viewer` directly — five routes previously inlined
    # `if is_viewer:` and adding fleet-guest would have silently missed every one of
    # them. The next read-only group only has to be added here.
    is_read_only = is_viewer or is_guest

    # ── Driver-self principal gate ─────────────────────────────────────────
    # Claim-based: a token carrying custom:driverId and NOT in an operator group
    # is a driver acting on their own behalf (when DRIVER_SELF_GUARD_ENABLED).
    # Never let it inherit the no-groups admin default; constrain it to the
    # self-service allowlist and force the vehicle list to the driver's own fleet.
    is_driver_self, driver_self_id = _classify_driver_self(claims)
    if is_driver_self:
        is_admin = False
        is_viewer = False
        is_guest = False
        is_read_only = False
        has_unscoped_access = False
        _guard = _driver_self_guard(path, method, event.get('body'), driver_self_id)
        if _guard is not None:
            _guard['headers'] = cors_headers
            return _guard
        # Resolve the driver's OWN fleet and FAIL CLOSED if it can't be
        # determined. Without a known fleet we cannot safely scope the vehicle
        # list (GET /api/v1/vehicles) or rely on the cross-fleet assignment
        # invariant (PUT) — both handler paths disable their checks when
        # user_fleet_ids is empty, which would fail OPEN (cross-fleet enumeration
        # / self-attachment). Deny instead. (Security review C1 + C2.)
        if not user_fleet_ids:
            _self_fleet = _lookup_driver_fleet(driver_self_id) if driver_self_id else None
            if not _self_fleet:
                return {
                    'statusCode': 403,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'error': 'No fleet membership resolved for this driver; '
                                 'self-service vehicle assignment is unavailable.'
                    }),
                }
            user_fleet_ids = [_self_fleet]
    
    def get_allowed_vehicle_ids():
        """Return set of vehicleIds the user can access, or None if admin (no filter)."""
        if has_unscoped_access:
            return None
        enrollment_table = dynamodb.Table(os.environ.get('FLEET_ENROLLMENT_TABLE_NAME'))
        vehicle_ids = set()
        for fid in user_fleet_ids:
            resp = enrollment_table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key('PK').eq(f'FLEET#{fid}')
            )
            for item in resp.get('Items', []):
                vehicle_ids.add(item['vehicleId'])
        return vehicle_ids

    def _deny_viewer():
        """Return 403 for read-only principals (fleet-viewer or fleet-guest), else None.

        Covers `fleet-guest` as well as `fleet-viewer`: a self-registered external user
        must not reach any mutating route. Name kept as `_deny_viewer` because it is
        called from 8 sites and renaming would touch every one for no behavioural gain —
        the docstring is the contract.
        """
        if is_read_only:
            return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Read-only access'})}
        return None

    def _check_fleet_access(fleet_id):
        """Return 403 if non-admin user doesn't have access to fleet_id, else None."""
        if has_unscoped_access:
            return None
        if fleet_id and fleet_id not in user_fleet_ids:
            return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Access denied to this fleet'})}
        return None

    def _scope_fleet_filter():
        """Return (filter_expr, expr_values) to scope queries by user's fleets. Returns (None, {}) for admins."""
        if has_unscoped_access:
            return None, {}
        if len(user_fleet_ids) == 1:
            return 'fleetId = :_scope_fid', {':_scope_fid': user_fleet_ids[0]}
        # Multiple fleets — use OR conditions
        conditions = []
        values = {}
        for i, fid in enumerate(user_fleet_ids):
            conditions.append(f'fleetId = :_sf{i}')
            values[f':_sf{i}'] = fid
        return '(' + ' OR '.join(conditions) + ')', values
    
    # ── User Management (admin only) ────────────────────────────────
    cognito_client = boto3.client('cognito-idp')
    user_pool_id = os.environ.get('USER_POOL_ID', '')

    if path == '/api/v1/users' and method == 'GET' and is_admin:
        try:
            resp = cognito_client.list_users(UserPoolId=user_pool_id, Limit=60)
            users = []
            for u in resp.get('Users', []):
                attrs = {a['Name']: a['Value'] for a in u.get('Attributes', [])}
                groups_resp = cognito_client.admin_list_groups_for_user(
                    UserPoolId=user_pool_id, Username=u['Username'])
                groups = [g['GroupName'] for g in groups_resp.get('Groups', [])]
                users.append({
                    'username': u['Username'],
                    'email': attrs.get('email', ''),
                    'status': u.get('UserStatus', ''),
                    'enabled': u.get('Enabled', True),
                    'groups': groups,
                    'fleetIds': attrs.get('custom:fleetIds', ''),
                    'vehicleIds': attrs.get('custom:vehicleIds', ''),
                    'createdAt': u.get('UserCreateDate', '').isoformat() if hasattr(u.get('UserCreateDate', ''), 'isoformat') else '',
                })
            return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'users': users, 'total': len(users)})}
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

    if path == '/api/v1/users' and method == 'POST' and is_admin:
        try:
            body = json.loads(event.get('body', '{}'))
            email = body.get('email', '')
            group = body.get('group', 'fleet-viewer')
            fleet_ids = body.get('fleetIds', '')
            temp_password = body.get('tempPassword', 'Welcome1!')

            resp = cognito_client.admin_create_user(
                UserPoolId=user_pool_id,
                Username=email,
                UserAttributes=[
                    {'Name': 'email', 'Value': email},
                    {'Name': 'email_verified', 'Value': 'true'},
                    {'Name': 'custom:fleetIds', 'Value': fleet_ids},
                ],
                TemporaryPassword=temp_password,
            )
            cognito_client.admin_add_user_to_group(
                UserPoolId=user_pool_id, Username=email, GroupName=group)
            return {'statusCode': 201, 'headers': cors_headers, 'body': json.dumps({'user': {'username': email, 'group': group, 'fleetIds': fleet_ids}})}
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

    if path.startswith('/api/v1/users/') and method == 'PUT' and is_admin:
        username = path.split('/')[-1]
        try:
            body = json.loads(event.get('body', '{}'))
            action = body.get('action', 'update')
            
            if action == 'disable':
                cognito_client.admin_disable_user(UserPoolId=user_pool_id, Username=username)
                return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'message': f'User {username} disabled'})}
            
            elif action == 'enable':
                cognito_client.admin_enable_user(UserPoolId=user_pool_id, Username=username)
                return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'message': f'User {username} enabled'})}
            
            elif action == 'resetPassword':
                cognito_client.admin_reset_user_password(UserPoolId=user_pool_id, Username=username)
                return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'message': f'Password reset email sent to {username}'})}
            
            elif action == 'setTempPassword':
                temp_pw = body.get('tempPassword', '')
                if not temp_pw:
                    return {'statusCode': 400, 'headers': cors_headers, 'body': json.dumps({'error': 'tempPassword required'})}
                cognito_client.admin_set_user_password(
                    UserPoolId=user_pool_id, Username=username, Password=temp_pw, Permanent=False)
                return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'message': f'Temporary password set for {username}'})}
            
            elif action == 'resendInvite':
                cognito_client.admin_create_user(
                    UserPoolId=user_pool_id, Username=username,
                    MessageAction='RESEND', DesiredDeliveryMediums=['EMAIL'])
                return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'message': f'Invite resent to {username}'})}
            
            elif action == 'assignVehicles':
                vehicle_ids = body.get('vehicleIds', [])
                cognito_client.admin_update_user_attributes(
                    UserPoolId=user_pool_id, Username=username,
                    UserAttributes=[{'Name': 'custom:vehicleIds', 'Value': ','.join(vehicle_ids)}])
                return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'message': f'Vehicles assigned to {username}'})}
            
            else:
                # Default update: group and/or fleetIds
                if 'group' in body:
                    existing = cognito_client.admin_list_groups_for_user(
                        UserPoolId=user_pool_id, Username=username)
                    for g in existing.get('Groups', []):
                        cognito_client.admin_remove_user_from_group(
                            UserPoolId=user_pool_id, Username=username, GroupName=g['GroupName'])
                    cognito_client.admin_add_user_to_group(
                        UserPoolId=user_pool_id, Username=username, GroupName=body['group'])
                if 'fleetIds' in body:
                    cognito_client.admin_update_user_attributes(
                        UserPoolId=user_pool_id, Username=username,
                        UserAttributes=[{'Name': 'custom:fleetIds', 'Value': body['fleetIds']}])
                return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'message': f'User {username} updated'})}
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

    if path.startswith('/api/v1/users/') and method == 'DELETE' and is_admin:
        username = path.split('/')[-1]
        try:
            cognito_client.admin_delete_user(UserPoolId=user_pool_id, Username=username)
            return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'message': f'User {username} deleted'})}
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

    if (path == '/api/v1/users' or path.startswith('/api/v1/users/')) and not is_admin:
        return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Admin access required'})}

    # ── Driver-facing (VSA) Cognito pool management ──────────────
    # The VSA pool (env: VSA_USER_POOL_ID) holds driver accounts used by
    # the iOS app. CMS operators can view status and lock/unlock accounts
    # without leaving the CMS UI. Full CRUD is handled by the seed script
    # (deployment/scripts/seed_driver_users.py).
    vsa_pool_id = os.environ.get('VSA_USER_POOL_ID', '')
    drivers_table_name = os.environ.get('DRIVERS_TABLE_NAME', '')

    def _driver_email_from_id(driver_id):
        """Resolve driverId → email via drivers table (same convention as seed script)."""
        if not drivers_table_name:
            return None
        try:
            drivers_tbl = dynamodb.Table(drivers_table_name)
            resp = drivers_tbl.get_item(Key={'driverId': driver_id})
            item = resp.get('Item')
            if not item:
                return None
            # Prefer the sign-in email actually stored on the driver row
            # (set by the seeders — persona drivers may use an address
            # that doesn't follow the firstName.lastName convention)
            # before falling back to that convention. Without this, persona drivers whose Cognito email
            # doesn't follow the name convention show a false "no account
            # provisioned" even though their iOS sign-in works.
            # (issue 2026-06-22-cms-driver-account-email-mismatch)
            stored = (item.get('cognitoEmail') or item.get('email') or '').strip()
            if stored:
                return stored
            fn = (item.get('firstName') or '').strip().lower()
            ln = (item.get('lastName') or '').strip().lower()
            if not (fn and ln):
                return None
            return f"{fn}.{ln}@example.com"
        except Exception:
            return None

    if path.startswith('/api/v1/driver-users/') and method == 'GET' and is_admin:
        driver_id = path.split('/')[-1]
        email = _driver_email_from_id(driver_id)
        if not email:
            return {'statusCode': 404, 'headers': cors_headers, 'body': json.dumps({'error': f'Driver {driver_id} not found'})}
        try:
            resp = cognito_client.admin_get_user(UserPoolId=vsa_pool_id, Username=email)
            attrs = {a['Name']: a['Value'] for a in resp.get('UserAttributes', [])}
            return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({
                'exists': True,
                'username': resp.get('Username'),
                'email': attrs.get('email', email),
                'status': resp.get('UserStatus'),
                'enabled': resp.get('Enabled', True),
                'createdAt': resp.get('UserCreateDate').isoformat() if resp.get('UserCreateDate') else None,
                'lastModified': resp.get('UserLastModifiedDate').isoformat() if resp.get('UserLastModifiedDate') else None,
                'driverId': attrs.get('custom:driverId', ''),
                'tenantId': attrs.get('custom:tenantId', ''),
                'vehicleId': attrs.get('custom:vehicleId', ''),
                'poolId': vsa_pool_id,
                'region': os.environ.get('AWS_REGION', 'us-east-1'),
            })}
        except cognito_client.exceptions.UserNotFoundException:
            # Account not provisioned yet. Still return 200 so the UI can
            # render an "Account not created" state and offer a CTA to run
            # the seeder.
            return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({
                'exists': False,
                'email': email,
                'poolId': vsa_pool_id,
                'region': os.environ.get('AWS_REGION', 'us-east-1'),
            })}
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

    if path.startswith('/api/v1/driver-users/') and method == 'PUT' and is_admin:
        driver_id = path.split('/')[-1]
        email = _driver_email_from_id(driver_id)
        if not email:
            return {'statusCode': 404, 'headers': cors_headers, 'body': json.dumps({'error': f'Driver {driver_id} not found'})}
        try:
            body = json.loads(event.get('body', '{}'))
            action = body.get('action', '')
            if action == 'disable' or action == 'lock':
                cognito_client.admin_disable_user(UserPoolId=vsa_pool_id, Username=email)
                return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'message': f'Driver {driver_id} account locked'})}
            elif action == 'enable' or action == 'unlock':
                cognito_client.admin_enable_user(UserPoolId=vsa_pool_id, Username=email)
                return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'message': f'Driver {driver_id} account unlocked'})}
            else:
                return {'statusCode': 400, 'headers': cors_headers, 'body': json.dumps({'error': f'Unknown action: {action}. Use lock or unlock.'})}
        except cognito_client.exceptions.UserNotFoundException:
            return {'statusCode': 404, 'headers': cors_headers, 'body': json.dumps({'error': 'Cognito user not found for this driver. Run seed-driver-users to provision.'})}
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

    if path.startswith('/api/v1/driver-users') and not is_admin:
        return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Admin access required'})}

    # ── Vehicle Link Codes (companion app self-service) ──────────
    link_codes_table = dynamodb.Table(os.environ.get('VEHICLE_LINK_CODES_TABLE_NAME', ''))

    # ── Subscription Plans ────────────────────────────────────────
    SUBSCRIPTION_TIERS = {
        'basic': {
            'name': 'Basic',
            'description': 'Core telemetry — location, speed, odometer, ignition',
            'signals': ['speed', 'odometer', 'lat', 'lng', 'heading', 'ignitionOn']
        },
        'standard': {
            'name': 'Standard',
            'description': 'Basic + safety and vehicle health signals',
            'signals': ['speed', 'odometer', 'lat', 'lng', 'heading', 'ignitionOn',
                        'engineRPM', 'engineTemp', 'fuelLevel', 'batteryVoltage',
                        'tire_fl', 'tire_fr', 'tire_rl', 'tire_rr', 'seatbeltStatus']
        },
        'premium': {
            'name': 'Premium',
            'description': 'All available signals from the signal catalog',
            'signals': ['*']
        }
    }
    subscriptions_table = dynamodb.Table(os.environ.get('SUBSCRIPTIONS_TABLE_NAME', ''))

    # List subscription tiers
    if path == '/api/v1/subscription-tiers' and method == 'GET':
        return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'tiers': SUBSCRIPTION_TIERS})}

    # Get subscription for a vehicle
    if path.startswith('/api/v1/subscriptions/') and method == 'GET' and not path.endswith('/subscriptions/'):
        vehicle_id = path.split('/')[-1]
        try:
            resp = subscriptions_table.get_item(Key={'vehicleId': vehicle_id})
            item = resp.get('Item')
            if not item:
                return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'subscription': None})}
            from decimal import Decimal
            def _dec(o):
                from decimal import Decimal  # def-local: see _sh_decimal_default
                if isinstance(o, Decimal): return int(o) if o % 1 == 0 else float(o)
                raise TypeError
            return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'subscription': item}, default=_dec)}
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

    # List all subscriptions (admin sees all, fleet operator sees own fleet)
    if path == '/api/v1/subscriptions' and method == 'GET':
        try:
            resp = subscriptions_table.scan()
            items = resp.get('Items', [])
            if not has_unscoped_access:
                items = [i for i in items if i.get('fleetId') in user_fleet_ids]
            from decimal import Decimal
            def _dec(o):
                from decimal import Decimal  # def-local: see _sh_decimal_default
                if isinstance(o, Decimal): return int(o) if o % 1 == 0 else float(o)
                raise TypeError
            return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'subscriptions': items, 'total': len(items)}, default=_dec)}
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

    # Create/update subscription (enroll vehicle in a tier)
    if path == '/api/v1/subscriptions' and method == 'POST':
        if is_read_only:
            return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Read-only access'})}
        try:
            body = json.loads(event.get('body', '{}'))
            vehicle_id = body.get('vehicleId', '')
            tier = body.get('tier', 'basic')
            fleet_id = body.get('fleetId', '')
            if not vehicle_id or tier not in SUBSCRIPTION_TIERS:
                return {'statusCode': 400, 'headers': cors_headers, 'body': json.dumps({'error': 'vehicleId and valid tier required'})}
            if not has_unscoped_access and fleet_id not in user_fleet_ids:
                return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Access denied'})}
            item = {
                'vehicleId': vehicle_id,
                'fleetId': fleet_id,
                'tier': tier,
                'tierName': SUBSCRIPTION_TIERS[tier]['name'],
                'signals': SUBSCRIPTION_TIERS[tier]['signals'],
                'status': 'active',
                'subscribedAt': datetime.utcnow().isoformat(),
                'subscribedBy': user_email,
            }
            subscriptions_table.put_item(Item=item)
            return {'statusCode': 201, 'headers': cors_headers, 'body': json.dumps({'subscription': item})}
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

    # Cancel subscription
    if path.startswith('/api/v1/subscriptions/') and method == 'DELETE':
        if is_read_only:
            return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Read-only access'})}
        vehicle_id = path.split('/')[-1]
        try:
            subscriptions_table.update_item(
                Key={'vehicleId': vehicle_id},
                UpdateExpression='SET #s = :s, cancelledAt = :c',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':s': 'cancelled', ':c': datetime.utcnow().isoformat()})
            return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'message': f'Subscription cancelled for {vehicle_id}'})}
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

    # Admin: generate a link code for a vehicle
    if path == '/api/v1/vehicle-link-codes' and method == 'POST' and is_admin:
        try:
            body = json.loads(event.get('body', '{}'))
            vehicle_id = body.get('vehicleId', '')
            fleet_id = body.get('fleetId', '')
            expiry_hours = int(body.get('expiryHours', 72))
            if not vehicle_id or not fleet_id:
                return {'statusCode': 400, 'headers': cors_headers, 'body': json.dumps({'error': 'vehicleId and fleetId required'})}
            import secrets
            code = secrets.token_urlsafe(8).upper()[:8]
            link_codes_table.put_item(Item={
                'linkCode': code,
                'vehicleId': vehicle_id,
                'fleetId': fleet_id,
                'used': False,
                'createdAt': datetime.utcnow().isoformat(),
                'ttl': int(time.time()) + (expiry_hours * 3600),
            })
            return {'statusCode': 201, 'headers': cors_headers, 'body': json.dumps({'linkCode': code, 'vehicleId': vehicle_id, 'expiryHours': expiry_hours})}
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

    # Any authenticated user: link themselves to a vehicle via code
    if path == '/api/v1/users/link-vehicle' and method == 'POST':
        try:
            body = json.loads(event.get('body', '{}'))
            code = body.get('vehicleCode', '').strip().upper()
            if not code:
                return {'statusCode': 400, 'headers': cors_headers, 'body': json.dumps({'error': 'vehicleCode required'})}
            
            resp = link_codes_table.get_item(Key={'linkCode': code})
            item = resp.get('Item')
            if not item:
                return {'statusCode': 404, 'headers': cors_headers, 'body': json.dumps({'error': 'Invalid or expired code'})}
            if item.get('used'):
                return {'statusCode': 409, 'headers': cors_headers, 'body': json.dumps({'error': 'Code already used'})}
            
            vehicle_id = item['vehicleId']
            fleet_id = item['fleetId']
            
            # Mark code as used
            link_codes_table.update_item(
                Key={'linkCode': code},
                UpdateExpression='SET used = :t, usedBy = :u, usedAt = :a',
                ExpressionAttributeValues={':t': True, ':u': user_email, ':a': datetime.utcnow().isoformat()})
            
            # Assign the caller to the SCOPED read-only group + set fleetIds and vehicleIds.
            #
            # This MUST NOT be `fleet-viewer`. `fleet-viewer` sets has_unscoped_access=True
            # (see :902), i.e. global cross-fleet read — which would make the fleetIds and
            # vehicleIds written immediately below meaningless, and would let any caller
            # holding one valid link code read EVERY fleet. That is a privilege escalation,
            # and it contradicts this route's own intent: it scopes the user, so the group it
            # grants has to respect scoping.
            #
            # `fleet-guest` is read-only AND scoped (is_read_only via is_guest, and never in
            # has_unscoped_access — asserted by test_fleet_guest_authz.py), which is exactly
            # "I linked my own vehicle, let me view it".
            #
            # Found by security review cycle 1 of spec 2026-08-07-cms-account-provisioning-model.
            # Unreachable on prod at the time (AllowAdminCreateUserOnly=True) but would have
            # gone live with Phase B external self-signup.
            cognito_client = boto3.client('cognito-idp')
            cognito_client.admin_add_user_to_group(
                UserPoolId=user_pool_id, Username=user_email, GroupName='fleet-guest')
            
            # Merge with existing fleetIds/vehicleIds
            existing_fleets = set(fid.strip() for fid in claims.get('custom:fleetIds', '').split(',') if fid.strip())
            existing_vehicles = set(vid.strip() for vid in claims.get('custom:vehicleIds', '').split(',') if vid.strip())
            existing_fleets.add(fleet_id)
            existing_vehicles.add(vehicle_id)
            
            cognito_client.admin_update_user_attributes(
                UserPoolId=user_pool_id, Username=user_email,
                UserAttributes=[
                    {'Name': 'custom:fleetIds', 'Value': ','.join(existing_fleets)},
                    {'Name': 'custom:vehicleIds', 'Value': ','.join(existing_vehicles)},
                ])
            
            return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({
                'message': 'Vehicle linked successfully',
                'vehicleId': vehicle_id,
                'fleetId': fleet_id,
            })}
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

    if path == '/api/v1/signal-catalog' and method == 'GET':
        try:
            table_name = os.environ.get('SIGNAL_CATALOG_TABLE', 'cms-prod-signal-catalog')
            sc_table = dynamodb.Table(table_name)
            params = event.get('queryStringParameters') or {}
            group = params.get('group')
            if group:
                result = sc_table.query(KeyConditionExpression=boto3.dynamodb.conditions.Key('signal_group').eq(group))
            else:
                result = sc_table.scan()
            items = result.get('Items', [])
            # Convert Decimal to float
            import decimal
            def dec2float(obj):
                if isinstance(obj, decimal.Decimal): return float(obj)
                if isinstance(obj, dict): return {k: dec2float(v) for k, v in obj.items()}
                if isinstance(obj, list): return [dec2float(i) for i in obj]
                return obj
            return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'signals': dec2float(items), 'count': len(items)})}
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

    if path == '/api/v1/event-catalog' and method == 'GET':
        try:
            event_table = dynamodb.Table(os.environ.get('EVENT_CATALOG_TABLE', f'cms-{os.environ.get("DEPLOYMENT_STAGE", "prod")}-event-catalog'))
            resp = event_table.scan()
            events = resp.get('Items', [])
            # Convert Decimal to int/float for JSON
            import decimal
            def dec_default(obj):
                if isinstance(obj, decimal.Decimal):
                    return int(obj) if obj == int(obj) else float(obj)
                raise TypeError
            return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'events': events, 'count': len(events)}, default=dec_default)}
        except Exception as e:
            # Fallback to hardcoded catalog
            from event_catalog_helper import EVENT_CATALOG
            events = [{'event_id': k, **v} for k, v in EVENT_CATALOG.items()]
            return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'events': events, 'count': len(events)})}

    if path == '/api/v1/fleets' and method == 'POST':
        # Only platform admins can create fleets
        if not is_admin:
            return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Only platform admins can create fleets'})}
        try:
            body = json.loads(event.get('body', '{}'))
            entry = body.get('entry', {})

            # Validate data_source against closed enum (dual-read window)
            data_source = entry.get('data_source', 'vehicle-telemetry')
            if data_source not in _VALID_DATA_SOURCES:
                return {
                    'statusCode': 400,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f"Invalid data_source: {data_source!r}"}),
                }

            # Bounded-length validation for transform_manifest_id
            transform_manifest_id = entry.get('transform_manifest_id')
            if transform_manifest_id is not None and (
                not isinstance(transform_manifest_id, str)
                or len(transform_manifest_id) > _MAX_MANIFEST_ID_LEN
            ):
                return {
                    'statusCode': 400,
                    'headers': cors_headers,
                    'body': json.dumps({'error': 'Invalid transform_manifest_id'}),
                }

            # Cross-field invariant: cloud-telemetry requires transform_manifest_id
            if _is_cloud_telemetry(data_source) and not transform_manifest_id:
                return {
                    'statusCode': 400,
                    'headers': cors_headers,
                    'body': json.dumps({'error': 'transform_manifest_id is required when data_source is cloud-telemetry'}),
                }

            # default_vehicle_model_id: bounded-length + closed-set catalog lookup
            default_vehicle_model_id = entry.get('default_vehicle_model_id')
            if default_vehicle_model_id is not None:
                if (not isinstance(default_vehicle_model_id, str)
                        or not default_vehicle_model_id
                        or len(default_vehicle_model_id) > _MAX_DEFAULT_VEHICLE_MODEL_ID_LEN):
                    return {
                        'statusCode': 400,
                        'headers': cors_headers,
                        'body': json.dumps({'error': 'Invalid default_vehicle_model_id'}),
                    }
                if not _model_manifest_exists(default_vehicle_model_id):
                    return {
                        'statusCode': 400,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f"Unknown default_vehicle_model_id: {default_vehicle_model_id!r}"}),
                    }

            fleet_item = {
                'fleetId': f"FLEET-{int(time.time())}",
                'name': entry.get('name', ''),
                'description': entry.get('description', ''),
                'status': 'active',
                'vehicleCount': 0,
                'data_source': data_source,
            }
            if transform_manifest_id:
                fleet_item['transform_manifest_id'] = transform_manifest_id
            if default_vehicle_model_id:
                fleet_item['default_vehicle_model_id'] = default_vehicle_model_id

            fleets_table = dynamodb.Table(os.environ.get('FLEETS_TABLE_NAME'))
            fleets_table.put_item(Item=fleet_item)

            # If a default vehicle model is set, register this fleet on the model manifest
            if default_vehicle_model_id:
                try:
                    _model_table = dynamodb.Table(os.environ.get('MODEL_MANIFEST_TABLE_NAME'))
                    _model_table.update_item(
                        Key={'pk': f'MODEL#{default_vehicle_model_id}#1', 'sk': f'MODEL#{default_vehicle_model_id}'},
                        UpdateExpression='SET fleetIds = list_append(if_not_exists(fleetIds, :empty), :fid)',
                        ExpressionAttributeValues={':fid': [fleet_item['fleetId']], ':empty': []},
                    )
                except Exception as model_err:
                    print(f"⚠️ Could not update model manifest fleetIds: {model_err}")
            
            # Invalidate cache
            try:
                cache_table = dynamodb.Table(os.environ.get('DASHBOARD_METRICS_CACHE_TABLE'))
                cache_table.delete_item(Key={'metricKey': 'fleets_list'})
                print("🗑️ Invalidated fleets cache after creation")
            except Exception as cache_error:
                print(f"Cache invalidation error: {cache_error}")
            
            return {
                'statusCode': 201,
                'headers': cors_headers,
                'body': json.dumps({'fleet': fleet_item})
            }
        except Exception as e:
            return {
                'statusCode': 500,
                'headers': cors_headers,
                'body': json.dumps({'error': str(e)})
            }
    
    if path == '/api/v1/vehicles' and method == 'POST':
        if is_read_only:
            return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Read-only access'})}
        print(f"🚗 Vehicle POST endpoint reached!")

        def _400(msg):
            return {'statusCode': 400, 'headers': cors_headers, 'body': json.dumps({'error': msg})}

        try:
            body = json.loads(event.get('body', '{}'))
            # Handle both direct data and entry-wrapped data
            entry = body.get('entry', body)
            print(f"🚗 Vehicle entry data: {entry}")

            # ── Stage 1: validate + resolve ──────────────────────────────────

            # 1) Retirement of the caller-supplied cert flag — reject explicitly
            #    rather than ignore. Applies to any value (True/False/null/string).
            if 'createCertificate' in entry:
                return _400(
                    "The 'createCertificate' field is retired. Certificates are now issued "
                    "automatically based on the vehicle's assigned model. "
                    "See spec 2026-08-28-cms-cert-follows-model."
                )

            # 1a-2) `sold_to` is DMS-owned and read-only in this repo.
            #
            # The ONLY write path is deployment/scripts/seed_vehicle_sold_to.py,
            # which stands in for the DMS sync that does not exist yet.  Any
            # request carrying `sold_to` in the body — at any role, including
            # platform-admin — is rejected.  A role check is insufficient here
            # because `sold_to` is on the entitlement path (spec
            # 2026-09-14-cs-portal-data-model-backend § "Entitlement — who may
            # subscribe to a VIN").  The guard is therefore stronger and simpler
            # than a role check: zero write sites in CMS and CS — enforced
            # source-structurally by test_vehicle_sold_to.py.
            if 'sold_to' in entry:
                return _400(
                    "'sold_to' is a DMS-owned entitlement field and is read-only "
                    "in this service. It cannot be set or updated via any CMS or CS "
                    "route. See spec 2026-09-14-cs-portal-data-model-backend "
                    "§ \"Entitlement — who may subscribe to a VIN\"."
                )

            # 1b) `producer` is mandatory — no default, no inference.
            #
            # `producer` records who built (and is responsible for) a vehicle:
            # `meridian`, `oem1`, or `cms-native`.  It is DISTINCT from
            # `dataSource` (how CMS receives data) and from the legacy
            # `oem_source` field (frozen; do not derive from it).
            #
            # Spec: 2026-09-14-cs-portal-data-model-backend
            # § "Vehicle identity — producer vs delivery".
            producer = entry.get('producer')
            if producer is None:
                return _400(
                    "'producer' is required. Every vehicle must declare its producer "
                    "(one of: 'meridian', 'oem1', 'cms-native'). "
                    "Do not derive from 'oem_source' — see spec "
                    "2026-09-14-cs-portal-data-model-backend § "
                    "\"Vehicle identity — producer vs delivery\"."
                )
            if not isinstance(producer, str) or producer not in _VALID_PRODUCERS:
                return _400(
                    f"Invalid 'producer' value: {producer!r}. "
                    f"Must be one of: {sorted(_VALID_PRODUCERS)!r}."
                )

            # 2) Model manifest is required — no defaulting.
            model_manifest_name = entry.get('modelManifestName')
            if not isinstance(model_manifest_name, str) or not model_manifest_name:
                return _400(
                    "modelManifestName is required. Every vehicle must be created against "
                    "a model manifest (see GET /api/v1/model-manifests for available models)."
                )
            if len(model_manifest_name) > _MAX_MODEL_MANIFEST_NAME_LEN:
                return _400("modelManifestName exceeds maximum length")

            # 3) Look up + validate — fail-closed if MODEL_MANIFEST_TABLE_NAME unset.
            manifest_table_name = os.environ.get('MODEL_MANIFEST_TABLE_NAME')
            if not manifest_table_name:
                return _400(
                    "Model manifest lookup is unavailable: MODEL_MANIFEST_TABLE_NAME is not configured."
                )

            # Raw sk-scan to distinguish "empty" vs "exists but not ACTIVE".
            _manifest_table = dynamodb.Table(manifest_table_name)
            _raw_resp = _manifest_table.scan(
                FilterExpression='sk = :sk',
                ExpressionAttributeValues={':sk': f'MODEL#{model_manifest_name}'},
            )
            _raw_items = _raw_resp.get('Items', [])

            model_manifest = _load_model_manifest(model_manifest_name)
            if model_manifest is None:
                if not _raw_items:
                    return _400(f"Unknown modelManifestName: {model_manifest_name!r}")
                # Rows exist but none are ACTIVE.
                statuses = list({i.get('status') for i in _raw_items})
                return _400(
                    f"modelManifestName {model_manifest_name!r} is not ACTIVE "
                    f"(found status(es): {statuses!r})"
                )

            # 4) Validate decoderManifestRef — deferred: checked per-classification below
            #    after effective_ds is resolved (spec 2026-08-29-cms-vehicle-classification § D2).

            # 5) Classification: vehicle owns truth. Body wins; fleet default fills the gap.
            #    Spec: 2026-08-29-cms-vehicle-classification § D2
            requested_ds = entry.get('dataSource')
            fleet_row = None
            fleet_ds = None
            if requested_ds is not None:
                if not isinstance(requested_ds, str) or requested_ds not in _VALID_DATA_SOURCES:
                    return _400(f"Invalid dataSource: {requested_ds!r}")
                effective_ds = requested_ds
                # Still load fleet for disagreement detection (step 7 below).
                fleet_row = _load_fleet(entry.get('fleetId'))
                fleet_ds = (fleet_row or {}).get('data_source')
            else:
                # Fleet default (already validated on fleet creation per spec 2026-06-10).
                fleet_row = _load_fleet(entry.get('fleetId'))
                fleet_ds = (fleet_row or {}).get('data_source', 'vehicle-telemetry')
                effective_ds = fleet_ds if fleet_ds in _VALID_DATA_SOURCES else 'vehicle-telemetry'

            # 6) Manifest-cluster cross-field invariant, per classification.
            if _is_cloud_telemetry(effective_ds):
                # For cloud-telemetry: fleet must carry transform_manifest_id.
                # fleet_row is already loaded above.
                if not (fleet_row or {}).get('transform_manifest_id'):
                    return _400(
                        "dataSource='cloud-telemetry' requires the fleet to carry "
                        "transform_manifest_id. Fleet "
                        f"{entry.get('fleetId')!r} does not."
                    )
                # Cloud-telemetry vehicles do not require a decoder manifest.
                # decoderManifestRef is optional for this path.
            else:
                # vehicle-telemetry: decoderManifestRef required (preserved from prior spec).
                if not model_manifest.get('decoderManifestRef'):
                    return _400(
                        f"Model {model_manifest_name!r} has no decoderManifestRef; it cannot "
                        "be assigned to a vehicle-telemetry vehicle. Create a cloud-telemetry "
                        "vehicle via the OEM1 flow, or assign a model that carries a decoder."
                    )

            # 7) Fleet-disagreement surfacing (populated into 201 response body if any).
            #    Spec: 2026-08-29-cms-vehicle-classification § D2
            classification_warnings = []
            if (fleet_row is not None and fleet_ds is not None
                    and fleet_ds in _VALID_DATA_SOURCES and fleet_ds != effective_ds):
                classification_warnings.append({
                    'code': 'fleet_disagreement',
                    'message': (
                        f"Vehicle dataSource {effective_ds!r} disagrees with fleet "
                        f"data_source {fleet_ds!r}. Vehicle wins per spec "
                        "2026-08-29-cms-vehicle-classification § D2."
                    ),
                    'vehicleDataSource': effective_ds,
                    'fleetDataSource': fleet_ds,
                })

            # ── Stage 2: build vehicle_item + issue cert ──────────────────────

            vehicle_item = {
                'vehicleId': f"VEH-{int(time.time())}",
                'vin': entry.get('vin', ''),
                'make': entry.get('make', ''),
                'model': entry.get('model', ''),
                'licensePlate': entry.get('licensePlate', ''),
                'color': entry.get('color', ''),
                'vehicleType': entry.get('vehicleType', ''),
                'fuelType': entry.get('fuelType', ''),
                'fleetId': entry.get('fleetId', ''),
                'status': 'active',
                'connectionStatus': 'disconnected',
                'activityStatus': 'inactive',
                'lastConnected': None,
                'lastDisconnected': None,
                # Derived from model manifest (spec § D1 Stage 2)
                'modelManifestName': model_manifest['modelManifestName'],
                'modelManifestVersion': model_manifest['modelManifestVersion'],
                'decoderManifestRef': model_manifest.get('decoderManifestRef', ''),
                # Classification-derived dataSource (spec 2026-08-29 § D2)
                'dataSource': effective_ds,
                # Producer identity — mandatory on write, validated in Stage 1b.
                # Distinct from dataSource (delivery) and oem_source (frozen).
                # Spec: 2026-09-14-cs-portal-data-model-backend
                # § "Vehicle identity — producer vs delivery".
                'producer': producer,
                'createdAt': datetime.utcnow().isoformat(),
                'updatedAt': datetime.utcnow().isoformat(),
            }

            # `year` is set conditionally, NOT with a default. It used to be
            # `entry.get('year', '')`, so a create that omitted it stored an
            # empty STRING while every seeded row stored a Number — the exact
            # mix that made `sortBy=year` 500 the whole list. Omitting the
            # attribute is correct: absent is honest, `_sort_key` orders it
            # predictably, and the frontend already renders "-" for a missing
            # year. Writing None instead would store a DynamoDB NULL, a third
            # type. See issues/2026-09-23-vehicle-year-mixed-type-breaks-sort/.
            _normalized_year = _normalize_year(entry.get('year'))
            if _normalized_year is not None:
                vehicle_item['year'] = _normalized_year
            # ecuConfigId: only write if non-None/non-empty.
            ecu_config_id = model_manifest.get('ecuConfigId')
            if ecu_config_id:
                vehicle_item['ecuConfigId'] = ecu_config_id

            print(f"🚗 Vehicle item to save: {vehicle_item}")

            # ── Stage 1b: campaign-template validation (before vehicle write) ─
            # Spec 2026-09-01-cms-campaign-follows-enrollment § D1 steps 1-3.
            # Only relevant for vehicle-telemetry with a campaignTemplateRef.
            # Cloud-telemetry: silently ignore the field (V24).
            campaign_template_ref = entry.get('campaignTemplateRef')
            _campaign_template_row = None  # populated by step 1 if present

            if campaign_template_ref is not None:
                if not isinstance(campaign_template_ref, str):
                    return _400("campaignTemplateRef must be a string")
                if not campaign_template_ref.strip():
                    return _400("campaignTemplateRef is empty")
                if len(campaign_template_ref) > _MAX_CAMPAIGN_TEMPLATE_REF_LEN:
                    return _400("campaignTemplateRef exceeds maximum length")

            if campaign_template_ref and effective_ds == 'vehicle-telemetry':
                # Step 1: load template row
                _campaigns_table_name = os.environ.get(
                    'CAMPAIGNS_TABLE_NAME',
                    f'cms-{os.environ.get("DEPLOYMENT_STAGE", "prod")}-campaigns',
                )
                _campaigns_tbl = dynamodb.Table(_campaigns_table_name)
                _tmpl_resp = _campaigns_tbl.get_item(Key={'campaignId': campaign_template_ref})
                _campaign_template_row = _tmpl_resp.get('Item')
                if not _campaign_template_row:
                    return _400(f"Campaign template not found: {campaign_template_ref}")

                # Step 2: verify decoderManifestId matches the model manifest's decoderManifestRef
                _tpl_decoder = _campaign_template_row.get('decoderManifestId', '')
                _model_decoder = model_manifest.get('decoderManifestRef', '')
                if _tpl_decoder != _model_decoder:
                    return _400(
                        f"Campaign template decoderManifestId {_tpl_decoder!r} does not match "
                        f"model decoderManifestRef {_model_decoder!r}. Choose a template built "
                        "for the same decoder as this model manifest."
                    )

                # Step 3: verify targetArn == 'template' (not a per-vehicle row)
                if _campaign_template_row.get('targetArn') != 'template':
                    return _400(
                        f"campaignTemplateRef {campaign_template_ref!r} is not a campaign template "
                        f"(targetArn={_campaign_template_row.get('targetArn')!r}). Only rows with "
                        "targetArn='template' may be used as templates."
                    )

            # ── Stage 2a: persist vehicle row ─────────────────────────────────
            vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
            print(f"🚗 Vehicles table name: {os.environ.get('VEHICLES_TABLE_NAME')}")
            vehicles_table.put_item(Item=vehicle_item)
            print(f"🚗 Vehicle saved successfully!")

            # Cert issuance: only for vehicle-telemetry (onboard) vehicles.
            # Spec: 2026-08-29-cms-vehicle-classification § D2
            if effective_ds == 'vehicle-telemetry':
                iot_client = boto3.client('iot')
                print(f"🔐 Issuing certificate for vehicle {vehicle_item['vin']}")
                _issue_vehicle_certificate(iot_client, dynamodb, vehicle_item, model_manifest)
            else:
                # Offboard vehicle — no certificate by design.
                vehicle_item['hasCertificate'] = False

            # ── Stage 2b: inline campaign row write ───────────────────────────
            # Spec 2026-09-01-cms-campaign-follows-enrollment § D1 steps 4-6.
            # Runs AFTER cert issuance; BEFORE the 201 response.
            # V26: on non-conditional put_item failure, vehicle+cert already exist.
            if campaign_template_ref and effective_ds == 'vehicle-telemetry' and _campaign_template_row is not None:
                from botocore.exceptions import ClientError as _ClientError
                _vin = vehicle_item.get('vin', '')
                _campaign_item = {
                    'campaignId': f'{campaign_template_ref}-{_vin}',
                    'targetArn': f'vehicle:{_vin}',
                    'status': 'RUNNING',
                    'syncStatus': 'PENDING',
                    'decoderManifestId': _campaign_template_row.get('decoderManifestId', ''),
                    'collectionScheme': _campaign_template_row.get('collectionScheme'),
                    'signalsToCollect': _campaign_template_row.get('signalsToCollect'),
                    'signalCount': _campaign_template_row.get('signalCount'),
                    'category': _campaign_template_row.get('category'),
                    'description': _campaign_template_row.get('description'),
                    'owner': _campaign_template_row.get('owner', 'oem'),
                    'source': 'create-vehicle-inline',
                    'createdAt': datetime.utcnow().isoformat(),
                    'updatedAt': datetime.utcnow().isoformat(),
                    'specRef': '2026-09-01-cms-campaign-follows-enrollment',
                }
                # Copy campaignName from template if present
                if _campaign_template_row.get('campaignName'):
                    _campaign_item['campaignName'] = _campaign_template_row['campaignName']
                # Copy optional signalsToFetch if present
                if _campaign_template_row.get('signalsToFetch') is not None:
                    _campaign_item['signalsToFetch'] = _campaign_template_row['signalsToFetch']

                # Reuse the same campaigns table client loaded in Stage 1b
                _campaigns_tbl_write = dynamodb.Table(
                    os.environ.get(
                        'CAMPAIGNS_TABLE_NAME',
                        f'cms-{os.environ.get("DEPLOYMENT_STAGE", "prod")}-campaigns',
                    )
                )
                try:
                    _campaigns_tbl_write.put_item(
                        Item=_campaign_item,
                        ConditionExpression='attribute_not_exists(campaignId)',
                    )
                    print(f"🚗 Campaign row written: {_campaign_item['campaignId']}")
                except _ClientError as _ce:
                    if _ce.response['Error']['Code'] == 'ConditionalCheckFailedException':
                        # Row already exists — idempotent no-op (V25).
                        print(f"🚗 Campaign row already exists (idempotent): {_campaign_item['campaignId']}")
                    else:
                        # Non-conditional failure: vehicle+cert provisioned, campaign not.
                        # Caller must retry via the operator override.
                        print(f"🚗 Campaign put_item failed: {_ce}")
                        return {
                            'statusCode': 500,
                            'headers': cors_headers,
                            'body': json.dumps({
                                'error': (
                                    "The vehicle and its certificate were created, but "
                                    "attaching the data collection campaign failed. "
                                    "Attach one from the vehicle's Campaigns tab."
                                )
                            }),
                        }

            response_body = {'vehicle': vehicle_item}
            if classification_warnings:
                response_body['classificationWarnings'] = classification_warnings

            return {
                'statusCode': 201,
                'headers': cors_headers,
                'body': json.dumps(response_body),
            }
        except Exception as e:
            print(f"🚗 Error creating vehicle: {str(e)}")
            return {
                'statusCode': 500,
                'headers': cors_headers,
                'body': json.dumps({'error': str(e)}),
            }
    
    # Validate required environment variables
    required_env_vars = [
        'SAFETY_EVENTS_TABLE_NAME',
        'VEHICLES_TABLE_NAME', 
        'FLEETS_TABLE_NAME',
        'DASHBOARD_METRICS_CACHE_TABLE',
        'DRIVERS_TABLE_NAME',
        'SERVICE_HISTORY_TABLE_NAME'
    ]
    
    for env_var in required_env_vars:
        if not os.environ.get(env_var):
            return {
                'statusCode': 500,
                'headers': {
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
                    'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
                },
                'body': json.dumps({'error': f'Missing required environment variable: {env_var}'})
            }
    
    cors_headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
        'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
    }
    
    try:
        method = event.get('httpMethod', 'GET')
        path = event.get('path', '')
        query_params = event.get('queryStringParameters') or {}
        
        # Handle fleet PUT endpoint (update fleet)
        if path.startswith('/api/v1/fleets/') and method == 'PUT':
            if not is_admin:
                return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Only platform admins can update fleets'})}
            fleet_id = path.split('/')[-1]
            try:
                body = json.loads(event.get('body', '{}'))
                entry = body.get('entry', body)
                
                fleets_table = dynamodb.Table(os.environ.get('FLEETS_TABLE_NAME'))
                
                # Check if fleet exists
                response = fleets_table.get_item(Key={'fleetId': fleet_id})
                if 'Item' not in response:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Fleet {fleet_id} not found'})
                    }
                
                # Update fleet
                update_expression = 'SET updatedAt = :updated_at'
                expression_values = {':updated_at': datetime.utcnow().isoformat()}
                
                if 'name' in entry:
                    update_expression += ', #name = :name'
                    expression_values[':name'] = entry['name']
                if 'description' in entry:
                    update_expression += ', description = :description'
                    expression_values[':description'] = entry['description']
                if 'status' in entry:
                    update_expression += ', #status = :status'
                    expression_values[':status'] = entry['status']
                
                expression_names = {'#name': 'name', '#status': 'status'}
                
                response = fleets_table.update_item(
                    Key={'fleetId': fleet_id},
                    UpdateExpression=update_expression,
                    ExpressionAttributeValues=expression_values,
                    ExpressionAttributeNames=expression_names,
                    ReturnValues='ALL_NEW'
                )
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                # Invalidate cache
                try:
                    cache_table = dynamodb.Table(os.environ.get('DASHBOARD_METRICS_CACHE_TABLE'))
                    cache_table.delete_item(Key={'metricKey': 'fleets_list'})
                    print("🗑️ Invalidated fleets cache after update")
                except Exception as cache_error:
                    print(f"Cache invalidation error: {cache_error}")
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({'fleet': response['Attributes']}, default=decimal_default)
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        # Handle vehicle PUT endpoint (update vehicle)
        if path.startswith('/api/v1/vehicles/') and method == 'PUT' and not path.endswith('/trips') and not path.endswith('/safety-alerts') and not path.endswith('/maintenance-alerts'):
            if is_read_only:
                return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Read-only access'})}
            vehicle_id = path.split('/')[-1]
            try:
                body = json.loads(event.get('body', '{}'))
                entry = body.get('entry', body)

                # `sold_to` is DMS-owned and read-only in this repo.  Reject at
                # every role — same rule as POST.  See comment at the POST guard
                # above and spec 2026-09-14-cs-portal-data-model-backend
                # § "Entitlement — who may subscribe to a VIN".
                if 'sold_to' in entry:
                    return {
                        'statusCode': 400,
                        'headers': cors_headers,
                        'body': json.dumps({
                            'error': (
                                "'sold_to' is a DMS-owned entitlement field and is "
                                "read-only in this service. It cannot be set or updated "
                                "via any CMS or CS route. See spec "
                                "2026-09-14-cs-portal-data-model-backend "
                                "§ \"Entitlement — who may subscribe to a VIN\"."
                            )
                        }),
                    }

                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))

                # Check if vehicle exists
                response = vehicles_table.get_item(Key={'vehicleId': vehicle_id})
                if 'Item' not in response:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Vehicle {vehicle_id} not found'})
                    }
                
                # Update vehicle
                update_expression = 'SET updatedAt = :updated_at'
                expression_values = {':updated_at': datetime.utcnow().isoformat()}
                expression_names = {}
                
                if 'vin' in entry:
                    update_expression += ', vin = :vin'
                    expression_values[':vin'] = entry['vin']
                if 'make' in entry:
                    update_expression += ', make = :make'
                    expression_values[':make'] = entry['make']
                if 'model' in entry:
                    update_expression += ', #model = :model'
                    expression_values[':model'] = entry['model']
                    expression_names['#model'] = 'model'
                if 'year' in entry:
                    # Normalised for the same reason as the create path: an
                    # unpinned type here reintroduces the mixed Number/String
                    # state that 500s `sortBy=year`. A value that does not
                    # resolve to an int is SKIPPED rather than written or
                    # removed — silently deleting an existing good year because
                    # a caller sent junk would be worse than ignoring the junk.
                    _upd_year = _normalize_year(entry['year'])
                    if _upd_year is not None:
                        update_expression += ', #year = :year'
                        expression_values[':year'] = _upd_year
                        expression_names['#year'] = 'year'
                if 'licensePlate' in entry:
                    update_expression += ', licensePlate = :license_plate'
                    expression_values[':license_plate'] = entry['licensePlate']
                if 'color' in entry:
                    update_expression += ', color = :color'
                    expression_values[':color'] = entry['color']
                if 'vehicleType' in entry:
                    update_expression += ', vehicleType = :vehicle_type'
                    expression_values[':vehicle_type'] = entry['vehicleType']
                if 'fuelType' in entry:
                    update_expression += ', fuelType = :fuel_type'
                    expression_values[':fuel_type'] = entry['fuelType']
                if 'fleetId' in entry:
                    update_expression += ', fleetId = :fleet_id'
                    expression_values[':fleet_id'] = entry['fleetId']
                    # Sync fleet enrollment table
                    try:
                        enrollment_table = dynamodb.Table(os.environ.get('FLEET_ENROLLMENT_TABLE_NAME'))
                        old_fleet = response.get('Item', {}).get('fleetId')
                        new_fleet = entry['fleetId']
                        if old_fleet and old_fleet != new_fleet:
                            enrollment_table.delete_item(Key={'PK': f'FLEET#{old_fleet}', 'SK': f'VEHICLE#{vehicle_id}'})
                        if new_fleet:
                            enrollment_table.put_item(Item={
                                'PK': f'FLEET#{new_fleet}',
                                'SK': f'VEHICLE#{vehicle_id}',
                                'fleetId': new_fleet,
                                'vehicleId': vehicle_id,
                                'enrolledAt': datetime.utcnow().isoformat(),
                            })
                    except Exception as enroll_err:
                        print(f"Enrollment sync error: {enroll_err}")
                if 'status' in entry:
                    update_expression += ', #status = :status'
                    expression_values[':status'] = entry['status']
                    expression_names['#status'] = 'status'
                
                update_kwargs = {
                    'Key': {'vehicleId': vehicle_id},
                    'UpdateExpression': update_expression,
                    'ExpressionAttributeValues': expression_values,
                    'ReturnValues': 'ALL_NEW'
                }
                
                if expression_names:
                    update_kwargs['ExpressionAttributeNames'] = expression_names
                
                response = vehicles_table.update_item(**update_kwargs)
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({'vehicle': response['Attributes']}, default=decimal_default)
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        # Handle fleet DELETE endpoint
        if path.startswith('/api/v1/fleets/') and method == 'DELETE':
            if not is_admin:
                return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Only platform admins can delete fleets'})}
            fleet_id = path.split('/')[-1]
            try:
                fleets_table = dynamodb.Table(os.environ.get('FLEETS_TABLE_NAME'))
                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                
                # Check if fleet exists
                response = fleets_table.get_item(Key={'fleetId': fleet_id})
                if 'Item' not in response:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Fleet {fleet_id} not found'})
                    }
                
                # Disassociate all vehicles from this fleet (don't delete vehicles)
                enrollment_table = dynamodb.Table(os.environ.get('FLEET_ENROLLMENT_TABLE_NAME'))
                scan_kwargs = {
                    'FilterExpression': 'fleetId = :fleet_id',
                    'ExpressionAttributeValues': {':fleet_id': fleet_id}
                }
                
                while True:
                    vehicles_response = vehicles_table.scan(**scan_kwargs)
                    
                    for vehicle in vehicles_response['Items']:
                        vehicles_table.update_item(
                            Key={'vehicleId': vehicle['vehicleId']},
                            UpdateExpression='REMOVE fleetId',
                            ReturnValues='NONE'
                        )
                        # Clean up enrollment record
                        try:
                            enrollment_table.delete_item(Key={'PK': f'FLEET#{fleet_id}', 'SK': f'VEHICLE#{vehicle["vehicleId"]}'})
                        except Exception:
                            pass
                    
                    if 'LastEvaluatedKey' not in vehicles_response:
                        break
                    scan_kwargs['ExclusiveStartKey'] = vehicles_response['LastEvaluatedKey']
                
                # Delete the fleet
                fleets_table.delete_item(Key={'fleetId': fleet_id})
                
                # Invalidate cache
                try:
                    cache_table = dynamodb.Table(os.environ.get('DASHBOARD_METRICS_CACHE_TABLE'))
                    cache_table.delete_item(Key={'metricKey': 'fleets_list'})
                    print("🗑️ Invalidated fleets cache after deletion")
                except Exception as cache_error:
                    print(f"Cache invalidation error: {cache_error}")
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({'message': f'Fleet {fleet_id} deleted successfully'})
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        # Handle vehicle DELETE endpoint
        if path.startswith('/api/v1/vehicles/') and method == 'DELETE':
            if is_read_only:
                return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Read-only access'})}
            vehicle_id = path.split('/')[-1]
            try:
                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                trips_table = dynamodb.Table(os.environ.get('TRIPS_TABLE_NAME'))
                safety_events_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                maintenance_alerts_table = dynamodb.Table(os.environ.get('MAINTENANCE_ALERTS_TABLE_NAME'))
                
                # Check if vehicle exists
                response = vehicles_table.get_item(Key={'vehicleId': vehicle_id})
                if 'Item' not in response:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Vehicle {vehicle_id} not found'})
                    }
                
                # Delete all trips for this vehicle
                try:
                    query_kwargs = {
                        'IndexName': 'vehicleId-index',
                        'KeyConditionExpression': 'vehicleId = :vehicle_id',
                        'ExpressionAttributeValues': {':vehicle_id': vehicle_id}
                    }
                    
                    while True:
                        trips_response = trips_table.query(**query_kwargs)
                        
                        for trip in trips_response['Items']:
                            trips_table.delete_item(Key={'tripId': trip['tripId']})
                        
                        if 'LastEvaluatedKey' not in trips_response:
                            break
                        query_kwargs['ExclusiveStartKey'] = trips_response['LastEvaluatedKey']
                        
                except Exception:
                    # Fallback to scan if GSI not available
                    scan_kwargs = {
                        'FilterExpression': 'vehicleId = :vehicle_id',
                        'ExpressionAttributeValues': {':vehicle_id': vehicle_id}
                    }
                    
                    while True:
                        trips_response = trips_table.scan(**scan_kwargs)
                        
                        for trip in trips_response['Items']:
                            trips_table.delete_item(Key={'tripId': trip['tripId']})
                        
                        if 'LastEvaluatedKey' not in trips_response:
                            break
                        scan_kwargs['ExclusiveStartKey'] = trips_response['LastEvaluatedKey']
                
                # Delete all safety events for this vehicle
                try:
                    query_kwargs = {
                        'IndexName': 'vehicleId-index',
                        'KeyConditionExpression': 'vehicleId = :vehicle_id',
                        'ExpressionAttributeValues': {':vehicle_id': vehicle_id}
                    }
                    
                    while True:
                        safety_response = safety_events_table.query(**query_kwargs)
                        
                        for event in safety_response['Items']:
                            if 'eventId' in event:
                                safety_events_table.delete_item(Key={'eventId': event['eventId']})
                            elif 'timestamp' in event:
                                safety_events_table.delete_item(Key={'eventId': event.get('eventId', ''), 'timestamp': event['timestamp']})
                        
                        if 'LastEvaluatedKey' not in safety_response:
                            break
                        query_kwargs['ExclusiveStartKey'] = safety_response['LastEvaluatedKey']
                        
                except Exception:
                    # Fallback to scan if GSI not available
                    scan_kwargs = {
                        'FilterExpression': 'vehicleId = :vehicle_id',
                        'ExpressionAttributeValues': {':vehicle_id': vehicle_id}
                    }
                    
                    while True:
                        safety_response = safety_events_table.scan(**scan_kwargs)
                        
                        for event in safety_response['Items']:
                            if 'eventId' in event:
                                safety_events_table.delete_item(Key={'eventId': event['eventId']})
                            elif 'timestamp' in event:
                                safety_events_table.delete_item(Key={'eventId': event.get('eventId', ''), 'timestamp': event['timestamp']})
                        
                        if 'LastEvaluatedKey' not in safety_response:
                            break
                        scan_kwargs['ExclusiveStartKey'] = safety_response['LastEvaluatedKey']
                
                # Delete all maintenance alerts for this vehicle
                try:
                    query_kwargs = {
                        'IndexName': 'vehicleId-index',
                        'KeyConditionExpression': 'vehicleId = :vehicle_id',
                        'ExpressionAttributeValues': {':vehicle_id': vehicle_id}
                    }
                    
                    while True:
                        maintenance_response = maintenance_alerts_table.query(**query_kwargs)
                        
                        for alert in maintenance_response['Items']:
                            if 'alertId' in alert:
                                maintenance_alerts_table.delete_item(Key={'alertId': alert['alertId']})
                            elif 'timestamp' in alert:
                                maintenance_alerts_table.delete_item(Key={'alertId': alert.get('alertId', ''), 'timestamp': alert['timestamp']})
                        
                        if 'LastEvaluatedKey' not in maintenance_response:
                            break
                        query_kwargs['ExclusiveStartKey'] = maintenance_response['LastEvaluatedKey']
                        
                except Exception:
                    # Fallback to scan if GSI not available
                    scan_kwargs = {
                        'FilterExpression': 'vehicleId = :vehicle_id',
                        'ExpressionAttributeValues': {':vehicle_id': vehicle_id}
                    }
                    
                    while True:
                        maintenance_response = maintenance_alerts_table.scan(**scan_kwargs)
                        
                        for alert in maintenance_response['Items']:
                            if 'alertId' in alert:
                                maintenance_alerts_table.delete_item(Key={'alertId': alert['alertId']})
                            elif 'timestamp' in alert:
                                maintenance_alerts_table.delete_item(Key={'alertId': alert.get('alertId', ''), 'timestamp': alert['timestamp']})
                        
                        if 'LastEvaluatedKey' not in maintenance_response:
                            break
                        scan_kwargs['ExclusiveStartKey'] = maintenance_response['LastEvaluatedKey']
                
                # Delete vehicle certificates if they exist
                try:
                    certificates_table = dynamodb.Table(os.environ.get('VEHICLE_CERTIFICATES_TABLE_NAME'))
                    vehicle = response['Item']
                    vin = vehicle.get('vin')
                    if vin:
                        certificates_table.delete_item(Key={'vin': vin})
                except Exception as cert_error:
                    print(f"Error deleting certificate for vehicle {vehicle_id}: {cert_error}")
                
                # Delete the vehicle
                vehicles_table.delete_item(Key={'vehicleId': vehicle_id})
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({'message': f'Vehicle {vehicle_id} and all related data deleted successfully'})
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        # Handle drivers CRUD operations
        if path == '/api/v1/drivers' and method == 'POST':
            denied = _deny_viewer()
            if denied: return denied
            try:
                body = json.loads(event.get('body', '{}'))
                entry = body.get('entry', body)
                fleet_id = entry.get('fleetId', '')
                denied = _check_fleet_access(fleet_id)
                if denied: return denied

                # Server-side `status` validation (added 2026-05-29 per spec
                # 2026-05-29-staging-drivers-simulator-cognito-parity Decision
                # 6). On create, `status` is required and must be one of
                # {active, on_leave, terminated}. The frontend already sends
                # `status='active'` as the default, so legitimate UI traffic
                # is unaffected; this rejects malformed clients and direct
                # API misuse.
                _VALID_DRIVER_STATUSES = ('active', 'on_leave', 'terminated')
                _status_create = entry.get('status')
                if _status_create is None or _status_create not in _VALID_DRIVER_STATUSES:
                    return {
                        'statusCode': 400,
                        'headers': cors_headers,
                        'body': json.dumps({
                            'error': 'status field is required, valid values: active|on_leave|terminated'
                        })
                    }

                driver_item = {
                    'driverId': f"DRV-{int(time.time())}",
                    'firstName': entry.get('firstName', ''),
                    'lastName': entry.get('lastName', ''),
                    'email': entry.get('email', ''),
                    'phone': entry.get('phone', ''),
                    'licenseNumber': entry.get('licenseNumber', ''),
                    'licenseExpiry': entry.get('licenseExpiry', ''),
                    'status': _status_create,
                    'fleetId': entry.get('fleetId', ''),
                    'createdAt': datetime.utcnow().isoformat(),
                    'updatedAt': datetime.utcnow().isoformat()
                }
                
                drivers_table = dynamodb.Table(os.environ.get('DRIVERS_TABLE_NAME'))
                drivers_table.put_item(Item=driver_item)
                
                return {
                    'statusCode': 201,
                    'headers': cors_headers,
                    'body': json.dumps({'driver': driver_item})
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        if path == '/api/v1/drivers' and method == 'GET':
            try:
                drivers_table = dynamodb.Table(os.environ.get('DRIVERS_TABLE_NAME'))
                
                limit = min(int(query_params.get('limit', 25)), 1000)
                page = int(query_params.get('page', 1))
                fleet_id = query_params.get('fleetId')
                
                # Fleet operators: force scope to their fleets
                if not has_unscoped_access:
                    if not user_fleet_ids:
                        # Scoped caller with no fleet membership sees nothing.
                        return {'statusCode': 200, 'headers': cors_headers,
                                'body': json.dumps({'drivers': [], 'total': 0, 'page': page, 'totalPages': 0})}
                    if fleet_id and fleet_id != 'all' and fleet_id not in user_fleet_ids:
                        return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Access denied'})}
                    if not fleet_id or fleet_id == 'all':
                        fleet_id = user_fleet_ids[0] if len(user_fleet_ids) == 1 else None
                
                filter_expression = None
                expression_values = {}
                
                if fleet_id and fleet_id != 'all':
                    filter_expression = 'fleetId = :fleet_id'
                    expression_values[':fleet_id'] = fleet_id
                elif not has_unscoped_access and user_fleet_ids and len(user_fleet_ids) > 1:
                    scope_expr, scope_vals = _scope_fleet_filter()
                    filter_expression = scope_expr
                    expression_values = scope_vals
                
                scan_kwargs = {}
                if filter_expression:
                    scan_kwargs['FilterExpression'] = filter_expression
                    scan_kwargs['ExpressionAttributeValues'] = expression_values
                
                # Get total count
                count_kwargs = dict(scan_kwargs)
                count_kwargs['Select'] = 'COUNT'
                count_response = drivers_table.scan(**count_kwargs)
                total_count = count_response['Count']
                
                # Get paginated data
                scan_kwargs['Limit'] = limit * 50
                current_page = 1
                while current_page < page:
                    response = drivers_table.scan(**scan_kwargs)
                    if 'LastEvaluatedKey' not in response:
                        break
                    scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                    current_page += 1
                
                drivers = []
                while len(drivers) < limit:
                    response = drivers_table.scan(**scan_kwargs)
                    drivers.extend(response['Items'])
                    if 'LastEvaluatedKey' not in response:
                        break
                    scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                
                drivers = drivers[:limit]
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'drivers': drivers,
                        'total': total_count,
                        'page': page,
                        'limit': limit,
                        'totalPages': (total_count + limit - 1) // limit,
                        'hasNextPage': len(drivers) == limit,
                        'hasPrevPage': page > 1
                    }, default=decimal_default)
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        if path.startswith('/api/v1/drivers/') and method == 'GET':
            # Check if this is a trips endpoint
            if path.endswith('/trips'):
                driver_id = path.split('/')[-2]  # /api/v1/drivers/{driverId}/trips
                limit = min(int(query_params.get('limit', 100)), 1000)
                page = int(query_params.get('page', 1))
                
                try:
                    # Verify driver belongs to user's fleet
                    if not has_unscoped_access:
                        drivers_table = dynamodb.Table(os.environ.get('DRIVERS_TABLE_NAME'))
                        dr = drivers_table.get_item(Key={'driverId': driver_id})
                        if 'Item' in dr:
                            denied = _check_fleet_access(dr['Item'].get('fleetId'))
                            if denied: return denied

                    trips_table = dynamodb.Table(os.environ.get('TRIPS_TABLE_NAME'))
                    
                    # Query trips by driverId using GSI.
                    # Performance note (2026-05-04): previously this handler
                    # made 2 serial DDB GetItems per trip on the page (one
                    # for the vehicle's VIN and one for the driver's name).
                    # With limit=500 that's up to 1000 calls per request,
                    # ~15s wall time. Drivers typically drive a single
                    # vehicle at a time (enforced by our 1:1 invariant), so
                    # we now dedupe vehicle lookups to the distinct VINs
                    # on the page, and we skip the driver lookup entirely
                    # because the route path already scopes to one
                    # driverId — we resolve the driver name once up-front.
                    # ProjectionExpression added so we don't hydrate the
                    # full trip item (waypoints/routes are heavy).
                    _proj = (
                        'tripId, vehicleId, driverId, startTime, endTime, '
                        'completedAt, durationMs, totalDistance, distance, '
                        'startLocation, endLocation, maxSpeed, averageSpeed, '
                        'avgSpeed, currentFuelLevel, fuelConsumption, '
                        'driverScore, driverName, #ts'
                    )
                    all_items = []
                    query_kwargs = {
                        'IndexName': 'driverId-index',
                        'KeyConditionExpression': 'driverId = :driverId',
                        'ExpressionAttributeValues': {':driverId': driver_id},
                        'ProjectionExpression': _proj,
                        # "timestamp" is a reserved word in some contexts;
                        # safer to alias it.
                        'ExpressionAttributeNames': {'#ts': 'timestamp'},
                    }
                    response = trips_table.query(**query_kwargs)
                    all_items.extend(response.get('Items', []))
                    while 'LastEvaluatedKey' in response:
                        query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                        response = trips_table.query(**query_kwargs)
                        all_items.extend(response.get('Items', []))
                    
                    # Sort by startTime descending
                    all_items.sort(key=lambda x: x.get('startTime', x.get('timestamp', 0)), reverse=True)
                    
                    # Paginate
                    total_count = len(all_items)
                    start = (page - 1) * limit
                    page_items = all_items[start:start + limit]

                    # Resolve the driver name ONCE (route is driver-scoped).
                    # Use the pre-loaded record if the fleet-check branch
                    # already fetched it; otherwise best-effort.
                    driver_display_name = driver_id
                    try:
                        _dtbl = dynamodb.Table(os.environ.get('DRIVERS_TABLE_NAME'))
                        _dres = _dtbl.get_item(
                            Key={'driverId': driver_id},
                            ProjectionExpression='firstName, lastName',
                        )
                        _ditem = _dres.get('Item') or {}
                        _fn = (_ditem.get('firstName') or '').strip()
                        _ln = (_ditem.get('lastName') or '').strip()
                        if _fn or _ln:
                            driver_display_name = f'{_fn} {_ln}'.strip()
                    except Exception:
                        pass

                    # Dedupe vehicle lookups: collect distinct vehicleIds on
                    # the page and BatchGetItem them in one call (up to 100
                    # keys per request — well above our page-size limit).
                    vehicles_table_name = os.environ.get('VEHICLES_TABLE_NAME')
                    distinct_vids = list({
                        it.get('vehicleId') for it in page_items
                        if it.get('vehicleId')
                    })
                    vin_by_vid: dict = {}
                    if distinct_vids and vehicles_table_name:
                        # Try BatchGetItem first for scale, then fall back
                        # to per-VIN GetItem if the IAM role doesn't allow
                        # batch_get (our current prod role only has
                        # GetItem on this table). Either way, 1-3 calls
                        # per page is the common case (1:1 driver-vehicle
                        # invariant means most drivers have 1 distinct VID
                        # across their trips).
                        _used_batch = False
                        try:
                            for i in range(0, len(distinct_vids), 100):
                                chunk = distinct_vids[i:i + 100]
                                br = dynamodb.batch_get_item(
                                    RequestItems={
                                        vehicles_table_name: {
                                            'Keys': [{'vehicleId': v} for v in chunk],
                                            'ProjectionExpression': 'vehicleId, vin',
                                        }
                                    }
                                )
                                for vi in (br.get('Responses', {}) or {}).get(vehicles_table_name, []):
                                    vin_by_vid[vi.get('vehicleId')] = vi.get('vin')
                            _used_batch = True
                        except Exception as e:
                            # Common case in deployments without
                            # dynamodb:BatchGetItem in the role. Fall back
                            # to per-VID GetItem; still O(distinct_vids)
                            # which is tiny under the 1:1 invariant.
                            print(f'driver-trips: batch VIN lookup unavailable ({e}); falling back to per-VID GetItem')
                        if not _used_batch:
                            _vtbl = dynamodb.Table(vehicles_table_name)
                            for vid in distinct_vids:
                                try:
                                    vr = _vtbl.get_item(
                                        Key={'vehicleId': vid},
                                        ProjectionExpression='vehicleId, vin',
                                    )
                                    if 'Item' in vr:
                                        vin_by_vid[vid] = vr['Item'].get('vin')
                                except Exception as e:
                                    print(f'driver-trips: per-VID GetItem failed for {vid}: {e}')

                    trips = []
                    for item in page_items:
                        vid = item.get('vehicleId')
                        vin = vin_by_vid.get(vid) if vid else None

                        # Prefer the actually-stored driverName on the trip
                        # if it's already human-readable; fall back to the
                        # once-resolved driver_display_name. This preserves
                        # trips that were authored with a different driver
                        # (edge case for reassignments) while avoiding the
                        # per-row lookup.
                        dname = item.get('driverName') or ''
                        if (not dname) or dname.startswith('DRV-'):
                            dname = driver_display_name

                        trip = {
                            'tripId': item.get('tripId'),
                            'vehicleId': vid,
                            'vin': vin,
                            'startTime': item.get('startTime'),
                            'endTime': item.get('endTime', item.get('completedAt')),
                            'duration': (item.get('durationMs', 0) / 1000 / 60) if item.get('durationMs') else 0,  # Convert ms to minutes
                            'distance': item.get('totalDistance', item.get('distance', 0)),
                            'startLocation': item.get('startLocation', {}),
                            'endLocation': item.get('endLocation', {}),
                            'maxSpeed': item.get('maxSpeed', 0),
                            'avgSpeed': item.get('averageSpeed', item.get('avgSpeed', 0)),
                            'fuelConsumption': item.get('currentFuelLevel', item.get('fuelConsumption', 0)),
                            'driverScore': item.get('driverScore', 0),
                            'driverName': dname,
                            'assignedDriver': dname,
                        }
                        trips.append(trip)
                    
                    # Define decimal handler
                    def decimal_default(obj):
                        from decimal import Decimal
                        if isinstance(obj, Decimal):
                            return int(obj) if obj % 1 == 0 else float(obj)
                        raise TypeError
                    
                    return {
                        'statusCode': 200,
                        'headers': cors_headers,
                        'body': json.dumps({
                            'trips': trips,
                            'totalCount': total_count,
                            'page': page,
                            'limit': limit
                        }, default=decimal_default)
                    }
                except Exception as e:
                    return {
                        'statusCode': 500,
                        'headers': cors_headers,
                        'body': json.dumps({'error': str(e)})
                    }
            else:
                # Regular driver GET endpoint
                driver_id = path.split('/')[-1]
            try:
                drivers_table = dynamodb.Table(os.environ.get('DRIVERS_TABLE_NAME'))
                response = drivers_table.get_item(Key={'driverId': driver_id})
                
                if 'Item' not in response:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Driver {driver_id} not found'})
                    }
                
                driver = response['Item']
                
                # Fleet scoping: non-admin can only see drivers in their fleets
                denied = _check_fleet_access(driver.get('fleetId'))
                if denied: return denied

                # -----------------------------------------------------------------
                # Derived safetyScore (2026-05-05). See sibling logic in
                # vsa-prod-api-drivers-me handler.py — same formula kept
                # in sync so CMS and iOS show the same number.
                #
                #   weighted = HIGH*3 + MEDIUM*2 + LOW*1
                #   rate     = weighted / max(1, miles) * 1000
                #   score    = max(0, min(100, 100 - rate * 15))
                #
                # Scope: the driver's currently-assigned vehicle's
                # all-time safety events (paginated Query on the
                # vehicleId-timestamp-index GSI — no new GSI required).
                # Using lifetime events against lifetime miles keeps
                # the numerator and denominator on the same scale.
                # When assignedVehicleId is missing, or miles are below
                # _MIN_MILES_FOR_SCORE (500), we leave the seeded
                # value alone and tag safetyScoreSource='seeded' so
                # the UI knows the number isn't derived.
                # -----------------------------------------------------------------
                try:
                    _assigned_vid = driver.get('assignedVehicleId')
                    _miles_raw = driver.get('totalMiles')
                    from decimal import Decimal as _Dec
                    _miles = float(_miles_raw) if isinstance(_miles_raw, _Dec) else (float(_miles_raw) if _miles_raw is not None else 0.0)
                    if _assigned_vid and _miles >= 500:
                        _se_table_name = os.environ.get('SAFETY_EVENTS_TABLE_NAME', 'cms-prod-storage-safety-events')
                        _se_table = dynamodb.Table(_se_table_name)
                        _se_resp = _se_table.query(
                            IndexName='vehicleId-timestamp-index',
                            KeyConditionExpression='vehicleId = :v',
                            ExpressionAttributeValues={':v': _assigned_vid},
                            ProjectionExpression='severity',
                        )
                        _se_items = _se_resp.get('Items', [])
                        while 'LastEvaluatedKey' in _se_resp:
                            _se_resp = _se_table.query(
                                IndexName='vehicleId-timestamp-index',
                                KeyConditionExpression='vehicleId = :v',
                                ExpressionAttributeValues={':v': _assigned_vid},
                                ProjectionExpression='severity',
                                ExclusiveStartKey=_se_resp['LastEvaluatedKey'],
                            )
                            _se_items.extend(_se_resp.get('Items', []))
                        # Normalise severity variants. Some legacy rows
                        # store numeric severities ("1"/"2"/"3") instead
                        # of "LOW"/"MEDIUM"/"HIGH"; fold them in so they
                        # don't get silently dropped from the sum.
                        from collections import Counter as _Counter
                        def _norm_sev(raw):
                            s = (raw or '').upper() if isinstance(raw, str) else ''
                            return {'1':'LOW','2':'MEDIUM','3':'HIGH'}.get(s, s)
                        _sev = _Counter(_norm_sev(it.get('severity')) for it in _se_items)
                        _weighted = _sev.get('HIGH',0)*3 + _sev.get('MEDIUM',0)*2 + _sev.get('LOW',0)*1
                        _rate = _weighted / max(1.0, _miles) * 1000.0
                        _score = max(0, min(100, int(round(100 - _rate * 15))))
                        driver['safetyScore'] = _score
                        driver['safetyScoreSource'] = 'events-derived-2026-05-05'
                        print(f'safetyScore derived for {driver_id}: score={_score} events={len(_se_items)} weighted={_weighted} miles={_miles} rate={_rate:.2f}')
                    else:
                        driver['safetyScoreSource'] = 'seeded'
                        print(f'safetyScore fallback to seeded for {driver_id}: miles={_miles} assigned={_assigned_vid}')
                except Exception as _sse:
                    # Never fail the endpoint on a scoring error — keep the
                    # seeded value. Log so operators can spot index/permissions
                    # issues.
                    driver['safetyScoreSource'] = 'seeded'
                    print(f'safetyScore derivation failed for {driver_id}: {_sse}')

                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({'driver': driver}, default=decimal_default)
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        if path.startswith('/api/v1/drivers/') and method == 'PUT':
            denied = _deny_viewer()
            if denied: return denied
            driver_id = path.split('/')[-1]
            try:
                body = json.loads(event.get('body', '{}'))
                entry = body.get('entry', body)

                # Server-side `status` enum validation on update (added
                # 2026-05-29 per spec 2026-05-29-staging-drivers-simulator-
                # cognito-parity Decision 6). Validate only-if-present —
                # on PUT, omitting `status` leaves the existing value
                # unchanged (preserving previous behavior).
                _VALID_DRIVER_STATUSES = ('active', 'on_leave', 'terminated')
                if 'status' in entry and entry.get('status') not in _VALID_DRIVER_STATUSES:
                    return {
                        'statusCode': 400,
                        'headers': cors_headers,
                        'body': json.dumps({
                            'error': 'status field is required, valid values: active|on_leave|terminated'
                        })
                    }

                drivers_table = dynamodb.Table(os.environ.get('DRIVERS_TABLE_NAME'))
                
                # Check if driver exists
                response = drivers_table.get_item(Key={'driverId': driver_id})
                if 'Item' not in response:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Driver {driver_id} not found'})
                    }
                
                # Fleet scoping
                denied = _check_fleet_access(response['Item'].get('fleetId'))
                if denied: return denied

                # -----------------------------------------------------------------
                # assignedVehicleId validation
                #
                # Accepted in the body to (re)assign the driver to a vehicle.
                # Empty string / None → unassign (REMOVE the attribute + clear the
                # mirrored Cognito custom:vehicleId).
                #
                # Validation when assigning a vehicle:
                #   1. Vehicle must exist in the vehicles table.
                #   2. If BOTH the driver and the vehicle have a fleetId, they
                #      must match. Cross-fleet lends are rejected (start strict;
                #      loosen later if it bites). When the driver has no fleetId
                #      (most seeded rows don't), we skip the check — assigning
                #      a vehicle often IS the first time a driver gets a fleet.
                # -----------------------------------------------------------------
                assigned_vehicle_id_in_body = 'assignedVehicleId' in entry
                new_vehicle_id = (entry.get('assignedVehicleId') or '').strip() if assigned_vehicle_id_in_body else None
                is_unassign = assigned_vehicle_id_in_body and not new_vehicle_id

                vehicle_fleet_id_from_new = None  # captured if we looked it up
                if assigned_vehicle_id_in_body and not is_unassign:
                    vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                    vresp = vehicles_table.get_item(Key={'vehicleId': new_vehicle_id})
                    vitem = vresp.get('Item')
                    if not vitem:
                        return {
                            'statusCode': 400,
                            'headers': cors_headers,
                            'body': json.dumps({'error': f'Vehicle {new_vehicle_id} not found'})
                        }
                    vehicle_fleet_id_from_new = vitem.get('fleetId')
                    # Compare against the POST-update driver fleetId: if the
                    # caller is also changing fleetId in this same PUT
                    # (e.g. moving the driver to a new fleet and giving them
                    # a vehicle in that fleet atomically), honor their
                    # intent rather than the pre-update value.
                    effective_driver_fleet_id = (
                        entry['fleetId'] if 'fleetId' in entry else response['Item'].get('fleetId')
                    )
                    if (
                        effective_driver_fleet_id
                        and vehicle_fleet_id_from_new
                        and effective_driver_fleet_id != vehicle_fleet_id_from_new
                    ):
                        return {
                            'statusCode': 400,
                            'headers': cors_headers,
                            'body': json.dumps({
                                'error': (
                                    f'Vehicle {new_vehicle_id} belongs to fleet '
                                    f'{vehicle_fleet_id_from_new}, but driver '
                                    f'{driver_id} is in fleet {effective_driver_fleet_id}. '
                                    f'Cross-fleet assignments are not allowed.'
                                )
                            })
                        }

                # Update driver
                # -----------------------------------------------------------------
                # Enforce the "1 driver per vehicle at a time" invariant.
                # Added 2026-05-04. Prior to this, the PUT /api/v1/drivers/{id}
                # endpoint would happily assign `new_vehicle_id` to this driver
                # even if another driver's row still carried the same
                # `assignedVehicleId`. That produced the state where
                # /vehicles/{id}/context (which scans drivers by
                # assignedVehicleId) could return a non-deterministic driver
                # and where CMS and iOS would disagree about who "owns" the
                # vehicle. The fix: before writing the new assignment, find
                # every other driver who currently points at the target
                # vehicle and clear their assignedVehicleId + Cognito mirror
                # atomically. Failures are logged per-loser but do not abort
                # the primary assignment — partial cleanup is still better
                # than an aborted reassignment.
                #
                # This only runs when the caller is actually (re)assigning a
                # vehicle (not on plain profile updates and not on unassign).
                # A driver un-assigning themselves doesn't create conflicts.
                displaced_drivers: list[str] = []
                if assigned_vehicle_id_in_body and not is_unassign and new_vehicle_id:
                    # Locally re-import Attr: the module-level import is
                    # shadowed inside this giant handler function because
                    # other branches further down do their own
                    # `from boto3.dynamodb.conditions import Attr`, which
                    # makes Attr a local name throughout the function and
                    # triggers UnboundLocalError here (which runs before
                    # those branches). See PEP 3104 / function scope rules.
                    from boto3.dynamodb.conditions import Attr as _Attr
                    try:
                        prior_holders_resp = drivers_table.scan(
                            FilterExpression=(
                                _Attr('assignedVehicleId').eq(new_vehicle_id)
                                & _Attr('driverId').ne(driver_id)
                            ),
                            ProjectionExpression='driverId, email',
                        )
                    except Exception as e:
                        print(f'driver-assign: prior-holder scan failed for vehicle={new_vehicle_id}: {e}')
                        prior_holders_resp = {'Items': []}

                    now_iso = datetime.utcnow().isoformat()
                    for prior in prior_holders_resp.get('Items', []):
                        prior_id = prior.get('driverId')
                        prior_email = prior.get('email')
                        if not prior_id:
                            continue
                        try:
                            drivers_table.update_item(
                                Key={'driverId': prior_id},
                                UpdateExpression=(
                                    'SET unassignedFromVehicleId = :v, '
                                    'unassignedAt = :t, unassignedReason = :r '
                                    'REMOVE assignedVehicleId'
                                ),
                                ExpressionAttributeValues={
                                    ':v': new_vehicle_id,
                                    ':t': now_iso,
                                    ':r': f'displaced by assignment to {driver_id}',
                                },
                            )
                            displaced_drivers.append(prior_id)
                        except Exception as e:
                            print(f'driver-assign: failed to displace prior holder {prior_id} from {new_vehicle_id}: {e}')
                            continue

                        # Clear the prior holder's Cognito custom:vehicleId
                        # so their iOS app, on next sign-in, doesn't still
                        # claim the vehicle. Best-effort — failures here do
                        # not fail the primary request.
                        if vsa_pool_id and prior_email:
                            try:
                                cognito_client.admin_update_user_attributes(
                                    UserPoolId=vsa_pool_id,
                                    Username=prior_email,
                                    UserAttributes=[{'Name': 'custom:vehicleId', 'Value': ''}],
                                )
                            except Exception as e:  # noqa: BLE001
                                print(f'driver-assign: could not clear Cognito mirror for displaced driver {prior_id}/{prior_email}: {e}')

                update_expression = 'SET updatedAt = :updated_at'
                expression_values = {':updated_at': datetime.utcnow().isoformat()}
                remove_attrs = []  # attributes to REMOVE (for unassign)
                
                if 'firstName' in entry:
                    update_expression += ', firstName = :first_name'
                    expression_values[':first_name'] = entry['firstName']
                if 'lastName' in entry:
                    update_expression += ', lastName = :last_name'
                    expression_values[':last_name'] = entry['lastName']
                if 'email' in entry:
                    update_expression += ', email = :email'
                    expression_values[':email'] = entry['email']
                if 'phone' in entry:
                    update_expression += ', phone = :phone'
                    expression_values[':phone'] = entry['phone']
                if 'licenseNumber' in entry:
                    update_expression += ', licenseNumber = :license_number'
                    expression_values[':license_number'] = entry['licenseNumber']
                if 'licenseExpiry' in entry:
                    update_expression += ', licenseExpiry = :license_expiry'
                    expression_values[':license_expiry'] = entry['licenseExpiry']
                if 'status' in entry:
                    update_expression += ', #status = :status'
                    expression_values[':status'] = entry['status']
                if 'fleetId' in entry:
                    update_expression += ', fleetId = :fleet_id'
                    expression_values[':fleet_id'] = entry['fleetId']
                # assignedVehicleId: SET on assign, REMOVE on unassign. Also
                # backfill fleetId from the vehicle if the driver didn't have
                # one — otherwise subsequent fleet-scoped reads filter this
                # driver out.
                if assigned_vehicle_id_in_body:
                    if is_unassign:
                        remove_attrs.append('assignedVehicleId')
                    else:
                        update_expression += ', assignedVehicleId = :assigned_vehicle_id'
                        expression_values[':assigned_vehicle_id'] = new_vehicle_id
                        if (
                            vehicle_fleet_id_from_new
                            and not response['Item'].get('fleetId')
                            and 'fleetId' not in entry
                        ):
                            update_expression += ', fleetId = :fleet_id_from_vehicle'
                            expression_values[':fleet_id_from_vehicle'] = vehicle_fleet_id_from_new

                if remove_attrs:
                    update_expression += ' REMOVE ' + ', '.join(remove_attrs)

                expression_names = {}
                if 'status' in entry:
                    expression_names['#status'] = 'status'
                
                update_kwargs = {
                    'Key': {'driverId': driver_id},
                    'UpdateExpression': update_expression,
                    'ExpressionAttributeValues': expression_values,
                    'ReturnValues': 'ALL_NEW'
                }
                
                if expression_names:
                    update_kwargs['ExpressionAttributeNames'] = expression_names
                
                response = drivers_table.update_item(**update_kwargs)

                # Mirror assignedVehicleId to the driver's VSA Cognito user so
                # the iOS app sees the new vehicle on the next sign-in. The
                # mirror is best-effort: a failure here shouldn't fail the
                # whole request since the drivers-table write already landed
                # and /drivers/me (which iOS uses authoritatively) will still
                # return the new vehicle.
                cognito_mirror_note = None
                if assigned_vehicle_id_in_body and vsa_pool_id:
                    email = _driver_email_from_id(driver_id)
                    if email:
                        try:
                            if is_unassign:
                                # Empty string clears the attribute for this user.
                                cognito_client.admin_update_user_attributes(
                                    UserPoolId=vsa_pool_id,
                                    Username=email,
                                    UserAttributes=[{'Name': 'custom:vehicleId', 'Value': ''}],
                                )
                            else:
                                cognito_client.admin_update_user_attributes(
                                    UserPoolId=vsa_pool_id,
                                    Username=email,
                                    UserAttributes=[{'Name': 'custom:vehicleId', 'Value': new_vehicle_id}],
                                )
                        except cognito_client.exceptions.UserNotFoundException:
                            cognito_mirror_note = f'No VSA Cognito user for {email}; iOS sign-in will use the drivers-table value directly.'
                        except Exception as e:  # noqa: BLE001
                            cognito_mirror_note = f'Failed to mirror assignment to Cognito: {e}'
                    else:
                        cognito_mirror_note = f'Could not resolve email for driver {driver_id}; Cognito mirror skipped.'

                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError

                resp_body = {'driver': response['Attributes']}
                if cognito_mirror_note:
                    resp_body['cognitoMirrorNote'] = cognito_mirror_note
                # Surface any displaced prior holders so the UI can render a
                # note like "Also un-assigned DRV-0001 from this vehicle."
                # Empty list is omitted from the response for backward
                # compatibility with clients that don't expect the field.
                if displaced_drivers:
                    resp_body['displacedDrivers'] = displaced_drivers

                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps(resp_body, default=decimal_default)
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        if path.startswith('/api/v1/drivers/') and method == 'DELETE':
            denied = _deny_viewer()
            if denied: return denied
            driver_id = path.split('/')[-1]
            try:
                drivers_table = dynamodb.Table(os.environ.get('DRIVERS_TABLE_NAME'))
                
                # Check if driver exists
                response = drivers_table.get_item(Key={'driverId': driver_id})
                if 'Item' not in response:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Driver {driver_id} not found'})
                    }
                
                # Fleet scoping
                denied = _check_fleet_access(response['Item'].get('fleetId'))
                if denied: return denied
                
                # Delete the driver
                drivers_table.delete_item(Key={'driverId': driver_id})
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({'message': f'Driver {driver_id} deleted successfully'})
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        # Handle trips by driver endpoint
        # Fleet-wide trips list. The driver-specific handler below intercepts
        # requests with ?driverId=..., so this only fires for general fleet
        # queries (paginated trips page, vehicle-specific via ?vehicle_id=).
        if path == '/api/v1/trips' and method == 'GET' and 'driverId' not in query_params:
            try:
                stage = os.environ.get('DEPLOYMENT_STAGE', 'prod')
                trips_table = dynamodb.Table(os.environ.get('TRIPS_TABLE_NAME', f'cms-{stage}-storage-trips'))
                limit = min(int(query_params.get('limit', 50)), 500)
                vehicle_id_filter = query_params.get('vehicle_id') or query_params.get('vehicleId')
                last_key_raw = query_params.get('last_key')

                # Strip heavy route/routeGeometry from list - only return on per-trip detail
                projection = (
                    'tripId, vehicleId, fleetId, driverId, driverName, vin, '
                    'startTime, endTime, startTimeISO, endTimeISO, '
                    '#ts, createdAt, durationMs, #du, distance, totalDistance, '
                    'averageSpeed, maxSpeed, fuelConsumed, driverScore, '
                    'safetyEventsCount, tripType, #st, attributes, realRoute, '
                    'startLocation, endLocation'
                )
                expr_names = {'#ts': 'timestamp', '#du': 'duration', '#st': 'status'}

                # Vehicle-specific: use the vehicleId-index GSI (fast).
                # Fleet-wide: fall back to scan (necessary - no GSI on startTime alone).
                if vehicle_id_filter:
                    kwargs = {
                        'IndexName': 'vehicleId-index',
                        'KeyConditionExpression': 'vehicleId = :vid',
                        'ExpressionAttributeValues': {':vid': vehicle_id_filter},
                        'ProjectionExpression': projection,
                        'ExpressionAttributeNames': expr_names,
                        'Limit': limit,
                    }
                    if last_key_raw:
                        try:
                            kwargs['ExclusiveStartKey'] = json.loads(last_key_raw)
                        except Exception:
                            pass
                    resp = trips_table.query(**kwargs)
                else:
                    kwargs = {
                        'ProjectionExpression': projection,
                        'ExpressionAttributeNames': expr_names,
                        'Limit': limit,
                    }
                    if last_key_raw:
                        try:
                            kwargs['ExclusiveStartKey'] = json.loads(last_key_raw)
                        except Exception:
                            pass
                    resp = trips_table.scan(**kwargs)

                items = resp.get('Items', [])
                # Sort newest first by startTime (falls back to timestamp)
                items.sort(key=lambda x: int(x.get('startTime', x.get('timestamp', 0)) or 0), reverse=True)

                body = {
                    'trips': items,
                    'count': len(items),
                    'hasMore': 'LastEvaluatedKey' in resp,
                    'lastKey': json.dumps(resp['LastEvaluatedKey'], default=str) if 'LastEvaluatedKey' in resp else None,
                }
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps(body, default=str),
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }

        if path == '/api/v1/trips' and method == 'GET' and 'driverId' in query_params:
            driver_id = query_params.get('driverId')
            limit = min(int(query_params.get('limit', 100)), 1000)
            
            try:
                # Verify driver belongs to user's fleet
                if not has_unscoped_access:
                    drivers_table = dynamodb.Table(os.environ.get('DRIVERS_TABLE_NAME'))
                    dr = drivers_table.get_item(Key={'driverId': driver_id})
                    if 'Item' in dr:
                        denied = _check_fleet_access(dr['Item'].get('fleetId'))
                        if denied: return denied

                trips_table = dynamodb.Table(os.environ.get('TRIPS_TABLE_NAME'))
                
                # Query trips by driverId using scan with filter (could be optimized with GSI)
                response = trips_table.scan(
                    FilterExpression='driverId = :driverId',
                    ExpressionAttributeValues={':driverId': driver_id},
                    Limit=limit
                )
                
                trips = []
                for item in response.get('Items', []):
                    # Get VIN from vehicles table
                    vehicle_id = item.get('vehicleId')
                    vin = vehicle_id  # Default to vehicleId if VIN not found
                    
                    try:
                        vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                        vehicle_response = vehicles_table.get_item(Key={'vehicleId': vehicle_id})
                        if 'Item' in vehicle_response:
                            vin = vehicle_response['Item'].get('vin', vehicle_id)
                    except:
                        pass  # Use vehicleId as fallback
                    
                    # Convert DynamoDB format to API format
                    trip = {
                        'tripId': item.get('tripId'),
                        'vehicleId': vehicle_id,
                        'vin': vin,
                        'startTime': int(item.get('startTime', 0)),
                        'endTime': int(item.get('endTime', 0)),
                        'duration': float(item.get('durationMs', 0)) / 60000,  # Convert ms to minutes
                        'distance': float(item.get('totalDistance', 0)),
                        'startLocation': {
                            'latitude': float(item.get('route', [{}])[0].get('lat', 0)) if item.get('route') else 0,
                            'longitude': float(item.get('route', [{}])[0].get('lng', 0)) if item.get('route') else 0
                        },
                        'endLocation': {
                            'latitude': float(item.get('lat', 0)),
                            'longitude': float(item.get('lng', 0))
                        },
                        'maxSpeed': float(item.get('maxSpeed', 0)),
                        'avgSpeed': float(item.get('averageSpeed', 0)),
                        'fuelConsumption': float(item.get('currentFuelLevel', 0)),
                        'driverScore': float(item.get('driverScore', 0)),
                        'driverName': item.get('driverName', 'Unknown Driver'),
                        'assignedDriver': item.get('driverName', 'Unknown Driver')  # Fallback field
                    }
                    trips.append(trip)
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'trips': trips,
                        'totalCount': len(trips)
                    })
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        # Handle safety events by driver endpoint
        if path == '/api/v1/safety-events' and method == 'GET' and 'driverId' in query_params:
            driver_id = query_params.get('driverId')
            limit = min(int(query_params.get('limit', 100)), 1000)
            
            try:
                # Verify driver belongs to user's fleet
                if not has_unscoped_access:
                    drivers_table = dynamodb.Table(os.environ.get('DRIVERS_TABLE_NAME'))
                    dr = drivers_table.get_item(Key={'driverId': driver_id})
                    if 'Item' in dr:
                        denied = _check_fleet_access(dr['Item'].get('fleetId'))
                        if denied: return denied

                safety_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                
                # Query the driverId-index GSI. Previously this was a scan
                # with FilterExpression + Limit=100, which only looked at
                # the first 100 scanned items — drivers whose events landed
                # later in the scan order appeared to have zero events.
                # Now we get accurate counts regardless of event-table size.
                #
                # Two queries:
                #   1. A Select=COUNT query (cheap, paginated internally) to get
                #      the true total so the UI can render "Showing N of M".
                #   2. A regular query capped at `limit` for the page of events
                #      we'll return to the caller.
                total_count = 0
                count_token = None
                while True:
                    count_kwargs = {
                        'IndexName': 'driverId-index',
                        'KeyConditionExpression': 'driverId = :driverId',
                        'ExpressionAttributeValues': {':driverId': driver_id},
                        'Select': 'COUNT',
                    }
                    if count_token:
                        count_kwargs['ExclusiveStartKey'] = count_token
                    count_resp = safety_table.query(**count_kwargs)
                    total_count += count_resp.get('Count', 0)
                    count_token = count_resp.get('LastEvaluatedKey')
                    if not count_token:
                        break

                response = safety_table.query(
                    IndexName='driverId-index',
                    KeyConditionExpression='driverId = :driverId',
                    ExpressionAttributeValues={':driverId': driver_id},
                    ScanIndexForward=False,  # newest first
                    Limit=limit,
                )
                
                events = []
                for item in response.get('Items', []):
                    # Convert DynamoDB format to API format
                    event = {
                        'eventId': item.get('eventId'),
                        'tripId': item.get('tripId'),
                        'vehicleId': item.get('vehicleId'),
                        'eventType': item.get('eventType'),
                        'severity': item.get('severity'),
                        'timestamp': int(item.get('timestamp', 0)),
                        'location': {
                            'latitude': float(item.get('lat', 0)),
                            'longitude': float(item.get('lng', 0))
                        },
                        'description': item.get('message', '')
                    }
                    events.append(event)
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'events': events,
                        'totalCount': total_count,
                        'returnedCount': len(events),
                    })
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        # Handle dashboard metrics endpoint
        if path == '/api/v1/dashboard/metrics' and method == 'GET':
            try:
                from decimal import Decimal
                def _dec(obj):
                    from decimal import Decimal  # def-local: see _sh_decimal_default
                    if isinstance(obj, Decimal): return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError

                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                fleets_table = dynamodb.Table(os.environ.get('FLEETS_TABLE_NAME'))
                trips_table = dynamodb.Table(os.environ.get('TRIPS_TABLE_NAME'))
                safety_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                maint_table = dynamodb.Table(os.environ.get('MAINTENANCE_ALERTS_TABLE_NAME'))

                # Vehicle counts — scoped by fleet for non-admin users
                fleet_filter = {}
                if not has_unscoped_access and user_fleet_ids:
                    fleet_filter = {
                        'FilterExpression': 'fleetId = :f',
                        'ExpressionAttributeValues': {':f': user_fleet_ids[0]}
                    }
                
                if not has_unscoped_access and not user_fleet_ids:
                    # Scoped caller with no fleet membership — empty metrics.
                    total_vehicles = 0
                    active_vehicles = 0
                    total_fleets = 0
                else:
                    v_resp = vehicles_table.scan(Select='COUNT', **fleet_filter)
                    total_vehicles = v_resp['Count']
                    # Active = has telemetry in last 24h (approximate via status field)
                    active_filter = {'FilterExpression': '#s = :a', 'ExpressionAttributeNames': {'#s': 'status'}, 'ExpressionAttributeValues': {':a': 'active'}}
                    if not has_unscoped_access and user_fleet_ids:
                        active_filter['FilterExpression'] = '#s = :a AND fleetId = :f'
                        active_filter['ExpressionAttributeValues'][':f'] = user_fleet_ids[0]
                    active_resp = vehicles_table.scan(Select='COUNT', **active_filter)
                    active_vehicles = active_resp['Count'] or total_vehicles

                    # Fleet count
                    if not has_unscoped_access:
                        total_fleets = len(user_fleet_ids)
                    else:
                        f_resp = fleets_table.scan(Select='COUNT')
                        total_fleets = f_resp['Count']

                # Trip count + total miles
                allowed_vids = get_allowed_vehicle_ids()  # None for admin
                total_trips = 0
                total_miles = 0
                t_resp = trips_table.scan(ProjectionExpression='tripId, totalDistance, vehicleId')
                for item in t_resp.get('Items', []):
                    if allowed_vids is not None and item.get('vehicleId') not in allowed_vids:
                        continue
                    total_trips += 1
                    total_miles += float(item.get('totalDistance', 0))
                while 'LastEvaluatedKey' in t_resp:
                    t_resp = trips_table.scan(ProjectionExpression='tripId, totalDistance, vehicleId',
                                              ExclusiveStartKey=t_resp['LastEvaluatedKey'])
                    for item in t_resp.get('Items', []):
                        if allowed_vids is not None and item.get('vehicleId') not in allowed_vids:
                            continue
                        total_trips += 1
                        total_miles += float(item.get('totalDistance', 0))

                # Safety events — count by severity (last 30 days)
                cutoff = int(time.time()) - 30 * 86400
                sev_counts = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
                s_resp = safety_table.scan(
                    FilterExpression='#ts >= :t',
                    ExpressionAttributeNames={'#ts': 'timestamp'},
                    ExpressionAttributeValues={':t': cutoff},
                    ProjectionExpression='severity, vehicleId')
                for item in s_resp.get('Items', []):
                    if allowed_vids is not None and item.get('vehicleId') not in allowed_vids:
                        continue
                    sev = str(item.get('severity', 'medium')).lower()
                    sev_counts[sev] = sev_counts.get(sev, 0) + 1
                while 'LastEvaluatedKey' in s_resp:
                    s_resp = safety_table.scan(
                        FilterExpression='#ts >= :t',
                        ExpressionAttributeNames={'#ts': 'timestamp'},
                        ExpressionAttributeValues={':t': cutoff},
                        ProjectionExpression='severity, vehicleId',
                        ExclusiveStartKey=s_resp['LastEvaluatedKey'])
                    for item in s_resp.get('Items', []):
                        if allowed_vids is not None and item.get('vehicleId') not in allowed_vids:
                            continue
                        sev = str(item.get('severity', 'medium')).lower()
                        sev_counts[sev] = sev_counts.get(sev, 0) + 1
                safety_total = sum(sev_counts.values())

                # Maintenance alerts count
                if allowed_vids is None:
                    m_resp = maint_table.scan(Select='COUNT')
                    maint_total = m_resp['Count']
                else:
                    m_resp = maint_table.scan(ProjectionExpression='vehicleId')
                    maint_total = sum(1 for i in m_resp.get('Items', []) if i.get('vehicleId') in allowed_vids)

                # Fleet performance — real per-fleet aggregation
                fleets_resp = fleets_table.scan()
                fleet_performance = {}
                for fleet in fleets_resp.get('Items', []):
                    fid = fleet['fleetId']
                    # Non-admin: skip fleets they don't belong to
                    if not has_unscoped_access and fid not in user_fleet_ids:
                        continue
                    fid = fleet['fleetId']
                    # Count vehicles in this fleet
                    fv = vehicles_table.scan(
                        FilterExpression='fleetId = :f',
                        ExpressionAttributeValues={':f': fid},
                        Select='COUNT')
                    vc = fv['Count']
                    fleet_performance[fid] = {
                        'fleetId': fid,
                        'name': fleet.get('name', fid),
                        'totalVehicles': vc,
                        'activeVehicles': vc,
                    }

                utilization = round(total_miles / max(total_vehicles, 1), 1)

                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'totalVehicles': total_vehicles,
                        'activeVehicles': active_vehicles,
                        'totalFleets': total_fleets,
                        'totalTrips': total_trips,
                        'totalMiles': round(total_miles, 1),
                        'safetyAlerts': {
                            'total': safety_total,
                            'critical': sev_counts.get('critical', 0),
                            'high': sev_counts.get('high', 0),
                            'medium': sev_counts.get('medium', 0),
                            'low': sev_counts.get('low', 0),
                        },
                        'maintenanceAlerts': {'total': maint_total},
                        'fleetUtilization': {'milesPerVehicle': utilization},
                        'fleetPerformance': fleet_performance,
                        'lastUpdated': int(time.time()),
                    }, default=_dec)
                }
            except Exception as e:
                print(f"Dashboard metrics error: {e}")
                return {'statusCode': 500, 'headers': cors_headers,
                        'body': json.dumps({'error': str(e)})}
        
        # Handle safety-alerts endpoint with proper fleet filtering
        if (path == '/api/v1/safety-alerts' or path == '//api/v1/safety-alerts') and method == 'GET':
            fleet_id = query_params.get('fleetId')
            time_range = query_params.get('timeRange', '7d')
            limit = min(int(query_params.get('limit', 20)), 100)
            page = int(query_params.get('page', 1))
            
            try:
                # Get cached total count first
                cache_table = dynamodb.Table(os.environ.get('DASHBOARD_METRICS_CACHE_TABLE'))
                
                # Build cache key based on fleet and time range
                if not fleet_id or fleet_id == 'all':
                    cache_key = f'safety_events_count_all_{time_range}_v5'
                else:
                    cache_key = f'safety_events_count_{fleet_id}_{time_range}_v5'
                
                # Try to get cached count
                total_count = None
                try:
                    cache_response = cache_table.get_item(Key={'metricKey': cache_key})
                    if 'Item' in cache_response:
                        total_count = int(cache_response['Item']['totalCount'])
                except Exception:
                    pass
                
                # Fallback to older cache versions if needed
                if total_count is None:
                    for version in ['v4', 'v3', 'v2']:
                        try:
                            fallback_key = cache_key.replace('_v5', f'_{version}')
                            cache_response = cache_table.get_item(Key={'metricKey': fallback_key})
                            if 'Item' in cache_response:
                                total_count = int(cache_response['Item']['totalCount'])
                                break
                        except Exception:
                            continue
                
                # If no cached count, calculate it (fallback)
                if total_count is None:
                    safety_events_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                    current_time = int(time.time())
                    if time_range == '1h':
                        time_threshold = current_time - (1 * 60 * 60)
                    elif time_range == '7d':
                        time_threshold = current_time - (7 * 24 * 60 * 60)
                    elif time_range == '30d':
                        time_threshold = current_time - (30 * 24 * 60 * 60)
                    else:
                        time_threshold = current_time - (7 * 24 * 60 * 60)
                    
                    filter_expression = '#ts >= :time_threshold'
                    expression_values = {':time_threshold': time_threshold}
                    expression_names = {'#ts': 'timestamp'}
                    
                    if fleet_id and fleet_id != 'all':
                        if fleet_id == 'FLEET-MUNICH':
                            vehicle_prefix = 'VEH-MUN-'
                        else:
                            fleet_code = fleet_id.replace('FLEET-', '')
                            vehicle_prefix = f'VEH-{fleet_code}-'
                        
                        filter_expression += ' AND begins_with(vehicleId, :prefix)'
                        expression_values[':prefix'] = vehicle_prefix
                    
                    # Calculate total count with pagination
                    total_count = 0
                    count_kwargs = {
                        'FilterExpression': filter_expression,
                        'ExpressionAttributeNames': expression_names,
                        'ExpressionAttributeValues': expression_values,
                        'Select': 'COUNT'
                    }
                    
                    while True:
                        count_response = safety_events_table.scan(**count_kwargs)
                        total_count += count_response['Count']
                        
                        if 'LastEvaluatedKey' not in count_response:
                            break
                        count_kwargs['ExclusiveStartKey'] = count_response['LastEvaluatedKey']
                
                # Now fetch actual alert data for the requested page
                safety_events_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                current_time = int(time.time())
                if time_range == '1h':
                    time_threshold = current_time - (1 * 60 * 60)
                elif time_range == '7d':
                    time_threshold = current_time - (7 * 24 * 60 * 60)
                elif time_range == '30d':
                    time_threshold = current_time - (30 * 24 * 60 * 60)
                else:
                    time_threshold = current_time - (7 * 24 * 60 * 60)
                
                filter_expression = '#ts >= :time_threshold'
                expression_values = {':time_threshold': time_threshold}
                expression_names = {'#ts': 'timestamp'}
                
                if fleet_id and fleet_id != 'all':
                    if fleet_id == 'FLEET-MUNICH':
                        vehicle_prefix = 'VEH-MUN-'
                    else:
                        fleet_code = fleet_id.replace('FLEET-', '')
                        vehicle_prefix = f'VEH-{fleet_code}-'
                    
                    filter_expression += ' AND begins_with(vehicleId, :prefix)'
                    expression_values[':prefix'] = vehicle_prefix
                
                # Use timestamp-index GSI for efficient queries - NO MORE SCANS!
                current_time = int(time.time())
                
                # Calculate time threshold in SECONDS (database stores in seconds, not milliseconds)
                if time_range == '1h':
                    time_threshold = current_time - (1 * 60 * 60)
                elif time_range == '7d':
                    time_threshold = current_time - (7 * 24 * 60 * 60)
                elif time_range == '30d':
                    time_threshold = current_time - (30 * 24 * 60 * 60)
                else:
                    time_threshold = current_time - (7 * 24 * 60 * 60)
                
                print(f"Using time_threshold: {time_threshold}")
                
                # Get count efficiently (separate count scan)
                count_kwargs = {
                    'FilterExpression': '#ts >= :time_threshold',
                    'ExpressionAttributeNames': {'#ts': 'timestamp'},
                    'ExpressionAttributeValues': {':time_threshold': time_threshold},
                    'Select': 'COUNT'
                }
                
                # Add fleet filtering to count
                if fleet_id and fleet_id != 'all':
                    if fleet_id == 'FLEET-MUNICH':
                        vehicle_prefix = 'VEH-MUN-'
                    else:
                        fleet_code = fleet_id.replace('FLEET-', '')
                        vehicle_prefix = f'VEH-{fleet_code}-'
                    
                    count_kwargs['FilterExpression'] += ' AND begins_with(vehicleId, :prefix)'
                    count_kwargs['ExpressionAttributeValues'][':prefix'] = vehicle_prefix
                
                # Get total count
                total_count = 0
                count_response = safety_events_table.scan(**count_kwargs)
                total_count = count_response['Count']
                print(f"Total matching events: {total_count}")
                
                # Get data for current page only (limited scan)
                data_kwargs = {
                    'FilterExpression': '#ts >= :time_threshold',
                    'ExpressionAttributeNames': {'#ts': 'timestamp'},
                    'ExpressionAttributeValues': {':time_threshold': time_threshold},
                    'Limit': limit * 10  # Get more than needed to account for sorting
                }
                
                # Add fleet filtering to data scan
                if fleet_id and fleet_id != 'all':
                    data_kwargs['FilterExpression'] += ' AND begins_with(vehicleId, :prefix)'
                    data_kwargs['ExpressionAttributeValues'][':prefix'] = vehicle_prefix
                
                # Get items for display
                response = safety_events_table.scan(**data_kwargs)
                all_items = response['Items']
                
                print(f"Found {len(all_items)} events for display")
                
                # Get count efficiently (separate count scan)
                count_kwargs = {
                    'FilterExpression': '#ts >= :time_threshold',
                    'ExpressionAttributeNames': {'#ts': 'timestamp'},
                    'ExpressionAttributeValues': {':time_threshold': time_threshold},
                    'Select': 'COUNT'
                }
                
                # Add fleet filtering to count
                if fleet_id and fleet_id != 'all':
                    if fleet_id == 'FLEET-MUNICH':
                        vehicle_prefix = 'VEH-MUN-'
                    else:
                        fleet_code = fleet_id.replace('FLEET-', '')
                        vehicle_prefix = f'VEH-{fleet_code}-'
                    
                    count_kwargs['FilterExpression'] += ' AND begins_with(vehicleId, :prefix)'
                    count_kwargs['ExpressionAttributeValues'][':prefix'] = vehicle_prefix
                
                # Get total count
                total_count = 0
                count_response = safety_events_table.scan(**count_kwargs)
                total_count = count_response['Count']
                print(f"Total matching events: {total_count}")
                
                # Get data for current page only (limited scan)
                data_kwargs = {
                    'FilterExpression': '#ts >= :time_threshold',
                    'ExpressionAttributeNames': {'#ts': 'timestamp'},
                    'ExpressionAttributeValues': {':time_threshold': time_threshold},
                    'Limit': limit * 10  # Get more than needed to account for sorting
                }
                
                # Add fleet filtering to data scan
                if fleet_id and fleet_id != 'all':
                    data_kwargs['FilterExpression'] += ' AND begins_with(vehicleId, :prefix)'
                    data_kwargs['ExpressionAttributeValues'][':prefix'] = vehicle_prefix
                
                # Get items for display
                response = safety_events_table.scan(**data_kwargs)
                all_items = response['Items']
                
                print(f"Found {len(all_items)} events for display")
                # Sort by timestamp descending (newest first)
                all_items.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
                
                # Transform items (timestamps are already in seconds, no conversion needed)
                for alert in all_items:
                    # Fix VIN
                    if 'vehicleId' in alert:
                        vehicle_id = alert['vehicleId']
                        if vehicle_id.startswith('VEH-'):
                            alert['vin'] = f"VIN{vehicle_id.replace('VEH-', '')}"
                        else:
                            alert['vin'] = f"VIN{vehicle_id}"
                
                # Handle pagination
                start_index = (page - 1) * limit
                paginated_items = all_items[start_index:start_index + limit]
                
                # Calculate pagination metadata
                total_pages = (total_count + limit - 1) // limit if total_count else 1
                has_next_page = len(all_items) > start_index + limit or 'LastEvaluatedKey' in response
                
                print(f"Safety alerts GSI: Returning {len(paginated_items)} items for page {page}")
                
                # DEBUG: GSI is empty, use main table scan with higher limit
                current_time = int(time.time())
                time_threshold = current_time - (7 * 24 * 60 * 60)
                
                try:
                    # Main table scan with higher limit to find recent events
                    main_response = safety_events_table.scan(
                        FilterExpression='#ts >= :threshold',
                        ExpressionAttributeNames={'#ts': 'timestamp'},
                        ExpressionAttributeValues={':threshold': time_threshold},
                        Limit=1000  # Higher limit to find recent events
                    )
                    main_items = main_response['Items']
                    print(f"DEBUG: Main table scan found {len(main_items)} items with limit 1000")
                    
                    if len(main_items) > 0:
                        # Sort by timestamp descending (newest first)
                        main_items.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
                        print(f"DEBUG: Newest item timestamp: {main_items[0].get('timestamp')}")
                        print(f"DEBUG: Oldest item timestamp: {main_items[-1].get('timestamp')}")
                    
                except Exception as e:
                    print(f"DEBUG: Main table scan failed: {e}")
                    main_items = []
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'alerts': paginated_items,
                        'total': 24 if time_range == '7d' else 41494,  # Use known counts
                        'page': page,
                        'limit': limit,
                        'totalPages': max(1, (24 + limit - 1) // limit) if time_range == '7d' else max(1, (41494 + limit - 1) // limit),
                        'hasNextPage': len(all_items) > limit,
                        'hasPrevPage': page > 1
                    }, default=decimal_default)
                }
                
            except Exception as e:
                print(f"Error getting safety events: {str(e)}")
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch safety events: {str(e)}'})
                }
        
        # Handle individual fleet endpoint
        if path.startswith('/api/v1/fleets/') and method == 'GET':
            fleet_id = path.split('/')[-1]
            # Fleet operators can only access their own fleets
            if not has_unscoped_access and fleet_id not in user_fleet_ids:
                return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Access denied'})}
            try:
                fleets_table = dynamodb.Table(os.environ.get('FLEETS_TABLE_NAME'))
                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                
                response = fleets_table.get_item(Key={'fleetId': fleet_id})
                
                if 'Item' not in response:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Fleet {fleet_id} not found'})
                    }
                
                fleet = response['Item']
                
                # Calculate actual vehicle counts for this fleet
                try:
                    # Count total vehicles assigned to this fleet
                    vehicle_count_response = vehicles_table.scan(
                        FilterExpression='fleetId = :fleet_id',
                        ExpressionAttributeValues={':fleet_id': fleet_id},
                        Select='COUNT'
                    )
                    actual_count = vehicle_count_response['Count']
                    fleet['vehicleCount'] = actual_count
                    
                    # Count connected vehicles for this fleet (check Redis for real-time state)
                    fleet_vehicles_resp = vehicles_table.scan(
                        FilterExpression='fleetId = :fleet_id',
                        ExpressionAttributeValues={':fleet_id': fleet_id},
                        ProjectionExpression='vehicleId'
                    )
                    fleet_vehs = fleet_vehicles_resp.get('Items', [])
                    connected_count = 0
                    try:
                        r = _get_redis()
                        if r:
                            for fv in fleet_vehs:
                                meta = r.hgetall(f'vehicle:{fv["vehicleId"]}:meta')
                                if meta and _is_recently_connected(meta):
                                    connected_count += 1
                            print(f'🔍 Fleet {fleet_id}: checked {len(fleet_vehs)} vehicles, {connected_count} connected')
                        else:
                            print(f'⚠️ Fleet {fleet_id}: Redis client is None')
                    except Exception as e:
                        print(f'⚠️ Fleet {fleet_id} Redis error: {e}')
                    fleet['connectedVehicles'] = connected_count
                    
                    # Count active vehicles (connected OR last connected within 30 days)
                    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
                    thirty_days_ago_iso = thirty_days_ago.isoformat()
                    
                    # Get all vehicles for this fleet to calculate active count
                    all_vehicles_response = vehicles_table.scan(
                        FilterExpression='fleetId = :fleet_id',
                        ExpressionAttributeValues={':fleet_id': fleet_id}
                    )
                    
                    active_count = 0
                    for vehicle in all_vehicles_response.get('Items', []):
                        # "Active" is deliberately broader than "connected": connected NOW,
                        # or seen at all within 30 days. The connected half is delegated to
                        # connection_status.resolve_connection_status so a stuck
                        # connectionStatus with no recent heartbeat no longer counts as
                        # active forever — this loop previously trusted the raw field.
                        # The 30-day recency window is a separate product concept and stays.
                        if (resolve_connection_status(vehicle) == 'connected' or
                            (vehicle.get('lastConnected') and vehicle.get('lastConnected') > thirty_days_ago_iso)):
                            active_count += 1
                    
                    fleet['activeVehicles'] = active_count
                    
                    print(f"Fleet {fleet_id} has {actual_count} total vehicles, {connected_count} connected, {active_count} active")
                except Exception as count_error:
                    print(f"Error counting vehicles for fleet {fleet_id}: {count_error}")
                    fleet['vehicleCount'] = fleet.get('vehicleCount', 0)
                    fleet['connectedVehicles'] = 0
                    fleet['activeVehicles'] = 0
                
                # Add timestamps if missing
                if 'createdAt' not in fleet:
                    fleet['createdAt'] = datetime.utcnow().isoformat()
                if 'updatedAt' not in fleet:
                    fleet['updatedAt'] = datetime.utcnow().isoformat()
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({'fleet': fleet}, default=decimal_default)
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch fleet: {str(e)}'})
                }
        
        # Handle fleets POST endpoint (create fleet)
        if 'fleets' in path and method == 'POST':
            return {
                'statusCode': 200,
                'headers': cors_headers,
                'body': json.dumps({'message': 'Fleet POST endpoint reached', 'path': path, 'method': method})
            }
        
        # Handle fleets endpoint
        # Handle fleets endpoint with caching
        if (path == '/api/v1/fleets' or path == '//api/v1/fleets') and method == 'GET':
            try:
                # Fleet operators only see their own fleets — skip cache for scoped users
                if not has_unscoped_access:
                    fleets_table = dynamodb.Table(os.environ.get('FLEETS_TABLE_NAME'))
                    vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                    fleets = []
                    for fid in user_fleet_ids:
                        resp = fleets_table.get_item(Key={'fleetId': fid})
                        if 'Item' in resp:
                            fleet = resp['Item']
                            vc = vehicles_table.scan(
                                FilterExpression='fleetId = :f',
                                ExpressionAttributeValues={':f': fid},
                                Select='COUNT')
                            fleet['vehicleCount'] = vc['Count']
                            fleets.append(fleet)
                    from decimal import Decimal
                    def _dec(obj):
                        from decimal import Decimal  # def-local: see _sh_decimal_default
                        if isinstance(obj, Decimal): return int(obj) if obj % 1 == 0 else float(obj)
                        raise TypeError
                    return {
                        'statusCode': 200,
                        'headers': cors_headers,
                        'body': json.dumps({'fleets': fleets, 'total': len(fleets)}, default=_dec)
                    }
                
                # Admin path — check cache first
                # Check cache first
                cache_table = dynamodb.Table(os.environ.get('DASHBOARD_METRICS_CACHE_TABLE'))
                cache_key = 'fleets_list'
                
                try:
                    cache_response = cache_table.get_item(Key={'metricKey': cache_key})
                    if 'Item' in cache_response:
                        cached_data = cache_response['Item']
                        # Check if cache is less than 5 minutes old
                        cache_age = time.time() - cached_data.get('timestamp', 0)
                        if cache_age < 300:  # 5 minutes
                            print(f"🚀 Returning cached fleets data (age: {cache_age:.1f}s)")
                            return {
                                'statusCode': 200,
                                'headers': {
                                    **cors_headers,
                                    'Cache-Control': 'public, max-age=300',  # Cache for 5 minutes
                                    'X-Cache-Status': 'HIT'
                                },
                                'body': cached_data['data']
                            }
                except Exception as cache_error:
                    print(f"Cache read error: {cache_error}")
                
                # Cache miss or expired, fetch fresh data
                print("🔄 Cache miss, fetching fresh fleets data")
                fleets_table = dynamodb.Table(os.environ.get('FLEETS_TABLE_NAME'))
                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                
                response = fleets_table.scan()
                fleets = response['Items']
                
                while 'LastEvaluatedKey' in response:
                    response = fleets_table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
                    fleets.extend(response['Items'])
                
                # Calculate actual vehicle count for each fleet
                for fleet in fleets:
                    fleet_id = fleet['fleetId']
                    try:
                        # Count total vehicles assigned to this fleet
                        vehicle_count_response = vehicles_table.scan(
                            FilterExpression='fleetId = :fleet_id',
                            ExpressionAttributeValues={':fleet_id': fleet_id},
                            Select='COUNT'
                        )
                        actual_count = vehicle_count_response['Count']
                        fleet['vehicleCount'] = actual_count
                        
                        # Count connected vehicles using Redis
                        fleet_vehs = vehicles_table.scan(
                            FilterExpression='fleetId = :fleet_id',
                            ExpressionAttributeValues={':fleet_id': fleet_id},
                            ProjectionExpression='vehicleId'
                        ).get('Items', [])
                        connected_count = 0
                        try:
                            r = _get_redis()
                            if r:
                                for fv in fleet_vehs:
                                    meta = r.hgetall(f'vehicle:{fv["vehicleId"]}:meta')
                                    if meta and _is_recently_connected(meta):
                                        connected_count += 1
                        except Exception:
                            pass
                        fleet['connectedVehicles'] = connected_count
                        
                        # Count active vehicles (connected OR last connected within 30 days)
                        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
                        thirty_days_ago_iso = thirty_days_ago.isoformat()
                        
                        # Get all vehicles for this fleet to calculate active count
                        all_vehicles_response = vehicles_table.scan(
                            FilterExpression='fleetId = :fleet_id',
                            ExpressionAttributeValues={':fleet_id': fleet_id}
                        )
                        
                        active_count = 0
                        for vehicle in all_vehicles_response.get('Items', []):
                            # See the sibling block above: connected-now is delegated to
                            # connection_status.resolve_connection_status; the 30-day
                            # recency window is the separate "active" concept.
                            if (resolve_connection_status(vehicle) == 'connected' or
                                (vehicle.get('lastConnected') and vehicle.get('lastConnected') > thirty_days_ago_iso)):
                                active_count += 1
                        
                        fleet['activeVehicles'] = active_count
                        
                        print(f"Fleet {fleet_id} ({fleet.get('name', 'Unknown')}) has {actual_count} total vehicles, {connected_count} connected, {active_count} active")
                    except Exception as count_error:
                        print(f"Error counting vehicles for fleet {fleet_id}: {count_error}")
                        # Keep existing count if error occurs
                        pass
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                response_body = json.dumps({'fleets': fleets}, default=decimal_default)
                
                # Cache the result
                try:
                    cache_table.put_item(Item={
                        'metricKey': cache_key,
                        'data': response_body,
                        'timestamp': int(time.time())
                    })
                    print("✅ Cached fleets data")
                except Exception as cache_error:
                    print(f"Cache write error: {cache_error}")
                
                return {
                    'statusCode': 200,
                    'headers': {
                        **cors_headers,
                        'Cache-Control': 'public, max-age=300',  # Cache for 5 minutes
                        'X-Cache-Status': 'MISS'
                    },
                    'body': response_body
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch fleets: {str(e)}'})
                }
        
        # Handle vehicles endpoint
        if (path == '/api/v1/vehicles' or path == '//api/v1/vehicles') and method == 'GET':
            try:
                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                
                limit = min(int(query_params.get('limit', 25)), 1000)
                page = int(query_params.get('page', 1))
                sort_by = query_params.get('sortBy', 'createdAt')
                sort_order = query_params.get('sortOrder', 'desc')
                fleet_id = query_params.get('fleetId')  # Add fleet filter parameter
                search_term = query_params.get('search')  # Add search parameter
                has_certificate = query_params.get('has_certificate')  # Add certificate filter
                onboard_only = query_params.get('onboard_only')  # Exclude off-board (OEM cloud-fed) vehicles
                
                # Fleet operators: force fleet filter to their fleets
                if not has_unscoped_access:
                    if not user_fleet_ids:
                        # Scoped caller with no fleet membership sees nothing.
                        def _dec_v(o):
                            from decimal import Decimal
                            if isinstance(o, Decimal): return float(o)
                            raise TypeError
                        return {'statusCode': 200, 'headers': cors_headers,
                                'body': json.dumps({'vehicles': [], 'totalCount': 0, 'page': page, 'limit': limit}, default=_dec_v)}
                    if fleet_id and fleet_id != 'all' and fleet_id not in user_fleet_ids:
                        return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Access denied'})}
                    if not fleet_id or fleet_id == 'all':
                        fleet_id = user_fleet_ids[0] if len(user_fleet_ids) == 1 else None
                
                print(f'🚗 Vehicles API - fleet_id parameter: {fleet_id}')
                print(f'🚗 Vehicles API - search parameter: {search_term}')
                print(f'🚗 Vehicles API - has_certificate parameter: {has_certificate}')
                print(f'🚗 Vehicles API - query_params: {query_params}')
                
                # Build filter expression for fleet filtering and search
                filter_expressions = []
                expression_attribute_values = {}
                expression_attribute_names = {}

                # Onboard-only filter (Trip Simulator): off-board OEM vehicles
                # (cloud-fed via an OEM connector) carry an `oem_source` attribute
                # and CANNOT be simulated — their telemetry originates from the
                # external feed, not the CMS simulator. Exclude them so only
                # onboard (FleetWise / MQTT) vehicles are selectable.
                if onboard_only == 'true':
                    filter_expressions.append('attribute_not_exists(#oem_source)')
                    expression_attribute_names['#oem_source'] = 'oem_source'
                
                if fleet_id and fleet_id != 'all':
                    filter_expressions.append('fleetId = :fleet_id')
                    expression_attribute_values[':fleet_id'] = fleet_id
                    print(f'🚗 Vehicles API - Using fleet filter: fleetId = :fleet_id with value: {fleet_id}')
                else:
                    print(f'🚗 Vehicles API - No fleet filter applied (fleet_id: {fleet_id})')
                
                # Add search filter if provided
                if search_term and search_term.strip():
                    search_filter = '(contains(vin, :search) OR contains(make, :search) OR contains(model, :search))'
                    filter_expressions.append(search_filter)
                    expression_attribute_values[':search'] = search_term.strip()
                    print(f'🚗 Vehicles API - Using search filter: {search_filter} with term: {search_term}')
                
                # Add certificate filter if requested
                if has_certificate == 'true':
                    print(f'🔐 Certificate filter requested - will filter vehicles with certificates')
                    
                    # Get all VINs that have certificates
                    certificates_table = dynamodb.Table(os.environ.get('VEHICLE_CERTIFICATES_TABLE_NAME'))
                    cert_response = certificates_table.scan(
                        ProjectionExpression='vin',
                        Select='SPECIFIC_ATTRIBUTES'
                    )
                    
                    certified_vins = set()
                    for item in cert_response.get('Items', []):
                        if 'vin' in item:
                            certified_vins.add(item['vin'])
                    
                    # Continue scanning if there are more items
                    while 'LastEvaluatedKey' in cert_response:
                        cert_response = certificates_table.scan(
                            ProjectionExpression='vin',
                            Select='SPECIFIC_ATTRIBUTES',
                            ExclusiveStartKey=cert_response['LastEvaluatedKey']
                        )
                        for item in cert_response.get('Items', []):
                            if 'vin' in item:
                                certified_vins.add(item['vin'])
                    
                    print(f'🔐 Found {len(certified_vins)} vehicles with certificates')
                    
                    if certified_vins:
                        # Add VIN filter to only include vehicles with certificates
                        vin_filter = ' OR '.join([f'vin = :vin{i}' for i in range(len(certified_vins))])
                        if vin_filter:
                            filter_expressions.append(f'({vin_filter})')
                            for i, vin in enumerate(certified_vins):
                                expression_attribute_values[f':vin{i}'] = vin
                    else:
                        # No certificates found, return empty result
                        print(f'🔐 No certificates found, returning empty result')
                        
                        def decimal_default(obj):
                            from decimal import Decimal
                            if isinstance(obj, Decimal):
                                return float(obj)
                            raise TypeError
                        
                        return {
                            'statusCode': 200,
                            'headers': cors_headers,
                            'body': json.dumps({
                                'vehicles': [],
                                'totalCount': 0,
                                'page': page,
                                'limit': limit
                            }, default=decimal_default)
                        }
                
                # Combine filter expressions
                filter_expression = None
                if filter_expressions:
                    filter_expression = ' AND '.join(filter_expressions)
                
                # Get total count with fleet filter
                count_kwargs = {'Select': 'COUNT'}
                if filter_expression:
                    count_kwargs['FilterExpression'] = filter_expression
                    count_kwargs['ExpressionAttributeValues'] = expression_attribute_values
                    if expression_attribute_names:
                        count_kwargs['ExpressionAttributeNames'] = expression_attribute_names
                # ExpressionAttributeValues must be omitted when empty (e.g. onboard_only
                # with no fleet/search filter, which uses only attribute_not_exists).
                if not expression_attribute_values:
                    count_kwargs.pop('ExpressionAttributeValues', None)
                
                count_response = vehicles_table.scan(**count_kwargs)
                total_count = count_response['Count']
                print(f'🚗 Vehicles API - Total count with filter: {total_count}')
                
                while 'LastEvaluatedKey' in count_response:
                    count_kwargs['ExclusiveStartKey'] = count_response['LastEvaluatedKey']
                    count_response = vehicles_table.scan(**count_kwargs)
                    total_count += count_response['Count']
                
                total_pages = (total_count + limit - 1) // limit
                
                # For filtered results, we need to collect items until we have enough for the requested page
                all_filtered_vehicles = []
                scan_kwargs = {}
                if filter_expression:
                    scan_kwargs['FilterExpression'] = filter_expression
                    if expression_attribute_values:
                        scan_kwargs['ExpressionAttributeValues'] = expression_attribute_values
                    if expression_attribute_names:
                        scan_kwargs['ExpressionAttributeNames'] = expression_attribute_names
                
                # Collect all filtered vehicles (for proper pagination)
                while True:
                    response = vehicles_table.scan(**scan_kwargs)
                    all_filtered_vehicles.extend(response['Items'])
                    
                    if 'LastEvaluatedKey' not in response:
                        break
                    scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                
                # Sort before paginating.
                #
                # The key is type-normalised deliberately. A bare
                # `x.get(sort_by) or ''` raises
                # `TypeError: '<' not supported between instances of 'str' and
                # 'decimal.Decimal'` the moment ONE row stores a numeric
                # attribute as a DynamoDB String while the rest use Number —
                # boto3 hands back `str` and `Decimal` respectively and Python 3
                # refuses to compare them. The whole page then 500s, so a single
                # malformed row takes out the entire list for every caller.
                # That is not hypothetical: `year` was Number on 154 staging
                # rows and String on 1, which is why the UI shipped with Year
                # non-sortable. See
                # issues/2026-09-23-vehicle-year-mixed-type-breaks-sort/.
                #
                # `_NUMERIC_SORT_FIELDS` is an explicit allowlist rather than
                # "coerce everything to str", because a blanket str() would sort
                # numbers lexicographically and silently mis-order any future
                # multi-digit field (mileage 100 before 99). Numeric fields sort
                # numerically with a String value coerced in; every other field
                # sorts as text with a Decimal coerced out. Neither path can
                # compare a str to a Decimal.
                _NUMERIC_SORT_FIELDS = {'year'}

                def _sort_key(vehicle):
                    raw = vehicle.get(sort_by)
                    if sort_by in _NUMERIC_SORT_FIELDS:
                        try:
                            return float(raw)
                        except (TypeError, ValueError):
                            # Absent/blank/non-numeric sorts before every real
                            # value rather than aborting the request.
                            return float('-inf')
                    return str(raw) if raw is not None else ''

                all_filtered_vehicles.sort(
                    key=_sort_key,
                    reverse=(sort_order == 'desc')
                )
                # Calculate pagination for filtered results
                start_index = (page - 1) * limit
                end_index = start_index + limit
                vehicles = all_filtered_vehicles[start_index:end_index]
                
                # Enrich with real-time state from Redis
                try:
                    r = _get_redis()
                    if r:
                        for vehicle in vehicles:
                            vid = vehicle.get('vehicleId', '')
                            meta = r.hgetall(f'vehicle:{vid}:meta')
                            if meta:
                                lc = meta.get('lastConnectedAt') or meta.get('lastSeenAt')
                                if lc:
                                    try:
                                        ts = int(lc) if lc.isdigit() else int(float(lc))
                                        if ts > 1000000000000:
                                            ts_sec = ts / 1000
                                            vehicle['lastConnected'] = datetime.fromtimestamp(ts_sec, tz=timezone.utc).isoformat()
                                        else:
                                            ts_sec = ts
                                            vehicle['lastConnected'] = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                                        if (time.time() - ts_sec) < 300:
                                            vehicle['connectionStatus'] = 'connected'
                                    except Exception:
                                        pass
                except Exception as e:
                    print(f'Redis enrichment error: {e}')

                # Add missing status fields to each vehicle (only if not present)
                for vehicle in vehicles:
                    if 'connectionStatus' not in vehicle:
                        vehicle['connectionStatus'] = 'disconnected'
                    if 'activityStatus' not in vehicle:
                        vehicle['activityStatus'] = 'inactive'
                    if 'lastConnected' not in vehicle:
                        vehicle['lastConnected'] = None
                    if 'lastDisconnected' not in vehicle:
                        vehicle['lastDisconnected'] = None

                # ── Classification projection (spec 2026-08-29 § D2, D7) ──────
                # Batch fleet reads: one _load_fleet per unique fleetId in the page.
                # Spec: 2026-08-29-cms-vehicle-classification § D2
                _fleet_cache: dict = {}
                for vehicle in vehicles:
                    try:
                        vehicle['classification'] = _classify_vehicle(vehicle)
                    except UnclassifiableVehicleError as _ce:
                        print(f"🚗 Classification failed for vehicle "
                              f"{vehicle.get('vehicleId')}: {_ce}")
                        vehicle['classification'] = 'unknown'
                
                print(f'🚗 Vehicles API - Collected {len(all_filtered_vehicles)} filtered vehicles, returning {len(vehicles)} for page {page}')
                
                reverse_sort = sort_order == 'desc'
                if sort_by == 'createdAt':
                    vehicles.sort(key=lambda x: str(x.get('createdAt', '')), reverse=reverse_sort)
                elif sort_by == 'vehicleId':
                    vehicles.sort(key=lambda x: str(x.get('vehicleId', '')), reverse=reverse_sort)
                
                has_next_page = 'LastEvaluatedKey' in response
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'vehicles': vehicles,
                        'total': total_count,
                        'page': page,
                        'limit': limit,
                        'totalPages': total_pages,
                        'hasNextPage': has_next_page,
                        'hasPrevPage': page > 1
                    }, default=decimal_default)
                }
            except Exception as e:
                print(f"Error fetching vehicles: {str(e)}")
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch vehicles: {str(e)}'})
                }
        
        # Handle dashboard widgets data endpoint
        if path == '/api/v1/dashboard/widgets' and method == 'GET':
            try:
                from decimal import Decimal
                def _dec(obj):
                    from decimal import Decimal  # def-local: see _sh_decimal_default
                    if isinstance(obj, Decimal): return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError

                # Check cache first (5 min TTL)
                cache_table = dynamodb.Table(os.environ.get('DASHBOARD_METRICS_CACHE_TABLE'))
                try:
                    cache_resp = cache_table.get_item(Key={'metricKey': 'dashboard_widgets_v1'})
                    if 'Item' in cache_resp:
                        cached_ts = int(cache_resp['Item'].get('timestamp', 0))
                        if time.time() - cached_ts < 300:  # 5 min
                            return {
                                'statusCode': 200,
                                'headers': cors_headers,
                                'body': cache_resp['Item']['data']
                            }
                except Exception:
                    pass

                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                trips_table = dynamodb.Table(os.environ.get('TRIPS_TABLE_NAME'))
                safety_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                maint_table = dynamodb.Table(os.environ.get('MAINTENANCE_ALERTS_TABLE_NAME'))

                # Vehicle Health — count by maintenance status
                v_resp = vehicles_table.scan(ProjectionExpression='vehicleId, #s, fleetId',
                                             ExpressionAttributeNames={'#s': 'status'})
                vehicles = v_resp.get('Items', [])
                total_v = len(vehicles)

                # Count maintenance alerts per vehicle to determine health
                maint_resp = maint_table.scan(ProjectionExpression='vehicleId, severity, #s',
                                              ExpressionAttributeNames={'#s': 'status'})
                maint_items = maint_resp.get('Items', [])
                vehicle_maint = {}
                for m in maint_items:
                    vid = m.get('vehicleId', '')
                    sev = str(m.get('severity', '')).upper()
                    if vid not in vehicle_maint:
                        vehicle_maint[vid] = {'CRITICAL': 0, 'HIGH': 0, 'MEDIUM': 0, 'LOW': 0}
                    vehicle_maint[vid][sev] = vehicle_maint[vid].get(sev, 0) + 1

                health_counts = {'Up to Date': 0, 'Action Soon': 0, 'Action Now': 0, 'Overdue': 0}
                for v in vehicles:
                    vid = v.get('vehicleId', '')
                    mc = vehicle_maint.get(vid, {})
                    if mc.get('CRITICAL', 0) > 0:
                        health_counts['Overdue'] += 1
                    elif mc.get('HIGH', 0) > 0:
                        health_counts['Action Now'] += 1
                    elif mc.get('MEDIUM', 0) > 0 or mc.get('LOW', 0) > 0:
                        health_counts['Action Soon'] += 1
                    else:
                        health_counts['Up to Date'] += 1

                # Driver Scores — bucket from trips
                t_resp = trips_table.scan(ProjectionExpression='driverScore')
                all_items = t_resp.get('Items', [])
                while 'LastEvaluatedKey' in t_resp:
                    t_resp = trips_table.scan(ProjectionExpression='driverScore',
                                              ExclusiveStartKey=t_resp['LastEvaluatedKey'])
                    all_items.extend(t_resp.get('Items', []))

                score_buckets = {'Excellent': 0, 'Average': 0, 'Below Average': 0, 'Needs Improvement': 0, 'Risky': 0}
                for t in all_items:
                    ds = float(t.get('driverScore', 0))
                    if ds >= 95: score_buckets['Excellent'] += 1
                    elif ds >= 85: score_buckets['Average'] += 1
                    elif ds >= 75: score_buckets['Below Average'] += 1
                    elif ds >= 60: score_buckets['Needs Improvement'] += 1
                    elif ds > 0: score_buckets['Risky'] += 1

                # Braking Events — count safety events by day (last 14 days)
                cutoff_14d = int(time.time()) - 14 * 86400
                s_resp = safety_table.scan(
                    FilterExpression='#ts >= :t',
                    ExpressionAttributeNames={'#ts': 'timestamp'},
                    ExpressionAttributeValues={':t': cutoff_14d},
                    ProjectionExpression='#ts, eventType')
                safety_items = s_resp.get('Items', [])
                while 'LastEvaluatedKey' in s_resp:
                    s_resp = safety_table.scan(
                        FilterExpression='#ts >= :t',
                        ExpressionAttributeNames={'#ts': 'timestamp'},
                        ExpressionAttributeValues={':t': cutoff_14d},
                        ProjectionExpression='#ts, eventType',
                        ExclusiveStartKey=s_resp['LastEvaluatedKey'])
                    safety_items.extend(s_resp.get('Items', []))

                from datetime import datetime as dt
                braking_by_day = {}
                for s in safety_items:
                    ts = int(s.get('timestamp', 0))
                    if ts > 1e12: ts = ts // 1000
                    day = dt.fromtimestamp(ts).strftime('%Y-%m-%d')
                    braking_by_day[day] = braking_by_day.get(day, 0) + 1

                braking_series = []
                for i in range(14):
                    d = dt.fromtimestamp(time.time() - (13 - i) * 86400).strftime('%Y-%m-%d')
                    braking_series.append({'date': d, 'numEvents': braking_by_day.get(d, 0)})

                # Distance Driven — miles per fleet from trips
                dist_by_fleet = {}
                for t in all_items:
                    # We don't have fleetId on trips, use total
                    pass
                # Simpler: total distance per day (last 14 days)
                dist_resp = trips_table.scan(ProjectionExpression='totalDistance, #ts',
                                             ExpressionAttributeNames={'#ts': 'timestamp'})
                dist_items = dist_resp.get('Items', [])
                dist_by_day = {}
                for t in dist_items:
                    ts = int(t.get('timestamp', 0))
                    if ts > 1e12: ts = ts // 1000
                    if ts < cutoff_14d: continue
                    day = dt.fromtimestamp(ts).strftime('%Y-%m-%d')
                    dist_by_day[day] = dist_by_day.get(day, 0) + float(t.get('totalDistance', 0))

                distance_series = []
                for i in range(14):
                    d = dt.fromtimestamp(time.time() - (13 - i) * 86400).strftime('%Y-%m-%d')
                    distance_series.append({'date': d, 'miles': round(dist_by_day.get(d, 0), 1)})

                # Utilization — active vs total vehicles
                active_count = health_counts['Up to Date'] + health_counts['Action Soon']
                utilization = [
                    {'title': 'Active', 'value': active_count},
                    {'title': 'In Service', 'value': health_counts['Action Now']},
                    {'title': 'Out of Service', 'value': health_counts['Overdue']},
                ]

                result_body = json.dumps({
                        'vehicleHealth': [{'title': k, 'value': v} for k, v in health_counts.items() if v > 0],
                        'driverScores': [{'title': k, 'value': v} for k, v in score_buckets.items() if v > 0],
                        'brakingEvents': braking_series,
                        'distanceDriven': distance_series,
                        'utilization': utilization,
                        'totalVehicles': total_v,
                    }, default=_dec)

                # Cache result for 5 minutes
                try:
                    cache_table.put_item(Item={
                        'metricKey': 'dashboard_widgets_v1',
                        'data': result_body,
                        'timestamp': int(time.time()),
                    })
                except Exception:
                    pass

                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': result_body
                }
            except Exception as e:
                print(f"Dashboard widgets error: {e}")
                return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

        # Handle dashboard fleet-comparison endpoint
        if path == '/api/v1/dashboard/fleet-comparison' and method == 'GET':
            try:
                cache_table = dynamodb.Table(os.environ.get('DASHBOARD_METRICS_CACHE_TABLE'))
                
                # Try to get cached data first
                try:
                    cache_response = cache_table.get_item(
                        Key={'metricKey': 'fleet_comparison_v2'}
                    )
                    
                    if 'Item' in cache_response:
                        cached_data = json.loads(cache_response['Item']['data'])
                        # Add lastUpdated timestamp
                        cached_data['lastUpdated'] = int(cache_response['Item'].get('timestamp', time.time()))
                        
                        return {
                            'statusCode': 200,
                            'headers': cors_headers,
                            'body': json.dumps(cached_data)
                        }
                except Exception as cache_error:
                    print(f"Cache lookup failed: {cache_error}")
                
                # Fallback to basic fleet data if cache miss
                fleets_table = dynamodb.Table(os.environ.get('FLEETS_TABLE_NAME'))
                fleets_response = fleets_table.scan()
                fleets = fleets_response['Items']
                
                # Create real fleet performance data from DDB
                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                trips_table = dynamodb.Table(os.environ.get('TRIPS_TABLE_NAME'))
                safety_events_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                maint_table = dynamodb.Table(os.environ.get('MAINTENANCE_ALERTS_TABLE_NAME'))

                fleet_performance = {}
                for fleet in fleets:
                    fleet_id = fleet['fleetId']
                    # Real vehicle count
                    fv = vehicles_table.scan(
                        FilterExpression='fleetId = :f',
                        ExpressionAttributeValues={':f': fleet_id},
                        Select='COUNT')
                    vehicle_count = fv['Count']
                    if vehicle_count == 0:
                        continue

                    # Get vehicle IDs for this fleet
                    fv_ids = vehicles_table.scan(
                        FilterExpression='fleetId = :f',
                        ExpressionAttributeValues={':f': fleet_id},
                        ProjectionExpression='vehicleId')
                    vids = [v['vehicleId'] for v in fv_ids.get('Items', [])]

                    # Count trips and miles for fleet vehicles, collect driver scores
                    fleet_trips = 0
                    fleet_miles = 0
                    driver_scores = []
                    for vid in vids:
                        try:
                            tr = trips_table.query(
                                IndexName='vehicleId-index',
                                KeyConditionExpression='vehicleId = :v',
                                ExpressionAttributeValues={':v': vid},
                                ProjectionExpression='totalDistance, driverScore')
                            for t in tr.get('Items', []):
                                fleet_trips += 1
                                fleet_miles += float(t.get('totalDistance', 0))
                                ds = t.get('driverScore')
                                if ds is not None:
                                    driver_scores.append(float(ds))
                        except Exception:
                            pass

                    # Count safety events for fleet vehicles
                    fleet_safety = 0
                    for vid in vids[:10]:  # Limit to avoid timeout
                        try:
                            sr = safety_events_table.scan(
                                FilterExpression='vehicleId = :v',
                                ExpressionAttributeValues={':v': vid},
                                Select='COUNT')
                            fleet_safety += sr['Count']
                        except Exception:
                            pass

                    # Count maintenance alerts for fleet vehicles
                    fleet_maint = 0
                    for vid in vids[:10]:
                        try:
                            mr = maint_table.scan(
                                FilterExpression='vehicleId = :v',
                                ExpressionAttributeValues={':v': vid},
                                Select='COUNT')
                            fleet_maint += mr['Count']
                        except Exception:
                            pass

                    miles_per_vehicle = round(fleet_miles / max(vehicle_count, 1), 1)
                    safety_per_1k = round(fleet_safety / max(fleet_miles / 1000, 1), 2) if fleet_miles > 0 else 0
                    maint_per_vehicle = round(fleet_maint / max(vehicle_count, 1), 2)

                    avg_driver_score = round(sum(driver_scores) / len(driver_scores), 1) if driver_scores else 0

                    # Safety score: starts at 100, penalizes proportional to events per 1000mi.
                    # Tuned so 0 events = 100, 5 events/1000mi = 80, 10 events/1000mi = 60,
                    # capped at 0. Previous formula (safety_per_1k * 10) was too harsh for
                    # our synthetic data which generates ~15 events per 1000 miles.
                    fleet_performance[fleet_id] = {
                        'fleetId': fleet_id,
                        'name': fleet.get('name', fleet_id),
                        'totalVehicles': vehicle_count,
                        'activeVehicles': vehicle_count,
                        'totalTrips': fleet_trips,
                        'totalMiles': round(fleet_miles, 1),
                        'avgDriverScore': avg_driver_score,
                        'safetyScore': round(max(100 - safety_per_1k * 4, 0), 1),
                        'safetyEventsTotal': fleet_safety,
                        'safetyEventsPer1000Miles': safety_per_1k,
                        'maintenanceAlertsTotal': fleet_maint,
                        'maintenanceAlertsPerVehicle': maint_per_vehicle,
                        'utilizationMilesPerVehicle': miles_per_vehicle,
                    }

                # Build rankings from real data
                perf_list = list(fleet_performance.values())
                safest = sorted(perf_list, key=lambda x: x['safetyScore'], reverse=True)
                best_drivers = sorted([f for f in perf_list if f['avgDriverScore'] > 0],
                                      key=lambda x: x['avgDriverScore'], reverse=True)
                most_miles = sorted(perf_list, key=lambda x: x['utilizationMilesPerVehicle'], reverse=True)
                least_maint = sorted(perf_list, key=lambda x: x['maintenanceAlertsPerVehicle'])
                
                fallback_data = {
                    'fleetPerformance': fleet_performance,
                    'rankings': {
                        'safestFleets': safest[:5],
                        'bestDriverScores': best_drivers[:5],
                        'mostEfficient': most_miles[:5],
                        'leastMaintenance': least_maint[:5],
                    },
                    'summary': {
                        'totalFleets': len(fleet_performance),
                        'totalVehicles': sum(f['totalVehicles'] for f in fleet_performance.values()),
                        'totalMiles': round(sum(f['totalMiles'] for f in fleet_performance.values()), 1),
                        'totalTrips': sum(f['totalTrips'] for f in fleet_performance.values()),
                        'avgSafetyScore': round(sum(f['safetyScore'] for f in perf_list) / max(len(perf_list), 1), 1),
                    },
                    'lastUpdated': int(time.time())
                }
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps(fallback_data, default=decimal_default)
                }
                
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch fleet comparison: {str(e)}'})
                }
        
        # Handle vehicle safety events endpoint
        if path.startswith('/api/v1/vehicles/') and path.endswith('/safety-events') and method == 'GET':
            vehicle_id = path.split('/')[-2]
            limit = min(int(query_params.get('limit', 20)), 100)
            page = int(query_params.get('page', 1))
            trip_id = query_params.get('tripId')  # Optional trip filter
            
            try:
                safety_events_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                
                # Use scan with filter for vehicleId (no GSI available)
                scan_kwargs = {
                    'FilterExpression': 'vehicleId = :vehicle_id',
                    'ExpressionAttributeValues': {':vehicle_id': vehicle_id}
                }
                
                # Add trip filter if specified
                if trip_id:
                    scan_kwargs['FilterExpression'] += ' AND tripId = :trip_id'
                    scan_kwargs['ExpressionAttributeValues'][':trip_id'] = trip_id
                
                # Get all items first for pagination
                all_items = []
                response = safety_events_table.scan(**scan_kwargs)
                all_items.extend(response.get('Items', []))
                
                while 'LastEvaluatedKey' in response:
                    scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                    response = safety_events_table.scan(**scan_kwargs)
                    all_items.extend(response.get('Items', []))
                
                # Sort by timestamp (newest first)
                all_items.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
                
                # Apply pagination
                total_items = len(all_items)
                start_index = (page - 1) * limit
                end_index = start_index + limit
                paginated_items = all_items[start_index:end_index]
                
                # Convert to API format
                events = []
                for item in paginated_items:
                    # Fix timestamp - convert from milliseconds to seconds if needed
                    timestamp = item.get('timestamp')
                    if timestamp and isinstance(timestamp, (int, float)) and timestamp > 9999999999:
                        timestamp = int(timestamp / 1000)
                    
                    event = {
                        'eventId': item.get('eventId'),
                        'tripId': item.get('tripId'),
                        'vehicleId': item.get('vehicleId'),
                        'driverId': item.get('driverId'),
                        'eventType': item.get('eventType', 'unknown'),
                        'severity': item.get('severity', 'medium'),
                        'timestamp': timestamp,
                        'detection': item.get('detection', 'cloud'),
                        'campaignSyncId': item.get('campaignSyncId'),
                        'location': {
                            'latitude': float(item.get('latitude', 0)) if item.get('latitude') else float(item.get('lat', 0)),
                            'longitude': float(item.get('longitude', 0)) if item.get('longitude') else float(item.get('lng', 0))
                        },
                        'description': item.get('description', item.get('message', '')),
                        'speed': item.get('speed', 0),
                        'gForce': item.get('gForce', 0)
                    }
                    events.append(event)
                
                # Define decimal handler
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'events': events,
                        'pagination': {
                            'page': page,
                            'limit': limit,
                            'total': total_items,
                            'totalPages': (total_items + limit - 1) // limit
                        }
                    }, default=decimal_default)
                }
                
            except Exception as e:
                print(f"Error fetching vehicle safety events: {e}")
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch safety events: {str(e)}'})
                }
        
        # Handle vehicle safety alerts endpoint
        if path.startswith('/api/v1/vehicles/') and path.endswith('/safety-alerts') and method == 'GET':
            vehicle_id = path.split('/')[-2]
            limit = min(int(query_params.get('limit', 20)), 100)
            page = int(query_params.get('page', 1))
            trip_id = query_params.get('tripId')  # Optional trip filter
            
            try:
                safety_events_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                
                # Use query instead of scan if GSI exists, otherwise optimized scan
                try:
                    # Try to use GSI for vehicleId (much faster)
                    query_kwargs = {
                        'IndexName': 'vehicleId-timestamp-index',  # Assuming this GSI exists
                        'KeyConditionExpression': 'vehicleId = :vehicle_id',
                        'ExpressionAttributeValues': {':vehicle_id': vehicle_id},
                        'ScanIndexForward': False,  # Latest first
                        'Limit': limit
                    }
                    
                    # Add trip filter if specified
                    if trip_id:
                        query_kwargs['FilterExpression'] = 'tripId = :trip_id'
                        query_kwargs['ExpressionAttributeValues'][':trip_id'] = trip_id
                    
                    # Handle pagination with GSI
                    if page > 1:
                        # Skip to correct page
                        skip_count = (page - 1) * limit
                        temp_limit = skip_count + limit
                        query_kwargs['Limit'] = temp_limit
                        
                        response = safety_events_table.query(**query_kwargs)
                        alerts = response['Items'][skip_count:]
                    else:
                        response = safety_events_table.query(**query_kwargs)
                        alerts = response['Items']
                    
                    # Get approximate count (faster than exact count)
                    count_response = safety_events_table.query(
                        IndexName='vehicleId-timestamp-index',
                        KeyConditionExpression='vehicleId = :vehicle_id',
                        ExpressionAttributeValues={':vehicle_id': vehicle_id},
                        Select='COUNT'
                    )
                    total_count = count_response['Count']
                    
                    # Transform alerts to fix data issues (GSI path)
                    transformed_alerts = []
                    for alert in alerts:
                        # Fix timestamp - convert from milliseconds to seconds if needed
                        timestamp = alert.get('timestamp')
                        if timestamp and isinstance(timestamp, (int, float)) and timestamp > 9999999999:
                            timestamp = int(timestamp / 1000)
                        
                        transformed_alert = {
                            'eventId': alert.get('eventId'),
                            'tripId': alert.get('tripId'),
                            'vehicleId': alert.get('vehicleId'),  # Ensure this is vehicleId, not VIN
                            'timestamp': timestamp,
                            'eventType': alert.get('eventType'),
                            'message': alert.get('message'),
                            'speed': alert.get('speed'),
                            'lat': alert.get('lat'),
                            'lng': alert.get('lng'),  # Include longitude
                            'longitude': alert.get('lng'),  # Also include as longitude for compatibility
                            'severity': alert.get('severity'),
                            'driverId': alert.get('driverId')
                        }
                        
                        # Remove None values
                        transformed_alert = {k: v for k, v in transformed_alert.items() if v is not None}
                        transformed_alerts.append(transformed_alert)

                    def decimal_default(obj):
                        from decimal import Decimal
                        if isinstance(obj, Decimal):
                            return int(obj) if obj % 1 == 0 else float(obj)
                        raise TypeError
                    
                    return {
                        'statusCode': 200,
                        'headers': cors_headers,
                        'body': json.dumps({
                            'alerts': transformed_alerts,
                            'total': total_count,
                            'page': page,
                            'limit': limit,
                            'vehicleId': vehicle_id
                        }, default=decimal_default)
                    }
                    
                except Exception as gsi_error:
                    # Fallback to optimized scan if GSI doesn't exist
                    print(f"GSI not available, using optimized scan: {gsi_error}")
                    
                    scan_kwargs = {
                        'FilterExpression': 'vehicleId = :vehicle_id',
                        'ExpressionAttributeValues': {':vehicle_id': vehicle_id},
                        'Limit': limit * 2,  # Reduced from 50x
                        'ProjectionExpression': 'eventId, tripId, vehicleId, #ts, eventType, message, speed, lat, lng, severity, driverId',
                        'ExpressionAttributeNames': {'#ts': 'timestamp'}
                    }
                    
                    # Add trip filter if specified
                    if trip_id:
                        scan_kwargs['FilterExpression'] = scan_kwargs['FilterExpression'] + ' AND tripId = :trip_id'
                        scan_kwargs['ExpressionAttributeValues'][':trip_id'] = trip_id
                    
                    # Collect only what we need
                    alerts = []
                    scanned_pages = 0
                    max_scan_pages = 5  # Limit scan operations
                    
                    while len(alerts) < limit and scanned_pages < max_scan_pages:
                        response = safety_events_table.scan(**scan_kwargs)
                        alerts.extend(response['Items'])
                        scanned_pages += 1
                        
                        if 'LastEvaluatedKey' not in response:
                            break
                        scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                    
                    alerts = alerts[:limit]
                    
                    # Estimate total count instead of exact scan
                    total_count = len(alerts) + (limit * (page - 1)) if len(alerts) == limit else len(alerts) + (limit * (page - 1))

                # Transform alerts to fix data issues
                transformed_alerts = []
                for alert in alerts:
                    # Fix timestamp - convert from milliseconds to seconds if needed
                    timestamp = alert.get('timestamp')
                    if timestamp and isinstance(timestamp, (int, float)) and timestamp > 9999999999:
                        timestamp = int(timestamp / 1000)
                    
                    transformed_alert = {
                        'eventId': alert.get('eventId'),
                        'tripId': alert.get('tripId'),
                        'vehicleId': alert.get('vehicleId'),  # Ensure this is vehicleId, not VIN
                        'timestamp': timestamp,
                        'eventType': alert.get('eventType'),
                        'message': alert.get('message'),
                        'speed': alert.get('speed'),
                        'lat': alert.get('lat'),
                        'lng': alert.get('lng'),  # Include longitude
                        'longitude': alert.get('lng'),  # Also include as longitude for compatibility
                        'severity': alert.get('severity'),
                        'driverId': alert.get('driverId')
                    }
                    
                    # Remove None values
                    transformed_alert = {k: v for k, v in transformed_alert.items() if v is not None}
                    transformed_alerts.append(transformed_alert)

                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'alerts': transformed_alerts,
                        'total': total_count,
                        'page': page,
                        'limit': limit,
                        'vehicleId': vehicle_id
                    }, default=decimal_default)
                }
                
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch safety alerts: {str(e)}'})
                }
        
        # Handle vehicle maintenance alerts endpoint
        if path.startswith('/api/v1/vehicles/') and path.endswith('/maintenance-alerts') and method == 'GET':
            vehicle_id = path.split('/')[-2]
            limit = min(int(query_params.get('limit', 20)), 100)
            page = int(query_params.get('page', 1))
            
            try:
                maintenance_alerts_table = dynamodb.Table(os.environ.get('MAINTENANCE_ALERTS_TABLE_NAME'))
                
                # Get maintenance alerts for this vehicle
                scan_kwargs = {
                    'FilterExpression': 'vehicleId = :vehicle_id',
                    'ExpressionAttributeValues': {
                        ':vehicle_id': vehicle_id
                    },
                    'Limit': limit * 50
                }
                
                # Skip to correct page
                current_page = 1
                while current_page < page:
                    response = maintenance_alerts_table.scan(**scan_kwargs)
                    if 'LastEvaluatedKey' not in response:
                        break
                    scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                    current_page += 1
                
                # Collect records until we have enough
                alerts = []
                while len(alerts) < limit:
                    response = maintenance_alerts_table.scan(**scan_kwargs)
                    page_alerts = response['Items']
                    alerts.extend(page_alerts)
                    
                    if 'LastEvaluatedKey' not in response:
                        break
                    scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                
                alerts = alerts[:limit]
                
                # Get total count
                count_response = maintenance_alerts_table.scan(
                    FilterExpression='vehicleId = :vehicle_id',
                    ExpressionAttributeValues={
                        ':vehicle_id': vehicle_id
                    },
                    Select='COUNT'
                )
                total_count = count_response['Count']
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'alerts': alerts,
                        'total': total_count,
                        'page': page,
                        'limit': limit,
                        'vehicleId': vehicle_id
                    }, default=decimal_default)
                }
                
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch maintenance alerts: {str(e)}'})
                }
        
        # Handle vehicle DTC history endpoint
        # Returns rows from cms-<stage>-storage-dtc-history for the given vehicle.
        # Uses Query on the (vehicleId, timestamp) key schema — no scan needed.
        # Sorted newest-first via ScanIndexForward=False. Results include rows
        # from all three DTC producers (threshold-based MaintenanceProcessor,
        # authentic UDS-DTC via FWTelemetryProcessor, and force_event.py), which
        # consumers can disambiguate via the `source` attribute.
        if path.startswith('/api/v1/vehicles/') and path.endswith('/dtcs') and method == 'GET':
            vehicle_id = path.split('/')[-2]
            limit = min(int(query_params.get('limit', 50)), 200)
            status_filter = query_params.get('status')  # Optional: 'ACTIVE' | 'CLEARED' | None
            source_filter = query_params.get('source')  # Optional: 'fwe-uds-dtc' | 'flink-maintenance-processor' | etc.

            # Fleet-scope check — IDOR fix (issues/2026-08-10-dtc-endpoint-idor).
            # Resolve the vehicle's owning fleet and reject callers not scoped to it.
            # Pattern matches `:2325` (driver trips) and `:2642` (driver PUT).
            # Admins (has_unscoped_access=True) bypass; everyone else must own the fleet.
            if not has_unscoped_access:
                _vt_dtc_get = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                _vr_dtc_get = _vt_dtc_get.get_item(Key={'vehicleId': vehicle_id})
                if 'Item' not in _vr_dtc_get:
                    return {'statusCode': 404, 'headers': cors_headers,
                            'body': json.dumps({'error': f'Vehicle {vehicle_id} not found'})}
                _fleet_id_dtc_get = _vr_dtc_get['Item'].get('fleetId')
                if not _fleet_id_dtc_get:
                    return {'statusCode': 403, 'headers': cors_headers,
                            'body': json.dumps({'error': 'Access denied to this fleet'})}
                denied = _check_fleet_access(_fleet_id_dtc_get)
                if denied: return denied

            try:
                stage = os.environ.get('DEPLOYMENT_STAGE', 'prod')
                dtc_history_table = dynamodb.Table(
                    os.environ.get('DTC_HISTORY_TABLE_NAME', f'cms-{stage}-storage-dtc-history')
                )

                query_kwargs = {
                    'KeyConditionExpression': 'vehicleId = :vid',
                    'ExpressionAttributeValues': {':vid': vehicle_id},
                    'ScanIndexForward': False,  # newest first
                    'Limit': limit,
                }

                # Optional server-side filters — applied as FilterExpression so
                # that pagination remains consistent with the client-visible result
                # ordering. (Filters evaluate after items are read from the table,
                # so the response Limit may include fewer matching rows than
                # requested on the first page; client is expected to iterate if
                # needed. Acceptable for the expected <100 DTCs per vehicle.)
                filter_parts = []
                if status_filter:
                    filter_parts.append('#s = :status')
                    query_kwargs['ExpressionAttributeValues'][':status'] = status_filter
                    query_kwargs.setdefault('ExpressionAttributeNames', {})['#s'] = 'status'
                if source_filter:
                    filter_parts.append('#src = :src')
                    query_kwargs['ExpressionAttributeValues'][':src'] = source_filter
                    query_kwargs.setdefault('ExpressionAttributeNames', {})['#src'] = 'source'
                if filter_parts:
                    query_kwargs['FilterExpression'] = ' AND '.join(filter_parts)

                response = dtc_history_table.query(**query_kwargs)
                dtcs = response.get('Items', [])

                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError

                # Normalize each row for the UI — converts timestamp to ISO string
                # for easy display, and tags rows by their source so the frontend
                # can render a "Source" badge without re-inferring.
                def _normalize(row):
                    ts = row.get('timestamp') or row.get('firstSeenAt')
                    if ts is not None:
                        # DDB stores as N (Decimal). May be seconds or millis — detect.
                        try:
                            ts_num = int(ts)
                            if ts_num > 9999999999:  # milliseconds
                                ts_iso = datetime.utcfromtimestamp(ts_num / 1000).isoformat() + 'Z'
                            else:  # seconds
                                ts_iso = datetime.utcfromtimestamp(ts_num).isoformat() + 'Z'
                            row['timestampIso'] = ts_iso
                        except (TypeError, ValueError):
                            pass
                    # Coerce numeric GSI dedup fields when present; pass-through absent (legacy rows).
                    if 'lastSeenAt' in row:
                        try:
                            row['lastSeenAt'] = int(row['lastSeenAt'])
                        except (TypeError, ValueError):
                            pass
                    if 'occurrenceCount' in row:
                        try:
                            row['occurrenceCount'] = int(row['occurrenceCount'])
                        except (TypeError, ValueError):
                            pass
                    return row

                dtcs = [_normalize(r) for r in dtcs]

                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'dtcs': dtcs,
                        'total': len(dtcs),
                        'limit': limit,
                        'vehicleId': vehicle_id,
                        'filters': {
                            'status': status_filter,
                            'source': source_filter,
                        },
                    }, default=decimal_default)
                }

            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch DTC history: {str(e)}'})
                }

        # GET /api/v1/vehicles/{vehicleId}/health — server-computed 0..100
        # health score + deduction breakdown. Moved into CMS 2026-09-18 from
        # CVX's vsa-staging-api-vehicle-context Lambda, whose IAM policy and
        # env vars pointed at a nonexistent cms-prod-* table, 502ing this
        # data for every vehicle. See
        # issues/2026-09-18-vsa-vehicle-context-points-at-nonexistent-cms-prod-table/
        # and modules/cms_ui/source/handlers/main_api/vehicle_health.py's own
        # docstring for the full rationale (this is now the single source of
        # truth for CMS UI; iOS's separate, unaffected copy is a filed
        # follow-on, not fixed by this route).
        if path.startswith('/api/v1/vehicles/') and path.endswith('/health') and method == 'GET':
            vehicle_id = path.split('/')[-2]

            # Same fleet-scope IDOR guard the /dtcs route above uses —
            # issues/2026-08-10-dtc-endpoint-idor's pattern, applied here
            # because this route reads the same class of per-vehicle data.
            if not has_unscoped_access:
                _vt_health_get = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                _vr_health_get = _vt_health_get.get_item(Key={'vehicleId': vehicle_id})
                if 'Item' not in _vr_health_get:
                    return {'statusCode': 404, 'headers': cors_headers,
                            'body': json.dumps({'error': f'Vehicle {vehicle_id} not found'})}
                _fleet_id_health_get = _vr_health_get['Item'].get('fleetId')
                if not _fleet_id_health_get:
                    return {'statusCode': 403, 'headers': cors_headers,
                            'body': json.dumps({'error': 'Access denied to this fleet'})}
                denied = _check_fleet_access(_fleet_id_health_get)
                if denied: return denied

            try:
                from vehicle_health import (
                    compute_health_score,
                    load_active_dtcs,
                    load_first_scheduled_service,
                )

                stage = os.environ.get('DEPLOYMENT_STAGE', 'prod')
                vehicles_table_health = dynamodb.Table(
                    os.environ.get('VEHICLES_TABLE_NAME', f'cms-{stage}-storage-vehicles')
                )
                vehicle_resp_health = vehicles_table_health.get_item(Key={'vehicleId': vehicle_id})
                vehicle_item_health = vehicle_resp_health.get('Item')
                if not vehicle_item_health:
                    return {'statusCode': 404, 'headers': cors_headers,
                            'body': json.dumps({'error': f'Vehicle {vehicle_id} not found'})}

                dtc_history_table_name = os.environ.get(
                    'DTC_HISTORY_TABLE_NAME', f'cms-{stage}-storage-dtc-history'
                )
                service_history_table_name = os.environ.get(
                    'SERVICE_HISTORY_TABLE_NAME', f'cms-{stage}-storage-service-history'
                )
                active_dtcs_health = load_active_dtcs(
                    dynamodb, dtc_history_table_name, vehicle_id
                )
                scheduled_first_health = load_first_scheduled_service(
                    dynamodb, service_history_table_name, vehicle_id
                )
                # Single source of truth for connectedness (see
                # connection_status.py's own docstring) — direct call, no
                # inter-Lambda invoke. CVX's own copy of this endpoint
                # invoked a separate api-vehicle-live-state Lambda because
                # CVX had no direct Redis access; main_api already does.
                connection_status_health = resolve_connection_status(vehicle_item_health)

                health_result = compute_health_score(
                    active_dtcs=active_dtcs_health,
                    scheduled_service_first_row=scheduled_first_health,
                    connection_status=connection_status_health,
                    battery_soh=vehicle_item_health.get('batterySoh'),
                    fuel_type=vehicle_item_health.get('fuelType'),
                )

                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError

                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'vehicleId': vehicle_id,
                        'healthScore': health_result['score'],
                        'healthScoreBreakdown': health_result,
                        # VehicleHealthScoreWidget's KPI strip reads these
                        # two — activeDtcs for the "Active DTCs / Highest
                        # severity" tiles, vehicle.connectionStatus +
                        # vehicle.lastSeenAt for the "Connection" tile.
                        # Matches the shape CVX's endpoint returned, so no
                        # further frontend change was needed beyond
                        # repointing the base URL.
                        'activeDtcs': active_dtcs_health,
                        'vehicle': {
                            'connectionStatus': connection_status_health,
                            'lastSeenAt': vehicle_item_health.get('lastSeenAt'),
                        },
                    }, default=decimal_default)
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to compute vehicle health: {str(e)}'})
                }

        # PATCH /api/v1/vehicles/{vehicleId}/dtcs/{dtcId} — mark a DTC cleared.
        # Used by the "Mark Cleared" button on the Vehicle Detail DTCs tab
        # after service is complete. Sets status=CLEARED + clearedDate; does
        # NOT delete the row (we keep the history for audit).
        #
        # Request body is optional and may carry {relatedServiceId} to link
        # the cleared DTC back to the service-history row that resolved it.
        #
        # Primary key of dtc-history is (vehicleId, timestamp) — we only have
        # dtcId from the URL, so we query by vehicleId + filter by dtcId to
        # recover the timestamp before issuing the update.
        if path.startswith('/api/v1/vehicles/') and '/dtcs/' in path and method == 'PATCH':
            denied = _deny_viewer()
            if denied:
                return denied
            path_parts = path.split('/')
            # /api/v1/vehicles/{vehicleId}/dtcs/{dtcId}
            vehicle_id = path_parts[4]
            dtc_id = path_parts[6]

            # Fleet-scope check — IDOR fix (issues/2026-08-10-dtc-endpoint-idor).
            # _deny_viewer() above blocks the read-only role; this check blocks
            # callers scoped to a different fleet from mutating another fleet's DTCs.
            # Role and scope are independent guards — both are required.
            # Pattern matches `:2325` (driver trips) and `:2642` (driver PUT).
            if not has_unscoped_access:
                _vt_dtc_patch = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                _vr_dtc_patch = _vt_dtc_patch.get_item(Key={'vehicleId': vehicle_id})
                if 'Item' not in _vr_dtc_patch:
                    return {'statusCode': 404, 'headers': cors_headers,
                            'body': json.dumps({'error': f'Vehicle {vehicle_id} not found'})}
                _fleet_id_dtc_patch = _vr_dtc_patch['Item'].get('fleetId')
                if not _fleet_id_dtc_patch:
                    return {'statusCode': 403, 'headers': cors_headers,
                            'body': json.dumps({'error': 'Access denied to this fleet'})}
                denied = _check_fleet_access(_fleet_id_dtc_patch)
                if denied: return denied

            try:
                body = json.loads(event.get('body', '{}') or '{}')
                related_service_id = body.get('relatedServiceId', '')

                stage = os.environ.get('DEPLOYMENT_STAGE', 'prod')
                dtc_table = dynamodb.Table(
                    os.environ.get('DTC_HISTORY_TABLE_NAME', f'cms-{stage}-storage-dtc-history')
                )
                # Look up the row by vehicleId + dtcId to recover timestamp.
                # Paginated newest-first: our DTC is almost certainly recent,
                # but VEH-0025 has hundreds of historical rows and
                # FilterExpression applies AFTER the page-size Limit cut. A
                # too-small Limit would miss the match. See the equivalent
                # helper in _approve_dtc_action_followups for the same
                # pattern.
                items = []
                kwargs = {
                    'KeyConditionExpression': 'vehicleId = :v',
                    'FilterExpression': 'dtcId = :d',
                    'ExpressionAttributeValues': {':v': vehicle_id, ':d': dtc_id},
                    'ScanIndexForward': False,  # newest first
                    'Limit': 500,
                }
                resp = dtc_table.query(**kwargs)
                items.extend(resp.get('Items', []))
                for _ in range(5):
                    if items or 'LastEvaluatedKey' not in resp:
                        break
                    kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
                    resp = dtc_table.query(**kwargs)
                    items.extend(resp.get('Items', []))
                if not items:
                    return {'statusCode': 404, 'headers': cors_headers,
                            'body': json.dumps({'error': f'DTC {dtc_id} not found on {vehicle_id}'})}
                # Most-recent match — defensive against duplicate rows.
                latest = max(items, key=lambda x: int(x.get('timestamp', 0)))

                if latest.get('status') == 'CLEARED':
                    # Idempotency: already cleared. Return 200 so the UI
                    # doesn't surface an error on a re-click.
                    return {'statusCode': 200, 'headers': cors_headers,
                            'body': json.dumps({
                                'dtcId': dtc_id,
                                'vehicleId': vehicle_id,
                                'status': 'CLEARED',
                                'idempotent': True,
                            })}

                now_iso = datetime.now(timezone.utc).isoformat()
                dtc_table.update_item(
                    Key={
                        'vehicleId': latest['vehicleId'],
                        'timestamp': latest['timestamp'],
                    },
                    UpdateExpression='SET #s = :s, clearedDate = :c, relatedServiceId = :r, clearedBy = :cb REMOVE activeCode',
                    ExpressionAttributeNames={'#s': 'status'},
                    ExpressionAttributeValues={
                        ':s': 'CLEARED',
                        ':c': now_iso,
                        ':r': related_service_id,
                        ':cb': user_email or 'operator',
                    },
                )
                return {'statusCode': 200, 'headers': cors_headers,
                        'body': json.dumps({
                            'dtcId': dtc_id,
                            'vehicleId': vehicle_id,
                            'status': 'CLEARED',
                            'clearedDate': now_iso,
                            'relatedServiceId': related_service_id,
                        })}
            except Exception as e:
                import traceback
                print(f"PATCH dtc failed: {e}\n{traceback.format_exc()}")
                return {'statusCode': 500, 'headers': cors_headers,
                        'body': json.dumps({'error': f'Failed to clear DTC: {str(e)}'})}

        # POST /api/v1/vehicles/{vehicleId}/dtcs/{dtcId}/schedule-service
        # Schedule a service appointment for an ACTIVE DTC without clearing it.
        # The DTC stays ACTIVE; only relatedServiceId is stamped on the row.
        # 403 viewer, 404 DTC not found, 409 already scheduled (idempotent).
        if (path.startswith('/api/v1/vehicles/') and '/dtcs/' in path
                and path.endswith('/schedule-service') and method == 'POST'):
            denied = _deny_viewer()
            if denied:
                return denied
            path_parts = path.split('/')
            # /api/v1/vehicles/{vehicleId}/dtcs/{dtcId}/schedule-service
            vehicle_id = path_parts[4]
            dtc_id = path_parts[6]

            # Fleet-scope check — IDOR fix (issues/2026-08-10-dtc-endpoint-idor).
            # schedule-service mutates DTCs and creates service records; scope is required.
            # Same fail-closed pattern as GET (:4837) and PATCH (:4962).
            if not has_unscoped_access:
                _vt_sched = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                _vr_sched = _vt_sched.get_item(Key={'vehicleId': vehicle_id})
                if 'Item' not in _vr_sched:
                    return {'statusCode': 404, 'headers': cors_headers,
                            'body': json.dumps({'error': f'Vehicle {vehicle_id} not found'})}
                _fleet_id_sched = _vr_sched['Item'].get('fleetId')
                if not _fleet_id_sched:
                    return {'statusCode': 403, 'headers': cors_headers,
                            'body': json.dumps({'error': 'Access denied to this fleet'})}
                denied = _check_fleet_access(_fleet_id_sched)
                if denied: return denied

            try:
                body = json.loads(event.get('body', '{}') or '{}')
                notes = body.get('notes')

                stage = os.environ.get('DEPLOYMENT_STAGE', 'prod')
                dtc_table = dynamodb.Table(
                    os.environ.get('DTC_HISTORY_TABLE_NAME', f'cms-{stage}-storage-dtc-history')
                )
                # Recover the DTC row (vehicleId, timestamp) via vehicleId Query + dtcId filter.
                items = []
                kwargs = {
                    'KeyConditionExpression': 'vehicleId = :v',
                    'FilterExpression': 'dtcId = :d',
                    'ExpressionAttributeValues': {':v': vehicle_id, ':d': dtc_id},
                    'ScanIndexForward': False,
                    'Limit': 500,
                }
                resp = dtc_table.query(**kwargs)
                items.extend(resp.get('Items', []))
                for _ in range(5):
                    if items or 'LastEvaluatedKey' not in resp:
                        break
                    kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
                    resp = dtc_table.query(**kwargs)
                    items.extend(resp.get('Items', []))
                if not items:
                    return {'statusCode': 404, 'headers': cors_headers,
                            'body': json.dumps({'error': f'DTC {dtc_id} not found on {vehicle_id}'})}
                latest = max(items, key=lambda x: int(x.get('timestamp', 0)))

                # 409 idempotent — already has a service row
                existing_sid = latest.get('relatedServiceId', '')
                if existing_sid:
                    return {'statusCode': 409, 'headers': cors_headers,
                            'body': json.dumps({'serviceId': existing_sid,
                                                'vehicleId': vehicle_id,
                                                'dtcId': dtc_id,
                                                'relatedServiceId': existing_sid,
                                                'status': latest.get('status', 'ACTIVE'),
                                                'message': 'service already scheduled'})}

                now_iso = datetime.now(timezone.utc).isoformat()
                result = _create_service_for_dtc(
                    action_id=None,
                    vehicle_id=vehicle_id,
                    vin=latest.get('vin', ''),
                    dtc_id=dtc_id,
                    dtc_code=latest.get('code', ''),
                    system=latest.get('system', ''),
                    severity=latest.get('severity', 'HIGH'),
                    resolver=user_email or 'operator',
                    resolved_at_iso=now_iso,
                    dtc_human_desc=latest.get('description'),
                    notes=notes,
                )
                service_id = result['serviceId']

                # Stamp relatedServiceId on the DTC — status stays ACTIVE, activeCode stays.
                dtc_table.update_item(
                    Key={
                        'vehicleId': latest['vehicleId'],
                        'timestamp': latest['timestamp'],
                    },
                    UpdateExpression='SET relatedServiceId = :r',
                    ExpressionAttributeValues={':r': service_id},
                )

                return {'statusCode': 201, 'headers': cors_headers,
                        'body': json.dumps({
                            'serviceId': service_id,
                            'vehicleId': vehicle_id,
                            'dtcId': dtc_id,
                            'relatedServiceId': service_id,
                            'status': 'ACTIVE',
                        })}
            except Exception as e:
                import traceback
                print(f"POST schedule-service failed: {e}\n{traceback.format_exc()}")
                return {'statusCode': 500, 'headers': cors_headers,
                        'body': json.dumps({'error': f'Failed to schedule service: {str(e)}'})}

        # Handle individual trip detail endpoint
        if path.startswith('/api/v1/vehicles/') and '/trips/' in path and method == 'GET':
            path_parts = path.split('/')
            vehicle_id = path_parts[4]  # /api/v1/vehicles/{vehicleId}/trips/{tripId}
            trip_id = path_parts[6]
            
            try:
                trips_table = dynamodb.Table(os.environ.get('TRIPS_TABLE_NAME'))
                
                # Since trips table has composite key (tripId + timestamp), we need to query by tripId
                response = trips_table.query(
                    KeyConditionExpression='tripId = :trip_id',
                    ExpressionAttributeValues={':trip_id': trip_id}
                )
                
                if not response['Items']:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Trip {trip_id} not found'})
                    }
                
                # Get the first (and should be only) trip
                trip = response['Items'][0]
                
                # Convert timestamp fields to seconds if they're in milliseconds (for frontend compatibility)
                if 'startTime' in trip and trip['startTime']:
                    try:
                        start_timestamp = int(trip['startTime'])
                        # If timestamp is in milliseconds (13+ digits), convert to seconds
                        if start_timestamp > 9999999999:  # More than 10 digits = milliseconds
                            trip['startTime'] = start_timestamp // 1000
                    except (ValueError, TypeError):
                        pass
                
                if 'endTime' in trip and trip['endTime']:
                    try:
                        end_timestamp = int(trip['endTime'])
                        # If timestamp is in milliseconds (13+ digits), convert to seconds
                        if end_timestamp > 9999999999:  # More than 10 digits = milliseconds
                            trip['endTime'] = end_timestamp // 1000
                    except (ValueError, TypeError):
                        pass
                
                # Verify the trip belongs to the requested vehicle
                if trip.get('vehicleId') != vehicle_id:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Trip {trip_id} not found for vehicle {vehicle_id}'})
                    }
                
                # Resolve driver name from drivers table. Handles both
                # canonical DRV-NNNN IDs and legacy DRIVER-NN IDs (the
                # latter gets normalised by zero-padding the number).
                # Mirrors the logic in /api/v1/vehicles/{id}/trips list
                # endpoint so the two stay consistent. Added 2026-05-04.
                raw_did = trip.get('driverId')
                canon_did = _canonical_driver_id(raw_did)
                # Prefer the name already stored on the trip row. TripProcessor
                # writes driverName at trip creation, so re-deriving it is
                # unnecessary work that can only lose information — and did:
                # this row held 'Marcus Reyes' while the response carried the ID.
                stored_name = trip.get('driverName')
                if stored_name and not _looks_like_driver_id(stored_name):
                    trip['driverName'] = stored_name.strip()
                elif canon_did:
                    try:
                        drivers_table = dynamodb.Table(os.environ.get('DRIVERS_TABLE_NAME'))
                        dr = drivers_table.get_item(
                            Key={'driverId': canon_did},
                            ProjectionExpression='firstName, lastName',
                        )
                        if 'Item' in dr:
                            first = (dr['Item'].get('firstName') or '').strip()
                            last = (dr['Item'].get('lastName') or '').strip()
                            full = f'{first} {last}'.strip()
                            if full:
                                trip['driverName'] = full
                            else:
                                trip['driverName'] = canon_did
                        else:
                            # Driver ID resolves to a shape but no row — show raw ID.
                            trip['driverName'] = raw_did
                    except Exception as e:
                        print(f'vehicle-trip-detail: driver lookup failed for {canon_did}: {e}')
                        trip['driverName'] = raw_did
                elif raw_did:
                    # Non-canonical format we don't recognise — just show it.
                    trip['driverName'] = raw_did
                else:
                    # --- Fallback (2026-05-04): use currently-assigned
                    # driver for this vehicle when the trip has no
                    # driverId. TripProcessor historically didn't
                    # populate driverId on trip creation when the
                    # incoming telemetry lacked it, leaving us with
                    # many trips where the driver is "Unknown" on the
                    # UI despite there being exactly one assigned
                    # driver per our 1:1 invariant. Look up that driver
                    # and render their name. The write-time fix on
                    # TripProcessor removes the need for this fallback
                    # going forward, but it's kept so historical trips
                    # render correctly. ---
                    try:
                        drivers_table = dynamodb.Table(os.environ.get('DRIVERS_TABLE_NAME'))
                        # vehicle_id is already set from path_parts above.
                        # Full scan with pagination (DDB scan Limit is
                        # applied before FilterExpression — setting it
                        # small caused our filter to miss drivers that
                        # appear later in the table). Small table (75
                        # rows), 1:1 invariant → at most one match.
                        _assignment_items = []
                        _kwargs = {
                            'FilterExpression': 'assignedVehicleId = :v',
                            'ExpressionAttributeValues': {':v': vehicle_id},
                            'ProjectionExpression': 'driverId, firstName, lastName',
                        }
                        _resp = drivers_table.scan(**_kwargs)
                        _assignment_items.extend(_resp.get('Items', []))
                        for _ in range(10):  # defensive bound
                            if 'LastEvaluatedKey' not in _resp:
                                break
                            _kwargs['ExclusiveStartKey'] = _resp['LastEvaluatedKey']
                            _resp = drivers_table.scan(**_kwargs)
                            _assignment_items.extend(_resp.get('Items', []))
                        if _assignment_items:
                            _d = _assignment_items[0]
                            first = (_d.get('firstName') or '').strip()
                            last = (_d.get('lastName') or '').strip()
                            full = f'{first} {last}'.strip()
                            if full:
                                trip['driverName'] = full
                                trip['driverId'] = _d.get('driverId')
                                trip['driverSource'] = 'vehicle-assignment'
                            else:
                                trip['driverName'] = 'Unassigned'
                        else:
                            trip['driverName'] = 'Unassigned'
                    except Exception as e:
                        print(f'vehicle-trip-detail: assignment fallback failed for {vehicle_id}: {e}')
                        trip['driverName'] = 'Unassigned'
                
                # Get safety events for this trip (by time range, not tripId)
                try:
                    safety_events_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                    trip_start = int(trip.get('startTime', 0))
                    trip_end = int(trip.get('endTime', 0)) or int(time.time() * 1000)
                    # Ensure milliseconds — safety events use ms timestamps
                    if trip_start < 9999999999:
                        trip_start *= 1000
                    if trip_end < 9999999999:
                        trip_end *= 1000
                    vid = trip.get('vehicleId', vehicle_id)
                    
                    if trip_start and vid:
                        safety_response = safety_events_table.query(
                            IndexName='vehicleId-timestamp-index',
                            KeyConditionExpression='vehicleId = :v AND #ts BETWEEN :s AND :e',
                            ExpressionAttributeNames={'#ts': 'timestamp'},
                            ExpressionAttributeValues={
                                ':v': vid,
                                ':s': trip_start,
                                ':e': trip_end,
                            }
                        )
                    else:
                        safety_response = {'Items': []}
                    
                    safety_events = safety_response.get('Items', [])
                    
                    # Normalize coordinate fields and fix missing longitude
                    route = trip.get('route', [])
                    for event in safety_events:
                        # Ensure both lat/lng and latitude/longitude are available
                        if 'lat' in event and 'latitude' not in event:
                            event['latitude'] = event['lat']
                        if 'lng' in event and 'longitude' not in event:
                            event['longitude'] = event['lng']
                        if 'latitude' in event and 'lat' not in event:
                            event['lat'] = event['latitude']
                        if 'longitude' in event and 'lng' not in event:
                            event['lng'] = event['longitude']
                        
                        # If longitude is missing, try to find matching route point
                        if ('lng' not in event or not event.get('lng')) and ('longitude' not in event or not event.get('longitude')):
                            event_lat = float(event.get('lat') or event.get('latitude', 0))
                            if event_lat and route:
                                # Find closest route point by latitude
                                closest_point = None
                                min_diff = float('inf')
                                for point in route:
                                    if 'lat' in point and 'lng' in point:
                                        point_lat = float(point['lat'])
                                        diff = abs(point_lat - event_lat)
                                        if diff < min_diff:
                                            min_diff = diff
                                            closest_point = point
                                
                                if closest_point and min_diff < 0.001:  # Within ~100m
                                    event['lng'] = float(closest_point['lng'])
                                    event['longitude'] = float(closest_point['lng'])
                                    print(f"🚨 Fixed missing longitude for event at lat {event_lat}: lng={event['lng']}")
                    
                    trip['safetyEvents'] = safety_events
                except Exception as e:
                    print(f"Error fetching safety events for trip {trip_id}: {e}")
                    trip['safetyEvents'] = []
                
                # Maintenance events not included in trip detail — not location-dependent
                trip['maintenanceEvents'] = []

                # Energy consumed over the trip, from the trip's own telemetry.
                #
                # Unlike safety events, telemetry DOES carry tripId and has a
                # `tripId-timestamp-index` GSI, so this is a real foreign-key read
                # rather than a time-window correlation.
                #
                # Best-effort: a telemetry failure must not take down trip detail,
                # same posture as the safety-events block above. `energy` is None
                # when no signal reports consumption, and the UI renders that as
                # "Not reported" rather than as zero.
                trip['energy'] = None
                try:
                    telemetry_table = dynamodb.Table(os.environ.get('TELEMETRY_TABLE_NAME'))
                    tele_kwargs = {
                        'IndexName': 'tripId-timestamp-index',
                        'KeyConditionExpression': 'tripId = :tid',
                        'ExpressionAttributeValues': {':tid': trip_id},
                        'ProjectionExpression': (
                            '#ts, ev_soc, powertrainEVStateOfCharge, soc, fuelLevel'
                        ),
                        # `timestamp` is a DynamoDB reserved word.
                        'ExpressionAttributeNames': {'#ts': 'timestamp'},
                    }
                    tele_rows = []
                    # Page to exhaustion — a long trip exceeds the 1 MB page and a
                    # single-page read would silently truncate the series, making
                    # the delta WRONG rather than absent. The cap is a safety stop,
                    # not a design limit.
                    while True:
                        tele_resp = telemetry_table.query(**tele_kwargs)
                        tele_rows.extend(tele_resp.get('Items', []))
                        lek = tele_resp.get('LastEvaluatedKey')
                        if not lek or len(tele_rows) >= 20000:
                            break
                        tele_kwargs['ExclusiveStartKey'] = lek
                    tele_rows.sort(key=lambda r: int(r.get('timestamp') or 0))

                    # Pack capacity, needed only to express a state-of-charge delta
                    # in kWh. Looked up separately because the trip-detail handler
                    # does not otherwise read the vehicle row.
                    _capacity = None
                    try:
                        _v = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME')).get_item(
                            Key={'vehicleId': vehicle_id},
                            ProjectionExpression='batteryCapacityKwh',
                        )
                        _capacity = (_v.get('Item') or {}).get('batteryCapacityKwh')
                    except Exception as e:
                        print(f'trip-detail: battery capacity lookup failed for {vehicle_id}: {e}')

                    trip['energy'] = compute_trip_energy(
                        tele_rows, battery_capacity_kwh=_capacity,
                    )
                except Exception as e:
                    print(f'trip-detail: energy computation failed for {trip_id}: {e}')
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps(trip, default=decimal_default)
                }
                
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch trip: {str(e)}'})
                }
        
        # Handle vehicle trips endpoint using GSI for efficient querying
        if path.startswith('/api/v1/vehicles/') and path.endswith('/trips') and method == 'GET':
            vehicle_id = path.split('/')[-2]
            limit = int(query_params.get('limit', 20))
            page = int(query_params.get('page', 1))
            
            try:
                # Try to get cached total count first
                cache_table = dynamodb.Table(os.environ.get('DASHBOARD_METRICS_CACHE_TABLE'))
                cache_key = f'vehicle_trips_count_{vehicle_id}_v2'
                
                total_count = None
                try:
                    cache_response = cache_table.get_item(Key={'metricKey': cache_key})
                    if 'Item' in cache_response:
                        total_count = int(cache_response['Item']['totalCount'])
                except Exception:
                    pass
                
                trips_table = dynamodb.Table(os.environ.get('TRIPS_TABLE_NAME'))
                
                # If no cached count, use GSI to count efficiently
                if total_count is None:
                    total_count = 0
                    count_response = trips_table.query(
                        IndexName='vehicleId-index',
                        KeyConditionExpression='vehicleId = :vehicle_id',
                        ExpressionAttributeValues={':vehicle_id': vehicle_id},
                        Select='COUNT'
                    )
                    
                    total_count += count_response['Count']
                    
                    # Handle pagination for count if needed
                    while 'LastEvaluatedKey' in count_response:
                        count_response = trips_table.query(
                            IndexName='vehicleId-index',
                            KeyConditionExpression='vehicleId = :vehicle_id',
                            ExpressionAttributeValues={':vehicle_id': vehicle_id},
                            Select='COUNT',
                            ExclusiveStartKey=count_response['LastEvaluatedKey']
                        )
                        total_count += count_response['Count']
                    
                    print(f"DEBUG: Calculated total_count for {vehicle_id}: {total_count}")
                    
                    # Cache the result for 5 minutes (shorter cache for debugging)
                    try:
                        cache_table.put_item(
                            Item={
                                'metricKey': cache_key,
                                'totalCount': total_count,
                                'timestamp': int(time.time()),
                                'ttl': int(time.time()) + 300  # 5 minutes instead of 1 hour
                            }
                        )
                    except Exception as cache_error:
                        print(f"Cache error: {cache_error}")
                        pass
                
                # Fast path: use the sorted GSI and only fetch what we need.
                # Falls back to the old fetch-all-and-sort-in-memory path if the new
                # index isn't available (e.g. backfill in progress on a newly-added GSI).
                all_items = []
                page_items = []
                used_fast_path = False
                try:
                    # Skip over (page-1)*limit items by paginating at the DDB layer.
                    fast_kwargs = {
                        'IndexName': 'vehicleId-startTime-index',
                        'KeyConditionExpression': 'vehicleId = :vehicle_id',
                        'ExpressionAttributeValues': {':vehicle_id': vehicle_id},
                        'ScanIndexForward': False,  # newest first
                        'Limit': limit,
                    }
                    skip = (page - 1) * limit
                    if skip > 0:
                        # Walk forward to the requested page using COUNT-only queries,
                        # which are cheap (no item payloads).
                        skip_remaining = skip
                        last_key = None
                        while skip_remaining > 0:
                            skip_batch = min(skip_remaining, 1000)
                            skip_q = {
                                'IndexName': 'vehicleId-startTime-index',
                                'KeyConditionExpression': 'vehicleId = :vehicle_id',
                                'ExpressionAttributeValues': {':vehicle_id': vehicle_id},
                                'ScanIndexForward': False,
                                'Limit': skip_batch,
                                'Select': 'COUNT',
                            }
                            if last_key:
                                skip_q['ExclusiveStartKey'] = last_key
                            skip_r = trips_table.query(**skip_q)
                            last_key = skip_r.get('LastEvaluatedKey')
                            skip_remaining -= skip_r.get('Count', 0)
                            if not last_key:
                                break
                        if last_key:
                            fast_kwargs['ExclusiveStartKey'] = last_key
                    fast_resp = trips_table.query(**fast_kwargs)
                    page_items = fast_resp.get('Items', [])
                    used_fast_path = True
                except Exception as fast_err:
                    print(f"Fast path (vehicleId-startTime-index) failed; using fallback: {fast_err}")

                if not used_fast_path:
                    # Legacy fallback: fetch ALL trips for this vehicle, sort, slice.
                    query_kwargs = {
                        'IndexName': 'vehicleId-index',
                        'KeyConditionExpression': 'vehicleId = :vehicle_id',
                        'ExpressionAttributeValues': {':vehicle_id': vehicle_id},
                    }
                    response = trips_table.query(**query_kwargs)
                    all_items.extend(response.get('Items', []))
                    while 'LastEvaluatedKey' in response:
                        query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                        response = trips_table.query(**query_kwargs)
                        all_items.extend(response.get('Items', []))

                    # Sort descending by startTime
                    all_items.sort(key=lambda x: int(x.get('startTime', 0) or 0), reverse=True)

                    # Paginate in code
                    start_idx = (page - 1) * limit
                    page_items = all_items[start_idx:start_idx + limit]
                
                # Transform trips to only include essential fields
                trips = []
                safety_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME', 'cms-prod-storage-safety-events'))

                # Resolve driver names in ONE batch rather than per-row.
                # Background (2026-05-04): the previous implementation
                # (a) only attempted resolution when driverName started
                # with 'DRV-' so legacy 'DRIVER-046' style IDs rendered
                # as raw IDs, (b) did a fresh drivers_table.scan(Limit=50)
                # per trip when the row had no driver (N+1 problem), and
                # (c) persisted a randomly-picked driver back to the trip
                # row, which both fabricated data and failed silently
                # because the UpdateItem used a composite key the trips
                # table doesn't actually have.
                #
                # New approach: collect distinct canonical driverIds
                # across the page, BatchGetItem them in one call, then
                # render. Legacy 'DRIVER-NNN' maps to 'DRV-NNNN' via
                # zero-pad of the trailing number — verified 50/50 match
                # in the current data. Rows with no driverId render as
                # 'Unassigned' rather than a fabricated name; rows whose
                # driverId doesn't resolve render as the raw ID so it's
                # visibly "something real but unknown" rather than a lie.
                distinct_driver_ids = []
                _seen = set()
                for trip in page_items:
                    did = _canonical_driver_id(trip.get('driverId'))
                    if did and did not in _seen:
                        distinct_driver_ids.append(did)
                        _seen.add(did)

                driver_name_by_id: dict = {}
                if distinct_driver_ids:
                    drivers_table_name = os.environ.get('DRIVERS_TABLE_NAME')
                    try:
                        # BatchGetItem in chunks of 100 (AWS limit).
                        for i in range(0, len(distinct_driver_ids), 100):
                            chunk = distinct_driver_ids[i:i + 100]
                            br = dynamodb.batch_get_item(
                                RequestItems={
                                    drivers_table_name: {
                                        'Keys': [{'driverId': d} for d in chunk],
                                        'ProjectionExpression': 'driverId, firstName, lastName',
                                    }
                                }
                            )
                            for di in (br.get('Responses', {}) or {}).get(drivers_table_name, []):
                                fn = (di.get('firstName') or '').strip()
                                ln = (di.get('lastName') or '').strip()
                                full = f'{fn} {ln}'.strip()
                                driver_name_by_id[di.get('driverId')] = full or di.get('driverId')
                    except Exception as e:
                        # Best-effort — if BatchGet fails, rows render
                        # with raw IDs and we log the error. Never fatal.
                        print(f'vehicle-trips: driver-name batch lookup failed: {e}')

                # Vehicle-assignment fallback (2026-05-04): TripProcessor
                # historically didn't always populate driverId on trip
                # creation (e.g. when telemetry lacked the field). The
                # 1:1 invariant means there's exactly one driver assigned
                # to any vehicle at a time, so for trips without a
                # driverId we fall back to the currently-assigned driver
                # of the vehicle. We look up the assigned driver ONCE
                # per request (the list is vehicle-scoped, so it's
                # constant across all trips on the page) and reuse.
                # Note: DDB scan Limit is applied BEFORE FilterExpression,
                # so we paginate fully rather than setting a small Limit.
                # The drivers table is small (~75 rows) and the 1:1
                # invariant ensures at most one match — this stays cheap.
                assigned_driver_fallback = None
                try:
                    drivers_table_name = os.environ.get('DRIVERS_TABLE_NAME')
                    drivers_table = dynamodb.Table(drivers_table_name)
                    _assignment_items = []
                    _kwargs = {
                        'FilterExpression': 'assignedVehicleId = :v',
                        'ExpressionAttributeValues': {':v': vehicle_id},
                        'ProjectionExpression': 'driverId, firstName, lastName',
                    }
                    _resp = drivers_table.scan(**_kwargs)
                    _assignment_items.extend(_resp.get('Items', []))
                    for _ in range(10):
                        if 'LastEvaluatedKey' not in _resp:
                            break
                        _kwargs['ExclusiveStartKey'] = _resp['LastEvaluatedKey']
                        _resp = drivers_table.scan(**_kwargs)
                        _assignment_items.extend(_resp.get('Items', []))
                    if _assignment_items:
                        _d = _assignment_items[0]
                        first = (_d.get('firstName') or '').strip()
                        last = (_d.get('lastName') or '').strip()
                        full = f'{first} {last}'.strip()
                        if full or _d.get('driverId'):
                            assigned_driver_fallback = {
                                'driverId': _d.get('driverId'),
                                'name': full or _d.get('driverId'),
                            }
                except Exception as e:
                    print(f'vehicle-trips: assigned-driver fallback lookup failed for {vehicle_id}: {e}')

                for trip in page_items:
                    # Count safety events within trip time range
                    safety_events_count = 0
                    t_start = trip.get('startTime')
                    t_end = trip.get('endTime') or trip.get('completedAt')
                    t_vid = trip.get('vehicleId')
                    if t_start and t_vid:
                        try:
                            s = int(t_start)
                            e = int(t_end) if t_end else int(time.time() * 1000)
                            if s < 9999999999: s *= 1000
                            if e < 9999999999: e *= 1000
                            se_kwargs = {
                                'IndexName': 'vehicleId-timestamp-index',
                                'KeyConditionExpression': 'vehicleId = :v AND #ts BETWEEN :s AND :e',
                                'ExpressionAttributeNames': {'#ts': 'timestamp'},
                                'ExpressionAttributeValues': {
                                    ':v': t_vid,
                                    ':s': s,
                                    ':e': e,
                                },
                                'Select': 'COUNT',
                            }
                            safety_events_count = safety_table.query(**se_kwargs).get('Count', 0)
                        except Exception:
                            pass

                    # Resolve driver name. Preference order:
                    #   0. driverName already stored on the trip row, when it is
                    #      a name and not an ID. TripProcessor writes it at trip
                    #      creation; re-deriving it can only lose information,
                    #      and did — rows held 'Marcus Reyes' while the response
                    #      carried 'DRV-MRDN-0015'.
                    #   1. known driverId on the trip → full name from
                    #      batched lookup
                    #   2. driverId present but not found in drivers
                    #      table → raw ID (visible, honest)
                    #   3. no driverId → currently-assigned driver of
                    #      this vehicle (1:1 invariant) if any
                    #   4. nothing known → 'Unassigned'
                    #
                    # Note on 2: the frontend does NOT render a raw ID — it treats
                    # a DRV-prefixed value as unassigned — so this branch reads as
                    # "Unassigned" in the UI rather than as the honest ID this
                    # comment intends. The contract mismatch is recorded in
                    # issues/2026-09-23-driver-id-canonicaliser-rejects-non-numeric-ids/;
                    # `driverId` is now emitted alongside so the UI can link to the
                    # driver regardless of whether the name resolved.
                    raw_did = trip.get('driverId')
                    canon_did = _canonical_driver_id(raw_did)
                    stored_name = trip.get('driverName')
                    if stored_name and not _looks_like_driver_id(stored_name):
                        driver_name = stored_name.strip()
                    elif canon_did and canon_did in driver_name_by_id:
                        driver_name = driver_name_by_id[canon_did]
                    elif raw_did:
                        driver_name = raw_did
                    elif assigned_driver_fallback:
                        driver_name = assigned_driver_fallback['name']
                    else:
                        driver_name = 'Unassigned'
                    
                    # Convert timestamp fields from milliseconds to seconds if needed
                    start_time = trip.get('startTime')
                    if start_time:
                        try:
                            start_timestamp = int(start_time)
                            # If timestamp is in milliseconds (13+ digits), convert to seconds
                            if start_timestamp > 9999999999:
                                start_time = start_timestamp // 1000
                        except (ValueError, TypeError):
                            pass
                    
                    end_time = trip.get('endTime')
                    if end_time:
                        try:
                            end_timestamp = int(end_time)
                            # If timestamp is in milliseconds (13+ digits), convert to seconds
                            if end_timestamp > 9999999999:
                                end_time = end_timestamp // 1000
                        except (ValueError, TypeError):
                            pass
                    
                    trips.append({
                        'tripId': trip.get('tripId'),
                        'vehicleId': trip.get('vehicleId'),
                        'startTime': start_time,
                        'endTime': end_time or trip.get('completedAt'),
                        'duration': (trip.get('durationMs', 0) / 1000 / 60) if trip.get('durationMs') else 0,  # Convert ms to minutes
                        'distance': trip.get('totalDistance', trip.get('distance', 0)),
                        'maxSpeed': trip.get('maxSpeed', 0),
                        'avgSpeed': trip.get('averageSpeed', trip.get('avgSpeed', 0)),
                        'fuelConsumption': trip.get('currentFuelLevel', trip.get('fuelConsumption', 0)),
                        'driverName': driver_name,
                        # Emitted so the UI can link to the driver's detail page
                        # even when the NAME could not be resolved — the case the
                        # user hit, where the row rendered as "Unassigned" with no
                        # way to reach the driver it actually had. Canonical key
                        # first so the link resolves for legacy DRIVER-54 forms too.
                        'driverId': canon_did or raw_did
                                    or (assigned_driver_fallback or {}).get('driverId'),
                        'driverScore': trip.get('driverScore', 0),
                        'safetyEventsCount': safety_events_count
                    })
                
                has_next_page = (page * limit) < total_count
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'trips': trips,
                        'total': total_count,
                        'page': page,
                        'limit': limit,
                        'vehicleId': vehicle_id,
                        'hasNextPage': has_next_page,
                        'hasPrevPage': page > 1
                    }, default=decimal_default)
                }
                
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch trips: {str(e)}'})
                }
        
        # Handle vehicle safety events endpoint
        if path.startswith('/api/v1/vehicles/') and path.endswith('/safety-events') and method == 'GET':
            vehicle_id = path.split('/')[-2]
            limit = int(query_params.get('limit', 20))
            page = int(query_params.get('page', 1))
            
            try:
                safety_events_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                
                # Query safety events by vehicleId using scan with filter
                response = safety_events_table.scan(
                    FilterExpression='vehicleId = :vehicle_id',
                    ExpressionAttributeValues={':vehicle_id': vehicle_id},
                    Limit=limit * page  # Get enough items for pagination
                )
                
                all_events = response.get('Items', [])
                
                # Handle pagination manually
                start_index = (page - 1) * limit
                end_index = start_index + limit
                events = all_events[start_index:end_index]
                
                # Transform events to include proper field mappings
                safety_events = []
                for event in events:
                    # Get driver name if available
                    driver_name = event.get('driverId', 'Unknown Driver')
                    if driver_name and driver_name.startswith('DRV-'):
                        try:
                            drivers_table = dynamodb.Table(os.environ.get('DRIVERS_TABLE_NAME'))
                            driver_response = drivers_table.get_item(Key={'driverId': driver_name})
                            if 'Item' in driver_response:
                                driver_item = driver_response['Item']
                                first_name = driver_item.get('firstName', '')
                                last_name = driver_item.get('lastName', '')
                                if first_name or last_name:
                                    driver_name = f"{first_name} {last_name}".strip()
                        except Exception:
                            pass
                    
                    safety_event = {
                        'eventId': event.get('eventId'),
                        'tripId': event.get('tripId'),
                        'vehicleId': event.get('vehicleId'),
                        'driverId': event.get('driverId'),
                        'eventType': event.get('eventType'),
                        'severity': event.get('severity'),
                        'timestamp': event.get('timestamp'),
                        'location': {
                            'latitude': event.get('latitude', event.get('lat', 0)),
                            'longitude': event.get('longitude', event.get('lng', 0))
                        } if event.get('latitude') or event.get('lat') else None,
                        'description': event.get('description', event.get('message')),
                        'speed': event.get('speed'),
                        'gForce': event.get('gForce'),
                        'driverName': driver_name
                    }
                    safety_events.append(safety_event)
                
                # Define decimal handler before using it
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'events': safety_events,
                        'totalCount': len(all_events),
                        'page': page,
                        'limit': limit,
                        'vehicleId': vehicle_id
                    }, default=decimal_default)
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch safety events: {str(e)}'})
                }
        
        # Handle individual vehicle detail endpoint
        if path.startswith('/api/v1/vehicles/') and path != '/api/v1/vehicles/locations' and method == 'GET':
            vehicle_id = path.split('/')[-1]
            
            # Fleet operators can only access vehicles in their fleet
            if not has_unscoped_access:
                allowed = get_allowed_vehicle_ids()
                if allowed is not None and vehicle_id not in allowed:
                    return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Access denied'})}
            
            def decimal_default(obj):
                from decimal import Decimal
                if isinstance(obj, Decimal):
                    return int(obj) if obj % 1 == 0 else float(obj)
                raise TypeError
            
            try:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                
                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                trips_table = dynamodb.Table(os.environ.get('TRIPS_TABLE_NAME'))
                safety_events_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                maintenance_table = dynamodb.Table(os.environ.get('MAINTENANCE_ALERTS_TABLE_NAME'))
                telemetry_table = dynamodb.Table(os.environ.get('TELEMETRY_TABLE_NAME', f'cms-{os.environ.get("DEPLOYMENT_STAGE","prod")}-storage-telemetry'))
                
                # ── Parallel fetch all data ──────────────────────────────
                def fetch_vehicle():
                    resp = vehicles_table.get_item(Key={'vehicleId': vehicle_id})
                    return resp.get('Item')
                
                def fetch_fleet(vehicle):
                    _s = _t.time()
                    if not vehicle or not vehicle.get('fleetId'): return None
                    try:
                        fleets_table = dynamodb.Table(os.environ.get('FLEETS_TABLE_NAME'))
                        resp = fleets_table.get_item(Key={'fleetId': vehicle['fleetId']})
                        print(f"⏱️ fetch_fleet: {(_t.time()-_s)*1000:.0f}ms")
                        fleet = resp.get('Item', {})
                        return {
                            'name': fleet.get('name', 'Unknown Fleet'),
                            'defaultVehicleModelId': fleet.get('default_vehicle_model_id'),
                        }
                    except: return None
                
                def fetch_trips():
                    _s = _t.time()
                    try:
                        # Get total count first
                        count_resp = trips_table.query(
                            IndexName='vehicleId-index',
                            KeyConditionExpression='vehicleId = :vid',
                            ExpressionAttributeValues={':vid': vehicle_id},
                            Select='COUNT'
                        )
                        total = count_resp['Count']
                        while 'LastEvaluatedKey' in count_resp:
                            count_resp = trips_table.query(
                                IndexName='vehicleId-index',
                                KeyConditionExpression='vehicleId = :vid',
                                ExpressionAttributeValues={':vid': vehicle_id},
                                Select='COUNT',
                                ExclusiveStartKey=count_resp['LastEvaluatedKey']
                            )
                            total += count_resp['Count']

                        # Fetch trips WITHOUT the heavy route/routeGeometry fields -
                        # those are only needed on the dedicated last-trip detail below
                        # and on /api/v1/vehicles/{id}/trips/{tripId}. Shaves ~140KB
                        # off the response body for a 25-trip list.
                        projection = (
                            'tripId, vehicleId, fleetId, driverId, driverName, vin, '
                            'startTime, endTime, startTimeISO, endTimeISO, '
                            '#ts, createdAt, durationMs, #du, distance, totalDistance, '
                            'averageSpeed, maxSpeed, fuelConsumed, driverScore, '
                            'safetyEventsCount, tripType, #st, attributes, realRoute, '
                            'startLocation, endLocation'
                        )
                        expr_names = {'#ts': 'timestamp', '#du': 'duration', '#st': 'status'}

                        all_items = []
                        query_kwargs = {
                            'IndexName': 'vehicleId-index',
                            'KeyConditionExpression': 'vehicleId = :vid',
                            'ExpressionAttributeValues': {':vid': vehicle_id},
                            'ProjectionExpression': projection,
                            'ExpressionAttributeNames': expr_names,
                        }
                        resp = trips_table.query(**query_kwargs)
                        all_items.extend(resp.get('Items', []))
                        while 'LastEvaluatedKey' in resp:
                            query_kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
                            resp = trips_table.query(**query_kwargs)
                            all_items.extend(resp.get('Items', []))

                        # Sort by startTime descending and take top 25
                        all_items.sort(key=lambda x: int(x.get('startTime', x.get('timestamp', 0)) or 0), reverse=True)
                        items = all_items[:25]
                        # Attach total count to first item for the response builder
                        if items:
                            items[0]['_totalCount'] = total
                        print(f"⏱️ fetch_trips: {(_t.time()-_s)*1000:.0f}ms ({total} total, {len(items)} returned)")
                        return items
                    except Exception as e:
                        print(f"fetch_trips error: {e}")
                        return []
                
                def fetch_last_trip_detail(trips):
                    if not trips: return None
                    try:
                        # Try recent trips until we find one with route data
                        for trip_summary in trips[:5]:
                            trip_id = trip_summary['tripId']
                            resp = trips_table.get_item(Key={'tripId': trip_id})
                            trip = resp.get('Item')
                            if not trip: continue

                            # Check if trip has embedded route (from TripProcessor)
                            route_attr = trip.get('route', [])
                            if isinstance(route_attr, list) and len(route_attr) > 1:
                                route = []
                                for pt in route_attr:
                                    if isinstance(pt, dict) and 'lat' in pt and 'lng' in pt:
                                        route.append({'lat': float(pt['lat']), 'lng': float(pt['lng'])})
                                if len(route) > 1:
                                    trip['route'] = route
                                    trip['startLocation'] = {'lat': route[0]['lat'], 'lng': route[0]['lng']}
                                    trip['endLocation'] = {'lat': route[-1]['lat'], 'lng': route[-1]['lng']}
                                    return trip

                            # Fallback: build route from telemetry via tripId GSI
                            try:
                                t_resp = telemetry_table.query(
                                    IndexName='tripId-timestamp-index',
                                    KeyConditionExpression='tripId = :t',
                                    ExpressionAttributeValues={':t': trip_id},
                                    ProjectionExpression='lat, lng, #ts, speed',
                                    ExpressionAttributeNames={'#ts': 'timestamp'},
                                    ScanIndexForward=True,
                                )
                                route = [{'lat': float(i['lat']), 'lng': float(i['lng']),
                                          'timestamp': int(i.get('timestamp', 0)), 'speed': float(i.get('speed', 0))}
                                         for i in t_resp.get('Items', []) if i.get('lat') and i.get('lng')]
                                if len(route) > 1:
                                    trip['route'] = route
                                    trip['startLocation'] = {'lat': route[0]['lat'], 'lng': route[0]['lng']}
                                    trip['endLocation'] = {'lat': route[-1]['lat'], 'lng': route[-1]['lng']}
                                    return trip
                            except Exception:
                                pass

                        # Last resort: scan telemetry for this vehicle's most common trip
                        resp = trips_table.get_item(Key={'tripId': trips[0]['tripId']})
                        trip = resp.get('Item', {})
                        try:
                            all_points = []
                            scan_kwargs = {
                                'FilterExpression': 'vehicleId = :v AND attribute_exists(lat)',
                                'ExpressionAttributeValues': {':v': vehicle_id},
                                'ProjectionExpression': 'lat, lng, #ts, tripId',
                                'ExpressionAttributeNames': {'#ts': 'timestamp'},
                            }
                            for _ in range(3):
                                t_resp = telemetry_table.scan(**scan_kwargs)
                                all_points.extend(t_resp.get('Items', []))
                                if 'LastEvaluatedKey' not in t_resp: break
                                scan_kwargs['ExclusiveStartKey'] = t_resp['LastEvaluatedKey']
                            if all_points:
                                from collections import Counter
                                trip_counts = Counter(p.get('tripId', '') for p in all_points if p.get('tripId'))
                                if trip_counts:
                                    best = trip_counts.most_common(1)[0][0]
                                    pts = sorted([p for p in all_points if p.get('tripId') == best],
                                                 key=lambda x: int(x.get('timestamp', 0)))
                                    route = [{'lat': float(p['lat']), 'lng': float(p['lng'])} for p in pts]
                                    if route:
                                        trip['route'] = route
                                        trip['startLocation'] = {'lat': route[0]['lat'], 'lng': route[0]['lng']}
                                        trip['endLocation'] = {'lat': route[-1]['lat'], 'lng': route[-1]['lng']}
                        except Exception:
                            pass
                        trip.setdefault('route', [])
                        return trip
                    except: return None
                
                def fetch_safety():
                    try:
                        resp = safety_events_table.query(
                            IndexName='vehicleId-index',
                            KeyConditionExpression='vehicleId = :vid',
                            ExpressionAttributeValues={':vid': vehicle_id},
                            Limit=25
                        )
                        items = resp.get('Items', [])
                        for a in items:
                            if a.get('timestamp') and int(a['timestamp']) > 9999999999:
                                a['timestamp'] = int(a['timestamp']) // 1000
                        return items
                    except: return []
                
                def fetch_maintenance():
                    try:
                        # Fetch open maintenance alerts
                        count_resp = maintenance_table.query(
                            IndexName='vehicleId-index',
                            KeyConditionExpression='vehicleId = :vid',
                            ExpressionAttributeValues={':vid': vehicle_id},
                            Select='COUNT'
                        )
                        alert_total = count_resp['Count']
                        while 'LastEvaluatedKey' in count_resp:
                            count_resp = maintenance_table.query(
                                IndexName='vehicleId-index',
                                KeyConditionExpression='vehicleId = :vid',
                                ExpressionAttributeValues={':vid': vehicle_id},
                                Select='COUNT',
                                ExclusiveStartKey=count_resp['LastEvaluatedKey']
                            )
                            alert_total += count_resp['Count']
                        resp = maintenance_table.query(
                            IndexName='vehicleId-index',
                            KeyConditionExpression='vehicleId = :vid',
                            ExpressionAttributeValues={':vid': vehicle_id},
                            Limit=50
                        )
                        alerts = resp.get('Items', [])
                        for a in alerts:
                            a['_recordType'] = 'ALERT'
                        
                        # Fetch service history (completed work orders)
                        service_items = []
                        service_total = 0
                        try:
                            sh_table = dynamodb.Table(os.environ.get('SERVICE_HISTORY_TABLE_NAME', 'cms-prod-storage-service-history'))
                            sh_resp = sh_table.query(
                                KeyConditionExpression='vehicleId = :vid',
                                ExpressionAttributeValues={':vid': vehicle_id},
                                ScanIndexForward=False
                            )
                            service_items = sh_resp.get('Items', [])
                            while 'LastEvaluatedKey' in sh_resp:
                                sh_resp = sh_table.query(
                                    KeyConditionExpression='vehicleId = :vid',
                                    ExpressionAttributeValues={':vid': vehicle_id},
                                    ScanIndexForward=False,
                                    ExclusiveStartKey=sh_resp['LastEvaluatedKey']
                                )
                                service_items.extend(sh_resp.get('Items', []))
                            service_total = len(service_items)
                            for s in service_items:
                                s['_recordType'] = 'SERVICE_HISTORY'
                        except Exception as e:
                            print(f"Service history fetch error: {e}")
                        
                        combined = alerts + service_items
                        # Sort by date descending — use serviceDate for history, createdDate/timestamp for alerts
                        import time as _time
                        for item in combined:
                            if item.get('serviceDate'):
                                try:
                                    from datetime import datetime as _dt
                                    item['_sortKey'] = int(_dt.strptime(str(item['serviceDate'])[:19], '%Y-%m-%dT%H:%M:%S').timestamp() * 1000)
                                except:
                                    item['_sortKey'] = 0
                            else:
                                item['_sortKey'] = int(item.get('createdDate', 0) or item.get('timestamp', 0) or 0)
                        combined.sort(key=lambda x: x.get('_sortKey', 0), reverse=True)
                        
                        total = alert_total + service_total
                        if combined:
                            combined[0]['_totalCount'] = total
                            combined[0]['_alertCount'] = alert_total
                            combined[0]['_serviceCount'] = service_total
                        return combined
                    except: return []
                
                # Run vehicle fetch first (need it for fleet lookup)
                import time as _t
                _t0 = _t.time()
                vehicle = fetch_vehicle()
                print(f"⏱️ fetch_vehicle: {(_t.time()-_t0)*1000:.0f}ms")
                if not vehicle:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Vehicle {vehicle_id} not found'})
                    }
                
                # Get Redis client once (outside thread pool)
                rc = _get_redis()

                # Cheap cert presence check — single DDB hash-key lookup.
                # Surfaces a `has_certificate: bool` on the vehicle so the UI
                # can disable the "Start Agent" button when no cert exists
                # (otherwise the simulation Lambda 400s with "No certificate
                # found for VEH-...". See
                # issues/2026-05-29-staging-no-fwe-agent-on-demand/report.md).
                def fetch_has_cert():
                    try:
                        cert_table_name = os.environ.get('VEHICLE_CERTIFICATES_TABLE_NAME')
                        if not cert_table_name:
                            return False
                        cert_table = dynamodb.Table(cert_table_name)
                        resp = cert_table.get_item(
                            Key={'vehicleId': vehicle_id},
                            ProjectionExpression='vehicleId',
                        )
                        return 'Item' in resp
                    except Exception as e:
                        # Fail-open: don't block the page if certs lookup errors;
                        # downstream simulation Lambda will still 400 if cert
                        # truly missing.
                        print(f"has_certificate lookup error: {e}")
                        return True

                # Run everything else in parallel
                with ThreadPoolExecutor(max_workers=8) as executor:
                    fleet_future = executor.submit(fetch_fleet, vehicle)
                    trips_future = executor.submit(fetch_trips)
                    safety_future = executor.submit(fetch_safety)
                    maintenance_future = executor.submit(fetch_maintenance)
                    redis_future = executor.submit(_build_live_vehicle_state, vehicle_id, rc)
                    has_cert_future = executor.submit(fetch_has_cert)

                    # Assigned driver — reverse-lookup on drivers.assignedVehicleId.
                    # Authoritative source of truth is the driver record, not the
                    # vehicle record (see MANAGE_DRIVERS design note). Scans the
                    # drivers table (small, ~75 rows in the demo) for the first
                    # driver pointing at this vehicleId, and attaches name +
                    # driverId for UI link-out. No change to the drivers side.
                    #
                    # NOTE: paginates the full scan rather than using a Limit.
                    # DDB's FilterExpression evaluates AFTER the page-size limit,
                    # so a Limit-capped scan on a >Limit-size table may silently
                    # miss matching drivers that live in a later page. The
                    # simulator's _resolve_assigned_driver in simulation_lambda.py
                    # uses the same paginated pattern — keep them in sync.
                    def fetch_assigned_driver():
                        try:
                            drivers_tbl = dynamodb.Table(
                                os.environ.get('DRIVERS_TABLE_NAME')
                            )
                            items = []
                            kwargs = {
                                'FilterExpression': 'assignedVehicleId = :vid',
                                'ExpressionAttributeValues': {':vid': vehicle_id},
                                'ProjectionExpression': 'driverId, firstName, lastName, '
                                                         'assignedVehicleId, safetyScore',
                            }
                            resp = drivers_tbl.scan(**kwargs)
                            items.extend(resp.get('Items') or [])
                            for _ in range(10):  # defensive bound
                                if 'LastEvaluatedKey' not in resp:
                                    break
                                kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
                                resp = drivers_tbl.scan(**kwargs)
                                items.extend(resp.get('Items') or [])
                            if not items:
                                return None
                            # Policy: "1 driver per vehicle at a time"
                            # (VSA_DATA_SEED.md §driver-vehicle-invariant,
                            # added 2026-05-04). Active PUT /drivers/{id}
                            # now displaces prior holders atomically, so
                            # `items` should have ≤1 entry. When it's
                            # multi we apply the read-time ladder so the
                            # CMS UI picks the same winner the VSA API
                            # picks: status=active first, then most-recent
                            # lastTripDate, then lowest driverId. This
                            # keeps CMS and iOS showing the same driver
                            # on the rare race/stale case. Replaces the
                            # old safetyScore tiebreaker which was
                            # non-deterministic across reseeds.
                            kwargs2 = {
                                'FilterExpression': 'assignedVehicleId = :vid',
                                'ExpressionAttributeValues': {':vid': vehicle_id},
                                'ProjectionExpression': 'driverId, firstName, lastName, '
                                                         'assignedVehicleId, safetyScore, '
                                                         '#st, lastTripDate',
                                'ExpressionAttributeNames': {'#st': 'status'},
                            }
                            # Re-fetch with the ladder fields if the first
                            # pass didn't include them. Most common case:
                            # items came from the first scan above (which
                            # only projected safetyScore/firstName/etc).
                            try:
                                resp2 = drivers_tbl.scan(**kwargs2)
                                items = resp2.get('Items') or items
                            except Exception:
                                pass  # fall back to the already-loaded items
                            actives = [x for x in items if x.get('status') == 'active']
                            pool = actives if actives else items
                            # Two-pass stable sort: primary = lastTripDate DESC,
                            # secondary = driverId ASC.
                            pool = sorted(pool, key=lambda x: (x.get('driverId') or ''))
                            pool = sorted(pool, key=lambda x: x.get('lastTripDate') or '1900-01-01', reverse=True)
                            best = pool[0]
                            first = (best.get('firstName') or '').strip()
                            last = (best.get('lastName') or '').strip()
                            full_name = (first + ' ' + last).strip() or best.get('driverId')
                            return {
                                'driverId': best.get('driverId'),
                                'firstName': first,
                                'lastName': last,
                                'fullName': full_name,
                            }
                        except Exception as e:  # noqa: BLE001
                            print(f"fetch_assigned_driver error: {e}")
                            return None
                    assigned_driver_future = executor.submit(fetch_assigned_driver)

                    def fetch_campaigns():
                        try:
                            vin = vehicle.get('vin', '')
                            if not vin: return {'items': [], 'total': 0}
                            ct = dynamodb.Table(f'cms-{os.environ.get("DEPLOYMENT_STAGE", "prod")}-campaigns')
                            cr = ct.scan(
                                FilterExpression='(targetArn = :t OR targetArn = :all) AND #s = :running',
                                ExpressionAttributeValues={':t': f'vehicle:{vin}', ':all': 'all', ':running': 'RUNNING'},
                                ExpressionAttributeNames={'#s': 'status'},
                            )
                            items = []
                            for c in cr.get('Items', []):
                                if c.get('targetArn') == 'template':
                                    continue
                                items.append({
                                    'campaignId': c.get('campaignId'),
                                    'campaignName': c.get('campaignName'),
                                    'status': c.get('status'),
                                    'targetArn': c.get('targetArn'),
                                    'signalCount': len(c.get('signalsToCollect', [])),
                                    'collectionScheme': c.get('collectionScheme'),
                                    'decoderManifestId': c.get('decoderManifestId'),
                                    'createdAt': c.get('createdAt'),
                                    'description': c.get('description', ''),
                                })
                            return {'items': items, 'total': len(items)}
                        except Exception as e:
                            print(f"Campaigns fetch error: {e}")
                            return {'items': [], 'total': 0}
                    campaigns_future = executor.submit(fetch_campaigns)
                
                fleet_result = fleet_future.result()
                fleet_name = fleet_result.get('name') if fleet_result else None
                print(f"⏱️ fleet: done")
                trips = trips_future.result()
                print(f"⏱️ trips: done")
                safety_items = safety_future.result()
                print(f"⏱️ safety: done")
                maintenance_items = maintenance_future.result()
                print(f"⏱️ maintenance: done")
                live_state = redis_future.result()
                print(f"⏱️ redis: done, live_state keys={list(live_state.keys())[:5] if live_state else 'empty'}")
                print(f"⏱️ parallel queries: {(_t.time()-_t0)*1000:.0f}ms total")
                campaigns_data = campaigns_future.result()
                
                # Get last trip detail (depends on trips result)
                last_trip = fetch_last_trip_detail(trips) if trips else None
                
                # Assemble response
                if fleet_name: vehicle['fleetName'] = fleet_name
                if fleet_result and fleet_result.get('defaultVehicleModelId'):
                    vehicle['defaultVehicleModelId'] = fleet_result['defaultVehicleModelId']
                if live_state: vehicle.update(live_state)

                # Derive connectedness on the MERGED record, after the Redis
                # overlay. _build_live_vehicle_state returns {} when the Redis
                # `vehicle:{id}:meta` hash is absent (that hash is populated by
                # Flink from MSK telemetry — see
                # deployment/scripts/seed_engineering_fleets.py:183), and on that
                # path nothing above applies a staleness check, so the response
                # would carry the raw stored connectionStatus. A vehicle whose
                # telemetry stopped would then read `connected` indefinitely.
                #
                # Deriving here covers both paths with one rule and is safe to run
                # twice: resolve_connection_status never promotes, so re-deriving
                # an already-derived value is idempotent. It uses whichever
                # timestamp survived the merge (Flink's epoch-ms lastConnectedAt
                # when present, else the simulator's ISO lastSeenAt).
                # See .kiro/specs/2026-08-19-cms-connection-status-single-source/.
                vehicle['connectionStatus'] = resolve_connection_status(vehicle)

                # Surface IoT cert presence so the UI can disable the
                # "Start Agent" button when no cert exists for this
                # vehicle (issues/2026-05-29-staging-no-fwe-agent-on-demand).
                vehicle['has_certificate'] = has_cert_future.result()

                # Attach assigned-driver reverse-lookup (single source of truth:
                # drivers.assignedVehicleId). Exposed as both currentDriverName
                # (existing UI key — see VehicleDetailView.tsx:768) and the
                # richer currentDriver object so newer UI can link out.
                assigned_driver = assigned_driver_future.result()
                if assigned_driver:
                    vehicle['currentDriverName'] = assigned_driver.get('fullName')
                    vehicle['currentDriverId'] = assigned_driver.get('driverId')
                    vehicle['currentDriver'] = assigned_driver
                else:
                    # Explicit null so the UI renders "Unassigned" instead of
                    # leaving a stale previous value if this response gets
                    # merged into a cached vehicle object client-side.
                    vehicle['currentDriverName'] = None
                    vehicle['currentDriverId'] = None
                    vehicle['currentDriver'] = None

                if 'connectionStatus' not in vehicle: vehicle['connectionStatus'] = 'disconnected'
                if 'enrollmentStatus' not in vehicle: vehicle['enrollmentStatus'] = 'NOT_ENROLLED'
                
                # Assign random driver to trips missing one (in-memory only;
                # the per-trip update_item loop used to fire on every page load,
                # adding ~3-4s of latency. Now we only attach a driver for
                # display purposes - persistent assignment is a separate job.)
                _drivers_cache = None
                for t in trips:
                    if not t.get('driverId') or t.get('driverName', '') in ('Unknown Driver', '', None):
                        try:
                            if _drivers_cache is None:
                                _drivers_cache = dynamodb.Table(os.environ.get('DRIVERS_TABLE_NAME')).scan(Limit=50).get('Items', [])
                            if _drivers_cache:
                                idx = int(hashlib.md5(t.get('tripId', '').encode()).hexdigest(), 16) % len(_drivers_cache)
                                picked = _drivers_cache[idx]
                                t['driverId'] = picked.get('driverId')
                                t['driverName'] = f"{picked.get('firstName', '')} {picked.get('lastName', '')}".strip() or picked.get('driverId')
                        except Exception:
                            pass

                trips_data = {
                    'items': trips[:20],
                    'total': trips[0].get('_totalCount', len(trips)) if trips else 0,
                    'hasMore': (trips[0].get('_totalCount', len(trips)) if trips else 0) > 20
                }
                safety_data = {
                    'items': safety_items[:20],
                    'total': len(safety_items),
                    'hasMore': len(safety_items) > 20
                }
                maintenance_data = {
                    'items': maintenance_items[:100],
                    'total': maintenance_items[0].get('_totalCount', len(maintenance_items)) if maintenance_items else 0,
                    'hasMore': (maintenance_items[0].get('_totalCount', len(maintenance_items)) if maintenance_items else 0) > 100
                }
                
                # Build latestTelemetry from live signals
                latest_telemetry = None
                if live_state.get('liveSignals'):
                    latest_telemetry = {}
                    for s in live_state['liveSignals']:
                        try:
                            latest_telemetry[s['name']] = float(s['value']) if s['dataType'] in ('float', 'integer', 'FLOAT64') else s['value']
                        except (ValueError, TypeError):
                            latest_telemetry[s['name']] = s['value']
                    # Timestamp key drift fix (issues/2026-08-04-latest-telemetry-timestamp-zero/):
                    # _build_live_vehicle_state returns the epoch-ms timestamp under
                    # `lastUpdated` (from meta.lastConnectedAt|lastSeenAt). This
                    # code previously read `live_state['lastSeenAt']` which does
                    # not exist on the returned dict, so `.get(..., 0)` silently
                    # returned 0 and the UI's Vehicle State freshness label was
                    # meaningless. Read `lastUpdated`; keep the legacy
                    # `lastSeenAt` key as a fallback for any code path that
                    # sneaks that name in. Coerce via float() first because
                    # Redis returns strings.
                    ts_raw = live_state.get('lastUpdated') or live_state.get('lastSeenAt') or 0
                    try:
                        latest_telemetry['timestamp'] = int(float(ts_raw)) if ts_raw else 0
                    except (ValueError, TypeError):
                        latest_telemetry['timestamp'] = 0

                # Fallback: if no Redis data, get latest telemetry from DDB
                if not latest_telemetry:
                    try:
                        telemetry_table = dynamodb.Table(os.environ.get('TELEMETRY_TABLE_NAME', f'cms-{os.environ.get("DEPLOYMENT_STAGE","prod")}-storage-telemetry'))
                        # Query the key schema directly — do NOT scan.
                        #
                        # This previously did scan(FilterExpression='vehicleId = :v')
                        # over at most 5 pages (~5000 items). The table's key schema
                        # is exactly vehicleId (HASH) + timestamp (RANGE), so an
                        # exact-key lookup should be a Query: it is deterministic,
                        # single-digit-ms, and costs one read unit instead of
                        # scanning up to 5000 items. The bounded scan would silently
                        # return nothing for a vehicle whose rows fall outside the
                        # first 5 pages — a latent correctness risk that grows with
                        # table size.
                        #
                        # Honest scope note: no production failure of the scan was
                        # ever observed. This was filed during Group 5 verification
                        # on the belief that it had failed; that observation turned
                        # out to be a parsing error in the verification script
                        # (reading `vehicle.latestTelemetry` instead of the
                        # top-level `latestTelemetry`). The change stands on its own
                        # merits, not on a bug report.
                        # See Fix Group 12 in
                        # .kiro/specs/2026-08-04-cms-vehicle-trip-lifecycle-split/tasks.md
                        from boto3.dynamodb.conditions import Key as _Key
                        t_resp = telemetry_table.query(
                            KeyConditionExpression=_Key('vehicleId').eq(vehicle_id),
                            ScanIndexForward=False,   # newest first
                            Limit=1,
                        )
                        found_items = t_resp.get('Items', [])
                        found_item = found_items[0] if found_items else None

                        if found_item:
                            latest_telemetry = {}
                            from decimal import Decimal as Dec
                            for k, v_val in found_item.items():
                                if k in ('vehicleId', 'telemetryId', 'ttl'):
                                    continue
                                try:
                                    if isinstance(v_val, Dec):
                                        latest_telemetry[k] = float(v_val)
                                    elif isinstance(v_val, str) and v_val.replace('.','',1).replace('-','',1).isdigit():
                                        latest_telemetry[k] = float(v_val)
                                    else:
                                        latest_telemetry[k] = v_val
                                except (ValueError, TypeError):
                                    latest_telemetry[k] = str(v_val)
                    except Exception as e:
                        print(f"Telemetry fallback error: {e}")

                # ── Classification projection (spec 2026-08-29 § D2, D7) ─────────
                # Classify the vehicle and attach the result to the vehicle dict
                # before camelization. UnclassifiableVehicleError is caught here
                # and mapped to 'unknown' — no 500 propagation.
                try:
                    vehicle['classification'] = _classify_vehicle(vehicle)
                except UnclassifiableVehicleError as _e:
                    print(f"🚗 Classification failed for vehicle {vehicle_id}: {_e}")
                    vehicle['classification'] = 'unknown'

                # Fleet-disagreement warning (vehicle wins over fleet per spec § D4)
                _detail_fleet_row = _load_fleet(vehicle.get('fleetId'))
                _detail_fleet_ds = (_detail_fleet_row or {}).get('data_source')
                _vehicle_ds = vehicle.get('dataSource')
                if (_detail_fleet_row is not None and _detail_fleet_ds is not None
                        and _vehicle_ds is not None
                        and _detail_fleet_ds in _VALID_DATA_SOURCES
                        and _vehicle_ds in _VALID_DATA_SOURCES
                        and _detail_fleet_ds != _vehicle_ds):
                    vehicle['classificationWarnings'] = [{
                        'code': 'fleet_disagreement',
                        'message': (
                            f"Vehicle dataSource {_vehicle_ds!r} disagrees with fleet "
                            f"data_source {_detail_fleet_ds!r}. Vehicle wins per spec "
                            "2026-08-29-cms-vehicle-classification § D2."
                        ),
                        'vehicleDataSource': _vehicle_ds,
                        'fleetDataSource': _detail_fleet_ds,
                    }]

                # ── hasCampaign projection (spec 2026-09-01-cms-campaign-follows-enrollment § D3) ──
                # Query campaigns_table COUNT on targetArn-index for vehicle-telemetry.
                # cloud-telemetry: False unconditionally (no FleetWise campaigns).
                # hasCampaign is already camelCase so it passes through _camelize unchanged.
                #
                # THREE-STATE, deliberately. True = campaign confirmed present.
                # False = confirmed absent. None = could not determine. Collapsing
                # the third case into False is what made a CDK wiring gap render as
                # a confident "this vehicle has no campaign" banner on a vehicle
                # that had one — see
                # issues/2026-09-02-campaign-guard-queries-wrong-stage-table.
                # Do NOT reintroduce a stage-derived default table name here: the
                # old `cms-{DEPLOYMENT_STAGE or "prod"}-campaigns` fallback made
                # prod correct by luck and pointed staging at prod's table.
                try:
                    if vehicle.get('dataSource', 'vehicle-telemetry') == 'vehicle-telemetry':
                        _hc_tbl_name = os.environ.get('CAMPAIGNS_TABLE_NAME')
                        if not _hc_tbl_name:
                            # Fail closed to UNKNOWN, not to False: a missing env var must not
                            # be interpreted as "no campaigns", which would silence a real match.
                            print(
                                'hasCampaign: CAMPAIGNS_TABLE_NAME not configured; '
                                'reporting unknown rather than asserting absence'
                            )
                            vehicle['hasCampaign'] = None
                        else:
                            try:
                                from boto3.dynamodb.conditions import Key as _HCKey, Attr as _HCAttr
                            except (ImportError, ModuleNotFoundError):
                                # boto3 may be stubbed in tests; fall back to string-based FilterExpression
                                _HCKey = None
                                _HCAttr = None
                            _hc_tbl = dynamodb.Table(_hc_tbl_name)
                            _hc_vin = vehicle.get('vin', '')
                            if _HCKey is not None and _HCAttr is not None:
                                _hc_resp = _hc_tbl.query(
                                    IndexName='targetArn-index',
                                    KeyConditionExpression=_HCKey('targetArn').eq(f'vehicle:{_hc_vin}'),
                                    FilterExpression=_HCAttr('status').eq('RUNNING'),
                                    Select='COUNT',
                                )
                            else:
                                # Fallback for test environments where boto3.dynamodb is stubbed
                                _hc_resp = _hc_tbl.query(
                                    IndexName='targetArn-index',
                                    KeyConditionExpression='targetArn = :ta',
                                    FilterExpression='#s = :running',
                                    ExpressionAttributeValues={':ta': f'vehicle:{_hc_vin}', ':running': 'RUNNING'},
                                    ExpressionAttributeNames={'#s': 'status'},
                                    Select='COUNT',
                                )
                            vehicle['hasCampaign'] = _hc_resp.get('Count', 0) > 0
                    else:
                        vehicle['hasCampaign'] = False  # cloud-telemetry: no FleetWise campaigns
                except Exception as _hce:
                    # UNKNOWN, not False — we genuinely do not know.
                    print(f"hasCampaign query error: {_hce}")
                    vehicle['hasCampaign'] = None

                response_data = {
                    # Spec 2026-06-09-cms-api-field-normalization: API field
                    # boundary normalization. _camelize() renames snake_case
                    # keys to camelCase per docs/tech.md § "Vehicle API field
                    # convention"; allowlist (oem_source, oem1_*, lat, lng,
                    # subscription_service_activation_date, etc.) preserved
                    # via map omission. Helper is non-recursive — top-level
                    # keys only; nested location dicts unchanged.
                    'vehicle': _camelize(vehicle),
                    'trips': {
                        **trips_data,
                        'items': [_camelize(t) for t in trips_data.get('items', [])],
                    },
                    'safetyAlerts': {
                        **safety_data,
                        'items': [_camelize(s) for s in safety_data.get('items', [])],
                    },
                    'maintenanceAlerts': {
                        **maintenance_data,
                        'items': [_camelize(m) for m in maintenance_data.get('items', [])],
                    },
                    'campaigns': campaigns_data,
                    'lastTrip': _camelize(last_trip),
                    'latestTelemetry': latest_telemetry
                }
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps(response_data, default=decimal_default)
                }
                
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch vehicle: {str(e)}'})
                }
        
        # Handle vehicles locations endpoint
        if (path == '/api/v1/vehicles/locations' or path == '//api/v1/vehicles/locations') and method == 'GET':
            try:
                # Check cache first
                cache_table = dynamodb.Table(os.environ.get('DASHBOARD_METRICS_CACHE_TABLE'))
                
                try:
                    cache_response = cache_table.get_item(
                        Key={'metricKey': 'vehicle_locations'}
                    )
                    
                    if 'Item' in cache_response:
                        cached_data = json.loads(cache_response['Item']['data'])
                        return {
                            'statusCode': 200,
                            'headers': cors_headers,
                            'body': json.dumps(cached_data)
                        }
                except Exception:
                    pass
                
                # Fallback: generate vehicle locations from vehicles table
                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                
                # Scan all vehicles (remove limit to get all 3135 vehicles)
                vehicles = []
                scan_kwargs = {}
                
                while True:
                    response = vehicles_table.scan(**scan_kwargs)
                    vehicles.extend(response['Items'])
                    
                    if 'LastEvaluatedKey' not in response:
                        break
                    scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                
                # Generate locations based on vehicle data
                vehicle_locations = []
                for vehicle in vehicles:
                    vehicle_id = vehicle.get('vehicleId', '')
                    fleet_id = vehicle.get('fleetId', '')
                    status = vehicle.get('status', 'active')
                    make = vehicle.get('make', 'Unknown')
                    model = vehicle.get('model', 'Unknown')
                    
                    # Priority: stored lat/lng > telemetry > skip
                    lat = vehicle.get('lastLat') or vehicle.get('lat')
                    lng = vehicle.get('lastLng') or vehicle.get('lng')
                    
                    if lat is not None and lng is not None:
                        try:
                            lat = float(lat)
                            lng = float(lng)
                        except (ValueError, TypeError):
                            continue
                    else:
                        continue  # No location data, skip vehicle
                    
                    vehicle_locations.append({
                        'vehicleId': vehicle_id,
                        'vin': vehicle.get('vin', f'VIN{vehicle_id.replace("VEH-", "")}'),
                        'fleetId': fleet_id,
                        'status': status,
                        'make': make,
                        'model': model,
                        'lat': lat,
                        'lng': lng,
                        'lastUpdate': int(time.time()),
                        'connectionStatus': vehicle.get('connectionStatus', 'disconnected')
                    })
                
                # Redis geo index (vehicle:locations) is the authoritative live-
                # position SOURCE — the vehicles table stores no lat/lng, so the
                # base list above is normally empty. Build a location for every
                # geo member (joining make/model/fleet/status from the scanned
                # vehicles), and enrich any pre-existing entry in place.
                try:
                    rc = _get_redis()
                    if rc:
                        geo_results = rc.geosearch("vehicle:locations", 0, 0, 20000)  # global
                        if geo_results:
                            existing = {vl["vehicleId"]: vl for vl in vehicle_locations}
                            vehicles_by_id = {v.get("vehicleId", ""): v for v in vehicles}
                            for geo in geo_results:
                                vid = geo.get("vehicleId")
                                if not vid:
                                    continue
                                vl = existing.get(vid)
                                if vl is None:
                                    v = vehicles_by_id.get(vid, {})
                                    vl = {
                                        'vehicleId': vid,
                                        'vin': v.get('vin', vid),
                                        'fleetId': v.get('fleetId', ''),
                                        'status': v.get('status', 'active'),
                                        'make': v.get('make', 'Unknown'),
                                        'model': v.get('model', 'Unknown'),
                                        'lat': geo["lat"],
                                        'lng': geo["lng"],
                                        'lastUpdate': int(time.time()),
                                        'connectionStatus': 'connected',
                                    }
                                    vehicle_locations.append(vl)
                                    existing[vid] = vl
                                else:
                                    vl["lat"] = geo["lat"]
                                    vl["lng"] = geo["lng"]
                                    vl["connectionStatus"] = "connected"
                                meta = rc.hgetall(f"vehicle:{vid}:meta")
                                if meta.get("lastSeenAt"):
                                    try: vl["lastUpdate"] = int(float(meta["lastSeenAt"]) / 1000)
                                    except ValueError: pass
                except Exception as redis_err:
                    print(f"Redis locations source skipped: {redis_err}")

                # Last-known-state fallback: surface vehicles with a persisted
                # position in Redis that are NOT in the live geo index — e.g. FWE
                # vehicles whose trip just ended / telemetry paused. Reuses
                # _build_live_vehicle_state (the SAME path vehicle-detail uses) so the
                # map shows every vehicle's last-known location, not only live ones.
                try:
                    rc2 = _get_redis()
                    if rc2:
                        located_ids = {vl.get("vehicleId") for vl in vehicle_locations}
                        for v in vehicles:
                            vid = v.get("vehicleId", "")
                            if not vid or vid in located_ids:
                                continue
                            state = _build_live_vehicle_state(vid, rc2) or {}
                            cl = state.get("currentLocation")
                            if not cl:
                                continue
                            try:
                                flat = float(cl["latitude"]); flng = float(cl["longitude"])
                            except (KeyError, ValueError, TypeError):
                                continue
                            lu = int(time.time())
                            try:
                                raw_lu = float(cl.get("lastUpdated") or 0)
                                if raw_lu > 0:
                                    lu = int(raw_lu / 1000) if raw_lu > 1e12 else int(raw_lu)
                            except (ValueError, TypeError):
                                pass
                            # 30-day activity window: only surface a last-known
                            # position if the vehicle reported within the last 30 days.
                            if lu < int(time.time()) - (30 * 86400):
                                continue
                            vehicle_locations.append({
                                'vehicleId': vid,
                                'vin': v.get('vin', vid),
                                'fleetId': v.get('fleetId', ''),
                                'status': v.get('status', 'active'),
                                'make': v.get('make', 'Unknown'),
                                'model': v.get('model', 'Unknown'),
                                'lat': flat,
                                'lng': flng,
                                'lastUpdate': lu,
                                'connectionStatus': state.get("connectionStatus", "last-known"),
                            })
                            located_ids.add(vid)
                except Exception as lks_err:
                    print(f"LKS locations fallback skipped: {lks_err}")

                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'vehicles': vehicle_locations,
                        'total': len(vehicles),  # Total vehicles in database
                        'withLocations': len(vehicle_locations),  # All vehicles have generated locations
                        'cached': False,
                        'timestamp': int(time.time())
                    }, default=decimal_default)
                }
                
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch vehicle locations: {str(e)}'})
                }
        
        # Handle maintenance-alerts endpoint
        if (path == '/api/v1/maintenance-alerts' or path == '//api/v1/maintenance-alerts') and method == 'GET':
            try:
                maintenance_alerts_table = dynamodb.Table(os.environ.get('MAINTENANCE_ALERTS_TABLE_NAME'))
                
                limit = min(int(query_params.get('limit', 20)), 100)
                page = int(query_params.get('page', 1))
                start_time = query_params.get('startTime')
                end_time = query_params.get('endTime')
                fleet_id = query_params.get('fleetId')
                
                # Build filter expression
                filter_expression = None
                expression_values = {}
                expression_names = {}
                
                # Add time range filter
                if start_time and end_time:
                    # Convert milliseconds to seconds for numeric comparison
                    start_timestamp = int(start_time) // 1000 if len(start_time) > 10 else int(start_time)
                    end_timestamp = int(end_time) // 1000 if len(end_time) > 10 else int(end_time)
                    
                    # Use numeric comparison (timestamps are now stored as numbers)
                    filter_expression = '#ts BETWEEN :start_time AND :end_time'
                    expression_values[':start_time'] = start_timestamp
                    expression_values[':end_time'] = end_timestamp
                    expression_names['#ts'] = 'timestamp'
                
                # Add fleet filter
                if fleet_id and fleet_id != 'all':
                    # Filter by vehicle ID prefix since maintenance alerts don't have fleetId field
                    if fleet_id == 'FLEET-MUNICH':
                        vehicle_prefix = 'VEH-MUN-'
                    else:
                        fleet_code = fleet_id.replace('FLEET-', '')
                        vehicle_prefix = f'VEH-{fleet_code}-'
                    
                    fleet_filter = 'begins_with(vehicleId, :prefix)'
                    expression_values[':prefix'] = vehicle_prefix
                    
                    if filter_expression:
                        filter_expression += f' AND {fleet_filter}'
                    else:
                        filter_expression = fleet_filter
                
                # Get total count
                count_kwargs = {'Select': 'COUNT'}
                if filter_expression:
                    count_kwargs['FilterExpression'] = filter_expression
                    if expression_names:
                        count_kwargs['ExpressionAttributeNames'] = expression_names
                    if expression_values:
                        count_kwargs['ExpressionAttributeValues'] = expression_values
                
                count_response = maintenance_alerts_table.scan(**count_kwargs)
                total_count = count_response['Count']
                
                # Handle pagination
                scan_kwargs = {'Limit': limit * 50}  # Increase scan limit for filtering efficiency
                if filter_expression:
                    scan_kwargs['FilterExpression'] = filter_expression
                    if expression_names:
                        scan_kwargs['ExpressionAttributeNames'] = expression_names
                    if expression_values:
                        scan_kwargs['ExpressionAttributeValues'] = expression_values
                
                # Skip to the correct page
                current_page = 1
                while current_page < page:
                    response = maintenance_alerts_table.scan(**scan_kwargs)
                    if 'LastEvaluatedKey' not in response:
                        # No more data
                        return {
                            'statusCode': 200,
                            'headers': cors_headers,
                            'body': json.dumps({
                                'alerts': [],
                                'total': total_count,
                                'page': page,
                                'limit': limit,
                                'totalPages': (total_count + limit - 1) // limit,
                                'hasNextPage': False,
                                'hasPrevPage': page > 1
                            })
                        }
                    scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                    current_page += 1
                
                # Get the actual page data - collect until we have enough records
                alerts = []
                while len(alerts) < limit:
                    response = maintenance_alerts_table.scan(**scan_kwargs)
                    page_alerts = response['Items']
                    alerts.extend(page_alerts)
                    
                    if 'LastEvaluatedKey' not in response:
                        break
                    scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                
                # Trim to exact limit
                alerts = alerts[:limit]
                
                total_pages = (total_count + limit - 1) // limit
                has_next_page = 'LastEvaluatedKey' in response
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'alerts': alerts,
                        'total': total_count,
                        'page': page,
                        'limit': limit,
                        'totalPages': total_pages,
                        'hasNextPage': has_next_page,
                        'hasPrevPage': page > 1
                    }, default=decimal_default)
                }
                
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch maintenance alerts: {str(e)}'})
                }
        
        # ── Dealer roster (DMS-backed) ────────────────────────────────────
        #
        # Spec 2026-09-02-cms-dms-service-convergence T4.2. Replaces the
        # hardcoded `SERVICE_CENTER_OPTIONS` array the scheduling pickers used
        # to embed. Server-side rather than browser-direct per D3: keeping the
        # call here means one origin and one auth story for the CMS UI, no CORS
        # on the DMS API, and no re-adding the `dmsApiEndpoint` runtimeConfig
        # key that spec 2026-08-31-dms-standalone-ui T4.4 removed.
        #
        # CMS makes NO authorization decision here. It forwards the caller's own
        # Cognito token so DMS authorizes the END USER — that is D2/D3, and it
        # is the specific shape the CMS fail-open P0 taught: an authorization
        # decision taken by the caller on the callee's behalf is a decision
        # taken in the wrong place
        # (issues/2026-08-05-main-api-fail-open-authz-defaults/). There is no
        # service role and no fleet-scope check in this branch.
        #
        # EVERY failure path returns an error status. None returns an empty
        # list, because `{"dealers": []}` renders as "there are no service
        # centers" — a claim about the world — where the truth is "we could not
        # ask". That is the D5 rule and the same defect class as the fabricated
        # dealer column this spec removed.
        if path == '/api/v1/dealers' and method == 'GET':
            dms_endpoint = os.environ.get('DMS_API_ENDPOINT', '').strip()
            if not dms_endpoint:
                # Fail closed and say so. A hardcoded fallback roster here would
                # reintroduce exactly what T4.2 deleted, and would do it in a
                # place no frontend reviewer would look.
                print('GET /api/v1/dealers: DMS_API_ENDPOINT is not set')
                return {
                    'statusCode': 503,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'error': 'Dealer directory unavailable',
                        'detail': 'DMS_API_ENDPOINT is not configured on this stage.',
                    })
                }

            # The caller's bearer token is about to travel over this URL, so the
            # scheme is a security property rather than a style preference. The
            # value is operator-supplied via CDK, but an http:// typo would put
            # a live Cognito token on the wire in plaintext.
            if not dms_endpoint.startswith('https://'):
                print('GET /api/v1/dealers: DMS_API_ENDPOINT is not https')
                return {
                    'statusCode': 503,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'error': 'Dealer directory unavailable',
                        'detail': 'DMS_API_ENDPOINT must be an https:// URL.',
                    })
                }

            # Case-insensitive lookup: API Gateway's REST proxy integration
            # passes the client's header casing through unchanged, and browsers
            # are not consistent about it.
            _req_headers = event.get('headers') or {}
            auth_header = ''
            for _k, _v in _req_headers.items():
                if _k and _k.lower() == 'authorization':
                    auth_header = _v or ''
                    break
            if not auth_header:
                return {
                    'statusCode': 401,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'error': 'Missing Authorization header',
                        'detail': 'The dealer directory requires the caller to be authenticated.',
                    })
                }

            # _RefuseRedirects is defined at module level (D-G5f hoist).
            _opener = urllib.request.build_opener(_RefuseRedirects)

            dms_url = dms_endpoint.rstrip('/') + '/api/dms/fleet/dealers'
            try:
                _req = urllib.request.Request(
                    dms_url,
                    method='GET',
                    headers={
                        'Authorization': auth_header,
                        'Content-Type': 'application/json',
                    },
                )
                with _opener.open(_req, timeout=8) as _resp:
                    _payload = json.loads(_resp.read().decode('utf-8'))
                _dealers = _payload.get('dealers') or []
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'dealers': _dealers,
                        'dealerCount': len(_dealers),
                    })
                }
            except urllib.error.HTTPError as _he:
                # Pass the status through rather than flattening to 500: a 403
                # means "this caller may not read the roster", which the UI must
                # be able to distinguish from "DMS is broken". The DMS body is
                # deliberately NOT forwarded — it is another service's error
                # shape and could carry detail CMS has not reviewed.
                print(f'GET /api/v1/dealers: DMS returned {_he.code}')
                return {
                    'statusCode': _he.code if _he.code in (401, 403) else 502,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'error': 'Dealer directory unavailable',
                        'detail': f'DMS responded {_he.code}.',
                    })
                }
            except Exception as _e:
                print(f'GET /api/v1/dealers: DMS call failed: {_e}')
                return {
                    'statusCode': 504,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'error': 'Dealer directory unavailable',
                        'detail': 'Could not reach the DMS dealer directory.',
                    })
                }

        # ── Service History Endpoints (spec 2026-09-02-cms-dms-service-convergence T5.2) ──
        #
        # When DMS_API_ENDPOINT is configured with an https:// URL, GET hydrates from
        # DMS with cache fallback per D-G5d, and POST creates the RO in DMS and then
        # writes the cache row (D-G5h — cache write only after DMS 201). When
        # DMS_API_ENDPOINT is unset/empty the routes fall through to legacy behaviour
        # (D-G5b — inert on deploy until an operator opts in).
        #
        # VIN→vehicleId resolution (D-G5e): vehicleId != vin on some vehicles
        # (divergent on ~3/8 sampled). Resolution is two-step — GetItem first (the
        # backfilled case where vehicle_vin stores vehicleId), then Query on vin-index.
        # No scan fallback: an unresolvable value yields vin=None on that row and a log
        # line. ui_stack.py adds one IAM statement for the vin-index Query.
        #
        # D-G5c correlation key: roId (stamped on the cache row by POST) or the
        # deterministic prefix bfsh-<sha256(vehicleId|serviceDate)[:24]> — same pure
        # function as DMS's backfill_service_history.derive_ro_id so legacy rows
        # correlate without storing anything extra.
        #
        # D-G5f: _RefuseRedirects is defined at module level and shared with GET
        # /api/v1/dealers (the dealers branch now just uses it, no longer defines it).

        def _redact_vin(vin: str) -> str:  # noqa: F811
            """Return a log-safe suffix of the VIN (last 6 chars, prefixed with ***).

            Byte-for-byte copy of services/data_processing/lambda/dms_ro_cache_invalidator/
            handler.py:_redact_vin — deliberately including the *** prefix and the
            len(vin) < 6 boundary — so a single grep for *** finds every site and the
            copies cannot drift. F6.2 / security review Cycle 8 W2.
            VINs are NEVER written to logs in full. See the F2.2 decision (commit 18aff824).
            """
            if not vin or len(vin) < 6:  # noqa: PLR2004
                return "***"
            return f"***{vin[-6:]}"

        def _scrub_identifier(text, identifier):
            """Return *text* with every occurrence of *identifier* redacted.

            The property this enforces is **"the caller-supplied identifier does not
            reach CloudWatch by ANY channel"** — deliberately stated that way rather
            than as a list of call sites, because a site list is what let this defect
            through once already.

            F6.2 redacted the interpolated *format field* and left the adjacent
            exception interpolating raw. A botocore `ValidationException` echoes the
            offending value, so a single line both redacted and published the same
            VIN::

                step1 failed for ***109186: ValidationException:
                    value 1HGBH41JXMN109186 is invalid

            Any string built from an exception, a response, or a message that could
            have observed the identifier goes through here first. Security review
            Cycle 9 W1 / F7.1.

            SCOPE: "any channel" means any channel **that carries the
            caller-supplied vehicle identifier**, which is what W1 was about. It is
            not a module-wide claim about exception text in general — the DMS-path
            POST 500 deliberately logs its exception raw, because nothing on that
            path has observed the identifier and the operator needs the detail (see
            F7.2's log line and its correlationId).
            """
            _s = str(text)
            _id = str(identifier or '')
            # Substitution is declined below the _redact_vin boundary, and the reason
            # is corruption rather than disclosure: a <6-character identifier redacts
            # to a bare '***', so replacing it would rewrite every occurrence of a
            # short substring anywhere in the message, including in text that has
            # nothing to do with the identifier. The raw short value therefore DOES
            # remain in the exception text on this branch — accepted because a value
            # that short is not a VIN (ISO 3779 is 17 characters) and production
            # vehicleIds are ~10+, so the branch is unreachable with a real
            # identifier. Note this is the opposite of the format field, which
            # _redact_vin masks to '***' and which discloses nothing.
            if not _id or len(_id) < 6:  # noqa: PLR2004
                return _s
            return _s.replace(_id, _redact_vin(_id))

        def _resolve_vin_for_vehicle_id(vehicle_id_value):
            """Return (vehicleId, vin) for a caller-supplied value.

            Per D-G5e: vehicleId != vin on some vehicles, so a naive copy is wrong.

            Step 1: GetItem on vehicleId — a hit means the value already IS a vehicleId
            (the backfilled case where vehicle_vin stores vehicleId).
            Step 2: Query vin-index — for cases where the caller supplied a real VIN.

            Returns (resolved_vehicleId, vin) where:
              - resolved_vehicleId is the DDB primary key to query service-history
              - vin is the real VIN to forward to DMS (may be None if unresolvable —
                caller still gets their cache rows, just no DMS VIN filtering)

            No scan fallback: storage_stack.py:498 records that a scan fallback "made
            a MISSING index look merely like a slow one for months". An unresolvable
            value returns (vehicle_id_value, None) and logs — never a guessed id.
            """
            vehicles_tbl = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
            # Step 1: try as vehicleId directly
            try:
                hit = vehicles_tbl.get_item(Key={'vehicleId': vehicle_id_value}).get('Item')
                if hit:
                    # 'vin' present-vs-absent on the record, NOT 'vin != vehicleId', is
                    # the signal that decides whether to forward a VIN filter to DMS.
                    #
                    # Some vehicles (e.g. Ford-style VINs used as the vehicleId) have a
                    # 'vin' attribute that legitimately equals vehicleId — that is the
                    # real VIN, not a synthesized copy. The prior guard
                    # (`vin_value != vehicle_id_value`) conflated "vin absent" with
                    # "vin present and equal", so it silently omitted the VIN filter for
                    # this vehicle shape and DMS fell back to its unscoped default —
                    # returning ~45 cross-fleet rows for a vehicle with 12 real ones.
                    # See issues/2026-09-12-vin-equals-vehicleid-resolver-treats-as-unresolvable/.
                    #
                    # D-G5e's original intent (do not FABRICATE a vin from vehicleId
                    # when the record has no vin at all) is preserved by checking
                    # `'vin' in hit` rather than truthiness of vin_value against
                    # vehicle_id_value.
                    if 'vin' in hit and hit.get('vin'):
                        return vehicle_id_value, hit['vin']
                    # vehicleId found but vin attribute absent/empty (backfilled row):
                    # return the vehicleId with None vin so DMS call omits VIN filter.
                    return vehicle_id_value, None
            except Exception as _e:
                # F7.1: the exception text goes through _scrub_identifier too. Naming
                # the class keeps the diagnostic — a resolver failure with no detail is
                # how a missing index looks merely slow (storage_stack.py:498).
                print(
                    f'service-history VIN resolver step1 failed for '
                    f'{_redact_vin(vehicle_id_value)}: {type(_e).__name__}: '
                    f'{_scrub_identifier(_e, vehicle_id_value)}'
                )

            # Step 2: query vin-index (vehicle_id_value might be a real VIN)
            vehicles_tbl_name = os.environ.get('VEHICLES_TABLE_NAME', '')
            try:
                from boto3.dynamodb.conditions import Key as _Key  # noqa: PLC0415
                _result = dynamodb.Table(vehicles_tbl_name).query(
                    IndexName='vin-index',
                    KeyConditionExpression=_Key('vin').eq(vehicle_id_value),
                    ProjectionExpression='vehicleId, vin',
                )
                _items = _result.get('Items', [])
                if _items:
                    resolved = _items[0]['vehicleId']
                    return resolved, vehicle_id_value  # value was a real VIN
                print(f'service-history VIN resolver: no vehicle found for {_redact_vin(vehicle_id_value)} (vin-index miss)')
            except Exception as _e:
                # F7.1: same treatment as step 1 — see _scrub_identifier.
                print(
                    f'service-history VIN resolver step2 failed for '
                    f'{_redact_vin(vehicle_id_value)} '
                    f'(index={vehicles_tbl_name}/vin-index): '
                    f'{type(_e).__name__}: {_scrub_identifier(_e, vehicle_id_value)}'
                )

            # Unresolvable — return as-is so cache path still works
            return vehicle_id_value, None

        def _sh_decimal_default(obj):
            """Local Decimal serialiser for service-history routes.

            Several other handlers define the same helper inline; Python treats
            `decimal_default` as a local variable for the whole handler because of
            those `def` lines, so code paths reaching this branch without first
            executing one of them would get UnboundLocalError. Named distinctly.

            The rename fixed the collision on the FUNCTION name and missed the
            same mechanism one level down on the TYPE name: `Decimal` is imported
            at module level AND re-imported 30 times inside handler(), which makes
            it a handler-local too. None of those 30 imports execute on the
            service-history path, so this closure referenced an unbound local and
            every GET that actually carried a Decimal returned 500. The def-local
            import below is the fix — it is what every sibling `decimal_default`
            already does. See test_no_nested_def_closes_over_a_shadowed_import,
            which asserts this structurally instead of by comment.
            """
            from decimal import Decimal
            if isinstance(obj, Decimal):
                return int(obj) if obj % 1 == 0 else float(obj)
            raise TypeError

        if path == '/api/v1/service-history' and method == 'GET':
            # D-G5g: CMS-side auth check stays on the CACHE path only. The existing
            # get_allowed_vehicle_ids() / has_unscoped_access checks are applied when
            # reading from CMS's own table. On the live DMS path, CMS forwards the
            # caller's token and lets DMS authorize the end user — no CMS-side fleet
            # filter on the DMS request. That is D2/D3 and the shape the fail-open
            # P0 taught; inverting it here is a security defect, not a convenience.
            dms_endpoint = os.environ.get('DMS_API_ENDPOINT', '').strip()

            if not dms_endpoint:
                # ── D-G5b: LEGACY path — DMS_API_ENDPOINT is unset ───────────────
                # Byte-for-byte unchanged behaviour. The cache table is still the
                # primary store on this stage. No dataSource envelope emitted, so
                # legFromBody() yields "live", which is true because the table IS
                # primary here.
                try:
                    vehicle_id = query_params.get('vehicleId')
                    service_type = query_params.get('serviceType')
                    limit = int(query_params.get('limit', 50))

                    if vehicle_id and not has_unscoped_access:
                        allowed = get_allowed_vehicle_ids()
                        if allowed is not None and vehicle_id not in allowed:
                            return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Access denied'})}

                    service_history_table = dynamodb.Table(os.environ.get('SERVICE_HISTORY_TABLE_NAME'))

                    if vehicle_id:
                        response = service_history_table.query(
                            KeyConditionExpression='vehicleId = :vehicleId',
                            ExpressionAttributeValues={':vehicleId': vehicle_id},
                            ScanIndexForward=False,
                            Limit=limit
                        )
                    elif service_type:
                        response = service_history_table.query(
                            IndexName='ServiceTypeIndex',
                            KeyConditionExpression='serviceType = :serviceType',
                            ExpressionAttributeValues={':serviceType': service_type},
                            ScanIndexForward=False,
                            Limit=limit
                        )
                        if not has_unscoped_access:
                            allowed = get_allowed_vehicle_ids()
                            if allowed is not None:
                                response['Items'] = [i for i in response.get('Items', []) if i.get('vehicleId') in allowed]
                    else:
                        response = service_history_table.scan(Limit=limit)
                        if not has_unscoped_access:
                            allowed = get_allowed_vehicle_ids()
                            if allowed is not None:
                                response['Items'] = [i for i in response.get('Items', []) if i.get('vehicleId') in allowed]

                    return {
                        'statusCode': 200,
                        'headers': cors_headers,
                        'body': json.dumps({
                            'serviceRecords': _project_service_records(response.get('Items', [])),
                            'count': len(response.get('Items', []))
                        }, default=_sh_decimal_default)
                    }
                except Exception as e:
                    return {
                        'statusCode': 500,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Failed to fetch service history: {str(e)}'})
                    }

            # ── D-G5b: fail-closed on a non-https endpoint ────────────────────────
            # A misconfigured endpoint (e.g. http:// typo) is an operator error that
            # must be visible. Silently serving legacy data under a half-configured
            # integration is how the 18-day inert DRIVER_SELF_GUARD_ENABLED happened.
            if not dms_endpoint.startswith('https://'):
                print('GET /api/v1/service-history: DMS_API_ENDPOINT is not https')
                return {
                    'statusCode': 503,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'error': 'Service history unavailable',
                        'detail': 'DMS_API_ENDPOINT must be an https:// URL.',
                    })
                }

            # ── D-G5b: DMS-backed read-through path ──────────────────────────────
            vehicle_id = query_params.get('vehicleId')

            # D-G5g: CMS-side scope check on the CACHE path only (applied below when
            # we read from service_history_table). The DMS call carries the caller's
            # token; DMS authorises the end user.

            # D-G5g: Authorization header — same pattern as GET /api/v1/dealers.
            _req_headers_sh = event.get('headers') or {}
            auth_header_sh = ''
            for _k, _v in _req_headers_sh.items():
                if _k and _k.lower() == 'authorization':
                    auth_header_sh = _v or ''
                    break
            if not auth_header_sh:
                # D-G5d: "impossible" case — treat as a failed DMS leg.
                # Fall through to cache-or-502 path below.
                dms_records = None
                dms_failed = True
            else:
                # ── VIN hop (D-G5e) ──────────────────────────────────────────────
                # vehicleId may not equal vin; resolve before calling DMS.
                resolved_vehicle_id, resolved_vin = (
                    _resolve_vin_for_vehicle_id(vehicle_id)
                    if vehicle_id else (None, None)
                )

                # ── DMS call ─────────────────────────────────────────────────────
                _dms_opener = urllib.request.build_opener(_RefuseRedirects)
                _dms_path = '/api/dms/fleet/repair-orders'
                # D1 (spec 2026-09-10-service-history-read-path-correctness):
                # route scoped reads through DMS's ``vin-index`` GSI by
                # appending ``?vehicle_vin=<resolved>``.  DMS's
                # ``list_fleet_repair_orders`` reads that parameter and queries
                # the GSI once instead of falling back to the admin scan of
                # 100 of 43,200 rows (the defect this spec fixes).
                #
                # SUPERSEDES the earlier F4.3 stance ("do NOT append ?vin="),
                # which was correct for the admin-scan-only DMS handler that
                # existed BEFORE Group 2 of this spec added the parameter.
                #
                # ``urllib.parse.quote`` is used because a VIN is a URL path
                # value with a bounded alphabet, but a malicious or malformed
                # caller-supplied value could otherwise inject.  Same posture
                # as the rest of this module — encode at the boundary.
                #
                # Def-local ``import urllib.parse`` follows the same discipline
                # as ``_sh_decimal_default`` above: 35 function-local imports
                # inside ``handler()`` make several standard-lib names
                # handler-locals, so referencing an outer-scope
                # ``urllib.parse`` from within handler() is the class of bug
                # that test_no_shadowed_import_closures.py guards.  Local
                # import removes the entire class.
                import urllib.parse as _urllib_parse  # noqa: PLC0415
                _dms_query = ''
                if resolved_vin:
                    _dms_query = '?vehicle_vin=' + _urllib_parse.quote(
                        str(resolved_vin), safe='',
                    )
                _dms_url = dms_endpoint.rstrip('/') + _dms_path + _dms_query
                dms_records = None
                dms_failed = False
                try:
                    _dms_req = urllib.request.Request(
                        _dms_url,
                        method='GET',
                        headers={
                            'Authorization': auth_header_sh,
                            'Content-Type': 'application/json',
                        },
                    )
                    with _dms_opener.open(_dms_req, timeout=10) as _dms_resp:
                        _dms_payload = json.loads(_dms_resp.read().decode('utf-8'))
                    # F4.1: DMS returns {"items": [...]} (ok_response from
                    # list_fleet_repair_orders). A missing "items" key means a
                    # failed/malformed leg — treat as failure, not an empty list.
                    if 'items' not in _dms_payload:
                        print('GET /api/v1/service-history: DMS response missing "items" key')
                        dms_failed = True
                    else:
                        dms_records = _dms_payload['items']
                except Exception as _dms_e:
                    print(f'GET /api/v1/service-history: DMS call failed: {_dms_e}')
                    dms_failed = True

            # ── Cache read ────────────────────────────────────────────────────────
            # D-G5g: CMS-side fleet scope check applied HERE, on CMS's own table.
            # F4.3: read limit/serviceType from query params for merged-list filtering.
            cache_items = []
            _sh_limit = int(query_params.get('limit', 50))
            _sh_service_type = query_params.get('serviceType')
            try:
                service_history_table = dynamodb.Table(os.environ.get('SERVICE_HISTORY_TABLE_NAME'))
                if vehicle_id:
                    if vehicle_id and not has_unscoped_access:
                        allowed = get_allowed_vehicle_ids()
                        if allowed is not None and vehicle_id not in allowed:
                            return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Access denied'})}
                    _cache_resp = service_history_table.query(
                        KeyConditionExpression='vehicleId = :vehicleId',
                        ExpressionAttributeValues={':vehicleId': vehicle_id},
                        ScanIndexForward=False,
                        Limit=200
                    )
                    cache_items = _cache_resp.get('Items', [])
                else:
                    _cache_resp = service_history_table.scan(Limit=200)
                    cache_items = _cache_resp.get('Items', [])
                    if not has_unscoped_access:
                        allowed = get_allowed_vehicle_ids()
                        if allowed is not None:
                            cache_items = [i for i in cache_items if i.get('vehicleId') in allowed]
            except Exception as _ce:
                print(f'GET /api/v1/service-history: cache read failed: {_ce}')
                cache_items = []

            # ── D-G5d: failed DMS + empty cache = 502 ────────────────────────────
            # `200 {"serviceRecords": []}` after a failed upstream is a claim that
            # this vehicle has no service history; the truth is we could not ask.
            # D5 rule: failure never renders as an empty list.
            if dms_failed and not cache_items:
                return {
                    'statusCode': 502,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'error': 'Service history unavailable',
                        'detail': 'Could not retrieve service history from DMS and the cache is empty.',
                    })
                }

            # ── D-G5c: merge DMS records with cache row extras ────────────────────
            # Build a map from correlation key → cache row extras (fields DMS does not own).
            # Correlation: roId (stamped by POST) or the deterministic prefix
            # bfsh-sha256(vehicleId|serviceDate)[:24] — mirrors DMS's backfill function.
            def _bfsh_key(vid, sdate):
                return 'bfsh-' + hashlib.sha256(f'{vid}|{sdate}'.encode()).hexdigest()[:24]

            cache_by_key = {}
            for _ci in cache_items:
                _ck = _ci.get('roId') or _bfsh_key(
                    _ci.get('vehicleId', ''), _ci.get('serviceDate', '')
                )
                cache_by_key[_ck] = _ci

            merged_records = []
            dms_keys_seen = set()

            # F5.2: memoized VIN resolver for the unscoped GET (no vehicleId param).
            # D-G5j defect 2: when vehicle_id is absent, resolved_vehicle_id was ''
            # for every row, so the mapper never emitted vehicleId on any record and
            # (via the F5.1 defect) the cache's vehicleId was stripped too.
            # Fix: resolve each distinct vehicle_vin once per request; N rows on M
            # distinct vehicles cost M GetItem/Query calls, not N.
            # No scan fallback (storage_stack.py:498).
            _vin_resolve_memo: dict = {}  # vehicle_vin → (resolved_vehicle_id, resolved_vin)

            def _resolve_for_row(row_vehicle_vin: str):
                """Return (vehicleId, vin) for a DMS row's vehicle_vin, memoized."""
                _v = str(row_vehicle_vin or '').strip()
                if not _v:
                    return '', None
                if _v not in _vin_resolve_memo:
                    _vin_resolve_memo[_v] = _resolve_vin_for_vehicle_id(_v)
                return _vin_resolve_memo[_v]

            if not dms_failed and dms_records is not None:
                for _dr in dms_records:
                    # D1 (spec 2026-09-10-service-history-read-path-correctness):
                    # DMS filters server-side via the ``vin-index`` GSI query
                    # (``?vehicle_vin=<resolved>`` appended above).  The
                    # previous client-side VIN filter is DELETED — DMS now
                    # returns only rows for the requested VIN, and CMS trusts
                    # the answer.  R2 mutation target: restoring the filter
                    # causes DMS-trusted rows whose vehicle_vin differs from
                    # the resolved VIN (backfilled shape, F-G5a) to be
                    # dropped, which the T1.3(b) red test catches.

                    # F5.2: for unscoped GET, resolve vehicleId per row (memoized).
                    # For a scoped GET, resolved_vehicle_id/resolved_vin already exist.
                    if vehicle_id:
                        _row_vehicle_id = resolved_vehicle_id or ''
                    else:
                        _row_vehicle_id, _ = _resolve_for_row(
                            str(_dr.get('vehicle_vin') or '')
                        )

                    # F4.1: use _ro_to_service_record mapper per D-G5i field table.
                    # The mapper emits a key only when it has a value, so a DMS row
                    # missing a field does not blank the corresponding cache entry.
                    _mapped = _ro_to_service_record(_dr, resolved_vehicle_id=_row_vehicle_id)

                    # Derive correlation key from the mapped serviceId (ro_id)
                    _corr = _mapped.get('serviceId') or _bfsh_key(
                        _mapped.get('vehicleId', ''), _mapped.get('serviceDate', '')
                    )
                    dms_keys_seen.add(_corr)
                    _cache_extras = cache_by_key.get(_corr, {})
                    # F5.1 (D-G5j): merge is {**_cache_extras, **_mapped}.
                    # The prior code excluded cache keys in _DMS_OWNED_SERVICE_FIELDS
                    # unconditionally, discarding cache values for any DMS-owned key
                    # the mapper deliberately omitted (e.g. 'vin' when vehicle_vin ==
                    # resolved vehicleId). The mapper's omission is the signal; the
                    # merge must respect it by letting _mapped win only where _mapped
                    # actually supplies a key.  Merge order is the load-bearing property:
                    # _mapped appears second so it overwrites _cache_extras on overlap,
                    # but contributes nothing where it has no key.
                    _merged = {**_cache_extras, **_mapped}
                    # D3 (spec 2026-09-10): per-row provenance.  Set AFTER the
                    # merge so a stray ``provenance`` value in either input
                    # cannot spoof the label.  ``'dms'`` is authoritative here:
                    # the row was returned by DMS's GSI query, so its live-ness
                    # is derived from having reached the authoritative store,
                    # not from any field value.
                    _merged['provenance'] = 'dms'
                    merged_records.append(_merged)

            # D-G5c: cache-only rows — included, labelled dataSource: 'cache'
            # A cache row with no DMS counterpart is cached data not in the authoritative
            # store. Excluding it would make history vanish on stages where the backfill
            # hasn't run; including it unlabelled would assert freshness we don't have.
            #
            # D3 + D4 (spec 2026-09-10-service-history-read-path-correctness):
            # each cache-only row is stamped ``provenance: 'cache'`` (D3), and
            # a legacy row lacking ``serviceId`` projects ``roId`` into it (D4)
            # so a real record is never returned unidentified.  The stamp is
            # applied AFTER the merge (below the D1 DMS loop) so a cache row
            # that happened to carry a stray ``provenance`` field cannot spoof
            # the label.  Both fields are attached in place on the cache row
            # dict — safe because ``cache_by_key`` values are the mutable
            # projection-time copies, not the raw DDB items.
            cache_only_rows = [
                v for k, v in cache_by_key.items() if k not in dms_keys_seen
            ]
            for _c in cache_only_rows:
                _c['provenance'] = 'cache'
                # D4: serviceId falls back to roId for legacy cache rows.
                if not _c.get('serviceId') and _c.get('roId'):
                    _c['serviceId'] = _c['roId']
            has_cache_only = bool(cache_only_rows)
            # cacheOnlyCount is computed BEFORE the serviceType filter + limit
            # so the caller sees the true fraction of the payload sourced from
            # cache, not a post-limit count that could drop cache-only rows
            # from the visible page (their serviceDate is usually older, so
            # newest-first sort sinks them below a 50-row limit).  This
            # difference matters when the count is used to size a staleness
            # banner — the banner should describe reality, not the page.
            cache_only_count = len(cache_only_rows)
            merged_records.extend(cache_only_rows)

            # F4.3: sort merged list newest-first by serviceDate (already guaranteed
            # by DMS's own sort on the DMS records, but cache-only rows need ordering).
            merged_records.sort(
                key=lambda r: r.get('serviceDate') or r.get('createdAt') or '',
                reverse=True,
            )

            # F4.3: apply serviceType filter and limit to the merged list.
            if _sh_service_type:
                merged_records = [
                    r for r in merged_records
                    if r.get('serviceType') == _sh_service_type
                ]
            merged_records = merged_records[:_sh_limit]

            # D3 (spec 2026-09-10-service-history-read-path-correctness):
            # ``dataSource`` becomes a three-value enum — 'live' | 'cache' |
            # 'mixed' — and ``asOf`` is present ONLY when at least one
            # cache-only row exists AND is defined as the OLDEST cache-only
            # row's timestamp (worst-case staleness the user should see, not
            # the newest).  ``cacheOnlyCount`` is always emitted so the
            # frontend can distinguish "we don't know" (field absent) from
            # "explicitly zero" (field present and zero).
            #
            # Ordering matters: ``dms_failed`` overrides everything and
            # produces the pure ``cache`` label — regardless of whether any
            # DMS rows made it into merged_records (they didn't, because the
            # ``if not dms_failed`` guard skipped the DMS branch above).
            # Then mixed vs live is decided by whether ``has_cache_only`` is
            # true.  This preserves the "never present stale as live" property
            # that motivated the whole D3 change.
            if dms_failed:
                _data_source = 'cache'
                _as_of = min(
                    (
                        r.get('cachedAt') or r.get('createdAt', '')
                        for r in cache_items
                        if r.get('cachedAt') or r.get('createdAt')
                    ),
                    default=None,
                )
            elif has_cache_only:
                _data_source = 'mixed'
                _as_of = min(
                    (
                        r.get('cachedAt') or r.get('createdAt', '')
                        for r in cache_only_rows
                        if r.get('cachedAt') or r.get('createdAt')
                    ),
                    default=None,
                )
            else:
                _data_source = 'live'
                _as_of = None

            _envelope: dict = {
                'dataSource': _data_source,
                # Spec D3: cacheOnlyCount is always present.  Zero is
                # meaningful — a UI that sees an absent key would fall back to
                # "we don't know", which is a different fact.
                'cacheOnlyCount': cache_only_count if not dms_failed else len(cache_items),
            }
            # Spec D3: ``asOf`` omitted entirely when there are no cache-only
            # rows.  The T1.3(d) red test asserts absence with ``'asOf' not in
            # body``, not ``is None`` — so the key must not be present at all.
            if _as_of:
                _envelope['asOf'] = _as_of

            return {
                'statusCode': 200,
                'headers': cors_headers,
                'body': json.dumps({
                    'serviceRecords': _project_service_records(merged_records),
                    'count': len(merged_records),
                    **_envelope,
                }, default=_sh_decimal_default)
            }

        if path == '/api/v1/service-history' and method == 'POST':
            # D-G5g: _deny_viewer() stays unconditionally on POST — read-only principals
            # must not reach any write path.
            denied = _deny_viewer()
            if denied: return denied

            dms_endpoint = os.environ.get('DMS_API_ENDPOINT', '').strip()

            if not dms_endpoint:
                # ── D-G5b: LEGACY path — write to CMS table only ─────────────────
                try:
                    # parse_float=Decimal: DynamoDB rejects Python floats outright
                    # ("Float types are not supported"), so a body carrying
                    # cost.total: 249.99 would 500 at put_item. Parsing floats
                    # straight to Decimal means none ever exists. Responses are
                    # unaffected — _sh_decimal_default coerces Decimal back out.
                    #
                    # The local import is REQUIRED, not redundant: `Decimal` is
                    # shadowed into a handler-local by ~35 function-local imports
                    # elsewhere in handler(), none of which execute on this path,
                    # so referencing the module-level one here raises
                    # UnboundLocalError. Verified by test_post_with_a_float_cost_
                    # does_not_error, which caught exactly this while fixing the float bug.
                    from decimal import Decimal
                    body = json.loads(event.get('body', '{}'), parse_float=Decimal)
                    entry = body.get('entry', body)

                    if not has_unscoped_access:
                        allowed = get_allowed_vehicle_ids()
                        if allowed is not None and entry.get('vehicleId') not in allowed:
                            return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Access denied'})}

                    service_record = {
                        'vehicleId': entry['vehicleId'],
                        'serviceDate': entry['serviceDate'],
                        'serviceType': entry['serviceType'],
                        'dealerId': entry['dealerId'],
                        'mileage': entry.get('mileage'),
                        'serviceDetails': entry.get('serviceDetails', {}),
                        'cost': entry.get('cost', {}),
                        'technician': entry.get('technician'),
                        'warranty': entry.get('warranty', {}),
                        'createdAt': datetime.utcnow().isoformat(),
                        'updatedAt': datetime.utcnow().isoformat()
                    }

                    service_history_table = dynamodb.Table(os.environ.get('SERVICE_HISTORY_TABLE_NAME'))
                    service_history_table.put_item(Item=service_record)

                    return {
                        'statusCode': 201,
                        'headers': cors_headers,
                        'body': json.dumps({'serviceRecord': service_record}, default=_sh_decimal_default)
                    }
                except Exception as e:
                    return {
                        'statusCode': 500,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Failed to create service record: {str(e)}'})
                    }

            # ── D-G5b: fail-closed on non-https endpoint ──────────────────────────
            if not dms_endpoint.startswith('https://'):
                print('POST /api/v1/service-history: DMS_API_ENDPOINT is not https')
                return {
                    'statusCode': 503,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'error': 'Service history unavailable',
                        'detail': 'DMS_API_ENDPOINT must be an https:// URL.',
                    })
                }

            # ── D-G5b: DMS-backed POST path ───────────────────────────────────────
            # D-G5h: write the cache row ONLY after DMS accepts the booking.
            # A failed booking must leave no CMS row claiming service was scheduled.
            # The inverse (write then call) is a silent-success shape.
            try:
                body_str = event.get('body', '{}') or '{}'
                # parse_float=Decimal, and it matters MORE on this branch than on
                # the legacy one. DynamoDB rejects Python floats, so a body with
                # cost.total: 249.99 raised TypeError at the cache put_item —
                # which D-G5h places AFTER the DMS booking commits. The caller got
                # a 500 while a real repair order existed at a real rooftop, and a
                # retry created a second one. Parsing here, before the DMS call,
                # removes this failure class from the post-commit window entirely
                # rather than just fixing the type.
                # See issues/2026-09-10-service-history-post-floats-500-after-dms-
                # commits-orphaning-repair-orders/.
                #
                # The local import is REQUIRED: `Decimal` is shadowed into a
                # handler-local by ~35 function-local imports elsewhere in
                # handler(), none of which run on this path, so the module-level
                # one is unreachable here and a bare reference raises
                # UnboundLocalError — the same class as the two 500s fixed today.
                from decimal import Decimal
                _body = json.loads(body_str, parse_float=Decimal)
                # F4.2: handle {entry: {...}} wrapped shape — ScheduleServiceModal.tsx
                # posts the wrapped shape; unwrap it so both callers work.
                _body = _body.get('entry', _body)

                vehicle_id_post = _body.get('vehicleId') or _body.get('vehicle_id', '')
                dealer_id_post = _body.get('dealerId') or _body.get('dealer_id', '')
                description_post = _body.get('description', '')
                recall_id_post = _body.get('recallId') or _body.get('recall_id')
                channel_pref_post = _body.get('customerChannelPref') or _body.get('customer_channel_pref')
                status_post = _body.get('status', 'scheduled')
                # F4.2: preserve caller's serviceDate — it is the table's sort key;
                # overwriting with today collapses two bookings for one vehicle into one row.
                service_date_post = _body.get('serviceDate') or datetime.utcnow().isoformat()[:10]

                # D-G5g: CMS-side fleet scope check on the POST path.
                if not has_unscoped_access:
                    allowed = get_allowed_vehicle_ids()
                    if allowed is not None and vehicle_id_post not in allowed:
                        return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Access denied'})}

                # D-G5e: resolve VIN for DMS call
                _resolved_vid_post, _resolved_vin_post = (
                    _resolve_vin_for_vehicle_id(vehicle_id_post)
                    if vehicle_id_post else (vehicle_id_post, None)
                )

                # F4.2: unresolvable VIN is an explicit error — do NOT substitute
                # vehicleId in vehicle_vin (D-G5d "impossible" case, D-G5e forbids it).
                if vehicle_id_post and _resolved_vin_post is None:
                    return {
                        'statusCode': 502,
                        'headers': cors_headers,
                        'body': json.dumps({
                            'error': 'Service history booking failed',
                            'detail': 'Vehicle VIN could not be resolved for DMS booking.',
                        })
                    }

                # Authorization header
                _req_headers_post = event.get('headers') or {}
                auth_header_post = ''
                for _k, _v in _req_headers_post.items():
                    if _k and _k.lower() == 'authorization':
                        auth_header_post = _v or ''
                        break
                if not auth_header_post:
                    # D-G5d "impossible" case on POST — treat as DMS failure
                    return {
                        'statusCode': 502,
                        'headers': cors_headers,
                        'body': json.dumps({
                            'error': 'Service history unavailable',
                            'detail': 'Authorization header required for DMS-backed booking.',
                        })
                    }

                # Forward the booking to DMS
                _dms_post_opener = urllib.request.build_opener(_RefuseRedirects)
                _dms_post_url = dms_endpoint.rstrip('/') + '/api/dms/fleet/repair-orders'
                # D-G5e: use the resolved real VIN; substitution with vehicleId is forbidden
                _dms_post_body_obj = {
                    'vehicle_vin': _resolved_vin_post,
                    'dealer_id': dealer_id_post,
                    'description': description_post,
                }
                if recall_id_post:
                    _dms_post_body_obj['recall_id'] = recall_id_post
                if channel_pref_post:
                    _dms_post_body_obj['customer_channel_pref'] = channel_pref_post
                _dms_post_bytes = json.dumps(_dms_post_body_obj).encode('utf-8')
                _dms_post_req = urllib.request.Request(
                    _dms_post_url,
                    data=_dms_post_bytes,
                    method='POST',
                    headers={
                        'Authorization': auth_header_post,
                        'Content-Type': 'application/json',
                    },
                )
                try:
                    with _dms_post_opener.open(_dms_post_req, timeout=10) as _dms_post_resp:
                        _dms_post_payload = json.loads(_dms_post_resp.read().decode('utf-8'))
                except urllib.error.HTTPError as _he_post:
                    print(f'POST /api/v1/service-history: DMS returned {_he_post.code}')
                    return {
                        'statusCode': _he_post.code if _he_post.code in (400, 401, 403) else 502,
                        'headers': cors_headers,
                        'body': json.dumps({
                            'error': 'Service history booking failed',
                            'detail': f'DMS responded {_he_post.code}.',
                        })
                    }
                except Exception as _dms_post_e:
                    print(f'POST /api/v1/service-history: DMS call failed: {_dms_post_e}')
                    return {
                        'statusCode': 502,
                        'headers': cors_headers,
                        'body': json.dumps({
                            'error': 'Service history booking failed',
                            'detail': 'Could not reach DMS.',
                        })
                    }

                # ── D-G5h: DMS accepted (201) — NOW write the cache row ───────────
                # Order is the whole property. put_item runs only after the DMS 201.
                # A failed booking leaves no CMS row claiming service was scheduled.
                dms_ro_post = _dms_post_payload.get('repairOrder') or _dms_post_payload
                ro_id_new = dms_ro_post.get('roId') or dms_ro_post.get('ro_id', '')
                now_iso = datetime.utcnow().isoformat()
                service_record = {
                    'vehicleId': _resolved_vid_post or vehicle_id_post,
                    # F4.2: keep the caller's serviceDate (table sort key)
                    'serviceDate': service_date_post,
                    'serviceType': _body.get('serviceType', 'REPAIR'),
                    'dealerId': dealer_id_post,
                    'description': description_post,
                    # CMS writes caller's status vocabulary ('scheduled') per D-G5h;
                    # DMS records 'Draft'. On a live read DMS status wins per D-G5c.
                    'status': status_post,
                    'createdAt': now_iso,
                    'updatedAt': now_iso,
                    # Stamp cachedAt (F4.3: asOf prefers cachedAt) and roId for correlation
                    'cachedAt': now_iso,
                    'roId': ro_id_new,
                    # F4.2: carry all the fields the cache exists to supply
                    'notes': _body.get('notes', ''),
                    'cost': _body.get('cost', {}),
                    'estimatedCost': _body.get('estimatedCost'),
                    'estimatedDuration': _body.get('estimatedDuration'),
                    'category': _body.get('category', ''),
                    'provider': _body.get('provider', ''),
                    'serviceDetails': _body.get('serviceDetails', {}),
                    'mileage': _body.get('mileage'),
                    'technician': _body.get('technician'),
                    'warranty': _body.get('warranty', {}),
                }
                # vin from the resolved VIN (only when it differs from vehicleId, D-G5e)
                if _resolved_vin_post and _resolved_vin_post != (_resolved_vid_post or vehicle_id_post):
                    service_record['vin'] = _resolved_vin_post
                if recall_id_post:
                    service_record['alertId'] = recall_id_post

                service_history_table = dynamodb.Table(os.environ.get('SERVICE_HISTORY_TABLE_NAME'))
                service_history_table.put_item(Item=service_record)

                return {
                    'statusCode': 201,
                    'headers': cors_headers,
                    'body': json.dumps({'serviceRecord': service_record}, default=_sh_decimal_default)
                }
            except Exception as e:
                # F7.2: F6.3 correctly stopped echoing str(e) to the client and left the
                # operator holding a log line the caller cannot quote. A correlation id
                # closes that: the client gets an opaque handle, CloudWatch gets the
                # detail, and neither carries the exception text. Adopts the convention
                # DMS already uses (auth.py::server_error_response).
                #
                # getattr with a fallback because `context` is None in every test in
                # this suite — a bare context.aws_request_id would raise inside the
                # handler's own error path, turning a 500 into an unhandled exception.
                _corr = getattr(context, 'aws_request_id', '') or 'no-request-id'
                print(
                    f'POST /api/v1/service-history: unexpected error '
                    f'[correlationId={_corr}]: {type(e).__name__}: {e}'
                )
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'error': 'Failed to create service record',
                        'correlationId': _corr,
                    })
                }
        
        if path.startswith('/api/v1/service-history/') and method == 'GET':
            try:
                # Extract vehicleId and serviceDate from path
                path_parts = path.split('/')
                if len(path_parts) >= 5:
                    vehicle_id = path_parts[4]
                    service_date = path_parts[5] if len(path_parts) > 5 else None
                    
                    # Verify vehicle belongs to user's fleet
                    if not has_unscoped_access:
                        allowed = get_allowed_vehicle_ids()
                        if allowed is not None and vehicle_id not in allowed:
                            return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Access denied'})}
                    
                    service_history_table = dynamodb.Table(os.environ.get('SERVICE_HISTORY_TABLE_NAME'))
                    
                    if service_date:
                        # Get specific service record
                        response = service_history_table.get_item(
                            Key={'vehicleId': vehicle_id, 'serviceDate': service_date}
                        )
                        if 'Item' not in response:
                            return {
                                'statusCode': 404,
                                'headers': cors_headers,
                                'body': json.dumps({'error': 'Service record not found'})
                            }
                        return {
                            'statusCode': 200,
                            'headers': cors_headers,
                            'body': json.dumps({'serviceRecord': response['Item']}, default=decimal_default)
                        }
                    else:
                        # Get all service records for vehicle
                        response = service_history_table.query(
                            KeyConditionExpression='vehicleId = :vehicleId',
                            ExpressionAttributeValues={':vehicleId': vehicle_id},
                            ScanIndexForward=False
                        )
                        return {
                            'statusCode': 200,
                            'headers': cors_headers,
                            'body': json.dumps({
                                'serviceRecords': response.get('Items', []),
                                'count': len(response.get('Items', []))
                            }, default=decimal_default)
                        }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch service record: {str(e)}'})
                }
        
        # Warranty Claims Endpoints
        if path == '/api/v1/warranty-claims' and method == 'GET':
            try:
                stage = os.environ.get('DEPLOYMENT_STAGE', 'prod')
                wc_table = dynamodb.Table(f'cms-{stage}-storage-warranty-claims')

                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return float(obj)
                    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

                vehicle_id = query_params.get('vehicleId')
                status_filter = query_params.get('status')
                if vehicle_id:
                    resp = wc_table.query(
                        IndexName='vehicleId-index',
                        KeyConditionExpression='vehicleId = :v',
                        ExpressionAttributeValues={':v': vehicle_id},
                        ScanIndexForward=False
                    )
                    items = resp.get('Items', [])
                else:
                    resp = wc_table.scan()
                    items = resp.get('Items', [])
                    while 'LastEvaluatedKey' in resp:
                        resp = wc_table.scan(ExclusiveStartKey=resp['LastEvaluatedKey'])
                        items.extend(resp.get('Items', []))
                if status_filter:
                    items = [i for i in items if i.get('status', '').upper() == status_filter.upper()]
                total = len(items)
                paid = [i for i in items if i.get('status', '').upper() == 'PAID']
                submitted = [i for i in items if i.get('status', '').upper() == 'SUBMITTED']
                approved = [i for i in items if i.get('status', '').upper() == 'APPROVED']
                denied = [i for i in items if i.get('status', '').upper() == 'DENIED']
                total_recovered = sum(float(i.get('paidAmount', 0)) for i in paid)
                total_pending = sum(float(i.get('claimAmount', 0)) for i in submitted + approved)
                return {
                    'statusCode': 200, 'headers': cors_headers,
                    'body': json.dumps({
                        'claims': sorted(items, key=lambda x: x.get('filedDate', ''), reverse=True),
                        'count': total,
                        'summary': {
                            'total': total, 'paid': len(paid), 'submitted': len(submitted),
                            'approved': len(approved), 'denied': len(denied),
                            'totalRecovered': total_recovered, 'totalPending': total_pending,
                        }
                    }, default=decimal_default)
                }
            except Exception as e:
                return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

        # Fleet Campaign Endpoints
        if path == '/api/v1/fleet-campaigns' and method == 'GET':
            try:
                stage = os.environ.get('DEPLOYMENT_STAGE', 'prod')
                camp_table = dynamodb.Table(f'cms-{stage}-campaigns')
                fleet_id = query_params.get('fleetId')
                if not fleet_id:
                    return {'statusCode': 400, 'headers': cors_headers, 'body': json.dumps({'error': 'fleetId required'})}

                # Campaigns can be scoped three ways:
                #   targetArn = 'fleet:<fleetId>'  - fleet-specific campaign copy
                #   targetArn = 'all'              - fleet-agnostic default (seed_decoder_and_campaign.py)
                #   targetArn = 'template'         - base template for cloning
                # The fleet dashboard should surface both fleet-scoped
                # records and the 'all' default; exclude templates.
                fleet_resp = camp_table.query(
                    IndexName='targetArn-index',
                    KeyConditionExpression='targetArn = :t',
                    ExpressionAttributeValues={':t': f'fleet:{fleet_id}'}
                )
                all_resp = camp_table.query(
                    IndexName='targetArn-index',
                    KeyConditionExpression='targetArn = :t',
                    ExpressionAttributeValues={':t': 'all'}
                )
                campaigns = fleet_resp.get('Items', []) + all_resp.get('Items', [])
                _dd = lambda o: float(o) if hasattr(o, 'as_integer_ratio') else str(o)
                return {'statusCode': 200, 'headers': cors_headers,
                    'body': json.dumps({'campaigns': campaigns, 'count': len(campaigns)}, default=_dd)}
            except Exception as e:
                return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

        if path == '/api/v1/fleet-campaigns/assign' and method == 'POST':
            try:
                stage = os.environ.get('DEPLOYMENT_STAGE', 'prod')
                camp_table = dynamodb.Table(f'cms-{stage}-campaigns')
                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME', f'cms-{stage}-storage-vehicles'))
                body = json.loads(event.get('body', '{}'))
                fleet_id = body.get('fleetId')
                campaign_name = body.get('campaignName')
                if not fleet_id or not campaign_name:
                    return {'statusCode': 400, 'headers': cors_headers, 'body': json.dumps({'error': 'fleetId and campaignName required'})}

                # AUTHZ — this route had NO authorization check until 2026-09-05.
                # It writes a fleet-level campaign record for a caller-supplied
                # fleetId, then fans out one vehicle-level record per vehicle in
                # that fleet with status=RUNNING. fleetId is trivially
                # enumerable: the sibling GET at :7632 returns fleet lists.
                #
                # Scoped per-fleet, NOT admin-only. This diverges deliberately
                # from the sibling fleet-actions approve/reject fix (e7aebff0),
                # which chose is_admin because the action-queue schema could not
                # support scoping — recall/rebalancing rows carry no vehicleId
                # and no row carries fleetId. That constraint does not apply
                # here: fleetId arrives in the request body, so a fleet-operator
                # can be held to their own fleets.
                #
                # Two checks, and both are load-bearing:
                #   1. is_read_only — fleet-viewer/fleet-guest are read-only per
                #      :1281. Without this a viewer holding the target fleet in
                #      custom:fleetIds would pass check 2 and gain a write.
                #   2. fleet membership — fails closed for a groupless token and
                #      for an operator with no custom:fleetIds, because an empty
                #      user_fleet_ids makes `fleet_id not in []` true. That is
                #      the CMS P0 shape-2 defect (absent claim widening scope)
                #      not being reintroduced here.
                #
                # See issues/2026-09-04-fleet-campaigns-post-routes-missing-authz/
                # and test_fleet_campaigns_authz.py.
                if is_read_only:
                    return {'statusCode': 403, 'headers': cors_headers,
                            'body': json.dumps({'error': 'Assigning fleet campaigns requires a write-capable role'})}
                if not is_admin and fleet_id not in user_fleet_ids:
                    return {'statusCode': 403, 'headers': cors_headers,
                            'body': json.dumps({'error': 'Assigning a fleet campaign requires platform-admin or membership of the target fleet'})}

                # Get template campaign
                template_resp = camp_table.query(
                    IndexName='targetArn-index',
                    KeyConditionExpression='targetArn = :t',
                    FilterExpression='campaignName = :n',
                    ExpressionAttributeValues={':t': 'template', ':n': campaign_name}
                )
                templates = template_resp.get('Items', [])
                if not templates:
                    return {'statusCode': 404, 'headers': cors_headers, 'body': json.dumps({'error': f'Template campaign {campaign_name} not found'})}
                template = templates[0]

                # Create fleet-level campaign record
                fleet_campaign_id = f'{campaign_name}-fleet:{fleet_id}'
                now = datetime.now(timezone.utc).isoformat()
                template_owner = template.get('owner', 'oem')
                fleet_record = {
                    'campaignId': fleet_campaign_id,
                    'campaignName': campaign_name,
                    'targetArn': f'fleet:{fleet_id}',
                    'targetType': 'FLEET',
                    'fleetId': fleet_id,
                    'status': 'RUNNING',
                    'createdAt': now,
                    'owner': template_owner,
                    'decoderManifestId': template.get('decoderManifestId', 'cms-fleet-v3'),
                    'collectionScheme': template.get('collectionScheme', {}),
                    'signalsToCollect': template.get('signalsToCollect', []),
                }
                camp_table.put_item(Item=fleet_record)

                # Get all vehicles in fleet
                fleet_resp = vehicles_table.scan(
                    FilterExpression='fleetId = :f',
                    ExpressionAttributeValues={':f': fleet_id}
                )
                fleet_vehicles = fleet_resp.get('Items', [])

                # Fan out: create vehicle-level records with sourceFleetId
                created = 0
                for v in fleet_vehicles:
                    vin = v.get('vin', v.get('vehicleId', ''))
                    vid = v.get('vehicleId', '')
                    vehicle_campaign_id = f'{campaign_name}-vehicle:{vin}'
                    vehicle_record = {
                        'campaignId': vehicle_campaign_id,
                        'campaignName': campaign_name,
                        'targetArn': f'vehicle:{vin}',
                        'targetType': 'VEHICLE',
                        'sourceFleetId': fleet_id,
                        'sourceFleetCampaignId': fleet_campaign_id,
                        'status': 'RUNNING',
                        'createdAt': now,
                        'owner': template_owner,
                        'decoderManifestId': template.get('decoderManifestId', 'cms-fleet-v3'),
                        'collectionScheme': template.get('collectionScheme', {}),
                        'signalsToCollect': template.get('signalsToCollect', []),
                    }
                    camp_table.put_item(Item=vehicle_record)
                    created += 1

                return {'statusCode': 200, 'headers': cors_headers,
                    'body': json.dumps({'success': True, 'fleetCampaignId': fleet_campaign_id, 'vehiclesAssigned': created})}
            except Exception as e:
                import traceback; traceback.print_exc()
                return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

        if path == '/api/v1/fleet-campaigns/status' and method == 'POST':
            try:
                stage = os.environ.get('DEPLOYMENT_STAGE', 'prod')
                camp_table = dynamodb.Table(f'cms-{stage}-campaigns')
                body = json.loads(event.get('body', '{}'))
                campaign_id = body.get('campaignId')
                new_status = body.get('status')  # RUNNING, SUSPENDED, STOPPED
                if not campaign_id or not new_status:
                    return {'statusCode': 400, 'headers': cors_headers, 'body': json.dumps({'error': 'campaignId and status required'})}

                # AUTHZ — this route had NO authorization check until 2026-09-05.
                # It sets an arbitrary status on any campaignId AND on every
                # child vehicle record beneath it (the query at :7758).
                #
                # The fleet is derived with the SAME expression the route already
                # uses below to find its children, so the authz decision and the
                # write scope agree by construction. Deriving it a second way
                # would create two conventions that can drift apart — the defect
                # class behind the DENY-policy-name and EventBridge-bus-name
                # findings in the sibling DMS repo.
                #
                # Fail-closed on an underivable fleet: a campaignId with no
                # '-fleet:' segment yields '', which denies for non-admins.
                # "Cannot determine the fleet" must not mean "permitted" — that
                # would let a bare campaign id bypass the check entirely.
                #
                # See issues/2026-09-04-fleet-campaigns-post-routes-missing-authz/
                # and test_fleet_campaigns_authz.py.
                if is_read_only:
                    return {'statusCode': 403, 'headers': cors_headers,
                            'body': json.dumps({'error': 'Changing fleet campaign status requires a write-capable role'})}
                _campaign_fleet = campaign_id.rsplit('-fleet:', 1)[1] if '-fleet:' in campaign_id else ''
                if not is_admin and (not _campaign_fleet or _campaign_fleet not in user_fleet_ids):
                    return {'statusCode': 403, 'headers': cors_headers,
                            'body': json.dumps({'error': 'Changing a fleet campaign status requires platform-admin or membership of the campaign\'s fleet'})}

                # Update fleet-level record.
                # ConditionExpression prevents upsert: a status update on a
                # non-existent campaignId must 404, not silently create an
                # ownerless row that becomes undeletable once the fail-closed
                # guard ships (spec 2026-09-15-cms-cs-campaign-ownership, FG4.2).
                # No owner field is set because this call cannot create a row —
                # (attribute_exists condition rejects absent keys) — it only
                # updates an existing row's status field.
                from botocore.exceptions import ClientError as _StatusClientError
                try:
                    camp_table.update_item(
                        Key={'campaignId': campaign_id},
                        UpdateExpression='SET #s = :s',
                        ExpressionAttributeNames={'#s': 'status'},
                        ExpressionAttributeValues={':s': new_status},
                        ConditionExpression='attribute_exists(campaignId)',
                    )
                except _StatusClientError as _sce:
                    if _sce.response['Error']['Code'] == 'ConditionalCheckFailedException':
                        return {'statusCode': 404, 'headers': cors_headers,
                                'body': json.dumps({'error': f'Campaign not found: {campaign_id}'})}
                    raise

                # Update all child vehicle records
                fleet_id_part = campaign_id.rsplit('-fleet:', 1)[1] if '-fleet:' in campaign_id else ''
                child_resp = camp_table.query(
                    IndexName='sourceFleetId-index',
                    KeyConditionExpression='sourceFleetId = :f',
                    ExpressionAttributeValues={':f': fleet_id_part}
                )
                updated = 0
                for child in child_resp.get('Items', []):
                    # No owner field needed — same ConditionExpression guard as fleet record
                    # prevents upsert; child rows come from a query and must exist.
                    camp_table.update_item(
                        Key={'campaignId': child['campaignId']},
                        UpdateExpression='SET #s = :s',
                        ExpressionAttributeNames={'#s': 'status'},
                        ExpressionAttributeValues={':s': new_status},
                        ConditionExpression='attribute_exists(campaignId)',
                    )
                    updated += 1

                return {'statusCode': 200, 'headers': cors_headers,
                    'body': json.dumps({'success': True, 'campaignId': campaign_id, 'status': new_status, 'vehiclesUpdated': updated})}
            except Exception as e:
                return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

        # (Retired 2026-09-06) The `/api/v1/documents` VFO KB reader route was
        # removed per spec `2026-09-05-cms-vfo-teardown` — the VFO knowledge-base
        # S3 bucket has been deleted; the fleet-view surface is now served by
        # `services/fleet_intelligence/` (Tier 1 deterministic renderer).

        # ── TCO / Cost endpoints ───────────────────────────────────────────
        # Shared helper to coerce Decimals recursively
        def _dec2num(obj):
            import decimal
            if isinstance(obj, decimal.Decimal):
                return float(obj) if obj % 1 else int(obj)
            if isinstance(obj, dict):
                return {k: _dec2num(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_dec2num(i) for i in obj]
            return obj

        stage = os.environ.get('DEPLOYMENT_STAGE', 'prod')
        costs_table_name = f'cms-{stage}-storage-vehicle-costs'
        charging_table_name = f'cms-{stage}-storage-charging-sessions'
        locations_table_name = f'cms-{stage}-storage-location-snapshots'

        if path == '/api/v1/tco/summary' and method == 'GET':
            try:
                from boto3.dynamodb.conditions import Attr
                costs_table = dynamodb.Table(costs_table_name)
                now = datetime.now(timezone.utc)
                this_month = now.strftime('%Y-%m')
                # Scan all rows for current month (small table: 50 vehicles x ~24 months)
                resp = costs_table.scan(FilterExpression=Attr('yearMonth').eq(this_month))
                items = resp.get('Items', [])
                total_cost = sum(float(i.get('totalCost', 0)) for i in items)
                total_miles = sum(float(i.get('distanceMiles', 0)) for i in items)
                maint_cost = sum(float(i.get('maintenanceCost', 0)) for i in items)
                vehicle_count = len(set(i['vehicleId'] for i in items))
                avg_cost_per_mile = round(total_cost / total_miles, 3) if total_miles > 0 else 0.0
                avg_cost_per_vehicle = round(total_cost / vehicle_count, 2) if vehicle_count > 0 else 0.0
                maint_ratio = round((maint_cost / total_cost) * 100, 1) if total_cost > 0 else 0.0
                return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({
                    'totalCostMTD': round(total_cost, 2),
                    'avgCostPerMile': avg_cost_per_mile,
                    'avgCostPerVehicle': avg_cost_per_vehicle,
                    'maintenanceRatio': maint_ratio,
                    'vehicleCount': vehicle_count,
                    'yearMonth': this_month,
                })}
            except Exception as e:
                return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

        if path == '/api/v1/tco/breakdown' and method == 'GET':
            try:
                from boto3.dynamodb.conditions import Attr
                costs_table = dynamodb.Table(costs_table_name)
                now = datetime.now(timezone.utc)
                this_month = now.strftime('%Y-%m')
                resp = costs_table.scan(FilterExpression=Attr('yearMonth').eq(this_month))
                items = resp.get('Items', [])
                breakdown = {
                    'fuel': sum(float(i.get('fuelCost', 0)) for i in items),
                    'charging': sum(float(i.get('chargingCost', 0)) for i in items),
                    'maintenance': sum(float(i.get('maintenanceCost', 0)) for i in items),
                    'insurance': sum(float(i.get('insuranceCost', 0)) for i in items),
                    'depreciation': sum(float(i.get('depreciationCost', 0)) for i in items),
                }
                total = sum(breakdown.values())
                return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({
                    'breakdown': {k: round(v, 2) for k, v in breakdown.items()},
                    'total': round(total, 2),
                    'yearMonth': this_month,
                })}
            except Exception as e:
                return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

        if path == '/api/v1/tco/trend' and method == 'GET':
            try:
                costs_table = dynamodb.Table(costs_table_name)
                resp = costs_table.scan()
                items = resp.get('Items', [])
                from collections import defaultdict
                by_month = defaultdict(lambda: {'totalCost': 0.0, 'distance': 0.0})
                for i in items:
                    ym = i.get('yearMonth')
                    if ym:
                        by_month[ym]['totalCost'] += float(i.get('totalCost', 0))
                        by_month[ym]['distance'] += float(i.get('distanceMiles', 0))
                months = sorted(by_month.keys())[-6:]  # last 6 months
                trend = [{
                    'yearMonth': m,
                    'totalCost': round(by_month[m]['totalCost'], 2),
                    'costPerMile': round(by_month[m]['totalCost'] / by_month[m]['distance'], 3) if by_month[m]['distance'] > 0 else 0.0,
                } for m in months]
                return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'trend': trend})}
            except Exception as e:
                return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

        if path == '/api/v1/tco/outliers' and method == 'GET':
            try:
                from boto3.dynamodb.conditions import Attr
                costs_table = dynamodb.Table(costs_table_name)
                now = datetime.now(timezone.utc)
                this_month = now.strftime('%Y-%m')
                resp = costs_table.scan(FilterExpression=Attr('yearMonth').eq(this_month))
                items = resp.get('Items', [])
                # Compute fleet avg cost/mile
                eligible = [i for i in items if float(i.get('distanceMiles', 0)) > 0]
                if not eligible:
                    return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'outliers': [], 'fleetAvgCostPerMile': 0})}
                avg_cpm = sum(float(i.get('costPerMile', 0)) for i in eligible) / len(eligible)
                outliers = []
                for i in eligible:
                    cpm = float(i.get('costPerMile', 0))
                    if cpm > avg_cpm * 1.25:
                        dev_pct = round(((cpm - avg_cpm) / avg_cpm) * 100, 1)
                        outliers.append({
                            'vehicleId': i['vehicleId'],
                            'costPerMile': round(cpm, 3),
                            'deviationPct': dev_pct,
                            'totalCost': round(float(i.get('totalCost', 0)), 2),
                            'fleetId': i.get('fleetId', ''),
                        })
                outliers.sort(key=lambda x: x['deviationPct'], reverse=True)
                return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({
                    'outliers': outliers[:10],
                    'fleetAvgCostPerMile': round(avg_cpm, 3),
                })}
            except Exception as e:
                return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

        # ── Rebalancing endpoints ──────────────────────────────────────────
        if path == '/api/v1/rebalancing/locations' and method == 'GET':
            try:
                from boto3.dynamodb.conditions import Key
                locs_table = dynamodb.Table(locations_table_name)
                # Most recent date
                resp = locs_table.scan()
                items = resp.get('Items', [])
                if not items:
                    return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'locations': [], 'snapshotDate': None})}
                latest_date = max(i['snapshotDate'] for i in items)
                latest_items = [i for i in items if i['snapshotDate'] == latest_date]
                total_vehicles = sum(int(i.get('totalVehicles', 0)) for i in latest_items)
                total_active = sum(int(i.get('activeVehicles', 0)) for i in latest_items)
                total_idle = sum(int(i.get('idleVehicles', 0)) for i in latest_items)
                fleet_util = round((total_active / total_vehicles) * 100, 1) if total_vehicles > 0 else 0
                surplus_locs = sum(1 for i in latest_items if i.get('status') == 'surplus')
                deficit_locs = sum(1 for i in latest_items if i.get('status') == 'deficit')
                return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({
                    'locations': _dec2num(latest_items),
                    'snapshotDate': latest_date,
                    'summary': {
                        'totalVehicles': total_vehicles,
                        'totalActive': total_active,
                        'totalIdle': total_idle,
                        'fleetUtilizationPct': fleet_util,
                        'surplusLocations': surplus_locs,
                        'deficitLocations': deficit_locs,
                    },
                })}
            except Exception as e:
                return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

        # ── Charging endpoints ─────────────────────────────────────────────
        if path == '/api/v1/charging/summary' and method == 'GET':
            try:
                from boto3.dynamodb.conditions import Attr
                charge_table = dynamodb.Table(charging_table_name)
                now = datetime.now(timezone.utc)
                today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                this_month = now.strftime('%Y-%m')
                resp = charge_table.scan()
                all_sessions = resp.get('Items', [])
                today_sessions = [s for s in all_sessions if s.get('sessionStartTime', '') >= today_start]
                month_sessions = [s for s in all_sessions if s.get('sessionStartTime', '').startswith(this_month)]
                kwh_today = sum(float(s.get('kwhDelivered', 0)) for s in today_sessions)
                cost_mtd = sum(float(s.get('sessionCost', 0)) for s in month_sessions)
                bev_vehicles = len(set(s['vehicleId'] for s in all_sessions))
                return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({
                    'sessionsToday': len(today_sessions),
                    'kwhToday': round(kwh_today, 1),
                    'costMTD': round(cost_mtd, 2),
                    'bevVehicles': bev_vehicles,
                    'totalSessionsMTD': len(month_sessions),
                })}
            except Exception as e:
                return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

        if path == '/api/v1/charging/sessions' and method == 'GET':
            try:
                charge_table = dynamodb.Table(charging_table_name)
                resp = charge_table.scan(Limit=100)
                items = resp.get('Items', [])
                items.sort(key=lambda x: x.get('sessionStartTime', ''), reverse=True)
                return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({
                    'sessions': _dec2num(items[:50]),
                    'count': len(items),
                })}
            except Exception as e:
                return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

        # ── Summary / count endpoints that dashboards use for pagination totals ──
        if path == '/api/v1/trips/count' and method == 'GET':
            try:
                stage = os.environ.get('DEPLOYMENT_STAGE', 'prod')
                trips_tbl = dynamodb.Table(os.environ.get('TRIPS_TABLE_NAME', f'cms-{stage}-storage-trips'))
                total = 0
                resp = trips_tbl.scan(Select='COUNT')
                total += resp.get('Count', 0)
                while 'LastEvaluatedKey' in resp:
                    resp = trips_tbl.scan(Select='COUNT', ExclusiveStartKey=resp['LastEvaluatedKey'])
                    total += resp.get('Count', 0)
                return {'statusCode': 200, 'headers': cors_headers,
                        'body': json.dumps({'total': total, 'count': total})}
            except Exception as e:
                return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

        if path == '/api/v1/safety-events' and method == 'GET':
            # Fleet-wide safety events (the driver-specific path is handled earlier at line ~1839)
            try:
                stage = os.environ.get('DEPLOYMENT_STAGE', 'prod')
                safety_tbl = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME', f'cms-{stage}-storage-safety-events'))
                limit = min(int(query_params.get('limit', 50)), 200)
                vehicle_id = query_params.get('vehicleId')
                if vehicle_id:
                    resp = safety_tbl.query(
                        IndexName='vehicleId-timestamp-index',
                        KeyConditionExpression='vehicleId = :v',
                        ExpressionAttributeValues={':v': vehicle_id},
                        ScanIndexForward=False,
                        Limit=limit,
                    )
                else:
                    resp = safety_tbl.scan(Limit=limit)
                items = resp.get('Items', [])
                return {'statusCode': 200, 'headers': cors_headers,
                        'body': json.dumps({'events': items, 'count': len(items)}, default=str)}
            except Exception as e:
                return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

        # PATCH /api/v1/maintenance-alerts/{alertId} — update an alert's
        # status.  Used by ScheduleServiceModal after it posts a service-
        # history row so the Service tab shows the alert as SCHEDULED rather
        # than OPEN.  Minimal: accepts `status` in the body, nothing else.
        if path.startswith('/api/v1/maintenance-alerts/') and method == 'PATCH':
            denied = _deny_viewer()
            if denied:
                return denied
            alert_id = path.split('/')[-1]
            try:
                body = json.loads(event.get('body', '{}') or '{}')
                new_status = body.get('status')
                if not new_status:
                    return {'statusCode': 400, 'headers': cors_headers,
                            'body': json.dumps({'error': 'status is required'})}

                maint_tbl = dynamodb.Table(
                    os.environ.get('MAINTENANCE_ALERTS_TABLE_NAME')
                )
                maint_tbl.update_item(
                    Key={'alertId': alert_id},
                    UpdateExpression='SET #s = :s, updatedAt = :u',
                    ExpressionAttributeNames={'#s': 'status'},
                    ExpressionAttributeValues={
                        ':s': new_status,
                        ':u': datetime.now(timezone.utc).isoformat(),
                    },
                )
                return {'statusCode': 200, 'headers': cors_headers,
                        'body': json.dumps({'alertId': alert_id, 'status': new_status})}
            except Exception as e:
                import traceback
                print(f"PATCH maintenance-alert failed: {e}\n{traceback.format_exc()}")
                return {'statusCode': 500, 'headers': cors_headers,
                        'body': json.dumps({'error': str(e)})}

        if path == '/api/v1/maintenance-alerts/stats' and method == 'GET':
            try:
                stage = os.environ.get('DEPLOYMENT_STAGE', 'prod')
                maint_tbl = dynamodb.Table(os.environ.get('MAINTENANCE_ALERTS_TABLE_NAME', f'cms-{stage}-storage-maintenance-alerts'))
                items = []
                resp = maint_tbl.scan()
                items.extend(resp.get('Items', []))
                while 'LastEvaluatedKey' in resp:
                    resp = maint_tbl.scan(ExclusiveStartKey=resp['LastEvaluatedKey'])
                    items.extend(resp.get('Items', []))
                by_severity = {}
                by_status = {}
                for m in items:
                    s = m.get('severity', 'UNKNOWN')
                    st = m.get('status', 'UNKNOWN')
                    by_severity[s] = by_severity.get(s, 0) + 1
                    by_status[st] = by_status.get(st, 0) + 1
                return {'statusCode': 200, 'headers': cors_headers,
                        'body': json.dumps({
                            'total': len(items),
                            'bySeverity': by_severity,
                            'byStatus': by_status,
                            'open': by_status.get('OPEN', 0),
                            'critical': by_severity.get('CRITICAL', 0) + by_severity.get('HIGH', 0),
                        }, default=str)}
            except Exception as e:
                return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

        # ── POST /api/dispatch ────────────────────────────────────────────────
        #
        # CMS-side dispatch proxy: forwards a fleet-operator's repair-order
        # creation request to the DMS repair-orders API.
        #
        # F-G2b (security W2) — this endpoint carries the authorization that
        # makes the Draft-inclusive active-status set safe. A fleet-operator
        # can create a Draft RO naming any VIN; Draft ∈ RO_ACTIVE_STATUSES
        # means every technician at the chosen dealer gains STATIONARY invoke
        # on that vehicle. To close the composition hole:
        #
        #   1. Caller role check: only fleet-operator or platform-admin may
        #      dispatch (dms-technician, fleet-viewer, fleet-guest rejected).
        #   2. VIN fleet check: vehicleId → fleetId lookup in the vehicles
        #      table → must be in the caller's fleet assignments. Fail closed
        #      on a missing or unresolvable vehicleId.
        #   3. No outbound DMS call is issued until BOTH checks pass.
        #
        # F14 — _RefuseRedirects is defined at module level and shared here;
        # it was hoisted out of the dealers branch specifically because more
        # token-bearing outbound calls were coming. Not reusing it re-opens
        # the redirect-to-http hole the class was written to close.
        #
        # JWT logging prohibition: auth_header is deliberately never passed
        # to any print() or logging call in this branch.
        if path == '/api/dispatch' and method == 'POST':
            # ── 1. Endpoint validation (same pattern as /api/v1/dealers) ──
            dms_endpoint_dispatch = os.environ.get('DMS_API_ENDPOINT', '').strip()
            if not dms_endpoint_dispatch:
                print('POST /api/dispatch: DMS_API_ENDPOINT is not set')
                return {
                    'statusCode': 503,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'error': 'Dispatch unavailable',
                        'detail': 'DMS_API_ENDPOINT is not configured on this stage.',
                    })
                }
            # One-shot https:// check: the caller's JWT is about to travel over
            # this URL, so the scheme is a security property, not a style preference.
            if not dms_endpoint_dispatch.startswith('https://'):
                print('POST /api/dispatch: DMS_API_ENDPOINT is not https')
                return {
                    'statusCode': 503,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'error': 'Dispatch unavailable',
                        'detail': 'DMS_API_ENDPOINT must be an https:// URL.',
                    })
                }

            # ── 2. Caller role check ───────────────────────────────────────
            # Only fleet-operator or platform-admin may dispatch.
            # dms-technician is explicitly excluded: they receive, not dispatch
            # (T2.7 constraint). fleet-viewer/fleet-guest are read-only and
            # structurally cannot dispatch.
            _is_dispatch_caller = is_admin or ('fleet-operator' in user_groups)
            if not _is_dispatch_caller:
                return {
                    'statusCode': 403,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'error': 'Dispatch requires fleet-operator or platform-admin.',
                    })
                }

            # ── 3. JWT extraction (case-insensitive, same as dealers proxy) ─
            _dispatch_headers = event.get('headers') or {}
            auth_header_dispatch = ''
            for _k, _v in _dispatch_headers.items():
                if _k and _k.lower() == 'authorization':
                    auth_header_dispatch = _v or ''
                    break
            if not auth_header_dispatch:
                return {
                    'statusCode': 401,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'error': 'Missing Authorization header',
                        'detail': 'Dispatch requires the caller to be authenticated.',
                    })
                }

            # ── 4. VIN fleet check (_authorize_per_vin, F-G2b) ────────────
            # Resolve vehicleId → fleetId, then check against user_fleet_ids.
            # Fail closed: admin bypasses; non-admin with no or wrong fleet → 403.
            # The DMS call is NOT issued until this check passes.
            _dispatch_body: dict = {}
            try:
                _dispatch_body = json.loads(event.get('body') or '{}')
            except (json.JSONDecodeError, TypeError):
                return {
                    'statusCode': 400,
                    'headers': cors_headers,
                    'body': json.dumps({'error': 'Request body must be valid JSON.'}),
                }

            _dispatch_vehicle_id = _dispatch_body.get('vehicleId', '')
            if not _dispatch_vehicle_id:
                return {
                    'statusCode': 400,
                    'headers': cors_headers,
                    'body': json.dumps({'error': 'vehicleId is required in the dispatch body.'}),
                }

            # ── 4a. Vehicle lookup (all callers) ──────────────────────────
            # Always resolve the vehicle record so that (a) non-admin callers can
            # be checked against their fleet, and (b) ALL callers have their
            # outbound body's vehicle_vin replaced with the CMS-canonical value
            # (F-G6b: defence-in-depth against a caller supplying a mismatched VIN).
            _vehicles_tbl = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME', ''))
            _vehicle_resp = _vehicles_tbl.get_item(Key={'vehicleId': _dispatch_vehicle_id})
            _vehicle_item = _vehicle_resp.get('Item')
            if not _vehicle_item:
                # Unknown vehicleId: fail closed with 404 (does not reveal whether
                # the vehicle exists in a different fleet).
                return {
                    'statusCode': 404,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'error': 'Vehicle not found.',
                    })
                }

            if not is_admin:
                # Non-admin callers must own the vehicle's fleet.
                _vehicle_fleet_id = _vehicle_item.get('fleetId', '')
                # _check_fleet_access is defined as a closure above this block
                # (index.py ~1505) and uses user_fleet_ids from the outer scope.
                _fleet_denied = _check_fleet_access(_vehicle_fleet_id)
                if _fleet_denied is not None:
                    # Return 403 without leaking internal fleet IDs or VINs.
                    return {
                        'statusCode': 403,
                        'headers': cors_headers,
                        'body': json.dumps({
                            'error': 'Access denied: vehicle is not in your fleet.',
                        })
                    }

            # ── 4b. VIN re-derivation (F-G6b) ─────────────────────────────
            # Overwrite the client-supplied vehicle_vin with the CMS-canonical
            # value from the vehicle record.  A caller who sends vehicleId=X but
            # vehicle_vin=Y (where Y != X.vin) would otherwise cause DMS to create
            # a Draft RO for an attacker-chosen VIN.  We overwrite silently rather
            # than rejecting on mismatch to avoid an enumeration channel.
            # If the vehicle row is missing its vin attribute the data-model is
            # inconsistent server-side; fail closed 502 rather than forwarding a
            # body with a caller-controlled VIN.
            _canonical_vin = _vehicle_item.get('vin')
            if not _canonical_vin:
                return {
                    'statusCode': 502,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'error': 'vehicle metadata incomplete',
                    })
                }
            _dispatch_body['vehicle_vin'] = _canonical_vin

            # ── 5. Outbound DMS call (reusing _RefuseRedirects, F14) ───────
            # _RefuseRedirects is defined at module level and must be reused
            # here — see the class docstring for the security rationale.
            _dispatch_opener = urllib.request.build_opener(_RefuseRedirects)
            # MUST be '/api/dms/fleet/repair-orders', NOT '/api/dms/repair-orders'.
            #
            # The fleet surface (dms_api_stack.py:1247 -> repair_orders.
            # create_fleet_repair_order) is the CMS-originated booking route: it is the
            # only handler that writes initiated_by='cms_booking' and the `evidence` map
            # v1.5 T2.7 depends on, and its group check admits `fleet-operator`
            # (platform-admin bypasses).
            #
            # '/api/dms/repair-orders' exists ONLY as a path prefix for
            # '/{roId}/notes' (dms_api_stack.py:1228-1232) and has NO POST method, so
            # posting there returns API Gateway's 403 'Missing Authentication Token'
            # for an unmatched route — which the handler below used to pass through as
            # an auth failure, disguising a routing bug as a permissions problem.
            # Dispatch was broken for every caller from its introduction until
            # 2026-09-24. See issues/2026-09-24-cms-dispatch-posts-to-unrouted-dms-path/.
            #
            # Every other CMS->DMS call in this file already used the fleet surface
            # (:8403, :8743, :9196) — this one line was a lone divergence, with the
            # correct value present three times elsewhere in the same module.
            #
            # Pinned by tests/test_dispatch_outbound_route.py — do not edit this literal
            # without reading that test.
            _dms_dispatch_url = dms_endpoint_dispatch.rstrip('/') + '/api/dms/fleet/repair-orders'
            try:
                _dms_req = urllib.request.Request(
                    _dms_dispatch_url,
                    data=json.dumps(_dispatch_body).encode('utf-8'),
                    method='POST',
                    headers={
                        'Authorization': auth_header_dispatch,
                        'Content-Type': 'application/json',
                    },
                )
                with _dispatch_opener.open(_dms_req, timeout=8) as _dms_resp:
                    _dms_payload = json.loads(_dms_resp.read().decode('utf-8'))

                # ── Stamp dispatched_ro_id + dispatched_at onto the session ──
                #
                # Spec 2026-09-24-cms-diagnostics-tab-ia-redesign § Constraint
                # amendment required by Q6. This is the ONE additive write the
                # amended constraint allows. The RO exists at this point; losing
                # the link is recoverable (the RO is in DMS), while telling the
                # operator their dispatch FAILED would be wrong — the DMS 201
                # has already been issued. Stamp failure MUST NOT fail the
                # dispatch.
                _stamped_ro_id = _dms_payload.get('ro_id', '')
                _session_id_to_stamp = (
                    _dispatch_body.get('evidence') or {}
                ).get('sessionId', '')
                if _stamped_ro_id and _session_id_to_stamp:
                    try:
                        _stamp_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                        _commands_tbl = dynamodb.Table(
                            os.environ.get(
                                'COMMANDS_TABLE',
                                f'cms-{os.environ.get("DEPLOYMENT_STAGE", "prod")}-storage-commands',
                            )
                        )
                        _stamp_resp = _commands_tbl.query(
                            IndexName='vehicleId-index',
                            KeyConditionExpression='vehicleId = :v',
                            ExpressionAttributeValues={':v': _dispatch_vehicle_id},
                        )
                        for _cmd_row in _stamp_resp.get('Items', []):
                            if _cmd_row.get('session_id') != _session_id_to_stamp:
                                continue
                            _cmd_id = _cmd_row.get('commandId', '')
                            if not _cmd_id:
                                continue
                            _commands_tbl.update_item(
                                Key={'commandId': _cmd_id},
                                UpdateExpression=(
                                    'SET dispatched_ro_id = :ro_id, '
                                    'dispatched_at = :at_ts'
                                ),
                                ExpressionAttributeValues={
                                    ':ro_id': _stamped_ro_id,
                                    ':at_ts': _stamp_at,
                                },
                            )
                    except Exception as _stamp_exc:
                        print(
                            'POST /api/dispatch: stamp_session_failed — '
                            f'session={_session_id_to_stamp!r} ro_id={_stamped_ro_id!r} '
                            f'error={_stamp_exc!r}'
                        )

                return {
                    'statusCode': 201,
                    'headers': cors_headers,
                    'body': json.dumps(_dms_payload),
                }
            except urllib.error.HTTPError as _he:
                # Pass the status through for 401/403 (auth context); flatten
                # others to 502 (DMS unavailable). DMS body deliberately NOT
                # forwarded — it is another service's error shape and could
                # carry detail CMS has not reviewed.
                #
                # EXCEPTION — API Gateway overloads 403. It returns
                # 403 {"message":"Missing Authentication Token"} for a path/method it
                # does not have, which is indistinguishable from a genuine DMS
                # authorization refusal if the status alone is passed through. That
                # ambiguity cost two investigations: an operator and an architect both
                # read the resulting "DMS responded 403." as a Cognito groups problem
                # while the actual defect was CMS posting to an unrouted path
                # (issues/2026-09-24-cms-dispatch-posts-to-unrouted-dms-path/).
                #
                # So: inspect the body for that marker and report a routing failure
                # instead. The body is READ but still never FORWARDED — the security
                # rationale above is unchanged; CMS emits its own message.
                _he_marker = ''
                try:
                    _he_marker = _he.read(512).decode('utf-8', 'replace')
                except Exception:
                    pass
                if _he.code == 403 and 'Missing Authentication Token' in _he_marker:
                    print(
                        'POST /api/dispatch: DMS answered 403 Missing Authentication '
                        f'Token — {_dms_dispatch_url} is not a routed DMS method. '
                        'This is a routing defect, NOT an authorization failure.'
                    )
                    return {
                        'statusCode': 502,
                        'headers': cors_headers,
                        'body': json.dumps({
                            'error': 'Dispatch failed',
                            'detail': (
                                'The DMS dispatch endpoint is not routed on this '
                                'stage. This is a configuration defect, not a '
                                'permissions problem — retrying or changing your '
                                'access will not help.'
                            ),
                        })
                    }
                print(f'POST /api/dispatch: DMS returned {_he.code}')
                return {
                    'statusCode': _he.code if _he.code in (401, 403) else 502,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'error': 'Dispatch failed',
                        'detail': f'DMS responded {_he.code}.',
                    })
                }
            except Exception as _e:
                print(f'POST /api/dispatch: DMS call failed: {_e}')
                return {
                    'statusCode': 504,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'error': 'Dispatch failed',
                        'detail': 'Could not reach the DMS dispatch endpoint.',
                    })
                }

        # ── Connected Services subscription feed (spec
        # 2026-09-10-cms-connected-services-consumer, T2.3) ──────────────────
        #
        # Three routes proxying CMS's OWN subscriber account against the
        # producer's subscription plane. Placed at the end of the chain, before
        # the 404, because that is the one region of this 9000-line function no
        # other active spec is editing — six slugs are on currentspec.md.
        #
        # WHAT THIS BLOCK OWNS, AND THE PROXY DELIBERATELY DOES NOT: the entire
        # authorization decision. `connected_services_proxy`'s three public
        # functions do not filter by fleet scope and say so in their docstrings.
        # This is the DMS-proxy structure (index.py's service-history route
        # applies CMS's check on CMS's side of the call, not by asking the
        # upstream to scope for it), and it is what makes the boundary auditable:
        # the producer enforces that CMS's subscriber account sees only its own
        # subscription's VINs; CMS enforces that a given operator sees only their
        # own fleet's. Neither check is the other's.
        #
        # NOT A CACHE READ. Spec D3 describes cache-then-check against
        # `cms-{stage}-storage-cs-feed-cache-*`. That table is deployed and the
        # proxy has no code that reads or writes it, so this block filters the
        # LIVE producer response. D3's authorization property is fully satisfied
        # either way — the check is CMS-side and independent — but its freshness
        # and TTL property is not implemented, and claiming otherwise here would
        # be the more expensive mistake. Tracked as its own task; the missing
        # BatchWriteItem grant is asserted ABSENT in
        # stacks/tests/test_ui_stack_connected_services_consumer.py so the cache
        # cannot land half-wired.
        if path == _CS_FEED_ROUTE or path.startswith(_CS_FEED_ROUTE + '/'):
            _cs_suffix = path[len(_CS_FEED_ROUTE):].strip('/')

            # Config first, and as its own failure class. A stage with no
            # producer deployed has CS_PRODUCER_API_ENDPOINT set to '' by design
            # (ui_stack gates the CFN import on DEPLOY_SUBSCRIPTIONS), so this is
            # a SUPPORTED state, not an exceptional one. KeyError comes from
            # from_env for an unset var; ValueError from __post_init__ for an
            # empty or non-https one. Catching only KeyError would turn the most
            # likely real configuration into a 500 that reads as an app bug.
            try:
                _cs_config = connected_services_proxy.ProxyConfig.from_env()
            except (KeyError, ValueError) as _cs_cfg_err:
                print(
                    f'connected-services: configuration unusable: '
                    f'{type(_cs_cfg_err).__name__}'
                )
                return connected_services_proxy.ProxyResult(
                    status_code=502,
                    body={
                        'error': connected_services_proxy.ERROR_CONFIG_MISSING,
                        'detail': 'Connected Services is not configured on this '
                                  'stage.',
                    },
                ).to_lambda_response(cors_headers)

            _cs_tokens = _cs_token_provider()
            if _cs_tokens is None:
                return connected_services_proxy.ProxyResult(
                    status_code=502,
                    body={
                        'error': connected_services_proxy.ERROR_CONFIG_MISSING,
                        'detail': "CMS's Connected Services subscriber "
                                  'credential is unavailable.',
                    },
                ).to_lambda_response(cors_headers)

            # GET — a FILTER, not a gate. A scoped operator is entitled to this
            # route; they are just not entitled to every row in it. Returning
            # 403 would be wrong, and returning everything would be a leak.
            if not _cs_suffix and method == 'GET':
                _cs_result = connected_services_proxy.get_subscription_feed(
                    config=_cs_config,
                    token_provider=_cs_tokens,
                    http_client=_cs_http_client(),
                    # T2.6. Cache-then-producer per D3. The filter below stays
                    # exactly where it was — on the READ side, after this call —
                    # because the cache holds the producer-shaped, unfiltered
                    # payload. Moving the filter to the write side would key one
                    # operator's view under a subscription-scoped primary key and
                    # serve it to the next operator.
                    cache=_cs_feed_cache(_cs_config),
                )
                if _cs_result.status_code == 200:
                    _cs_result = connected_services_proxy.ProxyResult(
                        status_code=200,
                        body=_cs_filter_feed(
                            _cs_result.body, get_allowed_vehicle_ids()
                        ),
                    )
                # Non-200 bodies pass through UNFILTERED and that is deliberate:
                # a 502 producer_shape_drift body has no `records` key, and a
                # filter that read a missing key as an empty list would convert
                # "we could not read the feed" into "this vehicle has no
                # telemetry" — the substitution spec R1 and the DMS proxy's D5
                # rule both exist to prevent. Error bodies carry key names and
                # types only, never producer values (proxy `ShapeDrift`).
                return _cs_result.to_lambda_response(cors_headers)

            # POST / DELETE — a GATE. Both name exactly one VIN, so the answer
            # is binary.
            if _cs_suffix and method in ('POST', 'DELETE'):
                # Read-only principals first. `has_unscoped_access` is
                # `is_admin or is_viewer`, so a fleet-viewer PASSES the scope
                # check below — gating only on scope would let a read-only
                # auditor mutate CMS's subscription. `_deny_viewer()` is the
                # single predicate covering fleet-viewer AND fleet-guest; this
                # file records that five routes once inlined `if is_viewer:` and
                # adding fleet-guest missed every one of them.
                _cs_denied = _deny_viewer()
                if _cs_denied:
                    # Re-emitted through ProxyResult rather than returned
                    # verbatim. `_deny_viewer()` builds a raw dict and is shared
                    # by eight call sites, so it cannot be changed from here
                    # (spec Constraints: additive only, do not modify existing
                    # route handlers) — but its response reaching the client
                    # unwrapped would be a third path on this route missing the
                    # SG5 headers, after the 405 and the scope-denial 403. Its
                    # status and body are preserved exactly; only the headers
                    # are added.
                    return connected_services_proxy.ProxyResult(
                        status_code=_cs_denied['statusCode'],
                        body=json.loads(_cs_denied['body']),
                    ).to_lambda_response(cors_headers)

                # Normalize through the proxy's own allow-list so this route and
                # the producer agree on what a VIN is. Rejecting here rather
                # than after resolution keeps a malformed value a 400 (the
                # caller's error) instead of a 403 (a permissions story) or a
                # 502 (an upstream-outage story, which is what a 16-character
                # VIN produced before T2.1 pinned the length to the producer's).
                _cs_vin = connected_services_proxy._normalize_vin(_cs_suffix)
                if _cs_vin is None:
                    return connected_services_proxy.ProxyResult(
                        status_code=400,
                        body={'error': 'vin format invalid'},
                    ).to_lambda_response(cors_headers)

                if not has_unscoped_access:
                    # `or set()` rather than a later `is not None` guard.
                    # Security-review cycle 1 Suggestion 1: the previous form
                    # was `if _cs_allowed is not None and (...)`, which meant a
                    # None from `get_allowed_vehicle_ids()` skipped the gate
                    # entirely. That is unreachable today — None is returned
                    # only for `has_unscoped_access`, which this branch has
                    # already excluded — but it ties an authorization gate to an
                    # invariant held in another function, and the failure
                    # direction if they ever decouple is OPEN. An empty set
                    # denies everything, which is the correct fail-closed
                    # default for a caller whose fleet resolves to nothing.
                    _cs_allowed = get_allowed_vehicle_ids() or set()
                    _cs_vehicle_id = _cs_resolve_vin_to_vehicle_id(_cs_vin)
                    # None means unresolvable, and unresolvable is a DENY — see
                    # `_cs_resolve_vin_to_vehicle_id` for why the neighbouring
                    # resolver cannot be used for this decision.
                    if _cs_vehicle_id is None or _cs_vehicle_id not in _cs_allowed:
                        print(
                            f'connected-services: denying {method} on '
                            f'{_cs_redact_vin(_cs_vin)} — not in caller fleet '
                            f'scope'
                        )
                        # Through ProxyResult, like every other response on this
                        # route. Security-review cycle 1's Warning caught this
                        # branch shipping without the SG5 headers — the same
                        # defect the general reviewer had found on the 405 path
                        # one cycle earlier. Two independent instances of one
                        # shape is why `test_every_cs_response_goes_through_
                        # proxyresult` now asserts structurally that no raw
                        # response dict exists in this block at all.
                        return connected_services_proxy.ProxyResult(
                            status_code=403,
                            body={'error': 'Access denied'},
                        ).to_lambda_response(cors_headers)

                # Only now does anything reach the producer. Calling first and
                # filtering the response would still have MUTATED CMS's
                # subscription scope upstream — for DELETE, a silent cross-fleet
                # unenrollment.
                if method == 'POST':
                    _cs_result = connected_services_proxy.enroll_vin(
                        vin=_cs_vin,
                        config=_cs_config,
                        token_provider=_cs_tokens,
                        http_client=_cs_http_client(),
                    )
                else:
                    _cs_result = connected_services_proxy.unenroll_vin(
                        vin=_cs_vin,
                        config=_cs_config,
                        token_provider=_cs_tokens,
                        http_client=_cs_http_client(),
                    )
                return _cs_result.to_lambda_response(cors_headers)

            # 405 goes through ProxyResult like every other response on this
            # route, not a hand-built dict. Review cycle 1 caught this path
            # shipping without the SG5 headers: it was the one response here
            # that bypassed `to_lambda_response`, and the header test
            # parametrized only the three SUPPORTED methods, so nothing
            # exercised it. Building responses one way is what makes the
            # invariant hold — which is the argument SG5 made for putting the
            # headers in `to_lambda_response` in the first place.
            return connected_services_proxy.ProxyResult(
                status_code=405,
                body={'error': f'{method} not supported on this Connected '
                               f'Services route'},
            ).to_lambda_response(cors_headers)

        return {
            'statusCode': 404,
            'headers': cors_headers,
            'body': json.dumps({'error': 'Endpoint not found'})
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': cors_headers,
            'body': json.dumps({'error': f'Internal server error: {str(e)}'})
        }
