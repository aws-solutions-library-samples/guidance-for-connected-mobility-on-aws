// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * HomologationView tests — T6.1
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.1
 *
 * ## What this file covers
 *
 * 1. Settle marker present in rendered output.
 * 2. Table renders with rows from the fixture.
 * 3. RXSWIN references are rendered (plain ProvenanceField, not evidence[]).
 * 4. Type-approval status indicators render correctly.
 * 5. TableNoMatchState shown when text filter matches nothing.
 * 6. Fixture integrity: every leaf carries a valid provenance marker.
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import HomologationView from "../HomologationView";
import {
  HOMOLOGATION_FIXTURE,
  HOMOLOGATION_ROWS,
} from "../homologation.fixture";
import { VALID_PROVENANCE_MARKERS } from "../../../../types";

afterEach(() => {
  cleanup();
});

// ── Render helper ─────────────────────────────────────────────────────────────

function renderHomologation() {
  return render(
    <MemoryRouter initialEntries={["/manufacturing/homologation"]}>
      <Routes>
        <Route
          path="/manufacturing/homologation"
          element={<HomologationView />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

// ── 1. Settle marker ─────────────────────────────────────────────────────────

describe("settle marker", () => {
  it("renders the screen settleMarker in the output", () => {
    renderHomologation();
    const marker = screen.getByTestId("homologation-settle-marker");
    expect(marker).toBeTruthy();
    expect(marker.textContent).toContain("cs-settle-homologation-rxswin-registry");
  });
});

// ── 2. Table renders ─────────────────────────────────────────────────────────

describe("table renders", () => {
  it("renders the homologation table", () => {
    renderHomologation();
    expect(screen.getByTestId("homologation-table")).toBeTruthy();
  });

  it("renders at least one model variant from the fixture", () => {
    renderHomologation();
    // The first model variant ("Sedan-A / TCU2-LTE") appears in multiple rows
    // (same model variant, different markets). Use getAllByText since it's
    // intentionally repeated across market rows.
    const firstVariant = HOMOLOGATION_ROWS[0].modelVariant.value!;
    const matches = screen.getAllByText(firstVariant);
    expect(matches.length).toBeGreaterThan(0);
  });

  it("renders the correct total row count in the header counter", () => {
    renderHomologation();
    const total = HOMOLOGATION_FIXTURE.rows.length;
    const header = screen.getByTestId("homologation-table-header");
    expect(header.textContent).toContain(`(${total})`);
  });
});

// ── 3. RXSWIN references ──────────────────────────────────────────────────────

describe("RXSWIN references", () => {
  it("renders RXSWIN references from the fixture", () => {
    renderHomologation();
    const firstRxswin = HOMOLOGATION_ROWS[0].rxswinReference.value!;
    // RXSWIN is rendered as a plain ProvenanceField — its text is visible.
    expect(screen.getByText(firstRxswin)).toBeTruthy();
  });

  it("renders multiple distinct RXSWIN references", () => {
    renderHomologation();
    // The fixture has 10 rows with unique RXSWIN values.
    const firstRxswin = HOMOLOGATION_ROWS[0].rxswinReference.value!;
    const secondRxswin = HOMOLOGATION_ROWS[1].rxswinReference.value!;
    expect(firstRxswin).not.toBe(secondRxswin);
    expect(screen.getByText(firstRxswin)).toBeTruthy();
    expect(screen.getByText(secondRxswin)).toBeTruthy();
  });
});

// ── 4. Type-approval status indicators ───────────────────────────────────────

describe("type-approval status indicators", () => {
  it("renders at least one 'Approved' status indicator from the fixture", () => {
    renderHomologation();
    // fixture has multiple 'approved' rows — at least one should be visible.
    const approvedItems = screen.getAllByText("Approved");
    expect(approvedItems.length).toBeGreaterThan(0);
  });

  it("renders 'Pending' status indicator for pending rows", () => {
    renderHomologation();
    const pendingItems = screen.getAllByText("Pending");
    expect(pendingItems.length).toBeGreaterThan(0);
  });
});

// ── 5. TableNoMatchState ──────────────────────────────────────────────────────

describe("TableNoMatchState", () => {
  it("renders 'No matches' when text filter matches nothing", async () => {
    const user = userEvent.setup();
    renderHomologation();

    const table = screen.getByTestId("homologation-table");
    const filterInput = within(table).getByRole("searchbox");
    await user.type(filterInput, "zzz_no_match_xyzzy");

    expect(screen.getByText("No matches")).toBeTruthy();
  });
});

// ── 6. Fixture integrity ──────────────────────────────────────────────────────

describe("fixture integrity", () => {
  const EXEMPT_KEYS = new Set(["id"]);

  it("every leaf in HOMOLOGATION_ROWS carries a valid provenance marker", () => {
    for (const row of HOMOLOGATION_ROWS) {
      for (const [key, value] of Object.entries(row)) {
        if (EXEMPT_KEYS.has(key)) continue;
        const pv = value as { value: unknown; provenance: string };
        expect(
          VALID_PROVENANCE_MARKERS.has(pv.provenance),
          `HOMOLOGATION_ROWS key "${key}" has invalid provenance "${pv.provenance}"`,
        ).toBe(true);
      }
    }
  });

  it("HOMOLOGATION_FIXTURE is not empty", () => {
    expect(HOMOLOGATION_FIXTURE.rows.length).toBeGreaterThan(0);
  });

  it("all RXSWIN values in the fixture are unique", () => {
    const rxswinValues = HOMOLOGATION_ROWS.map((r) => r.rxswinReference.value);
    const unique = new Set(rxswinValues);
    expect(unique.size).toBe(rxswinValues.length);
  });
});
