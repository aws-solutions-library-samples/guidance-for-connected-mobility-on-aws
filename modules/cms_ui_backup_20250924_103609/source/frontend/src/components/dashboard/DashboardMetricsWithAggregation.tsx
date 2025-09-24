// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Grid,
  Box,
  ColumnLayout,
  KeyValuePairs,
  Table,
  StatusIndicator,
  ProgressBar,
  Badge,
  Select,
  DateRangePicker,
  Button
} from '@cloudscape-design/components';
import { FleetFilterContainer } from '../commons/FleetFilterContainer';
import { getRuntimeConfig } from '../../config/api';
import { useAuth } from '../../auth/useAuth';

interface FleetMetrics {
  fleetId: string;
  totalVehicles: number;
  activeVehicles: number;
  totalTrips: number;
  totalMiles: number;
  avgDriverScore: number;
  safetyScore: number;
  safetyEventsTotal: number;
  safetyEventsPer1000Miles: number;
  maintenanceAlertsTotal: number;
  maintenanceAlertsPerVehicle: number;
  utilizationMilesPerVehicle: number;
}

interface DashboardData {
  fleetPerformance: Record<string, FleetMetrics>;
  rankings: {
    safestFleets: FleetMetrics[];
    bestDriverScores: FleetMetrics[];
    mostEfficient: FleetMetrics[];
    leastMaintenance: FleetMetrics[];
  };
  summary: {
    totalFleets: number;
    totalVehicles: number;
    totalMiles: number;
    avgSafetyScore: number;
  };
}

export function DashboardMetricsWithAggregation() {
  const [dashboardData, setDashboardData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedFleet, setSelectedFleet] = useState<any>(null);
  const [dateRange, setDateRange] = useState<any>(null);
  const { getAuthHeaders } = useAuth();

  const fetchDashboardData = async () => {
    try {
      setLoading(true);
      const apiEndpoint = getRuntimeConfig().apiEndpoint;
      
      let url = `${apiEndpoint.replace(/\/$/, '')}/api/v1/dashboard/fleet-comparison`;
      const params = new URLSearchParams();
      
      if (selectedFleet?.value) {
        params.append('fleetId', selectedFleet.value);
      }
      if (dateRange?.startDate) {
        params.append('startDate', dateRange.startDate);
      }
      if (dateRange?.endDate) {
        params.append('endDate', dateRange.endDate);
      }
      
      if (params.toString()) {
        url += `?${params.toString()}`;
      }

      const response = await fetch(url, {
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        }
      });

      if (response.ok) {
        const data = await response.json();
        setDashboardData(data);
      }
    } catch (error) {
      console.error('Error fetching dashboard data:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDashboardData();
  }, [selectedFleet, dateRange]);

  const fleetOptions = dashboardData ? 
    Object.keys(dashboardData.fleetPerformance).map(fleetId => ({
      label: fleetId,
      value: fleetId
    })) : [];

  const formatNumber = (num: number | undefined) => (num ?? 0).toLocaleString();
  const formatDecimal = (num: number | undefined, decimals = 1) => (num ?? 0).toFixed(decimals);

  if (loading) {
    return (
      <Container>
        <Box textAlign="center" padding="xxl">
          <StatusIndicator type="loading">Loading dashboard metrics...</StatusIndicator>
        </Box>
      </Container>
    );
  }

  if (!dashboardData || !dashboardData.rankings) {
    return (
      <Container>
        <Box textAlign="center" padding="xxl">
          <StatusIndicator type="error">Failed to load dashboard data</StatusIndicator>
        </Box>
      </Container>
    );
  }

  return (
    <SpaceBetween size="l">
      {/* Header with Filters */}
      <Container>
        <SpaceBetween size="m">
          <Header
            variant="h1"
            description="Fleet performance metrics and comparisons across your connected mobility operations"
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={fetchDashboardData} iconName="refresh">
                  Refresh
                </Button>
              </SpaceBetween>
            }
          >
            Fleet Performance Dashboard
          </Header>
          
          <FleetFilterContainer
            selectedFleet={selectedFleet?.value || 'all'}
            onFleetChange={(fleetId) => {
              const option = fleetOptions.find(opt => opt.value === fleetId) || { label: 'All Fleets', value: 'all' };
              setSelectedFleet(option);
            }}
            dateRange={dateRange}
            onDateRangeChange={setDateRange}
            showDateRange={true}
            showTimeRange={false}
            title="Dashboard Filters"
          />
        </SpaceBetween>
      </Container>

      {/* Summary Metrics */}
      <Container>
        <Header variant="h2">Fleet Overview</Header>
        <ColumnLayout columns={4} variant="text-grid">
          <KeyValuePairs
            columns={1}
            items={[
              { label: 'Total Fleets', value: formatNumber(dashboardData.summary.totalFleets || 0) },
              { label: 'Total Vehicles', value: formatNumber(dashboardData.summary.totalVehicles || 0) }
            ]}
          />
          <KeyValuePairs
            columns={1}
            items={[
              { label: 'Total Miles Driven', value: formatNumber(dashboardData.summary.totalMiles || 0) },
              { label: 'Average Safety Score', value: formatDecimal(dashboardData.summary.avgSafetyScore || 0) }
            ]}
          />
          <Box>
            <Box variant="awsui-key-label">Fleet Safety Score</Box>
            <ProgressBar
              value={Math.min(Math.max(dashboardData.summary.avgSafetyScore || 0, 0), 100)}
              additionalInfo={`${formatDecimal(dashboardData.summary.avgSafetyScore || 0)}/100`}
              description="Higher is better"
            />
          </Box>
          <Box>
            <Box variant="awsui-key-label">Fleet Utilization</Box>
            <ProgressBar
              value={75}
              additionalInfo="75% active"
              description="Vehicle utilization rate"
            />
          </Box>
        </ColumnLayout>
      </Container>

      {/* Fleet Rankings */}
      <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
        <Container>
          <Header variant="h2">Top Performing Fleets</Header>
          <SpaceBetween size="m">
            <Box>
              <Box variant="h3">Safest Fleets</Box>
              <Table
                columnDefinitions={[
                  {
                    id: 'rank',
                    header: 'Rank',
                    cell: (item) => {
                      const index = dashboardData.rankings.safestFleets.indexOf(item);
                      return `#${index + 1}`;
                    },
                    width: 60
                  },
                  {
                    id: 'fleetId',
                    header: 'Fleet ID',
                    cell: item => item.fleetId
                  },
                  {
                    id: 'safetyScore',
                    header: 'Safety Score',
                    cell: item => (
                      <Badge color={item.safetyScore > 95 ? 'green' : item.safetyScore > 90 ? 'blue' : 'grey'}>
                        {formatDecimal(item.safetyScore || 0)}
                      </Badge>
                    )
                  },
                  {
                    id: 'events',
                    header: 'Events/1K Miles',
                    cell: item => formatDecimal(item.safetyEventsPer1000Miles || 0, 2)
                  }
                ]}
                items={dashboardData.rankings.safestFleets.slice(0, 5)}
                variant="embedded"
              />
            </Box>
          </SpaceBetween>
        </Container>

        <Container>
          <Header variant="h2">Performance Metrics</Header>
          <SpaceBetween size="m">
            <Box>
              <Box variant="h3">Best Driver Scores</Box>
              <Table
                columnDefinitions={[
                  {
                    id: 'rank',
                    header: 'Rank',
                    cell: (item) => {
                      const index = dashboardData.rankings.bestDriverScores.indexOf(item);
                      return `#${index + 1}`;
                    },
                    width: 60
                  },
                  {
                    id: 'fleetId',
                    header: 'Fleet ID',
                    cell: item => item.fleetId
                  },
                  {
                    id: 'driverScore',
                    header: 'Avg Driver Score',
                    cell: item => (
                      <Badge color={item.avgDriverScore > 90 ? 'green' : item.avgDriverScore > 80 ? 'blue' : 'grey'}>
                        {formatDecimal(item.avgDriverScore || 0)}
                      </Badge>
                    )
                  },
                  {
                    id: 'trips',
                    header: 'Total Trips',
                    cell: item => formatNumber(item.totalTrips || 0)
                  }
                ]}
                items={dashboardData.rankings.bestDriverScores.slice(0, 5)}
                variant="embedded"
              />
            </Box>
          </SpaceBetween>
        </Container>
      </Grid>

      {/* Fleet Comparison Table */}
      <Container>
        <Header 
          variant="h2"
          counter={`(${Object.keys(dashboardData.fleetPerformance).length})`}
        >
          Fleet Performance Comparison
        </Header>
        <Table
          columnDefinitions={[
            {
              id: 'fleetId',
              header: 'Fleet ID',
              cell: item => item.fleetId,
              isRowHeader: true
            },
            {
              id: 'vehicles',
              header: 'Vehicles',
              cell: item => (
                <Box>
                  <Box>{formatNumber(item.totalVehicles)} total</Box>
                  <Box variant="small" color="text-body-secondary">
                    {item.activeVehicles} active
                  </Box>
                </Box>
              )
            },
            {
              id: 'utilization',
              header: 'Utilization',
              cell: item => (
                <Box>
                  <Box>{formatNumber(item.utilizationMilesPerVehicle)} mi/vehicle</Box>
                  <Box variant="small" color="text-body-secondary">
                    {formatNumber(item.totalTrips)} trips
                  </Box>
                </Box>
              )
            },
            {
              id: 'safety',
              header: 'Safety Performance',
              cell: item => (
                <Box>
                  <Badge color={item.safetyScore > 95 ? 'green' : item.safetyScore > 90 ? 'blue' : 'grey'}>
                    Score: {formatDecimal(item.safetyScore)}
                  </Badge>
                  <Box variant="small" color="text-body-secondary">
                    {item.safetyEventsTotal} events total
                  </Box>
                </Box>
              )
            },
            {
              id: 'maintenance',
              header: 'Maintenance',
              cell: item => (
                <Box>
                  <Box>{formatDecimal(item.maintenanceAlertsPerVehicle)} alerts/vehicle</Box>
                  <Box variant="small" color="text-body-secondary">
                    {item.maintenanceAlertsTotal} total alerts
                  </Box>
                </Box>
              )
            },
            {
              id: 'driverScore',
              header: 'Driver Performance',
              cell: item => (
                <Badge color={item.avgDriverScore > 90 ? 'green' : item.avgDriverScore > 80 ? 'blue' : 'grey'}>
                  {formatDecimal(item.avgDriverScore)}
                </Badge>
              )
            }
          ]}
          items={Object.values(dashboardData.fleetPerformance)}
          sortingDisabled={false}
          variant="full-page"
          empty={
            <Box textAlign="center" color="inherit">
              <b>No fleet data</b>
              <Box padding={{ bottom: 's' }} variant="p" color="inherit">
                No fleet performance data available.
              </Box>
            </Box>
          }
        />
      </Container>
    </SpaceBetween>
  );
}
