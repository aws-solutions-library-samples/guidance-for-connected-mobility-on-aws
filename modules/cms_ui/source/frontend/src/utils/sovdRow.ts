// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// sovdRow — the one place the Diagnostics tab turns a commands-API row into
// the shape its components read.
//
// Why this exists (issue 2026-09-25-diagnostics-ia-uat-defects): the commands
// API returns DynamoDB items verbatim (`services/commands/commands_lambda.py`
// `_resp(200, {'commands': commands})`). A live row carries:
//
//   session_id   snake_case, not `sessionId`
//   issuedAt     ISO string. There is no `submittedAt`
//   timestamp    epoch ms
//   respondedAt  ISO string, on terminal rows
//   components   the REQUEST list for a scan, e.g. ["*"]
//   response.components  the per-ECU RESULTS map
//
// The panel's types had been written against `sessionId`, `submittedAt` and a
// top-level results map. None of those exist on a live row, so the sessions
// table grouped every run into one "session" with no start time, and a
// completed scan rendered one ECU card named "0". Unit tests passed because
// their fixtures used the invented names. Normalising here, once, at the fetch
// boundary keeps every downstream reader on one shape.

/** A per-ECU entry in a scan's results map. */
export interface SovdEcuResult {
  id?: string;
  dtcs?: Array<{ code: string; status?: string; freeze_frame?: Record<string, unknown> | null }>;
  [key: string]: unknown;
}

export type SovdResultComponents = Record<string, SovdEcuResult>;

/** Fields this module reads or writes; everything else passes through. */
export interface NormalizedSovdRow {
  commandId?: string;
  commandType?: string;
  type?: string;
  status?: string;
  routineId?: string;
  /** From `sessionId`, else the API's `session_id`. */
  sessionId?: string;
  /** Kept identical to `sessionId` so the grouping helper's wire key is filled. */
  session_id?: string;
  /** From `submittedAt`, else the API's `issuedAt`, else `timestamp` (ms). */
  submittedAt?: string;
  respondedAt?: string;
  /** The per-ECU results map, or undefined when the row has none. */
  resultComponents?: SovdResultComponents;
  response?: unknown;
  [key: string]: unknown;
}

function isPlainMap(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function msToIso(v: unknown): string | undefined {
  const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN;
  if (!Number.isFinite(n) || n <= 0) return undefined;
  const d = new Date(n);
  return isNaN(d.getTime()) ? undefined : d.toISOString();
}

function nonEmptyString(v: unknown): string | undefined {
  return typeof v === 'string' && v !== '' ? v : undefined;
}

/**
 * Normalise one commands-API row. Idempotent: a row that already carries the
 * camelCase fields keeps them.
 */
export function normalizeSovdRow<T extends object>(input: T): T & NormalizedSovdRow {
  const raw = input as Record<string, unknown>;
  const sessionId = nonEmptyString(raw.sessionId) ?? nonEmptyString(raw.session_id);
  const submittedAt =
    nonEmptyString(raw.submittedAt) ?? nonEmptyString(raw.issuedAt) ?? msToIso(raw.timestamp);

  // Results are only meaningful on a scan row. A run_routine row's
  // response.components is the routine envelope ({status, correlation_id,
  // routine_id}), not per-ECU results, and must never be read as ECUs.
  // Rows with no command type at all (older test doubles) are still accepted.
  const kind = raw.commandType;
  const scanShaped = kind === undefined || kind === 'read_dtcs';
  const response = raw.response;
  const fromResponse = scanShaped && isPlainMap(response) && isPlainMap(response.components)
    ? (response.components as SovdResultComponents)
    : undefined;
  const fromTopLevel = scanShaped && isPlainMap(raw.components)
    ? (raw.components as SovdResultComponents)
    : undefined;

  return {
    ...input,
    sessionId,
    session_id: sessionId,
    submittedAt,
    resultComponents: fromResponse ?? fromTopLevel,
  };
}

/**
 * How much of a scan's result the stored row actually carries.
 * - 'complete'    — every ECU entry has a `dtcs` list, and the scan SUCCEEDED
 * - 'partial'     — the scan is PARTIAL, or some ECU entries lack a `dtcs` list;
 *                   `dtcs` covers only the ECUs that answered
 * - 'unavailable' — the row carries no per-ECU results at all (for example the
 *                   response was offloaded to S3, or dropped as oversize)
 * Only 'complete' may ever back a "no issues found" claim.
 */
export type ScanResults = 'complete' | 'partial' | 'unavailable';

/** The scan summary the health strip renders. */
export interface LatestScan {
  /** When the vehicle answered, else when the scan was issued. */
  scannedAt?: string;
  dtcs: Array<{ code: string; status?: string; ecu?: string }>;
  results: ScanResults;
  /** ECU entries that carried a `dtcs` list. */
  ecusAnswered: number;
  /** Results are stored elsewhere (see ScanRowSummary.offloaded). */
  offloaded: boolean;
}

const SCAN_DONE = new Set(['SUCCEEDED', 'PARTIAL']);

/**
 * True when the scan asked every ECU. A single-ECU "Read DTCs" from the DTC
 * table is also `read_dtcs`, but it reads one ECU and must not stand in for the
 * vehicle's last full scan (it would hide faults the full scan found). The
 * request list is the row's top-level `components`: `["*"]` or `["ALL"]` for a
 * full scan (realtime_telemetry_simulator.py). Rows without a request list are
 * not treated as full scans.
 */
export function isFullScanRequest(row: object): boolean {
  const req = (row as Record<string, unknown>).components;
  // Exactly ['*'] or ['ALL'], the only lists the sidecar treats as "every ECU".
  // ['*', 'ECU_BRAKE'] reads one ECU (review FG2 cycle 2, S1).
  return Array.isArray(req) && req.length === 1 && (req[0] === '*' || req[0] === 'ALL');
}

/** What one scan row's stored result says, independent of which scan it is. */
export interface ScanRowSummary {
  results: ScanResults;
  ecusAnswered: number;
  dtcs: LatestScan['dtcs'];
  /** The ECU entries that carried a `dtcs` list, keyed by ECU. */
  answered: SovdResultComponents;
  /**
   * The row points at results stored elsewhere (`s3Key` / `storage_uri`), so
   * "unavailable" means "not viewable here", not "lost" — re-scanning would
   * offload again.
   */
  offloaded: boolean;
}

/**
 * Classify a single scan row's stored result. Shared by the health strip's
 * latest-scan derivation and the scan poll, so the two can never disagree about
 * whether a result is complete (review FG2 cycle 2, C2: the poll had its own
 * copy of this check, which no test reached).
 */
export function summarizeScanRow(input: object): ScanRowSummary {
  const row = normalizeSovdRow(input);
  const entries = Object.entries(row.resultComponents ?? {});
  const answered: SovdResultComponents = {};
  const dtcs: LatestScan['dtcs'] = [];
  // A DTC entry with no string `code` cannot be reported, so its ECU cannot
  // vouch for "no faults" either (review FG2 cycle 2, S2).
  let unreadableDtc = false;
  for (const [ecu, entry] of entries) {
    if (!entry || !Array.isArray(entry.dtcs)) continue;
    answered[ecu] = entry;
    for (const d of entry.dtcs) {
      if (d && typeof d.code === 'string') dtcs.push({ code: d.code, status: d.status, ecu });
      else unreadableDtc = true;
    }
  }
  const ecusAnswered = Object.keys(answered).length;
  let results: ScanResults;
  if (ecusAnswered === 0) {
    results = 'unavailable';
  } else if (row.status === 'PARTIAL' || ecusAnswered < entries.length || unreadableDtc) {
    results = 'partial';
  } else {
    results = 'complete';
  }
  const r = row as Record<string, unknown>;
  const offloaded =
    nonEmptyString(r.s3Key) !== undefined ||
    nonEmptyString(r.storage_uri) !== undefined ||
    (isPlainMap(r.response) && nonEmptyString(r.response.storage_uri) !== undefined);
  return { results, ecusAnswered, dtcs, answered, offloaded };
}

/**
 * The most recent completed FULL scan (`read_dtcs` over all ECUs, SUCCEEDED or
 * PARTIAL) among the rows, or null when there is none. Rows are normalised
 * first, so raw API rows are accepted.
 */
export function deriveLatestScan(rows: ReadonlyArray<object>): LatestScan | null {
  let best: { row: NormalizedSovdRow; at: string } | null = null;
  for (const raw of rows) {
    const row = normalizeSovdRow(raw);
    const kind = row.commandType ?? row.type;
    if (kind !== 'read_dtcs' || !SCAN_DONE.has(row.status ?? '')) continue;
    if (!isFullScanRequest(raw)) continue;
    const at = nonEmptyString(row.respondedAt) ?? row.submittedAt;
    if (at === undefined) continue;
    if (best === null || Date.parse(at) > Date.parse(best.at)) best = { row, at };
  }
  if (best === null) return null;

  const { results, ecusAnswered, dtcs, offloaded } = summarizeScanRow(best.row);
  return { scannedAt: best.at, dtcs, results, ecusAnswered, offloaded };
}
