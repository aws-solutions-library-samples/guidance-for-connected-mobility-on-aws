// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect, useRef } from 'react';
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
  Spinner,
  Modal,
  Icon
} from '@cloudscape-design/components';
import { getRuntimeConfig } from '../../config/api';
import { useAuth } from '../../auth/useAuth';
import { SafetyEventsTable } from '../commons/SafetyEventsTable';
import { SafetyEventLocationModal } from '../commons/SafetyEventLocationModal';
import { TripsTable } from '../commons/TripsTable';
import { useVehicle } from '../../contexts/VehicleContext';
import { DriverAccountPanel } from './DriverAccountPanel';
import { DriverAssignmentPanel } from './DriverAssignmentPanel';

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
  assignedVehicleId?: string | null;
  createdAt: string;
  updatedAt: string;
  // Behavior-derived safety score (2026-05-05). Computed server-side
  // in the /api/v1/drivers/{id} Lambda from the current vehicle's
  // safety-event rate per 1000 miles. When `safetyScoreSource` is
  // 'events-derived-*' this reflects actual driving behavior;
  // 'seeded' means the driver has too few miles yet so the Lambda
  // fell back to the static seed value. Both are integers 0-100.
  safetyScore?: number;
  safetyScoreSource?: string;
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
  vin?: string;
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
  const { setDriverName } = useVehicle();
  const { getIdToken } = useAuth();
  const [driver, setDriver] = useState<Driver | null>(null);
  const [stats, setStats] = useState<DriverStats | null>(null);
  const [trips, setTrips] = useState<Trip[]>([]);
  const [tripsCount, setTripsCount] = useState(0);
  const [safetyEvents, setSafetyEvents] = useState<SafetyEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState('overview');
  const [vehicleVinMap, setVehicleVinMap] = useState<Record<string, string>>({});
  const [locationModalVisible, setLocationModalVisible] = useState(false);
  const [selectedEventLocation, setSelectedEventLocation] = useState<{latitude: number, longitude: number} | null>(null);
  const [selectedEventDetails, setSelectedEventDetails] = useState<SafetyEvent | null>(null);

  // Pagination and filtering
  const itemsPerPage = 10;

  const handleLocationClick = (location: {latitude: number, longitude: number}, eventDetails?: SafetyEvent) => {
    setSelectedEventLocation(location);
    setSelectedEventDetails(eventDetails || null);
    setLocationModalVisible(true);
  };

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
      const driver = driverData.driver || driverData;
      setDriver(driver);
      
      // Set driver name in context for breadcrumbs
      const fullName = `${driver.firstName || ''} ${driver.lastName || ''}`.trim();
      console.log('👤 Setting driver name in context:', fullName);
      setDriverName(fullName || null);

      // Fetch trips for this driver.
      // Limit bumped 500 → 1000 (2026-05-04) because the driver-trips
      // endpoint's per-row cost was previously dominated by N+1 DDB
      // GetItems; after the BatchGetItem fix earlier today, pulling
      // 1000 rows comfortably completes in ~400ms. This keeps the
      // summary aggregates (distance/duration/avg score) accurate for
      // high-volume drivers like DRV-0010 (616 trips).
      let tripsData = { trips: [] };
      const tripsResponse = await fetch(`${apiEndpoint}api/v1/drivers/${driverId}/trips?limit=1000`);
      if (tripsResponse.ok) {
        tripsData = await tripsResponse.json();
        setTrips(tripsData.trips || tripsData.items || []);
        setTripsCount(tripsData.totalCount || tripsData.trips?.length || 0);
      }

      // Fetch safety events for this driver
      let safetyData = { events: [] };
      const safetyResponse = await fetch(`${apiEndpoint}api/v1/safety-events?driverId=${driverId}&limit=500`);
      if (safetyResponse.ok) {
        safetyData = await safetyResponse.json();
        setSafetyEvents(safetyData.events || safetyData.items || []);
      }

      // Build VIN map from trip data (VIN is already in the response!)
      const vinMap: Record<string, string> = {};
      (tripsData.trips || []).forEach((trip: Trip) => {
        if (trip.vehicleId && trip.vin) {
          vinMap[trip.vehicleId] = trip.vin;
        }
      });
      setVehicleVinMap(vinMap);

      // Calculate stats. Pass the authoritative totalCount from the API
      // so the "Total Trips" stat matches the Trips tab badge count. The
      // other aggregates (distance, duration, avg score) still reflect
      // only the loaded trips window — increasing the limit below to
      // 1000 means this window covers ~99% of drivers.
      calculateStats(
        tripsData?.trips || [],
        safetyData?.events || [],
        tripsData?.totalCount,
      );

    } catch (err) {
      console.error('Error fetching driver data:', err);
      setError(err instanceof Error ? err.message : 'Failed to load driver data');
    } finally {
      setLoading(false);
    }
  };

  const calculateStats = (tripsData: Trip[], safetyData: SafetyEvent[], totalTripsCount?: number) => {
    // `totalTripsCount` is the authoritative total from the API (respects
    // all pages, not just what the /trips?limit=N call returned). When it
    // isn't passed we fall back to the loaded-rows count so the stats
    // still render — but the stats card will understate the total when
    // the driver has more trips than we loaded. See DRV-0010 at 616
    // trips with a 1000-row load window.
    const effectiveTotal = typeof totalTripsCount === 'number' && totalTripsCount > 0
      ? totalTripsCount
      : tripsData.length;
    if (tripsData.length === 0) {
      setStats({
        totalTrips: effectiveTotal,
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
      totalTrips: effectiveTotal,
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

      {driverId && <DriverAccountPanel driverId={driverId} getIdToken={getIdToken} />}

      {driverId && driver && (
        <DriverAssignmentPanel
          driverId={driverId}
          driverFleetId={driver.fleetId}
          assignedVehicleId={driver.assignedVehicleId ?? null}
          getIdToken={getIdToken}
          onAssignmentChanged={() => fetchDriverData()}
        />
      )}

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
                        <Box variant="awsui-key-label">Safety Score</Box>
                        <div style={{ fontSize: '24px', fontWeight: 'bold' }}>
                          {typeof driver?.safetyScore === 'number' ? (
                            <>
                              <Badge color={getScoreColor(driver.safetyScore)}>
                                {driver.safetyScore}/100
                              </Badge>
                              {driver?.safetyScoreSource?.startsWith('events-derived') && (
                                <span style={{ fontSize: '11px', fontWeight: 'normal', color: '#5f6b7a', marginLeft: 8 }}>
                                  from safety events
                                </span>
                              )}
                              {driver?.safetyScoreSource === 'seeded' && (
                                <span style={{ fontSize: '11px', fontWeight: 'normal', color: '#5f6b7a', marginLeft: 8 }}>
                                  insufficient mileage — using seeded value
                                </span>
                              )}
                            </>
                          ) : (
                            <Badge color="grey">—</Badge>
                          )}
                        </div>
                      </div>
                      <div>
                        <Box variant="awsui-key-label">Average Trip Score</Box>
                        <div style={{ fontSize: '24px', fontWeight: 'bold' }}>
                          <Badge color={getScoreColor(stats.avgDriverScore)}>
                            {stats.avgDriverScore}/100
                          </Badge>
                          <span style={{ fontSize: '11px', fontWeight: 'normal', color: '#5f6b7a', marginLeft: 8 }}>
                            average of last {stats.totalTrips} trips
                          </span>
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
            label: `Trips (${tripsCount})`,
            content: (
              <TripsTable 
                driverId={driverId}
                showVehicleColumn={true}
                showDriverColumn={false}
                vehicleVinMap={vehicleVinMap}
                totalTripsCount={tripsCount}
                onTotalCountChange={setTripsCount}
              />
            )
          },
          {
            id: 'safety',
            label: `Safety Events (${safetyEvents.length})`,
            content: (
              <SafetyEventsTable
                driverId={driverId}
                onLocationClick={handleLocationClick}
                showVehicleColumn={true}
                showDriverColumn={false}
                showTripColumn={false}
                vehicleVinMap={vehicleVinMap}
                totalEventsCount={safetyEvents.length}
              />
            )
          }
        ]}
      />

      {/* Location Modal */}
      {selectedEventLocation && (
        <SafetyEventLocationModal
          visible={locationModalVisible}
          onDismiss={() => setLocationModalVisible(false)}
          eventLocation={selectedEventLocation}
          eventDetails={selectedEventDetails ? {
            eventType: selectedEventDetails.eventType,
            severity: selectedEventDetails.severity,
            vehicleId: selectedEventDetails.vehicleId,
            timestamp: selectedEventDetails.timestamp,
            description: selectedEventDetails.description
          } : undefined}
          vehicleVinMap={vehicleVinMap}
        />
      )}
    </SpaceBetween>
  );
}
