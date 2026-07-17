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
      const isAmazonFederate =
        typeof payload.email === 'string' && payload.email.endsWith('@amazon.com');
      const rawGroups: string[] = Array.isArray(payload['cognito:groups'])
        ? payload['cognito:groups']
        : [];
      return {
        username: payload.email || payload.sub || 'user',
        email: payload.email || '',
        name: payload.name || payload.email || 'User',
        groups: isAmazonFederate ? ['platform-admin'] : rawGroups,
        roles: isAmazonFederate ? ['platform-admin'] : (rawGroups.length ? rawGroups : ['user']),
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
