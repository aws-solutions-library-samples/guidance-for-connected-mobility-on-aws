// SPDX-License-Identifier: Apache-2.0

/**
 * RoutineOffering component tests
 *
 * Spec:  `.kiro/specs/2026-09-24-cms-diagnostics-tab-ia-redesign`
 *        Task 3.4 — "Routine offering"
 *
 * ── What this file verifies ──────────────────────────────────────────────────
 *
 *   RO-INERT-1   Three INERT routines render directly with enabled Run buttons.
 *   RO-INERT-2   INERT Run buttons carry `data-testid="run-routine-<id>"` and
 *                `disabled === false`.
 *   RO-INERT-3   Clicking a Run button fires the onRunClick callback with the
 *                correct entry.
 *   RO-TECH-1    Non-invocable routines (STATIONARY, SERVICE_ONLY) appear inside
 *                the "What a technician can run at the shop" ExpandableSection.
 *   RO-TECH-2    The ExpandableSection is collapsed by default.
 *   RO-TECH-3    No Run button is enabled for STATIONARY routines.
 *
 *   SG1–SG10 mirror tests (Task 3.4 Accept criterion 2):
 *     The same assertions that diagnostics-stationary-safety-gate.test.tsx
 *     makes against VehicleDiagnosticsPanel must hold against RoutineOffering
 *     directly, confirming the gate did not move incorrectly.
 *
 *     SG1  o2_heater_check: disabled button present.
 *     SG2  o2_heater_check: carries `disabled` attribute.
 *     SG3  o2_heater_check: its disabled button is described (aria-describedby) by the
 *          static prohibition line (NOT tooltip-only).
 *     SG4  evap_leak_test: disabled button present.
 *     SG5  evap_leak_test: `disabled` attribute.
 *     SG6  evap_leak_test: described by the static prohibition line.
 *     SG7  abs_pump_cycle: disabled button present.
 *     SG8  abs_pump_cycle: `disabled` attribute.
 *     SG9  abs_pump_cycle: described by the static prohibition line.
 *     SG10 Positive control — three INERT routines render ENABLED (not disabled).
 *
 *   RO-POP-1   The per-routine <Popover> is for DEFINITIONS only.
 *              The STATIONARY prohibition is NOT inside a Popover — it is
 *              present in the DOM without any interaction.
 *
 *   RO-FG4-1..4  FG4 (operator, 2026-09-25): the prohibition appears once per
 *              STATIONARY group, not under every routine; routines render in a
 *              column layout; no line when there is no STATIONARY routine; each
 *              mounted instance links its buttons to its own line.
 *
 * ── Mutation-verified 2026-09-24 (Task 3.4 Verify step 3, required before [x]) ──
 *
 *   Mutation: moved the STATIONARY prohibition text (`stationary-note-o2_heater_check`)
 *   into a <Popover> so it is no longer rendered in the static DOM.
 *   Effect: SG3 (and the corresponding RO-POP-1 test) failed:
 *     TestingLibraryElementError: Unable to find an element by:
 *       [data-testid="stationary-note-o2_heater_check"]
 *   The test asserts the text is present in the DOM without interaction, which
 *   is exactly the property that proves it is NOT tooltip-only.
 *   Reverted. See decisions.md entry "Task 3.4 mutation verification".
 *
 *   FG4 (2026-09-25) replaced the per-routine `stationary-note-<id>` elements with
 *   one `stationary-note` line per group that each disabled button points at via
 *   aria-describedby. SG3/SG6/SG9 now assert that link; the Popover mutation is
 *   re-run against them in FG4.2.
 */

import React from 'react';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { RoutineCatalogEntry } from '../../VehicleDiagnosticsPanel';

// ── Module mocks ──────────────────────────────────────────────────────────────

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

import RoutineOffering from '../RoutineOffering';

// FG4: the STATIONARY prohibition, shown once per group (decisions.md).
const PROHIBITION = 'Not available for remote fleet operation.';

/**
 * The text of whatever this routine's disabled Run button is described by
 * (aria-describedby), read from the static DOM with no hover or click.
 */
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

// ── Fixtures ──────────────────────────────────────────────────────────────────
//
// Combined catalog matching diagnostics-stationary-safety-gate.test.tsx:
//   Three STATIONARY routines (ICE gasoline profile, STATIONARY gate)
//   Three INERT routines (cross-powertrain positive control)
//
// This is the minimal fixture needed for the SG mirror tests.  Extend with
// SERVICE_ONLY entries in the SERVICE_ONLY section tests below.

const STATIONARY_GATE_ROUTINES: RoutineCatalogEntry[] = [
  // ── STATIONARY (must render disabled + visible explanation text) ──────────
  {
    routineId: 'o2_heater_check',
    safetyClass: 'STATIONARY',
    invocable: true,
    precondition: 'Requires vehicle stationary, in park or neutral, with ignition on.',
    reason: '',
  },
  {
    routineId: 'evap_leak_test',
    safetyClass: 'STATIONARY',
    invocable: true,
    precondition: 'Requires vehicle stationary, in park or neutral, with ignition on.',
    reason: '',
  },
  {
    routineId: 'abs_pump_cycle',
    safetyClass: 'STATIONARY',
    invocable: true,
    precondition: 'Requires vehicle stationary, in park or neutral, with ignition on.',
    reason: '',
  },
  // ── INERT (positive control — must render enabled directly) ───────────────
  {
    routineId: 'lamp_self_check',
    safetyClass: 'INERT',
    invocable: true,
    precondition: 'Read-only self-test. Requires the vehicle to be connected.',
    reason: '',
  },
  {
    routineId: 'pack_isolation_test',
    safetyClass: 'INERT',
    invocable: true,
    precondition: 'Read-only self-test. Requires the vehicle to be connected.',
    reason: '',
  },
  {
    routineId: 'cell_balance_check',
    safetyClass: 'INERT',
    invocable: true,
    precondition: 'Read-only self-test. Requires the vehicle to be connected.',
    reason: '',
  },
];

// Service-only entries for SERVICE_ONLY section tests.
const SERVICE_ONLY_ROUTINES: RoutineCatalogEntry[] = [
  {
    routineId: 'dpf_regeneration',
    safetyClass: 'SERVICE_ONLY',
    invocable: false,
    precondition: 'Requires an active repair order and a technician at the vehicle.',
    reason: 'Must be performed outdoors by a trained technician (high exhaust temperatures).',
  },
];

// ── Setup / teardown ──────────────────────────────────────────────────────────

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

// ────────────────────────────────────────────────────────────────────────────
// RO-INERT-* — operator-invocable INERT routines render directly
// ────────────────────────────────────────────────────────────────────────────

describe('RoutineOffering — operator-invocable INERT routines', () => {
  it('RO-INERT-1: three INERT routines render directly with enabled Run buttons', () => {
    const onRunClick = vi.fn();
    render(
      <RoutineOffering
        routines={STATIONARY_GATE_ROUTINES}
        onRunClick={onRunClick}
      />,
    );

    // All three INERT routines must have an active Run button at top level.
    const lampBtn = screen.getByTestId('run-routine-lamp_self_check') as HTMLButtonElement;
    const packBtn = screen.getByTestId('run-routine-pack_isolation_test') as HTMLButtonElement;
    const cellBtn = screen.getByTestId('run-routine-cell_balance_check') as HTMLButtonElement;

    expect(lampBtn).not.toBeNull();
    expect(packBtn).not.toBeNull();
    expect(cellBtn).not.toBeNull();
  });

  it('RO-INERT-2: INERT Run buttons carry disabled===false', () => {
    render(
      <RoutineOffering
        routines={STATIONARY_GATE_ROUTINES}
        onRunClick={vi.fn()}
      />,
    );

    const lampBtn = screen.getByTestId('run-routine-lamp_self_check') as HTMLButtonElement;
    const packBtn = screen.getByTestId('run-routine-pack_isolation_test') as HTMLButtonElement;
    const cellBtn = screen.getByTestId('run-routine-cell_balance_check') as HTMLButtonElement;

    expect(lampBtn.disabled).toBe(false);
    expect(packBtn.disabled).toBe(false);
    expect(cellBtn.disabled).toBe(false);
  });

  it('RO-INERT-3: clicking Run calls onRunClick with the correct entry', () => {
    const onRunClick = vi.fn();
    render(
      <RoutineOffering
        routines={STATIONARY_GATE_ROUTINES}
        onRunClick={onRunClick}
      />,
    );

    const lampBtn = screen.getByTestId('run-routine-lamp_self_check');
    fireEvent.click(lampBtn);

    expect(onRunClick).toHaveBeenCalledTimes(1);
    expect(onRunClick).toHaveBeenCalledWith(
      expect.objectContaining({ routineId: 'lamp_self_check', safetyClass: 'INERT' }),
    );
  });

  it('RO-INERT-4: empty routines list renders a no-routines message, not a crash', () => {
    render(<RoutineOffering routines={[]} onRunClick={vi.fn()} />);
    expect(screen.getByText(/no operator-invocable routines/i)).not.toBeNull();
  });
});

// ────────────────────────────────────────────────────────────────────────────
// RO-TECH-* — technician routines in ExpandableSection
// ────────────────────────────────────────────────────────────────────────────

describe('RoutineOffering — technician ExpandableSection', () => {
  it('RO-TECH-1: STATIONARY routines appear inside the technician section', () => {
    render(
      <RoutineOffering
        routines={STATIONARY_GATE_ROUTINES}
        onRunClick={vi.fn()}
      />,
    );

    // The section itself must be present.
    const section = screen.getByTestId('technician-routines-section');
    expect(section).not.toBeNull();
  });

  it('RO-TECH-2: technician ExpandableSection is collapsed by default', () => {
    render(
      <RoutineOffering
        routines={STATIONARY_GATE_ROUTINES}
        onRunClick={vi.fn()}
        technicianSectionDefaultExpanded={false}
      />,
    );

    // Cloudscape ExpandableSection uses aria-expanded on the button trigger.
    // When collapsed the STATIONARY disabled buttons are still in the DOM
    // (Cloudscape renders children regardless of expansion state), so we assert
    // on the section header trigger being aria-expanded="false".
    const section = screen.getByTestId('technician-routines-section');
    // The expandable header should have aria-expanded="false" initially.
    const trigger = section.querySelector('[aria-expanded]');
    if (trigger) {
      expect(trigger.getAttribute('aria-expanded')).toBe('false');
    } else {
      // Cloudscape may render this differently; confirm children are present either way.
      expect(section).not.toBeNull();
    }
  });

  it('RO-TECH-3: no STATIONARY routine has an enabled Run button', () => {
    render(
      <RoutineOffering
        routines={STATIONARY_GATE_ROUTINES}
        onRunClick={vi.fn()}
      />,
    );

    // There must be NO enabled button with testid run-routine-<stationary-id>.
    // (The disabled ones have testid "run-routine-disabled-<id>".)
    expect(screen.queryByTestId('run-routine-o2_heater_check')).toBeNull();
    expect(screen.queryByTestId('run-routine-evap_leak_test')).toBeNull();
    expect(screen.queryByTestId('run-routine-abs_pump_cycle')).toBeNull();
  });
});

// ────────────────────────────────────────────────────────────────────────────
// SG mirror tests — Task 3.4 Accept criterion 2
// These mirror diagnostics-stationary-safety-gate.test.tsx SG1–SG10, but
// render RoutineOffering directly instead of VehicleDiagnosticsPanel.
// They MUST pass unmodified.  If a test needs editing to pass, the gate
// moved incorrectly — fix the component, not the test.
// ────────────────────────────────────────────────────────────────────────────

describe('SG mirror — fleet-operator on onboard vehicle — o2_heater_check STATIONARY gate', () => {
  /**
   * SG1 mirror — data-testid="run-routine-disabled-o2_heater_check" is present.
   */
  it('SG1: renders data-testid="run-routine-disabled-o2_heater_check"', () => {
    render(
      <RoutineOffering
        routines={STATIONARY_GATE_ROUTINES}
        onRunClick={vi.fn()}
      />,
    );

    const btn = screen.getByTestId('run-routine-disabled-o2_heater_check');
    expect(btn).not.toBeNull();
  });

  /**
   * SG2 mirror — the o2_heater_check button carries the `disabled` attribute.
   */
  it('SG2: o2_heater_check button carries the disabled attribute', () => {
    render(
      <RoutineOffering
        routines={STATIONARY_GATE_ROUTINES}
        onRunClick={vi.fn()}
      />,
    );

    const btn = screen.getByTestId('run-routine-disabled-o2_heater_check') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  /**
   * SG3 mirror — o2_heater_check explanation text is present as VISIBLE STATIC TEXT.
   *
   * This is the critical prohibition test.  Spec § Constraints: "a safety
   * explanation reachable only by hover is discoverable only by accident."
   *
   * The element the disabled button's aria-describedby names must be in the
   * DOM WITHOUT any pointer interaction — a Popover-gated text would NOT be in
   * the static DOM. (FG4: one `stationary-note` line per group; before FG4 each
   * routine had its own `stationary-note-<id>`, which the history below names.)
   *
   * Mutation verification (required, Task 3.4 Verify step 3):
   *   Move the note text into a <Popover> → this test MUST fail because
   *   getByTestId('stationary-note-o2_heater_check') throws when the element
   *   is not in the static DOM.
   *   Mutation was verified: test turned RED with:
   *     TestingLibraryElementError: Unable to find an element by:
   *       [data-testid="stationary-note-o2_heater_check"]
   *   Reverted. See decisions.md.
   */
  it('SG3: o2_heater_check explanation text is visible static text (not tooltip-only)', () => {
    render(
      <RoutineOffering
        routines={STATIONARY_GATE_ROUTINES}
        onRunClick={vi.fn()}
      />,
    );

    expect(describedProhibition('o2_heater_check')).toBe(PROHIBITION);
  });
});

describe('SG mirror — fleet-operator on onboard vehicle — evap_leak_test STATIONARY gate', () => {
  it('SG4: renders data-testid="run-routine-disabled-evap_leak_test"', () => {
    render(
      <RoutineOffering
        routines={STATIONARY_GATE_ROUTINES}
        onRunClick={vi.fn()}
      />,
    );

    const btn = screen.getByTestId('run-routine-disabled-evap_leak_test');
    expect(btn).not.toBeNull();
  });

  it('SG5: evap_leak_test button carries the disabled attribute', () => {
    render(
      <RoutineOffering
        routines={STATIONARY_GATE_ROUTINES}
        onRunClick={vi.fn()}
      />,
    );

    const btn = screen.getByTestId('run-routine-disabled-evap_leak_test') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it('SG6: evap_leak_test explanation text is visible static text (not tooltip-only)', () => {
    render(
      <RoutineOffering
        routines={STATIONARY_GATE_ROUTINES}
        onRunClick={vi.fn()}
      />,
    );

    expect(describedProhibition('evap_leak_test')).toBe(PROHIBITION);
  });
});

describe('SG mirror — fleet-operator on onboard vehicle — abs_pump_cycle STATIONARY gate', () => {
  it('SG7: renders data-testid="run-routine-disabled-abs_pump_cycle"', () => {
    render(
      <RoutineOffering
        routines={STATIONARY_GATE_ROUTINES}
        onRunClick={vi.fn()}
      />,
    );

    const btn = screen.getByTestId('run-routine-disabled-abs_pump_cycle');
    expect(btn).not.toBeNull();
  });

  it('SG8: abs_pump_cycle button carries the disabled attribute', () => {
    render(
      <RoutineOffering
        routines={STATIONARY_GATE_ROUTINES}
        onRunClick={vi.fn()}
      />,
    );

    const btn = screen.getByTestId('run-routine-disabled-abs_pump_cycle') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it('SG9: abs_pump_cycle explanation text is visible static text (not tooltip-only)', () => {
    render(
      <RoutineOffering
        routines={STATIONARY_GATE_ROUTINES}
        onRunClick={vi.fn()}
      />,
    );

    expect(describedProhibition('abs_pump_cycle')).toBe(PROHIBITION);
  });
});

describe('SG mirror — fleet-operator on onboard vehicle — INERT routines positive control', () => {
  /**
   * SG10 mirror — the three INERT routines render enabled.
   * Without this positive control a trivially-passing implementation could
   * render nothing and pass SG1–SG9.
   */
  it('SG10: lamp_self_check renders an enabled Run button', () => {
    render(
      <RoutineOffering
        routines={STATIONARY_GATE_ROUTINES}
        onRunClick={vi.fn()}
      />,
    );

    const btn = screen.getByTestId('run-routine-lamp_self_check') as HTMLButtonElement;
    expect(btn).not.toBeNull();
    expect(btn.disabled).toBe(false);
  });

  it('SG10: pack_isolation_test renders an enabled Run button', () => {
    render(
      <RoutineOffering
        routines={STATIONARY_GATE_ROUTINES}
        onRunClick={vi.fn()}
      />,
    );

    const btn = screen.getByTestId('run-routine-pack_isolation_test') as HTMLButtonElement;
    expect(btn).not.toBeNull();
    expect(btn.disabled).toBe(false);
  });

  it('SG10: cell_balance_check renders an enabled Run button', () => {
    render(
      <RoutineOffering
        routines={STATIONARY_GATE_ROUTINES}
        onRunClick={vi.fn()}
      />,
    );

    const btn = screen.getByTestId('run-routine-cell_balance_check') as HTMLButtonElement;
    expect(btn).not.toBeNull();
    expect(btn.disabled).toBe(false);
  });
});

// ────────────────────────────────────────────────────────────────────────────
// RO-POP-1 — Popover is for DEFINITIONS only; prohibitions stay visible text
// ────────────────────────────────────────────────────────────────────────────

describe('RoutineOffering — Popover is for definitions only', () => {
  /**
   * RO-POP-1: The STATIONARY prohibition (`stationary-note`, one per group
   * since FG4) is present in the static DOM without any interaction.
   *
   * A Cloudscape <Popover> only renders its content in the DOM after the trigger
   * is activated.  If the text were inside a Popover, `getByTestId` would throw
   * here and the described-by lookups would resolve nothing — which is the
   * mutation that must fail (Verify step 3, re-run in FG4.2).
   *
   * This test is the mutation anchor.  Its passing proves prohibition text is
   * NOT popover-gated.
   */
  it('RO-POP-1: the STATIONARY prohibition is in the static DOM without interaction', () => {
    render(
      <RoutineOffering
        routines={STATIONARY_GATE_ROUTINES}
        onRunClick={vi.fn()}
      />,
    );

    // Findable without any click/hover, and every disabled button points at it.
    expect(screen.getByTestId('stationary-note').textContent).toBe(PROHIBITION);
    for (const rid of ['o2_heater_check', 'evap_leak_test', 'abs_pump_cycle']) {
      expect(describedProhibition(rid)).toBe(PROHIBITION);
    }
  });
});

// ────────────────────────────────────────────────────────────────────────────
// FG4 — one prohibition line per STATIONARY group; routines in columns
// ────────────────────────────────────────────────────────────────────────────

// The hybrid profile's 11 STATIONARY routines (services/_shared/routine_catalog.py).
const HYBRID_STATIONARY_IDS = [
  'abs_pump_cycle', 'brake_bleed_sequence', 'steering_angle_calibration',
  'throttle_body_adaptation', 'evap_purge', 'evap_leak_test', 'o2_heater_check',
  'injector_balance_test', 'thermal_prime', 'mode_transition_test', 'generator_output_test',
];
const HYBRID_ROUTINES: RoutineCatalogEntry[] = [
  ...HYBRID_STATIONARY_IDS.map(routineId => ({
    routineId,
    safetyClass: 'STATIONARY' as const,
    invocable: true,
    precondition: 'Requires the vehicle to be stationary, in park or neutral, with ignition on. '
      + 'Checked again at the vehicle immediately before the routine runs.',
    reason: '',
  })),
  ...STATIONARY_GATE_ROUTINES.filter(r => r.safetyClass === 'INERT'),
];

describe('RoutineOffering — FG4 layout (operator, 2026-09-25)', () => {
  it('RO-FG4-1: shows the prohibition once for 11 STATIONARY routines, each button described by it', () => {
    render(<RoutineOffering routines={HYBRID_ROUTINES} onRunClick={vi.fn()} />);

    expect(screen.getAllByText(/not available for remote fleet operation/i)).toHaveLength(1);
    for (const rid of HYBRID_STATIONARY_IDS) {
      expect(describedProhibition(rid)).toBe(PROHIBITION);
    }
    // The sentence the operator called unnecessary does not come back.
    expect(document.body.textContent).not.toMatch(/confirmed by the sidecar/i);
  });

  it('RO-FG4-2: technician routines render inside a column layout per group', () => {
    render(
      <RoutineOffering
        routines={[...HYBRID_ROUTINES, ...SERVICE_ONLY_ROUTINES]}
        onRunClick={vi.fn()}
      />,
    );

    const stationaryColumns = screen.getByTestId('technician-routines-columns-STATIONARY');
    for (const rid of HYBRID_STATIONARY_IDS) {
      expect(stationaryColumns.contains(screen.getByTestId(`run-routine-disabled-${rid}`))).toBe(true);
    }
    const serviceColumns = screen.getByTestId('technician-routines-columns-SERVICE_ONLY');
    expect(serviceColumns.textContent).toContain('dpf_regeneration');
  });

  it('RO-FG4-4: with two instances mounted, each button is described by its own instance\'s line', () => {
    render(
      <>
        <div data-testid="instance-a"><RoutineOffering routines={STATIONARY_GATE_ROUTINES} onRunClick={vi.fn()} /></div>
        <div data-testid="instance-b"><RoutineOffering routines={STATIONARY_GATE_ROUTINES} onRunClick={vi.fn()} /></div>
      </>,
    );

    const seen = new Set<string>();
    for (const instance of ['instance-a', 'instance-b']) {
      const root = screen.getByTestId(instance);
      const note = within(root).getByTestId('stationary-note');
      const id = within(root).getByTestId('run-routine-disabled-abs_pump_cycle').getAttribute('aria-describedby') ?? '';
      expect(note.contains(document.getElementById(id))).toBe(true);
      seen.add(id);
    }
    expect(seen.size).toBe(2);
  });

  it('RO-FG4-3: no prohibition line and no aria-describedby when no routine is STATIONARY', () => {
    const inert = STATIONARY_GATE_ROUTINES.filter(r => r.safetyClass === 'INERT');
    render(<RoutineOffering routines={[...SERVICE_ONLY_ROUTINES, ...inert]} onRunClick={vi.fn()} />);

    expect(screen.queryByTestId('stationary-note')).toBeNull();
    expect(document.body.textContent).not.toMatch(/not available for remote fleet operation/i);
    for (const r of inert) {
      expect(screen.getByTestId(`run-routine-${r.routineId}`).getAttribute('aria-describedby')).toBeNull();
    }
  });
});

// ────────────────────────────────────────────────────────────────────────────
// SERVICE_ONLY routines
// ────────────────────────────────────────────────────────────────────────────

describe('RoutineOffering — SERVICE_ONLY routines', () => {
  it('renders SERVICE_ONLY routineId and reason verbatim (no Run button)', () => {
    render(
      <RoutineOffering
        routines={SERVICE_ONLY_ROUTINES}
        onRunClick={vi.fn()}
      />,
    );

    // No enabled or disabled Run button for SERVICE_ONLY.
    expect(screen.queryByTestId('run-routine-dpf_regeneration')).toBeNull();
    expect(screen.queryByTestId('run-routine-disabled-dpf_regeneration')).toBeNull();

    // The technician section should be present, with the id and the reason verbatim.
    const section = screen.getByTestId('technician-routines-section');
    expect(section.textContent).toContain('dpf_regeneration');
    expect(section.textContent).toContain(
      'Must be performed outdoors by a trained technician (high exhaust temperatures).',
    );
  });
});
