// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * signalDetection.fixture.ts — Simulated data for the Signal Detection screen.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T5.3
 *
 * ## Tier 2 artifact contract
 *
 * Each signal is a Tier2Artifact<SignalPayload> carrying all five metadata
 * fields (computed_at, confidence, evidence, agent_version, inputs_hash) plus
 * a domain payload whose every displayed field is ProvenanceValue-wrapped.
 *
 * The five envelope fields are exempt from provenance wrapping per spec D4 and
 * the provenanceFixtures guard allowlist — they describe the conclusion, not
 * displayed data values.
 *
 * ## VIN vocabulary
 *
 * VINs are imported from subscriberLookup.fixture.ts constants, which are
 * derived from fleetHealth.fixture.ts — the single source of truth.
 * No VIN literals appear in this file.
 *
 * ## No location fields
 *
 * No trip / GPS / location / odometer field anywhere in this file.
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

/** The inner payload of a detected fault signal. */
export interface SignalPayload {
  /** Short human-readable fault signature — e.g. "firmware-version-drift". */
  faultSignature: ProvenanceValue<string>;
  /** Estimated number of vehicles in the affected population. */
  affectedVehicleCountEstimate: ProvenanceValue<number>;
  /** Free-form description of the signal. */
  description: ProvenanceValue<string>;
  /**
   * VIN(s) that triggered this signal. Used to pre-load the Diagnosis
   * Workbench when the user clicks "Diagnose" on this card.
   * The first VIN in the array is used as the signalId route parameter.
   */
  triggeringVins: ProvenanceValue<string[]>;
  /** Connectivity state associated with the triggering VINs. */
  connectivityState: ProvenanceValue<string>;
  /** Market identifier (e.g. "DE", "US", "IN"). */
  market: ProvenanceValue<string>;
}

/** A single detected fault signal — a Tier2Artifact with a SignalPayload. */
export type DetectedSignal = Tier2Artifact<SignalPayload>;

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

// ---------------------------------------------------------------------------
// Evidence chips
// ---------------------------------------------------------------------------

const EVIDENCE_FIRMWARE_DRIFT: EvidenceChip = {
  label: "Fleet Health: firmware-version-drift",
  detail: `VIN ${DEGRADED_VIN_0} — TCAM Firmware 4.1.2 vs baseline 4.2.0`,
};

const EVIDENCE_DEGRADED_POPULATION: EvidenceChip = {
  label: "Population: DE degraded VINs",
  detail: "47 degraded vehicles in DE market as of last agent run",
};

const EVIDENCE_DEGRADED_US_SIGNAL: EvidenceChip = {
  label: "Fleet Health: connectivity-degraded",
  detail: `VIN ${DEGRADED_VIN_1} — persistent RSP failure, US market`,
};

const EVIDENCE_US_DEGRADED_POPULATION: EvidenceChip = {
  label: "Population: US degraded VINs",
  detail: "12 degraded vehicles in US market as of last agent run",
};

const EVIDENCE_UNREACHABLE_ROAMING: EvidenceChip = {
  label: "Fleet Health: unreachable — roaming policy conflict",
  detail: `VIN ${UNREACHABLE_VIN_0} — APN mismatch in US roaming zone`,
};

const EVIDENCE_UNREACHABLE_POPULATION: EvidenceChip = {
  label: "Population: US unreachable VINs",
  detail: "4 unreachable vehicles with roaming-policy conflict pattern",
};

// ---------------------------------------------------------------------------
// Simulated signals — newest first (descending computed_at)
// ---------------------------------------------------------------------------

/**
 * Signal 1 — firmware-version-drift in DE market.
 *
 * Triggering VIN: DEGRADED_VIN_0 (first degraded row — DE).
 * Click-through navigates to /software/workbench/:signalId with the VIN as signalId.
 */
const SIGNAL_FIRMWARE_DRIFT_DE: DetectedSignal = {
  computed_at: "2026-09-03T11:30:00Z",
  confidence: 0.87,
  evidence: [EVIDENCE_FIRMWARE_DRIFT, EVIDENCE_DEGRADED_POPULATION],
  agent_version: "signal-detection-agent@1.2.0",
  inputs_hash:
    "sha256:a1b2c3d4e5f6000000000000000000000000000000000000000000000000000001",
  payload: {
    faultSignature: sim("firmware-version-drift"),
    affectedVehicleCountEstimate: sim(47),
    description: sim(
      "TCAM Firmware drift from baseline 4.2.0 to 4.1.2 detected across DE market."
    ),
    triggeringVins: sim([DEGRADED_VIN_0]),
    connectivityState: sim("degraded"),
    market: sim("DE"),
  },
};

/**
 * Signal 2 — connectivity-degraded in US market.
 *
 * Triggering VIN: DEGRADED_VIN_1 (second degraded row — US).
 */
const SIGNAL_CONNECTIVITY_DEGRADED_US: DetectedSignal = {
  computed_at: "2026-09-02T22:15:00Z",
  confidence: 0.74,
  evidence: [EVIDENCE_DEGRADED_US_SIGNAL, EVIDENCE_US_DEGRADED_POPULATION],
  agent_version: "signal-detection-agent@1.2.0",
  inputs_hash:
    "sha256:b2c3d4e5f6a7000000000000000000000000000000000000000000000000000002",
  payload: {
    faultSignature: sim("connectivity-degraded"),
    affectedVehicleCountEstimate: sim(12),
    description: sim(
      "Persistent RSP failure pattern across US degraded fleet. Potential carrier or APN issue."
    ),
    triggeringVins: sim([DEGRADED_VIN_1]),
    connectivityState: sim("degraded"),
    market: sim("US"),
  },
};

/**
 * Signal 3 — roaming-policy-conflict in US market.
 *
 * Triggering VIN: UNREACHABLE_VIN_0 (first unreachable row — US).
 */
const SIGNAL_ROAMING_POLICY_CONFLICT_US: DetectedSignal = {
  computed_at: "2026-09-01T08:42:00Z",
  confidence: 0.91,
  evidence: [EVIDENCE_UNREACHABLE_ROAMING, EVIDENCE_UNREACHABLE_POPULATION],
  agent_version: "signal-detection-agent@1.1.0",
  inputs_hash:
    "sha256:c3d4e5f6a7b8000000000000000000000000000000000000000000000000000003",
  payload: {
    faultSignature: sim("roaming-policy-conflict"),
    affectedVehicleCountEstimate: sim(4),
    description: sim(
      "APN mismatch causes unreachable status during US roaming zone transitions."
    ),
    triggeringVins: sim([UNREACHABLE_VIN_0]),
    connectivityState: sim("unreachable"),
    market: sim("US"),
  },
};

// ---------------------------------------------------------------------------
// Exported catalogue — newest first
// ---------------------------------------------------------------------------

/**
 * All simulated detected signals, ordered newest-first by computed_at.
 *
 * SignalDetectionView renders these as cards. The click-through pre-loads the
 * Diagnosis Workbench using the first entry of payload.triggeringVins as the
 * signalId route parameter.
 */
export const DETECTED_SIGNALS: DetectedSignal[] = [
  SIGNAL_FIRMWARE_DRIFT_DE,
  SIGNAL_CONNECTIVITY_DEGRADED_US,
  SIGNAL_ROAMING_POLICY_CONFLICT_US,
];
