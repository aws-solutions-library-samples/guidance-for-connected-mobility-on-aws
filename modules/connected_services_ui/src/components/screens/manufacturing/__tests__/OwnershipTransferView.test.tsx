// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * OwnershipTransferView tests — T6.2
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.2
 *
 * ## What this file covers
 *
 * 1. Settle marker present in rendered output.
 * 2. Transfer requests table renders rows from the fixture.
 * 3. Subscription carryover effect statement renders.
 * 4. TableNoMatchState shown when text filter matches nothing.
 * 5. Fixture integrity: every leaf carries a valid provenance marker.
 * 6. VINs come from fleet-health fixture constants (not literals).
 * 7. Pool values are 'production' or 'in_transit' — not 'dealer_lot'.
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import OwnershipTransferView, { SUBSCRIPTION_CARRYOVER_LABELS } from "../OwnershipTransferView";
import {
  OWNERSHIP_TRANSFER_FIXTURE,
  OWNERSHIP_TRANSFER_ROWS,
} from "../ownershipTransfer.fixture";
import { FLEET_ROWS } from "../../connectivity/fleetHealth.fixture";
import { VALID_PROVENANCE_MARKERS } from "../../../../types";

afterEach(() => {
  cleanup();
});

// ── Render helper ─────────────────────────────────────────────────────────────

function renderOwnershipTransfer() {
  return render(
    <MemoryRouter initialEntries={["/manufacturing/ownership-transfer"]}>
      <Routes>
        <Route
          path="/manufacturing/ownership-transfer"
          element={<OwnershipTransferView />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

// ── 1. Settle marker ──────────────────────────────────────────────────────────

describe("settle marker", () => {
  it("renders the screen settleMarker in the output", () => {
    renderOwnershipTransfer();
    const marker = screen.getByTestId("cs-ownership-transfer-settle-marker");
    expect(marker).toBeTruthy();
    expect(marker.textContent).toContain(
      "cs-settle-ownership-transfer-request-queue",
    );
  });
});

// ── 2. Table renders rows ─────────────────────────────────────────────────────

describe("transfer requests table", () => {
  it("renders the transfer table", () => {
    renderOwnershipTransfer();
    expect(screen.getByTestId("cs-transfer-table")).toBeTruthy();
  });

  it("renders at least one VIN from the fixture rows", () => {
    renderOwnershipTransfer();
    const firstVin = OWNERSHIP_TRANSFER_ROWS[0].vin.value!;
    expect(screen.getByText(firstVin)).toBeTruthy();
  });

  it("renders the correct total row count in the header counter", () => {
    renderOwnershipTransfer();
    const total = OWNERSHIP_TRANSFER_FIXTURE.rows.length;
    const header = screen.getByTestId("cs-transfer-table-header");
    expect(header.textContent).toContain(`(${total})`);
  });
});

// ── 3. Subscription carryover effect ─────────────────────────────────────────

describe("subscription carryover effect statement", () => {
  it("renders a carryover effect description for the default selection", () => {
    renderOwnershipTransfer();
    const effectEl = screen.getByTestId("cs-transfer-form-carryover-effect");
    // Default is 'transfer'
    const expectedEffect = SUBSCRIPTION_CARRYOVER_LABELS["transfer"];
    expect(effectEl.textContent).toContain(expectedEffect.slice(0, 20));
  });
});

// ── 4. TableNoMatchState ──────────────────────────────────────────────────────

describe("TableNoMatchState", () => {
  it("renders 'No matches' when text filter matches nothing", async () => {
    const user = userEvent.setup();
    renderOwnershipTransfer();

    const table = screen.getByTestId("cs-transfer-table");
    const filterInput = within(table).getByRole("searchbox");
    await user.type(filterInput, "zzz_no_match_xyzzy");

    expect(screen.getByText("No matches")).toBeTruthy();
  });
});

// ── 5. Fixture integrity ──────────────────────────────────────────────────────

describe("fixture integrity", () => {
  const EXEMPT_KEYS = new Set(["id"]);

  it("every leaf in OWNERSHIP_TRANSFER_ROWS carries a valid provenance marker", () => {
    for (const row of OWNERSHIP_TRANSFER_ROWS) {
      for (const [key, value] of Object.entries(row)) {
        if (EXEMPT_KEYS.has(key)) continue;
        const pv = value as { value: unknown; provenance: string };
        expect(
          VALID_PROVENANCE_MARKERS.has(pv.provenance),
          `OWNERSHIP_TRANSFER_ROWS key "${key}" has invalid provenance "${pv.provenance}"`,
        ).toBe(true);
      }
    }
  });

  it("OWNERSHIP_TRANSFER_FIXTURE is not empty", () => {
    expect(OWNERSHIP_TRANSFER_FIXTURE.rows.length).toBeGreaterThan(0);
  });
});

// ── 6. VINs from fleet-health fixture ────────────────────────────────────────

describe("VIN provenance — no literals", () => {
  it("VINs in the transfer fixture match fleet-health FLEET_ROWS constants", () => {
    const fleetVins = new Set(
      FLEET_ROWS.map((r) => r.vin.value).filter(Boolean),
    );
    const transferVins = OWNERSHIP_TRANSFER_ROWS.map((r) => r.vin.value);

    // Every transfer VIN should be traceable to a fleet-health row.
    for (const vin of transferVins) {
      if (vin == null) continue;
      expect(
        fleetVins.has(vin),
        `Transfer VIN "${vin}" not found in FLEET_ROWS — must come from fleetHealth.fixture constants`,
      ).toBe(true);
    }
  });
});

// ── 7. Pool values — not 'dealer_lot' ────────────────────────────────────────

describe("inventory pool values", () => {
  it("every inventoryPool value is 'production' or 'in_transit', never 'dealer_lot'", () => {
    const allowedPools = new Set<string>(["production", "in_transit"]);
    for (const row of OWNERSHIP_TRANSFER_ROWS) {
      const pool = row.inventoryPool.value;
      expect(
        allowedPools.has(pool!),
        `Row "${row.id}" has inventoryPool "${pool}" — must be 'production' or 'in_transit', not 'dealer_lot' or other value`,
      ).toBe(true);
    }
  });
});
