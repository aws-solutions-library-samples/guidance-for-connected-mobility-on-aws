// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import "./auth/fetchInterceptor";
import "@cloudscape-design/global-styles/index.css";
import "./styles/theme.css";

// Patch global fetch to auto-inject Cognito ID token for API calls
const _origFetch = window.fetch;
window.fetch = function(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : (input as Request).url;
  if (url.includes('execute-api') || url.includes('/api/v1/')) {
    const token = sessionStorage.getItem('idToken') || localStorage.getItem('idToken') ||
                  sessionStorage.getItem('authToken') || localStorage.getItem('authToken');
    if (token) {
      const headers = new Headers(init?.headers);
      if (!headers.has('Authorization')) {
        headers.set('Authorization', `Bearer ${token}`);
      }
      return _origFetch(input, { ...init, headers });
    }
  }
  return _origFetch(input, init);
};

import { Mode, applyMode } from "@cloudscape-design/global-styles";

import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { getRuntimeConfig, getApiEndpoint } from './config/api';
import App from "./App";
import { UserContextProvider } from "./components/commons/UserContext";
import { ApiProviderWithAuth, ApiConfig } from "./api/provider";
import { SimpleAuthProvider } from "./auth/SimpleAuthProvider";

// apply saved color mode or default to dark
const savedMode = localStorage.getItem('Awsui-Theme-Preference');
const initialMode = savedMode === '"Light"' ? Mode.Light : Mode.Dark;
applyMode(initialMode);

console.log('🚀 Starting Connected Mobility Solution UI with native authentication...');

// Load runtime configuration
const loadRuntimeConfig = async () => {
  try {
    const response = await fetch('/runtimeConfig.json');
    if (response.ok) {
      const config = await response.json();
      console.log('✅ Loaded runtime configuration from /runtimeConfig.json');
      return config;
    }
  } catch (error) {
    console.error('❌ Failed to load runtime configuration:', error);
    console.warn('⚠️ Using fallback configuration for development');
    
    // Minimal fallback for development.
    //
    // SECURITY NOTE: do NOT hardcode real Cognito or environment-specific
    // identifiers here — those would ship in the JS bundle to whatever
    // CloudFront URL serves this app, including public mirrors. The
    // fallback below uses empty/placeholder strings only; runtime
    // initialization should fail-closed if /runtimeConfig.json was not
    // deployed (see the throw below). The earlier hardcoded
    // identityPoolId / userPoolId / userPoolWebClientId values were
    // foreign-environment Cognito IDs that ended up on public CloudFront —
    // remediated 2026-05-26 by Fix Group 1.2 of the
    // 2026-05-26-cms-public-mirror-and-quick-ui spec.
    return {
      awsRegion: "us-east-1",
      isDemoMode: "false",
      apiEndpoint: getApiEndpoint(),
      awsCredentials: {
        region: "us-east-1",
        identityPoolId: "",
        userPoolId: "",
        userPoolWebClientId: ""
      }
    };
  }

  // This should not be reached if config is loaded successfully
  throw new Error('Runtime configuration not found. Please ensure the configuration is properly deployed.');
};

const initializeApp = async () => {
  const runtimeConfig = await loadRuntimeConfig();
  
  // Set runtime config on window for other components to access
  (window as any).runtimeConfig = runtimeConfig;
  
  console.log('📋 Runtime config loaded:', runtimeConfig);
  
  const isDemoMode = runtimeConfig.isDemoMode === true || 
                     runtimeConfig.isDemoMode === 'true';

  console.log(`🔐 Authentication mode: ${isDemoMode ? 'Demo' : 'Cognito'}`);

  const apiConfig: ApiConfig = {
    baseUrl: runtimeConfig.apiEndpoint || getApiEndpoint(),
    isDemoMode: isDemoMode ? "true" : "false",
    awsCredentials: runtimeConfig.awsCredentials,
  };

  console.log('🔧 API config created:', apiConfig);

  if (!apiConfig.baseUrl) {
    console.error('❌ API config missing baseUrl:', apiConfig);
    return;
  }

  const root = ReactDOM.createRoot(document.getElementById("root") as any);

  root.render(
    <React.StrictMode>
      <SimpleAuthProvider 
        userPoolId={runtimeConfig.awsCredentials?.userPoolId}
        clientId={runtimeConfig.awsCredentials?.userPoolWebClientId}
        region={runtimeConfig.awsCredentials?.region}
        isDemoMode={isDemoMode}
      >
        <BrowserRouter>
          <UserContextProvider>
            <ApiProviderWithAuth apiConfig={apiConfig}>
              <App runtimeConfig={runtimeConfig} />
            </ApiProviderWithAuth>
          </UserContextProvider>
        </BrowserRouter>
      </SimpleAuthProvider>
    </React.StrictMode>
  );
};

initializeApp().catch(error => {
  console.error('❌ Failed to initialize application:', error);
});
