# Severity Vocabulary

> The canonical severity scale across the CMS ecosystem is:
>
> **`CRITICAL` → `HIGH` → `MEDIUM` → `LOW`**

Every operator-facing surface (UI badges, Pending Actions card, alerts table,
chat agent responses, notifications) uses these four words, in exactly this
form, in exactly this order of decreasing severity.

This document is the single source of truth. When a new producer or consumer
is added to the platform, or when a vocabulary mismatch is found, update the
mapping table here first, then update the code.

---

## Why this matters

Before this document existed, the platform used **three** different severity
vocabularies across various tables and code paths:

1. **Word-severity** (`CRITICAL`/`HIGH`/`MEDIUM`/`LOW`) — the UI vocab, what
   most pipelines write to `dtc-history` and `maintenance-alerts`.
2. **Numeric-severity** (`1`/`2`/`3`/`4`) — seeded into `event-catalog` by
   `seed_vsa_demo_events.py` and into 52 legacy `safety-events` rows.
3. **SAE DTC hint** (`P0`/`P1`/`P2`/`P3`) — industry-standard DTC severity
   code, stored in `event-catalog.severity_hint`.

Nothing in code enforced the mapping, no doc explained it, and the two
numeric-scale conventions (Word-severity → LOW is "lowest"; Numeric-severity
→ 1 is "lowest" but 4 is "highest"; SAE → P0 is "highest") created real
operator confusion: `severity=4` looks like the least urgent thing in the
world until you realize it means "stop driving immediately."

---

## The canonical mapping

| UI / canonical | SAE DTC (`severity_hint`) | Legacy numeric (`severity`) | Meaning                                         |
| -------------- | ------------------------- | --------------------------- | ----------------------------------------------- |
| `CRITICAL`     | `P0`                      | `4`                         | Stop driving, safety-impacting                  |
| `HIGH`         | `P1`                      | `3`                         | Service within 48 hours                         |
| `MEDIUM`       | `P2`                      | `2`                         | Service within a week                           |
| `LOW`          | `P3`                      | `1`                         | Monitor, no immediate action                    |

Note: in the legacy numeric form, **higher number = higher severity**, which
is backwards from what SAE DTC codes use (P0 is the highest). This is the
single most common source of confusion and is why we're canonicalizing on
the word form.

---

## Where each form is allowed

| Table / Field                            | Canonical (`CRITICAL`/...) | SAE hint (`P0`/...) | Numeric | Notes                                                                  |
| ---------------------------------------- | :------------------------: | :-----------------: | :-----: | ---------------------------------------------------------------------- |
| `cms-<stage>-storage-dtc-history.severity`       | ✅                | ❌                  | ❌      | Written by Flink processors + simulator — always word form.            |
| `cms-<stage>-storage-maintenance-alerts.severity`| ✅                | ❌                  | ❌      | Same.                                                                  |
| `cms-<stage>-storage-safety-events.severity`     | ✅                | ❌                  | ❌      | 52 legacy rows had `1`/`2` — backfilled by `backfill_safety_event_severity.py`. |
| `cms-<stage>-event-catalog.severity`             | ❌                | ❌                  | ✅      | **Deprecated**. Kept for backwards compat. Readers should prefer `severity_hint`. |
| `cms-<stage>-event-catalog.severity_hint`        | ❌                | ✅                  | ❌      | **SAE DTC semantics.** Kept because P0-P3 carry industry-meaningful information (P0 = "powertrain mandatory regulation", etc.) that the word form can't express. |
| `cms-<stage>-storage-recalls.severity`           | ❌ (`Title`)      | ❌                  | ❌      | Uses title-case (`Critical`/`High`/`Medium`/`Low`) — NHTSA-style. `_normalize_severity` accepts and upper-cases these. Left as title-case because the NHTSA data source uses that form and rewriting would break diff-based refresh. |
| `cms-<stage>-vfo-action-queue.severity`          | ✅ (new)          | ✅ (VSA legacy)     | ❌      | Writers may emit either form; the `_normalize_action` helper in `main_api/index.py` converts to canonical on read. |
| `cms-<stage>-vfo-action-queue.priority`          | ✅                | ❌                  | ❌      | Intentionally different from severity — "priority" is operator urgency (which may differ from raw severity once we know context). See "Severity vs priority" below. |

---

## Severity vs priority (they're different on purpose)

**Severity** is a property of the underlying event — inherent, immutable,
derived from the signal that triggered it. A brake-system-fault DTC is
`CRITICAL` regardless of context.

**Priority** is a property of the operator-facing action queued for review.
It may differ from severity:

- A `CRITICAL` brake DTC on a vehicle that's already in the shop with an open
  service ticket is `MEDIUM` priority — the problem is already being handled.
- A `HIGH` maintenance recommendation on a vehicle about to cross a border
  for a cross-country trip is `CRITICAL` priority — time-sensitive.

In practice today, the Flink DTC emitters set `priority = severity` for
simplicity. A future VFO policy layer could re-rank based on fleet context.
The UI intentionally renders `priority` on Pending Actions, not `severity` —
it's what the operator should act on, not the raw event classification.

Both are shown on the Pending Actions card so operators can see both
dimensions.

---

## Canonical conversion rules

When reading from a producer that may use a non-canonical form, apply these
rules to normalize to the canonical vocabulary:

### SAE hint → canonical

| Input     | Output      |
| --------- | ----------- |
| `P0`      | `CRITICAL`  |
| `P1`      | `HIGH`      |
| `P2`      | `MEDIUM`    |
| `P3`      | `LOW`       |

### Numeric → canonical (legacy)

| Input     | Output      |
| --------- | ----------- |
| `4`       | `CRITICAL`  |
| `3`       | `HIGH`      |
| `2`       | `MEDIUM`    |
| `1`       | `LOW`       |

### Canonical pass-through

`CRITICAL`/`HIGH`/`MEDIUM`/`LOW` pass through unchanged. Case-insensitive
input is accepted and normalized to upper-case.

### Unknown input

Anything else (empty, null, garbage) → `MEDIUM` as a safe default. Log a
warning so the offending producer can be identified.

---

## Where the conversion lives

One helper, imported everywhere. Pick ONE of these, not more, depending on
language:

### Python (Lambdas, scripts)

Define `_normalize_severity(value)` co-located with any code that needs it
or extract to a shared module. The helper in
`modules/cms_ui/source/handlers/main_api/index.py :: _normalize_action` is
the authoritative implementation; copy its logic, don't re-derive.

### Java (Flink processors)

Not needed today — the Flink processors already write canonical form
directly. If you ever need to *read* event-catalog `severity_hint` into a
canonical field, mirror the Python logic in a static helper.

### TypeScript (frontend)

The frontend should never need to convert — all data from the API is
already canonical. If you find yourself writing a converter in React code,
it means a backend code path is skipping the normalizer. Fix it there
instead.

---

## Historical data

Pre-canonicalization rows in DDB tables (`dtc-history`, `maintenance-alerts`,
`safety-events`) are mostly already in canonical form. Known exceptions:

- **Legacy `safety-events` rows** that pre-date canonicalization had
  `severity='1'` / `severity='2'` and are backfilled by
  `deployment/scripts/backfill_safety_event_severity.py`. The script
  is idempotent and can be re-run against a fresh environment.
- **`vfo-action-queue` rows** from external producers (e.g. the VSA
  Virtual Service Advisor integration) may arrive with `severity='P0'`
  and no canonical equivalent; the `_normalize_action` helper maps
  these on read. New producers should emit canonical values directly —
  see [For new producers](#for-new-producers).

---

## For new producers

When adding a new producer that writes severity/priority data:

1. **Write canonical values** (`CRITICAL`/`HIGH`/`MEDIUM`/`LOW`) whenever
   possible. No new numeric scales. No new prefixes.
2. **If you're in event-catalog**, use `severity_hint` with `P0`-`P3` values.
   The numeric `severity` field is deprecated; don't populate it for new
   rows unless you have a reason.
3. **If the information you're recording is genuinely different from the
   canonical 4 levels** (e.g., you need a separate dimension like confidence,
   urgency, or blast radius), give it a different field name. Don't squeeze
   it into `severity`.
4. **Add a row to the tables above** in this document describing your field
   so future operators know where to look.

---

## For new consumers (readers)

When reading severity/priority from any DDB table:

1. Call the canonical normalizer. Don't do ad-hoc conversion in component
   code. If the normalizer doesn't handle your case, fix the normalizer —
   don't fork it.
2. Display the canonical value in the UI. Don't translate to another scale
   at render time (e.g., don't convert to numeric for a progress bar).
3. If you're building a new UI surface, show both severity AND priority when
   relevant (see "Severity vs priority" above).
