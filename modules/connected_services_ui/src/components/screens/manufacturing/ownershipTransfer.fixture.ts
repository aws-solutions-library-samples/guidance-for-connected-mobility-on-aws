// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ownershipTransfer.fixture.ts — simulated data for the Ownership Transfer screen (T6.2).
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.2
 *
 * ## Screen contents
 *
 * VIN lookup plus a transfer form: old owner, new owner, subscription-carryover
 * radio choice (transfer / lapse / require_re_subscription). The selected
 * effect is stated in plain language in the fixture.
 *
 * ## Pool scope
 *
 * Transfer pool covers units in production and units in in-transit inventory.
 * NOT dealer lot inventory.
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

export type TransferStatus = "pending" | "approved" | "completed" | "rejected";

export type SubscriptionCarryoverChoice =
  | "transfer"
  | "lapse"
  | "require_re_subscription";

export interface OwnershipTransferRow {
  /** Structural id — exempt from provenance wrapping per spec D4. */
  id: string;
  vin: ProvenanceValue<string>;
  /** Pool: 'production' or 'in_transit'. NOT 'dealer_lot'. */
  inventoryPool: ProvenanceValue<"production" | "in_transit">;
  previousOwner: ProvenanceValue<string>;
  newOwner: ProvenanceValue<string>;
  subscriptionCarryover: ProvenanceValue<SubscriptionCarryoverChoice>;
  requestedAt: ProvenanceValue<string>;
  status: ProvenanceValue<TransferStatus>;
}

export interface OwnershipTransferFixture {
  rows: OwnershipTransferRow[];
}

// ── Helper ────────────────────────────────────────────────────────────────────

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

// VIN constants from fleet-health fixture — no literals here.
// Non-null assertion is safe: the fleet-health fixture always populates VINs.
const VIN_001 = FLEET_ROWS[0].vin.value!; // US / connected
const VIN_002 = FLEET_ROWS[1].vin.value!; // Germany / degraded
const VIN_003 = FLEET_ROWS[2].vin.value!; // India / connected
const VIN_004 = FLEET_ROWS[3].vin.value!; // US / degraded
const VIN_005 = FLEET_ROWS[4].vin.value!; // Germany / ntn_fallback

// ── Transfer rows ─────────────────────────────────────────────────────────────

export const OWNERSHIP_TRANSFER_ROWS: OwnershipTransferRow[] = [
  {
    id: "tr-001",
    vin: sim(VIN_001),
    inventoryPool: sim("in_transit"),
    previousOwner: sim("Owner A"),
    newOwner: sim("Owner B"),
    subscriptionCarryover: sim("transfer"),
    requestedAt: sim("2026-09-01T10:00:00Z"),
    status: sim("approved"),
  },
  {
    id: "tr-002",
    vin: sim(VIN_002),
    inventoryPool: sim("production"),
    previousOwner: sim("Owner C"),
    newOwner: sim("Owner D"),
    subscriptionCarryover: sim("lapse"),
    requestedAt: sim("2026-09-02T14:30:00Z"),
    status: sim("pending"),
  },
  {
    id: "tr-003",
    vin: sim(VIN_003),
    inventoryPool: sim("in_transit"),
    previousOwner: sim("Owner E"),
    newOwner: sim("Owner F"),
    subscriptionCarryover: sim("require_re_subscription"),
    requestedAt: sim("2026-09-02T09:15:00Z"),
    status: sim("completed"),
  },
  {
    id: "tr-004",
    vin: sim(VIN_004),
    inventoryPool: sim("production"),
    previousOwner: sim("Owner G"),
    newOwner: sim("Owner H"),
    subscriptionCarryover: sim("transfer"),
    requestedAt: sim("2026-09-03T11:45:00Z"),
    status: sim("rejected"),
  },
  {
    id: "tr-005",
    vin: sim(VIN_005),
    inventoryPool: sim("in_transit"),
    previousOwner: sim("Owner I"),
    newOwner: sim("Owner J"),
    subscriptionCarryover: sim("lapse"),
    requestedAt: sim("2026-09-03T16:00:00Z"),
    status: sim("pending"),
  },
];

export const OWNERSHIP_TRANSFER_FIXTURE: OwnershipTransferFixture = {
  rows: OWNERSHIP_TRANSFER_ROWS,
};
