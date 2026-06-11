// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { oem1EnrollQuota, OEM1EnrollQuotaError } from '../oem1EnrollQuota';

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

describe('oem1EnrollQuota', () => {
  it('happy path — returns quota details', async () => {
    const mockResponse = {
      remaining: 3,
      submissions_in_last_hour: 1,
      next_quota_reset_at: '2026-06-05T16:00:00Z',
    };
    mockAuthFetch.mockResolvedValueOnce(makeResponse(mockResponse));

    const result = await oem1EnrollQuota();

    expect(mockAuthFetch).toHaveBeenCalledWith('/admin/oem1/enroll-quota', { method: 'GET' });
    expect(result.remaining).toBe(3);
    expect(result.submissions_in_last_hour).toBe(1);
    expect(result.next_quota_reset_at).toBe('2026-06-05T16:00:00Z');
  });

  it('error path — throws OEM1EnrollQuotaError on 500', async () => {
    mockAuthFetch.mockResolvedValueOnce(
      makeResponse({ error: 'Internal server error' }, 500),
    );

    try {
      await oem1EnrollQuota();
    } catch (e) {
      expect(e).toBeInstanceOf(OEM1EnrollQuotaError);
      expect((e as OEM1EnrollQuotaError).statusCode).toBe(500);
      expect((e as OEM1EnrollQuotaError).name).toBe('OEM1EnrollQuotaError');
    }
  });

  it('auth failure — throws OEM1EnrollQuotaError with statusCode 403', async () => {
    mockAuthFetch.mockResolvedValueOnce(
      makeResponse({ message: 'Forbidden' }, 403),
    );

    try {
      await oem1EnrollQuota();
    } catch (e) {
      expect(e).toBeInstanceOf(OEM1EnrollQuotaError);
      expect((e as OEM1EnrollQuotaError).statusCode).toBe(403);
    }
  });

  it('network error — throws OEM1EnrollQuotaError with statusCode 0', async () => {
    mockAuthFetch.mockRejectedValueOnce(new TypeError('Failed to fetch'));

    try {
      await oem1EnrollQuota();
    } catch (e) {
      expect(e).toBeInstanceOf(OEM1EnrollQuotaError);
      expect((e as OEM1EnrollQuotaError).statusCode).toBe(0);
    }
  });
});
