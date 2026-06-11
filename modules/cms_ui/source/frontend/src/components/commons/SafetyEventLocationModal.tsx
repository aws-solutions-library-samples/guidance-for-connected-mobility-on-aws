import React, { useEffect, useRef, useLayoutEffect } from 'react';
import { Modal, Box, Header, SpaceBetween, Container, ColumnLayout, Badge } from '@cloudscape-design/components';
import maplibregl from 'maplibre-gl';
import { getMapAuthenticationOptions } from '../../utils/mapConfig';

interface SafetyEventLocationModalProps {
  visible: boolean;
  onDismiss: () => void;
  eventLocation: {
    latitude: number;
    longitude: number;
  };
  eventDetails?: {
    eventType: string;
    severity: string;
    vehicleId: string;
    timestamp: number;
    description?: string;
  };
  vehicleVinMap?: Record<string, string>;
}

export const SafetyEventLocationModal: React.FC<SafetyEventLocationModalProps> = ({
  visible,
  onDismiss,
  eventLocation,
  eventDetails,
  vehicleVinMap = {}
}) => {
  const map = useRef<maplibregl.Map | null>(null);
  const mapContainerRef = useRef<HTMLDivElement>(null);

  console.log('SafetyEventLocationModal props:', { 
    visible, 
    eventLocation, 
    eventDetails: JSON.stringify(eventDetails, null, 2)
  });

  const initializeMap = async (container: HTMLDivElement) => {
    console.log('initializeMap called with container:', container);
    if (map.current) {
      map.current.remove();
      map.current = null;
    }

    try {
      console.log('Initializing map...');
      
      // Use the same pattern as FleetVehicleMapView
      const runtimeConfig = (window as any).runtimeConfig;
      const locationServicesEnabled = runtimeConfig?.locationServices?.enabled;
      
      let mapStyle;
      let authOptions = {};
      
      if (locationServicesEnabled) {
        const region = runtimeConfig.locationServices.region || 'us-east-1';
        mapStyle = `https://maps.geo.${region}.amazonaws.com/v2/styles/Standard/descriptor`;
        const authHelper = await getMapAuthenticationOptions();
        authOptions = authHelper;
      } else {
        // Fallback to OpenStreetMap
        mapStyle = {
          version: 8,
          sources: {
            'osm': {
              type: 'raster',
              tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
              tileSize: 256,
              attribution: '© OpenStreetMap contributors'
            }
          },
          layers: [{
            id: 'osm',
            type: 'raster',
            source: 'osm'
          }]
        };
      }
      
      map.current = new maplibregl.Map({
        container,
        center: [eventLocation.longitude, eventLocation.latitude],
        zoom: 15,
        style: mapStyle,
        ...authOptions
      });

      console.log('Map created, waiting for load event');

      map.current.on('load', () => {
        console.log('Map loaded, adding marker');
        if (map.current) {
          new maplibregl.Marker({ color: 'red' })
            .setLngLat([eventLocation.longitude, eventLocation.latitude])
            .addTo(map.current);
        }
      });

    } catch (error) {
      console.error('Failed to initialize map:', error);
    }
  };

  const setMapContainer = (element: HTMLDivElement | null) => {
    console.log('setMapContainer called with:', element, 'visible:', visible);
    mapContainerRef.current = element;
    if (element && visible) {
      console.log('Calling initializeMap');
      initializeMap(element);
    }
  };

  const getSeverityColor = (severity: string | number) => {
    switch (String(severity ?? "").toLowerCase()) {
      case 'high': return 'red' as const;
      case 'medium': return 'blue' as const;
      case 'low': return 'green' as const;
      default: return 'grey' as const;
    }
  };

  useEffect(() => {
    return () => {
      if (map.current) {
        map.current.remove();
        map.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (visible && mapContainerRef.current) {
      initializeMap(mapContainerRef.current);
    }
  }, [visible, eventLocation]);

  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      header={<Header variant="h2">Safety Event Location</Header>}
      size="large"
    >
      <SpaceBetween size="m">
        {eventDetails && (
          <Container>
            <ColumnLayout columns={2} variant="text-grid">
              <div>
                <Box variant="awsui-key-label">Event Type</Box>
                <div>{eventDetails.eventType?.replace(/_/g, ' ') || 'Unknown'}</div>
              </div>
              <div>
                <Box variant="awsui-key-label">Severity</Box>
                <Badge color={getSeverityColor(eventDetails.severity)}>
                  {eventDetails.severity || 'Unknown'}
                </Badge>
              </div>
              <div>
                <Box variant="awsui-key-label">Vehicle VIN</Box>
                <div>{vehicleVinMap[eventDetails.vehicleId] || eventDetails.vehicleId || 'Unknown'}</div>
              </div>
              <div>
                <Box variant="awsui-key-label">Time</Box>
                <div>
                  {(() => {
                    const timestamp = eventDetails.timestamp > 1e12 ? eventDetails.timestamp : eventDetails.timestamp * 1000;
                    return new Date(timestamp).toLocaleString();
                  })()}
                </div>
              </div>
              <div>
                <Box variant="awsui-key-label">Latitude</Box>
                <div>{eventLocation.latitude.toFixed(6)}</div>
              </div>
              <div>
                <Box variant="awsui-key-label">Longitude</Box>
                <div>{eventLocation.longitude.toFixed(6)}</div>
              </div>
            </ColumnLayout>
          </Container>
        )}
        
        <Container>
          <Box variant="h3">Event Location</Box>
          <div 
            ref={setMapContainer} 
            style={{ 
              width: '100%', 
              height: '400px',
              borderRadius: '8px',
              backgroundColor: 'var(--color-background-container-content, #f5f5f5)',
              border: '1px solid #ddd'
            }} 
          />
        </Container>

        {eventDetails?.description && (
          <Container>
            <Box variant="h3">Event Description</Box>
            <Box>{eventDetails.description}</Box>
          </Container>
        )}
      </SpaceBetween>
    </Modal>
  );
};
