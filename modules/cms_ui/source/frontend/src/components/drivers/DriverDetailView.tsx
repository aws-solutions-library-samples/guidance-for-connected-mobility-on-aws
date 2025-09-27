// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Container,
  Header,
  SpaceBetween,
  Tabs,
  Box,
  ColumnLayout,
  StatusIndicator,
  Badge,
  Table,
  Pagination,
  TextFilter,
  Button,
  Alert,
  Spinner
} from '@cloudscape-design/components';
import { getRuntimeConfig } from '../../config/api';

interface Driver {
  driverId: string;
  firstName: string;
  lastName: string;
  email: string;
  phone: string;
  licenseNumber: string;
  licenseExpiry: string;
  status: string;
  fleetId: string;
  createdAt: string;
  updatedAt: string;
}

interface Trip {
  tripId: string;
  vehicleId: string;
  startTime: string;
  endTime: string;
  duration: number;
  distance: number;
  startLocation: {
    latitude: number;
    longitude: number;
  };
  endLocation: {
    latitude: number;
    longitude: number;
  };
  maxSpeed: number;
  avgSpeed: number;
  fuelConsumption: number;
  driverScore: number;
}

interface SafetyEvent {
  eventId: string;
  tripId: string;
  vehicleId: string;
  eventType: string;
  severity: string;
  timestamp: number;
  location: {
    latitude: number;
    longitude: number;
  };
  description: string;
}

interface DriverStats {
  totalTrips: number;
  totalDistance: number;
  totalDuration: number;
  avgDriverScore: number;
  safetyEventsCount: number;
  lastTripDate: string;
}

export default function DriverDetailView() {
  const { driverId } = useParams<{ driverId: string }>();
  const navigate = useNavigate();
  const [driver, setDriver] = useState<Driver | null>(null);
  const [stats, setStats] = useState<DriverStats | null>(null);
  const [trips, setTrips] = useState<Trip[]>([]);
  const [safetyEvents, setSafetyEvents] = useState<SafetyEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState('overview');

  // Pagination and filtering
  const [tripsCurrentPage, setTripsCurrentPage] = useState(1);
  const [tripsFilterText, setTripsFilterText] = useState('');
  const [safetyCurrentPage, setSafetyCurrentPage] = useState(1);
  const [safetyFilterText, setSafetyFilterText] = useState('');
  const itemsPerPage = 10;

  useEffect(() => {
    if (driverId) {
      fetchDriverData();
    }
  }, [driverId]);

  const fetchDriverData = async () => {
    setLoading(true);
    setError(null);

    try {
      const runtimeConfig = (window as any).runtimeConfig;
      const apiEndpoint = runtimeConfig?.apiEndpoint || 'getApiEndpoint()/';

      // Fetch driver details
      const driverResponse = await fetch(`${apiEndpoint}api/v1/drivers/${driverId}`);
      if (!driverResponse.ok) {
        throw new Error(`Failed to fetch driver: ${driverResponse.statusText}`);
      }
      const driverData = await driverResponse.json();
      setDriver(driverData.driver || driverData); // Handle both wrapped and unwrapped responses

      // Fetch trips for this driver
      let tripsData = { trips: [] };
      const tripsResponse = await fetch(`${apiEndpoint}api/v1/trips?driverId=${driverId}&limit=100`);
      if (tripsResponse.ok) {
        tripsData = await tripsResponse.json();
        setTrips(tripsData.trips || tripsData.items || []);
      }

      // Fetch safety events for this driver
      let safetyData = { events: [] };
      const safetyResponse = await fetch(`${apiEndpoint}api/v1/safety-events?driverId=${driverId}&limit=100`);
      if (safetyResponse.ok) {
        safetyData = await safetyResponse.json();
        setSafetyEvents(safetyData.events || safetyData.items || []);
      }

      // Calculate stats
      calculateStats(tripsData?.trips || [], safetyData?.events || []);

    } catch (err) {
      console.error('Error fetching driver data:', err);
      setError(err instanceof Error ? err.message : 'Failed to load driver data');
    } finally {
      setLoading(false);
    }
  };

  const calculateStats = (tripsData: Trip[], safetyData: SafetyEvent[]) => {
    if (tripsData.length === 0) {
      setStats({
        totalTrips: 0,
        totalDistance: 0,
        totalDuration: 0,
        avgDriverScore: 0,
        safetyEventsCount: safetyData.length,
        lastTripDate: 'N/A'
      });
      return;
    }

    const totalDistance = tripsData.reduce((sum, trip) => sum + (trip.distance || 0), 0);
    const totalDuration = tripsData.reduce((sum, trip) => sum + (trip.duration || 0), 0);
    const avgScore = tripsData.reduce((sum, trip) => sum + (trip.driverScore || 0), 0) / tripsData.length;
    const lastTrip = tripsData.sort((a, b) => new Date(b.endTime).getTime() - new Date(a.endTime).getTime())[0];

    setStats({
      totalTrips: tripsData.length,
      totalDistance: Math.round(totalDistance * 100) / 100,
      totalDuration: Math.round(totalDuration),
      avgDriverScore: Math.round(avgScore * 10) / 10,
      safetyEventsCount: safetyData.length,
      lastTripDate: lastTrip ? new Date(lastTrip.endTime).toLocaleDateString() : 'N/A'
    });
  };

  const formatDuration = (minutes: number) => {
    const hours = Math.floor(minutes / 60);
    const mins = Math.round(minutes % 60);
    return hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;
  };

  const getScoreColor = (score: number) => {
    if (score >= 90) return 'green';
    if (score >= 70) return 'blue';
    return 'red';
  };

  const getSeverityColor = (severity: string) => {
    switch (severity.toLowerCase()) {
      case 'high': return 'error';
      case 'medium': return 'warning';
      case 'low': return 'success';
      default: return 'info';
    }
  };

  // Filter trips
  const filteredTrips = trips.filter(trip =>
    trip.tripId.toLowerCase().includes(tripsFilterText.toLowerCase()) ||
    trip.vehicleId.toLowerCase().includes(tripsFilterText.toLowerCase())
  );

  // Filter safety events
  const filteredSafetyEvents = safetyEvents.filter(event =>
    event.eventType.toLowerCase().includes(safetyFilterText.toLowerCase()) ||
    event.vehicleId.toLowerCase().includes(safetyFilterText.toLowerCase())
  );

  // Paginate trips
  const tripsStartIndex = (tripsCurrentPage - 1) * itemsPerPage;
  const paginatedTrips = filteredTrips.slice(tripsStartIndex, tripsStartIndex + itemsPerPage);

  // Paginate safety events
  const safetyStartIndex = (safetyCurrentPage - 1) * itemsPerPage;
  const paginatedSafetyEvents = filteredSafetyEvents.slice(safetyStartIndex, safetyStartIndex + itemsPerPage);

  if (loading) {
    return (
      <Container>
        <Box textAlign="center" padding="xxl">
          <Spinner size="large" />
          <Box variant="p" padding={{ top: 's' }}>Loading driver details...</Box>
        </Box>
      </Container>
    );
  }

  if (error) {
    return (
      <Container>
        <Alert type="error" header="Error loading driver">
          {error}
        </Alert>
      </Container>
    );
  }

  if (!driver) {
    return (
      <Container>
        <Alert type="warning" header="Driver not found">
          The requested driver could not be found.
        </Alert>
      </Container>
    );
  }

  return (
    <SpaceBetween size="l">
      <Container
        header={
          <Header
            variant="h1"
            description={`Driver ID: ${driver.driverId}`}
            actions={
              <Button variant="primary" iconName="refresh" onClick={fetchDriverData}>
                Refresh
              </Button>
            }
          >
            {driver.firstName} {driver.lastName}
          </Header>
        }
      >
        <ColumnLayout columns={4} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Status</Box>
            <StatusIndicator type={driver.status === 'active' ? 'success' : 'error'}>
              {driver.status}
            </StatusIndicator>
          </div>
          <div>
            <Box variant="awsui-key-label">Email</Box>
            <div>{driver.email}</div>
          </div>
          <div>
            <Box variant="awsui-key-label">Phone</Box>
            <div>{driver.phone}</div>
          </div>
          <div>
            <Box variant="awsui-key-label">License</Box>
            <div>{driver.licenseNumber}</div>
          </div>
        </ColumnLayout>
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
                  <Header variant="h2">Driver Statistics</Header>
                  
                  {stats && (
                    <ColumnLayout columns={3} variant="text-grid">
                      <div>
                        <Box variant="awsui-key-label">Total Trips</Box>
                        <div style={{ fontSize: '24px', fontWeight: 'bold' }}>{stats.totalTrips}</div>
                      </div>
                      <div>
                        <Box variant="awsui-key-label">Total Distance</Box>
                        <div style={{ fontSize: '24px', fontWeight: 'bold' }}>{stats.totalDistance} miles</div>
                      </div>
                      <div>
                        <Box variant="awsui-key-label">Total Duration</Box>
                        <div style={{ fontSize: '24px', fontWeight: 'bold' }}>{formatDuration(stats.totalDuration)}</div>
                      </div>
                      <div>
                        <Box variant="awsui-key-label">Average Driver Score</Box>
                        <div style={{ fontSize: '24px', fontWeight: 'bold' }}>
                          <Badge color={getScoreColor(stats.avgDriverScore)}>
                            {stats.avgDriverScore}/100
                          </Badge>
                        </div>
                      </div>
                      <div>
                        <Box variant="awsui-key-label">Safety Events</Box>
                        <div style={{ fontSize: '24px', fontWeight: 'bold', color: stats.safetyEventsCount > 0 ? '#d13212' : '#037f0c' }}>
                          {stats.safetyEventsCount}
                        </div>
                      </div>
                      <div>
                        <Box variant="awsui-key-label">Last Trip</Box>
                        <div style={{ fontSize: '18px', fontWeight: 'bold' }}>{stats.lastTripDate}</div>
                      </div>
                    </ColumnLayout>
                  )}
                </SpaceBetween>
              </Container>
            )
          },
          {
            id: 'trips',
            label: `Trips (${trips.length})`,
            content: (
              <Container>
                <Table
                  columnDefinitions={[
                    {
                      id: 'vehicleId',
                      header: 'Vehicle VIN',
                      cell: item => item.vin || item.vehicleId, // Use VIN if available, fallback to vehicleId
                      sortingField: 'vehicleId'
                    },
                    {
                      id: 'startTime',
                      header: 'Start Time',
                      cell: item => new Date(item.startTime).toLocaleString(),
                      sortingField: 'startTime'
                    },
                    {
                      id: 'duration',
                      header: 'Duration',
                      cell: item => formatDuration(item.duration || 0),
                      sortingField: 'duration'
                    },
                    {
                      id: 'distance',
                      header: 'Distance',
                      cell: item => `${(item.distance || 0).toFixed(1)} mi`,
                      sortingField: 'distance'
                    },
                    {
                      id: 'avgSpeed',
                      header: 'Avg Speed',
                      cell: item => `${(item.avgSpeed || 0).toFixed(1)} mph`,
                      sortingField: 'avgSpeed'
                    },
                    {
                      id: 'driverScore',
                      header: 'Score',
                      cell: item => {
                        const score = item.driverScore || 0;
                        return (
                          <Badge color={getScoreColor(score)}>
                            {score.toFixed(1)}
                          </Badge>
                        );
                      },
                      sortingField: 'driverScore'
                    },
                    {
                      id: 'actions',
                      header: '',
                      cell: item => (
                        <Button
                          variant="icon"
                          iconName="external"
                          ariaLabel="View trip details"
                          onClick={() => {
                            // Navigate to trip detail page
                            const encodedTripId = encodeURIComponent(item.tripId);
                            navigate(`/vehicles/management/${item.vehicleId}/trips/${encodedTripId}`);
                          }}
                        />
                      ),
                      width: 60
                    }
                  ]}
                  items={paginatedTrips}
                  loading={loading}
                  empty={
                    <Box textAlign="center" color="inherit">
                      <b>No trips found</b>
                      <Box padding={{ bottom: 's' }} variant="p" color="inherit">
                        No trips found for this driver.
                      </Box>
                    </Box>
                  }
                  filter={
                    <TextFilter
                      filteringText={tripsFilterText}
                      onChange={({ detail }) => setTripsFilterText(detail.filteringText)}
                      placeholder="Search trips..."
                    />
                  }
                  pagination={
                    <Pagination
                      currentPageIndex={tripsCurrentPage}
                      pagesCount={Math.ceil(filteredTrips.length / itemsPerPage)}
                      onChange={({ detail }) => setTripsCurrentPage(detail.currentPageIndex)}
                    />
                  }
                  sortingDisabled
                />
              </Container>
            )
          },
          {
            id: 'safety',
            label: `Safety Events (${safetyEvents.length})`,
            content: (
              <Container>
                <Table
                  columnDefinitions={[
                    {
                      id: 'timestamp',
                      header: 'Time',
                      cell: item => new Date(item.timestamp * 1000).toLocaleString(),
                      sortingField: 'timestamp'
                    },
                    {
                      id: 'eventType',
                      header: 'Event Type',
                      cell: item => item.eventType.replace(/_/g, ' '),
                      sortingField: 'eventType'
                    },
                    {
                      id: 'severity',
                      header: 'Severity',
                      cell: item => (
                        <Badge color={getSeverityColor(item.severity)}>
                          {item.severity}
                        </Badge>
                      ),
                      sortingField: 'severity'
                    },
                    {
                      id: 'vehicleId',
                      header: 'Vehicle',
                      cell: item => item.vehicleId,
                      sortingField: 'vehicleId'
                    },
                    {
                      id: 'tripId',
                      header: 'Trip ID',
                      cell: item => item.tripId,
                      sortingField: 'tripId'
                    },
                    {
                      id: 'description',
                      header: 'Description',
                      cell: item => item.description || 'N/A'
                    }
                  ]}
                  items={paginatedSafetyEvents}
                  loading={loading}
                  empty={
                    <Box textAlign="center" color="inherit">
                      <b>No safety events</b>
                      <Box padding={{ bottom: 's' }} variant="p" color="inherit">
                        No safety events found for this driver.
                      </Box>
                    </Box>
                  }
                  filter={
                    <TextFilter
                      filteringText={safetyFilterText}
                      onChange={({ detail }) => setSafetyFilterText(detail.filteringText)}
                      placeholder="Search safety events..."
                    />
                  }
                  pagination={
                    <Pagination
                      currentPageIndex={safetyCurrentPage}
                      pagesCount={Math.ceil(filteredSafetyEvents.length / itemsPerPage)}
                      onChange={({ detail }) => setSafetyCurrentPage(detail.currentPageIndex)}
                    />
                  }
                  sortingDisabled
                />
              </Container>
            )
          }
        ]}
      />
    </SpaceBetween>
  );
}
