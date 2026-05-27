// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { createContext, useContext, useEffect, useState, ReactNode } from 'react';
import { AuthProvider, AuthContextProps } from 'react-oauth2-code-pkce';
import { CognitoIdentityClient } from '@aws-sdk/client-cognito-identity';
import { fromCognitoIdentityPool } from '@aws-sdk/credential-provider-cognito-identity';
import { Alert, Spinner } from '@cloudscape-design/components';

export interface CognitoConfig {
  userPoolId: string;
  clientId: string;
  region: string;
  identityPoolId?: string;
  domain: string;
  redirectUri: string;
  logoutUri: string;
  scopes: string[];
}

export interface CognitoAuthContextProps extends AuthContextProps {
  cognitoConfig?: CognitoConfig;
  awsCredentials?: any;
  isConfigured: boolean;
  configError?: string;
}

const CognitoAuthContext = createContext<CognitoAuthContextProps | null>(null);

export const useCognitoAuth = (): CognitoAuthContextProps => {
  const context = useContext(CognitoAuthContext);
  if (!context) {
    throw new Error('useCognitoAuth must be used within a CognitoAuthProvider');
  }
  return context;
};

interface CognitoAuthProviderProps {
  children: ReactNode;
  config: CognitoConfig;
  isDemoMode?: boolean;
}

export const CognitoAuthProvider: React.FC<CognitoAuthProviderProps> = ({
  children,
  config,
  isDemoMode = false,
}) => {
  const [isConfigured, setIsConfigured] = useState(false);
  const [configError, setConfigError] = useState<string>();
  const [awsCredentials, setAwsCredentials] = useState<any>();

  // Validate configuration
  useEffect(() => {
    if (isDemoMode) {
      setIsConfigured(true);
      return;
    }

    const requiredFields = ['userPoolId', 'clientId', 'region', 'domain'];
    const missingFields = requiredFields.filter(field => !config[field as keyof CognitoConfig]);

    if (missingFields.length > 0) {
      setConfigError(`Missing required Cognito configuration: ${missingFields.join(', ')}`);
      return;
    }

    // Validate format
    if (!config.userPoolId.match(/^[a-zA-Z0-9_-]+$/)) {
      setConfigError('Invalid User Pool ID format');
      return;
    }

    if (!config.clientId.match(/^[a-zA-Z0-9]+$/)) {
      setConfigError('Invalid Client ID format');
      return;
    }

    setIsConfigured(true);
  }, [config, isDemoMode]);

  // Setup AWS credentials when identity pool is available
  useEffect(() => {
    if (!isConfigured || isDemoMode || !config.identityPoolId) {
      return;
    }

    try {
      const credentials = fromCognitoIdentityPool({
        client: new CognitoIdentityClient({ region: config.region }),
        identityPoolId: config.identityPoolId,
      });

      setAwsCredentials(credentials);
    } catch (error) {
      console.error('Failed to setup AWS credentials:', error);
      setConfigError('Failed to setup AWS credentials');
    }
  }, [isConfigured, config, isDemoMode]);

  // Demo mode mock context
  if (isDemoMode) {
    const mockContext: CognitoAuthContextProps = {
      tokenData: { 
        username: 'demo-user',
        sub: 'demo-sub-123',
        email: 'demo@example.com',
        access_token: 'demo-access-token',
        expires_at: new Date(Date.now() + 3600000).toISOString(), // 1 hour from now
      },
      idTokenData: { 
        email: 'demo@example.com',
        name: 'Demo User',
        sub: 'demo-sub-123'
      },
      idToken: 'demo-id-token', // Add this for map authentication
      loginInProgress: false,
      error: undefined,
      logIn: () => console.log('Demo mode: Mock login'),
      logOut: () => console.log('Demo mode: Mock logout'),
      cognitoConfig: config,
      awsCredentials: undefined,
      isConfigured: true,
    };

    return (
      <CognitoAuthContext.Provider value={mockContext}>
        {children}
      </CognitoAuthContext.Provider>
    );
  }

  // Configuration error state
  if (configError) {
    return (
      <div style={{ padding: '20px' }}>
        <Alert
          statusIconAriaLabel="Error"
          type="error"
          header="Authentication Configuration Error"
        >
          {configError}
        </Alert>
      </div>
    );
  }

  // Loading state
  if (!isConfigured) {
    return (
      <div style={{ 
        display: 'flex', 
        justifyContent: 'center', 
        alignItems: 'center', 
        height: '100vh',
        flexDirection: 'column',
        gap: '16px'
      }}>
        <Spinner size="large" />
        <div>Configuring authentication...</div>
      </div>
    );
  }

  // OAuth2 configuration
  const authConfig = {
    clientId: config.clientId,
    authorizationEndpoint: `https://${config.domain}/oauth2/authorize`,
    tokenEndpoint: `https://${config.domain}/oauth2/token`,
    redirectUri: config.redirectUri,
    scope: config.scopes.join(' '),
    autoLogin: false,
    clearURL: true,
    extraAuthParams: {
      response_type: 'code',
    },
    extraTokenParams: {},
    onRefreshTokenExpire: (event: any) => {
      console.warn('Refresh token expired:', event);
      // Force re-login
      window.location.reload();
    },
  };

  return (
    <AuthProvider authConfig={authConfig}>
      <CognitoAuthWrapper 
        config={config} 
        awsCredentials={awsCredentials}
        isConfigured={isConfigured}
      >
        {children}
      </CognitoAuthWrapper>
    </AuthProvider>
  );
};

interface CognitoAuthWrapperProps {
  children: ReactNode;
  config: CognitoConfig;
  awsCredentials?: any;
  isConfigured: boolean;
}

const CognitoAuthWrapper: React.FC<CognitoAuthWrapperProps> = ({
  children,
  config,
  awsCredentials,
  isConfigured,
}) => {
  const authContext = useContext(AuthProvider as any);

  const enhancedContext: CognitoAuthContextProps = {
    ...authContext,
    cognitoConfig: config,
    awsCredentials,
    isConfigured,
  };

  return (
    <CognitoAuthContext.Provider value={enhancedContext}>
      {children}
    </CognitoAuthContext.Provider>
  );
};

export default CognitoAuthProvider;
