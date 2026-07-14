// Simple types to replace fleet-management-client interfaces

export type VehicleSource = 'cms' | 'oem1';

// Phase 3 additions — data source discriminator for fleets
// Renamed from 'onboard-fwe' | 'cloud-oem1' to operator-semantic values.
// Spec: 2026-06-09-cms-data-source-model-refactor (dual-read transition window active; Phase D cleanup is a follow-on spec).
export type FleetDataSource = 'vehicle-telemetry' | 'cloud-telemetry';

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

  // Phase 3 — data source discriminator; undefined on legacy rows treated as 'vehicle-telemetry'.
  // Widened to FleetDataSource | string to accept old DDB strings ('onboard-fwe', 'cloud-oem1')
  // during dual-read transition window (spec: 2026-06-09-cms-data-source-model-refactor).
  data_source?: FleetDataSource | string;
  transform_manifest_id?: string;
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
  oem_source?: VehicleSource | string;

  // Phase 3 — OEM1 enrollment & status fields (all optional; undefined for legacy/CMS-native rows)
  oem1_active_sku?: string;
  oem1_request_id?: number;
  oem1_enrollment_status?: OEM1EnrollmentStatus | string;
  oem1_fcs_code?: number;
  oem1_status_message?: string;
  oem1_readiness_summary?: OEM1ReadinessSummary | string;
  oem1_status_refreshed_at?: string; // ISO8601
  subscription_service_activation_date?: string; // ISO8601
  assigned_driver_id?: string;
  enrollment_pending?: boolean;
  oem1_unenroll_pending?: boolean;
}

// Phase 3 additions — OEM1 enrollment status (exact strings written by Lambdas)
export type OEM1EnrollmentStatus =
  | 'IN_PROGRESS'
  | 'COMPLETED'
  | 'FAILED'
  | 'UN_ENROLL_IN_PROGRESS'
  | 'UNENROLLED'
  | 'UNKNOWN';

// Phase 3 additions — OEM1 readiness summary
export type OEM1ReadinessSummary =
  | 'READY'
  | 'CCS_OFF'
  | 'TRANSPORT_MODE'
  | 'NOT_RECENTLY_KEYED_ON'
  | 'UNKNOWN';

// Phase 3 additions — render-friendly OEM1 status snapshot
export interface OEM1VehicleStatusSnapshot {
  enrollmentStatus: OEM1EnrollmentStatus;
  fcsCode?: number;
  message?: string;
  readiness?: OEM1ReadinessSummary;
  refreshedAt?: string;
  activationDate?: string;
}

// Phase 3 → refactor — dual-read constants for transition window.
// Both old strings ('cloud-oem1', 'onboard-fwe') and new strings ('cloud-telemetry', 'vehicle-telemetry')
// are accepted. Spec: 2026-06-09-cms-data-source-model-refactor.
const _CLOUD_VALUES = new Set(['cloud-telemetry', 'cloud-oem1']);
const _VEHICLE_VALUES = new Set(['vehicle-telemetry', 'onboard-fwe']);

// Fleet data source helper — returns new enum value via dual-read logic; defaults to 'vehicle-telemetry' for legacy rows.
export const getFleetDataSource = (
  f: Pick<FleetItem, 'data_source'>,
): FleetDataSource => (_CLOUD_VALUES.has(f.data_source ?? '') ? 'cloud-telemetry' : 'vehicle-telemetry');

// Returns true when fleet is configured for cloud-fed telemetry (accepts both old and new enum strings).
export const isCloudTelemetryFleet = (f: Pick<FleetItem, 'data_source'>): boolean =>
  _CLOUD_VALUES.has(f.data_source ?? '');

/** @deprecated Use isCloudTelemetryFleet. Kept for one cycle; removed in Phase D follow-on spec (2026-06-09-cms-data-source-model-refactor). */
export const isCloudOEM1Fleet = isCloudTelemetryFleet;

// Derive vehicle source from fleet's data_source; 'oem1' for cloud-fed fleets, 'cms' for vehicle-telemetry fleets.
export const deriveVehicleSourceFromFleet = (
  f: Pick<FleetItem, 'data_source'>,
): VehicleSource => (isCloudTelemetryFleet(f) ? 'oem1' : 'cms');

// Suppress unused-variable warning on _VEHICLE_VALUES (defined for symmetry; used in backend helper mirror).
void _VEHICLE_VALUES;

// Phase 3 — derive a render-friendly OEM1 status snapshot from a vehicle row
export const getOEM1Status = (v: VehicleItem): OEM1VehicleStatusSnapshot => ({
  enrollmentStatus: (v.oem1_enrollment_status as OEM1EnrollmentStatus) ?? 'UNKNOWN',
  fcsCode: v.oem1_fcs_code,
  message: v.oem1_status_message,
  readiness: v.oem1_readiness_summary as OEM1ReadinessSummary | undefined,
  refreshedAt: v.oem1_status_refreshed_at,
  activationDate: v.subscription_service_activation_date,
});

export const isOEM1Vehicle = (v: Pick<VehicleItem, 'oem_source'>): boolean => v.oem_source === 'oem1';

export const getVehicleSource = (v: Pick<VehicleItem, 'oem_source'>): VehicleSource => (v.oem_source === 'oem1' ? 'oem1' : 'cms');

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
