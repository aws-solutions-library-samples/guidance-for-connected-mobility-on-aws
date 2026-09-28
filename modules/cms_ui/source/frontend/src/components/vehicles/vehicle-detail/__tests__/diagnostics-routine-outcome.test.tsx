// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Self-test outcomes on the Diagnostics tab, after the rate-limit poller was
// fixed to read the live API (review FG2 cycle 2, issue
// issues/2026-09-25-diagnostics-ia-uat-defects/).
//
//   C1 — a vehicle-side failure must replace an earlier "completed successfully"
//   W1 — a self-test finishing must not stop a scan that is still running
//
// runRoutineInSession is replaced with a stub returning the outcome shapes the
// real client returns, so these tests exercise the panel's handling of them.

import React from 'react';
import { render, screen, fireEvent, act, within, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('@/auth/useAuth', () => ({
  useAuth: () => ({
    getAuthHeaders: () => ({ Authorization: 'Bearer test-token' }),
    user: { email: 'operator@example.com', groups: ['fleet-operator'], roles: ['fleet-operator'] },
  }),
}));

vi.mock('@/config/api', () => ({
  getApiEndpoint: () => 'http://localhost/',
  getRuntimeConfig: () => ({ apiEndpoint: 'http://localhost/' }),
}));

vi.mock('@/utils/authFetch', () => ({
  authFetch: vi.fn(),
}));

vi.mock('@/utils/sovdScanClient', async () => {
  const actual = await vi.importActual<typeof import('@/utils/sovdScanClient')>('@/utils/sovdScanClient');
  return { ...actual, runRoutineInSession: vi.fn() };
});

import VehicleDiagnosticsPanel from '@/components/vehicles/vehicle-detail/VehicleDiagnosticsPanel';
import type { RoutineCatalogEntry } from '@/components/vehicles/vehicle-detail/VehicleDiagnosticsPanel';
import * as authFetchModule from '@/utils/authFetch';
import * as client from '@/utils/sovdScanClient';

const VEHICLE_ID = 'VEH-MRDN-0001';
const LAMP: RoutineCatalogEntry = {
  routineId: 'lamp_self_check',
  safetyClass: 'INERT',
  invocable: true,
  precondition: 'Read-only self-test.',
  reason: '',
};
const okResponse = { ok: true, status: 200, json: async () => ({ success: true, commandId: 'r-1' }) } as unknown as Response;

beforeEach(() => {
  vi.mocked(authFetchModule.authFetch).mockReset();
  vi.mocked(authFetchModule.authFetch).mockReturnValue(undefined as unknown as Promise<Response>);
  vi.mocked(client.runRoutineInSession).mockReset();
});

async function runLamp() {
  fireEvent.click(screen.getByTestId('run-routine-lamp_self_check'));
  const dialog = await waitFor(() => screen.getByRole('dialog'));
  fireEvent.click(within(dialog).getByRole('checkbox'));
  await act(async () => {
    fireEvent.click(within(dialog).getByRole('button', { name: /confirm|run|execute/i }));
  });
}

describe('C1 — a failed self-test replaces the previous success', () => {
  it('shows the failure reason, and no longer shows "completed successfully"', async () => {
    vi.mocked(client.runRoutineInSession)
      .mockResolvedValueOnce({ kind: 'ok', response: okResponse, status: 'SUCCEEDED' })
      .mockResolvedValueOnce({ kind: 'error', response: okResponse, status: 'FAILED', reason: 'Lamp circuit open on bulb 3.' });

    render(<VehicleDiagnosticsPanel vehicleId={VEHICLE_ID} connectionStatus="connected" catalog={[]} routines={[LAMP]} />);

    await runLamp();
    await waitFor(() => expect(screen.getByTestId('routine-success')).toBeDefined());

    await runLamp();
    await waitFor(() => expect(screen.getByText('Lamp circuit open on bulb 3.')).toBeDefined());
    expect(screen.queryByTestId('routine-success')).toBeNull();
    expect(screen.queryByTestId('routine-running-indicator')).toBeNull();
  });

  it('shows "Running…" while a re-run is in flight, not the previous result', async () => {
    let finish: (v: client.RoutineInvocationOutcome) => void = () => {};
    vi.mocked(client.runRoutineInSession)
      .mockResolvedValueOnce({ kind: 'ok', response: okResponse, status: 'SUCCEEDED' })
      .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));

    render(<VehicleDiagnosticsPanel vehicleId={VEHICLE_ID} connectionStatus="connected" catalog={[]} routines={[LAMP]} />);
    await runLamp();
    await waitFor(() => expect(screen.getByTestId('routine-success')).toBeDefined());

    await runLamp();
    expect(screen.getByTestId('routine-running-indicator')).toBeDefined();
    expect(screen.queryByTestId('routine-success')).toBeNull();
    await act(async () => { finish({ kind: 'ok', response: okResponse, status: 'SUCCEEDED' }); });
  });
});

describe('W1 — a self-test finishing does not stop a running scan', () => {
  it.each([
    ['the client saw the terminal row', { kind: 'ok', response: okResponse, status: 'SUCCEEDED' } as const],
    ['the client hit its ceiling, so the panel polls for the outcome', { kind: 'ok', response: okResponse } as const],
  ])('leaves the scan poll registered and the scan still in progress (%s)', async (_label, outcome) => {
    vi.mocked(client.runRoutineInSession).mockResolvedValue(outcome);
    // The scan POST succeeds; its poll never sees a terminal row in this test.
    vi.mocked(authFetchModule.authFetch).mockImplementation(((url: string, o?: RequestInit) => {
      if (o?.method === 'POST') {
        return Promise.resolve({ ok: true, status: 200, clone() { return this; }, json: async () => ({ success: true, commandId: 'scan-1' }) } as unknown as Response);
      }
      return undefined;
    }) as unknown as typeof authFetchModule.authFetch);
    const cleared: unknown[] = [];
    const clearSpy = vi.spyOn(globalThis, 'clearInterval').mockImplementation(((id: unknown) => { cleared.push(id); }) as typeof clearInterval);
    const ids: unknown[] = [];
    const realSetInterval = globalThis.setInterval;
    const setSpy = vi.spyOn(globalThis, 'setInterval').mockImplementation(((fn: () => void, ms?: number) => {
      const id = realSetInterval(() => {}, 1_000_000);
      ids.push({ id, fn, ms });
      return id;
    }) as unknown as typeof setInterval);
    try {
      render(<VehicleDiagnosticsPanel vehicleId={VEHICLE_ID} connectionStatus="connected" catalog={[]} routines={[LAMP]} />);
      const before = ids.length;
      await act(async () => {
        fireEvent.click(screen.getByTestId('health-strip-run-scan-button'));
      });
      await act(async () => { await Promise.resolve(); });
      const scanTimer = (ids.slice(before) as Array<{ id: unknown; fn: () => void }>)
        .filter((t) => !String(t.fn).includes('runAnimationFrameCallbacks'))
        .pop();
      expect(scanTimer).toBeDefined();
      expect(screen.getByTestId('scan-spinner')).toBeDefined();

      await runLamp();
      // Let the outcome path settle (a direct result, or the fallback poll's first read).
      await act(async () => { await Promise.resolve(); await Promise.resolve(); });

      expect(cleared).not.toContain(scanTimer!.id);
      expect(screen.getByTestId('scan-spinner')).toBeDefined();
    } finally {
      setSpy.mockRestore();
      clearSpy.mockRestore();
      for (const t of ids as Array<{ id: ReturnType<typeof setInterval> }>) clearInterval(t.id);
    }
  });
});
