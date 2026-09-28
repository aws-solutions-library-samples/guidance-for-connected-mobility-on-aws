// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// diagnosticSessions — pure unit tests.
//
// Spec: `.kiro/specs/2026-09-24-cms-diagnostics-tab-ia-redesign` Group 2, Task 2.2.
//
// Required coverage (per tasks.md § 2.2 Verify):
//   ✓ a session with mixed verdicts returns the worst
//   ✓ a session with one non-terminal row is Active
//   ✓ unsessioned rows survive (not dropped)
//   ✓ empty input returns an empty list, not a throw
//
// Pure — no DOM, no fetch, no React, no Date.now().

import { describe, it, expect } from 'vitest';
import {
  groupSovdRowsIntoSessions,
  UNSESSIONED_SESSION_ID,
  type SovdCommandRow,
} from '@/utils/diagnosticSessions';

// ── Fixtures ──────────────────────────────────────────────────────────────────

const SESSION_A = 'session-alpha';
const SESSION_B = 'session-beta';

/** A completed run_routine row for session A. */
function row(
  sessionId: string | undefined,
  status: string,
  verdict?: string,
  overrides?: Partial<SovdCommandRow>,
): SovdCommandRow {
  return {
    session_id: sessionId,
    commandType: 'run_routine',
    status,
    submittedAt: '2026-09-24T12:00:00.000Z',
    response: verdict !== undefined ? { verdict } : undefined,
    ...overrides,
  };
}

// ── Empty input ────────────────────────────────────────────────────────────────

describe('groupSovdRowsIntoSessions — empty input', () => {
  it('returns an empty array without throwing', () => {
    const result = groupSovdRowsIntoSessions([]);
    expect(result).toEqual([]);
  });

  it('returns an empty array without throwing when a `now` argument is supplied', () => {
    const result = groupSovdRowsIntoSessions([], new Date('2026-09-24T00:00:00Z'));
    expect(result).toEqual([]);
  });
});

// ── Worst-verdict derivation ───────────────────────────────────────────────────

describe('groupSovdRowsIntoSessions — worstVerdict', () => {
  it('single in_spec row → worstVerdict is in_spec', () => {
    const rows: SovdCommandRow[] = [row(SESSION_A, 'SUCCEEDED', 'in_spec')];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions).toHaveLength(1);
    expect(sessions[0].worstVerdict).toBe('in_spec');
  });

  it('single marginal row → worstVerdict is marginal', () => {
    const rows: SovdCommandRow[] = [row(SESSION_A, 'SUCCEEDED', 'marginal')];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].worstVerdict).toBe('marginal');
  });

  it('single out_of_spec row → worstVerdict is out_of_spec', () => {
    const rows: SovdCommandRow[] = [row(SESSION_A, 'SUCCEEDED', 'out_of_spec')];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].worstVerdict).toBe('out_of_spec');
  });

  it('mixed [in_spec, marginal] → worst is marginal', () => {
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'SUCCEEDED', 'in_spec'),
      row(SESSION_A, 'SUCCEEDED', 'marginal'),
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].worstVerdict).toBe('marginal');
  });

  it('mixed [in_spec, marginal, out_of_spec] → worst is out_of_spec', () => {
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'SUCCEEDED', 'in_spec'),
      row(SESSION_A, 'SUCCEEDED', 'marginal'),
      row(SESSION_A, 'SUCCEEDED', 'out_of_spec'),
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].worstVerdict).toBe('out_of_spec');
  });

  it('mixed [out_of_spec, in_spec] in reverse order → worst is still out_of_spec', () => {
    // Order must not matter — out_of_spec wins regardless of position.
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'SUCCEEDED', 'out_of_spec'),
      row(SESSION_A, 'SUCCEEDED', 'in_spec'),
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].worstVerdict).toBe('out_of_spec');
  });

  it('no response on any row → worstVerdict is none', () => {
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'SUCCEEDED', undefined),
      row(SESSION_A, 'FAILED', undefined),
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].worstVerdict).toBe('none');
  });

  it('unrecognised verdict string → treated as none', () => {
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'SUCCEEDED', 'UNKNOWN_VERDICT'),
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].worstVerdict).toBe('none');
  });

  it('mix of in_spec and no verdict → in_spec wins over none', () => {
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'SUCCEEDED', 'in_spec'),
      row(SESSION_A, 'SUCCEEDED', undefined),
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].worstVerdict).toBe('in_spec');
  });
});

// ── Session status derivation ─────────────────────────────────────────────────

describe('groupSovdRowsIntoSessions — session status', () => {
  it('all rows terminal (SUCCEEDED) → status is Complete', () => {
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'SUCCEEDED', 'in_spec'),
      row(SESSION_A, 'SUCCEEDED', 'marginal'),
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].status).toBe('Complete');
  });

  it('all rows terminal (FAILED) → status is Complete', () => {
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'FAILED'),
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].status).toBe('Complete');
  });

  it('at least one IN_PROGRESS row → status is Active', () => {
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'SUCCEEDED', 'in_spec'),
      row(SESSION_A, 'IN_PROGRESS'),
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].status).toBe('Active');
  });

  it('single IN_PROGRESS row → status is Active', () => {
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'IN_PROGRESS'),
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].status).toBe('Active');
  });

  it('at least one SENT row → status is Active', () => {
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'SENT'),
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].status).toBe('Active');
  });

  it('row with empty-string status → status is Active (non-terminal)', () => {
    const rows: SovdCommandRow[] = [
      row(SESSION_A, ''),
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].status).toBe('Active');
  });

  it('row with undefined status → status is Active (non-terminal)', () => {
    const rows: SovdCommandRow[] = [
      { session_id: SESSION_A, commandType: 'run_routine', submittedAt: '2026-09-24T12:00:00.000Z' },
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].status).toBe('Active');
  });

  it('all rows terminal AND at least one RATE_LIMITED → status is RateLimited', () => {
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'SUCCEEDED', 'in_spec'),
      row(SESSION_A, 'RATE_LIMITED'),
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].status).toBe('RateLimited');
  });

  it('single RATE_LIMITED row → status is RateLimited', () => {
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'RATE_LIMITED'),
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].status).toBe('RateLimited');
  });

  it('IN_PROGRESS row alongside RATE_LIMITED → status is Active (non-terminal wins)', () => {
    // A session that has one exhausted run AND one still-running run is Active,
    // not RateLimited — the Active status is a superset signal.
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'IN_PROGRESS'),
      row(SESSION_A, 'RATE_LIMITED'),
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].status).toBe('Active');
  });
});

// ── Unsessioned rows ──────────────────────────────────────────────────────────

describe('groupSovdRowsIntoSessions — unsessioned rows survive', () => {
  it('row with no session_id is not dropped', () => {
    const rows: SovdCommandRow[] = [
      { commandType: 'run_routine', status: 'SUCCEEDED', submittedAt: '2026-09-01T10:00:00.000Z' },
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions).toHaveLength(1);
    expect(sessions[0].sessionId).toBe(UNSESSIONED_SESSION_ID);
  });

  it('row with undefined session_id is not dropped', () => {
    const rows: SovdCommandRow[] = [
      { session_id: undefined, commandType: 'run_routine', status: 'SUCCEEDED' },
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions).toHaveLength(1);
    expect(sessions[0].sessionId).toBe(UNSESSIONED_SESSION_ID);
  });

  it('row with empty-string session_id falls into unsessioned bucket', () => {
    const rows: SovdCommandRow[] = [
      { session_id: '', commandType: 'run_routine', status: 'SUCCEEDED' },
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions).toHaveLength(1);
    expect(sessions[0].sessionId).toBe(UNSESSIONED_SESSION_ID);
  });

  it('multiple unsessioned rows are merged into a single bucket', () => {
    const rows: SovdCommandRow[] = [
      { commandType: 'run_routine', status: 'SUCCEEDED', submittedAt: '2026-09-01T10:00:00.000Z' },
      { commandType: 'run_routine', status: 'SUCCEEDED', submittedAt: '2026-09-01T11:00:00.000Z' },
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions).toHaveLength(1);
    expect(sessions[0].sessionId).toBe(UNSESSIONED_SESSION_ID);
    expect(sessions[0].routineCount).toBe(2);
  });

  it('unsessioned rows coexist with sessioned rows', () => {
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'SUCCEEDED', 'in_spec'),
      { commandType: 'run_routine', status: 'SUCCEEDED', submittedAt: '2026-09-01T10:00:00.000Z' },
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions).toHaveLength(2);
    const ids = sessions.map(s => s.sessionId);
    expect(ids).toContain(SESSION_A);
    expect(ids).toContain(UNSESSIONED_SESSION_ID);
  });
});

// ── camelCase output vs snake_case input ─────────────────────────────────────

describe('groupSovdRowsIntoSessions — sessionId casing', () => {
  it('exposes sessionId (camel) even though rows carry session_id (snake)', () => {
    const rows: SovdCommandRow[] = [row('my-session-001', 'SUCCEEDED')];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].sessionId).toBe('my-session-001');
    // Ensure no snake_case leak in the output shape
    expect(sessions[0]).not.toHaveProperty('session_id');
  });
});

// ── routineCount ──────────────────────────────────────────────────────────────

describe('groupSovdRowsIntoSessions — routineCount', () => {
  it('counts only run_routine rows', () => {
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'SUCCEEDED', 'in_spec'),
      { session_id: SESSION_A, commandType: 'read_dtcs', status: 'SUCCEEDED' },
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].routineCount).toBe(1);
  });

  it('counts run_routine from `type` field when commandType is absent', () => {
    const rows: SovdCommandRow[] = [
      { session_id: SESSION_A, type: 'run_routine', status: 'SUCCEEDED' },
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].routineCount).toBe(1);
  });

  it('routineCount is 0 when no run_routine rows exist', () => {
    const rows: SovdCommandRow[] = [
      { session_id: SESSION_A, commandType: 'read_dtcs', status: 'SUCCEEDED' },
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].routineCount).toBe(0);
  });
});

// ── startedAt ────────────────────────────────────────────────────────────────

describe('groupSovdRowsIntoSessions — startedAt', () => {
  it('is the earliest submittedAt among session rows', () => {
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'SUCCEEDED', 'in_spec', { submittedAt: '2026-09-24T14:00:00.000Z' }),
      row(SESSION_A, 'SUCCEEDED', 'in_spec', { submittedAt: '2026-09-24T12:00:00.000Z' }),
      row(SESSION_A, 'SUCCEEDED', 'in_spec', { submittedAt: '2026-09-24T13:00:00.000Z' }),
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].startedAt).toBe('2026-09-24T12:00:00.000Z');
  });

  it('is undefined when no row has a submittedAt', () => {
    const rows: SovdCommandRow[] = [
      { session_id: SESSION_A, commandType: 'run_routine', status: 'SUCCEEDED' },
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].startedAt).toBeUndefined();
  });
});

// ── dispatchedRoId / dispatchedAt ─────────────────────────────────────────────

describe('groupSovdRowsIntoSessions — dispatch stamp', () => {
  it('dispatchedRoId and dispatchedAt are undefined when no row carries the stamp', () => {
    const rows: SovdCommandRow[] = [row(SESSION_A, 'SUCCEEDED', 'in_spec')];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].dispatchedRoId).toBeUndefined();
    expect(sessions[0].dispatchedAt).toBeUndefined();
  });

  it('populates dispatchedRoId / dispatchedAt from a stamped row', () => {
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'SUCCEEDED', 'in_spec', {
        dispatched_ro_id: 'RO-1042',
        dispatched_at: '2026-09-24T15:00:00.000Z',
      }),
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].dispatchedRoId).toBe('RO-1042');
    expect(sessions[0].dispatchedAt).toBe('2026-09-24T15:00:00.000Z');
  });

  it('when multiple rows are stamped, the most recent dispatchedAt wins', () => {
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'SUCCEEDED', 'in_spec', {
        dispatched_ro_id: 'RO-1000',
        dispatched_at: '2026-09-24T14:00:00.000Z',
      }),
      row(SESSION_A, 'SUCCEEDED', 'in_spec', {
        dispatched_ro_id: 'RO-1042',
        dispatched_at: '2026-09-24T15:00:00.000Z',
      }),
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions[0].dispatchedRoId).toBe('RO-1042');
    expect(sessions[0].dispatchedAt).toBe('2026-09-24T15:00:00.000Z');
  });
});

// ── Multiple sessions ─────────────────────────────────────────────────────────

describe('groupSovdRowsIntoSessions — multiple sessions', () => {
  it('separates rows into distinct sessions by session_id', () => {
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'SUCCEEDED', 'in_spec'),
      row(SESSION_B, 'IN_PROGRESS'),
    ];
    const sessions = groupSovdRowsIntoSessions(rows);
    expect(sessions).toHaveLength(2);

    const sA = sessions.find(s => s.sessionId === SESSION_A);
    const sB = sessions.find(s => s.sessionId === SESSION_B);

    expect(sA?.status).toBe('Complete');
    expect(sB?.status).toBe('Active');
  });

  it('worst verdict is computed per session independently', () => {
    const rows: SovdCommandRow[] = [
      row(SESSION_A, 'SUCCEEDED', 'out_of_spec'),
      row(SESSION_B, 'SUCCEEDED', 'in_spec'),
    ];
    const sessions = groupSovdRowsIntoSessions(rows);

    const sA = sessions.find(s => s.sessionId === SESSION_A);
    const sB = sessions.find(s => s.sessionId === SESSION_B);

    expect(sA?.worstVerdict).toBe('out_of_spec');
    expect(sB?.worstVerdict).toBe('in_spec');
  });
});

// ── `now` argument ────────────────────────────────────────────────────────────

describe('groupSovdRowsIntoSessions — now argument', () => {
  it('accepts a Date object as now without affecting output', () => {
    const rows: SovdCommandRow[] = [row(SESSION_A, 'SUCCEEDED', 'in_spec')];
    const withNow = groupSovdRowsIntoSessions(rows, new Date('2026-09-24T16:00:00Z'));
    const withoutNow = groupSovdRowsIntoSessions(rows);
    expect(withNow).toEqual(withoutNow);
  });

  it('accepts a string as now without affecting output', () => {
    const rows: SovdCommandRow[] = [row(SESSION_A, 'SUCCEEDED', 'in_spec')];
    const withNow = groupSovdRowsIntoSessions(rows, '2026-09-24T16:00:00Z');
    const withoutNow = groupSovdRowsIntoSessions(rows);
    expect(withNow).toEqual(withoutNow);
  });

  it('accepts a number timestamp as now without affecting output', () => {
    const rows: SovdCommandRow[] = [row(SESSION_A, 'SUCCEEDED', 'in_spec')];
    const withNow = groupSovdRowsIntoSessions(rows, Date.now());
    const withoutNow = groupSovdRowsIntoSessions(rows);
    expect(withNow).toEqual(withoutNow);
  });
});


// ── Retried routines (issue 2026-09-25-diagnostics-ia-uat-defects) ────────────
//
// The auto-retry writes one row per attempt. Staging session a11296bf… on
// VEH-MRDN-0001 (2026-09-24) ran three routines; two were rate-limited once and
// then succeeded. Counting rows made it read "Rate limited" with 6 routines.

describe('groupSovdRowsIntoSessions — retried routines', () => {
  const attempt = (routineId: string, status: string, at: string, verdict?: string): SovdCommandRow =>
    row(SESSION_A, status, verdict, { routineId, submittedAt: at });

  // The live sequence, in issue order.
  const LIVE_SEQUENCE: SovdCommandRow[] = [
    attempt('lamp_self_check', 'SUCCEEDED', '2026-09-24T19:54:08Z', 'out_of_spec'),
    attempt('pack_isolation_test', 'RATE_LIMITED', '2026-09-24T19:54:14Z'),
    attempt('cell_balance_check', 'RATE_LIMITED', '2026-09-24T19:54:19Z'),
    attempt('pack_isolation_test', 'SUCCEEDED', '2026-09-24T19:55:16Z', 'in_spec'),
    attempt('cell_balance_check', 'SUCCEEDED', '2026-09-24T19:59:46Z', 'in_spec'),
    attempt('cell_balance_check', 'SUCCEEDED', '2026-09-24T20:00:23Z', 'in_spec'),
  ];

  it('a session whose rate-limited routines later succeeded is Complete', () => {
    const [s] = groupSovdRowsIntoSessions(LIVE_SEQUENCE);
    expect(s.status).toBe('Complete');
  });

  it('counts distinct routines, not attempts', () => {
    const [s] = groupSovdRowsIntoSessions(LIVE_SEQUENCE);
    expect(s.routineCount).toBe(3);
  });

  it('is order-independent: the latest attempt by time decides, not row position', () => {
    const [s] = groupSovdRowsIntoSessions([...LIVE_SEQUENCE].reverse());
    expect(s.status).toBe('Complete');
    expect(s.routineCount).toBe(3);
  });

  it('is RateLimited when a routine\'s latest attempt was rate-limited', () => {
    const [s] = groupSovdRowsIntoSessions([
      attempt('lamp_self_check', 'SUCCEEDED', '2026-09-24T19:54:08Z', 'in_spec'),
      attempt('pack_isolation_test', 'SUCCEEDED', '2026-09-24T19:54:14Z', 'in_spec'),
      attempt('pack_isolation_test', 'RATE_LIMITED', '2026-09-24T19:58:00Z'),
    ]);
    expect(s.status).toBe('RateLimited');
    expect(s.routineCount).toBe(2);
  });

  it('keeps the worst verdict across every attempt', () => {
    const [s] = groupSovdRowsIntoSessions(LIVE_SEQUENCE);
    expect(s.worstVerdict).toBe('out_of_spec');
  });
});

describe('groupSovdRowsIntoSessions — attempts with equal timestamps (review S1)', () => {
  it('keeps the first-listed attempt, which is the newest in API order', () => {
    const [s] = groupSovdRowsIntoSessions([
      row(SESSION_A, 'SUCCEEDED', 'in_spec', { routineId: 'lamp_self_check', submittedAt: '2026-09-24T19:54:08Z' }),
      row(SESSION_A, 'RATE_LIMITED', undefined, { routineId: 'lamp_self_check', submittedAt: '2026-09-24T19:54:08Z' }),
    ]);
    expect(s.status).toBe('Complete');
  });
});
