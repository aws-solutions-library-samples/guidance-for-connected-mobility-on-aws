// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import "@cloudscape-design/global-styles/index.css";
import { getRuntimeConfig } from '../../../config/api';

import { Mode, applyMode } from "@cloudscape-design/global-styles";

import React, { useState, useRef } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "react-oauth2-code-pkce";
import { SessionExpiredModal } from "./components/commons/session-expired-modal";
import {
  TAuthConfig,
  TRefreshTokenExpiredEvent,
} from "react-oauth2-code-pkce/dist/types";
import { UserContextProvider } from "./components/commons/UserContext";
import { ApiProviderWithAuth, ApiConfig } from "./api/provider";

// apply a color mode
applyMode(Mode.Light);

export async function getRuntimeConfig() {
  try {
    console.log('🔧 Fetching runtime config...');
    const runtimeConfig = await fetch("/runtimeConfig.json");
    if (!runtimeConfig.ok) {
      throw new Error(`Failed to fetch runtime config: ${runtimeConfig.status}`);
    }
    const config = await runtimeConfig.json();
    console.log('✅ Runtime config loaded:', config);
    return config;
  } catch (error) {
    console.error('❌ Runtime config error:', error);
    // Return fallback config for development
    return {
      awsRegion: "us-east-1",
      isDemoMode: "true",
      apiEndpoint: "getApiEndpoint()",
      oAuth: {
        clientId: "test",
        scopes: "test",
        authorizationEndpoint: "test",
        tokenEndpoint: "test",
        logoutEndpoint: "test"
      },
      awsCredentials: {
        region: "us-east-1",
        identityPoolId: "test",
        userPoolId: "test"
      }
    };
  }
}

getRuntimeConfig().then(function (config) {
  console.log('🚀 Starting app with config:', config);
  const runtimeConfig = config;
  
  // Check if we're in local development mode
  const isLocalDemo = import.meta.env.VITE_LOCAL_DEMO === 'true' || import.meta.env.VITE_BYPASS_AUTH === 'true';

  //TODO: Read from config.json or dynamically

  const loginRedirectPathName = "callback";
  const loginRedirectUri = `${window.location.origin}/${loginRedirectPathName}`;
  runtimeConfig.oAuth.loginRedirectPathName = `/${loginRedirectPathName}`;

  const root = ReactDOM.createRoot(document.getElementById("root") as any);

  function Main() {
    const [showSessionExpiredModal, setShowSessionExpiredModal] =
      useState(false);
    const refreshTokenExpireEventRef = useRef<TRefreshTokenExpiredEvent | null>(
      null,
    );

    const handleSessionRefresh = () => {
      setShowSessionExpiredModal(false);
      if (refreshTokenExpireEventRef.current)
        refreshTokenExpireEventRef.current.logIn();
    };

    const authConfig: TAuthConfig = {
      clientId: runtimeConfig.oAuth.clientId,
      authorizationEndpoint: runtimeConfig.oAuth.authorizationEndpoint,
      tokenEndpoint: runtimeConfig.oAuth.tokenEndpoint,
      redirectUri: loginRedirectUri,
      clearURL: true,
      scope: runtimeConfig.oAuth.scopes,
      autoLogin: !isLocalDemo, // Disable auto-login in local demo mode
      decodeToken: true,
      logoutEndpoint: runtimeConfig.oAuth.logoutEndpoint,
      extraLogoutParameters: {
        redirect_uri: loginRedirectUri,
        response_type: "code",
      },
      refreshTokenExpiresIn: 10 * 24 * 60 * 60, //Default to 10 days in seconds
      onRefreshTokenExpire: (event: TRefreshTokenExpiredEvent) => {
        refreshTokenExpireEventRef.current = event;
        setShowSessionExpiredModal(true);
      },
      preLogin: () => {
        let currentPath = window.location.pathname;
        if (window.location.hash) currentPath += `/${window.location.hash}`;
        return localStorage.setItem("preLoginPath", currentPath);
      },
      postLogin: () =>
        window.location.replace(localStorage.getItem("preLoginPath") || ""),
    };

    const apiConfig: ApiConfig = {
      baseUrl: import.meta.env.VITE_API_BASE_URL || runtimeConfig.apiEndpoint,
      isDemoMode: (import.meta.env.VITE_LOCAL_DEMO === 'true' ? 'true' : 'false') || runtimeConfig.isDemoMode || isLocalDemo,
    };

    // For local development, render without AuthProvider
    if (isLocalDemo) {
      return (
        <ApiProviderWithAuth apiConfig={apiConfig}>
          <BrowserRouter>
            <UserContextProvider>
              <App runtimeConfig={{...runtimeConfig, isDemoMode: true}} />
            </UserContextProvider>
          </BrowserRouter>
        </ApiProviderWithAuth>
      );
    }

    // For production, use full authentication
    return (
      <AuthProvider authConfig={authConfig}>
        <ApiProviderWithAuth apiConfig={apiConfig}>
          <BrowserRouter>
            <UserContextProvider>
              <App runtimeConfig={runtimeConfig} />
              <SessionExpiredModal
                visible={showSessionExpiredModal}
                onRefresh={handleSessionRefresh}
              />
            </UserContextProvider>
          </BrowserRouter>
        </ApiProviderWithAuth>
      </AuthProvider>
    );
  }

  root.render(
    <React.StrictMode>
      <Main />
    </React.StrictMode>,
  );
}).catch(error => {
  console.error('❌ App initialization failed:', error);
  
  // Render error page
  const root = ReactDOM.createRoot(document.getElementById("root") as any);
  root.render(
    <div style={{ padding: '20px', fontFamily: 'Arial, sans-serif' }}>
      <h1 style={{ color: 'red' }}>Application Error</h1>
      <p>Failed to initialize the application.</p>
      <p><strong>Error:</strong> {error.message}</p>
      <p>Please check the browser console for more details.</p>
      <button onClick={() => window.location.reload()}>Reload Page</button>
    </div>
  );
});
