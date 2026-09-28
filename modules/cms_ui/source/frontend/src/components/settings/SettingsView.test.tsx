// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import { render, screen } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import SettingsView from "./SettingsView";
import { UserContext } from "../commons/UserContext";
import { Mode } from "@cloudscape-design/global-styles";

// SettingsView reaches useAuth -> useSimpleAuth, which throws outside a
// SimpleAuthProvider. Mock the app's own auth abstraction (the same approach
// App.test.tsx uses) rather than provider internals, keeping this a render
// test. This replaces the pre-migration react-oauth2-code-pkce AuthContext
// wrapper the old test used.
vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({
    user: {
      username: "testuser",
      email: "test@example.com",
      name: "Test User",
      groups: ["platform-admin"],
      roles: ["platform-admin"],
      fleetIds: "",
    },
    isAuthenticated: true,
    isLoading: false,
    login: vi.fn(),
    loginWithFederate: vi.fn(),
    logout: vi.fn(),
    getAccessToken: () => "test-token",
    getIdToken: () => "test-token",
    getAuthHeaders: () => ({ Authorization: "Bearer test-token" }),
    isTokenValid: () => true,
  }),
}));

const makeUserContext = (themeMode: Mode = Mode.Light) => ({
  fleet: {
    selectedFleet: null,
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
    currentThemeMode: themeMode,
    switchThemeMode: vi.fn(),
    applyInitialTheme: vi.fn(),
  },
  demoMode: {
    isDemoMode: false,
    setIsDemoMode: vi.fn(),
  },
  managedService: {
    isEnabled: false,
    setIsEnabled: vi.fn(),
  },
});

const renderSettings = (ctx = makeUserContext()) =>
  render(
    <BrowserRouter>
      <UserContext.Provider value={ctx as any}>
        <SettingsView />
      </UserContext.Provider>
    </BrowserRouter>
  );

// Rewritten 2026-09-09. The pre-2026-03-07 Settings page (Notifications /
// Metrics / Logging / Encryption / Managed Service sections, an Edit-button
// pair, and a breadcrumbs component) was replaced wholesale by commit 9d813453
// ("rewrite Settings page"). The prior test asserted every one of those removed
// surfaces and had also never run under vitest (jest-era, blocked on collection).
// These assertions target the sections the current component actually renders:
// Appearance (Theme/Dark mode), Simulation (Cloud simulator), and Account (email).
describe("SettingsView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test("renders the Appearance, Simulation and Account sections", () => {
    renderSettings();

    expect(screen.getByText("Appearance")).toBeInTheDocument();
    expect(screen.getByText("Dark mode")).toBeInTheDocument();
    expect(screen.getByText("Simulation")).toBeInTheDocument();
    expect(screen.getByText("Cloud simulator")).toBeInTheDocument();
    expect(screen.getByText("Account")).toBeInTheDocument();
  });

  test("dark-mode toggle is checked when the theme context is dark", () => {
    renderSettings(makeUserContext(Mode.Dark));

    expect(screen.getByLabelText("Dark mode")).toBeChecked();
  });

  test("dark-mode toggle is unchecked when the theme context is light", () => {
    renderSettings(makeUserContext(Mode.Light));

    expect(screen.getByLabelText("Dark mode")).not.toBeChecked();
  });

  test("Account section shows the authenticated user's email", () => {
    renderSettings();

    expect(screen.getByText("test@example.com")).toBeInTheDocument();
  });
});
