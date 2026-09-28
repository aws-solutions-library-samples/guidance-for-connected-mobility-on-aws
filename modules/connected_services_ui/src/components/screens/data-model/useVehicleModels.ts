// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * useVehicleModels — shared React hook for the /model-manifests payload.
 *
 * Both ECUsView and VehicleModelsView read the same `/model-manifests`
 * endpoint. This hook centralises that single fetch so neither view makes
 * its own duplicate call to the same endpoint.
 *
 * ## States
 *
 * - "loading":     initial fetch in progress
 * - "ready":       data received; `manifests` is populated
 * - "unavailable": API endpoint not configured (fetchVehicleModels returned null)
 * - "error":       HTTP error or network failure; `error` contains the message
 *
 * These are DISTINCT — callers must not conflate "no data" (unavailable or
 * error) with "empty list" (ready with zero manifests). See spec § "three
 * distinct states: unconfigured / error / empty".
 */

import { useCallback, useEffect, useState } from "react";

import type { ModelManifestItem, ModelManifestsResponse } from "../../../api/dataModelClient";
import { fetchVehicleModels } from "../../../api/dataModelClient";

export type VehicleModelsStatus = "loading" | "ready" | "unavailable" | "error";

export interface VehicleModelsState {
  readonly status: VehicleModelsStatus;
  readonly manifests: readonly ModelManifestItem[];
  readonly error: string | null;
}

/**
 * Fetch the model-manifests payload once on mount.
 *
 * @param fetchImpl - Injectable fetch function for testing. Defaults to global fetch.
 */
export function useVehicleModels(
  fetchImpl: typeof fetch = fetch,
): VehicleModelsState {
  const [status, setStatus] = useState<VehicleModelsStatus>("loading");
  const [manifests, setManifests] = useState<readonly ModelManifestItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setStatus("loading");
    setError(null);
    try {
      const resp: ModelManifestsResponse | null = await fetchVehicleModels(fetchImpl);
      if (resp === null) {
        // Endpoint is not configured for this stage — degrade honestly.
        setStatus("unavailable");
        return;
      }
      setManifests(resp.modelManifests);
      setStatus("ready");
    } catch (e) {
      setStatus("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [fetchImpl]);

  useEffect(() => {
    void load();
  }, [load]);

  return { status, manifests, error };
}

export type { ModelManifestItem };
