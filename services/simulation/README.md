# Fleet Simulation Service

Generates realistic vehicle telemetry for the Connected Mobility Solution. Supports two deployment modes (cloud and local) and two telemetry source modes (MQTT Direct and FleetWise Edge).

## Deployment Modes

### Cloud Simulator (ECS)

The cloud simulator runs on ECS and is managed through the UI and a Lambda-backed API Gateway. This is the production deployment.

**Architecture:**
```
UI → API Gateway → simulation_lambda.py → ECS RunTask
                                        ↓
                              ┌─────────────────────┐
                              │  MQTT Direct mode:   │
                              │  Fargate sim-worker  │
                              │  (JSON → IoT Core)   │
                              └─────────────────────┘
                              ┌─────────────────────────────────────┐
                              │  FWE mode (per vehicle):            │
                              │  EC2 fwe-agent task (persistent)    │
                              │    ├─ fwe-agent container           │
                              │    │  └─ reads vcanN, uploads       │
                              │    │     protobuf                   │
                              │    └─ vehicle-ecu sidecar           │
                              │       └─ owns commands, presence    │
                              │          state, idles on vcanN      │
                              │  EC2 fwe-simulator task (per-trip)  │
                              │    └─ signals vehicle-ecu via DDB   │
                              │       writes CAN frames on vcanN    │
                              └─────────────────────────────────────┘
```

**Key components:**
- `lambda/simulation_lambda.py` — API handler for start/stop/status/list, manages ECS tasks
- `cms-prod-sim-worker` task def — Fargate, MQTT Direct mode
- `cms-prod-fwe-agent` task def — EC2 with HOST networking, runs the [FWE agent](https://github.com/aws/aws-iot-fleetwise-edge) (v1.3.2) + `vehicle-ecu` sidecar
- `cms-prod-fwe-simulator` task def — EC2 with HOST networking + NET_ADMIN, generates CAN frames and signals the vehicle-ecu via DynamoDB

**Multi-vehicle FWE:** Each vehicle gets an isolated virtual CAN bus (`vcan0`, `vcan1`, etc.). The persistent FWE agent task owns one vcan. Per-trip simulator tasks signal the vehicle-ecu and write to the same vcan. The Lambda allocates the next available index by inspecting `CAN_BUS0` env vars on running agent tasks.

**Deploy:**
```bash
cd deployment
DEPLOYMENT_STAGE=prod DEPLOY_SIMULATION=true cdk deploy cms-prod-simulation --require-approval never
```

### Local Simulator (Docker + Python)

The local simulator runs on your development machine. The Flask API server manages simulations directly.

**Architecture:**
```
UI → localhost:5001 → simulation_api.py → Python threads (MQTT Direct)
                                        → Docker containers (FWE mode)
```

**Start:**
```bash
cd services/simulation
./manage_simulation.sh start
```

The UI auto-detects local vs cloud mode based on which API endpoint responds.

## Telemetry Source Modes

### MQTT Direct

The simulator publishes JSON telemetry directly to IoT Core via MQTT using the vehicle's X.509 certificate.

```
Simulator → MQTT (JSON) → IoT Core → IoT Rule → MSK cms-telemetry → Flink → DynamoDB
```

- Simple, no CAN bus or FWE agent needed
- Each message contains all telemetry fields as JSON
- Good for quick testing and development

### FleetWise Edge (FWE)

The simulator generates CAN frames on a virtual CAN bus. The FWE agent reads the CAN bus, decodes signals per the decoder manifest, filters per campaign rules, and uploads as protobuf.

```
Simulator → CAN frames → vcanN → FWE Agent → protobuf → IoT Core → MSK fw-telemetry-raw
  → FWTelemetryProcessor (decode + map) → MSK cms-telemetry-preprocessed → Flink → DynamoDB
```

- Realistic vehicle data pipeline using AWS IoT FleetWise
- Campaign-driven: change what's collected without code changes
- 262 VSS-aligned signals across 56 CAN messages (see `can/cms-fleet.dbc`)
- GPS encoded as CAN signals (`GPS_Position` message ID 456)

## Event selection → fire (signal contract)

When a trip is started with `safety_scenarios` / `maintenance_scenarios` selected, each selected
event fires only if its trigger signal name holds identically across five layers (catalog
`json_fields` → `EventCatalogDriver` injection → `can_encoder.py` key → FWE decoder-manifest field
→ processor `rule.jsonFields`). The authoritative per-event contract — required `json_fields`,
operator/threshold, FWE-supported vs MQTT-only, and observed firing results — is:

**[`docs/event-signal-contract.md`](../../docs/event-signal-contract.md)** — do not change the
event/signal catalogs or the decoder manifest without updating it first.

Caveats that affect what you observe:
- **Event IDs are namespaced** (`safety.harsh_acceleration`); bare names register as "Unknown event IDs".
- **Cooldown** `COOLDOWN_MS = 300_000` — one event per type per vehicle per 5 min.
- Some base signals (e.g. `phoneConnected`) are emitted opportunistically and can fire unselected
  events; deterministic selection-driving + opportunistic-emission gating ship in the simulator
  image — re-verify selection fidelity after a sim-image deploy.

## Trip-intent parameter propagation

When a trip is started on a **warm (already-running) vehicle container** via the CS portal or API with optional parameters (city, route length, event scenarios), the parameters are written to a `tripIntent` map in DynamoDB by `simulation_lambda.py::_write_trip_intent()` and consumed by the simulator's `PresenceLoop` before each trip.

**The Contract**: The key set written by the Lambda must exactly match the key set consumed by the simulator. Any divergence (new key written without a reader, or orphan read of a non-existent key) is enforced by `scripts/check_trip_intent_contract.py`, wired into `.github/workflows/lint.yml` at the `trip-intent-contract` job.

**Tier A — Per-trip Parameter Application** (spec 2026-09-20-trip-intent-param-contract):
- **`city`** and **`routeLength`** are applied to the simulator's instance state before `run_trips()` and restored after, per trip. An intent-driven trip cannot permanently mutate a long-lived container's configuration.
- Empty values (`city: ""`, `routeLength: 0`) are treated as "absent" and leave the container default untouched.
- Invalid cities log a warning and fall back to the container default; invalid route lengths are clamped to `[5, 60]`.

**Tier B — Event Scenarios** (implemented 2026-09-21, spec T4.2 commit `009b8b46`):
- **`safetyScenarios`** and **`maintenanceScenarios`** carry **event-catalog `event_id` values** — the dotted, prefixed form (`safety.harsh_braking`, `maintenance.low_oil_pressure`, `diagnostics.dtc.P0128`), **not** the bare `SCENARIOS` keys the `--scenario` CLI flag accepts. The two namespaces do not intersect; the reader keys on `event_id`.
- Applied via `EventCatalogDriver.set_active_events()` + `compute_degradation_targets()`, snapshotted before each trip and restored after, exactly as Tier A. The driver is constructed lazily on the first intent carrying a non-empty list and cached for the container's lifetime (its `__init__` does a full table scan).
- Empty lists (`[]`) are the "absent" sentinel, as with `city: ""` / `routeLength: 0` — they apply no override and do **not** clear a previous trip's scenarios. Clearing across trips is the restore side's job.
- A malformed value (non-list, or a bare string) is rejected and logged; it leaves `catalog_scenarios` empty **without** discarding the Tier A overrides from the same intent.
- `_DEFERRED_READERS` in the contract guard is now **empty**, and must stay that way. A new entry there is a claim that a key is written with no reader on purpose and temporarily; it needs a cited decision and an owning task.

**Implementation**:
- Writer: `services/simulation/lambda/simulation_lambda.py::_write_trip_intent()` writes nine keys (six consumed, three write-only-by-design).
- Reader: `services/simulation/realtime_telemetry_simulator.py::PresenceLoop.run()` consumes six keys (Tier A: `city`, `routeLength`; Tier B: `safetyScenarios`, `maintenanceScenarios`; plus `simulationId`, `tripsCount`).
- Guard: `scripts/check_trip_intent_contract.py` — AST-based extraction of written vs consumed keys, enforces equality mod allowlist; mutations verified per `scripts/tests/test_check_trip_intent_contract.py`.

**Guard Usage** (CI wired per T5.1):
```bash
# Check the contract locally
python3 scripts/check_trip_intent_contract.py
# Exit 0 if the allowlist matches exactly; non-zero if divergence detected.
```
<!-- verify: python3 scripts/check_trip_intent_contract.py -- the counts are printed by the
     guard itself; re-run it rather than trusting a number transcribed here, which is how
     this section carried '4 consumed, 2 awaiting a reader' after Tier B had already landed. -->

Note that the guard proves contract **shape**, not **effect** — a reader that binds a key and discards it satisfies it completely. Effect is covered by `services/simulation/tests/test_trip_intent_overrides.py` (by-value assertions) and by live verification against a warm container.

For full implementation details, see spec 2026-09-20-trip-intent-param-contract § "Decision D3" and decisions.md.

## Key Files

```
services/simulation/
├── lambda/
│   └── simulation_lambda.py          # Cloud simulator API (Lambda)
├── can/
│   └── cms-fleet.dbc                 # CAN database — 262 signals, 56 messages
├── realtime_telemetry_simulator.py   # Telemetry generation engine
├── can_encoder.py                    # Telemetry → CAN frame encoder (262 signal mappings)
├── can_bus_writer.py                 # CAN bus interface (auto-creates vcanN)
├── simulation_api.py                 # Local simulator API (Flask)
├── manage_simulation.sh               # Local service management
├── Dockerfile                        # Simulator container image
├── docker-compose.yml                # Local FWE + simulator stack
├── generate_fwe_persistency.py       # Generate FWE decoder manifest binaries
└── tests/
    └── test_trip_intent_overrides.py  # Unit tests for per-trip parameter application (city, routeLength)

Contract guards (scripts/):
├── check_trip_intent_contract.py     # Enforces tripIntent map writer/reader contract
├── tests/test_check_trip_intent_contract.py  # Guard tests (mutations verified)
└── check_trip_simulator_modal_sync.py  # Enforces CMS/CS modal event-list parity (unchanged)
```

## CAN Signal Architecture

The DBC file (`can/cms-fleet.dbc`) defines the complete vehicle signal model:

| Category | Messages | Signals | Examples |
|----------|----------|---------|----------|
| Engine/Powertrain | ECM_Engine_1/2/3, TCM | 18 | VehicleSpeed, EngineRPM, ThrottlePosition |
| ADAS/Safety | ADAS, ADAS_1/2, SAFETY, SAFETY_1 | 32 | AEBIsActive, LaneDepartureWarning, HarshBraking |
| Body/Cabin | BCM, DOORS, CABIN_CLIMATE | 40+ | DoorAllLocked, HVACMode, SeatbeltStatus |
| Tires | TPMS, TPMS_1, MAINTENANCE | 16 | TirePressureFL, TireTreadDepth |
| EV/Charging | EV_CHARGING_1-4, EV_SPECIFIC | 20+ | StateOfCharge, ChargingChargeRate |
| Connectivity | CONNECTIVITY, CONNECTIVITY_1 | 8 | CellularSignalStrength, WiFiConnected |
| Fleet/Geofence | GEOFENCE, GEOFENCE_1 | 14 | GeofenceIsViolated, FleetSpeedLimit |
| GPS | GPS_Position | 2 | Latitude, Longitude |
| **Total** | **56** | **262** | |

The `can_encoder.py` maps all 262 telemetry keys to DBC signal names. Values are clamped to each signal's bit-width capacity (`strict=False` encoding).

## Campaign System

Campaigns control what the FWE agent collects. Stored in DynamoDB (`cms-{stage}-campaigns`).

**Time-based** — collect all signals at a fixed interval:
```json
{
  "campaignName": "cms-fleet-telemetry-30s",
  "collectionScheme": { "type": "TIME_BASED", "periodMs": 30000 },
  "signalsToCollect": [1, 2, 3, ... 262 signal IDs],
  "decoderManifestId": "cms-fleet-v3"
}
```

**Condition-based** — collect when a signal threshold is met:
```json
{
  "campaignName": "cms-safety-harsh-braking",
  "collectionScheme": {
    "type": "CONDITION_BASED",
    "conditionExpression": "signal(40) > 0.3",
    "minimumIntervalMs": 1000
  }
}
```

The **CampaignSyncProcessor** (Flink) listens for FWE agent checkins and pushes the appropriate decoder manifest + collection schemes to each vehicle in real-time.

## API Endpoints (Cloud)

All routes are under the API Gateway base URL.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/simulation/start` | Start a simulation (MQTT Direct or FWE) |
| POST | `/simulation/stop/{id}` | Stop a simulation |
| GET | `/simulation/status/{id}` | Get sim status + logs |
| GET | `/simulation/list` | List all simulations |
| POST | `/simulation/agent/start` | Start FWE agent only |
| POST | `/simulation/agent/stop` | Stop FWE agent |
| GET | `/simulation/agent/status` | Get running agent tasks |
| GET | `/simulation/agent/logs/{vin}` | Get FWE agent logs for VIN |
| GET | `/simulation/campaigns` | List active campaigns |
| GET | `/simulation/presets` | Get simulation presets |

## Connection Status

Vehicle connection status is managed in Redis by the Flink pipeline:

- **MQTT Direct**: `EventDrivenTelemetryProcessor` sets `connected` on each telemetry message
- **FWE mode**: `CampaignSyncProcessor` sets `connected` on each agent checkin, marks `disconnected` after 2 minutes of no checkins (configurable via `FWE_DISCONNECT_TIMEOUT_MS`)

The API includes a staleness guard: if `lastSeenAt` is older than 2 minutes, the vehicle shows as disconnected regardless of the Redis value.

## Remote Commands (In Progress)

FWE v1.3.2 supports native remote commands. The agent subscribes to:
```
cms/commands/things/{VIN}/executions/+/request/protobuf
```

The Commands Lambda builds a protobuf `CommandRequest` with the actuator signal ID and value, and publishes to this topic. Full CAN actuator dispatch via the Network Agnostic Data Collection (NADC) approach is planned for a future release.

## Fault events: how they surface

Maintenance scenarios in the Trip Simulator can include both signal-based events (e.g., harsh braking, high temperature) and discrete fault events (e.g., brake system fault, transmission failure). Fault events are distinguished by the presence of a `dtc_code` (Diagnostic Trouble Code) in the event catalog.

### Signal-based maintenance events

Events without a `dtc_code` (e.g., `filter_replacement`, `low_battery`, `oil_life_low`, `tire_tread_low`, `washer_fluid_low`) surface via the catalog-driven threshold path in both MQTT Direct and FWE modes. The simulator injects the scalar `dtc_codes_active` or a matching signal value into the telemetry, which the `MaintenanceProcessor` evaluates against catalog thresholds to generate `maintenance-alerts` rows.

### Fault events with UDS-DTC codes

Maintenance scenarios with a `dtc_code` (e.g., `maintenance.brake_system_fault` → P-code P1234, `maintenance.transmission_failure` → P0700, `maintenance.engine_misfire` → P0300) surface via the authentic UDS-DTC pipeline when running in **FWE mode**:

1. **Selection → DTC registration**: When a fault scenario is selected in the Trip Simulator UI, the `simulation_lambda` resolves its `dtc_code` from the event catalog and registers it in the `UDS_DTC_MAP` passed to the FWE simulator task.
2. **UDS polling**: The FWE agent fires UDS Service 0x19 requests (poll every 30s) to the virtual responder running on the simulator.
3. **FWE signal emission**: FWE packages the reported DTCs as `Vehicle.ECU{n}.DTC_INFO` STRING signals (e.g., `Vehicle.ECU1.DTC_INFO` contains the JSON envelope with fault codes).
4. **Flink processing**: `FWTelemetryProcessor` parses the STRING envelope and emits one synthetic `record_kind="uds_dtc"` JSON record per DTC entry. `MaintenanceProcessor` consumes these records and:
   - Reverse-looks up the DTC code to recover the original `event_id` (e.g., P1234 → `maintenance.brake_system_fault`)
   - Resolves the associated `tripId`
   - Deduplicates within the trip (one `maintenance-alerts` row per unique DTC per trip)
   - Writes **both** `maintenance-alerts` and `dtc-history` rows with `source="fwe-uds-dtc"`
   - For CRITICAL/HIGH severity faults, emits a `vfo-action-queue` pending action row
5. **Result**: The selected fault event materializes in both:
   - `maintenance-alerts` table (with `alertType=event_id`, tripId, severity)
   - `dtc-history` table (with tripId, dtc code, status)
   - VFO Fleet Command Center's Pending Actions card (for CRITICAL/HIGH)

**Mode specificity**: This FWE-UDS-DTC path is only active in **FWE mode** (`SIM_MODE=fwe`). In **MQTT Direct mode** (`SIM_MODE=mqtt_direct`), fault events without an explicit signal in the `can_encoder.py` will not surface; they must be seeded via the catalog/threshold path or emitted manually.

For documentation of the full UDS-DTC pipeline, signal routing, and troubleshooting, see `docs/FWE_UDS_DTC.md` and `docs/fault-event-dtc-routing.md`.

## Authorization & Fleet Scoping

The simulation API (`lambda/simulation_lambda.py`) enforces role-based authorization and fleet-scoped access control on all 12 routes. This section documents the authorization model, identity extraction, and caller configuration requirements.

### Authorization Gates by Route

All routes are protected by one of three gates, enforced at dispatch entry before handler execution:

| Route | Method | Gate | Description |
|-------|--------|------|-------------|
| `/start` | POST | Per-VIN fleet scope | Start simulation; must own all vehicles in `config.vehicles` |
| `/stop/{id}` | POST | Per-VIN fleet scope | Stop simulation; must own all vehicles in the sim's config |
| `/status/{id}` | GET | Per-VIN fleet scope (read) | Retrieve sim status; viewer allowed |
| `/list` | GET | Listing filter | List all sims (admin) or filter to caller's fleets (operator/viewer) |
| `/agent/start` | POST | Per-VIN fleet scope | Start FWE agent for single vehicle; must own the vehicle's fleet |
| `/agent/stop` | POST | Admin only | Stop all FWE agent tasks; reserved for platform-admin |
| `/agent/status` | GET | Authenticated | Get running agent task count; any authenticated caller allowed |
| `/agent/logs/{vin}` | GET | Per-VIN fleet scope (read) | Get FWE agent logs for a vehicle; viewer allowed |
| `/campaigns` | GET | Listing filter | List campaigns (admin) or filter to caller's fleets (operator/viewer) |
| `/drivers` | GET | Listing filter | List drivers (admin) or filter to caller's fleets (operator/viewer) |
| `/presets` | GET | Authenticated | Get simulation presets; any authenticated caller allowed |
| `/discover-iot-endpoint` | GET | Authenticated | Get IoT endpoint URL; any authenticated caller allowed |
| `/vehicle/{id}/faults` | PUT/GET | Per-VIN fleet scope | Set/retrieve faults for a vehicle; viewer denied on writes |

### Identity Model

Caller identity is extracted from the Cognito JWT claims at request dispatch entry:

```python
# From lambda/simulation_lambda.py::_extract_caller(event)
claims = {
    'cognito:groups': 'platform-admin,fleet-operator',  # comma-separated role list
    'custom:fleetIds': 'fleet-A,fleet-B',               # comma-separated fleet IDs (operator/viewer only)
    'custom:driverId': 'driver-123',                     # present for driver-self tokens (mobile app)
    'email': 'operator@example.com'
}
```

**Role classification:**
- **`platform-admin`** in `cognito:groups` → admin bypass (full access, no fleet filtering)
- **`fleet-operator`** in `cognito:groups` → scoped to `custom:fleetIds`; can perform write operations
- **`fleet-viewer`** in `cognito:groups` (without `fleet-operator`) → scoped to `custom:fleetIds`; read-only
- **`fleet-guest`** in `cognito:groups` → scoped read-only (Phase B placeholder)
- **`custom:driverId` present** (any group) → driver-self token (e.g., mobile app); denied on all routes

**Fail-open closure:** The authorization model enforces NO fallback grants. A token with empty `cognito:groups` is treated as unauthenticated and receives 403, not promoted to admin. This matches the post-fix `main_api/index.py:1246` pattern.

### Per-VIN Fleet Scoping

Routes that operate on specific vehicles (start, stop, agent operations) resolve vehicle VINs to their fleet assignment via the `FLEET_ENROLLMENT_TABLE_NAME` table and its `vehicleId-index` GSI, using the shared helper `resolve_vins_to_fleets(vins: list[str]) -> dict[vin, fleet_id]` from `_lib/fleet_membership.py`.

**Authorization logic:**
```
IF caller is platform-admin:
    ADMIT (no fleet check needed)
IF caller is driver-self:
    DENY with 403
IF caller has no custom:fleetIds:
    DENY with 403
IF (for write routes) caller is fleet-viewer:
    DENY with 403 (viewers are read-only)
FOR each VIN in the request:
    IF VIN cannot be resolved to a fleet (404):
        DENY with 404 "Unknown VIN"
    IF resolved fleet NOT in caller's custom:fleetIds:
        DENY with 403 "Caller not authorized for fleet of VIN(s): [...]"
ADMIT request
```

Listing routes (`/list`, `/campaigns`, `/drivers`) apply the same fleet membership check but filter results instead of returning 403; admin callers receive unfiltered results.

### Configuring a New Caller

To grant a new user or service role access to the simulation API:

1. **Create a Cognito user** in the CMS User Pool (`cms-{stage}-storage-user-pool-cognitodb`) with:
   - `email` or `preferred_username` for identification
   - `custom:fleetIds` attribute set to the comma-separated list of fleet IDs the caller owns (e.g., `fleet-A,fleet-B`)
   - Group membership:
     - For admin: add to `platform-admin` group (full access, bypasses fleet checks)
     - For operator: add to `fleet-operator` group (can start/stop/write, scoped to `custom:fleetIds`)
     - For viewer: add to `fleet-viewer` group (read-only, scoped to `custom:fleetIds`)

2. **Update Lambda IAM role** in `deployment/stacks/simulation_stack.py`:
   - The sim Lambda role already has `dynamodb:Query` on the `vehicleId-index` GSI of `FLEET_ENROLLMENT_TABLE_NAME` (added during Phase B deploy)
   - No additional IAM changes needed per-caller (role grants are table-wide, not per-user)

3. **Verify in deployment**:
   ```bash
   # Check the FLEET_ENROLLMENT_TABLE_NAME env var is set
   aws lambda get-function-configuration \
     --function-name cms-staging-simulation-SimulationApiFunction-<hash> \
     --region us-west-2 --query 'Environment.Variables' | grep FLEET_ENROLLMENT_TABLE_NAME

   # Verify IAM grant (should show Query on vehicleId-index)
   aws iam get-role-policy \
     --role-name cms-staging-simulation-SimulationApiServiceRole<hash> \
     --policy-name SimulationApiServiceRoleDefaultPolicy<hash> \
     --query 'RolePolicy.PolicyDocument' | jq '.Statement[] | select(.Action[] == "dynamodb:Query")'
   ```

### Shared Library: `_lib/fleet_membership.py`

The sim Lambda includes a bundled copy of `services/connectors/oem1/_lib/fleet_membership.py` via the build helper `_bundle_sim_lambda()` in `deployment/stacks/simulation_stack.py`. This is NOT a code duplication — the build overlay copies the source at synth time, keeping the connector's `_lib/` as the single source of truth.

**Public helpers used by the sim Lambda:**
- `resolve_vins_to_fleets(vins: list[str]) -> dict[str, str]` — Query the `vehicleId-index` GSI to map VINs to fleet IDs
- `classify_driver_self(claims: dict, driver_self_enabled: bool = True) -> tuple[bool, str]` — Detect driver-self tokens by checking for `custom:driverId` claim

**Environment variables required:**
- `FLEET_ENROLLMENT_TABLE_NAME` — The DynamoDB table name for fleet-vehicle mappings (e.g., `cms-staging-storage-fleet-enrollment`). Set by the CDK stack.

### Source-Scan Invariants

The authorization model is enforced by three executable assertions in `services/simulation/lambda/test_simulation_lambda.py`. These tests ensure correctness and prevent regression of the fail-open class:

1. **`test_no_fail_open_invariant`** — scans `simulation_lambda.py` source and asserts:
   - `is_admin = 'platform-admin' in user_groups` appears exactly once
   - `or not user_groups` appears zero times (the P0 fail-open pattern, removed)

2. **`test_route_coverage_invariant`** — enumerates every route dispatch branch in `handler()` and asserts each route calls an appropriate `_authorize_*` gate or is documented as "any authenticated"

3. **`test_driver_self_denial`** — verifies driver-self tokens receive 403 on every route

Verify these before deploying any changes to authorization logic:
```bash
cd services/simulation/lambda && python3 -m pytest test_simulation_lambda.py::test_no_fail_open_invariant test_simulation_lambda.py::test_route_coverage_invariant test_simulation_lambda.py::test_driver_self_denial -v
```

### Design Decision: `_OPERATOR_GROUPS` Composition

The authorization model treats `_OPERATOR_GROUPS = {'platform-admin', 'fleet-operator'}` as scoped-access roles (both require `custom:fleetIds`), and keeps `fleet-viewer` separate as a strictly read-only, scoped role. This diverges from `main_api/index.py`, which includes `fleet-viewer` in its `_OPERATOR_GROUPS` set.

**Rationale:** The sim Lambda's routes are all write-adjacent (start/stop compute) or listing-oriented, with no read-only routes that benefit from a hybrid viewer+operator token. The 2-member set is stricter and safer. For full details on this decision, see `.kiro/specs/2026-08-31-cms-sim-api-fleet-scoping/decisions.md#2026-08-31--_operator_groups-is-a-2-member-set-not-3`.

## License

Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0
