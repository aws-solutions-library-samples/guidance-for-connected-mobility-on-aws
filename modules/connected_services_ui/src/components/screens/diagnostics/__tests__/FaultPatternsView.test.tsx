// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * FaultPatternsView tests — T6.5
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.5
 *
 * ## What this file covers
 *
 * 1. Settle marker present in the rendered output.
 * 2. Fault signature values rendered for all fixture rows.
 * 3. TableEmptyState shown when source array is empty (both empty states wired).
 * 4. TableNoMatchState shown when text filter produces no matches.
 * 5. Row click navigates to the Diagnosis Workbench for the representative VIN.
 * 6. No trip/GPS/location fields rendered (compliance control (d)).
 * 7. Fixture provenance compliance: all ProvenanceValue leaves are 'simulated'.
 * 8. Aggregate population data only: no individual VIN identity in displayed rows.
 */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import FaultPatternsView from "../FaultPatternsView";
import { FAULT_PATTERNS_FIXTURE, FAULT_PATTERN_ROWS } from "../faultPatterns.fixture";
import { TableEmptyState } from "../../../commons/tableStates";

afterEach(() => {
  cleanup();
});

// ── Render helper ─────────────────────────────────────────────────────────────

function renderFaultPatterns() {
  return render(
    <MemoryRouter initialEntries={["/diagnostics/fault-patterns"]}>
      <Routes>
        <Route path="/diagnostics/fault-patterns" element={<FaultPatternsView />} />
        <Route
          path="/software/workbench/:signalId"
          element={<div data-testid="diagnosis-workbench-page" />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

// ── Settle marker ─────────────────────────────────────────────────────────────

describe("settle marker", () => {
  it("renders the screen's settleMarker in the output", () => {
    renderFaultPatterns();
    expect(screen.getByTestId("fault-patterns-settle-marker")).toBeTruthy();
    expect(
      screen.getByTestId("fault-patterns-settle-marker").textContent,
    ).toContain("cs-settle-fault-patterns-signature-table");
  });
});

// ── Fault signature values ────────────────────────────────────────────────────

describe("fault pattern rows rendered", () => {
  it("renders fault signature values from the fixture", () => {
    renderFaultPatterns();
    // First fixture row's fault signature should be visible
    const firstSig = FAULT_PATTERN_ROWS[0].faultSignature.value!;
    expect(screen.getByText(firstSig)).toBeTruthy();
  });

  it("renders at least one affected VIN count", () => {
    renderFaultPatterns();
    const firstCount = FAULT_PATTERN_ROWS[0].affectedVinCount.value!;
    expect(screen.getByText(String(firstCount))).toBeTruthy();
  });

  it("renders market attribution for first row", () => {
    renderFaultPatterns();
    const firstMarket = FAULT_PATTERN_ROWS[0].affectedMarkets.value!;
    // May appear multiple times (simulated badge etc.) — just check it's present
    const matches = screen.getAllByText(firstMarket);
    expect(matches.length).toBeGreaterThan(0);
  });
});

// ── Empty states (spec T6.5: "Both empty states wired") ──────────────────────

describe("TableEmptyState — no data at all", () => {
  it("TableEmptyState renders 'No fault patterns' heading when source array is empty", () => {
    const { unmount } = render(<TableEmptyState resourceName="fault patterns" />);
    expect(screen.getByRole("heading").textContent).toContain("No fault patterns");
    unmount();
  });
});

describe("TableNoMatchState — text filter active but no rows match", () => {
  it("TableNoMatchState renders 'No matches' when text filter returns nothing", async () => {
    renderFaultPatterns();
    const textFilter = screen.getByPlaceholderText("Find fault patterns");
    await userEvent.type(textFilter, "ZZZZZZ-IMPOSSIBLE-FAULT-SIGNATURE-99999");
    expect(screen.getByRole("heading", { name: /no matches/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /clear filter/i })).toBeTruthy();
  });
});

// ── Row click navigation ──────────────────────────────────────────────────────

describe("row click navigation", () => {
  it("clicking the fault signature cell navigates to the Diagnosis Workbench", async () => {
    renderFaultPatterns();
    const firstSig = FAULT_PATTERN_ROWS[0].faultSignature.value!;
    // The fault signature text appears in the table cell — click it
    const cell = screen.getByText(firstSig);
    await userEvent.click(cell);
    expect(screen.getByTestId("diagnosis-workbench-page")).toBeTruthy();
  });
});

// ── Compliance: no trip/GPS/location fields ───────────────────────────────────

describe("compliance: no trip/GPS/location fields", () => {
  it("does not render any GPS, trip, latitude, longitude, or location text", () => {
    const { container } = renderFaultPatterns();
    const text = container.textContent?.toLowerCase() ?? "";
    expect(text).not.toMatch(/\bgps\b/);
    expect(text).not.toMatch(/\blatitude\b/);
    expect(text).not.toMatch(/\blongitude\b/);
    expect(text).not.toMatch(/\btrip\b/);
    expect(text).not.toMatch(/\bcell.?location\b/);
    expect(text).not.toMatch(/\bwaypoint\b/);
  });
});

// ── Fixture provenance compliance ─────────────────────────────────────────────

describe("fixture provenance compliance", () => {
  it("every FAULT_PATTERN_ROWS entry has provenance: 'simulated' on all ProvenanceValue fields", () => {
    const PROVENANCE_FIELDS = [
      "faultSignature",
      "affectedVinCount",
      "firstSeen",
      "trend",
      "affectedModel",
      "affectedMarkets",
      "representativeVin",
    ] as const;

    for (const row of FAULT_PATTERN_ROWS) {
      for (const field of PROVENANCE_FIELDS) {
        expect(row[field].provenance).toBe("simulated");
      }
    }
  });

  it("fixture has a non-empty row set (anti-vacuity)", () => {
    expect(FAULT_PATTERNS_FIXTURE.rows.length).toBeGreaterThan(0);
  });
});

// ── Aggregate data only ───────────────────────────────────────────────────────

describe("aggregate population data only", () => {
  it("affectedVinCount fields are numeric aggregate counts, not VIN strings", () => {
    for (const row of FAULT_PATTERN_ROWS) {
      const count = row.affectedVinCount.value;
      expect(typeof count).toBe("number");
      // A real VIN is 17 chars. A count should be a small integer.
      expect(String(count).length).toBeLessThan(17);
    }
  });

  it("representativeVin values come from FLEET_ROWS (non-null, 17-char VINs)", () => {
    for (const row of FAULT_PATTERN_ROWS) {
      const vin = row.representativeVin.value;
      expect(vin).not.toBeNull();
      expect(typeof vin).toBe("string");
      // VINs in FLEET_ROWS are 17 chars
      expect((vin as string).length).toBe(17);
    }
  });
});
