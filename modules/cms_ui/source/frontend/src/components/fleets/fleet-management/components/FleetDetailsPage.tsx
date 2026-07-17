// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { getApiEndpoint } from '../../../../utils/api-config';
import { useAuth } from '../../../../auth/useAuth';
import {
  TableEmptyState,
  TableNoMatchState,
} from "@/components/commons/common-components";
import {
  baseTableAriaLabels,
  createTableSortLabelFn,
  getHeaderCounterText,
  getTextFilterCounterText,
} from "@/i18n-strings";
import {
  Box,
  Button,
  ColumnLayout,
  Container,
  Header,
  KeyValuePairs,
  SpaceBetween,
  Spinner,
  StatusIndicator,
  Table,
  Pagination,
  TextFilter,
  CollectionPreferences,
  Link,
  Modal,
  Select,
  FormField,
} from "@cloudscape-design/components";
import { ReactNode, useEffect, useState, useContext, useRef } from "react";
import { getDataProcessingApiEndpoint } from "../../../../config/api";
import { authFetch } from "../../../../utils/authFetch";

// Simple cache to prevent duplicate API calls
const apiCache = new Map<string, { data: any; timestamp: number; loading: boolean }>();
const CACHE_DURATION = 30000; // 30 seconds

const getCacheKey = (endpoint: string, params?: any) => {
  return `${endpoint}${params ? JSON.stringify(params) : ''}`;
};

const isCacheValid = (cacheEntry: any) => {
  return cacheEntry && (Date.now() - cacheEntry.timestamp) < CACHE_DURATION;
};

const clearFleetCache = (fleetId: string) => {
  apiCache.delete(getCacheKey('fleet', fleetId));
  apiCache.delete(getCacheKey('campaigns', fleetId));
  apiCache.delete(getCacheKey('vehicles', fleetId));
  console.log('🗑️ Cleared cache for fleet:', fleetId);
};
import { ApiContext } from "@/api/provider";
import { FleetItem, CampaignItem, CampaignStatus, VehicleItem, VehicleStatus, calculateVehicleStatus, getVehicleStatusIndicator } from "@/types/fleet-types";
import { getRuntimeConfig } from "../../../../config/api";
import { useCollection } from "@cloudscape-design/collection-hooks";
import { useNavigate } from "react-router-dom";
import { UI_ROUTES } from "@/utils/constants";
import { FleetManagementContext } from "../FleetManagementContext";
import { useIsEngineerTenant } from "@/auth/useIsEngineerTenant";
import EngineeringFleetDetailsPage from "@/components/engineering/EngineeringFleetDetailsPage";

export function FleetDetailsPage({
  fleetId: propFleetId,
  onDeleteInit,
}: any) {
  // Get fleetId from props or URL hash as fallback
  const urlFleetId = window.location.hash.substring(1);
  const fleetId = propFleetId || urlFleetId;
  const { getAuthHeaders } = useAuth();
  const { breadcrumbs } = useContext(FleetManagementContext);
  
  console.log('FleetDetailsPage - propFleetId:', propFleetId);
  console.log('FleetDetailsPage - urlFleetId:', urlFleetId);
  console.log('FleetDetailsPage - final fleetId:', fleetId);

  const [fleet, setFleet] = useState<FleetItem>();
  const [fleetCampaigns, setFleetCampaigns] = useState<CampaignItem[]>([]);
  const [campaignsLoading, setCampaignsLoading] = useState<boolean>(true);
  const [isLoadingFleet, setIsLoadingFleet] = useState<boolean>(false);
  const [vehiclesLoading, setVehiclesLoading] = useState<boolean>(false);

  const api = useContext(ApiContext);

  // Early return if fleetId is not provided
  console.log('FleetDetailsPage - Validation check:', {
    fleetId,
    'fleetId type': typeof fleetId,
    'fleetId length': fleetId?.length,
    'is falsy': !fleetId,
    'is undefined string': fleetId === 'undefined',
    'is empty after trim': fleetId?.trim() === ''
  });

  if (!fleetId || fleetId === 'undefined' || fleetId.trim() === '') {
    console.warn('FleetDetailsPage: No valid fleetId provided. PropFleetId:', propFleetId, 'UrlFleetId:', urlFleetId);
    return (
      <Container>
        <Header variant="h1">Fleet Details</Header>
        <Box>
          <StatusIndicator type="error">
            No fleet selected. Please select a fleet from the fleet management page.
          </StatusIndicator>
        </Box>
      </Container>
    );
  }

  console.log('✅ FleetDetailsPage - Validation passed, proceeding with fleet ID:', fleetId);

  const fetchFleet = async (fleetId: string) => {
    console.log('🔥 UPDATED: fetchFleet called with fleetId:', fleetId);
    
    if (!fleetId || fleetId === 'undefined') {
      console.warn('Cannot fetch fleet: fleetId is undefined');
      return;
    }
    
    const cacheKey = getCacheKey('fleet', fleetId);
    const cached = apiCache.get(cacheKey);
    
    // Return cached data if valid
    if (isCacheValid(cached) && cached.data) {
      console.log('✅ Using cached fleet data:', cached.data);
      setFleet(cached.data);
      return;
    }
    
    // Skip if already loading
    if (cached?.loading) {
      console.log('⏳ Fleet already being loaded, waiting...');
      // Set up a retry mechanism for waiting calls
      setTimeout(() => {
        const updatedCache = apiCache.get(cacheKey);
        if (updatedCache?.data && !updatedCache.loading) {
          console.log('✅ Using fleet data from completed request:', updatedCache.data);
          setFleet(updatedCache.data);
        }
      }, 1000);
      return;
    }
    
    // Mark as loading in cache
    apiCache.set(cacheKey, { data: null, timestamp: Date.now(), loading: true });
    setIsLoadingFleet(true);
    
    try {
      console.log('📡 Making API call to fetch fleet...');
      
      // Use direct API call instead of GetFleetCommand
      const apiEndpoint = getRuntimeConfig().apiEndpoint;
      const response = await fetch(`${apiEndpoint}api/v1/fleets/${fleetId}`);
      const apiResponse = await response.json();
      const fleetData = apiResponse.fleet; // Extract fleet from response
      
      console.log('🔧 Fleet API response:', fleetData);
      
      // Check if fleetData exists
      if (!fleetData) {
        throw new Error(`Fleet data not found for fleetId: ${fleetId}`);
      }
      
      // The API response should already have the correct structure
      const mappedFleetData = {
        // Preserve all raw API fields first (engineering metadata: tenantType,
        // fleetType, attributes, fleetId, vehicleCount, etc. — needed by
        // EngineeringFleetDetailsPage). Explicit fields below override.
        ...fleetData,
        id: fleetData.fleetId || fleetId,
        fleetId: fleetData.fleetId || fleetId,
        name: fleetData.name,
        description: fleetData.description,
        status: fleetData.status,
        vehicleCount: fleetData.vehicleCount || 0,
        connectedVehicles: fleetData.connectedVehicles || 0,
        numTotalVehicles: fleetData.vehicleCount || 0,
        numConnectedVehicles: fleetData.connectedVehicles || 0,
        numActiveVehicles: fleetData.activeVehicles || 0,
        numTotalCampaigns: fleetCampaigns.length,
        numActiveCampaigns: fleetCampaigns.filter((c: any) => c.status === CampaignStatus.RUNNING || c.status === 'RUNNING').length,
        createdTime: fleetData.createdAt || '2024-01-15T10:00:00Z',
        lastModifiedTime: fleetData.updatedAt || new Date().toISOString(),
        // Preserve fleetType as-is (the spread already does this; do NOT rename
        // to vehicleType because EngineeringFleetDetailsPage reads fleet.fleetType
        // directly). vehicleType is kept as a separate alias for any operational
        // code that depends on the previous shape.
        vehicleType: fleetData.fleetType,
        autoCreated: fleetData.autoCreated,
        telemetryFleetId: fleetData.telemetryFleetId,
        tags: fleetData.tags || {},
        configuration: fleetData.configuration || {}
      };
      
      console.log('🔧 Mapped fleet data:', mappedFleetData);
      
      // Cache the result
      apiCache.set(cacheKey, { 
        data: mappedFleetData, 
        timestamp: Date.now(), 
        loading: false 
      });
      
      // Cache fleet name in localStorage for breadcrumbs
      if (mappedFleetData.name) {
        localStorage.setItem(`fleet_name_${fleetId}`, mappedFleetData.name);
      }
      
      setFleet(mappedFleetData);
      console.log('✅ Fleet state updated and cached via GetFleetCommand for fleet', fleetId);
    } catch (error) {
      console.error('❌ Error fetching fleet via GetFleetCommand:', error);
      // Remove loading flag from cache on error
      apiCache.delete(cacheKey);
      setFleet(null);
    } finally {
      setIsLoadingFleet(false);
    }
  };

  useEffect(() => {
    console.log('🔄 Fleet loading useEffect triggered with fleetId:', fleetId);
    console.log('🔄 Current fleet state:', fleet);
    console.log('🔄 Is loading fleet:', isLoadingFleet);
    
    if (fleetId && fleetId !== 'undefined') {
      console.log('✅ FleetId is valid, starting fleet fetch...');
      async function getFleet() {
        console.log('🚀 About to call fetchFleet...');
        await fetchFleet(fleetId);
        console.log('✅ fetchFleet completed');
      }
      getFleet();
    } else {
      console.warn('❌ FleetDetailsPage: fleetId is undefined or invalid:', fleetId);
    }
  }, [fleetId]); // Only depend on fleetId

  // Set breadcrumbs when fleet data is available
  useEffect(() => {
    if (fleet?.name) {
      breadcrumbs.setBreadcrumbItems([
        { text: 'Home', href: '/' },
        { text: 'Fleets', href: '/fleets' },
        { text: 'Fleet Detail', href: `#${fleetId}` }
      ]);
    }
  }, [fleet, fleetId, breadcrumbs]);

  const fetchFleetCampaigns = async (fleetId: string) => {
    // Fleet campaigns are now fetched directly by FleetCampaignsTable component
    setCampaignsLoading(false);
  };

  useEffect(() => {
    if (fleetId && fleetId !== 'undefined') {
      async function getCampaigns() {
        await fetchFleetCampaigns(fleetId);
      }
      getCampaigns();
    } else {
      console.warn('Cannot load campaigns: fleetId is undefined');
      setFleetCampaigns([]);
      setCampaignsLoading(false);
    }
  }, [fleetId]);

  const navigate = useNavigate();

  // Engineering tenant branch — placed AFTER all hooks per rules of hooks.
  // When an engineer views an engineering fleet, render the engineering
  // layout instead of the operational one.
  const isEngineerTenant = useIsEngineerTenant(fleet ?? null);
  if (fleet && isEngineerTenant) {
    return <EngineeringFleetDetailsPage fleet={fleet} />;
  }

  return (
          <Container>
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description={`Detailed information for ${fleet?.name || 'Fleet'}`}
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button iconName="edit">Edit</Button>
            <Button onClick={onDeleteInit}>Delete</Button>
          </SpaceBetween>
        }
      >
        {fleet?.name || 'Fleet Details'}
      </Header>
      
      {/* Fleet Summary Tiles */}
      <Container>
        <SpaceBetween size="l">
          <Header variant="h2">Fleet Overview</Header>
          {isLoadingFleet ? (
            <Box textAlign="center" padding="xl">
              <Spinner size="large" />
            </Box>
          ) : fleet ? (
            <ColumnLayout columns={4} variant="text-grid">
              <div>
                <div style={{ fontSize: '24px', fontWeight: 'bold' }}>{fleet.numTotalVehicles || 0}</div>
                <Box variant="awsui-key-label">Total Vehicles</Box>
              </div>
              <div>
                <div style={{ fontSize: '24px', fontWeight: 'bold', color: '#037f0c' }}>{fleet.numConnectedVehicles || 0}</div>
                <Box variant="awsui-key-label">Connected Vehicles</Box>
              </div>
              <div>
                <div style={{ fontSize: '24px', fontWeight: 'bold' }}>{fleetCampaigns.filter((c: any) => c.status === "RUNNING" || c.status === CampaignStatus.RUNNING).length}</div>
                <Box variant="awsui-key-label">Active Campaigns</Box>
              </div>
              <div>
                <div style={{ fontSize: '18px', fontWeight: 'bold' }}>
                  <StatusIndicator type={fleet.status === 'ACTIVE' ? 'success' : 'warning'}>
                    {fleet.status || 'ACTIVE'}
                  </StatusIndicator>
                </div>
                <Box variant="awsui-key-label">Fleet Status</Box>
              </div>
            </ColumnLayout>
          ) : (
            <Box textAlign="center" padding="xl" color="text-body-secondary">
              <Box variant="strong" color="inherit">No fleet data available</Box>
            </Box>
          )}
        </SpaceBetween>
      </Container>
      
      <Container>
        <FleetDetails fleet={fleet ? {
          ...fleet,
          numTotalCampaigns: fleetCampaigns.length,
          numActiveCampaigns: fleetCampaigns.filter((c: any) => c.status === CampaignStatus.RUNNING || c.status === 'RUNNING').length,
        } : fleet} isLoadingFleet={isLoadingFleet} />
      </Container>
      <FleetCampaignsTable
        fleetId={fleetId}
        fleetCampaigns={fleetCampaigns}
        setFleetCampaigns={setFleetCampaigns}
        campaignsLoading={campaignsLoading}
      />
      <FleetVehiclesTable 
        fleetId={fleetId} 
        vehiclesLoading={vehiclesLoading}
        setVehiclesLoading={setVehiclesLoading}
      />
    </SpaceBetween>
    </Container>
  );
}

export function FleetDetails({
  fleet,
  isLoadingFleet,
}: {
  fleet: FleetItem | undefined;
  isLoadingFleet?: boolean;
}): ReactNode {

  if (isLoadingFleet) {
    return <Spinner size="large" />;
  }

  if (!fleet) {
    console.warn('❌ FleetDetails: No fleet data, returning empty');
    return null;
  }
  
  console.log('✅ FleetDetails: Rendering fleet details for:', fleet.name);
  console.log('🔍 FleetDetails field values:', {
    'fleet.id': fleet.id,
    'fleet.id type': typeof fleet.id,
    'fleet.id === undefined': fleet.id === undefined,
    'fleet.id === null': fleet.id === null,
    'fleet.name': fleet.name,
    'fleet.numTotalVehicles': fleet.numTotalVehicles,
    'fleet.numConnectedVehicles': fleet.numConnectedVehicles,
    'fleet.numTotalCampaigns': fleet.numTotalCampaigns,
    'fleet.numActiveCampaigns': fleet.numActiveCampaigns,
    'fleet.createdTime': fleet.createdTime,
    'fleet.lastModifiedTime': fleet.lastModifiedTime
  });
  
  return (
    <KeyValuePairs
      columns={4}
      items={[
        {
          type: "group",
          items: [
            {
              label: "Fleet Name",
              value: fleet.name || "Logistics Fleet Atlanta",
            },
            {
              label: "Fleet ID",
              value: fleet.id,
            },
          ],
        },
        {
          type: "group",
          items: [
            {
              label: "Total Vehicles",
              value: fleet.numTotalVehicles,
            },
            {
              label: "Connected Vehicles",
              value: fleet.numConnectedVehicles,
            },
          ],
        },
        {
          type: "group",
          items: [
            {
              label: "Total Campaigns",
              value: fleet.numTotalCampaigns,
            },
            {
              label: "Active Campaigns",
              value: fleet.numActiveCampaigns,
            },
          ],
        },
        {
          type: "group",
          items: [
            {
              label: "Fleet Type",
              value: fleet.vehicleType || "Mixed",
            },
            {
              label: "Region",
              value: (fleet as any).operationalCity || "Multi-region",
            },
          ],
        },
      ]}
    />
  );
}

const CAMPAIGN_COLUMN_DEFINITIONS = [
  {
    id: "name",
    header: "Name",
    cell: (item: FleetItem) => item.name,
    isRowHeader: true,
  },
  {
    id: "status",
    header: "Status",
    cell: (item: CampaignItem) => {
      const getStatusIndicator = (status: CampaignStatus | undefined) => {
        if (status === CampaignStatus.RUNNING) {
          return (
            <StatusIndicator type="success">
              {CampaignStatus.RUNNING}
            </StatusIndicator>
          );
        } else if (status === CampaignStatus.SUSPENDED) {
          return (
            <StatusIndicator type="stopped">
              {CampaignStatus.SUSPENDED}
            </StatusIndicator>
          );
        } else if (status === CampaignStatus.CREATING) {
          return (
            <StatusIndicator type="in-progress">
              {CampaignStatus.CREATING}
            </StatusIndicator>
          );
        } else if (status === CampaignStatus.WAITING_FOR_APPROVAL) {
          return (
            <StatusIndicator type="in-progress">
              {CampaignStatus.WAITING_FOR_APPROVAL}
            </StatusIndicator>
          );
        }
      };

      return getStatusIndicator(item.status);
    },
    minWidth: 200,
  },
];

export const VEHICLE_COLUMN_DEFINITIONS = (navigate: (path: string) => void) => [
  {
    id: "vehicleVin",
    sortingField: "vin",
    header: "VIN",
    cell: (item: VehicleItem) => (
      <Link onFollow={(e) => { e.preventDefault(); navigate(`${UI_ROUTES.VEHICLE_MANAGEMENT}/${item.id || item.vehicleId}`); }}>
        {item.vin || item.name}
      </Link>
    ),
    isRowHeader: true,
  },
  {
    id: "status",
    sortingField: "status",
    header: "Status",
    sortingComparator: (a: VehicleItem, b: VehicleItem) => {
      const statusOrder = { 'maintenance': 1, 'connected': 2, 'active': 3, 'inactive': 4 };
      return (statusOrder[a.status as keyof typeof statusOrder] || 5) - (statusOrder[b.status as keyof typeof statusOrder] || 5);
    },
    cell: (item: VehicleItem) => {
      const statusIndicator = getVehicleStatusIndicator(item);
      return <StatusIndicator type={statusIndicator.type}>{statusIndicator.label}</StatusIndicator>;
    },
  },
  {
    id: "make",
    sortingField: "make",
    header: "Make",
    cell: (item: VehicleItem) => item.attributes?.make,
    isRowHeader: true,
  },
  {
    id: "model",
    sortingField: "model",
    header: "Model",
    cell: (item: VehicleItem) => item.attributes?.model,
    isRowHeader: true,
  },
  {
    id: "year",
    sortingField: "year",
    header: "Year",
    cell: (item: VehicleItem) => item.attributes?.year,
    isRowHeader: true,
  },
  {
    id: "licensePlate",
    sortingField: "licensePlate",
    header: "License Plate",
    cell: (item: VehicleItem) => item.attributes?.licensePlate,
    isRowHeader: true,
  },
  {
    id: "odometer",
    sortingField: "odometer",
    header: "Odometer",
    cell: (item: VehicleItem) => item.odometer ? `${item.odometer.toLocaleString()} mi` : '-',
    isRowHeader: true,
  },
];

const campaignsSelectionLabels = {
  ...baseTableAriaLabels,
  itemSelectionLabel: (_data: any, row: any) => `select ${row.name}`,
  selectionGroupLabel: "Campaigns selection",
};

const vehicleSelectionLabels = {
  ...baseTableAriaLabels,
  itemSelectionLabel: (_data: any, row: any) => `select ${row.name}`,
  selectionGroupLabel: "Vehicles selection",
};

export function FleetCampaignsTable({
  fleetId,
  fleetCampaigns,
  setFleetCampaigns,
  campaignsLoading,
}: {
  fleetId: string;
  fleetCampaigns: CampaignItem[];
  setFleetCampaigns: any;
  campaignsLoading: boolean;
}) {
  const [selectedItems, setSelectedItems] = useState<any>([]);
  const [assignVisible, setAssignVisible] = useState(false);
  const [templates, setTemplates] = useState<any[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<any>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const enableStopCampaignButton =
    selectedItems.length > 0 && selectedItems[0].status === "RUNNING";
  const enableStartCampaignButton =
    selectedItems.length > 0 && selectedItems[0].status !== "RUNNING";
  const atLeastOneSelected = selectedItems.length > 0;

  const apiEndpoint = getRuntimeConfig().apiEndpoint.replace(/\/$/, '');

  const loadCampaigns = async () => {
    try {
      const res = await fetch(`${apiEndpoint}/api/v1/fleet-campaigns?fleetId=${fleetId}`);
      if (res.ok) {
        const data = await res.json();
        setFleetCampaigns((data.campaigns || []).map((c: any) => ({
          id: c.campaignId,
          name: c.campaignName,
          status: c.status === 'RUNNING' ? CampaignStatus.RUNNING : c.status === 'SUSPENDED' ? CampaignStatus.SUSPENDED : CampaignStatus.STOPPED,
          targetType: 'FLEET',
          targetId: fleetId,
          createdAt: c.createdAt,
          campaignId: c.campaignId,
        })));
      }
    } catch (e) { console.error('Failed to load fleet campaigns:', e); }
  };

  const loadTemplates = async () => {
    try {
      const dpApi = getDataProcessingApiEndpoint();
      if (!dpApi) return;
      const res = await authFetch(`${dpApi}campaigns`);
      if (res.ok) {
        const data = await res.json();
        setTemplates((data.campaigns || []).filter((c: any) => c.targetArn === 'template'));
      }
    } catch { /* ignore */ }
  };

  useEffect(() => { loadCampaigns(); loadTemplates(); }, [fleetId]); // eslint-disable-line

  const assignCampaign = async () => {
    if (!selectedTemplate) return;
    setActionLoading(true);
    try {
      await fetch(`${apiEndpoint}/api/v1/fleet-campaigns/assign`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fleetId, campaignName: selectedTemplate.value })
      });
      setAssignVisible(false);
      setSelectedTemplate(null);
      await loadCampaigns();
    } catch (e) { console.error(e); }
    setActionLoading(false);
  };

  const updateStatus = async (status: string) => {
    if (!selectedItems[0]) return;
    setActionLoading(true);
    try {
      await fetch(`${apiEndpoint}/api/v1/fleet-campaigns/status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaignId: selectedItems[0].campaignId || selectedItems[0].id, status })
      });
      setSelectedItems([]);
      await loadCampaigns();
    } catch (e) { console.error(e); }
    setActionLoading(false);
  };

  return (
    <>
      <Table
        enableKeyboardNavigation={true}
        columnDefinitions={CAMPAIGN_COLUMN_DEFINITIONS}
        loading={campaignsLoading}
        loadingText="Loading campaigns"
        items={fleetCampaigns}
        ariaLabels={campaignsSelectionLabels}
        selectionType="single"
        selectedItems={selectedItems}
        onSelectionChange={(event) => setSelectedItems(event.detail.selectedItems)}
        empty={<Box textAlign="center" color="inherit" padding="l">No campaigns assigned to this fleet</Box>}
        header={
          <Header
            counter={!campaignsLoading && fleetCampaigns ? getHeaderCounterText(fleetCampaigns, selectedItems) : undefined}
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button disabled={!enableStopCampaignButton} loading={actionLoading} onClick={() => updateStatus('SUSPENDED')}>Suspend</Button>
                <Button disabled={!enableStartCampaignButton} loading={actionLoading} onClick={() => updateStatus('RUNNING')}>Resume</Button>
                <Button variant="primary" onClick={() => setAssignVisible(true)}>Assign Campaign</Button>
              </SpaceBetween>
            }
          >
            Fleet Campaigns
          </Header>
        }
      />
      {assignVisible && (
        <Modal visible onDismiss={() => setAssignVisible(false)} size="medium"
          header="Assign Campaign to Fleet"
          footer={<Box float="right"><SpaceBetween direction="horizontal" size="xs">
            <Button onClick={() => setAssignVisible(false)}>Cancel</Button>
            <Button variant="primary" loading={actionLoading} disabled={!selectedTemplate} onClick={assignCampaign}>Assign to All Fleet Vehicles</Button>
          </SpaceBetween></Box>}>
          <SpaceBetween size="m">
            <Box>Select a campaign template to assign to all vehicles in this fleet. Fleet-assigned campaigns cannot be modified at the vehicle level.</Box>
            <FormField label="Campaign Template">
              <Select
                selectedOption={selectedTemplate}
                onChange={({ detail }) => setSelectedTemplate(detail.selectedOption)}
                options={templates.map((t: any) => ({ label: t.campaignName, value: t.campaignName, description: `${t.signalsToCollect?.length || 0} signals` }))}
                placeholder="Select a campaign"
              />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}
    </>
  );
}
//
export function FleetVehiclesTable({ 
  fleetId, 
  vehiclesLoading, 
  setVehiclesLoading 
}: { 
  fleetId: string;
  vehiclesLoading?: boolean;
  setVehiclesLoading?: (loading: boolean) => void;
}) {
  console.log('🚗 FleetVehiclesTable rendered with fleetId:', fleetId);
  const [selectedItems, setSelectedItems] = useState<VehicleItem[]>([]);
  const [fleetVehicles, setFleetVehicles] = useState<VehicleItem[]>([]);
  const [totalVehicleCount, setTotalVehicleCount] = useState<number>(0);
  const [localVehiclesLoading, setLocalVehiclesLoading] = useState<boolean>(true);
  const [debugInfo, setDebugInfo] = useState<string>('Initializing...');
  const [associateVisible, setAssociateVisible] = useState(false);
  const [associateLoading, setAssociateLoading] = useState(false);
  const [availableVehicles, setAvailableVehicles] = useState<any[]>([]);
  const [selectedToAssociate, setSelectedToAssociate] = useState<any[]>([]);
  const atLeastOneSelected = selectedItems.length > 0;

  // Use prop loading state if provided, otherwise use local state
  const isVehiclesLoading = vehiclesLoading !== undefined ? vehiclesLoading : localVehiclesLoading;
  const setIsVehiclesLoading = setVehiclesLoading || setLocalVehiclesLoading;

  const api = useContext(ApiContext);
  const navigate = useNavigate();
  const { getAuthHeaders } = useAuth();

  const fetchFleetVehicles = async (fleetId: string) => {
    setDebugInfo(`Fetching vehicles for ${fleetId}...`);
    if (!fleetId || fleetId === 'undefined') {
      console.warn('Cannot fetch fleet vehicles: fleetId is undefined');
      setFleetVehicles([]);
      setDebugInfo('FleetId undefined');
      return;
    }
    
    const cacheKey = getCacheKey('vehicles', fleetId);
    const cached = apiCache.get(cacheKey);
    
    // Return cached data if valid
    if (isCacheValid(cached) && cached.data) {
      console.log('✅ Using cached vehicles data:', cached.data);
      console.log('🔍 Cached vehicles count:', cached.data.length);
      setFleetVehicles(cached.data);
      setTotalVehicleCount(cached.total || cached.data.length);
      return;
    }
    
    // Skip if already loading
    if (cached?.loading) {
      console.log('⏳ Vehicles already being loaded, waiting...');
      // Set up a retry mechanism for waiting calls
      setTimeout(() => {
        const updatedCache = apiCache.get(cacheKey);
        if (updatedCache?.data && !updatedCache.loading) {
          console.log('✅ Using vehicles data from completed request:', updatedCache.data);
          setFleetVehicles(updatedCache.data);
        }
      }, 1000);
      return;
    }
    
    // Mark as loading in cache
    apiCache.set(cacheKey, { data: null, timestamp: Date.now(), loading: true });
    
    try {
      console.log('🚗 Fetching vehicles for fleet using new API:', fleetId);
      
      // Use the working vehicles API endpoint with fleetId filter
      const apiEndpoint = getRuntimeConfig().apiEndpoint;
      const response = await fetch(`${apiEndpoint}api/v1/vehicles?fleetId=${fleetId}&limit=100&page=1`, {
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        }
      });
      
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      const data = await response.json();
      console.log('🚗 Fleet vehicles API response:', data);
      console.log('🔍 API endpoint called:', `${apiEndpoint}api/v1/vehicles?fleetId=${fleetId}`);
      
      const fleetVehicles = data.vehicles || [];
      const totalCount = data.pagination?.total || data.total || data.totalCount || fleetVehicles.length;
      console.log('🔍 Vehicles count from API for fleet', fleetId, ':', fleetVehicles.length);
      console.log('🔍 Total vehicle count for fleet', fleetId, ':', totalCount);
      console.log('🔍 Pagination info:', data.pagination);
      console.log('🔍 Sample vehicle data:', fleetVehicles[0]);
      console.log('🔍 All vehicle connection statuses:', fleetVehicles.map(v => ({
        vin: v.vin,
        connectionStatus: v.connectionStatus,
        activityStatus: v.activityStatus,
        status: v.status
      })));
      
      // Map the vehicle data to match the expected format
      const vehicles = fleetVehicles.map((vehicle: any) => ({
        name: vehicle.vin || vehicle.vehicleId, // Prefer VIN over vehicleId
        vin: vehicle.vin || vehicle.vehicleId,
        id: vehicle.vehicleId,
        vehicleId: vehicle.vehicleId,
        make: vehicle.make || 'Unknown',
        model: vehicle.model || 'Unknown',
        year: vehicle.year || 'Unknown',
        color: vehicle.color || 'Unknown',
        fuelType: vehicle.fuelType || 'Unknown',
        vehicleType: vehicle.vehicleType || 'Unknown',
        licensePlate: vehicle.licensePlate,
        odometer: vehicle.odometer || 0,
        connectionStatus: vehicle.connectionStatus || 'disconnected',
        activityStatus: vehicle.activityStatus || 'inactive',
        lastConnected: vehicle.lastConnected,
        lastDisconnected: vehicle.lastDisconnected,
        status: calculateVehicleStatus({
          connectionStatus: vehicle.connectionStatus,
          lastConnected: vehicle.lastConnected,
          status: vehicle.status,
          activityStatus: vehicle.activityStatus,
        } as VehicleItem),
        fleetId: vehicle.fleetId,
        createdAt: vehicle.createdAt,
        lastMaintenance: vehicle.lastMaintenance,
        nextMaintenanceDue: vehicle.nextMaintenanceDue,
        insuranceExpiry: vehicle.insuranceExpiry,
        registrationExpiry: vehicle.registrationExpiry,
        driverAssigned: vehicle.driverAssigned,
        attributes: {
          make: vehicle.make || 'Unknown',
          model: vehicle.model || 'Unknown',
          year: vehicle.year || 'Unknown',
          color: vehicle.color || 'Unknown',
          fuelType: vehicle.fuelType || 'Unknown',
          vehicleType: vehicle.vehicleType || 'Unknown',
          licensePlate: vehicle.licensePlate
        }
      }));
      
      console.log('🚗 Final mapped vehicles for fleet', fleetId, ':', vehicles);
      console.log('🚗 Final vehicle count:', vehicles.length);
      
      // Cache the result
      apiCache.set(cacheKey, { 
        data: vehicles, 
        total: totalCount,
        timestamp: Date.now(), 
        loading: false 
      });
      
      setFleetVehicles(vehicles);
      setTotalVehicleCount(totalCount);
      setDebugInfo(`API Success: ${vehicles.length}/${totalCount} vehicles`);
      console.log('✅ Fleet vehicles state updated for fleet', fleetId, 'with', vehicles.length, 'vehicles, total:', totalCount);
      console.log('✅ Vehicles state updated and cached via ListVehiclesInFleetCommand with', vehicles.length, 'vehicles');
    } catch (error) {
      console.error('Error fetching fleet vehicles via ListVehiclesInFleetCommand:', error);
      setDebugInfo(`API Error: ${error}`);
      // Remove loading flag from cache on error
      apiCache.delete(cacheKey);
      setFleetVehicles([]);
    }
  };

  const onDisassociateVehicles = async () => {
    if (!fleetId || fleetId === 'undefined') {
      console.warn('Cannot disassociate vehicles: fleetId is undefined');
      return;
    }
    
    selectedItems.map(async (vehicle) => {
      const apiEndpoint = getRuntimeConfig().apiEndpoint.replace(/\/$/, '');
      const response = await fetch(`${apiEndpoint}api/v1/fleets/${fleetId}/vehicles/${vehicle.name}`, {
        method: 'DELETE'
      });
    });
    setSelectedItems([]);
    setIsVehiclesLoading(true);
    await fetchFleetVehicles(fleetId);
    setIsVehiclesLoading(false);
  };

  const onAssociateVehicles = async () => {
    setAssociateVisible(true);
    setAssociateLoading(true);
    try {
      const apiEndpoint = getRuntimeConfig().apiEndpoint.replace(/\/$/, '');
      const res = await authFetch(`${apiEndpoint}/api/v1/vehicles?limit=200`);
      if (res.ok) {
        const data = await res.json();
        const all = data.vehicles || [];
        // Filter out vehicles already in this fleet
        setAvailableVehicles(all.filter((v: any) => v.fleetId !== fleetId && !v.fleetId));
      }
    } catch (e) { console.error(e); }
    setAssociateLoading(false);
  };

  useEffect(() => {
    console.log('🚗 Vehicle useEffect called with fleetId:', fleetId, 'type:', typeof fleetId);
    if (fleetId && fleetId !== 'undefined') {
      console.log('🚗 Vehicle useEffect triggered for fleet:', fleetId);
      
      setVehiclesLoading(true);
      
      async function getVehicles() {
        await fetchFleetVehicles(fleetId);
        setIsVehiclesLoading(false);
      }
      getVehicles();
    } else {
      console.warn('Cannot load vehicles: fleetId is undefined');
      setFleetVehicles([]);
      setVehiclesLoading(false);
    }
  }, [fleetId]);

  const columnDefinitions = VEHICLE_COLUMN_DEFINITIONS(navigate).map((column) => ({
    ...column,
    ariaLabel: createTableSortLabelFn(column),
  }));

  const {
    items,
    actions,
    filteredItemsCount,
    collectionProps,
    filterProps,
    paginationProps,
    propertyFilterProps,
  } = useCollection(fleetVehicles, {
    filtering: {
      empty: <TableEmptyState resourceName="Vehicle" />,
      noMatch: (
        <TableNoMatchState onClearFilter={() => actions.setFiltering("")} />
      ),
    },
    pagination: { pageSize: 25 },
    sorting: { defaultState: { sortingColumn: columnDefinitions[1] } }, // Status column
    selection: {},
  });

  return (
    <>
    <Table
      {...collectionProps}
      enableKeyboardNavigation={true}
      columnDefinitions={columnDefinitions}
      loading={isVehiclesLoading}
      loadingText="Loading vehicles"
      items={items}
      ariaLabels={vehicleSelectionLabels}
      selectionType="multi"
      selectedItems={selectedItems}
      onSelectionChange={(event) =>
        setSelectedItems(event.detail.selectedItems)
      }
      pagination={<Pagination {...paginationProps} openEnd />}
      preferences={
        <CollectionPreferences
          title="Preferences"
          confirmLabel="Confirm"
          cancelLabel="Cancel"
          preferences={{
            pageSize: paginationProps.pageSize,
            visibleContent: ['vehicleVin', 'make', 'model', 'year', 'status', 'odometer']
          }}
          pageSizePreference={{
            title: 'Page size',
            options: [
              { value: 10, label: '10 vehicles' },
              { value: 25, label: '25 vehicles' },
              { value: 50, label: '50 vehicles' }
            ]
          }}
          visibleContentPreference={{
            title: 'Select visible columns',
            options: [
              { id: 'vehicleVin', label: 'VIN' },
              { id: 'make', label: 'Make' },
              { id: 'model', label: 'Model' },
              { id: 'year', label: 'Year' },
              { id: 'status', label: 'Status' },
              { id: 'licensePlate', label: 'License Plate' },
              { id: 'odometer', label: 'Odometer' }
            ]
          }}
          onConfirm={({ detail }) => {
            actions.setPageSize(detail.pageSize);
          }}
        />
      }
      filter={
        <TextFilter
          {...filterProps}
          filteringAriaLabel="Filter vehicles"
          filteringPlaceholder="Find vehicles"
          filteringClearAriaLabel="Clear"
          countText={getTextFilterCounterText(filteredItemsCount)}
        />
      }
      empty={
        <Box textAlign="center" color="inherit">
          <b>No vehicles</b>
          <Box padding={{ bottom: "s" }} variant="p" color="inherit">
            No vehicles found.
          </Box>
          <Button onClick={() => navigate(UI_ROUTES.VEHICLE_CREATE)}>
            Create vehicle
          </Button>
        </Box>
      }
      header={
        <Header
          counter={
            !vehiclesLoading && totalVehicleCount > 0 && items.length > 0
              ? `(${((paginationProps.currentPageIndex || 1) - 1) * 25 + 1}-${Math.min((paginationProps.currentPageIndex || 1) * 25, totalVehicleCount)} of ${totalVehicleCount} total)`
              : totalVehicleCount > 0 ? `(${totalVehicleCount})` : undefined
          }
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                disabled={!atLeastOneSelected}
                onClick={onDisassociateVehicles}
              >
                Disassociate Vehicles
              </Button>
              <Button onClick={onAssociateVehicles}>Associate Vehicles</Button>
              <Button 
                variant="primary"
                onClick={() => navigate(`/vehicles/create?fleetId=${fleetId}`)}
              >
                Add Vehicle
              </Button>
            </SpaceBetween>
          }
        >
          Fleet Vehicles
        </Header>
      }
    />
    {associateVisible && (
      <Modal visible onDismiss={() => { setAssociateVisible(false); setSelectedToAssociate([]); }} size="max"
        header="Associate Vehicles to Fleet"
        footer={<Box float="right"><SpaceBetween direction="horizontal" size="xs">
          <Button onClick={() => { setAssociateVisible(false); setSelectedToAssociate([]); }}>Cancel</Button>
          <Button variant="primary" disabled={!selectedToAssociate.length} loading={associateLoading}
            onClick={async () => {
              setAssociateLoading(true);
              try {
                const apiEndpoint = getRuntimeConfig().apiEndpoint.replace(/\/$/, '');
                await authFetch(`${apiEndpoint}/api/v1/fleets/associate-vehicles`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ fleetId, vehicleIds: selectedToAssociate.map((v: any) => v.vehicleId || v.id) })
                });
                setAssociateVisible(false);
                setSelectedToAssociate([]);
                window.location.reload();
              } catch (e) { console.error(e); }
              setAssociateLoading(false);
            }}>
            Associate {selectedToAssociate.length} Vehicle{selectedToAssociate.length !== 1 ? 's' : ''}
          </Button>
        </SpaceBetween></Box>}>
        <Table
          loading={associateLoading}
          loadingText="Loading available vehicles"
          items={availableVehicles}
          selectionType="multi"
          selectedItems={selectedToAssociate}
          onSelectionChange={({ detail }) => setSelectedToAssociate(detail.selectedItems)}
          empty={<Box textAlign="center" color="inherit" padding="l">No unassigned vehicles available</Box>}
          columnDefinitions={[
            { id: 'id', header: 'Vehicle ID', cell: (v: any) => v.vehicleId || v.id },
            { id: 'vin', header: 'VIN', cell: (v: any) => v.vin || '—' },
            { id: 'make', header: 'Make', cell: (v: any) => v.make || '—' },
            { id: 'model', header: 'Model', cell: (v: any) => v.model || '—' },
            { id: 'status', header: 'Status', cell: (v: any) => <StatusIndicator type={v.status === 'ACTIVE' ? 'success' : 'stopped'}>{v.status || 'Unknown'}</StatusIndicator> },
          ]}
          header={<Header counter={`(${availableVehicles.length} available)`}>Select Vehicles</Header>}
        />
      </Modal>
    )}
    </>
  );
}
