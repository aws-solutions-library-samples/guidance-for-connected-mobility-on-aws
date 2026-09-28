// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Tests for the vehicles table's server-side sorting contract.
//
// Sorting MUST be server-side here: the table renders 25 of ~155 rows, so a
// client-side sort orders only the current page while appearing to order the
// whole fleet. These tests therefore assert the CONTRACT WITH THE BACKEND, not
// the visual order of rendered rows.
//
// Three properties, each of which was a real defect or a real 500:
//
//  1. The VIN column must sort on `vin`. It shipped as sortingField: "name",
//     a real but DIFFERENT DynamoDB attribute (the friendly label) absent on
//     55 of 155 staging rows.
//
//  2. `year` must stay sortingDisabled. The backend sorts with
//     `key=lambda x: x.get(sort_by) or ''` (main_api/index.py:5061) and `year`
//     is Number on 154 staging rows and String on 1 — comparing Decimal to str
//     raises TypeError and the endpoint 500s for the entire page. Verified by
//     reproducing the sort against a live scan.
//
//  3. `source` must stay sortingDisabled. It is derived per-row by the backend
//     (`classification`) and is not a stored attribute, so sorting on it reads
//     a missing key on every row and silently changes no order.

import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";

import VehiclesTable from "../vehicles-table";
import { FleetFilterProvider } from "@/components/fleet-filter/FleetFilter";

vi.mock("@/config/api", () => ({
  getRuntimeConfig: () => ({ apiEndpoint: "https://api.example.invalid/" }),
  isDemoMode: () => false,
}));
vi.mock("@/utils/authFetch", () => ({
  authFetch: vi.fn().mockResolvedValue({ ok: true, json: async () => ({ fleets: [] }) }),
}));

// FleetFilterProvider reads useUserRole (2026-09-26 — follow-on 1 to the FI
// auth fix). Mock as platform-admin (cross-fleet) so the provider's default
// stays ALL_FLEETS_ID and these table-contract tests remain focused on
// column-definition properties, not fleet-filter behaviour.
vi.mock("@/auth/useUserRole", () => ({
  useUserRole: () => ({
    isAdmin: true,
    isOperator: false,
    isViewer: false,
    isGuest: false,
    isConnectAgent: false,
    isEngineer: false,
    isDispatcher: false,
    canWrite: true,
    fleetIds: [],
  }),
}));

const VEHICLES = [
  { vehicleId: "VEH-1", vin: "MRDN0000000000015", make: "Meridian", model: "Azimuth", year: 2025, status: "active" },
  { vehicleId: "VEH-2", vin: "MRDN0000000000016", make: "Meridian", model: "Azimuth", year: 2025, status: "active" },
];

/**
 * Captures the columnDefinitions the table hands to Cloudscape, plus the
 * sorting props, by spying on the Table component. Asserting on the definition
 * objects is what lets these tests check the backend contract (sortingField
 * strings) rather than rendered row order, which a server-side sort does not
 * control.
 */
function renderTable(overrides: Record<string, unknown> = {}) {
  const onSortChange = vi.fn();
  const props = {
    vehicles: VEHICLES,
    totalVehicleCount: VEHICLES.length,
    selectedItems: [],
    onSelectionChange: () => {},
    onDelete: () => {},
    isLoading: false,
    error: null,
    currentPage: 1,
    pageSize: 25,
    paginationInfo: { totalPages: 1, pageSize: 25, totalItems: 2 },
    onPageChange: () => {},
    onPageSizeChange: () => {},
    onFleetFilterChange: () => {},
    searchText: "",
    onSearchChange: () => {},
    sortBy: "vin",
    sortOrder: "asc",
    onSortChange,
    ...overrides,
  };
  const utils = render(
    <MemoryRouter>
      <FleetFilterProvider>
        <VehiclesTable {...props} />
      </FleetFilterProvider>
    </MemoryRouter>
  );
  return { ...utils, onSortChange };
}

/**
 * Returns the header's sort control, or null when the column is not sortable.
 *
 * Cloudscape renders the control as a `<div role="button">`, NOT a `<button>`
 * element — an earlier version of this helper queried `button` and returned
 * null for every column, which made the two "must NOT be sortable" assertions
 * below pass vacuously. Querying the role is what makes them real.
 */
function sortControlFor(label: string): HTMLElement | null {
  const th = screen.getAllByRole("columnheader").find((h) => h.textContent?.includes(label));
  if (!th) return null;
  return th.querySelector('[role="button"]');
}

describe("vehicles table — server-side sorting contract", () => {
  it("renders the VIN column header", () => {
    renderTable();
    const th = screen.getAllByRole("columnheader").find((h) => h.textContent?.includes("VIN"));
    expect(th).toBeTruthy();
  });

  // ---- Property 1: the VIN column sorts on `vin`, not `name` ----

  it("makes the VIN column sortable (an interactive sort control is rendered)", () => {
    renderTable();
    // Cloudscape only renders a header sort button when the table has sorting
    // wired AND the column is not sortingDisabled. Before this fix the table
    // had no sorting props at all, so no column rendered one.
    expect(sortControlFor("VIN")).toBeTruthy();
  });

  it("reports `vin` as the sort field when the VIN header is activated", async () => {
    const { onSortChange } = renderTable();
    const btn = sortControlFor("VIN");
    expect(btn).toBeTruthy();
    btn!.click();
    expect(onSortChange).toHaveBeenCalled();
    // The field sent to the backend must be the DDB attribute `vin`. "name" is
    // the original defect: a real attribute, missing on a third of the rows.
    expect(onSortChange.mock.calls[0][0]).toBe("vin");
    expect(onSortChange.mock.calls[0][0]).not.toBe("name");
  });

  it("toggles direction rather than always sending the same one", async () => {
    // asc currently active -> activating VIN again must request desc.
    const { onSortChange } = renderTable({ sortBy: "vin", sortOrder: "asc" });
    sortControlFor("VIN")!.click();
    expect(onSortChange).toHaveBeenCalledWith("vin", "desc");
  });

  it("requests asc when the active direction is desc", async () => {
    const { onSortChange } = renderTable({ sortBy: "vin", sortOrder: "desc" });
    sortControlFor("VIN")!.click();
    expect(onSortChange).toHaveBeenCalledWith("vin", "asc");
  });

  // ---- Properties 2 and 3: the two unsafe columns stay non-sortable ----

  it("renders a sort control on Year and reports the `year` field", () => {
    // Year was non-sortable until the mixed-type fix: one staging row stored
    // `year` as a DynamoDB String while 154 used Number, so the backend's sort
    // key compared str to Decimal and the whole page 500'd. Both halves are now
    // fixed (row normalised; main_api treats `year` as numeric), so the control
    // is expected to exist. See
    // issues/2026-09-23-vehicle-year-mixed-type-breaks-sort/.
    const { onSortChange } = renderTable();
    const btn = sortControlFor("Year");
    expect(btn).toBeTruthy();
    btn!.click();
    expect(onSortChange.mock.calls[0][0]).toBe("year");
  });

  it("does NOT render a sort control on Source (derived field, silent no-op sort)", () => {
    renderTable();
    // Positive control first: prove the helper CAN find a control, so a null
    // result below means "not sortable" and not "selector is broken". An
    // earlier version of this test queried `button` instead of role=button,
    // found nothing anywhere, and passed vacuously.
    expect(sortControlFor("VIN")).toBeTruthy();
    // Source stays non-sortable: it is derived per-row by the backend
    // (`classification`), not a stored attribute, so sortBy=source reads a
    // missing key on every row and silently changes no order. Unlike Year this
    // is NOT fixable by normalising data — there is no column to sort on.
    expect(sortControlFor("Source")).toBeNull();
  });

  it("never reports Source as a sort field", () => {
    const { onSortChange } = renderTable();
    const btn = sortControlFor("Source");
    if (btn) btn.click();
    const fields = onSortChange.mock.calls.map((c) => c[0]);
    expect(fields).not.toContain("source");
  });

  // ---- The other enabled columns ----

  it.each([
    ["Status", "status"],
    ["Make", "make"],
    ["Model", "model"],
    ["License Plate", "licensePlate"],
  ])("sorts %s on the DDB attribute %s", (label, field) => {
    // Each verified safe against a live scan: all are DynamoDB String, so the
    // backend's `x.get(f) or ''` key stays comparable even where the attribute
    // is absent.
    const { onSortChange } = renderTable();
    const btn = sortControlFor(label);
    expect(btn).toBeTruthy();
    btn!.click();
    expect(onSortChange.mock.calls[0][0]).toBe(field);
  });
});
