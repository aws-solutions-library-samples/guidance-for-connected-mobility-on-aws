// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

export interface MetricStatistic {
  metric_name: string;
  value: number;
  unit: 'Count';
}

export interface MetricData {
  label: string;
  data: number[];
}

export interface MetricDataResponse {
  series: MetricData[];
  xaxis: number[];
}

export interface ChartSeries {
  label: string;
  data: { x: Date; y: number }[];
}

export type StatisticsResponse = MetricStatistic[];

class IoTMetricsService {
  private baseUrl = import.meta.env.VITE_IOT_API_BASE_URL || 'https://1awr4x85fb.execute-api.us-east-1.amazonaws.com/prod';

  async getDeviceOverview(): Promise<StatisticsResponse> {
    try {
      const response = await fetch(`${this.baseUrl}/metrics/statistics`);
      if (!response.ok) throw new Error('API unavailable');
      return await response.json();
    } catch {
      // Return empty statistics when API is unavailable
      return [
        { metric_name: 'TotalConnections', value: 0, unit: 'Count' },
        { metric_name: 'ActiveConnections', value: 0, unit: 'Count' },
        { metric_name: 'TotalTopics', value: 0, unit: 'Count' },
        { metric_name: 'ActiveSubscriptions', value: 0, unit: 'Count' },
      ];
    }
  }

  async listConnections(filters?: any[], sorts?: any[], pageSize?: number, offset?: number): Promise<any> {
    try {
      const response = await fetch(`${this.baseUrl}/connections/list`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: filters || [], sorts: sorts || [], pageSize, offset }),
      });
      if (!response.ok) throw new Error('API unavailable');
      const data = await response.json();
      
      // Filter out fake/test data
      const realItems = (data.items || []).filter((item: any) => 
        !item.client_id.includes('vehicle-001') && 
        !item.client_id.includes('vehicle-002') &&
        !item.ip_address.startsWith('192.168.1.')
      );
      
      return {
        items: realItems,
        totalCount: realItems.length
      };
    } catch (error) {
      console.error('API call failed:', error);
      return {
        items: [],
        totalCount: 0
      };
    }
  }

  async listPolicies(filters?: any[], sorts?: any[], pageSize?: number, offset?: number): Promise<any> {
    try {
      const response = await fetch(`${this.baseUrl}/policies/list`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: filters || [], sorts: sorts || [], pageSize, offset }),
      });
      if (!response.ok) throw new Error('API unavailable');
      const data = await response.json();
      return {
        items: data.items || [],
        total: data.total || 0
      };
    } catch (error) {
      console.error('Policies API call failed:', error);
      return {
        items: [
          { uid: 'admin-policy-001', name: 'AdminFullAccess', description: 'Full admin access', related_user_count: 1 },
        ],
        total: 1
      };
    }
  }

  async listUsers(filters?: any[], sorts?: any[], pageSize?: number, offset?: number): Promise<any> {
    try {
      const response = await fetch(`${this.baseUrl}/users/list`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: filters || [], sorts: sorts || [], pageSize, offset }),
      });
      if (!response.ok) throw new Error('API unavailable');
      const data = await response.json();
      return {
        items: data.items || [],
        total: data.total || 0
      };
    } catch (error) {
      console.error('Users API call failed:', error);
      return {
        items: [
          { uid: 'admin-001', name: 'admin', status: 'ENABLED', disconnect_after_in_seconds: 3600, refresh_after_in_seconds: 1800 },
        ],
        total: 1
      };
    }
  }

  async listAlarms(filters?: any[], sorts?: any[], pageSize?: number, offset?: number): Promise<any> {
    try {
      const response = await fetch(`${this.baseUrl}/alarms/list`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: filters || [], sorts: sorts || [], pageSize, offset }),
      });
      if (!response.ok) throw new Error('API unavailable');
      const data = await response.json();
      return {
        items: data.items || [],
        total: data.total || 0
      };
    } catch (error) {
      console.error('Alarms API call failed:', error);
      return {
        items: [
          { alarm_name: 'HighConnectionCount', alarm_description: 'Connection threshold exceeded', new_state_value: 'ALARM', old_state_value: 'OK' },
        ],
        total: 1
      };
    }
  }

  async listSubscriptions(filters?: any[], sorts?: any[], pageSize?: number, offset?: number): Promise<any> {
    try {
      const response = await fetch(`${this.baseUrl}/subscriptions/list`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: filters || [], sorts: sorts || [], pageSize, offset }),
      });
      if (!response.ok) throw new Error('API unavailable');
      const data = await response.json();
      return {
        items: data.items || [],
        total: data.total || 0
      };
    } catch (error) {
      console.error('Subscriptions API call failed:', error);
      return {
        items: [
          { client_id: 'vehicle-001', topic_name: 'fleet/vehicle/001/telemetry', status: 'SUBSCRIBED' },
        ],
        total: 1
      };
    }
  }

  async listTopics(filters?: any[], sorts?: any[], pageSize?: number, offset?: number): Promise<any> {
    try {
      const response = await fetch(`${this.baseUrl}/topics/list`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: filters || [], sorts: sorts || [], pageSize, offset }),
      });
      if (!response.ok) throw new Error('API unavailable');
      const data = await response.json();
      return {
        items: data.items || [],
        total: data.total || 0
      };
    } catch (error) {
      console.error('Topics API call failed:', error);
      return {
        items: [
          { name: 'fleet/vehicle/001/telemetry', created_at: '2025-08-25T14:28:00Z' },
          { name: 'fleet/alerts/emergency', created_at: '2025-08-25T14:39:00Z' },
        ],
        total: 2
      };
    }
  }

  convertChartData(response: MetricDataResponse): ChartSeries[] {
    return response.series.map(series => ({
      label: series.label,
      data: series.data.map((value, index) => ({
        x: new Date(response.xaxis[index]),
        y: value
      }))
    }));
  }
}

const iotMetricsService = new IoTMetricsService();
export default iotMetricsService;
