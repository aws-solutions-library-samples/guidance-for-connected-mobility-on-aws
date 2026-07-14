import React, { useState, useEffect } from 'react';
import { getSimulationApiUrl } from '../../../utils/simulation-config';
import { severityLabel } from '../../../utils/severity';
import { authFetch } from '../../../utils/authFetch';
import {
  Modal, Box, Button, SpaceBetween, FormField, Select, Multiselect, Alert, Spinner
} from '@cloudscape-design/components';

const API = () => (window as any).runtimeConfig?.apiEndpoint?.replace(/\/$/, '') || '';

const CITIES = [
  { label: 'Atlanta', value: 'atlanta' },
  { label: 'Chicago', value: 'chicago' },
  { label: 'Miami', value: 'miami' },
  { label: 'Munich', value: 'munich' },
  { label: 'New York', value: 'nyc' },
  { label: 'San Francisco', value: 'sf' },
  { label: 'Seattle', value: 'seattle' },
];

interface EventOption {
  label: string;
  value: string;
  description?: string;
}

interface TripSimulatorModalProps {
  visible: boolean;
  vehicleId: string;
  vin?: string;
  onDismiss: () => void;
  onStarted?: (simId: string) => void;
}

const TripSimulatorModal: React.FC<TripSimulatorModalProps> = ({ visible, vehicleId, vin, onDismiss, onStarted }) => {
  const [city, setCity] = useState(CITIES[0]);
  // Default to FWE Agent as the ingestion source since that exercises
  // the full real-vehicle pipeline (AWS IoT FleetWise + signal catalog
  // + Flink). MQTT Direct remains available as a fallback/diagnostic
  // option when FWE is unavailable or for lower-cost quick tests, but
  // it short-circuits the FleetWise path which is usually not what
  // demos want. Changed 2026-05-04.
  const [mode, setMode] = useState<any>({ value: 'fwe', label: 'FWE Agent' });
  // Route length (number of GPS waypoints the simulator generates per
  // trip). Each waypoint = one telemetry tick at the default 15s
  // interval, so 20 waypoints ≈ 5 minutes of simulated driving.
  // Exposed as a preset selector so operators can dial in a quick
  // smoke test vs. a longer demo trip; the API clamps to [5, 60].
  // Added 2026-05-05 after we found trips occasionally ran past the
  // worker-thread timeout when AWS Location Service returned
  // unusually dense coordinate lists.
  const [routeLength, setRouteLength] = useState<any>({ value: '20', label: 'Default (~5 min, 20 points)' });
  const [selectedSafety, setSelectedSafety] = useState<any[]>([]);
  const [selectedMaintenance, setSelectedMaintenance] = useState<any[]>([]);
  const [safetyOptions, setSafetyOptions] = useState<EventOption[]>([]);
  const [maintenanceOptions, setMaintenanceOptions] = useState<EventOption[]>([]);
  const [loadingCatalog, setLoadingCatalog] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Fetch event catalog on mount
  useEffect(() => {
    const fetchCatalog = async () => {
      setLoadingCatalog(true);
      try {
        const resp = await authFetch(`${API()}/api/v1/event-catalog`);
        if (resp.ok) {
          const data = await resp.json();
          const events = data.events || data.Items || data || [];
          const safety: EventOption[] = [];
          const maintenance: EventOption[] = [];
          for (const evt of events) {
            // Show the canonical OBD-II DTC code next to events that
            // carry one. Operators use this to know which scenarios
            // will produce a DTC row (→ header pill / iOS banner /
            // Alerts tab) vs. which ones only write a maintenance- or
            // safety-alert. See force_event.py (same gate) for why
            // only events with dtc_code create DTCs. Added 2026-05-04
            // after catalog promotions bumped the dtc-code count to
            // 24/40. The "·" separator + bold-ish monospace via the
            // label formatting keeps code readable at a glance.
            const baseLabel = evt.description || evt.event_id;
            const label = evt.dtc_code
              ? `${baseLabel} · ${evt.dtc_code}`
              : baseLabel;
            const option: EventOption = {
              label,
              value: evt.event_id || evt.eventId,
              description: `Signal: ${evt.trigger_signal || '—'} | Severity: ${severityLabel(evt.severity ?? evt.severity_hint)}${evt.dtc_code ? ' | Creates DTC' : ''}`,
            };
            if (evt.category === 'safety') {
              safety.push(option);
            } else if (evt.category === 'maintenance') {
              maintenance.push(option);
            }
          }
          setSafetyOptions(safety.sort((a, b) => a.label.localeCompare(b.label)));
          setMaintenanceOptions(maintenance.sort((a, b) => a.label.localeCompare(b.label)));
        }
      } catch (e) {
        console.warn('Failed to load event catalog, using defaults');
      }
      setLoadingCatalog(false);
    };
    fetchCatalog();
  }, []);

  useEffect(() => {
    if (visible) {
      setCity(CITIES[Math.floor(Math.random() * CITIES.length)]);
      setSelectedSafety([]);
      setSelectedMaintenance([]);
      setError('');
    }
  }, [visible]);

  const handleStart = async () => {
    setLoading(true);
    setError('');
    try {
      const config: Record<string, unknown> = {
        vehicle_source: 'real',
        vehicles: [vehicleId],
        trips: 1,
        city: city.value,
        mode: mode.value,
        interval: 15,
        route_length: Number(routeLength.value),
        driver_selection: 'random',
        aws_region: (window as any).runtimeConfig?.awsRegion || 'us-east-1',
        safety_scenarios: selectedSafety.map(s => s.value),
        maintenance_scenarios: selectedMaintenance.map(s => s.value),
        safety_rate: selectedSafety.length > 0 ? 0.9 : 0.15,
        progressive_degradation: selectedMaintenance.length > 0,
      };

      const resp = await fetch(getSimulationApiUrl('/start'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });
      const data = await resp.json();
      if (data.success) {
        onStarted?.(data.simulation_id);
        onDismiss();
      } else {
        setError(data.error || 'Failed to start simulation');
      }
    } catch {
      setError('Cannot reach simulation service. Is it running?');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal visible={visible} onDismiss={onDismiss} header="Trip Simulator"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" onClick={handleStart} loading={loading}>Start Trip</Button>
          </SpaceBetween>
        </Box>
      }>
      <SpaceBetween size="m">
        <Alert type="info">
          Simulate a trip for <strong>{vin || vehicleId}</strong>. Select events from the catalog to test during this trip.
        </Alert>
        <FormField label="City">
          <Select selectedOption={city} onChange={({ detail }) => setCity(detail.selectedOption as typeof city)} options={CITIES} />
        </FormField>
        <FormField label="Source">
          <Select selectedOption={mode} onChange={({ detail }) => setMode(detail.selectedOption)}
            options={[
              { value: 'mqtt_direct', label: 'MQTT Direct' },
              { value: 'fwe', label: 'FWE Agent' },
            ]} />
        </FormField>
        <FormField
          label="Route Length"
          description="How many GPS waypoints to generate per trip. Each point ≈ 15 seconds of simulated driving. Shorter routes finish faster for quick smoke tests; longer routes exercise longer-duration event triggers."
        >
          <Select
            selectedOption={routeLength}
            onChange={({ detail }) => setRouteLength(detail.selectedOption)}
            options={[
              { value: '10', label: 'Short (~2.5 min, 10 points)' },
              { value: '20', label: 'Default (~5 min, 20 points)' },
              { value: '30', label: 'Medium (~7.5 min, 30 points)' },
              { value: '45', label: 'Long (~11 min, 45 points)' },
              { value: '60', label: 'Max (~15 min, 60 points)' },
            ]}
          />
        </FormField>
        <FormField label="Safety Events" description="Select safety events to simulate during this trip">
          {loadingCatalog ? <Spinner /> : (
            <Multiselect
              selectedOptions={selectedSafety}
              onChange={({ detail }) => setSelectedSafety([...detail.selectedOptions])}
              options={safetyOptions}
              placeholder={safetyOptions.length ? 'None (normal driving)' : 'No events in catalog'}
              filteringType="auto"
            />
          )}
        </FormField>
        <FormField
          label="Maintenance Events"
          description="Select maintenance conditions to simulate. Events ending with a code (e.g. '· P0520') create an active DTC row visible on the Vehicle Detail header, iOS Alerts tab, and voice assistant."
        >
          {loadingCatalog ? <Spinner /> : (
            <Multiselect
              selectedOptions={selectedMaintenance}
              onChange={({ detail }) => setSelectedMaintenance([...detail.selectedOptions])}
              options={maintenanceOptions}
              placeholder={maintenanceOptions.length ? 'None (healthy vehicle)' : 'No events in catalog'}
              filteringType="auto"
            />
          )}
        </FormField>
        {error && <Alert type="error">{error}</Alert>}
      </SpaceBetween>
    </Modal>
  );
};

export default TripSimulatorModal;
