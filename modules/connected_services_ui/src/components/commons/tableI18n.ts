// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * tableI18n.ts — Accessibility and counter-text helpers for Cloudscape tables.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T3.1 AC4
 * Reference: docs/tech.md § (g) — TextFilter, Pagination, CollectionPreferences
 *   verified against @cloudscape-design/components@3.0.1354
 *
 * Centralises the four i18n string helpers so every table uses consistent
 * English text and the same aria-live narration pattern.
 *
 * Zero-import constraint (T2.5): no *.fixture.ts, no API client, no fetch/XHR.
 */

import type { TableProps } from "@cloudscape-design/components/table";

// ── getTextFilterCounterText ──────────────────────────────────────────────────

/**
 * Returns the counter text for a TextFilter component.
 *
 * Pass this as `countText` on `<TextFilter>`.
 *
 * English conventions:
 *   count === 1  → "1 match out of N"
 *   count !== 1  → "N matches out of M"
 *
 * @param count       Items matching the current filter.
 * @param totalCount  Total items before filtering.
 */
export function getTextFilterCounterText(count: number, totalCount: number): string {
  const matchWord = count === 1 ? "match" : "matches";
  return `${count} ${matchWord} out of ${totalCount}`;
}

// ── getHeaderCounterText ──────────────────────────────────────────────────────

/**
 * Returns the counter text for a Table header.
 *
 * Pass this as `counter` on `<Header>`.
 *
 * - Filter active (filteredItemsCount defined): "(3/25)"
 * - No filter (filteredItemsCount undefined): "(25)"
 *
 * @param filteredItemsCount  From useCollection; undefined when no filter active.
 * @param totalCount          Total unfiltered item count.
 */
export function getHeaderCounterText(
  filteredItemsCount: number | undefined,
  totalCount: number,
): string {
  if (filteredItemsCount !== undefined) {
    return `(${filteredItemsCount}/${totalCount})`;
  }
  return `(${totalCount})`;
}

// ── renderAriaLive ────────────────────────────────────────────────────────────

/**
 * Produces an aria-live announcement string when the table contents change.
 *
 * @param filteredItemsCount  Current filtered count; undefined when no filter.
 * @param totalCount          Total items before filtering.
 */
export function renderAriaLive(
  filteredItemsCount: number | undefined,
  totalCount: number,
): string {
  if (filteredItemsCount !== undefined) {
    return `Displaying ${filteredItemsCount} out of ${totalCount} items`;
  }
  return `Displaying ${totalCount} items`;
}

// ── createTableSortLabelFn ────────────────────────────────────────────────────

/**
 * Creates a sort-label function for a specific column.
 *
 * Pass the returned function to `ariaLabel` on a ColumnDefinition so screen
 * readers announce the sort direction for each column.
 *
 * Source: docs/tech.md § (g) — TableProps.LabelData shape verified against
 * node_modules/@cloudscape-design/components/table/interfaces.d.ts
 *
 * @param column  The column definition whose sort label is needed.
 * @returns A function `(state: TableProps.LabelData) => string`.
 */
export function createTableSortLabelFn<T>(
  column: TableProps.ColumnDefinition<T>,
): (state: TableProps.LabelData) => string {
  return (state: TableProps.LabelData): string => {
    const header = typeof column.header === "string" ? column.header : "Column";
    if (!state.sorted) {
      return `${header}, not sorted`;
    }
    if (state.descending) {
      return `${header}, sorted descending`;
    }
    return `${header}, sorted ascending`;
  };
}
