// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Tests for FleetFilterProvider + useFleetFilter — the ambient fleet-selection
// state that lives in sessionStorage. The picker itself is tested where it
// lives (FleetPicker.test.tsx + useFleetPickerUtility). This file covers the
// properties that matter for the Provider: session-scoped default,
// restore-from-storage, setSelectedFleetId writing back, and (2026-09-26)
// role-aware defaults + stored-sentinel replacement, matching
// `services/fleet_intelligence/_auth.py`.

import React from "react";
import { render, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

// `FleetFilterProvider` now reads `useUserRole` for its default. Without a
// mock, `useUserRole` calls `useAuth` → `useSimpleAuth`, which throws outside
// a `SimpleAuthProvider`. Default the mock to platform-admin (cross-fleet) so
// the pre-2026-09-26 assertions still hold.
vi.mock("@/auth/useUserRole", () => ({ useUserRole: vi.fn() }));

import { FleetFilterProvider, useFleetFilter } from "./FleetFilter";
import { ALL_FLEETS_ID } from "../fleet-picker/useFleetSelection";
import { useUserRole } from "@/auth/useUserRole";

const mockUseUserRole = vi.mocked(useUserRole);

type Role = ReturnType<typeof useUserRole>;
const makeRole = (overrides: Partial<Role> = {}): Role => ({
  isAdmin: false,
  isOperator: false,
  isViewer: false,
  isGuest: false,
  isConnectAgent: false,
  isEngineer: false,
  isDispatcher: false,
  canWrite: false,
  fleetIds: [],
  ...overrides,
});

const SESSION_KEY = "cms-fleet-filter-id";

describe("FleetFilterProvider — cross-fleet role (regression guard)", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    mockUseUserRole.mockReset();
    // Default: platform-admin — cross-fleet, keeps the pre-2026-09-26
    // assertions valid.
    mockUseUserRole.mockReturnValue(makeRole({ isAdmin: true, canWrite: true }));
  });

  it("defaults to ALL_FLEETS_ID when nothing is stored", () => {
    const seen: string[] = [];
    const Probe: React.FC = () => {
      seen.push(useFleetFilter().selectedFleetId);
      return null;
    };
    render(
      <FleetFilterProvider>
        <Probe />
      </FleetFilterProvider>
    );
    expect(seen[0]).toBe(ALL_FLEETS_ID);
  });

  it("restores a stored selection on mount", () => {
    sessionStorage.setItem(SESSION_KEY, "fleet-z");
    const seen: string[] = [];
    const Probe: React.FC = () => {
      seen.push(useFleetFilter().selectedFleetId);
      return null;
    };
    render(
      <FleetFilterProvider>
        <Probe />
      </FleetFilterProvider>
    );
    expect(seen[0]).toBe("fleet-z");
  });

  it("writes to sessionStorage, not localStorage, when setSelectedFleetId is called", () => {
    // Session-scoped by design: fleet selection is ephemeral context, not a
    // sticky preference across browser sessions.
    let setter: ((id: string) => void) | null = null;
    const Probe: React.FC = () => {
      const { setSelectedFleetId } = useFleetFilter();
      setter = setSelectedFleetId;
      return null;
    };
    render(
      <FleetFilterProvider>
        <Probe />
      </FleetFilterProvider>
    );
    act(() => {
      setter?.("fleet-b");
    });
    expect(sessionStorage.getItem(SESSION_KEY)).toBe("fleet-b");
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
  });

  it("propagates the new selection to consumers after set", () => {
    let setter: ((id: string) => void) | null = null;
    const seen: string[] = [];
    const Probe: React.FC = () => {
      const { selectedFleetId, setSelectedFleetId } = useFleetFilter();
      setter = setSelectedFleetId;
      seen.push(selectedFleetId);
      return null;
    };
    render(
      <FleetFilterProvider>
        <Probe />
      </FleetFilterProvider>
    );
    expect(seen[seen.length - 1]).toBe(ALL_FLEETS_ID);
    act(() => {
      setter?.("fleet-x");
    });
    expect(seen[seen.length - 1]).toBe("fleet-x");
  });
});

// ── Role-awareness (2026-09-26 — follow-on 1 to the FI auth fix)
//
// The FleetFilterProvider drives the fleetId sent to /api/v1/fleet-intelligence
// via useFleetFilter().selectedFleetId. If a scoped caller starts with
// ALL_FLEETS_ID, the FI dashboard's first request is portal-wide and the
// backend now 403s. Same rule as useFleetSelection: scoped roles default to
// their first custom:fleetIds entry; a stored ALL is replaced; a stored
// not-owned fleet is replaced; a scoped caller with no fleets sees an empty
// state, not an error.
describe("FleetFilterProvider — scoped role: default is first custom:fleetIds entry", () => {
  beforeEach(() => {
    sessionStorage.clear();
    mockUseUserRole.mockReset();
  });

  it("fleet-operator defaults to its first owned fleet", () => {
    mockUseUserRole.mockReturnValue(
      makeRole({
        isOperator: true,
        canWrite: true,
        fleetIds: ["fleet-op-A", "fleet-op-B"],
      })
    );
    const seen: string[] = [];
    const Probe: React.FC = () => {
      seen.push(useFleetFilter().selectedFleetId);
      return null;
    };
    render(
      <FleetFilterProvider>
        <Probe />
      </FleetFilterProvider>
    );
    expect(seen[0]).toBe("fleet-op-A");
    expect(seen[0]).not.toBe(ALL_FLEETS_ID);
  });

  it("fleet-guest defaults to its first owned fleet", () => {
    mockUseUserRole.mockReturnValue(
      makeRole({ isGuest: true, fleetIds: ["guest-only-fleet"] })
    );
    const seen: string[] = [];
    const Probe: React.FC = () => {
      seen.push(useFleetFilter().selectedFleetId);
      return null;
    };
    render(
      <FleetFilterProvider>
        <Probe />
      </FleetFilterProvider>
    );
    expect(seen[0]).toBe("guest-only-fleet");
  });

  it("dispatcher (read-only monitoring persona) also defaults to its first fleet", () => {
    mockUseUserRole.mockReturnValue(
      makeRole({ isDispatcher: true, fleetIds: ["disp-X", "disp-Y"] })
    );
    const seen: string[] = [];
    const Probe: React.FC = () => {
      seen.push(useFleetFilter().selectedFleetId);
      return null;
    };
    render(
      <FleetFilterProvider>
        <Probe />
      </FleetFilterProvider>
    );
    expect(seen[0]).toBe("disp-X");
  });
});

describe("FleetFilterProvider — scoped role: stored ALL_FLEETS_ID selection is replaced", () => {
  beforeEach(() => {
    sessionStorage.clear();
    mockUseUserRole.mockReset();
  });

  it("replaces sessionStorage ALL_FLEETS_ID sentinel with fleetIds[0]", () => {
    // Simulate a scoped caller whose sessionStorage was set before this fix
    // landed (the pre-fix default was ALL_FLEETS_ID for every role).
    sessionStorage.setItem(SESSION_KEY, ALL_FLEETS_ID);
    mockUseUserRole.mockReturnValue(
      makeRole({ isOperator: true, fleetIds: ["fleet-op-A", "fleet-op-B"] })
    );
    const seen: string[] = [];
    const Probe: React.FC = () => {
      seen.push(useFleetFilter().selectedFleetId);
      return null;
    };
    render(
      <FleetFilterProvider>
        <Probe />
      </FleetFilterProvider>
    );

    // The load-bearing assertion — with the fix reverted (initial default =
    // ALL_FLEETS_ID for scoped users), this fails.
    expect(seen[0]).toBe("fleet-op-A");
    expect(seen[0]).not.toBe(ALL_FLEETS_ID);
    // sessionStorage is refreshed so the next tab open starts from the
    // replaced value, not the stale sentinel.
    expect(sessionStorage.getItem(SESSION_KEY)).toBe("fleet-op-A");
  });

  it("replaces a stored fleetId the caller no longer holds", () => {
    sessionStorage.setItem(SESSION_KEY, "fleet-i-do-not-own");
    mockUseUserRole.mockReturnValue(
      makeRole({ isOperator: true, fleetIds: ["owned-A", "owned-B"] })
    );
    const seen: string[] = [];
    const Probe: React.FC = () => {
      seen.push(useFleetFilter().selectedFleetId);
      return null;
    };
    render(
      <FleetFilterProvider>
        <Probe />
      </FleetFilterProvider>
    );
    expect(seen[0]).toBe("owned-A");
    expect(sessionStorage.getItem(SESSION_KEY)).toBe("owned-A");
  });

  it("preserves a stored fleetId the caller DOES hold (no spurious replacement)", () => {
    sessionStorage.setItem(SESSION_KEY, "owned-B");
    mockUseUserRole.mockReturnValue(
      makeRole({ isOperator: true, fleetIds: ["owned-A", "owned-B"] })
    );
    const seen: string[] = [];
    const Probe: React.FC = () => {
      seen.push(useFleetFilter().selectedFleetId);
      return null;
    };
    render(
      <FleetFilterProvider>
        <Probe />
      </FleetFilterProvider>
    );
    expect(seen[0]).toBe("owned-B");
  });
});

describe("FleetFilterProvider — scoped role with no fleets: empty state, not an error", () => {
  beforeEach(() => {
    sessionStorage.clear();
    mockUseUserRole.mockReset();
  });

  it("fleetIds=[] → selectedFleetId=''", () => {
    mockUseUserRole.mockReturnValue(makeRole({ isOperator: true, fleetIds: [] }));
    const seen: string[] = [];
    const Probe: React.FC = () => {
      seen.push(useFleetFilter().selectedFleetId);
      return null;
    };
    render(
      <FleetFilterProvider>
        <Probe />
      </FleetFilterProvider>
    );
    expect(seen[0]).toBe("");
  });

  it("fleetIds=[] clears any stale sessionStorage entry", () => {
    sessionStorage.setItem(SESSION_KEY, "orphaned-fleet");
    mockUseUserRole.mockReturnValue(makeRole({ isOperator: true, fleetIds: [] }));
    const Probe: React.FC = () => {
      useFleetFilter();
      return null;
    };
    render(
      <FleetFilterProvider>
        <Probe />
      </FleetFilterProvider>
    );
    expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
  });
});
