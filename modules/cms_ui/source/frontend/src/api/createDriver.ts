// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { authFetch } from '@/utils/authFetch';

export interface CreateDriverInput {
  firstName: string;
  lastName: string;
  email: string;
  phone?: string;
  licenseNumber?: string;
  licenseExpiry?: string;
  fleetId?: string;
}

export interface CreateDriverResult {
  driver: {
    id: string;
    firstName: string;
    lastName: string;
    email: string;
    [key: string]: unknown;
  };
  message: string;
}

export class CreateDriverError extends Error {
  constructor(
    message: string,
    public readonly statusCode: number,
  ) {
    super(message);
    this.name = 'CreateDriverError';
  }
}

/**
 * Standalone create-driver wrapper usable from sub-modal contexts.
 * Mirrors the request shape of RealFleetClient.createDriver (real-fleet-client.ts).
 */
export async function createDriver(input: CreateDriverInput): Promise<CreateDriverResult> {
  const url = '/api/v1/drivers';

  let response: Response;
  try {
    response = await authFetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        entry: {
          firstName: input.firstName,
          lastName: input.lastName,
          email: input.email,
          phone: input.phone,
          licenseNumber: input.licenseNumber,
          licenseExpiry: input.licenseExpiry,
          fleetId: input.fleetId,
        },
      }),
    });
  } catch {
    throw new CreateDriverError('Network error contacting drivers API', 0);
  }

  if (!response.ok) {
    let message: string;
    try {
      const body = await response.json();
      message = body.message ?? body.error ?? `Request failed: ${response.status}`;
    } catch {
      message = `Request failed: ${response.status}`;
    }
    throw new CreateDriverError(message, response.status);
  }

  return response.json() as Promise<CreateDriverResult>;
}
