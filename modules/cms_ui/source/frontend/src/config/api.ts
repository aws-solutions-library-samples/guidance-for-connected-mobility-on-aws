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
   * The CMS UI uses this URL to fetch `/vehicles/{id}/context`, which
   * returns the server-computed `healthScore` + breakdown that the
   * Vehicle Detail page renders alongside its other widgets.
   *
   * Optional. When not set we fall back to `apiEndpoint` (so a single
   * combined deployment also works), and the VehicleHealthScoreWidget
   * silently hides itself if neither endpoint serves the route.
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

export function isDemoMode(): boolean {
  const config = getRuntimeConfig();
  return config.isDemoMode === 'true';
}

export function getDataProcessingApiEndpoint(): string {
  const config = getRuntimeConfig();
  return config.dataProcessingApiEndpoint || import.meta.env.VITE_DATA_PROCESSING_API_ENDPOINT || '';
}
