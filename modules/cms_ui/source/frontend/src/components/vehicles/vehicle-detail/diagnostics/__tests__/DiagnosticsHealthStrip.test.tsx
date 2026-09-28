// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/// <reference types="@testing-library/jest-dom/vitest" />

/**
 * DiagnosticsHealthStrip — Task 3.1 tests.
 *
 * Spec: `.kiro/specs/2026-09-24-cms-diagnostics-tab-ia-redesign` Task 3.1
 *
 * Verify contract:
 *  1. Renders connection state via StatusIndicator
 *  2. Renders open fault count with highest severity
 *  3. Renders last scan age as relative time (or "none found" when absent)
 *  4. Renders recommended action derived from computeVerdict — NOT hardcoded
 *  5. Missing lastScanAt renders "none found" — NOT "0 minutes ago"
 *  6. Run scan button is present; disabled when not connected
 *  7. Dispatch button is present for fleet-operator with vin
 *  8. Dispatch button absent when vin is absent
 *  9. Absent fault count renders "Not scanned yet" — NOT "0 open faults"
 * 10. While scan history is loading or unavailable, no health claim is made
 *     (issue 2026-09-25-diagnostics-ia-uat-defects)
 *
 * Mutation-verification note (Verify step 2):
 *   The test "recommended action derives from verdict logic, not a hardcoded
 *   string" was mutated by replacing verdictToRecommendedAction() with a
 *   hardcoded string in the component under test. That mutation made this test
 *   FAIL — confirming the test is sensitive to the verdict source, not just
 *   the presence of any text.
 *   Recorded in decisions.md (2026-09-24 — Task 3.1 mutation).
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Module mocks (must precede component imports) ────────────────────────────

vi.mock('@/auth/useAuth', () => ({
  useAuth: () => ({
    getAuthHeaders: () => ({ Authorization: 'Bearer test-token' }),
    user: { email: 'test@example.com', groups: ['fleet-operator'] },
  }),
}));

vi.mock('@/config/api', () => ({
  getApiEndpoint: () => 'http://localhost/',
  getRuntimeConfig: () => ({ apiEndpoint: 'http://localhost/' }),
}));

vi.mock('@/utils/authFetch', () => ({
  authFetch: vi.fn(),
}));

vi.mock('@/hooks/useDealerOptions', () => ({
  useDealerOptions: () => ({ options: [], status: 'finished', errorMessage: null }),
  dealerPlaceholder: () => 'Select a service centre',
}));

// ── Imports ──────────────────────────────────────────────────────────────────

import DiagnosticsHealthStrip, {
  formatRelativeAge,
  verdictToRecommendedAction,
} from '../DiagnosticsHealthStrip';
import type { DiagnosticsHealthStripProps } from '../DiagnosticsHealthStrip';
import { computeVerdict } from '@/utils/diagnosticsVerdict';
import type { DispatchEvidence } from '../../DispatchModal';

// ── Fixtures ─────────────────────────────────────────────────────────────────

const baseEvidence: DispatchEvidence = {
  sessionId: 'sess-001',
  dtcs: [],
  routinesRun: [],
  notes: [],
};

const baseProps: DiagnosticsHealthStripProps = {
  vehicleId: 'veh-001',
  connectionStatus: 'connected',
  scanHistory: 'loaded',
  scanResults: 'complete',
  onRunScan: vi.fn(),
  dispatchEvidence: baseEvidence,
  onDispatchSuccess: vi.fn(),
};

// ── Helper ───────────────────────────────────────────────────────────────────

function renderStrip(props: Partial<DiagnosticsHealthStripProps> = {}) {
  return render(<DiagnosticsHealthStrip {...baseProps} {...props} />);
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('DiagnosticsHealthStrip', () => {

  // ── Connection state ────────────────────────────────────────────────────────

  describe('connection state', () => {
    it('renders "Connected" when connectionStatus is "connected"', () => {
      renderStrip({ connectionStatus: 'connected' });
      expect(screen.getByTestId('health-strip-connection')).toHaveTextContent('Connected');
    });

    it('renders connection status text when not connected', () => {
      renderStrip({ connectionStatus: 'disconnected' });
      const indicator = screen.getByTestId('health-strip-connection');
      expect(indicator).toBeInTheDocument();
      // Should NOT say "Connected"
      expect(indicator).not.toHaveTextContent(/^Connected$/);
    });

    it('says the connection status is unavailable when connectionStatus is absent', () => {
      renderStrip({ connectionStatus: undefined });
      const indicator = screen.getByTestId('health-strip-connection');
      expect(indicator).toHaveTextContent('Connection status unavailable');
      expect(indicator).not.toHaveTextContent(/^Connected$/);
    });

    it('disables Run scan when not connected', () => {
      renderStrip({ connectionStatus: 'disconnected' });
      const btn = screen.getByTestId('health-strip-run-scan-button');
      expect(btn).toBeDisabled();
    });

    it('enables Run scan when connected', () => {
      renderStrip({ connectionStatus: 'connected' });
      const btn = screen.getByTestId('health-strip-run-scan-button');
      expect(btn).not.toBeDisabled();
    });

    it('disables Run scan while scanning', () => {
      renderStrip({ connectionStatus: 'connected', scanning: true });
      const btn = screen.getByTestId('health-strip-run-scan-button');
      expect(btn).toBeDisabled();
    });
  });

  // ── Fault count ─────────────────────────────────────────────────────────────

  describe('open fault count', () => {
    it('renders fault count with severity when both are present', () => {
      renderStrip({ openFaultCount: 3, highestSeverity: 'P1' });
      const faults = screen.getByTestId('health-strip-faults');
      expect(faults).toHaveTextContent('3');
      expect(faults).toHaveTextContent('P1');
    });

    it('renders "Not scanned yet" when openFaultCount is absent — NEVER "0 open faults"', () => {
      renderStrip({ openFaultCount: undefined, highestSeverity: undefined });
      const faults = screen.getByTestId('health-strip-faults');
      expect(faults).toHaveTextContent('Not scanned yet');
      expect(faults).not.toHaveTextContent('0 open faults');
      expect(faults).not.toHaveTextContent('0 fault');
    });

    it('renders "No open faults" for a scan that found none', () => {
      renderStrip({ openFaultCount: 0 });
      expect(screen.getByTestId('health-strip-faults')).toHaveTextContent('No open faults');
    });

    it('omits severity from fault text when severity is unrecognised', () => {
      renderStrip({ openFaultCount: 2, highestSeverity: undefined });
      const faults = screen.getByTestId('health-strip-faults');
      expect(faults).toHaveTextContent('2 open faults');
      expect(faults).not.toHaveTextContent('(');
    });
  });

  // ── Last scan age ───────────────────────────────────────────────────────────

  describe('last scan age', () => {
    it('renders "none found" when lastScanAt is absent — NEVER "0 minutes ago"', () => {
      renderStrip({ lastScanAt: undefined });
      const el = screen.getByTestId('health-strip-last-scan');
      expect(el).toHaveTextContent('Last scan: none found');
      expect(el).not.toHaveTextContent('0 minutes ago');
      expect(el).not.toHaveTextContent('0 min ago');
    });

    it('renders relative time when lastScanAt is present', () => {
      // 14 minutes ago
      const fourteenMinAgo = Date.now() - 14 * 60 * 1000;
      renderStrip({ lastScanAt: fourteenMinAgo });
      const el = screen.getByTestId('health-strip-last-scan');
      expect(el).toHaveTextContent('14 min ago');
    });

    it('renders "time unavailable" for an unparseable ISO string', () => {
      renderStrip({ lastScanAt: 'not-a-date' });
      const el = screen.getByTestId('health-strip-last-scan');
      expect(el).toHaveTextContent('Last scan: time unavailable');
    });
  });

  // ── Recommended action ──────────────────────────────────────────────────────

  describe('recommended action', () => {
    it('derives the recommended action from computeVerdict — not a hardcoded string', () => {
      // Provide two distinct DTC scenarios and assert the text differs.
      // If the text were hardcoded, both renders would produce the same output.

      // Scenario A: healthy vehicle (no DTCs)
      const { unmount } = renderStrip({ dtcs: [], catalog: [] });
      const healthyText = screen.getByTestId('health-strip-recommended-action').textContent ?? '';
      unmount();

      // Scenario B: P0 fault
      const catalog = [
        { dtc_code: 'P0420', severity_hint: 'P0', description: 'Catalyst system efficiency below threshold — stop driving.' },
      ];
      const dtcs = [{ code: 'P0420', status: 'active', ecu: 'ECU1' }];
      renderStrip({ dtcs, catalog });
      const p0Text = screen.getByTestId('health-strip-recommended-action').textContent ?? '';

      // The two texts must be different — hardcoding would make them equal
      expect(healthyText).not.toBe(p0Text);

      // And the P0 text must contain the catalog description verbatim
      expect(p0Text).toContain('Catalyst system efficiency below threshold — stop driving.');
    });

    it('renders "Run a scan" copy when dtcs prop is absent (no scan run yet)', () => {
      renderStrip({ dtcs: undefined, catalog: undefined });
      const el = screen.getByTestId('health-strip-recommended-action');
      expect(el).toHaveTextContent('Run a scan');
    });

    it('renders healthy copy when dtcs is empty array', () => {
      renderStrip({ dtcs: [], catalog: [] });
      const el = screen.getByTestId('health-strip-recommended-action');
      expect(el).toHaveTextContent('No issues found');
    });

    it('renders "Undetermined" copy for unrecognised DTC codes', () => {
      renderStrip({ dtcs: [{ code: 'U9999', status: 'active' }], catalog: [] });
      const el = screen.getByTestId('health-strip-recommended-action');
      expect(el).toHaveTextContent('Undetermined');
    });
  });

  // ── Scan history not yet authoritative ──────────────────────────────────────
  //
  // The first UAT saw "No issues found — vehicle health looks good." next to two
  // "unknown"s: a health claim with no scan data behind it. While the history is
  // loading or failed to load, the strip must make no claim at all, even if the
  // caller passes an empty DTC list.

  describe('scan history loading or unavailable', () => {
    it('makes no health claim while loading, even with dtcs=[]', () => {
      renderStrip({ scanHistory: 'loading', dtcs: [], catalog: [], openFaultCount: 0, lastScanAt: Date.now() });
      expect(screen.getByTestId('health-strip-recommended-action')).not.toHaveTextContent(/No issues found/i);
      expect(screen.getByTestId('health-strip-recommended-action')).toHaveTextContent('Checking');
      expect(screen.getByTestId('health-strip-faults')).toHaveTextContent('Checking for fault codes');
      expect(screen.getByTestId('health-strip-last-scan')).toHaveTextContent('Last scan: checking');
    });

    it('makes no health claim when unavailable, and says it could not load', () => {
      renderStrip({ scanHistory: 'unavailable', dtcs: [], catalog: [], openFaultCount: 0 });
      const action = screen.getByTestId('health-strip-recommended-action');
      expect(action).not.toHaveTextContent(/No issues found/i);
      expect(action).toHaveTextContent("Couldn't load scan history");
      expect(screen.getByTestId('health-strip-faults')).toHaveTextContent('Fault codes unavailable');
      expect(screen.getByTestId('health-strip-last-scan')).toHaveTextContent('Last scan: unavailable');
    });

    it('never renders the bare word "unknown" in any state', () => {
      for (const scanHistory of ['loading', 'unavailable', 'loaded'] as const) {
        const { unmount, container } = renderStrip({ scanHistory, connectionStatus: undefined });
        expect(container.textContent ?? '').not.toMatch(/\bunknown\b/i);
        unmount();
      }
    });
  });

  // ── Dispatch button ─────────────────────────────────────────────────────────

  describe('dispatch button', () => {
    it('renders dispatch button for fleet-operator when vin is present', () => {
      renderStrip({ callerGroups: ['fleet-operator'], vin: 'VIN123' });
      expect(screen.getByTestId('dispatch-to-service-button')).toBeInTheDocument();
    });

    it('renders dispatch button for platform-admin when vin is present', () => {
      renderStrip({ callerGroups: ['platform-admin'], vin: 'VIN123' });
      expect(screen.getByTestId('dispatch-to-service-button')).toBeInTheDocument();
    });

    it('hides dispatch button when vin is absent', () => {
      renderStrip({ callerGroups: ['fleet-operator'], vin: undefined });
      expect(screen.queryByTestId('dispatch-to-service-button')).not.toBeInTheDocument();
    });

    it('hides dispatch button when callerGroups is absent', () => {
      renderStrip({ callerGroups: undefined, vin: 'VIN123' });
      expect(screen.queryByTestId('dispatch-to-service-button')).not.toBeInTheDocument();
    });

    it('hides dispatch button for non-privileged groups', () => {
      renderStrip({ callerGroups: ['read-only-user'], vin: 'VIN123' });
      expect(screen.queryByTestId('dispatch-to-service-button')).not.toBeInTheDocument();
    });

    it('opens DispatchModal when dispatch button is clicked', () => {
      renderStrip({ callerGroups: ['fleet-operator'], vin: 'VIN123' });
      fireEvent.click(screen.getByTestId('dispatch-to-service-button'));
      // Modal is rendered with data-testid="dispatch-modal"
      expect(screen.getByTestId('dispatch-modal')).toBeInTheDocument();
    });
  });

  // ── Run scan ────────────────────────────────────────────────────────────────

  describe('run scan', () => {
    it('calls onRunScan when Run scan is clicked', () => {
      const onRunScan = vi.fn();
      renderStrip({ connectionStatus: 'connected', onRunScan });
      fireEvent.click(screen.getByTestId('health-strip-run-scan-button'));
      expect(onRunScan).toHaveBeenCalledOnce();
    });
  });

  // ── formatRelativeAge (unit tests) ──────────────────────────────────────────

  describe('formatRelativeAge', () => {
    it('returns "unknown" for undefined', () => {
      expect(formatRelativeAge(undefined)).toBe('unknown');
    });

    it('returns "unknown" for NaN date string', () => {
      expect(formatRelativeAge('not-a-date')).toBe('unknown');
    });

    it('returns "just now" for very recent timestamp', () => {
      expect(formatRelativeAge(Date.now() - 5000)).toBe('just now');
    });

    it('returns "N min ago" for minutes-old timestamp', () => {
      const result = formatRelativeAge(Date.now() - 14 * 60 * 1000);
      expect(result).toBe('14 min ago');
    });

    it('returns "N hrs ago" for hours-old timestamp', () => {
      const result = formatRelativeAge(Date.now() - 3 * 60 * 60 * 1000);
      expect(result).toBe('3 hrs ago');
    });
  });

  // ── verdictToRecommendedAction (unit tests) ──────────────────────────────────

  describe('verdictToRecommendedAction', () => {
    it('returns "Run a scan" copy when verdict is undefined', () => {
      const text = verdictToRecommendedAction(undefined, [], []);
      expect(text).toContain('Run a scan');
    });

    it('returns Healthy copy for Healthy verdict', () => {
      const verdict = computeVerdict([], []);
      const text = verdictToRecommendedAction(verdict, [], []);
      expect(text).toContain('No issues found');
    });

    it('returns Undetermined copy for Unrecognised verdict', () => {
      const dtcs = [{ code: 'U9999', status: 'active' }];
      const verdict = computeVerdict(dtcs, []);
      const text = verdictToRecommendedAction(verdict, [], dtcs);
      expect(text).toContain('Undetermined');
    });

    it('includes catalog description verbatim for P0 verdict', () => {
      const dtcs = [{ code: 'P0420', status: 'active' }];
      const catalog = [
        { dtc_code: 'P0420', severity_hint: 'P0', description: 'Stop driving immediately.' },
      ];
      const verdict = computeVerdict(dtcs, catalog);
      const text = verdictToRecommendedAction(verdict, catalog, dtcs);
      expect(text).toContain('Stop driving immediately.');
    });

    it('produces different text for Healthy vs P0 verdicts', () => {
      const healthyVerdict = computeVerdict([], []);
      const healthyText = verdictToRecommendedAction(healthyVerdict, [], []);

      const dtcs = [{ code: 'P0420', status: 'active' }];
      const catalog = [
        { dtc_code: 'P0420', severity_hint: 'P0', description: 'P0 action required.' },
      ];
      const p0Verdict = computeVerdict(dtcs, catalog);
      const p0Text = verdictToRecommendedAction(p0Verdict, catalog, dtcs);

      expect(healthyText).not.toBe(p0Text);
    });
  });

});
