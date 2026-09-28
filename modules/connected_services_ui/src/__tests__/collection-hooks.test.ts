// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * collection-hooks.test.ts — dependency-presence guard for T1.3.
 *
 * Purpose: `@cloudscape-design/collection-hooks` was promoted from a transitive
 * dependency of `@cloudscape-design/components` to a direct, exact pin. This guard
 * fails if that link breaks — e.g. a future `npm install` that drops the direct
 * entry, leaving the import resolvable only by hoisting accident.
 *
 * ## Why this file was rewritten (2026-09-04)
 *
 * The first version of this guard asserted three exports —
 * `useCollection`, `useCollectionPagination`, `useAsyncCollection` — and that
 * `typeof useCollection === "string"`.
 *
 * The package exports exactly ONE symbol: `useCollection`. The other two do not
 * exist at any version, and `useCollection` is a function, not a string. All three
 * assertions were unsatisfiable, and the file used `require()` inside a
 * `"type": "module"` package, so every case failed on the import rather than on
 * the assertion.
 *
 * That matters beyond the wasted red: T1.3 AC3 wires `npm run build` to run the
 * guard suite. Had the build-gate been wired while this file was present, the
 * build would have been permanently broken by a test asserting an API that was
 * never real — and the failure would have looked like a dependency problem.
 *
 * The lesson is the one in the spec's own T1.1 constraint: assert against the
 * installed package, not against a remembered API surface. The real export list
 * is documented in this module's `docs/tech.md` § (a), cited to
 * `node_modules/@cloudscape-design/collection-hooks/mjs/use-collection.d.ts`.
 */

import { describe, it, expect } from "vitest";
import * as collectionHooks from "@cloudscape-design/collection-hooks";

describe("collection-hooks dependency guard", () => {
  it("resolves as a direct dependency and exports useCollection as a function", () => {
    expect(typeof collectionHooks.useCollection).toBe("function");
  });

  it("exports exactly the surface docs/tech.md documents", () => {
    // Pinned deliberately: if a version bump adds or removes an export, this
    // fails and docs/tech.md § (a) gets re-verified against the new package
    // rather than silently drifting.
    expect(Object.keys(collectionHooks).sort()).toEqual(["useCollection"]);
  });
});
