# SOVD end-to-end smoke playbook (staging, single-persona)

Consolidates the two `[!]` smoke flags:

- `2026-09-10-cms-dms-sovd-diagnostic-sessions-v1.5` Group 6 — "Smoke test playbook against staging"
- `2026-09-10-cms-sovd-routine-result-contracts` Group 6 — "End-to-end smoke against staging"

> **A partial run was executed 2026-09-23 — read
> `docs/SOVD-SMOKE-RUN-2026-09-23.md` before starting.** Steps 0–2 and **4a/4b PASS**.
> Steps 3, 5 and 6 need a browser and remain owed to the user. Neither `[!]` flag has
> flipped.
>
> **Latest run: `docs/SOVD-SMOKE-RUN-2026-09-25.md`** (Diagnostics tab redesign; Step 6
> dispatch now PASSES end to end, and View RO opens the repair order in DMS).
>
> **SUPERSEDED 2026-09-24 — read `docs/SOVD-SMOKE-RUN-2026-09-24.md` instead.** The
> browser steps were executed by the operator that day: **Steps 0–5 all PASS** (Step 3 and
> Step 5 for the first time in a real browser, which is what found two defects no
> API-driven run could reach). **Step 6 is BLOCKED** on
> `issues/2026-09-24-cms-dispatch-posts-to-unrouted-dms-path/` (**P2**) — CMS posts to a
> DMS path with no POST method, so dispatch has never worked for any caller. Fix applied at
> code level; **not deployed**, so Step 6 is the only step still outstanding. Both `[!]`
> flags remain open on that one observation.
>
> **UPDATED 2026-09-24.** The P1 that made 4a/4b fail
> (`issues/2026-09-23-iot-rule-destroys-arrays-in-sovd-responses/`) is **RESOLVED** —
> committed, deployed to staging, and live-verified, all on 2026-09-23, and re-confirmed
> through the real UI path on 2026-09-24. All three INERT pilots persist `verdict` + a
> complete `result` with no schema errors, and stored verdict equals recomputed. The
> earlier revision of this note said 4a/4b FAIL; that is no longer true and the Step 3
> blocker warning has been corrected to match (see Step 3). Three factual errors in this
> playbook were found and corrected in the 2026-09-23 pass.

## Scope and non-goals

Per user direction 2026-09-23, verbatim:

> *"no personas, i'm not going to test a bunch of different personas. lets just
> make sure SOVD works, it makes sense, and it is integrated into the DMS Service
> (eventually)"*

**One persona: `fleet-operator`.** No technician login, no DMS-side browser UAT.
The CMS→DMS integration is verified by observing the DMS repair-order row that
the CMS dispatch creates, plus the shared CMS read path that DMS's
`resolveVehicleByVin()` consumes — both agent-runnable against the deployed
system. Full DMS UI walkthrough (login as `service-advisor` / `dms-technician`,
run STATIONARY routines from DMS, add notes, close RO) is deferred; it needs a
persona this smoke deliberately does not exercise.

**All six pilot routines are covered**, just not all invoked. Fleet-operator can
invoke only the three INERT routines from CMS — this is the safety-class
property enforced in the UI. The three STATIONARY routines are verified as
disabled in the UI (that IS the property that matters for the fleet-operator
persona; a STATIONARY row that becomes clickable for this persona is a safety
regression). The playbook says so at each step, so a reader does not read
"three invocations" as "three-of-six coverage."

**Staging only.** Every command below runs against `us-west-2` in the staging
AWS account (resolve the account id from `aws sts get-caller-identity` under
your staging profile — do not hardcode). Do not run write steps against prod.

## Deployed-sim state, at the time of writing

Currently satisfied. Task-def `cms-staging-fwe-agent` is at **rev 39** (ACTIVE,
registered 2026-09-23T09:54Z), with `vehicle-ecu` on the private CDK asset
digest `2b471fcb…` pushed 2026-09-22. An offline check of that image via a
pulled ECR digest confirms:

- `/app/routine_sims.py` is present (the file whose omission was the
  2026-09-13 defect — `issues/2026-09-13-routine-sims-omitted-from-sidecar-image/`)
- `/app/_shared/routine_result_schemas.py` is present
- `ROUTINE_RESULT_SCHEMAS` registers all six pilot routine IDs:
  `abs_pump_cycle`, `cell_balance_check`, `evap_leak_test`, `lamp_self_check`,
  `o2_heater_check`, `pack_isolation_test`
- `produce_result('lamp_self_check', 'VEH-MRDN-0001')` returns
  `verdict == 'out_of_spec'` and `lamps[2] == 'open_circuit'` — the demo
  storytelling override lands as designed

**Zero fwe-agent tasks are running** in cluster `cms-staging-simulation`. That
is not a defect: fwe-agent tasks are launched on demand by
`simulation_lambda.run_task`; the post-drain state after any sim redeploy is
zero. Step 2 (starting a simulation from the CMS UI) is what launches the
first task on rev 39, and that step is what completes the T2.3 live verify
half as a side effect.

> **UPDATED 2026-09-24 — a task IS currently running, so Step 2 can be skipped.**
> The 2026-09-23 run left task `7886d7bb007942efb42e92e99b45afc3` up deliberately so the
> browser steps could be done immediately. Verified live 2026-09-24: `lastStatus RUNNING`,
> `healthStatus HEALTHY`, on `cms-staging-fwe-agent:39`, started 2026-09-23T18:33Z;
> containers `fwe-agent` RUNNING/HEALTHY and `vehicle-ecu` RUNNING. **Re-check before
> relying on this** — ECS may have cycled it since:
>
> ```bash
> aws ecs list-tasks --cluster cms-staging-simulation --region us-west-2 --query 'taskArns'
> ```
>
> Non-empty → go straight to Step 3. Empty → run Step 2 first (it is agent-runnable via
> `POST /api/simulation/agent/start`; see the run record's § "Step 2 is agent-runnable").
> The paragraph above remains the correct description of the *post-drain* steady state.

## Legend

Each step is one of:

- **AGENT-RUNNABLE** — a shell command; an agent or an operator can execute
  it identically.
- **USER-REQUIRED** — a browser action, a Cognito login through the Hosted UI,
  or any interaction with the CMS UI that needs a signed-in human. Per
  `~/.kiro/steering/agent-capabilities.md`, agents cannot tap simulator UIs or
  authenticate through Cognito's Hosted UI on a real browser.

Every step's "pass" condition is a falsifiable observation — a specific DOM
`data-testid`, a specific value in a specific DDB attribute, a specific HTTP
status paired with a specific JSON field. Per
`~/.kiro/steering/deploy-validation.md`, a bare HTTP 200 on a SPA route
measures CloudFront, not the application, and does not count.

## Step 0 — Operator prerequisite (conditional, USER-REQUIRED only if the check fails)

**Check first, do not rebuild by default.** The bump commands below apply only
if the check fails. If the check passes, skip to § "Prerequisites."

**AGENT-RUNNABLE check** — task-def revision and image freshness:

```bash
aws ecs describe-task-definition \
  --task-definition cms-staging-fwe-agent \
  --region us-west-2 \
  --query 'taskDefinition.{rev:revision,image:containerDefinitions[?name==`vehicle-ecu`].image | [0]}'
```

Pass condition: `rev` is `>= 30` and the `image` reference resolves (any tag/
digest returned by ECR — the value itself changes on every redeploy). At time
of writing this file, `rev: 39`.

If either check fails (task-def not registered, or image missing from ECR),
the sim service has been rolled back or destroyed and the operator step below
is required before the smoke can pass.

**USER-REQUIRED bump** — from a shell logged into the staging CMS AWS account
in `us-west-2`:

```bash
cd ~/connected-mobility-guidance-on-aws/deployment
SIM_IMAGE_MODE=asset \
SIM_IMAGE_ALLOW_STALE=1 \
  make deploy-simulation DEPLOYMENT_STAGE=staging
```

`SIM_IMAGE_MODE=asset` builds the sim images from this checkout instead of
using the published public-ECR tag (the published tag lags whenever the sim
code moves ahead of the last release). `SIM_IMAGE_ALLOW_STALE=1` accepts the
drift check that fires because
`deployment/stacks/_sim_image_config.py::SIM_IMAGE_SOURCE_COMMIT` still points
at the last published commit — this is expected pre-release and is not a
defect. Docker must be running (asset mode builds locally).

`make deploy-simulation` chains `deployment/scripts/drain_stale_fwe_agents.sh`
after the CDK deploy, which stops any task on a below-latest revision so the
new revision can claim the kernel ISO-TP socket pool. Zero running tasks
after the drain is the expected post-condition — see § "Deployed-sim state"
above.

**Verify the bump landed** — re-run the AGENT-RUNNABLE check. The `rev` must
have incremented and the new `image` must resolve in ECR.

## Prerequisites

Provided by the operator (out of band, before starting):

- Fleet-operator credentials (email + password) in the `fleet-operator`
  Cognito group with `custom:fleetIds` covering the target vehicle. Verify
  before starting:
  ```bash
  aws cognito-idp admin-get-user \
    --user-pool-id "$USER_POOL_ID" \
    --username <fleet-op-email> \
    --region us-west-2 \
    --query "{groups:UserAttributes[?Name=='cognito:groups'].Value | [0], fleetIds:UserAttributes[?Name=='custom:fleetIds'].Value | [0]}"
  ```
  (Resolve `USER_POOL_ID` in § "Setup" below.)
- A **HYBRID** vehicle in the operator's fleet — this is the only powertrain
  that carries all six pilot routines. The three below are seeded demo
  vehicles that also land the storytelling verdict overrides; pick one:

  | VIN | Fuel | Demo override |
  |---|---|---|
  | `VEH-MRDN-0001` | hybrid | `lamp_self_check` → `out_of_spec` (L-brake `open_circuit`) |
  | `VEH-MRDN-0002` | hybrid | `o2_heater_check` → `marginal` (STATIONARY — visible only via DMS, not this smoke) |
  | `VEH-MRDN-0015` | hybrid | `cell_balance_check` → `out_of_spec` |

  `VEH-MRDN-0001` is the recommended pick — its storytelling verdict lands on
  an INERT routine that the fleet-operator persona can actually invoke from
  CMS. On the other two the storytelling verdict is on a routine this
  persona cannot invoke, so the smoke never sees the "colour" — the sim's
  default for the remaining routines is `in_spec` on this VIN.

## Setup — resolve URLs, endpoints, JWT (AGENT-RUNNABLE)

```bash
export AWS_REGION=us-west-2

# CMS UI + Cognito
# Resolve the CMS UI URL from the deployed stack rather than hardcoding — it
# tracks whatever `UI_CUSTOM_DOMAIN` is configured to on the current stage.
export CMS_URL=$(aws cloudformation describe-stacks \
  --stack-name cms-staging-ui \
  --query "Stacks[0].Outputs[?OutputKey=='CustomDomainURL' || contains(OutputKey,'CloudFrontURL')].OutputValue | [0]" \
  --output text --region "$AWS_REGION")
export USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name cms-staging-ui \
  --query "Stacks[0].Outputs[?contains(OutputKey,'UserPoolId') && !contains(OutputKey,'Client')].OutputValue | [0]" \
  --output text --region "$AWS_REGION")
export CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name cms-staging-ui \
  --query "Stacks[0].Outputs[?contains(OutputKey,'ClientId')].OutputValue | [0]" \
  --output text --region "$AWS_REGION")

# Commands API (SOVD routines + run_routine live here)
export COMMANDS_API=$(aws cloudformation describe-stacks \
  --stack-name cms-staging-commands \
  --query "Stacks[0].Outputs[?OutputKey=='CommandsApiUrl'].OutputValue | [0]" \
  --output text --region "$AWS_REGION")

# CMS main API — this is where /api/v1/vehicles/{vin-or-id} lives. It is a
# DIFFERENT API Gateway from the commands API. Corrected 2026-09-23: earlier
# revisions of this file derived the vehicles URL from $COMMANDS_API's host,
# which has no such route and answers 403 with a SigV4 header-parse error.
# An operator following that would conclude the shared CMS read path — and so
# the whole DMS Diagnostics surface — was broken. See docs/SOVD-SMOKE-RUN-2026-09-23.md.
export CMS_API=$(aws cloudformation describe-stacks \
  --stack-name cms-staging-ui \
  --query "Stacks[0].Outputs[?OutputKey=='APIEndpoint'].OutputValue | [0]" \
  --output text --region "$AWS_REGION")

# Tables (both HASH-key only, verified against live)
export COMMANDS_TABLE=cms-staging-storage-commands   # key: commandId (S)
export RO_TABLE=dms-staging-repair-orders             # key: ro_id (S)

# Fleet-operator JWT — Hosted UI is normally the browser path, but
# USER_PASSWORD_AUTH is enabled on this client (verified 2026-09-23) so a
# curl-first path is available for the API-preflight steps below.
# For the CMS UI login (§ Steps 1, 3, 5) use the browser instead — this JWT
# is for curl.
export JWT_FLEETOP=$(aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=<fleet-op-email>,PASSWORD=<password> \
  --client-id "$CLIENT_ID" --region "$AWS_REGION" \
  --query 'AuthenticationResult.IdToken' --output text)

# Chosen VIN and vehicleId. NOTE (corrected 2026-09-23): VIN is NOT equal to
# vehicleId on this fleet — VEH-MRDN-0001 has vin=MRDN0000000000001. Both are
# needed: vehicleId keys the commands/fleet-membership paths, the real VIN keys
# the DMS repair-order scan in Step 6.
# Resolve through the same endpoint DMS's resolveVehicleByVin uses — one call
# proves both. The response nests the vehicle under a "vehicle" key.
export VIN="VEH-MRDN-0001"
export VEHICLE_ID=$(curl -sSf -H "Authorization: Bearer $JWT_FLEETOP" \
  "${CMS_API}api/v1/vehicles/$VIN" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); v=d.get('vehicle',d); print(v.get('vehicleId',''))")
export REAL_VIN=$(curl -sSf -H "Authorization: Bearer $JWT_FLEETOP" \
  "${CMS_API}api/v1/vehicles/$VIN" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); v=d.get('vehicle',d); print(v.get('vin',''))")
echo "VEHICLE_ID=$VEHICLE_ID  REAL_VIN=$REAL_VIN  (both must be non-empty)"
```

Pass condition for setup: every export resolves non-empty; `$VEHICLE_ID` is a
non-empty string. An empty `$VEHICLE_ID` means the shared CMS `GET
/api/v1/vehicles/{vin-or-id}` path — the one DMS's `resolveVehicleByVin()`
consumes — is broken, and everything downstream is moot.

## Step 1 — Preflight (AGENT-RUNNABLE)

### 1a. Routines endpoint lists all six pilots with correct safety classes

```bash
curl -sSf -H "Authorization: Bearer $JWT_FLEETOP" \
  "${COMMANDS_API}api/commands/${VEHICLE_ID}/routines" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
pilots = {'lamp_self_check','cell_balance_check','pack_isolation_test',
          'abs_pump_cycle','evap_leak_test','o2_heater_check'}
by_id = {r['routineId']: r for r in d.get('routines', [])}
missing = pilots - by_id.keys()
if missing:
    print('FAIL: missing pilot routines:', sorted(missing)); sys.exit(1)
for rid in sorted(pilots):
    r = by_id[rid]
    print(f\"{rid:22s} safetyClass={r['safetyClass']:11s} invocable={r['invocable']!s:5s} invocableByCaller={r.get('invocableByCaller','?')}\")"
```

Pass condition (exact):

```
abs_pump_cycle         safetyClass=STATIONARY  invocable=True  invocableByCaller=True
cell_balance_check     safetyClass=INERT       invocable=True  invocableByCaller=True
evap_leak_test         safetyClass=STATIONARY  invocable=True  invocableByCaller=True
lamp_self_check        safetyClass=INERT       invocable=True  invocableByCaller=True
o2_heater_check        safetyClass=STATIONARY  invocable=True  invocableByCaller=True
pack_isolation_test    safetyClass=INERT       invocable=True  invocableByCaller=True
```

Note: `invocableByCaller=True` on all six is expected for a fleet-operator —
`invocableByCaller` gates only on the technician active-RO predicate, not on
STATIONARY. The safety-class enforcement for this persona happens in the CMS
UI (Step 3 below reads the disabled `data-testid` for each STATIONARY row);
the safety-class enforcement for a technician persona happens server-side and
is out of scope for this smoke.

### 1b. Pre-compute the expected verdicts

The sim is deterministic per `(routine_id, vehicle_id)`. Compute what each
routine will produce before invoking anything, then compare on the way out:

```bash
cd ~/connected-mobility-guidance-on-aws
VIN="$VIN" python3 -c "
import os, sys; sys.path.insert(0,'services')
from simulation.routine_sims import produce_result
from _shared.routine_result_schemas import ROUTINE_RESULT_SCHEMAS as S
vin = os.environ['VIN']
for rid in sorted(S):
    res = produce_result(rid, vin)
    v = S[rid]['verdict_from_result'](res)
    print(f'{rid:22s} -> {v}')"
```

Pass condition for `VEH-MRDN-0001`:

```
abs_pump_cycle         -> in_spec
cell_balance_check     -> in_spec
evap_leak_test         -> in_spec
lamp_self_check        -> out_of_spec
o2_heater_check        -> in_spec
pack_isolation_test    -> in_spec
```

Keep this output beside you — Step 4 compares each observed verdict against
its expected value. A mismatch means the sim in the deployed container has
drifted from the checkout, not that the UI is broken.

### 1c. Shared CMS read path (what DMS calls) is reachable

```bash
curl -sSf -H "Authorization: Bearer $JWT_FLEETOP" \
  "${CMS_API}api/v1/vehicles/$VIN" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); v=d.get('vehicle',d); assert v.get('vehicleId'), d; print('resolveVehicleByVin OK:', v['vehicleId'])"
```

Pass condition: prints `resolveVehicleByVin OK: VEH-MRDN-0001`. This is the
exact request DMS's `frontend/src/api/cms-client.ts:201` `resolveVehicleByVin()`
makes; CMS's centralized rewrite at `main_api/index.py:1365` transparently
accepts either the VIN or the vehicleId. A 4xx here breaks DMS's diagnostics
panel and every dispatched RO's Diagnostics tab; the CMS surface has to work
before the DMS-side surface is worth looking at.

## Step 2 — Start a simulation (USER-REQUIRED via UI, or AGENT-RUNNABLE via API)

**Corrected 2026-09-23: this step does not require a browser.**
`POST /api/simulation/agent/start` is Cognito-authorised and takes the same body
`VehicleDetailView.tsx:558`'s "Start Agent" control sends:

```bash
SIM_API=$(aws cloudformation describe-stacks --stack-name cms-staging-simulation \
  --query "Stacks[0].Outputs[?OutputKey=='SimulationApiUrl'].OutputValue | [0]" \
  --output text --region "$AWS_REGION")

curl -sS -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer $JWT_FLEETOP" \
  -d "{\"vin\":\"$REAL_VIN\",\"vehicleId\":\"$VEHICLE_ID\"}" \
  "${SIM_API}api/simulation/agent/start"
```

Two gates to know about: the vehicle must be classified `onboard`
(`_vehicle_is_onboard(...) is not True` → 403), and the cluster needs an ACTIVE,
agent-connected ECS container instance — otherwise the route returns a retryable
503 and scales the ASG, and you wait ~3 minutes.

**USER alternative**: log in to CMS as the fleet-operator through the Hosted UI
at `$CMS_URL`, open the chosen vehicle, open the **Simulation** panel and start a
trip. Same effect — `simulation_lambda.run_task` launches an fwe-agent ECS task.

**AGENT-RUNNABLE verification** — wait ~60 seconds after starting the trip,
then check for a running fwe-agent task on the current revision:

```bash
CURRENT_REV=$(aws ecs describe-task-definition \
  --task-definition cms-staging-fwe-agent \
  --region us-west-2 --query 'taskDefinition.revision' --output text)

aws ecs list-tasks --cluster cms-staging-simulation --region us-west-2 --output text \
  | tr '\t' '\n' | grep -v '^$' \
  | xargs -I {} aws ecs describe-tasks --cluster cms-staging-simulation \
      --tasks {} --region us-west-2 \
      --query 'tasks[].{arn:taskArn,def:taskDefinitionArn,status:lastStatus}' \
      --output text \
  | grep -F ":task-definition/cms-staging-fwe-agent:$CURRENT_REV"
```

Pass condition: at least one line printed, with `RUNNING` (or `PROVISIONING`
→ `RUNNING` on a re-poll) and the task-definition ARN ending
`:cms-staging-fwe-agent:$CURRENT_REV`. Zero lines means the sim did not
launch — inspect `simulation_lambda` CloudWatch logs before proceeding.

## Step 3 — Invoke 3 INERT routines from the CMS UI (USER-REQUIRED)

**USER**: In the same browser session, on the vehicle detail page, open the
**Diagnostics** tab. The top of the tab shows the status bar (connection, open
faults, last scan, and the **Run diagnostic scan** and **Dispatch to service**
buttons), then **Self-tests you can run here**. For each of the three INERT
routines listed below, click **Run** under the self-tests and confirm. When the
run finishes, click **View** on its row in the **Diagnostic sessions** table: the
session detail opens in a dialog over the page, showing the result with its named
renderer. Close it with **Close**. The rest of the tab stays in place behind it.

| Routine | Safety class | Expected renderer `data-testid` |
|---|---|---|
| `lamp_self_check` | INERT | `lamp-self-check-renderer` (8-cell 4×2 grid) |
| `cell_balance_check` | INERT | `cell-balance-check-renderer` (one bar per cell) |
| `pack_isolation_test` | INERT | `pack-isolation-renderer` (resistance vs threshold) |

**USER falsifiable observation per row**: within ~5 seconds of the run
finishing, the session's row shows its worst verdict, and the named renderer's
DOM appears in the session dialog. Seeing `session-log-response-drawer-*` instead of the named
renderer is a failure (registry did not resolve, or schema validation failed
sidecar-side) — inspect Step 4a's DDB row for that command before assuming
the UI is at fault. **Opening or closing a session dialog does not abort the run** —
polling continues and results materialize even if you re-open the session after a
delay.

**Do not click Run on `abs_pump_cycle`, `evap_leak_test`, or `o2_heater_check`.**
Those are STATIONARY and the button is disabled for this persona — but the
server has no STATIONARY-specific gate (only SERVICE_ONLY refuses fleet-op
outright). Bypassing the UI disable via curl would run the routine, which
defeats the purpose of the safety class this smoke is trying to prove.

**Rate-limited invocations are now auto-retried.** The sidecar holds a
per-ECU-slot token bucket (`realtime_telemetry_simulator.py:1156-1168`). Three
quick invocations may produce transient `RATE_LIMITED` responses, which the client
now retries up to 3 times, honouring the returned `retry_after_ms`. From the
operator's perspective, a run simply takes a moment longer — no pacing required.
If all 3 attempts are refused, the row lands on `Rate limited` with a manual retry
button, because at that point it is no longer transient and hiding it would mean
the run silently never happened.

**CORRECTED 2026-09-24 — the blocker below is RESOLVED. Expect all three to render
correctly, and treat a wrong render as a REAL failure.**

The previous revision of this step read: *"Known blocker as of 2026-09-23 — expect
`lamp_self_check` and `cell_balance_check` to render wrong… `pack_isolation_test` is the
only one of the three INERT pilots whose result survives intact."* That was accurate when
written and is now false.

`issues/2026-09-23-iot-rule-destroys-arrays-in-sovd-responses/` is **RESOLVED** —
committed, deployed to staging and live-verified on 2026-09-23. It was two stacked
defects: the IoT rule omitted `aws_iot_sql_version` (defaulting to the legacy
`2015-10-08`, which empties top-level arrays, drops nested ones and flattens nested
objects), and underneath it `_persist_response_payload` passed raw floats to DynamoDB
inside a non-fatal `except`, silently dropping the whole response attribute for the 3 of 6
pilots that emit floats. Post-fix, Step 4a/4b confirmed all three INERT pilots carry
`verdict` + a complete `result`, `schema errors: none`, stored verdict == recomputed
(`lamp_self_check` `out_of_spec`, `cell_balance_check` `in_spec`, `pack_isolation_test`
`in_spec`).

**So the pass condition for this step is now unconditional**: all three rows show their
named renderer and a verdict banner in the detail view. A `session-log-response-drawer-*`
fallback, an empty renderer, or a verdict contradicting the drawn payload is a genuine
regression — do not write it off as the known blocker. Re-read Step 4a's DDB row for that
command before concluding the UI is at fault.

This step also now exercises `issues/2026-09-23-diagnostics-session-log-never-populates/`
(P2), whose fix is deployed to staging but **never live-verified** — it is browser-only.
`sovdScanClient.ts`'s body key was `session_id` where the backend reads `sessionId`, and
the session-less `runRoutine` export was deleted. A populated session detail here, and a
non-empty `evidence.sessionId` at Step 6, is that issue's proof.

## Step 4 — DDB verify per invocation (AGENT-RUNNABLE)

### 4a. All three INERT invocations produced rows with `verdict` + `result`

```bash
aws dynamodb scan --table-name "$COMMANDS_TABLE" \
  --filter-expression '#v = :vin AND attribute_exists(#r.verdict)' \
  --expression-attribute-names '{"#v":"vehicleId", "#r":"response"}' \
  --expression-attribute-values "{\":vin\":{\"S\":\"$VIN\"}}" \
  --projection-expression 'commandId, routineId, session_id, #r.verdict, #r.#res' \
  --expression-attribute-names '{"#v":"vehicleId", "#r":"response", "#res":"result"}' \
  --region us-west-2 \
  --max-items 12 \
  --output json \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
rows = d.get('Items', [])
by_routine = {}
for r in rows:
    # Corrected 2026-09-23: the routine id is a TOP-LEVEL 'routineId' attribute.
    # The sidecar does not emit 'routine_id' inside the response map, so the
    # earlier 'response.routine_id' grouping key matched nothing and Step 4a
    # could never pass, even on a healthy row.
    rid = r.get('routineId', {}).get('S')
    if rid: by_routine[rid] = r
inert = {'lamp_self_check', 'cell_balance_check', 'pack_isolation_test'}
missing = inert - by_routine.keys()
if missing:
    print('FAIL: no DDB row for:', sorted(missing)); sys.exit(1)
for rid in sorted(inert):
    row = by_routine[rid]
    r = row['response']['M']
    verdict = r.get('verdict', {}).get('S', '?')
    has_result = 'result' in r and r['result'].get('M') is not None
    print(f'{rid:22s} verdict={verdict:12s} has_result={has_result}')"
```

Pass condition (against `VEH-MRDN-0001`, using Step 1b's expected values):

```
cell_balance_check     verdict=in_spec      has_result=True
lamp_self_check        verdict=out_of_spec  has_result=True
pack_isolation_test    verdict=in_spec      has_result=True
```

A row with `verdict` but `has_result=False` means the sidecar computed a
verdict from a payload it then dropped — that is a T2.1 handler bug in
`realtime_telemetry_simulator._handle_sovd`, not a UI bug.

A missing row means the sidecar never received the command (MQTT path
broken, or fwe-agent not running on rev ≥ 30) or never responded.
`aws logs tail /aws/lambda/cms-staging-commands-* --since 10m` will show
which.

### 4b. Cross-check one row's verdict against the schema

Pick any of the three routines and one row from 4a:

```bash
RID="lamp_self_check"
RESULT_JSON=$(aws dynamodb scan --table-name "$COMMANDS_TABLE" \
  --filter-expression '#v = :vin AND #r.routine_id = :rid' \
  --expression-attribute-names '{"#v":"vehicleId","#r":"response"}' \
  --expression-attribute-values "{\":vin\":{\"S\":\"$VIN\"},\":rid\":{\"S\":\"$RID\"}}" \
  --projection-expression '#r.#res' \
  --expression-attribute-names '{"#v":"vehicleId","#r":"response","#res":"result"}' \
  --region us-west-2 --max-items 1 --output json \
  | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)['Items'][0]['response']['M']['result']))")

cd ~/connected-mobility-guidance-on-aws/services
python3 -c "
import json, sys; sys.path.insert(0,'.')
from _shared.routine_result_schemas import validate_result, ROUTINE_RESULT_SCHEMAS as S
# DDB Map form → plain Python dict:
def unwrap(m):
    if isinstance(m, dict) and set(m.keys()) & {'S','N','BOOL','M','L','NULL'}:
        for k in ('S','N','BOOL','NULL'):
            if k in m: return {'N': float(m['N'])}.get(k, m[k])
        if 'M' in m: return {k: unwrap(v) for k,v in m['M'].items()}
        if 'L' in m: return [unwrap(x) for x in m['L']]
    return m
result = unwrap(json.loads('''$RESULT_JSON'''))
errs = validate_result('$RID', result)
recomputed = S['$RID']['verdict_from_result'](result)
print('schema errors:', errs or 'none')
print('recomputed verdict:', recomputed)"
```

Pass condition: `schema errors: none` and the recomputed verdict matches the
`verdict` string from step 4a. A mismatch means the sidecar's schema copy has
drifted from the deployed `_shared/` overlay — that is a T2.3 packaging bug
(the class of defect that shipped in `issues/2026-09-13-routine-sims-omitted-from-sidecar-image/`).

## Step 5 — Verify STATIONARY rows disabled in the UI (USER-REQUIRED)

**USER**: Still on the Diagnostics tab as the fleet-operator, expand **What a
technician can run at the shop** (it starts collapsed). Then, for each of these
three routines, open DevTools and inspect the DOM:

| Routine | Safety class | Expected `data-testid` |
|---|---|---|
| `o2_heater_check` | STATIONARY | `run-routine-disabled-o2_heater_check` |
| `evap_leak_test` | STATIONARY | `run-routine-disabled-evap_leak_test` |
| `abs_pump_cycle` | STATIONARY | `run-routine-disabled-abs_pump_cycle` |

Pass condition per row: the button is present, carries the `disabled`
attribute, and matches the `data-testid` above. Its `aria-describedby` names text
that is **visible in the DOM as static text** (not a hover-only tooltip). Since FG4
(2026-09-25) that text is the single line shown once for the whole STATIONARY group,
directly under the group's precondition sentence: the id resolves to the `<span>`
inside the `data-testid="stationary-note"` element (the span itself has no testid).
It reads:

> *"Not available for remote fleet operation."*

> **CHANGED 2026-09-25 (FG4, operator direction).** The explanation used to repeat
> under every STATIONARY button as "Requires the vehicle to be at rest and confirmed
> by the sidecar. Not available for remote fleet operation." The first sentence
> duplicated the server's precondition line, and a hybrid vehicle showed it 11 times.
> It now appears once per group and the routines sit in columns. A per-row copy
> coming back is a regression of that decision; a disabled button with no
> `aria-describedby`, or one pointing at a tooltip, is a regression of the
> prohibition rule below.

> **CORRECTED 2026-09-24 — the explanation is static text, not a tooltip.** Earlier
> revisions of this step asserted it "displays the tooltip text". Operator observation
> during the 2026-09-24 run: the explanation rendered beneath each disabled button as
> **visible copy**, not on hover (the per-button placement is the pre-FG4 layout; see the
> 2026-09-25 note above). The pass condition is that the text is **present in the DOM
> without user interaction**. Where it sits may change, but it must stay static text:
> never a popover, tooltip or the button's `disabledReason`, per spec § Constraints
> ("Tooltips split by kind"). (An earlier wording of this note allowed a later move to a
> popover; that contradicted the spec and was removed 2026-09-25.)

**A STATIONARY row that renders as `data-testid=run-routine-<id>` (enabled)
for this persona is a safety regression, not a smoke failure — stop and file
it.** The UI-side gate (the `cls === 'STATIONARY'` branch in
`diagnostics/RoutineOffering.tsx`, since Task 3.4) is the only
enforcement point for this persona (Lambda has no STATIONARY-specific gate
for fleet-op — see summary of the persona-matrix issue for the structural
gap this leaves latent, and `services/commands/commands_lambda.py:684–748`
for why).

## Step 6 — Dispatch to service (USER + AGENT)

**USER**: On the Diagnostics tab, click **Dispatch to service**. In the modal,
pick any dealer from the picker and confirm. The modal closes and the
Diagnostics tab remains open. Note the `dealerId` you picked.

**AGENT-RUNNABLE verification** — a new DMS repair order exists, initiated by
CMS, carrying the session and at least one routine run:

```bash
aws dynamodb scan --table-name "$RO_TABLE" \
  --filter-expression 'vehicle_vin = :vin AND initiated_by = :src' \
  --expression-attribute-values "{\":vin\":{\"S\":\"$VIN\"},\":src\":{\"S\":\"cms_booking\"}}" \
  --projection-expression 'ro_id, initiated_by, dealer_id, evidence' \
  --region us-west-2 \
  --output json \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
items = d.get('Items', [])
if not items:
    print('FAIL: no dms-staging-repair-orders row with initiated_by=cms_booking for this VIN'); sys.exit(1)
# Latest by ro_id string sort (v1.5 mints ro_id with a millisecond prefix)
items.sort(key=lambda x: x.get('ro_id',{}).get('S',''), reverse=True)
top = items[0]
ev = top.get('evidence', {}).get('M', {})
session_id = ev.get('sessionId', {}).get('S', '')
routines_run = ev.get('routinesRun', {}).get('L', [])
print('ro_id            :', top.get('ro_id',{}).get('S',''))
print('dealer_id        :', top.get('dealer_id',{}).get('S',''))
print('evidence.sessionId:', session_id or '(EMPTY — v1.5 D9 predicate fails)')
print('routinesRun count :', len(routines_run))
if routines_run:
    first = routines_run[0].get('M', {})
    print('first routineId  :', first.get('routineId', {}).get('S', ''))"
```

Pass condition:

- exactly one new RO with `initiated_by = cms_booking`, or an existing one
  from a prior run (latest by `ro_id` is the one dispatch just wrote)
- `dealer_id` equals the dealer picked in the UI
- `evidence.sessionId` is non-empty — the load-bearing predicate of v1.5 T5.2's
  auto-open-Diagnostics gate on the DMS side
- `routinesRun count >= 1`
- `first routineId` is one of the three INERT routines run in Step 3

A row with `initiated_by = cms_booking` but empty `evidence.sessionId` is a
regression of v1.5 T2.7 — the DMS-side handler received the dispatch but did
not correlate on the CMS session, and the DMS Diagnostics tab will not open by
default on that RO.

## What this smoke does NOT verify, and where that coverage lives

- **Full DMS UI walkthrough** (login as `service-advisor` / `dms-technician`,
  open the dispatched RO, see the Diagnostics tab pre-selected, invoke a
  STATIONARY routine, add a note, close the RO). This needs a persona this
  smoke does not exercise, per user direction. Renderer parity across CMS
  and DMS is separately guarded by `scripts/verify_renderer_parity.py` in
  DMS (10/10 byte-identical at the time of writing) and by
  `.github/workflows/lint.yml`, so a UI-side regression on the DMS renderer
  set would fire independently of this smoke.

- **All three verdict bands per renderer** (18 combinations across 6
  renderers × `in_spec` / `marginal` / `out_of_spec`). That coverage is
  unit-level and already exists: T6.1's `routine-renderers-verdict.test.tsx`
  runs those 18 cases in both repos, with every payload and expected verdict
  generated from `ROUTINE_RESULT_SCHEMAS` by
  `services/_shared/export_verdict_fixtures.py`.

- **The 13 non-pilot routines.** v1 shipped six schemas + six renderers; the
  other 13 render via `RawDrawer` fallback. Adding one is one schema + one
  producer + one renderer; the pattern is in
  `services/_shared/routine_result_schemas.py` and the sibling test files.

- **Server-side denial for a fleet-operator invoking a STATIONARY routine
  via curl bypass**. The Lambda has no STATIONARY-specific gate for fleet-op
  (STATIONARY relies on the sidecar's vehicle-state check, which is
  behavioural not authorization). This is a documented gap — see
  `issues/2026-09-12-diagnostics-persona-matrix-not-group-enforced/summary.md`
  — and is why Step 5 verifies only the UI disable, not the API refusal.

- **Any write into `cms-{stage}-storage-dtc-history` from the SOVD path.**
  Added 2026-09-24 so an empty table is not misread as a failure of this smoke.
  `_write_dtc_history_rows()` in `services/commands/command_response_handler.py`
  writes nothing and has four independent defects (empty `vehicle_id` → empty
  partition key; the item omits `timestamp`, the table's RANGE key; the
  `update_item` fallback keys on `dtcCode`, not in the schema; and it emits
  `dtcCode` where all 200 sampled live rows and every reader use `dtcId`).
  `cms-staging-storage-dtc-history` holds **0 rows with `source='sovd'`**.
  Tracked as `issues/2026-09-23-sovd-dtc-history-write-path-never-worked/`
  (**P2, open by design**) — fixing it needs a row-contract decision across four
  existing producers whose live data already disagrees, which is spec work, not a
  patch. **No pass condition in this playbook depends on it.** Note the
  transport half *is* fixed: post-`2016-03-23` a live scan delivers all 9 ECUs
  each carrying its `dtcs` key, and on `VEH-MRDN-0001` every ECU legitimately
  reports `dtcs: 0`, so zero rows is also the correct outcome for that vehicle
  independent of the write-path defect.

## Cleanup (AGENT-RUNNABLE + USER)

- **USER**: In the CMS UI simulation panel, stop the trip. That triggers
  `simulation_lambda`'s stop path, which drains the fwe-agent task.
- **AGENT**: confirm the task drained:
  ```bash
  aws ecs list-tasks --cluster cms-staging-simulation --region us-west-2 \
    --query 'length(taskArns)'
  ```
  Pass condition: `0`. A non-zero here means a fwe-agent task orphaned; the
  drain script (`deployment/scripts/drain_stale_fwe_agents.sh`) can be
  re-run against any old-revision leaks.

## Result recording

At completion, record which of the pass conditions passed. Owed back to the
PO before either `[!]` flag flips to `[x]`:

- Step 0 check outcome (passed / bump-required-and-run)
- Step 1a: 6/6 routines listed with correct safety class (yes/no)
- Step 1b: expected verdicts printed (yes/no)
- Step 1c: `resolveVehicleByVin` OK (yes/no)
- Step 2: fwe-agent task RUNNING on rev ≥ 30 (yes/no)
- Step 3: 3 INERT rows rendered with named renderer (yes/no per row)
- Step 4a: 3 DDB rows with `verdict` + `result` (yes/no per row, verdict
  values)
- Step 4b: schema round-trip clean, recomputed verdict matches stored (yes/no)
- Step 5: 3 STATIONARY rows show `run-routine-disabled-*` `data-testid`
  (yes/no per row)
- Step 6: DMS RO row with `initiated_by=cms_booking` + non-empty
  `evidence.sessionId` + `routinesRun[0].routineId` matching Step 3
  (yes/no per field)

Only after the user records those outcomes may the two `[!]` flags be
flipped to `[x]`:

- `.kiro/specs/2026-09-10-cms-dms-sovd-diagnostic-sessions-v1.5/tasks.md`
  Group 6 "Smoke test playbook against staging"
- `.kiro/specs/2026-09-10-cms-sovd-routine-result-contracts/tasks.md`
  Group 6 "End-to-end smoke against staging"
