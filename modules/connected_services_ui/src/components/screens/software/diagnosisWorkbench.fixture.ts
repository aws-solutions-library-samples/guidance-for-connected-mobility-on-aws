// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * diagnosisWorkbench.fixture.ts — Simulated data for the Diagnosis Workbench screen.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T4.4 / T5.4
 *
 * ## VIN vocabulary
 *
 * VINs are imported from subscriberLookup.fixture.ts, which derives them from
 * fleetHealth.fixture.ts — the single source of truth. Every VIN in
 * SIMULATED_SUBSCRIBER_DETAILS has a corresponding entry in VIN_SOFTWARE_STATES
 * so that the "View software history" button never lands on the not-found alert.
 *
 * The fixtureReferentialIntegrity guard (Contract 3) enforces this at test time.
 *
 * ## Provenance contract
 *
 * Every displayed-value field is a ProvenanceValue<T> with provenance: 'simulated'.
 * Structural keys (id) are exempt from wrapping per provenanceFixtures guard.
 * The five Tier2Artifact envelope fields (computed_at, confidence, evidence,
 * agent_version, inputs_hash) are also exempt — they describe the conclusion.
 *
 * ## Seam 3 (RXSWIN) — deferred
 *
 * RXSWIN fields are plain ProvenanceValue<string> fields, never inside evidence[].
 * See decisions.md 2026-09-04 — "Seam 3 (RXSWIN classification) is deferred."
 *
 * ## No location fields
 *
 * No trip/GPS/location/odometer field appears anywhere in this file.
 */

import type { ProvenanceValue, Tier2Artifact, EvidenceChip } from "../../../types";
import {
  DEGRADED_VIN_0,
  DEGRADED_VIN_1,
  UNREACHABLE_VIN_0,
} from "../connectivity/subscriberLookup.fixture";

// ---------------------------------------------------------------------------
// Domain types
// ---------------------------------------------------------------------------

export interface SoftwarePackage {
  id: string;
  name: ProvenanceValue<string>;
  installedVersion: ProvenanceValue<string>;
  baselineVersion: ProvenanceValue<string>;
  hasDrift: ProvenanceValue<boolean>;
  lastUpdatedAt: ProvenanceValue<string>;
}

export interface VinSoftwareState {
  id: string;
  vin: ProvenanceValue<string>;
  market: ProvenanceValue<string>;
  overallDrift: ProvenanceValue<boolean>;
  packages: SoftwarePackage[];
}

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

function nodrData(vin: string, market: string): VinSoftwareState {
  return {
    id: vin,
    vin: sim(vin),
    market: sim(market),
    overallDrift: sim(false),
    packages: [
      {
        id: "pkg-tcam-fw",
        name: sim("TCAM Firmware"),
        installedVersion: sim("4.2.0"),
        baselineVersion: sim("4.2.0"),
        hasDrift: sim(false),
        lastUpdatedAt: sim("2026-08-20T11:30:00Z"),
      },
      {
        id: "pkg-modem-fw",
        name: sim("Modem Firmware"),
        installedVersion: sim("3.0.8"),
        baselineVersion: sim("3.0.8"),
        hasDrift: sim(false),
        lastUpdatedAt: sim("2026-08-01T07:00:00Z"),
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Per-VIN fixture catalogue — one entry per VIN in SIMULATED_SUBSCRIBER_DETAILS
//
// Fleet Health rows (12 total):
//   1HGCM82633A004352  connected     US
//   WBA3A5C51CF256985  degraded      DE  ← DEGRADED_VIN_0
//   MAKA0000000000003  connected     IN
//   1HGCM82633A004353  degraded      US  ← DEGRADED_VIN_1
//   WBA3A5C51CF256986  ntn_fallback  DE
//   1HGCM82633A004354  unreachable   US  ← UNREACHABLE_VIN_0
//   MAKA0000000000007  connected     IN
//   WBA3A5C51CF256987  degraded      DE
//   1HGCM82633A004355  connected     US
//   MAKA0000000000010  ntn_fallback  IN
//   1HGCM82633A004356  degraded      US
//   WBA3A5C51CF256988  unreachable   DE
// ---------------------------------------------------------------------------

const VIN_SOFTWARE_STATES: Record<string, VinSoftwareState> = {
  // connected VINs — no drift
  "1HGCM82633A004352": nodrData("1HGCM82633A004352", "US"),
  "MAKA0000000000003": nodrData("MAKA0000000000003", "IN"),
  "MAKA0000000000007": nodrData("MAKA0000000000007", "IN"),
  "1HGCM82633A004355": nodrData("1HGCM82633A004355", "US"),

  // ntn_fallback VINs — no drift
  "WBA3A5C51CF256986": nodrData("WBA3A5C51CF256986", "DE"),
  "MAKA0000000000010": nodrData("MAKA0000000000010", "IN"),

  // DEGRADED_VIN_0 — has firmware drift (click-path hop 3 target)
  [DEGRADED_VIN_0]: {
    id: DEGRADED_VIN_0,
    vin: sim(DEGRADED_VIN_0),
    market: sim("DE"),
    overallDrift: sim(true),
    packages: [
      {
        id: "pkg-tcam-fw",
        name: sim("TCAM Firmware"),
        installedVersion: sim("4.1.2"),
        baselineVersion: sim("4.2.0"),
        hasDrift: sim(true),
        lastUpdatedAt: sim("2026-08-14T09:22:00Z"),
      },
      {
        id: "pkg-modem-fw",
        name: sim("Modem Firmware"),
        installedVersion: sim("3.0.8"),
        baselineVersion: sim("3.0.8"),
        hasDrift: sim(false),
        lastUpdatedAt: sim("2026-07-30T14:10:00Z"),
      },
      {
        id: "pkg-ota-client",
        name: sim("OTA Client"),
        installedVersion: sim("2.5.1"),
        baselineVersion: sim("2.5.1"),
        hasDrift: sim(false),
        lastUpdatedAt: sim("2026-07-15T08:00:00Z"),
      },
    ],
  },

  // DEGRADED_VIN_1 — no drift
  [DEGRADED_VIN_1]: nodrData(DEGRADED_VIN_1, "US"),

  // UNREACHABLE_VIN_0 — no drift
  [UNREACHABLE_VIN_0]: nodrData(UNREACHABLE_VIN_0, "US"),

  // Additional degraded VINs
  "WBA3A5C51CF256987": nodrData("WBA3A5C51CF256987", "DE"),
  "1HGCM82633A004356": nodrData("1HGCM82633A004356", "US"),

  // Unreachable VINs
  "WBA3A5C51CF256988": nodrData("WBA3A5C51CF256988", "DE"),
};

export { VIN_SOFTWARE_STATES };

// ===========================================================================
// T5.4 types and fixtures — full six-step stepper
// ===========================================================================

// ---------------------------------------------------------------------------
// Step: Signal
// ---------------------------------------------------------------------------

export interface DiagnosisSignal {
  id: string;
  signalType: ProvenanceValue<string>;
  severity: ProvenanceValue<string>;
  detectedAt: ProvenanceValue<string>;
  affectedPopulationEstimate: ProvenanceValue<number>;
  // RXSWIN field — plain ProvenanceValue, never inside evidence[].
  // Seam 3 deferred: see decisions.md 2026-09-04.
  rxswinReference: ProvenanceValue<string>;
}

// ---------------------------------------------------------------------------
// Step: Diagnosis — Tier2Artifact backed
// ---------------------------------------------------------------------------

export interface DiagnosisHypothesisPayload {
  rank: ProvenanceValue<number>;
  description: ProvenanceValue<string>;
  confidence: ProvenanceValue<number>;
  affectedComponent: ProvenanceValue<string>;
  recommendedAction: ProvenanceValue<string>;
}

export type DiagnosisArtifact = Tier2Artifact<DiagnosisHypothesisPayload[]>;

// ---------------------------------------------------------------------------
// Step: Proposal — target criteria (pure, not artifact-backed)
// ---------------------------------------------------------------------------

export interface ProposalCriteria {
  /** Which market(s) to target. Empty = all markets. */
  markets: string[];
  /** Which TCU tiers to include. Empty = all tiers. */
  tcuTiers: string[];
  /** Connectivity states to include. Empty = all states. */
  connectivityStates: string[];
}

// ---------------------------------------------------------------------------
// Step: Approval
// ---------------------------------------------------------------------------

export interface ApprovalData {
  id: string;
  targetVehicleCount: ProvenanceValue<number>;
  bandwidthEstimateGb: ProvenanceValue<number>;
  terrestrialCostEstimate: ProvenanceValue<string>;
  ntnCostEstimate: ProvenanceValue<string>;
  blastRadiusEstimate: ProvenanceValue<string>;
}

// ---------------------------------------------------------------------------
// Step: Record
// ---------------------------------------------------------------------------

/** D12 naming: renamed from CampaignRecord to SoftwareCampaignWorkbenchRecord. */
export interface SoftwareCampaignWorkbenchRecord {
  id: string;
  softwareCampaignId: ProvenanceValue<string>;
  // RXSWIN touched — plain ProvenanceValue, never inside evidence[].
  // Seam 3 deferred: see decisions.md 2026-09-04.
  rxswinTouched: ProvenanceValue<string[]>;
  approvalRationale: ProvenanceValue<string>;
  rolloutOutcomeSummary: ProvenanceValue<string>;
  completedAt: ProvenanceValue<string>;
}

// ---------------------------------------------------------------------------
// Stepper workflow: top-level type
// ---------------------------------------------------------------------------

/** All six steps of the OTA campaign approval workflow. */
export interface WorkbenchWorkflow {
  signal: DiagnosisSignal;
  diagnosisArtifact: DiagnosisArtifact;
  approvalData: ApprovalData;
  record: SoftwareCampaignWorkbenchRecord;
}

// ---------------------------------------------------------------------------
// Simulated workflow fixture for DEGRADED_VIN_0
// ---------------------------------------------------------------------------

const DEGRADED_VIN_0_EVIDENCE: EvidenceChip[] = [
  { label: "Fleet Health signal", detail: "Signal type: firmware-version-drift at 4.1.2 vs baseline 4.2.0" },
  { label: "Package: TCAM Firmware", detail: "Installed 4.1.2 – baseline 4.2.0 – drift confirmed" },
  { label: "Population: Germany fleet", detail: "47 degraded vehicles in DE market as of last update" },
];

export const WORKBENCH_WORKFLOW: WorkbenchWorkflow = {
  signal: {
    id: "sig-tcam-drift-de-001",
    signalType: sim("firmware-version-drift"),
    severity: sim("medium"),
    detectedAt: sim("2026-09-03T11:05:44Z"),
    affectedPopulationEstimate: sim(31),
    rxswinReference: sim("RXSWIN-DE-TCAM-4.2.0"),
  },
  diagnosisArtifact: {
    computed_at: "2026-09-03T11:30:00Z",
    confidence: 0.87,
    evidence: DEGRADED_VIN_0_EVIDENCE,
    agent_version: "diagnosis-agent@1.2.0",
    inputs_hash: "sha256:a1b2c3d4e5f6000000000000000000000000000000000000000000000000000001",
    payload: [
      {
        rank: sim(1),
        description: sim("TCAM Firmware 4.1.2 drift from baseline 4.2.0 — known fix available"),
        confidence: sim(0.87),
        affectedComponent: sim("TCAM Firmware"),
        recommendedAction: sim("Deploy OTA campaign targeting DE fleet with TCU-2 / TCU-3"),
      },
      {
        rank: sim(2),
        description: sim("Modem Firmware 3.0.8 — at baseline, no action required"),
        confidence: sim(0.95),
        affectedComponent: sim("Modem Firmware"),
        recommendedAction: sim("No action required"),
      },
    ],
  },
  approvalData: {
    id: "approval-tcam-de-001",
    targetVehicleCount: sim(31),
    bandwidthEstimateGb: sim(2.1),
    terrestrialCostEstimate: sim("€ 0.18 / vehicle (LTE)"),
    ntnCostEstimate: sim("€ 1.25 / vehicle (NTN)"),
    blastRadiusEstimate: sim("DE market, TCU-2/TCU-3 tier, degraded + connected state"),
  },
  record: {
    id: "rec-tcam-de-001",
    softwareCampaignId: sim("SC-2026-09-03-TCAM-DE-001"),
    rxswinTouched: sim(["RXSWIN-DE-TCAM-4.2.0"]),
    approvalRationale: sim("Approved: known fix, bounded blast radius, no active safety issues reported"),
    rolloutOutcomeSummary: sim("Rollout complete — 31 vehicles updated to TCAM Firmware 4.2.0"),
    completedAt: sim("2026-09-04T08:00:00Z"),
  },
};
