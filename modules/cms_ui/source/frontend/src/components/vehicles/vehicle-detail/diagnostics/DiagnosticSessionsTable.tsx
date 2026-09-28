// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// DiagnosticSessionsTable — presentational table over per-session summaries.
//
// Spec: `.kiro/specs/2026-09-24-cms-diagnostics-tab-ia-redesign` Group 3, Task 3.2.
//
// ── Design ─────────────────────────────────────────────────────────────────────
//
// Renders a Cloudscape <Table> whose rows come from groupSovdRowsIntoSessions()
// (Task 2.2).  The parent owns the data and the view-state; this component owns
// nothing beyond the scope toggle (recent | all).
//
// Columns:
//   started         — ISO → locale string (or "—" when absent).
//   status          — Active / Complete / RateLimited, styled as StatusIndicator.
//   routines        — numeric count.
//   worst verdict   — out_of_spec / marginal / in_spec / none styled with badge.
//   service order   — when dispatchedRoId is present, a link to DMS's fleet
//                     repair-order page on the DMS origin (utils/dmsLinks.ts), opening
//                     in a new tab; the plain id when no DMS origin is configured;
//                     "—" otherwise.
//   action          — "View" button that calls onSessionSelect; not rendered for
//                     dispatched (terminal) sessions — those are read-only markers.
//
// Scope:
//   "recent" (default) — sessions started within the last RECENT_WINDOW_MS.
//   "all"              — all sessions, no time filter.
//
// Dispatched (terminal) sessions:
//   A row whose dispatchedRoId is set is terminal.  It renders a "Dispatched" badge
//   alongside the status, and its row action is suppressed (spec Q6).
//
// This component does NOT own view state (Group 4 does).  Selecting a row calls
// onSessionSelect — the parent decides what happens next.

import React, { useState, useMemo } from 'react';
import {
  Table,
  Box,
  Header,
  Badge,
  Link,
  Select,
  SpaceBetween,
  StatusIndicator,
  Button,
} from '@cloudscape-design/components';
import type { SelectProps } from '@cloudscape-design/components';
import type { DiagnosticSession, SessionStatus, RoutineVerdict } from '@/utils/diagnosticSessions';
import { dmsFleetRepairOrderUrl } from '@/utils/dmsLinks';

// ── Types ───────────────────────────────────────────────────────────────────

export interface DiagnosticSessionsTableProps {
  /** Pre-computed sessions from groupSovdRowsIntoSessions(). */
  sessions: DiagnosticSession[];
  /** Called when the operator clicks "View" on a non-terminal session. */
  onSessionSelect: (session: DiagnosticSession) => void;
  /**
   * Optional: if your tests need to control "now" for the recent window
   * calculation, pass it in.  Defaults to Date.now() when omitted.
   */
  now?: number;
}

// ── Constants ───────────────────────────────────────────────────────────────

/** Sessions started within this window are shown in the "recent" scope. */
const RECENT_WINDOW_MS = 7 * 24 * 60 * 60 * 1000; // 7 days

// ── Scope select options ─────────────────────────────────────────────────────

const SCOPE_OPTIONS: SelectProps.Options = [
  { value: 'recent', label: 'Recent' },
  { value: 'all',    label: 'All' },
];

// ── Status rendering ─────────────────────────────────────────────────────────

const STATUS_TYPE: Record<SessionStatus, 'in-progress' | 'success' | 'warning'> = {
  Active:      'in-progress',
  Complete:    'success',
  RateLimited: 'warning',
};

const STATUS_LABEL: Record<SessionStatus, string> = {
  Active:      'Active',
  Complete:    'Complete',
  RateLimited: 'Rate limited',
};

// ── Verdict rendering ────────────────────────────────────────────────────────

type BadgeColor = 'red' | 'blue' | 'green' | 'grey';

const VERDICT_BADGE_COLOR: Record<RoutineVerdict, BadgeColor> = {
  out_of_spec: 'red',
  marginal:    'blue',
  in_spec:     'green',
  none:        'grey',
};

const VERDICT_LABEL: Record<RoutineVerdict, string> = {
  out_of_spec: 'Out of spec',
  marginal:    'Marginal',
  in_spec:     'In spec',
  none:        '—',
};

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Format an ISO timestamp to a locale string, or "—" when absent. */
function formatStarted(iso: string | undefined): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (!isNaN(d.getTime())) return d.toLocaleString();
  } catch { /* fall through */ }
  return iso;
}

/**
 * Returns true when the session falls inside the "recent" window.
 * A session with no startedAt is always shown in both scopes so it
 * does not silently disappear.
 */
function isRecent(session: DiagnosticSession, now: number): boolean {
  if (!session.startedAt) return true;
  try {
    const d = new Date(session.startedAt);
    if (!isNaN(d.getTime())) return now - d.getTime() <= RECENT_WINDOW_MS;
  } catch { /* fall through */ }
  return true;
}

// ── Component ────────────────────────────────────────────────────────────────

const DiagnosticSessionsTable: React.FC<DiagnosticSessionsTableProps> = ({
  sessions,
  onSessionSelect,
  now: nowProp,
}) => {
  const now = nowProp ?? Date.now();

  const [scope, setScope] = useState<'recent' | 'all'>('recent');

  const visibleSessions = useMemo(() => {
    if (scope === 'all') return sessions;
    return sessions.filter(s => isRecent(s, now));
  }, [sessions, scope, now]);

  const selectedScopeOption = SCOPE_OPTIONS.find(o => o.value === scope) ?? SCOPE_OPTIONS[0];

  return (
    <Table
      data-testid="diagnostic-sessions-table"
      variant="embedded"
      columnDefinitions={[
        {
          id: 'started',
          header: 'Started',
          cell: (item: DiagnosticSession) => formatStarted(item.startedAt),
        },
        {
          id: 'status',
          header: 'Status',
          cell: (item: DiagnosticSession) => {
            const indicator = (
              <StatusIndicator type={STATUS_TYPE[item.status]}>
                {STATUS_LABEL[item.status]}
              </StatusIndicator>
            );
            if (item.dispatchedRoId) {
              return (
                <SpaceBetween size="xs" direction="horizontal">
                  {indicator}
                  <Badge color="blue">Dispatched</Badge>
                </SpaceBetween>
              );
            }
            return indicator;
          },
        },
        {
          id: 'routines',
          header: 'Routines',
          cell: (item: DiagnosticSession) => item.routineCount,
        },
        {
          id: 'worstVerdict',
          header: 'Worst verdict',
          cell: (item: DiagnosticSession) => {
            if (item.worstVerdict === 'none') return '—';
            return (
              <Badge color={VERDICT_BADGE_COLOR[item.worstVerdict]}>
                {VERDICT_LABEL[item.worstVerdict]}
              </Badge>
            );
          },
        },
        {
          id: 'serviceOrder',
          header: 'Service order',
          cell: (item: DiagnosticSession) => {
            // GUARD: render nothing but "—" when dispatchedRoId is absent.
            // The mutation test in Task 3.2 Verify step 2 verifies this by
            // rendering the link unconditionally and confirming the
            // absent-dispatchedRoId test fails (guards a link to an undefined id).
            if (!item.dispatchedRoId) return '—';
            // The repair order lives in DMS, a separate app. Link to its fleet
            // repair-order page on the DMS origin; with no origin configured, show
            // the id without a link. Never a relative path: that resolves against
            // CMS and opened CMS's 404 page (issues/2026-09-25-diagnostics-view-ro-link-404/).
            const href = dmsFleetRepairOrderUrl(item.dispatchedRoId);
            if (!href) {
              return (
                <span data-testid={`service-order-id-${item.sessionId}`}>{item.dispatchedRoId}</span>
              );
            }
            return (
              <Link
                href={href}
                external
                externalIconAriaLabel="(opens DMS in a new tab)"
                data-testid={`service-order-link-${item.sessionId}`}
              >
                View RO
              </Link>
            );
          },
        },
        {
          id: 'action',
          header: '',
          cell: (item: DiagnosticSession) => {
            // Dispatched sessions are terminal (spec Q6) — suppress the View action.
            if (item.dispatchedRoId) return null;
            return (
              <Button
                variant="inline-link"
                data-testid={`session-view-button-${item.sessionId}`}
                onClick={() => onSessionSelect(item)}
              >
                View
              </Button>
            );
          },
        },
      ]}
      items={visibleSessions}
      empty={
        <Box textAlign="center" color="inherit">
          <b>No sessions</b>
          <Box padding={{ bottom: 's' }} variant="p" color="inherit">
            {scope === 'recent'
              ? 'No diagnostic sessions in the last 7 days. Switch to "All" to see older sessions.'
              : 'No diagnostic sessions recorded for this vehicle.'}
          </Box>
        </Box>
      }
      header={
        <Header
          counter={`(${visibleSessions.length})`}
          actions={
            <Select
              data-testid="sessions-scope-select"
              selectedOption={selectedScopeOption}
              onChange={({ detail }) =>
                setScope((detail.selectedOption.value ?? 'recent') as 'recent' | 'all')
              }
              options={SCOPE_OPTIONS}
              ariaLabel="Session scope"
            />
          }
        >
          Diagnostic sessions
        </Header>
      }
    />
  );
};

export default DiagnosticSessionsTable;
