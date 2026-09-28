// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Vehicle diagnostics — HARD GATE H + progress honesty tests (DX7, DX8).
 *
 * Spec: `.kiro/specs/2026-09-02-cms-diagnostics-platform` § Constraints C1,
 *       § Decision D6, § Test surface rows DX7 and DX8.
 *
 * DX7 — HARD GATE H preservation.
 *   SOVD scan action (Run diagnostic scan) is disabled unless
 *   `connectionStatus === 'connected'` EXACTLY. Near-miss strings that look
 *   "connected-ish" (`'Connected'`, `'connecting'`) must also be disabled —
 *   the gate is an allow-list, not a deny-list. When disabled, the button (or
 *   equivalent control) must provide a reason visible to the user, never just
 *   a grey button with no explanation.
 *
 *   This test asserts the gate is preserved on VehicleDiagnosticsPanel, the
 *   new home for scan actions (Group 3, T3.1). It does NOT alter the gate
 *   logic — C1 forbids relaxing it. It does NOT touch VehicleDTCsTable.tsx
 *   (C4: target diff is zero). It reads the sidecar rate-limiter per-ECU
 *   accounting block to confirm F1 is fixed and the bypass is gone —
 *   that side-effect assertion is in `test('F1 bypass is gone …')` below.
 *
 * DX8 — Honest in-flight progress (D6 honesty, F2 constraint).
 *   F2: the sidecar publishes exactly ONE terminal response per scan. Nothing
 *   is emitted per-ECU mid-scan. Any UI claiming "Scanning 4 of 9 (Powertrain)"
 *   would be fabricating it. Stage 1 therefore MUST NOT name an ECU while a
 *   scan is in flight.
 *   This test asserts:
 *     (a) the in-flight state renders elapsed time and/or an indeterminate
 *         progress indicator, and
 *     (b) no ECU name from `_SIDECAR_ECU_MAP` appears in the rendered output
 *         while the scan is in flight.
 *   Per-ECU naming is deferred to Stage 2 (D7, T6.5) when the sidecar contract
 *   changes. Asserting absence now prevents it from slipping in early via a
 *   "just show the first ECU" shortcut.
 *
 * HARD GATE H source of truth:
 *   `VehicleDTCsTable.tsx:270`  — `const isConnected = connectionStatus === 'connected';`
 *   `VehicleDTCsTable.tsx:83-88` — allow-list documented in JSDoc
 *   This file's test `rate-limiter bypass branch is unchanged` reads those
 *   lines programmatically to prove they have not been weakened.
 *
 * Rate-limiter source of truth:
 *   `services/simulation/realtime_telemetry_simulator.py:569-622`
 *   `is_full_scan` determined at line 569; per-ECU accounting loop at ~590
 *   (charges N tokens for N ECUs; T6.1 fixed F1 bypass).
 *   This test reads the file and asserts the per-ECU accounting is in place.
 *
 * RED PHASE — all tests are expected to FAIL because:
 *   1. `VehicleDiagnosticsPanel` does not exist yet (Group 3, T3.1–T3.3).
 *      The import below will throw "Cannot find module …" — that IS the
 *      expected failure. Do NOT create a stub or proxy to silence it.
 *   2. Where the component does eventually exist, additional assertion failures
 *      may appear until T3.3 is implemented.
 */

import React from 'react';
import { render, screen, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
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

// Imported at top level, NOT via require(). `require` is defined in vitest but does
// not resolve vite's `@` alias, and it bypasses the ESM graph that vi.mock hoists
// into — so a require() here would throw before any assertion ran, and would hand
// back the real module even if it resolved. See decisions.md 2026-09-02.
import { authFetch } from '@/utils/authFetch';

// ── Import under test ────────────────────────────────────────────────────────
//
// VehicleDiagnosticsPanel does not exist yet (Group 3, T3.1).
// This import will fail with "Cannot find module …" — the expected red-phase
// failure. Do NOT create a stub. The tests must fail at import time.
//
import VehicleDiagnosticsPanel from '@/components/vehicles/vehicle-detail/VehicleDiagnosticsPanel';

// ── ECU names that must NOT appear in in-flight UI ───────────────────────────
//
// These are the keys of `_SIDECAR_ECU_MAP` in realtime_telemetry_simulator.py.
// F2: the sidecar emits one terminal response — no per-ECU progress is possible.
// Naming any of these while a scan is in flight is dishonest.
//
const SIDECAR_ECU_NAMES: readonly string[] = [
  'ECU_BRAKE',
  'ECU_ENGINE',
  'ECU_POWERTRAIN',
  'ECU_PCM',
  'ECU_COMM',
  'ECU_BATTERY_HV',
  'ECU_BATTERY_12V',
  'ECU_EVAP',
  'ECU_BODY',
];

// ── Shared props helpers ─────────────────────────────────────────────────────

/** Minimal vehicle prop for the diagnostics panel. */
const makeVehicle = (connectionStatus: string | undefined | null) => ({
  vehicleId: 'VEH-TEST-001',
  vin: 'WBAJB0C51BC782778',
  connectionStatus: connectionStatus as string | undefined,
  make: 'Meridian',
  model: 'Trailwind',
  year: '2023',
});

// ── DX7: HARD GATE H — allow-list, near-miss negatives, reason required ──────

describe('DX7 — HARD GATE H: scan disabled unless connectionStatus === "connected" exactly', () => {

  // ── Cases that MUST be disabled ────────────────────────────────────────────

  it.each([
    // Core failing values
    ['undefined', undefined],
    ['null', null],
    ['empty string', ''],
    ['disconnected', 'disconnected'],
    // Near-miss values that look like "connected" but are not exact matches.
    // DX7 explicitly requires these in the test (spec tasks.md T2.4 Accept).
    ['Connected (capital C)', 'Connected'],
    ['connecting (in-progress)', 'connecting'],
    // Additional casing variants — the gate is === not .toLowerCase()
    ['CONNECTED (uppercase)', 'CONNECTED'],
    ['Connected (title-case)', 'Connected'],
  ])(
    'scan button disabled when connectionStatus = %s',
    (_label, status) => {
      const { container } = render(
        <VehicleDiagnosticsPanel
          vehicleId="VEH-TEST-001"
          connectionStatus={status as string | undefined}
        />,
      );

      // The "Run diagnostic scan" button (or equivalent primary action) must
      // be disabled. Cloudscape may use `aria-disabled="true"` instead of the
      // native `disabled` attribute when `disabledReason` is present.
      const scanBtn = screen.queryByTestId('health-strip-run-scan-button');
      expect(scanBtn, 'Scan button must be present in DOM').not.toBeNull();
      const isDisabled =
        scanBtn!.hasAttribute('disabled') ||
        scanBtn!.getAttribute('aria-disabled') === 'true';
      expect(isDisabled, `Scan button must be disabled when connectionStatus='${status}'`).toBe(true);
    },
  );

  // ── The only value that MUST be enabled ────────────────────────────────────

  it('scan button enabled when connectionStatus === "connected" exactly', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TEST-001"
        connectionStatus="connected"
      />,
    );

    const scanBtn = screen.queryByTestId('health-strip-run-scan-button');
    expect(scanBtn, 'Scan button must be present in DOM').not.toBeNull();
    const isDisabled =
      scanBtn!.hasAttribute('disabled') ||
      scanBtn!.getAttribute('aria-disabled') === 'true';
    expect(isDisabled, 'Scan button must be enabled when connectionStatus="connected"').toBe(false);

    // Exactly one scan control. The first UAT found a second, test-only
    // "Run diagnostic scan" link rendered next to the real button
    // (issue 2026-09-25-diagnostics-ia-uat-defects).
    expect(screen.getAllByRole('button', { name: /run (diagnostic )?scan/i })).toHaveLength(1);
  });

  // ── Disabled state must explain itself (C1: "never just a grey button") ────
  //
  // When the button is disabled, the reason must be communicated to the user.
  // Cloudscape's `disabledReason` prop renders as a tooltip. The pattern in
  // RemoteCommandsPanel.tsx is an inline callout banner — either is acceptable
  // as long as *something* in the rendered output describes why the action is
  // unavailable. An absence of any reason text is the failure mode C1 guards
  // against.
  //

  it.each([
    ['disconnected', 'disconnected'],
    ['undefined', undefined],
    ['near-miss: Connected', 'Connected'],
    ['near-miss: connecting', 'connecting'],
  ])(
    'disabled state states a reason when connectionStatus = %s',
    (_label, status) => {
      const { container } = render(
        <VehicleDiagnosticsPanel
          vehicleId="VEH-TEST-001"
          connectionStatus={status as string | undefined}
        />,
      );

      // The reason may be in any of these forms:
      //   1. An aria-description / aria-describedby on the button
      //   2. A visible callout/alert element near the button
      //   3. A tooltip text rendered as a child
      // We assert at minimum that some DOM content mentions either the
      // disconnected state or instructs the user to reconnect / wait.
      // The exact wording is the component's concern — the test only ensures
      // *something* explains the disabled state.
      const htmlContent = container.innerHTML;
      const hasReason =
        /offline|disconnect|reconnect|vehicle is not connected|unavailable|not connected/i.test(
          htmlContent,
        );
      expect(
        hasReason,
        `Disabled state must show a reason when connectionStatus='${status}'. ` +
        `Found no explanation text in rendered output.`,
      ).toBe(true);
    },
  );
});

// ── DX8: in-flight UI — elapsed/indeterminate, no ECU names ──────────────────

describe('DX8 — scan in-flight: elapsed/indeterminate, must not name an ECU', () => {

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /**
   * Renders the panel with a scan in progress.
   *
   * The component is expected to issue a POST to /api/commands/{vehicleId}
   * (observed from VehicleDiagnose.tsx) and then poll for the result.
   * We freeze the authFetch mock to never resolve so the component stays
   * in the in-flight state, which is exactly what we want to assert against.
   */
  const renderInFlight = () => {
    const authFetchMock = vi.mocked(authFetch);
    // POST returns a pending promise — scan never completes.
    authFetchMock.mockReturnValue(new Promise(() => {}) as never);

    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TEST-001"
        connectionStatus="connected"
      />,
    );

    // Trigger the scan action
    const scanBtn = screen.getByTestId('health-strip-run-scan-button');
    scanBtn.click();

    // Advance timers slightly to let state updates settle (but not enough to
    // time out the 60s ceiling).
    act(() => {
      vi.advanceTimersByTime(1_500);
    });
  };

  it('shows elapsed time or an indeterminate progress indicator while in-flight', () => {
    renderInFlight();

    // Acceptable indicators:
    //   - A spinner (Cloudscape Spinner, aria role="progressbar" or "status")
    //   - A text containing "scanning", "elapsed", "in progress", seconds, or a timer
    //   - An element with aria-busy="true"
    const hasProgressIndicator =
      // A spinner or progressbar
      !!screen.queryByRole('progressbar') ||
      !!screen.queryByRole('status') ||
      // Cloudscape Spinner has no standard role; look for its data-testid or class
      !!document.querySelector('[class*="spinner"]') ||
      !!document.querySelector('[data-testid="scan-spinner"]') ||
      // A text node mentioning the scan state or elapsed time
      /scanning|elapsed|in.progress|[0-9]+\s*s(ec)?/i.test(
        document.body.textContent ?? '',
      ) ||
      // aria-busy on the button or container
      !!document.querySelector('[aria-busy="true"]');

    expect(
      hasProgressIndicator,
      'In-flight scan must show elapsed time or an indeterminate progress indicator. ' +
      'Found none of: role=progressbar, role=status, spinner element, ' +
      '"scanning/elapsed/in progress/Ns" text, or aria-busy="true".',
    ).toBe(true);
  });

  it('does NOT name any ECU while scan is in flight (F2 honesty, D6)', () => {
    renderInFlight();

    // F2: the sidecar emits exactly one terminal response. Nothing per-ECU is
    // published during the scan. Naming an ECU while in-flight is fabrication.
    // Per spec D6: "must not name an ECU it cannot know".
    // This test is categorical: none of the 9 sidecar ECU identifiers may
    // appear in visible DOM text content while the scan has not yet completed.
    //
    // NOTE: ECU names in comments, `data-testid`s, or aria-labels intended
    // for screen-reader-only content are also prohibited during in-flight state
    // because a screen reader would narrate them, creating the same false
    // impression of per-ECU progress.

    const bodyText = document.body.textContent ?? '';
    const bodyHtml = document.body.innerHTML;

    for (const ecuName of SIDECAR_ECU_NAMES) {
      // Check visible text content
      expect(
        bodyText,
        `ECU name "${ecuName}" must NOT appear in rendered text while scan is in flight. ` +
        `F2: the sidecar publishes one terminal response — per-ECU progress is not available. ` +
        `"Scanning 4 of 9 (${ecuName})" would be fabricated.`,
      ).not.toContain(ecuName);

      // Also check HTML to catch aria-label / title / placeholder text
      expect(
        bodyHtml,
        `ECU name "${ecuName}" must NOT appear anywhere in the rendered HTML ` +
        `(including aria-label, title, placeholder) while scan is in flight.`,
      ).not.toContain(ecuName);
    }
  });

  it('does NOT claim a specific count of ECUs scanned (e.g. "Scanning 4 of 9")', () => {
    renderInFlight();

    // A "Scanning N of M" message implies per-ECU progress, which does not exist
    // at Stage 1. This assertion catches any attempt to synthesize it.
    const bodyText = document.body.textContent ?? '';
    expect(
      /scanning\s+\d+\s+of\s+\d+/i.test(bodyText),
      'Must not claim a specific ECU count mid-scan ("Scanning N of M"). ' +
      'F2: the sidecar publishes one terminal response — no intermediate ECU count is available.',
    ).toBe(false);
  });
});

// ── HARD GATE H preservation in VehicleDTCsTable.tsx (source audit) ──────────
//
// C4: VehicleDTCsTable.tsx target diff is ZERO for this spec.
// This test reads the source file and asserts the allow-list gate expression
// has not been weakened. It is a static assertion, not a render test.
// If the gate is removed or softened, this test fails immediately.
//

describe('HARD GATE H source integrity — VehicleDTCsTable.tsx must be unmodified', () => {

  const vehicleDTCsTablePath = path.resolve(
    __dirname,
    '../VehicleDTCsTable.tsx',
  );

  it('VehicleDTCsTable.tsx still contains the allow-list gate expression', () => {
    const source = fs.readFileSync(vehicleDTCsTablePath, 'utf-8');

    // The gate expression must still read exactly:
    //   const isConnected = connectionStatus === 'connected';
    // Any weakening (e.g. !== 'disconnected', ?.includes, || true) fails this test.
    expect(
      source,
      'HARD GATE H expression must be present and unmodified in VehicleDTCsTable.tsx',
    ).toMatch(/const isConnected\s*=\s*connectionStatus\s*===\s*'connected'/);
  });

  it('VehicleDTCsTable.tsx still documents HARD GATE H in its JSDoc', () => {
    const source = fs.readFileSync(vehicleDTCsTablePath, 'utf-8');

    expect(
      source,
      'HARD GATE H JSDoc comment must still be present in VehicleDTCsTable.tsx',
    ).toContain('HARD GATE H');
  });

  it('VehicleDTCsTable.tsx does not use a deny-list (!=== disconnected) in place of the allow-list', () => {
    const source = fs.readFileSync(vehicleDTCsTablePath, 'utf-8');

    // A deny-list gate (`connectionStatus !== 'disconnected'`) fails open on
    // undefined / null / 'Connected' — the exact cases DX7 guards against.
    // The gate must remain an allow-list (=== 'connected').
    const denyListPattern = /connectionStatus\s*!==?\s*['"]disconnected['"]/;
    expect(
      denyListPattern.test(source),
      'VehicleDTCsTable.tsx must not use a deny-list gate (connectionStatus !== "disconnected"). ' +
      'The allow-list (=== "connected") must be preserved.',
    ).toBe(false);
  });
});

// ── Per-ECU rate-limiter accounting (sidecar, F1 fix — T6.1 shipped) ─────────
//
// C2: rate limiting must strengthen, never weaken.
// F1 FIX (T6.1): the pre-fix bypass (token bucket gated on `if is_full_scan:`)
//   is gone. Per-ECU accounting is now in place: a request targeting N ECUs
//   costs N tokens whether packaged as one full-scan or N single-ECU calls.
// D13: the `is_full_scan` variable is preserved in the sidecar source — it
//   shapes ECU-resolution logic and log messages — but it MUST NOT gate the
//   accounting. The accounting loop runs unconditionally.
//
// This describe block asserts the per-ECU accounting is in place.
// It reads the sidecar source file to confirm:
//   1. `is_full_scan` variable still exists (D13 preservation requirement).
//   2. `token_bucket.consume()` is still present (accounting still happens).
//   3. The F1 bypass guard (`if is_full_scan:` gating `token_bucket.consume()`)
//      is GONE — the rate-limiting loop now runs unconditionally.
//   4. The per-ECU accounting comment is present (documents D13 fix).
//   5. The RATE_LIMITED response shape is unchanged (frontend renders it identically).
//

describe('Per-ECU accounting in place (T6.1 shipped, F1 bypass gone)', () => {

  const simulatorPath = path.resolve(
    __dirname,
    // 9 levels: __tests__ → vehicle-detail → vehicles → components → src →
    // frontend → source → cms_ui → modules → repo root. The original 8 landed on
    // modules/services/… which does not exist, so every assertion in this block
    // failed on the read rather than on the property it names. Re-anchored, not
    // deleted (see decisions.md 2026-09-02).
    '../../../../../../../../../services/simulation/realtime_telemetry_simulator.py',
  );

  it('realtime_telemetry_simulator.py file is readable', () => {
    expect(
      () => fs.readFileSync(simulatorPath, 'utf-8'),
      'Cannot read realtime_telemetry_simulator.py — check the relative path in this test',
    ).not.toThrow();
  });

  it('is_full_scan variable still present (D13: preserved for ECU-resolution, not for gating accounting)', () => {
    const source = fs.readFileSync(simulatorPath, 'utf-8');

    // D13 preservation: the is_full_scan variable must still exist in the
    // source so it can continue to shape ECU-resolution logic and log messages.
    // Its presence does NOT mean the accounting is still gated on it.
    expect(
      source,
      'is_full_scan determination must still be present in realtime_telemetry_simulator.py ' +
      '(D13: preserved for ECU-resolution and log messages)',
    ).toMatch(/is_full_scan\s*=\s*\(components_req\s*==\s*\['\*'\]\s*or\s*components_req\s*==\s*\['ALL'\]\)/);
  });

  it('token_bucket.consume() is present and per-ECU accounting loop is used (F1 fix, D13)', () => {
    const source = fs.readFileSync(simulatorPath, 'utf-8');

    // token_bucket.consume() must still be called (accounting still happens).
    expect(source).toContain('token_bucket.consume()');

    // The per-ECU accounting loop must be present.
    // Pattern: `for _i in range(_ecu_count):` wrapping the consume() call.
    expect(
      source,
      'Per-ECU accounting loop must be present (D13: for _i in range(_ecu_count))',
    ).toMatch(/for\s+_i\s+in\s+range\s*\(\s*_ecu_count\s*\)/);
  });

  it('F1 bypass is gone: token_bucket.consume() is NOT gated by `if is_full_scan:` for accounting', () => {
    const source = fs.readFileSync(simulatorPath, 'utf-8');

    // Pre-fix: the rate-limiting block was wrapped in `if is_full_scan:`.
    // The F1 bypass. This pattern must be gone.
    //
    // We detect the bypass pattern as: `if is_full_scan:` immediately preceding
    // the rate-limiting block (with only whitespace/comments between them).
    // Specifically: `if is_full_scan:\n        if not token_bucket.consume():`
    // Post-fix this becomes an unconditional loop, no `if is_full_scan:` wrapper.
    const bypassPattern = /if is_full_scan:\s+if not token_bucket\.consume\(\)/s;
    expect(
      bypassPattern.test(source),
      'F1 bypass must be gone: `if is_full_scan:` must NOT immediately gate ' +
      '`if not token_bucket.consume()`. The accounting loop must be unconditional. ' +
      'If this test fails, the F1 bypass was re-introduced.',
    ).toBe(false);
  });

  it('per-ECU accounting comment is present (documents D13 fix)', () => {
    const source = fs.readFileSync(simulatorPath, 'utf-8');

    // The comment block marking the per-ECU accounting must exist.
    // Pre-fix comment: '# ── Rate limiting for full scans'
    // Post-fix comment: '# ── Rate limiting per-ECU (HARD GATE F, D13 fix)'
    expect(
      source,
      'Per-ECU accounting comment must be present (documents D13 fix)',
    ).toContain('# ── Rate limiting per-ECU');
  });

  it('the RATE_LIMITED response shape is unchanged (frontend renders it identically)', () => {
    const source = fs.readFileSync(simulatorPath, 'utf-8');

    // The rate-limiter must still emit a RATE_LIMITED response with retry_after_ms.
    expect(
      source,
      'RATE_LIMITED response status must still be produced by the rate limiter',
    ).toContain("'status': 'RATE_LIMITED'");

    expect(
      source,
      'retry_after_ms must still be present in the RATE_LIMITED response',
    ).toContain("'retry_after_ms'");
  });
});
