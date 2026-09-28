// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// SOVD scan transport — extracted from VehicleDiagnosticsPanel.tsx.
//
// Spec: `.kiro/specs/2026-09-02-cms-diagnostics-platform` Group 3, T3.1 / DX6 (C8).
//
// WHY THIS MODULE EXISTS
// ----------------------
// The DX6 copy-lint (`__tests__/diagnostics-copy-lint.test.ts`) asserts that
// `VehicleDiagnosticsPanel.tsx` contains no HTTP method names, no `/api/` paths and
// no `.py` / `force_event` references, because that file is the customer-facing
// surface. The lint's own scope note says it targets "customer-facing copy only".
//
// A fetch's `method: 'POST'` is not copy — but the lint is deliberately line-based
// and conservative, so it fires on implementation detail sitting in the same file.
// The correct response is to move the transport OUT of the customer-facing file,
// not to obfuscate the strings in place (e.g. `'PO' + 'ST'`), which would defeat
// the lint while leaving the behaviour identical and the source harder to read.
//
// So: the panel owns presentation and copy; this module owns the wire format. The
// literals below are written plainly, which is the point — they are honest here.

import { getCommandsApiBase } from '@/utils/api-config';
import { authFetch } from '@/utils/authFetch';
import { getRuntimeConfig } from '@/config/api';

/** Resolve the commands API base, tolerating an unconfigured runtime. */
function resolveApiBase(): string {
  try {
    return getCommandsApiBase();
  } catch {
    return '';
  }
}

/** POST target for issuing a command to a vehicle. */
export function commandsUrl(vehicleId: string): string {
  return `${resolveApiBase()}/api/commands/${vehicleId}`;
}

/** GET target for the most recent SOVD command record for a vehicle. */
export function latestSovdCommandUrl(vehicleId: string): string {
  return `${resolveApiBase()}/api/commands/${vehicleId}?type=sovd&limit=1`;
}

/**
 * Issue a full SOVD scan: all components, freeze frames included.
 *
 * Returns whatever `authFetch` returns — including `undefined` when the caller has
 * stubbed `authFetch` with a bare mock. Callers must treat the result as possibly
 * absent rather than assuming a Response.
 */
export function startFullScan(vehicleId: string) {
  return authFetch(commandsUrl(vehicleId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      command_type: 'read_dtcs',
      components: ['*'],
      include_freeze_frame: true,
    }),
  });
}

/** Fetch the latest SOVD command record, used to poll for scan completion. */
export function fetchLatestSovdCommand(vehicleId: string) {
  return authFetch(latestSovdCommandUrl(vehicleId));
}

/** GET target for the available routines for a vehicle. */
export function routinesUrl(vehicleId: string): string {
  return `${resolveApiBase()}/api/commands/${vehicleId}/routines`;
}

/** Fetch the available routines for a vehicle, including safety-class and invocability. */
export function fetchRoutines(vehicleId: string) {
  return authFetch(routinesUrl(vehicleId));
}

/*
 * `runRoutine` (session-less) was REMOVED 2026-09-23.
 *
 * It existed alongside `runRoutineInSession` below, and the panel was wired to
 * the session-less one — so no command row ever carried a session, the
 * Diagnostics session log was always empty, and every dispatched repair order
 * reached DMS with zero routine evidence.
 *
 * Two near-identical invoke helpers where only one is correct is the trap that
 * produced that bug, so the wrong one is gone rather than left for the next
 * caller to pick. Every run_routine invocation is session-correlated.
 * See issues/2026-09-23-diagnostics-session-log-never-populates/.
 */

// ── Task 2.3: bounded auto-retry on rate-limited routine invocations ─────────
//
// Spec: `.kiro/specs/2026-09-24-cms-diagnostics-tab-ia-redesign` Group 2, Task 2.3.
//
// Q5 invariant (spec § Decision Q5):
//   "refusal is INVISIBLE for transient cases, VISIBLE on exhaustion"
//   — while retrying the caller observes `Running`; only exhaustion surfaces
//     a `RateLimited` outcome.
//
// The RATE_LIMITED status arrives asynchronously: the sidecar publishes it back
// via MQTT → IoT rule → Lambda → DynamoDB, then the client polls DynamoDB.
// `runRoutineInSession` therefore issues the POST and polls the session commands
// endpoint, retrying the whole round-trip on RATE_LIMITED up to MAX_ATTEMPTS.
//
// Injected dependencies (pollFn, delayFn) exist exclusively for testing.
// Default values call the real transport and setTimeout.

/** Maximum total invocation attempts before resolving as RateLimited. */
const MAX_ATTEMPTS = 3;

/** Poll interval (ms) when waiting for a command to reach terminal status. */
const ROUTINE_POLL_INTERVAL_MS = 1_500;

/** Ceiling (ms) for the per-attempt poll before giving up on that attempt. */
const ROUTINE_POLL_MAX_MS = 30_000;

/** Terminal statuses — anything else keeps the poll running. */
/**
 * Statuses that mean the vehicle has not answered yet. Anything else a row
 * carries is terminal. An allowlist of terminal statuses missed the sidecar's
 * refusals (PRECONDITION_FAILED_MOVING, REFUSED_SERVICE_ONLY,
 * UNRESOLVABLE_SAFETY_CLASS, REFUSED_NO_VEHICLE_STATE —
 * realtime_telemetry_simulator.py), so a refused routine sat at "Running…" for
 * the whole poll ceiling (review FG2 cycle 2, S5). Matches the sessions
 * grouping helper's notion of non-terminal.
 */
const NON_TERMINAL_STATUSES = new Set(['', 'SENT', 'IN_PROGRESS', 'PROGRESS', 'PENDING']);
const isTerminalStatus = (status: string | undefined): status is string =>
  typeof status === 'string' && !NON_TERMINAL_STATUSES.has(status);

/**
 * Structured outcome returned by `runRoutineInSession`.
 *
 * `ok`           — server accepted and the sidecar reported SUCCEEDED.
 * `error`        — synchronous HTTP error (4xx/5xx) or sidecar non-RATE_LIMITED failure.
 * `RateLimited`  — all attempts exhausted on RATE_LIMITED. Carries attempt count.
 * `network_error` — thrown before/during the POST (no Response available).
 */
export type RoutineInvocationOutcome =
  /**
   * `status` is 'SUCCEEDED' when the poll saw the vehicle's terminal row, so
   * the caller already has the outcome. It is absent when the poll hit its
   * ceiling first, in which case the caller has to look for the outcome.
   */
  | { kind: 'ok'; response: Response; status?: 'SUCCEEDED' }
  | { kind: 'error'; response: Response; status?: string; reason?: string }
  | { kind: 'RateLimited'; attempts: number }
  | { kind: 'network_error'; error: unknown };

/**
 * Shape of a single command row from the session-commands poll. On a live row
 * `retry_after_ms` is nested under `response` (the sidecar's payload); the
 * top-level field is accepted too.
 */
interface RawCommandRow {
  commandId?: string;
  correlationId?: string;
  status?: string;
  reason?: string;
  retry_after_ms?: number;
  response?: { retry_after_ms?: number; reason?: string; [key: string]: unknown };
}

/**
 * Rows from a commands-API body. The live API returns `{commands: [...], count}`
 * (commands_lambda.py `_get_history`). A bare array is accepted as well. Reading
 * only the bare-array shape made every poll throw, so auto-retry never ran and
 * each routine sat at "Running…" for the full poll ceiling
 * (issue 2026-09-25-diagnostics-ia-uat-defects, review W4).
 */
function rowsFromBody(body: unknown): RawCommandRow[] {
  if (Array.isArray(body)) return body as RawCommandRow[];
  const commands = (body as { commands?: unknown } | null)?.commands;
  return Array.isArray(commands) ? (commands as RawCommandRow[]) : [];
}

/** The rate-limit back-off a row asks for, from either location. */
function retryAfterMs(row: RawCommandRow): number | undefined {
  const v = row.retry_after_ms ?? row.response?.retry_after_ms;
  return typeof v === 'number' && v > 0 ? v : undefined;
}

/**
 * Poll the session commands endpoint until a row for `commandId` reaches a
 * terminal status, returning the raw row.  Resolves `undefined` on poll ceiling.
 */
async function pollForTerminalStatus(
  vehicleId: string,
  sessionId: string,
  commandId: string,
  pollFn: (vid: string, sid: string) => Promise<Response | undefined>,
  delayFn: (ms: number) => Promise<void>,
): Promise<RawCommandRow | undefined> {
  const deadline = Date.now() + ROUTINE_POLL_MAX_MS;
  while (Date.now() < deadline) {
    await delayFn(ROUTINE_POLL_INTERVAL_MS);
    try {
      const res = await pollFn(vehicleId, sessionId);
      if (!res) continue;
      const rows = rowsFromBody(await res.json().catch(() => null));
      const row = rows.find(
        (r) => r.commandId === commandId || r.correlationId === commandId,
      );
      if (row && isTerminalStatus(row.status)) {
        return row;
      }
    } catch {
      // Network blip — keep polling
    }
  }
  return undefined; // poll timed out
}

/**
 * T3.1 (SOVD sessions v1.5): Issue a run_routine command with a session
 * correlation ID.  The sessionId is sent in the request body so the
 * backend can persist it on the command row and clients can filter by it.
 *
 * Extended (Task 2.3): on RATE_LIMITED sidecar response, auto-retries up to
 * MAX_ATTEMPTS (3) total, honouring the returned `retry_after_ms` delay.
 * The caller sees `Running` throughout; exhaustion resolves to a `RateLimited`
 * outcome — it does NOT throw and does NOT resolve as success.
 *
 * Transport lives here per DX6 — the panel must not contain POST or /api/ paths.
 *
 * @param pollFn  Injectable override for `fetchSessionCommands` (testing only).
 * @param delayFn Injectable override for the retry/poll delay (testing only).
 */
export async function runRoutineInSession(
  vehicleId: string,
  routineId: string,
  sessionId: string,
  attestation: { text: string; user_email: string; timestamp_ms: number },
  pollFn: (vid: string, sid: string) => Promise<Response | undefined> = fetchSessionCommands,
  delayFn: (ms: number) => Promise<void> = (ms) =>
    new Promise((resolve) => setTimeout(resolve, ms)),
): Promise<RoutineInvocationOutcome> {
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    // ── Issue POST ────────────────────────────────────────────────────────────
    let res: Response | undefined;
    try {
      res = await authFetch(commandsUrl(vehicleId), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          command_type: 'run_routine',
          routine_id: routineId,
          // MUST be `sessionId` (camelCase). The backend reads
          // `body.get('sessionId')` (commands_lambda.py), the query-string filter is
          // `?sessionId=`, and the 400 text says "sessionId must be a string of at
          // most 64 characters" — three places agreeing on this spelling. This
          // previously sent `session_id`, which the backend silently ignored, so no
          // command row ever carried a session and the session log could not populate.
          // Verified against staging with positive controls on both spellings:
          // `sessionId` persists, `session_id` does not.
          // See issues/2026-09-23-diagnostics-session-log-never-populates/.
          sessionId: sessionId,
          attestation,
        }),
      });
    } catch (error) {
      return { kind: 'network_error', error };
    }

    // ── Synchronous HTTP failure → no retry ──────────────────────────────────
    if (!res.ok) {
      return { kind: 'error', response: res };
    }

    // ── Extract commandId from response ──────────────────────────────────────
    let commandId: string | undefined;
    try {
      const body = await res.clone().json();
      commandId = body?.commandId ?? body?.correlationId;
    } catch {
      // Body unreadable — poll by session anyway; terminal-row lookup by commandId
      // will be skipped (row?.commandId won't match undefined)
    }

    // ── Poll for terminal status ──────────────────────────────────────────────
    const row = commandId
      ? await pollForTerminalStatus(vehicleId, sessionId, commandId, pollFn, delayFn)
      : undefined;

    if (!row) {
      // Poll timed out — return the raw HTTP success; caller handles timeout
      return { kind: 'ok', response: res };
    }

    // ── RATE_LIMITED: wait and retry or exhaust ───────────────────────────────
    if (row.status === 'RATE_LIMITED') {
      if (attempt < MAX_ATTEMPTS) {
        const wait = retryAfterMs(row) ?? ROUTINE_POLL_INTERVAL_MS;
        await delayFn(wait);
        continue; // retry
      }
      // All attempts exhausted
      return { kind: 'RateLimited', attempts: attempt };
    }

    // ── Non-rate-limit terminal status ───────────────────────────────────────
    if (row.status === 'SUCCEEDED') {
      return { kind: 'ok', response: res, status: 'SUCCEEDED' };
    }
    return { kind: 'error', response: res, status: row.status, reason: row.reason ?? row.response?.reason };
  }

  // Unreachable — loop always returns inside, but required for TypeScript
  /* c8 ignore next */
  return { kind: 'RateLimited', attempts: MAX_ATTEMPTS };
}

/** GET URL for all SOVD commands in a specific session. */
export function sessionCommandsUrl(vehicleId: string, sessionId: string): string {
  return `${resolveApiBase()}/api/commands/${vehicleId}?type=sovd&sessionId=${encodeURIComponent(sessionId)}`;
}

/** GET URL for prior SOVD command history (no sessionId filter — shows all). */
export function allSovdCommandsUrl(vehicleId: string): string {
  // limit=100: the API defaults to the newest 20 rows. The Diagnostics tab
  // derives "last scan" from this list, so a vehicle with 20+ routine runs
  // since its last scan would otherwise read as never scanned. The server caps
  // the query at 500 rows (commands_lambda.py _HISTORY_FETCH_CAP).
  return `${resolveApiBase()}/api/commands/${vehicleId}?type=sovd&limit=100`;
}

/**
 * Fetch all SOVD commands for a session (current-session log).
 * Returns the raw authFetch result; callers handle absent / undefined.
 */
export function fetchSessionCommands(vehicleId: string, sessionId: string) {
  return authFetch(sessionCommandsUrl(vehicleId, sessionId));
}

/**
 * Fetch all SOVD commands (across all sessions) for the prior-session divider.
 * The panel filters out entries matching the current sessionId before rendering.
 */
export function fetchAllSovdCommands(vehicleId: string) {
  return authFetch(allSovdCommandsUrl(vehicleId));
}

/** GET URL for DTC-suggested routines for a vehicle. */
export function dtcRoutinesUrl(vehicleId: string, dtcCode: string): string {
  return `${resolveApiBase()}/api/commands/${vehicleId}/routines?dtc=${encodeURIComponent(dtcCode)}`;
}

/**
 * T3.1: Fetch the routine suggestions for a specific DTC code.
 * The endpoint (T2.6) returns profile-filtered routines with DTC-suggested
 * ones flagged/ordered first via the `suggestedForDtc` field.
 */
export function fetchDtcSuggestedRoutines(vehicleId: string, dtcCode: string) {
  return authFetch(dtcRoutinesUrl(vehicleId, dtcCode));
}


/**
 * GET the DTC/event catalog (dtc_code, severity_hint, description) from the
 * main API, the same route CreateCampaignWizard and EventCatalogViewer use
 * (main_api/index.py `/api/v1/event-catalog`). The Diagnostics tab needs it to
 * turn scanned DTCs into a severity verdict; its parent never supplied one, so
 * every DTC read "Undetermined" (issue 2026-09-25-diagnostics-ia-uat-defects).
 */
export function eventCatalogUrl(): string {
  let base = '';
  try {
    base = getRuntimeConfig().apiEndpoint ?? '';
  } catch {
    base = '';
  }
  return `${base.replace(/\/$/, '')}/api/v1/event-catalog`;
}

export function fetchEventCatalog() {
  return authFetch(eventCatalogUrl());
}
