// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useMemo } from 'react';
import { useSimpleAuth } from './SimpleAuthProvider';

export interface AuthUser {
  username: string;
  email?: string;
  name?: string;
  groups: string[];
  roles: string[];
  fleetIds?: string;
}

export interface UseAuthReturn {
  user: AuthUser | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: () => void;
  loginWithFederate?: () => void;
  logout: () => void;
  getAccessToken: () => string | null;
  getIdToken: () => string | null;
  getAuthHeaders: () => Record<string, string>;
  isTokenValid: () => boolean;
  error?: string | null;
  clearError?: () => void;
}

/**
 * `useAuth` — thin adapter over `useSimpleAuth` that decodes the id-token
 * into an `AuthUser`.
 *
 * ## Identity stability (2026-07-16 rate-limit fix)
 *
 * The `user` field and the entire return object are `useMemo`'d against
 * the underlying stable primitives (`simpleAuth.idToken`,
 * `simpleAuth.token`, `simpleAuth.isAuthenticated`, `simpleAuth.isLoading`).
 * Before this fix, every render of any consumer built a fresh `AuthUser`
 * object literal and a fresh return-object literal, which meant any
 * downstream `useEffect(..., [auth.user])` re-fired on every parent
 * render. In the map components that read `auth.user` and call
 * `getMapConfiguration()` in their setup effect, that produced dozens of
 * Cognito Identity Pool API calls per second under WebSocket-driven
 * re-renders — hitting the 200 RPS `GetCredentialsForIdentity` quota and
 * dropping maps to OSM. See
 * `issues/2026-07-16-cms-map-auth-cognito-identity-rate-limit/`.
 */
export const useAuth = (): UseAuthReturn => {
  const simpleAuth = useSimpleAuth();

  // Stable AuthUser identity per (idToken, isAuthenticated). Rebuilds only
  // when the id-token actually changes.
  const user = useMemo<AuthUser | null>(() => {
    if (!simpleAuth.isAuthenticated || !simpleAuth.idToken) return null;

    try {
      const payload = JSON.parse(atob(simpleAuth.idToken.split('.')[1]));

      // The token's `cognito:groups` claim is the SOLE source of group membership.
      //
      // This deliberately does NOT infer privilege from the email address. Until
      // 2026-08-28 it did: an `@amazon.com` suffix granted `platform-admin`
      // client-side. That was removed (task F13a.3), for two independent reasons.
      //
      // 1. It was a privilege-escalation path. `email` is client-writable by design
      //    (`ui_stack.py` — the AmazonFederate IdP maps email -> EMAIL, and
      //    `test_client_write_attributes.py` asserts it must stay writable), and
      //    Cognito clears `email_verified` when a user changes their own email. So a
      //    self-registered external user could set any `@amazon.com` address and be
      //    granted admin in the UI. Checking `email_verified` was NOT the fix: the
      //    IdP maps only email/name/username, so all 10 prod Federate users carry
      //    `email_verified: false` and that check would have locked out every real
      //    admin.
      //
      // 2. It was redundant. The provisioning trigger already assigns
      //    `platform-admin` to internal-IdP users SERVER-side — wired as both
      //    PostConfirmation and PostAuthentication on staging and prod, with
      //    `INTERNAL_AUTO_ASSIGN_GROUP=platform-admin`. It identifies internal users
      //    from the `identities` claim's `providerName`, which Cognito populates and
      //    a user cannot forge, so it has no equivalent hole. All 10 prod Federate
      //    users are already in the group, meaning the claim below already carries
      //    it. Duplicating a server-side authorization decision in untrusted client
      //    code could only ever diverge from it.
      //
      // KNOWN CONSEQUENCE, accepted deliberately: `PostAuthentication` fires after
      // the token is minted, and federated users get no `PostConfirmation`. So on a
      // user's FIRST Federate login the group is assigned but that session's token
      // does not yet carry it, and they must refresh or re-login once. Existing
      // users are unaffected. The alternative — keying off the `identities` claim
      // client-side to close the gap — is a follow-on rather than part of this
      // change, because it rests on `identities` being present in the id token and
      // that has not been verified against a real token.
      const rawGroups: string[] = Array.isArray(payload['cognito:groups'])
        ? payload['cognito:groups']
        : [];

      return {
        username: payload.email || payload.sub || 'user',
        email: payload.email || '',
        name: payload.name || payload.email || 'User',
        groups: rawGroups,
        roles: rawGroups.length ? rawGroups : ['user'],
        fleetIds: payload['custom:fleetIds'] || '',
      };
    } catch (error) {
      console.error('Error parsing token:', error);
      return {
        username: 'user',
        email: 'user@example.com',
        name: 'User',
        groups: [],
        roles: ['user'],
      };
    }
  }, [simpleAuth.idToken, simpleAuth.isAuthenticated]);

  // Stable function identities so consumers that pass these as deps or
  // props do not spuriously re-render.
  const login = useCallback(() => {
    // Handled by the LoginForm component; nothing to do here.
  }, []);

  const logout = useCallback(() => {
    console.log('🚪 Logout called from useAuth');
    simpleAuth.logout();
  }, [simpleAuth.logout]);

  // NOTE (2026-07-16): the body of this function preserves the pre-existing
  // reference to `signInWithRedirect` verbatim. That symbol is NOT imported
  // in this module — this is a latent pre-existing issue that predates the
  // rate-limit fix and is intentionally out of scope here. The primary
  // Federate entry point on the login screen is `SimpleAuthProvider`'s
  // internal `loginWithFederate` (rendered via `onFederateLogin`), which
  // works correctly. This function is only reached from
  // `ProtectedRoute.tsx`, which is already de-facto broken; fixing it is
  // tracked separately.
  const loginWithFederate = useCallback(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any, no-undef
    (signInWithRedirect as any)({ provider: { custom: 'AmazonFederate' } });
  }, []);

  const getAccessToken = useCallback(() => simpleAuth.token, [simpleAuth.token]);
  const getIdToken = useCallback(() => simpleAuth.idToken, [simpleAuth.idToken]);
  const getAuthHeaders = useCallback((): Record<string, string> => {
    const t = simpleAuth.idToken || simpleAuth.token;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }, [simpleAuth.idToken, simpleAuth.token]);
  const isTokenValid = useCallback(
    () => simpleAuth.isAuthenticated,
    [simpleAuth.isAuthenticated],
  );

  return useMemo<UseAuthReturn>(
    () => ({
      user,
      isAuthenticated: simpleAuth.isAuthenticated,
      isLoading: simpleAuth.isLoading,
      login,
      loginWithFederate,
      logout,
      getAccessToken,
      getIdToken,
      getAuthHeaders,
      isTokenValid,
    }),
    [
      user,
      simpleAuth.isAuthenticated,
      simpleAuth.isLoading,
      login,
      loginWithFederate,
      logout,
      getAccessToken,
      getIdToken,
      getAuthHeaders,
      isTokenValid,
    ],
  );
};
