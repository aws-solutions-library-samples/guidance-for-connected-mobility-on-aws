// SPDX-License-Identifier: Apache-2.0

/**
 * STATIONARY safety-gate tests — hardened guards for the fleet-operator persona.
 *
 * Spec:  `.kiro/specs/2026-09-24-cms-diagnostics-tab-ia-redesign`
 *        Task 1.2 — "Harden the STATIONARY safety-gate tests, and mutation-verify
 *        them"
 *
 * Background:
 *   The UI gate (the `cls === 'STATIONARY'` branch of `diagnostics/RoutineOffering.tsx`,
 *   which the panel renders; it moved there in Task 3.4) is the ONLY enforcement
 *   point preventing a fleet-operator from invoking a STATIONARY routine.
 *   The Lambda has no STATIONARY-specific gate for this persona — see
 *   `issues/2026-09-12-diagnostics-persona-matrix-not-group-enforced/report.md`.
 *   A STATIONARY routine becoming clickable is therefore a **safety regression**,
 *   not a UI bug.
 *
 * Scope:
 *   Asserts that, on an onboard vehicle, a fleet-operator persona sees each of
 *   the three ICE gasoline STATIONARY routines disabled with visible static
 *   explanation text — NOT a hover-only tooltip (per 2026-09-24 operator finding).
 *   Also asserts the three INERT routines DO render enabled so the suite cannot
 *   pass trivially by everything being disabled.
 *
 * ── Test ID map ─────────────────────────────────────────────────────────────
 *
 *  SG1  o2_heater_check: disabled button present (data-testid="run-routine-disabled-o2_heater_check").
 *  SG2  o2_heater_check: carries the `disabled` attribute.
 *  SG3  o2_heater_check: its disabled button is described (aria-describedby) by the
 *       prohibition, present in the DOM as visible text, NOT tooltip-only.
 *  SG4  evap_leak_test: disabled button present.
 *  SG5  evap_leak_test: carries the `disabled` attribute.
 *  SG6  evap_leak_test: described by the visible static prohibition.
 *  SG7  abs_pump_cycle: disabled button present.
 *  SG8  abs_pump_cycle: carries the `disabled` attribute.
 *  SG9  abs_pump_cycle: described by the visible static prohibition.
 * SG10  Positive control — three INERT routines render enabled (not disabled).
 *       Prevents a trivially-passing suite where all routines are disabled.
 *
 * Mutation-verified 2026-09-24 (task requirement):
 *   Mutation: removed the `cls === 'STATIONARY'` gate for `o2_heater_check` so
 *   it rendered as INERT (enabled). Tests SG1, SG2, SG3 turned RED (named
 *   "o2_heater_check"). Reverted. See decisions.md.
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { RoutineCatalogEntry } from '@/components/vehicles/vehicle-detail/VehicleDiagnosticsPanel';

// ── Module mocks (must precede component imports) ────────────────────────────

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
    startFullScan: vi.fn(),
    fetchLatestSovdCommand: vi.fn(),
    fetchRoutines: vi.fn(),
    runRoutine: vi.fn(),
    runRoutineInSession: vi.fn(),
    fetchSessionCommands: vi.fn(),
    fetchAllSovdCommands: vi.fn(),
    fetchDtcSuggestedRoutines: vi.fn(),
  };
});

vi.mock('@/hooks/useDealerOptions', () => ({
  useDealerOptions: vi.fn(() => ({
    options: [{ value: 'dealer-denver', label: 'Meridian of Denver' }],
    loading: false,
    error: undefined,
  })),
  dealerPlaceholder: vi.fn((_status: string) => 'Select a dealership'),
}));

import VehicleDiagnosticsPanel from '@/components/vehicles/vehicle-detail/VehicleDiagnosticsPanel';

// FG4 (operator, 2026-09-25): the STATIONARY prohibition is one line per
// group, and each disabled button points at it with aria-describedby.
const PROHIBITION = 'Not available for remote fleet operation.';

/** Text of whatever this routine's disabled Run button is described by, from the static DOM. */
function describedProhibition(routineId: string): string {
  const btn = screen.getByTestId(`run-routine-disabled-${routineId}`);
  const ids = (btn.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean);
  expect(ids).not.toHaveLength(0);
  // Every target must be the visible group line. Cloudscape's `disabledReason`
  // tooltip also renders a static, hidden description element, so text alone
  // cannot tell the two apart (review FG4 cycle 1, W1).
  const note = screen.getByTestId('stationary-note');
  const targets = ids.map(id => document.getElementById(id));
  for (const el of targets) {
    expect(el).not.toBeNull();
    expect(note.contains(el)).toBe(true);
    expect(el!.closest('[hidden]')).toBeNull();
    // Also rules out CSS hiding (display:none, visibility:hidden), review FG4 cycle 2.
    expect(el).toBeVisible();
  }
  return targets.map(el => el!.textContent ?? '').join(' ').trim();
}

// ── Fixture ──────────────────────────────────────────────────────────────────
//
// Combined catalog: three STATIONARY routines (ICE gasoline profile) + three
// INERT routines (cross-powertrain and EV, drawn from routine_catalog.py).
//
// STATIONARY entries are sourced from `services/_shared/routine_catalog.py`:
//   o2_heater_check:  Classification: STATIONARY.
//   evap_leak_test:   Classification: STATIONARY.
//   abs_pump_cycle:   Classification: STATIONARY.
//
// INERT entries (positive control — must render enabled):
//   lamp_self_check:      Classification: INERT. (cross-powertrain)
//   pack_isolation_test:  Classification: INERT. (EV/hybrid)
//   cell_balance_check:   Classification: INERT. (EV/hybrid)
//
// The precondition strings are sourced verbatim from the catalog's _r() calls
// which inherit the class-level precondition: "Requires vehicle stationary, in
// park or neutral, with ignition on." for STATIONARY, and
// "Read-only self-test. Requires the vehicle to be connected." for INERT.

const STATIONARY_GATE_ROUTINES: RoutineCatalogEntry[] = [
  // ── STATIONARY (must render disabled + visible explanation text) ──
  {
    routineId: 'o2_heater_check',
    safetyClass: 'STATIONARY',
    invocable: true,
    precondition:
      'Requires vehicle stationary, in park or neutral, with ignition on.',
    reason: '',
  },
  {
    routineId: 'evap_leak_test',
    safetyClass: 'STATIONARY',
    invocable: true,
    precondition:
      'Requires vehicle stationary, in park or neutral, with ignition on.',
    reason: '',
  },
  {
    routineId: 'abs_pump_cycle',
    safetyClass: 'STATIONARY',
    invocable: true,
    precondition:
      'Requires vehicle stationary, in park or neutral, with ignition on.',
    reason: '',
  },
  // ── INERT (positive control — must render enabled) ──────────────────
  {
    routineId: 'lamp_self_check',
    safetyClass: 'INERT',
    invocable: true,
    precondition:
      'Read-only self-test. Requires the vehicle to be connected.',
    reason: '',
  },
  {
    routineId: 'pack_isolation_test',
    safetyClass: 'INERT',
    invocable: true,
    precondition:
      'Read-only self-test. Requires the vehicle to be connected.',
    reason: '',
  },
  {
    routineId: 'cell_balance_check',
    safetyClass: 'INERT',
    invocable: true,
    precondition:
      'Read-only self-test. Requires the vehicle to be connected.',
    reason: '',
  },
];

// Session prop overrides bypass network calls at mount time.
const NO_SESSIONS = { sessionCommands: [], priorSessionCommands: [] };

// ── Shared setup / teardown ───────────────────────────────────────────────────

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

// ────────────────────────────────────────────────────────────────────────────
// SG1–SG3 — o2_heater_check: disabled + visible explanation text
// ────────────────────────────────────────────────────────────────────────────

describe('fleet-operator on onboard vehicle — o2_heater_check STATIONARY gate', () => {
  /**
   * SG1 — data-testid="run-routine-disabled-o2_heater_check" is present in the DOM.
   *
   * The component renders a disabled button element with this specific testid
   * for every STATIONARY routine in the fleet-operator view.  If absent, the
   * gate logic has lost track of the routine entirely.
   *
   * `getByTestId` throws if the element is absent — so a successful return is
   * the assertion.  We additionally confirm the element is not null.
   */
  it('SG1: renders data-testid="run-routine-disabled-o2_heater_check"', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-GAS-001"
        connectionStatus="connected"
        callerGroups={['fleet-operator']}
        vin="GAS0000000000001"
        routines={STATIONARY_GATE_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    // getByTestId throws (and fails the test) if the element is absent.
    const btn = screen.getByTestId('run-routine-disabled-o2_heater_check');
    expect(btn).not.toBeNull();
  });

  /**
   * SG2 — the o2_heater_check button carries the `disabled` attribute.
   *
   * Presence alone (SG1) is necessary but not sufficient: an enabled button
   * with the wrong testid is a gate failure.  This assertion reads the actual
   * disabled property from the HTML button element.
   */
  it('SG2: o2_heater_check button carries the disabled attribute', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-GAS-001"
        connectionStatus="connected"
        callerGroups={['fleet-operator']}
        vin="GAS0000000000001"
        routines={STATIONARY_GATE_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    const btn = screen.getByTestId('run-routine-disabled-o2_heater_check') as HTMLButtonElement;
    // The HTML button's `disabled` property must be true — not just aria-disabled.
    expect(btn.disabled).toBe(true);
  });

  /**
   * SG3 — the explanation text for o2_heater_check is present as visible
   * static text in the DOM, NOT a hover-only tooltip.
   *
   * Spec § Constraints (2026-09-24 operator finding): "a safety explanation
   * reachable only by hover is discoverable only by accident."  The text must
   * be in the DOM without any pointer interaction: the element the disabled
   * button's aria-describedby names must exist on static render and carry the
   * prohibition's exact text (FG4: one line per group, not one per routine).
   *
   * The Cloudscape `<Popover>` pattern renders trigger-only text until hover/
   * focus, so resolving the described-by element on static render proves the
   * text is NOT popover-gated.
   */
  it('SG3: o2_heater_check explanation text is visible static text (not tooltip-only)', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-GAS-001"
        connectionStatus="connected"
        callerGroups={['fleet-operator']}
        vin="GAS0000000000001"
        routines={STATIONARY_GATE_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    // The button must be described by text that is in the DOM without
    // interaction, and that text must be the prohibition itself (exact text,
    // so an added or softened claim fails). FG4: one line per STATIONARY
    // group, linked from each disabled button by aria-describedby.
    expect(describedProhibition('o2_heater_check')).toBe(PROHIBITION);
  });
});

// ────────────────────────────────────────────────────────────────────────────
// SG4–SG6 — evap_leak_test: disabled + visible explanation text
// ────────────────────────────────────────────────────────────────────────────

describe('fleet-operator on onboard vehicle — evap_leak_test STATIONARY gate', () => {
  /**
   * SG4 — data-testid="run-routine-disabled-evap_leak_test" is present in the DOM.
   */
  it('SG4: renders data-testid="run-routine-disabled-evap_leak_test"', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-GAS-001"
        connectionStatus="connected"
        callerGroups={['fleet-operator']}
        vin="GAS0000000000001"
        routines={STATIONARY_GATE_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    const btn = screen.getByTestId('run-routine-disabled-evap_leak_test');
    expect(btn).not.toBeNull();
  });

  /**
   * SG5 — the evap_leak_test button carries the `disabled` attribute.
   */
  it('SG5: evap_leak_test button carries the disabled attribute', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-GAS-001"
        connectionStatus="connected"
        callerGroups={['fleet-operator']}
        vin="GAS0000000000001"
        routines={STATIONARY_GATE_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    const btn = screen.getByTestId('run-routine-disabled-evap_leak_test') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  /**
   * SG6 — the explanation text for evap_leak_test is present as visible static
   * text, not a tooltip.
   */
  it('SG6: evap_leak_test explanation text is visible static text (not tooltip-only)', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-GAS-001"
        connectionStatus="connected"
        callerGroups={['fleet-operator']}
        vin="GAS0000000000001"
        routines={STATIONARY_GATE_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    expect(describedProhibition('evap_leak_test')).toBe(PROHIBITION);
  });
});

// ────────────────────────────────────────────────────────────────────────────
// SG7–SG9 — abs_pump_cycle: disabled + visible explanation text
// ────────────────────────────────────────────────────────────────────────────

describe('fleet-operator on onboard vehicle — abs_pump_cycle STATIONARY gate', () => {
  /**
   * SG7 — data-testid="run-routine-disabled-abs_pump_cycle" is present in the DOM.
   *
   * Note: this routine is ALSO present in DiagnosticsTab.persona.test.tsx (P2).
   * Both suites guard the same gate from different angles — this file guards all
   * three STATIONARY routines as a set; the persona file guards the
   * fleet-operator persona matrix.  They are complementary, not redundant.
   */
  it('SG7: renders data-testid="run-routine-disabled-abs_pump_cycle"', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-GAS-001"
        connectionStatus="connected"
        callerGroups={['fleet-operator']}
        vin="GAS0000000000001"
        routines={STATIONARY_GATE_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    const btn = screen.getByTestId('run-routine-disabled-abs_pump_cycle');
    expect(btn).not.toBeNull();
  });

  /**
   * SG8 — the abs_pump_cycle button carries the `disabled` attribute.
   */
  it('SG8: abs_pump_cycle button carries the disabled attribute', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-GAS-001"
        connectionStatus="connected"
        callerGroups={['fleet-operator']}
        vin="GAS0000000000001"
        routines={STATIONARY_GATE_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    const btn = screen.getByTestId('run-routine-disabled-abs_pump_cycle') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  /**
   * SG9 — the explanation text for abs_pump_cycle is present as visible static
   * text, not a tooltip.
   */
  it('SG9: abs_pump_cycle explanation text is visible static text (not tooltip-only)', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-GAS-001"
        connectionStatus="connected"
        callerGroups={['fleet-operator']}
        vin="GAS0000000000001"
        routines={STATIONARY_GATE_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    expect(describedProhibition('abs_pump_cycle')).toBe(PROHIBITION);
  });
});

// ────────────────────────────────────────────────────────────────────────────
// SG10 — positive control: three INERT routines render enabled
// ────────────────────────────────────────────────────────────────────────────

describe('fleet-operator on onboard vehicle — INERT routines positive control', () => {
  /**
   * SG10 — each of the three INERT routines renders an enabled Run button.
   *
   * Without this positive control, a trivially-passing implementation could
   * render no routines at all (or disable everything) and pass SG1–SG9.
   * This asserts that:
   *   (a) lamp_self_check, pack_isolation_test, cell_balance_check each has
   *       an enabled Run button (data-testid="run-routine-<id>").
   *   (b) None of those buttons has `disabled === true`.
   *
   * This is the "positive control" SG10 in the test ID map.
   */
  it('SG10: lamp_self_check renders an enabled Run button', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-GAS-001"
        connectionStatus="connected"
        callerGroups={['fleet-operator']}
        vin="GAS0000000000001"
        routines={STATIONARY_GATE_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    const btn = screen.getByTestId('run-routine-lamp_self_check') as HTMLButtonElement;
    expect(btn).not.toBeNull();
    expect(btn.disabled).toBe(false);
  });

  it('SG10: pack_isolation_test renders an enabled Run button', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-GAS-001"
        connectionStatus="connected"
        callerGroups={['fleet-operator']}
        vin="GAS0000000000001"
        routines={STATIONARY_GATE_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    const btn = screen.getByTestId('run-routine-pack_isolation_test') as HTMLButtonElement;
    expect(btn).not.toBeNull();
    expect(btn.disabled).toBe(false);
  });

  it('SG10: cell_balance_check renders an enabled Run button', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-GAS-001"
        connectionStatus="connected"
        callerGroups={['fleet-operator']}
        vin="GAS0000000000001"
        routines={STATIONARY_GATE_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    const btn = screen.getByTestId('run-routine-cell_balance_check') as HTMLButtonElement;
    expect(btn).not.toBeNull();
    expect(btn.disabled).toBe(false);
  });
});
