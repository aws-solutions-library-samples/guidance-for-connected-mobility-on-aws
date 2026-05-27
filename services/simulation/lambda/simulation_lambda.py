"""
Simulation API Lambda — thin orchestrator that manages ECS Fargate sim workers.
Routes: /api/simulation/{start,stop,status,list,health,drivers,presets,discover-iot-endpoint}
"""
import json, os, time, uuid, boto3
from datetime import datetime, timezone

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

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Content-Type": "application/json",
}

def _resp(code, body):
    return {"statusCode": code, "headers": CORS, "body": json.dumps(body, default=str)}


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

    if path.endswith("/agent/start") and method == "POST":
        return _agent_start(json.loads(event.get("body", "{}")))

    if path.endswith("/agent/stop") and method == "POST":
        return _agent_stop(json.loads(event.get("body", "{}")))

    if path.endswith("/agent/status") and method == "GET":
        return _agent_status()

    if "/agent/logs/" in path and method == "GET":
        vin = path.split("/agent/logs/")[-1].split("?")[0]
        return _agent_logs(vin)

    if path.endswith("/start") and method == "POST":
        return _start(json.loads(event.get("body", "{}")))

    if "/stop/" in path and method == "POST":
        return _stop(path.split("/stop/")[-1])

    if "/status/" in path and method == "GET":
        return _status(path.split("/status/")[-1])

    if path.endswith("/list"):
        return _list()

    if path.endswith("/drivers"):
        return _drivers()

    if path.endswith("/presets"):
        return _presets()

    if path.endswith("/discover-iot-endpoint"):
        return _discover_iot()

    if path.endswith("/campaigns") and method == "GET":
        return _campaigns()

    return _resp(404, {"error": f"Unknown route: {method} {path}"})


def _check_running_tasks(vin):
    """Return task ARN if a task is already running for this VIN, else None."""
    try:
        task_arns = ecs.list_tasks(cluster=CLUSTER)["taskArns"]
        if not task_arns:
            return None
        tasks = ecs.describe_tasks(cluster=CLUSTER, tasks=task_arns)["tasks"]
        for t in tasks:
            if t["lastStatus"] in ("RUNNING", "PENDING", "PROVISIONING"):
                for c in t.get("overrides", {}).get("containerOverrides", []):
                    for env in c.get("environment", []):
                        if env.get("name") == "VEHICLE_NAME" and env.get("value", "").startswith(vin):
                            return t["taskArn"]
    except Exception:
        pass
    return None


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

# Explicit DTC → ECU# mapping for the 19 demo codes. Derived from the
# handoff's ECU groupings. Keyed by raw DTC string (caller passes the
# dtc_code from cms-<stage>-event-catalog).
_ECU_BY_CODE = {
    # ECU1 — ECU_BRAKE (C-prefix)
    "C1234": 1, "C1241": 1, "C1201": 1, "C0035": 1, "C0040": 1,
    # ECU2 — ECU_ENGINE (most P-prefix engine-side codes)
    "P0217": 2, "P0300": 2, "P0340": 2, "P0420": 2, "P0171": 2, "P0461": 2,
    # ECU3 — ECU_POWERTRAIN
    "P0700": 3,
    # ECU4 — ECU_PCM
    "P0606": 4,
    # ECU5 — ECU_COMM (U-prefix)
    "U0100": 5, "U0401": 5,
    # ECU6 — ECU_BATTERY_HV
    "P0A80": 6,
    # ECU7 — ECU_BATTERY_12V
    "P0562": 7,
    # ECU8 — ECU_EVAP
    "P0442": 8,
    # ECU9 — ECU_BODY (B-prefix)
    "B1000": 9,
}

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
        ecu_num = _ECU_BY_CODE.get(dtc)
        if ecu_num is None:
            missing.append((event_id, dtc))
            continue
        dtcs_by_ecu.setdefault(ecu_num, []).append(dtc)

    if missing:
        print(f"⚠️ DTCs with no ECU mapping (add to _ECU_BY_CODE): {missing}")

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
            # Fire every 30s — enough for the demo, doesn't hammer the CAN bus.
            "executionFrequencyMs": 30_000,
            "maxExecutionCount": 0,
        })

    return uds_dtc_map, signals_to_fetch, set(dtcs_by_ecu.keys())


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
    }
    try:
        camp_table.put_item(Item=item)
        print(f"✓ Ephemeral UDS campaign: {campaign_id} "
              f"(ECUs={sorted(ecus_in_play)}, fetches={len(signals_to_fetch)})")
        return campaign_id
    except Exception as e:
        print(f"⚠ Failed to write ephemeral UDS campaign {campaign_id}: {e}")
        return None


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
            "--rule-name", f"cms_{STAGE}_iot_msk_rule",
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

            # Verify RUNNING campaign with signals exists for this vehicle
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
                return _resp(400, {
                    "success": False,
                    "error": f"No active campaign with signals found for {vin}. Assign a campaign before starting FWE simulation."
                })

            try:
                cert_table = ddb.Table(f"cms-{STAGE}-storage-vehicle-certificates")
                cert_resp = cert_table.get_item(Key={"vehicleId": vehicle_id})
                if "Item" in cert_resp:
                    cert_pem = cert_resp["Item"].get("certificatePem", "")
                    private_key = cert_resp["Item"].get("privateKey", "")
                    # Use IoT thing name from cert table
                    vin = cert_resp["Item"].get("vin", vin)
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
        uds_dtc_map, signals_to_fetch, ecus_in_play = _build_uds_dtc_map(
            config.get("maintenance_scenarios") or []
        )
        if signals_to_fetch:
            _ensure_uds_campaign(vin, signals_to_fetch, ecus_in_play, sim_id)
        uds_dtc_map_json = json.dumps(uds_dtc_map) if uds_dtc_map else ""

        # 1. Start FWE agent task if not already running
        existing_agent = _check_running_tasks(vin)
        agent_task_arn = existing_agent
        can_iface = "vcan0"
        if not existing_agent:
            # Assign next available vcan interface
            vcan_idx = _next_vcan_index()
            can_iface = f"vcan{vcan_idx}"
            try:
                agent_resp = ecs.run_task(
                    cluster=CLUSTER,
                    taskDefinition=fwe_agent_task_def,
                    capacityProviderStrategy=cap_strategy,
                    overrides={"containerOverrides": [
                        {"name": "fwe-agent", "environment": [
                            {"name": "VEHICLE_NAME", "value": vin},
                            {"name": "ENDPOINT_URL", "value": iot_endpoint},
                            {"name": "CAN_BUS0", "value": can_iface},
                            {"name": "CERTIFICATE", "value": cert_pem},
                            {"name": "PRIVATE_KEY", "value": private_key},
                            {"name": "TOPIC_PREFIX", "value": "cms/fleetwise/"},
                        ]},
                    ]},
                )
                agent_task_arn = agent_resp.get("tasks", [{}])[0].get("taskArn")
                print(f"Started FWE agent task: {agent_task_arn} on {can_iface}")
            except Exception as e:
                print(f"FWE agent run_task error: {e}")
        else:
            # Find which vcan the existing agent uses
            try:
                t = ecs.describe_tasks(cluster=CLUSTER, tasks=[existing_agent])["tasks"][0]
                for c in t.get("overrides", {}).get("containerOverrides", []):
                    for env in c.get("environment", []):
                        if env.get("name") == "CAN_BUS0":
                            can_iface = env["value"]
            except Exception:
                pass

        # 2. Always start a new simulator task for this trip
        # Pass UDS_DTC_MAP so the simulator can spawn uds_dtc_responder.py
        # as a subprocess. Empty string means no DTC responder needed.
        sim_env = env_overrides + [
            {"name": "CAN_BUS0", "value": can_iface},
            {"name": "UDS_DTC_MAP", "value": uds_dtc_map_json},
        ]
        try:
            container_overrides = [
                {"name": "fwe-simulator", "command": args, "environment": sim_env},
            ]
            resp = ecs.run_task(
                cluster=CLUSTER,
                taskDefinition=fwe_sim_task_def,
                capacityProviderStrategy=cap_strategy,
                overrides={"containerOverrides": container_overrides},
            )
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

    return _resp(200, {"success": True, "simulation_id": sim_id, "task_arn": task_arn})


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

    SIM_TABLE.update_item(
        Key={"simulationId": sim_id},
        UpdateExpression="SET #s = :s, endTime = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "stopped", ":t": datetime.now(timezone.utc).isoformat()})

    return _resp(200, {"success": True})


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

    config = json.loads(item.get("config", "{}"))
    vehicles = config.get("vehicles", config.get("vehicles", 10))
    vehicle_count = len(vehicles) if isinstance(vehicles, list) else vehicles
    total_trips = config.get("trips", 3) * vehicle_count

    # Fetch logs from CloudWatch
    mode = config.get("mode", "mqtt_direct")
    sim_logs, fwe_logs = _get_worker_logs(task_arn, mode, agent_task_arn=item.get("agentTaskArn"))

    return _resp(200, {
        "id": sim_id,
        "status": item.get("status"),
        "start_time": item.get("startTime"),
        "end_time": item.get("endTime"),
        "task_arn": task_arn,
        "ecs_status": ecs_status,
        "config": config,
        "trips": {
            "total": total_trips,
            "completed": 0,
            "progress": 0,
        },
        "output": sim_logs,
        "fwe_logs": fwe_logs,
    })


def _get_worker_logs(task_arn, mode="mqtt_direct", agent_task_arn=None):
    """Fetch recent logs from CloudWatch. Returns (sim_logs, fwe_logs)."""
    if not task_arn:
        return [], []
    task_id = task_arn.split("/")[-1]
    if mode == "fwe":
        agent_task_id = agent_task_arn.split("/")[-1] if agent_task_arn else task_id
        sources = [
            (f"/ecs/cms-{STAGE}/fwe-simulator", f"sim/fwe-simulator/{task_id}"),
            (f"/ecs/cms-{STAGE}/fwe-agent", f"fwe/fwe-agent/{agent_task_id}"),
        ]
    else:
        sources = [(WORKER_LOG_GROUP, f"worker/worker/{task_id}")]
    results = []
    for log_group, stream_name in sources:
        entries = []
        try:
            resp = logs_client.get_log_events(
                logGroupName=log_group, logStreamName=stream_name,
                limit=100, startFromHead=False,
            )
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
        except Exception:
            pass
        results.append(entries[-50:])
    # Return (sim_logs, fwe_logs) — for non-FWE mode, fwe_logs is empty
    return results[0] if results else [], results[1] if len(results) > 1 else []


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
        cert_resp = cert_table.get_item(Key={"vehicleId": vehicle_id or vin})
        if "Item" in cert_resp:
            cert_pem = cert_resp["Item"].get("certificatePem", "")
            private_key = cert_resp["Item"].get("privateKey", "")
    except Exception as e:
        return _resp(400, {"error": f"Cert lookup failed: {e}"})

    if not cert_pem:
        return _resp(400, {"error": f"No certificate found for {vehicle_id}"})

    iot_endpoint = iot.describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
    can_iface = f"vcan{_next_vcan_index()}"

    try:
        resp = ecs.run_task(
            cluster=CLUSTER,
            taskDefinition=fwe_task_def,
            capacityProviderStrategy=[{
                "capacityProvider": fwe_cap_provider, "weight": 1, "base": 1,
            }] if fwe_cap_provider else [],
            overrides={"containerOverrides": [
                {"name": "fwe-agent", "environment": [
                    {"name": "VEHICLE_NAME", "value": vin},
                    {"name": "ENDPOINT_URL", "value": iot_endpoint},
                    {"name": "CAN_BUS0", "value": can_iface},
                    {"name": "CERTIFICATE", "value": cert_pem},
                    {"name": "PRIVATE_KEY", "value": private_key},
                    {"name": "TOPIC_PREFIX", "value": "cms/fleetwise/"},
                ]},
            ]},
        )
    except Exception as e:
        return _resp(500, {"error": str(e)})

    task_arn = resp["tasks"][0]["taskArn"] if resp.get("tasks") else None
    if not task_arn:
        return _resp(500, {"error": f"RunTask failed: {resp.get('failures', [])}"})

    return _resp(200, {"success": True, "task_arn": task_arn, "vin": vin})


def _agent_stop(config):
    """Stop all FWE agent tasks and mark vehicles as disconnected."""
    stopped = 0
    tasks = ecs.list_tasks(cluster=CLUSTER)["taskArns"]
    vins = set()
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
        except: pass
    # Mark vehicles as disconnected
    vehicles_table = ddb.Table(f"cms-{STAGE}-storage-vehicles")
    for vin in vins:
        try:
            resp = vehicles_table.scan(FilterExpression="vin = :v", ExpressionAttributeValues={":v": vin}, ProjectionExpression="vehicleId", Limit=1)
            if resp.get("Items"):
                vehicles_table.update_item(
                    Key={"vehicleId": resp["Items"][0]["vehicleId"]},
                    UpdateExpression="SET connectionStatus = :cs",
                    ExpressionAttributeValues={":cs": "disconnected"}
                )
        except: pass
    return _resp(200, {"success": True, "stopped": stopped})



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

def _agent_status():
    """Get running FWE agent tasks with VIN info."""
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

def _discover_iot():
    try:
        endpoint = iot.describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
        sts = boto3.client("sts")
        account = sts.get_caller_identity()["Account"]
        return _resp(200, {"success": True, "endpoint": endpoint, "region": REGION, "account": account})
    except Exception as e:
        return _resp(500, {"success": False, "error": str(e)})
