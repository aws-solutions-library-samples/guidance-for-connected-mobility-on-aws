// SPDX-License-Identifier: Apache-2.0
//
// signalMapping.ts — the "two disparate sources" bridge.
//
//   1. CMS canonical catalog     — the internal signals + events CMS itself
//                                   needs to run the fleet. Curated demo
//                                   subset of services/data_processing/
//                                   signal-catalog.json + event-catalog.json
//                                   (the real ~67-signal / 13-event source),
//                                   scoped to what a fleet operator maps
//                                   in the UI.
//
//   2. Producer advertisement    — what a CS-side producer (Meridian, Ford,
//                                   Tesla, etc.) publishes via a stubbed
//                                   /available-signals + /available-events
//                                   API. Different producer, different
//                                   schema — camelCase-nested for Meridian
//                                   Kafka, snake_case protobuf for Ford
//                                   gRPC, dotted state-family paths for
//                                   Tesla's public Fleet API. That contrast
//                                   is the whole reason a mapping exists.
//
// The UI on CatalogDetailPage picks pairs (CMS canonical ← producer
// advertised) and stores the mapping on the data product. Both catalogs
// live here rather than in mockData.ts so mockData stays focused on the
// stateful mutable overlay stuff (assignments, enrollments).

export type CanonicalDataType = 'float' | 'integer' | 'string' | 'boolean';

export interface CmsCanonicalSignal {
  /** Canonical field name — what CMS uses internally. */
  name: string;
  /** Group / category (matches CMS's signal-catalog.json signal_groups). */
  group: string;
  dataType: CanonicalDataType;
  unit?: string;
  description: string;
  /** Whether every message MUST carry this signal for CMS to function. */
  required: boolean;
}

export interface CmsCanonicalEvent {
  /** Event name — CMS internal identifier. */
  name: string;
  group: 'maintenance' | 'safety' | 'commercial';
  severity: 'info' | 'warning' | 'critical';
  description: string;
}

export interface ProducerSignal {
  /** Producer's signal name (usually short — how they refer to it internally). */
  name: string;
  /** Dot-notation path into the producer's payload (auto-derived from name
   *  by default but stored explicitly for producer-specific schemas). */
  path: string;
  dataType: CanonicalDataType;
  unit?: string;
}

export interface ProducerEvent {
  /** Producer's event name. */
  name: string;
  /** Producer's own severity label (may not map cleanly to CMS's). */
  severity: string;
}

// ─── CMS canonical signal catalog ─────────────────────────────────────────
//
// Curated demo subset of services/data_processing/signal-catalog.json — the
// signals a fleet operator would realistically map from an OEM producer to
// keep CMS running (location, telemetry, safety, tires, diagnostics). The
// real JSON has 67 signals; showing all of them dilutes the demo. Add
// entries here as the demo story grows.

export const CMS_CANONICAL_SIGNALS: CmsCanonicalSignal[] = [
  // vehicle_identity
  { name: 'vin',          group: 'vehicle_identity', dataType: 'string',  description: 'Vehicle Identification Number.', required: true },
  { name: 'fid',          group: 'vehicle_identity', dataType: 'string',  description: 'Fleet ID.', required: false },

  // location
  { name: 'ts',           group: 'location', dataType: 'integer', unit: 's',   description: 'Unix timestamp (seconds).', required: true },
  { name: 'lat',          group: 'location', dataType: 'float',   unit: '°',   description: 'Latitude in decimal degrees.', required: true },
  { name: 'lon',          group: 'location', dataType: 'float',   unit: '°',   description: 'Longitude in decimal degrees.', required: true },
  { name: 'spd',          group: 'location', dataType: 'float',   unit: 'mph', description: 'Vehicle speed.', required: true },
  { name: 'hdg',          group: 'location', dataType: 'float',   unit: '°',   description: 'Compass heading.', required: false },
  { name: 'alt',          group: 'location', dataType: 'float',   unit: 'm',   description: 'Altitude above sea level.', required: false },

  // vehicle_state
  { name: 'ign',          group: 'vehicle_state', dataType: 'string',  description: 'Ignition state.', required: true },
  { name: 'gear',         group: 'vehicle_state', dataType: 'integer', description: 'Gear position (0=P, 1=R, 2=N, 3=D, etc).', required: false },
  { name: 'odo',          group: 'vehicle_state', dataType: 'float',   unit: 'mi', description: 'Odometer reading.', required: true },
  { name: 'eng',          group: 'vehicle_state', dataType: 'boolean', description: 'Engine running state.', required: true },

  // fuel_energy
  { name: 'soc',          group: 'fuel_energy', dataType: 'float', unit: '%',  description: 'Battery state of charge (EVs).', required: false },
  { name: 'soh',          group: 'fuel_energy', dataType: 'float', unit: '%',  description: 'Battery state of health (EVs).', required: false },
  { name: 'range',        group: 'fuel_energy', dataType: 'float', unit: 'mi', description: 'Estimated remaining range.', required: false },
  { name: 'fuel',         group: 'fuel_energy', dataType: 'float', unit: '%',  description: 'Fuel level (ICE).', required: false },

  // tires
  { name: 'tire_fl',      group: 'tires', dataType: 'float', unit: 'psi', description: 'Tire pressure — front-left.', required: false },
  { name: 'tire_fr',      group: 'tires', dataType: 'float', unit: 'psi', description: 'Tire pressure — front-right.', required: false },
  { name: 'tire_rl',      group: 'tires', dataType: 'float', unit: 'psi', description: 'Tire pressure — rear-left.', required: false },
  { name: 'tire_rr',      group: 'tires', dataType: 'float', unit: 'psi', description: 'Tire pressure — rear-right.', required: false },
  { name: 'tire_temp_max',group: 'tires', dataType: 'float', unit: '°F',  description: 'Highest tire temperature across all four.', required: false },

  // diagnostics
  { name: 'dtc_count',    group: 'diagnostics', dataType: 'integer', description: 'Number of active diagnostic trouble codes.', required: true },
  { name: 'battv',        group: 'diagnostics', dataType: 'float', unit: 'V', description: 'Battery voltage (12V system).', required: false },
  { name: 'engine_temp',  group: 'diagnostics', dataType: 'float', unit: '°F', description: 'Engine coolant temperature.', required: false },

  // safety_systems
  { name: 'seatbelt_drv', group: 'safety_systems', dataType: 'boolean', description: 'Driver seatbelt fastened.', required: false },
  { name: 'seatbelt_pax', group: 'safety_systems', dataType: 'boolean', description: 'Passenger seatbelt fastened.', required: false },
  { name: 'headlight',    group: 'safety_systems', dataType: 'string',  description: 'Headlight state (off/low/high).', required: false },

  // driver_behavior
  { name: 'brk',          group: 'driver_behavior', dataType: 'boolean', description: 'Brake pedal pressed.', required: false },
  { name: 'acc',          group: 'driver_behavior', dataType: 'float',   unit: 'g', description: 'Longitudinal acceleration.', required: false },
];

// ─── CMS canonical event catalog ──────────────────────────────────────────

export const CMS_CANONICAL_EVENTS: CmsCanonicalEvent[] = [
  // maintenance
  { name: 'oil_change_due',       group: 'maintenance', severity: 'warning',  description: 'Vehicle needs an oil change based on mileage or hours.' },
  { name: 'tire_rotation_due',    group: 'maintenance', severity: 'info',     description: 'Tire rotation service is due.' },
  { name: 'brake_pad_worn',       group: 'maintenance', severity: 'warning',  description: 'Brake pad thickness below service threshold.' },
  { name: 'battery_health_low',   group: 'maintenance', severity: 'warning',  description: 'HV battery state of health degraded.' },
  { name: 'coolant_low',          group: 'maintenance', severity: 'warning',  description: 'Coolant reservoir below minimum level.' },
  { name: 'dtc_active',           group: 'maintenance', severity: 'info',     description: 'One or more active diagnostic trouble codes present.' },
  { name: 'inspection_overdue',   group: 'maintenance', severity: 'critical', description: 'Scheduled inspection has passed its due date.' },

  // safety
  { name: 'hard_braking',         group: 'safety', severity: 'warning',  description: 'Sudden hard-braking event detected.' },
  { name: 'harsh_cornering',      group: 'safety', severity: 'warning',  description: 'Harsh cornering event exceeding lateral-g threshold.' },
  { name: 'harsh_acceleration',   group: 'safety', severity: 'warning',  description: 'Sudden acceleration exceeding longitudinal-g threshold.' },
  { name: 'collision_detected',   group: 'safety', severity: 'critical', description: 'Impact/collision event detected by airbag or ABS module.' },

  // commercial
  { name: 'pto_engaged',          group: 'commercial', severity: 'info', description: 'Power take-off engaged (commercial vehicle work mode).' },
  { name: 'excessive_idle',       group: 'commercial', severity: 'info', description: 'Vehicle idling beyond configured threshold (fuel waste).' },
];

// ─── Producer signal/event advertisement (stubbed) ────────────────────────
//
// Each producer publishes its OWN signal and event schema. In production
// this would come from a CS-side /available-signals + /available-events API
// keyed by producer. Here we stub with realistic-looking per-producer
// schemas so the mapping story is concrete:
//
//   Meridian  (Kafka)      — camelCase nested paths (telemetry.battery.socPct)
//   Ford Pro  (gRPC)       — snake_case protobuf-style (hv_battery.state_of_charge)
//   Tesla     (Fleet API)  — dotted state-family paths under vehicle_state /
//                            drive_state / charge_state / climate_state, as
//                            defined by Tesla's public Fleet API (see
//                            developer.tesla.com/docs/fleet-api).
//
// The three producers cover the same conceptual signals but with different
// names and paths, which is exactly the shape a real mapping tool has to
// resolve. Note that producer catalogs INTENTIONALLY don't cover every CMS
// canonical signal — coverage gaps are part of the demo.

const MERIDIAN_SIGNALS: ProducerSignal[] = [
  { name: 'vehicleIdentificationNumber', path: 'metadata.vin',                        dataType: 'string' },
  { name: 'fleetIdentifier',             path: 'metadata.fleetId',                    dataType: 'string' },
  { name: 'timestamp',                   path: 'timestamp',                           dataType: 'integer', unit: 's' },
  { name: 'gpsLatitudeDeg',              path: 'telemetry.location.latDeg',           dataType: 'float',   unit: '°' },
  { name: 'gpsLongitudeDeg',             path: 'telemetry.location.lonDeg',           dataType: 'float',   unit: '°' },
  { name: 'speedMph',                    path: 'telemetry.vehicle.speedMph',          dataType: 'float',   unit: 'mph' },
  { name: 'headingDeg',                  path: 'telemetry.location.headingDeg',       dataType: 'float',   unit: '°' },
  { name: 'altitudeMeters',              path: 'telemetry.location.altitudeM',        dataType: 'float',   unit: 'm' },
  { name: 'ignitionState',               path: 'telemetry.powertrain.ignition',       dataType: 'string' },
  { name: 'gearPosition',                path: 'telemetry.powertrain.gearPos',        dataType: 'integer' },
  { name: 'odometerMi',                  path: 'telemetry.vehicle.odometerMi',        dataType: 'float',   unit: 'mi' },
  { name: 'engineRunningState',          path: 'telemetry.powertrain.engineOn',       dataType: 'boolean' },
  { name: 'stateOfChargePct',            path: 'telemetry.battery.socPct',            dataType: 'float',   unit: '%' },
  { name: 'stateOfHealthPct',            path: 'telemetry.battery.sohPct',            dataType: 'float',   unit: '%' },
  { name: 'rangeEstimateMi',             path: 'telemetry.range.estimateMi',          dataType: 'float',   unit: 'mi' },
  { name: 'tirePressureFrontLeft',       path: 'telemetry.tires.fl.pressurePsi',      dataType: 'float',   unit: 'psi' },
  { name: 'tirePressureFrontRight',      path: 'telemetry.tires.fr.pressurePsi',      dataType: 'float',   unit: 'psi' },
  { name: 'tirePressureRearLeft',        path: 'telemetry.tires.rl.pressurePsi',      dataType: 'float',   unit: 'psi' },
  { name: 'tirePressureRearRight',       path: 'telemetry.tires.rr.pressurePsi',      dataType: 'float',   unit: 'psi' },
  { name: 'tireMaxTemperatureF',         path: 'telemetry.tires.maxTempF',            dataType: 'float',   unit: '°F' },
  { name: 'dtcActiveCount',              path: 'telemetry.diagnostics.dtcActive',     dataType: 'integer' },
  { name: 'batteryVoltage12v',           path: 'telemetry.electrical.batt12v',        dataType: 'float',   unit: 'V' },
  { name: 'engineCoolantTempF',          path: 'telemetry.diagnostics.coolantTempF',  dataType: 'float',   unit: '°F' },
  { name: 'brakePedalPressed',           path: 'telemetry.driverBehavior.brakePressed', dataType: 'boolean' },
  { name: 'longitudinalAccelG',          path: 'telemetry.driverBehavior.accelG',     dataType: 'float',   unit: 'g' },
];

const MERIDIAN_EVENTS: ProducerEvent[] = [
  { name: 'ServiceOilChangeDue',      severity: 'MEDIUM' },
  { name: 'ServiceTireRotationDue',   severity: 'LOW' },
  { name: 'ServiceBrakePadWorn',      severity: 'MEDIUM' },
  { name: 'BatterySohDegraded',       severity: 'MEDIUM' },
  { name: 'CoolantLevelLow',          severity: 'MEDIUM' },
  { name: 'DtcActiveDetected',        severity: 'LOW' },
  { name: 'HardBrakingDetected',      severity: 'HIGH' },
  { name: 'CorneringHarshDetected',   severity: 'HIGH' },
  { name: 'CollisionImpactDetected',  severity: 'CRITICAL' },
];

const FORD_SIGNALS: ProducerSignal[] = [
  { name: 'vehicle_vin',           path: 'vehicle_identity.vin',                dataType: 'string' },
  { name: 'gps_timestamp',         path: 'gps.timestamp',                       dataType: 'integer', unit: 's' },
  { name: 'gps_lat',               path: 'gps.latitude',                        dataType: 'float',   unit: '°' },
  { name: 'gps_lon',               path: 'gps.longitude',                       dataType: 'float',   unit: '°' },
  { name: 'gps_heading',           path: 'gps.heading_deg',                     dataType: 'float',   unit: '°' },
  { name: 'vehicle_speed_mph',     path: 'vehicle_data.speed_mph',              dataType: 'float',   unit: 'mph' },
  { name: 'ignition_status',       path: 'vehicle_data.ignition',               dataType: 'string' },
  { name: 'transmission_gear',     path: 'powertrain.transmission_gear',        dataType: 'integer' },
  { name: 'odometer_miles',        path: 'vehicle_data.odometer_miles',         dataType: 'float',   unit: 'mi' },
  { name: 'engine_running',        path: 'vehicle_data.engine_running',         dataType: 'boolean' },
  { name: 'hv_battery_soc',        path: 'hv_battery.state_of_charge',          dataType: 'float',   unit: '%' },
  { name: 'hv_battery_soh',        path: 'hv_battery.state_of_health',          dataType: 'float',   unit: '%' },
  { name: 'estimated_range_mi',    path: 'vehicle_data.estimated_range_miles',  dataType: 'float',   unit: 'mi' },
  { name: 'fuel_level_percent',    path: 'powertrain.fuel_level_percent',       dataType: 'float',   unit: '%' },
  { name: 'tire_pressure_fl_psi',  path: 'tires.front_left.pressure_psi',       dataType: 'float',   unit: 'psi' },
  { name: 'tire_pressure_fr_psi',  path: 'tires.front_right.pressure_psi',      dataType: 'float',   unit: 'psi' },
  { name: 'tire_pressure_rl_psi',  path: 'tires.rear_left.pressure_psi',        dataType: 'float',   unit: 'psi' },
  { name: 'tire_pressure_rr_psi',  path: 'tires.rear_right.pressure_psi',       dataType: 'float',   unit: 'psi' },
  { name: 'dtc_active_count',      path: 'diagnostics.dtc_active_count',        dataType: 'integer' },
  { name: 'coolant_temp_f',        path: 'powertrain.coolant_temperature_f',    dataType: 'float',   unit: '°F' },
  { name: 'seatbelt_driver',       path: 'safety.seatbelt.driver',              dataType: 'boolean' },
  { name: 'seatbelt_passenger',    path: 'safety.seatbelt.passenger',           dataType: 'boolean' },
  { name: 'headlight_state',       path: 'lighting.headlight_state',            dataType: 'string' },
];

const FORD_EVENTS: ProducerEvent[] = [
  { name: 'maint.oil_change_due',      severity: 'warning' },
  { name: 'maint.tire_rotation_due',   severity: 'info' },
  { name: 'maint.brake_wear_warning',  severity: 'warning' },
  { name: 'maint.battery_soh_low',     severity: 'warning' },
  { name: 'diag.dtc_stored',           severity: 'info' },
  { name: 'safety.hard_brake_event',   severity: 'warning' },
  { name: 'safety.harsh_accel_event',  severity: 'warning' },
  { name: 'safety.crash_detected',     severity: 'critical' },
  { name: 'commercial.pto_active',     severity: 'info' },
];

const TESLA_SIGNALS: ProducerSignal[] = [
  // Vehicle identity + timing
  { name: 'vin',                    path: 'vin',                              dataType: 'string' },
  { name: 'timestamp',              path: 'vehicle_state.timestamp',          dataType: 'integer', unit: 'ms' },
  // drive_state family — location, motion, gear
  { name: 'latitude',               path: 'drive_state.latitude',             dataType: 'float',   unit: '°' },
  { name: 'longitude',              path: 'drive_state.longitude',            dataType: 'float',   unit: '°' },
  { name: 'heading',                path: 'drive_state.heading',              dataType: 'float',   unit: '°' },
  { name: 'speed',                  path: 'drive_state.speed',                dataType: 'float',   unit: 'mph' },
  { name: 'shift_state',            path: 'drive_state.shift_state',          dataType: 'string' },
  // vehicle_state family — odometer, doors, tpms, sw version
  { name: 'odometer',               path: 'vehicle_state.odometer',           dataType: 'float',   unit: 'mi' },
  { name: 'locked',                 path: 'vehicle_state.locked',             dataType: 'boolean' },
  { name: 'car_version',            path: 'vehicle_state.car_version',        dataType: 'string' },
  { name: 'tpms_pressure_fl',       path: 'vehicle_state.tpms_pressure_fl',   dataType: 'float',   unit: 'bar' },
  { name: 'tpms_pressure_fr',       path: 'vehicle_state.tpms_pressure_fr',   dataType: 'float',   unit: 'bar' },
  { name: 'tpms_pressure_rl',       path: 'vehicle_state.tpms_pressure_rl',   dataType: 'float',   unit: 'bar' },
  { name: 'tpms_pressure_rr',       path: 'vehicle_state.tpms_pressure_rr',   dataType: 'float',   unit: 'bar' },
  // charge_state family — SOC, range, charging status
  { name: 'battery_level',          path: 'charge_state.battery_level',       dataType: 'float',   unit: '%' },
  { name: 'battery_range',          path: 'charge_state.battery_range',       dataType: 'float',   unit: 'mi' },
  { name: 'charging_state',         path: 'charge_state.charging_state',      dataType: 'string' },
  { name: 'charge_limit_soc',       path: 'charge_state.charge_limit_soc',    dataType: 'float',   unit: '%' },
  { name: 'charger_power',          path: 'charge_state.charger_power',       dataType: 'float',   unit: 'kW' },
  // climate_state family — temps
  { name: 'inside_temp',            path: 'climate_state.inside_temp',        dataType: 'float',   unit: '°C' },
  { name: 'outside_temp',           path: 'climate_state.outside_temp',       dataType: 'float',   unit: '°C' },
  // Telemetry alerts / drivetrain
  { name: 'is_user_present',        path: 'vehicle_state.is_user_present',    dataType: 'boolean' },
  { name: 'sentry_mode',            path: 'vehicle_state.sentry_mode',        dataType: 'boolean' },
];

// Tesla Fleet API telemetry-stream events. Real names sourced from Tesla's
// public streaming/fleet telemetry docs (developer.tesla.com/docs/fleet-api).
const TESLA_EVENTS: ProducerEvent[] = [
  { name: 'ServiceRequired',         severity: 'medium' },
  { name: 'TirePressureWarning',     severity: 'medium' },
  { name: 'BrakePadWearIndicator',   severity: 'medium' },
  { name: 'HighVoltageBatteryFault', severity: 'high' },
  { name: 'DiagnosticTroubleCode',   severity: 'low' },
  { name: 'HardBrakingEvent',        severity: 'medium' },
  { name: 'AbruptAccelerationEvent', severity: 'medium' },
  { name: 'ImpactDetected',          severity: 'critical' },
  { name: 'SentryModeAlarm',         severity: 'medium' },
];

/**
 * The stubbed producer signal advertisement — imagine a
 * GET /producers/{producer}/available-signals API. Returns whatever the
 * producer publishes; empty array for producers we don't have a stub for.
 */
export function getProducerSignals(producer: string): ProducerSignal[] {
  switch (producer) {
    case 'Meridian':
      return MERIDIAN_SIGNALS;
    case 'Ford':
      return FORD_SIGNALS;
    case 'Tesla':
      return TESLA_SIGNALS;
    default:
      return [];
  }
}

/**
 * Producers that have a real stubbed catalog behind getProducerSignals /
 * getProducerEvents. Anything else falls back to the generic example
 * catalog when the fallback-friendly variants are used.
 */
export function isProducerStubbed(producer: string): boolean {
  return producer === 'Meridian' || producer === 'Ford' || producer === 'Tesla';
}

/** Generic example producer signal catalog — used as a fallback in the
 *  wizard when the operator types a producer name that isn't one of the
 *  stubbed catalogs (Meridian / Ford / Tesla). Realistic-shaped names +
 *  paths so the pick-and-match UI is demoable even for a fresh
 *  producer, with the understanding that in production these would come
 *  from the producer's own /available-signals API. */
export const GENERIC_PRODUCER_SIGNALS: ProducerSignal[] = [
  { name: 'vin',              path: 'metadata.vin',                       dataType: 'string' },
  { name: 'timestamp',        path: 'metadata.timestamp',                 dataType: 'integer', unit: 's' },
  { name: 'vehicle_speed',    path: 'telemetry.speed',                    dataType: 'float',   unit: 'mph' },
  { name: 'odometer',         path: 'telemetry.odometer',                 dataType: 'float',   unit: 'mi' },
  { name: 'latitude',         path: 'gps.latitude',                       dataType: 'float',   unit: '°' },
  { name: 'longitude',        path: 'gps.longitude',                      dataType: 'float',   unit: '°' },
  { name: 'heading',          path: 'gps.heading',                        dataType: 'float',   unit: '°' },
  { name: 'ignition_state',   path: 'vehicle.ignition',                   dataType: 'string' },
  { name: 'gear_position',    path: 'vehicle.gear',                       dataType: 'integer' },
  { name: 'engine_running',   path: 'vehicle.engine_running',             dataType: 'boolean' },
  { name: 'battery_soc',      path: 'battery.state_of_charge',            dataType: 'float',   unit: '%' },
  { name: 'fuel_level',       path: 'fuel.level',                         dataType: 'float',   unit: '%' },
  { name: 'tire_pressure_fl', path: 'tires.front_left.pressure',          dataType: 'float',   unit: 'psi' },
  { name: 'tire_pressure_fr', path: 'tires.front_right.pressure',         dataType: 'float',   unit: 'psi' },
  { name: 'tire_pressure_rl', path: 'tires.rear_left.pressure',           dataType: 'float',   unit: 'psi' },
  { name: 'tire_pressure_rr', path: 'tires.rear_right.pressure',          dataType: 'float',   unit: 'psi' },
  { name: 'dtc_count',        path: 'diagnostics.dtc_active_count',       dataType: 'integer' },
];

export const GENERIC_PRODUCER_EVENTS: ProducerEvent[] = [
  { name: 'service_due',        severity: 'medium' },
  { name: 'tire_pressure_low',  severity: 'medium' },
  { name: 'battery_soh_low',    severity: 'medium' },
  { name: 'dtc_reported',       severity: 'low' },
  { name: 'hard_brake',         severity: 'high' },
  { name: 'crash_detected',     severity: 'critical' },
];

/** Producer signals with generic fallback. Returns the stubbed catalog for
 *  Meridian / Ford / Tesla, otherwise the generic example set — so the
 *  wizard's pick-and-match remains functional for any producer name the
 *  operator types. */
export function getProducerSignalsOrGeneric(producer: string): ProducerSignal[] {
  const stubbed = getProducerSignals(producer);
  return stubbed.length > 0 ? stubbed : GENERIC_PRODUCER_SIGNALS;
}

/**
 * The stubbed producer event advertisement — imagine a
 * GET /producers/{producer}/available-events API. Returns whatever the
 * producer publishes; empty array for producers we don't have a stub for.
 */
export function getProducerEvents(producer: string): ProducerEvent[] {
  switch (producer) {
    case 'Meridian':
      return MERIDIAN_EVENTS;
    case 'Ford':
      return FORD_EVENTS;
    case 'Tesla':
      return TESLA_EVENTS;
    default:
      return [];
  }
}

/** Producer events with generic fallback. Returns the stubbed catalog for
 *  Meridian / Ford / Tesla, otherwise the generic example set. */
export function getProducerEventsOrGeneric(producer: string): ProducerEvent[] {
  const stubbed = getProducerEvents(producer);
  return stubbed.length > 0 ? stubbed : GENERIC_PRODUCER_EVENTS;
}

// ─── Auto-matching helpers ────────────────────────────────────────────────
//
// Fuzzy name-similarity match — used by the 'Auto-map by name' button on
// the mapping UI. Not perfect (real-world mapping tools use synonyms,
// ontologies, embeddings) but pretty good on the demo data where producer
// names are usually a longer/different-cased variant of the CMS name.
//
// Score is intentionally simple:
//   - exact match wins
//   - shortest producer name containing CMS name wins next
//   - shortest producer name whose normalized form matches CMS's wins next

function normalize(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]/g, '');
}

export function autoMatchSignal(
  cmsName: string,
  producerSignals: ProducerSignal[],
): ProducerSignal | undefined {
  const target = normalize(cmsName);
  // Exact normalized match
  const exact = producerSignals.find((p) => normalize(p.name) === target);
  if (exact) return exact;
  // Contains-substring match — CMS name inside producer name (e.g. 'soc' inside 'battery_soc')
  const contains = producerSignals
    .filter((p) => normalize(p.name).includes(target))
    .sort((a, b) => a.name.length - b.name.length)[0];
  if (contains) return contains;
  // Contains-substring the other way — producer inside CMS (rare but happens)
  const inverse = producerSignals
    .filter((p) => target.includes(normalize(p.name)))
    .sort((a, b) => b.name.length - a.name.length)[0];
  return inverse;
}

export function autoMatchEvent(
  cmsName: string,
  producerEvents: ProducerEvent[],
): ProducerEvent | undefined {
  const target = normalize(cmsName);
  const exact = producerEvents.find((p) => normalize(p.name) === target);
  if (exact) return exact;
  const contains = producerEvents
    .filter((p) => normalize(p.name).includes(target))
    .sort((a, b) => a.name.length - b.name.length)[0];
  return contains;
}
