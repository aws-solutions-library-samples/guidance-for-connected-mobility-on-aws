// Placeholder config for build time
// Real config is injected at deployment time via runtimeConfig.json

export interface RuntimeConfig {
  awsRegion: string;
  apiEndpoint: string;
  userPreferencesApiEndpoint: string;
  isDemoMode: string;
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

export function isDemoMode(): boolean {
  const config = getRuntimeConfig();
  return config.isDemoMode === 'true';
}
