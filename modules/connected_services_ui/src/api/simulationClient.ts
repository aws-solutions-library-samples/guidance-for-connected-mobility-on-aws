// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Simulation REST client.
 *
 * Spec: `.kiro/specs/2026-09-12-cs-simulator-oem2-manifest-path/tasks.md` T7.7
 *
 * Reads `runtimeConfig.simulationApiEndpoint` (T7.4) and
 * `runtimeConfig.simulationProductRuleName` (T7.4a). When the endpoint is
 * empty (simulation stack not deployed), every function returns `null` so
 * the caller can degrade honestly rather than firing an undefined-URL fetch —
 * the same pattern as `subscriptionsClient.ts`.
 *
 * Auth: the `sessionStorage` **ID** token, `STORAGE_KEY_ID_TOKEN` ("idToken"),
 * exactly as `subscriptionsClient.ts` does — no second auth surface.
 *
 * It must be the ID token, not the access token. MEASURED against the live
 * simulation API (`tekm196qb5`) on 2026-09-13, same request, same user:
 *     ID token     -> HTTP 200
 *     ACCESS token -> HTTP 401 {"message":"Unauthorized"}
 * The COGNITO_USER_POOLS authorizer validates the `aud` claim, which access
 * tokens do not carry. `authorizationScopes` is null on these methods, which
 * rules out an access-token-only configuration but does NOT imply access
 * tokens are accepted — only a live two-token probe settles that, and it did.
 *
 * This shipped wrong: T7.7's Accept said "sends the sessionStorage `authToken`
 * bearer header exactly as `subscriptionsClient.ts` does", and those two clauses
 * contradict — `authToken` IS the access-token key, while subscriptionsClient
 * sends `idToken`. The literal key name won, and every portal call 401'd. Unit
 * tests could not see it: they mock `fetch`, so no authorizer runs.
 *
 * POST /start body:
 *   - `vehicles`: the vehicleId array (`["VEH-MRDN-0011"]`), NOT the VIN.
 *     `resolve_vins_to_fleets` queries `IndexName="vehicleId-index"` on
 *     `vehicleId`; enrollment rows hold `VEH-MRDN-000N`; passing a VIN
 *     resolves to `{}` → 404.  See
 *     `issues/2026-09-05-vehicleid-diverges-from-vin`.
 *   - `rule_name`: `runtimeConfig.simulationProductRuleName`, omitted entirely
 *     when that value is empty so the Lambda applies its CMS-native default
 *     rather than receiving `""` (T7.4a).
 *
 * No second auth surface — same `authToken` sessionStorage key that `auth.ts`
 * writes and `subscriptionsClient.ts` reads.
 */

import { getRuntimeConfig } from "../env";
import { STORAGE_KEY_ID_TOKEN } from "../auth";

// ── Public types ─────────────────────────────────────────────────────────────

export interface SimulationStartResponse {
  readonly simulation_id: string;
}

export interface SimulationStatus {
  readonly simulation_id: string;
  readonly status: string;
  /**
   * Last telemetry timestamp for the vehicle this run drives, or absent/null when
   * unknown. Sourced server-side from the vehicle row's `lastSeenAt`.
   *
   * Declared here since 2026-09-13 but NOT returned by the API until 2026-09-22 —
   * the UI read it and rendered a permanent em-dash for nine days. See
   * issues/2026-09-22-cs-simulate-session-panel-reads-fields-that-never-existed/.
   */
  readonly last_message_at?: string | null;
  /** Trip counts. `materialised` is the real achieved count; `total` is a request echo. */
  readonly trips?: {
    /** Requested: config.trips x vehicle_count. NOT progress — do not render as such. */
    readonly total?: number;
    /**
     * Achieved: trip rows created since the run started. `null`/absent means
     * "could not be determined" and must NOT be rendered as 0 — a run of unknown
     * output and a run of zero output are different states, and conflating them is
     * what made the zero-data condition invisible.
     */
    readonly materialised?: number | null;
    /** Never populated by the API. Do not read. Kept only to document that. */
    readonly completed?: number;
    /** Never populated by the API. Do not read. Kept only to document that. */
    readonly progress?: number;
  };
  /**
   * Present only when the backend determined the run completed without producing
   * any trip. Carries operator-facing remediation text. The API has always sent
   * this; the UI discarded it until 2026-09-22.
   */
  readonly dataWarning?: string;
  readonly error?: string;
}

export interface SimulationSummary {
  readonly simulation_id: string;
  readonly status: string;
  readonly vehicle_id: string;
}

export interface SimulationListResponse {
  readonly simulations: readonly SimulationSummary[];
}

// ── Internal helpers ──────────────────────────────────────────────────────────

/**
 * Base URL for the simulation API, trimmed of trailing slash.
 * Returns null when the simulation stack is not deployed (endpoint empty).
 */
export function getSimulationApiBase(): string | null {
  const cfg = getRuntimeConfig();
  const raw = (cfg.simulationApiEndpoint ?? "").trim();
  if (!raw) return null;
  return raw.endsWith("/") ? raw.slice(0, -1) : raw;
}

/**
 * Route prefix every simulation endpoint sits behind.
 *
 * A single constant on purpose. The first cut of this client spelled the prefix
 * inline at each call site and got it right for `/start` and wrong for `/stop`,
 * `/status` and `/list` — which the unit tests could not see, because they mock
 * `fetch` and so assert on the body and headers rather than on the URL. Review
 * caught it against the live API (`/api/simulation/stop/{simulationId}` et al,
 * `simulation_stack.py:972-990`).
 *
 * Four literals are four chances to diverge; one constant is one. The tests now
 * also assert the full URL passed to `fetch`, so a missing prefix fails locally
 * rather than at the API Gateway.
 */
const SIMULATION_ROUTE_PREFIX = "/api/simulation";

/** Absolute URL for a simulation route, e.g. `path = "/list"`. */
function simulationUrl(base: string, path: string): string {
  return `${base}${SIMULATION_ROUTE_PREFIX}${path}`;
}

/**
 * Absolute URL for a simulation route, resolving the base itself.
 *
 * Exists so the mirrored `SimLogViewer.tsx` can stay byte-identical to the CMS
 * canonical, which calls `getSimulationApiUrl('/list')`. It is the one line of
 * that mirror permitted to differ (the import path) — the call signature and
 * behaviour must match CMS's `utils/simulation-config.ts:32` exactly, including
 * the `null` base case, or the mirror is not a mirror.
 *
 * Deliberately NOT fail-closed on a null base, matching CMS: the resulting
 * `"null/api/simulation/..."` request fails and is swallowed by the caller's
 * try/catch. In practice unreachable, because every consumer gates rendering on
 * `simReachable`, which is false whenever the base is null. Prefer
 * `getSimulationApiBase()` + `simulationUrl()` for new code, which is typed
 * `string | null` and forces the caller to handle it.
 */
export function getSimulationApiUrl(path: string): string {
  return `${getSimulationApiBase()}${SIMULATION_ROUTE_PREFIX}${path}`;
}

/**
 * Bearer token for the simulation API's authorizer.
 * Reads the ID token from sessionStorage — see the module docstring for why it
 * must not be the access token (measured 401).
 * Returns null when sessionStorage is unavailable (e.g. some test envs).
 */
function currentAuthToken(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY_ID_TOKEN);
  } catch {
    return null;
  }
}

// ── Simulation API calls ──────────────────────────────────────────────────────

/**
 * Start a simulation session for the given vehicle.
 *
 * - `vehicles` must carry vehicleId values, NOT VINs (see module docstring).
 * - `rule_name` is injected from `runtimeConfig.simulationProductRuleName`
 *   (T7.4a) and omitted when that value is empty — the Lambda applies its
 *   CMS-native default in that case, which is an honest degradation.
 *
 * Returns `null` when the simulation API is not configured.
 */
export async function startSimulation(
  vehicleIds: string[],
  fetchImpl: typeof fetch = fetch,
): Promise<SimulationStartResponse | null> {
  const base = getSimulationApiBase();
  if (!base) return null;

  const cfg = getRuntimeConfig();
  const ruleName = (cfg.simulationProductRuleName ?? "").trim();

  const token = currentAuthToken();
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  // Build the request body. rule_name is omitted entirely when empty —
  // never sent as "": the Lambda treats absent differently from empty.
  const body: Record<string, unknown> = { vehicles: vehicleIds };
  if (ruleName) {
    body.rule_name = ruleName;
  }

  const resp = await fetchImpl(simulationUrl(base, "/start"), {
    method: "POST",
    headers,
    credentials: "omit",
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    throw new Error(
      `simulation API /start returned ${resp.status} ${resp.statusText}`,
    );
  }
  return (await resp.json()) as SimulationStartResponse;
}

/**
 * Stop a running simulation.
 *
 * Returns `null` when the simulation API is not configured.
 */
export async function stopSimulation(
  simulationId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<void> {
  const base = getSimulationApiBase();
  if (!base) return;

  const token = currentAuthToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetchImpl(
    simulationUrl(base, `/stop/${encodeURIComponent(simulationId)}`),
    {
      method: "POST",
      headers,
      credentials: "omit",
    },
  );
  if (!resp.ok) {
    throw new Error(
      `simulation API /stop/${simulationId} returned ${resp.status} ${resp.statusText}`,
    );
  }
}

/**
 * Get the current status of a simulation.
 *
 * Returns `null` when the simulation API is not configured.
 */
export async function getSimulationStatus(
  simulationId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<SimulationStatus | null> {
  const base = getSimulationApiBase();
  if (!base) return null;

  const token = currentAuthToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetchImpl(
    simulationUrl(base, `/status/${encodeURIComponent(simulationId)}`),
    {
      method: "GET",
      headers,
      credentials: "omit",
    },
  );
  if (!resp.ok) {
    throw new Error(
      `simulation API /status/${simulationId} returned ${resp.status} ${resp.statusText}`,
    );
  }
  return (await resp.json()) as SimulationStatus;
}

/**
 * List active/recent simulations.
 *
 * Returns `null` when the simulation API is not configured.
 */
export async function listSimulations(
  fetchImpl: typeof fetch = fetch,
): Promise<SimulationListResponse | null> {
  const base = getSimulationApiBase();
  if (!base) return null;

  const token = currentAuthToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetchImpl(simulationUrl(base, "/list"), {
    method: "GET",
    headers,
    credentials: "omit",
  });
  if (!resp.ok) {
    throw new Error(
      `simulation API /list returned ${resp.status} ${resp.statusText}`,
    );
  }
  return (await resp.json()) as SimulationListResponse;
}

// ── Agent status ──────────────────────────────────────────────────────────────

/** Single agent entry as returned by GET /api/simulation/agent/status. */
export interface AgentEntry {
  readonly vin?: string;
  readonly vehicleName?: string;
  readonly status: string;
  [key: string]: unknown;
}

/** Response shape for GET /api/simulation/agent/status. */
export interface AgentStatusResponse {
  readonly agents: readonly AgentEntry[];
}

/**
 * Outcome of an agent-status probe.
 *
 * `httpStatus` is present ONLY on the failure paths, and is the difference
 * between "the simulator is offline" and "this caller was rejected" — two states
 * the caller previously could not tell apart, so it reported the first for both.
 * `null` means the request never completed (network error / abort), a number means
 * the gateway answered and refused.
 *
 * Deliberately absent on success so the existing strict `toEqual({ reachable,
 * agentRunning })` assertions keep describing the success contract exactly.
 */
export interface AgentStatusProbe {
  readonly reachable: boolean;
  readonly agentRunning: boolean;
  readonly httpStatus?: number | null;
}

/**
 * Check whether a FWE agent is running for the given VIN.
 *
 * Mirrors the pattern in CMS's VehicleDetailView.tsx:520-535: GET
 * /api/simulation/agent/status, look for an entry with
 * status RUNNING|PENDING|PROVISIONING matching `a.vin === vin` or
 * `a.vehicleName === vin`.
 *
 * Returns `null` when the simulation API is not configured (base is null) —
 * callers should treat null as "not running" (simReachable=false).
 *
 * Spec: `.kiro/specs/2026-09-15-cms-cs-campaign-ownership/tasks.md` Task 7.1
 */
export async function getAgentStatus(
  vin: string,
  fetchImpl: typeof fetch = fetch,
): Promise<AgentStatusProbe | null> {
  const base = getSimulationApiBase();
  if (!base) return null;

  const token = currentAuthToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  try {
    const resp = await fetchImpl(simulationUrl(base, "/agent/status"), {
      method: "GET",
      headers,
      credentials: "omit",
    });
    if (!resp.ok) {
      return { reachable: false, agentRunning: false, httpStatus: resp.status };
    }
    const d = (await resp.json()) as AgentStatusResponse;
    const agentRunning = (d.agents ?? []).some(
      (a) =>
        (a.status === "RUNNING" ||
          a.status === "PENDING" ||
          a.status === "PROVISIONING") &&
        (a.vin === vin || a.vehicleName === vin),
    );
    return { reachable: true, agentRunning };
  } catch {
    return { reachable: false, agentRunning: false, httpStatus: null };
  }
}


/** Outcome of an agent start/stop call. `null` means the API is not configured. */
export interface AgentToggleResult {
  readonly ok: boolean;
  /** HTTP status, or null when the request never completed (network/parse error). */
  readonly status: number | null;
  /** Server-supplied detail on failure, for surfacing to the operator. */
  readonly detail?: string;
}

/**
 * Start the FWE onboard agent for a VIN.
 *
 * Mirrors CMS's `VehicleDetailView.tsx:558` (`POST /api/simulation/agent/start`
 * with `{ vin, vehicleId }`). Delivers the "Agent controls ... are added to CS"
 * clause of spec `2026-09-15-cms-cs-campaign-ownership` § Decision 10, which was
 * specified but never decomposed into a task — see
 * `issues/2026-09-22-cs-simulate-missing-sim-console-and-agent-controls/`.
 *
 * CALLERS MUST GATE ON `dataSource === "vehicle-telemetry"`. A start against an
 * offboard/cloud vehicle is not a harmless no-op: the route provisions ECS/ASG
 * capacity and can reboot stale instances, so a spurious call bills real compute
 * for a vehicle with nothing to receive it. Do NOT copy CMS's gate, which uses
 * `!isOEM1` — the wrong field, and an open defect
 * (`issues/2026-09-18-start-agent-button-shown-for-offboard-vehicles/`).
 *
 * Returns `null` when the simulation API is not configured (base is null).
 */
export async function startAgent(
  vin: string,
  vehicleId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<AgentToggleResult | null> {
  return _toggleAgent("/agent/start", { vin, vehicleId }, fetchImpl);
}

/**
 * Stop the FWE onboard agent for a VIN.
 *
 * Sends `vehicleId` as well as `vin`, and that is load-bearing rather than
 * defensive. As of 2026-09-23 `/agent/stop` has two scopes: with a `vehicleId` it
 * stops only that vehicle's task and is authorized for a connected-services
 * caller on the allowlisted fleet; WITHOUT one it stops every agent task in the
 * cluster and is admin-only. Omitting it — which this function did, mirroring
 * CMS's vin-only call — sends a CS operator down the cluster-wide path, where
 * they are correctly refused with a 403.
 * See issues/2026-09-23-agent-routes-do-not-authorize-connected-services-callers/.
 *
 * Returns `null` when the simulation API is not configured (base is null).
 */
export async function stopAgent(
  vin: string,
  vehicleId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<AgentToggleResult | null> {
  return _toggleAgent("/agent/stop", { vin, vehicleId }, fetchImpl);
}

/**
 * Shared transport for the two agent-lifecycle calls.
 *
 * One function rather than two bodies for the same reason
 * `SIMULATION_ROUTE_PREFIX` is one constant: two copies are two chances for the
 * auth header, the `credentials` mode or the error handling to drift, and the
 * unit tests mock `fetch` — so a drift in headers is exactly the class of bug
 * they would still pass through.
 */
async function _toggleAgent(
  path: string,
  body: Record<string, string>,
  fetchImpl: typeof fetch,
): Promise<AgentToggleResult | null> {
  const base = getSimulationApiBase();
  if (!base) return null;

  const token = currentAuthToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  try {
    const resp = await fetchImpl(simulationUrl(base, path), {
      method: "POST",
      headers,
      credentials: "omit",
      body: JSON.stringify(body),
    });
    if (resp.ok) return { ok: true, status: resp.status };
    const detail = await resp.text().catch(() => "");
    return { ok: false, status: resp.status, detail: detail || undefined };
  } catch (e) {
    return { ok: false, status: null, detail: e instanceof Error ? e.message : undefined };
  }
}
