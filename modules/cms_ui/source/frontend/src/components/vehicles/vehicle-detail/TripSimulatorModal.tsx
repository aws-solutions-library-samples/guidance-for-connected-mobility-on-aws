import React, { useState, useEffect } from 'react';
import {
  Modal, Box, Button, SpaceBetween, FormField, Select, Toggle, Alert
} from '@cloudscape-design/components';

const CITIES = [
  { label: 'Atlanta', value: 'atlanta' },
  { label: 'Chicago', value: 'chicago' },
  { label: 'Miami', value: 'miami' },
  { label: 'Munich', value: 'munich' },
  { label: 'New York', value: 'nyc' },
  { label: 'San Francisco', value: 'sf' },
  { label: 'Seattle', value: 'seattle' },
];

interface TripSimulatorModalProps {
  visible: boolean;
  vehicleId: string;
  onDismiss: () => void;
}

const TripSimulatorModal: React.FC<TripSimulatorModalProps> = ({ visible, vehicleId, onDismiss }) => {
  const [city, setCity] = useState(CITIES[0]);
  const [forceSafety, setForceSafety] = useState(false);
  const [forceMaintenance, setForceMaintenance] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  useEffect(() => {
    if (visible) {
      setCity(CITIES[Math.floor(Math.random() * CITIES.length)]);
      setForceSafety(false);
      setForceMaintenance(false);
      setError('');
      setSuccess('');
    }
  }, [visible]);

  const handleStart = async () => {
    setLoading(true);
    setError('');
    setSuccess('');
    try {
      const config: Record<string, unknown> = {
        vehicle_source: 'real',
        vehicles: [vehicleId],
        trips: 1,
        city: city.value,
        interval: 15,
        driver_selection: 'random',
        safety_rate: forceSafety ? 0.9 : 0.15,
        force_safety_event: forceSafety ? 'hard_braking' : null,
        force_engine_overheat: forceMaintenance,
        progressive_degradation: forceMaintenance,
        aws_region: (window as any).runtimeConfig?.awsRegion || 'us-east-1',
      };

      const resp = await fetch('http://localhost:5001/api/simulation/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });
      const data = await resp.json();
      if (data.success) {
        setSuccess(`Simulation started (ID: ${data.simulation_id}). Trip will appear in ~5 minutes.`);
      } else {
        setError(data.error || 'Failed to start simulation');
      }
    } catch {
      setError('Cannot reach simulation service at localhost:5001. Is it running?');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      header="Trip Simulator"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" onClick={handleStart} loading={loading} disabled={!!success}>
              Start Trip
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        <Alert type="info">
          Simulate a single trip for <strong>{vehicleId}</strong> with a random driver. The trip flows through MQTT → IoT Core → Flink and appears in the trips list once complete (~5 min).
        </Alert>

        <FormField label="City">
          <Select
            selectedOption={city}
            onChange={({ detail }) => setCity(detail.selectedOption as typeof city)}
            options={CITIES}
          />
        </FormField>

        <Toggle checked={forceSafety} onChange={({ detail }) => setForceSafety(detail.checked)}>
          Force Safety Event
        </Toggle>

        <Toggle checked={forceMaintenance} onChange={({ detail }) => setForceMaintenance(detail.checked)}>
          Force Maintenance Alert
        </Toggle>

        {error && <Alert type="error">{error}</Alert>}
        {success && <Alert type="success">{success}</Alert>}
      </SpaceBetween>
    </Modal>
  );
};

export default TripSimulatorModal;
