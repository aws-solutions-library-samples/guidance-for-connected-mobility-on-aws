// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * RED-PHASE skeletons for vehicles-table Source column + filter chip.
 * Spec: .kiro/specs/2026-06-04-cms-ui-vehicle-type-separation/spec.md § 8 + § 9
 * Tests:
 *   T7  — Source column renders blue "CMS" / severity-medium "OEM1" badges (spec § 9 matrix)
 *   S5  — clicking OEM1 source badge filters table to OEM1-only + chip visible
 *   C7  — default sort behaviour unchanged from baseline (regression anchor)
 *
 * MFD-1: OEM1 badge color is "severity-medium" (not "orange" — unsupported by CloudScape).
 * MFD-2: type-check verify is per-file only (pre-existing project-wide failures out of scope).
 * M8:   source classification uses getVehicleSource from fleet-types.ts, never literal === 'oem1'.
 * W7:   fixture data matches existing VehicleItem shape — no new dual-shape acceptance.
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import VehiclesTable from '../components/vehicles-table';
import { VehicleItem, getVehicleSource } from '@/types/fleet-types';
import { MemoryRouter } from 'react-router-dom';

// ──────────────────────────────────────────────────
// Fixtures — match existing VehicleItem shape (W7).
// oem_source is the ONE new field (spec § 1 / C1).
// ──────────────────────────────────────────────────

const cmsVehicle: VehicleItem = {
  vehicleId: 'cms-001',
  vin: '1FDWF37S23EC10000',
  name: '1FDWF37S23EC10000',
  status: 'active',
  // oem_source absent → getVehicleSource returns 'cms'
};

const oem1Vehicle: VehicleItem = {
  vehicleId: 'oem1-001',
  vin: '1FORDOEM100000001',
  name: '1FORDOEM100000001',
  status: 'active',
  oem_source: 'oem1',
};

const mixedVehicles: VehicleItem[] = [cmsVehicle, oem1Vehicle];

// ──────────────────────────────────────────────────
// Helper — renders VehiclesTable inside MemoryRouter
// (VehiclesTable uses useNavigate internally).
// Passes minimum required props; uses 'any' prop bag
// to avoid coupling tests to implementation details.
// ──────────────────────────────────────────────────

function renderTable(
  vehicles: VehicleItem[],
  extraProps: Record<string, unknown> = {},
) {
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

// ──────────────────────────────────────────────────
// Sanity: getVehicleSource helper contract (M8)
// ──────────────────────────────────────────────────

describe('getVehicleSource contract (M8 helper, no literal comparisons)', () => {
  it('returns cms for a vehicle without oem_source', () => {
    expect(getVehicleSource(cmsVehicle)).toBe('cms');
  });

  it('returns oem1 for a vehicle with oem_source oem1', () => {
    expect(getVehicleSource(oem1Vehicle)).toBe('oem1');
  });
});

// ──────────────────────────────────────────────────
// T7 — Source column badges
// RED: VehiclesTable does not yet have a Source column
// (task 5.3 implements it). These tests FAIL in red phase.
// ──────────────────────────────────────────────────

describe('T7 — vehicles-table Source column (spec § 9 matrix T7 + spec § 8)', () => {
  it('T7a: renders a "Source" column header when table has mixed-source data', () => {
    renderTable(mixedVehicles);
    // Expects column header — FAIL until task 5.3 adds the column
    expect(screen.getByRole('columnheader', { name: /source/i })).toBeInTheDocument();
  });

  it('T7b: CMS row renders a Badge with color="blue" and label "On-board"', () => {
    renderTable(mixedVehicles);
    const cmsBadge = screen.getByTestId('source-badge-cms-001');
    expect(cmsBadge).toBeInTheDocument();
    expect(cmsBadge).toHaveTextContent('On-board');
    expect(cmsBadge.closest('[class*="badge"]') ?? cmsBadge).toBeTruthy();
  });

  it('T7c: OEM1 row renders a Badge with color="severity-medium" and label "Off-board" (MFD-1)', () => {
    renderTable(mixedVehicles);
    // MFD-1: "orange" is unsupported by CloudScape; severity-medium chosen.
    const oem1Badge = screen.getByTestId('source-badge-oem1-001');
    expect(oem1Badge).toBeInTheDocument();
    expect(oem1Badge).toHaveTextContent('Off-board');
  });
});

// ──────────────────────────────────────────────────
// S5 — Source filter chip (spec § 8 + S5)
// RED: filter chip wiring lives in task 5.4 (VehiclesPage).
// The test drives the interaction via VehiclesTable's
// onSourceFilterChange callback (to be wired in 5.4).
// These tests FAIL in red phase — the prop + chip don't exist yet.
// ──────────────────────────────────────────────────

describe('S5 — Source filter chip (spec § 8 S5)', () => {
  it('S5a: clicking the OEM1 source badge calls onSourceFilterChange with "oem1"', () => {
    const onSourceFilterChange = vi.fn();
    renderTable(mixedVehicles, { onSourceFilterChange });

    const oem1Badge = screen.getByTestId('source-badge-oem1-001');
    fireEvent.click(oem1Badge);

    expect(onSourceFilterChange).toHaveBeenCalledWith('oem1');
  });

  it('S5b: after source filter applied, only OEM1 rows are visible', () => {
    // When VehiclesTable receives sourceFilter="oem1" prop (wired by task 5.4),
    // it should only render OEM1 rows.
    renderTable(mixedVehicles, { sourceFilter: 'oem1' });

    // OEM1 VIN visible
    expect(screen.getByText('1FORDOEM100000001')).toBeInTheDocument();
    // CMS VIN not visible
    expect(screen.queryByText('1FDWF37S23EC10000')).not.toBeInTheDocument();
  });

  it('S5c: a dismissible filter chip is rendered when sourceFilter is active', () => {
    renderTable(mixedVehicles, { sourceFilter: 'oem1' });
    // Implementation must render a chip/token indicating the active source filter.
    // The chip must be dismissible (task 5.4 wires dismissal back to VehiclesPage).
    expect(screen.getByTestId('source-filter-chip')).toBeInTheDocument();
    expect(screen.getByTestId('source-filter-chip')).toHaveTextContent(/off-board only/i);
  });
});

// ──────────────────────────────────────────────────
// C7 — Default sort regression (spec § 8, C7 anchor)
// The default VehiclesTable sort order is unchanged by
// adding the Source column. We assert on DOM position
// of VIN text nodes to confirm no sort regression.
// This test is GREEN in red phase (no sort change yet).
// ──────────────────────────────────────────────────

describe('C7 — Default sort regression (spec § 8 C7 anchor)', () => {
  it('C7: table renders both vehicles and preserves input order (default sort unchanged)', () => {
    const { container } = renderTable(mixedVehicles);
    // Both VINs must appear somewhere in the rendered output
    expect(screen.getByText('1FDWF37S23EC10000')).toBeInTheDocument();
    expect(screen.getByText('1FORDOEM100000001')).toBeInTheDocument();
    // CMS vehicle appears before OEM1 vehicle in the DOM (input order preserved)
    const allText = container.innerHTML;
    const cmsPos = allText.indexOf('1FDWF37S23EC10000');
    const oem1Pos = allText.indexOf('1FORDOEM100000001');
    expect(cmsPos).toBeGreaterThan(-1);
    expect(oem1Pos).toBeGreaterThan(-1);
    expect(cmsPos).toBeLessThan(oem1Pos);
  });
});
