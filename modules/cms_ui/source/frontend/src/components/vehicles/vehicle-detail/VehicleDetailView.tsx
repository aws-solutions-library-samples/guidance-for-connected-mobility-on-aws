// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect, useContext } from 'react';
import { getRuntimeConfig, getApiEndpoint } from '../../../config/api';
import { useAuth } from '../../../auth/useAuth';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Container,
  Header,
  ColumnLayout,
  Box,
  StatusIndicator,
  Tabs,
  Table,
  Button,
  Badge,
  SpaceBetween,
  Spinner,
  Alert,
  Pagination,
  ProgressBar,
  Popover,
  CollectionPreferences
} from '@cloudscape-design/components';
import { UserContext } from '../../commons/UserContext';
import { UI_ROUTES } from "../../../utils/constants";
import { RouteMapModal } from './RouteMapModal';
import { TripMap } from '../trip-detail/TripMap';
import TirePressureWidget from './TirePressureWidget';
import './vehicle-detail-tabs-borderless.css';

interface VehicleMetadata {
  vin: string;
  license_plate: string;
  licensePlate?: string;
  make: string;
  model: string;
  year: number;
  color: string;
  vehicle_type: string;
  vehicleType?: string;
  fleet_id: string;
  fleet_name: string;
  fleetId?: string;
  fleetName?: string;
  fuel_type: string;
  fuelType?: string;
  status: string;
  vehicleStatus?: string;
  connectionStatus: string;
  activityStatus: string;
  lastConnected: string | null;
  lastDisconnected: string | null;
  odometer: number;
  mileage?: number;
  fuel_level: number;
  fuelLevel?: number;
  battery_level: number;
  batteryLevel?: number;
  last_maintenance: string;
  lastMaintenance?: string;
  next_maintenance_due: string;
  nextMaintenanceDue?: string;
  insurance_expiry: string;
  insuranceExpiry?: string;
  registration_expiry: string;
  registrationExpiry?: string;
  driver_assigned: string;
  driverAssigned?: string;
  auto_registered: boolean;
  has_certificate?: boolean;
  last_updated: string;
  updatedAt?: string;
  createdAt?: string;
  location?: {
    latitude: number;
    longitude: number;
    address: string;
    last_updated: string;
  };
  lastKnownLocation?: {
    lat: number;
    lng: number;
  };
  currentLocation?: {
    latitude: number;
    longitude: number;
    address: string;
    lastUpdated: string;
  };
  calculatedOdometer?: number;
  calculatedOdometerKm?: number;
}

interface Trip {
  tripId: string;
  trip_id?: string;
  vehicleId: string;
  startTime: number;
  endTime?: number;
  duration: number;
  distance: number;
  totalLength?: number;
  driverName: string;
  assignedDriver?: string;
  driverScore: number;
  safetyEventsCount?: number;
  route?: Array<{
    lat: number | string;
    lng: number | string;
    timestamp?: number;
  }>;
}

interface SafetyEvent {
  eventId: string;
  tripId: string;
  vehicleId: string;
  timestamp: number;
  eventType: string;
  message: string;
  severity: string;
  speed?: number;
  latitude?: number;
  longitude?: number;
  lat?: number;
  lng?: number;
}

interface MaintenanceAlert {
  alertId: string;
  vehicleId: string;
  alertType: string;
  type?: string;
  severity: string;
  description: string;
  message?: string;
  dueDate?: string;
  scheduledDate?: string;
  status?: string;
}

const VehicleDetailView: React.FC = () => {
  console.log('VehicleDetailView - URL pathname:', window.location.pathname);
  
  const { vehicleId } = useParams<{ vehicleId: string }>();
  const navigate = useNavigate();
  const userContext = useContext(UserContext);
  const { getAuthHeaders } = useAuth();
  
  console.log('VehicleDetailView - URL pathname:', window.location.pathname);
  
  const [activeTab, setActiveTab] = useState("overview");
  
  // Data states
  const [vehicleData, setVehicleData] = useState<VehicleMetadata | null>(null);
  const [trips, setTrips] = useState<Trip[]>([]);
  const [tripsTotal, setTripsTotal] = useState(0);
  const [hasMoreTrips, setHasMoreTrips] = useState(false);
  const [tripsPagination, setTripsPagination] = useState({
    currentPage: 1,
    pageSize: 20,
    totalItems: 0,
    totalPages: 0,
    hasNextPage: false,
    hasPrevPage: false
  });
  const [safetyEvents, setSafetyEvents] = useState<SafetyEvent[]>([]);
  const [safetyEventsTotal, setSafetyEventsTotal] = useState(0);
  const [hasMoreSafetyEvents, setHasMoreSafetyEvents] = useState(false);
  const [safetyEventsPagination, setSafetyEventsPagination] = useState({
    currentPage: 1,
    pageSize: 20,
    totalItems: 0,
    totalPages: 0
  });
  const [maintenanceAlerts, setMaintenanceAlerts] = useState<MaintenanceAlert[]>([]);
  const [maintenanceAlertsTotal, setMaintenanceAlertsTotal] = useState(0);
  const [hasMoreMaintenanceAlerts, setHasMoreMaintenanceAlerts] = useState(false);
  const [maintenanceAlertsPagination, setMaintenanceAlertsPagination] = useState({
    currentPage: 1,
    pageSize: 20,
    totalItems: 0,
    totalPages: 0
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [debugData, setDebugData] = useState<any>(null);
  
  // Separate pagination state for each table
  const [tripsCurrentPage, setTripsCurrentPage] = useState(1);
  const [tripsPageSize, setTripsPageSize] = useState(10);
  const [safetyCurrentPage, setSafetyCurrentPage] = useState(1);
  const [safetyPageSize, setSafetyPageSize] = useState(10);
  const [maintenanceCurrentPage, setMaintenanceCurrentPage] = useState(1);
  const [maintenancePageSize, setMaintenancePageSize] = useState(10);
  
  // Last trip details for overview map
  const [lastTripDetails, setLastTripDetails] = useState<any>(null);
  const [loadingLastTrip, setLoadingLastTrip] = useState(false);
  
  // Latest telemetry data for tire pressure and other metrics
  const [latestTelemetry, setLatestTelemetry] = useState<any>(null);

  // Request deduplication
  const [ongoingRequests, setOngoingRequests] = useState<Set<string>>(new Set());

  // Derived data for recent activity table
  const recentActivity = [
    ...trips.slice(0, 3).map((trip: Trip) => ({
      type: 'Trip',
      date: new Date(trip.startTime * 1000).toLocaleDateString(),
      description: `${trip.driverName || trip.assignedDriver || 'Unknown Driver'} - ${(trip.distance || trip.totalLength || 0).toFixed(1)} km`
    })),
    ...safetyEvents.slice(0, 2).map((event: SafetyEvent) => ({
      type: 'Safety',
      date: new Date(event.timestamp * 1000).toLocaleDateString(),
      description: `${event.eventType || event.message} - ${event.severity}`
    })),
    ...maintenanceAlerts.slice(0, 2).map((alert: MaintenanceAlert) => ({
      type: 'Maintenance',
      date: alert.dueDate ? new Date(alert.dueDate).toLocaleDateString() : 'N/A',
      description: `${alert.alertType || alert.type} - ${alert.severity}`
    }))
  ];

  const isRequestInProgress = (requestKey: string): boolean => {
    return ongoingRequests.has(requestKey);
  };

  const markRequestStarted = (requestKey: string) => {
    setOngoingRequests(prev => new Set(prev).add(requestKey));
  };

  const markRequestCompleted = (requestKey: string) => {
    setOngoingRequests(prev => {
      const newSet = new Set(prev);
      newSet.delete(requestKey);
      return newSet;
    });
  };

  useEffect(() => {
    console.log(`🚗 VehicleDetailView useEffect running for vehicleId: ${vehicleId}`);
    if (vehicleId && vehicleId !== 'undefined') {
      console.log(`🚗 Calling consolidated fetch for vehicle ${vehicleId}`);
      fetchVehicleData();
    } else {
      console.warn('VIN is undefined or invalid:', vehicleId);
      setError('Invalid vehicle identifier');
      setLoading(false);
    }
  }, [vehicleId]);

  // Fetch last trip details when trips are loaded
  // Remove this useEffect since lastTrip details now come from vehicle response
  // useEffect(() => {
  //   if (trips.length > 0 && !lastTripDetails) {
  //     fetchLastTripDetails();
  //   }
  // }, [trips]);

  // Helper functions for pagination
  const getPaginatedTrips = () => {
    const startIndex = (tripsCurrentPage - 1) * tripsPageSize;
    const endIndex = startIndex + tripsPageSize;
    return trips.slice(startIndex, endIndex);
  };

  const getPaginatedSafetyEvents = () => {
    const startIndex = (safetyCurrentPage - 1) * safetyPageSize;
    const endIndex = startIndex + safetyPageSize;
    return safetyEvents.slice(startIndex, endIndex);
  };

  const getPaginatedMaintenanceAlerts = () => {
    const startIndex = (maintenanceCurrentPage - 1) * maintenancePageSize;
    const endIndex = startIndex + maintenancePageSize;
    return maintenanceAlerts.slice(startIndex, endIndex);
  };

  // Server-side pagination functions
  const fetchTripsPage = async (page: number) => {
    // For now, use client-side pagination since backend may not support it
    setTripsCurrentPage(page);
  };

  const fetchSafetyEventsPage = async (page: number) => {
    try {
      setLoading(true);
      const apiEndpoint = getRuntimeConfig().apiEndpoint;
      const response = await fetch(`${apiEndpoint}api/v1/vehicles/${vehicleId}/safety-events?page=${page}&limit=${safetyPageSize}`, {
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        }
      });
      
      if (response.ok) {
        const data = await response.json();
        setSafetyEvents(data.safetyEvents || []);
        setSafetyEventsTotal(data.total || 0);
        setSafetyCurrentPage(page);
      }
    } catch (error) {
      console.error('Error fetching safety events page:', error);
    } finally {
      setLoading(false);
    }
  };

  const fetchMaintenanceAlertsPage = async (page: number) => {
    // Use client-side pagination since backend returns mixed alert types
    setMaintenanceCurrentPage(page);
  };

  // Helper function to format coordinates as address
  const formatLocationAddress = (lat: number, lon: number) => {
    if (!lat || !lon) return 'Unknown location';
    return `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
  };

  const fetchVehicleData = async () => {
    if (!vehicleId || vehicleId === 'undefined') {
      setError('Invalid vehicle identifier');
      setLoading(false);
      return;
    }

    const requestKey = `vehicle-${vehicleId}`;
    
    if (isRequestInProgress(requestKey)) {
      console.log(`🔄 Vehicle data request for ${vehicleId} already in progress, skipping duplicate`);
      return;
    }

    try {
      markRequestStarted(requestKey);
      setLoading(true);
      setError(null);
      
      const runtimeConfig = getRuntimeConfig();
      const apiEndpoint = runtimeConfig.apiEndpoint;
      
      console.log(`🚗 Fetching vehicle data for vehicleId: ${vehicleId}`);
      
      const response = await fetch(`${apiEndpoint}api/v1/vehicles/${vehicleId}`, {
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        }
      });
      
      if (!response.ok) {
        throw new Error(`Failed to fetch vehicle: ${response.status} ${response.statusText}`);
      }
      
      const data = await response.json();
      console.log('🔍 Vehicle API Response:', data);
      
      // Handle consolidated response format
      if (data.vehicle) {
        // New consolidated format
        setVehicleData(data.vehicle);
        
        // Set trips data
        if (data.trips) {
          setTrips(data.trips.items || []);
          setTripsTotal(data.trips.total || 0);
          setHasMoreTrips(data.trips.hasMore || false);
        }
        
        // Set safety alerts data
        if (data.safetyAlerts) {
          console.log('🚨 Setting safety alerts:', data.safetyAlerts);
          setSafetyEvents(data.safetyAlerts.items || []);
          setSafetyEventsTotal(data.safetyAlerts.total || 0);
          setHasMoreSafetyEvents(data.safetyAlerts.hasMore || false);
        } else {
          console.log('🚨 No safety alerts in response');
        }
        
        // Set maintenance alerts data
        if (data.maintenanceAlerts) {
          console.log('🔧 Setting maintenance alerts:', data.maintenanceAlerts);
          // Get all maintenance alerts from the items array
          const alerts = data.maintenanceAlerts.items || data.maintenanceAlerts.alerts || [];
          console.log('🔧 Raw maintenance alerts:', alerts);
          // Don't filter by alertType since these are already maintenance alerts
          // Ensure we only show the page size limit (10 items)
          const paginatedMaintenance = alerts.slice(0, maintenancePageSize);
          setMaintenanceAlerts(paginatedMaintenance);
          setMaintenanceAlertsTotal(data.maintenanceAlerts.total || alerts.length);
          setHasMoreMaintenanceAlerts(data.maintenanceAlerts.hasMore || false);
        } else {
          console.log('🔧 No maintenance alerts in response');
        }
        
        // Set last trip details (includes route, safety events, maintenance events)
        if (data.lastTrip) {
          console.log('🗺️ Setting last trip details from vehicle response:', data.lastTrip);
          setLastTripDetails(data.lastTrip);
          setLoadingLastTrip(false);
        } else {
          console.log('🗺️ No last trip data in vehicle response');
          setLoadingLastTrip(false);
        }
        
        // Set latest telemetry data
        if (data.latestTelemetry) {
          console.log('🔧 Setting latest telemetry data:', data.latestTelemetry);
          setLatestTelemetry(data.latestTelemetry);
        } else {
          console.log('🔧 No telemetry data in vehicle response');
          setLatestTelemetry(null);
        }
        
      } else {
        // Fallback to old format
        let vehicle;
        if (data.vehicles && Array.isArray(data.vehicles)) {
          vehicle = data.vehicles.find((v: any) => v.vehicleId === vehicleId);
          if (!vehicle) {
            console.log(`❌ Vehicle with ID ${vehicleId} not found in vehicles array`);
            setError(`Vehicle with ID ${vehicleId} not found`);
            setLoading(false);
            markRequestCompleted(requestKey);
            return;
          }
        } else if (data.vehicleId || data.vin) {
          vehicle = data;
        } else {
          console.log(`❌ Vehicle with ID ${vehicleId} not found`);
          setError(`Vehicle with ID ${vehicleId} not found`);
          setLoading(false);
          markRequestCompleted(requestKey);
          return;
        }
        
        setVehicleData(vehicle);
      }
      
      console.log('✅ Vehicle data loaded successfully');
      setLoading(false);
      
    } catch (error) {
      console.error('❌ Error fetching vehicle data:', error);
      setError(error instanceof Error ? error.message : 'Failed to fetch vehicle data');
      setLoading(false);
    } finally {
      markRequestCompleted(requestKey);
    }
  };

  const handleViewTrip = (trip: Trip) => {
    const tripId = trip.tripId || trip.trip_id;
    console.log('🚗 Navigating to trip detail:', { tripId, vehicleId, fullTrip: trip });
    const encodedTripId = encodeURIComponent(tripId);
    const url = `${UI_ROUTES.VEHICLE_MANAGEMENT}/${vehicleId}/trips/${encodedTripId}`;
    console.log('🚗 Navigation URL:', url);
    navigate(url);
  };

  // fetchLastTripDetails function removed - lastTrip now comes from vehicle detail response

  // Stub pagination functions for now
  const fetchTrips = async (page: number = 1, pageSize: number = 20) => {
    console.log(`Pagination requested for trips: page ${page}, size ${pageSize}`);
  };

  const fetchSafetyEvents = async (page: number = 1, pageSize: number = 20) => {
    console.log(`Pagination requested for safety events: page ${page}, size ${pageSize}`);
  };

  const fetchMaintenanceAlerts = async (page: number = 1, pageSize: number = 20) => {
    console.log(`Pagination requested for maintenance: page ${page}, size ${pageSize}`);
  };

  if (loading) {
    return (
      <div style={{ padding: '20px', textAlign: 'center' }}>
        <Spinner size="large" />
        <Box variant="p" margin={{ top: 'm' }}>Loading vehicle details...</Box>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: '20px', textAlign: 'center' }}>
        <Alert type="error" header="Error loading vehicle">
          {error}
        </Alert>
      </div>
    );
  }

  if (!vehicleData) {
    return (
      <div style={{ padding: '20px', textAlign: 'center' }}>
        <Box variant="p">No vehicle data available</Box>
      </div>
    );
  }

  return (
    <Container>
      <SpaceBetween size="l">
        {/* Header */}
        <Header
          variant="h1"
        >
          Vehicle Details: {vehicleData?.vin || vehicleId}
        </Header>
          {/* Tabs */}
          <Tabs
            activeTabId={activeTab}
            onChange={({ detail }) => setActiveTab(detail.activeTabId)}
            tabs={[
                {
                  id: "overview",
                  label: "Overview",
                  content: (
                    <SpaceBetween size="l">
                      {/* Vehicle Details and Map in one container */}
                      <Container
                        header={<Header variant="h2">Vehicle Information</Header>}
                      >
                        <SpaceBetween size="l">
                          {/* Vehicle Overview */}
                          <ColumnLayout columns={4} variant="text-grid">
                            <div>
                              <Box variant="awsui-key-label">VIN</Box>
                              <div>{vehicleData.vin || vehicleId}</div>
                            </div>
                            <div>
                              <Box variant="awsui-key-label">Make & Model</Box>
                              <div>{vehicleData.make} {vehicleData.model} ({vehicleData.year})</div>
                            </div>
                            <div>
                              <Box variant="awsui-key-label">License Plate</Box>
                              <div>{vehicleData.license_plate || vehicleData.licensePlate || 'N/A'}</div>
                            </div>
                            <div>
                              <Box variant="awsui-key-label">Color</Box>
                              <div>{vehicleData.color || 'N/A'}</div>
                            </div>
                            <div>
                              <Box variant="awsui-key-label">Software Version</Box>
                              <div>{vehicleData.softwareVersion || vehicleData.software_version || 'v2.4.1'}</div>
                            </div>
                            <div>
                              <Box variant="awsui-key-label">Vehicle Type</Box>
                              <div>{vehicleData.vehicle_type || vehicleData.vehicleType || 'N/A'}</div>
                            </div>
                            <div>
                              <SpaceBetween direction="vertical" size="xs">
                                <SpaceBetween direction="horizontal" size="xs">
                                  <Box variant="awsui-key-label">Vehicle Status</Box>
                                  <Popover
                                    size="small"
                                    position="top"
                                    triggerType="custom"
                                    dismissButton={false}
                                    content={
                                      <div>
                                        <strong>Active:</strong> Registered & operational<br/>
                                        <strong>Inactive:</strong> Registered but not operational<br/>
                                        <strong>Not Registered:</strong> Vehicle exists in system but not yet fully registered for fleet operations
                                      </div>
                                    }
                                  >
                                    <Button variant="inline-icon" iconName="status-info" />
                                  </Popover>
                                </SpaceBetween>
                                <Badge color={vehicleData.status === 'active' ? 'green' : vehicleData.status === 'inactive' ? 'grey' : 'red'}>
                                  {vehicleData.status === 'active' ? 'Active' : vehicleData.status === 'inactive' ? 'Inactive' : vehicleData.status || vehicleData.vehicleStatus || 'Unknown'}
                                </Badge>
                              </SpaceBetween>
                            </div>
                            <div>
                              <SpaceBetween direction="vertical" size="xs">
                                <SpaceBetween direction="horizontal" size="xs">
                                  <Box variant="awsui-key-label">Connection Status</Box>
                                  <Popover
                                    size="small"
                                    position="top"
                                    triggerType="custom"
                                    dismissButton={false}
                                    content={
                                      <div>
                                        <strong>Connected:</strong> Vehicle is online & sending data<br/>
                                        <strong>Disconnected:</strong> Vehicle is offline or not sending data
                                      </div>
                                    }
                                  >
                                    <Button variant="inline-icon" iconName="status-info" />
                                  </Popover>
                                </SpaceBetween>
                                <Badge color={vehicleData.connectionStatus === 'connected' ? 'blue' : 'red'}>
                                  {vehicleData.connectionStatus === 'connected' ? 'Connected' : vehicleData.connectionStatus === 'disconnected' ? 'Disconnected' : vehicleData.connectionStatus || 'Unknown'}
                                </Badge>
                              </SpaceBetween>
                            </div>
                            <div>
                              <SpaceBetween direction="vertical" size="xs">
                                <SpaceBetween direction="horizontal" size="xs">
                                  <Box variant="awsui-key-label">Activity Status</Box>
                                  <Popover
                                    size="small"
                                    position="top"
                                    triggerType="custom"
                                    dismissButton={false}
                                    content={
                                      <div>
                                        <strong>Active:</strong> Currently in use or recently used<br/>
                                        <strong>Inactive:</strong> Not currently in use
                                      </div>
                                    }
                                  >
                                    <Button variant="inline-icon" iconName="status-info" />
                                  </Popover>
                                </SpaceBetween>
                                <Badge color={vehicleData.activityStatus === 'active' ? 'green' : 'grey'}>
                                  {vehicleData.activityStatus === 'active' ? 'Active' : vehicleData.activityStatus === 'inactive' ? 'Inactive' : vehicleData.activityStatus || 'Unknown'}
                                </Badge>
                              </SpaceBetween>
                            </div>
                            <div>
                              <SpaceBetween direction="horizontal" size="xs">
                                <Box variant="awsui-key-label">Fleet</Box>
                                <Popover
                                  size="small"
                                  position="top"
                                  triggerType="custom"
                                  dismissButton={false}
                                  content="The fleet group this vehicle is assigned to"
                                >
                                  <Button variant="inline-icon" iconName="status-info" />
                                </Popover>
                              </SpaceBetween>
                              <div>{vehicleData.fleet_name || vehicleData.fleetName || 'Unassigned'}</div>
                            </div>
                            <div>
                              <SpaceBetween direction="horizontal" size="xs">
                                <Box variant="awsui-key-label">Fuel Level</Box>
                                <Popover
                                  size="small"
                                  position="top"
                                  triggerType="custom"
                                  dismissButton={false}
                                  content="Current fuel tank level as a percentage"
                                >
                                  <Button variant="inline-icon" iconName="status-info" />
                                </Popover>
                              </SpaceBetween>
                              <div>{vehicleData.fuel_level || vehicleData.fuelLevel || 0}%</div>
                            </div>
                            <div>
                              <SpaceBetween direction="horizontal" size="xs">
                                <Box variant="awsui-key-label">Battery Level</Box>
                                <Popover
                                  size="small"
                                  position="top"
                                  triggerType="custom"
                                  dismissButton={false}
                                  content="Current battery charge level as a percentage"
                                >
                                  <Button variant="inline-icon" iconName="status-info" />
                                </Popover>
                              </SpaceBetween>
                              <div>{vehicleData.battery_level || vehicleData.batteryLevel || 0}%</div>
                            </div>
                            <div>
                              <SpaceBetween direction="horizontal" size="xs">
                                <Box variant="awsui-key-label">Driver Assigned</Box>
                                <Popover
                                  size="small"
                                  position="top"
                                  triggerType="custom"
                                  dismissButton={false}
                                  content="The driver currently assigned to this vehicle"
                                >
                                  <Button variant="inline-icon" iconName="status-info" />
                                </Popover>
                              </SpaceBetween>
                              <div>{vehicleData.driver_assigned || vehicleData.driverAssigned || 'Unassigned'}</div>
                            </div>
                            <div>
                              <SpaceBetween direction="vertical" size="xs">
                                <SpaceBetween direction="horizontal" size="xs">
                                  <Box variant="awsui-key-label">Auto Registration</Box>
                                  <Popover
                                    size="small"
                                    position="top"
                                    triggerType="custom"
                                    dismissButton={false}
                                    content={
                                      <div>
                                        <strong>Registered:</strong> Vehicle has valid certificates and is auto-registered<br/>
                                        <strong>Not Registered:</strong> Vehicle lacks proper certificates or registration
                                      </div>
                                    }
                                  >
                                    <Button variant="inline-icon" iconName="status-info" />
                                  </Popover>
                                </SpaceBetween>
                                <Badge color={vehicleData.auto_registered || vehicleData.has_certificate ? 'green' : 'red'}>
                                  {vehicleData.auto_registered || vehicleData.has_certificate ? 'Registered' : 'Not Registered'}
                                </Badge>
                              </SpaceBetween>
                            </div>
                            <div>
                              <Box variant="awsui-key-label">Current Location</Box>
                              <div>
                                {vehicleData.currentLocation ? (
                                  <div>
                                    <div>{vehicleData.currentLocation.address}</div>
                                    <Box variant="small" color="text-body-secondary">
                                      {vehicleData.currentLocation.latitude.toFixed(4)}, {vehicleData.currentLocation.longitude.toFixed(4)}
                                    </Box>
                                    <Box variant="small" color="text-body-secondary">
                                      Updated: {new Date(vehicleData.currentLocation.lastUpdated * 1000).toLocaleString()}
                                    </Box>
                                  </div>
                                ) : vehicleData.lastKnownLocation ? (
                                  <div>
                                    <Box variant="small" color="text-body-secondary">
                                      {vehicleData.lastKnownLocation.lat.toFixed(4)}, {vehicleData.lastKnownLocation.lng.toFixed(4)}
                                    </Box>
                                  </div>
                                ) : (
                                  'Unknown'
                                )}
                              </div>
                            </div>
                            <div>
                              <Box variant="awsui-key-label">Last Connected</Box>
                              <div>{vehicleData.lastConnected ? new Date(vehicleData.lastConnected).toLocaleString() : 'N/A'}</div>
                            </div>
                            <div>
                              <Box variant="awsui-key-label">Last Maintenance</Box>
                              <div>{vehicleData.last_maintenance || vehicleData.lastMaintenance || 'N/A'}</div>
                            </div>
                            <div>
                              <Box variant="awsui-key-label">Next Maintenance Due</Box>
                              <div>{vehicleData.next_maintenance_due || vehicleData.nextMaintenanceDue || 'N/A'}</div>
                            </div>
                            <div>
                              <Box variant="awsui-key-label">Insurance Expiry</Box>
                              <div>{vehicleData.insurance_expiry || vehicleData.insuranceExpiry || 'N/A'}</div>
                            </div>
                            <div>
                              <Box variant="awsui-key-label">Registration Expiry</Box>
                              <div>{vehicleData.registration_expiry || vehicleData.registrationExpiry || 'N/A'}</div>
                            </div>
                            <div>
                              <Box variant="awsui-key-label">Last Updated</Box>
                              <div>{vehicleData.last_updated ? new Date(vehicleData.last_updated).toLocaleString() : vehicleData.updatedAt ? new Date(vehicleData.updatedAt).toLocaleString() : 'N/A'}</div>
                            </div>
                          </ColumnLayout>

                          {/* Vehicle Map */}
                          {(vehicleData.currentLocation || vehicleData.lastKnownLocation) && (
                            <div>
                              <Box variant="h3" margin={{ bottom: 's' }}>Vehicle Location</Box>
                              <TripMap
                                route={[]}
                                startLocation={
                                  vehicleData.currentLocation 
                                    ? { lat: vehicleData.currentLocation.latitude, lng: vehicleData.currentLocation.longitude, address: vehicleData.currentLocation.address }
                                    : vehicleData.lastKnownLocation 
                                      ? { lat: vehicleData.lastKnownLocation.lat, lng: vehicleData.lastKnownLocation.lng }
                                      : undefined
                                }
                                vehicleType={vehicleData.vehicleType || vehicleData.vehicle_type || 'Sedan'}
                                safetyEvents={safetyEvents.map(event => ({
                                  latitude: event.latitude || event.lat || 0,
                                  longitude: event.longitude || event.lng || 0,
                                  eventType: event.eventType || event.message || 'Unknown',
                                  timestamp: event.timestamp,
                                  severity: event.severity
                                }))}
                                height="400px"
                              />
                              <Box margin={{ top: 's' }} variant="small" color="text-body-secondary">
                                {vehicleData.currentLocation ? (
                                  `Current location: ${vehicleData.currentLocation.address} (Updated: ${new Date(vehicleData.currentLocation.lastUpdated * 1000).toLocaleString()})`
                                ) : (
                                  `Last known location: ${vehicleData.lastKnownLocation!.lat.toFixed(4)}, ${vehicleData.lastKnownLocation!.lng.toFixed(4)}`
                                )}
                              </Box>
                            </div>
                          )}
                        </SpaceBetween>
                      </Container>

                      {/* Key Metrics */}
                      <Container
                        header={<Header variant="h2">Key Metrics</Header>}
                      >
                        <ColumnLayout columns={4} variant="text-grid">
                          <div>
                            <Box variant="awsui-key-label">Total Trips</Box>
                            <div>{tripsTotal}</div>
                          </div>
                          <div>
                            <Box variant="awsui-key-label">Total Distance</Box>
                            <div>{vehicleData.calculatedOdometerKm ? `${vehicleData.calculatedOdometerKm} km` : 'N/A'}</div>
                          </div>
                          <div>
                            <Box variant="awsui-key-label">Safety Events</Box>
                            <div>{safetyEventsTotal}</div>
                          </div>
                          <div>
                            <Box variant="awsui-key-label">Maintenance Alerts</Box>
                            <div>{maintenanceAlertsTotal}</div>
                          </div>
                        </ColumnLayout>
                      </Container>

                      {/* Vehicle Location, Tire Pressure, and Recent Activity */}
                      <ColumnLayout columns={3} variant="text-grid">
                        {/* Tire Pressure Monitor */}
                        <div>
                          {latestTelemetry && (latestTelemetry.tire_fl || latestTelemetry.tire_fr || latestTelemetry.tire_rl || latestTelemetry.tire_rr) ? (
                            <TirePressureWidget
                              tirePressure={{
                                tire_fl: latestTelemetry.tire_fl,
                                tire_fr: latestTelemetry.tire_fr,
                                tire_rl: latestTelemetry.tire_rl,
                                tire_rr: latestTelemetry.tire_rr,
                                tire_temp_max: latestTelemetry.tire_temp_max
                              }}
                              lastUpdated={latestTelemetry.timestamp ? new Date(latestTelemetry.timestamp * 1000).toISOString() : undefined}
                            />
                          ) : (
                            <Container
                              header={<Header variant="h3">Tire Pressure Monitor</Header>}
                            >
                              <Box textAlign="center" padding="xl" color="text-body-secondary">
                                <Box variant="strong" color="inherit">No tire pressure data</Box>
                                <Box variant="p" color="inherit">
                                  Tire pressure readings will appear here when telemetry data is available.
                                </Box>
                              </Box>
                            </Container>
                          )}
                        </div>

                        {/* Last Trip Map */}
                        <Container
                          header={<Header variant="h3">Last Trip</Header>}
                        >
                          {loadingLastTrip ? (
                            <Box textAlign="center" padding="xl">
                              <Spinner size="normal" />
                              <Box variant="p" color="text-body-secondary">Loading trip details...</Box>
                            </Box>
                          ) : trips.length > 0 && lastTripDetails ? (
                            <SpaceBetween size="m">
                              <TripMap
                                route={lastTripDetails.route || []}
                                startLocation={
                                  lastTripDetails.startLocation || 
                                  (lastTripDetails.route && lastTripDetails.route.length > 0 
                                    ? { lat: parseFloat(lastTripDetails.route[0].lat), lng: parseFloat(lastTripDetails.route[0].lng) }
                                    : undefined)
                                }
                                endLocation={
                                  lastTripDetails.endLocation || 
                                  (lastTripDetails.route && lastTripDetails.route.length > 0 
                                    ? { lat: parseFloat(lastTripDetails.route[lastTripDetails.route.length - 1].lat), lng: parseFloat(lastTripDetails.route[lastTripDetails.route.length - 1].lng) }
                                    : undefined)
                                }
                                showStartEndMarkers={true}
                                safetyEvents={safetyEvents.filter(event => 
                                  event.tripId === trips[0].tripId || event.tripId === trips[0].trip_id
                                ).map(event => ({
                                  latitude: event.latitude || event.lat || 0,
                                  longitude: event.longitude || event.lng || (lastTripDetails.route && lastTripDetails.route.length > 0 ? parseFloat(lastTripDetails.route[0].lng) : -74.0),
                                  eventType: event.eventType || event.message || 'Unknown',
                                  timestamp: event.timestamp,
                                  severity: event.severity
                                }))}
                                height="300px"
                              />
                              <Box variant="small" color="text-body-secondary">
                                {new Date(trips[0].startTime * 1000).toLocaleDateString()} - 
                                {trips[0].driverName || trips[0].assignedDriver || 'Unknown Driver'} - 
                                {(trips[0].distance || trips[0].totalLength || 0).toFixed(1)} km
                              </Box>
                            </SpaceBetween>
                          ) : trips.length > 0 ? (
                            <Box textAlign="center" padding="xl" color="text-body-secondary">
                              <Box variant="strong" color="inherit">Trip route not available</Box>
                              <Box variant="p" color="inherit">
                                Route data for the last trip could not be loaded.
                              </Box>
                            </Box>
                          ) : (
                            <Box textAlign="center" padding="xl" color="text-body-secondary">
                              <Box variant="strong" color="inherit">No trips available</Box>
                              <Box variant="p" color="inherit">
                                Trip routes will appear here when trips are recorded.
                              </Box>
                            </Box>
                          )}
                        </Container>

                        {/* Recent Activity Table */}
                        <Container
                          header={<Header variant="h3">Recent Activity</Header>}
                        >
                          <Table
                            variant="container"
                            items={recentActivity}
                            header={
                              <Header
                                counter={`(${recentActivity.length})`}
                                description="Recent vehicle activity"
                              >
                                Recent Activity
                              </Header>
                            }
                            columnDefinitions={[
                              {
                                id: "type",
                                header: "Type",
                                cell: (item: any) => (
                                  <Badge color={item.type === 'Trip' ? 'blue' : item.type === 'Safety' ? 'red' : 'grey'}>
                                    {item.type}
                                  </Badge>
                                )
                              },
                              {
                                id: "date",
                                header: "Date",
                                cell: (item: any) => item.date
                              },
                              {
                                id: "description",
                                header: "Description",
                                cell: (item: any) => item.description
                              }
                            ]}
                            empty={
                              <Box textAlign="center" color="inherit">
                                <Box variant="strong" textAlign="center" color="inherit">
                                  No recent activity
                                </Box>
                              </Box>
                            }
                          />
                        </Container>
                      </ColumnLayout>
                    </SpaceBetween>
                  )
                },
                {
                  id: "trips",
                  label: `Trips (${tripsTotal})`,
                  content: (
                    <Container>
                      <SpaceBetween size="s">
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'nowrap', minHeight: '40px' }}>
                          <Header variant="h2" counter={`(${getPaginatedTrips().length} of ${tripsTotal} total)`}>
                            Trips
                          </Header>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
                            <CollectionPreferences
                              title="Preferences"
                              confirmLabel="Confirm"
                              cancelLabel="Cancel"
                              preferences={{
                                pageSize: tripsPageSize
                              }}
                              pageSizePreference={{
                                title: "Page size",
                                options: [
                                  { value: 10, label: "10 items" },
                                  { value: 20, label: "20 items" },
                                  { value: 50, label: "50 items" }
                                ]
                              }}
                            />
                            <Pagination
                              currentPageIndex={tripsCurrentPage}
                              pagesCount={Math.ceil(tripsTotal / tripsPageSize)}
                              onChange={({ detail }) => fetchTripsPage(detail.currentPageIndex)}
                            />
                          </div>
                        </div>
                        <Table
                      loading={loading}
                      loadingText="Loading trips..."
                      enableKeyboardNavigation={true}
                      sortingDisabled={false}
                      items={getPaginatedTrips()}
                      columnDefinitions={[
                        {
                          id: "startTime",
                          header: "Start Time",
                          sortingField: "startTime",
                          cell: (trip: Trip) => {
                            const startTime = trip.startTime;
                            if (!startTime) return 'N/A';
                            return new Date(startTime * 1000).toLocaleString();
                          }
                        },
                        {
                          id: "endTime",
                          header: "End Time", 
                          sortingField: "endTime",
                          cell: (trip: Trip) => {
                            const endTime = trip.endTime;
                            if (!endTime) return 'Active';
                            return new Date(endTime * 1000).toLocaleString();
                          }
                        },
                        {
                          id: "duration",
                          header: "Duration",
                          cell: (trip: Trip) => {
                            const duration = trip.duration || 0;
                            const hours = Math.floor(duration / 3600);
                            const minutes = Math.floor((duration % 3600) / 60);
                            return `${hours}h ${minutes}m`;
                          }
                        },
                        {
                          id: "distance",
                          header: "Distance",
                          cell: (trip: Trip) => `${(trip.distance || trip.totalLength || 0).toFixed(1)} km`
                        },
                        {
                          id: "driver",
                          header: "Driver",
                          cell: (trip: Trip) => trip.driverName || trip.assignedDriver || 'Unknown'
                        },
                        {
                          id: "driverScore",
                          header: "Driver Score",
                          cell: (trip: Trip) => trip.driverScore || 0
                        },
                        {
                          id: "safetyEvents",
                          header: "Safety Events",
                          cell: (trip: Trip) => trip.safetyEventsCount || 0
                        },
                        {
                          id: "actions",
                          header: "Actions",
                          cell: (trip: Trip) => (
                            <Button variant="icon" iconName="external" ariaLabel="View trip details" onClick={() => handleViewTrip(trip)} />
                          )
                        }
                      ]}
                      variant="full-page"
                      stickyHeader={true}
                      empty={
                        <Box textAlign="center" color="inherit">
                          <Box variant="strong" textAlign="center" color="inherit">
                            No trips
                          </Box>
                          <Box variant="p" padding={{ bottom: "s" }} color="inherit">
                            No trips recorded for this vehicle.
                          </Box>
                        </Box>
                      }
                    />
                      </SpaceBetween>
                    </Container>
                  )
                },
                {
                  id: "safety",
                  label: `Safety Events (${safetyEventsTotal})`,
                  content: (
                    <Container>
                      <SpaceBetween size="s">
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'nowrap', minHeight: '40px' }}>
                          <Header variant="h2" counter={`(${safetyEvents.length} of ${safetyEventsTotal} total)`}>
                            Safety Events
                          </Header>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
                            <CollectionPreferences
                              title="Preferences"
                              confirmLabel="Confirm"
                              cancelLabel="Cancel"
                              preferences={{
                                pageSize: safetyPageSize
                              }}
                              pageSizePreference={{
                                title: "Page size",
                                options: [
                                  { value: 10, label: "10 items" },
                                  { value: 20, label: "20 items" },
                                  { value: 50, label: "50 items" }
                                ]
                              }}
                            />
                            <Pagination
                              currentPageIndex={safetyCurrentPage}
                              pagesCount={Math.ceil(safetyEventsTotal / safetyPageSize)}
                              onChange={({ detail }) => fetchSafetyEventsPage(detail.currentPageIndex)}
                            />
                          </div>
                        </div>
                        <Table
                      loading={loading}
                      loadingText="Loading safety events..."
                      enableKeyboardNavigation={true}
                      sortingDisabled={false}
                      items={safetyEvents}
                      columnDefinitions={[
                        {
                          id: "timestamp",
                          header: "Time",
                          cell: (event: SafetyEvent) => {
                            const timestamp = event.timestamp;
                            if (!timestamp) return 'N/A';
                            return new Date(timestamp * 1000).toLocaleString();
                          }
                        },
                        {
                          id: "eventType",
                          header: "Event Type",
                          cell: (event: SafetyEvent) => event.eventType || event.message || 'Unknown'
                        },
                        {
                          id: "severity",
                          header: "Severity",
                          cell: (event: SafetyEvent) => (
                            <Badge color={event.severity === 'HIGH' ? 'red' : event.severity === 'MEDIUM' ? 'blue' : 'grey'}>
                              {event.severity || 'Unknown'}
                            </Badge>
                          )
                        },
                        {
                          id: "speed",
                          header: "Speed",
                          cell: (event: SafetyEvent) => event.speed ? `${event.speed} mph` : 'N/A'
                        },
                        {
                          id: "location",
                          header: "Location",
                          cell: (event: SafetyEvent) => {
                            const lat = event.latitude || event.lat;
                            const lng = event.longitude || event.lng;
                            if (lat && lng) {
                              return `${parseFloat(lat.toString()).toFixed(4)}, ${parseFloat(lng.toString()).toFixed(4)}`;
                            }
                            return 'N/A';
                          }
                        }
                      ]}
                      variant="full-page"
                      stickyHeader={true}
                      empty={
                        <Box textAlign="center" color="inherit">
                          <Box variant="strong" textAlign="center" color="inherit">
                            No safety events
                          </Box>
                          <Box variant="p" padding={{ bottom: "s" }} color="inherit">
                            No safety events recorded for this vehicle.
                          </Box>
                        </Box>
                      }
                    />
                      </SpaceBetween>
                    </Container>
                  )
                },
                {
                  id: "maintenance",
                  label: `Maintenance (${maintenanceAlertsTotal})`,
                  content: (
                    <Container>
                      <SpaceBetween size="s">
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'nowrap', minHeight: '40px' }}>
                          <Header variant="h2" counter={`(${maintenanceAlerts.length} of ${maintenanceAlertsTotal} total)`}>
                            Maintenance
                          </Header>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
                            <CollectionPreferences
                              title="Preferences"
                              confirmLabel="Confirm"
                              cancelLabel="Cancel"
                              preferences={{
                                pageSize: maintenancePageSize
                              }}
                              pageSizePreference={{
                                title: "Page size",
                                options: [
                                  { value: 10, label: "10 items" },
                                  { value: 20, label: "20 items" },
                                  { value: 50, label: "50 items" }
                                ]
                              }}
                            />
                            <Pagination
                              currentPageIndex={maintenanceCurrentPage}
                              pagesCount={Math.ceil(maintenanceAlertsTotal / maintenancePageSize)}
                              onChange={({ detail }) => fetchMaintenanceAlertsPage(detail.currentPageIndex)}
                            />
                          </div>
                        </div>
                        <Table
                      loading={loading}
                      loadingText="Loading maintenance alerts..."
                      enableKeyboardNavigation={true}
                      sortingDisabled={false}
                      items={maintenanceAlerts}
                      columnDefinitions={[
                        {
                          id: "alertType",
                          header: "Alert Type",
                          cell: (alert: MaintenanceAlert) => alert.alertType || alert.type || 'Unknown'
                        },
                        {
                          id: "severity",
                          header: "Severity",
                          cell: (alert: MaintenanceAlert) => (
                            <Badge color={alert.severity === 'HIGH' ? 'red' : alert.severity === 'MEDIUM' ? 'blue' : 'grey'}>
                              {alert.severity || 'Unknown'}
                            </Badge>
                          )
                        },
                        {
                          id: "description",
                          header: "Description",
                          cell: (alert: MaintenanceAlert) => alert.description || alert.message || 'N/A'
                        },
                        {
                          id: "dueDate",
                          header: "Due Date",
                          cell: (alert: MaintenanceAlert) => {
                            const dueDate = alert.dueDate || alert.scheduledDate;
                            return dueDate ? new Date(dueDate).toLocaleDateString() : 'N/A';
                          }
                        },
                        {
                          id: "status",
                          header: "Status",
                          cell: (alert: MaintenanceAlert) => (
                            <StatusIndicator type={alert.status === 'completed' ? 'success' : 'pending'}>
                              {alert.status || 'Pending'}
                            </StatusIndicator>
                          )
                        }
                      ]}
                      variant="full-page"
                      stickyHeader={true}
                      empty={
                        <Box textAlign="center" color="inherit">
                          <Box variant="strong" textAlign="center" color="inherit">
                            No maintenance alerts
                          </Box>
                          <Box variant="p" padding={{ bottom: "s" }} color="inherit">
                            No maintenance alerts recorded for this vehicle.
                          </Box>
                        </Box>
                      }
                    />
                      </SpaceBetween>
                    </Container>
                  )
                }
              ]}
            />
      </SpaceBetween>
    </Container>
  );
};

export default VehicleDetailView;
