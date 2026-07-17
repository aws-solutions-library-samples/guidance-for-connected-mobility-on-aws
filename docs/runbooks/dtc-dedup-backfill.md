# Runbook: DTC Dedup Backfill

Collapses duplicate ACTIVE processor-sourced rows in `cms-{stage}-storage-dtc-history`.
Background: `MaintenanceProcessor` previously issued a fresh `PutItem` per detection; a persistent
fault accumulated multiple ACTIVE rows per `(vehicleId, code, source)`. This script consolidates
them to one row per group, keeping the earliest `firstSeenAt` winner.

---

## Prerequisites

- Python ≥ 3.11 with `boto3` installed (`pip install boto3`)
- AWS credentials with access to the target account/region (profile or env vars)
- IAM permissions on `cms-{stage}-storage-dtc-history`:
  - `dynamodb:Scan`
  - `dynamodb:UpdateItem`
  - `dynamodb:DeleteItem`
- Run from: `connected-mobility-guidance-on-aws/` root (or `deployment/scripts/`)
- **Run AFTER** Group 2.1 (CDK GSI) and Group 2.2 (Flink upsert helper) are deployed and validated.
  Running before the upsert helper is live risks the live Flink job recreating dups immediately.

---

## Row-count smoke check (before)

Record the duplicate count for the known-noisy vehicle `VEH-1780003031` before running `--apply`:

```bash
aws dynamodb query \
  --table-name cms-staging-storage-dtc-history \
  --region ap-northeast-1 \
  --key-condition-expression "vehicleId = :v" \
  --filter-expression "#s = :active" \
  --expression-attribute-names '{"#s": "status"}' \
  --expression-attribute-values '{":v": {"S": "VEH-1780003031"}, ":active": {"S": "ACTIVE"}}' \
  --select COUNT \
  --output json | jq '.Count'
```

Note the count. After `--apply`, re-run — the count should drop to the number of distinct
`(code, source)` combinations for that vehicle (typically 1–5).

---

## Step 1: Dry run

```bash
python3 deployment/scripts/backfill_dtc_dedup.py \
  --stage staging \
  --region ap-northeast-1 \
  2>&1 | tee /tmp/backfill-dryrun-staging.log
```

Expected output sections:

```
Table: cms-staging-storage-dtc-history  mode: DRY-RUN
[DRY-RUN] groups inspected: <N>
[DRY-RUN] dups found: <M> rows in <K> dup groups
  would update VEH-... ts=... code=P0217 → lastSeenAt=... occurrenceCount=5; would delete 4 loser(s)
  ...
```

Review the plan. Verify the vehicles and codes listed match expected noisy sources.

---

## Step 2: Apply (interactive)

```bash
python3 deployment/scripts/backfill_dtc_dedup.py \
  --stage staging \
  --region ap-northeast-1 \
  --apply
```

You will be prompted:

```
About to apply dedup writes to cms-staging-storage-dtc-history in ap-northeast-1.
This will DELETE loser rows (unrecoverable). Type 'yes' to confirm:
```

Type `yes` to proceed. Final output:

```
[DONE] groups_inspected=<N> dups_found=<M> updated=<K> deleted=<L> errors=0
```

### Non-interactive (CI / automation)

```bash
python3 deployment/scripts/backfill_dtc_dedup.py \
  --stage staging \
  --region ap-northeast-1 \
  --apply --yes
```

---

## Step 3: Row-count smoke check (after)

Re-run the query from the smoke check above. Count should equal the number of distinct
`(code, source)` combinations — typically much lower than the before count.

Example expected transition for `VEH-1780003031` with P0217 duplicated 5×:

| Before | After |
|--------|-------|
| 5      | 1     |

---

## Custom source filter

By default the script processes: `flink-maintenance-processor`, `fwe-uds-dtc`, `oem1-uds-dtc`, `dtc-fwe-uds`.

To restrict to a subset:

```bash
python3 deployment/scripts/backfill_dtc_dedup.py \
  --stage staging \
  --region ap-northeast-1 \
  --source-filter flink-maintenance-processor
```

---

## Rollback notes

- **CLEARED rows**: untouched by the script. They remain as-is in the table for audit history.
- **Deleted ACTIVE losers**: unrecoverable once `--apply` runs. The default `--dry-run` mode is the
  safeguard — review the plan before committing.
- **Winner UpdateItem**: idempotent. Re-running `--apply` on an already-collapsed table is a no-op
  (no group has >1 ACTIVE row, so zero writes occur).
- **Concurrent Flink writes**: if the live Flink job writes a new dup between dry-run and apply,
  a second `--apply` pass will collapse it. Recommend running during a low-traffic window.
- **If errors > 0**: the script exits non-zero and prints per-row error details. Fix the IAM/network
  issue and re-run — idempotent design means partial runs are safe to retry.

---

## Known limits

- The script uses `Scan` (full-table), which consumes read capacity. On a large table, consider
  running with `--source-filter` to limit scope, or during off-peak hours.
- `occurrenceCount` on the winner is set to `len(group)` (count of pre-backfill ACTIVE rows), which
  is a lower bound on true detections (some detections may not have produced rows if Flink dropped
  them). This is acceptable for historical context — post-backfill, the live upsert path will
  increment `occurrenceCount` accurately.
