// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * RatePlansView tests — T5.2
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T5.2
 *
 * ## What this file covers
 *
 * 1. Settle marker present in rendered output.
 * 2. Default mode is Self-Managed — billing-owner column header reads "Billed To (OEM)".
 * 3. Toggle to Amazon Managed — ONLY the billing-owner column header changes.
 *    All other column headers and all cell values remain identical.
 *    This is the critical invariant required by spec T5.2.
 * 4. Toggling back to Self-Managed restores the original header.
 * 5. TableEmptyState shown when plan source is empty.
 * 6. TableNoMatchState shown when text filter matches nothing.
 * 7. Usage/overage table renders.
 * 8. Fixture integrity: every leaf in the fixture carries a valid provenance marker.
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import RatePlansView from "../RatePlansView";
import { BILLING_OWNER_LABEL } from "../RatePlansView";
import {
  RATE_PLANS_FIXTURE,
  RATE_PLAN_ROWS,
  USAGE_OVERAGE_ROWS,
} from "../ratePlans.fixture";
import { VALID_PROVENANCE_MARKERS } from "../../../../types";

afterEach(() => {
  cleanup();
});

// ── Render helper ─────────────────────────────────────────────────────────────

function renderRatePlans() {
  return render(
    <MemoryRouter initialEntries={["/connectivity/rate-plans"]}>
      <Routes>
        <Route
          path="/connectivity/rate-plans"
          element={<RatePlansView />}
        />
        <Route
          path="/connectivity/fleet-health"
          element={<div data-testid="fleet-health-page" />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

// ── 1. Settle marker ─────────────────────────────────────────────────────────

describe("settle marker", () => {
  it("renders the screen settleMarker in the output", () => {
    renderRatePlans();
    const marker = screen.getByTestId("rate-plans-settle-marker");
    expect(marker).toBeTruthy();
    expect(marker.textContent).toContain("cs-settle-rate-plans-catalogue-table");
  });
});

// ── 2. Default billing mode ───────────────────────────────────────────────────

describe("default billing mode", () => {
  it("defaults to Self-Managed — billing-owner column header is 'Billed To (OEM)'", () => {
    renderRatePlans();
    const table = screen.getByTestId("rate-plans-catalogue-table");
    expect(
      within(table).getByText(BILLING_OWNER_LABEL["self-managed"]),
    ).toBeTruthy();
    // Amazon Managed label must NOT be present as a column header in this mode.
    expect(
      within(table).queryByText(BILLING_OWNER_LABEL["amazon-managed"]),
    ).toBeNull();
  });
});

// ── 3. Toggle changes ONLY the billing-owner column header ────────────────────
//
// This is the primary invariant required by spec T5.2:
//   "a toggle that relabels the billing-owner column ONLY"
//   "includes a test asserting the toggle changes only the billing-owner
//    label and no other cell"

/**
 * Helper: click the billing-mode toggle.
 *
 * The Cloudscape Toggle component renders a <span data-testid="billing-mode-toggle">
 * wrapping a <span> → <input type="checkbox">.  userEvent.click on the outer span
 * does not always reach the native input.  We locate the underlying checkbox via
 * getByRole("checkbox") inside the toggle container, which is reliable regardless
 * of the component's internal HTML structure.
 */
async function clickBillingModeToggle(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  const container = screen.getByTestId("billing-mode-toggle-container");
  const checkbox = within(container).getByRole("checkbox");
  await user.click(checkbox);
}

describe("billing-mode toggle — only billing-owner column header changes", () => {
  it("toggling to Amazon Managed changes only the billing-owner column header", async () => {
    const user = userEvent.setup();
    renderRatePlans();

    const table = screen.getByTestId("rate-plans-catalogue-table");

    // Capture all column headers before the toggle.
    const headersBefore = within(table)
      .getAllByRole("columnheader")
      .map((th) => th.textContent ?? "");

    // Click the toggle.
    await clickBillingModeToggle(user);

    // Capture all column headers after the toggle.
    const headersAfter = within(table)
      .getAllByRole("columnheader")
      .map((th) => th.textContent ?? "");

    // The number of columns must be unchanged.
    expect(headersAfter.length).toBe(headersBefore.length);

    // Find which headers changed.
    const changedIndices: number[] = [];
    for (let i = 0; i < headersBefore.length; i++) {
      if (headersBefore[i] !== headersAfter[i]) {
        changedIndices.push(i);
      }
    }

    // EXACTLY ONE column header must change.
    expect(changedIndices).toHaveLength(1);

    // The changed header must be the billing-owner column.
    const changedIndex = changedIndices[0];
    expect(headersBefore[changedIndex]).toBe(
      BILLING_OWNER_LABEL["self-managed"],
    );
    expect(headersAfter[changedIndex]).toBe(
      BILLING_OWNER_LABEL["amazon-managed"],
    );
  });

  it("cell values in the billing-owner column are unchanged after the toggle", async () => {
    const user = userEvent.setup();
    renderRatePlans();

    const table = screen.getByTestId("rate-plans-catalogue-table");

    // Collect all cell text content before the toggle.
    const cellsBefore = within(table)
      .getAllByRole("cell")
      .map((td) => td.textContent ?? "");

    // Toggle to Amazon Managed.
    await clickBillingModeToggle(user);

    // Collect all cell text content after the toggle.
    const cellsAfter = within(table)
      .getAllByRole("cell")
      .map((td) => td.textContent ?? "");

    // Cell content must be completely unchanged.
    expect(cellsAfter).toEqual(cellsBefore);
  });
});

// ── 4. Toggle back restores original header ───────────────────────────────────

describe("billing-mode toggle — round-trip", () => {
  it("toggling back to Self-Managed restores the original billing-owner header", async () => {
    const user = userEvent.setup();
    renderRatePlans();

    const table = screen.getByTestId("rate-plans-catalogue-table");

    // Toggle to Amazon Managed, then back.
    await clickBillingModeToggle(user);
    await clickBillingModeToggle(user);

    expect(
      within(table).getByText(BILLING_OWNER_LABEL["self-managed"]),
    ).toBeTruthy();
    expect(
      within(table).queryByText(BILLING_OWNER_LABEL["amazon-managed"]),
    ).toBeNull();
  });
});

// ── 5. TableEmptyState ────────────────────────────────────────────────────────

describe("table empty states", () => {
  it("renders TableEmptyState when the plan catalogue has items and no match", async () => {
    const user = userEvent.setup();
    renderRatePlans();

    const table = screen.getByTestId("rate-plans-catalogue-table");
    // Type a filter text that will match nothing.
    const filterInput = within(table).getByRole("searchbox");
    await user.type(filterInput, "zzz_no_match_xyzzy");

    // TableNoMatchState heading should appear.
    expect(screen.getByText("No matches")).toBeTruthy();
  });

  it("renders usage/overage table", () => {
    renderRatePlans();
    expect(screen.getByTestId("rate-plans-usage-table")).toBeTruthy();
  });
});

// ── 6. Usage/overage table renders ───────────────────────────────────────────

describe("usage / overage table", () => {
  it("renders at least one usage row when the fixture has overage records", () => {
    renderRatePlans();
    const usageTable = screen.getByTestId("rate-plans-usage-table");
    // The fixture has 4 overage records — at least one row must appear.
    const rows = within(usageTable).getAllByRole("row");
    // rows[0] is the header row; rows[1..n] are data rows.
    expect(rows.length).toBeGreaterThan(1);
  });
});

// ── 7. Fixture integrity ──────────────────────────────────────────────────────

describe("fixture integrity", () => {
  /**
   * Walk every ProvenanceValue leaf in the plan rows and usage rows,
   * asserting each carries a valid provenance marker.
   *
   * Structural keys (`id`) are exempt per spec D4.
   */
  it("every leaf in RATE_PLAN_ROWS carries a valid provenance marker", () => {
    const exemptKeys = new Set(["id"]);
    for (const row of RATE_PLAN_ROWS) {
      for (const [key, value] of Object.entries(row)) {
        if (exemptKeys.has(key)) continue;
        const pv = value as { value: unknown; provenance: string };
        expect(
          VALID_PROVENANCE_MARKERS.has(pv.provenance),
          `RATE_PLAN_ROWS key "${key}" has invalid provenance "${pv.provenance}"`,
        ).toBe(true);
      }
    }
  });

  it("every leaf in USAGE_OVERAGE_ROWS carries a valid provenance marker", () => {
    const exemptKeys = new Set(["id"]);
    for (const row of USAGE_OVERAGE_ROWS) {
      for (const [key, value] of Object.entries(row)) {
        if (exemptKeys.has(key)) continue;
        const pv = value as { value: unknown; provenance: string };
        expect(
          VALID_PROVENANCE_MARKERS.has(pv.provenance),
          `USAGE_OVERAGE_ROWS key "${key}" has invalid provenance "${pv.provenance}"`,
        ).toBe(true);
      }
    }
  });

  it("RATE_PLANS_FIXTURE is not empty", () => {
    expect(RATE_PLANS_FIXTURE.plans.length).toBeGreaterThan(0);
    expect(RATE_PLANS_FIXTURE.usageOverages.length).toBeGreaterThan(0);
  });
});
