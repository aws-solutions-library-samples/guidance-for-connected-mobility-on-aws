// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { createContext, useContext, useEffect, useState, useMemo, useCallback, ReactNode } from 'react';
import { CognitoIdentityProviderClient, InitiateAuthCommand, AuthFlowType } from '@aws-sdk/client-cognito-identity-provider';
import { Container, Header, SpaceBetween, Form, FormField, Input, Button, Alert, Box } from '@cloudscape-design/components';
import { randomBase64url, computeCodeChallenge } from './pkce';

export interface SimpleAuthContextProps {
  token: string | null;
  idToken: string | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (email: string, password: string, rememberMe?: boolean) => void;
  logout: () => void;
  error: string | null;
}

const SimpleAuthContext = createContext<SimpleAuthContextProps | null>(null);

export const useSimpleAuth = (): SimpleAuthContextProps => {
  const context = useContext(SimpleAuthContext);
  if (!context) {
    throw new Error('useSimpleAuth must be used within a SimpleAuthProvider');
  }
  return context;
};

interface SimpleAuthProviderProps {
  children: ReactNode;
  userPoolId: string;
  clientId: string;
  region: string;
  isDemoMode?: boolean;
}

const LoginForm: React.FC<{
  onLogin: (email: string, password: string, rememberMe: boolean) => void;
  onFederateLogin: () => void;
  isLoading: boolean;
  error: string | null;
  /**
   * True when the user arrived here because their session ended on its own —
   * token expiry, or a token we could not parse — rather than by signing out or
   * by visiting for the first time.
   *
   * Deliberately NOT derived inside `logout()`: the user's own sign-out calls the
   * same function, and a signpost after a deliberate sign-out is noise.
   */
  sessionEnded?: boolean;
}> = ({ onLogin, onFederateLogin, isLoading, error, sessionEnded = false }) => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [rememberMe, setRememberMe] = useState(false);
  const [showForgotPassword, setShowForgotPassword] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  // Pre-fill email if remembered
  useEffect(() => {
    const rememberedEmail = localStorage.getItem('userEmail');
    const wasRemembered = localStorage.getItem('rememberMe') === 'true';
    if (rememberedEmail && wasRemembered) {
      setEmail(rememberedEmail);
      setRememberMe(true);
    }
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    console.log('🔐 Form submitted with:', { email, password: '***', rememberMe });
    onLogin(email, password, rememberMe);
  };

  const handleKeyPress = (e: any) => {
    const key = e.key || e.detail?.key || e.nativeEvent?.key;
    if (key === 'Enter' && !isLoading && email && password) {
      e.preventDefault?.();
      handleSubmit(e);
    }
  };

  const handleForgotPassword = () => {
    setShowForgotPassword(true);
    // TODO: Implement forgot password functionality
    alert('Forgot password functionality would be implemented here');
  };

  return (
    <div style={{ 
      display: 'flex', 
      justifyContent: 'center', 
      alignItems: 'center', 
      minHeight: '100vh',
      backgroundColor: 'var(--color-background-layout-main-5ilwcb, #f2f3f3)'
    }}>
      <Container>
        <div style={{ maxWidth: '400px', margin: '0 auto' }}>
          <Header variant="h1">Connected Mobility Intelligence</Header>
          <SpaceBetween size="l">
            {sessionEnded && !error && (
              <Alert type="info" header="Your session ended">
                You were signed out because your session expired. Please sign in again.
              </Alert>
            )}
            {error && <Alert type="error">{error}</Alert>}
            <Form onSubmit={handleSubmit}>
              <SpaceBetween size="m">
                <FormField label="Email">
                  <Input
                    value={email}
                    onChange={({ detail }) => setEmail(detail.value)}
                    onKeyDown={handleKeyPress}
                    type="email"
                    placeholder="Enter your email"
                    disabled={isLoading}
                    autoComplete="email"
                    autoFocus
                  />
                </FormField>
                <FormField label="Password">
                  <div style={{ position: 'relative', width: '100%' }}>
                    <Input
                      value={password}
                      onChange={({ detail }) => setPassword(detail.value)}
                      onKeyDown={handleKeyPress}
                      type={showPassword ? "text" : "password"}
                      placeholder="Enter your password"
                      disabled={isLoading}
                      autoComplete="current-password"
                    />
                    <div 
                      onClick={() => setShowPassword(!showPassword)}
                      style={{
                        position: 'absolute',
                        right: '12px',
                        top: '50%',
                        transform: 'translateY(-50%)',
                        cursor: 'pointer',
                        zIndex: 10,
                        padding: '4px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        width: '20px',
                        height: '20px'
                      }}
                      title={showPassword ? "Hide password" : "Show password"}
                    >
                      {showPassword ? (
                        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                          <path d="M13.359 11.238C15.06 9.72 16 8 16 8s-3-5.5-8-5.5a7.028 7.028 0 0 0-2.79.588l.77.771A5.944 5.944 0 0 1 8 3.5c2.12 0 3.879 1.168 5.168 2.457A13.134 13.134 0 0 1 14.828 8c-.058.087-.122.183-.195.288-.335.48-.83 1.12-1.465 1.755-.165.165-.337.328-.517.486l.708.709z"/>
                          <path d="M11.297 9.176a3.5 3.5 0 0 0-4.474-4.474l.823.823a2.5 2.5 0 0 1 2.829 2.829l.822.822zm-2.943 1.299.822.822a3.5 3.5 0 0 1-4.474-4.474l.823.823a2.5 2.5 0 0 0 2.829 2.829z"/>
                          <path d="M3.35 5.47c-.18.16-.353.322-.518.487A13.134 13.134 0 0 0 1.172 8l.195.288c.335.48.83 1.12 1.465 1.755C4.121 11.332 5.881 12.5 8 12.5c.716 0 1.39-.133 2.02-.36l.77.772A7.029 7.029 0 0 1 8 13.5C3 13.5 0 8 0 8s.939-1.721 2.641-3.238l.708.708zm10.296 8.884-12-12 .708-.708 12 12-.708.708z"/>
                        </svg>
                      ) : (
                        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                          <path d="M16 8s-3-5.5-8-5.5S0 8 0 8s3 5.5 8 5.5S16 8 16 8zM1.173 8a13.133 13.133 0 0 1 1.66-2.043C4.12 4.668 5.88 3.5 8 3.5c2.12 0 3.879 1.168 5.168 2.457A13.133 13.133 0 0 1 14.828 8c-.058.087-.122.183-.195.288-.335.48-.83 1.12-1.465 1.755C11.879 11.332 10.119 12.5 8 12.5c-2.12 0-3.879-1.168-5.168-2.457A13.134 13.134 0 0 1 1.172 8z"/>
                          <path d="M8 5.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5zM4.5 8a3.5 3.5 0 1 1 7 0 3.5 3.5 0 0 1-7 0z"/>
                        </svg>
                      )}
                    </div>
                  </div>
                </FormField>
                
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <label style={{ display: 'flex', alignItems: 'center', cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={rememberMe}
                      onChange={(e) => setRememberMe(e.target.checked)}
                      disabled={isLoading}
                      style={{ marginRight: '8px' }}
                    />
                    <span style={{ fontSize: '14px', color: '#5f6b7a' }}>Remember me</span>
                  </label>
                  
                  <Button
                    variant="link"
                    onClick={handleForgotPassword}
                    disabled={isLoading}
                    ariaLabel="Forgot password"
                  >
                    Forgot password?
                  </Button>
                </div>

                <Button 
                  variant="primary" 
                  loading={isLoading}
                  onClick={handleSubmit}
                  disabled={!email || !password}
                  fullWidth
                >
                  Sign In
                </Button>

                {((typeof window !== 'undefined' && (window as any).runtimeConfig?.cognitoDomain) || import.meta.env.VITE_COGNITO_DOMAIN) && (<>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', margin: '4px 0' }}>
                  <div style={{ flex: 1, height: '1px', background: '#d1d5db' }} />
                  <span style={{ fontSize: '12px', color: '#6b7280' }}>or</span>
                  <div style={{ flex: 1, height: '1px', background: '#d1d5db' }} />
                </div>

                <Button
                  variant="normal"
                  onClick={onFederateLogin}
                  fullWidth
                >
                  Sign in with Amazon (Federate)
                </Button></>)}

                {/* Quick-login block deleted 2026-09-02: Federate is now the
                    only auth path on staging (env-collapse: staging is the sole
                    CMS environment). Demo-persona password sign-ins were the last
                    consumer of `runtimeConfig.demoPasswords` on the login page;
                    the auto-persona logic further down in this file still reads
                    it and remains for now (separate cleanup — see
                    issues/2026-09-02-cms-quick-login-block-removed/). */}
              </SpaceBetween>
            </Form>
          </SpaceBetween>
        </div>
      </Container>
    </div>
  );
};

export const SimpleAuthProvider: React.FC<SimpleAuthProviderProps> = ({
  children,
  userPoolId,
  clientId,
  region,
  isDemoMode = false,
}) => {
  const [token, setToken] = useState<string | null>(null);
  const [idToken, setIdToken] = useState<string | null>(null);
  // Start as true so we never flash LoginForm before the storage check completes.
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  /**
   * Set when the session ends without the user asking — expiry, or a token we
   * cannot parse. Read by `LoginForm` to explain why the sign-in screen appeared.
   *
   * `logout()` performs no redirect and no call to Cognito's logout endpoint; it
   * clears state and the app falls back to sign-in via `isAuthenticated` going
   * false. So recovery worked and was entirely unsignposted. Set at the ejection
   * SITES, never inside `logout()`, which the user's own sign-out also calls.
   *
   * Ported from DMS `2bb5ccc` — see
   * `guidance-for-dealer-management-system-on-aws/.kiro/specs/2026-09-08-portfolio-ui-token-refresh/`.
   */
  const [sessionEnded, setSessionEnded] = useState(false);

  useEffect(() => {
    // Check for existing token in localStorage (remember me) or sessionStorage
    const savedToken = localStorage.getItem('authToken') || sessionStorage.getItem('authToken');
    const savedIdToken = localStorage.getItem('idToken') || sessionStorage.getItem('idToken');
    if (savedToken && !savedIdToken) {
      // Legacy synthetic-token state (e.g. old 'demo-token' from before the
      // 2026-07-16 fix, or any session where the auth path failed to write
      // idToken alongside authToken). Not usable for authenticated calls
      // (Amazon Location maps, API Gateway) — force a fresh login to
      // recover. See spec
      // `.kiro/specs/2026-07-16-cms-demo-mode-maps-osm-fallback/`.
      console.log('🕐 Detected stale auth session (no idToken), clearing and forcing re-login');
      localStorage.removeItem('authToken');
      localStorage.removeItem('idToken');
      sessionStorage.removeItem('authToken');
      sessionStorage.removeItem('idToken');
      setIsLoading(false);
      return;
    }
    if (savedToken) {
      // Check if token is expired
      try {
        if (savedIdToken) {
          const payload = JSON.parse(atob(savedIdToken.split('.')[1]));
          const currentTime = Math.floor(Date.now() / 1000);
          if (payload.exp && payload.exp < currentTime) {
            console.log('🕐 Token expired, logging out');
            setSessionEnded(true);
            localStorage.removeItem('authToken');
            localStorage.removeItem('idToken');
            sessionStorage.removeItem('authToken');
            sessionStorage.removeItem('idToken');
            setIsLoading(false);
            return;
          }
        }
        setToken(savedToken);
        setIdToken(savedIdToken);
      } catch (error) {
        console.error('Error checking token expiration:', error);
        setSessionEnded(true);
        localStorage.removeItem('authToken');
        localStorage.removeItem('idToken');
        sessionStorage.removeItem('authToken');
        sessionStorage.removeItem('idToken');
      }
    }
    setIsLoading(false);
  }, []);

  // Check token expiration periodically
  useEffect(() => {
    if (!idToken) return;

    const checkTokenExpiration = () => {
      try {
        const payload = JSON.parse(atob(idToken.split('.')[1]));
        const currentTime = Math.floor(Date.now() / 1000);
        if (payload.exp && payload.exp < currentTime) {
          console.log('🕐 Token expired during session, logging out');
          setSessionEnded(true);
          logout();
        }
      } catch (error) {
        console.error('Error checking token expiration:', error);
        setSessionEnded(true);
        logout();
      }
    };

    // Check every minute
    const interval = setInterval(checkTokenExpiration, 60000);
    return () => clearInterval(interval);
  }, [idToken]);

  const COGNITO_DOMAIN = (typeof window !== 'undefined' && (window as any).runtimeConfig?.cognitoDomain)
    || import.meta.env.VITE_COGNITO_DOMAIN
    || '';
  const REDIRECT_URI = import.meta.env.VITE_REDIRECT_URI || `${window.location.origin}/auth/callback`;

  // ── OAuth callback — exchange code with Cognito token endpoint ──────────────
  useEffect(() => {
    if (window.location.pathname !== '/auth/callback') return;
    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    if (!code) return;

    // CSRF state verification — fail closed on mismatch or absence.
    const incomingState = params.get('state');
    const expectedState = sessionStorage.getItem('oauth.state');
    sessionStorage.removeItem('oauth.state'); // single-use
    if (!incomingState || !expectedState || incomingState !== expectedState) {
      setError('Login failed: invalid OAuth state. Please try signing in again.');
      setIsLoading(false);
      return;
    }

    // Retrieve and clear the PKCE code_verifier (single-use).
    const codeVerifier = sessionStorage.getItem('oauth.code_verifier');
    sessionStorage.removeItem('oauth.code_verifier');
    if (!codeVerifier) {
      setError('Login failed: missing PKCE verifier. Please try signing in again.');
      setIsLoading(false);
      return;
    }

    setIsLoading(true);
    fetch(`https://${COGNITO_DOMAIN}/oauth2/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'authorization_code',
        client_id: clientId,
        code,
        redirect_uri: REDIRECT_URI,
        code_verifier: codeVerifier,
      }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.access_token) {
          setToken(data.access_token);
          setIdToken(data.id_token || null);
          sessionStorage.setItem('authToken', data.access_token);
          if (data.id_token) sessionStorage.setItem('idToken', data.id_token);
          // Restore the URL the user was trying to access before the auth redirect.
          const preAuthUrl = sessionStorage.getItem('preAuthUrl') || '/';
          sessionStorage.removeItem('preAuthUrl');
          // Guard against open-redirect: only follow same-origin paths.
          const safePath = preAuthUrl.startsWith('/') && !preAuthUrl.startsWith('//') ? preAuthUrl : '/';
          window.location.replace(safePath);
        } else {
          setError('Federate login failed. Please try again.');
        }
      })
      .catch(() => setError('Federate login failed. Please try again.'))
      .finally(() => setIsLoading(false));
  }, [clientId]);

  const loginWithFederate = async () => {
    // Save the current URL so we can restore it after the OAuth callback.
    if (window.location.pathname !== '/auth/callback') {
      sessionStorage.setItem('preAuthUrl', window.location.pathname + window.location.search);
    }

    // Generate PKCE code_verifier (43–128 chars; 32 random bytes → 43 base64url chars).
    const codeVerifier = randomBase64url(32);
    const codeChallenge = await computeCodeChallenge(codeVerifier);
    sessionStorage.setItem('oauth.code_verifier', codeVerifier);

    // Generate random state for CSRF protection.
    const state = randomBase64url(16);
    sessionStorage.setItem('oauth.state', state);

    const params = new URLSearchParams({
      response_type: 'code',
      client_id: clientId,
      redirect_uri: REDIRECT_URI,
      identity_provider: 'AmazonFederate',
      scope: 'openid email profile',
      code_challenge: codeChallenge,
      code_challenge_method: 'S256',
      state,
    });
    window.location.href = `https://${COGNITO_DOMAIN}/oauth2/authorize?${params}`;
  };

  const login = useCallback(async (email: string, password: string, rememberMe: boolean = false) => {
    // NOTE (2026-07-16): the historical `if (isDemoMode) { token='demo-token'; ... }`
    // early-return has been REMOVED. It produced an authenticated-appearing
    // session with no `idToken`, which broke Amazon Location map auth (falls
    // back to OSM) on every map surface. Demo users are real seeded Cognito
    // users (see `deployment/scripts/seed_*.py`) with real passwords, so the
    // demo quick-fill buttons flow through the standard USER_PASSWORD_AUTH
    // path below, get a real id-token, and render HERE maps like every other
    // authenticated user. The `isDemoMode` prop is still accepted for other
    // consumers (`ProtectedRoute`, etc.) but does not alter auth behavior
    // here. See spec `.kiro/specs/2026-07-16-cms-demo-mode-maps-osm-fallback/`.

    setIsLoading(true);
    setError(null);
    // A new sign-in attempt clears the signpost, so the "your session ended"
    // alert cannot outlive a successful login and reappear after a later
    // deliberate sign-out — which would turn an accurate message into a wrong one.
    setSessionEnded(false);

    try {
      console.log('🔐 Attempting login with:', { email, userPoolId, clientId, region, rememberMe });
      
      const client = new CognitoIdentityProviderClient({ region });
      
      const command = new InitiateAuthCommand({
        AuthFlow: AuthFlowType.USER_PASSWORD_AUTH,
        ClientId: clientId,
        AuthParameters: {
          USERNAME: email,
          PASSWORD: password,
        },
      });

      console.log('📤 Sending auth command...');
      const response = await client.send(command);
      // NEVER log `response` itself. It carries AccessToken, IdToken AND the
      // long-lived RefreshToken, and the Cognito pool is SHARED with DMS — a
      // leaked refresh token grants continued authentication against both.
      // Console output is reachable by any browser extension, screen share,
      // session-replay/RUM agent or XSS foothold, and it survives bundling (it
      // is not stripped as dead code in a production build). Log only a
      // non-sensitive shape summary; that is all the diagnostic value the
      // original line actually carried.
      // See issues/2026-09-01-cms-auth-token-logging-and-oauth-hardening/.
      console.log('📥 Auth response received:', {
        hasAuthenticationResult: Boolean(response.AuthenticationResult),
        challengeName: response.ChallengeName ?? null,
      });
      
      if (response.AuthenticationResult?.AccessToken) {
        const accessToken = response.AuthenticationResult.AccessToken;
        const idTokenValue = response.AuthenticationResult.IdToken;
        console.log('✅ Login successful, setting tokens');
        setToken(accessToken);
        setIdToken(idTokenValue || null);
        
        // Store tokens based on remember me preference
        if (rememberMe) {
          localStorage.setItem('authToken', accessToken);
          if (idTokenValue) localStorage.setItem('idToken', idTokenValue);
          localStorage.setItem('rememberMe', 'true');
          localStorage.setItem('userEmail', email); // Store email for convenience
        } else {
          sessionStorage.setItem('authToken', accessToken);
          if (idTokenValue) sessionStorage.setItem('idToken', idTokenValue);
          localStorage.removeItem('rememberMe');
          localStorage.removeItem('userEmail');
        }
      } else if (response.ChallengeName) {
        console.log('🔄 Challenge required:', response.ChallengeName);
        setError(`Challenge required: ${response.ChallengeName}`);
      } else {
        console.error('❌ No access token in response');
        throw new Error('Authentication failed - no access token received');
      }
    } catch (err: any) {
      console.error('❌ Login error:', err);
      setError(err.message || 'Login failed. Please check your credentials.');
    } finally {
      setIsLoading(false);
    }
  }, [region, clientId]);

  const logout = useCallback(() => {
    console.log('🚪 SimpleAuthProvider logout called');
    setToken(null);
    setIdToken(null);
    localStorage.removeItem('authToken');
    localStorage.removeItem('idToken');
    sessionStorage.removeItem('authToken');
    sessionStorage.removeItem('idToken');
    // Keep userEmail and rememberMe if user had remember me checked
    const wasRemembered = localStorage.getItem('rememberMe') === 'true';
    if (!wasRemembered) {
      localStorage.removeItem('userEmail');
      localStorage.removeItem('rememberMe');
    }
    console.log('🚪 SimpleAuthProvider logout completed');
  }, []);

  // 2026-09-02: Auto-sign-in on landing (Group E, spec 2026-08-05-cms-demo-identity-model)
  // was DELETED. Federate-only auth is now the sole path — a fresh landing
  // shows the login page which auto-passes through Cognito Hosted UI's SSO
  // to Federate. See issues/2026-09-02-cms-auto-persona-signin-removed/.

  // Memoize the context value so consumers (`useSimpleAuth` / `useAuth`)
  // that read via `useContext` do NOT re-render on every parent render.
  // Fresh object literals here were an amplifier for the 2026-07-16 map
  // auth rate-limit bug — see `useAuth.ts` and
  // `issues/2026-07-16-cms-map-auth-cognito-identity-rate-limit/`.
  const contextValue = useMemo<SimpleAuthContextProps>(
    () => ({
      token,
      idToken,
      isLoading,
      isAuthenticated: !!token,
      login,
      logout,
      error,
    }),
    [token, idToken, isLoading, error, login, logout],
  );

  if (!token) {
    // During auto-sign-in we are mid-login but have no token yet.  Show a
    // minimal loading state rather than briefly flashing the full LoginForm —
    // the user should perceive a seamless "already signed in" experience.
    if (isLoading && !error) {
      return (
        <SimpleAuthContext.Provider value={contextValue}>
          <div style={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            minHeight: '100vh',
            backgroundColor: 'var(--color-background-layout-main-5ilwcb, #f2f3f3)'
          }}>
            <div style={{ textAlign: 'center', color: '#5f6b7a' }}>
              <div style={{ fontSize: '24px', marginBottom: '8px' }}>⏳</div>
              <div>Signing in…</div>
            </div>
          </div>
        </SimpleAuthContext.Provider>
      );
    }
    return (
      <SimpleAuthContext.Provider value={contextValue}>
        <LoginForm 
          onLogin={login} 
          onFederateLogin={loginWithFederate}
          isLoading={isLoading} 
          error={error} 
          sessionEnded={sessionEnded}
        />
      </SimpleAuthContext.Provider>
    );
  }

  return (
    <SimpleAuthContext.Provider value={contextValue}>
      {children}
    </SimpleAuthContext.Provider>
  );
};
