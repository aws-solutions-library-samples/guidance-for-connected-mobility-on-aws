// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * § D3 property 3 — the `?session=` URL parameter is inert, everywhere.
 *
 * Spec: .kiro/specs/2026-09-05-cms-connected-services-auth-integration/ § D2, D3
 * Security review Cycle 1, Critical 1.
 *
 * This is the regression guard for Talos DAST Critical `69d7f6e6`
 * (`UnauthWebService`): `https://<portal>/?session=anyone:connected-services` granted
 * full portal access to any caller.
 *
 * ── Why this file scans text as well as behaviour ───────────────────────────
 * A behavioural assertion can only observe the reader it calls. The first version of
 * this guard asserted that `getSession()` ignores the parameter — which was true, and
 * passed — while a parser survived in `src/index.tsx`, running before React mounted,
 * writing caller-supplied JSON into `localStorage.__DEMO_SESSION__` and shipping in the
 * built bundle. The reader had moved; the guard followed the reader; the parser was
 * invisible to it. `src/index.tsx` is also in `vite.config.ts`'s coverage `exclude`, so
 * the coverage report pointed nowhere either.
 *
 * "This mechanism exists nowhere in what we ship" is a property of the artifact, not of
 * a function, so it is asserted against the artifact. Both halves are kept: the
 * behavioural check proves the reader is clean, the scan proves no second parser is
 * hiding somewhere the reader never touches.
 * ───────────────────────────────────────────────────────────────────────────
 */

import { readFileSync, existsSync, readdirSync, statSync } from "fs";
import path from "path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { CONNECTED_SERVICES_GROUP, getSession } from "../auth";

const SRC_DIR = path.resolve(__dirname, "..");
const DIST_ASSETS = path.resolve(__dirname, "../../dist/assets");

/** This file legitimately contains the forbidden strings; so do the other guards. */
const SCAN_EXEMPT_BASENAMES = new Set([
  "noUrlParamSession.test.ts",
  "authFailClosed.test.tsx",
  "registryCompleteness.test.tsx",
  "authHardening.test.ts",
  "index.tsx", // retains a comment recording the deletion — checked separately below
  "App.tsx", // strips the param; names it in FORBIDDEN_QUERY_PARAM
  "auth.ts", // header documents the reversal
]);

/** Recursively collect .ts/.tsx files under `dir`. */
function collectSources(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...collectSources(full));
    } else if (/\.tsx?$/.test(entry)) {
      out.push(full);
    }
  }
  return out;
}

beforeEach(() => {
  window.history.replaceState({}, "", "/");
  window.localStorage.clear();
  window.sessionStorage.clear();
});

afterEach(() => {
  window.history.replaceState({}, "", "/");
  window.localStorage.clear();
  window.sessionStorage.clear();
});

// ---------------------------------------------------------------------------
// Behavioural half — the reader ignores the parameter
// ---------------------------------------------------------------------------

describe("behaviour — ?session= produces no session", () => {
  it("getSession() returns null with the attack URL present", () => {
    window.history.replaceState({}, "", `/?session=probe:${CONNECTED_SERVICES_GROUP}`);
    expect(getSession()).toBeNull();
  });

  it("reading with the attack URL present writes nothing to localStorage", () => {
    window.history.replaceState({}, "", `/?session=probe:${CONNECTED_SERVICES_GROUP}`);
    getSession();
    expect(Object.keys(window.localStorage)).toHaveLength(0);
  });

  it("a pre-planted __DEMO_SESSION__ is not honoured", () => {
    // Callers already bypassed before the fix must not remain authorized.
    window.localStorage.setItem(
      "__DEMO_SESSION__",
      JSON.stringify({ alias: "probe", groups: [CONNECTED_SERVICES_GROUP] })
    );
    expect(getSession()).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Artifact half — no parser survives anywhere in src/
// ---------------------------------------------------------------------------

describe("source scan — no module parses a session from the URL", () => {
  const files = collectSources(SRC_DIR);

  it("scanned a non-trivial number of source files", () => {
    // Anti-vacuity: an empty or tiny scan would make every assertion below pass.
    expect(
      files.length,
      "the source scan collected almost nothing — the scan is vacuous"
    ).toBeGreaterThan(50);
  });

  it("no module reads a 'session' search parameter", () => {
    const offenders: string[] = [];
    for (const file of files) {
      if (SCAN_EXEMPT_BASENAMES.has(path.basename(file))) continue;
      const text = readFileSync(file, "utf8");
      // The shape of the deleted mechanism: pulling "session" out of the query string.
      if (/\.get\(\s*["'`]session["'`]\s*\)/.test(text)) {
        offenders.push(path.relative(SRC_DIR, file));
      }
    }
    expect(
      offenders,
      "these modules read a 'session' search parameter — that is the Talos-flagged " +
        `mechanism (69d7f6e6): ${offenders.join(", ")}`
    ).toEqual([]);
  });

  it("no module writes the __DEMO_SESSION__ storage key", () => {
    const offenders: string[] = [];
    for (const file of files) {
      if (SCAN_EXEMPT_BASENAMES.has(path.basename(file))) continue;
      const text = readFileSync(file, "utf8");
      if (/setItem\(\s*["'`]__DEMO_SESSION__["'`]/.test(text)) {
        offenders.push(path.relative(SRC_DIR, file));
      }
    }
    expect(offenders).toEqual([]);
  });

  it("the entry point retains no executable session bootstrap", () => {
    // index.tsx documents the deletion in prose, and that prose necessarily names the
    // things it forbids. Comments are stripped before asserting so the record can stay
    // specific without tripping the check that reads it — the same trap as a denylist
    // written in terms of the value it guards.
    const raw = readFileSync(path.join(SRC_DIR, "index.tsx"), "utf8");
    const code = raw
      .replace(/\/\*[\s\S]*?\*\//g, "") // block comments
      .replace(/^\s*\/\/.*$/gm, ""); // line comments

    expect(code).not.toMatch(/setItem\(/);
    expect(code).not.toMatch(/URLSearchParams/);
    expect(code).not.toMatch(/localStorage/);

    // Anti-vacuity: prove the stripper left real code behind to assert against.
    expect(
      code,
      "comment-stripping removed everything — the assertions above are vacuous"
    ).toMatch(/createRoot/);
  });
});

// ---------------------------------------------------------------------------
// Built bundle — what actually ships
// ---------------------------------------------------------------------------

describe("bundle scan — the shipped artifact carries no session bootstrap", () => {
  it("no dist asset contains the __DEMO_SESSION__ key or a session-param parser", () => {
    if (!existsSync(DIST_ASSETS)) {
      // No build present. The source scan above already proves the property for the
      // code that would be built, so this still asserts something real rather than
      // silently skipping — but it cannot speak for a stale artifact on disk.
      expect(
        existsSync(SRC_DIR),
        "neither a build nor a source tree was found — the scan is vacuous"
      ).toBe(true);
      return;
    }

    const offenders: string[] = [];
    for (const entry of readdirSync(DIST_ASSETS)) {
      if (!entry.endsWith(".js")) continue;
      const text = readFileSync(path.join(DIST_ASSETS, entry), "utf8");
      if (text.includes("__DEMO_SESSION__") || text.includes("demo session bootstrapped")) {
        offenders.push(entry);
      }
    }
    expect(
      offenders,
      "the built bundle still contains the demo-session bootstrap. This is what a " +
        "DAST scan reaches. Rebuild (`npm run build`) after deleting it, and do not " +
        `re-enable the distribution until this is empty. Offending assets: ${offenders.join(", ")}`
    ).toEqual([]);
  });
});
