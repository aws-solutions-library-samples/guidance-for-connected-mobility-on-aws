// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Hardening controls for the Connected Services OAuth flow: `state` (CSRF) and PKCE.
 *
 * Spec: .kiro/specs/2026-09-05-cms-connected-services-auth-integration/ H1.2, H3.1
 *
 * v2's Group 9 shipped a working authorization-code flow with neither control. This
 * file is the executable form of the two properties that were missing:
 *
 *   STATE — the callback must be bound to the browser that started the flow. Without
 *   it, `AuthCallback` exchanges whatever `code` is present in the URL, which is login
 *   CSRF / authorization-code injection. The rejection must happen BEFORE any network
 *   call, so the assertions below check that `fetch` was never invoked rather than only
 *   that no token was stored.
 *
 *   PKCE — this app client is public and has no secret in this flow, so the verifier is
 *   the only thing binding the code to this client. Cognito computes
 *   base64url(SHA-256(verifier)) and compares it to the `code_challenge` sent at
 *   /oauth2/authorize.
 *
 * Both are single-use: consumed on every path, success or failure, so a replayed
 * callback cannot reuse them.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  STORAGE_KEY_ACCESS_TOKEN,
  STORAGE_KEY_CODE_VERIFIER,
  STORAGE_KEY_ID_TOKEN,
  STORAGE_KEY_OAUTH_STATE,
  STORAGE_KEY_PRE_AUTH_URL,
  beginFederateSignIn,
  buildAuthorizeUrl,
  handleOAuthCallback,
} from "../auth";
import { computeCodeChallenge } from "../auth/pkce";

const CONFIG = {
  cognitoClientId: "testclientid12345",
  cognitoDomain: "auth.example.invalid",
} as const;

/** A syntactically valid unsigned JWT with a far-future exp. */
function jwt(groups: string[]): string {
  const b64 = (o: unknown) =>
    btoa(JSON.stringify(o)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
  return [
    b64({ alg: "RS256", typ: "JWT" }),
    b64({
      email: "insider@example.invalid",
      "cognito:groups": groups,
      exp: Math.floor(Date.now() / 1000) + 3600,
    }),
    "c2ln",
  ].join(".");
}

/** Install a fetch stub that returns a successful token response. */
function stubTokenEndpoint(body: Record<string, unknown> = {}) {
  // Argument types are declared so `f.mock.calls[0]` is a typed tuple rather than `[]`
  // — otherwise indexing it to inspect the outbound request is a tsc error.
  const f = vi.fn(async (_url: string, _init?: { body?: URLSearchParams }) => ({
    json: async () => ({
      access_token: "access-token-value",
      id_token: jwt(["connected-services"]),
      refresh_token: "refresh-token-value",
      ...body,
    }),
  }));
  vi.stubGlobal("fetch", f);
  return f;
}

/** Read the form-encoded body of the single fetch call. */
function sentBody(f: ReturnType<typeof stubTokenEndpoint>): URLSearchParams {
  return new URLSearchParams(f.mock.calls[0]?.[1]?.body?.toString() ?? "");
}

/** The URL the single fetch call was made against. */
function sentUrl(f: ReturnType<typeof stubTokenEndpoint>): string {
  return f.mock.calls[0]?.[0] ?? "";
}

beforeEach(() => {
  window.sessionStorage.clear();
  window.history.replaceState({}, "", "/");
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

// ---------------------------------------------------------------------------
// The authorize request must carry state + PKCE
// ---------------------------------------------------------------------------

describe("buildAuthorizeUrl — carries state and PKCE", () => {
  const PKCE = { state: "state-abc", codeChallenge: "challenge-xyz" };

  it("includes the code_challenge", () => {
    expect(buildAuthorizeUrl(CONFIG, PKCE)).toContain("code_challenge=challenge-xyz");
  });

  it("declares code_challenge_method=S256", () => {
    // "plain" is permitted by RFC 7636 but offers no protection against an
    // attacker who can read the authorize request.
    expect(buildAuthorizeUrl(CONFIG, PKCE)).toContain("code_challenge_method=S256");
  });

  it("includes the state parameter", () => {
    expect(buildAuthorizeUrl(CONFIG, PKCE)).toContain("state=state-abc");
  });

  it("never sends the verifier itself", () => {
    // Sending code_verifier at the authorize endpoint would defeat PKCE entirely.
    expect(buildAuthorizeUrl(CONFIG, PKCE)).not.toContain("code_verifier");
  });

  it("retains the v2 parameters it already had", () => {
    const url = buildAuthorizeUrl(CONFIG, PKCE);
    expect(url).toMatch(/^https:\/\/auth\.example\.invalid\/oauth2\/authorize\?/);
    expect(url).toContain("response_type=code");
    expect(url).toContain(`client_id=${CONFIG.cognitoClientId}`);
    expect(url).toContain("identity_provider=AmazonFederate");
  });
});

// ---------------------------------------------------------------------------
// beginFederateSignIn persists what the callback will need
// ---------------------------------------------------------------------------

describe("beginFederateSignIn — persists verifier and state before redirecting", () => {
  it("stores a verifier and a state in sessionStorage", async () => {
    await beginFederateSignIn(CONFIG);
    expect(window.sessionStorage.getItem(STORAGE_KEY_CODE_VERIFIER)).toBeTruthy();
    expect(window.sessionStorage.getItem(STORAGE_KEY_OAUTH_STATE)).toBeTruthy();
  });

  it("the URL's code_challenge is the S256 of the STORED verifier", async () => {
    // The strongest available check short of a live exchange: it proves the two
    // halves are actually paired, not merely both present. A flow that stores one
    // verifier and sends the challenge of another fails only at the token endpoint.
    const url = await beginFederateSignIn(CONFIG);
    const stored = window.sessionStorage.getItem(STORAGE_KEY_CODE_VERIFIER)!;
    const expected = await computeCodeChallenge(stored);
    expect(new URL(url).searchParams.get("code_challenge")).toBe(expected);
  });

  it("the URL's state is the stored state", async () => {
    const url = await beginFederateSignIn(CONFIG);
    const stored = window.sessionStorage.getItem(STORAGE_KEY_OAUTH_STATE);
    expect(new URL(url).searchParams.get("state")).toBe(stored);
  });

  it("issues a fresh verifier and state per sign-in attempt", async () => {
    await beginFederateSignIn(CONFIG);
    const v1 = window.sessionStorage.getItem(STORAGE_KEY_CODE_VERIFIER);
    const s1 = window.sessionStorage.getItem(STORAGE_KEY_OAUTH_STATE);
    await beginFederateSignIn(CONFIG);
    expect(window.sessionStorage.getItem(STORAGE_KEY_CODE_VERIFIER)).not.toBe(v1);
    expect(window.sessionStorage.getItem(STORAGE_KEY_OAUTH_STATE)).not.toBe(s1);
  });

  it("records the pre-auth path for post-login return", async () => {
    window.history.replaceState({}, "", "/software/campaigns?filter=open");
    await beginFederateSignIn(CONFIG);
    expect(window.sessionStorage.getItem(STORAGE_KEY_PRE_AUTH_URL)).toBe(
      "/software/campaigns?filter=open"
    );
  });
});

// ---------------------------------------------------------------------------
// The callback must reject on any state problem, before any network call
// ---------------------------------------------------------------------------

describe("handleOAuthCallback — state is enforced before the token request", () => {
  it("rejects a MISMATCHED state without calling the token endpoint", async () => {
    const f = stubTokenEndpoint();
    window.sessionStorage.setItem(STORAGE_KEY_OAUTH_STATE, "expected-state");
    window.sessionStorage.setItem(STORAGE_KEY_CODE_VERIFIER, "verifier");

    await expect(
      handleOAuthCallback("auth-code", "attacker-state", CONFIG)
    ).rejects.toThrow(/state/i);

    expect(
      f,
      "The token endpoint was called despite a state mismatch. Rejection must " +
        "precede the network call — otherwise an injected code is redeemed and only " +
        "the storage step is skipped."
    ).not.toHaveBeenCalled();
    expect(window.sessionStorage.getItem(STORAGE_KEY_ID_TOKEN)).toBeNull();
    expect(window.sessionStorage.getItem(STORAGE_KEY_ACCESS_TOKEN)).toBeNull();
  });

  it("rejects an ABSENT incoming state", async () => {
    const f = stubTokenEndpoint();
    window.sessionStorage.setItem(STORAGE_KEY_OAUTH_STATE, "expected-state");
    window.sessionStorage.setItem(STORAGE_KEY_CODE_VERIFIER, "verifier");

    await expect(handleOAuthCallback("auth-code", null, CONFIG)).rejects.toThrow(/state/i);
    expect(f).not.toHaveBeenCalled();
  });

  it("rejects when NO state was stored — an unsolicited callback", async () => {
    const f = stubTokenEndpoint();
    window.sessionStorage.setItem(STORAGE_KEY_CODE_VERIFIER, "verifier");

    // This is the bare login-CSRF case: the victim's browser never started a flow.
    await expect(
      handleOAuthCallback("auth-code", "attacker-state", CONFIG)
    ).rejects.toThrow(/state/i);
    expect(f).not.toHaveBeenCalled();
  });

  it("rejects an empty-string stored state rather than treating it as a match", async () => {
    const f = stubTokenEndpoint();
    window.sessionStorage.setItem(STORAGE_KEY_OAUTH_STATE, "");
    window.sessionStorage.setItem(STORAGE_KEY_CODE_VERIFIER, "verifier");

    // "" === "" would pass a naive equality check.
    await expect(handleOAuthCallback("auth-code", "", CONFIG)).rejects.toThrow(/state/i);
    expect(f).not.toHaveBeenCalled();
  });

  it("rejects when the verifier is missing even if state matches", async () => {
    const f = stubTokenEndpoint();
    window.sessionStorage.setItem(STORAGE_KEY_OAUTH_STATE, "s");

    await expect(handleOAuthCallback("auth-code", "s", CONFIG)).rejects.toThrow(/verifier/i);
    expect(f).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Single-use — consumed on every path
// ---------------------------------------------------------------------------

describe("handleOAuthCallback — state and verifier are single-use", () => {
  it("clears both after a SUCCESSFUL exchange", async () => {
    stubTokenEndpoint();
    window.sessionStorage.setItem(STORAGE_KEY_OAUTH_STATE, "s");
    window.sessionStorage.setItem(STORAGE_KEY_CODE_VERIFIER, "v");

    await handleOAuthCallback("auth-code", "s", CONFIG);

    expect(window.sessionStorage.getItem(STORAGE_KEY_OAUTH_STATE)).toBeNull();
    expect(window.sessionStorage.getItem(STORAGE_KEY_CODE_VERIFIER)).toBeNull();
  });

  it("clears both after a REJECTED exchange", async () => {
    stubTokenEndpoint();
    window.sessionStorage.setItem(STORAGE_KEY_OAUTH_STATE, "s");
    window.sessionStorage.setItem(STORAGE_KEY_CODE_VERIFIER, "v");

    await expect(handleOAuthCallback("auth-code", "wrong", CONFIG)).rejects.toThrow();

    // Retaining them after a rejection lets an attacker retry against a still-live
    // verifier until they guess the state.
    expect(window.sessionStorage.getItem(STORAGE_KEY_OAUTH_STATE)).toBeNull();
    expect(window.sessionStorage.getItem(STORAGE_KEY_CODE_VERIFIER)).toBeNull();
  });

  it("a replayed callback fails because the first consumed the state", async () => {
    stubTokenEndpoint();
    window.sessionStorage.setItem(STORAGE_KEY_OAUTH_STATE, "s");
    window.sessionStorage.setItem(STORAGE_KEY_CODE_VERIFIER, "v");

    await handleOAuthCallback("auth-code", "s", CONFIG);
    await expect(handleOAuthCallback("auth-code", "s", CONFIG)).rejects.toThrow(/state/i);
  });
});

// ---------------------------------------------------------------------------
// The happy path sends the verifier
// ---------------------------------------------------------------------------

describe("handleOAuthCallback — the token request", () => {
  it("sends the code_verifier", async () => {
    const f = stubTokenEndpoint();
    window.sessionStorage.setItem(STORAGE_KEY_OAUTH_STATE, "s");
    window.sessionStorage.setItem(STORAGE_KEY_CODE_VERIFIER, "the-verifier");

    await handleOAuthCallback("auth-code", "s", CONFIG);

    expect(sentBody(f).get("code_verifier")).toBe("the-verifier");
  });

  it("retains the parameters v2 already sent", async () => {
    const f = stubTokenEndpoint();
    window.sessionStorage.setItem(STORAGE_KEY_OAUTH_STATE, "s");
    window.sessionStorage.setItem(STORAGE_KEY_CODE_VERIFIER, "v");

    await handleOAuthCallback("auth-code", "s", CONFIG);

    const body = sentBody(f);
    expect(body.get("grant_type")).toBe("authorization_code");
    expect(body.get("client_id")).toBe(CONFIG.cognitoClientId);
    expect(body.get("code")).toBe("auth-code");
    expect(body.get("redirect_uri")).toContain("/auth/callback");
    expect(sentUrl(f)).toBe("https://auth.example.invalid/oauth2/token");
  });

  it("stores the tokens under the keys v2 established", async () => {
    stubTokenEndpoint();
    window.sessionStorage.setItem(STORAGE_KEY_OAUTH_STATE, "s");
    window.sessionStorage.setItem(STORAGE_KEY_CODE_VERIFIER, "v");

    await handleOAuthCallback("auth-code", "s", CONFIG);

    expect(window.sessionStorage.getItem(STORAGE_KEY_ACCESS_TOKEN)).toBe("access-token-value");
    expect(window.sessionStorage.getItem(STORAGE_KEY_ID_TOKEN)).toBeTruthy();
  });

  it("throws when the response carries no access_token", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ json: async () => ({ error: "invalid_grant" }) }))
    );
    window.sessionStorage.setItem(STORAGE_KEY_OAUTH_STATE, "s");
    window.sessionStorage.setItem(STORAGE_KEY_CODE_VERIFIER, "v");

    await expect(handleOAuthCallback("auth-code", "s", CONFIG)).rejects.toThrow();
  });
});

// ---------------------------------------------------------------------------
// Open-redirect guard on the post-login return path
// ---------------------------------------------------------------------------

describe("handleOAuthCallback — the return path cannot leave the origin", () => {
  it("rejects a protocol-relative //host path", async () => {
    stubTokenEndpoint();
    window.sessionStorage.setItem(STORAGE_KEY_OAUTH_STATE, "s");
    window.sessionStorage.setItem(STORAGE_KEY_CODE_VERIFIER, "v");
    window.sessionStorage.setItem(STORAGE_KEY_PRE_AUTH_URL, "//evil.example/phish");

    // `//evil.example` passes a naive startsWith('/') check and is treated by the
    // browser as an absolute URL on the current protocol. DMS guards this at
    // SimpleAuthProvider.tsx:346; the v2 port dropped the guard.
    const dest = await handleOAuthCallback("auth-code", "s", CONFIG);
    expect(dest).toBe("/");
  });

  it("rejects an absolute http(s) URL", async () => {
    stubTokenEndpoint();
    window.sessionStorage.setItem(STORAGE_KEY_OAUTH_STATE, "s");
    window.sessionStorage.setItem(STORAGE_KEY_CODE_VERIFIER, "v");
    window.sessionStorage.setItem(STORAGE_KEY_PRE_AUTH_URL, "https://evil.example/phish");

    expect(await handleOAuthCallback("auth-code", "s", CONFIG)).toBe("/");
  });

  it.each([
    "javascript:alert(document.domain)",
    "JaVaScRiPt:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "\\\\evil.example\\share",
  ])("rejects the scheme-bearing return path %s", async (candidate) => {
    // Reviewer Cycle 1 suggestion 1. These are all rejected by the same rule that
    // rejects absolute URLs — they do not begin with "/" — but asserting them pins the
    // guard against a future rewrite that starts allowlisting schemes instead.
    stubTokenEndpoint();
    window.sessionStorage.setItem(STORAGE_KEY_OAUTH_STATE, "s");
    window.sessionStorage.setItem(STORAGE_KEY_CODE_VERIFIER, "v");
    window.sessionStorage.setItem(STORAGE_KEY_PRE_AUTH_URL, candidate);

    expect(await handleOAuthCallback("auth-code", "s", CONFIG)).toBe("/");
  });

  it("preserves a legitimate same-origin path and its query string", async () => {
    stubTokenEndpoint();
    window.sessionStorage.setItem(STORAGE_KEY_OAUTH_STATE, "s");
    window.sessionStorage.setItem(STORAGE_KEY_CODE_VERIFIER, "v");
    window.sessionStorage.setItem(STORAGE_KEY_PRE_AUTH_URL, "/software/campaigns?filter=open");

    expect(await handleOAuthCallback("auth-code", "s", CONFIG)).toBe(
      "/software/campaigns?filter=open"
    );
  });

  it("defaults to / when no pre-auth path was recorded", async () => {
    stubTokenEndpoint();
    window.sessionStorage.setItem(STORAGE_KEY_OAUTH_STATE, "s");
    window.sessionStorage.setItem(STORAGE_KEY_CODE_VERIFIER, "v");

    expect(await handleOAuthCallback("auth-code", "s", CONFIG)).toBe("/");
  });

  it("consumes the pre-auth path so a later login does not reuse it", async () => {
    stubTokenEndpoint();
    window.sessionStorage.setItem(STORAGE_KEY_OAUTH_STATE, "s");
    window.sessionStorage.setItem(STORAGE_KEY_CODE_VERIFIER, "v");
    window.sessionStorage.setItem(STORAGE_KEY_PRE_AUTH_URL, "/sales/data-products");

    await handleOAuthCallback("auth-code", "s", CONFIG);
    expect(window.sessionStorage.getItem(STORAGE_KEY_PRE_AUTH_URL)).toBeNull();
  });
});
