// Engineering signal-to-ECU mapping. Annotates the existing signal catalog
// (in components/data-processing/SignalCatalogViewer.tsx) with which ECU
// produces each signal and the minimum ECU firmware version that emits it.
//
// Used by:
//   - Signal Catalog viewer column annotations (engineer-only)
//   - Cross-link from Vehicle Detail → ECUs tab → click ECU → filter signals to that ECU
//   - The Build #4823 narrative ("new signal thermal_compensation_factor available in BMS ≥ v3.3.0")
//
// In production: this mapping lives in the AWS IoT FleetWise decoder manifest.
// For the demo: inline lookup table.

import { ECUId } from './ecus';

export interface SignalECUMapping {
  /** Fully-qualified signal name as it appears in the VSS / signal catalog. */
  fullyQualifiedName: string;
  /** ECU that produces this signal. */
  producingECU: ECUId;
  /** Minimum ECU firmware version that emits this signal. */
  ecuMinVersion: string;
  /** Whether this signal is new in the latest pipeline (badge in UI). */
  isNewInLatestBuild?: boolean;
  /** Optional pipeline ID that introduced this signal. */
  introducedByPipeline?: string;
  /** Short human-readable purpose. */
  description?: string;
}

// ============================================================================
// SIGNAL → ECU MAPPING
// ============================================================================

export const SIGNAL_ECU_MAP: SignalECUMapping[] = [
  // ----- BMS signals -----
  { fullyQualifiedName: 'Vehicle.Powertrain.TractionBattery.StateOfCharge.Current',           producingECU: 'BMS',  ecuMinVersion: '2.0.0', description: 'Battery state of charge, instantaneous %.' },
  { fullyQualifiedName: 'Vehicle.Powertrain.TractionBattery.StateOfHealth',                  producingECU: 'BMS',  ecuMinVersion: '3.0.0', description: 'Battery state of health, % of rated capacity.' },
  { fullyQualifiedName: 'Vehicle.Powertrain.TractionBattery.Temperature.Cell.Max',           producingECU: 'BMS',  ecuMinVersion: '3.0.0', description: 'Hottest cell temperature in the pack.' },
  { fullyQualifiedName: 'Vehicle.Powertrain.TractionBattery.Temperature.Cell.Min',           producingECU: 'BMS',  ecuMinVersion: '3.0.0', description: 'Coldest cell temperature in the pack.' },
  { fullyQualifiedName: 'Vehicle.Powertrain.TractionBattery.Temperature.Cell.Spread',        producingECU: 'BMS',  ecuMinVersion: '3.2.0', description: 'Temperature delta between hottest and coldest cell.' },
  { fullyQualifiedName: 'Vehicle.Powertrain.TractionBattery.Temperature.Coolant.In',         producingECU: 'BMS',  ecuMinVersion: '3.0.0' },
  { fullyQualifiedName: 'Vehicle.Powertrain.TractionBattery.Temperature.Coolant.Out',        producingECU: 'BMS',  ecuMinVersion: '3.0.0' },
  { fullyQualifiedName: 'Vehicle.Powertrain.TractionBattery.CellBalanceDelta',               producingECU: 'BMS',  ecuMinVersion: '3.2.1' },
  {
    fullyQualifiedName: 'Vehicle.Powertrain.TractionBattery.ThermalCompensationFactor',
    producingECU:        'BMS',
    ecuMinVersion:       '3.3.0',
    isNewInLatestBuild:  true,
    introducedByPipeline: 'BUILD-4823',
    description:         'NEW in BMS v3.3.0 — dynamic thermal derate coefficient referencing Voltrix datasheet rev 2.3 thermal limit. 1.0 = no derate; <1.0 = derating.',
  },
  {
    fullyQualifiedName: 'Vehicle.Powertrain.TractionBattery.DerateActiveSeconds',
    producingECU:        'BMS',
    ecuMinVersion:       '3.3.0',
    isNewInLatestBuild:  true,
    introducedByPipeline: 'BUILD-4823',
    description:         'NEW in BMS v3.3.0 — cumulative seconds the thermal derate has been active in the current trip. Used by engineering insights to validate the fix.',
  },

  // ----- VCU signals -----
  { fullyQualifiedName: 'Vehicle.Powertrain.Motor.Torque.Actual',                            producingECU: 'VCU',  ecuMinVersion: '7.0.0', description: 'Motor output torque, signed Nm.' },
  { fullyQualifiedName: 'Vehicle.Powertrain.Motor.Power.Actual',                             producingECU: 'VCU',  ecuMinVersion: '7.0.0' },
  { fullyQualifiedName: 'Vehicle.Powertrain.Motor.Speed',                                    producingECU: 'VCU',  ecuMinVersion: '7.0.0' },
  { fullyQualifiedName: 'Vehicle.Powertrain.RegenBraking.Active',                            producingECU: 'VCU',  ecuMinVersion: '7.4.0' },
  { fullyQualifiedName: 'Vehicle.Powertrain.RegenBraking.RecoveredEnergy',                   producingECU: 'VCU',  ecuMinVersion: '7.4.0' },

  // ----- TCU signals -----
  { fullyQualifiedName: 'Vehicle.Connectivity.LinkQuality',                                  producingECU: 'TCU',  ecuMinVersion: '4.0.0' },
  { fullyQualifiedName: 'Vehicle.Connectivity.SignalStrength.Cellular',                      producingECU: 'TCU',  ecuMinVersion: '4.0.0' },
  { fullyQualifiedName: 'Vehicle.Connectivity.NetworkType',                                  producingECU: 'TCU',  ecuMinVersion: '4.0.0' },
  { fullyQualifiedName: 'Vehicle.Connectivity.OTA.LastCampaignId',                           producingECU: 'TCU',  ecuMinVersion: '4.1.0', description: 'ID of the last OTA campaign processed by this vehicle.' },

  // ----- ADAS signals -----
  { fullyQualifiedName: 'Vehicle.ADAS.LaneAssist.Active',                                    producingECU: 'ADAS', ecuMinVersion: '2.0.0' },
  { fullyQualifiedName: 'Vehicle.ADAS.AdaptiveCruiseControl.SetSpeed',                       producingECU: 'ADAS', ecuMinVersion: '2.0.0' },
  { fullyQualifiedName: 'Vehicle.ADAS.AutomaticEmergencyBraking.Triggered',                  producingECU: 'ADAS', ecuMinVersion: '2.0.0' },
  { fullyQualifiedName: 'Vehicle.ADAS.ForwardCollisionWarning.Active',                       producingECU: 'ADAS', ecuMinVersion: '2.1.0' },

  // ----- BCM signals -----
  { fullyQualifiedName: 'Vehicle.Body.Lights.Headlights.Status',                             producingECU: 'BCM',  ecuMinVersion: '2.0.0' },
  { fullyQualifiedName: 'Vehicle.Body.Doors.LockedStatus',                                   producingECU: 'BCM',  ecuMinVersion: '2.0.0' },
  { fullyQualifiedName: 'Vehicle.Cabin.HVAC.Temperature.Set',                                producingECU: 'BCM',  ecuMinVersion: '2.5.0' },
  { fullyQualifiedName: 'Vehicle.Cabin.HVAC.Mode',                                           producingECU: 'BCM',  ecuMinVersion: '2.5.0' },

  // ----- CCU signals -----
  { fullyQualifiedName: 'Vehicle.Charging.Active',                                           producingECU: 'CCU',  ecuMinVersion: '2.0.0' },
  { fullyQualifiedName: 'Vehicle.Charging.Power.Current',                                    producingECU: 'CCU',  ecuMinVersion: '2.0.0' },
  { fullyQualifiedName: 'Vehicle.Charging.Power.Maximum',                                    producingECU: 'CCU',  ecuMinVersion: '2.0.0' },
  { fullyQualifiedName: 'Vehicle.Charging.Type',                                             producingECU: 'CCU',  ecuMinVersion: '2.3.0', description: 'AC, DC, or PnC (ISO 15118).' },

  // ----- IVI signals (limited — IVI mostly user-facing) -----
  { fullyQualifiedName: 'Vehicle.Cabin.Infotainment.Volume',                                 producingECU: 'IVI',  ecuMinVersion: '14.0.0' },
  { fullyQualifiedName: 'Vehicle.Cabin.Infotainment.Source',                                 producingECU: 'IVI',  ecuMinVersion: '14.0.0' },

  // ----- GW signals (gateway internal diagnostics) -----
  { fullyQualifiedName: 'Vehicle.Network.CAN.BusLoad',                                       producingECU: 'GW',   ecuMinVersion: '1.5.0' },
  { fullyQualifiedName: 'Vehicle.Network.Ethernet.LinkStatus',                               producingECU: 'GW',   ecuMinVersion: '1.5.0' },
];

// ============================================================================
// LOOKUP HELPERS
// ============================================================================

const _byNameIndex = new Map(SIGNAL_ECU_MAP.map((m) => [m.fullyQualifiedName, m]));
const _byECU: Record<string, SignalECUMapping[]> = {};
for (const m of SIGNAL_ECU_MAP) {
  (_byECU[m.producingECU] ??= []).push(m);
}

/**
 * signal_group → ECU mapping. Catches every signal in the catalog by its
 * group rather than requiring an exact VSS path match. The catalog's
 * signal_group field is canonical; ECU is derived from it.
 */
export const SIGNAL_GROUP_TO_ECU: Record<string, ECUId> = {
  // ADAS / driver assist / safety — produced by the ADAS Domain Controller
  'adas':            'ADAS',
  'ADAS':            'ADAS',
  'safety':          'ADAS',

  // Powertrain / chassis / vehicle control / maintenance — produced by VCU
  'core_telemetry':  'VCU',
  'vehicle_control': 'VCU',
  'powertrain':      'VCU',
  'maintenance':     'VCU',
  'Engine':          'VCU',
  'Chassis':         'VCU',

  // Energy / battery / charging — produced by BMS (cell/pack telemetry)
  'ev_charging':     'BMS',
  'ev_specific':     'BMS',
  'EV':              'BMS',

  // Body / comfort / cabin — produced by BCM
  'cabin_climate':   'BCM',
  'HVAC':            'BCM',
  'doors':           'BCM',
  'windows':         'BCM',
  'lighting':        'BCM',
  'mirrors':         'BCM',
  'wipers':          'BCM',
  'security':        'BCM',
  'tpms':            'BCM',

  // Connectivity / location — produced by TCU
  'connectivity':    'TCU',
  'gps':             'TCU',
  'geofence':        'TCU',

  // Diagnostics / environmental sensors — produced by Gateway
  'diagnostics':     'GW',
  'environment':     'GW',
};

/**
 * Return the ECU mapping for a signal. First checks the explicit per-signal
 * override map (used for v3.3.0 new signals with version constraints), then
 * falls back to the signal_group lookup. Returns undefined only when the
 * signal has neither match.
 */
export function getSignalECUMapping(
  fullyQualifiedName: string,
  signalGroup?: string,
): SignalECUMapping | undefined {
  const explicit = _byNameIndex.get(fullyQualifiedName);
  if (explicit) return explicit;

  if (signalGroup) {
    const ecu = SIGNAL_GROUP_TO_ECU[signalGroup];
    if (ecu) {
      return {
        fullyQualifiedName,
        producingECU:  ecu,
        ecuMinVersion: '1.0.0',
      };
    }
  }
  return undefined;
}

export function getSignalsByECU(ecu: ECUId): SignalECUMapping[] {
  return _byECU[ecu] ?? [];
}

export function getNewSignalsInLatestBuild(): SignalECUMapping[] {
  return SIGNAL_ECU_MAP.filter((m) => m.isNewInLatestBuild);
}

/** Total signals tracked in the engineering catalog (NOT including the catalog-wide group mapping). */
export const SIGNAL_COUNT = SIGNAL_ECU_MAP.length;

/** Per-ECU signal counts — keeps ECU_CATALOG.signalCount roughly aligned. */
export const SIGNAL_COUNT_BY_ECU = Object.fromEntries(
  Object.entries(_byECU).map(([ecu, signals]) => [ecu, signals.length])
) as Record<ECUId, number>;
