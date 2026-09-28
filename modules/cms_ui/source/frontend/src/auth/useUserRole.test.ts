// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useUserRole } from './useUserRole';

const mockIsDemoMode = vi.fn(() => false);
vi.mock('../config/api', () => ({ isDemoMode: () => mockIsDemoMode() }));

const mockUseAuth = vi.fn();
vi.mock('./useAuth', () => ({ useAuth: () => mockUseAuth() }));

beforeEach(() => {
  mockUseAuth.mockReset();
  mockIsDemoMode.mockReset();
  mockIsDemoMode.mockReturnValue(false);
});

function makeUser(groups: string[], fleetIds?: string) {
  return { user: { username: 'u', groups, roles: groups, fleetIds: fleetIds ?? '' } };
}

describe('useUserRole — role-detection contract', () => {
  it('(a) isOperator true when cognito:groups includes fleet-operator', () => {
    mockUseAuth.mockReturnValue(makeUser(['fleet-operator']));
    const { result } = renderHook(() => useUserRole());
    expect(result.current.isOperator).toBe(true);
  });

  it('(b) isOperator false when only fleet-viewer', () => {
    mockUseAuth.mockReturnValue(makeUser(['fleet-viewer']));
    const { result } = renderHook(() => useUserRole());
    expect(result.current.isOperator).toBe(false);
  });

  it('(c) isAdmin true for platform-admin', () => {
    mockUseAuth.mockReturnValue(makeUser(['platform-admin']));
    const { result } = renderHook(() => useUserRole());
    expect(result.current.isAdmin).toBe(true);
  });

  it('(d) fleetIds parsed correctly from comma-separated custom:fleetIds', () => {
    mockUseAuth.mockReturnValue(makeUser([], 'fleet-a, fleet-b, fleet-c'));
    const { result } = renderHook(() => useUserRole());
    expect(result.current.fleetIds).toEqual(['fleet-a', 'fleet-b', 'fleet-c']);
  });

  it('(e) fleetIds empty array when claim absent', () => {
    mockUseAuth.mockReturnValue(makeUser([]));
    const { result } = renderHook(() => useUserRole());
    expect(result.current.fleetIds).toEqual([]);
  });
});

// ─── Dispatcher persona — spec 2026-07-17-cms-dispatcher-persona-nav-scope ───
describe('useUserRole — dispatcher persona', () => {
  it('(disp-a) isDispatcher true when groups includes "dispatcher"', () => {
    mockUseAuth.mockReturnValue(makeUser(['dispatcher']));
    const { result } = renderHook(() => useUserRole());
    expect(result.current.isDispatcher).toBe(true);
  });

  it('(disp-b) isDispatcher false when groups does NOT include "dispatcher"', () => {
    mockUseAuth.mockReturnValue(makeUser(['fleet-operator']));
    const { result } = renderHook(() => useUserRole());
    expect(result.current.isDispatcher).toBe(false);
  });

  it('(disp-c) dispatcher-only user has canWrite=false (READ-ONLY persona)', () => {
    mockUseAuth.mockReturnValue(makeUser(['dispatcher']));
    const { result } = renderHook(() => useUserRole());
    expect(result.current.canWrite).toBe(false);
    expect(result.current.isAdmin).toBe(false);
    expect(result.current.isOperator).toBe(false);
    expect(result.current.isEngineer).toBe(false);
    expect(result.current.isConnectAgent).toBe(false);
    expect(result.current.isViewer).toBe(false);
  });

  it('(disp-d) demo mode returns isDispatcher=false (multi-persona demo already covers everything)', () => {
    mockIsDemoMode.mockReturnValue(true);
    mockUseAuth.mockReturnValue(makeUser([]));
    const { result } = renderHook(() => useUserRole());
    expect(result.current.isDispatcher).toBe(false);
    // Sanity: demo mode still grants the wide demo persona
    expect(result.current.isOperator).toBe(true);
    expect(result.current.isConnectAgent).toBe(true);
    expect(result.current.isEngineer).toBe(true);
    expect(result.current.canWrite).toBe(true);
  });

  it('(disp-e) isDispatcher coexists with isAdmin — both flags true when user in both groups', () => {
    mockUseAuth.mockReturnValue(makeUser(['dispatcher', 'platform-admin']));
    const { result } = renderHook(() => useUserRole());
    expect(result.current.isDispatcher).toBe(true);
    expect(result.current.isAdmin).toBe(true);
    // Admin gets canWrite via the isAdmin path, unchanged
    expect(result.current.canWrite).toBe(true);
  });

  it('(disp-f) isDispatcher independent of isConnectAgent — both true when user in both groups', () => {
    mockUseAuth.mockReturnValue(makeUser(['dispatcher', 'connect-agent']));
    const { result } = renderHook(() => useUserRole());
    expect(result.current.isDispatcher).toBe(true);
    expect(result.current.isConnectAgent).toBe(true);
  });
});
