// SPDX-License-Identifier: Apache-2.0
//
// useDataProductFleets — small hook that fetches the operator's REAL
// fleets from /api/v1/fleets and hands them to the data-products flow.
//
// Design notes:
//   - Fleets themselves come from the CMS backend (real DDB records).
//   - The fleet ↔ data-product assignments are a session-scoped overlay
//     that lives in mockData.ts (fleetAssignments map). This hook has no
//     opinion on the overlay — callers combine the two.
//   - A synthetic 'Tesla Fleet' is appended to the returned list. Real
//     CMS staging has no Tesla fleet in /api/v1/fleets and creating one
//     via the real API path is out of scope for the demo; injecting a
//     virtual fleet gives the Tesla producer story a fleet + vehicle to
//     bind to without touching the backend.
//   - Auto-seeds obvious fleet-to-producer assignments on first fetch:
//     any real fleet name containing 'meridian' gets Meridian; any name
//     containing 'oem1' gets Ford Pro; the synthetic Tesla Fleet gets
//     Tesla. Runs once per module lifetime (session-scoped flag).
//   - Mirrors the fetch pattern used by useFleetSelection.ts and
//     FleetSelector.tsx: authFetch against runtimeConfig.apiEndpoint,
//     unwrap the { fleets: [] } envelope, normalize id from
//     fleetId ?? id.
//   - A `bump` counter forces a refetch (used after an assignment
//     changes so unassigned-fleet lists refresh).

import { useCallback, useEffect, useState } from 'react';
import { authFetch } from '@/utils/authFetch';
import { getRuntimeConfig } from '../../config/api';
import type { FleetItem } from '@/types/fleet-types';
import { assignDataProductToFleet, getAssignedProductIdsForFleet } from './mockData';

export interface UseDataProductFleetsResult {
  fleets: FleetItem[];
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

/** Synthetic Tesla Fleet appended to the real /api/v1/fleets response so
 *  the Tesla producer story has a matching fleet without needing a real
 *  fleet-creation flow. Vehicle count of 1 mirrors the single seeded
 *  vehicle in CMS_FLEET_POOL under fleetName 'Tesla Fleet'. */
export const SYNTHETIC_TESLA_FLEET: FleetItem = {
  id: 'virt_tesla_fleet',
  fleetId: 'virt_tesla_fleet',
  name: 'Tesla Fleet',
  vehicleCount: 1,
  totalVehicles: 1,
  data_source: 'cloud-telemetry',
} as FleetItem;

// Module-scope flag — seeds Meridian/OEM1/Tesla assignments once per
// session on the first successful fetch. Additional refetches won't
// re-seed (protects operator's manual assignment changes).
let didAutoSeedAssignments = false;

function autoSeedAssignments(fleets: FleetItem[]): void {
  if (didAutoSeedAssignments) return;
  didAutoSeedAssignments = true;
  for (const f of fleets) {
    const id = (f.id ?? f.fleetId ?? '') as string;
    if (!id) continue;
    // Never clobber an existing assignment (e.g. from a re-mount).
    if (getAssignedProductIdsForFleet(id).length > 0) continue;
    // Match on either the fleet's display name OR its id — staging fleets
    // often carry the producer name in the id (flt-meridian-range-001)
    // while the display name is a marketing string. Case-insensitive.
    const haystack = `${f.name ?? ''} ${id}`.toLowerCase();
    if (haystack.includes('meridian')) {
      assignDataProductToFleet(id, 'prd_meridian_fleet');
    } else if (haystack.includes('oem1') || haystack.includes('ford')) {
      assignDataProductToFleet(id, 'prd_ford_pro');
    } else if (id === 'virt_tesla_fleet' || haystack.includes('tesla')) {
      assignDataProductToFleet(id, 'prd_tesla_fleet');
    }
  }
}

export function useDataProductFleets(): UseDataProductFleetsResult {
  const [fleets, setFleets] = useState<FleetItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [bump, setBump] = useState(0);

  const refetch = useCallback(() => setBump((n) => n + 1), []);

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
        // Normalize `id` from the DDB partition key `fleetId` when only
        // one is present. Preserves every other attribute so downstream
        // callers can read `name`, `vehicleCount`, etc.
        const normalized: FleetItem[] = raw.map((f) => ({
          ...f,
          id: (f.id ?? f.fleetId) as string,
        }));
        // Append the synthetic Tesla Fleet so the Tesla producer story
        // has a fleet to bind to.
        const withSynthetic: FleetItem[] = [...normalized, SYNTHETIC_TESLA_FLEET];
        autoSeedAssignments(withSynthetic);
        setFleets(withSynthetic);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : 'Failed to load fleets');
        // Even on API failure, expose the synthetic Tesla Fleet so the
        // Tesla story remains demoable when staging can't be reached.
        autoSeedAssignments([SYNTHETIC_TESLA_FLEET]);
        setFleets([SYNTHETIC_TESLA_FLEET]);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [bump]);

  return { fleets, loading, error, refetch };
}
