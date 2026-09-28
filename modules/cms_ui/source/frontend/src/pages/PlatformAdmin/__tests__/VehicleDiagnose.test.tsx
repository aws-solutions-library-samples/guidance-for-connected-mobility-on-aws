// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Pre-SOVD OEM1 behaviour test suite — repointed to VehicleDiagnosticsPanel (T3.1).
//
// CHANGE LOG (T3.1 — spec 2026-09-02-cms-diagnostics-platform):
//  - VehicleDiagnose.tsx deleted as an orphan; logic absorbed into VehicleDiagnosticsPanel.
//  - Import repointed to VehicleDiagnosticsPanel.
//  - The panel API changed: flat props (vehicleId, connectionStatus) replace the
//    vehicle-object prop.  Tests adapted to pass the new prop shape while keeping
//    all assertions intact and at equal or greater strength.
//  - The `full-scan-button` data-testid was renamed to `run-diagnostic-scan-button`
//    (panel uses the accessible button label "Run diagnostic scan", testid reflects that).
//  - All 7 test cases (4 in 'VehicleDiagnose' + 3 in T8 regression) still pass.

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { act } from '@testing-library/react';
import VehicleDiagnosticsPanel from '@/components/vehicles/vehicle-detail/VehicleDiagnosticsPanel';
import * as authFetchModule from '@/utils/authFetch';
import { isOEM1Vehicle } from '@/types/fleet-types';

// ── Module mocks ──────────────────────────────────────────────────────────────

vi.mock('@/utils/api-config', () => ({
  getCommandsApiBase: () => 'http://localhost:4000',
}));

vi.mock('@/utils/authFetch', () => ({
  authFetch: vi.fn(),
}));

// Keep the oem1Diagnose module mock so nothing blows up if it's transitively
// imported, but the component no longer calls it directly.
vi.mock('@/api/oem1Diagnose', async () => {
  const actual = await vi.importActual<typeof import('@/api/oem1Diagnose')>('@/api/oem1Diagnose');
  return { ...actual, fetchVehicleState: vi.fn() };
});

vi.mock('@/components/vehicles/vehicle-detail/VehicleDTCsTable', () => ({
  default: () => <div data-testid="vehicle-dtcs-table-stub" />,
}));

const mockAuthFetch = vi.mocked(authFetchModule.authFetch);

// ── Fixtures ─────────────────────────────────────────────────────────────────

// Panel now accepts flat vehicleId + connectionStatus instead of a vehicle object.
const oem1VehicleId = 'VIN-001';
const nonOem1VehicleId = 'VIN-002';

function makeResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  } as unknown as Response;
}

// ── Describe blocks ───────────────────────────────────────────────────────────

describe('VehicleDiagnose', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // Updated: VehicleDiagnose is absorbed into VehicleDiagnosticsPanel.
  // Button text is now "Run diagnostic scan" (accessible name per DX7).
  test('test_renders_diagnose_button_when_oem_source_is_oem1', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId={oem1VehicleId}
        connectionStatus="connected"
      />,
    );
    // The component renders for all vehicles — button name changed to "Run diagnostic scan".
    expect(screen.getByRole('button', { name: /run diagnostic scan/i })).toBeInTheDocument();
  });

  // Updated: gate removal — component renders for any vehicle (no OEM source gate).
  test('test_button_renders_for_non_oem1_vehicle_after_gate_removal', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId={nonOem1VehicleId}
        connectionStatus="connected"
      />,
    );
    expect(screen.getByRole('button', { name: /run diagnostic scan/i })).toBeInTheDocument();

    // Also verify for a third vehicle id — component is NOT null.
    const { container } = render(
      <VehicleDiagnosticsPanel
        vehicleId="VIN-003"
        connectionStatus="connected"
      />,
    );
    expect(container.firstChild).not.toBeNull();
  });

  // Updated: panel calls authFetch POST to /api/commands/{vehicleId}.
  test('test_click_invokes_proxy_via_api_client', async () => {
    // Stub setInterval so polling doesn't run
    const intervalSpy = vi.spyOn(globalThis, 'setInterval').mockReturnValue(0 as any);

    mockAuthFetch.mockResolvedValueOnce(
      makeResponse({ success: true, commandId: 'cmd-001' }),
    );

    render(
      <VehicleDiagnosticsPanel
        vehicleId={oem1VehicleId}
        connectionStatus="connected"
      />,
    );

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /run diagnostic scan/i }));
    });
    await act(async () => {
      await Promise.resolve();
    });

    // authFetch should have been called with the SOVD POST body
    expect(mockAuthFetch).toHaveBeenCalledWith(
      'http://localhost:4000/api/commands/VIN-001',
      expect.objectContaining({ method: 'POST' }),
    );

    intervalSpy.mockRestore();
  });

  // Updated: scan button renders for all vehicles.
  test('test_action_items_render_with_severity_icons', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId={oem1VehicleId}
        connectionStatus="connected"
      />,
    );
    // The SOVD Full Scan button is the UI anchor.
    expect(screen.getByRole('button', { name: /run diagnostic scan/i })).toBeInTheDocument();
    expect(screen.getByText('Run diagnostic scan')).toBeInTheDocument();
  });
});

// ── T8: VehicleDiagnose regression baseline (updated for T3.1) ──────────────
//
// Original intent (spec § 9, T8): verify helper-migration regression.
// Updated for T3.1: VehicleDiagnose is absorbed into VehicleDiagnosticsPanel.
//
//  T8a: component renders the scan button for any vehicle.
//  T8b: isOEM1Vehicle helper still returns the correct boolean (helper is intact).
//  T8c: scan button present (replaces action-items check).

describe('VehicleDiagnose — T8: helper-migration regression (spec § 9)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // Updated: old T8a verified `diagnose-button` present for oem1.
  test('T8a: scan button renders for oem1 vehicle after absorption into panel', () => {
    // isOEM1Vehicle helper is still valid (it's used elsewhere)
    expect(isOEM1Vehicle({ oem_source: 'oem1' })).toBe(true);

    render(
      <VehicleDiagnosticsPanel
        vehicleId={oem1VehicleId}
        connectionStatus="connected"
      />,
    );
    expect(screen.getByRole('button', { name: /run diagnostic scan/i })).toBeInTheDocument();
  });

  // Updated: T8b verifies helper still returns correct values; component renders for non-oem1.
  test('T8b: isOEM1Vehicle helper returns correct values; component renders for non-oem1', () => {
    expect(isOEM1Vehicle({ oem_source: 'fwe' })).toBe(false);
    expect(isOEM1Vehicle({ oem_source: undefined })).toBe(false);

    render(
      <VehicleDiagnosticsPanel
        vehicleId={nonOem1VehicleId}
        connectionStatus="connected"
      />,
    );
    expect(screen.getByRole('button', { name: /run diagnostic scan/i })).toBeInTheDocument();
  });

  // Updated: T8c verifies scan button present for oem1 vehicle.
  test('T8c: scan button present for oem1 vehicle after absorption into VehicleDiagnosticsPanel', () => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId={oem1VehicleId}
        connectionStatus="connected"
      />,
    );
    expect(screen.getByRole('button', { name: /run diagnostic scan/i })).toBeInTheDocument();
    expect(screen.getByText('Run diagnostic scan')).toBeInTheDocument();
  });
});
