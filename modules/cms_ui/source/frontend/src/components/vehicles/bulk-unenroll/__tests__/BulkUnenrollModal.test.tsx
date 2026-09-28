// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for BulkUnenrollModal (spec § 5.3, test F6).
 *
 * F6 — typed-confirmation gate for batches ≥10 vehicles.
 *
 * Additional coverage:
 *  - Billing/timing copy per spec § 5.3 / C2:
 *      "subscription billing stops on submission" + "may take up to 7 days"
 *  - Heterogeneous-SKU → error + disabled Submit (decision 2026-06-05-005)
 *  - Hard-delete checkbox default OFF (C9)
 *  - clientRequestId stable across retries within modal session
 *
 * T3.3 coverage (2026-06-09-cms-fleet-manager-cognito-role):
 *  - Hidden for fleet-viewer (rendering guard)
 *  - Visible for fleet-operator with VIN multi-select scoped to user.fleetIds
 *  - Visible for platform-admin (unscoped)
 *  - Defense-in-depth: out-of-scope vehicles blocked, not silently dropped
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { BulkUnenrollModal } from '../BulkUnenrollModal';
import type { VehicleItem } from '@/types/fleet-types';
import * as oem1BulkUnenrollModule from '@/api/oem1BulkUnenroll';
import * as oem1EnrollQuotaModule from '@/api/oem1EnrollQuota';

// ── mocks ──────────────────────────────────────────────────────────────────

vi.mock('@/api/oem1BulkUnenroll', () => ({
  oem1BulkUnenroll: vi.fn(),
  BulkUnenrollError: class BulkUnenrollError extends Error {
    statusCode: number;
    constructor(message: string, statusCode: number) {
      super(message);
      this.name = 'BulkUnenrollError';
      this.statusCode = statusCode;
    }
  },
}));

vi.mock('@/api/oem1EnrollQuota', () => ({
  oem1EnrollQuota: vi.fn(),
}));

vi.mock('@/auth/useUserRole', () => ({
  useUserRole: vi.fn(),
}));

// ── helpers ────────────────────────────────────────────────────────────────

import { useUserRole } from '@/auth/useUserRole';
const mockUseUserRole = vi.mocked(useUserRole);

function setRole(overrides: Partial<ReturnType<typeof useUserRole>>) {
  mockUseUserRole.mockReturnValue({
    isAdmin: false,
    isOperator: false,
    isViewer: false,
    isConnectAgent: false,
    isEngineer: false,
    canWrite: false,
    fleetIds: [],
    ...overrides,
  });
}

// ── fixtures ───────────────────────────────────────────────────────────────

function makeVehicle(i: number, sku = 'SKU-00000069', fleetId = 'fleet-001'): VehicleItem {
  return {
    vehicleId: `VIN-${String(i).padStart(3, '0')}`,
    vin: `VIN-${String(i).padStart(3, '0')}`,
    oem_source: 'oem1',
    oem1_active_sku: sku,
    fleetId,
  };
}

const FLEET_NAME = 'demo-trucks-fleet';
const FLEET_ID = 'fleet-001';

function makeVehicles(n: number, sku = 'SKU-00000069', fleetId = 'fleet-001') {
  return Array.from({ length: n }, (_, i) => makeVehicle(i + 1, sku, fleetId));
}

function renderModal(
  vehicles: VehicleItem[],
  overrides: Partial<React.ComponentProps<typeof BulkUnenrollModal>> = {},
) {
  return render(
    <BulkUnenrollModal
      visible
      vehicles={vehicles}
      fleetId={FLEET_ID}
      fleetName={FLEET_NAME}
      onDismiss={vi.fn()}
      onSuccess={vi.fn()}
      {...overrides}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(oem1EnrollQuotaModule.oem1EnrollQuota).mockResolvedValue({
    remaining: 3,
    submissions_in_last_hour: 1,
    next_quota_reset_at: '2026-06-05T18:00:00Z',
  });
  // Default: platform-admin (preserves existing tests unchanged)
  setRole({ isAdmin: true, canWrite: true });
});

// ── T3.3 — role-gating (2026-06-09-cms-fleet-manager-cognito-role) ─────────

describe('T3.3 — rendering guard and fleet-scoped pre-filter', () => {
  it('hidden for fleet-viewer (renders nothing)', () => {
    setRole({ isViewer: true });
    const { container } = renderModal(makeVehicles(3));
    // Modal renders null — no modal portal content
    expect(container.firstChild).toBeNull();
  });

  it('visible for fleet-operator; VIN multi-select scoped to user.fleetIds', () => {
    setRole({ isOperator: true, canWrite: true, fleetIds: ['fleet-001'] });
    // vehicles: 2 in-scope (fleet-001) + 1 out-of-scope (fleet-999)
    const vehicles = [
      ...makeVehicles(2, 'SKU-00000069', 'fleet-001'),
      makeVehicle(99, 'SKU-00000069', 'fleet-999'),
    ];
    renderModal(vehicles);
    // Modal rendered — submit button present
    expect(screen.getByTestId('submit-unenroll')).toBeInTheDocument();
    // Out-of-scope vehicle triggers defense-in-depth error
    expect(screen.getByTestId('out-of-scope-error')).toBeInTheDocument();
    expect(screen.getByTestId('out-of-scope-error')).toHaveTextContent(
      "You can't un-enroll 1 vehicle outside your fleet scope",
    );
    // Submit blocked due to out-of-scope vehicle
    expect(screen.getByTestId('submit-unenroll')).toBeDisabled();
  });

  it('visible for fleet-operator with all VINs in scope; submit enabled', () => {
    setRole({ isOperator: true, canWrite: true, fleetIds: ['fleet-001'] });
    renderModal(makeVehicles(2, 'SKU-00000069', 'fleet-001'));
    expect(screen.getByTestId('submit-unenroll')).toBeInTheDocument();
    expect(screen.queryByTestId('out-of-scope-error')).not.toBeInTheDocument();
    expect(screen.getByTestId('submit-unenroll')).not.toBeDisabled();
  });

  it('visible for platform-admin with all vehicles (unscoped)', () => {
    setRole({ isAdmin: true, canWrite: true, fleetIds: [] });
    // vehicles from multiple fleets — admin sees all
    const vehicles = [
      ...makeVehicles(2, 'SKU-00000069', 'fleet-001'),
      makeVehicle(99, 'SKU-00000069', 'fleet-999'),
    ];
    renderModal(vehicles);
    expect(screen.getByTestId('submit-unenroll')).toBeInTheDocument();
    // No out-of-scope error for admin
    expect(screen.queryByTestId('out-of-scope-error')).not.toBeInTheDocument();
    expect(screen.getByTestId('submit-unenroll')).not.toBeDisabled();
  });

  it('fleet-operator with no out-of-scope: no error shown', () => {
    setRole({ isOperator: true, canWrite: true, fleetIds: ['fleet-001'] });
    renderModal(makeVehicles(3, 'SKU-00000069', 'fleet-001'));
    expect(screen.queryByTestId('out-of-scope-error')).not.toBeInTheDocument();
  });

  it('defense-in-depth: shows plural message for multiple out-of-scope vehicles', () => {
    setRole({ isOperator: true, canWrite: true, fleetIds: ['fleet-001'] });
    const vehicles = [
      makeVehicle(1, 'SKU-00000069', 'fleet-001'),
      makeVehicle(2, 'SKU-00000069', 'fleet-999'),
      makeVehicle(3, 'SKU-00000069', 'fleet-888'),
    ];
    renderModal(vehicles);
    expect(screen.getByTestId('out-of-scope-error')).toHaveTextContent(
      "You can't un-enroll 2 vehicles outside your fleet scope",
    );
  });
});

// ── F6: typed-confirmation gate ────────────────────────────────────────────

describe('F6 — typed-confirmation gate for ≥10 vehicles', () => {
  it('F6a: does NOT render confirmation input for <10 vehicles', () => {
    renderModal(makeVehicles(9));
    expect(screen.queryByTestId('confirmation-input')).not.toBeInTheDocument();
  });

  it('F6b: renders confirmation input for exactly 10 vehicles', () => {
    renderModal(makeVehicles(10));
    expect(screen.getByTestId('confirmation-input')).toBeInTheDocument();
  });

  it('F6c: Submit is disabled when confirmation text is empty (≥10 vehicles)', () => {
    renderModal(makeVehicles(10));
    const submit = screen.getByTestId('submit-unenroll');
    expect(submit).toBeDisabled();
  });

  it('F6d: Submit is disabled when confirmation text is wrong', () => {
    renderModal(makeVehicles(10));
    const input = screen.getByRole('textbox', { name: /type fleet name to confirm/i });
    fireEvent.change(input, { target: { value: 'wrong-fleet-name' } });
    expect(screen.getByTestId('submit-unenroll')).toBeDisabled();
  });

  it('F6e: Submit is enabled when confirmation text matches fleet name exactly', () => {
    renderModal(makeVehicles(10));
    const input = screen.getByRole('textbox', { name: /type fleet name to confirm/i });
    fireEvent.change(input, { target: { value: FLEET_NAME } });
    expect(screen.getByTestId('submit-unenroll')).not.toBeDisabled();
  });

  it('F6f: Submit is enabled without confirmation for <10 vehicles', () => {
    renderModal(makeVehicles(5));
    // No confirmation gate for small batches
    expect(screen.queryByTestId('confirmation-input')).not.toBeInTheDocument();
    expect(screen.getByTestId('submit-unenroll')).not.toBeDisabled();
  });
});

// ── Mandatory copy: C2 billing / timing ────────────────────────────────────

describe('C2 — billing + timing copy (spec § 5.3)', () => {
  it('shows "subscription billing stops on submission" copy', () => {
    renderModal(makeVehicles(3));
    expect(
      screen.getByText(/subscription billing stops on submission/i),
    ).toBeInTheDocument();
  });

  it('shows "may take up to 7 days" copy', () => {
    renderModal(makeVehicles(3));
    expect(screen.getByText(/may take up to 7 days/i)).toBeInTheDocument();
  });
});

// ── Heterogeneous SKU (decision 2026-06-05-005) ────────────────────────────

describe('heterogeneous SKU handling', () => {
  it('shows error alert when vehicles have different SKUs', () => {
    const mixed = [
      makeVehicle(1, 'SKU-00000069'),
      makeVehicle(2, 'SKU-00000104'),
    ];
    renderModal(mixed);
    expect(screen.getByTestId('heterogeneous-sku-error')).toBeInTheDocument();
  });

  it('disables Submit when vehicles have different SKUs', () => {
    const mixed = [
      makeVehicle(1, 'SKU-00000069'),
      makeVehicle(2, 'SKU-00000104'),
    ];
    renderModal(mixed);
    expect(screen.getByTestId('submit-unenroll')).toBeDisabled();
  });

  it('hides billing warning when SKUs are heterogeneous (error takes precedence)', () => {
    const mixed = [
      makeVehicle(1, 'SKU-A'),
      makeVehicle(2, 'SKU-B'),
    ];
    renderModal(mixed);
    // Billing warning not shown when in error state
    expect(screen.queryByTestId('billing-warning')).not.toBeInTheDocument();
  });
});

// ── Hard-delete default OFF (C9) ───────────────────────────────────────────

describe('C9 — hard-delete checkbox default', () => {
  it('hard-delete checkbox is unchecked by default', () => {
    renderModal(makeVehicles(3));
    const checkbox = screen.getByTestId('hard-delete-checkbox');
    const input = checkbox.querySelector('input[type="checkbox"]') ?? checkbox;
    expect(input).not.toBeChecked();
  });
});

// ── Submit flow ────────────────────────────────────────────────────────────

describe('submit flow', () => {
  it('calls oem1BulkUnenroll with correct vehicleIds, sku, fleetId', async () => {
    const onSuccess = vi.fn();
    vi.mocked(oem1BulkUnenrollModule.oem1BulkUnenroll).mockResolvedValue({
      requestId: 'req-123',
      acceptedCount: 3,
      preFlightFailureCount: 0,
      statusSummary: {},
    });

    renderModal(makeVehicles(3), { onSuccess });
    fireEvent.click(screen.getByTestId('submit-unenroll'));

    await waitFor(() => {
      expect(vi.mocked(oem1BulkUnenrollModule.oem1BulkUnenroll)).toHaveBeenCalledOnce();
    });

    const call = vi.mocked(oem1BulkUnenrollModule.oem1BulkUnenroll).mock.calls[0][0];
    expect(call.fleetId).toBe(FLEET_ID);
    expect(call.sku).toBe('SKU-00000069');
    expect(call.vehicleIds).toHaveLength(3);
    expect(call.hardDelete).toBe(false);
    expect(onSuccess).toHaveBeenCalledWith('req-123');
  });

  it('uses a stable clientRequestId across retries within same modal session', async () => {
    vi.mocked(oem1BulkUnenrollModule.oem1BulkUnenroll)
      .mockRejectedValueOnce(
        new oem1BulkUnenrollModule.BulkUnenrollError('server error', 500),
      )
      .mockResolvedValueOnce({
        requestId: 'req-456',
        acceptedCount: 1,
        preFlightFailureCount: 0,
        statusSummary: {},
      });

    renderModal(makeVehicles(1));

    // First attempt (fails)
    fireEvent.click(screen.getByTestId('submit-unenroll'));
    await waitFor(() =>
      expect(vi.mocked(oem1BulkUnenrollModule.oem1BulkUnenroll)).toHaveBeenCalledOnce(),
    );

    // Second attempt (succeeds)
    fireEvent.click(screen.getByTestId('submit-unenroll'));
    await waitFor(() =>
      expect(vi.mocked(oem1BulkUnenrollModule.oem1BulkUnenroll)).toHaveBeenCalledTimes(2),
    );

    const firstId =
      vi.mocked(oem1BulkUnenrollModule.oem1BulkUnenroll).mock.calls[0][0].clientRequestId;
    const secondId =
      vi.mocked(oem1BulkUnenrollModule.oem1BulkUnenroll).mock.calls[1][0].clientRequestId;

    // Same UUID reused across retries in the same modal session
    expect(firstId).toBe(secondId);
    // Must be a valid UUID v4 format
    expect(firstId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
  });

  it('shows error alert on submission failure', async () => {
    vi.mocked(oem1BulkUnenrollModule.oem1BulkUnenroll).mockRejectedValue(
      new oem1BulkUnenrollModule.BulkUnenrollError('quota exceeded', 429),
    );

    renderModal(makeVehicles(2));
    fireEvent.click(screen.getByTestId('submit-unenroll'));

    await waitFor(() =>
      expect(screen.getByTestId('submit-error')).toBeInTheDocument(),
    );
    expect(screen.getByTestId('submit-error')).toHaveTextContent('quota exceeded');
  });
});
