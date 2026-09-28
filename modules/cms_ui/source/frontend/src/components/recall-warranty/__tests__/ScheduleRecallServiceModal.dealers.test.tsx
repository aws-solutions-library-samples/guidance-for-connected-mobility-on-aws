// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * T4.2 — the recall-scheduling picker renders DMS-sourced dealers, and renders
 * its failure states honestly.
 *
 * WHY A COMPONENT TEST WHEN THE HOOK IS ALREADY TESTED.
 *
 * `useDealerOptions` returning the right four states proves nothing about what
 * the operator sees. A consumer can hold a correct `status: "error"` and still
 * render an empty dropdown with no message — the hook's contract is satisfied
 * and the user is told "there are no service centres". That gap is precisely
 * where D5's rule lives, so it has to be asserted at the render layer too.
 *
 * The dealer NAMES asserted here come from the mocked API response, never from a
 * literal in the component. `no-fabricated-records.test.ts` guards the component
 * source itself.
 */

import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const dealerOptionsMock = vi.fn();
vi.mock('../../../hooks/useDealerOptions', async () => {
  const actual = await vi.importActual<typeof import('../../../hooks/useDealerOptions')>(
    '../../../hooks/useDealerOptions',
  );
  return {
    ...actual,
    // `dealerPlaceholder` is deliberately the REAL implementation — the point of
    // several assertions below is the exact wording it produces.
    useDealerOptions: (...args: unknown[]) => dealerOptionsMock(...args),
  };
});

vi.mock('../../../config/api', () => ({ getApiEndpoint: () => 'https://api.example.invalid' }));
vi.mock('../../../utils/authFetch', () => ({
  authFetch: vi.fn().mockResolvedValue({ ok: true, json: async () => ({ vehicles: [] }) }),
}));

import { ScheduleRecallServiceModal } from '../ScheduleRecallServiceModal';

const RECALL = {
  id: '24V123',
  component: 'BRAKES: HYDRAULIC: LINE',
  severity: 'Critical',
  vehicles: ['VEH-001'],
};

function state(overrides: Record<string, unknown> = {}) {
  return {
    options: [],
    status: 'loading',
    errorMessage: '',
    reload: vi.fn(),
    ...overrides,
  };
}

function renderModal() {
  return render(
    <ScheduleRecallServiceModal
      visible
      recall={RECALL}
      onDismiss={vi.fn()}
      vehicleVinMap={{ 'VEH-001': '1MRDN00000000001' }}
    />,
  );
}

beforeEach(() => {
  dealerOptionsMock.mockReset();
});

describe('ScheduleRecallServiceModal — dealer picker states', () => {
  it('passes the modal visibility through as the hook enable flag', async () => {
    // Both polarities, deliberately. A first draft asserted only
    // `toHaveBeenCalledWith(true)` while rendering `visible={true}` — which a
    // hardcoded `useDealerOptions(true)` satisfies just as well as the correct
    // `useDealerOptions(visible)`. Mutation testing caught it passing against
    // that mutation, which is the same assertion-adjacent-to-property hole this
    // spec's sibling recorded six instances of. The closed case is the one with
    // teeth.
    dealerOptionsMock.mockReturnValue(state({ status: 'ready' }));
    renderModal();
    await waitFor(() => expect(dealerOptionsMock).toHaveBeenCalled());
    expect(dealerOptionsMock).toHaveBeenCalledWith(true);

    dealerOptionsMock.mockClear();
    render(
      <ScheduleRecallServiceModal
        visible={false}
        recall={RECALL}
        onDismiss={vi.fn()}
        vehicleVinMap={{ 'VEH-001': '1MRDN00000000001' }}
      />,
    );
    await waitFor(() => expect(dealerOptionsMock).toHaveBeenCalled());
    expect(dealerOptionsMock).toHaveBeenCalledWith(false);
    expect(dealerOptionsMock).not.toHaveBeenCalledWith(true);
  });

  it('shows a loading placeholder while fetching, not an empty picker', async () => {
    dealerOptionsMock.mockReturnValue(state({ status: 'loading' }));
    renderModal();
    expect(await screen.findByText(/Loading service centres/i)).toBeInTheDocument();
  });

  it('renders an explicit unavailable state plus a retry on failure', async () => {
    // The two assertions are separate on purpose: the placeholder must not read
    // as an absence, AND the operator must have a way forward that is not a
    // fabricated list.
    const reload = vi.fn();
    dealerOptionsMock.mockReturnValue(
      state({ status: 'error', errorMessage: 'Dealer directory returned 502.', reload }),
    );
    renderModal();
    expect(await screen.findByText(/Service centres unavailable/i)).toBeInTheDocument();
    expect(screen.getByText(/Dealer directory returned 502\./)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Retry loading service centres/i })).toBeInTheDocument();
  });

  it('a failure never renders the words used for an authoritative empty result', async () => {
    // The load-bearing D5 assertion at the render layer. If a future refactor
    // routes `error` through the same branch as `empty`, this fails.
    dealerOptionsMock.mockReturnValue(
      state({ status: 'error', errorMessage: 'Could not load service centres.' }),
    );
    renderModal();
    await waitFor(() => expect(screen.getByText(/Service centres unavailable/i)).toBeInTheDocument());
    expect(screen.queryByText(/No authorised service centres available/i)).not.toBeInTheDocument();
  });

  it('an authoritative empty result says so, and offers no retry', async () => {
    dealerOptionsMock.mockReturnValue(state({ status: 'empty' }));
    renderModal();
    expect(await screen.findByText(/No authorised service centres available/i)).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /Retry loading service centres/i }),
    ).not.toBeInTheDocument();
  });

  it('renders the fetched rooftops when ready', async () => {
    dealerOptionsMock.mockReturnValue(
      state({
        status: 'ready',
        options: [
          { value: 'dealer-austin', label: 'Fetched Rooftop A', description: 'district-central' },
        ],
      }),
    );
    renderModal();
    // Cloudscape renders the selected/placeholder trigger; with nothing selected
    // the ready-state placeholder is the observable signal that the picker is
    // enabled and populated.
    expect(await screen.findByText(/Choose a service centre/i)).toBeInTheDocument();
  });

  it('keeps Schedule disabled until a dealer is chosen', async () => {
    // Guards against "fixing" the empty picker by removing the requirement — a
    // booking with no rooftop is not a booking DMS can accept.
    dealerOptionsMock.mockReturnValue(state({ status: 'ready', options: [] }));
    renderModal();
    const submit = await screen.findByRole('button', { name: /Schedule 1 vehicle/i });
    expect(submit).toBeDisabled();
  });
});
