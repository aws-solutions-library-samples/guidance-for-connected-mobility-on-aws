// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { authFetch } from '@/utils/authFetch';

export interface OEM1EnrollQuotaResponse {
  remaining: number;
  submissions_in_last_hour: number;
  next_quota_reset_at: string;
}

export class OEM1EnrollQuotaError extends Error {
  constructor(
    message: string,
    public readonly statusCode: number,
  ) {
    super(message);
    this.name = 'OEM1EnrollQuotaError';
  }
}

export async function oem1EnrollQuota(): Promise<OEM1EnrollQuotaResponse> {
  const url = '/admin/oem1/enroll-quota';

  let response: Response;
  try {
    response = await authFetch(url, { method: 'GET' });
  } catch {
    throw new OEM1EnrollQuotaError('Network error contacting OEM1 enroll-quota API', 0);
  }

  if (!response.ok) {
    let message: string;
    try {
      const body = await response.json();
      message = body.message ?? body.error ?? `Request failed: ${response.status}`;
    } catch {
      message = `Request failed: ${response.status}`;
    }
    throw new OEM1EnrollQuotaError(message, response.status);
  }

  return response.json() as Promise<OEM1EnrollQuotaResponse>;
}
