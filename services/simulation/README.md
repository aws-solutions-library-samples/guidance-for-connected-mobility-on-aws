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
                              │  EC2 fwe-agent task (long-lived)    │
                              │    └─ reads vcanN, uploads protobuf │
                              │  EC2 fwe-simulator task (per-trip)  │
                              │    └─ generates CAN frames on vcanN │
                              └─────────────────────────────────────┘
```

**Key components:**
- `lambda/simulation_lambda.py` — API handler for start/stop/status/list, manages ECS tasks
- `cms-prod-sim-worker` task def — Fargate, MQTT Direct mode
- `cms-prod-fwe-agent` task def — EC2 with HOST networking, runs the [FWE agent](https://github.com/aws/aws-iot-fleetwise-edge) (v1.3.2)
- `cms-prod-fwe-simulator` task def — EC2 with HOST networking + NET_ADMIN, generates CAN frames

**Multi-vehicle FWE:** Each vehicle gets an isolated virtual CAN bus (`vcan0`, `vcan1`, etc.). The Lambda allocates the next available index by inspecting `CAN_BUS0` env vars on running agent tasks.

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
├── manage_simulation.sh              # Local service management
├── Dockerfile                        # Simulator container image
├── docker-compose.yml                # Local FWE + simulator stack
└── generate_fwe_persistency.py       # Generate FWE decoder manifest binaries
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

## License

Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0
