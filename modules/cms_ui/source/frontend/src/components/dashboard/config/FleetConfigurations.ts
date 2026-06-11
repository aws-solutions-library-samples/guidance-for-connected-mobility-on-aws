// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { getApiEndpoint } from '../../../config/api';

/**
 * Fleet Configuration System for CMS UI
 * 
 * This file contains configurations for different fleet deployments
 * that integrate with the existing CMS UI structure.
 */

export interface FleetConfiguration {
  // Basic Information
  id: string;
  name: string;
  description: string;
  
  // Dashboard Configuration
  dashboard: {
    title: string;
    subtitle: string;
    branding?: {
      logo?: string;
      primaryColor?: string;
      secondaryColor?: string;
    };
  };
  
  // Geographic Configuration
  region: {
    name: string;
    center: [number, number]; // [lat, lon]
    zoom: number;
    timezone: string;
    fleet_boundaries?: {
      [fleetId: string]: {
        center: [number, number];
        radius: number;
        color?: string;
        name?: string;
      };
    };
  };
  
  // Feature Configuration
  features: {
    showMap: boolean;
    showAutoRegistration: boolean;
    showAnalytics: boolean;
    showRealTimeMetrics: boolean;
    showFleetBoundaries: boolean;
    enableAlerts: boolean;
    enableReporting: boolean;
  };
  
  // Data Configuration
  data: {
    refreshInterval: number; // milliseconds
    maxVehiclesOnMap: number;
    maxEventsInFeed: number;
    vehicleIdentifierPattern?: RegExp; // Pattern to identify vehicles for this deployment
    fleetIdentifierPattern?: RegExp;   // Pattern to identify fleets for this deployment
  };
  
  // Vehicle Configuration
  vehicles: {
    defaultIcon: string;
    statusIcons: {
      [status: string]: string;
    };
    colorScheme: string[]; // Colors for different fleets
  };
  
  // API Configuration
  api: {
    baseUrl: string;
    endpoints?: {
      vehicles?: string;
      dashboard?: string;
      events?: string;
    };
  };
}

// Seattle Delivery Fleet Configuration
export const SeattleFleetConfig: FleetConfiguration = {
  id: 'seattle-delivery',
  name: 'Seattle Delivery Operations',
  description: 'Fleet management for Seattle delivery vehicles with auto-registration',
  
  dashboard: {
    title: 'Seattle Delivery Fleet Dashboard',
    subtitle: 'Real-time monitoring of auto-registered delivery vehicles',
    branding: {
      primaryColor: '#1E40AF',
      secondaryColor: '#10B981'
    }
  },
  
  region: {
    name: 'Seattle Metropolitan Area',
    center: [47.6062, -122.3321],
    zoom: 11,
    timezone: 'America/Los_Angeles',
    fleet_boundaries: {
      'SD0001': { center: [47.6205, -122.3493], radius: 2000, color: '#FF6B6B', name: 'Downtown/Belltown' },
      'SD0002': { center: [47.6587, -122.3125], radius: 2500, color: '#4ECDC4', name: 'Fremont/Wallingford' },
      'SD0003': { center: [47.5445, -122.3045], radius: 2200, color: '#45B7D1', name: 'Georgetown/SODO' },
    }
  },
  
  features: {
    showMap: true,
    showAutoRegistration: true,
    showAnalytics: true,
    showRealTimeMetrics: true,
    showFleetBoundaries: true,
    enableAlerts: true,
    enableReporting: true
  },
  
  data: {
    refreshInterval: 30000,
    maxVehiclesOnMap: 100,
    maxEventsInFeed: 200,
    vehicleIdentifierPattern: /^1FLEET\d+/, // Matches Seattle delivery vehicle VINs
    fleetIdentifierPattern: /^SD\d+/        // Matches Seattle delivery fleet IDs
  },
  
  vehicles: {
    defaultIcon: '🚚',
    statusIcons: {
      'CONNECTED': '🚚',
      'OFFLINE': '⚫',
      'MAINTENANCE': '🔧',
      'DELIVERING': '📦'
    },
    colorScheme: ['#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FFEAA7']
  },
  
  api: {
    baseUrl: 'getApiEndpoint()'
  }
};

// Generic Fleet Configuration (Default)
export const GenericFleetConfig: FleetConfiguration = {
  id: 'generic',
  name: 'Generic Fleet Operations',
  description: 'General purpose fleet management dashboard',
  
  dashboard: {
    title: 'Fleet Management Dashboard',
    subtitle: 'Real-time fleet monitoring and management',
    branding: {
      primaryColor: '#3B82F6',
      secondaryColor: '#10B981'
    }
  },
  
  region: {
    name: 'Fleet Region',
    center: [39.8283, -98.5795], // Geographic center of US
    zoom: 4,
    timezone: 'America/Chicago'
  },
  
  features: {
    showMap: true,
    showAutoRegistration: true,
    showAnalytics: true,
    showRealTimeMetrics: true,
    showFleetBoundaries: false,
    enableAlerts: true,
    enableReporting: true
  },
  
  data: {
    refreshInterval: 60000,
    maxVehiclesOnMap: 200,
    maxEventsInFeed: 100
  },
  
  vehicles: {
    defaultIcon: '🚗',
    statusIcons: {
      'CONNECTED': '🚗',
      'OFFLINE': '⚫',
      'MAINTENANCE': '🔧'
    },
    colorScheme: ['#3B82F6', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6']
  },
  
  api: {
    baseUrl: getApiEndpoint()
  }
};

// Configuration Registry
export const FleetConfigurations = {
  'seattle-delivery': SeattleFleetConfig,
  'generic': GenericFleetConfig
};

// Configuration Helper Functions
export class FleetConfigurationManager {
  private static instance: FleetConfigurationManager;
  private currentConfig: FleetConfiguration;

  private constructor() {
    this.currentConfig = GenericFleetConfig;
  }

  public static getInstance(): FleetConfigurationManager {
    if (!FleetConfigurationManager.instance) {
      FleetConfigurationManager.instance = new FleetConfigurationManager();
    }
    return FleetConfigurationManager.instance;
  }

  /**
   * Set the active configuration
   */
  public setConfiguration(configId: string): void {
    const config = FleetConfigurations[configId as keyof typeof FleetConfigurations];
    if (config) {
      this.currentConfig = config;
    } else {
      console.warn(`Configuration '${configId}' not found, using generic configuration`);
      this.currentConfig = GenericFleetConfig;
    }
  }

  /**
   * Get the current configuration
   */
  public getConfiguration(): FleetConfiguration {
    return this.currentConfig;
  }

  /**
   * Auto-detect configuration based on data patterns
   */
  public autoDetectConfiguration(vehicles: any[]): FleetConfiguration {
    if (vehicles.length === 0) {
      return GenericFleetConfig;
    }

    // Check for Seattle delivery patterns
    const seattleVehicles = vehicles.filter(v => 
      v.vin?.match(SeattleFleetConfig.data.vehicleIdentifierPattern) ||
      v.fleet_info?.telemetry_fleet_id?.match(SeattleFleetConfig.data.fleetIdentifierPattern)
    );

    if (seattleVehicles.length > vehicles.length * 0.5) {
      return SeattleFleetConfig;
    }

    // Default to generic
    return GenericFleetConfig;
  }

  /**
   * Filter vehicles based on current configuration
   */
  public filterVehiclesForConfiguration(vehicles: any[]): any[] {
    const config = this.getConfiguration();
    
    if (!config.data.vehicleIdentifierPattern && !config.data.fleetIdentifierPattern) {
      return vehicles; // No filtering for generic config
    }

    return vehicles.filter(vehicle => {
      const vinMatch = !config.data.vehicleIdentifierPattern || 
        vehicle.vin?.match(config.data.vehicleIdentifierPattern);
      
      const fleetMatch = !config.data.fleetIdentifierPattern || 
        vehicle.fleet_info?.telemetry_fleet_id?.match(config.data.fleetIdentifierPattern);
      
      return vinMatch || fleetMatch;
    });
  }

  /**
   * Get available configurations
   */
  public getAvailableConfigurations(): { id: string; name: string; description: string }[] {
    return Object.values(FleetConfigurations).map(config => ({
      id: config.id,
      name: config.name,
      description: config.description
    }));
  }
}

export default FleetConfigurationManager;
