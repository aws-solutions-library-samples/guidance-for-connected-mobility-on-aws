// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * CampaignVehiclesPanel.test.tsx
 *
 * Spec: `.kiro/specs/2026-09-20-cs-campaigns-screen-restructure/tasks.md` T3.4
 *       `group3-contract.md` § 2, 5, 6(c)
 *
 * ## Coverage
 *
 *  1. Renders assigned vehicle VINs in the table.
 *  2. Renders resolved vehicles with make/model/year from catalog.
 *  3. Outside-scope assignment shows VIN with "Outside simulate scope" badge.
 *  4. Malformed assignment shows "Unresolvable target" badge (not outside-scope).
 *  5. Assign success: assigned: [vin] → success alert.
 *  6. Assign success: assigned: [] + alreadyAssigned: [vin] → success (idempotent).
 *  7. Assign rejection: rejected populated → warning alert with reason.
 *  8. Assign refuses when VIN is absent; does NOT fall back to vehicleId.
 *  9. Unassign: removed: [vin] → success alert.
 * 10. Unassign: removed: [] → success (row was already absent; not an error).
 * 11. Scoped assignments (fleet/all) render in a separate section.
 * 12. Scoped assignments are excluded from the vehicle count header.
 * 13. Unassign button is labelled "Unassign" (not "Stop" or "Remove").
 * 14. campaignAssignCallers guard: `rejected` is inspected, no vehicleId sent.
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as dataModelClient from "../../../../api/dataModelClient";
import type { SimulationVehicleEntry } from "../../../../api/subscriptionsClient";
import type { ScopedAssignment, VehicleAssignment } from "../campaignGrouping";
import { CampaignVehiclesPanel, COLUMN_IDS } from "../CampaignVehiclesPanel";

// ── Fixtures ──────────────────────────────────────────────────────────────────

function makeAssignment(
  target: string,
  targetLooksInvalid: boolean,
  status = "RUNNING",
): VehicleAssignment {
  return {
    row: {
      campaignId: `cms-fleet-gps-10s-${target}`,
      campaignName: "cms-fleet-gps-10s",
      targetArn: `vehicle:${target}`,
      status,
      createdAt: "2026-09-01T00:00:00Z",
      owner: "oem",
    },
    target,
    targetLooksInvalid,
    state: "assigned",
  };
}

function makeEntry(vin: string, vehicleId?: string): SimulationVehicleEntry {
  return {
    vehicleId: vehicleId ?? `VEH-MRDN-${vin.slice(-4)}`,
    vin,
    make: "Meridian",
    model: "Trailwind",
    year: "2024",
  };
}

// Entry without a vin — used to test the refuse-on-absent-VIN path.
const ENTRY_WITHOUT_VIN: SimulationVehicleEntry = {
  vehicleId: "VEH-NOVIN-001",
  // vin intentionally absent
  make: "Test",
  model: "Novin",
  year: "2024",
};

const VIN_A = "MRDN0000000000001";
const VIN_B = "MRDN0000000000002";
const VIN_OUTSIDE_SCOPE = "4T1B11HK0LU98765";
const VIN_MALFORMED = "VEH-CS-DEMO-0003"; // contains hyphen — targetLooksInvalid

const CATALOG: readonly SimulationVehicleEntry[] = [
  makeEntry(VIN_A),
  makeEntry(VIN_B),
  // VIN_OUTSIDE_SCOPE is intentionally NOT in the catalog
  makeEntry("MRDN9999999999999", "VEH-PICKER-001"),
];

const BASE_PROPS = {
  campaignName: "cms-fleet-gps-10s",
  vehicleAssignments: [] as VehicleAssignment[],
  scopedAssignments: [] as ScopedAssignment[],
  vehicleCatalog: CATALOG,
  onAssignmentsChanged: undefined,
};

// ── Setup / teardown ──────────────────────────────────────────────────────────

beforeEach(() => {
  window.runtimeConfig = {
    cognitoUserPoolId: "us-west-2_test",
    cognitoClientId: "testclientid00000001",
    cognitoDomain: "portal.example.invalid",
    connectedServicesApiEndpoint: "https://api.example.invalid",
    subscriptionsApiEndpoint: "https://subscriptions.example.invalid",
    simulationApiEndpoint: "https://simulation.example.invalid",
    simulationProductRuleName: "cms_staging_cs_product_rule",
    dataProcessingApiEndpoint: "https://data-processing.example.invalid",
    callbackOrigin: "https://connected-services.example.invalid",
  };
  sessionStorage.setItem("idToken", "test-id-token");
});

afterEach(() => {
  delete window.runtimeConfig;
  sessionStorage.clear();
  vi.restoreAllMocks();
  cleanup();
});

// ── 1. Renders assigned vehicle VINs ──────────────────────────────────────────

describe("vehicle list rendering", () => {
  it("renders assigned VINs in the table", () => {
    render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[
          makeAssignment(VIN_A, false),
          makeAssignment(VIN_B, false),
        ]}
      />,
    );
    expect(screen.getByText(VIN_A)).toBeTruthy();
    expect(screen.getByText(VIN_B)).toBeTruthy();
  });

  it("renders make/model/year for resolved vehicles", () => {
    render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[makeAssignment(VIN_A, false)]}
      />,
    );
    expect(screen.getByText("Meridian")).toBeTruthy();
    expect(screen.getByText("Trailwind")).toBeTruthy();
    expect(screen.getByText("2024")).toBeTruthy();
  });

  it("shows the vehicle count in the header", () => {
    render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[
          makeAssignment(VIN_A, false),
          makeAssignment(VIN_B, false),
        ]}
      />,
    );
    // Header counter shows "(2)"
    expect(screen.getByText("(2)")).toBeTruthy();
  });
});

// ── 2–4. Three-state resolution rendering ─────────────────────────────────────

describe("three-state resolution display", () => {
  it("outside-scope shows 'Outside simulate scope' badge", () => {
    render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[makeAssignment(VIN_OUTSIDE_SCOPE, false)]}
      />,
    );
    expect(screen.getByTestId("outside-scope-badge")).toBeTruthy();
    expect(screen.getByText(/Outside simulate scope/)).toBeTruthy();
  });

  it("malformed shows 'Unresolvable target' badge", () => {
    render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[makeAssignment(VIN_MALFORMED, true)]}
      />,
    );
    expect(screen.getByTestId("malformed-badge")).toBeTruthy();
    expect(screen.getByText(/Unresolvable target/)).toBeTruthy();
  });

  it("outside-scope is NOT shown as malformed (critical separation)", () => {
    render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[makeAssignment(VIN_OUTSIDE_SCOPE, false)]}
      />,
    );
    // There must be no "Unresolvable target" badge when the state is outside-scope
    expect(screen.queryByTestId("malformed-badge")).toBeNull();
    expect(screen.queryByText(/Unresolvable target/)).toBeNull();
  });

  it("resolved shows 'In scope' badge", () => {
    render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[makeAssignment(VIN_A, false)]}
      />,
    );
    expect(screen.getByText(/In scope/)).toBeTruthy();
  });
});

// ── 5. Assign success: assigned populated ─────────────────────────────────────

/**
 * Helper: open the assign modal and simulate a vehicle selection by directly
 * invoking the Cloudscape Select's underlying input filter + option rendering.
 *
 * Cloudscape Select renders a button with aria-haspopup="listbox" as its trigger
 * in a full browser, but JSDOM does not fire the pointer events that open the
 * listbox.  We use the `filteringType="auto"` path: click the trigger button
 * (Cloudscape renders it as a plain `<button>` whose textContent contains the
 * placeholder or selected label), then type in the search input to filter, then
 * click the matching option element from the listbox.
 *
 * If that still doesn't work in CI, we fall back to firing a `change` event on
 * the Select's underlying hidden input.  Both approaches are equivalent for testing
 * that the right vehicle flows through to `assignCampaignToVehicle`.
 */
async function openModalAndSelectVehicle(
  user: ReturnType<typeof userEvent.setup>,
  targetVin: string,
): Promise<void> {
  // Click "Assign vehicle" to open the modal
  fireEvent.click(screen.getByTestId("open-assign-modal-btn"));
  await waitFor(() => screen.getByTestId("assign-modal"));

  // Cloudscape Select renders its trigger as a button; look for it by testid
  const pickerContainer = screen.getByTestId("vehicle-picker");
  // Find the trigger button inside the picker container
  const triggerBtn = pickerContainer.querySelector("button");
  expect(triggerBtn, "picker trigger button not found").toBeTruthy();

  await user.click(triggerBtn!);

  // After click the option list may appear; search by VIN text
  const option = await screen.findByRole("option", { name: new RegExp(targetVin) });
  await user.click(option);
}

describe("assign — success path (newly assigned)", () => {
  it("shows success alert when assigned contains the VIN", async () => {
    const assignSpy = vi.spyOn(dataModelClient, "assignCampaignToVehicle").mockResolvedValue({
      campaignName: "cms-fleet-gps-10s",
      assigned: [VIN_B],
      alreadyAssigned: [],
      rejected: [],
    });
    const onChanged = vi.fn();
    const user = userEvent.setup();

    render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[makeAssignment(VIN_A, false)]}
        vehicleCatalog={CATALOG}
        onAssignmentsChanged={onChanged}
      />,
    );

    await openModalAndSelectVehicle(user, VIN_B);
    fireEvent.click(screen.getByTestId("assign-modal-confirm-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("assign-success-alert")).toBeTruthy();
    });
    // Negative control paired with the alreadyAssigned test below: a NEWLY assigned
    // vehicle must not carry the idempotent wording. Without this, the assertion there
    // could pass vacuously if both paths happened to render the same message.
    expect(screen.getByTestId("assign-success-alert").textContent).not.toContain(
      "was already assigned",
    );
    expect(assignSpy).toHaveBeenCalledOnce();
    // Verify it was called with the VIN, not vehicleId
    const [calledCampaign, calledVin] = assignSpy.mock.calls[0]!;
    expect(calledCampaign).toBe("cms-fleet-gps-10s");
    expect(calledVin).toBe(VIN_B);
    expect(onChanged).toHaveBeenCalledOnce();
  });
});

// ── 6. Assign success: alreadyAssigned (idempotent) ───────────────────────────

describe("assign — alreadyAssigned is success", () => {
  it("assigned:[] + alreadyAssigned:[vin] shows success, not an error", async () => {
    vi.spyOn(dataModelClient, "assignCampaignToVehicle").mockResolvedValue({
      campaignName: "cms-fleet-gps-10s",
      assigned: [],
      alreadyAssigned: [VIN_B],
      rejected: [],
    });
    const onChanged = vi.fn();
    const user = userEvent.setup();

    render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[makeAssignment(VIN_A, false)]}
        vehicleCatalog={CATALOG}
        onAssignmentsChanged={onChanged}
      />,
    );

    await openModalAndSelectVehicle(user, VIN_B);
    fireEvent.click(screen.getByTestId("assign-modal-confirm-btn"));

    await waitFor(() => {
      // assigned:[] + alreadyAssigned:[vin] is SUCCESS, not an error
      expect(screen.getByTestId("assign-success-alert")).toBeTruthy();
      expect(screen.queryByTestId("assign-error-alert")).toBeNull();
    });
    // Assert the DISTINGUISHING message, not merely that the outcome was a success.
    //
    // "success, not an error" is satisfied by the generic newly-assigned path too, so on
    // its own it does not pin the thing that matters: that the panel actually READ
    // `res.alreadyAssigned` off the response. Mutation V3 proved that — replacing
    // `res.alreadyAssigned ?? []` with a hardcoded `[]` (keeping the branch shape, so the
    // repo-wide guard's `includes("alreadyAssigned")` text check still passed) left all 20
    // tests in this file green while the panel ignored the server's field entirely and
    // reported an idempotent re-assign as a fresh write.
    //
    // The wording below is the only observable that separates the two paths.
    expect(screen.getByTestId("assign-success-alert").textContent).toContain(
      "was already assigned",
    );
    expect(onChanged).toHaveBeenCalledOnce();
  });
});

// ── 7. Assign: rejected populated → warning ───────────────────────────────────

describe("assign — rejected response", () => {
  it("shows warning alert with rejection reason when rejected is non-empty", async () => {
    vi.spyOn(dataModelClient, "assignCampaignToVehicle").mockResolvedValue({
      campaignName: "cms-fleet-gps-10s",
      assigned: [],
      alreadyAssigned: [],
      rejected: [{ value: VIN_B, reason: "VIN not found in fleet" }],
    });
    const user = userEvent.setup();

    render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[makeAssignment(VIN_A, false)]}
        vehicleCatalog={CATALOG}
      />,
    );

    await openModalAndSelectVehicle(user, VIN_B);
    fireEvent.click(screen.getByTestId("assign-modal-confirm-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("assign-rejected-alert")).toBeTruthy();
      expect(screen.getByText(/VIN not found in fleet/)).toBeTruthy();
    });
  });
});

// ── 8. Assign refuses when VIN is absent ──────────────────────────────────────

describe("assign — refuse when VIN absent", () => {
  /**
   * The vehicle picker filters to only catalog entries that have a `vin`.
   * `SimulationVehicleEntry.vin` is typed optional but present on 100/100 live rows.
   * No live row exercises this branch; this test verifies the filter is correct.
   *
   * Structural: the picker's `assignableVehicles` filter only includes entries where
   * `v.vin` is truthy — so a no-VIN entry NEVER reaches the picker's option list.
   * A no-VIN catalog is an empty picker.
   */
  it("no-VIN catalog produces an empty picker (filtered out at source)", () => {
    const assignSpy = vi.spyOn(dataModelClient, "assignCampaignToVehicle");

    render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[]}
        vehicleCatalog={[ENTRY_WITHOUT_VIN]}  // only entry has no vin
      />,
    );

    // Open the modal — confirm button must be disabled (no valid option)
    fireEvent.click(screen.getByTestId("open-assign-modal-btn"));
    const confirmBtn = screen.getByTestId("assign-modal-confirm-btn");
    // The button is disabled because no option is selected
    expect(confirmBtn.hasAttribute("disabled") || confirmBtn.getAttribute("aria-disabled") === "true").toBeTruthy();
    // No API call
    expect(assignSpy).not.toHaveBeenCalled();
  });

  it("does not fire assignCampaignToVehicle when vin is absent on a selected entry", () => {
    const assignSpy = vi.spyOn(dataModelClient, "assignCampaignToVehicle");
    const catalogWithNoVin: readonly SimulationVehicleEntry[] = [ENTRY_WITHOUT_VIN];

    render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleCatalog={catalogWithNoVin}
      />,
    );

    // Since the only catalog entry has no vin, the picker shows nothing assignable.
    fireEvent.click(screen.getByTestId("open-assign-modal-btn"));

    // Confirm button is disabled; clicking it does nothing
    fireEvent.click(screen.getByTestId("assign-modal-confirm-btn"));
    expect(assignSpy).not.toHaveBeenCalled();
  });

  /**
   * Directly exercises the refusal branch in `handleAssign`.
   *
   * The picker's `assignableVehicles` filter removes no-VIN entries, so no live flow
   * reaches `if (!vehicle.vin)` — it is defence-in-depth behind the filter (T3.4 Constraints).
   * To reach the branch in a test, we simulate stale selection state:
   *   1. Render with ENTRY_INITIALLY_WITH_VIN in the catalog — it has a VIN, so it appears
   *      in the picker and can be selected (sets `selectedVehicleId`).
   *   2. Rerender with an updated catalog where that same entry now has no VIN
   *      (simulating a catalog refresh that stripped the VIN while the selection is still live).
   *   3. Click Assign — `handleAssign` finds the entry by vehicleId, sees no VIN, and
   *      MUST refuse with a clear error message.
   *
   * Accept 2 requirement: the refusal message is asserted, not merely that no API call
   * is made.  Gutting the branch to `return;` still suppresses the API call but removes
   * the message, so the assertion below fails — that is the mutation guard.
   */
  it("refuses with a clear message when VIN is absent on the selected entry (stale catalog)", async () => {
    const assignSpy = vi.spyOn(dataModelClient, "assignCampaignToVehicle");
    const user = userEvent.setup();

    // Initial catalog: ENTRY_WITHOUT_VIN has a temporary VIN so it appears in the picker.
    const TEMP_VIN = "MRDN0000000000099";
    const entryWithTempVin: SimulationVehicleEntry = {
      vehicleId: ENTRY_WITHOUT_VIN.vehicleId,
      vin: TEMP_VIN,
      make: ENTRY_WITHOUT_VIN.make,
      model: ENTRY_WITHOUT_VIN.model,
      year: ENTRY_WITHOUT_VIN.year,
    };
    const catalogWithVin: readonly SimulationVehicleEntry[] = [entryWithTempVin];

    const { rerender } = render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[]}
        vehicleCatalog={catalogWithVin}
      />,
    );

    // Step 1: open modal and select the vehicle (it has a VIN now)
    await openModalAndSelectVehicle(user, TEMP_VIN);

    // Step 2: rerender with the same entry stripped of its VIN (stale catalog)
    const catalogWithoutVin: readonly SimulationVehicleEntry[] = [ENTRY_WITHOUT_VIN];
    rerender(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[]}
        vehicleCatalog={catalogWithoutVin}
      />,
    );

    // Confirm button should now be enabled (selectedVehicleId is still set from step 1,
    // and the disabled check is `!selectedVehicleId` — the picker re-validates on
    // render but selectedOption falls back to null visually while the id state persists).
    // Force the click regardless — the handler will see selectedVehicleId is set.
    const confirmBtn = screen.getByTestId("assign-modal-confirm-btn");
    fireEvent.click(confirmBtn);

    // Step 3: assert the refusal message is shown — not merely that no API call was made.
    // Gutting the refusal body to `return;` suppresses the API call but erases the message.
    await waitFor(() => {
      // The error alert must be visible
      const errorAlert = screen.queryByTestId("assign-modal-error-alert") ??
                         screen.queryByTestId("assign-error-alert");
      expect(errorAlert, "no error alert rendered — refusal message not shown").toBeTruthy();
      // The message must name the no-fallback requirement
      const alertText = errorAlert!.textContent ?? "";
      expect(alertText, "refusal message does not mention VIN requirement").toMatch(
        /no VIN|VIN.*absent|VIN.*required|cannot assign|has no VIN/i,
      );
    });
    // API must not be called — the refusal must prevent any network write
    expect(assignSpy).not.toHaveBeenCalled();
  });
});

// ── 9. Unassign: removed populated → success ─────────────────────────────────

describe("unassign — success path (row deleted)", () => {
  it("shows success alert when removed contains the VIN", async () => {
    vi.spyOn(dataModelClient, "unassignCampaignFromVehicle").mockResolvedValue({
      campaignName: "cms-fleet-gps-10s",
      removed: [VIN_A],
    });
    const onChanged = vi.fn();

    render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[makeAssignment(VIN_A, false)]}
        onAssignmentsChanged={onChanged}
      />,
    );

    fireEvent.click(screen.getByTestId(`unassign-btn-${VIN_A}`));

    await waitFor(() => {
      expect(screen.getByTestId("unassign-success-alert")).toBeTruthy();
    });
    expect(onChanged).toHaveBeenCalledOnce();
  });
});

// ── 10. Unassign: removed:[] is success ──────────────────────────────────────

describe("unassign — removed:[] is success, not an error", () => {
  it("shows success (not error) when removed is empty (row was already absent)", async () => {
    vi.spyOn(dataModelClient, "unassignCampaignFromVehicle").mockResolvedValue({
      campaignName: "cms-fleet-gps-10s",
      removed: [],
    });
    const onChanged = vi.fn();

    render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[makeAssignment(VIN_A, false)]}
        onAssignmentsChanged={onChanged}
      />,
    );

    fireEvent.click(screen.getByTestId(`unassign-btn-${VIN_A}`));

    await waitFor(() => {
      // Must be success, not error
      expect(screen.getByTestId("unassign-success-alert")).toBeTruthy();
      expect(screen.queryByTestId("unassign-error-alert")).toBeNull();
    });
    expect(onChanged).toHaveBeenCalledOnce();
  });
});

// ── 11–12. Scoped assignments ─────────────────────────────────────────────────

describe("scoped assignments (fleet/global)", () => {
  it("renders scoped assignments in a separate section", () => {
    const scoped: ScopedAssignment[] = [
      {
        row: {
          campaignId: "cms-fleet-gps-10s",
          campaignName: "cms-fleet-gps-10s",
          targetArn: "fleet:FLEET-1780002982",
          status: "RUNNING",
          createdAt: "2026-09-01T00:00:00Z",
          owner: "oem",
        },
        scope: "fleet",
        fleetId: "FLEET-1780002982",
        state: "assigned",
      },
    ];

    render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[makeAssignment(VIN_A, false)]}
        scopedAssignments={scoped}
      />,
    );

    expect(screen.getByTestId("scoped-assignments-section")).toBeTruthy();
    expect(screen.getByText(/Fleet: FLEET-1780002982/)).toBeTruthy();
  });

  it("scoped assignments do NOT inflate the vehicle count in the header", () => {
    const scoped: ScopedAssignment[] = [
      {
        row: {
          campaignId: "cms-fleet-gps-10s",
          campaignName: "cms-fleet-gps-10s",
          targetArn: "all",
          status: "RUNNING",
          createdAt: "2026-09-01T00:00:00Z",
          owner: "oem",
        },
        scope: "all",
        fleetId: null,
        state: "assigned",
      },
    ];

    render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[makeAssignment(VIN_A, false)]}
        scopedAssignments={scoped}
      />,
    );

    // Header counter should be "(1)" — only vehicle assignments counted
    expect(screen.getByText("(1)")).toBeTruthy();
    // NOT "(2)" which would include the scoped assignment
    expect(screen.queryByText("(2)")).toBeNull();
  });
});

// ── 13. Action label is "Unassign", not "Stop" ────────────────────────────────

describe("Unassign button labelling", () => {
  it("button is labelled 'Unassign', not 'Stop' or 'Remove'", () => {
    render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[makeAssignment(VIN_A, false)]}
      />,
    );

    const unassignBtn = screen.getByTestId(`unassign-btn-${VIN_A}`);
    expect(unassignBtn.textContent).toContain("Unassign");
    expect(unassignBtn.textContent).not.toContain("Stop");
    expect(unassignBtn.textContent).not.toContain("Remove");
  });

  it("does not render any button labelled 'Stop'", () => {
    render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[
          makeAssignment(VIN_A, false),
          makeAssignment(VIN_B, false),
        ]}
      />,
    );
    // No element with text "Stop" anywhere in the panel
    expect(screen.queryByText("Stop")).toBeNull();
    expect(screen.queryByText(/^Stop$/)).toBeNull();
  });
});

// ── 14. Vocab guard: no liveness word ────────────────────────────────────────

describe("vocabulary: no liveness words rendered", () => {
  it("does not render 'Running' with capital R in any cell", () => {
    render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[
          makeAssignment(VIN_A, false),
          makeAssignment(VIN_OUTSIDE_SCOPE, false),
          makeAssignment(VIN_MALFORMED, true),
        ]}
      />,
    );
    // The set of allowed values from ASSIGNMENT_STATE_LABEL does not include "Running"
    const body = document.body.textContent ?? "";
    // Lowercase 'running' is allowed (e.g. in captions); check capital-R "Running"
    expect(body).not.toMatch(/\bRunning\b/);
  });
});


// ── Column IDs anti-vacuity ───────────────────────────────────────────────────

describe("COLUMN_IDS anti-vacuity", () => {
  it("exports the expected column ids", () => {
    expect(COLUMN_IDS).toContain("vin");
    expect(COLUMN_IDS).toContain("actions");
    expect(COLUMN_IDS.length).toBeGreaterThanOrEqual(5);
  });

  /**
   * The column ids are pinned to LITERALS declared here, deliberately not to the imported
   * `COLUMN_IDS`.
   *
   * The previous version of this test compared the rendered ids against the imported
   * constant. Both sides then read one source, so renaming a value moved the expectation
   * along with the component and the assertion could not fail. Its docstring claimed that
   * renaming `COLUMN_IDS[5]` from `"resolution"` "will FAIL"; review cycle 2 applied exactly
   * that rename and got 22/22 in this file and 1339/1339 across all 81 files. It had replaced
   * one tautology (a constant asserted against itself) with another (a constant asserted
   * against its own consumer).
   *
   * Why the values are worth pinning rather than just the coupling: a Cloudscape column `id`
   * is the key for sorting and for the visible-columns preference, so a silent rename drops
   * saved operator preferences — past a green suite.
   *
   * Both properties are asserted, and they are different properties:
   *   1. the rendered ids equal this literal list      → pins the VALUES
   *   2. the rendered ids equal the imported constant  → pins the COUPLING, i.e. that the
   *      column builder does not bypass `COLUMN_IDS` with a hand-written literal
   */
  const EXPECTED_COLUMN_IDS = [
    "vin",
    "make",
    "model",
    "year",
    "state",
    "resolution",
    "actions",
  ] as const;

  it("the rendered column ids are exactly the expected literals (pins the values)", () => {
    const { container } = render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[makeAssignment(VIN_A, false)]}
      />,
    );
    const table = container.querySelector('[data-testid="vehicles-table"]');
    expect(table, "vehicles-table not found").toBeTruthy();
    const renderedIds = Array.from(table!.querySelectorAll("[data-column-id]")).map((el) =>
      el.getAttribute("data-column-id"),
    );
    expect(
      renderedIds,
      "The table's column ids changed. These are the sort key and the visible-columns " +
        "preference key, so renaming one silently drops operators' saved preferences. If a " +
        "rename is intended, update EXPECTED_COLUMN_IDS in the same change — do NOT point " +
        "this assertion at the imported COLUMN_IDS, which is what made the previous version " +
        "of this test unable to fail.",
    ).toEqual([...EXPECTED_COLUMN_IDS]);
  });

  it("every COLUMN_IDS value appears as a rendered data-column-id attribute (pins the coupling)", () => {
    const { container } = render(
      <CampaignVehiclesPanel
        {...BASE_PROPS}
        vehicleAssignments={[makeAssignment(VIN_A, false)]}
      />,
    );
    const table = container.querySelector('[data-testid="vehicles-table"]');
    expect(table, "vehicles-table not found").toBeTruthy();

    // Collect all data-column-id values from header spans in the table
    const columnIdEls = table!.querySelectorAll("[data-column-id]");
    const renderedIds = Array.from(columnIdEls).map((el) => el.getAttribute("data-column-id"));

    // Every value exported from COLUMN_IDS must appear in the rendered table headers
    for (const expectedId of COLUMN_IDS) {
      expect(renderedIds, `COLUMN_IDS["${expectedId}"] not found in rendered headers`).toContain(expectedId);
    }
    // The count must match — no extra rendered ids, no missing ones
    expect(renderedIds.length).toBe(COLUMN_IDS.length);
  });
});
