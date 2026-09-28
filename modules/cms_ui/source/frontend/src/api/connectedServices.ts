// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Connected Services subscription-feed client (spec
 * `2026-09-10-cms-connected-services-consumer`, T2.4).
 *
 * Calls CMS's OWN three proxy routes and nothing else. The producer's API is
 * never addressed from the browser — spec.md D5 states this as a hard
 * constraint, not a style preference: CMS's subscriber credential is held
 * server-side in Secrets Manager, and a browser that could reach the producer
 * directly with it would be able to act as CMS's subscriber account against the
 * producer, bypassing CMS's own fleet-scope check entirely.
 *
 * That constraint is enforced two ways, because a comment is not a control:
 *   - this module has no producer-endpoint symbol to import (there is no
 *     `runtimeConfig` key for one, by design), and
 *   - `src/__tests__/connectedServicesBundleHygiene.test.ts` builds the real
 *     bundle and greps the emitted assets.
 *
 * Error shapes are a DISCRIMINATED UNION, not a single `Error`. The card must
 * render "we could not read the feed" differently from "this vehicle has no
 * telemetry": index.py's `_cs_filter_feed` docstring and the proxy's R1 both
 * exist to prevent exactly that substitution, and collapsing the classes here
 * would reintroduce it one layer up.
 */

import { authFetch } from '@/utils/authFetch';
import { getApiEndpoint } from '@/config/api';

/** Path suffix, appended to `getApiEndpoint()`'s trailing-slash base. */
export const CS_FEED_PATH = 'api/v1/connected-services/subscription-feed';

/** Producer error codes CMS's proxy re-emits verbatim in `body.error`. */
export const CS_ERROR_CONFIG_MISSING = 'config_missing';
export const CS_ERROR_PRODUCER_UNAVAILABLE = 'producer_unavailable';
export const CS_ERROR_PRODUCER_UNAUTHORIZED = 'producer_unauthorized';
export const CS_ERROR_PRODUCER_SHAPE_DRIFT = 'producer_shape_drift';

/**
 * One telemetry record.
 *
 * `timestamp` is **epoch milliseconds** (e.g. 1789245554121), not ISO 8601 and
 * not epoch seconds — captured from the live producer response, not inferred
 * from prose. `vehicleId` is optional on purpose: it is deliberately absent
 * from the proxy's `_RECORD_REQUIRED_KEYS` so one odd record cannot black out
 * the whole feed, which means the frontend cannot rely on it either.
 */
export interface ConnectedServicesRecord {
  vin: string;
  vehicleId?: string;
  signals: Record<string, unknown>;
  dataSourceRoute: string;
  timestamp: number;
}

export interface ConnectedServicesFeed {
  subscription_id: string;
  records: ConnectedServicesRecord[];
  count: number;
  /**
   * The VINs the caller may see in this subscription's scope.
   *
   * For an UNSCOPED caller (platform-admin / fleet-viewer) this is the
   * subscription's true scope. For a fleet-scoped operator `_cs_filter_feed`
   * narrows it to the VINs of records that survived the fleet filter — so a
   * VIN that is genuinely enrolled but has not yet produced a record is NOT
   * in this list for a scoped caller. `deriveEnrollment` documents what that
   * costs and how the card compensates.
   */
  vins_in_scope: string[];
  /** In scope, resolves to no CMS vehicle. Always `[]` for a scoped caller. */
  unresolved_vins: string[];
  quota: Record<string, unknown>;
  /**
   * Present only when the feed was served from CMS's server-side cache (T2.6).
   * ISO-8601. Absent means this response came straight from the producer.
   *
   * Rendered, not swallowed: a cached feed presented as live is a correctness
   * bug, not a cosmetic one — the same reason a Tier 2 artifact carries
   * `computed_at`. The card shows "as of <cached_at>" so an operator can tell
   * how old the answer is.
   */
  cached_at?: string;
}

export interface ScopeAddResult {
  added: string[];
  already_present: string[];
  scope_size: number;
}

export interface ScopeRemoveResult {
  /** `false` means the VIN was already absent — a normal no-op, not a failure. */
  removed: boolean;
  scope_size: number;
}

/**
 * Why a Connected Services call did not return data.
 *
 * `notConfigured` is a SUPPORTED state, not an exception: a stage with no
 * producer deployed sets `CS_PRODUCER_API_ENDPOINT` to '' by design and the
 * route answers 502 `config_missing`. It must not render as "no telemetry".
 */
export type ConnectedServicesFailureKind =
  | 'notConfigured'
  | 'producerUnavailable'
  | 'producerUnauthorized'
  | 'shapeDrift'
  | 'forbidden'
  | 'invalidVin'
  | 'network'
  | 'unknown';

export class ConnectedServicesError extends Error {
  constructor(
    message: string,
    public readonly kind: ConnectedServicesFailureKind,
    public readonly statusCode: number,
  ) {
    super(message);
    this.name = 'ConnectedServicesError';
  }
}

function feedUrl(vin?: string): string {
  const base = getApiEndpoint();
  const withSlash = base.endsWith('/') || base === '' ? base : `${base}/`;
  return vin ? `${withSlash}${CS_FEED_PATH}/${encodeURIComponent(vin)}` : `${withSlash}${CS_FEED_PATH}`;
}

/**
 * Map an HTTP status + parsed body onto a failure kind.
 *
 * Keyed on `body.error` first because the proxy's four 502 sub-classes are
 * distinguishable ONLY by that field, and they need different copy: one says
 * "this stage has no producer", another says "we could not parse the feed".
 * Both would otherwise become "502".
 */
function classify(status: number, body: unknown): ConnectedServicesFailureKind {
  const code =
    body && typeof body === 'object' && typeof (body as { error?: unknown }).error === 'string'
      ? ((body as { error: string }).error)
      : undefined;

  if (code === CS_ERROR_CONFIG_MISSING) return 'notConfigured';
  if (code === CS_ERROR_PRODUCER_SHAPE_DRIFT) return 'shapeDrift';
  if (code === CS_ERROR_PRODUCER_UNAVAILABLE) return 'producerUnavailable';
  if (code === CS_ERROR_PRODUCER_UNAUTHORIZED) return 'producerUnauthorized';
  if (status === 403) return 'forbidden';
  if (status === 400) return 'invalidVin';
  return 'unknown';
}

async function parseJsonSafely(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

function detailOf(body: unknown, fallback: string): string {
  if (body && typeof body === 'object') {
    const b = body as { detail?: unknown; error?: unknown; message?: unknown };
    for (const candidate of [b.detail, b.message, b.error]) {
      if (typeof candidate === 'string' && candidate.length > 0) return candidate;
    }
  }
  return fallback;
}

async function call<T>(url: string, method: 'GET' | 'POST' | 'DELETE'): Promise<T> {
  let response: Response;
  try {
    response = await authFetch(url, { method });
  } catch {
    throw new ConnectedServicesError(
      'Network error contacting the Connected Services feed',
      'network',
      0,
    );
  }

  const body = await parseJsonSafely(response);

  if (!response.ok) {
    throw new ConnectedServicesError(
      detailOf(body, `Request failed: ${response.status}`),
      classify(response.status, body),
      response.status,
    );
  }

  return body as T;
}

/** `GET` the feed. Fleet-scope filtering happens server-side (index.py). */
export async function getSubscriptionFeed(): Promise<ConnectedServicesFeed> {
  return call<ConnectedServicesFeed>(feedUrl(), 'GET');
}

/** `POST` — enroll one VIN into CMS's subscription scope. */
export async function enrollVin(vin: string): Promise<ScopeAddResult> {
  return call<ScopeAddResult>(feedUrl(vin), 'POST');
}

/** `DELETE` — unenroll one VIN from CMS's subscription scope. */
export async function unenrollVin(vin: string): Promise<ScopeRemoveResult> {
  return call<ScopeRemoveResult>(feedUrl(vin), 'DELETE');
}

export interface EnrollmentView {
  enrolled: boolean;
  /** Newest record for this VIN, or `null`. Epoch-ms `timestamp`. */
  latestRecord: ConnectedServicesRecord | null;
  recordCount: number;
  /** In scope but resolving to no CMS vehicle — a state, not an error. */
  unresolved: boolean;
  /** Set when the feed came from CMS's cache. ISO-8601. */
  cachedAt?: string;
}

/**
 * Reduce a whole-subscription feed to one VIN's enrollment view.
 *
 * Enrollment is `vins_in_scope ∪ {r.vin}` rather than `vins_in_scope` alone.
 * The union is load-bearing, not defensive: for a fleet-scoped operator
 * `_cs_filter_feed` narrows `vins_in_scope` to the VINs of surviving records,
 * so a VIN enrolled seconds ago with no telemetry yet is absent from it. Reading
 * enrollment from that field alone would make a SUCCESSFUL enroll render as
 * "not enrolled" until the first record lands — indistinguishable, to the
 * operator, from the enroll having failed.
 *
 * The union does not fully close that gap (a scoped operator still cannot
 * observe a record-less enrollment after a page reload); the card covers the
 * live case by trusting the enroll response, and the residual gap is recorded
 * in the spec's `decisions.md` rather than hidden here.
 */
export function deriveEnrollment(
  feed: ConnectedServicesFeed,
  vin: string,
): EnrollmentView {
  const mine = (feed.records ?? []).filter((r) => r?.vin === vin);
  const latest = mine.reduce<ConnectedServicesRecord | null>(
    (best, r) => (best === null || (r.timestamp ?? 0) > (best.timestamp ?? 0) ? r : best),
    null,
  );
  const inScope = (feed.vins_in_scope ?? []).includes(vin);
  return {
    enrolled: inScope || mine.length > 0,
    latestRecord: latest,
    recordCount: mine.length,
    unresolved: (feed.unresolved_vins ?? []).includes(vin),
    cachedAt: feed.cached_at,
  };
}
