// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * SubscriberLookupView smoke-test — redirects to the canonical suite.
 *
 * The v1 SubscriberLookupView (src/components/SubscriberLookupView.tsx) was
 * superseded by T4.3 and deleted.  The canonical tests for the new screen now
 * live at:
 *   src/components/screens/connectivity/__tests__/SubscriberLookupView.test.tsx
 *
 * This file keeps a minimal smoke-test in the root __tests__/ directory so any
 * test runner glob that resolves only this level still gets at least one assertion.
 * All substantive tests are in the canonical suite linked above.
 */

import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import SubscriberLookupView from "../components/screens/connectivity/SubscriberLookupView";

afterEach(cleanup);

describe("SubscriberLookupView — root-level smoke-test (see canonical suite in screens/connectivity/__tests__/)", () => {
  it("renders the settle marker in the search state", () => {
    render(
      <MemoryRouter initialEntries={["/connectivity/subscriber-lookup"]}>
        <Routes>
          <Route
            path="/connectivity/subscriber-lookup"
            element={<SubscriberLookupView />}
          />
        </Routes>
      </MemoryRouter>
    );

    const marker = document.querySelector(
      "[data-settle-marker='cs-settle-subscriber-lookup-search-field']"
    );
    expect(marker, "settle marker must be present").toBeTruthy();
  });

  it("renders the search field (no trip/GPS fields present)", () => {
    const { container } = render(
      <MemoryRouter initialEntries={["/connectivity/subscriber-lookup"]}>
        <Routes>
          <Route
            path="/connectivity/subscriber-lookup"
            element={<SubscriberLookupView />}
          />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByTestId("cs-subscriber-search-input")).toBeTruthy();

    const text = container.textContent?.toLowerCase() ?? "";
    expect(text).not.toMatch(/\bgps\b/);
    expect(text).not.toMatch(/\blatitude\b/);
    expect(text).not.toMatch(/\blongitude\b/);
    expect(text).not.toMatch(/\btrip\b/);
  });
});
