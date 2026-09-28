// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import "@testing-library/jest-dom";

// ---------------------------------------------------------------------------
// Global test session — T9.1 migration
//
// Guards in src/__tests__/ (registryCompleteness, clickPath, deadLinks, etc.)
// render the real AppRoutes which passes through RequireAuth.  RequireAuth
// calls getSession(), which reads sessionStorage 'idToken'.  To avoid
// requiring every guard to inject its own session, we seed a valid authorized
// JWT once here at global setup time.
//
// Tests that need to exercise the absent / expired / malformed paths
// (auth.test.ts, RequireAuth.test.tsx) explicitly call
// sessionStorage.clear() in their beforeEach() and are unaffected by this
// seed because their beforeEach runs AFTER this file.
//
// The token is a synthetic test JWT (fake signature, future exp, connected-services
// group) — it is never sent to a real service.
// ---------------------------------------------------------------------------
function makeTestToken(): string {
  const b64url = (obj: object): string => {
    const json = JSON.stringify(obj);
    const b64 = btoa(unescape(encodeURIComponent(json)));
    return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
  };
  const header = b64url({ alg: "RS256", typ: "JWT" });
  const payload = b64url({
    email: "test-harness@example.com",
    "cognito:groups": ["connected-services"],
    exp: Math.floor(Date.now() / 1000) + 86400, // valid for 24 h
  });
  return `${header}.${payload}.test-harness-fake-sig`;
}

// Seed sessionStorage with an authorized test token so that RequireAuth
// renders children (not SignIn) for tests that don't explicitly manage auth.
sessionStorage.setItem("idToken", makeTestToken());

// Provide a minimal runtimeConfig stub so that SignIn components (reached if
// a test explicitly clears the token) can mount without throwing.
if (typeof window !== "undefined" && !window.runtimeConfig) {
  (window as unknown as { runtimeConfig: unknown }).runtimeConfig = {
    cognitoUserPoolId: "<test-pool-id>",
    cognitoClientId: "testclientid00000001",
    cognitoDomain: "portal.example.invalid",
    apiEndpoint: "https://api.example.invalid",
    callbackOrigin: "https://connected-services.example.invalid",
  };
}
