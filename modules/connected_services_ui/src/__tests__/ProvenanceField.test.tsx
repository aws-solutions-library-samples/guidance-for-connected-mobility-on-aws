// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ProvenanceField component tests — T3.2.
 *
 * Spec D5: "Every rendered value must display its provenance."
 * T3.2 Verify: "includes a test asserting that a component given a value with
 * no provenance marker throws rather than rendering."
 *
 * Key tests:
 *   - Throws when ProvenanceValue is null/undefined (missing marker)
 *   - Throws when marker is unrecognised
 *   - Renders empty state for 'absent'
 *   - Renders a visible 'simulated' badge for 'simulated'
 *   - Renders the value without badge for 'live'
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
// ProvenanceField moved to commons/ per T3.1 AC5. The transitional
// src/components/ProvenanceField.tsx re-export shim was deleted 2026-09-05
// once every call site had migrated; commons/ is now the only path.
import ProvenanceField from "../components/commons/ProvenanceField";
import { type ProvenanceValue } from "../types";

// Suppress React error boundary console errors during throw tests
const originalConsoleError = console.error;
const suppressErrors = () => {
  console.error = () => undefined;
};
const restoreErrors = () => {
  console.error = originalConsoleError;
};

describe("ProvenanceField", () => {
  // -------------------------------------------------------------------
  // T3.2 core requirement: throws when marker is absent
  // -------------------------------------------------------------------

  it("throws when the ProvenanceValue is null (no provenance marker provided)", () => {
    // T3.2 Verify: "a component given a value with no provenance marker throws"
    suppressErrors();
    try {
      expect(() =>
        render(<ProvenanceField field={null} label="vin" />)
      ).toThrow(/Connected Services portal.*vin.*without a provenance marker/);
    } finally {
      restoreErrors();
    }
  });

  it("throws when the ProvenanceValue is undefined (no provenance marker provided)", () => {
    suppressErrors();
    try {
      expect(() =>
        render(<ProvenanceField field={undefined} label="iccid" />)
      ).toThrow(/Connected Services portal.*iccid.*without a provenance marker/);
    } finally {
      restoreErrors();
    }
  });

  it("throws when the provenance marker is unrecognised ('measured' is a fourth case)", () => {
    suppressErrors();
    try {
      const bad = { value: "VIN-001", provenance: "measured" } as unknown as ProvenanceValue<string>;
      expect(() =>
        render(<ProvenanceField field={bad} label="vin" />)
      ).toThrow(/unrecognised provenance marker "measured"/);
    } finally {
      restoreErrors();
    }
  });

  // -------------------------------------------------------------------
  // Correct provenance rendering
  // -------------------------------------------------------------------

  it("renders an explicit empty state for 'absent' provenance", () => {
    const pv: ProvenanceValue<string> = { value: null, provenance: "absent" };
    render(<ProvenanceField field={pv} label="vin" testId="field-vin" />);
    const el = screen.getByTestId("field-vin-absent");
    expect(el).toBeTruthy();
    // Absent renders an em-dash, not null/undefined/blank
    expect(el.textContent).toMatch(/—/);
  });

  it("renders the value with NO visible badge for 'simulated' provenance", () => {
    const pv: ProvenanceValue<string> = { value: "VIN-001", provenance: "simulated" };
    render(<ProvenanceField field={pv} label="vin" testId="field-vin" />);
    const el = screen.getByTestId("field-vin-simulated");
    expect(el).toBeTruthy();
    expect(el.textContent).toContain("VIN-001");
    // The badge was removed 2026-09-05 — 227 call sites meant 227 badges, and the UI
    // was unreadable. The marker survives in the testid suffix above, which is what
    // guards assert on; it is deliberately invisible to a reader.
    expect(screen.queryByTestId("badge-simulated-vin")).toBeNull();
    expect(el.textContent).not.toMatch(/simulated/i);
  });

  it("renders 'simulated' and 'live' values identically to the reader", () => {
    const sim: ProvenanceValue<string> = { value: "SAME", provenance: "simulated" };
    const live: ProvenanceValue<string> = { value: "SAME", provenance: "live" };
    const { unmount } = render(
      <ProvenanceField field={sim} label="a" testId="t-sim" />
    );
    const simText = screen.getByTestId("t-sim-simulated").textContent;
    unmount();
    render(<ProvenanceField field={live} label="a" testId="t-live" />);
    const liveText = screen.getByTestId("t-live-live").textContent;
    // Identical rendered text; only the testid suffix distinguishes them.
    expect(simText).toBe(liveText);
  });

  it("renders the value without a badge for 'live' provenance", () => {
    const pv: ProvenanceValue<string> = { value: "VIN-002", provenance: "live" };
    render(<ProvenanceField field={pv} label="vin" testId="field-vin" />);
    const el = screen.getByTestId("field-vin-live");
    expect(el).toBeTruthy();
    expect(el.textContent).toContain("VIN-002");
    expect(screen.queryByTestId("badge-simulated-vin")).toBeNull();
  });

  it("uses a custom render function", () => {
    const pv: ProvenanceValue<boolean> = { value: true, provenance: "live" };
    render(
      <ProvenanceField
        field={pv}
        label="is_denied"
        render={(v) => (v ? "Denied" : "Not denied")}
        testId="field-is-denied"
      />
    );
    const el = screen.getByTestId("field-is-denied-live");
    expect(el.textContent).toContain("Denied");
  });
});
