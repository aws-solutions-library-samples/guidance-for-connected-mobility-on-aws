/**
 * React Hook for Real-time Data
 * Provides easy integration of real-time fleet data in React components
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  RealtimeDataService,
  RealtimeApiClient,
  VehicleUpdate,
  FleetUpdate,
  AlertData,
  DashboardData,
  SubscriptionType,
  getRealtimeService,
  getRealtimeApiClient
} from '../services/RealtimeDataService';

export interface UseRealtimeDataOptions {
  autoConnect?: boolean;
  reconnectOnError?: boolean;
  pollingInterval?: number; // Fallback polling interval in ms
}

/**
 * Hook for managing real-time WebSocket connection
 */
export function useRealtimeConnection(
  apiEndpoint: string,
  options: UseRealtimeDataOptions = {}
) {
  const [isConnected, setIsConnected] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const serviceRef = useRef<RealtimeDataService | null>(null);

  const { autoConnect = true, reconnectOnError = true } = options;

  useEffect(() => {
    if (!apiEndpoint) return;

    serviceRef.current = getRealtimeService(apiEndpoint);
    const service = serviceRef.current;

    const handleConnected = () => {
      setIsConnected(true);
      setIsConnecting(false);
      setError(null);
    };

    const handleDisconnected = () => {
      setIsConnected(false);
      setIsConnecting(false);
    };

    const handleError = (err: Error) => {
      setError(err);
      setIsConnecting(false);
      if (!reconnectOnError) {
        setIsConnected(false);
      }
    };

    service.on('connected', handleConnected);
    service.on('disconnected', handleDisconnected);
    service.on('error', handleError);

    if (autoConnect) {
      setIsConnecting(true);
      service.connect().catch(handleError);
    }

    return () => {
      service.off('connected', handleConnected);
      service.off('disconnected', handleDisconnected);
      service.off('error', handleError);
    };
  }, [apiEndpoint, autoConnect, reconnectOnError]);

  const connect = useCallback(async () => {
    if (!serviceRef.current) return;
    
    setIsConnecting(true);
    setError(null);
    
    try {
      await serviceRef.current.connect();
    } catch (err) {
      setError(err as Error);
      setIsConnecting(false);
    }
  }, []);

  const disconnect = useCallback(() => {
    if (!serviceRef.current) return;
    serviceRef.current.disconnect();
  }, []);

  return {
    isConnected,
    isConnecting,
    error,
    connect,
    disconnect,
    service: serviceRef.current
  };
}

/**
 * Hook for subscribing to real-time vehicle updates
 */
export function useRealtimeVehicles(
  apiEndpoint: string,
  filters?: { status?: string; fleet_id?: string; limit?: number },
  options: UseRealtimeDataOptions = {}
) {
  const [vehicles, setVehicles] = useState<VehicleUpdate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [lastUpdate, setLastUpdate] = useState<string | null>(null);

  const { service, isConnected } = useRealtimeConnection(apiEndpoint, options);
  const apiClient = getRealtimeApiClient(apiEndpoint);
  const pollingIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // Fetch initial data
  const fetchVehicles = useCallback(async () => {
    try {
      setLoading(true);
      const response = await apiClient.getLiveVehicles(filters);
      setVehicles(response.vehicles);
      setLastUpdate(response.timestamp);
      setError(null);
    } catch (err) {
      setError(err as Error);
    } finally {
      setLoading(false);
    }
  }, [apiClient, filters]);

  // Handle real-time updates
  useEffect(() => {
    if (!service || !isConnected) return;

    const handleVehicleUpdate = (update: VehicleUpdate) => {
      setVehicles(prev => {
        const index = prev.findIndex(v => v.vin === update.vin);
        if (index >= 0) {
          const updated = [...prev];
          updated[index] = { ...updated[index], ...update };
          return updated;
        } else {
          // Check if vehicle matches filters
          if (filters?.status && update.status !== filters.status) return prev;
          if (filters?.fleet_id && update.fleet_id !== filters.fleet_id) return prev;
          return [...prev, update];
        }
      });
      setLastUpdate(new Date().toISOString());
    };

    service.on('vehicleUpdate', handleVehicleUpdate);

    // Subscribe to updates
    if (filters?.fleet_id) {
      service.subscribe('fleet', filters.fleet_id);
    } else {
      service.subscribe('dashboard');
    }

    return () => {
      service.off('vehicleUpdate', handleVehicleUpdate);
      if (filters?.fleet_id) {
        service.unsubscribe('fleet', filters.fleet_id);
      } else {
        service.unsubscribe('dashboard');
      }
    };
  }, [service, isConnected, filters]);

  // Fallback polling when WebSocket is not available
  useEffect(() => {
    if (isConnected || !options.pollingInterval) return;

    pollingIntervalRef.current = setInterval(fetchVehicles, options.pollingInterval);

    return () => {
      if (pollingIntervalRef.current) {
        clearInterval(pollingIntervalRef.current);
      }
    };
  }, [isConnected, options.pollingInterval, fetchVehicles]);

  // Initial fetch
  useEffect(() => {
    fetchVehicles();
  }, [fetchVehicles]);

  return {
    vehicles,
    loading,
    error,
    lastUpdate,
    refresh: fetchVehicles,
    isRealtime: isConnected
  };
}

/**
 * Hook for subscribing to real-time fleet updates
 */
export function useRealtimeFleet(
  apiEndpoint: string,
  fleetId: string,
  options: UseRealtimeDataOptions = {}
) {
  const [fleetData, setFleetData] = useState<FleetUpdate | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [lastUpdate, setLastUpdate] = useState<string | null>(null);

  const { service, isConnected } = useRealtimeConnection(apiEndpoint, options);
  const apiClient = getRealtimeApiClient(apiEndpoint);

  // Fetch initial data
  const fetchFleetData = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiClient.getFleetLiveStatus(fleetId);
      setFleetData(data);
      setLastUpdate(new Date().toISOString());
      setError(null);
    } catch (err) {
      setError(err as Error);
    } finally {
      setLoading(false);
    }
  }, [apiClient, fleetId]);

  // Handle real-time updates
  useEffect(() => {
    if (!service || !isConnected || !fleetId) return;

    const handleFleetUpdate = (update: FleetUpdate) => {
      if (update.fleet_id === fleetId) {
        setFleetData(prev => ({ ...prev, ...update }));
        setLastUpdate(new Date().toISOString());
      }
    };

    service.on('fleetUpdate', handleFleetUpdate);
    service.subscribe('fleet', fleetId);

    return () => {
      service.off('fleetUpdate', handleFleetUpdate);
      service.unsubscribe('fleet', fleetId);
    };
  }, [service, isConnected, fleetId]);

  // Initial fetch
  useEffect(() => {
    if (fleetId) {
      fetchFleetData();
    }
  }, [fetchFleetData, fleetId]);

  return {
    fleetData,
    loading,
    error,
    lastUpdate,
    refresh: fetchFleetData,
    isRealtime: isConnected
  };
}

/**
 * Hook for subscribing to real-time alerts
 */
export function useRealtimeAlerts(
  apiEndpoint: string,
  options: UseRealtimeDataOptions = {}
) {
  const [alerts, setAlerts] = useState<AlertData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [lastUpdate, setLastUpdate] = useState<string | null>(null);

  const { service, isConnected } = useRealtimeConnection(apiEndpoint, options);
  const apiClient = getRealtimeApiClient(apiEndpoint);

  // Fetch initial data
  const fetchAlerts = useCallback(async () => {
    try {
      setLoading(true);
      const response = await apiClient.getActiveAlerts();
      setAlerts(response.alerts);
      setLastUpdate(response.timestamp);
      setError(null);
    } catch (err) {
      setError(err as Error);
    } finally {
      setLoading(false);
    }
  }, [apiClient]);

  // Handle real-time updates
  useEffect(() => {
    if (!service || !isConnected) return;

    const handleAlert = (alert: AlertData) => {
      setAlerts(prev => {
        // Add new alert to the beginning
        const updated = [alert, ...prev];
        // Keep only the most recent 50 alerts
        return updated.slice(0, 50);
      });
      setLastUpdate(new Date().toISOString());
    };

    service.on('alert', handleAlert);
    service.subscribe('alerts');

    return () => {
      service.off('alert', handleAlert);
      service.unsubscribe('alerts');
    };
  }, [service, isConnected]);

  // Initial fetch
  useEffect(() => {
    fetchAlerts();
  }, [fetchAlerts]);

  const dismissAlert = useCallback((alertId: string) => {
    setAlerts(prev => prev.filter(alert => alert.alert_id !== alertId));
  }, []);

  return {
    alerts,
    loading,
    error,
    lastUpdate,
    refresh: fetchAlerts,
    dismissAlert,
    isRealtime: isConnected
  };
}

/**
 * Hook for dashboard real-time data
 */
export function useRealtimeDashboard(
  apiEndpoint: string,
  options: UseRealtimeDataOptions = {}
) {
  const [dashboardData, setDashboardData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [lastUpdate, setLastUpdate] = useState<string | null>(null);

  const { service, isConnected } = useRealtimeConnection(apiEndpoint, options);
  const apiClient = getRealtimeApiClient(apiEndpoint);

  // Fetch initial data
  const fetchDashboardData = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiClient.getDashboardData();
      setDashboardData(data);
      setLastUpdate(new Date().toISOString());
      setError(null);
    } catch (err) {
      setError(err as Error);
    } finally {
      setLoading(false);
    }
  }, [apiClient]);

  // Handle real-time updates
  useEffect(() => {
    if (!service || !isConnected) return;

    const handleDashboardUpdate = (update: DashboardData) => {
      setDashboardData(update);
      setLastUpdate(new Date().toISOString());
    };

    const handleVehicleUpdate = () => {
      // Refresh dashboard data when vehicles update
      fetchDashboardData();
    };

    const handleAlert = () => {
      // Refresh dashboard data when new alerts arrive
      fetchDashboardData();
    };

    service.on('dashboardUpdate', handleDashboardUpdate);
    service.on('vehicleUpdate', handleVehicleUpdate);
    service.on('alert', handleAlert);
    service.subscribe('dashboard');

    return () => {
      service.off('dashboardUpdate', handleDashboardUpdate);
      service.off('vehicleUpdate', handleVehicleUpdate);
      service.off('alert', handleAlert);
      service.unsubscribe('dashboard');
    };
  }, [service, isConnected, fetchDashboardData]);

  // Initial fetch
  useEffect(() => {
    fetchDashboardData();
  }, [fetchDashboardData]);

  return {
    dashboardData,
    loading,
    error,
    lastUpdate,
    refresh: fetchDashboardData,
    isRealtime: isConnected
  };
}

/**
 * Hook for individual vehicle real-time data
 */
export function useRealtimeVehicle(
  apiEndpoint: string,
  vin: string,
  options: UseRealtimeDataOptions = {}
) {
  const [vehicleData, setVehicleData] = useState<VehicleUpdate | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [lastUpdate, setLastUpdate] = useState<string | null>(null);

  const { service, isConnected } = useRealtimeConnection(apiEndpoint, options);
  const apiClient = getRealtimeApiClient(apiEndpoint);

  // Fetch initial data
  const fetchVehicleData = useCallback(async () => {
    if (!vin) return;
    
    try {
      setLoading(true);
      const response = await apiClient.getVehicleLiveData(vin);
      setVehicleData(response.vehicle);
      setLastUpdate(response.timestamp);
      setError(null);
    } catch (err) {
      setError(err as Error);
    } finally {
      setLoading(false);
    }
  }, [apiClient, vin]);

  // Handle real-time updates
  useEffect(() => {
    if (!service || !isConnected || !vin) return;

    const handleVehicleUpdate = (update: VehicleUpdate) => {
      if (update.vin === vin) {
        setVehicleData(prev => ({ ...prev, ...update }));
        setLastUpdate(new Date().toISOString());
      }
    };

    service.on('vehicleUpdate', handleVehicleUpdate);
    service.subscribe('vehicle', vin);

    return () => {
      service.off('vehicleUpdate', handleVehicleUpdate);
      service.unsubscribe('vehicle', vin);
    };
  }, [service, isConnected, vin]);

  // Initial fetch
  useEffect(() => {
    if (vin) {
      fetchVehicleData();
    }
  }, [fetchVehicleData, vin]);

  return {
    vehicleData,
    loading,
    error,
    lastUpdate,
    refresh: fetchVehicleData,
    isRealtime: isConnected
  };
}
