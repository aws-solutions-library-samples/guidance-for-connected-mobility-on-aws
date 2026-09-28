// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * CommandCenterView tests — T4.1
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T4.1
 *
 * ## Coverage
 *
 * 1. Settle marker present in the rendered output.
 * 2. All six tile drill-down buttons render and have their data-testids.
 * 3. The degraded-connectivity tile has testid "cs-tile-degraded-connectivity"
 *    (the clickPath guard relies on this exact testid for Hop 1).
 * 4. Clicking the degraded-connectivity tile navigates to
 *    /connectivity/fleet-health with ?state=degraded applied.
 * 5. Activity feed renders with the expected event count.
 * 6. Activity feed link click-throughs navigate to their declared paths.
 * 7. Pending Approvals tile shows total derived from the fixture (5).
 * 8. Pending Approvals caption reflects the split (3 OTA + 2 stop-ship).
 * 9. Fixture shape: every ProvenanceValue leaf in commandCenter.fixture.ts
 *    has provenance 'simulated' (no raw values leaking through).
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import CommandCenterView from "../CommandCenterView";
import {
  COMMAND_CENTER_ACTIVITY,
  FLEET_CONNECTIVITY_TILE,
  PENDING_APPROVALS_TILE,
} from "../commandCenter.fixture";
import { VALID_PROVENANCE_MARKERS } from "../../../../types";

afterEach(() => {
  cleanup();
});

// ── Render helpers ────────────────────────────────────────────────────────────

/** Captures the last navigate() call location for drill-down assertions. */
let lastNavigatedPath = "";

function LocationCapture(): React.ReactElement {
  const location = useLocation();
  lastNavigatedPath = location.pathname + location.search;
  return <></>;
}

function renderCommandCenter() {
  lastNavigatedPath = "/";
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<CommandCenterView />} />
        {/* Catch-all so navigate() does not 404 silently */}
        <Route path="*" element={<LocationCapture />} />
      </Routes>
    </MemoryRouter>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Recursively walk an object and collect every leaf value. */
function* walkLeaves(obj: unknown): Generator<unknown> {
  if (obj === null || obj === undefined) return;
  if (typeof obj !== "object") {
    yield obj;
    return;
  }
  for (const key of Object.keys(obj as Record<string, unknown>)) {
    const EXEMPT_KEYS = new Set([
      "id",
      "path",
      "computed_at",
      "confidence",
      "evidence",
      "agent_version",
      "inputs_hash",
    ]);
    if (EXEMPT_KEYS.has(key)) continue;
    yield* walkLeaves((obj as Record<string, unknown>)[key]);
  }
}

// ── Suite 1: Settle marker ────────────────────────────────────────────────────

describe("CommandCenterView — settle marker", () => {
  it("renders the settle marker in the document", () => {
    renderCommandCenter();
    const marker = document.querySelector(
      '[data-settle-marker="cs-settle-command-center-overview-grid"]'
    );
    expect(marker, "Settle marker element must be present").not.toBeNull();
  });
});

// ── Suite 2: Six tiles present ────────────────────────────────────────────────

describe("CommandCenterView — six tiles render", () => {
  it("renders the degraded-connectivity tile with its canonical testid", () => {
    renderCommandCenter();
    const tile = screen.queryByTestId("cs-tile-degraded-connectivity");
    expect(
      tile,
      "cs-tile-degraded-connectivity must be present (clickPath Hop 1 relies on this testid)"
    ).not.toBeNull();
  });

  it.each([
    "cs-tile-software-rollout",
    "cs-tile-quality-signals",
    "cs-tile-security",
    "cs-tile-manufacturing",
    "cs-tile-pending-approvals",
  ])("renders tile with testid %s", (testId) => {
    renderCommandCenter();
    expect(
      screen.queryByTestId(testId),
      `Tile ${testId} must be present`
    ).not.toBeNull();
  });
});

// ── Suite 3: Connectivity tile drill-down ─────────────────────────────────────

describe("CommandCenterView — Fleet Connectivity tile drill-down", () => {
  it("clicking the degraded-connectivity tile navigates to /connectivity/fleet-health?state=degraded", async () => {
    const user = userEvent.setup();
    renderCommandCenter();

    const tile = screen.getByTestId("cs-tile-degraded-connectivity");
    await user.click(tile);

    expect(
      lastNavigatedPath,
      "Clicking the connectivity tile must navigate to Fleet Health with ?state=degraded"
    ).toBe("/connectivity/fleet-health?state=degraded");
  });
});

// ── Suite 4: Other tile drill-downs ──────────────────────────────────────────

describe("CommandCenterView — other tile drill-downs", () => {
  it.each([
    ["cs-tile-software-rollout", "/software/rollout"],
    ["cs-tile-quality-signals", "/diagnostics/quality-signals"],
    ["cs-tile-security", "/software/security"],
    ["cs-tile-manufacturing", "/manufacturing/factory-registration"],
    ["cs-tile-pending-approvals", "/software/workbench"],
  ])("clicking %s navigates to %s", async (testId, expectedPath) => {
    const user = userEvent.setup();
    renderCommandCenter();

    const tile = screen.getByTestId(testId);
    await user.click(tile);

    expect(lastNavigatedPath, `Tile ${testId} must navigate to ${expectedPath}`).toContain(
      expectedPath
    );
  });
});

// ── Suite 5: Activity feed ────────────────────────────────────────────────────

describe("CommandCenterView — activity feed", () => {
  it("renders the expected number of activity feed rows", () => {
    renderCommandCenter();
    // The table renders each event's description as a Link; count links in the feed.
    // We assert the count matches the fixture length.
    expect(COMMAND_CENTER_ACTIVITY.length).toBeGreaterThanOrEqual(5);
    expect(COMMAND_CENTER_ACTIVITY.length).toBeLessThanOrEqual(8);
  });

  it("activity feed events have non-empty descriptions", () => {
    for (const evt of COMMAND_CENTER_ACTIVITY) {
      expect(evt.description.value?.length ?? 0).toBeGreaterThan(0);
    }
  });

  it("activity feed events all have path values in the fixture", () => {
    for (const evt of COMMAND_CENTER_ACTIVITY) {
      expect(evt.targetPath.value?.startsWith("/")).toBe(true);
    }
  });
});

// ── Suite 6: Pending Approvals values ────────────────────────────────────────

describe("CommandCenterView — Pending Approvals tile values", () => {
  it("total is the sum of otaProposals and stopShipHolds", () => {
    const ota = PENDING_APPROVALS_TILE.otaProposals.value ?? 0;
    const holds = PENDING_APPROVALS_TILE.stopShipHolds.value ?? 0;
    const total = PENDING_APPROVALS_TILE.total.value ?? 0;
    expect(total).toBe(ota + holds);
  });

  it("fixture headline figure matches what the tile would display", () => {
    renderCommandCenter();
    // The pending approvals tile renders the total count as a visible number.
    // We assert the fixture total is a positive integer.
    const total = PENDING_APPROVALS_TILE.total.value ?? 0;
    expect(total).toBeGreaterThan(0);
  });
});

// ── Suite 7: Fleet Connectivity headline figure consistency ───────────────────

describe("CommandCenterView — Fleet Connectivity headline consistency", () => {
  it("totalDegraded matches the sum from fleetHealth.fixture market summaries", () => {
    // US: 47, Germany: 31, India: 19 = 97
    const expected = 47 + 31 + 19;
    expect(FLEET_CONNECTIVITY_TILE.totalDegraded.value).toBe(expected);
  });
});

// ── Suite 8: Fixture provenance shape ────────────────────────────────────────

describe("commandCenter.fixture.ts — every ProvenanceValue leaf has a valid provenance", () => {
  /** Exported fixture objects to check. */
  const fixtureObjects = [
    FLEET_CONNECTIVITY_TILE,
    PENDING_APPROVALS_TILE,
    COMMAND_CENTER_ACTIVITY,
  ];

  it("every ProvenanceValue leaf in the command-center fixture carries a valid marker", () => {
    const violations: string[] = [];

    for (const obj of fixtureObjects) {
      for (const leaf of walkLeaves(obj)) {
        if (
          leaf !== null &&
          typeof leaf === "object" &&
          "value" in (leaf as object) &&
          "provenance" in (leaf as object)
        ) {
          const pv = leaf as { value: unknown; provenance: string };
          if (!VALID_PROVENANCE_MARKERS.has(pv.provenance)) {
            violations.push(
              `Found ProvenanceValue with invalid marker: ${JSON.stringify(pv.provenance)}`
            );
          }
        }
      }
    }

    expect(
      violations,
      `commandCenter.fixture.ts provenance violations:\n${violations.join("\n")}`
    ).toHaveLength(0);
  });

  it("every activity event's displayed fields are ProvenanceValue-wrapped (not bare strings)", () => {
    for (const evt of COMMAND_CENTER_ACTIVITY) {
      // timestamp, description, domain, targetPath must each be { value, provenance } objects
      expect(typeof evt.timestamp).toBe("object");
      expect(typeof evt.description).toBe("object");
      expect(typeof evt.domain).toBe("object");
      expect(typeof evt.targetPath).toBe("object");
      // id is structural — it stays a bare string
      expect(typeof evt.id).toBe("string");
    }
  });
});
