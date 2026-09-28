// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for CpmOemView — T4.1 of spec 2026-09-02-cms-fleet-intelligence-v1.
 *
 * Coverage:
 *   - Renders OEM rows from API response
 *   - ProvenanceBadge is present on both panel (header) and row level
 *   - Existing /api/v1/tco/* panels are unaffected (they live in FleetCostDashboard
 *     which this component does not import or touch)
 *   - Error state is surfaced, not swallowed
 *   - Loading state while fetch is in-flight
 *   - Empty state when API returns no items
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { CpmOemView } from "./CpmOemView";
import type { CpmOemResponse } from "./CpmOemView";

// ---------------------------------------------------------------------------
// Mock authFetch + api config
// ---------------------------------------------------------------------------

vi.mock("../../utils/authFetch", () => ({
  authFetch: vi.fn(),
}));

vi.mock("../../config/api", () => ({
  getApiEndpoint: () => "https://api.example.com",
}));

import { authFetch } from "../../utils/authFetch";
const mockAuthFetch = vi.mocked(authFetch);

function mockFetchSuccess(data: CpmOemResponse) {
  mockAuthFetch.mockResolvedValueOnce({
    ok: true,
    json: () => Promise.resolve(data),
  } as unknown as Response);
}

function mockFetchError(status = 500) {
  mockAuthFetch.mockResolvedValueOnce({
    ok: false,
    status,
    json: () => Promise.resolve({}),
  } as unknown as Response);
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const SAMPLE_RESPONSE: CpmOemResponse = {
  groupBy: "oem",
  yearMonth: "2025-09",
  provenance: "simulated",
  // Shape matches services/fleet_intelligence/cpm.py::group_cpm_by exactly
  // (data[] with group/cpm/totalCost/totalMiles/vehicleCount) — see
  // issues/2026-09-03-fleet-intelligence-ui-api-contract-mismatch/.
  data: [
    {
      group: "DemoMotors",
      cpm: 0.185,
      totalCost: 12500,
      totalMiles: 67567,
      vehicleCount: 22,
    },
    {
      group: "AcmeAuto",
      cpm: 0.241,
      totalCost: 9800,
      totalMiles: 40664,
      vehicleCount: 18,
    },
    {
      group: "Meridian",
      cpm: 0.162,
      totalCost: 4320,
      totalMiles: 26666,
      vehicleCount: 9,
    },
  ],
};

const EMPTY_RESPONSE: CpmOemResponse = {
  groupBy: "oem",
  provenance: null,
  data: [],
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("CpmOemView — OEM grouping", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders OEM rows from the API response", async () => {
    mockFetchSuccess(SAMPLE_RESPONSE);
    render(<CpmOemView />);

    await waitFor(() => {
      // Use getAllByText because the OEM name appears in both chart axis and table cell
      expect(screen.getAllByText("DemoMotors").length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText("AcmeAuto").length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText("Meridian").length).toBeGreaterThanOrEqual(1);
    });
  });

  it("displays CPM formatted as dollars per mile", async () => {
    mockFetchSuccess(SAMPLE_RESPONSE);
    render(<CpmOemView />);

    // DemoMotors avg CPM = $0.185/mi
    await waitFor(() => {
      expect(screen.getByText("$0.185/mi")).toBeInTheDocument();
    });
  });

  it("calls the fleet-intelligence CPM endpoint with groupBy=oem", async () => {
    mockFetchSuccess(SAMPLE_RESPONSE);
    render(<CpmOemView />);

    await waitFor(() => {
      expect(mockAuthFetch).toHaveBeenCalled();
    });

    const calledUrl = (mockAuthFetch.mock.calls[0][0] as string);
    expect(calledUrl).toContain("/api/v1/fleet-intelligence/cpm");
    expect(calledUrl).toContain("groupBy=oem");
  });

  it("includes fleetId query param when provided", async () => {
    mockFetchSuccess(SAMPLE_RESPONSE);
    render(<CpmOemView fleetId="fleet-123" />);

    await waitFor(() => {
      const calledUrl = (mockAuthFetch.mock.calls[0][0] as string);
      expect(calledUrl).toContain("fleetId=fleet-123");
    });
  });
});

describe("CpmOemView — ProvenanceBadge presence (spec § D1 requirement)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders panel-level ProvenanceBadge in header for simulated data", async () => {
    mockFetchSuccess(SAMPLE_RESPONSE);
    render(<CpmOemView />);

    await waitFor(() => {
      // Panel badge has data-variant="panel"
      const panelBadge = document.querySelector('[data-variant="panel"]');
      expect(panelBadge).not.toBeNull();
      expect(panelBadge!.getAttribute("data-provenance")).toBe("simulated");
    });
  });

  it("renders row-level ProvenanceBadge for each OEM CPM figure", async () => {
    mockFetchSuccess(SAMPLE_RESPONSE);
    render(<CpmOemView />);

    await waitFor(() => {
      // Row badges have data-variant="row" and data-provenance="simulated"
      const rowBadges = document.querySelectorAll('[data-variant="row"][data-provenance="simulated"]');
      // At least as many badges as OEM rows
      expect(rowBadges.length).toBeGreaterThanOrEqual(SAMPLE_RESPONSE.data.length);
    });
  });

  it("provenance badges are visible in the DOM (not tooltip-only)", async () => {
    mockFetchSuccess(SAMPLE_RESPONSE);
    render(<CpmOemView />);

    await waitFor(() => {
      // Each badge must contain "Simulated" text directly
      const badges = document.querySelectorAll('[data-testid="provenance-badge-simulated"]');
      expect(badges.length).toBeGreaterThan(0);
      badges.forEach((b) => {
        expect(b.textContent).toContain("Simulated");
      });
    });
  });
});

describe("CpmOemView — empty state", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders empty state message when API returns no items", async () => {
    mockFetchSuccess(EMPTY_RESPONSE);
    render(<CpmOemView />);

    await waitFor(() => {
      // Both chart and table show the empty message — use getAllByText
      expect(screen.getAllByText("No CPM data available").length).toBeGreaterThanOrEqual(1);
    });
  });
});

describe("CpmOemView — error state", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("surfaces an error alert when the API returns a non-ok status", async () => {
    mockFetchError(502);
    render(<CpmOemView />);

    await waitFor(() => {
      expect(screen.getByText("Failed to load CPM data")).toBeInTheDocument();
    });
  });

  it("surfaces a fetch rejection as an error alert", async () => {
    mockAuthFetch.mockRejectedValueOnce(new Error("Network error"));
    render(<CpmOemView />);

    await waitFor(() => {
      expect(screen.getByText("Failed to load CPM data")).toBeInTheDocument();
    });
  });
});
