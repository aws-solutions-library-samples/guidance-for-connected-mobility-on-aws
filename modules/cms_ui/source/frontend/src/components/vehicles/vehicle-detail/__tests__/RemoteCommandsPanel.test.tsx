// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

// RED-PHASE tests — spec `2026-08-04-cms-vehicle-trip-lifecycle-split`,
// Group 2 task "UI connection-gate tests".
//
// These three cases define the contract the Group 4 implementation
// (`disabled={!def || !isConnected}` + explanatory affordance) must
// satisfy.  The DISCONNECTED case (Case 2) MUST FAIL until Group 4
// lands because the gate does not yet exist — `RemoteCommandsPanel.tsx`
// line 212 reads `disabled={!def}` only.
//
// Failing assertion for Case 2:
//   expect(button).toBeDisabled()  ← FAILS because the button is NOT
//   disabled when `connectionStatus='disconnected'` and a catalog
//   definition IS present.  The gate that would disable it on
//   disconnection does not exist yet.  This is the correct red-phase
//   signal; do NOT make it pass until Group 4 adds the gate.
//
// Group 4 implementation surface required (props/shape):
//   interface Props {
//     vehicleId: string;
//     connectionStatus?: 'connected' | 'disconnected' | string;  // NEW
//     latestTelemetry?: Record<string, unknown> | null;
//     onRefreshTelemetry?: () => Promise<void> | void;
//   }
//
//   Gate expression in quickActions.map (line 212):
//     disabled={!def || connectionStatus !== 'connected'}
//
//   Explanatory affordance (Case 2 asserts this exists):
//     When connectionStatus !== 'connected', render an Alert or
//     StatusIndicator with text that tells the operator the vehicle
//     is disconnected and commands cannot be sent.  Suggested copy:
//       "Vehicle is disconnected. Reconnect the vehicle to send commands."
//     The assertion below uses /disconnected/i so any case is accepted.

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// ── Module mocks ────────────────────────────────────────────────────────────
// Match the conventions in VehicleStatePanel.test.tsx and
// VehicleDetailView.test.tsx: vi.mock at the top level, factories return
// stable React stubs so JSX imports don't blow up in jsdom.

vi.mock('../../../utils/api-config', () => ({
  getCommandsApiBase: () => 'http://localhost',
}));

// VehicleStatePanel is a real sub-component of RemoteCommandsPanel.
// Stub it so these tests own only the connection-gate surface and are
// not subject to VehicleStatePanel's own prop requirements.
vi.mock('../VehicleStatePanel', () => ({
  default: () => <div data-testid="vehicle-state-panel" />,
}));

// Task 3.2 added useUserRole to RemoteCommandsPanel (spec
// `2026-09-14-cms-frontend-fleet-persona-alignment`). Mock useAuth as
// fleet-operator so the Task 3.2 render gate passes and these
// connection-gate tests continue to exercise their intended surface.
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

// RemoteCommandsPanel fires three fetch() calls on mount (catalog,
// history, geofences).  Intercept all of them with minimal happy-path
// responses so the component renders without network errors.
//
// The catalog response shape is the critical one: it must include the
// `lock_all_doors` actuator so a catalog *definition* (`def`) IS found,
// which isolates the connection-gate as the sole disabled signal in
// Case 2.  Cases 1 and 3 rely on this fixture too.
const CATALOG_WITH_LOCK: Record<string, unknown[]> = {
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

const EMPTY_CATALOG: Record<string, unknown[]> = {};

function makeFetch(catalog: Record<string, unknown[]>) {
  return vi.fn().mockImplementation((url: string) => {
    if (typeof url === 'string' && url.includes('/catalog')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ actuators: catalog }),
      });
    }
    if (typeof url === 'string' && url.includes('/geofences')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ geofences: [] }),
      });
    }
    // Command history endpoint — keyed on vehicleId path segment
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ commands: [] }),
    });
  });
}

// ── Import under test (after mocks) ────────────────────────────────────────
import RemoteCommandsPanel from '../RemoteCommandsPanel';

// ── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Wait for the catalog fetch to resolve so Buttons render in their final
 * enabled/disabled state.  The component calls `fetchCatalog` inside
 * `useEffect`, so the initial render has an empty catalog (all buttons
 * disabled via `!def`).  We wait for the "Lock All Doors" button to appear
 * before asserting button state.
 *
 * IMPORTANT: must use exact name match. The Quick Actions grid also renders
 * "Unlock All Doors" (for lock_all_doors val=false), and /lock all doors/i
 * matches both because "Unlock" contains "lock". Using exact:true restricts
 * the match to the button whose accessible name is exactly "Lock All Doors".
 *
 * Uses `findByRole` (async) which retries until the element appears or
 * the default 1000 ms timeout fires.
 */
async function waitForLockButton() {
  return screen.findByRole('button', { name: 'Lock All Doors', exact: true });
}

// ── Case 1: CONNECTED — button must be enabled ──────────────────────────────
describe('RemoteCommandsPanel — connection gate', () => {
  describe('Case 1: connected vehicle with catalog definition present', () => {
    beforeEach(() => {
      global.fetch = makeFetch(CATALOG_WITH_LOCK) as unknown as typeof fetch;
    });

    it('renders the Lock All Doors button enabled when connectionStatus is "connected"', async () => {
      render(
        <RemoteCommandsPanel
          vehicleId="VEH-MICH-001"
          connectionStatus="connected"
        />,
      );

      const button = await waitForLockButton();

      // The button must NOT be disabled — a connected vehicle can accept
      // remote commands.
      expect(button).not.toBeDisabled();
    });

    it('does not render a disconnection affordance when vehicle is connected', async () => {
      render(
        <RemoteCommandsPanel
          vehicleId="VEH-MICH-001"
          connectionStatus="connected"
        />,
      );

      // Wait for the panel to be fully hydrated before asserting absence.
      await waitForLockButton();

      // No disconnection warning should be visible.
      expect(
        screen.queryByText(/disconnected/i),
      ).not.toBeInTheDocument();
    });
  });

  // ── Case 2: DISCONNECTED — button must be DISABLED + affordance present ──
  //
  // THIS CASE MUST FAIL PRE-IMPLEMENTATION.
  //
  // Failing assertion:
  //   expect(button).toBeDisabled()
  //     → received: <button ... disabled={false}>Lock All Doors</button>
  //   The button is NOT disabled because `disabled={!def}` at line 212
  //   does not consult connectionStatus. `def` IS defined (catalog has
  //   the entry), so `!def` is false, so disabled=false regardless of
  //   connectionStatus.
  //
  // Why this is the correct red-phase signal: the test encodes the spec
  // requirement that a disconnected vehicle may NOT receive commands.
  // Once Group 4 adds `disabled={!def || connectionStatus !== 'connected'}`,
  // both assertions below pass and the case turns green.
  describe('Case 2: disconnected vehicle with catalog definition present', () => {
    beforeEach(() => {
      global.fetch = makeFetch(CATALOG_WITH_LOCK) as unknown as typeof fetch;
    });

    it('renders the Lock All Doors button DISABLED when connectionStatus is "disconnected"', async () => {
      render(
        <RemoteCommandsPanel
          vehicleId="VEH-MICH-001"
          connectionStatus="disconnected"
        />,
      );

      const button = await waitForLockButton();

      // PRE-IMPLEMENTATION: this assertion FAILS because `disabled={!def}`
      // does not consult connectionStatus.  The button is currently enabled
      // even for disconnected vehicles.
      expect(button).toBeDisabled();
    });

    it('renders an explanatory affordance the operator can act on', async () => {
      render(
        <RemoteCommandsPanel
          vehicleId="VEH-MICH-001"
          connectionStatus="disconnected"
        />,
      );

      // Wait for the panel to resolve its async fetch before asserting
      // the affordance — it should be rendered regardless of catalog state.
      await waitForLockButton();

      // An Alert or StatusIndicator containing the word "disconnected"
      // must appear so the operator knows WHY commands are disabled and
      // can take action (reconnect, restart agent, etc.).
      //
      // PRE-IMPLEMENTATION: this assertion ALSO FAILS because no such
      // affordance exists in the current component.
      expect(
        screen.getByText(/disconnected/i),
      ).toBeInTheDocument();
    });
  });

  // ── Case 4: UNDEFINED / NULL connectionStatus — button must be DISABLED ───
  //
  // The allow-list gate (=== 'connected') must fail closed for any value that
  // is not explicitly 'connected'. This includes undefined (the realistic case
  // when a caller does not yet pass the prop) and null.  A deny-list gate
  // (!== 'disconnected') would pass these through as enabled — which is wrong.
  //
  // These cases must be GREEN only after Group 4 adds the allow-list gate.
  // Pre-implementation they were also GREEN for the wrong reason (the old
  // disabled={!def} gate disables when the catalog is empty, but with a
  // full catalog and connectionStatus=undefined, the button was enabled).
  // After Group 4, they must be GREEN for the right reason: undefined/null
  // fail the allow-list check and disable the button.
  describe('Case 4: connectionStatus is undefined or null — button must be DISABLED', () => {
    beforeEach(() => {
      global.fetch = makeFetch(CATALOG_WITH_LOCK) as unknown as typeof fetch;
    });

    it('renders the Lock All Doors button DISABLED when connectionStatus is undefined (prop not passed)', async () => {
      render(
        <RemoteCommandsPanel
          vehicleId="VEH-MICH-001"
          // connectionStatus intentionally omitted — simulates a caller
          // that does not yet plumb the prop through. The allow-list gate
          // (=== 'connected') must fail closed here.
        />,
      );

      const button = await waitForLockButton();

      // undefined must not be treated as 'connected'.  Allow-list gate
      // requires positive proof of connection; absence of proof = disabled.
      expect(button).toBeDisabled();
    });

    it('renders the Lock All Doors button DISABLED when connectionStatus is null', async () => {
      render(
        <RemoteCommandsPanel
          vehicleId="VEH-MICH-001"
          connectionStatus={null as unknown as string}
        />,
      );

      const button = await waitForLockButton();

      // null must not be treated as 'connected'.
      expect(button).toBeDisabled();
    });

    it('renders the disconnection affordance when connectionStatus is undefined', async () => {
      render(
        <RemoteCommandsPanel
          vehicleId="VEH-MICH-001"
        />,
      );

      await waitForLockButton();

      // The affordance should appear any time connectionStatus !== 'connected',
      // including undefined — it tells the operator what to do next.
      expect(
        screen.getByText(/disconnected/i),
      ).toBeInTheDocument();
    });
  });

  // ── Case 3: MISSING CATALOG DEFINITION — button disabled (existing behaviour)
  //
  // This case is GREEN today.  It encodes the existing `disabled={!def}`
  // behaviour so Group 4 cannot accidentally remove it while adding the
  // connection gate.  Preserve this regardless of connectionStatus.
  describe('Case 3: missing catalog definition (connected vehicle, empty catalog)', () => {
    beforeEach(() => {
      global.fetch = makeFetch(EMPTY_CATALOG) as unknown as typeof fetch;
    });

    it('renders the Lock All Doors button DISABLED when no catalog definition exists', async () => {
      render(
        <RemoteCommandsPanel
          vehicleId="VEH-MICH-001"
          connectionStatus="connected"
        />,
      );

      // With an empty catalog, `def` is `undefined` in quickActions.map,
      // so `!def` is true.  The button must remain disabled — this is the
      // pre-existing behavior that protects against sending commands with
      // no known catalog entry.
      //
      // The button renders immediately (no catalog definition needed to
      // render the label), so we don't need to wait for a catalog fetch
      // here — but the `findByRole` is still async-safe.
      const button = await screen.findByRole('button', { name: 'Lock All Doors', exact: true });
      expect(button).toBeDisabled();
    });

    it('does not render the disconnection affordance when catalog is missing (not a connection issue)', async () => {
      render(
        <RemoteCommandsPanel
          vehicleId="VEH-MICH-001"
          connectionStatus="connected"
        />,
      );

      await screen.findByRole('button', { name: 'Lock All Doors', exact: true });

      // The button is disabled for a different reason (no definition),
      // not because the vehicle is disconnected.  Group 4 must NOT show
      // the disconnection affordance in this state — doing so would
      // mislead the operator into thinking they need to reconnect when
      // the real problem is a missing catalog entry.
      expect(
        screen.queryByText(/disconnected/i),
      ).not.toBeInTheDocument();
    });
  });
});
