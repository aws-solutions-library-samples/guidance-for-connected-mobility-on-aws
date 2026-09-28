// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * The fleet picker lock (spec .kiro/specs/2026-09-25-cms-fi-adp-wide-lifecycle/ § D7):
 * while a page sets `pickerLockReason`, the top-nav picker renders every item
 * disabled with that reason, and a click changes neither selection store.
 */

import React, { useEffect } from "react";
import { render, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { TopNavigationProps } from "@cloudscape-design/components/top-navigation";

const { setSelectedIdSpy } = vi.hoisted(() => ({ setSelectedIdSpy: vi.fn() }));

vi.mock("../useFleetSelection", async () => {
  const actual = await vi.importActual<typeof import("../useFleetSelection")>(
    "../useFleetSelection"
  );
  return {
    ...actual,
    useFleetSelection: () => ({
      options: [
        { id: actual.ALL_FLEETS_ID, name: "All fleets" },
        { id: "fleet-a", name: "Fleet A" },
      ],
      setSelectedId: setSelectedIdSpy,
      loading: false,
      error: null,
    }),
  };
});

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

import { FleetFilterProvider, useFleetFilter } from "../../fleet-filter/FleetFilter";
import { useFleetPickerUtility } from "../useFleetPickerUtility";

const REASON = "The fleet filter doesn't apply here.";

let latest: TopNavigationProps.MenuDropdownUtility | null = null;
let latestSelected = "";

const Probe: React.FC<{ lock: string | null }> = ({ lock }) => {
  const { setPickerLockReason, selectedFleetId } = useFleetFilter();
  useEffect(() => {
    setPickerLockReason(lock);
  }, [lock, setPickerLockReason]);
  latest = useFleetPickerUtility();
  latestSelected = selectedFleetId;
  return null;
};

const clickItem = (id: string) =>
  act(() => {
    latest!.onItemClick!(
      new CustomEvent("click", { detail: { id } }) as unknown as Parameters<
        NonNullable<TopNavigationProps.MenuDropdownUtility["onItemClick"]>
      >[0]
    );
  });

beforeEach(() => {
  sessionStorage.clear();
  setSelectedIdSpy.mockReset();
  latest = null;
});

describe("fleet picker lock", () => {
  it("unlocked: items are enabled and a click selects the fleet", () => {
    render(
      <FleetFilterProvider>
        <Probe lock={null} />
      </FleetFilterProvider>
    );
    const items = latest!.items as ReadonlyArray<{ id: string; disabled?: boolean }>;
    expect(items.every((i) => !i.disabled)).toBe(true);

    clickItem("fleet-a");
    expect(setSelectedIdSpy).toHaveBeenCalledWith("fleet-a");
    expect(latestSelected).toBe("fleet-a");
  });

  it("locked: every item is disabled with the reason and a click changes nothing", () => {
    render(
      <FleetFilterProvider>
        <Probe lock={REASON} />
      </FleetFilterProvider>
    );
    expect(latest!.text).toBe("Fleet filter off");
    expect(latest!.description).toBe(REASON);
    const items = latest!.items as ReadonlyArray<{ id: string; disabled?: boolean; disabledReason?: string }>;
    expect(items).toHaveLength(2);
    expect(items.every((i) => i.disabled === true && i.disabledReason === REASON)).toBe(true);

    const before = latestSelected;
    clickItem("fleet-a");
    expect(setSelectedIdSpy).not.toHaveBeenCalled();
    expect(latestSelected).toBe(before);
    expect(sessionStorage.getItem("cms-fleet-filter-id")).not.toBe("fleet-a");
  });

  it("releasing the lock restores the enabled picker with the previous fleet still selected", () => {
    sessionStorage.setItem("cms-fleet-filter-id", "fleet-a");
    const view = render(
      <FleetFilterProvider>
        <Probe lock={REASON} />
      </FleetFilterProvider>
    );
    view.rerender(
      <FleetFilterProvider>
        <Probe lock={null} />
      </FleetFilterProvider>
    );
    const items = latest!.items as ReadonlyArray<{ id: string; disabled?: boolean; iconName?: string }>;
    expect(items.every((i) => !i.disabled)).toBe(true);
    expect(latest!.text).toBe("Fleet A");
    expect(items.find((i) => i.id === "fleet-a")?.iconName).toBe("check");
    expect(latestSelected).toBe("fleet-a");
  });
});
