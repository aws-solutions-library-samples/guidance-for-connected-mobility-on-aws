// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect, useContext } from 'react';
import { getRuntimeConfig } from '../../../config/api';
import {
  SplitPanel,
  Container,
  Header,
  SpaceBetween,
  Button,
  Select,
  Box,
  StatusIndicator,
  Badge,
  FormField,
  Toggle,
  ColumnLayout,
  Alert
} from '@cloudscape-design/components';
import { useNavigate } from "react-router-dom";
import { useLocalStorage } from "../../commons/use-local-storage";
import { AlertsFleetFilter, useAlertsFleetFilter } from '../../commons/AlertsFleetFilter';
import { DashboardHeader } from '../../alerts/header';
import { useAuth } from "../../../auth/useAuth";
import { getRealtimeApiClient } from "../../../services/RealtimeDataService";

// MapLibre GL JS with React Map GL (works with both OpenStreetMap and Amazon Location Services)
import Map, { NavigationControl, Marker, Popup } from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';

interface Vehicle {
  vin: string;
  fleet_id: string;
  fleet_name: string;
  telemetry_fleet_id: string;
  location: { lat: number; lon: number };
  status: 'CONNECTED' | 'OFFLINE' | 'MAINTENANCE';
  speed: number;
  fuel_level: number;
  driver_score: number;
  auto_registered: boolean;
  last_update: string;
  make: string;
  model: string;
  maintenance_status: 'none' | 'engine_light' | 'critical';
  in_trip: boolean;
}

const splitPanelMaxSize = 360;

export default function FleetVehicleMapView() {
  const navigate = useNavigate();
  const { getAuthHeaders } = useAuth();
  const auth = useAuth();
  
  const [toolsOpen, setToolsOpen] = useLocalStorage("Fleet-Map-Tools-Open", false);
  const [splitPanelOpen, setSplitPanelOpen] = useLocalStorage("Fleet-Map-Split-Panel-Open", false);
  const [splitPanelSize, setSplitPanelSize] = useLocalStorage("Fleet-Map-Split-Panel-Size", splitPanelMaxSize);
  
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [totalVehicleCount, setTotalVehicleCount] = useState(0);
  const [vehiclesWithLocations, setVehiclesWithLocations] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedVehicle, setSelectedVehicle] = useState<Vehicle | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  // Use the new fleet filter hook for consistent filtering
  const {
    selectedFleet,
    selectedFleetName,
    handleFleetChange,
    isAllFleets
  } = useAlertsFleetFilter();

  // Time range filter state
  const [selectedTimeRange, setSelectedTimeRange] = useState('24h');
  
  const timeRangeOptions = [
    { label: 'Last hour', value: '1h' },
    { label: 'Last 6 hours', value: '6h' },
    { label: 'Last 24 hours', value: '24h' },
    { label: 'Last 3 days', value: '3d' },
    { label: 'Last 7 days', value: '7d' },
    { label: 'Last 30 days', value: '30d' },
  ];

  // Dynamic map viewport - starts with a default view and adjusts to vehicle locations
  const [viewState, setViewState] = useState({
    longitude: -95.7129, // Center of US
    latitude: 37.0902,
    zoom: 4
  });

  // OpenStreetMap style (ready for Amazon Location Services upgrade)
  const mapStyle = {
    version: 8,
    sources: {
      'osm-tiles': {
        type: 'raster' as const,
        tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
        tileSize: 256,
        attribution: '© OpenStreetMap contributors | Ready for Amazon Location Services'
      }
    },
    layers: [
      {
        id: 'osm-tiles',
        type: 'raster' as const,
        source: 'osm-tiles'
      }
    ]
  };

  // Fetch vehicle data from optimized locations API
  const fetchVehicleData = async () => {
    try {
      setRefreshing(true);
      setError(null);
      
      const runtimeConfig = getRuntimeConfig();
      const apiEndpoint = runtimeConfig.apiEndpoint;
      
      console.log('🚗 Fetching vehicle locations from:', apiEndpoint);
      
      // Use optimized locations endpoint
      const vehiclesResponse = await fetch(`${apiEndpoint}/api/v1/vehicles/locations`);
      if (!vehiclesResponse.ok) {
        throw new Error(`HTTP error! status: ${vehiclesResponse.status}`);
      }
      
      const vehiclesResult = await vehiclesResponse.json();
      console.log('🔍 Number of vehicles returned:', vehiclesResult.vehicles?.length || 0);

      // Transform vehicles with real locations from optimized API
      const transformedVehicles: Vehicle[] = (vehiclesResult.vehicles || []).map((v: any) => {
        // Only include vehicles with actual location data for map display
        if (!v.hasLocation || !v.lat || !v.lng) {
          return null;
        }
        
        return {
          vin: v.vehicleId, // Use vehicleId as VIN
          fleet_id: v.fleetId || 'unknown',
          fleet_name: `Fleet ${v.fleetId?.replace('FLEET-', '') || 'Unknown'}`,
          telemetry_fleet_id: v.fleetId || 'unknown',
          location: {
            lat: v.lat,
            lon: v.lng
          },
          status: v.status === 'active' ? 'CONNECTED' : 'OFFLINE',
          speed: Math.random() * 60, // Mock speed
          fuel_level: Math.random() * 100, // Mock fuel
          driver_score: 85 + Math.random() * 15,
          auto_registered: true,
          last_update: v.lastUpdate || new Date().toISOString(),
          make: v.make || 'Fleet Vehicle',
          model: v.model || 'Fleet Unit',
          maintenance_status: Math.random() > 0.8 ? 'critical' : Math.random() > 0.6 ? 'engine_light' : 'none',
          in_trip: Math.random() > 0.7 // 30% chance of being in trip
        };
      }).filter(v => v !== null); // Remove vehicles without locations

      // Store total counts from API response
      const totalVehicleCount = vehiclesResult.total || 0;
      const vehiclesWithLocations = vehiclesResult.withLocations || 0;
      
      setVehicles(transformedVehicles);
      setTotalVehicleCount(totalVehicleCount);
      setVehiclesWithLocations(vehiclesWithLocations);
      console.log('✅ Successfully loaded', transformedVehicles.length, 'vehicles with locations out of', totalVehicleCount, 'total vehicles');

      // Auto-fit map to show all vehicles
      if (transformedVehicles.length > 0) {
        const lats = transformedVehicles.map(v => v.location.lat);
        const lons = transformedVehicles.map(v => v.location.lon);
        
        const minLat = Math.min(...lats);
        const maxLat = Math.max(...lats);
        const minLon = Math.min(...lons);
        const maxLon = Math.max(...lons);
        
        // Calculate center and zoom to fit all vehicles
        const centerLat = (minLat + maxLat) / 2;
        const centerLon = (minLon + maxLon) / 2;
        
        // Calculate appropriate zoom level based on bounds
        const latDiff = maxLat - minLat;
        const lonDiff = maxLon - minLon;
        const maxDiff = Math.max(latDiff, lonDiff);
        
        let zoom = 10;
        if (maxDiff > 10) zoom = 4;
        else if (maxDiff > 5) zoom = 6;
        else if (maxDiff > 1) zoom = 8;
        else if (maxDiff > 0.1) zoom = 10;
        else zoom = 12;
        
        setViewState({
          longitude: centerLon,
          latitude: centerLat,
          zoom: zoom
        });
      }
    } catch (error) {
      console.error('Error fetching vehicle data:', error);
      setError('Failed to load vehicle data. Please try refreshing.');
      setVehicles([]);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchVehicleData();
    
    // Auto-refresh every 30 seconds for real-time updates
    const interval = setInterval(fetchVehicleData, 30000);
    return () => clearInterval(interval);
  }, []);

  // Filter vehicles based on user selections
  const filteredVehicles = vehicles.filter(vehicle => {
    const fleetMatch = isAllFleets || vehicle.telemetry_fleet_id === selectedFleet;
    return fleetMatch;
  });

  // Create vehicle markers with status-based styling
  const createVehicleMarker = (vehicle: Vehicle) => {
    // Validate coordinates before creating marker
    if (!vehicle.location || 
        typeof vehicle.location.lat !== 'number' || 
        typeof vehicle.location.lon !== 'number' ||
        isNaN(vehicle.location.lat) || 
        isNaN(vehicle.location.lon)) {
      console.warn(`Skipping marker for vehicle ${vehicle.vin} due to invalid coordinates:`, vehicle.location);
      return null; // Don't render marker with invalid coordinates
    }
    
    // Determine dot color based on status priority:
    // 1. Blue if in trip (highest priority)
    // 2. Red if critical maintenance alert
    // 3. Yellow if engine light on
    // 4. Green if no maintenance issues
    let dotColor = '#10B981'; // Green (no maintenance issues)
    
    if (vehicle.maintenance_status === 'critical') {
      dotColor = '#EF4444'; // Red (critical maintenance)
    } else if (vehicle.maintenance_status === 'engine_light') {
      dotColor = '#F59E0B'; // Yellow (engine light)
    }
    
    if (vehicle.in_trip) {
      dotColor = '#3B82F6'; // Blue (currently in trip - overrides maintenance status)
    }

    return (
      <Marker
        key={vehicle.vin}
        longitude={vehicle.location.lon}
        latitude={vehicle.location.lat}
        onClick={(e) => {
          e.originalEvent.stopPropagation();
          setSelectedVehicle(vehicle);
          setSplitPanelOpen(true);
        }}
      >
        <div
          style={{
            width: '12px',
            height: '12px',
            borderRadius: '50%',
            backgroundColor: dotColor,
            border: '2px solid white',
            boxShadow: '0 2px 4px rgba(0,0,0,0.3)',
            cursor: 'pointer',
            transition: 'transform 0.2s ease',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.transform = 'scale(1.5)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = 'scale(1)';
          }}
        />
      </Marker>
    );
  };

  // Split panel content for selected vehicle details
  const splitPanelContent = selectedVehicle ? (
    <Container
      header={
        <Header
          variant="h2"
          actions={
            <Button
              variant="icon"
              iconName="close"
              onClick={() => {
                setSelectedVehicle(null);
                setSplitPanelOpen(false);
              }}
            />
          }
        >
          Vehicle Details
        </Header>
      }
    >
      <SpaceBetween size="m">
        <ColumnLayout columns={2} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">VIN</Box>
            <div>{selectedVehicle.vin}</div>
          </div>
          <div>
            <Box variant="awsui-key-label">Fleet</Box>
            <div>{selectedVehicle.fleet_name}</div>
          </div>
          <div>
            <Box variant="awsui-key-label">Make/Model</Box>
            <div>{selectedVehicle.make} {selectedVehicle.model}</div>
          </div>
          <div>
            <Box variant="awsui-key-label">Status</Box>
            <StatusIndicator type={selectedVehicle.status === 'CONNECTED' ? 'success' : 'stopped'}>
              {selectedVehicle.status}
            </StatusIndicator>
          </div>
        </ColumnLayout>

        <ColumnLayout columns={3} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Speed</Box>
            <div>{selectedVehicle.speed} mph</div>
          </div>
          <div>
            <Box variant="awsui-key-label">Fuel Level</Box>
            <div>{selectedVehicle.fuel_level}%</div>
          </div>
          <div>
            <Box variant="awsui-key-label">Driver Score</Box>
            <div>{selectedVehicle.driver_score}/100</div>
          </div>
        </ColumnLayout>

        <ColumnLayout columns={2} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Location</Box>
            <div>{selectedVehicle.location.lat.toFixed(6)}, {selectedVehicle.location.lon.toFixed(6)}</div>
          </div>
          <div>
            <Box variant="awsui-key-label">Last Update</Box>
            <div>{new Date(selectedVehicle.last_update).toLocaleString()}</div>
          </div>
        </ColumnLayout>
      </SpaceBetween>
    </Container>
  ) : null;

  return (
    <Container>
      <SpaceBetween size="l">
        {/* Dashboard Header */}
        <DashboardHeader
          title="Fleet Vehicles Map"
          actions={
            <SpaceBetween size="xs" direction="horizontal">
              <Button
                iconName="refresh"
                loading={refreshing}
                onClick={fetchVehicleData}
                disabled={refreshing}
                disabledReason="Refresh in progress..."
              >
                Refresh
              </Button>
            </SpaceBetween>
          }
        />

        {/* Filter Header */}
        <Container
          header={
            <Header
              variant="h2"
              description="Real-time vehicle locations and status monitoring with interactive map visualization"
              actions={
                <SpaceBetween direction="horizontal" size="s">
                  <Select
                    selectedOption={timeRangeOptions.find(option => option.value === selectedTimeRange)}
                    onChange={({ detail }) => setSelectedTimeRange(detail.selectedOption.value!)}
                    options={timeRangeOptions}
                    placeholder="Select time range"
                  />
                  <AlertsFleetFilter
                    selectedFleet={selectedFleet}
                    onFleetChange={handleFleetChange}
                    placeholder="Select fleet"
                    showContext={false}
                  />
                </SpaceBetween>
              }
            >
              Vehicle Map Dashboard
            </Header>
          }
        />

        {error && (
          <Alert
            statusIconAriaLabel="Error"
            type="error"
            header="Failed to load vehicle data"
            action={
              <Button onClick={fetchVehicleData}>
                Retry
              </Button>
            }
          >
            {error}
          </Alert>
        )}

        {loading ? (
          <Box textAlign="center" padding="xxl">
            <StatusIndicator type="loading">Loading fleet map...</StatusIndicator>
          </Box>
        ) : (
          <>
            {/* Interactive Map */}
            <div style={{ height: '600px', width: '100%', borderRadius: '8px', overflow: 'hidden', boxShadow: '0 2px 8px rgba(0,0,0,0.1)' }}>
              <Map
                {...viewState}
                onMove={evt => setViewState(evt.viewState)}
                mapStyle={mapStyle}
                attributionControl={true}
                style={{ width: '100%', height: '100%' }}
              >
                <NavigationControl position="top-right" />
                
                {/* Vehicle markers */}
                {filteredVehicles.map(createVehicleMarker).filter(marker => marker !== null)}
                
                {/* Selected vehicle popup */}
                {selectedVehicle && 
                 selectedVehicle.location &&
                 typeof selectedVehicle.location.lat === 'number' &&
                 typeof selectedVehicle.location.lon === 'number' &&
                 !isNaN(selectedVehicle.location.lat) &&
                 !isNaN(selectedVehicle.location.lon) && (
                  <Popup
                    longitude={selectedVehicle.location.lon}
                    latitude={selectedVehicle.location.lat}
                    anchor="bottom"
                    onClose={() => setSelectedVehicle(null)}
                    closeButton={true}
                    closeOnClick={false}
                  >
                  <div style={{ padding: '10px', minWidth: '200px' }}>
                    <div style={{ fontWeight: 'bold', marginBottom: '5px' }}>
                      {selectedVehicle.fleet_name}
                    </div>
                    <div style={{ fontSize: '12px', color: '#666' }}>
                      VIN: {selectedVehicle.vin}
                    </div>
                    <div style={{ fontSize: '12px', color: '#666' }}>
                      Status: {selectedVehicle.status}
                    </div>
                    <div style={{ fontSize: '12px', color: '#666' }}>
                      Speed: {selectedVehicle.speed} mph
                    </div>
                    <div style={{ fontSize: '12px', color: '#666' }}>
                      Fuel: {selectedVehicle.fuel_level}%
                    </div>
                  </div>
                </Popup>
              )}
            </Map>
          </div>

          {/* Fleet Summary Statistics */}
          <Container
            header={<Header variant="h3">Fleet Summary</Header>}
          >
            <ColumnLayout columns={3} variant="text-grid">
              <div>
                <Box variant="awsui-key-label">Total Vehicles</Box>
                <Box fontSize="display-l" fontWeight="bold">
                  {totalVehicleCount}
                </Box>
                <Box variant="small" color="text-status-info">
                  {vehiclesWithLocations} with locations
                </Box>
              </div>
              <div>
                <Box variant="awsui-key-label">Connected</Box>
                <Box fontSize="display-l" fontWeight="bold" color="text-status-success">
                  {filteredVehicles.filter(v => v.status === 'CONNECTED').length}
                </Box>
                <Box variant="small" color="text-status-info">
                  On map with locations
                </Box>
              </div>
              <div>
                <Box variant="awsui-key-label">Active Fleets</Box>
                <Box fontSize="display-l" fontWeight="bold">
                  {new Set(filteredVehicles.map(v => v.telemetry_fleet_id)).size}
                </Box>
                <Box variant="small" color="text-status-info">
                  With vehicle locations
                </Box>
              </div>
            </ColumnLayout>
          </Container>
        </>
      )}
    </SpaceBetween>

    <style>{`
      @keyframes pulse {
        0% {
          transform: scale(1);
          box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        }
        50% {
          transform: scale(1.1);
          box-shadow: 0 4px 16px rgba(59, 130, 246, 0.5);
        }
        100% {
          transform: scale(1);
          box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        }
      }
    `}</style>
  </Container>
  );
}
