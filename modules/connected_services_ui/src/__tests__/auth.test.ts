// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Authorization tests — T9.1 Federate sign-in integration.
 *
 * Spec T9.1: getSession() derives the session from a Cognito id-token in
 * sessionStorage rather than window.__SESSION__.  All fail-closed cases apply:
 *   - Absent token  → null (unauthenticated)
 *   - Expired token → null (fail-closed)
 *   - Malformed JWT → null (fail-closed, no throw)
 *   - Valid token   → Session with groups from 'cognito:groups' claim
 *
 * Migrated from the v1 window.__SESSION__ contract (spec note: "if a v1 auth
 * test asserts the window.__SESSION__ contract, migrate it rather than deleting
 * it"). The isAuthorized() and group-membership cases are unchanged.
 *
 * window.__SESSION__ was the tier-1 test-harness override in v1. It is no
 * longer read by getSession() — tests now inject sessions via sessionStorage.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  CONNECTED_SERVICES_GROUP,
  STORAGE_KEY_ACCESS_TOKEN,
  STORAGE_KEY_ID_TOKEN,
  STORAGE_KEY_PRE_AUTH_URL,
  type Session,
  buildAuthorizeUrl,
  getSession,
  isAuthorized,
} from "../auth";

// ---------------------------------------------------------------------------
// JWT helpers — build test tokens
// ---------------------------------------------------------------------------

function base64urlEncode(obj: object): string {
  const json = JSON.stringify(obj);
  // btoa works on latin1; use encodeURIComponent+replace for unicode safety
  const b64 = btoa(unescape(encodeURIComponent(json)));
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}

/**
 * Build a minimal JWT (header.payload.signature) for testing.
 * The signature is a placeholder — we only decode, never verify.
 */
function makeIdToken(claims: Record<string, unknown>): string {
  const header = base64urlEncode({ alg: "RS256", typ: "JWT" });
  const payload = base64urlEncode(claims);
  return `${header}.${payload}.fake-signature`;
}

/** Future exp — valid, not expired */
const FUTURE_EXP = Math.floor(Date.now() / 1000) + 3600;
/** Past exp — expired */
const PAST_EXP = Math.floor(Date.now() / 1000) - 3600;

// ---------------------------------------------------------------------------
// isAuthorized — fail-closed authorization function
// (unchanged from v1; migrated for completeness)
// ---------------------------------------------------------------------------

describe("isAuthorized", () => {
  it("returns true for a session with the connected-services group", () => {
    const session: Session = {
      alias: "jdoe@example.com",
      groups: [CONNECTED_SERVICES_GROUP],
    };
    expect(isAuthorized(session)).toBe(true);
  });

  it("returns true when connected-services is among multiple groups", () => {
    const session: Session = {
      alias: "jdoe@example.com",
      groups: ["fleet-operator", CONNECTED_SERVICES_GROUP, "platform-admin"],
    };
    expect(isAuthorized(session)).toBe(true);
  });

  // Fail-closed cases:

  it("returns false for a null session (no session)", () => {
    expect(isAuthorized(null)).toBe(false);
  });

  it("returns false for a session with an empty groups array", () => {
    const session: Session = { alias: "jdoe@example.com", groups: [] };
    expect(isAuthorized(session)).toBe(false);
  });

  it("returns false for a session lacking the connected-services group", () => {
    const session: Session = {
      alias: "jdoe@example.com",
      groups: ["fleet-operator", "platform-admin"],
    };
    expect(isAuthorized(session)).toBe(false);
  });

  it("returns false for a session with an unrelated group only", () => {
    const session: Session = { alias: "jdoe@example.com", groups: ["some-other-group"] };
    expect(isAuthorized(session)).toBe(false);
  });

  it("is case-sensitive — 'Connected-Services' does not match", () => {
    const session: Session = {
      alias: "jdoe@example.com",
      groups: ["Connected-Services", "CONNECTED-SERVICES"],
    };
    expect(isAuthorized(session)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// getSession — JWT-based session from sessionStorage (T9.1)
// ---------------------------------------------------------------------------

describe("getSession — JWT from sessionStorage", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  afterEach(() => {
    sessionStorage.clear();
  });

  // ── Absent token (AC1, fail-closed) ──────────────────────────────────────

  it("returns null when sessionStorage has no idToken (unauthenticated)", () => {
    expect(getSession()).toBeNull();
  });

  it("returns null when idToken is an empty string", () => {
    sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, "");
    expect(getSession()).toBeNull();
  });

  // ── Expired token (AC4, fail-closed) ─────────────────────────────────────

  it("returns null for an expired id-token (exp in the past)", () => {
    const token = makeIdToken({
      email: "jdoe@example.com",
      "cognito:groups": [CONNECTED_SERVICES_GROUP],
      exp: PAST_EXP,
    });
    sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, token);
    expect(getSession()).toBeNull();
  });

  // ── Malformed JWT (AC4, fail-closed, no throw) ────────────────────────────

  it("returns null for a non-JWT string — does not throw", () => {
    sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, "not-a-jwt");
    expect(() => getSession()).not.toThrow();
    expect(getSession()).toBeNull();
  });

  it("returns null for a two-part token (missing signature segment)", () => {
    sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, "header.payload");
    expect(getSession()).toBeNull();
  });

  it("returns null for a token with invalid base64url payload", () => {
    sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, "header.!!!invalid!!!.sig");
    expect(getSession()).toBeNull();
  });

  it("returns null for a token whose payload is valid base64 but not JSON", () => {
    const fakePayload = btoa("not json at all");
    sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, `header.${fakePayload}.sig`);
    expect(getSession()).toBeNull();
  });

  // ── Valid token (AC1, groups from cognito:groups) ─────────────────────────

  it("returns a session with groups from cognito:groups claim", () => {
    const token = makeIdToken({
      email: "jdoe@example.com",
      "cognito:groups": [CONNECTED_SERVICES_GROUP, "fleet-operator"],
      exp: FUTURE_EXP,
    });
    sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, token);
    const session = getSession();
    expect(session).not.toBeNull();
    expect(session!.alias).toBe("jdoe@example.com");
    expect(session!.groups).toContain(CONNECTED_SERVICES_GROUP);
    expect(session!.groups).toContain("fleet-operator");
  });

  it("returns a session with alias from email claim", () => {
    const token = makeIdToken({
      email: "testuser@example.com",
      "cognito:groups": [CONNECTED_SERVICES_GROUP],
      exp: FUTURE_EXP,
    });
    sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, token);
    const session = getSession();
    expect(session!.alias).toBe("testuser@example.com");
  });

  it("falls back to sub claim when email is absent", () => {
    const token = makeIdToken({
      sub: "user-sub-12345",
      "cognito:groups": [CONNECTED_SERVICES_GROUP],
      exp: FUTURE_EXP,
    });
    sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, token);
    const session = getSession();
    expect(session!.alias).toBe("user-sub-12345");
  });

  it("returns empty groups array when cognito:groups claim is absent", () => {
    const token = makeIdToken({
      email: "jdoe@example.com",
      exp: FUTURE_EXP,
      // no cognito:groups
    });
    sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, token);
    const session = getSession();
    expect(session).not.toBeNull();
    expect(session!.groups).toEqual([]);
  });

  it("returns a session for a token with no exp claim (no expiry check)", () => {
    const token = makeIdToken({
      email: "jdoe@example.com",
      "cognito:groups": [CONNECTED_SERVICES_GROUP],
      // no exp
    });
    sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, token);
    expect(getSession()).not.toBeNull();
  });

  it("combined: valid token is authorized", () => {
    const token = makeIdToken({
      email: "jdoe@example.com",
      "cognito:groups": [CONNECTED_SERVICES_GROUP],
      exp: FUTURE_EXP,
    });
    sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, token);
    const session = getSession();
    expect(isAuthorized(session)).toBe(true);
  });

  it("combined: expired token is unauthorized (fail-closed)", () => {
    const token = makeIdToken({
      email: "jdoe@example.com",
      "cognito:groups": [CONNECTED_SERVICES_GROUP],
      exp: PAST_EXP,
    });
    sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, token);
    expect(isAuthorized(getSession())).toBe(false);
  });

  it("combined: token with wrong group is unauthorized (fail-closed)", () => {
    const token = makeIdToken({
      email: "jdoe@example.com",
      "cognito:groups": ["other-group"],
      exp: FUTURE_EXP,
    });
    sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, token);
    expect(isAuthorized(getSession())).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// buildAuthorizeUrl — Hosted UI URL construction (AC2)
// ---------------------------------------------------------------------------

describe("buildAuthorizeUrl", () => {
  const config = {
    cognitoClientId: "testclientid12345",
    cognitoDomain: "auth.example.invalid",
  };

  // buildAuthorizeUrl takes a required { state, codeChallenge } as of H3.1 — omitting
  // it is a compile error, which is the point. Fixed literals keep these assertions
  // about URL construction rather than about crypto.
  const PKCE = { state: "test-state", codeChallenge: "test-challenge" };

  it("constructs a URL pointing at the oauth2/authorize endpoint", () => {
    const url = buildAuthorizeUrl(config, PKCE);
    expect(url).toMatch(/^https:\/\/auth\.example\.invalid\/oauth2\/authorize\?/);
  });

  it("includes response_type=code", () => {
    const url = buildAuthorizeUrl(config, PKCE);
    expect(url).toContain("response_type=code");
  });

  it("includes the client_id", () => {
    const url = buildAuthorizeUrl(config, PKCE);
    expect(url).toContain(`client_id=${config.cognitoClientId}`);
  });

  it("includes identity_provider=AmazonFederate", () => {
    const url = buildAuthorizeUrl(config, PKCE);
    expect(url).toContain("identity_provider=AmazonFederate");
  });

  it("includes scope=openid+email+profile (space-encoded as +)", () => {
    const url = buildAuthorizeUrl(config, PKCE);
    // URLSearchParams encodes spaces as '+' in query strings
    expect(url).toMatch(/scope=openid[+%20]email[+%20]profile/);
  });

  it("includes redirect_uri ending in /auth/callback", () => {
    const url = buildAuthorizeUrl(config, PKCE);
    expect(url).toContain("redirect_uri=");
    // The redirect_uri is URL-encoded by URLSearchParams — check both plain and encoded forms.
    expect(url.includes("/auth/callback") || url.includes("%2Fauth%2Fcallback")).toBe(true);
  });

  // REVERSED 2026-09-05 by spec 2026-09-05-cms-connected-services-auth-integration H3.1.
  //
  // This assertion previously pinned the ABSENCE of PKCE, which was a correct record of
  // T9.1's deliberate choice (see that spec's decisions.md: the user was unavailable to
  // UAT a divergent redirect flow). That choice was overruled by explicit direction once
  // the flow was working, so the pin is inverted rather than deleted — the file should
  // still fail if PKCE silently disappears again.
  //
  // Detailed PKCE/state behaviour lives in src/__tests__/authHardening.test.ts.
  it("DOES include code_challenge with method S256 (PKCE)", () => {
    const url = buildAuthorizeUrl(config, PKCE);
    expect(url).toContain("code_challenge=");
    expect(url).toContain("code_challenge_method=S256");
  });

  it("never sends the code_verifier at the authorize endpoint", () => {
    // Sending the verifier here instead of the challenge defeats PKCE entirely.
    const url = buildAuthorizeUrl(config, PKCE);
    expect(url).not.toContain("code_verifier");
  });
});

// ---------------------------------------------------------------------------
// sessionStorage key constants (T9.1 — same keys as CMS)
// ---------------------------------------------------------------------------

describe("sessionStorage key constants", () => {
  it("STORAGE_KEY_ID_TOKEN is 'idToken' (mirrors CMS)", () => {
    expect(STORAGE_KEY_ID_TOKEN).toBe("idToken");
  });

  it("STORAGE_KEY_ACCESS_TOKEN is 'authToken' (mirrors CMS)", () => {
    expect(STORAGE_KEY_ACCESS_TOKEN).toBe("authToken");
  });

  it("STORAGE_KEY_PRE_AUTH_URL is 'preAuthUrl' (mirrors CMS)", () => {
    expect(STORAGE_KEY_PRE_AUTH_URL).toBe("preAuthUrl");
  });
});
