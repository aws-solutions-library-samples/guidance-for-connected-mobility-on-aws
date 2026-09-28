// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ratePlans.fixture.ts — simulated data for the Rate Plans screen (T5.2).
 *
 * ## Shape notes
 *
 * `RatePlanRow`     — one row in the plan catalogue table.
 *   name            — plan display name
 *   market          — market the plan is available in (US / Germany / India)
 *   dataAllowance   — human-readable allowance string ("5 GB / month")
 *   price           — price string ("$12.00 / month")
 *   billingOwner    — who receives the invoice.
 *                     Relabelled by the Self-Managed / Amazon Managed toggle;
 *                     the VALUE is the same in both modes — only the column
 *                     HEADER changes.  The two commercial models differ in
 *                     who receives the invoice — a label, not a system
 *                     (spec T5.2 Constraints).
 *   assignedVehicleCount — number of vehicles currently on this plan
 *
 * `UsageOverageRow` — one row in the usage / overage list.
 *
 * Every leaf value is a ProvenanceValue with provenance: 'simulated'.
 * Structural keys (`id`) are exempt from provenance wrapping per spec D4.
 *
 * ## VIN references
 *
 * VINs are imported from fleetHealth.fixture.ts (fixtureReferentialIntegrity
 * constraint — no VIN literals allowed).
 */

import type { ProvenanceValue } from "../../../types";
import { FLEET_ROWS } from "./fleetHealth.fixture";

// ── Helper ────────────────────────────────────────────────────────────────────

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

// ── Domain types ──────────────────────────────────────────────────────────────

export interface RatePlanRow {
  /** Structural id — exempt from provenance wrapping per spec D4. */
  id: string;
  name: ProvenanceValue<string>;
  market: ProvenanceValue<string>;
  dataAllowance: ProvenanceValue<string>;
  price: ProvenanceValue<string>;
  /**
   * Who receives the invoice.
   *
   * The value is the same regardless of the Self-Managed / Amazon Managed
   * toggle position.  Only the COLUMN HEADER is relabelled by the toggle.
   * Spec T5.2: "a label, not a system — do not model settlement."
   */
  billingOwner: ProvenanceValue<string>;
  assignedVehicleCount: ProvenanceValue<number>;
}

export interface UsageOverageRow {
  /** Structural id — exempt from provenance wrapping per spec D4. */
  id: string;
  /**
   * VIN is imported from fleetHealth.fixture.ts via FLEET_ROWS — no VIN
   * literal allowed (fixtureReferentialIntegrity constraint).
   */
  vin: ProvenanceValue<string>;
  plan: ProvenanceValue<string>;
  market: ProvenanceValue<string>;
  usedMb: ProvenanceValue<number>;
  allowanceMb: ProvenanceValue<number>;
  overageMb: ProvenanceValue<number>;
  billingCycle: ProvenanceValue<string>;
}

export interface RatePlansFixture {
  plans: RatePlanRow[];
  usageOverages: UsageOverageRow[];
}

// ── Plan catalogue rows ───────────────────────────────────────────────────────

export const RATE_PLAN_ROWS: RatePlanRow[] = [
  {
    id: "plan-001",
    name: sim("Standard Connect"),
    market: sim("US"),
    dataAllowance: sim("2 GB / month"),
    price: sim("$8.00 / month"),
    billingOwner: sim("OEM Fleet Account"),
    assignedVehicleCount: sim(1423),
  },
  {
    id: "plan-002",
    name: sim("Enhanced Connect"),
    market: sim("US"),
    dataAllowance: sim("10 GB / month"),
    price: sim("$18.00 / month"),
    billingOwner: sim("OEM Fleet Account"),
    assignedVehicleCount: sim(876),
  },
  {
    id: "plan-003",
    name: sim("NTN Fallback Bundle"),
    market: sim("US"),
    dataAllowance: sim("500 MB / month + NTN"),
    price: sim("$22.00 / month"),
    billingOwner: sim("OEM Fleet Account"),
    assignedVehicleCount: sim(94),
  },
  {
    id: "plan-004",
    name: sim("Standard Connect DE"),
    market: sim("Germany"),
    dataAllowance: sim("2 GB / month"),
    price: sim("€9.00 / month"),
    billingOwner: sim("OEM Fleet Account"),
    assignedVehicleCount: sim(621),
  },
  {
    id: "plan-005",
    name: sim("Enhanced Connect DE"),
    market: sim("Germany"),
    dataAllowance: sim("10 GB / month"),
    price: sim("€17.50 / month"),
    billingOwner: sim("OEM Fleet Account"),
    assignedVehicleCount: sim(388),
  },
  {
    id: "plan-006",
    name: sim("Standard Connect IN"),
    market: sim("India"),
    dataAllowance: sim("1 GB / month"),
    price: sim("₹350 / month"),
    billingOwner: sim("OEM Fleet Account"),
    assignedVehicleCount: sim(543),
  },
  {
    id: "plan-007",
    name: sim("Enhanced Connect IN"),
    market: sim("India"),
    dataAllowance: sim("5 GB / month"),
    price: sim("₹780 / month"),
    billingOwner: sim("OEM Fleet Account"),
    assignedVehicleCount: sim(271),
  },
];

// ── Usage / overage rows ──────────────────────────────────────────────────────
//
// VINs referenced via FLEET_ROWS[n].vin — no VIN literals (fixtureReferentialIntegrity).

export const USAGE_OVERAGE_ROWS: UsageOverageRow[] = [
  {
    id: "usage-001",
    // FLEET_ROWS[0] — row-001
    vin: FLEET_ROWS[0].vin,
    plan: sim("Enhanced Connect"),
    market: sim("US"),
    usedMb: sim(11264),
    allowanceMb: sim(10240),
    overageMb: sim(1024),
    billingCycle: sim("2026-09"),
  },
  {
    id: "usage-002",
    // FLEET_ROWS[1] — row-002
    vin: FLEET_ROWS[1].vin,
    plan: sim("Enhanced Connect DE"),
    market: sim("Germany"),
    usedMb: sim(12800),
    allowanceMb: sim(10240),
    overageMb: sim(2560),
    billingCycle: sim("2026-09"),
  },
  {
    id: "usage-003",
    // FLEET_ROWS[3] — row-004
    vin: FLEET_ROWS[3].vin,
    plan: sim("Standard Connect"),
    market: sim("US"),
    usedMb: sim(2150),
    allowanceMb: sim(2048),
    overageMb: sim(102),
    billingCycle: sim("2026-09"),
  },
  {
    id: "usage-004",
    // FLEET_ROWS[7] — row-008
    vin: FLEET_ROWS[7].vin,
    plan: sim("Enhanced Connect DE"),
    market: sim("Germany"),
    usedMb: sim(14336),
    allowanceMb: sim(10240),
    overageMb: sim(4096),
    billingCycle: sim("2026-09"),
  },
];

export const RATE_PLANS_FIXTURE: RatePlansFixture = {
  plans: RATE_PLAN_ROWS,
  usageOverages: USAGE_OVERAGE_ROWS,
};
