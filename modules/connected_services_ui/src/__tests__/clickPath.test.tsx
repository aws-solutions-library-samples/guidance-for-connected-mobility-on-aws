// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Click-path walk — T2.3.
 *
 * Goal-3 of the spec: "A connectivity operations officer can diagnose a
 * degraded-connectivity vehicle end-to-end without leaving the portal."
 *
 * This test drives the real router through the six-hop journey. Each hop
 * clicks a rendered element (no navigate() calls — the test must detect
 * unwired tiles) and then asserts the destination screen's settleMarker.
 *
 * Six hops, each in its own it() block so a failure names its hop precisely:
 *
 *   Hop 1  Command Center → click the degraded-connectivity tile
 *          → Fleet Health with state filter pre-applied
 *          Assert settleMarker: cs-settle-fleet-health-market-summary
 *
 *   Hop 2  Fleet Health → click a VIN row
 *          → Subscriber Lookup detail (VIN pre-filled)
 *          Assert settleMarker: cs-settle-subscriber-lookup-search-field
 *
 *   Hop 3  Subscriber Lookup → root-cause panel visible
 *          → click "View software history for this VIN"
 *          → Diagnosis Workbench, VIN-scoped
 *          Assert settleMarker: cs-settle-diagnosis-workbench-stepper
 *
 *   Hop 4  Diagnosis Workbench → back
 *          Assert: we are back on the Subscriber Lookup screen
 *          (cs-settle-subscriber-lookup-search-field)
 *
 *   Hop 5  Subscriber Lookup → click "Deny All Services" → confirm by typing the VIN
 *          Assert settleMarker still on Subscriber Lookup (modal on same screen)
 *
 *   Hop 6  After denial → Connectivity State for that VIN shows denied
 *          (Connectivity State is surfaced inside Subscriber Lookup in v1)
 *
 * Each hop picks up where the previous left off via shared `sharedState` —
 * the render is performed once in beforeAll and each it() advances the journey.
 *
 * Constraint: every hop navigates by clicking rendered elements. A hop that
 * directly calls navigate() cannot detect a tile whose onClick was never wired,
 * which is the defect class this guard exists to catch.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/spec.md § Goal 3
 * Tasks: T2.3
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react/pure";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { getEntryByPath, SCREEN_REGISTRY } from "../screenRegistry";
import { AppRoutes } from "../App";
import { DEGRADED_VIN_0 } from "../components/screens/connectivity/subscriberLookup.fixture";

// All imports from @testing-library/react/pure to suppress the global
// afterEach(cleanup) auto-registration that @testing-library/react's index
// registers on import — that global cleanup would unmount the hop-walk's
// renderPure tree between it() blocks, breaking the shared-journey state.
import {
  render as renderPure,
  screen as screenPure,
  waitFor as waitForPure,
  cleanup as cleanupPure,
} from "@testing-library/react/pure";

// ---------------------------------------------------------------------------
// Settle-marker lookup
// ---------------------------------------------------------------------------

const markerFor = (registryPath: string): string => {
  const entry = getEntryByPath(registryPath);
  if (!entry) throw new Error(`No registry entry for path="${registryPath}"`);
  return entry.settleMarker;
};

const COMMAND_CENTER_MARKER = markerFor("/");
const FLEET_HEALTH_MARKER = markerFor("/connectivity/fleet-health");
const SUBSCRIBER_LOOKUP_MARKER = markerFor("/connectivity/subscriber-lookup");
const DIAGNOSIS_WORKBENCH_MARKER = markerFor("/software/workbench");

// ---------------------------------------------------------------------------
// Harness
//
// Mount the REAL AppRoutes + REAL AppShell inside a MemoryRouter.  Auth passes
// via getSession() tier-4 (DEMO_DEFAULT_SESSION, connected-services group) —
// no window.__SESSION__ override needed.
// ---------------------------------------------------------------------------

function renderApp(initialPath = "/"): void {
  render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AppRoutes />
    </MemoryRouter>
  );
}

// ---------------------------------------------------------------------------
// Anti-vacuity companion
// ---------------------------------------------------------------------------

describe("Anti-vacuity: real app shell mounts with content and nav", () => {
  afterEach(cleanup);

  it("rendering at '/' produces the app-main-content area and the side navigation", async () => {
    renderApp("/");
    // AppShell renders both of these testids.
    const mainContent = await screen.findByTestId("app-main-content", {}, { timeout: 5000 });
    expect(mainContent, "app-main-content must be present").toBeTruthy();
    const sideNav = await screen.findByTestId("side-navigation", {}, { timeout: 5000 });
    expect(sideNav, "side-navigation must be present").toBeTruthy();
  });

  it("registry settle markers are all non-empty strings (click-path precondition)", () => {
    for (const entry of SCREEN_REGISTRY) {
      expect(typeof entry.settleMarker).toBe("string");
      expect(entry.settleMarker.length).toBeGreaterThan(0);
    }
  });
});

// ---------------------------------------------------------------------------
// clickPath positive controls — two stable forms that survive T4.1+ completion
//
// Form (a): querying a deliberately non-existent testid must return null and
//   findByTestId must reject — proves the harness detects real absence rather
//   than silently passing on any tree.
//
// Form (b): after Hop 1, the tree shows Fleet Health's settleMarker AND
//   explicitly does NOT show the Command Center's settleMarker — proves routing
//   specificity, not just "something rendered".
//
// These controls never invert when a feature ships; they test the harness
// mechanics, not a particular feature state.
// ---------------------------------------------------------------------------

describe("clickPath positive controls — harness has real bite", () => {
  afterEach(cleanup);

  it("[positive-control-a] queryByTestId returns null for a deliberate non-existent id; findByTestId rejects", async () => {
    renderApp("/");
    await screen.findByTestId("app-main-content", {}, { timeout: 5000 });

    // queryByTestId must return null — not throw, but genuinely absent.
    const absent = screen.queryByTestId("__definitely_does_not_exist__");
    expect(
      absent,
      "[positive-control-a] queryByTestId must return null for a non-existent id — harness is not silently passing"
    ).toBeNull();

    // findByTestId must reject (throw) when the element is absent.
    await expect(
      screen.findByTestId("__definitely_does_not_exist__", {}, { timeout: 200 })
    ).rejects.toThrow();
  });

  it("[positive-control-b] after Hop 1 (Fleet Health route), tree shows Fleet Health marker and NOT Command Center marker", async () => {
    // Render directly at the Fleet Health route — bypasses tile click but
    // proves the marker assertion logic (routing specificity).
    renderApp("/connectivity/fleet-health");
    const content = await screen.findByTestId("app-main-content", {}, { timeout: 5000 });

    await waitFor(
      () => {
        expect(
          content.textContent,
          `[positive-control-b] Fleet Health marker "${FLEET_HEALTH_MARKER}" must appear in app-main-content`
        ).toContain(FLEET_HEALTH_MARKER);
      },
      { timeout: 5000 }
    );

    // The Command Center marker must NOT appear on the Fleet Health screen.
    expect(
      content.textContent,
      `[positive-control-b] Command Center marker "${COMMAND_CENTER_MARKER}" must NOT appear on the Fleet Health screen — routing specificity check`
    ).not.toContain(COMMAND_CENTER_MARKER);
  });
});

// ---------------------------------------------------------------------------
// Goal-3 click-path walk — six hops, each in its own it() block.
//
// The describe uses beforeAll to render once; each it() advances the shared
// journey state so failures name their hop precisely.
// ---------------------------------------------------------------------------

describe("Goal-3 click-path walk", () => {
  let user: ReturnType<typeof userEvent.setup>;

  beforeAll(() => {
    renderPure(
      <MemoryRouter initialEntries={["/"]}>
        <AppRoutes />
      </MemoryRouter>
    );
  });

  beforeEach(() => {
    user = userEvent.setup();
  });

  afterAll(cleanupPure);

  // -----------------------------------------------------------------------
  // Hop 1: Command Center → click degraded-connectivity tile → Fleet Health
  // -----------------------------------------------------------------------
  it("Hop 1 — Command Center: degraded-connectivity tile navigates to Fleet Health", async () => {
    const content = await screenPure.findByTestId("app-main-content", {}, { timeout: 5000 });

    // Pre-condition: Command Center settle marker visible.
    await waitForPure(
      () => {
        expect(
          content.textContent,
          `Hop 1 pre-check: Command Center marker "${COMMAND_CENTER_MARKER}" not in content`
        ).toContain(COMMAND_CENTER_MARKER);
      },
      { timeout: 5000 }
    );

    // Click the degraded-connectivity tile.
    const degradedTile = await screenPure.findByTestId(
      "cs-tile-degraded-connectivity",
      {},
      { timeout: 5000 }
    );
    await user.click(degradedTile);

    // Assert Fleet Health settle marker.
    await waitForPure(
      () => {
        expect(
          screenPure.getByTestId("app-main-content").textContent,
          `Hop 1 FAIL: Fleet Health marker "${FLEET_HEALTH_MARKER}" not found after clicking degraded-connectivity tile`
        ).toContain(FLEET_HEALTH_MARKER);
      },
      { timeout: 5000 }
    );
  });

  // -----------------------------------------------------------------------
  // Hop 2: Fleet Health → click a VIN row → Subscriber Lookup detail
  // -----------------------------------------------------------------------
  it("Hop 2 — Fleet Health: VIN row click navigates to Subscriber Lookup", async () => {
    // Pre-condition: Fleet Health must already be rendered (from Hop 1).
    await waitForPure(
      () => {
        expect(
          screenPure.getByTestId("app-main-content").textContent,
          `Hop 2 pre-check: Fleet Health marker "${FLEET_HEALTH_MARKER}" must be on screen`
        ).toContain(FLEET_HEALTH_MARKER);
      },
      { timeout: 5000 }
    );

    const vinRow = await screenPure.findByTestId("cs-fleet-health-vin-row-0", {}, { timeout: 5000 });
    await user.click(vinRow);

    await waitForPure(
      () => {
        expect(
          screenPure.getByTestId("app-main-content").textContent,
          `Hop 2 FAIL: Subscriber Lookup marker "${SUBSCRIBER_LOOKUP_MARKER}" not found after clicking VIN row`
        ).toContain(SUBSCRIBER_LOOKUP_MARKER);
      },
      { timeout: 5000 }
    );
  });

  // -----------------------------------------------------------------------
  // Hop 3: Subscriber Lookup → click "View software history" → Diagnosis Workbench
  // -----------------------------------------------------------------------
  it("Hop 3 — Subscriber Lookup: 'View software history' navigates to Diagnosis Workbench", async () => {
    // Pre-condition: Subscriber Lookup must already be rendered (from Hop 2).
    await waitForPure(
      () => {
        expect(
          screenPure.getByTestId("app-main-content").textContent,
          `Hop 3 pre-check: Subscriber Lookup marker "${SUBSCRIBER_LOOKUP_MARKER}" must be on screen`
        ).toContain(SUBSCRIBER_LOOKUP_MARKER);
      },
      { timeout: 5000 }
    );

    const rootCausePanel = await screenPure.findByTestId(
      "cs-subscriber-root-cause-panel",
      {},
      { timeout: 5000 }
    );
    expect(rootCausePanel, "Hop 3 pre-condition: root-cause panel must be visible").toBeTruthy();

    const softwareHistoryLink = await screenPure.findByTestId(
      "cs-view-software-history",
      {},
      { timeout: 5000 }
    );
    await user.click(softwareHistoryLink);

    await waitForPure(
      () => {
        expect(
          screenPure.getByTestId("app-main-content").textContent,
          `Hop 3 FAIL: Diagnosis Workbench marker "${DIAGNOSIS_WORKBENCH_MARKER}" not found after clicking software history link`
        ).toContain(DIAGNOSIS_WORKBENCH_MARKER);
      },
      { timeout: 5000 }
    );
  });

  // -----------------------------------------------------------------------
  // Hop 4: Diagnosis Workbench → back → Subscriber Lookup
  // -----------------------------------------------------------------------
  it("Hop 4 — Diagnosis Workbench: back button returns to Subscriber Lookup", async () => {
    // Pre-condition: Diagnosis Workbench must already be rendered (from Hop 3).
    await waitForPure(
      () => {
        expect(
          screenPure.getByTestId("app-main-content").textContent,
          `Hop 4 pre-check: Diagnosis Workbench marker "${DIAGNOSIS_WORKBENCH_MARKER}" must be on screen`
        ).toContain(DIAGNOSIS_WORKBENCH_MARKER);
      },
      { timeout: 5000 }
    );

    const backButton = await screenPure.findByTestId(
      "cs-workbench-back-to-subscriber",
      {},
      { timeout: 5000 }
    );
    await user.click(backButton);

    await waitForPure(
      () => {
        expect(
          screenPure.getByTestId("app-main-content").textContent,
          `Hop 4 FAIL: Subscriber Lookup marker "${SUBSCRIBER_LOOKUP_MARKER}" not found after navigating back`
        ).toContain(SUBSCRIBER_LOOKUP_MARKER);
      },
      { timeout: 5000 }
    );
  });

  // -----------------------------------------------------------------------
  // Hop 5: Subscriber Lookup → "Deny All Services" → confirm VIN → still on SL
  // -----------------------------------------------------------------------
  it("Hop 5 — Subscriber Lookup: deny-all-services confirm keeps user on Subscriber Lookup", async () => {
    // Pre-condition: Subscriber Lookup must already be rendered (from Hop 4).
    await waitForPure(
      () => {
        expect(
          screenPure.getByTestId("app-main-content").textContent,
          `Hop 5 pre-check: Subscriber Lookup marker "${SUBSCRIBER_LOOKUP_MARKER}" must be on screen`
        ).toContain(SUBSCRIBER_LOOKUP_MARKER);
      },
      { timeout: 5000 }
    );

    const denyAllButton = await screenPure.findByTestId(
      "cs-subscriber-deny-all-services",
      {},
      { timeout: 5000 }
    );
    await user.click(denyAllButton);

    const vinConfirmInput = await screenPure.findByTestId(
      "cs-deny-confirm-vin-input",
      {},
      { timeout: 5000 }
    );
    // Cloudscape's Input renders the testid on a wrapper div; userEvent.type
    // needs the actual <input> element to fire the onChange handler.
    const innerVinInput = vinConfirmInput.querySelector("input") ?? vinConfirmInput;
    // Type the actual VIN from the fixture (derived, not hardcoded).
    await user.type(innerVinInput, DEGRADED_VIN_0);

    const confirmDenyButton = await screenPure.findByTestId(
      "cs-deny-confirm-submit",
      {},
      { timeout: 5000 }
    );
    await user.click(confirmDenyButton);

    // Modal is on the same screen — Subscriber Lookup marker must remain.
    await waitForPure(
      () => {
        expect(
          screenPure.getByTestId("app-main-content").textContent,
          `Hop 5 FAIL: Subscriber Lookup marker "${SUBSCRIBER_LOOKUP_MARKER}" not found after denial confirmation`
        ).toContain(SUBSCRIBER_LOOKUP_MARKER);
      },
      { timeout: 5000 }
    );
  });

  // -----------------------------------------------------------------------
  // Hop 6: After denial — Connectivity State for that VIN shows denied
  //
  // Testid derived from DEGRADED_VIN_0 (from the fixture) so it stays correct
  // if the fixture VIN ever changes. The component renders
  // data-testid={`cs-connectivity-state-denied-${detail.id}`} where detail.id
  // equals the VIN that was navigated to from Fleet Health row-0.
  // -----------------------------------------------------------------------
  it(`Hop 6 — after denial: Connectivity State shows denied indicator for ${DEGRADED_VIN_0}`, async () => {
    // Pre-condition: still on Subscriber Lookup (from Hop 5).
    await waitForPure(
      () => {
        expect(
          screenPure.getByTestId("app-main-content").textContent,
          `Hop 6 pre-check: Subscriber Lookup marker "${SUBSCRIBER_LOOKUP_MARKER}" must be on screen`
        ).toContain(SUBSCRIBER_LOOKUP_MARKER);
      },
      { timeout: 5000 }
    );

    const deniedTestId = `cs-connectivity-state-denied-${DEGRADED_VIN_0}`;
    // Wait for the denied indicator — the state update is async after the confirm click.
    await waitForPure(
      () => {
        const el = screenPure.queryByTestId(deniedTestId);
        expect(
          el,
          `Hop 6 FAIL: Connectivity denied indicator "${deniedTestId}" must be visible after denial`
        ).toBeTruthy();
      },
      { timeout: 5000 }
    );
  });
});
