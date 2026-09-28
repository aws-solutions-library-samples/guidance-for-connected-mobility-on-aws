// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { FleetSelectionItem } from './fleet-selection';
import { UserContext } from './UserContext';
import { ApiContext } from '@/api/provider';
import { FleetItem } from '../../../api/fleet-management-models';

// Mock the API context
const mockApiClient = {
  send: vi.fn(),
};

const mockApiContext = {
  config: { baseUrl: 'http://localhost', isDemoMode: 'true' },
  token: '',
  client: mockApiClient,
};

// Mock the user context
const createMockUserContext = (isEnabled = false, selectedFleet = null) => ({
  fleet: {
    selectedFleet,
    setSelectedFleet: vi.fn(),
    resetSelectedFleet: vi.fn(),
  },
  vehicle: {
    selectedVehicle: null,
    setSelectedVehicle: vi.fn(),
    resetSelectedVehicle: vi.fn(),
    fleetForSelectedVehicle: null,
    setFleetForSelectedVehicle: vi.fn(),
  },
  theme: {
    currentThemeMode: 'light',
    switchThemeMode: vi.fn(),
    applyInitialTheme: vi.fn(),
  },
  demoMode: {
    isDemoMode: true,
    setIsDemoMode: vi.fn(),
  },
  managedService: {
    isEnabled,
    setIsEnabled: vi.fn(),
  },
});

// Mock fleet data
const mockFleets: FleetItem[] = [
  {
    id: 'test-fleet-1',
    name: 'Fleet 1',
  },
  {
    id: 'test-fleet-2',
    name: 'Fleet 2',
  },
];

describe('FleetSelectionItem', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Mock the API response
    mockApiClient.send.mockResolvedValue({ fleets: mockFleets });
  });

  it('should fetch fleets on mount', async () => {
    const mockUserContext = createMockUserContext();

    render(
      <BrowserRouter>
      <ApiContext.Provider value={mockApiContext}>
        <UserContext.Provider value={mockUserContext}>
          <FleetSelectionItem />
        </UserContext.Provider>
      </ApiContext.Provider>
      </BrowserRouter>
    );

    // Wait for the fleets to be loaded
    await waitFor(() => {
      expect(mockApiClient.send).toHaveBeenCalled();
    });

    // The component defaults the active fleet to the injected "All Fleets"
    // option (not the first API fleet) whenever nothing is selected yet.
    // (The former assertion for the "Fetching fleets..." loadingText was
    // dropped: Cloudscape Select renders loadingText only inside the open
    // dropdown popover, not as static text on a closed select.)
    expect(mockUserContext.fleet.setSelectedFleet).toHaveBeenCalledWith({ id: 'all', name: 'All Fleets' });
  });

  // Removed 2026-09-09: the "Using AWS IoT FleetWise" managed-service indicator
  // is no longer rendered by FleetSelectionItem (the component reads
  // uc.managedService.isEnabled only as a useEffect dependency, not in render).
  // The two prior tests asserted that string; the "enabled" one failed and the
  // "disabled" one passed vacuously — green because the text is gone entirely
  // rather than conditionally hidden. Both exercised a removed feature, so they
  // were deleted rather than rewritten to assert an absence.

  it('should fall back to All Fleets when no fleets are returned', async () => {
    const mockUserContext = createMockUserContext();
    mockApiClient.send.mockResolvedValue({ fleets: [] });

    render(
      <BrowserRouter>
      <ApiContext.Provider value={mockApiContext}>
        <UserContext.Provider value={mockUserContext}>
          <FleetSelectionItem />
        </UserContext.Provider>
      </ApiContext.Provider>
      </BrowserRouter>
    );

    // Wait for the fleets to be loaded
    await waitFor(() => {
      expect(mockApiClient.send).toHaveBeenCalled();
    });

    // Empty fleets is not an error state: the component injects an "All Fleets"
    // option and defaults the active fleet to it. (There is dead code for an
    // empty-state message gated on fleetSelections.length === 0, but that length
    // is never 0 because "All Fleets" is always prepended — noted in
    // issues/2026-09-09-fleet-selection-swallows-api-error/.)
    expect(mockUserContext.fleet.setSelectedFleet).toHaveBeenCalledWith({ id: 'all', name: 'All Fleets' });
  });

  it('should show error message when API call fails', async () => {
    const mockUserContext = createMockUserContext();
    mockApiClient.send.mockRejectedValue(new Error('API error'));

    render(
      <BrowserRouter>
      <ApiContext.Provider value={mockApiContext}>
        <UserContext.Provider value={mockUserContext}>
          <FleetSelectionItem />
        </UserContext.Provider>
      </ApiContext.Provider>
      </BrowserRouter>
    );

    // Wait for the API call to fail
    await waitFor(() => {
      expect(mockApiClient.send).toHaveBeenCalled();
    });

    // A fleet-list API failure must surface to the user. Fixed 2026-09-09:
    // FleetSelectionItem previously swallowed the error — the useEffect .then
    // overwrote the catch's "error" status with "finished", and the Select's
    // errorText renders only inside the open dropdown — so a failed load looked
    // like a normally-loaded picker. It now shows an inline error
    // StatusIndicator when the fetch fails. See
    // issues/2026-09-09-fleet-selection-swallows-api-error/.
    expect(screen.getByText('API error')).toBeInTheDocument();
  });
});