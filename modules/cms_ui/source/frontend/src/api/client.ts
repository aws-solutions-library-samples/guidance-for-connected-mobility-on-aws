// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { MaintenanceAlertsClient, RealMaintenanceAlertsClient } from "./maintenance-alerts-client";
import { ApiConfig } from "./provider";
import { MockFleetManagementClient } from "./mock/client";

export const createFleetManagementClient = (
  config: ApiConfig,
  authToken: string,
) => {
  // Since we removed fleet-management-client, return a simple mock
  console.log('🌐 Fleet management client disabled - using direct API calls');
  return {
    send: () => Promise.resolve({}),
  };
};

export const createMaintenanceAlertsClient = (
  config: ApiConfig,
  authToken: string,
): MaintenanceAlertsClient => {
  // Always use real API client - no demo mode for maintenance alerts
  const client = new RealMaintenanceAlertsClient({
    endpoint: config.baseUrl,
  });
  return client;
};
