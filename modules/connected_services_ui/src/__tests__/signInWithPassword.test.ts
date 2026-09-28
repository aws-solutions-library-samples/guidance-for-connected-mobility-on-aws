// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * signInWithPassword tests.
 *
 * Three behavioural cases per the task spec:
 *   1. Success — stores idToken + authToken to sessionStorage and returns ok:true
 *   2. Wrong credentials — surfaces an error and stores nothing
 *   3. (UI layer) Empty fields — tested in SignIn.test.tsx
 *
 * The Cognito SDK call is mocked via vi.mock so no real network call is made.
 * Mirrors the mock pattern used by CMS vitest suites.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  STORAGE_KEY_ACCESS_TOKEN,
  STORAGE_KEY_ID_TOKEN,
  signInWithPassword,
} from "../auth";

// ── JWT helper ────────────────────────────────────────────────────────────────

function base64urlEncode(obj: object): string {
  const json = JSON.stringify(obj);
  const b64 = btoa(unescape(encodeURIComponent(json)));
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}

function makeIdToken(email: string): string {
  const header = base64urlEncode({ alg: "RS256", typ: "JWT" });
  const payload = base64urlEncode({
    email,
    "cognito:groups": ["connected-services"],
    exp: Math.floor(Date.now() / 1000) + 3600,
  });
  return `${header}.${payload}.fake-signature`;
}

// ── Mock the Cognito SDK (static import in auth.ts) ───────────────────────────
//
// signInWithPassword uses a static import from "@aws-sdk/client-cognito-identity-provider".
// vi.mock with a factory is hoisted before imports, so it intercepts the static import.

const FAKE_ACCESS_TOKEN = "fake-access-token-abc123";
const FAKE_ID_TOKEN = makeIdToken("user@example.com");

// Mocked send function — overridden per-test where needed
// `vi.mock` factories are HOISTED above module-level `const` declarations, so a factory
// that closes over a plain `const mockSend = vi.fn()` captures `undefined` — the mocked
// client's `send` is then not a function, the call throws, `signInWithPassword`'s
// try/catch swallows it, and every success case silently returns `{ok: false}`. That is
// exactly how this suite failed when first written. `vi.hoisted()` runs with the mock
// factories, so the reference is live by the time the factory executes.
const { mockSend, clientCtorArgs } = vi.hoisted(() => ({
  mockSend: vi.fn(),
  // Every `new CognitoIdentityProviderClient(cfg)` pushes its config here. A plain
  // `vi.fn()` cannot be used for the client (see the note below), so `mockClear()` /
  // `toHaveBeenCalledWith` are unavailable — this array is the constructor-call record.
  clientCtorArgs: [] as Array<Record<string, unknown>>,
}));

// Both SDK objects are invoked with `new`, so their mocks must be CONSTRUCTIBLE.
// `vi.fn().mockImplementation(() => ({...}))` uses an arrow function, which cannot be
// `new`-ed — vitest 4 warns "the vi.fn() mock did not use 'function' or 'class' in its
// implementation" and construction yields an object whose `send` is missing, so the call
// throws and `signInWithPassword`'s try/catch reports failure. Classes avoid it.
vi.mock("@aws-sdk/client-cognito-identity-provider", () => ({
  CognitoIdentityProviderClient: class {
    send = mockSend;
    constructor(config: Record<string, unknown>) {
      clientCtorArgs.push(config);
    }
  },
  InitiateAuthCommand: class {
    constructor(public input: unknown) {}
  },
  AuthFlowType: {
    USER_PASSWORD_AUTH: "USER_PASSWORD_AUTH",
  },
}));

// ── Config stub ───────────────────────────────────────────────────────────────

const CONFIG = {
  cognitoClientId: "testclientid00000001",
  cognitoRegion: "us-east-1",
  cognitoUserPoolId: "eu-west-1_TESTPOOL",
};

// ── Test setup ────────────────────────────────────────────────────────────────

beforeEach(() => {
  sessionStorage.clear();
  mockSend.mockReset();
  clientCtorArgs.length = 0;
});

afterEach(() => {
  sessionStorage.clear();
});

// ── 1. Success — stores tokens and yields an authorized session ───────────────

describe("signInWithPassword — success", () => {
  beforeEach(() => {
    mockSend.mockResolvedValue({
      AuthenticationResult: {
        AccessToken: FAKE_ACCESS_TOKEN,
        IdToken: FAKE_ID_TOKEN,
      },
    });
  });

  it("returns ok:true on successful authentication", async () => {
    const result = await signInWithPassword("user@example.com", "correct-password", CONFIG);
    expect(result.ok).toBe(true);
  });

  it("stores the access token under STORAGE_KEY_ACCESS_TOKEN ('authToken')", async () => {
    await signInWithPassword("user@example.com", "correct-password", CONFIG);
    expect(sessionStorage.getItem(STORAGE_KEY_ACCESS_TOKEN)).toBe(FAKE_ACCESS_TOKEN);
  });

  it("stores the id token under STORAGE_KEY_ID_TOKEN ('idToken')", async () => {
    await signInWithPassword("user@example.com", "correct-password", CONFIG);
    expect(sessionStorage.getItem(STORAGE_KEY_ID_TOKEN)).toBe(FAKE_ID_TOKEN);
  });

  it("stores the token to the correct key names that mirror CMS ('authToken', 'idToken')", async () => {
    await signInWithPassword("user@example.com", "correct-password", CONFIG);
    // Key names are the same as CMS SimpleAuthProvider.tsx — see auth.ts constants.
    expect(sessionStorage.getItem("authToken")).toBe(FAKE_ACCESS_TOKEN);
    expect(sessionStorage.getItem("idToken")).toBe(FAKE_ID_TOKEN);
  });

  it("yields an authorized getSession() after storing the id token", async () => {
    const { getSession, isAuthorized } = await import("../auth");
    await signInWithPassword("user@example.com", "correct-password", CONFIG);
    expect(isAuthorized(getSession())).toBe(true);
  });
});

// ── 2. Wrong credentials — surfaces error, stores nothing ────────────────────

describe("signInWithPassword — wrong credentials", () => {
  beforeEach(() => {
    // Cognito raises NotAuthorizedException for bad credentials
    const err = Object.assign(new Error("Incorrect username or password."), {
      name: "NotAuthorizedException",
    });
    mockSend.mockRejectedValue(err);
  });

  it("returns ok:false", async () => {
    const result = await signInWithPassword("user@example.com", "wrong-password", CONFIG);
    expect(result.ok).toBe(false);
  });

  it("returns a user-facing error message", async () => {
    const result = await signInWithPassword("user@example.com", "wrong-password", CONFIG);
    if (result.ok) throw new Error("Expected ok:false");
    expect(result.message.length).toBeGreaterThan(0);
    // Should NOT leak the raw Cognito message — that confirms a user exists.
    expect(result.message).not.toBe("Incorrect username or password.");
  });

  it("does NOT store anything to sessionStorage on failure", async () => {
    await signInWithPassword("user@example.com", "wrong-password", CONFIG);
    expect(sessionStorage.getItem(STORAGE_KEY_ACCESS_TOKEN)).toBeNull();
    expect(sessionStorage.getItem(STORAGE_KEY_ID_TOKEN)).toBeNull();
  });

  it("does not store authToken on failure", async () => {
    await signInWithPassword("user@example.com", "wrong-password", CONFIG);
    expect(sessionStorage.getItem("authToken")).toBeNull();
  });

  it("does not store idToken on failure", async () => {
    await signInWithPassword("user@example.com", "wrong-password", CONFIG);
    expect(sessionStorage.getItem("idToken")).toBeNull();
  });
});

// ── UserNotFoundException is normalised the same way as NotAuthorizedException ──

describe("signInWithPassword — user not found", () => {
  beforeEach(() => {
    const err = Object.assign(new Error("User does not exist."), {
      name: "UserNotFoundException",
    });
    mockSend.mockRejectedValue(err);
  });

  it("returns ok:false without leaking that the user doesn't exist", async () => {
    const result = await signInWithPassword("nobody@example.com", "any-pw", CONFIG);
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("Expected ok:false");
    // Must not confirm non-existence (username enumeration)
    expect(result.message.toLowerCase()).not.toContain("does not exist");
  });
});

// ── Challenge path ────────────────────────────────────────────────────────────

describe("signInWithPassword — challenge required", () => {
  it("returns ok:false when Cognito issues a challenge", async () => {
    mockSend.mockResolvedValue({
      ChallengeName: "NEW_PASSWORD_REQUIRED",
    });
    const result = await signInWithPassword("user@example.com", "pw", CONFIG);
    expect(result.ok).toBe(false);
  });
});

// ── Enumeration parity: every credential outcome yields ONE message ───────────

describe("signInWithPassword — no username enumeration oracle", () => {
  // UserNotConfirmedException and PasswordResetRequiredException are raised ONLY for
  // accounts that exist. If their messages differ from the wrong-password message, an
  // attacker can enumerate valid accounts. All four must be indistinguishable.
  const CREDENTIAL_ERRORS = [
    ["NotAuthorizedException", "Incorrect username or password."],
    ["UserNotFoundException", "User does not exist."],
    ["UserNotConfirmedException", "User is not confirmed."],
    ["PasswordResetRequiredException", "Password reset required for the user."],
  ] as const;

  it.each(CREDENTIAL_ERRORS)(
    "%s yields the same generic message as a wrong password",
    async (errName, rawMessage) => {
      mockSend.mockRejectedValue(
        Object.assign(new Error(rawMessage), { name: errName })
      );
      const result = await signInWithPassword("user@example.com", "pw", CONFIG);
      expect(result.ok).toBe(false);
      if (result.ok) throw new Error("Expected ok:false");
      expect(result.message).toBe("Incorrect email or password.");
      // The raw SDK text must never reach the user.
      expect(result.message).not.toBe(rawMessage);
    }
  );

  it("produces exactly one distinct message across all credential outcomes", async () => {
    const messages = new Set<string>();
    for (const [errName, rawMessage] of CREDENTIAL_ERRORS) {
      mockSend.mockRejectedValue(
        Object.assign(new Error(rawMessage), { name: errName })
      );
      const result = await signInWithPassword("user@example.com", "pw", CONFIG);
      if (result.ok) throw new Error("Expected ok:false");
      messages.add(result.message);
    }
    expect(messages.size).toBe(1);
  });

  it("does not leak raw SDK detail for unexpected error names", async () => {
    // Configuration and transport faults carry internals — endpoints, request ids, pool
    // config. Detail goes to console for operators; the UI gets a generic message.
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    mockSend.mockRejectedValue(
      Object.assign(
        new Error("Auth flow not enabled for this client: arn:aws:cognito-idp:us-west-2:..."),
        { name: "InvalidParameterException" }
      )
    );
    const result = await signInWithPassword("user@example.com", "pw", CONFIG);
    if (result.ok) throw new Error("Expected ok:false");
    expect(result.message).toBe("Sign-in failed. Please try again.");
    expect(result.message).not.toContain("arn:aws");
    expect(result.message).not.toContain("us-west-2");
    expect(consoleSpy).toHaveBeenCalled();
    consoleSpy.mockRestore();
  });

  it("surfaces throttling distinctly from credential failure", async () => {
    mockSend.mockRejectedValue(
      Object.assign(new Error("Rate exceeded"), { name: "TooManyRequestsException" })
    );
    const result = await signInWithPassword("user@example.com", "pw", CONFIG);
    if (result.ok) throw new Error("Expected ok:false");
    expect(result.message.toLowerCase()).toContain("too many attempts");
  });
});

// ── Partial-session guard ─────────────────────────────────────────────────────

describe("signInWithPassword — requires BOTH tokens", () => {
  it("returns ok:false when Cognito returns an access token but no ID token", async () => {
    // The group claim isAuthorized() reads lives in the ID token, so access-token-only is
    // not an authorized session. Returning ok:true here made SignIn reload into a
    // RequireAuth that found no ID token and bounced straight back, showing no error.
    mockSend.mockResolvedValue({
      AuthenticationResult: { AccessToken: FAKE_ACCESS_TOKEN },
    });
    const result = await signInWithPassword("user@example.com", "pw", CONFIG);
    expect(result.ok).toBe(false);
  });

  it("writes NOTHING to sessionStorage when the ID token is missing", async () => {
    mockSend.mockResolvedValue({
      AuthenticationResult: { AccessToken: FAKE_ACCESS_TOKEN },
    });
    await signInWithPassword("user@example.com", "pw", CONFIG);
    expect(sessionStorage.getItem(STORAGE_KEY_ACCESS_TOKEN)).toBeNull();
    expect(sessionStorage.getItem(STORAGE_KEY_ID_TOKEN)).toBeNull();
  });

  it("returns ok:false when Cognito returns an ID token but no access token", async () => {
    mockSend.mockResolvedValue({
      AuthenticationResult: { IdToken: FAKE_ID_TOKEN },
    });
    const result = await signInWithPassword("user@example.com", "pw", CONFIG);
    expect(result.ok).toBe(false);
    expect(sessionStorage.getItem(STORAGE_KEY_ID_TOKEN)).toBeNull();
  });
});

// ── Region derivation ─────────────────────────────────────────────────────────

describe("signInWithPassword — region derivation", () => {
  it("uses cognitoRegion when present in config", async () => {
    mockSend.mockResolvedValue({
      AuthenticationResult: {
        AccessToken: FAKE_ACCESS_TOKEN,
        IdToken: FAKE_ID_TOKEN,
      },
    });

    await signInWithPassword("user@example.com", "pw", {
      cognitoClientId: "testclientid",
      cognitoRegion: "ap-southeast-1",
      cognitoUserPoolId: "eu-west-1_DOESNOTMATTER",
    });

    expect(clientCtorArgs).toHaveLength(1);
    expect(clientCtorArgs[0]).toEqual(
      expect.objectContaining({ region: "ap-southeast-1" })
    );
  });

  it("falls back to pool-id prefix when cognitoRegion is absent", async () => {
    mockSend.mockResolvedValue({
      AuthenticationResult: {
        AccessToken: FAKE_ACCESS_TOKEN,
        IdToken: FAKE_ID_TOKEN,
      },
    });

    await signInWithPassword("user@example.com", "pw", {
      cognitoClientId: "testclientid",
      cognitoUserPoolId: "eu-west-1_TESTPOOL",
      // cognitoRegion deliberately absent
    });

    expect(clientCtorArgs).toHaveLength(1);
    expect(clientCtorArgs[0]).toEqual(
      expect.objectContaining({ region: "eu-west-1" })
    );
  });

  it("fails closed when no region can be determined", async () => {
    // Earlier revision defaulted to "us-east-1". For a us-west-2 pool that silently
    // produced an opaque SDK error instead of naming the missing configuration, and it
    // sent credentials to the wrong regional endpoint on the way.
    mockSend.mockResolvedValue({
      AuthenticationResult: {
        AccessToken: FAKE_ACCESS_TOKEN,
        IdToken: FAKE_ID_TOKEN,
      },
    });

    const result = await signInWithPassword("user@example.com", "pw", {
      cognitoClientId: "testclientid",
      cognitoUserPoolId: "malformed-no-underscore",
      // cognitoRegion deliberately absent
    });

    expect(result.ok).toBe(false);
    // No client was constructed, so no credential left the browser.
    expect(clientCtorArgs).toHaveLength(0);
    expect(mockSend).not.toHaveBeenCalled();
  });
});
