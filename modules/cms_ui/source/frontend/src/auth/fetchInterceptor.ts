/**
 * Fetch interceptor that auto-injects the Cognito ID token for all
 * CMS-managed API Gateway calls. This avoids modifying every component
 * that makes direct fetch() calls.
 *
 * Covered API hosts (auto-injected):
 *   - apiEndpoint                — main CMS Fleet API (Cognito user pool authorizer)
 *   - dataProcessingApiEndpoint  — Signal Catalog + Campaigns API
 *   - vsaApiEndpoint             — Virtual Service Agent API (multi-pool authorizer)
 *   - simulationApiEndpoint      — Simulation REST API (every route is
 *                                  COGNITO_USER_POOLS, confirmed against live API
 *                                  `tekm196qb5` — unauthenticated requests return 401)
 *   - commandsApiEndpoint        — Remote Commands REST API (every route is
 *                                  COGNITO_USER_POOLS, confirmed against live API
 *                                  `9xpiwl2269`, 7 of 7 routes — unauthenticated
 *                                  requests return 401, observed 2026-09-15). See
 *                                  `issues/2026-09-15-cms-commands-api-401-bare-fetches/`.
 *                                  NOTE: `utils/sovdScanClient.ts` already calls
 *                                  `authFetch()` which sets `Authorization: Bearer <token>`
 *                                  before this interceptor runs. The interceptor's
 *                                  `has("Authorization")` no-overwrite guard ensures it
 *                                  is not double-stamped.
 *
 * Uses the runtimeConfig URLs so prod/dev/local all "just work". Same
 * id-token gets used everywhere — the simulation API, like the data-processing API,
 * trusts the CMS user pool.
 *
 * ── WHY simulationApiEndpoint IS NOW COVERED ────────────────────────────────
 * This comment previously read "NOT covered: simulationApiEndpoint (no auth on that
 * backend by design — it's a developer-tool surface)." That was accurate until
 * commit 2e8c200d (2026-06-11, "fix(security): close H1 3775026 — CMS template
 * authorization gaps"), which added COGNITO_USER_POOLS authorization to every
 * simulation route. Commit 2e8c200d touched no frontend file, so the interceptor was
 * not updated, and all 16 getSimulationApiBase() call sites 401'd in cloud mode from
 * that day onward.
 *
 * To re-check: `aws apigateway get-resources --rest-api-id <APIGW_ID> --region
 * us-west-2 --embed methods` — look for `authorizationType: COGNITO_USER_POOLS` on
 * every `/api/simulation/...` route. A bare curl without an Authorization header
 * returns HTTP 401 (observed 2026-09-15). Every route on this base carries a Cognito
 * authorizer; update the list above if any route is explicitly made public again.
 * ────────────────────────────────────────────────────────────────────────────────────
 *
 * ── HEADER SHAPE DIVERGENCE ──────────────────────────────────────────────────
 * This interceptor sends the raw ID token string with no prefix (i.e. the token
 * value directly, not `Bearer <token>`). The connected-services interceptor
 * (`connected_services_ui/…/auth/fetchInterceptor.ts`) sends `Bearer <idToken>`.
 * Both target Cognito-authorized API Gateway routes; both forms are currently in
 * production use.
 *
 * Evidence: `simulationClient.ts:18-21` documents a measured HTTP 200 from live
 * API `tekm196qb5` with the Bearer-prefixed ID token. The raw form used by this
 * interceptor has no corresponding measured 200 against that specific base.
 * Do NOT harmonize to one form without a live two-token probe first — an unverified
 * header-shape change against a Cognito authorizer means a silent 401. See Task
 * 7.3(c) to capture both observations while a token is in hand.
 * ────────────────────────────────────────────────────────────────────────────────────
 */

const originalFetch = window.fetch;

/**
 * True when `url` targets the same origin as `base` and the URL's path begins
 * with the base path, matched on a `/`-boundary.
 *
 * This is an origin/path-boundary test, NOT a bare string-prefix test.
 * `url.startsWith(base)` fails when the base has no trailing slash:
 * `"https://api.example.invalid"` also prefix-matches the attacker host
 * `"https://api.example.invalid.attacker.test/steal"`. This helper parses
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
 * Build and install the CMS fetch interceptor.
 *
 * Exported for testing: tests import this function directly, pass a mock as
 * `underlyingFetch`, and call it to install the interceptor in a controlled way.
 * The module side-effect (last line of this file) calls it with the real
 * `window.fetch` so production behaviour is unchanged.
 *
 * @param underlyingFetch  The underlying fetch implementation to call after
 *                         optionally stamping the request.
 */
export function installCmsFetchInterceptor(underlyingFetch: typeof fetch): void {
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const rc = (window as any).runtimeConfig;

    // Build the list of API base URLs we should auth-stamp. Only non-empty
    // strings so we don't accidentally match the (always-truthy) empty
    // string against every relative URL on the SPA's own origin.
    const authedBases: string[] = [];
    for (const key of ["apiEndpoint", "dataProcessingApiEndpoint", "vsaApiEndpoint", "simulationApiEndpoint", "commandsApiEndpoint"] as const) {
      const v = rc?.[key];
      if (typeof v === "string" && v.length > 0) authedBases.push(v);
    }

    if (authedBases.some(base => matchesBase(url, base))) {
      const idToken = localStorage.getItem("idToken") || sessionStorage.getItem("idToken");
      if (idToken) {
        const headers = new Headers(init?.headers);
        if (!headers.has("Authorization")) {
          headers.set("Authorization", idToken);
        }
        init = { ...init, headers };
      }
    }

    return underlyingFetch(input, init);
  };
}

installCmsFetchInterceptor(originalFetch);
