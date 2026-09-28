// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * DiagnosisWorkbenchView tests — T4.4
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T4.4
 *
 * ## What this file covers
 *
 * 1. settleMarker rendered in both VIN-scoped and no-VIN landing states.
 * 2. No-VIN landing state: instruction panel rendered.
 * 3. VIN-scoped state: summary card + packages table rendered.
 * 4. Drift indicator shown when overallDrift is true (DEGRADED_VIN_0).
 * 5. No-drift indicator shown when overallDrift is false (UNREACHABLE_VIN_0).
 * 6. Not-found alert shown for an unknown VIN.
 * 7. Back button (cs-workbench-back-to-subscriber) navigates to subscriber lookup.
 * 8. Fixture shape: VIN_SOFTWARE_STATES has at least one entry, all provenance
 *    values are {value, provenance} pairs with a valid marker.
 *
 * ## VIN vocabulary
 *
 * All VINs are derived from the fixture constants (imported from
 * subscriberLookup.fixture.ts which derives from fleetHealth.fixture.ts) so this
 * file stays aligned with the single source of truth for the VIN catalogue.
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { VALID_PROVENANCE_MARKERS } from "../../../../types";
import DiagnosisWorkbenchView from "../DiagnosisWorkbenchView";
import { VIN_SOFTWARE_STATES } from "../diagnosisWorkbench.fixture";
import {
  DEGRADED_VIN_0,
  UNREACHABLE_VIN_0,
} from "../../connectivity/subscriberLookup.fixture";

// ---------------------------------------------------------------------------
// Harness helpers
// ---------------------------------------------------------------------------

const SETTLE_MARKER = "cs-settle-diagnosis-workbench-stepper";

function renderWorkbench(initialPath: string): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/software/workbench" element={<DiagnosisWorkbenchView />} />
        <Route path="/software/workbench/:signalId" element={<DiagnosisWorkbenchView />} />
        {/* Stub subscriber lookup route so navigation does not 404 */}
        <Route
          path="/connectivity/subscriber-lookup/:vin"
          element={<div data-testid="subscriber-lookup-stub">subscriber lookup</div>}
        />
      </Routes>
    </MemoryRouter>
  );
}

// ---------------------------------------------------------------------------
// settle marker
// ---------------------------------------------------------------------------

describe("DiagnosisWorkbenchView — settle marker", () => {
  afterEach(cleanup);

  it("renders the settle marker in the no-VIN landing state", () => {
    renderWorkbench("/software/workbench");
    expect(document.body.textContent).toContain(SETTLE_MARKER);
  });

  it("renders the settle marker in the VIN-scoped state", () => {
    renderWorkbench(`/software/workbench/${DEGRADED_VIN_0}`);
    expect(document.body.textContent).toContain(SETTLE_MARKER);
  });

  it("renders the settle marker even for an unknown VIN", () => {
    renderWorkbench("/software/workbench/UNKNOWN-VIN");
    expect(document.body.textContent).toContain(SETTLE_MARKER);
  });
});

// ---------------------------------------------------------------------------
// No-VIN landing state
// ---------------------------------------------------------------------------

describe("DiagnosisWorkbenchView — no-VIN landing state", () => {
  afterEach(cleanup);

  it("renders the landing instruction panel when no VIN is in the URL", () => {
    renderWorkbench("/software/workbench");
    expect(screen.getByTestId("cs-workbench-landing")).toBeTruthy();
    expect(document.body.textContent).toContain("Diagnosis Workbench");
    expect(document.body.textContent).toContain("Subscriber Lookup");
  });
});

// ---------------------------------------------------------------------------
// VIN-scoped state — DEGRADED_VIN_0 (overallDrift: true)
// ---------------------------------------------------------------------------

describe(`DiagnosisWorkbenchView — VIN-scoped state (DEGRADED_VIN_0 = ${DEGRADED_VIN_0})`, () => {
  afterEach(cleanup);

  it("renders the summary card and packages table for a known VIN", () => {
    renderWorkbench(`/software/workbench/${DEGRADED_VIN_0}`);
    expect(screen.getByTestId("cs-workbench-summary-card")).toBeTruthy();
    expect(screen.getByTestId("cs-workbench-packages-container")).toBeTruthy();
  });

  it("shows the drift indicator when overallDrift is true", () => {
    renderWorkbench(`/software/workbench/${DEGRADED_VIN_0}`);
    expect(screen.getByTestId("cs-workbench-drift-indicator")).toBeTruthy();
    expect(screen.getByTestId("cs-workbench-drift-alert")).toBeTruthy();
  });

  it("shows the VIN in the summary header", () => {
    renderWorkbench(`/software/workbench/${DEGRADED_VIN_0}`);
    // ProvenanceField appends the provenance to testId: "cs-workbench-vin-simulated"
    const vin = screen.getByTestId("cs-workbench-vin-simulated");
    expect(vin.textContent).toContain(DEGRADED_VIN_0);
  });

  it("renders a row for each package in the fixture", () => {
    renderWorkbench(`/software/workbench/${DEGRADED_VIN_0}`);
    const packages = VIN_SOFTWARE_STATES[DEGRADED_VIN_0]!.packages;
    for (const pkg of packages) {
      // ProvenanceField appends provenance to testId: "cs-workbench-pkg-name-<id>-simulated"
      expect(screen.getByTestId(`cs-workbench-pkg-name-${pkg.id}-simulated`)).toBeTruthy();
    }
  });
});

// ---------------------------------------------------------------------------
// VIN-scoped state — UNREACHABLE_VIN_0 (overallDrift: false)
// ---------------------------------------------------------------------------

describe(`DiagnosisWorkbenchView — VIN-scoped state (UNREACHABLE_VIN_0 = ${UNREACHABLE_VIN_0}, no drift)`, () => {
  afterEach(cleanup);

  it("shows the no-drift indicator when overallDrift is false", () => {
    renderWorkbench(`/software/workbench/${UNREACHABLE_VIN_0}`);
    expect(screen.getByTestId("cs-workbench-no-drift-indicator")).toBeTruthy();
    // drift-alert must NOT be present
    expect(screen.queryByTestId("cs-workbench-drift-alert")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Not-found state
// ---------------------------------------------------------------------------

describe("DiagnosisWorkbenchView — unknown VIN", () => {
  afterEach(cleanup);

  it("renders the not-found alert for an unknown VIN", () => {
    renderWorkbench("/software/workbench/UNKNOWN-VIN-999");
    expect(screen.getByTestId("cs-workbench-vin-not-found")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Back navigation
// ---------------------------------------------------------------------------

describe("DiagnosisWorkbenchView — back button", () => {
  afterEach(cleanup);

  it("renders the back button in VIN-scoped state", () => {
    renderWorkbench(`/software/workbench/${DEGRADED_VIN_0}`);
    const btn = screen.getByTestId("cs-workbench-back-to-subscriber");
    expect(btn).toBeTruthy();
  });

  it("does NOT render the back button in the no-VIN landing state", () => {
    renderWorkbench("/software/workbench");
    expect(screen.queryByTestId("cs-workbench-back-to-subscriber")).toBeNull();
  });

  it("clicking the back button navigates to subscriber-lookup stub for that VIN", async () => {
    const user = userEvent.setup();
    renderWorkbench(`/software/workbench/${DEGRADED_VIN_0}`);

    const btn = screen.getByTestId("cs-workbench-back-to-subscriber");
    await user.click(btn);

    await waitFor(() => {
      expect(screen.getByTestId("subscriber-lookup-stub")).toBeTruthy();
    });
  });
});

// ---------------------------------------------------------------------------
// Fixture shape guard
// ---------------------------------------------------------------------------

describe("DiagnosisWorkbenchView — fixture shape", () => {
  it("VIN_SOFTWARE_STATES contains at least one entry", () => {
    expect(Object.keys(VIN_SOFTWARE_STATES).length).toBeGreaterThan(0);
  });

  it("every displayed ProvenanceValue in the fixture has a valid provenance marker", () => {
    const violations: string[] = [];
    const validMarkers = new Set<string>(VALID_PROVENANCE_MARKERS);
    const EXEMPT_KEYS = new Set(["id", "computed_at", "confidence", "evidence", "agent_version", "inputs_hash"]);

    function checkNode(node: unknown, path: string): void {
      if (node == null || typeof node !== "object") return;
      if (Array.isArray(node)) {
        node.forEach((item, i) => checkNode(item, `${path}[${i}]`));
        return;
      }
      const obj = node as Record<string, unknown>;
      // If it looks like a ProvenanceValue, validate it
      if ("value" in obj && "provenance" in obj && Object.keys(obj).length === 2) {
        const prov = obj["provenance"];
        if (typeof prov !== "string" || !validMarkers.has(prov)) {
          violations.push(`${path}: invalid provenance "${String(prov)}"`);
        }
        return;
      }
      // Otherwise recurse into keys, skipping exempt ones
      for (const [key, val] of Object.entries(obj)) {
        if (!EXEMPT_KEYS.has(key)) {
          checkNode(val, `${path}.${key}`);
        }
      }
    }

    for (const [vin, state] of Object.entries(VIN_SOFTWARE_STATES)) {
      checkNode(state, `VIN_SOFTWARE_STATES["${vin}"]`);
    }

    expect(violations, `Fixture provenance violations:\n${violations.join("\n")}`).toHaveLength(0);
  });
});
