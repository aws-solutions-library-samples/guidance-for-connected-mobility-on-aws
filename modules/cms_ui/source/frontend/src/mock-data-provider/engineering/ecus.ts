// Engineering ECU data — ECU catalog, ECU configurations per platform, and
// per-vehicle current ECU software state. Used by the ECUs tab on the
// engineering vehicle detail page, the OTA rollout view, and the signal
// catalog ECU annotations.
//
// In production this would live in:
//   - PLM / Configuration Management (ECU catalog, configs)
//   - Device Management / OTA service (per-vehicle current versions)
// For the demo, it's joined client-side with the DynamoDB vehicle by ecuConfigId.

// ============================================================================
// ECU CATALOG — definitions, vendor, role
// ============================================================================

export type ECUId =
  | 'TCU'    // Telematics Control Unit
  | 'BMS'    // Battery Management System
  | 'VCU'    // Vehicle Control Unit
  | 'BCM'    // Body Control Module
  | 'ADAS'   // Advanced Driver Assistance Domain Controller
  | 'IVI'    // Infotainment / Cockpit
  | 'GW'     // Central Gateway
  | 'CCU';   // Charger Control Unit

export interface ECUDefinition {
  id: ECUId;
  displayName: string;
  vendor: string;
  partNumberFamily: string;
  domain: 'Powertrain' | 'Body' | 'ADAS' | 'Connectivity' | 'Infotainment' | 'Energy';
  description: string;
  /** Whether this ECU is OTA-updatable. */
  otaCapable: boolean;
  /** Approximate count of signals this ECU produces in the catalog. */
  signalCount: number;
}

export const ECU_CATALOG: Record<ECUId, ECUDefinition> = {
  TCU: {
    id: 'TCU',
    displayName: 'Telematics Control Unit',
    vendor: 'Acme Motors',
    partNumberFamily: 'MFG-TCU-G2',
    domain: 'Connectivity',
    description: 'Cellular modem, OTA receiver, vehicle gateway to AWS IoT FleetWise. Hosts the AWS IoT FleetWise Edge Agent and acts as the OTA campaign endpoint.',
    otaCapable: true,
    signalCount: 18,
  },
  BMS: {
    id: 'BMS',
    displayName: 'Battery Management System',
    vendor: 'Acme Motors (HW) / In-House (SW)',
    partNumberFamily: 'MFG-BMS-INGLO-V2',
    domain: 'Energy',
    description: 'High-voltage battery pack monitoring, cell balancing, thermal management, contactor control. Owns SoC/SoH estimation and the thermal compensation algorithm.',
    otaCapable: true,
    signalCount: 64,
  },
  VCU: {
    id: 'VCU',
    displayName: 'Vehicle Control Unit',
    vendor: 'Bosch',
    partNumberFamily: 'BSH-VCU-INGLO',
    domain: 'Powertrain',
    description: 'Top-level powertrain controller. Coordinates BMS, inverter, motor, brake regen, and torque arbitration.',
    otaCapable: true,
    signalCount: 42,
  },
  BCM: {
    id: 'BCM',
    displayName: 'Body Control Module',
    vendor: 'Continental',
    partNumberFamily: 'CON-BCM-INGLO',
    domain: 'Body',
    description: 'Lighting, locks, windows, HVAC actuators, comfort features. CAN-only, no direct telemetry to cloud.',
    otaCapable: true,
    signalCount: 28,
  },
  ADAS: {
    id: 'ADAS',
    displayName: 'ADAS Domain Controller',
    vendor: 'Acme Motors (HW) / NVIDIA Drive (SW)',
    partNumberFamily: 'MFG-ADAS-DRIVE-V1',
    domain: 'ADAS',
    description: 'Sensor fusion, perception, planning. Drives Level 2+ assistance features. NVIDIA Drive Orin SoC.',
    otaCapable: true,
    signalCount: 36,
  },
  IVI: {
    id: 'IVI',
    displayName: 'Infotainment & Cluster',
    vendor: 'Visteon',
    partNumberFamily: 'VIS-COCKPIT-INGLO',
    domain: 'Infotainment',
    description: 'Two displays (driver cluster + center stack), Android Automotive runtime, voice assistant, navigation.',
    otaCapable: true,
    signalCount: 12,
  },
  GW: {
    id: 'GW',
    displayName: 'Central Gateway',
    vendor: 'Continental',
    partNumberFamily: 'CON-GW-INGLO',
    domain: 'Connectivity',
    description: 'Inter-domain CAN/Ethernet routing, security firewall, secure-onboard-communication enforcement.',
    otaCapable: true,
    signalCount: 8,
  },
  CCU: {
    id: 'CCU',
    displayName: 'Charger Control Unit',
    vendor: 'LG Innotek',
    partNumberFamily: 'LGI-OBC-INGLO-79',
    domain: 'Energy',
    description: 'On-board AC charger control, DC fast-charge handshake, charge port lock, ISO 15118 plug-and-charge.',
    otaCapable: true,
    signalCount: 22,
  },
};

export const ECU_LIST: ECUDefinition[] = Object.values(ECU_CATALOG);

// ============================================================================
// ECU CONFIGURATIONS — per platform/build
// ============================================================================

export interface ECUVersionSpec {
  ecu: ECUId;
  version: string;
  releasedAt: string;
  signedBy: string;
  releaseNotes?: string;
}

export interface ECUConfig {
  configId: string;
  description: string;
  modelLine: string;
  /** Versions an unaffected vehicle on this config currently runs. */
  baselineVersions: ECUVersionSpec[];
}

/**
 * BE 6 production configuration — what production cohort vehicles SHOULD run.
 * Affected-cohort BMS is one minor version behind (v3.2.0 vs v3.2.1) — the
 * v3.2.1 was a small fix that didn't address the thermal-limit issue revealed
 * by Q3 2025 Voltrix cell datasheet rev 2.3.
 */
export const ECU_CONFIG_BE6_V12_PROD: ECUConfig = {
  configId: 'ECU-CONFIG-BE6-V12-PROD',
  description: 'BE 6 production platform v12 — released for general production 2025-04.',
  modelLine: 'BE 6',
  baselineVersions: [
    { ecu: 'TCU',  version: '4.1.0',  releasedAt: '2025-03-15', signedBy: 'acme-ota-prod' },
    { ecu: 'BMS',  version: '3.2.1',  releasedAt: '2025-04-22', signedBy: 'acme-ota-prod', releaseNotes: 'Cell balancing tuning. Does NOT address Voltrix datasheet rev 2.3 thermal limit drop (42°C → was 45°C in BE 6 spec).' },
    { ecu: 'VCU',  version: '7.4.2',  releasedAt: '2025-04-01', signedBy: 'bosch-prod' },
    { ecu: 'BCM',  version: '2.8.5',  releasedAt: '2024-12-10', signedBy: 'continental-prod' },
    { ecu: 'ADAS', version: '2.1.3',  releasedAt: '2025-04-18', signedBy: 'nvidia-acme-prod' },
    { ecu: 'IVI',  version: '14.2.0', releasedAt: '2025-05-02', signedBy: 'visteon-prod' },
    { ecu: 'GW',   version: '1.5.0',  releasedAt: '2025-01-08', signedBy: 'continental-prod' },
    { ecu: 'CCU',  version: '2.3.1',  releasedAt: '2025-02-14', signedBy: 'lg-innotek-prod' },
  ],
};

/**
 * BE.07 validation configuration — what test fleet vehicles run.
 * Notably: BMS v3.3.0-rc2 — the validation fleet has already validated the
 * thermal-compensation fix that the OTA pipeline is now rolling out to BE 6.
 */
export const ECU_CONFIG_BE07_V13_DEV: ECUConfig = {
  configId: 'ECU-CONFIG-BE07-V13-DEV',
  description: 'BE.07 validation platform v13 — engineering builds, not for production deployment.',
  modelLine: 'BE.07',
  baselineVersions: [
    { ecu: 'TCU',  version: '4.2.0-rc1',  releasedAt: '2026-04-10', signedBy: 'acme-ota-eng' },
    { ecu: 'BMS',  version: '3.3.0-rc2',  releasedAt: '2026-05-12', signedBy: 'acme-ota-eng', releaseNotes: 'Adds thermal_compensation_factor signal and dynamic-derate algorithm referencing Voltrix datasheet rev 2.3 thermal limit. Validation passed on Pune R&D test fleet.' },
    { ecu: 'VCU',  version: '7.5.0-rc3',  releasedAt: '2026-04-22', signedBy: 'bosch-eng' },
    { ecu: 'BCM',  version: '2.9.0-beta', releasedAt: '2026-03-08', signedBy: 'continental-eng' },
    { ecu: 'ADAS', version: '3.0.0-rc1',  releasedAt: '2026-05-04', signedBy: 'nvidia-acme-eng' },
    { ecu: 'IVI',  version: '15.0.0-rc2', releasedAt: '2026-04-30', signedBy: 'visteon-eng' },
    { ecu: 'GW',   version: '1.6.0-rc1',  releasedAt: '2026-03-22', signedBy: 'continental-eng' },
    { ecu: 'CCU',  version: '2.4.0-rc2',  releasedAt: '2026-04-15', signedBy: 'lg-innotek-eng' },
  ],
};

export const ECU_CONFIGS: Record<string, ECUConfig> = {
  'ECU-CONFIG-BE6-V12-PROD':  ECU_CONFIG_BE6_V12_PROD,
  'ECU-CONFIG-BE07-V13-DEV':  ECU_CONFIG_BE07_V13_DEV,
};

// ============================================================================
// PER-VEHICLE ECU STATE (computed)
// ============================================================================

export type ECUOTAStatus = 'current' | 'pending' | 'in-flight' | 'failed' | 'older';

export interface VehicleECUState {
  ecu: ECUId;
  displayName: string;
  vendor: string;
  partNumberFamily: string;
  domain: ECUDefinition['domain'];
  currentVersion: string;
  baselineVersion: string;
  /** OTA pipeline target version, if any rollout is targeting this ECU. */
  pendingVersion?: string;
  /** Status of this ECU vs the latest pipeline target. */
  otaStatus: ECUOTAStatus;
  /** Last time this ECU's firmware was changed on this vehicle. */
  lastUpdatedAt: string;
  /** Timestamp the OTA pipeline targets/has completed for this vehicle, if any. */
  pipelineApplyAt?: string;
  /** Optional pipeline ID currently applying to this ECU. */
  pipelineId?: string;
  signalCount: number;
}

/**
 * Vehicle context for ECU state computation. Kept narrow to avoid coupling
 * to the DynamoDB vehicle shape — pass only the fields needed.
 */
export interface VehicleECUContext {
  vehicleId: string;
  ecuConfigId: string;
  isAffectedCohort: boolean;
  /** Build canary recipient? Used to pre-show the BMS v3.3.0 update. */
  isCanaryRecipient?: boolean;
}

/**
 * Returns the ECU state list for a vehicle. The affected cohort runs the
 * older BMS v3.2.0 (the buggy version); canary recipients of Build #4823
 * have already received BMS v3.3.0; everyone else runs the baseline.
 */
export function getECUStateForVehicle(ctx: VehicleECUContext): VehicleECUState[] {
  const config = ECU_CONFIGS[ctx.ecuConfigId];
  if (!config) return [];

  return config.baselineVersions.map((spec) => {
    const cat = ECU_CATALOG[spec.ecu];
    let currentVersion = spec.version;
    let pendingVersion: string | undefined;
    let otaStatus: ECUOTAStatus = 'current';
    let lastUpdatedAt = spec.releasedAt;
    let pipelineApplyAt: string | undefined;
    let pipelineId: string | undefined;

    // BMS-specific behaviour — driven by the in-flight Build #4823.
    if (spec.ecu === 'BMS' && config.configId === 'ECU-CONFIG-BE6-V12-PROD') {
      if (ctx.isAffectedCohort) {
        // Affected cohort lagged at v3.2.0 (didn't pick up the v3.2.1 bal tuning).
        currentVersion = '3.2.0';
        otaStatus = 'older';
        lastUpdatedAt = '2025-02-08';
      }
      if (ctx.isCanaryRecipient) {
        currentVersion = '3.3.0';
        otaStatus = 'current';
        lastUpdatedAt = '2026-05-19T15:30:00Z';
        pipelineId = 'BUILD-4823';
        pipelineApplyAt = '2026-05-19T15:30:00Z';
      } else {
        // Pipeline is rolling out v3.3.0 — flag pending for non-canary BE 6.
        pendingVersion = '3.3.0';
        otaStatus = ctx.isAffectedCohort ? 'older' : 'pending';
        pipelineId = 'BUILD-4823';
      }
    }

    return {
      ecu:               spec.ecu,
      displayName:       cat.displayName,
      vendor:            cat.vendor,
      partNumberFamily:  cat.partNumberFamily,
      domain:            cat.domain,
      currentVersion,
      baselineVersion:   spec.version,
      pendingVersion,
      otaStatus,
      lastUpdatedAt,
      pipelineApplyAt,
      pipelineId,
      signalCount:       cat.signalCount,
    };
  });
}

/**
 * Helper for fleet-level ECU rollout state — counts vehicles by their BMS
 * version for the OTA Rollouts tab. Pass the full vehicle list for a fleet.
 */
export function getBMSVersionDistribution(
  vehicles: VehicleECUContext[]
): Record<string, number> {
  const dist: Record<string, number> = {};
  vehicles.forEach((v) => {
    const ecus = getECUStateForVehicle(v);
    const bms = ecus.find((e) => e.ecu === 'BMS');
    if (!bms) return;
    dist[bms.currentVersion] = (dist[bms.currentVersion] || 0) + 1;
  });
  return dist;
}
