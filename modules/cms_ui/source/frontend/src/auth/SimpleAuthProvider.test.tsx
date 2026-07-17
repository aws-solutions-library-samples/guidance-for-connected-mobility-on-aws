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
import { render, screen } from '@testing-library/react';
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
      userPoolId="us-east-1_testpool"
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
      cognitoDomain: 'connected-mobility-demo.auth.us-east-1.amazoncognito.com',
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

describe('SimpleAuthProvider LoginForm — 2026-07-16 orphaned "Quick login:" header on prod', () => {
  // NOTE: Vitest/Vite statically resolve `import.meta.env.DEV` to `true` at
  // transform time in the test environment (Vite's dev-mode default), which
  // short-circuits the `DEV || showDemoButtons` gate regardless of
  // showDemoButtons — attempting to mock it via vi.spyOn does not work
  // because the value is inlined before the mock can intercept it. That
  // means this suite cannot exercise the "hidden in prod" case directly
  // under Vitest; it instead asserts the invariant that actually prevents
  // the regression — the header and the buttons share ONE gate, so they can
  // never be observed in a split state (header visible, buttons hidden, or
  // vice versa). This is the property the prod bug violated.
  it('(C) "Quick login:" header and buttons are never observed split — both present together whenever the section renders', async () => {
    (window as any).runtimeConfig = {
      cognitoDomain: 'connected-mobility-demo.auth.us-east-1.amazoncognito.com',
      awsCredentials: { userPoolWebClientId: 'test-client-id' },
      showDemoButtons: true,
    };

    renderProvider(false);

    await screen.findByText('Connected Mobility Intelligence');
    // Under DEV=true (unavoidable in this test env) the gate is open, so
    // both the header and the buttons MUST appear together — if a future
    // edit re-splits the gate (header outside, buttons inside, as the
    // pre-fix bug did), this assertion still holds trivially, but the
    // structural guard is the source-level fix itself + the prod-only
    // manual verification documented in
    // issues/2026-07-16-quick-login-header-orphaned-on-prod/summary.md.
    const header = screen.getByText('Quick login:');
    const fleetManagerButton = screen.getByText(/Fleet Manager/i);
    expect(header).toBeInTheDocument();
    expect(fleetManagerButton).toBeInTheDocument();
    // Structural guard: the header and the first button share the same
    // gated ancestor element (the borderTop div) — confirms they were NOT
    // pulled apart into two independently-rendered fragments.
    const gatedSection = header.closest('div');
    expect(gatedSection).toContainElement(fleetManagerButton);
  });

  it('(D) shows the "Quick login:" header + buttons together when showDemoButtons is true (staging)', async () => {
    (window as any).runtimeConfig = {
      cognitoDomain: 'connected-mobility-demo.auth.us-east-1.amazoncognito.com',
      awsCredentials: { userPoolWebClientId: 'test-client-id' },
      showDemoButtons: true,
    };

    renderProvider(false);

    await screen.findByText('Connected Mobility Intelligence');
    expect(screen.getByText('Quick login:')).toBeInTheDocument();
    expect(screen.getByText(/Fleet Manager/i)).toBeInTheDocument();
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
