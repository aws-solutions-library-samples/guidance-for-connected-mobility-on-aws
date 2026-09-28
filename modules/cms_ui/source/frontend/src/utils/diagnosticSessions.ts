// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// diagnosticSessions — pure helper grouping SOVD command rows into sessions.
//
// Spec: `.kiro/specs/2026-09-24-cms-diagnostics-tab-ia-redesign` Group 2, Task 2.2.
//
// ── Design ─────────────────────────────────────────────────────────────────────
//
// Input: an array of raw SOVD command rows as returned by the commands API
// (camelCase keys except `session_id` which remains snake_case on the wire —
// see docs/tech.md § "Diagnostics IA redesign" 3 for the deliberate asymmetry).
//
// Output: one `DiagnosticSession` per distinct `session_id`, plus a single
// "unsessioned" bucket for rows that carry no `session_id` at all (pre-T2.4
// rows; silently dropping them would misreport history).
//
// Status derivation:
//   Active      — at least one row is non-terminal
//                 (non-terminal = status IN_PROGRESS | SENT | '' | undefined)
//   RateLimited — all rows are terminal AND at least one is RATE_LIMITED
//                 (exhausted retry outcome from Task 2.3)
//   Complete    — all rows are terminal AND none is RATE_LIMITED
//
// worstVerdict ordering (descending):
//   out_of_spec > marginal > in_spec > none (absent/unrecognised)
//
// dispatchedRoId / dispatchedAt — sourced from the stamped fields added by
// Task 2.1 (`dispatched_ro_id` / `dispatched_at` on individual command rows).
// Any row in the session carrying these fields populates the session-level
// fields; the most-recently-dispatched wins when multiple rows carry them.
//
// Pure — no fetch, no React, no Date.now(). The `now` argument is required for
// any callers that need relative-time rendering; this module does not read it
// but exposes it as part of the function signature for uniformity.

// ── Types ───────────────────────────────────────────────────────────────────

/**
 * A single SOVD command row as returned by the commands API.
 *
 * Key fields match the wire shape from `SessionCommandEntry` in
 * VehicleDiagnosticsPanel.tsx. `session_id` is deliberately snake_case —
 * that is what the API stores and returns (see docs/tech.md § 3).
 * `dispatched_ro_id` and `dispatched_at` are written by Task 2.1.
 */
export interface SovdCommandRow {
  /** Wire-shape key — snake_case, deliberate (see module comment). */
  session_id?: string;
  status?: string;
  commandType?: string;
  type?: string;
  routineId?: string;
  /** ISO timestamp when the command was submitted. */
  submittedAt?: string;
  /** Routine result payload; may carry a `verdict` field. */
  response?: {
    verdict?: string;
    [key: string]: unknown;
  };
  /** Stamped by Task 2.1 on 201 from DMS dispatch. */
  dispatched_ro_id?: string;
  /** ISO timestamp; stamped alongside dispatched_ro_id. */
  dispatched_at?: string;
  [key: string]: unknown;
}

export type SessionStatus = 'Active' | 'Complete' | 'RateLimited';

/**
 * The verdict ranking values understood by this helper.
 * `none` is the synthetic default for rows/sessions with no verdict.
 */
export type RoutineVerdict = 'out_of_spec' | 'marginal' | 'in_spec' | 'none';

/**
 * One session's aggregated data, ready for display.
 *
 * sessionId is camelCase (output) even though the raw row attribute is
 * snake_case session_id (input). See docs/tech.md § "Diagnostics IA redesign"
 * § 3 and tasks.md § 2.2 Constraints.
 */
export interface DiagnosticSession {
  /** camelCase output — see module comment on the asymmetry. */
  sessionId: string;
  /** ISO string of the earliest `submittedAt` among the session's rows. */
  startedAt: string | undefined;
  status: SessionStatus;
  /**
   * Number of distinct routines run in the session. Retries of the same
   * routineId count once; run_routine rows with no routineId count one each.
   */
  routineCount: number;
  /** Worst verdict seen across all rows with a response.verdict field. */
  worstVerdict: RoutineVerdict;
  /** RO ID stamped by Task 2.1; undefined when not yet dispatched. */
  dispatchedRoId: string | undefined;
  /** ISO timestamp stamped alongside dispatchedRoId; undefined when absent. */
  dispatchedAt: string | undefined;
}

// ── Verdict ordering ────────────────────────────────────────────────────────

/** Numeric rank; higher = worse. `none` = 0 (no verdict). */
const VERDICT_RANK: Record<RoutineVerdict, number> = {
  out_of_spec: 3,
  marginal: 2,
  in_spec: 1,
  none: 0,
};

const SENTINEL_UNSESSIONED = '__unsessioned__';

/** Returns true when a command row is in a non-terminal state. */
function isNonTerminal(status: string | undefined): boolean {
  return (
    status === undefined ||
    status === '' ||
    status === 'IN_PROGRESS' ||
    status === 'SENT'
  );
}

/**
 * Map a raw verdict string to a typed `RoutineVerdict`.
 * Anything unrecognised (including undefined) maps to `'none'`.
 */
function toRoutineVerdict(raw: string | undefined): RoutineVerdict {
  if (raw === 'out_of_spec' || raw === 'marginal' || raw === 'in_spec') {
    return raw;
  }
  return 'none';
}

// ── groupSovdRowsIntoSessions ───────────────────────────────────────────────

/**
 * Group an array of SOVD command rows into per-session summaries.
 *
 * @param rows  Raw rows from the commands API.
 * @param _now  Current timestamp (ISO string or number) — accepted for
 *              uniformity with callers that need relative-time rendering;
 *              not consumed by this pure helper.
 * @returns     Array of `DiagnosticSession`; empty when `rows` is empty.
 *              Never throws.
 */
export function groupSovdRowsIntoSessions(
  rows: SovdCommandRow[],
  _now?: string | number | Date,
): DiagnosticSession[] {
  if (rows.length === 0) return [];

  // ── Bucket rows by session_id ─────────────────────────────────────────
  //
  // Rows with no session_id go into a synthetic SENTINEL_UNSESSIONED bucket
  // rather than being dropped — pre-T2.4 rows carry no session and silently
  // hiding them would misreport history.

  const buckets = new Map<string, SovdCommandRow[]>();

  for (const row of rows) {
    const key =
      typeof row.session_id === 'string' && row.session_id !== ''
        ? row.session_id
        : SENTINEL_UNSESSIONED;

    const existing = buckets.get(key);
    if (existing) {
      existing.push(row);
    } else {
      buckets.set(key, [row]);
    }
  }

  // ── Aggregate each bucket into a DiagnosticSession ───────────────────

  const sessions: DiagnosticSession[] = [];

  for (const [key, bucketRows] of buckets) {
    let hasNonTerminal = false;
    // A rate-limited attempt that was later retried and succeeded is not a
    // rate-limited session: the auto-retry (Task 2.3) writes one row per attempt.
    // So the status and the count read each routine's LATEST attempt, not every
    // row (issue 2026-09-25-diagnostics-ia-uat-defects — a live session with two
    // retried-then-succeeded routines read "Rate limited" and "6 routines").
    // Rows without a routineId keep the per-row rule.
    let rateLimitedWithoutRoutineId = false;
    let routineRowsWithoutId = 0;
    const latestByRoutine = new Map<string, { at: string; status: string | undefined }>();
    let worstRank = 0;
    let worstVerdict: RoutineVerdict = 'none';
    let earliestSubmittedAt: string | undefined;
    let dispatchedRoId: string | undefined;
    let dispatchedAt: string | undefined;

    for (const row of bucketRows) {
      // ── Status derivation ────────────────────────────────────────────
      const routineId =
        typeof row.routineId === 'string' && row.routineId !== '' ? row.routineId : undefined;
      if (isNonTerminal(row.status)) {
        hasNonTerminal = true;
      } else if (row.status === 'RATE_LIMITED' && routineId === undefined) {
        rateLimitedWithoutRoutineId = true;
      }

      // ── Routine count + latest attempt per routine ───────────────────
      const cmdType = row.commandType ?? row.type ?? '';
      if (cmdType === 'run_routine') {
        if (routineId === undefined) {
          routineRowsWithoutId += 1;
        } else {
          const at = typeof row.submittedAt === 'string' ? row.submittedAt : '';
          const prev = latestByRoutine.get(routineId);
          // Strictly newer wins. On a tie the row seen first is kept: the API
          // returns rows newest-first, so that is the later attempt.
          if (prev === undefined || at > prev.at) {
            latestByRoutine.set(routineId, { at, status: row.status });
          }
        }
      }

      // ── Worst verdict ────────────────────────────────────────────────
      const rawVerdict =
        row.response && typeof row.response === 'object'
          ? (row.response as { verdict?: string }).verdict
          : undefined;
      const verdict = toRoutineVerdict(rawVerdict);
      const rank = VERDICT_RANK[verdict];
      if (rank > worstRank) {
        worstRank = rank;
        worstVerdict = verdict;
      }

      // ── Earliest submittedAt ─────────────────────────────────────────
      if (typeof row.submittedAt === 'string' && row.submittedAt !== '') {
        if (
          earliestSubmittedAt === undefined ||
          row.submittedAt < earliestSubmittedAt
        ) {
          earliestSubmittedAt = row.submittedAt;
        }
      }

      // ── Dispatch stamp (Task 2.1) ─────────────────────────────────────
      // If multiple rows carry the stamp, prefer the one with the most
      // recent dispatched_at (ISO string comparison is valid here since
      // these are server-generated ISO timestamps).
      if (
        typeof row.dispatched_ro_id === 'string' &&
        row.dispatched_ro_id !== ''
      ) {
        if (
          dispatchedAt === undefined ||
          (typeof row.dispatched_at === 'string' &&
            row.dispatched_at > dispatchedAt)
        ) {
          dispatchedRoId = row.dispatched_ro_id;
          dispatchedAt =
            typeof row.dispatched_at === 'string'
              ? row.dispatched_at
              : undefined;
        }
      }
    }

    // ── Derive session status ────────────────────────────────────────────
    const routineCount = routineRowsWithoutId + latestByRoutine.size;
    const hasRateLimited =
      rateLimitedWithoutRoutineId ||
      [...latestByRoutine.values()].some((v) => v.status === 'RATE_LIMITED');
    let status: SessionStatus;
    if (hasNonTerminal) {
      status = 'Active';
    } else if (hasRateLimited) {
      status = 'RateLimited';
    } else {
      status = 'Complete';
    }

    sessions.push({
      sessionId: key === SENTINEL_UNSESSIONED ? SENTINEL_UNSESSIONED : key,
      startedAt: earliestSubmittedAt,
      status,
      routineCount,
      worstVerdict,
      dispatchedRoId,
      dispatchedAt,
    });
  }

  return sessions;
}

// ── Convenience re-export for callers that want the sentinel constant ────────
export { SENTINEL_UNSESSIONED as UNSESSIONED_SESSION_ID };
