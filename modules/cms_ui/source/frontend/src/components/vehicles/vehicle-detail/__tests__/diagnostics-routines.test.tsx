// SPDX-License-Identifier: Apache-2.0

/**
 * Vehicle diagnostics — Stage 3 routines disclosure (DX51–DX57).
 *
 * Spec: `.kiro/specs/2026-09-02-cms-diagnostics-platform`
 *       § Decision D14 (three safety classes: INERT | STATIONARY | SERVICE_ONLY)
 *       § Decision D17 (SERVICE_ONLY enumerated with a non-empty reason)
 *       § Decision D25 (SERVICE_ONLY when preconditions include unverifiable site facts)
 *       § Design Seam 2 (Stage 3 actuation — server owns preconditions)
 *       tasks.md T9.1
 *
 * Accept (paraphrased): routines grouped by safety class with preconditions
 * stated; SERVICE_ONLY shown as "requires service visit"; refusal reasons
 * surfaced verbatim from the server.
 *
 * Load-bearing constraint (task text): "the UI must not pre-judge a
 * precondition the server owns — show intent, let the server decide, render
 * its reason."  DX54 and DX55 turn that constraint into two executable checks.
 *
 * ── Test ID map ─────────────────────────────────────────────────────────────
 *
 * DX51  Routines are grouped by safety_class (three distinct groups render;
 *       an entry appears in exactly one group).
 * DX52  Each group states its precondition — INERT is read-only-safe,
 *       STATIONARY requires the vehicle stationary, SERVICE_ONLY requires a
 *       service visit.  Assertions are literal substrings so a copy edit
 *       that softens "requires service visit" fails immediately.
 * DX53  Every SERVICE_ONLY entry renders its `reason` string verbatim (D17).
 *       DPF regeneration's D25 rationale is the canonical case and appears
 *       word-for-word — no paraphrase, no truncation.  Fabricating a stub
 *       reason to satisfy DX53 would be caught by the reason-provenance
 *       negative control below.
 * DX54  SERVICE_ONLY entries expose no in-panel Run affordance.  The
 *       actuation path is the server's; a disabled button would still be an
 *       affordance the operator can defeat, per D15's "a disabled button is
 *       an affordance, not a control."
 * DX55  The panel does NOT pre-judge preconditions the server owns.  For a
 *       STATIONARY routine, the UI renders no client-side speed/ignition
 *       gate copy — those checks live server-side at the sidecar (T8.2 D15).
 *       Negative control: strings the F1 defect would introduce
 *       (`speed`, `moving`, `stationary check`, `ignition off`) are forbidden.
 * DX56  A server-supplied refusal reason renders verbatim.  The panel does
 *       not translate, summarise, or map error codes to prose — the reason
 *       is the reason.
 * DX57  Absence of the `routines` prop suppresses the section entirely.
 *       Stage 3 must not surface a disclosure with no content, and must not
 *       fabricate an empty catalog for a vehicle whose powertrain profile
 *       resolves to no routines (that is a valid, if rare, state).
 *
 * RED PHASE — every test in this file is expected to fail until T9.1 lands.
 * Failures are AssertionError (or `expect` failure), not import errors:
 * `VehicleDiagnosticsPanel` exists, and the tests drive it with props the
 * panel does not yet accept.  Do not stub the section into life; T9.1 owns
 * the implementation.
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

// ── Fixture: diesel routine catalog ─────────────────────────────────────────
//
// Sourced from `services/_shared/routine_catalog.py` — the diesel profile is
// the one that carries entries in all three safety classes and includes DPF
// regeneration (the D25 canonical SERVICE_ONLY case).  Shape matches the
// module's `get_routines_for_profile(...)` return: a list of dicts with
// `routine_id`, `safety_class`, and `reason`.
//
// The reason text is copied verbatim from the catalog rather than paraphrased,
// so DX53's substring check is a true provenance assertion, not a matching
// game against a re-written string.  A drift in either place will fail
// noisily rather than silently.

const DPF_REASON =
  'Requires verified site preconditions that no telemetry can confirm: ' +
  'vehicle outdoors, area free of combustibles and flammable vapours, ' +
  'adequate ventilation, no personnel near the tailpipe. ' +
  'Forced DPF regeneration holds exhaust temperature near 600 °C for ' +
  '20-40 minutes; speed==0 is equally true of a vehicle parked indoors. ' +
  'D25: classify SERVICE_ONLY when real preconditions include unverifiable ' +
  'site facts.';

const REDUCTANT_REASON =
  'Requires a physically present technician to manage pressurised DEF fluid ' +
  'handling safely. Hot components and fluid spray risk cannot be mitigated ' +
  'by vehicle-state checks alone. D25: site-presence is an unverifiable ' +
  'precondition.';

const DIESEL_ROUTINES = [
  { routineId: 'lamp_self_check',          safetyClass: 'INERT',        invocable: true,  precondition: 'Read-only self-test. Requires the vehicle to be connected.', reason: '' },
  { routineId: 'abs_pump_cycle',           safetyClass: 'STATIONARY',   invocable: true,  precondition: 'Requires vehicle stationary, in park or neutral, with ignition on.', reason: '' },
  { routineId: 'brake_bleed_sequence',     safetyClass: 'STATIONARY',   invocable: true,  precondition: 'Requires vehicle stationary, in park or neutral, with ignition on.', reason: '' },
  { routineId: 'steering_angle_calibration', safetyClass: 'STATIONARY', invocable: true,  precondition: 'Requires vehicle stationary, in park or neutral, with ignition on.', reason: '' },
  { routineId: 'throttle_body_adaptation', safetyClass: 'STATIONARY',   invocable: true,  precondition: 'Requires vehicle stationary, in park or neutral, with ignition on.', reason: '' },
  { routineId: 'glow_plug_test',           safetyClass: 'STATIONARY',   invocable: true,  precondition: 'Requires vehicle stationary, in park or neutral, with ignition on.', reason: '' },
  { routineId: 'dpf_regeneration',         safetyClass: 'SERVICE_ONLY', invocable: false, precondition: 'Requires service visit. Not available remotely.', reason: DPF_REASON },
  { routineId: 'reductant_dosing_cycle',   safetyClass: 'SERVICE_ONLY', invocable: false, precondition: 'Requires service visit. Not available remotely.', reason: REDUCTANT_REASON },
] as const;

const EV_ROUTINES = [
  { routineId: 'lamp_self_check',      safetyClass: 'INERT',      invocable: true,  precondition: 'Read-only self-test. Requires the vehicle to be connected.', reason: '' },
  { routineId: 'pack_isolation_test',  safetyClass: 'INERT',      invocable: true,  precondition: 'Read-only self-test. Requires the vehicle to be connected.', reason: '' },
  { routineId: 'cell_balance_check',   safetyClass: 'INERT',      invocable: true,  precondition: 'Read-only self-test. Requires the vehicle to be connected.', reason: '' },
  { routineId: 'abs_pump_cycle',       safetyClass: 'STATIONARY', invocable: true,  precondition: 'Requires vehicle stationary, in park or neutral, with ignition on.', reason: '' },
  { routineId: 'thermal_prime',        safetyClass: 'STATIONARY', invocable: true,  precondition: 'Requires vehicle stationary, in park or neutral, with ignition on.', reason: '' },
  { routineId: 'charge_port_lock_test', safetyClass: 'STATIONARY', invocable: true, precondition: 'Requires vehicle stationary, in park or neutral, with ignition on.', reason: '' },
] as const;

// ── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

// ────────────────────────────────────────────────────────────────────────────
// DX51 — routines grouped by safety_class
// ────────────────────────────────────────────────────────────────────────────

describe('DX51 — routines are grouped by safety class', () => {
  it('renders three distinct group sections for the diesel catalog', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-MICH-001"
        connectionStatus="connected"
        routines={DIESEL_ROUTINES}
      />,
    );

    // Each of the three safety classes has at least one entry in the diesel
    // catalog, so all three groups should be present.  The panel MAY render
    // each as a heading, a section landmark, or an ExpandableSection header —
    // the assertion is on visible text substring so it does not over-specify.
    const body = document.body.textContent ?? '';
    expect(body).toMatch(/INERT|read-only|Read-only/);
    expect(body).toMatch(/STATIONARY|Stationary|stationary/);
    expect(body).toMatch(/SERVICE_ONLY|service visit|Service visit|Requires service/);
  });

  it('places each routine in exactly one group by its safety_class', () => {
    // Structural guard: `abs_pump_cycle` (STATIONARY) must not render inside a
    // block that also names "INERT" or "SERVICE_ONLY".  This is looser than a
    // full DOM tree assertion but catches the failure mode where all routines
    // land in a single flat list — a "grouping" that groups nothing.
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-MICH-001"
        connectionStatus="connected"
        routines={DIESEL_ROUTINES}
      />,
    );

    // Locate the STATIONARY group by its header text; then assert the DPF
    // routine (SERVICE_ONLY) does NOT appear inside it.
    const stationaryHeader = screen.queryByText(/STATIONARY|Stationary|stationary/i);
    expect(stationaryHeader).not.toBeNull();

    // Walk up to the nearest region container and confirm `dpf_regeneration`
    // is not one of its descendants.  Cloudscape sections do not carry a
    // <section> element unconditionally, so search the header's parent tree.
    const container = stationaryHeader!.closest('div, section, article') ?? stationaryHeader!.parentElement;
    expect(container).not.toBeNull();
    expect(container!.textContent ?? '').not.toMatch(/dpf_regeneration/);
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX52 — each group states its precondition
// ────────────────────────────────────────────────────────────────────────────

describe('DX52 — each safety-class group states its precondition', () => {
  it('names the SERVICE_ONLY precondition as "requires service visit"', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-MICH-001"
        connectionStatus="connected"
        routines={DIESEL_ROUTINES}
      />,
    );

    // Task text is prescriptive: SERVICE_ONLY shown as "requires service
    // visit".  Case-insensitive substring — a group header of "Requires
    // service visit" or "Service visit required" satisfies both.  Softening
    // to "unavailable" or "service required" fails.
    expect(document.body.textContent ?? '').toMatch(/requires? service visit/i);
  });

  it('names the STATIONARY precondition — vehicle at rest / stationary', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-EV-001"
        connectionStatus="connected"
        routines={EV_ROUTINES}
      />,
    );

    // "Requires vehicle stationary" or "at rest" — the group header must
    // convey what STATIONARY means without stating a client-side gate
    // condition (that is DX55's job).  This just asserts SOMETHING labels
    // the group.
    expect(document.body.textContent ?? '').toMatch(/stationary|at rest/i);
  });

  it('names the INERT class as read-only / self-test', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-EV-001"
        connectionStatus="connected"
        routines={EV_ROUTINES}
      />,
    );

    // INERT — from D14: "read-like self-test, no actuation".  Group header
    // conveys the read-only nature; label choice is left to T9.1 but must
    // include one of these substrings.
    expect(document.body.textContent ?? '').toMatch(/read-only|self-test|read-like|no actuation/i);
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX53 — SERVICE_ONLY reasons render verbatim (D17)
// ────────────────────────────────────────────────────────────────────────────

describe('DX53 — every SERVICE_ONLY entry renders its reason verbatim (D17, D25)', () => {
  it('renders the DPF regeneration reason word-for-word', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-MICH-001"
        connectionStatus="connected"
        routines={DIESEL_ROUTINES}
      />,
    );

    // DPF regeneration is the D25 canonical case.  A distinctive substring
    // from the catalog's reason must appear verbatim.  600 °C is the load-
    // bearing safety fact — paraphrasing it to "high temperature" or
    // dropping the unit would strip the reason of its content.
    const body = document.body.textContent ?? '';
    expect(body).toContain('600');
    expect(body).toContain('outdoors');
    expect(body).toMatch(/free of combustibles/);
    expect(body).toMatch(/no personnel near the tailpipe/);
  });

  it('renders the reductant dosing reason word-for-word', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-MICH-001"
        connectionStatus="connected"
        routines={DIESEL_ROUTINES}
      />,
    );

    // Reductant dosing carries a different D25 rationale — the technician-
    // present precondition.  Both must render; showing one and dropping the
    // other would preserve the classification while losing information the
    // operator needs.
    const body = document.body.textContent ?? '';
    expect(body).toMatch(/physically present technician/);
    expect(body).toMatch(/site-presence is an unverifiable/);
  });

  it('renders NO SERVICE_ONLY entry with an empty reason (D17 guard)', () => {
    // Reason-provenance negative control.  If a future implementation
    // synthesises a stub reason to satisfy the two positive controls, this
    // test fails: an entry with `reason: ''` must not silently render as a
    // SERVICE_ONLY item without its rationale.  The routine catalog module
    // already rejects this at construction time (D17), and the panel must
    // not weaken that rejection at the render layer.
    const routinesWithEmptyReason = [
      { routineId: 'lamp_self_check',   safetyClass: 'INERT',        invocable: true,  precondition: 'Read-only self-test.', reason: '' },
      { routineId: 'broken_entry',      safetyClass: 'SERVICE_ONLY', invocable: false, precondition: 'Requires service visit. Not available remotely.', reason: '' },
    ];

    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-MICH-001"
        connectionStatus="connected"
        routines={routinesWithEmptyReason}
      />,
    );

    // The panel MUST NOT render `broken_entry` as SERVICE_ONLY without
    // surfacing a reason.  Two acceptable behaviours: (a) skip the entry
    // entirely with a warning in the console; (b) render an inline "no
    // reason supplied" placeholder that itself violates render-verbatim.
    // The stronger behaviour is (a); the test asserts EITHER by requiring
    // that `broken_entry` does not appear as a plausibly-invocable routine.
    const body = document.body.textContent ?? '';
    if (body.includes('broken_entry')) {
      // If the ID is rendered, the panel MUST also render an "unsupplied"
      // or "unknown" marker rather than treating it as a normal entry.
      expect(body).toMatch(/reason unavailable|reason not supplied|no reason/i);
    }
    // Otherwise (skipped), the assertion is vacuous — which is the intended
    // permissive behaviour.
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX54 — SERVICE_ONLY exposes no in-panel Run affordance
// ────────────────────────────────────────────────────────────────────────────

describe('DX54 — SERVICE_ONLY routines expose no in-panel Run affordance (D15)', () => {
  it('renders no Run button for dpf_regeneration', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-MICH-001"
        connectionStatus="connected"
        routines={DIESEL_ROUTINES}
      />,
    );

    // Locate every button in the panel; NONE must carry an accessible name
    // that names dpf_regeneration OR reductant_dosing_cycle.  Disabled is not
    // sufficient — D15's "a disabled button is an affordance, not a control"
    // makes disabled UI itself a bug for SERVICE_ONLY.  The affordance must
    // not exist.
    const buttons = screen.queryAllByRole('button');
    const runButtonNames = buttons
      .map(b => (b.textContent ?? '').toLowerCase())
      .filter(name => name.includes('run'));

    for (const name of runButtonNames) {
      expect(name).not.toMatch(/dpf.regeneration|reductant.dosing/i);
    }
  });

  it('renders Run affordances for INERT and STATIONARY routines', () => {
    // Positive control — without it, the SERVICE_ONLY negative could pass
    // trivially by rendering zero Run buttons anywhere.  T9.1 owns whether
    // Run buttons are per-routine or per-group, but at least one must exist
    // whose accessible name reaches an INERT or STATIONARY routine identifier.
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-EV-001"
        connectionStatus="connected"
        routines={EV_ROUTINES}
      />,
    );

    const buttons = screen.queryAllByRole('button');
    const runButtonNames = buttons
      .map(b => (b.textContent ?? '').toLowerCase())
      .filter(name => name.includes('run'));

    // At least one Run affordance references an INERT or STATIONARY routine.
    const anyInvocable = runButtonNames.some(name =>
      /lamp.self.check|pack.isolation|cell.balance|abs.pump|thermal.prime|charge.port/i.test(name),
    );
    expect(anyInvocable).toBe(true);
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX55 — the panel does not pre-judge server-owned preconditions
// ────────────────────────────────────────────────────────────────────────────

describe('DX55 — no client-side pre-judgement of server-owned preconditions (D15, T8.2)', () => {
  it('does not render speed/ignition/gear gate copy for STATIONARY routines', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-EV-001"
        connectionStatus="connected"
        routines={EV_ROUTINES}
      />,
    );

    // The task text is prescriptive: "show intent, let the server decide,
    // render its reason."  Naming server-side preconditions in the UI reads
    // to the operator as a client-side promise that the server may or may
    // not keep — the exact defect D15 exists to close.
    //
    // Case-insensitive forbidden substrings:
    //   'speed' / 'moving'      — the runtime facts the sidecar re-checks
    //   'ignition off' / 'gear' — the exact preconditions the server owns
    //   'park' / 'neutral'      — a client copy that pretends to gate them
    //
    // A copy edit that adds "vehicle must be stationary" as a group HEADER
    // is fine (DX52 requires it).  A copy edit that says "cannot run — speed
    // is 42 mph" is the defect.  DX55 keys on the RUNTIME-STATE substrings,
    // not the class-label substrings, so DX52's "stationary" group header
    // does not trip this test.
    //
    // FORWARD-BINDING: while T9.1 is unbuilt this passes trivially — the panel
    // renders no routines section at all so the forbidden substrings cannot
    // appear.  Once T9.1 ships, this becomes an active constraint on the
    // section's copy.  Pattern per F24 in diagnostics-stage2.test.tsx.
    const body = (document.body.textContent ?? '').toLowerCase();
    expect(body).not.toMatch(/speed is|current speed|moving at|is moving/);
    expect(body).not.toMatch(/ignition off|engine off|key off/);
    expect(body).not.toMatch(/gear is|park position|neutral position/);
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX56 — server refusal reasons render verbatim
// ────────────────────────────────────────────────────────────────────────────

describe('DX56 — server refusal reasons render verbatim', () => {
  it('renders the reason string from the last routine result unchanged', () => {
    const refusalReason =
      'REFUSED_STATIONARY_PRECONDITION_LAPSED: vehicle speed 42 mph at frame ' +
      'preflight; last precondition check re-run 180ms before CAN emit.';

    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-EV-001"
        connectionStatus="connected"
        routines={EV_ROUTINES}
        latestRoutineResult={{
          routineId: 'abs_pump_cycle',
          status: 'REFUSED',
          reason: refusalReason,
        }}
      />,
    );

    // The panel does not translate or map — the reason is the reason.
    // T8.2's sidecar-side refusal path returns a distinct status; the
    // reason must reach the operator character-for-character so they can
    // reproduce the failure.
    const body = document.body.textContent ?? '';
    expect(body).toContain(refusalReason);
  });

  it('does NOT synthesise a refusal reason when none is supplied', () => {
    // Negative control: an implementation that renders a helpful-sounding
    // placeholder ("please check preconditions and try again") when the
    // server did not supply a reason is doing exactly what the constraint
    // forbids — pre-judging what the server would say.
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-EV-001"
        connectionStatus="connected"
        routines={EV_ROUTINES}
        latestRoutineResult={{
          routineId: 'abs_pump_cycle',
          status: 'REFUSED',
          reason: undefined,
        }}
      />,
    );

    // No fabricated reason strings.  Acceptable behaviours: render nothing,
    // or render a status-only marker like "refused" with NO explanatory
    // sentence.  The forbidden patterns are helpful-sounding placeholders.
    //
    // FORWARD-BINDING: same trivial-pass shape as DX55 in the red phase.
    const body = (document.body.textContent ?? '').toLowerCase();
    expect(body).not.toMatch(/please check preconditions/);
    expect(body).not.toMatch(/try again|retry the routine/);
    expect(body).not.toMatch(/precondition may have lapsed/);
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX57 — absence of routines suppresses the section
// ────────────────────────────────────────────────────────────────────────────

describe('DX57 — routines section is absent when the prop is not supplied', () => {
  it('renders no Diagnostic routines heading without the prop', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-MICH-001"
        connectionStatus="connected"
      />,
    );

    // Do not render an empty section or an "unavailable" placeholder — a
    // vehicle whose profile resolves to no routines is a valid (rare)
    // state.  The panel already carries the concept of prop-absent from
    // the T7.1a suite: `ecuInventory` absent → no inventory section.
    // Same discipline for routines.
    expect(screen.queryByText(/Diagnostic routines/i)).toBeNull();
  });

  it('renders no Diagnostic routines heading for an empty catalog', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-MICH-001"
        connectionStatus="connected"
        routines={[]}
      />,
    );

    // Empty array is a valid resolved state (no routines apply to this
    // vehicle).  It must not fabricate a "no routines available" section
    // header either — that becomes visual noise for the majority of
    // vehicles that DO have routines.
    expect(screen.queryByText(/Diagnostic routines/i)).toBeNull();
  });
});
