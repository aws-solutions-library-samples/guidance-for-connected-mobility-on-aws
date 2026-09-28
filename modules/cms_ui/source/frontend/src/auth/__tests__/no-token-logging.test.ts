// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * No Cognito token material may be passed to `console` from the auth path.
 *
 * Issue: `issues/2026-09-01-cms-auth-token-logging-and-oauth-hardening/`.
 * `SimpleAuthProvider` logged the entire `InitiateAuthCommand` response —
 * `AccessToken`, `IdToken` **and** the long-lived `RefreshToken` — on every
 * successful sign-in. The Cognito pool is shared with DMS, so a leaked refresh
 * token grants continued authentication against both products. The string was
 * verified to survive bundling, so it was reachable in production by any browser
 * extension, screen share, session-replay/RUM agent or XSS foothold.
 *
 * Why this is a SOURCE assertion rather than a behavioural one. Driving the real
 * `login()` path under jsdom needs the provider to render `children` (it renders
 * its own sign-in screen while unauthenticated) and needs the mocked AWS client
 * to be reached; no existing test in this suite exercises that path, and an
 * attempt to add one did not reach `client.send`. A test that does not actually
 * run the code it claims to guard is worse than no test — it reports safety it
 * cannot deliver. This assertion is narrower but it is honest, it runs in
 * milliseconds, and it catches the regression class that actually occurred:
 * handing a token-bearing object to `console`.
 *
 * The complementary real-artifact check is the built-bundle grep documented in
 * the issue's `summary.md` — that one covers "did it survive bundling", which no
 * source assertion can answer.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = join(__dirname, '..', 'SimpleAuthProvider.tsx');
const source = readFileSync(SRC, 'utf8');

/** Console calls, with their full argument list, as written in the source. */
const consoleCalls = (text: string): string[] =>
  text.match(/console\.(?:log|debug|info|warn|error|trace)\([^;]*\)/g) ?? [];

/** Identifiers that hold, or transitively hold, Cognito token material. */
const TOKEN_BEARING = [
  'response',
  'AuthenticationResult',
  'accessToken',
  'idTokenValue',
  'RefreshToken',
  'codeVerifier',
  'code_verifier',
];

describe('auth path does not log token material', () => {
  it('passes no token-bearing identifier to console', () => {
    const offenders = consoleCalls(source).filter((call) => {
      // Only the ARGUMENTS matter, not the message text — a label mentioning
      // "token" is fine ("Token expired"), passing `response` is not.
      const args = call.slice(call.indexOf('(') + 1);
      return TOKEN_BEARING.some((id) =>
        // The identifier passed AS ITSELF — terminated by `,` or `)`. A scalar
        // FIELD of it is fine and must not trip this: `response.ChallengeName`
        // is a challenge name and `Boolean(response.AuthenticationResult)` is a
        // boolean, neither of which is token material. Only the whole object
        // leaks tokens.
        new RegExp(`[,(]\\s*${id}\\s*(?=[,)])`).test(args),
      );
    });

    expect(
      offenders,
      `console call(s) in SimpleAuthProvider.tsx pass token-bearing values:\n${offenders.join('\n')}`,
    ).toEqual([]);
  });

  it('never logs the raw InitiateAuth response object', () => {
    // The exact regression: `console.log('📥 Auth response:', response)`.
    expect(source).not.toMatch(/console\.\w+\([^)]*,\s*response\s*\)/);
  });

  it('does log a redacted shape summary, so the diagnostic signal is preserved', () => {
    // Positive control. Without this, deleting the logging entirely would pass
    // the two assertions above — and the next person would "fix" a regression by
    // removing observability rather than redacting it.
    expect(source).toMatch(/hasAuthenticationResult/);
    expect(source).toMatch(/challengeName/);
  });

  it('the guard itself can detect an offender (anti-vacuity)', () => {
    // Proves the matcher works, so a green result means "no offender" rather
    // than "the regex never matches anything".
    const mutated = `console.log('📥 Auth response:', response);`;
    expect(consoleCalls(mutated)).toHaveLength(1);
    const args = mutated.slice(mutated.indexOf('(') + 1);
    const matcher = new RegExp('[,(]\\s*response\\s*(?=[,)])');
    expect(matcher.test(args)).toBe(true);
    // ...and does NOT fire on a scalar field of the same object, which is the
    // over-match that made the first version of this guard useless.
    const safe = `console.log('x:', response.ChallengeName);`;
    expect(matcher.test(safe.slice(safe.indexOf('(') + 1))).toBe(false);
  });
});
