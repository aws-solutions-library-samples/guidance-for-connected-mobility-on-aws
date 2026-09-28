// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * PKCE (Proof Key for Code Exchange — RFC 7636) helpers.
 *
 * Web Crypto only — no third-party dependencies.
 * Extracted from SimpleAuthProvider so the algorithms are independently testable.
 */

/** Base64url-encode a byte array without padding. */
export const base64urlEncode = (bytes: Uint8Array): string =>
  btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=/g, '');

/** Generate a cryptographically random base64url string of `byteLength` bytes. */
export const randomBase64url = (byteLength: number): string =>
  base64urlEncode(crypto.getRandomValues(new Uint8Array(byteLength)));

/**
 * Compute base64url(SHA-256(asciiString)) — the PKCE `code_challenge`.
 * Returns a Promise because SubtleCrypto.digest is async.
 *
 * The `code_challenge_method` is always `S256`.
 */
export const computeCodeChallenge = async (codeVerifier: string): Promise<string> => {
  const encoder = new TextEncoder();
  const data = encoder.encode(codeVerifier);
  const hashBuffer = await crypto.subtle.digest('SHA-256', data);
  return base64urlEncode(new Uint8Array(hashBuffer));
};
