import React, { useState, useEffect, useMemo } from 'react';
import {
  Container, Header, SpaceBetween, Button, Box, Table, StatusIndicator,
  Select, Input, ColumnLayout, Badge, Tabs, Cards, Flashbar
} from '@cloudscape-design/components';

interface Command {
  commandId: string;
  commandName: string;
  vehicleId: string;
  status: string;
  value: string;
  label?: string;
  category?: string;
  issuedAt: string;
  respondedAt?: string;
  latencyMs?: number;
}

interface ActuatorDef {
  commandName: string;
  label: string;
  category: string;
  valueType: string;
  min?: number;
  max?: number;
  unit?: string;
  options?: string[];
  responseTimeout: number;
  signalField: string;
  vssPath: string;
}

interface Geofence {
  geofenceId: string;
  vehicleId: string;
  name: string;
  centerLat: number;
  centerLng: number;
  radiusKm: number;
  active: boolean;
  createdAt: string;
}

interface Props {
  vehicleId: string;
}

const API = () => (window as any).runtimeConfig?.commandsApiEndpoint || '';

export default function RemoteCommandsPanel({ vehicleId }: Props) {
  const [catalog, setCatalog] = useState<Record<string, ActuatorDef[]>>({});
  const [history, setHistory] = useState<Command[]>([]);
  const [geofences, setGeofences] = useState<Geofence[]>([]);
  const [sending, setSending] = useState<string | null>(null);
  const [flash, setFlash] = useState<any[]>([]);
  const [selectedCategory, setSelectedCategory] = useState('security');

  useEffect(() => {
    fetchCatalog();
    fetchHistory();
    fetchGeofences();
    const interval = setInterval(fetchHistory, 5000);
    return () => clearInterval(interval);
  }, [vehicleId]);

  const fetchCatalog = async () => {
    try {
      const r = await fetch(`${API()}/api/commands/catalog`);
      if (r.ok) { const d = await r.json(); setCatalog(d.actuators || {}); }
    } catch {}
  };

  const fetchHistory = async () => {
    try {
      const r = await fetch(`${API()}/api/commands/${vehicleId}?limit=50`);
      if (r.ok) { const d = await r.json(); setHistory(d.commands || []); }
    } catch {}
  };

  const fetchGeofences = async () => {
    try {
      const r = await fetch(`${API()}/api/geofences/${vehicleId}`);
      if (r.ok) { const d = await r.json(); setGeofences(d.geofences || []); }
    } catch {}
  };

  const sendCommand = async (cmd: ActuatorDef, value: any) => {
    setSending(cmd.commandName);
    try {
      const r = await fetch(`${API()}/api/commands/${vehicleId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ commandName: cmd.commandName, value, label: cmd.label, category: cmd.category }),
      });
      const d = await r.json();
      if (d.success) {
        setFlash([{ type: 'success', content: `${cmd.label} sent (${d.commandId})`, dismissible: true, onDismiss: () => setFlash([]) }]);
        fetchHistory();
      }
    } catch {}
    setSending(null);
  };

  const deleteGeofence = async (gfId: string) => {
    await fetch(`${API()}/api/geofences/${gfId}`, { method: 'DELETE' });
    fetchGeofences();
  };

  const statusIcon = (s: string) => {
    switch (s) {
      case 'SUCCEEDED': return <StatusIndicator type="success">Executed</StatusIndicator>;
      case 'SENT': return <StatusIndicator type="in-progress">Sent</StatusIndicator>;
      case 'FAILED': return <StatusIndicator type="error">Failed</StatusIndicator>;
      default: return <StatusIndicator type="info">{s}</StatusIndicator>;
    }
  };

  const quickActions: { cmd: string; label: string; val: any; variant?: 'primary' | 'normal' }[] = [
    { cmd: 'lock_all_doors', label: 'Lock All Doors', val: true, variant: 'primary' },
    { cmd: 'lock_all_doors', label: 'Unlock All Doors', val: false },
    { cmd: 'remote_start', label: 'Remote Start', val: true, variant: 'primary' },
    { cmd: 'find_my_vehicle', label: 'Find My Vehicle', val: true },
    { cmd: 'start_preconditioning', label: 'Pre-Condition Cabin', val: true },
    { cmd: 'honk_horn', label: 'Honk Horn', val: true },
    { cmd: 'flash_hazards', label: 'Flash Hazard Lights', val: true },
    { cmd: 'panic_mode', label: 'Activate Panic Mode', val: true },
  ];

  const categories = Object.keys(catalog).sort();

  return (
    <SpaceBetween size="l">
      {flash.length > 0 && <Flashbar items={flash} />}

      {/* Quick Actions */}
      <Container header={<Header variant="h2" description="Common vehicle commands">Quick Actions</Header>}>
        <ColumnLayout columns={4}>
          {quickActions.map(({ cmd, label, val, variant }) => {
            const def = Object.values(catalog).flat().find(c => c.commandName === cmd);
            return (
              <Button key={`${cmd}-${val}`} fullWidth
                variant={variant || 'normal'}
                loading={sending === cmd}
                disabled={!def}
                onClick={() => def && sendCommand(def, val)}
              >
                {label}
              </Button>
            );
          })}
        </ColumnLayout>
      </Container>

      {/* Active Geofences */}
      <Container header={
        <Header variant="h2" counter={`(${geofences.filter(g => g.active !== false).length})`}
          description="Geofences assigned to this vehicle"
          actions={<Button iconName="refresh" onClick={fetchGeofences}>Refresh</Button>}
        >Geofences</Header>
      }>
        <Table
          variant="embedded"
          columnDefinitions={[
            { id: 'name', header: 'Name', cell: item => <Box fontWeight="bold">{item.name}</Box> },
            { id: 'center', header: 'Center', cell: item => `${parseFloat(item.centerLat).toFixed(4)}, ${parseFloat(item.centerLng).toFixed(4)}` },
            { id: 'radius', header: 'Radius', cell: item => `${item.radiusKm} km` },
            { id: 'scope', header: 'Scope', cell: item => <Badge color={item.vehicleId === 'ALL' ? 'blue' : 'grey'}>{item.vehicleId === 'ALL' ? 'Fleet-wide' : 'This vehicle'}</Badge> },
            { id: 'status', header: 'Status', cell: item => item.active !== false ? <StatusIndicator type="success">Active</StatusIndicator> : <StatusIndicator type="stopped">Inactive</StatusIndicator> },
            { id: 'actions', header: '', cell: item => <Button variant="icon" iconName="remove" onClick={() => deleteGeofence(item.geofenceId)} /> },
          ]}
          items={geofences.filter(g => g.active !== false)}
          empty={<Box textAlign="center" padding="l">No geofences assigned. Set geofences from the Fleet Map view.</Box>}
        />
      </Container>

      {/* All Commands by Category */}
      <Container header={<Header variant="h2">All Commands</Header>}>
        {categories.length === 0 ? (
          <Box textAlign="center" padding="l"><StatusIndicator type="loading">Loading command catalog...</StatusIndicator></Box>
        ) : (
          <Tabs
            activeTabId={selectedCategory}
            onChange={({ detail }) => setSelectedCategory(detail.activeTabId)}
            tabs={categories.map(cat => ({
              id: cat,
              label: `${cat.charAt(0).toUpperCase() + cat.slice(1)} (${catalog[cat].length})`,
              content: (
                <Table
                  variant="embedded"
                  columnDefinitions={[
                    { id: 'label', header: 'Command', cell: item => <Box fontWeight="bold">{item.label}</Box>, width: 200 },
                    { id: 'type', header: 'Type', cell: item => <Badge>{item.valueType}</Badge>, width: 80 },
                    { id: 'vss', header: 'VSS Path', cell: item => <Box variant="code" fontSize="body-s">{item.vssPath}</Box> },
                    { id: 'action', header: 'Action', cell: item => <CommandAction cmd={item} sending={sending} onSend={sendCommand} />, width: 250 },
                  ]}
                  items={catalog[cat]}
                />
              ),
            }))}
          />
        )}
      </Container>

      {/* Command History */}
      <Container header={
        <Header variant="h2" counter={`(${history.length})`}
          actions={<Button iconName="refresh" onClick={fetchHistory}>Refresh</Button>}
        >Command History</Header>
      }>
        <Table
          variant="embedded"
          columnDefinitions={[
            { id: 'time', header: 'Time', cell: item => new Date(item.issuedAt).toLocaleString(), width: 180 },
            { id: 'command', header: 'Command', cell: item => item.label || item.commandName },
            { id: 'value', header: 'Value', cell: item => String(item.value), width: 80 },
            { id: 'status', header: 'Status', cell: item => statusIcon(item.status), width: 120 },
            { id: 'latency', header: 'Latency', cell: item => item.latencyMs ? `${item.latencyMs}ms` : '—', width: 80 },
            { id: 'id', header: 'ID', cell: item => <Box variant="code" fontSize="body-s">{item.commandId}</Box>, width: 120 },
          ]}
          items={history}
          empty={<Box textAlign="center">No commands sent yet</Box>}
          sortingDisabled
        />
      </Container>
    </SpaceBetween>
  );
}

function CommandAction({ cmd, sending, onSend }: { cmd: ActuatorDef; sending: string | null; onSend: (cmd: ActuatorDef, val: any) => void }) {
  const [value, setValue] = useState<any>(cmd.valueType === 'boolean' ? true : cmd.min || 0);

  if (cmd.valueType === 'boolean') {
    return (
      <SpaceBetween direction="horizontal" size="xs">
        <Button variant="primary" loading={sending === cmd.commandName} onClick={() => onSend(cmd, true)}>On</Button>
        <Button loading={sending === cmd.commandName} onClick={() => onSend(cmd, false)}>Off</Button>
      </SpaceBetween>
    );
  }

  if (cmd.options) {
    return (
      <SpaceBetween direction="horizontal" size="xs">
        <Select
          selectedOption={{ value: String(value), label: cmd.options[value] || String(value) }}
          options={cmd.options.map((o, i) => ({ value: String(i), label: o }))}
          onChange={({ detail }) => setValue(Number(detail.selectedOption.value))}
        />
        <Button variant="primary" loading={sending === cmd.commandName} onClick={() => onSend(cmd, value)}>Set</Button>
      </SpaceBetween>
    );
  }

  return (
    <SpaceBetween direction="horizontal" size="xs">
      <Input type="number" value={String(value)}
        onChange={({ detail }) => setValue(Number(detail.value))} />
      <Box variant="small">{cmd.unit}</Box>
      <Button variant="primary" loading={sending === cmd.commandName} onClick={() => onSend(cmd, value)}>Set</Button>
    </SpaceBetween>
  );
}
