// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * T6.3b — FleetCostDashboard passes the ambient fleet selection to CpmOemView.
 *
 * Spec: .kiro/specs/2026-09-10-cms-fleet-intelligence-adp-consumer/tasks.md T6.3b
 *
 * CpmOemView already accepted and sent a `fleetId` prop before Group 6 (see
 * CpmOemView.test.tsx "includes fleetId query param when provided"), and the
 * backend already received the param — it just ignored it. The only missing
 * wire was the dashboard passing the selector's value down. These tests pin
 * that wire and, specifically, the ALL_FLEETS_ID mapping.
 *
 * Why the sentinel case is the load-bearing one: ALL_FLEETS_ID is the literal
 * string '__all__'. If it were forwarded as a fleetId the backend would match
 * no vehicle and return an empty result, so the Cost page would render empty
 * on the DEFAULT selector state — a silent failure that looks like missing ADP
 * data rather than a mapping bug.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../utils/authFetch", () => ({ authFetch: vi.fn() }));
vi.mock("../../config/api", () => ({
  getApiEndpoint: () => "https://api.example.com",
  isDemoMode: () => false,
}));

// FleetFilterProvider reads useUserRole (2026-09-26 — follow-on 1 to the FI
// auth fix). Mock as platform-admin (cross-fleet) so today's ALL_FLEETS_ID
// default stands and this file's pre-existing ALL_FLEETS_ID / portal-wide
// assertions still hold.
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

import { authFetch } from "../../utils/authFetch";
import FleetCostDashboard from "./FleetCostDashboard";
import { FleetFilterProvider } from "../fleet-filter/FleetFilter";
import { ALL_FLEETS_ID } from "../fleet-picker/useFleetSelection";

const mockAuthFetch = vi.mocked(authFetch);

const SESSION_KEY = "cms-fleet-filter-id";

/** Every endpoint the dashboard touches answers with an empty-but-valid body. */
function stubAllEndpoints() {
  mockAuthFetch.mockResolvedValue({
    ok: true,
    status: 200,
    json: () =>
      Promise.resolve({
        groupBy: "oem",
        data: [],
        provenance: "simulated",
        breakdown: {},
      }),
  } as unknown as Response);
}

/** The CPM request URL, or undefined if the dashboard never issued one. */
async function cpmRequestUrl(): Promise<string | undefined> {
  await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled());
  const call = mockAuthFetch.mock.calls
    .map((c) => c[0] as string)
    .find((u) => u.includes("/api/v1/fleet-intelligence/cpm"));
  return call;
}

describe("FleetCostDashboard — T6.3b fleet scope wiring", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    stubAllEndpoints();
  });

  it("forwards a real selected fleetId to the CPM request", async () => {
    sessionStorage.setItem(SESSION_KEY, "flt-meridian-range-001");

    render(
      <FleetFilterProvider>
        <FleetCostDashboard />
      </FleetFilterProvider>
    );

    const url = await cpmRequestUrl();
    expect(url).toBeDefined();
    expect(url).toContain("fleetId=flt-meridian-range-001");
  });

  it("omits fleetId entirely when the selector is on All my fleets", async () => {
    sessionStorage.setItem(SESSION_KEY, ALL_FLEETS_ID);

    render(
      <FleetFilterProvider>
        <FleetCostDashboard />
      </FleetFilterProvider>
    );

    const url = await cpmRequestUrl();
    expect(url).toBeDefined();
    // Not merely "does not equal the sentinel" — the param must be absent, so
    // the backend takes its single no-filter branch rather than looking up a
    // fleet literally named '__all__'.
    expect(url).not.toContain("fleetId");
    expect(url).not.toContain(ALL_FLEETS_ID);
  });

  it("omits fleetId when no selection has ever been made", async () => {
    // No sessionStorage value — the provider defaults to ALL_FLEETS_ID. This is
    // the first-visit path and must behave as portal-wide, matching the
    // pre-Group-6 behaviour exactly.
    render(
      <FleetFilterProvider>
        <FleetCostDashboard />
      </FleetFilterProvider>
    );

    const url = await cpmRequestUrl();
    expect(url).toBeDefined();
    expect(url).not.toContain("fleetId");
  });

  it("omits fleetId when rendered with no FleetFilterProvider mounted", async () => {
    // The context default is ALL_FLEETS_ID, so an unwrapped render must fail
    // safe to portal-wide rather than throwing or sending the sentinel. Pinned
    // because the dashboard is reachable from routes that may not sit inside
    // the provider.
    render(<FleetCostDashboard />);

    const url = await cpmRequestUrl();
    expect(url).toBeDefined();
    expect(url).not.toContain("fleetId");
  });
});
