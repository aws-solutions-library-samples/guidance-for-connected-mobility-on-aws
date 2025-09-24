// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useRef, useEffect, useState } from 'react';
import {
  Modal,
  SpaceBetween,
  ColumnLayout,
  Box,
  Container,
  Header,
  Badge,
  StatusIndicator,
  Table
} from '@cloudscape-design/components';
import Map, { Source, Layer, Marker, Popup } from 'react-map-gl';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';

interface Trip {
  trip_id: string;
  vin: string;
  start_time: string;
  end_time: string;
  start_location: {
    lat: number;
    lon: number;
    address: string;
  };
  end_location: {
    lat: number;
    lon: number;
    address: string;
  };
  distance_miles: number;
  duration_minutes: number;
  avg_speed: number;
  max_speed: number;
  fuel_consumed: number;
  route_points: Array<{
    lat: number;
    lon: number;
    timestamp: string;
    speed: number;
  }>;
  safety_events: number;
  driver_score: number;
  purpose: string;
  status: 'completed' | 'in_progress' | 'cancelled';
}

interface SafetyAlert {
  id: string;
  lat: number;
  lon: number;
  type: string;
  severity: 'low' | 'medium' | 'high';
  timestamp: string;
  description: string;
}

interface RouteMapModalProps {
  trip: Trip;
  onDismiss: () => void;
  safetyAlerts?: SafetyAlert[];
}

export function RouteMapModal({ trip, onDismiss, safetyAlerts = [] }: RouteMapModalProps) {
  const mapRef = useRef<any>();
  const [selectedAlert, setSelectedAlert] = useState<SafetyAlert | null>(null);
  const [viewState, setViewState] = useState({
    longitude: trip.start_location.lon || -122.4194,
    latitude: trip.start_location.lat || 37.7749,
    zoom: 12
  });

  // Create route line from route points
  const routeGeoJSON = {
    type: 'Feature' as const,
    properties: {},
    geometry: {
      type: 'LineString' as const,
      coordinates: trip.route_points?.map(point => [point.lon, point.lat]) || [
        [trip.start_location.lon, trip.start_location.lat],
        [trip.end_location.lon, trip.end_location.lat]
      ]
    }
  };

  // Fit map to route bounds on load
  useEffect(() => {
    if (mapRef.current && trip.route_points?.length > 0) {
      const coordinates = trip.route_points.map(point => [point.lon, point.lat]);
      const bounds = new maplibregl.LngLatBounds();
      coordinates.forEach(coord => bounds.extend(coord));
      
      mapRef.current.getMap().fitBounds(bounds, { padding: 50 });
    }
  }, [trip.route_points]);

  const getSeverityColor = (severity: string) => {
    switch (severity) {
      case 'high': return '#d13212';
      case 'medium': return '#ff9900';
      case 'low': return '#037f0c';
      default: return '#0073bb';
    }
  };

  const getSeverityIcon = (severity: string) => {
    switch (severity) {
      case 'high': return '🚨';
      case 'medium': return '⚠️';
      case 'low': return 'ℹ️';
      default: return '📍';
    }
  };

  return (
    <Modal
      onDismiss={onDismiss}
      visible={true}
      size="max"
      header={`Trip Route - ${new Date(trip.start_time).toLocaleDateString()}`}
    >
      <SpaceBetween size="m">
        {/* Trip Summary */}
        <ColumnLayout columns={4} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Trip ID</Box>
            <Box variant="code" fontSize="body-s">{trip.trip_id}</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Duration</Box>
            <Box>{trip.duration_minutes || 0} minutes</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Distance</Box>
            <Box>{trip.distance_miles?.toFixed(1) || 0} miles</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Driver Score</Box>
            <Box>{trip.driver_score ? `${trip.driver_score}/100` : 'No score'}</Box>
          </div>
        </ColumnLayout>

        <ColumnLayout columns={3} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Avg Speed</Box>
            <Box>{trip.avg_speed?.toFixed(1) || 0} mph</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Max Speed</Box>
            <Box>{trip.max_speed?.toFixed(1) || 0} mph</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Safety Events</Box>
            <Badge color={safetyAlerts.length > 0 ? 'red' : 'green'}>
              {safetyAlerts.length} alerts
            </Badge>
          </div>
        </ColumnLayout>

        {/* Interactive Map */}
        <Container
          header={<Header variant="h3">Route Map with Safety Alerts</Header>}
        >
          <div style={{ height: '500px', width: '100%' }}>
            <Map
              ref={mapRef}
              {...viewState}
              onMove={evt => setViewState(evt.viewState)}
              mapStyle="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"
              style={{ width: '100%', height: '100%' }}
              mapLib={maplibregl}
            >
              {/* Route Line */}
              <Source id="route" type="geojson" data={routeGeoJSON}>
                <Layer
                  id="route-line"
                  type="line"
                  paint={{
                    'line-color': '#0073bb',
                    'line-width': 4,
                    'line-opacity': 0.8
                  }}
                />
              </Source>

              {/* Start Marker */}
              <Marker
                longitude={trip.start_location.lon}
                latitude={trip.start_location.lat}
                color="green"
              >
                <div style={{ 
                  backgroundColor: '#037f0c', 
                  borderRadius: '50%', 
                  width: '20px', 
                  height: '20px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: 'white',
                  fontSize: '12px',
                  fontWeight: 'bold'
                }}>
                  S
                </div>
              </Marker>

              {/* End Marker */}
              <Marker
                longitude={trip.end_location.lon}
                latitude={trip.end_location.lat}
                color="red"
              >
                <div style={{ 
                  backgroundColor: '#d13212', 
                  borderRadius: '50%', 
                  width: '20px', 
                  height: '20px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: 'white',
                  fontSize: '12px',
                  fontWeight: 'bold'
                }}>
                  E
                </div>
              </Marker>

              {/* Safety Alert Markers */}
              {safetyAlerts.map((alert) => (
                <Marker
                  key={alert.id}
                  longitude={alert.lon}
                  latitude={alert.lat}
                  onClick={() => setSelectedAlert(alert)}
                >
                  <div style={{ 
                    backgroundColor: getSeverityColor(alert.severity),
                    borderRadius: '50%',
                    width: '24px',
                    height: '24px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    cursor: 'pointer',
                    border: '2px solid white',
                    boxShadow: '0 2px 4px rgba(0,0,0,0.3)'
                  }}>
                    {getSeverityIcon(alert.severity)}
                  </div>
                </Marker>
              ))}

              {/* Safety Alert Popup */}
              {selectedAlert && (
                <Popup
                  longitude={selectedAlert.lon}
                  latitude={selectedAlert.lat}
                  onClose={() => setSelectedAlert(null)}
                  closeButton={true}
                  closeOnClick={false}
                >
                  <div style={{ padding: '8px', minWidth: '200px' }}>
                    <Box variant="h4">{selectedAlert.type}</Box>
                    <SpaceBetween size="xs">
                      <Badge color={selectedAlert.severity === 'high' ? 'red' : selectedAlert.severity === 'medium' ? 'blue' : 'green'}>
                        {selectedAlert.severity.toUpperCase()}
                      </Badge>
                      <Box variant="small">
                        {new Date(selectedAlert.timestamp).toLocaleString()}
                      </Box>
                      <Box variant="small">
                        {selectedAlert.description}
                      </Box>
                    </SpaceBetween>
                  </div>
                </Popup>
              )}
            </Map>
          </div>
        </Container>

        {/* Route Details */}
        <Container
          header={<Header variant="h3">Route Details</Header>}
        >
          <SpaceBetween size="s">
            <ColumnLayout columns={2} variant="text-grid">
              <div>
                <Box variant="awsui-key-label">Start Location</Box>
                <Box>{trip.start_location?.address || 'Start location unknown'}</Box>
                <Box variant="small" color="text-body-secondary">
                  {new Date(trip.start_time).toLocaleString()}
                </Box>
              </div>
              <div>
                <Box variant="awsui-key-label">End Location</Box>
                <Box>{trip.end_location?.address || 'End location unknown'}</Box>
                <Box variant="small" color="text-body-secondary">
                  {new Date(trip.end_time).toLocaleString()}
                </Box>
              </div>
            </ColumnLayout>
            
            {/* Safety Alerts Table */}
            {safetyAlerts.length > 0 && (
              <>
                <Box variant="h4">Safety Alerts Along Route</Box>
                <Table
                  columnDefinitions={[
                    {
                      id: "type",
                      header: "Alert Type",
                      cell: item => item.type
                    },
                    {
                      id: "severity",
                      header: "Severity",
                      cell: item => (
                        <Badge color={item.severity === 'high' ? 'red' : item.severity === 'medium' ? 'blue' : 'green'}>
                          {item.severity.toUpperCase()}
                        </Badge>
                      )
                    },
                    {
                      id: "timestamp",
                      header: "Time",
                      cell: item => new Date(item.timestamp).toLocaleString()
                    },
                    {
                      id: "location",
                      header: "Location",
                      cell: item => `${item.lat.toFixed(4)}, ${item.lon.toFixed(4)}`
                    },
                    {
                      id: "description",
                      header: "Description",
                      cell: item => item.description
                    }
                  ]}
                  items={safetyAlerts}
                  variant="embedded"
                />
              </>
            )}

            {/* Route Points Table */}
            {trip.route_points && trip.route_points.length > 0 && (
              <>
                <Box variant="h4">Route Points ({trip.route_points.length} GPS points)</Box>
                <Table
                  columnDefinitions={[
                    {
                      id: "timestamp",
                      header: "Time",
                      cell: item => new Date(item.timestamp).toLocaleTimeString()
                    },
                    {
                      id: "location",
                      header: "Location",
                      cell: item => `${item.lat.toFixed(4)}, ${item.lon.toFixed(4)}`
                    },
                    {
                      id: "speed",
                      header: "Speed",
                      cell: item => `${item.speed} mph`
                    }
                  ]}
                  items={trip.route_points.slice(0, 10)} // Show first 10 points
                  variant="embedded"
                />
                {trip.route_points.length > 10 && (
                  <Box variant="small" color="text-body-secondary">
                    Showing first 10 of {trip.route_points.length} route points
                  </Box>
                )}
              </>
            )}
          </SpaceBetween>
        </Container>
      </SpaceBetween>
    </Modal>
  );
}
