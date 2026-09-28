// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { authFetch } from '@/utils/authFetch';
import type { OEM1EnrollmentStatus, OEM1ReadinessSummary } from '@/types/fleet-types';

export interface RefreshStatusVehicleResult {
  vehicleId: string;
  oem1_enrollment_status?: OEM1EnrollmentStatus | string;
  oem1_fcs_code?: number;
  oem1_status_message?: string;
  oem1_readiness_summary?: OEM1ReadinessSummary | string;
  oem1_status_refreshed_at?: string;
  error?: string;
}

export interface OEM1RefreshStatusRequest {
  vehicle_ids: string[];
}

export interface OEM1RefreshStatusResponse {
  refreshed: RefreshStatusVehicleResult[];
}

export class OEM1RefreshStatusError extends Error {
  constructor(
    message: string,
    public readonly statusCode: number,
  ) {
    super(message);
    this.name = 'OEM1RefreshStatusError';
  }
}

export async function oem1RefreshStatus(
  request: OEM1RefreshStatusRequest,
): Promise<OEM1RefreshStatusResponse> {
  const url = '/admin/oem1/refresh-status';

  let response: Response;
  try {
    response = await authFetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  } catch {
    throw new OEM1RefreshStatusError('Network error contacting OEM1 refresh-status API', 0);
  }

  if (!response.ok) {
    let message: string;
    try {
      const body = await response.json();
      message = body.message ?? body.error ?? `Request failed: ${response.status}`;
    } catch {
      message = `Request failed: ${response.status}`;
    }
    throw new OEM1RefreshStatusError(message, response.status);
  }

  return response.json() as Promise<OEM1RefreshStatusResponse>;
}
