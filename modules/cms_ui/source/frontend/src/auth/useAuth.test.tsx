// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Regression guard for the 2026-07-16 Cognito Identity Pool rate-limit fix
 * (`issues/2026-07-16-cms-map-auth-cognito-identity-rate-limit/`).
 *
 * The bug: `useAuth()` returned a fresh object literal on every render, with
 * `user` also a fresh object literal (via `getUserFromToken()`). Consumers
 * that used `[auth.user]` as a `useEffect` dependency (the 5 MapLibre
 * surfaces) re-fired on every render, triggering repeated Cognito Identity
 * Pool `GetCredentialsForIdentity` calls and exhausting the 200 RPS
 * per-pool quota.
 *
 * The fix: `useMemo` the `user` object (keyed on `simpleAuth.idToken +
 * isAuthenticated`) and `useMemo` the whole return object (keyed on the
 * underlying stable primitives).
 *
 * These tests assert identity stability across re-renders, i.e. that
 * `Object.is(prevAuth.user, currentAuth.user)` holds when the id-token is
 * unchanged.
 */
import React, { useEffect } from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, act } from '@testing-library/react';

// Mock the Cognito SDK — not exercised in this test but the module resolves
// eagerly on import of SimpleAuthProvider.
vi.mock('@aws-sdk/client-cognito-identity-provider', () => ({
  CognitoIdentityProviderClient: vi.fn(() => ({ send: vi.fn() })),
  InitiateAuthCommand: vi.fn(),
  AuthFlowType: { USER_PASSWORD_AUTH: 'USER_PASSWORD_AUTH' },
}));

import { SimpleAuthProvider } from './SimpleAuthProvider';
import { useAuth } from './useAuth';

const makeUnexpiredJwt = (email = 'test@example.com', groups: string[] = []) => {
  const header = btoa(JSON.stringify({ alg: 'none', typ: 'JWT' }));
  const payload = btoa(
    JSON.stringify({
      email,
      exp: 4102444800, // year 2099
      ...(groups.length ? { 'cognito:groups': groups } : {}),
    }),
  );
  return `${header}.${payload}.sig`;
};

/**
 * Test harness that captures `user` identity across every render.
 */
const AuthCapture: React.FC<{ onRender: (auth: ReturnType<typeof useAuth>) => void; trigger: number }> = ({
  onRender,
  trigger,
}) => {
  const auth = useAuth();
  useEffect(() => {
    onRender(auth);
  });
  // Referencing trigger inside render so React knows to re-run when it changes.
  return <div data-trigger={trigger} />;
};

// Wrapper that forces the provider tree to re-render via a state hook,
// without changing the underlying auth state. This is exactly what happens
// in prod when a WebSocket update triggers a parent `setState`.
const RerenderWrapper: React.FC<{ onRender: (auth: ReturnType<typeof useAuth>) => void }> = ({
  onRender,
}) => {
  const [counter, setCounter] = React.useState(0);
  // Re-export the setter for the test to trigger re-renders.
  (globalThis as any).__forceRerender = () => setCounter((c) => c + 1);
  return <AuthCapture onRender={onRender} trigger={counter} />;
};

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  delete (window as any).runtimeConfig;
  delete (globalThis as any).__forceRerender;
});

describe('useAuth — identity stability across re-renders (2026-07-16 rate-limit fix)', () => {
  it('(1) user object identity is stable across re-renders when id-token is unchanged', async () => {
    const jwt = makeUnexpiredJwt();
    sessionStorage.setItem('authToken', 'access-token');
    sessionStorage.setItem('idToken', jwt);

    const captured: Array<ReturnType<typeof useAuth>> = [];
    render(
      <SimpleAuthProvider userPoolId="us-east-1_test" clientId="c" region="us-east-1">
        <RerenderWrapper onRender={(a) => captured.push(a)} />
      </SimpleAuthProvider>,
    );

    // Give the provider one microtask to hydrate from storage.
    await act(async () => { await Promise.resolve(); });

    // Force 5 re-renders of the parent tree — mimicking WebSocket-driven
    // parent state updates that in prod caused the rate-limit bug.
    for (let i = 0; i < 5; i++) {
      await act(async () => { (globalThis as any).__forceRerender(); });
    }

    expect(captured.length).toBeGreaterThan(1);

    // After hydration, isAuthenticated becomes true. Find the first render
    // where `user !== null` and check ALL subsequent renders' user is the
    // SAME reference (`Object.is`).
    const authedRenders = captured.filter((a) => a.user !== null);
    expect(authedRenders.length).toBeGreaterThanOrEqual(2); // at least a couple renders where auth is hydrated
    const firstUser = authedRenders[0].user;
    for (const r of authedRenders) {
      expect(Object.is(r.user, firstUser)).toBe(true);
    }
  });

  it('(2) useAuth() return object identity is stable across re-renders when auth state is unchanged', async () => {
    const jwt = makeUnexpiredJwt();
    sessionStorage.setItem('authToken', 'access-token');
    sessionStorage.setItem('idToken', jwt);

    const captured: Array<ReturnType<typeof useAuth>> = [];
    render(
      <SimpleAuthProvider userPoolId="us-east-1_test" clientId="c" region="us-east-1">
        <RerenderWrapper onRender={(a) => captured.push(a)} />
      </SimpleAuthProvider>,
    );

    await act(async () => { await Promise.resolve(); });

    for (let i = 0; i < 3; i++) {
      await act(async () => { (globalThis as any).__forceRerender(); });
    }

    const authedRenders = captured.filter((a) => a.user !== null);
    const first = authedRenders[0];
    for (const r of authedRenders) {
      // The full return object is stable → a `useEffect(..., [auth])` would
      // NOT re-fire.
      expect(Object.is(r, first)).toBe(true);
    }
  });

  it('(3) user object identity CHANGES when id-token payload changes (regression guard against stale memo)', async () => {
    const jwt1 = makeUnexpiredJwt('alice@example.com');
    sessionStorage.setItem('authToken', 'access-1');
    sessionStorage.setItem('idToken', jwt1);

    const captured: Array<ReturnType<typeof useAuth>> = [];

    const { rerender } = render(
      <SimpleAuthProvider userPoolId="us-east-1_test" clientId="c" region="us-east-1">
        <RerenderWrapper onRender={(a) => captured.push(a)} />
      </SimpleAuthProvider>,
    );

    await act(async () => { await Promise.resolve(); });
    const firstAuthed = captured.filter((a) => a.user !== null)[0];
    expect(firstAuthed.user?.email).toBe('alice@example.com');

    // Simulate a token refresh — different id-token in storage. In prod
    // this happens on the OAuth callback path. Unmount + remount to force
    // a fresh init-from-storage read.
    const jwt2 = makeUnexpiredJwt('bob@example.com');
    sessionStorage.setItem('idToken', jwt2);
    sessionStorage.setItem('authToken', 'access-2');

    // Rerender with a fresh provider to trigger the init-from-storage path
    // (SimpleAuthProvider only reads storage in its mount effect).
    rerender(
      <SimpleAuthProvider
        userPoolId="us-east-1_test"
        clientId="c-new"
        region="us-east-1"
        key="fresh"
      >
        <RerenderWrapper onRender={(a) => captured.push(a)} />
      </SimpleAuthProvider>,
    );
    await act(async () => { await Promise.resolve(); });

    const bobRender = captured.filter((a) => a.user?.email === 'bob@example.com')[0];
    expect(bobRender).toBeDefined();
    // Identity has necessarily changed — new memo output.
    expect(Object.is(firstAuthed.user, bobRender.user)).toBe(false);
  });
});
