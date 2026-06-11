# OEM1 Fleet Lifecycle Architecture

This document describes the data model and operational flows for the OEM1 fleet-scoped bulk lifecycle management system. It covers the enroll, unenroll, refresh-status, and status-sync workflows.

## Data Model

The core entities interact as follows:

```mermaid
erDiagram
    FLEET ||--o{ VEHICLE : contains
    FLEET ||--o{ MANIFEST : references
    VEHICLE ||--o{ ENROLLMENT_REQUEST : has
    ENROLLMENT_REQUEST ||--o{ VEHICLE : tracks

    FLEET {
        string fleet_id PK
        string data_source "vehicle-telemetry or cloud-telemetry"
        string transform_manifest_id FK "optional, for cloud-telemetry"
    }

    VEHICLE {
        string vehicle_id PK
        string fleet_id FK
        string vin
        string oem_source "cms or oem1"
        string oem1_active_sku "current OEM1 product"
        number oem1_request_id "last OEM1 request"
        string oem1_enrollment_status "IN_PROGRESS, COMPLETED, FAILED, UNENROLLED"
        number oem1_fcs_code "OEM1 status code"
        string oem1_status_message
        string oem1_readiness_summary "READY, CCS_OFF, etc"
        string oem1_status_refreshed_at "ISO8601 timestamp"
        string subscription_service_activation_date
    }

    MANIFEST {
        string manifest_id PK
        string s3_path "e.g. transforms/oem1-transform.json"
    }

    ENROLLMENT_REQUEST {
        number request_id PK
        string request_type "ENROLL or UN_ENROLL"
        string customer_id
        string fleet_id
        string oem1_request_id
        string submitted_by "actor email"
        string submitted_at "ISO8601"
        string status_summary
        number accepted_count
        number pre_flight_failure_count
        string client_request_id "optional UUID for idempotency"
        boolean hard_delete "true=hard, false=soft (default)"
        string terminal_at "ISO8601, null if in-flight"
        string expires_at "90-day TTL"
    }
```

**Key design notes:**

- **Fleet source**: `data_source` discriminates between OEM1-sourced fleets (`cloud-telemetry`) and native CMS fleets (`vehicle-telemetry`). Defaults to `vehicle-telemetry` for back-compat.
- **Vehicle inheritance**: All OEM1 vehicles belong to a `cloud-telemetry` fleet; the consistency invariant is enforced at enrollment time.
- **Enrollment-request history**: Every enroll/unenroll submission creates an enrollment-request row, retained for 90 days. The poller consults this table to drive vehicles to terminal states.
- **Status fields**: The 8 new OEM1 status fields (`oem1_*`) are written by multiple Lambdas but only the poller and status-sync drive them to terminal states.

## Enroll Flow

The enroll flow validates a batch of VINs, runs a server-side capability check, submits to OEM1, and persists the request for asynchronous polling.

```mermaid
sequenceDiagram
    actor Admin
    participant FE as CMS Frontend
    participant AdminEnroll as admin_bulk_enroll Lambda
    participant OEM1 as OEM1 API
    participant DDB as DynamoDB
    participant Logs as CloudWatch Logs

    Admin->>FE: Paste VINs, select SKU & fleet, assign drivers
    FE->>FE: Client-side pre-flight (advisory only)
    Admin->>FE: Click Submit
    FE->>AdminEnroll: POST /admin/oem1/bulk-enroll<br/>{fleet_id, sku, vehicles[...], clientRequestId?}

    AdminEnroll->>AdminEnroll: Auth: cognito:groups contains platform-admin
    AdminEnroll->>DDB: Query client_request_id GSI (if present)
    alt Idempotent re-submit
        AdminEnroll->>AdminEnroll: Found cached response
        AdminEnroll-->>FE: 200 + X-Idempotency-Replay: true + cached status_summary
    else First-time submit
        AdminEnroll->>AdminEnroll: Validate: VIN/SKU/fleet format, drivers present
        AdminEnroll->>OEM1: POST /enrollment/v2/preflight<br/>{vins, sku}
        OEM1-->>AdminEnroll: liteCheck results (capable/not-capable per VIN)
        alt Pre-flight failure
            AdminEnroll->>AdminEnroll: Build failed-VINs summary
            AdminEnroll-->>FE: 200 + {accepted: [], pre_flight_failures: [...]}<br/>+ CloudWatch INFO log
        else All capable
            AdminEnroll->>OEM1: POST /enrollment/v2/enroll<br/>{product: SKU, vehicles: [...]}
            OEM1-->>AdminEnroll: 202 + request_id
            AdminEnroll->>DDB: PutItem enrollment-requests<br/>(request_id, submitted_by, submitted_at, fleet_id, oem1_request_id, client_request_id, status_summary)
            AdminEnroll->>DDB: PutItem vehicles (idempotent)<br/>oem1_enrollment_status='IN_PROGRESS', oem1_request_id, oem1_active_sku
            AdminEnroll->>DDB: PutItem fleet-enrollment (idempotent)
            AdminEnroll->>Logs: INFO: actor, fleet_id, action=ENROLL, vin_count, sku, oem1_request_id
            AdminEnroll-->>FE: 200 + {status_summary, oem1_request_id, accepted_count}
        end
    end
    FE->>FE: Render IN_PROGRESS, poll status/latest every 5s
```

**Key design points:**

- **Idempotency**: When `clientRequestId` is present and a cached response exists, return immediately without calling OEM1.
- **Pre-flight is mandatory**: Server-side `liteCheck` is always run, regardless of UI state (constraint C3).
- **Async completion**: Returns 200 on OEM1 202; vehicles are marked `IN_PROGRESS` and the poller drives them to terminal.
- **Audit trail**: A CloudWatch structured log line records the submission actor, fleet, VINs, and outcome.

## Unenroll Flow

The unenroll flow submits a batch of VINs for OEM1 unenrollment and marks them pending removal.

```mermaid
sequenceDiagram
    actor Admin
    participant FE as CMS Frontend
    participant AdminUnenroll as admin_bulk_unenroll Lambda
    participant OEM1 as OEM1 API
    participant DDB as DynamoDB
    participant Logs as CloudWatch Logs

    Admin->>FE: Multi-select OEM1 vehicles from list
    FE->>FE: Show unenroll modal (copy, hard-delete checkbox)
    Admin->>FE: Confirm unenroll (type fleet name for ≥10 VINs)
    FE->>AdminUnenroll: POST /admin/oem1/bulk-unenroll<br/>{fleet_id, sku, vins, hard_delete?, clientRequestId?}

    AdminUnenroll->>AdminUnenroll: Auth: cognito:groups contains platform-admin
    AdminUnenroll->>DDB: Query client_request_id GSI (if present)
    alt Idempotent re-submit
        AdminUnenroll->>AdminUnenroll: Found cached response
        AdminUnenroll-->>FE: 200 + X-Idempotency-Replay: true + cached status_summary
    else First-time submit
        AdminUnenroll->>DDB: BatchGetItem vehicles (verify oem1_source, oem1_active_sku matches)
        AdminUnenroll->>OEM1: POST /enrollment/v2/unenroll<br/>{products: [SKU], vehicles: [...]}
        OEM1-->>AdminUnenroll: 202 + request_id
        AdminUnenroll->>DDB: PutItem enrollment-requests<br/>(request_id, request_type='UN_ENROLL', hard_delete flag)
        AdminUnenroll->>DDB: UpdateItem vehicles<br/>oem1_enrollment_status='UN_ENROLL_IN_PROGRESS', oem1_request_id
        AdminUnenroll->>Logs: INFO: actor, fleet_id, action=UN_ENROLL, vin_count, sku, hard_delete
        AdminUnenroll-->>FE: 200 + {status_summary, oem1_request_id}
    end
    FE->>FE: Render UN_ENROLL_IN_PROGRESS, poll for UNENROLLED
```

**Key design points:**

- **Idempotency**: Same pattern as enroll—cached response on `clientRequestId` re-submit.
- **SKU homogeneity**: All selected vehicles must have the same `oem1_active_sku` (heterogeneous batches rejected 400).
- **Soft vs. hard delete**: Default is soft (mark `Inactive`, clear `oem1_active_sku`); hard-delete is opt-in and does not cascade to trips/events/maintenance-alerts (constraint C9).
- **Poller-driven terminal**: Unenroll requests reach terminal state (UNENROLLED) via the poller's consumption of `status/latest` and application of the Consumer Action policy.

## Refresh-Status Flow

An admin manually refreshes the OEM1 status for a vehicle or batch, subject to a 60-second rate-limit per VIN.

```mermaid
sequenceDiagram
    actor Admin
    participant FE as CMS Frontend
    participant AdminRefresh as admin_refresh_vehicle_status Lambda
    participant OEM1 as OEM1 API
    participant DDB as DynamoDB
    participant Logs as CloudWatch Logs

    Admin->>FE: Click "Refresh OEM1 status" button (on vehicle detail or bulk action)
    FE->>AdminRefresh: POST /admin/oem1/refresh-status<br/>{vehicle_ids: [...]}

    AdminRefresh->>AdminRefresh: Auth: cognito:groups contains platform-admin
    loop Per vehicle
        AdminRefresh->>DDB: Check oem1_status_refreshed_at
        alt Last refresh < 60s ago
            AdminRefresh->>AdminRefresh: Rate-limit hit, skip this VIN
        else Eligible for refresh
            AdminRefresh->>OEM1: POST /enrollment/v2/status/latest<br/>{vins: [...], page_size: 1000}
            OEM1-->>AdminRefresh: fcs_code, status_message for each VIN
            AdminRefresh->>OEM1: POST /selfserve/v1/vehicleState<br/>{vins: [...]}
            OEM1-->>AdminRefresh: readiness_summary for each VIN
            AdminRefresh->>DDB: UpdateItem vehicle<br/>oem1_fcs_code, oem1_status_message, oem1_readiness_summary, oem1_status_refreshed_at
        end
    end
    AdminRefresh->>Logs: INFO: actor, action=REFRESH, vin_count, refreshed_count, error_count
    AdminRefresh-->>FE: 200 + {refreshed_count, rate_limit_skipped_count}
    FE->>FE: Render updated status fields
```

**Key design points:**

- **Per-VIN rate-limit**: 60-second minimum between refreshes for the same VIN (constraint C19).
- **Advisory only**: This is operator-initiated; the background `admin_status_sync` provides continuous sync every 15 minutes.
- **Enrichment**: Fetches both `status/latest` and `vehicleState` to populate both fcs_code and readiness_summary.

## Status-Sync Flow

The system poller runs every 15 minutes (configurable via `cdk.json:context.oem1StatusSyncCadenceMinutes`) to sync OEM1 vehicle status for all OEM1-sourced vehicles not refreshed in the last hour. Drift events are emitted for state transitions.

```mermaid
sequenceDiagram
    participant EventBridge as EventBridge Schedule
    participant AdminSync as admin_status_sync Lambda
    participant OEM1 as OEM1 API
    participant DDB as DynamoDB
    participant Events as EventBridge Events
    participant Metrics as CloudWatch Metrics

    EventBridge->>AdminSync: Trigger every 15 minutes

    AdminSync->>DDB: Scan vehicles<br/>oem_source='oem1' AND (oem1_status_refreshed_at IS NULL OR < now-1h)
    AdminSync->>AdminSync: Batch VINs by 1000

    loop Each batch
        AdminSync->>OEM1: POST /enrollment/v2/status/latest<br/>{vins: [...], page_size: 1000}
        OEM1-->>AdminSync: fcs_code, status_message per VIN

        loop Per VIN
            alt Status changed (e.g. COMPLETED → UNENROLLED)
                AdminSync->>DDB: UpdateItem vehicle with new status fields
                AdminSync->>Events: PutEvents OEM1StatusDrift<br/>(vin, old_status, new_status)
            else No change
                AdminSync->>AdminSync: No-op, skip event
            end
        end
    end

    AdminSync->>Metrics: PutMetricData cms/oem1/status_sync<br/>(vehicles_refreshed, drift_detected, duration_ms, calls_per_15min)
    AdminSync->>AdminSync: Done
```

**Key design points:**

- **Background sync**: Runs independently of user actions; catches OEM1 portal-initiated unenrollments.
- **Drift detection**: Only emits events on terminal-state transitions (e.g. COMPLETED → UNENROLLED), not for IN_PROGRESS → IN_PROGRESS repeats.
- **Rate-limit aware**: Skips vehicles refreshed within the last hour to respect OEM1 API budget (constraint C19).
- **Metrics**: Emits CloudWatch metrics for operational monitoring.

## Consumer Action Policy (Spec § 4.1)

The enrollment poller (`admin_enrollment_poller`) consults the Consumer Action policy table to drive vehicles to terminal states based on OEM1 fcs_codes:

| fcs_code | Name | Behavior |
|---|---|---|
| 0 | Initial request received | Continue polling, backoff schedule per spec § 4.2 |
| 1 | Request processing | Continue polling |
| 2 | Vehicle added to fleet | Continue polling |
| 3 | Successfully enrolled | Terminal COMPLETED; set `subscription_service_activation_date` |
| 5 | Awaiting engine start | Continue polling (related to code 1001) |
| 6 | Vehicle has engine-started | Continue polling |
| 7 | Successfully unenrolled | Terminal UNENROLLED; soft/hard-delete per flag |
| 1001 | Vehicle requires engine start, not keyed-on within 7d | Continue polling, backoff up to 7 days (per OQ4 guidance) |
| 1002 | Vehicle does not meet requirements | Terminal FAILED |
| 1003 | Enrollment limit per fleet reached | Terminal FAILED |
| 8010 | Request ID is invalid | Terminal FAILED |
| 8020 | Enrollment processing timeout (7 days) | Terminal FAILED + emit `OEM1EnrollmentTimeout` event |
| 8030 | VIN not in OEM1 ecosystem | Terminal FAILED (surface immediately, no retry) |
| 8040 | Capability check service unavailable | Terminal FAILED (surface immediately, no retry) |
| 9999 | Please retry the request | Terminal FAILED (surface immediately, no retry; do NOT auto-retry) |
| 429 | OEM1 quota exhausted (HTTP-level) | HTTP 429 passthrough; return upstream; no retry |
| unknown | Unrecognized code | Continue polling with caution; log warning |

**Revision 3 (OQ16) surface-immediately policy**: Codes 9999, 8030, and 8040 are marked for immediate surface on the first poll cycle that returns them. No automatic retry. Manual retry is possible via the UI but only by repeating the user's enroll submission.

## Operational References

- **Postman Collection**: FCS-Vehicle-Enrollment-2.0-postman_collection.json — authoritative for OEM1 endpoint request/response shapes.
- **Spec § 4.1**: Consumer Action policy table (this document, above).
- **Spec § 4.3 (Revision 3)**: OQ16 failure-handling policy — surface-immediately behavior for codes 9999, 8030, 8040.

## See Also

- `docs/DEPLOYMENT.md` — Configuration and operational runbook for this system.
- `docs/runbooks/oem1-fleet-lifecycle.md` — Troubleshooting scenarios and remediation steps.
- Spec: `~/.kiro/specs/2026-06-05-cms-oem1-fleet-bulk-management/spec.md`
