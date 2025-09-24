// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { CognitoConfig } from './CognitoAuthProvider';

export interface RuntimeConfig {
  awsRegion: string;
  isDemoMode?: string | boolean;
  apiEndpoint: string;
  oAuth?: {
    clientId: string;
    scopes: string;
    authorizationEndpoint: string;
    tokenEndpoint: string;
    logoutEndpoint: string;
  };
  awsCredentials?: {
    region: string;
    identityPoolId: string;
    userPoolId?: string;
  };
  mapAuth?: {
    identityPoolClient: string;
    identityPoolId: string;
    mapName: string;
  };
  cognitoDomain?: string;
}

export const createCognitoConfig = (runtimeConfig: RuntimeConfig): CognitoConfig | null => {
  // Check if we're in demo mode
  const isDemoMode = runtimeConfig.isDemoMode === true || 
                     runtimeConfig.isDemoMode === 'true' ||
                     import.meta.env.VITE_LOCAL_DEMO === 'true' ||
                     import.meta.env.VITE_BYPASS_AUTH === 'true';

  if (isDemoMode) {
    // Return minimal config for demo mode
    return {
      userPoolId: 'demo',
      clientId: 'demo',
      region: runtimeConfig.awsRegion || 'us-east-1',
      domain: 'demo.auth.amazonaws.com',
      redirectUri: window.location.origin,
      logoutUri: window.location.origin,
      scopes: ['openid', 'email', 'profile'],
    };
  }

  // Validate required OAuth configuration
  if (!runtimeConfig.oAuth?.clientId) {
    console.error('Missing OAuth client ID in runtime configuration');
    return null;
  }

  // Extract User Pool ID from mapAuth.identityPoolClient if available
  let userPoolId = runtimeConfig.awsCredentials?.userPoolId;
  if (!userPoolId && runtimeConfig.mapAuth?.identityPoolClient) {
    // Format: "cognito-idp.us-east-1.amazonaws.com/us-east-1_XXXXXXXXX"
    const match = runtimeConfig.mapAuth.identityPoolClient.match(/\/([^\/]+)$/);
    if (match) {
      userPoolId = match[1];
      console.log('✅ Extracted User Pool ID from mapAuth:', userPoolId);
    }
  }

  if (!userPoolId) {
    console.error('Could not determine User Pool ID from runtime configuration');
    return null;
  }

  // Extract domain from authorization endpoint
  let domain = runtimeConfig.cognitoDomain;
  if (!domain && runtimeConfig.oAuth.authorizationEndpoint) {
    try {
      const url = new URL(runtimeConfig.oAuth.authorizationEndpoint);
      domain = url.hostname;
    } catch (error) {
      console.error('Failed to extract domain from authorization endpoint:', error);
    }
  }

  if (!domain) {
    console.error('Could not determine Cognito domain');
    return null;
  }

  // Parse scopes
  const scopes = runtimeConfig.oAuth.scopes 
    ? runtimeConfig.oAuth.scopes.split(' ')
    : ['openid', 'email', 'profile'];

  // Use identity pool ID from awsCredentials or mapAuth
  const identityPoolId = runtimeConfig.awsCredentials?.identityPoolId || 
                         runtimeConfig.mapAuth?.identityPoolId;

  const config: CognitoConfig = {
    userPoolId,
    clientId: runtimeConfig.oAuth.clientId,
    region: runtimeConfig.awsCredentials?.region || runtimeConfig.awsRegion,
    identityPoolId,
    domain,
    redirectUri: window.location.origin,
    logoutUri: window.location.origin,
    scopes,
  };

  console.log('🔧 Created Cognito configuration:', {
    userPoolId: config.userPoolId,
    clientId: config.clientId,
    domain: config.domain,
    region: config.region,
    identityPoolId: config.identityPoolId,
    scopes: config.scopes,
  });

  return config;
};

export const validateAuthConfig = (config: CognitoConfig): string[] => {
  const errors: string[] = [];

  if (!config.userPoolId) {
    errors.push('User Pool ID is required');
  }

  if (!config.clientId) {
    errors.push('Client ID is required');
  }

  if (!config.region) {
    errors.push('AWS Region is required');
  }

  if (!config.domain) {
    errors.push('Cognito Domain is required');
  }

  if (!config.redirectUri) {
    errors.push('Redirect URI is required');
  }

  if (!config.scopes || config.scopes.length === 0) {
    errors.push('OAuth scopes are required');
  }

  return errors;
};

export const getAuthHeaders = (tokenData: any): Record<string, string> => {
  if (!tokenData?.access_token) {
    return {};
  }

  return {
    'Authorization': `Bearer ${tokenData.access_token}`,
    'Content-Type': 'application/json',
  };
};

export const isTokenExpired = (tokenData: any): boolean => {
  if (!tokenData?.expires_at) {
    return true;
  }

  const expirationTime = new Date(tokenData.expires_at).getTime();
  const currentTime = Date.now();
  const bufferTime = 5 * 60 * 1000; // 5 minutes buffer

  return currentTime >= (expirationTime - bufferTime);
};

export const getUserInfo = (idTokenData: any) => {
  return {
    username: idTokenData?.preferred_username || 
              idTokenData?.['cognito:username'] || 
              idTokenData?.email ||
              idTokenData?.sub,
    email: idTokenData?.email,
    name: idTokenData?.name || 
          idTokenData?.given_name || 
          idTokenData?.family_name ||
          (idTokenData?.given_name && idTokenData?.family_name ? 
            `${idTokenData.given_name} ${idTokenData.family_name}` : undefined),
    groups: idTokenData?.['cognito:groups'] || [],
    roles: idTokenData?.['custom:roles'] || [],
  };
};
