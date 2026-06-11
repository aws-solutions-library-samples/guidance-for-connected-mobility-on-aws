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
