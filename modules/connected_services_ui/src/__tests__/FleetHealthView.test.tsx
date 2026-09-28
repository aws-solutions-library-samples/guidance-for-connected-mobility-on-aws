// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * FleetHealthView tests — v1 → v2 migration shim (T4.2).
 *
 * V1 had tests in this file against `src/components/FleetHealthView.tsx`.
 * That file has been superseded and deleted per spec T4.2.
 *
 * The canonical v2 tests live at:
 *   src/components/screens/connectivity/__tests__/FleetHealthView.test.tsx
 *
 * This file runs a subset of those tests to ensure this path (which CI or
 * tooling may reference by name) continues to pass and is not stale.
 *
 * V1 tests that are NOT carried forward:
 *   - FleetHealthRollup prop tests (v2 reads from fixture, no props)
 *   - onDenyVehicle callback tests (denial flow moved to SubscriberLookupView T4.3)
 *   - renderActionsCellContent export tests (removed in v2)
 *   - denial modal tests (moved to T4.3)
 *
 * V1 tests that ARE carried forward (as new v2 assertions):
 *   - Settle marker present
 *   - No GPS/trip/location fields (compliance control (d))
 *   - Every displayed value has provenance: 'simulated'
 */

import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import FleetHealthView from "../components/screens/connectivity/FleetHealthView";
import { FLEET_ROWS } from "../components/screens/connectivity/fleetHealth.fixture";

afterEach(cleanup);

function renderFleetHealth(path = "/connectivity/fleet-health") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/connectivity/fleet-health" element={<FleetHealthView />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("FleetHealthView (v2) — migrated v1 test shim", () => {
  it("renders the settle marker", () => {
    renderFleetHealth();
    const marker = screen.getByTestId("fleet-health-settle-marker");
    expect(marker.textContent).toContain("cs-settle-fleet-health-market-summary");
  });

  it("does not render any GPS, trip, or location fields (compliance control (d))", () => {
    const { container } = renderFleetHealth();
    const text = container.textContent?.toLowerCase() ?? "";
    expect(text).not.toMatch(/\bgps\b/);
    expect(text).not.toMatch(/\blatitude\b/);
    expect(text).not.toMatch(/\blongitude\b/);
    expect(text).not.toMatch(/\btrip\b/);
    expect(text).not.toMatch(/\bcell.?location\b/);
  });

  it("every fixture row has provenance: 'simulated' on all ProvenanceValue fields", () => {
    for (const row of FLEET_ROWS) {
      expect(row.vin.provenance).toBe("simulated");
      expect(row.market.provenance).toBe("simulated");
      expect(row.connectivityState.provenance).toBe("simulated");
      expect(row.bearer.provenance).toBe("simulated");
      expect(row.signal.provenance).toBe("simulated");
      expect(row.lastSession.provenance).toBe("simulated");
      expect(row.tcuTier.provenance).toBe("simulated");
    }
  });

  it("renders the fleet table", () => {
    renderFleetHealth();
    expect(screen.getByTestId("fleet-health-table")).toBeTruthy();
  });

  it("?state=degraded reduces row count (pre-filter contract)", () => {
    renderFleetHealth("/connectivity/fleet-health?state=degraded");
    // State chip must be visible
    expect(screen.getByTestId("state-filter-chip")).toBeTruthy();
    // At least one non-degraded VIN must not be rendered
    const nonDegradedVin = FLEET_ROWS.find(
      (r) => r.connectivityState.value !== "degraded",
    )?.vin.value;
    if (nonDegradedVin) {
      expect(screen.queryByText(nonDegradedVin)).toBeNull();
    }
  });
});
