# Backfill: Fleet `data_source` Enum Rename

**Date**: 2026-06-09  
**Spec**: `2026-06-09-cms-data-source-model-refactor`  
**Script**: `backfill_data_source_enum.py`

This runbook documents the one-shot backfill procedure to rewrite legacy `data_source` enum values on the fleets table.

## Rewrites

```
onboard-fwe  → vehicle-telemetry
cloud-oem1   → cloud-telemetry
```

## When to Run

After Phase A + Phase B deploy and smoke-tests pass:

1. **Phase A** — Lambda M3 checks accept BOTH old and new strings (dual-read)
2. **Phase B** — Frontend forms emit ONLY new strings
3. **Smoke-tests** — Manual verification confirms no regressions
4. **Then**: Run the backfill script

## Prerequisites

- **AWS credentials**: The deployment account for the target stage (staging or prod)
- **Deploy state**: Phase A + Phase B code deployed to CloudFormation
- **Smoke-tests**: Manual UAT confirms Create Vehicle, fleet picker, and OEM1 vehicle flows work
- **Region**: Specify via `--region` flag (e.g., `us-west-2`)
- **Stage**: Specify via `--stage` flag (e.g., `staging` or `prod`)

## Invocation Examples

### Dry-Run (fail-safe default)

```bash
python3 deployment/scripts/backfill_data_source_enum.py \
  --stage staging \
  --region us-west-2 \
  --dry-run
```

**Output**: Prints proposed changes (count + sample rows). No DynamoDB writes.

Review the proposed changes. Confirm:
- Every row scanned has a `data_source` attribute
- Old strings (`onboard-fwe`, `cloud-oem1`) are correctly mapped to new values
- Row count makes sense (should match your fleet count)

### Apply (after dry-run review)

```bash
python3 deployment/scripts/backfill_data_source_enum.py \
  --stage staging \
  --region us-west-2 \
  --apply
```

**Output**: Executes UpdateItem calls; prints final tally.

### Profile Override (optional)

```bash
python3 deployment/scripts/backfill_data_source_enum.py \
  --stage staging \
  --region us-west-2 \
  --apply \
  --profile <aws-profile-name>
```

If not specified, the script uses the default AWS credentials.

## Expected Output

Typical run produces:

```
Scanning cms-staging-storage-fleets ...
Scan complete: 42 rows scanned

Proposed changes (dry-run):
  - onboard-fwe → vehicle-telemetry: 35 rows
  - cloud-oem1 → cloud-telemetry: 7 rows

Sample changes:
  - fleetId="cms-native-fleet-1", data_source="onboard-fwe" → "vehicle-telemetry"
  - fleetId="oem1-staging-fleet", data_source="cloud-oem1" → "cloud-telemetry"
  ...

Final tally:
  scanned: 42
  rewritten: 42
  skipped(already_new): 0
  skipped(no_attribute): 0
  failed: 0

Backfill complete. ✓
```

### With Apply

```
Scanning cms-staging-storage-fleets ...
Scan complete: 42 rows scanned

Rewriting ...
Final tally:
  scanned: 42
  rewritten: 42
  skipped(already_new): 0
  skipped(no_attribute): 0
  failed: 0

Backfill complete. ✓
```

## Idempotency Assertion

Run the script a **second time with `--apply`** immediately after success:

```bash
python3 deployment/scripts/backfill_data_source_enum.py \
  --stage staging \
  --region us-west-2 \
  --apply
```

**Expected output**: `rewritten: 0` (proves idempotency — no changes the second time).

This confirms:
- Rows written in the first run stay in new form
- Concurrent fleet creations (if any) use the new enum values
- The backfill is safe to re-run without side effects

## Troubleshooting

### Concurrent-Write Race Semantics

If a fleet is created by the UI **during the backfill**, the new row will emit `cloud-telemetry` or `vehicle-telemetry`. The script's `ConditionExpression` checks `attribute_exists(data_source) AND data_source IN (:old1, :old2)` and skips rows that don't match. This means:

- **If the new row is written BEFORE the script reaches it**: The script's UpdateItem fails the condition check and counts it as `skipped(already_new)`. ✓ Correct.
- **If the new row is written AFTER the script finishes**: No issue — the row is already in new form. ✓ Correct.

**No manual intervention needed** — the conditional logic handles races safely.

### AWS API Errors

#### `ConditionalCheckFailedException`

This is **expected and safe**. It means the row is already in new form (or has no `data_source` attribute). The script counts it as `skipped(already_new)`, not a failure.

#### `ThrottlingException` or `ProvisionedThroughputExceededException`

The backfill uses `Scan` with a default `--page-limit` of 10. If you hit throttling:

1. Run `--dry-run` first to see how many rows need rewriting
2. If > 100 rows, consider spreading the backfill across multiple runs with a delay between batches
3. Contact DynamoDB provisioning ops to increase capacity if needed

#### `AccessDeniedException`

Verify AWS credentials and IAM role permissions:

- **Required action**: `dynamodb:UpdateItem` on the fleets table
- **Check**: `aws sts get-caller-identity` to confirm your account/role
- **Check**: Run a test query: `aws dynamodb scan --table-name cms-staging-storage-fleets --region us-west-2 --limit 1`

#### `ResourceNotFoundException`

The fleets table was not found. Check:

1. **Stage spelling**: `--stage staging` (not `--stage Staging`)
2. **Region**: `--region us-west-2` (not default region if different)
3. **Account**: Confirm you're in the correct AWS account for the stage
4. **Table name**: Verify the table exists with a manual query:
   ```bash
   aws dynamodb scan \
     --table-name cms-staging-storage-fleets \
     --region us-west-2 \
     --limit 1
   ```

If the table name doesn't match, set the environment variable:

```bash
FLEETS_TABLE_NAME=cms-staging-storage-fleets-custom python3 \
  deployment/scripts/backfill_data_source_enum.py \
  --stage staging \
  --region us-west-2 \
  --apply
```

## See Also

- **Spec**: `~/.kiro/specs/2026-06-09-cms-data-source-model-refactor/spec.md` § "Backfill script (Phase C)"
- **Decisions**: `~/.kiro/specs/2026-06-09-cms-data-source-model-refactor/decisions.md` § OQ4 (location rationale)
- **Fleet docs**: `services/connectors/oem1/README.md` § "Data Model"
