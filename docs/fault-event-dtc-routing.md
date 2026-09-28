# Fault-Event DTC Routing — Selection → maintenance-alerts (FWE mode)

> Spec: `.kiro/specs/2026-06-16-cms-fault-event-uds-dtc-fwe-routing/`
> Companion docs: `FWE_UDS_DTC.md` (existing two-path dtc-history),
>   `OEM1_DTC_PIPELINE.md` (live OEM1 canonical DTC flow),
>   `event-signal-contract.md` (CAN-backed signal alignment).

## Purpose

This doc captures the contract for fault-event scenarios selected in the Trip
Simulator UI surfacing as `maintenance-alerts` rows in **FWE mode**, via the
authentic UDS-DTC diagnostic path. It is the Group 1 research deliverable for
spec `2026-06-16-cms-fault-event-uds-dtc-fwe-routing`.

It documents what's **already shipped end-to-end into `dtc-history`** (most of
the wiring) and what's **missing** to actually land a `maintenance-alerts` row
on a FWE-UDS-sourced DTC (the actual gap this spec must close).

## TL;DR — what works, what's missing

| Stage | Status |
|---|---|
| 1. UI selection of `maintenance_scenarios` → simulation_lambda | **WORKS** |
| 2. `_build_uds_dtc_map` resolves event_id → catalog `dtc_code` | **WORKS** (`simulation_lambda.py:276`) |
| 3. DTC → ECU mapping (P0300→ECU2, etc.) | **WORKS** (`_ECU_BY_CODE` dict) |
| 4. `UDS_DTC_MAP` env var passed to fwe-simulator ECS task | **WORKS** (`simulation_lambda.py:724`) |
| 5. `uds_dtc_responder.py` subprocess spawned at sim start | **WORKS** (`realtime_telemetry_simulator.py:99` `_spawn_uds_responder()`) |
| 6. Ephemeral per-trip campaign upserted with `signalsToFetch` (DTC_QUERY) | **WORKS** (`simulation_lambda.py:355` `_ensure_uds_campaign`) |
| 7. CampaignSyncProcessor delivers `signalsToFetch` to FWE | (verified separately — campaign-sync pipeline) |
| 8. FWE polls UDS Service 0x19 sub 0x02 every 30s | **WORKS** (cadence per `_build_uds_dtc_map` line 348 `executionFrequencyMs: 30000`) |
| 9. UDS responder answers with active DTCs | **WORKS** (`uds_dtc_responder.py` ISO-TP/CAN, status byte 0x09 = testFailed | confirmedDTC) |
| 10. FWE packages DTCs as `Vehicle.ECU{n}.DTC_INFO` STRING signal | (FWE built-in; not in this repo) |
| 11. `FWTelemetryProcessor.decodeFWTelemetry` parses envelope, emits N synthetic "uds_dtc" event records | **WORKS** (`FWTelemetryProcessor.java`, refactored to `List<String>` return) |
| **12. MaintenanceProcessor reverses `dtc_code → event_id` and writes `maintenance-alerts` + tripId** | **WORKS** (`MaintenanceProcessor.java` `handleUdsDtcEvent` branch) |
| 13. tripId association on maintenance-alerts row + per-trip dedup | **WORKS** (resolver existing; dedup via `active-code-index` GSI: one ACTIVE row per vehicleId\|code\|source) |
| 14. DTCs cleared at trip end | **WORKS** by virtue of Fargate task lifecycle — responder dies with the task; no in-task DTC mutation today (see "Lifecycle" below) |
| 15. Ephemeral campaign row cleaned up at trip end | **GAP** (comment in `_ensure_uds_campaign` says "for future cleanup in `_stop`"; not currently cleaned) |

The headline finding: **Step 12 is the substantive missing piece.** The selection-driven UDS path works; it just lands in `dtc-history`, not `maintenance-alerts`. Spec.md was authored as if the entire path were absent; in fact only the maintenance-alerts mapping is. The implementation work is much smaller than the spec implies, but the design call (where to put the mapping) needs explicit user/architect agreement before coding.

## Components

### A. Sim-side (Python, `services/simulation/`)

#### `lambda/simulation_lambda.py` — selection → DTC + ephemeral campaign

- `_ECU_BY_NUMBER` (line 232) — 9-ECU table with `(name, req, resp, target)` tuples.
  ECU1 = ECU_BRAKE on `0x7E0/0x7E8`, ..., ECU8 = ECU_EVAP on `0x7E7/0x7EF`,
  ECU9 = ECU_BODY on extended-29bit `0x18DA09F1/0x18DAF109`.
- `_ECU_BY_CODE` (line 254) — explicit DTC → ECU# mapping for 19 demo codes.
  Authoritative; agrees with FWE static config, decoder manifest signal 901–909
  (`Vehicle.ECU{n}.DTC_INFO`), and signal-catalog seed.
- `_ECU_SIGNAL_ID = {n: 900 + n}` (line 273) — signal IDs for the 9 DTC_INFO STRING
  signals.
- `_build_uds_dtc_map(maintenance_scenarios) -> (uds_dtc_map, signals_to_fetch, ecus_in_play)`
  (line 276):
  - Looks up each event_id in `cms-{stage}-event-catalog` → `dtc_code`.
  - Events with no `dtc_code` are skipped (catalog/threshold-only path; see
    [Out-of-DTC-path events](#out-of-dtc-path-events) below).
  - Codes with no `_ECU_BY_CODE` entry are logged as missing and skipped.
  - Builds long-form `UDS_DTC_MAP` (`{ECU1: {req, resp, dtcs:[...]}, ...}`) and
    a `signalsToFetch` list (`signalId, functionName=DTC_QUERY, params=[ecu_num, 2, -1], executionFrequencyMs=30000`).
- `_ensure_uds_campaign(vin, signals_to_fetch, ecus_in_play, sim_id)` (line 355):
  - Upserts a per-vehicle campaign row keyed `uds-dtc-{vin[:12]}-{sim_id}`.
  - `decoderManifestId="cms-fleet-v3"` — same as default fleet campaign.
  - `signalsToCollect = [_ECU_SIGNAL_ID[n] for n in ecus_in_play]` so FWE actually
    reports back the DTC_INFO STRING values (not just runs the fetch action).
  - `simulationId` field is set "for future cleanup in _stop" — **cleanup not
    currently implemented** (see [Open follow-ups](#open-follow-ups)).
- CP8 wiring site (line 661):
  ```python
  uds_dtc_map, signals_to_fetch, ecus_in_play = _build_uds_dtc_map(
      config.get("maintenance_scenarios") or []
  )
  if signals_to_fetch:
      _ensure_uds_campaign(vin, signals_to_fetch, ecus_in_play, sim_id)
  uds_dtc_map_json = json.dumps(uds_dtc_map) if uds_dtc_map else ""
  # ... later:
  sim_env = env_overrides + [
      {"name": "CAN_BUS0", "value": can_iface},
      {"name": "UDS_DTC_MAP", "value": uds_dtc_map_json},
  ]
  ```

#### UPSERT model (v0.2.7+)

Processor-sourced DTC rows now follow dedup semantics (see `modules/flink/README.md` § "DTC dedup"):
one ACTIVE row per `(vehicleId, code, source)`. On each detection, queries the `active-code-index`
sparse GSI and either updates the existing row (refresh `lastSeenAt`, `occurrenceCount++`) or
creates a fresh one. This prevents the accumulation of duplicate ACTIVE rows for persistent faults.

#### `realtime_telemetry_simulator.py` — responder daemon launcher

- Lines 35–100: `_spawn_uds_responder()` runs at module import time. Reads
  `UDS_DTC_MAP` from env; if non-empty and not `{}`/`null`, spawns
  `uds_dtc_responder.py` as a `subprocess.Popen` with `start_new_session=True`.
- `_kill_uds_responder()` is registered via `atexit` — SIGTERM with 3-second
  timeout, falls back to SIGKILL.
- This means **the UDS_DTC_MAP is set ONCE per Fargate task at boot.** The map
  is immutable for the lifetime of the responder process. Per-trip-during-task
  DTC mutation is NOT supported in the current architecture.

#### `uds_dtc_responder.py` — UDS Service 0x19 server

- One responder process per fwe-simulator task. Spawns one `_ECUThread` per
  configured ECU; each owns an `isotp.NotifierBasedCanStack` filtered on its
  `(req_id, resp_id)` pair. They share the same socketcan bus.
- Supports UDS sub-functions:
  - `0x19 0x01` reportNumberOfDTCByStatusMask
  - `0x19 0x02` reportDTCByStatusMask (the one FWE actually fires)
  - `0x19 0x06` reportDTCExtDataRecordByDTCNumber (minimal stub)
- Status byte default: `_DEFAULT_DTC_STATUS = 0x09` (testFailed | confirmedDTC).
- DTC encoding per ISO 14229-1 Annex D / SAE J2012: 3-byte ISO form, e.g.
  `C1234` → `52 34 00`, `P0520` → `05 20 00`. See `encode_dtc()` (line 89).

### B. FWE side (out of repo)

- The fwe-agent process runs in a separate ECS task. It receives the ephemeral
  campaign via CampaignSyncProcessor and starts polling UDS at the configured
  cadence (30s for our demo).
- `ExampleUDSInterface.findTargetAddress` resolves the `params[0]` ECU# to the
  CAN ID it should query — must agree with `_ECU_BY_NUMBER` above.
- Polling cadence implication: **the first DTC surfaces ~30s into a trip**,
  with the second poll at ~60s, etc. For short demo trips, this latency budget
  matters — see [FWE polling cadence](#fwe-polling-cadence) below.
- FWE wraps the response in a STRING-typed CapturedSignal named
  `Vehicle.ECU{n}.DTC_INFO` with envelope:
  ```json
  {"DetectedDTCs":[{"DTCAndSnapshot":{"dtcCodes":[{"DTC":"<6-hex>","status":9}, ...]}}]}
  ```
  Note the DTC is in 6-character hex form (the binary ISO encoding rendered as
  hex), not the human SAE form. `523400` is "C1234".

### C. Flink side (Java, `modules/flink/src/main/java/com/cms/telemetry/`)

#### `FWTelemetryProcessor.handleUdsDtcInfo` — current FWE-UDS terminus

- Trigger: any signal name ending in `.DTC_INFO` is routed to `handleUdsDtcInfo`
  instead of the numeric value path (line 208).
- `decodeDtcFromHex(rawDtc)` reverses the responder's encoding:
  hex → SAE form, e.g. `523400` → `C1234`. (Line ~330.)
- `loadDtcSeverity(eventCatalogTable, region)` scans `cms-{stage}-event-catalog`
  for items with `dtc_code` set, projects `dtc_code, severity_hint`, builds an
  in-memory cache `{dtc_code → severity_hint→DDB-vocab}`. Cached for processor
  lifetime (~19 entries). Maps `P0/P1/P2/P3` → `CRITICAL/HIGH/MEDIUM/LOW`.
- `storeUdsDtc(...)` writes ONE row to `cms-{stage}-storage-dtc-history` with
  `source="fwe-uds-dtc"`. Dedupes by `(vehicleId, code)` per processor lifetime.
- **Does NOT write to `maintenance-alerts`.** This is the critical gap the spec
  must close.

#### `MaintenanceProcessor` — current maintenance-alerts producer

- Reads decoded telemetry from upstream Kafka topic.
- Writes `cms-{stage}-storage-maintenance-alerts` rows through several entry
  points; the relevant ones for fault events are:
  - **Threshold path** (line ~210+): reads scalar `dtc_codes_active` from
    telemetry; if `==1`, emits a maintenance alert. The sim injects this scalar
    randomly, NOT keyed to selected scenarios. This is what the
    `2026-06-15-safety-scenario-selection-undercount` issue documents for the
    selection-undercount problem.
  - **OEM1 canonical indicator path** (`handleCanonicalIndicatorEvent`,
    line ~470+): reads `indicator_state, dtc_clear, dtc_code` from telemetry,
    derives sub-state (ACTIVE / ACTIVE_NO_DTC / CLEARED /
    DTC_CLEARED_INDICATOR_ACTIVE), writes maintenance-alerts. **OEM1-only;
    do not modify per spec constraint "no OEM1 DTC pipeline regression".**
  - **`storeActiveDtc` path** (line ~868): writes a sibling `dtc-history` row
    with `source="flink-maintenance-processor"` when a maintenance alert is
    materialized via the threshold path. This is the "Path 2" in `FWE_UDS_DTC.md`.
- **`MaintenanceProcessor` does NOT currently consume DTC_INFO STRING signals**
  (those are handled in FWTelemetryProcessor). It does not have a
  `dtc_code → event_id` reverse-lookup capability anywhere in its code.

## Catalog state — fault events with `dtc_code`

Source: `deployment/scripts/seed_event_catalog.py` (~32 maintenance/safety
events with non-empty `dtc_code`). Subset:

| event_id | dtc_code | ECU | severity_hint | covered by `_ECU_BY_CODE`? |
|---|---|---|---|---|
| safety.crash | U3000_CRASH | ECU5 | P0 | yes |
| maintenance.brake_system_fault | C1234 | ECU1 | P1 | yes |
| maintenance.brake_pads_worn (?) | C1213 | ECU1 | (varies) | yes |
| maintenance.engine_misfire / variants | P0300 | ECU2 | P1 | yes |
| maintenance.coolant_critical_overheat | P0217 | ECU2 | P0 | yes |
| maintenance.fuel_system | P0219 | ECU2 | P1 | yes |
| maintenance.coolant_temp_sensor | P0118 | ECU2 | P2 | yes |
| maintenance.lambda_sensor (cat eff) | P0420 | ECU2 | P1 | yes |
| maintenance.cam_pos_sensor | P0340 | ECU2 | P1 | yes |
| maintenance.lean_mixture | P0171 | ECU2 | P2 | yes |
| maintenance.evap_emissions | P0461 | ECU2 | P2 | yes |
| maintenance.low_oil_pressure | P0520 | ECU2 | P0 | yes |
| maintenance.transmission_failure | P0700 | ECU3 | P0 | yes |
| maintenance.pcm_processor_fault | P0606 | ECU4 | P0 | yes |
| maintenance.canbus_loss / variants | U0100, U0401 | ECU5 | P1 | yes |
| safety.crash (fire variant) | B1000 | ECU9 | P0 | yes |
| (fire variant 2) | B0001_FIRE | ECU9 | P0 | yes |
| maintenance.hv_battery_critical | P0A80 | ECU6 | P0 | yes |
| maintenance.12v_battery_low | P0562 | ECU7 | P2 | yes |
| maintenance.evap_leak | P0442 | ECU8 | P2 | yes |
| maintenance.tire_pressure_critical | C0710 | ECU1 | P1 | yes |
| maintenance.abs_module_fault | C1241 | ECU1 | P1 | yes |
| maintenance.brake_master_low | C1201 | ECU1 | P1 | yes |
| maintenance.steering_torque | C0035 / C0040 | ECU1 | P1 | yes |
| maintenance.air_intake_clog | P0299 | ECU2 | P2 | yes |
| (more in seed_event_catalog.py:888-1015 — some not in `_ECU_BY_CODE` yet, e.g. C0091, P0001, P205B, P0607, P0620, P0093, B0001, C0460, B1004) | | | | **PARTIAL** — these would need entries added to `_ECU_BY_CODE` to surface via FWE-UDS path |

### Out-of-DTC-path events

Catalog wear/level items intentionally have no `dtc_code` (they are not
diagnostic faults — they're threshold-tracked degradations):

- `maintenance.filter_replacement` (cabin/oil/air filter)
- `maintenance.low_battery` (12V soft warning, distinct from `12v_battery_low` P0562)
- `maintenance.oil_life_low`
- `maintenance.tire_tread_low`
- `maintenance.washer_fluid_low`

These ride the catalog/threshold path (covered by the
`2026-06-15-cms-event-signal-contract-alignment` spec). They are out of scope
for THIS spec.

## FWE polling cadence

Per `simulation_lambda._build_uds_dtc_map` (line 348):

```python
"executionFrequencyMs": 30_000,
"maxExecutionCount": 0,  # 0 = unbounded
```

→ FWE polls each ECU every 30 seconds, indefinitely until campaign stops.

**Demo timing implications:**

- A short trip (≤60s wall-clock) may see 1–2 polls per ECU, so a selected fault
  may surface but not all 9 ECU's worth.
- A trip ≤30s could end before the first poll fires — DTC may never surface.
- The 30s cadence is hardcoded; making it configurable would be a separate
  enhancement (not in this spec).

For the demo's typical 60–300s trips, 30s is acceptable. Document expectation:
**first DTC surfaces ~30s after trip start**, not instantly.

## Lifecycle — DTC clear-on-stop

| Event | Effect on DTCs |
|---|---|
| Fargate task starts | `_spawn_uds_responder()` reads UDS_DTC_MAP, starts responder with the active set fixed for the task. |
| Trip ends, task continues | (Not supported — current architecture has 1 trip = 1 task.) |
| Trip ends, task ends | `atexit` → `_kill_uds_responder()` → responder dies. CAN bus stops answering 0x19 → FWE polling sees no responses (NRC or timeout). DTCs effectively cleared. |
| Manual stop_simulation API call | `simulation_lambda` does ECS StopTask; same atexit path. |
| New trip, new task | Fresh `UDS_DTC_MAP`, fresh responder, no carryover. |

**No "stuck DTCs across trips" risk** in current architecture, because the
responder dies with the task. Spec's stuck-DTCs risk is only relevant if we
later go to a long-lived responder + control-channel architecture (e.g.,
multiple sequential trips on one shared sim task). Not needed for the demo.

The `dtc-history` table accumulates rows historically — it's the audit
log, not a "currently active" surface. `maintenance-alerts` is the "currently
active" surface; **whatever fix we build for the gap must scope alert
materialization to the current trip** so a trip's selection doesn't pollute the
next trip's alert list. Trip-id association (already shipped) is the natural
bound.

## Open follow-ups (not closed by this spec, surfaced for tracking)

- **Ephemeral campaign cleanup at trip stop.** `_ensure_uds_campaign` writes
  `simulationId`; no code currently reads it on `_stop`. Stale rows accumulate
  in `cms-{stage}-campaigns`. Low priority; may bundle into the spec or file
  as a P3.
- **`_ECU_BY_CODE` expansion.** Several catalog events (~8 codes from
  `seed_event_catalog.py:888-1015`) lack ECU mapping. Today they would be
  silently skipped by `_build_uds_dtc_map` with a `⚠️` log. If those events
  are ever to be selectable in FWE mode, extend `_ECU_BY_CODE` (and possibly
  `_ECU_BY_NUMBER` if a new ECU is needed).

## Implementation reference (2026-06-16)

The maintenance-alerts mapping gap has been closed via spec `2026-06-16-cms-fault-event-uds-dtc-fwe-routing`. The implementation follows Option B (user-approved): MaintenanceProcessor owns the `maintenance-alerts` write.

### Data flow (post-implementation)

```
FWE telemetry → FWTelemetryProcessor.decodeFWTelemetry
                  ├── regular telemetry JSON → cms-telemetry-preprocessed
                  └── per DTC: synthetic "uds_dtc" JSON
                      (vehicleId, vin, timestamp, tripId, source="fleetwise",
                       record_kind="uds_dtc", dtc_code, system, signal_name, campaignSyncId)
                                                           ↓
                        EventDrivenTelemetryProcessor (fans all to domain topics)
                                                           ↓
                              cms-telemetry-maintenance topic
                                                           ↓
                    MaintenanceProcessor.MaintenanceHandler
                      ├── handleUdsDtcEvent(json):
                      │   1. Extract dtc_code, vehicleId, vin, timestamp, system, signal_name
                      │   2. Resolve tripId (prefer JSON; fallback resolveActiveTrip)
                      │   3. Dedup via GSI Query: `active-code-index` (vehicleId, activeCode) returns existing ACTIVE row if present
                      │   4. Load dtc_code→event_id from event-catalog cache (new loadDtcCodeToEventId)
                      │   5. Load severity from catalog severity_hint (new loadDtcSeverityForUds)
                      │   6. Write maintenance-alerts row: schema = existing storeAlert pattern, source="fwe-uds-dtc"
                      │   7. Upsert dtc-history row via `upsertActiveDtc`: one ACTIVE row per (vehicleId, code, source), schema = existing + tripId, source="fwe-uds-dtc"
                      │   8. If severity ∈ {CRITICAL, HIGH}: emit vfo-action-queue pending-action row
                      │
                      └── [existing threshold + OEM1 paths unchanged]
```

### Key components

**`FWTelemetryProcessor.java`**
- Refactored `decodeFWTelemetry` return type: `String` → `List<String>` (one telemetry record + N uds_dtc records per input)
- `parseDtcInfoToEvents` helper parses `DTC_INFO` envelope, decodes hex→SAE per entry, returns synthetic uds_dtc JSON records
- Removed: `storeUdsDtc`, `UDS_DTC_DEDUP`, `DTC_SEVERITY_CACHE`, `loadDtcSeverity` (moved to MaintenanceProcessor)

**`MaintenanceProcessor.java`**
- New `handleUdsDtcEvent(json)` method handles the uds_dtc record-kind at top of MaintenanceHandler
- New `loadDtcCodeToEventId(eventCatalogTable, region)` cache (double-checked lock, transient-failure retry via cache-miss semantics)
- New `loadDtcSeverityForUds(eventCatalogTable, region)` cache (ported from FWTelemetryProcessor, same retry semantics)
- New `upsertActiveDtc(...)` dedup helper: queries `active-code-index` GSI (one ACTIVE row per vehicleId|code|source); UpdateItem on hit or PutItem on GSI miss. Replaces pre-v0.2.7 `udsDtcKeys` in-memory dedup set.
- Dedup-add placed AFTER catalog lookup (if lookup fails, dedup remains untouched for retry on next polling cycle)

**`TelemetryDataProcessor.java` + `TripProcessor.java`**
- New early-return filter: skip uds_dtc records from landing in telemetry-table and trip-telemetryCount

**`MaintenanceProcessorUdsDtcTest.java` (new test class)**
- 8+ test cases: alert+history write, dedup within trip, no dedup across trips, unknown code skip, tripId fallback, pending-action gating, cache-retry after transient failure

### Schema notes

- `maintenance-alerts` rows from FWE-UDS path: `alertType=event_id` (unified namespace with `dtcCode` field), `source="fwe-uds-dtc"`, `tripId` (associated), severity from catalog
- `dtc-history` rows: `source="fwe-uds-dtc"`, `tripId` (new field added via refactor)
- `vfo-action-queue` rows: emitted for CRITICAL/HIGH severity DTCs, tagged `sourceTag="dtc-fwe-uds"`; `dtcId` matches the corresponding dtc-history row

## The actual gap — Stage 12 (`dtc_code → event_id → maintenance-alerts`) [RESOLVED]

There is currently no Java code that, on receiving a DTC_INFO with
`dtc_code=X`, looks up the catalog event whose `dtc_code=X`, and writes a
maintenance-alert row keyed by that `event_id` (with tripId, severity,
timestamp, vehicleId).

Two design options:

### Option A: Extend `FWTelemetryProcessor.handleUdsDtcInfo`

Add a second write inside `handleUdsDtcInfo` after `storeUdsDtc`. Use a new
helper `loadDtcCodeToEventId(eventCatalogTable, region)` (mirrors
`loadDtcSeverity`) to maintain a `{dtc_code → event_id}` reverse-lookup cache.
Resolve `tripId` via the same path the recently-shipped tripId-association
fix uses. Write a `maintenance-alerts` row with `source="fwe-uds-dtc"`.

- **Pro**: minimal code surface; reuses the JSON parse + decoded SAE code +
  catalog scan that's already in this method. Single processor write.
- **Pro**: matches the existing `storeUdsDtc` pattern of FWTelemetryProcessor
  writing directly to a downstream DDB table.
- **Con**: violates a "MaintenanceProcessor owns maintenance-alerts" purity
  argument. (Counter: that purity is already breached — this is the same
  processor that writes dtc-history, so we're not introducing a new
  cross-cut.)
- **Con**: needs the tripId resolver to be invocable from FWTelemetryProcessor
  context (verify accessibility — likely a static helper module).

### Option B: Have MaintenanceProcessor consume DTC_INFO

Route DTC_INFO records to `MaintenanceProcessor` (either via the existing
upstream Kafka topic if it carries them, or a new shape). Add a handler that
does the reverse-lookup and writes maintenance-alerts; the dtc-history write
stays in FWTelemetryProcessor.

- **Pro**: cleaner separation — MaintenanceProcessor owns maintenance-alerts.
- **Con**: requires reshaping the FW→MP message flow OR a new Kafka topic,
  which is invasive. New schema, new test surface, possible coordination with
  the active `2026-06-15-cms-event-signal-contract-alignment` Java work.
- **Con**: bigger blast radius; higher chance of regression in the OEM1
  path.

**Recommendation: Option A.** Lower risk, smaller code change, sticks to the
processor that already understands the DTC_INFO envelope. Decision pending
user/architect concurrence in `decisions.md`.

## TripId association

Per the shipped `2026-06-15-cms-safety-maintenance-event-tripid-association`
fix (commit `66eee6c` + `fa0d73d`), there is a tripId resolver used by the
maintenance/safety event paths. Whatever Option A/B implementation lands MUST
attach tripId to the new maintenance-alerts row using that resolver — the
spec.md explicitly requires this.

The `dtc-history` rows written by `storeUdsDtc` today **do NOT carry
tripId** (look at `storeUdsDtc` in FWTelemetryProcessor: vehicleId, timestamp,
dtcId, code, status, severity, system, description, signalName,
campaignSyncId — no tripId). For consistency, consider whether to also add
tripId to dtc-history when we do Option A. Probably yes — but that's a
secondary touch and can be flagged as scope-decision in `decisions.md`.

## Sim-mode interaction (FWE vs MQTT-direct)

| Sim mode | Catalog-driven (MQTT-direct) | UDS-DTC (FWE) |
|---|---|---|
| FWE | not applicable (FWE doesn't run MQTT-direct) | THIS SPEC's path; produces dtc-history (today) + maintenance-alerts (gap) |
| MQTT-direct | catalog/threshold path produces maintenance-alerts directly | not applicable (no FWE agent, no UDS responder) |

A given scenario fires via exactly one path per trip — the path determined by
`SIM_MODE` env var / sim-task selection. There is no double-emission risk
between FWE-UDS and MQTT-direct because the trip never runs both modes
simultaneously.

(De-duplication WITHIN FWE mode — between the FWE-UDS path and the
threshold-scalar `dtc_codes_active` path — is a separate concern. The 2026-06-15
issue notes the threshold path fires randomly with `random.random() < 0.02`;
arguably that injection should be suppressed when the user has explicitly
selected fault scenarios that already produce DTCs via the UDS path. Track as
a Group 3 design item if we keep that group; otherwise a follow-up row.)

## Verification — how the doc was built

| Source | Lines / paths read |
|---|---|
| `services/simulation/uds_dtc_responder.py` | full file |
| `services/simulation/lambda/simulation_lambda.py` | 220–410 (CP8 helpers), 645–770 (CP8 wiring) |
| `services/simulation/realtime_telemetry_simulator.py` | 30–145 (responder launcher), 925–940 (`dtc_codes_active` scalar) |
| `modules/flink/src/main/java/com/cms/telemetry/FWTelemetryProcessor.java` | 240–460 (UDS-DTC handling: `handleUdsDtcInfo`, `decodeDtcFromHex`, `loadDtcSeverity`, `storeUdsDtc`) |
| `modules/flink/src/main/java/com/cms/telemetry/MaintenanceProcessor.java` | 40–155 (table-name derivation), 200–230 (threshold dtc_codes_active), 470–530 (canonical indicator path), 850–960 (storeActiveDtc) |
| `deployment/scripts/seed_event_catalog.py` | grep for `dtc_code` — 32 entries |
| `issues/2026-06-15-safety-scenario-selection-undercount/report.md` | full file (5-layer contract failure mode for selection-driven safety events) |
| Existing docs | `docs/FWE_UDS_DTC.md` (two-path model), `docs/OEM1_DTC_PIPELINE.md` (live OEM1) |

## Cross-references

- `docs/FWE_UDS_DTC.md` — existing two-path model into dtc-history (Path 1:
  `source=fwe-uds-dtc`; Path 2: `source=flink-maintenance-processor`).
- `docs/OEM1_DTC_PIPELINE.md` — live OEM1 canonical DTC pipeline (do NOT modify).
- `docs/event-signal-contract.md` — CAN-backed signal alignment for catalog events.
- `issues/2026-06-15-safety-scenario-selection-undercount/report.md` — sister
  selection-undercount problem on the safety-event side; same
  selection-doesn't-fire family of bug, different mechanism.
- Spec `2026-06-15-cms-event-signal-contract-alignment` — owns
  `services/simulation/can_encoder.py`, `realtime_telemetry_simulator.py`
  (already-checked tasks), and is mid-flight on Java SafetyProcessor /
  MaintenanceProcessor rule-field reads — coordinate before any Java edits in
  THIS spec to avoid a working-tree race.
