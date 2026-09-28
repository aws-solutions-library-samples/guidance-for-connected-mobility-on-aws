// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * FactoryRegistrationView tests — T6.1
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.1
 *
 * ## What this file covers
 *
 * 1. Settle marker present in rendered output.
 * 2. Registration form renders with VIN input.
 * 3. VIN shorter than 17 characters produces a validation error.
 * 4. Valid 17-char VIN triggers credential generation (ICCID/IMSI/Profile shown).
 * 5. Confirming registration prepends a row to the log table.
 * 6. Cancelling registration clears the form without adding a row.
 * 7. Recent registrations table renders fixture rows.
 * 8. TableNoMatchState shown when text filter matches nothing.
 * 9. Fixture integrity: every leaf carries a valid provenance marker.
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import FactoryRegistrationView from "../FactoryRegistrationView";
import {
  FACTORY_REGISTRATION_FIXTURE,
  REGISTRATION_LOG_ROWS,
} from "../factoryRegistration.fixture";
import { VALID_PROVENANCE_MARKERS } from "../../../../types";

afterEach(() => {
  cleanup();
});

// ── Render helper ─────────────────────────────────────────────────────────────

function renderFactoryRegistration() {
  return render(
    <MemoryRouter initialEntries={["/manufacturing/factory-registration"]}>
      <Routes>
        <Route
          path="/manufacturing/factory-registration"
          element={<FactoryRegistrationView />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

// A synthetic 17-char VIN that is not in the fixture (so it is clearly new).
const NEW_VIN = "TEST00000000VIN17";

// ── 1. Settle marker ─────────────────────────────────────────────────────────

describe("settle marker", () => {
  it("renders the screen settleMarker in the output", () => {
    renderFactoryRegistration();
    const marker = screen.getByTestId("factory-registration-settle-marker");
    expect(marker).toBeTruthy();
    expect(marker.textContent).toContain(
      "cs-settle-factory-registration-vin-batch-table",
    );
  });
});

// ── 2. Form renders ──────────────────────────────────────────────────────────

describe("registration form renders", () => {
  it("renders the VIN input field", () => {
    renderFactoryRegistration();
    expect(screen.getByTestId("vin-input")).toBeTruthy();
  });

  it("renders the generate-credentials button", () => {
    renderFactoryRegistration();
    expect(screen.getByTestId("generate-credentials-btn")).toBeTruthy();
  });

  it("generate button is disabled when VIN input is empty", () => {
    renderFactoryRegistration();
    const btn = screen.getByTestId("generate-credentials-btn") as HTMLButtonElement;
    // Cloudscape Button renders as <button> in the DOM.
    expect(btn.disabled || btn.getAttribute("disabled") !== null).toBe(true);
  });
});

// ── Helper: find the native <input> inside a Cloudscape Input testid wrapper ──

/**
 * Cloudscape Input renders the data-testid on a wrapper <div>, but typing must
 * target the native <input type="text"> inside it.
 */
function getVinNativeInput(): HTMLInputElement {
  const wrapper = screen.getByTestId("vin-input");
  const input = wrapper.querySelector('input[type="text"]') as HTMLInputElement | null;
  if (!input) throw new Error("native input not found inside vin-input wrapper");
  return input;
}

// ── 3. Validation — short VIN ────────────────────────────────────────────────

describe("VIN validation", () => {
  it("shows an error when VIN is shorter than 17 characters", async () => {
    const user = userEvent.setup();
    renderFactoryRegistration();

    const input = getVinNativeInput();
    // Type a 16-char VIN (one short)
    await user.type(input, "SHORTVIN0000000");
    const btn = screen.getByTestId("generate-credentials-btn");
    await user.click(btn);

    expect(screen.getByText("VIN must be 17 characters.")).toBeTruthy();
  });
});

// ── 4. Credential generation ─────────────────────────────────────────────────

describe("credential generation", () => {
  it("shows ICCID, IMSI, Profile after a valid VIN is entered", async () => {
    const user = userEvent.setup();
    renderFactoryRegistration();

    const input = getVinNativeInput();
    await user.type(input, NEW_VIN);
    await user.click(screen.getByTestId("generate-credentials-btn"));

    // The confirm and cancel buttons should appear — these are unique.
    expect(screen.getByTestId("confirm-registration-btn")).toBeTruthy();
    expect(screen.getByTestId("cancel-registration-btn")).toBeTruthy();

    // The generated ICCID/IMSI/Profile labels appear (possibly alongside column
    // headers in the table — use getAllByText which succeeds on ≥1 match).
    expect(screen.getAllByText("ICCID").length).toBeGreaterThan(0);
    expect(screen.getAllByText("IMSI").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Profile").length).toBeGreaterThan(0);
  });
});

// ── 5. Confirm registration prepends a row ────────────────────────────────────

describe("confirm registration", () => {
  it("prepends the new VIN row to the registrations table on confirm", async () => {
    const user = userEvent.setup();
    renderFactoryRegistration();

    const rowsBefore = REGISTRATION_LOG_ROWS.length;

    // Enter VIN and generate credentials.
    const input = getVinNativeInput();
    await user.type(input, NEW_VIN);
    await user.click(screen.getByTestId("generate-credentials-btn"));

    // Confirm.
    await user.click(screen.getByTestId("confirm-registration-btn"));

    // The header counter should reflect one more row.
    const header = screen.getByTestId("factory-registration-table-header");
    expect(header.textContent).toContain(`(${rowsBefore + 1})`);

    // The new VIN should appear in the table.
    expect(screen.getByText(NEW_VIN)).toBeTruthy();
  });
});

// ── 6. Cancel clears form ────────────────────────────────────────────────────

describe("cancel registration", () => {
  it("cancelling clears the form without changing row count", async () => {
    const user = userEvent.setup();
    renderFactoryRegistration();

    const rowsBefore = REGISTRATION_LOG_ROWS.length;

    // Enter VIN and generate, then cancel.
    const input = getVinNativeInput();
    await user.type(input, NEW_VIN);
    await user.click(screen.getByTestId("generate-credentials-btn"));
    await user.click(screen.getByTestId("cancel-registration-btn"));

    // Row count unchanged.
    const header = screen.getByTestId("factory-registration-table-header");
    expect(header.textContent).toContain(`(${rowsBefore})`);

    // Generate button is visible again (form cleared).
    expect(screen.getByTestId("generate-credentials-btn")).toBeTruthy();
  });
});

// ── 7. Table renders fixture rows ─────────────────────────────────────────────

describe("recent registrations table", () => {
  it("renders the factory registration table", () => {
    renderFactoryRegistration();
    expect(screen.getByTestId("factory-registration-table")).toBeTruthy();
  });

  it("renders at least one VIN from the fixture in the table", () => {
    renderFactoryRegistration();
    const firstVin = REGISTRATION_LOG_ROWS[0].vin.value!;
    expect(screen.getByText(firstVin)).toBeTruthy();
  });
});

// ── 8. TableNoMatchState ──────────────────────────────────────────────────────

describe("TableNoMatchState", () => {
  it("renders 'No matches' when text filter matches nothing", async () => {
    const user = userEvent.setup();
    renderFactoryRegistration();

    const table = screen.getByTestId("factory-registration-table");
    const filterInput = within(table).getByRole("searchbox");
    await user.type(filterInput, "zzz_no_match_xyzzy");

    expect(screen.getByText("No matches")).toBeTruthy();
  });
});

// ── 9. Fixture integrity ──────────────────────────────────────────────────────

describe("fixture integrity", () => {
  const EXEMPT_KEYS = new Set(["id"]);

  it("every leaf in REGISTRATION_LOG_ROWS carries a valid provenance marker", () => {
    for (const row of REGISTRATION_LOG_ROWS) {
      for (const [key, value] of Object.entries(row)) {
        if (EXEMPT_KEYS.has(key)) continue;
        const pv = value as { value: unknown; provenance: string };
        expect(
          VALID_PROVENANCE_MARKERS.has(pv.provenance),
          `REGISTRATION_LOG_ROWS key "${key}" has invalid provenance "${pv.provenance}"`,
        ).toBe(true);
      }
    }
  });

  it("FACTORY_REGISTRATION_FIXTURE is not empty", () => {
    expect(FACTORY_REGISTRATION_FIXTURE.recentRegistrations.length).toBeGreaterThan(0);
  });
});
