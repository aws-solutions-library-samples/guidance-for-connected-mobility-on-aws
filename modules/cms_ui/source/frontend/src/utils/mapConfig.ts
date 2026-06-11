// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { withIdentityPoolId } from '@aws/amazon-location-utilities-auth-helper';

export interface MapConfig {
  mapStyle: string | object;
  authOptions?: object;
}

export const getMapAuthenticationOptions = async () => {
  const runtimeConfig = (window as any).runtimeConfig;
  const locationServicesEnabled = runtimeConfig?.locationServices?.enabled;
  
  if (locationServicesEnabled && runtimeConfig?.awsCredentials?.identityPoolId) {
    try {
      const authHelper = await withIdentityPoolId(runtimeConfig.awsCredentials.identityPoolId);
      return authHelper.getMapAuthenticationOptions();
    } catch (error) {
      console.error('Failed to setup Amazon Location Services auth:', error);
    }
  }
  
  return {};
};

export const getMapConfiguration = async (): Promise<MapConfig> => {
  const runtimeConfig = (window as any).runtimeConfig;
  const locationServicesEnabled = runtimeConfig?.locationServices?.enabled;
  
  if (locationServicesEnabled && runtimeConfig?.awsCredentials?.identityPoolId) {
    try {
      // Create auth helper for Amazon Location Services
      const authHelper = await withIdentityPoolId(runtimeConfig.awsCredentials.identityPoolId);
      const region = runtimeConfig.locationServices.region || 'us-east-1';
      // Map name resolves from locationServices.mapName, falling back to the
      // legacy mapAuth.mapName for older runtimeConfigs.
      const mapName =
        runtimeConfig.locationServices.mapName ||
        runtimeConfig.mapAuth?.mapName ||
        'cms-prod-ui-vehicle-map';
      
      return {
        // Use the v1 Maps API URL — the Cognito auth role has `geo:GetMap*`
        // perms (v1) but not `geo-maps:*` (v2). The v2 URL pattern
        // `/v2/styles/Standard/descriptor` would 403 and fall through to OSM.
        mapStyle: `https://maps.geo.${region}.amazonaws.com/maps/v0/maps/${mapName}/style-descriptor`,
        authOptions: authHelper.getMapAuthenticationOptions()
      };
    } catch (error) {
      // Log prominently so browser devtools shows the real error
      console.error('[mapConfig] Amazon Location Services auth failed — falling back to OSM:', error);
      // Fall through to OpenStreetMap
    }
  }
  
  // Fallback to OpenStreetMap
  return {
    mapStyle: {
      version: 8,
      sources: {
        'osm-tiles': {
          type: 'raster' as const,
          tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
          tileSize: 256,
          attribution: '© OpenStreetMap contributors'
        }
      },
      layers: [
        {
          id: 'osm-tiles',
          type: 'raster' as const,
          source: 'osm-tiles'
        }
      ]
    }
  };
};
