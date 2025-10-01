// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { FleetManagementClient } from "./fleet-management-client";
import { MaintenanceAlertsClient } from "./maintenance-alerts-client";
import React, { createContext, useMemo, ReactNode, useContext } from "react";
import { createFleetManagementClient, createMaintenanceAlertsClient } from "./client";
import { useAuth } from "../auth/useAuth";
import { AwsCredentialsConfig, CredentialsProvider } from "./credentials-provider";

export interface ApiConfig {
  baseUrl: string;
  isDemoMode: "true" | "false";
  awsCredentials?: AwsCredentialsConfig;
}

export interface ApiContextValue {
  config: ApiConfig;
  token: string;
  client: FleetManagementClient;
  maintenanceAlertsClient: MaintenanceAlertsClient;
  credentialsProvider?: CredentialsProvider;
}

// Default to local simulation API for development
export const ApiContext = createContext<ApiContextValue>({
  config: { 
    baseUrl: "http://localhost:5001", 
    isDemoMode: "false" 
  },
  token: "",
  client: createFleetManagementClient(
    { 
      baseUrl: "http://localhost:5001", 
      isDemoMode: "false" 
    },
    "",
  ),
  maintenanceAlertsClient: createMaintenanceAlertsClient(
    { 
      baseUrl: "http://localhost:5001", 
      isDemoMode: "false" 
    },
    "",
  ),
});

/**
 * Hook to access the API client from the ApiContext
 * @returns The FleetManagementClient instance from the context
 */
export const useApiClient = (): FleetManagementClient => {
  const { client } = useContext(ApiContext);
  return client;
};

/**
 * Hook to access the maintenance alerts client from the ApiContext
 * @returns The MaintenanceAlertsClient instance from the context
 */
export const useMaintenanceAlertsClient = (): MaintenanceAlertsClient => {
  const { maintenanceAlertsClient } = useContext(ApiContext);
  return maintenanceAlertsClient;
};

/**
 * Hook to access the credentials provider from the ApiContext
 * @returns The CredentialsProvider instance from the context
 */
export const useCredentialsProvider = (): CredentialsProvider | undefined => {
  const { credentialsProvider } = useContext(ApiContext);
  return credentialsProvider;
};

interface ApiProviderProps {
  children: ReactNode;
  apiConfig: ApiConfig;
  token: string;
}

interface ApiProviderWithAuthProps {
  children: ReactNode;
  apiConfig: ApiConfig;
}

export const ApiProviderWithAuth = ({
  children,
  apiConfig,
}: ApiProviderWithAuthProps) => {
  const auth = useAuth();
  return React.createElement(ApiProvider, {
    children: children,
    apiConfig: apiConfig,
    token: auth.getAccessToken(),
  });
};

export const ApiProvider = ({
  children,
  apiConfig,
  token,
}: ApiProviderProps) => {
  // Use the provided API configuration instead of hardcoded localhost
  const effectiveApiConfig = {
    ...apiConfig,
    // Only override with localhost in development if no baseUrl is provided
    baseUrl: apiConfig.baseUrl || "http://localhost:5001",
  };

  const credentialsProvider = useMemo(() => {
    if (effectiveApiConfig.awsCredentials && token) {
      return new CredentialsProvider(effectiveApiConfig.awsCredentials, token);
    }
    return undefined;
  }, [effectiveApiConfig.awsCredentials, token]);

  const apiValue = useMemo<ApiContextValue>(
    () => ({
      config: effectiveApiConfig,
      token,
      client: createFleetManagementClient(effectiveApiConfig, token),
      maintenanceAlertsClient: createMaintenanceAlertsClient(effectiveApiConfig, token),
      credentialsProvider,
    }),
    [effectiveApiConfig, token, credentialsProvider],
  );

  return React.createElement(
    ApiContext.Provider,
    { value: apiValue },
    children,
  );
};
