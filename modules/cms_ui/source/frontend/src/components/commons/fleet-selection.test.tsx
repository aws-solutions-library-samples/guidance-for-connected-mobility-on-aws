// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { FleetSelectionItem } from './fleet-selection';
import { UserContext } from './UserContext';
import { ApiContext } from '@/api/provider';
import { FleetItem } from '../../../api/fleet-management-models';

// Mock the API context
const mockApiClient = {
  send: jest.fn(),
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
    setSelectedFleet: jest.fn(),
    resetSelectedFleet: jest.fn(),
  },
  vehicle: {
    selectedVehicle: null,
    setSelectedVehicle: jest.fn(),
    resetSelectedVehicle: jest.fn(),
    fleetForSelectedVehicle: null,
    setFleetForSelectedVehicle: jest.fn(),
  },
  theme: {
    currentThemeMode: 'light',
    switchThemeMode: jest.fn(),
    applyInitialTheme: jest.fn(),
  },
  demoMode: {
    isDemoMode: true,
    setIsDemoMode: jest.fn(),
  },
  managedService: {
    isEnabled,
    setIsEnabled: jest.fn(),
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
    jest.clearAllMocks();
    // Mock the API response
    mockApiClient.send.mockResolvedValue({ fleets: mockFleets });
  });

  it('should fetch fleets on mount', async () => {
    const mockUserContext = createMockUserContext();

    render(
      <ApiContext.Provider value={mockApiContext}>
        <UserContext.Provider value={mockUserContext}>
          <FleetSelectionItem />
        </UserContext.Provider>
      </ApiContext.Provider>
    );

    // Check if loading state is shown
    expect(screen.getByText('Fetching fleets...')).toBeInTheDocument();

    // Wait for the fleets to be loaded
    await waitFor(() => {
      expect(mockApiClient.send).toHaveBeenCalled();
    });

    // Check if the first fleet is selected
    expect(mockUserContext.fleet.setSelectedFleet).toHaveBeenCalledWith(mockFleets[0]);
  });

  it('should show AWS IoT FleetWise indicator when managed service is enabled', async () => {
    const mockUserContext = createMockUserContext(true);

    render(
      <ApiContext.Provider value={mockApiContext}>
        <UserContext.Provider value={mockUserContext}>
          <FleetSelectionItem />
        </UserContext.Provider>
      </ApiContext.Provider>
    );

    // Wait for the fleets to be loaded
    await waitFor(() => {
      expect(mockApiClient.send).toHaveBeenCalled();
    });

    // Check if the AWS IoT FleetWise indicator is shown
    expect(screen.getByText('Using AWS IoT FleetWise')).toBeInTheDocument();
  });

  it('should not show AWS IoT FleetWise indicator when managed service is disabled', async () => {
    const mockUserContext = createMockUserContext(false);

    render(
      <ApiContext.Provider value={mockApiContext}>
        <UserContext.Provider value={mockUserContext}>
          <FleetSelectionItem />
        </UserContext.Provider>
      </ApiContext.Provider>
    );

    // Wait for the fleets to be loaded
    await waitFor(() => {
      expect(mockApiClient.send).toHaveBeenCalled();
    });

    // Check if the AWS IoT FleetWise indicator is not shown
    expect(screen.queryByText('Using AWS IoT FleetWise')).not.toBeInTheDocument();
  });

  it('should show error message when no fleets are returned', async () => {
    const mockUserContext = createMockUserContext();
    mockApiClient.send.mockResolvedValue({ fleets: [] });

    render(
      <ApiContext.Provider value={mockApiContext}>
        <UserContext.Provider value={mockUserContext}>
          <FleetSelectionItem />
        </UserContext.Provider>
      </ApiContext.Provider>
    );

    // Wait for the fleets to be loaded
    await waitFor(() => {
      expect(mockApiClient.send).toHaveBeenCalled();
    });

    // Check if the error message is shown
    expect(screen.getByText('No fleets available. Please create a fleet first.')).toBeInTheDocument();
  });

  it('should show error message when API call fails', async () => {
    const mockUserContext = createMockUserContext();
    mockApiClient.send.mockRejectedValue(new Error('API error'));

    render(
      <ApiContext.Provider value={mockApiContext}>
        <UserContext.Provider value={mockUserContext}>
          <FleetSelectionItem />
        </UserContext.Provider>
      </ApiContext.Provider>
    );

    // Wait for the API call to fail
    await waitFor(() => {
      expect(mockApiClient.send).toHaveBeenCalled();
    });

    // Check if the error message is shown
    expect(screen.getByText('API error')).toBeInTheDocument();
  });
});