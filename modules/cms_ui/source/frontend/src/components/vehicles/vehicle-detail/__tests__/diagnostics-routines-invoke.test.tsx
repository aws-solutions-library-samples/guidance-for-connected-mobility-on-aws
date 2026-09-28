// SPDX-License-Identifier: Apache-2.0

/**
 * Vehicle diagnostics — Stage 3 invoke surface (DX58–DX64).
 *
 * Spec: `.kiro/specs/2026-09-02-cms-diagnostics-platform`
 *       § Group 11 T11.0a (red-phase tests for invoke)
 *       decisions.md § 2026-09-09 (F29, F30, D26, D27, D28)
 *
 * ── Why this file exists alongside diagnostics-routines.test.tsx ─────────────
 *
 * DX51–DX57 certified the DISCLOSURE surface: safety-class grouping,
 * precondition text, SERVICE_ONLY reasons.  Those tests run against the
 * current interface (`routine_id`, `safety_class`).
 *
 * DX58–DX64 certify the INVOKE surface: the attestation modal, the POST,
 * the `invocable` gate (D27), and the refusal-status gate (F30).  The
 * fixtures here use **camelCase** fields (`routineId`, `safetyClass`,
 * `invocable`, `precondition`) — the shape the server returns after T11.2
 * (D26: the frontend moves to camelCase at the Lambda boundary).
 *
 * Do NOT touch `diagnostics-routines.test.tsx` — T11.4 owns its fixture
 * rename from snake_case to camelCase.
 *
 * ── What is RED, and why ──────────────────────────────────────────────────────
 *
 * Every test in this file is expected to fail until T11.2–T11.5 ship.
 * The failures are AssertionErrors (or `expect` failures), not import
 * errors: VehicleDiagnosticsPanel exists; it is missing the fetch, the
 * onClick, the modal, and the inverted refusal gate.  Specifically:
 *
 * DX58  RED  — camelCase props (routineId, safetyClass, invocable, precondition)
 *              are not yet accepted; the panel interface is snake_case (D26).
 *              The routines section will render nothing.
 * DX59  RED  — clicking the Run button does nothing today (no onClick); the
 *              modal does not open.  F29: the section is also invisible at HEAD
 *              when the caller passes camelCase props.
 * DX60  RED  — fetchRoutines and RunRoutineModal do not exist; no POST is ever
 *              issued.
 * DX61  RED  — the attestation checkbox does not exist yet; HARD GATE I cannot
 *              be asserted.
 * DX62  RED  — `invocable: false` renders a Run button today because the panel
 *              gates on `cls !== 'SERVICE_ONLY'`, not on `entry.invocable === true`
 *              (D27).  The positive control passes trivially today (no section
 *              rendered with camelCase props), making the negative-control the
 *              load-bearing assertion.
 * DX63  RED  — F30: the refusal gate enumerates REFUSED/FAILED/RATE_LIMITED;
 *              none of the four sidecar statuses is in that set, so refusal
 *              reasons never render.  After T11.4 the gate inverts to
 *              `status !== 'SUCCEEDED' && reason`.
 * DX64  RED  — no 400 error surface exists today; the panel has no error-render
 *              path for synchronous command rejection.
 *
 * ── Test ID map ──────────────────────────────────────────────────────────────
 *
 * DX58  Panel renders routines from camelCase fixtures supplied via
 *       `fetchRoutines` (T11.2); routine names are visible.
 * DX59  Clicking `run-routine-<id>` opens the attestation modal.  The
 *       `onClick` reaches something — F29 says nothing in the suite makes
 *       this assertion today.
 * DX60  Confirming the modal issues exactly one `run_routine` POST carrying
 *       `routine_id` and an `attestation.text` that is non-empty.
 *       Negative control: no POST while the checkbox is unchecked.
 * DX61  HARD GATE I — the attestation checkbox resets to unchecked on every
 *       modal open, including a re-open after it was previously checked.
 * DX62  `invocable: false` renders no Run button; an entry omitting
 *       `invocable` entirely also renders none (D27 fail-closed).
 *       Positive control: `invocable: true` renders one.
 * DX63  Each of the four sidecar statuses renders its `reason` verbatim (F30
 *       regression).  Negative control: SUCCEEDED + reason shows no refusal
 *       banner.
 * DX64  A synchronous 400 body's `error` and `reason` render verbatim.  No
 *       synthesised copy.
 */

import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
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
    fetchRoutines: vi.fn(),
  };
});

import * as authFetchModule from '@/utils/authFetch';
import * as sovdScanClientModule from '@/utils/sovdScanClient';
import VehicleDiagnosticsPanel from '@/components/vehicles/vehicle-detail/VehicleDiagnosticsPanel';

const mockAuthFetch = vi.mocked(authFetchModule.authFetch);
const mockFetchRoutines = vi.mocked(sovdScanClientModule.fetchRoutines);

// ── Fixtures — camelCase shape (D26) ─────────────────────────────────────────
//
// These use the endpoint's camelCase field names (routineId, safetyClass,
// invocable, precondition) which the panel interface does not yet accept.
// T11.4 renames the interface; T11.2 wires fetchRoutines.

const CAMEL_INERT_ROUTINE = {
  routineId: 'lamp_self_check',
  safetyClass: 'INERT' as const,
  invocable: true,
  precondition: 'Read-only self-test. Requires the vehicle to be connected.',
  reason: '',
};

const CAMEL_STATIONARY_ROUTINE = {
  routineId: 'abs_pump_cycle',
  safetyClass: 'STATIONARY' as const,
  invocable: true,
  precondition:
    'Requires the vehicle to be stationary, in park or neutral, with ignition on. ' +
    'Checked again at the vehicle immediately before the routine runs.',
  reason: '',
};

const CAMEL_SERVICE_ONLY_ROUTINE = {
  routineId: 'dpf_regeneration',
  safetyClass: 'SERVICE_ONLY' as const,
  invocable: false,
  precondition: 'Requires service visit. Not available remotely.',
  reason:
    'Requires verified site preconditions that no telemetry can confirm: ' +
    'vehicle outdoors, area free of combustibles and flammable vapours.',
};

// Routine with invocable explicitly false (not SERVICE_ONLY, but flagged non-invocable
// to exercise D27's field gate independently of the class gate).
const CAMEL_NON_INVOCABLE_INERT = {
  routineId: 'pack_isolation_test',
  safetyClass: 'INERT' as const,
  invocable: false,
  precondition: 'Read-only self-test. Requires the vehicle to be connected.',
  reason: '',
};

// Routine that omits invocable entirely (D27 fail-closed: absent means no).
const CAMEL_NO_INVOCABLE_FIELD = {
  routineId: 'cell_balance_check',
  safetyClass: 'INERT' as const,
  precondition: 'Read-only self-test. Requires the vehicle to be connected.',
  reason: '',
};

const VEHICLE_ID = 'VEH-TEST-001';

// ── Setup / teardown ─────────────────────────────────────────────────────────

beforeEach(() => {
  vi.useFakeTimers();
  mockAuthFetch.mockResolvedValue({ ok: false, status: 404, json: async () => ({}) } as Response);
  // Default: fetchRoutines returns empty — tests that need routines override this.
  mockFetchRoutines.mockResolvedValue({ ok: false, status: 404, json: async () => ({}) } as Response);
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

// ────────────────────────────────────────────────────────────────────────────
// DX58 — panel renders routines from camelCase fixtures supplied via fetchRoutines
// ────────────────────────────────────────────────────────────────────────────

describe('DX58 — panel renders routines from mocked fetchRoutines (camelCase fixtures, D26)', () => {
  it('makes routine names visible once fetchRoutines resolves', async () => {
    // The panel calls fetchRoutines (T11.2) and renders the returned
    // camelCase entries.  The mock is sovdScanClient.fetchRoutines (the named
    // export), which routes through authFetch in production but is mocked
    // directly here so the interception is synchronous and does not require
    // fake-timer advancement for waitFor to see the DOM update.
    //
    // D26: interface fields are camelCase (routineId, safetyClass, invocable,
    // precondition).  At HEAD before T11.4: the interface was snake_case, so
    // the section never rendered.  T11.4 renames the interface so this test
    // turns green.
    vi.useRealTimers(); // waitFor requires real timers; fake timers freeze its polling
    mockFetchRoutines.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        vehicleId: VEHICLE_ID,
        powertrainProfile: 'diesel',
        routines: [CAMEL_INERT_ROUTINE, CAMEL_STATIONARY_ROUTINE],
      }),
    } as Response);

    await act(async () => {
      render(
        <VehicleDiagnosticsPanel
          vehicleId={VEHICLE_ID}
          connectionStatus="connected"
        />,
      );
    });

    // Drain remaining microtasks from the fetchRoutines Promise chain.
    await act(async () => {});

    // The routine section heading must appear after the fetch resolves.
    // (Task 4.1 refactoring: RoutineOffering (Task 3.4) replaces the old
    // RoutinesSection with "Diagnostic routines" heading. The new heading is
    // "Self-tests you can run here" for INERT routines. Guarded property —
    // routine names must appear — is unchanged. See decisions.md Task 4.1.)
    await waitFor(() => {
      expect(screen.queryByText(/Self-tests you can run here/i)).not.toBeNull();
    });

    // Each routine's id must appear in the rendered section.
    expect(screen.queryByText(/lamp_self_check/)).not.toBeNull();
    expect(screen.queryByText(/abs_pump_cycle/)).not.toBeNull();
  });

  it('renders the invocable precondition text from the server field, not GROUP_HEADER', async () => {
    // D28: the server's `precondition` field replaces the hardcoded GROUP_HEADER.
    // The panel renders `entry.precondition` per group header rather than the
    // GROUP_HEADER constant (which is deleted in T11.4).  The STATIONARY
    // precondition from the server includes "ignition on" — GROUP_HEADER only
    // said "Requires vehicle stationary".
    vi.useRealTimers(); // waitFor requires real timers
    mockFetchRoutines.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        vehicleId: VEHICLE_ID,
        powertrainProfile: 'diesel',
        routines: [CAMEL_STATIONARY_ROUTINE],
      }),
    } as Response);

    await act(async () => {
      render(
        <VehicleDiagnosticsPanel
          vehicleId={VEHICLE_ID}
          connectionStatus="connected"
        />,
      );
    });

    // Drain remaining microtasks from the fetchRoutines Promise chain.
    await act(async () => {});

    // The server string for STATIONARY includes "ignition on" — GROUP_HEADER
    // only says "Requires vehicle stationary".  If the panel renders the
    // server's precondition field, "ignition on" appears.
    await waitFor(() => {
      const body = document.body.textContent ?? '';
      expect(body).toMatch(/ignition on/i);
    });
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX59 — clicking run-routine-<id> opens the attestation modal
// ────────────────────────────────────────────────────────────────────────────

describe('DX59 — clicking run-routine-<id> opens the attestation modal', () => {
  it('opens an attestation modal when the Run button is clicked', async () => {
    // F29: the section is invisible at HEAD when camelCase props are supplied,
    // because fetchRoutines does not exist and the interface is snake_case.
    // Even if we pass `routines` directly with snake_case fixtures (which would
    // render the section), the Run button has no onClick — clicking it does nothing.
    //
    // T11.4: fetchRoutines now exists and is mocked via sovdScanClient; the
    // interface is camelCase (D26); the Run button has an onClick that opens the modal.
    vi.useRealTimers(); // waitFor requires real timers
    mockFetchRoutines.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        vehicleId: VEHICLE_ID,
        powertrainProfile: 'ev',
        routines: [CAMEL_INERT_ROUTINE],
      }),
    } as Response);

    await act(async () => {
      render(
        <VehicleDiagnosticsPanel
          vehicleId={VEHICLE_ID}
          connectionStatus="connected"
        />,
      );
    });

    // Drain remaining microtasks from the fetchRoutines Promise chain.
    await act(async () => {});

    const runBtn = await waitFor(() => {
      const btn = screen.getByTestId('run-routine-lamp_self_check');
      return btn;
    });

    fireEvent.click(runBtn);

    // After click, an attestation modal must be visible.  It must carry
    // a checkbox (HARD GATE I) and a confirm button.
    await waitFor(() => {
      // The modal is identified by its heading or a role="dialog".
      const dialog = screen.queryByRole('dialog');
      expect(dialog).not.toBeNull();
    });

    // The attestation checkbox must be present and unchecked (DX61 pre-check).
    const checkbox = screen.queryByRole('checkbox');
    expect(checkbox).not.toBeNull();
    expect(checkbox).not.toBeChecked();
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX60 — confirming the modal issues exactly one run_routine POST
// ────────────────────────────────────────────────────────────────────────────

describe('DX60 — confirming the modal issues exactly one run_routine POST', () => {
  it('issues exactly one POST with routine_id and non-empty attestation.text when confirmed', async () => {
    // T11.4: fetchRoutines is mocked via sovdScanClient; the modal exists (T11.3);
    // the Run button has an onClick; the POST is issued via runRoutine in sovdScanClient.
    vi.useRealTimers(); // waitFor requires real timers
    mockFetchRoutines.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        vehicleId: VEHICLE_ID,
        powertrainProfile: 'ev',
        routines: [CAMEL_INERT_ROUTINE],
      }),
    } as Response);
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url.includes('/commands/')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ status: 'SUCCEEDED', reason: '' }),
        } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });

    await act(async () => {
      render(
        <VehicleDiagnosticsPanel
          vehicleId={VEHICLE_ID}
          connectionStatus="connected"
        />,
      );
    });

    // Drain remaining microtasks from the fetchRoutines Promise chain.
    await act(async () => {});

    // Open the modal
    const runBtn = await waitFor(() => screen.getByTestId('run-routine-lamp_self_check'));
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeNull();
    });

    // Tick the attestation checkbox so the confirm button becomes enabled
    const checkbox = screen.getByRole('checkbox');
    fireEvent.click(checkbox);
    expect(checkbox).toBeChecked();

    // Click confirm
    const confirmBtn = screen.getByRole('button', { name: /confirm|run|execute/i });
    await act(async () => {
      fireEvent.click(confirmBtn);
    });

    // Exactly one POST to the commands endpoint carrying run_routine
    const commandCalls = mockAuthFetch.mock.calls.filter(
      ([url, options]) =>
        typeof url === 'string' &&
        url.includes('/commands/') &&
        (options as RequestInit | undefined)?.method === 'POST',
    );
    expect(commandCalls).toHaveLength(1);

    const [, opts] = commandCalls[0];
    const body = JSON.parse((opts as RequestInit).body as string);
    expect(body.command_type).toBe('run_routine');
    expect(body.routine_id).toBe('lamp_self_check');
    expect(body.attestation).toBeDefined();
    expect(typeof body.attestation.text).toBe('string');
    expect(body.attestation.text.trim()).not.toBe('');
  });

  it('issues NO POST while the attestation checkbox is unchecked (negative control)', async () => {
    // This test is RED for the same reasons: no fetchRoutines, no onClick, no modal.

    vi.useRealTimers(); // waitFor requires real timers
    mockFetchRoutines.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        vehicleId: VEHICLE_ID,
        powertrainProfile: 'ev',
        routines: [CAMEL_INERT_ROUTINE],
      }),
    } as Response);

    await act(async () => {
      render(
        <VehicleDiagnosticsPanel
          vehicleId={VEHICLE_ID}
          connectionStatus="connected"
        />,
      );
    });

    // Drain remaining microtasks from the fetchRoutines Promise chain.
    await act(async () => {});

    // Open the modal WITHOUT ticking the checkbox
    const runBtn = await waitFor(() => screen.getByTestId('run-routine-lamp_self_check'));
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeNull();
    });

    // Do NOT tick the checkbox.  The confirm button should be disabled.
    const confirmBtn = screen.queryByRole('button', { name: /confirm|run|execute/i });
    if (confirmBtn && !confirmBtn.hasAttribute('disabled')) {
      fireEvent.click(confirmBtn);
    }

    // No run_routine POST should have been issued
    const commandCalls = mockAuthFetch.mock.calls.filter(
      ([url, options]) =>
        typeof url === 'string' &&
        url.includes('/commands/') &&
        (options as RequestInit | undefined)?.method === 'POST',
    );
    expect(commandCalls).toHaveLength(0);
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX61 — HARD GATE I — attestation checkbox resets on every open
// ────────────────────────────────────────────────────────────────────────────

describe('DX61 — HARD GATE I: attestation checkbox resets to unchecked on every modal open', () => {
  it('unchecks the checkbox on a re-open after a previous open where it was checked', async () => {
    // This test is RED because: the modal does not exist, so there is no
    // checkbox state to persist or reset.

    vi.useRealTimers(); // waitFor requires real timers
    mockFetchRoutines.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        vehicleId: VEHICLE_ID,
        powertrainProfile: 'ev',
        routines: [CAMEL_INERT_ROUTINE],
      }),
    } as Response);

    await act(async () => {
      render(
        <VehicleDiagnosticsPanel
          vehicleId={VEHICLE_ID}
          connectionStatus="connected"
        />,
      );
    });

    // Drain remaining microtasks from the fetchRoutines Promise chain.
    await act(async () => {});

    const runBtn = await waitFor(() => screen.getByTestId('run-routine-lamp_self_check'));

    // ── First open ──
    fireEvent.click(runBtn);
    await waitFor(() => { expect(screen.queryByRole('dialog')).not.toBeNull(); });

    const checkbox1 = screen.getByRole('checkbox');
    expect(checkbox1).not.toBeChecked();

    // Tick the checkbox
    fireEvent.click(checkbox1);
    expect(checkbox1).toBeChecked();

    // Close the modal via Cancel or dismiss
    const cancelBtn = screen.queryByRole('button', { name: /cancel|close|dismiss/i });
    if (cancelBtn) {
      fireEvent.click(cancelBtn);
    } else {
      // Dismiss by pressing Escape
      fireEvent.keyDown(document.body, { key: 'Escape' });
    }

    await waitFor(() => { expect(screen.queryByRole('dialog')).toBeNull(); });

    // ── Second open ──
    fireEvent.click(runBtn);
    await waitFor(() => { expect(screen.queryByRole('dialog')).not.toBeNull(); });

    // HARD GATE I: checkbox MUST be unchecked, even though it was checked
    // in the previous open.
    const checkbox2 = screen.getByRole('checkbox');
    expect(checkbox2).not.toBeChecked();
  });

  it('checkbox is unchecked on the very first open (baseline assertion)', async () => {
    // FORWARD-BINDING: this assertion would pass trivially if the modal does
    // not render at all (queryByRole returns null, and the unchecked assertion
    // is never reached).  It becomes load-bearing once T11.3 ships RunRoutineModal.
    vi.useRealTimers(); // waitFor requires real timers
    mockFetchRoutines.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        vehicleId: VEHICLE_ID,
        powertrainProfile: 'ev',
        routines: [CAMEL_INERT_ROUTINE],
      }),
    } as Response);

    await act(async () => {
      render(
        <VehicleDiagnosticsPanel
          vehicleId={VEHICLE_ID}
          connectionStatus="connected"
        />,
      );
    });

    // Drain remaining microtasks from the fetchRoutines Promise chain.
    await act(async () => {});

    const runBtn = await waitFor(() => screen.getByTestId('run-routine-lamp_self_check'));
    fireEvent.click(runBtn);

    await waitFor(() => { expect(screen.queryByRole('dialog')).not.toBeNull(); });
    const checkbox = screen.getByRole('checkbox');
    expect(checkbox).not.toBeChecked();
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX62 — invocable gate (D27)
// ────────────────────────────────────────────────────────────────────────────

describe('DX62 — invocable gate: invocable:false or absent → no Run button (D27 fail-closed)', () => {
  it('renders no Run button for an entry with invocable:false', async () => {
    // D27: `entry.invocable === true` is the gate.  At HEAD the panel gates
    // on `cls !== 'SERVICE_ONLY'`, not on the field.  This INERT entry with
    // invocable:false would still get a Run button at HEAD — BUT only if the
    // section renders.
    //
    // T11.4: the gate is now `entry.invocable === true` (D27). The section
    // renders (via mockFetchRoutines), and pack_isolation_test with invocable:false
    // must NOT get a Run button.
    mockFetchRoutines.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        vehicleId: VEHICLE_ID,
        powertrainProfile: 'ev',
        routines: [CAMEL_NON_INVOCABLE_INERT],
      }),
    } as Response);

    await act(async () => {
      render(
        <VehicleDiagnosticsPanel
          vehicleId={VEHICLE_ID}
          connectionStatus="connected"
        />,
      );
    });

    // Wait for the fetchRoutines Promise chain to resolve.
    // (Task 4.1: replaced vi.runAllTimers() with act + microtask drain to avoid
    // the infinite-loop from the session-refresh setInterval. The interval fires
    // every 10s; runAllTimers() would keep re-queuing it until the 10000-timer limit.)
    await act(async () => {});

    // No Run button for pack_isolation_test (invocable:false)
    const runBtn = screen.queryByTestId('run-routine-pack_isolation_test');
    expect(runBtn).toBeNull();
  });

  it('renders no Run button for an entry omitting invocable entirely (fail-closed)', async () => {
    // D27 fail-closed: absent invocable means no button.
    //
    // T11.4: the section renders (via mockFetchRoutines), and an entry without
    // `invocable` must be treated as invocable:false (fail-closed, same lesson as F28).
    mockFetchRoutines.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        vehicleId: VEHICLE_ID,
        powertrainProfile: 'ev',
        routines: [CAMEL_NO_INVOCABLE_FIELD],
      }),
    } as Response);

    await act(async () => {
      render(
        <VehicleDiagnosticsPanel
          vehicleId={VEHICLE_ID}
          connectionStatus="connected"
        />,
      );
    });

    await act(async () => {}); // drain microtasks (see DX62 note above)

    const runBtn = screen.queryByTestId('run-routine-cell_balance_check');
    expect(runBtn).toBeNull();
  });

  it('renders a Run button for an entry with invocable:true (positive control)', async () => {
    // FORWARD-BINDING: passes trivially at HEAD because the camelCase fetch
    // does not land and the section is absent.  Becomes load-bearing once
    // T11.2 and T11.4 ship (at which point the absent-section path fails the
    // `not.toBeNull()` assertion).
    vi.useRealTimers(); // waitFor requires real timers
    mockFetchRoutines.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        vehicleId: VEHICLE_ID,
        powertrainProfile: 'ev',
        routines: [CAMEL_INERT_ROUTINE],
      }),
    } as Response);

    await act(async () => {
      render(
        <VehicleDiagnosticsPanel
          vehicleId={VEHICLE_ID}
          connectionStatus="connected"
        />,
      );
    });

    // Drain remaining microtasks from the fetchRoutines Promise chain.
    await act(async () => {});

    // lamp_self_check has invocable:true — a Run button must exist.
    const runBtn = await waitFor(() => {
      const btn = screen.queryByTestId('run-routine-lamp_self_check');
      expect(btn).not.toBeNull();
      return btn;
    });
    expect(runBtn).not.toBeNull();
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX63 — all four sidecar refusal statuses render their reason verbatim (F30)
// ────────────────────────────────────────────────────────────────────────────

describe('DX63 — four sidecar refusal statuses render reason verbatim (F30 regression)', () => {
  // F30: the panel's current refusal gate matches REFUSED | FAILED | RATE_LIMITED.
  // The sidecar emits four different statuses; none matches any of the three.
  // After T11.4 the gate inverts: status !== 'SUCCEEDED' && reason.

  const REFUSAL_CASES: Array<[string, string]> = [
    [
      'PRECONDITION_FAILED_MOVING',
      'PRECONDITION_FAILED_MOVING: vehicle speed 42 mph at preflight; routine requires stationary.',
    ],
    [
      'REFUSED_SERVICE_ONLY',
      'REFUSED_SERVICE_ONLY: dpf_regeneration is classified SERVICE_ONLY; remote invocation is blocked.',
    ],
    [
      'UNRESOLVABLE_SAFETY_CLASS',
      'UNRESOLVABLE_SAFETY_CLASS: powertrain profile returned unknown class "EV_HEAVY_TOWING".',
    ],
    [
      'REFUSED_NO_VEHICLE_STATE',
      'REFUSED_NO_VEHICLE_STATE: no vehicle-state record found in command table for VEH-TEST-001.',
    ],
  ];

  it.each(REFUSAL_CASES)(
    'renders reason verbatim when latestRoutineResult.status is %s',
    async (status, reason) => {
      // This test is RED because the current refusal gate explicitly lists
      // REFUSED / FAILED / RATE_LIMITED — none of these four statuses is in
      // that list, so the reason is never rendered.

      // Supply routines via the camelCase interface so the section renders
      // and the refusal banner is visible.
      render(
        <VehicleDiagnosticsPanel
          vehicleId={VEHICLE_ID}
          connectionStatus="connected"
          routines={[
            { routineId: 'abs_pump_cycle', safetyClass: 'STATIONARY', invocable: true, precondition: 'Requires vehicle stationary.', reason: '' },
          ]}
          latestRoutineResult={{ routineId: 'abs_pump_cycle', status, reason }}
        />,
      );

      // The reason string must appear verbatim in the rendered output.
      const body = document.body.textContent ?? '';
      expect(body).toContain(reason);
    },
  );

  it('renders NO refusal banner when status is SUCCEEDED (inverted gate negative control)', () => {
    // After T11.4 the gate is `status !== 'SUCCEEDED'`.  This negative control
    // pins the other direction: a SUCCEEDED result with a non-empty reason
    // must NOT show a refusal banner.
    //
    // FORWARD-BINDING: at HEAD, SUCCEEDED is already excluded from the
    // REFUSED/FAILED/RATE_LIMITED list, so this passes trivially.  It
    // becomes load-bearing once the inverted gate ships — preventing a
    // regression where the inversion accidentally fires on SUCCEEDED too.
    const reason = 'Routine completed successfully.';
    render(
      <VehicleDiagnosticsPanel
        vehicleId={VEHICLE_ID}
        connectionStatus="connected"
        routines={[
          { routineId: 'abs_pump_cycle', safetyClass: 'STATIONARY', invocable: true, precondition: 'Requires vehicle stationary.', reason: '' },
        ]}
        latestRoutineResult={{ routineId: 'abs_pump_cycle', status: 'SUCCEEDED', reason }}
      />,
    );

    // The reason string for a SUCCEEDED result must NOT appear in a refusal
    // banner.  It is acceptable if it appears in a success indicator, but
    // it must not render inside the warning-color refusal block.
    const warningBoxes = document.querySelectorAll('[color="text-status-warning"]');
    for (const box of warningBoxes) {
      expect(box.textContent ?? '').not.toContain(reason);
    }
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX64 — synchronous 400 body renders error and reason verbatim
// ────────────────────────────────────────────────────────────────────────────

describe('DX64 — synchronous 400 body renders error and reason verbatim (no synthesised copy)', () => {
  it('renders the 400 error string verbatim when the POST returns a 400', async () => {
    // This test is RED because: the POST path does not exist (no fetchRoutines,
    // no RunRoutineModal, no onClick) — no 400 is ever issued or rendered.
    //
    // The test will fail at the `screen.getByTestId('run-routine-lamp_self_check')`
    // step (element not found) because fetchRoutines does not land, which prevents
    // the section from rendering.  The `waitFor` timeout is shortened so it fails
    // with an informative message rather than hanging.
    const ERROR_MSG = 'attestation is required for run_routine and must be an object.';
    const REASON_MSG = 'PRECONDITION_FAILED_MOVING: vehicle speed 31 mph at attestation gate.';

    vi.useRealTimers(); // waitFor and the POST-to-render path require real timers
    mockFetchRoutines.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        vehicleId: VEHICLE_ID,
        powertrainProfile: 'ev',
        routines: [CAMEL_INERT_ROUTINE],
      }),
    } as Response);
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url.includes('/commands/')) {
        return {
          ok: false,
          status: 400,
          json: async () => ({ error: ERROR_MSG, reason: REASON_MSG }),
        } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });

    await act(async () => {
      render(
        <VehicleDiagnosticsPanel
          vehicleId={VEHICLE_ID}
          connectionStatus="connected"
        />,
      );
    });

    // Drain remaining microtasks from the fetchRoutines Promise chain.
    await act(async () => {});

    // Step 1: the Run button must exist (requires fetchRoutines → RED at HEAD)
    const runBtn = screen.queryByTestId('run-routine-lamp_self_check');
    expect(
      runBtn,
      'DX64: run-routine button must exist — requires fetchRoutines (T11.2) to land',
    ).not.toBeNull();

    // Steps 2–4 are only reached once runBtn is non-null (i.e., post-T11.2/T11.4).
    // They will be exercised in the green phase.
    if (runBtn) {
      fireEvent.click(runBtn);

      await waitFor(() => { expect(screen.queryByRole('dialog')).not.toBeNull(); },
        { timeout: 2000 });

      const checkbox = screen.getByRole('checkbox');
      fireEvent.click(checkbox);

      const confirmBtn = screen.getByRole('button', { name: /confirm|run|execute/i });
      await act(async () => { fireEvent.click(confirmBtn); });

      // After the 400, both the `error` and `reason` fields must appear verbatim.
      await waitFor(() => {
        const body = document.body.textContent ?? '';
        expect(body).toContain(ERROR_MSG);
        expect(body).toContain(REASON_MSG);
      }, { timeout: 2000 });
    }
  });

  it('does NOT render synthesised fallback copy on a 400 error (negative control)', async () => {
    // An implementation that maps the 400 to a helpful message like "please
    // check the attestation" is synthesising copy.  The discipline is the
    // same as DX56: the server's strings are the strings.
    //
    // FORWARD-BINDING: passes trivially at HEAD because no 400 error surface
    // exists yet.  Becomes load-bearing once T11.3/T11.4 ship the modal and
    // the POST path.

    mockFetchRoutines.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        vehicleId: VEHICLE_ID,
        powertrainProfile: 'ev',
        routines: [CAMEL_INERT_ROUTINE],
      }),
    } as Response);
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url.includes('/commands/')) {
        return {
          ok: false,
          status: 400,
          json: async () => ({ error: 'routine_id not found in catalog.', reason: '' }),
        } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });

    await act(async () => {
      render(
        <VehicleDiagnosticsPanel
          vehicleId={VEHICLE_ID}
          connectionStatus="connected"
        />,
      );
    });

    const body = (document.body.textContent ?? '').toLowerCase();
    // Forbidden synthesised messages
    expect(body).not.toMatch(/please check the attestation|please try again|check your input/);
    expect(body).not.toMatch(/unexpected error occurred|something went wrong/);
  });
});


// ────────────────────────────────────────────────────────────────────────────
// DX66 — delivery, not rendering: refusal reason reaches the operator via the
//         runtime path, not via a prop (F31 regression)
// ────────────────────────────────────────────────────────────────────────────

describe('DX66 — delivery: sidecar refusal reason reaches the operator without latestRoutineResult prop (F31)', () => {
  /**
   * Why this test exists alongside DX63
   * ------------------------------------
   * DX63 proves the panel CAN render a refusal reason when it is HANDED one via
   * the `latestRoutineResult` prop.  It cannot see F31 because supplying the prop
   * bypasses the delivery path entirely.
   *
   * F31 (decisions.md § 2026-09-09): `handleModalConfirm` POSTs, renders a
   * synchronous 400 body, and then stops.  On a successful POST the only comment
   * is "Success is silent here; latestRoutineResult prop handles outcome rendering."
   * But `latestRoutineResult` is a prop nobody sets after an invocation —
   * T11.5's caller reads it once on mount and never re-reads.  The four sidecar
   * refusals (PRECONDITION_FAILED_MOVING, REFUSED_SERVICE_ONLY, …) land on the
   * command row seconds after the POST and reach nobody.
   *
   * DX66 drives the runtime path:
   *   1. No `latestRoutineResult` prop is supplied.
   *   2. The POST returns 200 (server accepted the command).
   *   3. A subsequent GET of the command row (fetchLatestSovdCommand) returns a
   *      row with `PRECONDITION_FAILED_MOVING` and a reason.
   *   4. The reason must appear in the DOM.
   *
   * This test is RED at HEAD because step 4 fails: nothing calls
   * fetchLatestSovdCommand after a successful POST, so the reason never appears.
   * The failure must be "expected … but received …" or a waitFor timeout,
   * NOT a missing-button or missing-mock error.
   */

  const REFUSAL_REASON =
    'PRECONDITION_FAILED_MOVING: vehicle speed 37 mph at preflight; routine requires stationary.';

  it(
    'renders sidecar refusal reason after a successful POST + command-row re-read ' +
    '(no latestRoutineResult prop — F31 regression gate)',
    async () => {
      vi.useRealTimers(); // waitFor and the POST-to-render path require real timers

      // Routines fetch: one invocable INERT routine.
      // T3.1: Active Run buttons are offered only for INERT routines; STATIONARY
      // routines now render disabled per condition (d). The runtime poll path
      // (F31) is class-independent — testing with INERT preserves the regression gate.
      mockFetchRoutines.mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          vehicleId: VEHICLE_ID,
          powertrainProfile: 'ev',
          routines: [CAMEL_INERT_ROUTINE],
        }),
      } as Response);

      // authFetch routing:
      //   POST to /commands/{id}          → 200 (server accepted the command)
      //   GET  to /commands/{id}?type=... → 200 carrying PRECONDITION_FAILED_MOVING
      //   anything else                    → 404
      mockAuthFetch.mockImplementation(async (url: string, opts?: RequestInit) => {
        const method = opts?.method ?? 'GET';
        if (typeof url === 'string' && url.includes('/commands/')) {
          if (method === 'POST') {
            // Successful command acceptance.
            return {
              ok: true,
              status: 200,
              json: async () => ({ commandId: 'cmd-abc-123', status: 'ACCEPTED' }),
            } as Response;
          }
          // GET re-read: the command row now carries the sidecar's async refusal.
          return {
            ok: true,
            status: 200,
            json: async () => ({
              commands: [
                {
                  commandId: 'cmd-abc-123',
                  commandType: 'run_routine',
                  status: 'PRECONDITION_FAILED_MOVING',
                  reason: REFUSAL_REASON,
                },
              ],
            }),
          } as Response;
        }
        return { ok: false, status: 404, json: async () => ({}) } as Response;
      });

      // Render WITHOUT latestRoutineResult — if the prop were supplied, DX63
      // already covers that path, and this test would not be measuring delivery.
      await act(async () => {
        render(
          <VehicleDiagnosticsPanel
            vehicleId={VEHICLE_ID}
            connectionStatus="connected"
            // latestRoutineResult is intentionally absent: injecting it is
            // precisely what makes DX63 unable to see F31.
          />,
        );
      });

      // Drain the fetchRoutines microtask chain.
      await act(async () => {});

      // The routines section must appear before we can click Run.
      const runBtn = await waitFor(
        () => {
          const btn = screen.getByTestId('run-routine-lamp_self_check');
          return btn;
        },
        { timeout: 3000 },
      );

      // Open the attestation modal.
      fireEvent.click(runBtn);

      await waitFor(() => {
        expect(screen.queryByRole('dialog')).not.toBeNull();
      }, { timeout: 3000 });

      // Tick the checkbox so the confirm button is enabled.
      const checkbox = screen.getByRole('checkbox');
      fireEvent.click(checkbox);
      expect(checkbox).toBeChecked();

      // Confirm — this triggers the POST.
      const confirmBtn = screen.getByRole('button', { name: /confirm|run|execute/i });
      await act(async () => {
        fireEvent.click(confirmBtn);
      });

      // Allow any microtasks / state updates from the POST response to settle.
      await act(async () => {});

      // The refusal reason from the command-row re-read MUST now be visible.
      // This assertion is the load-bearing part of F31:
      //   - At HEAD: FAILS — nothing re-reads the command row after a successful
      //     POST, so REFUSAL_REASON never appears in the DOM.
      //   - After FG3.1: PASSES — the panel polls fetchLatestSovdCommand after
      //     the POST and surfaces the reason.
      await waitFor(
        () => {
          const body = document.body.textContent ?? '';
          expect(body).toContain(REFUSAL_REASON);
        },
        { timeout: 3000 },
      );
    },
  );
});




// ---------------------------------------------------------------------------
// F33 — "Running <routineId>…" indicator + POST-error surfacing (Fix Group 4)
//
// Motivation: user UAT on 2026-09-09 clicked Execute on lamp_self_check, the
// modal closed, and the panel went silent. The invoke path was working end-
// to-end at the API layer (three DDB records with status=SENT), but the panel
// showed no feedback during the 60 s outcome poll. When the poll timed out,
// it stopped silently — no message, no state change. That is indistinguishable
// from "nothing happened" and is the eighth instance of this spec's dominant
// shape-vs-function failure pattern.
//
// These tests are mutation-per-contract:
//   - DX67 fails if `setRunningRoutineId(routineId)` is removed from
//     handleModalConfirm.
//   - DX68 fails if the latestRoutineResult useEffect is removed.
//   - DX69 fails if the POLL_MAX_MS setTimeout useEffect is removed.
//   - DX70 fails if the `catch { }` is left empty (as it originally was).
//   - DX71 fails if the SUCCEEDED positive-outcome render block is removed.
//
// Setup notes:
//   - `mockFetchRoutines` (from beforeEach in this file) is the seam the
//     panel uses to obtain the routine catalog; setting mockAuthFetch alone
//     is not sufficient because sovdScanClient.fetchRoutines is module-mocked.
//   - `vi.useRealTimers()` is called at the top of each `it` that uses
//     `waitFor` (which needs a real timer to poll). DX69 keeps fake timers
//     because that IS the point — advancing the POLL_MAX_MS ceiling.
// ---------------------------------------------------------------------------

describe('DX67 — Running indicator appears immediately when Execute is clicked', () => {
  it('shows "Running <routineId>… waiting for vehicle response." between click and terminal outcome', async () => {
    vi.useRealTimers();

    mockFetchRoutines.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ routines: [CAMEL_INERT_ROUTINE] }),
    } as Response);

    // POST resolves late. GET never resolves so the indicator stays visible
    // — that is the window this test guards.
    let resolvePost: ((r: Response) => void) | null = null;
    mockAuthFetch.mockImplementation(async (url: string, opts?: RequestInit) => {
      const method = opts?.method ?? 'GET';
      if (typeof url === 'string' && url.includes('/commands/') && method === 'POST') {
        return new Promise<Response>((res) => { resolvePost = res; });
      }
      if (typeof url === 'string' && url.includes('/commands/')) {
        // GET (fetchLatestSovdCommand) — never resolve.
        return new Promise<Response>(() => { /* hang */ });
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });

    render(<VehicleDiagnosticsPanel vehicleId="VEH-DX67" connectionStatus="connected" />);
    await act(async () => {});

    const runBtn = await waitFor(
      () => screen.getByTestId('run-routine-lamp_self_check'),
      { timeout: 3000 },
    );
    fireEvent.click(runBtn);
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeNull();
    }, { timeout: 3000 });

    fireEvent.click(screen.getByRole('checkbox'));
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /confirm|run|execute/i }));
    });
    await act(async () => {});

    // Load-bearing: indicator is visible and names the routine. If missing,
    // the operator sees exactly what the 2026-09-09 UAT saw: silence.
    await waitFor(
      () => {
        const indicator = screen.queryByTestId('routine-running-indicator');
        expect(indicator).not.toBeNull();
        expect(indicator!.textContent).toContain('Running lamp_self_check');
        expect(indicator!.textContent).toContain('waiting for vehicle response');
      },
      { timeout: 3000 },
    );

    // Let the POST resolve. Indicator MUST stay because the GET is still hung.
    resolvePost!({ ok: true, status: 200, json: async () => ({}) } as unknown as Response);
    await act(async () => {});
    expect(screen.queryByTestId('routine-running-indicator')).not.toBeNull();
  });
});

describe('DX68 — Running indicator clears when a terminal poll result arrives', () => {
  it('replaces the indicator with the refusal reason once the poll delivers a non-SUCCEEDED status', async () => {
    vi.useRealTimers();

    const REFUSAL = 'PRECONDITION_FAILED_MOVING: speed 42 mph, refuse.';
    mockFetchRoutines.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        // T3.1: Active Run buttons are only for INERT routines; use lamp_self_check.
        routines: [CAMEL_INERT_ROUTINE],
      }),
    } as Response);

    mockAuthFetch.mockImplementation(async (url: string, opts?: RequestInit) => {
      const method = opts?.method ?? 'GET';
      if (typeof url === 'string' && url.includes('/commands/')) {
        if (method === 'POST') {
          return { ok: true, status: 200, json: async () => ({ commandId: 'x' }) } as Response;
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({
            commands: [{
              commandId: 'x',
              commandType: 'run_routine',
              routineId: 'lamp_self_check',
              status: 'PRECONDITION_FAILED_MOVING',
              reason: REFUSAL,
            }],
          }),
        } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });

    render(<VehicleDiagnosticsPanel vehicleId="VEH-DX68" connectionStatus="connected" />);
    await act(async () => {});

    const runBtn = await waitFor(
      () => screen.getByTestId('run-routine-lamp_self_check'),
      { timeout: 3000 },
    );
    fireEvent.click(runBtn);
    await waitFor(() => screen.queryByRole('dialog'), { timeout: 3000 });
    fireEvent.click(screen.getByRole('checkbox'));
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /confirm|run|execute/i }));
    });
    await act(async () => {});

    // The terminal poll arrives → useEffect clears runningRoutineId → indicator
    // gone, refusal reason shown.
    await waitFor(
      () => {
        expect(screen.queryByTestId('routine-running-indicator')).toBeNull();
        expect(document.body.textContent).toContain(REFUSAL);
      },
      { timeout: 3000 },
    );
  });
});

describe('DX69 — POLL_MAX_MS ceiling clears the indicator and surfaces a timeout', () => {
  // NOTE: this test does not advance a 60 s real-time wait — it instead
  // asserts the ceiling is SCHEDULED with the right delay when the
  // indicator appears, then invokes the scheduled callback directly.
  // A full real-time 60 s wait is impractical in the suite; a mid-test
  // fake-timer swap does not affect setTimeouts already scheduled under
  // real timers (they are orphaned). This approach captures the mutation
  // contract (removing the useEffect fails this test) without the wait.
  it('schedules a 60 s ceiling that clears the indicator and renders a timeout message', async () => {
    vi.useRealTimers();

    mockFetchRoutines.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ routines: [CAMEL_INERT_ROUTINE] }),
    } as Response);

    mockAuthFetch.mockImplementation(async (url: string, opts?: RequestInit) => {
      const method = opts?.method ?? 'GET';
      if (typeof url === 'string' && url.includes('/commands/')) {
        if (method === 'POST') {
          return { ok: true, status: 200, json: async () => ({ commandId: 'x' }) } as Response;
        }
        // GET: non-terminal (SENT) forever — indicator will not clear via poll.
        return {
          ok: true,
          status: 200,
          json: async () => ({
            commands: [{
              commandId: 'x',
              commandType: 'run_routine',
              routineId: 'lamp_self_check',
              status: 'SENT',
            }],
          }),
        } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });

    // Capture setTimeout callbacks with delay >= 30 s (the ceiling useEffect
    // is the only one in this component that schedules that long).
    const originalSetTimeout = window.setTimeout;
    const captured: Array<{ delay: number; cb: () => void }> = [];
    const spy = vi.spyOn(window, 'setTimeout').mockImplementation(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ((cb: any, delay?: number, ...args: any[]) => {
        if (typeof delay === 'number' && delay >= 30_000) {
          captured.push({ delay, cb });
          // Return a fake handle; don't actually schedule (we invoke manually).
          return 0 as unknown as ReturnType<typeof setTimeout>;
        }
        return originalSetTimeout(cb, delay, ...args);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      }) as any,
    );

    try {
      render(<VehicleDiagnosticsPanel vehicleId="VEH-DX69" connectionStatus="connected" />);
      await act(async () => {});

      const runBtn = await waitFor(
        () => screen.getByTestId('run-routine-lamp_self_check'),
        { timeout: 3000 },
      );
      fireEvent.click(runBtn);
      await waitFor(() => screen.queryByRole('dialog'), { timeout: 3000 });
      fireEvent.click(screen.getByRole('checkbox'));
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /confirm|run|execute/i }));
      });
      await act(async () => {});

      // Indicator is visible right now.
      await waitFor(() => {
        expect(screen.queryByTestId('routine-running-indicator')).not.toBeNull();
      }, { timeout: 3000 });

      // Load-bearing assertion #1: the useEffect scheduled a ceiling with a
      // 60 s delay. Removing the useEffect makes this fail.
      expect(captured.length).toBeGreaterThanOrEqual(1);
      const ceiling = captured.find(c => c.delay >= 55_000 && c.delay <= 65_000);
      expect(ceiling).toBeDefined();

      // Invoke the ceiling callback manually — simulates the 60 s elapsing.
      await act(async () => { ceiling!.cb(); });

      // Load-bearing assertion #2: the callback clears the indicator AND
      // renders a timeout message. Removing setInvokeError(...) inside the
      // useEffect makes this fail.
      expect(screen.queryByTestId('routine-running-indicator')).toBeNull();
      expect(document.body.textContent).toMatch(/timed out.*60 s.*waiting for the vehicle/i);
    } finally {
      spy.mockRestore();
    }
  });
});

describe('DX70 — Network error on the run_routine POST surfaces a visible message', () => {
  it('replaces the indicator with "Network error contacting the command service." on POST throw', async () => {
    vi.useRealTimers();

    mockFetchRoutines.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ routines: [CAMEL_INERT_ROUTINE] }),
    } as Response);

    mockAuthFetch.mockImplementation(async (url: string, opts?: RequestInit) => {
      const method = opts?.method ?? 'GET';
      if (typeof url === 'string' && url.includes('/commands/') && method === 'POST') {
        throw new TypeError('Failed to fetch');
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });

    render(<VehicleDiagnosticsPanel vehicleId="VEH-DX70" connectionStatus="connected" />);
    await act(async () => {});

    const runBtn = await waitFor(
      () => screen.getByTestId('run-routine-lamp_self_check'),
      { timeout: 3000 },
    );
    fireEvent.click(runBtn);
    await waitFor(() => screen.queryByRole('dialog'), { timeout: 3000 });
    fireEvent.click(screen.getByRole('checkbox'));
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /confirm|run|execute/i }));
    });
    await act(async () => {});

    // The empty-catch behaviour would have shown nothing. The new catch MUST
    // surface a network error and clear the indicator.
    await waitFor(
      () => {
        expect(screen.queryByTestId('routine-running-indicator')).toBeNull();
        expect(document.body.textContent).toContain('Network error contacting the command service.');
      },
      { timeout: 3000 },
    );
  });
});

describe('DX71 — SUCCEEDED status renders a positive-outcome message', () => {
  it('shows "<routineId> completed successfully." when the poll returns SUCCEEDED', async () => {
    vi.useRealTimers();

    mockFetchRoutines.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ routines: [CAMEL_INERT_ROUTINE] }),
    } as Response);

    mockAuthFetch.mockImplementation(async (url: string, opts?: RequestInit) => {
      const method = opts?.method ?? 'GET';
      if (typeof url === 'string' && url.includes('/commands/')) {
        if (method === 'POST') {
          return { ok: true, status: 200, json: async () => ({ commandId: 'x' }) } as Response;
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({
            commands: [{
              commandId: 'x',
              commandType: 'run_routine',
              routineId: 'lamp_self_check',
              status: 'SUCCEEDED',
            }],
          }),
        } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });

    render(<VehicleDiagnosticsPanel vehicleId="VEH-DX71" connectionStatus="connected" />);
    await act(async () => {});

    const runBtn = await waitFor(
      () => screen.getByTestId('run-routine-lamp_self_check'),
      { timeout: 3000 },
    );
    fireEvent.click(runBtn);
    await waitFor(() => screen.queryByRole('dialog'));
    fireEvent.click(screen.getByRole('checkbox'));
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /confirm|run|execute/i }));
    });

    await waitFor(
      () => {
        expect(screen.queryByTestId('routine-success')).not.toBeNull();
        expect(document.body.textContent).toMatch(/lamp_self_check.*completed successfully/i);
      },
      { timeout: 3000 },
    );
  });
});



// ─────────────────────────────────────────────────────────────────────────────
// Session correlation on the run_routine POST
// issues/2026-09-23-diagnostics-session-log-never-populates/
//
// The panel mints a sessionId and reads its session log with a `?sessionId=`
// filter; the backend excludes rows carrying no session_id. Two stacked defects
// meant no row ever carried one:
//   1. the panel called the session-less `runRoutine` (now deleted), and
//   2. the session-aware helper sent `session_id` while the backend reads
//      `sessionId`.
//
// Consequence was NOT cosmetic: `evidence.routinesRun` on the DispatchModal is
// built FROM the session log, so every dispatched repair order reached DMS with
// zero routine evidence — the entire point of v1.5.
//
// DX60 above asserts command_type / routine_id / attestation and passed
// throughout, which is why it never caught this. These assert the session.
// ─────────────────────────────────────────────────────────────────────────────

describe('SOVD session correlation — run_routine POST carries a session', () => {
  async function invokeAndCaptureBody() {
    // The file-level beforeEach installs FAKE timers; waitFor needs real ones.
    // DX60 does the same for the same reason. Omitting this hangs the test.
    vi.useRealTimers();
    mockFetchRoutines.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ routines: [CAMEL_INERT_ROUTINE] }),
    } as Response);
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url.includes('/commands/')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ status: 'SUCCEEDED', reason: '' }),
        } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });

    await act(async () => {
      render(
        <VehicleDiagnosticsPanel vehicleId={VEHICLE_ID} connectionStatus="connected" />,
      );
    });
    await act(async () => {});

    const runBtn = await waitFor(() => screen.getByTestId('run-routine-lamp_self_check'));
    fireEvent.click(runBtn);
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeNull();
    });
    fireEvent.click(screen.getByRole('checkbox'));
    const confirmBtn = screen.getByRole('button', { name: /confirm|run|execute/i });
    await act(async () => {
      fireEvent.click(confirmBtn);
    });

    const posts = mockAuthFetch.mock.calls.filter(
      ([url, options]) =>
        typeof url === 'string' &&
        url.includes('/commands/') &&
        (options as RequestInit | undefined)?.method === 'POST',
    );
    // Anti-vacuity: if the POST never happened, every body assertion below would
    // pass trivially on an empty array. It also catches the ReferenceError shape
    // — an out-of-scope sessionId throws inside the panel's try/catch, which
    // swallows it and issues NO POST at all.
    expect(posts).toHaveLength(1);
    return JSON.parse((posts[0][1] as RequestInit).body as string);
  }

  it('sends a non-empty sessionId (camelCase — the key the backend reads)', async () => {
    const body = await invokeAndCaptureBody();

    expect(body.sessionId).toBeDefined();
    expect(typeof body.sessionId).toBe('string');
    expect((body.sessionId as string).length).toBeGreaterThan(0);
  });

  it('does NOT send the snake_case session_id the backend silently ignores', async () => {
    const body = await invokeAndCaptureBody();

    expect(body.session_id).toBeUndefined();
  });

  it('still sends command_type, routine_id and attestation alongside the session', async () => {
    const body = await invokeAndCaptureBody();

    expect(body.command_type).toBe('run_routine');
    expect(body.routine_id).toBe('lamp_self_check');
    expect(body.attestation?.text?.trim()).not.toBe('');
  });
});
