// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useRef, useEffect, useState } from 'react';
import { StatusIndicator, Box, Badge } from '@cloudscape-design/components';
import Map, { Source, Layer, Marker, Popup } from 'react-map-gl';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { getMapConfiguration, MapConfig } from '../../../utils/mapConfig';

interface TripMapProps {
  route?: Array<{ lat: number; lng: number; timestamp?: string; speed?: number }>;
  startLocation?: { lat: number; lng?: number; lon?: number; address?: string };
  endLocation?: { lat: number; lng?: number; lon?: number; address?: string };
  safetyEvents?: Array<{ latitude: number; longitude: number; eventType: string; timestamp: number; severity: string }>;
  height?: string;
  isActive?: boolean;
  vehicleType?: string;
  showStartEndMarkers?: boolean;
}

export function TripMap({ route, startLocation, endLocation, safetyEvents = [], height = '400px', isActive = false, vehicleType = 'Sedan', showStartEndMarkers = false }: TripMapProps) {
  const mapRef = useRef<any>(null);
  const [mapError, setMapError] = useState<string | null>(null);
  const [hoveredEvent, setHoveredEvent] = useState<any>(null);
  const [popupInfo, setPopupInfo] = useState<any>(null);
  const [mapConfig, setMapConfig] = useState<MapConfig | null>(null);

  // Setup map configuration
  useEffect(() => {
    const setupMap = async () => {
      try {
        const config = await getMapConfiguration();
        setMapConfig(config);
      } catch (error) {
        console.error('Failed to setup map configuration:', error);
        setMapError('Failed to load map configuration');
      }
    };
    setupMap();
  }, []);

  // Get vehicle icon based on type
  const getVehicleIcon = (type: string) => {
    const iconStyle = { width: '20px', height: '20px', fill: 'white' };
    
    switch (type?.toLowerCase()) {
      case 'suv':
      case 'truck':
        return (
          <svg viewBox="0 0 24 24" style={iconStyle}>
            <path d="M18,18.5A1.5,1.5 0 0,1 16.5,17A1.5,1.5 0 0,1 18,15.5A1.5,1.5 0 0,1 19.5,17A1.5,1.5 0 0,1 18,18.5M19.5,9.5L18.5,6.5H15.5V5A1,1 0 0,0 14.5,4H9.5A1,1 0 0,0 8.5,5V6.5H5.5L4.5,9.5V18.5A1,1 0 0,0 5.5,19.5H6.5A1,1 0 0,0 7.5,18.5V18H16.5V18.5A1,1 0 0,0 17.5,19.5H18.5A1,1 0 0,0 19.5,18.5V9.5M7.5,15.5A1.5,1.5 0 0,1 6,17A1.5,1.5 0 0,1 4.5,15.5A1.5,1.5 0 0,1 6,14A1.5,1.5 0 0,1 7.5,15.5M16.5,6.5H18.5L19.2,8.5H16.5V6.5M8.5,8.5H5.8L6.5,6.5H8.5V8.5Z"/>
          </svg>
        );
      case 'van':
        return (
          <svg viewBox="0 0 24 24" style={iconStyle}>
            <path d="M6,19A2,2 0 0,1 4,17A2,2 0 0,1 6,15A2,2 0 0,1 8,17A2,2 0 0,1 6,19M18,19A2,2 0 0,1 16,17A2,2 0 0,1 18,15A2,2 0 0,1 20,17A2,2 0 0,1 18,19M20,8H17V4H3C1.89,4 1,4.89 1,6V17H3A3,3 0 0,0 6,20A3,3 0 0,0 9,17H15A3,3 0 0,0 18,20A3,3 0 0,0 21,17H23V12L20,8M19,10L21.25,12H17V10H19Z"/>
          </svg>
        );
      default: // sedan, car, etc.
        return (
          <svg viewBox="0 0 24 24" style={iconStyle}>
            <path d="M5,11L6.5,6.5H17.5L19,11M17.5,16A1.5,1.5 0 0,1 16,14.5A1.5,1.5 0 0,1 17.5,13A1.5,1.5 0 0,1 19,14.5A1.5,1.5 0 0,1 17.5,16M6.5,16A1.5,1.5 0 0,1 5,14.5A1.5,1.5 0 0,1 6.5,13A1.5,1.5 0 0,1 8,14.5A1.5,1.5 0 0,1 6.5,16M18.92,6C18.72,5.42 18.16,5 17.5,5H6.5C5.84,5 5.28,5.42 5.08,6L3,12V20A1,1 0 0,0 4,21H5A1,1 0 0,0 6,20V19H18V20A1,1 0 0,0 19,21H20A1,1 0 0,0 21,20V12L18.92,6Z"/>
          </svg>
        );
    }
  };

  // fitBounds handled by onLoad callback on the Map component

  // Calculate initial view state from route data
  const getInitialViewState = () => {
    if (Array.isArray(route) && route.length > 0) {
      // Use center of route for initial view
      const lats = route.map(p => parseFloat(p.lat)).filter(lat => !isNaN(lat));
      const lngs = route.map(p => parseFloat(p.lng)).filter(lng => !isNaN(lng));
      
      if (lats.length > 0 && lngs.length > 0) {
        const centerLat = (Math.min(...lats) + Math.max(...lats)) / 2;
        const centerLng = (Math.min(...lngs) + Math.max(...lngs)) / 2;
        
        return {
          longitude: centerLng,
          latitude: centerLat,
          zoom: 13
        };
      }
    }
    
    // Fallback to start location or default
    return {
      longitude: startLocation?.lng || startLocation?.lon || -122.4,
      latitude: startLocation?.lat || 37.8,
      zoom: 14
    };
  };

  // Create route line from route points
  const routeGeoJSON = {
    type: 'Feature' as const,
    properties: {},
    geometry: {
      type: 'LineString' as const,
      coordinates: (Array.isArray(route) ? route : []).map(point => [parseFloat(point.lng), parseFloat(point.lat)]).filter(coord => !isNaN(coord[0]) && !isNaN(coord[1])) || [
        [startLocation?.lng || startLocation?.lon || 0, startLocation?.lat || 0],
        [endLocation?.lng || endLocation?.lon || 0, endLocation?.lat || 0]
      ]
    }
  };

  const mapStyle = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";

  // Don't render map until configuration is loaded
  if (!mapConfig) {
    return (
      <div style={{ height, width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <StatusIndicator type="loading">Loading map...</StatusIndicator>
      </div>
    );
  }

  const getSeverityColor = (severity: string | number) => {
    const s = String(severity ?? '').toLowerCase();
    switch (s) {
      case 'critical': case '4': return '#d32f2f';
      case 'high': case '3': return '#ff5722';
      case 'medium': case '2': return '#ff9800';
      case 'low': case '1': return '#ffc107';
      default: return '#ff9800';
    }
  };

  const getSeverityBadgeType = (severity: string | number) => {
    const s = String(severity ?? '').toLowerCase();
    switch (s) {
      case 'critical': case '4': return 'red';
      case 'high': case '3': return 'red';
      case 'medium': case '2': return 'orange';
      case 'low': return 'yellow';
      default: return 'orange';
    }
  };

  const formatEventType = (eventType: string) => {
    return eventType.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
  };

  if (mapError) {
    return (
      <div style={{ height, width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <StatusIndicator type="error">
          Failed to load map: {mapError}
        </StatusIndicator>
      </div>
    );
  }

  return (
    <div style={{ height, width: '100%' }}>
      <Map
        ref={mapRef}
        initialViewState={getInitialViewState()}
        style={{ width: '100%', height: '100%' }}
        mapStyle={mapConfig.mapStyle}
        {...(mapConfig.authOptions || {})}
        mapLib={maplibregl}
        onLoad={() => {
          // Fit bounds when map is fully loaded
          if (mapRef.current && Array.isArray(route) && route.length > 1) {
            const lngs = route.map((p: any) => parseFloat(p.lng)).filter((v: number) => !isNaN(v));
            const lats = route.map((p: any) => parseFloat(p.lat)).filter((v: number) => !isNaN(v));
            if (lngs.length > 0 && lats.length > 0) {
              const map = mapRef.current.getMap ? mapRef.current.getMap() : mapRef.current;
              map.fitBounds(
                [[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]],
                { padding: 60, maxZoom: 14, duration: 500 }
              );
            }
          }
        }}
        onError={(error) => {
          console.error('Map error:', error);
          setMapError(error.error?.message || 'Unknown map error');
        }}
      >
        {/* Route Line */}
        <Source id="route" type="geojson" data={routeGeoJSON}>
          <Layer
            id="route-line"
            type="line"
            paint={{
              'line-color': '#2196F3',
              'line-width': 4,
              'line-opacity': 0.8
            }}
          />
        </Source>

        {/* Start Marker */}
        {startLocation && (
          <Marker
            longitude={parseFloat(startLocation.lng || startLocation.lon || '0')}
            latitude={parseFloat(startLocation.lat || '0')}
          >
            <div style={{ 
              backgroundColor: showStartEndMarkers ? '#4CAF50' : '#037f0c', 
              borderRadius: '50%', 
              width: '30px', 
              height: '30px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'white',
              fontSize: '10px',
              fontWeight: 'bold',
              border: '3px solid white',
              boxShadow: '0 2px 4px rgba(0,0,0,0.3)'
            }}>
              {showStartEndMarkers ? 'START' : getVehicleIcon(vehicleType)}
            </div>
          </Marker>
        )}

        {/* End Marker - only show when showStartEndMarkers is true */}
        {showStartEndMarkers && endLocation && (
          <Marker
            longitude={parseFloat(endLocation.lng || endLocation.lon || '0')}
            latitude={parseFloat(endLocation.lat || '0')}
          >
            <div style={{ 
              backgroundColor: '#f44336', 
              borderRadius: '50%', 
              width: '30px', 
              height: '30px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'white',
              fontSize: '10px',
              fontWeight: 'bold',
              border: '3px solid white',
              boxShadow: '0 2px 4px rgba(0,0,0,0.3)'
            }}>
              END
            </div>
          </Marker>
        )}

        {/* Safety Event Markers - show as caution icons when showStartEndMarkers is true */}
        {showStartEndMarkers && safetyEvents.map((event, index) => (
          <Marker
            key={index}
            longitude={parseFloat(event.longitude?.toString() || '0')}
            latitude={parseFloat(event.latitude?.toString() || '0')}
          >
            <div 
              style={{ 
                backgroundColor: event.severity === 'HIGH' ? '#ff5722' : event.severity === 'MEDIUM' ? '#ff9800' : '#ffc107',
                borderRadius: '50%', 
                width: '24px', 
                height: '24px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'white',
                fontSize: '12px',
                fontWeight: 'bold',
                border: '2px solid white',
                boxShadow: '0 2px 4px rgba(0,0,0,0.3)',
                cursor: 'pointer'
              }}
              title={`${event.eventType} - ${event.severity}`}
            >
              ⚠️
            </div>
          </Marker>
        ))}

        {/* End Marker or Vehicle Icon for Active Trips */}
        {endLocation && (
          <Marker
            longitude={parseFloat(endLocation.lng || endLocation.lon || '0')}
            latitude={parseFloat(endLocation.lat || '0')}
          >
            <div style={{ 
              backgroundColor: isActive ? '#2196F3' : '#d32f2f', 
              borderRadius: isActive ? '6px' : '50%', 
              width: '32px', 
              height: '32px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'white',
              fontSize: '10px',
              fontWeight: 'bold',
              border: '2px solid white',
              boxShadow: '0 2px 6px rgba(0,0,0,0.2)'
            }}>
              {isActive ? (
                <svg width="18" height="12" viewBox="0 0 18 12" fill="none">
                  <path 
                    d="M2 8.5h1.5c0 1.1.9 2 2 2s2-.9 2-2h3c0 1.1.9 2 2 2s2-.9 2-2H16c.6 0 1-.4 1-1V4c0-.6-.4-1-1-1h-2l-1.5-2H9.5L8 3H2c-.6 0-1 .4-1 1v3.5c0 .6.4 1 1 1z" 
                    fill="white" 
                    stroke="white" 
                    strokeWidth="0.5"
                  />
                  <circle cx="5.5" cy="8.5" r="1" fill="#2196F3"/>
                  <circle cx="12.5" cy="8.5" r="1" fill="#2196F3"/>
                </svg>
              ) : 'END'}
            </div>
          </Marker>
        )}

        {/* Safety Event Markers */}
        {safetyEvents.filter(event => {
          const lat = parseFloat(event.latitude || event.lat);
          const lng = parseFloat(event.longitude || event.lng);
          return !isNaN(lat) && !isNaN(lng);
        }).map((event, index) => (
          <Marker
            key={`safety-${index}`}
            longitude={parseFloat(event.longitude || event.lng)}
            latitude={parseFloat(event.latitude || event.lat)}
          >
            <div 
              style={{ 
                backgroundColor: getSeverityColor(event.severity), 
                borderRadius: '50%', 
                width: '24px', 
                height: '24px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'white',
                fontSize: '12px',
                fontWeight: 'bold',
                border: '2px solid white',
                boxShadow: '0 2px 4px rgba(0,0,0,0.3)',
                cursor: 'pointer',
                transition: 'transform 0.2s ease',
                transform: hoveredEvent === index ? 'scale(1.2)' : 'scale(1)'
              }}
              onMouseEnter={() => {
                setHoveredEvent(index);
                setPopupInfo(event);
              }}
              onMouseLeave={() => {
                setHoveredEvent(null);
                setPopupInfo(null);
              }}
              onClick={() => {
                setPopupInfo(popupInfo === event ? null : event);
              }}
            >
              ⚠
            </div>
          </Marker>
        ))}

        {/* Safety Event Popup */}
        {popupInfo && (
          <Popup
            longitude={parseFloat(popupInfo.longitude || popupInfo.lng)}
            latitude={parseFloat(popupInfo.latitude || popupInfo.lat)}
            anchor="bottom"
            onClose={() => setPopupInfo(null)}
            closeButton={true}
            closeOnClick={false}
            style={{ zIndex: 1000 }}
          >
            <div style={{ padding: '8px', minWidth: '200px' }}>
              <Box variant="h4" margin={{ bottom: 'xs' }}>
                Safety Event
              </Box>
              <div style={{ marginBottom: '8px' }}>
                <Box variant="awsui-key-label">Event Type</Box>
                <Box>{formatEventType(popupInfo.eventType)}</Box>
              </div>
              <div style={{ marginBottom: '8px' }}>
                <Box variant="awsui-key-label">Severity</Box>
                <Badge color={getSeverityBadgeType(popupInfo.severity)}>
                  {popupInfo.severity.toUpperCase()}
                </Badge>
              </div>
              <div style={{ marginBottom: '8px' }}>
                <Box variant="awsui-key-label">Time</Box>
                <Box variant="small">
                  {new Date(popupInfo.timestamp * 1000).toLocaleString()}
                </Box>
              </div>
              <div>
                <Box variant="awsui-key-label">Location</Box>
                <Box variant="small" color="text-body-secondary">
                  {parseFloat(popupInfo.latitude || popupInfo.lat).toFixed(4)}, {parseFloat(popupInfo.longitude || popupInfo.lng).toFixed(4)}
                </Box>
              </div>
            </div>
          </Popup>
        )}
      </Map>
    </div>
  );
}
