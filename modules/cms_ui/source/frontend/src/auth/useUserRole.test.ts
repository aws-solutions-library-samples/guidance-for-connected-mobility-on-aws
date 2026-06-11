// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useUserRole } from './useUserRole';

vi.mock('../config/api', () => ({ isDemoMode: () => false }));

const mockUseAuth = vi.fn();
vi.mock('./useAuth', () => ({ useAuth: () => mockUseAuth() }));

beforeEach(() => {
  mockUseAuth.mockReset();
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
