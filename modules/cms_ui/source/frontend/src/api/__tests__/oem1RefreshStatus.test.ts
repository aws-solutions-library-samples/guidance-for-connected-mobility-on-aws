// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { oem1RefreshStatus, OEM1RefreshStatusError } from '../oem1RefreshStatus';

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

describe('oem1RefreshStatus', () => {
  it('happy path — returns refreshed vehicle status', async () => {
    const mockResponse = {
      refreshed: [
        {
          vehicleId: '1FTFW1E16JFD55835',
          oem1_enrollment_status: 'COMPLETED',
          oem1_fcs_code: 3,
          oem1_status_refreshed_at: '2026-06-05T18:00:00Z',
        },
      ],
    };
    mockAuthFetch.mockResolvedValueOnce(makeResponse(mockResponse));

    const result = await oem1RefreshStatus({ vehicle_ids: ['1FTFW1E16JFD55835'] });

    expect(mockAuthFetch).toHaveBeenCalledWith('/admin/oem1/refresh-status', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ vehicle_ids: ['1FTFW1E16JFD55835'] }),
    });
    expect(result.refreshed[0].oem1_enrollment_status).toBe('COMPLETED');
  });

  it('error path — throws OEM1RefreshStatusError on 429 rate limit', async () => {
    mockAuthFetch.mockResolvedValueOnce(
      makeResponse({ error: 'Rate limit exceeded' }, 429),
    );

    try {
      await oem1RefreshStatus({ vehicle_ids: ['1FTFW1E16JFD55835'] });
    } catch (e) {
      expect(e).toBeInstanceOf(OEM1RefreshStatusError);
      expect((e as OEM1RefreshStatusError).statusCode).toBe(429);
      expect((e as OEM1RefreshStatusError).name).toBe('OEM1RefreshStatusError');
    }
  });

  it('auth failure — throws OEM1RefreshStatusError with statusCode 403', async () => {
    mockAuthFetch.mockResolvedValueOnce(
      makeResponse({ message: 'Forbidden' }, 403),
    );

    try {
      await oem1RefreshStatus({ vehicle_ids: ['1FTFW1E16JFD55835'] });
    } catch (e) {
      expect(e).toBeInstanceOf(OEM1RefreshStatusError);
      expect((e as OEM1RefreshStatusError).statusCode).toBe(403);
    }
  });

  it('network error — throws OEM1RefreshStatusError with statusCode 0', async () => {
    mockAuthFetch.mockRejectedValueOnce(new TypeError('Failed to fetch'));

    try {
      await oem1RefreshStatus({ vehicle_ids: ['1FTFW1E16JFD55835'] });
    } catch (e) {
      expect(e).toBeInstanceOf(OEM1RefreshStatusError);
      expect((e as OEM1RefreshStatusError).statusCode).toBe(0);
    }
  });
});
