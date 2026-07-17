# OEM1 Add-Vehicle Architecture

## Overview

CMS provides two paths for enrolling OEM1 vehicles:

1. **Bulk enrollment** via `seed_vehicles.py` — the canonical batch path for seeding fleets
2. **Single-vehicle UI flow** — the admin add-vehicle path for one-off enrollments

This document covers the single-vehicle UI flow. For bulk seeding, see `services/connectors/oem1/seed_vehicles.py`.

## Data Flow

```
CreateVehicle UI (source-picker)
  ↓
Select OEM1 → OEM1 Form (VIN + fleet selector)
  ↓
Submit → oem1AddVehicle.ts (authFetch + Bearer idToken)
  ↓
Authorization: Bearer <idToken>
  ↓
API Gateway (Cognito User Pool authorizer)
  ↓
Lambda cms-{stage}-oem1-admin-add-vehicle
  ↓
OEM1 POST /enrollment/v2/status/latest (bulk-fetch, 5 pages × 100 vehicles)
  ↓
Client-side VIN filter on response
  ↓
DynamoDB vehicles table + fleet-enrollment table write
  ↓
200 response with enrollment status
  ↓
VehicleDetailView with OEM1-specific UI
```

## OEM1 Endpoint Shape (Verified)

**No per-VIN enrollment-status endpoint exists** in the verified OEM API Postman collection. The Lambda implements a bulk-fetch + client-side filter pattern:

- **Endpoint**: `POST /enrollment/v2/status/latest`
- **Request**: `{ "statuses": ["COMPLETED","PENDING","FAILED"], "page_size": 100, "page_number": N, "order_by": "DESC" }`
- **Response**: Array of enrollment records with VIN, status, and optional `make`, `model`, `year` fields
- **Pagination**: Maximum 5 pages (500 vehicles); if VIN not found within first 500, returns `UNKNOWN` with reason directing to bulk-enrollment CLI

The Lambda performs client-side filtering on the bulk response to locate the submitted VIN and extract its status.

## Enrollment Status Variants

| Status | Behavior | UI Response |
|--------|----------|-------------|
| `COMPLETED` | VIN is enrolled; telemetry flowing | `{ enrollmentStatus: "COMPLETED", writeStatus: "inserted" \| "updated" }` |
| `PENDING` | VIN pending enrollment from OEM | `{ enrollmentStatus: "PENDING", writeStatus: "pending" }` — writes `enrollment_pending: true` to DDB without `enrolled_at` |
| `FAILED` | OEM enrollment failed | `{ enrollmentStatus: "FAILED", writeStatus: "pending" }` — same as PENDING; reconciliation pending |
| `UNKNOWN` | VIN not in first 500 enrollments | `{ enrollmentStatus: "UNKNOWN", reason: "VIN not found in first 500 enrollments — use seed-vehicles-oem1 CLI for bulk enrollment" }` — no DDB write |

### PENDING Row Reconciliation

When a VIN has status `PENDING`, the Lambda writes to DDB with `enrollment_pending: true` and omits `enrolled_at`. The next run of `seed_vehicles.py` reconciles this row: when it fetches the same VIN and sees status `COMPLETED`, the seed script performs an `UpdateItem` to set `enrolled_at`. This one-shot reconciliation is implicit — the UI simply displays "Enrollment pending" until the status changes.

## Idempotency Contract

Submitting the same VIN twice returns `{ writeStatus: "already_enrolled" }` on the second submission. The Lambda uses a conditional-put pattern (`ConditionExpression="attribute_not_exists(vehicleId)"` on insert, falling through to `UpdateItem` on conflict) to guarantee idempotent writes. The response is always 200 and the caller sees no error — a re-submission is a safe no-op.

## Data Plane

The Lambda writes the same item shape as `seed_vehicles.py:_write_vehicle`:

| Field | Type | Example |
|-------|------|---------|
| `vehicleId` | String (PK) | `oem1#STL123456789` |
| `oem_source` | String | `"oem1"` |
| `last_seen_at` | String (ISO8601) | `"2026-06-04T11:47:50Z"` |
| `enrolled_at` | String (ISO8601) | `"2026-06-04T11:47:50Z"` — absent for PENDING |
| `make` | String (optional) | `"OEM-A"` |
| `model` | String (optional) | `"F-150"` |
| `year` | String (optional) | `"2024"` |
| `oem1_shard_uuid` | NULL | (reserved; always NULL) |

Tables: `cms-{stage}-storage-vehicles` and `cms-{stage}-storage-fleet-enrollment`.

**Note**: Field naming uses snake_case (e.g., `oem_source`, `last_seen_at`, `enrollment_pending`) to match the DynamoDB write shape from `seed_vehicles.py`. API field normalization (camelCase / snake_case cleanup) is out of scope and tracked as a separate P3 hygiene initiative.

## Server-Side Defenses

1. **Cognito `platform-admin` group gate** — the route requires `Authorization: Bearer <idToken>` where the token carries a `cognito:groups` claim including `platform-admin`. Missing group returns 403.
2. **Engineering-tenant rejection** — the Lambda reads `/cms/{stage}/engineering-fleet-ids` from SSM and rejects requests for Engineering-tenant fleets with 400 + `{ "error": "OEM1 vehicles are not available in the Engineering tenant" }`.
3. **Client-side defense** (in-depth) — the UI source-picker disables the OEM1 card for non-admins and for users in the Engineering tenant.

## Pagination Cap (R8)

The 500-vehicle ceiling (5 pages × 100 per page) is a denial-of-service mitigation for the single-VIN UI flow. For a typical staging fleet this is comfortable. If a VIN is not found within 500 vehicles, the response surfaces the cap-hit reason: `"VIN not found in first 500 enrollments — use seed-vehicles-oem1 CLI for bulk enrollment"`. Users are directed to the canonical bulk-enrollment path.

## See Also

- **Bulk enrollment**: `services/connectors/oem1/seed_vehicles.py` — the primary enrollment mechanism
- **Troubleshooting**: [OEM1 operator runbook](../runbooks/oem1-add-vehicle.md)
