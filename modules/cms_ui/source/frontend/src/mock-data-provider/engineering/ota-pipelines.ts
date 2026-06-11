// Engineering OTA pipelines — software build/sign/deploy lifecycle for vehicle ECU firmware.
// Used by the OTA Rollouts tab on the engineering fleet detail page, and by the
// "Ship to fleet" flow from the Digital Thread / Design Options pages.
//
// Build #4823 is the demo's hero pipeline: it ships BMS firmware v3.3.0 (the
// thermal-compensation fix) through the full lifecycle — virtual SiL test on
// QEMU/Renode, then validation fleet (BE.07), then canary on BE 6, then full
// production rollout.

export type PipelineStageId =
  | 'build'
  | 'sil'
  | 'validation-fleet'
  | 'canary'
  | 'full-rollout';

export type StageStatus =
  | 'completed'
  | 'in-progress'
  | 'pending'
  | 'failed'
  | 'skipped';

export interface PipelineStage {
  id: PipelineStageId;
  name: string;
  status: StageStatus;
  startedAt?: string;
  completedAt?: string;
  /** Stage-specific context message — what's happening / what completed. */
  description: string;
  /** Stage-specific metrics (e.g., "9/200 vehicles received", "all 25 passed"). */
  metrics?: { label: string; value: string }[];
  /** Target fleet ID (if the stage targets a fleet). */
  targetFleetId?: string;
  /** Stage gate: must be passed for the next stage to start. */
  gateBy?: string;
}

export interface OTAArtifact {
  artifactId: string;
  filename: string;
  hash: string;
  signedBy: string;
  sizeBytes: number;
  /**
   * Artifact category — drives the icon and sub-description in the UI:
   * - 'firmware': ECU firmware binary (the thing flashed onto the vehicle)
   * - 'decoder-manifest': AWS IoT FleetWise decoder manifest update
   *    (adds CAN message → signal decoding for new signals introduced by
   *    the firmware)
   * - 'fleetwise-campaign': AWS IoT FleetWise campaign config
   *    (collection scheme that begins gathering the new signals from the
   *    target fleet once firmware is deployed)
   */
  type?: 'firmware' | 'decoder-manifest' | 'fleetwise-campaign';
  /** Short human-readable description of what this artifact does. */
  description?: string;
}

export interface OTAPipeline {
  pipelineId: string;
  /** Display label, e.g. "Build #4823". */
  label: string;
  /** ECU being updated. */
  targetECU: string;
  /** Versions: from → to. */
  fromVersion: string;
  toVersion: string;
  /** Why this pipeline was kicked off — links back to the design loop. */
  rationale: string;
  /** Optional reference to the design option that produced this build. */
  designOptionId?: string;
  /** Optional reference to the anomaly that triggered the design loop. */
  triggeringAnomalyId?: string;
  triggeredBy: string;
  triggeredAt: string;
  artifacts: OTAArtifact[];
  stages: PipelineStage[];
  /** Workbench/project context (the engineering environment that built this). */
  workbench: {
    name: string;
    type: 'thermal-validation' | 'powertrain' | 'adas' | 'safety';
    leadEngineer: string;
    /** AMI image used by the virtual engineering workbench. */
    amiImage?: string;
    amiVersion?: string;
    /** Virtual target / simulation environment. */
    virtualTarget?: string;
    /** Software-in-Loop coverage description. */
    silCoverage?: string;
    /** Most recent commit on the workbench branch that produced this build. */
    lastCommit?: {
      sha: string;
      message: string;
      author: string;
      timestamp: string;
    };
    /** Optional workbench engagement summary (cumulative). */
    summary?: {
      totalCommits: number;
      activeContributors: number;
      simRunsLast7Days: number;
    };
  };
  /** Overall pipeline state — derived from stages. */
  status: 'in-progress' | 'completed' | 'failed' | 'cancelled';
  /**
   * Closed-loop validation: post-fix telemetry from canary recipients.
   * Populated for in-flight pipelines that have completed the canary stage.
   * Demonstrates the design change actually moving the metric it set out to fix,
   * including the appearance of new signals introduced by the firmware update.
   */
  postFixValidation?: PostFixValidation;
}

export interface PostFixValidationMetric {
  label: string;
  /** Pre-fix value (string with unit), null if metric didn't exist before this build. */
  preFix: string | null;
  /** Post-fix value (string with unit). */
  postFix: string;
  /** Optional pre→post delta description ("-1.9°C", "−9%", "−12"). */
  delta?: string;
  /** Direction: "improved" (green), "regressed" (red), "neutral" (grey). */
  direction?: 'improved' | 'regressed' | 'neutral';
  /** True if this signal didn't exist in the pre-fix firmware (introduced by this build). */
  isNewSignal?: boolean;
  /** Coverage description for new signals ("active in 8/9 recipients"). */
  coverage?: string;
}

export interface PostFixValidation {
  /** Human-readable window description ("Last 4 hours, 9 of 10 canary recipients"). */
  windowDescription: string;
  /** When the canary stage applied / when post-fix observations begin. */
  windowStart: string;
  /** Number of vehicles in the canary that are reporting. */
  reportingCount: number;
  totalCanaryCount: number;
  metrics: PostFixValidationMetric[];
  /** Signals introduced by this firmware that are actively arriving from canary vehicles. */
  newSignalsArriving: string[];
}

// ============================================================================
// BUILD #4823 — the hero pipeline (BMS v3.2.1 → v3.3.0)
// ============================================================================

const NOW_DEMO = '2026-05-19T15:55:00Z';

export const BUILD_4823: OTAPipeline = {
  pipelineId: 'BUILD-4823',
  label: 'Build #4823',
  targetECU: 'BMS',
  fromVersion: '3.2.1',
  toVersion: '3.3.0',
  rationale:
    'Implements Option B from the BE 6 thermal degradation investigation — dynamic derate referencing Voltrix datasheet rev 2.3 thermal limit. Closes the gap between the cell vendor spec (42°C) and the BE 6 pack thermal envelope (45°C).',
  designOptionId: 'design-opt-002',
  triggeringAnomalyId: 'anom-be6-thermal-001',
  triggeredBy: 'engineer@example.com',
  triggeredAt: '2026-05-19T14:30:00Z',
  artifacts: [
    {
      artifactId: 'ART-bms-3.3.0-bin',
      type: 'firmware',
      filename: 'mfg-bms-master-v2-3.3.0.signed.bin',
      hash: 'sha256:8f2c4a1b9d6e3f0a5b8c2d4e7f1a0b3c6d9e2f5a8b1c4d7e0f3a6b9c2d5e8f1a',
      signedBy: 'acme-ota-prod',
      sizeBytes: 2_481_152,
      description: 'BMS Master Controller firmware v3.3.0 — adds dynamic thermal derate algorithm.',
    },
    {
      artifactId: 'ART-decoder-manifest-v18',
      type: 'decoder-manifest',
      filename: 'cms-prod-decoder-manifest-v18.json',
      hash: 'sha256:3a7b1c8d9e4f5a2b6c0d8e1f3a5b7c9d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b',
      signedBy: 'acme-fleetwise-prod',
      sizeBytes: 48_640,
      description:
        'AWS IoT FleetWise decoder manifest — adds CAN message decoding for two new BMS signals: ThermalCompensationFactor (CAN 0x18A, scale 1/256) and DerateActiveSeconds (CAN 0x18B, scale 1).',
    },
    {
      artifactId: 'ART-campaign-bms-thermal-validation',
      type: 'fleetwise-campaign',
      filename: 'cms-prod-campaign-bms-thermal-validation.json',
      hash: 'sha256:5c1d3e9f7a2b4c6d8e0f1a3b5c7d9e1f3a5b7c9d1e3f5a7b9c1d3e5f7a9b1c3d',
      signedBy: 'acme-fleetwise-prod',
      sizeBytes: 12_288,
      description:
        'AWS IoT FleetWise campaign — condition-based collection scheme that gathers ThermalCompensationFactor and DerateActiveSeconds at 1 Hz from canary recipients, with auto-extend to full BE 6 cohort once canary stage promotes.',
    },
  ],
  workbench: {
    name: 'BE.07-thermal-validation',
    type: 'thermal-validation',
    leadEngineer: 'engineer@example.com',
    amiImage: 'bms-eng-amd64',
    amiVersion: '2026-04-r3',
    virtualTarget: 'QEMU + Renode (BMS Master Controller V2 simulation)',
    silCoverage: '100% across thermal envelope (-30 °C to +60 °C)',
    lastCommit: {
      sha: '9b2f4c1',
      message: 'BMS v3.3.0: dynamic thermal derate per Voltrix datasheet rev 2.3',
      author: 'engineer@example.com',
      timestamp: '2026-05-19T14:22:00Z',
    },
    summary: {
      totalCommits: 47,
      activeContributors: 3,
      simRunsLast7Days: 18,
    },
  },
  status: 'in-progress',
  stages: [
    {
      id: 'build',
      name: 'Build',
      status: 'completed',
      startedAt: '2026-05-19T14:30:00Z',
      completedAt: '2026-05-19T14:42:00Z',
      description: 'CI build from BE.07-thermal-validation workbench. Compiled, statically analyzed, MISRA-C compliance verified, cryptographically signed.',
      metrics: [
        { label: 'Compile time',    value: '4 min 12 s' },
        { label: 'MISRA-C',         value: '0 errors, 0 warnings' },
        { label: 'Static analysis', value: '0 critical, 2 minor' },
        { label: 'Signature',       value: 'acme-ota-prod (RSA-3072)' },
      ],
    },
    {
      id: 'sil',
      name: 'Software-in-Loop (Virtual Target)',
      status: 'completed',
      startedAt: '2026-05-19T14:43:00Z',
      completedAt: '2026-05-19T15:18:00Z',
      description:
        'QEMU + Renode virtual ECU target running thermal regression suite. Replays 6 months of telemetry from the affected cohort against the new firmware to verify the derate algorithm engages correctly without sacrificing peak-power capability.',
      metrics: [
        { label: 'Test cases',                 value: '247 / 247 passed' },
        { label: 'Thermal envelope coverage',  value: '100% (-30°C to +60°C)' },
        { label: 'Peak-power regression',      value: 'within 0.4% of baseline' },
        { label: 'Cycle-life simulation',      value: 'projects 2.1% improvement at 5 years in hot regions' },
      ],
    },
    {
      id: 'validation-fleet',
      name: 'BE.07 Validation Fleet',
      status: 'completed',
      startedAt: '2026-05-19T15:20:00Z',
      completedAt: '2026-05-19T15:48:00Z',
      description:
        'OTA delivery to all 25 BE.07 prototype vehicles at Pune R&D Center. Vehicles instrumented with full CAN dump and dyno cell-temperature monitoring. 90-minute thermal soak + run profile.',
      targetFleetId: 'be07-test-fleet-001',
      gateBy: 'Pune R&D test lead',
      metrics: [
        { label: 'Vehicles updated', value: '25 / 25' },
        { label: 'Update success',   value: '25 / 25 (100%)' },
        { label: 'Cell temp peak',   value: '40.8°C (within new 42°C limit)' },
        { label: 'Roll-back events', value: '0' },
      ],
    },
    {
      id: 'canary',
      name: 'BE 6 Canary Rollout (5%)',
      status: 'in-progress',
      startedAt: '2026-05-19T15:50:00Z',
      description:
        'OTA delivery to a 5% slice of the BE 6 production cohort (10 of 200 vehicles). Slice intentionally biased toward the affected hot-region cohort (Maharashtra-Hot, Gujarat-Hot) to validate the fix on the originally-affected vehicles. 24-hour soak before promoting.',
      targetFleetId: 'be6-prod-cohort-001',
      gateBy: '24-hour SoH telemetry review',
      metrics: [
        { label: 'Vehicles updated',     value: '9 / 10' },
        { label: 'Update success',       value: '9 / 10 (1 vehicle offline, retry queued)' },
        { label: 'Telemetry collected',  value: '8 / 9 reporting' },
        { label: 'SoH degradation rate (post-update, last 4 hr)', value: '0.91 %/mo (baseline 0.90)' },
      ],
    },
    {
      id: 'full-rollout',
      name: 'BE 6 Full Rollout',
      status: 'pending',
      description:
        'Promotion to all remaining 190 BE 6 production vehicles. Auto-promotes after canary 24-hour SoH review. Engineer can cancel or hold via the Rollout console.',
      targetFleetId: 'be6-prod-cohort-001',
      gateBy: 'Auto-promote on canary green',
    },
  ],
  postFixValidation: {
    windowDescription: 'Since canary stage applied — 9 of 10 recipients reporting',
    windowStart: '2026-05-19T15:48:00Z',
    reportingCount: 9,
    totalCanaryCount: 10,
    newSignalsArriving: [
      'Vehicle.Powertrain.TractionBattery.ThermalCompensationFactor',
      'Vehicle.Powertrain.TractionBattery.DerateActiveSeconds',
    ],
    metrics: [
      {
        label: 'Vehicle.Powertrain.TractionBattery.ThermalCompensationFactor',
        preFix: null,
        postFix: '0.87 – 0.94',
        isNewSignal: true,
        coverage: 'active in 8 of 9 reporters',
        direction: 'improved',
      },
      {
        label: 'Vehicle.Powertrain.TractionBattery.DerateActiveSeconds',
        preFix: null,
        postFix: '47 s/trip avg, 312 s peak',
        isNewSignal: true,
        coverage: 'active in 8 of 9 reporters',
        direction: 'neutral',
      },
      {
        label: 'Battery peak temperature',
        preFix: '43.1 °C',
        postFix: '41.2 °C',
        delta: '−1.9 °C',
        direction: 'improved',
      },
      {
        label: 'Cell temperature spread',
        preFix: '3.4 °C',
        postFix: '2.1 °C',
        delta: '−1.3 °C',
        direction: 'improved',
      },
      {
        label: 'SoH degradation rate (windowed)',
        preFix: '1.01 %/mo',
        postFix: '0.92 %/mo',
        delta: '−9% (within baseline 0.9 envelope)',
        direction: 'improved',
      },
      {
        label: 'Thermal events (4hr window)',
        preFix: '12 cells',
        postFix: '0 cells',
        delta: '−12',
        direction: 'improved',
      },
      {
        label: 'Voltrix datasheet thermal limit (42 °C) breaches',
        preFix: '4 vehicles',
        postFix: '0 vehicles',
        delta: '−4',
        direction: 'improved',
      },
    ],
  },
};

// ============================================================================
// HISTORICAL PIPELINES (decoy / context for the activity feed)
// ============================================================================

export const HISTORICAL_PIPELINES: OTAPipeline[] = [
  {
    pipelineId: 'BUILD-4822',
    label: 'Build #4822',
    targetECU: 'IVI',
    fromVersion: '14.1.4',
    toVersion: '14.2.0',
    rationale: 'Visteon cockpit Hindi voice assistant accuracy fix and minor UI polish.',
    triggeredBy: 'visteon-rel@acmemotors.com',
    triggeredAt: '2026-05-15T10:00:00Z',
    workbench: { name: 'visteon-cockpit', type: 'powertrain', leadEngineer: 'visteon-rel@acmemotors.com' },
    artifacts: [{ artifactId: 'ART-ivi-14.2.0', filename: 'visteon-cockpit-14.2.0.bin', hash: 'sha256:…', signedBy: 'visteon-prod', sizeBytes: 12_582_912 }],
    status: 'completed',
    stages: [
      { id: 'build',             name: 'Build',                status: 'completed', completedAt: '2026-05-15T10:42Z', description: '' },
      { id: 'sil',               name: 'SiL',                  status: 'completed', completedAt: '2026-05-15T11:15Z', description: '' },
      { id: 'validation-fleet',  name: 'BE.07 Validation Fleet', status: 'completed', completedAt: '2026-05-15T13:00Z', description: '', targetFleetId: 'be07-test-fleet-001' },
      { id: 'canary',            name: 'Canary 5%',            status: 'completed', completedAt: '2026-05-16T13:00Z', description: '', targetFleetId: 'be6-prod-cohort-001' },
      { id: 'full-rollout',      name: 'Full Rollout',         status: 'completed', completedAt: '2026-05-17T18:30Z', description: '', targetFleetId: 'be6-prod-cohort-001' },
    ],
  },
  {
    pipelineId: 'BUILD-4821',
    label: 'Build #4821',
    targetECU: 'BMS',
    fromVersion: '3.2.0',
    toVersion: '3.2.1',
    rationale: 'Cell balancing tuning; routine update. Did not address Voltrix Q3 thermal-limit issue.',
    triggeredBy: 'bms-rel@acmemotors.com',
    triggeredAt: '2025-04-22T09:00:00Z',
    workbench: { name: 'bms-routine', type: 'thermal-validation', leadEngineer: 'bms-rel@acmemotors.com' },
    artifacts: [{ artifactId: 'ART-bms-3.2.1', filename: 'mfg-bms-master-v2-3.2.1.bin', hash: 'sha256:…', signedBy: 'acme-ota-prod', sizeBytes: 2_408_960 }],
    status: 'completed',
    stages: [
      { id: 'build',             name: 'Build',                status: 'completed', completedAt: '2025-04-22T09:35Z', description: '' },
      { id: 'sil',               name: 'SiL',                  status: 'completed', completedAt: '2025-04-22T10:50Z', description: '' },
      { id: 'validation-fleet',  name: 'BE.07 Validation Fleet', status: 'completed', completedAt: '2025-04-22T15:00Z', description: '', targetFleetId: 'be07-test-fleet-001' },
      { id: 'canary',            name: 'Canary 5%',            status: 'completed', completedAt: '2025-04-23T15:00Z', description: '', targetFleetId: 'be6-prod-cohort-001' },
      { id: 'full-rollout',      name: 'Full Rollout',         status: 'completed', completedAt: '2025-04-24T22:00Z', description: '', targetFleetId: 'be6-prod-cohort-001' },
    ],
  },
  {
    pipelineId: 'BUILD-4820',
    label: 'Build #4820',
    targetECU: 'ADAS',
    fromVersion: '2.1.2',
    toVersion: '2.1.3',
    rationale: 'False-positive AEB events on highway off-ramps (NVIDIA fix).',
    triggeredBy: 'adas-rel@acmemotors.com',
    triggeredAt: '2025-04-15T12:00:00Z',
    workbench: { name: 'adas-perception', type: 'adas', leadEngineer: 'adas-rel@acmemotors.com' },
    artifacts: [{ artifactId: 'ART-adas-2.1.3', filename: 'mfg-adas-orin-2.1.3.bin', hash: 'sha256:…', signedBy: 'nvidia-acme-prod', sizeBytes: 48_234_496 }],
    status: 'completed',
    stages: [
      { id: 'build',             name: 'Build',                status: 'completed', completedAt: '2025-04-15T13:20Z', description: '' },
      { id: 'sil',               name: 'SiL',                  status: 'completed', completedAt: '2025-04-15T17:00Z', description: '' },
      { id: 'validation-fleet',  name: 'BE.07 Validation Fleet', status: 'completed', completedAt: '2025-04-16T15:00Z', description: '', targetFleetId: 'be07-test-fleet-001' },
      { id: 'canary',            name: 'Canary 5%',            status: 'completed', completedAt: '2025-04-17T15:00Z', description: '', targetFleetId: 'be6-prod-cohort-001' },
      { id: 'full-rollout',      name: 'Full Rollout',         status: 'completed', completedAt: '2025-04-18T20:00Z', description: '', targetFleetId: 'be6-prod-cohort-001' },
    ],
  },
  {
    pipelineId: 'BUILD-4819',
    label: 'Build #4819',
    targetECU: 'TCU',
    fromVersion: '4.0.7',
    toVersion: '4.1.0',
    rationale: 'AWS IoT FleetWise Edge Agent upgrade; new collection schemes for charging events.',
    triggeredBy: 'tcu-rel@acmemotors.com',
    triggeredAt: '2025-03-15T08:00:00Z',
    workbench: { name: 'tcu-platform', type: 'powertrain', leadEngineer: 'tcu-rel@acmemotors.com' },
    artifacts: [{ artifactId: 'ART-tcu-4.1.0', filename: 'mfg-tcu-g2-4.1.0.bin', hash: 'sha256:…', signedBy: 'acme-ota-prod', sizeBytes: 18_874_368 }],
    status: 'completed',
    stages: [
      { id: 'build',             name: 'Build',                status: 'completed', completedAt: '2025-03-15T09:00Z', description: '' },
      { id: 'sil',               name: 'SiL',                  status: 'completed', completedAt: '2025-03-15T11:30Z', description: '' },
      { id: 'validation-fleet',  name: 'BE.07 Validation Fleet', status: 'completed', completedAt: '2025-03-16T15:00Z', description: '', targetFleetId: 'be07-test-fleet-001' },
      { id: 'canary',            name: 'Canary 5%',            status: 'completed', completedAt: '2025-03-17T15:00Z', description: '', targetFleetId: 'be6-prod-cohort-001' },
      { id: 'full-rollout',      name: 'Full Rollout',         status: 'completed', completedAt: '2025-03-19T18:00Z', description: '', targetFleetId: 'be6-prod-cohort-001' },
    ],
  },
  {
    pipelineId: 'BUILD-4818',
    label: 'Build #4818',
    targetECU: 'CCU',
    fromVersion: '2.3.0',
    toVersion: '2.3.1',
    rationale: 'ISO 15118 plug-and-charge interop fix for Tata Power chargers.',
    triggeredBy: 'ccu-rel@acmemotors.com',
    triggeredAt: '2025-02-14T10:00:00Z',
    workbench: { name: 'charging-interop', type: 'powertrain', leadEngineer: 'ccu-rel@acmemotors.com' },
    artifacts: [{ artifactId: 'ART-ccu-2.3.1', filename: 'lgi-obc-2.3.1.bin', hash: 'sha256:…', signedBy: 'lg-innotek-prod', sizeBytes: 1_572_864 }],
    status: 'completed',
    stages: [
      { id: 'build',             name: 'Build',                status: 'completed', completedAt: '2025-02-14T10:48Z', description: '' },
      { id: 'sil',               name: 'SiL',                  status: 'completed', completedAt: '2025-02-14T12:00Z', description: '' },
      { id: 'validation-fleet',  name: 'BE.07 Validation Fleet', status: 'completed', completedAt: '2025-02-14T17:00Z', description: '', targetFleetId: 'be07-test-fleet-001' },
      { id: 'canary',            name: 'Canary 5%',            status: 'completed', completedAt: '2025-02-15T17:00Z', description: '', targetFleetId: 'be6-prod-cohort-001' },
      { id: 'full-rollout',      name: 'Full Rollout',         status: 'completed', completedAt: '2025-02-17T22:30Z', description: '', targetFleetId: 'be6-prod-cohort-001' },
    ],
  },
];

export const ALL_PIPELINES: OTAPipeline[] = [BUILD_4823, ...HISTORICAL_PIPELINES];

// ============================================================================
// HELPERS
// ============================================================================

export function getPipeline(pipelineId: string): OTAPipeline | undefined {
  return ALL_PIPELINES.find((p) => p.pipelineId === pipelineId);
}

export function getPipelinesForFleet(fleetId: string): OTAPipeline[] {
  return ALL_PIPELINES.filter((p) =>
    p.stages.some((s) => s.targetFleetId === fleetId)
  );
}

export function getActivePipelinesForFleet(fleetId: string): OTAPipeline[] {
  return getPipelinesForFleet(fleetId).filter((p) => p.status === 'in-progress');
}

/** % completion of an in-progress pipeline (counts completed stages of total). */
export function pipelineProgress(p: OTAPipeline): number {
  const completed = p.stages.filter((s) => s.status === 'completed').length;
  return Math.round((completed / p.stages.length) * 100);
}

/**
 * IDs of canary-recipient vehicles for an active pipeline. For Build #4823,
 * the canary slice is biased toward the affected cohort (the originally-broken
 * vehicles get the fix first to validate the fix). 9 of the 10 canary slots
 * have been delivered.
 */
export const BUILD_4823_CANARY_VEHICLE_IDS: string[] = [
  // Affected cohort vehicles (Maharashtra-Hot + Gujarat-Hot, BATCH-MH-Q3-2025-A12/B14)
  'VEH-BE6-0061', 'VEH-BE6-0062', 'VEH-BE6-0063', 'VEH-BE6-0070', 'VEH-BE6-0080',
  'VEH-BE6-0085', 'VEH-BE6-0090', 'VEH-BE6-0095',
  // Non-affected baseline for control comparison
  'VEH-BE6-0001',
  // 10th vehicle (offline, pending retry) — VEH-BE6-0099 — keeps array length 9 here, 10 conceptually.
];

/** Returns true if the given vehicle has received the BMS v3.3.0 update via the canary stage. */
export function isBuild4823CanaryRecipient(vehicleId: string): boolean {
  return BUILD_4823_CANARY_VEHICLE_IDS.includes(vehicleId);
}
