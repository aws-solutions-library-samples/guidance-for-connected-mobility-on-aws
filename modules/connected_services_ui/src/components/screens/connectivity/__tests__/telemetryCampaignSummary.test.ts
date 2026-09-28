// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for the Simulate page's campaign-coverage wording.
 *
 * Two load-bearing properties, both of which a reader is likely to break by
 * simplifying:
 *
 * 1. **An absent `telemetry_campaigns` is not the same as `[]`.** The backend omits
 *    the field entirely when the readiness scan failed, and returns `[]` when it
 *    succeeded and found no coverage. Collapsing the two turns "we could not check"
 *    into "there is no campaign" — a false claim, and one an operator would act on
 *    by assigning a campaign that may already exist.
 *
 * 2. **The signal total is the server's distinct union, never a sum of
 *    `signalCount`.** Ids repeat within one campaign (`cms-fleet-gps-10s` is 293
 *    entries / 286 distinct) and across campaigns, so the four campaigns on
 *    `vehicle:MRDN0000000000015` sum to 304 entries while covering 295 distinct
 *    signals. Summing here would overstate collection and contradict
 *    `CampaignSignalsPanel` one level down.
 *
 * This file was rewritten when the field went plural. Its first version asserted a
 * single campaign, which was wrong about FleetWise: every matching campaign runs.
 *
 * Mutation-verified: see the block comment at the foot of the file.
 */

import { describe, expect, it } from "vitest";

import type { SimulationVehicleEntry } from "../../../../api/subscriptionsClient";
import {
  TELEMETRY_CAMPAIGN_TONE_STATUS,
  summariseTelemetryCampaign,
} from "../telemetryCampaignSummary";

type TelemetryCampaignList = NonNullable<SimulationVehicleEntry["telemetry_campaigns"]>;

/** A vehicle with readiness known and no campaign field set at all. */
function indeterminate(): SimulationVehicleEntry {
  // Deliberately built without the key, not with `undefined` — `"k" in obj` is
  // the discriminator and an explicit `undefined` would satisfy it.
  return {
    vehicleId: "VEH-MRDN-0015",
    vin: "MRDN0000000000015",
    dataSource: "vehicle-telemetry",
    simulation_ready: null,
    not_ready_reasons: ["readiness_unavailable"],
  } as SimulationVehicleEntry;
}

function withCampaigns(
  telemetryCampaigns: TelemetryCampaignList,
  signalTotal: number | null = null,
  overrides: Partial<SimulationVehicleEntry> = {},
): SimulationVehicleEntry {
  return {
    vehicleId: "VEH-MRDN-0015",
    vin: "MRDN0000000000015",
    dataSource: "vehicle-telemetry",
    simulation_ready: true,
    not_ready_reasons: [],
    telemetry_campaigns: telemetryCampaigns,
    telemetry_signal_total: signalTotal,
    telemetry_campaign_applicable: true,
    ...overrides,
  } as SimulationVehicleEntry;
}

const VEHICLE_SCOPED: TelemetryCampaignList[number] = {
  scope: "vehicle",
  target: "vehicle:MRDN0000000000015",
  campaignId: "camp-0015",
  campaignName: "Meridian baseline",
  signalCount: 12,
};

describe("summariseTelemetryCampaign", () => {
  it("says nothing when no vehicle is selected", () => {
    const s = summariseTelemetryCampaign(null);
    expect(s.headline).toBe("No vehicle selected");
    expect(s.detail).toBeNull();
    expect(s.tone).toBe("neutral");
    expect(s.hasDetail).toBe(false);
  });

  it("treats undefined like null", () => {
    expect(summariseTelemetryCampaign(undefined).headline).toBe("No vehicle selected");
  });

  it("reports coverage unknown — not 'none' — when the field is absent", () => {
    const s = summariseTelemetryCampaign(indeterminate());
    expect(s.headline).toBe("Campaign coverage unknown");
    expect(s.tone).toBe("neutral");
    // The wording must actively disclaim the stronger reading, because the
    // adjacent `no_telemetry_campaign` copy says the opposite thing.
    expect(s.detail).toContain("not a report that the vehicle has no campaigns");
    // Nothing to open: there is no campaign list to break down.
    expect(s.hasDetail).toBe(false);
  });

  it("reports no campaign required for a cloud-telemetry vehicle", () => {
    const s = summariseTelemetryCampaign(
      withCampaigns([], 0, {
        dataSource: "cloud-telemetry",
        telemetry_campaign_applicable: false,
      }),
    );
    expect(s.headline).toBe("No campaign required");
    expect(s.tone).toBe("info");
    expect(s.detail).toContain("cloud telemetry");
    // No campaign assigned, so nothing to disclose about one.
    expect(s.detail).not.toContain("will not collect");
    expect(s.hasDetail).toBe(false);
  });

  it("discloses inert campaigns on a cloud-telemetry vehicle without promising collection", () => {
    // Real staging shape: VEH-CS-DEMO-0001 is cloud-telemetry AND carries a
    // `vehicle:<vin>` campaign. Naming it in the headline would promise collection
    // that cannot happen on this transport; omitting it entirely would hide a
    // misassignment that only this screen is positioned to show.
    const s = summariseTelemetryCampaign(
      withCampaigns(
        [{ ...VEHICLE_SCOPED, campaignName: "cms-fleet-gps-10s" }],
        293,
        { dataSource: "cloud-telemetry", telemetry_campaign_applicable: false },
      ),
    );
    expect(s.headline).toBe("No campaign required");
    expect(s.detail).toContain("cms-fleet-gps-10s");
    expect(s.detail).toContain("will not collect");
    // The campaign must NOT be presented as the thing that will run.
    expect(s.headline).not.toContain("cms-fleet-gps-10s");
    expect(s.tone).toBe("info");
    // Still worth opening — an operator wants to see what it WOULD have collected.
    expect(s.hasDetail).toBe(true);
  });

  it("pluralises the inert disclosure correctly for several campaigns", () => {
    const s = summariseTelemetryCampaign(
      withCampaigns(
        [
          { ...VEHICLE_SCOPED, campaignId: "a", campaignName: "one" },
          { ...VEHICLE_SCOPED, campaignId: "b", campaignName: "two" },
        ],
        5,
        { dataSource: "cloud-telemetry", telemetry_campaign_applicable: false },
      ),
    );
    expect(s.detail).toContain("2 campaigns");
    expect(s.detail).toContain("are assigned");
    expect(s.detail).not.toContain("is assigned");
  });

  // ── One campaign ───────────────────────────────────────────────────────────

  it("names the campaign and how it resolved for a per-vehicle assignment", () => {
    const s = summariseTelemetryCampaign(withCampaigns([VEHICLE_SCOPED], 12));
    expect(s.headline).toBe("Meridian baseline — assigned to this vehicle");
    expect(s.detail).toBe("12 signals");
    expect(s.tone).toBe("info");
    expect(s.hasDetail).toBe(true);
  });

  it("prefers the server's distinct total over a single campaign's entry count", () => {
    // 293 entries / 286 distinct for cms-fleet-gps-10s. Reporting the entry count
    // here and the distinct count in the modal would look like a contradiction.
    const s = summariseTelemetryCampaign(
      withCampaigns([{ ...VEHICLE_SCOPED, signalCount: 293 }], 286),
    );
    expect(s.detail).toBe("286 signals");
  });

  it("distinguishes fleet scope from vehicle scope in the wording", () => {
    const s = summariseTelemetryCampaign(
      withCampaigns(
        [{ ...VEHICLE_SCOPED, scope: "fleet", target: "fleet:flt-1", campaignName: "Fleet sweep" }],
        3,
      ),
    );
    expect(s.headline).toBe("Fleet sweep — assigned to this vehicle's fleet");
  });

  it("describes broadcast coverage as fleet-wide", () => {
    const s = summariseTelemetryCampaign(
      withCampaigns(
        [{ ...VEHICLE_SCOPED, scope: "broadcast", target: "all", campaignName: "Everything" }],
        1,
      ),
    );
    expect(s.headline).toBe("Everything — a fleet-wide campaign");
    // Singular, not "1 signals".
    expect(s.detail).toBe("1 signal");
  });

  it("falls back to campaignId when the name is missing", () => {
    const s = summariseTelemetryCampaign(
      withCampaigns([{ ...VEHICLE_SCOPED, campaignName: null }], 2),
    );
    expect(s.headline).toBe("camp-0015 — assigned to this vehicle");
  });

  it("reports the scope without inventing an identity when both are missing", () => {
    const s = summariseTelemetryCampaign(
      withCampaigns([{ ...VEHICLE_SCOPED, campaignId: null, campaignName: null }], 0),
    );
    expect(s.headline).toBe("Covered by assigned to this vehicle");
    expect(s.detail).toContain("campaign name unavailable from this endpoint");
    // Never the literal string "null" — the failure mode of interpolating an
    // absent name directly.
    expect(s.headline).not.toContain("null");
  });

  it("omits the signal count rather than rendering a placeholder", () => {
    const s = summariseTelemetryCampaign(
      withCampaigns([{ ...VEHICLE_SCOPED, signalCount: null }], null),
    );
    expect(s.detail).toBeNull();
  });

  it("reports a zero signal count rather than treating it as absent", () => {
    // 0 is falsy; a `if (count)` guard would silently drop a real finding — a
    // RUNNING campaign that collects nothing is exactly what an operator needs
    // to be told about.
    const s = summariseTelemetryCampaign(withCampaigns([VEHICLE_SCOPED], 0));
    expect(s.detail).toBe("0 signals");
  });

  // ── Several campaigns ──────────────────────────────────────────────────────

  it("reports the count and the distinct total when several campaigns cover", () => {
    // The live case: 4 campaigns, 304 entries, 295 DISTINCT. The headline must
    // carry 295.
    const s = summariseTelemetryCampaign(
      withCampaigns(
        [
          { ...VEHICLE_SCOPED, campaignId: "c1", campaignName: "cms-fleet-gps-10s", signalCount: 293 },
          { ...VEHICLE_SCOPED, campaignId: "c2", campaignName: "uds-dtc-polling", signalCount: 9 },
          { ...VEHICLE_SCOPED, campaignId: "c3", campaignName: "uds-dtc-a", signalCount: 1 },
          { ...VEHICLE_SCOPED, campaignId: "c4", campaignName: "uds-dtc-b", signalCount: 1 },
        ],
        295,
      ),
    );
    expect(s.headline).toBe("4 campaigns will collect — 295 signals");
    // The entry sum must NOT appear: it overstates what the vehicle collects.
    expect(s.headline).not.toContain("304");
    expect(s.detail).toContain("cms-fleet-gps-10s");
    expect(s.detail).toContain("uds-dtc-polling");
    expect(s.hasDetail).toBe(true);
  });

  it("names the scopes when coverage is both direct and inherited", () => {
    // An operator who removes the per-vehicle assignment is still collecting via
    // the fleet. Reporting only a count would hide that.
    const s = summariseTelemetryCampaign(
      withCampaigns(
        [
          { ...VEHICLE_SCOPED, campaignId: "c1", campaignName: "direct" },
          { ...VEHICLE_SCOPED, scope: "fleet", target: "fleet:f", campaignId: "c2", campaignName: "inherited" },
        ],
        20,
      ),
    );
    expect(s.detail).toContain("assigned to this vehicle");
    expect(s.detail).toContain("assigned to this vehicle's fleet");
  });

  it("does not add a scope parenthetical when every campaign shares one scope", () => {
    const s = summariseTelemetryCampaign(
      withCampaigns(
        [
          { ...VEHICLE_SCOPED, campaignId: "c1", campaignName: "one" },
          { ...VEHICLE_SCOPED, campaignId: "c2", campaignName: "two" },
        ],
        20,
      ),
    );
    expect(s.detail).toBe("one, two");
  });

  it("omits the total from the headline when the server did not supply one", () => {
    const s = summariseTelemetryCampaign(
      withCampaigns(
        [
          { ...VEHICLE_SCOPED, campaignId: "c1", campaignName: "one" },
          { ...VEHICLE_SCOPED, campaignId: "c2", campaignName: "two" },
        ],
        null,
      ),
    );
    expect(s.headline).toBe("2 campaigns will collect");
  });

  // ── No coverage ────────────────────────────────────────────────────────────

  it("warns, actionably, when a campaign is applicable and none covers", () => {
    const s = summariseTelemetryCampaign(withCampaigns([], 0));
    expect(s.headline).toBe("No campaign will collect");
    expect(s.tone).toBe("warning");
    expect(s.detail).toContain("Assign a campaign");
    expect(s.hasDetail).toBe(false);
  });

  it("does not read an absent applicable flag as 'not required'", () => {
    // Only `applicable === false` means "none is needed". An absent flag is a
    // server that resolved coverage but did not say whether it mattered, and
    // treating that as "not required" would suppress the one actionable state on
    // this panel. `!applicable` would; `=== false` does not.
    const vehicle = withCampaigns([], 0);
    delete (vehicle as { telemetry_campaign_applicable?: boolean })
      .telemetry_campaign_applicable;
    const s = summariseTelemetryCampaign(vehicle);
    expect(s.headline).toBe("No campaign will collect");
    expect(s.tone).toBe("warning");
  });
});

describe("TELEMETRY_CAMPAIGN_TONE_STATUS", () => {
  it("maps every tone", () => {
    expect(Object.keys(TELEMETRY_CAMPAIGN_TONE_STATUS).sort()).toEqual([
      "info",
      "neutral",
      "warning",
    ]);
  });

  it("never renders any state as an error", () => {
    // None of these states is a platform failure. `error` is red and operators
    // are trained to escalate on it; a configuration gap is not an escalation.
    expect(Object.values(TELEMETRY_CAMPAIGN_TONE_STATUS)).not.toContain("error");
  });

  it("gives the actionable state the warning treatment", () => {
    expect(TELEMETRY_CAMPAIGN_TONE_STATUS.warning).toBe("warning");
    expect(TELEMETRY_CAMPAIGN_TONE_STATUS.neutral).toBe("info");
  });
});

/**
 * Mutations run against this suite (all CAUGHT — see the commit message for the
 * per-mutation failure counts):
 *
 *  1. `!("telemetry_campaigns" in vehicle)` → `campaigns.length === 0` — collapses
 *     absent into empty. Caught by "reports coverage unknown".
 *  2. Delete the `telemetry_campaign_applicable === false` branch. Caught by
 *     "reports no campaign required".
 *  3. `applicable === false` → `!applicable`. Caught by "does not read an absent
 *     applicable flag".
 *  4. `SCOPE_WORDING.fleet` → the vehicle wording. Caught by "distinguishes fleet
 *     scope".
 *  5. `campaignName || campaignId` → drop the `|| campaignId`. Caught by "falls back
 *     to campaignId".
 *  6. `typeof count !== "number"` → `!count`. Caught by "reports a zero signal
 *     count".
 *  7. `tone: "warning"` → `"info"` on the no-coverage branch. Caught by "warns,
 *     actionably".
 *  8. Drop the inert-campaign disclosure. Caught by "discloses inert campaigns".
 *  9. Name the inert campaigns in the HEADLINE — the opposite error, promising
 *     collection on a transport that cannot deliver it. Caught by the same test's
 *     `headline).not.toContain` assertion.
 * 10. Several-campaign headline uses `sum(signalCount)` instead of the server total.
 *     Caught by "reports the count and the distinct total" via `not.toContain("304")`.
 * 11. `campaigns.length === 1` → `>= 1`, so a multi-campaign vehicle reports only
 *     its first. Caught by "reports the count and the distinct total".
 * 12. `hasDetail: false` → `true` on the no-coverage branch. Caught by "warns,
 *     actionably".
 */
