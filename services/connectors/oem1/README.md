# OEM1 Fleet Lifecycle Management

This directory contains the OEM1 connector infrastructure, admin Lambdas, and supporting services for managing fleet-scoped vehicle enrollment, unenrollment, and status synchronization with Ford Pro's cloud-fed OEM1 platform.

## Overview

The OEM1 fleet lifecycle management system enables fleet managers to bulk-enroll vehicles, manage subscriptions, and monitor enrollment status through a set of admin APIs backed by serverless Lambdas. The system operates in three layers:

1. **Phase 1 (Connector)** — Background gRPC streaming consumer + manifest-driven telemetry transform
   - `main.py`, `connector.py` — Kafka consumer + gRPC client
   - `token_supplier.py` — OEM1 OAuth token management (refresh on 401)
   - `seed_vehicles.py` — CLI tool for bulk vehicle seeding

2. **Phase 2 (Single-VIN Admin)** — Single vehicle add via UI + API
   - `admin_add_vehicle/` — Admin proxy Lambda for `/admin/oem1/add-vehicle`

3. **Phase 3 (Bulk Lifecycle)** — New fleet-scoped admin operations
   - `admin_bulk_enroll/` — Bulk enroll with pre-flight validation + driver assignment
   - `admin_bulk_unenroll/` — Bulk unenroll with soft/hard-delete modes
   - `admin_enrollment_poller/` — EventBridge-scheduled poller that drives enrollments to terminal states
   - `admin_status_sync/` — EventBridge-scheduled background status sync (every 15 min)
   - `admin_refresh_vehicle_status/` — Manual per-VIN or batch status refresh
   - `admin_enroll_quota/` — Query remaining hourly enroll quota (4 requests/hour/customer)
   - `admin_preflight/` — Pre-flight capability + eligibility check via `liteCheck`
   - `admin_list_enrolled/` — Query OEM1's full enrolled roster for reconciliation

## Architecture

### Data Model

See `docs/architecture/oem1-fleet-lifecycle.md` for the data model diagram and sequence flows.

Fleet-scoped enrollment state is tracked across three DynamoDB tables:

| Table | Purpose | Key Structure |
|-------|---------|---|
| `cms-{stage}-storage-fleets-*` | Fleet metadata | PK: `fleetId` → includes `data_source` (`'vehicle-telemetry' \| 'cloud-telemetry'`) + optional `transform_manifest_id` |
| `cms-{stage}-storage-vehicles-*` | Vehicle lifecycle + OEM1 status fields | PK: `vehicleId` → 8 new OEM1 fields track enrollment status, FCS codes, readiness, and activation date |
| `cms-{stage}-storage-oem1-enrollment-requests-{region}-{account}` | Enrollment request history | PK: `request_id` (from OEM1) → tracks submitted VINs, SKU, fleet, submitter, status, and driver assignments; 90-day TTL |

**Note on `data_source` enum**: Legacy values `'onboard-fwe'` and `'cloud-oem1'` are accepted by Lambda M3 checks during a transitional dual-read phase; new code writes `'vehicle-telemetry'` and `'cloud-telemetry'` respectively. See spec `2026-06-09-cms-data-source-model-refactor` for details.

### Admin Lambdas

All admin Lambdas are configured with:
- **Auth**: Cognito User Pool authorizer + per-route gate matrix supporting `platform-admin` (cross-fleet authority) and `fleet-operator` (per-fleet authority via `custom:fleetIds` JWT claim). See `.kiro/specs/2026-06-09-cms-fleet-manager-cognito-role/spec.md` § 1 for the per-route gate matrix.
- **Pattern**: TokenSupplier singleton + one-shot 401 retry + sanitized error envelope (reused from Phase 2 `admin_add_vehicle`)
- **Error mapping**: 4xx OEM1 errors → passthrough status + sanitized body; 5xx → 502; timeout → 504; unknown → 500
- **Logging**: Structured CloudWatch INFO log lines via `aws_lambda_powertools` Logger for all write operations (actor, fleet_id, action, vin_count, outcome)

#### Write-Path Lambdas

**`admin_bulk_enroll`** — POST `/admin/oem1/bulk-enroll`
- Validates batch (1-500 VINs, single SKU)
- Runs server-side `liteCheck` batched 10/req (pre-flight gate)
- Calls OEM1 `/enrollment/v2/enroll` (singular `product` per Postman spec)
- Persists enrollment-requests row + idempotent vehicle + fleet-enrollment writes
- Returns 202 + enroll response OR pre-flight failures
- Implements clientRequestId dedup for idempotency on client retries

**`admin_bulk_unenroll`** — POST `/admin/oem1/bulk-unenroll`
- Validates batch + heterogeneous-SKU rejection (v1)
- Calls OEM1 `/enrollment/v2/unenroll` (plural `products` array per Postman)
- Marks rows UN_ENROLL_IN_PROGRESS (poller owns terminal cleanup)
- Persists hard-delete flag for poller's consumption
- Returns 202 + unenroll response

#### Read-Only Lambdas

**`admin_preflight`** — POST `/admin/oem1/preflight`
- Synchronous capability check (no OEM1 mutation)
- Calls `vehicleData(vins, ['modelInfo'])` + `liteCheck(vins, [sku])`
- Returns per-VIN capability + readiness + reason

**`admin_enroll_quota`** — GET `/admin/oem1/enroll-quota`
- Queries enrollment-requests GSI: `(customer_id, submitted_at)` for ENROLL rows in last 60 min
- Returns `{remaining, submissions_in_last_hour, next_quota_reset_at}`

**`admin_refresh_vehicle_status`** — POST `/admin/oem1/refresh-status`
- Per-VIN 60s rate-limit (checked via `oem1_status_refreshed_at`)
- Calls `status/latest` + `vehicleState` for batch
- UPDATEs vehicle rows with fcs_code, status_message, enrollment_status, readiness_summary, refreshed_at

**`admin_status_sync`** — EventBridge schedule (every 15 min)
- Scans vehicles with `oem1_status_refreshed_at < now - 1h`
- Paginated `status/latest` calls (1000 VINs/page)
- Emits `OEM1StatusDrift` events on terminal-state transitions
- Emits CloudWatch metrics (vehicles_refreshed, drift_detected, duration_ms)

**`admin_list_enrolled`** — GET `/admin/oem1/list-enrolled`
- Reads full enrolled roster from OEM1 via `status/latest` (no VIN filter)
- Returns for UI reconciliation: "Found N at OEM1; M in CMS; K missing"

#### System Lambdas (EventBridge-triggered)

**`admin_enrollment_poller`** — EventBridge schedule (every 1 min; configurable via CDK context)
- Scans enrollment-requests: `terminal_at IS NULL AND submitted_at > now - 8d`
- Calls OEM1 `status/latest` per request_id batch (≤100/call)
- **Consumer Action Policy** (§ 4.1 in spec): maps fcs_code → enrollment_status transitions
- **OQ16 Surface-Immediately Policy** (rev 3): TC9999/8030/8040 → mark FAILED immediately (no auto-retry)
- Terminal success (code 3) → set COMPLETED + subscription_service_activation_date
- Terminal unenroll (code 7) → consult hard_delete flag: soft-remove (UPDATE Inactive) OR hard-delete (DELETE vehicle + fleet-enrollment; NO cascade to trips/events)
- Terminal failure + 8020 → emit `OEM1EnrollmentTimeout` EventBridge event for ops
- Reserved concurrency = 1 (serializes polling, bounded load)
- Emits CloudWatch metrics (requests_polled, terminal_completed, terminal_failed, duration_ms)

## Consumer Action Policy

The enrollment poller follows the OEM1 Consumer Action policy for fcs_code → enrollment_status mapping:

| fcs_code | Meaning | Action |
|----------|---------|--------|
| 0, 1, 2, 5, 6 | Pending | `IN_PROGRESS` — continue polling |
| 3 | Success (enrolled) | `COMPLETED` — set `subscription_service_activation_date` |
| 7 | Success (unenrolled) | `UNENROLLED` — cleanup per hard_delete flag |
| 1001 | Key-on timeout (7d window) | `IN_PROGRESS` → `FAILED` after threshold |
| 1002, 1003, 8010 | Vehicle ineligible | `FAILED` — no retry |
| **9999, 8030, 8040** (rev 3 B2) | **Surface-immediately failures** | **`FAILED` on first poll — NO automatic retry** |
| 8020 | 7-day key-on timeout | `FAILED` + emit `OEM1EnrollmentTimeout` event; UI offers manual retry |
| 429 | OEM1 quota exceeded | Pause polling 1h; row stays IN_PROGRESS |

See `docs/runbooks/oem1-fleet-lifecycle.md` for operator guidance on each failure mode, especially TC9999/8030/8040 escalation procedures.

## Related Files & References

- **Phase 1 Connector**: `main.py`, `connector.py`, `token_supplier.py`
- **CLI Seed Tool**: `seed_vehicles.py` — bulk seed vehicles with OEM1 enrollment state; supports `--sku` and `--manifest-id` flags
- **Auto-Register**: `auto_register.py` — sets `oem1_enrollment_status='COMPLETED'` on first telemetry (signals: "OEM1 finished enrolling, data is flowing")
- **Phase 2 Single-Add**: `admin_add_vehicle/` — single-VIN add via UI; mirrors seed_vehicles.py write semantics
- **Type System**: `modules/cms_ui/source/frontend/src/types/fleet-types.ts` — includes `FleetDataSource`, `OEM1EnrollmentStatus` enum, `OEM1ReadinessSummary` enum; `getOEM1Status()` helper
- **Frontend UX**: 
  - Enroll wizard (7 steps) at `modules/cms_ui/source/frontend/src/components/vehicles/enroll-wizard/`
  - Bulk-unenroll modal at `modules/cms_ui/source/frontend/src/components/vehicles/bulk-unenroll/`
  - OEM1 status column in Vehicles list
  - Fleet creation form source picker (CMS-native OR OEM1 + manifest selector)
  - OEM1 detail-view panel with refresh + enrollment history + retry affordance
  - Fleet picker primitive for multi-fleet users

## Deployment & Configuration

See `docs/DEPLOYMENT.md` for full deployment runbook. Key points:

- **CDK Context** (`cdk.json`):
  - `oem1ProductCatalog` — list of available SKUs (empty → free-form entry in UI with banner)
  - `oem1StatusSyncCadenceMinutes` — status sync frequency (default 15)
  - `oem1EnrollmentPollerCadenceMinutes` — enrollment poller frequency (default 1)
  - `oem1BulkEnrollMaxVins` — max VINs per bulk enroll (default 500)

- **Environment Variables** (per Lambda):
  - `OEM1_FEED_HOST` — OEM1 API base URL
  - `OEM1_APPLICATION_ID` — application ID header (verified via Postman)
  - `SECRETS_NAME` — AWS Secrets Manager secret ARN (OEM1 OAuth credentials)
  - `DEPLOYMENT_STAGE` — stage name (dev, staging, prod)
  - `CMS_USER_POOL_ID` — Cognito User Pool ID (set after UI stack lands)

- **IAM Permissions**: Each Lambda has least-privilege grants per spec § 2.1-2.7 (secretsmanager, dynamodb, ssm, logs, events)

- **Auth Gate**: Admin routes require `cognito:groups` claim containing `platform-admin` (cross-fleet) OR `fleet-operator` (per-fleet via `custom:fleetIds` claim). Pre-enroll routes verify body `fleet_id ∈ user.fleetIds`; post-enroll routes resolve VIN→fleet via `vehicleId-index` GSI and verify each fleet ∈ user.fleetIds. Any other group → 403.

## Testing

- **Unit Tests**: `services/connectors/oem1/admin_<name>/tests/test_handler.py`
  - Run all: `python -m pytest services/connectors/oem1/ -v`
  - Per-Lambda: `cd services/connectors/oem1/admin_bulk_enroll && python -m pytest tests/ -v`

- **Integration Tests**: `services/connectors/oem1/tests/integration/`
  - Mock OEM1 server (extend `mock_rest_server.py`) supports all endpoints with configurable failure injection
  - Full scenarios: enroll → poll → COMPLETED, unenroll → terminal 7 (soft/hard), status sync drift, quota exhaustion, OQ16 surface-immediately
  - Run: `python -m pytest services/connectors/oem1/tests/integration/ -v`

## Known Limitations (v1)

- **Per-fleet authorization**: `fleet-operator` Cognito group with `custom:fleetIds` JWT claim is enforced server-side via `services/connectors/oem1/_lib/fleet_membership.py` (resolves VINs to fleets via `vehicleId-index` GSI on `cms-{stage}-storage-fleet-enrollment`). `platform-admin` retains cross-fleet authority. See spec `2026-06-09-cms-fleet-manager-cognito-role`.
- **Single SKU per vehicle**: Multi-SKU per vehicle deferred to v1.1 (C7); switch SKU requires unenroll + re-enroll
- **No cascade delete on hard-unenroll**: trips/events/maintenance-alerts preserved per OQ3
- **No automated TC9999/8030/8040 retry**: Surface immediately; manual retry available via UI (consumes quota)
- **Status sync cadence not per-fleet**: All OEM1 vehicles refreshed on the same 15-min schedule (no per-fleet tuning in v1)
- **Fleet-creation Lambda does not persist client `data_source`**: The main-API `POST /fleets` Lambda at `main_api/index.py` does not currently honor the client-supplied `data_source` field; new fleets default to `vehicle-telemetry` via helper fallback. OEM1 fleets are seeded via `seed_generic_fleets.py` which writes `data_source` directly to DynamoDB. Client-supplied `data_source` acceptance on fleet creation is filed as a Phase D backlog follow-on.

## Next Steps / Future Work

- **Fleet-Operator Role** — enforced via Cognito group `fleet-operator` + `custom:fleetIds` JWT claim; resolved by `services/connectors/oem1/_lib/fleet_membership.py`. Per-route gate matrix per spec `2026-06-09-cms-fleet-manager-cognito-role` § 1.
- **Cross-Cutting Audit Framework** — centralized audit-log DDB table + UI surface for CMS/ADP/CVX (P2 backlog: `Cross-cutting audit framework`)
- **Step Functions Polling** — migrate `admin_enrollment_poller` to Step Functions for higher throughput (P3 backlog: W13 carry-forward)
- **OEM2/OEM3 Support** — expand connector pattern to additional OEM sources (G9 carry-forward)
- **Multi-Tenant Fleet Isolation** — per-customer fleet boundaries + per-customer quota enforcement (NG4/W4 carry-forward)


## Event-Type Decode Coverage

The OEM1 connector implements an inner-type dispatch mechanism to decode diverse Event-family message types. The Event decode branch (connector.py, lines 368–391) dispatches on the protobuf type-URL suffix to unwrap the appropriate nested message class, then emits decoded camelCase JSON.

### Inner-Type Dispatch Table

The `_INNER_PAYLOAD_DISPATCH` module-level dict (lines 296–300) defines supported inner types:

| Inner Type | Proto Class | Behavior |
|---|---|---|
| `TriggeredEvent` | `autonomic.ext.event.TriggeredEvent` | Emits decoded TriggeredEvent JSON; applies `string_label` → `_raw_hex` fallback (preserves vha-diagnostics custom events) |
| `StateTransition` | `autonomic.ext.event.StateTransition` | Emits decoded StateTransition JSON (e.g., COMMAND_PRECLUSION_STATE FSM transitions) |
| `GeofenceEvent` | `autonomic.ext.event.GeofenceEvent` | Emits decoded GeofenceEvent JSON |

When the connector receives an `Event` message with a nested `payload`, it:
1. Extracts the inner type from `Event.payload.type_url`
2. Looks up the inner type in `_INNER_PAYLOAD_DISPATCH`
3. Attempts `Unpack()` to the target inner class
4. For TriggeredEvent only, checks `WhichOneof('label')` to detect `string_label` events (preserved as `_raw_hex` fallback per parent spec)
5. Emits the result as camelCase JSON or `_raw_hex` on unpack failure

### Fallback Behavior

Unknown inner types (not in the dispatch table) and unpackable messages fall through to the `_raw_hex` fallback path. The raw protobuf bytes are preserved as a hex-encoded string in the `typedData.value._raw_hex` field. This allows the Flink processor to gracefully handle unknown event types without crashing:

```json
{
  "typedData": {
    "@type": "type.googleapis.com/autonomic.ext.event.Event",
    "value": {
      "_raw_hex": "08011088c9..."
    }
  },
  "oem_source": "oem1"
}
```

The processor's manifest layer (`OEMTelemetryProcessor.transformEventMessage()`) checks for matching `event_mappings` entries. Unknown events with no manifest entry route to DLQ. See `services/data_processing/manifests/oem1-transform.json` event_mappings schema for coverage details.
