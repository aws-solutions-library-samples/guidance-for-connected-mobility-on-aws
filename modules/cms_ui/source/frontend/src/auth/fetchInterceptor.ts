/**
 * Fetch interceptor that auto-injects the Cognito ID token for all
 * CMS-managed API Gateway calls. This avoids modifying every component
 * that makes direct fetch() calls.
 *
 * Covered API hosts (auto-injected):
 *   - apiEndpoint                — main CMS Fleet API (Cognito user pool authorizer)
 *   - dataProcessingApiEndpoint  — Signal Catalog + Campaigns API
 *   - vsaApiEndpoint             — Virtual Service Agent API (multi-pool authorizer)
 *
 * Uses the runtimeConfig URLs so prod/dev/local all "just work". Same
 * id-token gets used everywhere — both the data-processing API and the
 * VSA API trust the CMS user pool (the latter via its
 * extraCognitoUserPoolIds list, see vsa-api-stack.ts), so a single token
 * suffices.
 *
 * NOT covered: simulationApiEndpoint (no auth on that backend by design
 * — it's a developer-tool surface). Add here if that changes.
 */

const originalFetch = window.fetch;

window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
  const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
  const rc = (window as any).runtimeConfig;

  // Build the list of API base URLs we should auth-stamp. Only non-empty
  // strings so we don't accidentally match the (always-truthy) empty
  // string against every relative URL on the SPA's own origin.
  const authedBases: string[] = [];
  for (const key of ["apiEndpoint", "dataProcessingApiEndpoint", "vsaApiEndpoint"] as const) {
    const v = rc?.[key];
    if (typeof v === "string" && v.length > 0) authedBases.push(v);
  }

  if (authedBases.some(base => url.startsWith(base))) {
    const idToken = localStorage.getItem("idToken") || sessionStorage.getItem("idToken");
    if (idToken) {
      const headers = new Headers(init?.headers);
      if (!headers.has("Authorization")) {
        headers.set("Authorization", idToken);
      }
      init = { ...init, headers };
    }
  }

  return originalFetch(input, init);
};
