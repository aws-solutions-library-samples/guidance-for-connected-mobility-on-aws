// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * F8 — fleet picker URL+localStorage
 * Assertions:
 *  - URL param `?fleet=<id>` overrides localStorage on initial mount.
 *  - setSelectedId persists to localStorage.
 *  - First-visit default is ALL_FLEETS_ID.
 *  - Options include "All my fleets" + API-returned fleets.
 *
 * Source-of-truth: Fleet Management API via ApiContext (rev 3 A3).
 */

import { renderHook, act, waitFor } from '@testing-library/react';
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import { useFleetSelection, ALL_FLEETS_ID, STORAGE_KEY as _STORAGE_KEY } from '../useFleetSelection';
import { ApiContext, ApiContextValue } from '@/api/provider';

const STORAGE_KEY = 'cms-fleet-picker-selection';

// ── mock fleet data ──────────────────────────────────────────────────────────
const MOCK_FLEETS = [
  { id: 'fleet-001', name: 'Alpha Fleet' },
  { id: 'fleet-002', name: 'Beta Fleet' },
];

// ── helpers ──────────────────────────────────────────────────────────────────
const makeClient = (fleets = MOCK_FLEETS) => ({
  send: vi.fn().mockResolvedValue({ fleets }),
});

const makeWrapper = (client: ReturnType<typeof makeClient>) => {
  const ctx: Partial<ApiContextValue> = { client: client as any };
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(ApiContext.Provider, { value: ctx as ApiContextValue }, children);
};

// ── reset env between tests ──────────────────────────────────────────────────
beforeEach(() => {
  localStorage.clear();
  // Reset URL to no query params.
  window.history.replaceState({}, '', '/');
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ── tests ─────────────────────────────────────────────────────────────────────

describe('F8 — fleet picker: first-visit default', () => {
  it('defaults to ALL_FLEETS_ID when localStorage is empty and no URL param', async () => {
    const client = makeClient();
    const { result } = renderHook(() => useFleetSelection(), {
      wrapper: makeWrapper(client),
    });

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

    const client = makeClient();
    const { result } = renderHook(() => useFleetSelection(), {
      wrapper: makeWrapper(client),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));

    // URL wins — should be fleet-002, not fleet-001 from localStorage.
    expect(result.current.selectedId).toBe('fleet-002');
  });

  it('reads URL param even on an empty localStorage (first visit)', async () => {
    window.history.replaceState({}, '', '/?fleet=fleet-001');

    const client = makeClient();
    const { result } = renderHook(() => useFleetSelection(), {
      wrapper: makeWrapper(client),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.selectedId).toBe('fleet-001');
  });
});

describe('F8 — fleet picker: localStorage persistence', () => {
  it('persists selection to localStorage when setSelectedId is called', async () => {
    const client = makeClient();
    const { result } = renderHook(() => useFleetSelection(), {
      wrapper: makeWrapper(client),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => {
      result.current.setSelectedId('fleet-001');
    });

    expect(result.current.selectedId).toBe('fleet-001');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('fleet-001');
  });

  it('reads persisted value from localStorage on remount (no URL param)', async () => {
    localStorage.setItem(STORAGE_KEY, 'fleet-002');

    const client = makeClient();
    const { result } = renderHook(() => useFleetSelection(), {
      wrapper: makeWrapper(client),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.selectedId).toBe('fleet-002');
  });
});

describe('F8 — fleet picker: options include All my fleets + API fleets', () => {
  it('includes "All my fleets" as first option and maps API fleets', async () => {
    const client = makeClient();
    const { result } = renderHook(() => useFleetSelection(), {
      wrapper: makeWrapper(client),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));

    const ids = result.current.options.map((o) => o.id);
    expect(ids[0]).toBe(ALL_FLEETS_ID);
    expect(ids).toContain('fleet-001');
    expect(ids).toContain('fleet-002');

    expect(result.current.options[0].name).toBe('All my fleets');
  });

  it('shows only "All my fleets" when API returns empty list', async () => {
    const client = makeClient([]);
    const { result } = renderHook(() => useFleetSelection(), {
      wrapper: makeWrapper(client),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.options).toHaveLength(1);
    expect(result.current.options[0].id).toBe(ALL_FLEETS_ID);
  });
});

describe('F8 — fleet picker: Fleet Management API is the source-of-truth (rev 3 A3)', () => {
  it('calls ApiContext.client.send with ListFleetsCommand', async () => {
    const client = makeClient();
    const { result } = renderHook(() => useFleetSelection(), {
      wrapper: makeWrapper(client),
    });

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(client.send).toHaveBeenCalledTimes(1);
    const callArg = client.send.mock.calls[0][0];
    // The argument must be a ListFleetsCommand (or equivalent object that
    // the real-fleet client's send() dispatches on via command name).
    expect(callArg.constructor.name).toBe('ListFleetsCommand');
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

import { render } from '@testing-library/react';
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
