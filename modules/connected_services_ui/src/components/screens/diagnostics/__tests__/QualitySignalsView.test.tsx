// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * QualitySignalsView tests — T6.5
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.5
 *
 * ## What this file covers
 *
 * 1. Settle marker present in the rendered output.
 * 2. Signal names rendered for all fixture items.
 * 3. Confidence displayed for each signal (artifact envelope field — no provenance badge).
 * 4. computed_at displayed for each signal (artifact envelope field).
 * 5. Evidence chips rendered and expandable.
 * 6. All five Tier2Artifact envelope fields present on fixture entries.
 * 7. No trip/GPS/location fields rendered (compliance control (d)).
 * 8. Fixture provenance compliance: all payload ProvenanceValue leaves are 'simulated'.
 * 9. Aggregate population data only: no individual VIN identity in payload.
 */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import QualitySignalsView from "../QualitySignalsView";
import { QUALITY_SIGNALS } from "../qualitySignals.fixture";

afterEach(() => {
  cleanup();
});

// ── Render helper ─────────────────────────────────────────────────────────────

function renderQualitySignals() {
  return render(
    <MemoryRouter initialEntries={["/diagnostics/quality-signals"]}>
      <QualitySignalsView />
    </MemoryRouter>,
  );
}

// ── Settle marker ─────────────────────────────────────────────────────────────

describe("settle marker", () => {
  it("renders the screen's settleMarker in the output", () => {
    renderQualitySignals();
    expect(screen.getByTestId("quality-signals-settle-marker")).toBeTruthy();
    expect(
      screen.getByTestId("quality-signals-settle-marker").textContent,
    ).toContain("cs-settle-quality-signals-trend-panel");
  });
});

// ── Signal names rendered ─────────────────────────────────────────────────────

describe("quality signal names rendered", () => {
  it("renders signal name from first fixture item", () => {
    renderQualitySignals();
    const firstName = QUALITY_SIGNALS[0].payload.signalName.value!;
    const matches = screen.getAllByText(firstName);
    expect(matches.length).toBeGreaterThan(0);
  });

  it("renders at least two distinct signal names", () => {
    renderQualitySignals();
    const names = QUALITY_SIGNALS.map((s) => s.payload.signalName.value!);
    const uniqueNames = [...new Set(names)];
    expect(uniqueNames.length).toBeGreaterThanOrEqual(2);
    for (const name of uniqueNames) {
      expect(screen.getAllByText(name).length).toBeGreaterThan(0);
    }
  });
});

// ── Artifact envelope fields displayed ───────────────────────────────────────

describe("Tier2Artifact envelope fields rendered", () => {
  it("renders 'Agent confidence' label", () => {
    renderQualitySignals();
    const confidenceLabels = screen.getAllByText("Agent confidence");
    expect(confidenceLabels.length).toBeGreaterThan(0);
  });

  it("renders 'Computed at' label", () => {
    renderQualitySignals();
    const computedAtLabels = screen.getAllByText("Computed at");
    expect(computedAtLabels.length).toBeGreaterThan(0);
  });

  it("renders formatted computed_at for the first signal", () => {
    renderQualitySignals();
    // First signal in fixture — computed_at: "2026-09-03T06:05:00Z"
    // Formatted: "2026-09-03 06:05 UTC"
    expect(screen.getByText("2026-09-03 06:05 UTC")).toBeTruthy();
  });
});

// ── Evidence chips ────────────────────────────────────────────────────────────

describe("evidence chips", () => {
  it("renders evidence section for the first signal", async () => {
    renderQualitySignals();
    // Find the Evidence expandable section header for the first card
    const evidenceHeaders = screen.getAllByText(/Evidence \(\d+\)/);
    expect(evidenceHeaders.length).toBeGreaterThan(0);
  });

  it("expands evidence section to show chips on click", async () => {
    renderQualitySignals();
    const evidenceHeaders = screen.getAllByText(/Evidence \(\d+\)/);
    // Click the first evidence section
    await userEvent.click(evidenceHeaders[0]);
    // At least one chip label from the fixture should become visible
    const firstSignalChip = QUALITY_SIGNALS[0].evidence[0].label;
    expect(screen.getByText(firstSignalChip)).toBeTruthy();
  });
});

// ── Tier2Artifact contract shape on fixture entries ───────────────────────────

describe("Tier2Artifact contract — all five envelope fields present", () => {
  it("every QUALITY_SIGNALS entry carries all five metadata fields", () => {
    for (const signal of QUALITY_SIGNALS) {
      // computed_at: non-empty ISO string
      expect(typeof signal.computed_at).toBe("string");
      expect(signal.computed_at.length).toBeGreaterThan(0);

      // confidence: number in 0..1
      expect(typeof signal.confidence).toBe("number");
      expect(signal.confidence).toBeGreaterThanOrEqual(0);
      expect(signal.confidence).toBeLessThanOrEqual(1);

      // evidence: array (may be empty)
      expect(Array.isArray(signal.evidence)).toBe(true);

      // agent_version: non-empty string
      expect(typeof signal.agent_version).toBe("string");
      expect(signal.agent_version.length).toBeGreaterThan(0);

      // inputs_hash: non-empty string
      expect(typeof signal.inputs_hash).toBe("string");
      expect(signal.inputs_hash.length).toBeGreaterThan(0);
    }
  });

  it("fixture has a non-empty signal set (anti-vacuity)", () => {
    expect(QUALITY_SIGNALS.length).toBeGreaterThan(0);
  });
});

// ── Compliance: no trip/GPS/location fields ───────────────────────────────────

describe("compliance: no trip/GPS/location fields", () => {
  it("does not render any GPS, trip, latitude, longitude, or location text", () => {
    const { container } = renderQualitySignals();
    const text = container.textContent?.toLowerCase() ?? "";
    expect(text).not.toMatch(/\bgps\b/);
    expect(text).not.toMatch(/\blatitude\b/);
    expect(text).not.toMatch(/\blongitude\b/);
    expect(text).not.toMatch(/\btrip\b/);
    expect(text).not.toMatch(/\bcell.?location\b/);
    expect(text).not.toMatch(/\bwaypoint\b/);
  });
});

// ── Fixture provenance compliance ─────────────────────────────────────────────

describe("fixture provenance compliance", () => {
  it("every QUALITY_SIGNALS payload field has provenance: 'simulated'", () => {
    const PAYLOAD_FIELDS = [
      "signalName",
      "populationScore",
      "trend",
      "vehicleCount",
      "scope",
      "summary",
    ] as const;

    for (const signal of QUALITY_SIGNALS) {
      for (const field of PAYLOAD_FIELDS) {
        expect(signal.payload[field].provenance).toBe("simulated");
      }
    }
  });
});

// ── Aggregate population data only ───────────────────────────────────────────

describe("aggregate population data only", () => {
  it("vehicleCount values are numeric aggregate counts", () => {
    for (const signal of QUALITY_SIGNALS) {
      const count = signal.payload.vehicleCount.value;
      expect(typeof count).toBe("number");
    }
  });

  it("populationScore values are in 0..100 range", () => {
    for (const signal of QUALITY_SIGNALS) {
      const score = signal.payload.populationScore.value;
      expect(score).not.toBeNull();
      expect(score as number).toBeGreaterThanOrEqual(0);
      expect(score as number).toBeLessThanOrEqual(100);
    }
  });

  it("no payload field is named 'vin', 'driverIdentity', or similar", () => {
    for (const signal of QUALITY_SIGNALS) {
      const keys = Object.keys(signal.payload);
      for (const key of keys) {
        expect(key.toLowerCase()).not.toMatch(/\bvin\b/);
        expect(key.toLowerCase()).not.toMatch(/driver/);
        expect(key.toLowerCase()).not.toMatch(/gps/);
        expect(key.toLowerCase()).not.toMatch(/location/);
        expect(key.toLowerCase()).not.toMatch(/trip/);
      }
    }
  });
});
