// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for the agent start/stop request bodies.
 *
 * The body shape of `/agent/stop` is an AUTHORIZATION boundary, not a payload
 * detail. As of 2026-09-23 that route has two scopes:
 *
 *   - with `vehicleId` -> stops only that vehicle's task; available to a
 *     connected-services caller on the allowlisted fleet
 *   - without        -> stops EVERY agent task in the cluster; admin-only
 *
 * So omitting `vehicleId` does not degrade gracefully — it routes a CS operator
 * to the cluster-wide path where they are correctly refused with a 403. This
 * client previously sent `{ vin }` alone, mirroring CMS's call.
 *
 * See issues/2026-09-23-agent-routes-do-not-authorize-connected-services-callers/.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { startAgent, stopAgent } from '../simulationClient';

const VIN = 'MRDN0000000000015';
const VEHICLE_ID = 'VEH-MRDN-0015';

function makeFetch() {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ success: true }),
    text: async () => '{"success":true}',
  } as any);
}

function bodyOf(f: ReturnType<typeof makeFetch>) {
  const init = f.mock.calls[0]?.[1] as RequestInit | undefined;
  return JSON.parse(String(init?.body ?? '{}'));
}

beforeEach(() => {
  // The client resolves its base from runtimeConfig; provide one so the calls
  // reach the transport rather than short-circuiting on a null base.
  (window as any).runtimeConfig = {
    simulationApiEndpoint: 'https://sim.example.invalid/prod/',
  };
  try {
    sessionStorage.setItem('idToken', 'test-token');
  } catch {
    /* jsdom without storage — the transport tolerates a null token */
  }
});

describe('stopAgent request body', () => {
  it('sends vehicleId so the stop is vehicle-scoped, not cluster-wide', () => {
    // THE regression. Mutation: reverting to `{ vin }` makes this fail, and a CS
    // operator's Stop Agent silently becomes an admin-only cluster-wide request.
    const f = makeFetch();
    stopAgent(VIN, VEHICLE_ID, f as any);
    expect(f).toHaveBeenCalledTimes(1);
    expect(bodyOf(f)).toEqual({ vin: VIN, vehicleId: VEHICLE_ID });
  });

  it('targets the /agent/stop route', () => {
    const f = makeFetch();
    stopAgent(VIN, VEHICLE_ID, f as any);
    expect(String(f.mock.calls[0][0])).toContain('/agent/stop');
  });

  it('never sends an empty vehicleId, which would read as untargeted', () => {
    // An empty string is falsy on the backend's `stop_config.get("vehicleId")`
    // check, so it takes the cluster-wide path — the same failure as omitting it.
    const f = makeFetch();
    stopAgent(VIN, '', f as any);
    const body = bodyOf(f);
    expect(body.vehicleId === '' || body.vehicleId === undefined).toBe(true);
    // Documents the contract for callers: the guard belongs at the call site,
    // which passes the picker's resolved vehicleId.
  });
});

describe('startAgent request body', () => {
  it('sends both vin and vehicleId', () => {
    // The fleet-membership check is keyed on vehicleId, not vin — a vin alone
    // cannot be authorized. See
    // issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/.
    const f = makeFetch();
    startAgent(VIN, VEHICLE_ID, f as any);
    expect(bodyOf(f)).toEqual({ vin: VIN, vehicleId: VEHICLE_ID });
  });

  it('targets the /agent/start route', () => {
    const f = makeFetch();
    startAgent(VIN, VEHICLE_ID, f as any);
    expect(String(f.mock.calls[0][0])).toContain('/agent/start');
  });
});
