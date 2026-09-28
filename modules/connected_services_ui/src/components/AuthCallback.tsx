// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * AuthCallback — OAuth authorization code callback handler.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/ T9.1
 *
 * This component is mounted at /auth/callback (an unprotected route).  It
 * reads the `code` search param, exchanges it at the Cognito token endpoint,
 * stores the tokens, and navigates to the pre-auth URL.
 *
 * Mirrors CMS SimpleAuthProvider.tsx token-exchange effect (lines 302-330):
 *   POST https://<cognitoDomain>/oauth2/token
 *   body: grant_type=authorization_code, client_id, code, redirect_uri
 *   Stores: sessionStorage 'authToken' (access_token), 'idToken' (id_token)
 *   Restores: sessionStorage 'preAuthUrl'
 */

import Box from "@cloudscape-design/components/box";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";
import React, { useEffect, useState } from "react";
import { getRuntimeConfig } from "../env";
import { handleOAuthCallback } from "../auth";

/**
 * Handle the Cognito Hosted UI callback.
 *
 * This component is NOT wrapped in RequireAuth — it must be reachable by
 * unauthenticated users completing the OAuth round-trip.
 */
const AuthCallback: React.FC = () => {
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const state = params.get("state");

    if (!code) {
      setError("No authorization code received. Please try signing in again.");
      return;
    }

    let config;
    try {
      config = getRuntimeConfig();
    } catch (e) {
      setError("Runtime configuration is not available. Please contact support.");
      return;
    }

    // `state` is passed through even when absent — handleOAuthCallback fails closed on
    // a null incoming state rather than treating it as "skip the CSRF check".
    handleOAuthCallback(code, state, config)
      .then((preAuthUrl) => {
        window.location.replace(preAuthUrl);
      })
      .catch(() => {
        // Deliberately generic: the specific reason (state mismatch vs. missing
        // verifier vs. rejected code) is not something to disclose to whoever
        // arrived at this URL.
        setError("Sign-in could not be completed. Please try signing in again.");
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error) {
    return (
      <Box padding="xxl" data-testid="auth-callback-error">
        <Box variant="h2" color="text-status-error">
          Sign-in failed
        </Box>
        <Box variant="p">{error}</Box>
      </Box>
    );
  }

  return (
    <Box padding="xxl" data-testid="auth-callback-loading" textAlign="center">
      <SpaceBetween size="m" direction="vertical">
        <Spinner size="large" />
        <Box variant="p" color="text-body-secondary">
          Completing sign-in…
        </Box>
      </SpaceBetween>
    </Box>
  );
};

export default AuthCallback;
