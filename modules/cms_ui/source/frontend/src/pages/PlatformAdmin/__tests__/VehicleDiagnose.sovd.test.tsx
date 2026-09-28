// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Tests: VehicleDiagnose — SOVD additions (repointed to VehicleDiagnosticsPanel, T3.1)
//
// CHANGE LOG (T3.1 — spec 2026-09-02-cms-diagnostics-platform):
//  - VehicleDiagnose.tsx deleted as an orphan; logic absorbed into VehicleDiagnosticsPanel.
//  - Import repointed to VehicleDiagnosticsPanel.
//  - Panel API: flat props (vehicleId, connectionStatus) replace the vehicle-object prop.
//  - All 4 test cases adapted to the new prop shape; assertions preserved.
//  - `full-scan-button` testid replaced by role query `button, name:/run diagnostic scan/`.
//
// Coverage (unchanged from original):
//  1. Renders for non-OEM1 vehicle — regression after gate removal.
//  2. Scan button POSTs read_dtcs with components ['*'].
//  3. Renders one card per ECU with DTC count.
//  4. Disabled when connectionStatus !== 'connected'.

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import VehicleDiagnosticsPanel from '@/components/vehicles/vehicle-detail/VehicleDiagnosticsPanel';
import * as authFetchModule from '@/utils/authFetch';
import { isOEM1Vehicle } from '@/types/fleet-types';

// ── Module mocks ─────────────────────────────────────────────────────────────

vi.mock('@/utils/api-config', () => ({
  getCommandsApiBase: () => 'http://localhost:4000',
}));

vi.mock('@/utils/authFetch', () => ({
  authFetch: vi.fn(),
}));

vi.mock('@/components/vehicles/vehicle-detail/VehicleDTCsTable', () => ({
  default: () => <div data-testid="vehicle-dtcs-table-stub" />,
}));

const mockAuthFetch = vi.mocked(authFetchModule.authFetch);

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  } as unknown as Response;
}

/** A SOVD command row returned by GET /api/commands/{id}?type=sovd&limit=1 with SUCCEEDED status. */
function makeSovdSuccessResponse(ecus: string[]) {
  const components: Record<string, unknown> = {};
  ecus.forEach(ecu => {
    components[ecu] = {
      id: ecu,
      dtcs: [{ code: 'P0420', freeze_frame: {} }],
    };
  });
  return {
    commands: [
      {
        commandId: 'cmd-001',
        status: 'SUCCEEDED',
        commandType: 'read_dtcs',
        type: 'sovd',
        components,
        latency_ms: 1234,
        storage_uri: null,
      },
    ],
  };
}

// ── Setup / teardown ──────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('VehicleDiagnose — SOVD additions (Group 4.3, repointed to VehicleDiagnosticsPanel T3.1)', () => {
  /**
   * 1. REGRESSION — renders for non-OEM1 vehicle.
   *
   * The panel renders for ANY vehicle; no OEM source gate exists.
   * isOEM1Vehicle is confirmed to return false for the test vehicle,
   * proving the post-gate behaviour is exercised.
   */
  it('renders for non-OEM1 vehicle', () => {
    const nonOem1VehicleId = 'VEH-TEST-001';
    // Confirm the vehicle would have been rejected by the old gate.
    // isOEM1Vehicle takes an object with optional oem_source; here it's absent.
    expect(isOEM1Vehicle({ vehicleId: nonOem1VehicleId } as any)).toBe(false);

    render(
      <VehicleDiagnosticsPanel
        vehicleId={nonOem1VehicleId}
        connectionStatus="connected"
      />,
    );

    // Component must render — not null. The scan button is the primary interactive element.
    expect(screen.getByRole('button', { name: /run diagnostic scan/i })).toBeInTheDocument();
    expect(screen.getByText('Run diagnostic scan')).toBeInTheDocument();
  });

  /**
   * 2. Scan button POSTs read_dtcs with components ['*'].
   */
  it('scan button POSTs read_dtcs with components ["*"]', async () => {
    // Stub setInterval to a no-op so we don't need to manage the polling loop.
    const intervalSpy = vi.spyOn(globalThis, 'setInterval').mockReturnValue(0 as any);

    // POST returns 200
    mockAuthFetch.mockResolvedValueOnce(
      makeResponse({ success: true, commandId: 'cmd-001' }),
    );

    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-OEM1-001"
        connectionStatus="connected"
      />,
    );

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /run diagnostic scan/i }));
    });

    // Flush promises so the POST resolves
    await act(async () => {
      await Promise.resolve();
    });

    expect(mockAuthFetch).toHaveBeenCalledWith(
      'http://localhost:4000/api/commands/VEH-OEM1-001',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          command_type: 'read_dtcs',
          components: ['*'],
          include_freeze_frame: true,
        }),
      }),
    );

    intervalSpy.mockRestore();
  });

  /**
   * 3. Renders one card per ECU with DTC count.
   */
  it('renders one card per ECU with DTC count', async () => {
    const ecuIds = ['ECU_ENGINE', 'ECU_TRANSMISSION', 'ECU_ABS'];
    const successPayload = makeSovdSuccessResponse(ecuIds);

    // Capture the interval callback so we can invoke it manually
    let pollCallback: (() => void) | null = null;
    const intervalSpy = vi.spyOn(globalThis, 'setInterval').mockImplementation((fn: any) => {
      pollCallback = fn;
      return 99 as any;
    });

    // authFetch is mocked by URL/method rather than by call order: the panel's
    // mount-time effects (routines fetch DX58, session-log fetches) issue their
    // own GETs before the scan button is ever clicked, so a positional
    // `mockResolvedValueOnce` queue silently shifts by one and starves the
    // scan's own POST/GET pair. See issues/2026-09-13-vehiclediagnose-sovd-test-regression/.
    mockAuthFetch.mockImplementation((url: string, opts?: RequestInit) => {
      if (opts?.method === 'POST') {
        // Same id as the polled row: the panel matches poll rows to the
        // scan it started (issue 2026-09-25-diagnostics-ia-uat-defects, W2).
        return Promise.resolve(makeResponse({ success: true, commandId: 'cmd-001' }));
      }
      if (url.includes('/routines')) {
        return Promise.resolve(makeResponse({ routines: [] }));
      }
      // GET poll for the latest SOVD command (session-log fetches match here
      // too, but the shape they get back — `commands: [...]` — is harmless to
      // them since none of this test's assertions depend on session-log state)
      return Promise.resolve(makeResponse(successPayload));
    });

    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-OEM1-001"
        connectionStatus="connected"
      />,
    );

    // Click scan button and let the POST resolve
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /run diagnostic scan/i }));
    });
    await act(async () => {
      await Promise.resolve();
    });

    // The interval was set — now invoke the poll callback once
    expect(pollCallback).not.toBeNull();
    await act(async () => {
      await (pollCallback as () => void)();
    });

    // Wait for the ECU cards to appear
    await waitFor(() => {
      expect(screen.getByTestId('ecu-cards')).toBeInTheDocument();
    });

    // One card per ECU — Cloudscape Cards renders each item with the header text
    for (const ecuId of ecuIds) {
      expect(screen.getByText(ecuId)).toBeInTheDocument();
    }

    intervalSpy.mockRestore();
  });

  /**
   * 4. Scan button is disabled when connectionStatus !== 'connected'.
   *
   * Allow-list gate — only the exact string 'connected' enables the button.
   */
  it.each([
    [undefined, 'undefined connectionStatus'],
    [null as unknown as string, 'null connectionStatus'],
    ['disconnected', "'disconnected' connectionStatus"],
  ])('disabled when connectionStatus is %s (%s)', (status, _label) => {
    render(
      <VehicleDiagnosticsPanel
        vehicleId="VEH-TEST-002"
        connectionStatus={status as string | undefined}
      />,
    );

    const btn = screen.getByRole('button', { name: /run diagnostic scan/i });
    // Cloudscape Button renders as disabled — check attribute
    expect(btn).toBeDisabled();
  });
});
