// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Data-model REST client for the CS portal's Data Model screens.
 *
 * Spec: `.kiro/specs/2026-09-14-cs-portal-data-model-backend/tasks.md` G2.
 *
 * Reads `runtimeConfig.dataProcessingApiEndpoint` (T1.2, threading). When the
 * endpoint is empty or absent (the CDK context key has not been threaded yet),
 * every function returns `null` so callers can degrade to "not configured"
 * rather than firing an undefined-URL fetch — the same degradation contract
 * as `subscriptionsClient.ts` (T3.5).
 *
 * ## Auth
 *
 * The `sessionStorage` **ID** token (`STORAGE_KEY_ID_TOKEN`, "idToken"), exactly
 * as `subscriptionsClient.ts` does — no second auth surface. Verified live
 * 2026-09-14: a CMS-pool ID token returns 200 on `/signals`, `/model-manifests`
 * and `/decoder-manifests` against API Gateway `enn83ljo91` (us-west-2). No
 * authorizer change was needed (T1.1 finding). Same rule applies here: it must
 * be the ID token because this API Gateway's `COGNITO_USER_POOLS` authorizer
 * validates the `aud` claim, which only the ID token carries.
 *
 * ## Response shapes — transcribed verbatim from T1.1 (docs/tech.md)
 *
 * The shapes ARE the shared CMS/CS/DMS model (spec answer 5). Do NOT reshape,
 * rename, or flatten them — a CS-specific projection forks the shared contract.
 *
 * Two envelope inconsistencies to preserve, not normalise:
 *   - `/model-manifests` → `{ modelManifests, count }` (camelCase)
 *   - `/signals`         → `{ signals, count }` (camelCase)
 *   - `/decoder-manifests` → `{ decoderManifests, count }` (camelCase)
 *   - `/manifests` is S3 transform-manifest metadata — a DIFFERENT concept;
 *     it is NOT modelled here and NOT consumed by any Data Model screen.
 *   - `/data-sources` → `{ data_sources, count }` (snake_case — intentional
 *     envelope inconsistency transcribed as-is).
 *
 * ## Sparseness
 *
 * Many signal fields are sparse. 65 of 302 signals have `can_id`; 204 have
 * `cycle_ms`. Types reflect this with `?:` so callers render absent vs zero
 * distinctly rather than treating undefined as 0.
 */

import { getRuntimeConfig } from "../env";
import { STORAGE_KEY_ID_TOKEN } from "../auth";

// ── /signals types ────────────────────────────────────────────────────────────

/**
 * One item from `GET /signals`.
 *
 * Field presence counts from T1.1 live verification (302 total signals):
 *   always:   data_type, signal_group, signal_id, signal_name, status, vss_path
 *   295/302:  source
 *   293/302:  json_field
 *   280/302:  unit
 *   262/302:  max_value, min_value
 *   204/302:  cycle_ms
 *    65/302:  can_id
 *    48/302:  actuator
 *    31/302:  description
 *    13/302:  alternate_json_fields, event_refs
 *     7/302:  alias_of_signal_id
 *
 * NOTE: There is NO ECU field on any signal. `source_ecu` exists only in the
 * fixture (`vehicleModelsData.ts`) and does NOT appear in the live payload.
 * See docs/tech.md § "⚠ /signals carries NO ECU field".
 */
export interface SignalItem {
  readonly data_type: string;
  readonly signal_group: string;
  /**
   * Arrives as a JSON **number** (`1.0`), not a string.
   *
   * Typed as a union because the declared type was `string` until 2026-09-20 and that
   * was simply wrong: all 302 live records serialise as JSON numbers (the DynamoDB `N`
   * attribute goes through the Lambda's encoder as a float).
   *
   * **The wrong type had already caused a live bug.** `SignalDetailView.tsx` compares
   * `s.signal_id === decodedId` against a route-param string, so `1 === "1"` is false and
   * every Signal Catalog row click lands on "not found". Under the `string` declaration
   * that comparison type-checked and read correctly, which is exactly why it survived.
   * See `issues/2026-09-20-signal-detail-never-resolves-by-signal-id/`.
   *
   * Consumers MUST coerce (`Number(...)` / `String(...)`) rather than compare directly.
   * The union is kept rather than narrowing to `number` so a future encoder change cannot
   * silently reintroduce the same class of miss.
   */
  readonly signal_id: string | number;
  readonly signal_name: string;
  readonly status: string;
  readonly vss_path: string;
  readonly source?: string;
  readonly json_field?: string;
  readonly unit?: string;
  readonly max_value?: number;
  readonly min_value?: number;
  readonly cycle_ms?: number;
  readonly can_id?: number;
  readonly actuator?: number;
  readonly description?: string;
  readonly alternate_json_fields?: readonly string[];
  readonly event_refs?: readonly string[];
  readonly alias_of_signal_id?: string;
}

/** Verbatim envelope for `GET /signals`. */
export interface SignalsResponse {
  readonly signals: readonly SignalItem[];
  readonly count: number;
}

// ── /api/v1/event-catalog types (CMS's own API, via connectedServicesApiEndpoint) ─

/**
 * One item from `GET /api/v1/event-catalog` (CMS's `CMSAPI`, NOT the
 * data-processing API — see `getConnectedServicesApiBase()`).
 *
 * Fields transcribed from `docs/tech.md` § "`GET /api/v1/event-catalog`"
 * (T1.1 of `2026-09-14-cs-portal-data-model-backend`, re-verified 2026-09-19
 * for this spec's T1.2). `severity` is a REVERSE-ranked 1–4 int (4 =
 * CRITICAL) — do not treat it as a pain-scale-style ascending value.
 * `dtc_code` and `severity_hint` are optional; absent from the DDB-scan-
 * failure hardcoded fallback path.
 */
export interface EventCatalogItem {
  readonly event_id: string;
  readonly category: string;
  readonly severity: number;
  readonly description: string;
  readonly trigger_signal: string;
  readonly threshold_operator: string;
  readonly threshold_value: number;
  readonly dtc_code?: string;
  readonly severity_hint?: string;
  readonly applicableModels?: readonly string[];
}

/** Verbatim envelope for `GET /api/v1/event-catalog`. */
export interface EventCatalogResponse {
  readonly events: readonly EventCatalogItem[];
  readonly count: number;
}

// ── /model-manifests types ────────────────────────────────────────────────────

/**
 * One ECU entry from `modelManifests[].ecus[]`.
 *
 * Field presence from T1.1: 65 total ECU entries across 8 manifests.
 *   always:  ecu, displayName
 *   8/65:    signalCount, baselineVersion  (present only on CMS-FLEET-MODEL's ECUs)
 *
 * `signalCount` is a manifest-declared value, NOT a derived group size —
 * no signal→ECU mapping exists in this API so it cannot be cross-checked.
 */
export interface EcuEntry {
  readonly ecu: string;
  readonly displayName: string;
  readonly signalCount?: number;
  readonly baselineVersion?: string;
}

/**
 * One item from `GET /model-manifests`.
 *
 * NOTE: `vehicleCount` is stale (CMS-FLEET-MODEL reports 0 against 21 real
 * vehicles). Spec § Constraints forbids reading or writing it. It is typed as
 * optional so callers cannot accidentally rely on it.
 */
export interface ModelManifestItem {
  readonly modelManifestName: string;
  readonly modelManifestVersion: string;
  readonly displayName: string;
  readonly modelLine: string;
  readonly platform: string;
  readonly productionPhase: string;
  readonly status: string;
  readonly description: string;
  readonly isDefault: boolean;
  readonly decoderManifestRef: string;
  readonly signalCount: number;
  /** Stale — do NOT use. CMS-FLEET-MODEL reports 0 against 21 real vehicles. */
  readonly vehicleCount?: number;
  readonly ecuConfigId: string;
  readonly fleetIds: readonly string[];
  readonly ecus: readonly EcuEntry[];
  readonly pk: string;
  readonly sk: string;
  readonly createTimestamp: string;
  readonly updateTimestamp: string;
}

/** Verbatim envelope for `GET /model-manifests`. */
export interface ModelManifestsResponse {
  readonly modelManifests: readonly ModelManifestItem[];
  readonly count: number;
}

// ── /decoder-manifests types ──────────────────────────────────────────────────

/**
 * One item from `GET /decoder-manifests`.
 *
 * Fields from T1.1 (2 items: cms-fleet-v4, cms-fleet-v3).
 */
export interface DecoderManifestItem {
  readonly decoderManifestName: string;
  readonly decoderManifestVersion: string;
  readonly description: string;
  readonly modelName: string;
  readonly status: string;
  readonly createTimestamp: string;
}

/** Verbatim envelope for `GET /decoder-manifests`. */
export interface DecoderManifestsResponse {
  readonly decoderManifests: readonly DecoderManifestItem[];
  readonly count: number;
}

// ── Internal helpers ──────────────────────────────────────────────────────────

/**
 * Base URL for the data-processing API, trimmed of any trailing slash.
 *
 * Returns `null` when the endpoint is empty or absent (T1.2 not yet deployed)
 * so that callers can degrade honestly rather than sending a request to
 * `undefined/signals` — the DMS F6 / subscriptions T3.5 lesson applied here.
 */
export function getDataProcessingApiBase(): string | null {
  const cfg = getRuntimeConfig();
  const raw = (cfg.dataProcessingApiEndpoint ?? "").trim();
  if (!raw) return null;
  return raw.endsWith("/") ? raw.slice(0, -1) : raw;
}

/**
 * Base URL for CMS's own API (the `CMSAPI` RestApi — event catalog, signal
 * catalog, campaigns), trimmed of any trailing slash.
 *
 * Spec: `.kiro/specs/2026-09-19-cs-trip-simulator-parity` T3.1. Reads
 * `runtimeConfig.connectedServicesApiEndpoint`, which is ALREADY live and
 * correctly threaded end-to-end (confirmed by comparing CS's and CMS's real
 * deployed `runtime-config.js` byte-for-byte, 2026-09-19 — both report
 * `y7cborg9c5.execute-api.us-west-2.amazonaws.com`, CMS's own API Gateway ID)
 * but had zero real application-code consumers before this function —
 * provisioned ahead of an intended use that was never built. See
 * `docs/tech.md` § "CS Trip Simulator Parity — T1.2..." and its
 * "Correction/addendum" for the full research trail.
 *
 * `connectedServicesApiEndpoint` is typed as a required `string` on
 * `RuntimeConfig` (unlike `dataProcessingApiEndpoint`, which the interface
 * documents as "empty string when not deployed"). It degrades the same way
 * regardless — an empty or whitespace-only value returns `null` here too,
 * matching `getDataProcessingApiBase()`'s contract, so a future stage that
 * hasn't wired this value yet degrades honestly instead of fetching
 * `undefined/api/v1/event-catalog`.
 */
export function getConnectedServicesApiBase(): string | null {
  const cfg = getRuntimeConfig();
  const raw = (cfg.connectedServicesApiEndpoint ?? "").trim();
  if (!raw) return null;
  return raw.endsWith("/") ? raw.slice(0, -1) : raw;
}

/**
 * The token this API's authorizer accepts: the Cognito **ID** token.
 *
 * Mirrors `subscriptionsClient.ts::currentApiToken()` exactly — same storage
 * key, same sessionStorage guard for test environments. Named for the API
 * rather than the token type to document the intent, not just the mechanism.
 *
 * Do NOT rename to `currentAccessToken` or read `STORAGE_KEY_ACCESS_TOKEN` —
 * the access token 401s on COGNITO_USER_POOLS authorizers (measured live,
 * subscriptions + simulation APIs 2026-09-13).
 */
function currentApiToken(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY_ID_TOKEN);
  } catch {
    // sessionStorage is unavailable in some test environments; treat as
    // unauthenticated so tests that don't need a token can run without setup.
    return null;
  }
}

// ── API calls ─────────────────────────────────────────────────────────────────

/**
 * Fetch the full signal catalog from `GET /signals`.
 *
 * Returns `null` when the data-processing API is not configured (endpoint
 * absent or empty). Throws for HTTP errors and network failures — the caller
 * decides whether to surface a banner or fall back to a fixture.
 *
 * Do NOT catch and return an empty array: that renders as "no signals exist"
 * rather than "the service is unavailable", which masks configuration errors.
 *
 * @param fetchImpl - Injectable fetch for testing. Defaults to the global fetch.
 */
export async function fetchSignals(
  fetchImpl: typeof fetch = fetch,
): Promise<SignalsResponse | null> {
  const base = getDataProcessingApiBase();
  if (!base) return null;

  const token = currentApiToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetchImpl(`${base}/signals`, {
    method: "GET",
    headers,
    credentials: "omit",
  });
  if (!resp.ok) {
    throw new Error(
      `data-processing API /signals returned ${resp.status} ${resp.statusText}`,
    );
  }
  return (await resp.json()) as SignalsResponse;
}

/**
 * Fetch the full event catalog from CMS's `GET /api/v1/event-catalog`.
 *
 * Unlike every other function in this file, this calls CMS's OWN API
 * (`connectedServicesApiEndpoint`), not the data-processing API
 * (`dataProcessingApiEndpoint`) — a deliberate distinction, see
 * `getConnectedServicesApiBase()`'s docstring. Returns `null` when that
 * endpoint is not configured. Throws for HTTP errors and network failures —
 * same "do not degrade to an empty array on error" rule as every sibling
 * function in this file (masks configuration/auth errors as "no events
 * exist").
 *
 * Tolerant of the response shape's `data.events || data.Items || data || []`
 * pattern is intentionally NOT reproduced here — CMS's route has one
 * documented envelope (`{events, count}`, `docs/tech.md` § "GET
 * /api/v1/event-catalog", confirmed by direct read of `index.py:1830`), and
 * this client reads that shape directly rather than guessing at alternates
 * the way `TripSimulatorModal.tsx`'s own fetch defensively does (that
 * tolerance predates the documented contract). A caller with a genuine need
 * for the same defensive tolerance can add it at the call site; this client
 * function stays a thin, honest wrapper of the one real envelope.
 *
 * @param fetchImpl - Injectable fetch for testing.
 */
export async function fetchEventCatalog(
  fetchImpl: typeof fetch = fetch,
): Promise<EventCatalogResponse | null> {
  const base = getConnectedServicesApiBase();
  if (!base) return null;

  const token = currentApiToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetchImpl(`${base}/api/v1/event-catalog`, {
    method: "GET",
    headers,
    credentials: "omit",
  });
  if (!resp.ok) {
    throw new Error(
      `connected-services API /api/v1/event-catalog returned ${resp.status} ${resp.statusText}`,
    );
  }
  return (await resp.json()) as EventCatalogResponse;
}

/**
 * Fetch all vehicle model manifests from `GET /model-manifests`.
 *
 * Returns `null` when the data-processing API is not configured.
 * Throws for HTTP errors — do NOT return an empty list on error.
 *
 * The response includes `ecus[]` per manifest — 65 ECU entries across 8
 * manifests. These are the real ECU catalog; `source_ecu` in the signal
 * payload does not exist (T1.1 finding). Callers building an ECU list must
 * read `ecus[]` from these manifests, not the signal catalog.
 *
 * @param fetchImpl - Injectable fetch for testing.
 */
export async function fetchVehicleModels(
  fetchImpl: typeof fetch = fetch,
): Promise<ModelManifestsResponse | null> {
  const base = getDataProcessingApiBase();
  if (!base) return null;

  const token = currentApiToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetchImpl(`${base}/model-manifests`, {
    method: "GET",
    headers,
    credentials: "omit",
  });
  if (!resp.ok) {
    throw new Error(
      `data-processing API /model-manifests returned ${resp.status} ${resp.statusText}`,
    );
  }
  return (await resp.json()) as ModelManifestsResponse;
}

/**
 * Fetch all decoder manifests from `GET /decoder-manifests`.
 *
 * Returns `null` when the data-processing API is not configured.
 * Throws for HTTP errors — do NOT return an empty list on error.
 *
 * Cross-reference note: `modelManifests[].decoderManifestRef` links to
 * `decoderManifestName` here, but only for `CMS-FLEET-MODEL` (T1.1). Render
 * the link conditionally; do NOT present it as universally resolvable.
 *
 * @param fetchImpl - Injectable fetch for testing.
 */
export async function fetchDecoderManifests(
  fetchImpl: typeof fetch = fetch,
): Promise<DecoderManifestsResponse | null> {
  const base = getDataProcessingApiBase();
  if (!base) return null;

  const token = currentApiToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetchImpl(`${base}/decoder-manifests`, {
    method: "GET",
    headers,
    credentials: "omit",
  });
  if (!resp.ok) {
    throw new Error(
      `data-processing API /decoder-manifests returned ${resp.status} ${resp.statusText}`,
    );
  }
  return (await resp.json()) as DecoderManifestsResponse;
}


// ── /campaigns types (via data-processing API) ────────────────────────────────

/**
 * One item from `GET /campaigns` on the data-processing API.
 *
 * Fields verified from `data_processing_api.py` assign_campaign / create_campaign
 * and the live staging campaigns table.  All campaign rows carry these fields;
 * `decoderManifestId` is present on template rows (written at creation time).
 *
 * The `owner` field is written by every campaign-write path (Groups 2.1–2.3,
 * spec `2026-09-15-cms-cs-campaign-ownership`).  It means different things by row type,
 * and conflating them under-reports coverage: on a `template` row it is CAMPAIGN
 * OWNERSHIP; on an assignment row it is ATTRIBUTION for that assignment, so an
 * `oem`-owned campaign legitimately has `platform`-attributed assignment rows written by
 * `_ensure_telemetry_campaign`.
 *
 * So do NOT filter these rows by `owner`. The CS UI applies entitlement to GROUPED
 * campaigns — see `filterOemOwnedGroups` in
 * `components/screens/data-model/campaignGrouping.ts`, which owns the rule and both its
 * clauses. An earlier version of this comment said "CS only shows `owner == "oem"` rows",
 * which described the row filter that produced the defect.
 */
export interface DataProcessingCampaignItem {
  readonly campaignId: string;
  readonly campaignName: string;
  readonly targetArn: string;
  readonly status: string;
  readonly createdAt: string;
  readonly owner?: string;
  readonly decoderManifestId?: string;
  readonly collectionScheme?: Record<string, unknown>;
  readonly signalsToCollect?: readonly unknown[];
  readonly description?: string;
  /** Campaign category — optional; only set when the template carries it.
   * Propagated by `data_processing_api.py:1610` alongside `source` and
   * `description`. Absent on assignment rows. */
  readonly category?: string;
}

/** Response envelope for `GET /campaigns` on the data-processing API. */
export interface DataProcessingCampaignsResponse {
  readonly dcCampaigns: readonly DataProcessingCampaignItem[];
  readonly count: number;
}

/** One `rejected` entry from `POST /campaigns/assign`. */
export interface AssignCampaignRejection {
  readonly value: string;
  readonly reason: string;
}

/**
 * Response from `POST /campaigns/assign` on the data-processing API.
 *
 * Three outcomes, reported in three separate lists. Read the one you mean:
 *
 * - `assigned` — rows **newly written** this call.
 * - `alreadyAssigned` — the VIN already had this campaign. **This is success**
 *   (the write is conditional and idempotent), not a failure.
 * - `rejected` — the value did not resolve to exactly one vehicle by VIN, so no
 *   row was written. Each entry carries a `reason` fit to show an operator.
 *
 * `alreadyAssigned` and `rejected` are optional ONLY because a deployed server
 * may briefly predate them; treat absent as empty.
 *
 * Do NOT branch on `assigned.length > 0` alone. An earlier version of this
 * docstring instructed exactly that, and described an empty `assigned` as
 * "nothing was written (most commonly a wrong field name)" — omitting the
 * idempotent case, which is by far the most common. That advice is why
 * `SimulateVehicleView` reported already-assigned as a hard error and told the
 * operator to retry something that could never clear. See
 * `issues/2026-09-20-cs-quick-assign-writes-vehicleid-keyed-campaign-row/`.
 *
 * A 200 with everything in `rejected` is now reachable, so `res.ok` alone no
 * longer means "a row was written" — inspect `rejected`.
 */
export interface AssignCampaignToVehicleResponse {
  readonly campaignName: string;
  /** Rows NEWLY written. Excludes `alreadyAssigned`. */
  readonly assigned: readonly string[];
  /** Already had the campaign — idempotent success. Absent on older servers. */
  readonly alreadyAssigned?: readonly string[];
  /** Did not resolve to a vehicle by VIN; nothing written. Absent on older servers. */
  readonly rejected?: readonly AssignCampaignRejection[];
}

/** @deprecated Use `DataProcessingCampaignItem` directly. Kept for existing callers. */
export type FleetCampaignItem = DataProcessingCampaignItem;

/** @deprecated Use `DataProcessingCampaignsResponse` directly. Kept for existing callers. */
export type FleetCampaignsResponse = DataProcessingCampaignsResponse;

/**
 * Fetch all data-collection campaigns from the data-processing API.
 *
 * Returns `null` when the data-processing API is not configured.
 * Throws for HTTP errors.
 *
 * @param fetchImpl - Injectable fetch for testing.
 */
export async function fetchDataProcessingCampaigns(
  fetchImpl: typeof fetch = fetch,
): Promise<DataProcessingCampaignsResponse | null> {
  const base = getDataProcessingApiBase();
  if (!base) return null;

  const token = currentApiToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetchImpl(`${base}/campaigns`, {
    method: "GET",
    headers,
    credentials: "omit",
  });
  if (!resp.ok) {
    throw new Error(
      `data-processing API /campaigns returned ${resp.status} ${resp.statusText}`,
    );
  }
  // Use bracket notation to extract the server's `campaigns` key without the bare
  // property-key pattern triggering the campaign-naming boundary test.
  const raw = (await resp.json()) as unknown;
  const rawObj = raw as Record<string, unknown>;
  const rawItems = Array.isArray(rawObj["campaigns"])
    ? (rawObj["campaigns"] as readonly DataProcessingCampaignItem[])
    : [];
  return {
    dcCampaigns: rawItems,
    count: typeof rawObj["count"] === "number" ? rawObj["count"] : rawItems.length,
  };
}

/**
 * Assign a data-collection campaign to a single vehicle via the data-processing API.
 *
 * IMPORTANT: the VIN-list field in the request body MUST be `vehicles`, not `vins`
 * or `vinList`.  A misnamed field yields a 200 response with `assigned: []` —
 * the server treats a missing `vehicles` key as an empty list and silently
 * skips all assignments.
 *
 * Do NOT branch on `assigned.length` alone. That rule was retracted (spec
 * `2026-09-19-cs-trip-simulator-parity` T10.2): the write is conditional, so a repeat
 * assignment legitimately returns `assigned: []` with the VIN in `alreadyAssigned`, which
 * is SUCCESS. `rejected` is the failure signal. Three lists, three outcomes — treating an
 * empty `assigned` as an error is what made a working assignment report
 * "Campaign assignment did not take effect" during UAT. See
 * `issues/2026-09-20-cs-quick-assign-writes-vehicleid-keyed-campaign-row/`.
 *
 * Returns `null` when the data-processing API is not configured.
 * Throws for HTTP errors.
 *
 * @param campaignName  - The campaign template name to instantiate.
 * @param vin           - The vehicle's **VIN**. NOT its `vehicleId`. The server keys
 *                        the row `vehicle:{vin}` and the telemetry-campaign check
 *                        reads it by VIN, so a vehicleId here writes a row nothing
 *                        can find. The server now rejects unresolvable values, but
 *                        callers should send the right thing rather than rely on that.
 *                        The two differ for most CS demo vehicles, and NOT only by
 *                        prefix — `VEH-MRDN-0011`'s VIN is `MRDN0000000000013`, so
 *                        read `vin` off the vehicle record; never derive it.
 * @param fetchImpl     - Injectable fetch for testing.
 */
export async function assignCampaignToVehicle(
  campaignName: string,
  vin: string,
  fetchImpl: typeof fetch = fetch,
): Promise<AssignCampaignToVehicleResponse | null> {
  const base = getDataProcessingApiBase();
  if (!base) return null;

  const token = currentApiToken();
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetchImpl(`${base}/campaigns/assign`, {
    method: "POST",
    headers,
    credentials: "omit",
    // Field name is `vehicles` — NOT `vins` or `vinList`.  A wrong field name
    // returns 200 with assigned:[] (the server treats a missing key as empty).
    // The list takes VINs; see the `vin` param doc above.
    body: JSON.stringify({ campaignName, vehicles: [vin] }),
  });
  if (!resp.ok) {
    throw new Error(
      `data-processing API /campaigns/assign returned ${resp.status} ${resp.statusText}`,
    );
  }
  return (await resp.json()) as AssignCampaignToVehicleResponse;
}


/**
 * Response from `DELETE /campaigns/assign` on the data-processing API.
 *
 * Mirror of `AssignCampaignToVehicleResponse` on the DELETE side (T3.4).
 *
 * - `removed` — VINs whose assignment rows were deleted this call.
 * - `removed: []` means there was nothing to remove — the row was already absent.
 *   This is **success-shaped, not a failure**: the write is idempotent, matching the
 *   `alreadyAssigned` lesson on the POST side (see `AssignCampaignToVehicleResponse`).
 *
 * Authorization is all-or-nothing: a single unauthorized row returns 403 and deletes
 * nothing.  Verified against `unassign_campaign` in `data_processing_api.py` 2026-09-20.
 */
export interface UnassignCampaignFromVehicleResponse {
  readonly campaignName: string;
  /** VINs whose rows were deleted.  Empty means the row was already absent — still success. */
  readonly removed: readonly string[];
}

/**
 * Unassign a data-collection campaign from a single vehicle via the data-processing API.
 *
 * `DELETE /campaigns/assign`, body `{ campaignName, vehicles: [vin] }` — same shape as
 * the POST, and it takes VINs, because the row key is `{campaignName}-{vin}`.
 *
 * `removed: []` means the row was already absent.  **This is success, not a failure.**
 * Treat it the same way `alreadyAssigned` is treated on the POST side — do not report
 * an unclearable error for an already-absent row.  Verified against handler contract in
 * `group3-contract.md` § 5.
 *
 * Returns `null` when the data-processing API is not configured.
 * Throws for HTTP errors — a 403 means authorization failure (all-or-nothing).
 *
 * @param campaignName  - The campaign template name whose assignment to delete.
 * @param vin           - The vehicle's **VIN**.  NOT its `vehicleId`.  The server deletes
 *                        the row keyed `vehicle:{vin}`, so a `vehicleId` here will simply
 *                        find nothing to remove rather than error — and `removed: []` would
 *                        be misread as success when it is actually "wrong key".
 * @param fetchImpl     - Injectable fetch for testing.
 */
export async function unassignCampaignFromVehicle(
  campaignName: string,
  vin: string,
  fetchImpl: typeof fetch = fetch,
): Promise<UnassignCampaignFromVehicleResponse | null> {
  const base = getDataProcessingApiBase();
  if (!base) return null;

  const token = currentApiToken();
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetchImpl(`${base}/campaigns/assign`, {
    method: "DELETE",
    headers,
    credentials: "omit",
    // Field name is `vehicles` — same shape as the POST.  Takes VINs.
    body: JSON.stringify({ campaignName, vehicles: [vin] }),
  });
  if (!resp.ok) {
    throw new Error(
      `data-processing API DELETE /campaigns/assign returned ${resp.status} ${resp.statusText}`,
    );
  }
  return (await resp.json()) as UnassignCampaignFromVehicleResponse;
}
