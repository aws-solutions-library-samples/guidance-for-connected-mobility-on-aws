// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * tableStates.test.tsx — tests for TableEmptyState and TableNoMatchState.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T3.1 AC3
 *
 * ## AC3 assertions
 *
 * 1. TableEmptyState and TableNoMatchState are TWO DISTINCT components with
 *    DIFFERENT headings.
 * 2. TableEmptyState has an optional per-call `description` override whose
 *    value MUST NOT equal the heading.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TableEmptyState, TableNoMatchState } from "../tableStates";

describe("TableEmptyState", () => {
  it("renders a heading containing the resourceName", () => {
    render(<TableEmptyState resourceName="vehicle" />);
    const heading = screen.getByRole("heading");
    expect(heading.textContent).toContain("No vehicle");
  });

  it("renders default body text that differs from the heading", () => {
    render(<TableEmptyState resourceName="vehicle" />);
    const heading = screen.getByRole("heading");
    const bodyEl = screen.getByText("No vehicle to display.");
    expect(bodyEl).toBeTruthy();
    // AC3: description must not equal the heading
    expect(bodyEl.textContent).not.toBe(heading.textContent?.trim());
  });

  it("renders the description override when supplied", () => {
    const desc = "No vehicles found in this market.";
    render(<TableEmptyState resourceName="vehicle" description={desc} />);
    expect(screen.getByText(desc)).toBeTruthy();
  });

  it("description override does not equal the heading (AC3 guard)", () => {
    // The heading is "No vehicle". Any description equal to it would make the
    // two strings indistinguishable, violating AC3.
    const desc = "No vehicles found in this market.";
    render(<TableEmptyState resourceName="vehicle" description={desc} />);
    const heading = screen.getByRole("heading");
    expect(desc).not.toBe(heading.textContent?.trim());
  });
});

describe("TableNoMatchState", () => {
  it("renders a 'No matches' heading — distinct from TableEmptyState", () => {
    render(<TableNoMatchState onClearFilter={() => undefined} />);
    const heading = screen.getByRole("heading");
    expect(heading.textContent).toContain("No matches");
  });

  it("renders a 'Clear filter' button", () => {
    render(<TableNoMatchState onClearFilter={() => undefined} />);
    expect(screen.getByRole("button", { name: /clear filter/i })).toBeTruthy();
  });

  it("calls onClearFilter when 'Clear filter' is clicked", () => {
    const fn = vi.fn();
    render(<TableNoMatchState onClearFilter={fn} />);
    const btn = screen.getByRole("button", { name: /clear filter/i });
    btn.click();
    expect(fn).toHaveBeenCalledOnce();
  });
});

describe("Two distinct components (AC3)", () => {
  it("TableEmptyState and TableNoMatchState have different headings", () => {
    const { unmount } = render(<TableEmptyState resourceName="vehicle" />);
    const emptyHeading = screen.getByRole("heading").textContent;
    unmount();

    render(<TableNoMatchState onClearFilter={() => undefined} />);
    const noMatchHeading = screen.getByRole("heading").textContent;

    expect(emptyHeading).not.toBe(noMatchHeading);
  });
});
