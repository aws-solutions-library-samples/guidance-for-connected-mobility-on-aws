// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Vehicle diagnostics — verdict safety + state tests (DX1, DX4, DX5).
 *
 * Spec: `.kiro/specs/2026-09-02-cms-diagnostics-platform` § Tier-classification Seam 1,
 *       and test-ID table rows DX1, DX4, DX5.
 *
 * Cases:
 *  DX1: P0 finding → catalog.description present as exact verbatim substring in
 *       visible DOM text content. Fails on paraphrase, truncation, or relocation
 *       into title/aria-label only. The P0 sentence is sourced from the same
 *       catalog fixture the component consumes — NOT hardcoded — so a wording
 *       change in the fixture propagates to the assertion automatically.
 *       This is the Seam 1 safety boundary in executable form.
 *
 *  DX4: Undetermined state (catalog lookup inconclusive, e.g. mixed/absent
 *       severity hints) must never render the string "No issues found", because
 *       that string is only true when the verdict is confirmed Healthy.
 *
 *  DX5: Never-scanned state (vehicle has no diagnostic history at all) must not
 *       render as Healthy. Zero stored DTCs with no scan run is not the same as
 *       a clean bill of health.
 *
 * RED PHASE — all tests are expected to FAIL. Do not build the implementation
 * to make them pass. The component at the import path below does not exist;
 * that is intentional.
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

// ── Module mocks ─────────────────────────────────────────────────────────────
// Added in Task 4.1 refactoring: VehicleDiagnosticsPanel now renders VehicleDTCsTable
// in list view as a peer table (spec § Design 3). VehicleDTCsTable calls useAuth,
// which requires a provider. We mock it here to prevent auth context errors in
// DX1/DX4/DX5 tests that only verify verdict rendering.
// (Rationale: the original panel had VehicleDTCsTable behind a collapsed ExpandableSection
// that was never expanded in these tests; the new panel promotes it to a peer table.
// Adding the mock is the correct fix — not hiding the DTC table — per decisions.md
// Task 4.1 rationale entry: standing rule 2 test update documented there.)

vi.mock('@/auth/useAuth', () => ({
  useAuth: () => ({
    getAuthHeaders: () => ({ Authorization: 'Bearer test-token' }),
    user: {
      username: 'operator@example.com',
      email: 'operator@example.com',
      groups: ['fleet-operator'],
    },
  }),
}));

vi.mock('@/config/api', () => ({
  getApiEndpoint: () => 'http://localhost/',
  getRuntimeConfig: () => ({ apiEndpoint: 'http://localhost/' }),
}));

vi.mock('@/utils/authFetch', () => ({
  authFetch: vi.fn().mockResolvedValue({
    ok: false, status: 404, json: async () => ({}),
  } as Response),
}));

vi.mock('@/utils/simulation-config', () => ({
  getSimulationApiUrl: (p: string) => `http://localhost/api/simulation${p}`,
  getSimulationApiBase: () => 'http://localhost',
  getSimulationMode: () => 'local',
  isCloudSimAvailable: () => false,
  setSimulationMode: () => {},
}));

vi.mock('@/utils/sovdScanClient', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/sovdScanClient')>();
  return {
    ...actual,
    fetchSessionCommands: vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => ({ commands: [] }),
    } as Response),
    fetchAllSovdCommands: vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => ({ commands: [] }),
    } as Response),
    fetchRoutines: vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => ({ routines: [] }),
    } as Response),
  };
});

vi.mock('@/hooks/useDealerOptions', () => ({
  useDealerOptions: vi.fn(() => ({
    options: [], status: 'loading' as const, errorMessage: '', reload: vi.fn(),
  })),
  dealerPlaceholder: (_s: string) => 'Choose a service centre…',
}));

// ── Import under test ────────────────────────────────────────────────────────
//
// VehicleDiagnosticsPanel does not exist yet (Group 3, T3.1–T3.2).
// This import will fail with "Cannot find module …" — that is the expected
// red-phase failure. Do not create a stub or mock to silence it.
//
import VehicleDiagnosticsPanel from '@/components/vehicles/vehicle-detail/VehicleDiagnosticsPanel';

// ── Catalog fixture ──────────────────────────────────────────────────────────
//
// These objects mirror the `GET /api/v1/event-catalog` item shape documented in
// docs/tech.md § "GET /api/v1/event-catalog". The component receives the catalog
// as a prop (or fetches it); tests pass the catalog directly so they own the data.
//
// DX1 CRITICAL CONSTRAINT: the description for P0 entries is written here as the
// source of truth. The test assertion sources the expected string from
// `P0_CATALOG_ENTRY.description` at assertion time. If the description is ever
// updated in this fixture, the assertion updates automatically. Never paste the
// description as a string literal in the expect() call.
//

interface CatalogEntry {
  event_id: string;
  category: string;
  severity: number;        // 4 = CRITICAL (P0), 3 = HIGH (P1), 2 = MEDIUM, 1 = LOW
  severity_hint: string;   // P0 / P1 / P2 / P3
  description: string;     // The text narrated verbatim to the operator.
  trigger_signal: string;
  threshold_operator: string;
  threshold_value: number;
  dtc_code: string;
  [key: string]: unknown;  // index signature to match VehicleDiagnosticsPanelProps.catalog
}

// P0 entry — stop-driving severity. DX1 asserts this description is rendered
// verbatim in visible DOM text. It is a safety instruction, not a hint.
// The exact wording comes from deployment/scripts/seed_event_catalog.py —
// look for entries where severity_hint == 'P0'. This fixture duplicates
// that wording so the component contract is independently testable.
const P0_CATALOG_ENTRY: CatalogEntry = {
  event_id: 'OVERHEAT_ENGINE_CRITICAL',
  category: 'safety',
  severity: 4,
  severity_hint: 'P0',
  description: 'Engine temperature critically high. Stop the vehicle immediately and allow engine to cool before driving.',
  trigger_signal: 'engine_temp',
  threshold_operator: '>',
  threshold_value: 130,
  dtc_code: 'P0217',
};

// P1 entry — high severity, not stop-driving. Used to verify that DX1's
// verbatim-render requirement applies specifically to P0 and the test
// is not over-reaching to all severities.
const P1_CATALOG_ENTRY: CatalogEntry = {
  event_id: 'BATTERY_VOLTAGE_LOW',
  category: 'safety',
  severity: 3,
  severity_hint: 'P1',
  description: 'Battery voltage is below recommended operating range. Schedule service soon.',
  trigger_signal: 'battery_voltage',
  threshold_operator: '<',
  threshold_value: 11.5,
  dtc_code: 'P0562',
};

const TEST_CATALOG: CatalogEntry[] = [P0_CATALOG_ENTRY, P1_CATALOG_ENTRY];

// ── DTC scan result fixtures ─────────────────────────────────────────────────

// A scan result containing a P0 DTC — i.e. a code that maps to P0_CATALOG_ENTRY.
const P0_SCAN_RESULT = {
  vehicleId: 'VEH-TEST-001',
  scannedAt: '2026-09-02T12:00:00Z',
  dtcs: [
    {
      code: P0_CATALOG_ENTRY.dtc_code,   // 'P0217'
      status: 'CONFIRMED_DTC',
      ecu: 'ECU_ENGINE',
    },
  ],
};

// A scan result containing mixed-severity codes where none have a severity_hint
// — the verdict cannot be determined with confidence.
const UNDETERMINED_SCAN_RESULT = {
  vehicleId: 'VEH-TEST-002',
  scannedAt: '2026-09-02T12:00:00Z',
  dtcs: [
    {
      code: 'U9999',    // not in catalog — no severity_hint available
      status: 'CONFIRMED_DTC',
      ecu: 'ECU_COMM',
    },
  ],
};

// A "never-scanned" state — the vehicle has no scan history at all.
// This is structurally different from a scan that returned zero DTCs.
const NEVER_SCANNED_RESULT = null;   // null means: no scan has ever been run.

// ── Component props factory ──────────────────────────────────────────────────

interface PanelProps {
  vehicleId: string;
  connectionStatus: string;
  catalog: CatalogEntry[];
  // null = never scanned; object = most recent scan result
  latestScanResult: typeof P0_SCAN_RESULT | typeof UNDETERMINED_SCAN_RESULT | null;
}

function makePanelProps(overrides: Partial<PanelProps> = {}): PanelProps {
  return {
    vehicleId: 'VEH-TEST-001',
    connectionStatus: 'connected',
    catalog: TEST_CATALOG,
    latestScanResult: P0_SCAN_RESULT,
    ...overrides,
  };
}

// ── Tests ────────────────────────────────────────────────────────────────────

describe('VehicleDiagnosticsPanel — verdict safety + state tests (DX1, DX4, DX5)', () => {

  // ── DX1 — P0 description verbatim in visible DOM ──────────────────────────
  //
  // The Seam 1 safety boundary. When a P0 DTC is present, the catalog's
  // description must appear as an exact substring of visible DOM text content —
  // not in title, aria-label, or any attribute-only position.
  //
  // "Exact substring" means: every character of P0_CATALOG_ENTRY.description
  // must appear consecutively in the textContent of some rendered DOM node.
  // Paraphrase is a failure. Truncation is a failure.
  //
  // The expected string is sourced from the fixture at assertion time:
  //   expect(...).toContain(P0_CATALOG_ENTRY.description)
  // NOT from a hardcoded string literal.
  //
  it('DX1: P0 DTC → catalog.description appears verbatim in visible DOM textContent', () => {
    const props = makePanelProps({ latestScanResult: P0_SCAN_RESULT });
    const { container } = render(<VehicleDiagnosticsPanel {...props} />);

    // Walk visible text content — exclude title/aria-label attributes.
    // textContent aggregates all descendant text nodes in render order.
    const visibleText = container.textContent ?? '';

    // DX1 core assertion: sourced from fixture, never from a hardcoded literal.
    expect(visibleText).toContain(P0_CATALOG_ENTRY.description);

    // Negative control: ensure the assertion would NOT pass on a paraphrase.
    // If the component renders "Stop vehicle immediately" instead of the full
    // sentence, this next assertion would still pass (substring of a paraphrase
    // is not necessarily absent), but the textContent.includes guard above catches
    // the actual paraphrase when the full sentence is absent.
    //
    // We additionally assert that the full sentence is NOT trimmed to its first
    // clause — a truncated description passes the substring test only if the
    // truncated portion happens to appear in the description, so we add a check
    // on a portion from the latter half of the sentence:
    const secondHalf = P0_CATALOG_ENTRY.description.slice(
      Math.floor(P0_CATALOG_ENTRY.description.length / 2),
    );
    expect(visibleText).toContain(secondHalf);
  });

  it('DX1: P0 description must NOT appear only in an aria-label or title attribute', () => {
    const props = makePanelProps({ latestScanResult: P0_SCAN_RESULT });
    render(<VehicleDiagnosticsPanel {...props} />);

    // Confirm the P0 description is visible in the document body text,
    // not hidden inside an attribute. getByText uses accessible text and
    // respects aria-hidden — but we need exact DOM text, so use getAllByText
    // with an exact-match regex derived from the fixture.
    //
    // Escape the description for use as a regex pattern.
    const escapedDesc = P0_CATALOG_ENTRY.description.replace(
      /[.*+?^${}()|[\]\\]/g,
      '\\$&',
    );
    // If the description is rendered as text content, this should find at least
    // one element. If it only lives in an attribute, getAllByText throws — which is
    // the intended failure mode, carried by the query rather than by the length
    // assertion. (`toHaveLength` rejects asymmetric matchers; see decisions.md
    // 2026-09-02.)
    expect(screen.getAllByText(new RegExp(escapedDesc)).length).toBeGreaterThan(0);
    // The returned elements must be visible (not aria-hidden).
    const matchingEls = screen.getAllByText(new RegExp(escapedDesc));
    matchingEls.forEach((el) => {
      expect(el).toBeVisible();
    });
  });

  // ── DX4 — Undetermined never renders "No issues found" ────────────────────
  //
  // When the verdict cannot be determined (unknown DTCs, missing catalog entries,
  // ambiguous severity), the component must NOT render the string "No issues
  // found". That string is only appropriate when the scan confirmed zero issues,
  // and rendering it in an undetermined state is a safety-relevant false positive.
  //
  it('DX4: Undetermined state — does not render "No issues found"', () => {
    const props = makePanelProps({ latestScanResult: UNDETERMINED_SCAN_RESULT });
    render(<VehicleDiagnosticsPanel {...props} />);

    expect(screen.queryByText(/No issues found/i)).not.toBeInTheDocument();
  });

  it('DX4: Undetermined state — renders an "Undetermined" or equivalent indicator (not Healthy)', () => {
    const props = makePanelProps({ latestScanResult: UNDETERMINED_SCAN_RESULT });
    render(<VehicleDiagnosticsPanel {...props} />);

    // The component must surface that the verdict is uncertain.
    // Accept "Undetermined" or "Unknown" or "Could not determine" but not
    // "Healthy" or "No issues".
    const bodyText = document.body.textContent ?? '';
    expect(bodyText).toMatch(/Undetermined|Unknown|Could not determine/i);
    expect(bodyText).not.toMatch(/No issues found/i);
    expect(bodyText).not.toMatch(/\bHealthy\b/i);
  });

  // ── DX5 — Never-scanned ≠ Healthy ─────────────────────────────────────────
  //
  // A vehicle that has never had a diagnostic scan run has no information
  // to base a health verdict on. Rendering it as Healthy is a false positive.
  // The component must distinguish "no scan data" from "scan found no issues".
  //
  it('DX5: Never-scanned state — does not render "Healthy" or "No issues found"', () => {
    const props = makePanelProps({ latestScanResult: NEVER_SCANNED_RESULT });
    render(<VehicleDiagnosticsPanel {...props} />);

    const bodyText = document.body.textContent ?? '';
    expect(bodyText).not.toMatch(/\bHealthy\b/i);
    expect(bodyText).not.toMatch(/No issues found/i);
  });

  it('DX5: Never-scanned state — renders a "not scanned yet" or equivalent indicator', () => {
    const props = makePanelProps({ latestScanResult: NEVER_SCANNED_RESULT });
    render(<VehicleDiagnosticsPanel {...props} />);

    // The UI must communicate that no scan has run, not that the vehicle is fine.
    const bodyText = document.body.textContent ?? '';
    expect(bodyText).toMatch(/no scan|not scanned|never scanned|run a scan/i);
  });
});
