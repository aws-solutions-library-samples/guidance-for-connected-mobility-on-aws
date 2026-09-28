// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { authFetch } from '@/utils/authFetch';

export interface BulkEnrollRequest {
  fleetId: string;
  vehicleIds: string[];
  sku: string;
  clientRequestId: string; // UUID v4 — caller generates; enables idempotent re-submit
}

export interface BulkEnrollResponse {
  requestId: string;
  acceptedCount: number;
  preFlightFailureCount: number;
  statusSummary: Record<string, string>;
}

export class BulkEnrollError extends Error {
  constructor(
    message: string,
    public readonly statusCode: number,
  ) {
    super(message);
    this.name = 'BulkEnrollError';
  }
}

/**
 * Submit a bulk OEM1 enroll request.
 * clientRequestId enables idempotent re-submit — backend dedupes via GSI.
 * X-Idempotency-Replay: true in the response means a cached result was returned.
 */
export async function oem1BulkEnroll(req: BulkEnrollRequest): Promise<BulkEnrollResponse> {
  const url = '/admin/oem1/bulk-enroll';

  let response: Response;
  try {
    response = await authFetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        fleetId: req.fleetId,
        vehicleIds: req.vehicleIds,
        sku: req.sku,
        clientRequestId: req.clientRequestId,
      }),
    });
  } catch {
    throw new BulkEnrollError('Network error contacting bulk-enroll API', 0);
  }

  if (!response.ok) {
    let message: string;
    try {
      const body = await response.json();
      message = body.message ?? body.error ?? `Request failed: ${response.status}`;
    } catch {
      message = `Request failed: ${response.status}`;
    }
    throw new BulkEnrollError(message, response.status);
  }

  return response.json() as Promise<BulkEnrollResponse>;
}
