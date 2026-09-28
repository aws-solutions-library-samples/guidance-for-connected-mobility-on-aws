import React, { useMemo, useState, useEffect } from 'react';
import { Modal, Box, StatusIndicator } from '@cloudscape-design/components';
import Map, { Source, Layer, Marker, NavigationControl } from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';
import { withIdentityPoolId } from '@aws/amazon-location-utilities-auth-helper';

interface Props {
  visible: boolean;
  onDismiss: () => void;
  geofence: { name: string; centerLat: number; centerLng: number; radiusKm: number } | null;
  vehicleLat?: number;
  vehicleLng?: number;
}

export default function GeofenceMapModal({ visible, onDismiss, geofence, vehicleLat, vehicleLng }: Props) {
  const [authHelper, setAuthHelper] = useState<any>(null);

  useEffect(() => {
    if (!visible) return;
    const rc = (window as any).runtimeConfig;
    if (rc?.awsCredentials?.identityPoolId) {
      withIdentityPoolId(rc.awsCredentials.identityPoolId)
        .then(helper => setAuthHelper(helper))
        .catch(e => console.error('Auth helper error:', e));
    }
  }, [visible]);

  if (!geofence) return null;

  const lat = parseFloat(String(geofence.centerLat));
  const lng = parseFloat(String(geofence.centerLng));
  const r = parseFloat(String(geofence.radiusKm));

  const circleGeoJSON = useMemo(() => {
    const pts = 64;
    const coords = [];
    for (let i = 0; i <= pts; i++) {
      const angle = (i / pts) * 2 * Math.PI;
      const dlat = (r / 111.32) * Math.cos(angle);
      const dlng = (r / (111.32 * Math.cos(lat * Math.PI / 180))) * Math.sin(angle);
      coords.push([lng + dlng, lat + dlat]);
    }
    return { type: 'Feature' as const, geometry: { type: 'Polygon' as const, coordinates: [coords] }, properties: {} };
  }, [lat, lng, r]);

  const rc = (window as any).runtimeConfig;
  const region = rc?.locationServices?.region || 'us-east-1';
  const mapStyle = `https://maps.geo.${region}.amazonaws.com/v2/styles/Standard/descriptor`;

  return (
    <Modal visible={visible} onDismiss={onDismiss} size="large"
      header={`${geofence.name} — ${r} km radius`}>
      <div style={{ height: '450px', borderRadius: '8px', overflow: 'hidden' }}>
        {authHelper ? (
          <Map
            initialViewState={{ longitude: lng, latitude: lat, zoom: Math.max(10, 14 - Math.log2(r)) }}
            mapStyle={mapStyle}
            {...(authHelper.getMapAuthenticationOptions?.() || {})}
            style={{ width: '100%', height: '100%' }}
          >
            <NavigationControl position="top-right" />
            <Source id="gf" type="geojson" data={circleGeoJSON}>
              <Layer id="gf-fill" type="fill" paint={{ 'fill-color': '#ff6b6b', 'fill-opacity': 0.15 }} />
              <Layer id="gf-border" type="line" paint={{ 'line-color': '#ff6b6b', 'line-width': 2, 'line-dasharray': [3, 2] }} />
            </Source>
            <Marker longitude={lng} latitude={lat}>
              <div style={{ width: 12, height: 12, borderRadius: '50%', background: '#ff6b6b', border: '2px solid white' }} />
            </Marker>
            {vehicleLat && vehicleLng && (
              <Marker longitude={vehicleLng} latitude={vehicleLat}>
                <div style={{ width: 14, height: 14, borderRadius: '50%', background: '#0073bb', border: '2px solid white' }} />
              </Marker>
            )}
          </Map>
        ) : (
          <Box textAlign="center" padding="xxl">
            <StatusIndicator type="loading">Loading map...</StatusIndicator>
          </Box>
        )}
      </div>
      <Box variant="small" color="text-body-secondary" padding={{ top: 's' }}>
        Center: {lat.toFixed(4)}, {lng.toFixed(4)} — Radius: {r} km
      </Box>
    </Modal>
  );
}
