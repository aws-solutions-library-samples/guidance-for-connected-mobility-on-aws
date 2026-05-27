// Engineering fleet data: fleets, suppliers, regions, manufacturing batches.
// Used by Engineer persona views in /engineering/* routes.
// In production, these are DynamoDB records; for the demo, they are inline mocks.

export interface EngineeringFleet {
  fleetId: string;
  name: string;
  fleetType: 'engineering-prod-cohort' | 'engineering-test';
  /**
   * Tenancy classification: who owns the vehicles and the operational
   * relationship to the OEM.
   *
   * - `internal`: OEM-owned/operated. Engineering controls these vehicles
   *   directly. Full telemetry, full PII access, OTA allowed. Typical for
   *   validation fleets, employee fleets, factory test cars.
   * - `external`: customer-owned. Engineering has telemetry-based visibility
   *   (typically anonymized via the data governance layer) but does not
   *   operate them. Typical for production cohorts, dealer fleets, rental
   *   companies, commercial fleet operators.
   */
  tenantType: 'internal' | 'external';
  vehicleCount: number;
  modelLine: string;
  description: string;
}

export interface BatterySupplier {
  supplierId: string;
  supplierName: string;
  cellChemistry: string;
  ratedCapacity_kWh: number;
  thermalLimit_C: number;
  optimalTemp_C: number;
  bomCostIndex: number; // relative cost, baseline = 1.0
  qualifiedSince: string;
  notes: string;
}

export interface OperatingRegion {
  regionId: string;
  regionName: string;
  country: string;
  avgAmbientTemp_C: number;
  avgSummerPeak_C: number;
  elevation_m: number;
  climateClass: 'hot-arid' | 'hot-humid' | 'temperate' | 'cool';
}

export interface ManufacturingBatch {
  batchId: string;
  modelLine: string;
  assemblyPlantId: string;
  assemblyDateRange: { start: string; end: string };
  vehicleCount: number;
  batterySupplierId: string;
  batteryCellLot: string;
  thermalChamberTestPassRate: number;
  knownIssues: string[];
}

// ============================================================================
// FLEETS
// ============================================================================

export const ENGINEERING_FLEETS: EngineeringFleet[] = [
  {
    fleetId: 'be6-prod-cohort-001',
    name: 'BE 6 Production Cohort',
    fleetType: 'engineering-prod-cohort',
    tenantType: 'external',
    vehicleCount: 200,
    modelLine: 'BE 6',
    description:
      'In-market BE 6 production vehicles routed to product engineering for telemetry-driven analysis. 200-vehicle representative cohort across India operating regions. Customer-owned; anonymized telemetry feeds engineering insights via the data governance layer.',
  },
  {
    fleetId: 'be07-test-fleet-001',
    name: 'BE.07 Validation Fleet',
    fleetType: 'engineering-test',
    tenantType: 'internal',
    vehicleCount: 25,
    modelLine: 'BE.07',
    description:
      'Pre-production BE.07 vehicles for engineering validation. OEM-owned; operated by internal test drivers in climate chambers and hot/cold-region road trials. Instrumented telemetry tier (full CAN dump, thermal sensors, vibration). OTA + design parameter changes allowed.',
  },
];

// ============================================================================
// BATTERY CELL SUPPLIERS
// ============================================================================

export const BATTERY_SUPPLIERS: BatterySupplier[] = [
  {
    supplierId: 'SUP-VOLTRIX',
    supplierName: 'Voltrix Energy Systems',
    cellChemistry: 'NMC811',
    ratedCapacity_kWh: 79,
    thermalLimit_C: 42,
    optimalTemp_C: 25,
    bomCostIndex: 1.0,
    qualifiedSince: '2024-04-15',
    notes:
      'Datasheet rev 2.3 (effective 2025-07-01) lowered thermal limit from 45°C to 42°C. BE 6 thermal management spec was not updated to reflect change.',
  },
  {
    supplierId: 'SUP-CELLPRIME',
    supplierName: 'CellPrime Industries',
    cellChemistry: 'NMC811',
    ratedCapacity_kWh: 79,
    thermalLimit_C: 48,
    optimalTemp_C: 28,
    bomCostIndex: 1.038,
    qualifiedSince: '2024-09-22',
    notes:
      'Higher BOM cost (+3.8%) but full thermal margin against BE 6 operating envelope. Currently used in ~50% of BE 6 production batches.',
  },
];

// ============================================================================
// OPERATING REGIONS
// ============================================================================

export const OPERATING_REGIONS: OperatingRegion[] = [
  {
    regionId: 'Maharashtra-Hot',
    regionName: 'Maharashtra (Hot Climate)',
    country: 'IN',
    avgAmbientTemp_C: 32,
    avgSummerPeak_C: 44,
    elevation_m: 560,
    climateClass: 'hot-humid',
  },
  {
    regionId: 'Gujarat-Hot',
    regionName: 'Gujarat (Hot Arid)',
    country: 'IN',
    avgAmbientTemp_C: 33,
    avgSummerPeak_C: 46,
    elevation_m: 53,
    climateClass: 'hot-arid',
  },
  {
    regionId: 'Punjab-Cool',
    regionName: 'Punjab',
    country: 'IN',
    avgAmbientTemp_C: 25,
    avgSummerPeak_C: 38,
    elevation_m: 220,
    climateClass: 'temperate',
  },
  {
    regionId: 'Karnataka-Moderate',
    regionName: 'Karnataka (Bangalore)',
    country: 'IN',
    avgAmbientTemp_C: 23,
    avgSummerPeak_C: 35,
    elevation_m: 920,
    climateClass: 'temperate',
  },
  {
    regionId: 'Tamil-Nadu-Hot',
    regionName: 'Tamil Nadu (Chennai)',
    country: 'IN',
    avgAmbientTemp_C: 30,
    avgSummerPeak_C: 42,
    elevation_m: 6,
    climateClass: 'hot-humid',
  },
];

// ============================================================================
// MANUFACTURING BATCHES
// ============================================================================

export const MANUFACTURING_BATCHES: ManufacturingBatch[] = [
  // BE 6 Voltrix batches — Q3 2025 has the issue
  {
    batchId: 'BATCH-MH-Q1-2025-V01',
    modelLine: 'BE 6',
    assemblyPlantId: 'Chakan-MH',
    assemblyDateRange: { start: '2025-01-15', end: '2025-03-20' },
    vehicleCount: 28,
    batterySupplierId: 'SUP-VOLTRIX',
    batteryCellLot: 'CELL-LOT-VTX-2025-Q1-3',
    thermalChamberTestPassRate: 0.96,
    knownIssues: [],
  },
  {
    batchId: 'BATCH-MH-Q2-2025-V02',
    modelLine: 'BE 6',
    assemblyPlantId: 'Chakan-MH',
    assemblyDateRange: { start: '2025-04-08', end: '2025-06-12' },
    vehicleCount: 32,
    batterySupplierId: 'SUP-VOLTRIX',
    batteryCellLot: 'CELL-LOT-VTX-2025-Q2-5',
    thermalChamberTestPassRate: 0.95,
    knownIssues: [],
  },
  {
    batchId: 'BATCH-MH-Q3-2025-A12',
    modelLine: 'BE 6',
    assemblyPlantId: 'Chakan-MH',
    assemblyDateRange: { start: '2025-07-05', end: '2025-08-28' },
    vehicleCount: 22,
    batterySupplierId: 'SUP-VOLTRIX',
    batteryCellLot: 'CELL-LOT-VTX-2025-Q3-7',
    thermalChamberTestPassRate: 0.87,
    knownIssues: [
      'Thermal chamber pass rate below 0.92 threshold (root cause: cell datasheet rev 2.3 lowered thermal limit to 42°C; BE 6 spec assumes 45°C)',
    ],
  },
  {
    batchId: 'BATCH-MH-Q3-2025-B14',
    modelLine: 'BE 6',
    assemblyPlantId: 'Chakan-MH',
    assemblyDateRange: { start: '2025-09-01', end: '2025-10-18' },
    vehicleCount: 18,
    batterySupplierId: 'SUP-VOLTRIX',
    batteryCellLot: 'CELL-LOT-VTX-2025-Q3-9',
    thermalChamberTestPassRate: 0.88,
    knownIssues: [
      'Thermal chamber pass rate below 0.92 threshold; same root cause as BATCH-MH-Q3-2025-A12.',
    ],
  },
  // BE 6 CellPrime batches — baseline
  {
    batchId: 'BATCH-MH-Q1-2025-C03',
    modelLine: 'BE 6',
    assemblyPlantId: 'Chakan-MH',
    assemblyDateRange: { start: '2025-02-01', end: '2025-03-30' },
    vehicleCount: 35,
    batterySupplierId: 'SUP-CELLPRIME',
    batteryCellLot: 'CELL-LOT-CPR-2025-Q1-2',
    thermalChamberTestPassRate: 0.97,
    knownIssues: [],
  },
  {
    batchId: 'BATCH-MH-Q3-2025-C08',
    modelLine: 'BE 6',
    assemblyPlantId: 'Chakan-MH',
    assemblyDateRange: { start: '2025-08-15', end: '2025-10-22' },
    vehicleCount: 65,
    batterySupplierId: 'SUP-CELLPRIME',
    batteryCellLot: 'CELL-LOT-CPR-2025-Q3-4',
    thermalChamberTestPassRate: 0.96,
    knownIssues: [],
  },
  // BE.07 pre-production batches
  {
    batchId: 'BATCH-MH-Q4-2025-T01',
    modelLine: 'BE.07',
    assemblyPlantId: 'Chakan-MH',
    assemblyDateRange: { start: '2025-11-10', end: '2025-12-22' },
    vehicleCount: 12,
    batterySupplierId: 'SUP-VOLTRIX',
    batteryCellLot: 'CELL-LOT-VTX-2025-Q4-2',
    thermalChamberTestPassRate: 0.89,
    knownIssues: ['Carries forward BE 6 thermal architecture; thermal margin gap not yet resolved.'],
  },
  {
    batchId: 'BATCH-MH-Q1-2026-T02',
    modelLine: 'BE.07',
    assemblyPlantId: 'Chakan-MH',
    assemblyDateRange: { start: '2026-02-01', end: '2026-03-15' },
    vehicleCount: 13,
    batterySupplierId: 'SUP-VOLTRIX',
    batteryCellLot: 'CELL-LOT-VTX-2026-Q1-1',
    thermalChamberTestPassRate: 0.9,
    knownIssues: ['BE.07 thermal validation pending; PRD draft v0.7 has open question on supplier datasheet alignment.'],
  },
];

// ============================================================================
// HELPERS
// ============================================================================

export const getFleet = (fleetId: string) =>
  ENGINEERING_FLEETS.find((f) => f.fleetId === fleetId);

export const getSupplier = (supplierId: string) =>
  BATTERY_SUPPLIERS.find((s) => s.supplierId === supplierId);

export const getRegion = (regionId: string) =>
  OPERATING_REGIONS.find((r) => r.regionId === regionId);

export const getBatch = (batchId: string) =>
  MANUFACTURING_BATCHES.find((b) => b.batchId === batchId);

/**
 * Returns the affected cohort: vehicles in hot-climate regions on Voltrix Q3 2025 batches.
 * Used by both the anomaly detection logic and the agent's traversal step.
 */
export const AFFECTED_COHORT_FILTER = {
  regionClimateClasses: ['hot-arid', 'hot-humid'] as const,
  affectedRegionIds: ['Maharashtra-Hot', 'Gujarat-Hot'] as const,
  affectedBatchIds: ['BATCH-MH-Q3-2025-A12', 'BATCH-MH-Q3-2025-B14'] as const,
  supplierId: 'SUP-VOLTRIX',
};
