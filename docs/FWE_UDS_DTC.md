# FWE UDS-DTC — Two Paths to `cms-<stage>-storage-dtc-history`

> **Audience:** engineers debugging DTC rows, adding new DTC codes, or
> porting this setup to a fresh environment. Read the **Quick
> reference** table first, then jump to the path you care about.

## TL;DR

The CMS platform has **two independent paths** that write rows
to `cms-<stage>-storage-dtc-history`. Both coexist; both are expected
to fire for realistic maintenance scenarios. Rows are distinguished
by the `source` attribute:

| `source` value                   | Path                                          | When it fires                                                                           | Latency                                 |
| -------------------------------- | --------------------------------------------- | --------------------------------------------------------------------------------------- | --------------------------------------- |
| `fwe-uds-dtc`                    | **Authentic** — FWE → UDS 0x19 → protobuf     | FWE agent fires DTC_QUERY every 30s while a UDS campaign is active for the vehicle      | ~30s after sim starts, then every 30s   |
| `flink-maintenance-processor`    | **Threshold-based** — signal rule match       | Simulator emits a signal value that crosses a threshold in the event catalog            | ~1-10s after threshold breach           |
| `force_event.py`                 | **Manual** — ops tool                         | Operator runs `deployment/scripts/force_event.py` with `--emit-dtc`                     | Immediate                               |
| _(missing)_                      | **Legacy** — historical seed data             | From `historical_data_injector.py` (deprecated) or older Flink writes pre-dating the    | One-time, at env seed                   |
|                                  |                                               | `source` attribute                                                                      |                                         |

Downstream consumers (VFO triage classifier, operator UI, maintenance
alerts dashboard) read **all** rows regardless of `source`. The tag
exists for debugging/provenance only — "where did this specific row
come from?" — and for audit when a DTC appears in the UI.

**Path 1 unified output (as of 2026-06-16):** Both `maintenance-alerts` and `dtc-history` rows are produced from the FWE-UDS path with `source="fwe-uds-dtc"`. The `maintenance-alerts` row carries `alertType=eventId` (e.g., `maintenance.brake_system_fault`), `eventId`, and the `dtcCode` field for reference. This provides a unified event namespace across both paths (Path 1 FWE-UDS and Path 2 threshold-based).

---

## Path 1 — Authentic UDS-DTC (`source=fwe-uds-dtc`)

This path exercises the full AWS IoT FleetWise Edge (FWE) UDS-DTC
pipeline, end-to-end: real ISO 14229-1 0x19 PDUs on a virtual CAN
bus, answered by a Python responder, shipped via MQTT/Kafka/Flink
to DynamoDB.

### Flow diagram

```text
┌────────────────────────────────────────────────────────────────────┐
│ Trip Simulator UI (React)                                          │
│   maintenance.brake_system_fault   +   Source=FWE Agent            │
│                            │                                       │
│                            ▼  POST to /simulation/start            │
│ ┌───────────────────────────────────────────────────────────────┐  │
│ │ cms-<stage>-simulation-api Lambda                              │ │
│ │  _start() → _build_uds_dtc_map() reads event catalog dtc_codes │ │
│ │         → _ensure_uds_campaign() writes:                       │ │
│ │           cms-<stage>-campaigns: "uds-dtc-<vin[:12]>-<sim_id>" │ │
│ │           with signalsToFetch=[{901, DTC_QUERY, [1,2,-1]}]     │ │
│ │         → ECS RunTask cms-<stage>-fwe-simulator                │ │
│ │           env: UDS_DTC_MAP={"ECU1":["C1234"]}                  │ │
│ └───────────────────────────────────────────────────────────────┘  │
│                            │                                       │
│                            ▼                                       │
│ ┌───────────────────────────────────────────────────────────────┐  │
│ │ ECS cluster cms-<stage>-simulation  (ASG with can-isotp.ko)   │ │
│ │                                                                │ │
│ │  ┌──────────────────────┐      ┌────────────────────────────┐  │
│ │  │ cms-<stage>-fwe-     │      │ cms-<stage>-fwe-agent task │ │
│ │  │ simulator task       │      │ (persistent, 1 task/fleet) │ │
│ │  │                      │      │                            │ │
│ │  │ realtime_telemetry_  │      │ /usr/bin/aws-iot-          │ │
│ │  │ simulator.py         │      │ fleetwise-edge --with-     │ │
│ │  │ atexit-spawns:       │      │ uds-dtc-example            │ │
│ │  │ uds_dtc_responder.py │      │                            │ │
│ │  │                      │      │  exampleUDSInterface: 9    │ │
│ │  │ python-isotp server  │      │  ECUs, CAN IDs 0x7E0-0x7E7 │ │
│ │  │ on vcan0/vcan1       │      │  + 0x18DA09F1 for ECU9     │ │
│ │  │                      │      │                            │ │
│ │  │ Listens 0x7E0 (ECU1) │◄─────│  DTC_QUERY fires every 30s │ │
│ │  │ answers 0x7E8        │─────►│  on CAN ID 0x7E0:          │ │
│ │  │  59 02 FF 52 34 00 09│      │  "19 02 08" (readDTCByMask,│ │
│ │  │                      │      │  confirmed|pending|current)│ │
│ │  └──────────────────────┘      └────────────┬───────────────┘  │
│ └─────────────────────────────────────────────┼──────────────────┘
│                                               │ MQTT publish to   │
│                                               ▼ cms/fleetwise/    │
│                                                 vehicles/<vin>/   │
│                                                 signals           │
│ ┌───────────────────────────────────────────────────────────────┐  │
│ │ AWS IoT Core                                                   │ │
│ │  Topic rule fw_prod_iot_msk_rule:                              │ │
│ │    SELECT encode(*, 'base64') AS data,                         │ │
│ │           topic(4)           AS vehicleId,                     │ │
│ │           timestamp()        AS ts                             │ │
│ │    FROM 'cms/fleetwise/vehicles/+/signals'                     │ │
│ │  Kafka action → fw-telemetry-raw                               │ │
│ └──────────────────────────────┬────────────────────────────────┘  │
│                                │                                   │
│ ┌──────────────────────────────▼────────────────────────────────┐  │
│ │ MSK cluster: topic fw-telemetry-raw                            │ │
│ │  Payload = base64-encoded protobuf VehicleData:                │ │
│ │   campaign_sync_id = "uds-dtc-<vin[:12]>-<sim_id>"             │ │
│ │   decoder_sync_id  = "cms-fleet-v3"                            │ │
│ │   captured_signals = [{signal_id:901, string_value:"<JSON>"}]  │ │
│ └──────────────────────────────┬────────────────────────────────┘  │
│                                │                                   │
│ ┌──────────────────────────────▼────────────────────────────────┐  │
│ │ Kinesis Data Analytics for Apache Flink                        │ │
│ │  cms-<stage>-flink-fw-telemetry-processor                      │ │
│ │   ↓ FWTelemetryProcessor.java                                  │ │
│ │   1. decode protobuf (base64→bytes→VehicleData)                │ │
│ │   2. lookup signalNames[901] → "Vehicle.ECU1.DTC_INFO"         │ │
│ │      (from cms-<stage>-decoder-manifest DDB)                   │ │
│ │   3. case STRING_VALUE + name.endsWith(".DTC_INFO"):           │ │
│ │      → handleUdsDtcInfo(raw JSON)                              │ │
│ │   4. parse DetectedDTCs → for each dtc:                        │ │
│ │      → decodeDtcFromHex("523400") → "C1234"                    │ │
│ │      → severity from DTC_SEVERITY_CACHE (event catalog)        │ │
│ │   5. dedup by (vehicleId, code), then storeUdsDtc()            │ │
│ └──────────────────────────────┬────────────────────────────────┘  │
│                                │                                   │
│ ┌──────────────────────────────▼────────────────────────────────┐  │
│ │ DDB cms-<stage>-storage-dtc-history                            │ │
│ │   { vehicleId: VEH-0025, timestamp: ...,                       │ │
│ │     code: "C1234", severity: "CRITICAL",                       │ │
│ │     source: "fwe-uds-dtc",                                     │ │
│ │     triggerEventId: "uds-dtc-1HGBH41JXMN0-5d298e0b", ... }     │ │
│ └───────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
```

### Components owned by Path 1

| Component                                                                                 | Owns                                                                                    |
| ----------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `services/simulation/Dockerfile.fwe`                                                      | FWE agent binary build with `--with-uds-dtc-example` + 2 sed patches                    |
| `services/simulation/uds_dtc_responder.py`                                                | Python UDS responder (python-can + python-isotp). 9-ECU support.                        |
| `services/simulation/realtime_telemetry_simulator.py`                                     | Spawns the responder as a subprocess at module-load time                                |
| `services/simulation/lambda/simulation_lambda.py`                                         | Builds `UDS_DTC_MAP`, creates ephemeral campaign, launches tasks                        |
| `deployment/stacks/simulation_stack.py`                                                   | JQ transform injects `exampleUDSInterface` into FWE static config; ASG builds can-isotp |
| `deployment/scripts/generate_decoder_manifest.py`                                         | Regenerates `DecoderManifest.bin` from DDB `cms-fleet-v3` (262 CAN + 9 UDS signals). Run with `DRY_RUN=1` to validate, then commit the `.bin` to `deployment/fwe-config/` for stack-managed deploy. |
| `deployment/stacks/flink_stack.py`                                                        | CDK `BucketDeployment` uploads `deployment/fwe-config/DecoderManifest.bin` to `fwe-config/` prefix in the app's jar bucket on every deploy. **This is the durable source of truth** (replaced out-of-band manual uploads). |
| `deployment/scripts/signal_catalog_seed.json`                                             | 9 `diagnostics` group entries for the signal catalog                                    |
| `modules/flink/.../CampaignSyncProcessor.java`                                            | Reads `signalsToFetch` from DDB, emits `FetchInformation` protobuf to FWE               |
| `modules/flink/.../FWTelemetryProcessor.java`                                             | STRING signal handling, DTC hex→J2012 decode, emits uds_dtc synthetic records           |
| `modules/flink/.../MaintenanceProcessor.java`                                             | Consumes uds_dtc events, reverse-looks up event_id, writes `maintenance-alerts` + `dtc-history` rows with source="fwe-uds-dtc" |

### UPSERT model (v0.2.7+)

As of v0.2.7, processor-sourced DTC rows (all source tags) follow a dedup pattern: **one ACTIVE row per
`(vehicleId, code)`**. On each detection, the processor queries the `active-code-index` sparse GSI
keyed `(vehicleId, activeCode)` to find an existing ACTIVE row:
- **Found**: UpdateItem on the existing row, incrementing `lastSeenAt = now`, `occurrenceCount++`
- **Not found**: PutItem a fresh row with `firstSeenAt = lastSeenAt = now`, `occurrenceCount = 1`

Sparse GSI semantics: items without `activeCode` (CLEARED rows, legacy rows) are not indexed.
Clear operations REMOVE the `activeCode` attribute so the row drops out of the GSI.

See `modules/flink/README.md` § "DTC dedup" for the full model and backfill runbook
(`docs/runbooks/dtc-dedup-backfill.md`).

### Five places that all must agree

Any mismatch between these causes silent data loss on Path 1:

1. **Signal IDs 901-909.** Hardcoded in `generate_decoder_manifest.py`
   (`UDS_SIGNAL_IDS`), `signal_catalog_seed.json` (`signal_id`
   column), `simulation_lambda.py` (`_ECU_SIGNAL_ID`). Must match.
2. **Fully-qualified names `Vehicle.ECU1.DTC_INFO`..`Vehicle.ECU9.DTC_INFO`.**
   - Referenced in `DecoderManifest.bin`
     (`CustomDecodingSignal.custom_decoding_id` AND
     `CustomDecodingSignal.name`).
   - Mirrored in `cms-<stage>-decoder-manifest` DDB (so the Flink app
     can resolve signal_id → FQN).
   - Baked into the FWE binary's `mSignalNames` compile-time constant
     (via `Dockerfile.fwe` sed patch).
3. **ECU CAN IDs.** Request/response pairs 0x7E0/0x7E8 through
     0x7E7/0x7EF for ECU1-8, extended 0x18DA09F1/0x18DAF109 for ECU9.
     Declared in `simulation_stack.py` JQ block (FWE-side) and
     `simulation_lambda.py`'s `_ECU_BY_NUMBER` (responder-side).
4. **DTC code hex encoding** (ISO 14229-1 Annex D).
   `uds_dtc_responder.py::encode_dtc` and
   `FWTelemetryProcessor::decodeDtcFromHex` are inverses. Changes to
   one must mirror the other.
5. **`source="fwe-uds-dtc"`** tag on DDB rows. Any future Path in the
   same codebase must pick a different tag to stay disambiguable.

### Ephemeral campaign pattern (Trip Simulator)

Each sim start creates a new row in `cms-<stage>-campaigns`:

- `campaignId = "uds-dtc-<vin[:12]>-<sim_id>"`
- `targetArn = "vehicle:<vin>"`
- `status = "RUNNING"`
- `simulationId = <sim_id>` (tag for future cleanup)
- `signalsToFetch = [{signalId: 90N, functionName: "DTC_QUERY", params: [ecu_num, 2, -1], executionFrequencyMs: 30000}]`

This deliberately does NOT modify any user-managed telemetry campaign.
`CampaignSyncProcessor` picks it up on its next tick and publishes the
collection scheme to the FWE agent. On sim stop, the campaign row is
currently not cleaned up — left behind for debugging.

### Reusable template (`uds-dtc-polling`)

Operators who want UDS-DTC polling on a vehicle *outside* of a Trip
Simulator run can use the reusable template seeded by
`deployment/scripts/seed_uds_dtc_template.py`. This creates a row with
`campaignId="uds-dtc-polling"`, `targetArn="template"` in the
`cms-<stage>-campaigns` table. The template carries the same 9
`signalsToFetch` entries as the ephemeral pattern above, but for all 9
ECUs (not just the ones the simulator expects to fault).

**From the UI:**

1. Navigate to **Vehicles → &lt;vehicle&gt; → Campaigns** tab
2. Click **Assign Campaign**
3. The **Campaign template** dropdown now includes `uds-dtc-polling`
4. Select it → Assign → a new row `uds-dtc-polling-<vin>` is written to
   `cms-<stage>-campaigns` with `targetArn="vehicle:<vin>"`,
   `status="RUNNING"`, and the full `signalsToFetch` copied from the
   template.
5. `CampaignSyncProcessor` delivers it to the vehicle's FWE agent on
   its next sync tick. FWE starts firing `DTC_QUERY` on all 9 ECUs
   every 30s.

**Prerequisite:** the vehicle must have a running FWE agent with the
`exampleUDSInterface` static config and a UDS responder reachable on
its vcan bus. In the demo environment this means the vehicle's
`fwe-agent` ECS task + its matching `fwe-simulator` task (which spawns
`uds_dtc_responder.py`) must both be running. Assigning the template
to a vehicle with no FWE agent is a no-op — the campaign sits in
`RUNNING` state but no CAN traffic is ever generated.

**To seed into a fresh environment:**

```bash
DEPLOYMENT_STAGE=prod AWS_REGION=us-east-1 AWS_PROFILE=default \
    python3 deployment/scripts/seed_uds_dtc_template.py
```

Idempotent — re-running overwrites the existing template with the
current config, so it's safe to bake into a CI seeding pipeline.

**Template vs ephemeral — when each fires:**

| Scenario                                 | Ephemeral (sim)                           | Template (`uds-dtc-polling`)                  |
| ---------------------------------------- | ----------------------------------------- | --------------------------------------------- |
| Running a Trip Simulator scenario        | Created automatically on sim start        | Not used                                      |
| Enabling UDS-DTC on a long-lived vehicle | Not used                                  | Assigned manually (or via seed)               |
| Fleet-wide UDS-DTC policy                | N/A                                       | Assigned to all vehicles via fleet assignment |
| Signals fetched                          | Only ECUs relevant to the chosen scenario | All 9 ECUs                                    |
| Cleanup                                  | Left behind after sim stops (followup)    | Stays until operator unassigns                |

Both paths produce identical `source=fwe-uds-dtc` rows in
`cms-<stage>-storage-dtc-history`; consumers don't distinguish.

---

## Path 2 — Threshold-based (`source=flink-maintenance-processor`)

This is the original DTC-creation path. It fires when a normal signal
value (speed, coolant_temp, oil_pressure, etc.) crosses a threshold
defined in the event catalog.

### Flow diagram

```text
Simulator emits signal (e.g., coolant_temp=105°C)
  ↓ protobuf → MQTT → IoT rule → Kafka fw-telemetry-raw
  ↓
FWTelemetryProcessor decodes, re-emits on cms-telemetry-preprocessed
  ↓
MaintenanceProcessor consumes cms-telemetry-preprocessed
  ↓ queries event catalog for matching rule (signal+threshold+duration)
  ↓ on match, builds MaintenanceAlert
  ↓
  1. Writes maintenance-alerts row    (primary business output)
  2. Writes dtc-history row           (derived, for VFO triage)
     → source="flink-maintenance-processor"
     → triggerEventId=<maintenance event_id>
```

### Key differences vs Path 1

| Aspect               | Path 1 (UDS)                          | Path 2 (threshold)                     |
| -------------------- | ------------------------------------- | -------------------------------------- |
| Trigger              | FWE DTC_QUERY timer (30s)             | Signal crosses threshold               |
| DTC code source      | Configured in `UDS_DTC_MAP` → responder → FWE | Event catalog `dtc_code` column |
| Authentic?           | Yes — real 0x19 PDUs on CAN           | No — skips CAN, derives from signals   |
| Requires FWE agent?  | Yes                                   | No — any data source works             |
| Latency              | 0-30s (next tick)                     | 1-10s (Flink checkpoint cadence)       |
| severity source      | Event catalog `severity_hint`         | Event catalog `severity_hint`          |

Both paths end up querying `cms-<stage>-event-catalog` for severity,
so a DTC code's severity level is consistent regardless of which path
wrote the row.

---

## Troubleshooting

### "DTC didn't appear in the UI after I started a sim"

1. **Check the CloudWatch alarm fired** — if the manifest was missing, the alarm `cms-<stage>-flink-campaign-sync-decoder-manifest-fetch-failed` (metric `CMS/FWE / DecoderManifestFetchFailed`) would be in ALARM state. Check:
   ```bash
   aws cloudwatch describe-alarms \
     --alarm-names "cms-<stage>-flink-campaign-sync-decoder-manifest-fetch-failed" \
     --region us-west-2 --query 'MetricAlarms[].{Name:AlarmName,State:StateValue}'
   ```
   If ALARM: the manifest bucket is either missing the `fwe-config/DecoderManifest.bin` object or the app's `fwe.config.bucket` environment variable points to the wrong bucket. Verify:
   ```bash
   # The jar bucket (what the app reads from):
   aws kinesisanalyticsv2 describe-application \
     --application-name cms-<stage>-flink-campaign-sync-processor \
     --region us-west-2 \
     --query 'ApplicationDetail.ApplicationConfigurationDescription.EnvironmentProperties.PropertyGroups[?PropertyGroupName==`aws.iot.fwe.config`].Properties.fwe.config.bucket'

   # Verify the object exists:
   BUCKET=<value-from-above>
   aws s3api head-object --bucket "$BUCKET" --key fwe-config/DecoderManifest.bin --region us-west-2
   ```
   If missing: the stack-managed `BucketDeployment` did not run on your latest deploy. Re-run `make phase4` to force a re-deploy of the flink stack (or run `cdk deploy cms-<stage>-flink --require-approval never`).

2. **Check the ephemeral campaign was created.** DDB query:
   ```
   aws dynamodb get-item --table-name cms-<stage>-campaigns \
     --key '{"campaignId":{"S":"uds-dtc-<vin[:12]>-<sim_id>"}}'
   ```
   If missing, Lambda's `_build_uds_dtc_map` got zero DTCs for the
   scenario. Check the event catalog has `dtc_code` set for the
   maintenance event, and check the Lambda's IAM allows
   `dynamodb:Query` on `cms-<stage>-event-catalog`.

2. **Check FWE is firing DTC_QUERY.**
   ```
   aws logs filter-log-events --log-group-name /ecs/cms-<stage>/fwe-agent \
     --start-time <recent> \
     --filter-pattern '"DTC_QUERY" OR "Retrieved DTCs"'
   ```
   Expect to see `executeRequest` with the right ECU target address
   (0x01 for ECU1, etc.) every 30s.

3. **Check the responder is listening.** SSM to the fwe-simulator task
   container:
   ```
   ps ax | grep uds_dtc_responder
   candump -tz vcan0 | head -20   # should show 19 02 08 ... 59 02 FF ...
   ```

4. **Check the protobuf is reaching Kafka.** Enable a temporary debug
   IoT rule routing to S3 (`SELECT encode(*, 'base64') AS data FROM
   'cms/fleetwise/vehicles/+/signals'` → S3 action) to capture the
   raw payload. Decode the base64 bytes with the FWE protobuf schema
   to confirm `signal_id=901` with `string_value` populated.

5. **Check FWTelemetryProcessor is parsing STRING signals.**
   ```
   aws logs filter-log-events \
     --log-group-name /aws/kinesis-analytics/cms-<stage>-flink-fw-telemetry-processor \
     --filter-pattern '"UDS-DTC emitted" OR "handleUdsDtcInfo failed"'
   ```
   If no logs fire, check the signal name lookup — query the DDB
   decoder-manifest table for the 9 `SIGNAL_DECODER#Vehicle.ECU*.DTC_INFO`
   rows. Regenerate via `deployment/scripts/generate_decoder_manifest.py`
   if missing.

6. **Check the row was dedup-caught.** Detection dedup is now authoritative via the `active-code-index` GSI Query — there is no in-memory set to clear. A Flink app restart clears `DTC_SEVERITY_CACHE` and `TRIP_CACHE` (which are unrelated to dedup), but does NOT affect dedup behavior. If a duplicate row appears, it indicates a separate issue (e.g., multiple vehicles with the same vehicleId in the staging simulator); review the table directly.

### "I assigned `uds-dtc-polling` but no DTCs appear"

Common causes, in order of likelihood:

1. **The vehicle has no FWE agent running.** Assigning a campaign
   writes a DDB row, but FWE must be running on the vehicle to
   receive it. Check ECS: `aws ecs list-tasks --cluster
   cms-<stage>-simulation --region <region>` should show a
   `fwe-agent` task. If not, start a Trip Simulator run first
   (which boots the agent) — the assigned template will activate
   as soon as the agent connects.

2. **No UDS responder listening on vcan.** The `fwe-simulator` task
   spawns `uds_dtc_responder.py` at container startup, but only when
   `UDS_DTC_MAP` env var is set (via the Trip Simulator). If you
   assigned `uds-dtc-polling` without a Trip Simulator run, FWE
   will fire requests but get ISO-TP timeouts. Visible in FWE logs
   as `ISOTPOverCANSenderReceiver: receivePDU timeout`. Solution:
   start a Trip Simulator run, which spawns the responder.

3. **CampaignSyncProcessor hasn't synced yet.** Runs every 60s.
   Check `aws logs tail /aws/kinesis-analytics/cms-<stage>-flink-
   campaign-sync-processor --since 2m` for "Campaign sync sent" lines
   mentioning `uds-dtc-polling-<vin>`.

4. **Using a non-UDS template.** Only `uds-dtc-polling` (or any
   template with `signalsToFetch` populated) drives DTC polling.
   Assigning `cms-fleet-gps-10s` or the safety templates won't
   generate DTCs — those are signal-collection templates only.
   See `deployment/scripts/seed_uds_dtc_template.py` to create more
   UDS-DTC templates if needed.

### "DTC appeared but shows `code: 523400` instead of `C1234`"

The `decodeDtcFromHex()` function in `FWTelemetryProcessor` failed. If
you're seeing the raw hex, the Flink app is likely running an older
JAR that predates this helper. Rebuild the Flink JAR and redeploy:

```
cd modules/flink && ./build.sh
make phase4   # redeploys cms-<stage>-flink with the fresh JAR
```

To confirm the currently-running JAR:

```
aws kinesisanalyticsv2 describe-application \
  --application-name cms-<stage>-flink-fw-telemetry-processor \
  --query 'ApplicationDetail.ApplicationConfigurationDescription.ApplicationCodeConfigurationDescription.CodeContentDescription.S3ApplicationCodeLocationDescription'
```

### Force a fresh emission (cache and DTC lifecycle)

**DTC dedup state:** Dedup is now GSI-based (`active-code-index` with one ACTIVE row per vehicleId|code|source). App restart does NOT affect dedup — the authoritative state lives in DynamoDB, not in-memory. Operators cannot "clear" dedup by restarting; dedup state persists until rows are explicitly CLEARED (cleared_date set, activeCode removed from GSI).

**In-memory caches cleared on restart:** A Flink app restart clears `DTC_SEVERITY_CACHE` and `TRIP_CACHE`, which are independent of dedup and may affect fresh-detection behavior (severity lookups and trip-boundary logic re-evaluate). To fully reset detection and cache state:

```
aws kinesisanalyticsv2 stop-application \
  --application-name cms-<stage>-flink-fw-telemetry-processor --force
# wait for READY status
aws kinesisanalyticsv2 start-application \
  --application-name cms-<stage>-flink-fw-telemetry-processor \
  --run-configuration '{"ApplicationRestoreConfiguration":{"ApplicationRestoreType":"RESTORE_FROM_LATEST_SNAPSHOT"}}'
```

### Where's the row that a manual DTC-emit tool wrote?

A third path exists for ops/dashboard testing:
`deployment/scripts/force_event.py --emit-dtc` writes directly to
`cms-<stage>-storage-dtc-history` with `source="force_event.py"`.
This bypasses Flink entirely — used when you don't want to spin up
a simulation run just to populate the UI.

---

## Adding a new ECU to Path 1

The pipeline supports 9 ECUs as delivered. To add ECU10+:

1. **Extend `mSignalNames` sed patch** in `services/simulation/Dockerfile.fwe`.
2. **Extend `exampleUDSInterface` JQ block** in `deployment/stacks/simulation_stack.py`. Pick unused CAN IDs (ideally use extended addressing 0x18DA0AF1/0x18DAF10A for ECU10).
3. **Extend `UDS_ECU_FQNS` and `UDS_SIGNAL_IDS`** in `deployment/scripts/generate_decoder_manifest.py`. 
4. **Regenerate the manifest** into the committed location:
   ```bash
   cd ~/connected-mobility-guidance-on-aws
   DRY_RUN=1 python3 deployment/scripts/generate_decoder_manifest.py  # validate
   python3 deployment/scripts/generate_decoder_manifest.py            # commit to deployment/fwe-config/DecoderManifest.bin
   git add deployment/fwe-config/DecoderManifest.bin
   git commit -m "regen: add ECU10"
   ```
   The updated `.bin` is now the source of truth. On the next deploy, the stack's `BucketDeployment` will upload it to the Flink jar bucket automatically.
5. **Add catalog row** in `deployment/scripts/signal_catalog_seed.json` with `signal_group="diagnostics"`, `signal_name="ECU10_DTC_INFO"`, etc. Seed with direct boto3 put_item (do not use `seed_signal_catalog.py` — it clobbers the event catalog).
6. **Extend `_ECU_BY_NUMBER`, `_ECU_BY_CODE`, `_ECU_SIGNAL_ID`** in `services/simulation/lambda/simulation_lambda.py`. Add all DTCs that should route to this ECU in `_ECU_BY_CODE`.
7. **Redeploy everything:** `cdk deploy cms-<stage>-simulation` (picks up Dockerfile + stack changes; triggers FWE binary rebuild) and `cdk deploy cms-<stage>-flink` (uploads the new manifest).

---

## Adding a new DTC code to Path 1

To add a new DTC code (e.g., `P0500`):

1. **Add row to event catalog** with `event_id="maintenance.<slug>"`, `dtc_code="P0500"`, `severity_hint="P1"` (maps to HIGH), `json_fields` for matching, etc. The threshold path will also pick this up automatically.
2. **Route it to an ECU** in `_ECU_BY_CODE` in `simulation_lambda.py` (e.g., `"P0500": 2` for ECU2/ENGINE).
3. **Redeploy Lambda:** `cdk deploy cms-<stage>-simulation` is sufficient.

No manifest regeneration or FWE rebuild needed — the DTC code is configured at sim-start time via `UDS_DTC_MAP`, and the responder produces it from the map on demand.

---

## See also

- `docs/SEVERITY_VOCABULARY.md` — canonical severity scale used by
  DTC rows and every downstream consumer.
- `docs/RUNBOOK_clean_region_deploy.md` — from-scratch deployment of
  the full CMS stack to a new AWS region.
