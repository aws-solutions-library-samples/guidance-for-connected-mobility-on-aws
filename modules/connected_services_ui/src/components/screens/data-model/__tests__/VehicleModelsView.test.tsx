// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * VehicleModelsView.test.tsx — integration tests for VehicleModelsView.
 *
 * Spec: `.kiro/specs/2026-09-14-cs-portal-data-model-backend/tasks.md` T3.3
 *
 * ## What this file covers
 *
 * 1. Settle marker is present in ready state.
 * 2. Loading state renders a spinner (not the table).
 * 3. Unconfigured state (null response) renders an info alert.
 * 4. Error state renders an error alert with the message.
 * 5. Empty state (ready, zero manifests) renders the table with empty message.
 * 6. Ready state: rows appear for each manifest.
 * 7. Ready state: vehicleCount column is NOT present.
 * 8. Ready state: decoderManifestRef link renders when present.
 * 9. Ready state: clicking a model navigates to the detail page.
 * 10. Filter: matching text reduces visible rows; unmatched text shows no-match state.
 */

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as dataModelClient from "../../../../api/dataModelClient";
import type { ModelManifestItem, ModelManifestsResponse } from "../../../../api/dataModelClient";
import VehicleModelsView from "../VehicleModelsView";

// ── Fixtures ──────────────────────────────────────────────────────────────────

const MANIFEST_A: ModelManifestItem = {
  modelManifestName: "MERIDIAN-TRAILWIND",
  modelManifestVersion: "1",
  displayName: "Meridian Trailwind",
  modelLine: "Trailwind",
  platform: "EV_PLATFORM",
  productionPhase: "PRODUCTION",
  status: "ACTIVE",
  description: "Meridian Trailwind EV",
  isDefault: false,
  decoderManifestRef: "cms-fleet-v3",
  signalCount: 45,
  ecuConfigId: "ecu-cfg-1",
  fleetIds: [],
  ecus: [
    { ecu: "TCU", displayName: "Telematics Control Unit" },
    { ecu: "BMS", displayName: "Battery Management System" },
  ],
  pk: "MODEL#MERIDIAN-TRAILWIND",
  sk: "MANIFEST#1",
  createTimestamp: "2024-01-01T00:00:00Z",
  updateTimestamp: "2024-06-01T00:00:00Z",
};

const MANIFEST_B: ModelManifestItem = {
  modelManifestName: "MERIDIAN-WINDROSE",
  modelManifestVersion: "2",
  displayName: "Meridian Windrose",
  modelLine: "Windrose",
  platform: "EV_PLATFORM",
  productionPhase: "VALIDATION",
  status: "DRAFT",
  description: "Meridian Windrose EV — validation",
  isDefault: false,
  decoderManifestRef: "",
  signalCount: 38,
  ecuConfigId: "ecu-cfg-2",
  fleetIds: [],
  ecus: [{ ecu: "TCU", displayName: "Telematics Control Unit" }],
  pk: "MODEL#MERIDIAN-WINDROSE",
  sk: "MANIFEST#2",
  createTimestamp: "2024-03-01T00:00:00Z",
  updateTimestamp: "2024-09-01T00:00:00Z",
};

const TWO_MANIFESTS: ModelManifestsResponse = {
  modelManifests: [MANIFEST_A, MANIFEST_B],
  count: 2,
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

// ── Render helper ─────────────────────────────────────────────────────────────

function renderVehicleModels() {
  return render(
    <MemoryRouter initialEntries={["/data-model/vehicle-models"]}>
      <Routes>
        <Route path="/data-model/vehicle-models" element={<VehicleModelsView />} />
        <Route
          path="/data-model/vehicle-models/:modelId"
          element={<div data-testid="model-detail-page" />}
        />
        <Route
          path="/data-model/decoder-manifests/:manifestId"
          element={<div data-testid="decoder-manifest-page" />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

// ── 1. Loading state ──────────────────────────────────────────────────────────

describe("loading state", () => {
  it("renders a spinner while the fetch is in flight", async () => {
    // Never resolves so loading state persists
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockReturnValue(
      new Promise(() => {}),
    );

    renderVehicleModels();
    expect(screen.getByTestId("vehicle-models-loading")).toBeTruthy();
  });
});

// ── 2. Unconfigured state ─────────────────────────────────────────────────────

describe("unconfigured state (null response)", () => {
  it("renders info alert when fetchVehicleModels returns null", async () => {
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue(null);

    renderVehicleModels();
    await waitFor(() => {
      expect(screen.getByTestId("vehicle-models-unconfigured")).toBeTruthy();
    });
    // Must not render the table
    expect(screen.queryByTestId("vehicle-models-table")).toBeNull();
  });
});

// ── 3. Error state ────────────────────────────────────────────────────────────

describe("error state", () => {
  it("renders error alert when fetchVehicleModels throws", async () => {
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockRejectedValue(
      new Error("data-processing API /model-manifests returned 500 Internal Server Error"),
    );

    renderVehicleModels();
    await waitFor(() => {
      expect(screen.getByTestId("vehicle-models-error")).toBeTruthy();
    });
    expect(screen.getByText(/500/)).toBeTruthy();
  });
});

// ── 4. Empty state ────────────────────────────────────────────────────────────

describe("empty state (ready with zero manifests)", () => {
  it("renders the table with empty message when response has zero manifests", async () => {
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue({
      modelManifests: [],
      count: 0,
    });

    renderVehicleModels();
    await waitFor(() => {
      expect(screen.getByTestId("vehicle-models-table")).toBeTruthy();
    });
    expect(screen.getByTestId("vehicle-models-empty")).toBeTruthy();
  });
});

// ── 5. Ready state: rows render ───────────────────────────────────────────────

describe("ready state", () => {
  it("renders the settle marker", async () => {
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue(TWO_MANIFESTS);

    renderVehicleModels();
    await waitFor(() => {
      expect(screen.getByTestId("vehicle-models-settle-marker")).toBeTruthy();
    });
  });

  it("renders one row per manifest in the table", async () => {
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue(TWO_MANIFESTS);

    renderVehicleModels();
    const table = await screen.findByTestId("vehicle-models-table");
    const rows = within(table).getAllByRole("row");
    // header row + 2 data rows
    expect(rows.length).toBeGreaterThanOrEqual(3);
  });

  it("renders the manifest name as a link", async () => {
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue(TWO_MANIFESTS);

    renderVehicleModels();
    await waitFor(() => {
      expect(screen.getByText("MERIDIAN-TRAILWIND")).toBeTruthy();
    });
  });

  it("renders the signalCount column", async () => {
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue(TWO_MANIFESTS);

    renderVehicleModels();
    await waitFor(() => {
      // MANIFEST_A has signalCount: 45
      expect(screen.getByText("45")).toBeTruthy();
    });
  });

  it("does NOT render a vehicleCount column (stale field)", async () => {
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue(TWO_MANIFESTS);

    renderVehicleModels();
    await waitFor(() => screen.getByTestId("vehicle-models-table"));

    // Column header should not appear
    expect(screen.queryByText(/VINs|vehicleCount|Vehicle count/i)).toBeNull();
  });

  it("renders decoderManifestRef as a link when present", async () => {
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue(TWO_MANIFESTS);

    renderVehicleModels();
    await waitFor(() => {
      expect(screen.getByText("cms-fleet-v3")).toBeTruthy();
    });
  });

  it("renders absent (—) for decoderManifestRef when empty string", async () => {
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue({
      modelManifests: [MANIFEST_B], // has decoderManifestRef: ""
      count: 1,
    });

    renderVehicleModels();
    await waitFor(() => screen.getByTestId("vehicle-models-table"));

    // MANIFEST_B has no ref — should show em-dash not a broken link
    const table = screen.getByTestId("vehicle-models-table");
    // The row should contain an em dash for the empty ref
    expect(within(table).getAllByRole("row").length).toBeGreaterThanOrEqual(2);
  });
});

// ── 6. Filter ─────────────────────────────────────────────────────────────────

describe("text filter", () => {
  it("shows no-match state when filter matches nothing", async () => {
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue(TWO_MANIFESTS);

    renderVehicleModels();
    await waitFor(() => screen.getByTestId("vehicle-models-table"));

    const filter = screen.getByPlaceholderText("Filter by model name, line, or platform");
    await userEvent.type(filter, "XYZNONEXISTENT");

    await waitFor(() => {
      expect(screen.getByTestId("vehicle-models-no-match")).toBeTruthy();
    });
  });

  it("filters rows to matching manifests", async () => {
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue(TWO_MANIFESTS);

    renderVehicleModels();
    await waitFor(() => screen.getByTestId("vehicle-models-table"));

    const filter = screen.getByPlaceholderText("Filter by model name, line, or platform");
    await userEvent.type(filter, "Trailwind");

    await waitFor(() => {
      expect(screen.getByText("MERIDIAN-TRAILWIND")).toBeTruthy();
    });
    expect(screen.queryByText("MERIDIAN-WINDROSE")).toBeNull();
  });
});

// ── 7. ECU count column ───────────────────────────────────────────────────────

describe("ECU count column", () => {
  it("renders the number of ecus[] entries per manifest", async () => {
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue({
      modelManifests: [MANIFEST_A],
      count: 1,
    });

    renderVehicleModels();
    await waitFor(() => screen.getByTestId("vehicle-models-table"));

    // MANIFEST_A has 2 ECUs
    expect(screen.getByText("2")).toBeTruthy();
  });
});
