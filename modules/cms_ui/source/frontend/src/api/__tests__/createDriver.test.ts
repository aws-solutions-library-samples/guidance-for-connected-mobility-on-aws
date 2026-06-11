// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createDriver, CreateDriverError } from '../createDriver';
import * as authFetchModule from '@/utils/authFetch';

vi.mock('@/utils/authFetch', () => ({
  authFetch: vi.fn(),
}));

const BASE_INPUT = {
  firstName: 'Jane',
  lastName: 'Doe',
  email: 'jane.doe@example.com',
  fleetId: 'fleet-1',
};

const BASE_RESP = {
  driver: { id: 'drv-001', firstName: 'Jane', lastName: 'Doe', email: 'jane.doe@example.com' },
  message: 'Driver created successfully',
};

beforeEach(() => vi.clearAllMocks());

describe('createDriver', () => {
  it('returns CreateDriverResult on 200', async () => {
    vi.mocked(authFetchModule.authFetch).mockResolvedValue(
      new Response(JSON.stringify(BASE_RESP), { status: 200 }),
    );
    const result = await createDriver(BASE_INPUT);
    expect(result.driver.id).toBe('drv-001');
    expect(result.message).toBe('Driver created successfully');
  });

  it('sends request to /api/v1/drivers with correct entry shape', async () => {
    vi.mocked(authFetchModule.authFetch).mockResolvedValue(
      new Response(JSON.stringify(BASE_RESP), { status: 200 }),
    );
    await createDriver(BASE_INPUT);
    const [url, init] = vi.mocked(authFetchModule.authFetch).mock.calls[0];
    expect(url).toBe('/api/v1/drivers');
    const body = JSON.parse(init!.body as string);
    expect(body.entry.firstName).toBe('Jane');
    expect(body.entry.email).toBe('jane.doe@example.com');
  });

  it('throws CreateDriverError on non-2xx', async () => {
    vi.mocked(authFetchModule.authFetch).mockResolvedValue(
      new Response(JSON.stringify({ message: 'conflict' }), { status: 409 }),
    );
    await expect(createDriver(BASE_INPUT)).rejects.toSatisfy(
      (e: unknown) => e instanceof CreateDriverError && (e as CreateDriverError).statusCode === 409,
    );
  });

  it('throws CreateDriverError with statusCode 0 on network error', async () => {
    vi.mocked(authFetchModule.authFetch).mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(createDriver(BASE_INPUT)).rejects.toSatisfy(
      (e: unknown) => e instanceof CreateDriverError && (e as CreateDriverError).statusCode === 0,
    );
  });
});
