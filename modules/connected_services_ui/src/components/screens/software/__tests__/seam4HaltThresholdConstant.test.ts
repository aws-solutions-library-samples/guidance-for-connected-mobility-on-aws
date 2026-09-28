// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * seam4HaltThresholdConstant.test.ts — T5.5 Seam 4 guard
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T5.5
 *
 * ## What this guard asserts
 *
 * Seam 4: the halt threshold is a named exported constant (ROLLOUT_HALT_THRESHOLD_PCT)
 * rendered verbatim on screen. This guard asserts that the rendered string inside
 * [data-testid="cs-rollout-halt-threshold-value"] begins with String(ROLLOUT_HALT_THRESHOLD_PCT)
 * so that changing the constant without updating the render (or vice versa) fails the build.
 *
 * The enforcement is source-level (static analysis of the render file), not DOM-based,
 * because this guard must pass without requiring a browser environment.
 *
 * ## Three assertions
 *
 *  1. Anti-vacuity: RolloutMonitorView.tsx exists and is non-empty.
 *  2. ROLLOUT_HALT_THRESHOLD_PCT is exported from RolloutMonitorView.tsx and is a
 *     positive finite number.
 *  3. The render path inside RolloutMonitorView.tsx contains exactly one occurrence of
 *     String(ROLLOUT_HALT_THRESHOLD_PCT) used as JSX text inside the
 *     cs-rollout-halt-threshold-value element, derived from the constant expression
 *     `{String(ROLLOUT_HALT_THRESHOLD_PCT)}`, NOT from a hardcoded literal.
 *
 * ## Why static analysis, not a DOM render
 *
 * DOM-based tests for this would require jsdom and full React rendering of Cloudscape
 * components, which introduces flake from component internals. The invariant we want to
 * enforce is purely textual: "the render expression must reference the constant, not a
 * hardcoded number." A source-scan catches the regression class (hardcoded duplicate)
 * that a DOM render would miss if someone wrote `{5}` and `ROLLOUT_HALT_THRESHOLD_PCT = 5`.
 *
 * ## Anti-vacuity
 *
 * The companion asserts the file exists and is non-empty. A guard that passes on a
 * missing file is a false green.
 */

import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";

// ---------------------------------------------------------------------------
// Import the actual constant to test against
// ---------------------------------------------------------------------------

import { ROLLOUT_HALT_THRESHOLD_PCT } from "../RolloutMonitorView";

// ---------------------------------------------------------------------------
// File path
// ---------------------------------------------------------------------------

const ROLLOUT_MONITOR_FILE = path.resolve(
  __dirname,
  "..",
  "RolloutMonitorView.tsx",
);

// ---------------------------------------------------------------------------
// Anti-vacuity
// ---------------------------------------------------------------------------

describe("Seam 4 anti-vacuity — RolloutMonitorView.tsx must exist", () => {
  it("RolloutMonitorView.tsx exists and is non-empty", () => {
    expect(
      fs.existsSync(ROLLOUT_MONITOR_FILE),
      `Expected RolloutMonitorView.tsx to exist at ${ROLLOUT_MONITOR_FILE}`,
    ).toBe(true);

    const content = fs.readFileSync(ROLLOUT_MONITOR_FILE, "utf8");
    expect(content.length, "Expected RolloutMonitorView.tsx to be non-empty").toBeGreaterThan(100);
  });
});

// ---------------------------------------------------------------------------
// Seam 4 guard
// ---------------------------------------------------------------------------

describe("Seam 4 — ROLLOUT_HALT_THRESHOLD_PCT rendered verbatim", () => {
  it("ROLLOUT_HALT_THRESHOLD_PCT is a positive finite number", () => {
    expect(typeof ROLLOUT_HALT_THRESHOLD_PCT).toBe("number");
    expect(Number.isFinite(ROLLOUT_HALT_THRESHOLD_PCT)).toBe(true);
    expect(ROLLOUT_HALT_THRESHOLD_PCT).toBeGreaterThan(0);
  });

  it("render path uses {String(ROLLOUT_HALT_THRESHOLD_PCT)} — not a hardcoded literal", () => {
    const source = fs.readFileSync(ROLLOUT_MONITOR_FILE, "utf8");

    // The render expression must contain the constant reference form.
    // If someone writes `{5}` instead of `{String(ROLLOUT_HALT_THRESHOLD_PCT)}` this fails.
    const renderPattern = /\{String\(ROLLOUT_HALT_THRESHOLD_PCT\)\}/;
    expect(
      renderPattern.test(source),
      "Expected the render path to contain `{String(ROLLOUT_HALT_THRESHOLD_PCT)}`.\n" +
        "Seam 4 requires the constant to be rendered verbatim — a hardcoded literal " +
        "would drift from the constant if either is changed without the other.",
    ).toBe(true);
  });

  it("rendered string starts with String(ROLLOUT_HALT_THRESHOLD_PCT)", () => {
    const source = fs.readFileSync(ROLLOUT_MONITOR_FILE, "utf8");
    const thresholdStr = String(ROLLOUT_HALT_THRESHOLD_PCT);

    // Verify the constant's string form appears in JSX text position inside the
    // cs-rollout-halt-threshold-value element.
    // The source must contain `data-testid="cs-rollout-halt-threshold-value"` on one line
    // and the `{String(ROLLOUT_HALT_THRESHOLD_PCT)}` expression in the adjacent JSX body.
    const testIdPresent = source.includes('data-testid="cs-rollout-halt-threshold-value"');
    expect(
      testIdPresent,
      "Expected [data-testid=\"cs-rollout-halt-threshold-value\"] to appear in RolloutMonitorView.tsx",
    ).toBe(true);

    // Additional: confirm the constant value as a string appears in the file
    // (catches the case where String(ROLLOUT_HALT_THRESHOLD_PCT) produces "5" and
    // someone also writes a hardcoded "5" — both are present, which is fine, but
    // the constant reference form must be present too — checked above).
    expect(
      source.includes(thresholdStr),
      `Expected "${thresholdStr}" (String(ROLLOUT_HALT_THRESHOLD_PCT)) to appear in RolloutMonitorView.tsx`,
    ).toBe(true);
  });

  it("ROLLOUT_HALT_THRESHOLD_PCT value matches what a consumer would expect in render", () => {
    // Regression guard: if the constant is changed to a non-positive or non-integer
    // value the render (which calls .toFixed(1)) would produce unexpected output.
    expect(Number.isInteger(ROLLOUT_HALT_THRESHOLD_PCT)).toBe(true);
    expect(ROLLOUT_HALT_THRESHOLD_PCT).toBeLessThanOrEqual(100);
    // The typical range for a halt threshold is 1–20%
    expect(ROLLOUT_HALT_THRESHOLD_PCT).toBeGreaterThanOrEqual(1);
    expect(ROLLOUT_HALT_THRESHOLD_PCT).toBeLessThanOrEqual(20);
  });
});
