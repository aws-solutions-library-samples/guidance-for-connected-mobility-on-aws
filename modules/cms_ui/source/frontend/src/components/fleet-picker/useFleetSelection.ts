// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useMemo, useState } from 'react';
import { authFetch } from '@/utils/authFetch';
import { getRuntimeConfig } from '@/config/api';
import { useUserRole } from '@/auth/useUserRole';
import { FleetItem } from '@/types/fleet-types';

const STORAGE_KEY = 'cms-fleet-picker-selection';

/** Sentinel id representing "all fleets" selection. */
export const ALL_FLEETS_ID = '__all__';

export interface FleetOption {
  id: string;
  name: string;
}

export interface UseFleetSelectionResult {
  /** Loaded fleet options. Includes "All my fleets" at index 0 for cross-fleet
   *  roles (platform-admin, fleet-viewer). For scoped roles (fleet-operator,
   *  fleet-guest, dispatcher) the sentinel is dropped and the list is filtered
   *  to the fleets in the caller's `custom:fleetIds` claim, per the auth
   *  contract at `services/fleet_intelligence/_auth.py`. */
  options: FleetOption[];
  /** Raw FleetItem[] from the API (excludes the "All my fleets" sentinel). Used by FleetPicker for data_source filtering. */
  rawFleets: FleetItem[];
  /** Currently selected fleet id (ALL_FLEETS_ID = "all"). For scoped roles this
   *  is always a specific fleetId (or `''` when the caller holds no fleets),
   *  never the ALL sentinel. */
  selectedId: string;
  setSelectedId: (id: string) => void;
  loading: boolean;
  error: string | null;
}

/**
 * Decide the initial `selectedId` synchronously from the current role, AND
 * persist any replacement back to localStorage.
 *
 * The rules mirror `services/fleet_intelligence/_auth.py::authorize_fleet_scope`:
 *
 *   - `platform-admin` or `fleet-viewer` (cross-fleet) → allowed to select any
 *     fleet, including the ALL sentinel. URL param wins, then localStorage,
 *     then ALL_FLEETS_ID.
 *   - `fleet-operator`, `fleet-guest`, `dispatcher` (scoped) → the ALL sentinel
 *     would 403 on first FI page load (see the fix's summary.md, "Follow-ons
 *     (not blocking)"). Reject a stored ALL and any fleetId not in
 *     `custom:fleetIds`; default to the first owned fleet. When the caller
 *     holds no fleets, return `''` so consumers render an empty state instead
 *     of firing a request that will 403.
 *
 * Persistence: when the resolved value differs from what is in localStorage,
 * the storage is refreshed. The write is idempotent under React's
 * StrictMode double-invocation of `useState` initializers.
 *
 * Called from the `useState(() => …)` initializer, which runs once on mount.
 * If the role resolves late (auth async), `useFleetSelection`'s role-
 * correction `useEffect` re-runs the same rule.
 */
const resolveInitialSelectedId = (
  isCrossFleet: boolean,
  roleFleetIds: string[],
): string => {
  const params = new URLSearchParams(window.location.search);
  const urlFleet = params.get('fleet');
  const stored = localStorage.getItem(STORAGE_KEY);
  const candidate = urlFleet ?? stored ?? '';
  let resolved: string;
  if (isCrossFleet) {
    resolved = candidate || ALL_FLEETS_ID;
  } else if (
    candidate &&
    candidate !== ALL_FLEETS_ID &&
    roleFleetIds.includes(candidate)
  ) {
    // Scoped: keep the candidate only if it is a fleet the caller actually holds.
    resolved = candidate;
  } else {
    resolved = roleFleetIds[0] ?? '';
  }
  // Keep localStorage in sync so a subsequent page load (before the user
  // picks anything) starts from the resolved value, not the stale stored one.
  if (resolved !== stored) {
    if (resolved) {
      localStorage.setItem(STORAGE_KEY, resolved);
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  }
  return resolved;
};

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
 *  - Falls back to a role-appropriate default (see `resolveInitialSelectedId`).
 *
 * Role-awareness (2026-09-26 — follow-on 1 to
 * `issues/2026-09-25-fleet-intelligence-routes-trust-caller-fleet-id/`):
 *  - Cross-fleet roles (platform-admin, fleet-viewer) keep today's behaviour:
 *    the sentinel is the default and "All my fleets" is always at index 0.
 *  - Scoped roles (fleet-operator, fleet-guest, dispatcher) default to their
 *    first `custom:fleetIds` entry, the sentinel is dropped from `options`,
 *    and a stored ALL_FLEETS_ID (or a not-owned fleetId) is replaced. This
 *    keeps first-page load of Fleet Intelligence within the auth contract at
 *    `services/fleet_intelligence/_auth.py`, which now 403s on portal-wide
 *    requests from scoped callers.
 */
export const useFleetSelection = (): UseFleetSelectionResult => {
  const role = useUserRole();
  const isCrossFleet = role.isAdmin || role.isViewer;
  const roleFleetIds = role.fleetIds;

  const [rawFleets, setRawFleets] = useState<FleetItem[]>([]);
  const [selectedId, _setSelectedId] = useState<string>(() =>
    resolveInitialSelectedId(isCrossFleet, roleFleetIds),
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const setSelectedId = useCallback((id: string) => {
    _setSelectedId(id);
    localStorage.setItem(STORAGE_KEY, id);
  }, []);

  // Role-correction: keep `selectedId` in sync with the caller's role if it
  // resolves after mount (auth loads asynchronously) or if the current
  // selection is no longer valid for the role (e.g. a stored ALL from before
  // this fix landed, or a stored fleet the caller has since lost access to).
  //
  // The rule is intentionally identical to `resolveInitialSelectedId` above so
  // an initializer-mount and a post-mount role change land on the same answer.
  useEffect(() => {
    if (isCrossFleet) {
      // Cross-fleet: only correct an empty selection (transient
      // unauthenticated init). Never overwrite an explicit choice.
      if (selectedId === '') {
        _setSelectedId(ALL_FLEETS_ID);
        localStorage.setItem(STORAGE_KEY, ALL_FLEETS_ID);
      }
      return;
    }
    const shouldBe = roleFleetIds.includes(selectedId)
      ? selectedId
      : roleFleetIds[0] ?? '';
    if (shouldBe !== selectedId) {
      _setSelectedId(shouldBe);
      if (shouldBe) {
        localStorage.setItem(STORAGE_KEY, shouldBe);
      } else {
        localStorage.removeItem(STORAGE_KEY);
      }
    }
    // Depend on the stringified fleetIds list so a fresh array reference with
    // the same members does not re-fire this effect (useUserRole memoises the
    // array per auth.user identity, so this is defence-in-depth).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isCrossFleet, roleFleetIds.join('|'), selectedId]);

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
        setRawFleets(rawNormalized);
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

  // `options` is derived from `rawFleets` + role so that a late role resolution
  // (auth async) or a runtime role change reshapes the picker without
  // re-fetching. Cross-fleet gets the ALL sentinel; scoped gets the intersection
  // of API-returned fleets and `custom:fleetIds`.
  const options = useMemo<FleetOption[]>(() => {
    const fleetOptions: FleetOption[] = rawFleets.map((f) => ({
      id: (f.id ?? f.fleetId) as string,
      name: (f.name ?? f.fleetId ?? (f.id as string)) as string,
    }));
    if (isCrossFleet) {
      return [{ id: ALL_FLEETS_ID, name: 'All my fleets' }, ...fleetOptions];
    }
    const owned = new Set(roleFleetIds);
    return fleetOptions.filter((o) => owned.has(o.id));
  }, [rawFleets, isCrossFleet, roleFleetIds]);

  return { options, rawFleets, selectedId, setSelectedId, loading, error };
};
