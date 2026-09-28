// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Wording for "which campaigns will run on this vehicle" on the Simulate page.
 *
 * A separate module so it is unit-testable without rendering `SimulateVehicleView`
 * — that component's import chain is heavy, and the same extraction was forced on
 * the trip-detail helpers for the same reason.
 *
 * The screen previously showed only `no_telemetry_campaign`, which told an operator
 * something was missing but never what would run if it were not.
 *
 * ## Plural, because FleetWise is plural
 *
 * A first version of this module assumed one campaign per vehicle and named it.
 * That is false: FleetWise runs every matching campaign concurrently. On staging 8
 * targetArns carry more than one RUNNING campaign and `vehicle:MRDN0000000000015`
 * carries four — so the singular version reported "1 signal" for a vehicle
 * collecting 295.
 *
 * ## The total is given, never derived
 *
 * `telemetry_signal_total` is the DISTINCT union computed server-side. Do not sum
 * `signalCount` across campaigns to replace it: ids repeat both within one campaign
 * (`cms-fleet-gps-10s` is 293 entries / 286 distinct) and across campaigns, so
 * those four campaigns sum to 304 entries while covering 295 distinct signals.
 * `CampaignSignalsPanel` documents the same obligation one level down.
 *
 * ## Why `TelemetryCampaign*` and not `Campaign*`
 *
 * `contentBoundary.test.ts` Suite 3 bans bare `Campaign*` identifiers in this
 * module, because it also hosts a **software**-campaign (OTA update) feature and
 * CMS's `main_api` uses bare `campaignId` / `CAMPAIGNS_TABLE_NAME` for FleetWise
 * data-collection campaigns. The guard's stated remedy is `SoftwareCampaign*` —
 * which would be wrong here: these ARE the data-collection campaigns, so that
 * prefix would put a false claim in the identifier. `TelemetryCampaign*` satisfies
 * the guard's purpose (the two kinds are unambiguous) without misnaming the thing,
 * and matches the wire field `telemetry_campaigns` exactly. Do not shorten it.
 */

import type { SimulationVehicleEntry } from "../../../api/subscriptionsClient";

export type TelemetryCampaignTone = "info" | "warning" | "neutral";

/**
 * Tone → Cloudscape `StatusIndicator` type.
 *
 * Lives here rather than in the view so it is covered by this module's tests.
 * Deliberately not `error`: none of these states is a failure of the platform —
 * "no campaign" is a configuration gap the operator can close, and "unknown" is a
 * degraded read, so neither warrants the red that operators are trained to
 * escalate on.
 */
export const TELEMETRY_CAMPAIGN_TONE_STATUS: Record<
  TelemetryCampaignTone,
  "success" | "warning" | "info"
> = {
  info: "success",
  warning: "warning",
  neutral: "info",
};

export interface TelemetryCampaignSummary {
  /** Headline the operator reads first. */
  readonly headline: string;
  /** Supporting line; null when there is nothing honest to add. */
  readonly detail: string | null;
  readonly tone: TelemetryCampaignTone;
  /**
   * True when there is per-campaign detail worth opening a modal for. False in
   * every state where the modal would be empty or meaningless, so the view does not
   * have to re-derive the condition and cannot disagree with this module about it.
   */
  readonly hasDetail: boolean;
}

/** How a campaign came to cover this vehicle, in operator language. */
const SCOPE_WORDING: Record<string, string> = {
  vehicle: "assigned to this vehicle",
  fleet: "assigned to this vehicle's fleet",
  broadcast: "a fleet-wide campaign",
};

type TelemetryCampaignList = NonNullable<SimulationVehicleEntry["telemetry_campaigns"]>;

/** `N signal`/`N signals`, or null when the count is absent (NOT when it is 0). */
function signalPhrase(count: number | null | undefined): string | null {
  if (typeof count !== "number") return null;
  return `${count} signal${count === 1 ? "" : "s"}`;
}

/** Name a campaign, or null — never the string "null". */
function campaignLabel(c: TelemetryCampaignList[number]): string | null {
  return c.campaignName || c.campaignId || null;
}

/**
 * Summarise campaign coverage for the selected vehicle.
 *
 * Five states, deliberately distinct:
 *
 *  1. **Indeterminate** (`telemetry_campaigns` absent) — the readiness scan failed.
 *     Says so, rather than reporting "none", which would be a false claim.
 *  2. **Not applicable** (`telemetry_campaign_applicable === false`) — a
 *     cloud-telemetry vehicle needs no campaign. Reported as fine, not missing.
 *  3. **One campaign** — names it and how it resolved.
 *  4. **Several campaigns** — reports how many and the distinct signal total,
 *     because the individual names do not fit a one-line headline.
 *  5. **None** — the actionable case, and the reason the vehicle is unselectable.
 */
export function summariseTelemetryCampaign(
  vehicle: SimulationVehicleEntry | null | undefined,
): TelemetryCampaignSummary {
  if (!vehicle) {
    return {
      headline: "No vehicle selected",
      detail: null,
      tone: "neutral",
      hasDetail: false,
    };
  }

  // Absent — NOT empty. Readiness could not be determined, so make no claim.
  if (!("telemetry_campaigns" in vehicle)) {
    return {
      headline: "Campaign coverage unknown",
      detail:
        "The readiness check could not be completed, so coverage is unconfirmed — " +
        "this is not a report that the vehicle has no campaigns.",
      tone: "neutral",
      hasDetail: false,
    };
  }

  const covering: TelemetryCampaignList = vehicle.telemetry_campaigns ?? [];
  const total = vehicle.telemetry_signal_total;

  if (vehicle.telemetry_campaign_applicable === false) {
    // A campaign row can still target a cloud-telemetry vehicle's VIN, and two on
    // staging do (VEH-CS-DEMO-0001 and -0031, both `cms-fleet-gps-10s`). It is
    // inert: no onboard agent runs it, so promoting it to the headline would
    // promise collection that will not happen. Saying nothing is the other error —
    // an assignment that does nothing is a data-hygiene finding, and this is the
    // only screen positioned to show it. So: headline stays "not required", and
    // the inert assignments are named in the detail.
    const names = covering.map(campaignLabel).filter(Boolean);
    return {
      headline: "No campaign required",
      detail:
        "This vehicle sends cloud telemetry, which reaches the platform via the " +
        "rule path rather than an onboard FleetWise campaign." +
        (covering.length
          ? ` ${covering.length === 1 ? "A campaign" : `${covering.length} campaigns`}` +
            `${names.length ? ` (${names.join(", ")})` : ""} ` +
            `${covering.length === 1 ? "is" : "are"} assigned to it, but will not ` +
            "collect on this transport."
          : ""),
      tone: "info",
      // The modal is still worth opening: an operator looking at an inert
      // assignment wants to see what it *would* have collected.
      hasDetail: covering.length > 0,
    };
  }

  if (covering.length === 0) {
    return {
      headline: "No campaign will collect",
      detail:
        "No RUNNING campaign covers this vehicle, so a simulated trip would " +
        "publish signals that are immediately discarded. Assign a campaign before " +
        "running.",
      tone: "warning",
      hasDetail: false,
    };
  }

  if (covering.length === 1) {
    const only = covering[0];
    const named = campaignLabel(only);
    const how = SCOPE_WORDING[only.scope] ?? only.target;
    const bits: string[] = [];
    // Prefer the server's distinct union over this campaign's entry count: for a
    // single campaign they can still differ (293 entries, 286 distinct).
    const phrase =
      signalPhrase(typeof total === "number" ? total : only.signalCount);
    if (phrase) bits.push(phrase);
    if (!named) {
      // Be explicit about the gap rather than silently omitting the name.
      bits.push("campaign name unavailable from this endpoint");
    }
    return {
      headline: named ? `${named} — ${how}` : `Covered by ${how}`,
      detail: bits.length ? bits.join(" · ") : null,
      tone: "info",
      hasDetail: true,
    };
  }

  // Several. Naming four campaigns in a headline is unreadable, and the operator's
  // question at this level is "how much is being collected" — the per-campaign
  // breakdown is what the modal is for.
  const phrase = signalPhrase(total);
  const scopes = new Set(covering.map((c) => c.scope));
  return {
    headline:
      `${covering.length} campaigns will collect` +
      (phrase ? ` — ${phrase}` : ""),
    detail:
      covering
        .map((c) => campaignLabel(c) ?? SCOPE_WORDING[c.scope] ?? c.target)
        .join(", ") +
      // Say so when coverage is inherited as well as direct: an operator who
      // removes the per-vehicle assignment will still be collecting via the fleet.
      (scopes.size > 1
        ? ` (${[...scopes].map((s) => SCOPE_WORDING[s] ?? s).join("; ")})`
        : ""),
    tone: "info",
    hasDetail: true,
  };
}
