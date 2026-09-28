// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * VehicleDiagnosticsPanel — persona-shaped render-contract matrix (T6.1).
 *
 * Spec: `.kiro/specs/2026-09-10-cms-dms-sovd-diagnostic-sessions-v1.5`
 *       Group 6, T6.1
 *
 * ⚠ SCOPE — READ THIS BEFORE TRUSTING A GREEN RUN.
 *
 * This file pins the component's **render contract against its props**. It is NOT
 * evidence of persona authorization, and only one of the three personas below is
 * a real group.
 *
 * `fleet-operator` exists. `oem-engineer` and `warranty-analyst` **do not exist**
 * anywhere in CMS production code, in the Cognito group inventory, or in
 * `services/commands/commands_lambda.py` — verified 2026-09-12 by scoped grep over
 * `modules/`, `services/`, `deployment/`, which returns hits only inside this file.
 * No token carrying either group can currently be minted.
 *
 * Consequences a reader must not gloss:
 *
 *  1. For the two fictional personas, THIS TEST SUPPLIES THE `routines` PROP. The
 *     assertions therefore discriminate the component's gate on that prop — they
 *     cannot fail for a reason connected to the persona. P7's "read-only" holds
 *     because the fixture omits `invocable`, not because `warranty-analyst` is
 *     read-only anywhere in the system.
 *
 *  2. The Run gate reads the STATIC field. `commands_lambda.py:1221` computes
 *     `invocable = safety_class !== 'SERVICE_ONLY'` (per-routine) and `:1226`
 *     computes `invocableByCaller` (per-caller). This component gates on
 *     `entry.invocable` (line ~1072) and references `invocableByCaller` zero
 *     times. For INERT the two are equal for every caller, so there is no
 *     behavioral difference today — but the field this UI reads **cannot express
 *     per-persona INERT gating**, which is what T6.1's `warranty-analyst` row
 *     describes. A future `invocableByCaller = false` would be silently ignored here.
 *
 *  3. Write authorization has no group allowlist. `_authorize_per_vin`
 *     (`commands_lambda.py:329`) admits any caller that is not driver-self, not
 *     admin, not fleet-viewer and not technician on matching `custom:fleetIds`
 *     alone. If `warranty-analyst` were created tomorrow with fleet claims it
 *     could invoke INERT routines. "Read-only" is enforced nowhere.
 *
 * Full finding + recommendation:
 * `issues/2026-09-12-diagnostics-persona-matrix-not-group-enforced/report.md`
 *
 * What the two fictional rows DO earn: P6 and P8 pin the Dispatch gate
 * (`callerGroups` must include `fleet-operator` or `platform-admin`), which is a
 * real control on a real prop. Mutation M6 — replacing that gate with `true` —
 * turns both RED, so the gate is genuinely pinned. Treat these rows as
 * "non-qualifying group" cases, which is what they actually test, rather than as
 * named-persona authorization cases.
 *
 * ── Render contract under test ───────────────────────────────────────────────
 *
 *   fleet-operator  — INERT invocable Run button rendered (active), STATIONARY
 *                     Run button rendered disabled with the explanatory note,
 *                     Dispatch button rendered (vin + callerGroups gate satisfied).
 *   oem-engineer    — (non-qualifying group) INERT invocable Run button rendered
 *                     (active), NO Dispatch button.
 *   warranty-analyst — (non-qualifying group, fixture withholds `invocable`) no
 *                     Run button for any routine, no Dispatch button.
 *
 * Cognito claims are injected via the callerGroups prop at the component boundary.
 * The panel does not call useAuth() directly; RunRoutineModal and DispatchModal do.
 * Both are mocked out below so tests render without a live auth provider.
 *
 * "Tooltips" in T6.1 Accept: VehicleDiagnosticsPanel renders no HTML tooltip or
 * aria-describedby on buttons. The STATIONARY explanatory note — visible paragraph
 * text rendered below the disabled button — is the equivalent attestation surface;
 * all assertions use that text verbatim.
 *
 * "Network calls issued": assertions are structural (button present / absent /
 * disabled) rather than click-through because the invoke path requires a modal
 * interaction (RunRoutineModal).  The structural assertions are the precise
 * discriminator: an enabled Run button means the component would issue a
 * runRoutine call on confirmation; a disabled or absent button means it cannot.
 * runRoutine is additionally asserted NOT called in the warranty-analyst case to
 * pin that the render path does not invoke the function at mount time.
 *
 * ── Test ID map ─────────────────────────────────────────────────────────────
 *
 * P1   fleet-operator: INERT invocable Run button is present and NOT disabled.
 * P2   fleet-operator: STATIONARY Run button is present and IS disabled.
 * P3   fleet-operator: STATIONARY explanatory note renders the exact component text.
 * P4   fleet-operator: Dispatch button is present (callerGroups + vin gate both satisfied).
 * P5   oem-engineer: INERT invocable Run button is present and NOT disabled.
 * P6   oem-engineer: Dispatch button is absent (callerGroups contains no qualifying role).
 * P7   warranty-analyst: No Run button for any routine (invocable absent from all entries).
 * P8   warranty-analyst: Dispatch button is absent.
 * P9   warranty-analyst: runRoutine is not called at mount time (network-call guard).
 *
 * Each test name states the property it discriminates.  Every assertion reads the
 * property that the test name claims, not just the presence of a related element.
 *
 * Mutation-verified 2026-09-12 (T6.5 discipline): M6 (Dispatch gate → `true`) and
 * M7 (STATIONARY note text altered) each turn this suite RED. Both reverted.
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Module mocks (must precede component imports) ────────────────────────────

// useAuth is consumed by RunRoutineModal and DispatchModal (both mounted inside
// VehicleDiagnosticsPanel).  The panel itself does not call useAuth.
vi.mock('@/auth/useAuth', () => ({
  useAuth: () => ({
    getAuthHeaders: () => ({ Authorization: 'Bearer test-token' }),
    user: {
      username: 'operator@example.com',
      email: 'operator@example.com',
      groups: [],
      roles: [],
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

// Mock all sovdScanClient exports.  fetchRoutines and fetchSessionCommands are
// bypassed in these tests via prop seeding (routines, sessionCommands,
// priorSessionCommands), so only runRoutine needs call-count assertions.
// The spread preserves URL-helper exports (commandsUrl, etc.) so TypeScript
// import resolution stays valid.
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

// useDealerOptions is consumed by DispatchModal.
vi.mock('@/hooks/useDealerOptions', () => ({
  useDealerOptions: vi.fn(() => ({
    options: [
      { value: 'dealer-denver', label: 'Meridian of Denver' },
    ],
    loading: false,
    error: undefined,
  })),
  // dealerPlaceholder is called as a function by DispatchModal (e.g., dealerPlaceholder(status)).
  // Must be a vi.fn() returning a string (matching the real signature), not a plain object.
  dealerPlaceholder: vi.fn((_status: string) => 'Select a dealership'),
}));

import * as sovdScanClientModule from '@/utils/sovdScanClient';
import VehicleDiagnosticsPanel from '@/components/vehicles/vehicle-detail/VehicleDiagnosticsPanel';

const mockRunRoutine = vi.mocked(sovdScanClientModule.runRoutine);

// ── Fixtures ──────────────────────────────────────────────────────────────────
//
// Routine catalog used for fleet-operator and oem-engineer personas.
// Contains one INERT-invocable entry and one STATIONARY entry so both
// persona-specific assertions can be made in a single render.
//
// Strings are sourced from the live catalog in
// `services/_shared/routine_catalog.py` (verified in Group 1 research).

const SHARED_ROUTINES = [
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
] as const;

// warranty-analyst receives routines where invocable is absent (or false) so
// no Run button is rendered.  Using a distinct fixture makes the assertion's
// meaning unambiguous: we are testing the no-invocable path, not just the
// callerGroups path.
const READONLY_ROUTINES = [
  {
    routineId: 'lamp_self_check',
    safetyClass: 'INERT' as const,
    invocable: false,   // D27 fail-closed: absent or false → no Run button
    precondition: 'Read-only self-test. Requires the vehicle to be connected.',
    reason: '',
  },
] as const;

// Empty session props bypass the fetchSessionCommands / fetchAllSovdCommands
// network calls at mount time (the panel short-circuits the fetch when the
// prop override is present).
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
// fleet-operator persona
// ────────────────────────────────────────────────────────────────────────────

describe('fleet-operator persona', () => {
  /**
   * P1 — INERT invocable Run button is present and NOT disabled.
   *
   * The component renders a Run button for each INERT entry where
   * invocable === true (data-testid="run-routine-<routineId>").
   * This test reads the aria-disabled attribute of that specific element so
   * the property named in the test name (not disabled) is what is checked.
   */
  it('P1: INERT invocable routine Run button is present and NOT disabled', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-MICH-001"
        connectionStatus="connected"
        callerGroups={['fleet-operator']}
        vin="ACME0000000000001"
        routines={SHARED_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    const runBtn = screen.getByTestId('run-routine-lamp_self_check');
    // The button must be present (previous line would throw if absent).
    // aria-disabled="true" or the disabled attribute being truthy would mean
    // the operator cannot issue the command — assert neither is set.
    expect(runBtn).not.toHaveAttribute('aria-disabled', 'true');
    expect(runBtn).not.toBeDisabled();
  });

  /**
   * P2 — STATIONARY Run button is present and IS disabled.
   *
   * The component renders a disabled button for STATIONARY entries
   * (data-testid="run-routine-disabled-<routineId>").  The Cloudscape Button
   * `disabled` prop sets the underlying HTML button's disabled attribute.
   * This test reads disabled directly — the naming difference (disabled- prefix)
   * is a proxy, but the assertion is on the actual disabled property.
   */
  it('P2: STATIONARY routine Run button is present and IS disabled', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-MICH-001"
        connectionStatus="connected"
        callerGroups={['fleet-operator']}
        vin="ACME0000000000001"
        routines={SHARED_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    const disabledBtn = screen.getByTestId('run-routine-disabled-abs_pump_cycle');
    expect(disabledBtn).toBeDisabled();
  });

  /**
   * P3 — STATIONARY prohibition renders the exact component text, once.
   *
   * FG4 (operator, 2026-09-25): the line appears once for the STATIONARY group
   * (data-testid="stationary-note"), not under each routine, and each disabled
   * button points at it with aria-describedby. Exact text, so any copy edit
   * that softens or adds to it fails immediately.
   *
   * Quoted from RoutineOffering.tsx: "Not available for remote fleet operation."
   */
  it('P3: STATIONARY prohibition renders the exact text from the component, once', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-MICH-001"
        connectionStatus="connected"
        callerGroups={['fleet-operator']}
        vin="ACME0000000000001"
        routines={SHARED_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    const note = screen.getByTestId('stationary-note');
    // Assert the exact text the component renders — do not paraphrase.
    expect(note.textContent).toBe('Not available for remote fleet operation.');
    expect(screen.getAllByText(/not available for remote fleet operation/i)).toHaveLength(1);
    const describedBy = screen.getByTestId('run-routine-disabled-abs_pump_cycle').getAttribute('aria-describedby');
    expect(describedBy && note.contains(document.getElementById(describedBy))).toBe(true);
  });

  /**
   * P4 — Dispatch button is present.
   *
   * The component renders the Dispatch button only when BOTH conditions hold:
   *   (a) callerGroups includes 'fleet-operator' or 'platform-admin'
   *   (b) vin prop is provided
   * This test satisfies both and asserts the button is in the document.
   * data-testid="dispatch-to-service-button" is the discriminating attribute.
   */
  it('P4: Dispatch button is visible (callerGroups=fleet-operator, vin present)', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-MICH-001"
        connectionStatus="connected"
        callerGroups={['fleet-operator']}
        vin="ACME0000000000001"
        routines={SHARED_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    // toBeInTheDocument asserts presence; if absent the component's callerGroups
    // gate has failed and the test correctly flags it.
    const dispatchBtn = screen.getByTestId('dispatch-to-service-button');
    expect(dispatchBtn).toBeInTheDocument();
  });
});

// ────────────────────────────────────────────────────────────────────────────
// oem-engineer persona
// ────────────────────────────────────────────────────────────────────────────

describe('oem-engineer persona', () => {
  /**
   * P5 — INERT invocable Run button is present and NOT disabled.
   *
   * The component applies no persona gate to the Run button for INERT routines.
   * An OEM engineer with callerGroups=['oem-engineer'] receives the same INERT
   * affordance as a fleet-operator — the component's only gate is invocable===true.
   * This test discriminates the enabled state (not the presence alone).
   */
  it('P5: INERT invocable routine Run button is present and NOT disabled', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-MRDN-0015"
        connectionStatus="connected"
        callerGroups={['oem-engineer']}
        vin="MRDN0000000000011"
        routines={SHARED_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    const runBtn = screen.getByTestId('run-routine-lamp_self_check');
    expect(runBtn).not.toHaveAttribute('aria-disabled', 'true');
    expect(runBtn).not.toBeDisabled();
  });

  /**
   * P6 — Dispatch button is absent.
   *
   * callerGroups=['oem-engineer'] satisfies neither 'fleet-operator' nor
   * 'platform-admin', so canDispatch is false and the Dispatch button must
   * not be rendered.  queryByTestId returns null if absent — this is the
   * negative control for P4.
   */
  it('P6: Dispatch button is absent (callerGroups=oem-engineer lacks qualifying role)', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-MRDN-0015"
        connectionStatus="connected"
        callerGroups={['oem-engineer']}
        vin="MRDN0000000000011"
        routines={SHARED_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    // queryByTestId returns null when the element is absent; this assertion
    // checks that the callerGroups gate excluded the oem-engineer from dispatch.
    const dispatchBtn = screen.queryByTestId('dispatch-to-service-button');
    expect(dispatchBtn).toBeNull();
  });
});

// ────────────────────────────────────────────────────────────────────────────
// warranty-analyst persona
// ────────────────────────────────────────────────────────────────────────────

describe('warranty-analyst persona', () => {
  /**
   * P7 — No Run button for any routine (invocable absent from all entries).
   *
   * warranty-analyst is a read-only persona.  The component renders no Run
   * button when invocable is false (D27 fail-closed).  READONLY_ROUTINES
   * carries invocable=false for the single INERT entry, so neither
   * data-testid="run-routine-*" nor data-testid="run-routine-disabled-*"
   * should appear in the DOM.
   *
   * This assertion discriminates the property named in the test name: the
   * Run button is absent, not just disabled — the component must not render
   * the affordance at all.
   */
  it('P7: No Run button rendered for any routine (invocable=false on all entries)', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-ENT-001"
        connectionStatus="connected"
        callerGroups={['warranty-analyst']}
        vin="WPR00000000000002"
        routines={READONLY_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    // Neither the active Run button (INERT) nor the disabled Run button (STATIONARY)
    // should be present for any routine in the catalog.
    const activeRunBtn = screen.queryByTestId('run-routine-lamp_self_check');
    const disabledRunBtn = screen.queryByTestId('run-routine-disabled-lamp_self_check');

    expect(activeRunBtn).toBeNull();
    expect(disabledRunBtn).toBeNull();
  });

  /**
   * P8 — Dispatch button is absent.
   *
   * callerGroups=['warranty-analyst'] is not 'fleet-operator' or 'platform-admin',
   * so the component's canDispatch gate is false.  Negative control for P4.
   */
  it('P8: Dispatch button is absent (callerGroups=warranty-analyst lacks qualifying role)', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-ENT-001"
        connectionStatus="connected"
        callerGroups={['warranty-analyst']}
        vin="WPR00000000000002"
        routines={READONLY_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    const dispatchBtn = screen.queryByTestId('dispatch-to-service-button');
    expect(dispatchBtn).toBeNull();
  });

  /**
   * P9 — runRoutine is not called at mount time (network-call guard).
   *
   * The component must not issue a routine invocation at mount; runRoutine is
   * only called when the operator confirms the attestation modal after clicking
   * the Run button.  For the warranty-analyst persona no Run button is rendered,
   * so no path to runRoutine exists from this render.
   *
   * This is the explicit network-call assertion T6.1 Accept requires.
   * Discriminates mount-time behaviour specifically — not click-through.
   */
  it('P9: runRoutine is not called at mount time', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-ENT-001"
        connectionStatus="connected"
        callerGroups={['warranty-analyst']}
        vin="WPR00000000000002"
        routines={READONLY_ROUTINES}
        {...NO_SESSIONS}
      />,
    );

    expect(mockRunRoutine).not.toHaveBeenCalled();
  });
});
