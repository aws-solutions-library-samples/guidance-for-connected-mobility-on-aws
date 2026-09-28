// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { oem1BulkEnroll, BulkEnrollError } from '../oem1BulkEnroll';
import * as authFetchModule from '@/utils/authFetch';

vi.mock('@/utils/authFetch', () => ({
  authFetch: vi.fn(),
}));

const BASE_REQ = {
  fleetId: 'fleet-1',
  vehicleIds: ['VIN001', 'VIN002'],
  sku: 'SKU-00000069',
  clientRequestId: 'f47ac10b-58cc-4372-a567-0e02b2c3d479',
};

const BASE_RESP = {
  requestId: 'req-abc',
  acceptedCount: 2,
  preFlightFailureCount: 0,
  statusSummary: { VIN001: 'PENDING', VIN002: 'PENDING' },
};

beforeEach(() => vi.clearAllMocks());

describe('oem1BulkEnroll', () => {
  it('returns BulkEnrollResponse on 200', async () => {
    vi.mocked(authFetchModule.authFetch).mockResolvedValue(
      new Response(JSON.stringify(BASE_RESP), { status: 200 }),
    );
    const result = await oem1BulkEnroll(BASE_REQ);
    expect(result.requestId).toBe('req-abc');
    expect(result.acceptedCount).toBe(2);
  });

  it('passes clientRequestId in request body', async () => {
    vi.mocked(authFetchModule.authFetch).mockResolvedValue(
      new Response(JSON.stringify(BASE_RESP), { status: 200 }),
    );
    await oem1BulkEnroll(BASE_REQ);
    const [, init] = vi.mocked(authFetchModule.authFetch).mock.calls[0];
    const body = JSON.parse(init!.body as string);
    expect(body.clientRequestId).toBe(BASE_REQ.clientRequestId);
  });

  it('handles X-Idempotency-Replay response gracefully (same shape)', async () => {
    vi.mocked(authFetchModule.authFetch).mockResolvedValue(
      new Response(JSON.stringify(BASE_RESP), {
        status: 200,
        headers: { 'X-Idempotency-Replay': 'true' },
      }),
    );
    const result = await oem1BulkEnroll(BASE_REQ);
    expect(result.acceptedCount).toBe(2);
  });

  it('throws BulkEnrollError with statusCode on non-2xx', async () => {
    vi.mocked(authFetchModule.authFetch).mockResolvedValue(
      new Response(JSON.stringify({ message: 'quota exceeded' }), { status: 429 }),
    );
    await expect(oem1BulkEnroll(BASE_REQ)).rejects.toSatisfy(
      (e: unknown) => e instanceof BulkEnrollError && (e as BulkEnrollError).statusCode === 429,
    );
  });

  it('throws BulkEnrollError with statusCode 0 on network error', async () => {
    vi.mocked(authFetchModule.authFetch).mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(oem1BulkEnroll(BASE_REQ)).rejects.toSatisfy(
      (e: unknown) => e instanceof BulkEnrollError && (e as BulkEnrollError).statusCode === 0,
    );
  });
});
