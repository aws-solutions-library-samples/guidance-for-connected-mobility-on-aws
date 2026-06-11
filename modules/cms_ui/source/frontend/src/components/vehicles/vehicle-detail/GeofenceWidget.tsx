import React, { useState, useEffect } from 'react';
import { Container, Header, Table, Box, StatusIndicator, Badge, Button, Link } from '@cloudscape-design/components';
import GeofenceMapModal from './GeofenceMapModal';

interface Props { vehicleId: string; vehicleLat?: number; vehicleLng?: number; }

const API = () => (window as any).runtimeConfig?.commandsApiEndpoint || '';

export default function GeofenceWidget({ vehicleId, vehicleLat, vehicleLng }: Props) {
  const [geofences, setGeofences] = useState<any[]>([]);
  const [selectedGf, setSelectedGf] = useState<any>(null);

  const fetch_gf = async () => {
    try {
      const r = await fetch(`${API()}/api/geofences/${vehicleId}`);
      if (r.ok) { const d = await r.json(); setGeofences((d.geofences || []).filter((g: any) => g.active !== false)); }
    } catch {}
  };

  useEffect(() => { fetch_gf(); }, [vehicleId]);

  return (
    <>
      <Container header={<Header variant="h3" counter={`(${geofences.length})`}
        description="Active geofence boundaries">Geofences</Header>}>
        {geofences.length === 0 ? (
          <Box textAlign="center" color="text-status-inactive" padding="s">No active geofences</Box>
        ) : (
          <Table variant="embedded" items={geofences}
            columnDefinitions={[
              { id: 'name', header: 'Name', cell: i => (
                <Link onFollow={(e) => { e.preventDefault(); setSelectedGf(i); }}>{i.name}</Link>
              )},
              { id: 'radius', header: 'Radius', cell: i => `${i.radiusKm} km` },
              { id: 'scope', header: 'Scope', cell: i => (
                <Badge color={i.vehicleId === 'ALL' ? 'blue' : 'grey'}>
                  {i.vehicleId === 'ALL' ? 'Fleet' : 'Vehicle'}
                </Badge>
              )},
              { id: 'view', header: '', cell: i => (
                <Button variant="icon" iconName="expand" onClick={() => setSelectedGf(i)} />
              )},
            ]}
          />
        )}
      </Container>
      <GeofenceMapModal
        visible={!!selectedGf}
        onDismiss={() => setSelectedGf(null)}
        geofence={selectedGf}
        vehicleLat={vehicleLat}
        vehicleLng={vehicleLng}
      />
    </>
  );
}
