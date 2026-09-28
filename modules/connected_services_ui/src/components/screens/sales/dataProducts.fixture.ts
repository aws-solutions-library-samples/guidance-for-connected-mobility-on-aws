// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * dataProducts.fixture.ts — simulated data for the Data Products screen (T6.3).
 *
 * ## Scope (2026-09-04 decision)
 *
 * Scoped strictly to outbound telemetry-sharing data products — this OEM pushing
 * telemetry to a third-party partner. Inbound data purchases, bidirectional
 * exchanges, and general data-marketplace products are NOT represented here
 * (spec T6.3 / UX spec § 6.3 "scoped to third-party telemetry pushes").
 *
 * ## Shape notes
 *
 * `DataProductRow` — one outbound telemetry-sharing product.
 *   name            — product display name, e.g. "Fleet Telemetry Feed — Partner A"
 *   description     — what data is pushed and to whom (generic)
 *   partnerLabel    — generic label for the partner (MUST NOT be a real company name)
 *   active          — whether the push is currently enabled
 *   consentCompliant — whether all VINs in scope have the required consent
 *   vehicleCount    — number of VINs included in the push scope
 *   market          — market the data product covers
 *   dataCategory    — category of telemetry being shared
 *
 * CRITICAL: `partnerLabel` MUST be a generic label like "Partner A", "Partner B".
 *   No real company, OEM, or vendor names. Real names fail contentBoundary.test.ts's
 *   brand canary — the guard stores sha256 digests and reports a digest, not the name,
 *   on failure.
 *
 * Every leaf value is a ProvenanceValue with provenance: 'simulated'.
 * Structural keys (`id`) are exempt from provenance wrapping per spec D4.
 */

import type { ProvenanceValue } from "../../../types";

// ── Helper ────────────────────────────────────────────────────────────────────

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

// ── Domain types ──────────────────────────────────────────────────────────────

export type DataProductStatus = "active" | "inactive" | "suspended";
export type ConsentStatus = "compliant" | "partial" | "non_compliant";

export interface DataProductRow {
  /** Structural id — exempt from provenance wrapping per spec D4. */
  id: string;
  name: ProvenanceValue<string>;
  description: ProvenanceValue<string>;
  /**
   * Generic partner label — MUST NOT be a real company/OEM/vendor name.
   * Real names fail the brand canary in contentBoundary.test.ts.
   * Use labels like "Partner A", "Partner B", "Analytics Partner 1".
   */
  partnerLabel: ProvenanceValue<string>;
  /** Whether the outbound push is currently enabled. */
  status: ProvenanceValue<DataProductStatus>;
  /**
   * Consent-compliance indicator.
   * "compliant" — all in-scope VINs have consented to third-party data sharing.
   * "partial"   — some VINs are missing consent (partial push in effect).
   * "non_compliant" — consent is not in place; push should be suspended.
   */
  consentStatus: ProvenanceValue<ConsentStatus>;
  /** Number of VINs included in the push scope. */
  vehicleCount: ProvenanceValue<number>;
  market: ProvenanceValue<string>;
  /** Category of telemetry data being shared (generic label). */
  dataCategory: ProvenanceValue<string>;
}

export interface DataProductsFixture {
  products: DataProductRow[];
}

// ── Data product rows ─────────────────────────────────────────────────────────

export const DATA_PRODUCT_ROWS: DataProductRow[] = [
  {
    id: "dp-001",
    name: sim("Fleet Telemetry Feed — Partner A"),
    description: sim(
      "Outbound push of anonymised vehicle health and connectivity status metrics. Used by the partner for fleet-optimisation analytics.",
    ),
    partnerLabel: sim("Partner A"),
    status: sim("active"),
    consentStatus: sim("compliant"),
    vehicleCount: sim(3841),
    market: sim("US"),
    dataCategory: sim("Vehicle Health & Connectivity"),
  },
  {
    id: "dp-002",
    name: sim("Fleet Telemetry Feed — Partner B"),
    description: sim(
      "Outbound push of aggregated telematics events (ignition, trip start/end, battery state). Used for mobility-service scheduling.",
    ),
    partnerLabel: sim("Partner B"),
    status: sim("active"),
    consentStatus: sim("compliant"),
    vehicleCount: sim(2107),
    market: sim("Germany"),
    dataCategory: sim("Telematics Events"),
  },
  {
    id: "dp-003",
    name: sim("Connectivity Diagnostics Feed — Partner C"),
    description: sim(
      "Outbound push of network-quality signals (signal strength, bearer type, session duration). Used to improve regional coverage planning.",
    ),
    partnerLabel: sim("Partner C"),
    status: sim("inactive"),
    consentStatus: sim("partial"),
    vehicleCount: sim(914),
    market: sim("India"),
    dataCategory: sim("Network Quality Signals"),
  },
  {
    id: "dp-004",
    name: sim("Safety Event Feed — Analytics Partner 1"),
    description: sim(
      "Outbound push of anonymised safety-event records (AEB activations, lane-departure events). Used for ADAS performance benchmarking.",
    ),
    partnerLabel: sim("Analytics Partner 1"),
    status: sim("active"),
    consentStatus: sim("compliant"),
    vehicleCount: sim(1560),
    market: sim("US"),
    dataCategory: sim("Safety Events"),
  },
  {
    id: "dp-005",
    name: sim("EV Energy Usage Feed — Analytics Partner 2"),
    description: sim(
      "Outbound push of battery charge/discharge telemetry for fleet electrification studies. Anonymised at VIN level.",
    ),
    partnerLabel: sim("Analytics Partner 2"),
    status: sim("suspended"),
    consentStatus: sim("non_compliant"),
    vehicleCount: sim(622),
    market: sim("Germany"),
    dataCategory: sim("Energy & Charging"),
  },
  {
    id: "dp-006",
    name: sim("Market Compliance Telemetry — Regulatory Feed"),
    description: sim(
      "Outbound push of emissions and CO2 fleet-average data required for regulatory reporting. Anonymised; no individual VIN is identifiable in the output.",
    ),
    partnerLabel: sim("Regulatory Body"),
    status: sim("active"),
    consentStatus: sim("compliant"),
    vehicleCount: sim(7200),
    market: sim("US"),
    dataCategory: sim("Regulatory Compliance"),
  },
];

export const DATA_PRODUCTS_FIXTURE: DataProductsFixture = {
  products: DATA_PRODUCT_ROWS,
};
