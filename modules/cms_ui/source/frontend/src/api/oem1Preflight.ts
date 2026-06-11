// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { authFetch } from '@/utils/authFetch';

export interface PreflightVehicleResult {
  vin: string;
  isCapable: boolean;
  reason?: string;
  pdSkus?: string[];
  modelInfo?: {
    make?: string;
    model?: string;
    year?: number;
    fuelType?: string;
    engineType?: string;
  };
}

export interface OEM1PreflightRequest {
  vins: string[];
  sku: string;
}

export interface OEM1PreflightResponse {
  results: PreflightVehicleResult[];
}

export class OEM1PreflightError extends Error {
  constructor(
    message: string,
    public readonly statusCode: number,
  ) {
    super(message);
    this.name = 'OEM1PreflightError';
  }
}

export async function oem1Preflight(
  request: OEM1PreflightRequest,
): Promise<OEM1PreflightResponse> {
  const url = '/admin/oem1/preflight';

  let response: Response;
  try {
    response = await authFetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  } catch {
    throw new OEM1PreflightError('Network error contacting OEM1 preflight API', 0);
  }

  if (!response.ok) {
    let message: string;
    try {
      const body = await response.json();
      message = body.message ?? body.error ?? `Request failed: ${response.status}`;
    } catch {
      message = `Request failed: ${response.status}`;
    }
    throw new OEM1PreflightError(message, response.status);
  }

  return response.json() as Promise<OEM1PreflightResponse>;
}
