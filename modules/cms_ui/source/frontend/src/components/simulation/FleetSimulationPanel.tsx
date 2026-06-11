import React, { useState, useEffect, useCallback } from 'react';
import {
  Box, Button, Container, Header, SpaceBetween, Table, StatusIndicator,
  FormField, Select, Input, Multiselect, ColumnLayout, Flashbar, Modal,
  TextFilter, Pagination, Badge,
} from '@cloudscape-design/components';
import { getSimulationApiBase } from '../../utils/simulation-config';
import { authFetch } from '../../utils/authFetch';
import { severityLabel } from '../../utils/severity';
// Reuse the same two log viewers the Vehicle Detail → Logs tab uses
// so the fleet-level "Logs" action shows both simulator + FWE agent
// output side-by-side instead of just the simulator output.
// Added 2026-05-04.
import SimLogViewer from '../vehicles/vehicle-detail/SimLogViewer';
import FWELogViewer from '../vehicles/vehicle-detail/FWELogViewer';

interface Simulation {
  simulationId: string;
  status: string;
  mode: string;
  vehicles: any[];
  startedAt: string;
  trips: number;
  city: string;
}

const CITIES = [
  { value: 'seattle', label: 'Seattle' },
  { value: 'atlanta', label: 'Atlanta' },
  { value: 'dallas', label: 'Dallas' },
  { value: 'chicago', label: 'Chicago' },
  { value: 'denver', label: 'Denver' },
  { value: 'munich', label: 'Munich' },
];

const SAFETY_EVENTS: any[] = [];
const MAINTENANCE_EVENTS: any[] = [];

export default function FleetSimulationPanel() {
  const [simulations, setSimulations] = useState<Simulation[]>([]);
  const [vehicles, setVehicles] = useState<any[]>([]);
  const [selectedVehicles, setSelectedVehicles] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [flash, setFlash] = useState<any[]>([]);
  const [statusFilter, setStatusFilter] = useState<any>({ value: 'all', label: 'All' });
  // Log modal now carries the full simulation context so we can pass
  // the vehicle id/vin into the side-by-side sim + FWE log viewers.
  // When a sim has multiple vehicles, we show a selector inside the
  // modal and default to the first one.
  const [logModal, setLogModal] = useState<Simulation | null>(null);
  const [logModalVehicleIdx, setLogModalVehicleIdx] = useState<number>(0);
  // simReachable gates the viewers' streaming; probe the simulation
  // service the same way VehicleDetailView does.
  const [simReachable, setSimReachable] = useState<boolean>(false);
  const [showVehicleModal, setShowVehicleModal] = useState(false);
  const [tempSelectedVehicles, setTempSelectedVehicles] = useState<any[]>([]);

  // Config
  const [city, setCity] = useState<any>(CITIES[0]);
  const [trips, setTrips] = useState('1');
  const [mode, setMode] = useState<any>({ value: 'fwe', label: 'FWE (CAN Bus)' });
  // Route length preset — mirrored from TripSimulatorModal so the
  // fleet-scale sim has the same knob. See realtime_telemetry_simulator.py
  // for the underlying semantics (waypoints per trip at 15s interval).
  const [routeLength, setRouteLength] = useState<any>({ value: '20', label: 'Default (~5 min, 20 points)' });
  const [safetyEvents, setSafetyEvents] = useState<any[]>([]);
  const [maintenanceEvents, setMaintenanceEvents] = useState<any[]>([]);
  const [safetyOptions, setSafetyOptions] = useState<any[]>(SAFETY_EVENTS);
  const [maintenanceOptions, setMaintenanceOptions] = useState<any[]>(MAINTENANCE_EVENTS);

  const simApi = getSimulationApiBase();
  const apiEndpoint = (window as any).runtimeConfig?.apiEndpoint || '';

  // Load event catalog
  useEffect(() => {
    (async () => {
      try {
        const resp = await fetch(`${apiEndpoint}/api/v1/event-catalog`);
        if (resp.ok) {
          const data = await resp.json();
          const events = data.events || data.Items || data || [];
          const safety: any[] = [];
          const maintenance: any[] = [];
          for (const evt of events) {
            // Label shows the canonical DTC code when present so
            // operators know which scenarios create a DTC row; severity
            // is normalised to CRITICAL/HIGH/MEDIUM/LOW via the shared
            // severityLabel helper to avoid the old "Severity: 2"
            // ambiguity (event catalog is reverse-ranked numeric).
            const baseLabel = evt.description || evt.event_id;
            const label = evt.dtc_code ? `${baseLabel} · ${evt.dtc_code}` : baseLabel;
            const opt = {
              label,
              value: evt.event_id || evt.eventId,
              description: `Signal: ${evt.trigger_signal || '—'} | Severity: ${severityLabel(evt.severity ?? evt.severity_hint)}${evt.dtc_code ? ' | Creates DTC' : ''}`,
            };
            if (evt.category === 'safety') safety.push(opt);
            else if (evt.category === 'maintenance') maintenance.push(opt);
          }
          if (safety.length) setSafetyOptions(safety.sort((a: any, b: any) => a.label.localeCompare(b.label)));
          if (maintenance.length) setMaintenanceOptions(maintenance.sort((a: any, b: any) => a.label.localeCompare(b.label)));
        }
      } catch {}
    })();
  }, [apiEndpoint]);

  const fetchSimulations = useCallback(async () => {
    try {
      const res = await fetch(`${simApi}/api/simulation/list`);
      if (res.ok) {
        const data = await res.json();
        setSimulations((data.simulations || []).map((s: any) => ({
          ...s,
          simulationId: s.simulationId || s.id,
          startedAt: s.startedAt || s.start_time,
          vehicles: s.config?.vehicles || [],
          trips: s.config?.trips || 0,
          city: s.config?.city || '—',
          mode: s.config?.mode || '—',
        })));
      }
    } catch {}
  }, [simApi]);

  const fetchVehicles = useCallback(async () => {
    try {
      const res = await authFetch(`${apiEndpoint}/api/v1/vehicles?limit=100`);
      if (res.ok) {
        const data = await res.json();
        setVehicles(data.vehicles || []);
      }
    } catch {}
  }, [apiEndpoint]);

  useEffect(() => {
    fetchSimulations();
    fetchVehicles();
    const id = setInterval(fetchSimulations, 5000);
    return () => clearInterval(id);
  }, [fetchSimulations, fetchVehicles]);

  const startSimulation = async () => {
    if (!selectedVehicles.length) return;
    setLoading(true);
    try {
      const body = {
        mode: mode.value,
        vehicles: selectedVehicles.map((v: any) => ({ vehicleId: v.vehicleId, vin: v.vin })),
        trips: parseInt(trips) || 1,
        route_length: Number(routeLength.value),
        city: city.value,
        safetyEvents: safetyEvents.map((e: any) => e.value),
        maintenanceEvents: maintenanceEvents.map((e: any) => e.value),
      };
      const res = await fetch(`${simApi}/api/simulation/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (res.ok && data.success !== false) {
        setFlash([{ type: 'success', content: `Simulation ${data.simulation_id} started for ${selectedVehicles.length} vehicle(s)`, dismissible: true, onDismiss: () => setFlash([]) }]);
        setSelectedVehicles([]);
        fetchSimulations();
      } else {
        setFlash([{ type: 'error', content: data.error || 'Failed to start simulation', dismissible: true, onDismiss: () => setFlash([]) }]);
      }
    } catch (e: any) {
      setFlash([{ type: 'error', content: e.message, dismissible: true, onDismiss: () => setFlash([]) }]);
    }
    setLoading(false);
  };

  const stopSimulation = async (simId: string) => {
    try {
      await fetch(`${simApi}/api/simulation/stop/${simId}`, { method: 'POST' });
      fetchSimulations();
    } catch {}
  };

  // Opens the logs modal for a given simulation. No pre-fetch — the
  // SimLogViewer + FWELogViewer components manage their own streaming,
  // identical to the Vehicle Detail → Logs tab. We just hand them the
  // sim + first vehicle to key off.
  const viewLogs = (sim: Simulation) => {
    setLogModalVehicleIdx(0);
    setLogModal(sim);
  };

  // Probe the simulation service once on mount so the log viewers'
  // `simReachable` prop is correct when they're rendered. Mirrors
  // VehicleDetailView's approach so both surfaces behave identically.
  //
  // We also collect the VINs of currently-running FWE agents from the
  // same response. The per-VIN set drives the FWELogViewer's
  // `agentRunning` prop — previously we derived it from the sim's
  // status, but FWE agents are long-lived (outlive the simulator task
  // that triggered them), so `sim.status==='running'` false-negatived
  // even when the agent for that vehicle was in fact still streaming.
  // Fixed 2026-05-04.
  const [runningAgentVins, setRunningAgentVins] = useState<Set<string>>(new Set());
  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const r = await fetch(`${getSimulationApiBase()}/api/simulation/agent/status`);
        if (!r.ok) {
          if (!cancelled) { setSimReachable(false); setRunningAgentVins(new Set()); }
          return;
        }
        const d = await r.json();
        const vins = new Set<string>();
        for (const a of (d.agents || [])) {
          const status: string = a.status || '';
          if (status === 'RUNNING' || status === 'PENDING' || status === 'PROVISIONING') {
            if (a.vin) vins.add(a.vin);
            if (a.vehicleName && a.vehicleName !== a.vin) vins.add(a.vehicleName);
          }
        }
        if (!cancelled) { setSimReachable(true); setRunningAgentVins(vins); }
      } catch {
        if (!cancelled) { setSimReachable(false); setRunningAgentVins(new Set()); }
      }
    };
    check();
    const id = setInterval(check, 15000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const filtered = statusFilter.value === 'all'
    ? simulations
    : simulations.filter(s => s.status === statusFilter.value);

  return (
    <SpaceBetween size="l">
      {flash.length > 0 && <Flashbar items={flash} />}

      {/* Simulation Configuration */}
      <Container header={
        <Header variant="h2"
          description="Select vehicles and configure simulation parameters"
          actions={
            <Button variant="primary" loading={loading} disabled={!selectedVehicles.length}
              onClick={startSimulation} iconName="caret-right-filled">
              Start Simulation ({selectedVehicles.length})
            </Button>
          }>
          New Simulation
        </Header>
      }>
        <SpaceBetween size="l">
          <ColumnLayout columns={4}>
            <FormField label="Mode">
              <Select selectedOption={mode} onChange={({ detail }) => setMode(detail.selectedOption)}
                options={[
                  { value: 'fwe', label: 'FWE (CAN Bus)' },
                  { value: 'mqtt_direct', label: 'MQTT Direct' },
                ]} />
            </FormField>
            <FormField label="City">
              <Select selectedOption={city} onChange={({ detail }) => setCity(detail.selectedOption)} options={CITIES} />
            </FormField>
            <FormField label="Trips per vehicle">
              <Input type="number" value={trips} onChange={({ detail }) => setTrips(detail.value)} />
            </FormField>
            <FormField label="Route Length">
              <Select
                selectedOption={routeLength}
                onChange={({ detail }) => setRouteLength(detail.selectedOption)}
                options={[
                  { value: '10', label: 'Short (~2.5 min)' },
                  { value: '20', label: 'Default (~5 min)' },
                  { value: '30', label: 'Medium (~7.5 min)' },
                  { value: '45', label: 'Long (~11 min)' },
                  { value: '60', label: 'Max (~15 min)' },
                ]}
              />
            </FormField>
            <FormField label="Safety Events">
              <Multiselect selectedOptions={safetyEvents} onChange={({ detail }) => setSafetyEvents([...detail.selectedOptions])}
                options={safetyOptions} placeholder={safetyOptions.length ? 'None (normal driving)' : 'Loading...'} filteringType="auto" />
            </FormField>
          </ColumnLayout>
          <FormField label="Maintenance Events">
            <Multiselect selectedOptions={maintenanceEvents} onChange={({ detail }) => setMaintenanceEvents([...detail.selectedOptions])}
              options={maintenanceOptions} placeholder={maintenanceOptions.length ? 'None (healthy vehicle)' : 'Loading...'} filteringType="auto" />
          </FormField>

          {/* Vehicle Selection */}
          <FormField label="Vehicles">
            <SpaceBetween direction="horizontal" size="s">
              <Button onClick={() => { setTempSelectedVehicles(selectedVehicles); setShowVehicleModal(true); }} iconName="add-plus">
                Select Vehicles ({selectedVehicles.length} selected)
              </Button>
              {selectedVehicles.length > 0 && (
                <Box variant="small" color="text-status-info">
                  {selectedVehicles.map((v: any) => v.vehicleId).join(', ')}
                </Box>
              )}
            </SpaceBetween>
          </FormField>
        </SpaceBetween>
      </Container>

      {/* Active & Completed Simulations */}
      <Container header={
        <Header variant="h2" counter={`(${filtered.length})`}
          actions={
            <SpaceBetween direction="horizontal" size="s">
              <Select selectedOption={statusFilter}
                onChange={({ detail }) => setStatusFilter(detail.selectedOption)}
                options={[
                  { value: 'all', label: 'All' },
                  { value: 'running', label: 'Running' },
                  { value: 'completed', label: 'Completed' },
                  { value: 'failed', label: 'Failed' },
                ]} />
              <Button iconName="refresh" onClick={fetchSimulations} />
            </SpaceBetween>
          }>
          Simulations
        </Header>
      }>
        <Table
          items={filtered.sort((a, b) => (b.startedAt || '').localeCompare(a.startedAt || ''))}
          columnDefinitions={[
            { id: 'id', header: 'ID', cell: (s) => s.simulationId, width: 100 },
            { id: 'status', header: 'Status', cell: (s) => (
              <StatusIndicator type={
                s.status === 'running' ? 'in-progress' : s.status === 'completed' ? 'success' : s.status === 'failed' ? 'error' : 'stopped'
              }>{s.status}</StatusIndicator>
            ), width: 110 },
            { id: 'mode', header: 'Mode', cell: (s) => s.mode === 'fwe' ? 'FWE' : 'MQTT', width: 80 },
            { id: 'vehicles', header: 'Vehicles', cell: (s) => s.vehicles?.length || 0, width: 80 },
            { id: 'vins', header: 'VINs', cell: (s) => (s.vehicles || []).map((v: any) => v.vin || v.vehicleId).join(', ') },
            { id: 'trips', header: 'Trips', cell: (s) => s.trips, width: 60 },
            { id: 'city', header: 'City', cell: (s) => s.city, width: 90 },
            { id: 'started', header: 'Started', cell: (s) => s.startedAt ? new Date(s.startedAt).toLocaleString() : '—', width: 170 },
            { id: 'actions', header: 'Actions', cell: (s) => (
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="inline-link" onClick={() => viewLogs(s)}>Logs</Button>
                {s.status === 'running' && <Button variant="inline-link" onClick={() => stopSimulation(s.simulationId)}>Stop</Button>}
              </SpaceBetween>
            ), width: 120 },
          ]}
          variant="embedded"
          stickyHeader
          empty={<Box textAlign="center" padding="l">No simulations found</Box>}
        />
      </Container>

      {/* Vehicle Selection Modal */}
      {showVehicleModal && (
        <Modal visible onDismiss={() => setShowVehicleModal(false)} size="max"
          header="Select Vehicles for Simulation"
          footer={<Box float="right"><SpaceBetween direction="horizontal" size="xs">
            <Button onClick={() => setShowVehicleModal(false)}>Cancel</Button>
            <Button variant="primary" onClick={() => { setSelectedVehicles(tempSelectedVehicles); setShowVehicleModal(false); }}>
              Confirm ({tempSelectedVehicles.length} selected)
            </Button>
          </SpaceBetween></Box>}>
          <Table
            items={vehicles}
            selectionType="multi"
            selectedItems={tempSelectedVehicles}
            onSelectionChange={({ detail }) => setTempSelectedVehicles(detail.selectedItems)}
            columnDefinitions={[
              { id: 'id', header: 'Vehicle ID', cell: (v: any) => v.vehicleId, width: 100 },
              { id: 'vin', header: 'VIN', cell: (v: any) => v.vin || '—', width: 200 },
              { id: 'make', header: 'Make', cell: (v: any) => v.make || '—' },
              { id: 'model', header: 'Model', cell: (v: any) => v.model || '—' },
              { id: 'fleet', header: 'Fleet', cell: (v: any) => v.fleetId ? <Badge>{v.fleetId}</Badge> : '—' },
              { id: 'status', header: 'Status', cell: (v: any) => (
                <StatusIndicator type={v.connectionStatus === 'connected' ? 'success' : 'stopped'}>
                  {v.connectionStatus || 'Offline'}
                </StatusIndicator>
              )},
            ]}
            header={<Header counter={`(${vehicles.length})`}>Available Vehicles</Header>}
            empty={<Box textAlign="center" padding="l">Loading vehicles...</Box>}
          />
        </Modal>
      )}

      {/* Log Modal — two-pane view matching the Vehicle Detail > Logs tab.
          Left pane: the fwe-simulator (CAN data generator) log stream.
          Right pane: the FWE agent (on-vehicle telemetry publisher) log
          stream. When the sim targets more than one vehicle, a selector
          at the top lets the operator switch which vehicle's FWE logs
          they're looking at; the simulator log pane is the same either
          way (one process per sim). */}
      {logModal && (() => {
        const sim = logModal;
        const vehicles = Array.isArray(sim.vehicles) ? sim.vehicles : [];
        const activeVehicle = vehicles[logModalVehicleIdx] || vehicles[0] || null;
        const activeVin: string = activeVehicle?.vin || '';
        const activeVehicleId: string = activeVehicle?.vehicleId || activeVin;
        return (
          <Modal visible onDismiss={() => setLogModal(null)} size="max"
            header={`Simulation Logs — ${sim.simulationId}`}>
            <SpaceBetween size="s">
              {vehicles.length > 1 && (
                <FormField label="Vehicle (FWE log pane)">
                  <Select
                    selectedOption={{
                      value: String(logModalVehicleIdx),
                      label: `${activeVehicle?.vehicleId || '?'}${activeVin ? ` (${activeVin})` : ''}`,
                    }}
                    onChange={({ detail }) => setLogModalVehicleIdx(Number(detail.selectedOption.value || '0'))}
                    options={vehicles.map((v: any, i: number) => ({
                      value: String(i),
                      label: `${v.vehicleId || '?'}${v.vin ? ` (${v.vin})` : ''}`,
                    }))}
                  />
                </FormField>
              )}
              <ColumnLayout columns={2}>
                <SimLogViewer
                  vehicleId={activeVehicleId}
                  vin={activeVin}
                  simReachable={simReachable}
                  simulationId={sim.simulationId}
                />
                <FWELogViewer
                  vin={activeVin}
                  simReachable={simReachable}
                  // agentRunning is derived from the cluster-wide FWE
                  // task list (polled every 15s), not from the sim's
                  // status. FWE agents are long-lived and typically
                  // outlive the simulator task that spawned them — so
                  // `sim.status==='running'` is not a reliable proxy.
                  agentRunning={!!activeVin && runningAgentVins.has(activeVin)}
                />
              </ColumnLayout>
            </SpaceBetween>
          </Modal>
        );
      })()}
    </SpaceBetween>
  );
}
