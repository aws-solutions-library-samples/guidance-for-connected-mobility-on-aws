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
import Map, { NavigationControl, Marker, Popup, Source, Layer } from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';

interface Vehicle {
  vehicleId: string;
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

  // Map visualization options - default to clusters
  const [mapVisualization, setMapVisualization] = useState('clusters');
  
  // Track if user has manually interacted with the map
  const [userHasInteracted, setUserHasInteracted] = useState(false);
  
  
  const visualizationOptions = [
    { label: 'Individual markers', value: 'markers' },
    { label: 'Clustered bubbles', value: 'clusters' },
    { label: 'Heat map', value: 'heatmap' },
  ];
  // Connection status filter state
  const [selectedConnectionStatus, setSelectedConnectionStatus] = useState('all');
  
  const connectionStatusOptions = [
    { label: 'All Status', value: 'all' },
    { label: 'Connected', value: 'connected' },
    { label: 'Offline', value: 'offline' },
    { label: 'Maintenance', value: 'maintenance' }
  ];

  // Dynamic map viewport - starts centered on US 
  const [viewState, setViewState] = useState({
    longitude: -95.7129, 
    latitude: 37.0902,
    zoom: 4
  });

  // Handle map clicks - clusters and individual vehicles
  const handleMapClick = (event: any) => {
    const features = event.features;
    if (features && features.length > 0) {
      const feature = features[0];
      
      // Check if it's a cluster
      if (feature.properties && feature.properties.point_count) {
        const coordinates = feature.geometry.coordinates;
        
        console.log('🎯 Cluster clicked, centering and zooming in');
        
        // Zoom in by 3 levels and center on cluster
        setViewState(prev => ({
          longitude: coordinates[0],
          latitude: coordinates[1], 
          zoom: Math.min(prev.zoom + 3, 16) // Max zoom of 16
        }));
        
        // Mark that user has interacted with the map
        setUserHasInteracted(true);
      }
      // Check if it's an individual vehicle
      else if (feature.properties && feature.properties.id) {
        const vehicleId = feature.properties.id;
        const vehicle = filteredVehicles.find(v => v.vin === vehicleId);
        
        if (vehicle) {
          console.log('🚗 Vehicle clicked:', vehicle.make, vehicle.model, vehicle.vin);
          setSelectedVehicle(vehicle);
        }
      }
    }
  };

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
      console.log('🔍 Raw API response:', vehiclesResult);
      console.log('🔍 Number of vehicles returned:', vehiclesResult.vehicles?.length || 0);
      console.log('🔍 First vehicle sample:', vehiclesResult.vehicles?.[0]);
      console.log('🔍 Vehicle fields available:', Object.keys(vehiclesResult.vehicles?.[0] || {}));

      // Transform vehicles with real locations from optimized API
      const transformedVehicles: Vehicle[] = (vehiclesResult.vehicles || []).map((v: any) => {
        // Only include vehicles with actual location data for map display
        if (!v.lat || !v.lng || typeof v.lat !== 'number' || typeof v.lng !== 'number') {
          console.log('Skipping vehicle without valid coordinates:', v.vehicleId, v.lat, v.lng);
          return null;
        }
        
        return {
          vehicleId: v.vehicleId, // Keep original vehicleId for navigation
          vin: v.vin || v.vehicleId, // Use VIN field from API, fallback to vehicleId
          fleet_id: v.fleetId || 'unknown',
          fleet_name: `Fleet ${v.fleetId?.replace('FLEET-', '') || 'Unknown'}`,
          telemetry_fleet_id: v.fleetId || 'unknown',
          location: {
            lat: v.lat,
            lon: v.lng
          },
          status: v.connectionStatus === 'connected' ? 'CONNECTED' : 'OFFLINE',
          speed: Math.random() * 60, // Mock speed
          fuel_level: Math.random() * 100, // Mock fuel
          driver_score: 85 + Math.random() * 15,
          auto_registered: true,
          last_update: v.lastUpdate ? new Date(v.lastUpdate * 1000).toISOString() : new Date().toISOString(),
          make: v.make || 'Fleet Vehicle',
          model: v.model || 'Fleet Unit',
          maintenance_status: Math.random() > 0.8 ? 'critical' : Math.random() > 0.6 ? 'engine_light' : 'none',
          in_trip: Math.random() > 0.7 // 30% chance of being in trip
        };
      }).filter(v => v !== null); // Remove vehicles without locations

      // Store total counts from API response
      const totalVehicleCount = vehiclesResult.total || vehiclesResult.vehicles?.length || 0;
      const vehiclesWithLocations = vehiclesResult.withLocations || transformedVehicles.length;
      
      setVehicles(transformedVehicles);
      setTotalVehicleCount(totalVehicleCount);
      setVehiclesWithLocations(vehiclesWithLocations);
      console.log('✅ Successfully loaded', transformedVehicles.length, 'vehicles with locations out of', totalVehicleCount, 'total vehicles');

      // Auto-fit map to show all vehicles - only on initial load, not on refresh
      if (transformedVehicles.length > 0 && !userHasInteracted) {
        // Filter to US coordinates only (longitude between -125 and -65)
        const usVehicles = transformedVehicles.filter(v => 
          v.location.lon >= -125 && v.location.lon <= -65
        );
        
        if (usVehicles.length > 0) {
          const lats = usVehicles.map(v => v.location.lat);
          const lons = usVehicles.map(v => v.location.lon);
          
          const minLat = Math.min(...lats);
          const maxLat = Math.max(...lats);
          const minLon = Math.min(...lons);
          const maxLon = Math.max(...lons);
          
          const centerLat = (minLat + maxLat) / 2;
          const centerLon = (minLon + maxLon) / 2;
          
          console.log('🗺️ Centering on US vehicles only:', usVehicles.length, 'vehicles');
          console.log('🗺️ US center:', centerLat, centerLon);
          
          // Calculate appropriate zoom based on vehicle spread
          const latDiff = maxLat - minLat;
          const lonDiff = maxLon - minLon;
          const maxDiff = Math.max(latDiff, lonDiff);
          
          let zoom = 4; // Default for US fleet view
          if (maxDiff > 15) zoom = 3;      // Very spread out (coast to coast)
          else if (maxDiff > 8) zoom = 4;  // Multi-state
          else if (maxDiff > 4) zoom = 5;  // Regional
          else if (maxDiff > 2) zoom = 6;  // State-level
          else zoom = 7;                   // City-level
          
          setViewState({
            longitude: centerLon,
            latitude: centerLat,
            zoom: zoom
          });
        } else {
          // Fallback to all vehicles if no US vehicles found
          const lats = transformedVehicles.map(v => v.location.lat);
          const lons = transformedVehicles.map(v => v.location.lon);
          const centerLat = (Math.min(...lats) + Math.max(...lats)) / 2;
          const centerLon = (Math.min(...lons) + Math.max(...lons)) / 2;
          
          console.log('🗺️ No US vehicles found, centering on all vehicles:', centerLat, centerLon);
          setViewState({
            longitude: centerLon,
            latitude: centerLat,
            zoom: 3
          });
        }
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
    
    // Connection status filter
    let statusMatch = true;
    if (selectedConnectionStatus !== 'all') {
      switch (selectedConnectionStatus) {
        case 'connected':
          statusMatch = vehicle.status === 'CONNECTED';
          break;
        case 'offline':
          statusMatch = vehicle.status === 'OFFLINE';
          break;
        case 'maintenance':
          statusMatch = vehicle.status === 'MAINTENANCE';
          break;
      }
    }
    
    return fleetMatch && statusMatch;
  });

  // Prepare GeoJSON data for clustering and heatmap
  const vehicleGeoJSON = {
    type: 'FeatureCollection',
    features: filteredVehicles.map(vehicle => ({
      type: 'Feature',
      properties: {
        id: vehicle.vin,
        status: vehicle.status,
        make: vehicle.make,
        model: vehicle.model,
        fleetId: vehicle.fleet_id
      },
      geometry: {
        type: 'Point',
        coordinates: [vehicle.location.lon, vehicle.location.lat]
      }
    }))
  };

  // Cluster layer configuration with AWS CloudScape colors and transparency
  const clusterLayer = {
    id: 'clusters',
    type: 'circle',
    source: 'vehicles',
    filter: ['has', 'point_count'],
    paint: {
      'circle-color': [
        'step',
        ['get', 'point_count'],
        'rgba(22, 78, 99, 0.25)',    // AWS Blue-900 with 25% opacity (small clusters)
        50,
        'rgba(59, 130, 246, 0.25)',  // AWS Blue-500 with 25% opacity (medium clusters)  
        200,
        'rgba(147, 197, 253, 0.25)'  // AWS Blue-300 with 25% opacity (large clusters)
      ],
      'circle-radius': [
        'step',
        ['get', 'point_count'],
        15, 50,    // Small clusters: 15px radius
        25, 200,   // Medium clusters: 25px radius  
        35         // Large clusters: 35px radius
      ],
      'circle-stroke-width': 2,
      'circle-stroke-color': [
        'step',
        ['get', 'point_count'],
        '#164e63',  // AWS Blue-900 (small clusters)
        50,
        '#3b82f6',  // AWS Blue-500 (medium clusters)
        200,
        '#93c5fd'   // AWS Blue-300 (large clusters)
      ]
    }
  };

  const clusterCountLayer = {
    id: 'cluster-count',
    type: 'symbol',
    source: 'vehicles',
    filter: ['has', 'point_count'],
    layout: {
      'text-field': ['get', 'point_count_abbreviated'],
      'text-size': 14
    },
    paint: {
      'text-color': '#164e63'  // AWS Blue-900 for better contrast
    }
  };

  const unclusteredPointLayer = {
    id: 'unclustered-point',
    type: 'circle',
    source: 'vehicles',
    filter: ['!', ['has', 'point_count']],
    paint: {
      'circle-color': [
        'case',
        ['==', ['get', 'status'], 'CONNECTED'], '#059669',  // AWS Green-600
        '#dc2626'  // AWS Red-600
      ],
      'circle-radius': [
        'case',
        ['boolean', ['feature-state', 'hover'], false],
        8,  // Larger when hovered
        6   // Normal size
      ],
      'circle-stroke-width': [
        'case',
        ['boolean', ['feature-state', 'hover'], false],
        3,  // Thicker stroke when hovered
        2   // Normal stroke
      ],
      'circle-stroke-color': '#fff'
    }
  };

  // Heatmap layer configuration
  const heatmapLayer = {
    id: 'vehicles-heat',
    type: 'heatmap',
    source: 'vehicles',
    maxzoom: 15,
    paint: {
      'heatmap-weight': [
        'interpolate',
        ['linear'],
        ['zoom'],
        0, 1,
        15, 1
      ],
      'heatmap-intensity': [
        'interpolate',
        ['linear'],
        ['zoom'],
        0, 1,
        15, 3
      ],
      'heatmap-color': [
        'interpolate',
        ['linear'],
        ['heatmap-density'],
        0, 'rgba(33,102,172,0)',
        0.2, 'rgb(103,169,207)',
        0.4, 'rgb(209,229,240)',
        0.6, 'rgb(253,219,199)',
        0.8, 'rgb(239,138,98)',
        1, 'rgb(178,24,43)'
      ],
      'heatmap-radius': [
        'interpolate',
        ['linear'],
        ['zoom'],
        0, 2,
        15, 20
      ]
    }
  };

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
                    selectedOption={connectionStatusOptions.find(option => option.value === selectedConnectionStatus)}
                    onChange={({ detail }) => setSelectedConnectionStatus(detail.selectedOption.value!)}
                    options={connectionStatusOptions}
                    placeholder="Select connection status"
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
                onMove={evt => {
                  setViewState(evt.viewState);
                  setUserHasInteracted(true); // Mark interaction on any map movement
                }}
                onClick={handleMapClick}
                interactiveLayerIds={['clusters', 'unclustered-point']}
                mapStyle={mapStyle}
                attributionControl={true}
                style={{ width: '100%', height: '100%', cursor: 'pointer' }}
              >
                <NavigationControl position="top-right" />
                
                {/* Vehicle data source */}
                <Source
                  id="vehicles"
                  type="geojson"
                  data={vehicleGeoJSON}
                  cluster={mapVisualization === 'clusters'}
                  clusterMaxZoom={12}  // Stop clustering at zoom 12, show individual vehicles after
                  clusterRadius={60}   // Larger cluster radius for better grouping
                >
                  {/* Render based on selected visualization */}
                  {mapVisualization === 'clusters' && (
                    <>
                      <Layer {...clusterLayer} />
                      <Layer {...unclusteredPointLayer} />
                    </>
                  )}
                  
                  {mapVisualization === 'heatmap' && (
                    <Layer {...heatmapLayer} />
                  )}
                  
                  {mapVisualization === 'markers' && (
                    <Layer {...unclusteredPointLayer} />
                  )}
                </Source>
                
                {/* Individual markers for very detailed view (zoom > 12 and < 100 vehicles visible) */}
                {viewState.zoom > 12 && filteredVehicles.length <= 100 && 
                  filteredVehicles.map(createVehicleMarker).filter(marker => marker !== null)
                }
                
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
                  <div style={{ padding: '10px', minWidth: '220px' }}>
                    <div style={{ fontWeight: 'bold', marginBottom: '8px', fontSize: '14px' }}>
                      {selectedVehicle.make} {selectedVehicle.model}
                    </div>
                    <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>
                      VIN: <span 
                        style={{ cursor: 'pointer', color: '#0073bb', textDecoration: 'underline' }}
                        onClick={() => {
                          // Navigate to vehicle detail page using vehicleId
                          window.location.href = `/fleets/vehicles/${selectedVehicle.vehicleId || selectedVehicle.vin}`;
                        }}
                      >
                        {selectedVehicle.vin}
                      </span>
                    </div>
                    <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>
                      Fleet: {selectedVehicle.fleet_name}
                    </div>
                    <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>
                      Status: <span style={{ 
                        color: selectedVehicle.status === 'CONNECTED' ? '#059669' : '#dc2626',
                        fontWeight: 'bold'
                      }}>
                        {selectedVehicle.status}
                      </span>
                    </div>
                    <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>
                      Speed: {Math.round(selectedVehicle.speed || 0)} mph
                    </div>
                    <div style={{ fontSize: '12px', color: '#666' }}>
                      Fuel: {Math.round(selectedVehicle.fuel_level || 0)}%
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
