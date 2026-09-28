// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Subscription-plane REST client.
 *
 * Spec: `.kiro/specs/2026-09-10-cms-connected-services-subscriptions/spec.md`
 *       T3.5 (Group 3 frontend wiring).
 *
 * Reads `runtimeConfig.subscriptionsApiEndpoint` (populated by
 * `ConnectedServicesUiStack` — see `env.ts::RuntimeConfig`). When the endpoint
 * is empty (the subscriptions stack is not deployed), `fetchProducts()`
 * returns `null` so callers can fall back to their fixture rather than
 * fire an undefined-URL fetch — this is the same defect class the DMS F6
 * runtime-config lesson caught, applied to a graceful degradation instead
 * of an outright failure.
 *
 * The Authorization header carries the **ID** token from `sessionStorage`
 * (`idToken` / `STORAGE_KEY_ID_TOKEN`, populated by `auth.ts`). Same read
 * `auth.ts::currentSession` uses — no separate auth surface.
 *
 * It must be the ID token, not the access token. This API's API Gateway
 * authorizer is `COGNITO_USER_POOLS` and validates the `aud` claim, which
 * only the ID token carries; the access token carries `client_id` instead.
 * Measured against live staging 2026-09-13 on `/products`, `/subscriptions`
 * and `/vehicles/available`, with and without the `Bearer ` prefix: ID token
 * 200 on all six, access token 401 on all six. This file previously read
 * `authToken` (the access token), which 401'd every call from the browser —
 * see `issues/2026-09-13-cs-portal-subscriptions-client-sends-access-token/`.
 */

import { getRuntimeConfig } from "../env";
import { STORAGE_KEY_ID_TOKEN } from "../auth";

/** JSON shape returned by `GET /products`, mirroring `products/handler.py`. */
export interface ProductCatalogEntry {
  readonly product_id: string;
  readonly name: string;
  readonly schema_version: string;
  readonly data_category: string;
  readonly delivery_profile: { frequency: string; fidelity: string };
  readonly source: string;
}

export interface ProductCatalogResponse {
  readonly catalog_version: string;
  readonly products: readonly ProductCatalogEntry[];
}

/** JSON shape returned by `GET /vehicles/available`, mirroring
 *  `vehicles_available/handler.py`. */
export interface AvailableVehicle {
  readonly vin: string;
  readonly vehicleId: string;
}

/**
 * A vehicle entry in the simulation picker, mirroring
 * `simulate_vehicle/handler.py` list_vehicles_handler.
 *
 * Includes `dataSource` so the display can show the transport,
 * and `producer` for informational labelling only — never used
 * as a filter (spec T6.1: the picker is unfiltered on producer).
 *
 * `simulation_ready` and `not_ready_reasons` are optional (T5.0 additions).
 * If absent, treat as ready — so the screen stays functional against a server
 * that has not yet deployed T5.0. See spec T5.2 item 6.
 */
export interface SimulationVehicleEntry {
  readonly vehicleId: string;
  readonly vin?: string;
  readonly dataSource?: string;
  readonly producer?: string;
  /**
   * Human-meaningful identity, added 2026-09-20 so the picker can label a vehicle
   * the way an operator recognises it rather than by `vehicleId`, which carries no
   * meaning to a person and in a few legacy rows carries a misleading one.
   */
  readonly make?: string;
  readonly model?: string;
  readonly year?: string | number;
  /**
   * T5.0 readiness advisory fields. Optional for backward compatibility — absent
   * means treat as ready so no version-skew can brick the picker.
   *
   * Three distinct states:
   *   absent / undefined → ready (backward compat: version-skew for pre-T5.0 server)
   *   false              → not ready; option should be disabled
   *   null               → readiness indeterminate (scan failed); option MUST NOT be disabled
   *
   * Reason tokens: "not_fleet_enrolled" | "no_certificate" | "no_telemetry_campaign" |
   *                "readiness_unavailable" (FG13.T3: readiness scan failed)
   * Wording lives in SimulateVehicleView's READINESS_REASON_LABELS — one place only.
   */
  readonly simulation_ready?: boolean | null;
  readonly not_ready_reasons?: readonly string[];
  /**
   * Which RUNNING campaigns cover this vehicle, resolved server-side.
   *
   * **Plural, and every matching scope is present** — FleetWise runs all matching
   * campaigns concurrently, so a vehicle can be covered by a per-vehicle campaign
   * AND its fleet's AND a broadcast at the same time. `vehicle:MRDN0000000000015`
   * carries four. An earlier revision of this field was singular and reported one
   * of the four, which understated collection by 300x.
   *
   * Ordered most-specific-first (vehicle → fleet → broadcast), largest first within
   * a scope, then by `campaignId`. The order is server-assigned and stable; do not
   * re-sort on scan-order assumptions.
   *
   * Three distinct states, and conflating any two of them misinforms the operator:
   *   non-empty → these campaigns will collect on the next run
   *   `[]`      → nothing covers it (paired with `no_telemetry_campaign` when
   *               applicable)
   *   absent    → readiness was indeterminate; the scan failed and NO claim is
   *               being made either way
   *
   * `campaignId`/`campaignName` may be null when the coverage came from a
   * set-shaped source that carries no identity — report the scope, do not invent
   * a name.
   */
  readonly telemetry_campaigns?: readonly {
    readonly scope: "vehicle" | "fleet" | "broadcast";
    readonly target: string;
    readonly campaignId?: string | null;
    readonly campaignName?: string | null;
    /**
     * Entries in THIS campaign's `signalsToCollect`, deliberately not
     * de-duplicated — it is the number the campaigns list view shows, and
     * `CampaignSignalsPanel` reconciles entries against distinct itself.
     */
    readonly signalCount?: number | null;
  }[];
  /**
   * DISTINCT signal ids across every campaign above.
   *
   * **Never sum `signalCount` to derive this.** Ids repeat both within one campaign
   * (`cms-fleet-gps-10s` is 293 entries / 286 distinct) and across campaigns, so
   * the four campaigns on `vehicle:MRDN0000000000015` sum to 304 entries while
   * covering 295 distinct signals. The union is computed server-side because the
   * ids themselves are far too large to ship in a 99-vehicle list response.
   */
  readonly telemetry_signal_total?: number | null;
  /**
   * False for a cloud-telemetry vehicle, which reaches MSK via the rule path and
   * needs no FleetWise campaign. Distinguishes "no campaign, and that is a
   * problem" from "no campaign, and none is needed".
   */
  readonly telemetry_campaign_applicable?: boolean;
}

export interface SimulationVehiclesResponse {
  readonly vehicles: readonly SimulationVehicleEntry[];
  readonly count: number;
  /**
   * True — the endpoint filters to `producer == meridian` (Group 8, reversing
   * T6.1). CS is Meridian's own portal and there is no oem1/tesla simulation path.
   */
  readonly filtered_on_producer: boolean;
  /**
   * T5.0 addition: how many vehicles in this response are simulation-ready.
   * Optional for backward compatibility — absent if server has not deployed T5.0.
   *
   * FG13.T3 changed the semantics: this count now EXCLUDES vehicles whose
   * readiness is indeterminate (simulation_ready === null). A response with
   * ready_count: 0 and all-null simulation_ready values means readiness could
   * not be determined for the batch — NOT that zero vehicles are actually ready.
   * Callers must check for all-null vehicles before treating 0 as "none are ready".
   */
  readonly ready_count?: number;
}

export interface VehiclesAvailableResponse {
  readonly vehicles: readonly AvailableVehicle[];
  readonly count: number;
  readonly candidates_total: number;
  readonly candidates_probed: number;
  readonly probe_limit: number;
  readonly truncated: boolean;
  readonly next_after: string | null;
  readonly unresolved_vins: readonly string[];
}

export interface SubscriptionRow {
  readonly subscription_id: string;
  readonly consumer_id: string;
  readonly product_id: string;
  readonly vehicle_scope: readonly string[];
  readonly vehicle_scope_count: number;
  readonly state: string;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface ListSubscriptionsResponse {
  readonly subscriptions: readonly SubscriptionRow[];
  readonly count: number;
}

/**
 * Fetch VINs the caller is eligible to enroll into a new subscription.
 *
 * Returns `null` when the subscriptions API is not configured — same
 * degradation contract as `fetchProducts`.
 */
export async function fetchAvailableVehicles(
  fetchImpl: typeof fetch = fetch,
): Promise<VehiclesAvailableResponse | null> {
  const base = getSubscriptionsApiBase();
  if (!base) return null;

  const token = currentApiToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetchImpl(`${base}/vehicles/available`, {
    method: "GET",
    headers,
    credentials: "omit",
  });
  if (!resp.ok) {
    throw new Error(
      `subscriptions API /vehicles/available returned ${resp.status} ${resp.statusText}`,
    );
  }
  return (await resp.json()) as VehiclesAvailableResponse;
}

/**
 * List the caller's own subscriptions.
 *
 * Returns `null` when the API is not configured.
 */
export async function fetchMySubscriptions(
  fetchImpl: typeof fetch = fetch,
): Promise<ListSubscriptionsResponse | null> {
  const base = getSubscriptionsApiBase();
  if (!base) return null;

  const token = currentApiToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetchImpl(`${base}/subscriptions`, {
    method: "GET",
    headers,
    credentials: "omit",
  });
  if (!resp.ok) {
    throw new Error(
      `subscriptions API /subscriptions returned ${resp.status} ${resp.statusText}`,
    );
  }
  return (await resp.json()) as ListSubscriptionsResponse;
}

/**
 * Add a VIN to a subscription's scope.
 *
 * Idempotent — adding an already-present VIN is a no-op success server-side
 * (see `subscription_scope/handler.py`'s `add_handler`).
 */
export async function addVinToSubscription(
  subscriptionId: string,
  vin: string,
  fetchImpl: typeof fetch = fetch,
): Promise<void> {
  const base = getSubscriptionsApiBase();
  if (!base) {
    throw new Error("Subscriptions API endpoint is not configured");
  }
  const token = currentApiToken();
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetchImpl(
    `${base}/subscriptions/${encodeURIComponent(subscriptionId)}/scope`,
    {
      method: "POST",
      headers,
      credentials: "omit",
      body: JSON.stringify({ vin }),
    },
  );
  if (!resp.ok) {
    throw new Error(
      `subscriptions API /subscriptions/${subscriptionId}/scope returned ${resp.status} ${resp.statusText}`,
    );
  }
}


/** Base URL, trimmed of any trailing slash so the join is unambiguous. */
export function getSubscriptionsApiBase(): string | null {
  const cfg = getRuntimeConfig();
  const raw = (cfg.subscriptionsApiEndpoint ?? "").trim();
  if (!raw) return null;
  return raw.endsWith("/") ? raw.slice(0, -1) : raw;
}

/**
 * The token this API's authorizer accepts: the Cognito **ID** token.
 *
 * Named for the API rather than the token type on purpose — the previous name
 * (`currentAccessToken`) described the wrong token and is how the defect
 * survived review. Reads the key `auth.ts` owns; do not introduce a third
 * token-storage path.
 */
function currentApiToken(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY_ID_TOKEN);
  } catch {
    // sessionStorage is unavailable in some test envs; treat as unauthenticated.
    return null;
  }
}

/**
 * Fetch the seeded product catalog.
 *
 * Returns:
 *   - the parsed response body on 200,
 *   - `null` when the subscriptions API is not configured (endpoint empty),
 *     signalling to the caller that it should render fixtures instead,
 *   - throws for HTTP errors and network failures — the caller decides
 *     whether to surface a banner or fall back.
 */
export async function fetchProducts(
  fetchImpl: typeof fetch = fetch,
): Promise<ProductCatalogResponse | null> {
  const base = getSubscriptionsApiBase();
  if (!base) return null;

  const token = currentApiToken();
  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const resp = await fetchImpl(`${base}/products`, {
    method: "GET",
    headers,
    // Same-origin cookies are not used by this API — Cognito auth is
    // header-only, so credentials: 'omit' is the correct default.
    credentials: "omit",
  });
  if (!resp.ok) {
    throw new Error(
      `subscriptions API /products returned ${resp.status} ${resp.statusText}`,
    );
  }
  const body = (await resp.json()) as ProductCatalogResponse;
  return body;
}


/**
 * List the vehicles available to the simulation picker.
 *
 * Spec: `.kiro/specs/2026-09-14-cs-portal-data-model-backend`, Group 6 T6.1,
 *       superseded on this point by that spec's Group 8 (2026-09-20).
 *
 * **`GET /simulate/vehicles` IS producer-filtered** — it returns only vehicles with
 * `producer == meridian`, and sets `filtered_on_producer: true` to say so. The handler's own
 * docstring states it ("The vehicle picker IS filtered by `producer` as of Group 8"), and it
 * is visible in the response type on `SimulationVehiclesResponse.filtered_on_producer`.
 *
 * This docstring previously said the opposite — "applies NO producer filter" — which was true
 * for T6.1 and was not updated when Group 8 reversed it. Corrected 2026-09-20 while building
 * the campaign detail view, whose vehicle resolution depends on knowing this is a SUBSET.
 *
 * ## Consequence for callers: absence from this list is not absence from the fleet
 *
 * Measured against staging the same day: **100 of the 155** vehicles in
 * `cms-staging-storage-vehicles` are returned. So a VIN missing from this response may still
 * be a real, valid vehicle that simply sits outside the producer filter — exactly one such
 * case exists live (`4T1B11HK0LU98765`).
 *
 * A caller MUST NOT treat absence here as evidence that a vehicle is invalid. See
 * `components/screens/data-model/vehicleResolution.ts`, which exists for this reason: it
 * distinguishes `outside-scope` (VIN-shaped, absent from this subset) from `malformed`
 * (cannot be a VIN at all), and only the latter is presented as unresolvable.
 *
 * Returns `null` when the subscriptions API is not configured — same
 * degradation contract as `fetchProducts`.
 */
export async function listVehiclesForSimulation(
  fetchImpl: typeof fetch = fetch,
): Promise<SimulationVehiclesResponse | null> {
  const base = getSubscriptionsApiBase();
  if (!base) return null;

  const token = currentApiToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetchImpl(`${base}/simulate/vehicles`, {
    method: "GET",
    headers,
    credentials: "omit",
  });
  if (!resp.ok) {
    throw new Error(
      `subscriptions API /simulate/vehicles returned ${resp.status} ${resp.statusText}`,
    );
  }
  return (await resp.json()) as SimulationVehiclesResponse;
}

/**
 * Start a simulation for a given vehicle, via the subscriptions plane.
 *
 * Spec: `.kiro/specs/2026-09-15-cms-cs-campaign-ownership`, Task 6.2.
 * Originally `resolveSimulationPath` (spec `2026-09-14-cs-portal-data-model-backend`
 * Group 6 T6.1), when `POST /simulate/start` resolved a path and started nothing.
 * Task 6.1 made that route dispatch to the simulation Lambda, so the old name and
 * the old `SimulationStartResolvedResponse` type both described behaviour that no
 * longer exists — and a reader trusts the name over the code.
 *
 * The request carries NO path/transport parameter. The server reads the vehicle's
 * `dataSource` and derives BOTH simulation axes (`mode` and `rule_name`) from it, so
 * there is deliberately nothing to override here. A vehicle with no `dataSource` is
 * rejected server-side with an explicit reason.
 *
 * This replaces `simulationClient.startSimulation` as the start path for CS. That
 * function posted straight to the simulation API with a client-supplied `rule_name`
 * from runtime config; going through the subscriptions plane means the caller is
 * authorized (`connected-services` group) and the routing is derived server-side
 * rather than asserted by the browser. Stop and status stay on the simulation API.
 *
 * Returns `null` when the subscriptions API is not configured.
 * Throws `SimulationStartError` on a non-2xx, carrying the server's `reason` — see
 * that class for why this is not a plain `Error`.
 */
export interface VehicleSimulationStartResponse {
  /** The simulation Lambda's id — poll `GET /api/simulation/status/{id}` with it. */
  readonly simulation_id: string;
  readonly vehicle_id: string;
  readonly data_source: string;
  /** Human-readable description of the transport actually dispatched. */
  readonly dispatch: string;
  readonly success?: boolean;
  readonly status?: string;
  /**
   * Non-blocking advisory — e.g. an FWE agent started with no campaign assigned.
   * Its absence means "no advisory", not "not checked".
   */
  readonly warning?: string;
}

/**
 * A non-2xx from `POST /simulate/start`, preserving the server's `reason` token.
 *
 * The previous implementation threw `new Error("... returned 400 Bad Request")`,
 * which discarded the response body. That was survivable while the route only
 * resolved a path, but Task 6.1 gave it a contract the operator has to act on:
 * `no_data_source` and `unknown_data_source` (400) mean "fix the vehicle record",
 * `no_telemetry_campaign` (409) means "assign a campaign", and 403 means "you are
 * not an operator". Collapsing all four into an HTTP status line tells the operator
 * nothing about which one they hit.
 */
export class SimulationStartError extends Error {
  readonly status: number;
  /** Machine token from the server, when it sent one. */
  readonly reason?: string;

  constructor(status: number, message: string, reason?: string) {
    super(message);
    this.name = "SimulationStartError";
    this.status = status;
    this.reason = reason;
  }
}

/**
 * Optional trip parameters for `startVehicleSimulation`.
 *
 * Spec: `.kiro/specs/2026-09-19-cs-trip-simulator-parity`, T4.2 — this is the
 * CS-side mirror of `simulate_vehicle/handler.py`'s extended `sim_config` (T2.1).
 *
 * Deliberately has NO `mode` or `rule_name` field. Those two axes are the ones
 * `startVehicleSimulation`'s own docstring above says are "deliberately nothing
 * to override" — the interface enforces that at the type level, not just by
 * convention: there is no field here a caller COULD populate to influence
 * routing, so `{ vehicle_id, ...tripParams }` can never smuggle one in even if
 * a future caller tried. `subscriptionsClient.test.ts`'s existing "sends
 * NOTHING that could influence routing" test remains the runtime pin; this
 * type is the compile-time one.
 */
export interface TripSimulationParams {
  readonly city?: string;
  readonly trips?: number;
  readonly route_length?: number;
  readonly safety_scenarios?: readonly string[];
  readonly maintenance_scenarios?: readonly string[];
}

export async function startVehicleSimulation(
  vehicleId: string,
  fetchImpl: typeof fetch = fetch,
  tripParams?: TripSimulationParams,
): Promise<VehicleSimulationStartResponse | null> {
  const base = getSubscriptionsApiBase();
  if (!base) return null;

  const token = currentApiToken();
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  // `tripParams` is spread AFTER `vehicle_id` and never carries `mode`/`rule_name`
  // (`TripSimulationParams` has no such fields — see its docstring). When
  // `tripParams` is omitted the body is EXACTLY `{ vehicle_id }`, unchanged from
  // before this parameter existed — `subscriptionsClient.test.ts`'s "sends NOTHING
  // that could influence routing" test pins this shape and must keep passing.
  const body: Record<string, unknown> = { vehicle_id: vehicleId, ...tripParams };

  const resp = await fetchImpl(`${base}/simulate/start`, {
    method: "POST",
    headers,
    credentials: "omit",
    body: JSON.stringify(body),
  });

  if (!resp.ok) {
    // Read the body for `reason` / `error`. A non-JSON body (a gateway HTML error
    // page, say) must not mask the status, so parsing failure degrades to the
    // status line rather than throwing something unrelated.
    let reason: string | undefined;
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = (await resp.json()) as { error?: string; reason?: string };
      if (typeof body?.reason === "string") reason = body.reason;
      if (typeof body?.error === "string" && body.error) detail = body.error;
    } catch {
      // keep the status line
    }
    throw new SimulationStartError(resp.status, detail, reason);
  }

  return (await resp.json()) as VehicleSimulationStartResponse;
}
