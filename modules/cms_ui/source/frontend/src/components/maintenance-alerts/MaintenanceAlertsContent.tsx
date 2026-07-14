// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect, useContext } from 'react';
import { getRuntimeConfig } from '../../../config/api';
import {
  Container,
  Header,
  SpaceBetween,
  Grid,
  Box,
  ColumnLayout,
  KeyValuePairs,
  Tabs,
  Icon,
  Popover,
  LineChart,
  TextContent,
  Alert,
  Badge,
  Button,
  Select
} from '@cloudscape-design/components';
import MaintenanceEventsTable from './MaintenanceEventsTable';
import { FleetFilterContainer } from '../commons/FleetFilterContainer';
import { DashboardHeader } from '../alerts/header';
import { PageBanner } from '@/components/dashboard/components/page-banner';
import { ApiContext } from '@/api/provider';

import {
  RealMaintenanceAlertsClient,
  ListMaintenanceAlertsCommand,
  MaintenanceAlert as APIMaintenanceAlert,
} from '@/api/maintenance-alerts-client';

// Frontend interface for maintenance events
interface MaintenanceEvent {
  id: string;
  vehicleId: string;
  vin: string;
  type: string;
  severity: 'low' | 'medium' | 'high' | 'critical';
  priority: 'low' | 'medium' | 'high';
  urgency: 'routine' | 'moderate' | 'urgent';
  timestamp: string;
  location: {
    lat: number;
    lng: number;
  };
  estimatedCost: number;
  category: string;
  fleetName: string;
  description?: string;
  resolved?: boolean;
  driverId?: string;
  tripId?: string;
}

// Metrics interface
interface MaintenanceMetrics {
  totalEvents: number;
  engineMaintenanceEvents: number;
  brakeMaintenanceEvents: number;
  tireMaintenanceEvents: number;
  batteryReplacementEvents: number;
  averageCost: number;
  trendsData: any[];
}

// Static sample data - NEVER changes
const STATIC_SAMPLE_EVENTS: MaintenanceEvent[] = Array.from({ length: 55 }, (_, i) => ({
  id: `maint-${(i + 1).toString().padStart(3, '0')}`,
  vehicleId: `VEH-${(i + 1).toString().padStart(3, '0')}`,
  vin: `VIN${(i + 1).toString().padStart(3, '0')}`,
  type: 'oil_change_due',
  severity: 'medium' as const,
  priority: 'medium' as const,
  urgency: 'moderate',
  timestamp: new Date(Date.now() - (i * 2 * 60 * 60 * 1000)).toISOString(),
  location: { lat: 37.7749, lng: -122.4194 },
  estimatedCost: 100,
  category: 'ENGINE',
  fleetName: 'Fleet A',
  description: `Maintenance event ${i + 1}`,
  resolved: false
}));

console.log('🔧 STATIC_SAMPLE_EVENTS created with length:', STATIC_SAMPLE_EVENTS.length);

export function MaintenanceAlertsContent() {
  const [maintenanceEvents, setMaintenanceEvents] = useState<MaintenanceEvent[]>([]);
  const [pagination, setPagination] = useState({
    total: 0,
    page: 1,
    limit: 20,
    totalPages: 0,
    hasNextPage: false,
    hasPrevPage: false,
    returned: 0
  });
  const [metrics, setMetrics] = useState<MaintenanceMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedTimeRange, setSelectedTimeRange] = useState('7d');
  const [activeTab, setActiveTab] = useState('overview');
  const [refreshInProgress, setRefreshInProgress] = useState(false);
  const [dataSource, setDataSource] = useState<'api' | 'sample'>('api');
  const [error, setError] = useState<string | null>(null);

  // Use the API context to get the endpoint
  const apiContext = useContext(ApiContext);

  // Create maintenance alerts client
  const maintenanceAlertsClient = new RealMaintenanceAlertsClient({
    endpoint: apiContext?.config?.baseUrl || 'http://localhost:5001'
  });

  // Fetch maintenance events using the client pattern with pagination
  const fetchMaintenanceEvents = async (page: number = 1, limit: number = 20) => {
    try {
      setLoading(true);
      setError(null);
      
      console.log(`🔧 Fetching maintenance events page ${page} with limit ${limit}...`);
      
      // Calculate time range for API query
      const now = new Date();
      const timeRangeMs = {
        '1h': 60 * 60 * 1000,
        '6h': 6 * 60 * 60 * 1000,
        '24h': 24 * 60 * 60 * 1000,
        '7d': 7 * 24 * 60 * 60 * 1000,
        '30d': 30 * 24 * 60 * 60 * 1000,
      }[selectedTimeRange] || 7 * 24 * 60 * 60 * 1000;
      
      const startTime = now.getTime() - timeRangeMs;
      const endTime = now.getTime();

      // Use the maintenance alerts client with pagination parameters
      const listEventsCmd = new ListMaintenanceAlertsCommand({
        startTime,
        endTime,
        limit,
        page,
        fleetId: selectedFleet !== 'all' ? selectedFleet : undefined
      });

      const eventsResponse = await maintenanceAlertsClient.send(listEventsCmd);

      console.log('🔧 Maintenance events API response:', eventsResponse);

      // Transform API events to match our interface
      const apiAlerts = eventsResponse.alerts || [];
      const apiPagination = {
        total: eventsResponse.total || 0,
        page: eventsResponse.page || 1,
        limit: eventsResponse.limit || 20,
        totalPages: eventsResponse.totalPages || 0,
        hasNextPage: eventsResponse.hasNextPage || false,
        hasPrevPage: eventsResponse.hasPrevPage || false,
        returned: eventsResponse.returned || 0
      };

      const transformedEvents: MaintenanceEvent[] = apiAlerts.map((alert: any) => {
        // Generate estimated cost based on alert type
        const getEstimatedCost = (alertType: string) => {
          const costs = {
            'LOW_OIL_PRESSURE': 150,
            'ENGINE_OVERHEATING': 800,
            'BRAKE_WEAR': 400,
            'TIRE_PRESSURE_LOW': 50,
            'BATTERY_LOW': 200,
            'TRANSMISSION_ISSUE': 1200,
            'COOLANT_LOW': 100,
            'AIR_FILTER_DIRTY': 75,
            'SPARK_PLUG_REPLACEMENT': 300,
            'BELT_REPLACEMENT': 250
          };
          return costs[alertType] || 200; // Default cost
        };

        return {
          id: alert.alertId || alert.id,
          vehicleId: alert.vehicleId,
          vin: alert.vin || alert.vehicleId, // Don't append "VIN" prefix
          type: alert.alertType || alert.type || 'general',
          alertType: alert.alertType, // Keep original alertType
          severity: alert.severity?.toLowerCase() || 'medium',
          priority: alert.severity?.toLowerCase() || 'medium', // Use severity for priority
          urgency: alert.urgency?.toLowerCase() || 'moderate',
          timestamp: alert.timestamp,
          location: {
            lat: alert.location?.lat || 0,
            lng: alert.location?.lng || 0
          },
          estimatedCost: alert.estimatedCost || getEstimatedCost(alert.alertType),
          category: alert.category || 'GENERAL',
          fleetName: getFleetName(alert.fleetId),
          description: alert.message || alert.description,
          resolved: alert.resolved || false,
          driverId: `driver-${alert.vehicleId}`,
          tripId: alert.tripId || `trip-${alert.id}`
        };
      });

      setMaintenanceEvents(transformedEvents);
      console.log('🔧 Setting pagination data:', apiPagination);
      
      // Add returned count based on actual events received
      const paginationWithReturned = {
        ...apiPagination,
        returned: transformedEvents.length
      };
      
      setPagination(paginationWithReturned);
      
      // Use metrics from API response instead of calculating locally
      const apiMetrics = eventsResponse.metrics || {};
      
      const transformedMetrics: MaintenanceMetrics = {
        totalEvents: apiMetrics.totalEvents || 0,
        engineMaintenanceEvents: apiMetrics.engineMaintenanceEvents || 0,
        brakeMaintenanceEvents: apiMetrics.brakeMaintenanceEvents || 0,
        tireMaintenanceEvents: apiMetrics.tireMaintenanceEvents || 0,
        batteryReplacementEvents: apiMetrics.batteryReplacementEvents || 0,
        averageCost: apiMetrics.averageCost || 0,
        trendsData: generateTrendsData(transformedEvents)
      };

      setMetrics(transformedMetrics);
      setDataSource('api');
      
      console.log(`✅ Loaded ${transformedEvents.length} maintenance events from API Gateway (page ${page}/${apiPagination.totalPages})`);
      
    } catch (error) {
      console.warn('Failed to fetch from Maintenance Alerts API:', error);
      setError('Unable to connect to Maintenance Alerts API. Please ensure the API Gateway is deployed and accessible.');
      
      // Use static sample data as fallback
      console.log('🔧 Using STATIC sample data - ALWAYS 55 events');
      console.log('🔧 STATIC_SAMPLE_EVENTS.length:', STATIC_SAMPLE_EVENTS.length);
      
      // Simulate server-side pagination with static sample data
      const startIndex = (page - 1) * limit;
      const endIndex = startIndex + limit;
      const paginatedSampleEvents = STATIC_SAMPLE_EVENTS.slice(startIndex, endIndex);
      console.log('🔧 Page', page, 'events:', paginatedSampleEvents.length, '(indices', startIndex, 'to', endIndex, ')');
      
      const totalPages = Math.ceil(STATIC_SAMPLE_EVENTS.length / limit);
      console.log('🔧 STATIC totalPages:', totalPages, '(', STATIC_SAMPLE_EVENTS.length, '/', limit, ')');
      
      setMaintenanceEvents(paginatedSampleEvents);
      setMetrics(generateSampleMetrics(STATIC_SAMPLE_EVENTS)); // Metrics based on all data
      const paginationData = {
        total: STATIC_SAMPLE_EVENTS.length,
        page: page,
        limit: limit,
        totalPages: totalPages,
        hasNextPage: page < totalPages,
        hasPrevPage: page > 1,
        returned: paginatedSampleEvents.length
      };
      console.log('🔧 STATIC pagination data:', paginationData);
      setPagination(paginationData);
      setDataSource('sample');
    } finally {
      setLoading(false);
    }
  };

  // Handle page changes
  const handlePageChange = (page: number) => {
    fetchMaintenanceEvents(page, pagination.limit);
  };

  // Handle page size changes
  const handlePageSizeChange = (pageSize: number) => {
    fetchMaintenanceEvents(1, pageSize); // Reset to page 1 when changing page size
  };

  // Refresh data function
  const refreshData = async () => {
    setRefreshInProgress(true);
    await fetchMaintenanceEvents(pagination.page, pagination.limit);
    setRefreshInProgress(false);
  };

  // Fleet filter state
  const [selectedFleet, setSelectedFleet] = useState<string>('all');

  // Fetch maintenance events on component mount and when filters change
  useEffect(() => {
    fetchMaintenanceEvents(1, 20); // Reset to page 1 when filters change
  }, [selectedTimeRange, selectedFleet]);

  // Auto-refresh every 60 seconds for API data
  useEffect(() => {
    if (dataSource !== 'api') return;

    const interval = setInterval(() => {
      fetchMaintenanceEvents(pagination.page, pagination.limit);
    }, 60000); // 60 seconds for DynamoDB data

    return () => clearInterval(interval);
  }, [dataSource, selectedTimeRange, pagination.page, pagination.limit]);

  // Time range options
  const timeRangeOptions = [
    { label: 'Last hour', value: '1h' },
    { label: 'Last 6 hours', value: '6h' },
    { label: 'Last 24 hours', value: '24h' },
    { label: 'Last 7 days', value: '7d' },
    { label: 'Last 30 days', value: '30d' },
  ];

  // Calculate filtered metrics
  const getFilteredEvents = () => {
    let filtered = maintenanceEvents;
    
    const now = new Date();
    const timeRangeMs = {
      '1h': 60 * 60 * 1000,
      '6h': 6 * 60 * 60 * 1000,
      '24h': 24 * 60 * 60 * 1000,
      '7d': 7 * 24 * 60 * 60 * 1000,
      '30d': 30 * 24 * 60 * 60 * 1000,
    }[selectedTimeRange] || 7 * 24 * 60 * 60 * 1000;
    
    const cutoffTime = new Date(now.getTime() - timeRangeMs);
    filtered = filtered.filter(event => new Date(event.timestamp) >= cutoffTime);
    
    return filtered;
  };

  const filteredEvents = getFilteredEvents();

  // Calculate current metrics with proper color coding
  const currentMetrics = {
    totalEvents: maintenanceEvents.length,
    highPriorityEvents: maintenanceEvents.filter(e => e.priority === 'high').length,
    mediumPriorityEvents: maintenanceEvents.filter(e => e.priority === 'medium').length,
    lowPriorityEvents: maintenanceEvents.filter(e => e.priority === 'low').length,
    resolvedEvents: maintenanceEvents.filter(e => e.resolved).length,
    pendingEvents: maintenanceEvents.filter(e => !e.resolved).length,
    averageCost: maintenanceEvents.length > 0 
      ? maintenanceEvents.reduce((sum, e) => sum + (e.estimatedCost || 0), 0) / maintenanceEvents.length
      : 0,
  };

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

  const calculateAverageCost = (events: MaintenanceEvent[]): number => {
    if (events.length === 0) return 0;
    const totalCost = events.reduce((sum, event) => sum + (event.estimatedCost || 0), 0);
    return totalCost / events.length;
  };

  const generateTrendsData = (events: MaintenanceEvent[]): any[] => {
    // Generate simple trends data for the last 7 days
    const trends = [];
    const now = new Date();
    
    for (let i = 6; i >= 0; i--) {
      const date = new Date(now.getTime() - i * 24 * 60 * 60 * 1000);
      const dayEvents = events.filter(event => {
        const eventDate = new Date(event.timestamp);
        return eventDate.toDateString() === date.toDateString();
      });
      
      trends.push({
        date: date.toISOString().split('T')[0],
        events: dayEvents.length,
        cost: dayEvents.reduce((sum, e) => sum + (e.estimatedCost || 0), 0)
      });
    }
    
    return trends;
  };

  const generateSampleMaintenanceEvents = (): MaintenanceEvent[] => {
    // Generate sample maintenance events for fallback (50+ events to test pagination)
    const events: MaintenanceEvent[] = [];
    const types = ['oil_change_due', 'brake_inspection', 'tire_service', 'engine_service', 'battery_replacement', 'transmission_service'];
    const severities = ['low', 'medium', 'high', 'critical'];
    const priorities = ['low', 'medium', 'high'];
    const categories = ['ENGINE', 'BRAKES', 'TIRES', 'ELECTRICAL', 'TRANSMISSION'];
    const fleets = ['Fleet A', 'Fleet B', 'Fleet C'];
    
    for (let i = 1; i <= 55; i++) {
      events.push({
        id: `maint-${i.toString().padStart(3, '0')}`,
        vehicleId: `VEH-${i.toString().padStart(3, '0')}`,
        vin: `VIN${i.toString().padStart(3, '0')}`,
        type: types[Math.floor(Math.random() * types.length)],
        severity: severities[Math.floor(Math.random() * severities.length)] as 'low' | 'medium' | 'high' | 'critical',
        priority: priorities[Math.floor(Math.random() * priorities.length)] as 'low' | 'medium' | 'high',
        urgency: 'moderate',
        timestamp: new Date(Date.now() - (i * 2 * 60 * 60 * 1000)).toISOString(), // Spread events over time
        location: { 
          lat: 37.7749 + (Math.random() - 0.5) * 0.1, 
          lng: -122.4194 + (Math.random() - 0.5) * 0.1 
        },
        estimatedCost: Math.floor(Math.random() * 500) + 50,
        category: categories[Math.floor(Math.random() * categories.length)],
        fleetName: fleets[Math.floor(Math.random() * fleets.length)],
        description: `Maintenance required for ${types[Math.floor(Math.random() * types.length)].replace('_', ' ')}`,
        resolved: Math.random() > 0.7 // 30% resolved
      });
    }
    
    return events.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
  };

  const generateSampleMetrics = (events: MaintenanceEvent[]): MaintenanceMetrics => {
    return {
      totalEvents: events.length,
      engineMaintenanceEvents: events.filter(e => e.category === 'ENGINE').length,
      brakeMaintenanceEvents: events.filter(e => e.category === 'BRAKES').length,
      tireMaintenanceEvents: events.filter(e => e.category === 'TIRES').length,
      batteryReplacementEvents: events.filter(e => e.type.includes('battery')).length,
      averageCost: calculateAverageCost(events),
      trendsData: generateTrendsData(events)
    };
  };

  // Generate 30-day maintenance trends data
  const generateMaintenanceTrends = () => {
    const trends = [];
    const now = new Date();
    
    for (let i = 29; i >= 0; i--) {
      const date = new Date(now.getTime() - i * 24 * 60 * 60 * 1000);
      
      // Generate realistic daily maintenance counts
      const oilChange = Math.floor(Math.random() * 3) + 1; // 1-3 per day
      const brakeService = Math.floor(Math.random() * 2); // 0-1 per day  
      const tireRotation = Math.floor(Math.random() * 2); // 0-1 per day
      const engineCheck = Math.floor(Math.random() * 1.5); // 0-1 per day
      
      // Weekend reduction
      const dayOfWeek = date.getDay();
      const weekendReduction = (dayOfWeek === 0 || dayOfWeek === 6) ? 0.3 : 1.0;
      
      trends.push({
        x: date,
        oilChange: Math.floor(oilChange * weekendReduction),
        brakeService: Math.floor(brakeService * weekendReduction),
        tireRotation: Math.floor(tireRotation * weekendReduction),
        engineCheck: Math.floor(engineCheck * weekendReduction)
      });
    }
    
    return trends;
  };

  const maintenanceTrends = generateMaintenanceTrends();

  // Maintenance trends chart component
  const MaintenanceTrendsChart = () => (
    <Container header={<Header variant="h2">30-Day Maintenance Trends</Header>}>
      <LineChart
        series={[
          {
            title: 'Oil Changes',
            type: 'line',
            data: maintenanceTrends.map(d => ({ x: d.x, y: d.oilChange })),
            color: '#0073bb'
          },
          {
            title: 'Brake Service',
            type: 'line', 
            data: maintenanceTrends.map(d => ({ x: d.x, y: d.brakeService })),
            color: '#d13212'
          },
          {
            title: 'Tire Rotation',
            type: 'line',
            data: maintenanceTrends.map(d => ({ x: d.x, y: d.tireRotation })),
            color: '#ff9900'
          },
          {
            title: 'Engine Check',
            type: 'line',
            data: maintenanceTrends.map(d => ({ x: d.x, y: d.engineCheck })),
            color: '#037f0c'
          }
        ]}
        yDomain={[0, Math.max(...maintenanceTrends.map(d => Math.max(d.oilChange, d.brakeService, d.tireRotation, d.engineCheck)), 1)]}
        xScaleType="time"
        i18nStrings={{
          filterLabel: "Filter displayed data",
          filterPlaceholder: "Filter data",
          filterSelectedAriaLabel: "selected", 
          legendAriaLabel: "Legend",
          chartAriaRoleDescription: "line chart showing maintenance trends over 30 days",
          xTickFormatter: (e) => {
            if (e instanceof Date) {
              return (e.getMonth() + 1) + '/' + e.getDate();
            }
            const date = new Date(e);
            return (date.getMonth() + 1) + '/' + date.getDate();
          },
          yTickFormatter: (e) => e.toString()
        }}
        ariaLabel="30-day maintenance trends"
        height={300}
        xTitle="Date"
        yTitle="Number of Services"
        empty={
          <Box textAlign="center" color="inherit">
            <b>No maintenance data available</b>
            <Box variant="p" color="inherit">
              There is no maintenance data available
            </Box>
          </Box>
        }
      />
    </Container>
  );

  return (
    <div className="safety-alerts-page">
      <SpaceBetween size="l">
        {/* Dashboard Header */}
        <DashboardHeader
          title="Maintenance Alerts"
          actions={
            <SpaceBetween size="xs" direction="horizontal">
              <Button
                iconName="refresh"
                onClick={() => refreshData()}
                disabled={refreshInProgress}
                disabledReason="Refresh in progress..."
              >
                Refresh
              </Button>
              <Button iconName="add-plus" onClick={() => {}}>
                Schedule Maintenance
              </Button>
            </SpaceBetween>
          }
        />
        
        <PageBanner />

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

        <FleetFilterContainer
          selectedFleet={selectedFleet}
          onFleetChange={setSelectedFleet}
          selectedTimeRange={selectedTimeRange}
          onTimeRangeChange={setSelectedTimeRange}
          timeRangeOptions={timeRangeOptions}
          showDateRange={false}
          showTimeRange={true}
          title="Filters"
        />

        <Tabs
          className="safety-alerts-tabs"
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
                            <div className="metric-value high-priority">{currentMetrics.highPriorityEvents}</div>
                            <div className="metric-label">High Priority</div>
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
                            <div className="metric-value">${currentMetrics.averageCost.toFixed(2)}</div>
                            <div className="metric-label">Avg Cost</div>
                          </Box>
                        </div>
                      </Container>
                    </Grid>

                    <ColumnLayout columns={2}>
                      <Container header={<Header variant="h2">Priority Distribution</Header>}>
                        <KeyValuePairs
                          columns={2}
                          items={[
                            { label: 'High Priority', value: currentMetrics.highPriorityEvents },
                            { label: 'Medium Priority', value: currentMetrics.mediumPriorityEvents },
                            { label: 'Low Priority', value: currentMetrics.lowPriorityEvents },
                            { label: 'Resolved', value: currentMetrics.resolvedEvents },
                          ]}
                        />
                      </Container>
                      <Container header={<Header variant="h2">Cost Analysis</Header>}>
                        <KeyValuePairs
                          columns={1}
                          items={[
                            { label: 'Average Cost', value: `$${currentMetrics.averageCost.toFixed(2)}` },
                            { label: 'Total Estimated Cost', value: `$${(maintenanceEvents.reduce((sum, e) => sum + (e.estimatedCost || 0), 0)).toFixed(2)}` },
                            { label: 'Events This Period', value: maintenanceEvents.length },
                          ]}
                        />
                      </Container>
                    </ColumnLayout>
                    
                    <MaintenanceTrendsChart />
                  </SpaceBetween>
                </Container>
              ),
            },
            {
              id: 'events',
              label: `Detailed Events (${pagination.total})`,
              content: (
                <Container>
                  <MaintenanceEventsTable 
                    maintenanceEvents={maintenanceEvents} 
                    pagination={pagination}
                    loading={loading} 
                    onRefresh={refreshData}
                    onPageChange={handlePageChange}
                    onPageSizeChange={handlePageSizeChange}
                  />
                </Container>
              ),
            },
          ]}
        />
      </SpaceBetween>
    </div>
  );
}
