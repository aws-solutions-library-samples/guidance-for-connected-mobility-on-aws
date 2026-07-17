// Synthetic vehicles for the Engineer persona demo.
// 200 BE 6 in production cohort + 25 BE.07 in test fleet.
// Includes the planted degradation signal: Maharashtra/Gujarat + Voltrix Q3 2025 batches.

import { AFFECTED_COHORT_FILTER, MANUFACTURING_BATCHES } from './fleet-data';

export interface EngineeringVehicle {
  vehicleId: string;
  vin: string;
  make: string;
  model: string;
  year: number;
  fleetId: string;
  manufacturingBatchId: string;
  assemblyDate: string;
  assemblyPlantId: string;
  operatingRegionId: string;
  vehicleEnvironment: 'production' | 'test' | 'validation';
  telemetryTier: 'standard' | 'instrumented';
  // Computed at generation time, used by agent traversal:
  isAffectedCohort: boolean;
}

// ============================================================================
// VIN GENERATOR (deterministic for repeatable demo)
// ============================================================================

const HEX = '0123456789ABCDEFGHJKLMNPRSTUVWXYZ';
function syntheticVin(seed: number, model: string): string {
  // Acme Motors WMI: AME (placeholder); not real allocations — for demo only.
  const wmi = 'AME';
  const rest = Array.from({ length: 14 }, (_, i) => HEX[(seed * 13 + i * 7) % HEX.length]).join('');
  const modelTag = model === 'BE 6' ? 'BE6' : 'B07';
  return `${wmi}${modelTag}${rest}`.slice(0, 17);
}

// ============================================================================
// BE 6 PRODUCTION COHORT (200 vehicles)
// ============================================================================

interface RegionDistribution {
  regionId: string;
  count: number;
}

const BE6_REGION_DISTRIBUTION: RegionDistribution[] = [
  { regionId: 'Maharashtra-Hot', count: 80 },
  { regionId: 'Gujarat-Hot', count: 50 },
  { regionId: 'Punjab-Cool', count: 40 },
  { regionId: 'Karnataka-Moderate', count: 30 },
];

// Within each region, batches distribute by their vehicleCount weights.
// To keep totals at 200 we'll deterministically assign batches per vehicle.
const BE6_BATCHES = MANUFACTURING_BATCHES.filter((b) => b.modelLine === 'BE 6');

function pickBatchForVehicle(vehicleIndex: number) {
  // Deterministic round-robin weighted by batch vehicleCount
  const weights = BE6_BATCHES.map((b) => b.vehicleCount);
  const total = weights.reduce((a, b) => a + b, 0);
  let cursor = vehicleIndex % total;
  for (let i = 0; i < BE6_BATCHES.length; i++) {
    if (cursor < weights[i]) return BE6_BATCHES[i];
    cursor -= weights[i];
  }
  return BE6_BATCHES[0];
}

function generateBE6Cohort(): EngineeringVehicle[] {
  const vehicles: EngineeringVehicle[] = [];
  let globalIdx = 0;
  for (const region of BE6_REGION_DISTRIBUTION) {
    for (let i = 0; i < region.count; i++) {
      const batch = pickBatchForVehicle(globalIdx);
      const assemblyDate = batch.assemblyDateRange.start;
      const isAffected =
        (AFFECTED_COHORT_FILTER.affectedRegionIds as readonly string[]).includes(region.regionId) &&
        (AFFECTED_COHORT_FILTER.affectedBatchIds as readonly string[]).includes(batch.batchId);
      vehicles.push({
        vehicleId: `VH-BE6-${String(globalIdx + 1).padStart(4, '0')}`,
        vin: syntheticVin(globalIdx, 'BE 6'),
        make: 'Acme Motors',
        model: 'BE 6',
        year: 2025,
        fleetId: 'be6-prod-cohort-001',
        manufacturingBatchId: batch.batchId,
        assemblyDate,
        assemblyPlantId: batch.assemblyPlantId,
        operatingRegionId: region.regionId,
        vehicleEnvironment: 'production',
        telemetryTier: 'standard',
        isAffectedCohort: isAffected,
      });
      globalIdx++;
    }
  }
  return vehicles;
}

// ============================================================================
// BE.07 TEST FLEET (25 vehicles)
// ============================================================================

const BE07_BATCHES = MANUFACTURING_BATCHES.filter((b) => b.modelLine === 'BE.07');

const BE07_REGION_DISTRIBUTION: RegionDistribution[] = [
  { regionId: 'Maharashtra-Hot', count: 6 }, // hot-region road testing
  { regionId: 'Gujarat-Hot', count: 5 }, // hot-arid validation
  { regionId: 'Tamil-Nadu-Hot', count: 5 }, // hot-humid validation
  { regionId: 'Punjab-Cool', count: 5 }, // cold/temperate testing
  { regionId: 'Karnataka-Moderate', count: 4 }, // baseline reference
];

function generateBE07TestFleet(): EngineeringVehicle[] {
  const vehicles: EngineeringVehicle[] = [];
  let globalIdx = 0;
  for (const region of BE07_REGION_DISTRIBUTION) {
    for (let i = 0; i < region.count; i++) {
      const batch = BE07_BATCHES[globalIdx % BE07_BATCHES.length];
      vehicles.push({
        vehicleId: `VH-BE07-${String(globalIdx + 1).padStart(3, '0')}`,
        vin: syntheticVin(1000 + globalIdx, 'BE.07'),
        make: 'Acme Motors',
        model: 'BE.07',
        year: 2026,
        fleetId: 'be07-test-fleet-001',
        manufacturingBatchId: batch.batchId,
        assemblyDate: batch.assemblyDateRange.start,
        assemblyPlantId: batch.assemblyPlantId,
        operatingRegionId: region.regionId,
        vehicleEnvironment: 'test',
        telemetryTier: 'instrumented',
        isAffectedCohort: false,
      });
      globalIdx++;
    }
  }
  return vehicles;
}

// ============================================================================
// EXPORTS
// ============================================================================

export const BE6_PROD_COHORT_VEHICLES = generateBE6Cohort();
export const BE07_TEST_FLEET_VEHICLES = generateBE07TestFleet();
export const ALL_ENGINEERING_VEHICLES = [
  ...BE6_PROD_COHORT_VEHICLES,
  ...BE07_TEST_FLEET_VEHICLES,
];

export const getVehiclesByFleet = (fleetId: string) =>
  ALL_ENGINEERING_VEHICLES.filter((v) => v.fleetId === fleetId);

export const getAffectedCohort = () =>
  BE6_PROD_COHORT_VEHICLES.filter((v) => v.isAffectedCohort);

// Sanity: report cohort sizes (used in tests / debugging)
export const COHORT_STATS = {
  be6Total: BE6_PROD_COHORT_VEHICLES.length,
  be6Affected: BE6_PROD_COHORT_VEHICLES.filter((v) => v.isAffectedCohort).length,
  be07Total: BE07_TEST_FLEET_VEHICLES.length,
  byRegion: BE6_PROD_COHORT_VEHICLES.reduce<Record<string, number>>((acc, v) => {
    acc[v.operatingRegionId] = (acc[v.operatingRegionId] || 0) + 1;
    return acc;
  }, {}),
  affectedByRegion: BE6_PROD_COHORT_VEHICLES.filter((v) => v.isAffectedCohort).reduce<Record<string, number>>((acc, v) => {
    acc[v.operatingRegionId] = (acc[v.operatingRegionId] || 0) + 1;
    return acc;
  }, {}),
};
