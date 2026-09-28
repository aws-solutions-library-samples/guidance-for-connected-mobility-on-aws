// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * F8 — fleet picker URL+localStorage
 * Assertions:
 *  - URL param `?fleet=<id>` overrides localStorage on initial mount.
 *  - setSelectedId persists to localStorage.
 *  - First-visit default depends on role:
 *      * cross-fleet (platform-admin, fleet-viewer) → ALL_FLEETS_ID
 *      * scoped     (fleet-operator, fleet-guest, dispatcher) → first
 *                                                                custom:fleetIds entry
 *  - Options include "All my fleets" ONLY for cross-fleet roles; scoped roles
 *    see the intersection of API-returned fleets and `custom:fleetIds`.
 *
 * Source-of-truth: main_api `/api/v1/fleets` via `authFetch` (rev 4 — switched
 * from the stubbed `ApiContext.client.send(ListFleetsCommand)` after the
 * 2026-06-15 Tokyo Create-Vehicle empty-fleet-picker bug; see
 * `issues/2026-06-15-create-vehicle-fleet-picker-empty/`).
 *
 * Role-awareness added 2026-09-26 as follow-on 1 to
 * `issues/2026-09-25-fleet-intelligence-routes-trust-caller-fleet-id/` — the FI
 * routes now 403 a portal-wide request from a scoped caller, and the picker's
 * pre-fix default ALL_FLEETS_ID would produce exactly that request on first
 * page load.
 */

import { renderHook, act, waitFor, render } from '@testing-library/react';
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import { useFleetSelection, ALL_FLEETS_ID } from '../useFleetSelection';

const STORAGE_KEY = 'cms-fleet-picker-selection';

// ── runtime config + authFetch mocks ─────────────────────────────────────────
//
// `useFleetSelection` calls `authFetch('${apiEndpoint}/api/v1/fleets')`. Mock
// the module so we control the resolved JSON shape per test.
vi.mock('@/utils/authFetch', () => ({
  authFetch: vi.fn(),
}));

vi.mock('@/config/api', () => ({
  getRuntimeConfig: () => ({ apiEndpoint: 'http://localhost:5001' }),
}));

// ── useUserRole mock ─────────────────────────────────────────────────────────
//
// `useFleetSelection` reads role → cross-fleet vs scoped default. Without a
// mock, `useUserRole` calls `useAuth` → `useSimpleAuth`, which throws outside a
// `SimpleAuthProvider`. Default mock = platform-admin (cross-fleet) so the
// original F8 tests keep asserting the ALL_FLEETS_ID default.
vi.mock('@/auth/useUserRole', () => ({ useUserRole: vi.fn() }));

import { authFetch } from '@/utils/authFetch';
import { useUserRole } from '@/auth/useUserRole';

const authFetchMock = vi.mocked(authFetch);
const mockUseUserRole = vi.mocked(useUserRole);

type Role = ReturnType<typeof useUserRole>;
const makeRole = (overrides: Partial<Role> = {}): Role => ({
  isAdmin: false,
  isOperator: false,
  isViewer: false,
  isGuest: false,
  isConnectAgent: false,
  isEngineer: false,
  isDispatcher: false,
  canWrite: false,
  fleetIds: [],
  ...overrides,
});

const FLEETS_OK = (fleets: any[]) =>
  Promise.resolve({
    ok: true,
    status: 200,
    json: async () => ({ fleets }),
    text: async () => JSON.stringify({ fleets }),
  } as unknown as Response);

const FLEETS_FAIL = (status: number, body: string) =>
  Promise.resolve({
    ok: false,
    status,
    json: async () => ({}),
    text: async () => body,
  } as unknown as Response);

// ── mock fleet data ──────────────────────────────────────────────────────────
// Backend ddb shape: `fleetId` is the partition key (NOT `id`); the picker
// must normalize this, per the 2026-06-15 fix.
const MOCK_FLEETS = [
  { fleetId: 'fleet-001', name: 'Alpha Fleet' },
  { fleetId: 'fleet-002', name: 'Beta Fleet' },
];

// ── reset env between tests ──────────────────────────────────────────────────
beforeEach(() => {
  localStorage.clear();
  // Reset URL to no query params.
  window.history.replaceState({}, '', '/');
  authFetchMock.mockReset();
  authFetchMock.mockImplementation(() => FLEETS_OK(MOCK_FLEETS));
  mockUseUserRole.mockReset();
  // Default: cross-fleet admin — keeps the pre-2026-09-26 F8 assertions valid.
  mockUseUserRole.mockReturnValue(makeRole({ isAdmin: true, canWrite: true }));
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ── tests ─────────────────────────────────────────────────────────────────────

describe('F8 — fleet picker: first-visit default (cross-fleet role)', () => {
  it('defaults to ALL_FLEETS_ID when localStorage is empty and no URL param', async () => {
    const { result } = renderHook(() => useFleetSelection());

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.selectedId).toBe(ALL_FLEETS_ID);
  });
});

describe('F8 — fleet picker: URL param overrides localStorage on initial mount', () => {
  it('uses URL ?fleet=<id> even when localStorage has a different value', async () => {
    // Pre-seed localStorage with a different fleet.
    localStorage.setItem(STORAGE_KEY, 'fleet-001');
    // Set URL param to fleet-002.
    window.history.replaceState({}, '', '/?fleet=fleet-002');

    const { result } = renderHook(() => useFleetSelection());

    await waitFor(() => expect(result.current.loading).toBe(false));

    // URL wins — should be fleet-002, not fleet-001 from localStorage.
    expect(result.current.selectedId).toBe('fleet-002');
  });

  it('reads URL param even on an empty localStorage (first visit)', async () => {
    window.history.replaceState({}, '', '/?fleet=fleet-001');

    const { result } = renderHook(() => useFleetSelection());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.selectedId).toBe('fleet-001');
  });
});

describe('F8 — fleet picker: localStorage persistence', () => {
  it('persists selection to localStorage when setSelectedId is called', async () => {
    const { result } = renderHook(() => useFleetSelection());

    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => {
      result.current.setSelectedId('fleet-001');
    });

    expect(result.current.selectedId).toBe('fleet-001');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('fleet-001');
  });

  it('reads persisted value from localStorage on remount (no URL param)', async () => {
    localStorage.setItem(STORAGE_KEY, 'fleet-002');

    const { result } = renderHook(() => useFleetSelection());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.selectedId).toBe('fleet-002');
  });
});

describe('F8 — fleet picker: options include All my fleets + API fleets (cross-fleet role)', () => {
  it('includes "All my fleets" as first option and maps API fleets', async () => {
    const { result } = renderHook(() => useFleetSelection());

    await waitFor(() => expect(result.current.loading).toBe(false));

    const ids = result.current.options.map((o) => o.id);
    expect(ids[0]).toBe(ALL_FLEETS_ID);
    expect(ids).toContain('fleet-001');
    expect(ids).toContain('fleet-002');

    expect(result.current.options[0].name).toBe('All my fleets');
  });

  it('shows only "All my fleets" when API returns empty list', async () => {
    authFetchMock.mockImplementationOnce(() => FLEETS_OK([]));
    const { result } = renderHook(() => useFleetSelection());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.options).toHaveLength(1);
    expect(result.current.options[0].id).toBe(ALL_FLEETS_ID);
  });

  it('surfaces error when API returns non-OK', async () => {
    authFetchMock.mockImplementationOnce(() => FLEETS_FAIL(500, 'boom'));
    const { result } = renderHook(() => useFleetSelection());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toMatch(/500/);
  });
});

describe('F8 — fleet picker: main_api /api/v1/fleets is the source-of-truth (rev 4)', () => {
  it('calls authFetch with the configured apiEndpoint + /api/v1/fleets', async () => {
    const { result } = renderHook(() => useFleetSelection());

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(authFetchMock).toHaveBeenCalledTimes(1);
    const url = authFetchMock.mock.calls[0][0] as string;
    expect(url).toBe('http://localhost:5001/api/v1/fleets');
  });
});

// ── 2026-06-15 regression: Tokyo Create-Vehicle empty-fleet-picker ───────────
//
// The bug: backend ddb fleets are keyed by `fleetId` (not `id`) and carry
// `data_source: 'vehicle-telemetry'` per the 2026-06-09 refactor. The hook
// must normalize `id ← fleetId` so the option's `value` matches the DDB key
// and the FleetPicker can render it. `data_source` MUST also be preserved on
// `rawFleets` so the optional `dataSourceFilter` continues to work.
describe('F8 — Tokyo regression (2026-06-15): vehicle-telemetry fleet renders', () => {
  it('normalizes `fleetId` to `id` and surfaces a `vehicle-telemetry` fleet', async () => {
    authFetchMock.mockImplementationOnce(() =>
      FLEETS_OK([
        {
          fleetId: 'FLEET-1781545790',
          name: 'Tokyo Demo Fleet',
          description: 'Tokyo staging — single fleet',
          status: 'ACTIVE',
          data_source: 'vehicle-telemetry',
          default_vehicle_model_id: 'model-x',
          vehicleCount: 0,
        },
      ]),
    );

    const { result } = renderHook(() => useFleetSelection());
    await waitFor(() => expect(result.current.loading).toBe(false));

    // Sentinel + 1 fleet.
    expect(result.current.options).toHaveLength(2);
    expect(result.current.options[1].id).toBe('FLEET-1781545790');
    expect(result.current.options[1].name).toBe('Tokyo Demo Fleet');

    // rawFleets preserves data_source for downstream filter logic.
    expect(result.current.rawFleets).toHaveLength(1);
    expect(result.current.rawFleets[0].data_source).toBe('vehicle-telemetry');
    expect(result.current.rawFleets[0].id).toBe('FLEET-1781545790');
  });

  it('a fleet with missing `data_source` (legacy default) is also rendered', async () => {
    authFetchMock.mockImplementationOnce(() =>
      FLEETS_OK([
        {
          fleetId: 'FLEET-LEGACY-001',
          name: 'Legacy Fleet',
          status: 'ACTIVE',
          // data_source intentionally omitted — legacy default == vehicle-telemetry
          vehicleCount: 0,
        },
      ]),
    );

    const { result } = renderHook(() => useFleetSelection());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.options).toHaveLength(2);
    expect(result.current.options[1].id).toBe('FLEET-LEGACY-001');
    expect(result.current.rawFleets[0].data_source).toBeUndefined();
  });
});

// ── F8-role (2026-09-26): scoped-role default + sentinel drop + stored replacement
//
// Follow-on 1 to `issues/2026-09-25-fleet-intelligence-routes-trust-caller-
// fleet-id/summary.md`. The FI backend now 403s a scoped caller who requests
// portal-wide scope. The picker's ALL_FLEETS_ID default would produce exactly
// that request on first page load, so scoped callers must default to a
// specific fleet.
//
// Role classes mirror `services/fleet_intelligence/_auth.py` (and
// `auth/useUserRole.ts`):
//   cross-fleet: platform-admin, fleet-viewer
//   scoped:      fleet-operator, fleet-guest, dispatcher

describe('F8-role — scoped role: default is first custom:fleetIds entry', () => {
  it('fleet-operator with two owned fleets defaults to the first one', async () => {
    mockUseUserRole.mockReturnValue(
      makeRole({ isOperator: true, canWrite: true, fleetIds: ['fleet-op-A', 'fleet-op-B'] }),
    );
    const { result } = renderHook(() => useFleetSelection());

    await waitFor(() => expect(result.current.loading).toBe(false));

    // Load-bearing: NOT ALL_FLEETS_ID, and specifically the first entry.
    expect(result.current.selectedId).toBe('fleet-op-A');
    expect(result.current.selectedId).not.toBe(ALL_FLEETS_ID);
  });

  it('fleet-guest with one owned fleet defaults to that fleet', async () => {
    mockUseUserRole.mockReturnValue(
      makeRole({ isGuest: true, fleetIds: ['guest-only-fleet'] }),
    );
    const { result } = renderHook(() => useFleetSelection());

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.selectedId).toBe('guest-only-fleet');
    expect(result.current.selectedId).not.toBe(ALL_FLEETS_ID);
  });

  it('dispatcher (read-only monitoring persona) also defaults to its first fleet', async () => {
    mockUseUserRole.mockReturnValue(
      makeRole({ isDispatcher: true, fleetIds: ['disp-fleet-X', 'disp-fleet-Y'] }),
    );
    const { result } = renderHook(() => useFleetSelection());

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.selectedId).toBe('disp-fleet-X');
  });
});

describe('F8-role — scoped role: options drop the "All my fleets" sentinel', () => {
  it('fleet-operator sees a picker with NO sentinel; options are the intersection of API-returned fleets and custom:fleetIds', async () => {
    // API returns 3 fleets; the operator only holds 2 of them.
    authFetchMock.mockImplementationOnce(() =>
      FLEETS_OK([
        { fleetId: 'fleet-op-A', name: 'Alpha' },
        { fleetId: 'fleet-op-B', name: 'Beta' },
        { fleetId: 'fleet-other', name: 'Not Mine' },
      ]),
    );
    mockUseUserRole.mockReturnValue(
      makeRole({ isOperator: true, fleetIds: ['fleet-op-A', 'fleet-op-B'] }),
    );

    const { result } = renderHook(() => useFleetSelection());
    await waitFor(() => expect(result.current.loading).toBe(false));

    const ids = result.current.options.map((o) => o.id);
    // Sentinel MUST be dropped, and only owned fleets remain.
    expect(ids).not.toContain(ALL_FLEETS_ID);
    expect(ids).toEqual(['fleet-op-A', 'fleet-op-B']);
  });

  it('dispatcher sees no sentinel even when API returns fleets outside its claim', async () => {
    authFetchMock.mockImplementationOnce(() =>
      FLEETS_OK([
        { fleetId: 'disp-fleet-X', name: 'X' },
        { fleetId: 'disp-fleet-Y', name: 'Y' },
        { fleetId: 'admin-fleet-Z', name: 'Z' },
      ]),
    );
    mockUseUserRole.mockReturnValue(
      makeRole({ isDispatcher: true, fleetIds: ['disp-fleet-X', 'disp-fleet-Y'] }),
    );

    const { result } = renderHook(() => useFleetSelection());
    await waitFor(() => expect(result.current.loading).toBe(false));

    const ids = result.current.options.map((o) => o.id);
    expect(ids).not.toContain(ALL_FLEETS_ID);
    expect(ids).not.toContain('admin-fleet-Z');
    expect(ids).toEqual(['disp-fleet-X', 'disp-fleet-Y']);
  });
});

describe('F8-role — scoped role: a stored ALL_FLEETS_ID selection is replaced with the first owned fleet', () => {
  it('replaces localStorage ALL_FLEETS_ID sentinel with fleetIds[0] on init', async () => {
    // Simulate a scoped caller whose localStorage was seeded before this
    // fix landed (the pre-fix default was ALL_FLEETS_ID for every role).
    localStorage.setItem(STORAGE_KEY, ALL_FLEETS_ID);
    mockUseUserRole.mockReturnValue(
      makeRole({ isOperator: true, fleetIds: ['fleet-op-A', 'fleet-op-B'] }),
    );

    const { result } = renderHook(() => useFleetSelection());
    await waitFor(() => expect(result.current.loading).toBe(false));

    // The load-bearing assertion — with the fix reverted (initial default =
    // ALL_FLEETS_ID again), this fails. See the recorded mutation in the
    // commit message + issue summary.
    expect(result.current.selectedId).toBe('fleet-op-A');
    expect(result.current.selectedId).not.toBe(ALL_FLEETS_ID);
    // And the stored value is refreshed to the new selection so a subsequent
    // page load starts from the right place.
    expect(localStorage.getItem(STORAGE_KEY)).toBe('fleet-op-A');
  });

  it('URL ?fleet=__all__ is also rejected for a scoped caller (they cannot request portal-wide)', async () => {
    window.history.replaceState({}, '', `/?fleet=${encodeURIComponent(ALL_FLEETS_ID)}`);
    mockUseUserRole.mockReturnValue(
      makeRole({ isOperator: true, fleetIds: ['fleet-op-A'] }),
    );

    const { result } = renderHook(() => useFleetSelection());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.selectedId).toBe('fleet-op-A');
  });

  it('a stored fleetId the caller no longer holds is replaced with the first owned fleet', async () => {
    localStorage.setItem(STORAGE_KEY, 'fleet-i-do-not-own');
    mockUseUserRole.mockReturnValue(
      makeRole({ isOperator: true, fleetIds: ['owned-A', 'owned-B'] }),
    );

    const { result } = renderHook(() => useFleetSelection());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.selectedId).toBe('owned-A');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('owned-A');
  });

  it('a stored fleetId the caller DOES hold is preserved (no spurious replacement)', async () => {
    localStorage.setItem(STORAGE_KEY, 'owned-B');
    mockUseUserRole.mockReturnValue(
      makeRole({ isOperator: true, fleetIds: ['owned-A', 'owned-B'] }),
    );

    const { result } = renderHook(() => useFleetSelection());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.selectedId).toBe('owned-B');
  });
});

describe('F8-role — scoped role with no fleets: empty state, not an error', () => {
  it('fleetIds=[] → selectedId="", options=[], no error surfaced', async () => {
    mockUseUserRole.mockReturnValue(makeRole({ isOperator: true, fleetIds: [] }));

    const { result } = renderHook(() => useFleetSelection());
    await waitFor(() => expect(result.current.loading).toBe(false));

    // Empty state: no selection, no sentinel, no options (the API returned
    // 2 fleets from the default mock but the operator holds none).
    expect(result.current.selectedId).toBe('');
    expect(result.current.options).toEqual([]);
    expect(result.current.error).toBeNull();
  });

  it('fleetIds=[] clears any stored selection from localStorage so a subsequent visit starts fresh', async () => {
    localStorage.setItem(STORAGE_KEY, 'orphaned-fleet');
    mockUseUserRole.mockReturnValue(makeRole({ isOperator: true, fleetIds: [] }));

    const { result } = renderHook(() => useFleetSelection());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.selectedId).toBe('');
    // The old orphaned value must not persist; leaving it there would mean the
    // next role change (e.g. operator regains a fleet) starts from a stale
    // pointer instead of the fresh role-appropriate default.
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });
});

describe('F8-role — cross-fleet role: unchanged behaviour (regression guard)', () => {
  it('fleet-viewer sees the ALL_FLEETS_ID default and the sentinel in options', async () => {
    // useUserRole treats fleet-viewer as cross-fleet (UNSCOPED global-read in
    // main_api), matching services/fleet_intelligence/_auth.py's rule.
    mockUseUserRole.mockReturnValue(makeRole({ isViewer: true }));

    const { result } = renderHook(() => useFleetSelection());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.selectedId).toBe(ALL_FLEETS_ID);
    expect(result.current.options[0].id).toBe(ALL_FLEETS_ID);
  });

  it('platform-admin does NOT have its options filtered even when the API returns fleets outside its claim', async () => {
    authFetchMock.mockImplementationOnce(() =>
      FLEETS_OK([
        { fleetId: 'fleet-A', name: 'A' },
        { fleetId: 'fleet-B', name: 'B' },
      ]),
    );
    // Admin with empty fleetIds is the normal shape — admins do not carry a
    // per-user allowlist because they have cross-fleet READ.
    mockUseUserRole.mockReturnValue(makeRole({ isAdmin: true, fleetIds: [] }));

    const { result } = renderHook(() => useFleetSelection());
    await waitFor(() => expect(result.current.loading).toBe(false));

    // Sentinel first, then every fleet the API returned.
    const ids = result.current.options.map((o) => o.id);
    expect(ids).toEqual([ALL_FLEETS_ID, 'fleet-A', 'fleet-B']);
  });
});

// ── F8-filter: dataSourceFilter prop ─────────────────────────────────────────
//
// Tests for the optional `dataSourceFilter` prop on FleetPicker.
// We mock `useFleetSelection` to inject controlled rawFleets with different
// data_source values, then render FleetPicker and assert which options are
// passed to the underlying Cloudscape Select.
//
// Fleet inventory used across all 3 cases:
//   fleet-vt  : data_source = 'vehicle-telemetry'
//   fleet-ct  : data_source = 'cloud-telemetry'
//   fleet-old : data_source = 'cloud-oem1'   (legacy → cloud-telemetry via getFleetDataSource)
//   fleet-nil : data_source = undefined       (missing → vehicle-telemetry legacy default)

import FleetPicker from '../FleetPicker';
import * as useFleetSelectionModule from '../useFleetSelection';
import { ALL_FLEETS_ID as _ALL_FLEETS_ID } from '../useFleetSelection';

const ALL = _ALL_FLEETS_ID;

const RAW_FLEETS = [
  { id: 'fleet-vt',  name: 'VT Fleet',     data_source: 'vehicle-telemetry' },
  { id: 'fleet-ct',  name: 'CT Fleet',     data_source: 'cloud-telemetry' },
  { id: 'fleet-old', name: 'Old OEM Fleet', data_source: 'cloud-oem1' },
  { id: 'fleet-nil', name: 'Legacy Fleet',  data_source: undefined },
];

const BASE_OPTIONS = [
  { id: ALL, name: 'All my fleets' },
  { id: 'fleet-vt',  name: 'VT Fleet' },
  { id: 'fleet-ct',  name: 'CT Fleet' },
  { id: 'fleet-old', name: 'Old OEM Fleet' },
  { id: 'fleet-nil', name: 'Legacy Fleet' },
];

function mockHook() {
  vi.spyOn(useFleetSelectionModule, 'useFleetSelection').mockReturnValue({
    options: BASE_OPTIONS,
    rawFleets: RAW_FLEETS as any,
    selectedId: ALL,
    setSelectedId: vi.fn(),
    loading: false,
    error: null,
  });
}

// Capture the options prop passed to Cloudscape Select by mocking the module.
vi.mock('@cloudscape-design/components', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@cloudscape-design/components')>();
  return {
    ...actual,
    Select: ({ options, ...rest }: any) =>
      React.createElement('select', { 'data-testid': 'fleet-select', ...rest },
        (options ?? []).map((o: any) =>
          React.createElement('option', { key: o.value, value: o.value }, o.label)
        ),
      ),
    FormField: ({ children }: any) => React.createElement('div', null, children),
  };
});

describe('F8-filter — dataSourceFilter prop', () => {
  beforeEach(() => {
    mockHook();
  });

  it('(a) filter undefined → all fleets shown', () => {
    const { getByTestId } = render(<FleetPicker />);
    const select = getByTestId('fleet-select');
    const values = Array.from(select.querySelectorAll('option')).map((o) => o.getAttribute('value'));
    expect(values).toEqual([ALL, 'fleet-vt', 'fleet-ct', 'fleet-old', 'fleet-nil']);
  });

  it('(b) filter cloud-telemetry → only cloud-telemetry fleets (incl dual-read cloud-oem1)', () => {
    const { getByTestId } = render(<FleetPicker dataSourceFilter="cloud-telemetry" />);
    const select = getByTestId('fleet-select');
    const values = Array.from(select.querySelectorAll('option')).map((o) => o.getAttribute('value'));
    // sentinel + cloud-telemetry + cloud-oem1 (dual-read); vehicle-telemetry and nil excluded
    expect(values).toEqual([ALL, 'fleet-ct', 'fleet-old']);
  });

  it('(c) filter vehicle-telemetry → vehicle-telemetry + missing-attribute fleets', () => {
    const { getByTestId } = render(<FleetPicker dataSourceFilter="vehicle-telemetry" />);
    const select = getByTestId('fleet-select');
    const values = Array.from(select.querySelectorAll('option')).map((o) => o.getAttribute('value'));
    // sentinel + vehicle-telemetry + nil (missing attr → legacy default); cloud-* excluded
    expect(values).toEqual([ALL, 'fleet-vt', 'fleet-nil']);
  });
});
