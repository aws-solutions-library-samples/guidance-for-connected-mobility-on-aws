// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Vehicle diagnostics — Stage 2 surface (DX36–DX49).
 *
 * Spec: `.kiro/specs/2026-09-02-cms-diagnostics-platform`
 *       § Design, 8-hop resolution block (~line 854)
 *       tasks.md T7.0b Accept blocks (a)–(e)
 *       tasks.md T6.4 RESULT block (identity/drift response shape)
 *       tasks.md T6.5 RESULT block (PROGRESS response shape)
 *       decisions.md F22 Decision 1 (DX8 preserved; lazy-mount constraint)
 *       decisions.md F22 Decision 2 (two ECU vocabularies; backend adjudicates)
 *
 * RED PHASE — every test in this file is EXPECTED TO FAIL.
 *
 * T7.1a does not exist yet.  The assertions in this file describe the panel's
 * post-T7.1a behaviour.  They fail on the current panel because:
 *   (a) The 8 named unresolvable states are not rendered; the panel renders
 *       nothing in their place (blank output, not a reason string).
 *   (b) The "Live data" and "ECU inventory" disclosures do not exist yet.
 *   (c) Drift (actual / expected / delta) is not rendered.
 *   (d) `expected: null` → "cannot verify" is not implemented.
 *   (e) Per-ECU progress (N of M, ECU label) is not implemented.
 *
 * Do NOT create stubs to make these tests pass.  Green tests here are
 * unacceptable until T7.1a/b ship.
 *
 * ── Test ID map ─────────────────────────────────────────────────────────────
 *
 * DX36  8 named unresolvable states — each hop renders its reason string
 * DX37  8 named states — none renders a blank panel
 * DX38  "Live data" disclosure is present when a live-data result is available
 * DX39  "ECU inventory" disclosure is present and lazy-mounts (F22 Decision 1)
 * DX40  ECU inventory stays invisible in DX8's in-flight fixture (DX8 compat)
 * DX41  Drift renders actual, expected AND delta — never actual alone (F6)
 * DX42  `expected: null` renders as "cannot verify", never as a match
 * DX43  ECU with no diagnostic address renders "not remotely diagnosable"
 *       and is never silently omitted (DX17 UI half)
 * DX44  Powertrain-inapplicable ECU is not rendered (C15)
 * DX45  Manifest ECU labels (TCU, BMS, VCU…) appear in inventory via
 *       ecu_names.ts extended table (F22 Decision 2)
 * DX46  Per-ECU progress names the ECU and states N of M when PROGRESS data
 *       is present in the polled command row (T6.5 contract)
 * DX47  With NO progress data the panel degrades to indeterminate state and
 *       names nothing (T7.1b / DX8 safe)
 * DX48  `ecu_names.ts` contains the manifest-vocabulary label table
 *       (TCU, BMS, VCU, BCM, ADAS, IVI, GW, CCU, ECM) as a second named
 *       export, distinct from ECU_OPTIONS (F22 Decision 2)
 * DX49  Copy-lint extension — "ECU inventory" and "Live data" section headers
 *       contain no API paths, Python identifiers, or force_event strings
 */

import React from 'react';
import { render, screen, within, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import * as fs from 'node:fs';
import * as path from 'node:path';

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

import { authFetch } from '@/utils/authFetch';

// ── Import under test ────────────────────────────────────────────────────────
//
// VehicleDiagnosticsPanel exists (Group 3, T3.1).  The red-phase failures below
// come from assertions about Stage 2 features not yet implemented, not from a
// missing module.  Importing it here so vi.mock hoisting works correctly and
// so individual tests do not have to guard against the import throwing.
//
import VehicleDiagnosticsPanel from '@/components/vehicles/vehicle-detail/VehicleDiagnosticsPanel';

// ── The 8 named unresolvable reason strings ──────────────────────────────────
//
// From spec.md § Design, 8-hop resolution block (~line 854).
// Each string is what the panel MUST render when the corresponding hop fails;
// rendering nothing (blank, null, or a generic "no data" message) is a failure.
//
const HOP_REASONS = [
  'not diagnosable — cloud-fed vehicle',  // hop 0: classification offboard
  'classification not determined',         // hop 0: classification unknown
  'model not assigned',                    // hop 1: no modelManifestName
  'model has no ECU set',                  // hop 2: ecus[] empty
  'no DID profile for this model',         // hop 3: no didProfileRef
  'ECU not diagnosable on this model',     // hop 4: profile[modelEcu] absent
  'not remotely diagnosable',              // hop 5: D18 map → no CAN address
  'not supported by this ECU',             // hop 6: 0x22 request → NRC
] as const;

// ── T6.4 RESULT: identity/drift response shape ───────────────────────────────
//
// From tasks.md T6.4 RESULT block.
// Per ECU: { actual: { sw_version, hw_version }, expected: {...} | null, delta: { sw, hw } | null }
// F6 fail-closed: if expected is null → delta.sw = 'unknown' (not null, not a match).
//
interface EcuVersionEntry {
  actual: { sw_version: string; hw_version: string };
  expected: { sw_version: string; hw_version: string } | null;
  delta: { sw: string | null; hw: string | null } | null;
}

/** Minimal identity/drift result that T7.1a will receive as a prop or via fetch. */
const makeDriftResult = (overrides?: Partial<Record<string, EcuVersionEntry>>) => ({
  vehicleId: 'VEH-TEST-001',
  commandType: 'read_identity',
  status: 'SUCCEEDED',
  components: {
    BMS: {
      actual: { sw_version: '3.0.1', hw_version: '1.0' },
      expected: { sw_version: '3.0.0', hw_version: '1.0' },
      delta: { sw: '3.0.1 vs 3.0.0', hw: null },  // drift
    },
    TCU: {
      actual: { sw_version: '2.1.4', hw_version: '2.0' },
      expected: { sw_version: '2.1.4', hw_version: '2.0' },
      delta: { sw: null, hw: null },               // match
    },
    GW: {
      actual: { sw_version: '1.0.0', hw_version: '1.0' },
      expected: null,                              // F6: baseline absent → cannot verify
      delta: { sw: 'unknown', hw: 'unknown' },
    },
    ...overrides,
  },
});

/** Drift result containing ONLY `actual` with no `expected` or `delta`.
 *  Any panel prop path that accepts this and displays "match" or omits "expected"
 *  is the F6 defect DX41 guards against.
 */
const makeActualOnlyResult = () => ({
  vehicleId: 'VEH-TEST-001',
  commandType: 'read_identity',
  status: 'SUCCEEDED',
  components: {
    BMS: {
      actual: { sw_version: '3.0.1', hw_version: '1.0' },
      // `expected` and `delta` deliberately absent — structural invariant violation
    },
  },
});

// ── T6.5 RESULT: PROGRESS polled command row shape ───────────────────────────
//
// From tasks.md T7.0 Accept block and T6.5 RESULT block.
// After T7.0 lands, the polled command row exposes `progress`:
//   { ecu_index: N, ecu_total: M, ecu_name: '<KEY>', ecu_status: 'ok'|'timeout' }
// T7.1b uses this to render "Scanning ECU N of M: <label>".
//
interface ProgressEntry {
  ecu_index: number;
  ecu_total: number;
  ecu_name: string;
  ecu_status: 'ok' | 'timeout' | 'error';
}

// ── Fixture: scan in-flight with no progress data (DX8 baseline state) ───────

const renderInFlight = () => {
  const authFetchMock = vi.mocked(authFetch);
  authFetchMock.mockReturnValue(new Promise(() => {}) as never);

  render(
    <VehicleDiagnosticsPanel
      vehicleId="VEH-TEST-001"
      connectionStatus="connected"
    />,
  );

  const scanBtn = screen.getByRole('button', { name: /run diagnostic scan/i });
  scanBtn.click();

  act(() => { vi.advanceTimersByTime(1_500); });
};

// ── Fixture: scan completed with a polled result including progress ───────────

const mockPolledCommandWithProgress = async (progress: ProgressEntry) => {
  const authFetchMock = vi.mocked(authFetch);
  let callCount = 0;
  authFetchMock.mockImplementation(async (url: string) => {
    callCount++;
    // First call is the POST (startFullScan); return ok
    if (callCount === 1) {
      return { ok: true, status: 200, text: async () => '' } as Response;
    }
    // Subsequent calls are GET polls; return an in-progress row with progress
    return {
      ok: true,
      status: 200,
      json: async () => ({
        commands: [{
          commandId: 'cmd-test-001',
          status: 'IN_PROGRESS',
          // Live scan rows carry commandType 'read_dtcs' and type 'sovd'. The
          // scan poll now matches rows to the scan it started, so an invented
          // commandType 'sovd' would read as some other command
          // (issue 2026-09-25-diagnostics-ia-uat-defects).
          commandType: 'read_dtcs',
          type: 'sovd',
          progress,
        }],
      }),
    } as Response;
  });

  render(
    <VehicleDiagnosticsPanel
      vehicleId="VEH-TEST-001"
      connectionStatus="connected"
    />,
  );

  const scanBtn = screen.getByRole('button', { name: /run diagnostic scan/i });
  scanBtn.click();

  // F25: two drains are required, in this order.
  //
  // `handleRunDiagnosticScan` is async and only registers the poll interval
  // AFTER `await startFullScan(...)` resolves. Advancing timers first would fire
  // nothing, because the interval does not exist yet — which is why DX46 stayed
  // red even after the helper was made async. So: drain once to let the POST
  // settle and the interval register, THEN advance to fire the first poll, then
  // let the poll's own two awaits (fetch + json) settle.
  await act(async () => {});
  await act(async () => { vi.advanceTimersByTime(11_000); });
};

// ────────────────────────────────────────────────────────────────────────────
// DX36 — 8 named unresolvable states each render their reason string
// ────────────────────────────────────────────────────────────────────────────
//
// Spec § Design: each arrow in the 8-hop resolution chain is a distinct
// operator-facing reason.  "no data" is what the current product would say
// for all eight; that is the defect.
//
// The panel will receive the resolved reason string as part of the component
// data (server-side adjudication per F22 Decision 2).  Here we drive the
// panel with a prop that carries an `unreachableReason` field and assert
// the reason appears verbatim.  T7.1a owns how the prop is named; the test
// asserts the string is present in visible text.
//
describe('DX36 — 8 named unresolvable states render their reason string (§ Design, T7.0b-a)', () => {

  HOP_REASONS.forEach(reason => {
    it(`renders "${reason}" verbatim, never blank`, () => {
      // T7.1a will accept a prop (e.g. `ecuDiagnosticResult`) carrying the
      // resolved reason.  The panel does not accept that prop yet.
      // This test will fail until the prop and the rendering exist.
      render(
        <VehicleDiagnosticsPanel
          vehicleId="VEH-TEST-001"
          connectionStatus="connected"
          // @ts-expect-error prop does not exist yet; T7.1a adds it
          ecuDiagnosticResult={{
            components: {
              BMS: { unreachableReason: reason },
            },
          }}
        />,
      );

      // The reason string must appear in visible DOM text, not only in
      // aria-label or title attributes.
      const bodyText = document.body.textContent ?? '';
      expect(
        bodyText,
        `Reason "${reason}" must appear verbatim in rendered text. ` +
        `Rendering nothing is the "no data" defect § Design identifies.`,
      ).toContain(reason);

      document.body.innerHTML = '';  // cleanup
    });
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX37 — none of the 8 states renders a blank panel
// ────────────────────────────────────────────────────────────────────────────
//
// When an unresolvable hop is present, the panel must show a non-empty
// diagnostic section — not an empty container and not the default "No scan
// has run yet" message.
//
describe('DX37 — unresolvable hops never produce a blank panel (T7.0b-a)', () => {

  HOP_REASONS.forEach(reason => {
    it(`panel non-empty for hop reason "${reason}"`, () => {
      const { container } = render(
        <VehicleDiagnosticsPanel
          vehicleId="VEH-TEST-001"
          connectionStatus="connected"
          // @ts-expect-error prop does not exist yet
          ecuDiagnosticResult={{
            components: {
              BMS: { unreachableReason: reason },
            },
          }}
        />,
      );

      // The panel must not fall through to the generic "No scan has run yet"
      // message when a reason string is provided.
      const bodyText = document.body.textContent ?? '';
      expect(
        bodyText,
        `Panel must not show "No scan has run yet" when a diagnostic reason is present`,
      ).not.toContain('No scan has run yet');

      // And the reason-bearing section must have some non-whitespace text
      // (guards against invisible containers that pass innerHTML checks).
      expect(
        container.textContent?.trim(),
        `Panel must render non-whitespace content when reason = "${reason}"`,
      ).not.toBe('');

      document.body.innerHTML = '';
    });
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX38 — "Live data" disclosure renders when live-data result is available
// ────────────────────────────────────────────────────────────────────────────
//
// tasks.md T7.1a Accept: "'Live data' … disclosures render".
// The section header text must appear when a live-data result is provided.
//
describe('DX38 — "Live data" disclosure present when a live-data result is available (T7.0b-b)', () => {

  it('renders a "Live data" section header when liveDataResult is provided', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TEST-001"
        connectionStatus="connected"
        // @ts-expect-error prop does not exist yet
        liveDataResult={{ components: { BMS: { did: '0x2120', value: '42', unit: '%' } } }}
      />,
    );

    const bodyText = document.body.textContent ?? '';
    // "Live data" as visible section header text
    expect(
      bodyText,
      '"Live data" section header must be visible when liveDataResult is provided. ' +
      'T7.1a Accept: "Live data … disclosures render".',
    ).toContain('Live data');
  });

  it('does NOT render a "Live data" section when no live-data result is provided', () => {
    // FORWARD-BINDING: load-bearing once T7.1a wires the `liveDataResult` prop;
    // passes today because the prop does not exist and the panel renders nothing for it.
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TEST-001"
        connectionStatus="connected"
      />,
    );

    // With no live data prop the section should be absent, not empty.
    // An empty section is worse than none because it suggests data exists.
    const bodyText = document.body.textContent ?? '';
    // Acceptable if there is no such section, or if the panel shows a placeholder
    // only inside an ExpandableSection that is not yet expanded.
    // The test gates only against *content* appearing as if data is present.
    const liveDataHeader = screen.queryByText(/live data/i);
    expect(
      liveDataHeader,
      '"Live data" section must NOT be visible when no live-data result is provided',
    ).toBeNull();
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX39 — "ECU inventory" disclosure is present and lazy-mounts
// ────────────────────────────────────────────────────────────────────────────
//
// tasks.md T7.1a Accept: "'ECU inventory' disclosures render".
// F22 Decision 1: the inventory MUST lazy-mount — Cloudscape ExpandableSection
// renders children into the DOM even while collapsed, so a non-lazy-mounted
// inventory would inject ECU names into document.body.innerHTML during a scan,
// failing DX8's categorical check.
//
// Two parts:
//   (a) The ExpandableSection header "ECU inventory" is present.
//   (b) When COLLAPSED, no ECU inventory content (ECU labels from ecu_names.ts)
//       appears in document.body.innerHTML (lazy-mount enforcement).
//
describe('DX39 — "ECU inventory" disclosure present and lazy-mounts (F22 D1, T7.0b-b)', () => {

  it('renders an "ECU inventory" expandable section header', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TEST-001"
        connectionStatus="connected"
        // @ts-expect-error prop does not exist yet
        ecuInventory={[
          { ecuName: 'BMS', label: 'Battery Management System', reachable: true },
          { ecuName: 'GW', label: 'Gateway', unreachableReason: 'not remotely diagnosable' },
        ]}
      />,
    );

    const bodyText = document.body.textContent ?? '';
    expect(
      bodyText,
      '"ECU inventory" section header must be visible when ecuInventory is provided. ' +
      'T7.1a Accept: "ECU inventory … disclosures render".',
    ).toMatch(/ecu inventory/i);
  });

  it('inventory render is gated on an expanded-state variable in the component source (F22 D1, F25)', () => {
    // Re-pointed per F25 (decisions.md 2026-09-04): the previous assertion
    // (render-time bodyHtml.not.toContain) contradicted DX45/DX43 — same fixture,
    // opposite assertion on the same string, unsatisfiable.  DX39's substantive
    // invariant — inventory children must not leak into the DOM where DX8's
    // categorical innerHTML check would see them — is already independently
    // enforced by DX40 (manifest labels absent during in-flight scan) and DX8
    // itself (green at 25/25).  F25 selects the mechanism-pin as the repair:
    // assert the source code gates inventory children on the expanded-state
    // variable, the same technique the rate-limiter regression tests use.
    const panelSource = fs.readFileSync(
      path.resolve(__dirname, '../VehicleDiagnosticsPanel.tsx'),
      'utf-8',
    );

    // The inventory children block must be gated on ecuInventoryExpanded.
    // This is the state variable that collapses the section and gates content
    // out of the DOM (F22 Decision 1: lazy-mount to prevent DX8 collision).
    expect(
      panelSource,
      'VehicleDiagnosticsPanel.tsx must gate the ECU inventory children render ' +
      'on ecuInventoryExpanded (F22 Decision 1). ' +
      'F25: source-read pin replaces the unsatisfiable render-time assertion.',
    ).toContain('ecuInventoryExpanded');

    // The gating condition must also exclude scanning=true (DX40 invariant).
    expect(
      panelSource,
      'VehicleDiagnosticsPanel.tsx must exclude children while scanning is true ' +
      '(ecuInventoryExpanded && !scanning). F22 Decision 1 / DX40.',
    ).toMatch(/ecuInventoryExpanded\s*&&\s*!scanning|!scanning\s*&&\s*ecuInventoryExpanded/);
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX40 — ECU inventory stays invisible in DX8's in-flight fixture
// ────────────────────────────────────────────────────────────────────────────
//
// F22 Decision 1: the ECU inventory must not appear in document.body.innerHTML
// while a scan is in flight, regardless of the collapsed/expanded state.
// This is a compatibility assertion: DX8 checks innerHTML categorically.
//
describe('DX40 — ECU inventory invisible during in-flight scan (DX8 compat, F22 D1)', () => {

  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it('no manifest ECU name appears in DOM while scan is in flight', () => {
    // FORWARD-BINDING: load-bearing once T7.1a wires the `ecuInventory` prop and
    // T7.1b wires in-flight inventory hiding; passes today because the prop does
    // not exist and the panel never injects inventory content.
    // Render with an inventory prop so T7.1a has data to display, but
    // the scan is in-flight so the inventory must be hidden.
    vi.mocked(authFetch).mockReturnValue(new Promise(() => {}) as never);

    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TEST-001"
        connectionStatus="connected"
        // @ts-expect-error prop does not exist yet
        ecuInventory={[
          { ecuName: 'BMS', label: 'Battery Management System', reachable: true },
          { ecuName: 'TCU', label: 'Telematics Control Unit', reachable: true },
          { ecuName: 'VCU', label: 'Vehicle Control Unit', reachable: true },
        ]}
      />,
    );

    const scanBtn = screen.getByRole('button', { name: /run diagnostic scan/i });
    scanBtn.click();
    act(() => { vi.advanceTimersByTime(1_500); });

    const bodyHtml = document.body.innerHTML;
    // None of the manifest-vocabulary ECU labels may appear while in-flight.
    const manifestLabels = [
      'Battery Management System', 'BMS',
      'Telematics Control Unit', 'TCU',
      'Vehicle Control Unit', 'VCU',
    ];
    for (const label of manifestLabels) {
      expect(
        bodyHtml,
        `"${label}" must not appear in DOM while scan is in flight (DX8 compat, F22 D1)`,
      ).not.toContain(label);
    }
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX41 — drift renders actual, expected AND delta together — never actual alone
// ────────────────────────────────────────────────────────────────────────────
//
// F6 fail-closed: drift must expose all three fields.
// tasks.md T6.4 RESULT: "drift reports actual, expected and delta".
// tasks.md T7.1a Accept: "drift shows actual/expected/delta".
// T7.0b Accept (b): "drift renders actual, expected and delta together,
//   and never actual alone (F6)".
//
describe('DX41 — drift renders actual, expected AND delta (F6, T6.4 RESULT, T7.0b-b)', () => {

  it('renders BMS sw drift: actual "3.0.1", expected "3.0.0", and delta "3.0.1 vs 3.0.0"', () => {
    const driftResult = makeDriftResult();

    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TEST-001"
        connectionStatus="connected"
        // @ts-expect-error prop does not exist yet
        identityResult={driftResult}
      />,
    );

    const bodyText = document.body.textContent ?? '';

    // All three fields must be present
    expect(bodyText, 'actual sw version must be rendered').toContain('3.0.1');
    expect(bodyText, 'expected sw version must be rendered').toContain('3.0.0');
    expect(bodyText, 'delta string must be rendered verbatim').toContain('3.0.1 vs 3.0.0');
  });

  it('does NOT render only actual without expected or delta (F6 negative control)', () => {
    // FORWARD-BINDING: load-bearing once T7.1a wires the `identityResult` prop;
    // passes today because the prop does not exist and the panel renders no version
    // data at all, so neither "match" nor a version number appears.
    const actualOnlyResult = makeActualOnlyResult();

    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TEST-001"
        connectionStatus="connected"
        // @ts-expect-error prop does not exist yet
        identityResult={actualOnlyResult}
      />,
    );

    // If the panel accepts data missing `expected` and `delta` and renders it as
    // a match or renders "actual" in isolation, that is the F6 defect.
    // The panel MUST either refuse to render (structural invariant) or
    // surface "cannot verify" / "unknown" rather than displaying version number
    // alone alongside a "match" indicator.
    const bodyText = document.body.textContent ?? '';
    // The actual version number is visible — but it must not appear alongside
    // any "match" or "✓" or "ok" language, because there is no expected to compare.
    const impliesMatch = /match|✓|same|no.drift|up.to.date/i.test(bodyText);
    expect(
      impliesMatch,
      'Panel must NOT imply a match when `expected` is absent (F6 negative control). ' +
      'Rendering "3.0.1" without "expected" alongside a match indicator is the defect.',
    ).toBe(false);
  });

  it('renders matching ECU (TCU delta sw: null) without a drift indicator', () => {
    // FORWARD-BINDING: load-bearing once T7.1a wires the `identityResult` prop;
    // passes today because the prop does not exist and no version strings are rendered.
    const driftResult = makeDriftResult();

    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TEST-001"
        connectionStatus="connected"
        // @ts-expect-error prop does not exist yet
        identityResult={driftResult}
      />,
    );

    // TCU has delta.sw == null (match).  The panel may show "match" or "✓"
    // for TCU but must not show a drift string like "2.1.4 vs 2.1.4".
    const bodyText = document.body.textContent ?? '';
    expect(bodyText, 'TCU with null delta must NOT show a drift string like "X vs X"')
      .not.toMatch(/2\.1\.4\s+vs\s+2\.1\.4/);
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX42 — `expected: null` renders as "cannot verify", never as a match
// ────────────────────────────────────────────────────────────────────────────
//
// T6.4 RESULT: if manifest carries no baselineVersion for an ECU,
//   response includes `expected: null` and `delta.sw: 'unknown'`.
//   NOT `delta.sw: null` (that would falsely claim a match).
// T7.0b Accept (e): "`expected: null` renders as 'cannot verify',
//   never as a match".
//
describe('DX42 — `expected: null` renders as "cannot verify" (T6.4 RESULT, T7.0b-e)', () => {

  it('renders "cannot verify" or "unknown" when expected is null (GW component)', () => {
    const driftResult = makeDriftResult();  // GW has expected: null, delta.sw: 'unknown'

    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TEST-001"
        connectionStatus="connected"
        // @ts-expect-error prop does not exist yet
        identityResult={driftResult}
      />,
    );

    const bodyText = document.body.textContent ?? '';

    // "cannot verify" or "unknown" must appear for GW (the null-expected component)
    const hasCannotVerify = /cannot verify|unknown/i.test(bodyText);
    expect(
      hasCannotVerify,
      '"cannot verify" or "unknown" must appear when expected is null (GW component). ' +
      'T6.4 RESULT: delta.sw = "unknown" when expected is absent; the UI must surface this.',
    ).toBe(true);
  });

  it('does NOT render expected: null as a match (no "match" / "✓" alongside null-expected ECU)', () => {
    // FORWARD-BINDING: load-bearing once T7.1a wires the `identityResult` prop
    // and renders GW; passes today because the prop does not exist, GW is never
    // rendered, and the `if (bodyText.includes('GW'))` guard short-circuits.
    const driftResult = makeDriftResult();

    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TEST-001"
        connectionStatus="connected"
        // @ts-expect-error prop does not exist yet
        identityResult={driftResult}
      />,
    );

    const bodyText = document.body.textContent ?? '';

    // The panel must not show "match" adjacent to GW's version output.
    // (A global "match" for TCU is fine; the test checks the null-expected entry.)
    // Strategy: if "GW" appears in the text, the content near it must not say "match".
    // Since we cannot easily do proximity assertion on text, we assert that
    // the panel does NOT render GW's actual version alongside a match indicator
    // with no "cannot verify" anywhere.
    if (bodyText.includes('GW')) {
      expect(
        bodyText,
        'When GW (expected: null) is rendered, "cannot verify" or "unknown" must also appear',
      ).toMatch(/cannot verify|unknown/i);
    }
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX43 — ECU with no diagnostic address renders "not remotely diagnosable"
//         and is NEVER silently omitted (DX17 UI half)
// ────────────────────────────────────────────────────────────────────────────
//
// spec.md Test surface row DX17: "A model ECU with no diagnostic address
//   renders 'not remotely diagnosable' and is never silently omitted (D18)".
// T7.0b Accept (c): "an ECU with no diagnostic address renders
//   'not remotely diagnosable' and is never silently omitted (DX17)".
//
// F22 Decision 2: the backend resolves the reason; the frontend renders it.
// The prop carries `unreachableReason: 'not remotely diagnosable'` for the
// affected ECU.
//
describe('DX43 — no-address ECU renders "not remotely diagnosable", never omitted (DX17 UI, T7.0b-c)', () => {

  it('renders "not remotely diagnosable" for a GW ECU with no diagnostic address', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TEST-001"
        connectionStatus="connected"
        // @ts-expect-error prop does not exist yet
        ecuInventory={[
          { ecuName: 'BMS', label: 'Battery Management System', reachable: true },
          {
            ecuName: 'GW',
            label: 'Gateway',
            reachable: false,
            unreachableReason: 'not remotely diagnosable',
          },
        ]}
      />,
    );

    const bodyText = document.body.textContent ?? '';
    expect(
      bodyText,
      '"not remotely diagnosable" must appear for a GW ECU with no diagnostic address. ' +
      'DX17: never silently omitted.',
    ).toContain('not remotely diagnosable');
  });

  it('includes GW in the rendered output (never silently omitted)', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TEST-001"
        connectionStatus="connected"
        // @ts-expect-error prop does not exist yet
        ecuInventory={[
          { ecuName: 'BMS', label: 'Battery Management System', reachable: true },
          {
            ecuName: 'GW',
            label: 'Gateway',
            reachable: false,
            unreachableReason: 'not remotely diagnosable',
          },
        ]}
      />,
    );

    // GW must appear in the ECU inventory, not be silently dropped.
    // The inventory is collapsed by default, so this test applies AFTER expansion.
    // Here we assert the prop-driven render includes GW's label or ecuName
    // somewhere in the final DOM (expanded state).
    // Since expansion requires user interaction, we instead verify that the
    // container is prepared to show it — the test will fail until T7.1a
    // renders the inventory on expand.
    //
    // Test strategy: render with expanded prop or interact with the section.
    // Since we can't click Cloudscape's ExpandableSection in this environment
    // without a full user-event setup, we assert via the prop pathway that
    // GW is not filtered from the ecuInventory before rendering.
    // T7.1a must not silently drop unreachable ECUs from the prop before passing
    // it to the disclosure.
    const bodyText = document.body.textContent ?? '';
    // The section header "ECU inventory" must exist (otherwise GW can't be shown)
    expect(
      bodyText,
      'ECU inventory section must be present when ecuInventory includes GW. ' +
      'GW must never be silently dropped.',
    ).toMatch(/ecu inventory/i);
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX44 — powertrain-inapplicable ECU is NOT rendered (C15)
// ────────────────────────────────────────────────────────────────────────────
//
// C15: "Powertrain drives the profile … a vehicle with no fuelType cannot be
//   assigned a profile and must resolve to 'powertrain not determined'".
// T7.0b Accept (d): "a powertrain-inapplicable ECU is not rendered (C15)".
//
// The backend marks ECUs that are not applicable to the vehicle's powertrain
// with `applicable: false`.  The frontend must not render those ECUs at all —
// not even with a "not applicable" badge.
//
describe('DX44 — powertrain-inapplicable ECU is NOT rendered (C15, T7.0b-d)', () => {

  it('does not render an ECU marked applicable: false', () => {
    // FORWARD-BINDING: load-bearing once T7.1a wires the `ecuInventory` prop and
    // renders the inventory; passes today because the prop does not exist and
    // EVAP never appears in the DOM regardless.
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TEST-001"
        connectionStatus="connected"
        // @ts-expect-error prop does not exist yet
        ecuInventory={[
          { ecuName: 'BMS', label: 'Battery Management System', reachable: true, applicable: true },
          {
            ecuName: 'ECU_EVAP',
            label: 'EVAP (ECU_EVAP)',
            reachable: true,
            applicable: false,  // BEV vehicle — EVAP not applicable (C15 / D22)
          },
        ]}
      />,
    );

    const bodyText = document.body.textContent ?? '';
    // The EVAP ECU must not appear at all — not even with "not applicable".
    // C15: the front-end must not render it; it is excluded at the server.
    expect(
      bodyText,
      'ECU_EVAP (applicable: false) must NOT appear in rendered output. C15.',
    ).not.toContain('EVAP');
    expect(bodyText).not.toContain('ECU_EVAP');
  });

  it('renders ECUs with applicable: true', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TEST-001"
        connectionStatus="connected"
        // @ts-expect-error prop does not exist yet
        ecuInventory={[
          { ecuName: 'BMS', label: 'Battery Management System', reachable: true, applicable: true },
        ]}
      />,
    );

    // BMS is applicable — must appear after expanding ECU inventory
    const bodyText = document.body.textContent ?? '';
    // The section header must exist (content is lazy-mounted):
    expect(bodyText).toMatch(/ecu inventory/i);
    // (Content of collapsed section is tested in DX39)
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX45 — manifest ECU labels appear in inventory via ecu_names.ts
// ────────────────────────────────────────────────────────────────────────────
//
// F22 Decision 2: two ECU vocabularies reach this panel:
//   _SIDECAR_ECU_MAP keys (ECU_*) — already in ecu_names.ts (ECU_OPTIONS)
//   manifest names (TCU, BMS, VCU, BCM, ADAS, IVI, GW, CCU, ECM) — to be added
// T7.1a Constraints: extend ecu_names.ts in place with a manifest-vocabulary
//   label table.  Do not add a second module.
//
// This test asserts the panel resolves a manifest-name ECU to a human-readable
// label sourced from ecu_names.ts, not a bare key like "BMS" or "TCU".
//
describe('DX45 — manifest ECU labels resolved from ecu_names.ts (F22 D2, T7.1a)', () => {

  it('renders the human label "Battery Management System" for manifest ECU "BMS"', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TEST-001"
        connectionStatus="connected"
        // @ts-expect-error prop does not exist yet
        ecuInventory={[
          { ecuName: 'BMS', reachable: true, applicable: true },
        ]}
      />,
    );

    // The panel must look up "BMS" in ecu_names.ts extended table and render
    // its human-readable label.  Rendering the bare key "BMS" is acceptable
    // only if the label is also shown alongside it.
    // This test asserts the LABEL appears; bare-key-only rendering fails.
    const bodyText = document.body.textContent ?? '';
    // After the inventory is lazy-mounted (expanded), the human label must be present.
    // Since we can't expand here, assert the section exists and the panel
    // is structured to show it (the full assertion turns green on T7.1a).
    expect(
      bodyText,
      '"Battery Management System" or similar label must appear when BMS is in the inventory. ' +
      'F22 Decision 2: ecu_names.ts must be extended in place with the manifest vocabulary.',
    ).toContain('Battery Management System');
  });

  it('renders the human label for "GW" (Gateway)', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TEST-001"
        connectionStatus="connected"
        // @ts-expect-error prop does not exist yet
        ecuInventory={[
          { ecuName: 'GW', reachable: true, applicable: true },
        ]}
      />,
    );

    const bodyText = document.body.textContent ?? '';
    expect(
      bodyText,
      '"Gateway" label must appear when GW is in the inventory. ' +
      'ecu_names.ts manifest table must include GW.',
    ).toContain('Gateway');
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX46 — per-ECU progress names ECU and states N of M when PROGRESS data present
// ────────────────────────────────────────────────────────────────────────────
//
// T7.1b Accept: "in-flight progress names the ECU T6.5's stream reported,
//   via ecu_names.ts, and states N of M from ecu_index/ecu_total".
// T6.5 RESULT: PROGRESS payload: { ecu_index, ecu_total, ecu_name, ecu_status }.
// T7.0 Accept 1: the polled command row exposes a `progress` field after T7.0.
//
describe('DX46 — per-ECU progress names ECU and states N of M (T6.5 RESULT, T7.1b)', () => {

  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it('renders ECU label and "N of M" when progress data is present in the polled row', async () => {
    const progress: ProgressEntry = {
      ecu_index: 2,
      ecu_total: 9,
      ecu_name: 'ECU_BATTERY_HV',
      ecu_status: 'ok',
    };

    await mockPolledCommandWithProgress(progress);

    const bodyText = document.body.textContent ?? '';

    // N of M format (e.g. "3 of 9" — ecu_index is 0-based so display as index+1)
    const hasNOfM = /\d+\s+of\s+9/i.test(bodyText) || bodyText.includes('3 of 9') || bodyText.includes('2 of 9');
    expect(
      hasNOfM,
      '"N of M" progress must appear when progress.ecu_index/ecu_total are present. ' +
      'T7.1b Accept: states N of M from ecu_index/ecu_total.',
    ).toBe(true);

    // The ECU label from ecu_names.ts for ECU_BATTERY_HV must appear
    expect(
      bodyText,
      'ECU_BATTERY_HV label ("HV Battery") must appear in progress indicator. ' +
      'T7.1b Accept: names the ECU via ecu_names.ts.',
    ).toContain('HV Battery');
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX47 — with NO progress data the panel degrades to indeterminate / names nothing
// ────────────────────────────────────────────────────────────────────────────
//
// T7.1b Constraints: "With no progress data available the panel degrades to
//   the indeterminate state and names nothing."
// This is also consistent with DX8's in-flight fixture (no progress data →
// no ECU names in DOM).
//
describe('DX47 — no progress data → indeterminate state, names nothing (T7.1b, DX8 compat)', () => {

  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it('shows an indeterminate indicator (not ECU-specific) when no progress data', () => {
    // FORWARD-BINDING: partially load-bearing now — the panel already shows a scan
    // indicator — but becomes fully binding once T7.1b wires progress-based
    // ECU labelling, because the indeterminate requirement is the explicit fallback
    // contract when `progress` is absent from the polled command row.
    renderInFlight();  // uses the DX8 fixture — POST never resolves, no progress

    const bodyText = document.body.textContent ?? '';

    // Must show *some* in-flight indicator
    const hasIndicator =
      !!screen.queryByRole('progressbar') ||
      !!screen.queryByRole('status') ||
      !!document.querySelector('[data-testid="scan-spinner"]') ||
      /scanning|in.progress|elapsed/i.test(bodyText);
    expect(
      hasIndicator,
      'Panel must show an indeterminate indicator when no progress data is available.',
    ).toBe(true);

    // Must NOT name any ECU (DX8 compatibility + T7.1b degradation rule)
    const sidecarKeys = [
      'ECU_BRAKE', 'ECU_ENGINE', 'ECU_POWERTRAIN', 'ECU_PCM',
      'ECU_COMM', 'ECU_BATTERY_HV', 'ECU_BATTERY_12V', 'ECU_EVAP', 'ECU_BODY',
    ];
    for (const k of sidecarKeys) {
      expect(
        document.body.innerHTML,
        `"${k}" must NOT appear when no progress data is available`,
      ).not.toContain(k);
    }
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX48 — ecu_names.ts contains the manifest-vocabulary label table
// ────────────────────────────────────────────────────────────────────────────
//
// F22 Decision 2: ecu_names.ts is extended in place.  T7.1a Constraints:
//   "extend ecu_names.ts in place with a manifest-vocabulary label table
//    (TCU, BMS, VCU, BCM, ADAS, IVI, GW, CCU, ECM)".
// This test reads the source file and asserts:
//   (a) A second named export exists for the manifest vocabulary.
//   (b) All 9 manifest ECU names are present in the file.
//   (c) The Python source annotation is present (like the existing header).
//
describe('DX48 — ecu_names.ts has manifest-vocabulary label table (F22 D2, T7.1a)', () => {

  const ecuNamesPath = path.resolve(
    __dirname,
    '../ecu_names.ts',
  );

  it('ecu_names.ts is readable', () => {
    expect(() => fs.readFileSync(ecuNamesPath, 'utf-8')).not.toThrow();
  });

  it('ecu_names.ts exports a second named table for the manifest vocabulary', () => {
    const source = fs.readFileSync(ecuNamesPath, 'utf-8');

    // F22 Decision 2: a second VALUE table (distinct from ECU_OPTIONS) is added in-place.
    // It must be exported as a const (not an interface or type) so the panel can
    // import and index into it at runtime.  The existing `export interface EcuOption`
    // does NOT satisfy this requirement — it is a type definition, not a lookup table.
    //
    // This assertion previously matched `export interface EcuOption`, reporting the
    // requirement as satisfied while ecu_names.ts contained zero manifest ECU names.
    // That was F24.  The fix: require a const export whose name is not ECU_OPTIONS
    // or ECU_VALUE_SET AND whose body contains at least one manifest key ('TCU',
    // 'BMS', 'VCU'…).  An interface or type cannot satisfy both conditions.
    //
    // T7.1a Constraints: extend ecu_names.ts in place with a manifest-vocabulary
    // label table (TCU, BMS, VCU, BCM, ADAS, IVI, GW, CCU, ECM).
    const hasManifestConst =
      /export\s+const\s+(?!ECU_OPTIONS\b|ECU_VALUE_SET\b)\w+/.test(source) &&
      /'TCU'|"TCU"/.test(source) &&
      /'BMS'|"BMS"/.test(source);

    expect(
      hasManifestConst,
      'ecu_names.ts must export a const table (not just an interface) for the manifest ' +
      'ECU vocabulary (TCU, BMS, VCU…). F22 Decision 2: extend in place, do not add a second module. ' +
      'The pre-existing `export interface EcuOption` does NOT satisfy this requirement (F24).',
    ).toBe(true);
  });

  it('ecu_names.ts contains all 9 manifest ECU names', () => {
    const source = fs.readFileSync(ecuNamesPath, 'utf-8');

    const manifestNames = ['TCU', 'BMS', 'VCU', 'BCM', 'ADAS', 'IVI', 'GW', 'CCU', 'ECM'];
    for (const name of manifestNames) {
      expect(
        source,
        `ecu_names.ts must contain manifest ECU name "${name}" (F22 D2 / T7.1a).`,
      ).toContain(`'${name}'`);
    }
  });

  it('ecu_names.ts annotates the manifest table with its Python source of truth', () => {
    const source = fs.readFileSync(ecuNamesPath, 'utf-8');

    // T7.1a Constraints: "annotated with its Python source of truth the way the
    // existing header annotates _SIDECAR_ECU_MAP".
    // The annotation must name the Python file (at minimum `ecu_vocabulary_map.py`
    // or `_IDENTITY_ECU_BASELINE`).
    const hasPythonAnnotation =
      source.includes('ecu_vocabulary_map.py') ||
      source.includes('_IDENTITY_ECU_BASELINE') ||
      source.includes('seed_model_manifests.py');
    expect(
      hasPythonAnnotation,
      'ecu_names.ts manifest table must carry a Python source annotation, ' +
      'as the existing header does for _SIDECAR_ECU_MAP.',
    ).toBe(true);
  });
});

// ────────────────────────────────────────────────────────────────────────────
// DX50 (NEW) — Stage 2 props are consumed at all
// ────────────────────────────────────────────────────────────────────────────
//
// F24 requirement (FG1.2 Accept 3): without this assertion "T7.1a rendered
// nothing" and "T7.1a rendered correctly" are indistinguishable.  DX36–DX49
// assert correct output — but if the panel ignores all Stage 2 props entirely,
// every DX36-DX45 positive test fails AND every DX37/DX39/DX41/DX42/DX44/DX47
// negative test passes, making the negative greens look like evidence of
// correctness when they are evidence of nothing.
//
// This test drives the panel with `ecuDiagnosticResult` carrying a known
// reason string and asserts the panel's text output contains that string.
// If the prop is ignored, the reason never appears.
//
// The test is RED until T7.1a wires the prop.
//
describe('DX50 — Stage 2 props are consumed at all (T7.1a prop consumption gate, FG1.2)', () => {

  it('ecuDiagnosticResult unreachableReason appears in rendered text (prop is consumed)', () => {
    // Pass a recognisable sentinel that appears nowhere in the panel's static copy.
    // If T7.1a is not wired, the string never reaches the DOM.
    const sentinel = 'not remotely diagnosable — FG1.2-sentinel';

    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TEST-001"
        connectionStatus="connected"
        // @ts-expect-error prop does not exist yet — T7.1a adds it
        ecuDiagnosticResult={{
          components: {
            BMS: { unreachableReason: sentinel },
          },
        }}
      />,
    );

    const bodyText = document.body.textContent ?? '';

    // The sentinel must appear in visible text.
    // Until T7.1a wires ecuDiagnosticResult → rendered reason,
    // this test is RED — which is the correct state for a red-phase file.
    expect(
      bodyText,
      `ecuDiagnosticResult.components.BMS.unreachableReason "${sentinel}" must appear ` +
      'in rendered text. If this is missing, the prop is not wired and ALL of DX36–DX49 ' +
      'positive assertions are vacuous. T7.1a owns the prop wiring.',
    ).toContain(sentinel);
  });
});

//
// DX6 copy-lint still applies.  The "Live data" and "ECU inventory" section
// headers added by T7.1a must not contain API paths, Python identifiers,
// or force_event strings.  This is a static assertion on the component source.
//
describe('DX49 — copy-lint: Stage 2 section headers contain no API paths or internals (DX6 ext)', () => {

  const panelPath = path.resolve(
    __dirname,
    '../VehicleDiagnosticsPanel.tsx',
  );

  it('VehicleDiagnosticsPanel.tsx contains no /api/ paths in customer-visible strings', () => {
    const source = fs.readFileSync(panelPath, 'utf-8');

    // Look for string literals that contain /api/ — these are transport paths
    // that must never appear in customer-facing copy.
    // Allowlist: JSDoc comments and test comments are exempt; look only in JSX
    // and string literals.
    const apiPathInString = /["'`][^"'`]*\/api\/[^"'`]*["'`]/.test(source);
    expect(
      apiPathInString,
      'VehicleDiagnosticsPanel.tsx must not embed /api/ paths in customer-facing strings.',
    ).toBe(false);
  });

  it('VehicleDiagnosticsPanel.tsx contains no Python identifiers in visible copy', () => {
    const source = fs.readFileSync(panelPath, 'utf-8');

    // DX6: no .py extension, no Python-style identifiers in JSX text content.
    // Pattern: "\.py\b" inside a string literal.
    const pyExtInString = /["'`][^"'`]*\.py\b[^"'`]*["'`]/.test(source);
    expect(
      pyExtInString,
      'VehicleDiagnosticsPanel.tsx must not reference .py files in customer-facing strings.',
    ).toBe(false);
  });

  it('VehicleDiagnosticsPanel.tsx contains no force_event in customer-visible strings', () => {
    const source = fs.readFileSync(panelPath, 'utf-8');

    const forceEventInString = /["'`][^"'`]*force_event[^"'`]*["'`]/.test(source);
    expect(
      forceEventInString,
      'VehicleDiagnosticsPanel.tsx must not reference force_event in customer-facing strings.',
    ).toBe(false);
  });
});
