/**
 * VehicleDTCsTable — first-class Active Diagnostic Trouble Codes panel.
 *
 * Renders rows from cms-<stage>-storage-dtc-history for a single vehicle,
 * sourced from the Fleet API `/api/v1/vehicles/{vehicleId}/dtcs` route.
 *
 * Rows may come from four different upstream producers:
 *   - source=flink-maintenance-processor → threshold-based Flink detection
 *   - source=fwe-uds-dtc                 → authentic FWE UDS 0x19 response
 *   - source=force_event.py              → operator-forced demo event
 *   - source=sovd                        → on-demand SOVD read
 *   - source=(missing)                   → legacy historical seed data
 *
 * A per-row badge shows which producer wrote the row, so operators can
 * trace a DTC back to its origin when debugging.
 *
 * Self-contained: fetches on mount + vehicleId change, manages its own
 * loading/error state, does not require the parent's consolidated payload.
 */
import React, { useState, useEffect, useMemo } from 'react';
import {
  Table,
  Box,
  Header,
  Button,
  SpaceBetween,
  Badge,
  Select,
  Pagination,
  Modal,
  ColumnLayout,
  Multiselect,
  Alert,
  FormField,
  Popover,
  ExpandableSection,
  Spinner,
  Flashbar,
} from '@cloudscape-design/components';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import { getApiEndpoint } from '../../../config/api';
import { useAuth } from '../../../auth/useAuth';
import { authFetch } from '../../../utils/authFetch';
import { getSimulationApiUrl } from '../../../utils/simulation-config';
import { ECU_OPTIONS } from './ecu_names';
import type { EcuOption } from './ecu_names';
import ClearDTCModal from './ClearDTCModal';

interface DTCRecord {
  vehicleId: string;
  timestamp: number;
  timestampIso?: string;
  dtcId?: string;
  code: string;
  status?: string;
  severity?: string;
  system?: string;
  description?: string;
  source?: string;
  triggerEventId?: string;
  maintenanceAlertType?: string;
  mileage?: number;
  firstSeenAt?: number;
  lastSeenAt?: number;
  occurrenceCount?: number;
  relatedServiceId?: string;
  persistent?: boolean;
  serviceRequired?: boolean;
  clearedDate?: string;
  /** SOVD freeze-frame data — flat map of signalName → {value, unit, timestamp} */
  freezeFrame?: Record<string, { value: number | string; unit: string; timestamp?: string }>;
  /** ECU identifier when populated by SOVD reads */
  ecu?: string;
}

interface Props {
  vehicleId?: string;
  /** Optional callback fired when the row count changes — lets the
   *  parent show a count on the tab label. */
  onCountChange?: (count: number) => void;
  /**
   * Vehicle connection status from the presence loop.
   * HARD GATE H: SOVD actions (Read DTCs, Full Scan, Clear DTC) are
   * enabled ONLY when this equals the string 'connected' — allow-list,
   * not deny-list.  Undefined, null, 'disconnected', or any other value
   * is treated as disconnected (fail-closed).
   */
  connectionStatus?: string;
}

/**
 * One row from GET /api/simulation/vehicle/{id}/faults.injectable —
 * the server-computed set of catalog codes that PUT /faults will
 * actually accept. This is the intersection of:
 *   - catalog entries carrying a dtc_code,
 *   - codes that resolve to an ECU via the SAE prefix (P/C/B/U + override table),
 *   - codes accepted by uds_dtc_responder.encode_dtc().
 * Never filter this list on the client — see spec § API surface. In
 * particular, do NOT restrict to category=='maintenance' (hides mapped
 * safety codes like C0035) and do NOT offer every event with a dtc_code
 * (13 of 37 would fail with 400).
 */
interface InjectableEvent {
  eventId: string;
  dtcCode: string;
  ecu: number;
  description: string;
}

/** faultState map attribute persisted on cms-<stage>-storage-vehicles items. */
interface FaultStatePayload {
  ecus?: Record<string, { req: number; resp: number; dtcs: string[] }>;
  eventIds?: string[];
  setAt?: string;
  setBy?: string;
  requestId?: string;
}

interface FaultsGetResponse {
  faultState?: FaultStatePayload | null;
  campaignRunning?: boolean;
  injectable?: InjectableEvent[];
}

const severityColor = (s?: string): 'red' | 'blue' | 'grey' => {
  if (s === 'CRITICAL' || s === 'HIGH') return 'red';
  if (s === 'MEDIUM') return 'blue';
  return 'grey';
};

/** Short, recognizable label for each DTC source.  Kept intentionally
 *  concise so the badge fits in a narrow table column. */
const sourceLabel = (src?: string): { label: string; color: 'blue' | 'green' | 'grey' | 'red' } => {
  switch (src) {
    case 'fwe-uds-dtc':
      return { label: 'UDS (FWE)', color: 'green' };
    case 'flink-maintenance-processor':
      return { label: 'Threshold', color: 'blue' };
    case 'force_event.py':
      return { label: 'Forced', color: 'red' };
    case 'sovd':
      return { label: 'SOVD', color: 'blue' };
    case undefined:
    case null:
    case '':
      return { label: 'Legacy', color: 'grey' };
    default:
      return { label: src, color: 'grey' };
  }
};

/** Format a timestamp (ISO or epoch ms/s) into a human-readable string.
 *  Falls back to the raw value when parsing fails, rather than showing
 *  "Invalid Date" which is meaningless to operators. */
const formatTimestamp = (dtc: DTCRecord): string => {
  if (dtc.timestampIso) {
    try {
      const d = new Date(dtc.timestampIso);
      if (!isNaN(d.getTime())) return d.toLocaleString();
    } catch { /* fall through */ }
    return dtc.timestampIso;
  }
  const raw = dtc.timestamp ?? dtc.firstSeenAt;
  if (typeof raw === 'number' && raw > 0) {
    const ms = raw > 9999999999 ? raw : raw * 1000;
    const d = new Date(ms);
    if (!isNaN(d.getTime())) return d.toLocaleString();
  }
  return 'N/A';
};

const formatEpochMs = (ms?: number): string => {
  if (typeof ms !== 'number' || ms <= 0) return 'N/A';
  const normalized = ms > 9999999999 ? ms : ms * 1000;
  const d = new Date(normalized);
  return isNaN(d.getTime()) ? 'N/A' : d.toLocaleString();
};

/** Compact relative age for the Status cell: "just now", "14 min ago", "5 days ago".
 *
 * Rendered in italics under an ACTIVE badge so an operator can tell at a glance
 * whether the vehicle is STILL reporting the DTC or the record is merely open.
 * The Last seen column keeps the absolute timestamp for precision; this is the
 * glanceable form.
 *
 * "ago" rather than "old" because it has to read correctly at both ends of the
 * range — "(6s old)" is odd, "(6s ago)" is not.
 */
const formatAge = (ms?: number): string => {
  const t = Number(ms ?? 0);
  if (!t) return '';
  const secs = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (secs < 10) return 'just now';
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hr${hrs === 1 ? '' : 's'} ago`;
  const days = Math.round(hrs / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
};

/** The 6 freeze-frame signals rendered as a first-class KeyValuePairs panel.
 *  Keys are exactly what the SOVD sidecar writes (camelCase). */
const KNOWN_FF_SIGNALS: Array<{ key: string; label: string }> = [
  { key: 'engineRpm',      label: 'Engine RPM' },
  { key: 'coolantTemp',    label: 'Coolant Temp' },
  { key: 'vehicleSpeed',   label: 'Vehicle Speed' },
  { key: 'engineLoad',     label: 'Engine Load' },
  { key: 'throttlePosition', label: 'Throttle Position' },
  { key: 'fuelTrim',       label: 'Fuel Trim' },
];

/** Render the freeze-frame expandable section for a DTC row.
 *  Not auto-opened — user must click to expand (HARD GATE spec § 4 D5). */
const FreezeFrameSection: React.FC<{
  freezeFrame: Record<string, { value: number | string; unit: string; timestamp?: string }>;
}> = ({ freezeFrame }) => {
  const knownItems = KNOWN_FF_SIGNALS
    .filter(s => freezeFrame[s.key] !== undefined)
    .map(s => {
      const sig = freezeFrame[s.key];
      return {
        label: s.label,
        value: `${sig.value} ${sig.unit}`.trim(),
      };
    });

  const additionalKeys = Object.keys(freezeFrame).filter(
    k => !KNOWN_FF_SIGNALS.some(s => s.key === k),
  );

  return (
    <ExpandableSection headerText="Freeze frame">
      {knownItems.length > 0 && (
        <KeyValuePairs
          columns={3}
          items={knownItems}
        />
      )}
      {additionalKeys.length > 0 && (
        <ExpandableSection headerText={`Additional signals (${additionalKeys.length})`}>
          <Table
            variant="embedded"
            columnDefinitions={[
              { id: 'signal', header: 'Signal', cell: (item: string) => item, width: 200 },
              {
                id: 'value',
                header: 'Value',
                cell: (item: string) => {
                  const sig = freezeFrame[item];
                  return `${sig.value} ${sig.unit}`.trim();
                },
              },
            ]}
            items={additionalKeys}
          />
        </ExpandableSection>
      )}
    </ExpandableSection>
  );
};

const VehicleDTCsTable: React.FC<Props> = ({ vehicleId, onCountChange, connectionStatus }) => {
  const { getAuthHeaders, user } = useAuth();
  const groups: string[] = (user as any)?.groups || [];
  const isViewer = groups.includes('fleet-viewer') && !groups.includes('fleet-operator') && !groups.includes('platform-admin');
  const apiEndpoint = getApiEndpoint();

  // HARD GATE H: allow-list — only the exact string 'connected' enables SOVD ops.
  // Fail-closed on undefined, null, 'disconnected', or any unrecognised value.
  const isConnected = connectionStatus === 'connected';

  const [dtcs, setDtcs] = useState<DTCRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /** Tracks which DTC's "Mark Cleared" button is in-flight, so only that
   *  row's button goes into loading state (not the whole table). */
  const [clearingDtcIds, setClearingDtcIds] = useState<Set<string>>(new Set());

  // Filter controls
  const [statusFilter, setStatusFilter] = useState<'ALL' | 'ACTIVE' | 'CLEARED'>('ALL');
  const [sourceFilter, setSourceFilter] = useState<string>('ALL');

  // Client-side pagination (API returns up to 200 rows per call, which
  // is more than any vehicle is realistically expected to carry)
  const [currentPage, setCurrentPage] = useState(1);
  const pageSize = 20;

  // Schedule Service modal state
  const [scheduleModalItem, setScheduleModalItem] = useState<DTCRecord | null>(null);
  const [scheduling, setScheduling] = useState(false);
  const [scheduleError, setScheduleError] = useState<string | null>(null);

  // --- Fault injection state (spec 2026-08-05-cms-parked-vehicle-dtc-injection) ---
  // Currently-injected faults, injectable options, and standing-campaign
  // liveness are all read from a single GET /faults request against the
  // simulation Lambda. The DTC table above continues to load from the
  // fleet API's `/dtcs` route — the two payloads are independent, and a
  // /faults failure must not block the DTC table.
  const [faultState, setFaultState] = useState<FaultStatePayload | null>(null);
  const [injectable, setInjectable] = useState<InjectableEvent[]>([]);
  const [campaignRunning, setCampaignRunning] = useState<boolean>(false);
  const [injectionModalVisible, setInjectionModalVisible] = useState(false);
  const [selectedInjection, setSelectedInjection] = useState<
    Array<{ label: string; value: string; description?: string }>
  >([]);
  const [injecting, setInjecting] = useState(false);
  const [injectionError, setInjectionError] = useState<string | null>(null);
  const [injectionResult, setInjectionResult] = useState<
    { clearGuidance?: string; appliesWithinSeconds?: number; cleared?: boolean } | null
  >(null);

  // --- Clear faults confirmation modal state ---
  const [clearFaultsModalVisible, setClearFaultsModalVisible] = useState(false);
  const [clearFaultsConfirming, setClearFaultsConfirming] = useState(false);
  const [clearFaultsError, setClearFaultsError] = useState<string | null>(null);

  // --- SOVD Read DTCs state ---
  /** Whether the ECU select dropdown is open inline next to the Read DTCs button */
  const [readDtcsSelectOpen, setReadDtcsSelectOpen] = useState(false);
  const [selectedEcu, setSelectedEcu] = useState<EcuOption | null>(null);
  /** Full Scan in-flight flag */
  const [fullScanLoading, setFullScanLoading] = useState(false);
  /** Read DTCs in-flight flag */
  const [readDtcsLoading, setReadDtcsLoading] = useState(false);
  /** Toast notifications (Flashbar items) for SOVD operations */
  const [sovdFlashItems, setSovdFlashItems] = useState<any[]>([]);

  // --- Clear DTC modal state (per-row SOVD clear) ---
  const [clearDtcModalItem, setClearDtcModalItem] = useState<DTCRecord | null>(null);

  const load = async () => {
    if (!vehicleId || !apiEndpoint) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      // Server-side status filter keeps the payload small for filtered views;
      // source filter is applied client-side since we want the "ALL" view by
      // default and the payload is already small.
      const params = new URLSearchParams({ limit: '200' });
      if (statusFilter !== 'ALL') params.set('status', statusFilter);
      const url = `${apiEndpoint}api/v1/vehicles/${vehicleId}/dtcs?${params.toString()}`;
      const res = await fetch(url, {
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders(),
        },
      });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      }
      const data = await res.json();
      setDtcs(data.dtcs || []);
      onCountChange?.(data.total ?? (data.dtcs || []).length);
    } catch (e: any) {
      console.error('Failed to load DTCs:', e);
      setError(e?.message || 'Failed to load DTC history');
      setDtcs([]);
      onCountChange?.(0);
    } finally {
      setLoading(false);
    }
  };

  /** Mark a DTC as CLEARED via PATCH /api/v1/vehicles/{id}/dtcs/{dtcId}.
   *  Reloads the table on success so the row transitions to CLEARED. The
   *  row isn't removed — operators can still see cleared DTCs by switching
   *  the status filter to "Cleared only" for audit. */
  const clearDtc = async (item: DTCRecord) => {
    if (!vehicleId || !item.dtcId) return;
    setClearingDtcIds(prev => new Set(prev).add(item.dtcId!));
    try {
      const url = `${apiEndpoint}api/v1/vehicles/${vehicleId}/dtcs/${item.dtcId}`;
      const res = await fetch(url, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({}),  // relatedServiceId optional; operator-triggered clear
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(`HTTP ${res.status}: ${body.slice(0, 200)}`);
      }
      await load();  // refresh so row flips to CLEARED
    } catch (e: any) {
      console.error('Failed to clear DTC:', e);
      setError(e?.message || 'Failed to clear DTC');
    } finally {
      setClearingDtcIds(prev => {
        const next = new Set(prev);
        if (item.dtcId) next.delete(item.dtcId);
        return next;
      });
    }
  };

  const scheduleService = async () => {
    if (!vehicleId || !scheduleModalItem?.dtcId) return;
    setScheduling(true);
    setScheduleError(null);
    try {
      const url = `${apiEndpoint}api/v1/vehicles/${vehicleId}/dtcs/${scheduleModalItem.dtcId}/schedule-service`;
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({}),
      });
      if (res.status === 409) {
        const body = await res.json().catch(() => ({}));
        const existingId = body.serviceId || body.relatedServiceId || '';
        setScheduleError(`already scheduled${existingId ? `: ${existingId}` : ''}`);
        return;
      }
      if (!res.ok) {
        const body = await res.text();
        throw new Error(`HTTP ${res.status}: ${body.slice(0, 200)}`);
      }
      setScheduleModalItem(null);
      await load();
    } catch (e: any) {
      console.error('Failed to schedule service:', e);
      setScheduleError(e?.message || 'Failed to schedule service');
    } finally {
      setScheduling(false);
    }
  };

  /**
   * Full Scan — POST /api/commands/{vehicleId} with command_type=read_dtcs,
   * components=['*'], include_freeze_frame=true.
   *
   * Uses authFetch (same pattern as fault injection above) so the Cognito
   * ID token is attached automatically.  Shows a "Scanning…" toast while
   * the command is in flight.  Reloads the DTC table on success so any
   * SOVD-discovered codes appear immediately.
   */
  const sendFullScan = async () => {
    if (!vehicleId || !apiEndpoint) return;
    setFullScanLoading(true);
    setSovdFlashItems([{
      type: 'in-progress',
      content: 'Scanning…',
      id: 'sovd-scan',
      dismissible: false,
    }]);
    try {
      const url = `${apiEndpoint}api/commands/${vehicleId}`;
      const res = await authFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          command_type: 'read_dtcs',
          components: ['*'],
          include_freeze_frame: true,
        }),
      });
      if (!res.ok) {
        const body = await res.text();
        setSovdFlashItems([{
          type: 'error',
          content: `Full Scan failed: HTTP ${res.status} — ${body.slice(0, 200)}`,
          id: 'sovd-scan-err',
          dismissible: true,
          onDismiss: () => setSovdFlashItems([]),
        }]);
        return;
      }
      setSovdFlashItems([{
        type: 'success',
        content: 'Full scan submitted. DTCs will appear below within ~15 s.',
        id: 'sovd-scan-ok',
        dismissible: true,
        onDismiss: () => setSovdFlashItems([]),
      }]);
      // Refresh table so SOVD rows appear once the response handler writes them
      await load();
    } catch (e: any) {
      setSovdFlashItems([{
        type: 'error',
        content: e?.message || 'Full Scan failed',
        id: 'sovd-scan-err',
        dismissible: true,
        onDismiss: () => setSovdFlashItems([]),
      }]);
    } finally {
      setFullScanLoading(false);
    }
  };

  /**
   * Read DTCs for a specific ECU — POST /api/commands/{vehicleId} with
   * command_type=read_dtcs, components=[ecuName], include_freeze_frame=true.
   */
  const sendReadDtcs = async (ecu: EcuOption) => {
    if (!vehicleId || !apiEndpoint) return;
    setReadDtcsLoading(true);
    setReadDtcsSelectOpen(false);
    setSelectedEcu(null);
    setSovdFlashItems([{
      type: 'in-progress',
      content: `Reading DTCs from ${ecu.label}…`,
      id: 'sovd-read',
      dismissible: false,
    }]);
    try {
      const url = `${apiEndpoint}api/commands/${vehicleId}`;
      const res = await authFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          command_type: 'read_dtcs',
          components: [ecu.value],
          include_freeze_frame: true,
        }),
      });
      if (!res.ok) {
        const body = await res.text();
        setSovdFlashItems([{
          type: 'error',
          content: `Read DTCs failed: HTTP ${res.status} — ${body.slice(0, 200)}`,
          id: 'sovd-read-err',
          dismissible: true,
          onDismiss: () => setSovdFlashItems([]),
        }]);
        return;
      }
      setSovdFlashItems([{
        type: 'success',
        content: `Read DTC request submitted for ${ecu.label}. DTCs will appear below within ~15 s.`,
        id: 'sovd-read-ok',
        dismissible: true,
        onDismiss: () => setSovdFlashItems([]),
      }]);
      await load();
    } catch (e: any) {
      setSovdFlashItems([{
        type: 'error',
        content: e?.message || 'Read DTCs failed',
        id: 'sovd-read-err',
        dismissible: true,
        onDismiss: () => setSovdFlashItems([]),
      }]);
    } finally {
      setReadDtcsLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicleId, statusFilter]);

  /** GET /api/simulation/vehicle/{vehicleId}/faults
   *  Populates the "currently injected" panel + the injectable option list
   *  used by the Set-fault modal. Non-fatal on failure — the DTC table
   *  above continues to function. */
  const loadFaults = async () => {
    if (!vehicleId) return;
    try {
      const url = getSimulationApiUrl(`/vehicle/${vehicleId}/faults`);
      const res = await authFetch(url);
      if (!res.ok) {
        setFaultState(null);
        setInjectable([]);
        setCampaignRunning(false);
        return;
      }
      const data: FaultsGetResponse = await res.json();
      setFaultState(data.faultState || null);
      setInjectable(data.injectable || []);
      setCampaignRunning(!!data.campaignRunning);
    } catch (e) {
      // Non-fatal — DTC table remains usable even if simulation API is down.
      console.warn('Failed to load /faults:', e);
      setFaultState(null);
      setInjectable([]);
      setCampaignRunning(false);
    }
  };

  /** PUT /api/simulation/vehicle/{vehicleId}/faults
   *  Full-replacement write. Empty selection clears faultState — that
   *  is the deliberate first half of the two-step clear (spec § Clearing
   *  a fault). Surfaces 400/409/403 bodies verbatim so operators see
   *  the actual constraint that fired rather than a generic failure. */
  const injectFaults = async () => {
    if (!vehicleId) return;
    setInjecting(true);
    setInjectionError(null);
    setInjectionResult(null);
    const eventIds = selectedInjection.map(o => o.value);
    try {
      const url = getSimulationApiUrl(`/vehicle/${vehicleId}/faults`);
      const res = await authFetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ maintenance_scenarios: eventIds }),
      });
      if (res.status === 409) {
        const body: { error?: string } = await res.json().catch(() => ({}));
        setInjectionError(
          body.error ||
            'No standing "uds-dtc-polling" campaign is running. Assign it from the Campaigns tab, then retry.',
        );
        return;
      }
      if (res.status === 400) {
        const body: { error?: string } = await res.json().catch(() => ({}));
        setInjectionError(body.error || 'Invalid injection request.');
        return;
      }
      if (res.status === 403) {
        setInjectionError('You do not have permission to inject faults on this vehicle.');
        return;
      }
      if (!res.ok) {
        const text = await res.text();
        setInjectionError(`HTTP ${res.status}: ${text.slice(0, 200)}`);
        return;
      }
      const data: {
        cleared?: boolean;
        clearGuidance?: string;
        appliesWithinSeconds?: number;
      } = await res.json();
      // {cleared: true} for empty-list REMOVE; clearGuidance+appliesWithinSeconds for a set.
      setInjectionResult({
        clearGuidance: data.clearGuidance,
        appliesWithinSeconds: data.appliesWithinSeconds,
        cleared: data.cleared,
      });
      setInjectionModalVisible(false);
      setSelectedInjection([]);
      await loadFaults();
    } catch (e: any) {
      setInjectionError(e?.message || 'Failed to inject faults');
    } finally {
      setInjecting(false);
    }
  };

  /** True when the vehicle currently has at least one fault code set on the bus.
   *  Used to enable/disable the "Clear faults" button. */
  // ── Is the vehicle STILL reporting this DTC? ─────────────────────────
  // `status` is the RECORD lifecycle (ACTIVE = unresolved, CLEARED = closed).
  // It does NOT mean the ECU is reporting it right now: after the fault is
  // cleared on the bus, rows stay ACTIVE until each is marked cleared.
  // Reading ACTIVE as "live on the vehicle" is the most confusing thing on
  // this screen, so derive the real answer and surface both.
  //
  // Threshold = 2 missed UDS polls. The standing uds-dtc campaign polls
  // every 30s, so >90s without a refresh means the ECU has gone quiet.
  // Freshness window. `lastSeenAt` is stamped when the DTC reaches the CLOUD,
  // not when the ECU answered, so on a genuinely-live DTC it already lags by
  // one UDS poll interval (30s) plus the FWE batch + MSK + Flink legs
  // (~40s measured on staging) — about 70s worst case.
  //
  // 90s left only ~20s of margin, and a false GREY is the worse error: it tells
  // the operator the vehicle has gone quiet when it has not. 120s (4 poll
  // intervals) keeps the useful signal while surviving one slow pipeline run.
  const REPORTING_STALE_MS = 120_000;
  const REPORTING_STALE_S = Math.round(REPORTING_STALE_MS / 1000);
  const isCurrentlyReported = (item: DTCRecord): boolean => {
    if (item.status !== 'ACTIVE') return false;
    const ls = Number(item.lastSeenAt ?? 0);
    if (!ls) return false;
    return Date.now() - ls < REPORTING_STALE_MS;
  };

  const hasFaultsSet = !!(
    faultState &&
    faultState.ecus &&
    Object.values(faultState.ecus).some(e => (e.dtcs?.length ?? 0) > 0)
  );

  /** PUT /api/simulation/vehicle/{vehicleId}/faults with maintenance_scenarios: []
   *  Reuses the existing injectFaults code path — an empty list is the
   *  deliberate clear path (spec § Clearing a fault). */
  const clearFaults = async () => {
    if (!vehicleId) return;
    setClearFaultsConfirming(true);
    setClearFaultsError(null);
    setInjectionResult(null);
    try {
      const url = getSimulationApiUrl(`/vehicle/${vehicleId}/faults`);
      const res = await authFetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ maintenance_scenarios: [] }),
      });
      if (res.status === 409) {
        const body: { error?: string } = await res.json().catch(() => ({}));
        setClearFaultsError(
          body.error ||
            'No standing "uds-dtc-polling" campaign is running. Assign it from the Campaigns tab, then retry.',
        );
        return;
      }
      if (!res.ok) {
        const text = await res.text();
        setClearFaultsError(`HTTP ${res.status}: ${text.slice(0, 200)}`);
        return;
      }
      const data: { cleared?: boolean } = await res.json().catch(() => ({}));
      setInjectionResult({ cleared: data.cleared ?? true });
      setClearFaultsModalVisible(false);
      await loadFaults();
    } catch (e: any) {
      setClearFaultsError(e?.message || 'Failed to clear faults');
    } finally {
      setClearFaultsConfirming(false);
    }
  };

  // Faults state has a different reload cadence than the DTC table — it
  // does not react to status/source filters, only to vehicleId changes
  // and to explicit refreshes after a successful PUT /faults.
  useEffect(() => {
    loadFaults();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicleId]);

  // Distinct sources present in the current data — drives the source filter dropdown.
  const availableSources = useMemo(() => {
    const s = new Set<string>();
    for (const d of dtcs) {
      s.add(d.source || '');
    }
    return Array.from(s);
  }, [dtcs]);

  const filteredDtcs = useMemo(() => {
    // Source filter first
    let items = dtcs;
    if (sourceFilter === '_MISSING') items = items.filter(d => !d.source);
    else if (sourceFilter !== 'ALL') items = items.filter(d => d.source === sourceFilter);

    // Re-sort with the "most actionable first" ladder (added 2026-05-04):
    //   1. status=ACTIVE before CLEARED/PENDING/anything else
    //   2. severity DESC (CRITICAL → HIGH → MEDIUM → LOW → UNKNOWN)
    //   3. timestamp DESC (newer first)
    // The server already returns newest-first by timestamp, but without
    // this re-sort a new low-severity DTC would appear above an older
    // critical one — exactly the wrong order for triage.
    const severityRank = (s?: string): number => {
      const v = (s || '').toUpperCase();
      if (v === 'CRITICAL') return 0;
      if (v === 'HIGH') return 1;
      if (v === 'MEDIUM') return 2;
      if (v === 'LOW') return 3;
      return 4;
    };
    const statusRank = (s?: string): number => (s === 'ACTIVE' ? 0 : 1);
    const tsOf = (d: DTCRecord): number =>
      typeof d.timestamp === 'number'
        ? d.timestamp
        : typeof d.firstSeenAt === 'number'
        ? d.firstSeenAt
        : 0;

    // Sort a shallow copy so React doesn't see us mutating props-derived state.
    return [...items].sort((a, b) => {
      const sr = statusRank(a.status) - statusRank(b.status);
      if (sr !== 0) return sr;
      const vr = severityRank(a.severity) - severityRank(b.severity);
      if (vr !== 0) return vr;
      return tsOf(b) - tsOf(a); // newest first
    });
  }, [dtcs, sourceFilter]);

  // Reset page when filter changes so we don't land on an empty page.
  useEffect(() => {
    setCurrentPage(1);
  }, [statusFilter, sourceFilter]);

  const paginatedDtcs = useMemo(() => {
    const start = (currentPage - 1) * pageSize;
    return filteredDtcs.slice(start, start + pageSize);
  }, [filteredDtcs, currentPage]);

  const pagesCount = Math.max(1, Math.ceil(filteredDtcs.length / pageSize));

  const serviceIdPreview = scheduleModalItem
    ? `SVC-${(scheduleModalItem.dtcId || '').slice(0, 8)}-${Math.floor(Date.now() / 1000)}`
    : '';

  return (
    <SpaceBetween size="s">
      {/*
        Row-height note (2026-05-04): this table used to render with
        variant="full-page" wrapped in <Container>. full-page is sized
        for standalone page content (extra top/bottom padding on every
        row, roomy header gutter) and compounds badly with the outer
        Container's own padding plus the parent <Tabs> shell. That's
        why DTC rows looked ~2-3x taller than necessary. Switching to
        variant="embedded" (designed for nesting inside another
        container — here, Tabs) removes the extra row padding without
        losing sticky header / keyboard nav. Also dropped the Code
        cell's <Box fontSize="body-m"> wrapper, which forced a larger
        line-height than the rest of the row.
      */}
      {sovdFlashItems.length > 0 && (
        <Flashbar items={sovdFlashItems} />
      )}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '8px',
          minHeight: '40px',
        }}
      >
          <Header variant="h2" counter={`(${filteredDtcs.length})`}>
            Diagnostic Trouble Codes
          </Header>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <Select
              selectedOption={{
                value: statusFilter,
                label:
                  statusFilter === 'ALL'
                    ? 'All statuses'
                    : statusFilter === 'ACTIVE'
                    ? 'Active only'
                    : 'Cleared only',
              }}
              onChange={({ detail }) => {
                const v = (detail.selectedOption.value || 'ALL') as 'ALL' | 'ACTIVE' | 'CLEARED';
                setStatusFilter(v);
              }}
              options={[
                { value: 'ALL', label: 'All statuses' },
                { value: 'ACTIVE', label: 'Active only' },
                { value: 'CLEARED', label: 'Cleared only' },
              ]}
            />
            {/* Source filter — documented values: flink-maintenance-processor,
                fwe-uds-dtc, force_event.py, sovd, and (missing)/legacy.
                sovd added 2026-09-01 for on-demand SOVD reads. */}
            <Select
              selectedOption={{
                value: sourceFilter,
                label:
                  sourceFilter === 'ALL'
                    ? 'All sources'
                    : sourceFilter === '_MISSING'
                    ? 'Legacy'
                    : sourceLabel(sourceFilter).label,
              }}
              onChange={({ detail }) => setSourceFilter(detail.selectedOption.value || 'ALL')}
              options={[
                { value: 'ALL', label: 'All sources' },
                // Dynamic sources from the current dataset
                ...availableSources.map(src => ({
                  value: src === '' ? '_MISSING' : src,
                  label: src === '' ? 'Legacy' : sourceLabel(src).label,
                })),
                // Ensure 'sovd' is always present as a documented option
                ...(availableSources.includes('sovd') ? [] : [
                  { value: 'sovd', label: 'SOVD' },
                ]),
              ]}
            />
            <Button iconName="refresh" onClick={load} ariaLabel="Refresh DTCs" />
            {/* SOVD Read DTCs button (HARD GATE H: disabled unless connected) */}
            {!isViewer && (
              <Button
                iconName="refresh"
                disabled={!isConnected}
                loading={readDtcsLoading}
                disabledReason="Vehicle must be connected to read DTCs on demand."
                onClick={() => setReadDtcsSelectOpen(prev => !prev)}
                ariaLabel="Read DTCs"
              >
                {readDtcsLoading ? <Spinner /> : 'Read DTCs'}
              </Button>
            )}
            {/* Inline ECU select — shown when Read DTCs is clicked */}
            {!isViewer && readDtcsSelectOpen && (
              <Select
                selectedOption={selectedEcu ? { value: selectedEcu.value, label: selectedEcu.label } : null}
                placeholder="Select ECU…"
                onChange={({ detail }) => {
                  const opt = ECU_OPTIONS.find(e => e.value === detail.selectedOption.value);
                  if (opt) {
                    setSelectedEcu(opt);
                    sendReadDtcs(opt);
                  }
                }}
                options={ECU_OPTIONS.map(e => ({ value: e.value, label: e.label }))}
              />
            )}
            {/* SOVD Full Scan button (HARD GATE H: disabled unless connected) */}
            {!isViewer && (
              <Button
                iconName="search"
                disabled={!isConnected}
                loading={fullScanLoading}
                disabledReason="Vehicle must be connected to run a full diagnostic scan."
                onClick={sendFullScan}
                ariaLabel="Full Scan"
              >
                {fullScanLoading ? <Spinner /> : 'Full Scan'}
              </Button>
            )}
            {!isViewer && (
              <Button
                onClick={() => {
                  setInjectionError(null);
                  setSelectedInjection([]);
                  setInjectionResult(null);
                  setInjectionModalVisible(true);
                }}
                ariaLabel="Set fault on vehicle"
              >
                Set fault
              </Button>
            )}
            {!isViewer && (
              <Button
                onClick={() => {
                  setClearFaultsError(null);
                  setClearFaultsModalVisible(true);
                }}
                disabled={!hasFaultsSet}
                // A disabled button with no stated reason reads as broken. Cloudscape
                // renders disabledReason as a hover tooltip and keeps the control
                // focusable so keyboard users can discover it too.
                disabledReason="No faults are currently set on this vehicle. Use Set fault first."
                ariaLabel="Clear faults on vehicle"
              >
                Clear faults
              </Button>
            )}
            <Popover
              dismissButton={false}
              position="left"
              size="medium"
              triggerType="custom"
              content={
                <SpaceBetween size="xs">
                  <Box variant="p">
                    <strong>Clearing faults vs. marking DTCs cleared are two separate operations.</strong>
                  </Box>
                  <Box variant="p">
                    <strong>Clear faults</strong> tells the vehicle's ECU to stop reporting the
                    fault codes on the bus. Use this first.
                  </Box>
                  <Box variant="p">
                    <strong>Mark cleared</strong> (the check-mark on each DTC row) closes the
                    history record in this table.
                  </Box>
                  <Box variant="p" color="text-body-secondary">
                    Do them in this order. If you mark a record cleared while the ECU is still
                    reporting, the next poll (~30–40 s) will resurrect it as a new ACTIVE row.
                    To clear only <em>some</em> faults, use <em>Set fault</em> and deselect the
                    codes you want to stop — the multiselect already handles partial clearing.
                  </Box>
                  <div>
                    <strong>Status ACTIVE</strong> means the record is unresolved — not that the
                    vehicle is reporting it. A <strong>red</strong> badge means it is being reported
                    within the last {REPORTING_STALE_S}s; <strong>grey</strong> means it has gone quiet and only the record is
                    still open.
                  </div>
                </SpaceBetween>
              }
            >
              <Button
                variant="inline-icon"
                iconName="status-info"
                ariaLabel="About clearing faults and DTCs"
              />
            </Popover>
            {pagesCount > 1 && (
              <Pagination
                currentPageIndex={currentPage}
                pagesCount={pagesCount}
                onChange={({ detail }) => setCurrentPage(detail.currentPageIndex)}
              />
            )}
          </div>
        </div>

        {error && (
          <Box color="text-status-error" variant="p">
            {error}
          </Box>
        )}

        {faultState && faultState.eventIds && faultState.eventIds.length > 0 && (
          <Alert
            type="info"
            header={`Faults currently injected on this vehicle (${
              Object.values(faultState.ecus || {}).reduce(
                (acc, e) => acc + (e.dtcs?.length || 0),
                0,
              )
            })`}
          >
            <SpaceBetween size="xs">
              <div>
                <strong>Codes:</strong>{' '}
                {Object.entries(faultState.ecus || {})
                  .map(([ecu, e]) => `ECU${ecu}: ${(e.dtcs || []).join(', ')}`)
                  .join(' · ')}
              </div>
              {faultState.setAt && (
                <div>
                  <strong>Set at:</strong> {new Date(faultState.setAt).toLocaleString()}
                  {faultState.setBy ? ` by ${faultState.setBy}` : ''}
                </div>
              )}
              <Box color="text-body-secondary" variant="small">
                To clear: click <em>Clear faults</em> above, wait ~40 s, then use the
                check-mark button on each DTC row (Mark cleared). Clearing the row
                first while the ECU is still reporting will resurrect it on the next 30 s poll.
              </Box>
            </SpaceBetween>
          </Alert>
        )}

        {injectionResult && (
          <Alert
            type="success"
            dismissible
            onDismiss={() => setInjectionResult(null)}
          >
            {injectionResult.cleared
              ? 'Fault state cleared. The ECU will stop reporting within one poll interval (~40 s); then use Mark cleared on each DTC row.'
              : (
                <>
                  Fault request accepted.
                  {typeof injectionResult.appliesWithinSeconds === 'number' &&
                    ` The bus will report within ~${injectionResult.appliesWithinSeconds}s.`}
                  {injectionResult.clearGuidance && ` ${injectionResult.clearGuidance}`}
                </>
              )}
          </Alert>
        )}

        <Table
          loading={loading}
          loadingText="Loading DTCs..."
          enableKeyboardNavigation={true}
          items={paginatedDtcs}
          trackBy={(item: DTCRecord) => item.dtcId || `${item.vehicleId}-${item.timestamp}`}
          columnDefinitions={[
            {
              id: 'code',
              header: 'Code',
              // Use Box variant="strong" (semantic bold) instead of
              // forcing a larger font-size that bloats the row height.
              cell: (item: DTCRecord) => <Box variant="strong">{item.code || '—'}</Box>,
              width: 80,
            },
            {
              id: 'severity',
              header: 'Severity',
              cell: (item: DTCRecord) => (
                <Badge color={severityColor(item.severity)}>{item.severity || 'UNKNOWN'}</Badge>
              ),
              width: 90,
            },
            {
              id: 'status',
              // Colour legend lives ON the column, because the meaning of red vs grey is
              // not guessable — the person who specified it forgot within the hour.
              header: (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                  Status
                  <Popover
                    dismissButton={false}
                    position="bottom"
                    size="medium"
                    triggerType="custom"
                    header="What the status colours mean"
                    content={
                      <SpaceBetween size="xs">
                        <div>
                          <Badge color="red">ACTIVE</Badge>{' '}
                          the vehicle reported this within the last{' '}
                    <strong>{REPORTING_STALE_S}s</strong>. See the italic age under the badge.
                        </div>
                        <div>
                          <Badge color="grey">ACTIVE</Badge>{' '}
                          no report for over <strong>{REPORTING_STALE_S}s</strong>, so the ECU has
                    stopped reporting it. The record is still open.
                          Use <em>Mark cleared</em> to close it.
                        </div>
                        <div>
                          <Badge color="green">CLEARED</Badge>{' '}
                          the record is closed. Nothing outstanding.
                        </div>
                        <Box variant="small" color="text-body-secondary">
                          The italic age under a badge is when the vehicle last reported it.
                          ACTIVE describes the <em>record</em>, not the vehicle — which is why
                          a grey ACTIVE is normal after clearing faults.
                        </Box>
                      </SpaceBetween>
                    }
                  >
                    <Button
                      variant="inline-icon"
                      iconName="status-info"
                      ariaLabel="What the status colours mean"
                    />
                  </Popover>
                </span>
              ),
              cell: (item: DTCRecord) => {
                const s = item.status || 'UNKNOWN';
                const reported = isCurrentlyReported(item);
                const seen = Number(item.lastSeenAt ?? 0);
                const color: 'red' | 'grey' | 'green' | 'blue' =
                  s === 'ACTIVE' ? (reported ? 'red' : 'grey')
                  : s === 'CLEARED' ? 'green'
                  : s === 'PENDING' ? 'blue'
                  : 'grey';
                const title =
                  s !== 'ACTIVE'
                    ? `Record ${s.toLowerCase()}.`
                    : reported
                      ? `Reported by the vehicle within the last ${REPORTING_STALE_S}s — treated as still being reported.`
                      : seen
                        ? `No report from the vehicle for over ${REPORTING_STALE_S}s (last at ${new Date(seen).toLocaleTimeString()}). The record is still open — use Mark cleared to close it.`
                        : 'Record still open. No report seen from the vehicle yet.';
                const age = s === 'ACTIVE' ? formatAge(seen) : '';
                return (
                  <span title={title}>
                    <Badge color={color}>{s}</Badge>
                    {age && (
                      <Box variant="small" color="text-body-secondary">
                        <i style={{ whiteSpace: 'nowrap' }}>({age})</i>
                      </Box>
                    )}
                  </span>
                );
              },
              width: 150,
            },
            {
              id: 'system',
              header: 'System',
              cell: (item: DTCRecord) => item.system || '—',
              width: 130,
            },
            {
              id: 'description',
              header: 'Description',
              cell: (item: DTCRecord) => {
                const desc = item.description || item.maintenanceAlertType || item.triggerEventId || '—';
                const hasFF = item.freezeFrame && Object.keys(item.freezeFrame).length > 0;
                return (
                  <SpaceBetween size="xs">
                    <span>{desc}</span>
                    {hasFF && item.freezeFrame && (
                      <FreezeFrameSection freezeFrame={item.freezeFrame} />
                    )}
                  </SpaceBetween>
                );
              },
              width: 300,
              maxWidth: 400,
            },
            {
              id: 'source',
              header: 'Source',
              cell: (item: DTCRecord) => {
                const { label, color } = sourceLabel(item.source);
                return <Badge color={color}>{label}</Badge>;
              },
              width: 130,
            },
            {
              id: 'firstSeenAt',
              header: 'First seen',
              cell: (item: DTCRecord) =>
                item.firstSeenAt ? formatEpochMs(item.firstSeenAt) : formatTimestamp(item),
              width: 180,
            },
            {
              id: 'lastSeenAt',
              header: 'Last seen',
              cell: (item: DTCRecord) => formatEpochMs(item.lastSeenAt),
              width: 180,
            },
            {
              id: 'occurrenceCount',
              header: 'Detections',
              cell: (item: DTCRecord) => {
                const count = item.occurrenceCount;
                if (typeof count !== 'number') return '—';
                if (count > 1) return <Badge color="blue">{String(count)}</Badge>;
                return <span>{String(count)}</span>;
              },
              width: 100,
            },
            {
              id: 'actions',
              header: 'Actions',
              cell: (item: DTCRecord) => {
                if (item.status === 'CLEARED') {
                  return null;
                }
                const inFlight = clearingDtcIds.has(item.dtcId || '');
                const canSchedule =
                  item.status === 'ACTIVE' && !item.relatedServiceId && !isViewer;
                return (
                  <SpaceBetween direction="horizontal" size="xs">
                    <Button
                      variant="inline-icon"
                      iconName={inFlight ? 'status-in-progress' : 'check'}
                      onClick={() => clearDtc(item)}
                      loading={inFlight}
                      disabled={inFlight}
                      ariaLabel={inFlight ? 'Clearing DTC…' : 'Mark cleared'}
                    />
                    {canSchedule && (
                      <Button
                        variant="inline-icon"
                        iconName="calendar"
                        onClick={() => {
                          setScheduleError(null);
                          setScheduleModalItem(item);
                        }}
                        ariaLabel="Schedule service"
                      />
                    )}
                    {/* SOVD per-row Clear DTC button (HARD GATE H: disabled unless connected) */}
                    {!isViewer && (
                      <Button
                        variant="link"
                        iconName="close"
                        disabled={!isConnected}
                        disabledReason="Vehicle must be connected to clear DTCs remotely."
                        onClick={() => setClearDtcModalItem(item)}
                        ariaLabel="Clear DTC remotely"
                      >
                        Clear
                      </Button>
                    )}
                  </SpaceBetween>
                );
              },
              width: 220,
            },
          ]}
          variant="embedded"
          wrapLines={true}
          stickyHeader={true}
          empty={
            <Box textAlign="center" color="inherit">
              <Box variant="strong" textAlign="center" color="inherit">
                No DTCs
              </Box>
              <Box variant="p" padding={{ bottom: 's' }} color="inherit">
                No diagnostic trouble codes have been recorded for this vehicle
                {statusFilter !== 'ALL' ? ` with status=${statusFilter}` : ''}
                {sourceFilter !== 'ALL' ? ` from source ${sourceFilter}` : ''}.
              </Box>
            </Box>
          }
        />

      {injectionModalVisible && (
        <Modal
          visible={true}
          onDismiss={() => {
            setInjectionModalVisible(false);
            setInjectionError(null);
            setSelectedInjection([]);
          }}
          header="Set fault on this vehicle"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  variant="link"
                  onClick={() => {
                    setInjectionModalVisible(false);
                    setInjectionError(null);
                    setSelectedInjection([]);
                  }}
                  disabled={injecting}
                >
                  Cancel
                </Button>
                <Button
                  variant="primary"
                  onClick={injectFaults}
                  loading={injecting}
                  disabled={injecting}
                  ariaLabel="Confirm inject"
                >
                  Confirm
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="s">
            <Box variant="p">
              Select one or more diagnostic trouble codes to inject via the vehicle's UDS bus.
              Codes appear in the DTC table within ~40 seconds. Confirming with no codes
              selected clears the current fault state.
            </Box>
            {!campaignRunning && (
              <Alert type="warning">
                No standing <code>uds-dtc-polling</code> campaign appears to be running for
                this vehicle. Assign the <em>uds-dtc-polling</em> template from the Campaigns
                tab first, otherwise Confirm will return 409.
              </Alert>
            )}
            <FormField
              label="Diagnostic trouble codes"
              description="Only codes the vehicle's ECUs can encode and answer for are listed."
            >
              <Multiselect
                selectedOptions={selectedInjection}
                onChange={({ detail }) =>
                  setSelectedInjection([...detail.selectedOptions] as any)
                }
                options={injectable.map(ev => ({
                  label: ev.description ? `${ev.description} · ${ev.dtcCode}` : ev.dtcCode,
                  value: ev.eventId,
                  description: `ECU${ev.ecu} · ${ev.dtcCode}`,
                }))}
                placeholder={
                  injectable.length
                    ? 'Select codes to inject'
                    : 'No injectable codes available'
                }
                empty="No codes available"
              />
            </FormField>
            {injectionError && <Alert type="error">{injectionError}</Alert>}
          </SpaceBetween>
        </Modal>
      )}

      {clearFaultsModalVisible && (
        <Modal
          visible={true}
          onDismiss={() => {
            setClearFaultsModalVisible(false);
            setClearFaultsError(null);
          }}
          header="Clear faults on this vehicle"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  variant="link"
                  onClick={() => {
                    setClearFaultsModalVisible(false);
                    setClearFaultsError(null);
                  }}
                  disabled={clearFaultsConfirming}
                >
                  Cancel
                </Button>
                <Button
                  variant="primary"
                  onClick={clearFaults}
                  loading={clearFaultsConfirming}
                  disabled={clearFaultsConfirming}
                  ariaLabel="Confirm clear faults"
                >
                  Confirm
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="s">
            <Box variant="p">
              The following codes will stop being reported by the vehicle's ECUs:
            </Box>
            <Box>
              {Object.entries(faultState?.ecus || {})
                .filter(([, e]) => (e.dtcs?.length ?? 0) > 0)
                .map(([ecu, e]) => (
                  <div key={ecu}>
                    <strong>{`ECU${ecu}: ${(e.dtcs || []).join(', ')}`}</strong>
                  </div>
                ))}
            </Box>
            <Alert type="info">
              The DTC rows in the table will remain <strong>ACTIVE</strong> until you mark each
              one cleared individually. Use the check-mark button on each row after the ECU
              stops reporting (~40 s).
            </Alert>
            <Box color="text-body-secondary" variant="small">
              To clear only <em>some</em> faults, use <em>Set fault</em> and deselect the
              codes you want to remove — the multiselect handles partial clearing.
            </Box>
            {clearFaultsError && <Alert type="error">{clearFaultsError}</Alert>}
          </SpaceBetween>
        </Modal>
      )}

      {scheduleModalItem && (
        <Modal
          visible={true}
          onDismiss={() => { setScheduleModalItem(null); setScheduleError(null); }}
          header="Schedule Service"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  variant="link"
                  onClick={() => { setScheduleModalItem(null); setScheduleError(null); }}
                  disabled={scheduling}
                >
                  Cancel
                </Button>
                <Button
                  variant="primary"
                  onClick={scheduleService}
                  loading={scheduling}
                  disabled={scheduling}
                  ariaLabel="Confirm"
                >
                  Confirm
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="s">
            <ColumnLayout columns={2} variant="text-grid">
              <div>
                <Box variant="awsui-key-label">System</Box>
                <div>{scheduleModalItem.system || '—'}</div>
              </div>
              <div>
                <Box variant="awsui-key-label">Severity</Box>
                <div>{scheduleModalItem.severity || '—'}</div>
              </div>
              <div>
                <Box variant="awsui-key-label">Service ID (preview)</Box>
                <div>{serviceIdPreview}</div>
              </div>
            </ColumnLayout>
            {scheduleError && (
              <Box color="text-status-error">{scheduleError}</Box>
            )}
          </SpaceBetween>
        </Modal>
      )}

      {/* SOVD per-row Clear DTC modal */}
      {clearDtcModalItem && (
        <ClearDTCModal
          visible={true}
          onDismiss={() => setClearDtcModalItem(null)}
          onSuccess={async () => {
            setClearDtcModalItem(null);
            await load();
          }}
          dtc={{
            code: clearDtcModalItem.code,
            description: clearDtcModalItem.description,
            firstSeenAt: clearDtcModalItem.firstSeenAt,
            occurrenceCount: clearDtcModalItem.occurrenceCount,
            ecu: clearDtcModalItem.ecu,
          }}
          vehicleId={vehicleId || ''}
          connectionStatus={connectionStatus}
        />
      )}
    </SpaceBetween>
  );
};

export default VehicleDTCsTable;
