// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for the Simulate page's per-signal campaign breakdown modal.
 *
 * The signal grouping and the entries-vs-distinct reconciliation belong to
 * `CampaignSignalsPanel` and are tested with it. What is tested here is everything
 * this modal adds: the fetch-on-open split, the four load states kept distinct, the
 * readiness-entry-to-full-record match, and the two cases where a naive
 * implementation would silently show nothing.
 *
 * Mutation-verified: see the block comment at the foot of the file.
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchDataProcessingCampaigns: vi.fn(),
  fetchSignals: vi.fn(),
}));

vi.mock("../../../../api/dataModelClient", async (importOriginal) => {
  const actual = (await importOriginal()) as typeof import("../../../../api/dataModelClient");
  return {
    ...actual,
    fetchDataProcessingCampaigns: mocks.fetchDataProcessingCampaigns,
    fetchSignals: mocks.fetchSignals,
  };
});

import type { SimulationVehicleEntry } from "../../../../api/subscriptionsClient";
import TelemetryCampaignDetailModal from "../TelemetryCampaignDetailModal";

const SIGNAL_CATALOG = [
  { signal_id: 1, signal_name: "Vehicle.Speed", signal_group: "Powertrain", data_type: "DOUBLE", vss_path: "Vehicle.Speed" },
  { signal_id: 2, signal_name: "Vehicle.Odometer", signal_group: "Powertrain", data_type: "DOUBLE", vss_path: "Vehicle.Odometer" },
  { signal_id: 901, signal_name: "Vehicle.DTC", signal_group: "Diagnostics", data_type: "STRING", vss_path: "Vehicle.DTC" },
];

function vehicleWith(
  entries: NonNullable<SimulationVehicleEntry["telemetry_campaigns"]>,
  signalTotal: number | null = 3,
  overrides: Partial<SimulationVehicleEntry> = {},
): SimulationVehicleEntry {
  return {
    vehicleId: "VEH-MRDN-0015",
    vin: "MRDN0000000000015",
    dataSource: "vehicle-telemetry",
    simulation_ready: true,
    not_ready_reasons: [],
    telemetry_campaigns: entries,
    telemetry_signal_total: signalTotal,
    telemetry_campaign_applicable: true,
    ...overrides,
  } as SimulationVehicleEntry;
}

const GPS = {
  scope: "vehicle" as const,
  target: "vehicle:MRDN0000000000015",
  campaignId: "c-gps",
  campaignName: "cms-fleet-gps-10s",
  signalCount: 2,
};
const DTC = {
  scope: "fleet" as const,
  target: "fleet:flt-1",
  campaignId: "c-dtc",
  campaignName: "uds-dtc-polling",
  signalCount: 1,
};

function loaded() {
  mocks.fetchDataProcessingCampaigns.mockResolvedValue({
    dcCampaigns: [
      { campaignId: "c-gps", campaignName: "cms-fleet-gps-10s", targetArn: "vehicle:MRDN0000000000015", status: "RUNNING", createdAt: "2026-01-01", signalsToCollect: [1, 2] },
      { campaignId: "c-dtc", campaignName: "uds-dtc-polling", targetArn: "fleet:flt-1", status: "RUNNING", createdAt: "2026-01-01", signalsToCollect: [901] },
    ],
    count: 2,
  });
  mocks.fetchSignals.mockResolvedValue({ signals: SIGNAL_CATALOG, count: 3 });
}

beforeEach(() => {
  vi.clearAllMocks();
  loaded();
});

afterEach(() => {
  cleanup();
});

describe("TelemetryCampaignDetailModal", () => {
  it("does not fetch while closed", async () => {
    render(
      <TelemetryCampaignDetailModal visible={false} onDismiss={() => {}} vehicle={vehicleWith([GPS])} />,
    );
    // An unopened modal must cost nothing: the campaigns + signals endpoints are
    // the two heaviest reads on this screen.
    expect(mocks.fetchDataProcessingCampaigns).not.toHaveBeenCalled();
    expect(mocks.fetchSignals).not.toHaveBeenCalled();
  });

  it("fetches both the campaign records and the signal catalog on open", async () => {
    render(
      <TelemetryCampaignDetailModal visible onDismiss={() => {}} vehicle={vehicleWith([GPS])} />,
    );
    await waitFor(() => {
      expect(mocks.fetchDataProcessingCampaigns).toHaveBeenCalled();
      expect(mocks.fetchSignals).toHaveBeenCalled();
    });
  });

  it("reports the distinct total from the server, not a sum of entry counts", async () => {
    // The live case scaled down: two campaigns of 2 and 1 entries whose union is 2
    // because one id is shared. A sum would say 3.
    render(
      <TelemetryCampaignDetailModal visible onDismiss={() => {}} vehicle={vehicleWith([GPS, DTC], 2)} />,
    );
    const summary = await screen.findByTestId("simulate-vehicle-campaign-modal-summary");
    expect(summary.textContent).toContain("2 campaigns");
    expect(summary.textContent).toContain("2 distinct signals");
    expect(summary.textContent).not.toContain("3 distinct");
  });

  it("renders one expandable section per covering campaign", async () => {
    render(
      <TelemetryCampaignDetailModal visible onDismiss={() => {}} vehicle={vehicleWith([GPS, DTC], 3)} />,
    );
    expect(await screen.findByTestId("simulate-vehicle-campaign-modal-section-0")).toBeTruthy();
    expect(screen.getByTestId("simulate-vehicle-campaign-modal-section-1")).toBeTruthy();
    expect(screen.queryByTestId("simulate-vehicle-campaign-modal-section-2")).toBeNull();
  });

  it("labels each section with the campaign name and how it resolved", async () => {
    render(
      <TelemetryCampaignDetailModal visible onDismiss={() => {}} vehicle={vehicleWith([GPS, DTC], 3)} />,
    );
    const first = await screen.findByTestId("simulate-vehicle-campaign-modal-section-0");
    expect(first.textContent).toContain("cms-fleet-gps-10s");
    expect(first.textContent).toContain("assigned to this vehicle");
    const second = screen.getByTestId("simulate-vehicle-campaign-modal-section-1");
    expect(second.textContent).toContain("assigned to this vehicle's fleet");
  });

  it("explains an unmatched campaign rather than rendering an empty section", async () => {
    // A readiness entry whose record is absent from the campaigns endpoint. Showing
    // nothing would read as "this campaign collects nothing", which is a different
    // and false claim.
    render(
      <TelemetryCampaignDetailModal
        visible
        onDismiss={() => {}}
        vehicle={vehicleWith([{ ...GPS, campaignId: "c-missing", campaignName: "ghost" }], 0)}
      />,
    );
    const note = await screen.findByTestId("simulate-vehicle-campaign-modal-unmatched-0");
    expect(note.textContent).toContain("c-missing");
    expect(note.textContent).toContain("not returned by the");
    // It still covers the vehicle — say so, with the count we do have.
    expect(note.textContent).toContain("still covers this vehicle");
  });

  it("explains a coverage entry that carries no identity at all", async () => {
    // Set-shaped coverage: the scope is known, the campaign is not, so there is
    // nothing to look up. Must not be reported as a missing record.
    render(
      <TelemetryCampaignDetailModal
        visible
        onDismiss={() => {}}
        vehicle={vehicleWith([{ ...GPS, campaignId: null, campaignName: null }], 0)}
      />,
    );
    const note = await screen.findByTestId("simulate-vehicle-campaign-modal-unmatched-0");
    expect(note.textContent).toContain("carried no campaign identity");
    expect(note.textContent).not.toContain("null");
  });

  it("reports an unconfigured data-processing API as information, not an error", async () => {
    // `fetchSignals` returning null means the endpoint is absent from runtimeConfig.
    // That is a deployment fact. Rendering it red would send an operator chasing a
    // fault that does not exist.
    mocks.fetchSignals.mockResolvedValue(null);
    render(
      <TelemetryCampaignDetailModal visible onDismiss={() => {}} vehicle={vehicleWith([GPS])} />,
    );
    expect(
      await screen.findByTestId("simulate-vehicle-campaign-modal-unconfigured"),
    ).toBeTruthy();
    expect(screen.queryByTestId("simulate-vehicle-campaign-modal-error")).toBeNull();
    // The counts came from readiness and are unaffected — say so rather than
    // leaving the operator unsure whether the whole panel is untrustworthy.
    expect(
      screen.getByTestId("simulate-vehicle-campaign-modal-summary").textContent,
    ).toContain("1 campaign");
  });

  it("treats an unconfigured campaigns endpoint the same way", async () => {
    mocks.fetchDataProcessingCampaigns.mockResolvedValue(null);
    render(
      <TelemetryCampaignDetailModal visible onDismiss={() => {}} vehicle={vehicleWith([GPS])} />,
    );
    expect(
      await screen.findByTestId("simulate-vehicle-campaign-modal-unconfigured"),
    ).toBeTruthy();
  });

  it("surfaces a fetch failure as an error, distinctly from unconfigured", async () => {
    mocks.fetchSignals.mockRejectedValue(new Error("signals returned 502"));
    render(
      <TelemetryCampaignDetailModal visible onDismiss={() => {}} vehicle={vehicleWith([GPS])} />,
    );
    const err = await screen.findByTestId("simulate-vehicle-campaign-modal-error");
    expect(err.textContent).toContain("signals returned 502");
    expect(screen.queryByTestId("simulate-vehicle-campaign-modal-unconfigured")).toBeNull();
  });

  it("warns that campaigns are inert on a cloud-telemetry vehicle", async () => {
    render(
      <TelemetryCampaignDetailModal
        visible
        onDismiss={() => {}}
        vehicle={vehicleWith([GPS], 2, {
          dataSource: "cloud-telemetry",
          telemetry_campaign_applicable: false,
        })}
      />,
    );
    const alert = await screen.findByTestId("simulate-vehicle-campaign-modal-inert");
    expect(alert.textContent).toContain("will not collect");
    expect(alert.textContent).toContain("rule path");
  });

  it("does not warn about inertness on an onboard vehicle", async () => {
    render(
      <TelemetryCampaignDetailModal visible onDismiss={() => {}} vehicle={vehicleWith([GPS], 2)} />,
    );
    await screen.findByTestId("simulate-vehicle-campaign-modal-summary");
    expect(screen.queryByTestId("simulate-vehicle-campaign-modal-inert")).toBeNull();
  });

  it("states plainly when nothing covers the vehicle", async () => {
    render(
      <TelemetryCampaignDetailModal visible onDismiss={() => {}} vehicle={vehicleWith([], 0)} />,
    );
    const summary = await screen.findByTestId("simulate-vehicle-campaign-modal-summary");
    expect(summary.textContent).toContain("No RUNNING campaign covers this vehicle");
  });
});

/**
 * Mutations run against this suite (all CAUGHT):
 *
 *  1. Drop the `if (!visible) return` fetch guard — caught by "does not fetch while
 *     closed".
 *  2. `!campaignsResp || !signalsResp` → `!campaignsResp` only, so a null signals
 *     response falls through to `loaded` with an undefined catalog. Caught by
 *     "reports an unconfigured data-processing API".
 *  3. Route the not-configured case to the `error` state. Caught by the same test's
 *     `queryByTestId(...error)).toBeNull()`.
 *  4. Render nothing instead of the unmatched note. Caught by "explains an unmatched
 *     campaign".
 *  5. Collapse the null-identity branch into the missing-record branch. Caught by
 *     "explains a coverage entry that carries no identity".
 *  6. Sum `signalCount` instead of using `telemetry_signal_total`. Caught by
 *     "reports the distinct total from the server".
 *  7. Show the inert alert unconditionally. Caught by "does not warn about
 *     inertness on an onboard vehicle".
 */
