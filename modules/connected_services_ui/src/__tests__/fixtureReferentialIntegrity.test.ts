// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * fixtureReferentialIntegrity.test.ts
 *
 * Guards against the two-vocabulary split that caused Hop 3-6 failures in the
 * click-path walk:
 *
 *   Fleet Health used realistic 17-char VINs (WBA3A5C51CF256985, …).
 *   Subscriber Lookup keyed its detail catalogue off VIN-DEMO-001/002/003.
 *   Diagnosis Workbench also used VIN-DEMO-*.
 *
 * The router navigates from Fleet Health → Subscriber Lookup via the VIN in the
 * Fleet Health row. If Subscriber Lookup does not have a detail for that VIN the
 * screen renders cs-subscriber-not-found instead of the detail cards, and Hop 3
 * cannot find the root-cause panel.
 *
 * This guard is the real deliverable — Groups 5 and 6 add more cross-screen
 * links and this test will catch any future reintroduction of the split.
 *
 * ## What is asserted
 *
 * Contract 1 — Fleet Health → Subscriber Lookup navigability:
 *   Every VIN that can be navigated to from Fleet Health (i.e., every row whose
 *   vin.value is non-null) MUST resolve to a SubscriberDetail entry in
 *   SIMULATED_SUBSCRIBER_DETAILS. If it does not, a click on that row lands on
 *   cs-subscriber-not-found and any downstream hop fails silently.
 *
 * Contract 2 — Root-cause panel reachability:
 *   Every Fleet Health row whose connectivityState is 'degraded' or 'unreachable'
 *   MUST have a corresponding SubscriberDetail entry that carries a rootCause
 *   artifact. The root-cause panel in SubscriberLookupView is only shown when the
 *   state is degraded/unreachable AND rootCause is present. If it is absent the
 *   click-path's Hop 3 cannot find cs-subscriber-root-cause-panel.
 *
 * Contract 3 — Subscriber Lookup → Diagnosis Workbench navigability:
 *   Every VIN keyed in SIMULATED_SUBSCRIBER_DETAILS MUST also appear in
 *   VIN_SOFTWARE_STATES. The "View software history for this VIN" button navigates
 *   to /software/workbench/:vin; if the workbench has no record for that VIN it
 *   may render an empty or error state instead of its settle marker.
 *
 * Anti-vacuity companion:
 *   All three sets (Fleet Health navigable VINs, Subscriber Lookup detail VINs,
 *   Workbench VINs) must be non-empty. An empty set passes all loops trivially.
 *   Also asserts that at least one Fleet Health row is degraded/unreachable so
 *   the root-cause path is exercised.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/spec.md § Goal 3
 * Tasks: T2.3 follow-on guard
 */

import { describe, expect, it } from "vitest";
import { FLEET_ROWS } from "../components/screens/connectivity/fleetHealth.fixture";
import {
  SIMULATED_SUBSCRIBER_DETAILS,
} from "../components/screens/connectivity/subscriberLookup.fixture";
import { VIN_SOFTWARE_STATES } from "../components/screens/software/diagnosisWorkbench.fixture";

// ── Pre-compute sets for readability ──────────────────────────────────────────

/** VINs of Fleet Health rows that the router can navigate from (non-null vin). */
const fleetNavigableVins = FLEET_ROWS
  .map((row) => row.vin.value)
  .filter((v): v is string => v !== null);

/** VINs of Fleet Health rows whose state is degraded or unreachable. */
const fleetRootCauseVins = FLEET_ROWS
  .filter(
    (row) =>
      row.connectivityState.value === "degraded" ||
      row.connectivityState.value === "unreachable"
  )
  .map((row) => row.vin.value)
  .filter((v): v is string => v !== null);

/** VINs keyed in the Subscriber Lookup detail catalogue. */
const subscriberDetailVins = Object.keys(SIMULATED_SUBSCRIBER_DETAILS);

/** VINs keyed in the Diagnosis Workbench software-state catalogue. */
const workbenchVins = Object.keys(VIN_SOFTWARE_STATES);

// ── Anti-vacuity assertions ───────────────────────────────────────────────────

describe("fixtureReferentialIntegrity — anti-vacuity", () => {
  it("Fleet Health navigable VINs set is non-empty (at least one row has a non-null VIN)", () => {
    expect(
      fleetNavigableVins.length,
      "Fleet Health must expose at least one navigable VIN row — empty set vacuously passes all referential checks"
    ).toBeGreaterThan(0);
  });

  it("Subscriber Lookup detail catalogue is non-empty", () => {
    expect(
      subscriberDetailVins.length,
      "SIMULATED_SUBSCRIBER_DETAILS must have at least one entry — empty catalogue vacuously passes all checks"
    ).toBeGreaterThan(0);
  });

  it("Diagnosis Workbench VIN catalogue is non-empty", () => {
    expect(
      workbenchVins.length,
      "VIN_SOFTWARE_STATES must have at least one entry — empty catalogue vacuously passes all checks"
    ).toBeGreaterThan(0);
  });

  it("at least one Fleet Health row is degraded or unreachable (root-cause path is exercisable)", () => {
    expect(
      fleetRootCauseVins.length,
      "Fleet Health must have at least one degraded/unreachable row so the root-cause panel and Hop 3 can be exercised"
    ).toBeGreaterThan(0);
  });
});

// ── Contract 1: Fleet Health → Subscriber Lookup navigability ─────────────────

describe("fixtureReferentialIntegrity — Contract 1: Fleet Health VINs resolve in Subscriber Lookup", () => {
  it.each(fleetNavigableVins)(
    "Fleet Health VIN '%s' resolves to a SubscriberDetail entry",
    (vin) => {
      expect(
        SIMULATED_SUBSCRIBER_DETAILS,
        `Fleet Health row VIN "${vin}" has no entry in SIMULATED_SUBSCRIBER_DETAILS. ` +
        `Clicking this row navigates to /connectivity/subscriber-lookup/${vin} ` +
        `which renders cs-subscriber-not-found, breaking any hop that follows.`
      ).toHaveProperty(vin);
    }
  );
});

// ── Contract 2: Root-cause panel reachability ─────────────────────────────────

describe("fixtureReferentialIntegrity — Contract 2: degraded/unreachable Fleet Health VINs have rootCause in Subscriber Lookup", () => {
  it.each(fleetRootCauseVins)(
    "Fleet Health degraded/unreachable VIN '%s' has a rootCause artifact in its SubscriberDetail",
    (vin) => {
      const detail = SIMULATED_SUBSCRIBER_DETAILS[vin];
      expect(
        detail,
        `Fleet Health row VIN "${vin}" is degraded/unreachable but has no entry in SIMULATED_SUBSCRIBER_DETAILS`
      ).toBeDefined();

      expect(
        detail?.rootCause,
        `SubscriberDetail for VIN "${vin}" is degraded/unreachable in Fleet Health but is missing rootCause. ` +
        `The root-cause panel (cs-subscriber-root-cause-panel) only renders when rootCause is present; ` +
        `without it Hop 3 cannot find cs-subscriber-root-cause-panel.`
      ).toBeDefined();
    }
  );
});

// ── Contract 3: Subscriber Lookup → Diagnosis Workbench navigability ──────────

describe("fixtureReferentialIntegrity — Contract 3: Subscriber Lookup VINs resolve in Diagnosis Workbench", () => {
  it.each(subscriberDetailVins)(
    "Subscriber Lookup VIN '%s' resolves to a VinSoftwareState entry in the Workbench",
    (vin) => {
      expect(
        VIN_SOFTWARE_STATES,
        `SIMULATED_SUBSCRIBER_DETAILS has an entry for VIN "${vin}" but VIN_SOFTWARE_STATES does not. ` +
        `The 'View software history for this VIN' button navigates to /software/workbench/${vin}; ` +
        `if the workbench has no record for that VIN it cannot render its settle marker.`
      ).toHaveProperty(vin);
    }
  );
});
