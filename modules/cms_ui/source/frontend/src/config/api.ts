// Placeholder config for build time
// Real config is injected at deployment time via runtimeConfig.json

export interface RuntimeConfig {
  awsRegion: string;
  apiEndpoint: string;
  /**
   * WebSocket API endpoint (`wss://{id}.execute-api.{region}.amazonaws.com/live`)
   * for realtime fleet telemetry. Injected from the `WebSocketEndpoint` CFN
   * output (ui_stack BucketDeployment + generate_runtime_config.py). Optional —
   * when absent, realtime hooks fall back to REST polling.
   */
  wsEndpoint?: string;
  /**
   * Endpoint for the Virtual Service Agent (VSA) backend, served by the
   * separate `guidance-for-connected-vehicle-experience-on-aws` stack.
   * The CMS UI's web ChatAgent (`components/commons/ChatAgent.tsx`)
   * uses this to reach `/assistant/chat` for the fleet-driver voice/text
   * assistant.
   *
   * Optional. When not set we fall back to `apiEndpoint` (so a single
   * combined deployment also works).
   *
   * Not used by `VehicleHealthScoreWidget` — that widget's data now
   * comes from CMS's own `main_api` Lambda
   * (`/api/v1/vehicles/{id}/health`), moved out of this VSA backend
   * 2026-09-18 after a broken CVX-side table reference started 502ing
   * it. See
   * issues/2026-09-18-vsa-vehicle-context-points-at-nonexistent-cms-prod-table/.
   */
  vsaApiEndpoint?: string;
  dataProcessingApiEndpoint?: string;
  userPreferencesApiEndpoint: string;
  isDemoMode: string;
  cognitoDomain?: string;
  mapAuth?: {
    identityPoolClient: string;
    mapName: string;
    identityPoolId: string;
  };
  locationServices?: {
    mapName: string;
    placeIndexName: string;
    routeCalculatorName: string;
    region: string;
    enabled: boolean;
  };
  awsCredentials?: {
    region: string;
    identityPoolId: string;
    userPoolId: string;
    userPoolWebClientId: string;
  };
  /**
   * Absolute URL of the standalone DMS dealer console, injected per stage.
   *
   * Consumed ONLY by `auth/DealerPersonaBanner.tsx` to link a dealer-persona
   * user to the app that actually serves them. Absent by default — when absent
   * the banner renders without a link rather than pointing at a dead host.
   *
   * ## Not a reinstatement of `dmsApiEndpoint`
   *
   * Spec `2026-08-31-dms-standalone-ui` T4.3 deliberately REMOVED
   * `dmsApiEndpoint` / `getDmsApiEndpoint()`, because CMS no longer calls the
   * DMS API — DMS's own frontend does. This key is a different thing: a
   * human-navigable URL for a redirect hint, not an API base. Do not "clean it
   * up" as leftover, and do not use it to reach the DMS API.
   *
   * ## Why runtimeConfig and not a constant
   *
   * The first implementation hardcoded a hardcoded internal-domain host.
   * That domain does not exist (the parent zone is not ours), and
   * the internal corp domain suffix is a forbidden string — so it was caught by
   * `make ui-quick-deploy`'s pre-sync secret scan, which refused to ship the
   * bundle. Correct on both counts: the value is stage-specific and must not be
   * baked into a publishable artifact.
   */
  dmsUiUrl?: string;
  /**
   * Origin of the standalone DMS console (`https://<host>`), used for deep links
   * to a specific repair order (`utils/dmsLinks.ts`). Written by
   * `make regenerate-runtime-config` from the stage's `DMS_UI_CALLBACK_ORIGIN`;
   * omitted when unset, and callers then render the id without a link.
   * Distinct from `dmsUiUrl`, which only the dealer banner reads.
   */
  dmsUiOrigin?: string;
}

export function getRuntimeConfig(): RuntimeConfig {
  if (typeof window !== 'undefined' && (window as any).runtimeConfig) {
    return (window as any).runtimeConfig;
  }
  
  return {
    awsRegion: import.meta.env.VITE_AWS_REGION || 'us-east-1',
    apiEndpoint: import.meta.env.VITE_API_ENDPOINT || '',
    userPreferencesApiEndpoint: import.meta.env.VITE_API_ENDPOINT || '',
    isDemoMode: import.meta.env.VITE_DEMO_MODE || 'false',
  };
}

export function getApiEndpoint(): string {
  const config = getRuntimeConfig();
  return config.apiEndpoint || import.meta.env.VITE_API_ENDPOINT || '';
}

/**
 * Endpoint for the Virtual Service Agent (VSA) backend.
 *
 * Reads `runtimeConfig.vsaApiEndpoint` first, then falls back to
 * `VITE_VSA_API_ENDPOINT` (local-dev / build-time), and finally to the
 * main `apiEndpoint` so a combined deployment that hosts VSA routes
 * under the CMS UI's main API still works without extra config.
 *
 * Returns "" when nothing is configured. Callers should treat the
 * empty value as "VSA is not available in this deployment" and degrade
 * gracefully (e.g. hide the widget) rather than issue a request to a
 * relative URL.
 */
export function getVsaApiEndpoint(): string {
  const config = getRuntimeConfig();
  return (
    config.vsaApiEndpoint
    || (import.meta.env.VITE_VSA_API_ENDPOINT as string | undefined)
    || config.apiEndpoint
    || (import.meta.env.VITE_API_ENDPOINT as string | undefined)
    || ''
  );
}

export function isDemoMode(): boolean {  const config = getRuntimeConfig();
  return config.isDemoMode === 'true';
}

export function getDataProcessingApiEndpoint(): string {
  const config = getRuntimeConfig();
  return config.dataProcessingApiEndpoint || import.meta.env.VITE_DATA_PROCESSING_API_ENDPOINT || '';
}

/**
 * Absolute URL of the standalone DMS dealer console for this stage, or
 * `undefined` when not configured.
 *
 * Callers MUST treat `undefined` as "no DMS console configured here" and render
 * without a link. Do NOT substitute a default host: the previous hardcoded
 * default pointed at a domain that does not exist and tripped the pre-sync
 * secret scan. See `RuntimeConfig.dmsUiUrl`.
 */
export function getDmsUiUrl(): string | undefined {
  const config = getRuntimeConfig();
  return config.dmsUiUrl;
}
