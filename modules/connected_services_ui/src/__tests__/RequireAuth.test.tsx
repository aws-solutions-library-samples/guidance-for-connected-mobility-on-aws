// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * RequireAuth component tests — fail-closed authorization guard.
 *
 * Spec T9.1 (supersedes T3.1 for session injection):
 *   - No session (absent idToken)    → renders <SignIn />
 *   - Session without required group → renders <Unauthorized />
 *   - Session with required group    → renders children
 *
 * Migrated from v1: the test-harness session injection changed from
 * window.__SESSION__ (removed in T9.1) to sessionStorage 'idToken'.
 * The fail-closed invariant ("never empty") is unchanged.
 *
 * window.__SESSION__ is no longer read by getSession().  Tests now inject
 * sessions via sessionStorage by placing a valid JWT in 'idToken'.
 */

import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import RequireAuth from "../components/RequireAuth";
import { CONNECTED_SERVICES_GROUP, STORAGE_KEY_ID_TOKEN } from "../auth";

// ---------------------------------------------------------------------------
// JWT helper — same as auth.test.ts
// ---------------------------------------------------------------------------

function base64urlEncode(obj: object): string {
  const json = JSON.stringify(obj);
  const b64 = btoa(unescape(encodeURIComponent(json)));
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}

function makeIdToken(claims: Record<string, unknown>): string {
  const header = base64urlEncode({ alg: "RS256", typ: "JWT" });
  const payload = base64urlEncode(claims);
  return `${header}.${payload}.fake-signature`;
}

const FUTURE_EXP = Math.floor(Date.now() / 1000) + 3600;

// ---------------------------------------------------------------------------
// Session injection helpers (migrated from window.__SESSION__)
// ---------------------------------------------------------------------------

/** Inject an authorized session via sessionStorage. */
function setAuthorizedSession(): void {
  const token = makeIdToken({
    email: "jdoe@example.com",
    "cognito:groups": [CONNECTED_SERVICES_GROUP],
    exp: FUTURE_EXP,
  });
  sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, token);
}

/** Inject a session lacking the connected-services group. */
function setUnauthorizedSession(): void {
  const token = makeIdToken({
    email: "jdoe@example.com",
    "cognito:groups": ["fleet-operator"],
    exp: FUTURE_EXP,
  });
  sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, token);
}

/** Inject a session with an empty groups array. */
function setEmptyGroupsSession(): void {
  const token = makeIdToken({
    email: "jdoe@example.com",
    "cognito:groups": [],
    exp: FUTURE_EXP,
  });
  sessionStorage.setItem(STORAGE_KEY_ID_TOKEN, token);
}

/** Clear all session storage (no session). */
function clearSession(): void {
  sessionStorage.clear();
}

// RequireAuth renders SignIn which calls getRuntimeConfig() — provide a stub
// so the component mounts without throwing.
beforeEach(() => {
  (window as unknown as { runtimeConfig: unknown }).runtimeConfig = {
    cognitoUserPoolId: "<pool-id>",
    cognitoClientId: "testclientid00000001",
    cognitoDomain: "portal.example.invalid",
    connectedServicesApiEndpoint: "https://api.example.invalid",
  subscriptionsApiEndpoint: "https://subscriptions.example.invalid",
    callbackOrigin: "https://connected-services.example.invalid",
  };
});

afterEach(() => {
  clearSession();
  delete (window as unknown as { runtimeConfig?: unknown }).runtimeConfig;
});

describe("RequireAuth — fail-closed authorization", () => {
  it("renders children for an authorized session", () => {
    setAuthorizedSession();
    render(
      <MemoryRouter>
        <RequireAuth>
          <div data-testid="protected-content">Protected content</div>
        </RequireAuth>
      </MemoryRouter>
    );
    expect(screen.getByTestId("protected-content")).toBeTruthy();
    expect(screen.queryByTestId("sign-in-view")).toBeNull();
    expect(screen.queryByTestId("unauthorized-view")).toBeNull();
  });

  it("renders SignIn (not empty) when no session exists (absent idToken)", () => {
    clearSession();
    render(
      <MemoryRouter>
        <RequireAuth>
          <div data-testid="protected-content">Protected content</div>
        </RequireAuth>
      </MemoryRouter>
    );
    // Must render sign-in — never empty (T9.1 + T3.1 invariant)
    expect(screen.getByTestId("sign-in-view")).toBeTruthy();
    expect(screen.queryByTestId("protected-content")).toBeNull();
  });

  it("renders Unauthorized when session lacks the connected-services group", () => {
    setUnauthorizedSession();
    render(
      <MemoryRouter>
        <RequireAuth>
          <div data-testid="protected-content">Protected content</div>
        </RequireAuth>
      </MemoryRouter>
    );
    expect(screen.getByTestId("unauthorized-view")).toBeTruthy();
    expect(screen.queryByTestId("protected-content")).toBeNull();
  });

  it("renders Unauthorized for an empty groups array (fail-closed)", () => {
    setEmptyGroupsSession();
    render(
      <MemoryRouter>
        <RequireAuth>
          <div data-testid="protected-content">Protected content</div>
        </RequireAuth>
      </MemoryRouter>
    );
    expect(screen.getByTestId("unauthorized-view")).toBeTruthy();
    expect(screen.queryByTestId("protected-content")).toBeNull();
  });

  it("does NOT render an empty page for an unauthorized persona (defect guard)", () => {
    setEmptyGroupsSession();
    const { container } = render(
      <MemoryRouter>
        <RequireAuth>
          <div data-testid="protected-content">Protected content</div>
        </RequireAuth>
      </MemoryRouter>
    );
    // Empty render is a defect, not a pass (spec T3.1)
    expect(container.firstChild).not.toBeNull();
    expect(container.textContent?.trim().length).toBeGreaterThan(0);
  });

  it("does NOT render an empty page when no session exists (defect guard)", () => {
    clearSession();
    const { container } = render(
      <MemoryRouter>
        <RequireAuth>
          <div data-testid="protected-content">Protected content</div>
        </RequireAuth>
      </MemoryRouter>
    );
    expect(container.firstChild).not.toBeNull();
    expect(container.textContent?.trim().length).toBeGreaterThan(0);
  });

  it("renders the Federate sign-in button in the sign-in view", () => {
    clearSession();
    render(
      <MemoryRouter>
        <RequireAuth>
          <div data-testid="protected-content">Protected content</div>
        </RequireAuth>
      </MemoryRouter>
    );
    expect(screen.getByTestId("federate-sign-in-button")).toBeTruthy();
  });
});
