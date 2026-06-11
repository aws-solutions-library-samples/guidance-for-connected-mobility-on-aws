// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useCallback } from 'react';
import { useApiClient } from '../api/AuthenticatedApiProvider';
import { DeviceManagementClient, DeviceConnection, DeviceSubscription, DeviceTopic, DeviceUser, DevicePolicy, DeviceAlarm, MetricStatistic, ListRequest } from '../api/device-management-client';

export const useDeviceManagementClient = () => {
  const apiClient = useApiClient();
  return new DeviceManagementClient(apiClient);
};

export const useDeviceConnections = (request: ListRequest = {}) => {
  const [connections, setConnections] = useState<DeviceConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const client = useDeviceManagementClient();

  const fetchConnections = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await client.listConnections(request);
      setConnections(response.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch connections');
    } finally {
      setLoading(false);
    }
  }, [client, request]);

  useEffect(() => {
    fetchConnections();
  }, [fetchConnections]);

  return { connections, loading, error, refetch: fetchConnections };
};

export const useDeviceSubscriptions = (request: ListRequest = {}) => {
  const [subscriptions, setSubscriptions] = useState<DeviceSubscription[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const client = useDeviceManagementClient();

  const fetchSubscriptions = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await client.listSubscriptions(request);
      setSubscriptions(response.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch subscriptions');
    } finally {
      setLoading(false);
    }
  }, [client, request]);

  useEffect(() => {
    fetchSubscriptions();
  }, [fetchSubscriptions]);

  return { subscriptions, loading, error, refetch: fetchSubscriptions };
};

export const useDeviceTopics = (request: ListRequest = {}) => {
  const [topics, setTopics] = useState<DeviceTopic[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const client = useDeviceManagementClient();

  const fetchTopics = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await client.listTopics(request);
      setTopics(response.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch topics');
    } finally {
      setLoading(false);
    }
  }, [client, request]);

  useEffect(() => {
    fetchTopics();
  }, [fetchTopics]);

  return { topics, loading, error, refetch: fetchTopics };
};

export const useDeviceUsers = (request: ListRequest = {}) => {
  const [users, setUsers] = useState<DeviceUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const client = useDeviceManagementClient();

  const fetchUsers = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await client.listUsers(request);
      setUsers(response.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch users');
    } finally {
      setLoading(false);
    }
  }, [client, request]);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  const createUser = useCallback(async (userData: Partial<DeviceUser>) => {
    try {
      await client.createUser(userData);
      await fetchUsers();
    } catch (err) {
      throw new Error(err instanceof Error ? err.message : 'Failed to create user');
    }
  }, [client, fetchUsers]);

  const updateUser = useCallback(async (userUid: string, userData: Partial<DeviceUser>) => {
    try {
      await client.updateUser(userUid, userData);
      await fetchUsers();
    } catch (err) {
      throw new Error(err instanceof Error ? err.message : 'Failed to update user');
    }
  }, [client, fetchUsers]);

  const deleteUser = useCallback(async (userUid: string) => {
    try {
      await client.deleteUser(userUid);
      await fetchUsers();
    } catch (err) {
      throw new Error(err instanceof Error ? err.message : 'Failed to delete user');
    }
  }, [client, fetchUsers]);

  return { users, loading, error, refetch: fetchUsers, createUser, updateUser, deleteUser };
};

export const useDevicePolicies = (request: ListRequest = {}) => {
  const [policies, setPolicies] = useState<DevicePolicy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const client = useDeviceManagementClient();

  const fetchPolicies = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await client.listPolicies(request);
      setPolicies(response.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch policies');
    } finally {
      setLoading(false);
    }
  }, [client, request]);

  useEffect(() => {
    fetchPolicies();
  }, [fetchPolicies]);

  const createPolicy = useCallback(async (policyData: Partial<DevicePolicy>) => {
    try {
      await client.createPolicy(policyData);
      await fetchPolicies();
    } catch (err) {
      throw new Error(err instanceof Error ? err.message : 'Failed to create policy');
    }
  }, [client, fetchPolicies]);

  const updatePolicy = useCallback(async (policyUid: string, policyData: Partial<DevicePolicy>) => {
    try {
      await client.updatePolicy(policyUid, policyData);
      await fetchPolicies();
    } catch (err) {
      throw new Error(err instanceof Error ? err.message : 'Failed to update policy');
    }
  }, [client, fetchPolicies]);

  const deletePolicy = useCallback(async (policyUid: string) => {
    try {
      await client.deletePolicy(policyUid);
      await fetchPolicies();
    } catch (err) {
      throw new Error(err instanceof Error ? err.message : 'Failed to delete policy');
    }
  }, [client, fetchPolicies]);

  return { policies, loading, error, refetch: fetchPolicies, createPolicy, updatePolicy, deletePolicy };
};

export const useDeviceAlarms = (request: ListRequest = {}) => {
  const [alarms, setAlarms] = useState<DeviceAlarm[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const client = useDeviceManagementClient();

  const fetchAlarms = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await client.listAlarms(request);
      setAlarms(response.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch alarms');
    } finally {
      setLoading(false);
    }
  }, [client, request]);

  useEffect(() => {
    fetchAlarms();
  }, [fetchAlarms]);

  return { alarms, loading, error, refetch: fetchAlarms };
};

export const useDeviceMetrics = () => {
  const [statistics, setStatistics] = useState<MetricStatistic[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const client = useDeviceManagementClient();

  const fetchStatistics = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const stats = await client.getMetricsStatistics();
      setStatistics(stats);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch metrics');
    } finally {
      setLoading(false);
    }
  }, [client]);

  useEffect(() => {
    fetchStatistics();
  }, [fetchStatistics]);

  const getMetricsData = useCallback(async (params?: any) => {
    return client.getMetricsData(params);
  }, [client]);

  return { statistics, loading, error, refetch: fetchStatistics, getMetricsData };
};
