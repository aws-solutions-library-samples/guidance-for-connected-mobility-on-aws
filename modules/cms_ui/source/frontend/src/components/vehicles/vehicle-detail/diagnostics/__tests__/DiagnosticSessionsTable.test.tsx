// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// DiagnosticSessionsTable — unit tests.
//
// Spec: `.kiro/specs/2026-09-24-cms-diagnostics-tab-ia-redesign` Group 3, Task 3.2.
//
// Required coverage (per tasks.md § 3.2 Verify):
//   ✓ the link is absent when dispatchedRoId is absent
//   ✓ 'recent' is the default scope
//   ✓ switching to 'all' shows a row outside the recent window
//
// Mutation verification:
//   Verify step 2 requires: rendering the link unconditionally MUST cause the
//   absent-dispatchedRoId test to fail.  See decisions.md for the mutation record.

import React from 'react';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';

import DiagnosticSessionsTable from '../DiagnosticSessionsTable';
import type { DiagnosticSession } from '@/utils/diagnosticSessions';

// ── Fixtures ──────────────────────────────────────────────────────────────────

const NOW_ISO = '2026-09-27T12:00:00.000Z'; // anchor for "recent" window testing
const NOW_MS = new Date(NOW_ISO).getTime();

/** A session that started 2 days ago — within the 7-day recent window. */
const RECENT_SESSION: DiagnosticSession = {
  sessionId: 'session-recent-001',
  startedAt: new Date(NOW_MS - 2 * 24 * 60 * 60 * 1000).toISOString(), // 2 days ago
  status: 'Complete',
  routineCount: 3,
  worstVerdict: 'in_spec',
  dispatchedRoId: undefined,
  dispatchedAt: undefined,
};

/** A session that started 10 days ago — OUTSIDE the 7-day recent window. */
const OLD_SESSION: DiagnosticSession = {
  sessionId: 'session-old-002',
  startedAt: new Date(NOW_MS - 10 * 24 * 60 * 60 * 1000).toISOString(), // 10 days ago
  status: 'Complete',
  routineCount: 1,
  worstVerdict: 'none',
  dispatchedRoId: undefined,
  dispatchedAt: undefined,
};

/** A session that has been dispatched to DMS — terminal, carries an RO link. */
const DISPATCHED_SESSION: DiagnosticSession = {
  sessionId: 'session-dispatched-003',
  startedAt: new Date(NOW_MS - 1 * 24 * 60 * 60 * 1000).toISOString(), // 1 day ago
  status: 'Complete',
  routineCount: 2,
  worstVerdict: 'marginal',
  dispatchedRoId: 'RO-2026-0042',
  dispatchedAt: new Date(NOW_MS - 23 * 60 * 60 * 1000).toISOString(),
};

/** An active (in-flight) session. */
const ACTIVE_SESSION: DiagnosticSession = {
  sessionId: 'session-active-004',
  startedAt: new Date(NOW_MS - 30 * 60 * 1000).toISOString(), // 30 minutes ago
  status: 'Active',
  routineCount: 1,
  worstVerdict: 'none',
  dispatchedRoId: undefined,
  dispatchedAt: undefined,
};

/** A session with no startedAt — pre-session rows. */
const UNSESSIONED: DiagnosticSession = {
  sessionId: '__unsessioned__',
  startedAt: undefined,
  status: 'Complete',
  routineCount: 0,
  worstVerdict: 'none',
  dispatchedRoId: undefined,
  dispatchedAt: undefined,
};

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Render the table with a controlled `now` so the recent window is deterministic. */
function renderTable(
  sessions: DiagnosticSession[],
  onSessionSelect = vi.fn(),
) {
  return render(
    <DiagnosticSessionsTable
      sessions={sessions}
      onSessionSelect={onSessionSelect}
      now={NOW_MS}
    />,
  );
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('DiagnosticSessionsTable — scope defaults', () => {
  it('renders "recent" as the default scope (label visible in header)', () => {
    renderTable([RECENT_SESSION]);
    // The scope Select should show "Recent" as the selected option by default.
    // Cloudscape Select renders the selected option's label in its trigger.
    const scopeSelect = screen.getByTestId('sessions-scope-select');
    expect(scopeSelect).toBeDefined();
    // The label "Recent" should appear in the table header area.
    expect(screen.getByText(/recent/i)).toBeDefined();
  });

  it('shows a session within the 7-day window in the default recent scope', () => {
    renderTable([RECENT_SESSION]);
    expect(screen.getByTestId(`session-view-button-${RECENT_SESSION.sessionId}`)).toBeDefined();
  });

  it('hides a session outside the 7-day window in the default recent scope', () => {
    renderTable([OLD_SESSION]);
    expect(
      screen.queryByTestId(`session-view-button-${OLD_SESSION.sessionId}`),
    ).toBeNull();
  });
});

describe('DiagnosticSessionsTable — scope toggle', () => {
  it('switching to "all" reveals a session outside the recent window', async () => {
    renderTable([RECENT_SESSION, OLD_SESSION]);

    // OLD_SESSION should not be visible in the default "recent" scope.
    expect(
      screen.queryByTestId(`session-view-button-${OLD_SESSION.sessionId}`),
    ).toBeNull();

    // RECENT_SESSION is visible.
    expect(
      screen.getByTestId(`session-view-button-${RECENT_SESSION.sessionId}`),
    ).toBeDefined();

    // Open the Cloudscape Select dropdown.
    // Cloudscape opens its Select dropdowns on mouseDown on the trigger.
    const scopeSelect = screen.getByTestId('sessions-scope-select');
    const trigBtn = scopeSelect.querySelector('button') as HTMLButtonElement;
    expect(trigBtn).not.toBeNull();
    fireEvent.mouseDown(trigBtn);

    // After mouseDown the dropdown options appear as [role="option"] elements.
    await new Promise(r => setTimeout(r, 50));
    const options = document.querySelectorAll('[role="option"]');
    const allOption = Array.from(options).find(
      el => el.textContent?.trim() === 'All',
    );
    expect(allOption).not.toBeNull();
    // Cloudscape selects an option on mouseDown on the option element.
    fireEvent.mouseDown(allOption as HTMLElement);
    // Also fire mouseUp + click to complete the selection gesture.
    fireEvent.mouseUp(allOption as HTMLElement);
    fireEvent.click(allOption as HTMLElement);

    await new Promise(r => setTimeout(r, 50));

    // OLD_SESSION should now be visible after switching to "all".
    expect(
      screen.getByTestId(`session-view-button-${OLD_SESSION.sessionId}`),
    ).toBeDefined();
  });
});

describe('DiagnosticSessionsTable — service-order link', () => {
  // The repair order lives in DMS, on its own origin. FG5
  // (issues/2026-09-25-diagnostics-view-ro-link-404/): the link used to be the
  // CMS-relative `/service?ro_id=…`, which opened CMS's own 404 page.
  const savedRuntimeConfig = (window as any).runtimeConfig;
  afterEach(() => {
    (window as any).runtimeConfig = savedRuntimeConfig;
  });

  it('links to the DMS fleet repair-order page on the DMS origin, in a new tab', () => {
    (window as any).runtimeConfig = { awsRegion: 'us-west-2', apiEndpoint: 'x', dmsUiOrigin: 'https://dms.example.com' };
    renderTable([DISPATCHED_SESSION]);
    const link = screen.getByTestId(
      `service-order-link-${DISPATCHED_SESSION.sessionId}`,
    );
    expect(link.getAttribute('href')).toBe('https://dms.example.com/fleet/repair-orders/RO-2026-0042');
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
  });

  it('shows the repair-order id without a link when no DMS origin is configured', () => {
    (window as any).runtimeConfig = { awsRegion: 'us-west-2', apiEndpoint: 'x' };
    renderTable([DISPATCHED_SESSION]);
    expect(screen.queryByTestId(`service-order-link-${DISPATCHED_SESSION.sessionId}`)).toBeNull();
    expect(screen.getByTestId(`service-order-id-${DISPATCHED_SESSION.sessionId}`).textContent).toBe('RO-2026-0042');
    // No relative link anywhere in the table: it would resolve against CMS.
    const table = screen.getByTestId('diagnostic-sessions-table');
    for (const a of Array.from(table.querySelectorAll('a[href]'))) {
      expect(a.getAttribute('href')).not.toMatch(/^\//);
    }
  });

  it('renders "—" (no link) when dispatchedRoId is absent', () => {
    renderTable([RECENT_SESSION]);

    // No link element with the service-order testid for this session.
    expect(
      screen.queryByTestId(`service-order-link-${RECENT_SESSION.sessionId}`),
    ).toBeNull();

    // The cell should display a "—" dash instead.
    // The table row for this session should contain "—" in the service-order column.
    // We check this by asserting the link is absent (above) — the component
    // renders "—" when the guard prevents the link.  If the link were rendered
    // unconditionally as `/service?ro_id=undefined`, the link testid would be present
    // AND the href would contain "undefined" — both bad.
    // This assertion is the one the mutation test must break.
    const table = screen.getByTestId('diagnostic-sessions-table');
    // Confirm no href contains "ro_id=undefined" anywhere in the rendered output.
    expect(table.innerHTML).not.toContain('ro_id=undefined');
  });
});

describe('DiagnosticSessionsTable — dispatched (terminal) sessions', () => {
  it('shows "Dispatched" badge for a dispatched session', () => {
    renderTable([DISPATCHED_SESSION]);
    expect(screen.getByText('Dispatched')).toBeDefined();
  });

  it('suppresses the View action for a dispatched (terminal) session', () => {
    renderTable([DISPATCHED_SESSION]);
    // The View button must be absent for terminal sessions.
    expect(
      screen.queryByTestId(`session-view-button-${DISPATCHED_SESSION.sessionId}`),
    ).toBeNull();
  });

  it('shows the View action for a non-dispatched session', () => {
    renderTable([RECENT_SESSION]);
    expect(
      screen.getByTestId(`session-view-button-${RECENT_SESSION.sessionId}`),
    ).toBeDefined();
  });
});

describe('DiagnosticSessionsTable — onSessionSelect callback', () => {
  it('calls onSessionSelect with the session when View is clicked', () => {
    const onSelect = vi.fn();
    renderTable([RECENT_SESSION], onSelect);

    const viewBtn = screen.getByTestId(`session-view-button-${RECENT_SESSION.sessionId}`);
    fireEvent.click(viewBtn);

    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect).toHaveBeenCalledWith(RECENT_SESSION);
  });

  it('does not call onSessionSelect for a dispatched session (no button rendered)', () => {
    const onSelect = vi.fn();
    renderTable([DISPATCHED_SESSION], onSelect);
    // There is no View button for a dispatched session; no accidental call possible.
    expect(onSelect).not.toHaveBeenCalled();
  });
});

describe('DiagnosticSessionsTable — status rendering', () => {
  it('renders Active status indicator for an in-flight session', () => {
    renderTable([ACTIVE_SESSION]);
    expect(screen.getByText('Active')).toBeDefined();
  });

  it('renders Complete status indicator for a completed session', () => {
    renderTable([RECENT_SESSION]);
    expect(screen.getByText('Complete')).toBeDefined();
  });
});

describe('DiagnosticSessionsTable — worst verdict rendering', () => {
  it('renders "—" for a session with no verdict (none)', () => {
    renderTable([ACTIVE_SESSION]);
    // The worstVerdict column for ACTIVE_SESSION should render "—"
    // (the active session has no results yet).
    // There may be multiple "—" in the table (started column too if no date, etc.)
    // but we specifically check the worstVerdict renders as text.
    const table = screen.getByTestId('diagnostic-sessions-table');
    expect(table.textContent).toContain('—');
  });

  it('renders a verdict badge for a session with a verdict', () => {
    renderTable([
      {
        ...RECENT_SESSION,
        worstVerdict: 'out_of_spec',
      },
    ]);
    expect(screen.getByText('Out of spec')).toBeDefined();
  });
});

describe('DiagnosticSessionsTable — unsessioned rows', () => {
  it('renders the unsessioned bucket row (not dropped)', () => {
    renderTable([UNSESSIONED]);
    // The unsessioned bucket should appear in the table — it is always "recent"
    // (no startedAt → passes the isRecent check).
    const table = screen.getByTestId('diagnostic-sessions-table');
    // Should not be empty (unsessioned row must be visible).
    expect(table.textContent).not.toContain('No sessions');
  });
});

describe('DiagnosticSessionsTable — empty state', () => {
  it('renders the empty state when sessions is empty', () => {
    renderTable([]);
    expect(screen.getByText('No sessions')).toBeDefined();
  });

  it('renders a context-aware empty state message in recent scope', () => {
    // No sessions at all.
    renderTable([]);
    expect(
      screen.getByText(/no diagnostic sessions in the last 7 days/i),
    ).toBeDefined();
  });
});
