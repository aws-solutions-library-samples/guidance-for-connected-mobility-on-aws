/**
 * ECU name constants for the SOVD Read-DTCs selector.
 *
 * Aligned with `_SIDECAR_ECU_MAP` in
 * `services/simulation/realtime_telemetry_simulator.py` (lines 391-399),
 * which is a READ-ONLY copy of `_ECU_BY_NUMBER` in
 * `services/simulation/lambda/simulation_lambda.py` (line 715).
 *
 * Do NOT add, remove, or rename entries without updating those Python
 * constants; the SOVD sidecar routes requests by these exact string keys.
 */

export interface EcuOption {
  /** String key used in `components[]` of the SOVD read_dtcs payload. */
  value: string;
  /** Human-readable label shown in the ECU select dropdown. */
  label: string;
}

/** Ordered list of the 9 ECUs the sidecar supports, in CAN-ID order. */
export const ECU_OPTIONS: EcuOption[] = [
  { value: 'ECU_BRAKE',       label: 'Brake (ECU_BRAKE)' },
  { value: 'ECU_ENGINE',      label: 'Engine (ECU_ENGINE)' },
  { value: 'ECU_POWERTRAIN',  label: 'Powertrain (ECU_POWERTRAIN)' },
  { value: 'ECU_PCM',         label: 'PCM (ECU_PCM)' },
  { value: 'ECU_COMM',        label: 'Communications (ECU_COMM)' },
  { value: 'ECU_BATTERY_HV',  label: 'HV Battery (ECU_BATTERY_HV)' },
  { value: 'ECU_BATTERY_12V', label: '12V Battery (ECU_BATTERY_12V)' },
  { value: 'ECU_EVAP',        label: 'EVAP (ECU_EVAP)' },
  { value: 'ECU_BODY',        label: 'Body (ECU_BODY)' },
];

/** Set of valid ECU string values, for O(1) membership tests. */
export const ECU_VALUE_SET: ReadonlySet<string> = new Set(ECU_OPTIONS.map(e => e.value));

/**
 * Manifest-vocabulary ECU label table.
 *
 * Aligned with `_IDENTITY_ECU_BASELINE` in
 * `services/simulation/seed_model_manifests.py`, which lists the 9 ECUs
 * by their manifest key (TCU, BMS, VCU, BCM, ADAS, IVI, GW, CCU, ECM).
 * Also cross-referenced against `ecu_vocabulary_map.py`
 * (`MODEL_ECU_TO_SIDECAR` and `NOT_REMOTELY_DIAGNOSABLE_REASON`), which
 * maps these manifest keys to their sidecar counterparts for reachability
 * resolution. The frontend renders labels received from the server; it
 * never re-derives reachability (F22 Decision 2).
 *
 * Do NOT add entries here without updating the Python source files above;
 * do NOT add a second module for this vocabulary (C11 / F18 precedent).
 */
export const MANIFEST_ECU_LABELS: Readonly<Record<string, string>> = {
  'TCU':  'Telematics Control Unit',
  'BMS':  'Battery Management System',
  'VCU':  'Vehicle Control Unit',
  'BCM':  'Body Control Module',
  'ADAS': 'Advanced Driver Assistance System',
  'IVI':  'In-Vehicle Infotainment',
  'GW':   'Gateway',
  'CCU':  'Charging Control Unit',
  'ECM':  'Engine Control Module',
} as const;
