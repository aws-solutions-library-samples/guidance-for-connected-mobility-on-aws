// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Fail-closed authorization for the Connected Services portal.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/ T9.1
 *
 * UI route-guard rule: a persona must be a member of the "connected-services"
 * Cognito group for the portal to render any route. A persona lacking that group is
 * shown an explicit Unauthorized surface rather than an empty page.
 *
 * ⚠ THIS IS A UI CONTROL, NOT AN AUTHORIZATION BOUNDARY. The group check below runs
 * in the browser against a token this code decodes but does not verify. It decides what
 * to render; it decides nothing about what a caller may do.
 *
 * Every backend endpoint this portal calls MUST enforce its own check server-side — an
 * API Gateway Cognito authorizer for token validity, plus an in-handler
 * `cognito:groups` check for the group. No backend is wired today, which is exactly why
 * this is written down now: the next engineer to add one will read the guard below and
 * could reasonably conclude the check is already covered. It is not.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * T9.1 session read path — JWT from sessionStorage:
 *
 *   getSession() reads sessionStorage key 'idToken', decodes the JWT payload
 *   (no signature verification — Cognito signs; we decode and trust), checks
 *   exp, and extracts the 'email' and 'cognito:groups' claims.
 *
 *   Absent token    → null  (unauthenticated, redirect to sign-in)
 *   Expired token   → null  (fail-closed)
 *   Malformed token → null  (fail-closed, no throw)
 *   Valid token     → Session with alias from email, groups from cognito:groups
 *
 * This mirrors CMS SimpleAuthProvider.tsx:302-352 in its storage keys
 * ('authToken', 'idToken', 'preAuthUrl') and endpoints.
 *
 * ── PKCE + state added 2026-09-05 ──────────────────────────────────────────
 * Spec: .kiro/specs/2026-09-05-cms-connected-services-auth-integration/ H3.1
 *
 * The flow now carries RFC 7636 PKCE (S256) and an OAuth `state` CSRF token, both
 * single-use in sessionStorage. This REVERSES the "no PKCE — match CMS to avoid
 * divergence" decision recorded for T9.1: that call was made while the user was
 * unavailable to UAT a divergent redirect flow, and was overruled by explicit
 * direction once the flow was working.
 *
 * Two things are worth knowing before touching this file:
 *
 *   - `state` is what binds the callback to a sign-in that started in THIS browser.
 *     Without it `handleOAuthCallback` will redeem any `code` placed in the URL,
 *     which is login CSRF / authorization-code injection. Validation happens before
 *     the token request, not after, so an injected code is never redeemed at all.
 *
 *   - PKCE is what binds the code to THIS client. The app client is public and sends
 *     no secret in this flow, so the verifier is the only such binding.
 *
 * CMS's own SimpleAuthProvider has neither and remains the weaker sibling; DMS's has
 * both and is what this was ported from. See docs/tech.md § (o).
 * ─────────────────────────────────────────────────────────────────────────
 */

import { type RuntimeConfig } from "./env";
import { computeCodeChallenge, randomBase64url } from "./auth/pkce";
import {
  CognitoIdentityProviderClient,
  InitiateAuthCommand,
  AuthFlowType,
} from "@aws-sdk/client-cognito-identity-provider";

/** The Cognito group that grants access to the connected-services portal. */
export const CONNECTED_SERVICES_GROUP = "connected-services" as const;

// ── sessionStorage keys ── mirror CMS SimpleAuthProvider exactly
export const STORAGE_KEY_ID_TOKEN = "idToken" as const;
export const STORAGE_KEY_ACCESS_TOKEN = "authToken" as const;
export const STORAGE_KEY_PRE_AUTH_URL = "preAuthUrl" as const;

// ── OAuth single-use values (spec 2026-09-05-...-auth-integration H3.1) ──────
// Key names match DMS's SimpleAuthProvider.tsx:306,315 so the two portals' storage
// surfaces read as one pattern. Both are consumed on every callback path.
export const STORAGE_KEY_CODE_VERIFIER = "oauth.code_verifier" as const;
export const STORAGE_KEY_OAUTH_STATE = "oauth.state" as const;

/** Byte lengths for the two random values. 32 → 43 base64url chars (RFC 7636 § 4.1). */
const CODE_VERIFIER_BYTES = 32;
const STATE_BYTES = 16;

/** PKCE + CSRF parameters that `buildAuthorizeUrl` threads into the authorize request. */
export interface AuthorizeSecurityParams {
  /** Opaque CSRF token, echoed back by Cognito and verified on the callback. */
  readonly state: string;
  /** base64url(SHA-256(code_verifier)). Never the verifier itself. */
  readonly codeChallenge: string;
}

/** Shape of the session derived from the Cognito id-token JWT claims. */
export interface Session {
  alias: string;
  groups: readonly string[];
}

/**
 * Decoded subset of a Cognito id-token JWT payload.
 * Only the claims we actually use are listed; others are ignored.
 */
interface IdTokenPayload {
  sub?: string;
  email?: string;
  "cognito:groups"?: string[];
  exp?: number;
  [key: string]: unknown;
}

/**
 * Decode the payload section of a JWT without verifying the signature.
 *
 * Cognito signs tokens on the server; the browser decodes and trusts them.
 * We never send the token back with elevated privilege — we only use it to
 * derive the session for client-side routing decisions.
 *
 * Returns null on any decode error so callers never need to catch.
 */
function decodeJwtPayload(token: string): IdTokenPayload | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const payload = parts[1];
    // base64url → base64 (pad if needed, swap URL-safe chars)
    const base64 = payload.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), "=");
    const jsonStr = atob(padded);
    return JSON.parse(jsonStr) as IdTokenPayload;
  } catch {
    return null;
  }
}

/**
 * Return the current session derived from the Cognito id-token in
 * sessionStorage, or null if absent, expired, or malformed.
 *
 * Fail-closed in all error cases — null means unauthenticated.
 */
export function getSession(): Session | null {
  let rawToken: string | null = null;
  try {
    rawToken = sessionStorage.getItem(STORAGE_KEY_ID_TOKEN);
  } catch {
    return null;
  }

  if (!rawToken) return null;

  const payload = decodeJwtPayload(rawToken);
  if (!payload) return null;

  // Expired token — fail-closed
  const now = Math.floor(Date.now() / 1000);
  if (typeof payload.exp === "number" && payload.exp < now) return null;

  const alias = payload.email ?? payload.sub ?? "unknown";
  const groups: string[] = Array.isArray(payload["cognito:groups"])
    ? (payload["cognito:groups"] as string[])
    : [];

  return { alias, groups };
}

/**
 * Return true iff the current session is authorised to access the portal.
 *
 * Fail-closed: a null session, an empty groups list, or a session without
 * "connected-services" in its groups all return false.
 */
export function isAuthorized(session: Session | null): boolean {
  if (session == null) return false;
  return session.groups.includes(CONNECTED_SERVICES_GROUP);
}

/**
 * Build the Cognito Hosted UI authorization URL.
 *
 * Parameters:
 *   - response_type: 'code'
 *   - identity_provider: 'AmazonFederate'
 *   - scope: 'openid email profile'
 *   - redirect_uri: <origin>/auth/callback
 *   - state + code_challenge + code_challenge_method=S256
 *
 * `security` is a REQUIRED second argument, not an optional one. That is deliberate:
 * an optional parameter makes "no state, no PKCE" a default reachable by forgetting an
 * argument, which is how the control gets lost again. Omitting it is a compile error.
 *
 * Pure and synchronous — it neither generates nor persists the values it embeds, so it
 * can be asserted on directly. `beginFederateSignIn` owns the async, side-effecting
 * half.
 */
export function buildAuthorizeUrl(
  config: Pick<RuntimeConfig, "cognitoClientId" | "cognitoDomain">,
  security: AuthorizeSecurityParams
): string {
  const redirectUri = `${window.location.origin}/auth/callback`;
  const params = new URLSearchParams({
    response_type: "code",
    client_id: config.cognitoClientId,
    redirect_uri: redirectUri,
    identity_provider: "AmazonFederate",
    scope: "openid email profile",
    state: security.state,
    code_challenge: security.codeChallenge,
    code_challenge_method: "S256",
  });
  return `https://${config.cognitoDomain}/oauth2/authorize?${params.toString()}`;
}

/**
 * Generate and persist the PKCE verifier and CSRF state, then return the authorize URL.
 *
 * Order matters: both values are written to `sessionStorage` **before** the URL is
 * returned, so a caller that redirects immediately cannot race the persistence.
 *
 * Also records the pre-auth path so the callback can return the user where they were.
 */
export async function beginFederateSignIn(
  config: Pick<RuntimeConfig, "cognitoClientId" | "cognitoDomain">
): Promise<string> {
  const codeVerifier = randomBase64url(CODE_VERIFIER_BYTES);
  const state = randomBase64url(STATE_BYTES);
  const codeChallenge = await computeCodeChallenge(codeVerifier);

  sessionStorage.setItem(STORAGE_KEY_CODE_VERIFIER, codeVerifier);
  sessionStorage.setItem(STORAGE_KEY_OAUTH_STATE, state);

  if (window.location.pathname !== "/auth/callback") {
    sessionStorage.setItem(
      STORAGE_KEY_PRE_AUTH_URL,
      window.location.pathname + window.location.search
    );
  }

  return buildAuthorizeUrl(config, { state, codeChallenge });
}

/**
 * Consume the stored single-use OAuth values, clearing them unconditionally.
 *
 * Called before any validation so both are consumed on *every* path, success or
 * failure. Leaving them in place after a rejection would let an attacker retry against
 * a still-live verifier until they guessed the state.
 */
function consumeOAuthSingleUseValues(): {
  storedState: string | null;
  codeVerifier: string | null;
} {
  let storedState: string | null = null;
  let codeVerifier: string | null = null;
  try {
    storedState = sessionStorage.getItem(STORAGE_KEY_OAUTH_STATE);
    codeVerifier = sessionStorage.getItem(STORAGE_KEY_CODE_VERIFIER);
    sessionStorage.removeItem(STORAGE_KEY_OAUTH_STATE);
    sessionStorage.removeItem(STORAGE_KEY_CODE_VERIFIER);
  } catch {
    // Storage unavailable — treat as absent, which fails closed below.
  }
  return { storedState, codeVerifier };
}

/**
 * Reduce a stored pre-auth URL to a path that cannot leave this origin.
 *
 * `//evil.example/x` is the case that makes this a real guard rather than a cosmetic
 * one: it satisfies a naive `startsWith('/')` check and the browser treats it as an
 * absolute URL on the current protocol, so the post-login navigation becomes an open
 * redirect. Ported from DMS `SimpleAuthProvider.tsx:346`, which the v2 port dropped.
 */
function safeReturnPath(candidate: string | null): string {
  if (!candidate) return "/";
  if (!candidate.startsWith("/")) return "/";
  if (candidate.startsWith("//")) return "/";
  return candidate;
}

/**
 * Initiate the Federate sign-in redirect.
 *
 * Saves the current URL to sessionStorage.preAuthUrl (mirroring CMS) and
 * redirects to the Hosted UI authorization endpoint.
 */
/**
 * Initiate the Federate sign-in redirect.
 *
 * Async because the PKCE challenge is a digest. Callers must handle rejection — a
 * failure here must leave the user on the sign-in screen rather than navigating to a
 * malformed authorize URL.
 */
export async function redirectToFederate(
  config: Pick<RuntimeConfig, "cognitoClientId" | "cognitoDomain">
): Promise<void> {
  window.location.href = await beginFederateSignIn(config);
}

/**
 * Handle the OAuth authorization code callback.
 *
 * Exchanges the code at /oauth2/token, stores access_token as 'authToken' and
 * id_token as 'idToken' (same keys as CMS SimpleAuthProvider), then returns
 * the pre-auth URL to navigate to.
 *
 * Mirrors CMS SimpleAuthProvider.tsx token-exchange fetch exactly:
 *   POST /oauth2/token
 *   Content-Type: application/x-www-form-urlencoded
 *   body: grant_type=authorization_code, client_id, code, redirect_uri
 *
 * Returns the URL to navigate to after success, or throws on failure.
 */
export async function handleOAuthCallback(
  code: string,
  incomingState: string | null,
  config: Pick<RuntimeConfig, "cognitoClientId" | "cognitoDomain">
): Promise<string> {
  const { storedState, codeVerifier } = consumeOAuthSingleUseValues();

  // ── CSRF: state must be present on both sides and must match ───────────────
  // Empty string is rejected explicitly. A bare `stored === incoming` would treat
  // "" === "" as a match, so a caller who can clear sessionStorage and omit the
  // parameter would pass the check.
  if (!storedState || !incomingState || storedState !== incomingState) {
    throw new Error(
      "OAuth state validation failed — the callback is not bound to a sign-in " +
        "started in this browser. Refusing to exchange the authorization code."
    );
  }

  if (!codeVerifier) {
    throw new Error(
      "OAuth code_verifier is missing — cannot complete the PKCE exchange. " +
        "Start sign-in again."
    );
  }

  const redirectUri = `${window.location.origin}/auth/callback`;
  const response = await fetch(`https://${config.cognitoDomain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: config.cognitoClientId,
      code,
      redirect_uri: redirectUri,
      code_verifier: codeVerifier,
    }),
  });

  // A non-JSON body (an HTML error page from an edge, say) must surface as a failed
  // exchange rather than an unhandled SyntaxError. Reviewer Cycle 1 suggestion 2.
  let data: Record<string, unknown>;
  try {
    data = (await response.json()) as Record<string, unknown>;
  } catch {
    throw new Error("Token exchange failed — token endpoint returned a non-JSON body");
  }

  if (!data.access_token) {
    throw new Error("Token exchange failed — no access_token in response");
  }

  sessionStorage.setItem(STORAGE_KEY_ACCESS_TOKEN, data.access_token as string);
  if (data.id_token) {
    sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, data.id_token as string);
  }

  const preAuthUrl = sessionStorage.getItem(STORAGE_KEY_PRE_AUTH_URL);
  sessionStorage.removeItem(STORAGE_KEY_PRE_AUTH_URL);
  return safeReturnPath(preAuthUrl);
}

/**
 * Sign in with email and password using Cognito USER_PASSWORD_AUTH flow.
 *
 * Mirrors CMS SimpleAuthProvider.tsx `login()` (lines 302-352):
 *   - CognitoIdentityProviderClient + InitiateAuthCommand
 *   - AuthFlowType.USER_PASSWORD_AUTH
 *   - Stores access_token as 'authToken' and id_token as 'idToken' to sessionStorage
 *     (same storage keys as the Federate path)
 *
 * Config reads `cognitoClientId` and `cognitoRegion` from getRuntimeConfig().
 * If `cognitoRegion` is not set in the runtime config, falls back to deriving
 * the region from the first segment of `cognitoUserPoolId` (same logic used by
 * the CDK stack when building runtime-config.js).
 *
 * Returns { ok: true } on success or { ok: false, message } on failure.
 * Never throws — callers should check the returned `ok` flag.
 */
export async function signInWithPassword(
  email: string,
  password: string,
  config: Pick<RuntimeConfig, "cognitoClientId" | "cognitoRegion" | "cognitoUserPoolId">
): Promise<{ ok: true } | { ok: false; message: string }> {
  // Derive the region: prefer explicit cognitoRegion, fall back to pool-id prefix.
  // If neither is available, fail closed. An earlier revision defaulted to "us-east-1",
  // which for this deployment (us-west-2) would have produced an opaque SDK error
  // instead of naming the missing configuration.
  const region =
    config.cognitoRegion ??
    (config.cognitoUserPoolId.includes("_")
      ? config.cognitoUserPoolId.split("_")[0]
      : undefined);

  if (region === undefined || region === "") {
    return {
      ok: false,
      message: "Sign-in is not configured correctly. Please contact support.",
    };
  }

  try {
    const client = new CognitoIdentityProviderClient({ region });
    const command = new InitiateAuthCommand({
      AuthFlow: AuthFlowType.USER_PASSWORD_AUTH,
      ClientId: config.cognitoClientId,
      AuthParameters: {
        USERNAME: email,
        PASSWORD: password,
      },
    });

    const response = await client.send(command);

    const accessToken = response.AuthenticationResult?.AccessToken;
    const idTokenValue = response.AuthenticationResult?.IdToken;

    // BOTH tokens are required. The group claim that isAuthorized() checks lives in the
    // ID token, so an access-token-only result is not an authorized session. Writing the
    // access token alone and returning ok:true made SignIn reload into a RequireAuth that
    // found no ID token, bouncing back to this screen with no error shown — a silent loop.
    // Nothing is written unless the session will actually be usable.
    if (accessToken && idTokenValue) {
      sessionStorage.setItem(STORAGE_KEY_ACCESS_TOKEN, accessToken);
      sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, idTokenValue);
      return { ok: true };
    }

    if (response.ChallengeName) {
      // Reaching a challenge requires correct credentials, so naming it does not help an
      // attacker enumerate accounts.
      return {
        ok: false,
        message: `Additional sign-in steps are required (${response.ChallengeName}). Please contact support.`,
      };
    }

    return { ok: false, message: "Sign-in failed. Please try again." };
  } catch (err: unknown) {
    const name: string =
      err != null && typeof err === "object" && "name" in err
        ? String((err as { name: unknown }).name)
        : "";

    // Every credential-related outcome returns the SAME message. UserNotConfirmed and
    // PasswordResetRequired are only raised for accounts that EXIST, so distinguishing
    // them — or returning the raw SDK text, which says "User is not confirmed." — is a
    // username-enumeration oracle. An earlier revision handled only NotAuthorized and
    // UserNotFound and returned `err.message` for everything else, which leaked both
    // these cases and arbitrary SDK internals (endpoints, request ids, pool config).
    if (
      name === "NotAuthorizedException" ||
      name === "UserNotFoundException" ||
      name === "UserNotConfirmedException" ||
      name === "PasswordResetRequiredException"
    ) {
      return { ok: false, message: "Incorrect email or password." };
    }

    if (name === "TooManyRequestsException" || name === "LimitExceededException") {
      return { ok: false, message: "Too many attempts. Please wait and try again." };
    }

    // Everything else is a configuration or transport fault. Keep the detail in the
    // console for operators; do not render it.
    // eslint-disable-next-line no-console
    console.error("signInWithPassword failed", err);
    return { ok: false, message: "Sign-in failed. Please try again." };
  }
}

/**
 * Sign out: clear session tokens from sessionStorage and redirect to the
 * Cognito Hosted UI logout endpoint.
 */
export function signOut(config: Pick<RuntimeConfig, "cognitoClientId" | "cognitoDomain">): void {
  sessionStorage.removeItem(STORAGE_KEY_ID_TOKEN);
  sessionStorage.removeItem(STORAGE_KEY_ACCESS_TOKEN);
  sessionStorage.removeItem(STORAGE_KEY_PRE_AUTH_URL);
  // Clear in-flight OAuth values too, so signing out mid-flow leaves nothing behind
  // for a later callback to consume.
  sessionStorage.removeItem(STORAGE_KEY_OAUTH_STATE);
  sessionStorage.removeItem(STORAGE_KEY_CODE_VERIFIER);

  const logoutUri = `${window.location.origin}/`;
  const params = new URLSearchParams({
    client_id: config.cognitoClientId,
    logout_uri: logoutUri,
  });
  window.location.href = `https://${config.cognitoDomain}/logout?${params.toString()}`;
}
