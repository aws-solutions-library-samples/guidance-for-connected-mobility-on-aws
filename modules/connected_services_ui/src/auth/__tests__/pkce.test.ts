// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * PKCE (RFC 7636) helper tests.
 *
 * Spec: .kiro/specs/2026-09-05-cms-connected-services-auth-integration/ H1.1, H2.1
 *
 * The S256 expectation is the test vector from RFC 7636 Appendix B itself, as a
 * literal. That matters: computing the expectation by calling the code under test
 * proves only that the function agrees with itself. An external, authoritative vector
 * proves it computes the algorithm the authorization server will compute.
 *
 * A second, independent re-derivation is included as well. It reaches the same answer
 * through raw Web Crypto without touching pkce.ts, so a bug in our base64url encoder
 * cannot hide behind a matching bug in our expectation.
 */

import { describe, expect, it } from "vitest";
import { base64urlEncode, computeCodeChallenge, randomBase64url } from "../pkce";

// ---------------------------------------------------------------------------
// RFC 7636 Appendix B — the specification's own worked example.
// https://datatracker.ietf.org/doc/html/rfc7636#appendix-B
// ---------------------------------------------------------------------------

const RFC7636_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
const RFC7636_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM";

/**
 * Independent reference implementation of base64url(SHA-256(ascii)).
 *
 * Deliberately does NOT import from pkce.ts — it re-derives from raw Web Crypto so
 * this test cannot pass by two matching bugs.
 */
async function referenceChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier)
  );
  const bytes = new Uint8Array(digest);
  return btoa(Array.from(bytes, (b) => String.fromCharCode(b)).join(""))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=/g, "");
}

// ---------------------------------------------------------------------------
// computeCodeChallenge — the S256 transformation
// ---------------------------------------------------------------------------

describe("computeCodeChallenge — S256", () => {
  it("matches RFC 7636 Appendix B's literal test vector", async () => {
    const challenge = await computeCodeChallenge(RFC7636_VERIFIER);
    expect(
      challenge,
      "The S256 challenge does not match RFC 7636 Appendix B. Cognito computes " +
        "base64url(SHA-256(verifier)) server-side and compares; a mismatch here means " +
        "every token exchange will be rejected as invalid_grant."
    ).toBe(RFC7636_CHALLENGE);
  });

  it("agrees with an independent Web Crypto re-derivation", async () => {
    const [ours, reference] = await Promise.all([
      computeCodeChallenge(RFC7636_VERIFIER),
      referenceChallenge(RFC7636_VERIFIER),
    ]);
    expect(ours).toBe(reference);
  });

  it("is base64url — no '+', '/', or '=' padding", async () => {
    // A base64 (not base64url) challenge is the classic PKCE bug: it round-trips
    // locally and fails only against the real authorization server.
    const challenge = await computeCodeChallenge(RFC7636_VERIFIER);
    expect(challenge).not.toMatch(/[+/=]/);
  });

  it("produces a 43-character challenge (256 bits, base64url, unpadded)", async () => {
    const challenge = await computeCodeChallenge(RFC7636_VERIFIER);
    expect(challenge).toHaveLength(43);
  });

  it("is deterministic for a given verifier", async () => {
    const a = await computeCodeChallenge(RFC7636_VERIFIER);
    const b = await computeCodeChallenge(RFC7636_VERIFIER);
    expect(a).toBe(b);
  });

  it("differs for different verifiers", async () => {
    const a = await computeCodeChallenge(RFC7636_VERIFIER);
    const b = await computeCodeChallenge(`${RFC7636_VERIFIER}x`);
    expect(a).not.toBe(b);
  });
});

// ---------------------------------------------------------------------------
// randomBase64url — verifier and state generation
// ---------------------------------------------------------------------------

describe("randomBase64url", () => {
  it("produces 43 characters from 32 bytes — inside RFC 7636's 43-128 range", () => {
    // RFC 7636 § 4.1: code_verifier is 43-128 characters. 32 random bytes is the
    // minimum that reaches 43 base64url characters.
    expect(randomBase64url(32)).toHaveLength(43);
  });

  it("emits only the RFC 7636 unreserved character set", () => {
    // RFC 7636 § 4.1 permits ALPHA / DIGIT / "-" / "." / "_" / "~".
    // base64url yields a subset: ALPHA / DIGIT / "-" / "_".
    for (let i = 0; i < 32; i++) {
      expect(randomBase64url(32)).toMatch(/^[A-Za-z0-9\-_]+$/);
    }
  });

  it("does not pad", () => {
    expect(randomBase64url(16)).not.toContain("=");
    expect(randomBase64url(32)).not.toContain("=");
  });

  it("returns a different value on each call", () => {
    // A verifier that repeats defeats PKCE: an intercepted code could be replayed
    // with a previously observed verifier.
    const seen = new Set<string>();
    for (let i = 0; i < 64; i++) {
      seen.add(randomBase64url(32));
    }
    expect(
      seen.size,
      "randomBase64url returned a duplicate across 64 calls — it is not drawing " +
        "from a cryptographic source."
    ).toBe(64);
  });

  it("two successive state values differ", () => {
    expect(randomBase64url(16)).not.toBe(randomBase64url(16));
  });
});

// ---------------------------------------------------------------------------
// base64urlEncode — the shared primitive
// ---------------------------------------------------------------------------

describe("base64urlEncode", () => {
  it("maps the base64 '+' and '/' alphabet to '-' and '_'", () => {
    // 0xFB 0xFF encodes to "+/8=" in standard base64 → "-_8" in base64url.
    expect(base64urlEncode(new Uint8Array([0xfb, 0xff]))).toBe("-_8");
  });

  it("strips padding", () => {
    expect(base64urlEncode(new Uint8Array([0x00]))).not.toContain("=");
  });

  it("round-trips a known byte sequence", () => {
    // "Hello" → "SGVsbG8"
    expect(base64urlEncode(new TextEncoder().encode("Hello"))).toBe("SGVsbG8");
  });
});
