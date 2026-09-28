# Connected Services Portal — Data Model Surfaces

## Overview

The Connected Services (CS) portal exposes the CMS's existing data-model backend as a read-only surface for producers (external systems or OEMs) to browse vehicle models, signal catalogs, and other fleet configuration metadata. **CS owns no data-model storage** — all data is read from CMS's deployed `cms-{stage}-data-processing-api` service.

This document describes the architectural relationship, the shared API contract, and the key constraints that preserve cross-repo consistency.

## Core Principle: CS Reads CMS, Does Not Duplicate

Rev 1 of the CS data-model spec (2026-09-14) incorrectly planned to build new tables, handlers, and seeded JSON catalogs to populate these surfaces. That approach was rejected because:

1. The backend already exists, is deployed live, and is Cognito-gated
2. Building a second source of truth for signals, models, and manifests introduces maintenance burden and silent drift
3. The correct pattern is *exposure and binding*, not *storage and seeding*

**Implementation consequence**: CS does not create new Lambda handlers for data-model operations. All reads go through the existing CMS API; all writes (if any) go back to CMS and are subject to CMS's authorization model.

## Shared Data Model Across CMS, CS, and DMS

The three portals (CMS, CS, DMS) share a single vehicle data model and must not reshape it in transit:

| Concept | Canonical Location | Read By | Write By |
|---------|-------------------|---------|----------|
| Vehicle model manifests | CMS `cms-{stage}-data-processing-api` `/model-manifests` | CMS, CS, DMS | Admin (CMS only) |
| Signal catalog | CMS `cms-{stage}-data-processing-api` `/signals` | CMS, CS, DMS | Admin (CMS only) |
| Decoder manifests | CMS `cms-{stage}-data-processing-api` `/decoder-manifests` | CMS, CS, DMS | Admin (CMS only) |
| Vehicle-to-model binding | CMS `cms-{stage}-storage-vehicles` table, `modelManifestName` field | CMS, CS, DMS | Producers and admins (CMS) |

**Constraint**: CS must not reshape or flatten response bodies from the data-processing API. A CS-specific projection (e.g., flattening nested ECU arrays) forks the model and causes silent divergence when DMS implements the same feature. Response types transcribe the API shapes verbatim.

## Data Model Surfaces in CS Portal

### 1. Signal Catalog

**Endpoint**: `GET https://enn83ljo91.execute-api.us-west-2.amazonaws.com/prod/signals` (CMS data-processing API)

**Visible in CS Portal**: Signal Catalog screen, browsable by signal ID and functional domain (`signal_group`)

**Sparse Fields**: Only 65 of 302 signals have `can_id`; 204 have `cycle_ms`. Absent fields render distinctly from zero.

**Key Fields**: `signal_id`, `signal_name`, `signal_group` (24 functional domains: adas, body, cabin_climate, chassis, connectivity, core_telemetry, diagnostics, doors, emissions, environment, ev_charging, ev_specific, geofence, gps, lighting, maintenance, mirrors, powertrain, safety, security, tpms, vehicle_control, windows, wipers), `data_type`, `unit`, `min_value`, `max_value`

**⚠️ ECU Projection Warning**: The real `/signals` payload has **no ECU field**. The `source_ecu` values appear in the fixture (`vehicleModelsData.ts`) but do not exist in the live API. ECUs are a projection of the model manifest (see § 2), not of the signal catalog.

### 2. Vehicle Models

**Endpoint**: `GET https://enn83ljo91.execute-api.us-west-2.amazonaws.com/prod/model-manifests` (CMS data-processing API)

**Visible in CS Portal**: Vehicle Models screen, searchable by model line and production phase

**Cross-Reference**: The `decoderManifestRef` field links to Decoder Manifests (§ 4). This link resolves for `CMS-FLEET-MODEL` and must be verified per-manifest before rendering as universal.

**Stale Data Warning**: `vehicleCount` on each manifest is stale and must not be read. Derive counts from the vehicles table instead.

**Key Fields**: `modelManifestName` (e.g., `MERIDIAN-TRAILWIND`), `displayName`, `modelLine`, `platform`, `productionPhase`, `status`, `decoderManifestRef`, `ecus[]`

### 3. ECUs (Electrical Control Units)

**Source**: Derived from `ecus[]` on the model manifest (not from signals; see warning in § 1)

**Visible in CS Portal**: ECUs screen, browsable per vehicle model

**Sparse Fields**: `signalCount` and `baselineVersion` are present on only 8 of 65 ECU entries (those on `CMS-FLEET-MODEL`). Meridian model manifests' ECU entries carry only `ecu` code and `displayName`. Absent fields render distinctly from zero.

**Key Fields**: `ecu` (e.g., TCU, BMS, VCU, BCM), `displayName`, `signalCount` (when present, this is a **stored count with no derivable counterpart**, not computed), `baselineVersion` (when present)

**Constraint**: Do not attempt to join signals to ECUs. No signal names an ECU, and no ECU lists its signals.

### 4. Decoder Manifests

**Endpoint**: `GET https://enn83ljo91.execute-api.us-west-2.amazonaws.com/prod/decoder-manifests` (CMS data-processing API)

**Visible in CS Portal**: Decoder Manifests screen, linked from Vehicle Models via `decoderManifestRef`

**Key Fields**: `decoderManifestName`, `decoderManifestVersion`, `modelName`, `status`

### 5. Data-Collection Campaigns (updated 2026-09-20)

**Source**: Data-processing API (`GET /campaigns`, `POST /campaigns/assign`)
<!-- verify: grep -n "def get_campaigns\|def assign_campaign" services/data_processing/lambda/data_processing_api.py -->

**Visible in CS Portal**: Data-Collection Campaigns screen

**Purpose**: OEMs view and assign their own FleetWise collection campaigns to specific vehicles

#### The table holds THREE kinds of row, not one

`cms-{stage}-campaigns` stores definitions and assignments in the same shape,
distinguished only by `targetArn`. Counts measured on staging 2026-09-20:

| `targetArn` | rows | stored `status` | what it is |
|---|---|---|---|
| `template` | 10 | `ACTIVE` | a campaign **definition** |
| `vehicle:<VIN>` | 28 | `RUNNING` | an **assignment** to one vehicle |
| `fleet:<id>` / `all` | 2 | `RUNNING` / `SUSPENDED` | a fleet-wide or global assignment |

<!-- verify: aws dynamodb scan --table-name cms-staging-campaigns --region us-west-2 --output json | python3 -c "import json,sys,collections; items=json.load(sys.stdin)['Items']; print(collections.Counter('template' if i['targetArn']['S']=='template' else ('vehicle' if i['targetArn']['S'].startswith('vehicle:') else 'fleet/all') for i in items))" -->

**40 rows for 11 campaigns**, and the row count grows linearly with assignments. Reading
this table as one-row-per-campaign is what made the flat screen misleading.

**11 distinct `campaignName`s, but only 10 template rows.** `cms-fleet-telemetry-30s` has
assignments and **no definition**, so the "campaign with no template" case is already live
on staging, not hypothetical. Any grouping must emit it and keep its assignments visible.

<!-- verify: aws dynamodb scan --table-name cms-staging-campaigns --region us-west-2 --output json | python3 -c "import json,sys; i=json.load(sys.stdin)['Items']; n={x['campaignName']['S'] for x in i}; t={x['campaignName']['S'] for x in i if x['targetArn']['S']=='template'}; print('campaigns',len(n),'| templates',len(t),'| missing',sorted(n-t))" -->

**Grouping key is `campaignName`, NOT `campaignId`.** `campaignId` is
`{campaignName}-{vin}` on an assignment and `{campaignName}` on the template, so grouping
by it puts every assignment in its own group.

**`RUNNING` means _assigned_, not _transmitting_.** Nothing reconciles an assignment row
against actual telemetry — a vehicle can hold a `RUNNING` row and transmit nothing. A
template's stored status is `ACTIVE`, so the same column carries two vocabularies.

**There is no campaign start/stop API.** Assignment *is* the start (the row is written
with `status: RUNNING`); the only inverse is `DELETE /campaigns/assign`, which deletes the
row. Any UI offering "Stop" would be presenting a destructive operation as a pause.

#### Signals: `signalsToCollect` holds numeric IDs, not names — and neither side is unique

A campaign's `signalsToCollect` is a list of signal **IDs** (`{"N":"1"}` in DynamoDB,
`1.0` over the wire). Resolving them needs `GET /signals`, joined on `signal_id`.

Counts measured on staging 2026-09-20. Every number here was wrong in this doc's first
revision; see the note at the end of the section for why.

- **The catalog the API exposes is a subset.** `cms-staging-signal-catalog` holds **319**
  items; `GET /signals` returns the **302** that carry a `status`, because the handler
  queries a sparse `status-index`. The other **17** are unreachable through the API.
- **All 302 returned records carry a `signal_id`.** (An earlier revision of this section
  said "295 of 302", misreading `dataModelClient.ts`'s `7/302: alias_of_signal_id` line —
  that 7 is the count of *alias* records, not of records lacking an id.)
- **`signal_id` is NOT unique.** 7 ids — 130, 156, 158, 162, 166, 170, 265 — appear
  **twice** each: once canonical, once as a `*_JsonAlias` variant with a camelCase
  `json_field` and an `alias_of_signal_id` back-reference. Index on
  `alias_of_signal_id`'s absence to pick the canonical record; a last-wins `Map` picks the
  **alias** for all 7, because the aliases sort later in the API's order.
- **`signalsToCollect` repeats ids.** `cms-fleet-gps-10s` holds **293 entries but 286
  distinct** — those same 7 ids appear twice in the campaign's own list. Count distinct;
  entry-counting makes "293 of 293 resolve" true only because a duplicate resolves twice.
- `signal_id` arrives as a JSON **number**. Its TypeScript type declared `string` until
  2026-09-20, which is why `SignalDetailView`'s `s.signal_id === decodedId` never matched
  — see `issues/2026-09-20-signal-detail-never-resolves-by-signal-id/`. **Coerce both
  sides of any join.**
- `signal_group` is the useful presentation axis: 24 groups across that campaign's set
  (`core_telemetry` 39, `vehicle_control` 31, `maintenance` 25, `doors` 24, `safety` 23,
  … `body` 1).

<!-- verify: aws dynamodb scan --table-name cms-staging-signal-catalog --region us-west-2 --output json | python3 -c "import json,sys; i=json.load(sys.stdin)['Items']; print('table',len(i),'| with status',sum(1 for x in i if 'status' in x),'| with signal_id',sum(1 for x in i if 'signal_id' in x))" -->
<!-- verify: curl -s "$DP/signals" -H "Authorization: Bearer $TOKEN" | python3 -c "import json,sys,collections; s=json.load(sys.stdin)['signals']; ids=[int(x['signal_id']) for x in s]; d={k:v for k,v in collections.Counter(ids).items() if v>1}; print('returned',len(s),'| all carry id',all('signal_id' in x for x in s),'| duplicate ids',sorted(d),'| aliases',sum(1 for x in s if x.get('alias_of_signal_id')))" -->
<!-- verify: aws dynamodb get-item --table-name cms-staging-campaigns --region us-west-2 --key '{"campaignId":{"S":"cms-fleet-gps-10s"}}' --output json | python3 -c "import json,sys; ids=[int(x['N']) for x in json.load(sys.stdin)['Item']['signalsToCollect']['L']]; print('entries',len(ids),'| distinct',len(set(ids)))" -->

#### ⚠ ECUs cannot be attributed to a telemetry campaign

There is **no signal→ECU mapping in any reachable surface**. Verified 2026-09-20 across
all 319 signal-catalog records (no `ecu` field — corroborated independently by
`SignalItem`'s own docstring in `dataModelClient.ts`) and all 21 campaign attributes (no
ECU reference).

| Campaign kind | ECU information available | Honest label |
|---|---|---|
| **UDS / DTC** (`signalsToFetch` present) | **Real, per-campaign.** 9 entries, each `{signalId, functionName: "DTC_QUERY", executionFrequencyMs, maxExecutionCount, params}`; `params[0]` is the 1-based ECU index and the `signalId` resolves to `ECU1_DTC_INFO`…`ECU9_DTC_INFO`. | "ECUs polled" |
| **Telemetry** (`signalsToCollect` only) | **Not derivable.** Only the ECU roster of the model manifests referencing the campaign's decoder. | "ECUs available on vehicles running `<decoder>`" — context, **not** attribution |

**Join on `decoderManifestRef`, and do NOT pool across manifests.** The manifest field is
`decoderManifestRef` (there is no `decoderManifestId` on a model manifest). All 40
campaign rows reference `cms-fleet-v3`, and exactly **one** manifest references it:
`CMS-FLEET-MODEL`, carrying **8** ECUs — `ADAS, BCM, BMS, CCU, GW, IVI, TCU, VCU`.

`ECM` is **not** among them; it appears only on `MERIDIAN-AZIMUTH`, `MERIDIAN-MISTRAL` and
`MERIDIAN-SIROCCO`. The 9-code union across all 8 manifests is **not** any one vehicle's
roster, and presenting it as this campaign's coverage would be exactly the fabricated
column this section exists to prevent. An earlier revision of this doc did pool it.

<!-- verify: curl -s "$DP/model-manifests" -H "Authorization: Bearer $TOKEN" | python3 -c "import json,sys; mm=json.load(sys.stdin)['modelManifests']; [print(m['modelManifestName'], sorted(e['ecu'] for e in (m.get('ecus') or []))) for m in mm if m.get('decoderManifestRef')=='cms-fleet-v3']" -->

Do not infer ECUs from `signal_group`: the groups are functional domains
(`core_telemetry`, `doors`, `tpms`), not control units, and the mapping would be
fabricated.

> **Why this section's counts were wrong once.** Its first revision carried two
> `verify:` commands that both ran green while three of the prose claims beside them were
> false. One counted row *kinds* — which cannot distinguish 10 templates from 11
> campaigns — and the other printed a record count and a type, never computing distinct
> or duplicate ids. A `verify:` comment that does not compute the claim it sits beside
> only proves a command runs. The commands above each compute the specific number in the
> sentence they follow.

**Campaign Ownership Model**:

Three `owner` values exist on campaign rows:

| Value | Meaning | Visible in CS? |
|-------|---------|----------------|
| `"oem"` | Authored by the CS/OEM caller (create, update, or assign via data-processing API) | **Yes** — CS only shows these |
| `"fleet:<fleetId>"` | Authored by a fleet operator for that specific fleet | No — fleet-internal campaigns are not the OEM's to manage |
| `"platform"` | Auto-created baseline by `_ensure_telemetry_campaign` (written by the simulation Lambda, not by any operator) | No — baseline campaigns are infrastructure-managed |

CS filters to `owner == "oem"` on the client side. The filtering is behavioral (not API-enforced) — the data-processing `GET /campaigns` endpoint returns all campaigns for authorized callers. CS's filter is a product decision, not an authorization boundary.

**Assignment**: `POST /campaigns/assign` with body `{ "campaignName": string, "vehicles": [vin] }`. The field name is `vehicles` (plural) — NOT `vins` or `vinList`. The list takes **VINs, not `vehicleId`s**: the handler writes `campaignId = "{campaignName}-{vin}"` / `targetArn = "vehicle:{vin}"`, and the simulation Lambda's telemetry-campaign check reads that row **by VIN**. A `vehicleId` here used to write a row nothing could find; since 2026-09-20 the server resolves every entry against the vehicles table's `vin-index` and **rejects** anything that does not resolve to exactly one vehicle. The two identifiers differ for most CS demo vehicles and **not only by prefix** — `VEH-MRDN-0011`'s VIN is `MRDN0000000000013` — so read `vin` off the vehicle record; never derive it.

The response carries **three** lists, and they mean different things:

| Field | Meaning |
|---|---|
| `assigned` | rows **newly written** this call |
| `alreadyAssigned` | the VIN already had this campaign — **idempotent success**, not a failure |
| `rejected` | `{value, reason}`; did not resolve by VIN, nothing written |

Do not branch on `assigned.length > 0` alone. The write is conditional
(`attribute_not_exists(campaignId)`), so a repeat assignment legitimately returns
`assigned: []`. Reading that as failure produced an error that could never clear —
see `issues/2026-09-20-cs-quick-assign-writes-vehicleid-keyed-campaign-row/`.
`alreadyAssigned` and `rejected` are absent on a server predating that change; treat
absent as empty.

**Guard**: the client-side `canAssignCampaignToVehicle` requires the vehicle to have a `modelManifestName` **and** the campaign to have a `decoderManifestId`. It does **not** compare them. The 48 OEM1 vehicles without `modelManifestName` are blocked.

**Authorization**: Gated by `connected-services` Cognito group on the data-processing API (part of the FGS1.1 two-tier policy: `platform-admin` / `fleet-operator` / `connected-services` for writes).

**Constraint**: Data-collection campaigns are NOT the same as OTA campaigns. The OTA screens (`SoftwareCampaignsView`, `RolloutMonitorView`, `SecurityMonitorView`) remain unchanged and fixture-backed.

### 6. Simulation (updated 2026-09-20)

**Entry Point**: CS portal vehicle picker. CS operators trigger a trip simulation by selecting a vehicle and, optionally, customizing trip parameters (city, route length, event scenarios).

**Simulation Topology — One Shared Backend, Three UIs**:

The CMS simulation backend (simulation Lambda + ECS tasks) is shared across three entry points:

| UI | Start Path | Request Shape | Notes |
|----|-----------|-------|-------|
| CMS (`TripSimulatorModal`) | `POST /api/simulation/start` (simulation Lambda directly) | Full fleet-simulation shape: `vehicle_source`, `vehicles`, `interval`, `driver_selection`, `aws_region`, plus event scenarios | Fleet operators with full control |
| CS portal (`SimulateVehicleView`, via subscriptions wrapper) | `POST /simulate/start` (subscriptions plane, T4) | **Parameterized trip start** — optional trip parameters: `city`, `route_length`, `trips` (fixed to 1), and event scenarios. Transport is **always derived** from vehicle's `dataSource`. | Gated on `connected-services` group; transport never accepted as a parameter; picker shows readiness via disabled state + reason tooltip |
| Manual operator debugging | `POST /api/simulation/start` (simulation Lambda directly) | Full fleet-simulation shape | Service operators bypassing normal portals |

**UI Shape (CS Simulate screen, per spec 2026-09-20-trip-intent-param-contract T5.2)**:

Single `Start` button with inline parameter controls:
- **Vehicle Picker** — lists all catalog vehicles. Three readiness states, per spec T5.0 + FG13/FG14: `simulation_ready: true` (or absent, for a pre-T5.0 server) renders normally; `false` renders **disabled** with the reason (`not_fleet_enrolled`, `no_certificate`, `no_telemetry_campaign`); `null` means the readiness scan failed and readiness is **indeterminate** — the option stays **selectable** with a "could not determine readiness" advisory, because disabling on unknown would turn a transient scan failure into a fleet-wide block, and `simulate_start_handler` is authoritative regardless.
- **Campaign coverage line** — renders under the picker once a vehicle is selected (the load effect auto-selects `vehicles[0]`, so this is populated on arrival). Answers "which campaigns will collect", which the screen previously could not: it flagged `no_telemetry_campaign` when nothing covered the vehicle but said nothing at all when something did. Resolved **server-side** in `_annotate_vehicle_readiness` and returned as `telemetry_campaigns` + `telemetry_signal_total` + `telemetry_campaign_applicable`, because the vehicle entry carries no `fleetId` and so a client cannot evaluate `fleet:<id>` coverage.
  - **Plural, because FleetWise is plural.** Every matching scope is reported, not the most specific one — FleetWise runs all matching campaigns concurrently. 8 targetArns on staging carry more than one RUNNING campaign and `vehicle:MRDN0000000000015` carries four. Ordered `(scope rank, -signalCount, campaignId)`: most specific first, largest first within a scope, id as a total tiebreak so the list cannot reorder on DynamoDB scan order alone.
  - **`telemetry_signal_total` is the DISTINCT union, never a sum of `signalCount`.** Ids repeat within one campaign (`cms-fleet-gps-10s` is 293 entries / 286 distinct) and across campaigns, so those four campaigns sum to 304 entries while covering 295 distinct signals. The ids stay server-side: 293 per campaign x 4 campaigns x 99 vehicles is not a list-endpoint payload.
  - **Four states, deliberately distinct** — a non-empty list names the campaigns and scopes; `[]` warns that nothing will collect; **field absent** means the readiness scan failed and makes no claim either way (reporting it as "none" would tell an operator to assign a campaign that may already exist); `telemetry_campaign_applicable: false` reports "not required" for `cloud-telemetry`, which reaches MSK via the rule path. A campaign that targets a `cloud-telemetry` vehicle's VIN is **inert** — 2 of 16 such vehicles on staging have one — so it is named in the detail as something that will not collect, never promoted to the headline.
  - Absent-vs-empty makes the deploy order safe both ways: an old UI against a new Lambda and a new UI against an old Lambda both degrade to "Campaign coverage unknown" rather than to a false "no campaign".
  - Wording lives in `telemetryCampaignSummary.ts`, named `TelemetryCampaign*` rather than `Campaign*` because `contentBoundary.test.ts` Suite 3 reserves the bare form for software (OTA) campaigns.
- **"View signals collected"** — opens `TelemetryCampaignDetailModal`, which reuses `screens/data-model/CampaignSignalsPanel` for the per-campaign signal breakdown rather than reimplementing the signal-catalog join; that panel owns the grouping, the collapse-by-default behaviour, and the entries-vs-distinct reconciliation. Fetches `fetchDataProcessingCampaigns()` + `fetchSignals()` on open (the same split `CampaignDetailView` uses). Four load states stay distinct and an unconfigured data-processing API renders as information, not an error, noting that the readiness counts are unaffected. A coverage entry whose record is missing from the campaigns endpoint, or which carries no identity at all, gets an explanatory row — a blank section would read as "this campaign collects nothing".
- **City** — dropdown; defaults to `seattle`; 7-city allowlist enforced server-side
- **Route Length** — numeric input; clamped to `[5, 60]` on the server; defaults to `20`
- **Safety Events / Maintenance Events** — multiselects populated from `GET /api/v1/event-catalog`. Option values are the catalog's own `event_id`s; labels carry the DTC code and a "Creates DTC" hint where the event has one. Shipped 2026-09-21 (T5.4) once Tier B gained a reader; they were deliberately withheld until then, because a control the backend ignores must not be offered. A failed or unconfigured catalog fetch disables them with the reason and **never** renders as "No events in catalog" — that message is reserved for a genuinely empty catalog.
- Trips sent to backend as `trips: 1` (named constant; the parameterized screen is for tuning route characteristics, not trip count)

**Request Contract for `POST /simulate/start` (spec 2026-09-20-trip-intent-param-contract)**:

```
{
  "vehicle_id": "VEH-CS-DEMO-0021",        // required; must be a single vehicle
  "city": "atlanta",                       // optional; must be one of 7 city values (allowlisted server-side); defaults to "seattle"
  "trips": 1,                              // implicit; always 1 for parameterized starts; not exposed as a form field
  "route_length": 20,                      // optional; positive int, clamped to [5, 60] server-side; defaults to 20
  "safety_scenarios": ["safety.harsh_braking"],        // optional; event-catalog event_ids; OMITTED from the body when the selection is empty
  "maintenance_scenarios": ["maintenance.low_oil_pressure"]  // optional; same; OMITTED when empty
}
```

Both scenario lists carry **event-catalog `event_id` values**, not bare `SCENARIOS` names — see `services/simulation/README.md` § "Trip-intent parameter propagation". The wrapper type-checks each list and caps it at 100 items; the callee is the authority on ranges.

**Server-Derived Fields** (never accepted from request body, always derived server-side from vehicle metadata):
- `mode` — transport layer (`vehicle-telemetry` for FWE, `cloud-telemetry` for OEM1), derived from the vehicle's `dataSource`
- `rule_name` — dispatcher rule, derived from the vehicle's `dataSource` and deployment stage

**Authorization**: The wrapper at `services/connectors/subscriptions/simulate_vehicle/handler.py` enforces `_require_operator` (Cognito `connected-services` group), then derives `mode` and `rule_name` from the vehicle's `dataSource` before dispatching to the simulation Lambda. The wrapper refuses any caller-supplied `mode` or `rule_name` value — this is the spec's **central security property** (see `decisions.md`'s T2.2 mutation-testing entry).

**Event Catalog Access** (spec Group 3): CS's `SimulateVehicleView` needs the same safety and maintenance event list the CMS `TripSimulatorModal` fetches for its Multiselects. The route is `GET /api/v1/event-catalog` on CMS's API Gateway (`connectedServicesApiEndpoint`, already deployed and correctly threaded). Response shape: `{events: [...], count: N}`. CS and CMS share the same Cognito user pool, so the token accepts the same authorization gate. Zero new infrastructure: CS reads from CMS's existing catalog table via the existing CMS API. See `docs/tech.md` § `GET /api/v1/event-catalog` for field details and the 4-call-site inventory.

**Shared UI Component** (spec 2026-09-20-trip-intent-param-contract T5.2): CS's `SimulateVehicleView` previously used `TripSimulatorModal` for parameter entry. As of 2026-09-20, parameters render inline on the screen and the modal is no longer used by CS. `TripSimulatorModal` remains in CMS only for its own Simulate screen. The CS and CMS copies are no longer cross-synced (parity guard removed as obsolete per spec Group 5); the CMS version stays stable and the CS version was deleted as part of the T5.2 refactor.

**Tier A Contract — Intent Parameter Propagation** (spec 2026-09-20-trip-intent-param-contract): The simulation Lambda writes a `tripIntent` map to DynamoDB (`cms-{stage}-trip-intents` table) which the warm simulator container consumes. The contract — which keys are written and which are read — is enforced by `scripts/check_trip_intent_contract.py`, wired into `.github/workflows/lint.yml:102-108` (`trip-intent-contract` job). See `services/simulation/README.md` § "Trip-intent parameter propagation" for the full mechanism and `scripts/check_trip_intent_contract.py` for the guard's structure.

**Path Selection**: The simulation Lambda derives transport from the vehicle's `dataSource` field — it is NEVER accepted as a parameter from any start path (spec § Constraints):
- `dataSource == "vehicle-telemetry"` → FWE agent + collection scheme → MQTT → Flink
- `dataSource == "cloud-telemetry"` (OEM1) → CS product rule → MSK `cs-product-*` → OEMTelemetryProcessor

**Guard**: The simulator never writes vehicle identity fields (`producer`, `sold_to`, `oem_source`). The identity-write guard is pinned in `services/connectors/subscriptions/simulate_vehicle/handler.py` and its test (T2.1).

**Default Behavior**: A CS caller sending only `vehicle_id` (the pre-spec minimal request shape) sees byte-identical behavior to today — `city="seattle"`, `trips=3`, `route_length=20`, no event scenarios. This backward-compatibility contract is pinned by T1.3's test suite (spec Group 1).
<!-- verify: grep -n "def handler\|city.*seattle\|_ALLOWED_CITIES" services/connectors/subscriptions/simulate_vehicle/handler.py -->

## Entitlement Chain: Who May Subscribe to a VIN

**Anchor**: `sold_to` — a DMS customer identifier (a person, not a company; per Q8 resolution), immutable and DMS-owned

**Customer Identity**: `custom:customerIds` claim on the Cognito token — same claim format as `custom:fleetIds` and `custom:dealerIds`

**Entitlement Rule**: A caller may see and subscribe to a vehicle if and only if the vehicle's `sold_to` matches one of the caller's `custom:customerIds`

**Implementation**: 
- Availability table keyed on `(sold_to HASH, vin RANGE)` via GSI, not re-keyed composite
- `_fetch_availability_vins()` queries the GSI per company in the caller's claim set and unions results
- A vehicle with no `sold_to` is invisible to every scoped query (sparse index is fail-closed)

**Critical Constraint**: Nothing on the entitlement path is consumer-writable

| Link | Field | Authority | Writable in CMS/CS |
|------|-------|-----------|-------------------|
| Who may subscribe | `vehicle.sold_to` | DMS (the sale) | **No** — zero write sites in this repo |
| Which customers the caller is | `custom:customerIds` claim | Provisioning | **No** — token-based |
| Is it subscribable yet | Availability table query | Producer | **No** — producer-set only |
| Am I taking it | Subscription `vehicle_scope` | Consumer | Own scope only |

**Why `fleetId` is NOT on the entitlement path**: `fleetId` is consumer-writable in CMS, so an operator could self-grant access to another customer's vehicles by moving them into its own fleet. Entitlement must not read consumer-writable data.

## Vehicle Attributes Added by This Spec

Two new attributes on the vehicle record:

1. **`producer`** — `meridian`, `oem1`, or `cms-native`
   - Set at creation time, mandatory, never consumer-writable
   - Producer identity (who built/shipped the vehicle), not delivery mode
   - CS filters its inventory on `producer == meridian`

2. **`sold_to`** — DMS customer identifier (e.g., `CUST-0040014E`)
   - Set at creation time via `seed_vehicle_sold_to.py` (standing in for DMS sync)
   - Immutable in CMS and CS; zero write sites anywhere in this repo
   - Entitlement anchor: determines who may subscribe to the VIN

**Distinction from `dataSource`**: `dataSource` (`vehicle-telemetry` or `cloud-telemetry`) is a delivery fact owned by the consumer (CMS), not a producer identity. CS filters on **producer**, never on `dataSource`.

## API Dependency Threading

**New Endpoint**: `CONNECTED_SERVICES_UI_DATA_PROCESSING_API_ENDPOINT`

Threaded in the commit that reads it:
1. `deployment/config/staging.env` — the environment value
2. `deployment/Makefile` — passed to CDK as context
3. `deployment/stacks/connected_services_ui_stack.py` — read by the stack
4. `modules/connected_services_ui/src/api/dataModelClient.ts` — read at runtime

**Verification**: `cd deployment && python3 -m pytest stacks/tests/test_cdk_context_key_threading.py -v` enforces this threading in all three files at once.

## No Second Source of Truth

This spec does NOT create:
- Seeded JSON catalogs for signals, models, or manifests
- New Lambda handlers or DynamoDB tables for these resources
- A CS-specific projection or transformation layer

**Consequence for future work**: If a second OEM source is added, reuse the same data-processing API and respond with the same shapes. Do not fork the model.

## Cross-Repo Consistency Drift Guards

The four Data Model screens (Signal Catalog, Vehicle Models, ECUs, Decoder Manifests) move together in one group during implementation because they are referentially coupled:

- Signal Catalog and ECUs cannot be joined at the API level (signals have no ECU field)
- Vehicle Models and Decoder Manifests are joined via `decoderManifestRef`
- Splitting these screens breaks the joins invisibly: every screen renders and the cross-references silently stop resolving

**Test enforcement**: A live-data gate exists after unit tests pass (per `spec-workflow.md`'s live-verification rule). All four screens are rendered against the real `cms-staging-data-processing-api` API and cross-references are verified to resolve.

## Related Documentation

- `docs/tech.md` § "CS portal data-model API" — raw API shapes, field sparseness, and verification details
- `docs/connected-services-consumer.md` — CMS-side consumer of CS-produced telemetry (separate document)
- `docs/cs-data-product-onboarding.md` — for OEMs onboarding new telemetry products to CS
