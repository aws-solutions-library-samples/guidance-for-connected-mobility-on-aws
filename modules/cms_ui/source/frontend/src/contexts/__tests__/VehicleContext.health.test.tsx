// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// issues/2026-09-18-vsa-vehicle-context-points-at-nonexistent-cms-prod-table/
//
// `loadVehicleHealth` was repointed 2026-09-18 from CVX's `/vehicles/{id}/context`
// (broken — its Lambda pointed at a nonexistent `cms-prod-*` table) to CMS's own
// `main_api` route `GET /api/v1/vehicles/{id}/health`. These tests pin the new
// endpoint resolution (getApiEndpoint, not getVsaApiEndpoint), the new URL path,
// and that the widened response shape (`activeDtcs`, `vehicle.connectionStatus`,
// `vehicle.lastSeenAt`) survives into context state — a regression here would
// silently blank the widget's KPI tiles without any prior assertion failing,
// since VehicleHealthScoreWidget renders missing values as "—" rather than
// erroring.

import { renderHook, waitFor, act } from '@testing-library/react';
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../config/api', () => ({
  getApiEndpoint: () => 'https://cms-api.example.invalid/',
}));

import { VehicleProvider, useVehicle } from '../VehicleContext';

function wrapper({ children }: { children: React.ReactNode }) {
  return <VehicleProvider>{children}</VehicleProvider>;
}

const getAuthHeaders = () => ({ Authorization: 'Bearer test-token' });

function okResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) };
}

function errResponse(status: number) {
  return { ok: false, status, json: async () => ({ error: 'nope' }), text: async () => 'nope' };
}

beforeEach(() => {
  global.fetch = vi.fn();
});

describe('VehicleContext.loadVehicleHealth', () => {
  it('fetches from CMS main_api, not CVX — correct base and path', async () => {
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(okResponse({
      vehicleId: 'VEH-1',
      healthScore: 92,
      healthScoreBreakdown: { score: 92, deductions: [], computedAt: '2026-09-18T00:00:00Z' },
      activeDtcs: [],
      vehicle: { connectionStatus: 'connected', lastSeenAt: '2026-09-18T00:00:00Z' },
    }));

    const { result } = renderHook(() => useVehicle(), { wrapper });
    await act(async () => {
      await result.current.loadVehicleHealth('VEH-1', getAuthHeaders);
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [calledUrl] = fetchMock.mock.calls[0];
    // Must NOT be CVX's /vehicles/{id}/context path, and must be CMS's
    // own /api/v1/vehicles/{id}/health path against the CMS base.
    expect(calledUrl).toBe(
      'https://cms-api.example.invalid/api/v1/vehicles/VEH-1/health'
    );
    expect(calledUrl).not.toContain('/context');
  });

  it('surfaces score, breakdown, activeDtcs, and connection fields into context state', async () => {
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(okResponse({
      vehicleId: 'VEH-2',
      healthScore: 77,
      healthScoreBreakdown: {
        score: 77,
        deductions: [{ reason: 'DTC P0420 HIGH', amount: 15 }],
        computedAt: '2026-09-18T01:02:03.000Z',
      },
      activeDtcs: [{ code: 'P0420', severity: 'HIGH' }],
      vehicle: { connectionStatus: 'disconnected', lastSeenAt: '2026-09-17T23:00:00Z' },
    }));

    const { result } = renderHook(() => useVehicle(), { wrapper });
    await act(async () => {
      await result.current.loadVehicleHealth('VEH-2', getAuthHeaders);
    });

    await waitFor(() => {
      expect(result.current.healthVehicleId).toBe('VEH-2');
    });
    expect(result.current.healthScore).toBe(77);
    expect(result.current.healthScoreBreakdown?.deductions).toEqual([
      { reason: 'DTC P0420 HIGH', amount: 15 },
    ]);
    // These two are the KPI-strip inputs — the exact fields that would
    // silently render as "—" with no failing assertion elsewhere if the
    // route's response shape ever narrowed back down.
    expect(result.current.healthActiveDtcs).toEqual([{ code: 'P0420', severity: 'HIGH' }]);
    expect(result.current.healthConnectionStatus).toBe('disconnected');
    expect(result.current.healthLastSeenAt).toBe('2026-09-17T23:00:00Z');
    expect(result.current.healthError).toBeNull();
  });

  it('sends the caller-supplied auth headers on every request', async () => {
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(okResponse({
      vehicleId: 'VEH-3', healthScore: 100,
      healthScoreBreakdown: { score: 100, deductions: [], computedAt: '2026-09-18T00:00:00Z' },
      activeDtcs: [], vehicle: { connectionStatus: 'connected', lastSeenAt: null },
    }));

    const { result } = renderHook(() => useVehicle(), { wrapper });
    await act(async () => {
      await result.current.loadVehicleHealth('VEH-3', getAuthHeaders);
    });

    const [, calledInit] = fetchMock.mock.calls[0];
    expect((calledInit as RequestInit).headers).toMatchObject({
      Authorization: 'Bearer test-token',
    });
  });

  it('sets a soft error (not a throw) on a non-200 response', async () => {
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(errResponse(404));

    const { result } = renderHook(() => useVehicle(), { wrapper });
    await act(async () => {
      await result.current.loadVehicleHealth('VEH-404', getAuthHeaders);
    });

    await waitFor(() => {
      expect(result.current.healthVehicleId).toBe('VEH-404');
    });
    expect(result.current.healthError).toBe('HTTP 404');
    expect(result.current.healthScore).toBeNull();
  });

  it('sets a soft error when no CMS API endpoint is configured, without calling fetch', async () => {
    vi.doMock('../../config/api', () => ({ getApiEndpoint: () => '' }));
    vi.resetModules();
    const { VehicleProvider: FreshProvider, useVehicle: freshUseVehicle } = await import('../VehicleContext');
    const freshWrapper = ({ children }: { children: React.ReactNode }) => (
      <FreshProvider>{children}</FreshProvider>
    );

    const fetchMock = global.fetch as ReturnType<typeof vi.fn>;
    const { result } = renderHook(() => freshUseVehicle(), { wrapper: freshWrapper });
    await act(async () => {
      await result.current.loadVehicleHealth('VEH-5', getAuthHeaders);
    });

    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.healthError).toBe('CMS API endpoint not configured');
  });
});
