// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// FleetFilterProvider + useFleetFilter — ambient fleet-selection state.
//
// The fleet picker itself lives in the TopNavigation utilities strip (see
// `components/fleet-picker/useFleetPickerUtility.ts`), not in the left nav.
// This module only exposes the sessionStorage-backed state that ambient
// consumers (FleetCostDashboard, etc.) read via `useFleetFilter()`.
//
// The selected fleetId is held in sessionStorage so it survives a page refresh
// but resets between browser sessions — fleet selection is ephemeral context,
// not a saved user preference.
//
// Role-awareness (2026-09-26 — follow-on 1 to
// `issues/2026-09-25-fleet-intelligence-routes-trust-caller-fleet-id/`):
//   Same rule as `useFleetSelection.ts`. Cross-fleet roles (platform-admin,
//   fleet-viewer) keep today's ALL_FLEETS_ID default. Scoped roles
//   (fleet-operator, fleet-guest, dispatcher) default to their first
//   `custom:fleetIds` entry, and a stored ALL_FLEETS_ID (or a fleetId no
//   longer in the caller's claim) is replaced. Without this, the FI dashboard
//   fires a portal-wide request on first render for a scoped caller and the
//   backend 403s.
//
// History:
//   - 2026-09-02 (spec 2026-09-02-cms-fleet-intelligence-v1 § D5 / T5.4):
//     Fleet left the SideNavigation as a top-level nav item and became an
//     ambient filter, rendered above the nav via a now-deleted `FleetFilter`
//     component (a `<Box>` with a "Fleet" section header + FleetPicker + a
//     standalone "Manage" button).
//   - 2026-09-14 (issues/2026-09-14-cms-fleet-filter-duplicate-label/): the
//     picker moved to the TopNavigation utilities strip; the `FleetFilter`
//     component was removed. FleetFilterProvider + useFleetFilter remain,
//     since other components (FleetCostDashboard) already depend on them.
//   - 2026-09-26 (this file): role-aware default + stored-sentinel replacement.
//   - 2026-09-26 (spec 2026-09-25-cms-fi-adp-wide-lifecycle § D7): the
//     `pickerLockReason` lock, so a page with no fleet dimension can disable
//     the top-nav picker while it is shown.

import React, { createContext, useContext, useEffect, useState } from "react";
import { useUserRole } from "@/auth/useUserRole";
import { ALL_FLEETS_ID } from "../fleet-picker/useFleetSelection";

// ---------------------------------------------------------------------------
// Context — exposes the selected fleetId to descendant components
// ---------------------------------------------------------------------------

interface FleetFilterContextValue {
  /** The currently selected fleet ID, or ALL_FLEETS_ID for all fleets. Empty
   *  string when the caller is scoped and holds no fleets — consumers should
   *  render an empty state rather than firing a portal-wide request. */
  selectedFleetId: string;
  setSelectedFleetId: (fleetId: string) => void;
  /** When non-null, the current page ignores the fleet filter and the
   *  top-nav picker renders disabled with this text as the reason. Set by a
   *  page whose data has no fleet dimension (Fleet Intelligence lifecycle,
   *  "All ADP vehicles" scope); the page must clear it on unmount. */
  pickerLockReason: string | null;
  setPickerLockReason: (reason: string | null) => void;
}

const FleetFilterContext = createContext<FleetFilterContextValue>({
  selectedFleetId: ALL_FLEETS_ID,
  setSelectedFleetId: () => {},
  pickerLockReason: null,
  setPickerLockReason: () => {},
});

export const useFleetFilter = () => useContext(FleetFilterContext);

// ---------------------------------------------------------------------------
// Provider — wraps the app to broadcast the selected fleet
// ---------------------------------------------------------------------------

const SESSION_KEY = "cms-fleet-filter-id";

/**
 * Decide the initial `selectedFleetId` synchronously from the current role,
 * AND persist any replacement back to sessionStorage. Mirrors
 * `useFleetSelection::resolveInitialSelectedId` — the two stores must agree so
 * the picker (backed by `useFleetSelection`) and downstream consumers of this
 * context (FleetCostDashboard, etc.) do not disagree on first render.
 */
const resolveInitialSelectedFleetId = (
  isCrossFleet: boolean,
  roleFleetIds: string[],
): string => {
  const stored = sessionStorage.getItem(SESSION_KEY);
  let resolved: string;
  if (isCrossFleet) {
    resolved = stored ?? ALL_FLEETS_ID;
  } else if (
    stored &&
    stored !== ALL_FLEETS_ID &&
    roleFleetIds.includes(stored)
  ) {
    resolved = stored;
  } else {
    resolved = roleFleetIds[0] ?? "";
  }
  if (resolved !== stored) {
    if (resolved) {
      sessionStorage.setItem(SESSION_KEY, resolved);
    } else {
      sessionStorage.removeItem(SESSION_KEY);
    }
  }
  return resolved;
};

export const FleetFilterProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const role = useUserRole();
  const isCrossFleet = role.isAdmin || role.isViewer;
  const roleFleetIds = role.fleetIds;

  const [selectedFleetId, setSelectedFleetIdState] = useState<string>(() =>
    resolveInitialSelectedFleetId(isCrossFleet, roleFleetIds),
  );
  const [pickerLockReason, setPickerLockReason] = useState<string | null>(null);

  const setSelectedFleetId = (fleetId: string) => {
    if (fleetId) {
      sessionStorage.setItem(SESSION_KEY, fleetId);
    } else {
      sessionStorage.removeItem(SESSION_KEY);
    }
    setSelectedFleetIdState(fleetId);
  };

  // Role-correction: keep the sessionStorage-backed selection in sync with
  // the caller's role. Same rule as the initializer above, so a synchronous
  // mount and a post-mount role change land on the same answer. Without this
  // effect, a scoped caller whose token resolves after the provider mounts
  // would keep ALL_FLEETS_ID and the FI dashboard's first render would fire a
  // portal-wide request that the backend now 403s.
  useEffect(() => {
    if (isCrossFleet) {
      if (selectedFleetId === "") {
        sessionStorage.setItem(SESSION_KEY, ALL_FLEETS_ID);
        setSelectedFleetIdState(ALL_FLEETS_ID);
      }
      return;
    }
    const shouldBe = roleFleetIds.includes(selectedFleetId)
      ? selectedFleetId
      : roleFleetIds[0] ?? "";
    if (shouldBe !== selectedFleetId) {
      if (shouldBe) {
        sessionStorage.setItem(SESSION_KEY, shouldBe);
      } else {
        sessionStorage.removeItem(SESSION_KEY);
      }
      setSelectedFleetIdState(shouldBe);
    }
    // See note in useFleetSelection.ts — stringified deps so a fresh array
    // reference with identical members does not re-fire.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isCrossFleet, roleFleetIds.join("|"), selectedFleetId]);

  return (
    <FleetFilterContext.Provider
      value={{ selectedFleetId, setSelectedFleetId, pickerLockReason, setPickerLockReason }}
    >
      {children}
    </FleetFilterContext.Provider>
  );
};
