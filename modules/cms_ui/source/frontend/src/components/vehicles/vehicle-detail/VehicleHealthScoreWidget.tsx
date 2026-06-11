// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useEffect, useState } from "react";
import {
  Box,
  Container,
  Header,
  Spinner,
  StatusIndicator,
} from "@cloudscape-design/components";
import { useVehicle } from "../../../contexts/VehicleContext";
import { useAuth } from "../../../auth/useAuth";
import type { HealthActiveDtc } from "../../../contexts/VehicleContext";

interface Props {
  vehicleId: string;
  /**
   * Connection state from the Vehicle Information section's `vehicleData`
   * — i.e. the field the page header's "Connection Status" badge
   * already uses. When supplied, this becomes the source of truth for
   * the widget's Connection KPI and for reconciling the score.
   *
   * Why: the VSA backend's `/vehicles/{id}/context` reads the canonical
   * Redis-backed live signal, which usually agrees with the CMS API
   * the rest of the page consumes — but they can briefly disagree
   * during a reconnect window. Operators have asked that the page
   * never show "Connected" in one widget and "Offline" in another, so
   * the widget defers to whatever the page header is showing.
   *
   * If override is set to "connected" but the VSA breakdown still
   * carries a "Vehicle disconnected" deduction, the widget removes
   * that deduction and adjusts the score back up by 5. Inverse path
   * (override "disconnected" but no VSA deduction) does NOT add a
   * deduction client-side — operators flagged that as too
   * presumptuous; we trust the server score in that direction.
   */
  overrideConnectionStatus?: string | null;
  /**
   * Best ISO timestamp the page already has for "last connected".
   * Used for the "Last seen Xm ago" sub-label so it matches the
   * Vehicle Information section's "Last Connected" value.
   */
  overrideLastConnectedAt?: string | null;
}

/**
 * VehicleHealthScoreWidget — server-computed `healthScore` (0..100), a
 * small KPI strip summarizing the inputs to the score, and the
 * deduction breakdown for a single vehicle on the Vehicle Detail
 * page.
 *
 * Source of truth is the VSA backend (api-vehicle-context Lambda).
 * Both this widget and the iOS Home tab render the same value
 * verbatim — no client-side recomputation.
 *
 * Visual layout (full-width container):
 *   ┌─ Vehicle Health ──────────────────────────────────────────────────┐
 *   │ ┌────┐  Active DTCs: 4   Highest: HIGH   Connection: ✓   Last: 2m │
 *   │ │ 95 │  ──────────────────────────────────────────────────────────│
 *   │ │FAIR│  DTC P0299 HIGH                                       −15  │
 *   │ └────┘  DTC P0562 LOW                                          −4 │
 *   └────────────────────────────────────────────────────────────────────┘
 *
 * KPI strip purpose: even when the deductions list has only one item
 * (e.g., a single "Vehicle disconnected") the right column had a lot
 * of empty space stretched across the full container width. The KPI
 * strip — Active DTCs count + highest severity + connection state +
 * last-seen — fills that horizontal real estate with information
 * directly relevant to the score.
 *
 * 404-as-hide: the widget renders `null` when the VSA backend
 * returns 404 (vehicle has no row in `cms-prod-storage-vehicles`,
 * e.g. simulator-only VINs). Other failures still render the inline
 * error UI so transient backend issues stay visible.
 */
const VehicleHealthScoreWidget: React.FC<Props> = ({
  vehicleId,
  overrideConnectionStatus,
  overrideLastConnectedAt,
}) => {
  const {
    healthScore,
    healthScoreBreakdown,
    healthActiveDtcs,
    healthConnectionStatus,
    healthLastSeenAt,
    healthVehicleId,
    healthLoading,
    healthError,
    loadVehicleHealth,
  } = useVehicle();
  const { getAuthHeaders } = useAuth();
  // Local mirror of the relevant vehicleId so we can detect when the
  // user navigates between vehicles and refire the load. Without this
  // the widget would keep showing the previous vehicle's score for a
  // beat after the URL changes.
  const [requestedVehicleId, setRequestedVehicleId] = useState<string | null>(null);

  useEffect(() => {
    if (!vehicleId) return;
    if (requestedVehicleId === vehicleId) return;
    setRequestedVehicleId(vehicleId);
    void loadVehicleHealth(vehicleId, getAuthHeaders);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicleId, requestedVehicleId, loadVehicleHealth]);

  // Guard against stale renders — the context is shared across the
  // whole page, so use the data only when it belongs to *this*
  // vehicleId.
  const ours = healthVehicleId === vehicleId;
  const breakdownRaw = ours ? healthScoreBreakdown : null;
  const activeDtcs = ours ? healthActiveDtcs : null;
  const lastSeenAtFromContext = ours ? healthLastSeenAt : null;

  // Reconcile the connection signal:
  //   1. Caller-supplied `overrideConnectionStatus` (the Vehicle
  //      Information section's value) wins. Operators have asked
  //      that the page never show two different connection states
  //      side by side.
  //   2. Otherwise fall back to whatever the VSA backend put on
  //      `vehicle.connectionStatus` (already canonical from
  //      api-vehicle-live-state when available).
  const connectionStatus =
    typeof overrideConnectionStatus === 'string' && overrideConnectionStatus.length > 0
      ? overrideConnectionStatus
      : (ours ? healthConnectionStatus : null);
  const lastSeenAt =
    typeof overrideLastConnectedAt === 'string' && overrideLastConnectedAt.length > 0
      ? overrideLastConnectedAt
      : lastSeenAtFromContext;

  // If the override says "connected" but the server-computed
  // breakdown still carries a "Vehicle disconnected" deduction, the
  // server's view is stale relative to the page's view. Drop that
  // single deduction client-side and add 5 back to the headline
  // score so the operator sees one consistent story.
  //
  // We do NOT do the inverse (add a deduction when override is
  // "disconnected" but the breakdown has none) — that's too
  // presumptuous, and the typical case there is the server briefly
  // ahead of the page.
  const overrideIsConnected = (overrideConnectionStatus || '').toLowerCase() === 'connected';
  let breakdown = breakdownRaw;
  let score = ours ? healthScore : null;
  if (overrideIsConnected && breakdownRaw && score != null) {
    const filtered = (breakdownRaw.deductions || []).filter(d => d.reason !== 'Vehicle disconnected');
    if (filtered.length !== (breakdownRaw.deductions || []).length) {
      const removedAmount = (breakdownRaw.deductions || [])
        .filter(d => d.reason === 'Vehicle disconnected')
        .reduce((sum, d) => sum + (d.amount || 0), 0);
      breakdown = {
        ...breakdownRaw,
        deductions: filtered,
      };
      score = Math.max(0, Math.min(100, score + removedAmount));
    }
  }

  // 404 from the VSA backend means this vehicleId has no row in
  // cms-prod-storage-vehicles — i.e. it's a simulator-only VIN
  // (FleetWise edge agents) or a fleet-enrollment record without
  // canonical vehicle metadata. Render `null` so the parent grid
  // collapses cleanly.
  if (healthError === 'HTTP 404' && healthVehicleId === vehicleId) {
    return null;
  }

  // Pre-load: hold the same shell, render an inline spinner where the
  // score would go so the page grid doesn't reflow.
  if (healthLoading && score == null) {
    return (
      <Container header={<Header variant="h2">Vehicle Health</Header>}>
        <div style={ROW_STYLE}>
          <div style={SCORE_COL_STYLE}><Spinner size="big" /></div>
          <div style={DIVIDER_STYLE} />
          <div style={BREAKDOWN_COL_STYLE}>
            <Box color="text-body-secondary">Loading score…</Box>
          </div>
        </div>
      </Container>
    );
  }

  // Error / endpoint missing — same shell, inline status.
  if (score == null) {
    return (
      <Container header={<Header variant="h2">Vehicle Health</Header>}>
        <div style={ROW_STYLE}>
          <div style={SCORE_COL_STYLE}>
            <Box variant="h1" color="text-body-secondary">—</Box>
          </div>
          <div style={DIVIDER_STYLE} />
          <div style={BREAKDOWN_COL_STYLE}>
            <StatusIndicator type="info">
              {healthError ? `Score unavailable (${healthError})` : 'Score unavailable'}
            </StatusIndicator>
          </div>
        </div>
      </Container>
    );
  }

  const { color, label } = scoreVisuals(score);

  return (
    <Container header={<Header variant="h2">Vehicle Health</Header>}>
      <div style={ROW_STYLE}>
        {/* Left: score + label */}
        <div style={SCORE_COL_STYLE}>
          <div
            style={{
              fontSize: 48,
              fontWeight: 700,
              lineHeight: 1,
              color,
            }}
          >
            {score}
          </div>
          <div
            style={{
              marginTop: 4,
              fontSize: 12,
              fontWeight: 600,
              letterSpacing: 0.3,
              textTransform: 'uppercase',
              color,
            }}
          >
            {label}
          </div>
        </div>

        <div style={DIVIDER_STYLE} />

        {/* Right: KPI strip + deductions */}
        <div style={BREAKDOWN_COL_STYLE}>
          <KpiStrip
            activeDtcs={activeDtcs ?? []}
            connectionStatus={connectionStatus}
            lastSeenAt={lastSeenAt}
            computedAt={breakdown?.computedAt ?? null}
          />
          {breakdown && breakdown.deductions && breakdown.deductions.length > 0 ? (
            <>
              <div style={KPI_DIVIDER_STYLE} />
              <DeductionList items={breakdown.deductions} />
            </>
          ) : (
            <Box color="text-body-secondary" margin={{ top: 'xs' }}>
              No deductions — vehicle is in top condition.
            </Box>
          )}
        </div>
      </div>
    </Container>
  );
};

// ── KPI strip ──────────────────────────────────────────────────────
//
// Four fixed-width-ish tiles stretched evenly across the right
// column. Empty / unknown values render as "—" so the strip layout
// stays stable across vehicles.

const KpiStrip: React.FC<{
  activeDtcs: HealthActiveDtc[];
  connectionStatus: string | null;
  lastSeenAt: string | null;
  computedAt: string | null;
}> = ({ activeDtcs, connectionStatus, lastSeenAt, computedAt }) => {
  const dtcCount = activeDtcs.length;
  const sevCounts = activeDtcs.reduce<Record<string, number>>((acc, d) => {
    const s = (d.severity || 'UNKNOWN').toUpperCase();
    acc[s] = (acc[s] || 0) + 1;
    return acc;
  }, {});
  // Highest severity, ranked.
  const RANK = ['CRITICAL', 'HIGH', 'MEDIUM', 'MODERATE', 'LOW'];
  const highest = RANK.find(s => sevCounts[s] > 0) || (dtcCount === 0 ? 'NONE' : 'UNKNOWN');
  const sevDetail = dtcCount > 0
    ? Object.entries(sevCounts).map(([k, n]) => `${n} ${shortSev(k)}`).join(', ')
    : 'all clear';

  const isConnected = (connectionStatus || '').toLowerCase() === 'connected';
  const lastSeenAgo = formatAgo(lastSeenAt);
  const computedAgo = formatAgo(computedAt);

  return (
    <div style={KPI_GRID_STYLE}>
      <KpiTile
        label="Active DTCs"
        value={String(dtcCount)}
        sub={sevDetail}
        valueColor={dtcCount > 0 ? '#414d5c' : '#037f0c'}
      />
      <KpiTile
        label="Highest severity"
        value={highest === 'NONE' ? '—' : highest}
        sub={highest === 'NONE' ? 'no active faults' : ''}
        valueColor={severityColor(highest)}
      />
      <KpiTile
        label="Connection"
        value={isConnected ? '✓ Connected' : '✗ Offline'}
        sub={lastSeenAgo ? `Last seen ${lastSeenAgo}` : ''}
        valueColor={isConnected ? '#037f0c' : '#d91515'}
      />
      <KpiTile
        label="Score updated"
        value={computedAgo || '—'}
        sub=""
        valueColor="#414d5c"
      />
    </div>
  );
};

const KpiTile: React.FC<{
  label: string;
  value: string;
  sub: string;
  valueColor: string;
}> = ({ label, value, sub, valueColor }) => (
  <div style={{ minWidth: 0 }}>
    <div
      style={{
        fontSize: 11,
        fontWeight: 600,
        letterSpacing: 0.3,
        textTransform: 'uppercase',
        color: '#5f6b7a',
      }}
    >
      {label}
    </div>
    <div
      style={{
        fontSize: 16,
        fontWeight: 600,
        color: valueColor,
        lineHeight: 1.2,
        marginTop: 2,
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
      }}
      title={value}
    >
      {value}
    </div>
    {sub ? (
      <div
        style={{
          fontSize: 11,
          color: '#5f6b7a',
          marginTop: 2,
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}
        title={sub}
      >
        {sub}
      </div>
    ) : null}
  </div>
);

// ── Deductions list (unchanged from prior pass) ────────────────────

const DeductionList: React.FC<{ items: { reason: string; amount: number }[] }> = ({ items }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
    {items.map((d) => (
      <div
        key={d.reason}
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'baseline',
          gap: 12,
          fontSize: 13,
        }}
      >
        <span style={{ color: '#414d5c' }}>{d.reason}</span>
        <span style={{ color: '#d91515', fontWeight: 600, whiteSpace: 'nowrap' }}>
          −{d.amount}
        </span>
      </div>
    ))}
  </div>
);

// ── Layout style constants ─────────────────────────────────────────

const ROW_STYLE: React.CSSProperties = {
  display: 'flex',
  alignItems: 'flex-start',
  gap: 24,
  flexWrap: 'wrap',
};

const SCORE_COL_STYLE: React.CSSProperties = {
  flex: '0 0 auto',
  minWidth: 96,
  textAlign: 'center',
  paddingTop: 4,
};

const DIVIDER_STYLE: React.CSSProperties = {
  width: 1,
  alignSelf: 'stretch',
  minHeight: 96,
  backgroundColor: '#e9ebed',
};

const BREAKDOWN_COL_STYLE: React.CSSProperties = {
  flex: '1 1 320px',
  minWidth: 0,
};

const KPI_GRID_STYLE: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
  gap: 16,
};

const KPI_DIVIDER_STYLE: React.CSSProperties = {
  height: 1,
  backgroundColor: '#e9ebed',
  margin: '12px 0',
};

// ── Helpers ────────────────────────────────────────────────────────

function scoreVisuals(score: number): { color: string; label: string } {
  // Cloudscape-aligned hex values: status-success / status-warning /
  // status-error.
  if (score >= 90) return { color: '#037f0c', label: 'Excellent' };
  if (score >= 80) return { color: '#037f0c', label: 'Good' };
  if (score >= 60) return { color: '#8d6605', label: 'Fair' };
  return { color: '#d91515', label: 'Poor' };
}

function severityColor(sev: string): string {
  switch (sev.toUpperCase()) {
    case 'CRITICAL': return '#d91515';
    case 'HIGH':     return '#d91515';
    case 'MEDIUM':
    case 'MODERATE': return '#8d6605';
    case 'LOW':      return '#414d5c';
    case 'NONE':     return '#037f0c';
    default:         return '#414d5c';
  }
}

function shortSev(s: string): string {
  // Compact letter for the sub-label: CRITICAL→C, HIGH→H, MEDIUM→M, LOW→L
  const u = s.toUpperCase();
  if (u === 'CRITICAL') return 'C';
  if (u === 'HIGH')     return 'H';
  if (u === 'MEDIUM' || u === 'MODERATE') return 'M';
  if (u === 'LOW')      return 'L';
  return s;
}

/**
 * "3m ago", "12h ago", "2d ago", or null if the input couldn't be
 * parsed. Tolerates both ISO-with-Z and ISO-with-offset; ignores
 * fractional seconds.
 */
function formatAgo(ts: string | null): string | null {
  if (!ts) return null;
  let d: Date;
  try {
    d = new Date(ts);
    if (isNaN(d.getTime())) return null;
  } catch {
    return null;
  }
  const diffSec = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
  if (diffSec < 60)   return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.round(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.round(diffSec / 3600)}h ago`;
  return `${Math.round(diffSec / 86400)}d ago`;
}

export default VehicleHealthScoreWidget;
