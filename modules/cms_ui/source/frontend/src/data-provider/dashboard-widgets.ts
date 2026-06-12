// Real data provider — fetches widget data from /api/v1/dashboard/widgets
import { getRuntimeConfig } from '../config/api';

let cachedData: any = null;
let cacheTime = 0;
const CACHE_TTL = 30000; // 30s

async function fetchWidgetData(): Promise<any> {
  const now = Date.now();
  if (cachedData && now - cacheTime < CACHE_TTL) return cachedData;

  try {
    const api = getRuntimeConfig().apiEndpoint.replace(/\/$/, '');
    const resp = await fetch(`${api}/api/v1/dashboard/widgets`);
    if (resp.ok) {
      cachedData = await resp.json();
      cacheTime = now;
      return cachedData;
    }
  } catch (e) {
    console.error('Failed to fetch widget data:', e);
  }
  return cachedData || {};
}

export type VehicleHealthData = { title: string; value: number };
export type ScoreData = { title: string; value: number };
export type HardBrakingEventData = { date: Date; numEvents: number };
export type DistanceDrivenData = { x: Date; y: number };
export type UtilizationData = { title: string; value: number };

export const fetchVehicleHealth = async (_fleetId?: string): Promise<VehicleHealthData[]> => {
  const d = await fetchWidgetData();
  return d.vehicleHealth || [];
};

export const fetchDriverScores = async (_fleetId?: string): Promise<ScoreData[]> => {
  const d = await fetchWidgetData();
  return d.driverScores || [];
};

export const fetchHardBrakingEvents = async (_fleetId?: string): Promise<HardBrakingEventData[]> => {
  const d = await fetchWidgetData();
  return (d.brakingEvents || []).map((e: any) => ({ date: new Date(e.date), numEvents: e.numEvents }));
};

export const fetchDistanceDriven = async (_fleetId?: string): Promise<DistanceDrivenData[]> => {
  const d = await fetchWidgetData();
  return (d.distanceDriven || []).map((e: any) => ({ x: new Date(e.date), y: e.miles }));
};

export const fetchVehicleUtilization = async (_fleetId?: string): Promise<UtilizationData[]> => {
  const d = await fetchWidgetData();
  return d.utilization || [];
};
