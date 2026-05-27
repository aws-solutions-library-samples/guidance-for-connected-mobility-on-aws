// Simple types to replace fleet-management-client interfaces
export interface FleetItem {
  id?: string;
  name?: string;
  fleetId?: string;
  totalVehicles?: number;
  vehicleCount?: number; // Add vehicleCount from API
  connectedVehicles?: number;
  activeVehicles?: number; // Add activeVehicles count
  operationalCity?: string;
  status?: string;
  description?: string;
  numTotalVehicles?: number;
  numConnectedVehicles?: number;
  numActiveVehicles?: number; // Add mapped active vehicles count
  numTotalCampaigns?: number;
  numActiveCampaigns?: number;
  createdTime?: string;
  lastModifiedTime?: string;

  // Engineering metadata (Acme Motors Product Digital Thread demo).
  // - `tenantType`: who owns the fleet's vehicles. Drives engineering vs
  //   operational view branching on detail pages.
  // - `fleetType`: production/validation classification used by engineering surfaces.
  // - `attributes.isEngineeringFleet`: marker flag for cross-cutting filters.
  // These fields are persisted by deployment/scripts/seed_engineering_fleets.py
  // on the engineering fleet records. Operational fleets simply have them
  // unset (undefined), which means "operational fleet" in the UI.
  tenantType?: 'internal' | 'external';
  fleetType?: string;
  attributes?: { [key: string]: any; isEngineeringFleet?: boolean };
}

export interface CampaignItem {
  name?: string;
  arn?: string;
  status?: string;
  creationTime?: string;
  lastModificationTime?: string;
  [key: string]: any;
}

export enum CampaignStatus {
  CREATING = "CREATING",
  WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL",
  RUNNING = "RUNNING",
  SUSPENDED = "SUSPENDED"
}

export enum VehicleStatus {
  CREATED = "CREATED",
  READY = "READY",
  HEALTHY = "HEALTHY",
  SUSPEND = "SUSPEND",
  DELETING = "DELETING",
  ACTIVE = "ACTIVE",
  INACTIVE = "INACTIVE"
}

export enum CampaignTargetType {
  FLEET = "FLEET",
  VEHICLE = "VEHICLE"
}

export interface VehicleItem {
  id?: string;
  vehicleId?: string;
  name?: string;
  vin?: string;
  make?: string;
  model?: string;
  year?: number;
  licensePlate?: string;
  status?: string;
  fleetId?: string;
  odometer?: string;
  connectionStatus?: string;
  activityStatus?: string;
  lastConnected?: string | null;
  lastDisconnected?: string | null;
  color?: string;
  fuelType?: string;
  vehicleType?: string;
  createdAt?: string;
  updatedAt?: string;
  attributes?: {
    make?: string;
    model?: string;
    year?: number;
    vin?: string;
    [key: string]: any;
  };

  // Engineering metadata (Acme Motors Product Digital Thread demo).
  // Persisted by deployment/scripts/seed_engineering_fleets.py.
  tenantType?: 'internal' | 'external';
  manufacturingBatchId?: string;
  supplierId?: string;
  regionId?: string;
  assemblyPlantId?: string;
  assemblyDate?: string;
  batteryCellLot?: string;
  isAffectedCohort?: boolean;
  ecuConfigId?: string;
  vehicleEnvironment?: 'production' | 'test' | 'validation';
  telemetryTier?: 'standard' | 'instrumented';
}

// Utility function to calculate consistent vehicle status across the UI
export const calculateVehicleStatus = (vehicle: VehicleItem): 'connected' | 'active' | 'inactive' | 'maintenance' => {
  // Currently connected to IoT Core
  if (vehicle.connectionStatus === 'connected' || vehicle.connectionStatus === 'CONNECTED') {
    return 'connected';
  }
  
  // Check if vehicle was active recently (last 5 minutes = likely still connected)
  if (vehicle.lastConnected) {
    const lastConnectedDate = new Date(typeof vehicle.lastConnected === 'number' 
      ? vehicle.lastConnected 
      : vehicle.lastConnected);
    const fiveMinutesAgo = new Date();
    fiveMinutesAgo.setMinutes(fiveMinutesAgo.getMinutes() - 5);
    
    if (lastConnectedDate > fiveMinutesAgo) {
      return 'connected';
    }
    
    const thirtyDaysAgo = new Date();
    thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);
    
    if (lastConnectedDate > thirtyDaysAgo) {
      return 'active';
    }
  }
  
  // Fall back to DDB status field
  const s = (vehicle as any).status || (vehicle as any).activityStatus || '';
  if (s === 'active' || s === 'ACTIVE') {
    return 'active';
  }
  
  return 'inactive';
};

// Utility function to get status indicator props for consistent UI display
export const getVehicleStatusIndicator = (vehicle: VehicleItem) => {
  const status = calculateVehicleStatus(vehicle);
  
  switch (status) {
    case 'maintenance':
      return { type: 'warning' as const, label: 'Maintenance' };
    case 'connected':
      return { type: 'success' as const, label: 'Connected' };
    case 'active':
      return { type: 'success' as const, label: 'Active' };
    case 'inactive':
      return { type: 'stopped' as const, label: 'Inactive' };
    default:
      return { type: 'info' as const, label: 'Unknown' };
  }
};
