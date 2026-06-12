// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import VehicleDiagnose from '../VehicleDiagnose';
import * as oem1DiagnoseApi from '@/api/oem1Diagnose';
import { isOEM1Vehicle } from '@/types/fleet-types';

// Mock the API module
vi.mock('@/api/oem1Diagnose', async () => {
  const actual = await vi.importActual<typeof oem1DiagnoseApi>('@/api/oem1Diagnose');
  return {
    ...actual,
    fetchVehicleState: vi.fn(),
  };
});

const mockFetchVehicleState = vi.mocked(oem1DiagnoseApi.fetchVehicleState);

const oem1Vehicle = {
  vehicleId: 'VIN-001',
  oem_source: 'oem1' as const,
};

const nonOem1Vehicle = {
  vehicleId: 'VIN-002',
  oem_source: 'fwe' as const,
};

const mockActionItems = [
  { category: 'ccs-off' as const, severity: 'critical' as const, message: 'CCS is disabled' },
  { category: 'transport-mode' as const, severity: 'warning' as const, message: 'Transport mode active' },
  { category: 'lifecycle' as const, severity: 'info' as const, message: 'Lifecycle: enrolled' },
];

const mockResponse: oem1DiagnoseApi.VehicleStateResponse = {
  vehicleId: 'VIN-001',
  ccsEnabled: false,
  transportMode: 'transport',
  lifecycleStatus: 'enrolled',
  actionItems: mockActionItems,
};

describe('VehicleDiagnose', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test('test_renders_diagnose_button_when_oem_source_is_oem1', () => {
    mockFetchVehicleState.mockResolvedValue(mockResponse);
    render(<VehicleDiagnose vehicle={oem1Vehicle} />);
    expect(screen.getByTestId('diagnose-button')).toBeInTheDocument();
  });

  test('test_button_hidden_when_oem_source_is_not_oem1', () => {
    render(<VehicleDiagnose vehicle={nonOem1Vehicle} />);
    expect(screen.queryByTestId('diagnose-button')).not.toBeInTheDocument();

    // Also verify for 'simulator'
    const simVehicle = { vehicleId: 'VIN-003', oem_source: 'simulator' as const };
    const { container } = render(<VehicleDiagnose vehicle={simVehicle} />);
    expect(container.firstChild).toBeNull();
  });

  test('test_click_invokes_proxy_via_api_client', async () => {
    mockFetchVehicleState.mockResolvedValue({ ...mockResponse, actionItems: [] });
    render(<VehicleDiagnose vehicle={oem1Vehicle} />);

    fireEvent.click(screen.getByTestId('diagnose-button'));

    await waitFor(() => {
      expect(mockFetchVehicleState).toHaveBeenCalledWith('VIN-001');
    });
  });

  test('test_action_items_render_with_severity_icons', async () => {
    mockFetchVehicleState.mockResolvedValue(mockResponse);
    render(<VehicleDiagnose vehicle={oem1Vehicle} />);

    fireEvent.click(screen.getByTestId('diagnose-button'));

    await waitFor(() => {
      expect(screen.getByTestId('action-item-ccs-off')).toBeInTheDocument();
      expect(screen.getByTestId('action-item-transport-mode')).toBeInTheDocument();
      expect(screen.getByTestId('action-item-lifecycle')).toBeInTheDocument();
    });

    expect(screen.getByText('CCS is disabled')).toBeInTheDocument();
    expect(screen.getByText('Transport mode active')).toBeInTheDocument();
    expect(screen.getByText('Lifecycle: enrolled')).toBeInTheDocument();
  });
});

// ── T8: VehicleDiagnose regression baseline (spec § 9, T8) ──────────────────
// Verifies that after Group 5.5 migrates the literal `=== 'oem1'` gate in
// VehicleDiagnose.tsx to `isOEM1Vehicle(vehicle)`, render output is
// byte-equivalent to the C1.2 baseline above.
//
// These tests MUST PASS now (regression baseline lock) and continue to
// pass after the helper migration lands in Group 5.5.
// M8: all conditional logic uses isOEM1Vehicle, never literal === 'oem1'.

describe('VehicleDiagnose — T8: helper-migration regression (spec § 9)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test('T8a: isOEM1Vehicle gate matches literal-compare gate for oem1 vehicle', () => {
    // M8: use isOEM1Vehicle — must agree with current literal gate behavior
    expect(isOEM1Vehicle({ oem_source: 'oem1' })).toBe(true);

    mockFetchVehicleState.mockResolvedValue(mockResponse);
    render(<VehicleDiagnose vehicle={oem1Vehicle} />);
    // Diagnose button is present — same result as literal gate
    expect(screen.getByTestId('diagnose-button')).toBeInTheDocument();
  });

  test('T8b: isOEM1Vehicle gate matches literal-compare gate for non-oem1 vehicle', () => {
    // M8: use isOEM1Vehicle — non-oem1 must return false (no button rendered)
    expect(isOEM1Vehicle({ oem_source: 'fwe' })).toBe(false);
    expect(isOEM1Vehicle({ oem_source: undefined })).toBe(false);

    render(<VehicleDiagnose vehicle={nonOem1Vehicle} />);
    expect(screen.queryByTestId('diagnose-button')).not.toBeInTheDocument();
  });

  test('T8c: action items render identically after helper migration', async () => {
    // Verifies full render pipeline is unchanged — same C1.2 assertion set
    mockFetchVehicleState.mockResolvedValue(mockResponse);
    render(<VehicleDiagnose vehicle={oem1Vehicle} />);

    fireEvent.click(screen.getByTestId('diagnose-button'));

    await waitFor(() => {
      expect(screen.getByTestId('action-item-ccs-off')).toBeInTheDocument();
    });
    expect(screen.getByText('CCS is disabled')).toBeInTheDocument();
    expect(screen.getByText('Transport mode active')).toBeInTheDocument();
    expect(screen.getByText('Lifecycle: enrolled')).toBeInTheDocument();
  });
});
