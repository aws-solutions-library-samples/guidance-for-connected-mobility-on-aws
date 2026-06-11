// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

// Note: Removed Smithy dependency - implement as needed

// Maintenance Alert interfaces
export interface MaintenanceAlert {
  id: string;
  type: string;
  severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  priority: 'LOW' | 'MEDIUM' | 'HIGH';
  urgency: 'ROUTINE' | 'MODERATE' | 'URGENT';
  timestamp: string;
  vehicleId: string;
  location: {
    lat: number;
    lng: number;
  };
  description: string;
  fleetId: string;
  vin: string;
  estimatedCost: number;
  category: string;
  resolved: boolean;
}

export interface MaintenanceStats {
  totalAlerts: number;
  criticalAlerts: number;
  highAlerts: number;
  warningAlerts: number;
  infoAlerts: number;
  alertsByType: Record<string, number>;
  timeRange?: {
    startTime?: string;
    endTime?: string;
  };
  note?: string;
}

export interface MaintenanceAlertsResponse {
  alerts: MaintenanceAlert[];
  pagination: {
    total: number;
    page: number;
    limit: number;
    totalPages: number;
    hasNextPage: boolean;
    hasPrevPage: boolean;
    returned: number;
  };
  message?: string;
}

// Command interfaces
export interface ListMaintenanceAlertsInput {
  startTime?: number;
  endTime?: number;
  alertType?: string;
  vehicleId?: string;
  fleetId?: string;  // Add fleetId parameter
  severity?: string;
  priority?: string;
  category?: string;
  limit?: number;
  page?: number;
}

export interface GetMaintenanceStatsInput {
  startTime?: number;
  endTime?: number;
  limit?: number;
}

export interface GetMaintenanceAlertInput {
  alertId: string;
}

// Commands
export class ListMaintenanceAlertsCommand implements Command<ListMaintenanceAlertsInput, MaintenanceAlertsResponse> {
  readonly input: ListMaintenanceAlertsInput;

  constructor(input: ListMaintenanceAlertsInput = {}) {
    this.input = input;
  }
}

export class GetMaintenanceStatsCommand implements Command<GetMaintenanceStatsInput, MaintenanceStats> {
  readonly input: GetMaintenanceStatsInput;

  constructor(input: GetMaintenanceStatsInput = {}) {
    this.input = input;
  }
}

export class GetMaintenanceAlertCommand implements Command<GetMaintenanceAlertInput, MaintenanceAlert> {
  readonly input: GetMaintenanceAlertInput;

  constructor(input: GetMaintenanceAlertInput) {
    this.input = input;
  }
}

// Client configuration
export interface MaintenanceAlertsClientConfig {
  endpoint: string;
}

// Base client class
export abstract class MaintenanceAlertsClient {
  abstract send<TInput, TOutput>(command: Command<TInput, TOutput>): Promise<TOutput>;
}

// Real implementation
export class RealMaintenanceAlertsClient extends MaintenanceAlertsClient {
  private config: MaintenanceAlertsClientConfig;

  constructor(config: MaintenanceAlertsClientConfig) {
    super();
    this.config = config;
  }

  async send<TInput, TOutput>(command: Command<TInput, TOutput>): Promise<TOutput> {
    if (command instanceof ListMaintenanceAlertsCommand) {
      return this.listMaintenanceAlerts(command.input) as Promise<TOutput>;
    } else if (command instanceof GetMaintenanceStatsCommand) {
      return this.getMaintenanceStats(command.input) as Promise<TOutput>;
    } else if (command instanceof GetMaintenanceAlertCommand) {
      return this.getMaintenanceAlert(command.input) as Promise<TOutput>;
    } else {
      throw new Error(`Unknown command: ${command.constructor.name}`);
    }
  }

  private async listMaintenanceAlerts(input: any): Promise<MaintenanceAlertsResponse> {
    const queryParams = new URLSearchParams();
    
    if (input.startTime) queryParams.append('startTime', input.startTime.toString());
    if (input.endTime) queryParams.append('endTime', input.endTime.toString());
    if (input.alertType) queryParams.append('alertType', input.alertType);
    if (input.vehicleId) queryParams.append('vehicleId', input.vehicleId);
    if (input.fleetId) queryParams.append('fleetId', input.fleetId);  // Add missing fleetId parameter
    if (input.severity) queryParams.append('severity', input.severity);
    if (input.priority) queryParams.append('priority', input.priority);
    if (input.category) queryParams.append('category', input.category);
    if (input.limit) queryParams.append('limit', input.limit.toString());
    if (input.page) queryParams.append('page', input.page.toString());

    const url = `${this.config.endpoint}/api/v1/maintenance-alerts${queryParams.toString() ? `?${queryParams.toString()}` : ''}`;

    console.log(`🔧 Config endpoint: ${this.config.endpoint}`);
    console.log(`🔧 Fetching maintenance alerts from: ${url}`);

    try {
      const response = await fetch(url, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();
      console.log('🔧 Maintenance alerts API response:', data);

      return data;
    } catch (error) {
      console.error('🔧 Error fetching maintenance alerts:', error);
      throw error;
    }
  }

  private async getMaintenanceStats(input: any): Promise<MaintenanceStats> {
    const queryParams = new URLSearchParams();
    
    if (input.startTime) queryParams.append('startTime', input.startTime.toString());
    if (input.endTime) queryParams.append('endTime', input.endTime.toString());
    if (input.limit) queryParams.append('limit', input.limit.toString());

    const url = `${this.config.endpoint}/api/v1/maintenance-alerts/stats${queryParams.toString() ? `?${queryParams.toString()}` : ''}`;

    console.log(`🔧 Config endpoint: ${this.config.endpoint}`);
    console.log(`🔧 Fetching maintenance stats from: ${url}`);

    try {
      const response = await fetch(url, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();
      console.log('🔧 Maintenance stats API response:', data);

      return data;
    } catch (error) {
      console.error('🔧 Error fetching maintenance stats:', error);
      throw error;
    }
  }

  private async getMaintenanceAlert(input: any): Promise<MaintenanceAlert> {
    const url = `${this.config.endpoint}/api/v1/maintenance-alerts/${input.alertId}`;

    console.log(`🔧 Fetching maintenance alert from: ${url}`);

    try {
      const response = await fetch(url, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();
      console.log('🔧 Maintenance alert API response:', data);

      return data;
    } catch (error) {
      console.error('🔧 Error fetching maintenance alert:', error);
      throw error;
    }
  }
}
