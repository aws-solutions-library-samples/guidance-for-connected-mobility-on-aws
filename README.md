# Guidance for Connected Mobility on AWS

A comprehensive reference accelerator with CDK modules to help customers build fleet management, telematics, and connected vehicle applications on AWS using modern streaming analytics and IoT platforms.

**Multi-OEM telemetry normalization** is a named capability of this accelerator: telemetry
arriving in different vendor shapes is normalized into one canonical model, so a mixed-OEM fleet
presents a single operational view. The design is documented in
[docs/oem-telemetry-normalization-design.md](docs/oem-telemetry-normalization-design.md).

Stated plainly, because the distinction matters when evaluating this repo: **one vendor source is
implemented** end-to-end, and the second-source path is **open and unpopulated** — the manifest
and transform seams exist and are exercised by one vendor, but no second vendor source ships
here. Adding one is integration work against those seams, not new architecture.

> **Migrating from AWS IoT FleetWise?** AWS's IoT FleetWise
> [availability-change notice](https://docs.aws.amazon.com/iot-fleetwise/latest/developerguide/iotfleetwise-availability-change.html)
> states: *"The Guidance for Connected Mobility on AWS provides guidance on how to
> develop and deploy modular services for connected mobility solutions that can be
> used to achieve equivalent capabilities as AWS IoT FleetWise."* See
> [`docs/migrating-from-fleetwise.md`](./docs/migrating-from-fleetwise.md) for a
> concept map, an honest disclosure of two gaps (data destinations and vision-system
> data), and pointers into the code.
>
> **Note on terminology.** Throughout this repository, **"FleetWise Edge" / "FWE"**
> refers to the open-source
> [AWS IoT FleetWise Edge Agent](https://github.com/aws/aws-iot-fleetwise-edge)
> (built from source in `deployment/ecr/cms-fwe-agent/Dockerfile`) — not the AWS IoT
> FleetWise managed service. CMS does not depend on the managed service.

## Table of Contents

1. [Overview](#overview)
    - [Cost](#cost)
2. [Prerequisites](#prerequisites)
    - [Operating System](#operating-system)
3. [Deployment Steps](#deployment-steps)
4. [Deployment Validation](#deployment-validation)
5. [Running the Guidance](#running-the-guidance)
6. [Next Steps](#next-steps)
7. [Cleanup](#cleanup)
8. [FAQ, known issues, additional considerations, and limitations](#faq-known-issues-additional-considerations-and-limitations)
9. [Notices](#notices)
10. [Authors](#authors)

## Overview

This Guidance provides a modern, scalable telemetry architecture designed to handle high-volume, real-time data streams from connected vehicle fleets. It addresses the challenge of building enterprise-grade connected mobility platforms by providing pre-built, production-ready components that follow AWS Well-Architected principles.

**Why did we build this Guidance?**
Connected mobility applications require complex integration of IoT devices, real-time analytics, fleet management, and safety compliance systems. This Guidance accelerates development by providing tested, scalable components that customers can customize for their specific requirements.

**What problem does this Guidance solve?**
- Eliminates months of development time for core connected mobility infrastructure
- Provides secure, scalable telemetry ingestion and processing
- Implements industry best practices for fleet management and safety compliance
- Offers realistic simulation capabilities for testing without physical vehicle fleets
- Remote diagnostics — on-demand DTC read + clear + freeze-frame retrieval, ASAM SOVD-aligned JSON, over the existing MQTT commands transport

**Note on dealer-side surfaces:** Dealer-facing operations (service lane, warranty, certification, inventory, parts) have been separated into a companion accelerator, Guidance for Dealer Management System on AWS, a standalone application with its own React frontend and API. CMS focuses on fleet-operator and vehicle-owner journeys; DMS handles the dealer-network side of the business logic. <!-- verify: the DMS accelerator has no public mirror yet — do NOT add a repository link until one exists; both aws-samples and aws-solutions-library-samples returned 404 on 2026-09-02. -->

### Telemetry Source Modes

The solution supports four telemetry ingestion modes that can operate independently or side-by-side:

| Mode | Description | Data Path |
|------|-------------|-----------|
| **MQTT Direct** | Simulator publishes JSON telemetry directly to IoT Core via MQTT | IoT Core → MSK `cms-telemetry` → Flink processors → DynamoDB |
| **FleetWise Edge (FWE)** | [AWS IoT FleetWise Edge Agent](https://github.com/aws/aws-iot-fleetwise-edge) (v1.3.2) collects CAN bus signals based on campaign collection schemes, encodes as protobuf, and uploads to the cloud | FWE Agent → IoT Core → MSK `fw-telemetry-raw` → FWTelemetryProcessor (protobuf decode + signal mapping) → MSK `cms-telemetry-preprocessed` → Flink processors → DynamoDB |
| **OEM Cloud Connectors** | Real OEM cloud-to-cloud feeds (e.g., OEM1 gRPC streaming, REST polling). See [`services/connectors/`](./services/connectors/README.md#grpc-streaming-oem1-reference-implementation) | OEM gRPC/REST → MSK `cms-telemetry-oem` → OEMTelemetryProcessor (manifest-driven transform) → MSK `cms-telemetry-preprocessed` → Flink processors → DynamoDB |
| **Connected Services (CS) Bridge** | Third-party external producers post telemetry via HTTP to the CS ingest endpoint; a scheduled puller normalizes and writes to canonical storage | HTTP POST → API Gateway → `cs-source-telemetry` DDB table → puller Lambda (1-min cron) → canonical `cms-storage-telemetry` DynamoDB <!-- verify: grep -c "cs-source-telemetry" deployment/stacks/meridian_ingestion_stack.py | xargs -I {} test {} -gt 0 && echo "table exists" --> |
| **CS portal-triggered simulation (Path B)** | The Connected Services portal can trigger a vehicle simulation via the subscriptions plane (`POST /simulate/start`), which gates the call on `connected-services` Cognito group membership, derives the transport from the vehicle's `dataSource` (never accepted as a parameter), and dispatches to the simulation Lambda with optional trip parameters (city, trips, route_length, safety_scenarios, maintenance_scenarios). See [`docs/cs-portal-data-model-surfaces.md`](docs/cs-portal-data-model-surfaces.md) § Simulation and the simulation topology note below. | HTTP POST `/simulate/start` (subscriptions plane) → `_require_operator` guard → dataSource resolution → simulation Lambda → IoT Core basic ingest → product rule → MSK `cs-product-*` → OEMTelemetryProcessor → MSK `cms-telemetry-preprocessed` → Flink processors → DynamoDB <!-- verify: grep -n "def handler\|def _SIMULATION_DISPATCH" services/connectors/subscriptions/simulate_vehicle/handler.py --> |

### Dynamic Data Collection with FleetWise Edge

In FWE mode, data collection is fully dynamic and campaign-driven — no code changes are needed to adjust what signals are collected, how often, or under what conditions:

**Signal Catalog & Decoder Manifest**: The system maintains a signal catalog of 262 VSS-aligned signals (engine, ADAS, body, cabin, chassis, EV/charging, environment, fleet management, GPS, and more). Each signal maps to a CAN message/signal definition in the DBC file (`services/simulation/can/cms-fleet.dbc`). The decoder manifest (`cms-fleet-v3`) tells the FWE agent how to decode raw CAN frames into named signals.

**Campaign-Driven Collection**: Campaigns define what to collect and when. A time-based campaign (e.g., `cms-fleet-telemetry-30s`) collects all signals every 30 seconds. Condition-based campaigns (e.g., `cms-safety-harsh-braking`) trigger collection only when specific signal thresholds are met (e.g., `signal(40) > 0.3`). Campaigns can target individual vehicles or the entire fleet.

**CampaignSyncProcessor**: A Flink application that listens for FWE agent checkins on the `fw-checkin` Kafka topic. On each checkin, it queries DynamoDB for active campaigns assigned to the vehicle, builds CollectionSchemes protobuf dynamically, and publishes the decoder manifest + collection schemes to the agent via IoT Core MQTT. This means campaigns can be created, modified, or suspended in real-time without restarting the agent.

**Connection Lifecycle**: The CampaignSyncProcessor also manages vehicle connection status in Redis. On checkin, it sets `connectionStatus: "connected"`. A periodic staleness check (configurable via `FWE_DISCONNECT_TIMEOUT_MS`, default 2 minutes) marks vehicles as `"disconnected"` if no checkin is received.

**Architecture (FWE mode)**:
1. Simulator generates telemetry → encodes as CAN frames via DBC → writes to `vcanN`
2. FWE agent reads CAN frames from `vcanN`, decodes using decoder manifest, filters per campaign rules
3. FWE agent uploads collected signals as protobuf to IoT Core → MSK `fw-telemetry-raw`
4. FWTelemetryProcessor decodes protobuf, maps signal IDs to names via signal catalog → MSK `cms-telemetry-preprocessed`
5. Standard Flink pipeline (trips, safety, maintenance, telemetry) processes the data → DynamoDB + Redis

**Multi-Vehicle Simulation**: Each vehicle gets its own isolated virtual CAN bus (`vcan0`, `vcan1`, etc.) with a dedicated FWE agent task and simulator task. The Lambda automatically allocates the next available vcan interface when starting a new vehicle simulation.

**Remote Commands** (in progress): FWE v1.3.2 supports native remote commands via the `commandsTopicPrefix` configuration. Commands are delivered as protobuf `CommandRequest` messages to `cms/commands/things/{VIN}/executions/{id}/request/protobuf`. Full CAN actuator dispatch via the Network Agnostic Data Collection (NADC) approach is planned.

![Architecture Diagram](/documentation/architecture-overview.png)

### Architecture Flow

1. **Vehicle Connectivity**: Vehicles connect securely to AWS IoT Core using X.509 certificates and MQTT protocol. In FleetWise Edge mode, the FWE agent handles connectivity, authentication, and campaign-driven signal collection.
2. **Data Ingestion**: Telemetry data flows through Amazon MSK (Kafka) for high-throughput processing. MQTT Direct telemetry lands on `cms-telemetry`; FleetWise protobuf telemetry lands on `fw-telemetry-raw` and is decoded by the FWTelemetryProcessor before joining the standard pipeline.
3. **Campaign Management**: In FWE mode, the CampaignSyncProcessor monitors agent checkins, resolves active campaigns from DynamoDB, and pushes decoder manifests and collection schemes to the edge agent via IoT Core MQTT.
4. **Real-time Processing**: Apache Flink on Amazon Kinesis Data Analytics processes streams to generate trips, safety events, and maintenance alerts
4. **Data Storage**: Processed data is stored in DynamoDB with automatic scaling and backup
5. **Real-time State Management**: Amazon ElastiCache for Redis implements the Last Known State (LKS) pattern — the Flink telemetry processor writes every signal value to Redis hashes on each message, providing sub-millisecond vehicle state lookups. Redis geospatial indexing (GEOADD/GEOSEARCH) powers the map view, and Redis streams provide capped time-series for sparkline charts. Vehicle state expires automatically when telemetry stops.
6. **Location Services**: Amazon Location Service provides maps, geocoding, and route calculation for vehicle tracking and trip planning
7. **Fleet Management**: Web application provides comprehensive fleet management, driver tracking, and analytics dashboards with real-time map visualization
8. **Simulation**: Integrated fleet simulator generates realistic telemetry for testing and development. Supports both MQTT Direct mode (JSON over MQTT) and FleetWise Edge mode (CAN signal generation → FWE agent → protobuf upload). In FWE mode, a persistent ECS task per vehicle runs the FWE agent alongside a `vehicle-ecu` sidecar that manages command subscriptions and vehicle presence state. Per-trip simulator tasks signal the vehicle-ecu and write CAN frames to isolated virtual CAN buses per vehicle, enabling multi-vehicle parallel simulation.
9. **CVX Chat Integration**: The web UI ChatAgent routes conversational fleet-operations requests to `/assistant/chat` on the CVX API, which invokes the AgentCore text runtime (`vsa_supervisor_text_staging`). The AgentCore runtime runs a Sonnet 4.6 supervisor agent that grounds responses via cross-account Retrieve against the Automotive Data Platform (ADP) Knowledge Base. This enables in-UI assistant surfaces for fleet-driver, service-advisor, and vehicle-owner personas without changes to the core CMS infrastructure stacks.

### Connected Services Portal — Data Model Surfaces

The Connected Services portal surfaces CMS's existing data-model backend as a read-only catalog for producers and external systems. **CS owns no data-model storage** — all data is read directly from CMS's deployed API.

**Six surfaces (four read-only, two read-write)**:
1. **Signal Catalog** — 302 signals in a VSS-style tree, grouped by `signal_group` (24 functional domains: adas, body, cabin_climate, chassis, powertrain, tpms, …). Sparse fields render distinctly from zero — `can_id` is present on 65 of 302 and `cycle_ms` on 204 <!-- verify: curl -s -H "Authorization: Bearer $TOKEN" https://enn83ljo91.execute-api.us-west-2.amazonaws.com/prod/signals | jq '.count' -->
2. **Vehicle Models** — 8 model manifests: 7 Meridian lines (`MERIDIAN-AZIMUTH`, `-CRESTWIND`, `-MISTRAL`, `-SIROCCO`, `-TRAILWIND`, `-WINDROSE`, `-ZEPHYR`) plus `CMS-FLEET-MODEL`, the CMS Universal baseline model. `vehicleCount` is deliberately **not** displayed — it is a stale stored count <!-- verify: curl -s -H "Authorization: Bearer $TOKEN" https://enn83ljo91.execute-api.us-west-2.amazonaws.com/prod/model-manifests | jq '.count' -->
3. **ECUs** — **9 distinct ECUs** (TCU, BMS, VCU, BCM, ADAS, IVI, GW, CCU, ECM), de-duplicated by `ecu` code from 65 raw entries across the 8 manifests. Sourced from `ecus[]` **on the model manifest, not by grouping signals** — `/signals` carries no ECU field. `signalCount` and `baselineVersion` are manifest-declared and present on only 8 of the 65 raw entries, so they render absent rather than zero <!-- verify: curl -s -H "Authorization: Bearer $TOKEN" https://enn83ljo91.execute-api.us-west-2.amazonaws.com/prod/model-manifests | jq '[.modelManifests[].ecus[].ecu] | unique | length' -->
4. **Decoder Manifests** — 2 manifests (`cms-fleet-v3`, `cms-fleet-v4`). The API returns six fields only — name, version, description, `modelName`, status, created-at. It carries **no CAN signal mapping and no firmware baseline**, so those tabs were removed rather than populated from fixtures <!-- verify: curl -s -H "Authorization: Bearer $TOKEN" https://enn83ljo91.execute-api.us-west-2.amazonaws.com/prod/decoder-manifests | jq '[.decoderManifests[] | keys] | flatten | unique' -->
5. **Data-Collection Campaigns** — list, grouped by campaign name (not by row type) with drill-in detail view per campaign. The list shows **11 campaigns** <!-- verify: aws dynamodb scan --table-name cms-staging-campaigns --region us-west-2 --output json --filter-expression "owner = :o" --expression-attribute-values '{":o":{"S":"oem"}}' | python3 -c "import json,sys; print(len({x['campaignName']['S'] for x in json.load(sys.stdin)['Items']}))" --> on staging (as of 2026-09-20), one row per campaign definition plus a rollup of its assignments. Only OEM-owned campaigns (`owner == "oem"`) are shown. Click a campaign name to open the detail view, which displays:
   - **Definition**: decoder manifest, collection scheme (`time_based` / `condition_based`), category, description, owner, created date
   - **Signals**: the campaign's `signalsToCollect` ids resolved against the signal catalog and grouped by `signal_group`, collapsed by default. Entry and distinct counts are both shown when they differ, because ids repeat — `cms-fleet-gps-10s` declares 293 entries resolving to 286 distinct signals <!-- verify: aws dynamodb get-item --table-name cms-staging-campaigns --region us-west-2 --key '{"campaignId":{"S":"cms-fleet-gps-10s"}}' --output json | python3 -c "import json,sys; s=json.load(sys.stdin)['Item']['signalsToCollect']['L']; print(len(s),'entries;',len({x['N'] for x in s}),'distinct')" -->
   - **Coverage**: for a UDS campaign, the ECUs polled by that campaign, read from `signalsToFetch` — real per-campaign attribution (`uds-dtc-polling` polls 9) <!-- verify: aws dynamodb get-item --table-name cms-staging-campaigns --region us-west-2 --key '{"campaignId":{"S":"uds-dtc-polling"}}' --output json | python3 -c "import json,sys; print(len(json.load(sys.stdin)['Item']['signalsToFetch']['L']))" -->. For a telemetry campaign, the ECU roster of vehicles running its decoder manifest, captioned to state explicitly that this is the **vehicle's** available ECUs and **not** an attribution of the campaign's signals to those ECUs — no signal→ECU mapping exists in any reachable surface
   - **Assigned Vehicles**: assignment rows with per-vehicle **Assign** and **Unassign** actions. Assignment sends the **VIN**, never a `vehicleId`, and refuses when no VIN is on record. Unassign is a destructive DELETE operation, labelled as such.

For the data contract, signals-to-collect resolution, ECU limitations, and assignment-row ownership model, see [`docs/cs-portal-data-model-surfaces.md`](docs/cs-portal-data-model-surfaces.md) § 5.
6. **Simulation** (NEW) — trigger telemetry simulation for any vehicle in the catalog. Transport is **derived** from the vehicle's `dataSource` and is never accepted as a parameter; a vehicle with no `dataSource` is rejected rather than defaulted <!-- verify: grep -n "def resolve_simulation_path" services/connectors/subscriptions/simulate_vehicle/handler.py -->

**Key Architectural Point**: CS reads these surfaces from CMS; it does not duplicate or reshape data. No new tables, no seeded JSON catalogs. This preserves a single source of truth shared across CMS, CS, and DMS.

**Entitlement**: Visibility is scoped by the `producer` attribute on each vehicle:
- CS inventory filters on `producer == meridian` 
- Simulation picker is unfiltered (a data product may legitimately cover another OEM's vehicles)
- Subscription eligibility depends on `sold_to` (DMS customer identifier on the vehicle) matching one of the caller's `custom:customerIds` claim values

For detailed architecture and API contracts, see [`docs/cs-portal-data-model-surfaces.md`](docs/cs-portal-data-model-surfaces.md).

### OEM1 Fleet Lifecycle Management

For fleets sourced from OEM1 cloud feeds, the platform provides bulk fleet management capabilities including enrollment, unenrollment, and real-time status synchronization. Key features:

- **Bulk Enrollment**: Fleet managers can enroll up to 500 vehicles at once with SKU (product) selection, driver assignment, and pre-flight capability validation. Enrollment requests are asynchronous and can take up to 7 days to complete per OEM1 provisioning timelines. The system enforces a **4 enroll requests/hour quota** per customer account.
- **Status Synchronization**: An automated status poller (runs every minute) drives enrollments to terminal states by polling OEM1's status API. Background status sync (every 15 minutes) maintains vehicle status freshness independent of manual requests.
- **Unenrollment**: Soft-remove (default) marks vehicles as inactive and removes fleet membership; hard-delete also removes the vehicle record entirely (trips/events preserved per compliance).
- **Admin Lambdas**: Seven serverless functions handle bulk operations, quota tracking, pre-flight checks, and background synchronization — see [`services/connectors/oem1/README.md`](./services/connectors/oem1/README.md) for architecture details and Consumer Action policy mappings.
- **Real-time UI**: Enrollment progress dashboard, per-vehicle status column with readiness indicators, and manual refresh capability with 60-second rate-limiting.

For detailed operational guidance and troubleshooting, see `docs/runbooks/oem1-fleet-lifecycle.md`.

### Vehicle Diagnostic Triage

The vehicle-detail diagnostic triage surface unifies DTC catalog verdicts, a full UDS/SOVD
scan, ECU identity/version-drift comparison, and a Stage 3 safety-classified routines
disclosure. After the 2026-09-25 redesign and its first browser UAT, the tab reads top to
bottom as: a health strip with the vehicle's status and the single **Run diagnostic scan**
button, then the self-test routines, then the diagnostic sessions table, then DTC history,
then technician detail. Opening a session shows its detail in a **modal** over the page. The
poll lives on the panel, so a scan keeps running while the modal is closed. Four
presentational components carry the layout: `DiagnosticsHealthStrip`,
`DiagnosticSessionsTable`, `SessionDetailView`, and `RoutineOffering`, which owns the
INERT / STATIONARY / SERVICE_ONLY gate.
<!-- verify: ls modules/cms_ui/source/frontend/src/components/vehicles/vehicle-detail/diagnostics/*.tsx | grep -v __tests__ | wc -l   # expect 4 components -->

A fleet operator reads the vehicle's diagnostic state from the health strip and sessions
table, opens a session to see its detail, or dispatches the vehicle to a dealer service
center with the **Dispatch to service** button on the health strip. See [`docs/SOVD_ON_MQTT_DESIGN_PATTERN.md`](docs/SOVD_ON_MQTT_DESIGN_PATTERN.md)
for the transport-binding design pattern (SOVD data model over an MQTT binding on AWS
IoT Core, with a REST-to-MQTT gateway for ecosystem clients) and its
`Mapping to the reference implementation` appendix for the code correspondence.

- **SOVD command set**: the commands Lambda dispatches **six** SOVD command types
  onto the vehicle over the MQTT SOVD topic (`cms/commands/things/{vin}/executions/{execId}/sovd/request`):
  `read_dtcs`, `clear_dtcs`, `read_identity`, `read_freeze_frames`, `read_data`,
  `run_routine`. Note the reference-impl vocabulary (`read_dtcs` / `clear_dtcs` /
  `read_freeze_frames`) departs from the design pattern paper's standard-aligned
  names (`read_faults` / `clear_faults` / `read_freeze_frame`); the appendix on the
  paper documents the mapping.
  <!-- verify: python3 -c "import re,sys; src=open('services/commands/commands_lambda.py').read(); m=re.search(r\"if command_type in \\(([^)]+)\\)\", src); print(len([t.strip().strip(\"'\\\"\") for t in m.group(1).split(',') if t.strip()]))"   # expect 6 -->
- **DTC catalog verdicts**: every DTC code renders against a live event-catalog lookup
  (`GET /api/v1/event-catalog`) — an uncatalogued code shows no fabricated severity,
  never a default.
- **Powertrain-correct ECU scans**: the simulator resolves a per-vehicle powertrain
  profile (gasoline / diesel / hybrid / electric) before querying ECUs, so a
  battery-electric vehicle is never asked for engine or EVAP-system DTCs. <!-- verify: grep -c "^_PROFILES" deployment/scripts/powertrain_profiles.py -->
- **Diagnostic routines disclosure**: the routine catalog exposes **18 distinct
  routines** across the four powertrain profiles (ICE_GASOLINE, ICE_DIESEL, EV,
  HYBRID); a caller only ever sees the subset applicable to the vehicle's fuel
  type, resolved server-side.
  <!-- verify: python3 -c "import sys; sys.path.insert(0,'services'); from _shared.routine_catalog import _CATALOG; print(len({r['routine_id'] for e in _CATALOG.values() for r in e}))"   # expect 18 -->
  Routines are grouped by a server-derived **safety class**. The catalog defines
  **three classes**: `INERT` (read-only self-tests, remotely invocable),
  `STATIONARY` (vehicle-state preconditions the platform can verify — speed = 0,
  ignition on, park/neutral — remotely invocable from an authorised bay-side
  operator), and `SERVICE_ONLY` (preconditions the platform cannot observe —
  vehicle-on-lift, technician-on-site — refused remotely, listed with a reason
  string per D17). The fleet-operator-facing CMS surface stops at triage
  (observing the vehicle state); a technician at a dealership's service center
  performs actual repairs using the dealer-side DMS portal, which renders the
  same diagnostic panel and routine results as a continuation of the CMS
  session. `SERVICE_ONLY` routines render **no invocation control at all** (not
  a disabled button); refusal reasons render verbatim, with no client-side
  summarization or fabricated placeholder text. For the cross-platform
  diagnostic session details, see
  [Cross-platform diagnostic sessions (v1.5)](#cross-platform-diagnostic-sessions-v15) below.
  <!-- verify: grep -E "^(INERT|STATIONARY|SERVICE_ONLY) = " services/_shared/routine_catalog.py | wc -l   # expect 3 -->

**Demo fleet composition is two makes by design, not an unfinished conversion.** As of
this platform's Stage 2 backfill the staging fleet is `Meridian` (21 vehicles — the
platform's fully-modeled make, with ECU inventories, model manifests, and DID
profiles) and `Ford` (48 vehicles — retained as the OEM1 cloud-integration
reference demo, telemetry-only, deliberately without a model manifest). <!-- verify: aws dynamodb scan --table-name cms-staging-storage-vehicles --projection-expression make --query 'Items[].make.S' | sort | uniq -c -->
Do not "fix" the 48 Ford rows by assigning them a model manifest — doing so would
assert a diagnostic depth the OEM1 cloud telemetry path cannot deliver, and destroys
the reference demonstration that a second, telemetry-only OEM integration exists
alongside the fully-modeled on-board fleet.

### Cross-platform diagnostic sessions (v1.5)

Fleet-operator diagnostic triage can be handed off to a dealer's service technician
without breaking the diagnostic-session thread. A CMS fleet-operator reading the vehicle
state from the Diagnostic Triage panel clicks **Dispatch to service**,
picks a dealer, and the same `sessionId` propagates to DMS: a repair order is
opened in `Draft`, evidence carries `sessionId + routinesRun[]`, and DMS's
service view auto-opens on the Diagnostics tab when both `initiated_by` names
CMS origin (`cms_booking` or `cms:` prefix) and `evidence.sessionId` exists.
The technician's subsequent invokes append to the same session log; the
fleet-operator sees the technician's work in-line on the CMS side. Full
staging playbook: `docs/DEPLOYMENT.md` § *v1.5 SOVD Diagnostic Sessions*.

Two seams stay deterministic:

- **`POST /api/dispatch`** (CMS) — resolves VIN → vehicleId via the existing
  centralized resolver (`main_api/index.py:1365`), re-derives VIN server-side
  before the outbound DMS call to close a client-supplied-VIN spoof, and reuses
  the module-level `_RefuseRedirects` opener so a redirect can never carry the
  operator token to an attacker-controlled host. Fleet-scope authorized before
  forwarding as defence-in-depth; DMS's `authorize_fleet_scope` is the
  authoritative control. <!-- verify: grep -c "'/api/dispatch' and method" modules/cms_ui/source/handlers/main_api/index.py -->
- **SERVICE_ONLY invoke gating** — a `dms-technician` may invoke `SERVICE_ONLY`
  routines **only** when an active repair order exists for the VIN at one of
  the technician's `custom:dealerIds`, with RO status in `{InProgress,
  AwaitingParts}` (the `_RO_PRESENCE_STATUSES` set — narrower than the general
  active set, because physical presence is the whole justification for the
  lift). `platform-admin` composite-holders are explicitly excluded: presence
  cannot be assumed for a bypass role. `fleet-viewer` is excluded regardless.
  <!-- verify: grep -c "^_RO_PRESENCE_STATUSES = " services/commands/commands_lambda.py -->

The `dms-technician` Cognito group is provisioned by the CMS `ui_stack.py`;
the DMS handler layer recognises it via `source/handlers/auth.py`. Deploying
the group is a v1.5 prerequisite operator step — see
`docs/DEPLOYMENT.md` § *v1.5 SOVD Diagnostic Sessions* for the exact commands.

### Routine Result Contracts & Verdict-Driven Diagnostics

The v1 routine-result pilot ships **6 pilot routines** with deterministic sim producers, schema-defined result shapes, and custom UI renderers, out of the **18** routines the catalog defines across four powertrain profiles. Every SUCCEEDED diagnostic response now carries a `verdict` field (`in_spec` / `marginal` / `out_of_spec`) that drives UI severity colouring — replacing generic "check completed" with actionable fault signals.

**Schemas & Sim**: `services/_shared/routine_result_schemas.py` <!-- verify: python3 -c "import sys; sys.path.insert(0, 'services'); from _shared.routine_result_schemas import ROUTINE_RESULT_SCHEMAS; print(len(ROUTINE_RESULT_SCHEMAS))" --> defines the 6 pilot schemas. `services/simulation/routine_sims.py` produces deterministic per-(vehicle, routine) results via `hashlib.sha256` seeding. Demo vehicles (`VEH-MRDN-0001`, `VEH-MRDN-0015`) carry hardcoded storytelling overrides for specific routines to exercise all three verdict bands.

**UI Renderers** (<!-- verify: find modules/cms_ui/source/frontend/src/components/vehicles/vehicle-detail/routine-renderers -name '*Renderer.tsx' | wc -l --> 6 pilot + 1 fallback):
- `LampSelfCheckRenderer` — 4×2 grid with per-lamp icons + ambient lux
- `O2HeaterCheckRenderer` — 2-column kv grid (bank 1 response time vs. threshold)
- `EvapLeakTestRenderer` — pressure + leak rate kv grid
- `AbsPumpCycleRenderer` — observed vs. expected cycle count
- `PackIsolationRenderer` — resistance vs. threshold with degradation note
- `CellBalanceCheckRenderer` — per-cell voltage bar chart + max delta
- `RawDrawer` (fallback) — expandable raw-JSON for **12 non-pilot routines**
  the catalog defines but the pilot has not yet typed
  <!-- verify: python3 -c "import sys; sys.path.insert(0,'services'); from _shared.routine_catalog import _CATALOG; from _shared.routine_result_schemas import ROUTINE_RESULT_SCHEMAS as S; catalog={r['routine_id'] for e in _CATALOG.values() for r in e}; print(len(catalog - set(S)))"   # expect 12 -->

Every renderer reads `response.verdict` (no client-side recomputation per spec R5). Session log displays 🟢 / 🟡 / 🔴 verdict banners with routine-specific copy ("all lamps OK" / "N lamps marginal" / "N lamps failed").

**Cross-repo parity**: DMS's Diagnostics tab (v1.5 D9) renders the same payloads using byte-identical copies of the 6 renderers. `scripts/verify_renderer_parity.py` drift-guards both repos via CI.

See `docs/tech.md` § SOVD Routine Result Contracts for verified schema module, sim producer, and wire-shape details. See [`docs/SOVD_ON_MQTT_DESIGN_PATTERN.md`](docs/SOVD_ON_MQTT_DESIGN_PATTERN.md) for the transport-binding design pattern that carries these payloads.

### SOVD staging smoke

The end-to-end staging smoke lives at [`docs/SOVD-SMOKE-PLAYBOOK.md`](docs/SOVD-SMOKE-PLAYBOOK.md). It exercises the platform's SOVD surface from a single `fleet-operator` persona — the only persona that can invoke INERT routines from CMS — and, on the DMS side, the automated observation of the repair-order row that a `Dispatch to service` click creates. The latest run record is at [`docs/SOVD-SMOKE-RUN-2026-09-25.md`](docs/SOVD-SMOKE-RUN-2026-09-25.md), recorded after the Diagnostics tab redesign and the first end-to-end dispatch to DMS.

The playbook has 7 steps (Step 0 conditional prereq + Steps 1–6). The 2026-09-24 run ([`docs/SOVD-SMOKE-RUN-2026-09-24.md`](docs/SOVD-SMOKE-RUN-2026-09-24.md)) passed 6 of 7 steps, with Step 6 (Dispatch to DMS from the UI) blocked on a routing defect. The 2026-09-25 run ([`docs/SOVD-SMOKE-RUN-2026-09-25.md`](docs/SOVD-SMOKE-RUN-2026-09-25.md)), which supersedes it for the Diagnostics tab and Step 6, passed, and the dispatch reached DMS.
<!-- verify: grep -F "6 of 7 steps PASS. Step 6 BLOCKED" docs/SOVD-SMOKE-RUN-2026-09-24.md; grep -F "**PASS.**" docs/SOVD-SMOKE-RUN-2026-09-25.md -->

### Fleet-Operator Remote Commands

The Vehicle Detail **Remote Commands** tab offers a fleet operator four commands
across five palette buttons — `lock_all_doors` appears twice, as Lock and Unlock:
<!-- verify: grep -o "cmd: '[a-z_]*'" modules/cms_ui/source/frontend/src/components/vehicles/vehicle-detail/RemoteCommandsPanel.tsx | tee /dev/stderr | sort -u | wc -l   # buttons = total lines, commands = unique lines -->

- **Lock / Unlock All Doors** — asset security and driver lockout recovery
- **Remote Start** — engine start for duty-cycle prep
- **Find My Vehicle** — locate the vehicle
- **Pre-Condition Cabin** — climate prep before the driver arrives

The palette is scoped to the **fleet-operator and platform-admin** personas. Any
other persona (fleet-viewer, dispatcher, fleet-guest) sees an informational card
in place of the tab's contents and no command affordances.

Driver-idiom commands — panic mode, honk horn, flash hazards — are **not offered
in this palette**. They belong to the driver's own phone (the MeridianMotorsCompanion
iOS app), not to a fleet operator's console: an operator remotely activating panic
mode on a driver's van reads as alarming rather than as a feature.

Two scope notes, so the sentence above is not over-read:

- This is a **UI-affordance change, not an authorization change.** The commands
  Lambda (`services/commands/commands_lambda.py`) is unmodified and its endpoint
  remains reachable; server-side authorization is the real control. The persona
  gate governs what is rendered, not what is permitted.
- The **All Commands** section of the same tab renders whatever actuators the
  deployed decoder-manifest catalog serves, so it is the catalog — not this
  palette — that determines the full set of commands reachable from CMS.

For the full rationale see spec `2026-09-14-cms-frontend-fleet-persona-alignment`.

### Buy Tab: Discover & Configure Vehicle Journey

The platform provides an end-to-end retail ordering experience in the MeridianMotorsCompanion iOS app, accessed via a dedicated **Buy tab**. The journey comprises three phases:

**Phase 1 — Discover (Voice-First Catalog Exploration)**
- Anonymous or signed-in entry point launches DiscoverFlow with voice-first interface powered by Nova 2 Sonic
- Agent grounds responses in `vehicle_shopping_guide` KB source category (10 docs covering segment education, powertrain tradeoffs, financing primers, etc.)
- Agent tools: `catalog_query` (natural-language vehicle search), `catalog_compare` (side-by-side model comparison), `offers_lookup` (current promotional offers with required disclosure text), `lead_capture` (email + phone + consent → `vsa-acquire-leads` table)
- Cards render dynamically as agent narration surfaces catalog results; convergence CTA launches phase 2 with optional lead capture

**Phase 2 — Configure & Order (9-Step Configurator)**
- Linear wizard driven by `ConfiguratorFlow`: category → model → variant → color → accessories → review → finance → confirm → success
- Review step streams persona-tuned narration from AgentCore against selected configuration
- Finance step surfaces mock qualification tier + required disclosure text verbatim
- Reservation writes to `vsa-acquire-orders` table with optional `discoverLeadId` back-reference; receipt displays order ID
- Optional `variant_compare` tool lets the agent help compare variants of a selected model

**Phase 3 — Track Manufacturing & Delivery (10-Stage Pipeline)**
- OrderTrackerView displays order progression through: Order Placed → Payment Received → Assigned to Plant → Sub-Assembly → Paint → Final Assembly → Quality Control → Shipped → At Dealer → Ready for Delivery
- Per-stage narration via `manufacturing_explain` tool grounds stage-specific commentary in `vehicle_manufacturing` KB (12 docs on manufacturing process)
- 30-second polling reads current stage from `vsa-acquire-orders`; graceful degradation if Order API is unavailable
- Time-simulated backend advances orders through stages based per-tenant delivery cadence configuration

**Catalog & Configuration Schema**
- `vsa-acquire-catalog` DDB table (shared with Acquire phase): category, model, variant, color, accessory, offer, and optional IVE-package items
- `AcquireConfig` tenant extension: currency, symbols, plant network, delivery cadence, finance-partner names, Discover greeting/primed prompts (configurable per-tenant), guardrails ID
- Multi-tenant skinnable by design: all OEM-specific values flow from runtime config; no brand names in code

**Entry Paths**
- **Path A (Cold)**: Buy tab tap → DiscoverFlow opens anonymous (no LTM) or signed-in (LTM if available)
- **Path B (Warm, Demo-Only)**: PresenterControls mock trigger → DiscoverFlow pre-hydrated with returning-owner persona snapshot + path-B greeting swap
- Both paths converge at Order handoff via the same `ConfiguratorFlow`

For detailed deployment and schema documentation, see `docs/DEPLOYMENT.md § Discover Phase` and `docs/{acquire-catalog-schema.md, acquire-leads-schema.md}`.

**iOS client**: the Meridian Motors iOS app (Buy tab, Alerts tab, Controls, voice assistant) is in [`clients/ios/`](clients/ios/). See [`clients/ios/README.md`](clients/ios/README.md) for configuration against your deployed stacks and for building and running it on the iOS Simulator.

### Cost

_You are responsible for the cost of the AWS services used while running this Guidance. The sample table below covers the full **v0.2.x deployment** — OEM cloud connectors, multi-vehicle simulation, always-on Fargate services, and all 9 Managed Apache Flink applications are included in the totals rather than listed as separate variables. Re-verify against current AWS pricing pages before committing to a deployment, especially for Fargate task-hours (continuous on staging).<!-- verify: grep -n "Bedrock" README.md — after 2026-09-06 VFO teardown, CMS deploys no Bedrock Agents; sibling CVX still uses Bedrock but is a separate accelerator (see ~/.kiro/steering/active-projects.md CVX entry). -->_

### Fleet Intelligence — Cost & Lifecycle Analysis

Fleet Intelligence surfaces cost-per-mile (CPM) and lifecycle analysis for vehicle fleets by reading curated cost data from the Automotive Data Platform (ADP) via cross-account Athena queries. Accessible at `/fleet-intelligence/*` routes on the CMS UI.

**Key surfaces:**
- **Cost per mile** — aggregated by fleet, OEM, or vehicle; supports outlier detection (vehicles > 1.5× fleet average). Powered by ADP `service_records`, `charging_sessions`, and `energy_usage` curated products.
- **Lifecycle view** — sell-timing analysis via month-by-month maintenance-cost vs. depreciation breakeven. Available in two scopes: **CMS fleets** (multi-fleet operator view) or **All ADP vehicles** (admin-only rollup, precomputed hourly to avoid API Gateway timeout). The ADP scope uses a 60,000 USD straight-line depreciation assumption, with the assumption disclosed in every response.
- **Preventive Maintenance** — compliance status and schedule management, with mileage- and engine-hours-based schedules.

**Admin-only surface:** `/fleet-intelligence/lifecycle?scope=adp` returns a precomputed rollup over all ADP vehicles (~4.7M VINs in staging), available only to `platform-admin` users. Non-admins receive HTTP 403. The UI toggles between "CMS fleets" and "All ADP vehicles" for admins; the fleet picker is locked while the ADP scope is displayed.

For architecture, Athena SQL contracts, environment variables, and testing patterns, see [`services/fleet_intelligence/README.md`](./services/fleet_intelligence/README.md).

### Cost (Pricing Model)

_We recommend creating a [Budget](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html) through [AWS Cost Explorer](https://aws.amazon.com/aws-cost-management/aws-cost-explorer/) to help manage costs. Prices are subject to change. For full details, refer to the pricing webpage for each AWS service used in this Guidance._

### Sample Cost Table

_This cost table reflects the_ ***v0.2.x full-stack deployment*** _including OEM cloud connectors, multi-vehicle simulation, and all 9 Flink stream processing applications. Costs are shown at us-east-1 (N. Virginia) pricing as of September 2026. Regional pricing varies — multiply by ~1.25× for sa-east-1 (São Paulo). Re-verify against current_ [_AWS pricing pages_](https://aws.amazon.com/pricing/) _before committing. **Amazon Bedrock is no longer a CMS cost line** — the VFO Bedrock Agents stack was retired 2026-09-06 (spec `2026-09-05-cms-vfo-teardown`); the fleet-view surface is now served deterministically by `services/fleet_intelligence/`._

| AWS Service | Dimensions (v0.2.x) | Baseline (1K veh) | At Scale (5K veh) |
| ----------- | ------------------- | :---------------: | :---------------: |
| Amazon MSK | 3× kafka.m5.large, 100 GB storage ea | $490 | $490 |
| Managed Apache Flink | 9 apps, 20 KPU total (11 running + 9 orchestration; trip-processor at parallelism 3, all others parallelism 1) running 24/7 + storage <!-- verify: grep -n 'KPU total' README.md --> | $1,706 | $1,706 |
| Amazon DynamoDB | On-demand (scales with fleet) | $150 | $750 |
| ElastiCache for Redis | cache.t3.medium (→ r6g.large at scale) | $50 | $150 |
| AWS IoT Core | MQTT messages + connectivity + rules | $120 | $550 |
| Amazon Location Service | Map tiles + geocoding | $15 | $35 |
| Amazon API Gateway | REST API calls | $4 | $10 |
| Amazon Cognito | Fleet operator users | $0 | $0 |
| Amazon CloudFront + S3 | UI hosting, data transfer | $15 | $15 |
| AWS Lambda | Serverless compute | $25 | $40 |
| Amazon VPC | NAT Gateway + data transfer | $55 | $100 |
| Amazon ECS Fargate | OEM1 connector + WS fanout (2 always-on tasks) + ephemeral simulation workers via `ecs:RunTask` | $130 | $170 |
| **Total** | | **~$2,760** | **~$4,016** |<!-- verify: sum the rows above; row values changed 2026-09-06 when Amazon Bedrock line ($50/$150) was removed after the VFO teardown (spec 2026-09-05-cms-vfo-teardown). -->

### Cost Scaling Guidance

- **Fixed costs (~$2,200/mo)**: MSK brokers, Flink KPUs, and always-on Fargate tasks run continuously regardless of fleet size. These represent ~75% of cost at small scale.
- **Variable costs (scale linearly with fleet)**: IoT Core (~$1 per million messages at us-east-1), DynamoDB (on-demand WRU/RRU), and data transfer. (Bedrock invocation costs — formerly listed here — are zero after the 2026-09-06 VFO teardown removed the CMS-side Bedrock Agents stack.)
- **Scaling thresholds**:
  - MSK: additional brokers needed at ~5K concurrent vehicles.
  - Redis: upgrade from `cache.t3.medium` to `cache.r6g.large`.
  - Flink: may need 25+ KPU for >6K vehicles.
- **Per-vehicle cost**:
  - 1K vehicles ≈ **$2.81/veh/mo**
  - 5K vehicles ≈ **$0.83/veh/mo**
  - 10K vehicles ≈ **$0.65/veh/mo** (economies of scale on fixed infra)
- **Regional multiplier**: `sa-east-1` (São Paulo) is ~25% more expensive than `us-east-1` for most services. Multiply the baseline by 1.25× for LatAm deployments.

<!--
COST TABLE MAINTENANCE — READ BEFORE EDITING

Two figures in this section are derived from `deployment/stacks/flink_stack.py`
and drift silently as Flink apps are added/removed or `parallelism=` overrides
change. Re-verify both from source before editing the table or the disclaimer:

  1. Flink app count (currently 9). Verify with:
       grep -c 'kinesisanalytics\.CfnApplication(' deployment/stacks/flink_stack.py

  2. KPU sum (currently 11 KPU total) <!-- verify: grep -n 'parallelism=' deployment/stacks/flink_stack.py -->. Compute as:
       (sum of `parallelism` values across all apps) + (1 orchestration KPU per app)
     Non-default `parallelism=` values:
       - trip-processor: parallelism=3 (spec `2026-06-17-oem1-event-driven-pipeline-scale`)
       - all other 8 apps: parallelism=1 (default in `create_flink_app_config`)
     Grep every explicit override with:
       grep -n 'parallelism=' deployment/stacks/flink_stack.py

If either number changes, update BOTH the Flink Dimensions cell AND the
"all 9 Flink stream processing applications" phrase in the two prose blocks
above in the same edit. Prior drift context: issues/2026-09-01-readme-cost-table-v0.2.x/.
-->


## Prerequisites

### Operating System

These deployment instructions are optimized to best work on **Amazon Linux 2023 AMI** or recent macOS. Deployment on another OS may require additional steps.

**Required packages:**
- Node.js 18.x or later (Node.js 20+ recommended for Vite 5)
- Python 3.9 or later
- Java 11 (for the Flink stream processor JAR build) — `OpenJDK 11` or Amazon Corretto 11
- Maven 3.6+ (for Flink build)
- AWS CLI v2
- AWS CDK v2.100.0 or later
- Yarn 4 — managed automatically by Corepack (see step below)
- Docker (optional; required only for custom simulation image builds via `SIM_IMAGE_MODE=asset` or `make publish-public-ecr`)

**Installation commands for Amazon Linux 2023:**
```bash
# Install Node.js
sudo dnf install -y nodejs npm

# Install Python and pip
sudo dnf install -y python3 python3-pip

# Install Java 11 (for Flink build)
sudo dnf install -y java-11-amazon-corretto-devel maven

# Install AWS CLI v2
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install

# Install AWS CDK
npm install -g aws-cdk

# Enable Corepack (manages the project's pinned Yarn 4)
corepack enable
corepack prepare yarn@4.6.0 --activate
```

For macOS:
```bash
brew install node python@3.11 openjdk@11 maven awscli
npm install -g aws-cdk
corepack enable && corepack prepare yarn@4.6.0 --activate
```

### Third-party tools
- **Git** - For cloning the repository
- **Make** - For running deployment scripts (optional)

### AWS account requirements
- **AWS Account** with appropriate permissions for creating IAM roles, VPCs, and AWS services
- **AWS CLI configured** with credentials that have administrative permissions
- **Sufficient service quotas** for the services used (see Service limits section)

### AWS CDK

This Guidance uses AWS CDK. If you are using AWS CDK for the first time, please perform the following bootstrapping:

```bash
cdk bootstrap aws://ACCOUNT-NUMBER/REGION
```

Replace `ACCOUNT-NUMBER` with your AWS account ID and `REGION` with your preferred deployment region.

### Service limits

This Guidance may require increases to the following service limits:
- **Amazon MSK**: Default limit of 3 clusters per region
- **Amazon Kinesis Data Analytics**: Default limit of 8 applications per region — request increase to ≥9 via the Service Quotas console before deploy (this Guidance ships 9 Flink applications) <!-- verify: grep -nE 'Flink applications' README.md -->
- **AWS IoT Core**: Default limit of 500,000 things per region

To request limit increases, visit the [AWS Service Quotas console](https://console.aws.amazon.com/servicequotas/).

### Supported Regions

This Guidance supports deployment in the following AWS Regions:
- US East (N. Virginia) - us-east-1
- US West (Oregon) - us-west-2
- Europe (Ireland) - eu-west-1
- Asia Pacific (Tokyo) - ap-northeast-1

Cross-region namespace discipline (region-suffix on all globally-scoped S3 bucket names, CloudFront aliases, and IAM role names) has been validated across all 34 commercial AWS regions. Tokyo (`ap-northeast-1`) serves as the reference validated secondary region — it has been exercised end-to-end with the clean-deploy test harness. Other commercial regions follow the same naming patterns and are deployable with standard CDK bootstrapping. See [`docs/DEPLOYMENT.md`](./docs/DEPLOYMENT.md) for the cross-region deployment runbook.

## Deployment Steps

> **Modern deployment:** Use `make -C deployment staging-deploy` or `make -C deployment prod-deploy` for environment-aware deployments with built-in safety checks. The legacy phased deploy targets (`phase1`, `phase2`, etc.) remain available for advanced use cases and direct control.

### Build Prerequisites (Read First)

Before any deploy command, two build artifacts must exist:

1. **Flink JAR** — universal stream processor. Build with:
   ```bash
   cd modules/flink
   JAVA_HOME=/path/to/jdk-11 ./build.sh
   ```
   Produces `modules/flink/target/cms-telemetry-processor-1.0.0.jar` (~35 MB).
   Requires Java 11 + Maven.

2. **UI build** — React + Vite frontend. The Makefile target `build-ui` handles this:
   ```bash
   cd deployment
   make build-ui DEPLOYMENT_STAGE=staging
   ```
   Produces `modules/cms_ui/source/frontend/build/`. Requires Node.js 18+ and Yarn 4.

   **Yarn 4 setup** (Corepack, no manual install): the project pins Yarn 4.6.0
   via `.yarnrc.yml`. Enable Corepack once on your machine:
   ```bash
   corepack enable
   corepack prepare yarn@4.6.0 --activate
   ```

3. **Optional environment variable** — `CMS_DEMO_DEFAULT_PASSWORD`:
   ```bash
   export CMS_DEMO_DEFAULT_PASSWORD='<a-strong-password>'
   ```
   Deploys don't need it: the stack generates the demo password in AWS Secrets Manager
   (`cms-<stage>-demo-user-password`). Set it only to enable the one-click demo-login buttons on a
   staging deploy.

The phased deploy targets (`make phase4`) and the `make deploy-all` target both call into Flink build / UI build automatically. The build prerequisites above are most important when running individual `cdk deploy <stack>` commands directly.

## Security defaults

The CMS template ships with secure-by-default Cognito + API Gateway
configuration:

- **Cognito self-signup**: disabled. Users are admin-created via
  `seed_driver_users.py` or the AdminCreateUser API. Demo deployments
  that need open self-signup opt in via
  `cdk synth --context cms.allow_self_signup=true`.
- **Identity Pool guest credentials**: disabled. Anonymous callers do
  not receive AWS IAM credentials. Demo deployments needing anonymous
  map UI opt in via `cdk synth --context cms.allow_unauth_map_auth=true`.
- **API Gateway authorization**: every `/api/*` route across all
  stacks (UI, simulation, commands, predictive-agent, data-processing)
  requires a valid Cognito User Pool JWT. CORS preflight (OPTIONS)
  remains unauthenticated per the browser CORS protocol.
- **WebSocket API authorization**: the WebSocket API `$connect` requires a
  Cognito JWT passed as `?token=<jwt>` on the upgrade URL (validated by a
  Lambda authorizer against the User Pool JWKS). Anonymous upgrades get
  HTTP 401. Demo deployments may opt in to anonymous WebSocket via
  `cdk synth --context cms.allow_unauth_websocket=true`.

See `docs/DEPLOYMENT.md` § 'Security context flags' for details on
opting in and verifying enforcement.

### Account provisioning

CMS includes integrated account provisioning for Amazon internal users and optional external self-service registration. The internal provisioning (Federate auto-provisioning, Phase A) is opt-in and ships with zero-touch group assignment for authorized Amazon employees. External self-signup (Phase B) is gated on security preconditions.

For setup, enablement, and prerequisites:
- **Phase A (internal Federate auto-provisioning)**: See `docs/DEPLOYMENT.md` § "Account provisioning" § "Phase A: Federate auto-provisioning"
- **Phase B (external self-signup, not yet available)**: See `docs/DEPLOYMENT.md` § "Account provisioning" § "Phase B: External self-signup"
- **Custom attributes and audit**: See `docs/DEPLOYMENT.md` § "Account provisioning" § "Audit: custom:provisionedVia attribute"

### Option 1: Interactive Deployment with Makefile (Recommended)

The interactive deployment guides you through profile selection, environment configuration, and phased deployment.

1. Clone the repository:
   ```bash
   git clone https://github.com/aws-solutions-library-samples/guidance-for-connected-mobility-on-aws.git
   cd guidance-for-connected-mobility-on-aws/deployment
   ```

2. Install dependencies (creates a Python venv + installs CDK + dependencies):
   ```bash
   make install
   ```

3. Optional: set `CMS_DEMO_DEFAULT_PASSWORD` to enable the staging demo-login buttons (see Build
   Prerequisites):
   ```bash
   export CMS_DEMO_DEFAULT_PASSWORD='<a-strong-password>'
   ```

4. Bootstrap CDK (one time per AWS account / region):
   ```bash
   make bootstrap AWS_REGION=us-west-2  # or your target region
   ```

5. Start interactive deployment:
   ```bash
   make deploy
   ```

6. Follow the prompts to:
   - Select your AWS profile
   - Choose deployment stage (`staging` or `prod`)
   - Select deployment phase or deploy all phases

Note: We recommend deploying one phase at a time to ensure no issues in the deployment.

7. When the deployment is complete, all necessary data will be available on the screen, URL, username/password.

![Architecture Diagram](/documentation/deployment_options1.png)

### Option 2: Automated Deployment with Makefile

The Makefile automates environment setup, dependency installation, and phased deployment.

1. Clone the repository:
   ```bash
   git clone https://github.com/aws-solutions-library-samples/guidance-for-connected-mobility-on-aws.git
   cd guidance-for-connected-mobility-on-aws/deployment
   ```

2. Install dependencies + bootstrap CDK:
   ```bash
   make install
   make bootstrap AWS_REGION=us-west-2  # one-time per account/region
   ```

3. View available deployment options:
   ```bash
   make help
   ```

4. Deploy everything (recommended single-command path):
   ```bash
   make deploy-all DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2
   ```

   `deploy-all` runs the following phase groups in order:
   - **phase-foundation** — data-processing, storage, iot, ui, msk, telemetry-integration
   - **phase-streaming** — flink + fleetwise (order matters)
   - **phase-seeds** — signal/event catalog + fleet enrollment + fleetwise decoder
   - **phase-services** — simulation, commands, ws-fanout
   - Plus eval-user setup + runtime-config regeneration + demo-persona seeding

   Or deploy individual phases:
   ```bash
   make data-processing  # Signal catalog + transform manifests
   make phase1           # IoT, Storage, UI, Lambda, Cognito, S3
   make phase3           # VPC + MSK + Redis (deploys before phase1 in phase-foundation)
   make phase3b          # Telemetry integration (IoT → MSK rule + VPC destination)
   make phase4           # Flink stream processing (builds Flink JAR + deploys flink stack)
   make phase5           # Flink JAR rotation only (property maps now sourced from CDK)
   make deploy-flink-fast # ⚡ Fast Flink redeploy (~5 min vs ~45 min, skips snapshot restore)
   make deploy-fleetwise # FleetWise integration (FWE rules + VPC endpoints)
   make deploy-simulation # Simulation service (Docker)
   make deploy-commands  # Remote commands (Lambda + API GW + IoT Rule)
   make deploy-ws-fanout # WebSocket fanout (Docker, Kafka → WS bridge)
   ```

5. **Seed demo data with one command:**
   ```bash
   make bootstrap-demo AWS_PROFILE=default AWS_REGION=us-west-2 DEPLOYMENT_STAGE=staging
   ```
   This runs preflight checks, seeds all catalogs, generates 2 years of
   realistic fleet telemetry, and verifies the result. ETA 60-90 min with
   real Location Services routes, 5-10 min with synthetic routes
   (`USE_LOCATION_SERVICES=false`). See
   [docs/DEPLOYMENT.md § Demo fleet seeding](./docs/DEPLOYMENT.md#demo-fleet-seeding-generic-vs-customer-tenant) for details
   and tuning options.

7. Capture the CloudFront distribution URL:
   ```bash
   aws cloudformation describe-stacks --stack-name cms-staging-ui --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontURL`].OutputValue' --output text --region us-west-2
   ```

### Option 3: Clean-deploy validation (advanced, secondary region)

To validate the entire pipeline end-to-end on a fresh region (e.g. ap-northeast-1 / Tokyo) — useful for confirming a release works on a clean account or for cross-region disaster-recovery rehearsal — use the clean-deploy harness:

```bash
cd deployment
make clean-deploy-test REGION=ap-northeast-1
```

The harness orchestrates: preflight → bootstrap → all phases → e2e tests → audit, and writes per-run logs to `~/.cms/clean-deploy/<run-id>/`. Default region is `ap-northeast-1`; override with `--region` or the `REGION` env var. ETA ~45-60 min for a full clean run.

### Option 4: Manual per-stack deployment (advanced)

For per-stack control — deploying individual stacks, redeploying after a targeted change, or integrating CMS into a larger CDK app — see the per-stack `make` targets listed in Option 2 step 4 (`make data-processing`, `make phase1`, `make phase4`, `make deploy-fleetwise`, `make deploy-simulation`, `make deploy-commands`, `make deploy-ws-fanout`). The full per-stack deploy chain, ordering constraints, and operator runbook are in [`docs/DEPLOYMENT.md`](./docs/DEPLOYMENT.md). Most users should use Option 2 instead.

## Deployment Validation

1. **Verify CloudFormation stacks**: Open the AWS CloudFormation console and verify that all stacks with names starting with `cms-{deployment-stage}` show `CREATE_COMPLETE` status.

2. **Check DynamoDB tables**: In the DynamoDB console, verify that the following tables are created:
   - `cms-{deployment-stage}-storage-vehicles`
   - `cms-{deployment-stage}-storage-drivers`
   - `cms-{deployment-stage}-storage-trips`
   - `cms-{deployment-stage}-storage-safety-events`
   - `cms-{deployment-stage}-storage-maintenance-alerts`

3. **Validate ElastiCache for Redis**: Verify the Redis cluster is running:
   ```bash
   aws elasticache describe-cache-clusters --cache-cluster-id cms-staging-redis --show-cache-node-info
   ```

4. **Verify Amazon Location Service resources**: Check that map and place index are created:
   ```bash
   aws location list-maps
   aws location list-place-indexes
   ```

5. **Validate MSK cluster**: Run the following command to check MSK cluster status:
   ```bash
   aws kafka describe-cluster --cluster-arn $(aws kafka list-clusters --query 'ClusterInfoList[0].ClusterArn' --output text)
   ```

6. **Test API Gateway**: Verify the API is accessible:
   ```bash
   curl -X GET $(aws cloudformation describe-stacks --stack-name cms-staging-ui --query 'Stacks[0].Outputs[?OutputKey==`ApiEndpoint`].OutputValue' --output text)/api/v1/health
   ```

## Running the Guidance

### Accessing the Web Application

1. **Get the CloudFront URL** from the deployment output or run:
   ```bash
   aws cloudformation describe-stacks --stack-name cms-staging-ui --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontURL`].OutputValue' --output text
   ```

2. **Open the URL** in your web browser to access the Connected Mobility dashboard.

3. **Default login credentials**:
   - Username: `FleetManager@example.com`
   - Password: generated at deploy time and stored in AWS Secrets Manager (`cms-<stage>-demo-user-password`). Retrieve it on any machine after deployment:
     ```bash
     aws secretsmanager get-secret-value --secret-id cms-staging-demo-user-password \
       --query SecretString --output text --region us-west-2
     ```
   - For password rotation procedures, see `docs/DEPLOYMENT.md` § Demo credential rotation.

4. **Connected Services portal** (its own CloudFront distribution):
   - URL: the `DistributionDomainName` output of the `cms-<stage>-connected-services-ui` stack.
     The `CallbackOrigin` output shows the origin it signs in from, including a custom domain if you configured one.
     ```bash
     aws cloudformation describe-stacks --stack-name cms-staging-connected-services-ui \
       --query 'Stacks[0].Outputs[?OutputKey==`DistributionDomainName`].OutputValue' --output text --region us-west-2
     ```
   - Sign-in uses the same Cognito user pool as the CMS console. The portal admits only users in the
     `connected-services` group; anyone else sees an Unauthorized page. To grant access:
     `aws cognito-idp admin-add-user-to-group --user-pool-id <pool-id> --username <email> --group-name connected-services`.
   - Subscribers are separate accounts. An operator creates them through the portal's subscriber
     administration (`POST /admin/subscribers`), which puts them in the `subscriber` group that the
     subscription API routes require. `scripts/provision-cms-subscriber.py` does this for CMS's own
     telemetry subscription.

![img](/documentation/login1.png)

### Container image customization

By default, `make deploy-simulation` pulls pre-built simulation images from public ECR (`public.ecr.aws/o0q5e8r2/cms-sim-service:<version>` and `cms-fwe-agent:<version>`). This requires **no local container builder** and works across all regions.

To use custom simulation images (your own registry or private builds):

```bash
# Override the default public-registry image with your custom image
PUBLIC_ECR_REGISTRY=<your-registry> PUBLIC_ECR_TAG=<your-tag> \
  make -C deployment deploy-simulation DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2
```

For **active development** (editing `services/simulation/Dockerfile` or `Dockerfile.fwe`), use local container builds:

```bash
# Requires a local container builder (docker, finch, or podman)
SIM_IMAGE_MODE=asset CDK_DOCKER=finch make -C deployment deploy-simulation \
  DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2
```

See `docs/DEPLOYMENT.md` for additional customization patterns and the container builder setup guide.

### Running the Fleet Simulator

The solution includes two simulator deployment modes:

#### Cloud Simulator (Recommended)

The cloud simulator runs on ECS Fargate/EC2 and is managed entirely through the UI. No local setup required.

1. **Deploy the simulation stack** (included in `make deploy` or manually):
   ```bash
   cd deployment
   DEPLOYMENT_STAGE=prod DEPLOY_SIMULATION=true cdk deploy cms-prod-simulation --require-approval never
   ```

2. **Start simulations from the UI**:
   - Navigate to the **Fleet Simulation** panel to start multi-vehicle simulations
   - Or use the **Trip Simulator** on a vehicle's detail page for single-vehicle trips
   - In MQTT Direct mode, the simulator runs as a Fargate task publishing JSON telemetry
   - In FWE mode, a persistent ECS task per vehicle runs the FWE agent with a `vehicle-ecu` sidecar managing command subscriptions and presence state. Per-trip simulator tasks signal the vehicle-ecu and write to isolated virtual CAN buses per vehicle.

3. **Monitor in real-time**: The simulation panel shows merged `[SIM]` and `[FWE]` logs. The vehicle detail page has separate Sim Logs and FWE Logs tabs.

**FleetWise campaign required for vehicle-telemetry vehicles.** Per spec `2026-09-01-cms-campaign-follows-enrollment`, a vehicle-telemetry vehicle without a `RUNNING` FleetWise campaign is refused by `POST /api/simulation/start` with an actionable 400 explaining the recovery paths. To avoid this at enrollment time, `POST /api/v1/vehicles` accepts an optional `campaignTemplateRef` field on the request body (surfaced in the Create Vehicle wizard as a "FleetWise campaign" dropdown) — supplying it inline-provisions the per-vehicle campaign row in the same 201 response. For retroactive attachment to legacy vehicles, use the operator override `deployment/scripts/deploy_vehicle_campaign.py`. Cloud-telemetry vehicles do not use FleetWise campaigns and skip the gate entirely.

#### Local Simulator

The local simulator runs on your development machine using Docker for the FWE agent and a Python process for telemetry generation. Useful for development and debugging.

1. **Start the simulator service**:
   ```bash
   cd services/simulation
   ./manage_simulation.sh start
   ```

2. **Access the local API** at `http://localhost:5001` — the UI auto-detects local vs cloud mode.

3. **For FWE mode locally**, Docker is required. The simulator uses the official FWE image (`public.ecr.aws/aws-iot-fleetwise-edge/aws-iot-fleetwise-edge:v1.3.2`) and creates virtual CAN interfaces on the host.

4. **Create a vehicle and run the simulator** from the UI to generate data.

### Expected Output

- **Fleet Dashboard**: Real-time metrics showing active vehicles, total trips, and safety events
- **Vehicle Management**: List of registered vehicles with status and location information
- **Driver Management**: Driver profiles with trip history and safety scores
- **Trip Analytics**: Detailed trip information with routes, duration, and performance metrics
- **Safety Events**: Real-time safety alerts and incident tracking

### Vehicle Identity and Entitlement

The vehicle record carries three producer/identity fields working together to control visibility, routing, and subscription eligibility:

| Field | Values | Purpose | Authority | Writable |
|-------|--------|---------|-----------|----------|
| **`producer`** | `meridian`, `oem1`, `cms-native` | Producer identity (who built the vehicle); controls CS visibility filter | Producer | At creation only |
| **`dataSource`** | `vehicle-telemetry`, `cloud-telemetry` | **Delivery mode** (how telemetry arrives), not producer identity | Consumer (CMS) | Per subscriber |
| **`sold_to`** (NEW) | DMS customer id (e.g., `CUST-0040014E`) | Entitlement anchor — who may subscribe to this VIN | DMS | Never (read-only) |

**Important Distinction**: `dataSource` is how CMS obtains data (delivery fact owned by the consumer); `producer` is who built the vehicle (producer fact). CS filters its inventory on **`producer`**, never on `dataSource`. This prevents a consumer's delivery choice from punching holes in the producer's registry.

**Subscription Eligibility**: Determined by `sold_to` matching the caller's `custom:customerIds` claim. An operator cannot self-grant by editing its fleet — entitlement reads only immutable, token-based identity.

#### Vehicle Data Sources (CMS → CS visibility)

CMS supports vehicles from three producers:

1. **Meridian** (`producer: meridian`, 100 staging vehicles)
   - 90 with `dataSource: vehicle-telemetry` (FWE-instrumented, onboard signal collection)
   - 10 with `dataSource: cloud-telemetry` (cloud-only models like MERIDIAN-MISTRAL)
   - **Visible in CS portal** via `producer == meridian` filter

2. **OEM1** (`producer: oem1`, 48 staging vehicles)
   - All Ford vehicles via external OEM cloud-to-cloud feed
   - Enrollment, status sync, and real-time dashboards managed by admin Lambdas
   - **Not visible in CS portal** (OEM1-operated fleet, different entitlement model)

3. **CMS-native** (`producer: cms-native`, 1 demo vehicle)
   - DemoMotors reference vehicle with `dataSource: vehicle-telemetry`
   - **Not visible in CS portal**

**UI Rendering**: The vehicle list in CMS displays a **Source** column distinguishing OEM1 (amber badge) from CMS-native (blue badge). OEM1 vehicles show enrollment status, built-in diagnostics, trip history, and signal-coverage tabs; CMS-native vehicles show FleetWise logs, DTC codes, simulation controls, and remote commands. The remote-commands palette carries four operator commands (lock/unlock, remote start, find my vehicle, pre-condition cabin) and renders only for the platform-admin and fleet-operator personas — see [Fleet-Operator Remote Commands](#fleet-operator-remote-commands). For enrollment workflows, see [Enrolling OEM1 Vehicles](./docs/architecture/oem1-add-vehicle.md) and the [OEM1 troubleshooting guide](./docs/runbooks/oem1-add-vehicle.md).

**CS Simulation Routing** (NEW): When an operator triggers simulation from the CS portal, the transport (MQTT/onboard vs. HTTP/cloud) is **derived** from the vehicle's `dataSource`, never chosen as a parameter. This ensures the simulation path matches reality and the vehicle record stays consistent with the data flowing through it.

## Next Steps

### Customization Options

1. **Add Custom Telemetry Fields**: Modify the Flink processors in `modules/flink/` to process additional vehicle data points.

2. **Integrate External APIs**: Extend the Lambda functions to integrate with third-party fleet management or mapping services.

3. **Custom Safety Rules**: Implement custom safety event detection logic in the Flink applications.

4. **Multi-Region Deployment**: Deploy the solution across multiple AWS regions for global fleet management.

5. **Advanced Analytics**: Integrate with Amazon SageMaker for predictive maintenance and driver behavior analysis.

### Production Considerations

- **Security**: Implement proper IAM roles and policies for production use
- **Monitoring**: Set up CloudWatch alarms and dashboards for operational monitoring
- **Backup**: Configure automated backups for DynamoDB tables
- **Scaling**: Adjust MSK cluster size and Flink parallelism based on fleet size

## Cleanup

**Warning**: This will permanently delete all resources and data created by this Guidance.

1. **Tear down all stacks** using the Makefile wrapper (recommended):
   ```bash
   cd deployment
   make tear-down-staging   # for staging  (DEPLOYMENT_STAGE=staging, us-west-2)
   # or
   make tear-down-prod      # ARCHIVED — no prod target exists; see note below
   ```

   > **`make tear-down-prod` has nothing to tear down.** CMS became a single
   > environment on 2026-09-04; the `us-east-1` prod deployment was removed by spec
   > `.kiro/specs/2026-09-04-cms-prod-teardown/`. The target is retained only for
   > operators re-creating a prod environment from scratch.
   The tear-down target destroys stacks in reverse-dependency order and waits for each deletion to complete before proceeding. See [`docs/DEPLOYMENT.md` § Tear-down](docs/DEPLOYMENT.md#tear-down) for the full runbook, including the manual cleanup steps required for resources protected by the Bucket RETAIN aspect (S3 buckets with explicit names are retained by design and must be emptied and deleted manually).

2. **Manually clean up retained resources** after stack deletion:
   - **S3 buckets**: globally-named buckets (e.g., `cms-{stage}-storage-*`, `cms-{stage}-ui-*`) are retained by the Bucket RETAIN aspect. Empty each bucket, then delete it from the S3 console or via `aws s3 rb s3://<bucket-name> --force`.
   - **CloudWatch log groups**: log groups are not deleted by CDK by default. Remove with: `aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/cms-{stage}" --query 'logGroups[].logGroupName' --output text | xargs -I {} aws logs delete-log-group --log-group-name {}`
   - **ECS clusters**: simulation, ws-fanout, commands, and OEM1 connector clusters may need manual deletion if ECS task termination does not complete automatically.
   - **Bedrock agents**: (Retired 2026-09-06) The VFO Bedrock Agents stack was removed per spec `2026-09-05-cms-vfo-teardown`. No CMS-side Bedrock resources remain to delete. Sibling accelerator CVX still uses Bedrock AgentCore Runtime; if you deployed a CVX runtime alongside CMS, it is CVX-side and should be torn down per that accelerator's runbook, not this one.

## FAQ, known issues, additional considerations, and limitations

### Known Issues

1. **The FWE simulation host runs out of kernel memory within days, and UDS fault injection stops.** A running fwe-agent leaks CAN ISO-TP socket references (about 185 an hour, measured 2026-09-28 on a t4g.medium). After about 2.5 days the host's unreclaimable kernel memory fills, DTC fetches fail with `Cannot allocate memory`, and injected faults produce no DTC row, while ECS still reports the host and its tasks as healthy. Workaround: replace the FWE host (terminate the instance; its Auto Scaling group launches a fresh one) before a demo that injects faults. Issue: `issues/2026-08-04-can-rcvlist-orphaned-filter-leak/`.

2. **MSK Cluster Creation Time**: MSK cluster creation can take 15-20 minutes. This is normal AWS behavior.

3. **Flink Application Startup**: Flink applications may take 5-10 minutes to start processing data after deployment.

### Additional Considerations

- **Data Retention**: DynamoDB tables use on-demand billing. Consider implementing TTL for cost optimization in production.
- **Security**: This Guidance creates public API endpoints for demonstration purposes. Implement proper authentication for production use.
- **Scaling**: The default configuration supports up to 1,000 vehicles. For larger fleets, adjust MSK cluster size and Flink parallelism.

### Limitations

- **Real-time Processing**: Current implementation processes data with 1-2 second latency. Sub-second processing requires additional optimization.
- **Geographic Scope**: Map services are optimized for North American and European regions.
- **Device Types**: Currently optimized for passenger vehicles. Commercial vehicle support requires additional configuration.

For any feedback, questions, or suggestions, please use the [issues tab](https://github.com/aws-solutions-library-samples/guidance-for-connected-mobility-on-aws/issues) under this repository.

## Deployment & Operations

CMS deploys to a single AWS account with two-region environment isolation:

| Environment | Region | Default behavior |
|-------------|--------|------------------|
| **staging** | us-west-2 | Auto-deployed on every push to `main` (with GitHub environment approval) |
| **prod** | *(none — archived)* | **CMS is single-environment as of 2026-09-04.** The `us-east-1` prod deployment was torn down per `.kiro/specs/2026-09-04-cms-prod-teardown/`; `staging` in `us-west-2` is the sole live environment and serves the domain configured via `UI_CUSTOM_DOMAIN` in `deployment/config/staging.env`. |
<!-- verify: aws cloudformation list-stacks --query 'StackSummaries[?starts_with(StackName,`cms-prod-`) && StackStatus!=`DELETE_COMPLETE`].StackName' --region us-east-1 --output text -->

### Quick Start (Laptop)

```bash
# Pre-flight checks (read-only, ~30s)
bash deployment/scripts/preflight-staging.sh

# Deploy to staging (~45 min, real AWS resources)
make -C deployment staging-deploy

# Tear down staging when done
make -C deployment tear-down-staging
```

**Note on deploy commands:** `make -C deployment staging-deploy` is the modern wrapper that sources environment-specific config and adds safety checks. The legacy `make deploy-all` path is preserved for direct phased deploys and remains fully functional for advanced use cases.

**Operator-triggered clean-deploy integration test:** before promoting
CMS to a new region or publishing a new release tag, run the
operator-triggered first-time-deployment harness:

```bash
make -C deployment clean-deploy-test                  # default REGION=ap-northeast-1
make -C deployment clean-deploy-test REGION=eu-west-2 # any supported region
```

The harness performs CDK bootstrap, full `deploy-all`, demo-data
seeding, 14 setup-layer assertions + 1 telemetry assertion, then
trap-driven teardown and audit. The harness automatically isolates from
your operator-persisted CDK context to prevent cross-region resource
collisions. **Not a CI gate** — fresh-region cost is operator-controlled.
See
[`docs/DEPLOYMENT.md` § Clean-deploy integration test](docs/DEPLOYMENT.md#clean-deploy-integration-test)
for the full runbook.

**Note on staging access (internal contributors):** the staging
environment sits behind an external SSO gate enforced at the
CloudFront edge, so unauthenticated visitors cannot enumerate the
staging app surface. Internal contributors authorized in the
appropriate access group reach the existing Cognito login page
exactly as before. Operator setup for the gate is documented in an
internal staging-gate runbook kept under `docs/` and excluded from
the public mirror via `.publish-exclude` (the runbook itself is
intentionally not present in the public mirror).

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for the complete operations runbook including CI/CD setup, troubleshooting, baseline regeneration, and tear-down procedures.

## Notices

*Customers are responsible for making their own independent assessment of the information in this Guidance. This Guidance: (a) is for informational purposes only, (b) represents AWS current product offerings and practices, which are subject to change without notice, and (c) does not create any commitments or assurances from AWS and its affiliates, suppliers or licensors. AWS products or services are provided "as is" without warranties, representations, or conditions of any kind, whether express or implied. AWS responsibilities and liabilities to its customers are controlled by AWS agreements, and this Guidance is not part of, nor does it modify, any agreement between AWS and its customers.*

## Authors

- AWS Solutions Architecture Team
- AWS Connected Mobility Specialists
