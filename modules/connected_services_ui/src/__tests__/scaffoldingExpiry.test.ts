// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * scaffoldingExpiry.test.ts — the suppression must not outlive its reason.
 *
 * ## What this guards
 *
 * T3.4 requires App.tsx to declare one lazy route per registry entry, but the 23
 * screen modules are not authored until Groups 4-6. So App.tsx carries a
 * `@ts-ignore` above each screen import to suppress TS2307 "cannot find module"
 * for files that do not exist yet.
 *
 * That is legitimate scaffolding for exactly as long as the files are missing. The
 * moment a screen module exists, its `@ts-ignore` stops suppressing "module not
 * found" and starts suppressing *whatever else is wrong on that line* — a default
 * export that isn't a component, a renamed file, a wrong path. It converts from
 * scaffolding into a permanent blindfold, silently, with no diff to notice.
 *
 * This guard makes that conversion impossible: once all 23 screen modules exist,
 * any remaining `@ts-ignore` in App.tsx fails the suite. The scaffolding either
 * gets removed or the build goes red.
 *
 * ## Why a guard rather than a TODO
 *
 * A TODO comment relies on someone reading it at the right moment. This repo's own
 * history is the argument: the spec this test belongs to exists because v1's
 * screen coverage was assumed rather than checked, and Group 2's whole design
 * premise is that a rule with no executable form is not a control. The same
 * standard applies to our own scaffolding.
 *
 * ## Deliberately NOT asserted
 *
 * This does not fail while screens are still missing — that would just be a
 * duplicate of the P1 red-phase signal in registryCompleteness. It only fires on
 * the transition, which is the moment nothing else is watching.
 */

import { describe, it, expect } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { SCREEN_REGISTRY } from "../screenRegistry";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, "..");
const APP_TSX = resolve(SRC, "App.tsx");

/** Pull every relative screen-module path App.tsx lazily imports. */
function importedScreenPaths(appSource: string): string[] {
  const matches = appSource.matchAll(/import\(\s*["'](\.\/components\/screens\/[^"']+)["']\s*\)/g);
  return Array.from(matches, (m) => m[1]);
}

function resolveModule(relativeFromSrc: string): string | null {
  const base = resolve(SRC, relativeFromSrc);
  for (const ext of [".tsx", ".ts"]) {
    if (existsSync(base + ext)) return base + ext;
  }
  return null;
}

describe("scaffolding expiry — @ts-ignore in App.tsx must not outlive the missing screens", () => {
  const source = readFileSync(APP_TSX, "utf8");

  it("anti-vacuity: App.tsx exists and lazily imports at least one screen module", () => {
    expect(source.length, "App.tsx must be readable").toBeGreaterThan(0);
    expect(
      importedScreenPaths(source).length,
      "App.tsx must lazily import screen modules for this guard to mean anything"
    ).toBeGreaterThan(0);
  });

  it("every registry screen is lazily imported by App.tsx", () => {
    const imported = importedScreenPaths(source);

    // Parameterised-detail views that have their own lazy import in App.tsx but
    // deliberately no registry entry (they're reached only via a parent list row
    // click, not the sidebar). The counterpart to /connectivity/subscriber-lookup
    // — which uses the SAME import for both list and :vin routes — chose the
    // one-component pattern; these views chose the two-component pattern because
    // their list and detail render fundamentally different content (grouped table
    // vs tabbed detail page).
    //
    // Added by stub pass 2026-09-14-cs-portal-persona-lenses-stub.
    // CampaignDetailView added by spec 2026-09-20-cs-campaigns-screen-restructure T3.1
    // (reached only by clicking a row in DataCollectionCampaignsView).
    const PARAMETERIZED_DETAIL_ONLY_IMPORTS = [
      "./components/screens/sales/SubscriberDetailView",
      "./components/screens/sales/SignalDetailView",
      "./components/screens/sales/DataProductDetailView",
      "./components/screens/data-model/VehicleModelDetailView",
      "./components/screens/data-model/DecoderManifestDetailView",
      "./components/screens/data-model/CampaignDetailView",
    ];

    const registryImports = imported.filter(
      (p) => !PARAMETERIZED_DETAIL_ONLY_IMPORTS.includes(p),
    );

    expect(
      registryImports.length,
      `App.tsx lazily imports ${registryImports.length} registry-backed screen modules but the ` +
        `registry has ${SCREEN_REGISTRY.length} entries. (Parameterised-detail-only imports ` +
        `excluded from this count: ${PARAMETERIZED_DETAIL_ONLY_IMPORTS.join(", ")}.)`
    ).toBe(SCREEN_REGISTRY.length);
  });

  it("carries no @ts-ignore once every imported screen module exists on disk", () => {
    const imported = importedScreenPaths(source);
    const missing = imported.filter((p) => resolveModule(p) === null);
    const suppressions = Array.from(source.matchAll(/@ts-(ignore|expect-error)/g));

    if (missing.length > 0) {
      // Scaffolding is still doing real work. Record the count so the shrinking
      // set is visible in the test output as Groups 4-6 land.
      expect(
        suppressions.length,
        `${missing.length} screen module(s) still absent, so suppressions are expected. ` +
          `Absent: ${missing.join(", ")}`
      ).toBeGreaterThan(0);
      return;
    }

    expect(
      suppressions.length,
      "All 23 screen modules now exist on disk, so every @ts-ignore in App.tsx has " +
        "outlived its purpose and is now suppressing real type errors instead of " +
        "missing-module errors. Remove them."
    ).toBe(0);
  });

  it("no ambient screen-module declaration file has been reintroduced", () => {
    // src/screens.d.ts was deleted 2026-09-04: it declared all 26 screens as bare
    // React.ComponentType, which would mask genuine prop-type mismatches once the
    // real files landed. A second suppression mechanism for the same 23 errors is
    // strictly worse than one, because the hidden one is the one nobody greps for.
    expect(
      existsSync(resolve(SRC, "screens.d.ts")),
      "src/screens.d.ts must not exist — use the visible @ts-ignore scaffolding, " +
        "which this suite can see expire"
    ).toBe(false);
  });
});
