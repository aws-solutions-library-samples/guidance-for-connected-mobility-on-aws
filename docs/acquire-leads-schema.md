# CVX Discover — `vsa-acquire-leads` Table Schema

**Spec**: `.kiro/specs/2026-08-04-cvx-oem-discover-order-meridian/`
**Version**: v1 (2026-07-28)
**Status**: Group 1 authoritative reference — all Group 2+ implementation MUST
conform to shapes in this doc; changes require a decisions.md entry.

This document covers the `vsa-acquire-leads` DynamoDB table introduced by the
CVX Discover spec as the **identity-promotion boundary write** in the
Discover / Find customer-journey stage. It is distinct from the
sibling Acquire spec's `vsa-acquire-catalog` and `vsa-acquire-orders` tables
(documented in `docs/acquire-catalog-schema.md`).

## Overview

`vsa-acquire-leads` is the **mock CRM table** for the v1 reference
implementation. It stores STAR-JSON envelopes written by the `lead_capture()`
agent tool when a Discover-phase user saves a configuration, requests more
information, or is about to converge into the Order phase ("Let's build this").

It is **NOT** an actual OEM CRM or dealer DMS. The table defines a seam: a
follow-up spec can swap the `lead_capture()` write target from this table to
a real Salesforce, dealer DMS, or OEM lead-management system. The STAR
envelope shape (§ `envelope` attribute below) is intentionally aligned with
the sibling `reservation_handoff` STAR shape so downstream analytics tooling
reads one shape across both Discover-phase (lead) and Order-phase (reservation)
records.

## Table naming

DynamoDB tables are account-region scoped — no region suffix required (per
`~/.kiro/steering/cross-region-namespace.md` analysis for DDB). Convention:

```
vsa-{stage}-acquire-leads
```

Example staging name: `vsa-staging-acquire-leads`.

## Primary key

| Attribute | DDB type | Role |
|---|---|---|
| `leadId` | S | Partition key. Format: `LEAD-YYYYMMDD-<8char>` (e.g. `LEAD-20260728-a3f7bc21`). The 8-char suffix is a deterministic random alphanumeric. Matches the sibling order-ID format (`ORD-YYYYMMDD-<8char>`) for tooling consistency. |

## Global Secondary Indexes (GSIs)

### `by-tenant-and-contact`

Supports idempotent re-writes: before writing a new lead, the
`lead_capture()` tool queries this GSI to check if a lead already exists
for the same contact under the same tenant.

| Attribute | Type | Role |
|---|---|---|
| `tenantId` | S | GSI partition key. |
| `contactHash` | S | GSI sort key. SHA-256 hex of `(tenantId \| emailNormalized \| phoneE164)` — see normalization rules below. |

**Projection**: `KEYS_ONLY`. After finding a matching key via GSI query,
the tool does a `GetItem` on the primary key to retrieve the full record
(specifically `leadId` for the idempotent return).

### `by-session`

Supports Discover→Order handoff lookup: after a lead is captured, the
Configurator flow can look up the lead by `discoverSessionId` if the
`leadId` is not available from the in-memory handoff envelope.

| Attribute | Type | Role |
|---|---|---|
| `sessionId` | S | GSI partition key. The `discoverSessionId` value from the Discover session. |

**Projection**: `KEYS_ONLY`.

## Item attributes

| Attribute | DDB type | Required | Notes |
|---|---|---|---|
| `leadId` | S | yes | PK. Format `LEAD-YYYYMMDD-<8char>`. |
| `tenantId` | S | yes | GSI PK for `by-tenant-and-contact`. |
| `sessionId` | S | yes | The `discoverSessionId` that created this lead. GSI PK for `by-session`. |
| `contactHash` | S | yes | GSI SK for `by-tenant-and-contact`. SHA-256 hex — see normalization. |
| `contactInfo` | M | yes | Map with `emailNormalized` (S), `phoneE164` (S or empty), `contactHash` (S). |
| `savedConfig` | M | no | Map with `preferredCategory` (S) and `preferredModelId` (S). Carry-forward for ConfiguratorFlow pre-population. |
| `personaSnapshot` | M or NULL | no | LTM-hydrated persona attributes; NULL for anonymous sessions. MUST be NULL/absent for anonymous sessions — do NOT write persona data from an anonymous session. |
| `consent` | M | yes | Map with `contactConsent` (BOOL, always true), `contactConsentTimestamp` (S, ISO-8601), `marketingConsent` (BOOL). |
| `envelope` | M | yes | The STAR-JSON envelope — see § *`envelope` attribute* below. |
| `createdAt` | S | yes | ISO-8601 UTC timestamp of first write. |
| `updatedAt` | S | yes | ISO-8601 UTC timestamp of last write (updated on idempotent re-tap that appends to `associatedSessions`). |
| `associatedSessions` | SS | no | String set of `discoverSessionId` values. Populated on idempotent re-writes (subsequent taps from different sessions for the same contact). The set grows monotonically; items are never removed. |
| `sourcePath` | S | yes | `"pathA"` (anonymous Buy-tab entry) or `"pathB"` (Path B warm-start mock trigger). |

## `contactHash` normalization rules

The hash MUST be computed identically in the `lead_capture()` tool, the
unit tests, and any downstream analytics queries.

```
contactHash = SHA256-hex(tenantId + "|" + emailNormalized + "|" + phoneE164)
```

Where:
- **`emailNormalized`**: `email.strip().lower()`. Empty string if no email.
- **`phoneE164`**: strip all characters except digits and leading `+`;
  prepend `+` if the stripped string does not start with `+`; result is
  `+<digits>`. Empty string if no phone provided.
- **Separator**: literal pipe character `|` between each component.

Example computation (Python):

```python
import hashlib

def contact_hash(tenant_id: str, email: str, phone: str) -> str:
    norm_email = email.strip().lower()
    stripped = phone.strip()
    digits_plus = "".join(c for c in stripped if c.isdigit() or c == "+")
    if digits_plus and not digits_plus.startswith("+"):
        digits_plus = "+" + digits_plus
    norm_phone = digits_plus  # empty string if no phone
    raw = f"{tenant_id}|{norm_email}|{norm_phone}"
    return hashlib.sha256(raw.encode()).hexdigest()

# Example
assert contact_hash("genericmoto-demo", "  Buyer@Example.com ", "+1 (555) 123-4567") == contact_hash(
    "genericmoto-demo", "buyer@example.com", "+15551234567"
)
```

**Invariant**: this normalization is defined once in this doc and MUST be
copied verbatim to `lead_capture()` tool code and its unit tests. Any
divergence creates invisible duplicates that pass CI but break idempotency
in production.

## `envelope` attribute — STAR-JSON shape

The `envelope` M (map) attribute carries the full STAR-JSON record. It is
intentionally aligned with the sibling `reservation_handoff` STAR shape at
the top level.

```json
{
  "envelope_type": "discover.lead_capture",
  "envelope_version": "v1",
  "tenantId": "<tenant>",
  "leadId": "LEAD-YYYYMMDD-<8char>",
  "sessionId": "<discoverSessionId>",
  "contactInfo": {
    "emailNormalized": "<lowercase + trim>",
    "phoneE164": "<E.164 format or empty string>",
    "contactHash": "<sha256-hex of tenantId|emailNormalized|phoneE164>"
  },
  "consent": {
    "contactConsent": true,
    "contactConsentTimestamp": "<ISO-8601 UTC>",
    "marketingConsent": false
  },
  "savedConfig": {
    "preferredCategory": "<categoryId or empty string>",
    "preferredModelId": "<catalog MODEL# model-id fragment or empty string>"
  },
  "personaSnapshot": null,
  "createdAt": "<ISO-8601 UTC>",
  "updatedAt": "<ISO-8601 UTC>",
  "associatedSessions": ["<discoverSessionId>"],
  "sourcePath": "pathA"
}
```

**`personaSnapshot`**: MUST be `null` (not omitted — DDB stores the
explicit NULL marker) for anonymous sessions. For LTM-hydrated or Path B
sessions, it is the serialized `PersonaSnapshot` map. Only named fields
from `SessionContext.personaSnapshot` are written — never raw Cognito
claims or PII beyond what was explicitly stated preference.

**`savedConfig`**: carry-forward from the Discover session into the Order
phase. `ConfiguratorFlow` reads this via `DiscoverHandoff.preferredCategory`
and `DiscoverHandoff.preferredModelId` to pre-select Step 1 (category) and
Step 2 (model). Empty strings indicate "no preference stated".

## Table configuration

| Setting | Value | Rationale |
|---|---|---|
| Billing mode | `PAY_PER_REQUEST` | Matches sibling Acquire tables. |
| PITR | Enabled | Protects customer lead data. |
| RemovalPolicy | `RETAIN` | Lead records are customer data; never auto-delete. |
| Encryption | AWS-managed (default) | v1 baseline; upgrade to customer-managed KMS in a follow-up spec. |
| TTL | None (v1) | Leads are kept indefinitely in v1. Retention policy is a follow-up spec. |

## IAM grants

The `agentCoreExecutionRole` (the AgentCore Runtime's execution role) requires
`dynamodb:PutItem`, `dynamodb:GetItem`, `dynamodb:Query`,
`dynamodb:UpdateItem` on both the table ARN and the GSI ARNs:

```typescript
// infrastructure/lib/vsa-core-stack.ts (snippet)
if (props.vsaAcquireLeadsTable) {
  props.vsaAcquireLeadsTable.grantReadWriteData(agentCoreExecutionRole);
}
```

This mirrors the sibling's `vsaAcquireOrders` conditional grant pattern.

## Worked example — end-to-end lead_capture flow

**Scenario**: anonymous buyer (Path A) taps "Save this / send me info"
after seeing a commuter model in DiscoverFlow.

1. **iOS → API Gateway → Lambda (`api-assistant-chat`)**:
   - User submits `lead_capture` turn with
     `contact_info={"email": "buyer@example.com", "phone": ""}` and
     `consent_flags={"contact_consent": true}`.

2. **`lead_capture()` tool in the agent**:
   - Validates `contact_consent == True` (required; returns validation error if false).
   - Computes `contactHash = SHA256("genericmoto-demo|buyer@example.com|")`.
   - Queries `by-tenant-and-contact` GSI: no existing record found.
   - Generates `leadId = "LEAD-20260728-a3f7bc21"`.
   - Writes item to `vsa-staging-acquire-leads` with `attribute_not_exists(leadId)` condition.
   - Returns `{"leadId": "LEAD-20260728-a3f7bc21", "wroteAt": "2026-07-28T15:30:00Z", "available": true}`.

3. **Second tap (idempotency check)**:
   - Same `contactHash` query finds the existing record.
   - `GetItem` by `leadId = "LEAD-20260728-a3f7bc21"` retrieves full record.
   - Returns `{"leadId": "LEAD-20260728-a3f7bc21", "wroteAt": "2026-07-28T15:30:00Z", "available": true}` — SAME `leadId`, no duplicate row.

4. **"Let's build this" convergence**:
   - `DiscoverHandoff(discoverSessionId="disc-<uuid>", leadId="LEAD-20260728-a3f7bc21", preferredCategory="commuter", preferredModelId="meridian-300", personaSnapshot=nil, sourcePath="pathA")` is passed into `ConfiguratorFlow`.
   - ConfiguratorFlow pre-selects Steps 1 + 2 from the handoff.
   - On `reservation_handoff` write: `vsa-acquire-orders` item stamps `discoverLeadId = "LEAD-20260728-a3f7bc21"` as an additive attribute.

**Zero internal-tenant brand strings**: the worked example uses `genericmoto-demo` as the
`tenantId` and generic model IDs. Internal-tenant `tenantId` values live
only in local seed data (never committed).

## Alignment with `reservation_handoff` STAR shape

The `discover.lead_capture` envelope shares these top-level fields with the
sibling `reservation_handoff` STAR envelope (documented in
`docs/VSA_STAR_JSON_SCHEMA.md` in the CVX repo):

| Field | Both envelopes | Notes |
|---|---|---|
| `envelope_type` | yes | Different values: `discover.lead_capture` vs `order.reservation_handoff` |
| `envelope_version` | yes | Both `"v1"` in the initial ship |
| `tenantId` | yes | Same tenant scoping |
| `createdAt` | yes | ISO-8601 UTC |
| `updatedAt` | yes | ISO-8601 UTC |

This alignment allows downstream analytics or audit tooling to parse the
shared header without knowing which envelope type it is reading.
