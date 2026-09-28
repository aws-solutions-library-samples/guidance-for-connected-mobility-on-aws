// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * sovdScanClient — bounded auto-retry on rate-limited routine invocations.
 *
 * Spec: `.kiro/specs/2026-09-24-cms-diagnostics-tab-ia-redesign` Group 2, Task 2.3.
 *
 * Q5 invariant: refusal is INVISIBLE for transient cases (caller sees `Running`),
 * VISIBLE on exhaustion (resolves to `RateLimited` outcome with attempt count).
 *
 * ── Test cases ────────────────────────────────────────────────────────────────
 *
 * RS1 — succeeds on attempt 2 after one RATE_LIMITED response.
 * RS2 — exhausts after exactly 3 attempts; returns {kind:'RateLimited', attempts:3}.
 * RS3 — honours the returned `retry_after_ms` (fake timers assert the delay used).
 * RS4 — a non-rate-limit failure (FAILED status) is NOT retried; returns error outcome.
 * RS5 — a synchronous HTTP error (400) is NOT retried.
 *
 * ── Architecture note ─────────────────────────────────────────────────────────
 *
 * RATE_LIMITED arrives asynchronously: POST → sidecar → MQTT → IoT rule →
 * Lambda → DynamoDB → client poll. `runRoutineInSession` therefore accepts
 * injectable `pollFn` and `delayFn` parameters so these tests can control timing
 * deterministically without real timers or network calls.
 *
 * `authFetch` is mocked via vi.mock so the POST calls are intercepted.
 * `pollFn` is an explicit parameter, making it transparent to the test.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { runRoutineInSession } from '@/utils/sovdScanClient';

// ── Mock authFetch ─────────────────────────────────────────────────────────────

vi.mock('@/utils/authFetch', () => ({
  authFetch: vi.fn(),
}));

// Must be after vi.mock so the mocked version is imported
import * as authFetchModule from '@/utils/authFetch';
const mockAuthFetch = vi.mocked(authFetchModule.authFetch);

// ── Helpers ────────────────────────────────────────────────────────────────────

const VEHICLE_ID = 'VEH-001';
const ROUTINE_ID = 'lamp_self_check';
const SESSION_ID = 'session-abc-123';
const ATTESTATION = {
  text: 'I confirm this is safe.',
  user_email: 'operator@example.com',
  timestamp_ms: 1_700_000_000_000,
};
const COMMAND_ID = 'cmd-xyz-42';

/** Build a fake 200 POST response with a given commandId. */
function makePostOk(commandId = COMMAND_ID): Response {
  return {
    ok: true,
    status: 200,
    clone: () => makePostOk(commandId),
    json: async () => ({ success: true, commandId, status: 'SENT' }),
  } as unknown as Response;
}

/** Build a fake HTTP error response (e.g. 400). */
function makeHttpError(status = 400): Response {
  return {
    ok: false,
    status,
    clone: () => makeHttpError(status),
    json: async () => ({ error: 'bad request' }),
  } as unknown as Response;
}

/**
 * Build a `pollFn` stub that returns a given sequence of statuses.
 * Each call pops the next status from the queue; the last status is held.
 *
 * `retry_after_ms` is included in RATE_LIMITED rows to exercise RS3.
 */
function makePollFn(
  statusSequence: Array<'RATE_LIMITED' | 'SUCCEEDED' | 'FAILED' | 'IN_PROGRESS'>,
  retryAfterMs = 400,
): (_vid: string, _sid: string) => Promise<Response | undefined> {
  let callIndex = 0;
  return async (_vid: string, _sid: string): Promise<Response | undefined> => {
    const status = statusSequence[Math.min(callIndex++, statusSequence.length - 1)];
    const rows = [
      {
        commandId: COMMAND_ID,
        status,
        retry_after_ms: status === 'RATE_LIMITED' ? retryAfterMs : undefined,
        reason: status === 'FAILED' ? 'Sidecar reported failure.' : undefined,
      },
    ];
    return {
      ok: true,
      json: async () => rows,
    } as unknown as Response;
  };
}

/** An instant delay that resolves immediately (no real timer consumption). */
const instantDelay = (_ms: number): Promise<void> => Promise.resolve();

// ── Tests ──────────────────────────────────────────────────────────────────────

describe('runRoutineInSession — rate-limit auto-retry', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mockAuthFetch.mockResolvedValue(makePostOk());
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ── RS1 ────────────────────────────────────────────────────────────────────
  it('RS1 — succeeds on attempt 2 after one RATE_LIMITED poll response', async () => {
    // First poll call returns RATE_LIMITED; second returns SUCCEEDED.
    const pollFn = makePollFn(['RATE_LIMITED', 'SUCCEEDED']);

    const result = await runRoutineInSession(
      VEHICLE_ID, ROUTINE_ID, SESSION_ID, ATTESTATION,
      pollFn, instantDelay,
    );

    expect(result.kind).toBe('ok');
    // authFetch (POST) should have been called exactly twice (one per attempt).
    expect(mockAuthFetch).toHaveBeenCalledTimes(2);
  });

  // ── RS2 ────────────────────────────────────────────────────────────────────
  it('RS2 — exhausts after exactly 3 attempts and returns RateLimited outcome', async () => {
    // All three poll calls return RATE_LIMITED.
    const pollFn = makePollFn(['RATE_LIMITED', 'RATE_LIMITED', 'RATE_LIMITED']);

    const result = await runRoutineInSession(
      VEHICLE_ID, ROUTINE_ID, SESSION_ID, ATTESTATION,
      pollFn, instantDelay,
    );

    expect(result.kind).toBe('RateLimited');
    if (result.kind === 'RateLimited') {
      expect(result.attempts).toBe(3);
    }
    // POST issued exactly 3 times — one per attempt.
    expect(mockAuthFetch).toHaveBeenCalledTimes(3);
    // Does NOT resolve as success (RS2's companion assertion).
    expect(result.kind).not.toBe('ok');
  });

  // ── RS3 ────────────────────────────────────────────────────────────────────
  it('RS3 — honours retry_after_ms from the RATE_LIMITED row', async () => {
    vi.useFakeTimers();

    const RETRY_AFTER = 680;
    // Two RATE_LIMITED then SUCCEEDED; only two retry delays observed.
    const pollFn = makePollFn(['RATE_LIMITED', 'RATE_LIMITED', 'SUCCEEDED'], RETRY_AFTER);

    const delays: number[] = [];
    const capturingDelay = async (ms: number): Promise<void> => {
      delays.push(ms);
      // Must actually advance fake timers for awaits in the function under test.
      await Promise.resolve();
    };

    const promise = runRoutineInSession(
      VEHICLE_ID, ROUTINE_ID, SESSION_ID, ATTESTATION,
      pollFn, capturingDelay,
    );

    // Flush all microtasks and timers.
    await vi.runAllTimersAsync();
    const result = await promise;

    expect(result.kind).toBe('ok');
    // The first two RATE_LIMITED responses should each trigger a delay of RETRY_AFTER.
    // Additionally there are ROUTINE_POLL_INTERVAL_MS delays inside the poll loop.
    // We specifically want to see RETRY_AFTER appear in the captured delays.
    expect(delays).toContain(RETRY_AFTER);
  });

  // ── RS4 ────────────────────────────────────────────────────────────────────
  it('RS4 — non-rate-limit failure (FAILED status) is not retried', async () => {
    const pollFn = makePollFn(['FAILED']);

    const result = await runRoutineInSession(
      VEHICLE_ID, ROUTINE_ID, SESSION_ID, ATTESTATION,
      pollFn, instantDelay,
    );

    expect(result.kind).toBe('error');
    // POST issued exactly ONCE — FAILED does not trigger retry.
    expect(mockAuthFetch).toHaveBeenCalledTimes(1);
  });

  // ── RS5 ────────────────────────────────────────────────────────────────────
  it('RS5 — synchronous HTTP error (400) is not retried', async () => {
    // Override the POST mock to return a 400.
    mockAuthFetch.mockResolvedValue(makeHttpError(400));

    const pollFn = vi.fn(); // should never be called
    const result = await runRoutineInSession(
      VEHICLE_ID, ROUTINE_ID, SESSION_ID, ATTESTATION,
      pollFn as any, instantDelay,
    );

    expect(result.kind).toBe('error');
    expect(result.kind).not.toBe('RateLimited');
    expect(mockAuthFetch).toHaveBeenCalledTimes(1);
    // Poll was never invoked.
    expect(pollFn).not.toHaveBeenCalled();
  });
});


// ── Live response shape (issue 2026-09-25-diagnostics-ia-uat-defects, review W4) ─
//
// The stubs above return a bare array. The live API returns
// `{commands: [...], count}` and nests `retry_after_ms` under `response`
// (commands_lambda.py `_get_history`, command_response_handler). Against that
// shape the poll threw on every call, so auto-retry never ran and every routine
// waited out the full poll ceiling.

function makeLivePollFn(
  statusSequence: Array<'RATE_LIMITED' | 'SUCCEEDED' | 'FAILED' | 'PRECONDITION_FAILED_MOVING'>,
  retryAfterMs = 400,
): (_vid: string, _sid: string) => Promise<Response | undefined> {
  let callIndex = 0;
  return async () => {
    const status = statusSequence[Math.min(callIndex++, statusSequence.length - 1)];
    const row = {
      commandId: COMMAND_ID,
      correlationId: COMMAND_ID,
      commandType: 'run_routine',
      type: 'sovd',
      status,
      session_id: SESSION_ID,
      issuedAt: '2026-09-24T19:54:14.132733+00:00',
      response: status === 'RATE_LIMITED'
        ? { status, retry_after_ms: retryAfterMs, correlation_id: COMMAND_ID }
        : status === 'FAILED' || status === 'PRECONDITION_FAILED_MOVING'
          ? { status, reason: 'Sidecar reported failure.' }
          : { status, verdict: 'in_spec' },
    };
    return { ok: true, json: async () => ({ commands: [row], count: 1 }) } as unknown as Response;
  };
}

describe('runRoutineInSession — live {commands: [...]} response shape', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mockAuthFetch.mockResolvedValue(makePostOk());
  });

  it('finds the terminal row in {commands: [...]} and resolves ok on the first poll', async () => {
    let polls = 0;
    const live = makeLivePollFn(['SUCCEEDED']);
    const pollFn = async (v: string, s: string) => { polls += 1; return live(v, s); };
    const result = await runRoutineInSession(VEHICLE_ID, ROUTINE_ID, SESSION_ID, ATTESTATION, pollFn, instantDelay);
    expect(result.kind).toBe('ok');
    expect(polls).toBe(1);
  });

  it('retries on RATE_LIMITED from the live shape, then succeeds', async () => {
    const result = await runRoutineInSession(
      VEHICLE_ID, ROUTINE_ID, SESSION_ID, ATTESTATION,
      makeLivePollFn(['RATE_LIMITED', 'SUCCEEDED']), instantDelay,
    );
    expect(result.kind).toBe('ok');
    expect(mockAuthFetch).toHaveBeenCalledTimes(2);
  });

  it('honours retry_after_ms nested under response', async () => {
    const waits: number[] = [];
    const delay = async (ms: number) => { waits.push(ms); };
    await runRoutineInSession(
      VEHICLE_ID, ROUTINE_ID, SESSION_ID, ATTESTATION,
      makeLivePollFn(['RATE_LIMITED', 'SUCCEEDED'], 2500), delay,
    );
    expect(waits).toContain(2500);
  });

  it('carries the nested failure reason on a FAILED row', async () => {
    const result = await runRoutineInSession(
      VEHICLE_ID, ROUTINE_ID, SESSION_ID, ATTESTATION,
      makeLivePollFn(['FAILED']), instantDelay,
    );
    expect(result.kind).toBe('error');
    if (result.kind === 'error') expect(result.reason).toBe('Sidecar reported failure.');
  });
});

describe('runRoutineInSession — sidecar refusal statuses are terminal (review FG2 cycle 2, S5)', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mockAuthFetch.mockResolvedValue(makePostOk());
  });

  it('resolves on the first poll with the refusal status and nested reason', async () => {
    let polls = 0;
    const live = makeLivePollFn(['PRECONDITION_FAILED_MOVING']);
    const pollFn = async (v: string, s: string) => { polls += 1; return live(v, s); };
    const result = await runRoutineInSession(VEHICLE_ID, ROUTINE_ID, SESSION_ID, ATTESTATION, pollFn, instantDelay);
    expect(polls).toBe(1);
    expect(result.kind).toBe('error');
    if (result.kind === 'error') {
      expect(result.status).toBe('PRECONDITION_FAILED_MOVING');
      expect(result.reason).toBe('Sidecar reported failure.');
    }
  });
});
