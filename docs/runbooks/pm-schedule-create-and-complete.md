# Runbook: create and complete a preventive-maintenance schedule

Spec: `2026-09-02-cms-fleet-intelligence-v1` § D6 (T6.1 clause 5).
Applies to the PM surfaces added by that spec: `components/pm/` in the CMS UI and the
`fleet-intelligence` handler behind `/api/v1/fleet-intelligence/pm/*`.

## Prerequisites

- The `cms-{stage}-ui` stack is deployed **after** that spec landed, so the
  `fleet-intelligence` subtree and its Lambda exist.
  <!-- verify: aws apigateway get-resources --rest-api-id <id> --region <region> --query "items[?pathPart=='fleet-intelligence']" -->
- The `cms-{stage}-pm-schedules` table exists (created by `cms-{stage}-storage`).
  <!-- verify: aws dynamodb describe-table --table-name cms-<stage>-pm-schedules --region <region> --query 'Table.TableStatus' -->
- You are signed in to the CMS UI with a group that grants write access. A read-only
  group can view schedules and compliance but not create or complete them.

## Create a schedule (UI)

1. Navigate to **Compliance → PM Compliance**.
2. Choose **Create schedule**.
3. Pick a **basis**. This is the decision that matters; the rest follows from it:
   - **Mileage** — supply the interval (e.g. 5,000) and the last-performed odometer.
     Due when the vehicle's odometer reaches `lastPerformedMileage + intervalValue`.
   - **Engine hours** — supply the interval and last-performed engine hours. Due when
     `engine_hours_total` reaches the sum. Use this for equipment whose wear tracks
     runtime rather than distance.
   - **Calendar** — supply an interval in days and the last-performed date. Due when
     that many days have elapsed. Due-today counts as due.
4. Enter a **task code**. Codes are VMRS-*shaped* in a synthetic namespace (e.g.
   `CFI-005-001`); no VMRS extract exists in this repo, deliberately — see the T1.2
   licensing finding in `docs/tech.md`.
5. Save. The row appears under **Due**, **Overdue** or **Upcoming** according to the
   current reading, and carries a provenance badge.

## Complete a schedule (UI)

1. Find the schedule in the list and choose **Complete**.
2. The completion posts `vehicleId` and an ISO-8601 **date** (`YYYY-MM-DD`). Both are
   required — the handler returns 400 without them.
3. On success the list reloads and the compliance rollup recomputes. On failure a
   dismissible error banner appears; a completion that silently did nothing is a bug,
   not an expected state.

## Read compliance

**Compliance → PM Compliance** shows the rate for the selected fleet and window.
Compliance is **computed at read time** from schedules and completions, never stored:
it is the share of in-window schedules completed on or before their due point. A
schedule with no completion counts as non-compliant.

The API returns `rate` as a **0–1 fraction**; the UI renders 0–100%. That conversion
happens in exactly one place (`toPmComplianceRollup()` in `components/pm/types.ts`) —
do not add a second one.

## CLI equivalents

```bash
API=https://<api-id>.execute-api.<region>.amazonaws.com/prod
TOKEN=<cognito-id-token>

# List schedules for a fleet
curl -s -H "Authorization: Bearer $TOKEN" \
  "$API/api/v1/fleet-intelligence/pm/schedules?fleetId=<fleet-id>"

# Compliance for a fleet and window
curl -s -H "Authorization: Bearer $TOKEN" \
  "$API/api/v1/fleet-intelligence/pm/compliance?fleetId=<fleet-id>&windowStart=2026-01-01&windowEnd=2026-12-31"

# Create a mileage-based schedule
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"vehicleId":"VEH-001","fleetId":"<fleet-id>","basis":"mileage",
       "intervalValue":5000,"taskCode":"CFI-005-001",
       "lastPerformedMileage":50000,"currentOdometer":55000}' \
  "$API/api/v1/fleet-intelligence/pm/schedules"

# Record a completion
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"vehicleId":"VEH-001","completedDate":"2026-09-03"}' \
  "$API/api/v1/fleet-intelligence/pm/schedules/<schedule-id>/complete"
```

## Troubleshooting

| Symptom | Cause |
|---|---|
| `400 vehicleId and completedDate are required` | Completion body missing either field. `vehicleId` is the table partition key, so it cannot be inferred from the schedule id alone. |
| `400 completedDate must be ISO-8601 (YYYY-MM-DD)` | A full datetime was sent. Send the date only. |
| `400 fleetId is required` | The compliance route needs an explicit fleet; there is no implicit "all fleets" rollup. |
| Schedule list empty when rows exist | The list reads the `data` key. A caller expecting `schedules` sees an empty list — see `issues/2026-09-03-fleet-intelligence-ui-api-contract-mismatch/`. |
| Every projection says `simulated` | Expected today. Current readings come from the simulator, and provenance inherits the weakest input, so projections are honestly labelled `simulated` until real telemetry arrives. |
| Compliance looks low right after seeding | Seeded schedules generally have no completions, and a schedule without a completion is non-compliant by definition. |
