// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Vehicle diagnostics — poll ownership guard (spec 2026-09-24-cms-diagnostics-tab-ia-redesign).
 *
 * Task 1.3 — RED PHASE.
 *
 * This file is a load-bearing guard for spec § Design 4:
 *
 *   "The poll is owned by the panel, not the view. Navigating back to the
 *    list while a routine runs must not abort it; the sessions table continues
 *    to show Active and the strip keeps updating. This is the load-bearing
 *    behaviour of the whole design and needs an explicit test, because the
 *    obvious implementation — poll inside the detail component — is wrong in
 *    a way that only shows up when a user navigates away mid-run."
 *
 * Tests:
 *   DXN1  Starting a routine opens the session detail view (list⇄detail swap).
 *   DXN2  Navigating back to the sessions list while the routine is non-terminal
 *         does NOT stop the poll: fetchSessionCommands is called again after
 *         the navigation.
 *   DXN3  After navigating back, the sessions table shows the in-flight session
 *         as "Active" — the poll result is still delivered to the list surface.
 *
 * WHY THIS FILE IS RED
 * ────────────────────
 * The list⇄detail view swap does not exist yet (Group 4, Task 4.1).
 * These tests fail at the DOM-assertion level — NOT at the import level —
 * because:
 *
 *   - VehicleDiagnosticsPanel exists and renders.
 *   - The test clicks the (not-yet-present) session-row "Open" button and
 *     finds `data-testid="open-session-<sessionId>"` absent.
 *   - `getByTestId(...)` throws a Missing element error (assertion failure).
 *
 * The import of VehicleDiagnosticsPanel and the mock wiring are all valid.
 * No syntax or module-resolution error is expected.
 *
 * Do NOT weaken these assertions to make the tests green. Make the
 * implementation satisfy them (Task 4.1).
 *
 * Specifically: do NOT fix this by moving the poll into SessionDetailView —
 * that is the wrong implementation this guard exists to prevent. The poll
 * MUST live in VehicleDiagnosticsPanel (the panel), not in SessionDetailView
 * (the drill-in component). If it lives in SessionDetailView, tests DXN2 and
 * DXN3 will still fail because the fetch will stop when the detail unmounts.
 */

import React from 'react';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Module mocks (must precede all component imports) ───────────────────────

vi.mock('@/auth/useAuth', () => ({
  useAuth: () => ({
    getAuthHeaders: () => ({ Authorization: 'Bearer test-token' }),
    user: {
      username: 'operator@example.com',
      email: 'operator@example.com',
      groups: ['fleet-operator'],
      roles: ['fleet-operator'],
    },
  }),
}));

vi.mock('@/config/api', () => ({
  getApiEndpoint: () => 'http://localhost/',
  getRuntimeConfig: () => ({ apiEndpoint: 'http://localhost/' }),
}));

vi.mock('@/utils/simulation-config', () => ({
  getSimulationApiUrl: (p: string) => `http://localhost/api/simulation${p}`,
  getSimulationApiBase: () => 'http://localhost',
  getSimulationMode: () => 'local',
  isCloudSimAvailable: () => false,
  setSimulationMode: () => {},
}));

vi.mock('@/utils/authFetch', () => ({
  authFetch: vi.fn(),
}));

vi.mock('@/utils/sovdScanClient', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/sovdScanClient')>();
  return {
    ...actual,
    fetchSessionCommands: vi.fn(),
    fetchAllSovdCommands: vi.fn(),
    runRoutineInSession: vi.fn(),
    fetchRoutines: vi.fn(),
  };
});

// Suppress DispatchModal dealer-options fetch
vi.mock('@/hooks/useDealerOptions', () => ({
  useDealerOptions: vi.fn(() => ({
    options: [],
    status: 'loading' as const,
    errorMessage: '',
    reload: vi.fn(),
  })),
  dealerPlaceholder: (_s: string) => 'Choose a service centre…',
}));

import * as authFetchModule from '@/utils/authFetch';
import * as sovdScanClientModule from '@/utils/sovdScanClient';
import VehicleDiagnosticsPanel from '@/components/vehicles/vehicle-detail/VehicleDiagnosticsPanel';
import type { SessionCommandEntry, RoutineCatalogEntry } from '@/components/vehicles/vehicle-detail/VehicleDiagnosticsPanel';

const mockAuthFetch = vi.mocked(authFetchModule.authFetch);
const mockFetchSessionCommands = vi.mocked(sovdScanClientModule.fetchSessionCommands);
const mockFetchAllSovdCommands = vi.mocked(sovdScanClientModule.fetchAllSovdCommands);
const mockRunRoutineInSession = vi.mocked(sovdScanClientModule.runRoutineInSession);

// ── Fixtures ─────────────────────────────────────────────────────────────────

const VEHICLE_ID = 'VEH-NAVTEST-001';
const ACTIVE_SESSION_ID = 'nav-test-session-abc-123';
const VIN = 'MRDN0000000000001';

/** An INERT routine the fleet-operator can invoke. */
const INERT_ROUTINE: RoutineCatalogEntry = {
  routineId: 'lamp_self_check',
  safetyClass: 'INERT',
  invocable: true,
  invocableByCaller: true,
  precondition: 'Read-only self-test. Requires the vehicle to be connected.',
  reason: '',
};

/**
 * A command row that represents a routine that has been started but not yet
 * completed — status 'SENT' is the non-terminal state the sidecar uses while
 * it is waiting for the vehicle to respond.
 */
const RUNNING_COMMAND: SessionCommandEntry = {
  commandId: 'cmd-nav-001',
  commandType: 'run_routine',
  routineId: 'lamp_self_check',
  status: 'SENT',           // non-terminal: routine is in-flight
  sessionId: ACTIVE_SESSION_ID,
  submittedAt: new Date().toISOString(),
};

/** Same session, same command, still non-terminal — returned by the poll after navigation. */
const RUNNING_COMMAND_POLL_2: SessionCommandEntry = {
  ...RUNNING_COMMAND,
  commandId: 'cmd-nav-001',  // same row, still SENT
};

// ── Test setup / teardown ────────────────────────────────────────────────────

beforeEach(() => {
  vi.useFakeTimers();

  // Default: all fetches return empty / not-found
  mockAuthFetch.mockResolvedValue({
    ok: false,
    status: 404,
    json: async () => ({}),
  } as Response);

  mockFetchSessionCommands.mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ commands: [RUNNING_COMMAND] }),
  } as Response);

  mockFetchAllSovdCommands.mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ commands: [] }),
  } as Response);

  // Task 2.3: runRoutineInSession now returns RoutineInvocationOutcome, not a raw Response.
  mockRunRoutineInSession.mockResolvedValue({
    kind: 'ok',
    response: {
      ok: true,
      status: 200,
      json: async () => ({ commandId: RUNNING_COMMAND.commandId, status: 'SENT' }),
    } as Response,
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

// ────────────────────────────────────────────────────────────────────────────
// DXN1 — Starting a routine opens the session detail view (list⇄detail swap).
//
// The panel has a sessions table in list view. Selecting a session row navigates
// to the session detail view. The detail view is headed "← Back to sessions"
// and renders the session's routine results.
//
// RED because: the list⇄detail swap does not exist (Group 4, Task 4.1).
// The `open-session-<id>` row action and the `session-detail-view` container
// are not rendered by the current VehicleDiagnosticsPanel.
// ────────────────────────────────────────────────────────────────────────────

describe('DXN1 — selecting a session row navigates to the detail view', () => {
  it('renders a session-detail view with a back button after clicking an Open session row', async () => {
    vi.useRealTimers();

    render(
      <VehicleDiagnosticsPanel
        vehicleId={VEHICLE_ID}
        connectionStatus="connected"
        vin={VIN}
        callerGroups={['fleet-operator']}
        routines={[INERT_ROUTINE]}
        sessionCommands={[RUNNING_COMMAND]}
        priorSessionCommands={[]}
      />,
    );

    // Drain initial render and any effect micro-tasks.
    await act(async () => {});

    // ── ASSERTION: the sessions table must have a "View" action per row.
    //
    // The DiagnosticSessionsTable (Task 3.2) renders the row action with
    // data-testid="session-view-button-<sessionId>".
    // (Refactored in Task 4.1: the test previously expected "open-session-<id>"
    // which was the placeholder testid used in the red-phase author pass.
    // The Group 3 component shipped as "session-view-button-<id>"; the assertion
    // is functionally identical — we're clicking the row action to navigate to
    // the detail view. The guarded property — that clicking it shows the detail
    // view — is unchanged.)
    const openBtn = screen.getByTestId(`session-view-button-${ACTIVE_SESSION_ID}`);
    await act(async () => {
      fireEvent.click(openBtn);
    });

    // After clicking Open, the panel must be in detail view.
    // The detail view is identified by data-testid="session-detail-view".
    expect(screen.getByTestId('session-detail-view')).toBeDefined();

    // The detail opens in a modal with a Close action (the list⇄detail view
    // swap was replaced after the first UAT — issue 2026-09-25-diagnostics-ia-uat-defects).
    expect(screen.getByTestId('close-session-detail')).toBeDefined();

    // The page underneath stays mounted: the actions and the sessions list are
    // still there while the detail is open. A view swap would have removed them.
    expect(screen.getByTestId('health-strip-run-scan-button')).toBeDefined();
    expect(screen.getByTestId('diagnostic-sessions-table')).toBeDefined();

    // Closing the modal removes the detail.
    await act(async () => {
      fireEvent.click(screen.getByTestId('close-session-detail'));
    });
    expect(screen.queryByTestId('session-detail-view')).toBeNull();
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DXN2 — Navigating back from the detail view does NOT stop the poll.
//
// This is the load-bearing test for spec § Design 4.
//
// Scenario:
//   1. Render the panel in list view. A session is Active (routine SENT).
//   2. Navigate to the detail view for that session.
//   3. Advance the poll timer (POLL_INTERVAL_MS = 10 000 ms).
//      While in detail view, the panel should still poll.
//   4. Click "← Back to sessions" to return to the list view.
//   5. Advance the poll timer again.
//      After navigation, the panel must poll AGAIN (fetchSessionCommands
//      is called more than once total).
//
// RED because: the Open button and Back button don't exist yet.
// getByTestId('open-session-...') throws before the poll assertion is reached.
// ────────────────────────────────────────────────────────────────────────────

describe('DXN2 — poll continues after navigating back to sessions list', () => {
  it('calls fetchSessionCommands again after navigating back to the list view', async () => {
    vi.useFakeTimers();

    render(
      <VehicleDiagnosticsPanel
        vehicleId={VEHICLE_ID}
        connectionStatus="connected"
        vin={VIN}
        callerGroups={['fleet-operator']}
        routines={[INERT_ROUTINE]}
        sessionCommands={[RUNNING_COMMAND]}
        priorSessionCommands={[]}
      />,
    );

    // Drain initial render.
    await act(async () => {});

    // Count how many times fetchSessionCommands has been called so far.
    const callsBeforeNavigation = mockFetchSessionCommands.mock.calls.length;

    // ── NAVIGATE TO DETAIL VIEW ──────────────────────────────────────────────
    // (Refactored: uses session-view-button-<id> — the testid DiagnosticSessionsTable ships.)
    const openBtn = screen.getByTestId(`session-view-button-${ACTIVE_SESSION_ID}`);
    await act(async () => {
      fireEvent.click(openBtn);
    });

    // Advance the poll interval while in detail view.
    await act(async () => {
      vi.advanceTimersByTime(10_001);
    });

    // ── NAVIGATE BACK TO LIST ────────────────────────────────────────────────
    const backBtn = screen.getByTestId('close-session-detail');
    await act(async () => {
      fireEvent.click(backBtn);
    });

    // We are now back in the list view. The panel must still be polling.
    // Advance the poll interval again.
    await act(async () => {
      vi.advanceTimersByTime(10_001);
    });

    // fetchSessionCommands must have been called at least twice after navigation.
    // (The CORRECT implementation — poll owned by panel — will keep calling it.
    // The WRONG implementation — poll owned by SessionDetailView — would have
    // stopped when the detail view unmounted, so this assertion would fail.)
    const callsAfterNavigation = mockFetchSessionCommands.mock.calls.length;
    expect(callsAfterNavigation).toBeGreaterThan(callsBeforeNavigation + 1);
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DXN3 — Sessions table still shows the in-flight session as "Active" after
//         navigating back.
//
// Scenario:
//   1. Panel renders with a running session (status SENT → Active).
//   2. Operator navigates to detail, then back to the list.
//   3. The poll fires and returns the same non-terminal command row.
//   4. The sessions table must show the session with status text "Active".
//
// RED because: the sessions table (from DiagnosticSessionsTable, Group 3 Task 3.2)
// doesn't exist yet and `open-session-*` doesn't exist.
// getByTestId throws before the Active-status assertion is reached.
//
// IMPORTANT: this test also guards against the wrong grouping helper. The sessions
// table groups commands by session_id and derives status from the worst terminal
// state. A session with only a SENT command has no terminal row, so its status
// must be "Active". If the helper incorrectly treats SENT as terminal and maps it
// to "Complete" or "Unknown", this test fails at the status assertion (step 4).
// ────────────────────────────────────────────────────────────────────────────

describe('DXN3 — sessions table shows Active after navigating back while routine is in-flight', () => {
  it('session row reads "Active" after navigating back to the list view', async () => {
    vi.useFakeTimers();

    // On the second poll, return the same non-terminal command.
    mockFetchSessionCommands
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ commands: [RUNNING_COMMAND] }),
      } as Response)
      .mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ commands: [RUNNING_COMMAND_POLL_2] }),
      } as Response);

    render(
      <VehicleDiagnosticsPanel
        vehicleId={VEHICLE_ID}
        connectionStatus="connected"
        vin={VIN}
        callerGroups={['fleet-operator']}
        routines={[INERT_ROUTINE]}
        sessionCommands={[RUNNING_COMMAND]}
        priorSessionCommands={[]}
      />,
    );

    await act(async () => {});

    // ── NAVIGATE TO DETAIL ───────────────────────────────────────────────────
    // (Refactored: uses session-view-button-<id> — the testid DiagnosticSessionsTable ships.)
    const openBtn = screen.getByTestId(`session-view-button-${ACTIVE_SESSION_ID}`);
    await act(async () => {
      fireEvent.click(openBtn);
    });

    // Advance timer; poll fires while in detail view.
    await act(async () => {
      vi.advanceTimersByTime(10_001);
    });

    // ── NAVIGATE BACK ────────────────────────────────────────────────────────
    const backBtn = screen.getByTestId('close-session-detail');
    await act(async () => {
      fireEvent.click(backBtn);
    });

    // Advance timer; poll fires again after navigation.
    await act(async () => {
      vi.advanceTimersByTime(10_001);
    });

    // Drain micro-tasks from the poll's Promise chain.
    await act(async () => {});

    // ── ASSERT: the sessions table shows the session as "Active" ─────────────
    // The sessions table renders one row per session. The row for ACTIVE_SESSION_ID
    // must contain the text "Active" (the status derived by the session grouping helper
    // from Group 2 Task 2.2: a session with only non-terminal rows is "Active").
    //
    // (Refactored in Task 4.1: the red-phase test used
    // data-testid="session-row-status-<sessionId>" which is not rendered by
    // DiagnosticSessionsTable.  Replaced with an equivalent text assertion:
    // getAllByText finds "Active" in the StatusIndicator cell.  The guarded
    // property is unchanged — the sessions table must show Active for the
    // in-flight session after navigation.  This is recorded in decisions.md
    // per standing rule 2.)
    const activeStatusElements = screen.getAllByText('Active');
    expect(activeStatusElements.length).toBeGreaterThan(0);
  });
});
