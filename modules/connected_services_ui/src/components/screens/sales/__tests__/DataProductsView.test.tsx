// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * DataProductsView tests — T6.3
 *
 * ## What this file covers
 *
 * 1. Settle marker present.
 * 2. Data products table renders.
 * 3. Consent & Privacy cross-link present.
 * 4. TableNoMatchState shown when text filter matches nothing.
 * 5. Fixture integrity: every leaf carries a valid provenance marker.
 * 6. Brand canary: no partnerLabel value in the fixture is a real company/OEM name.
 *    (Structural check — partner labels must be generic like "Partner A".)
 * 7. All products are outbound telemetry pushes (scope constraint).
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import DataProductsView from "../DataProductsView";
import {
  DATA_PRODUCTS_FIXTURE,
  DATA_PRODUCT_ROWS,
} from "../dataProducts.fixture";
import { VALID_PROVENANCE_MARKERS } from "../../../../types";

afterEach(() => {
  cleanup();
});

// ── Render helper ─────────────────────────────────────────────────────────────

function renderDataProducts() {
  return render(
    <MemoryRouter initialEntries={["/sales/data-products"]}>
      <Routes>
        <Route path="/sales/data-products" element={<DataProductsView />} />
        <Route
          path="/compliance/consent"
          element={<div data-testid="consent-page" />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

// ── 1. Settle marker ──────────────────────────────────────────────────────────

describe("settle marker", () => {
  it("renders the screen settleMarker", () => {
    renderDataProducts();
    const marker = screen.getByTestId("data-products-settle-marker");
    expect(marker).toBeTruthy();
    expect(marker.textContent).toContain("cs-settle-data-products-partner-feed-table");
  });
});

// ── 2. Data products table renders ────────────────────────────────────────────

describe("data products table", () => {
  it("renders with at least one data row", () => {
    renderDataProducts();
    const table = screen.getByTestId("data-products-table");
    const rows = within(table).getAllByRole("row");
    expect(rows.length).toBeGreaterThan(1);
  });
});

// ── 3. Consent & Privacy cross-link ──────────────────────────────────────────

describe("Consent & Privacy cross-link", () => {
  it("renders a link to /compliance/consent", () => {
    renderDataProducts();
    const link = screen.getByTestId("consent-privacy-cross-link");
    expect(link).toBeTruthy();
    expect(link.getAttribute("href")).toBe("/compliance/consent");
  });
});

// ── 4. TableNoMatchState ──────────────────────────────────────────────────────

describe("table no-match state", () => {
  it("shows 'No matches' when text filter matches nothing", async () => {
    const user = userEvent.setup();
    renderDataProducts();
    const table = screen.getByTestId("data-products-table");
    const filterInput = within(table).getByRole("searchbox");
    await user.type(filterInput, "zzz_no_match_xyzzy");
    expect(screen.getByText("No matches")).toBeTruthy();
  });
});

// ── 5. Fixture integrity ──────────────────────────────────────────────────────

describe("fixture integrity", () => {
  it("every leaf in DATA_PRODUCT_ROWS carries a valid provenance marker", () => {
    const exemptKeys = new Set(["id"]);
    for (const row of DATA_PRODUCT_ROWS) {
      for (const [key, value] of Object.entries(row)) {
        if (exemptKeys.has(key)) continue;
        const pv = value as { value: unknown; provenance: string };
        expect(
          VALID_PROVENANCE_MARKERS.has(pv.provenance),
          `DATA_PRODUCT_ROWS key "${key}" has invalid provenance "${pv.provenance}"`,
        ).toBe(true);
      }
    }
  });

  it("DATA_PRODUCTS_FIXTURE is not empty", () => {
    expect(DATA_PRODUCTS_FIXTURE.products.length).toBeGreaterThan(0);
  });
});

// ── 6. Generic partner labels only ────────────────────────────────────────────

/**
 * Structural check — partner labels must be generic.
 *
 * The brand canary in contentBoundary.test.ts checks sha256 digests; this test
 * checks that labels contain no corporate identifiers (no legal-entity suffixes,
 * no trademark symbols, no market-segment context that would require a specific
 * real company name). This is a lightweight structural gate — the digest-based
 * canary is the authoritative enforcement.
 *
 * Allowed: "Partner A", "Partner B", "Analytics Partner 1", "Regulatory Body"
 * Disallowed: anything that reads as a specific real company name.
 *
 * The check: partnerLabel.value must start with "Partner" (generic label prefix) OR
 * match one of the explicitly allowed generic categories: "Analytics Partner",
 * "Regulatory Body", "Research Partner", "Insurance Partner", "Mobility Partner".
 * Any other value pattern indicates a real name was used.
 */
const ALLOWED_GENERIC_PATTERNS = [
  /^Partner\s+[A-Z0-9]/,            // "Partner A", "Partner B", "Partner 1", etc.
  /^Analytics\s+Partner/,           // "Analytics Partner 1"
  /^Regulatory\s+Body/,             // "Regulatory Body"
  /^Research\s+Partner/,
  /^Insurance\s+Partner/,
  /^Mobility\s+Partner/,
  /^Logistics\s+Partner/,
  /^Telematics\s+Partner/,
];

describe("partner label constraint — generic labels only", () => {
  it("every partnerLabel is a generic label, not a real company name", () => {
    for (const row of DATA_PRODUCT_ROWS) {
      const label = row.partnerLabel.value;
      if (label === null) continue;
      const isGeneric = ALLOWED_GENERIC_PATTERNS.some((p) => p.test(label));
      expect(
        isGeneric,
        `DATA_PRODUCT_ROWS row "${row.id}" has partnerLabel "${label}" which does not match ` +
          "any generic partner label pattern. " +
          "Partner labels must be generic (e.g. 'Partner A', 'Analytics Partner 1'). " +
          "Real company names fail the brand canary in contentBoundary.test.ts.",
      ).toBe(true);
    }
  });
});

// ── 7. Outbound telemetry pushes only ─────────────────────────────────────────

describe("scope constraint — outbound telemetry only", () => {
  it("every product name contains 'Feed' (outbound push framing)", () => {
    for (const row of DATA_PRODUCT_ROWS) {
      const name = row.name.value;
      if (name === null) continue;
      expect(
        name.includes("Feed"),
        `DATA_PRODUCT_ROWS row "${row.id}" name "${name}" does not contain "Feed". ` +
          "Per UX spec § 6.3, data products are outbound telemetry feeds (pushes). " +
          "Name must include 'Feed' to reinforce the outbound-push framing.",
      ).toBe(true);
    }
  });
});
