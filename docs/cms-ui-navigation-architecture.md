# CMS UI — Navigation & Data Architecture

**Date:** March 2026 | **Status:** Active
**Last updated:** 2026-07-17 — added dispatcher persona nav branch
(spec `.kiro/specs/2026-07-17-cms-dispatcher-persona-nav-scope/`)

---

## Persona-based nav branches (App.tsx SideNavigation)

The sidebar is persona-gated via booleans from `useUserRole()`. Three
mutually-exclusive branches, evaluated top-down:

| Order | Branch | Predicate | Items rendered |
|---|---|---|---|
| 1 | **Pure dispatcher** | `isDispatcher && !isAdmin && !isOperator && !isEngineer && !isConnectAgent` | Vehicles, Vehicle Map, Fleets, Drivers, Service, Safety (6 items, no dividers, no Settings) |
| 2 | **Pure engineer** | `isEngineer && !isOperator && !isAdmin` | Fleets, Vehicles, Vehicle Map, Drivers → Engineering Insights, Digital Thread → Data Processing → Settings |
| 3 | **Fallback (everyone else)** | else | Full nav with sub-block gates on `!isConnectAgent \|\| isAdmin \|\| canWrite`, `canWrite`, `isAdmin`, `isConnectAgent \|\| isAdmin`, `isEngineer \|\| isAdmin` |

The dispatcher branch is a POSITIVE assertion of what a dispatcher-only
user sees — an intentional deviation from the fallback branch's more
permissive "everyone except Connect agents" gate. It targets the prod
demo persona `kevin.dispatch@example.com` (in the `dispatcher` Cognito
group ONLY) without weakening any other persona's access.

Dispatcher backend behaviour (`main_api/index.py`):
- `is_admin` = false (dispatcher not in `platform-admin`)
- `is_viewer` = false (dispatcher not in `fleet-viewer`)
- `user_fleet_ids` = empty (no `custom:fleetIds` claim on the demo account)
- `get_allowed_vehicle_ids()` returns `None` (no filter) via the
  `is_admin or not user_fleet_ids` gate — dispatcher gets cross-fleet
  READ access.
- Admin-only mutations (`/api/v1/users`, driver-users, OEM1 admin
  handlers) 403 dispatcher — expected.

Websocket handler (`services/websocket/lambda/websocket_handler.py`):
- Dispatcher is NOT admin → cannot request `fleetId=*` all-fleet stream.
- Must supply a specific `fleetId`. With empty `custom:fleetIds` the
  membership check skips and the connection succeeds for any requested
  fleet.

---

## 1. Navigation Hierarchy

```
┌─────────────────────────────────────────────────┐
│  Connected Mobility Fleet Console                │
├─────────────────────────────────────────────────┤
│  Fleets              → Fleet management, enrollment, fleet details
│  Vehicles            → Vehicle inventory, detail, telemetry, trips
│  Drivers             → Driver profiles, assignments, behavior scoring
│  Energy              → Fuel + EV charging operations, efficiency
│  Service             → Maintenance, recalls, telemetry-driven alerts
│  Safety              → Safety events, driver incidents, geofences
│  Warranty            → Claims, recovery, coverage tracking
│  Costs               → TCO: aggregates all cost sources
│  Rebalancing         → Utilization, demand forecasting, move recommendations
│  ─────────────────
│  Subscriptions       → Data source subscriptions (write access)
│  ─────────────────
│  User Management     → Admin: users, roles, fleet access
│  Simulation          → Admin: telemetry simulation
│  Data Processing     → Admin: Flink apps, signal catalog
│  System Monitoring   → Admin: system health
│  Analytics           → Admin: telemetry dashboards
│  Settings            → Admin: configuration
└─────────────────────────────────────────────────┘
```

---

## 2. Page Definitions & Data Sources

### Fleets (`/fleets`)
**Purpose:** Create, manage, and monitor vehicle fleets
**Data sources:**
- DynamoDB: `cms-prod-storage-fleets`, `cms-prod-storage-fleet-enrollment`
- Redis: fleet-level aggregated state
**Status:** ✅ Live with real data

### Vehicles (`/vehicles`, `/vehicles/:vehicleId`)
**Purpose:** Vehicle inventory, individual vehicle detail with telemetry, trips, recalls
**Data sources:**
- DynamoDB: `cms-prod-storage-vehicles`
- Redis: real-time vehicle state (telemetry signals)
- Telemetry: `cms-telemetry-preprocessed` → Redis via Flink
- NHTSA: recall data matched to vehicle make/model
**Tabs:** Overview | Trips | Safety | Maintenance | Campaigns | Recalls | Commands | Logs
**Status:** ✅ Live with real telemetry data + NHTSA recalls

### Drivers (`/drivers`)
**Purpose:** Driver profiles, fleet assignments, behavior scoring
**Data sources:**
- DynamoDB: `cms-prod-storage-drivers`
- Telemetry-derived: hard braking, acceleration, speeding events
**Status:** 🟡 Stubbed — needs telemetry-driven behavior scoring

### Energy (`/energy`) — renamed from Charging
**Purpose:** Fuel and EV charging operations, efficiency monitoring
**Data sources:**
- Telemetry-derived (automatic):
  - ICE: `fuelLevel` signal deltas + `odometer` → fuel consumption (gallons), fuel efficiency (MPG)
  - EV: `batterySoC` deltas → energy consumption (kWh), charging sessions, charge rate
  - Both: cost per mile (fuel or energy)
- External (CSV upload / future API):
  - Fuel card transactions (WEX, Comdata, FleetCor)
  - EV charging sessions (ChargePoint, EVgo)
  - Grid pricing data for charge schedule optimization
**Views:**
- Fleet energy overview: total fuel spend, total charging spend, avg MPG, avg kWh/mile
- Vehicle energy detail: per-vehicle fuel/charging history, efficiency trends
- ICE vs EV comparison: cost per mile by fuel type, by route
- Charge scheduling (EV): current schedule, grid pricing overlay, optimization recommendations
- Fuel efficiency alerts: vehicles with declining MPG, excessive idle
**Feeds into:** Costs (fuel spend, charging spend, energy cost per mile)
**Status:** 🔴 Needs rebuild — current "Charging" page is EV-only stub

### Service (`/service`)
**Purpose:** Maintenance operations, recall management, telemetry-driven alerts
**Data sources:**
- Telemetry-derived (automatic):
  - Brake wear: derived from hard braking frequency + mileage
  - Tire health: from tire pressure signals + predictive maintenance model
  - Oil life: estimated from engine hours + mileage
  - Battery health (EV): from SoH degradation curves
  - DTC codes: from telemetry diagnostic signals
- NHTSA recalls: real data from `nhtsa_recall_poller.py`, matched to fleet VINs
- Maintenance history: DynamoDB (service records, work orders)
- Predictive maintenance: alerts from `guidance-for-predictive-maintenance` pipeline
**Views:**
- Service dashboard: KPI tiles (in service, expenses, PM compliance, active recalls)
- Scheduled maintenance: upcoming appointments, overdue items
- Recall management: active recalls with NHTSA data, VIN matching, telemetry cross-reference, compliance tracking
- Telemetry alerts: real-time maintenance alerts from vehicle signals
- Service history: past work orders per vehicle
**Feeds into:** Costs (maintenance spend, recall service costs), Warranty (completed repairs → eligibility check)
**Status:** 🟡 Partially stubbed — has UI but all mock data. NHTSA integration built but not wired to this page yet.

### Safety (`/safety`)
**Purpose:** Safety events, driver incidents, geofence violations
**Data sources:**
- Telemetry-derived: hard braking, rapid acceleration, speeding, lane departure
- Flink SafetyProcessor: `cms-telemetry-safety` topic → DynamoDB
- Geofence events: Amazon Location Service geofence monitoring
**Status:** 🟡 Stubbed — SafetyProcessor exists but events not flowing to UI consistently

### Warranty (`/warranty`)
**Purpose:** Warranty claim management, financial recovery
**Data sources:**
- Warranty terms: DynamoDB (coverage rules by OEM, component, mileage, age)
- Warranty-eligible failures: detected by agent from telemetry + maintenance history
- Claim tracking: DynamoDB (status: drafted → submitted → approved → paid → denied)
- NHTSA recall data: completed recall services may trigger warranty claims
**Views:**
- Warranty dashboard: KPI tiles (eligible unfiled, recovered YTD, open claims, expiring soon)
- Eligible failures table: agent-detected, with telemetry evidence, coverage remaining
- Claim tracking: filed claims with OEM status
- Expiring coverage: vehicles approaching warranty limits with known issues
**Feeds into:** Costs (warranty recoveries as negative cost line item)
**Status:** 🟢 UI built with mock data — needs agent integration for real eligibility detection

### Costs (`/fleet-costs`)
**Purpose:** Total cost of ownership — the financial intelligence layer
**Data sources — aggregated from all other domains:**

| Cost Category | Source Page | Data Flow |
|---|---|---|
| Fuel spend | Energy | Telemetry-derived fuel consumption × $/gallon |
| EV charging | Energy | Telemetry-derived kWh × $/kWh or charging session data |
| Maintenance | Service | Work order costs, parts, labor |
| Recall costs | Service | Recall service costs + revenue loss from grounded vehicles |
| Warranty recovery | Warranty | Claim payments (negative cost) |
| Insurance | External | CSV upload or manual entry |
| Depreciation | External | Age/mileage curves or market-based (Manheim/KBB) |
| Transfer costs | Rebalancing | Vehicle move costs (distance × $/mile + driver time) |
| Idle costs | Energy | Telemetry-derived idle time × fuel burn rate |
| Toll costs | External | CSV upload (PrePass, Bestpass) |

**Views:**
- TCO dashboard: KPI tiles, cost trend, cost breakdown by category
- Cost outliers: vehicles/routes/drivers above fleet average
- Lifecycle: buy/sell/hold recommendations, residual value tracking
- Action queue: agent recommendations for cost optimization
- Agent feed: real-time cost monitoring activity
**Status:** 🟢 UI built with mock data — needs real data pipeline (Flink CostProcessor)

### Rebalancing (`/fleet-rebalancing`)
**Purpose:** Fleet utilization optimization, demand-driven vehicle placement
**Data sources:**
- Telemetry-derived: vehicle location, status (active/idle/in-transit), trip data
- Fleet enrollment: vehicle-to-location assignments
- Demand signals: booking data (CSV upload), historical utilization patterns
- Constraints: depot capacity, maintenance schedule, charging infrastructure
**Views:**
- Rebalancing dashboard: KPI tiles, utilization bar chart, location status table
- Supply-demand heatmap: Amazon Location Service map with utilization markers
- Action queue: agent-recommended moves with cost/revenue estimates
- Vehicle availability: filterable list of vehicles available for rebalancing
- Agent feed: real-time detection, forecasting, recommendation activity
**Feeds into:** Costs (transfer costs, utilization-driven revenue impact)
**Status:** 🟢 UI built with mock data + real map — needs UtilizationProcessor (Flink)

---

## 3. Data Flow Architecture

```
TELEMETRY PIPELINE (existing, real data flowing)
cms-telemetry-preprocessed
    │
    ├── EventDrivenTelemetryProcessor (Flink) → existing routing
    │   ├── cms-telemetry-trips        → Trips tab on vehicle detail
    │   ├── cms-telemetry-safety       → Safety page
    │   ├── cms-telemetry-maintenance  → Service page alerts
    │   └── Redis vehicle state        → All pages (real-time)
    │
    ├── CostProcessor (Flink) → NEW
    │   ├── Derives: fuel consumption, EV energy, idle cost, cost/mile
    │   ├── Writes: S3 Iceberg + Redis + DynamoDB
    │   └── Feeds: Costs page + Energy page
    │
    ├── UtilizationProcessor (Flink) → NEW
    │   ├── Derives: per-vehicle, per-location utilization
    │   ├── Writes: S3 Iceberg + Redis + DynamoDB
    │   └── Feeds: Rebalancing page
    │
    └── Energy signals → NEW extraction
        ├── fuelLevel deltas → fuel consumption → Energy page
        ├── batterySoC deltas → charging sessions → Energy page
        └── ignitionOn + speed=0 → idle time → Energy page + Costs page

EXTERNAL DATA (new integrations)
├── NHTSA Recalls API → Lambda poller → Service page + Vehicle detail
├── Warranty terms → DynamoDB → Warranty page
├── CSV uploads → S3 → Glue ETL → Costs page (insurance, depreciation, tolls)
└── Demand signals → S3 → Rebalancing page

AGENT PIPELINE (new)
├── DynamoDB Streams → Agent Core Gateway → Bedrock Agents
├── Cost Agent → Costs page action queue
├── Rebalancing Agent → Rebalancing page action queue
├── Recall & Warranty Agent → Service + Warranty action queues
└── Virtual Fleet Operator → Fleet Command Center (future)
```

---

## 4. Implementation Priority

### Phase 1: Wire real telemetry to existing pages
1. **Energy page rebuild** — extract fuel/charging signals from Redis vehicle state, show real consumption data
2. **Service page rebuild** — wire NHTSA recalls (already fetched), show telemetry-driven maintenance alerts from Redis
3. **Safety page** — ensure SafetyProcessor events flow to UI

### Phase 2: Build new processors
4. **CostProcessor (Flink)** — derive cost metrics from telemetry, write to Redis + Iceberg
5. **UtilizationProcessor (Flink)** — derive utilization from telemetry, write to Redis + Iceberg
6. **Wire Costs page** to real CostProcessor output
7. **Wire Rebalancing page** to real UtilizationProcessor output

### Phase 3: Agent integration
8. **Bedrock Agents** — Cost, Rebalancing, Recall & Warranty
9. **Action queues** — real recommendations from agents
10. **Virtual Fleet Operator** — unified command center

---

## 5. Redis Vehicle State — Signal Mapping

The telemetry normalization pipeline writes canonical signals to Redis. Here's what each page reads:

| Redis Key | Signals Used | Pages |
|---|---|---|
| `vehicle:{id}:state` | speed, lat, lng, heading, ignitionOn | Vehicles, Fleets (map) |
| `vehicle:{id}:state` | fuelLevel, odometer | Energy (fuel consumption) |
| `vehicle:{id}:state` | batterySoC, batteryVoltage | Energy (EV charging) |
| `vehicle:{id}:state` | engineRPM, engineTemp | Service (engine health) |
| `vehicle:{id}:state` | tire_fl, tire_fr, tire_rl, tire_rr | Service (tire alerts), Vehicles (tire widget) |
| `vehicle:{id}:cost` | cost_per_mile, fuel_cost_mtd, maintenance_mtd | Costs |
| `vehicle:{id}:utilization` | status, idle_since, active_hours | Rebalancing |
| `location:{id}:utilization` | vehicle_count, active_count, utilization_pct | Rebalancing |
