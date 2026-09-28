// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Tests for useAlertsFleetFilter's wiring to FleetFilterContext.
//
// Why this file exists: four surfaces consume this hook (dashboard, vehicle
// map, fleet vehicle map, safety alerts) and every one of them silently
// ignored the top-nav fleet picker, because the hook used to hold its own
// local useState. The property under test is therefore NOT "the hook returns
// a fleet id" — it is "the hook is the SAME state as the top-nav picker, in
// both directions, with the sentinel translated".
//
// Sentinel translation is the subtle part: callers compare against "all",
// FleetFilterContext stores ALL_FLEETS_ID ("__all__"). A regression that
// leaked "__all__" out of this hook would make `isAllFleets` false and every
// consumer would filter its list down to zero rows.

import React from "react";
import { render, act } from "@testing-library/react";
import { describe, it, expect, beforeEach, vi } from "vitest";

import { useAlertsFleetFilter } from "./AlertsFleetFilter";
import { FleetFilterProvider, useFleetFilter } from "../fleet-filter/FleetFilter";
import { ALL_FLEETS_ID } from "../fleet-picker/useFleetSelection";

// The hook fetches /api/v1/fleets on mount purely to populate dropdown
// options. Stub it so these tests exercise the wiring, not the network.
vi.mock("../../config/api", () => ({
  getRuntimeConfig: () => ({ apiEndpoint: "https://api.example.invalid/" }),
}));
vi.mock("../../utils/authFetch", () => ({
  authFetch: vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      fleets: [
        { fleetId: "FLEET-MERIDIAN-OFFBOARD", name: "Meridian Offboard Fleet" },
        { fleetId: "FLEET-MERIDIAN-ONBOARD", name: "Meridian Onboard Fleet" },
      ],
    }),
  }),
}));

// FleetFilterProvider reads useUserRole (2026-09-26 — follow-on 1 to the FI
// auth fix). Mock as platform-admin (cross-fleet) so today's ALL_FLEETS_ID
// default stands and this file's pre-existing assertions still hold.
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

const SESSION_KEY = "cms-fleet-filter-id";

interface Captured {
  selectedFleet: string;
  isAllFleets: boolean;
  selectedFleetName: string;
  handleFleetChange: (fleetId: string, fleetName?: string) => void;
}

/** Renders the hook plus a direct context probe so both sides are observable. */
function renderHarness() {
  const hook: Captured[] = [];
  const contextIds: string[] = [];
  let setContext: ((id: string) => void) | null = null;

  const HookProbe: React.FC = () => {
    const r = useAlertsFleetFilter();
    hook.push({
      selectedFleet: r.selectedFleet,
      isAllFleets: r.isAllFleets,
      selectedFleetName: r.selectedFleetName,
      handleFleetChange: r.handleFleetChange,
    });
    return null;
  };

  const ContextProbe: React.FC = () => {
    const { selectedFleetId, setSelectedFleetId } = useFleetFilter();
    contextIds.push(selectedFleetId);
    setContext = setSelectedFleetId;
    return null;
  };

  render(
    <FleetFilterProvider>
      <HookProbe />
      <ContextProbe />
    </FleetFilterProvider>
  );

  return {
    lastHook: () => hook[hook.length - 1],
    lastContextId: () => contextIds[contextIds.length - 1],
    setContext: (id: string) => act(() => setContext?.(id)),
  };
}

describe("useAlertsFleetFilter <-> FleetFilterContext wiring", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });

  // ---- context -> hook (the direction the reported bug was in) ----

  it("reflects a fleet selected on the context (top-nav picker)", () => {
    const h = renderHarness();
    expect(h.lastHook().selectedFleet).toBe("all");

    h.setContext("FLEET-MERIDIAN-OFFBOARD");

    expect(h.lastHook().selectedFleet).toBe("FLEET-MERIDIAN-OFFBOARD");
    expect(h.lastHook().isAllFleets).toBe(false);
  });

  it("picks up a fleet already stored in session before mount", () => {
    sessionStorage.setItem(SESSION_KEY, "FLEET-MERIDIAN-ONBOARD");
    const h = renderHarness();
    expect(h.lastHook().selectedFleet).toBe("FLEET-MERIDIAN-ONBOARD");
    expect(h.lastHook().isAllFleets).toBe(false);
  });

  // ---- sentinel translation ----

  it("translates the context ALL sentinel to \"all\" and never leaks __all__", () => {
    const h = renderHarness();
    // Context holds ALL_FLEETS_ID...
    expect(h.lastContextId()).toBe(ALL_FLEETS_ID);
    // ...but consumers must see "all", or isAllFleets goes false and every
    // consuming list filters down to zero rows.
    expect(h.lastHook().selectedFleet).toBe("all");
    expect(h.lastHook().selectedFleet).not.toBe(ALL_FLEETS_ID);
    expect(h.lastHook().isAllFleets).toBe(true);
  });

  it("translates \"all\" back to the context sentinel on write", () => {
    const h = renderHarness();
    h.setContext("FLEET-MERIDIAN-OFFBOARD");
    expect(h.lastHook().isAllFleets).toBe(false);

    act(() => h.lastHook().handleFleetChange("all", "All Fleets"));

    expect(h.lastContextId()).toBe(ALL_FLEETS_ID);
    expect(h.lastHook().selectedFleet).toBe("all");
    expect(h.lastHook().isAllFleets).toBe(true);
  });

  // ---- hook -> context (in-page dropdown drives the shared selection) ----

  it("writes a fleet change back to the shared context", () => {
    const h = renderHarness();
    act(() => h.lastHook().handleFleetChange("FLEET-MERIDIAN-ONBOARD", "Meridian Onboard Fleet"));

    expect(h.lastContextId()).toBe("FLEET-MERIDIAN-ONBOARD");
    expect(h.lastHook().selectedFleet).toBe("FLEET-MERIDIAN-ONBOARD");
    expect(sessionStorage.getItem(SESSION_KEY)).toBe("FLEET-MERIDIAN-ONBOARD");
  });

  it("does not keep private state that can diverge from the context", () => {
    // Guards the specific regression: a hook-local useState shadowing the
    // context would survive an external context change.
    const h = renderHarness();
    act(() => h.lastHook().handleFleetChange("FLEET-MERIDIAN-ONBOARD"));
    expect(h.lastHook().selectedFleet).toBe("FLEET-MERIDIAN-ONBOARD");

    // An external change (top-nav picker) must win, not be shadowed.
    h.setContext("FLEET-MERIDIAN-OFFBOARD");
    expect(h.lastHook().selectedFleet).toBe("FLEET-MERIDIAN-OFFBOARD");
  });
});
