# Commands Service

## Overview

The Commands Service implements on-demand remote commands for connected vehicles, including SOVD (Service-Oriented Vehicle Diagnostics) operations for reading and clearing diagnostic trouble codes (DTCs).

## Supported Command Types

### read_dtcs — Read Diagnostic Trouble Codes

**Endpoint**: `POST /api/commands/{vehicleId}`

**Request**:
```json
{
  "command_type": "read_dtcs",
  "components": ["ECU_ENGINE"],
  "include_freeze_frame": true,
  "correlation_id": "optional-uuid"
}
```

- `components` — array of ECU identifiers (e.g., `["ECU_ENGINE", "ECU_TRANSMISSION"]`). Use `["*"]` for a full vehicle scan.
- `include_freeze_frame` — optional boolean; if true, includes signal snapshot data captured at the time each DTC was detected.
- `correlation_id` — optional; if omitted, the server generates a UUID4. Must match `[a-zA-Z0-9-]{1,64}` if provided.

**Response** (ASAM SOVD-aligned, ISO 17978-3):
```json
{
  "correlation_id": "a1b2c3d4-e5f6-g7h8-i9j0-k1l2m3n4o5p6",
  "status": "SUCCEEDED",
  "components": {
    "ECU_ENGINE": {
      "id": "ECU_ENGINE",
      "protocol": "ISO 15765-4",
      "dtcs": [
        {
          "code": "P0420",
          "status": "confirmed",
          "occurrence_count": 3,
          "first_seen_ms": 1693478400000,
          "last_seen_ms": 1693564800000,
          "freeze_frame": {
            "engineRpm": { "value": 2800, "unit": "rpm", "timestamp": "2026-08-31T14:30:00Z" },
            "coolantTemp": { "value": 88, "unit": "degC", "timestamp": "2026-08-31T14:30:00Z" },
            "vehicleSpeed": { "value": 65, "unit": "km/h", "timestamp": "2026-08-31T14:30:00Z" },
            "engineLoad": { "value": 45, "unit": "%", "timestamp": "2026-08-31T14:30:00Z" },
            "throttlePosition": { "value": 28, "unit": "%", "timestamp": "2026-08-31T14:30:00Z" },
            "fuelTrim": { "value": 8, "unit": "%", "timestamp": "2026-08-31T14:30:00Z" }
          }
        }
      ]
    }
  },
  "latency_ms": 3400,
  "storage_uri": null
}
```

**Large response fallback** (> 80 KB <!-- verify: grep SIZE_THRESHOLD_BYTES services/commands/sovd_payload_sizing.py -->):

When the full response exceeds 80 KB, the sidecar uploads the complete JSON to S3 and returns:
```json
{
  "correlation_id": "…",
  "status": "SUCCEEDED",
  "components": {
    "ECU_ENGINE": { "dtcCount": 12, "hasFreezeFrame": true, … }
  },
  "storage_uri": "s3://cms-staging-storage-sovd-responses-us-west-2-123456789012/VEH-STAGING-000/a1b2c3d4.json"
}
```

The cloud handler retrieves the full payload from S3 and generates a **pre-signed GET URL** (15-minute expiry) on-demand when the operator accesses the history route. The DynamoDB row stores only the **S3 key**, not the URL (immutable audit trail).

**Authorization**:
- Fleet-scoped: caller must have admin, operator, or viewer role for the target vehicle's fleet.
- All roles can read DTCs (viewers included).

**Latency**: Typical single-ECU read completes within 5 seconds (includes MQTT round-trip + UDS request timeout). Full scans of 9 ECUs <!-- verify: grep "^\s*[0-9]:" services/simulation/lambda/simulation_lambda.py | grep -E 'ECU_' | wc -l --> may take up to 15 seconds.

### clear_dtcs — Clear Diagnostic Trouble Codes

**Endpoint**: `POST /api/commands/{vehicleId}`

**Request**:
```json
{
  "command_type": "clear_dtcs",
  "components": ["ECU_ENGINE"],
  "attestation": {
    "text": "I have verified the underlying repair is complete",
    "user_email": "operator@example.com",
    "timestamp_ms": 1693564800000
  }
}
```

- `components` — array of ECU identifiers. Use `["*"]` to clear all DTCs across all ECUs.
- `attestation` — required object containing:
  - `text` — the operator's attestation statement (used in audit trail).
  - `user_email` — operator email (will be overwritten with the caller's Cognito email claim for authenticity).
  - `timestamp_ms` — milliseconds since epoch when the clear was initiated.

**Response** (same shape as `read_dtcs`):
```json
{
  "correlation_id": "…",
  "status": "SUCCEEDED",
  "components": {
    "ECU_ENGINE": {
      "id": "ECU_ENGINE",
      "dtcs": []
    }
  },
  "latency_ms": 1200
}
```

**Authorization**:
- Requires `admin` OR `operator` role. Viewers are denied write-route access.
- Fleet-scoped: same as `read_dtcs`.

**Audit Trail**:
- Each cleared DTC is marked in `dtc-history` with:
  - `status: 'CLEARED_REMOTE'`
  - `clearedAt`: timestamp of the clear operation
  - `clearedBy`: operator email (from Cognito claim)
  - `clearedAttestation`: the attestation text and original timestamp

## DTC History Storage

Responses are persisted to the `cms-{stage}-storage-dtc-history` DynamoDB table:

- **Read operations**: Create new rows with `source: 'sovd'`, `occurrence_count: 1`, and `first_seen_at == last_seen_at == now`. Duplicate detection within a 24-hour window increments `occurrence_count` and updates `last_seen_at` (no duplicate rows).
- **Clear operations**: UpdateItem on existing history rows, appending the clear operation details to the audit trail.

## Pre-signed URL Flow (Risk R2 Mitigation)

When a response exceeds 80 KB and is stored in S3:

1. **Sidecar** uploads the JSON file to `s3://cms-{stage}-storage-sovd-responses/{VIN}/{correlationId}.json` using the task IAM role.
2. **Cloud handler** receives the response, stores the **S3 key** (not a URL) in the `commands` DDB row.
3. **Fleet manager** requests the history via `GET /api/commands/{vehicleId}?type=sovd&limit=10`.
4. **API** generates a **pre-signed URL** on-demand (15-minute expiry) using the caller's Cognito identity, then includes it in the response.
5. **UI** fetches the full payload from the pre-signed URL.

**Security rationale**: Pre-signed URLs are time-limited and caller-specific. Storing URLs in the database would create a permanent, universally-accessible artifact. Generating URLs on-demand ensures every access is auditable and URLs cannot be shared or leaked from database backups.

## Authorization Model

All SOVD operations enforce fleet-scoping via `_lib.fleet_membership.py`:

- **admin** — can read and clear DTCs across any fleet.
- **operator** — can read and clear DTCs within assigned fleets only.
- **viewer** — can read DTCs within assigned fleets; cannot clear.
- **groupless** — denied on all paths (fail-open anti-pattern mitigation per spec `2026-08-05-main-api-fail-open-authz-defaults`).

Per-VIN authorization is enforced before any DTC query or command is issued. Callers without the required role for a vehicle receive a 403 Forbidden.

## Integration with Fleet History

SOVD DTCs appear in the vehicle detail page's DTC table alongside DTCs from passive FWE monitoring:

- **source column** — differentiates `sovd` (on-demand read) from `fwe` (passive campaign polling).
- **UI filtering** — operators can filter by source to see only on-demand or passive reads.
- **Deduplication** — DTCs from both sources are deduplicated within a 24-hour window (same DTC code + vehicle = same row, occurrence count incremented).

## Rate Limiting

The sidecar enforces a token-bucket rate limiter:

- **Full scans** (`components: ["*"]`) — maximum 1 per 10 seconds per vehicle.
- **Per-ECU reads** — issued sequentially (never in parallel) to avoid CAN bandwidth saturation.
- **Rate-limited response** — when exceeded, returns `status: 'RATE_LIMITED'` with `retry_after_ms` and no DTC data.

## Testing

Unit tests cover:
- Authorization matrix (admin, operator, viewer, groupless across read/clear routes)
- Request validation (schema, correlation_id format, component list bounds)
- Response sizing (inline < 80 KB, S3 > 80 KB)
- Deduplication logic (24-hour window per DTC)
- Pre-signed URL generation and expiry
- Clear operation attestation logging
- Rate limiting

Run tests with:
```bash
python3 -m pytest services/commands/tests/ -v
```

