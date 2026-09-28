// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// normalizeSovdRow / deriveLatestScan against rows captured from staging.
// Issue: issues/2026-09-25-diagnostics-ia-uat-defects/.

import { describe, it, expect } from 'vitest';
import { normalizeSovdRow, deriveLatestScan } from '../sovdRow';
import { groupSovdRowsIntoSessions } from '../diagnosticSessions';
import type { SovdCommandRow } from '../diagnosticSessions';
import { LIVE_SOVD_ROWS } from './__fixtures__/liveSovdRows';

const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v));

describe('normalizeSovdRow on live rows', () => {
  it('fills sessionId and session_id from the API field session_id', () => {
    const row = normalizeSovdRow(clone(LIVE_SOVD_ROWS.sessionRoutine));
    expect(row.sessionId).toBe('2cf1b8de-9df7-467d-a0d8-e2c867e3d935');
    expect(row.session_id).toBe('2cf1b8de-9df7-467d-a0d8-e2c867e3d935');
  });

  it('fills submittedAt from issuedAt, since live rows have no submittedAt', () => {
    const raw = clone(LIVE_SOVD_ROWS.sessionRoutine);
    expect(raw.submittedAt).toBeUndefined();
    expect(normalizeSovdRow(raw).submittedAt).toBe('2026-09-23T22:36:07.104947+00:00');
  });

  it('falls back to the epoch-ms timestamp when issuedAt is absent', () => {
    const raw = clone(LIVE_SOVD_ROWS.sessionRoutine);
    delete raw.issuedAt;
    expect(normalizeSovdRow(raw).submittedAt).toBe(new Date(1790202967104).toISOString());
  });

  it('leaves an unsessioned row without a session id', () => {
    const row = normalizeSovdRow(clone(LIVE_SOVD_ROWS.unsessionedRoutine));
    expect(row.sessionId).toBeUndefined();
    expect(row.session_id).toBeUndefined();
  });

  it('takes scan results from response.components, not the top-level request list', () => {
    const raw = clone(LIVE_SOVD_ROWS.scan);
    expect(Array.isArray(raw.components)).toBe(true); // the request: ["*"]
    const row = normalizeSovdRow(raw);
    expect(row.resultComponents).toBeDefined();
    expect(Object.keys(row.resultComponents ?? {})).toContain('ECU_PCM');
    expect(Object.keys(row.resultComponents ?? {})).not.toContain('0');
  });

  it('is idempotent', () => {
    const once = normalizeSovdRow(clone(LIVE_SOVD_ROWS.sessionRoutine));
    expect(normalizeSovdRow(once)).toEqual(once);
  });
});

describe('grouping live rows into sessions', () => {
  it('keeps a sessioned and an unsessioned run apart, each with a start time', () => {
    // Same cast the panel applies before grouping (response: unknown → object).
    const rows = [LIVE_SOVD_ROWS.sessionRoutine, LIVE_SOVD_ROWS.unsessionedRoutine].map(
      (r) => normalizeSovdRow(clone(r)) as unknown as SovdCommandRow,
    );
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions).toHaveLength(2);
    for (const s of sessions) expect(s.startedAt).toBeTruthy();
    const ids = sessions.map((s) => s.sessionId);
    expect(ids).toContain('2cf1b8de-9df7-467d-a0d8-e2c867e3d935');
  });
});

describe('deriveLatestScan on live rows', () => {
  it('returns the completed scan, dated by respondedAt, with zero DTCs', () => {
    const scan = deriveLatestScan([
      clone(LIVE_SOVD_ROWS.sessionRoutine),
      clone(LIVE_SOVD_ROWS.scan),
    ]);
    expect(scan).not.toBeNull();
    expect(scan?.scannedAt).toBe('2026-09-23T23:17:36.527291+00:00');
    expect(scan?.dtcs).toEqual([]);
  });

  it('flattens DTCs across ECUs and records which ECU reported each', () => {
    const raw = clone(LIVE_SOVD_ROWS.scan) as { response: { components: Record<string, { dtcs: unknown[] }> } };
    raw.response.components.ECU_BRAKE.dtcs = [{ code: 'C1234', status: 'active' }];
    const scan = deriveLatestScan([raw]);
    expect(scan?.dtcs).toEqual([{ code: 'C1234', status: 'active', ecu: 'ECU_BRAKE' }]);
  });

  it('returns null when there is no completed scan', () => {
    expect(deriveLatestScan([clone(LIVE_SOVD_ROWS.sessionRoutine)])).toBeNull();
    const failed = clone(LIVE_SOVD_ROWS.scan);
    failed.status = 'FAILED';
    expect(deriveLatestScan([failed])).toBeNull();
    expect(deriveLatestScan([])).toBeNull();
  });

  it('picks the most recent of several completed scans', () => {
    const older = clone(LIVE_SOVD_ROWS.scan);
    older.respondedAt = '2026-09-20T10:00:00+00:00';
    older.commandId = 'older';
    const newer = clone(LIVE_SOVD_ROWS.scan);
    expect(deriveLatestScan([newer, older])?.scannedAt).toBe('2026-09-23T23:17:36.527291+00:00');
    expect(deriveLatestScan([older, newer])?.scannedAt).toBe('2026-09-23T23:17:36.527291+00:00');
  });
});

// ── Review FG2 cycle 1: C1 (results missing or partial) and C2 (single-ECU read) ─

describe('deriveLatestScan never reads a missing result as zero DTCs (C1)', () => {
  it('results unavailable when the response was offloaded (no response, s3Key only)', () => {
    const raw = clone(LIVE_SOVD_ROWS.scan) as Record<string, unknown>;
    delete raw.response;
    raw.s3Key = 'sovd-responses/example.json';
    const scan = deriveLatestScan([raw]);
    expect(scan?.results).toBe('unavailable');
    expect(scan?.ecusAnswered).toBe(0);
  });

  it('results unavailable when no ECU entry carries a dtcs list', () => {
    const raw = clone(LIVE_SOVD_ROWS.scan) as { response: { components: Record<string, Record<string, unknown>> } };
    for (const e of Object.values(raw.response.components)) delete e.dtcs;
    expect(deriveLatestScan([raw])?.results).toBe('unavailable');
  });

  it('partial when some ECU entries lack a dtcs list', () => {
    const raw = clone(LIVE_SOVD_ROWS.scan) as { response: { components: Record<string, Record<string, unknown>> } };
    const total = Object.keys(raw.response.components).length;
    delete raw.response.components.ECU_BRAKE.dtcs;
    const scan = deriveLatestScan([raw]);
    expect(scan?.results).toBe('partial');
    expect(scan?.ecusAnswered).toBe(total - 1);
  });

  it('partial when the scan status is PARTIAL', () => {
    const raw = clone(LIVE_SOVD_ROWS.scan);
    raw.status = 'PARTIAL';
    expect(deriveLatestScan([raw])?.results).toBe('partial');
  });

  it('complete for the live all-ECU scan', () => {
    expect(deriveLatestScan([clone(LIVE_SOVD_ROWS.scan)])?.results).toBe('complete');
  });
});

describe('deriveLatestScan counts only full scans (C2)', () => {
  it('a newer single-ECU read does not replace the last full scan', () => {
    const full = clone(LIVE_SOVD_ROWS.scan) as { response: { components: Record<string, { dtcs: unknown[] }> } } & Record<string, unknown>;
    full.response.components.ECU_ENGINE.dtcs = [{ code: 'P0300', status: 'active' }];
    const single = clone(LIVE_SOVD_ROWS.scan) as Record<string, unknown>;
    single.commandId = 'single-ecu-read';
    single.components = ['ECU_BRAKE'];
    single.respondedAt = '2026-09-24T09:00:00+00:00';
    single.response = { components: { ECU_BRAKE: { id: 'ECU_BRAKE', dtcs: [] } } };
    const scan = deriveLatestScan([single, full]);
    expect(scan?.scannedAt).toBe('2026-09-23T23:17:36.527291+00:00');
    expect(scan?.dtcs.map((d) => d.code)).toEqual(['P0300']);
  });

  it('accepts ["ALL"] as a full-scan request', () => {
    const raw = clone(LIVE_SOVD_ROWS.scan);
    raw.components = ['ALL'];
    expect(deriveLatestScan([raw])).not.toBeNull();
  });

  it('ignores a scan row with no request list', () => {
    const raw = clone(LIVE_SOVD_ROWS.scan);
    delete raw.components;
    expect(deriveLatestScan([raw])).toBeNull();
  });
});

describe('normalizeSovdRow reads results only from scan rows (S5)', () => {
  it('a run_routine row has no resultComponents', () => {
    const row = normalizeSovdRow(clone(LIVE_SOVD_ROWS.sessionRoutine));
    expect(row.resultComponents).toBeUndefined();
  });
});

describe('isFullScanRequest (review FG2 cycle 2, S1)', () => {
  it('rejects a list that names a specific ECU alongside "*"', () => {
    const raw = clone(LIVE_SOVD_ROWS.scan);
    raw.components = ['*', 'ECU_BRAKE'];
    expect(deriveLatestScan([raw])).toBeNull();
  });
});

describe('summarizeScanRow edge cases (review FG2 cycle 2, S2/S3)', () => {
  it('a DTC entry with no code makes the result partial, not complete', () => {
    const raw = clone(LIVE_SOVD_ROWS.scan) as { response: { components: Record<string, { dtcs: unknown[] }> } };
    raw.response.components.ECU_ENGINE.dtcs = [{ dtc_code: 'P0300' }];
    expect(deriveLatestScan([raw])?.results).toBe('partial');
  });

  it('marks a result offloaded when the row carries an s3Key', () => {
    const raw = clone(LIVE_SOVD_ROWS.scan) as Record<string, unknown>;
    delete raw.response;
    raw.s3Key = 'sovd-responses/x.json';
    const scan = deriveLatestScan([raw]);
    expect(scan?.results).toBe('unavailable');
    expect(scan?.offloaded).toBe(true);
  });
});
