// Utility to get API configuration from runtime config
export function getApiEndpoint(): string {
  // Try environment variables first
  const envApiEndpoint = import.meta.env.VITE_API_BASE_URL || import.meta.env.VITE_API_ENDPOINT;
  if (envApiEndpoint) {
    return envApiEndpoint;
  }
  
  // Try to get from window.runtimeConfig
  const runtimeConfig = (window as any).runtimeConfig;
  if (runtimeConfig?.apiEndpoint) {
    return runtimeConfig.apiEndpoint;
  }
  
  // Fallback to local development
  return 'http://localhost:5001';
}

export function getApiConfig() {
  const runtimeConfig = (window as any).runtimeConfig;
  return {
    apiEndpoint: getApiEndpoint(),
    isDemoMode: runtimeConfig?.isDemoMode || import.meta.env.VITE_LOCAL_DEMO || 'false',
    awsRegion: runtimeConfig?.awsRegion || 'us-east-1'
  };
}

/**
 * Returns `runtimeConfig.commandsApiEndpoint` with any trailing slash stripped.
 *
 * The CFN Output `CommandsApiUrl` (consumed by `runtimeConfig`) carries a
 * trailing slash (e.g. `https://xxxxx.execute-api.us-east-1.amazonaws.com/prod/`).
 * Callers concatenate a leading-slash path (`/api/commands/...`), which produces
 * a cosmetic double-slash in the resulting URL. API Gateway tolerates it today,
 * but it is a latent trap for any path-sensitive route matching, logging, or
 * access log parsing.
 *
 * Client-side normalisation is preferred over changing the CFN Output shape —
 * the Output is consumed by other clients whose behaviour is not audited here.
 * Same pattern as `getApiBaseUrl()` in `api/real-fleet-client.ts`.
 *
 * Returns an empty string when `runtimeConfig.commandsApiEndpoint` is unset —
 * matches the prior inline `... || ''` fallback used by call sites.
 */
export function getCommandsApiBase(): string {
  const endpoint = (window as any).runtimeConfig?.commandsApiEndpoint || '';
  return endpoint.replace(/\/+$/, '');
}
