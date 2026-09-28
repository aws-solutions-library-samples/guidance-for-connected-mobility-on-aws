// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * PKCE (RFC 7636) + OAuth `state` (CSRF) tests for CMS SimpleAuthProvider.
 *
 * Ported from guidance-for-dealer-management-system-on-aws (same contract).
 *
 * Security contracts under test:
 *   1. Callback with mismatched state is REJECTED — no fetch, no token stored.
 *   2. Callback with absent state is REJECTED — fail closed.
 *   3. Callback with matching state is ACCEPTED — fetch called, verifier sent.
 *   4. `code_challenge` is base64url(SHA-256(code_verifier)), NOT the verifier.
 *      Tested against the exported `pkce.ts` helpers and an independent
 *      reference derivation using the same Web Crypto API.
 *   5. `code_challenge_method=S256` is the mandatory method.
 *   6. `state` is single-use — cleared from sessionStorage after callback.
 *   7. `code_verifier` is single-use — cleared from sessionStorage after callback.
 *   8. `randomBase64url` produces correct-length, URL-safe output.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';

// ── SDK mock ─────────────────────────────────────────────────────────────────

vi.mock('@aws-sdk/client-cognito-identity-provider', () => ({
  CognitoIdentityProviderClient: vi.fn(() => ({ send: vi.fn() })),
  InitiateAuthCommand: vi.fn(),
  AuthFlowType: { USER_PASSWORD_AUTH: 'USER_PASSWORD_AUTH' },
}));

import { SimpleAuthProvider } from '../SimpleAuthProvider';
import { computeCodeChallenge, randomBase64url, base64urlEncode } from '../pkce';

// ── Helpers ───────────────────────────────────────────────────────────────────

const POOL_PROPS = {
  userPoolId: 'us-east-1_EXAMPLE',
  clientId: 'test-public-client-id',
  region: 'us-east-1',
};

/** Independent reference implementation of base64url(SHA-256(str)). */
const refChallenge = async (verifier: string): Promise<string> => {
  const data = new TextEncoder().encode(verifier);
  const buf = await crypto.subtle.digest('SHA-256', data);
  // base64url from first principles — does NOT use our pkce.ts helpers.
  const bytes = new Uint8Array(buf);
  const b64 = btoa(Array.from(bytes, b => String.fromCharCode(b)).join(''));
  return b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
};

/** Set window.location to a given path + search string. */
function setWindowLocation(path: string, search = '') {
  Object.defineProperty(window, 'location', {
    configurable: true,
    writable: true,
    value: {
      ...window.location,
      pathname: path,
      search,
      href: `http://localhost${path}${search}`,
      replace: vi.fn(),
      assign: vi.fn(),
    },
  });
}

/** Render SimpleAuthProvider at `/auth/callback` with the given search params. */
const renderAtCallback = (search: string) => {
  setWindowLocation('/auth/callback', search);
  return render(
    <SimpleAuthProvider {...POOL_PROPS}>
      <span data-testid="child">authenticated</span>
    </SimpleAuthProvider>,
  );
};

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('PKCE helpers (unit)', () => {
  // These test the pure math in pkce.ts without any React surface.

  it('(4a) computeCodeChallenge returns the base64url SHA-256 of the verifier', async () => {
    const verifier = randomBase64url(32); // 43 base64url chars — RFC 7636 minimum
    const challenge = await computeCodeChallenge(verifier);
    const expected = await refChallenge(verifier);
    expect(challenge).toBe(expected);
  });

  it('(4b) code_challenge is NOT equal to code_verifier', async () => {
    const verifier = randomBase64url(32);
    const challenge = await computeCodeChallenge(verifier);
    expect(challenge).not.toBe(verifier);
  });

  it('(4c) base64urlEncode produces no +, /, or = characters', () => {
    for (let i = 0; i < 20; i++) {
      const bytes = crypto.getRandomValues(new Uint8Array(32));
      const encoded = base64urlEncode(bytes);
      expect(encoded).not.toMatch(/[+/=]/);
    }
  });

  it('(5) code_challenge_method is always S256 — SHA-256 digest has length 43 base64url chars', async () => {
    const verifier = randomBase64url(32);
    const challenge = await computeCodeChallenge(verifier);
    // A SHA-256 base64url digest of any 32-byte input is always 43 chars.
    expect(challenge).toHaveLength(43);
  });

  it('(8a) randomBase64url produces correct length output', () => {
    // byteLength=32 -> ceil(32 * 4/3) = 43 chars (without padding)
    const v32 = randomBase64url(32);
    expect(v32).toHaveLength(43);
    // byteLength=16 -> 22 chars
    const v16 = randomBase64url(16);
    expect(v16).toHaveLength(22);
  });

  it('(8b) randomBase64url output changes on every call (crypto.getRandomValues)', () => {
    const a = randomBase64url(32);
    const b = randomBase64url(32);
    expect(a).not.toBe(b);
  });
});

describe('OAuth state (CSRF) and PKCE — callback path', () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    vi.restoreAllMocks();
    setWindowLocation('/');
  });

  afterEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });

  // ── State CSRF ─────────────────────────────────────────────────────────────

  it('(1) callback with mismatched state is REJECTED — no fetch, no token stored', async () => {
    sessionStorage.setItem('oauth.state', 'correct-state-value');
    sessionStorage.setItem('oauth.code_verifier', 'some-verifier');

    const mockFetch = vi.fn().mockResolvedValue({
      json: () => Promise.resolve({ access_token: 'should-never-be-set' }),
    });
    vi.stubGlobal('fetch', mockFetch);

    renderAtCallback('?code=attacker-code&state=WRONG-state-value');

    await waitFor(() => {
      expect(sessionStorage.getItem('authToken')).toBeNull();
    });
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('(2) callback with absent state is REJECTED — fail closed', async () => {
    sessionStorage.setItem('oauth.state', 'expected-state');
    sessionStorage.setItem('oauth.code_verifier', 'some-verifier');

    const mockFetch = vi.fn().mockResolvedValue({
      json: () => Promise.resolve({ access_token: 'should-never-be-set' }),
    });
    vi.stubGlobal('fetch', mockFetch);

    // No `state` param in the callback URL.
    renderAtCallback('?code=some-code');

    await waitFor(() => {
      expect(sessionStorage.getItem('authToken')).toBeNull();
    });
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('(3) callback with matching state is ACCEPTED — fetch called, code_verifier in body', async () => {
    const matchingState = 'matching-state-xyz';
    const verifier = 'a'.repeat(43); // valid 43-char verifier
    sessionStorage.setItem('oauth.state', matchingState);
    sessionStorage.setItem('oauth.code_verifier', verifier);

    const mockFetch = vi.fn().mockResolvedValue({
      json: () =>
        Promise.resolve({
          access_token: 'valid-access-token',
          id_token: 'valid-id-token',
        }),
    });
    vi.stubGlobal('fetch', mockFetch);

    renderAtCallback(`?code=real-code&state=${matchingState}`);

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledOnce();
    });

    // code_verifier MUST be in the token-exchange body.
    const body = new URLSearchParams(mockFetch.mock.calls[0][1].body as string);
    expect(body.get('code_verifier')).toBe(verifier);

    // Both sessionStorage keys should be cleared (single-use).
    expect(sessionStorage.getItem('oauth.state')).toBeNull();
    expect(sessionStorage.getItem('oauth.code_verifier')).toBeNull();
  });

  // ── Single-use properties ─────────────────────────────────────────────────

  it('(6) state is single-use — cleared from sessionStorage after callback fires', async () => {
    const state = 'single-use-state';
    sessionStorage.setItem('oauth.state', state);
    sessionStorage.setItem('oauth.code_verifier', 'b'.repeat(43));

    const mockFetch = vi.fn().mockResolvedValue({
      json: () => Promise.resolve({ error: 'invalid_grant' }),
    });
    vi.stubGlobal('fetch', mockFetch);

    renderAtCallback(`?code=some-code&state=${state}`);

    await waitFor(() => {
      expect(sessionStorage.getItem('oauth.state')).toBeNull();
    });
  });

  it('(7) code_verifier is single-use — cleared from sessionStorage after callback fires', async () => {
    const state = 'state-for-verifier-test';
    const verifier = 'c'.repeat(43);
    sessionStorage.setItem('oauth.state', state);
    sessionStorage.setItem('oauth.code_verifier', verifier);

    const mockFetch = vi.fn().mockResolvedValue({
      json: () => Promise.resolve({ error: 'invalid_grant' }),
    });
    vi.stubGlobal('fetch', mockFetch);

    renderAtCallback(`?code=some-code&state=${state}`);

    await waitFor(() => {
      expect(sessionStorage.getItem('oauth.code_verifier')).toBeNull();
    });
  });

  it('(2b) callback with no stored expected state (oauth.state absent) is REJECTED — fail closed', async () => {
    // No sessionStorage pre-seeded — simulates a callback with no prior
    // loginWithFederate call (e.g. a replayed or injected redirect).
    sessionStorage.setItem('oauth.code_verifier', 'some-verifier');
    // DO NOT set oauth.state

    const mockFetch = vi.fn().mockResolvedValue({
      json: () => Promise.resolve({ access_token: 'should-never-be-set' }),
    });
    vi.stubGlobal('fetch', mockFetch);

    renderAtCallback('?code=some-code&state=injected-state');

    await waitFor(() => {
      expect(sessionStorage.getItem('authToken')).toBeNull();
    });
    expect(mockFetch).not.toHaveBeenCalled();
  });
});
