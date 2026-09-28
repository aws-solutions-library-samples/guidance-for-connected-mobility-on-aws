// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * tableStates.tsx — Two distinct empty-state components for Cloudscape tables.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T3.1 AC3
 * Reference: docs/tech.md § (f) — useCollection filtering.empty / filtering.noMatch
 *
 * ## Why two components, not one
 *
 * `TableEmptyState`   — the data set has NO items. The user needs to create data.
 * `TableNoMatchState` — the data set has items but the active filter matched nothing.
 *                       The user needs to clear or refine the filter.
 *
 * These are fundamentally different situations. Merging them into one component
 * hides the cause from the user (the DMS lesson recorded in T3.1 Constraints).
 *
 * ## Heading uniqueness
 *
 * "No <resourceName>" vs "No matches" — headings intentionally differ so a
 * testing-library query can target either by text without ambiguity.
 *
 * ## Zero-import constraint (T2.5)
 *
 * Zero imports from *.fixture.ts, any API client, fetch/XHR, or ../screens/.
 */

import React from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";

// ── TableEmptyState ───────────────────────────────────────────────────────────

export interface TableEmptyStateProps {
  /**
   * Human-readable resource name in singular form.
   * E.g. "vehicle" → heading "No vehicles", body "No vehicles to display."
   */
  resourceName: string;

  /**
   * Optional description override for the body sentence.
   *
   * When supplied, MUST NOT equal the heading ("No <resourceName>").
   * Use this to add context-specific scoping, e.g.
   * "No vehicles found in this market." vs the generic body.
   * Tested by AC3: the empty-state description must differ from the heading.
   */
  description?: string;
}

/**
 * Shown when the table's source array has zero items (no data exists yet).
 *
 * Heading:  "No <resourceName>"
 * Body:     "No <resourceName> to display."  (or the description override)
 */
export function TableEmptyState({
  resourceName,
  description,
}: TableEmptyStateProps): React.JSX.Element {
  return (
    <Box textAlign="center" color="inherit">
      <Box variant="strong" textAlign="center" color="inherit">
        <h3>No {resourceName}</h3>
      </Box>
      <Box variant="p" padding={{ bottom: "s" }} color="inherit">
        {description ?? `No ${resourceName} to display.`}
      </Box>
    </Box>
  );
}

// ── TableNoMatchState ─────────────────────────────────────────────────────────

export interface TableNoMatchStateProps {
  /** Invoked when the user clicks the "Clear filter" button. */
  onClearFilter: () => void;
}

/**
 * Shown when a text filter is active but produced zero matching rows.
 *
 * Heading:  "No matches"
 * Body:     "No items matched your filter criteria."
 * Action:   "Clear filter" button invoking onClearFilter.
 */
export function TableNoMatchState({
  onClearFilter,
}: TableNoMatchStateProps): React.JSX.Element {
  return (
    <Box textAlign="center" color="inherit">
      <Box variant="strong" textAlign="center" color="inherit">
        <h3>No matches</h3>
      </Box>
      <Box variant="p" padding={{ bottom: "s" }} color="inherit">
        No items matched your filter criteria.
      </Box>
      <SpaceBetween size="xs" direction="horizontal" alignItems="center">
        <Button onClick={onClearFilter}>Clear filter</Button>
      </SpaceBetween>
    </Box>
  );
}
