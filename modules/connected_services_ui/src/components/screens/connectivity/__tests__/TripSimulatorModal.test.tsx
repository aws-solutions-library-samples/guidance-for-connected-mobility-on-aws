// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * TripSimulatorModal — CS duplicate's own render + wiring contract.
 *
 * Spec: `.kiro/specs/2026-09-19-cs-trip-simulator-parity`, T4.3 + T4.2 + FG1.T3.
 *
 * ## What this file guards
 *
 * 1. `showSourceSelector={false}` (CS's real usage, per T4.3 decision (a))
 *    hides the "Source" FormField entirely — not disabled, not read-only text,
 *    absent from the DOM.
 * 2. `showSourceSelector={true}` (the default, for a hypothetical future
 *    second consumer) renders it.
 * 3. Starting a trip calls the injected `onStart` prop with a
 *    `TripSimulationParams` object that never carries `mode` or `rule_name`
 *    (T4.2's own Accept criterion, exercised through this component rather
 *    than through `subscriptionsClient.test.ts`'s isolated unit tests —
 *    this is the integration-level pin).
 * 4. The event catalog, once fetched via the injected `fetchEventCatalog`,
 *    populates the safety/maintenance Multiselect options correctly split
 *    by `category`.
 * 5. (FG1.T3) The inlined `severityLabel` function is faithful to CMS's
 *    canonical `severity.ts` for each of the operator-visible divergences
 *    identified in the review: null → "UNKNOWN" (not the string "null"),
 *    5 → "CRITICAL" (not "5"), "critical" lowercase → "CRITICAL",
 *    "MED" → "MEDIUM". Tested through the option-description text the
 *    component produces from a crafted catalog fixture, since the function
 *    is not exported (per spec's "inline, no new shared module" constraint).
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup } from "@testing-library/react";

import TripSimulatorModal from "../TripSimulatorModal";
import type { EventCatalogResponse } from "../../../../api/dataModelClient";
import type { TripSimulationParams } from "../../../../api/subscriptionsClient";

afterEach(() => {
  cleanup();
});

const CATALOG: EventCatalogResponse = {
  events: [
    {
      event_id: "hard_braking_event",
      category: "safety",
      severity: 3,
      description: "Hard braking event",
      trigger_signal: "Vehicle.Chassis.Brake.PedalPosition",
      threshold_operator: ">=",
      threshold_value: 80,
    },
    {
      event_id: "low_tire_pressure_P0520",
      category: "maintenance",
      severity: 2,
      description: "Low tire pressure",
      trigger_signal: "Vehicle.Chassis.Axle.Row1.Wheel.Left.Tire.Pressure",
      threshold_operator: "<",
      threshold_value: 28,
      dtc_code: "P0520",
    },
  ],
  count: 2,
};

function makeProps(overrides: Partial<Parameters<typeof TripSimulatorModal>[0]> = {}) {
  return {
    visible: true,
    vehicleId: "VEH-CS-DEMO-0021",
    vin: "CSPR0000000000021",
    onDismiss: vi.fn(),
    fetchEventCatalog: vi.fn(async () => CATALOG),
    onStart: vi.fn(async (_vehicleId: string, _params: TripSimulationParams) => "sim-abc123"),
    showSourceSelector: false,
    ...overrides,
  };
}

describe("showSourceSelector", () => {
  it("hides the Source FormField when showSourceSelector={false} (CS's real usage)", () => {
    render(<TripSimulatorModal {...makeProps({ showSourceSelector: false })} />);
    expect(screen.queryByText("Source")).toBeNull();
  });

  it("renders the Source FormField when showSourceSelector={true} (default)", () => {
    render(<TripSimulatorModal {...makeProps({ showSourceSelector: true })} />);
    expect(screen.getByText("Source")).toBeDefined();
  });

  it("defaults to showing it when the prop is omitted entirely", () => {
    const props = makeProps();
    // @ts-expect-error — deliberately omitting the prop to test the default.
    delete props.showSourceSelector;
    render(<TripSimulatorModal {...props} />);
    expect(screen.getByText("Source")).toBeDefined();
  });
});

describe("event catalog loading", () => {
  it("splits fetched events into safety and maintenance option lists", async () => {
    const fetchEventCatalog = vi.fn(async () => CATALOG);
    render(<TripSimulatorModal {...makeProps({ fetchEventCatalog })} />);
    await waitFor(() => expect(fetchEventCatalog).toHaveBeenCalledTimes(1));
    // Once the catalog loads, the Spinner is replaced by the real
    // Multiselect, which renders this text as its placeholder when nothing
    // is selected — its presence confirms loadingCatalog flipped false and
    // the safety/maintenance options were built without throwing. Cloudscape
    // renders the placeholder as visible text content, not an HTML
    // `placeholder=` attribute, so `getByText` is the right query here (NOT
    // `getByPlaceholderText`, which matches only the DOM attribute).
    await waitFor(() =>
      expect(screen.getByText("None (normal driving)")).toBeDefined(),
    );
    expect(screen.getByText("None (healthy vehicle)")).toBeDefined();
  });

  it("degrades to empty option lists (not a crash) when fetchEventCatalog rejects", async () => {
    const fetchEventCatalog = vi.fn(async () => {
      throw new Error("network down");
    });
    render(<TripSimulatorModal {...makeProps({ fetchEventCatalog })} />);
    await waitFor(() => expect(fetchEventCatalog).toHaveBeenCalledTimes(1));
    // Wait for loadingCatalog's terminal state (Spinner -> Multiselect) so
    // the catch block's setState has settled before the test ends — this is
    // what avoids the "not wrapped in act(...)" warning, not asserting a
    // specific placeholder string (the rejection path leaves both option
    // lists at their initial empty state, same placeholder either way).
    await waitFor(() => expect(screen.queryByText("Loading")).toBeNull());
    expect(screen.getByText("Trip Simulator")).toBeDefined();
  });
});

describe("handleStart — T4.2 integration pin", () => {
  it("calls onStart with a TripSimulationParams object carrying no mode/rule_name key", async () => {
    const onStart = vi.fn(async (_vehicleId: string, _params: TripSimulationParams) => "sim-xyz789");
    const onStarted = vi.fn();
    const onDismiss = vi.fn();
    render(
      <TripSimulatorModal
        {...makeProps({ onStart, onStarted, onDismiss })}
      />,
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Start Trip" }));

    await waitFor(() => expect(onStart).toHaveBeenCalledTimes(1));
    const calledArgs = onStart.mock.calls[0];
    const calledVehicleId = calledArgs[0];
    const calledParams = calledArgs[1];
    expect(calledVehicleId).toBe("VEH-CS-DEMO-0021");
    expect(calledParams).not.toHaveProperty("mode");
    expect(calledParams).not.toHaveProperty("rule_name");
    // The five fields TripSimulationParams actually declares.
    expect(calledParams).toHaveProperty("city");
    expect(calledParams).toHaveProperty("trips");
    expect(calledParams).toHaveProperty("route_length");
    expect(calledParams).toHaveProperty("safety_scenarios");
    expect(calledParams).toHaveProperty("maintenance_scenarios");
  });

  it("calls onStarted with the returned simulation id and dismisses on success", async () => {
    const onStart = vi.fn(async () => "sim-xyz789");
    const onStarted = vi.fn();
    const onDismiss = vi.fn();
    render(<TripSimulatorModal {...makeProps({ onStart, onStarted, onDismiss })} />);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Start Trip" }));

    await waitFor(() => expect(onStarted).toHaveBeenCalledWith("sim-xyz789"));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("surfaces a local error and does NOT dismiss when onStart rejects", async () => {
    const onStart = vi.fn(async () => {
      throw new Error("vehicle has no dataSource configured");
    });
    const onDismiss = vi.fn();
    render(<TripSimulatorModal {...makeProps({ onStart, onDismiss })} />);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Start Trip" }));

    await waitFor(() =>
      expect(screen.getByText("vehicle has no dataSource configured")).toBeDefined(),
    );
    expect(onDismiss).not.toHaveBeenCalled();
  });
});

// ── FG1.T3: severityLabel divergence-case unit tests ─────────────────────────
//
// The inlined `severityLabel` is not exported, so we exercise it through the
// option `description` text that `optionsFromCatalog()` builds from the injected
// catalog.  Each test renders one safety event with a specific `severity` value
// and asserts the description the component produces — the four divergences W1
// identifies plus the two canonical pass-throughs that confirm the fix didn't
// break anything.
//
// Mutation-verify note (per testing.md):
//   Reverting the fix (the old `map + ?? String(value)` implementation) causes:
//     - null test: description contains "null" → assertion fails
//     - 5 test: description contains "5"  → assertion fails
//     - "critical" (lower) test: description contains "critical" → assertion fails
//     - "MED" test: description contains "MED" → assertion fails
//   Each is individually confirmed below in the Verify step of FG1.T3 (see
//   tasks.md Result note).

/** Build a single-item catalog with the given severity/severity_hint. */
function catalogWith(
  severity: number | null | undefined,
  severity_hint?: string,
): EventCatalogResponse {
  return {
    events: [
      {
        event_id: "test_ev",
        category: "safety",
        severity: severity as number,
        severity_hint,
        description: "Test event",
        trigger_signal: "Vehicle.Speed",
        threshold_operator: ">=",
        threshold_value: 100,
      },
    ],
    count: 1,
  };
}

/** Helper: render the modal with a single-item catalog and return the
 * option-description text that `optionsFromCatalog` produces for the one item.
 *
 * The Multiselect places each option's `description` in an element rendered
 * alongside the option label inside the trigger button's accessible description
 * once the options are expanded.  This helper waits for the catalog to load
 * (Spinner disappears), opens the dropdown, and returns the text of the first
 * option description found.
 */
async function getRenderedSeverityDescription(
  severity: number | null | undefined,
  severity_hint?: string,
): Promise<string> {
  const { unmount } = render(
    <TripSimulatorModal
      {...makeProps({ fetchEventCatalog: vi.fn(async () => catalogWith(severity, severity_hint)) })}
    />,
  );

  // Wait for the catalog to finish loading (Spinner → Multiselect).
  await waitFor(() =>
    expect(screen.queryByText("None (normal driving)")).toBeDefined(),
  );

  // Open the Safety Events multiselect so its options + descriptions render.
  const user = userEvent.setup();
  // The dropdown trigger for the Safety Events Multiselect carries the placeholder text.
  const trigger = screen.getByText("None (normal driving)");
  await user.click(trigger);

  // Description text appears as an element containing "Severity: <label>".
  await waitFor(() =>
    expect(screen.queryByText(/Severity:/)).toBeDefined(),
  );
  const descEl = screen.getByText(/Severity:/);
  const result = descEl.textContent ?? "";
  unmount();
  return result;
}

describe("severityLabel — FG1.T3 divergent-case unit tests (W1)", () => {
  // W1 divergence 1: null → must be "UNKNOWN", not the string "null"
  it("null severity → UNKNOWN (not the string 'null')", async () => {
    const desc = await getRenderedSeverityDescription(null);
    expect(desc).toContain("UNKNOWN");
    expect(desc).not.toContain("null");
  });

  // W1 divergence 2: numeric 5 → CRITICAL (>= 4 threshold), not "5"
  it("severity 5 → CRITICAL (>= 4 threshold, not raw '5')", async () => {
    const desc = await getRenderedSeverityDescription(5);
    expect(desc).toContain("CRITICAL");
    expect(desc).not.toMatch(/Severity: 5\b/);
  });

  // W1 divergence 3: lowercase string "critical" → CRITICAL (not raw "critical")
  it("severity_hint 'critical' lowercase → CRITICAL (not raw 'critical')", async () => {
    const desc = await getRenderedSeverityDescription(undefined, "critical");
    expect(desc).toContain("CRITICAL");
    expect(desc).not.toContain(": critical");
  });

  // W1 divergence 4: "MED" → MEDIUM (not raw "MED")
  it("severity_hint 'MED' → MEDIUM (not raw 'MED')", async () => {
    const desc = await getRenderedSeverityDescription(undefined, "MED");
    expect(desc).toContain("MEDIUM");
    // The raw echo would be ": MED" followed by a non-letter (end, space, pipe).
    // We can't use "not.toContain(': MED')" because "MEDIUM" itself contains "MED".
    // Instead we confirm the rendered text is NOT the exact "Severity: MED" substring
    // that the old String(value) fallback produced — a word-boundary check.
    expect(desc).not.toMatch(/Severity:\s+MED(?!\w)/);
  });

  // Canonical pass-throughs — confirm the fix didn't break working cases.
  it("severity 4 → CRITICAL (canonical numeric)", async () => {
    const desc = await getRenderedSeverityDescription(4);
    expect(desc).toContain("CRITICAL");
  });

  it("severity_hint P1 → HIGH (canonical Px string)", async () => {
    const desc = await getRenderedSeverityDescription(undefined, "P1");
    expect(desc).toContain("HIGH");
  });
});

// ── FG2.T2: Complete numeric mapping coverage ─────────────────────────────────
//
// N1 in review cycle 2: `_check_severity_label_block` in the sync guard only
// checks that all five label strings appear as `return "LABEL"` literals — it
// does NOT verify WHICH input maps to WHICH label. Mutation proof: swapping
// only `3 → MEDIUM` and `2 → HIGH` in severityLabel leaves all five literals
// present, so the guard exits 0 and all tests pass — on a UI that renders
// MEDIUM where CMS renders HIGH for severity 3 (P1 = HIGH in CMS's canonical
// scale). The tests in *this* describe block are the correct layer for mapping
// semantics — they exercise the rendered output for every undocumented input
// value (3, 2, 1, 0, undefined, '') and confirm the direction of the
// reverse-ranked scale.
//
// Canonical scale (from CMS's `severity.ts` docstring):
//   4 = CRITICAL  (P0, worst)
//   3 = HIGH      (P1)
//   2 = MEDIUM    (P2)
//   1 = LOW       (P3, least urgent)
//   <= 1 = LOW  (handles 0 and negative)
//
// Mutation-verify note (per testing.md):
//   Swapping `asNum === 3` → return "MEDIUM" and `asNum === 2` → return "HIGH"
//   (the exact N1 mutation) causes:
//     - "severity 3 → HIGH"   test: description contains "MEDIUM" → assertion fails
//     - "severity 2 → MEDIUM" test: description contains "HIGH"   → assertion fails
//   Each confirmed individually before this task was marked complete.

describe("severityLabel — FG2.T2 complete numeric mapping (N1 fix)", () => {
  // The mid-band — these were entirely untested before FG2.T2.
  // severity 3 = HIGH (P1 in the event catalog's severity_hint system).
  it("severity 3 → HIGH (not MEDIUM, not raw '3')", async () => {
    const desc = await getRenderedSeverityDescription(3);
    expect(desc).toContain("HIGH");
    expect(desc).not.toContain("MEDIUM");
    expect(desc).not.toMatch(/Severity: 3\b/);
  });

  // severity 2 = MEDIUM (P2).
  it("severity 2 → MEDIUM (not HIGH, not raw '2')", async () => {
    const desc = await getRenderedSeverityDescription(2);
    expect(desc).toContain("MEDIUM");
    expect(desc).not.toContain("HIGH");
    expect(desc).not.toMatch(/Severity: 2\b/);
  });

  // severity 1 = LOW (P3, least urgent but not MEDIUM or below-minimum).
  it("severity 1 → LOW (not MEDIUM, not raw '1')", async () => {
    const desc = await getRenderedSeverityDescription(1);
    expect(desc).toContain("LOW");
    expect(desc).not.toContain("MEDIUM");
    expect(desc).not.toMatch(/Severity: 1\b/);
  });

  // severity 0 = LOW (<= 1 branch — ensures the lower-bound branch fires).
  it("severity 0 → LOW (<= 1 lower-bound, not UNKNOWN)", async () => {
    const desc = await getRenderedSeverityDescription(0);
    expect(desc).toContain("LOW");
    expect(desc).not.toContain("UNKNOWN");
    expect(desc).not.toMatch(/Severity: 0\b/);
  });

  // undefined → UNKNOWN (covers the null/undefined/'' early-return branch —
  // undefined is distinct from null and was not covered by FG1.T3's null test).
  it("undefined severity → UNKNOWN", async () => {
    const desc = await getRenderedSeverityDescription(undefined);
    expect(desc).toContain("UNKNOWN");
  });

  // Stringified numeric "3" → HIGH (same path as numeric 3 via Number() cast).
  // Important because event-catalog items sometimes carry stringified numbers
  // depending on which serializer emitted the JSON.
  it("severity_hint '3' (stringified) → HIGH", async () => {
    // Pass as string via severity_hint rather than the numeric severity field
    // so TypeScript doesn't complain about passing a string to the numeric field.
    const desc = await getRenderedSeverityDescription(undefined, "3");
    expect(desc).toContain("HIGH");
    expect(desc).not.toContain("MEDIUM");
  });

  // P0 → CRITICAL, P2 → MEDIUM, P3 → LOW (complete Px coverage; P1/HIGH already
  // covered above in FG1.T3's canonical pass-throughs).
  it("severity_hint P0 → CRITICAL", async () => {
    const desc = await getRenderedSeverityDescription(undefined, "P0");
    expect(desc).toContain("CRITICAL");
  });

  it("severity_hint P2 → MEDIUM", async () => {
    const desc = await getRenderedSeverityDescription(undefined, "P2");
    expect(desc).toContain("MEDIUM");
  });

  it("severity_hint P3 → LOW", async () => {
    const desc = await getRenderedSeverityDescription(undefined, "P3");
    expect(desc).toContain("LOW");
  });

  // HIGH (uppercase) → HIGH, LOW (uppercase) → LOW: canonical string round-trips.
  it("severity_hint 'HIGH' → HIGH (canonical string pass-through)", async () => {
    const desc = await getRenderedSeverityDescription(undefined, "HIGH");
    expect(desc).toContain("HIGH");
  });

  it("severity_hint 'LOW' → LOW (canonical string pass-through)", async () => {
    const desc = await getRenderedSeverityDescription(undefined, "LOW");
    expect(desc).toContain("LOW");
  });
});
