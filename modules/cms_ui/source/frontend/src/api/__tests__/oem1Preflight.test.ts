// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { oem1Preflight, OEM1PreflightError } from '../oem1Preflight';

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

describe('oem1Preflight', () => {
  it('happy path — returns results for capable VINs', async () => {
    const mockResponse = {
      results: [{ vin: '1FTFW1E16JFD55835', isCapable: true, pdSkus: ['PD-X'] }],
    };
    mockAuthFetch.mockResolvedValueOnce(makeResponse(mockResponse));

    const result = await oem1Preflight({ vins: ['1FTFW1E16JFD55835'], sku: 'SKU-X' });

    expect(mockAuthFetch).toHaveBeenCalledWith('/admin/oem1/preflight', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ vins: ['1FTFW1E16JFD55835'], sku: 'SKU-X' }),
    });
    expect(result.results[0].isCapable).toBe(true);
  });

  it('error path — throws OEM1PreflightError with statusCode on 4xx', async () => {
    mockAuthFetch.mockResolvedValue(makeResponse({ error: 'Bad request' }, 400));

    let caught: unknown;
    try {
      await oem1Preflight({ vins: ['BAD-VIN'], sku: 'SKU-X' });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(OEM1PreflightError);
    expect((caught as OEM1PreflightError).statusCode).toBe(400);
    expect((caught as OEM1PreflightError).name).toBe('OEM1PreflightError');
  });

  it('auth failure — throws OEM1PreflightError with statusCode 403', async () => {
    mockAuthFetch.mockResolvedValueOnce(
      makeResponse({ message: 'Forbidden' }, 403),
    );

    try {
      await oem1Preflight({ vins: ['1FTFW1E16JFD55835'], sku: 'SKU-X' });
    } catch (e) {
      expect(e).toBeInstanceOf(OEM1PreflightError);
      expect((e as OEM1PreflightError).statusCode).toBe(403);
    }
  });

  it('network error — throws OEM1PreflightError with statusCode 0', async () => {
    mockAuthFetch.mockRejectedValueOnce(new TypeError('Failed to fetch'));

    try {
      await oem1Preflight({ vins: ['1FTFW1E16JFD55835'], sku: 'SKU-X' });
    } catch (e) {
      expect(e).toBeInstanceOf(OEM1PreflightError);
      expect((e as OEM1PreflightError).statusCode).toBe(0);
    }
  });
});
