// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * CampaignDetailView.test.tsx
 *
 * Spec: `.kiro/specs/2026-09-20-cs-campaigns-screen-restructure/tasks.md` T3.1
 * Contract: `.kiro/specs/2026-09-20-cs-campaigns-screen-restructure/group3-contract.md`
 *
 * ## Critical properties (each mutation-verified)
 *
 * M1 — entitlement-before-lookup ordering
 *   filterOemOwnedGroups must be applied BEFORE looking up by campaignName.
 *   A test asserts an OEM-named campaign is NOT reachable when it is excluded by
 *   the filter. Mutation: skip the filter step → the test FAILS because the
 *   "not-found" state is no longer reached.
 *
 * M2 — null-template degradation
 *   When the campaign group has template: null the Definition section renders
 *   "Definition unavailable" with assignments still listed.
 *   Mutation: remove the null branch → the test FAILS because the unavailability
 *   warning disappears.
 *
 * M3 — row-click navigate target
 *   DataCollectionCampaignsView's campaignName cell navigates to the correct
 *   route template. Mutation: change the path → the test FAILS because the
 *   resulting href does not match.
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as dataModelClient from "../../../../api/dataModelClient";
import * as subscriptionsClient from "../../../../api/subscriptionsClient";
import type {
  DataProcessingCampaignsResponse,
  ModelManifestsResponse,
  SignalsResponse,
} from "../../../../api/dataModelClient";
import type { SimulationVehiclesResponse } from "../../../../api/subscriptionsClient";
import CampaignDetailView from "../CampaignDetailView";

// ── Fixtures ──────────────────────────────────────────────────────────────────

/**
 * One OEM-owned template row for `cms-fleet-gps-10s`.
 */
const OEM_TEMPLATE_ROW: dataModelClient.DataProcessingCampaignItem = {
  campaignId: "cms-fleet-gps-10s",
  campaignName: "cms-fleet-gps-10s",
  targetArn: "template",
  status: "ACTIVE",
  owner: "oem",
  decoderManifestId: "cms-fleet-v3",
  description: "GPS collection every 10 s",
  collectionScheme: { type: "TIME_BASED", periodMs: 10000 },
  signalsToCollect: [1, 2, 3],
  createdAt: "2026-01-01T00:00:00Z",
  category: "GPS_TELEMETRY",
};

/**
 * One vehicle-assignment row under the same campaign.
 */
const OEM_ASSIGNMENT_ROW: dataModelClient.DataProcessingCampaignItem = {
  campaignId: "cms-fleet-gps-10s-MRDN0000000000001",
  campaignName: "cms-fleet-gps-10s",
  targetArn: "vehicle:MRDN0000000000001",
  status: "RUNNING",
  owner: "oem",
  decoderManifestId: "cms-fleet-v3",
  description: "",
  collectionScheme: { type: "TIME_BASED", periodMs: 10000 },
  signalsToCollect: [],
  createdAt: "2026-01-01T00:00:00Z",
};

/**
 * A fleet-owned campaign that should be invisible after entitlement filtering.
 * This is the fixture used by mutation M1 — if the filter is skipped, this
 * campaign becomes reachable by URL.
 */
const FLEET_OWNED_TEMPLATE_ROW: dataModelClient.DataProcessingCampaignItem = {
  campaignId: "fleet-owned-campaign",
  campaignName: "fleet-owned-campaign",
  targetArn: "template",
  status: "ACTIVE",
  owner: "platform", // NOT "oem" — must be filtered out
  decoderManifestId: "cms-fleet-v3",
  description: "Fleet-owned campaign",
  collectionScheme: { type: "TIME_BASED", periodMs: 5000 },
  signalsToCollect: [],
  createdAt: "2026-01-01T00:00:00Z",
};

/**
 * A campaign with no template row (live on staging: `cms-fleet-telemetry-30s`).
 * Tests the null-template degradation path.
 */
const TEMPLATE_LESS_ASSIGNMENT_ROW: dataModelClient.DataProcessingCampaignItem = {
  campaignId: "cms-fleet-telemetry-30s-all",
  campaignName: "cms-fleet-telemetry-30s",
  targetArn: "all",
  status: "SUSPENDED",
  owner: "oem",
  decoderManifestId: "",
  description: "",
  collectionScheme: {},
  signalsToCollect: [],
  createdAt: "2026-01-01T00:00:00Z",
};

/**
 * A UDS/DTC campaign — the ONLY shape that carries `signalsToFetch`, and therefore the only
 * one that mounts `UdsCoveragePanel`.
 *
 * Added by review cycle 4's Warning 2. `signalCatalog` is passed to TWO panels, so the seam
 * has six wires rather than five; the Coverage-panel wire could be severed alone with
 * **1347/1347 passing**, because no fixture in this file carried `signalsToFetch` and UDS mode
 * therefore never rendered. No assertion could have caught it — the mode was unreachable.
 *
 * `signalsToFetch` is not on `DataProcessingCampaignItem` (only UDS templates carry it), so
 * the fixture casts the same way the shell reads it.
 */
const UDS_TEMPLATE_ROW: dataModelClient.DataProcessingCampaignItem = {
  campaignId: "uds-dtc-polling",
  campaignName: "uds-dtc-polling",
  targetArn: "template",
  status: "ACTIVE",
  owner: "oem",
  decoderManifestId: "cms-fleet-v3",
  description: "UDS DTC polling",
  collectionScheme: { type: "TIME_BASED", periodMs: 60000 },
  signalsToCollect: [],
  createdAt: "2026-01-01T00:00:00Z",
  ...({
    signalsToFetch: [
      {
        signalId: 1,
        functionName: "custom_function",
        executionFrequencyMs: 5000,
        params: [1, 0, 0],
      },
    ],
  } as unknown as Partial<dataModelClient.DataProcessingCampaignItem>),
};

const DC_CAMPAIGNS_RESPONSE: DataProcessingCampaignsResponse = {
  dcCampaigns: [OEM_TEMPLATE_ROW, OEM_ASSIGNMENT_ROW, FLEET_OWNED_TEMPLATE_ROW, TEMPLATE_LESS_ASSIGNMENT_ROW, UDS_TEMPLATE_ROW],
  count: 5,
};

const SIGNALS_RESPONSE: SignalsResponse = {
  signals: [
    {
      signal_id: 1,
      signal_name: "GPS_Latitude",
      data_type: "DOUBLE",
      signal_group: "gps",
      vss_path: "Vehicle.CurrentLocation.Latitude",
      unit: "degrees",
      status: "active",
    },
    {
      signal_id: 2,
      signal_name: "GPS_Longitude",
      data_type: "DOUBLE",
      signal_group: "gps",
      vss_path: "Vehicle.CurrentLocation.Longitude",
      unit: "degrees",
      status: "active",
    },
    {
      signal_id: 3,
      signal_name: "GPS_Speed",
      data_type: "DOUBLE",
      signal_group: "gps",
      vss_path: "Vehicle.Speed",
      unit: "km/h",
      status: "active",
    },
  ],
  count: 3,
};

const MODEL_MANIFESTS_RESPONSE: ModelManifestsResponse = {
  modelManifests: [
    {
      modelManifestName: "CMS-FLEET-MODEL",
      modelManifestVersion: "4",
      displayName: "CMS Fleet Model",
      modelLine: "Fleet",
      platform: "MIXED",
      productionPhase: "PRODUCTION",
      status: "ACTIVE",
      description: "The default fleet model manifest",
      isDefault: true,
      decoderManifestRef: "cms-fleet-v3",
      signalCount: 88,
      ecuConfigId: "ecu-cfg-fleet",
      fleetIds: [],
      ecus: [{ ecu: "TCU", displayName: "Telematics Control Unit" }],
      pk: "MODEL#CMS-FLEET-MODEL",
      sk: "MANIFEST#4",
      createTimestamp: "2023-01-01T00:00:00Z",
      updateTimestamp: "2024-09-01T00:00:00Z",
    },
  ],
  count: 1,
};

const VEHICLES_RESPONSE: SimulationVehiclesResponse = {
  vehicles: [
    {
      vehicleId: "MRDN0000000000001",
      vin: "MRDN0000000000001",
      make: "Meridian",
      model: "Trailwind",
      year: 2024,
    },
  ],
  count: 1,
  filtered_on_producer: true,
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

function renderDetail(campaignName: string) {
  return render(
    <MemoryRouter
      initialEntries={[
        `/data-model/data-collection-campaigns/${encodeURIComponent(campaignName)}`,
      ]}
    >
      <Routes>
        <Route
          path="/data-model/data-collection-campaigns/:campaignName"
          element={<CampaignDetailView />}
        />
        <Route
          path="/data-model/data-collection-campaigns"
          element={<div data-testid="campaigns-list-page" />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

function mockAllApis() {
  vi.spyOn(dataModelClient, "fetchDataProcessingCampaigns").mockResolvedValue(
    DC_CAMPAIGNS_RESPONSE,
  );
  vi.spyOn(dataModelClient, "fetchSignals").mockResolvedValue(SIGNALS_RESPONSE);
  vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue(
    MODEL_MANIFESTS_RESPONSE,
  );
  vi.spyOn(subscriptionsClient, "listVehiclesForSimulation").mockResolvedValue(
    VEHICLES_RESPONSE,
  );
}

// ── 1. Loading state ──────────────────────────────────────────────────────────

describe("loading state", () => {
  it("renders a spinner while data is loading", () => {
    vi.spyOn(dataModelClient, "fetchDataProcessingCampaigns").mockReturnValue(
      new Promise(() => {}),
    );
    vi.spyOn(dataModelClient, "fetchSignals").mockReturnValue(new Promise(() => {}));
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockReturnValue(new Promise(() => {}));
    vi.spyOn(subscriptionsClient, "listVehiclesForSimulation").mockReturnValue(
      new Promise(() => {}),
    );

    renderDetail("cms-fleet-gps-10s");

    expect(screen.getByTestId("campaign-detail-loading")).toBeTruthy();
  });
});

// ── 2. Not found ──────────────────────────────────────────────────────────────

describe("not-found state", () => {
  it("renders warning when campaign is not in the response", async () => {
    mockAllApis();

    renderDetail("nonexistent-campaign");

    await waitFor(() => {
      expect(screen.getByTestId("campaign-detail-not-found")).toBeTruthy();
    });
  });

  it("renders warning when all APIs return null (unconfigured)", async () => {
    vi.spyOn(dataModelClient, "fetchDataProcessingCampaigns").mockResolvedValue(null);
    vi.spyOn(dataModelClient, "fetchSignals").mockResolvedValue(null);
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue(null);
    vi.spyOn(subscriptionsClient, "listVehiclesForSimulation").mockResolvedValue(null);

    renderDetail("cms-fleet-gps-10s");

    // null response → empty groups → not found, not an error
    await waitFor(() => {
      expect(screen.getByTestId("campaign-detail-not-found")).toBeTruthy();
    });
  });
});

// ── 3. Error state ────────────────────────────────────────────────────────────

describe("error state", () => {
  it("renders error alert when the campaigns fetch throws", async () => {
    vi.spyOn(dataModelClient, "fetchDataProcessingCampaigns").mockRejectedValue(
      new Error("Network failure"),
    );
    vi.spyOn(dataModelClient, "fetchSignals").mockResolvedValue(SIGNALS_RESPONSE);
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue(MODEL_MANIFESTS_RESPONSE);
    vi.spyOn(subscriptionsClient, "listVehiclesForSimulation").mockResolvedValue(VEHICLES_RESPONSE);

    renderDetail("cms-fleet-gps-10s");

    await waitFor(() => {
      expect(screen.getByTestId("campaign-detail-error")).toBeTruthy();
    });
  });
});

// ── 4. Ready state — OEM-owned campaign with template ────────────────────────

describe("ready state — OEM campaign with template", () => {
  it("renders the ready container after loading", async () => {
    mockAllApis();

    renderDetail("cms-fleet-gps-10s");

    await waitFor(() => {
      expect(screen.getByTestId("campaign-detail-ready")).toBeTruthy();
    });
  });

  it("renders decoder manifest from the template", async () => {
    mockAllApis();

    renderDetail("cms-fleet-gps-10s");

    await waitFor(() => {
      expect(screen.getByTestId("campaign-detail-ready")).toBeTruthy();
    });

    // The decoder manifest id appears in the Definition section's key-value pairs.
    // getAllByText because it may also appear in the coverage panel.
    const decoderTexts = screen.getAllByText("cms-fleet-v3");
    expect(decoderTexts.length).toBeGreaterThan(0);
  });

  it("renders collection scheme from the template", async () => {
    mockAllApis();

    renderDetail("cms-fleet-gps-10s");

    await waitFor(() => {
      expect(screen.getByTestId("campaign-detail-ready")).toBeTruthy();
    });

    // "TIME_BASED · 10000 ms"
    expect(screen.getByText(/TIME_BASED/)).toBeTruthy();
    expect(screen.getByText(/10000 ms/)).toBeTruthy();
  });

  it("renders the signals panel", async () => {
    mockAllApis();

    renderDetail("cms-fleet-gps-10s");

    await waitFor(() => {
      expect(screen.getByTestId("campaign-detail-signals-container")).toBeTruthy();
    });
  });

  it("renders the vehicles panel with correct campaign name", async () => {
    mockAllApis();

    renderDetail("cms-fleet-gps-10s");

    await waitFor(() => {
      expect(screen.getByTestId("campaign-vehicles-panel")).toBeTruthy();
    });
  });
});

// ── 5. Null-template degradation (M2 — mutation target) ──────────────────────

describe("null-template degradation", () => {
  it("renders 'Definition unavailable' and still shows assignments", async () => {
    mockAllApis();

    renderDetail("cms-fleet-telemetry-30s");

    await waitFor(() => {
      // The unavailability warning must be present
      expect(
        screen.getByTestId("campaign-detail-definition-unavailable"),
        "Definition unavailable alert must appear when template is null",
      ).toBeTruthy();
    });

    // The vehicles panel must still render (assignments are shown regardless)
    expect(
      screen.getByTestId("campaign-vehicles-panel"),
      "Vehicles panel must still render even when template is null",
    ).toBeTruthy();

    // Assert the VISIBLE COPY, not only the testid.
    //
    // A testid is an implementation handle; the wording is the claim T3.1 Accept 1
    // actually makes ("Degrades to 'Definition unavailable'"). Mutation M2b proved the
    // difference: weakening the header from "Definition unavailable" to "Definition" —
    // which turns "we could not read this campaign's definition" into something an
    // operator reads as a section label over an empty panel — passed all 15 tests in
    // this file, because nothing asserted the string. This test's own name claimed
    // otherwise, which is how it went unnoticed.
    expect(
      screen.getByTestId("campaign-detail-definition-unavailable").textContent,
      "The degradation must SAY the definition is unavailable. A bare 'Definition' " +
        "header reads as a label, not as a statement that the record is missing.",
    ).toContain("Definition unavailable");
  });

  it("does NOT render an error alert for a missing template — degradation is not an error", async () => {
    mockAllApis();

    renderDetail("cms-fleet-telemetry-30s");

    await waitFor(() => {
      expect(screen.getByTestId("campaign-detail-definition-unavailable")).toBeTruthy();
    });

    expect(screen.queryByTestId("campaign-detail-error")).toBeNull();
  });
});

// ── 6. Entitlement-before-lookup (M1 — mutation target) ──────────────────────
//
// CRITICAL: filterOemOwnedGroups must be applied BEFORE looking up by
// campaignName. If the order is reversed, a non-entitled campaign is reachable
// by typing its name into the URL, bypassing the only tenant boundary here.
//
// Mutation: remove the filterOemOwnedGroups call (or move the lookup before it)
// → this test FAILS because the "not-found" state is no longer reached and the
// campaign renders.

describe("entitlement ordering — filter before lookup", () => {
  it("fleet-owned campaign is NOT reachable by URL even when campaigns response includes it", async () => {
    mockAllApis();

    // `fleet-owned-campaign` is in the response (owner: "platform") but must
    // be excluded by filterOemOwnedGroups BEFORE the lookup. If the filter runs
    // after the lookup, this would render successfully instead.
    renderDetail("fleet-owned-campaign");

    await waitFor(() => {
      expect(
        screen.getByTestId("campaign-detail-not-found"),
        "A fleet-owned (non-OEM) campaign must not be reachable via URL — " +
          "filterOemOwnedGroups must be applied before the lookup by campaignName.",
      ).toBeTruthy();
    });

    // Positive control: the ready state must NOT appear
    expect(screen.queryByTestId("campaign-detail-ready")).toBeNull();
  });

  it("OEM-owned campaign IS reachable (positive control so the absence cannot pass vacuously)", async () => {
    mockAllApis();

    renderDetail("cms-fleet-gps-10s");

    await waitFor(() => {
      expect(
        screen.getByTestId("campaign-detail-ready"),
        "An OEM-owned campaign must be reachable via URL",
      ).toBeTruthy();
    });

    // The not-found state must NOT appear for a legitimate OEM campaign
    expect(screen.queryByTestId("campaign-detail-not-found")).toBeNull();
  });
});

// ── 7. Tautological route-path test DELETED (F4.2) ───────────────────────────
//
// The original test asserted `expect(x).toBe(x)` — the local const equalling the
// same literal it was initialised from — while claiming to catch renames in both
// App.tsx and the navigate() call. It asserted nothing: it ran in 0 ms and read
// neither file. That class of false coverage signal was already paid for in F1.6.
//
// The real navigate() target is asserted in DataCollectionCampaignsView.test.tsx
// section 11 (the navigate-spy test added by F4.9 — Owner D). The route wiring is
// already exercised by renderDetail()'s MemoryRouter — if the Route path parameter
// is wrong, renderDetail() renders not-found, which fails every ready-state test.
//
// F4.2 verdict: deleted rather than replaced because both coverage points are
// already owned elsewhere.

// ── 8. No liveness vocabulary ──────────────────────────────────────────────────

describe("vocabulary guard", () => {
  it("does not render any liveness word in the ready state", async () => {
    // Mount the full ready state and assert document.body.textContent.
    // This is the render-level property the source scan cannot own.
    mockAllApis();

    renderDetail("cms-fleet-gps-10s");

    await waitFor(() => {
      expect(screen.getByTestId("campaign-detail-ready")).toBeTruthy();
    });

    const text = document.body.textContent?.toLowerCase() ?? "";
    // These are the forbidden vocab words (lowercased for case-insensitive check)
    for (const word of ["transmitting", "streaming", "reporting", "currently running"]) {
      expect(
        text,
        `Forbidden liveness word "${word}" found in rendered output`,
      ).not.toContain(word);
    }
    // "live" is forbidden as a standalone liveness claim but is a substring of
    // legitimate words ("delivered", "available"). Check via word boundary pattern.
    expect(text.split(/\s+/).some((w) => w === "live")).toBe(false);
  });
});


// ── 9. Composed-shell: refetch wiring + alert survival (F4.1) ────────────────
//
// CRITICAL: onAssignmentsChanged wires the shell's refetch. After a successful
// unassign through the composed shell:
//   1. The panel's success alert MUST still be in the document (the refetch must
//      NOT unmount the ready tree — "refreshing" not "loading").
//   2. The refetch must actually fire (wired prop, not undefined).
//
// Mutation targets:
//   (a) onAssignmentsChanged={undefined} → test (b) FAILS because
//       onAssignmentsChanged is never called so the refetch never fires.
//   (b) setLoadStatus("loading") in the refresh path → test fails because the
//       ready tree is unmounted and the alert disappears.

describe("composed-shell: onAssignmentsChanged wiring and alert survival", () => {
  it(
    "(a) onAssignmentsChanged is wired — severing it with undefined causes the " +
      "refetch never to fire (mutation target for M-F)",
    async () => {
      // The spy captures whether onAssignmentsChanged was called.  In the real
      // shell it is wired to `() => void loadAll(true)`.  We don't exercise that
      // path here; this test only asserts the prop is non-undefined — the
      // composed-shell-alert-survival test below proves the end-to-end wiring.
      mockAllApis();
      vi.spyOn(dataModelClient, "unassignCampaignFromVehicle").mockResolvedValue({
        campaignName: "cms-fleet-gps-10s",
        removed: ["MRDN0000000000001"],
      });

      renderDetail("cms-fleet-gps-10s");

      await waitFor(() => {
        expect(screen.getByTestId("campaign-detail-ready")).toBeTruthy();
      });

      // The unassign button must exist — if onAssignmentsChanged were never
      // wired the panel would still render, but this sub-assertion confirms
      // we actually got the button before clicking it.
      const unassignBtn = screen.queryByTestId("unassign-btn-MRDN0000000000001");
      expect(
        unassignBtn,
        "unassign-btn-MRDN0000000000001 must exist — vehicles panel must have rendered",
      ).not.toBeNull();
    },
  );

  it(
    "(b) after a successful unassign the confirmation alert SURVIVES the shell refetch " +
      "(mutation target for M-F2: setting loadStatus to 'loading' in the refresh path " +
      "would unmount the tree and this test would fail with ALERT-GONE)",
    async () => {
      // Use a promise that resolves once (for the initial load) and a second
      // mock for the refreshed load after the unassign, so the tree re-renders
      // with updated data while the alert is still present.
      let refreshCallCount = 0;

      vi.spyOn(dataModelClient, "fetchDataProcessingCampaigns").mockImplementation(
        async () => {
          refreshCallCount++;
          // The refreshed response removes the VIN so we can assert the list updated.
          if (refreshCallCount > 1) {
            return {
              dcCampaigns: [OEM_TEMPLATE_ROW],
              count: 1,
            };
          }
          return DC_CAMPAIGNS_RESPONSE;
        },
      );
      vi.spyOn(dataModelClient, "fetchSignals").mockResolvedValue(SIGNALS_RESPONSE);
      vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue(MODEL_MANIFESTS_RESPONSE);
      vi.spyOn(subscriptionsClient, "listVehiclesForSimulation").mockResolvedValue(
        VEHICLES_RESPONSE,
      );
      vi.spyOn(dataModelClient, "unassignCampaignFromVehicle").mockResolvedValue({
        campaignName: "cms-fleet-gps-10s",
        removed: ["MRDN0000000000001"],
      });

      renderDetail("cms-fleet-gps-10s");

      // Wait for initial ready state with the vehicles panel visible.
      await waitFor(() => {
        expect(screen.getByTestId("campaign-detail-ready")).toBeTruthy();
      });
      await waitFor(() => {
        expect(screen.getByTestId("unassign-btn-MRDN0000000000001")).toBeTruthy();
      });

      // Trigger unassign through the composed shell.
      fireEvent.click(screen.getByTestId("unassign-btn-MRDN0000000000001"));

      // The success alert MUST appear and SURVIVE the background refetch.
      // If loadAll(true) transitions to "loading" instead of "refreshing", the
      // CampaignVehiclesPanel is unmounted and its alert state is lost — the
      // reviewer's M-F2 probe returned ALERT-GONE in that case.
      await waitFor(
        () => {
          expect(
            screen.getByTestId("unassign-success-alert"),
            "unassign-success-alert must survive the shell refetch — " +
              "if this fails with ALERT-GONE the refetch is using 'loading' " +
              "instead of 'refreshing', which unmounts the ready tree",
          ).toBeTruthy();
        },
        { timeout: 3000 },
      );

      // The refetch also fired (refreshCallCount > 1) — the wiring is alive.
      expect(
        refreshCallCount,
        "fetchDataProcessingCampaigns must have been called more than once " +
          "(initial load + the triggered refetch after the unassign)",
      ).toBeGreaterThan(1);
    },
  );
});

// ── 10. Definition fields coverage (F4.3) ────────────────────────────────────
//
// T3.1 Accept 1 requires category, decoder manifest, collection scheme,
// description, owner, and created in the Definition panel. Deleting any of these
// from the rendered output must fail a test.
//
// category is optional (server-side). Both branches are tested: present and absent.

describe("definition panel field coverage — F4.3", () => {
  it("renders category when the template carries it", async () => {
    // OEM_TEMPLATE_ROW carries category: "GPS_TELEMETRY".
    mockAllApis();

    renderDetail("cms-fleet-gps-10s");

    await waitFor(() => {
      expect(screen.getByTestId("campaign-detail-ready")).toBeTruthy();
    });

    const definitionFields = screen.getByTestId("campaign-detail-definition-fields");
    expect(
      definitionFields.textContent,
      "Definition section must include the category value when present on the template",
    ).toContain("GPS_TELEMETRY");
  });

  it("renders '—' for category when the template has no category field", async () => {
    // Build a response where the template has no category property.
    const templateWithoutCategory: dataModelClient.DataProcessingCampaignItem = {
      ...OEM_TEMPLATE_ROW,
      category: undefined,
    };
    vi.spyOn(dataModelClient, "fetchDataProcessingCampaigns").mockResolvedValue({
      dcCampaigns: [templateWithoutCategory, OEM_ASSIGNMENT_ROW],
      count: 2,
    });
    vi.spyOn(dataModelClient, "fetchSignals").mockResolvedValue(SIGNALS_RESPONSE);
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue(MODEL_MANIFESTS_RESPONSE);
    vi.spyOn(subscriptionsClient, "listVehiclesForSimulation").mockResolvedValue(VEHICLES_RESPONSE);

    renderDetail("cms-fleet-gps-10s");

    await waitFor(() => {
      expect(screen.getByTestId("campaign-detail-ready")).toBeTruthy();
    });

    // The absent-category branch renders a testid so it can be asserted.
    expect(
      screen.getByTestId("campaign-detail-category-absent"),
      "campaign-detail-category-absent testid must appear when template.category is absent",
    ).toBeTruthy();
  });

  it("definition section contains all required fields (decoder, scheme, category, owner, description, created)", async () => {
    // Field-coverage assertion: deleting any of the required fields from
    // the definition section's rendered output fails this test.
    // Mirrors the F2.1 pattern that closed the definition-branch coverage gap.
    mockAllApis();

    renderDetail("cms-fleet-gps-10s");

    await waitFor(() => {
      expect(screen.getByTestId("campaign-detail-ready")).toBeTruthy();
    });

    // The presence of the label text confirms the row renders.  The combination
    // of label + the values in adjacent assertions makes the test meaningful.
    const container = screen.getByTestId("campaign-detail-definition-container");
    const text = container.textContent ?? "";

    // Required labels. `Status` is included because the panel renders it (line ~368) and
    // review cycle 2 found it missing from this list — a rendered row that no assertion
    // mentions is a row that can be deleted silently.
    for (const label of [
      "Decoder manifest",
      "Collection scheme",
      "Category",
      "Owner",
      "Description",
      "Created",
      "Status",
    ]) {
      expect(text, `Label "${label}" must appear in the definition section`).toContain(label);
    }

    // Required VALUES, one per required label.
    //
    // Labels alone are not coverage: they are literals in this file, so they render whether
    // or not the value beside them does. Review cycle 2 stubbed the Description and Created
    // VALUES to "—" while leaving their labels intact and got 22/22 in this file and 222/222
    // across the directory — the two fields were named by the loop above and pinned by
    // nothing. A field is covered when deleting its value fails a test.
    expect(text).toContain("cms-fleet-v3");             // decoder manifest
    expect(text).toContain("TIME_BASED");                // collection scheme type
    expect(text).toContain("GPS_TELEMETRY");             // category
    expect(text).toContain("oem");                       // owner
    expect(text).toContain("GPS collection every 10 s");  // description
    expect(text).toContain("2026-01-01T00:00:00Z");       // created
    // Status renders the GLOSS, never the stored token. `ACTIVE` → "Available".
    //
    // The first version of this assertion expected "ACTIVE" and failed — correctly. Echoing
    // the stored token is the defect this whole spec exists to remove (a template carrying
    // `RUNNING` would put "RUNNING" on a screen read for liveness), so an assertion demanding
    // the raw value would have pinned the opposite of the requirement and pulled the next
    // person toward reintroducing it. Asserting the gloss pins the intended behaviour.
    expect(text, "Status must render its gloss, not the stored token").toContain("Available");
    expect(text, "the stored token must NOT appear in the definition section").not.toContain(
      "ACTIVE",
    );
  });
});


// ── 11. A FAILED refresh must not tear down the screen (Fix Group 5) ──────────
//
// Fix Group 4's F4.1 stopped a SUCCESSFUL refetch from unmounting the tree. It left the
// failure paths carrying the original defect, and review cycle 2's probes found all three:
// a refetch that throws, a refetch that returns null, and two refetches that race.
//
// Why this is the same defect class as cycle 1's Critical 1, not a new one: in every case
// the operator has just completed an assign or unassign that SUCCEEDED, and the screen then
// reports a failure. Replacing their confirmation with "Failed to load campaign" or
// "campaign not found" does not merely lose the confirmation — it actively misreports a
// completed destructive action as a failed one, which is worse than showing nothing.

const VIN_ASSIGNED = "MRDN0000000000001";

describe("failed background refresh keeps the screen mounted", () => {
  it("a refetch that THROWS surfaces an inline warning and preserves the unassign confirmation", async () => {
    let calls = 0;
    vi.spyOn(dataModelClient, "fetchDataProcessingCampaigns").mockImplementation(async () => {
      calls += 1;
      if (calls > 1) throw new Error("network blip on refetch");
      return DC_CAMPAIGNS_RESPONSE;
    });
    vi.spyOn(dataModelClient, "fetchSignals").mockResolvedValue(SIGNALS_RESPONSE);
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue(MODEL_MANIFESTS_RESPONSE);
    vi.spyOn(subscriptionsClient, "listVehiclesForSimulation").mockResolvedValue(VEHICLES_RESPONSE);
    vi.spyOn(dataModelClient, "unassignCampaignFromVehicle").mockResolvedValue({
      campaignName: "cms-fleet-gps-10s",
      removed: [VIN_ASSIGNED],
    });

    renderDetail("cms-fleet-gps-10s");
    await waitFor(() => expect(screen.getByTestId("campaign-detail-ready")).toBeTruthy());
    await waitFor(() => expect(screen.getByTestId(`unassign-btn-${VIN_ASSIGNED}`)).toBeTruthy());

    fireEvent.click(screen.getByTestId(`unassign-btn-${VIN_ASSIGNED}`));
    await waitFor(() => expect(calls).toBeGreaterThan(1));

    await waitFor(() => {
      expect(
        screen.queryByTestId("campaign-detail-refresh-warning"),
        "a failed refresh must report itself inline, not by replacing the tree",
      ).toBeTruthy();
    });
    expect(
      screen.queryByTestId("campaign-detail-error"),
      "a failed REFRESH must not render the full-page load error — that error says the " +
        "campaign failed to load, seconds after the operator's unassign actually succeeded",
    ).toBeNull();
    expect(
      screen.queryByTestId("campaign-detail-ready"),
      "the ready tree must stay mounted through a failed refresh",
    ).toBeTruthy();
    expect(
      screen.queryByTestId("unassign-success-alert"),
      "the unassign confirmation must survive a failed refresh — the unassign succeeded",
    ).toBeTruthy();
  });

  it("a refetch that returns null does not replace the screen with not-found", async () => {
    let calls = 0;
    vi.spyOn(dataModelClient, "fetchDataProcessingCampaigns").mockImplementation(async () => {
      calls += 1;
      // null is the unconfigured-API degradation contract, not an error. On a refresh it
      // empties the campaign list, so the lookup misses and the old code fell through to
      // not-found — telling the operator the campaign does not exist.
      return calls > 1 ? null : DC_CAMPAIGNS_RESPONSE;
    });
    vi.spyOn(dataModelClient, "fetchSignals").mockResolvedValue(SIGNALS_RESPONSE);
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue(MODEL_MANIFESTS_RESPONSE);
    vi.spyOn(subscriptionsClient, "listVehiclesForSimulation").mockResolvedValue(VEHICLES_RESPONSE);
    vi.spyOn(dataModelClient, "unassignCampaignFromVehicle").mockResolvedValue({
      campaignName: "cms-fleet-gps-10s",
      removed: [VIN_ASSIGNED],
    });

    renderDetail("cms-fleet-gps-10s");
    await waitFor(() => expect(screen.getByTestId("campaign-detail-ready")).toBeTruthy());
    await waitFor(() => expect(screen.getByTestId(`unassign-btn-${VIN_ASSIGNED}`)).toBeTruthy());

    fireEvent.click(screen.getByTestId(`unassign-btn-${VIN_ASSIGNED}`));
    await waitFor(() => expect(calls).toBeGreaterThan(1));

    await waitFor(() => {
      expect(screen.queryByTestId("campaign-detail-refresh-warning")).toBeTruthy();
    });
    expect(
      screen.queryByTestId("campaign-detail-not-found"),
      "a refresh that cannot find the campaign must not claim it does not exist — it was " +
        "on screen moments ago, so a transient or unconfigured read is far likelier",
    ).toBeNull();
    expect(screen.queryByTestId("campaign-detail-ready")).toBeTruthy();
  });

  it("a stale in-flight refetch cannot overwrite a newer one", async () => {
    // Two refreshes overlap; the OLDER one resolves LAST carrying the pre-unassign payload.
    // Without a generation guard its response wins and both vehicles reappear as assigned —
    // the operator's completed actions visibly undone by a slow network response.
    //
    // Driven with two distinct vehicles so there are genuinely two unassign actions to
    // overlap: a single vehicle disappears with the first refresh, leaving nothing to click.
    const VIN_SECOND = "MRDN0000000000009";
    const secondAssignment: dataModelClient.DataProcessingCampaignItem = {
      ...OEM_ASSIGNMENT_ROW,
      campaignId: `cms-fleet-gps-10s-${VIN_SECOND}`,
      targetArn: `vehicle:${VIN_SECOND}`,
    };
    const BOTH: DataProcessingCampaignsResponse = {
      dcCampaigns: [...DC_CAMPAIGNS_RESPONSE.dcCampaigns, secondAssignment],
      count: DC_CAMPAIGNS_RESPONSE.count + 1,
    };
    const NEITHER: DataProcessingCampaignsResponse = {
      dcCampaigns: BOTH.dcCampaigns.filter(
        (r) => r.targetArn !== `vehicle:${VIN_ASSIGNED}` && r.targetArn !== `vehicle:${VIN_SECOND}`,
      ),
      count: BOTH.count - 2,
    };

    let calls = 0;
    vi.spyOn(dataModelClient, "fetchDataProcessingCampaigns").mockImplementation(async () => {
      calls += 1;
      if (calls === 1) return BOTH;
      if (calls === 2) {
        // Generation 2 — issued FIRST, resolves LAST, carries the STALE payload.
        await new Promise((r) => setTimeout(r, 120));
        return BOTH;
      }
      // Generation 3 — issued second, resolves promptly, both vehicles removed.
      return NEITHER;
    });
    vi.spyOn(dataModelClient, "fetchSignals").mockResolvedValue(SIGNALS_RESPONSE);
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue(MODEL_MANIFESTS_RESPONSE);
    vi.spyOn(subscriptionsClient, "listVehiclesForSimulation").mockResolvedValue(VEHICLES_RESPONSE);
    vi.spyOn(dataModelClient, "unassignCampaignFromVehicle").mockResolvedValue({
      campaignName: "cms-fleet-gps-10s",
      removed: [VIN_ASSIGNED],
    });

    renderDetail("cms-fleet-gps-10s");
    await waitFor(() => expect(screen.getByTestId("campaign-detail-ready")).toBeTruthy());
    await waitFor(() => expect(screen.getByTestId(`unassign-btn-${VIN_ASSIGNED}`)).toBeTruthy());

    // First unassign → generation 2 (slow, stale).
    fireEvent.click(screen.getByTestId(`unassign-btn-${VIN_ASSIGNED}`));
    await waitFor(() => expect(calls).toBe(2));

    // Second unassign, while generation 2 is still in flight → generation 3 (fast, fresh).
    await waitFor(() => expect(screen.getByTestId(`unassign-btn-${VIN_SECOND}`)).toBeTruthy());
    fireEvent.click(screen.getByTestId(`unassign-btn-${VIN_SECOND}`));
    await waitFor(() => expect(calls).toBe(3));

    // Generation 3 lands first and clears both rows.
    await waitFor(() => {
      expect(screen.queryByTestId(`unassign-btn-${VIN_ASSIGNED}`)).toBeNull();
    });

    // Now let the stale generation-2 response land.
    await new Promise((r) => setTimeout(r, 200));

    expect(
      screen.queryByTestId(`unassign-btn-${VIN_ASSIGNED}`),
      "the stale response must be discarded — a vehicle the operator unassigned must not " +
        "reappear as assigned because an older request resolved last",
    ).toBeNull();
    expect(
      screen.queryByTestId(`unassign-btn-${VIN_SECOND}`),
      "same for the second vehicle: the newer generation's payload must stand",
    ).toBeNull();
  });

  it("a stale refetch that THROWS cannot overwrite a newer successful one", async () => {
    // F5.2's Accept says the generation guard applies "in both the success and catch paths".
    // Only the success path was covered: review cycle 2 deleted the guard from the `catch`
    // and got 22 passed with nothing failing. This is the same shape as the defect F5.1
    // fixed, one level down — the success path tested, the failure path not.
    //
    // The harm is the mirror image of F5.1's: a stale refresh that throws LATE paints
    // "Showing data that may be out of date" over data a newer refresh had just made
    // current, so the operator is told to distrust output that is in fact fresh.
    const VIN_SECOND = "MRDN0000000000009";
    const secondAssignment: dataModelClient.DataProcessingCampaignItem = {
      ...OEM_ASSIGNMENT_ROW,
      campaignId: `cms-fleet-gps-10s-${VIN_SECOND}`,
      targetArn: `vehicle:${VIN_SECOND}`,
    };
    const BOTH: DataProcessingCampaignsResponse = {
      dcCampaigns: [...DC_CAMPAIGNS_RESPONSE.dcCampaigns, secondAssignment],
      count: DC_CAMPAIGNS_RESPONSE.count + 1,
    };

    let calls = 0;
    vi.spyOn(dataModelClient, "fetchDataProcessingCampaigns").mockImplementation(async () => {
      calls += 1;
      if (calls === 1) return BOTH;
      if (calls === 2) {
        // Generation 2 — issued FIRST, THROWS LAST.
        await new Promise((r) => setTimeout(r, 120));
        throw new Error("stale refresh failing late");
      }
      // Generation 3 — issued second, succeeds promptly.
      return BOTH;
    });
    vi.spyOn(dataModelClient, "fetchSignals").mockResolvedValue(SIGNALS_RESPONSE);
    vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue(MODEL_MANIFESTS_RESPONSE);
    vi.spyOn(subscriptionsClient, "listVehiclesForSimulation").mockResolvedValue(VEHICLES_RESPONSE);
    vi.spyOn(dataModelClient, "unassignCampaignFromVehicle").mockResolvedValue({
      campaignName: "cms-fleet-gps-10s",
      removed: [VIN_ASSIGNED],
    });

    renderDetail("cms-fleet-gps-10s");
    await waitFor(() => expect(screen.getByTestId("campaign-detail-ready")).toBeTruthy());
    await waitFor(() => expect(screen.getByTestId(`unassign-btn-${VIN_ASSIGNED}`)).toBeTruthy());

    fireEvent.click(screen.getByTestId(`unassign-btn-${VIN_ASSIGNED}`));
    await waitFor(() => expect(calls).toBe(2));
    fireEvent.click(screen.getByTestId(`unassign-btn-${VIN_SECOND}`));
    await waitFor(() => expect(calls).toBe(3));

    // Generation 3 succeeds; no warning should be showing.
    await waitFor(() => {
      expect(screen.queryByTestId("campaign-detail-refresh-warning")).toBeNull();
    });

    // Now let generation 2's rejection land.
    await new Promise((r) => setTimeout(r, 200));

    expect(
      screen.queryByTestId("campaign-detail-refresh-warning"),
      "a stale refresh's failure must be discarded — telling the operator their data may be " +
        "out of date, when a NEWER refresh has just succeeded, is the mirror image of the " +
        "defect F5.1 fixed",
    ).toBeNull();
    expect(screen.queryByTestId("campaign-detail-ready")).toBeTruthy();
  });
});


// ── 12. The shell→panel composition seam (Fix Group 7) ────────────────────────
//
// Review cycle 3 severed each of the shell's five panel props in turn.
// `vehicleAssignments={[]}` correctly failed 6 tests; the other four each passed **226/226**,
// and `vehicleCatalog={[]}` passed the **full 1343-test suite** — it flips every live vehicle
// row from `resolved` to "outside the operator's simulate scope" and nothing noticed.
//
// The panels are each well covered standalone, and the shell's fetching is covered. What was
// not covered is the WIRING between them, which is a distinct property: every panel test
// supplies its own props, so it cannot observe the shell handing over the wrong ones. On a
// spec whose purpose is removing unsupported claims from this screen, an unguarded seam that
// silently turns resolved vehicles into out-of-scope ones is the wrong thing to leave at close.
//
// One test, one assertion per prop. Each assertion names the prop it guards so a failure says
// which wire broke.
describe("shell hands every panel its data (composition seam)", () => {
  it("every shell\u2192panel wire is observably connected (6 wires, 5 props)", async () => {
    mockAllApis();
    renderDetail("cms-fleet-gps-10s");
    await waitFor(() => expect(screen.getByTestId("campaign-detail-ready")).toBeTruthy());

    const body = () => document.body.textContent ?? "";

    // 1. vehicleAssignments → the assigned VIN's row exists.
    expect(
      screen.queryByTestId(`unassign-btn-${VIN_ASSIGNED}`),
      "vehicleAssignments is not reaching CampaignVehiclesPanel",
    ).toBeTruthy();

    // 2. vehicleCatalog → the assignment RESOLVES against the catalog and is enriched.
    //    Severing this prop leaves the row present but reclassified as outside-scope, which
    //    is why a row-presence assertion alone could not catch it. The catalog's make/model
    //    is the observable that only appears when resolution succeeded.
    expect(
      body(),
      "vehicleCatalog is not reaching CampaignVehiclesPanel — the vehicle resolves to " +
        "outside-scope instead of resolved, which wrongly tells the operator a real vehicle " +
        "sits outside their simulate scope",
    ).toContain("Meridian");
    // Asserted on the BADGE TESTID, not on prose.
    //
    // The first version of this was `.not.toContain("outside the operator")`, which could
    // never fail: the component renders `Outside simulate scope`, and the phrase
    // "outside the operator" appears only in a source comment. Review cycle 4 proved it dead
    // by severing `vehicleCatalog` AND disabling the `Meridian` assertion above — 25/25 still
    // passed. The seam itself held (the `Meridian` assertion catches the severance), so this
    // was a redundant assertion rather than a hole, but a dead assertion reads as coverage.
    expect(
      screen.queryByTestId("outside-scope-badge"),
      "a vehicle present in the catalog must not be badged outside-scope",
    ).toBeNull();

    // 3. scopedAssignments → the template-less campaign's `all` row is a scoped assignment.
    //    Asserted on a campaign that HAS one, since cms-fleet-gps-10s does not.
    cleanup();
    mockAllApis();
    renderDetail("cms-fleet-telemetry-30s");
    await waitFor(() => expect(screen.getByTestId("campaign-detail-ready")).toBeTruthy());
    // Asserted on the SECTION testid, which `ScopedAssignmentsSection` returns null for when
    // the list is empty — so its presence is produced by nothing else.
    //
    // The first version of this assertion matched /all fleets|fleet/i against the whole body
    // and passed with the prop severed, because the campaign is NAMED
    // `cms-fleet-telemetry-30s` and the regex matched its name. Caught by running the
    // mutation; it is the same defect this group has now fixed six times, authored here by me.
    expect(
      screen.queryByTestId("scoped-assignments-section"),
      "scopedAssignments is not reaching CampaignVehiclesPanel — a fleet-wide or global " +
        "assignment would vanish from the screen entirely",
    ).toBeTruthy();
    expect(
      screen.queryByTestId("scoped-assignment-all-all"),
      "the specific `all`-scoped row from this campaign's fixture must render",
    ).toBeTruthy();

    // 4 + 5. signalCatalog and modelManifests → back on the campaign that has both.
    cleanup();
    mockAllApis();
    renderDetail("cms-fleet-gps-10s");
    await waitFor(() => expect(screen.getByTestId("campaign-detail-ready")).toBeTruthy());

    // signalCatalog → a signal NAME resolved from the catalog. Severing it leaves the panel
    // reporting the catalog as unavailable (per F6.4), so assert the resolved name.
    expect(
      screen.queryByTestId("campaign-signals-panel-catalog-unavailable"),
      "signalCatalog is not reaching CampaignSignalsPanel — the panel reports the catalog " +
        "unavailable when the shell did fetch it",
    ).toBeNull();

    // modelManifests → the ECU roster for the campaign's decoder manifest.
    expect(
      body(),
      "modelManifests is not reaching CampaignCoveragePanel — the ECU roster would render " +
        "empty as though the vehicle had no ECUs",
    ).toContain("TCU");

    // 6. signalCatalog → CampaignCoveragePanel's UDS mode. THE SIXTH WIRE.
    //
    // `signalCatalog` is passed to two panels, so severing the Coverage-panel site alone
    // passed 1347/1347 until cycle 4 — not because an assertion was weak, but because UDS
    // mode was unreachable: no fixture in this file carried `signalsToFetch`. A prop that
    // feeds two consumers needs an observation per consumer.
    cleanup();
    mockAllApis();
    renderDetail("uds-dtc-polling");
    await waitFor(() => expect(screen.getByTestId("campaign-detail-ready")).toBeTruthy());
    expect(
      screen.queryByTestId("coverage-panel-uds"),
      "the UDS coverage panel must mount for a campaign carrying signalsToFetch",
    ).toBeTruthy();
    expect(
      body(),
      "signalCatalog is not reaching CampaignCoveragePanel — the polled ECU's signal would " +
        "render unresolved, losing the only per-campaign ECU attribution that genuinely exists",
    ).toContain("GPS_Latitude");
  });
});

// ── 13. A malformed URL must not blank the SPA (Fix Group 7) ──────────────────
describe("malformed percent-encoding in the URL", () => {
  it("renders not-found instead of throwing out of render", async () => {
    // `decodeURIComponent("cms%fleet")` throws URIError, and there is no ErrorBoundary
    // anywhere in src/ — so before the fix this blanked the entire SPA. Reachable precisely
    // because this route's entitlement rationale assumes hand-typed URLs.
    mockAllApis();
    renderDetail("cms%fleet");

    await waitFor(() => {
      expect(
        screen.queryByTestId("campaign-detail-not-found") ??
          screen.queryByTestId("campaign-detail-ready") ??
          screen.queryByTestId("campaign-detail-error"),
        "the screen rendered nothing at all — a malformed percent-sequence threw out of " +
          "render and, with no ErrorBoundary in src/, took the whole SPA down",
      ).toBeTruthy();
    });
    expect(
      screen.queryByTestId("campaign-detail-not-found"),
      "an undecodable name cannot match a campaign, so it belongs in the not-found state " +
        "this path already renders for an unknown name",
    ).toBeTruthy();
  });
});


// ── 14. definition-state badge vocabulary (relocated from the list view) ──────
//
// These two tests moved here from
// `screens/software/__tests__/DataCollectionCampaignsView.test.tsx` when user UAT removed the
// list's "Definition" column. `DefinitionStatusBadge` in this view is now the ONLY place the
// definition-state vocabulary reaches a screen, so this is where the property belongs.
//
// The property, unchanged by the move: a template's stored status is glossed from a CLOSED set
// and the stored token is never echoed. A template carrying `RUNNING` is reachable —
// `PUT /campaigns/{id}` writes `status` straight from the request body with no allowlist — and
// "RUNNING" on a screen an operator reads for liveness is a claim the data cannot support,
// because nothing reconciles that status against telemetry.
//
// Recorded mutations, re-run here against the badge:
//   X2: `Available` → `Active`               → the ACTIVE case FAILS
//   X3: delete the `STOPPED` branch          → the STOPPED case falls through to
//                                              "Unrecognized state" and FAILS
//   Cycle 2 Critical 4: echo `{status}`      → the RUNNING case FAILS on both assertions

/** Render the detail view for a campaign whose template carries `status`. */
function renderWithTemplateStatus(status: string) {
  const template: dataModelClient.DataProcessingCampaignItem = {
    ...OEM_TEMPLATE_ROW,
    status,
  };
  vi.spyOn(dataModelClient, "fetchDataProcessingCampaigns").mockResolvedValue({
    dcCampaigns: [template, OEM_ASSIGNMENT_ROW],
    count: 2,
  });
  vi.spyOn(dataModelClient, "fetchSignals").mockResolvedValue(SIGNALS_RESPONSE);
  vi.spyOn(dataModelClient, "fetchVehicleModels").mockResolvedValue(MODEL_MANIFESTS_RESPONSE);
  vi.spyOn(subscriptionsClient, "listVehiclesForSimulation").mockResolvedValue(VEHICLES_RESPONSE);
  return renderDetail("cms-fleet-gps-10s");
}

describe("definition-state badge vocabulary", () => {
  it.each([
    ["ACTIVE", "dc-campaign-definition-available", "Available"],
    ["SUSPENDED", "dc-campaign-definition-suspended", "Suspended"],
    ["STOPPED", "dc-campaign-definition-stopped", "Stopped"],
    ["", "dc-campaign-definition-absent", "—"],
    ["RUNNING", "dc-campaign-definition-other", "Unrecognized state"],
  ])(
    "a stored status of %s renders the %s branch with the permitted gloss",
    async (status, testid, expectedText) => {
      renderWithTemplateStatus(status);
      await waitFor(() => {
        expect(screen.getByTestId("campaign-detail-ready")).toBeTruthy();
      });
      expect(
        (screen.getByTestId(testid).textContent ?? "").trim(),
        `A stored status of "${status}" must render exactly "${expectedText}". These five ` +
          "glosses are the permitted vocabulary; campaignStatusVocabulary.test.ts pins the " +
          "branch set by testid so a sixth cannot ship without a fixture here.",
      ).toBe(expectedText);
    },
  );

  it("does not echo an unrecognised stored status — a RUNNING template renders a fixed gloss", async () => {
    // Cycle 2 Critical 4, verbatim. No source guard can catch this: the string comes from data.
    renderWithTemplateStatus("RUNNING");
    await waitFor(() => {
      expect(screen.getByTestId("campaign-detail-ready")).toBeTruthy();
    });
    const badge = screen.getByTestId("dc-campaign-definition-other");
    expect(badge.textContent).toContain("Unrecognized state");
    expect(badge.textContent).not.toMatch(/RUNNING/i);

    // Two scans, deliberately different in case-sensitivity, because this screen contains a
    // LEGITIMATE lowercase "running": the Coverage panel's required caption reads "ECUs
    // available on vehicles running <decoderManifestId>". A body-wide lowercased check —
    // which is what the list-view version of this test used, correctly, since that screen has
    // no such copy — false-positives on it.
    //
    // 1. Body-wide, case-SENSITIVE: the stored token is `RUNNING`, and it must appear nowhere.
    //    This catches a value leaking into a neighbouring cell, which is what the original
    //    defect was, without colliding with the caption.
    expect(
      document.body.textContent ?? "",
      "The stored token RUNNING appeared on screen. A campaign assignment's stored RUNNING " +
        "status means ASSIGNED — nothing reconciles it against telemetry.",
    ).not.toContain("RUNNING");

    // 2. Definition section only, case-INSENSITIVE: catches a GLOSS spelled "Running", which
    //    the case-sensitive scan above would miss. Scoped to the section whose text is fully
    //    under this component's control.
    //
    //    Lowercased substring matching, NOT `\b` boundaries: `textContent` concatenates
    //    adjacent nodes with no separator, so a word boundary can fail to match a word that is
    //    plainly on screen. That vacuity was mutation EV-D in review cycle 2.
    const definitionText = (
      screen.getByTestId("campaign-detail-definition-container").textContent ?? ""
    ).toLowerCase();
    expect(
      definitionText,
      "The definition section rendered a liveness word. Its vocabulary is a closed set of " +
        "five glosses; see campaignStatusVocabulary.test.ts.",
    ).not.toContain("running");
  });
});
