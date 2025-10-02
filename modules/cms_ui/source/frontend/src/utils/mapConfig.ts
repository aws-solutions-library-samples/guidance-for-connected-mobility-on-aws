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
      
      return {
        mapStyle: `https://maps.geo.${region}.amazonaws.com/v2/styles/Standard/descriptor`,
        authOptions: authHelper.getMapAuthenticationOptions()
      };
    } catch (error) {
      console.error('Failed to setup Amazon Location Services:', error);
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
