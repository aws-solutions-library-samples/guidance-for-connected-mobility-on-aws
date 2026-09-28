// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * CampaignSignalsPanel — resolved signal catalog for one data-collection campaign.
 *
 * Spec: `.kiro/specs/2026-09-20-cs-campaigns-screen-restructure/tasks.md` T3.2
 * Contract: `.kiro/specs/2026-09-20-cs-campaigns-screen-restructure/group3-contract.md` §2
 *
 * ## What this panel shows
 *
 * `signalsToCollect` is a list of numeric IDs declared in the campaign definition.
 * The join against `signalCatalog` is done by `resolveCollectedSignals` from
 * `signalResolution.ts` — no reimplementation here.
 *
 * Resolved signals are bucketed by `signal_group` and rendered in collapsible
 * groups, **collapsed by default**. Rendering 293 rows expanded by default is
 * not useful and would degrade the detail view for every operator who opens it.
 *
 * ## The two-number reporting obligation
 *
 * The list view column shows the **entry** count (293 for `cms-fleet-gps-10s`).
 * This panel resolves **distinct** signals (286 for the same campaign). The two
 * numbers differ because 7 ids appear twice each in `signalsToCollect`. If this
 * panel rendered 286 without explaining the difference, it would appear to
 * contradict the list view the operator just navigated from.
 *
 * Required rendering when they differ:
 *   "286 distinct signals (293 entries, 7 repeated)"
 * When they agree, no parenthetical is needed.
 *
 * ## Props — no fetch, no useEffect
 *
 * The shell (`CampaignDetailView`, T3.1) owns all fetches and passes data as props
 * per the contract. This panel never fetches.
 *
 * ## Guard compliance (§6)
 *
 * - No `interface Campaign<X>` — props are inline on `React.FC<{...}>` per §6a.
 * - No liveness vocabulary (Transmitting, Live, Streaming, Online, Reporting,
 *   Currently running, Running delimited) per §6b.
 * - No assign call, so §6c does not apply.
 * - Not lazily imported by App.tsx, so §6d does not require registry edits.
 */

import Box from "@cloudscape-design/components/box";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import React, { useMemo } from "react";

import type { SignalItem } from "../../../api/dataModelClient";
import { resolveCollectedSignals } from "./signalResolution";

// ── Column definitions for the per-group signal table ─────────────────────────

const SIGNAL_COLUMNS = [
  {
    id: "signal_name",
    header: "Signal name",
    cell: (item: SignalItem) => (
      <Box data-testid={`signal-name-${String(item.signal_id)}`}>
        {item.signal_name}
      </Box>
    ),
  },
  {
    id: "vss_path",
    header: "VSS path",
    cell: (item: SignalItem) =>
      item.vss_path ? (
        <Box data-testid={`signal-vss-${String(item.signal_id)}`}>
          {item.vss_path}
        </Box>
      ) : (
        <Box color="text-status-inactive">
          <em>—</em>
        </Box>
      ),
  },
  {
    id: "unit",
    header: "Unit",
    cell: (item: SignalItem) =>
      item.unit ? (
        <Box>{item.unit}</Box>
      ) : (
        <Box color="text-status-inactive">
          <em>—</em>
        </Box>
      ),
  },
  {
    id: "data_type",
    header: "Data type",
    cell: (item: SignalItem) => <Box>{item.data_type}</Box>,
  },
  {
    id: "cycle_ms",
    header: "Cycle (ms)",
    cell: (item: SignalItem) =>
      item.cycle_ms !== undefined ? (
        <Box>{String(item.cycle_ms)}</Box>
      ) : (
        <Box color="text-status-inactive">
          <em>—</em>
        </Box>
      ),
  },
];

// ── CampaignSignalsPanel ───────────────────────────────────────────────────────

/**
 * CampaignSignalsPanel
 *
 * Props are data-only — no fetching. The shell resolves them before rendering.
 *
 * `signalCatalog` is the full `/signals` response. `signalsToCollect` is the raw
 * list from the campaign definition (numeric IDs, possibly with duplicates).
 */
const CampaignSignalsPanel: React.FC<{
  readonly signalsToCollect: readonly unknown[] | undefined;
  readonly signalCatalog: readonly SignalItem[];
}> = ({ signalsToCollect, signalCatalog }) => {
  // All resolution is delegated to signalResolution.ts — do not reimplement it.
  const resolved = useMemo(
    () => resolveCollectedSignals(signalsToCollect, signalCatalog),
    [signalsToCollect, signalCatalog],
  );

  const entryCount = signalsToCollect?.length ?? 0;
  const distinctCount = resolved.resolvedCount;
  // Per-ENTRY counts, not per-distinct-id. `duplicateIds.length` was used here until
  // Fix Group 4 F4.6 and it is a different number: an id appearing three times is ONE
  // duplicated id but TWO duplicate entries, so `[1, 1, 1]` rendered
  // "1 distinct signals (3 entries, 1 repeated, 0 unknown)" — 1 + 1 + 0 = 2, not 3.
  const repeatedCount = resolved.duplicateEntryCount;
  const unresolvedCount = resolved.unresolvedIds.length;
  const unreadableCount = resolved.malformedEntryCount;

  // Headline: account for every entry so the arithmetic reconciles.
  //
  //   entries = distinct + repeated + unknown + unreadable
  //
  // This identity holds by construction (`resolveCollectedSignals` assigns each entry to
  // exactly one bucket) and is property-tested over a matrix of inputs in
  // signalResolution.test.ts, not just asserted on a fixture or two.
  //
  // The parenthetical exists to EXPLAIN why the distinct count differs from the entry
  // count the list row shows, so a parenthetical whose numbers do not add up is worse
  // than none: it invites a subtraction that fails. `unreadable` is appended only when
  // non-zero, since it is unreachable from live data (`signalsToCollect` is a list of
  // numbers) and naming it unconditionally would imply a problem that does not exist.
  //
  // Examples:
  //   all resolve, no dups → "286 signals"
  //   dups only            → "286 distinct signals (293 entries, 7 repeated, 0 unknown)"
  //   id repeated 3x       → "1 distinct signals (3 entries, 2 repeated, 0 unknown)"
  //   dups + unresolved    → "2 distinct signals (4 entries, 1 repeated, 1 unknown)"
  const needsParenthetical =
    repeatedCount > 0 || unresolvedCount > 0 || unreadableCount > 0;
  const headline = needsParenthetical
    ? `${String(distinctCount)} distinct signals (${String(entryCount)} entries, ${String(repeatedCount)} repeated, ${String(unresolvedCount)} unknown${
        unreadableCount > 0 ? `, ${String(unreadableCount)} unreadable` : ""
      })`
    : `${String(distinctCount)} signals`;

  // F4.7: Distinguish undefined (no definition row → cannot know the signal list)
  // from [] (definition exists and declares no signals).
  // signalsToCollect is undefined when template is null — the shell passes
  // template?.signalsToCollect which is undefined when template itself is null.
  if (signalsToCollect === undefined) {
    return (
      <Box
        color="text-status-inactive"
        data-testid="campaign-signals-panel-empty"
      >
        Signal list unavailable — no definition row.
      </Box>
    );
  }

  if (entryCount === 0) {
    return (
      <Box
        color="text-status-inactive"
        data-testid="campaign-signals-panel-empty"
      >
        No signals declared for this campaign.
      </Box>
    );
  }

  // The catalog itself was not readable. `fetchSignals()` returns null when the
  // data-processing API is unconfigured and the shell degrades that to `[]`, so an empty
  // catalog alongside declared entries means "we could not read the catalog" — NOT "the
  // catalog does not contain these signals".
  //
  // Conflating them made the panel claim, on the live campaign, "293 signal IDs not found in
  // catalog" when one read had returned null: an assertion about the catalog's CONTENTS
  // derived from never having read it. Same class as the undefined-vs-[] conflation F4.7
  // fixed one prop over, and the opposite of what `unresolvedIds`' own docstring intends —
  // it exists to make a genuine catalog GAP visible, which requires a catalog that was read.
  const catalogUnavailable = signalCatalog.length === 0;

  if (catalogUnavailable) {
    // `repeated` and `unreadable` are derived from `signalsToCollect` alone and need no
    // catalog, so they stay valid here and are reported. Only `distinct` is dropped, because
    // nothing could be resolved — reporting it would be the same over-claim as the
    // "not found in catalog" wording this branch exists to replace.
    const declaredNotes: string[] = [];
    if (repeatedCount > 0) declaredNotes.push(`${String(repeatedCount)} repeated`);
    if (unreadableCount > 0) declaredNotes.push(`${String(unreadableCount)} unreadable`);
    return (
      <SpaceBetween size="m" data-testid="campaign-signals-panel">
        <Box data-testid="campaign-signals-panel-headline">
          {`${String(entryCount)} signal${entryCount === 1 ? "" : "s"} declared${
            declaredNotes.length > 0 ? ` (${declaredNotes.join(", ")})` : ""
          }`}
        </Box>
        <StatusIndicator
          type="warning"
          data-testid="campaign-signals-panel-catalog-unavailable"
        >
          Signal catalog unavailable — these signal IDs could not be resolved to names
        </StatusIndicator>
      </SpaceBetween>
    );
  }

  return (
    <SpaceBetween size="m" data-testid="campaign-signals-panel">
      {/* Summary line */}
      <Box data-testid="campaign-signals-panel-headline">{headline}</Box>

      {/* Unresolved-ID warning — surfaced only when non-zero. Reachable only with a
          populated catalog, so it is a genuine claim about the catalog's contents. */}
      {unresolvedCount > 0 && (
        <StatusIndicator
          type="warning"
          data-testid="campaign-signals-panel-unresolved"
        >
          {`${String(unresolvedCount)} signal ID${unresolvedCount === 1 ? "" : "s"} not found in catalog`}
        </StatusIndicator>
      )}

      {/* Signal groups — collapsed by default so 293 rows are not rendered expanded */}
      <SpaceBetween size="xs" data-testid="campaign-signals-panel-groups">
        {resolved.groups.map(({ group, signals }) => (
          <ExpandableSection
            key={group}
            headerText={`${group} (${String(signals.length)})`}
            // defaultExpanded is deliberately absent (falsy) — groups are collapsed by default.
            // Rendering 293 rows expanded is not useful and violates T3.2's Constraints.
            data-testid={`campaign-signals-group-${group}`}
          >
            <Table
              columnDefinitions={SIGNAL_COLUMNS}
              items={signals as SignalItem[]}
              variant="embedded"
              ariaLabels={{
                tableLabel: `${group} signals`,
              }}
              data-testid={`campaign-signals-table-${group}`}
            />
          </ExpandableSection>
        ))}
      </SpaceBetween>
    </SpaceBetween>
  );
};

export default CampaignSignalsPanel;
