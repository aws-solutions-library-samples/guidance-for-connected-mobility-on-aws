// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * useConnectedServicesCollection.ts — Thin wrapper around useCollection
 * pre-wiring the standard filtering / pagination / sorting config for
 * Connected Services tables.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T3.1 AC1-2
 * Reference: docs/tech.md § (a) — useCollection signature and UseCollectionResult
 *   verified against @cloudscape-design/collection-hooks@1.0.107
 *   node_modules/@cloudscape-design/collection-hooks/mjs/use-collection.d.ts
 *   node_modules/@cloudscape-design/collection-hooks/mjs/interfaces.d.ts
 *
 * ## Return contract (AC1)
 *
 * Returns useCollection's shape UNCHANGED — no renamed or wrapped fields.
 * A call site can swap `useConnectedServicesCollection` for a direct `useCollection`
 * call without touching any other line of code.
 *
 * ## filteredItemsCount normalisation
 *
 * collection-hooks returns the total count (not undefined) when filtering is
 * configured but `filteringText` is empty. We normalise here so call sites
 * get `undefined` when no filter is in effect, matching the documented contract
 * in docs/tech.md § (a) and the behaviour asserted by the contract test.
 *
 * ## Zero-import constraint (T2.5)
 *
 * Zero imports from *.fixture.ts, any API client, fetch/XHR, or ../screens/.
 */

import React from "react";
import { useCollection } from "@cloudscape-design/collection-hooks";
import type { UseCollectionResult } from "@cloudscape-design/collection-hooks";
import type { TableProps } from "@cloudscape-design/components/table";

import { TableEmptyState, TableNoMatchState } from "./tableStates";

// ── Public interface ──────────────────────────────────────────────────────────

export interface ConnectedServicesCollectionOptions<T> {
  /**
   * Human-readable resource name in singular form.
   * Drives the empty-state ("No <resourceName>") and no-match-state copy.
   */
  resourceName: string;

  /** Items per page; passed to pagination.pageSize. */
  pageSize: number;

  /**
   * Default sorting column. When supplied, the table opens pre-sorted on this
   * column in ascending order.
   */
  defaultSortingColumn?: TableProps.ColumnDefinition<T>;

  /** When true, enables item selection (row checkboxes). */
  selection?: boolean;

  /**
   * Invoked when the user clicks "Clear filter" in the no-match state.
   * Call sites may also call `actions.setFiltering("")` directly.
   */
  onClearFilter?: () => void;
}

// ── Hook ──────────────────────────────────────────────────────────────────────

/**
 * Wraps useCollection with the Connected Services standard config so every
 * table gets consistent filtering, pagination, sorting, and empty/no-match
 * states without repeating config boilerplate at every call site.
 *
 * Returns useCollection's shape unchanged:
 *   { items, allPageItems, filteredItemsCount, actions,
 *     collectionProps, filterProps, propertyFilterProps, paginationProps }
 *
 * @param items  All items to pass to useCollection (the unfiltered/unsorted source).
 * @param opts   Standard options — see ConnectedServicesCollectionOptions.
 */
export function useConnectedServicesCollection<T>(
  items: readonly T[],
  opts: ConnectedServicesCollectionOptions<T>,
): UseCollectionResult<T> {
  const {
    resourceName,
    pageSize,
    defaultSortingColumn,
    selection,
    onClearFilter,
  } = opts;

  // React.createElement used here so this file stays pure TypeScript with no
  // JSX pragma requirement. JSX files in this module use .tsx explicitly.
  const emptyStateNode = React.createElement(TableEmptyState, { resourceName });
  const noMatchStateNode = React.createElement(TableNoMatchState, {
    onClearFilter: onClearFilter ?? (() => undefined),
  });

  const result = useCollection(items, {
    filtering: {
      empty: emptyStateNode,
      noMatch: noMatchStateNode,
    },
    pagination: {
      pageSize,
    },
    sorting: defaultSortingColumn
      ? {
          defaultState: {
            sortingColumn: defaultSortingColumn,
            isDescending: false,
          },
        }
      : {},
    selection: selection ? {} : undefined,
  });

  // Normalise filteredItemsCount: collection-hooks returns a number even when
  // filteringText is empty (it counts all items). Normalise to undefined when
  // no filter is active so call sites can use `filteredItemsCount !== undefined`
  // as the "filter is active" signal, matching docs/tech.md § (a).
  const normalizedFilteredItemsCount =
    result.filterProps.filteringText === ""
      ? undefined
      : result.filteredItemsCount;

  return {
    ...result,
    filteredItemsCount: normalizedFilteredItemsCount,
  };
}
