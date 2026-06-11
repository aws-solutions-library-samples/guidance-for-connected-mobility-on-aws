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

## License

Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0
