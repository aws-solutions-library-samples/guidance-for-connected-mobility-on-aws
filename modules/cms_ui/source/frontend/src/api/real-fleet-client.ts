// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { getRuntimeConfig, getApiEndpoint } from '../config/api';
import {
  FleetManagementClient,
  FleetManagementClientConfig,
  FleetItem,
  VehicleItem,
  CreateFleetCommand,
  ListFleetsCommand,
  GetFleetCommand,
  EditFleetCommand,
  UpdateFleetCommand,
  DeleteFleetCommand,
  CreateVehicleCommand,
  ListVehiclesCommand,
  GetVehicleCommand,
  UpdateVehicleCommand,
  DeleteVehicleCommand,
  CreateVehicleEntry,
  VehicleStatus,
  ListSafetyEventsCommand,
  ListMaintenanceEventsCommand,
  ListTripsCommand,
  CreateDriverCommand,
  ListDriversCommand,
  GetDriverCommand,
  UpdateDriverCommand,
  DeleteDriverCommand
} from "./fleet-management-client";

// Real API base URL
// Use runtime configuration for API endpoint
const getApiBaseUrl = () => {
  if (typeof window !== 'undefined' && window.runtimeConfig?.apiEndpoint) {
    // Remove trailing slash to prevent double slashes
    return window.runtimeConfig.apiEndpoint.replace(/\/$/, '');
  }
  // Use the centralized API endpoint
  const fallback = import.meta.env.VITE_API_ENDPOINT || getApiEndpoint();
  return fallback.replace(/\/$/, '');
};

const API_BASE_URL = getApiBaseUrl();

export class RealFleetManagementClient implements FleetManagementClient {
  private config: FleetManagementClientConfig;

  constructor(config: FleetManagementClientConfig) {
    this.config = config;
  }

  private async makeRequest(endpoint: string, options: RequestInit = {}): Promise<any> {
    // Ensure endpoint starts with / and construct clean URL
    const cleanEndpoint = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
    const url = `${API_BASE_URL}${cleanEndpoint}`;
    
    console.log('🔗 Making request to URL:', url);
    
    const defaultHeaders = {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    };

    // Get auth token from localStorage (prefer idToken for Cognito authorizer)
    const authToken = localStorage.getItem('idToken') || sessionStorage.getItem('idToken') || localStorage.getItem('authToken');
    const authHeaders = authToken ? { Authorization: `Bearer ${authToken}` } : {};

    const response = await fetch(url, {
      ...options,
      headers: {
        ...defaultHeaders,
        ...authHeaders,
        ...options.headers,
      },
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`API Error ${response.status}: ${errorText}`);
    }

    return response.json();
  }

  // Fleet Management Methods
  async send(command: CreateFleetCommand): Promise<any>;
  async send(command: ListFleetsCommand): Promise<any>;
  async send(command: GetFleetCommand): Promise<any>;
  async send(command: EditFleetCommand): Promise<any>;
  async send(command: UpdateFleetCommand): Promise<any>;
  async send(command: DeleteFleetCommand): Promise<any>;
  async send(command: CreateVehicleCommand): Promise<any>;
  async send(command: ListVehiclesCommand): Promise<any>;
  async send(command: GetVehicleCommand): Promise<any>;
  async send(command: UpdateVehicleCommand): Promise<any>;
  async send(command: DeleteVehicleCommand): Promise<any>;
  async send(command: ListSafetyEventsCommand): Promise<any>;
  async send(command: ListMaintenanceEventsCommand): Promise<any>;
  async send(command: ListTripsCommand): Promise<any>;
  async send(command: CreateDriverCommand): Promise<any>;
  async send(command: ListDriversCommand): Promise<any>;
  async send(command: GetDriverCommand): Promise<any>;
  async send(command: UpdateDriverCommand): Promise<any>;
  async send(command: DeleteDriverCommand): Promise<any>;
  async send(command: any): Promise<any> {
    try {
      switch (command.constructor.name) {
        case 'CreateFleetCommand':
          return this.createFleet(command.input);
        
        case 'ListFleetsCommand':
          return this.listFleets(command.input);
        
        case 'GetFleetCommand':
          return this.getFleet(command.input);
        
        case 'EditFleetCommand':
          return this.updateFleet(command.input);
        
        case 'UpdateFleetCommand':
          return this.updateFleet(command.input);
        
        case 'DeleteFleetCommand':
          return this.deleteFleet(command.input);
        
        case 'CreateVehicleCommand':
          return this.createVehicle(command.input);
        
        case 'ListVehiclesCommand':
          return this.listVehicles(command.input);
        
        case 'GetVehicleCommand':
          return this.getVehicle(command.input);
        
        case 'UpdateVehicleCommand':
          return this.updateVehicle(command.input);
        
        case 'DeleteVehicleCommand':
          return this.deleteVehicle(command.input);
        
        case 'ListFleetsForVehicleCommand':
          return this.listFleetsForVehicle(command.input);
        
        case 'ListCampaignsForTargetCommand':
          return this.listCampaignsForTarget(command.input);
        
        case 'ListSafetyEventsCommand':
          return this.listSafetyEvents(command.input);
        
        case 'ListMaintenanceEventsCommand':
          return this.listMaintenanceEvents(command.input);
        
        case 'ListTripsCommand':
          return this.listTrips(command.input);
        
        case 'CreateDriverCommand':
          return this.createDriver(command.input);
        
        case 'ListDriversCommand':
          return this.listDrivers(command.input);
        
        case 'GetDriverCommand':
          return this.getDriver(command.input);
        
        case 'UpdateDriverCommand':
          return this.updateDriver(command.input);
        
        case 'DeleteDriverCommand':
          return this.deleteDriver(command.input);
        
        default:
          throw new Error(`Unknown command: ${command.constructor.name}`);
      }
    } catch (error) {
      console.error('API Error:', error);
      throw error;
    }
  }

  // Helper function to map vehicle status from API to UI format
  private mapVehicleStatus(apiStatus: string): VehicleStatus {
    switch (apiStatus?.toUpperCase()) {
      case 'CONNECTED':
      case 'ONLINE':
        return VehicleStatus.ACTIVE;
      case 'OFFLINE':
      case 'DISCONNECTED':
      case 'MAINTENANCE':
      default:
        return VehicleStatus.INACTIVE;
    }
  }

  // Helper function to map vehicle data from API to UI format
  private mapVehicleData(vehicle: any): any {
    return {
      name: vehicle.vin, // UI expects 'name' but we use VIN as the identifier
      vehicleId: vehicle.vehicleId, // Add vehicleId for routing
      vin: vehicle.vin, // Add vin at top level for easy access
      status: this.mapVehicleStatus(vehicle.status),
      licensePlate: vehicle.licensePlate, // Add direct mapping for license plate
      attributes: {
        make: vehicle.make,
        model: vehicle.model,
        year: vehicle.year,
        licensePlate: vehicle.licensePlate || vehicle.license_plate, // Support both formats
        vin: vehicle.vin,
        color: vehicle.color,
        fuelType: vehicle.fuel_type || vehicle.fuelType,
        transmission: vehicle.transmission
      },
      tags: vehicle.tags || {},
      fleetInfo: vehicle.fleet_info // Additional info for display
    };
  }

  private async createFleet(input: any): Promise<any> {
    const response = await this.makeRequest('/fleets', {
      method: 'POST',
      body: JSON.stringify({
        name: input.name,
        description: input.description,
        tags: input.tags || {},
        configuration: input.configuration || {}
      }),
    });

    return {
      fleet: {
        id: response.fleet_id,
        name: response.fleet.name,
        description: response.fleet.description,
        numTotalVehicles: response.fleet.num_total_vehicles,
        numConnectedVehicles: response.fleet.num_connected_vehicles,
        numTotalCampaigns: response.fleet.num_total_campaigns,
        numActiveCampaigns: response.fleet.num_active_campaigns,
        createdTime: response.fleet.created_time,
        lastModifiedTime: response.fleet.last_modified_time,
        status: response.fleet.status,
        tags: response.fleet.tags,
        configuration: response.fleet.configuration
      }
    };
  }

  private async listFleets(input: any = {}): Promise<any> {
    console.log('🚢 RealFleetManagementClient.listFleets called with input:', input);
    console.log('🌐 API Base URL:', API_BASE_URL);
    
    const queryParams = new URLSearchParams();
    
    if (input.limit) queryParams.append('limit', input.limit.toString());
    if (input.status) queryParams.append('status', input.status);
    if (input.lastKey) queryParams.append('last_key', input.lastKey);

    const queryString = queryParams.toString();
    const endpoint = `/api/v1/fleets${queryString ? `?${queryString}` : ''}`;
    
    console.log('📡 Making optimized single request to endpoint:', endpoint);
    console.log('🔗 Full URL:', `${API_BASE_URL}${endpoint}`);
    
    const response = await this.makeRequest(endpoint);
    
    console.log('✅ Received optimized response:', response);

    const mappedFleets = response.fleets.map((fleet: any) => ({
      id: fleet.id, // Use the correct field name
      name: fleet.name,
      description: fleet.description,
      numTotalVehicles: fleet.numTotalVehicles, // Use camelCase field names
      numConnectedVehicles: fleet.numConnectedVehicles,
      numTotalCampaigns: parseInt(fleet.numTotalCampaigns) || 0,
      numActiveCampaigns: parseInt(fleet.numActiveCampaigns) || 0,
      createdTime: fleet.createdAt,
      lastModifiedTime: fleet.updatedAt,
      status: fleet.status,
      tags: fleet.tags || [],
      configuration: fleet.attributes || {}
    }));

    // Return optimized response format with backend-provided totals
    return {
      fleets: mappedFleets,
      items: mappedFleets, // Standardized field
      total: response.total || response.count || mappedFleets.length,
      count: mappedFleets.length,
      hasMore: response.hasMore || false,
      lastKey: response.last_key,
      message: response.message || `Found ${mappedFleets.length} fleets`,
      performance: response.performance || {} // Include performance metrics
    };
  }

  private async getFleet(input: any): Promise<any> {
    // Use input.id (from GetFleetCommand) instead of input.fleetId
    const fleetId = input.id || input.fleetId;
    const response = await this.makeRequest(`/api/v1/fleets/${fleetId}`);

    return {
      fleet: {
        id: response.fleet_id,
        name: response.name,
        description: response.description,
        numTotalVehicles: response.num_total_vehicles,
        numConnectedVehicles: response.num_connected_vehicles,
        numTotalCampaigns: parseInt(response.num_total_campaigns) || 0,
        numActiveCampaigns: parseInt(response.num_active_campaigns) || 0,
        createdTime: response.created_time,
        lastModifiedTime: response.last_modified_time,
        status: response.status,
        tags: response.tags,
        configuration: response.configuration
      }
    };
  }

  private async updateFleet(input: any): Promise<any> {
    const fleetId = input.id || input.fleetId;
    const fleetData = input.entry || input;
    
    const response = await this.makeRequest(`/api/v1/fleets/${fleetId}`, {
      method: 'PUT',
      body: JSON.stringify({
        entry: {
          name: fleetData.name,
          description: fleetData.description,
          status: fleetData.status
        }
      }),
    });

    return {
      fleet: {
        id: response.fleet.fleetId,
        name: response.fleet.name,
        description: response.fleet.description,
        numTotalVehicles: response.fleet.vehicleCount || 0,
        numConnectedVehicles: response.fleet.connectedVehicles || 0,
        numTotalCampaigns: 0,
        numActiveCampaigns: 0,
        createdTime: response.fleet.createdAt,
        lastModifiedTime: response.fleet.updatedAt,
        status: response.fleet.status
      }
    };
  }

  private async deleteFleet(input: any): Promise<any> {
    const fleetId = input.id || input.fleetId;
    await this.makeRequest(`/api/v1/fleets/${fleetId}`, {
      method: 'DELETE',
    });

    return { message: 'Fleet deleted successfully' };
  }

  private async createVehicle(input: any): Promise<any> {
    const response = await this.makeRequest('/api/v1/vehicles', {
      method: 'POST',
      body: JSON.stringify({
        vin: input.vin,
        fleet_id: input.fleetId,
        make: input.make,
        model: input.model,
        year: input.year,
        license_plate: input.licensePlate,
        color: input.color,
        fuel_type: input.fuelType,
        transmission: input.transmission,
        tags: input.tags || {}
      }),
    });

    console.log('🚗 Create vehicle API response:', response);

    // The API returns: { message, vehicleId, vin }
    // We need to construct a vehicle object for mapVehicleData
    const vehicleData = {
      vin: response.vin || input.vin,
      vehicleId: response.vehicleId,
      make: input.make,
      model: input.model,
      year: input.year,
      license_plate: input.licensePlate,
      status: 'ACTIVE', // Default status for new vehicles
      tags: input.tags || {}
    };

    return {
      message: response.message,
      vehicleId: response.vehicleId,
      vehicle: this.mapVehicleData(vehicleData)
    };
  }

  private async listVehicles(input: any = {}): Promise<any> {
    console.log('🚗 RealFleetManagementClient.listVehicles called with input:', input);
    console.log('🌐 API Base URL:', API_BASE_URL);
    
    const queryParams = new URLSearchParams();
    
    // Use the provided pagination parameters
    const limit = Math.min(input.limit || 25, 100); // Cap at 100 for performance
    const page = input.page || 1;
    
    queryParams.append('limit', limit.toString());
    queryParams.append('page', page.toString());
    
    // Add optional filters
    if (input.status) queryParams.append('status', input.status);
    if (input.fleetId) queryParams.append('fleet_id', input.fleetId);
    if (input.make) queryParams.append('make', input.make);
    if (input.sortBy) queryParams.append('sort_by', input.sortBy);
    if (input.sortOrder) queryParams.append('sort_order', input.sortOrder);

    const queryString = queryParams.toString();
    const endpoint = `/api/v1/vehicles${queryString ? `?${queryString}` : ''}`;
    
    console.log('📡 Making optimized single request to endpoint:', endpoint);
    console.log('🔗 Full URL:', `${API_BASE_URL}${endpoint}`);
    console.log('📊 Request parameters:', { limit, page, filters: { status: input.status, fleetId: input.fleetId } });
    
    const response = await this.makeRequest(endpoint);
    
    console.log('✅ Received optimized pagination response:', {
      vehicleCount: response.vehicles?.length,
      total: response.total,
      page: response.page,
      totalPages: response.totalPages,
      hasMore: response.hasMore,
      hasPrevious: response.hasPrevious,
      pagination: response.pagination
    });

    // The backend now provides complete pagination info in a single optimized response
    return {
      vehicles: response.vehicles.map((vehicle: any) => this.mapVehicleData(vehicle)),
      items: response.vehicles.map((vehicle: any) => this.mapVehicleData(vehicle)), // Standardized field
      total: response.total || 0,
      count: response.returned || response.vehicles?.length || 0,
      page: response.page || page,
      limit: response.limit || limit,
      totalPages: response.totalPages || 1,
      hasMore: response.hasMore || false,
      hasPrevious: response.hasPrevious || false,
      pagination: response.pagination || {
        currentPage: response.page || page,
        totalPages: response.totalPages || 1,
        pageSize: response.limit || limit,
        totalItems: response.total || 0,
        hasNextPage: response.hasMore || false,
        hasPreviousPage: response.hasPrevious || false,
        startItem: ((response.page || page) - 1) * (response.limit || limit) + 1,
        endItem: Math.min((response.page || page) * (response.limit || limit), response.total || 0)
      },
      message: response.message || `Found ${response.vehicles?.length || 0} vehicles`,
      filters: response.filters || {},
      performance: response.performance || {} // Include performance metrics from backend
    };
  }

  private async getVehicle(input: any): Promise<any> {
    // GetVehicleCommand uses 'name' parameter, which should be the VIN
    const vin = input.name || input.vin;
    const response = await this.makeRequest(`/api/v1/vehicles?vin=${vin}`);

    return {
      vehicle: this.mapVehicleData(response)
    };
  }

  private async updateVehicle(input: any): Promise<any> {
    // Handle both name and vin parameters
    const vin = input.name || input.vin;
    const response = await this.makeRequest(`/api/v1/vehicles/${vin}`, {
      method: 'PUT',
      body: JSON.stringify({
        license_plate: input.licensePlate,
        status: input.status,
        fleet_id: input.fleetId,
        attributes: {
          color: input.color,
          fuel_type: input.fuelType,
          transmission: input.transmission,
          mileage: input.mileage
        },
        tags: input.tags
      }),
    });

    return {
      vehicle: {
        name: response.vehicle.vin,
        status: response.vehicle.status as VehicleStatus,
        attributes: {
          make: response.vehicle.make,
          model: response.vehicle.model,
          year: response.vehicle.year,
          licensePlate: response.vehicle.license_plate,
          vin: response.vehicle.vin,
          color: response.vehicle.attributes?.color,
          fuelType: response.vehicle.attributes?.fuel_type,
          transmission: response.vehicle.attributes?.transmission
        },
        tags: response.vehicle.tags
      }
    };
  }

  private async deleteVehicle(input: any): Promise<any> {
    const vehicleId = input.name || input.vin || input.vehicleId;
    await this.makeRequest(`/api/v1/vehicles/${vehicleId}`, {
      method: 'DELETE',
    });

    return { message: 'Vehicle deleted successfully' };
  }

  private async listFleetsForVehicle(input: any): Promise<any> {
    try {
      // Try to get fleets for the vehicle from the API
      const response = await this.makeRequest(`/api/v1/vehicles/${input.name}/fleets`);
      
      return {
        fleets: response.fleets || []
      };
    } catch (error) {
      console.warn(`No fleets found for vehicle: ${input.name}`, error);
      // Return empty fleets array if the endpoint doesn't exist or vehicle not found
      return {
        fleets: []
      };
    }
  }

  // Standardized method for safety events
  private async listSafetyEvents(input: any = {}): Promise<any> {
    console.log('🚨 RealFleetManagementClient.listSafetyEvents called with input:', input);
    
    const queryParams = new URLSearchParams();
    
    if (input.limit) queryParams.append('limit', input.limit.toString());
    if (input.vehicleId) queryParams.append('vehicle_id', input.vehicleId);
    if (input.eventType) queryParams.append('event_type', input.eventType);
    if (input.severity) queryParams.append('severity', input.severity);
    if (input.startDate) queryParams.append('start_date', input.startDate);
    if (input.endDate) queryParams.append('end_date', input.endDate);
    if (input.lastKey) queryParams.append('last_key', input.lastKey);

    const queryString = queryParams.toString();
    const endpoint = `/api/v1/safety-events${queryString ? `?${queryString}` : ''}`;
    
    console.log('📡 Making optimized single request to endpoint:', endpoint);
    
    const response = await this.makeRequest(endpoint);
    console.log('✅ Received optimized response:', response);

    return {
      safetyEvents: response.safetyEvents || response.safety_events || [],
      items: response.safetyEvents || response.safety_events || [],
      total: response.total || response.count || (response.safetyEvents || response.safety_events || []).length,
      count: (response.safetyEvents || response.safety_events || []).length,
      hasMore: response.hasMore || false,
      lastKey: response.last_key,
      message: response.message || `Found ${(response.safetyEvents || response.safety_events || []).length} safety events`,
      performance: response.performance || {}
    };
  }

  // Standardized method for maintenance events
  private async listMaintenanceEvents(input: any = {}): Promise<any> {
    console.log('🔧 RealFleetManagementClient.listMaintenanceEvents called with input:', input);
    
    const queryParams = new URLSearchParams();
    
    if (input.limit) queryParams.append('limit', input.limit.toString());
    if (input.vehicleId) queryParams.append('vehicle_id', input.vehicleId);
    if (input.eventType) queryParams.append('event_type', input.eventType);
    if (input.status) queryParams.append('status', input.status);
    if (input.startDate) queryParams.append('start_date', input.startDate);
    if (input.endDate) queryParams.append('end_date', input.endDate);
    if (input.lastKey) queryParams.append('last_key', input.lastKey);

    const queryString = queryParams.toString();
    const endpoint = `/api/v1/maintenance-events${queryString ? `?${queryString}` : ''}`;
    
    console.log('📡 Making request to endpoint:', endpoint);
    
    const response = await this.makeRequest(endpoint);
    console.log('✅ Received response:', response);

    // Get total count using standardized approach
    let actualTotal = response.total || response.count || response.maintenanceEvents?.length || 0;
    
    if (response.hasMore) {
      try {
        console.log('🔢 Getting total count for maintenance events...');
        
        try {
          const countResponse = await this.makeRequest(`/api/v1/maintenance-events/count`);
          if (countResponse.total || countResponse.count) {
            actualTotal = countResponse.total || countResponse.count;
            console.log('🎯 Using total from count endpoint:', actualTotal);
          }
        } catch {
          const highLimitParams = new URLSearchParams();
          highLimitParams.append('limit', '10000');
          Object.entries(input).forEach(([key, value]) => {
            if (key !== 'limit' && key !== 'lastKey' && value) {
              const apiKey = key.replace(/([A-Z])/g, '_$1').toLowerCase();
              highLimitParams.append(apiKey, value.toString());
            }
          });
          
          const highLimitResponse = await this.makeRequest(`/api/v1/maintenance-events?${highLimitParams.toString()}`);
          if (highLimitResponse.total > actualTotal) {
            actualTotal = highLimitResponse.total;
            console.log('🎯 Using total from high limit request:', actualTotal);
          }
        }
      } catch (error) {
        console.log('⚠️ Error getting total count for maintenance events:', error.message);
      }
    }

    return {
      maintenanceEvents: response.maintenanceEvents || response.maintenance_events || [],
      items: response.maintenanceEvents || response.maintenance_events || [],
      total: actualTotal,
      count: (response.maintenanceEvents || response.maintenance_events || []).length,
      hasMore: response.hasMore || false,
      lastKey: response.last_key,
      message: response.message || `Found ${(response.maintenanceEvents || response.maintenance_events || []).length} maintenance events`
    };
  }

  // Standardized method for trips
  private async listTrips(input: any = {}): Promise<any> {
    console.log('🛣️ RealFleetManagementClient.listTrips called with input:', input);
    
    const queryParams = new URLSearchParams();
    
    if (input.limit) queryParams.append('limit', input.limit.toString());
    if (input.vehicleId) queryParams.append('vehicle_id', input.vehicleId);
    if (input.startDate) queryParams.append('start_date', input.startDate);
    if (input.endDate) queryParams.append('end_date', input.endDate);
    if (input.lastKey) queryParams.append('last_key', input.lastKey);

    const queryString = queryParams.toString();
    const endpoint = `/api/v1/trips${queryString ? `?${queryString}` : ''}`;
    
    console.log('📡 Making request to endpoint:', endpoint);
    
    const response = await this.makeRequest(endpoint);
    console.log('✅ Received response:', response);

    // Get total count using standardized approach
    let actualTotal = response.total || response.count || response.trips?.length || 0;
    
    if (response.hasMore) {
      try {
        console.log('🔢 Getting total count for trips...');
        
        try {
          const countResponse = await this.makeRequest(`/api/v1/trips/count`);
          if (countResponse.total || countResponse.count) {
            actualTotal = countResponse.total || countResponse.count;
            console.log('🎯 Using total from count endpoint:', actualTotal);
          }
        } catch {
          const highLimitParams = new URLSearchParams();
          highLimitParams.append('limit', '10000');
          Object.entries(input).forEach(([key, value]) => {
            if (key !== 'limit' && key !== 'lastKey' && value) {
              const apiKey = key.replace(/([A-Z])/g, '_$1').toLowerCase();
              highLimitParams.append(apiKey, value.toString());
            }
          });
          
          const highLimitResponse = await this.makeRequest(`/api/v1/trips?${highLimitParams.toString()}`);
          if (highLimitResponse.total > actualTotal) {
            actualTotal = highLimitResponse.total;
            console.log('🎯 Using total from high limit request:', actualTotal);
          }
        }
      } catch (error) {
        console.log('⚠️ Error getting total count for trips:', error.message);
      }
    }

    return {
      trips: response.trips || [],
      items: response.trips || [],
      total: actualTotal,
      count: (response.trips || []).length,
      hasMore: response.hasMore || false,
      lastKey: response.last_key,
      message: response.message || `Found ${(response.trips || []).length} trips`
    };
  }

  private async listCampaignsForTarget(input: any): Promise<any> {
    try {
      // Use the correct endpoint path: /campaign/list/{targetType}/{targetId}
      // This matches the API Gateway resources we just created
      const response = await this.makeRequest(`/campaign/list/${input.targetType}/${input.targetId}`);
      
      return {
        campaigns: response.campaigns || []
      };
    } catch (error: any) {
      // Enhanced error handling for different error types
      if (error.message && error.message.includes('CORS')) {
        console.warn(`CORS error accessing campaigns endpoint. Check API Gateway CORS configuration.`);
      } else if (error.message && error.message.includes('Failed to fetch')) {
        console.warn(`Network error accessing campaigns endpoint. Endpoint may not be deployed yet.`);
      } else if (error.status === 404) {
        console.warn(`Campaigns endpoint not found. Verify API Gateway deployment.`);
      } else if (error.status === 401 || error.status === 403) {
        console.warn(`Authentication error accessing campaigns endpoint.`);
      } else {
        console.warn(`Error accessing campaigns for target: ${input.targetId}`, error);
      }
      
      // Return empty campaigns array for any error to prevent UI crashes
      return {
        campaigns: []
      };
    }
  }

  // Driver CRUD operations
  private async createDriver(input: any): Promise<any> {
    const response = await this.makeRequest('/api/v1/drivers', {
      method: 'POST',
      body: JSON.stringify({
        entry: {
          firstName: input.firstName,
          lastName: input.lastName,
          email: input.email,
          phone: input.phone,
          licenseNumber: input.licenseNumber,
          licenseExpiry: input.licenseExpiry,
          fleetId: input.fleetId
        }
      }),
    });

    return {
      driver: response.driver,
      message: 'Driver created successfully'
    };
  }

  private async listDrivers(input: any = {}): Promise<any> {
    const queryParams = new URLSearchParams();
    
    if (input.limit) queryParams.append('limit', input.limit.toString());
    if (input.page) queryParams.append('page', input.page.toString());
    if (input.fleetId) queryParams.append('fleetId', input.fleetId);

    const queryString = queryParams.toString();
    const endpoint = `/api/v1/drivers${queryString ? `?${queryString}` : ''}`;
    
    const response = await this.makeRequest(endpoint);

    return {
      drivers: response.drivers || [],
      items: response.drivers || [],
      total: response.total || 0,
      count: response.drivers?.length || 0,
      page: response.page || 1,
      limit: response.limit || 25,
      totalPages: response.totalPages || 1,
      hasMore: response.hasNextPage || false,
      hasPrevious: response.hasPrevPage || false
    };
  }

  private async getDriver(input: any): Promise<any> {
    const driverId = input.id || input.driverId;
    const response = await this.makeRequest(`/api/v1/drivers/${driverId}`);

    return {
      driver: response.driver
    };
  }

  private async updateDriver(input: any): Promise<any> {
    const driverId = input.id || input.driverId;
    const driverData = input.entry || input;
    
    const response = await this.makeRequest(`/api/v1/drivers/${driverId}`, {
      method: 'PUT',
      body: JSON.stringify({
        entry: {
          firstName: driverData.firstName,
          lastName: driverData.lastName,
          email: driverData.email,
          phone: driverData.phone,
          licenseNumber: driverData.licenseNumber,
          licenseExpiry: driverData.licenseExpiry,
          fleetId: driverData.fleetId,
          status: driverData.status
        }
      }),
    });

    return {
      driver: response.driver,
      message: 'Driver updated successfully'
    };
  }

  private async deleteDriver(input: any): Promise<any> {
    const driverId = input.id || input.driverId;
    await this.makeRequest(`/api/v1/drivers/${driverId}`, {
      method: 'DELETE',
    });

    return { message: 'Driver deleted successfully' };
  }
}
