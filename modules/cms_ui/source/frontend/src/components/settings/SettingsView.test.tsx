// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import { render, screen } from "@testing-library/react";
import SettingsView from "./SettingsView";
import { UserContext } from "../commons/UserContext";
import { Mode } from "@cloudscape-design/global-styles";
import { BrowserRouter } from "react-router-dom";
import { AuthContext } from "react-oauth2-code-pkce";

// Mock the commons components
jest.mock("../commons", () => ({
  HelpPanelProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

// Mock the Breadcrumbs component
jest.mock("../commons/breadcrumbs", () => ({
  Breadcrumbs: () => <div data-testid="breadcrumbs-component">Breadcrumbs</div>,
}));

// Mock the UserContext
const mockUserContext = {
  fleet: {
    selectedFleet: null,
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
    currentThemeMode: Mode.Light,
    switchThemeMode: jest.fn(),
    applyInitialTheme: jest.fn(),
  },
  demoMode: {
    isDemoMode: false,
    setIsDemoMode: jest.fn(),
  },
  managedService: {
    isEnabled: false,
    setIsEnabled: jest.fn(),
  },
};

// Mock the AuthContext
const mockAuthContext = {
  tokenData: { username: "testuser" },
  idTokenData: { email: "test@example.com" },
  loginInProgress: false,
  error: undefined,
  logIn: jest.fn(),
  logOut: jest.fn(),
};

describe("SettingsView", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test("renders settings page with theme toggle", () => {
    render(
      <BrowserRouter>
        <AuthContext.Provider value={mockAuthContext}>
          <UserContext.Provider value={mockUserContext}>
            <SettingsView />
          </UserContext.Provider>
        </AuthContext.Provider>
      </BrowserRouter>
    );

    // Check if the breadcrumbs component is rendered
    expect(screen.getByTestId("breadcrumbs-component")).toBeInTheDocument();
    
    // Check if the appearance section is rendered
    expect(screen.getByText("Appearance")).toBeInTheDocument();
    
    // Check if the theme toggle is rendered
    expect(screen.getByText("Dark mode")).toBeInTheDocument();
    
    // Check if the notifications section is rendered
    expect(screen.getByText("Notifications")).toBeInTheDocument();
    
    // Check if the account section is rendered
    expect(screen.getByText("Account")).toBeInTheDocument();
  });

  test("renders settings page with dark mode enabled", () => {
    const darkModeContext = {
      ...mockUserContext,
      theme: {
        ...mockUserContext.theme,
        currentThemeMode: Mode.Dark,
      },
    };

    render(
      <BrowserRouter>
        <AuthContext.Provider value={mockAuthContext}>
          <UserContext.Provider value={darkModeContext}>
            <SettingsView />
          </UserContext.Provider>
        </AuthContext.Provider>
      </BrowserRouter>
    );

    // Check if the theme toggle is checked when dark mode is enabled
    const darkModeToggle = screen.getByLabelText("Dark mode");
    expect(darkModeToggle).toBeChecked();
  });

  test("renders new settings sections", () => {
    render(
      <BrowserRouter>
        <AuthContext.Provider value={mockAuthContext}>
          <UserContext.Provider value={mockUserContext}>
            <SettingsView />
          </UserContext.Provider>
        </AuthContext.Provider>
      </BrowserRouter>
    );

    // Check if the metrics section is rendered
    expect(screen.getByText("Metrics")).toBeInTheDocument();
    expect(screen.getByText("Enable Metrics")).toBeInTheDocument();
    
    // Check if the logging section is rendered
    expect(screen.getByText("Logging")).toBeInTheDocument();
    
    // Check if the encryption section is rendered
    expect(screen.getByText("Encryption")).toBeInTheDocument();
    
    // Check if the managed service section is rendered
    expect(screen.getByText("Managed Service")).toBeInTheDocument();
    expect(screen.getByText("Enable managed service")).toBeInTheDocument();
    
    // Check if the edit buttons are rendered
    const editButtons = screen.getAllByText("Edit");
    expect(editButtons.length).toBe(2); // One for Logging and one for Encryption
  });

  test("managed service toggle uses context value", () => {
    const enabledManagedServiceContext = {
      ...mockUserContext,
      managedService: {
        isEnabled: true,
        setIsEnabled: jest.fn(),
      },
    };

    render(
      <BrowserRouter>
        <AuthContext.Provider value={mockAuthContext}>
          <UserContext.Provider value={enabledManagedServiceContext}>
            <SettingsView />
          </UserContext.Provider>
        </AuthContext.Provider>
      </BrowserRouter>
    );

    // Check if the managed service toggle is checked when enabled
    const managedServiceToggle = screen.getByLabelText("Enable managed service");
    expect(managedServiceToggle).toBeChecked();
  });
});