// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { Amplify } from 'aws-amplify';
import { getRuntimeConfig } from '../../../config/api';

export interface AmplifyConfig {
  userPoolId: string;
  userPoolWebClientId: string;
  region: string;
  identityPoolId: string;
}

export const configureAmplify = (config: AmplifyConfig) => {
  const redirectUrl = `${window.location.origin}/auth/callback`;
  const runtimeConfig = getRuntimeConfig();
  const cognitoDomain = runtimeConfig.cognitoDomain
    || `${config.userPoolId}.auth.${config.region}.amazoncognito.com`;
  Amplify.configure({
    Auth: {
      Cognito: {
        userPoolId: config.userPoolId,
        userPoolClientId: config.userPoolWebClientId,
        identityPoolId: config.identityPoolId,
        loginWith: {
          email: true,
          oauth: {
            domain: cognitoDomain,
            scopes: ['openid', 'email', 'profile'],
            redirectSignIn: [redirectUrl],
            redirectSignOut: [window.location.origin],
            responseType: 'code',
          },
        },
        signUpVerificationMethod: 'code',
        userAttributes: {
          email: {
            required: true,
          },
        },
        allowGuestAccess: false,
        passwordFormat: {
          minLength: 8,
          requireLowercase: true,
          requireUppercase: true,
          requireNumbers: true,
          requireSpecialCharacters: true,
        },
      },
    },
    API: {
      REST: {
        'cms-api': {
          endpoint: 'getApiEndpoint()',
          region: config.region,
        },
      },
    },
  });
};
