// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { createContext, useCallback, useContext, useRef, useState } from 'react';
import { getVsaApiEndpoint } from '../config/api';

/**
 * One deduction entry from the server-computed health-score breakdown.
 * Mirrors the shape produced by the api-vehicle-context Lambda's
 * `_compute_health_score` helper. Reason strings are stable enough for
 * the CMS UI to render them verbatim — no locale-specific reformatting
 * in v1.
 */
export interface HealthScoreDeduction {
  reason: string;
  amount: number;
}

/** Server-computed health-score breakdown for a single vehicle. */
export interface HealthScoreBreakdown {
  score: number;
  deductions: HealthScoreDeduction[];
  /** ISO-8601 timestamp from the Lambda — included so the UI can show
   *  a "computed at …" line in the tooltip if it ever wants to. */
  computedAt: string;
}

interface VehicleContextType {
  // ── Existing fields (legacy state holder) ──────────────────────────
  vehicleVin: string | null;
  setVehicleVin: (vin: string | null) => void;
  driverName: string | null;
  setDriverName: (name: string | null) => void;

  // ── Vehicle health (server-computed via VSA backend) ───────────────
  /**
   * Currently-loaded vehicle health score (0..100). Single source of
   * truth — computed by the api-vehicle-context Lambda; both the iOS
   * Home tab and the CMS UI Vehicle Detail page render this value
   * verbatim. `null` means no fetch has succeeded for the
   * `healthVehicleId` yet.
   */
  healthScore: number | null;
  /**
   * Per-deduction breakdown that explains the score. Powers the
   * VehicleHealthScoreWidget's expandable "what's bringing this
   * down?" list.
   */
  healthScoreBreakdown: HealthScoreBreakdown | null;
  /** Raw active DTC list from /vehicles/{id}/context. The widget uses
   *  this to render a "Active DTCs · severity breakdown" KPI tile next
   *  to the score so the wide container has real density beyond the
   *  deductions list. Null until the first successful fetch lands. */
  healthActiveDtcs: HealthActiveDtc[] | null;
  /** connectionStatus field as the Lambda saw it on the DDB row (raw,
   *  before the lastSeenAt freshness override). Used by the widget's
   *  KPI tile to show the live link state. */
  healthConnectionStatus: string | null;
  /** lastSeenAt ISO timestamp from the DDB row. The widget uses this
   *  to render "Last seen · 3m ago" — same source the freshness
   *  override on the score uses. Null when the field is missing. */
  healthLastSeenAt: string | null;
  /** vehicleId the health-score state currently belongs to. Used by
   *  the widget to decide whether the cached values are still
   *  applicable when navigating between vehicles. */
  healthVehicleId: string | null;
  healthLoading: boolean;
  healthError: string | null;
  /**
   * Fetch the server-computed health score for `vehicleId` and store
   * it on the context. The widget calls this on mount; subsequent
   * renders read straight from `healthScore` / `healthScoreBreakdown`.
   *
   * `getAuthHeaders` is supplied by the caller (CMS UI's `useAuth()`)
   * so the context doesn't have to import an auth provider directly —
   * keeps the context decoupled from the auth implementation and
   * makes it trivially mockable in tests.
   */
  loadVehicleHealth: (
    vehicleId: string,
    getAuthHeaders: () => Record<string, string>,
  ) => Promise<void>;
}

/** Subset of the ActiveDtc shape the widget actually reads. */
export interface HealthActiveDtc {
  code: string;
  severity?: string | null;
}

const VehicleContext = createContext<VehicleContextType | undefined>(undefined);

export const VehicleProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [vehicleVin, setVehicleVin] = useState<string | null>(null);
  const [driverName, setDriverName] = useState<string | null>(null);

  const [healthScore, setHealthScore] = useState<number | null>(null);
  const [healthScoreBreakdown, setHealthScoreBreakdown] = useState<HealthScoreBreakdown | null>(null);
  const [healthActiveDtcs, setHealthActiveDtcs] = useState<HealthActiveDtc[] | null>(null);
  const [healthConnectionStatus, setHealthConnectionStatus] = useState<string | null>(null);
  const [healthLastSeenAt, setHealthLastSeenAt] = useState<string | null>(null);
  const [healthVehicleId, setHealthVehicleId] = useState<string | null>(null);
  const [healthLoading, setHealthLoading] = useState<boolean>(false);
  const [healthError, setHealthError] = useState<string | null>(null);

  // De-dupe concurrent loads for the same vehicleId. React StrictMode
  // double-invokes effects in dev, and a slow VSA endpoint would
  // otherwise fire two parallel fetches for every navigation.
  const inFlightRef = useRef<Map<string, Promise<void>>>(new Map());

  const loadVehicleHealth = useCallback(async (
    vehicleId: string,
    getAuthHeaders: () => Record<string, string>,
  ) => {
    if (!vehicleId) return;
    const existing = inFlightRef.current.get(vehicleId);
    if (existing) {
      return existing;
    }

    const endpoint = getVsaApiEndpoint();
    if (!endpoint) {
      // No VSA endpoint configured for this deployment. Surface a
      // soft error so the widget can hide itself; don't try to issue
      // a relative-URL fetch that would 404 against the CMS UI's own
      // origin.
      setHealthError('VSA endpoint not configured');
      setHealthVehicleId(vehicleId);
      return;
    }

    // Belt-and-suspenders: ensure exactly one trailing slash before
    // joining the path. Different runtimeConfig sources have left it
    // both with and without one historically.
    const base = endpoint.endsWith('/') ? endpoint : endpoint + '/';
    const url = `${base}vehicles/${encodeURIComponent(vehicleId)}/context`;

    const promise = (async () => {
      setHealthLoading(true);
      setHealthError(null);
      try {
        const res = await fetch(url, {
          headers: {
            'Content-Type': 'application/json',
            ...getAuthHeaders(),
          },
        });
        if (!res.ok) {
          // Read the body for the operator's logs; the user-facing
          // error stays generic.
          const body = await res.text().catch(() => '');
          console.warn(
            `loadVehicleHealth: HTTP ${res.status} for ${vehicleId}`,
            body.slice(0, 200),
          );
          setHealthError(`HTTP ${res.status}`);
          setHealthVehicleId(vehicleId);
          return;
        }
        const data = await res.json();
        // The Lambda returns both the integer score and the breakdown.
        // Surface them separately so callers don't have to dig into the
        // breakdown for the headline number.
        const score = typeof data.healthScore === 'number' ? data.healthScore : null;
        const breakdown = data.healthScoreBreakdown && typeof data.healthScoreBreakdown === 'object'
          ? data.healthScoreBreakdown as HealthScoreBreakdown
          : null;
        // Pull the small set of context fields the widget renders as
        // KPI tiles next to the score. Keep them optional — older
        // Lambda deploys may not have them, in which case the widget
        // simply hides the corresponding tile.
        const dtcs = Array.isArray(data.activeDtcs)
          ? (data.activeDtcs as any[]).map(d => ({ code: d.code, severity: d.severity ?? null }))
          : null;
        const connStatus = data.vehicle && typeof data.vehicle.connectionStatus === 'string'
          ? data.vehicle.connectionStatus
          : null;
        const lastSeen = data.vehicle && typeof data.vehicle.lastSeenAt === 'string'
          ? data.vehicle.lastSeenAt
          : null;
        setHealthScore(score);
        setHealthScoreBreakdown(breakdown);
        setHealthActiveDtcs(dtcs);
        setHealthConnectionStatus(connStatus);
        setHealthLastSeenAt(lastSeen);
        setHealthVehicleId(vehicleId);
      } catch (e: any) {
        console.warn(`loadVehicleHealth: fetch failed for ${vehicleId}`, e);
        setHealthError(e?.message || 'Network error');
        setHealthVehicleId(vehicleId);
      } finally {
        setHealthLoading(false);
        inFlightRef.current.delete(vehicleId);
      }
    })();

    inFlightRef.current.set(vehicleId, promise);
    return promise;
  }, []);

  return (
    <VehicleContext.Provider
      value={{
        vehicleVin,
        setVehicleVin,
        driverName,
        setDriverName,
        healthScore,
        healthScoreBreakdown,
        healthActiveDtcs,
        healthConnectionStatus,
        healthLastSeenAt,
        healthVehicleId,
        healthLoading,
        healthError,
        loadVehicleHealth,
      }}
    >
      {children}
    </VehicleContext.Provider>
  );
};

export const useVehicle = (): VehicleContextType => {
  const context = useContext(VehicleContext);
  if (context === undefined) {
    // Return default values when provider is not available. Keeping
    // the no-op shape preserves the legacy "render even outside the
    // provider" contract used by drawer / popover surfaces that mount
    // outside the main app tree.
    return {
      vehicleVin: null,
      setVehicleVin: () => {},
      driverName: null,
      setDriverName: () => {},
      healthScore: null,
      healthScoreBreakdown: null,
      healthActiveDtcs: null,
      healthConnectionStatus: null,
      healthLastSeenAt: null,
      healthVehicleId: null,
      healthLoading: false,
      healthError: null,
      loadVehicleHealth: async () => {},
    };
  }
  return context;
};
