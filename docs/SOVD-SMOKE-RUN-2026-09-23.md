# SOVD smoke — run record, 2026-09-23

Executed by: architect (agent), against **staging**, us-west-2, account `<account-id>`.
Playbook: `docs/SOVD-SMOKE-PLAYBOOK.md`.
Vehicle: `VEH-MRDN-0001` (vin `MRDN0000000000001`, hybrid, fleet `flt-meridian-range-001`).

**Scope of this run**: every AGENT-RUNNABLE step, plus two steps the playbook marks
USER-REQUIRED that turned out to be drivable from the API (Step 2, and the invoke half of
Step 3). The browser-only observations — renderer DOM, disabled-state `data-testid`,
Dispatch modal — remain owed to the user.

## Verdict

**Steps 0–2 and 4a/4b now PASS.** The run initially failed at Step 4a/4b on a defect in the
SOVD response path; that defect was root-caused, fixed, deployed to staging and
live-verified in the same session. Steps 3, 5 and 6 need a browser and remain owed.

Two defects filed:

| Issue | Severity | Status |
|---|---|---|
| `issues/2026-09-23-iot-rule-destroys-arrays-in-sovd-responses/` | **P1** | **RESOLVED** — fixed, deployed to staging, live-verified |
| `issues/2026-09-23-diagnostics-session-log-never-populates/` | **P2** | Open |

The P1 turned out to be **two stacked defects**, the first masking the second:

1. `cms_staging_sovd_response_rule` omitted `aws_iot_sql_version`, defaulting to the legacy
   `2015-10-08` SQL version, which destroys nested arrays and flattens nested objects.
2. `_persist_response_payload` passed raw floats to DynamoDB, which rejects them — and the
   surrounding `except` is non-fatal, so the **entire** response attribute was dropped with
   only a WARNING. 3 of the 6 pilots emit floats and had therefore **never** persisted a
   response; this was invisible while defect 1 was stripping the float-bearing arrays first.

Pre-fix, only 2 of 6 pilots (`o2_heater_check`, `abs_pump_cycle`) persisted a correct result.

Three factual errors in the playbook itself were found and corrected in the same pass
(see § Playbook corrections).

## Result recording — against the playbook's own checklist

| Step | Outcome | Detail |
|---|---|---|
| Step 0 prereq check | **PASS** — no bump required | task-def `cms-staging-fwe-agent` rev **39**; `vehicle-ecu` image digest `2b471fcb…` present in ECR, pushed 2026-09-22; 0 running tasks (expected post-drain) |
| Step 1a — 6/6 routines, correct safety class | **PASS** | Output matched the playbook's expected block byte-for-byte. 14 routines returned total; all 6 pilots present with `INERT`×3 / `STATIONARY`×3 and `invocableByCaller=True` |
| Step 1b — expected verdicts printed | **PASS** | Matched expected exactly: `lamp_self_check -> out_of_spec`, other five `in_spec` |
| Step 1c — `resolveVehicleByVin` OK | **PASS (after correcting the playbook's host)** | As written the playbook derives the host from `$COMMANDS_API` → **HTTP 403**. Against the CMS main API host → **HTTP 200**, `vehicleId=VEH-MRDN-0001` |
| Step 2 — fwe-agent RUNNING on rev ≥ 30 | **PASS** | `POST /api/simulation/agent/start` → task `7886d7bb…` `RUNNING` on `cms-staging-fwe-agent:39`; containers `fwe-agent` HEALTHY, `vehicle-ecu` RUNNING. **Driven from the API, no browser needed** — see § Step 2 is agent-runnable |
| Step 3 — 3 INERT rows rendered with named renderer | **NOT RUN (browser)** — invoke half PASS | All three INERT routines accepted `HTTP 200 / SENT`. Sidecar logged `🔬 SOVD run_routine received` and published a response in **7 ms**. Renderer DOM not observable by an agent |
| Step 4a — 3 DDB rows with `verdict` + `result` | **PASS (after the fix)** | Initially FAIL: only `lamp_self_check` reached `SUCCEEDED`; the other two returned **`RATE_LIMITED`** (`retry_after_ms` 680) — see § Rate limiter. After both fixes and re-invocation, all three INERT pilots carry `verdict` + a complete `result`. Separately, Step 4a's grouping key `response.routine_id` does not exist on the row (corrected) |
| Step 4b — schema round-trip clean, recomputed verdict matches stored | **PASS (after the fix)** | Initially FAIL: persisted `response.result` was `{ambient_lux: 6322}` with `lamps` absent → `missing field 'lamps'`, recomputed `in_spec` vs stored `out_of_spec`. Post-fix all three INERT pilots: `schema errors: none`, stored == recomputed (`lamp_self_check` `out_of_spec`, `cell_balance_check` `in_spec`, `pack_isolation_test` `in_spec`) |
| Step 5 — 3 STATIONARY show `run-routine-disabled-*` | **NOT RUN (browser)** | Deliberately not bypassed via curl, per Step 3's constraint — invoking them would defeat the safety class under test |
| Step 6 — DMS RO row, `initiated_by=cms_booking`, non-empty `evidence.sessionId` | **NOT RUN (browser)** | Requires the Dispatch modal. Note the `sessionId` predicate is itself at risk — see the P2 issue |

## Headline defect (P1) — RESOLVED in this session

See `issues/2026-09-23-iot-rule-destroys-arrays-in-sovd-responses/summary.md` for the fix,
the verification table and the follow-ons. In brief:

`response.result` for `lamp_self_check` persisted as `{ambient_lux: 6322}` — the `lamps`
array gone. Isolated to the **IoT topic rule**, not the sidecar, not the Lambda, not
DynamoDB, by four controlled tests:

1. **Sidecar published the full payload.** It logged `448 bytes`. Rebuilding the payload
   locally: **with** `lamps` = 448 bytes, **without** = 376. Byte-exact match for *with*.
2. **DynamoDB is fine.** A direct `update_item` writing `{'lamps': [...], 'ambient_lux': …}`
   to that attribute round-trips intact.
3. **The Lambda is fine.** Invoking `cms-staging-command-response-handler` directly with a
   synthetic event preserved `top_array`, `result.nested_array` and a nested object whole.
4. **The IoT rule is the culprit.** The *same* payload published to the real SOVD response
   topic arrived with top-level arrays **emptied to `[]`**, nested arrays **dropped**, and
   nested objects **flattened into the parent**. Reproduced twice.

Blast radius is wider than the pilot renderers:

- **2 of 6 pilots** lose a required array — `lamp_self_check` (`lamps`) and
  `cell_balance_check` (`cell_voltages`). The other four carry scalars only and are
  unaffected.
- `lamp_self_check` is the routine the playbook **recommends specifically** because its
  storytelling verdict lands on an INERT routine a fleet operator can invoke. The one
  routine the smoke exists to showcase is the broken one.
- **Zero** list-valued fields survive in **any** SOVD response row in
  `cms-staging-storage-commands` (6 rows scanned, 0 empty lists, 0 non-empty lists).
- A `read_dtcs` row's `response.components` is a single flattened scalar-only ECU
  (`{id, protocol}`) — the per-ECU DTC arrays are gone.
- `cms-staging-storage-dtc-history` held **0 rows with `source='sovd'`**, while
  `flink-maintenance-processor` (84), `oem1-uds-dtc` (73) and `fwe-uds-dtc` (2) had rows.
  **This inference was over-reach and is corrected in the issue's `summary.md`**: post-fix,
  all 9 ECUs report `dtcs: 0`, so the vehicle genuinely has no DTCs and writing no rows is
  correct. What the fix demonstrably restored is the structure — every ECU now carries its
  `dtcs` key, where before the whole per-ECU object was flattened to a scalar-only
  `{id, protocol}`.

## Step 2 is agent-runnable, contrary to the playbook

The playbook marks Step 2 USER-REQUIRED ("open the Simulation panel and start a trip").
`POST /api/simulation/agent/start` is Cognito-authorised and takes
`{"vin": …, "vehicleId": …}` — byte-identical to what `VehicleDetailView.tsx:558`'s
"Start Agent" control sends. Driving it directly launched the task and satisfied Step 2's
pass condition, which also closes the ECS half of the `routine-result-contracts` live-sim
`[!]` Verify without a browser.

Gate to be aware of: the route requires the vehicle to be classified `onboard`
(`_vehicle_is_onboard(...) is not True` → 403) and needs an ACTIVE, agent-connected ECS
container instance, else it returns a retryable 503 and scales the ASG.

## Rate limiter breaks Step 3 as written

Three invocations issued back-to-back: the first `SUCCEEDED`, the next two came back
`RATE_LIMITED` with `retry_after_ms: 680`. The limiter is a token bucket in the **sidecar**
(`realtime_telemetry_simulator.py:1156-1168`, per-ECU-slot), not in the Lambda — the API
returns `HTTP 200 / SENT` and the refusal only appears on the response row.

Step 3 asks the user to click **Run** on three routines in sequence. Done briskly, two will
rate-limit, and the UI surfaces that as a failed run with no obvious cause. **Leave ~2 s
between invocations**, or expect to re-run. Folded into the playbook as a note.

## Other findings, not blocking

- **`fuelType` disagrees with itself** on `VEH-MRDN-0001`: top-level `fuelType: hybrid`,
  `attributes.fuelType: electric`. Authorisation and the routine profile read the
  **top-level** value (`commands_lambda.py:307 _resolve_vehicle_fuel_type`), so routine
  resolution is correct; a UI reading `attributes.fuelType` would display the wrong
  powertrain. Worth a P3.
- **`VIN` is not `vehicleId`** on this fleet, contrary to the playbook's Setup comment
  ("VIN == vehicleId for demo fleet"): `vehicleId=VEH-MRDN-0001`, `vin=MRDN0000000000001`.
  It matters at Step 6, whose RO scan filters on `vehicle_vin` and needs the real VIN.
- **Two `RATE_LIMITED` rows** left on `cms-staging-storage-commands` from this run
  (`76d1631a…`, `81a74d7c…`). They are accurate records of a real refusal; both carry a TTL.
- **`vehicle_id` logs empty** in the response handler
  (`✅ SOVD command … for  (command_type: run_routine)`) — neither `event.vehicleId` nor
  `payload.vehicleId` is populated on the SOVD path. Cosmetic, but it makes the log
  unusable for per-vehicle triage.

## Playbook corrections applied

1. **Step 1c + Setup** built the vehicles URL from the `$COMMANDS_API` host → HTTP 403.
   Now uses the CMS main API endpoint. As written, an operator would have concluded the
   shared CMS read path (and therefore the whole DMS Diagnostics surface) was broken.
2. **Setup** read `vehicleId` from the JSON top level; the response nests it under
   `{"vehicle": {…}}`, so `$VEHICLE_ID` was always empty — which the playbook's own pass
   condition interprets as "everything downstream is moot".
3. **Step 4a** grouped rows by `response.routine_id`. The sidecar does not emit that field;
   the routine is on the **top-level `routineId`** attribute. As written Step 4a can never
   pass, even on a healthy row.

## Environment left behind

- fwe-agent task `7886d7bb…` **left RUNNING** on rev 39 so the user can do the browser
  steps immediately. To stop it:
  `POST /api/simulation/agent/stop` with `{"vin":"MRDN0000000000001"}`, or the UI's
  Stop Agent control.
- Ephemeral Cognito user `sovd.smoke.agent@example.com` (created for API auth in the
  `fleet-operator` group) **deleted** at cleanup.
- All synthetic test rows (`ZZSMOKE*`) deleted.
