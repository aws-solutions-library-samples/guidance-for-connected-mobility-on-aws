// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for ChargingView — T4.3 of spec 2026-09-02-cms-fleet-intelligence-v1.
 *
 * Coverage:
 *   - Reads sessions from /api/v1/charging/sessions (not hardcoded arrays)
 *   - Reads summary from /api/v1/charging/summary
 *   - Empty state when no sessions
 *   - Error state when API fails
 *   - ProvenanceBadge visible on simulated values
 *   - Stations tab shows explicit "not implemented" (not a fabricated list)
 *   - no-fabricated-records guard will verify the hardcoded arrays are gone
 */

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import ChargingView from "./ChargingView";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("../../utils/authFetch", () => ({
  authFetch: vi.fn(),
}));

vi.mock("../../config/api", () => ({
  getApiEndpoint: () => "https://api.example.com",
}));

import { authFetch } from "../../utils/authFetch";
const mockAuthFetch = vi.mocked(authFetch);

/** Sets up mock responses for the three endpoints in order they are called:
 *  summary, sessions, trend */
function mockAllEndpoints({
  summary = null,
  sessions = [],
  trend = null,
  summaryOk = true,
  sessionsOk = true,
}: {
  summary?: object | null;
  sessions?: object[];
  trend?: object | null;
  summaryOk?: boolean;
  sessionsOk?: boolean;
} = {}) {
  // summary
  mockAuthFetch.mockResolvedValueOnce({
    ok: summaryOk,
    status: summaryOk ? 200 : 500,
    json: () => Promise.resolve(summary ?? {}),
  } as unknown as Response);

  // sessions
  mockAuthFetch.mockResolvedValueOnce({
    ok: sessionsOk,
    status: sessionsOk ? 200 : 500,
    json: () =>
      Promise.resolve(
        sessionsOk ? { sessions } : {}
      ),
  } as unknown as Response);

  // trend (always ok with empty)
  mockAuthFetch.mockResolvedValueOnce({
    ok: true,
    json: () => Promise.resolve(trend ? { trend } : {}),
  } as unknown as Response);
}

const SAMPLE_SUMMARY = {
  sessionsToday: 5,
  bevVehicles: 12,
  kwhToday: 420,
  costMTD: 850,
  totalSessionsMTD: 47,
  provenance: "simulated",
};

const SAMPLE_SESSION = {
  sessionId: "CS-001",
  vehicleId: "VEH-007",
  startTime: "2025-09-24T09:30:00Z",
  energyKwh: 35.5,
  currentCharge: 72,
  targetCharge: 90,
  chargingRateKw: 22,
  cost: 8.50,
  provenance: "simulated",
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ChargingView — summary tiles", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders summary KPI tiles from the API response", async () => {
    mockAllEndpoints({ summary: SAMPLE_SUMMARY, sessions: [] });
    render(<ChargingView />);

    await waitFor(() => {
      // sessionsToday
      expect(screen.getByText("5")).toBeInTheDocument();
      // bevVehicles
      expect(screen.getByText("12")).toBeInTheDocument();
      // kwhToday
      expect(screen.getByText("420 kWh")).toBeInTheDocument();
    });
  });

  it("renders '—' when summary is loading / unavailable", async () => {
    mockAllEndpoints({ summary: null, sessionsOk: false, summaryOk: false });
    render(<ChargingView />);

    // During loading, KPIs show '—'
    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBeGreaterThan(0);
  });
});

describe("ChargingView — sessions from live API", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders session row from API response", async () => {
    mockAllEndpoints({ summary: SAMPLE_SUMMARY, sessions: [SAMPLE_SESSION] });
    render(<ChargingView />);

    await waitFor(() => {
      expect(screen.getByText("CS-001")).toBeInTheDocument();
      expect(screen.getByText("VEH-007")).toBeInTheDocument();
    });
  });

  it("calls /api/v1/charging/sessions endpoint", async () => {
    mockAllEndpoints({ summary: SAMPLE_SUMMARY, sessions: [] });
    render(<ChargingView />);

    await waitFor(() => {
      const urls = mockAuthFetch.mock.calls.map((c) => c[0] as string);
      expect(urls.some((u) => u.includes("/api/v1/charging/sessions"))).toBe(
        true
      );
    });
  });

  it("calls /api/v1/charging/summary endpoint", async () => {
    mockAllEndpoints({ summary: SAMPLE_SUMMARY, sessions: [] });
    render(<ChargingView />);

    await waitFor(() => {
      const urls = mockAuthFetch.mock.calls.map((c) => c[0] as string);
      expect(urls.some((u) => u.includes("/api/v1/charging/summary"))).toBe(
        true
      );
    });
  });

  it("renders empty state when sessions list is empty", async () => {
    mockAllEndpoints({ summary: SAMPLE_SUMMARY, sessions: [] });
    render(<ChargingView />);

    await waitFor(() => {
      expect(screen.getByText("No charging sessions")).toBeInTheDocument();
    });
  });
});

describe("ChargingView — error states", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows error alert when summary API fails", async () => {
    mockAllEndpoints({ summaryOk: false, sessions: [] });
    render(<ChargingView />);

    await waitFor(() => {
      expect(
        screen.getByText("Failed to load charging summary")
      ).toBeInTheDocument();
    });
  });

  it("shows error alert when sessions API fails", async () => {
    mockAllEndpoints({ summary: SAMPLE_SUMMARY, sessionsOk: false });
    render(<ChargingView />);

    await waitFor(() => {
      expect(
        screen.getByText("Failed to load sessions")
      ).toBeInTheDocument();
    });
  });
});

describe("ChargingView — ProvenanceBadge on simulated values", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders panel-level ProvenanceBadge for simulated data", async () => {
    mockAllEndpoints({ summary: SAMPLE_SUMMARY, sessions: [SAMPLE_SESSION] });
    render(<ChargingView />);

    await waitFor(() => {
      const panelBadge = document.querySelector(
        '[data-testid="provenance-badge-simulated"][data-variant="panel"]'
      );
      expect(panelBadge).not.toBeNull();
    });
  });

  it("renders row-level ProvenanceBadge per session cost", async () => {
    mockAllEndpoints({ summary: SAMPLE_SUMMARY, sessions: [SAMPLE_SESSION] });
    render(<ChargingView />);

    await waitFor(() => {
      const rowBadges = document.querySelectorAll(
        '[data-testid="provenance-badge-simulated"][data-variant="row"]'
      );
      expect(rowBadges.length).toBeGreaterThan(0);
    });
  });

  it("badges contain visible text (not tooltip-only)", async () => {
    mockAllEndpoints({ summary: SAMPLE_SUMMARY, sessions: [SAMPLE_SESSION] });
    render(<ChargingView />);

    await waitFor(() => {
      const badges = document.querySelectorAll(
        '[data-testid="provenance-badge-simulated"]'
      );
      badges.forEach((b) => {
        expect(b.textContent).toContain("Simulated");
      });
    });
  });
});

describe("ChargingView — station infrastructure tab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("stations tab shows explicit not-implemented message (not fabricated data)", async () => {
    mockAllEndpoints({ summary: SAMPLE_SUMMARY, sessions: [] });
    render(<ChargingView />);

    // Wait for initial load
    await waitFor(() => {
      expect(screen.getByText("No charging sessions")).toBeInTheDocument();
    });

    // Click the Charging Infrastructure tab
    const stationsTab = screen.getByText("Charging Infrastructure");
    fireEvent.click(stationsTab);

    await waitFor(() => {
      const placeholder = document.querySelector(
        '[data-testid="stations-not-implemented"]'
      );
      expect(placeholder).not.toBeNull();
    });
  });
});
