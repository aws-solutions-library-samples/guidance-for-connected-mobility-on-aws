// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * CampaignSignalsPanel.test.tsx — tests for the resolved-signals panel.
 *
 * Spec: `.kiro/specs/2026-09-20-cs-campaigns-screen-restructure/tasks.md` T3.2
 * Contract: `.kiro/specs/2026-09-20-cs-campaigns-screen-restructure/group3-contract.md` §2, §7
 *
 * ## Critical properties under test (each mutation-verified)
 *
 * **P1 — Two-number reporting when distinct ≠ entries.**
 *   The panel MUST render both `resolvedCount` and `signalsToCollect.length` and
 *   state their relationship when they differ. A panel showing 286 where the list
 *   column said 293 would appear to contradict the row the operator just clicked.
 *   Mutation: replace headline with only the distinct count → P1 tests fail.
 *
 * **P2 — Groups collapsed by default.**
 *   `ExpandableSection` receives no `defaultExpanded` prop (falsy). Rendering 293
 *   rows expanded by default degrades every detail view open.
 *   Mutation: add `defaultExpanded` to ExpandableSection → P2 test fails.
 *
 * **P3 — No liveness vocabulary.**
 *   None of the forbidden words (Running, Live, Streaming, etc.) appear in the
 *   rendered output. Guarded by `campaignStatusVocabulary.test.ts` but also
 *   tested here so the panel itself documents the constraint.
 *
 * ## What this file does NOT test
 *
 * - The resolution logic itself (`resolveCollectedSignals`): that is covered by
 *   `signalResolution.test.ts`. Duplicating it here would couple two tests to an
 *   implementation detail.
 * - API fetching: the panel has none (props only).
 *
 * ## Fixture rationale
 *
 * Fixtures use numeric `signal_id` values (matching the live wire format). The
 * same shape was verified in `signalResolution.test.ts` — using strings here would
 * test a different code path from the real one.
 */

import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";

import type { SignalItem } from "../../../../api/dataModelClient";
import CampaignSignalsPanel from "../CampaignSignalsPanel";

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Construct a minimal SignalItem fixture. Uses numeric signal_id per live wire format. */
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

// ── Catalogs ──────────────────────────────────────────────────────────────────

/**
 * A 3-signal catalog across 2 groups. No duplicates.
 * Entry count === distinct count when `signalsToCollect` has no repeats.
 */
const CATALOG_3: readonly SignalItem[] = [
  sig(1, "safety", "ABS_ACTIVE", { data_type: "boolean" }),
  sig(2, "safety", "ESC_ACTIVE"),
  sig(10, "core_telemetry", "VEHICLE_SPEED", { unit: "km/h", cycle_ms: 100 }),
];

/**
 * A catalog with one duplicate-id pair (id 130: canonical + alias).
 * Mirrors the live staging structure — 7 ids are duplicated this way.
 */
const CATALOG_WITH_ALIAS: readonly SignalItem[] = [
  sig(1, "safety", "ABS_ACTIVE"),
  sig(130, "doors", "ChargeDoorOpen", { json_field: "charge_door_open" }),
  // alias — alias_of_signal_id present → resolveCollectedSignals prefers canonical above
  {
    signal_id: 130,
    signal_group: "doors",
    signal_name: "ChargeDoorOpen_JsonAlias",
    data_type: "boolean",
    status: "active",
    vss_path: "Vehicle.ChargeDoorOpen",
    json_field: "chargeDoorOpen",
    alias_of_signal_id: "130",
  },
];

// ── Setup / teardown ──────────────────────────────────────────────────────────

afterEach(() => {
  cleanup();
});

// ── 1. Empty-list state ───────────────────────────────────────────────────────
//
// F4.7: undefined (no definition row) and [] (definition declares nothing) must
// render DIFFERENT messages so an operator can tell whether the campaign has been
// defined and declares no signals, or whether the definition itself is missing.
//
// MUTATION-VERIFIED (F4.7):
//   In CampaignSignalsPanel.tsx, collapse the two branches back into one:
//     if (signalsToCollect === undefined || entryCount === 0) { render "No signals..." }
//   The test "renders 'no definition row' message for undefined" still finds the testid
//   but the textContent is the wrong string → test FAILS.
//   File restored byte-identical after observation.

describe("empty-list state", () => {
  it("renders 'no definition row' message when signalsToCollect is undefined", () => {
    render(
      <CampaignSignalsPanel
        signalsToCollect={undefined}
        signalCatalog={CATALOG_3}
      />,
    );
    const el = screen.getByTestId("campaign-signals-panel-empty");
    expect(el).toBeTruthy();
    // Must distinguish from "declares nothing" — the definition is unavailable.
    expect(el.textContent).toContain("unavailable");
    // Must NOT claim the campaign declares nothing.
    expect(el.textContent).not.toContain("No signals declared");
    expect(screen.queryByTestId("campaign-signals-panel")).toBeNull();
  });

  it("renders 'declares nothing' message when signalsToCollect is empty []", () => {
    render(
      <CampaignSignalsPanel signalsToCollect={[]} signalCatalog={CATALOG_3} />,
    );
    const el = screen.getByTestId("campaign-signals-panel-empty");
    expect(el).toBeTruthy();
    // Must state that the campaign explicitly declares no signals.
    expect(el.textContent).toContain("No signals declared");
    // Must NOT say the definition is unavailable.
    expect(el.textContent).not.toContain("unavailable");
  });

  it("the two empty states render DIFFERENT messages (undefined vs [])", () => {
    // Render undefined state and capture its text.
    const { unmount } = render(
      <CampaignSignalsPanel
        signalsToCollect={undefined}
        signalCatalog={CATALOG_3}
      />,
    );
    const undefinedText = screen.getByTestId("campaign-signals-panel-empty").textContent ?? "";
    unmount();

    // Render [] state and capture its text.
    render(
      <CampaignSignalsPanel signalsToCollect={[]} signalCatalog={CATALOG_3} />,
    );
    const emptyText = screen.getByTestId("campaign-signals-panel-empty").textContent ?? "";

    // They must be distinct.
    expect(undefinedText).not.toBe(emptyText);
  });
});

// ── 2. Headline — agreed counts ───────────────────────────────────────────────

describe("headline — distinct equals entry count", () => {
  it("renders '<N> signals' without a parenthetical when all IDs are unique", () => {
    render(
      <CampaignSignalsPanel
        signalsToCollect={[1, 2, 10]}
        signalCatalog={CATALOG_3}
      />,
    );
    const headline = screen.getByTestId("campaign-signals-panel-headline");
    expect(headline.textContent).toBe("3 signals");
    // Absence of parenthetical is the test: no "entries" text when counts agree.
    expect(headline.textContent).not.toContain("entries");
    expect(headline.textContent).not.toContain("repeated");
  });
});

// ── 3. Headline — P1: two-number reporting when counts differ ─────────────────
//
// F4.6: the headline must account for all three buckets so the arithmetic
// reconciles for every combination:
//   entries = distinct + repeated + unresolved
//
// The previous format "N distinct (M entries, K repeated)" was inconsistent
// when unresolved > 0: entries - repeated did NOT equal distinct because
// unresolved IDs are also subtracted from resolvedCount. E.g. [1, 999]:
//   entries=2, repeated=0, distinct=1 → "1 distinct (2 entries, 0 repeated)"
//   but 2 - 0 = 2 ≠ 1. The reader cannot verify the arithmetic.
//
// New format always shows all three buckets when any non-zero:
//   "N distinct signals (M entries, K repeated, U unknown)"
//
// MUTATION-VERIFIED (P1 / F4.6):
//   In CampaignSignalsPanel.tsx, replace the needsParenthetical branch headline
//   with `${String(distinctCount)} signals` (dropping the parenthetical).
//   The tests below fail because `.toContain("entries")` no longer matches.
//   File restored byte-identical after observation.

describe("headline — P1: two-number reporting when distinct < entries", () => {
  it("reports distinct, entries, repeated, unknown for a dups-only scenario", () => {
    // 3 entries with one duplicate id (130 appears twice):
    //   entries = 3, distinct = 2, repeated = 1, unresolved = 0
    render(
      <CampaignSignalsPanel
        signalsToCollect={[1, 130, 130]}
        signalCatalog={CATALOG_WITH_ALIAS}
      />,
    );
    const headline = screen.getByTestId("campaign-signals-panel-headline");
    expect(headline.textContent).toContain("2 distinct signals");
    expect(headline.textContent).toContain("3 entries");
    expect(headline.textContent).toContain("1 repeated");
    expect(headline.textContent).toContain("0 unknown");
  });

  it("uses the exact three-bucket phrasing: '<D> distinct signals (<E> entries, <R> repeated, <U> unknown)'", () => {
    render(
      <CampaignSignalsPanel
        signalsToCollect={[1, 130, 130]}
        signalCatalog={CATALOG_WITH_ALIAS}
      />,
    );
    const headline = screen.getByTestId("campaign-signals-panel-headline");
    expect(headline.textContent).toBe(
      "2 distinct signals (3 entries, 1 repeated, 0 unknown)",
    );
  });

  it("reconciles arithmetic when unresolved IDs are present (no dups)", () => {
    // [1, 999]: entries=2, distinct=1 (999 not in catalog), repeated=0, unknown=1
    // 1 + 0 + 1 = 2 = entries ✓
    render(
      <CampaignSignalsPanel
        signalsToCollect={[1, 999]}
        signalCatalog={CATALOG_3}
      />,
    );
    const headline = screen.getByTestId("campaign-signals-panel-headline");
    expect(headline.textContent).toBe(
      "1 distinct signals (2 entries, 0 repeated, 1 unknown)",
    );
  });

  it("reconciles arithmetic when BOTH dups and unresolved are present — F4.6 required fixture", () => {
    // Combined fixture: duplicates AND unresolved IDs together.
    // signalsToCollect=[1, 130, 130, 999]:
    //   entries=4, distinct=2 (1 + canonical-130), repeated=1 (dup-130), unknown=1 (999)
    //   2 + 1 + 1 = 4 = entries ✓
    render(
      <CampaignSignalsPanel
        signalsToCollect={[1, 130, 130, 999]}
        signalCatalog={CATALOG_WITH_ALIAS}
      />,
    );
    const headline = screen.getByTestId("campaign-signals-panel-headline");
    expect(headline.textContent).toBe(
      "2 distinct signals (4 entries, 1 repeated, 1 unknown)",
    );
  });

  it("reconciles arithmetic when an id is repeated THREE times — the 2x fixture could not catch this", () => {
    // The case the first F4.6 fix missed. `duplicateIds.length` and the duplicate-ENTRY
    // count coincide at exactly 2 occurrences, so a 2x fixture passes either way; at 3
    // occurrences they diverge (one duplicated id, two duplicate entries) and the old
    // figure rendered "1 distinct signals (3 entries, 1 repeated, 0 unknown)" — which does
    // not add up. Asserted on the exact string so the numbers, not just the shape, are pinned.
    render(<CampaignSignalsPanel signalsToCollect={[1, 1, 1]} signalCatalog={CATALOG_3} />);
    expect(
      screen.getByTestId("campaign-signals-panel-headline").textContent,
    ).toBe("1 distinct signals (3 entries, 2 repeated, 0 unknown)");
  });

  it("the rendered headline's own numbers always reconcile, across every shape", () => {
    // Render-level companion to the bucket-identity property test in
    // signalResolution.test.ts. That one pins the pure function; this one pins that the
    // PANEL reports those buckets rather than recomputing them into something that does
    // not add up — which is exactly what it did before F4.6.
    const SHAPES: readonly (readonly unknown[])[] = [
      [1, 1, 1],
      [1, 1, 1, 1, 1],
      [1, 1, 10, 10, 10],
      [1, 9999, 9999],
      [1, 1, 9999, "x"],
      [1, 1, 1, 9999, "x", 10, true],
    ];
    for (const shape of SHAPES) {
      cleanup();
      render(<CampaignSignalsPanel signalsToCollect={shape} signalCatalog={CATALOG_3} />);
      const text = screen.getByTestId("campaign-signals-panel-headline").textContent ?? "";
      const m =
        /^(\d+) distinct signals \((\d+) entries, (\d+) repeated, (\d+) unknown(?:, (\d+) unreadable)?\)$/.exec(
          text,
        );
      expect(m, `headline did not match the expected form for [${shape.map(String).join(", ")}]: "${text}"`).toBeTruthy();
      const distinct = Number(m![1]);
      const entries = Number(m![2]);
      const repeated = Number(m![3]);
      const unknown = Number(m![4]);
      const unreadable = m![5] === undefined ? 0 : Number(m![5]);
      expect(
        distinct + repeated + unknown + unreadable,
        `"${text}" does not reconcile: ${String(distinct)} + ${String(repeated)} + ` +
          `${String(unknown)} + ${String(unreadable)} != ${String(entries)} entries. ` +
          "A parenthetical that exists to explain a discrepancy must itself add up.",
      ).toBe(entries);
      expect(entries, "entry count must equal the declared list length").toBe(shape.length);
    }
  });

  it("scales correctly: 286 distinct, 293 entries, 7 repeated (staging shape)", () => {
    // Build a catalog where 7 ids are each duplicated (canonical + alias).
    // signalsToCollect repeats each of those 7 ids once, adding 7 extra entries.
    const canonicalIds = [101, 102, 103, 104, 105, 106, 107];
    const catalogItems: SignalItem[] = canonicalIds.flatMap((id) => [
      sig(id, "group_a", `SIGNAL_${String(id)}`),
      {
        signal_id: id,
        signal_group: "group_a",
        signal_name: `SIGNAL_${String(id)}_JsonAlias`,
        data_type: "boolean",
        status: "active",
        vss_path: `Vehicle.SIGNAL_${String(id)}`,
        alias_of_signal_id: String(id),
      } as SignalItem,
    ]);
    // 279 unique other ids
    const otherIds = Array.from({ length: 279 }, (_, i) => 200 + i);
    for (const id of otherIds) {
      catalogItems.push(sig(id, "group_b", `SIGNAL_OTHER_${String(id)}`));
    }
    // signalsToCollect: 286 unique + 7 duplicates = 293 entries, 0 unresolved
    const signalsToCollect = [
      ...canonicalIds,
      ...otherIds,
      ...canonicalIds, // the 7 duplicated ids
    ];
    expect(signalsToCollect.length).toBe(293);

    render(
      <CampaignSignalsPanel
        signalsToCollect={signalsToCollect}
        signalCatalog={catalogItems}
      />,
    );
    const headline = screen.getByTestId("campaign-signals-panel-headline");
    expect(headline.textContent).toBe(
      "286 distinct signals (293 entries, 7 repeated, 0 unknown)",
    );
  });
});

// ── 4. Unresolved IDs — surfaced only when non-zero ───────────────────────────

describe("unresolved IDs", () => {
  it("renders an unresolved warning when some IDs are not in the catalog", () => {
    // id 999 is not in CATALOG_3
    render(
      <CampaignSignalsPanel
        signalsToCollect={[1, 999]}
        signalCatalog={CATALOG_3}
      />,
    );
    const warn = screen.getByTestId("campaign-signals-panel-unresolved");
    expect(warn).toBeTruthy();
    expect(warn.textContent).toContain("1");
  });

  it("does NOT render the unresolved warning when all IDs resolve", () => {
    render(
      <CampaignSignalsPanel
        signalsToCollect={[1, 2]}
        signalCatalog={CATALOG_3}
      />,
    );
    expect(
      screen.queryByTestId("campaign-signals-panel-unresolved"),
    ).toBeNull();
  });
});

// ── 5. Groups — P2: collapsed by default ─────────────────────────────────────
//
// Cloudscape ExpandableSection renders its slot content in the DOM even when
// collapsed — it hides it with CSS, not with conditional rendering. The observable
// DOM signal for "collapsed" is `aria-expanded="false"` on the toggle button.
//
// MUTATION-VERIFIED (P2):
//   In CampaignSignalsPanel.tsx, add `defaultExpanded={true}` to the ExpandableSection
//   in the groups map. Doing so sets `aria-expanded="true"` on the toggle button.
//   The test below asserts `aria-expanded === "false"`, which FAILS with that mutation.
//   File restored byte-identical after the mutation was observed failing.
//
//   Applied 2026-09-20:
//     mutation → CampaignSignalsPanel.tsx (add defaultExpanded={true} to ExpandableSection)
//     caught by → "all group toggle buttons start aria-expanded false"
//       AssertionError: expected "true" to equal "false"

describe("groups — P2: collapsed by default", () => {
  it("all group toggle buttons start aria-expanded false (collapsed by default)", () => {
    render(
      <CampaignSignalsPanel
        signalsToCollect={[1, 2, 10]}
        signalCatalog={CATALOG_3}
      />,
    );
    // Group containers exist
    expect(screen.getByTestId("campaign-signals-group-safety")).toBeTruthy();
    expect(
      screen.getByTestId("campaign-signals-group-core_telemetry"),
    ).toBeTruthy();

    // Every ExpandableSection toggle button must report aria-expanded="false".
    // With defaultExpanded={true} the buttons report aria-expanded="true" → test FAILS.
    // With no defaultExpanded (collapsed default) → aria-expanded="false" → test PASSES.
    // Cloudscape ExpandableSection uses <span role="button" aria-expanded=...>.
    const toggleButtons = document.querySelectorAll('[role="button"][aria-expanded]');
    // At least one button per group must exist.
    expect(toggleButtons.length).toBeGreaterThanOrEqual(2);
    for (const btn of Array.from(toggleButtons)) {
      expect(
        btn.getAttribute("aria-expanded"),
        `button "${btn.textContent?.trim().substring(0, 30) ?? ""}" should be collapsed`,
      ).toBe("false");
    }
  });

  it("renders one ExpandableSection per distinct signal group", () => {
    render(
      <CampaignSignalsPanel
        signalsToCollect={[1, 2, 10]}
        signalCatalog={CATALOG_3}
      />,
    );
    // 2 groups: safety (2 signals), core_telemetry (1 signal)
    const groups = screen.getAllByTestId(/^campaign-signals-group-/);
    expect(groups.length).toBe(2);
  });
});

// ── 6. Group header includes name and count ───────────────────────────────────

describe("group header", () => {
  it("renders the group name and signal count in the header", () => {
    render(
      <CampaignSignalsPanel
        signalsToCollect={[1, 2]}
        signalCatalog={CATALOG_3}
      />,
    );
    // headerText for safety group: "safety (2)"
    const safetySection = screen.getByTestId("campaign-signals-group-safety");
    expect(safetySection.textContent).toContain("safety");
    expect(safetySection.textContent).toContain("2");
  });
});

// ── 7. No liveness vocabulary — P3 ───────────────────────────────────────────
//
// The campaignStatusVocabulary.test.ts guard scans source text, which means it
// also catches string literals in the component source. This test confirms at
// render time that none of the forbidden words appear in what the user sees.

describe("no liveness vocabulary — P3", () => {
  const FORBIDDEN = [
    "Transmitting",
    "Live",
    "Streaming",
    "Online",
    "Reporting",
    "Currently running",
    "Running",
  ] as const;

  it("renders none of the forbidden liveness vocabulary", () => {
    render(
      <CampaignSignalsPanel
        signalsToCollect={[1, 2, 10]}
        signalCatalog={CATALOG_3}
      />,
    );
    const body = document.body.textContent ?? "";
    for (const word of FORBIDDEN) {
      expect(body, `should not contain "${word}"`).not.toContain(word);
    }
  });
});

// ── 8. Column fields rendered in expanded table ───────────────────────────────
//
// Cloudscape ExpandableSection renders its slot content in the DOM even when
// collapsed — it hides it via CSS, not conditional rendering. The P2 comment
// above (§ 5) states this correctly. The earlier claim "We cannot expand in
// this environment" was false and has been removed: the testids
// signal-name-* / signal-vss-* emitted by SIGNAL_COLUMNS are queryable
// without any user interaction because the content is already in the DOM.
//
// MUTATION-VERIFIED (F4.8):
//   In CampaignSignalsPanel.tsx, delete the entire `unit` column from
//   SIGNAL_COLUMNS. The test "renders signal_name, unit, data_type, cycle_ms
//   when present" queries unit by textContent, so removing the column causes
//   the expected text to be absent → test FAILS.
//   File restored byte-identical after observation.

describe("column fields", () => {
  it("renders signal_name, unit, data_type, cycle_ms when present", () => {
    const catalogFull: SignalItem[] = [
      sig(10, "core_telemetry", "VEHICLE_SPEED", {
        unit: "km/h",
        cycle_ms: 100,
        data_type: "float",
      }),
    ];
    render(
      <CampaignSignalsPanel
        signalsToCollect={[10]}
        signalCatalog={catalogFull}
      />,
    );
    // signal-name-* testid is emitted by the signal_name cell renderer.
    // The content is in the DOM even when the ExpandableSection is collapsed.
    const nameCell = screen.getByTestId("signal-name-10");
    expect(nameCell.textContent).toBe("VEHICLE_SPEED");

    // vss_path cell emits signal-vss-* testid.
    const vssCell = screen.getByTestId("signal-vss-10");
    expect(vssCell.textContent).toContain("VEHICLE_SPEED");

    // unit, data_type, cycle_ms are rendered in the same table row.
    // They do not have dedicated testids, so assert by table text content.
    const section = screen.getByTestId("campaign-signals-group-core_telemetry");
    expect(section.textContent).toContain("km/h");       // unit
    expect(section.textContent).toContain("float");      // data_type
    expect(section.textContent).toContain("100");         // cycle_ms
  });

  it("renders absent markers for optional fields that are missing", () => {
    // sig() produces a SignalItem with no unit or cycle_ms; vss_path is set.
    const catalogMinimal: SignalItem[] = [
      sig(1, "safety", "ABS_ACTIVE"),
    ];
    render(
      <CampaignSignalsPanel
        signalsToCollect={[1]}
        signalCatalog={catalogMinimal}
      />,
    );
    // signal_name is always rendered via signal-name-* testid.
    const nameCell = screen.getByTestId("signal-name-1");
    expect(nameCell.textContent).toBe("ABS_ACTIVE");

    // vss_path is set on sig(), so its cell renders.
    const vssCell = screen.getByTestId("signal-vss-1");
    expect(vssCell.textContent).toContain("ABS_ACTIVE");

    // When unit is absent the column renders an em-dash placeholder.
    // The section text will contain "—" (em dash, U+2014).
    const section = screen.getByTestId("campaign-signals-group-safety");
    expect(section.textContent).toContain("—");
  });
});

// ── 9. Props-only: no fetch, no side effects ──────────────────────────────────

describe("props-only contract", () => {
  it("renders synchronously without any async waiting", () => {
    // If the panel had a useEffect that fetched, this test would render a loading
    // state, not the settle marker. Synchronous render proves the panel is props-only.
    render(
      <CampaignSignalsPanel
        signalsToCollect={[1]}
        signalCatalog={CATALOG_3}
      />,
    );
    // The panel renders immediately — no spinner, no loading state.
    expect(screen.queryByRole("progressbar")).toBeNull();
    // The panel root IS present without any await.
    expect(screen.getByTestId("campaign-signals-panel")).toBeTruthy();
  });
});


// ── Fix Group 6 — an unread catalog is not an empty catalog ────────────────────
//
// Found by review cycle 2. `fetchSignals()` returns null when the data-processing API is
// unconfigured and the shell degrades that to `[]`, so every declared id became "unresolved"
// and the panel reported "293 signal IDs not found in catalog" — an assertion about the
// catalog's CONTENTS derived from never having read it.
//
// It went uncovered because the only null-API test nulls ALL FOUR fetches, so the campaigns
// fetch short-circuits the shell to not-found and these panels never mount. A partial
// outage — one read failing while the others succeed — is the reachable case and had no test.
describe("catalog unavailable is distinct from ids missing from the catalog", () => {
  it("an empty catalog reports the catalog as unavailable, not the signals as absent", () => {
    render(<CampaignSignalsPanel signalsToCollect={[1, 2, 3]} signalCatalog={[]} />);

    expect(
      screen.getByTestId("campaign-signals-panel-catalog-unavailable"),
      "an empty catalog means the catalog was not read — say so",
    ).toBeTruthy();
    expect(
      screen.queryByTestId("campaign-signals-panel-unresolved"),
      'must NOT claim the ids are "not found in catalog" — that is a statement about the ' +
        "catalog's contents, and the catalog was never read",
    ).toBeNull();
    const text = document.body.textContent ?? "";
    expect(text).not.toContain("not found in catalog");
    // The declared count is still reported — it is known from the campaign, not the catalog.
    expect(screen.getByTestId("campaign-signals-panel-headline").textContent).toBe(
      "3 signals declared",
    );
  });

  it("a POPULATED catalog missing an id still reports that id as not found (positive control)", () => {
    // Without this, the fix above could be satisfied by never reporting unresolved ids at
    // all, which would hide the genuine catalog gap `unresolvedIds` exists to surface.
    render(<CampaignSignalsPanel signalsToCollect={[1, 9999]} signalCatalog={CATALOG_3} />);

    expect(
      screen.getByTestId("campaign-signals-panel-unresolved").textContent,
      "a real gap in a catalog that WAS read must still be reported",
    ).toContain("not found in catalog");
    expect(
      screen.queryByTestId("campaign-signals-panel-catalog-unavailable"),
    ).toBeNull();
  });

  it("reports repeated and unreadable counts even without a catalog — they need no catalog", () => {
    // Closing cycle 3's Suggestion. `distinct` genuinely requires the catalog and is dropped;
    // `repeated` and `unreadable` are derived from `signalsToCollect` alone, so withholding
    // them discards information the panel does have.
    render(
      <CampaignSignalsPanel signalsToCollect={[1, 1, 1, "x"]} signalCatalog={[]} />,
    );
    expect(screen.getByTestId("campaign-signals-panel-headline").textContent).toBe(
      "4 signals declared (2 repeated, 1 unreadable)",
    );
    expect(screen.getByTestId("campaign-signals-panel-catalog-unavailable")).toBeTruthy();
  });

  it("omits the parenthetical when there is nothing catalog-independent to report", () => {
    // Paired with the test above so the parenthetical cannot be unconditional.
    render(<CampaignSignalsPanel signalsToCollect={[1, 2, 3]} signalCatalog={[]} />);
    expect(screen.getByTestId("campaign-signals-panel-headline").textContent).toBe(
      "3 signals declared",
    );
  });
});
