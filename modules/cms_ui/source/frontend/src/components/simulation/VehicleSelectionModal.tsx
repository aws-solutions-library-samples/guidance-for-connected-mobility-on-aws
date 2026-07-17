// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useMemo, useEffect } from 'react';
import {
  Modal,
  Box,
  SpaceBetween,
  Button,
  Table,
  Header,
  Pagination,
  TextFilter,
  Badge,
  StatusIndicator,
  CollectionPreferences,
  PropertyFilter,
  Select
} from '@cloudscape-design/components';

interface Vehicle {
  vehicleId: string;
  vin: string;
  make: string;
  model: string;
  year: number;
  name: string;
  has_certificate: boolean;
  certificate_ready: boolean;
  status: string;
  fleetId?: string;
  license_plate?: string;
  fuel_type?: string;
  vehicle_type?: string;
  mileage?: number;
}

interface VehicleSelectionModalProps {
  visible: boolean;
  onDismiss: () => void;
  onConfirm: (selectedVehicles: Vehicle[]) => void;
  availableVehicles: Vehicle[];
  currentSelection: Vehicle[];
  onSearch?: (searchTerm: string, page?: number, limit?: number) => Promise<{ vehicles: Vehicle[]; totalCount: number }>;
  simulationMode?: 'mqtt_direct' | 'fwe';
}

const ITEMS_PER_PAGE = 20;

export default function VehicleSelectionModal({
  visible,
  onDismiss,
  onConfirm,
  availableVehicles,
  currentSelection,
  onSearch,
  simulationMode
}: VehicleSelectionModalProps) {
  const [selectedItems, setSelectedItems] = useState<Vehicle[]>([]);
  const [currentPageIndex, setCurrentPageIndex] = useState(1);
  const [filteringText, setFilteringText] = useState('');
  const [fleetFilter, setFleetFilter] = useState('all');
  const [sortingColumn, setSortingColumn] = useState<any>({ sortingField: 'name' });
  const [sortingDescending, setSortingDescending] = useState(false);
  const [paginatedVehicles, setPaginatedVehicles] = useState<Vehicle[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [fleets, setFleets] = useState<Array<{fleetId: string, name: string}>>([]);
  const [campaignVins, setCampaignVins] = useState<Set<string> | null>(null);

  // Fetch fleets for filter dropdown
  const fetchFleets = React.useCallback(async () => {
    try {
      const runtimeConfig = (window as any).runtimeConfig;
      const apiEndpoint = runtimeConfig?.apiEndpoint || 'getApiEndpoint()/';
      const response = await fetch(`${apiEndpoint}api/v1/fleets`);
      const data = await response.json();
      setFleets(data.fleets || []);
    } catch (error) {
      console.error('Failed to fetch fleets:', error);
    }
  }, []);

  // Fetch vehicles with server-side pagination
  const fetchVehicles = React.useCallback(async (searchTerm: string = '', page: number = 1, fleetId: string = 'all') => {
    if (!onSearch) {
      setPaginatedVehicles(availableVehicles);
      setTotalCount(availableVehicles.length);
      return;
    }

    setLoading(true);
    try {
      // Build parameters including fleet filter
      const params = new URLSearchParams({
        has_certificate: 'true',
        onboard_only: 'true',  // Off-board OEM (cloud-fed) vehicles can't be simulated
        limit: ITEMS_PER_PAGE.toString(),
        page: page.toString()
      });
      
      if (searchTerm && searchTerm.trim()) {
        params.append('search', searchTerm.trim());
      }
      
      if (fleetId && fleetId !== 'all') {
        params.append('fleetId', fleetId);
      }

      const runtimeConfig = (window as any).runtimeConfig;
      const apiEndpoint = runtimeConfig?.apiEndpoint || 'getApiEndpoint()/';
      const response = await fetch(`${apiEndpoint}api/v1/vehicles?${params.toString()}`);
      const data = await response.json();
      
      const transformedVehicles = (data.vehicles || []).map((vehicle: any) => ({
        vehicleId: vehicle.vehicleId,
        vin: vehicle.vin,
        make: vehicle.make,
        model: vehicle.model,
        year: vehicle.year,
        name: `${vehicle.make} ${vehicle.model}`,
        has_certificate: true,
        certificate_ready: true,
        status: vehicle.status || 'ACTIVE',
        fleetId: vehicle.fleetId,
        license_plate: vehicle.licensePlate,
        fuel_type: vehicle.fuelType,
        vehicle_type: vehicle.vehicleType,
        mileage: vehicle.mileage
      }));

      setPaginatedVehicles(transformedVehicles);
      setTotalCount(data.totalCount || transformedVehicles.length);
    } catch (error) {
      console.error('Failed to fetch vehicles:', error);
      setPaginatedVehicles([]);
      setTotalCount(0);
    } finally {
      setLoading(false);
    }
  }, [onSearch, availableVehicles]);

  // Debounced search effect
  React.useEffect(() => {
    const timeoutId = setTimeout(() => {
      fetchVehicles(filteringText, 1, fleetFilter);
      setCurrentPageIndex(1);
    }, 500);

    return () => clearTimeout(timeoutId);
  }, [filteringText, fleetFilter, fetchVehicles]);

  // Fetch vehicles when page changes
  React.useEffect(() => {
    fetchVehicles(filteringText, currentPageIndex, fleetFilter);
  }, [currentPageIndex, fetchVehicles]);

  // Initial load when modal opens
  React.useEffect(() => {
    if (visible) {
      fetchFleets();
      fetchVehicles('', 1, 'all');
      setCurrentPageIndex(1);
      setFilteringText('');
      setFleetFilter('all');

      // Fetch active campaigns for campaign column
      const runtimeConfig2 = (window as any).runtimeConfig;
      const simApi = runtimeConfig2?.simulationApiEndpoint || '';
      fetch(`${simApi}/api/simulation/campaigns`)
        .then(r => r.json())
        .then(data => {
          const vins = new Set<string>();
          Object.entries(data.campaigns || {}).forEach(([vin, info]: [string, any]) => {
            if (info.hasSignals) vins.add(vin);
          });
          setCampaignVins(vins);
        })
        .catch(() => setCampaignVins(null));
    }
  }, [visible, fetchVehicles, fetchFleets, simulationMode]);

  // Update selected items when modal opens or currentSelection changes
  React.useEffect(() => {
    if (visible) {
      setSelectedItems([...currentSelection]);
    }
  }, [visible, currentSelection]);

  // Calculate total pages
  const totalPages = Math.ceil(totalCount / ITEMS_PER_PAGE);

  const handleSelectionChange = ({ detail }: any) => {
    setSelectedItems(detail.selectedItems);
  };

  const handleDismiss = () => {
    // Reset to original selection when canceling
    setSelectedItems([...currentSelection]);
    setFilteringText('');
    setCurrentPageIndex(1);
    onDismiss();
  };

  const handleConfirm = () => {
    onConfirm(selectedItems);
    onDismiss();
  };

  // Filter vehicles by active campaign in FWE mode
  const displayedVehicles = React.useMemo(() => {
    if (simulationMode !== 'fwe' || !campaignVins) return paginatedVehicles;
    return paginatedVehicles.filter(v => campaignVins.has(v.vin));
  }, [paginatedVehicles, campaignVins, simulationMode]);

  const columnDefinitions = [
    {
      id: 'name',
      header: 'Vehicle Name',
      cell: (vehicle: Vehicle) => (
        <div>
          <div style={{ fontWeight: 'bold' }}>{vehicle.make} {vehicle.model}</div>
          <div style={{ fontSize: '0.875rem', color: '#5f6b7a', display: 'block' }}>
            Year: {vehicle.year}
          </div>
        </div>
      ),
      sortingField: 'name',
      isRowHeader: true
    },
    {
      id: 'vin',
      header: 'VIN',
      cell: (vehicle: Vehicle) => (
        <Box fontFamily="monospace" fontSize="body-s">
          {vehicle.vin}
        </Box>
      ),
      sortingField: 'vin'
    },
    {
      id: 'certificate',
      header: 'Certificate',
      cell: (vehicle: Vehicle) => (
        vehicle.has_certificate ? (
          <Badge color="green">Ready</Badge>
        ) : (
          <Badge color="grey">Will Create</Badge>
        )
      ),
      sortingField: 'has_certificate'
    },
    {
      id: 'campaign',
      header: 'Campaign',
      cell: (vehicle: Vehicle) => {
        if (!campaignVins) return <Badge color="grey">—</Badge>;
        return campaignVins.has(vehicle.vin)
          ? <Badge color="green">Active</Badge>
          : <Badge color="red">None</Badge>;
      }
    },
    {
      id: 'status',
      header: 'Status',
      cell: (vehicle: Vehicle) => (
        <StatusIndicator type={vehicle.status === 'ACTIVE' ? 'success' : 'info'}>
          {vehicle.status || 'IDLE'}
        </StatusIndicator>
      ),
      sortingField: 'status'
    }
  ];

  return (
    <Modal
      onDismiss={handleDismiss}
      visible={visible}
      size="large"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={handleDismiss}>
              Cancel
            </Button>
            <Button 
              variant="primary" 
              onClick={handleConfirm}
              disabled={selectedItems.length === 0}
            >
              Select {selectedItems.length} Vehicle{selectedItems.length !== 1 ? 's' : ''}
            </Button>
          </SpaceBetween>
        </Box>
      }
      header={
        <Header
          variant="h1"
          description={`Choose vehicles for simulation from ${totalCount} available vehicles`}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Box variant="awsui-key-label">
                Fleet Filter: {fleetFilter === 'all' ? 'All Fleets' : fleets.find(f => f.fleetId === fleetFilter)?.name || fleetFilter}
              </Box>
            </SpaceBetween>
          }
        >
          Select Vehicles for Simulation
        </Header>
      }
    >
      <SpaceBetween size="m">
        <Table
          columnDefinitions={columnDefinitions}
          items={displayedVehicles}
          selectionType="multi"
          selectedItems={selectedItems}
          onSelectionChange={handleSelectionChange}
          trackBy="vehicleId"
          onSortingChange={({ detail }) => {
            setSortingColumn(detail);
            setSortingDescending(detail.isDescending || false);
          }}
          sortingColumn={sortingColumn}
          sortingDescending={sortingDescending}
          header={
            <Header
              counter={`(${selectedItems.length} of ${totalCount} selected)`}
              actions={
                <SpaceBetween direction="horizontal" size="xs">
                  <Box variant="awsui-key-label">
                    Showing {displayedVehicles.length} of {totalCount} vehicles
                    {simulationMode === 'fwe' && campaignVins ? ` (${campaignVins.size} with active campaigns)` : ''}
                  </Box>
                </SpaceBetween>
              }
            >
              Vehicle Selection
            </Header>
          }
          filter={
            <SpaceBetween direction="horizontal" size="s">
              <div style={{ width: '87%' }}>
                <TextFilter
                  filteringText={filteringText}
                  onChange={({ detail }) => {
                    setFilteringText(detail.filteringText);
                    setCurrentPageIndex(1);
                  }}
                  filteringPlaceholder="Search by VIN, name, make, or model..."
                  filteringAriaLabel="Filter vehicles"
                />
              </div>
              <div style={{ width: '13%', minWidth: '150px' }}>
                <Select
                  selectedOption={
                    fleets.find(f => f.fleetId === fleetFilter) 
                      ? { label: fleets.find(f => f.fleetId === fleetFilter)?.name || 'All Fleets', value: fleetFilter }
                      : { label: 'All Fleets', value: 'all' }
                  }
                  onChange={({ detail }) => {
                    setFleetFilter(detail.selectedOption?.value || 'all');
                    setCurrentPageIndex(1);
                  }}
                  options={[
                    { label: 'All Fleets', value: 'all' },
                    ...fleets.map(fleet => ({ label: fleet.name, value: fleet.fleetId }))
                  ]}
                  placeholder="Filter by fleet"
                />
              </div>
            </SpaceBetween>
          }
          pagination={
            <Pagination
              currentPageIndex={currentPageIndex}
              onChange={({ detail }) => setCurrentPageIndex(detail.currentPageIndex)}
              pagesCount={totalPages}
              ariaLabels={{
                nextPageLabel: 'Next page',
                previousPageLabel: 'Previous page',
                pageLabel: pageNumber => `Page ${pageNumber} of all pages`
              }}
            />
          }
          empty={
            <Box textAlign="center" color="inherit">
              <Box variant="strong" textAlign="center" color="inherit">
                No vehicles found
              </Box>
              <Box variant="p" padding={{ bottom: 's' }} color="inherit">
                {filteringText ? 'No vehicles match your search criteria.' : 'No vehicles available for selection.'}
              </Box>
              {filteringText && (
                <Button onClick={() => setFilteringText('')}>Clear filter</Button>
              )}
            </Box>
          }
          loadingText="Loading vehicles..."
        />
      </SpaceBetween>
    </Modal>
  );
}
