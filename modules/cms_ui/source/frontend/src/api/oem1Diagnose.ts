// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

export type ActionItemSeverity = 'critical' | 'warning' | 'info';

export type ActionItemCategory = 'ccs-off' | 'transport-mode' | 'lifecycle';

export interface VehicleActionItem {
  category: ActionItemCategory;
  severity: ActionItemSeverity;
  message: string;
}

export interface VehicleStateResponse {
  vehicleId: string;
  ccsEnabled: boolean;
  transportMode: string | null;
  lifecycleStatus: string;
  actionItems: VehicleActionItem[];
}

export class VehicleStateError extends Error {
  constructor(
    message: string,
    public readonly statusCode: number,
  ) {
    super(message);
    this.name = 'VehicleStateError';
  }
}

/**
 * Fetch vehicle readiness state via the CMS admin proxy.
 * Stub for C1.2 — real proxy Lambda is C2.2 (user-gated).
 * Returns mock data shaped per the production response contract.
 */
export async function fetchVehicleState(
  vehicleId: string,
): Promise<VehicleStateResponse> {
  // Stub: simulate the proxy endpoint POST /admin/oem1/vehicle-state/{vehicleId}
  // Replace with a real fetch call when C2.2 is deployed.
  const url = `/admin/oem1/vehicle-state/${encodeURIComponent(vehicleId)}`;

  let response: Response;
  try {
    response = await fetch(url, { method: 'POST' });
  } catch (err) {
    throw new VehicleStateError(
      `Network error fetching vehicle state for ${vehicleId}`,
      0,
    );
  }

  if (!response.ok) {
    throw new VehicleStateError(
      `Vehicle state request failed: ${response.status} ${response.statusText}`,
      response.status,
    );
  }

  return response.json() as Promise<VehicleStateResponse>;
}
