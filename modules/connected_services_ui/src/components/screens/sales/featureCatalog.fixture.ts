// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * featureCatalog.fixture.ts — simulated data for the Feature Catalog screen (T6.3).
 *
 * ## Shape notes
 *
 * `FeatureRow` — one sellable feature in the capability grid.
 *   name            — display name (e.g. "Heated Seats", "Performance Mode")
 *   description     — short marketing description
 *   priceMonthly    — monthly subscription price, human-readable string
 *   activeSubscribers  — current active-subscriber count
 *   currentlyEnabled — whether the feature is currently marked as sellable
 *   eligibleVinCount — how many VINs in the fleet can receive this feature
 *                      (hardware / TCU tier qualification)
 *   revenueMonthly  — monthly revenue figure derived from active subscribers and price
 *                     This is the "toggle-and-watch-the-number-move" value:
 *                     when `currentlyEnabled` flips true, revenueMonthly is shown;
 *                     when false, the UI renders $0 for the feature.
 *   tcuTierRequired — minimum TCU tier required for the feature to work
 *
 * `TcuTier` type is shared with Fleet Health — "TCU-1" | "TCU-2" | "TCU-3".
 *
 * Every leaf value is a ProvenanceValue with provenance: 'simulated'.
 * Structural keys (`id`) are exempt from provenance wrapping per spec D4.
 *
 * No real OEM / company / partner / vendor names anywhere in this fixture.
 * The feature names (Heated Seats, Performance Mode, etc.) are generic product
 * category names — not brand-specific model names. They do not resolve to any
 * brand canary digest.
 */

import type { ProvenanceValue } from "../../../types";

// ── Helper ────────────────────────────────────────────────────────────────────

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

// ── Domain types ──────────────────────────────────────────────────────────────

export type TcuTierRequired = "TCU-1" | "TCU-2" | "TCU-3";

export interface FeatureRow {
  /** Structural id — exempt from provenance wrapping per spec D4. */
  id: string;
  name: ProvenanceValue<string>;
  description: ProvenanceValue<string>;
  /** Human-readable monthly price string, e.g. "$4.99 / month". */
  priceMonthly: ProvenanceValue<string>;
  /** Count of VINs currently subscribed to this feature. */
  activeSubscribers: ProvenanceValue<number>;
  /**
   * Whether the feature is currently marked as sellable.
   *
   * This is the toggle field — flipping it shows/hides the revenueMonthly figure.
   * When false the screen renders $0 for this feature's contribution.
   */
  currentlyEnabled: ProvenanceValue<boolean>;
  /**
   * Number of VINs whose hardware / TCU tier qualifies for this feature.
   * Used to display "X eligible VINs" when the feature card is expanded.
   */
  eligibleVinCount: ProvenanceValue<number>;
  /**
   * Monthly revenue contribution from active subscribers.
   *
   * When currentlyEnabled is true this is the live/projected figure.
   * When currentlyEnabled is false the UI shows $0 (feature is disabled).
   * The test for T6.3 asserts this is the ONLY number that changes on toggle.
   */
  revenueMonthly: ProvenanceValue<number>;
  /** Minimum TCU tier required for this feature. */
  tcuTierRequired: ProvenanceValue<TcuTierRequired>;
}

export interface FeatureCatalogFixture {
  features: FeatureRow[];
}

// ── Feature rows ──────────────────────────────────────────────────────────────

export const FEATURE_ROWS: FeatureRow[] = [
  {
    id: "feat-001",
    name: sim("Heated Seats"),
    description: sim("Seat heating for driver and front passenger with three intensity levels."),
    priceMonthly: sim("$4.99 / month"),
    activeSubscribers: sim(3218),
    currentlyEnabled: sim(true),
    eligibleVinCount: sim(4580),
    revenueMonthly: sim(16057),
    tcuTierRequired: sim("TCU-1"),
  },
  {
    id: "feat-002",
    name: sim("Performance Mode"),
    description: sim("Unlocks enhanced throttle response and dynamic stability tuning."),
    priceMonthly: sim("$9.99 / month"),
    activeSubscribers: sim(1104),
    currentlyEnabled: sim(true),
    eligibleVinCount: sim(2411),
    revenueMonthly: sim(11029),
    tcuTierRequired: sim("TCU-2"),
  },
  {
    id: "feat-003",
    name: sim("Advanced Safety Tier 2"),
    description: sim("Extended ADAS package: lane-keep assist, automatic emergency braking upgrade, and intersection detection."),
    priceMonthly: sim("$12.99 / month"),
    activeSubscribers: sim(872),
    currentlyEnabled: sim(true),
    eligibleVinCount: sim(1983),
    revenueMonthly: sim(11327),
    tcuTierRequired: sim("TCU-2"),
  },
  {
    id: "feat-004",
    name: sim("Remote Start"),
    description: sim("Start the engine remotely via the owner's mobile app; includes cabin pre-conditioning."),
    priceMonthly: sim("$2.99 / month"),
    activeSubscribers: sim(5841),
    currentlyEnabled: sim(true),
    eligibleVinCount: sim(7200),
    revenueMonthly: sim(17464),
    tcuTierRequired: sim("TCU-1"),
  },
  {
    id: "feat-005",
    name: sim("Satellite Connectivity Bundle"),
    description: sim("NTN satellite fallback for continuous connectivity in low-coverage areas."),
    priceMonthly: sim("$19.99 / month"),
    activeSubscribers: sim(241),
    currentlyEnabled: sim(false),
    eligibleVinCount: sim(629),
    revenueMonthly: sim(4818),
    tcuTierRequired: sim("TCU-3"),
  },
  {
    id: "feat-006",
    name: sim("Interior Ambient Lighting Pack"),
    description: sim("Customisable multi-zone ambient lighting with 16M colour options and mood presets."),
    priceMonthly: sim("$1.99 / month"),
    activeSubscribers: sim(2034),
    currentlyEnabled: sim(true),
    eligibleVinCount: sim(3910),
    revenueMonthly: sim(4048),
    tcuTierRequired: sim("TCU-1"),
  },
  {
    id: "feat-007",
    name: sim("Extended Range Boost"),
    description: sim("Temporarily extends EV battery range cap by unlocking reserved capacity. 30-day limited offer."),
    priceMonthly: sim("$7.99 / month"),
    activeSubscribers: sim(493),
    currentlyEnabled: sim(false),
    eligibleVinCount: sim(1220),
    revenueMonthly: sim(3939),
    tcuTierRequired: sim("TCU-2"),
  },
];

export const FEATURE_CATALOG_FIXTURE: FeatureCatalogFixture = {
  features: FEATURE_ROWS,
};
