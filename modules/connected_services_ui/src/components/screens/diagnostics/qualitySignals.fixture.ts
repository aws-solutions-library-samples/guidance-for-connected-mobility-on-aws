// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * qualitySignals.fixture.ts — Simulated data for the Quality Signals screen (T6.5).
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.5
 *
 * ## Tier 2 artifact contract
 *
 * Each quality signal is a Tier2Artifact<QualitySignalPayload> carrying all five
 * metadata fields (computed_at, confidence, evidence, agent_version, inputs_hash)
 * plus a domain payload whose every displayed field is ProvenanceValue-wrapped.
 *
 * The five envelope fields are exempt from provenance wrapping per spec D4 and
 * the provenanceFixtures guard allowlist — they describe the conclusion, not
 * displayed data values.
 *
 * ## Aggregate population data only
 *
 * Every payload field is an aggregate figure across the vehicle population.
 * No trip, GPS, driver-identity, or vehicle-location field appears anywhere
 * in this file.
 *
 * ## VIN vocabulary
 *
 * VINs referenced in evidence chips come from FLEET_ROWS exported constants —
 * no VIN literals appear in this file.
 *
 * ## Viewing this screen triggers no computation.
 *
 * This fixture represents pre-computed artifacts from the autonomous quality-
 * analysis agent. The QualitySignalsView component only reads these artifacts
 * and renders them. There is no inference, no fetch, and no API call at display
 * time. See agentic-tiers.md.
 */

import type { ProvenanceValue, Tier2Artifact, EvidenceChip } from "../../../types";
import { FLEET_ROWS } from "../connectivity/fleetHealth.fixture";

// ── Domain types ──────────────────────────────────────────────────────────────

export type SignalTrendDirection = "improving" | "stable" | "degrading";

/** The inner payload of a quality signal artifact. */
export interface QualitySignalPayload {
  /**
   * Human-readable name for this quality dimension.
   * E.g. "Connectivity Reliability" or "Firmware Coherence".
   */
  signalName: ProvenanceValue<string>;
  /**
   * Aggregate score for this quality dimension, 0..100.
   * Higher is better.
   */
  populationScore: ProvenanceValue<number>;
  /**
   * Trend direction compared to the previous agent run.
   * Aggregate — no per-vehicle breakdown.
   */
  trend: ProvenanceValue<SignalTrendDirection>;
  /**
   * Number of vehicles contributing to this signal's score.
   * Aggregate count — no individual identifiers.
   */
  vehicleCount: ProvenanceValue<number>;
  /**
   * Market scope for this signal (e.g. "All markets" or "US, DE").
   * Market attribution — not a GPS or location field.
   */
  scope: ProvenanceValue<string>;
  /**
   * Free-form summary of the signal's current state.
   */
  summary: ProvenanceValue<string>;
}

/** A single quality signal — a Tier2Artifact with a QualitySignalPayload. */
export type QualitySignal = Tier2Artifact<QualitySignalPayload>;

// ── Helper ────────────────────────────────────────────────────────────────────

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

// VIN constants from fleetHealth fixture — no literals.
const VIN_DEGRADED_DE  = FLEET_ROWS[1].vin.value;  // row-002  Germany / degraded
const VIN_DEGRADED_US  = FLEET_ROWS[3].vin.value;  // row-004  US / degraded
const VIN_NTN_DE       = FLEET_ROWS[4].vin.value;  // row-005  Germany / ntn_fallback
const VIN_UNREACHABLE  = FLEET_ROWS[5].vin.value;  // row-006  US / unreachable

// ── Evidence chips ────────────────────────────────────────────────────────────

const EVIDENCE_CONN_RELIABILITY_DE: EvidenceChip = {
  label: "Fleet Health: DE degraded count",
  detail: `VIN ${VIN_DEGRADED_DE} — persistent LTE degradation, −98 dBm`,
};

const EVIDENCE_CONN_RELIABILITY_US: EvidenceChip = {
  label: "Fleet Health: US degraded count",
  detail: `VIN ${VIN_DEGRADED_US} — RSP failure pattern, US market`,
};

const EVIDENCE_FIRMWARE_COHERENCE: EvidenceChip = {
  label: "Fault Patterns: firmware-version-drift",
  detail: "47 vehicles on firmware 4.1.2 vs baseline 4.2.0 in DE market",
};

const EVIDENCE_NTN_FALLBACK_RATE: EvidenceChip = {
  label: "Fleet Health: ntn_fallback population",
  detail: `VIN ${VIN_NTN_DE} — NTN satellite fallback active, Germany`,
};

const EVIDENCE_UNREACHABLE_RATE: EvidenceChip = {
  label: "Fleet Health: unreachable count",
  detail: `VIN ${VIN_UNREACHABLE} — APN mismatch, US roaming zone`,
};

const EVIDENCE_ROAMING_POLICY: EvidenceChip = {
  label: "Fault Patterns: roaming-policy-conflict",
  detail: "4 unreachable vehicles in US with APN mismatch pattern",
};

// ── Quality signal artifacts — newest first (descending computed_at) ──────────

/**
 * Signal 1 — Connectivity Reliability across all markets.
 *
 * Aggregate score based on the ratio of non-degraded / non-unreachable vehicles.
 * No per-vehicle identity in the payload.
 */
const SIGNAL_CONNECTIVITY_RELIABILITY: QualitySignal = {
  computed_at: "2026-09-03T06:00:00Z",
  confidence: 0.91,
  evidence: [EVIDENCE_CONN_RELIABILITY_DE, EVIDENCE_CONN_RELIABILITY_US],
  agent_version: "quality-analysis-agent@1.3.0",
  inputs_hash:
    "sha256:d4e5f6a7b8c9000000000000000000000000000000000000000000000000000001",
  payload: {
    signalName: sim("Connectivity Reliability"),
    populationScore: sim(82),
    trend: sim("stable"),
    vehicleCount: sim(4924),
    scope: sim("All markets"),
    summary: sim(
      "82% of the fleet maintained stable connectivity over the past 7 days. " +
      "97 vehicles in degraded or unreachable state — primary clusters in DE and US markets."
    ),
  },
};

/**
 * Signal 2 — Firmware Coherence across the connected population.
 *
 * Measures the fraction of vehicles on the current approved firmware baseline.
 */
const SIGNAL_FIRMWARE_COHERENCE: QualitySignal = {
  computed_at: "2026-09-03T06:05:00Z",
  confidence: 0.87,
  evidence: [EVIDENCE_FIRMWARE_COHERENCE],
  agent_version: "quality-analysis-agent@1.3.0",
  inputs_hash:
    "sha256:e5f6a7b8c9d0000000000000000000000000000000000000000000000000000002",
  payload: {
    signalName: sim("Firmware Coherence"),
    populationScore: sim(74),
    trend: sim("degrading"),
    vehicleCount: sim(4924),
    scope: sim("DE"),
    summary: sim(
      "74% of the DE fleet is on the approved firmware baseline (4.2.0). " +
      "47 vehicles on firmware 4.1.2 — fleet update recommended."
    ),
  },
};

/**
 * Signal 3 — NTN Fallback Rate across eligible TCU-3 vehicles.
 *
 * Higher fallback rate indicates terrestrial coverage gaps.
 */
const SIGNAL_NTN_FALLBACK_RATE: QualitySignal = {
  computed_at: "2026-09-03T06:10:00Z",
  confidence: 0.79,
  evidence: [EVIDENCE_NTN_FALLBACK_RATE],
  agent_version: "quality-analysis-agent@1.3.0",
  inputs_hash:
    "sha256:f6a7b8c9d0e1000000000000000000000000000000000000000000000000000003",
  payload: {
    signalName: sim("NTN Fallback Rate"),
    populationScore: sim(88),
    trend: sim("stable"),
    vehicleCount: sim(25),
    scope: sim("DE, IN"),
    summary: sim(
      "88 of 100 TCU-3 vehicles maintained terrestrial connectivity. " +
      "12% currently on NTN satellite fallback — within acceptable bounds."
    ),
  },
};

/**
 * Signal 4 — Roaming Policy Compliance across markets with roaming-enabled vehicles.
 *
 * Measures the fraction of roaming-enabled vehicles with a valid APN policy.
 */
const SIGNAL_ROAMING_COMPLIANCE: QualitySignal = {
  computed_at: "2026-09-02T06:00:00Z",
  confidence: 0.94,
  evidence: [EVIDENCE_UNREACHABLE_RATE, EVIDENCE_ROAMING_POLICY],
  agent_version: "quality-analysis-agent@1.2.0",
  inputs_hash:
    "sha256:a7b8c9d0e1f2000000000000000000000000000000000000000000000000000004",
  payload: {
    signalName: sim("Roaming Policy Compliance"),
    populationScore: sim(97),
    trend: sim("improving"),
    vehicleCount: sim(140),
    scope: sim("US"),
    summary: sim(
      "97% of US roaming-enabled vehicles have a valid APN policy. " +
      "4 vehicles with roaming-policy-conflict pattern identified for remediation."
    ),
  },
};

// ── Exported catalogue ────────────────────────────────────────────────────────

/**
 * All simulated quality signals, ordered newest-first by computed_at.
 *
 * QualitySignalsView renders these as trend cards. The computed_at and
 * confidence fields are read from the Tier2Artifact envelope and displayed
 * directly (exempt from ProvenanceField per spec D4).
 */
export const QUALITY_SIGNALS: QualitySignal[] = [
  SIGNAL_FIRMWARE_COHERENCE,
  SIGNAL_NTN_FALLBACK_RATE,
  SIGNAL_CONNECTIVITY_RELIABILITY,
  SIGNAL_ROAMING_COMPLIANCE,
];
