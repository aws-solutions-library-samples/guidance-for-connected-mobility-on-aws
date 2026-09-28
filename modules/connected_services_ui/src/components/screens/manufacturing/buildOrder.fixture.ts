// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * buildOrder.fixture.ts — simulated data for the Build & Order screen (T6.1).
 *
 * Generic production-sequencing table: order ID, configuration, plant,
 * planned build date, status. No OEM-specific production data shapes
 * (spec T6.1 Constraints / resolved OQ1).
 *
 * VINs are imported from fleetHealth.fixture.ts exported constants where
 * assigned units reference a known VIN; unassigned orders use null.
 *
 * Every leaf value is a ProvenanceValue with provenance: 'simulated'.
 * No trip/GPS/location fields. No brand canaries.
 */

import type { ProvenanceValue } from "../../../types";
import { FLEET_ROWS } from "../connectivity/fleetHealth.fixture";

// ── Domain types ──────────────────────────────────────────────────────────────

export type BuildOrderStatus =
  | "scheduled"
  | "in_production"
  | "quality_hold"
  | "completed"
  | "shipped";

export interface BuildOrderRow {
  /** Structural id — exempt from provenance wrapping per spec D4. */
  id: string;
  orderId: ProvenanceValue<string>;
  configuration: ProvenanceValue<string>;
  plant: ProvenanceValue<string>;
  plannedBuildDate: ProvenanceValue<string>;
  status: ProvenanceValue<BuildOrderStatus>;
  /** VIN assigned at end-of-line; null when not yet stamped. */
  assignedVin: ProvenanceValue<string | null>;
}

export interface BuildOrderFixture {
  rows: BuildOrderRow[];
}

// ── Helper ────────────────────────────────────────────────────────────────────

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

// VIN constants from fleet-health fixture — no literals here.
const VIN_ROW_001 = FLEET_ROWS[0].vin.value; // "1HGCM82633A004352" US / connected
const VIN_ROW_002 = FLEET_ROWS[1].vin.value; // "WBA3A5C51CF256985" Germany / degraded
const VIN_ROW_003 = FLEET_ROWS[2].vin.value; // "MAKA0000000000003" India / connected

// ── Build order rows ──────────────────────────────────────────────────────────

export const BUILD_ORDER_ROWS: BuildOrderRow[] = [
  {
    id: "bo-001",
    orderId: sim("ORD-2026-00141"),
    configuration: sim("Sedan / LTE-TCU2 / US-SPEC"),
    plant: sim("Plant Alpha"),
    plannedBuildDate: sim("2026-09-10"),
    status: sim("completed"),
    assignedVin: sim(VIN_ROW_001),
  },
  {
    id: "bo-002",
    orderId: sim("ORD-2026-00142"),
    configuration: sim("Saloon / LTE-TCU2 / EU-SPEC"),
    plant: sim("Plant Beta"),
    plannedBuildDate: sim("2026-09-11"),
    status: sim("completed"),
    assignedVin: sim(VIN_ROW_002),
  },
  {
    id: "bo-003",
    orderId: sim("ORD-2026-00143"),
    configuration: sim("Hatchback / 4G-TCU1 / IN-SPEC"),
    plant: sim("Plant Gamma"),
    plannedBuildDate: sim("2026-09-12"),
    status: sim("in_production"),
    assignedVin: sim(VIN_ROW_003),
  },
  {
    id: "bo-004",
    orderId: sim("ORD-2026-00144"),
    configuration: sim("SUV / LTE-TCU2 / US-SPEC"),
    plant: sim("Plant Alpha"),
    plannedBuildDate: sim("2026-09-14"),
    status: sim("scheduled"),
    assignedVin: sim(null),
  },
  {
    id: "bo-005",
    orderId: sim("ORD-2026-00145"),
    configuration: sim("Sedan / NTN-TCU3 / EU-SPEC"),
    plant: sim("Plant Beta"),
    plannedBuildDate: sim("2026-09-14"),
    status: sim("quality_hold"),
    assignedVin: sim(null),
  },
  {
    id: "bo-006",
    orderId: sim("ORD-2026-00146"),
    configuration: sim("Sedan / LTE-TCU2 / US-SPEC"),
    plant: sim("Plant Alpha"),
    plannedBuildDate: sim("2026-09-08"),
    status: sim("shipped"),
    assignedVin: sim("1HGCM82633A004353"),
  },
  {
    id: "bo-007",
    orderId: sim("ORD-2026-00147"),
    configuration: sim("SUV / 4G-TCU1 / IN-SPEC"),
    plant: sim("Plant Gamma"),
    plannedBuildDate: sim("2026-09-15"),
    status: sim("scheduled"),
    assignedVin: sim(null),
  },
  {
    id: "bo-008",
    orderId: sim("ORD-2026-00148"),
    configuration: sim("Hatchback / LTE-TCU3 / EU-SPEC"),
    plant: sim("Plant Beta"),
    plannedBuildDate: sim("2026-09-16"),
    status: sim("scheduled"),
    assignedVin: sim(null),
  },
];

export const BUILD_ORDER_FIXTURE: BuildOrderFixture = {
  rows: BUILD_ORDER_ROWS,
};
