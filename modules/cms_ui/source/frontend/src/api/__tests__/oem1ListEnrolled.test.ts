// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { oem1ListEnrolled, OEM1ListEnrolledError } from '../oem1ListEnrolled';

vi.mock('@/utils/authFetch');
import { authFetch } from '@/utils/authFetch';
const mockAuthFetch = vi.mocked(authFetch);

function makeResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    json: async () => body,
  } as unknown as Response;
}

beforeEach(() => {
  vi.resetAllMocks();
});

describe('oem1ListEnrolled', () => {
  it('happy path — returns enrolled vehicle reconciliation data', async () => {
    const mockResponse = {
      enrolled_at_oem1: 5,
      enrolled_in_cms: 4,
      missing_in_cms: 1,
      vehicles: [
        { vin: '1FTFW1E16JFD55835', oem1_enrollment_status: 'COMPLETED', oem1_active_sku: 'SKU-X', in_cms: true },
        { vin: '3FA6P0LUXKR100601', oem1_enrollment_status: 'COMPLETED', oem1_active_sku: 'SKU-X', in_cms: false },
      ],
    };
    mockAuthFetch.mockResolvedValueOnce(makeResponse(mockResponse));

    const result = await oem1ListEnrolled();

    expect(mockAuthFetch).toHaveBeenCalledWith('/admin/oem1/list-enrolled', { method: 'GET' });
    expect(result.enrolled_at_oem1).toBe(5);
    expect(result.missing_in_cms).toBe(1);
    expect(result.vehicles).toHaveLength(2);
  });

  it('error path — throws OEM1ListEnrolledError on 500', async () => {
    mockAuthFetch.mockResolvedValueOnce(
      makeResponse({ error: 'Internal server error' }, 500),
    );

    try {
      await oem1ListEnrolled();
    } catch (e) {
      expect(e).toBeInstanceOf(OEM1ListEnrolledError);
      expect((e as OEM1ListEnrolledError).statusCode).toBe(500);
      expect((e as OEM1ListEnrolledError).name).toBe('OEM1ListEnrolledError');
    }
  });

  it('auth failure — throws OEM1ListEnrolledError with statusCode 403', async () => {
    mockAuthFetch.mockResolvedValueOnce(
      makeResponse({ message: 'Forbidden' }, 403),
    );

    try {
      await oem1ListEnrolled();
    } catch (e) {
      expect(e).toBeInstanceOf(OEM1ListEnrolledError);
      expect((e as OEM1ListEnrolledError).statusCode).toBe(403);
    }
  });

  it('network error — throws OEM1ListEnrolledError with statusCode 0', async () => {
    mockAuthFetch.mockRejectedValueOnce(new TypeError('Failed to fetch'));

    try {
      await oem1ListEnrolled();
    } catch (e) {
      expect(e).toBeInstanceOf(OEM1ListEnrolledError);
      expect((e as OEM1ListEnrolledError).statusCode).toBe(0);
    }
  });
});
