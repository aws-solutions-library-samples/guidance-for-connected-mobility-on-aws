// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Session-end signpost — CMS half.
 *
 * Ported from DMS `2bb5ccc`. Spec:
 * `guidance-for-dealer-management-system-on-aws/.kiro/specs/2026-09-08-portfolio-ui-token-refresh/`,
 * T2.3.
 *
 * WHY THIS FILE EXISTS AT ALL, given that spec was closed with the CMS port
 * marked descoped: the descope removed **token refresh**, which the operator did
 * not want. It did not remove the signposting defect, which is independent of
 * token lifetime — `logout()` performs no redirect and no call to Cognito's
 * logout endpoint, so every automatic ejection renders a bare sign-in screen and
 * the user is left to guess that re-login is the recovery.
 *
 * Fixing that in DMS alone would have left two of three UIs with the defect and
 * created exactly the divergence the parent spec existed to prevent. DMS's
 * provider was extracted from CMS's and the two still differ by ~30 cosmetic
 * lines out of ~520, so the defect was inherited and the fix ports directly.
 *
 * Connected Services (`modules/connected_services_ui/src/auth.ts`) is a separate
 * implementation and is owned by active spec
 * `2026-09-04-cms-connected-services-portal-v2`. It remains unsignposted and is
 * recorded as such rather than edited under another session's spec.
 *
 * BOTH HALVES ASSERTED — a message that always shows is not a signpost.
 */
import React from 'react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';

vi.mock('@aws-sdk/client-cognito-identity-provider', () => ({
  CognitoIdentityProviderClient: vi.fn(() => ({ send: vi.fn() })),
  InitiateAuthCommand: vi.fn(),
  AuthFlowType: { USER_PASSWORD_AUTH: 'USER_PASSWORD_AUTH' },
}));

import { SimpleAuthProvider } from './SimpleAuthProvider';

function makeIdToken(offsetSeconds: number): string {
  const header = btoa(JSON.stringify({ alg: 'none', typ: 'JWT' }));
  const payload = btoa(
    JSON.stringify({
      sub: 'test-user',
      email: 'test@example.com',
      exp: Math.floor(Date.now() / 1000) + offsetSeconds,
    }),
  );
  return `${header}.${payload}.signature-not-verified-client-side`;
}

const SIGNPOST = /your session ended/i;

const provider = (children: React.ReactNode) => (
  <SimpleAuthProvider userPoolId="us-west-2_TEST" clientId="test-client-id" region="us-west-2">
    {children}
  </SimpleAuthProvider>
);

describe('session-end signpost (CMS)', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    vi.useRealTimers();
  });

  afterEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  it('does NOT show on a fresh first visit — the negative half', () => {
    // The case that makes every positive assertion below meaningful. Verified by
    // mutation in the DMS twin: forcing the alert to render unconditionally turns
    // this test red and leaves the others green.
    render(provider(<div>protected</div>));
    expect(screen.queryByText(SIGNPOST)).toBeNull();
  });

  it('renders no sign-in surface at all when a stored token is still valid', () => {
    // NOT a signpost negative control, despite reading like one: a valid token
    // means LoginForm never renders, so the alert is unreachable whatever its
    // condition says. Named for what it actually guards — the authenticated
    // branch not falling through to the sign-in surface.
    sessionStorage.setItem('authToken', 'access-token');
    sessionStorage.setItem('idToken', makeIdToken(3600));

    render(provider(<div>protected</div>));

    expect(screen.getByText('protected')).toBeTruthy();
    expect(screen.queryByText(SIGNPOST)).toBeNull();
  });

  it('shows when a stored token has already expired — mount path', () => {
    sessionStorage.setItem('authToken', 'access-token');
    sessionStorage.setItem('idToken', makeIdToken(-60));

    render(provider(<div>protected</div>));

    expect(screen.getByText(SIGNPOST)).toBeTruthy();
  });

  it('shows when the stored token is malformed — mount catch path', () => {
    sessionStorage.setItem('authToken', 'access-token');
    sessionStorage.setItem('idToken', 'not-a-jwt');

    render(provider(<div>protected</div>));

    expect(screen.getByText(SIGNPOST)).toBeTruthy();
  });

  it('shows when a valid token expires mid-session — interval path', async () => {
    vi.useFakeTimers();
    const now = Date.now();
    vi.setSystemTime(now);

    sessionStorage.setItem('authToken', 'access-token');
    sessionStorage.setItem('idToken', makeIdToken(30));

    render(provider(<div>protected</div>));

    expect(screen.queryByText(SIGNPOST)).toBeNull();

    await act(async () => {
      vi.setSystemTime(now + 61_000);
      await vi.advanceTimersByTimeAsync(60_000);
    });

    expect(screen.getByText(SIGNPOST)).toBeTruthy();
    vi.useRealTimers();
  });
});
