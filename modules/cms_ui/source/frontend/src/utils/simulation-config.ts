// Simulation API endpoint configuration
// Users can toggle between local (localhost:5001) and cloud (ALB/CloudFront) in the UI

const LOCAL_ENDPOINT = 'http://localhost:5001';
const STORAGE_KEY = 'cms-simulation-mode'; // 'local' | 'cloud'

export type SimulationMode = 'local' | 'cloud';

export function getSimulationMode(): SimulationMode {
  const rc = (window as any).runtimeConfig;
  // Default to cloud when endpoint is configured
  if (rc?.simulationApiEndpoint) {
    const stored = localStorage.getItem(STORAGE_KEY) as SimulationMode;
    return stored || 'cloud';
  }
  return 'local';
}

export function setSimulationMode(mode: SimulationMode) {
  localStorage.setItem(STORAGE_KEY, mode);
}

export function getSimulationApiBase(): string {
  const mode = getSimulationMode();
  if (mode === 'cloud') {
    const rc = (window as any).runtimeConfig;
    return rc?.simulationApiEndpoint || LOCAL_ENDPOINT;
  }
  return LOCAL_ENDPOINT;
}

export function getSimulationApiUrl(path: string): string {
  return `${getSimulationApiBase()}/api/simulation${path}`;
}

export function isCloudSimAvailable(): boolean {
  return !!(window as any).runtimeConfig?.simulationApiEndpoint;
}
