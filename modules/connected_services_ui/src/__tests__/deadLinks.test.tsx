// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Dead-link guard — T2.3 (RED PHASE).
 *
 * Two properties:
 *   L1  Every href in the rendered side-nav resolves to a registry path.
 *   L2  Every internal navigation target in screen components
 *       (navigate("…"), href="/…", <Link to="…">) resolves to a registry path
 *       or a declared legacy redirect. Query strings are stripped before matching.
 *
 * Expected state (as of T3.3/T3.4, 2026-09-04):
 *   L1  PASS — AppShell derives every nav item from getNavSections(), so every
 *       href is a registry path by construction. Previously failed against v1's
 *       hardcoded NavShell, which is now deleted.
 *   L2  FAIL — no src/components/screens/ directory exists yet, so the
 *       anti-vacuity companion fails (empty scan set). Clears in Groups 4-6.
 *
 * Anti-vacuity companions:
 *   - The rendered nav must contain at least one anchor element.
 *   - The screen-component scan must cover at least one file (anti-vacuity
 *     on the file-level scan; the companion FAILS today because screens/
 *     does not exist).
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/spec.md § T3.3
 * Tasks: T2.3, T3.3
 */

import { readFileSync, readdirSync, statSync } from "fs";
import path from "path";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { SCREEN_REGISTRY, getAllPaths } from "../screenRegistry";
import AppShell from "../components/layout/AppShell";

const MODULE_SRC = path.resolve(import.meta.dirname, "..");

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Collect all .tsx/.ts files under a directory, non-recursively stopping at node_modules. */
function collectTsxFiles(root: string): string[] {
  const results: string[] = [];
  try {
    statSync(root); // throws if dir does not exist
  } catch {
    return results;
  }
  function walk(dir: string): void {
    for (const entry of readdirSync(dir)) {
      const full = path.join(dir, entry);
      let st: ReturnType<typeof statSync>;
      try {
        st = statSync(full);
      } catch {
        continue;
      }
      if (st.isDirectory()) {
        if (entry !== "node_modules" && entry !== "dist" && entry !== "__tests__") {
          walk(full);
        }
      } else if (entry.endsWith(".tsx") || entry.endsWith(".ts")) {
        results.push(full);
      }
    }
  }
  walk(root);
  return results;
}

/** Strip query string from a path. */
function stripQuery(p: string): string {
  return p.split("?")[0] ?? p;
}

/** Normalise a potentially relative path to absolute (prepend / if needed). */
function toAbsolute(p: string): string {
  if (p.startsWith("/")) return p;
  // Relative paths like "fleet-health" become "/fleet-health"
  return `/${p}`;
}

// Registry path set for fast membership checks.
const REGISTRY_PATHS = new Set(getAllPaths());

// Legacy redirects that are declared in App.tsx but not in the registry
// (e.g. the old v1 root redirect / → /fleet-health). Add entries here
// as the app grows; entries not in this set that also miss the registry are
// dead links.
const DECLARED_LEGACY_REDIRECTS: ReadonlySet<string> = new Set([
  // v1 root redirect — the registry's "/" entry covers the post-rewrite target.
  // Until that rewrite lands, the nav legitimately points here.
  "/",
]);

// ---------------------------------------------------------------------------
// L1 — every nav href resolves to a registry path
// ---------------------------------------------------------------------------

/**
 * L1 renders the real shell and inspects every <a href="…"> in the side-nav.
 * Each href must be a registry path.
 *
 * ## Repointed 2026-09-04, when T3.3 landed
 *
 * L1 originally rendered v1's `NavShell`, whose two hardcoded hrefs
 * (/fleet-health, /subscriber-lookup) do not match the registry's
 * /connectivity/* paths — so L1 failed by construction, which was the correct
 * red-phase signal at the time.
 *
 * T3.3 replaced that nav with `AppShell`, deriving every item from
 * `getNavSections()`. `NavShell` became an orphan that nothing rendered, and is
 * deleted in this same change. Leaving L1 pointed at it would have kept the
 * guard red against a file the application no longer uses — a failure that
 * looks like a dead link but is really a stale test subject, which is worse
 * than either a pass or an honest fail because it trains the reader to discount
 * the guard.
 *
 * L1 now asserts against the nav the user actually sees.
 */
describe("L1 — every side-nav href resolves to a registry path", () => {
  afterEach(cleanup);

  it("anti-vacuity: rendered nav contains at least one anchor element", () => {
    render(
      <MemoryRouter>
        <AppShell>
          <div />
        </AppShell>
      </MemoryRouter>
    );
    const anchors = document.querySelectorAll("a[href]");
    expect(
      anchors.length,
      "Nav must render at least one <a href=…> for the dead-link guard to be non-vacuous"
    ).toBeGreaterThan(0);
  });

  it("every nav <a href=…> resolves to a registry path or a declared legacy redirect", () => {
    render(
      <MemoryRouter>
        <AppShell>
          <div />
        </AppShell>
      </MemoryRouter>
    );

    const anchors = document.querySelectorAll<HTMLAnchorElement>("a[href]");
    const deadLinks: string[] = [];

    for (const anchor of Array.from(anchors)) {
      const rawHref = anchor.getAttribute("href") ?? "";
      // Ignore external links, mailto, hash-only links
      if (
        rawHref.startsWith("http://") ||
        rawHref.startsWith("https://") ||
        rawHref.startsWith("mailto:") ||
        rawHref === "#"
      ) {
        continue;
      }
      const normalised = stripQuery(toAbsolute(rawHref));
      if (!REGISTRY_PATHS.has(normalised) && !DECLARED_LEGACY_REDIRECTS.has(normalised)) {
        deadLinks.push(`"${rawHref}" (normalised: "${normalised}")`);
      }
    }

    expect(
      deadLinks,
      `Dead nav hrefs (not in registry or declared legacy redirects):\n${deadLinks.join("\n")}`
    ).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// L2 — every in-screen navigation target resolves to a registry path
// ---------------------------------------------------------------------------

/**
 * L2 does a static scan of src/components/screens/**\/*.tsx looking for three
 * navigation patterns:
 *   (a) navigate("…") or navigate(`…`) calls
 *   (b) href="/…" or href={"…"} attributes
 *   (c) <Link to="…"> or <Link to={"…"}> usages
 *
 * Each extracted path is normalised, query-stripped, and checked against the
 * registry (or declared legacy redirects).
 *
 * Anti-vacuity companion: the scan must cover at least one file. Today the
 * screens/ directory does not exist, so the companion FAILS. This is the
 * correct red state — the guard is vacuous on a missing directory.
 *
 * Once screens/ is populated (T4 onwards), the companion passes and L2 starts
 * validating real navigation targets.
 */
describe("L2 — every in-screen navigation target resolves to a registry path", () => {
  const screensDir = path.join(MODULE_SRC, "components", "screens");

  it("anti-vacuity: src/components/screens/ contains at least one .tsx file (screens not implemented yet — EXPECTED FAIL)", () => {
    const files = collectTsxFiles(screensDir);
    // This assertion FAILS at Groups 1-2 stage (no screens/ yet).
    // Once T4 creates screen components, this passes and enables L2 to be
    // non-vacuous.
    expect(
      files.length,
      `src/components/screens/ must contain at least one .tsx file. ` +
        `Currently found ${files.length} file(s). ` +
        `This is expected to fail until screen components are authored (Groups 3-6).`
    ).toBeGreaterThan(0);
  });

  it("every navigate/href/Link target in screen components resolves to a registry path or declared legacy redirect", () => {
    const files = collectTsxFiles(screensDir);

    // If no files exist (Groups 1-2 state), skip the body of this test.
    // The anti-vacuity companion above already catches the empty-directory case.
    if (files.length === 0) {
      // This keeps P2 from throwing on an empty file list while the anti-vacuity
      // companion signals the failure clearly.
      return;
    }

    // Patterns to extract navigation targets from source.
    // (a) navigate("…") / navigate('…') / navigate(`…`)
    const navigateRe = /\bnavigate\(\s*[`"']([^`"']+)[`"']/g;
    // (b) href="/…" or href={"…"} (absolute paths only — relative hrefs are rare in SPA)
    const hrefRe = /\bhref=(?:["']|{["'])([^"'}]+)["'}]/g;
    // (c) <Link to="…"> or <Link to={'…'}> or <Link to={`…`}>
    const linkToRe = /<Link\b[^>]*\bto=(?:["'{`])([^"'`}]+)(?:["'`}])/g;

    const deadTargets: string[] = [];

    for (const file of files) {
      const src = readFileSync(file, "utf8");
      const relPath = path.relative(MODULE_SRC, file);

      const extractAndCheck = (re: RegExp, category: string): void => {
        re.lastIndex = 0;
        let m: RegExpExecArray | null;
        while ((m = re.exec(src)) !== null) {
          const raw = m[1];
          if (!raw) continue;
          // Skip template-literal expressions and dynamic paths
          if (raw.includes("${") || raw.includes("..")) continue;
          // Skip external URLs
          if (raw.startsWith("http://") || raw.startsWith("https://")) continue;
          const normalised = stripQuery(toAbsolute(raw));
          if (!REGISTRY_PATHS.has(normalised) && !DECLARED_LEGACY_REDIRECTS.has(normalised)) {
            deadTargets.push(`${relPath} [${category}]: "${raw}" → "${normalised}"`);
          }
        }
      };

      extractAndCheck(navigateRe, "navigate()");
      extractAndCheck(hrefRe, "href");
      extractAndCheck(linkToRe, "<Link to>");
    }

    expect(
      deadTargets,
      `Dead navigation targets found (not in registry or declared legacy redirects):\n${deadTargets.join("\n")}`
    ).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Bonus: registry itself declares non-dead paths (sanity check)
// ---------------------------------------------------------------------------

describe("Registry paths are internally consistent", () => {
  it("every registry path starts with / (absolute)", () => {
    const relative = SCREEN_REGISTRY.filter((e) => !e.path.startsWith("/"));
    expect(
      relative.map((e) => `id=${e.id} path="${e.path}"`),
      "All registry paths must be absolute"
    ).toHaveLength(0);
  });

  it("registry has exactly 32 entries (snapshot guard)", () => {
    // 23 from spec 2026-09-04-cms-connected-services-portal-v2 + 1 added by
    // spec 2026-09-10-cms-connected-services-subscriptions:
    //   * "Available Vehicles"  (T3.6, per its § D5)
    //   [My Subscriptions removed 2026-09-14 — 3P subscriber-persona surface,
    //    doesn't belong in this OEM-facing portal.]
    // + 1 added by spec 2026-09-12-cs-simulator-oem2-manifest-path:
    //   * "Simulate Vehicle"    (T7.8)
    // + 2 added by stub pass 2026-09-14-cs-portal-persona-lenses-stub:
    //   * "Subscribers"         (OEM lens — roster)
    //   * "Grants"              (OEM lens — VIN availability)
    // + 1 added by stub-pass extension (Signal Catalog + Data Product Detail):
    //   * "Signal Catalog"      (atomic telemetry channels)
    // + 3 added by stub-pass Data Model extension:
    //   * "Vehicle Models"      (Data Model — vehicle platform catalog)
    //   * "ECUs"                (Data Model — ECU catalog, list-only)
    //   * "Decoder Manifests"   (Data Model — decoder manifest list)
    // + 1 added by spec 2026-09-14-cs-portal-data-model-backend T5.2:
    //   * "Data-Collection Campaigns" (data-model section, CMS campaign API)
    expect(SCREEN_REGISTRY.length).toBe(32);
  });
});
