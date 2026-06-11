// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * OEM1 status column removed 2026-06-10 (not scalable, unclear semantics).
 *
 * Remaining coverage:
 *   F7 bulk-action buttons — Unenroll / Refresh OEM1 status gated to OEM1 rows.
 *   F12 Source column regression — blue CMS / severity-medium OEM1 badges unchanged.
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import VehiclesTable from '../vehicles-table';
import { VehicleItem } from '@/types/fleet-types';

// ── Fixtures ──────────────────────────────────────────────────────────────────

const cmsVehicle: VehicleItem = {
  vehicleId: 'cms-001',
  vin: '1FDWF37S23EC10000',
  name: '1FDWF37S23EC10000',
  status: 'active',
};

const oem1Completed: VehicleItem = {
  vehicleId: 'oem1-completed',
  vin: '1FORD00000COMPLETE',
  name: '1FORD00000COMPLETE',
  status: 'active',
  oem_source: 'oem1',
  oem1_enrollment_status: 'COMPLETED',
  oem1_active_sku: 'SKU-00000069',
};

// ── Render helper ─────────────────────────────────────────────────────────────

function renderTable(vehicles: VehicleItem[], extraProps: Record<string, unknown> = {}) {
  return render(
    <MemoryRouter>
      <VehiclesTable
        vehicles={vehicles}
        totalVehicleCount={vehicles.length}
        selectedItems={[]}
        onSelectionChange={() => {}}
        onDelete={() => {}}
        isLoading={false}
        error={null}
        currentPage={1}
        pageSize={25}
        paginationInfo={{ total: vehicles.length, returned: vehicles.length, totalPages: 1 }}
        onPageChange={() => {}}
        onPageSizeChange={() => {}}
        onFleetFilterChange={() => {}}
        searchText=""
        onSearchChange={() => {}}
        {...extraProps}
      />
    </MemoryRouter>,
  );
}

// ── F7 — Bulk action buttons gated to OEM1 rows ───────────────────────────────

describe('F7 — OEM1 bulk action buttons gated to OEM1-source rows', () => {
  it('F7j: Unenroll button is disabled when no items selected', () => {
    renderTable([oem1Completed], { selectedItems: [] });
    expect(screen.getByTestId('bulk-unenroll-btn')).toBeDisabled();
  });

  it('F7k: Unenroll button is disabled when only CMS rows selected', () => {
    renderTable([cmsVehicle], { selectedItems: [cmsVehicle] });
    expect(screen.getByTestId('bulk-unenroll-btn')).toBeDisabled();
  });

  it('F7l: Unenroll button is enabled when OEM1 rows selected', () => {
    const onBulkUnenroll = vi.fn();
    renderTable([oem1Completed], {
      selectedItems: [oem1Completed],
      onBulkUnenroll,
    });
    expect(screen.getByTestId('bulk-unenroll-btn')).not.toBeDisabled();
  });
});

// ── F12 — Source column regression ────────────────────────────────────────────

describe('F12 — Source column unchanged (regression)', () => {
  it('F12a: Source column header still present', () => {
    renderTable([cmsVehicle, oem1Completed]);
    expect(screen.getByRole('columnheader', { name: /^source$/i })).toBeInTheDocument();
  });

  it('F12b: CMS row renders blue "On-board" badge', () => {
    renderTable([cmsVehicle]);
    const cmsBadge = screen.getByTestId(`source-badge-${cmsVehicle.vehicleId}`);
    expect(cmsBadge).toBeInTheDocument();
    expect(cmsBadge).toHaveTextContent('On-board');
  });

  it('F12c: OEM1 row renders severity-medium "Off-board" badge', () => {
    renderTable([oem1Completed]);
    const oem1Badge = screen.getByTestId(`source-badge-${oem1Completed.vehicleId}`);
    expect(oem1Badge).toBeInTheDocument();
    expect(oem1Badge).toHaveTextContent('Off-board');
  });

  it('F12d: OEM1 status column is absent from the table', () => {
    renderTable([oem1Completed]);
    expect(screen.queryByRole('columnheader', { name: /oem1 status/i })).not.toBeInTheDocument();
  });
});
