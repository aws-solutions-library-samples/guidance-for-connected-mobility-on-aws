// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import { Select, FormField } from '@cloudscape-design/components';
import { FleetDataSource, FleetItem, getFleetDataSource } from '@/types/fleet-types';
import { useFleetSelection, ALL_FLEETS_ID } from './useFleetSelection';

export interface FleetPickerProps {
  /** Called whenever the selection changes (id only). */
  onChange?: (fleetId: string) => void;
  /**
   * Optional companion callback fired alongside `onChange` with the resolved
   * raw `FleetItem` (or `null` for the "All my fleets" sentinel / unknown id).
   * Use this when the parent needs the fleet's full attributes (e.g.
   * `data_source` for source derivation) without re-fetching. Added 2026-06-15
   * to remove the broken `useFleetItem` second-fetch path that depended on the
   * stubbed `ApiContext.client.send`.
   */
  onFleetItemChange?: (fleet: FleetItem | null) => void;
  label?: string;
  /**
   * When set, filters the options list to fleets whose data_source matches.
   * Special case: 'vehicle-telemetry' also includes fleets with a missing
   * data_source attribute (legacy-default semantics).
   * When undefined (default), no filtering — all fleets shown.
   */
  dataSourceFilter?: FleetDataSource;
}

/**
 * CloudScape Select-based fleet picker.
 *
 * Source-of-truth: main_api `/api/v1/fleets` via `authFetch` (rev 4).
 * Persists selection to localStorage key `cms-fleet-picker-selection`.
 * URL query param `?fleet=<id>` overrides localStorage on initial mount.
 * "All my fleets" option always present at the top.
 */
const FleetPicker: React.FC<FleetPickerProps> = ({
  onChange,
  onFleetItemChange,
  label = 'Fleet',
  dataSourceFilter,
}) => {
  const { options, rawFleets, selectedId, setSelectedId, loading, error } = useFleetSelection();

  // When dataSourceFilter is set, build a set of allowed fleet IDs from rawFleets
  // (which carry the data_source attribute), then filter the display options.
  // The "All my fleets" sentinel is never filtered out.
  const visibleOptions = React.useMemo(() => {
    if (dataSourceFilter === undefined) return options;

    const allowedIds = new Set(
      rawFleets
        .filter((f) => {
          if (dataSourceFilter === 'vehicle-telemetry') {
            // Include fleets with missing data_source (legacy-default == vehicle-telemetry).
            return !f.data_source || getFleetDataSource(f) === 'vehicle-telemetry';
          }
          return getFleetDataSource(f) === dataSourceFilter;
        })
        .map((f) => (f.id ?? f.fleetId) as string),
    );

    const [allOption, ...fleetOptions] = options;
    return [allOption, ...fleetOptions.filter((o) => allowedIds.has(o.id))];
  }, [options, rawFleets, dataSourceFilter]);

  const cloudscapeOptions = visibleOptions.map((o) => ({
    label: o.name,
    value: o.id,
  }));

  const selectedOption =
    cloudscapeOptions.find((o) => o.value === selectedId) ??
    cloudscapeOptions.find((o) => o.value === ALL_FLEETS_ID) ??
    null;

  const handleChange = (value: string) => {
    setSelectedId(value);
    onChange?.(value);
    if (onFleetItemChange) {
      if (value === ALL_FLEETS_ID) {
        onFleetItemChange(null);
      } else {
        const match = rawFleets.find((f) => (f.id ?? f.fleetId) === value) ?? null;
        onFleetItemChange(match);
      }
    }
  };

  return (
    <FormField label={label}>
      <Select
        selectedOption={selectedOption}
        onChange={({ detail }) => handleChange(detail.selectedOption.value ?? ALL_FLEETS_ID)}
        options={cloudscapeOptions}
        statusType={loading ? 'loading' : error ? 'error' : 'finished'}
        loadingText="Loading fleets…"
        errorText={error ?? undefined}
        placeholder="Select a fleet"
        ariaLabel="Fleet picker"
      />
    </FormField>
  );
};

export default FleetPicker;
