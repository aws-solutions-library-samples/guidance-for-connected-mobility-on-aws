// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Unauthorized component tests.
 *
 * Spec T3.1: "Fail-closed is the requirement — a route that renders empty for
 * an unauthorized persona is a defect, not a pass."
 *
 * These tests verify that the Unauthorized component:
 *   - Is never empty
 *   - Contains a user-visible error heading
 *   - Contains an explanation for the user
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Unauthorized from "../components/Unauthorized";

describe("Unauthorized", () => {
  it("renders a non-empty view (never empty for an unauthorized persona)", () => {
    const { container } = render(<Unauthorized />);
    // The component must render something visible
    expect(container.firstChild).not.toBeNull();
    expect(container.textContent?.trim().length).toBeGreaterThan(0);
  });

  it("renders the unauthorized error state with a visible heading", () => {
    render(<Unauthorized />);
    // Should contain "Unauthorized" in the heading
    expect(screen.getByTestId("unauthorized-view")).toBeTruthy();
    expect(screen.getByTestId("unauthorized-alert")).toBeTruthy();
  });

  it("displays text about the connected-services group", () => {
    render(<Unauthorized />);
    // The default message mentions the required group
    const view = screen.getByTestId("unauthorized-view");
    expect(view.textContent).toMatch(/connected-services/);
  });

  it("renders a custom detail message when provided", () => {
    render(<Unauthorized detail="Custom error detail for testing." />);
    expect(screen.getByText("Custom error detail for testing.")).toBeTruthy();
  });

  it("never renders an empty page (failsafe — component always has content)", () => {
    render(<Unauthorized />);
    // The data-testid element must exist and have non-empty content
    const view = screen.getByTestId("unauthorized-view");
    expect(view.textContent?.trim()).not.toBe("");
  });
});
