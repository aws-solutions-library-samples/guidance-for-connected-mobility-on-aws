# Deployment Scripts

This directory contains operator runbooks and utility scripts for CMS staging and production maintenance.

## Vehicle Classification Scripts

### backfill_vehicle_classification.py

**Purpose**: One-time operator-run backfill to assign `dataSource` classification to legacy vehicle rows that were created before the field was required. Backfill detects rows with null `dataSource`, classifies them by make/OEM source, and conditionally updates them.

**Spec Reference**: `2026-08-29-cms-vehicle-classification`

**Scope**:
- Staging: 68 vehicle rows with null `dataSource`
- Prod: 0 vehicle rows (validates empty prod surface and exercises `--confirm-prod` gate)

**Invocation**:

```bash
# Dry-run (fail-safe default, no writes)
python3 deployment/scripts/backfill_vehicle_classification.py \
  --stage staging \
  --apply

# Apply writes (after dry-run review)
python3 deployment/scripts/backfill_vehicle_classification.py \
  --stage staging \
  --apply

# Prod (requires explicit confirmation)
python3 deployment/scripts/backfill_vehicle_classification.py \
  --stage prod \
  --apply \
  --confirm-prod

# Certificate reconciliation (populate missing certificateId on onboard vehicles)
python3 deployment/scripts/backfill_vehicle_classification.py \
  --stage staging \
  --apply \
  --reconcile-certs
```

**Command-line Options**:
- `--stage {staging|prod}` — required, target environment
- `--apply` — execute writes (default: dry-run, no writes)
- `--confirm-prod` — required when `--stage prod` (fail-safe guard)
- `--reconcile-certs` — populate missing `certificateId` on onboard vehicles with cert rows
- `--vin <vin>` — narrow scope to single vehicle by VIN
- `--vehicle-id <id>` — narrow scope to single vehicle by vehicleId

**Features**:
- Fail-closed: unset env vars (`VEHICLES_TABLE_NAME`, `VEHICLE_CERTIFICATES_TABLE`, `FLEETS_TABLE_NAME`) cause immediate failure
- Conditional writes: `ConditionExpression` skips rows already classified, handling concurrent races safely
- Idempotent: re-running after success skips all rows (safe to re-run multiple times)
- CSV report: writes pre/post state to `deployment/scripts/reports/backfill_<timestamp>.csv`
- Verification: post-write scan confirms `dataSource` is now present and classifiable

**Output**:
```
Scanning cms-staging-storage-vehicles for null dataSource ...
Found 68 rows to backfill

Classifying by make:
  - Ford (48 rows) → cloud-telemetry (oem_source == oem1)
  - Meridian (12 rows) → vehicle-telemetry (oem_source not set)
  - DemoMotors (6 rows) → vehicle-telemetry
  - AcmeAuto (3 rows) → vehicle-telemetry

Dry-run: would update 68 rows
Final tally:
  scanned: 68
  rewritten: 68 (or 0 if --apply was already run)
  skipped(already_classified): 0
  failed: 0

Backfill complete. ✓
```

**See Also**: Operator runbook at `.kiro/portfolio/initiatives/2026-08-29-cms-vehicle-classification/runbook.md`

---

### scrub_vehicle_brand_identifiers.py

**Purpose**: Rewrite `vin` and `fleetId` fields on specified vehicle rows. Supports bulk substitution of old brand-bearing identifiers with new generic ones. No brand strings appear in the script source; all old/new pairs are provided at invocation.

**Spec Reference**: `2026-08-29-cms-vehicle-classification`

**Scope**:
- Staging scope: 2 rows (VEH-MRDN-0015, VEH-MICH-001)
- Report: fan-out count of references across `vehicle-certificates`, `service-history`, `maintenance-alerts` tables

**Invocation** (values below are placeholders — the actual old/new
mapping pairs live in the operator runbook under `.kiro/`, not in this
shipping doc):

```bash
python3 deployment/scripts/scrub_vehicle_brand_identifiers.py \
  --stage staging \
  --vehicle-id <vehicle-id-1> \
  --vehicle-id <vehicle-id-2> \
  --vin-map <old-vin-1>:<new-vin-1> \
  --vin-map <old-vin-2>:<new-vin-2> \
  --fleet-map <old-fleet-1>:<new-fleet-1> \
  --fleet-map <old-fleet-2>:<new-fleet-2> \
  --include-fan-out \
  --apply
```

**Command-line Options**:
- `--stage {staging|prod}` — required, target environment
- `--vehicle-id <id>` — repeatable, specify each vehicle to scrub
- `--vin-map old:new` — repeatable, map old VIN to new VIN
- `--fleet-map old:new` — repeatable, map old fleetId to new fleetId
- `--include-fan-out` — extend the scrub to denormalised copies of `vin`/`make`/`model` on `service-history` and `vehicle-certificates`. Recommended for every real run; the vehicles-table-only scrub leaves brand-bearing residue on the sibling tables invisible to any single-table reader. Added in commit `8aa4d9c7` post-close.
- `--apply` — execute writes (default: dry-run / report only)

**Features**:
- No brand strings in code: all mapping values are argv-driven. The
  operator runbook under `.kiro/` (not shipped) carries the actual
  pairs.
- Report-only by default: lists row counts per table without mutations
- Conditional writes: skips rows if old value not found (idempotent)
- Fan-out report: shows count of matching rows in downstream tables (cert, service history, alerts) for audit

**Output** (shape only; concrete values are runbook-owned):

```
Staging brand-scrub report (no writes):
  Target vehicles: <vehicle-id-1>, <vehicle-id-2>

Replacements:
  VINs:   <old-vin-1> → <new-vin-1>
          <old-vin-2> → <new-vin-2>
  Fleets: <old-fleet-1> → <new-fleet-1>
          <old-fleet-2> → <new-fleet-2>

Fan-out analysis (rows that reference these vehicles):
  vehicle-certificates: N rows
  service-history:      M rows
  maintenance-alerts:   K rows  (not written — audit only, no brand-bearing attributes)

Ready to apply. ✓
```

**See Also**: Operator runbook at `.kiro/portfolio/initiatives/2026-08-29-cms-vehicle-classification/runbook.md`

---

## Other Scripts

For documentation on other deployment scripts, see the script file headers or the relevant spec in `~/.kiro/specs/`.
