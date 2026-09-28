// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { withIdentityPoolId } from '@aws/amazon-location-utilities-auth-helper';

export interface MapConfig {
  mapStyle: string | object;
  authOptions?: object;
}

/**
 * Build an Amazon Location auth helper via a two-tier strategy:
 *
 *  1. **Authenticated** — when the logged-in user has an id-token in storage,
 *     call `withIdentityPoolId(id, { logins })` so the SDK gets credentials
 *     scoped to `CognitoAuthenticatedRole` (which carries `geo:GetMap*`).
 *  2. **Guest** — when no id-token is available (demo sessions, races before
 *     auth hydrates, apps deliberately configured for anonymous map preview),
 *     call `withIdentityPoolId(id)` for `CognitoUnauthenticatedRole`
 *     credentials. This only succeeds on deploys that opt in to unauth map
 *     auth via the CDK context flag `cms.allow_unauth_map_auth=true`
 *     (default `false`, per H1 2026-06-11 security posture; explicitly ON
 *     for prod per issue `2026-06-19-cms-phase1-prod-map-auth-flag`
 *     resolution — "maps always-on").
 *  3. **OSM fallback** — when both tiers throw (staging with the default
 *     posture; misconfigured pools; network failure) — the caller returns
 *     an OpenStreetMap tile source, preserving a rendered map.
 *
 * ## Caching (added 2026-07-16 for issue
 * `2026-07-16-cms-map-auth-cognito-identity-rate-limit`)
 *
 * The auth helper Promise is cached at module scope, keyed on
 * `(identityPoolId, region, idToken)`. All concurrent map components that
 * mount on the same page share the same resolved helper, and repeated
 * renders of the same component re-use the same Promise. This eliminates
 * the render-loop amplification that had `getMapConfiguration()` firing
 * once per re-render × N mounted map surfaces × parent re-renders driven
 * by WebSocket vehicle updates → per-pool 200 RPS Cognito Identity Pool
 * quota exhaustion.
 *
 * The cache invalidates when `idToken` changes (login / logout / token
 * refresh). It does NOT time out on its own — the underlying
 * `withIdentityPoolId` client refreshes STS credentials automatically.
 *
 * Test-only invalidation: `__resetMapAuthCacheForTests()`.
 */
async function _mapAuthHelperUncached(
  idToken: string | null,
  identityPoolId: string,
  userPoolId: string,
  region: string,
) {
  // Tier 1: authenticated. Preferred whenever an id-token is available —
  // works on every deploy regardless of the `allow_unauth_map_auth` context
  // flag.
  if (idToken) {
    try {
      return await withIdentityPoolId(identityPoolId, {
        logins: { [`cognito-idp.${region}.amazonaws.com/${userPoolId}`]: idToken },
        clientConfig: { region },
      });
    } catch (e) {
      // Fall through to guest. The most common cause is an id-token whose
      // audience does not match the identity pool's configured provider,
      // which is a config drift the guest tier cannot recover from either
      // — but on prod with unauth ON, the guest attempt still yields a
      // working map, and that's a strictly better UX than dropping to OSM.
      console.debug('[mapConfig] authenticated auth-helper failed, trying guest:', e);
    }
  }

  // Tier 2: guest. Only usable when the identity pool has
  // `AllowUnauthenticatedIdentities: true` AND the unauth role has
  // `geo:GetMap*` on the target map. Both conditions are true on prod
  // (opt-in via `cms.allow_unauth_map_auth=true`); both are false on
  // staging / default deploys, where this call throws
  // `NotAuthorizedException` and we drop to OSM.
  try {
    return await withIdentityPoolId(identityPoolId, {
      clientConfig: { region },
    });
  } catch (e) {
    console.debug(
      '[mapConfig] guest auth-helper unavailable (unauth disabled or misconfigured); using OSM fallback:',
      e,
    );
    return null;
  }
}

// Module-level cache. `null` in a cached Promise IS a valid cached result
// (means "both tiers failed, fall to OSM") — we intentionally cache the
// negative result so we don't retry-storm the identity pool. A page reload
// or an id-token change is the recovery path.
type CachedAuthHelper = Awaited<ReturnType<typeof _mapAuthHelperUncached>>;
let cachedAuthHelperKey: string | null = null;
let cachedAuthHelperPromise: Promise<CachedAuthHelper> | null = null;

async function _mapAuthHelper(): Promise<CachedAuthHelper> {
  const rc = (window as any).runtimeConfig;
  const enabled = rc?.locationServices?.enabled;
  const identityPoolId = rc?.awsCredentials?.identityPoolId;
  const userPoolId = rc?.awsCredentials?.userPoolId;
  const region = rc?.locationServices?.region || rc?.awsRegion || 'us-east-1';
  const idToken =
    typeof window !== 'undefined'
      ? localStorage.getItem('idToken') || sessionStorage.getItem('idToken')
      : null;

  if (!enabled || !identityPoolId || !userPoolId) {
    return null;
  }

  const key = `${identityPoolId}::${region}::${idToken ?? '__guest__'}`;

  // Cache hit — return the in-flight OR resolved Promise. Concurrent
  // callers await the same Promise; repeated calls after resolution
  // return the resolved value.
  if (cachedAuthHelperKey === key && cachedAuthHelperPromise) {
    return cachedAuthHelperPromise;
  }

  // Cache miss / invalidation on idToken change — kick off a new lookup.
  cachedAuthHelperKey = key;
  const inflight = _mapAuthHelperUncached(idToken, identityPoolId, userPoolId, region).catch(
    (e) => {
      // Uncaught throw from the helper (not a caught tier-1 failure —
      // those are already handled with a fall-through). Drop the cache
      // so a subsequent caller can retry cleanly rather than being
      // pinned to a rejected Promise.
      if (cachedAuthHelperKey === key) {
        cachedAuthHelperKey = null;
        cachedAuthHelperPromise = null;
      }
      throw e;
    },
  );
  cachedAuthHelperPromise = inflight;
  return inflight;
}

/**
 * Test-only: reset the module-level auth-helper cache. Not exported from
 * the barrel; imported directly in tests via `./mapConfig`.
 */
export function __resetMapAuthCacheForTests(): void {
  cachedAuthHelperKey = null;
  cachedAuthHelperPromise = null;
}

export const getMapAuthenticationOptions = async () => {
  try {
    const authHelper = await _mapAuthHelper();
    if (authHelper) {
      return authHelper.getMapAuthenticationOptions();
    }
  } catch (error) {
    console.error('[mapConfig] Amazon Location auth failed:', error);
  }
  return {};
};

export const getMapConfiguration = async (): Promise<MapConfig> => {
  const rc = (window as any).runtimeConfig;
  const region = rc?.locationServices?.region || rc?.awsRegion || 'us-east-1';

  try {
    const authHelper = await _mapAuthHelper();
    if (authHelper) {
      // Map name resolves from locationServices.mapName, falling back to the
      // legacy mapAuth.mapName for older runtimeConfigs.
      const mapName =
        rc.locationServices.mapName ||
        rc.mapAuth?.mapName ||
        'cms-prod-ui-vehicle-map';

      return {
        // v1 Maps API URL — both the authenticated role (`geo:GetMap*` on
        // `map/*` resources) and the prod unauth role (`geo:GetMap*` on the
        // specific `cms-prod-ui-vehicle-map-here` resource) support v1.
        // The v2 URL pattern would 403 for the authenticated role and fall
        // through to OSM.
        mapStyle: `https://maps.geo.${region}.amazonaws.com/maps/v0/maps/${mapName}/style-descriptor`,
        authOptions: authHelper.getMapAuthenticationOptions(),
      };
    }
  } catch (error) {
    console.error('[mapConfig] Amazon Location Services auth failed — falling back to OSM:', error);
  }

  // Fallback to OpenStreetMap (both auth tiers failed, or location services off).
  return {
    mapStyle: {
      version: 8,
      sources: {
        'osm-tiles': {
          type: 'raster' as const,
          tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
          tileSize: 256,
          attribution: '© OpenStreetMap contributors',
        },
      },
      layers: [
        {
          id: 'osm-tiles',
          type: 'raster' as const,
          source: 'osm-tiles',
        },
      ],
    },
  };
};
