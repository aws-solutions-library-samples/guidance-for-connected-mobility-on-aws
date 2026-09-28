# CMS Architecture Reference

## Remote Diagnostics (SOVD)

### Overview

Remote Diagnostics enables fleet operators to read vehicle diagnostic trouble codes (DTCs), clear DTCs, and retrieve freeze-frame data on-demand via the existing MQTT commands transport. Responses are **aligned with** the ASAM SOVD (Service-Oriented Vehicle Diagnostics) JSON shape — component-ID addressing, named signal values carrying `value`/`unit`/`timestamp` — so the interface is standards-ready.

Three limits on that alignment are load-bearing and must not be read past:

- **Aligned, not compliant.** ASAM SOVD is an HTTP/REST + JSON + OAuth API (ISO 17978 series). CMS carries SOVD-shaped JSON over MQTT, not SOVD REST, so this is a deliberate shape-level alignment rather than conformance to the standard.
- **The shape is derived from public secondary sources**, not from ISO normative text — ISO 17978-3 is paywalled. See `docs/tech.md` § "ASAM SOVD JSON Shape" for the citation type and source list. <!-- verify: grep -n "Citation type" docs/tech.md -->
- **No third-party SOVD tool can talk to this today.** A cloud-side SOVD HTTP server for third-party tool integration is an explicit non-goal of the initiative, as is a SOVD server on the vehicle. Interoperability would require that server to exist first.

### Architecture Layers

#### Layer 1: UI (Fleet Manager Interface)

The fleet manager initiates SOVD operations from the vehicle detail page:

- **VehicleDTCsTable** component displays active DTCs with status, occurrence count, and first-seen timestamp. New toolbar buttons enable `Read DTCs` (single ECU or all) and `Full Scan` (all ECUs with freeze-frame). Both buttons are disabled when `connectionStatus !== 'connected'` (allow-list gate, fail-closed).
- **ClearDTCModal** component appears when the operator clicks the clear button on a specific DTC row. Requires the operator to check "I have verified the underlying repair is complete" before enabling the Clear button; the attestation text and user email are included in the audit trail.
- **VehicleDiagnose** component on the Platform Admin page provides a unified diagnostics dashboard supporting both on-demand SOVD reads and historical DTC queries.

On user action, the UI POSTs to the existing Commands API endpoint (`POST /api/commands/{vehicleId}`), passing the command_type (`read_dtcs` or `clear_dtcs`), component list, and attestation (for clear operations).

#### Layer 2: Cloud Commands API

The Commands Lambda (`services/commands/commands_lambda.py`) extends the existing `POST /api/commands/{vehicleId}` endpoint to handle two new command types:

- **`read_dtcs`** — read active DTCs from one or more vehicle ECUs (electronic control units). Request specifies `components: ["ECU_ENGINE"]` for a single ECU or `["*"]` for a full vehicle scan. Optional `include_freeze_frame: true` retrieves signal snapshots captured at the time each DTC was detected.
- **`clear_dtcs`** — clear DTCs from vehicle ECUs. Requires operator attestation and admin-or-operator authorization level (viewers are denied write-route access).

Both operations enforce per-VIN fleet-scoping authorization via the `_lib.fleet_membership.py` overlay (same pattern as the Sim API; injected at CDK bundle time). The Lambda generates a unique `correlation_id` (UUID4, server-side) for audit traceability and persists an initial `commands` DynamoDB row with type `sovd` for tracking.

#### Layer 3: MQTT Sub-path (Cloud ↔ Sidecar Transport)

Commands are published to MQTT topics following the existing `cms/commands/things/{VIN}/executions/{correlationId}/` prefix:

- **Request path**: `cms/commands/things/{VIN}/executions/{correlationId}/sovd/request` (cloud → sidecar)
- **Response path**: `cms/commands/things/{VIN}/executions/{correlationId}/sovd/response` (sidecar → cloud)

Both paths carry JSON payloads using the ASAM SOVD shape (see § Design § 1 of the spec for exact schemas).

**Why the peer sub-path design?** The existing FWE (FleetWise Edge) agent subscribes on `cms/commands/things/{VIN}/executions/+/request/protobuf` with a literal trailing `/protobuf` segment. By using a peer sub-path (`/sovd/request` and `/sovd/response`), SOVD messages are naturally cordoned from FWE without any code changes to the FWE binary. FWE's subscription filter cannot match the SOVD topics because the MQTT wildcard `+` matches exactly one level, and a literal `/protobuf` segment does not wildcard. This design preserves the existing IoT device policy (no version bump, no certificate rotation across enrolled vehicles) and prevents accidental interference between the two protocols.

#### Layer 4: Vehicle Sidecar (Edge Processing & Response)

The sidecar (`vehicle-ecu` container) runs in a persistent ECS task alongside the FWE agent on each enrolled vehicle. It handles SOVD message dispatch:

1. **Message callback**: `on_sovd` closure receives the MQTT message on the SOVD request topic.
2. **Worker thread**: Spawns a background thread to handle the UDS (Unified Diagnostic Services) communication without blocking the paho network thread (critical for responsiveness).
3. **UDS request dispatch**: Issues UDS 0x19 (ReadDTCInformation) or 0x14 (ClearDiagnosticInformation) commands on the vehicle's virtual CAN bus (`vcanN`). 
   - UDS 0x19 sub-function 0x02 reads active DTCs by status mask
   - UDS 0x19 sub-function 0x04 retrieves freeze-frame data (signal snapshots) by DTC number
   - UDS 0x14 with group `FF FF FF` clears all DTCs
4. **Per-ECU responses**: Collects responses from each ECU via CAN, with a 3-second timeout per ECU.
5. **SOVD payload composition**: Decodes UDS bytes into ASAM SOVD JSON format, including signal names, values, units, and timestamps. Applies the sizing helper (`sovd_payload_sizing.SIZE_THRESHOLD_BYTES = 80 KB`) to decide the response delivery path.

**Sizing decision**:
- If the response ≤ 80 KB <!-- verify: grep SIZE_THRESHOLD_BYTES services/commands/sovd_payload_sizing.py -->: sidecar publishes the full SOVD JSON directly to the response MQTT topic (inline path).
- If the response > 80 KB: sidecar uploads the JSON to S3 at `s3://cms-{stage}-storage-sovd-responses/{VIN}/{correlationId}.json` using the task IAM role, then publishes a summary response to MQTT containing only `storageUri` and a summary (DTC count, freeze-frame present). The cloud handler then generates a pre-signed GET URL on-demand when the operator retrieves the history.

**Rate limiting**: The sidecar enforces a token-bucket rate limiter (1 full-scan per 10 seconds <!-- verify: grep -E "per.?10.*seconds|10\s*s" services/simulation/realtime_telemetry_simulator.py -->). Full scans (`components: ["*"]`) that exceed the rate return a `status: 'RATE_LIMITED'` response with `retry_after_ms`. Per-ECU reads are issued sequentially (never in parallel) to avoid exceeding vehicle CAN bandwidth.

### Data Storage

**DTC History**: The cloud Lambda handler (`services/commands/command_response_handler.py`) writes each DTC detected by a SOVD read operation to the `cms-{stage}-storage-dtc-history` DynamoDB table with:
- `source: 'sovd'` (distinguishes SOVD-discovered DTCs from passive FWE polling)
- Deduplication logic: if the same (vehicleId, dtcCode, source) combination exists within a 24-hour window, the handler increments `occurrence_count` and updates `last_seen_at` rather than inserting a duplicate.

**Clear operations**: Write an UpdateItem to existing history rows, setting `status: 'CLEARED_REMOTE'`, `clearedAt`, `clearedBy`, and `clearedAttestation` (audit trail).

**S3 Fallback**: Large responses (> 80 KB) are stored at the S3 key referenced in the MQTT response. A 30-day lifecycle rule <!-- verify: grep -E "30.*day|lifecycle.*expiration" deployment/stacks/commands_stack.py --> expires objects, and the cloud handler generates pre-signed GET URLs on-demand (per-caller, 15-minute expiry). Pre-signed URLs are NOT persisted in DDB — only the S3 key is stored.

### Example Workflow

1. **Fleet manager** clicks "Read DTCs" on a vehicle detail page, selects ECU_ENGINE, and checks "Include Freeze Frame".
2. **UI** POSTs `{ command_type: "read_dtcs", components: ["ECU_ENGINE"], include_freeze_frame: true }` to `/api/commands/{vehicleId}`.
3. **Commands Lambda** authorizes the operator, generates a correlation_id, persists a tracking row, and publishes to `cms/commands/things/{VIN}/executions/{correlationId}/sovd/request`.
4. **Sidecar** receives the message, spawns a worker thread, issues UDS 0x19 sub 0x02 on the CAN bus targeting ECU_ENGINE, waits for the response (3s timeout).
5. **ECU_ENGINE** responds with 2 active DTCs (e.g., P0420 catalyst efficiency low, P0171 fuel mixture too lean) plus freeze-frame data (RPM, coolant temp, etc.).
6. **Sidecar worker** encodes the response in ASAM SOVD JSON format (~2 KB, under threshold). Publishes to `cms/commands/things/{VIN}/executions/{correlationId}/sovd/response`.
7. **Cloud response handler** (via IoT rule) receives the response, writes two rows to `dtc-history` with `source='sovd'`, updates the commands tracking row to `status='SUCCEEDED'`.
8. **UI** polls the history endpoint, displays the two DTCs with their freeze-frame signals (RPM, temp, speed) in an expandable section, and renders a "Clear" button for each.
9. **Fleet manager** verifies the repairs are complete, clicks "Clear DTC" on P0420, checks the attestation box, confirms.
10. **UI** POSTs `{ command_type: "clear_dtcs", components: ["ECU_ENGINE"], attestation: { text: "I have verified…", user_email: "…", timestamp_ms: … } }`.
11. **Commands Lambda** validates the attestation, overwrites the user_email with the caller's Cognito claim (attestation forgery prevention), publishes to the SOVD request topic.
12. **Sidecar** issues UDS 0x14 FF FF FF to clear all DTCs on ECU_ENGINE, confirms success, publishes a `status: 'SUCCEEDED'` response.
13. **Cloud response handler** updates the matching dtc-history rows, setting `status='CLEARED_REMOTE'`, `clearedAttestation` with the attested reason, and `clearedBy`.
14. **UI** refreshes the history, shows the DTC marked as cleared with the operator's email, timestamp, and verification reason.

