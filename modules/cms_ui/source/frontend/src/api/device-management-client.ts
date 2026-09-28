// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { AuthenticatedApiClient } from '../auth/AuthenticatedApiClient';

export interface DeviceConnection {
  client_id: string;
  status: 'CONNECTED' | 'DISCONNECTED';
  connected_at?: string;
  disconnected_at?: string;
  ip_address?: string;
  protocol?: string;
  keep_alive?: number;
}

export interface DeviceSubscription {
  client_id: string;
  topic_filter: string;
  status: 'SUBSCRIBED' | 'UNSUBSCRIBED';
  subscribed_at?: string;
  qos?: number;
}

export interface DeviceTopic {
  topic_name: string;
  message_count?: number;
  last_message_at?: string;
  subscribers?: number;
}

export interface DeviceUser {
  user_uid: string;
  username: string;
  status: 'ACTIVE' | 'INACTIVE';
  created_at?: string;
  last_login?: string;
}

export interface DevicePolicy {
  policy_uid: string;
  policy_name: string;
  policy_document: any;
  created_at?: string;
  updated_at?: string;
}

export interface DeviceAlarm {
  alarm_name: string;
  alarm_description?: string;
  state_value: 'OK' | 'ALARM' | 'INSUFFICIENT_DATA';
  state_reason?: string;
  state_updated_timestamp?: string;
  metric_name?: string;
}

export interface MetricStatistic {
  metric_name: string;
  value: number;
  unit: string;
}

export interface MetricDataPoint {
  timestamp: number;
  value: number;
}

export interface MetricSeries {
  label: string;
  data: number[];
}

export interface MetricDataResponse {
  series: MetricSeries[];
  xaxis: number[];
}

export interface ListResponse<T> {
  items: T[];
  total: number;
}

export interface FilterSpec {
  field: string;
  operator: 'eq' | 'contains' | 'gt' | 'lt';
  value: any;
}

export interface ListRequest {
  filters?: FilterSpec[];
  limit?: number;
  offset?: number;
}

export class DeviceManagementClient {
  constructor(private apiClient: AuthenticatedApiClient) {}

  // Health and metrics
  async getHealth(): Promise<{ status: string; timestamp: string }> {
    return this.apiClient.get('/iot-api/health');
  }

  async getMetricsStatistics(): Promise<MetricStatistic[]> {
    return this.apiClient.get('/iot-api/metrics/statistics');
  }

  async getMetricsData(params?: any): Promise<MetricDataResponse> {
    return this.apiClient.get('/iot-api/metrics/data', params);
  }

  // Connections
  async listConnections(request: ListRequest = {}): Promise<ListResponse<DeviceConnection>> {
    return this.apiClient.post('/iot-api/connections/list', request);
  }

  async getConnection(clientId: string): Promise<DeviceConnection> {
    return this.apiClient.get(`/iot-api/connections/${clientId}`);
  }

  async startConnectionMetrics(clientId: string): Promise<{ query_execution_id: string }> {
    return this.apiClient.post(`/iot-api/connections/${clientId}/metrics`);
  }

  async getConnectionMetrics(clientId: string, executionId: string): Promise<any> {
    return this.apiClient.get(`/iot-api/connections/${clientId}/metrics/${executionId}`);
  }

  // Subscriptions
  async listSubscriptions(request: ListRequest = {}): Promise<ListResponse<DeviceSubscription>> {
    return this.apiClient.post('/iot-api/subscriptions/list', request);
  }

  // Topics
  async listTopics(request: ListRequest = {}): Promise<ListResponse<DeviceTopic>> {
    return this.apiClient.post('/iot-api/topics/list', request);
  }

  async startTopicMetrics(): Promise<{ query_execution_id: string }> {
    return this.apiClient.post('/iot-api/topics/metrics');
  }

  async getTopicMetrics(executionId: string): Promise<any> {
    return this.apiClient.get(`/iot-api/topics/metrics/${executionId}`);
  }

  // Users
  async listUsers(request: ListRequest = {}): Promise<ListResponse<DeviceUser>> {
    return this.apiClient.post('/iot-api/users/list', request);
  }

  async getUser(userUid: string): Promise<DeviceUser> {
    return this.apiClient.post(`/iot-api/users/${userUid}`);
  }

  async createUser(userData: Partial<DeviceUser>): Promise<DeviceUser> {
    return this.apiClient.post('/iot-api/users', userData);
  }

  async updateUser(userUid: string, userData: Partial<DeviceUser>): Promise<DeviceUser> {
    return this.apiClient.put(`/iot-api/users/${userUid}`, userData);
  }

  async deleteUser(userUid: string): Promise<void> {
    return this.apiClient.delete(`/iot-api/users/${userUid}`);
  }

  // Policies
  async listPolicies(request: ListRequest = {}): Promise<ListResponse<DevicePolicy>> {
    return this.apiClient.post('/iot-api/policies/list', request);
  }

  async getPolicy(policyUid: string): Promise<DevicePolicy> {
    return this.apiClient.post(`/iot-api/policies/${policyUid}`);
  }

  async createPolicy(policyData: Partial<DevicePolicy>): Promise<DevicePolicy> {
    return this.apiClient.post('/iot-api/policies', policyData);
  }

  async updatePolicy(policyUid: string, policyData: Partial<DevicePolicy>): Promise<DevicePolicy> {
    return this.apiClient.put(`/iot-api/policies/${policyUid}`, policyData);
  }

  async deletePolicy(policyUid: string): Promise<void> {
    return this.apiClient.delete(`/iot-api/policies/${policyUid}`);
  }

  // User-Policy relationships
  async listUserPolicies(userUid: string, request: ListRequest = {}): Promise<ListResponse<DevicePolicy>> {
    return this.apiClient.post(`/iot-api/users/${userUid}/policies/list`, request);
  }

  async createUserPolicyRelation(userUid: string, policyUid: string): Promise<void> {
    return this.apiClient.post(`/iot-api/users/${userUid}/policies/${policyUid}`);
  }

  async deleteUserPolicyRelation(userUid: string, policyUid: string): Promise<void> {
    return this.apiClient.delete(`/iot-api/users/${userUid}/policies/${policyUid}`);
  }

  async listUsersByPolicy(policyUid: string, request: ListRequest = {}): Promise<ListResponse<DeviceUser>> {
    return this.apiClient.post(`/iot-api/policies/${policyUid}/users/list`, request);
  }

  // Alarms
  async listAlarms(request: ListRequest = {}): Promise<ListResponse<DeviceAlarm>> {
    return this.apiClient.post('/iot-api/alarms/list', request);
  }

  // Log events
  async filterLogEvents(request: any): Promise<{ events: any[]; nextToken?: string }> {
    return this.apiClient.post('/iot-api/filter-log-events', request);
  }
}
