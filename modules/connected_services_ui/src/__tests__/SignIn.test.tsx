// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * SignIn component tests.
 *
 * Covers UI-level acceptance criteria:
 *   - Email and password fields render
 *   - Submit button is disabled when either field is empty
 *   - Submit button is enabled when both fields have values
 *   - Error message renders in the Alert when signInWithPassword returns ok:false
 *   - Federate button is still present (data-testid preserved)
 *   - Simulated-data notice is still present
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SignIn from "../components/SignIn";

// ── Mock auth.ts to avoid real Cognito calls ──────────────────────────────────
// Hoisted for the same reason as in signInWithPassword.test.ts: a `vi.mock` factory is
// lifted above module-level `const`s, so plain declarations would be `undefined` inside it.
const { mockSignInWithPassword, mockRedirectToFederate } = vi.hoisted(() => ({
  mockSignInWithPassword: vi.fn(),
  mockRedirectToFederate: vi.fn(),
}));

vi.mock("../auth", async (importOriginal) => {
  const original = await importOriginal<typeof import("../auth")>();
  return {
    ...original,
    signInWithPassword: mockSignInWithPassword,
    redirectToFederate: mockRedirectToFederate,
  };
});

// ── Helper: get the underlying native button element from a Cloudscape Button ──
function getNativeButton(wrapper: HTMLElement): HTMLButtonElement {
  const btn = wrapper.querySelector("button");
  if (btn) return btn as HTMLButtonElement;
  // fallback in case the testid is directly on the native element
  return wrapper as unknown as HTMLButtonElement;
}

// ── Setup / teardown ──────────────────────────────────────────────────────────

beforeEach(() => {
  sessionStorage.clear();
  mockSignInWithPassword.mockReset();
  mockRedirectToFederate.mockReset();

  // Provide runtimeConfig so getRuntimeConfig() doesn't throw
  (window as unknown as { runtimeConfig: unknown }).runtimeConfig = {
    cognitoUserPoolId: "eu-west-1_TESTPOOL",
    cognitoClientId: "testclientid00000001",
    cognitoRegion: "us-east-1",
    cognitoDomain: "portal.example.invalid",
    connectedServicesApiEndpoint: "https://api.example.invalid",
  subscriptionsApiEndpoint: "https://subscriptions.example.invalid",
    callbackOrigin: "https://connected-services.example.invalid",
  };
});

afterEach(() => {
  sessionStorage.clear();
  delete (window as unknown as { runtimeConfig?: unknown }).runtimeConfig;
  vi.restoreAllMocks();
});

// ── Rendering ─────────────────────────────────────────────────────────────────

describe("SignIn — rendering", () => {
  it("renders the sign-in view", () => {
    render(<SignIn />);
    expect(screen.getByTestId("sign-in-view")).toBeTruthy();
  });

  it("renders an email input", () => {
    render(<SignIn />);
    expect(screen.getByTestId("email-input")).toBeTruthy();
  });

  it("renders a password input", () => {
    render(<SignIn />);
    expect(screen.getByTestId("password-input")).toBeTruthy();
  });

  it("renders the password sign-in submit button", () => {
    render(<SignIn />);
    expect(screen.getByTestId("password-sign-in-button")).toBeTruthy();
  });

  it("renders the Federate button (data-testid preserved)", () => {
    render(<SignIn />);
    expect(screen.getByTestId("federate-sign-in-button")).toBeTruthy();
  });

  it("renders the simulated-data notice", () => {
    render(<SignIn />);
    expect(screen.getByTestId("simulated-data-notice")).toBeTruthy();
  });

  it("email + password form appears ABOVE the Federate button", () => {
    render(<SignIn />);
    const form = screen.getByTestId("password-sign-in-form");
    const fedBtn = screen.getByTestId("federate-sign-in-button");
    // compareDocumentPosition DOCUMENT_POSITION_FOLLOWING = 4
    const rel = form.compareDocumentPosition(fedBtn);
    expect(rel & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("does not show an error alert on initial render", () => {
    render(<SignIn />);
    expect(screen.queryByTestId("sign-in-error")).toBeNull();
  });
});

// ── Empty-field guard (submit disabled when either field is empty) ────────────

describe("SignIn — empty fields disable submit", () => {
  it("submit button is disabled when both fields are empty", () => {
    render(<SignIn />);
    const btnWrapper = screen.getByTestId("password-sign-in-button");
    const nativeBtn = getNativeButton(btnWrapper);
    expect(nativeBtn.disabled).toBe(true);
  });

  it("submit button is disabled when only email is filled", async () => {
    const user = userEvent.setup();
    render(<SignIn />);
    const emailInput = screen
      .getByTestId("email-input")
      .querySelector("input") as HTMLInputElement;
    await user.type(emailInput, "user@example.com");
    const btnWrapper = screen.getByTestId("password-sign-in-button");
    const nativeBtn = getNativeButton(btnWrapper);
    expect(nativeBtn.disabled).toBe(true);
  });

  it("submit button is disabled when only password is filled", async () => {
    const user = userEvent.setup();
    render(<SignIn />);
    const passwordInput = screen
      .getByTestId("password-input")
      .querySelector("input") as HTMLInputElement;
    await user.type(passwordInput, "secret");
    const btnWrapper = screen.getByTestId("password-sign-in-button");
    const nativeBtn = getNativeButton(btnWrapper);
    expect(nativeBtn.disabled).toBe(true);
  });

  it("submit button is enabled when both email and password are filled", async () => {
    const user = userEvent.setup();
    render(<SignIn />);
    // Cloudscape Input wraps a native input — type into the underlying input element
    const emailInput = screen
      .getByTestId("email-input")
      .querySelector("input") as HTMLInputElement;
    const passwordInput = screen
      .getByTestId("password-input")
      .querySelector("input") as HTMLInputElement;
    await user.type(emailInput, "user@example.com");
    await user.type(passwordInput, "secret");
    const btnWrapper = screen.getByTestId("password-sign-in-button");
    const nativeBtn = getNativeButton(btnWrapper);
    expect(nativeBtn.disabled).toBe(false);
  });
});

// ── Error display ─────────────────────────────────────────────────────────────

describe("SignIn — error display", () => {
  it("shows an error alert when signInWithPassword returns ok:false", async () => {
    mockSignInWithPassword.mockResolvedValue({
      ok: false,
      message: "Incorrect email or password.",
    });

    const user = userEvent.setup();
    render(<SignIn />);

    const emailInput = screen
      .getByTestId("email-input")
      .querySelector("input") as HTMLInputElement;
    const passwordInput = screen
      .getByTestId("password-input")
      .querySelector("input") as HTMLInputElement;
    await user.type(emailInput, "user@example.com");
    await user.type(passwordInput, "wrongpassword");

    const btnWrapper = screen.getByTestId("password-sign-in-button");
    await user.click(btnWrapper);

    // Wait for async state update
    const errorAlert = await screen.findByTestId("sign-in-error");
    expect(errorAlert).toBeTruthy();
    expect(errorAlert.textContent).toContain("Incorrect email or password.");
  });

  it("clears the error message text between submission attempts", async () => {
    mockSignInWithPassword
      .mockResolvedValueOnce({ ok: false, message: "First error." })
      .mockResolvedValueOnce({ ok: false, message: "Second error." });

    const user = userEvent.setup();
    render(<SignIn />);

    const emailInput = screen
      .getByTestId("email-input")
      .querySelector("input") as HTMLInputElement;
    const passwordInput = screen
      .getByTestId("password-input")
      .querySelector("input") as HTMLInputElement;
    await user.type(emailInput, "user@example.com");
    await user.type(passwordInput, "pw");

    const btnWrapper = screen
      .getByTestId("password-sign-in-button")
      .closest("button") as HTMLButtonElement;
    await user.click(btnWrapper);
    await screen.findByTestId("sign-in-error");

    await user.click(btnWrapper);
    // After the second submission the error should show the second message
    const errorAlert = await screen.findByTestId("sign-in-error");
    expect(errorAlert.textContent).toContain("Second error.");
  });
});

// ── Single-submission guard ───────────────────────────────────────────────────

describe("SignIn — submits credentials exactly once per click", () => {
  it("issues one signInWithPassword call per click, not two", async () => {
    // Regression: Cloudscape Button defaults to formAction="submit", so inside the
    // <form> a single click fired both onClick AND the form's onSubmit — two
    // InitiateAuth calls to Cognito per click. `loading` state could not prevent it
    // because both invocations were dispatched in one React batch and read the
    // pre-update value. Fixed with formAction="none" plus a synchronous ref guard.
    mockSignInWithPassword.mockResolvedValue({ ok: false, message: "nope" });

    const user = userEvent.setup();
    render(<SignIn />);

    const emailInput = screen
      .getByTestId("email-input")
      .querySelector("input") as HTMLInputElement;
    const passwordInput = screen
      .getByTestId("password-input")
      .querySelector("input") as HTMLInputElement;
    await user.type(emailInput, "user@example.com");
    await user.type(passwordInput, "pw");

    await user.click(screen.getByTestId("password-sign-in-button"));
    await screen.findByTestId("sign-in-error");

    expect(mockSignInWithPassword).toHaveBeenCalledTimes(1);
  });

  it("Enter key in the password field submits exactly once", async () => {
    mockSignInWithPassword.mockResolvedValue({ ok: false, message: "nope" });

    const user = userEvent.setup();
    render(<SignIn />);

    const emailInput = screen
      .getByTestId("email-input")
      .querySelector("input") as HTMLInputElement;
    const passwordInput = screen
      .getByTestId("password-input")
      .querySelector("input") as HTMLInputElement;
    await user.type(emailInput, "user@example.com");
    await user.type(passwordInput, "pw{Enter}");

    await screen.findByTestId("sign-in-error");
    expect(mockSignInWithPassword).toHaveBeenCalledTimes(1);
  });
});

// ── Success path ──────────────────────────────────────────────────────────────

describe("SignIn — success path", () => {
  it("calls window.location.reload() on signInWithPassword ok:true", async () => {
    mockSignInWithPassword.mockResolvedValue({ ok: true });
    // jsdom's `location.reload` is non-configurable, so `vi.spyOn` throws
    // "Cannot redefine property: reload". Replace the whole `location` object instead,
    // which jsdom does permit, and restore it afterwards.
    const originalLocation = window.location;
    const reloadSpy = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: { ...originalLocation, reload: reloadSpy },
    });

    const user = userEvent.setup();
    render(<SignIn />);

    const emailInput = screen
      .getByTestId("email-input")
      .querySelector("input") as HTMLInputElement;
    const passwordInput = screen
      .getByTestId("password-input")
      .querySelector("input") as HTMLInputElement;
    await user.type(emailInput, "user@example.com");
    await user.type(passwordInput, "correct");

    const btnWrapper = screen.getByTestId("password-sign-in-button");
    await user.click(btnWrapper);

    await vi.waitFor(() => {
      expect(reloadSpy).toHaveBeenCalled();
    });
    reloadSpy.mockRestore();
  });
});
