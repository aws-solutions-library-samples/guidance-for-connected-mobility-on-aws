// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * connectivityPlans.fixture.ts — simulated data for the Connectivity Plans screen (T6.3).
 *
 * ## Scope
 *
 * This is the subscription LIFECYCLE view — active, expired, and pending-renewal
 * subscriptions per VIN, plus a renewal-rate trend strip.
 *
 * It is DISTINCT from Rate Plans (connectivity/rate-plans), which is the pricing
 * table. ConnectivityPlansView cross-links to Rate Plans for the plan pricing detail;
 * it does NOT duplicate the plan catalogue.
 *
 * ## Shape notes
 *
 * `SubscriptionRow` — one per-VIN subscription lifecycle record.
 *   vin             — vehicle identifier
 *   planName        — the subscribed plan's name (human-readable)
 *   market          — market the plan is active in
 *   status          — "active" | "expired" | "pending_renewal" | "cancelled"
 *   startDate       — when the current subscription term began
 *   renewalDate     — when the subscription is due for renewal (or expired, date of expiry)
 *   autoRenew       — whether auto-renewal is enabled
 *
 * `RenewalTrendPoint` — one monthly data point for the renewal-rate trend strip.
 *   month           — "YYYY-MM" string
 *   renewalRate     — percentage (0-100) of subscriptions that renewed vs expired
 *   renewedCount    — raw count of renewals in the month
 *   expiredCount    — raw count of expirations in the month
 *
 * Every leaf value is a ProvenanceValue with provenance: 'simulated'.
 * Structural keys (`id`) are exempt from provenance wrapping per spec D4.
 *
 * ## VIN references
 *
 * VINs come from FLEET_ROWS imported from fleetHealth.fixture.ts.
 * No VIN literals allowed (fixtureReferentialIntegrity constraint).
 */

import type { ProvenanceValue } from "../../../types";
import { FLEET_ROWS } from "../connectivity/fleetHealth.fixture";

// ── Helper ────────────────────────────────────────────────────────────────────

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

// ── Domain types ──────────────────────────────────────────────────────────────

export type SubscriptionStatus =
  | "active"
  | "expired"
  | "pending_renewal"
  | "cancelled";

export interface SubscriptionRow {
  /** Structural id — exempt from provenance wrapping per spec D4. */
  id: string;
  /**
   * VIN references FLEET_ROWS via index — no VIN literal allowed
   * (fixtureReferentialIntegrity constraint).
   */
  vin: ProvenanceValue<string>;
  planName: ProvenanceValue<string>;
  market: ProvenanceValue<string>;
  status: ProvenanceValue<SubscriptionStatus>;
  startDate: ProvenanceValue<string>;
  renewalDate: ProvenanceValue<string>;
  autoRenew: ProvenanceValue<boolean>;
}

export interface RenewalTrendPoint {
  /** Structural id — exempt. */
  id: string;
  /** "YYYY-MM" format. */
  month: ProvenanceValue<string>;
  /** 0-100, renewal rate as a percentage. */
  renewalRate: ProvenanceValue<number>;
  renewedCount: ProvenanceValue<number>;
  expiredCount: ProvenanceValue<number>;
}

export interface ConnectivityPlansFixture {
  subscriptions: SubscriptionRow[];
  renewalTrend: RenewalTrendPoint[];
}

// ── Subscription rows ─────────────────────────────────────────────────────────
//
// VINs referenced via FLEET_ROWS[n].vin — no VIN literals.

export const SUBSCRIPTION_ROWS: SubscriptionRow[] = [
  {
    id: "sub-001",
    // FLEET_ROWS[0] — row-001 (US, connected)
    vin: FLEET_ROWS[0].vin,
    planName: sim("Enhanced Connect"),
    market: sim("US"),
    status: sim("active"),
    startDate: sim("2026-07-01"),
    renewalDate: sim("2026-10-01"),
    autoRenew: sim(true),
  },
  {
    id: "sub-002",
    // FLEET_ROWS[1] — row-002 (Germany, degraded)
    vin: FLEET_ROWS[1].vin,
    planName: sim("Enhanced Connect DE"),
    market: sim("Germany"),
    status: sim("active"),
    startDate: sim("2026-06-15"),
    renewalDate: sim("2026-09-15"),
    autoRenew: sim(true),
  },
  {
    id: "sub-003",
    // FLEET_ROWS[2] — row-003 (India, connected)
    vin: FLEET_ROWS[2].vin,
    planName: sim("Standard Connect IN"),
    market: sim("India"),
    status: sim("pending_renewal"),
    startDate: sim("2026-06-01"),
    renewalDate: sim("2026-09-01"),
    autoRenew: sim(false),
  },
  {
    id: "sub-004",
    // FLEET_ROWS[3] — row-004 (US, degraded)
    vin: FLEET_ROWS[3].vin,
    planName: sim("Standard Connect"),
    market: sim("US"),
    status: sim("active"),
    startDate: sim("2026-08-01"),
    renewalDate: sim("2026-11-01"),
    autoRenew: sim(true),
  },
  {
    id: "sub-005",
    // FLEET_ROWS[4] — row-005 (Germany, ntn_fallback)
    vin: FLEET_ROWS[4].vin,
    planName: sim("NTN Fallback Bundle"),
    market: sim("Germany"),
    status: sim("active"),
    startDate: sim("2026-05-01"),
    renewalDate: sim("2026-11-01"),
    autoRenew: sim(true),
  },
  {
    id: "sub-006",
    // FLEET_ROWS[5] — row-006 (US, unreachable)
    vin: FLEET_ROWS[5].vin,
    planName: sim("Standard Connect"),
    market: sim("US"),
    status: sim("expired"),
    startDate: sim("2026-03-01"),
    renewalDate: sim("2026-06-01"),
    autoRenew: sim(false),
  },
  {
    id: "sub-007",
    // FLEET_ROWS[6] — row-007 (India, connected)
    vin: FLEET_ROWS[6].vin,
    planName: sim("Enhanced Connect IN"),
    market: sim("India"),
    status: sim("active"),
    startDate: sim("2026-07-15"),
    renewalDate: sim("2026-10-15"),
    autoRenew: sim(true),
  },
  {
    id: "sub-008",
    // FLEET_ROWS[7] — row-008 (Germany, degraded)
    vin: FLEET_ROWS[7].vin,
    planName: sim("Standard Connect DE"),
    market: sim("Germany"),
    status: sim("pending_renewal"),
    startDate: sim("2026-06-05"),
    renewalDate: sim("2026-09-05"),
    autoRenew: sim(true),
  },
  {
    id: "sub-009",
    // FLEET_ROWS[8] — row-009 (US, connected)
    vin: FLEET_ROWS[8].vin,
    planName: sim("NTN Fallback Bundle"),
    market: sim("US"),
    status: sim("active"),
    startDate: sim("2026-08-10"),
    renewalDate: sim("2027-02-10"),
    autoRenew: sim(true),
  },
  {
    id: "sub-010",
    // FLEET_ROWS[9] — row-010 (India, ntn_fallback)
    vin: FLEET_ROWS[9].vin,
    planName: sim("Standard Connect IN"),
    market: sim("India"),
    status: sim("cancelled"),
    startDate: sim("2026-04-01"),
    renewalDate: sim("2026-07-01"),
    autoRenew: sim(false),
  },
  {
    id: "sub-011",
    // FLEET_ROWS[10] — row-011 (US, degraded)
    vin: FLEET_ROWS[10].vin,
    planName: sim("Enhanced Connect"),
    market: sim("US"),
    status: sim("active"),
    startDate: sim("2026-08-20"),
    renewalDate: sim("2026-11-20"),
    autoRenew: sim(true),
  },
  {
    id: "sub-012",
    // FLEET_ROWS[11] — row-012 (Germany, unreachable)
    vin: FLEET_ROWS[11].vin,
    planName: sim("Standard Connect DE"),
    market: sim("Germany"),
    status: sim("expired"),
    startDate: sim("2026-02-01"),
    renewalDate: sim("2026-05-01"),
    autoRenew: sim(false),
  },
];

// ── Renewal trend points ──────────────────────────────────────────────────────

export const RENEWAL_TREND: RenewalTrendPoint[] = [
  {
    id: "trend-2026-04",
    month: sim("2026-04"),
    renewalRate: sim(91),
    renewedCount: sim(412),
    expiredCount: sim(41),
  },
  {
    id: "trend-2026-05",
    month: sim("2026-05"),
    renewalRate: sim(88),
    renewedCount: sim(437),
    expiredCount: sim(59),
  },
  {
    id: "trend-2026-06",
    month: sim("2026-06"),
    renewalRate: sim(93),
    renewedCount: sim(521),
    expiredCount: sim(39),
  },
  {
    id: "trend-2026-07",
    month: sim("2026-07"),
    renewalRate: sim(90),
    renewedCount: sim(498),
    expiredCount: sim(55),
  },
  {
    id: "trend-2026-08",
    month: sim("2026-08"),
    renewalRate: sim(87),
    renewedCount: sim(519),
    expiredCount: sim(77),
  },
  {
    id: "trend-2026-09",
    month: sim("2026-09"),
    renewalRate: sim(92),
    renewedCount: sim(543),
    expiredCount: sim(47),
  },
];

export const CONNECTIVITY_PLANS_FIXTURE: ConnectivityPlansFixture = {
  subscriptions: SUBSCRIPTION_ROWS,
  renewalTrend: RENEWAL_TREND,
};
