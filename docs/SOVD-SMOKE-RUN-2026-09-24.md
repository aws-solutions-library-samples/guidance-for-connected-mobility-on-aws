# SOVD smoke — run record, 2026-09-24

Executed by: **operator (browser) + architect (agent)**, against **staging**, us-west-2.
Playbook: `docs/SOVD-SMOKE-PLAYBOOK.md`.
Vehicle: `VEH-MRDN-0001` / vin `MRDN0000000000001` (Meridian Azimuth, fleet
`FLEET-MERIDIAN-ONBOARD`, `dataSource=vehicle-telemetry` → classification `onboard`).
Session: `a11296bf-2c97-4a01-82af-3aff449c64e5`.

Supersedes the partial run in `docs/SOVD-SMOKE-RUN-2026-09-23.md` for Steps 3–6. Steps 0–2
and 4 were re-confirmed rather than assumed.

## Verdict

**6 of 7 steps PASS. Step 6 BLOCKED on a P2 routing defect found by this run.**

This is the first run where Steps 3 and 5 were executed in a **real browser** — the
2026-09-23 run drove the invoke half through the API, which cannot observe renderer DOM or
a disabled-button state. Two defects were found that no API-driven run or automated test
could have surfaced.

| Step | Mode | Outcome |
|---|---|---|
| 0 — sim prereq | agent | **PASS** — task-def `cms-staging-fwe-agent` rev **39**, `vehicle-ecu` digest `2b471fcb…` resolves in ECR |
| — sim running | agent | **PASS** — task `7886d7bb` RUNNING/HEALTHY since 2026-09-23T18:33Z; Step 2 skipped (already up) |
| 1a/1b/1c — preflight | agent (2026-09-23) | **PASS** — 6/6 pilots with correct safety classes; expected verdicts; read path 200 |
| 2 — start simulation | n/a | **SKIPPED** — a task was already running; re-checked via `ecs list-tasks` |
| 3 — invoke 3 INERT from the UI | **operator, browser** | **PASS** — all three reached `SUCCEEDED` with complete results |
| 4a — rows carry `verdict` + `result` | agent | **PASS** 3/3 |
| 4b — schema round-trip + verdict recompute | agent | **PASS** 3/3, `schema errors: none`, stored == recomputed |
| 5 — STATIONARY disabled in the UI | **operator, browser** | **PASS** — all three present and disabled |
| 6 — dispatch to DMS | **operator, browser** | **FAIL** — `403`; root-caused to a routing defect, P2 |

## Step 3 + 4 detail

Operator clicked the three INERT routines; the first attempt hit the sidecar rate limiter
(clicks ~5–6 s apart, which is not enough — see § Rate limiter below), so two of the three
needed a retry.

| Time (UTC) | Routine | Status | Verdict | `result` keys |
|---|---|---|---|---|
| 19:54:08 | `lamp_self_check` | SUCCEEDED | `out_of_spec` | `ambient_lux`, **`lamps`** |
| 19:54:14 | `pack_isolation_test` | RATE_LIMITED | — | — (`retry_after_ms` 315) |
| 19:54:19 | `cell_balance_check` | RATE_LIMITED | — | — (`retry_after_ms` 538) |
| 19:55:16 | `pack_isolation_test` | SUCCEEDED | `in_spec` | `isolation_resistance_mohm`, `threshold_mohm` |
| 19:59:46 | `cell_balance_check` | SUCCEEDED | `in_spec` | `cell_voltages`, `max_delta_mv` |
| 20:00:23 | `cell_balance_check` | SUCCEEDED | `in_spec` | `cell_voltages`, `max_delta_mv` |

All four rows in the first batch carry the same `session_id`, so no session re-mint occurred.

**Step 4b, all three (the playbook only requires one):** validated against
`services/_shared/routine_result_schemas.py` — `validate_result` plus
`verdict_from_result` recomputed and compared to the stored value.

```
cell_balance_check   schema errors: none   stored in_spec      recomputed in_spec      PASS
lamp_self_check      schema errors: none   stored out_of_spec  recomputed out_of_spec  PASS
pack_isolation_test  schema errors: none   stored in_spec      recomputed in_spec      PASS
```

Verdicts match Step 1b's predictions exactly.

**This closes out the 2026-09-23 P1.** `lamp_self_check` persisting `lamps` intact, through
the real UI path, is the end-to-end confirmation that
`issues/2026-09-23-iot-rule-destroys-arrays-in-sovd-responses/` is genuinely fixed — that
routine was the one the playbook warned would render wrong.

## Step 5 detail — PASS, with a playbook wording correction

All three STATIONARY routines (`o2_heater_check`, `evap_leak_test`, `abs_pump_cycle`)
render with their button **present and disabled**. The safety property holds: the UI gate
at `VehicleDiagnosticsPanel.tsx:1198` is the only enforcement point for the fleet-operator
persona, and it is doing its job.

**Playbook correction**: Step 5 says the explanation "displays the tooltip text". It is not
a tooltip — the text renders as static copy beneath each button. That is a documentation
error, not a defect, and arguably the better design: a safety explanation behind a hover is
discoverable only by accident. Step 5's pass condition should say "the explanation is
visible" and stop asserting the mechanism.

## Step 6 detail — the defect this run exists to have found

Operator saw:

```json
{"error":"Dispatch failed","detail":"DMS responded 403."}
```

Root cause: CMS forwards to `${DMS_API_ENDPOINT}/api/dms/repair-orders`, which **has no
POST method** — `dms_api_stack.py:1228-1232` creates that resource only as a prefix for
`/{roId}/notes`. API Gateway answers an unmatched route with `403 Missing Authentication
Token`, and `main_api/index.py` passed 401/403 through verbatim as auth context. So a
routing bug presented as a permissions problem.

Proven at route level, unauthenticated:

```
POST /api/dms/repair-orders        -> 403 {"message":"Missing Authentication Token"}
POST /api/dms/fleet/repair-orders  -> 401 {"message":"Unauthorized"}
```

**Dispatch has never worked for any caller** — route-level, so independent of groups,
claims and token validity. `platform-admin` does not help.

Worth recording honestly: **both the operator and the architect first read the 403 as a
Cognito groups problem**, and the architect spent several tool calls inspecting pool
membership and `custom:dealerIds` before the curl pair settled it. The error message sent
two investigations the wrong way, which is why disambiguating it shipped with the fix.

Full analysis, including a **second defect** (`complaint` and `priority` are silently
dropped because DMS reads `description` and has no `priority` field at all — so dispatch
will return 201 and still lose operator input):
`issues/2026-09-24-cms-dispatch-posts-to-unrouted-dms-path/`.

## Defects found by this run

| Issue | Severity | Status |
|---|---|---|
| `issues/2026-09-24-cms-dispatch-posts-to-unrouted-dms-path/` | **P2** | Fix applied at code level; **not deployed**, not live-verified |
| ↳ second defect in the same report: `complaint`/`priority` dropped | **P3** | Open — needs an owner decision, deliberately not patched |
| `issues/2026-09-24-diagnostics-results-render-below-fold-with-no-affordance/` | **P3** | Open — report only |

The third was the operator's own observation: *"the UI on this screen needs some formating
didn't know what was happening down here i didn't know i had to scroll to see the
results."* The routine-result renderers — the headline deliverable of
`2026-09-10-cms-sovd-routine-result-contracts` — render below the fold with nothing in the
viewport indicating they exist. Every automated check of that surface passes, and did.

## Rate limiter, again

Same finding as 2026-09-23, now confirmed with a human at the keyboard: ~5–6 s between
clicks is **not** enough. The limiter is a token bucket in the sidecar
(`realtime_telemetry_simulator.py:1156-1168`, per-ECU-slot); the API answers `HTTP 200 /
SENT` and the refusal appears only on the response row, so the UI shows a run that failed
for no stated reason.

The UI already receives `retry_after_ms`. Surfacing it would remove the need for the
playbook's pacing warning entirely — filed as part of the below-the-fold issue.

## Corrections made to the playbook before this run

Three blocks had gone stale when the 2026-09-23 P1 was fixed later the same day (commit
`401192ac`):

1. **Step 3's "Known blocker"** told the reader to *expect* `lamp_self_check` and
   `cell_balance_check` to render wrong. Left in place, the operator would have written off
   a genuine failure as known. This was the one that mattered.
2. **Header note** said Step 4a/4b FAIL.
3. **"Deployed-sim state"** said zero tasks are running, which would have sent the operator
   through Step 2 unnecessarily.

## What remains

- **Step 6** — re-run after a CMS `main_api` deploy. Its four pass conditions
  (`initiated_by=cms_booking`, `dealer_id` matches the picked dealer, non-empty
  `evidence.sessionId`, `routinesRun >= 1`) are all expected to be satisfiable once routed;
  the `dealer_id` roster check (400 outside DMS's twelve rooftops) is the most plausible
  remaining surprise.
- **Both `[!]` smoke flags stay open**:
  `2026-09-10-cms-dms-sovd-diagnostic-sessions-v1.5` T6.6 and
  `2026-09-10-cms-sovd-routine-result-contracts` T6.2. Steps 0–5 are done for both; only
  the dispatch observation is outstanding.
- **`issues/2026-09-23-diagnostics-session-log-never-populates/`** still records
  `Live-verified: no`. Its session-log half is now **confirmed working** by this run
  (several `session-log-entry-<id>` nodes observed in the DOM, and a populated `session_id`
  on every row — pre-fix the UI sent `session_id` in the body, which the backend ignored,
  so the attribute would have been absent entirely). Its `evidence.sessionId` half still
  needs a successful dispatch.

## Environment left behind

- fwe-agent task `7886d7bb` **left RUNNING** on rev 39 so Step 6 can be retried
  immediately after a deploy. To stop it: the UI's Stop Agent control, or
  `POST /api/simulation/agent/stop` with `{"vin":"MRDN0000000000001"}`.
- Six `run_routine` rows from this run remain on `cms-staging-storage-commands`, two of them
  accurate `RATE_LIMITED` records. All carry a TTL.
- No synthetic vehicles or users created; no cleanup owed.
