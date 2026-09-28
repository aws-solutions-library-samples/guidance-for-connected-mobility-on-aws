// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * homologation.fixture.ts — simulated data for the Homologation screen (T6.1).
 *
 * Per-market, per-model-variant type-approval status table.
 * Columns: model, market, type-approval status, RXSWIN reference.
 *
 * RXSWIN is a plain provenance: 'simulated' field (spec T5.4 Constraints:
 * "RXSWIN is a plain provenance: 'simulated' field, never inside an evidence[]
 * block").
 *
 * Every leaf value is a ProvenanceValue with provenance: 'simulated'.
 * No trip/GPS/location fields. No brand canaries.
 */

import type { ProvenanceValue } from "../../../types";

// ── Domain types ──────────────────────────────────────────────────────────────

export type TypeApprovalStatus =
  | "approved"
  | "pending"
  | "under_review"
  | "withdrawn";

export interface HomologationRow {
  /** Structural id — exempt from provenance wrapping per spec D4. */
  id: string;
  modelVariant: ProvenanceValue<string>;
  market: ProvenanceValue<string>;
  typeApprovalStatus: ProvenanceValue<TypeApprovalStatus>;
  /** RXSWIN: plain simulated field, not inside evidence[]. */
  rxswinReference: ProvenanceValue<string>;
  /** ISO date of latest status change. */
  lastUpdated: ProvenanceValue<string>;
}

export interface HomologationFixture {
  rows: HomologationRow[];
}

// ── Helper ────────────────────────────────────────────────────────────────────

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

// ── Homologation rows ─────────────────────────────────────────────────────────

export const HOMOLOGATION_ROWS: HomologationRow[] = [
  {
    id: "hom-001",
    modelVariant: sim("Sedan-A / TCU2-LTE"),
    market: sim("US"),
    typeApprovalStatus: sim("approved"),
    rxswinReference: sim("RXSWIN-2026-US-001"),
    lastUpdated: sim("2026-08-15"),
  },
  {
    id: "hom-002",
    modelVariant: sim("Sedan-A / TCU2-LTE"),
    market: sim("Germany"),
    typeApprovalStatus: sim("approved"),
    rxswinReference: sim("RXSWIN-2026-DE-001"),
    lastUpdated: sim("2026-08-20"),
  },
  {
    id: "hom-003",
    modelVariant: sim("Sedan-A / TCU2-LTE"),
    market: sim("India"),
    typeApprovalStatus: sim("pending"),
    rxswinReference: sim("RXSWIN-2026-IN-001"),
    lastUpdated: sim("2026-09-01"),
  },
  {
    id: "hom-004",
    modelVariant: sim("SUV-B / TCU2-LTE"),
    market: sim("US"),
    typeApprovalStatus: sim("approved"),
    rxswinReference: sim("RXSWIN-2026-US-002"),
    lastUpdated: sim("2026-07-30"),
  },
  {
    id: "hom-005",
    modelVariant: sim("SUV-B / TCU2-LTE"),
    market: sim("Germany"),
    typeApprovalStatus: sim("under_review"),
    rxswinReference: sim("RXSWIN-2026-DE-002"),
    lastUpdated: sim("2026-09-02"),
  },
  {
    id: "hom-006",
    modelVariant: sim("SUV-B / TCU2-LTE"),
    market: sim("India"),
    typeApprovalStatus: sim("pending"),
    rxswinReference: sim("RXSWIN-2026-IN-002"),
    lastUpdated: sim("2026-09-03"),
  },
  {
    id: "hom-007",
    modelVariant: sim("Hatchback-C / TCU1-4G"),
    market: sim("India"),
    typeApprovalStatus: sim("approved"),
    rxswinReference: sim("RXSWIN-2026-IN-003"),
    lastUpdated: sim("2026-08-01"),
  },
  {
    id: "hom-008",
    modelVariant: sim("Hatchback-C / TCU1-4G"),
    market: sim("Germany"),
    typeApprovalStatus: sim("approved"),
    rxswinReference: sim("RXSWIN-2026-DE-003"),
    lastUpdated: sim("2026-08-05"),
  },
  {
    id: "hom-009",
    modelVariant: sim("Sedan-NTN / TCU3-NTN"),
    market: sim("Germany"),
    typeApprovalStatus: sim("withdrawn"),
    rxswinReference: sim("RXSWIN-2026-DE-004"),
    lastUpdated: sim("2026-06-10"),
  },
  {
    id: "hom-010",
    modelVariant: sim("Sedan-NTN / TCU3-NTN"),
    market: sim("India"),
    typeApprovalStatus: sim("under_review"),
    rxswinReference: sim("RXSWIN-2026-IN-004"),
    lastUpdated: sim("2026-09-03"),
  },
];

export const HOMOLOGATION_FIXTURE: HomologationFixture = {
  rows: HOMOLOGATION_ROWS,
};
