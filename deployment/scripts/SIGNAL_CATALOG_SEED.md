# Signal-Catalog Seed Footguns

This doc captures known sharp edges in `deployment/scripts/seed_signal_catalog.py`
that an operator running the script can hit. Read this before invoking the
seed in any environment where data already exists in the target table.

If you are touching `seed_signal_catalog.py` to add new signals or change
its seeding logic, also pick up the "Recommended hardenings" section
below as part of that work.

---

## Footgun 1: `seed_signals()` CLOBBERS the live signal-catalog table

**What happens.** `seed_signals()` reads `signal_catalog_seed.json` (a frozen
snapshot of 262 signals) and uses `table.batch_writer().put_item(Item=item)`
to write every row into `cms-{stage}-signal-catalog`. DynamoDB's
`batch_writer` does NOT support `ConditionExpression`, so every existing
row whose **full primary key** matches a snapshot row is silently
**overwritten** by the snapshot value.

> **Key correction (verified empirically 2026-06-16).** The table's primary
> key is **composite** — `signal_group` (HASH) + `signal_name` (RANGE) — NOT
> `signal_name` alone (earlier revisions of this doc said "partition key =
> `signal_name`"; that is wrong and materially changes the clobber analysis).
> `put_item` overwrites only on an exact `(signal_group, signal_name)` match.
> Consequences when reconciling snapshot vs. live:
> - Same `(signal_group, signal_name)` with differing non-key attrs → **true
>   clobber** (operator edit lost).
> - Same `signal_name` under a **different** `signal_group` → NOT a clobber;
>   `put_item` creates a **duplicate** row and leaves the live row intact
>   (a different, stale-row hazard).
> - A clobber analysis keyed on `signal_name` alone will report false
>   positives — key on the composite PK.

**Impact.** Any operator who ran the script after the table had been
modified at runtime — e.g., a custom OEM signal added by hand, an
operator-tweaked `cycle_ms` value, a manually-updated `signal_id` for
an emergency hotfix — will lose those modifications. The script does
not warn; the snapshot just wins.

**Confusing detail.** The `seed_oem1_signals()` function in the same file
DOES use `ConditionExpression='attribute_not_exists(signal_name)'` so it
is genuinely idempotent (it skips existing rows, doesn't clobber them).
The contrast between the two functions is easy to miss on a casual read.
(Idempotency is per **composite** key: a row is skipped only when the exact
`(signal_group, signal_name)` already exists; the same `signal_name` under a
different `signal_group` would still be written as a new row.)

**Workaround when you must seed without clobbering existing rows.**
1. Inspect the snapshot file (`signal_catalog_seed.json`) and the live
   table contents side-by-side; reconcile any drift before re-seeding.
2. OR temporarily fork `seed_signals()` to use the same conditional-write
   pattern as `seed_oem1_signals()` (single-item `put_item` with
   `ConditionExpression`). This sacrifices throughput (262 sequential
   puts vs. one batch-write call) but is safe-by-default.
3. NEVER run the seed against `cms-prod-*` without coordinating with
   whoever last touched the live signal catalog. There is no prod
   guard in the script (see Footgun 2).

## Footgun 2: Table name from `DEPLOYMENT_STAGE` env var with no prod-guard

**What happens.** The script computes its target tables from the
`DEPLOYMENT_STAGE` environment variable, defaulting to `'dev'`:

```python
STAGE = os.environ.get('DEPLOYMENT_STAGE', 'dev')
SIGNAL_TABLE = f'cms-{STAGE}-signal-catalog'
EVENT_TABLE  = f'cms-{STAGE}-event-catalog'
```

There is no `--table` CLI override, no `--stage` argument with `choices`,
and no post-resolution guard that refuses to write to a `cms-prod-*`
table.

**Impact.** An operator who:
- exports `DEPLOYMENT_STAGE=prod` for an unrelated reason and forgets to
  unset it before running the seed, OR
- runs in a shell where the env var is sourced from a `prod.env` file by
  prior tooling, OR
- runs with `DEPLOYMENT_STAGE=` empty (resolves to `cms--signal-catalog`,
  invalid table → fail-loud, but only because the table doesn't exist;
  if a future stage named `<empty>` ever exists, this changes)

…can clobber the wrong table. The companion script
`align_event_catalog_signals.py` learned this lesson — it has a
post-argparse guard:

```python
if "prod" in args.table.lower():
    sys.exit(2)
```

**Workaround.** Always invoke the seed with `DEPLOYMENT_STAGE` explicitly
set in the same command line, never sourced silently from a shell env:

```bash
DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 AWS_PROFILE=cms-staging \
    python3 deployment/scripts/seed_signal_catalog.py --dry-run
```

…and inspect the dry-run output before re-running without `--dry-run`.

## Footgun 3: `seed_events()` has the same clobber pattern

`seed_events()` writes 20 hard-coded events from the `EVENTS` list (in
`seed_signal_catalog.py` itself, NOT from the JSON snapshot) into
`cms-{stage}-event-catalog` via the same `batch_writer().put_item`
pattern. Same clobber semantics.

The catalog updater script `align_event_catalog_signals.py` was authored
specifically to be the safe, idempotent, dry-run-default tool for
event-catalog edits. **Prefer it over `seed_events()`** when you need
to change anything about the event catalog after the initial seed.

---

## Recommended hardenings (TODO for whoever next touches the script)

When `seed_signal_catalog.py` next gets a substantive edit (e.g., the
in-flight `2026-06-15-cms-event-signal-contract-alignment` spec's Group
2 signal additions), the following hardenings should be picked up
together. They are not blockers for the current scope, but they remove
sharp edges from a tool every operator hits.

1. **Add a prod-guard** matching the pattern in
   `align_event_catalog_signals.py:350-352`:

   ```python
   if "prod" in SIGNAL_TABLE.lower() and "--allow-prod" not in sys.argv:
       sys.stderr.write(
           f"❌ Refusing to seed cms-prod table {SIGNAL_TABLE!r}. "
           f"Pass --allow-prod to override (you almost certainly should not).\n"
       )
       sys.exit(2)
   ```

2. **Switch `seed_signals()` to the conditional-write pattern**
   `seed_oem1_signals()` already uses, so the snapshot can never
   silently clobber a live row. Default-deny semantics; an `--overwrite`
   flag makes the clobber explicit when an operator actually wants it.

3. **Switch `seed_events()` to use `align_event_catalog_signals.py` (or
   the same conditional-write pattern)** for the same reason.

4. **Add `--table` and `--stage` CLI args** so the env var is no longer
   the only path to the table name. Argparse `choices=["staging",
   "prod"]` plus the prod guard makes the intended target unambiguous
   on every invocation.

5. **Print a backup-before-write hint** with the resolved table name
   before any write occurs, so the operator's terminal scrollback
   captures the exact target.

6. **Mark the script read-only-by-default in dry-run mode the same way
   `align_event_catalog_signals.py` does** (a `mutually_exclusive_group`
   with `default=True` for dry-run).

These six items roughly translate the safety affordances already in
`align_event_catalog_signals.py` into the seed script. They should be a
single PR with `seed_signal_catalog.py` and a paired test.

---

## Cross-references

- `deployment/scripts/seed_signal_catalog.py` — the file in question.
- `deployment/scripts/align_event_catalog_signals.py` — the safer pattern
  the seed should adopt.
- `.kiro/specs/2026-06-15-cms-event-signal-contract-alignment/` — the
  in-flight spec that adds new event-contract-gap signals to the seed.
  When that spec ships its Group-4 staging apply, the operator running
  the apply MUST be aware of the clobber footgun (this doc).
- `~/.kiro/steering/secrets-handling.md` — for the broader
  rotate-then-remove pattern that motivates the "fix the live system
  first, then the source" discipline.
