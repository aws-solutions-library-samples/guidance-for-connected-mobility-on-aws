// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import EnrolledOEM1FleetPage from '../EnrolledOEM1FleetPage';
import * as listEnrolledApi from '@/api/oem1ListEnrolled';
import * as addVehicleApi from '@/api/oem1AddVehicle';

vi.mock('@/api/oem1ListEnrolled', async () => {
  const actual = await vi.importActual<typeof listEnrolledApi>('@/api/oem1ListEnrolled');
  return { ...actual, oem1ListEnrolled: vi.fn() };
});

vi.mock('@/api/oem1AddVehicle', async () => {
  const actual = await vi.importActual<typeof addVehicleApi>('@/api/oem1AddVehicle');
  return { ...actual, addOEM1Vehicle: vi.fn() };
});

const mockListEnrolled = vi.mocked(listEnrolledApi.oem1ListEnrolled);
const mockAddVehicle = vi.mocked(addVehicleApi.addOEM1Vehicle);

// 5 OEM1-enrolled vehicles; 4 present in CMS, 1 missing
const MISSING_VIN = 'VIN00004';
const MOCK_RESPONSE: listEnrolledApi.OEM1ListEnrolledResponse = {
  enrolled_at_oem1: 5,
  enrolled_in_cms: 4,
  missing_in_cms: 1,
  vehicles: [
    { vin: 'VIN00000', in_cms: true },
    { vin: 'VIN00001', in_cms: true },
    { vin: 'VIN00002', in_cms: true },
    { vin: 'VIN00003', in_cms: true },
    { vin: MISSING_VIN, in_cms: false },
  ],
};

const MOCK_RESPONSE_AFTER_IMPORT: listEnrolledApi.OEM1ListEnrolledResponse = {
  enrolled_at_oem1: 5,
  enrolled_in_cms: 5,
  missing_in_cms: 0,
  vehicles: MOCK_RESPONSE.vehicles.map((v) => ({ ...v, in_cms: true })),
};

describe('EnrolledOEM1FleetPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test('renders load button initially', () => {
    render(<EnrolledOEM1FleetPage />);
    expect(screen.getByTestId('load-button')).toBeInTheDocument();
  });

  // ── F10: mocked OEM1 returns 5; CMS has 4; Import missing triggers add-vehicle ──
  test('F10: shows reconciliation summary and import action for missing VIN', async () => {
    mockListEnrolled.mockResolvedValue(MOCK_RESPONSE);
    render(<EnrolledOEM1FleetPage />);

    fireEvent.click(screen.getByTestId('load-button'));

    // Wait for reconciliation summary to appear
    await waitFor(() => {
      expect(screen.getByTestId('reconciliation-summary')).toBeInTheDocument();
    });

    const summary = screen.getByTestId('reconciliation-summary').textContent ?? '';
    expect(summary).toContain('5');        // enrolled_at_oem1
    expect(summary).toContain('4');        // enrolled_in_cms
    expect(summary).toContain('1');        // missing_in_cms
    expect(summary).toMatch(/Found 5 enrolled at OEM1; 4 in CMS — 1 missing rows/);

    // Import button visible for the missing VIN
    const importBtn = screen.getByTestId(`import-button-${MISSING_VIN}`);
    expect(importBtn).toBeInTheDocument();

    // Clicking Import missing calls Phase 2 add-vehicle (NOT bulk-enroll — NG11)
    mockAddVehicle.mockResolvedValue({
      vehicleId: MISSING_VIN,
      enrollmentStatus: 'COMPLETED',
      writeStatus: 'inserted',
    });
    mockListEnrolled.mockResolvedValueOnce(MOCK_RESPONSE_AFTER_IMPORT);

    fireEvent.click(importBtn);

    await waitFor(() => {
      expect(mockAddVehicle).toHaveBeenCalledWith(MISSING_VIN, '');
    });

    // Verify add-vehicle was called, NOT bulk-enroll
    expect(mockAddVehicle).toHaveBeenCalledTimes(1);
  });

  test('shows error message on API failure', async () => {
    mockListEnrolled.mockRejectedValue(
      new listEnrolledApi.OEM1ListEnrolledError('Rate limited', 429),
    );
    render(<EnrolledOEM1FleetPage />);

    fireEvent.click(screen.getByTestId('load-button'));

    await waitFor(() => {
      expect(screen.getByTestId('error-message')).toBeInTheDocument();
    });
    expect(screen.getByTestId('error-message').textContent).toContain('Rate limited');
  });

  test('shows empty state when no vehicles returned', async () => {
    mockListEnrolled.mockResolvedValue({
      enrolled_at_oem1: 0,
      enrolled_in_cms: 0,
      missing_in_cms: 0,
      vehicles: [],
    });
    render(<EnrolledOEM1FleetPage />);
    fireEvent.click(screen.getByTestId('load-button'));

    await waitFor(() => {
      expect(screen.getByTestId('reconciliation-summary')).toBeInTheDocument();
    });
    expect(screen.getByTestId('reconciliation-summary').textContent).toContain(
      'Found 0 enrolled at OEM1',
    );
  });
});
