// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * commandCenter.fixture.ts — simulated data for the Command Center screen (T4.1).
 *
 * ## Pending Approvals count
 *
 * The task constraint: "source that count from the fixtures those screens use so
 * it cannot drift from what the user sees when they click through."
 *
 * Screens contributing to pending approvals:
 *   - Software campaign approval proposals (Diagnosis Workbench, T5.4 — fixture to land)
 *   - Stop-ship / stop-sale holds awaiting authorizer sign-off (StopShip, T6.2 — fixture to land)
 *
 * Those fixtures are not yet created (Groups 5-6). For this pass, the count is
 * declared here as a simulated constant. When T5.4 and T6.2 land their fixtures,
 * the CommandCenterView is updated to import and sum from them directly — this
 * comment is the intent statement that makes that future change obvious.
 *
 * The split:
 *   - OTA campaign proposals awaiting approval: 3
 *   - Stop-ship holds awaiting authorizer sign-off: 2
 *   - Total: 5
 *
 * ## Activity feed
 *
 * 6 events spanning all five main sections, newest first. Each event navigates
 * to its source screen (the path value is the navigation target; see
 * COMMAND_CENTER_ACTIVITY for drill-down targets).
 *
 * ## Population-scale headline figures
 *
 * Three markets (US, Germany, India) with connected / degraded / NTN-fallback /
 * unreachable counts. Counts are consistent with fleetHealth.fixture.ts sums:
 *   US:      2841 connected, 47 degraded, 12 ntn_fallback,  6 unreachable
 *   Germany: 1209 connected, 31 degraded,  8 ntn_fallback,  3 unreachable
 *   India:    874 connected, 19 degraded,  5 ntn_fallback,  2 unreachable
 *
 * Total degraded: 97  — this is the headline figure on the Fleet Connectivity tile.
 *
 * ## ProvenanceValue contract
 *
 * Every displayed-value field is a ProvenanceValue<T> with provenance: 'simulated'.
 * Structural identity fields (id, path) are exempt per spec D4.
 *
 * ## No location fields
 *
 * No trip / GPS / driver-identity / cell-location field appears anywhere.
 */

import type { ProvenanceValue } from "../../../types";

// ── Helpers ───────────────────────────────────────────────────────────────────

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

// ── Tile data ─────────────────────────────────────────────────────────────────

/**
 * Fleet Connectivity tile:
 *   headline — total degraded vehicles (across all markets, consistent with fleetHealth.fixture)
 *   drillDownPath — /connectivity/fleet-health?state=degraded
 */
export interface FleetConnectivityTileData {
  totalDegraded: ProvenanceValue<number>;
  totalUnreachable: ProvenanceValue<number>;
  totalNtnFallback: ProvenanceValue<number>;
  totalConnected: ProvenanceValue<number>;
}

export const FLEET_CONNECTIVITY_TILE: FleetConnectivityTileData = {
  // 47 + 31 + 19 = 97 total degraded across US, Germany, India
  totalDegraded: sim(97),
  // 6 + 3 + 2 = 11 total unreachable
  totalUnreachable: sim(11),
  // 12 + 8 + 5 = 25 total ntn_fallback
  totalNtnFallback: sim(25),
  // 2841 + 1209 + 874 = 4924 total connected
  totalConnected: sim(4924),
};

/**
 * Software Rollout tile:
 *   headline — active rollouts and their combined fleet penetration
 */
export interface SoftwareRolloutTileData {
  activeRollouts: ProvenanceValue<number>;
  vehiclesUpdating: ProvenanceValue<number>;
  pctComplete: ProvenanceValue<number>;
}

export const SOFTWARE_ROLLOUT_TILE: SoftwareRolloutTileData = {
  activeRollouts: sim(3),
  vehiclesUpdating: sim(1240),
  pctComplete: sim(62),
};

/**
 * Quality Signals tile:
 *   headline — open fault signatures and how many vehicles are affected
 */
export interface QualitySignalsTileData {
  openSignals: ProvenanceValue<number>;
  affectedVehicles: ProvenanceValue<number>;
  highConfidenceSignals: ProvenanceValue<number>;
}

export const QUALITY_SIGNALS_TILE: QualitySignalsTileData = {
  openSignals: sim(8),
  affectedVehicles: sim(312),
  highConfidenceSignals: sim(3),
};

/**
 * Security tile:
 *   headline — R155 monitoring status note (placeholder screen)
 */
export interface SecurityTileData {
  monitoringStatus: ProvenanceValue<string>;
}

export const SECURITY_TILE: SecurityTileData = {
  monitoringStatus: sim("R155 monitoring not yet active"),
};

/**
 * Manufacturing tile:
 *   headline — units pending factory registration and in-transit holds
 */
export interface ManufacturingTileData {
  pendingRegistration: ProvenanceValue<number>;
  activeHolds: ProvenanceValue<number>;
}

export const MANUFACTURING_TILE: ManufacturingTileData = {
  pendingRegistration: sim(47),
  activeHolds: sim(2),
};

/**
 * Pending Approvals tile:
 *
 * Sources: OTA campaign proposals (T5.4 Diagnosis Workbench approval step) +
 *          stop-ship holds awaiting authorizer sign-off (T6.2 StopShip).
 *
 * When T5.4 and T6.2 land their own fixtures, replace these constants with
 * imports and derive the count from those arrays directly.
 *
 * OTA proposals awaiting approval: 3
 * Stop-ship holds awaiting authorizer: 2
 * Total: 5
 */
export interface PendingApprovalsTileData {
  /** OTA campaign proposals in the Diagnosis Workbench approval step. */
  otaProposals: ProvenanceValue<number>;
  /** Stop-ship holds awaiting authorizer sign-off. */
  stopShipHolds: ProvenanceValue<number>;
  /** Derived total — sum of the two above fields' values. */
  total: ProvenanceValue<number>;
}

export const PENDING_APPROVALS_TILE: PendingApprovalsTileData = {
  otaProposals: sim(3),
  stopShipHolds: sim(2),
  total: sim(5),
};

// ── Activity feed ─────────────────────────────────────────────────────────────

/**
 * One cross-domain event shown in the Command Center activity feed.
 *
 * `targetPath` is the navigation target (a registry path — query strings are
 * allowed but stripped by the deadLinks guard before matching, per L2 contract).
 * It is wrapped as a ProvenanceValue<string> to satisfy the provenanceFixtures
 * guard (spec D4 — every non-structural leaf must be a ProvenanceValue).
 *
 * In the component, `item.targetPath.value` is called inside a non-JSX
 * callback only (not inside JSX interpolation), so the provenanceRender guard
 * does not flag it. The deadLinks L2 scan captures only literal call-site
 * strings, not dynamic `.value` accesses, so the paths are validated by the
 * CommandCenterView unit tests instead.
 */
export interface ActivityFeedEvent {
  /** Structural identity key — exempt from provenance wrapping (spec D4). */
  id: string;
  /** ISO-8601 timestamp. */
  timestamp: ProvenanceValue<string>;
  /** Short description of the event. */
  description: ProvenanceValue<string>;
  /** Domain section label (e.g. "Connectivity", "Software"). */
  domain: ProvenanceValue<string>;
  /**
   * Navigation target for the click-through. A registry path.
   * ProvenanceValue<string> per fixture-guard requirements (spec D4).
   * The component accesses `.value` in a non-JSX click handler only.
   */
  targetPath: ProvenanceValue<string>;
}

export const COMMAND_CENTER_ACTIVITY: ActivityFeedEvent[] = [
  {
    id: "evt-001",
    timestamp: sim("2026-09-04T13:47:00Z"),
    description: sim("97 vehicles in degraded connectivity state — US, Germany, India markets"),
    domain: sim("Connectivity"),
    targetPath: sim("/connectivity/fleet-health?state=degraded"),
  },
  {
    id: "evt-002",
    timestamp: sim("2026-09-04T12:30:00Z"),
    description: sim("Software campaign SC-2026-047 reached 62 % fleet penetration"),
    domain: sim("Software"),
    targetPath: sim("/software/rollout"),
  },
  {
    id: "evt-003",
    timestamp: sim("2026-09-04T11:15:00Z"),
    description: sim("3 OTA campaign proposals awaiting approval in Diagnosis Workbench"),
    domain: sim("Software"),
    targetPath: sim("/software/workbench"),
  },
  {
    id: "evt-004",
    timestamp: sim("2026-09-04T10:02:00Z"),
    description: sim("New fault signature detected — 312 vehicles potentially affected"),
    domain: sim("Population Diagnostics"),
    targetPath: sim("/diagnostics/quality-signals"),
  },
  {
    id: "evt-005",
    timestamp: sim("2026-09-04T09:20:00Z"),
    description: sim("Stop-ship hold placed on 2 production batches pending homologation"),
    domain: sim("Manufacturing"),
    targetPath: sim("/manufacturing/stop-ship"),
  },
  {
    id: "evt-006",
    timestamp: sim("2026-09-04T08:44:00Z"),
    description: sim("47 vehicles pending factory registration across all markets"),
    domain: sim("Manufacturing"),
    targetPath: sim("/manufacturing/factory-registration"),
  },
];
