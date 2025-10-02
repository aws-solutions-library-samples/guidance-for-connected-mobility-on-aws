// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import "@cloudscape-design/global-styles/index.css";

import { Mode, applyMode } from "@cloudscape-design/global-styles";

import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { getRuntimeConfig, getApiEndpoint } from './config/api';
import App from "./App";
import { UserContextProvider } from "./components/commons/UserContext";
import { ApiProviderWithAuth, ApiConfig } from "./api/provider";
import { SimpleAuthProvider } from "./auth/SimpleAuthProvider";

// apply a color mode
applyMode(Mode.Light);

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
    
    // Minimal fallback for development
    return {
      awsRegion: "us-east-1",
      isDemoMode: "false",
      apiEndpoint: getApiEndpoint(),
      awsCredentials: {
        region: "us-east-1",
        identityPoolId: "us-east-1:238cef9d-33b7-4c3c-b6f3-4366e94935c9",
        userPoolId: "us-east-1_blEKnf4xG",
        userPoolWebClientId: "bja14roqjh2dgv10734k08ss8"
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
