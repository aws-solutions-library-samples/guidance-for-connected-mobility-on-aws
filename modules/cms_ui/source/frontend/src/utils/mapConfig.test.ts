// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for the two-tier map auth flow in `mapConfig.ts`.
 *
 * Regression guard for the 2026-07-16 fix (spec
 * `.kiro/specs/2026-07-16-cms-demo-mode-maps-osm-fallback/`).
 *
 * Contract under test:
 *  - When `idToken` is present, prefer the authenticated auth helper.
 *  - When `idToken` is absent OR the authenticated helper throws, attempt
 *    the guest auth helper (works on prod with `allow_unauth_map_auth`,
 *    fails on staging with the default OFF posture).
 *  - When both tiers fail, return the OpenStreetMap fallback configuration.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

// Mock the amazon-location-utilities-auth-helper module before importing
// mapConfig.ts (top-of-file import ordering matters for vi.mock hoisting).
vi.mock('@aws/amazon-location-utilities-auth-helper', () => ({
  withIdentityPoolId: vi.fn(),
}));

import { withIdentityPoolId } from '@aws/amazon-location-utilities-auth-helper';
import { getMapConfiguration, __resetMapAuthCacheForTests } from './mapConfig';

const mockWithIdentityPoolId = withIdentityPoolId as unknown as ReturnType<typeof vi.fn>;

const AUTH_OPTIONS = { auth: 'authenticated-marker' };
const GUEST_OPTIONS = { auth: 'guest-marker' };

const makeAuthHelper = (opts: object) => ({
  getMapAuthenticationOptions: () => opts,
});

const setRuntimeConfig = (overrides: Record<string, unknown> = {}) => {
  (globalThis as any).window = (globalThis as any).window || {};
  (window as any).runtimeConfig = {
    awsRegion: 'us-east-1',
    locationServices: {
      enabled: true,
      region: 'us-east-1',
      mapName: 'cms-prod-ui-vehicle-map-here',
    },
    awsCredentials: {
      region: 'us-east-1',
      identityPoolId: 'us-east-1:test-pool',
      userPoolId: 'us-east-1_testpool',
      userPoolWebClientId: 'test-client',
    },
    ...overrides,
  };
};

const setIdToken = (tok: string | null, where: 'session' | 'local' = 'session') => {
  sessionStorage.removeItem('idToken');
  localStorage.removeItem('idToken');
  if (tok !== null) {
    (where === 'session' ? sessionStorage : localStorage).setItem('idToken', tok);
  }
};

beforeEach(() => {
  mockWithIdentityPoolId.mockReset();
  sessionStorage.clear();
  localStorage.clear();
  setRuntimeConfig();
  __resetMapAuthCacheForTests();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('mapConfig.getMapConfiguration — two-tier auth', () => {
  it('(1) id-token present → uses authenticated auth helper', async () => {
    setIdToken('fake-jwt.header.body.sig');
    mockWithIdentityPoolId.mockImplementation(async (id: string, opts?: any) => {
      // Assert this call carries the `logins` map (authenticated tier).
      expect(id).toBe('us-east-1:test-pool');
      expect(opts?.logins).toBeDefined();
      expect(opts.logins['cognito-idp.us-east-1.amazonaws.com/us-east-1_testpool']).toBe(
        'fake-jwt.header.body.sig',
      );
      return makeAuthHelper(AUTH_OPTIONS);
    });

    const cfg = await getMapConfiguration();
    expect(cfg.mapStyle).toContain('maps.geo.us-east-1.amazonaws.com');
    expect(cfg.mapStyle).toContain('cms-prod-ui-vehicle-map-here');
    expect(cfg.authOptions).toEqual(AUTH_OPTIONS);
    // Only the authenticated tier should have fired.
    expect(mockWithIdentityPoolId).toHaveBeenCalledTimes(1);
  });

  it('(2) id-token absent + guest helper succeeds → uses guest auth helper', async () => {
    setIdToken(null);
    mockWithIdentityPoolId.mockImplementation(async (_id: string, opts?: any) => {
      // Assert this call is the guest call (no `logins`).
      expect(opts?.logins).toBeUndefined();
      return makeAuthHelper(GUEST_OPTIONS);
    });

    const cfg = await getMapConfiguration();
    expect(cfg.mapStyle).toContain('maps.geo.us-east-1.amazonaws.com');
    expect(cfg.authOptions).toEqual(GUEST_OPTIONS);
    expect(mockWithIdentityPoolId).toHaveBeenCalledTimes(1);
  });

  it('(3) auth helper throws + guest helper succeeds → falls through to guest', async () => {
    setIdToken('fake-jwt.header.body.sig');
    // First call (authenticated) throws; second call (guest) succeeds.
    mockWithIdentityPoolId
      .mockImplementationOnce(async () => {
        throw new Error('NotAuthorizedException: token audience mismatch');
      })
      .mockImplementationOnce(async () => makeAuthHelper(GUEST_OPTIONS));

    const cfg = await getMapConfiguration();
    expect(cfg.mapStyle).toContain('maps.geo.us-east-1.amazonaws.com');
    expect(cfg.authOptions).toEqual(GUEST_OPTIONS);
    expect(mockWithIdentityPoolId).toHaveBeenCalledTimes(2);
    // Second call MUST NOT carry `logins`.
    const guestCall = mockWithIdentityPoolId.mock.calls[1];
    expect(guestCall[1]?.logins).toBeUndefined();
  });

  it('(4) both auth tiers throw → OSM fallback style returned', async () => {
    setIdToken('fake-jwt.header.body.sig');
    mockWithIdentityPoolId.mockImplementation(async () => {
      throw new Error('NotAuthorizedException: unauth disabled');
    });

    const cfg = await getMapConfiguration();
    const style = cfg.mapStyle as any;
    expect(typeof style).toBe('object');
    expect(style.version).toBe(8);
    expect(style.sources['osm-tiles'].tiles[0]).toBe(
      'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    );
    expect(cfg.authOptions).toBeUndefined();
    expect(mockWithIdentityPoolId).toHaveBeenCalledTimes(2);
  });

  it('(5) locationServices.enabled=false → OSM fallback without invoking auth helper', async () => {
    setIdToken('fake-jwt.header.body.sig');
    setRuntimeConfig({
      locationServices: { enabled: false, mapName: 'cms-prod-ui-vehicle-map-here' },
    });

    const cfg = await getMapConfiguration();
    const style = cfg.mapStyle as any;
    expect(typeof style).toBe('object');
    expect(style.sources['osm-tiles']).toBeDefined();
    expect(mockWithIdentityPoolId).not.toHaveBeenCalled();
  });

  it('(6) id-token in localStorage (rememberMe path) is honored', async () => {
    setIdToken('remember-me-token', 'local');
    mockWithIdentityPoolId.mockImplementation(async (_id, opts) => {
      expect(opts?.logins?.['cognito-idp.us-east-1.amazonaws.com/us-east-1_testpool']).toBe(
        'remember-me-token',
      );
      return makeAuthHelper(AUTH_OPTIONS);
    });

    const cfg = await getMapConfiguration();
    expect(cfg.authOptions).toEqual(AUTH_OPTIONS);
  });

  it('(7) missing identityPoolId → OSM fallback, no auth helper call', async () => {
    setIdToken('any-token');
    setRuntimeConfig({
      awsCredentials: { region: 'us-east-1', identityPoolId: '', userPoolId: 'us-east-1_testpool' },
    });

    const cfg = await getMapConfiguration();
    const style = cfg.mapStyle as any;
    expect(style.sources['osm-tiles']).toBeDefined();
    expect(mockWithIdentityPoolId).not.toHaveBeenCalled();
  });
});

describe('mapConfig.getMapConfiguration — module-level auth-helper cache (2026-07-16 rate-limit fix)', () => {
  it('(8) N concurrent callers with the same id-token → withIdentityPoolId invoked ONCE', async () => {
    setIdToken('shared-token');
    // Simulate a slow underlying auth-helper resolution so multiple callers
    // fire while the first is in-flight.
    let resolveHelper: (v: any) => void;
    const pending = new Promise<any>((resolve) => { resolveHelper = resolve; });
    mockWithIdentityPoolId.mockImplementationOnce(async () => {
      await pending;
      return makeAuthHelper(AUTH_OPTIONS);
    });

    // 10 concurrent callers (simulating 5 map components × 2 mount cycles).
    const promises = Array.from({ length: 10 }, () => getMapConfiguration());
    // Let the microtask queue settle so all callers hit the cache.
    await Promise.resolve();
    resolveHelper!(undefined);
    const results = await Promise.all(promises);

    expect(mockWithIdentityPoolId).toHaveBeenCalledTimes(1);
    // All callers got the same authenticated result.
    results.forEach((r) => expect(r.authOptions).toEqual(AUTH_OPTIONS));
  });

  it('(9) sequential callers with the same id-token → withIdentityPoolId invoked ONCE across calls', async () => {
    setIdToken('sticky-token');
    mockWithIdentityPoolId.mockImplementation(async () => makeAuthHelper(AUTH_OPTIONS));

    await getMapConfiguration();
    await getMapConfiguration();
    await getMapConfiguration();

    // Post-resolution cache HIT for calls 2 and 3.
    expect(mockWithIdentityPoolId).toHaveBeenCalledTimes(1);
  });

  it('(10) id-token changes → cache invalidates and withIdentityPoolId is re-invoked', async () => {
    setIdToken('token-A');
    mockWithIdentityPoolId.mockImplementation(async () => makeAuthHelper(AUTH_OPTIONS));

    await getMapConfiguration();
    expect(mockWithIdentityPoolId).toHaveBeenCalledTimes(1);

    // Simulate the OAuth callback or a token refresh — new id-token value in
    // storage. Cache key changes → next call re-invokes the auth helper.
    setIdToken('token-B');
    await getMapConfiguration();
    expect(mockWithIdentityPoolId).toHaveBeenCalledTimes(2);
    // The second call carries the NEW token as `logins`.
    const secondCallOpts = mockWithIdentityPoolId.mock.calls[1][1];
    expect(secondCallOpts?.logins?.['cognito-idp.us-east-1.amazonaws.com/us-east-1_testpool']).toBe(
      'token-B',
    );
  });

  it('(11) guest → authenticated transition (login) invalidates cache', async () => {
    setIdToken(null);
    mockWithIdentityPoolId
      .mockImplementationOnce(async () => makeAuthHelper(GUEST_OPTIONS))  // first: guest
      .mockImplementationOnce(async () => makeAuthHelper(AUTH_OPTIONS));  // second: authenticated

    const cfg1 = await getMapConfiguration();
    expect(cfg1.authOptions).toEqual(GUEST_OPTIONS);

    // User logs in — id-token now present. Cache key changes.
    setIdToken('post-login-token');
    const cfg2 = await getMapConfiguration();
    expect(cfg2.authOptions).toEqual(AUTH_OPTIONS);
    expect(mockWithIdentityPoolId).toHaveBeenCalledTimes(2);
    // Verify the second call is the AUTHENTICATED tier.
    expect(mockWithIdentityPoolId.mock.calls[1][1]?.logins).toBeDefined();
  });

  it('(12) OSM-fallback (both tiers null) is cached — no retry storm within a session', async () => {
    setIdToken('token-that-triggers-both-failures');
    mockWithIdentityPoolId.mockImplementation(async () => {
      throw new Error('NotAuthorizedException');
    });

    // First call: authenticated fails → guest fails → OSM. That's 2
    // withIdentityPoolId invocations from tier-1 + tier-2.
    const cfg1 = await getMapConfiguration();
    expect((cfg1.mapStyle as any).sources['osm-tiles']).toBeDefined();
    expect(mockWithIdentityPoolId).toHaveBeenCalledTimes(2);

    // Subsequent calls with the same token: cache hit on the null result;
    // NO further Cognito calls.
    const cfg2 = await getMapConfiguration();
    const cfg3 = await getMapConfiguration();
    expect((cfg2.mapStyle as any).sources['osm-tiles']).toBeDefined();
    expect((cfg3.mapStyle as any).sources['osm-tiles']).toBeDefined();
    expect(mockWithIdentityPoolId).toHaveBeenCalledTimes(2);
  });
});
