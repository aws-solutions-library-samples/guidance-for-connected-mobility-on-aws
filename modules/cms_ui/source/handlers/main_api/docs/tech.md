# main_api — Verified Technical Patterns

## DTC Dedup — CDK GSI + Python API Patterns

Spec: `.kiro/specs/2026-06-17-dtc-dedup-first-last-seen-schedule-service/spec.md`
Verified: 2026-06-18

### (d) CDK Table.add_global_secondary_index Syntax

Verified against the aws-cdk-lib version pinned in `deployment/requirements.txt`:
`aws-cdk-lib>=2.100.0`.

From existing usage in `deployment/stacks/storage_stack.py` (lines 48-57, 85-97,
707-710, 180-193) — the Python CDK v2 `Table.add_global_secondary_index` signature
accepts named arguments matching the `GlobalSecondaryIndexProps` interface:

```python
from aws_cdk import aws_dynamodb as dynamodb

# Existing verified pattern from storage_stack.py:707-710 (warranty_claims GSI)
self.tables['warranty_claims'].add_global_secondary_index(
    index_name="vehicleId-index",
    partition_key=dynamodb.Attribute(name="vehicleId", type=dynamodb.AttributeType.STRING),
    projection_type=dynamodb.ProjectionType.ALL,
)

# Existing verified pattern with sort_key from storage_stack.py:85-97 (service_history GSI)
self.tables['service_history'].add_global_secondary_index(
    index_name="ServiceTypeIndex",
    partition_key=dynamodb.Attribute(
        name="serviceType",
        type=dynamodb.AttributeType.STRING
    ),
    sort_key=dynamodb.Attribute(
        name="serviceDate",
        type=dynamodb.AttributeType.STRING
    )
)

# New active-code-index for dtc_history (sparse GSI):
self.tables['dtc_history'].add_global_secondary_index(
    index_name="active-code-index",
    partition_key=dynamodb.Attribute(name="vehicleId", type=dynamodb.AttributeType.STRING),
    sort_key=dynamodb.Attribute(name="activeCode", type=dynamodb.AttributeType.STRING),
    projection_type=dynamodb.ProjectionType.ALL,
)
```

Key points:
- `index_name` (str): name of the GSI — shown in `QueryRequest.indexName()`.
- `partition_key` / `sort_key`: `dynamodb.Attribute(name=..., type=dynamodb.AttributeType.STRING | NUMBER | BINARY)`.
- `projection_type`: `dynamodb.ProjectionType.ALL` — all item attributes projected (needed so the upsert path has the full row for UpdateItem without a subsequent GetItem).
- Sparse semantics are automatic — no extra config. Items without `activeCode` are simply not indexed.
- `billing_mode` is NOT re-specified on `add_global_secondary_index`; it inherits from the table (PAY_PER_REQUEST for dtc_history).

- Source: `deployment/stacks/storage_stack.py` lines 48-57, 85-97, 180-210, 707-710 (this repo)
- Verified: 2026-06-18

### (f) _approve_dtc_action_followups — Extraction Surface for _create_service_for_dtc

The existing function is at `modules/cms_ui/source/handlers/main_api/index.py:402`.

**Signature** (line 402):
```python
def _approve_dtc_action_followups(action_id, vehicle_id, vin, dtc_id, dtc_code,
                                  system, resolver, resolved_at_iso,
                                  severity='HIGH'):
```

**What it does** (two sequential writes, both best-effort):

1. **Service-history write** (lines 437-530):
   - Constructs `service_id = f"SVC-{dtc_id[:8]}-{int(time.time())}"`.
   - Builds `service_record` dict with fields: `vehicleId`, `serviceDate`, `serviceType='DIAGNOSTIC_REPAIR'`, `serviceId`, `status='scheduled'`, `description` (human-readable from DTC lookup), `provider='Fleet Command Center'`, `providerType='Operator Approved'`, `triagePriority` (P0–P3 from severity map), `requestNumber`, `reportedSymptom`, `notes`, `category='DTC_TRIGGERED'`, `source='fleet-command-center'`, `dealerId='auto-scheduled'`, `technician=resolver`, `serviceDetails` (nested dict with `trigger`, `dtcCode`, `dtcId`, `system`, `severity`, `description`), `triggerActionId`, `triggerDtcId`, `triggerDtcCode`, `createdAt`, `updatedAt`.
   - Calls `service_history_table.put_item(Item=service_record)`.
   - Sets `out['serviceScheduled'] = True` and `out['serviceId'] = service_id`.

2. **DTC clear write** (lines 531-590):
   - Queries `dtc-history` by `vehicleId` + `FilterExpression='dtcId = :d'` to recover `timestamp` (the sort key).
   - `max(items, key=lambda x: int(x.get('timestamp', 0)))` selects the row.
   - Calls `dtc_table.update_item(Key=..., UpdateExpression='SET #s = :s, clearedDate = :c, relatedServiceId = :r', ExpressionAttributeNames={'#s': 'status'}, ExpressionAttributeValues={':s': 'CLEARED', ':c': resolved_at_iso, ':r': out.get('serviceId', '')})`.
   - Sets `out['dtcCleared'] = True`.

**Extraction scope for `_create_service_for_dtc`**:

The new helper extracts **only the service-history write** (step 1 above). The signature becomes:

```python
def _create_service_for_dtc(action_id, vehicle_id, vin, dtc_id, dtc_code,
                             system, severity, resolver, resolved_at_iso,
                             dtc_human_desc=None, notes=None):
    """Write a service-history row for a DTC. Does NOT clear the DTC.
    Returns dict with serviceId (str) or raises on failure."""
```

`_approve_dtc_action_followups` is refactored to call `_create_service_for_dtc`, then perform the additional `SET #s = :s, clearedDate = :c, relatedServiceId = :r REMOVE activeCode` step on the DTC row.

The new `POST /schedule-service` endpoint calls `_create_service_for_dtc` and then does **only** `SET relatedServiceId = :r` on the DTC row (status stays ACTIVE, `activeCode` stays — not cleared).

**DTC description lookup** (lines 440-454): best-effort `Query` on `dtc-history` by `vehicleId + dtcId` filter — this is also needed by `_create_service_for_dtc` to populate `description` and `reportedSymptom`. Pass `dtc_human_desc` as a parameter to avoid the lookup when the caller already has it.

- Source: `modules/cms_ui/source/handlers/main_api/index.py:402-590` (this repo)
- Verified: 2026-06-18
