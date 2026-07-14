// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useState } from 'react';
import { authFetch } from '@/utils/authFetch';
import { getRuntimeConfig } from '@/config/api';
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
 * Reads accessible fleets from the main_api `/api/v1/fleets` endpoint.
 *
 * History: this hook previously called `ApiContext.client.send(new ListFleetsCommand())`,
 * but `createFleetManagementClient` (src/api/client.ts) is a stub that returns
 * `Promise.resolve({})`. As a result, the picker rendered no fleets in production.
 * Switched 2026-06-15 to call `authFetch('/api/v1/fleets')` directly, mirroring the
 * working pattern in `commons/FleetSelector.tsx`. The backend returns raw DDB items,
 * so we normalize `id` from `fleetId ?? id` and preserve `data_source` (and any
 * other attributes) on `rawFleets` for downstream `dataSourceFilter` logic in
 * `FleetPicker`.
 *
 * Persistence:
 *  - URL query param `?fleet=<id>` wins on initial mount.
 *  - Otherwise uses localStorage key `cms-fleet-picker-selection`.
 *  - Falls back to ALL_FLEETS_ID on first visit.
 */
export const useFleetSelection = (): UseFleetSelectionResult => {
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

    const apiEndpoint = (getRuntimeConfig().apiEndpoint || '').replace(/\/$/, '');
    const url = `${apiEndpoint}/api/v1/fleets`;

    authFetch(url)
      .then(async (response) => {
        if (!response.ok) {
          const text = await response.text().catch(() => '');
          throw new Error(`Failed to load fleets (${response.status}): ${text}`);
        }
        return response.json();
      })
      .then((output: { fleets?: FleetItem[] }) => {
        if (cancelled) return;
        const raw = (output.fleets ?? []) as FleetItem[];
        // Normalize id from the DDB partition key (`fleetId`) when needed,
        // but preserve every original attribute (incl. `data_source`) for
        // downstream filter logic.
        const rawNormalized: FleetItem[] = raw.map((f) => ({
          ...f,
          id: (f.id ?? f.fleetId) as string,
        }));
        const fleetOptions: FleetOption[] = rawNormalized.map((f) => ({
          id: (f.id ?? f.fleetId) as string,
          name: (f.name ?? f.fleetId ?? (f.id as string)) as string,
        }));
        setRawFleets(rawNormalized);
        setOptions([{ id: ALL_FLEETS_ID, name: 'All my fleets' }, ...fleetOptions]);
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
  }, []);

  return { options, rawFleets, selectedId, setSelectedId, loading, error };
};
