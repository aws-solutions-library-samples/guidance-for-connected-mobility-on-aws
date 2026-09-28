// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * SimulateVehicleView — single-vehicle simulation control panel.
 *
 * Spec: `.kiro/specs/2026-09-12-cs-simulator-oem2-manifest-path/tasks.md` T7.7
 * Spec: `.kiro/specs/2026-09-20-trip-intent-param-contract/tasks.md` T5.2
 *
 * ## What this view does
 *
 * Provides build / start / stop controls and operator-visible status
 * (running, last message published, message count) for the OEM2 Meridian EV
 * simulation. Reuses the existing simulation generation logic via
 * `simulationClient.ts` — no reimplementation.
 *
 * ## Trip parameters (T5.2, T5.4)
 *
 * City, Route Length, Safety Events, and Maintenance Events render inline
 * above the single Start button. Safety/Maintenance selectors were omitted in
 * T5.2 while Tier B was unread; T4.2/T4.3 gave both scenario keys real readers
 * and emptied `_DEFERRED_READERS`, so they now ship.
 *
 * ### Catalog state handling (T5.4 Accept item 4)
 *
 * Three catalog states are distinguished — collapsing them all into empty
 * option lists (`TripSimulatorModal.tsx`'s behaviour) tells the operator
 * the catalog is empty when it is actually unreachable:
 *
 * - `fetchEventCatalog()` returns `null` → endpoint not configured;
 *   selectors are disabled with a reason naming the missing config.
 * - `fetchEventCatalog()` throws → HTTP/auth/network failure;
 *   selectors are disabled with the error surfaced.
 * - returns `{events: [], count: 0}` → catalog genuinely empty;
 *   "No events in catalog" placeholder is correct.
 *
 * `commercial`-category events (2 of the 82 rows live 2026-09-21) belong to
 * neither selector and are dropped. `diagnostics.dtc.*` IDs carry
 * `safety`/`maintenance` categories, so they DO appear.
 *
 * `trips` is sent as the named constant TRIP_FIXED_TRIPS (1). See item 3a
 * of T5.2 for the recorded reasoning: this screen is the successor of
 * TripSimulatorModal for parameterised starts, and 3 trips × 20 points is
 * ~13 minutes before a run ends — hostile to the parameter-tuning loop the
 * screen now exists for. TripSimulatorModal hardcoded 1; plain Start (before
 * T5.2) sent nothing and got the server's default of 3. One button cannot do
 * both; 1 was chosen because this screen is the modal's successor.
 *
 * ## Vehicle selection
 *
 * The picker lists Meridian vehicles from the subscriptions plane. Unready
 * vehicles (T5.0) render disabled with a human-readable reason so the operator
 * knows the prerequisite and the gap stays visible. Absent readiness fields
 * are treated as ready for backward compatibility (version-skew tolerance).
 *
 * `vehicle_id` — not the VIN — identifies the vehicle in every request. See
 * issues/2026-09-05-vehicleid-diverges-from-vin.
 *
 * ## Single-vehicle only
 *
 * Fleet simulation is a Won't-Have for this release.
 *
 * ## Simulated label
 *
 * Simulated vehicles are labelled "simulated" wherever surfaced (T7.7 Accept).
 *
 * ## Routing is decided server-side (T6.2)
 *
 * This component sends NO `rule_name`, `mode`, or transport parameter. An earlier
 * revision read `runtimeConfig.simulationProductRuleName` and put it in the
 * `POST /start` body, which let the browser assert a routing destination; T6.2
 * removed that. `POST /simulate/start` on the subscriptions plane authorizes the
 * caller and derives BOTH simulation axes from the vehicle's `dataSource`.
 *
 * Stop and status still run against the simulation API — only start moved.
 *
 * The `!v.dataSource` picker gate remains as UX; the server-side check is
 * authoritative, since a direct API caller bypasses anything this component does.
 *
 * ## settleMarker
 *
 * The string "cs-settle-simulate-vehicle-control-panel" must appear in this
 * component's rendered body. Required by registryCompleteness.test.tsx P1.
 */

import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import Multiselect from "@cloudscape-design/components/multiselect";
import Select from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import React, { useCallback, useEffect, useRef, useState } from "react";

import {
  getAgentStatus,
  getSimulationApiBase,
  getSimulationStatus,
  startAgent,
  stopAgent,
  stopSimulation,
} from "../../../api/simulationClient";
import type { SimulationStatus } from "../../../api/simulationClient";
import FWELogViewer from "./FWELogViewer";
import SimLogViewer from "./SimLogViewer";
import TelemetryCampaignDetailModal from "./TelemetryCampaignDetailModal";
import { TELEMETRY_CAMPAIGN_TONE_STATUS, summariseTelemetryCampaign } from "./telemetryCampaignSummary";
import {
  getSubscriptionsApiBase,
  listVehiclesForSimulation,
  SimulationStartError,
  startVehicleSimulation,
} from "../../../api/subscriptionsClient";
import type { SimulationVehicleEntry } from "../../../api/subscriptionsClient";
import {
  assignCampaignToVehicle,
  fetchDataProcessingCampaigns,
  fetchEventCatalog,
} from "../../../api/dataModelClient";
import type { EventCatalogItem, EventCatalogResponse } from "../../../api/dataModelClient";

// ── settleMarker ──────────────────────────────────────────────────────────────

/** Unique to this screen. Asserted by registryCompleteness.test.tsx P1. */
const SETTLE_MARKER = "cs-settle-simulate-vehicle-control-panel";

// ── Status polling interval ────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 5_000;

/**
 * Consecutive failed `/agent/status` probes before the simulator is declared
 * offline and both log panes are blacked out.
 *
 * 3 at a 10s poll = ~30s of sustained failure. Chosen because the failure this
 * guards against was a burst: on 2026-09-22 a trip start tripled request volume
 * against cms-{stage}-simulation-api (257 vs ~70 per 5 min, 77 rejected at the
 * gateway) and the next single probe blacked out both consoles mid-run with
 * "Simulator offline" while the simulator was demonstrably running.
 *
 * Not a tolerance-for-tolerance's-sake knob: a genuinely offline simulator still
 * reports within ~30s, and every failure is surfaced immediately via
 * `agentProbeError` regardless of the streak — the streak only gates the
 * pane-blanking, never the reporting.
 */
const AGENT_OFFLINE_AFTER_FAILURES = 3;

// ── Trip parameter defaults ───────────────────────────────────────────────────
//
// Sourced from `simulate_start_handler`'s `sim_config` in
// services/connectors/subscriptions/simulate_vehicle/handler.py (~:872-874 as of
// 2026-09-20). Cite the function name rather than a line number — lines drift when
// that file is edited, but the function name does not.
//
// TRIP_FIXED_TRIPS: TripSimulatorModal hardcodes 1; the server defaults to 3.
// One button cannot do both. Chose 1 because this screen is the modal's successor
// for parameterised starts, and 3 trips × 20 points is ~13 minutes before a run
// ends — hostile to the parameter-tuning loop the screen now exists for.
// See T5.2 item 3a for the full recorded reasoning.
const TRIP_DEFAULT_CITY = "seattle"; // sim_config default in simulate_start_handler
const TRIP_DEFAULT_ROUTE_LENGTH = 20; // sim_config default in simulate_start_handler
const TRIP_FIXED_TRIPS = 1;          // T5.2 item 3a: modal predecessor sent 1

// ── City picker options ───────────────────────────────────────────────────────
//
// Must exactly match `_ALLOWED_CITIES` in
// services/connectors/subscriptions/simulate_vehicle/handler.py
// and CITIES in TripSimulatorModal.tsx.
// Do NOT widen this set — the server's allowlist is the authority.
const CITY_OPTIONS = [
  { label: "Atlanta", value: "atlanta" },
  { label: "Chicago", value: "chicago" },
  { label: "Miami", value: "miami" },
  { label: "Munich", value: "munich" },
  { label: "New York", value: "nyc" },
  { label: "San Francisco", value: "sf" },
  { label: "Seattle", value: "seattle" },
];

// ── Readiness reason token → human wording ───────────────────────────────────
//
// Mapping lives in ONE place (spec T5.2 item 6). Token strings are stable
// identifiers from the server (T5.0); wording is owned by the UI.
// An unrecognised token falls back to the generic label rather than a blank
// or the raw token.
//
// `readiness_unavailable` is the FG13.T3 token: simulation_ready is null and
// at least one readiness scan failed. Rendered as an advisory, NOT a block —
// the server-side check is authoritative.
const READINESS_REASON_LABELS: Record<string, string> = {
  not_fleet_enrolled:    "not enrolled in the simulation fleet",
  no_certificate:        "no device certificate",
  no_telemetry_campaign: "no telemetry campaign",
  readiness_unavailable: "could not determine readiness",
};

const READINESS_REASON_FALLBACK = "not simulation-ready";

function readinessReasonLabel(token: string): string {
  return READINESS_REASON_LABELS[token] ?? READINESS_REASON_FALLBACK;
}

// ── Severity label for event catalog ─────────────────────────────────────────
//
// Inlined from TripSimulatorModal.tsx's own copy (same SYNC-BLOCK-START: severity-label
// pattern). Kept in sync with that file; if you change one, change the other.
// Numeric scale is REVERSE-RANKED: higher = worse (>= 4 → CRITICAL, 3 → HIGH,
// 2 → MEDIUM, <= 1 → LOW). Stringified numerics follow the same path.
function severityLabel(raw: number | string | null | undefined): string {
  if (raw === null || raw === undefined || raw === "") return "UNKNOWN";
  const asNum = typeof raw === "number" ? raw : Number(raw);
  if (!Number.isNaN(asNum)) {
    if (asNum >= 4) return "CRITICAL";
    if (asNum === 3) return "HIGH";
    if (asNum === 2) return "MEDIUM";
    if (asNum <= 1) return "LOW";
  }
  const s = String(raw).trim().toUpperCase();
  switch (s) {
    case "P0": case "CRITICAL": return "CRITICAL";
    case "P1": case "HIGH": return "HIGH";
    case "P2": case "MEDIUM": case "MED": return "MEDIUM";
    case "P3": case "LOW": return "LOW";
    default: return "UNKNOWN";
  }
}

interface EventOption {
  label: string;
  value: string;
  description?: string;
}

/**
 * Build safety/maintenance option lists from a real event-catalog response.
 *
 * Mirrors TripSimulatorModal.tsx's `optionsFromCatalog` behaviour:
 * - label is `description || event_id`, suffixed ` · {dtc_code}` when present
 * - description carries trigger signal, severity, and "| Creates DTC" when dtc_code is set
 * - sorted by label
 * - `commercial`-category events (2 of the 82 live rows 2026-09-21) are dropped here:
 *   they belong to neither selector. `diagnostics.dtc.*` IDs carry safety/maintenance
 *   categories, so they DO appear.
 * - `value` is `evt.event_id` — the dotted catalog ID (`safety.harsh_braking`,
 *   `diagnostics.dtc.P0128`). NEVER a bare derived name — see `decisions.md`
 *   § "T4.2 targets the wrong mechanism" for why the two namespaces do not intersect.
 *
 * Exported for testing: allows direct value-contract verification without opening
 * the Multiselect dropdown in JSDOM.
 */
export function optionsFromCatalog(
  data: EventCatalogResponse,
): { safety: EventOption[]; maintenance: EventOption[] } {
  const safety: EventOption[] = [];
  const maintenance: EventOption[] = [];
  for (const evt of data.events as readonly EventCatalogItem[]) {
    const baseLabel = evt.description || evt.event_id;
    const label = evt.dtc_code ? `${baseLabel} · ${evt.dtc_code}` : baseLabel;
    const option: EventOption = {
      label,
      value: evt.event_id,
      description: `Signal: ${evt.trigger_signal || "—"} | Severity: ${severityLabel(evt.severity ?? evt.severity_hint)}${evt.dtc_code ? " | Creates DTC" : ""}`,
    };
    if (evt.category === "safety") safety.push(option);
    else if (evt.category === "maintenance") maintenance.push(option);
    // commercial and any other category are deliberately dropped — see comment above
  }
  return {
    safety: safety.sort((a, b) => a.label.localeCompare(b.label)),
    maintenance: maintenance.sort((a, b) => a.label.localeCompare(b.label)),
  };
}

// ── Catalog fetch state ───────────────────────────────────────────────────────
//
// Three states are distinguished (T5.4 Accept item 4); TripSimulatorModal.tsx
// collapses all three into empty option lists — that is the defect class this
// spec exists to close.
//
// CATALOG_STATE_PENDING — initial state while the fetch is in flight.
// CATALOG_STATE_NOT_CONFIGURED — fetchEventCatalog() returned null.
// CATALOG_STATE_ERROR — fetchEventCatalog() threw (HTTP/auth/network failure).
// CATALOG_STATE_LOADED — fetch succeeded; options come from optionsFromCatalog().
type CatalogState =
  | { kind: "pending" }
  | { kind: "not_configured" }
  | { kind: "error"; message: string }
  | { kind: "loaded"; safety: EventOption[]; maintenance: EventOption[] };

const CATALOG_STATE_PENDING: CatalogState = { kind: "pending" };

// ── dataSource display helper ─────────────────────────────────────────────────

/**
 * Human-readable label for a vehicle's dataSource.
 *
 * Spec T6.1: the picker shows all vehicles regardless of producer; `dataSource`
 * is shown as a hint about the simulation path that will be used.
 */
/**
 * Human-readable label for a vehicle's dataSource.
 *
 * Spec T6.1: the picker shows all vehicles regardless of producer; `dataSource`
 * Exported for test: this string is a claim about which transport a simulation
 * uses, and its server-side twin (`_DISPATCH_DESCRIPTION`) was wrong for the
 * cloud arm through two review cycles. A test that asserts it via the rendered
 * DOM is vacuous unless a cloud-telemetry vehicle happens to be in the picker
 * AND the Select is open — which is how the first version of that test passed
 * against the reverted string. Asserting the function directly is exact.
 */
export function dataSourceLabel(dataSource: string | undefined): string {
  if (dataSource === "vehicle-telemetry") return "onboard (FWE → MQTT → Flink)";
  // NOT "producer feed → ingest API". That describes how a cloud-telemetry vehicle
  // is fed in PRODUCTION; what a simulation of one actually does is publish over
  // MQTT basic ingest to the CS product rule. The server-side twin of this string
  // (`_DISPATCH_DESCRIPTION` in simulate_vehicle/handler.py) carried the same wrong
  // claim and was corrected under review cycle 1's W2 — this is the UI mirror of
  // that finding, and the two must not drift apart again.
  if (dataSource === "cloud-telemetry") return "cloud (MQTT basic-ingest → CS product rule → MSK)";
  if (!dataSource) return "no dataSource — cannot simulate";
  return dataSource;
}

/**
 * Build the Cloudscape Select label + description for one vehicle in the
 * simulation picker.
 *
 * Label is `<year> <make> <model> — VIN <vin>`, e.g.
 * `2025 Meridian Azimuth — VIN CSPR0000000000021`.
 *
 * **`vehicleId` is deliberately NOT shown** (changed 2026-09-20, user: *"why are
 * we showing vehicleId in the drop down rather than just VIN and
 * make/model/year? vehicleid means nothing"*). It is correct that it means
 * nothing to an operator, and on a few legacy rows it means something actively
 * misleading: a handful of vehicles carry an id naming a DIFFERENT automaker than
 * every other field on the row — `make`, `model`, `fleetId` and
 * `modelManifestName` all say Meridian — left unreconciled by the customer
 * rebrand. A label asserting the wrong manufacturer on a Meridian-only portal is
 * worse than one that says nothing.
 *
 * (Those ids are not repeated here: they are brand canaries, and
 * `src/__tests__/contentBoundary.test.ts` blocks naming them in a screen file. It
 * caught this docstring when it first tried to.)
 *
 * `vehicleId` remains the option's `value` and the functional key for
 * `/simulate/start`; only the display drops it. If support ever needs it back,
 * add it to the description, not the label.
 *
 * `producer` is also no longer shown: the picker is Meridian-only as of Group 8,
 * so `producer: meridian` on all 100 rows was pure noise.
 *
 * History worth keeping, because the prior two revisions were both wrong in
 * instructive ways. The original showed `vehicleId` as the label with the VIN
 * buried in the description, which read as two unrelated identifiers per row
 * (`issues/2026-09-18-cs-simulate-no-campaign-gate-and-picker-labels/`). The fix
 * for that added the VIN to the label and **left it in the description too**, so
 * a diverging row stated both identifiers twice — reported by the user
 * 2026-09-20 as *"the drop down has VINs as the top line AND Vehicle_Ids as the
 * top line selector ... it's very confusing"*. Each identifier appears exactly
 * once now, and the one a human actually recognises leads.
 */
export function vehicleOptionLabel(v: SimulationVehicleEntry): {
  label: string;
  description: string;
} {
  // Extract before JSX/template use to avoid the ProvenanceValue render-path lint
  // (provenanceRender.test.ts flags `.vin` inside a JSX expression container even
  // when the field is a plain string, as it is here).
  const vehicleVin = v.vin;

  // year/make/model are all present on every Meridian row in staging, but the
  // label must still degrade sanely rather than rendering "undefined undefined".
  const identity = [v.year, v.make, v.model].filter(Boolean).join(" ").trim();

  let label: string;
  if (identity && vehicleVin) {
    label = `${identity} — VIN ${vehicleVin}`;
  } else if (identity) {
    // No VIN: fall back to the vehicleId so the row is still selectable and
    // distinguishable, and SAY it is the internal id rather than passing it off
    // as something meaningful.
    label = `${identity} — id ${v.vehicleId}`;
  } else if (vehicleVin) {
    label = `VIN ${vehicleVin}`;
  } else {
    // Neither identity nor VIN. Showing the raw key is the honest last resort.
    label = v.vehicleId;
  }

  const descParts: string[] = [];
  descParts.push(`path: ${dataSourceLabel(v.dataSource)}`);
  return { label, description: descParts.join(" · ") };
}

/**
 * Turn a start failure into something an operator can act on.
 *
 * `SimulationStartError` carries the server's `reason` token. Task 6.1 gave
 * `/simulate/start` a contract with four distinct actionable outcomes, and an HTTP
 * status line distinguishes none of them: `no_data_source` means the vehicle record
 * needs a dataSource, `no_telemetry_campaign` means a campaign must be assigned, and
 * 403 means the caller is not a CS operator. Anything unrecognised falls through to
 * the server's own message rather than being relabelled.
 */
function describeStartFailure(err: unknown): string {
  if (err instanceof SimulationStartError) {
    // Bound to a local before any interpolation. `${err.status}` inside a template
    // literal matches `provenanceRender.test.ts`'s `\{[^}]*\.status[}\s]` guard,
    // which exists to stop fixture-derived ProvenanceValue fields being rendered
    // outside `<ProvenanceField>`. This is an HTTP status, not a ProvenanceValue —
    // but the guard cannot tell, and a local read is cheaper than an allowlist
    // entry that would also exempt the next real violation on this line.
    const httpStatus = err.status;
    switch (err.reason) {
      case "no_data_source":
        return (
          "This vehicle has no dataSource, so there is no transport to simulate. " +
          "Set its dataSource to 'vehicle-telemetry' or 'cloud-telemetry' first."
        );
      case "no_telemetry_campaign":
        return (
          "The simulation was refused: this vehicle has no telemetry campaign, so it " +
          "would transmit nothing. Assign a campaign and retry."
        );
      case "simulation_invoke_failed":
      case "simulation_response_unparseable":
        return (
          `The simulation service did not accept the start request (${httpStatus}). ` +
          "Retry; if it persists, check the simulation Lambda's logs."
        );
      default:
        break;
    }
    if (err.reason?.startsWith("unknown_data_source")) {
      return (
        "This vehicle's dataSource is not a recognised transport, so the simulation " +
        "path cannot be derived. Correct the vehicle record."
      );
    }
    if (err.reason?.startsWith("not_simulatable_producer")) {
      // Group 8. Deliberately explains WHY rather than just refusing: the
      // vehicle looks simulatable in every other respect, so a bare refusal
      // reads as a bug. Only Meridian vehicles have a simulation path — an
      // oem1 or tesla vehicle would be fed by its own producer's ingest route,
      // which the simulator does not drive.
      return (
        "This vehicle was not produced by Meridian, so there is no simulation path " +
        "for it — CS can only simulate Meridian vehicles. Pick a Meridian vehicle, " +
        "or a real producer-specific simulation path would have to be built first."
      );
    }
    if (httpStatus === 403) {
      return "You are not authorized to start simulations. This requires the connected-services operator group.";
    }
    return err.message;
  }
  return err instanceof Error ? err.message : String(err);
}

// ── Vehicle readiness classification (exported for testing) ───────────────────
//
// FG13.T4 — W3: three simulation_ready states must remain distinct.
//
//   absent / undefined → ready (backward compat: version-skew for pre-T5.0 server)
//   false              → not ready; option should be disabled
//   null               → readiness could not be determined; option should NOT be disabled
//
// Do NOT collapse null into either neighbour.
export function vehicleReadinessState(
  simulationReady: boolean | null | undefined,
): "ready" | "not_ready" | "unknown" {
  if (simulationReady === null) return "unknown";
  if (simulationReady === false) return "not_ready";
  return "ready"; // true or absent
}

/**
 * Build the Cloudscape Select option shape for a single vehicle in the picker.
 *
 * Exported so tests can assert the `disabled` property directly, bypassing the
 * JSDOM limitation that Cloudscape Select dropdown options are not inspectable
 * without opening the dropdown. The comment at `:443-447` referenced this export;
 * this is the function that makes it true.
 *
 * The `vehicleOptions` computed value in the component delegates to this, so
 * asserting it here is exact — the same logic, without JSDOM opacity.
 */
export function buildVehicleOption(v: SimulationVehicleEntry): {
  value: string;
  label: string;
  description: string;
  disabled: boolean;
} {
  const { label, description } = vehicleOptionLabel(v);
  const readiness = vehicleReadinessState(v.simulation_ready);
  const isUnknown = readiness === "unknown";
  const isDisabled = !v.dataSource || readiness === "not_ready";

  let disabledReason = "";
  if (!v.dataSource) {
    disabledReason = "no dataSource — cannot simulate";
  } else if (readiness === "not_ready" && v.not_ready_reasons && v.not_ready_reasons.length > 0) {
    disabledReason = v.not_ready_reasons.map(readinessReasonLabel).join("; ");
  } else if (readiness === "not_ready") {
    disabledReason = READINESS_REASON_FALLBACK;
  }

  const unknownNotice = isUnknown ? " — could not determine readiness" : "";

  return {
    value: v.vehicleId,
    label: isDisabled && disabledReason
      ? `${label} — ${disabledReason}`
      : `${label}${unknownNotice}`,
    description,
    disabled: isDisabled,
  };
}

/**
 * Order the picker so selectable vehicles come first.
 *
 * The API returns vehicles in table order, which put most of the simulatable
 * ones below a long run of disabled entries — with ~99 options and many sharing
 * a label, an operator had to scroll past the unusable ones to reach anything
 * they could actually run. Reported directly: "can we have the active vehicles
 * at the top vs most of the active ones are at the bottom?"
 *
 * Primary key is `disabled`, not readiness, deliberately: `disabled` is what
 * actually gates selection in `buildVehicleOption` (no `dataSource` OR
 * `not_ready`), so sorting on it cannot disagree with what the picker permits.
 * Vehicles of UNKNOWN readiness are selectable and therefore sort with the
 * active group — showing them at the bottom would hide a vehicle that may well
 * run, which is the same complaint in the other direction.
 *
 * Secondary key is the label, so the order is stable and predictable rather than
 * inheriting scan order within each group.
 *
 * Returns a new array; callers derive this from `.map()` output and nothing
 * should depend on that intermediate being sorted in place.
 */
export function sortVehicleOptions<T extends { label: string; disabled: boolean }>(
  options: readonly T[],
): T[] {
  return [...options].sort((a, b) => {
    if (a.disabled !== b.disabled) return a.disabled ? 1 : -1;
    return a.label.localeCompare(b.label);
  });
}

// ── Component ──────────────────────────────────────────────────────────────────

type SimPhase = "idle" | "starting" | "running" | "stopping" | "error";

const SimulateVehicleView: React.FC = () => {
  // Vehicle picker state — loaded dynamically
  const [vehicles, setVehicles] = useState<readonly SimulationVehicleEntry[]>([]);
  const [vehiclesLoading, setVehiclesLoading] = useState(false);
  const [vehiclesError, setVehiclesError] = useState<string | null>(null);
  // T5.0: ready_count from the response (optional — absent on pre-T5.0 server)
  const [readyCount, setReadyCount] = useState<number | null>(null);

  const [selectedVehicleId, setSelectedVehicleId] = useState<string | null>(null);
  const [phase, setPhase] = useState<SimPhase>("idle");
  const [simulationId, setSimulationId] = useState<string | null>(null);
  const [status, setStatus] = useState<SimulationStatus | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  // Non-blocking assist: when a start is refused specifically for
  // `no_telemetry_campaign`, offer a quick "Assign a baseline campaign"
  // affordance right here rather than sending the operator to the Campaigns
  // tab (issues/2026-09-18-cs-simulate-no-campaign-gate-and-picker-labels/).
  // Cleared alongside errorMsg on any new attempt or vehicle change so it
  // never outlives the failure it was offered for.
  const [canQuickAssign, setCanQuickAssign] = useState(false);
  // Per-signal campaign breakdown, fetched on open by the modal itself — the
  // readiness response carries counts, not the ~293 ids per campaign.
  const [campaignModalVisible, setCampaignModalVisible] = useState(false);
  const [assigning, setAssigning] = useState(false);
  // Non-error notices — a successful start's advisory, or an unconfigured endpoint.
  // Separate from `errorMsg` because the error alert only renders on
  // `phase === "error"`, and neither of these is an error state (T6.2).
  const [noticeMsg, setNoticeMsg] = useState<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Trip parameter state (T5.2, T5.4) ─────────────────────────────────────
  //
  // Prefilled from server defaults (TRIP_DEFAULT_CITY / TRIP_DEFAULT_ROUTE_LENGTH).
  // See the named constants above for the citation and reasoning.
  const [tripCity, setTripCity] = useState(TRIP_DEFAULT_CITY);
  const [tripRouteLength, setTripRouteLength] = useState(String(TRIP_DEFAULT_ROUTE_LENGTH));
  // T5.4: Safety/Maintenance event selections (arrays of EventOption with value: event_id)
  const [selectedSafetyEvents, setSelectedSafetyEvents] = useState<EventOption[]>([]);
  const [selectedMaintenanceEvents, setSelectedMaintenanceEvents] = useState<EventOption[]>([]);
  // T5.4: Catalog fetch state — three states are distinguished, not collapsed.
  // "pending" while the fetch is in flight; "not_configured" when the endpoint is
  // absent; "error" on HTTP/auth/network failure; "loaded" with parsed options.
  const [catalogState, setCatalogState] = useState<CatalogState>(CATALOG_STATE_PENDING);

  // ── In-flight interlock ─────────────────────────────────────────────────────
  //
  // A synchronous latch, deliberately NOT React state. Read the cycle-4 entry in
  // decisions.md before changing this.
  //
  // Three cycles tried to serialize concurrent starts using `phase` (via the derived
  // `isBusy`). That cannot work: `isBusy` is computed during render and both the
  // buttons and the handler guards read the same value, so a handler guard can never
  // disagree with the button that dispatched it. Any concurrent async writer that
  // sets `phase` back to "idle" — the vehicle Select's onChange did it in cycle 2,
  // `handleQuickAssign` did it in cycle 4 — makes `isBusy` legitimately false, and
  // the guard does not fire because there is nothing left for it to detect.
  //
  // A ref is written and read synchronously, in the same tick as the dispatch and
  // before any `await`, so no interleaving can reorder it; and it is invisible to
  // render, so nothing that touches `phase` can clear it. `phase` goes back to
  // describing the UI, which is all it was ever able to do reliably.
  //
  // Scope: "a start CALL is in flight", not "a simulation is running". It is cleared
  // in `finally` when the call settles.
  //
  // It is NOT sufficient on its own, and the sentence that used to sit here -- that a
  // start while a simulation is already running is "still the buttons' job, via
  // `isBusy`" -- was refuted in review cycle 5: `isBusy` is only trustworthy while no
  // stale writer has clobbered `phase`. That is what `startGeneration` and `activeSimId`
  // below are for. Read all three together; no one of them carries the invariant.
  const startInFlight = useRef(false);

  // Monotonic counter, bumped every time a start is dispatched from either entry point.
  // Any async handler that resolves LATER and wants to write shared state captures this
  // at entry and re-checks it before writing: if it changed, a start happened in the
  // meantime and that start's state is authoritative.
  //
  // This exists because the latch above is not sufficient on its own. The latch covers
  // the window where a start CALL is outstanding. It does not stop a slower, unrelated
  // async handler from resolving after the call has settled and overwriting `phase` —
  // which is exactly what `handleQuickAssign` did (review cycle 5): start succeeds,
  // phase becomes "running", quick-assign's awaits then resolve and set "idle", and the
  // UI reports Idle with "Click Start to try again" while a simulation is transmitting
  // and Stop is disabled because `isRunning` is false. The operator was following
  // on-screen instructions, not losing a race.
  //
  // A generation check is the general form of the fix: stale async results do not get
  // to write.
  //
  // Read "any async handler", NOT "any NEW async handler". The original wording said
  // "new", and review cycle 6 identified that as the reason `pollStatus` -- an EXISTING
  // late-resolving writer -- went unguarded for a cycle. When adding this class of guard,
  // audit every existing writer as well; cycle 6 did that by enumerating all 13
  // `setPhase` sites, which is what finally bounded the problem.
  //
  // `pollStatus` uses `activeSimId` rather than this counter: it also writes `status` and
  // the poll timer, so it needs run identity, not dispatch count.
  const startGeneration = useRef(0);

  // The run `pollStatus` is currently allowed to speak for. Kept in a ref because
  // `pollStatus` captures its own `id` argument and would read a stale `simulationId`
  // from its closure.
  //
  // NOT a mirror of `simulationId`, and do not "fix" it into one. `simulationId` is the
  // run the UI displays; this is poll AUTHORITY, and the two legitimately diverge for
  // the duration of a stop: authority is revoked when a stop is initiated (see
  // `handleStop`), while `simulationId` is cleared only once the stop succeeds. Holding
  // them in lockstep through the await is precisely the bug review cycle 7 found.
  //
  // `clearInterval` does not cancel an already-outstanding promise, so a poll issued
  // for run N can resolve AFTER run N was stopped and run N+1 started. Without this
  // check that late poll writes `status`, tears down the NEW run's interval via
  // `stopPolling()`, and sets `phase` to "idle"/"error" -- reporting Idle over a
  // transmitting simulation and leaving it unmonitored. Review cycle 6 reproduced it
  // from the plain start -> stop -> start gesture; no error state required, because the
  // poll outstanding at Stop is the one most likely to return a terminal status.
  //
  // Same class as `startGeneration` (a stale async result must not write), but keyed on
  // run identity rather than dispatch count, because `pollStatus` also writes `status`
  // and the poll timer -- state a phase-only guard would not protect.
  const activeSimId = useRef<string | null>(null);

  // FWE agent running state — mirrors VehicleDetailView.tsx:520-535
  const [simReachable, setSimReachable] = useState(false);
  const [agentRunning, setAgentRunning] = useState(false);
  const [agentLoading, setAgentLoading] = useState(false);
  const [agentError, setAgentError] = useState<string | null>(null);
  const [agentProbeError, setAgentProbeError] = useState<string | null>(null);
  const agentPollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  // Consecutive failed agent-status probes. `simReachable` gates BOTH log panes,
  // and it used to be a single sample: one failed probe declared "Simulator
  // offline" in two places at once. That message was observed false on 2026-09-22
  // — the simulator was running (agent task live, trip intents being written)
  // while the UI claimed it was offline, and the Start controls disabled with it.
  //
  // A ref, not state: it must not trigger a render, and the poll closure needs the
  // current value rather than the one captured when the effect ran.
  const agentFailStreak = useRef(0);

  const apiConfigured = Boolean(getSimulationApiBase());
  const subscriptionsConfigured = Boolean(getSubscriptionsApiBase());

  // ── Load vehicles on mount ──────────────────────────────────────────────────

  useEffect(() => {
    if (!subscriptionsConfigured) return;
    setVehiclesLoading(true);
    void listVehiclesForSimulation()
      .then((result) => {
        if (result) {
          setVehicles(result.vehicles);
          // T5.0: capture ready_count if present
          if (typeof result.ready_count === "number") {
            setReadyCount(result.ready_count);
          }
          // Default-select the first vehicle if any
          if (result.vehicles.length > 0 && !selectedVehicleId) {
            setSelectedVehicleId(result.vehicles[0].vehicleId);
          }
        }
      })
      .catch((err: unknown) => {
        setVehiclesError(
          err instanceof Error ? err.message : "Failed to load vehicles",
        );
      })
      .finally(() => setVehiclesLoading(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subscriptionsConfigured]);

  // ── Event catalog fetch (T5.4) ─────────────────────────────────────────────
  //
  // Fetches once on mount. Three states are distinguished:
  //  - null return → endpoint not configured → "not_configured"
  //  - throw → HTTP/auth/network error → "error" with the message surfaced
  //  - {events: [], count: 0} → catalog genuinely empty → "loaded" with empty options
  //
  // Do NOT collapse null and throw into empty option lists — that tells the operator
  // the catalog is empty when it is actually unreachable, which is false.
  // `dataModelClient.ts`'s own docstring on `fetchSignals` states this rule explicitly.
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await fetchEventCatalog();
        if (cancelled) return;
        if (data === null) {
          // Endpoint not configured — distinct from the catalog being empty.
          setCatalogState({ kind: "not_configured" });
        } else {
          const { safety, maintenance } = optionsFromCatalog(data);
          setCatalogState({ kind: "loaded", safety, maintenance });
        }
      } catch (err) {
        if (cancelled) return;
        // HTTP/auth/network failure — distinct from the catalog being empty.
        // Surface the error message so the operator can diagnose the missing config.
        setCatalogState({
          kind: "error",
          message: err instanceof Error ? err.message : String(err),
        });
      }
    };
    void load();
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Agent status polling — mirrors VehicleDetailView.tsx:520-535 ───────────
  // When the simulation base is null (not configured), we never start polling
  // and keep simReachable=false so FWELogViewer receives simReachable=false,
  // which prevents polling with a null base (spec 7.1 Decision (b)).

  useEffect(() => {
    const base = getSimulationApiBase();
    if (!base || !selectedVehicleId) {
      setSimReachable(false);
      setAgentRunning(false);
      return;
    }

    // Find the selected vehicle's vin (needed for agent status matching).
    const selectedVehicle = vehicles.find((v) => v.vehicleId === selectedVehicleId);
    const vin = selectedVehicle?.vin ?? selectedVehicleId;

    const checkStatus = () => {
      void getAgentStatus(vin).then((result) => {
        if (result === null) {
          // base became null — a configuration fact, not a transient failure, so
          // it applies immediately with no streak tolerance.
          agentFailStreak.current = 0;
          setSimReachable(false);
          setAgentRunning(false);
          return;
        }

        if (result.reachable) {
          agentFailStreak.current = 0;
          setSimReachable(true);
          setAgentRunning(result.agentRunning);
          setAgentProbeError(null);
          return;
        }

        // Failed probe. Report WHY before deciding whether to declare offline —
        // 401/403 means this caller was refused, which is not the simulator being
        // down, and telling the operator "offline" sends them to debug the wrong
        // system.
        agentFailStreak.current += 1;
        const { httpStatus } = result;
        if (httpStatus === 401) {
          setAgentProbeError(
            "Your session has expired — agent status and logs are unavailable " +
              "until you reload and sign in again. The simulation itself is " +
              "unaffected and keeps running.",
          );
        } else if (httpStatus === 403) {
          setAgentProbeError(
            "Not authorized to read agent status for this vehicle (missing fleet " +
              "assignment). The simulation itself is unaffected.",
          );
        }

        // Only declare the simulator unreachable after repeated failures. A single
        // dropped or refused probe is not evidence of an offline simulator, and the
        // cost of getting this wrong is blacking out both log panes mid-run.
        if (agentFailStreak.current >= AGENT_OFFLINE_AFTER_FAILURES) {
          setSimReachable(false);
          setAgentRunning(false);
        }
      });
    };

    checkStatus();
    agentPollTimer.current = setInterval(checkStatus, 10_000);
    return () => {
      if (agentPollTimer.current !== null) {
        clearInterval(agentPollTimer.current);
        agentPollTimer.current = null;
      }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedVehicleId, vehicles]);

  // ── Vehicle Select options ──────────────────────────────────────────────────
  //
  // T5.2 item 6: unready vehicles are disabled with a reason label.
  // simulation_ready absent → treat as ready (backward compat / version skew).
  // Delegates to buildVehicleOption (exported for test assertions on disabled).

  const vehicleOptions = sortVehicleOptions(vehicles.map(buildVehicleOption));

  const selectedOption =
    vehicleOptions.find((o) => o.value === selectedVehicleId) ?? null;

  // ── Readiness indeterminate flag ────────────────────────────────────────────
  //
  // True when ANY vehicle in the current list has simulation_ready === null —
  // meaning the batch readiness scan failed and the count is unreliable.
  // FG13.T3 changed ready_count to EXCLUDE null vehicles, so ready_count: 0
  // with all-null vehicles is ambiguous between "none are ready" and "could not
  // determine" — this flag resolves that ambiguity explicitly (C3 fix).
  //
  // Derived from the vehicle list, not from ready_count, because ready_count
  // alone cannot carry the distinction.
  const isReadinessIndeterminate = vehicles.some(
    (v) => v.simulation_ready === null,
  );

  // ── Polling ─────────────────────────────────────────────────────────────────

  const stopPolling = useCallback(() => {
    if (pollTimer.current !== null) {
      clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  const pollStatus = useCallback(
    async (id: string) => {
      try {
        const s = await getSimulationStatus(id);
        // Identity check: this poll may have been issued for a run that has since been
        // stopped and replaced. A stale result must not write ANY shared state -- not
        // `status`, not the poll timer, not `phase`. See `activeSimId`'s declaration.
        if (activeSimId.current !== id) return;
        if (s) {
          setStatus(s);
          if (s.status === "completed" || s.status === "failed" || s.status === "stopped") {
            stopPolling();
            // Clear the advisory on every terminal transition — a notice describing
            // a running simulation is stale once it ends.  Without this, `noticeMsg`
            // outlives the run it described and reads as current advice (T6.2, review
            // cycle 3 follow-on).
            setNoticeMsg(null);
            if (s.status === "failed") {
              // Surface the server's reason — without an errorMsg the status indicator
              // reads "Error" with nothing on screen to explain it, while a stale
              // campaign-advisory may still be visible and reads as the cause (T6.2,
              // review cycle 3 follow-on; parallel to the `onChange` fix in Fix Group 21).
              setErrorMsg(s.error ?? "Simulation stopped with a failure status.");
              setPhase("error");
            } else {
              setPhase("idle");
            }
          }
        }
      } catch {
        // Transient poll failure — keep trying.
      }
    },
    [stopPolling],
  );

  useEffect(() => {
    return () => stopPolling();
  }, [stopPolling]);

  // ── Busy state ──────────────────────────────────────────────────────────────
  //
  // Declared HERE, above the start handlers, rather than down with the render
  // helpers, so the handler-level guards and the button `disabled`/`loading` props
  // derive from ONE predicate. When these lived below the handlers, the handlers
  // could not reference them and used a hand-rolled `phase !== "idle"` check
  // instead — which is how FG3.T1 shipped a retry-after-failure regression
  // (`phase === "error"` is not busy, but it is also not `"idle"`, so a legitimate
  // retry was silently swallowed by a button that rendered as enabled).
  //
  // `isBusy` includes "starting" so neither start affordance is live while a start
  // is in flight. It deliberately EXCLUDES "error" and "idle": both are states a
  // user must be able to start from.
  const isRunning = phase === "running" || phase === "stopping";
  const isBusy = isRunning || phase === "starting";

  // ── Onboard-agent affordances ───────────────────────────────────────────────
  //
  // Derived once here rather than recomputed inline per container, because three
  // surfaces need the same answer (the agent toggle, the FWE log container, and
  // the agent-status poll) and an inline `vehicles.find()` in each is three
  // chances to gate on a different field.
  //
  // The gate is `dataSource === "vehicle-telemetry"` — the field that actually
  // states whether the vehicle has an onboard telemetry unit. Do NOT switch this
  // to CMS's `!isOEM1`: that reads `oem_source`, which is absent on every
  // Meridian row, so CMS shows the button for offboard vehicles. That is an open
  // defect (`issues/2026-09-18-start-agent-button-shown-for-offboard-vehicles/`)
  // and mirroring it here would bill real ECS/ASG capacity for a vehicle with
  // nothing to receive it.
  //
  // Fail-closed: an absent or unrecognised `dataSource` hides the control rather
  // than showing it, matching the FWE log container's existing posture.
  const selectedVehicle = selectedVehicleId
    ? vehicles.find((v) => v.vehicleId === selectedVehicleId)
    : undefined;
  const selectedVin = selectedVehicle?.vin ?? selectedVehicleId ?? "";
  const isOnboardSelected = selectedVehicle?.dataSource === "vehicle-telemetry";

  // "Which campaigns will run on this vehicle" — the question the screen could not
  // answer before. It reported `no_telemetry_campaign` when nothing covered the
  // vehicle but was silent about what WOULD collect when something did, and
  // silence read as "nothing to say" rather than "covered".
  //
  // Resolved server-side (see `_annotate_vehicle_readiness`), not here: this entry
  // carries no `fleetId`, so a client cannot evaluate `fleet:<id>` coverage, and
  // this UI never calls `/campaigns` for coverage. A plain call per render — pure,
  // and it builds three short strings.
  const telemetryCampaignSummary = summariseTelemetryCampaign(selectedVehicle);

  const toggleAgent = useCallback(async () => {
    if (!selectedVehicleId || !selectedVin || !isOnboardSelected) return;
    setAgentLoading(true);
    try {
      const result = agentRunning
        ? await stopAgent(selectedVin, selectedVehicleId)
        : await startAgent(selectedVin, selectedVehicleId);
      if (result === null) {
        setAgentError("Simulation API is not configured for this environment.");
      } else if (result.ok) {
        // Optimistic, then corrected by the status poll — the agent takes
        // 1-2 minutes to pass its health check, so the poll is the source of
        // truth and this only makes the button responsive in the meantime.
        setAgentRunning(!agentRunning);
        setAgentError(null);
      } else {
        // Destructured rather than interpolated as `result.status` so the line
        // carries no `.status` dereference. `provenanceRender.test.ts` flags that
        // token by name without type information, and this is an HTTP status
        // number, not a ProvenanceValue. Avoiding the pattern is cheaper than
        // exempting the line, and reads better.
        const { status: httpStatus, detail } = result;
        setAgentError(
          `Agent ${agentRunning ? "stop" : "start"} failed` +
            (httpStatus ? ` (HTTP ${httpStatus})` : "") +
            (detail ? `: ${detail}` : ""),
        );
      }
    } finally {
      setAgentLoading(false);
    }
  }, [agentRunning, isOnboardSelected, selectedVehicleId, selectedVin]);

  // ── Start ───────────────────────────────────────────────────────────────────
  //
  // T5.2: single Start handler — merged from the old handleStart (no params) and
  // handleModalStart (tripParams + modal). Keeps handleStart's non-throwing
  // semantics (called from a button, not a modal). The `notConfigured`/re-throw
  // path from handleModalStart is gone with the modal.

  const handleStart = useCallback(async () => {
    if (!selectedVehicleId) return;
    // Synchronous in-flight interlock. See `startInFlight`'s declaration for why this
    // is a ref and not `isBusy` — three cycles of render-state guards were defeated by
    // concurrent writers resetting `phase`. Set before the first `await` so no
    // interleaving can slip between the check and the claim.
    if (startInFlight.current) return;
    startInFlight.current = true;
    startGeneration.current += 1;
    setPhase("starting");
    setErrorMsg(null);
    setNoticeMsg(null);
    setStatus(null);
    setCanQuickAssign(false);

    // Build trip params from inline state.
    // trips: always TRIP_FIXED_TRIPS (see constant declaration for reasoning).
    // safety_scenarios / maintenance_scenarios: omit entirely when empty (do not
    // send []). [] is the absent sentinel all the way down to the simulator
    // (simulation_lambda.py:824-830, T4.2 Accept 4), so sending it is harmless;
    // sending nothing is honest and preserves the "untouched controls behave
    // exactly as before" property T5.2 established.
    const safetyIds = selectedSafetyEvents.map((o) => o.value);
    const maintenanceIds = selectedMaintenanceEvents.map((o) => o.value);
    const tripParams = {
      city: tripCity,
      trips: TRIP_FIXED_TRIPS,
      route_length: Number(tripRouteLength) || TRIP_DEFAULT_ROUTE_LENGTH,
      ...(safetyIds.length > 0 ? { safety_scenarios: safetyIds } : {}),
      ...(maintenanceIds.length > 0 ? { maintenance_scenarios: maintenanceIds } : {}),
    };

    try {
      // T6.2: start goes through the subscriptions plane, not straight to the
      // simulation API. The server authorizes the caller (`connected-services`)
      // and derives BOTH simulation axes from the vehicle's dataSource, so no
      // rule_name is sent from the browser.
      const result = await startVehicleSimulation(selectedVehicleId, fetch, tripParams);
      if (!result) {
        // Subscriptions endpoint not configured — degrade honestly, and name the
        // right endpoint: stop/status still run against the simulation API, so
        // saying "simulation API" here would send the operator to the wrong knob.
        setPhase("idle");
        setNoticeMsg(
          "Subscriptions API endpoint is not configured — cannot start a simulation. Check runtime config.",
        );
        return;
      }
      const id = result.simulation_id;
      setSimulationId(id);
      activeSimId.current = id;
      setPhase("running");
      // A non-blocking advisory from the server (e.g. FWE agent started with no
      // campaign assigned). Surfaced rather than swallowed: without it a run that
      // will transmit nothing looks like an unqualified success.
      if (result.warning) setNoticeMsg(result.warning);

      // FG2.T1: clear any existing interval before starting a new one.
      // Without this, a second start (reachable before isBusy was fixed) would
      // orphan the first interval — it would keep polling a stale simulation id
      // past component unmount, because the unmount cleanup only sees whatever
      // pollTimer.current points at when it runs.
      stopPolling();
      // NOTE: deliberately NO immediate first poll here. Adding one makes the
      // Active Session panel populate ~5s sooner, and it was tried on 2026-09-22
      // and REVERTED: it broke five existing guards, including FG2.T1's
      // orphaned-timer and "exactly one poll per tick — no concurrent intervals"
      // invariants, which took seven review cycles to establish. A 5s cosmetic gain
      // does not justify editing five concurrency guards. The panel's fields were
      // the real defect (they read keys no backend returned) and are fixed
      // independently. If this is revisited, treat those five tests as the
      // specification, not as an obstacle.
      // Start polling for status.
      pollTimer.current = setInterval(() => {
        void pollStatus(id);
      }, POLL_INTERVAL_MS);
    } catch (err) {
      setPhase("error");
      setErrorMsg(describeStartFailure(err));
      // T5.2 item 4: no_telemetry_campaign recovery path is preserved.
      // With no modal to dismiss, the dismiss-then-throw branch collapses to
      // setting parent state directly — which is what this catch already does.
      if (err instanceof SimulationStartError && err.reason === "no_telemetry_campaign") {
        setCanQuickAssign(true);
      }
    } finally {
      // Release the interlock once the start CALL has settled, whatever the outcome.
      startInFlight.current = false;
    }
  }, [selectedVehicleId, tripCity, tripRouteLength, selectedSafetyEvents, selectedMaintenanceEvents, pollStatus, stopPolling]);

  // ── Quick campaign assignment ────────────────────────────────────────────────
  //
  // Assigns the existing baseline telemetry template (the same
  // `cms-fleet-gps-10s` row `_ensure_telemetry_campaign` uses server-side) to
  // the selected vehicle, without navigating away from this screen. Deliberately
  // does NOT create a new campaign — reusing the known-good baseline template
  // keeps this a one-click assist rather than a second campaign-authoring flow.
  // If the baseline template itself is missing (an operational gap, not this
  // operator's to fix from here), surfaces that plainly rather than inventing one.
  const handleQuickAssign = useCallback(async () => {
    if (!selectedVehicleId) return;
    // Snapshot the start generation: if a start is dispatched while our two network
    // calls are outstanding, we must not write `phase` or the retry notice afterwards.
    const gen = startGeneration.current;
    setAssigning(true);
    setErrorMsg(null);
    try {
      const campaigns = await fetchDataProcessingCampaigns();
      if (!campaigns) {
        setErrorMsg("Data-processing API is not configured — cannot assign a campaign.");
        return;
      }
      const template = campaigns.dcCampaigns.find(
        (c) => c.targetArn === "template" && c.campaignName === "cms-fleet-gps-10s",
      );
      if (!template) {
        setErrorMsg(
          "No baseline telemetry campaign template (cms-fleet-gps-10s) exists on this " +
          "stage — assign a campaign from the Data Collection Campaigns screen instead.",
        );
        return;
      }
      // Resolve the selected vehicle's VIN. `/campaigns/assign` keys the row
      // `vehicle:{vin}` and the telemetry-campaign check that produced the 409 reads
      // it BY VIN, so sending `selectedVehicleId` writes a row nothing can find —
      // silently, with a 200. That shipped; see
      // issues/2026-09-20-cs-quick-assign-writes-vehicleid-keyed-campaign-row/.
      //
      // Deliberately NO `?? selectedVehicleId` fallback. The identical-looking
      // fallback at the agent-status effect above is correct there (a best-effort
      // match), and copying it here is what made this defect silent. An absent VIN
      // must surface, because guessing writes a bad row. The two differ for most CS
      // demo vehicles and NOT only by prefix — VEH-MRDN-0011's VIN is
      // MRDN0000000000013 — so it must be read, never derived.
      const selectedVehicle = vehicles.find((v) => v.vehicleId === selectedVehicleId);
      const vin = selectedVehicle?.vin;
      if (!vin) {
        setErrorMsg(
          `No VIN on record for ${selectedVehicleId} — cannot assign a campaign. ` +
          "A campaign is keyed by VIN, so assigning without one would create a " +
          "record the simulator cannot see.",
        );
        return;
      }
      const result = await assignCampaignToVehicle(template.campaignName, vin);
      if (!result) {
        setErrorMsg("Data-processing API is not configured — cannot assign a campaign.");
        return;
      }
      // `rejected` is a real failure: the server refused to write because the value
      // did not resolve to a vehicle. Surface its reason rather than a generic retry.
      const rejected = result.rejected ?? [];
      if (rejected.length > 0) {
        setErrorMsg(`Campaign assignment refused: ${rejected[0].reason}`);
        return;
      }
      const alreadyAssigned = result.alreadyAssigned ?? [];
      // `assigned: []` WITH `alreadyAssigned` non-empty is idempotent SUCCESS — the
      // write is conditional and the row already existed. Treating it as a failure
      // (the previous behaviour) produced an error that could never clear, because on
      // every retry the row still exists. Only an empty-on-all-three response is
      // genuinely inconclusive.
      if (result.assigned.length === 0 && alreadyAssigned.length === 0) {
        setErrorMsg(
          "Campaign assignment returned no result — nothing was written and nothing " +
          "was refused. Check the data-processing API logs before retrying.",
        );
        return;
      }
      // A start dispatched while we were awaiting owns `phase` and the quick-assign
      // affordance; we must not overwrite either. But the assignment DID happen, and the
      // operator has to be told regardless -- review cycle 6 (N7) found that suppressing
      // the whole block leaves the screen saying "Assign a campaign and retry" with the
      // button already gone, for a campaign that is in fact assigned.
      //
      // So: always report the outcome, and only claim the phase when we still own it.
      // The "Click Start to try again" half is phase-flavoured advice, so it is only
      // appended when we actually returned the UI to idle.
      const superseded = startGeneration.current !== gen;
      setNoticeMsg(
        superseded
          ? `Assigned "${template.campaignName}" to ${selectedVehicleId}.`
          : `Assigned "${template.campaignName}" to ${selectedVehicleId}. Click Start to try again.`,
      );
      if (superseded) return;
      // Inside the guard: a newer start that failed with no_telemetry_campaign sets
      // canQuickAssign(true) itself, and clearing it here would remove the affordance
      // that start just asked for.
      setCanQuickAssign(false);
      setPhase("idle");
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setAssigning(false);
    }
    // `vehicles` is a dependency because the VIN is now read from it. Omitting it
    // would let the closure hold a stale list and resolve the wrong VIN — or none —
    // for a vehicle selected after the last render that rebuilt this callback.
  }, [selectedVehicleId, vehicles]);

  // ── Stop ────────────────────────────────────────────────────────────────────

  const handleStop = useCallback(async () => {
    if (!simulationId) return;
    setPhase("stopping");
    // Revoke poll authority BEFORE awaiting, and stop issuing new polls.
    //
    // Review cycle 7: `pollStatus`'s identity check only drops polls issued for a
    // REPLACED run. The poll that un-gates `"stopping"` is issued for the run being
    // stopped, so it still matched `activeSimId` and passed the check — then set phase
    // to "idle" mid-stop, and re-enabling Start. The post-await writes below would then
    // null out the NEW run's id, leaving it transmitting, unmonitored and unstoppable.
    // Measured: 2 concurrent runs, 0 polls in the following 5.3s, Active Session panel gone.
    //
    // Clearing `activeSimId` here makes cycle 6's safety argument true instead of
    // assumed: the un-gating poll now fails the identity check and writes nothing.
    stopPolling();
    activeSimId.current = null;
    try {
      await stopSimulation(simulationId);
      setPhase("idle");
      setSimulationId(null);
      setStatus(null);
      // Clear the advisory with the run it described (T6.2, review cycle 1 W4).
      // `noticeMsg` is not tied to `phase`, so without this a "no active campaign"
      // advisory outlives the simulation it was about and reads as current.
      setNoticeMsg(null);
    } catch (err) {
      // The stop FAILED, so the run is still live. The revocation above assumed it would
      // succeed, so two things have to be put back (review cycle 8, N10 + S27):
      //
      //  1. Poll authority and the interval. Measured after a failed stop at the previous
      //     commit: 0 polls in the following 5.4s, `message_count` frozen, and the status
      //     stuck at Error permanently -- even once the run ended server-side -- where the
      //     code before FG7 self-healed on the next poll.
      //  2. The phase. Parking in "error" disables Stop (it reads `isRunning`), so the
      //     failed stop cannot be retried, while leaving Start live -- which is how the run
      //     gets orphaned. Returning to "running" is simply true: the run is running, and
      //     `errorMsg` says the stop attempt failed.
      // Report through `noticeMsg`, NOT `errorMsg`.
      //
      // The error Alert is gated on `phase === "error"`, and we deliberately stay
      // "running" here -- so an `errorMsg` write renders nowhere. Review cycle 9 measured
      // exactly that: after a failed stop the DOM contained no mention of a stop failing,
      // making a failed Stop indistinguishable from a click that never registered, three
      // times over for three consecutive failures. The comment that used to sit here
      // claimed "errorMsg says the stop attempt failed", which was false.
      //
      // This is the same defect this file already documents beside the notice Alert below
      // in its "idle" form ("since the alert above only renders on phase === 'error' it
      // was never displayed to anyone"). `noticeMsg` renders at every phase, which is why
      // it exists; the phase-independence is the whole point of the channel.
      setNoticeMsg(
        `Stop request failed: ${err instanceof Error ? err.message : String(err)} — ` +
        `the simulation is still running. Click Stop to retry.`,
      );
      // Clear any older error rather than leaving invisible state behind to resurface
      // misattributed at the next phase change.
      setErrorMsg(null);
      setPhase("running");
      activeSimId.current = simulationId;
      stopPolling();
      pollTimer.current = setInterval(() => {
        void pollStatus(simulationId);
      }, POLL_INTERVAL_MS);
    }
  }, [simulationId, pollStatus, stopPolling]);

  // ── Status display helpers ──────────────────────────────────────────────────

  const statusIndicatorType =
    phase === "running"
      ? "success"
      : phase === "starting" || phase === "stopping"
        ? "in-progress"
        : phase === "error"
          ? "error"
          : "stopped";

  const statusLabel =
    phase === "running"
      ? "Running"
      : phase === "starting"
        ? "Starting…"
        : phase === "stopping"
          ? "Stopping…"
          : phase === "error"
            ? "Error"
            : "Idle";

  // ── Render ──────────────────────────────────────────────────────────────────

  // City select option for current value
  const selectedCityOption = CITY_OPTIONS.find((o) => o.value === tripCity) ?? null;

  return (
    <SpaceBetween size="l">
      {/* settleMarker — unique to this screen, required by registryCompleteness P1 */}
      <span
        data-settle-marker={SETTLE_MARKER}
        style={{ display: "none" }}
        aria-hidden="true"
        data-testid="simulate-vehicle-settle-marker"
      >
        {SETTLE_MARKER}
      </span>

      {/* Degradation notice when simulation endpoint is not configured */}
      {!apiConfigured && (
        <Alert type="info" data-testid="simulate-vehicle-no-endpoint-alert">
          The simulation API endpoint is not configured. Deploy the simulation stack and
          set the <code>connectedServicesUiSimulationApiEndpoint</code> context key, or
          the controls below will degrade to no-op.
        </Alert>
      )}

      {/* Degradation notice when subscriptions API is not configured (vehicle list) */}
      {!subscriptionsConfigured && (
        <Alert type="info" data-testid="simulate-vehicle-no-subscriptions-alert">
          The subscriptions API endpoint is not configured. Vehicle picker requires
          the subscriptions stack to be deployed.
        </Alert>
      )}

      {/* Vehicle list load error */}
      {vehiclesError && (
        <Alert type="error" data-testid="simulate-vehicle-vehicles-load-error">
          Could not load vehicle list: {vehiclesError}
        </Alert>
      )}

      {/* Main control panel */}
      <Container
        header={
          <Header
            variant="h2"
            description="Start, stop, and monitor a single-vehicle simulation session. The simulation path is derived from the vehicle's dataSource — never chosen by the operator."
            data-testid="simulate-vehicle-panel-header"
          >
            Simulation Control Panel
          </Header>
        }
        data-testid="simulate-vehicle-control-panel"
      >
        <SpaceBetween size="m">
          {/* Vehicle selector — Meridian vehicles; unready ones shown disabled (T5.2 item 7) */}
          <Box>
            {vehiclesLoading ? (
              <Spinner data-testid="simulate-vehicle-vehicles-loading" />
            ) : (
              <Select
                selectedOption={selectedOption}
                onChange={({ detail }) => {
                  // FG3.T1: guard on isBusy, not isRunning. The old `!isRunning`
                  // guard excluded only "running" and "stopping" — leaving "starting"
                  // reachable. Changing vehicle during phase === "starting" called
                  // setPhase("idle"), which cleared the busy flag and re-enabled
                  // both start affordances, opening a concurrent-start window.
                  if (!isBusy) {
                    setSelectedVehicleId(detail.selectedOption.value ?? null);
                    // A notice or error describes the previously selected vehicle's
                    // start attempt. Carrying it across a selection change
                    // attributes it to the wrong vehicle (T6.2, review cycle 1 W4).
                    //
                    // `phase` must reset with them. Clearing only the messages left
                    // phase === "error" with nothing rendered to explain it: the
                    // status line read "Error" and both alerts were empty, a
                    // dead-end reachable by the ordinary "try a different vehicle"
                    // gesture (review cycle 2 W3).
                    setNoticeMsg(null);
                    setErrorMsg(null);
                    setPhase("idle");
                  }
                }}
                options={vehicleOptions}
                disabled={isBusy || vehiclesLoading}
                placeholder={vehicles.length === 0 ? "No vehicles loaded" : "Select a vehicle"}
                ariaLabel="Select vehicle for simulation"
                data-testid="simulate-vehicle-selector"
                loadingText="Loading vehicles…"
                // NOT searchable, and that is a known defect rather than a choice:
                // 99 options of which 83 are labelled IDENTICALLY ("2025 Meridian
                // Azimuth — VIN MRDN…"), separated only by a 17-character VIN, and
                // the vehicleId is deliberately not rendered (see
                // vehicleOptionLabel). An operator told to use "VEH-MRDN-0015" has
                // no string in the list to look for.
                //
                // `filteringType="auto"` was tried on 2026-09-22 and REVERTED: it
                // works, but it changes how Cloudscape exposes options and breaks
                // the `findByRole("option", {name})` idiom this file's tests use in
                // six places. Adapting them is the real cost of the fix and it was
                // not in scope for a one-prop change.
                // issues/2026-09-22-vehicle-picker-unsearchable-99-options/
              />
            )}
            {/* T5.2 item 7: replace the false "All vehicles regardless of producer" note.
                The endpoint annotates vehicles (not filters them) — unready ones render
                disabled above. Source ready_count/count from the response when available. */}
            <Box color="text-status-inactive" fontSize="body-s" padding={{ top: "xs" }} data-testid="simulate-vehicle-picker-note">
              {readyCount !== null && !isReadinessIndeterminate
                ? `${readyCount} of ${vehicles.length} Meridian vehicles are simulation-ready. Unready vehicles are shown disabled with the reason.`
                : readyCount !== null && isReadinessIndeterminate
                  ? `Readiness could not be fully determined — vehicles remain selectable and the server validates readiness on start.`
                  : "Meridian vehicles only. Unready vehicles are shown disabled with the reason; the simulation path is derived from the selected vehicle\u2019s dataSource."}
            </Box>
            {/* Which campaign will collect on the selected vehicle.
                Rendered only once a vehicle is selected — there is nothing to say
                about coverage before then, and an empty row invites the reader to
                guess. See summariseTelemetryCampaign for why "absent" and "null" are
                different answers and must not be collapsed. */}
            {selectedVehicle ? (
              <Box padding={{ top: "xs" }} data-testid="simulate-vehicle-campaign-summary">
                <StatusIndicator
                  type={TELEMETRY_CAMPAIGN_TONE_STATUS[telemetryCampaignSummary.tone]}
                  data-testid="simulate-vehicle-campaign-indicator"
                >
                  {telemetryCampaignSummary.headline}
                </StatusIndicator>
                {telemetryCampaignSummary.detail ? (
                  <Box
                    color="text-status-inactive"
                    fontSize="body-s"
                    padding={{ top: "xxs" }}
                    data-testid="simulate-vehicle-campaign-detail"
                  >
                    {telemetryCampaignSummary.detail}
                  </Box>
                ) : null}
                {/* `hasDetail` comes from the summary module rather than being
                    re-derived here, so the link cannot disagree with the wording
                    about whether there is anything to open. */}
                {telemetryCampaignSummary.hasDetail ? (
                  <Box padding={{ top: "xxs" }}>
                    <Button
                      variant="inline-link"
                      onClick={() => setCampaignModalVisible(true)}
                      data-testid="simulate-vehicle-campaign-detail-link"
                    >
                      View signals collected
                    </Button>
                  </Box>
                ) : null}
              </Box>
            ) : null}
          </Box>

          {/* Status display */}
          <KeyValuePairs
            columns={3}
            items={[
              {
                label: "Status",
                value: (
                  <StatusIndicator
                    type={statusIndicatorType}
                    data-testid="simulate-vehicle-status-indicator"
                  >
                    {statusLabel}
                  </StatusIndicator>
                ),
              },
              {
                // Was "Messages Published", reading `status.message_count` — a key no
                // backend has ever returned, so it rendered "—" permanently from
                // 2026-09-13. No message counter exists anywhere in the simulation
                // table, the vehicle row, or the API, so the row could not be fixed in
                // place; it is repointed to the count that IS real and IS the useful
                // signal — trip rows actually created since this run started.
                // issues/2026-09-22-cs-simulate-session-panel-reads-fields-that-never-existed/
                label: "Trips Recorded",
                value: (
                  <span data-testid="simulate-vehicle-trips-materialised">
                    {status?.trips?.materialised != null
                      ? String(status.trips.materialised)
                      : "—"}
                  </span>
                ),
              },
              {
                label: "Last Message At",
                value: (
                  <span data-testid="simulate-vehicle-last-message-at">
                    {status?.last_message_at ?? "—"}
                  </span>
                ),
              },
            ]}
          />

          {/* Trip parameters — inline (T5.2, T5.4 items 1-3).
              City, Route Length, Safety Events, and Maintenance Events rendered inline.
              Safety/Maintenance were omitted in T5.2 while Tier B was unread; T4.2/T4.3
              gave both keys real readers, so they now ship (T5.4). */}
          <SpaceBetween size="s">
            <FormField label="City" data-testid="simulate-vehicle-city-field">
              <Select
                selectedOption={selectedCityOption}
                onChange={({ detail }) => {
                  if (detail.selectedOption.value) {
                    setTripCity(detail.selectedOption.value);
                  }
                }}
                options={CITY_OPTIONS}
                disabled={isBusy}
                ariaLabel="Select city for simulation"
                data-testid="simulate-vehicle-city-select"
              />
            </FormField>
            <FormField
              label="Route Length (points)"
              constraintText="5–60 points"
              data-testid="simulate-vehicle-route-length-field"
            >
              <Input
                value={tripRouteLength}
                onChange={({ detail }) => {
                  setTripRouteLength(detail.value);
                }}
                disabled={isBusy}
                type="number"
                inputMode="numeric"
                ariaLabel="Route length in points"
                data-testid="simulate-vehicle-route-length-input"
              />
            </FormField>

            {/* Safety Events selector (T5.4).
                Three catalog states: pending (spinner), loaded (real options),
                not_configured (disabled — names the missing config), error (disabled —
                surfaces the error). "No events in catalog" is ONLY shown when the catalog
                is genuinely empty, not when it is unreachable. */}
            <FormField
              label="Safety Events"
              description="Select safety events to simulate during this trip"
              data-testid="simulate-vehicle-safety-events-field"
            >
              {catalogState.kind === "pending" ? (
                <Spinner data-testid="simulate-vehicle-catalog-loading" />
              ) : catalogState.kind === "not_configured" ? (
                <Multiselect
                  selectedOptions={[]}
                  onChange={() => {}}
                  options={[]}
                  disabled={true}
                  placeholder="Event catalog endpoint not configured — deploy the subscriptions stack and set connectedServicesApiEndpoint"
                  data-testid="simulate-vehicle-safety-events-select"
                />
              ) : catalogState.kind === "error" ? (
                <Multiselect
                  selectedOptions={[]}
                  onChange={() => {}}
                  options={[]}
                  disabled={true}
                  placeholder={`Event catalog unavailable: ${catalogState.message}`}
                  data-testid="simulate-vehicle-safety-events-select"
                />
              ) : (
                <Multiselect
                  selectedOptions={selectedSafetyEvents}
                  onChange={({ detail }) =>
                    setSelectedSafetyEvents([...detail.selectedOptions] as EventOption[])
                  }
                  options={catalogState.safety}
                  disabled={isBusy}
                  placeholder={
                    catalogState.safety.length > 0
                      ? "None (normal driving)"
                      : "No events in catalog"
                  }
                  filteringType="auto"
                  data-testid="simulate-vehicle-safety-events-select"
                />
              )}
            </FormField>

            {/* Maintenance Events selector (T5.4).
                Same three-state catalog handling as Safety Events above.
                Events ending with a DTC code (e.g. · P0520) create an active DTC row. */}
            <FormField
              label="Maintenance Events"
              description="Select maintenance conditions to simulate. Events ending with a code (e.g. '· P0520') create an active DTC row."
              data-testid="simulate-vehicle-maintenance-events-field"
            >
              {catalogState.kind === "pending" ? (
                <Spinner data-testid="simulate-vehicle-catalog-loading-maintenance" />
              ) : catalogState.kind === "not_configured" ? (
                <Multiselect
                  selectedOptions={[]}
                  onChange={() => {}}
                  options={[]}
                  disabled={true}
                  placeholder="Event catalog endpoint not configured — deploy the subscriptions stack and set connectedServicesApiEndpoint"
                  data-testid="simulate-vehicle-maintenance-events-select"
                />
              ) : catalogState.kind === "error" ? (
                <Multiselect
                  selectedOptions={[]}
                  onChange={() => {}}
                  options={[]}
                  disabled={true}
                  placeholder={`Event catalog unavailable: ${catalogState.message}`}
                  data-testid="simulate-vehicle-maintenance-events-select"
                />
              ) : (
                <Multiselect
                  selectedOptions={selectedMaintenanceEvents}
                  onChange={({ detail }) =>
                    setSelectedMaintenanceEvents([...detail.selectedOptions] as EventOption[])
                  }
                  options={catalogState.maintenance}
                  disabled={isBusy}
                  placeholder={
                    catalogState.maintenance.length > 0
                      ? "None (healthy vehicle)"
                      : "No events in catalog"
                  }
                  filteringType="auto"
                  data-testid="simulate-vehicle-maintenance-events-select"
                />
              )}
            </FormField>
          </SpaceBetween>

          {/* Action buttons — single Start (T5.2 item 2: Trip Simulator button removed) */}
          <SpaceBetween size="xs" direction="horizontal">
            <Button
              variant="primary"
              onClick={() => { void handleStart(); }}
              disabled={isBusy || !apiConfigured || !selectedVehicleId}
              loading={phase === "starting"}
              data-testid="simulate-vehicle-start-button"
            >
              Start
            </Button>
            <Button
              onClick={() => { void handleStop(); }}
              disabled={!isRunning}
              loading={phase === "stopping"}
              data-testid="simulate-vehicle-stop-button"
            >
              Stop
            </Button>
            {/* Onboard FWE agent lifecycle. Rendered only for an onboard vehicle
                (see `isOnboardSelected` for why the gate is `dataSource` and not
                CMS's `!isOEM1`). Delivers the "Agent controls" clause of spec
                2026-09-15-cms-cs-campaign-ownership § Decision 10, which was
                specified but never tasked — issues/2026-09-22-cs-simulate-
                missing-sim-console-and-agent-controls/.
                Independent of the trip simulation: the agent must be running
                BEFORE a trip produces FWE output, so this is deliberately not
                disabled by `isBusy`. */}
            {isOnboardSelected && (
              <Button
                iconName={agentRunning ? "close" : "caret-right-filled"}
                onClick={() => { void toggleAgent(); }}
                disabled={!apiConfigured || agentLoading}
                loading={agentLoading}
                data-testid="simulate-vehicle-agent-toggle-button"
              >
                {agentRunning ? "Stop Agent" : "Start Agent"}
              </Button>
            )}
            {phase === "starting" || phase === "stopping" ? (
              <Spinner data-testid="simulate-vehicle-spinner" />
            ) : null}
          </SpaceBetween>
        </SpaceBetween>
      </Container>

      {/* Error display */}
      {phase === "error" && errorMsg && (
        <Alert
          type="error"
          header="Simulation error"
          data-testid="simulate-vehicle-error-alert"
          action={
            canQuickAssign ? (
              <Button
                onClick={() => { void handleQuickAssign(); }}
                loading={assigning}
                data-testid="simulate-vehicle-quick-assign-button"
              >
                Assign a baseline campaign
              </Button>
            ) : undefined
          }
        >
          {errorMsg}
        </Alert>
      )}

      {/* Agent lifecycle errors are surfaced separately from simulation errors:
          the two are independent failures (the agent can fail to start while a
          simulation runs fine, and vice versa), and folding them into `errorMsg`
          would let one clear the other. CMS only console.errors these, so an
          operator there gets no feedback on a failed start at all. */}
      {agentError && (
        <Alert
          type="error"
          header="Agent error"
          dismissible
          onDismiss={() => setAgentError(null)}
          data-testid="simulate-vehicle-agent-error-alert"
        >
          {agentError}
        </Alert>
      )}

      {/* Why agent status / logs are unavailable, when the cause is the caller
          rather than the simulator. Rendered as a warning, not an error: the
          simulation keeps running and the operator's run is not at risk, which is
          exactly the distinction "Simulator offline" erased. */}
      {agentProbeError && (
        <Alert
          type="warning"
          header="Agent status unavailable"
          dismissible
          onDismiss={() => setAgentProbeError(null)}
          data-testid="simulate-vehicle-agent-probe-error-alert"
        >
          {agentProbeError}
        </Alert>
      )}

      {/* Zero-data verdict from the backend. NOT dismissible: it means the run
          completed and produced nothing, which is a silent failure the operator
          cannot otherwise see — the run looks successful at every other layer, and
          the status still reads "completed". The API has sent this since
          2026-09-01; this view discarded it until 2026-09-22, so every zero-data
          run to date looked like a success here. The text carries its own
          remediation command, so it is rendered verbatim rather than summarised. */}
      {status?.dataWarning && (
        <Alert
          type="warning"
          header="Simulation produced no trip data"
          data-testid="simulate-vehicle-data-warning-alert"
        >
          {status.dataWarning}
        </Alert>
      )}

      {/* Non-error notices — T6.2.
          Rendered independently of `phase`, unlike the error alert above. Two
          messages need this and neither is an error:

            - the server's campaign advisory, which arrives WITH a successful start
              (phase "running"), and
            - "subscriptions endpoint not configured", which leaves phase "idle".

          The second is pre-existing: before T6.2 that string was written to
          `errorMsg` alongside `setPhase("idle")`, and since the alert above only
          renders on phase === "error" it was never displayed to anyone. An operator
          on an unconfigured deployment clicked Start and saw nothing happen. */}
      {noticeMsg && (
        <Alert
          type="warning"
          header="Simulation notice"
          data-testid="simulate-vehicle-notice-alert"
        >
          {noticeMsg}
        </Alert>
      )}

      {/* Active simulation details */}
      {simulationId && (
        <Container
          header={
            <Header variant="h3" data-testid="simulate-vehicle-session-header">
              Active Session
            </Header>
          }
          data-testid="simulate-vehicle-session-details"
        >
          <KeyValuePairs
            columns={2}
            items={[
              {
                label: "Simulation ID",
                value: (
                  <span data-testid="simulate-vehicle-simulation-id">{simulationId}</span>
                ),
              },
              {
                label: "Vehicle (simulated)",
                value: (
                  <span data-testid="simulate-vehicle-active-vehicle-id">
                    {selectedVehicleId}&nbsp;<StatusIndicator type="info">simulated</StatusIndicator>
                  </span>
                ),
              },
            ]}
          />
        </Container>
      )}

      {/* Simulation console — the trip-simulator log pane, mirrored from CMS
          VehicleDetailView.tsx:1961. Deliberately NOT gated on dataSource: a
          simulation run emits output for a cloud/MQTT-direct vehicle too, and
          CMS does not gate it either. Only the FWE container below is
          onboard-only.

          Spec 2026-09-15-cms-cs-campaign-ownership § Decision 10 excluded
          SimLogViewer because it "needs a simId CS does not have". That premise
          is no longer true — `simulationId` is held in this component and set by
          handleStart. See issues/2026-09-22-cs-simulate-missing-sim-console-and-
          agent-controls/ for the reversal. */}
      {selectedVehicleId && (
        <Container
          header={<Header variant="h3">Simulation Logs</Header>}
          data-testid="simulate-vehicle-sim-log-viewer-container"
        >
          <SimLogViewer
            vehicleId={selectedVehicleId}
            vin={selectedVin}
            simReachable={simReachable}
            simulationId={simulationId}
          />
        </Container>
      )}

      {/* FWE agent log viewer — mirrors CMS VehicleDetailView FWELogViewer
          integration. Gated on dataSource === "vehicle-telemetry" (onboard):
          an offboard/cloud vehicle's telemetry never touches an FWE agent,
          so this container has no state to show for one — same finding as
          the CMS-side gap on FWELogViewer/ConnectedServicesCard.
          issues/2026-09-19-fwe-agent-logs-and-message-count-shown-for-cloud-vehicle/

          Reads the shared `isOnboardSelected`/`selectedVin` derivation rather
          than re-deriving inline, so this container and the agent toggle cannot
          gate on different fields. */}
      {selectedVehicleId && isOnboardSelected && (
        <Container
          header={<Header variant="h3">FWE Agent Logs</Header>}
          data-testid="simulate-vehicle-fwe-log-viewer-container"
        >
          <FWELogViewer
            vin={selectedVin}
            simReachable={simReachable}
            agentRunning={agentRunning}
          />
        </Container>
      )}

      {/* Rendered unconditionally so the modal owns its own visibility; it fetches
          only while `visible`, so an unopened modal costs nothing. */}
      <TelemetryCampaignDetailModal
        visible={campaignModalVisible}
        onDismiss={() => setCampaignModalVisible(false)}
        vehicle={selectedVehicle}
      />
    </SpaceBetween>
  );
};

export default SimulateVehicleView;
