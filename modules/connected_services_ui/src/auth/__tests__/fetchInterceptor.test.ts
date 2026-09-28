// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * CS fetchInterceptor — auth-stamping properties.
 *
 * Spec: `.kiro/specs/2026-09-15-cms-cs-campaign-ownership/tasks.md` FG10.1, FG11.1, FG11.2, FG11.5
 *
 * ## Testing approach
 *
 * `installSimulationFetchInterceptor(mockFetch)` is called in each test with a
 * fresh mock. This installs the interceptor against the mock, then the test calls
 * `window.fetch` (the interceptor) and asserts on what was passed to mockFetch.
 *
 * After each test, `window.fetch` is restored to a no-op so tests don't interfere.
 * Tests never import `index.tsx` — that would mount the React app.
 *
 * ## Mutation coverage (FG10.1 + FG11.1 + FG11.2 + FG11.5 Verify)
 *
 * (a) simulationApiEndpoint removed from base check → stamping tests FAIL
 * (b) interceptor import removed from index.tsx → stamping tests FAIL
 * (c) interceptor overwrites existing Authorization → no-clobber tests FAIL
 * (d) interceptor stamps every URL (no base check) → scope-guard tests FAIL
 * (e) new *ApiEndpoint added to interface + stack without AUTH_MECHANISM entry
 *     → the guard in section 6 FAILS (FG11.1 mutation (a))
 * (f) simulationApiEndpoint classification changed to "explicit-client" while
 *     the interceptor actually stamps it → the direction-check test FAILS (FG11.1 mutation (d))
 * (g) revert matchesBase to url.startsWith(base) → the superstring-host tests FAIL
 *     (FG11.2 mutation (a)). Verified in decisions.md.
 * (h) configure simulationApiEndpoint as " https://…" (one leading space) →
 *     a request to the actual base receives no Authorization (FG11.5/1 mutation).
 *     Verified in decisions.md.
 * All mutations verified in decisions.md, FG11.1 section (a–f) and
 * decisions.md (g–h).
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installSimulationFetchInterceptor } from "../fetchInterceptor";
import { STORAGE_KEY_ID_TOKEN } from "../../auth";

// ── Constants ─────────────────────────────────────────────────────────────────

const SIM_BASE = "https://simulation.example.invalid";
const OTHER_BASE = "https://third-party.example.invalid";
const ID_TOKEN = "ID.TOKEN.test-bearer";
const EXPLICIT_TOKEN = "Bearer EXPLICIT.TOKEN.already-present";

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Create a fresh mock fetch that returns a minimal ok Response. */
function makeMockFetch() {
  return vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit): Promise<Response> => {
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({}),
      headers: new Headers(),
    } as unknown as Response;
  });
}

/**
 * Extract the Authorization header from the first mock call.
 * Handles both plain-object headers and Headers instances.
 */
function getAuthHeaderFromCall(
  mock: ReturnType<typeof makeMockFetch>,
  callIndex = 0,
): string | null {
  const init = mock.mock.calls[callIndex]?.[1] as RequestInit | undefined;
  if (!init?.headers) return null;
  if (init.headers instanceof Headers) {
    return init.headers.get("Authorization");
  }
  return (init.headers as Record<string, string>)["Authorization"] ?? null;
}

// ── Setup / teardown ──────────────────────────────────────────────────────────

const savedFetch = window.fetch;

beforeEach(() => {
  window.runtimeConfig = {
    cognitoUserPoolId: "<pool-id>",
    cognitoClientId: "testclientid00000001",
    cognitoDomain: "portal.example.invalid",
    connectedServicesApiEndpoint: "https://api.example.invalid",
    subscriptionsApiEndpoint: "https://subscriptions.example.invalid",
    simulationApiEndpoint: SIM_BASE,
    simulationProductRuleName: "",
    dataProcessingApiEndpoint: "https://data-processing.example.invalid",
    callbackOrigin: "https://connected-services.example.invalid",
  };
  sessionStorage.clear();
  sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, ID_TOKEN);
});

afterEach(() => {
  // Restore window.fetch so each test starts clean.
  window.fetch = savedFetch;
  delete window.runtimeConfig;
  sessionStorage.clear();
  vi.restoreAllMocks();
});

// ── 1. Simulation-base stamping ───────────────────────────────────────────────
// Mutation (a): remove simulationApiEndpoint from the base check
//   → the interceptor does nothing; Authorization header is absent → FAIL.
// Mutation (b): remove the interceptor import from index.tsx
//   → interceptor never installed, window.fetch is plain fetch → FAIL
//   (verified separately by running the CS suite with the import removed).

describe("CS fetchInterceptor — simulation base is auth-stamped", () => {
  it("adds Authorization: Bearer <idToken> to a request to simulationApiEndpoint", async () => {
    const mock = makeMockFetch();
    installSimulationFetchInterceptor(mock);
    await window.fetch(`${SIM_BASE}/api/simulation/agent/logs/TESTVIN`);
    expect(getAuthHeaderFromCall(mock)).toBe(`Bearer ${ID_TOKEN}`);
  });

  it("stamps a request to the simulation agent/status route", async () => {
    const mock = makeMockFetch();
    installSimulationFetchInterceptor(mock);
    await window.fetch(`${SIM_BASE}/api/simulation/agent/status`);
    expect(getAuthHeaderFromCall(mock)).toBe(`Bearer ${ID_TOKEN}`);
  });

  it("stamps a request to the simulation start route (POST with body)", async () => {
    const mock = makeMockFetch();
    installSimulationFetchInterceptor(mock);
    await window.fetch(`${SIM_BASE}/api/simulation/start`, {
      method: "POST",
      body: JSON.stringify({ vehicles: ["VEH-0001"] }),
    });
    expect(getAuthHeaderFromCall(mock)).toBe(`Bearer ${ID_TOKEN}`);
  });
});

// ── 2. No-clobber: explicit Authorization header is preserved ─────────────────
// Mutation (c): remove the `!headers.has("Authorization")` guard
//   → explicit token is overwritten with the idToken → FAIL.

describe("CS fetchInterceptor — explicit Authorization header is NOT overwritten", () => {
  it("does not replace an already-present Authorization header (plain object)", async () => {
    const mock = makeMockFetch();
    installSimulationFetchInterceptor(mock);
    await window.fetch(`${SIM_BASE}/api/simulation/start`, {
      method: "POST",
      headers: { Authorization: EXPLICIT_TOKEN },
    });
    expect(getAuthHeaderFromCall(mock)).toBe(EXPLICIT_TOKEN);
  });

  it("does not replace an already-present Authorization header (Headers instance)", async () => {
    const mock = makeMockFetch();
    installSimulationFetchInterceptor(mock);
    const h = new Headers();
    h.set("Authorization", EXPLICIT_TOKEN);
    await window.fetch(`${SIM_BASE}/api/simulation/list`, { headers: h });
    expect(getAuthHeaderFromCall(mock)).toBe(EXPLICIT_TOKEN);
  });
});

// ── 3. Scope: non-simulation URLs are NOT stamped ─────────────────────────────
// Mutation (d): remove the `url.startsWith(simulationBase)` check (stamp every URL)
//   → third-party host receives the ID token → FAIL (security property).

describe("CS fetchInterceptor — only the simulation base is stamped (scope guard)", () => {
  it("does NOT add Authorization to a request to a different/third-party host", async () => {
    const mock = makeMockFetch();
    installSimulationFetchInterceptor(mock);
    await window.fetch(`${OTHER_BASE}/some-endpoint`);
    expect(getAuthHeaderFromCall(mock)).toBeNull();
  });

  it("does NOT add Authorization to a request to connectedServicesApiEndpoint", async () => {
    const mock = makeMockFetch();
    installSimulationFetchInterceptor(mock);
    await window.fetch("https://api.example.invalid/v1/resource");
    expect(getAuthHeaderFromCall(mock)).toBeNull();
  });

  it("does NOT add Authorization to a relative URL on the SPA's own origin", async () => {
    const mock = makeMockFetch();
    installSimulationFetchInterceptor(mock);
    await window.fetch("/api/some-internal-route");
    expect(getAuthHeaderFromCall(mock)).toBeNull();
  });
});

// ── 4. Empty-base guard ───────────────────────────────────────────────────────
// When simulationApiEndpoint is "" the interceptor must NOT stamp ANY URL.
// The always-truthy empty-string bug: `"".startsWith("")` is true for every URL;
// CMS's own comment records that the empty-string variant of this bug shipped once.

describe("CS fetchInterceptor — empty simulationApiEndpoint does not stamp any URL", () => {
  it("does NOT stamp when simulationApiEndpoint is empty string", async () => {
    window.runtimeConfig = {
      ...window.runtimeConfig!,
      simulationApiEndpoint: "",
    };
    const mock = makeMockFetch();
    installSimulationFetchInterceptor(mock);
    // Even though the URL looks like a simulation URL, the empty base means nothing matches.
    await window.fetch(`${SIM_BASE}/api/simulation/agent/status`);
    expect(getAuthHeaderFromCall(mock)).toBeNull();
  });

  it("does NOT stamp when runtimeConfig is absent", async () => {
    delete window.runtimeConfig;
    const mock = makeMockFetch();
    installSimulationFetchInterceptor(mock);
    await window.fetch(`${SIM_BASE}/api/simulation/agent/status`);
    expect(getAuthHeaderFromCall(mock)).toBeNull();
  });
});

// ── 5. No-token degradation ───────────────────────────────────────────────────

describe("CS fetchInterceptor — no Authorization header when ID token is absent", () => {
  it("does NOT add Authorization when sessionStorage has no idToken", async () => {
    sessionStorage.clear();
    const mock = makeMockFetch();
    installSimulationFetchInterceptor(mock);
    await window.fetch(`${SIM_BASE}/api/simulation/agent/status`);
    expect(getAuthHeaderFromCall(mock)).toBeNull();
  });
});

// ── 6. Origin/path boundary — superstring host and malformed URLs (FG11.2) ────
//
// FG11.2 finding: url.startsWith(base) is a string-prefix test, not an origin
// test. `"https://simulation.example.invalid"` also prefix-matches
// `"https://simulation.example.invalid.attacker.test/steal"`. matchesBase()
// parses both URLs and compares origins structurally.
//
// Mutation (g) / FG11.2(a): revert matchesBase to url.startsWith(base) →
//   the superstring-host test FAILS. Verified in decisions.md.
//
// Note: not reachable today — every URL at these call sites is built from
// runtimeConfig plus a literal path, so no attacker-controlled URL flows in.

describe("CS fetchInterceptor — superstring host receives no Authorization (FG11.2)", () => {
  it("does NOT stamp a URL whose host begins with the simulation base host", async () => {
    // A bare startsWith check would stamp this. The origin/path-boundary check must not.
    const attackerUrl = `${SIM_BASE}.attacker.test/steal`;
    const mock = makeMockFetch();
    installSimulationFetchInterceptor(mock);
    await window.fetch(attackerUrl);
    expect(getAuthHeaderFromCall(mock)).toBeNull();
  });
});

describe("CS fetchInterceptor — malformed and relative URLs do not throw and are not stamped (FG11.2)", () => {
  it("does NOT throw and does NOT stamp a relative URL", async () => {
    const mock = makeMockFetch();
    installSimulationFetchInterceptor(mock);
    await expect(window.fetch("/relative/path")).resolves.toBeDefined();
    expect(getAuthHeaderFromCall(mock)).toBeNull();
  });

  it("does NOT throw and does NOT stamp a malformed URL", async () => {
    const mock = makeMockFetch();
    installSimulationFetchInterceptor(mock);
    await expect(window.fetch("not-a-url")).resolves.toBeDefined();
    expect(getAuthHeaderFromCall(mock)).toBeNull();
  });
});

// ── 6b. Normalizer consistency — FG11.5 item 1 ───────────────────────────────
//
// FG11.5/1 finding: the CS interceptor previously read rc.simulationApiEndpoint
// raw while getSimulationApiBase() (simulationClient.ts) trims whitespace and
// strips trailing slashes. A base with one leading space leaves the client
// building matching URLs while the interceptor stamps nothing — a silent 401
// with no signal. The fix: the interceptor now calls getSimulationApiBase()
// so both code paths share the same normalizer.
//
// Mutation (h) / FG11.5(1): set simulationApiEndpoint to " https://simulation…"
//   (one leading space) → without normalization the interceptor stamps nothing
//   → the stamp test FAILS. With normalization → PASSES.
//   Verified in decisions.md.

describe("CS fetchInterceptor — normalizer consistency: leading-space base is trimmed (FG11.5/1)", () => {
  it("stamps a request when simulationApiEndpoint has a leading space (normalizer applied)", async () => {
    // getSimulationApiBase() trims whitespace before building the base;
    // the interceptor must use the same normalizer or it will silently fail to stamp.
    window.runtimeConfig = {
      ...window.runtimeConfig!,
      simulationApiEndpoint: ` ${SIM_BASE}`,
    };
    const mock = makeMockFetch();
    installSimulationFetchInterceptor(mock);
    await window.fetch(`${SIM_BASE}/api/simulation/agent/status`);
    expect(getAuthHeaderFromCall(mock)).toBe(`Bearer ${ID_TOKEN}`);
  });
});

// ── 7. Guard: every CS API base has a declared auth mechanism ─────────────────
//
// FG11.1 CRITICAL fix: derive the list of *Endpoint keys from the stack's actual
// runtime-config emission (connected_services_ui_stack.py), not from an object
// literal in this file. An object literal that only iterates itself is circular:
// adding a new endpoint to the interface AND the stack leaves the test green even
// though the new base has no declared auth mechanism.
//
// The direction that matters: is every real base classified?
//   emitted *Endpoint keys → must each have an AUTH_MECHANISM entry
//
// Parse failure is fail-closed: if the regex stops matching the stack source,
// the corpus-sanity test fails loudly rather than silently classifying zero bases.
// That is the vacuity failure that testing.md names and this spec has shipped twice.
//
// Classification reasons (per-base, not a boolean):
//   interceptor        — this module stamps it (url.startsWith check)
//   explicit-client    — a named client attaches its own Authorization header
//   not-http           — not an HTTP/HTTPS endpoint (e.g. WebSocket, raw protocol)
//   alias-of:<key>     — delivers the same URL value as another named key
//   public             — no Cognito authorizer; state which routes and how checked
//
// Mutation (e) / FG11.1(a): adding a new *ApiEndpoint to both the RuntimeConfig
//   interface AND the stack's emission, with no AUTH_MECHANISM entry, must cause
//   this guard to FAIL. Before FG11.1 it passed 15/15. After FG11.1 it FAILS.
//   Verified in decisions.md, FG11.1 section.
//
// Mutation (f) / FG11.1(d): marking simulationApiEndpoint as "explicit-client"
//   while the interceptor actually stamps it → the direction-check test FAILS.

const STACK_PY = resolve(
  __dirname,
  "../../../../../deployment/stacks/connected_services_ui_stack.py",
);

/**
 * Extract API base keys from the CS stack's runtime-config.js emission string.
 *
 * The CS stack builds runtime-config.js as a Python concatenated string of lines:
 *   '  "simulationApiEndpoint": "{simulation_api_endpoint}",\n'
 * This regex captures the JSON key from each such line, restricted to keys that
 * end in "Endpoint" (case-sensitive).
 *
 * Fail-closed: throws if the corpus is empty, so a silently-changed format does
 * not make every comparison vacuously true.
 */
function parseStackEndpointKeys(source: string): string[] {
  // Matches: '  "key": "{placeholder}",' where key ends in Endpoint
  const keyRe = /'\s*"([A-Za-z_$][\w$]*Endpoint)"\s*:\s*"\{[^}]*\}"/g;
  const keys: string[] = [];
  let m: RegExpExecArray | null;
  while ((m = keyRe.exec(source)) !== null) {
    keys.push(m[1]);
  }
  return keys;
}

const stackSource = readFileSync(STACK_PY, "utf8");
const stackEndpointKeys = parseStackEndpointKeys(stackSource);

/**
 * Auth mechanism classifications for every *Endpoint key the CS stack emits.
 *
 * Classification reasons:
 *   interceptor        — this module stamps it via url.startsWith check
 *   explicit-client    — a named client in src/api/ attaches its own header
 *
 * Checked against deployment/stacks/connected_services_ui_stack.py at test time.
 * To classify a new key: run `aws apigateway get-resources --rest-api-id <id>
 * --region us-west-2 --embed methods` and record observed authorizationType.
 */
const AUTH_MECHANISM: Record<string, { mechanism: string; reason: string }> = {
  simulationApiEndpoint: {
    mechanism: "interceptor",
    reason:
      "All routes are COGNITO_USER_POOLS (confirmed against live API tekm196qb5, " +
      "observed 401 unauthenticated 2026-09-15). This module stamps it via " +
      "url.startsWith(simulationBase).",
  },
  connectedServicesApiEndpoint: {
    mechanism: "explicit-client",
    reason:
      "dataModelClient (src/api/dataModelClient.ts) attaches Authorization: Bearer " +
      "<idToken> explicitly. Interceptor must not overwrite it.",
  },
  subscriptionsApiEndpoint: {
    mechanism: "explicit-client",
    reason:
      "subscriptionsClient (src/api/subscriptionsClient.ts) attaches Authorization: " +
      "Bearer <idToken> explicitly. Interceptor must not overwrite it.",
  },
  dataProcessingApiEndpoint: {
    mechanism: "explicit-client",
    reason:
      "dataModelClient also sends requests to this endpoint and attaches Authorization " +
      "explicitly. Same explicit-client pattern as connectedServicesApiEndpoint.",
  },
};

describe("CS fetchInterceptor — every runtimeConfig *Endpoint base has a declared auth mechanism", () => {
  // Corpus sanity: fail loudly if the regex stopped matching.
  // An empty corpus makes every subsequent comparison vacuously true.
  it("parsed a non-trivial number of *Endpoint keys from the stack source (fail-closed guard)", () => {
    expect(
      stackEndpointKeys.length,
      `parseStackEndpointKeys returned 0 keys from ${STACK_PY} — ` +
        "the regex no longer matches the stack's runtime-config.js emission format. " +
        "Fix the regex in this file before the corpus-derived assertions below are meaningful.",
    ).toBeGreaterThanOrEqual(3);
  });

  it("every stack-emitted *Endpoint key is classified in AUTH_MECHANISM", () => {
    // Direction: stack emission → AUTH_MECHANISM
    // Mutation (e) / FG11.1(a): a new unclassified key added to the stack fails here.
    const unclassified = stackEndpointKeys.filter((key) => !(key in AUTH_MECHANISM));
    expect(
      unclassified,
      `Stack emits ${JSON.stringify(unclassified)} but they have no AUTH_MECHANISM entry. ` +
        "Add a classification with a reason for each.",
    ).toEqual([]);
  });

  it("every AUTH_MECHANISM key is actually emitted by the stack (no phantom entries)", () => {
    // Direction: AUTH_MECHANISM → stack emission
    // Catches stale entries left behind after a stack key is removed or renamed.
    const phantom = Object.keys(AUTH_MECHANISM).filter(
      (key) => !stackEndpointKeys.includes(key),
    );
    expect(
      phantom,
      `AUTH_MECHANISM contains ${JSON.stringify(phantom)} which are not emitted by the stack. ` +
        "Remove or rename the stale entries.",
    ).toEqual([]);
  });

  // Mutation (f) / FG11.1(d): marking simulationApiEndpoint as something other than
  // "interceptor" while the interceptor actually stamps it → FAILS here.
  it("simulationApiEndpoint is classified as 'interceptor' (not missed, not mis-classified)", () => {
    expect(AUTH_MECHANISM.simulationApiEndpoint?.mechanism).toBe("interceptor");
  });

  it("only simulationApiEndpoint is in the 'interceptor' category", () => {
    const interceptorBases = Object.entries(AUTH_MECHANISM)
      .filter(([, v]) => v.mechanism === "interceptor")
      .map(([k]) => k);
    expect(interceptorBases).toEqual(["simulationApiEndpoint"]);
  });

  it("every classification has a non-empty reason", () => {
    // A classification without a reason is a comment in test clothing.
    for (const [key, val] of Object.entries(AUTH_MECHANISM)) {
      expect(
        val.reason.length,
        `AUTH_MECHANISM["${key}"].reason is empty — state why this mechanism applies.`,
      ).toBeGreaterThan(0);
    }
  });
});


// ── 8. index.tsx installs the interceptor (mutation-b guard) ──────────────────
// Mutation (b): remove `import "./auth/fetchInterceptor"` from index.tsx
//   → this structural test FAILS.
//
// The interceptor must be installed before any component mounts so all fetch
// calls within the app — including those in `FWELogViewer` — are covered.

describe("CS fetchInterceptor — index.tsx installs the interceptor", () => {
  it("index.tsx contains the fetchInterceptor side-effect import", () => {
    // We read the source file via the Node fs module rather than importing it
    // (importing index.tsx would mount the React app and cause side effects).
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const fs = require("fs") as typeof import("fs");
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const path = require("path") as typeof import("path");
    const indexPath = path.resolve(__dirname, "../../index.tsx");
    const indexSrc = fs.readFileSync(indexPath, "utf8");
    // The import must be present as a side-effect import (no binding).
    // Accepts both double and single quotes.
    expect(
      /import\s+['"]\.\/auth\/fetchInterceptor['"]\s*;/.test(indexSrc),
      'index.tsx must contain `import "./auth/fetchInterceptor"` to install the interceptor before app mount',
    ).toBe(true);
  });
});
