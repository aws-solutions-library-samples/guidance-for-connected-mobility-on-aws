// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// useFleetPickerUtility — the fleet picker rendered as a Cloudscape
// TopNavigation menu-dropdown utility.
//
// The canonical AWS Console pattern for session-context switchers (region,
// account) is a menu-dropdown in the top-bar utilities strip. This hook
// returns a `TopNavigationProps.MenuDropdownUtility` a caller can splice into
// its `utilities` array.
//
// Consumes BOTH `useFleetSelection` (for the fleet list + the localStorage
// store that FleetPicker owns for its form-context callers) AND
// `useFleetFilter` (for the sessionStorage store that ambient consumers like
// FleetCostDashboard read). Selection updates both, mirroring the sync the
// original FleetFilter → FleetPicker component pair did.
//
// Added 2026-09-14 as part of the left-nav restructure that moved the fleet
// picker out of the SideNavigation header area so "Fleet Intelligence" is the
// top element there and "Manage Fleets" is a distinct nav link above the
// Operations section. See
// `issues/2026-09-14-cms-fleet-filter-duplicate-label/`.

import type { TopNavigationProps } from '@cloudscape-design/components/top-navigation';

import { useFleetSelection } from './useFleetSelection';
import { useFleetFilter } from '../fleet-filter/FleetFilter';

export function useFleetPickerUtility(): TopNavigationProps.MenuDropdownUtility {
  const { options, setSelectedId, loading, error } = useFleetSelection();
  const { selectedFleetId, setSelectedFleetId, pickerLockReason } = useFleetFilter();

  // A page with no fleet dimension (FI lifecycle, "All ADP vehicles") locks
  // the picker: every item is disabled with the page's reason, and clicks are
  // ignored even if a disabled item's event still fires. The stored selection
  // is left untouched so it applies again when the page releases the lock.
  if (pickerLockReason) {
    return {
      type: 'menu-dropdown',
      text: 'Fleet filter off',
      description: pickerLockReason,
      ariaLabel: 'Fleet (filter off on this page)',
      iconName: 'group',
      items: options.map((o) => ({
        id: o.id,
        text: o.name,
        disabled: true,
        disabledReason: pickerLockReason,
      })),
      onItemClick: () => {},
    };
  }

  const selected = options.find((o) => o.id === selectedFleetId);
  const triggerText = loading
    ? 'Loading fleets…'
    : error
    ? 'Fleets unavailable'
    : selected
    ? selected.name
    : 'Select fleet';

  return {
    type: 'menu-dropdown',
    text: triggerText,
    ariaLabel: 'Fleet',
    iconName: 'group',
    items: options.map((o) => ({
      id: o.id,
      text: o.name,
      // Check mark on the currently selected fleet — matches the AWS Console
      // region-picker pattern.
      iconName: o.id === selectedFleetId ? 'check' : undefined,
    })),
    onItemClick: (event) => {
      const nextId = event.detail.id;
      // Keep both stores in sync — the ambient sessionStorage store used by
      // FleetCostDashboard etc., AND the localStorage store used by
      // form-context FleetPicker instances (create-vehicle, enroll-wizard).
      setSelectedId(nextId);
      setSelectedFleetId(nextId);
    },
  };
}
