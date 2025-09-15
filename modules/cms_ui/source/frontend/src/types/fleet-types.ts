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
}

// Utility function to calculate consistent vehicle status across the UI
export const calculateVehicleStatus = (vehicle: VehicleItem): 'connected' | 'active' | 'inactive' | 'maintenance' => {
  // Check for maintenance status first (if you have maintenance logic)
  // if (vehicle.maintenanceRequired) return 'maintenance';
  
  // Currently connected to IoT Core
  if (vehicle.connectionStatus === 'connected') {
    return 'connected';
  }
  
  // Check if vehicle was active in the last 30 days
  if (vehicle.lastConnected) {
    const lastConnectedDate = new Date(vehicle.lastConnected);
    const thirtyDaysAgo = new Date();
    thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);
    
    if (lastConnectedDate > thirtyDaysAgo) {
      return 'active';
    }
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
