// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { authFetch } from '@/utils/authFetch';

export interface BulkUnenrollRequest {
  fleetId: string;
  vehicleIds: string[];
  sku: string;
  clientRequestId: string; // UUID v4 — caller generates; enables idempotent re-submit
  hardDelete?: boolean; // C9: default false (soft-remove); true = permanent delete
}

export interface BulkUnenrollResponse {
  requestId: string;
  acceptedCount: number;
  preFlightFailureCount: number;
  statusSummary: Record<string, string>;
}

export class BulkUnenrollError extends Error {
  constructor(
    message: string,
    public readonly statusCode: number,
  ) {
    super(message);
    this.name = 'BulkUnenrollError';
  }
}

/**
 * Submit a bulk OEM1 unenroll request.
 * clientRequestId enables idempotent re-submit — backend dedupes via GSI.
 * X-Idempotency-Replay: true in the response means a cached result was returned.
 */
export async function oem1BulkUnenroll(req: BulkUnenrollRequest): Promise<BulkUnenrollResponse> {
  const url = '/admin/oem1/bulk-unenroll';

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
        hard_delete: req.hardDelete ?? false,
      }),
    });
  } catch {
    throw new BulkUnenrollError('Network error contacting bulk-unenroll API', 0);
  }

  if (!response.ok) {
    let message: string;
    try {
      const body = await response.json();
      message = body.message ?? body.error ?? `Request failed: ${response.status}`;
    } catch {
      message = `Request failed: ${response.status}`;
    }
    throw new BulkUnenrollError(message, response.status);
  }

  return response.json() as Promise<BulkUnenrollResponse>;
}
