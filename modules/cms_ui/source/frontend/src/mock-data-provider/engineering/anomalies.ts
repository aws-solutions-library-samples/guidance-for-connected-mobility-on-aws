// Anomaly feed surfaced on the Engineer Insights landing page.
// One hero anomaly drives the demo; decoys make the feed look real.

export interface Anomaly {
  anomalyId: string;
  detectedAt: string; // ISO timestamp
  severity: 'low' | 'medium' | 'high' | 'critical';
  title: string;
  summary: string;
  affectedFleets: string[];
  affectedVehicleCount: number;
  cohortDescription: string;
  metricName: string;
  metricUnit: string;
  metricBaseline: number;
  metricObserved: number;
  metricDeltaPercent: number;
  status: 'new' | 'investigating' | 'resolved';
  modelLine: string;
  domain:
    | 'battery'
    | 'thermal'
    | 'powertrain'
    | 'safety'
    | 'driver-experience'
    | 'charging'
    | 'adas'
    | 'connectivity'
    | 'manufacturing';
  /**
   * Pre-prod = under engineering control on validation/test fleets.
   * Post-prod = in-market customer vehicles, requires OTA / TSB / recall paths.
   */
  productionPhase?: 'pre-prod' | 'post-prod';
  /** Optional ECU(s) implicated, helps cross-link to ECU/OTA surfaces. */
  implicatedECUs?: string[];
}

export const ENGINEERING_ANOMALIES: Anomaly[] = [
  // ===== HERO ANOMALY (the one we click into during the demo) =====
  {
    anomalyId: 'anom-be6-thermal-001',
    detectedAt: '2026-05-18T22:14:00Z',
    severity: 'high',
    title: 'Windrose battery SoH degradation 12.2% above baseline in hot-climate cohort',
    summary:
      'Vehicles operated in Maharashtra and Gujarat hot zones, assembled with Voltrix Q3 2025 cells at Chakan-MH, show accelerated battery State-of-Health degradation. Cohort statistically significant (n=40, p<0.001) over a rolling 90-day window.',
    affectedFleets: ['be6-prod-cohort-001'],
    affectedVehicleCount: 40,
    cohortDescription:
      'Operating region in {Maharashtra-Hot, Gujarat-Hot} AND manufacturing batch in {BATCH-MH-Q3-2025-A12, BATCH-MH-Q3-2025-B14}',
    metricName: 'Battery SoH degradation rate',
    metricUnit: '% per month',
    metricBaseline: 0.9,
    metricObserved: 1.01,
    metricDeltaPercent: 12.2,
    status: 'new',
    modelLine: 'Windrose',
    domain: 'battery',
    productionPhase: 'post-prod',
    implicatedECUs: ['BMS'],
  },

  // ===== PRE-PROD HIGH-SEVERITY: ADAS false-positive AEB on Trailwind =====
  {
    anomalyId: 'anom-be07-adas-001',
    detectedAt: '2026-05-19T11:08:00Z',
    severity: 'high',
    title: 'Trailwind false-positive AEB triggers near overhead structures on highway off-ramps',
    summary:
      'Trailwind validation vehicles running ADAS firmware v3.0.0-rc1 (NVIDIA Drive Orin) emit phantom forward-collision warnings and trigger AEB when passing under highway gantries and overhead signage on off-ramp geometry. Perception is misclassifying overhead structures as stopped lead vehicles. Pre-prod blocker for Trailwind ADAS sign-off — must close before any homologation testing.',
    affectedFleets: ['be07-test-fleet-001'],
    affectedVehicleCount: 8,
    cohortDescription:
      'Trailwind vehicles running ADAS-DC firmware v3.0.0-rc1, on highway off-ramp test profiles with overhead structure exposure',
    metricName: 'False-positive AEB events',
    metricUnit: 'events per 1000 km',
    metricBaseline: 0.2,
    metricObserved: 1.8,
    metricDeltaPercent: 800,
    status: 'investigating',
    modelLine: 'Trailwind',
    domain: 'adas',
    productionPhase: 'pre-prod',
    implicatedECUs: ['ADAS'],
  },

  // ===== POST-PROD MEDIUM: HV insulation DTC in coastal humidity =====
  {
    anomalyId: 'anom-be6-safety-002',
    detectedAt: '2026-05-19T07:33:00Z',
    severity: 'medium',
    title: 'Windrose phantom DTC P0AA6 (HV system isolation) intermittent post-charge in coastal humidity',
    summary:
      'Windrose vehicles in Mumbai and Chennai coastal humid zones intermittently emit DTC P0AA6 (HV system isolation fault) within 30 minutes of completing AC charging. DTC self-clears after vehicle dry-out (90-180 min). No actual isolation breach detected by precision insulation monitor; suspected transient condensation in HV connector backshells. Customer-visible warning lamp.',
    affectedFleets: ['be6-prod-cohort-001'],
    affectedVehicleCount: 9,
    cohortDescription:
      'Windrose in coastal humid sub-regions, post-charge interval, ambient dewpoint > 24°C',
    metricName: 'P0AA6 events per 1000 charge cycles',
    metricUnit: 'events',
    metricBaseline: 0.5,
    metricObserved: 14.2,
    metricDeltaPercent: 2740,
    status: 'investigating',
    modelLine: 'Windrose',
    domain: 'safety',
    productionPhase: 'post-prod',
    implicatedECUs: ['BMS', 'CCU'],
  },

  // ===== POST-PROD MEDIUM: Charging interop with Tata Power =====
  {
    anomalyId: 'anom-be6-charging-002',
    detectedAt: '2026-05-19T06:42:00Z',
    severity: 'medium',
    title: 'Windrose ISO 15118 plug-and-charge handshake fails on Tata Power Spark Mk2 50kW chargers',
    summary:
      'Windrose vehicles attempting plug-and-charge on Tata Power Spark Mk2 DC fast chargers fail at TLS handshake (78% success rate vs 99% baseline across other chargers). Falls back to manual auth, which works. Other charger models (ABB Terra, Delta DC Wallbox, Meridian-branded) succeed at expected rate. Vehicle-side EXI buffer suspected.',
    affectedFleets: ['be6-prod-cohort-001'],
    affectedVehicleCount: 12,
    cohortDescription:
      'Windrose charge sessions on Tata Power Spark Mk2 chargers (firmware ≤ 5.4.1)',
    metricName: 'Plug-and-charge handshake success rate',
    metricUnit: '%',
    metricBaseline: 99.0,
    metricObserved: 78.0,
    metricDeltaPercent: -21.2,
    status: 'investigating',
    modelLine: 'Windrose',
    domain: 'charging',
    productionPhase: 'post-prod',
    implicatedECUs: ['CCU'],
  },

  // ===== PRE-PROD MEDIUM: TCU OTA payload memory pressure =====
  {
    anomalyId: 'anom-be07-ota-003',
    detectedAt: '2026-05-19T13:55:00Z',
    severity: 'medium',
    title: 'Trailwind TCU OTA payload assembly fails when in-vehicle Wi-Fi hotspot is active',
    summary:
      'Trailwind validation vehicles with concurrent FleetWise streaming and customer-facing Wi-Fi hotspot enabled fail OTA payload assembly due to TCU heap exhaustion. Pre-prod issue; will gate the OTA throughput for the Windrose production fleet at scale unless TCU SW v4.2.0 ships with corrected memory pool sizing.',
    affectedFleets: ['be07-test-fleet-001'],
    affectedVehicleCount: 4,
    cohortDescription:
      'Trailwind with TCU SW v4.2.0-rc1 + active Wi-Fi hotspot during OTA download window',
    metricName: 'OTA download success rate',
    metricUnit: '%',
    metricBaseline: 99.5,
    metricObserved: 84.0,
    metricDeltaPercent: -15.6,
    status: 'investigating',
    modelLine: 'Trailwind',
    domain: 'connectivity',
    productionPhase: 'pre-prod',
    implicatedECUs: ['TCU'],
  },

  // ===== PRE-PROD MEDIUM: Heat pump COP in cold-region testing =====
  {
    anomalyId: 'anom-be07-hvac-004',
    detectedAt: '2026-05-18T09:14:00Z',
    severity: 'medium',
    title: 'Trailwind cabin heat-pump COP 14% below sim model in Punjab cold-region testing',
    summary:
      'Trailwind validation vehicles operating in Punjab winter testing (ambient -2°C to 8°C) show heat pump coefficient-of-performance 14% below simulation prediction, translating to projected 12-15% range loss in cold conditions. Recommend Valeo refrigerant circuit thermal model recalibration and re-spec of low-temp expansion valve.',
    affectedFleets: ['be07-test-fleet-001'],
    affectedVehicleCount: 6,
    cohortDescription: 'Trailwind in Punjab-Cool cold-region road trial, ambient < 8°C',
    metricName: 'HVAC heat pump COP',
    metricUnit: 'COP',
    metricBaseline: 3.4,
    metricObserved: 2.92,
    metricDeltaPercent: -14.1,
    status: 'investigating',
    modelLine: 'Trailwind',
    domain: 'thermal',
    productionPhase: 'pre-prod',
    implicatedECUs: ['BCM'],
  },

  // ===== PRE-PROD LOW: Trailwind inverter switching losses =====
  {
    anomalyId: 'anom-be07-powertrain-005',
    detectedAt: '2026-05-17T16:50:00Z',
    severity: 'low',
    title: 'Trailwind Bosch SiC inverter switching losses 11% above sim model at peak torque',
    summary:
      'All Trailwind validation vehicles in dyno characterization show inverter switching losses approximately 11% above the supplier sim model at peak-torque operating points. Recommend Bosch supplier model recalibration and a re-check of inverter cooling jacket flow rate spec for Trailwind thermal envelope.',
    affectedFleets: ['be07-test-fleet-001'],
    affectedVehicleCount: 25,
    cohortDescription: 'All Trailwind validation vehicles on chassis dyno characterization runs',
    metricName: 'Inverter switching loss',
    metricUnit: 'W (peak torque)',
    metricBaseline: 380,
    metricObserved: 422,
    metricDeltaPercent: 11.0,
    status: 'new',
    modelLine: 'Trailwind',
    domain: 'powertrain',
    productionPhase: 'pre-prod',
    implicatedECUs: ['VCU'],
  },

  // ===== POST-PROD LOW: Charge tail current decoy (kept from original) =====
  {
    anomalyId: 'anom-be6-charging-tail-006',
    detectedAt: '2026-05-19T06:42:00Z',
    severity: 'low',
    title: 'Windrose DC fast-charge tail current ramp-down 8% slower than baseline at SoC > 80%',
    summary:
      'Subset of Windrose production fleet shows slower charge curve in 80-100% SoC band. Suspected charger-vehicle communication parameter drift. No safety impact identified. Monitoring.',
    affectedFleets: ['be6-prod-cohort-001'],
    affectedVehicleCount: 47,
    cohortDescription: 'Windrose vehicles using Meridian-branded DC fast chargers',
    metricName: 'Tail current ramp-down rate',
    metricUnit: '% per minute',
    metricBaseline: 4.2,
    metricObserved: 3.86,
    metricDeltaPercent: -8.1,
    status: 'investigating',
    modelLine: 'Windrose',
    domain: 'charging',
    productionPhase: 'post-prod',
    implicatedECUs: ['CCU'],
  },

  // ===== PRE-PROD LOW: Suspension model recalibration =====
  {
    anomalyId: 'anom-be07-suspension-007',
    detectedAt: '2026-05-17T11:30:00Z',
    severity: 'low',
    title: 'Trailwind front-strut peak load 6% above sim model on rough-road profiles',
    summary:
      'Instrumented Trailwind validation vehicles show higher front strut peak loads than simulation predicted on standard rough-road test surface. Recommend FE model recalibration. Within acceptable margin for now.',
    affectedFleets: ['be07-test-fleet-001'],
    affectedVehicleCount: 4,
    cohortDescription: 'Trailwind vehicles on rough-road validation track',
    metricName: 'Strut peak load',
    metricUnit: 'kN',
    metricBaseline: 8.4,
    metricObserved: 8.91,
    metricDeltaPercent: 6.1,
    status: 'investigating',
    modelLine: 'Trailwind',
    domain: 'powertrain',
    productionPhase: 'pre-prod',
  },

  // ===== POST-PROD LOW: HVAC compressor cycling =====
  {
    anomalyId: 'anom-be6-hvac-008',
    detectedAt: '2026-05-19T03:18:00Z',
    severity: 'low',
    title: 'Windrose HVAC compressor cycle frequency 4% above baseline in southern monsoon regions',
    summary:
      'Karnataka and Tamil Nadu Windrose vehicles showing slightly elevated HVAC compressor cycling. Within tolerance but trending. Monitoring.',
    affectedFleets: ['be6-prod-cohort-001'],
    affectedVehicleCount: 18,
    cohortDescription: 'Windrose in Karnataka-Moderate and Tamil-Nadu-Hot regions, May–June',
    metricName: 'HVAC compressor cycles/hour',
    metricUnit: 'cycles/hour',
    metricBaseline: 32,
    metricObserved: 33.3,
    metricDeltaPercent: 4.1,
    status: 'new',
    modelLine: 'Windrose',
    domain: 'thermal',
    productionPhase: 'post-prod',
    implicatedECUs: ['BCM'],
  },

  // ===== POST-PROD LOW: Tire wear (kept) =====
  {
    anomalyId: 'anom-be6-tire-009',
    detectedAt: '2026-05-15T14:22:00Z',
    severity: 'low',
    title: 'Windrose front-left tire pressure loss rate trending above norm in cohort using OEM-A tires',
    summary:
      'Slow pressure-loss trend in front-left tire across vehicles using OEM-A tire SKU. Within OEM tolerance but warrants supplier discussion.',
    affectedFleets: ['be6-prod-cohort-001'],
    affectedVehicleCount: 12,
    cohortDescription: 'Windrose with OEM-A front-left tire SKU',
    metricName: 'Tire pressure loss',
    metricUnit: 'PSI/week',
    metricBaseline: 0.8,
    metricObserved: 1.1,
    metricDeltaPercent: 37.5,
    status: 'investigating',
    modelLine: 'Windrose',
    domain: 'safety',
    productionPhase: 'post-prod',
  },
];

export const HERO_ANOMALY_ID = 'anom-be6-thermal-001';

export const getAnomaly = (anomalyId: string): Anomaly | undefined =>
  ENGINEERING_ANOMALIES.find((a) => a.anomalyId === anomalyId);
