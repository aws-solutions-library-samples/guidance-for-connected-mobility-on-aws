// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

// Spec `2026-09-14-cms-frontend-fleet-persona-alignment`, Task 3.2.
//
// Asserts the role gate: platform-admin and fleet-operator see the Quick
// Actions palette; all other personas (including dispatcher) get a read-only
// informational card.
//
// Mutation check performed before this task flipped to [x]:
//   Replaced `!(isAdmin || isOperator)` early-return condition with `false`
//   (gate always passes through to the palette). Tests (a) and (d) FAILED:
//   (a) "queryByRole('button', …) returns null" → received a rendered button.
//   (d) "dispatcher cannot see palette" → received a rendered button.
//   Restored the correct gate.
//
// Mock strategy: mock `@/auth/useAuth` (the hook useUserRole depends on) to
// control Cognito groups. This exercises the real useUserRole derivation logic
// — isAdmin is derived from 'platform-admin' in groups, isOperator from
// 'fleet-operator', isDispatcher from 'dispatcher' — making the test validate
// the real group-to-role mapping as well as the render gate.

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Module mocks (must precede all component imports) ────────────────────────

vi.mock('../../../utils/api-config', () => ({
  getCommandsApiBase: () => 'http://localhost',
}));

vi.mock('../VehicleStatePanel', () => ({
  default: () => <div data-testid="vehicle-state-panel" />,
}));

// Control groups via useAuth mock. useUserRole reads user.groups to derive
// isAdmin/isOperator/isDispatcher etc. isDemoMode is mocked to false so the
// demo-mode branch (which unconditionally returns isOperator: true) does not
// suppress the gate.
const mockGetGroups = vi.fn<[], string[]>(() => []);

vi.mock('@/auth/useAuth', () => ({
  useAuth: () => ({
    getAuthHeaders: () => ({ Authorization: 'Bearer test-token' }),
    user: {
      username: 'test@example.com',
      email: 'test@example.com',
      name: 'Test User',
      groups: mockGetGroups(),
      roles: mockGetGroups(),
      fleetIds: '',
    },
  }),
}));

vi.mock('@/config/api', () => ({
  isDemoMode: () => false,
  getCommandsApiBase: () => 'http://localhost',
  getRuntimeConfig: () => ({ apiEndpoint: 'http://localhost/' }),
  getApiEndpoint: () => 'http://localhost/',
}));

const CATALOG_WITH_LOCK = {
  security: [
    {
      commandName: 'lock_all_doors',
      label: 'Lock All Doors',
      category: 'security',
      valueType: 'boolean',
      responseTimeout: '3000',
      signalField: 'allDoorsLocked',
      vssPath: 'Vehicle.Cabin.Door.IsAllLocked',
    },
  ],
};

function makeFetch() {
  return vi.fn().mockImplementation((url: string) => {
    if (typeof url === 'string' && url.includes('/catalog')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ actuators: CATALOG_WITH_LOCK }) });
    }
    if (typeof url === 'string' && url.includes('/geofences')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ geofences: [] }) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ commands: [] }) });
  });
}

import RemoteCommandsPanel from '../RemoteCommandsPanel';

// ── Tests ────────────────────────────────────────────────────────────────────

describe('RemoteCommandsPanel — persona render gate (Task 3.2)', () => {
  beforeEach(() => {
    global.fetch = makeFetch() as unknown as typeof fetch;
  });

  // (a) Non-operator, non-admin: palette must NOT render; read-only card MUST render.
  it('(a) shows read-only card and no palette for a non-admin non-operator user (fleet-viewer)', async () => {
    mockGetGroups.mockReturnValue(['fleet-viewer']);

    render(<RemoteCommandsPanel vehicleId="VEH-TEST-001" connectionStatus="connected" />);

    // The read-only card message must be present.
    expect(await screen.findByText(/Remote commands are available/i)).toBeInTheDocument();

    // Quick Actions palette must NOT render.
    expect(screen.queryByRole('button', { name: /Lock All Doors/i })).not.toBeInTheDocument();
  });

  // (b) isAdmin=true (platform-admin group): palette renders.
  it('(b) shows the Quick Actions palette for a platform-admin user', async () => {
    mockGetGroups.mockReturnValue(['platform-admin']);

    render(<RemoteCommandsPanel vehicleId="VEH-TEST-001" connectionStatus="connected" />);

    // Wait for catalog to load and "Lock All Doors" button to appear.
    expect(await screen.findByRole('button', { name: 'Lock All Doors', exact: true })).toBeInTheDocument();

    // No read-only card.
    expect(screen.queryByText(/Remote commands are available/i)).not.toBeInTheDocument();
  });

  // (c) isOperator=true (fleet-operator group): palette renders.
  it('(c) shows the Quick Actions palette for a fleet-operator user', async () => {
    mockGetGroups.mockReturnValue(['fleet-operator']);

    render(<RemoteCommandsPanel vehicleId="VEH-TEST-001" connectionStatus="connected" />);

    expect(await screen.findByRole('button', { name: 'Lock All Doors', exact: true })).toBeInTheDocument();
    expect(screen.queryByText(/Remote commands are available/i)).not.toBeInTheDocument();
  });

  // (d) Dispatcher-only (dispatcher group): read-only. dispatcher is NOT in
  //     write-eligible groups; canWrite remains false; isAdmin and isOperator
  //     are both false.
  it('(d) shows read-only card for a dispatcher-only user (palette must not render)', async () => {
    mockGetGroups.mockReturnValue(['dispatcher']);

    render(<RemoteCommandsPanel vehicleId="VEH-TEST-001" connectionStatus="connected" />);

    expect(await screen.findByText(/Remote commands are available/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Lock All Doors/i })).not.toBeInTheDocument();
  });
});
