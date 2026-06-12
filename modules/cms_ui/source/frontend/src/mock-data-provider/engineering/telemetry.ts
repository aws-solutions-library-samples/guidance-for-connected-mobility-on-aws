// Synthetic battery State-of-Health (SoH) telemetry with planted degradation signal.
// Affected cohort (Maharashtra-Hot/Gujarat-Hot + Voltrix Q3 2025 batches) shows
// 12.2% accelerated degradation vs baseline.

import { ALL_ENGINEERING_VEHICLES, EngineeringVehicle } from './vehicles';
import { getRegion, getBatch, getSupplier } from './fleet-data';

export interface DailyTelemetryAggregate {
  vehicleId: string;
  date: string; // YYYY-MM-DD
  batterySoH_pct: number;
  batteryTempPeak_C: number;
  batteryTempAvg_C: number;
  ambientTempAvg_C: number;
  chargeCycles: number;
  range_km: number;
  thermalEventsCount: number;
}

// ============================================================================
// CONFIGURATION
// ============================================================================

const HISTORY_DAYS = 180; // 6 months
const TODAY = new Date('2026-05-19');
const BASELINE_DEGRADATION_PCT_PER_MONTH = 0.9;
const AFFECTED_DEGRADATION_PCT_PER_MONTH = 1.01; // +12.2%

// ============================================================================
// SEEDED RNG (deterministic for repeatable demo)
// ============================================================================

function mulberry32(seed: number) {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function seedFromVehicleId(vehicleId: string): number {
  let h = 2166136261;
  for (let i = 0; i < vehicleId.length; i++) {
    h ^= vehicleId.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

// ============================================================================
// TELEMETRY GENERATION
// ============================================================================

function generateDailyTelemetry(vehicle: EngineeringVehicle): DailyTelemetryAggregate[] {
  const region = getRegion(vehicle.operatingRegionId);
  const batch = getBatch(vehicle.manufacturingBatchId);
  const supplier = batch ? getSupplier(batch.batterySupplierId) : null;
  if (!region || !batch || !supplier) return [];

  const rng = mulberry32(seedFromVehicleId(vehicle.vehicleId));
  const series: DailyTelemetryAggregate[] = [];
  const startSoH = 100;
  let currentSoH = startSoH;
  const monthlyDegradation = vehicle.isAffectedCohort
    ? AFFECTED_DEGRADATION_PCT_PER_MONTH
    : BASELINE_DEGRADATION_PCT_PER_MONTH;
  const dailyDegradation = monthlyDegradation / 30;

  for (let dayOffset = HISTORY_DAYS; dayOffset >= 0; dayOffset--) {
    const date = new Date(TODAY);
    date.setDate(date.getDate() - dayOffset);
    const dateStr = date.toISOString().slice(0, 10);

    // Seasonal ambient temp curve (May 2026 = end of Indian summer)
    const dayOfYear = Math.floor(
      (date.getTime() - new Date(date.getFullYear(), 0, 0).getTime()) / 86400000,
    );
    // Peak in mid-May (day ~135), trough in mid-January (day ~15)
    const seasonalFactor = Math.cos(((dayOfYear - 135) / 365) * 2 * Math.PI);
    const ambientTemp =
      region.avgAmbientTemp_C +
      ((region.avgSummerPeak_C - region.avgAmbientTemp_C) / 2) * (1 - seasonalFactor) +
      (rng() - 0.5) * 4; // ±2°C daily noise

    // Battery temp: ambient + thermal load. Affected cohort runs hotter.
    const thermalLoad = vehicle.isAffectedCohort
      ? 8 + rng() * 3 // hotter delta during operation
      : 5 + rng() * 2;
    const batteryTempAvg = ambientTemp + thermalLoad;
    const batteryTempPeak = batteryTempAvg + 4 + rng() * 3;

    // Thermal events: temps exceeding supplier thermal_limit
    const thermalEvents =
      batteryTempPeak > supplier.thermalLimit_C
        ? Math.floor((batteryTempPeak - supplier.thermalLimit_C) * (vehicle.isAffectedCohort ? 0.6 : 0.2))
        : 0;

    currentSoH -= dailyDegradation + (rng() - 0.5) * 0.005;
    // Extra hit on hot days for affected cohort (correlation reinforcement)
    if (vehicle.isAffectedCohort && batteryTempPeak > supplier.thermalLimit_C + 3) {
      currentSoH -= 0.008;
    }
    currentSoH = Math.max(currentSoH, 70);

    const chargeCycles = Math.floor(dayOffset * 0.7) + Math.floor(rng() * 2);
    const baseRange = 480; // km full charge new
    const range = baseRange * (currentSoH / 100) * (0.95 + rng() * 0.1);

    series.push({
      vehicleId: vehicle.vehicleId,
      date: dateStr,
      batterySoH_pct: Math.round(currentSoH * 100) / 100,
      batteryTempPeak_C: Math.round(batteryTempPeak * 10) / 10,
      batteryTempAvg_C: Math.round(batteryTempAvg * 10) / 10,
      ambientTempAvg_C: Math.round(ambientTemp * 10) / 10,
      chargeCycles,
      range_km: Math.round(range),
      thermalEventsCount: thermalEvents,
    });
  }
  return series;
}

// ============================================================================
// CACHED EXPORTS
// ============================================================================

let _cache: Map<string, DailyTelemetryAggregate[]> | null = null;

function buildCache(): Map<string, DailyTelemetryAggregate[]> {
  const cache = new Map<string, DailyTelemetryAggregate[]>();
  for (const vehicle of ALL_ENGINEERING_VEHICLES) {
    cache.set(vehicle.vehicleId, generateDailyTelemetry(vehicle));
  }
  return cache;
}

export function getTelemetryForVehicle(vehicleId: string): DailyTelemetryAggregate[] {
  if (!_cache) _cache = buildCache();
  return _cache.get(vehicleId) || [];
}

export function getTelemetryForFleet(fleetId: string): DailyTelemetryAggregate[] {
  return ALL_ENGINEERING_VEHICLES.filter((v) => v.fleetId === fleetId).flatMap((v) =>
    getTelemetryForVehicle(v.vehicleId),
  );
}

/**
 * Generate telemetry from arbitrary vehicle metadata — used by the engineering
 * vehicle detail page to render telemetry for DynamoDB-seeded vehicles whose
 * IDs (`VEH-BE6-NNNN`) don't match the in-memory `ALL_ENGINEERING_VEHICLES`
 * scheme (`VH-BE6-NNNN`). Generates on-the-fly using the same algorithm.
 *
 * Pass the engineering metadata fields stored on the DynamoDB vehicle record:
 *   regionId — joins to OPERATING_REGIONS by regionId
 *   manufacturingBatchId — joins to MANUFACTURING_BATCHES by batchId
 *   isAffectedCohort — drives the +12.2% degradation curve when true
 *   vehicleId — used as RNG seed (deterministic per vehicle)
 *
 * If the region or batch ID isn't found, returns an empty array (renders
 * empty-state in the chart).
 */
export function getTelemetryFromMetadata(meta: {
  vehicleId: string;
  isAffectedCohort: boolean;
  regionId: string;
  manufacturingBatchId: string;
}): DailyTelemetryAggregate[] {
  const region = getRegion(meta.regionId);
  const batch = getBatch(meta.manufacturingBatchId);
  if (!region || !batch) return [];

  // Build a synthetic EngineeringVehicle stub — generateDailyTelemetry only
  // reads operatingRegionId, manufacturingBatchId, vehicleId, isAffectedCohort.
  const stub: EngineeringVehicle = {
    vehicleId: meta.vehicleId,
    vin: '',
    make: '',
    model: '',
    year: 2025,
    fleetId: '',
    manufacturingBatchId: meta.manufacturingBatchId,
    assemblyDate: '',
    assemblyPlantId: '',
    operatingRegionId: meta.regionId,
    vehicleEnvironment: 'production',
    telemetryTier: 'standard',
    isAffectedCohort: meta.isAffectedCohort,
  };
  return generateDailyTelemetry(stub);
}

// ============================================================================
// AGGREGATE METRICS for the demo
// ============================================================================

/**
 * The agent's headline number: average SoH degradation rate (% per month)
 * for a given vehicle filter, computed from the latest 90 days of telemetry.
 */
export function avgSoHDegradationRatePerMonth(vehicles: EngineeringVehicle[]): number {
  if (vehicles.length === 0) return 0;
  const rates = vehicles.map((v) => {
    const series = getTelemetryForVehicle(v.vehicleId);
    if (series.length < 60) return 0;
    const recent = series.slice(-90);
    const startSoH = recent[0].batterySoH_pct;
    const endSoH = recent[recent.length - 1].batterySoH_pct;
    const months = recent.length / 30;
    return (startSoH - endSoH) / months;
  });
  return rates.reduce((a, b) => a + b, 0) / rates.length;
}

/**
 * Cohort-level summary for the InvestigationWorkspace center panel charts.
 */
export interface CohortSummary {
  vehicleCount: number;
  avgDegradationPctPerMonth: number;
  avgPeakBatteryTemp_C: number;
  totalThermalEventsLast30Days: number;
}

export function summariseCohort(vehicles: EngineeringVehicle[]): CohortSummary {
  if (vehicles.length === 0) {
    return {
      vehicleCount: 0,
      avgDegradationPctPerMonth: 0,
      avgPeakBatteryTemp_C: 0,
      totalThermalEventsLast30Days: 0,
    };
  }
  const peakTemps: number[] = [];
  let thermalEvents30d = 0;
  for (const v of vehicles) {
    const recent = getTelemetryForVehicle(v.vehicleId).slice(-30);
    const peakAvg =
      recent.reduce((a, b) => a + b.batteryTempPeak_C, 0) / Math.max(recent.length, 1);
    peakTemps.push(peakAvg);
    thermalEvents30d += recent.reduce((a, b) => a + b.thermalEventsCount, 0);
  }
  return {
    vehicleCount: vehicles.length,
    avgDegradationPctPerMonth: avgSoHDegradationRatePerMonth(vehicles),
    avgPeakBatteryTemp_C: peakTemps.reduce((a, b) => a + b, 0) / peakTemps.length,
    totalThermalEventsLast30Days: thermalEvents30d,
  };
}
