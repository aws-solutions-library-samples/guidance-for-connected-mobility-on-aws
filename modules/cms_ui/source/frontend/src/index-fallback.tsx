// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import "@cloudscape-design/global-styles/index.css";
import { getRuntimeConfig } from '../../../config/api';

import { Mode, applyMode } from "@cloudscape-design/global-styles";

import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { UserContextProvider } from "./components/commons/UserContext";
import { ApiProviderWithAuth, ApiConfig } from "./api/provider";

// apply a color mode
applyMode(Mode.Light);

console.log('🚀 Starting app with fallback configuration...');

// Fallback configuration for development
const fallbackConfig = {
  awsRegion: "us-east-1",
  isDemoMode: "true", // Enable demo mode to bypass auth issues
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

const root = ReactDOM.createRoot(document.getElementById("root") as any);

const apiConfig: ApiConfig = {
  baseUrl: fallbackConfig.apiEndpoint,
  isDemoMode: "true", // Force demo mode
};

// Render in demo mode to bypass authentication
root.render(
  <React.StrictMode>
    <ApiProviderWithAuth apiConfig={apiConfig}>
      <BrowserRouter>
        <UserContextProvider>
          <App runtimeConfig={{...fallbackConfig, isDemoMode: true}} />
        </UserContextProvider>
      </BrowserRouter>
    </ApiProviderWithAuth>
  </React.StrictMode>
);
