// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * softwareCampaigns.fixture.ts — Simulated data for Software Campaigns and
 * Rollout Monitor screens (T5.5).
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T5.5
 *
 * ## Naming discipline (D12)
 *
 * ALL campaign identifiers use the `SoftwareCampaign` / `software_campaign` /
 * `SOFTWARE_CAMPAIGNS_*` prefix to avoid collision with CMS's FleetWise
 * data-collection campaigns (`CAMPAIGNS_TABLE_NAME`, `campaignId`).
 *
 * ## Recall content — aggregate only
 *
 * Recall rows show aggregate completion percentage only. Per-dealer task lists
 * are NOT present in this fixture. Any file using the word "dealer" for recall
 * context must carry a DEALER-AGGREGATE-OK annotation.
 *
 * ## Provenance
 *
 * Every displayed-value leaf is a ProvenanceValue<T> with provenance: 'simulated'.
 * Structural keys (id) and campaign type discriminators are exempt per spec D4.
 *
 * ## No location fields
 *
 * No trip/GPS/location/odometer field anywhere in this file.
 *
 * ## Seam 4 — halt threshold constant
 *
 * ROLLOUT_HALT_THRESHOLD_PCT is exported from RolloutMonitorView.tsx and
 * rendered verbatim on screen. The seam-4 guard asserts the rendered string
 * matches the constant's value.
 */

import type { ProvenanceValue } from "../../../types";
import { FLEET_ROWS } from "../connectivity/fleetHealth.fixture";

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

// ---------------------------------------------------------------------------
// Domain types — all use SoftwareCampaign prefix (D12)
// ---------------------------------------------------------------------------

export type SoftwareCampaignType = "ota_update" | "recall";
export type SoftwareCampaignStatus =
  | "draft"
  | "approved"
  | "in_progress"
  | "completed"
  | "halted";

export interface SoftwareCampaignRow {
  /** Structural id — exempt from provenance wrapping. */
  id: string;
  softwareCampaignId: ProvenanceValue<string>;
  /** 'ota_update' or 'recall' — determines how recall rows are rendered */
  softwareCampaignType: ProvenanceValue<SoftwareCampaignType>;
  targetDescription: ProvenanceValue<string>;
  status: ProvenanceValue<SoftwareCampaignStatus>;
  /** 0–100 rollout percentage */
  rolloutPct: ProvenanceValue<number>;
  /**
   * For recall rows: aggregate completion percentage across the install base.
   * Rendered as-is — never as a per-dealer breakdown.
   * See spec T5.5 Constraints: "Recall content is aggregate-only".
   */
  recallAggregateCompletionPct: ProvenanceValue<number | null>;
  createdAt: ProvenanceValue<string>;
}

export interface RolloutStage {
  /** Stage name — canary / 10pct / 50pct / 100pct */
  stageName: ProvenanceValue<string>;
  targetPct: ProvenanceValue<number>;
  actualPct: ProvenanceValue<number>;
  status: ProvenanceValue<"pending" | "in_progress" | "completed" | "halted">;
  vehicleCount: ProvenanceValue<number>;
}

export interface FaultRateDeltaPoint {
  /** ISO timestamp for the chart x-axis */
  timestamp: ProvenanceValue<string>;
  /** Fault rate delta vs. baseline (signed float, -1..+1) */
  faultRateDelta: ProvenanceValue<number>;
}

export interface RolloutMonitorRecord {
  /** Structural id — exempt. */
  id: string;
  softwareCampaignId: ProvenanceValue<string>;
  stages: RolloutStage[];
  faultRateDeltaSeries: FaultRateDeltaPoint[];
  widenRecommendation: ProvenanceValue<string>;
  haltRecommendation: ProvenanceValue<string>;
  /** Current active stage index (0-based) */
  activeStageIndex: ProvenanceValue<number>;
  /** Campaign-level fault rate delta for the widen/halt card */
  currentFaultRateDelta: ProvenanceValue<number>;
}

// ---------------------------------------------------------------------------
// VIN vocabulary from fleet health fixture (no literals)
// ---------------------------------------------------------------------------

/** OTA-update campaign targeting DE market with TCU-2/TCU-3 — from T5.4 workflow */
const SOFTWARE_CAMPAIGNS_TARGET_VINS = FLEET_ROWS
  .filter(
    (row) =>
      row.market.value === "Germany" &&
      (row.tcuTier.value === "TCU-2" || row.tcuTier.value === "TCU-3"),
  )
  .map((row) => row.vin.value ?? "")
  .filter(Boolean);

/** Total vehicles across all markets — used for recall aggregate */
const SOFTWARE_CAMPAIGNS_TOTAL_VIN_COUNT = FLEET_ROWS.length;

// Export the constants so tests can reference them without re-deriving
export { SOFTWARE_CAMPAIGNS_TARGET_VINS, SOFTWARE_CAMPAIGNS_TOTAL_VIN_COUNT };

// ---------------------------------------------------------------------------
// Software campaign rows
// ---------------------------------------------------------------------------

export const SOFTWARE_CAMPAIGN_ROWS: SoftwareCampaignRow[] = [
  // Active OTA update — TCAM firmware fix (from T5.4 workbench workflow)
  {
    id: "sc-001",
    softwareCampaignId: sim("SC-2026-09-03-TCAM-DE-001"),
    softwareCampaignType: sim("ota_update"),
    targetDescription: sim(
      `Germany market, TCU-2/TCU-3 (${String(SOFTWARE_CAMPAIGNS_TARGET_VINS.length)} vehicles)`,
    ),
    status: sim("in_progress"),
    rolloutPct: sim(35),
    recallAggregateCompletionPct: sim(null),
    createdAt: sim("2026-09-03T11:30:00Z"),
  },
  // Completed OTA update — Modem firmware patch
  {
    id: "sc-002",
    softwareCampaignId: sim("SC-2026-08-15-MODEM-ALL-001"),
    softwareCampaignType: sim("ota_update"),
    targetDescription: sim(
      `All markets, all tiers (${String(SOFTWARE_CAMPAIGNS_TOTAL_VIN_COUNT)} vehicles)`,
    ),
    status: sim("completed"),
    rolloutPct: sim(100),
    recallAggregateCompletionPct: sim(null),
    createdAt: sim("2026-08-15T08:00:00Z"),
  },
  // Recall campaign — aggregate completion only (no per-dealer data)
  // DEALER-AGGREGATE-OK: recall row shows install-base aggregate pct only; no per-dealer fields
  {
    id: "sc-003",
    softwareCampaignId: sim("RECALL-2026-08-01-OTA-CLIENT-001"),
    softwareCampaignType: sim("recall"),
    targetDescription: sim(
      `All markets — OTA client 2.5.0 vulnerability (${String(SOFTWARE_CAMPAIGNS_TOTAL_VIN_COUNT)} vehicles)`,
    ),
    status: sim("in_progress"),
    rolloutPct: sim(62),
    /**
     * Recall rows: aggregate completion percentage across the install base only.
     * This is rendered verbatim as "62% complete across the install base".
     * No per-dealer breakdown is stored or rendered anywhere.
     * Enforced by contentBoundary.test.ts (dealer-workflow suite).
     */
    recallAggregateCompletionPct: sim(62),
    createdAt: sim("2026-08-01T00:00:00Z"),
  },
  // Draft OTA update — not yet approved
  {
    id: "sc-004",
    softwareCampaignId: sim("SC-DRAFT-2026-09-04-MODEM-IN-001"),
    softwareCampaignType: sim("ota_update"),
    targetDescription: sim("India market, TCU-1 (draft — awaiting approval)"),
    status: sim("draft"),
    rolloutPct: sim(0),
    recallAggregateCompletionPct: sim(null),
    createdAt: sim("2026-09-04T06:00:00Z"),
  },
];

// ---------------------------------------------------------------------------
// Rollout monitor data for the active TCAM campaign
// ---------------------------------------------------------------------------

export const SOFTWARE_CAMPAIGNS_ROLLOUT_RECORD: RolloutMonitorRecord = {
  id: "rm-sc-001",
  softwareCampaignId: sim("SC-2026-09-03-TCAM-DE-001"),
  stages: [
    {
      stageName: sim("Canary"),
      targetPct: sim(5),
      actualPct: sim(5),
      status: sim("completed"),
      vehicleCount: sim(Math.ceil(SOFTWARE_CAMPAIGNS_TARGET_VINS.length * 0.05) || 1),
    },
    {
      stageName: sim("10%"),
      targetPct: sim(10),
      actualPct: sim(10),
      status: sim("completed"),
      vehicleCount: sim(Math.ceil(SOFTWARE_CAMPAIGNS_TARGET_VINS.length * 0.10) || 1),
    },
    {
      stageName: sim("50%"),
      targetPct: sim(50),
      actualPct: sim(35),
      status: sim("in_progress"),
      vehicleCount: sim(Math.ceil(SOFTWARE_CAMPAIGNS_TARGET_VINS.length * 0.35) || 1),
    },
    {
      stageName: sim("100%"),
      targetPct: sim(100),
      actualPct: sim(0),
      status: sim("pending"),
      vehicleCount: sim(0),
    },
  ],
  faultRateDeltaSeries: [
    { timestamp: sim("2026-09-03T12:00:00Z"), faultRateDelta: sim(0.01) },
    { timestamp: sim("2026-09-03T14:00:00Z"), faultRateDelta: sim(-0.02) },
    { timestamp: sim("2026-09-03T16:00:00Z"), faultRateDelta: sim(-0.04) },
    { timestamp: sim("2026-09-03T18:00:00Z"), faultRateDelta: sim(-0.03) },
    { timestamp: sim("2026-09-04T08:00:00Z"), faultRateDelta: sim(-0.05) },
    { timestamp: sim("2026-09-04T10:00:00Z"), faultRateDelta: sim(-0.06) },
  ],
  widenRecommendation: sim(
    "Fault rate delta is negative (improvement). Consider widening to 50% target.",
  ),
  haltRecommendation: sim(
    "Halt if fault rate delta exceeds +5% above baseline in any 2-hour window.",
  ),
  activeStageIndex: sim(2),
  currentFaultRateDelta: sim(-0.06),
};
