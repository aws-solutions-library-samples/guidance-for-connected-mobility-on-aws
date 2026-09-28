// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * pageConfig.test.ts — contract tests for getPageConfig().
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/spec.md D9, T3.2
 * Modelled on: DMS frontend/src/components/commons/__tests__/pageConfig.test.ts
 *
 * ## What a config entry MUST have
 *
 * - `title` — non-empty string (blank banner is the silent failure mode)
 * - `description` — any string (may be empty, but the key must be present)
 * - `breadcrumbs` — array; each entry has text and href
 *
 * ## Coverage
 *
 * Every registry path, the two parameterised sub-routes
 * (/connectivity/subscriber-lookup/:vin, /software/workbench/:signalId),
 * and the unknown-path fallback.
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { getAllPaths, SCREEN_REGISTRY } from "../../../screenRegistry";
import { getPageConfig, type PageConfig } from "../pageConfig";

// ── Helper ─────────────────────────────────────────────────────────────────────

function assertValidConfig(config: PageConfig, path: string): void {
  expect(config, `getPageConfig("${path}") must not return null`).not.toBeNull();

  expect(
    config.title,
    `getPageConfig("${path}").title must be a non-empty string`,
  ).toBeTruthy();
  expect(
    typeof config.title,
    `getPageConfig("${path}").title must be a string`,
  ).toBe("string");
  expect(
    config.title.trim().length,
    `getPageConfig("${path}").title must not be blank`,
  ).toBeGreaterThan(0);

  expect(
    config,
    `getPageConfig("${path}") must have a description field`,
  ).toHaveProperty("description");
  expect(
    typeof config.description,
    `getPageConfig("${path}").description must be a string`,
  ).toBe("string");

  expect(
    Array.isArray(config.breadcrumbs),
    `getPageConfig("${path}").breadcrumbs must be an array`,
  ).toBe(true);

  for (const crumb of config.breadcrumbs) {
    expect(
      typeof crumb.text,
      `getPageConfig("${path}") breadcrumb.text must be a string`,
    ).toBe("string");
    expect(
      crumb.text.trim().length,
      `getPageConfig("${path}") breadcrumb.text must not be blank`,
    ).toBeGreaterThan(0);
    expect(
      typeof crumb.href,
      `getPageConfig("${path}") breadcrumb.href must be a string`,
    ).toBe("string");
  }
}

// ── Every registry path ───────────────────────────────────────────────────────

describe("pageConfig — every registry path returns a valid config", () => {
  const allPaths = getAllPaths();

  it("anti-vacuity: getAllPaths() returns at least 23 paths", () => {
    expect(allPaths.length).toBeGreaterThanOrEqual(23);
  });

  it.each(allPaths)("getPageConfig(%s) returns a valid config", (registryPath) => {
    const config = getPageConfig(registryPath);
    assertValidConfig(config, registryPath);
  });
});

// ── Parameterised sub-routes ──────────────────────────────────────────────────

describe("pageConfig — parameterised sub-routes resolve to their parent", () => {
  it("getPageConfig('/connectivity/subscriber-lookup/VIN-DEMO-001') is valid", () => {
    const config = getPageConfig("/connectivity/subscriber-lookup/VIN-DEMO-001");
    assertValidConfig(config, "/connectivity/subscriber-lookup/VIN-DEMO-001");
    // Must resolve to the subscriber-lookup entry (same banner)
    const parent = getPageConfig("/connectivity/subscriber-lookup");
    expect(config.title).toBe(parent.title);
  });

  it("getPageConfig('/software/workbench/SIG-001') is valid", () => {
    const config = getPageConfig("/software/workbench/SIG-001");
    assertValidConfig(config, "/software/workbench/SIG-001");
    const parent = getPageConfig("/software/workbench");
    expect(config.title).toBe(parent.title);
  });
});

// ── Unknown-path fallback ─────────────────────────────────────────────────────

describe("pageConfig — unknown path fallback", () => {
  it("getPageConfig('/totally-unknown-path') returns a non-empty title", () => {
    const config = getPageConfig("/totally-unknown-path");
    assertValidConfig(config, "/totally-unknown-path");
  });

  it("getPageConfig('/not-real/nested') returns a non-empty title", () => {
    const config = getPageConfig("/not-real/nested");
    assertValidConfig(config, "/not-real/nested");
  });
});

// ── All named routes have distinct titles ─────────────────────────────────────

describe("pageConfig — all named routes have distinct titles", () => {
  it("every registry path produces a distinct title", () => {
    const allPaths = getAllPaths();
    const titlesByPath = allPaths.map((p) => ({
      path: p,
      title: getPageConfig(p).title,
    }));
    const titles = titlesByPath.map((e) => e.title);
    const uniqueTitles = new Set(titles);

    if (uniqueTitles.size !== titles.length) {
      const seen = new Map<string, string>();
      const duplicates: string[] = [];
      for (const { path, title } of titlesByPath) {
        if (seen.has(title)) {
          duplicates.push(`"${title}" on paths: ${seen.get(title)!} AND ${path}`);
        } else {
          seen.set(title, path);
        }
      }
      throw new Error(
        `pageConfig title collision:\n${duplicates.join("\n")}`,
      );
    }

    expect(uniqueTitles.size).toBe(titles.length);
  });
});

// ── Root breadcrumb invariant ─────────────────────────────────────────────────

describe("pageConfig — every non-root path has a root breadcrumb", () => {
  const nonRootPaths = getAllPaths().filter((p) => p !== "/");

  it.each(nonRootPaths)(
    "getPageConfig(%s) has a breadcrumb with href='/'",
    (registryPath) => {
      const config = getPageConfig(registryPath);
      const rootCrumb = config.breadcrumbs.find((c) => c.href === "/");
      expect(
        rootCrumb,
        `getPageConfig("${registryPath}") must have a breadcrumb with href="/"`,
      ).toBeDefined();
    },
  );
});

// ── Return type shape ─────────────────────────────────────────────────────────

describe("pageConfig — return type shape", () => {
  it("getPageConfig('/') returns an object with title, description, and breadcrumbs", () => {
    const config = getPageConfig("/");
    expect(config).toBeTypeOf("object");
    expect(config).not.toBeNull();
    expect(config).toHaveProperty("title");
    expect(config).toHaveProperty("description");
    expect(config).toHaveProperty("breadcrumbs");
  });
});


// ── Explicit-entry invariant (added 2026-09-13, spec …-subscriptions T4.3 FG1) ─

/**
 * Every registry path must have its OWN `ROUTE_MAP` entry — not merely resolve
 * through `FALLBACK_CONFIG`.
 *
 * Why this exists: `/connectivity/available-vehicles` shipped with T3.6 and had
 * no `ROUTE_MAP` entry, so it rendered the generic "Connected Services" banner
 * instead of its own title. Nothing caught it. Every pre-existing guard here
 * asserts a **non-empty title**, and the fallback supplies one — so the guards
 * passed on a screen with the wrong banner. That is the presence-versus-property
 * shape this spec keeps producing: the property is "this path has its own page
 * chrome", and non-emptiness does not test it.
 *
 * `ROUTE_MAP` and `FALLBACK_CONFIG` are both module-private, so this asserts
 * behaviourally: a path with its own entry carries a self-referential
 * breadcrumb (`href === path`); the fallback carries only the root crumb. The
 * two parameterised sub-routes are excluded — they deliberately resolve to
 * their parent's config, whose deepest crumb points at the parent.
 */
describe("pageConfig — every registry path has its own entry, not the fallback", () => {
  const ownEntryPaths = SCREEN_REGISTRY.map((e) => e.path).filter(
    (p) => p !== "/",
  );

  it.each(ownEntryPaths)(
    "getPageConfig(%s) has a breadcrumb pointing at itself",
    (registryPath) => {
      const config = getPageConfig(registryPath);
      const selfCrumb = config.breadcrumbs.find((c) => c.href === registryPath);
      expect(
        selfCrumb,
        `getPageConfig("${registryPath}") resolved to the fallback banner — ` +
          "add an explicit ROUTE_MAP entry for it in pageConfig.ts",
      ).toBeDefined();
    },
  );

  it("no registry path renders the bare fallback title", () => {
    const onFallback = SCREEN_REGISTRY.filter((entry) => {
      if (entry.path === "/") return false;
      const config = getPageConfig(entry.path);
      // The fallback is exactly {title: "Connected Services", description: "",
      // breadcrumbs: [ROOT_CRUMB]} — a one-crumb result is the tell.
      return config.breadcrumbs.length === 1 && config.description === "";
    });
    expect(
      onFallback.map((e) => `${e.id} (${e.path})`),
      "These registry paths have no ROUTE_MAP entry of their own",
    ).toHaveLength(0);
  });
});


// ── Parameterised detail routes, derived from App.tsx ─────────────────────────
//
// The suite above iterates `SCREEN_REGISTRY`, and parameterised detail routes are
// deliberately NOT in the registry — they are reached by a row click, not the sidebar, which
// is why they sit in the `PARAMETERIZED_DETAIL_ONLY_*` allowlists in
// `placeholderInventory.test.ts` and `scaffoldingExpiry.test.ts`. So the registry-driven guard
// structurally could not see them, and `/data-model/data-collection-campaigns/:campaignName`
// shipped to staging with no `getPageConfig` branch: the page title read "Connected Services"
// and the breadcrumb trail was a single root crumb. Found by user UAT, not by any test.
//
// Routes are read out of `App.tsx` rather than listed here, so a route added next month is
// covered without anyone remembering this rule. Listing them would reproduce the original
// defect one file over — a roster that must be updated by hand and was not.
describe("parameterised detail routes have their own page config", () => {
  const APP_TSX = resolve(__dirname, "..", "..", "..", "App.tsx");
  const appSource = readFileSync(APP_TSX, "utf8");

  /**
   * Every `path="/..."` in App.tsx's route table that carries a `:param` segment AND
   * actually renders a page.
   *
   * Redirect-only routes are excluded. `/sales/signals/:signalId` is
   * `element={<LegacyRedirect to="/data-model/signals" />}` — it renders no banner and
   * navigates away, so requiring a page config for it would be demanding a fix for a
   * non-defect. The first version of this guard flagged it, and an over-broad guard that
   * demands unrelated edits is how guards get weakened or deleted (see the same reasoning in
   * `src/__tests__/campaignAssignCallers.test.ts`, where a whole-file match flagged two
   * column renderers that never reach the API).
   *
   * ## Matched in two passes, then reconciled
   *
   * The paired `path=…element={…}` regex below can MISS a route rather than misjudge it: an
   * `element={…}` whose first `}` falls outside the 120-character window, or a route written
   * `element` before `path`, simply does not match and the route drops out of the set
   * silently. Review cycle 1 of Fix Group 9 verified both shapes disappear with all tests
   * green, and noted the `>= 5` floor cannot see it — a floor catches "matched nothing", not
   * "matched 8 of 9".
   *
   * So paths are enumerated INDEPENDENTLY of the element pairing, and the two sets are
   * reconciled by the test below. Anything present in the simple scan but absent from the
   * paired scan is a matcher failure, not a passing route.
   */
  const allParameterisedPaths = [
    ...appSource.matchAll(/path="(\/[^"]*:[^"]*)"/g),
  ].map((m) => m[1]);

  const pairedMatches = [
    ...appSource.matchAll(/path="(\/[^"]*:[^"]*)"\s*\n\s*element=\{([\s\S]{0,120}?)\}/g),
  ];

  const redirectOnlyPaths = new Set(
    pairedMatches
      .filter(([, , element]) => /<(Navigate|LegacyRedirect)\b/.test(element))
      .map(([, p]) => p),
  );

  const parameterisedPaths = allParameterisedPaths.filter(
    (p) => !redirectOnlyPaths.has(p),
  );

  /** Substitute a plausible value for each `:param` so the path is concrete. */
  const concrete = (p: string): string => p.replace(/:[A-Za-z0-9_]+/g, "sample-value");

  it("every route path declaration is in the one shape the scans below can read", () => {
    // W1 (Fix Group 10 review) → F11.1 → W1 again (Fix Group 11 review). The reconciliation
    // further down compares two scans, which only detects a matcher failure if the two can fail
    // INDEPENDENTLY. They cannot: both key off `path="` — double quote, immediately after `=`,
    // no whitespace. A route in any other shape drops out of BOTH sets at once, so
    // `unclassified` is empty, the reconciliation passes vacuously, and every per-route check
    // below silently stops covering that route. It does not fail; it vanishes.
    //
    // F11.1 tried to close this by denylisting `path={…}`. That was too narrow, and its own
    // Accept criterion went unmet: `path = {ROUTES.CAMPAIGN_DETAIL}` (note the spaces) is
    // non-literal and still slipped through, as did `path='/…'` — which the repo-level prettier
    // config actively invites, since `.config/prettier.config.js` sets `singleQuote: true`.
    // Enumerating bad shapes is the wrong move: there is always one more.
    //
    // So reconcile on the ATTRIBUTE instead, one level down from where F10.3 put it. Every
    // `path=` declaration must also match the exact readable shape. That makes the check
    // shape-agnostic and self-maintaining: it subsumes the brace, single-quote, whitespace and
    // constant-extraction cases without naming any of them, and the day someone changes the
    // shape deliberately, this fails and points at the two regexes that must change with it.
    //
    // `\bpath` cannot match inside `filepath`/`subpath` — no word boundary between `e` and `p` —
    // so no anchoring is needed for those.
    const allPathDecls = [...appSource.matchAll(/\bpath\s*=/g)];
    const readablePathDecls = [...appSource.matchAll(/\bpath="[^"]*"/g)];
    expect(
      allPathDecls.length,
      `App.tsx declares ${String(allPathDecls.length)} route paths but only ` +
        `${String(readablePathDecls.length)} are in the shape every scan in this file reads ` +
        '(`path="…"` — double-quoted, no space before the quote). The difference is invisible ' +
        "to the reconciliation below AND to every per-route check: those routes do not fail, " +
        "they vanish. Either restore the readable shape, or update BOTH path regexes in this " +
        "file and this assertion in the same change.",
    ).toBe(readablePathDecls.length);
  });

  it("every parameterised route was classified — the element matcher lost none", () => {
    // The reconciliation. Without it, a route whose `element={…}` the paired regex cannot read
    // is neither checked nor reported: it vanishes, and the suite stays green. That is the
    // failure mode this whole file exists to prevent, one level up.
    const pairedPaths = new Set(pairedMatches.map(([, p]) => p));
    const unclassified = allParameterisedPaths.filter((p) => !pairedPaths.has(p));
    expect(
      unclassified,
      "These parameterised routes were found by the path scan but NOT by the path+element " +
        "scan, so they were never classified as page-rendering or redirect-only and every " +
        "check below skipped them silently. Widen the element window or handle the route's " +
        "shape:\n" + unclassified.map((p) => `  - ${p}`).join("\n"),
    ).toHaveLength(0);
  });

  it("anti-vacuity: App.tsx declares page-rendering parameterised routes for this suite", () => {
    // A floor, kept as a backstop to the reconciliation above. It catches "the route table
    // moved and the regex now matches nothing"; it cannot catch a partial match, which is what
    // the reconciliation is for.
    expect(
      parameterisedPaths.length,
      "No page-rendering parameterised routes found in App.tsx. Either the route table moved " +
        "or the regex no longer matches it — in which case every check below passes vacuously.",
    ).toBeGreaterThanOrEqual(5);
  });

  it.each(parameterisedPaths)(
    "%s does not fall through to the fallback banner",
    (routePath) => {
      const config = getPageConfig(concrete(routePath));
      // The fallback is exactly {title: "Connected Services", description: "",
      // breadcrumbs: [ROOT_CRUMB]}. A one-crumb result with no description is the tell —
      // same detection the registry-driven test above uses.
      const isFallback =
        config.breadcrumbs.length === 1 && config.description === "";
      expect(
        isFallback,
        `getPageConfig("${concrete(routePath)}") returned the fallback: the page banner would ` +
          `read "Connected Services" with a single root breadcrumb. Add a ` +
          `startsWith("${routePath.replace(/:[A-Za-z0-9_]+$/, "")}") branch to getPageConfig.`,
      ).toBe(false);
    },
  );

  it.each(parameterisedPaths)("%s has a non-empty title", (routePath) => {
    expect(getPageConfig(concrete(routePath)).title.trim()).not.toBe("");
  });

  // NOTE: deliberately NOT asserting that the last breadcrumb equals the detail path.
  //
  // `/connectivity/subscriber-lookup/:vin` and `/software/workbench/:signalId` use the
  // one-component pattern — the list and the detail are the SAME screen in a different state,
  // so they reuse the list page's config and its trail correctly ends at the list page.
  // pageConfig.ts's own docstring documents that choice. An assertion requiring the trail to
  // end at the detail path flagged both as failures; they are not defects, and the property
  // that actually matters to a user — "the banner does not say 'Connected Services'" — is
  // covered above.
});
