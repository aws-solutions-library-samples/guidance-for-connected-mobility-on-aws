# Fleet Intelligence

Fleet Intelligence provides cost-per-mile (CPM) and lifecycle analysis for vehicle fleets by reading curated cost data from the Automotive Data Platform (ADP) via cross-account Athena queries.

Spec: `.kiro/specs/2026-09-10-cms-fleet-intelligence-adp-consumer/spec.md`

## Architecture

The Fleet Intelligence service is a Tier 1 rendering surface — deterministic arithmetic only, no LLM SDK in any path. The reasoning lives in `cpm.py` / `lifecycle.py` / `pm.py`; `index.py` only reads DynamoDB, dispatches, and shapes the HTTP response.

### Data Flow

1. **ADP read** (`adp_source.fetch_cost_rows`):
   - Reads ADP staging or prod database via cross-account Athena
   - Single paginated DynamoDB Scan of the CMS `storage-vehicles` table to build `{vin: (vehicleId, fleetId)}` map
   - Returns per-vehicle cost rows with exact 8-key shape: `{vehicleId, yearMonth, maintenanceCost, fuelCost, totalMiles, provenance, make, fleetId}`

2. **Provenance attachment** (`provenance.py`):
   - Every row carries a `provenance` field drawn from: `"simulated"`, `"measured"`, `"derived"`, or `"reference"`
   - Weakest-input inheritance: simulated data poisons everything (simulated cost figures yield simulated CPM)
   - No default; fail-closed validation raises if unset

3. **CPM & lifecycle calculation** (`cpm.py` / `lifecycle.py`):
   - Deterministic grouping and aggregation (portal-wide, by fleet, by vehicle)
   - Sell-timing analysis via month-by-month breakeven computation
   - All arithmetic is deterministic; no LLM involved

4. **Preventive Maintenance** (`pm.py`):
   - Reads schedules from `PM_SCHEDULES_TABLE_NAME` DynamoDB table
   - Computes next-due date and compliance status
   - Supports mileage and engine-hours based schedules

## Athena Integration

### SQL Query Fixture

The query is parameterised via `ExecutionParameters` (positional `?` binds). The fixture lives in `adp_cost_rows.sql`.

**Critical contract**: every parameter value MUST be pre-quoted as a SQL literal (e.g., `"'2025-09-10'"` not `"2025-09-10"`), because Athena evaluates substituted values as SQL expressions, not opaque bindings. An unquoted date is substituted as arithmetic and causes `TYPE_MISMATCH`.

**ExecutionParameters order** (`3 * (1 + N)` binds where N = number of vins):
```
[window_start] + [vin_1, vin_2, ..., vin_N]  (maint CTE)
+ [window_start] + [vin_1, vin_2, ..., vin_N]  (energy CTE)
+ [window_start] + [vin_1, vin_2, ..., vin_N]  (charge CTE)
```

Validation is the injection boundary — vincharset and date format are validated by `_validate_vin()` and `_validate_date()` before substitution.

**Bind count guard**: `_MAX_BIND_COUNT = 3000` prevents runaway large fleets. Total binds = `3 * (1 + len(allowed_vins))`. Athena supports up to 3003; this guard at 3000 is conservative.

### DynamoDB Vehicle Map

A single paginated `Scan` of `cms-{stage}-storage-vehicles` returns every row with all three attributes (`vehicleId`, `vin`, `fleetId`). This one read provides:
- The vin allowlist for the SQL query
- The vin → vehicleId translation (for Athena join)
- The vin → fleetId association

This one read replaces the previous vin-index GSI Query + base-table BatchGetItem two-step (decisions.md D2 redesign).

### Fleet Scoping

When `fleet_id` is provided (Group 6 addition):
- It is a **fleetId** (e.g., `flt-meridian-range-001`), NOT a vin and NOT a vehicleId
- Membership is taken from the vehicles table `fleetId` attribute
- An unknown `fleet_id` narrows to zero vins and returns `[]` without calling Athena (correct answer, not a fallback)
- Fleet scoping does NOT reduce Athena cost because ADP's curated tables are unpartitioned; `vin IN (...)` is a row filter, not a partition prune

Measured (back-to-back): with and without fleet scoping on demo data:
- Binds: 213 → 36 (83% reduction)
- Bytes scanned: 33,530,104 → 32,901,875 (1.9% reduction)
<!-- verify: rerun the spec's T6.3 mutation test against live staging -->

## Environment Variables

The FI Lambda reads exactly **9 keys** from the environment (fail-closed on any missing variable):

| Variable | Purpose | Example |
|---|---|---|
| `ADP_STAGE` | ADP database suffix (`staging` or `prod`); validated fail-closed | `staging` |
| `ADP_REGION` | AWS region for Athena client (usually `us-east-1` where ADP lakes live) | `us-east-1` |
| `ATHENA_WORKGROUP` | Athena workgroup name for query execution | `cms-staging-analytics` |
| `ATHENA_OUTPUT_LOC` | S3 URI for query results (`s3://bucket/prefix/`) | `s3://cms-staging-athena-results/fleet-intelligence/` |
| `ADP_DATA_PROVENANCE` | Flip signal between `simulated` (demo) and `measured` (real data); no default | `simulated` |
| `FI_WINDOW_MONTHS` | Rolling window in months for the cost-per-mile routes (`/cpm`, `/cpm/outliers`) | `12` |
| `FI_LIFECYCLE_WINDOW_MONTHS` | Rolling window for lifecycle analysis across both CMS-fleet and ADP-wide scopes; independent from `FI_WINDOW_MONTHS` (cost-per-mile) | `36` |
| `VEHICLES_TABLE_NAME` | DynamoDB vehicles table name (for the vin → vehicleId/fleetId map) | `cms-staging-storage-vehicles` |
| `PM_SCHEDULES_TABLE_NAME` | DynamoDB preventive maintenance schedules table name | `cms-staging-pm-schedules` |

<!-- verify: cd deployment && pytest stacks/tests/test_ui_stack_fleet_intelligence_adp_grants.py -k test_env_keys_are_exactly_the_nine_the_code_reads -->

All 9 variables are threaded by `deployment/stacks/ui_stack.py` into the FI Lambda at synth time. Missing any variable causes the Lambda to raise at cold start (fail-closed).

## API Routes

All routes live under `/api/v1/fleet-intelligence` and require Cognito authorization:

| Method | Route | Purpose | Scope |
|---|---|---|---|
| GET | `/cpm` | CPM table grouped by `oem`/`fleet`/`vehicle` (query param) | CMS fleets |
| GET | `/cpm/outliers` | Vehicles whose CPM is > 1.5× fleet average | CMS fleets |
| GET | `/lifecycle` | Fleet-scoped lifecycle summary (respects `fleetId` query param and ambient FleetFilter) | CMS fleets (default) or `scope=adp` for all ADP vehicles |
| GET | `/lifecycle/{vehicleId}` | Sell-timing analysis for a single vehicle | CMS fleets |
| GET | `/pm/schedules` | All preventive maintenance schedules | CMS fleets |
| GET | `/pm/compliance` | Compliance status across all schedules | CMS fleets |
| POST | `/pm/schedules` | Create a new PM schedule | CMS fleets |
| POST | `/pm/schedules/{id}/complete` | Mark a schedule as complete | CMS fleets |

Every response carries a `provenance` field computed via weakest-input inheritance.

### ADP Scope: All-Vehicle Lifecycle Rollup

`GET /api/v1/fleet-intelligence/lifecycle?scope=adp` returns a precomputed rollup summarizing lifecycle across all ADP vehicles (about 4.7M VINs in staging), available only to `platform-admin` users. Non-admins receive HTTP 403. ADP has no fleet membership, so the `fleetId` parameter is ignored for the ADP scope.

**Computation:**

- The rollup is precomputed on an hourly schedule (EventBridge rule `FleetAdpRollupRefreshRule`) to avoid API Gateway's 29-second timeout. Athena queue delay alone has reached 354s (`issues/2026-09-25-fleet-lifecycle-athena-queue-delay-504/`).
- Fresh rollups are written to `{ATHENA_OUTPUT_LOC}_cache/adp-lifecycle-rollup-v1.json` with a `computedAt` timestamp.
- If the artifact is missing or older than 26 hours, the route returns HTTP 503 `{"error": "rollup not yet computed"}` with the last known `computedAt`.

**Response shape (200 OK):**

```json
{
  "scope": "adp",
  "computedAt": "2026-09-29T14:00:07+00:00",
  "windowMonths": 36,
  "horizonMonths": 36,
  "assumptions": {"purchasePriceUsd": 60000, "straightLineLifeMonths": 120},
  "summary": {
    "totalVehicles": 0,
    "sellRecommendedCount": 0,
    "sellSoonCount": 0,
    "healthyCount": 0,
    "insufficientDataCount": 0,
    "avgMonthsToCrossover": null
  },
  "monthlyTrend": [{"yearMonth": "2023-10", "avgMaintenance": 0.0, "vehicleCount": 0}],
  "cohorts": [{"model": "...", "modelYear": 2024, "vehicles": 0, "sellRecommendedCount": 0, "sellSoonCount": 0, "healthyCount": 0, "insufficientDataCount": 0, "avgMonthlyMaintenance": 0.0, "avgCostPerMile": null}],
  "topCrossovers": [{"vin": "...", "model": "...", "modelYear": 2024, "monthsUntilCrossover": 3, "currentMonthlyMaintenance": 0.0, "rSquared": 0.0, "series": [{"yearMonth": "2026-07", "maintenanceCost": 0.0}]}],
  "evidence": {"queryExecutionIds": ["..."]},
  "provenance": "simulated"
}
```

**Window:** The rollup uses `FI_LIFECYCLE_WINDOW_MONTHS` (default 36) to define the analysis window, independent of the `FI_WINDOW_MONTHS` (default 12) used by cost-per-mile routes. Both the CMS-fleet and ADP-wide lifecycle views read the same environment variable.

**Purchase price assumption:** ADP provides no vehicle purchase price. The rollup assumes a 60,000 USD purchase price depreciated straight-line over 120 months ($500/month). This assumption is included in the response under `assumptions` and displayed in the UI next to every crossover figure.

**UI presentation:** The ADP scope is exposed to admins via a scope toggle in `LifecycleView` ("CMS fleets | All ADP vehicles"). When ADP scope is selected, the top-nav fleet picker is disabled through `FleetFilterContext.pickerLockReason` (ADP has no fleet membership), and it is released when the user switches back or leaves the page. Non-admin users do not see the toggle.

### Lifecycle input cache

`GET /lifecycle` is the CMS landing page, and Athena on-demand queue time is unbounded (up to 354s observed) while API Gateway stops waiting at 29s. The Athena-backed routes therefore read their inputs from a cache (`lifecycle_cache.py`) instead of querying Athena per request:

- **Object:** `{ATHENA_OUTPUT_LOC}_cache/fleet-lifecycle-inputs-v1.json`. It holds portal-wide cost and tire-health rows, and the route narrows them to the requested `fleetId` per request.
- **Refresh:** EventBridge rule `FleetLifecycleCacheRefreshRule`, every 10 minutes, invokes the Lambda with `{"fleetIntelligenceTask": "refresh-lifecycle-cache"}`. The refresh polls Athena every 4s (240s per query); the Lambda timeout is 900s. <!-- verify: grep -n "FleetLifecycleCacheRefreshRule\|rate(10 minutes)" deployment/stacks/ui_stack.py | head -1 -->
- **ADP rollup refresh:** EventBridge rule `FleetAdpRollupRefreshRule`, every 1 hour, invokes the Lambda with `{"fleetIntelligenceTask": "refresh-adp-rollup"}`. The refresh runs 4 concurrent Athena queries (summary, monthly trend, cohorts, top-N crossovers; `adp_lifecycle_rollup.sql`) and polls them every 4s; the Lambda timeout is 900s. <!-- verify: grep -n "FleetAdpRollupRefreshRule\|rate(1 hour)" deployment/stacks/ui_stack.py | head -1 -->
- **Readers:** `/lifecycle` uses cost and tire-health rows for CMS-fleet scope; the ADP rollup is read-only from a precomputed artifact. `/cpm`, `/cpm/outliers` and `/lifecycle/{vehicleId}` use the cost rows only. Each narrows to the requested `fleetId` (and vehicle) per request.
- **Window:** the object records the `FI_WINDOW_MONTHS` it was read with. An object from another window, or one without the field, counts as a miss.
- **Fallback:** a missing, malformed, future-dated, other-window or older-than-6h object falls back to a live Athena read. For `/lifecycle` that read is portal-wide and writes through. For the other three it is the original fleet-scoped cost read and does not write, so the cost pages never depend on the tire-health query.
- **Staleness:** the response carries `computedAt`.
- **Manual refresh:** 
  ```bash
  # CMS-fleet cache
  aws lambda invoke --region us-west-2 --function-name <FleetIntelligenceFunction> \
    --cli-binary-format raw-in-base64-out \
    --payload '{"fleetIntelligenceTask":"refresh-lifecycle-cache"}' \
    --cli-read-timeout 900 /tmp/out.json
  
  # ADP rollup cache
  aws lambda invoke --region us-west-2 --function-name <FleetIntelligenceFunction> \
    --cli-binary-format raw-in-base64-out \
    --payload '{"fleetIntelligenceTask":"refresh-adp-rollup"}' \
    --cli-read-timeout 900 /tmp/out.json
  ```

On a cache miss every CMS-fleet route still queries Athena in the request path and can 504 during a queue spike; the 10-minute refresh exists to keep misses rare. The ADP scope never queries Athena in the request path: a missing or stale artifact is a 503.

## Testing

The module uses dependency injection for both `athena_client` and `ddb_client` to enable unit testing without AWS credentials. Tests pass `botocore.stub.Stubber`-backed clients directly.

```python
from botocore.stub import Stubber

athena_stub = Stubber(boto3.client("athena", region_name="us-east-1"))
ddb_stub = Stubber(boto3.client("dynamodb"))

# Stub responses for run_query and _scan_vehicles_table
# ...

rows = adp_source.fetch_cost_rows(
    vehicle_ids=None,
    window_months=12,
    athena_client=athena_stub,
    ddb_client=ddb_stub,
)
```

The live tests (`LIVE_TESTS=1`, for example `tests/test_adp_rollup_live.py`) run as your own principal, which is usually a Lake Formation admin. They check the SQL against real data, not the role's grants. To check the grants, invoke the deployed Lambda. `deployment/stacks/tests/test_ui_stack_fleet_intelligence_adp_grants.py::test_every_adp_table_the_code_reads_is_granted` checks that every `adp_{stage}_<db>.<table>` the code reads has a Glue and S3 grant. The ADP-side Lake Formation grant is pinned in ADP's `platform-foundation/tests/test_governance_cms_share.py`.

## Known Limitations

- **No partition pruning**: ADP's curated tables are unpartitioned, so fleet-scoped queries do not
  reduce bytes scanned significantly. Measured 2026-09-13: scoping cut vin binds 83% (213 → 36) but
  bytes scanned only 1.9% (33,530,104 → 32,901,875). `vin IN (...)` is a row filter, not a partition
  prune, so scoping buys correctness of the returned set — **not** cost savings.
  <!-- verify: grep -n "ADP curated partitioning" .kiro/specs/2026-09-10-cms-fleet-intelligence-adp-consumer/decisions.md -->
- **`fleet_id` is NOT an access control.** It selects which rows are returned; it does not decide
  who may ask. These routes carry a Cognito authorizer (authentication) but the handler reads no
  identity at all — no `cognito:groups`, no `custom:fleetIds` — so any authenticated caller may pass
  any `fleetId`. Do **not** treat this as a tenant boundary, and do not copy the pattern into a new
  endpoint expecting it to isolate anything. Tracked as follow-on
  `Fleet Intelligence scope is not authz` P2; a fix must gate `groupBy=fleet` as well, since that
  already returns every fleet broken out.
  <!-- verify: grep -cE "claims|requestContext|cognito:groups|custom:fleetIds" services/fleet_intelligence/index.py -->
- **Maintenance records**: read-only. PM schedules are written to DynamoDB, not sourced from ADP.
- **Provenance is module-level**: set at Lambda cold start via `ADP_DATA_PROVENANCE` env var. Cannot flip per-request.
- **`tire_health` is now read by `GET /lifecycle`** (v1 spec `2026-09-14-cms-fleet-lifecycle-view`; supersedes `2026-09-10-adp-consumer` § D11 deferral). Two Athena scans per cache refresh (cost rows + `tire_health`), not per request; see "Lifecycle input cache".

## Cross-Account Access

FI requires Lake Formation grants from the ADP account. See [Fleet Intelligence prerequisites](../../docs/DEPLOYMENT.md#fleet-intelligence-prerequisites) in the deployment guide.
