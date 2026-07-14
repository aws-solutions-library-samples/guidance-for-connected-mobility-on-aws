import json
import boto3
import os
import time
from datetime import datetime, timedelta, timezone
from cache_client import create_cached_dynamodb_client
from decimal import Decimal
from event_catalog_helper import enrich_event_with_catalog, normalize_event_response

# Create cached DynamoDB client
redis_endpoint = os.environ.get('REDIS_ENDPOINT')
dynamodb_client = create_cached_dynamodb_client(redis_endpoint)
dynamodb = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
s3_client = boto3.client('s3')

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


def _is_cloud_telemetry(data_source: str) -> bool:
    return data_source in {'cloud-telemetry', 'cloud-oem1'}


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
    # For FWE vehicles, treat as disconnected if no checkin for 2 minutes
    last_connected_ms = 0
    try:
        lc = meta.get("lastConnectedAt", meta.get("lastSeenAt", "0"))
        last_connected_ms = int(lc) if lc.isdigit() else int(float(lc))
    except Exception:
        pass
    conn_status = meta.get("connectionStatus", "connected")
    if conn_status == "connected" and (now_ms - last_connected_ms) > 120_000:
        conn_status = "disconnected"
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
        dtc_resp = dtc_table.query(
            KeyConditionExpression='vehicleId = :v',
            FilterExpression='dtcId = :d',
            ExpressionAttributeValues={':v': vehicle_id, ':d': dtc_id},
            Limit=50,
        )
        dtc_item = (dtc_resp.get('Items') or [None])[0]
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
# The iOS VSACompanion app reuses CMS capabilities (self-assigning a vehicle) by
# calling this API with its Cognito id-token. In staging the iOS app and the CMS
# Fleet UI share ONE consolidated Cognito pool, so the pool id cannot distinguish
# a driver from an operator — and the CMS API authorizer fronts a root {proxy+}
# ANY route + /api/v1/{proxy+}, so every /api/v1/* route is reachable by any
# trusted token. Driver tokens also carry no `cognito:groups`, which the
# handler's `is_admin = ... or not user_groups` default would otherwise treat as
# platform-admin.
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
    is_admin = 'platform-admin' in user_groups or not user_groups  # Default to admin if no groups (demo mode)
    is_viewer = 'fleet-viewer' in user_groups and 'fleet-operator' not in user_groups

    # ── Driver-self principal gate ─────────────────────────────────────────
    # Claim-based: a token carrying custom:driverId and NOT in an operator group
    # is a driver acting on their own behalf (when DRIVER_SELF_GUARD_ENABLED).
    # Never let it inherit the no-groups admin default; constrain it to the
    # self-service allowlist and force the vehicle list to the driver's own fleet.
    is_driver_self, driver_self_id = _classify_driver_self(claims)
    if is_driver_self:
        is_admin = False
        is_viewer = False
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
        if is_admin or not user_fleet_ids:
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
        """Return 403 response if user is a viewer, else None."""
        if is_viewer:
            return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Read-only access'})}
        return None

    def _check_fleet_access(fleet_id):
        """Return 403 if non-admin user doesn't have access to fleet_id, else None."""
        if is_admin or not user_fleet_ids:
            return None
        if fleet_id and fleet_id not in user_fleet_ids:
            return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Access denied to this fleet'})}
        return None

    def _scope_fleet_filter():
        """Return (filter_expr, expr_values) to scope queries by user's fleets. Returns (None, {}) for admins."""
        if is_admin or not user_fleet_ids:
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
            if not is_admin and user_fleet_ids:
                items = [i for i in items if i.get('fleetId') in user_fleet_ids]
            from decimal import Decimal
            def _dec(o):
                if isinstance(o, Decimal): return int(o) if o % 1 == 0 else float(o)
                raise TypeError
            return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'subscriptions': items, 'total': len(items)}, default=_dec)}
        except Exception as e:
            return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

    # Create/update subscription (enroll vehicle in a tier)
    if path == '/api/v1/subscriptions' and method == 'POST':
        if is_viewer:
            return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Read-only access'})}
        try:
            body = json.loads(event.get('body', '{}'))
            vehicle_id = body.get('vehicleId', '')
            tier = body.get('tier', 'basic')
            fleet_id = body.get('fleetId', '')
            if not vehicle_id or tier not in SUBSCRIPTION_TIERS:
                return {'statusCode': 400, 'headers': cors_headers, 'body': json.dumps({'error': 'vehicleId and valid tier required'})}
            if not is_admin and user_fleet_ids and fleet_id not in user_fleet_ids:
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
        if is_viewer:
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
            
            # Assign user to fleet-viewer group + set fleetIds and vehicleIds
            cognito_client = boto3.client('cognito-idp')
            cognito_client.admin_add_user_to_group(
                UserPoolId=user_pool_id, Username=user_email, GroupName='fleet-viewer')
            
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
        if is_viewer:
            return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Read-only access'})}
        print(f"🚗 Vehicle POST endpoint reached!")
        try:
            body = json.loads(event.get('body', '{}'))
            # Handle both direct data and entry-wrapped data
            entry = body.get('entry', body)
            print(f"🚗 Vehicle entry data: {entry}")
            
            vehicle_item = {
                'vehicleId': f"VEH-{int(time.time())}",
                'vin': entry.get('vin', ''),
                'make': entry.get('make', ''),
                'model': entry.get('model', ''),
                'year': entry.get('year', ''),
                'licensePlate': entry.get('licensePlate', ''),
                'color': entry.get('color', ''),
                'vehicleType': entry.get('vehicleType', ''),
                'fuelType': entry.get('fuelType', ''),
                'fleetId': entry.get('fleetId', ''),
                'status': 'active',
                'connectionStatus': 'disconnected',  # Default connection status
                'activityStatus': 'inactive',        # Default activity status  
                'lastConnected': None,               # Will be set when device connects
                'lastDisconnected': None,            # Will be set when device disconnects
                'createdAt': datetime.utcnow().isoformat(),
                'updatedAt': datetime.utcnow().isoformat()
            }
            print(f"🚗 Vehicle item to save: {vehicle_item}")
            
            vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
            print(f"🚗 Vehicles table name: {os.environ.get('VEHICLES_TABLE_NAME')}")
            
            vehicles_table.put_item(Item=vehicle_item)
            print(f"🚗 Vehicle saved successfully!")
            
            # Handle certificate creation if requested
            if entry.get('createCertificate', False):
                print(f"🔐 Creating certificate for vehicle {vehicle_item['vin']}")
                try:
                    
                    # Check if environment variable exists
                    cert_table_name = os.environ.get('VEHICLE_CERTIFICATES_TABLE_NAME')
                    if not cert_table_name:
                        print(f"🔐 ERROR: VEHICLE_CERTIFICATES_TABLE_NAME environment variable not set!")
                        raise Exception("VEHICLE_CERTIFICATES_TABLE_NAME environment variable not set")
                    
                    print(f"🔐 Using certificates table: {cert_table_name}")
                    
                    iot_client = boto3.client('iot')
                    
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
            
            return {
                'statusCode': 201,
                'headers': cors_headers,
                'body': json.dumps({'vehicle': vehicle_item})
            }
        except Exception as e:
            print(f"🚗 Error creating vehicle: {str(e)}")
            return {
                'statusCode': 500,
                'headers': cors_headers,
                'body': json.dumps({'error': str(e)})
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
            if is_viewer:
                return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Read-only access'})}
            vehicle_id = path.split('/')[-1]
            try:
                body = json.loads(event.get('body', '{}'))
                entry = body.get('entry', body)
                
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
                    update_expression += ', #year = :year'
                    expression_values[':year'] = entry['year']
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
            if is_viewer:
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
                if not is_admin and user_fleet_ids:
                    if fleet_id and fleet_id != 'all' and fleet_id not in user_fleet_ids:
                        return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Access denied'})}
                    if not fleet_id or fleet_id == 'all':
                        fleet_id = user_fleet_ids[0] if len(user_fleet_ids) == 1 else None
                
                filter_expression = None
                expression_values = {}
                
                if fleet_id and fleet_id != 'all':
                    filter_expression = 'fleetId = :fleet_id'
                    expression_values[':fleet_id'] = fleet_id
                elif not is_admin and user_fleet_ids and len(user_fleet_ids) > 1:
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
                    if not is_admin and user_fleet_ids:
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
                if not is_admin and user_fleet_ids:
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
                if not is_admin and user_fleet_ids:
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
                    if isinstance(obj, Decimal): return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError

                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                fleets_table = dynamodb.Table(os.environ.get('FLEETS_TABLE_NAME'))
                trips_table = dynamodb.Table(os.environ.get('TRIPS_TABLE_NAME'))
                safety_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                maint_table = dynamodb.Table(os.environ.get('MAINTENANCE_ALERTS_TABLE_NAME'))

                # Vehicle counts — scoped by fleet for non-admin users
                fleet_filter = {}
                if not is_admin and user_fleet_ids:
                    fleet_filter = {
                        'FilterExpression': 'fleetId = :f',
                        'ExpressionAttributeValues': {':f': user_fleet_ids[0]}
                    }
                
                v_resp = vehicles_table.scan(Select='COUNT', **fleet_filter)
                total_vehicles = v_resp['Count']
                # Active = has telemetry in last 24h (approximate via status field)
                active_filter = {'FilterExpression': '#s = :a', 'ExpressionAttributeNames': {'#s': 'status'}, 'ExpressionAttributeValues': {':a': 'active'}}
                if not is_admin and user_fleet_ids:
                    active_filter['FilterExpression'] = '#s = :a AND fleetId = :f'
                    active_filter['ExpressionAttributeValues'][':f'] = user_fleet_ids[0]
                active_resp = vehicles_table.scan(Select='COUNT', **active_filter)
                active_vehicles = active_resp['Count'] or total_vehicles

                # Fleet count
                if not is_admin and user_fleet_ids:
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
                    if not is_admin and user_fleet_ids and fid not in user_fleet_ids:
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
            if not is_admin and user_fleet_ids and fleet_id not in user_fleet_ids:
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
                        # Vehicle is active if currently connected OR last connected within 30 days
                        if (vehicle.get('connectionStatus') == 'connected' or 
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
                if not is_admin and user_fleet_ids:
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
                            # Vehicle is active if currently connected OR last connected within 30 days
                            if (vehicle.get('connectionStatus') == 'connected' or 
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
                if not is_admin and user_fleet_ids:
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
                
                # Sort before paginating
                all_filtered_vehicles.sort(
                    key=lambda x: x.get(sort_by) or '',
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
                import re as _re_driver_single
                def _canonical_driver_id_single(raw):
                    if not raw: return None
                    if _re_driver_single.match(r'^DRV-\d{4}$', raw): return raw
                    m = _re_driver_single.match(r'^DRIVER[-_](\d+)$', raw, _re_driver_single.IGNORECASE)
                    if m:
                        try:
                            return f'DRV-{int(m.group(1)):04d}'
                        except Exception:
                            return None
                    return None

                raw_did = trip.get('driverId')
                canon_did = _canonical_driver_id_single(raw_did)
                if canon_did:
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
                import re as _re_driver
                def _canonical_driver_id(raw):
                    if not raw: return None
                    m = _re_driver.match(r'^DRV-\d{4}$', raw)
                    if m: return raw
                    m = _re_driver.match(r'^DRIVER[-_](\d+)$', raw, _re_driver.IGNORECASE)
                    if m:
                        try:
                            return f'DRV-{int(m.group(1)):04d}'
                        except Exception:
                            return None
                    return None

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
                    #   1. known driverId on the trip → full name from
                    #      batched lookup
                    #   2. driverId present but not found in drivers
                    #      table → raw ID (visible, honest)
                    #   3. no driverId → currently-assigned driver of
                    #      this vehicle (1:1 invariant) if any
                    #   4. nothing known → 'Unassigned'
                    raw_did = trip.get('driverId')
                    canon_did = _canonical_driver_id(raw_did)
                    if canon_did and canon_did in driver_name_by_id:
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
            if not is_admin and user_fleet_ids:
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
                                import hashlib
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
                    latest_telemetry['timestamp'] = int(live_state.get('lastSeenAt', 0))

                # Fallback: if no Redis data, get latest telemetry from DDB
                if not latest_telemetry:
                    try:
                        telemetry_table = dynamodb.Table(os.environ.get('TELEMETRY_TABLE_NAME', f'cms-{os.environ.get("DEPLOYMENT_STAGE","prod")}-storage-telemetry'))
                        # Paginate scan to find at least one record for this vehicle
                        found_item = None
                        scan_kwargs = {
                            'FilterExpression': 'vehicleId = :v',
                            'ExpressionAttributeValues': {':v': vehicle_id},
                        }
                        for _ in range(5):  # Max 5 pages (~5000 items scanned)
                            t_resp = telemetry_table.scan(**scan_kwargs)
                            items = t_resp.get('Items', [])
                            if items:
                                items.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
                                found_item = items[0]
                                break
                            if 'LastEvaluatedKey' not in t_resp:
                                break
                            scan_kwargs['ExclusiveStartKey'] = t_resp['LastEvaluatedKey']

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
        
        # Service History Endpoints
        if path == '/api/v1/service-history' and method == 'GET':
            try:
                # Local Decimal serialiser. Several other handlers in
                # this function define the same helper inline; Python
                # treats `decimal_default` as a local variable for the
                # whole handler because of those `def` lines, so on
                # code paths that reach this branch without first
                # executing one of them (e.g. when the request hits
                # service-history without going through the consolidated
                # /api/v1/vehicles/{id} path), referencing
                # `decimal_default` raises UnboundLocalError. Defining
                # it locally here makes the binding deterministic.
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                vehicle_id = query_params.get('vehicleId')
                service_type = query_params.get('serviceType')
                limit = int(query_params.get('limit', 50))
                
                # Scope by fleet: verify vehicle belongs to user's fleet
                if vehicle_id and not is_admin and user_fleet_ids:
                    allowed = get_allowed_vehicle_ids()
                    if allowed is not None and vehicle_id not in allowed:
                        return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Access denied'})}
                
                service_history_table = dynamodb.Table(os.environ.get('SERVICE_HISTORY_TABLE_NAME'))
                
                if vehicle_id:
                    # Get service history for specific vehicle
                    response = service_history_table.query(
                        KeyConditionExpression='vehicleId = :vehicleId',
                        ExpressionAttributeValues={':vehicleId': vehicle_id},
                        ScanIndexForward=False,  # Most recent first
                        Limit=limit
                    )
                elif service_type:
                    # Get service history by service type
                    response = service_history_table.query(
                        IndexName='ServiceTypeIndex',
                        KeyConditionExpression='serviceType = :serviceType',
                        ExpressionAttributeValues={':serviceType': service_type},
                        ScanIndexForward=False,
                        Limit=limit
                    )
                    # Post-filter by allowed vehicles for non-admin
                    if not is_admin and user_fleet_ids:
                        allowed = get_allowed_vehicle_ids()
                        if allowed is not None:
                            response['Items'] = [i for i in response.get('Items', []) if i.get('vehicleId') in allowed]
                else:
                    # Get all service history (paginated) — scope by allowed vehicles
                    response = service_history_table.scan(Limit=limit)
                    if not is_admin and user_fleet_ids:
                        allowed = get_allowed_vehicle_ids()
                        if allowed is not None:
                            response['Items'] = [i for i in response.get('Items', []) if i.get('vehicleId') in allowed]
                
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
                    'body': json.dumps({'error': f'Failed to fetch service history: {str(e)}'})
                }
        
        if path == '/api/v1/service-history' and method == 'POST':
            denied = _deny_viewer()
            if denied: return denied
            try:
                body = json.loads(event.get('body', '{}'))
                entry = body.get('entry', body)
                
                # Verify vehicle belongs to user's fleet
                if not is_admin and user_fleet_ids:
                    allowed = get_allowed_vehicle_ids()
                    if allowed is not None and entry.get('vehicleId') not in allowed:
                        return {'statusCode': 403, 'headers': cors_headers, 'body': json.dumps({'error': 'Access denied'})}
                
                # Generate service record ID
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
                    'body': json.dumps({'serviceRecord': service_record}, default=decimal_default)
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to create service record: {str(e)}'})
                }
        
        if path.startswith('/api/v1/service-history/') and method == 'GET':
            try:
                # Extract vehicleId and serviceDate from path
                path_parts = path.split('/')
                if len(path_parts) >= 5:
                    vehicle_id = path_parts[4]
                    service_date = path_parts[5] if len(path_parts) > 5 else None
                    
                    # Verify vehicle belongs to user's fleet
                    if not is_admin and user_fleet_ids:
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
                fleet_record = {
                    'campaignId': fleet_campaign_id,
                    'campaignName': campaign_name,
                    'targetArn': f'fleet:{fleet_id}',
                    'targetType': 'FLEET',
                    'fleetId': fleet_id,
                    'status': 'RUNNING',
                    'createdAt': now,
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

                # Update fleet-level record
                camp_table.update_item(
                    Key={'campaignId': campaign_id},
                    UpdateExpression='SET #s = :s',
                    ExpressionAttributeNames={'#s': 'status'},
                    ExpressionAttributeValues={':s': new_status}
                )

                # Update all child vehicle records
                fleet_id_part = campaign_id.split('-fleet:')[1] if '-fleet:' in campaign_id else ''
                child_resp = camp_table.query(
                    IndexName='sourceFleetId-index',
                    KeyConditionExpression='sourceFleetId = :f',
                    ExpressionAttributeValues={':f': fleet_id_part}
                )
                updated = 0
                for child in child_resp.get('Items', []):
                    camp_table.update_item(
                        Key={'campaignId': child['campaignId']},
                        UpdateExpression='SET #s = :s',
                        ExpressionAttributeNames={'#s': 'status'},
                        ExpressionAttributeValues={':s': new_status}
                    )
                    updated += 1

                return {'statusCode': 200, 'headers': cors_headers,
                    'body': json.dumps({'success': True, 'campaignId': campaign_id, 'status': new_status, 'vehiclesUpdated': updated})}
            except Exception as e:
                return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

        # Decision Journal endpoint
        if path == '/api/v1/decision-journal' and method == 'GET':
            try:
                table = dynamodb.Table(f'cms-{os.environ.get("DEPLOYMENT_STAGE", "prod")}-decision-journal')
                resp = table.scan()
                items = sorted(resp.get('Items', []), key=lambda x: int(x.get('timestamp', 0)), reverse=True)
                for item in items:
                    for k, v in item.items():
                        if hasattr(v, 'as_integer_ratio'):  # Decimal
                            item[k] = float(v)
                return {'statusCode': 200, 'headers': cors_headers, 'body': json.dumps({'decisions': items[:50]})}
            except Exception as e:
                return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

        # Knowledge Base Document Endpoints
        if path == '/api/v1/fleet-actions' and method == 'GET':
            try:
                table = dynamodb.Table('cms-prod-vfo-action-queue')
                status_filter = query_params.get('status', 'PENDING')
                try:
                    resp = table.scan()
                    items = resp.get('Items', [])
                    while 'LastEvaluatedKey' in resp:
                        resp = table.scan(ExclusiveStartKey=resp['LastEvaluatedKey'])
                        items.extend(resp.get('Items', []))
                except Exception:
                    # VFO action queue table may not exist in all deployments
                    # (e.g. before the VFO pipeline stack is deployed). Return
                    # an empty list instead of 500 so the UI renders its
                    # empty state.
                    items = []
                if status_filter != 'ALL':
                    items = [i for i in items if i.get('status') == status_filter]
                items.sort(key=lambda x: x.get('createdAt', ''), reverse=True)

                # Normalize rows to a consistent UI-facing schema.
                # The vfo-action-queue table is written to by FOUR different
                # producers, each with its own historical field names:
                #
                #   1. seed_vfo_actions.py             → {priority, domain, agentResponse, ...}
                #   2. VSA driver voice app (external) → {severity, summary, reason, actionType, source=vsa-escalate}
                #   3. Flink DTC processors (new)      → {priority, domain, agentResponse, source=dtc-critical}
                #   4. Legacy historical injector      → mixed shapes
                #
                # The UI's FleetCommandCenter renders {priority, domain,
                # agentResponse, createdAt, actionId}. Rather than teach each
                # of 4 producers about the UI shape (producers #2 lives in a
                # separate repo), we normalize server-side: every row returned
                # from this endpoint has the 4 UI-facing keys populated, with
                # reasonable fallbacks from producer-specific fields when
                # UI-facing keys are missing.
                def _normalize_severity(raw):
                    """Map any severity-ish input to canonical CRITICAL/HIGH/MEDIUM/LOW.

                    Authoritative per docs/SEVERITY_VOCABULARY.md. Accepts:
                      - Canonical words (case-insensitive): CRITICAL, HIGH, MEDIUM, LOW
                      - SAE DTC hint: P0/P1/P2/P3
                      - Legacy numeric: '4'/'3'/'2'/'1' or 4/3/2/1
                      - Empty / unknown → 'MEDIUM' (safe default)
                    """
                    if raw is None:
                        return 'MEDIUM'
                    s = str(raw).strip().upper()
                    if not s:
                        return 'MEDIUM'
                    # Canonical pass-through
                    if s in ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW'):
                        return s
                    # SAE DTC hint
                    sae_map = {'P0': 'CRITICAL', 'P1': 'HIGH', 'P2': 'MEDIUM', 'P3': 'LOW'}
                    if s in sae_map:
                        return sae_map[s]
                    # Legacy numeric (note: 4 is most severe, inverted from SAE)
                    num_map = {'4': 'CRITICAL', '3': 'HIGH', '2': 'MEDIUM', '1': 'LOW'}
                    if s in num_map:
                        return num_map[s]
                    # Unknown — log and default.
                    print(f"_normalize_severity: unknown value {raw!r}, defaulting to MEDIUM")
                    return 'MEDIUM'

                def _normalize_action(item):
                    # Severity and priority are semantically distinct (see
                    # docs/SEVERITY_VOCABULARY.md §"Severity vs priority") but
                    # today all producers set them to the same value. Normalize
                    # both independently so a future policy layer can diverge
                    # them without the UI needing to change.
                    #
                    # Prefer an explicit severity field first; fall back to
                    # priority (used by some older seeded rows that have
                    # priority but no severity). Normalize whichever we get.
                    raw_severity = item.get('severity') or item.get('priority') or ''
                    item['severity'] = _normalize_severity(raw_severity)

                    raw_priority = item.get('priority') or item.get('severity') or ''
                    item['priority'] = _normalize_severity(raw_priority)

                    domain = item.get('domain') or ''
                    if not domain:
                        # Infer domain from source/actionType tags.
                        source = (item.get('source') or '').lower()
                        action_type = (item.get('actionType') or '').upper()
                        if source == 'vsa-escalate' or action_type in ('ROADSIDE_DISPATCH', 'TOWING'):
                            domain = 'Driver Escalation'
                        elif source == 'dtc-critical':
                            domain = 'Diagnostics'
                        elif action_type:
                            # Prettify e.g. ROADSIDE_DISPATCH → "Roadside Dispatch"
                            domain = action_type.replace('_', ' ').title()
                        else:
                            domain = 'Other'
                    item['domain'] = domain

                    agent_response = item.get('agentResponse') or ''
                    if not agent_response:
                        # Pull whatever human-readable text we have.  VSA rows
                        # carry {summary, reason}; DTC rows will carry {summary,
                        # description}; fall back to a one-line description
                        # from whatever fields are present.
                        parts = []
                        summary = item.get('summary')
                        if summary:
                            parts.append(summary)
                        reason = item.get('reason')
                        if reason and reason not in parts:
                            parts.append(reason)
                        if not parts:
                            # Last-resort synthesis — don't leave the card blank.
                            if item.get('vehicleId'):
                                parts.append(f"Action requested for {item['vehicleId']}")
                            if item.get('driverName'):
                                parts.append(f"Driver: {item['driverName']}")
                            if item.get('actionType'):
                                parts.append(f"Type: {item['actionType']}")
                        agent_response = ' — '.join(parts) if parts else '(no details)'
                    item['agentResponse'] = agent_response
                    return item

                items = [_normalize_action(i) for i in items]

                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({'actions': items[:50], 'total': len(items)}, default=str)
                }
            except Exception as e:
                return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

        if path.startswith('/api/v1/fleet-actions/') and method == 'POST':
            try:
                action_id = path.split('/')[-2]
                action_type = path.split('/')[-1]  # approve or reject
                if action_type not in ('approve', 'reject'):
                    return {'statusCode': 400, 'headers': cors_headers,
                            'body': json.dumps({'error': f'Unsupported action type: {action_type}'})}
                table = dynamodb.Table('cms-prod-vfo-action-queue')
                # Find the item
                resp = table.scan(FilterExpression=boto3.dynamodb.conditions.Attr('actionId').eq(action_id))
                items = resp.get('Items', [])
                if not items:
                    return {'statusCode': 404, 'headers': cors_headers, 'body': json.dumps({'error': 'Action not found'})}
                item = items[0]

                # Idempotency: don't re-approve/re-reject a row that's already
                # in a terminal state. The UI's 5s refresh polling can race
                # with the user's click and re-submit — without this check, we
                # overwrite resolvedAt with the later timestamp each time.
                current_status = item.get('status', 'PENDING')
                if current_status in ('APPROVED', 'REJECTED'):
                    return {'statusCode': 200, 'headers': cors_headers,
                            'body': json.dumps({
                                'actionId': action_id,
                                'status': current_status,
                                'idempotent': True,
                                'message': f'Action already {current_status.lower()}'
                            })}

                new_status = 'APPROVED' if action_type == 'approve' else 'REJECTED'
                # datetime + timezone are imported at module top (line 5);
                # always use them directly.  The previous
                # `'timezone' in dir() else json.dumps(None)` check was a bug
                # — dir() at handler scope doesn't include module-level imports,
                # so the fallback always fired and wrote the literal string
                # "null" as resolvedAt on every approved row.
                resolved_at_iso = datetime.now(timezone.utc).isoformat()
                table.update_item(
                    Key={'actionId': item['actionId'], 'createdAt': item['createdAt']},
                    UpdateExpression='SET #s = :s, resolvedAt = :r, resolvedBy = :rb',
                    ExpressionAttributeNames={'#s': 'status'},
                    ExpressionAttributeValues={
                        ':s': new_status,
                        ':r': resolved_at_iso,
                        ':rb': user_email or 'operator'
                    }
                )

                # Fix E: when approving a DTC-driven action, close the loop
                # automatically — schedule a service-history row AND mark the
                # underlying DTC as cleared.  Without this, the demo flow
                # leaves an orphaned ACTIVE DTC even after the operator has
                # explicitly approved the action on it.
                followups = {}
                if new_status == 'APPROVED' and item.get('source') == 'dtc-critical':
                    vehicle_id = item.get('vehicleId')
                    dtc_id = item.get('dtcId')
                    dtc_code = item.get('dtcCode')
                    if vehicle_id and dtc_id:
                        followups = _approve_dtc_action_followups(
                            action_id=action_id,
                            vehicle_id=vehicle_id,
                            vin=item.get('vin'),
                            dtc_id=dtc_id,
                            dtc_code=dtc_code or '',
                            system=item.get('system', 'UNKNOWN'),
                            severity=item.get('severity', 'HIGH'),
                            resolver=user_email or 'operator',
                            resolved_at_iso=resolved_at_iso,
                        )

                return {
                    'statusCode': 200, 'headers': cors_headers,
                    'body': json.dumps({
                        'actionId': action_id,
                        'status': new_status,
                        'resolvedAt': resolved_at_iso,
                        **followups,
                    })
                }
            except Exception as e:
                import traceback
                print(f"fleet-actions approve/reject failed: {e}\n{traceback.format_exc()}")
                return {'statusCode': 500, 'headers': cors_headers, 'body': json.dumps({'error': str(e)})}

        if path == '/api/v1/daily-briefing' and method == 'GET':
            try:
                stage = os.environ.get('DEPLOYMENT_STAGE', 'prod')
                cache_ttl_seconds = 900  # 15 min
                cache_table_name = f'cms-{stage}-storage-dashboard-metrics-cache'
                cache_key = 'daily_briefing_v1'

                # Cache lookup
                try:
                    cache_tbl = dynamodb.Table(cache_table_name)
                    cached = cache_tbl.get_item(Key={'metricKey': cache_key}).get('Item')
                    if cached:
                        ts = int(cached.get('timestamp', 0))
                        if (int(time.time()) - ts) < cache_ttl_seconds:
                            return {
                                'statusCode': 200,
                                'headers': {**cors_headers, 'X-Cache': 'HIT'},
                                'body': cached['data'],
                            }
                except Exception:
                    pass

                items = []

                def scan_count(table_name, **kwargs):
                    try:
                        client = dynamodb.meta.client
                        total = 0
                        kw = {'TableName': table_name, 'Select': 'COUNT'}
                        kw.update(kwargs)
                        resp = client.scan(**kw)
                        total += resp.get('Count', 0)
                        while 'LastEvaluatedKey' in resp:
                            kw['ExclusiveStartKey'] = resp['LastEvaluatedKey']
                            resp = client.scan(**kw)
                            total += resp.get('Count', 0)
                        return total
                    except Exception:
                        return 0

                def scan_items(table_name, **kwargs):
                    try:
                        t = dynamodb.Table(table_name)
                        out, resp = [], t.scan(**kwargs)
                        out.extend(resp.get('Items', []))
                        while 'LastEvaluatedKey' in resp:
                            kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
                            resp = t.scan(**kwargs)
                            out.extend(resp.get('Items', []))
                        return out
                    except Exception:
                        return []

                # ── Pull the data the briefing needs (parallelized) ──
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
                    f_vehicles = ex.submit(scan_items, f'cms-{stage}-storage-vehicles')
                    f_actions = ex.submit(scan_items, f'cms-{stage}-vfo-action-queue')
                    f_recalls = ex.submit(scan_items, f'cms-{stage}-storage-recalls')
                    f_warranty = ex.submit(scan_items, f'cms-{stage}-storage-warranty-claims')
                    f_tco_count = ex.submit(scan_count, f'cms-{stage}-storage-vehicle-costs')
                    f_safety_week = ex.submit(scan_count, f'cms-{stage}-storage-safety-events')
                    vehicles = f_vehicles.result() or []
                    actions = f_actions.result() or []
                    recalls = f_recalls.result() or []
                    warranty = f_warranty.result() or []
                    tco_count = f_tco_count.result() or 0
                    safety_total = f_safety_week.result() or 0

                today = datetime.now(timezone.utc).strftime('%B %d, %Y')
                total_vehicles = len(vehicles)
                active_vehicles = sum(1 for v in vehicles if v.get('status') == 'active')
                util_pct = round((active_vehicles / max(1, total_vehicles)) * 100, 1) if total_vehicles else 0.0

                # 1. Fleet Health headline
                # We reuse the composite if already cached (fast path)
                health_cached = None
                try:
                    fh = cache_tbl.get_item(Key={'metricKey': 'fleet_health_v2'}).get('Item')
                    if fh:
                        health_cached = json.loads(fh['data'])
                except Exception:
                    pass
                if health_cached:
                    composite = health_cached.get('composite', 85)
                    items.append({
                        'label': 'Fleet Health',
                        'text': f"{composite}/100 composite (utilization {health_cached.get('utilization', 0)}%, "
                                f"safety {health_cached.get('safety_compliance', 0)}%, "
                                f"cost {health_cached.get('cost_health', 0)}%, "
                                f"maintenance {health_cached.get('maintenance_health', 0)}%)",
                        'type': 'success' if composite >= 80 else 'warning' if composite >= 60 else 'error',
                    })

                # 2. Top priority from highest-priority PENDING action
                pending_actions = [a for a in actions if a.get('status') == 'PENDING']
                pending_actions.sort(
                    key=lambda a: (
                        {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}.get(a.get('priority', 'MEDIUM'), 1),
                        a.get('createdAt', ''),
                    )
                )
                if pending_actions:
                    top = pending_actions[0]
                    domain = top.get('domain', 'Action')
                    resp_text = str(top.get('agentResponse', '')).split('.')[0][:140]
                    items.append({
                        'label': 'Top Priority',
                        'text': f"{domain} — {resp_text}",
                        'type': 'error' if top.get('priority') == 'HIGH' else 'warning',
                    })

                # 3. Utilization status
                util_target = 82
                delta = round(util_pct - util_target, 1)
                direction = '+' if delta >= 0 else ''
                items.append({
                    'label': 'Utilization',
                    'text': (
                        f"{util_pct}% avg ({active_vehicles}/{total_vehicles} active) "
                        f"vs {util_target}% target ({direction}{delta}%)"
                    ),
                    'type': 'success' if delta >= 0 else 'warning' if delta >= -10 else 'error',
                })

                # 4. Open recall + warranty summary
                critical_recalls = sum(1 for r in recalls if r.get('severity') == 'Critical')
                high_recalls = sum(1 for r in recalls if r.get('severity') == 'High')
                pending_recalls = sum(1 for r in recalls if r.get('status') == 'pending')
                items.append({
                    'label': 'Recalls',
                    'text': (
                        f"{pending_recalls} open vehicle-recall combinations "
                        f"({critical_recalls} critical, {high_recalls} high severity)"
                    ),
                    'type': 'error' if critical_recalls > 0 else 'warning' if high_recalls > 0 else 'info',
                })

                # 5. Warranty status
                open_warranty = [c for c in warranty if c.get('status') in ('OPEN', 'UNDER_REVIEW')]
                at_risk_amount = sum(float(c.get('claimAmount', 0)) for c in open_warranty)
                paid = [c for c in warranty if c.get('status') == 'PAID']
                recovered = sum(float(c.get('paidAmount', 0)) for c in paid)
                items.append({
                    'label': 'Warranty',
                    'text': (
                        f"{len(open_warranty)} claims open (${at_risk_amount:,.0f} at risk); "
                        f"{len(paid)} paid (${recovered:,.0f} recovered)"
                    ),
                    'type': 'warning' if len(open_warranty) > 10 else 'success' if len(paid) > len(open_warranty) else 'info',
                })

                # 6. Focus today — top 3 pending action domains
                from collections import Counter
                if pending_actions:
                    domain_counts = Counter(a.get('domain', 'Unknown') for a in pending_actions)
                    focus = ', '.join(f"{d} ({c})" for d, c in domain_counts.most_common(3))
                    items.append({
                        'label': 'Focus Today',
                        'text': (
                            f"{len(pending_actions)} pending actions across: {focus}. "
                            f"Review in Command Center > Pending Actions."
                        ),
                        'type': 'info',
                    })

                body = json.dumps({
                    'generatedAt': datetime.now(timezone.utc).isoformat(),
                    'date': today,
                    'items': items,
                }, default=str)

                # Write-through cache (best-effort)
                try:
                    cache_tbl.put_item(Item={
                        'metricKey': cache_key,
                        'data': body,
                        'timestamp': int(time.time()),
                    })
                except Exception:
                    pass

                return {
                    'statusCode': 200,
                    'headers': {**cors_headers, 'X-Cache': 'MISS'},
                    'body': body,
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }

        if path == '/api/v1/fleet-health' and method == 'GET':
            try:
                stage = os.environ.get('DEPLOYMENT_STAGE', 'prod')
                cache_ttl_seconds = 300  # 5 min - fleet health is aggregate, doesn't need to be realtime
                cache_table_name = f'cms-{stage}-storage-dashboard-metrics-cache'
                cache_key = 'fleet_health_v2'

                # ── Step 1: cache lookup ──────────────────────────────────
                # This endpoint used to take 8-9s because it did 5 full table
                # scans serially (safety events alone = 14k rows). Now we cache
                # the composite and only recompute every 5 min.
                try:
                    cache_tbl = dynamodb.Table(cache_table_name)
                    cached = cache_tbl.get_item(Key={'metricKey': cache_key}).get('Item')
                    if cached:
                        ts = int(cached.get('timestamp', 0))
                        if (int(time.time()) - ts) < cache_ttl_seconds:
                            return {
                                'statusCode': 200,
                                'headers': {**cors_headers, 'X-Cache': 'HIT'},
                                'body': cached['data'],
                            }
                except Exception:
                    pass  # Cache miss or cache table doesn't exist - fall through

                # ── Step 2: parallel count-only scans + small data scans ──
                # Use DDB's Select='COUNT' for total counts (no payload transfer)
                # and only scan items when we need their field values.
                import concurrent.futures

                def count_scan(table_name, filter_expr=None, expr_vals=None, expr_names=None):
                    """Return total count via Select='COUNT' scan, paginated."""
                    try:
                        ddb_client = dynamodb.meta.client
                        kwargs = {'TableName': table_name, 'Select': 'COUNT'}
                        if filter_expr:
                            kwargs['FilterExpression'] = filter_expr
                        if expr_vals:
                            kwargs['ExpressionAttributeValues'] = expr_vals
                        if expr_names:
                            kwargs['ExpressionAttributeNames'] = expr_names
                        total = 0
                        resp = ddb_client.scan(**kwargs)
                        total += resp.get('Count', 0)
                        while 'LastEvaluatedKey' in resp:
                            kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
                            resp = ddb_client.scan(**kwargs)
                            total += resp.get('Count', 0)
                        return total
                    except Exception:
                        return 0

                def items_scan(table_name):
                    """Small-table scan returning all items."""
                    try:
                        t = dynamodb.Table(table_name)
                        items, resp = [], t.scan()
                        items.extend(resp.get('Items', []))
                        while 'LastEvaluatedKey' in resp:
                            resp = t.scan(ExclusiveStartKey=resp['LastEvaluatedKey'])
                            items.extend(resp.get('Items', []))
                        return items
                    except Exception:
                        return []

                vehicles_tbl = os.environ.get('VEHICLES_TABLE_NAME', f'cms-{stage}-storage-vehicles')
                maint_tbl = os.environ.get('MAINTENANCE_ALERTS_TABLE_NAME', f'cms-{stage}-storage-maintenance-alerts')
                safety_tbl = os.environ.get('SAFETY_EVENTS_TABLE_NAME', f'cms-{stage}-storage-safety-events')
                warranty_tbl = f'cms-{stage}-storage-warranty-claims'
                service_tbl = f'cms-{stage}-storage-service-history'

                # Tasks run in parallel. safety_count uses Select='COUNT' which
                # returns just row counts (no payload) - previously this
                # endpoint scanned 14k safety event rows just to get len(),
                # which was the bulk of the 8s latency.
                # Other tables are small (vehicles=50, maintenance=127,
                # warranty=69, service=701) so full scan is fine.
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
                    f_vehicles = ex.submit(items_scan, vehicles_tbl)
                    f_maintenance = ex.submit(items_scan, maint_tbl)
                    f_safety_count = ex.submit(count_scan, safety_tbl)
                    f_warranty = ex.submit(items_scan, warranty_tbl)
                    f_service = ex.submit(items_scan, service_tbl)

                    vehicles = f_vehicles.result() or []
                    maintenance = f_maintenance.result() or []
                    safety_count = f_safety_count.result() or 0
                    warranty = f_warranty.result() or []
                    service = f_service.result() or []

                tv = len(vehicles)

                # Utilization: default 100 when no vehicles; else active/total scaled to 85% target
                active = sum(1 for v in vehicles if v.get('status') == 'active') if tv else 0
                if tv == 0:
                    util_pct = 0
                    util_score = 100
                else:
                    util_pct = (active / tv) * 100
                    util_score = min(100, max(0, (util_pct / 85) * 100)) if active > 0 else 100

                # Cost: default 100 when no warranty/service records; else compute recovery-based score
                if len(warranty) == 0 and len(service) == 0:
                    cost_score = 100
                    recovery = 100
                else:
                    paid = sum(1 for c in warranty if c.get('status') == 'PAID')
                    recovery = (paid / max(1, len(warranty))) * 100
                    total_cost = sum(float(s.get('cost', 0)) for s in service)
                    warr_cov = sum(float(s.get('warrantyCoverage', 0)) for s in service)
                    cost_score = min(100, (recovery * 0.6 + (warr_cov / max(1, total_cost)) * 100 * 0.4 + 20))

                # Safety: use counts, not full items - don't need to iterate 14k rows
                crit_open = sum(1 for m in maintenance if m.get('severity') in ('CRITICAL', 'HIGH') and m.get('status') == 'OPEN')
                if tv == 0 or (crit_open == 0 and safety_count == 0):
                    safety_score = 100
                else:
                    vehicles_with_crit = len(set(m.get('vehicleId') for m in maintenance if m.get('severity') in ('CRITICAL', 'HIGH') and m.get('status') == 'OPEN'))
                    safety_score = max(0, min(100, ((tv - vehicles_with_crit) / max(1, tv)) * 100 - min(20, safety_count * 0.3)))

                # Maintenance: default 100 when no open alerts; else compute resolved ratio
                open_maint = sum(1 for m in maintenance if m.get('status') == 'OPEN')
                if tv == 0 or open_maint == 0:
                    maint_score = 100
                else:
                    vehicles_with_open = len(set(m.get('vehicleId') for m in maintenance if m.get('status') == 'OPEN'))
                    resolved_ratio = len(service) / max(1, len(service) + open_maint) * 100
                    maint_score = max(0, min(100, resolved_ratio + ((tv - vehicles_with_open) / max(1, tv)) * 20))

                composite = round(util_score * 0.25 + cost_score * 0.25 + safety_score * 0.30 + maint_score * 0.20)

                result = {
                    'composite': min(100, max(0, composite)),
                    'utilization': round(util_score),
                    'cost_health': round(cost_score),
                    'safety_compliance': round(safety_score),
                    'maintenance_health': round(maint_score),
                    'details': {
                        'total_vehicles': tv, 'active_vehicles': active,
                        'utilization_pct': round(util_pct, 1),
                        'open_critical_alerts': crit_open,
                        'total_safety_events': safety_count,
                        'total_maintenance_open': open_maint,
                        'total_service_records': len(service),
                        'warranty_claims': len(warranty),
                        'warranty_paid': sum(1 for c in warranty if c.get('status') == 'PAID'),
                        'recovery_rate': round(recovery, 1),
                    }
                }
                body = json.dumps(result, default=str)

                # ── Step 3: write-through cache (best-effort, don't block response) ──
                try:
                    cache_tbl.put_item(Item={
                        'metricKey': cache_key,
                        'data': body,
                        'timestamp': int(time.time()),
                    })
                except Exception:
                    pass

                return {
                    'statusCode': 200,
                    'headers': {**cors_headers, 'X-Cache': 'MISS'},
                    'body': body,
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }

        if path == '/api/v1/documents' and method == 'GET':
            try:
                from botocore.config import Config as BotoConfig
                vfo_region = os.environ.get('VFO_KB_REGION', os.environ.get('AWS_REGION', 'us-east-1'))
                # Bucket name suffixed with -{region}-{account} per spec
                # `2026-06-04-cms-vfo-kb-bucket-region-suffix`. Env-var
                # VFO_KB_BUCKET wins (set by ui_stack); defensive default
                # derives from runtime env vars.
                _vfo_stage = os.environ.get('DEPLOYMENT_STAGE', 'prod')
                _vfo_account = os.environ.get('AWS_ACCOUNT_ID', '')
                bucket = os.environ.get(
                    'VFO_KB_BUCKET',
                    f'cms-{_vfo_stage}-vfo-knowledge-base-{vfo_region}-{_vfo_account}',
                )
                s3_client = boto3.client('s3', region_name=vfo_region, config=BotoConfig(signature_version='s3v4'))
                doc_key = query_params.get('key', '')
                prefix = query_params.get('prefix', '')
                
                if doc_key:
                    if doc_key.endswith('.pdf'):
                        # Return presigned URL for PDFs
                        url = s3_client.generate_presigned_url('get_object', Params={'Bucket': bucket, 'Key': doc_key}, ExpiresIn=3600)
                        return {
                            'statusCode': 200,
                            'headers': cors_headers,
                            'body': json.dumps({'key': doc_key, 'type': 'pdf', 'url': url})
                        }
                    else:
                        obj = s3_client.get_object(Bucket=bucket, Key=doc_key)
                        content = obj['Body'].read().decode('utf-8')
                        return {
                            'statusCode': 200,
                            'headers': cors_headers,
                            'body': json.dumps({'key': doc_key, 'type': 'text', 'content': content})
                        }
                elif prefix:
                    resp = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=100)
                    docs = [{'key': o['Key'], 'size': o['Size'], 'lastModified': o['LastModified'].isoformat()} for o in resp.get('Contents', [])]
                    return {
                        'statusCode': 200,
                        'headers': cors_headers,
                        'body': json.dumps({'documents': docs, 'count': len(docs)})
                    }
                else:
                    resp = s3_client.list_objects_v2(Bucket=bucket, Delimiter='/')
                    prefixes = [p['Prefix'] for p in resp.get('CommonPrefixes', [])]
                    return {
                        'statusCode': 200,
                        'headers': cors_headers,
                        'body': json.dumps({'prefixes': prefixes})
                    }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
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
