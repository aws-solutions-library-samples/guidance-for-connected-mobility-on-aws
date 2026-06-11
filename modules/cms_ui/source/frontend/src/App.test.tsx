// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import { render, screen } from "@testing-library/react";
import App from "./App";
import { BrowserRouter } from "react-router-dom";
import { AuthContext } from "react-oauth2-code-pkce";
import { UserContextProvider } from "./components/commons/UserContext";

// Mock the AuthContext
const mockAuthContext = {
  tokenData: { username: "testuser" },
  idTokenData: { email: "test@example.com" },
  loginInProgress: false,
  error: undefined,
  logIn: jest.fn(),
  logOut: jest.fn(),
};

// Mock the runtime config
const mockRuntimeConfig = {
  isDemoMode: "false",
};

jest.mock("react-router-dom", () => ({
  ...jest.requireActual("react-router-dom"),
  useNavigate: () => jest.fn(),
}));

describe("App", () => {
  test("renders settings gear icon in top navigation", () => {
    render(
      <BrowserRouter>
        <AuthContext.Provider value={mockAuthContext}>
          <App runtimeConfig={mockRuntimeConfig} />
        </AuthContext.Provider>
      </BrowserRouter>
    );

    // Check if the settings button is rendered
    const settingsButton = screen.getByLabelText("Settings");
    expect(settingsButton).toBeInTheDocument();
  });
});