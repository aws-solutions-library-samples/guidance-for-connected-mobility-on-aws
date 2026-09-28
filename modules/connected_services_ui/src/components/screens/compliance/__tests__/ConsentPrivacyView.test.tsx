// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ConsentPrivacyView tests — T6.4
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.4
 *
 * ## What this file covers
 *
 * 1. Settle marker present in rendered output.
 * 2. Consent state table renders with rows.
 * 3. Consent category badges (Granted / Revoked) render correctly.
 * 4. Revocation timestamp appears when consent is revoked.
 * 5. DSR table renders with rows.
 * 6. DSR status badges render.
 * 7. TableNoMatchState for consent table.
 * 8. Fixture integrity: every leaf carries a valid provenance marker.
 * 9. No location values in fixture — "location" category holds boolean consent only.
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import ConsentPrivacyView from "../ConsentPrivacyView";
import {
  CONSENT_PRIVACY_FIXTURE,
  CONSENT_STATE_ROWS,
  DSR_ROWS,
} from "../consentPrivacy.fixture";
import { VALID_PROVENANCE_MARKERS } from "../../../../types";

afterEach(() => {
  cleanup();
});

// ── Render helper ─────────────────────────────────────────────────────────────

function renderConsentPrivacy() {
  return render(
    <MemoryRouter initialEntries={["/compliance/consent"]}>
      <Routes>
        <Route path="/compliance/consent" element={<ConsentPrivacyView />} />
      </Routes>
    </MemoryRouter>,
  );
}

// ── 1. Settle marker ─────────────────────────────────────────────────────────

describe("settle marker", () => {
  it("renders the screen settleMarker in the output", () => {
    renderConsentPrivacy();
    const marker = screen.getByTestId("consent-privacy-settle-marker");
    expect(marker).toBeTruthy();
    expect(marker.textContent).toContain(
      "cs-settle-consent-privacy-policy-version-table",
    );
  });
});

// ── 2. Consent state table ────────────────────────────────────────────────────

describe("consent state table", () => {
  it("renders the consent state table", () => {
    renderConsentPrivacy();
    expect(screen.getByTestId("consent-state-table")).toBeTruthy();
  });

  it("renders the correct total row count in the header counter", () => {
    renderConsentPrivacy();
    const total = CONSENT_PRIVACY_FIXTURE.consentRows.length;
    const header = screen.getByTestId("consent-state-table-header");
    expect(header.textContent).toContain(`(${total})`);
  });

  it("renders subscriber IDs from the fixture", () => {
    renderConsentPrivacy();
    const firstSubscriber = CONSENT_STATE_ROWS[0].subscriberId.value!;
    // Subscriber ID appears in both consent table and DSR table rows (SUB-00A2F1 is referenced in both)
    const matches = screen.getAllByText(firstSubscriber);
    expect(matches.length).toBeGreaterThan(0);
  });
});

// ── 3. Consent category badges ────────────────────────────────────────────────

describe("consent category badges", () => {
  it("renders 'Granted' badge for collection where consented = true", () => {
    renderConsentPrivacy();
    // sub-001 has collection.consented = true
    const badge = screen.getByTestId("consent-badge-sub-001-collection");
    expect(badge.textContent).toContain("Granted");
  });

  it("renders 'Revoked' badge for marketing where consented = false", () => {
    renderConsentPrivacy();
    // sub-001 has marketing.consented = false
    const badge = screen.getByTestId("consent-badge-sub-001-marketing");
    expect(badge.textContent).toContain("Revoked");
  });

  it("renders 'Revoked' badge for location-category where consented = false", () => {
    renderConsentPrivacy();
    // sub-002 has location.consented = false — boolean consent, not a location value
    const badge = screen.getByTestId("consent-badge-sub-002-location");
    expect(badge.textContent).toContain("Revoked");
  });
});

// ── 4. Revocation timestamp ───────────────────────────────────────────────────

describe("revocation timestamp", () => {
  it("renders the revocation timestamp for a revoked consent category", () => {
    renderConsentPrivacy();
    // sub-001 marketing.revokedAt = "2026-07-14T09:30:00Z"
    const revokedAtEl = screen.getByTestId("consent-revoked-at-sub-001-marketing");
    expect(revokedAtEl.textContent).toContain("2026-07-14T09:30:00Z");
  });

  it("does not render a revocation timestamp for a granted category", () => {
    renderConsentPrivacy();
    // sub-001 collection is granted — no revocation timestamp element
    expect(
      screen.queryByTestId("consent-revoked-at-sub-001-collection"),
    ).toBeNull();
  });
});

// ── 5. DSR table ──────────────────────────────────────────────────────────────

describe("DSR table", () => {
  it("renders the DSR table", () => {
    renderConsentPrivacy();
    expect(screen.getByTestId("dsr-table")).toBeTruthy();
  });

  it("renders the correct DSR total count in the header counter", () => {
    renderConsentPrivacy();
    const total = CONSENT_PRIVACY_FIXTURE.dsrRows.length;
    const header = screen.getByTestId("dsr-table-header");
    expect(header.textContent).toContain(`(${total})`);
  });
});

// ── 6. DSR status badges ──────────────────────────────────────────────────────

describe("DSR status badges", () => {
  it("renders at least one Completed DSR status badge", () => {
    renderConsentPrivacy();
    const completedBadges = screen.getAllByText("Completed");
    expect(completedBadges.length).toBeGreaterThan(0);
  });

  it("renders at least one Pending DSR status badge", () => {
    renderConsentPrivacy();
    const pendingBadges = screen.getAllByText("Pending");
    expect(pendingBadges.length).toBeGreaterThan(0);
  });
});

// ── 7. TableNoMatchState ──────────────────────────────────────────────────────

describe("TableNoMatchState for consent table", () => {
  it("renders 'No matches' when consent text filter matches nothing", async () => {
    const user = userEvent.setup();
    renderConsentPrivacy();
    const table = screen.getByTestId("consent-state-table");
    const filterInput = within(table).getByRole("searchbox");
    await user.type(filterInput, "zzz_no_match_xyzzy");
    expect(screen.getByText("No matches")).toBeTruthy();
  });
});

// ── 8. Fixture integrity ──────────────────────────────────────────────────────

describe("fixture integrity", () => {
  const EXEMPT_KEYS = new Set(["id"]);
  const TIER2_METADATA = new Set([
    "computed_at",
    "confidence",
    "evidence",
    "agent_version",
    "inputs_hash",
  ]);

  function checkProvenanceDeep(obj: unknown, path: string): void {
    if (obj === null || typeof obj !== "object") return;
    for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
      if (EXEMPT_KEYS.has(key) || TIER2_METADATA.has(key)) continue;
      if (value !== null && typeof value === "object") {
        if ("provenance" in value) {
          const pv = value as { value: unknown; provenance: string };
          expect(
            VALID_PROVENANCE_MARKERS.has(pv.provenance),
            `${path}.${key} has invalid provenance "${pv.provenance}"`,
          ).toBe(true);
        } else {
          checkProvenanceDeep(value, `${path}.${key}`);
        }
      }
    }
  }

  it("every leaf in CONSENT_STATE_ROWS carries a valid provenance marker", () => {
    for (const row of CONSENT_STATE_ROWS) {
      checkProvenanceDeep(row, `CONSENT_STATE_ROWS[${row.id}]`);
    }
  });

  it("every leaf in DSR_ROWS carries a valid provenance marker", () => {
    for (const row of DSR_ROWS) {
      checkProvenanceDeep(row, `DSR_ROWS[${row.id}]`);
    }
  });

  it("CONSENT_STATE_ROWS is not empty", () => {
    expect(CONSENT_STATE_ROWS.length).toBeGreaterThan(0);
  });

  it("DSR_ROWS is not empty", () => {
    expect(DSR_ROWS.length).toBeGreaterThan(0);
  });
});

// ── 9. No location values ─────────────────────────────────────────────────────

/**
 * The "location" consent category is a boolean consent state, not a geographic value.
 * This suite asserts no coordinate-shaped, GPS-shaped, cell-ID-shaped, or trip-shaped
 * values appear anywhere in the consent fixture.
 */
describe("no location values in fixture", () => {
  const LOCATION_PATTERNS = [
    /\b-?\d+\.\d{4,}\b/, // decimal degrees like 51.5074 or -73.9857
    /\b(lat|lon|lng|latitude|longitude|coordinates?|gps|cell.id|trip.id)\b/i,
    /[A-Z0-9]{3,}-[A-Z0-9]{3,}-trip/i,
  ];

  function scanForLocationValues(obj: unknown, path: string): void {
    if (obj === null) return;
    if (typeof obj === "string") {
      for (const pattern of LOCATION_PATTERNS) {
        expect(
          pattern.test(obj),
          `Unexpected location-shaped value at ${path}: "${obj}" matched ${pattern.toString()}`,
        ).toBe(false);
      }
      return;
    }
    if (typeof obj === "object") {
      for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
        scanForLocationValues(value, `${path}.${key}`);
      }
    }
  }

  it("no coordinate, GPS, cell-ID, or trip value appears in CONSENT_STATE_ROWS", () => {
    for (const row of CONSENT_STATE_ROWS) {
      scanForLocationValues(row, `CONSENT_STATE_ROWS[${row.id}]`);
    }
  });

  it("no coordinate, GPS, cell-ID, or trip value appears in DSR_ROWS", () => {
    for (const row of DSR_ROWS) {
      scanForLocationValues(row, `DSR_ROWS[${row.id}]`);
    }
  });

  it("location consent category holds a boolean, not a geographic value", () => {
    for (const row of CONSENT_STATE_ROWS) {
      // The .consented field must be boolean — never a string like a coordinate
      expect(typeof row.location.consented.value).toBe("boolean");
    }
  });
});
