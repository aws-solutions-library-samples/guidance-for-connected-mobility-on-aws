// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * MarketPostureView tests — T6.4
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.4
 *
 * ## What this file covers
 *
 * 1. Settle marker present in rendered output.
 * 2. Three jurisdiction cards rendered (US, Germany, India).
 * 3. Key fields visible: data-sovereignty posture, roaming status, homologation count.
 * 4. Homologation cross-link button present and navigates to /manufacturing/homologation.
 * 5. TCU-tier distribution rendered per card.
 * 6. Fixture integrity: every leaf carries a valid provenance marker.
 */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import MarketPostureView from "../MarketPostureView";
import { MARKET_POSTURE_CARDS } from "../marketPosture.fixture";
import { VALID_PROVENANCE_MARKERS } from "../../../../types";

afterEach(() => {
  cleanup();
});

// ── Render helper ─────────────────────────────────────────────────────────────

function renderMarketPosture() {
  return render(
    <MemoryRouter initialEntries={["/compliance/market-posture"]}>
      <Routes>
        <Route
          path="/compliance/market-posture"
          element={<MarketPostureView />}
        />
        <Route
          path="/manufacturing/homologation"
          element={<div data-testid="homologation-page" />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

// ── 1. Settle marker ─────────────────────────────────────────────────────────

describe("settle marker", () => {
  it("renders the screen settleMarker in the output", () => {
    renderMarketPosture();
    const marker = screen.getByTestId("market-posture-settle-marker");
    expect(marker).toBeTruthy();
    expect(marker.textContent).toContain("cs-settle-market-posture-jurisdiction-matrix");
  });
});

// ── 2. Three jurisdiction cards ───────────────────────────────────────────────

describe("jurisdiction cards", () => {
  it("renders cards container", () => {
    renderMarketPosture();
    expect(screen.getByTestId("market-posture-cards")).toBeTruthy();
  });

  it("renders the US card", () => {
    renderMarketPosture();
    expect(screen.getByTestId("market-posture-card-us")).toBeTruthy();
  });

  it("renders the Germany card", () => {
    renderMarketPosture();
    expect(screen.getByTestId("market-posture-card-de")).toBeTruthy();
  });

  it("renders the India card", () => {
    renderMarketPosture();
    expect(screen.getByTestId("market-posture-card-in")).toBeTruthy();
  });
});

// ── 3. Key fields ─────────────────────────────────────────────────────────────

describe("key fields", () => {
  it("renders the US data-sovereignty posture text", () => {
    renderMarketPosture();
    const usCard = MARKET_POSTURE_CARDS.find((c) => c.id === "us")!;
    expect(
      screen.getByText(usCard.dataSovereigntyPosture.value!),
    ).toBeTruthy();
  });

  it("renders the Germany roaming status text", () => {
    renderMarketPosture();
    const deCard = MARKET_POSTURE_CARDS.find((c) => c.id === "de")!;
    expect(
      screen.getByText(deCard.permanentRoamingStatus.value!),
    ).toBeTruthy();
  });

  it("renders the India homologation count", () => {
    renderMarketPosture();
    const inCard = MARKET_POSTURE_CARDS.find((c) => c.id === "in")!;
    expect(
      screen.getByTestId(`mp-homologation-count-in-simulated`),
    ).toBeTruthy();
    expect(
      screen.getByTestId("mp-homologation-count-in-simulated").textContent,
    ).toContain(String(inCard.homologationApprovedCount.value));
  });
});

// ── 4. Homologation cross-link ────────────────────────────────────────────────

describe("homologation cross-link", () => {
  it("renders a 'View registry' link for each card", () => {
    renderMarketPosture();
    const links = screen.getAllByText("View registry");
    expect(links.length).toBe(3);
  });

  it("navigates to /manufacturing/homologation when the US card link is clicked", async () => {
    const user = userEvent.setup();
    renderMarketPosture();
    const usLink = screen.getByTestId("mp-homologation-link-us");
    await user.click(usLink);
    expect(screen.getByTestId("homologation-page")).toBeTruthy();
  });
});

// ── 5. TCU-tier distribution ──────────────────────────────────────────────────

describe("TCU-tier distribution", () => {
  it("renders TCU tier 1 count for the US card", () => {
    renderMarketPosture();
    const usCard = MARKET_POSTURE_CARDS.find((c) => c.id === "us")!;
    const tier1El = screen.getByTestId("mp-tcu-tier1-us-simulated");
    expect(tier1El.textContent).toContain(
      String(usCard.tcuTierDistribution.tier1.value),
    );
  });

  it("renders TCU tier 2 count for the Germany card", () => {
    renderMarketPosture();
    const deCard = MARKET_POSTURE_CARDS.find((c) => c.id === "de")!;
    const tier2El = screen.getByTestId("mp-tcu-tier2-de-simulated");
    expect(tier2El.textContent).toContain(
      String(deCard.tcuTierDistribution.tier2.value),
    );
  });
});

// ── 6. Fixture integrity ──────────────────────────────────────────────────────

describe("fixture integrity", () => {
  const EXEMPT_KEYS = new Set(["id"]);

  it("every leaf in MARKET_POSTURE_CARDS carries a valid provenance marker", () => {
    for (const card of MARKET_POSTURE_CARDS) {
      const checkObject = (obj: Record<string, unknown>, path: string): void => {
        for (const [key, value] of Object.entries(obj)) {
          if (EXEMPT_KEYS.has(key)) continue;
          if (value !== null && typeof value === "object") {
            if ("provenance" in value) {
              const pv = value as { value: unknown; provenance: string };
              expect(
                VALID_PROVENANCE_MARKERS.has(pv.provenance),
                `${path}.${key} has invalid provenance "${pv.provenance}"`,
              ).toBe(true);
            } else {
              // Nested object (e.g. tcuTierDistribution)
              checkObject(value as Record<string, unknown>, `${path}.${key}`);
            }
          }
        }
      };
      checkObject(card as unknown as Record<string, unknown>, `card[${card.id}]`);
    }
  });

  it("MARKET_POSTURE_CARDS has exactly three entries", () => {
    expect(MARKET_POSTURE_CARDS.length).toBe(3);
  });

  it("MARKET_POSTURE_CARDS ids are us, de, in", () => {
    const ids = MARKET_POSTURE_CARDS.map((c) => c.id);
    expect(ids).toEqual(["us", "de", "in"]);
  });
});
