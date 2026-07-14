// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { withIdentityPoolId } from '@aws/amazon-location-utilities-auth-helper';

export interface MapConfig {
  mapStyle: string | object;
  authOptions?: object;
}

/**
 * Build an Amazon Location auth helper using the **authenticated** Cognito
 * identity (the logged-in user's id-token).
 *
 * H1 (2026-06-11) disabled *unauthenticated* Identity-Pool access, so the guest
 * `withIdentityPoolId(id)` flow returns 400 NotAuthorizedException. The
 * authenticated role (`CognitoAuthenticatedRole`) carries `geo:GetMap*`, so
 * passing the user's id-token via `logins` yields working Location Services
 * credentials without re-enabling anonymous access. Returns null (→ OSM
 * fallback) when location services are off or no id-token is present.
 */
async function _authenticatedAuthHelper() {
  const rc = (window as any).runtimeConfig;
  const enabled = rc?.locationServices?.enabled;
  const identityPoolId = rc?.awsCredentials?.identityPoolId;
  const userPoolId = rc?.awsCredentials?.userPoolId;
  const region = rc?.locationServices?.region || rc?.awsRegion || 'us-east-1';
  const idToken =
    typeof window !== 'undefined'
      ? localStorage.getItem('idToken') || sessionStorage.getItem('idToken')
      : null;

  if (!enabled || !identityPoolId || !userPoolId || !idToken) {
    return null;
  }
  return withIdentityPoolId(identityPoolId, {
    logins: { [`cognito-idp.${region}.amazonaws.com/${userPoolId}`]: idToken },
    clientConfig: { region },
  });
}

export const getMapAuthenticationOptions = async () => {
  try {
    const authHelper = await _authenticatedAuthHelper();
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
    const authHelper = await _authenticatedAuthHelper();
    if (authHelper) {
      // Map name resolves from locationServices.mapName, falling back to the
      // legacy mapAuth.mapName for older runtimeConfigs.
      const mapName =
        rc.locationServices.mapName ||
        rc.mapAuth?.mapName ||
        'cms-prod-ui-vehicle-map';

      return {
        // v1 Maps API URL — the Cognito role has `geo:GetMap*` (v1) but not
        // `geo-maps:*` (v2). The v2 URL pattern would 403 and fall through to OSM.
        mapStyle: `https://maps.geo.${region}.amazonaws.com/maps/v0/maps/${mapName}/style-descriptor`,
        authOptions: authHelper.getMapAuthenticationOptions(),
      };
    }
  } catch (error) {
    console.error('[mapConfig] Amazon Location Services auth failed — falling back to OSM:', error);
  }

  // Fallback to OpenStreetMap (no id-token, location services off, or auth error).
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
