// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import { render, screen } from "@testing-library/react";
import { vi } from "vitest";

// maplibre-gl must be mocked BEFORE App is imported. App.tsx imports it
// transitively, and maplibre calls URL.createObjectURL at module scope to build
// its worker. Under vitest/jsdom that hits Node's dual-realm Blob check and
// throws "The 'obj' argument must be an instance of Blob. Received an instance
// of Blob", which aborts collection before any test runs. Mocked here rather
// than in setupTests.ts to keep the blast radius to this file — the same
// approach the vehicle-detail suites use for TripMap.
vi.mock("maplibre-gl", () => {
  class Stub {
    on() { return this; }
    off() { return this; }
    remove() { return this; }
    addTo() { return this; }
    setLngLat() { return this; }
    addControl() { return this; }
    setHTML() { return this; }
    fitBounds() { return this; }
    getCanvas() { return { style: {} }; }
  }
  return {
    default: { Map: Stub, Marker: Stub, Popup: Stub, NavigationControl: Stub, LngLatBounds: Stub },
    Map: Stub,
    Marker: Stub,
    Popup: Stub,
    NavigationControl: Stub,
    LngLatBounds: Stub,
  };
});

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => vi.fn() };
});

// App's tree reaches useAuth -> useSimpleAuth, which throws outside a
// SimpleAuthProvider. The original test wrapped in react-oauth2-code-pkce's
// AuthContext, which predates the SimpleAuthProvider migration and no longer
// satisfies it. Mock the app's own auth abstraction rather than the provider
// internals, so this stays a shell-render test.
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

// jsdom/nwsapi strict-mode codegen workaround (App.test.tsx cause 4).
//
// Rendering the full app shell makes jsdom apply the whole stylesheet cascade.
// jsdom's selector engine (nwsapi) compiles selector matchers via `new
// Function`, and when a selector token carries a legacy IE `\9`/`\8` CSS hack it
// inlines that token into a `"use strict"` body as `"#\9"` — where `\9` is an
// illegal string escape, throwing `SyntaxError: \8 and \9 are not allowed in
// strict mode`. React reports it via captureCommitPhaseError and the render
// aborts. It is a jsdom/nwsapi limitation, not an app defect — see
// issues/2026-09-09-app-test-never-ran-under-vitest/.
//
// Fix: intercept Function construction; on that specific strict-mode failure,
// escape the offending backslash-digit (`\9` -> `\\9`) in the compiled body and
// retry. Semantics are preserved (no real element has id `\9`), and the scope is
// this file only. Promote to setupTests.ts if another suite hits the same bug.
const __OrigFunction = globalThis.Function;
const __escapeIllegal = (args: any[]): any[] => {
  const out = args.slice();
  const last = out.length - 1;
  if (typeof out[last] === "string") {
    out[last] = out[last].replace(/\\([89])/g, "\\\\$1");
  }
  return out;
};
// @ts-expect-error reassigning the global Function with a Proxy
globalThis.Function = new Proxy(__OrigFunction, {
  construct(target, args, newTarget) {
    try {
      return Reflect.construct(target, args, newTarget);
    } catch (e: any) {
      if (String(e?.message).includes("strict mode")) {
        return Reflect.construct(target, __escapeIllegal(args as any[]), newTarget);
      }
      throw e;
    }
  },
  apply(target, thisArg, args) {
    try {
      return Reflect.apply(target, thisArg, args);
    } catch (e: any) {
      if (String(e?.message).includes("strict mode")) {
        return Reflect.apply(target, thisArg, __escapeIllegal(args as any[]));
      }
      throw e;
    }
  },
});

import App from "./App";
import { BrowserRouter } from "react-router-dom";
import { AuthContext } from "react-oauth2-code-pkce";

const mockAuthContext = {
  tokenData: { username: "testuser" },
  idTokenData: { email: "test@example.com" },
  loginInProgress: false,
  error: undefined,
  logIn: vi.fn(),
  logOut: vi.fn(),
};

const mockRuntimeConfig = {
  isDemoMode: "false",
};

describe("App", () => {
  test("renders settings gear icon in top navigation", () => {
    render(
      <BrowserRouter>
        <AuthContext.Provider value={mockAuthContext as any}>
          <App runtimeConfig={mockRuntimeConfig as any} />
        </AuthContext.Provider>
      </BrowserRouter>
    );

    // Cloudscape's responsive TopNavigation renders the "Settings" utility more
    // than once (visible + overflow/narrow-viewport variants), so assert at
    // least one gear is present rather than requiring a unique match.
    const settingsButtons = screen.getAllByLabelText("Settings");
    expect(settingsButtons.length).toBeGreaterThan(0);
  });
});
