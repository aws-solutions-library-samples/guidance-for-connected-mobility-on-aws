// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for the 2026-07-16 SimpleAuthProvider fixes (spec
 * `.kiro/specs/2026-07-16-cms-demo-mode-maps-osm-fallback/`):
 *
 *  - Stale `authToken`-without-`idToken` sessions are cleared on init and
 *    the user is required to re-authenticate (fixes the OSM-fallback state
 *    that persists after a demo-mode session with the pre-fix synthetic
 *    `demo-token`).
 *  - The `isDemoMode` prop no longer alters the auth-write path; the
 *    demo-mode early-return branch has been removed.
 */
import React from 'react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { SimpleAuthProvider, useSimpleAuth } from './SimpleAuthProvider';

// Mock the Cognito SDK — the init-from-storage flow does NOT call Cognito,
// but if the test triggers login() it would; keep the module resolvable.
vi.mock('@aws-sdk/client-cognito-identity-provider', () => ({
  CognitoIdentityProviderClient: vi.fn(() => ({ send: vi.fn() })),
  InitiateAuthCommand: vi.fn(),
  AuthFlowType: { USER_PASSWORD_AUTH: 'USER_PASSWORD_AUTH' },
}));

const AuthProbe: React.FC = () => {
  const auth = useSimpleAuth();
  return (
    <div>
      <span data-testid="isAuthenticated">{String(auth.isAuthenticated)}</span>
      <span data-testid="idToken">{auth.idToken ?? 'null'}</span>
      <span data-testid="token">{auth.token ?? 'null'}</span>
    </div>
  );
};

const makeUnexpiredJwt = () => {
  // Header + payload with far-future `exp` (year 2099); signature can be any
  // opaque string because the code only reads and base64-decodes the payload.
  const header = btoa(JSON.stringify({ alg: 'none', typ: 'JWT' }));
  const payload = btoa(JSON.stringify({ email: 'test@example.com', exp: 4102444800 }));
  return `${header}.${payload}.sig`;
};

const renderProvider = (isDemoMode = false) =>
  render(
    <SimpleAuthProvider
      userPoolId="us-east-1_EXAMPLE"
      clientId="test-client"
      region="us-east-1"
      isDemoMode={isDemoMode}
    >
      <AuthProbe />
    </SimpleAuthProvider>,
  );

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  // Reset window.runtimeConfig between tests — some tests populate it to
  // exercise LoginForm gates; others expect it unset.
  delete (window as any).runtimeConfig;
});

describe('SimpleAuthProvider LoginForm — 2026-07-16 duplicate-Federate-button removal', () => {
  it('(A) renders exactly ONE "Sign in with Amazon (Federate)" button when runtimeConfig has cognitoDomain + userPoolWebClientId (the prod condition that previously showed two buttons)', async () => {
    (window as any).runtimeConfig = {
      cognitoDomain: 'example.auth.<region>.amazoncognito.com',
      awsCredentials: { userPoolWebClientId: 'test-client-id' },
    };

    renderProvider(false);

    // The primary Federate button rendered by SimpleAuthProvider's LoginForm.
    // Cloudscape wraps <Button> content in <span>, so use text-match, not role+name.
    const federateButtons = await screen.findAllByText(/Sign in with Amazon \(Federate\)/i);
    expect(federateButtons).toHaveLength(1);

    // Regression guard: the removed "🔐 Corporate SSO (Admin)" button MUST NOT
    // render — it was the duplicate Federate-flow button.
    expect(screen.queryByText(/Corporate SSO \(Admin\)/i)).toBeNull();
  });

  it('(B) renders ZERO Federate buttons when runtimeConfig lacks cognitoDomain (dev/local without Cognito hosted UI)', async () => {
    (window as any).runtimeConfig = {}; // no cognitoDomain

    renderProvider(false);

    await screen.findByText('Connected Mobility Intelligence');
    expect(screen.queryByText(/Sign in with Amazon \(Federate\)/i)).toBeNull();
    expect(screen.queryByText(/Corporate SSO \(Admin\)/i)).toBeNull();
  });
});

describe('SimpleAuthProvider LoginForm — 2026-09-02 quick-login block deleted', () => {
  // The quick-login section (visible persona buttons on the login page) was
  // deleted 2026-09-02 alongside the env-collapse Federate-only auth model.
  // These tests are regression guards: the buttons must not reappear.
  //
  // NOTE: Vitest/Vite statically resolve `import.meta.env.DEV` to `true` at
  // transform time in the test environment, so even DEV builds must show no
  // quick-login section.

  it('does not render the "Quick login:" header even in DEV', async () => {
    (window as any).runtimeConfig = {
      cognitoDomain: 'example.auth.<region>.amazoncognito.com',
      awsCredentials: { userPoolWebClientId: 'test-client-id' },
      showDemoButtons: true,
    };
    renderProvider(false);
    await screen.findByText('Connected Mobility Intelligence');
    expect(screen.queryByText('Quick login:')).not.toBeInTheDocument();
  });

  it('does not render persona buttons even when showDemoButtons is true', async () => {
    (window as any).runtimeConfig = {
      cognitoDomain: 'example.auth.<region>.amazoncognito.com',
      awsCredentials: { userPoolWebClientId: 'test-client-id' },
      showDemoButtons: true,
    };
    renderProvider(false);
    await screen.findByText('Connected Mobility Intelligence');
    expect(screen.queryByText(/Fleet Manager/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Agent/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Product Engineer/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Dispatcher/i)).not.toBeInTheDocument();
  });
});

describe('SimpleAuthProvider init-from-storage — 2026-07-16 stale-session purge', () => {
  it('(1) purges a legacy authToken="demo-token" session with no idToken → user NOT authenticated, LoginForm renders', async () => {
    localStorage.setItem('authToken', 'demo-token');
    // No idToken written — this is the exact state produced by the pre-fix
    // demo-mode branch.

    renderProvider(true);

    // LoginForm renders when there's no token; the header text is stable.
    expect(await screen.findByText('Connected Mobility Intelligence')).toBeInTheDocument();

    // Both keys are cleared post-purge.
    expect(localStorage.getItem('authToken')).toBeNull();
    expect(sessionStorage.getItem('authToken')).toBeNull();
    expect(localStorage.getItem('idToken')).toBeNull();
    expect(sessionStorage.getItem('idToken')).toBeNull();
  });

  it('(2) purges a sessionStorage authToken with no idToken (any auth-write failure mode)', async () => {
    sessionStorage.setItem('authToken', 'eyJhbGciOiJub25lIn0.abc.sig'); // shape doesn't matter

    renderProvider(false);

    expect(await screen.findByText('Connected Mobility Intelligence')).toBeInTheDocument();
    expect(sessionStorage.getItem('authToken')).toBeNull();
  });

  it('(3) preserves a real session with matching non-expired idToken (regression guard)', async () => {
    const jwt = makeUnexpiredJwt();
    sessionStorage.setItem('authToken', 'access-token-value');
    sessionStorage.setItem('idToken', jwt);

    renderProvider(false);

    // Children (AuthProbe) render — the auth-provider does not gate on LoginForm.
    expect(await screen.findByTestId('isAuthenticated')).toHaveTextContent('true');
    expect(screen.getByTestId('idToken')).toHaveTextContent(jwt);
    expect(screen.getByTestId('token')).toHaveTextContent('access-token-value');
    // Storage is untouched.
    expect(sessionStorage.getItem('authToken')).toBe('access-token-value');
    expect(sessionStorage.getItem('idToken')).toBe(jwt);
  });

  it('(4) with no stored session at all → LoginForm renders, no storage writes', async () => {
    renderProvider(false);

    expect(await screen.findByText('Connected Mobility Intelligence')).toBeInTheDocument();
    expect(localStorage.getItem('authToken')).toBeNull();
    expect(sessionStorage.getItem('authToken')).toBeNull();
  });
});


describe('SimpleAuthProvider LoginForm — source-scan guards (2026-08-05, retained)', () => {
  it('(F) source scan — SimpleAuthProvider.tsx contains no plaintext password literal (prevention rule)', async () => {
    // Read the source file directly and assert it does not contain any
    // string literal that looks like a bundled password. This is the
    // prevention rule for issue
    // `issues/2026-08-04-staging-demo-login-cred-frontend/`, where the
    // prior implementation embedded `SynthDemoPw` in a `demoCredentials`
    // object literal (SimpleAuthProvider.tsx :226-229, pre-2026-08-05).
    //
    // Two independent checks, each catching a different regression shape:
    //  (1) no `demoCredentials`-shaped object literal — an operator
    //      reintroducing the pattern typo-for-typo is what happens most
    //      often when a fix is reverted or cherry-picked back;
    //  (2) no persona field followed by a plausibly-password-shaped
    //      literal — catches the same class of leak even when renamed
    //      (e.g. `demoPasswords`, `personaCreds`).
    //
    // Word-boundary anchoring on persona field names avoids matching
    // longer identifiers (e.g. `serviceAgent`) that happen to end in the
    // same substring.
    const here = dirname(fileURLToPath(import.meta.url));
    const source = readFileSync(join(here, 'SimpleAuthProvider.tsx'), 'utf8');

    // Strip comments so historical references to the class of bug in
    // block comments don't false-trip the scan.
    const codeOnly = source
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '');

    // Check 1: no `demoCredentials`-shaped object literal.
    expect(codeOnly).not.toMatch(/demoCredentials\s*=\s*\{/);

    // Check 2: no persona field followed by a plausibly-password-shaped
    // literal. The literal shape here (6-32 non-quote chars) matches
    // typical demo passwords without over-firing on short display-only
    // strings like emoji labels.
    const personaFieldPasswordAssignment =
      /\b(fleetManager|agent|engineer|dispatcher|admin|driver|operator|owner)\b\s*:\s*['"][^'"\s]{6,32}['"]/i;
    expect(codeOnly).not.toMatch(personaFieldPasswordAssignment);

    // Check 3: no `Demo-<digits>`-shaped literal at all. Even outside a
    // structured assignment, this specific class of demo-password literal
    // should never reappear — this is a defense-in-depth catch for the
    // exact string that leaked previously.
    expect(codeOnly).not.toMatch(/Demo-\d{4,}/);
  });
});


// ---------------------------------------------------------------------------
// 2026-09-02: regression guards that Group E (auto-sign-in on landing) STAYS
// deleted. The useEffect that read `runtimeConfig.demoPasswords` +
// `localStorage.cms.lastPersona` and auto-signed as a demo persona on landing
// was removed 2026-09-02. See
// issues/2026-09-02-cms-auto-persona-signin-removed/.
// ---------------------------------------------------------------------------

describe('SimpleAuthProvider — auto-sign-in on landing is deleted (2026-09-02 regression guards)', () => {
  it('landing with demoPasswords populated does NOT auto-invoke Cognito', async () => {
    // Arrange: no stored session, demoPasswords present, showDemoButtons true
    // (the pre-2026-09-02 conditions that fired auto-sign-in).
    (window as any).runtimeConfig = {
      cognitoDomain: 'example.auth.<region>.amazoncognito.com',
      awsCredentials: { userPoolWebClientId: 'test-client-id' },
      showDemoButtons: true,
      demoPasswords: {
        'FleetManager@example.com': 'FMgr-pw-test1',
      },
    };

    renderProvider(false);

    // Assert: login form renders instead of a spinner or Authenticated state.
    // The form's header is the stable observable in this test environment.
    expect(await screen.findByText('Connected Mobility Intelligence')).toBeInTheDocument();

    // Assert: Cognito was never invoked. If auto-sign-in were still wired,
    // the mock CognitoIdentityProviderClient.send would have been called.
    const CognitoModule = await import('@aws-sdk/client-cognito-identity-provider');
    const clientInstances = (CognitoModule.CognitoIdentityProviderClient as any).mock?.results ?? [];
    for (const instance of clientInstances) {
      if (instance.value && typeof instance.value.send === 'function') {
        expect(instance.value.send).not.toHaveBeenCalled();
      }
    }
  });

  it('login does NOT persist cms.lastPersona (demo-persona persistence deleted)', async () => {
    // The pre-2026-09-02 login path wrote localStorage.setItem('cms.lastPersona', email)
    // when the email matched a demoPasswords key. That write is deleted;
    // this test guards against reintroduction.
    const initialLastPersona = localStorage.getItem('cms.lastPersona');
    (window as any).runtimeConfig = {
      cognitoDomain: 'example.auth.<region>.amazoncognito.com',
      awsCredentials: { userPoolWebClientId: 'test-client-id' },
      showDemoButtons: true,
      demoPasswords: {
        'FleetManager@example.com': 'FMgr-pw-test1',
      },
    };

    renderProvider(false);
    await screen.findByText('Connected Mobility Intelligence');

    // lastPersona must not appear during render (no login attempted here).
    expect(localStorage.getItem('cms.lastPersona')).toBe(initialLastPersona);
  });
});
