// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  Button,
  Table,
  Badge,
  ColumnLayout,
  KeyValuePairs,
  ProgressBar,
  StatusIndicator,
  Modal,
  Cards,
  Link
} from '@cloudscape-design/components';

// Map component for route visualization
import Map, { NavigationControl, Marker, Source, Layer } from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';
import { getMapConfiguration, MapConfig } from '../../../utils/mapConfig';
import { useAuth } from '../../../auth/useAuth';

interface RoutePoint {
  timestamp: string;
  latitude: number;
  longitude: number;
  altitude: number;
  speed_kmh: number;
  heading: number;
  acceleration_x: number;
  acceleration_y: number;
  fuel_level: number;
  engine_rpm: number;
  driver_score: number;
}

interface SafetyEvent {
  event_id: string;
  timestamp: string;
  event_type: string;
  severity: string;
  latitude: number;
  longitude: number;
  details: any;
}

interface TripRecord {
  vin: string;
  trip_id: string;
  vehicle_type: string;
  trip_purpose: string;
  planned_distance_km: number;
  actual_distance_km: number;
  planned_avg_speed_kmh: number;
  actual_avg_speed_kmh: number;
  trip_start_time: string;
  trip_end_time: string;
  actual_duration_minutes: number;
  route_points: RoutePoint[];
  total_telemetry_points: number;
  max_speed_kmh: number;
  min_speed_kmh: number;
  avg_driver_score: number;
  total_safety_events: number;
  critical_events: number;
  high_events: number;
  medium_events: number;
  low_events: number;
  hard_braking_events: number;
  hard_acceleration_events: number;
  lane_departure_events: number;
  speeding_events: number;
  safety_event_details: SafetyEvent[];
  trip_status: string;
  route_efficiency_percent: number;
  processed_at: string;
}

interface TripRouteVisualizationProps {
  vin: string;
  onTripSelect?: (trip: TripRecord) => void;
}

export const TripRouteVisualization: React.FC<TripRouteVisualizationProps> = ({
  vin,
  onTripSelect
}) => {
  const [trips, setTrips] = useState<TripRecord[]>([]);
  const [selectedTrip, setSelectedTrip] = useState<TripRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [showRouteModal, setShowRouteModal] = useState(false);
  const [mapConfig, setMapConfig] = useState<MapConfig | null>(null);
  const [mapViewState, setMapViewState] = useState({
    longitude: -122.4194,
    latitude: 37.7749,
    zoom: 12
  });
  // Map re-fetch race fix (mirrors commit `2f5c435`): re-fire once auth.user
  // hydrates so idToken is in storage before getMapConfiguration runs.
  const auth = useAuth();

  // Setup map configuration
  useEffect(() => {
    const setupMap = async () => {
      try {
        const config = await getMapConfiguration();
        setMapConfig(config);
      } catch (error) {
        console.error('Failed to setup map configuration:', error);
      }
    };
    setupMap();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth.user]);

  // Fetch trips for the vehicle
  useEffect(() => {
    fetchTripsForVehicle();
  }, [vin]);

  const fetchTripsForVehicle = async () => {
    try {
      setLoading(true);
      
      // In a real implementation, this would call your backend API
      // For now, we'll simulate with sample data
      const response = await fetch(`/api/vehicles/${vin}/trips`);
      
      if (response.ok) {
        const data = await response.json();
        setTrips(data.trips || []);
      } else {
        // Fallback to sample data for demonstration
        setTrips(generateSampleTrips());
      }
    } catch (error) {
      console.error('Error fetching trips:', error);
      // Use sample data as fallback
      setTrips(generateSampleTrips());
    } finally {
      setLoading(false);
    }
  };

  const generateSampleTrips = (): TripRecord[] => {
    const sampleRoutePoints: RoutePoint[] = [
      {
        timestamp: '2024-12-01T08:00:00Z',
        latitude: 37.7749,
        longitude: -122.4194,
        altitude: 50.0,
        speed_kmh: 0.0,
        heading: 0.0,
        acceleration_x: 0.0,
        acceleration_y: 0.0,
        fuel_level: 85.0,
        engine_rpm: 800.0,
        driver_score: 95.0
      },
      {
        timestamp: '2024-12-01T08:05:00Z',
        latitude: 37.7849,
        longitude: -122.4094,
        altitude: 55.0,
        speed_kmh: 45.0,
        heading: 45.0,
        acceleration_x: 2.5,
        acceleration_y: 0.1,
        fuel_level: 84.5,
        engine_rpm: 2200.0,
        driver_score: 92.0
      },
      {
        timestamp: '2024-12-01T08:15:00Z',
        latitude: 37.7949,
        longitude: -122.3994,
        altitude: 60.0,
        speed_kmh: 35.0,
        heading: 90.0,
        acceleration_x: -1.2,
        acceleration_y: 0.0,
        fuel_level: 84.0,
        engine_rpm: 1800.0,
        driver_score: 88.0
      },
      {
        timestamp: '2024-12-01T08:30:00Z',
        latitude: 37.8049,
        longitude: -122.3894,
        altitude: 45.0,
        speed_kmh: 0.0,
        heading: 90.0,
        acceleration_x: -3.5,
        acceleration_y: 0.0,
        fuel_level: 83.5,
        engine_rpm: 800.0,
        driver_score: 85.0
      }
    ];

    const sampleSafetyEvents: SafetyEvent[] = [
      {
        event_id: 'safety_001',
        timestamp: '2024-12-01T08:12:00Z',
        event_type: 'hard_braking',
        severity: 'high',
        latitude: 37.7899,
        longitude: -122.4044,
        details: {
          deceleration: 11.2,
          brake_pressure: 95,
          abs_activated: true
        }
      }
    ];

    return [
      {
        vin: vin,
        trip_id: `trip_${vin}_20241201_01`,
        vehicle_type: 'delivery_truck',
        trip_purpose: 'delivery_stop_1',
        planned_distance_km: 8.5,
        actual_distance_km: 9.2,
        planned_avg_speed_kmh: 35.0,
        actual_avg_speed_kmh: 32.5,
        trip_start_time: '2024-12-01T08:00:00Z',
        trip_end_time: '2024-12-01T08:30:00Z',
        actual_duration_minutes: 30,
        route_points: sampleRoutePoints,
        total_telemetry_points: 4,
        max_speed_kmh: 45.0,
        min_speed_kmh: 0.0,
        avg_driver_score: 90.0,
        total_safety_events: 1,
        critical_events: 0,
        high_events: 1,
        medium_events: 0,
        low_events: 0,
        hard_braking_events: 1,
        hard_acceleration_events: 0,
        lane_departure_events: 0,
        speeding_events: 0,
        safety_event_details: sampleSafetyEvents,
        trip_status: 'completed',
        route_efficiency_percent: 108.2,
        processed_at: new Date().toISOString()
      },
      {
        vin: vin,
        trip_id: `trip_${vin}_20241201_02`,
        vehicle_type: 'delivery_truck',
        trip_purpose: 'delivery_stop_2',
        planned_distance_km: 12.0,
        actual_distance_km: 11.8,
        planned_avg_speed_kmh: 40.0,
        actual_avg_speed_kmh: 38.5,
        trip_start_time: '2024-12-01T09:00:00Z',
        trip_end_time: '2024-12-01T09:45:00Z',
        actual_duration_minutes: 45,
        route_points: sampleRoutePoints.map(p => ({
          ...p,
          latitude: p.latitude + 0.01,
          longitude: p.longitude + 0.01
        })),
        total_telemetry_points: 6,
        max_speed_kmh: 52.0,
        min_speed_kmh: 0.0,
        avg_driver_score: 88.0,
        total_safety_events: 0,
        critical_events: 0,
        high_events: 0,
        medium_events: 0,
        low_events: 0,
        hard_braking_events: 0,
        hard_acceleration_events: 0,
        lane_departure_events: 0,
        speeding_events: 0,
        safety_event_details: [],
        trip_status: 'completed',
        route_efficiency_percent: 98.3,
        processed_at: new Date().toISOString()
      }
    ];
  };

  const handleTripSelect = (trip: TripRecord) => {
    setSelectedTrip(trip);
    
    // Center map on trip route
    if (trip.route_points.length > 0) {
      const firstPoint = trip.route_points[0];
      setMapViewState({
        longitude: firstPoint.longitude,
        latitude: firstPoint.latitude,
        zoom: 13
      });
    }
    
    if (onTripSelect) {
      onTripSelect(trip);
    }
  };

  const showRouteOnMap = (trip: TripRecord) => {
    setSelectedTrip(trip);
    setShowRouteModal(true);
    
    // Fit map to route bounds
    if (trip.route_points.length > 0) {
      const lats = trip.route_points.map(p => p.latitude);
      const lngs = trip.route_points.map(p => p.longitude);
      
      const centerLat = (Math.min(...lats) + Math.max(...lats)) / 2;
      const centerLng = (Math.min(...lngs) + Math.max(...lngs)) / 2;
      
      setMapViewState({
        longitude: centerLng,
        latitude: centerLat,
        zoom: 12
      });
    }
  };

  const getSeverityBadge = (severity: string) => {
    const colorMap = {
      critical: 'red',
      high: 'red',
      medium: 'yellow',
      low: 'blue'
    } as const;
    
    return <Badge color={colorMap[severity as keyof typeof colorMap] || 'grey'}>{severity}</Badge>;
  };

  const getStatusBadge = (status: string) => {
    const colorMap = {
      completed: 'green',
      in_progress: 'blue',
      cancelled: 'red'
    } as const;
    
    return <Badge color={colorMap[status as keyof typeof colorMap] || 'grey'}>{status}</Badge>;
  };

  const createRouteGeoJSON = (routePoints: RoutePoint[]) => {
    return {
      type: 'Feature',
      properties: {},
      geometry: {
        type: 'LineString',
        coordinates: routePoints.map(point => [point.longitude, point.latitude])
      }
    };
  };

  const tripTableColumns = [
    {
      id: 'trip_id',
      header: 'Trip ID',
      cell: (item: TripRecord) => (
        <Link onFollow={() => handleTripSelect(item)}>
          {item.trip_id.split('_').pop()}
        </Link>
      ),
      sortingField: 'trip_id'
    },
    {
      id: 'trip_purpose',
      header: 'Purpose',
      cell: (item: TripRecord) => item.trip_purpose.replace(/_/g, ' '),
      sortingField: 'trip_purpose'
    },
    {
      id: 'trip_start_time',
      header: 'Start Time',
      cell: (item: TripRecord) => new Date(item.trip_start_time).toLocaleString(),
      sortingField: 'trip_start_time'
    },
    {
      id: 'duration',
      header: 'Duration',
      cell: (item: TripRecord) => `${item.actual_duration_minutes} min`,
      sortingField: 'actual_duration_minutes'
    },
    {
      id: 'distance',
      header: 'Distance',
      cell: (item: TripRecord) => `${item.actual_distance_km.toFixed(1)} km`,
      sortingField: 'actual_distance_km'
    },
    {
      id: 'avg_speed',
      header: 'Avg Speed',
      cell: (item: TripRecord) => `${item.actual_avg_speed_kmh.toFixed(1)} km/h`,
      sortingField: 'actual_avg_speed_kmh'
    },
    {
      id: 'safety_events',
      header: 'Safety Events',
      cell: (item: TripRecord) => (
        <Box>
          {item.total_safety_events > 0 ? (
            <Badge color="red">{item.total_safety_events}</Badge>
          ) : (
            <Badge color="green">0</Badge>
          )}
        </Box>
      ),
      sortingField: 'total_safety_events'
    },
    {
      id: 'driver_score',
      header: 'Driver Score',
      cell: (item: TripRecord) => (
        <ProgressBar
          value={item.avg_driver_score}
          additionalInfo={`${item.avg_driver_score.toFixed(0)}/100`}
          variant={item.avg_driver_score >= 90 ? 'success' : item.avg_driver_score >= 70 ? 'warning' : 'error'}
        />
      ),
      sortingField: 'avg_driver_score'
    },
    {
      id: 'status',
      header: 'Status',
      cell: (item: TripRecord) => getStatusBadge(item.trip_status),
      sortingField: 'trip_status'
    },
    {
      id: 'actions',
      header: 'Actions',
      cell: (item: TripRecord) => (
        <Button
          variant="inline-link"
          iconName="external"
          onClick={() => showRouteOnMap(item)}
        >
          View Route
        </Button>
      )
    }
  ];

  return (
    <SpaceBetween direction="vertical" size="l">
      <Container
        header={
          <Header
            variant="h2"
            description="Complete trip history with routes and performance metrics"
            actions={
              <Button
                iconName="refresh"
                onClick={fetchTripsForVehicle}
                loading={loading}
              >
                Refresh
              </Button>
            }
          >
            Trip History & Routes
          </Header>
        }
      >
        <Table
          columnDefinitions={tripTableColumns}
          items={trips}
          loading={loading}
          loadingText="Loading trip history..."
          empty={
            <Box textAlign="center" color="inherit">
              <b>No trips found</b>
              <Box variant="p" color="inherit">
                No trip data available for this vehicle.
              </Box>
            </Box>
          }
          header={
            <Header
              counter={`(${trips.length})`}
              description="Recent trips with route data from ignition-based detection"
            >
              Trips
            </Header>
          }
        />
      </Container>

      {/* Route Visualization Modal */}
      <Modal
        onDismiss={() => setShowRouteModal(false)}
        visible={showRouteModal}
        size="max"
        header={selectedTrip ? `Route: ${selectedTrip.trip_purpose.replace(/_/g, ' ')}` : 'Trip Route'}
      >
        {selectedTrip && (
          <SpaceBetween direction="vertical" size="l">
            {/* Trip Summary */}
            <ColumnLayout columns={4} variant="text-grid">
              <KeyValuePairs
                columns={1}
                items={[
                  { label: 'Duration', value: `${selectedTrip.actual_duration_minutes} minutes` },
                  { label: 'Distance', value: `${selectedTrip.actual_distance_km.toFixed(1)} km` }
                ]}
              />
              <KeyValuePairs
                columns={1}
                items={[
                  { label: 'Avg Speed', value: `${selectedTrip.actual_avg_speed_kmh.toFixed(1)} km/h` },
                  { label: 'Max Speed', value: `${selectedTrip.max_speed_kmh.toFixed(1)} km/h` }
                ]}
              />
              <KeyValuePairs
                columns={1}
                items={[
                  { label: 'Driver Score', value: `${selectedTrip.avg_driver_score.toFixed(0)}/100` },
                  { label: 'Safety Events', value: selectedTrip.total_safety_events.toString() }
                ]}
              />
              <KeyValuePairs
                columns={1}
                items={[
                  { label: 'Route Points', value: selectedTrip.total_telemetry_points.toString() },
                  { label: 'Efficiency', value: `${selectedTrip.route_efficiency_percent.toFixed(1)}%` }
                ]}
              />
            </ColumnLayout>

            {/* Interactive Map */}
            <Box>
              <div style={{ height: '500px', width: '100%', borderRadius: '8px', overflow: 'hidden' }}>
                {!mapConfig ? (
                  <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <StatusIndicator type="loading">Loading map...</StatusIndicator>
                  </div>
                ) : (
                  <Map
                    {...mapViewState}
                    onMove={evt => setMapViewState(evt.viewState)}
                    mapStyle={mapConfig.mapStyle}
                    {...(mapConfig.authOptions || {})}
                    style={{ width: '100%', height: '100%' }}
                  >
                        type: 'raster',
                        tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
                        tileSize: 256,
                        attribution: '© OpenStreetMap contributors'
                      }
                    },
                    layers: [
                      {
                        id: 'osm-tiles',
                        type: 'raster',
                        source: 'osm-tiles'
                      }
                    ]
                  }}
                  style={{ width: '100%', height: '100%' }}
                >
                  <NavigationControl position="top-right" />
                  
                  {/* Route Line */}
                  {selectedTrip.route_points.length > 1 && (
                    <Source
                      id="route"
                      type="geojson"
                      data={createRouteGeoJSON(selectedTrip.route_points)}
                    >
                      <Layer
                        id="route-line"
                        type="line"
                        paint={{
                          'line-color': '#007eb3',
                          'line-width': 4,
                          'line-opacity': 0.8
                        }}
                      />
                    </Source>
                  )}
                  
                  {/* Start Marker */}
                  {selectedTrip.route_points.length > 0 && (
                    <Marker
                      longitude={selectedTrip.route_points[0].longitude}
                      latitude={selectedTrip.route_points[0].latitude}
                      color="green"
                    />
                  )}
                  
                  {/* End Marker */}
                  {selectedTrip.route_points.length > 1 && (
                    <Marker
                      longitude={selectedTrip.route_points[selectedTrip.route_points.length - 1].longitude}
                      latitude={selectedTrip.route_points[selectedTrip.route_points.length - 1].latitude}
                      color="red"
                    />
                  )}
                  
                  {/* Safety Event Markers */}
                  {selectedTrip.safety_event_details.map((event, index) => (
                    <Marker
                      key={event.event_id}
                      longitude={event.longitude}
                      latitude={event.latitude}
                      color="orange"
                    />
                  ))}
                </Map>
              </div>
            </Box>

            {/* Safety Events Details */}
            {selectedTrip.safety_event_details.length > 0 && (
              <Container header={<Header variant="h3">Safety Events</Header>}>
                <Cards
                  cardDefinition={{
                    header: (item: SafetyEvent) => item.event_type.replace(/_/g, ' '),
                    sections: [
                      {
                        id: 'severity',
                        header: 'Severity',
                        content: (item: SafetyEvent) => getSeverityBadge(item.severity)
                      },
                      {
                        id: 'time',
                        header: 'Time',
                        content: (item: SafetyEvent) => new Date(item.timestamp).toLocaleString()
                      },
                      {
                        id: 'location',
                        header: 'Location',
                        content: (item: SafetyEvent) => `${item.latitude.toFixed(4)}, ${item.longitude.toFixed(4)}`
                      }
                    ]
                  }}
                  cardsPerRow={[{ cards: 1 }, { minWidth: 500, cards: 2 }]}
                  items={selectedTrip.safety_event_details}
                />
              </Container>
            )}
          </SpaceBetween>
        )}
      </Modal>
    </SpaceBetween>
  );
};
