// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

// RealFleetManagementClient.makeRequest merges default, auth and caller
// headers. It used to object-spread `options.headers`, which is a HeadersInit:
// spreading a Headers instance yields {} and spreading [key, value] pairs yields
// index keys, so caller headers in either form were silently lost. tsc flagged
// it as TS2769 once the jest-dom types fix put the file back under checking.
//
// Assertions read the sent headers through `new Headers(...)`, so they hold
// whatever shape the client passes to fetch and fail only on the property.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { RealFleetManagementClient } from '../real-fleet-client';

const fetchMock = vi.fn();

function sentHeaders(): Headers {
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const init = fetchMock.mock.calls[0][1] as RequestInit;
  return new Headers(init.headers);
}

type Req = (endpoint: string, options?: RequestInit) => Promise<unknown>;
const makeRequest = (client: RealFleetManagementClient): Req =>
  (client as unknown as { makeRequest: Req }).makeRequest.bind(client);

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('RealFleetManagementClient.makeRequest headers', () => {
  it('sends JSON defaults and the bearer token when an idToken is stored', async () => {
    localStorage.setItem('idToken', 'tok-123');
    await makeRequest(new RealFleetManagementClient({}))('/api/v1/fleets');
    const h = sentHeaders();
    expect(h.get('content-type')).toBe('application/json');
    expect(h.get('accept')).toBe('application/json');
    expect(h.get('authorization')).toBe('Bearer tok-123');
  });

  it('sends no Authorization header when no token is stored', async () => {
    await makeRequest(new RealFleetManagementClient({}))('/api/v1/fleets');
    expect(sentHeaders().has('authorization')).toBe(false);
  });

  it('keeps caller headers passed as a Headers instance, and they override defaults', async () => {
    await makeRequest(new RealFleetManagementClient({}))('/api/v1/fleets', {
      headers: new Headers({ 'Content-Type': 'text/plain', 'X-Trace': 'a' }),
    });
    const h = sentHeaders();
    expect(h.get('content-type')).toBe('text/plain');
    expect(h.get('x-trace')).toBe('a');
  });

  it('keeps caller headers passed as [key, value] pairs', async () => {
    await makeRequest(new RealFleetManagementClient({}))('/api/v1/fleets', {
      headers: [['X-Trace', 'b']],
    });
    expect(sentHeaders().get('x-trace')).toBe('b');
  });

  it('lets a caller Authorization header override the stored token', async () => {
    localStorage.setItem('idToken', 'tok-123');
    await makeRequest(new RealFleetManagementClient({}))('/api/v1/fleets', {
      headers: { Authorization: 'Bearer caller' },
    });
    expect(sentHeaders().get('authorization')).toBe('Bearer caller');
  });
});
