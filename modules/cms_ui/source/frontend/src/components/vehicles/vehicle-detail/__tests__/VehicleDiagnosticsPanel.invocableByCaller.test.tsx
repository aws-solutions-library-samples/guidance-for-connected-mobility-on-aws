/**
 * VehicleDiagnosticsPanel — invocableByCaller per-caller gate (Task 2.2).
 *
 * Spec: `.kiro/specs/2026-09-14-cms-frontend-fleet-persona-alignment` Task 2.2
 *
 * The gate `(entry.invocableByCaller ?? entry.invocable) === true` was added at
 * the INERT Run button in Task 2.2.  Three cases:
 *
 *   (a) invocable: true, invocableByCaller: true  → Run button rendered
 *   (b) invocable: true, invocableByCaller: false → Run button NOT rendered
 *       (server explicitly denied this caller)
 *   (c) invocable: true, invocableByCaller: undefined → Run button rendered
 *       (backward-compat fallback to static field)
 *
 * Mutation target: reverting the gate to `entry.invocable === true` causes
 * case (b) to fail.
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Module mocks (must precede all component imports) ───────────────────────

vi.mock('@/auth/useAuth', () => ({
  useAuth: () => ({
    getAuthHeaders: () => ({ Authorization: 'Bearer test-token' }),
    user: { groups: ['fleet-operator'], roles: ['fleet-operator'] },
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

import VehicleDiagnosticsPanel from '@/components/vehicles/vehicle-detail/VehicleDiagnosticsPanel';

// ── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

// ── Helper: minimal INERT routine ────────────────────────────────────────────

const makeInertRoutine = (
  overrides: Partial<{
    invocable: boolean;
    invocableByCaller: boolean | undefined;
  }> = {},
) => ({
  routineId: 'lamp_self_check',
  safetyClass: 'INERT' as const,
  invocable: true,
  precondition: 'Read-only self-test. Requires the vehicle to be connected.',
  reason: '',
  ...overrides,
});

// ────────────────────────────────────────────────────────────────────────────
// invocableByCaller gate — three cases
// ────────────────────────────────────────────────────────────────────────────

describe('Task 2.2 — invocableByCaller per-caller Run gate', () => {
  it('(a) renders Run button when invocable: true and invocableByCaller: true', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-GATE-A"
        connectionStatus="connected"
        routines={[makeInertRoutine({ invocable: true, invocableByCaller: true })]}
      />,
    );
    expect(
      screen.getByTestId('run-routine-lamp_self_check'),
    ).toBeInTheDocument();
  });

  it('(b) does NOT render Run button when invocable: true but invocableByCaller: false', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-GATE-B"
        connectionStatus="connected"
        routines={[makeInertRoutine({ invocable: true, invocableByCaller: false })]}
      />,
    );
    // Server explicitly denied this caller; button must be absent.
    expect(
      screen.queryByTestId('run-routine-lamp_self_check'),
    ).not.toBeInTheDocument();
  });

  it('(c) renders Run button when invocable: true and invocableByCaller: undefined (backward-compat fallback)', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-GATE-C"
        connectionStatus="connected"
        routines={[makeInertRoutine({ invocable: true, invocableByCaller: undefined })]}
      />,
    );
    // undefined falls back to entry.invocable which is true.
    expect(
      screen.getByTestId('run-routine-lamp_self_check'),
    ).toBeInTheDocument();
  });
});


// ────────────────────────────────────────────────────────────────────────────
// Task 2.3 assertions — triage copy + D28 precondition preservation
// ────────────────────────────────────────────────────────────────────────────

const MIXED_CATALOG = [
  {
    routineId: 'lamp_self_check',
    safetyClass: 'INERT' as const,
    invocable: true,
    precondition: 'Read-only self-test. Requires the vehicle to be connected.',
    reason: '',
  },
  {
    routineId: 'abs_pump_cycle',
    safetyClass: 'STATIONARY' as const,
    invocable: true,
    precondition: 'Requires vehicle stationary, in park or neutral, with ignition on.',
    reason: '',
  },
  {
    routineId: 'dpf_regeneration',
    safetyClass: 'SERVICE_ONLY' as const,
    invocable: false,
    precondition: 'Requires service visit. Not available remotely.',
    reason: 'Requires verified site preconditions.',
  },
];

describe('Task 2.3 — triage copy + D28 precondition preservation', () => {
  it('renders "Handled by service at the dealership" for SERVICE_ONLY class', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TRIAGE-A"
        connectionStatus="connected"
        routines={MIXED_CATALOG}
      />,
    );
    expect(
      screen.getByText(/Handled by service at the dealership/i),
    ).toBeInTheDocument();
  });

  it('D28 preservation: server-supplied precondition text is still rendered after triage reframe', () => {
    // The precondition text belongs to the server; the triage label is
    // frontend-owned.  Both must appear — layered, not competing.
    // Mutation: removing the preconditionText secondary line would cause this
    // test to fail while the triage-label test still passed.
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TRIAGE-B"
        connectionStatus="connected"
        routines={MIXED_CATALOG}
      />,
    );
    const body = document.body.textContent ?? '';
    // Server-supplied INERT precondition text is in the Popover (not static DOM)
    // after Task 3.4 moved INERT routines to direct rendering with Popover definitions.
    // The D28 guard is still satisfied because:
    //   (a) STATIONARY precondition text IS rendered as static text in the ExpandableSection,
    //   (b) SERVICE_ONLY precondition text IS rendered as static text in the ExpandableSection.
    // The RoutineOffering component (Task 3.4) shows INERT preconditions in Popovers —
    // that is a definition (what the routine does), not a prohibition.  This is correct
    // per spec § Constraints "Tooltips split by kind": Popovers for definitions only.
    // (Task 4.1 refactoring rationale — decisions.md entry added per standing rule 2.)
    //
    // Server-supplied STATIONARY precondition text (visible in collapsed section DOM)
    expect(body).toContain('Requires vehicle stationary, in park or neutral, with ignition on.');
    // Server-supplied SERVICE_ONLY precondition text (visible in collapsed section DOM)
    expect(body).toContain('Requires service visit. Not available remotely.');
  });
});



// ────────────────────────────────────────────────────────────────────────────
// F2.1 — the SECOND INERT Run affordance honours the same gate
//
// `SuggestedRoutinesChips` renders an actionable Run chip per suggested INERT
// routine.  Before F2.1 its filter was `safetyClass === 'INERT'` alone, so a
// routine the server had denied to this caller (`invocableByCaller: false`)
// still got a live Run chip even though the main routine list correctly hid
// its Run button.  One affordance honoured the denial, the other ignored it.
//
// Escalated from security-review Cycle 3 Suggestion 1 by the architect: a
// half-applied gate is the defect Task 2.2 exists to prevent, not a cosmetic
// inconsistency.
//
// Mutation target: reverting the chips filter to `r.safetyClass === 'INERT'`
// causes the denied-chip case below to fail.
// ────────────────────────────────────────────────────────────────────────────

const makeSuggestedInert = (
  routineId: string,
  invocableByCaller: boolean | undefined,
) => ({
  routineId,
  safetyClass: 'INERT' as const,
  invocable: true,
  invocableByCaller,
  precondition: 'Read-only self-test. Requires the vehicle to be connected.',
  reason: '',
  suggestedForDtc: true,
});

describe('F2.1 — suggested-routine chips honour invocableByCaller', () => {
  it('renders an actionable chip when the server permits this caller', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-CHIP-A"
        connectionStatus="connected"
        activeDtcs={['P0420']}
        suggestedRoutinesOverride={[makeSuggestedInert('lamp_self_check', true)]}
      />,
    );
    expect(
      screen.getByTestId('suggested-chip-lamp_self_check'),
    ).toBeInTheDocument();
  });

  it('renders NO actionable chip when invocableByCaller is false — but still lists the routine', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-CHIP-B"
        connectionStatus="connected"
        activeDtcs={['P0420']}
        suggestedRoutinesOverride={[makeSuggestedInert('lamp_self_check', false)]}
      />,
    );
    // The actionable Run chip must be gone — this is the affordance that
    // bypassed the gate before F2.1.
    expect(
      screen.queryByTestId('suggested-chip-lamp_self_check'),
    ).not.toBeInTheDocument();
    // ...but the routine is still SHOWN as suggested, rendered disabled.
    // F2.1 changes what is actionable, not what is visible.
    expect(
      screen.getByTestId('suggested-chip-disabled-lamp_self_check'),
    ).toBeInTheDocument();
  });

  it('falls back to invocable when invocableByCaller is absent (backward compat)', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-CHIP-C"
        connectionStatus="connected"
        activeDtcs={['P0420']}
        suggestedRoutinesOverride={[makeSuggestedInert('lamp_self_check', undefined)]}
      />,
    );
    expect(
      screen.getByTestId('suggested-chip-lamp_self_check'),
    ).toBeInTheDocument();
  });
});
