/**
 * VehicleDiagnosticsPanel — triage-frame legibility test (Task 2.4).
 *
 * Spec: `.kiro/specs/2026-09-14-cms-frontend-fleet-persona-alignment` Task 2.4
 *
 * Two assertions against a full panel render given a mixed catalog (one INERT,
 * one STATIONARY, one SERVICE_ONLY):
 *
 *   (a) getByRole('heading', { name: /Diagnostic Triage/i }) is present.
 *   (b) The rendered text contains substrings matching all three triage labels
 *       so a demo-viewer can infer the fleet-operator/DMS seam from copy alone.
 *
 * Mutation: reverting Task 2.3's heading copy to "Vehicle Diagnostics" causes
 * (a) to fail.
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

// ── Mixed catalog — one of each safety class ─────────────────────────────────

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

// ── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

// ────────────────────────────────────────────────────────────────────────────
// Triage frame legibility — two assertions per Task 2.4 Accept
// ────────────────────────────────────────────────────────────────────────────

describe('Task 2.4 — triage-frame legibility with mixed catalog', () => {
  it('(a) renders the "Diagnostic Triage" h2 heading', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TRIAGE-X"
        connectionStatus="connected"
        routines={MIXED_CATALOG}
      />,
    );
    // Mutation: reverting h2 to "Vehicle Diagnostics" → this fails.
    expect(
      screen.getByRole('heading', { name: /Diagnostic Triage/i }),
    ).toBeInTheDocument();
  });

  it('(b) rendered text contains all three triage-class substrings', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TRIAGE-Y"
        connectionStatus="connected"
        routines={MIXED_CATALOG}
      />,
    );
    // Pins the demo-viewer's ability to infer the fleet-operator/DMS seam
    // from copy alone — all three triage labels must appear.
    const body = document.body.textContent ?? '';
    expect(body).toMatch(/self-tests you can run here/i);
    expect(body).toMatch(/require the vehicle at rest/i);
    expect(body).toMatch(/handled by service at the dealership/i);
  });
});
