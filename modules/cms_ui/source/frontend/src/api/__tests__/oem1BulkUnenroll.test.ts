// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { oem1BulkUnenroll, BulkUnenrollError } from '../oem1BulkUnenroll';
import * as authFetchModule from '@/utils/authFetch';

vi.mock('@/utils/authFetch', () => ({
  authFetch: vi.fn(),
}));

const BASE_REQ = {
  fleetId: 'fleet-1',
  vehicleIds: ['VIN001'],
  sku: 'SKU-00000104',
  clientRequestId: 'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
};

const BASE_RESP = {
  requestId: 'req-xyz',
  acceptedCount: 1,
  preFlightFailureCount: 0,
  statusSummary: { VIN001: 'PENDING' },
};

beforeEach(() => vi.clearAllMocks());

describe('oem1BulkUnenroll', () => {
  it('returns BulkUnenrollResponse on 200', async () => {
    vi.mocked(authFetchModule.authFetch).mockResolvedValue(
      new Response(JSON.stringify(BASE_RESP), { status: 200 }),
    );
    const result = await oem1BulkUnenroll(BASE_REQ);
    expect(result.requestId).toBe('req-xyz');
    expect(result.acceptedCount).toBe(1);
  });

  it('passes clientRequestId in request body', async () => {
    vi.mocked(authFetchModule.authFetch).mockResolvedValue(
      new Response(JSON.stringify(BASE_RESP), { status: 200 }),
    );
    await oem1BulkUnenroll(BASE_REQ);
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
    const result = await oem1BulkUnenroll(BASE_REQ);
    expect(result.acceptedCount).toBe(1);
  });

  it('throws BulkUnenrollError with statusCode on non-2xx', async () => {
    vi.mocked(authFetchModule.authFetch).mockResolvedValue(
      new Response(JSON.stringify({ message: 'not found' }), { status: 404 }),
    );
    await expect(oem1BulkUnenroll(BASE_REQ)).rejects.toSatisfy(
      (e: unknown) => e instanceof BulkUnenrollError && (e as BulkUnenrollError).statusCode === 404,
    );
  });

  it('throws BulkUnenrollError with statusCode 0 on network error', async () => {
    vi.mocked(authFetchModule.authFetch).mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(oem1BulkUnenroll(BASE_REQ)).rejects.toSatisfy(
      (e: unknown) => e instanceof BulkUnenrollError && (e as BulkUnenrollError).statusCode === 0,
    );
  });
});
