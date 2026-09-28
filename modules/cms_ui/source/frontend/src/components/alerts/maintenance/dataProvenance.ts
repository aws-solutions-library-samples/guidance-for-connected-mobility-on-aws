// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Data provenance for the merged Maintenance view.
//
// Spec `2026-09-02-cms-dms-service-convergence` T4.3: "Rows sourced from cache
// are labelled as cached with their age; DMS-unreachable renders an explicit
// state, never silent staleness presented as live."
//
// ## The defect this closes exists TODAY, before Group 5
//
// `ServiceDashboard.fetchAlerts` merges two sources under `Promise.allSettled`
// and, when one leg fails, logs to the console and renders the other leg's rows
// anyway. The comment there calls it a "partial-success path", and as a data
// strategy it is right — half a view beats none. What is wrong is that the
// operator is not told. The table renders a row count, a filter label and a KPI
// header that all describe a complete answer, while service history silently
// contributed nothing.
//
// That is the same class as the fabricated dealer column this spec removed: a UI
// asserting a fact it does not have. There the invented fact was a dealer name;
// here it is completeness.
//
// ## Why "no metadata" means live rather than unknown
//
// Today CMS's `service-history` table IS the primary store, so an unlabelled
// response genuinely is live data and saying so is accurate. Spec D1 makes that
// table a read cache in Group 5 (T5.2), at which point the GET will start
// declaring `dataSource: "cache"` and `asOf`, and this module labels it without
// further change. The optional fields are the seam between the two groups, which
// is why they are defined here and not inline at the call site.
//
// Deliberately NOT defaulting to "cached, age unknown": that would label every
// row in the current deployment as stale, which is false, and a UI that cries
// stale on fresh data trains its reader to ignore the label.

/** Which source a leg of the merge came from, and whether it succeeded. */
export type LegStatus = "ok" | "failed";

/** How fresh a successful leg's data is. */
export type Freshness =
  /** Read from the authoritative store at request time. */
  | "live"
  /** Served from CMS's cache because the upstream was unreachable (D5). */
  | "cached"
  /**
   * Some rows are live, some are served from cache (spec
   * `2026-09-10-service-history-read-path-correctness` D3).  The envelope
   * declares ``dataSource: "mixed"`` and carries ``cacheOnlyCount`` naming how
   * many rows in this payload are cache-only; ``asOf`` is documented as the
   * OLDEST cache-only row's timestamp — worst-case staleness the user should
   * see, not average.
   *
   * Historically legFromBody defaulted an unrecognised value to live.  That
   * default is correct (see module header) and is preserved, but "mixed" now
   * has its own branch — the risk R1 in that spec: without this branch a
   * payload containing stale rows would render as fully live.
   */
  | "partial";

export interface LegProvenance {
  status: LegStatus;
  /** Only meaningful when status === "ok". */
  freshness: Freshness;
  /**
   * ISO timestamp the cached data was captured.  For ``freshness === "partial"``
   * this is the OLDEST cache-only row's ``cachedAt`` — worst-case staleness.
   * Empty unless cached or partial.
   */
  asOf: string;
  /**
   * Number of cache-only rows in a mixed payload.  Undefined for pure live or
   * pure cached legs.  Read from ``ProvenanceEnvelope.cacheOnlyCount``.
   */
  cacheOnlyCount?: number;
  /** Why the leg failed. Empty unless status === "failed". */
  reason: string;
}

export interface MergeProvenance {
  alerts: LegProvenance;
  serviceHistory: LegProvenance;
}

/** Optional provenance envelope a response may declare. Absent today. */
export interface ProvenanceEnvelope {
  dataSource?: string;
  asOf?: string;
  /**
   * How many rows in this payload are cache-only.  Set by the CMS handler on
   * every response (including 0 for pure live), so a UI can distinguish
   * "we don't know" from "explicitly zero cache-only rows".
   */
  cacheOnlyCount?: number;
}

export function liveLeg(): LegProvenance {
  return { status: "ok", freshness: "live", asOf: "", reason: "" };
}

export function failedLeg(reason: string): LegProvenance {
  return { status: "failed", freshness: "live", asOf: "", reason };
}

/**
 * Read a leg's freshness from the response body.
 *
 * Absent or unrecognised `dataSource` yields "live" — see the module note. A
 * `dataSource: "cache"` with no `asOf` still yields "cached": the age is
 * unknown, but the staleness is not, and suppressing the label because one field
 * is missing would hide the more important fact.
 *
 * ``dataSource: "mixed"`` is recognised case-insensitively (matching the
 * cache/cached precedent) and produces ``freshness: "partial"`` — spec
 * `2026-09-10-service-history-read-path-correctness` D3/R1.  The branch exists
 * so a payload containing stale rows cannot render as fully live; without it
 * the else-branch would swallow the label.  The invariant that any *unrecognised*
 * value still falls back to live is preserved deliberately and is test-pinned.
 */
export function legFromBody(body: ProvenanceEnvelope | null | undefined): LegProvenance {
  const declared = String(body?.dataSource ?? "").toLowerCase();
  if (declared === "cache" || declared === "cached") {
    return {
      status: "ok",
      freshness: "cached",
      asOf: typeof body?.asOf === "string" ? body.asOf : "",
      reason: "",
    };
  }
  if (declared === "mixed") {
    const leg: LegProvenance = {
      status: "ok",
      freshness: "partial",
      asOf: typeof body?.asOf === "string" ? body.asOf : "",
      reason: "",
    };
    // Only attach when the envelope explicitly declared it — undefined is a
    // meaningful signal ("we don't know how many"), and forcing it to 0 would
    // let a downstream notice claim zero stale rows in a payload that clearly
    // has some.
    if (typeof body?.cacheOnlyCount === "number" && body.cacheOnlyCount >= 0) {
      leg.cacheOnlyCount = body.cacheOnlyCount;
    }
    return leg;
  }
  return liveLeg();
}

/** Render a cache age as a short human phrase. Empty when age is unknown. */
export function formatCacheAge(asOf: string, now: Date = new Date()): string {
  if (!asOf) return "";
  const then = Date.parse(asOf);
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, Math.floor((now.getTime() - then) / 1000));
  if (seconds < 60) return "less than a minute old";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} old`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} old`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} old`;
}

export interface ProvenanceNotice {
  /** Cloudscape Alert type. */
  type: "error" | "warning" | "info";
  header: string;
  body: string;
}

/**
 * Describe the merge honestly, or return null when there is nothing to say.
 *
 * Ordering is by severity of the misunderstanding a silent view would cause:
 *
 *  1. BOTH legs failed — the table is empty for a reason that is not "nothing to
 *     report". An empty table with no notice reads as "your fleet is healthy",
 *     which is the worst available lie.
 *  2. One leg failed — the view is INCOMPLETE. This is the case that exists in
 *     production today with no notice at all.
 *  3. A leg is cached — the view is complete but not current.
 *  4. All live — say nothing. A banner on every healthy load is noise, and noise
 *     is how a real notice gets skipped.
 */
export function describeProvenance(
  p: MergeProvenance,
  now: Date = new Date(),
): ProvenanceNotice | null {
  const alertsFailed = p.alerts.status === "failed";
  const historyFailed = p.serviceHistory.status === "failed";

  if (alertsFailed && historyFailed) {
    return {
      type: "error",
      header: "Maintenance data could not be loaded",
      body:
        "Neither telemetry alerts nor service history could be retrieved, so " +
        "this view is empty because the request failed — not because there is " +
        "nothing to report. Use Refresh to try again.",
    };
  }

  if (historyFailed) {
    return {
      type: "warning",
      header: "Showing telemetry alerts only — service history is missing",
      body:
        "Scheduled and completed service work could not be retrieved" +
        (p.serviceHistory.reason ? ` (${p.serviceHistory.reason})` : "") +
        ". Rows and counts below are incomplete. Use Refresh to try again.",
    };
  }

  if (alertsFailed) {
    return {
      type: "warning",
      header: "Showing service history only — telemetry alerts are missing",
      body:
        "Telemetry-triggered maintenance alerts could not be retrieved" +
        (p.alerts.reason ? ` (${p.alerts.reason})` : "") +
        ". Rows and counts below are incomplete. Use Refresh to try again.",
    };
  }

  if (p.serviceHistory.freshness === "cached") {
    const age = formatCacheAge(p.serviceHistory.asOf, now);
    return {
      type: "info",
      header: age
        ? `Service history is cached — ${age}`
        : "Service history is cached",
      body:
        "The dealer management system was unreachable, so service work is shown " +
        "from CMS's last known copy. Work completed since then will not appear.",
    };
  }

  // Partial — some rows are live, some are cache-only.  Spec
  // `2026-09-10-service-history-read-path-correctness` D3 / R1.  This is less
  // severe than a full cache (the live rows ARE current), more severe than
  // silence (some rows are older than the caller thinks).  Info type matches
  // fully-cached because the same failure mode — a UI narrating stale data as
  // live — is what both branches are here to prevent, and info renders a subtle
  // banner rather than an alarm on a routine query.
  if (p.serviceHistory.freshness === "partial") {
    const age = formatCacheAge(p.serviceHistory.asOf, now);
    const count = p.serviceHistory.cacheOnlyCount;
    // "some rows" appears in both the header and body so the case-insensitive
    // T1.4(b.2) grep for /partial|some|subset|certain|not all|cache/ hits reliably
    // and so a reader who saw only the header still knows this is not fully live.
    const countPhrase =
      typeof count === "number" && count > 0
        ? `${count} row${count === 1 ? "" : "s"} `
        : "some rows ";
    const ageSuffix = age ? ` — oldest ${age}` : "";
    return {
      type: "info",
      header: `Service history is partial — ${countPhrase}from cache${ageSuffix}`,
      body:
        `Live service records were retrieved, but ${countPhrase.trim()} could not be ` +
        "confirmed against the dealer management system and are shown from CMS's " +
        "cache. Work completed after the timestamp above may not appear on those " +
        "rows. Live rows above them are current.",
    };
  }

  return null;
}
