// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Button,
  Select,
  Box,
  StatusIndicator,
  ColumnLayout,
  Alert
} from '@cloudscape-design/components';
import { getRuntimeConfig } from '../../../../config/api';
import { AlertsFleetFilter, useAlertsFleetFilter } from '../../../commons/AlertsFleetFilter';

// MapLibre GL JS with React Map GL
import Map, { NavigationControl, Marker, Popup, Source, Layer } from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';

interface Vehicle {
  vehicleId: string;
  vin: string;
  fleet_id: string;
  fleet_name: string;
  location: { lat: number; lon: number };
  status: 'CONNECTED' | 'OFFLINE' | 'MAINTENANCE';
  make: string;
  model: string;
  in_trip: boolean;
}

const VehicleMapView: React.FC = () => {
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedVehicle, setSelectedVehicle] = useState<Vehicle | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const {
    selectedFleet,
    handleFleetChange,
    isAllFleets
  } = useAlertsFleetFilter();

  const [selectedConnectionStatus, setSelectedConnectionStatus] = useState('all');
  
  const connectionStatusOptions = [
    { label: 'All Status', value: 'all' },
    { label: 'Connected', value: 'connected' },
    { label: 'Offline', value: 'offline' },
    { label: 'Maintenance', value: 'maintenance' }
  ];

  const [viewState, setViewState] = useState({
    longitude: -95.7129, 
    latitude: 37.0902,
    zoom: 4
  });

  const mapStyle = {
    version: 8,
    sources: {
      'osm-tiles': {
        type: 'raster' as const,
        tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
        tileSize: 256,
        attribution: '© OpenStreetMap contributors'
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

  const fetchVehicleData = async () => {
    try {
      setRefreshing(true);
      setError(null);
      
      const runtimeConfig = getRuntimeConfig();
      const apiEndpoint = runtimeConfig.apiEndpoint;
      
      const vehiclesResponse = await fetch(`${apiEndpoint}api/v1/vehicles/locations`);
      if (!vehiclesResponse.ok) {
        throw new Error(`HTTP error! status: ${vehiclesResponse.status}`);
      }
      
      const vehiclesResult = await vehiclesResponse.json();

      const transformedVehicles: Vehicle[] = (vehiclesResult.vehicles || []).map((v: any) => {
        if (!v.lat || !v.lng || typeof v.lat !== 'number' || typeof v.lng !== 'number') {
          return null;
        }
        
        return {
          vehicleId: v.vehicleId,
          vin: v.vin || v.vehicleId,
          fleet_id: v.fleetId || 'unknown',
          fleet_name: `Fleet ${v.fleetId?.replace('FLEET-', '') || 'Unknown'}`,
          location: {
            lat: v.lat,
            lon: v.lng
          },
          status: v.connectionStatus === 'connected' ? 'CONNECTED' : 'OFFLINE',
          make: v.make || 'Fleet Vehicle',
          model: v.model || 'Fleet Unit',
          in_trip: Math.random() > 0.7
        };
      }).filter(v => v !== null);

      setVehicles(transformedVehicles);

      // Auto-fit map to show all vehicles
      if (transformedVehicles.length > 0) {
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
          
          const latDiff = maxLat - minLat;
          const lonDiff = maxLon - minLon;
          const maxDiff = Math.max(latDiff, lonDiff);
          
          let zoom = 4;
          if (maxDiff > 15) zoom = 3;
          else if (maxDiff > 8) zoom = 4;
          else if (maxDiff > 4) zoom = 5;
          else if (maxDiff > 2) zoom = 6;
          else zoom = 7;
          
          setViewState({
            longitude: centerLon,
            latitude: centerLat,
            zoom: zoom
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
  }, []);

  const filteredVehicles = vehicles.filter(vehicle => {
    const fleetMatch = isAllFleets || vehicle.fleet_id === selectedFleet;
    
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

  // Prepare GeoJSON data for clustering
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

  // Cluster layer configuration
  const clusterLayer = {
    id: 'clusters',
    type: 'circle',
    source: 'vehicles',
    filter: ['has', 'point_count'],
    paint: {
      'circle-color': [
        'step',
        ['get', 'point_count'],
        'rgba(22, 78, 99, 0.25)',    // Small clusters
        50,
        'rgba(59, 130, 246, 0.25)',  // Medium clusters  
        200,
        'rgba(147, 197, 253, 0.25)'  // Large clusters
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
        '#164e63',  // Small clusters
        50,
        '#3b82f6',  // Medium clusters
        200,
        '#93c5fd'   // Large clusters
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
      'text-color': '#164e63'
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
        ['==', ['get', 'status'], 'CONNECTED'], '#059669',  // Green
        '#dc2626'  // Red
      ],
      'circle-radius': 6,
      'circle-stroke-width': 2,
      'circle-stroke-color': '#fff'
    }
  };

  // Handle map clicks for clusters and individual vehicles
  const handleMapClick = (event: any) => {
    const features = event.features;
    if (features && features.length > 0) {
      const feature = features[0];
      
      // Check if it's a cluster
      if (feature.properties && feature.properties.point_count) {
        const coordinates = feature.geometry.coordinates;
        
        // Zoom in by 3 levels and center on cluster
        setViewState(prev => ({
          longitude: coordinates[0],
          latitude: coordinates[1], 
          zoom: Math.min(prev.zoom + 3, 16) // Max zoom of 16
        }));
      }
      // Check if it's an individual vehicle
      else if (feature.properties && feature.properties.id) {
        const vehicleId = feature.properties.id;
        const vehicle = filteredVehicles.find(v => v.vin === vehicleId);
        
        if (vehicle) {
          setSelectedVehicle(vehicle);
        }
      }
    }
  };

  const createVehicleMarker = (vehicle: Vehicle) => {
    if (!vehicle.location || 
        typeof vehicle.location.lat !== 'number' || 
        typeof vehicle.location.lon !== 'number' ||
        isNaN(vehicle.location.lat) || 
        isNaN(vehicle.location.lon)) {
      return null;
    }
    
    let dotColor = '#10B981'; // Green (connected)
    if (vehicle.status === 'OFFLINE') {
      dotColor = '#EF4444'; // Red (offline)
    } else if (vehicle.status === 'MAINTENANCE') {
      dotColor = '#F59E0B'; // Yellow (maintenance)
    }
    
    if (vehicle.in_trip) {
      dotColor = '#3B82F6'; // Blue (in trip)
    }

    return (
      <Marker
        key={vehicle.vin}
        longitude={vehicle.location.lon}
        latitude={vehicle.location.lat}
        onClick={(e) => {
          e.originalEvent.stopPropagation();
          setSelectedVehicle(vehicle);
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

  return (
    <SpaceBetween size="l">
      {/* Filter Controls */}
      <Container
        header={
          <Header
            variant="h3"
            description="Real-time vehicle locations and status monitoring"
            actions={
              <SpaceBetween direction="horizontal" size="s">
                <Button
                  iconName="refresh"
                  loading={refreshing}
                  onClick={fetchVehicleData}
                  disabled={refreshing}
                >
                  Refresh
                </Button>
                <Select
                  selectedOption={connectionStatusOptions.find(option => option.value === selectedConnectionStatus)}
                  onChange={({ detail }) => setSelectedConnectionStatus(detail.selectedOption.value!)}
                  options={connectionStatusOptions}
                  placeholder="Select status"
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
            Vehicle Map
          </Header>
        }
      />

      {error && (
        <Alert
          type="error"
          header="Failed to load vehicle data"
          action={<Button onClick={fetchVehicleData}>Retry</Button>}
        >
          {error}
        </Alert>
      )}

      {loading ? (
        <Box textAlign="center" padding="xxl">
          <StatusIndicator type="loading">Loading vehicle map...</StatusIndicator>
        </Box>
      ) : (
        <>
          {/* Interactive Map */}
          <div style={{ height: '500px', width: '100%', borderRadius: '8px', overflow: 'hidden' }}>
            <Map
              {...viewState}
              onMove={evt => setViewState(evt.viewState)}
              onClick={handleMapClick}
              interactiveLayerIds={['clusters', 'unclustered-point']}
              mapStyle={mapStyle}
              style={{ width: '100%', height: '100%', cursor: 'pointer' }}
            >
              <NavigationControl position="top-right" />
              
              {/* Vehicle data source with clustering */}
              <Source
                id="vehicles"
                type="geojson"
                data={vehicleGeoJSON}
                cluster={true}
                clusterMaxZoom={12}  // Stop clustering at zoom 12
                clusterRadius={60}   // Cluster radius for grouping
              >
                <Layer {...clusterLayer} />
                <Layer {...clusterCountLayer} />
                <Layer {...unclusteredPointLayer} />
              </Source>
              
              {/* Individual markers for very detailed view (zoom > 12 and < 100 vehicles) */}
              {viewState.zoom > 12 && filteredVehicles.length <= 100 && 
                filteredVehicles.map(createVehicleMarker).filter(marker => marker !== null)
              }
              
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
                          window.location.href = `/vehicles/management/${selectedVehicle.vehicleId || selectedVehicle.vin}`;
                        }}
                      >
                        {selectedVehicle.vin}
                      </span>
                    </div>
                    <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>
                      Fleet: {selectedVehicle.fleet_name}
                    </div>
                    <div style={{ fontSize: '12px', color: '#666' }}>
                      Status: <span style={{ 
                        color: selectedVehicle.status === 'CONNECTED' ? '#059669' : '#dc2626',
                        fontWeight: 'bold'
                      }}>
                        {selectedVehicle.status}
                      </span>
                    </div>
                  </div>
                </Popup>
              )}
            </Map>
          </div>

          {/* Fleet Summary */}
          <Container header={<Header variant="h3">Fleet Summary</Header>}>
            <ColumnLayout columns={3} variant="text-grid">
              <div>
                <Box variant="awsui-key-label">Total Vehicles</Box>
                <Box fontSize="display-l" fontWeight="bold">
                  {vehicles.length}
                </Box>
              </div>
              <div>
                <Box variant="awsui-key-label">Connected</Box>
                <Box fontSize="display-l" fontWeight="bold" color="text-status-success">
                  {filteredVehicles.filter(v => v.status === 'CONNECTED').length}
                </Box>
              </div>
              <div>
                <Box variant="awsui-key-label">Active Fleets</Box>
                <Box fontSize="display-l" fontWeight="bold">
                  {new Set(filteredVehicles.map(v => v.fleet_id)).size}
                </Box>
              </div>
            </ColumnLayout>
          </Container>
        </>
      )}
    </SpaceBetween>
  );
};

export default VehicleMapView;
