// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect, useContext, useMemo } from 'react';
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
  Input,
  ColumnLayout,
  Alert
} from '@cloudscape-design/components';
import { useNavigate } from "react-router-dom";
import { useLocalStorage } from "../../commons/use-local-storage";
import { AlertsFleetFilter, useAlertsFleetFilter } from '../../commons/AlertsFleetFilter';
import { DashboardHeader } from '../../alerts/header';
import { useAuth } from "../../../auth/useAuth";
import { getRealtimeApiClient, VehicleUpdate } from "../../../services/RealtimeDataService";
import { useRealtimeConnection } from "../../../hooks/useRealtimeData";
import { VehicleMarkerIcon } from '../../../utils/vehicleMarkerIcon';
import { getMapConfiguration, MapConfig } from '../../../utils/mapConfig';

// MapLibre GL JS with React Map GL (works with both OpenStreetMap and Amazon Location Services)
import Map, { NavigationControl, Marker, Popup, Source, Layer } from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';
import { fromCognitoIdentityPool } from '@aws-sdk/credential-provider-cognito-identity';
import { CognitoIdentityClient } from '@aws-sdk/client-cognito-identity';

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

  // ── Realtime WebSocket overlay (spec 2026-06-16-cms-ui-realtime-websocket-wiring) ──
  // WS-primary / poll-fallback: the REST load below provides the initial set;
  // live `vehicleUpdate` events from the secured WS API are merged in by VIN.
  // Purely additive — if the WS can't connect (no endpoint / auth / non-admin
  // without a fleet) the existing REST flow is unaffected. fleetId follows the
  // fleet filter; admins use the all-fleet ('*') stream.
  const wsFleetId = isAllFleets ? '*' : (selectedFleet || undefined);
  const { service: rtService } = useRealtimeConnection(undefined, { fleetId: wsFleetId });
  useEffect(() => {
    if (!rtService) return;
    const onVehicleUpdate = (u: VehicleUpdate) => {
      setVehicles(prev => prev.map(v => {
        if (v.vin !== u.vin) return v;
        const nextStatus =
          u.connectivity === 'OFFLINE'
            ? 'OFFLINE'
            : u.status
              ? (u.status.toUpperCase() === 'MAINTENANCE' ? 'MAINTENANCE' : 'CONNECTED')
              : v.status;
        return {
          ...v,
          location: u.location
            ? { lat: u.location.latitude, lon: u.location.longitude }
            : v.location,
          speed: u.location?.speed ?? u.telemetry?.speed ?? v.speed,
          status: nextStatus as Vehicle['status'],
          last_update: u.location?.last_updated || new Date().toISOString(),
        };
      }));
    };
    rtService.on('vehicleUpdate', onVehicleUpdate);
    if (isAllFleets) {
      rtService.subscribe('dashboard');
    } else if (selectedFleet) {
      rtService.subscribe('fleet', selectedFleet);
    }
    return () => {
      rtService.off('vehicleUpdate', onVehicleUpdate);
      if (isAllFleets) rtService.unsubscribe('dashboard');
      else if (selectedFleet) rtService.unsubscribe('fleet', selectedFleet);
    };
  }, [rtService, selectedFleet, isAllFleets]);

  // Map visualization options - default to clusters
  const [mapVisualization, setMapVisualization] = useState('clusters');
  
  // Heatmap overlays
  const [showSafetyHeatmap, setShowSafetyHeatmap] = useState(false);
  const [showMaintenanceHeatmap, setShowMaintenanceHeatmap] = useState(false);
  const [safetyEventGeoJSON, setSafetyEventGeoJSON] = useState<any>({ type: 'FeatureCollection', features: [] });
  const [maintenanceGeoJSON, setMaintenanceGeoJSON] = useState<any>({ type: 'FeatureCollection', features: [] });

  useEffect(() => {
    if (!showSafetyHeatmap) return;
    const runtimeConfig = getRuntimeConfig();
    fetch(`${runtimeConfig.apiEndpoint}api/v1/safety-alerts?limit=100`)
      .then(r => r.json())
      .then(d => {
        const features = (d.alerts || d.safetyAlerts || [])
          .filter((e: any) => isFinite(parseFloat(e.latitude || e.location?.latitude)) && isFinite(parseFloat(e.longitude || e.location?.longitude)))
          .map((e: any) => ({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [
              parseFloat(e.longitude || e.location?.longitude),
              parseFloat(e.latitude || e.location?.latitude),
            ]},
            properties: { weight: e.severity === 'high' ? 2 : 1 },
          }));
        setSafetyEventGeoJSON({ type: 'FeatureCollection', features });
      })
      .catch(() => {});
  }, [showSafetyHeatmap]);

  useEffect(() => {
    if (!showMaintenanceHeatmap) return;
    const runtimeConfig = getRuntimeConfig();
    // Maintenance alerts lack lat/lng — use vehicle last-known positions for vehicles with active alerts
    fetch(`${runtimeConfig.apiEndpoint}api/v1/maintenance-alerts?limit=100`)
      .then(r => r.json())
      .then(d => {
        const alertVehicleIds = new Set((d.alerts || []).map((a: any) => a.vehicleId).filter(Boolean));
        const features = vehicles
          .filter(v => alertVehicleIds.has(v.vehicleId) || alertVehicleIds.has(v.vin))
          .map(v => ({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [v.location.lon, v.location.lat] },
            properties: {},
          }));
        setMaintenanceGeoJSON({ type: 'FeatureCollection', features });
      })
      .catch(() => {});
  }, [showMaintenanceHeatmap, vehicles]);
  
  // Track if user has manually interacted with the map
  const [userHasInteracted, setUserHasInteracted] = useState(false);
  const [mapConfig, setMapConfig] = useState<MapConfig | null>(null);

  // Geofence state
  const [geofences, setGeofences] = useState<any[]>([]);
  const [geofenceMode, setGeofenceMode] = useState(false);
  const [geofenceRadius, setGeofenceRadius] = useState(5); // km
  const [geofenceName, setGeofenceName] = useState('');
  const [geofenceVehicle, setGeofenceVehicle] = useState('ALL');
  const CMD_API = () => (window as any).runtimeConfig?.commandsApiEndpoint || '';

  // Setup Amazon Location (HERE) basemap via the SHARED authenticated helper.
  // Uses the logged-in user's Cognito id-token (getMapConfiguration →
  // withIdentityPoolId + logins → v1 Maps API). This is the SAME path
  // VehicleMapView uses; consolidating here removes the divergent
  // unauthenticated v2 flow that H1 (unauth Identity-Pool disabled) broke
  // and that caused "lost HERE maps" to recur on the fleet map only.
  useEffect(() => {
    getMapConfiguration()
      .then(setMapConfig)
      .catch((e) => { console.error('[FleetVehicleMapView] map config failed:', e); setMapConfig(null); });
  // auth.user as dep: re-fires once the idToken lands in storage after login.
  // Empty-dep mount fires before the token is available → OSM fallback.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth.user]);
  
  
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
    // Geofence placement mode
    if (geofenceMode) {
      const { lngLat } = event;
      if (lngLat) {
        createGeofence(lngLat.lat, lngLat.lng);
      }
      return;
    }

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

  // Basemap style comes from the shared authenticated mapConfig (v1 HERE).
  const mapStyle = mapConfig?.mapStyle ?? null;

  // Fetch vehicle data from optimized locations API
  const fetchVehicleData = async () => {
    try {
      setRefreshing(true);
      setError(null);
      
      const runtimeConfig = getRuntimeConfig();
      const apiEndpoint = runtimeConfig.apiEndpoint;
      
      console.log('🚗 Fetching vehicle locations from:', apiEndpoint);
      
      // Use optimized locations endpoint
      const vehiclesResponse = await fetch(`${apiEndpoint}api/v1/vehicles/locations`);
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
        const lat = parseFloat(v.lat);
        const lng = parseFloat(v.lng);
        if (!isFinite(lat) || !isFinite(lng) || lat < -90 || lat > 90 || lng < -180 || lng > 180) {
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
            lat,
            lon: lng
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
  // ── Geofence functions ──
  const fetchGeofences = async () => {
    try {
      const r = await fetch(`${CMD_API()}/api/geofences/ALL`);
      if (r.ok) {
        const d = await r.json();
        setGeofences(d.geofences || []);
      }
    } catch {}
  };

  useEffect(() => { fetchGeofences(); }, []);

  const createGeofence = async (lat: number, lng: number) => {
    try {
      const r = await fetch(`${CMD_API()}/api/geofences`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          vehicleId: geofenceVehicle,
          name: geofenceName || `Geofence at ${lat.toFixed(4)}, ${lng.toFixed(4)}`,
          centerLat: lat, centerLng: lng,
          radiusKm: geofenceRadius,
          action: 'ALERT',
        }),
      });
      if (r.ok) {
        setGeofenceMode(false);
        setGeofenceName('');
        fetchGeofences();
      }
    } catch {}
  };

  const deleteGeofence = async (gfId: string) => {
    try {
      await fetch(`${CMD_API()}/api/geofences/${gfId}`, { method: 'DELETE' });
      fetchGeofences();
    } catch {}
  };

  // Generate GeoJSON circle polygon for a geofence
  const geofenceToGeoJSON = (gf: any) => {
    const lat = parseFloat(gf.centerLat);
    const lng = parseFloat(gf.centerLng);
    const r = parseFloat(gf.radiusKm);
    const points = 64;
    const coords = [];
    for (let i = 0; i <= points; i++) {
      const angle = (i / points) * 2 * Math.PI;
      const dlat = (r / 111.32) * Math.cos(angle);
      const dlng = (r / (111.32 * Math.cos(lat * Math.PI / 180))) * Math.sin(angle);
      coords.push([lng + dlng, lat + dlat]);
    }
    return { type: 'Feature' as const, properties: { id: gf.geofenceId, name: gf.name },
             geometry: { type: 'Polygon' as const, coordinates: [coords] } };
  };

  const geofenceGeoJSON = useMemo(() => ({
    type: 'FeatureCollection' as const,
    features: geofences.filter(g => g.active !== false).map(geofenceToGeoJSON),
  }), [geofences]);

  const createVehicleMarker = (vehicle: Vehicle) => {
    // Validate coordinates before creating marker
    if (!vehicle.location || 
        typeof vehicle.location.lat !== 'number' || 
        typeof vehicle.location.lon !== 'number' ||
        isNaN(vehicle.location.lat) || 
        isNaN(vehicle.location.lon)) {
      console.warn(`Skipping marker for vehicle ${vehicle.vin} due to invalid coordinates:`, vehicle.location);
      return null;
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
        <div style={{ cursor: 'pointer', transition: 'transform 0.15s ease' }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.transform = 'scale(1.25)'; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.transform = 'scale(1)'; }}
        >
          <VehicleMarkerIcon make={vehicle.make} model={vehicle.model} connected={vehicle.status === 'CONNECTED'} style="A" />
        </div>
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
                  <Toggle checked={showSafetyHeatmap} onChange={({ detail }) => setShowSafetyHeatmap(detail.checked)}>
                    🔴 Safety heatmap
                  </Toggle>
                  <Toggle checked={showMaintenanceHeatmap} onChange={({ detail }) => setShowMaintenanceHeatmap(detail.checked)}>
                    🟣 Maintenance heatmap
                  </Toggle>
                  <Toggle checked={geofenceMode}
                    onChange={({ detail }) => setGeofenceMode(detail.checked)}>
                    {geofenceMode ? '📍 Click map to place' : 'Set Geofence'}
                  </Toggle>
                  {geofenceMode && (
                    <>
                      <Input type="number" value={String(geofenceRadius)}
                        onChange={({ detail }) => setGeofenceRadius(Number(detail.value))}
                        placeholder="Radius (km)" />
                      <Input value={geofenceName}
                        onChange={({ detail }) => setGeofenceName(detail.value)}
                        placeholder="Geofence name" />
                    </>
                  )}
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
              {mapStyle && (
                <Map
                  {...viewState}
                  onMove={evt => {
                    setViewState(evt.viewState);
                    setUserHasInteracted(true); // Mark interaction on any map movement
                  }}
                  onClick={handleMapClick}
                  interactiveLayerIds={['clusters', 'unclustered-point']}
                  mapStyle={mapStyle}
                  {...(mapConfig?.authOptions || {})}
                  onLoad={() => console.log('🗺️ Map loaded successfully with AWS Location Services')}
                  onError={(error) => console.error('🚨 Map loading error:', error)}
                  attributionControl={true}
                  style={{ width: '100%', height: '100%', cursor: 'pointer' }}
                >
                  <NavigationControl position="top-right" />
                  
                  {/* Vehicle data source — always clustered for scale (handles 10k+).
                      GL clustering runs on the GPU; no React components involved until zoom ≥ 14. */}
                  <Source
                    id="vehicles"
                    type="geojson"
                    data={vehicleGeoJSON}
                    cluster={true}
                    clusterMaxZoom={13}
                    clusterRadius={50}
                  >
                    <Layer {...clusterLayer} />
                    <Layer {...clusterCountLayer} />
                    <Layer {...unclusteredPointLayer} />
                  </Source>

                  {/* Individual pin markers only when zoomed in far (≥14) with ≤50 visible —
                      keeps DOM node count low even at 10k total vehicles */}
                  {viewState.zoom >= 14 && filteredVehicles.length <= 50 &&
                    filteredVehicles.map(createVehicleMarker).filter(Boolean)
                  }

                  {/* Safety events heatmap overlay */}
                  {showSafetyHeatmap && safetyEventGeoJSON.features.length > 0 && (
                    <Source id="safety-heat" type="geojson" data={safetyEventGeoJSON}>
                      <Layer id="safety-heat-layer" type="heatmap" paint={{
                        'heatmap-weight': ['coalesce', ['get', 'weight'], 1],
                        'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 0, 1, 15, 2],
                        'heatmap-color': [
                          'interpolate', ['linear'], ['heatmap-density'],
                          0, 'rgba(255,237,160,0)',
                          0.3, 'rgba(254,178,76,0.6)',
                          0.6, 'rgba(240,59,32,0.8)',
                          1,   'rgba(189,0,38,1)',
                        ],
                        'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 0, 8, 15, 25],
                        'heatmap-opacity': 0.75,
                      }} />
                    </Source>
                  )}

                  {/* Maintenance alerts heatmap overlay */}
                  {showMaintenanceHeatmap && maintenanceGeoJSON.features.length > 0 && (
                    <Source id="maintenance-heat" type="geojson" data={maintenanceGeoJSON}>
                      <Layer id="maintenance-heat-layer" type="heatmap" paint={{
                        'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 0, 1, 15, 2],
                        'heatmap-color': [
                          'interpolate', ['linear'], ['heatmap-density'],
                          0, 'rgba(237,233,254,0)',
                          0.3, 'rgba(167,139,250,0.6)',
                          0.6, 'rgba(109,40,217,0.8)',
                          1,   'rgba(76,29,149,1)',
                        ],
                        'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 0, 8, 15, 25],
                        'heatmap-opacity': 0.75,
                      }} />
                    </Source>
                  )}
                  
                  {/* Selected vehicle popup */}
                  {/* Geofence circles layer */}
                  {geofenceGeoJSON.features.length > 0 && (
                    <Source id="geofences" type="geojson" data={geofenceGeoJSON}>
                      <Layer id="geofence-fill" type="fill" paint={{
                        'fill-color': '#ff6b6b', 'fill-opacity': 0.1
                      }} />
                      <Layer id="geofence-border" type="line" paint={{
                        'line-color': '#ff6b6b', 'line-width': 2, 'line-dasharray': [3, 2]
                      }} />
                    </Source>
                  )}

                  {/* Geofence center markers */}
                  {geofences.filter(g => g.active !== false && isFinite(parseFloat(g.centerLat)) && isFinite(parseFloat(g.centerLng))).map(gf => (
                    <Marker key={gf.geofenceId}
                      longitude={parseFloat(gf.centerLng)} latitude={parseFloat(gf.centerLat)}>
                      <div title={`${gf.name} (${gf.radiusKm}km)`}
                        style={{ cursor: 'pointer', fontSize: '20px' }}
                        onClick={(e) => { e.stopPropagation(); if(confirm(`Delete geofence "${gf.name}"?`)) deleteGeofence(gf.geofenceId); }}>
                        🎯
                      </div>
                    </Marker>
                  ))}

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
            )}
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
