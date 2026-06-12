/**
 * VehicleDTCsTable — first-class Active Diagnostic Trouble Codes panel.
 *
 * Renders rows from cms-<stage>-storage-dtc-history for a single vehicle,
 * sourced from the Fleet API `/api/v1/vehicles/{vehicleId}/dtcs` route.
 *
 * Rows may come from three different upstream producers:
 *   - source=flink-maintenance-processor → threshold-based Flink detection
 *   - source=fwe-uds-dtc                 → authentic FWE UDS 0x19 response
 *   - source=force_event.py              → operator-forced demo event
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
} from '@cloudscape-design/components';
import { getApiEndpoint } from '../../../config/api';
import { useAuth } from '../../../auth/useAuth';

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
  persistent?: boolean;
  serviceRequired?: boolean;
  clearedDate?: string;
}

interface Props {
  vehicleId?: string;
  /** Optional callback fired when the row count changes — lets the
   *  parent show a count on the tab label. */
  onCountChange?: (count: number) => void;
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

const VehicleDTCsTable: React.FC<Props> = ({ vehicleId, onCountChange }) => {
  const { getAuthHeaders } = useAuth();
  const apiEndpoint = getApiEndpoint();

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

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicleId, statusFilter]);

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
                ...availableSources.map(src => ({
                  value: src === '' ? '_MISSING' : src,
                  label: src === '' ? 'Legacy' : sourceLabel(src).label,
                })),
              ]}
            />
            <Button iconName="refresh" onClick={load} ariaLabel="Refresh DTCs" />
            <Pagination
              currentPageIndex={currentPage}
              pagesCount={pagesCount}
              onChange={({ detail }) => setCurrentPage(detail.currentPageIndex)}
            />
          </div>
        </div>

        {error && (
          <Box color="text-status-error" variant="p">
            {error}
          </Box>
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
              header: 'Status',
              // Use a Badge (same metrics as the Severity column) so the
              // row height is driven by one element type, not two. The
              // previous StatusIndicator rendered an icon + text with
              // larger vertical padding than our Badges, making every
              // row slightly taller than necessary.
              cell: (item: DTCRecord) => {
                const s = item.status || 'UNKNOWN';
                const color: 'red' | 'grey' | 'green' | 'blue' =
                  s === 'ACTIVE' ? 'red'
                  : s === 'CLEARED' ? 'green'
                  : s === 'PENDING' ? 'blue'
                  : 'grey';
                return <Badge color={color}>{s}</Badge>;
              },
              width: 80,
            },
            {
              id: 'system',
              header: 'System',
              cell: (item: DTCRecord) => item.system || '—',
              width: 130,
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
              id: 'description',
              header: 'Description',
              cell: (item: DTCRecord) =>
                item.description || item.maintenanceAlertType || item.triggerEventId || '—',
              width: 250,
              maxWidth: 300,
            },
            {
              id: 'timestamp',
              header: 'First seen',
              cell: formatTimestamp,
              width: 180,
            },
            {
              id: 'actions',
              header: '',
              // Row-height note (2026-05-04, round 2): the full-size
              // Cloudscape <Button variant="normal"> was forcing every
              // ACTIVE row to ~40px tall just because of the button's
              // built-in padding (severity/status/source badges are
              // much shorter). Switch to an icon-only button with an
              // accessible aria-label; behavior is identical, row is
              // ~18px shorter. The "Mark Cleared" text was redundant
              // with the column header anyway.
              cell: (item: DTCRecord) => {
                if (item.status === 'CLEARED') {
                  return (
                    <Box color="text-status-success" fontSize="body-s">
                      ✓
                    </Box>
                  );
                }
                const inFlight = clearingDtcIds.has(item.dtcId || '');
                return (
                  <Button
                    variant="inline-icon"
                    iconName={inFlight ? 'status-in-progress' : 'check'}
                    onClick={() => clearDtc(item)}
                    loading={inFlight}
                    disabled={inFlight}
                    ariaLabel={inFlight ? 'Clearing DTC' : 'Mark DTC as cleared'}
                  />
                );
              },
              width: 60,
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
    </SpaceBetween>
  );
};

export default VehicleDTCsTable;
