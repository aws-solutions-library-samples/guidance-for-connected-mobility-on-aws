// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { authFetch } from '@/utils/authFetch';

export type EnrollmentStatus = 'COMPLETED' | 'PENDING' | 'FAILED' | 'UNKNOWN';

export interface AddOEM1VehicleResult {
  vehicleId: string;
  enrollmentStatus: EnrollmentStatus;
  writeStatus: 'inserted' | 'updated' | 'pending' | 'already_enrolled';
}

export class AddOEM1VehicleError extends Error {
  constructor(
    message: string,
    public readonly statusCode: number,
  ) {
    super(message);
    this.name = 'AddOEM1VehicleError';
  }
}

export async function addOEM1Vehicle(
  vin: string,
  fleetId: string,
): Promise<AddOEM1VehicleResult> {
  const url = '/admin/oem1/add-vehicle';

  let response: Response;
  try {
    response = await authFetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ vin, fleetId }),
    });
  } catch {
    throw new AddOEM1VehicleError(
      'Network error contacting OEM1 admin API',
      0,
    );
  }

  if (!response.ok) {
    if (response.status >= 500) {
      throw new AddOEM1VehicleError('OEM1 backend error', response.status);
    }
    let message: string;
    try {
      const body = await response.json();
      message = body.message ?? body.error ?? `Request failed: ${response.status}`;
    } catch {
      message = `Request failed: ${response.status}`;
    }
    throw new AddOEM1VehicleError(message, response.status);
  }

  return response.json() as Promise<AddOEM1VehicleResult>;
}
