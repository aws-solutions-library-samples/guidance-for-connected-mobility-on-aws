// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

// Spec `2026-09-14-cms-frontend-fleet-persona-alignment`, Task 3.1.
//
// Asserts the fleet-operator palette shape: exactly 5 QUICK_ACTIONS entries
// covering 4 distinct commands (`lock_all_doors` appears twice, Lock and Unlock)
// (lock_all_doors x2, remote_start, find_my_vehicle, start_preconditioning).
// Driver-idiom commands (honk_horn, flash_hazards, panic_mode) must be absent.
//
// Mutation check performed before this task flipped to [x]:
//   Added { cmd: 'panic_mode', label: 'Activate Panic Mode', val: true } back
//   to QUICK_ACTIONS — the exact-shape test below FAILS on both the cmd-array
//   equality and the toHaveLength(5) assertion. Restored correct code.

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

// ── Module mocks ─────────────────────────────────────────────────────────────

vi.mock('../../../utils/api-config', () => ({
  getCommandsApiBase: () => 'http://localhost',
}));

vi.mock('../VehicleStatePanel', () => ({
  default: () => <div data-testid="vehicle-state-panel" />,
}));

// Mock useAuth as fleet-operator so the Task 3.2 gate passes and the palette renders.
// useUserRole derives isOperator from groups.includes('fleet-operator').
vi.mock('@/auth/useAuth', () => ({
  useAuth: () => ({
    getAuthHeaders: () => ({ Authorization: 'Bearer test-token' }),
    user: {
      username: 'test@example.com',
      email: 'test@example.com',
      name: 'Test User',
      groups: ['fleet-operator'],
      roles: ['fleet-operator'],
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

// Catalog with all four operator commands so every Quick Action button resolves
// a def and renders (buttons with no def are still rendered, just disabled).
const OPERATOR_CATALOG = {
  security: [
    { commandName: 'lock_all_doors', label: 'Lock All Doors', category: 'security', valueType: 'boolean', responseTimeout: '3000', signalField: 'allDoorsLocked', vssPath: 'Vehicle.Cabin.Door.IsAllLocked' },
    { commandName: 'remote_start', label: 'Remote Start', category: 'security', valueType: 'boolean', responseTimeout: '5000', signalField: 'engineRunning', vssPath: 'Vehicle.Powertrain.Engine.IsRunning' },
    { commandName: 'find_my_vehicle', label: 'Find My Vehicle', category: 'security', valueType: 'boolean', responseTimeout: '3000', signalField: 'locating', vssPath: 'Vehicle.CurrentLocation' },
    { commandName: 'start_preconditioning', label: 'Pre-Condition Cabin', category: 'climate', valueType: 'boolean', responseTimeout: '5000', signalField: 'preconditioning', vssPath: 'Vehicle.Cabin.HVAC.IsEnabled' },
  ],
};

function makeFetch() {
  return vi.fn().mockImplementation((url: string) => {
    if (typeof url === 'string' && url.includes('/catalog')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ actuators: OPERATOR_CATALOG }) });
    }
    if (typeof url === 'string' && url.includes('/geofences')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ geofences: [] }) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ commands: [] }) });
  });
}

import RemoteCommandsPanel, { QUICK_ACTIONS } from '../RemoteCommandsPanel';

// ── Tests ────────────────────────────────────────────────────────────────────

describe('RemoteCommandsPanel — fleet-operator palette shape (Task 3.1)', () => {
  it('QUICK_ACTIONS is exactly the 5 palette entries / 4 commands, in order (exact-shape pin)', () => {
    // F3.1: the exact-array assertion the spec's Task 3.1 Verify asked for.
    // The DOM-only version of this test asserted 5 labels present + 3 driver
    // labels absent, which catches the three NAMED removals but would admit an
    // arbitrary seventh command (e.g. `remote_stop`) — while its title claimed
    // a count it never checked. This pins shape, order and length at once.
    expect(QUICK_ACTIONS.map(a => a.cmd)).toEqual([
      'lock_all_doors',
      'lock_all_doors',
      'remote_start',
      'find_my_vehicle',
      'start_preconditioning',
    ]);
    expect(QUICK_ACTIONS).toHaveLength(5);
    // `lock_all_doors` appears twice and the pair must stay lock-then-unlock:
    // the two entries are distinguished only by `val`, so an order flip would
    // relabel the buttons without changing the cmd list above.
    expect(QUICK_ACTIONS[0].val).toBe(true);
    expect(QUICK_ACTIONS[1].val).toBe(false);
    // No driver-idiom command may reappear under any label.
    for (const banned of ['honk_horn', 'flash_hazards', 'panic_mode']) {
      expect(QUICK_ACTIONS.map(a => a.cmd)).not.toContain(banned);
    }
  });

  it('renders exactly 5 quick action buttons, one per QUICK_ACTIONS entry', async () => {
    global.fetch = makeFetch() as unknown as typeof fetch;

    render(<RemoteCommandsPanel vehicleId="VEH-TEST-001" connectionStatus="connected" />);

    // Wait for catalog to load — "Lock All Doors" is the first resolved button.
    const lockBtn = await screen.findByRole('button', { name: 'Lock All Doors', exact: true });
    expect(lockBtn).toBeInTheDocument();

    // Every QUICK_ACTIONS entry renders exactly one button with its label.
    for (const { label } of QUICK_ACTIONS) {
      expect(screen.getByRole('button', { name: label, exact: true })).toBeInTheDocument();
    }

    // Driver-idiom commands must not be present.
    expect(screen.queryByRole('button', { name: /honk/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /hazard/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /panic/i })).not.toBeInTheDocument();
  });

  it('does not render Honk Horn button', async () => {
    global.fetch = makeFetch() as unknown as typeof fetch;
    render(<RemoteCommandsPanel vehicleId="VEH-TEST-001" connectionStatus="connected" />);
    await screen.findByRole('button', { name: 'Lock All Doors', exact: true });
    expect(screen.queryByRole('button', { name: /honk horn/i })).not.toBeInTheDocument();
  });

  it('does not render Flash Hazard Lights button', async () => {
    global.fetch = makeFetch() as unknown as typeof fetch;
    render(<RemoteCommandsPanel vehicleId="VEH-TEST-001" connectionStatus="connected" />);
    await screen.findByRole('button', { name: 'Lock All Doors', exact: true });
    expect(screen.queryByRole('button', { name: /flash hazard/i })).not.toBeInTheDocument();
  });

  it('does not render Activate Panic Mode button', async () => {
    global.fetch = makeFetch() as unknown as typeof fetch;
    render(<RemoteCommandsPanel vehicleId="VEH-TEST-001" connectionStatus="connected" />);
    await screen.findByRole('button', { name: 'Lock All Doors', exact: true });
    expect(screen.queryByRole('button', { name: /panic mode/i })).not.toBeInTheDocument();
  });
});
