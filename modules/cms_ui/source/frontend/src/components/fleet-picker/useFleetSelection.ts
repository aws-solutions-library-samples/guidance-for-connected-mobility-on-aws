// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useContext, useEffect, useState } from 'react';
import { ApiContext } from '@/api/provider';
import { ListFleetsCommand } from '@/api/fleet-management-client';
import { FleetItem } from '@/types/fleet-types';

const STORAGE_KEY = 'cms-fleet-picker-selection';

/** Sentinel id representing "all fleets" selection. */
export const ALL_FLEETS_ID = '__all__';

export interface FleetOption {
  id: string;
  name: string;
}

export interface UseFleetSelectionResult {
  /** Loaded fleet options (includes "All my fleets" at index 0). */
  options: FleetOption[];
  /** Raw FleetItem[] from the API (excludes the "All my fleets" sentinel). Used by FleetPicker for data_source filtering. */
  rawFleets: FleetItem[];
  /** Currently selected fleet id (ALL_FLEETS_ID = "all"). */
  selectedId: string;
  setSelectedId: (id: string) => void;
  loading: boolean;
  error: string | null;
}

/**
 * Reads accessible fleets from the existing Fleet Management API
 * (ApiContext.client / /fleets endpoint — rev 3 A3 source-of-truth).
 *
 * Persistence:
 *  - URL query param `?fleet=<id>` wins on initial mount.
 *  - Otherwise uses localStorage key `cms-fleet-picker-selection`.
 *  - Falls back to ALL_FLEETS_ID on first visit.
 */
export const useFleetSelection = (): UseFleetSelectionResult => {
  const { client } = useContext(ApiContext);

  const [options, setOptions] = useState<FleetOption[]>([]);
  const [rawFleets, setRawFleets] = useState<FleetItem[]>([]);
  const [selectedId, _setSelectedId] = useState<string>(() => {
    // URL param wins on initial mount.
    const params = new URLSearchParams(window.location.search);
    const urlFleet = params.get('fleet');
    if (urlFleet) return urlFleet;
    // Fall back to localStorage.
    return localStorage.getItem(STORAGE_KEY) ?? ALL_FLEETS_ID;
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const setSelectedId = useCallback((id: string) => {
    _setSelectedId(id);
    localStorage.setItem(STORAGE_KEY, id);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    client
      .send(new ListFleetsCommand())
      .then((output: { fleets?: FleetItem[] }) => {
        if (cancelled) return;
        const fleets: FleetOption[] = (output.fleets ?? []).map((f: FleetItem) => ({
          id: (f.id ?? f.fleetId) as string,
          name: (f.name ?? f.fleetId ?? (f.id as string)),
        }));
        setRawFleets(output.fleets ?? []);
        setOptions([{ id: ALL_FLEETS_ID, name: 'All my fleets' }, ...fleets]);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : 'Failed to load fleets');
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [client]);

  return { options, rawFleets, selectedId, setSelectedId, loading, error };
};
