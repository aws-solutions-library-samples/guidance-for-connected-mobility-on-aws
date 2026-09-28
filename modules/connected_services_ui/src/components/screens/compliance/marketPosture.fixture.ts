// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * marketPosture.fixture.ts — simulated data for the Market Posture screen (T6.4).
 *
 * ## Shape
 *
 * Three jurisdiction cards — US, Germany, India — each with:
 *   - dataSovereigntyPosture: data-residency policy for that market
 *   - permanentRoamingStatus: whether permanent roaming is permitted
 *   - homologationApprovedCount: count of type-approved variants in the registry
 *   - fleetAvgEmissionsGCo2PerKm: fleet-average emissions in gCO₂/km
 *   - tcuTierDistribution: vehicle counts per TCU tier (1, 2, 3)
 *
 * ## Homologation cross-link
 *
 * The screen navigates to /manufacturing/homologation for the detail view.
 * The count comes from this fixture — no import of homologation.fixture.ts.
 *
 * ## ProvenanceValue contract
 *
 * Every displayed-value field is a ProvenanceValue<T> with provenance: 'simulated'.
 * Structural identity fields (id) are exempt per spec D4.
 *
 * ## No location fields
 *
 * No trip / GPS / driver-identity / vehicle-location field appears anywhere.
 */

import type { ProvenanceValue } from "../../../types";

// ── Helper ────────────────────────────────────────────────────────────────────

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

// ── Domain types ──────────────────────────────────────────────────────────────

export interface TcuTierDistribution {
  tier1: ProvenanceValue<number>;
  tier2: ProvenanceValue<number>;
  tier3: ProvenanceValue<number>;
}

export interface MarketPostureCard {
  /** Structural id — exempt from provenance wrapping per spec D4. */
  id: string;
  market: ProvenanceValue<string>;
  dataSovereigntyPosture: ProvenanceValue<string>;
  permanentRoamingStatus: ProvenanceValue<string>;
  homologationApprovedCount: ProvenanceValue<number>;
  fleetAvgEmissionsGCo2PerKm: ProvenanceValue<number>;
  tcuTierDistribution: TcuTierDistribution;
}

// ── Market cards ──────────────────────────────────────────────────────────────

export const MARKET_POSTURE_CARDS: MarketPostureCard[] = [
  {
    id: "us",
    market: sim("US"),
    dataSovereigntyPosture: sim("Data stored in-country; CCPA compliant"),
    permanentRoamingStatus: sim("Not permitted"),
    homologationApprovedCount: sim(14),
    fleetAvgEmissionsGCo2PerKm: sim(112),
    tcuTierDistribution: {
      tier1: sim(1240),
      tier2: sim(1680),
      tier3: sim(986),
    },
  },
  {
    id: "de",
    market: sim("Germany"),
    dataSovereigntyPosture: sim("EU-resident storage; GDPR Article 25 applied"),
    permanentRoamingStatus: sim("Restricted — 90-day cap per EU roaming regulation"),
    homologationApprovedCount: sim(9),
    fleetAvgEmissionsGCo2PerKm: sim(108),
    tcuTierDistribution: {
      tier1: sim(510),
      tier2: sim(720),
      tier3: sim(21),
    },
  },
  {
    id: "in",
    market: sim("India"),
    dataSovereigntyPosture: sim("Local data localisation; PDPA compliant"),
    permanentRoamingStatus: sim("Permitted under bilateral agreement"),
    homologationApprovedCount: sim(6),
    fleetAvgEmissionsGCo2PerKm: sim(127),
    tcuTierDistribution: {
      tier1: sim(620),
      tier2: sim(240),
      tier3: sim(40),
    },
  },
];
