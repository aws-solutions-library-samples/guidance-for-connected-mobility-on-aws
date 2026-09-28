// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Sign-out reachability guard.
 *
 * WHY THIS EXISTS
 * ---------------
 * `signOut()` shipped in `auth.ts` at T9.1 with **zero callers**. It was fully
 * implemented, unit-tested, and completely unreachable: no UI called it, so a signed-in
 * user could not end their session, could not switch accounts, and could not get back to
 * the sign-in screen. The only workaround was clearing `sessionStorage` by hand in
 * devtools. Found by the user on 2026-09-07 while trying to verify the redesigned sign-in.
 *
 * The whole test suite was green throughout, because unit tests call the function
 * directly and never ask whether anything in the app reaches it. That is the same defect
 * class as the deployed-but-unreachable code paths found earlier in this spec: correctness
 * of a unit says nothing about its reachability.
 *
 * So this guard asserts REACHABILITY, not behaviour — behaviour is covered by auth.test.ts.
 * It renders the real AppShell and requires an actual clickable control that invokes
 * `signOut`.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock auth so we can observe the call without a real redirect.
const { mockSignOut, mockGetSession } = vi.hoisted(() => ({
  mockSignOut: vi.fn(),
  mockGetSession: vi.fn(),
}));

vi.mock("../auth", async (importOriginal) => {
  const original = await importOriginal<typeof import("../auth")>();
  return { ...original, signOut: mockSignOut, getSession: mockGetSession };
});

import AppShell from "../components/layout/AppShell";

beforeEach(() => {
  mockSignOut.mockReset();
  mockGetSession.mockReset();
  mockGetSession.mockReturnValue({
    alias: "tester@example.com",
    groups: ["connected-services"],
  });
  (window as unknown as { runtimeConfig: unknown }).runtimeConfig = {
    cognitoUserPoolId: "eu-west-1_TESTPOOL",
    cognitoClientId: "testclientid00000001",
    cognitoRegion: "us-west-2",
    cognitoDomain: "portal.example.invalid",
    connectedServicesApiEndpoint: "https://api.example.invalid",
  subscriptionsApiEndpoint: "https://subscriptions.example.invalid",
    callbackOrigin: "https://connected-services.example.invalid",
  };
});

afterEach(() => {
  delete (window as unknown as { runtimeConfig?: unknown }).runtimeConfig;
  vi.restoreAllMocks();
});

function renderShell() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <AppShell>
        <div>content</div>
      </AppShell>
    </MemoryRouter>
  );
}

describe("sign-out is reachable from the app shell", () => {
  it("renders an account menu trigger", () => {
    renderShell();
    // Cloudscape renders the utility text TWICE — once in the visible top-nav trigger and
    // once in the collapsed overflow menu — so getByText throws "found multiple elements".
    // Assert on the set instead of picking one arbitrarily.
    expect(screen.getAllByText("tester@example.com").length).toBeGreaterThan(0);
  });

  it("clicking Sign out calls signOut() — the function has a real caller", async () => {
    const user = userEvent.setup();
    renderShell();

    // First match is the visible trigger; the duplicate lives in the overflow menu.
    await user.click(screen.getAllByText("tester@example.com")[0]);
    // The dropdown item only exists once the menu is open.
    const item = (await screen.findAllByText("Sign out"))[0];
    await user.click(item);

    expect(mockSignOut).toHaveBeenCalledTimes(1);
  });

  it("passes the runtime config through, so the Hosted UI logout URL can be built", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getAllByText("tester@example.com")[0]);
    await user.click((await screen.findAllByText("Sign out"))[0]);

    // Without cognitoDomain + cognitoClientId, signOut() cannot reach the Hosted UI
    // logout endpoint, and Federate would silently re-authenticate on the next attempt —
    // the user would appear never to have signed out.
    expect(mockSignOut).toHaveBeenCalledWith(
      expect.objectContaining({
        cognitoClientId: "testclientid00000001",
        cognitoDomain: "portal.example.invalid",
      })
    );
  });

  it("falls back to a generic label when there is no session", () => {
    mockGetSession.mockReturnValue(null);
    renderShell();
    expect(screen.getAllByText("Account").length).toBeGreaterThan(0);
  });
});
