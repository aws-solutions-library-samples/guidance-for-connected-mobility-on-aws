// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * placeholderInventory.test.ts — content-completeness guard.
 *
 * ## Why this guard exists
 *
 * All 23 screen files were scaffolded up-front so that `vite build` has
 * something to bundle at each deploy checkpoint (decisions.md last entry).
 * That decision costs a signal: `registryCompleteness` P1 now goes green
 * as soon as every route renders its own settleMarker — which it does from
 * the moment the scaffold lands, regardless of whether the screen contains
 * real content. P1's contract is a routing assertion; content completeness
 * is a separate claim.
 *
 * This guard restores the content-completeness signal by scanning screen
 * source files for the `// SCAFFOLDING:` marker that every stub carries.
 *
 * ## Three suites
 *
 *   I1  Anti-vacuity: the scan found all 23 screen files.
 *       PASSES today. Fails if a file is accidentally deleted or the glob
 *       is wrong — either error would make the other suites vacuous.
 *
 *   I2  Inventory report: counts and lists every screen still carrying
 *       `// SCAFFOLDING:`, keyed by registry id.
 *       FAILS today (22 remain). This is intentional and correct — the
 *       failure message names each remaining id and states it clears as
 *       Groups 4-6 land. The test transitions to PASS automatically as each
 *       group replaces its scaffold, with no manual maintenance required.
 *
 *   I3  Security Monitor exemption: the placeholder screen (availability:
 *       'placeholder', id='security-monitor') must NOT carry `// SCAFFOLDING:`
 *       and MUST render PlaceholderPanel from commons.
 *       PASSES today. Guards that T5.6's PlaceholderPanel contract is never
 *       accidentally replaced with a scaffold.
 *
 * ## Detection strategy
 *
 * Each screen file carries exactly one `// SCAFFOLDING:` line comment if
 * it is a scaffold stub. SecurityMonitorView is the sole exception — it is
 * the permanent placeholder (availability: 'placeholder') and was authored
 * without that marker by design.
 *
 * We identify screen files via the same lazy-import pattern that
 * scaffoldingExpiry.test.ts uses: extract `import("./components/screens/…")`
 * paths from App.tsx. This avoids duplicating the path list and means any
 * future screen added to App.tsx is automatically covered.
 *
 * We match each file back to its registry entry by searching the source for
 * the registry's settleMarker string — each marker is unique (P4 asserts this)
 * so the match is unambiguous.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/decisions.md
 *       last entry ("All 23 screen files are scaffolded up-front…")
 * Tasks: scaffold_screens (pre-Group 3), Group 2 guard suite
 */

import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { cleanup, render } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { SCREEN_REGISTRY } from "../screenRegistry";

// ---------------------------------------------------------------------------
// Path constants
// ---------------------------------------------------------------------------

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, "..");
const APP_TSX = resolve(SRC, "App.tsx");
const SCREENS_ROOT = resolve(SRC, "components", "screens");

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Extract the relative screen-module import paths declared in App.tsx's lazy
 * import() calls.  Returns paths like "./components/screens/software/SignalDetectionView".
 */
function screenImportPaths(appSource: string): string[] {
  const matches = appSource.matchAll(
    /import\(\s*["'](\.\/components\/screens\/[^"']+)["']\s*\)/g
  );
  return Array.from(matches, (m) => m[1]);
}

/** Resolve an App.tsx-relative import path to an absolute filesystem path. */
function resolveScreenFile(relativeFromSrc: string): string | null {
  const base = resolve(SRC, relativeFromSrc);
  for (const ext of [".tsx", ".ts"]) {
    const full = `${base}${ext}`;
    if (existsSync(full)) return full;
  }
  return null;
}

interface ScreenFileInfo {
  /** Registry id matched by settleMarker lookup.  null if no match found. */
  registryId: string | null;
  /** Absolute path to the source file. */
  filePath: string;
  /** Raw source text. */
  source: string;
  /** True if the file contains the `// SCAFFOLDING:` line comment. */
  hasScaffoldingMarker: boolean;
  /** Registry availability ('active' | 'placeholder' | null if unmatched). */
  availability: "active" | "placeholder" | null;
}

/**
 * For each screen file path (from App.tsx), read the source and attempt to
 * match it to a registry entry by searching for the entry's settleMarker.
 */
function buildScreenInventory(importPaths: string[]): ScreenFileInfo[] {
  return importPaths.map((rel) => {
    const filePath = resolveScreenFile(rel);
    if (!filePath) {
      return {
        registryId: null,
        filePath: rel,
        source: "",
        hasScaffoldingMarker: false,
        availability: null,
      };
    }

    const source = readFileSync(filePath, "utf8");

    // Detect `// SCAFFOLDING:` on its own line (any column, any trailing text).
    const hasScaffoldingMarker = /^\/\/ SCAFFOLDING:/m.test(source);

    // Match this file to a registry entry by finding which settleMarker
    // appears in the source.  Each marker is unique (P4 asserts this), so the
    // first match is unambiguous.
    const matchedEntry = SCREEN_REGISTRY.find((e) => source.includes(e.settleMarker));

    return {
      registryId: matchedEntry?.id ?? null,
      filePath,
      source,
      hasScaffoldingMarker,
      availability: matchedEntry?.availability ?? null,
    };
  });
}

// ---------------------------------------------------------------------------
// I1 — Anti-vacuity: all 23 screen files present
// ---------------------------------------------------------------------------

/**
 * I1 confirms the scan found every file the registry requires.  Without this,
 * I2's "all active screens have markers" assertion would pass vacuously on an
 * empty or incomplete file list.
 */
describe("I1 — anti-vacuity: scan found all 38 screen files", () => {
  const appSource = readFileSync(APP_TSX, "utf8");
  const importPaths = screenImportPaths(appSource);
  const inventory = buildScreenInventory(importPaths);

  it("App.tsx lazily imports exactly 38 screen modules", () => {
    // 23 from spec 2026-09-04-cms-connected-services-portal-v2 + 2 lazy imports
    // added by spec 2026-09-10-cms-connected-services-subscriptions:
    //   * "AvailableVehiclesView"   (T3.6)
    //   * "SubscriptionsListView"   (T4.3)
    // + 1 added by spec 2026-09-12-cs-simulator-oem2-manifest-path:
    //   * "SimulateVehicleView"     (T7.8)
    // + 4 added by stub pass 2026-09-14-cs-portal-persona-lenses-stub:
    //   * "SubscribersRosterView"   (OEM lens — roster)
    //   * "SubscriberDetailView"    (OEM lens — per-subscriber detail with tabs)
    //   * "GrantsView"              (OEM lens — VIN availability grants)
    //   [SubscriptionsListView + SubscriptionDetailView removed 2026-09-14 —
    //   My Subscriptions is a 3P subscriber-persona surface, doesn't belong
    //   in this OEM-facing portal.]
    // + 3 added by stub-pass extension (Signal Catalog + Data Product Detail):
    //   * "SignalCatalogView"       (atomic telemetry channels)
    //   * "SignalDetailView"        (parameterised signal detail)
    //   * "DataProductDetailView"   (parameterised product detail)
    // + 5 added by stub-pass Data Model extension (Vehicle Models, ECUs, Decoder Manifests):
    //   * "VehicleModelsView"           (Data Model — vehicle platform catalog)
    //   * "VehicleModelDetailView"      (parameterised vehicle model detail)
    //   * "ECUsView"                    (Data Model — ECU catalog, list-only)
    //   * "DecoderManifestsView"        (Data Model — decoder manifest list)
    //   * "DecoderManifestDetailView"   (parameterised decoder manifest detail)
    // + 1 added by spec 2026-09-14-cs-portal-data-model-backend T5.2:
    //   * "DataCollectionCampaignsView" (Data Model — data-collection campaigns)
    // + 1 added by spec 2026-09-20-cs-campaigns-screen-restructure T3.1:
    //   * "CampaignDetailView"          (parameterised campaign detail — row-click only)
    expect(
      importPaths.length,
      `Expected 38 lazy screen imports in App.tsx, found ${importPaths.length}. ` +
        "If this fails, App.tsx's route table has drifted from the registry."
    ).toBe(38);
  });

  it("every imported screen file exists on disk", () => {
    const missing = inventory.filter((info) => !existsSync(info.filePath));
    expect(
      missing.map((info) => info.filePath),
      "These screen files were imported by App.tsx but do not exist on disk:\n" +
        missing.map((info) => `  ${info.filePath}`).join("\n")
    ).toHaveLength(0);
  });

  it("every screen file matches exactly one registry entry via settleMarker", () => {
    // Parameterised-detail-only screen files that deliberately have no registry
    // entry (reached only via a parent list row click, not the sidebar).
    // Added by stub pass 2026-09-14-cs-portal-persona-lenses-stub — see
    // scaffoldingExpiry.test.ts for the counterpart allowlist.
    // CampaignDetailView added by spec 2026-09-20-cs-campaigns-screen-restructure T3.1
    // (reached only by clicking a row in DataCollectionCampaignsView).
    const PARAMETERIZED_DETAIL_ONLY_FILES = new Set([
      "SubscriberDetailView.tsx",
      "SignalDetailView.tsx",
      "DataProductDetailView.tsx",
      "VehicleModelDetailView.tsx",
      "DecoderManifestDetailView.tsx",
      "CampaignDetailView.tsx",
    ]);

    const unmatched = inventory.filter(
      (info) =>
        info.registryId === null &&
        !PARAMETERIZED_DETAIL_ONLY_FILES.has(info.filePath.split("/").pop() ?? ""),
    );
    expect(
      unmatched.map((info) => info.filePath),
      "These screen files could not be matched to a registry entry.\n" +
        "Each screen file must contain its registry entry's settleMarker in the source.\n" +
        unmatched.map((info) => `  ${info.filePath}`).join("\n")
    ).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// I2 — Inventory report: active screens with remaining scaffolding
// ---------------------------------------------------------------------------

/**
 * I2 counts screens still carrying `// SCAFFOLDING:` among those with
 * `availability: 'active'`, lists them by registry id, and FAILS while any
 * remain.
 *
 * Expected state right now (before Groups 4-6):
 *   22 active screens carry `// SCAFFOLDING:`.  The test is RED and the
 *   failure message names every remaining id.
 *
 * As Groups 4-6 replace scaffold content with real implementations, the
 * count shrinks and the test transitions to GREEN automatically once it
 * reaches zero.
 *
 * Security Monitor (availability: 'placeholder') is deliberately excluded
 * from this count — it is the permanent placeholder and has no scaffolding
 * marker by design.  I3 asserts that contract separately.
 */
describe("I2 — active screens must not carry a scaffolding marker", () => {
  const appSource = readFileSync(APP_TSX, "utf8");
  const importPaths = screenImportPaths(appSource);
  const inventory = buildScreenInventory(importPaths);

  // Report count and list in the test output regardless of pass/fail so the
  // shrinking set is visible as Groups 4-6 land.
  const scaffoldedActive = inventory.filter(
    (info) =>
      info.availability === "active" &&
      info.hasScaffoldingMarker
  );
  const scaffoldedIds = scaffoldedActive
    .map((info) => info.registryId ?? "<unmatched>")
    .sort();

  const placeholderActive = inventory.filter(
    (info) => info.availability === "placeholder"
  );

  it(
    `scaffolding inventory: ${scaffoldedActive.length} of ${
      inventory.filter((i) => i.availability === "active").length
    } active screens still carry // SCAFFOLDING:`,
    () => {
      // This test always passes — it is the inventory report.  Its sole
      // purpose is to surface the count and list in the test runner output.
      //
      // vitest prints the description string in the test tree, so the count
      // in the description is visible even when the suite is green.
      //
      // The assertion below is vacuously true and intentionally so.
      expect(
        true,
        // Detailed list printed as the assertion message so it appears in the
        // vitest output regardless of pass/fail.
        scaffoldedActive.length === 0
          ? "All active screens have had their scaffolding replaced — spec content completeness reached."
          : `Remaining scaffolded active screens (${scaffoldedActive.length}):\n` +
            scaffoldedIds.map((id) => `  ${id}`).join("\n") +
            "\n\nThese will clear as Groups 4-6 land.  Security Monitor is excluded" +
            " (availability: 'placeholder', permanent — see I3)."
      ).toBe(true);
    }
  );

  it(
    "FAIL while scaffolding remains — no active screen may carry // SCAFFOLDING: (clears as Groups 4-6 land)",
    () => {
      // This is the sentinel that keeps the content-completeness signal red
      // until all active screens are implemented.  The message is designed so
      // the developer immediately knows both how many remain and what action
      // clears it.
      expect(
        scaffoldedIds,
        `${scaffoldedActive.length} active screen(s) still carry a // SCAFFOLDING: marker.\n` +
          "The spec is not content-complete until this list is empty.\n" +
          "This clears as Groups 4-6 replace the scaffold content.\n" +
          `Remaining ids:\n${scaffoldedIds.map((id) => `  ${id}`).join("\n")}\n` +
          `\nPlaceholder screens (excluded from this check): ` +
          placeholderActive.map((i) => i.registryId ?? "<unmatched>").join(", ")
      ).toHaveLength(0);
    }
  );
});

// ---------------------------------------------------------------------------
// I3 — Security Monitor exemption
// ---------------------------------------------------------------------------

/**
 * I3 asserts the permanent-placeholder contract for Security Monitor:
 *
 *   (a) SecurityMonitorView.tsx does NOT carry `// SCAFFOLDING:`.
 *       A scaffold marker would signal "replace me in Groups 4-6", which is
 *       wrong — this screen is intentionally a placeholder for this release.
 *
 *   (b) SecurityMonitorView.tsx imports PlaceholderPanel from commons.
 *       The spec decision (T5.6) says this route renders PlaceholderPanel;
 *       this assertion encodes that contract so a future refactor cannot
 *       accidentally swap it for a real screen without breaking this guard.
 *
 *   (c) SecurityMonitorView renders a [data-testid="placeholder-panel"] node.
 *       PlaceholderPanel sets this testid on its root element, so the render
 *       test confirms the import is wired through to actual output.
 */
describe("I3 — Security Monitor (availability: 'placeholder') exemption", () => {
  afterEach(cleanup);

  // Locate SecurityMonitorView.tsx via the registry entry.
  const SECURITY_MONITOR_ID = "security-monitor";
  const registryEntry = SCREEN_REGISTRY.find((e) => e.id === SECURITY_MONITOR_ID);

  const SECURITY_MONITOR_PATH = resolve(
    SCREENS_ROOT,
    "software",
    "SecurityMonitorView.tsx"
  );

  it("registry entry for 'security-monitor' is availability: 'placeholder'", () => {
    expect(
      registryEntry,
      `Registry must contain an entry with id='${SECURITY_MONITOR_ID}'`
    ).toBeDefined();
    expect(
      registryEntry?.availability,
      "security-monitor must be declared 'placeholder', not 'active'"
    ).toBe("placeholder");
  });

  it("SecurityMonitorView.tsx does NOT carry // SCAFFOLDING:", () => {
    const source = readFileSync(SECURITY_MONITOR_PATH, "utf8");
    const hasScaffolding = /^\/\/ SCAFFOLDING:/m.test(source);
    expect(
      hasScaffolding,
      "SecurityMonitorView.tsx must not carry a // SCAFFOLDING: comment.\n" +
        "This screen is the permanent placeholder (availability: 'placeholder') and\n" +
        "must never be replaced by Groups 4-6 — it is not scaffolding."
    ).toBe(false);
  });

  it("SecurityMonitorView.tsx imports PlaceholderPanel from commons", () => {
    const source = readFileSync(SECURITY_MONITOR_PATH, "utf8");
    // The import may use a relative path like "../../commons/PlaceholderPanel" or
    // a module alias.  We check for the component name in an import statement.
    const hasImport = /import\s+.*PlaceholderPanel.*from\s+['"]/m.test(source);
    expect(
      hasImport,
      "SecurityMonitorView.tsx must import PlaceholderPanel.\n" +
        "This encodes the T5.6 decision that this route renders the placeholder panel,\n" +
        "not a scaffold stub and not an unimplemented screen."
    ).toBe(true);
  });

  it("SecurityMonitorView renders [data-testid='placeholder-panel'] (PlaceholderPanel is wired through)", async () => {
    // Dynamic import — deferred so the module resolves at test runtime rather
    // than at Vite transform time. Vitest resolves .tsx natively.
    const { default: SecurityMonitorView } = await import(
      "../components/screens/software/SecurityMonitorView"
    );

    const { container } = render(React.createElement(SecurityMonitorView));

    const panel = container.querySelector("[data-testid='placeholder-panel']");
    expect(
      panel,
      "SecurityMonitorView must render an element with [data-testid='placeholder-panel'].\n" +
        "PlaceholderPanel sets this testid on its root Box — if this fails, the component\n" +
        "has been replaced with something that is not the placeholder panel."
    ).not.toBeNull();
  });
});
