/**
 * T1.2 — signal-catalog resolution.
 *
 * Fixtures mirror the live payload's TYPES, which is the point: `signal_id` arrives as a
 * JSON number (`1.0`) while the interface declared `string` until 2026-09-20. A fixture
 * that used strings on both sides would pass while the real join matched nothing.
 *
 * Each mutation was APPLIED and the named test OBSERVED failing.
 */

import { describe, expect, it } from "vitest";
import {
  indexSignals,
  resolveCollectedSignals,
  resolveEcuPolls,
  type SignalToFetch,
} from "../signalResolution";
import type { SignalItem } from "../../../../api/dataModelClient";

/** Live-shaped: signal_id is a NUMBER, as the API actually sends it. */
function sig(
  id: number,
  group: string,
  name: string,
  over: Partial<SignalItem> = {},
): SignalItem {
  return {
    signal_id: id,
    signal_group: group,
    signal_name: name,
    data_type: "float",
    status: "active",
    vss_path: `Vehicle.${name}`,
    ...over,
  };
}

const CATALOG: SignalItem[] = [
  sig(1, "safety", "ABS_ACTIVE", { data_type: "boolean" }),
  sig(2, "safety", "ESC_ACTIVE"),
  sig(3, "safety", "AEB_ACTIVE"),
  sig(10, "core_telemetry", "VEHICLE_SPEED", { unit: "km/h", cycle_ms: 100 }),
  sig(11, "core_telemetry", "ODOMETER", { unit: "km" }),
  sig(20, "tpms", "TIRE_PRESSURE_FL", { unit: "kPa" }),
  sig(901, "diagnostics", "ECU1_DTC_INFO"),
  sig(902, "diagnostics", "ECU2_DTC_INFO"),
];

/**
 * The real duplicate-id shape, in the real API order: canonical first, alias second.
 *
 * Verified live 2026-09-20 — 7 ids are duplicated this way (130, 156, 158, 162, 166,
 * 170, 265) and the alias always sorts later, which is what let a last-wins Map pick it.
 */
const CATALOG_WITH_ALIASES: SignalItem[] = [
  ...CATALOG,
  sig(130, "doors", "ChargeDoorOpen", { json_field: "charge_door_open" }),
  sig(130, "doors", "ChargeDoorOpen_JsonAlias", {
    json_field: "chargeDoorOpen",
    alias_of_signal_id: "130",
  }),
];

describe("indexSignals", () => {
  it("indexes by numeric signal_id even though the wire type is a float", () => {
    // NOTE: this test does NOT guard the Number() coercion. Its fixture already holds a
    // number, so removing the coercion is a no-op here — verified by running that
    // mutation against this test and watching it PASS. The coercion is guarded by
    // "tolerates a string signal_id" below. Recorded because attaching a mutation to the
    // wrong assertion is how a coercion ends up believed-guarded and unguarded.
    const idx = indexSignals(CATALOG);
    expect(idx.get(1)?.signal_name).toBe("ABS_ACTIVE");
    expect(idx.get(901)?.signal_name).toBe("ECU1_DTC_INFO");
  });

  it("prefers the canonical record over a *_JsonAlias sharing the same signal_id", () => {
    // MUTATION: revert to a bare last-wins `out.set(id, s)` -> this test FAILS.
    // APPLIED 2026-09-20, OBSERVED failing.
    //
    // Not an edge case: 7 ids are duplicated this way on staging and ALL 7 are
    // referenced by `cms-fleet-gps-10s`, so last-wins would have shown the synthetic
    // alias name as the collected signal for every one of them. Found in review of
    // Group 1 — my original code had exactly that bug.
    const idx = indexSignals(CATALOG_WITH_ALIASES);
    expect(idx.get(130)?.signal_name).toBe("ChargeDoorOpen");
    expect(idx.get(130)?.alias_of_signal_id).toBeUndefined();
  });

  it("prefers the canonical record regardless of which order the pair arrives in", () => {
    // The live order is canonical-then-alias, so a fix that merely kept the FIRST seen
    // would pass the test above while remaining order-dependent. Scan order is not a
    // contract.
    const reversed = [
      sig(130, "doors", "ChargeDoorOpen_JsonAlias", {
        json_field: "chargeDoorOpen",
        alias_of_signal_id: "130",
      }),
      sig(130, "doors", "ChargeDoorOpen", { json_field: "charge_door_open" }),
    ];
    expect(indexSignals(reversed).get(130)?.signal_name).toBe("ChargeDoorOpen");
  });

  it("skips catalog entries with no signal_id", () => {
    const idx = indexSignals([
      ...CATALOG,
      { ...sig(0, "misc", "NO_ID"), signal_id: undefined as unknown as number },
    ]);
    expect(idx.size).toBe(CATALOG.length);
  });

  it("tolerates a string signal_id, since the declared type permits one", () => {
    // MUTATION: drop the Number() coercion in `indexSignals` (index on `s.signal_id`
    // directly) -> this test FAILS. APPLIED 2026-09-20, OBSERVED failing.
    //
    // This is the assertion that actually guards the catalog-side coercion. The union
    // type exists so an encoder change cannot silently break the join; this test is what
    // makes that non-theoretical.
    const idx = indexSignals([{ ...sig(5, "misc", "AS_STRING"), signal_id: "5" }]);
    expect(idx.get(5)?.signal_name).toBe("AS_STRING");
  });
});

describe("resolveCollectedSignals", () => {
  it("resolves numeric ids and buckets by signal_group", () => {
    const r = resolveCollectedSignals([1, 2, 3, 10, 11, 20], CATALOG);
    expect(r.resolvedCount).toBe(6);
    expect(r.unresolvedIds).toHaveLength(0);
    expect(r.groups.map((g) => g.group)).toEqual([
      "safety", // 3
      "core_telemetry", // 2
      "tpms", // 1
    ]);
    expect(r.groups[0].signals.map((s) => s.signal_name)).toEqual([
      "ABS_ACTIVE",
      "ESC_ACTIVE",
      "AEB_ACTIVE",
    ]);
  });

  it("orders groups by descending size, then name for ties", () => {
    // MUTATION: drop the sort -> FAILS. APPLIED 2026-09-20, OBSERVED failing.
    //
    // Descending size is what makes 293 signals readable; the name tiebreak is what makes
    // the order stable rather than dependent on catalog iteration order.
    const r = resolveCollectedSignals([1, 10, 20], CATALOG);
    expect(r.groups.map((g) => g.group)).toEqual([
      "core_telemetry",
      "safety",
      "tpms",
    ]);
  });

  it("counts DISTINCT signals and reports repeated ids, rather than double-counting", () => {
    // MUTATION: remove the `seen` de-duplication -> this test FAILS on resolvedCount (3,
    // not 2) and on the group length. APPLIED 2026-09-20, OBSERVED failing.
    //
    // Not an edge case: `cms-fleet-gps-10s` holds 293 entries but 286 DISTINCT — the same
    // 7 ids that are duplicated in the catalog also repeat in the campaign's own list. So
    // "293 of 293 resolve" was true only because a duplicate resolved twice, and the
    // panel would have rendered those 7 signals twice. Found in review of Group 1.
    const r = resolveCollectedSignals([1, 10, 1], CATALOG);
    expect(r.resolvedCount).toBe(2);
    expect(r.duplicateIds).toEqual([1]);
    const safety = r.groups.find((g) => g.group === "safety")!;
    expect(safety.signals).toHaveLength(1);
  });

  it("resolves the real 293-entry/286-distinct shape without double-counting", () => {
    // Models the flagship campaign's actual shape: an id that is duplicated in the
    // catalog AND repeated in signalsToCollect.
    const r = resolveCollectedSignals([1, 130, 130], CATALOG_WITH_ALIASES);
    expect(r.resolvedCount).toBe(2);
    expect(r.duplicateIds).toEqual([130]);
    expect(
      r.groups.find((g) => g.group === "doors")!.signals[0].signal_name,
    ).toBe("ChargeDoorOpen");
  });

  it("reports unresolved ids separately instead of dropping them", () => {
    // MUTATION: `continue` without pushing to unresolved -> FAILS.
    // APPLIED 2026-09-20, OBSERVED failing.
    //
    // Currently empty in production (293/293 resolve). It is surfaced anyway because a
    // silent drop would render 280 of 293 signals and look complete.
    const r = resolveCollectedSignals([1, 9999, 8888], CATALOG);
    expect(r.resolvedCount).toBe(1);
    expect(r.unresolvedIds).toEqual([9999, 8888]);
  });

  it("resolves ids supplied as strings (signalsToCollect is typed unknown[])", () => {
    // MUTATION: drop the Number() coercion on the CAMPAIGN side (`toSignalId(raw)` in
    // `resolveCollectedSignals`) -> this test FAILS. APPLIED 2026-09-20, OBSERVED failing.
    //
    // A SECOND, independent coercion from the catalog-side one above: this join has two
    // sides and each needs its own guard. The catalog-side mutation does NOT fail this
    // test, and vice versa — verified both ways.
    const r = resolveCollectedSignals(["1", "10"], CATALOG);
    expect(r.resolvedCount).toBe(2);
    expect(r.unresolvedIds).toHaveLength(0);
  });

  it("does not turn a non-numeric entry into a NaN lookup", () => {
    // NaN as a Map key would neither resolve nor report — the entry would vanish.
    const r = resolveCollectedSignals([1, "not-a-number", null, true], CATALOG);
    expect(r.resolvedCount).toBe(1);
    expect(r.unresolvedIds).toHaveLength(0);
  });

  it("handles an absent signalsToCollect", () => {
    const r = resolveCollectedSignals(undefined, CATALOG);
    expect(r.resolvedCount).toBe(0);
    expect(r.groups).toHaveLength(0);
  });

  it("does not render an absent group as the string 'undefined'", () => {
    const r = resolveCollectedSignals(
      [7],
      [{ ...sig(7, "", "NO_GROUP"), signal_group: "" }],
    );
    expect(r.groups[0].group).toBe("ungrouped");
  });
});

describe("resolveEcuPolls", () => {
  /** Live shape from `uds-dtc-polling`: params[0] is the 1-based ECU index. */
  const FETCH: SignalToFetch[] = [
    { signalId: 902, functionName: "DTC_QUERY", executionFrequencyMs: 30000, maxExecutionCount: 0, params: [2, 2, -1] },
    { signalId: 901, functionName: "DTC_QUERY", executionFrequencyMs: 30000, maxExecutionCount: 0, params: [1, 2, -1] },
  ];

  it("extracts the ECU index from params[0] and pairs it with the resolved name", () => {
    // MUTATION: read `params[1]` instead of `params[0]` -> FAILS.
    // APPLIED 2026-09-20, OBSERVED failing.
    //
    // params[0] is corroborated by the name: index 1 <-> ECU1_DTC_INFO. The name is
    // carried, not parsed for the index, so a naming change degrades to "index without a
    // name" rather than silently mis-numbering an ECU.
    const polls = resolveEcuPolls(FETCH, CATALOG);
    expect(polls).toHaveLength(2);
    expect(polls[0]).toMatchObject({
      ecuIndex: 1,
      signalId: 901,
      signalName: "ECU1_DTC_INFO",
      functionName: "DTC_QUERY",
      executionFrequencyMs: 30000,
    });
    expect(polls[1].ecuIndex).toBe(2);
  });

  it("sorts by ECU index regardless of input order", () => {
    // Input above is deliberately ECU2 then ECU1.
    const polls = resolveEcuPolls(FETCH, CATALOG);
    expect(polls.map((p) => p.ecuIndex)).toEqual([1, 2]);
  });

  it("skips an entry with no usable ECU index rather than inventing one", () => {
    // MUTATION: default a missing params[0] to 0 and keep the entry -> FAILS.
    // APPLIED 2026-09-20, OBSERVED failing.
    //
    // Without an index there is nothing to attribute, and inventing one fabricates the
    // very column this function exists to keep honest.
    const polls = resolveEcuPolls(
      [
        { signalId: 901, functionName: "DTC_QUERY", params: [] },
        { signalId: 902, functionName: "DTC_QUERY" },
        { signalId: 903, functionName: "DTC_QUERY", params: [3] },
      ],
      CATALOG,
    );
    expect(polls).toHaveLength(1);
    expect(polls[0].ecuIndex).toBe(3);
  });

  it("carries a null name when the polled id is not in the catalog", () => {
    const polls = resolveEcuPolls(
      [{ signalId: 9999, functionName: "DTC_QUERY", params: [4] }],
      CATALOG,
    );
    expect(polls[0].ecuIndex).toBe(4);
    expect(polls[0].signalName).toBeNull();
  });

  it("returns empty for a telemetry campaign, which has no signalsToFetch", () => {
    // The common case: only UDS campaigns carry signalsToFetch. A telemetry campaign
    // must yield NO ECU polls, so the view falls through to the manifest-roster panel
    // with its non-attribution caption.
    expect(resolveEcuPolls(undefined, CATALOG)).toHaveLength(0);
    expect(resolveEcuPolls([], CATALOG)).toHaveLength(0);
  });
});


// ── Fix Group 4 F4.6 — the bucket identity, property-tested ───────────────────
//
// Every entry in `signalsToCollect` must land in exactly one of four buckets:
//
//   signalsToCollect.length === resolvedCount + duplicateEntryCount +
//                               unresolvedIds.length + malformedEntryCount
//
// Property-tested over a matrix rather than asserted on a fixture or two, because the
// defect this closes was found by trying a combination nobody had written a fixture for.
//
// History, so the next reader does not re-derive it: the panel reported
// `duplicateIds.length` as its "repeated" figure, which is deduped — an id appearing three
// times is ONE duplicated id but TWO duplicate entries. So `[1, 1, 1]` rendered
// "1 distinct signals (3 entries, 1 repeated, 0 unknown)", and 1 + 1 + 0 = 2, not 3. The
// first fix for F4.6 added a fixture with duplicates AND unresolved ids together and
// claimed the invariant then held "for every combination" — it did not, because that
// fixture used a 2x duplicate, where `duplicateIds.length` and the duplicate-ENTRY count
// happen to coincide. A single extra fixture would have missed it again; the matrix does not.
//
// Malformed entries were the second half of the same hole: `toSignalId` returns null for a
// non-numeric value and the entry was skipped, appearing in NO bucket at all.
describe("resolveCollectedSignals — bucket identity (F4.6)", () => {
  const CASES: readonly { readonly label: string; readonly input: readonly unknown[] }[] = [
    { label: "empty", input: [] },
    { label: "all resolve, no repeats", input: [1, 10, 20] },
    { label: "one id twice", input: [1, 1] },
    { label: "one id THREE times (the case that broke the old figure)", input: [1, 1, 1] },
    { label: "one id five times", input: [1, 1, 1, 1, 1] },
    { label: "two ids each three times", input: [1, 1, 1, 10, 10, 10] },
    { label: "unresolved only", input: [9999] },
    { label: "same unresolved id twice", input: [9999, 9999] },
    { label: "duplicates and unresolved together", input: [1, 1, 9999, 10] },
    { label: "malformed only", input: ["not-a-number"] },
    { label: "malformed, null, boolean", input: ["x", null, true, undefined] },
    { label: "all four buckets at once", input: [1, 1, 1, 9999, "x", 10, true] },
    { label: "numeric strings coerce and still balance", input: ["1", "1", "10"] },
  ];

  for (const { label, input } of CASES) {
    it(`entries === resolved + duplicateEntries + unresolved + malformed — ${label}`, () => {
      const r = resolveCollectedSignals(input, CATALOG);
      const sum =
        r.resolvedCount +
        r.duplicateEntryCount +
        r.unresolvedIds.length +
        r.malformedEntryCount;
      expect(
        sum,
        `Buckets do not account for every entry in [${input.map(String).join(", ")}]: ` +
          `resolved=${String(r.resolvedCount)} + duplicateEntries=${String(r.duplicateEntryCount)} + ` +
          `unresolved=${String(r.unresolvedIds.length)} + malformed=${String(r.malformedEntryCount)} = ${String(sum)}, ` +
          `but signalsToCollect.length = ${String(input.length)}. Any entry that falls into no ` +
          "bucket makes the panel's headline arithmetic fail to reconcile, which is worse than " +
          "showing no breakdown at all — the parenthetical exists to explain a discrepancy.",
      ).toBe(input.length);
    });
  }

  it("duplicateEntryCount counts ENTRIES while duplicateIds counts distinct ids", () => {
    // The distinction the old figure collapsed. Both numbers are legitimate; they answer
    // different questions, and the headline needs the per-entry one.
    const r = resolveCollectedSignals([1, 1, 1], CATALOG);
    expect(r.duplicateIds, "one distinct id is duplicated").toEqual([1]);
    expect(r.duplicateEntryCount, "but TWO entries are duplicates of an earlier one").toBe(2);
  });

  it("malformed entries are counted, not silently dropped", () => {
    const r = resolveCollectedSignals([1, "x", null, true], CATALOG);
    expect(r.malformedEntryCount).toBe(3);
    expect(r.resolvedCount).toBe(1);
  });
});
