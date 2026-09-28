// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * faultPatterns.fixture.ts — Simulated data for the Fault Patterns screen (T6.5).
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.5
 *
 * ## Aggregate population data only
 *
 * Every row describes a fault signature across the vehicle population.
 * No trip, GPS, driver-identity, or vehicle-location field appears here.
 *
 * ## VIN vocabulary
 *
 * Affected-VIN counts are aggregate figures (ProvenanceValue<number>).
 * The representative VINs used to pre-load the Diagnosis Workbench click-through
 * come from FLEET_ROWS exported constants — no VIN literals appear in this file.
 *
 * ## Provenance
 *
 * Every leaf value is a ProvenanceValue with provenance: 'simulated'.
 * Structural ids are exempt per spec D4.
 */

import type { ProvenanceValue } from "../../../types";
import { FLEET_ROWS } from "../connectivity/fleetHealth.fixture";

// ── Domain types ──────────────────────────────────────────────────────────────

export type TrendDirection = "rising" | "stable" | "declining";

export interface FaultPatternRow {
  /** Structural id — exempt from provenance wrapping per spec D4. */
  id: string;
  /** Fault code / signature label — e.g. "firmware-version-drift". */
  faultSignature: ProvenanceValue<string>;
  /** Number of distinct VINs in the population exhibiting this fault. */
  affectedVinCount: ProvenanceValue<number>;
  /** ISO-8601 date the fault was first observed in the population. */
  firstSeen: ProvenanceValue<string>;
  /** Whether the affected count is growing, stable, or shrinking. */
  trend: ProvenanceValue<TrendDirection>;
  /**
   * Representative affected model identifier.
   * Aggregate attribute — no per-vehicle identity.
   */
  affectedModel: ProvenanceValue<string>;
  /**
   * Market code(s) where the fault is observed (e.g. "US, DE").
   * Aggregate market attribution — not a GPS or location field.
   */
  affectedMarkets: ProvenanceValue<string>;
  /**
   * VIN used to pre-load the Diagnosis Workbench click-through.
   * Sourced from FLEET_ROWS constants — no literals.
   * ProvenanceValue<string | null> because FLEET_ROWS[n].vin.value may be null.
   */
  representativeVin: ProvenanceValue<string | null>;
}

export interface FaultPatternsFixture {
  rows: FaultPatternRow[];
}

// ── Helper ────────────────────────────────────────────────────────────────────

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

// VIN constants from fleetHealth fixture — no literals.
const VIN_DEGRADED_DE = FLEET_ROWS[1].vin.value;  // row-002  Germany / degraded
const VIN_DEGRADED_US = FLEET_ROWS[3].vin.value;  // row-004  US / degraded
const VIN_NTN_DE      = FLEET_ROWS[4].vin.value;  // row-005  Germany / ntn_fallback
const VIN_UNREACHABLE = FLEET_ROWS[5].vin.value;  // row-006  US / unreachable
const VIN_DEGRADED_DE2 = FLEET_ROWS[7].vin.value; // row-008  Germany / degraded
const VIN_NTN_IN      = FLEET_ROWS[9].vin.value;  // row-010  India / ntn_fallback

// ── Fault pattern rows ────────────────────────────────────────────────────────

export const FAULT_PATTERN_ROWS: FaultPatternRow[] = [
  {
    id: "fp-001",
    faultSignature: sim("firmware-version-drift"),
    affectedVinCount: sim(47),
    firstSeen: sim("2026-08-15"),
    trend: sim("rising"),
    affectedModel: sim("Sedan / TCU-2"),
    affectedMarkets: sim("DE"),
    representativeVin: sim(VIN_DEGRADED_DE),
  },
  {
    id: "fp-002",
    faultSignature: sim("connectivity-degraded"),
    affectedVinCount: sim(12),
    firstSeen: sim("2026-08-28"),
    trend: sim("stable"),
    affectedModel: sim("Sedan / TCU-2"),
    affectedMarkets: sim("US"),
    representativeVin: sim(VIN_DEGRADED_US),
  },
  {
    id: "fp-003",
    faultSignature: sim("roaming-policy-conflict"),
    affectedVinCount: sim(4),
    firstSeen: sim("2026-09-01"),
    trend: sim("stable"),
    affectedModel: sim("Sedan / TCU-2"),
    affectedMarkets: sim("US"),
    representativeVin: sim(VIN_UNREACHABLE),
  },
  {
    id: "fp-004",
    faultSignature: sim("ntn-fallback-carrier-mismatch"),
    affectedVinCount: sim(8),
    firstSeen: sim("2026-08-20"),
    trend: sim("rising"),
    affectedModel: sim("Hatchback / TCU-3"),
    affectedMarkets: sim("DE, IN"),
    representativeVin: sim(VIN_NTN_DE),
  },
  {
    id: "fp-005",
    faultSignature: sim("ntn-fallback-threshold-exceeded"),
    affectedVinCount: sim(5),
    firstSeen: sim("2026-09-02"),
    trend: sim("rising"),
    affectedModel: sim("SUV / TCU-3"),
    affectedMarkets: sim("IN"),
    representativeVin: sim(VIN_NTN_IN),
  },
  {
    id: "fp-006",
    faultSignature: sim("rsp-provisioning-failure"),
    affectedVinCount: sim(3),
    firstSeen: sim("2026-08-30"),
    trend: sim("declining"),
    affectedModel: sim("Saloon / TCU-2"),
    affectedMarkets: sim("DE"),
    representativeVin: sim(VIN_DEGRADED_DE2),
  },
];

export const FAULT_PATTERNS_FIXTURE: FaultPatternsFixture = {
  rows: FAULT_PATTERN_ROWS,
};
