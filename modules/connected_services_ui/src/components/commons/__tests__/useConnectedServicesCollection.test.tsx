// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * useConnectedServicesCollection.test.tsx — contract test for the collection wrapper.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T3.1 AC2
 *
 * ## What this test covers (T3.1 AC2)
 *
 * "A contract test asserts the wrapper's return shape matches useCollection's
 * key-for-key (DMS's useDmsCollection.test.tsx is the template)."
 *
 * Assertions:
 * - Returns all required keys from UseCollectionResult unchanged.
 * - Filtering narrows items to matching rows.
 * - filteredItemsCount is undefined when no filter is active.
 * - filteredItemsCount equals match count when a filter is active.
 * - Pagination slices items to pageSize.
 * - onClearFilter is wired — actions.setFiltering('') restores all items.
 * - Empty array produces zero items and a clean render (no throw, no gate).
 */

import { describe, it, expect, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useConnectedServicesCollection } from "../useConnectedServicesCollection";

// ── Test data ─────────────────────────────────────────────────────────────────

interface TestItem {
  id: string;
  name: string;
  market: string;
}

const ITEMS: readonly TestItem[] = [
  { id: "1", name: "Alpha device", market: "US" },
  { id: "2", name: "Beta unit", market: "DE" },
  { id: "3", name: "Gamma module", market: "US" },
  { id: "4", name: "Delta sensor", market: "IN" },
  { id: "5", name: "Epsilon modem", market: "US" },
];

const DEFAULT_OPTS = {
  resourceName: "vehicle",
  pageSize: 3,
};

function renderCollection(
  items: readonly TestItem[] = ITEMS,
  opts: Parameters<typeof useConnectedServicesCollection<TestItem>>[1] = DEFAULT_OPTS,
) {
  return renderHook(() => useConnectedServicesCollection(items, opts));
}

// ── Return shape (AC2 key-for-key contract) ───────────────────────────────────

describe("useConnectedServicesCollection — return shape (AC2)", () => {
  it("returns all required keys from UseCollectionResult", () => {
    const { result } = renderCollection();

    // Each of these must be present on the return value — no renames allowed.
    expect(result.current).toHaveProperty("items");
    expect(result.current).toHaveProperty("allPageItems");
    expect(result.current).toHaveProperty("filteredItemsCount");
    expect(result.current).toHaveProperty("actions");
    expect(result.current).toHaveProperty("collectionProps");
    expect(result.current).toHaveProperty("filterProps");
    expect(result.current).toHaveProperty("paginationProps");
  });

  it("items is an array", () => {
    const { result } = renderCollection();
    expect(Array.isArray(result.current.items)).toBe(true);
  });

  it("actions exposes setFiltering", () => {
    const { result } = renderCollection();
    expect(typeof result.current.actions.setFiltering).toBe("function");
  });

  it("filterProps exposes filteringText and onChange", () => {
    const { result } = renderCollection();
    expect(typeof result.current.filterProps.filteringText).toBe("string");
    expect(typeof result.current.filterProps.onChange).toBe("function");
  });

  it("paginationProps exposes currentPageIndex, pagesCount, and onChange", () => {
    const { result } = renderCollection();
    expect(typeof result.current.paginationProps.currentPageIndex).toBe("number");
    expect(typeof result.current.paginationProps.pagesCount).toBe("number");
    expect(typeof result.current.paginationProps.onChange).toBe("function");
  });
});

// ── Pagination ────────────────────────────────────────────────────────────────

describe("useConnectedServicesCollection — pagination", () => {
  it("slices items to pageSize on the first page", () => {
    // 5 items, pageSize=3 → first page has 3 items.
    const { result } = renderCollection(ITEMS, { ...DEFAULT_OPTS, pageSize: 3 });
    expect(result.current.items).toHaveLength(3);
  });

  it("paginationProps.pagesCount reflects total pages", () => {
    // 5 items, pageSize=3 → ceil(5/3) = 2 pages.
    const { result } = renderCollection(ITEMS, { ...DEFAULT_OPTS, pageSize: 3 });
    expect(result.current.paginationProps.pagesCount).toBe(2);
  });

  it("page 2 contains remaining items", () => {
    const { result } = renderCollection(ITEMS, { ...DEFAULT_OPTS, pageSize: 3 });

    act(() => {
      result.current.paginationProps.onChange({
        detail: { currentPageIndex: 2 },
      } as never);
    });

    expect(result.current.items).toHaveLength(2);
  });

  it("single page when items ≤ pageSize", () => {
    const { result } = renderCollection(ITEMS, { ...DEFAULT_OPTS, pageSize: 10 });
    expect(result.current.paginationProps.pagesCount).toBe(1);
    expect(result.current.items).toHaveLength(5);
  });
});

// ── Filtering ─────────────────────────────────────────────────────────────────

describe("useConnectedServicesCollection — filtering", () => {
  it("active filter narrows items to matching rows", () => {
    const { result } = renderCollection(ITEMS, { ...DEFAULT_OPTS, pageSize: 10 });

    act(() => {
      result.current.filterProps.onChange({
        detail: { filteringText: "Alpha" },
      } as never);
    });

    expect(result.current.items).toHaveLength(1);
    expect((result.current.items[0] as TestItem).name).toBe("Alpha device");
  });

  it("filter is case-insensitive (collection-hooks default)", () => {
    const { result } = renderCollection(ITEMS, { ...DEFAULT_OPTS, pageSize: 10 });

    act(() => {
      result.current.filterProps.onChange({
        detail: { filteringText: "alpha" },
      } as never);
    });

    expect(result.current.items).toHaveLength(1);
  });

  it("filter with no match returns empty items", () => {
    const { result } = renderCollection(ITEMS, { ...DEFAULT_OPTS, pageSize: 10 });

    act(() => {
      result.current.filterProps.onChange({
        detail: { filteringText: "zzz-no-match-zzz" },
      } as never);
    });

    expect(result.current.items).toHaveLength(0);
  });

  it("clearing the filter restores all items", () => {
    const { result } = renderCollection(ITEMS, { ...DEFAULT_OPTS, pageSize: 10 });

    act(() => {
      result.current.filterProps.onChange({
        detail: { filteringText: "Alpha" },
      } as never);
    });

    expect(result.current.items).toHaveLength(1);

    act(() => {
      result.current.filterProps.onChange({
        detail: { filteringText: "" },
      } as never);
    });

    expect(result.current.items).toHaveLength(ITEMS.length);
  });
});

// ── filteredItemsCount normalisation ─────────────────────────────────────────

describe("useConnectedServicesCollection — filteredItemsCount", () => {
  it("is undefined when no filter is active", () => {
    const { result } = renderCollection(ITEMS, { ...DEFAULT_OPTS, pageSize: 10 });
    // No filter → undefined (normalised from collection-hooks' raw count).
    expect(result.current.filteredItemsCount).toBeUndefined();
  });

  it("equals match count when a filter is active", () => {
    const { result } = renderCollection(ITEMS, { ...DEFAULT_OPTS, pageSize: 10 });

    act(() => {
      result.current.filterProps.onChange({
        detail: { filteringText: "Alpha" },
      } as never);
    });

    expect(result.current.filteredItemsCount).toBe(1);
  });

  it("is 0 when the filter matches nothing", () => {
    const { result } = renderCollection(ITEMS, { ...DEFAULT_OPTS, pageSize: 10 });

    act(() => {
      result.current.filterProps.onChange({
        detail: { filteringText: "zzz-no-match" },
      } as never);
    });

    expect(result.current.filteredItemsCount).toBe(0);
  });

  it("becomes undefined again after the filter is cleared", () => {
    const { result } = renderCollection(ITEMS, { ...DEFAULT_OPTS, pageSize: 10 });

    act(() => {
      result.current.filterProps.onChange({
        detail: { filteringText: "Alpha" },
      } as never);
    });

    expect(result.current.filteredItemsCount).toBe(1);

    act(() => {
      result.current.filterProps.onChange({
        detail: { filteringText: "" },
      } as never);
    });

    expect(result.current.filteredItemsCount).toBeUndefined();
  });
});

// ── onClearFilter ─────────────────────────────────────────────────────────────

describe("useConnectedServicesCollection — onClearFilter", () => {
  it("hook accepts onClearFilter without throwing", () => {
    const onClearFilter = vi.fn();

    expect(() =>
      renderCollection(ITEMS, { ...DEFAULT_OPTS, pageSize: 10, onClearFilter }),
    ).not.toThrow();
  });

  it("actions.setFiltering('') restores all items after a filter", () => {
    const { result } = renderCollection(ITEMS, { ...DEFAULT_OPTS, pageSize: 10 });

    act(() => {
      result.current.filterProps.onChange({
        detail: { filteringText: "Alpha" },
      } as never);
    });

    expect(result.current.items).toHaveLength(1);

    act(() => {
      result.current.actions.setFiltering("");
    });

    expect(result.current.items).toHaveLength(ITEMS.length);
  });
});

// ── Empty array — no loading/error gate needed ────────────────────────────────

describe("useConnectedServicesCollection — empty array", () => {
  it("returns zero items without throwing when source array is empty", () => {
    // This is the 'no data' state — the grid must still render (T3.1 constraint).
    expect(() => renderCollection([], DEFAULT_OPTS)).not.toThrow();
    const { result } = renderCollection([], DEFAULT_OPTS);
    expect(result.current.items).toHaveLength(0);
  });

  it("filteredItemsCount is undefined when source array is empty and no filter active", () => {
    const { result } = renderCollection([], DEFAULT_OPTS);
    expect(result.current.filteredItemsCount).toBeUndefined();
  });
});
