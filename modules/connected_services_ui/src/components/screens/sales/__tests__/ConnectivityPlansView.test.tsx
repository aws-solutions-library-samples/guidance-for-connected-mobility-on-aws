// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ConnectivityPlansView tests — T6.3
 *
 * ## What this file covers
 *
 * 1. Settle marker present.
 * 2. Subscription lifecycle table renders.
 * 3. Rate Plans cross-link is present (screen cross-links, does not merge with Rate Plans).
 * 4. Renewal trend table renders.
 * 5. TableNoMatchState shown when text filter matches nothing.
 * 6. Fixture integrity: every leaf carries a valid provenance marker.
 * 7. VIN referential integrity: all VINs in subscriptions come from FLEET_ROWS.
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import ConnectivityPlansView from "../ConnectivityPlansView";
import {
  CONNECTIVITY_PLANS_FIXTURE,
  SUBSCRIPTION_ROWS,
  RENEWAL_TREND,
} from "../connectivityPlans.fixture";
import { FLEET_ROWS } from "../../connectivity/fleetHealth.fixture";
import { VALID_PROVENANCE_MARKERS } from "../../../../types";

afterEach(() => {
  cleanup();
});

// ── Render helper ─────────────────────────────────────────────────────────────

function renderConnectivityPlans() {
  return render(
    <MemoryRouter initialEntries={["/sales/connectivity-plans"]}>
      <Routes>
        <Route path="/sales/connectivity-plans" element={<ConnectivityPlansView />} />
        <Route
          path="/connectivity/rate-plans"
          element={<div data-testid="rate-plans-page" />}
        />
        <Route
          path="/compliance/consent"
          element={<div data-testid="consent-page" />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

// ── 1. Settle marker ──────────────────────────────────────────────────────────

describe("settle marker", () => {
  it("renders the screen settleMarker", () => {
    renderConnectivityPlans();
    const marker = screen.getByTestId("connectivity-plans-settle-marker");
    expect(marker).toBeTruthy();
    expect(marker.textContent).toContain("cs-settle-connectivity-plans-entitlement-table");
  });
});

// ── 2. Subscription table renders ─────────────────────────────────────────────

describe("subscription lifecycle table", () => {
  it("renders with at least one data row", () => {
    renderConnectivityPlans();
    const table = screen.getByTestId("connectivity-plans-table");
    const rows = within(table).getAllByRole("row");
    expect(rows.length).toBeGreaterThan(1);
  });
});

// ── 3. Rate Plans cross-link ──────────────────────────────────────────────────

describe("Rate Plans cross-link", () => {
  it("renders a link to /connectivity/rate-plans (does not merge with Rate Plans)", () => {
    renderConnectivityPlans();
    const link = screen.getByTestId("rate-plans-cross-link");
    expect(link).toBeTruthy();
    // The link must point to the Rate Plans path, not embed a Rate Plans table.
    expect(link.getAttribute("href")).toBe("/connectivity/rate-plans");
    // Confirm the Rate Plans pricing table is NOT embedded in this screen.
    expect(screen.queryByTestId("rate-plans-catalogue-table")).toBeNull();
  });
});

// ── 4. Renewal trend table ────────────────────────────────────────────────────

describe("renewal trend table", () => {
  it("renders with at least one data row", () => {
    renderConnectivityPlans();
    const table = screen.getByTestId("renewal-trend-table");
    const rows = within(table).getAllByRole("row");
    expect(rows.length).toBeGreaterThan(1);
  });
});

// ── 5. TableNoMatchState ──────────────────────────────────────────────────────

describe("table no-match state", () => {
  it("shows 'No matches' in the subscription table when filter matches nothing", async () => {
    const user = userEvent.setup();
    renderConnectivityPlans();
    const table = screen.getByTestId("connectivity-plans-table");
    const filterInput = within(table).getByRole("searchbox");
    await user.type(filterInput, "zzz_no_match_xyzzy");
    expect(screen.getByText("No matches")).toBeTruthy();
  });
});

// ── 6. Fixture integrity ──────────────────────────────────────────────────────

describe("fixture integrity", () => {
  it("every leaf in SUBSCRIPTION_ROWS carries a valid provenance marker", () => {
    const exemptKeys = new Set(["id"]);
    for (const row of SUBSCRIPTION_ROWS) {
      for (const [key, value] of Object.entries(row)) {
        if (exemptKeys.has(key)) continue;
        const pv = value as { value: unknown; provenance: string };
        expect(
          VALID_PROVENANCE_MARKERS.has(pv.provenance),
          `SUBSCRIPTION_ROWS key "${key}" has invalid provenance "${pv.provenance}"`,
        ).toBe(true);
      }
    }
  });

  it("every leaf in RENEWAL_TREND carries a valid provenance marker", () => {
    const exemptKeys = new Set(["id"]);
    for (const row of RENEWAL_TREND) {
      for (const [key, value] of Object.entries(row)) {
        if (exemptKeys.has(key)) continue;
        const pv = value as { value: unknown; provenance: string };
        expect(
          VALID_PROVENANCE_MARKERS.has(pv.provenance),
          `RENEWAL_TREND key "${key}" has invalid provenance "${pv.provenance}"`,
        ).toBe(true);
      }
    }
  });

  it("CONNECTIVITY_PLANS_FIXTURE is not empty", () => {
    expect(CONNECTIVITY_PLANS_FIXTURE.subscriptions.length).toBeGreaterThan(0);
    expect(CONNECTIVITY_PLANS_FIXTURE.renewalTrend.length).toBeGreaterThan(0);
  });
});

// ── 7. VIN referential integrity ──────────────────────────────────────────────

describe("VIN referential integrity", () => {
  it("every VIN in SUBSCRIPTION_ROWS comes from FLEET_ROWS (no literal VINs)", () => {
    const fleetVins = new Set(
      FLEET_ROWS.map((r) => r.vin.value).filter((v): v is string => v !== null),
    );
    for (const sub of SUBSCRIPTION_ROWS) {
      const vin = sub.vin.value;
      if (vin === null) continue;
      expect(
        fleetVins.has(vin),
        `SUBSCRIPTION_ROWS sub "${sub.id}" has VIN "${vin}" not present in FLEET_ROWS. ` +
          "VINs must come from FLEET_ROWS (fixtureReferentialIntegrity constraint).",
      ).toBe(true);
    }
  });
});
