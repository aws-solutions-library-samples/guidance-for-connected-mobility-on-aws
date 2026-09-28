/**
 * Resolve a campaign's signal references against the `GET /signals` catalog.
 *
 * A campaign's `signalsToCollect` is a list of **numeric signal IDs** (`{"N":"1"}` in
 * DynamoDB, `1.0` over the wire), not names — so the screen cannot show "what signals am
 * I collecting" without this join. Measured on staging 2026-09-20: **293 of 293** of
 * `cms-fleet-gps-10s`'s IDs resolve against the 302-record catalog, 0 unresolved.
 *
 * `signal_group` is the presentation axis. 24 groups across that campaign's collected
 * set — `core_telemetry` 39, `vehicle_control` 31, `maintenance` 25, `doors` 24,
 * `safety` 23, down to `body` 1. Rendering 293 flat rows is not useful.
 *
 * Pure: no fetch, no React.
 *
 * Spec: `.kiro/specs/2026-09-20-cs-campaigns-screen-restructure/spec.md`
 */

import type { SignalItem } from "../../../api/dataModelClient";

/**
 * One `signalsToFetch` entry. UDS/DTC campaigns only.
 *
 * Not on `DataProcessingCampaignItem` because only the UDS templates carry it — 9
 * entries on `uds-dtc-polling`, absent on every telemetry campaign.
 */
export interface SignalToFetch {
  readonly signalId: number | string;
  readonly functionName: string;
  readonly executionFrequencyMs?: number | string;
  readonly maxExecutionCount?: number | string;
  /** `[ecuIndex, ?, ?]` — see `resolveEcuPolls` for why index 0 is the ECU. */
  readonly params?: readonly (number | string)[];
}

export interface SignalGroupBucket {
  readonly group: string;
  readonly signals: readonly SignalItem[];
}

export interface ResolvedSignals {
  readonly groups: readonly SignalGroupBucket[];
  /**
   * Count of **distinct** resolved signals across all groups.
   *
   * Distinct, not entry count: `signalsToCollect` repeats ids. `cms-fleet-gps-10s` holds
   * **293 entries but 286 distinct** — the 7 ids that are duplicated in the catalog
   * (130, 156, 158, 162, 166, 170, 265) also appear twice each in the campaign's own
   * list. Counting entries made "293 of 293 resolve" true only because a duplicate
   * resolved twice, and would have rendered 7 signals twice in the panel.
   */
  readonly resolvedCount: number;
  /**
   * IDs present on the campaign but absent from the catalog.
   *
   * Surfaced rather than dropped even though it is currently empty. A silent drop would
   * make a future catalog gap invisible — the screen would show 280 of 293 signals and
   * look complete.
   *
   * Note the catalog the API exposes is itself a subset: the table holds 319 rows and
   * `GET /signals` returns the **302** that carry a `status`, because the handler queries
   * a sparse `status-index`. All 302 returned records carry a `signal_id`. The 17 without
   * `status` are unreachable through this API and so can never resolve here.
   */
  readonly unresolvedIds: readonly number[];
  /** Ids that appeared more than once in `signalsToCollect`, counted once. */
  readonly duplicateIds: readonly number[];
  /**
   * Number of ENTRIES that were duplicates of an earlier entry.
   *
   * Distinct from `duplicateIds.length`, and the distinction is load-bearing for any
   * caller that reports these numbers to a person. `duplicateIds` is deduped — an id
   * appearing three times contributes ONE entry to `duplicateIds` and TWO to this count.
   *
   * Fix Group 4 F4.6 added this because the panel's headline used `duplicateIds.length`
   * as its "repeated" figure and so rendered `1 distinct signals (3 entries, 1 repeated,
   * 0 unknown)` for `[1, 1, 1]` — 1 + 1 + 0 = 2, not 3. The parenthetical exists to
   * EXPLAIN a discrepancy, so one that does not reconcile is worse than no parenthetical.
   */
  readonly duplicateEntryCount: number;
  /**
   * Number of entries that were not a usable signal id at all.
   *
   * `toSignalId` returns `null` for a non-finite value, a boolean, or `null`/`undefined`,
   * and those entries are skipped — they appear in NO other bucket. Counting them is what
   * makes the four buckets exhaustive:
   *
   *   `signalsToCollect.length === resolvedCount + duplicateEntryCount +
   *                                unresolvedIds.length + malformedEntryCount`
   *
   * That identity holds by construction — the loop below assigns every entry to exactly
   * one bucket — and `resolveCollectedSignals` has a property test asserting it over a
   * matrix of inputs rather than a handful of fixtures.
   */
  readonly malformedEntryCount: number;
}

/**
 * Coerce a wire value to a signal id.
 *
 * `signal_id` arrives as a JSON number (`1.0`) while `signalsToCollect` entries arrive
 * as numbers too — but both were typed loosely (`string` and `unknown[]` respectively),
 * so neither side can be trusted to be one type. `Number()` on both sides of the join is
 * what makes it work; a `===` between a declared-string and a real-number would match
 * nothing, silently, and the screen would render zero signals with no error.
 *
 * Returns `null` for anything that is not a finite number, so a malformed entry becomes
 * an explicit unresolved id rather than `NaN` propagating into a Map lookup.
 */
function toSignalId(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "boolean") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/**
 * Index the catalog by numeric `signal_id`, preferring the **canonical** record.
 *
 * `signal_id` is NOT unique in this catalog. Measured on staging 2026-09-20: 7 ids
 * (130, 156, 158, 162, 166, 170, 265) each appear **twice** — once as the canonical
 * signal and once as a `*_JsonAlias` variant carrying a camelCase `json_field`:
 *
 *   id 130: ChargeDoorOpen (json_field charge_door_open)
 *           ChargeDoorOpen_JsonAlias (json_field chargeDoorOpen, alias_of_signal_id 130)
 *
 * A naive last-wins `Map.set` let the **alias win all 7**, because the aliases happen to
 * come later in the API's order. All 7 are referenced by `cms-fleet-gps-10s`, so the
 * signals panel would have displayed `ChargeDoorOpen_JsonAlias` as the collected
 * signal — a synthetic name presented as the real one. Found in review of Group 1;
 * scan order is not a contract, so "it happened to work" was never available either.
 *
 * Disambiguated on `alias_of_signal_id`, which is present on exactly those 7 alias
 * records and absent on every canonical one — not on the `_JsonAlias` name suffix, which
 * is a naming convention rather than a field and could change without notice.
 */
export function indexSignals(
  catalog: readonly SignalItem[],
): ReadonlyMap<number, SignalItem> {
  const out = new Map<number, SignalItem>();
  for (const s of catalog) {
    const id = toSignalId(s.signal_id);
    if (id === null) continue;
    const existing = out.get(id);
    if (existing !== undefined) {
      // Keep whichever is canonical. If both or neither are aliases, keep the first
      // seen — deterministic, and no basis exists to prefer the later one.
      const existingIsAlias = existing.alias_of_signal_id !== undefined;
      const candidateIsAlias = s.alias_of_signal_id !== undefined;
      if (!(existingIsAlias && !candidateIsAlias)) continue;
    }
    out.set(id, s);
  }
  return out;
}

/**
 * Resolve `signalsToCollect` and bucket by `signal_group`.
 *
 * Groups are ordered by descending size, then by name for ties, so the render order is
 * both useful (biggest first) and stable (no dependence on catalog iteration order).
 */
export function resolveCollectedSignals(
  signalsToCollect: readonly unknown[] | undefined,
  catalog: readonly SignalItem[],
): ResolvedSignals {
  const index = indexSignals(catalog);
  const buckets = new Map<string, SignalItem[]>();
  const unresolved: number[] = [];
  const duplicates: number[] = [];
  // De-duplicate the campaign's own list. It really does repeat ids — 293 entries, 286
  // distinct on `cms-fleet-gps-10s` — so without this the panel renders 7 signals twice
  // and the count overstates coverage.
  const seen = new Set<number>();
  let resolvedCount = 0;
  // Every entry lands in exactly one of four buckets: resolved, duplicate, unresolved,
  // malformed. Tracked per ENTRY (not per distinct id) so a caller can report figures
  // that reconcile against `signalsToCollect.length`. See `ResolvedSignals`.
  let duplicateEntryCount = 0;
  let malformedEntryCount = 0;

  for (const raw of signalsToCollect ?? []) {
    const id = toSignalId(raw);
    if (id === null) {
      // Not an id at all. Counted so it is not silently absent from every bucket.
      malformedEntryCount += 1;
      continue;
    }
    if (seen.has(id)) {
      if (!duplicates.includes(id)) duplicates.push(id);
      duplicateEntryCount += 1;
      continue;
    }
    seen.add(id);
    const sig = index.get(id);
    if (!sig) {
      unresolved.push(id);
      continue;
    }
    // `signal_group` is documented as always present, but an absent value must not
    // collapse into the string "undefined" as a group heading.
    const group = sig.signal_group || "ungrouped";
    const arr = buckets.get(group);
    if (arr) arr.push(sig);
    else buckets.set(group, [sig]);
    resolvedCount += 1;
  }

  const groups = [...buckets.entries()]
    .map(([group, signals]) => ({ group, signals }))
    .sort(
      (a, b) =>
        b.signals.length - a.signals.length || a.group.localeCompare(b.group),
    );

  return {
    groups,
    resolvedCount,
    unresolvedIds: unresolved,
    duplicateIds: duplicates,
    duplicateEntryCount,
    malformedEntryCount,
  };
}

export interface EcuPoll {
  /** 1-based ECU index, from `params[0]`. */
  readonly ecuIndex: number;
  /** Resolved catalog name, e.g. `ECU1_DTC_INFO`. Null when the id does not resolve. */
  readonly signalName: string | null;
  readonly signalId: number;
  readonly functionName: string;
  readonly executionFrequencyMs: number | null;
}

/**
 * Resolve `signalsToFetch` into per-ECU polls. **UDS/DTC campaigns only.**
 *
 * This is the ONLY per-campaign ECU information that exists. Verified 2026-09-20: no
 * `ecu` field on any of the 302 signal records (the repo's own `SignalItem` docstring
 * records the same finding independently), and no ECU attribute among the 21 campaign
 * attributes. So a telemetry campaign's signals cannot be attributed to ECUs at all, and
 * the view must not pretend otherwise — see the spec's Coverage table.
 *
 * ECU identity comes from `params[0]`, corroborated by the resolved signal name:
 * `uds-dtc-polling`'s 9 entries have `params[0]` 1-9 and resolve to
 * `ECU1_DTC_INFO`…`ECU9_DTC_INFO` respectively. The name is carried alongside rather
 * than parsed for the index, so a naming change degrades to "index without a name"
 * instead of silently mis-numbering an ECU.
 *
 * Entries whose `params[0]` is not a finite number are skipped: without an ECU index
 * there is nothing to attribute, and inventing one would fabricate the very column this
 * function exists to keep honest.
 */
export function resolveEcuPolls(
  signalsToFetch: readonly SignalToFetch[] | undefined,
  catalog: readonly SignalItem[],
): readonly EcuPoll[] {
  const index = indexSignals(catalog);
  const out: EcuPoll[] = [];

  for (const entry of signalsToFetch ?? []) {
    const ecuIndex = toSignalId(entry.params?.[0]);
    if (ecuIndex === null) continue;
    const signalId = toSignalId(entry.signalId);
    if (signalId === null) continue;
    const freq = toSignalId(entry.executionFrequencyMs);
    out.push({
      ecuIndex,
      signalId,
      signalName: index.get(signalId)?.signal_name ?? null,
      functionName: entry.functionName,
      executionFrequencyMs: freq,
    });
  }

  return out.sort((a, b) => a.ecuIndex - b.ecuIndex);
}
