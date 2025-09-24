// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useContext } from 'react';
import { getRuntimeConfig } from '../../config/api';
import {
  Container,
  Header,
  SpaceBetween,
  Grid,
  Box,
  ColumnLayout,
  KeyValuePairs,
  Tabs,
  Alert,
  Button,
  Select,
  LineChart
} from '@cloudscape-design/components';
import SafetyEventsTable from './SafetyEventsTable';
import { AlertsFleetFilter, useAlertsFleetFilter } from '../commons/AlertsFleetFilter';
import { ApiContext } from '@/api/provider';

// Frontend interface for safety events
interface SafetyEvent {
  id: string;
  vehicleId: string;
  vin: string;
  actualVin?: string;
  driverId?: string;
  driverName?: string;
  eventType: 'hard_braking' | 'lane_departure' | 'rapid_acceleration' | 'speeding' | 'driver_score_decline' | 'hard_acceleration' | 'drowsiness' | 'no_seatbelt' | 'hard_cornering';
  severity: 'low' | 'medium' | 'high' | 'critical';
  timestamp: string;
  location: {
    lat: number;
    lon: number;
  };
  driverScore?: number;
  fleetName: string;
  details?: any;
  resolved?: boolean;
}

interface PaginationInfo {
  total: number;
  page: number;
  limit: number;
  totalPages: number;
  hasNextPage: boolean;
  hasPrevPage: boolean;
  returned: number;
}

export function SafetyAlertsContent() {
  const [safetyEvents, setSafetyEvents] = useState<SafetyEvent[]>([]);
  const [pagination, setPagination] = useState<PaginationInfo>({
    total: 0,
    page: 1,
    limit: 20,
    totalPages: 0,
    hasNextPage: false,
    hasPrevPage: false,
    returned: 0
  });
  const [loading, setLoading] = useState(true);
  const [selectedTimeRange, setSelectedTimeRange] = useState('7d');
  const [currentFilters, setCurrentFilters] = useState<{ fleetId?: string; eventType?: string; timeRange?: string }>({ timeRange: '7d' });
  const [activeTab, setActiveTab] = useState('overview');
  const [refreshInProgress, setRefreshInProgress] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Use the API context to get the endpoint
  const apiContext = useContext(ApiContext);

  // Fetch safety events with pagination and filters
  const fetchSafetyEvents = async (page: number = 1, limit: number = 20, filters?: { fleetId?: string; eventType?: string; timeRange?: string }) => {
    try {
      setLoading(true);
      setError(null);
      
      const filtersToUse = filters || currentFilters;
      console.log(`🚨 Fetching safety events page ${page} with limit ${limit} and filters:`, filtersToUse);
      
      const runtimeConfig = getRuntimeConfig();
      const apiEndpoint = apiContext?.endpoint || runtimeConfig.apiEndpoint;
      
      const params = new URLSearchParams({
        page: page.toString(),
        limit: limit.toString(),
        timeRange: filtersToUse.timeRange || '7d'
      });
      
      if (filtersToUse.fleetId) {
        params.append('fleetId', filtersToUse.fleetId);
      }
      
      if (filtersToUse.eventType) {
        params.append('eventType', filtersToUse.eventType);
      }
      
      const url = `${apiEndpoint}/api/v1/safety-alerts?${params.toString()}`;
      console.log(`🚨 Calling safety events endpoint: ${url}`);
      
      const response = await fetch(url);
      
      if (!response.ok) {
        throw new Error(`API returned ${response.status}`);
      }
      
      const data = await response.json();
      console.log('🚨 Safety events API response:', data);
      
      // Transform API events to match our interface
      const apiAlerts = data.alerts || [];
      const apiPagination = {
        total: data.total || 0,
        page: data.page || 1,
        limit: data.limit || 20,
        totalPages: data.totalPages || 0,
        hasNextPage: data.hasNextPage || false,
        hasPrevPage: data.hasPrevPage || false,
        returned: data.returned || 0
      };

      const transformedEvents: SafetyEvent[] = apiAlerts.map((alert: any) => ({
        id: alert.eventId || alert.id,
        vehicleId: alert.vehicleId,
        vin: alert.actualVin || alert.vin || alert.vehicleId,
        actualVin: alert.actualVin,
        driverId: alert.driverId,
        driverName: alert.driverName || alert.driverId,
        eventType: alert.eventType?.toLowerCase().replace('_', '_') || 'safety_event',
        severity: alert.severity?.toLowerCase() || 'medium',
        timestamp: alert.timestamp,
        location: {
          lat: alert.latitude || alert.location?.lat || 0,
          lon: alert.longitude || alert.location?.lon || 0
        },
        fleetName: getFleetName(alert.fleetId),
        details: alert.details,
        resolved: alert.resolved || false
      }));

      setSafetyEvents(transformedEvents);
      
      // Add returned count based on actual events received
      const paginationWithReturned = {
        ...apiPagination,
        returned: transformedEvents.length
      };
      
      setPagination(paginationWithReturned);
      
    } catch (error) {
      console.error('❌ Error fetching safety events:', error);
      setError('Failed to load safety events from API');
      setSafetyEvents([]);
      setPagination({
        total: 0,
        page: 1,
        limit: 20,
        totalPages: 0,
        hasNextPage: false,
        hasPrevPage: false,
        returned: 0
      });
    } finally {
      setLoading(false);
    }
  };

  // Handle filter changes from SafetyEventsTable
  const handleFilterChange = (filters: { fleetId?: string; eventType?: string; timeRange?: string }) => {
    console.log('🚨 Filter change received:', filters);
    setCurrentFilters(filters);
    fetchSafetyEvents(1, pagination.limit, filters);
  };

  // Refresh data function
  const refreshData = async () => {
    setRefreshInProgress(true);
    await fetchSafetyEvents(pagination.page, pagination.limit, currentFilters);
    setRefreshInProgress(false);
  };

  // Fleet filter hook
  const {
    selectedFleet,
    selectedFleetName,
    handleFleetChange,
    isAllFleets
  } = useAlertsFleetFilter();

  // Single useEffect to handle all filter changes and initial load
  useEffect(() => {
    const filters = { ...currentFilters };
    
    // Update fleet filter
    if (!isAllFleets && selectedFleet && selectedFleet !== 'all') {
      filters.fleetId = selectedFleet;
    } else {
      filters.fleetId = undefined;
    }
    
    // Only fetch if filters actually changed
    const filtersChanged = JSON.stringify(filters) !== JSON.stringify(currentFilters);
    
    if (filtersChanged) {
      setCurrentFilters(filters);
    }
    
    // Always fetch on mount or when filters change
    fetchSafetyEvents(1, pagination.limit, filters);
  }, [selectedFleet, isAllFleets]); // Only depend on fleet changes

  // Time range options
  const timeRangeOptions = [
    { label: 'Last hour', value: '1h' },
    { label: 'Last 6 hours', value: '6h' },
    { label: 'Last 24 hours', value: '24h' },
    { label: 'Last 7 days', value: '7d' },
    { label: 'Last 30 days', value: '30d' },
  ];

  // Helper functions
  const getFleetName = (fleetId: string): string => {
    const fleetMap: Record<string, string> = {
      'FLEET-001': 'Fleet A',
      'FLEET-002': 'Fleet B', 
      'FLEET-003': 'Fleet C',
      'FLEET-004': 'Fleet D',
      'FLEET-005': 'Fleet E',
    };
    return fleetMap[fleetId] || 'Unknown Fleet';
  };

  // Calculate current metrics
  const currentMetrics = {
    totalEvents: pagination.total, // Use total from API, not current page
    highSeverityEvents: safetyEvents.filter(e => e.severity === 'high' || e.severity === 'critical').length,
    mediumSeverityEvents: safetyEvents.filter(e => e.severity === 'medium').length,
    lowSeverityEvents: safetyEvents.filter(e => e.severity === 'low').length,
    resolvedEvents: safetyEvents.filter(e => e.resolved).length,
    pendingEvents: safetyEvents.filter(e => !e.resolved).length,
    laneDepartureEvents: safetyEvents.filter(e => e.eventType.includes('lane') || e.eventType.includes('LANE')).length,
    speedingEvents: safetyEvents.filter(e => e.eventType.includes('speeding') || e.eventType.includes('SPEEDING')).length,
    hardBrakingEvents: safetyEvents.filter(e => e.eventType.includes('braking') || e.eventType.includes('BRAKING')).length,
    drowsinessEvents: safetyEvents.filter(e => e.eventType.includes('drowsiness') || e.eventType.includes('DROWSINESS')).length,
  };

  // Generate 30-day safety trends data by fleet
  const generateSafetyTrends = () => {
    const trends = [];
    const now = new Date();
    
    for (let i = 29; i >= 0; i--) {
      const date = new Date(now.getTime() - i * 24 * 60 * 60 * 1000);
      
      // Generate realistic daily safety event counts by fleet
      const fleetA = Math.floor(Math.random() * 5) + 2; // 2-6 per day
      const fleetB = Math.floor(Math.random() * 4) + 1; // 1-4 per day  
      const fleetC = Math.floor(Math.random() * 3) + 1; // 1-3 per day
      
      // Weekend reduction
      const dayOfWeek = date.getDay();
      const weekendReduction = (dayOfWeek === 0 || dayOfWeek === 6) ? 0.4 : 1.0;
      
      trends.push({
        x: date,
        'Fleet A': Math.floor(fleetA * weekendReduction),
        'Fleet B': Math.floor(fleetB * weekendReduction),
        'Fleet C': Math.floor(fleetC * weekendReduction)
      });
    }
    
    return trends;
  };

  const safetyTrends = generateSafetyTrends();

  // Safety trends chart component
  const SafetyTrendsChart = () => (
    <Container header={<Header variant="h2">30-Day Safety Events by Fleet</Header>}>
      <LineChart
        series={[
          {
            title: 'Fleet A',
            type: 'line',
            data: safetyTrends.map(d => ({ x: d.x, y: d['Fleet A'] })),
            color: '#0073bb'
          },
          {
            title: 'Fleet B', 
            type: 'line',
            data: safetyTrends.map(d => ({ x: d.x, y: d['Fleet B'] })),
            color: '#ff6b6b'
          },
          {
            title: 'Fleet C',
            type: 'line', 
            data: safetyTrends.map(d => ({ x: d.x, y: d['Fleet C'] })),
            color: '#4ecdc4'
          }
        ]}
        xDomain={[safetyTrends[0]?.x, safetyTrends[safetyTrends.length - 1]?.x]}
        yDomain={[0, Math.max(...safetyTrends.map(d => Math.max(d['Fleet A'], d['Fleet B'], d['Fleet C']))) + 2]}
        i18nStrings={{
          legendAriaLabel: 'Legend',
          chartAriaRoleDescription: 'Line chart showing 30-day safety events by fleet',
          xTickFormatter: (e) => {
            if (typeof e === 'number') {
              return (e.getMonth() + 1) + '/' + e.getDate();
            }
            const date = new Date(e);
            return (date.getMonth() + 1) + '/' + date.getDate();
          },
          yTickFormatter: (e) => e.toString()
        }}
        ariaLabel="30-day safety events by fleet"
        height={300}
        hideFilter
        hideLegend={false}
        xScaleType="time"
        yTitle="Safety Events"
        xTitle="Date"
      />
    </Container>
  );

  return (
    <div className="safety-alerts-page">
      <SpaceBetween size="l">
       
        {error && (
          <Alert
            statusIconAriaLabel="Error"
            type="error"
            header="API Connection Error"
            dismissible
            onDismiss={() => setError(null)}
          >
            {error}
          </Alert>
        )}

        <Container>
          <SpaceBetween size="l">
            <ColumnLayout columns={2} variant="text-grid">
              <div>
                <Box variant="awsui-key-label">Fleet Filter</Box>
                <AlertsFleetFilter
                  selectedFleet={selectedFleet}
                  onFleetChange={handleFleetChange}
                />
              </div>
              <div>
                <Box variant="awsui-key-label">Time Range</Box>
                <Select
                  selectedOption={timeRangeOptions.find(option => option.value === selectedTimeRange)}
                  onChange={({ detail }) => {
                    const newTimeRange = detail.selectedOption.value!;
                    setSelectedTimeRange(newTimeRange);
                    // Trigger new API call with updated time range
                    const updatedFilters = { ...currentFilters, timeRange: newTimeRange };
                    setCurrentFilters(updatedFilters);
                    fetchSafetyEvents(1, pagination.limit, updatedFilters);
                  }}
                  options={timeRangeOptions}
                  placeholder="Select time range"
                />
              </div>
            </ColumnLayout>
          </SpaceBetween>
        </Container>

        <Tabs
          activeTabId={activeTab}
          onChange={({ detail }) => setActiveTab(detail.activeTabId)}
          tabs={[
            {
              id: 'overview',
              label: 'Overview',
              content: (
                <Container>
                  <SpaceBetween size="l">
                    <Grid
                      gridDefinition={[
                        { colspan: { default: 12, xs: 6, s: 3 } },
                        { colspan: { default: 12, xs: 6, s: 3 } },
                        { colspan: { default: 12, xs: 6, s: 3 } },
                        { colspan: { default: 12, xs: 6, s: 3 } },
                      ]}
                    >
                      <Container>
                        <div className="metric-container">
                          <Box textAlign="center">
                            <div className="metric-value">{currentMetrics.totalEvents}</div>
                            <div className="metric-label">Total Events</div>
                          </Box>
                        </div>
                      </Container>
                      <Container>
                        <div className="metric-container">
                          <Box textAlign="center">
                            <div className="metric-value high-priority">{currentMetrics.highSeverityEvents}</div>
                            <div className="metric-label">High Severity</div>
                          </Box>
                        </div>
                      </Container>
                      <Container>
                        <div className="metric-container">
                          <Box textAlign="center">
                            <div className="metric-value pending">{currentMetrics.pendingEvents}</div>
                            <div className="metric-label">Pending</div>
                          </Box>
                        </div>
                      </Container>
                      <Container>
                        <div className="metric-container">
                          <Box textAlign="center">
                            <div className="metric-value resolved">{currentMetrics.resolvedEvents}</div>
                            <div className="metric-label">Resolved</div>
                          </Box>
                        </div>
                      </Container>
                    </Grid>

                    <ColumnLayout columns={2}>
                      <Container header={<Header variant="h2">Severity Distribution</Header>}>
                        <KeyValuePairs
                          columns={2}
                          items={[
                            { label: 'High Severity', value: currentMetrics.highSeverityEvents },
                            { label: 'Medium Severity', value: currentMetrics.mediumSeverityEvents },
                            { label: 'Low Severity', value: currentMetrics.lowSeverityEvents },
                            { label: 'Resolved', value: currentMetrics.resolvedEvents },
                          ]}
                        />
                      </Container>
                      <Container header={<Header variant="h2">Event Type Analysis</Header>}>
                        <KeyValuePairs
                          columns={1}
                          items={[
                            { label: 'Lane Departures', value: currentMetrics.laneDepartureEvents },
                            { label: 'Speeding Events', value: currentMetrics.speedingEvents },
                            { label: 'Hard Braking', value: currentMetrics.hardBrakingEvents },
                            { label: 'Drowsiness Detected', value: currentMetrics.drowsinessEvents },
                          ]}
                        />
                      </Container>
                    </ColumnLayout>
                    
                    <SafetyTrendsChart />
                  </SpaceBetween>
                </Container>
              )
            },
            {
              id: 'events',
              label: `Detailed Events (${pagination.total})`,
              content: (
                <SafetyEventsTable
                  safetyEvents={safetyEvents}
                  pagination={pagination}
                  loading={loading}
                  onRefresh={refreshData}
                  onPageChange={(page) => fetchSafetyEvents(page, pagination.limit, currentFilters)}
                  onPageSizeChange={(pageSize) => fetchSafetyEvents(1, pageSize, currentFilters)}
                  onFilterChange={handleFilterChange}
                />
              )
            }
          ]}
        />
      </SpaceBetween>
    </div>
  );
}
