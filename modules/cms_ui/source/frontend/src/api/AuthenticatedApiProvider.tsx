// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { createContext, useContext, ReactNode } from 'react';
import { useAuth } from '../auth/useAuth';
import { AuthenticatedApiClient, ApiClientConfig } from '../auth/AuthenticatedApiClient';

interface ApiContextProps {
  apiClient: AuthenticatedApiClient;
  baseUrl: string;
  isDemoMode: boolean;
}

const ApiContext = createContext<ApiContextProps | null>(null);

export const useApi = (): ApiContextProps => {
  const context = useContext(ApiContext);
  if (!context) {
    throw new Error('useApi must be used within an ApiProvider');
  }
  return context;
};

interface ApiProviderProps {
  children: ReactNode;
  config: ApiClientConfig;
}

export const AuthenticatedApiProvider: React.FC<ApiProviderProps> = ({
  children,
  config,
}) => {
  const auth = useAuth();

  const apiClient = new AuthenticatedApiClient(
    config,
    auth.getAuthHeaders,
    auth.isTokenValid,
    auth.login
  );

  const contextValue: ApiContextProps = {
    apiClient,
    baseUrl: config.baseUrl,
    isDemoMode: config.isDemoMode || false,
  };

  return (
    <ApiContext.Provider value={contextValue}>
      {children}
    </ApiContext.Provider>
  );
};

// Convenience hooks for common API operations
export const useApiClient = () => {
  const { apiClient } = useApi();
  return apiClient;
};

export const useFleetManagementApi = () => {
  const apiClient = useApiClient();

  return {
    // Fleet operations
    getFleets: (params?: any) => apiClient.get('/fleet-management/fleets', params),
    createFleet: (fleetData: any) => apiClient.post('/fleet-management/fleets', fleetData),
    getFleet: (fleetId: string) => apiClient.get(`/fleet-management/fleets/${fleetId}`),
    updateFleet: (fleetId: string, fleetData: any) => apiClient.put(`/fleet-management/fleets/${fleetId}`, fleetData),
    deleteFleet: (fleetId: string) => apiClient.delete(`/fleet-management/fleets/${fleetId}`),

    // Vehicle operations
    getVehicles: (params?: any) => apiClient.get('/fleet-management/vehicles', params),
    createVehicle: (vehicleData: any) => apiClient.post('/fleet-management/vehicles', vehicleData),
    getVehicle: (vehicleId: string) => apiClient.get(`/fleet-management/vehicles/${vehicleId}`),
    updateVehicle: (vehicleId: string, vehicleData: any) => apiClient.put(`/fleet-management/vehicles/${vehicleId}`, vehicleData),
    deleteVehicle: (vehicleId: string) => apiClient.delete(`/fleet-management/vehicles/${vehicleId}`),

    // Campaign operations
    getCampaigns: (params?: any) => apiClient.get('/campaign', params),
    createCampaign: (campaignData: any) => apiClient.post('/campaign', campaignData),
    getCampaign: (campaignName: string) => apiClient.get(`/campaign/${campaignName}`),
    updateCampaign: (campaignName: string, campaignData: any) => apiClient.put(`/campaign/${campaignName}`, campaignData),
    deleteCampaign: (campaignName: string) => apiClient.delete(`/campaign/${campaignName}`),
    startCampaign: (campaignName: string) => apiClient.post(`/campaign/${campaignName}/start`),
    stopCampaign: (campaignName: string) => apiClient.post(`/campaign/${campaignName}/stop`),
    getCampaignsForTarget: (targetType: string, targetId: string) => 
      apiClient.get(`/campaign/list/${targetType}/${targetId}`),
  };
};

export const useDeviceManagementApi = () => {
  const apiClient = useApiClient();

  return {
    // Health and metrics
    getHealth: () => apiClient.get('/iot-api/health'),
    getMetricsStatistics: () => apiClient.get('/iot-api/metrics/statistics'),
    getMetricsData: (params?: any) => apiClient.get('/iot-api/metrics/data', params),

    // Connections
    listConnections: (request: any = {}) => apiClient.post('/iot-api/connections/list', request),
    getConnection: (clientId: string) => apiClient.get(`/iot-api/connections/${clientId}`),
    startConnectionMetrics: (clientId: string) => apiClient.post(`/iot-api/connections/${clientId}/metrics`),
    getConnectionMetrics: (clientId: string, executionId: string) => 
      apiClient.get(`/iot-api/connections/${clientId}/metrics/${executionId}`),

    // Subscriptions
    listSubscriptions: (request: any = {}) => apiClient.post('/iot-api/subscriptions/list', request),

    // Topics
    listTopics: (request: any = {}) => apiClient.post('/iot-api/topics/list', request),
    startTopicMetrics: () => apiClient.post('/iot-api/topics/metrics'),
    getTopicMetrics: (executionId: string) => apiClient.get(`/iot-api/topics/metrics/${executionId}`),

    // Users
    listUsers: (request: any = {}) => apiClient.post('/iot-api/users/list', request),
    getUser: (userUid: string) => apiClient.post(`/iot-api/users/${userUid}`),
    createUser: (userData: any) => apiClient.post('/iot-api/users', userData),
    updateUser: (userUid: string, userData: any) => apiClient.put(`/iot-api/users/${userUid}`, userData),
    deleteUser: (userUid: string) => apiClient.delete(`/iot-api/users/${userUid}`),

    // Policies
    listPolicies: (request: any = {}) => apiClient.post('/iot-api/policies/list', request),
    getPolicy: (policyUid: string) => apiClient.post(`/iot-api/policies/${policyUid}`),
    createPolicy: (policyData: any) => apiClient.post('/iot-api/policies', policyData),
    updatePolicy: (policyUid: string, policyData: any) => apiClient.put(`/iot-api/policies/${policyUid}`, policyData),
    deletePolicy: (policyUid: string) => apiClient.delete(`/iot-api/policies/${policyUid}`),

    // User-Policy relationships
    listUserPolicies: (userUid: string, request: any = {}) => 
      apiClient.post(`/iot-api/users/${userUid}/policies/list`, request),
    createUserPolicyRelation: (userUid: string, policyUid: string) => 
      apiClient.post(`/iot-api/users/${userUid}/policies/${policyUid}`),
    deleteUserPolicyRelation: (userUid: string, policyUid: string) => 
      apiClient.delete(`/iot-api/users/${userUid}/policies/${policyUid}`),
    listUsersByPolicy: (policyUid: string, request: any = {}) => 
      apiClient.post(`/iot-api/policies/${policyUid}/users/list`, request),

    // Alarms
    listAlarms: (request: any = {}) => apiClient.post('/iot-api/alarms/list', request),

    // Log events
    filterLogEvents: (request: any) => apiClient.post('/iot-api/filter-log-events', request),
  };
};

export const useUserPreferencesApi = () => {
  const apiClient = useApiClient();

  return {
    getUserPreferences: () => apiClient.get('/user-preferences'),
    updateUserPreferences: (preferences: any) => apiClient.put('/user-preferences', preferences),
  };
};

export const useAwsServicesApi = () => {
  const apiClient = useApiClient();

  return {
    getS3Buckets: () => apiClient.get('/aws-services/s3/buckets'),
    getS3Objects: (bucket: string, prefix?: string) => 
      apiClient.get('/aws-services/s3/objects', { bucket, prefix }),
  };
};

export default AuthenticatedApiProvider;
