// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Fetch interceptor that auto-injects the Cognito ID token for calls to the
 * simulation REST API.
 *
 * ── WHY THIS EXISTS ──────────────────────────────────────────────────────────
 * The simulation API (`simulationApiEndpoint`) is protected by a Cognito
 * authorizer (`authorizationType: COGNITO_USER_POOLS`, confirmed against
 * live API `tekm196qb5`). A bare GET/POST without an Authorization header
 * returns HTTP 401. Commit 2e8c200d (2026-06-11, CMS repository) introduced
 * the authorizer, with no paired frontend change. See
 * `issues/2026-09-15-cms-simulation-api-401-since-authorizer-added/`.
 *
 * ── SCOPE ────────────────────────────────────────────────────────────────────
 * This interceptor stamps Authorization ONLY on requests whose URL shares the
 * same origin as `runtimeConfig.simulationApiEndpoint` AND whose path begins
 * with the configured base's path — `matchesBase()` below. It MUST NOT be
 * widened to any other base: the connected-services portal's other clients
 * (dataModelClient, subscriptionsClient, simulationClient) attach `Authorization`
 * explicitly, and this interceptor must not overwrite a header that is already
 * present — explicit clients win.
 *
 * A pure string-prefix test (`url.startsWith(base)`) is not sufficient because
 * a base like `https://simulation.example.invalid` (no trailing slash) also
 * matches `https://simulation.example.invalid.attacker.test/steal`. The
 * `matchesBase()` guard compares parsed origins and requires that the pathname
 * match falls on a `/` boundary — not reachable today (every URL at these call
 * sites is built from runtimeConfig plus a literal path, so no attacker-controlled
 * URL flows in), but the docstring claim must be accurate and the test must catch
 * the bypass.
 *
 * ── HEADER SHAPE DIVERGENCE ──────────────────────────────────────────────────
 * This interceptor sends `Authorization: Bearer <idToken>` (prefixed form).
 * CMS's own interceptor (`cms_ui/…/auth/fetchInterceptor.ts`) sends the raw
 * ID token string with no prefix. Both target Cognito-authorized API Gateway
 * routes; both forms are currently in production use.
 *
 * Evidence: `simulationClient.ts:18-21` documents a measured HTTP 200 from
 * live API `tekm196qb5` with the Bearer-prefixed ID token. The raw form used
 * by CMS's interceptor has no corresponding measured 200 against this specific
 * base. Do NOT harmonize to one form without a live two-token probe first — an
 * unverified header-shape change against a Cognito authorizer means a silent 401.
 * See Task 7.3(c) to capture both observations while a token is in hand.
 *
 * ── TOKEN SOURCE ─────────────────────────────────────────────────────────────
 * Reads `sessionStorage["idToken"]` via `STORAGE_KEY_ID_TOKEN` — the same key
 * `simulationClient.ts` uses and that `auth.ts` writes on sign-in. Using a
 * second key would create a second auth surface; the client's own docstring
 * records a measured 401 behind the id-vs-access-token choice (ID token → 200,
 * access token → 401 on `tekm196qb5`).
 *
 * ── INSTALLATION ─────────────────────────────────────────────────────────────
 * Installed once from `src/index.tsx` via `import "./auth/fetchInterceptor"`.
 * That import is deliberately absent from every OTHER production module: this
 * replaces `window.fetch` globally, so a second import site would be a second
 * wrapper.
 *
 * Note the module side-effect at the bottom of this file DOES run when a test
 * imports `installSimulationFetchInterceptor` from here — a top-level statement
 * cannot be opted out of. That is harmless only because each test then installs
 * its own wrapper over the top with a mock, and because the CS suite's other
 * files reach `window.fetch` through `vi.spyOn(global, "fetch")` rather than
 * through this module. If a future test asserts on the *un*-intercepted fetch
 * after importing this file, it will be measuring the wrapper. Verified
 * empirically: the CS suite is 995/995 with this file imported.
 *
 * ── GUARD ────────────────────────────────────────────────────────────────────
 * Every non-empty runtimeConfig API base that CS fetches MUST be stamped by
 * some mechanism (either this interceptor or the client's own explicit header).
 * Auth mechanisms are classified in
 * `src/auth/__tests__/fetchInterceptor.test.ts` section 6, which derives the
 * list of *Endpoint keys from `connected_services_ui_stack.py` at test time.
 * Adding a new *Endpoint key to the stack without a classification entry in that
 * file causes the guard test to FAIL — it is not re-derived from a comment here.
 * See decisions.md, FG11.1 section for the per-base evidence.
 *   simulationApiEndpoint  — this interceptor (routes are COGNITO_USER_POOLS)
 *   connectedServicesApiEndpoint — dataModelClient (explicit Authorization)
 *   subscriptionsApiEndpoint     — subscriptionsClient (explicit Authorization)
 *   dataProcessingApiEndpoint    — dataModelClient (explicit Authorization)
 *   (simulationClient itself also stamps its own calls — interceptor does not
 *    overwrite because of the `has("Authorization")` check below)
 */

import { STORAGE_KEY_ID_TOKEN } from "../auth";
import { getSimulationApiBase } from "../api/simulationClient";

/**
 * True when `url` targets the same origin as `base` and the URL's path begins
 * with the base path, matched on a `/`-boundary.
 *
 * This is an origin/path-boundary test, NOT a bare string-prefix test.
 * `url.startsWith(base)` fails when the base has no trailing slash:
 * `"https://sim.example.invalid"` also prefix-matches the attacker host
 * `"https://sim.example.invalid.attacker.test/steal"`. This helper parses
 * both URLs so the origin comparison is structural.
 *
 * Malformed or relative URLs: URL parsing throws a TypeError for relative URLs
 * and invalid inputs. Both cases return false — never stamp, never throw.
 *
 * Not reachable as an attack today (all URLs at these call sites are built from
 * runtimeConfig plus a literal path), but the function makes the docstring claim
 * accurate and the test catches the bypass.
 */
function matchesBase(url: string, base: string): boolean {
  let urlObj: URL;
  let baseObj: URL;
  try {
    urlObj = new URL(url);
    baseObj = new URL(base);
  } catch {
    // Relative URL, empty string, or malformed input — do not stamp.
    return false;
  }
  if (urlObj.origin !== baseObj.origin) return false;
  // Normalize the base path so it ends with "/" for boundary comparison.
  const basePath = baseObj.pathname.endsWith("/") ? baseObj.pathname : baseObj.pathname + "/";
  return urlObj.pathname === baseObj.pathname || urlObj.pathname.startsWith(basePath);
}

/**
 * Build and install the simulation-API fetch interceptor.
 *
 * Exported for testing: tests import this function directly, pass a mock as
 * `originalFetch`, and call it to install the interceptor in a controlled way.
 * The module side-effect (last line of this file) calls it with the real
 * `window.fetch` so production behaviour is unchanged.
 *
 * @param originalFetch  The underlying fetch implementation to call after
 *                       optionally stamping the request.
 */
export function installSimulationFetchInterceptor(
  originalFetch: typeof fetch,
): void {
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url;

    // Use getSimulationApiBase() so leading/trailing whitespace and trailing
    // slashes are normalized identically to how simulationClient.ts builds URLs.
    // A raw `rc.simulationApiEndpoint` with one leading space would leave the
    // client matching while the interceptor stamps nothing — a silent 401.
    // Wrapped in try/catch because getRuntimeConfig() throws when runtimeConfig
    // is absent (simulation stack not deployed, or config not yet loaded).
    let simulationBase: string;
    try {
      simulationBase = getSimulationApiBase() ?? "";
    } catch {
      simulationBase = "";
    }

    // Stamp ONLY requests to the simulation base, matched on an origin/path
    // boundary (see matchesBase above). An empty base matches nothing.
    if (simulationBase && matchesBase(url, simulationBase)) {
      let idToken: string | null = null;
      try {
        idToken = sessionStorage.getItem(STORAGE_KEY_ID_TOKEN);
      } catch {
        // sessionStorage unavailable — proceed without stamping.
      }
      if (idToken) {
        const headers = new Headers(init?.headers);
        // Do not overwrite an already-present Authorization header.
        // simulationClient attaches its own Bearer token; explicit clients win.
        if (!headers.has("Authorization")) {
          headers.set("Authorization", `Bearer ${idToken}`);
        }
        init = { ...init, headers };
      }
    }

    return originalFetch(input, init);
  };
}

// ── Module side-effect: install the interceptor in the browser ────────────────
// Captures window.fetch before replacing it, so production code calls the
// real fetch after any header modifications. This also runs when a test imports
// `installSimulationFetchInterceptor` from this module — see the INSTALLATION
// note above for why that is tolerable rather than intended.
installSimulationFetchInterceptor(window.fetch);
