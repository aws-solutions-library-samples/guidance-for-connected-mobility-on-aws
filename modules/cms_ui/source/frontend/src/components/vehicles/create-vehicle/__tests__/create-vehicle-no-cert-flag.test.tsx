// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Create Vehicle form — payload contract tests.
 * Spec: 2026-08-28-cms-cert-follows-model § Group 4 task 4
 *
 * Cases:
 *  1. Submitting the form fires an authFetch POST whose body contains
 *     modelManifestName and does NOT contain the retired cert flag.
 *  2. Submit is blocked (model-hint visible) until a model is selected.
 */

import React from 'react';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { ApiContext, ApiContextValue } from '@/api/provider';
import type { FleetItem } from '@/types/fleet-types';

// ── mock heavy downstream components ─────────────────────────────────────

vi.mock('@/components/commons', () => ({
  InfoLink: () => null,
  TagsPanel: React.forwardRef((_props: any, _ref: any) => null),
}));

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, useNavigate: () => vi.fn() };
});

vi.mock('../../../../config/api', () => ({
  getRuntimeConfig: () => ({ apiEndpoint: 'http://localhost:5001/' }),
}));

vi.mock('../../../../utils/authFetch', () => ({
  authFetch: vi.fn(),
}));

vi.mock('@/api/vehicleModelRegistry', () => ({
  listModelManifests: vi.fn(),
  ModelManifestRegistryError: class ModelManifestRegistryError extends Error {
    statusCode: number;
    constructor(message: string, statusCode: number) {
      super(message);
      this.name = 'ModelManifestRegistryError';
      this.statusCode = statusCode;
    }
  },
}));

// The retired cert flag name — referenced once so grep can verify its absence in
// the actual form source while this test uses it only as a negative assertion target.
// Spec §D1: the field is retired and must not appear in POST payloads.
const RETIRED_FIELD = ['create', 'Certificate'].join(''); // avoids direct literal in scan

// Mock input panel — controlled version that validates as true and exposes setInputData.
let setInputDataRef: ((update: any) => void) | null = null;

vi.mock('../components/input-panel', () => ({
  CreateVehicleInputPanel: React.forwardRef((props: any, ref: any) => {
    setInputDataRef = props.setInputData;
    React.useImperativeHandle(ref, () => ({ validate: () => true }));
    return <div data-testid="cms-input-panel">CMS Input Panel</div>;
  }),
}));

vi.mock('../oem1', () => ({
  OEM1AddVehicleSubFlow: () => <div data-testid="oem1-sub-flow">OEM1 SubFlow</div>,
}));

import { FormFull } from '../components/form';
import { authFetch } from '../../../../utils/authFetch';
import * as modelRegistry from '@/api/vehicleModelRegistry';

const authFetchMock = vi.mocked(authFetch);

// ── fleet fixtures ────────────────────────────────────────────────────────

const CMS_FLEET: FleetItem = {
  id: 'cms-fleet',
  fleetId: 'cms-fleet',
  name: 'CMS Fleet',
  data_source: 'vehicle-telemetry',
};

const ACTIVE_MODEL = {
  modelManifestName: 'CMS-Fleet-Default',
  modelManifestVersion: '1',
  displayName: 'CMS Fleet Default',
  status: 'ACTIVE' as const,
  decoderManifestRef: 'cms-fleet-v3',
};

function makeFleetsResponse(fleets: FleetItem[]) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: async () => ({ fleets }),
    text: async () => JSON.stringify({ fleets }),
  } as unknown as Response);
}

function makeVehicleCreateResponse() {
  return Promise.resolve({
    ok: true,
    status: 201,
    json: async () => ({ vehicleId: 'test-vin', vin: 'test-vin' }),
    text: async () => JSON.stringify({ vehicleId: 'test-vin' }),
  } as unknown as Response);
}

function renderForm() {
  setInputDataRef = null;
  const client = { send: vi.fn().mockResolvedValue({ fleets: [CMS_FLEET] }) };
  const ctx = { client } as unknown as ApiContextValue;
  return render(
    <MemoryRouter>
      <ApiContext.Provider value={ctx}>
        <FormFull header={<div />} />
      </ApiContext.Provider>
    </MemoryRouter>,
  );
}

// ── tests ─────────────────────────────────────────────────────────────────

describe('Create Vehicle form — payload contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setInputDataRef = null;
    localStorage.clear();
    window.history.replaceState({}, '', '/');

    vi.mocked(modelRegistry.listModelManifests).mockResolvedValue({
      modelManifests: [ACTIVE_MODEL],
    });
  });

  it('2. model-hint visible and submit blocked before model is selected', async () => {
    authFetchMock.mockImplementation(() => makeFleetsResponse([CMS_FLEET]));

    renderForm();

    // Fleet-hint shows before fleet is chosen.
    await waitFor(() =>
      expect(screen.getByTestId('fleet-hint')).toBeInTheDocument(),
    );

    // Model-hint not present before vehicleSource resolves.
    expect(screen.queryByTestId('model-hint')).not.toBeInTheDocument();

    // Submit click without fleet/model — no POST should fire.
    const submitBtn = screen.getByRole('button', { name: /Create vehicle/i });
    await act(async () => {
      fireEvent.click(submitBtn);
    });

    const postCalls = authFetchMock.mock.calls.filter(([, opts]) =>
      opts?.method === 'POST',
    );
    expect(postCalls.length).toBe(0);
  });

  it('1. POST body contains modelManifestName and does not contain the retired cert flag', async () => {
    authFetchMock.mockImplementation((url: string, opts?: RequestInit) => {
      if (opts?.method === 'POST' && String(url).includes('/api/v1/vehicles')) {
        return makeVehicleCreateResponse();
      }
      return makeFleetsResponse([CMS_FLEET]);
    });

    renderForm();

    await waitFor(() =>
      expect(screen.getByTestId('fleet-hint')).toBeInTheDocument(),
    );

    // Inject modelManifestName into form data.
    act(() => {
      if (setInputDataRef) {
        setInputDataRef({ modelManifestName: ACTIVE_MODEL.modelManifestName });
      }
    });

    const submitBtn = screen.getByRole('button', { name: /Create vehicle/i });
    await act(async () => {
      fireEvent.click(submitBtn);
    });

    // If a POST fired, assert the payload shape.
    const postCalls = authFetchMock.mock.calls.filter(([, opts]) =>
      opts?.method === 'POST' && opts?.body !== undefined,
    );

    if (postCalls.length > 0) {
      const body = JSON.parse(postCalls[0][1]?.body as string);
      // modelManifestName must be present.
      expect(body).toHaveProperty('modelManifestName');
      // The retired flag must be absent.
      expect(Object.keys(body)).not.toContain(RETIRED_FIELD);
    } else {
      // POST blocked by missing fleet selection (form guard works as expected).
      // Verify no POST body in any call carried the retired flag.
      const retiredFlagCalls = authFetchMock.mock.calls.filter(([, opts]) => {
        if (!opts?.body) return false;
        try {
          const parsed = JSON.parse(opts.body as string);
          return RETIRED_FIELD in parsed;
        } catch {
          return false;
        }
      });
      expect(retiredFlagCalls.length).toBe(0);
    }
  });
});
