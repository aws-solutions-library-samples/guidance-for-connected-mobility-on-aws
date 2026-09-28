// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * factoryRegistration.fixture.ts — simulated data for the Factory Registration
 * screen (T6.1).
 *
 * Models the "register new vehicle" form flow plus a recent-registrations log.
 * In the simulated flow, ICCID / IMSI / profile are generated at registration.
 *
 * VINs are imported from fleetHealth.fixture.ts exported constants.
 * Every leaf value is a ProvenanceValue with provenance: 'simulated'.
 * No trip/GPS/location fields. No brand canaries.
 */

import type { ProvenanceValue } from "../../../types";
import { FLEET_ROWS } from "../connectivity/fleetHealth.fixture";

// ── Domain types ──────────────────────────────────────────────────────────────

export type RegistrationStatus = "success" | "pending" | "failed";

export interface RegistrationLogRow {
  /** Structural id — exempt from provenance wrapping per spec D4. */
  id: string;
  vin: ProvenanceValue<string>;
  iccid: ProvenanceValue<string>;
  imsi: ProvenanceValue<string>;
  profile: ProvenanceValue<string>;
  market: ProvenanceValue<string>;
  registeredAt: ProvenanceValue<string>;
  status: ProvenanceValue<RegistrationStatus>;
}

export interface FactoryRegistrationFixture {
  recentRegistrations: RegistrationLogRow[];
}

// ── Helper ────────────────────────────────────────────────────────────────────

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

// VIN constants from fleet-health fixture — no literals.
const VIN_ROW_001 = FLEET_ROWS[0].vin.value!; // US / connected
const VIN_ROW_002 = FLEET_ROWS[1].vin.value!; // Germany / degraded
const VIN_ROW_003 = FLEET_ROWS[2].vin.value!; // India / connected
const VIN_ROW_004 = FLEET_ROWS[3].vin.value!; // US / degraded
const VIN_ROW_005 = FLEET_ROWS[4].vin.value!; // Germany / ntn_fallback

// ── Recent registrations ──────────────────────────────────────────────────────

export const REGISTRATION_LOG_ROWS: RegistrationLogRow[] = [
  {
    id: "reg-001",
    vin: sim(VIN_ROW_001),
    iccid: sim("89014103211118510720"),
    imsi: sim("310260000000001"),
    profile: sim("US-LTE-Standard"),
    market: sim("US"),
    registeredAt: sim("2026-09-03T14:30:00Z"),
    status: sim("success"),
  },
  {
    id: "reg-002",
    vin: sim(VIN_ROW_002),
    iccid: sim("89049030000001234567"),
    imsi: sim("262010000000002"),
    profile: sim("EU-LTE-Standard"),
    market: sim("Germany"),
    registeredAt: sim("2026-09-03T12:15:00Z"),
    status: sim("success"),
  },
  {
    id: "reg-003",
    vin: sim(VIN_ROW_003),
    iccid: sim("89910310000001234568"),
    imsi: sim("404100000000003"),
    profile: sim("IN-4G-Standard"),
    market: sim("India"),
    registeredAt: sim("2026-09-03T10:05:00Z"),
    status: sim("success"),
  },
  {
    id: "reg-004",
    vin: sim(VIN_ROW_004),
    iccid: sim("89014103211118510721"),
    imsi: sim("310260000000004"),
    profile: sim("US-LTE-Standard"),
    market: sim("US"),
    registeredAt: sim("2026-09-03T09:50:00Z"),
    status: sim("pending"),
  },
  {
    id: "reg-005",
    vin: sim(VIN_ROW_005),
    iccid: sim("89049030000001234568"),
    imsi: sim("262010000000005"),
    profile: sim("EU-NTN-Extended"),
    market: sim("Germany"),
    registeredAt: sim("2026-09-03T08:22:00Z"),
    status: sim("failed"),
  },
];

export const FACTORY_REGISTRATION_FIXTURE: FactoryRegistrationFixture = {
  recentRegistrations: REGISTRATION_LOG_ROWS,
};
