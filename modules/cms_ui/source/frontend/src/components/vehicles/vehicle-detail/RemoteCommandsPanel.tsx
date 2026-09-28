import React, { useState, useEffect, useMemo } from 'react';
import {
  Container, Header, SpaceBetween, Button, Box, Table, StatusIndicator,
  Select, Input, ColumnLayout, Badge, Tabs, Cards, Flashbar, Alert
} from '@cloudscape-design/components';
import VehicleStatePanel from './VehicleStatePanel';
import { getCommandsApiBase } from '../../../utils/api-config';
import { useUserRole } from '../../../auth/useUserRole';

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
  /** Milliseconds, and a STRING on the wire — the catalog stores it as a DDB
   * string and the Lambda stringifies its default to match. Declaring `number`
   * here was inert (nothing reads this field) but actively hazardous: it invited
   * "fixing" the wire to match the declaration, which would break the iOS client
   * on all 48 catalog entries instead of one. Convert at the point of use.
   * See issues/2026-08-19-cms-command-catalog-response-timeout-contract/. */
  responseTimeout: string;
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
  // Connection status from the vehicle record. Used to gate Quick Actions
  // buttons so a disconnected vehicle cannot receive commands (UX affordance
  // only — see note below).
  //
  // IMPORTANT: This gate is a UX affordance, NOT an authorization control.
  // The Commands Lambda accepts POST /api/commands/{vehicleId} regardless of
  // connection state. Nothing here should be relied on for access decisions.
  // Authorization is enforced server-side.
  //
  // Allow-list gate: we gate on connectionStatus === 'connected', never on
  // connectionStatus === 'disconnected'. This ensures that undefined, null,
  // empty string, or any unrecognised value all result in buttons being
  // disabled (fail-closed), not enabled (fail-open). The realistic missing-prop
  // case — callers that don't yet pass connectionStatus — therefore disables
  // rather than enables, which is the safer default.
  connectionStatus?: string;
  // Live telemetry from `/vehicles/{id}` REST — passed through to
  // <VehicleStatePanel/> so remote-command effects (lock/ignition/etc.)
  // are visible on the next telemetry frame. Optional to preserve
  // backward compat with any caller that doesn't yet plumb it through.
  latestTelemetry?: Record<string, unknown> | null;
  // Parent-owned scoped fetch that only re-fetches + updates
  // `latestTelemetry`, leaving trips/safety/dtcs/campaigns state
  // untouched. Called on a 5s interval while this panel is mounted
  // (i.e. while the Remote Commands tab is the active tab). Optional
  // to preserve backward compat.
  //
  // See issue `2026-08-03-vehicle-state-panel-not-polled` for the
  // rationale on this choice — we deliberately reuse the existing
  // `/api/v1/vehicles/{id}` full-detail endpoint (no narrower endpoint
  // exists in main_api or the commands service) and mutate only the
  // telemetry slice on the client.
  onRefreshTelemetry?: () => Promise<void> | void;
}

const API = () => getCommandsApiBase();
// Poll telemetry at the same 5s cadence as Command History so both
// panels in the Remote Commands tab pulse in sync — reduces user
// cognitive load waiting for actuation feedback. `checkStatus` at 10s
// (in VehicleDetailView) is unaffected. Telemetry publishes roughly
// per-frame from the simulator, so faster polling buys nothing.
const TELEMETRY_POLL_MS = 5_000;

/**
 * The fleet operator's Quick Actions palette.
 *
 * spec `2026-09-14-cms-frontend-fleet-persona-alignment` Task 3.1 + F3.1.
 *
 * Driver-idiom commands (`honk_horn`, `flash_hazards`, `panic_mode`) were
 * removed: they belong to the driver's own phone (the iOS app / CVX driver
 * surface), not to a fleet operator's console. A fleet operator remotely
 * activating panic mode on a driver's van reads as alarming, not as a feature.
 *
 * What remains is the operator's legitimate palette: asset security
 * (lock/unlock), driver lockout recovery, duty-cycle prep — 5 entries
 * covering 4 distinct commands (`lock_all_doors` appears twice, as Lock
 * and Unlock, differing only by `val`).
 *
 * Exported so tests can assert the **exact array, order-sensitive**, rather
 * than probing the DOM for a denylist of the removed labels. F3.1: the
 * DOM-only test caught the three named removals but would have admitted an
 * arbitrary seventh command — its title claimed a count it never asserted.
 * Keep this at module level: it depends on no component state, and an
 * exported literal is what makes the exact-shape assertion possible.
 */
export const QUICK_ACTIONS: { cmd: string; label: string; val: any; variant?: 'primary' | 'normal' }[] = [
  { cmd: 'lock_all_doors', label: 'Lock All Doors', val: true, variant: 'primary' },
  { cmd: 'lock_all_doors', label: 'Unlock All Doors', val: false },
  { cmd: 'remote_start', label: 'Remote Start', val: true, variant: 'primary' },
  { cmd: 'find_my_vehicle', label: 'Find My Vehicle', val: true },
  { cmd: 'start_preconditioning', label: 'Pre-Condition Cabin', val: true },
];

export default function RemoteCommandsPanel({ vehicleId, connectionStatus, latestTelemetry, onRefreshTelemetry }: Props) {
  const { isAdmin, isOperator } = useUserRole();
  const [catalog, setCatalog] = useState<Record<string, ActuatorDef[]>>({});
  const [history, setHistory] = useState<Command[]>([]);
  const [geofences, setGeofences] = useState<Geofence[]>([]);
  const [sending, setSending] = useState<string | null>(null);
  const [flash, setFlash] = useState<any[]>([]);
  const [selectedCategory, setSelectedCategory] = useState('security');
  const [telemetryRefreshing, setTelemetryRefreshing] = useState(false);

  useEffect(() => {
    fetchCatalog();
    fetchHistory();
    fetchGeofences();
    const interval = setInterval(fetchHistory, 5000);
    return () => clearInterval(interval);
  }, [vehicleId]);

  // Poll `latestTelemetry` while THIS panel is mounted — i.e. while the
  // Remote Commands tab is the active tab. Cloudscape <Tabs/> unmounts
  // inactive tab content, so this effect's cleanup fires on tab-switch
  // and clears the interval. That keeps the rest of the vehicle-detail
  // page's request profile unchanged (Overview / DTCs / Trips / etc.
  // tabs don't trigger telemetry polls).
  //
  // No optimistic UI: the panel only shows what telemetry actually
  // reports, so a click's visible effect must wait for the round-trip.
  useEffect(() => {
    if (!onRefreshTelemetry) return;
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      setTelemetryRefreshing(true);
      try {
        await onRefreshTelemetry();
      } finally {
        if (!cancelled) setTelemetryRefreshing(false);
      }
    };
    // Kick off an immediate refresh on mount so the panel is fresh on
    // tab-switch-in (mount-time telemetry may be minutes old).
    tick();
    const id = setInterval(tick, TELEMETRY_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [onRefreshTelemetry]);

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

  // Fleet-operator palette — see `QUICK_ACTIONS` at module level for the rule
  // that produced the trim and why it is exported rather than inlined here.
  const quickActions = QUICK_ACTIONS;

  const categories = Object.keys(catalog).sort();

  // Render gate (spec `2026-09-14-cms-frontend-fleet-persona-alignment`, Task 3.2).
  // This is a UI-affordance gate only — the backend endpoint remains accessible.
  // Non-operator callers who bookmark the URL still reach the backend; server-side
  // authorization is the real control (out of scope per spec § Non-goals).
  if (!(isAdmin || isOperator)) {
    return (
      <Alert type="info">
        Remote commands are available to platform-admin and fleet-operator personas.
        This view is read-only for your persona.
      </Alert>
    );
  }

  return (
    <SpaceBetween size="l">
      {flash.length > 0 && <Flashbar items={flash} />}

      {/* Vehicle State — live actuator/powertrain state; updates on next
          telemetry frame after a remote command fires. Placed here (top
          of the Remote Commands tab) so the user's visual feedback loop
          is immediate — click Lock → state below flips. Poll cadence is
          driven by this panel's parent-owned `onRefreshTelemetry`
          callback; see issue 2026-08-03-vehicle-state-panel-not-polled.
       */}
      <VehicleStatePanel latestTelemetry={latestTelemetry} refreshing={telemetryRefreshing} />

      {/* Quick Actions */}
      <Container header={<Header variant="h2" description="Common vehicle commands">Quick Actions</Header>}>
        <SpaceBetween size="m">
          {/* Disconnection affordance — rendered only when the vehicle is not
              confirmed connected. This is a UX affordance, NOT an authorization
              control: the Commands Lambda accepts POST /api/commands/{vehicleId}
              regardless of connection state, so nothing here should be relied on
              for access decisions. The gate uses an allow-list (=== 'connected'),
              not a deny-list (!== 'disconnected'), so undefined/null/'' all
              disable rather than enable (fail-closed). */}
          {connectionStatus !== 'connected' && (
            <Alert type="warning">
              Vehicle is disconnected. Reconnect the vehicle to send commands.
            </Alert>
          )}
          <ColumnLayout columns={4}>
            {quickActions.map(({ cmd, label, val, variant }) => {
              const def = Object.values(catalog).flat().find(c => c.commandName === cmd);
              // Allow-list gate: disabled unless BOTH a catalog definition exists AND the
              // vehicle is confirmed connected. Gate on === 'connected' (allow-list), never
              // on !== 'disconnected' (deny-list) — the latter fails open for undefined/null.
              const isConnected = connectionStatus === 'connected';
              return (
                <Button key={`${cmd}-${val}`} fullWidth
                  variant={variant || 'normal'}
                  loading={sending === cmd}
                  disabled={!def || !isConnected}
                  onClick={() => def && isConnected && sendCommand(def, val)}
                >
                  {label}
                </Button>
              );
            })}
          </ColumnLayout>
        </SpaceBetween>
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
