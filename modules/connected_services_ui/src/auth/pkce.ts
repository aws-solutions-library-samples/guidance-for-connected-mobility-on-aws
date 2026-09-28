// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * PKCE (Proof Key for Code Exchange — RFC 7636) helpers.
 *
 * Spec: .kiro/specs/2026-09-05-cms-connected-services-auth-integration/ § D1, H2.1
 *
 * ── Provenance ──────────────────────────────────────────────────────────────
 * Ported from the sibling DMS repository, verbatim apart from comments:
 *   repo:   guidance-for-dealer-management-system-on-aws
 *   path:   frontend/src/auth/pkce.ts
 *   commit: ffec018  (HEAD of `main` at 2026-09-05)
 *
 * Ported rather than imported: the source lives in a different git repository and a
 * cross-repo import is not something either build can express. § D1 chose porting over
 * adding `@aws-amplify/auth` — this file is 40 lines and adds no dependency, so neither
 * the 7-day quarantine nor the pinning rules in
 * ~/.kiro/steering/dependency-versions.md come into play.
 *
 * DMS was chosen as the source over CMS's own `SimpleAuthProvider.tsx` because CMS's
 * has no PKCE and no `state` to port. See docs/tech.md § (o) for the full comparison.
 * ────────────────────────────────────────────────────────────────────────────
 *
 * Web Crypto only. Deliberately free of application concerns so the algorithms are
 * independently testable against RFC 7636 Appendix B's published vector — see
 * `__tests__/pkce.test.ts`, which asserts a literal rather than re-deriving the
 * expectation by calling this module.
 */

/**
 * Base64url-encode a byte array without padding (RFC 4648 § 5).
 *
 * The `-`/`_` alphabet and the absence of `=` padding are both required: a standard
 * base64 `code_challenge` round-trips fine locally and is rejected only by the real
 * authorization server, which is the worst place to discover it.
 */
export const base64urlEncode = (bytes: Uint8Array): string =>
  btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=/g, "");

/**
 * Generate a cryptographically random base64url string from `byteLength` bytes.
 *
 * Used for both the PKCE `code_verifier` (32 bytes → 43 characters, the minimum that
 * satisfies RFC 7636 § 4.1's 43–128 range) and the OAuth `state` (16 bytes).
 *
 * `crypto.getRandomValues` only. A non-cryptographic PRNG must never be substituted: a
 * predictable verifier defeats PKCE and a predictable `state` defeats the CSRF check.
 *
 * H2.1 greps this file to prove the standard non-crypto PRNG is absent, so that
 * function's name is deliberately not written anywhere here — naming it in a comment
 * warning against it would trip the very check that exists to detect it. Same shape as
 * a denylist that embeds the value it guards
 * (~/.kiro/steering/public-mirror-publish.md § "The exclusion-mechanism rule").
 */
export const randomBase64url = (byteLength: number): string =>
  base64urlEncode(crypto.getRandomValues(new Uint8Array(byteLength)));

/**
 * Compute `base64url(SHA-256(codeVerifier))` — the PKCE `code_challenge`.
 *
 * Async because `SubtleCrypto.digest` is. The corresponding
 * `code_challenge_method` is always `S256`; RFC 7636 also permits `plain`, which
 * offers no protection against anyone able to observe the authorize request and is
 * never used here.
 */
export const computeCodeChallenge = async (codeVerifier: string): Promise<string> => {
  const data = new TextEncoder().encode(codeVerifier);
  const hashBuffer = await crypto.subtle.digest("SHA-256", data);
  return base64urlEncode(new Uint8Array(hashBuffer));
};
