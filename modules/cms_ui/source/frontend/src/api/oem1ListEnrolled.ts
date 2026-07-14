// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { authFetch } from '@/utils/authFetch';
import type { OEM1EnrollmentStatus } from '@/types/fleet-types';

export interface EnrolledVehicle {
  vin: string;
  oem1_enrollment_status?: OEM1EnrollmentStatus | string;
  oem1_active_sku?: string;
  in_cms: boolean;
}

export interface OEM1ListEnrolledResponse {
  enrolled_at_oem1: number;
  enrolled_in_cms: number;
  missing_in_cms: number;
  vehicles: EnrolledVehicle[];
}

export class OEM1ListEnrolledError extends Error {
  constructor(
    message: string,
    public readonly statusCode: number,
  ) {
    super(message);
    this.name = 'OEM1ListEnrolledError';
  }
}

export async function oem1ListEnrolled(): Promise<OEM1ListEnrolledResponse> {
  const url = '/admin/oem1/list-enrolled';

  let response: Response;
  try {
    response = await authFetch(url, { method: 'GET' });
  } catch {
    throw new OEM1ListEnrolledError('Network error contacting OEM1 list-enrolled API', 0);
  }

  if (!response.ok) {
    let message: string;
    try {
      const body = await response.json();
      message = body.message ?? body.error ?? `Request failed: ${response.status}`;
    } catch {
      message = `Request failed: ${response.status}`;
    }
    throw new OEM1ListEnrolledError(message, response.status);
  }

  return response.json() as Promise<OEM1ListEnrolledResponse>;
}
