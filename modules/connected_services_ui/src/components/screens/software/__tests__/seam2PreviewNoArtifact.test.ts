// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Seam 2 guard — fleet preview component imports no artifact-shaped fixture.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T5.4 Seam 2
 *
 * ## What this guard enforces
 *
 * The "N vehicles match" preview (FleetPreviewCount) is a pure function over
 * fixture rows, NOT over a Tier2Artifact. The distinction must be visible in
 * the UI: a plain count next to a labelled-AI card is the mechanism that makes
 * the deterministic/non-deterministic split legible to the user.
 *
 * If FleetPreviewCount imported diagnosisWorkbench.fixture.ts (which contains
 * WORKBENCH_WORKFLOW and the DiagnosisArtifact), the seam would collapse: the
 * preview would be reading from the same artifact-shaped data as the AI card,
 * and the split would be invisible.
 *
 * This guard asserts:
 *
 *   1. FleetPreviewCount.tsx exists and is non-empty (anti-vacuity).
 *
 *   2. FleetPreviewCount.tsx does NOT import diagnosisWorkbench.fixture.
 *      (The artifact-shaped data lives there: Tier2Artifact, DiagnosisArtifact,
 *      WorkbenchWorkflow, evidence[].)
 *
 *   3. FleetPreviewCount.tsx does NOT import any file whose name contains
 *      "artifact" or "tier2" (case-insensitive), preventing the seam from being
 *      re-broken by a renamed artifact module.
 *
 *   4. FleetPreviewCount.tsx exports computeFleetMatchCount as a function
 *      (verifiable via import), confirming the pure-function contract is live.
 *
 *   5. computeFleetMatchCount returns a number for every combination of empty
 *      criteria (all rows match) and maximal criteria (no rows match).
 *
 * ## Anti-vacuity
 *
 * The companion suite asserts the source file exists and has content, and that
 * the test actually exercised the function rather than passing on an empty tree.
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { describe, expect, it } from "vitest";
import { computeFleetMatchCount } from "../FleetPreviewCount";
import { FLEET_ROWS } from "../../connectivity/fleetHealth.fixture";

// ---------------------------------------------------------------------------
// File paths
// ---------------------------------------------------------------------------

const MODULE_ROOT = path.resolve(__dirname, "../../../../..");
const PREVIEW_PATH = path.join(
  MODULE_ROOT,
  "src/components/screens/software/FleetPreviewCount.tsx"
);

// ---------------------------------------------------------------------------
// Anti-vacuity
// ---------------------------------------------------------------------------

describe("Seam 2 guard — anti-vacuity: FleetPreviewCount source is present", () => {
  it("FleetPreviewCount.tsx exists and is non-empty", () => {
    expect(fs.existsSync(PREVIEW_PATH), `File not found: ${PREVIEW_PATH}`).toBe(true);
    const content = fs.readFileSync(PREVIEW_PATH, "utf-8");
    expect(content.trim().length, "FleetPreviewCount.tsx must not be empty").toBeGreaterThan(0);
  });

  it("FLEET_ROWS from fleetHealth.fixture has at least one entry (guard ran real data)", () => {
    expect(FLEET_ROWS.length, "FLEET_ROWS must be non-empty — guard would be vacuous on empty fleet").toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// Seam 2 property 1: no import of diagnosisWorkbench.fixture
// ---------------------------------------------------------------------------

describe("Seam 2 guard — no diagnosisWorkbench.fixture import", () => {
  it("FleetPreviewCount.tsx does not import diagnosisWorkbench.fixture", () => {
    const source = fs.readFileSync(PREVIEW_PATH, "utf-8");
    expect(
      source,
      "diagnosisWorkbench.fixture imported in FleetPreviewCount.tsx — the preview must be a pure function over fleet rows only. See T5.4 Seam 2."
    ).not.toContain("diagnosisWorkbench.fixture");
  });
});

// ---------------------------------------------------------------------------
// Seam 2 property 2: no import of any artifact- or tier2-named module
// ---------------------------------------------------------------------------

describe("Seam 2 guard — no artifact-shaped fixture import", () => {
  it("FleetPreviewCount.tsx does not import any file named 'artifact' or 'tier2' (case-insensitive)", () => {
    const source = fs.readFileSync(PREVIEW_PATH, "utf-8");
    // Match any import statement whose specifier contains 'artifact' or 'tier2'
    const artifactImport = /from\s+["'][^"']*(?:artifact|tier2)[^"']*["']/i;
    expect(
      artifactImport.test(source),
      "An artifact- or tier2-named import was found in FleetPreviewCount.tsx. The preview must not read from Tier2Artifact-shaped data. See T5.4 Seam 2."
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Seam 2 property 3: computeFleetMatchCount is a function
// ---------------------------------------------------------------------------

describe("Seam 2 guard — computeFleetMatchCount is exported and callable", () => {
  it("computeFleetMatchCount is a function", () => {
    expect(typeof computeFleetMatchCount).toBe("function");
  });
});

// ---------------------------------------------------------------------------
// Seam 2 property 4: pure-function behaviour
// ---------------------------------------------------------------------------

describe("Seam 2 guard — computeFleetMatchCount pure-function contracts", () => {
  it("empty criteria (all empty arrays) matches all rows", () => {
    const count = computeFleetMatchCount(FLEET_ROWS, {
      markets: [],
      tcuTiers: [],
      connectivityStates: [],
    });
    expect(count).toBe(FLEET_ROWS.length);
  });

  it("an impossible state filter matches zero rows", () => {
    const count = computeFleetMatchCount(FLEET_ROWS, {
      markets: [],
      tcuTiers: [],
      connectivityStates: ["__state_that_does_not_exist__"],
    });
    expect(count).toBe(0);
  });

  it("filtering to Germany market returns only DE rows", () => {
    const deRows = FLEET_ROWS.filter((r) => r.market.value === "Germany");
    const count = computeFleetMatchCount(FLEET_ROWS, {
      markets: ["Germany"],
      tcuTiers: [],
      connectivityStates: [],
    });
    expect(count).toBe(deRows.length);
  });

  it("filtering to TCU-2 returns only TCU-2 rows", () => {
    const tcu2Rows = FLEET_ROWS.filter((r) => r.tcuTier.value === "TCU-2");
    const count = computeFleetMatchCount(FLEET_ROWS, {
      markets: [],
      tcuTiers: ["TCU-2"],
      connectivityStates: [],
    });
    expect(count).toBe(tcu2Rows.length);
  });

  it("combining market and state filters produces a subset of each individual filter", () => {
    const deCount = computeFleetMatchCount(FLEET_ROWS, {
      markets: ["Germany"],
      tcuTiers: [],
      connectivityStates: [],
    });
    const degradedCount = computeFleetMatchCount(FLEET_ROWS, {
      markets: [],
      tcuTiers: [],
      connectivityStates: ["degraded"],
    });
    const combined = computeFleetMatchCount(FLEET_ROWS, {
      markets: ["Germany"],
      tcuTiers: [],
      connectivityStates: ["degraded"],
    });
    expect(combined).toBeLessThanOrEqual(deCount);
    expect(combined).toBeLessThanOrEqual(degradedCount);
  });

  it("returns the same value on repeated calls with the same arguments (pure)", () => {
    const criteria = {
      markets: ["US"],
      tcuTiers: ["TCU-2"],
      connectivityStates: ["degraded"],
    };
    const first = computeFleetMatchCount(FLEET_ROWS, criteria);
    const second = computeFleetMatchCount(FLEET_ROWS, criteria);
    expect(first).toBe(second);
  });
});
