// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * App.routes.test.tsx — static Navigate/Route consistency lint.
 *
 * Regression guard authored under spec `2026-09-14-cms-fleet-lifecycle-view`
 * T1.7 in response to `issues/2026-09-10-fleet-intelligence-lifecycle-redirect-target-has-no-route/`:
 * `<Navigate to="/fleet-intelligence/lifecycle" replace />` at App.tsx:1361
 * had no matching `<Route path=... />`. Every fresh visit to `/` hit a 404.
 *
 * ## Scope
 *
 * Reads `App.tsx` as a string. For every literal `<Navigate to="X" replace />`
 * occurrence, asserts that a matching `<Route path=... />` exists — either
 * as a literal `<Route path="X" ...>` or as `<Route path={UI_ROUTES.KEY} ...>`
 * where `UI_ROUTES.KEY === X` (resolved via static import of
 * `./utils/constants`).
 *
 * ## Known scope limits (documented, not asserted)
 *
 * - Extracts literal-string `<Navigate to=...>` only. Variable/expression
 *   forms (e.g. `<Navigate to={someVar}>`) are NOT asserted — they are
 *   known false-negatives.
 * - Extracts literal-string OR `UI_ROUTES.SOMETHING` `<Route path=...>`.
 *   Other computed forms (`path={base + '/x'}`) are known false-negatives.
 * - Ignores `<Link to=...>`, `history.push()`, `navigate()`, and other
 *   navigation primitives. Extending to those is a separate lint scope,
 *   filed as a follow-on.
 * - Only one file is scanned (`App.tsx`). Nested route configs in other
 *   files would need explicit inclusion here.
 *
 * The lint over-collects on Navigate (all literal Navigates asserted) and
 * under-collects on Route (variable-expression paths not evaluated). That
 * bias is intentional: false negatives are acceptable; the failure mode
 * we're guarding against (Navigate → nowhere) is only surfaced by
 * over-collecting on the Navigate side.
 */

import { readFileSync } from "fs";
import { resolve } from "path";
import { describe, it, expect } from "vitest";
import { UI_ROUTES } from "./utils/constants";

const APP_TSX_PATH = resolve(__dirname, "./App.tsx");

/** Extract every literal Navigate target from App.tsx source. */
function extractNavigateTargets(source: string): string[] {
  // Matches: <Navigate to="/path" ... /> — literal-string form only.
  const re = /<Navigate\s+to="([^"]+)"/g;
  const targets: string[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(source)) !== null) {
    targets.push(m[1]);
  }
  return targets;
}

/**
 * Extract every Route path from App.tsx source, resolving UI_ROUTES.KEY
 * references to their literal values.
 */
function extractRoutePaths(source: string): Set<string> {
  const paths = new Set<string>();

  // Form 1: <Route path="/literal" ...>
  const literalRe = /<Route\s+path="([^"]+)"/g;
  let m: RegExpExecArray | null;
  while ((m = literalRe.exec(source)) !== null) {
    paths.add(m[1]);
  }

  // Form 2: <Route path={UI_ROUTES.KEY} ...>
  const routesRe = /<Route\s+path=\{UI_ROUTES\.([A-Z_][A-Z0-9_]*)\}/g;
  while ((m = routesRe.exec(source)) !== null) {
    const key = m[1] as keyof typeof UI_ROUTES;
    const resolved = UI_ROUTES[key];
    if (typeof resolved === "string") {
      paths.add(resolved);
    }
  }

  return paths;
}

describe("App.tsx Navigate/Route consistency", () => {
  it("extraction functions find non-zero matches", () => {
    // Sanity check: if the regexes silently match nothing, every assertion
    // below passes vacuously. Pin non-emptiness so a broken regex is loud.
    const source = readFileSync(APP_TSX_PATH, "utf8");
    const navigates = extractNavigateTargets(source);
    const routes = extractRoutePaths(source);
    expect(navigates.length).toBeGreaterThan(0);
    expect(routes.size).toBeGreaterThan(5);
  });

  it("every_Navigate_to_has_matching_Route_path", () => {
    const source = readFileSync(APP_TSX_PATH, "utf8");
    const navigates = extractNavigateTargets(source);
    const routes = extractRoutePaths(source);

    const orphans = navigates.filter((target) => !routes.has(target));

    // Descriptive message names the missing target so debugging is one
    // read of the failure message, not a re-derivation of what the lint
    // is checking.
    expect(orphans, `Navigate targets with no matching Route path in App.tsx: ${JSON.stringify(orphans)}. Fix: add a <Route path="..." element={...} /> for each, OR change the <Navigate to="..."> to point at an existing route.`).toEqual([]);
  });

  it("mutation gate — injecting a fake orphan Navigate makes the lint fail", () => {
    // Verify the lint asserts the property, not just the current state.
    // A future refactor that silently narrows the extractor would let real
    // defects through; this test catches the extractor's mutation surface.
    const source = readFileSync(APP_TSX_PATH, "utf8");
    const mutated = source + '\n<Navigate to="/does-not-exist-anywhere" replace />';
    const navigates = extractNavigateTargets(mutated);
    const routes = extractRoutePaths(mutated);
    const orphans = navigates.filter((target) => !routes.has(target));
    expect(orphans).toContain("/does-not-exist-anywhere");
  });
});
