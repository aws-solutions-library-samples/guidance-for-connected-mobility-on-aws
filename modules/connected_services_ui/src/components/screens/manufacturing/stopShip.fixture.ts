// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * stopShip.fixture.ts — simulated data for the Stop-Ship / Stop-Sale screen (T6.2).
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.2
 *
 * ## Screen contents
 *
 * Quality-criterion input with a live-updating match count against two pools:
 *   - Units in production
 *   - Units in in-transit inventory
 * NOT dealer lot inventory.
 *
 * A "Place Hold" button opens a named-authorizer confirmation (Seam 7).
 * A holds log shows active and lifted holds with authorizer and timestamp.
 *
 * ## Seam 7
 *
 * This is a higher-stakes action class than a campaign proposal. Stop-Ship
 * halts units before a recall is formally issued. The confirmation requires
 * a named authorizer (person AND role), both non-empty. The confirmation
 * component is StopShipHoldConfirmation — distinctly different from
 * ApprovalStep (DiagnosisWorkbenchView) which is a Wizard step requiring only
 * a rationale textarea.
 *
 * ## VINs
 *
 * All VINs come from FLEET_ROWS exported constants. No literals.
 *
 * ## Provenance
 *
 * Every leaf value is a ProvenanceValue with provenance: 'simulated'.
 * No trip/GPS/location fields. No brand canaries.
 */

import type { ProvenanceValue } from "../../../types";
import { FLEET_ROWS } from "../connectivity/fleetHealth.fixture";

// ── Domain types ──────────────────────────────────────────────────────────────

export type HoldStatus = "active" | "lifted";

export interface HoldLogEntry {
  /** Structural id — exempt from provenance wrapping per spec D4. */
  id: string;
  criterion: ProvenanceValue<string>;
  /** Pool: 'production' or 'in_transit'. NOT 'dealer_lot'. */
  poolScope: ProvenanceValue<"production" | "in_transit" | "both">;
  matchCount: ProvenanceValue<number>;
  authorizerPerson: ProvenanceValue<string>;
  authorizerRole: ProvenanceValue<string>;
  placedAt: ProvenanceValue<string>;
  liftedAt: ProvenanceValue<string | null>;
  status: ProvenanceValue<HoldStatus>;
}

export interface StopShipPoolCount {
  /** Match count for in-production units matching the current criterion. */
  productionCount: ProvenanceValue<number>;
  /** Match count for in-transit units matching the current criterion. */
  inTransitCount: ProvenanceValue<number>;
}

export interface StopShipFixture {
  /** Example pool counts for a given criterion. */
  examplePoolCounts: StopShipPoolCount;
  /** Existing holds log (active and lifted). */
  holdsLog: HoldLogEntry[];
}

// ── Helper ────────────────────────────────────────────────────────────────────

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

// VIN references for pool sizing context — no literals.
// These are used to describe the affected VINs in hold criteria.
const VIN_006 = FLEET_ROWS[5].vin.value;  // US / unreachable
const VIN_007 = FLEET_ROWS[6].vin.value;  // India / connected
const VIN_008 = FLEET_ROWS[7].vin.value;  // Germany / degraded
const VIN_009 = FLEET_ROWS[8].vin.value;  // US / connected
const VIN_010 = FLEET_ROWS[9].vin.value;  // India / ntn_fallback

// Suppress unused-variable lint: VIN references document which fleet rows
// the hold records correspond to. They are not rendered directly.
void VIN_006;
void VIN_007;
void VIN_008;
void VIN_009;
void VIN_010;

// ── Example pool counts ───────────────────────────────────────────────────────

export const STOP_SHIP_EXAMPLE_POOL_COUNTS: StopShipPoolCount = {
  productionCount: sim(14),
  inTransitCount: sim(7),
};

// ── Holds log ─────────────────────────────────────────────────────────────────

export const STOP_SHIP_HOLDS_LOG: HoldLogEntry[] = [
  {
    id: "hold-001",
    criterion: sim("TCU firmware rev < 3.1.4"),
    poolScope: sim("both"),
    matchCount: sim(21),
    authorizerPerson: sim("J. Rivera"),
    authorizerRole: sim("Quality Director"),
    placedAt: sim("2026-09-01T08:30:00Z"),
    liftedAt: sim(null),
    status: sim("active"),
  },
  {
    id: "hold-002",
    criterion: sim("Battery management module version 2.0.0"),
    poolScope: sim("production"),
    matchCount: sim(8),
    authorizerPerson: sim("S. Park"),
    authorizerRole: sim("Engineering Lead"),
    placedAt: sim("2026-08-28T11:00:00Z"),
    liftedAt: sim("2026-09-02T14:00:00Z"),
    status: sim("lifted"),
  },
  {
    id: "hold-003",
    criterion: sim("NTN-TCU3 units — antenna calibration check required"),
    poolScope: sim("in_transit"),
    matchCount: sim(3),
    authorizerPerson: sim("M. Okafor"),
    authorizerRole: sim("Compliance Officer"),
    placedAt: sim("2026-09-03T09:45:00Z"),
    liftedAt: sim(null),
    status: sim("active"),
  },
];

export const STOP_SHIP_FIXTURE: StopShipFixture = {
  examplePoolCounts: STOP_SHIP_EXAMPLE_POOL_COUNTS,
  holdsLog: STOP_SHIP_HOLDS_LOG,
};
