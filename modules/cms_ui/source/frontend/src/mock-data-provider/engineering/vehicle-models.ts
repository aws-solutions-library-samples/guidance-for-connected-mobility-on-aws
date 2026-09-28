// Vehicle Models — FleetWise Model Manifests for the AM-100 / AM-200 platforms.
// A vehicle model is a curated subset of the signal catalog that names which
// signals THIS platform emits. Pairs 1:1 with a decoder manifest version that
// defines how those signals are decoded off the CAN/Ethernet bus.
//
// In production this lives in AWS IoT FleetWise and is queried via the
// ListModelManifests API. For the demo, it's derived from ECU_CONFIGS in
// ecus.ts so we don't duplicate the truth.

import { ECU_CATALOG, ECU_CONFIGS, ECU_LIST, type ECUId } from './ecus';

export interface VehicleModelECUEntry {
  ecu: ECUId;
  displayName: string;
  baselineVersion: string;
  signalCount: number;
}

export interface VehicleModel {
  /** FleetWise Model Manifest name. */
  modelId: string;
  /** Display label for the UI. */
  name: string;
  modelLine: string;
  platform: string;
  status: 'ACTIVE' | 'DRAFT' | 'DEPRECATED';
  /** "Production" / "Validation" framing for engineering audiences. */
  productionPhase: 'production' | 'validation';
  description: string;
  /** Paired decoder manifest version. */
  decoderManifestRef: string;
  /** ARN-like reference to the source signal catalog. */
  signalCatalogArn: string;
  /** Total signals included in this vehicle model. */
  signalCount: number;
  /** ECUs included in this vehicle model with their baseline FW versions. */
  ecus: VehicleModelECUEntry[];
  /** Number of fleet vehicles currently running this model. */
  vehicleCount: number;
  /** Linked fleet ID(s). */
  fleetIds: string[];
  /** ISO timestamp of last modification. */
  lastModified: string;
  /** ECU config ID this model derives from (for joining to vehicle records). */
  ecuConfigId: string;
}

function buildEcuEntries(configId: string): VehicleModelECUEntry[] {
  const config = ECU_CONFIGS[configId];
  if (!config) return [];
  return config.baselineVersions.map((spec) => ({
    ecu: spec.ecu,
    displayName: ECU_CATALOG[spec.ecu].displayName,
    baselineVersion: spec.version,
    signalCount: ECU_CATALOG[spec.ecu].signalCount,
  }));
}

const TOTAL_AM100_SIGNALS  = ECU_LIST.reduce((s, e) => s + e.signalCount, 0);
const TOTAL_BE07_SIGNALS = ECU_LIST.reduce((s, e) => s + e.signalCount, 0) + 22; // +instrumented dev signals

export const VEHICLE_MODELS: VehicleModel[] = [
  {
    modelId:           'AM100-V12-PROD',
    name:              'AM-100 — Production v12',
    modelLine:         'AM-100',
    platform:          'INGLO',
    status:            'ACTIVE',
    productionPhase:   'production',
    description:
      'Production vehicle model for the in-market AM-100 cohort. Manifests every signal emitted by 200 production vehicles operating across the demo region. Updated 2026-05-19 by Build #4823 — added 2 BMS-produced signals (ThermalCompensationFactor, DerateActiveSeconds) for thermal-derate observability.',
    decoderManifestRef: 'cms-prod-decoder-manifest-v17',
    signalCatalogArn:  'arn:aws:iotfleetwise:us-east-1:000000000000:signal-catalog/cms-prod-vss',
    signalCount:        TOTAL_AM100_SIGNALS,
    ecus:               buildEcuEntries('ECU-CONFIG-AM100-V12-PROD'),
    vehicleCount:       200,
    fleetIds:           ['be6-prod-cohort-001'],
    lastModified:       '2026-05-19T15:50:00Z',
    ecuConfigId:        'ECU-CONFIG-AM100-V12-PROD',
  },
  {
    modelId:           'BE07-V13-DEV',
    name:              'AM-200 — Validation v13',
    modelLine:         'AM-200',
    platform:          'INGLO',
    status:            'DRAFT',
    productionPhase:   'validation',
    description:
      'Engineering / validation vehicle model for the AM-200 prototype fleet. Includes 22 additional development-only signals (instrumented telemetry tier) not present in the production AM-100 model. Used by the 25-vehicle Pune R&D validation fleet. Iterates with each rc1/rc2/rc3 firmware release.',
    decoderManifestRef: 'cms-be07-decoder-manifest-v23',
    signalCatalogArn:  'arn:aws:iotfleetwise:us-east-1:000000000000:signal-catalog/cms-prod-vss',
    signalCount:        TOTAL_BE07_SIGNALS,
    ecus:               buildEcuEntries('ECU-CONFIG-BE07-V13-DEV'),
    vehicleCount:       25,
    fleetIds:           ['be07-test-fleet-001'],
    lastModified:       '2026-05-12T09:18:00Z',
    ecuConfigId:        'ECU-CONFIG-BE07-V13-DEV',
  },
];

export function getVehicleModel(modelId: string): VehicleModel | undefined {
  return VEHICLE_MODELS.find((m) => m.modelId === modelId);
}

export function getVehicleModelByEcuConfig(ecuConfigId: string): VehicleModel | undefined {
  return VEHICLE_MODELS.find((m) => m.ecuConfigId === ecuConfigId);
}
