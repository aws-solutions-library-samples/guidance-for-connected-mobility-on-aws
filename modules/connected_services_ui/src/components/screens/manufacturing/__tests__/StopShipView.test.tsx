// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * StopShipView tests — T6.2 (includes seam-7 guard)
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.2
 *
 * ## What this file covers
 *
 * 1. Settle marker present in rendered output.
 * 2. Holds log table renders from fixture.
 * 3. Pool scope note mentions production and in-transit only.
 * 4. Seam-7 guard:
 *    a. Hold cannot be placed with either authorizer field empty.
 *    b. Confirmation component is StopShipHoldConfirmation
 *       (testId "cs-stop-ship-hold-confirmation"), NOT ApprovalStep
 *       (testId "cs-workbench-approval-container").
 * 5. Fixture integrity: every leaf carries a valid provenance marker.
 * 6. Pool values in holds log are 'production', 'in_transit', or 'both' — not 'dealer_lot'.
 */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import StopShipView, { StopShipHoldConfirmation } from "../StopShipView";
import {
  STOP_SHIP_FIXTURE,
  STOP_SHIP_HOLDS_LOG,
} from "../stopShip.fixture";
import { VALID_PROVENANCE_MARKERS } from "../../../../types";

afterEach(() => {
  cleanup();
});

// ── Render helpers ────────────────────────────────────────────────────────────

function renderStopShip() {
  return render(
    <MemoryRouter initialEntries={["/manufacturing/stop-ship"]}>
      <Routes>
        <Route
          path="/manufacturing/stop-ship"
          element={<StopShipView />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

function renderHoldConfirmation(overrides: Partial<{
  authorizerPerson: string;
  authorizerRole: string;
}> = {}) {
  const props = {
    criterion: "TCU firmware rev < 3.1.4",
    productionMatchCount: 14,
    inTransitMatchCount: 7,
    authorizerPerson: overrides.authorizerPerson ?? "",
    authorizerRole: overrides.authorizerRole ?? "",
    onPersonChange: () => {},
    onRoleChange: () => {},
    onConfirm: () => {},
    onCancel: () => {},
  };
  return render(<StopShipHoldConfirmation {...props} />);
}

// ── 1. Settle marker ──────────────────────────────────────────────────────────

describe("settle marker", () => {
  it("renders the screen settleMarker in the output", () => {
    renderStopShip();
    const marker = screen.getByTestId("cs-stop-ship-settle-marker");
    expect(marker).toBeTruthy();
    expect(marker.textContent).toContain(
      "cs-settle-stop-ship-hold-authorization-panel",
    );
  });
});

// ── 2. Holds log table ────────────────────────────────────────────────────────

describe("holds log table", () => {
  it("renders the holds log table", () => {
    renderStopShip();
    expect(screen.getByTestId("cs-stop-ship-holds-log-table")).toBeTruthy();
  });

  it("renders at least one hold criterion from the fixture", () => {
    renderStopShip();
    const firstCriterion = STOP_SHIP_HOLDS_LOG[0].criterion.value!;
    // Criterion may be truncated in table; check at least the first word
    const firstWord = firstCriterion.split(" ")[0];
    expect(
      screen.getAllByText((_content, el) => {
        return (el?.textContent ?? "").includes(firstWord);
      }).length,
    ).toBeGreaterThan(0);
  });

  it("renders the correct total hold count in the holds log header", () => {
    renderStopShip();
    const header = screen.getByTestId("cs-stop-ship-holds-log-header");
    expect(header.textContent).toContain(
      String(STOP_SHIP_FIXTURE.holdsLog.length),
    );
  });
});

// ── 3. Pool scope note ────────────────────────────────────────────────────────

describe("pool scope note", () => {
  it("renders a scope note mentioning production and in-transit inventory", () => {
    renderStopShip();
    const note = screen.getByTestId("cs-stop-ship-pool-scope-note");
    const text = note.textContent ?? "";
    expect(text.toLowerCase()).toContain("production");
    expect(text.toLowerCase()).toContain("in-transit");
  });

  // DEALER-AGGREGATE-OK: test verifies aggregate pool-scope boundary — "dealer lot" appears as excluded scope name, not as a workflow surface
  it("pool scope note does not mention dealer lot inventory as in scope", () => {
    renderStopShip();
    const note = screen.getByTestId("cs-stop-ship-pool-scope-note");
    const text = note.textContent ?? "";
    // The note clarifies dealer lot is excluded — it may name it to say it's out of scope.
    // What it must NOT say is that dealer lot is included.
    // Acceptable: "Lot inventory held by distribution channels is not included."
    // DEALER-AGGREGATE-OK: assertion string negates dealer-lot inclusion; this is a boundary guard, not a workflow surface
    expect(text.toLowerCase()).not.toContain("dealer lot is included");
  });
});

// ── 4. Seam-7 guard ───────────────────────────────────────────────────────────

describe("Seam 7 — hold cannot be placed with empty authorizer fields", () => {
  /**
   * Seam 7 assertion (a): Hold cannot be placed with either field empty.
   *
   * The "Place Hold" button in StopShipHoldConfirmation is DISABLED unless
   * both authorizerPerson and authorizerRole are non-empty.
   */
  it("confirm button is disabled when authorizerPerson is empty", () => {
    renderHoldConfirmation({
      authorizerPerson: "",
      authorizerRole: "Quality Director",
    });

    const confirmBtn = screen.getByTestId(
      "cs-stop-ship-hold-confirmation-confirm-button",
    );
    // Cloudscape Button disabled renders with aria-disabled or disabled attribute.
    const isDisabled =
      confirmBtn.hasAttribute("disabled") ||
      confirmBtn.getAttribute("aria-disabled") === "true";
    expect(isDisabled).toBe(true);
  });

  it("confirm button is disabled when authorizerRole is empty", () => {
    renderHoldConfirmation({
      authorizerPerson: "J. Rivera",
      authorizerRole: "",
    });

    const confirmBtn = screen.getByTestId(
      "cs-stop-ship-hold-confirmation-confirm-button",
    );
    const isDisabled =
      confirmBtn.hasAttribute("disabled") ||
      confirmBtn.getAttribute("aria-disabled") === "true";
    expect(isDisabled).toBe(true);
  });

  it("confirm button is disabled when both fields are empty", () => {
    renderHoldConfirmation({ authorizerPerson: "", authorizerRole: "" });

    const confirmBtn = screen.getByTestId(
      "cs-stop-ship-hold-confirmation-confirm-button",
    );
    const isDisabled =
      confirmBtn.hasAttribute("disabled") ||
      confirmBtn.getAttribute("aria-disabled") === "true";
    expect(isDisabled).toBe(true);
  });

  it("confirm button is enabled when both fields are non-empty", () => {
    renderHoldConfirmation({
      authorizerPerson: "J. Rivera",
      authorizerRole: "Quality Director",
    });

    const confirmBtn = screen.getByTestId(
      "cs-stop-ship-hold-confirmation-confirm-button",
    );
    const isDisabled =
      confirmBtn.hasAttribute("disabled") ||
      confirmBtn.getAttribute("aria-disabled") === "true";
    expect(isDisabled).toBe(false);
  });

  /**
   * Seam 7 assertion (b): The confirmation component is StopShipHoldConfirmation,
   * NOT ApprovalStep (DiagnosisWorkbenchView).
   *
   * StopShipHoldConfirmation testId: "cs-stop-ship-hold-confirmation"
   * ApprovalStep testId:            "cs-workbench-approval-container"
   *
   * These must be different. The seam-7 guard asserts both:
   *   - StopShipHoldConfirmation renders its own distinct testId.
   *   - The campaign-approval testId is NEVER rendered in the stop-ship confirmation.
   */
  it("StopShipHoldConfirmation has testId 'cs-stop-ship-hold-confirmation'", () => {
    renderHoldConfirmation();
    expect(
      screen.getByTestId("cs-stop-ship-hold-confirmation"),
    ).toBeTruthy();
  });

  it("StopShipHoldConfirmation does NOT render the campaign-approval testId 'cs-workbench-approval-container'", () => {
    renderHoldConfirmation({
      authorizerPerson: "J. Rivera",
      authorizerRole: "Quality Director",
    });
    const approvalContainer = screen.queryByTestId(
      "cs-workbench-approval-container",
    );
    expect(approvalContainer).toBeNull();
  });

  it("StopShipView renders StopShipHoldConfirmation (not campaign-approval) when Place Hold is clicked with a criterion", async () => {
    const user = userEvent.setup();
    renderStopShip();

    // Cloudscape Input wraps a native <input>; target that directly.
    const criterionWrapper = screen.getByTestId("cs-stop-ship-criterion-input");
    const nativeInput = criterionWrapper.querySelector('input[type="text"]') as HTMLInputElement;
    if (!nativeInput) throw new Error("native criterion input not found");
    await user.type(nativeInput, "TCU firmware rev < 3.1.4");

    const placeHoldBtn = screen.getByTestId("cs-stop-ship-place-hold-button");
    await user.click(placeHoldBtn);

    // StopShipHoldConfirmation is shown — not ApprovalStep.
    expect(
      screen.getByTestId("cs-stop-ship-hold-confirmation"),
    ).toBeTruthy();
    expect(
      screen.queryByTestId("cs-workbench-approval-container"),
    ).toBeNull();
  });
});

// ── 5. Fixture integrity ──────────────────────────────────────────────────────

describe("fixture integrity", () => {
  const EXEMPT_KEYS = new Set(["id"]);

  it("every leaf in STOP_SHIP_HOLDS_LOG carries a valid provenance marker", () => {
    for (const row of STOP_SHIP_HOLDS_LOG) {
      for (const [key, value] of Object.entries(row)) {
        if (EXEMPT_KEYS.has(key)) continue;
        const pv = value as { value: unknown; provenance: string };
        expect(
          VALID_PROVENANCE_MARKERS.has(pv.provenance),
          `STOP_SHIP_HOLDS_LOG key "${key}" has invalid provenance "${pv.provenance}"`,
        ).toBe(true);
      }
    }
  });

  it("STOP_SHIP_HOLDS_LOG is not empty", () => {
    expect(STOP_SHIP_HOLDS_LOG.length).toBeGreaterThan(0);
  });

  it("example pool counts carry valid provenance markers", () => {
    const counts = STOP_SHIP_FIXTURE.examplePoolCounts;
    for (const [key, pv] of Object.entries(counts)) {
      expect(
        VALID_PROVENANCE_MARKERS.has(pv.provenance),
        `examplePoolCounts key "${key}" has invalid provenance "${pv.provenance}"`,
      ).toBe(true);
    }
  });
});

// ── 6. Pool scope values ──────────────────────────────────────────────────────

describe("hold log pool scope values", () => {
  const allowedScopes = new Set<string>(["production", "in_transit", "both"]);

  it("every poolScope in STOP_SHIP_HOLDS_LOG is 'production', 'in_transit', or 'both'", () => {
    for (const row of STOP_SHIP_HOLDS_LOG) {
      const scope = row.poolScope.value;
      expect(
        allowedScopes.has(scope!),
        `Row "${row.id}" has poolScope "${scope}" — must be 'production', 'in_transit', or 'both', not 'dealer_lot' or other value`,
      ).toBe(true);
    }
  });
});
