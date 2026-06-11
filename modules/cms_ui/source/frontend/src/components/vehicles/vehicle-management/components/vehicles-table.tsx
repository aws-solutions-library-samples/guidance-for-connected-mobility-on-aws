// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect } from "react";
import {
  Badge,
  Button,
  Pagination,
  Box,
  Table,
  TextFilter,
  SpaceBetween,
  Link,
  Header,
  StatusIndicator,
  Token,
} from "@cloudscape-design/components";
import {
  createTableSortLabelFn,
  getHeaderCounterText,
  getHeaderCounterServerSideText,
  getTextFilterCounterText,
  renderAriaLive,
} from "@/i18n-strings";
import {
  TableEmptyState,
  TableNoMatchState,
} from "@/components/commons/common-components";
import { vehicleTableAriaLabels } from "../i18-strings/table";
import { VehicleItem, VehicleStatus, getVehicleSource, isOEM1Vehicle } from "@/types/fleet-types";
import { UI_ROUTES } from "@/utils/constants";
import { useNavigate } from "react-router-dom";
import { TablePreferences, VEHICLE_COLUMNS, DEFAULT_PAGE_SIZE_OPTIONS } from "@/components/commons/TablePreferences";
import { FleetSelector } from "@/components/commons/FleetSelector";

export default function VehiclesTable({
  vehicles,
  totalVehicleCount,
  selectedItems,
  onSelectionChange,
  onDelete,
  isLoading,
  error,
  // Server-side pagination props
  currentPage,
  pageSize,
  paginationInfo,
  onPageChange,
  onPageSizeChange,
  onFleetFilterChange, // Add fleet filter callback
  searchText: externalSearchText, // Server-side search text (controlled)
  onSearchChange,                  // Callback to trigger server-side search
  // Source filter props (task 5.3 — wired by task 5.4)
  sourceFilter,
  onSourceFilterChange,
  // OEM1 bulk action callbacks (task 5.3 — gated to OEM1 rows)
  onBulkUnenroll,
}: any) {
  const navigate = useNavigate();
  
  // Table preferences state - now controlled by server-side pagination
  const [preferences, setPreferences] = useState({
    pageSize: pageSize || 25, // Use server-side pageSize
    visibleContent: ['name', 'vin', 'make', 'model', 'year', 'licensePlate', 'status', 'source'],
  });

  // Update preferences when server-side pageSize changes
  useEffect(() => {
    if (pageSize && pageSize !== preferences.pageSize) {
      setPreferences(prev => ({ ...prev, pageSize }));
    }
  }, [pageSize]);

  const rawColumns = [
    {
      id: "name",
      sortingField: "name",
      header: "VIN",
      cell: (item: VehicleItem) => {
        // Use the actual VIN from the database, fallback to vehicleId if VIN is not available
        const vehicleIdentifier = item.vin || item.vehicleId || item.name;
        
        // Check if this looks like a VIN (16-17 characters, alphanumeric, starts with fleet pattern)
        const isVinPattern = /^[A-HJ-NPR-Z0-9]{16,17}$/i.test(vehicleIdentifier) || 
                            /^1FLEET\d{10}$/i.test(vehicleIdentifier);
        
        if (isVinPattern) {
          // For VINs, link to VehicleDetailView with trip functionality
          return (
            <div>
              <Link onFollow={(e) => { e.preventDefault(); navigate(`/vehicles/management/${item.vehicleId}`); }}>{vehicleIdentifier}</Link>
            </div>
          );
        } else {
          // For non-VINs, use vehicleId for navigation
          return (
            <div>
              <Link onFollow={(e) => { e.preventDefault(); navigate(`/vehicles/management/${item.vehicleId}`); }}>{vehicleIdentifier}</Link>
            </div>
          );
        }
      },
      minWidth: 100,
    },
    {
      id: "status",
      sortingField: "status",
      cell: (item: VehicleItem) => {
        // For off-board (OEM1) vehicles, Active/Connected = enrolled in OEM1 feed.
        // Show "Enrolled" to avoid implying a live IoT connection state.
        if (isOEM1Vehicle(item)) {
          const oem1Status = (item as any).status?.toLowerCase();
          if (oem1Status === 'connected' || oem1Status === 'active') {
            return <StatusIndicator type={"success"}>Enrolled</StatusIndicator>;
          }
          return <StatusIndicator type={"warning"}>{(item as any).status || 'Unknown'}</StatusIndicator>;
        }
        const status = item.status?.toLowerCase();
        switch (status) {
          case "active":
          case VehicleStatus.ACTIVE.toLowerCase():
            return <StatusIndicator type={"success"}>{item.status}</StatusIndicator>;
          case "connected":
            return <StatusIndicator type={"info"}>{item.status}</StatusIndicator>;
          case "inactive":
          case VehicleStatus.INACTIVE.toLowerCase():
            return <StatusIndicator type={"in-progress"}>{item.status}</StatusIndicator>;
          default:
            return <StatusIndicator type={"warning"}>{item.status || "unknown"}</StatusIndicator>;
        }
      },
      header: "Status",
      minWidth: 120,
    },
    {
      id: "make",
      sortingField: "make",
      header: "Make",
      cell: (item: VehicleItem) => item.make || item.attributes?.make || "-",
      minWidth: 70,
    },
    {
      id: "model",
      sortingField: "model",
      header: "Model",
      cell: (item: VehicleItem) => item.model || item.attributes?.model || "-",
      minWidth: 70,
    },
    {
      id: "year",
      sortingField: "year",
      header: "Year",
      cell: (item: VehicleItem) => item.year || item.attributes?.year || "-",
      minWidth: 70,
    },
    {
      id: "licensePlate",
      sortingField: "licensePlate",
      header: "License Plate",
      cell: (item: VehicleItem) => item.licensePlate || item.attributes?.licensePlate || 'N/A',
      minWidth: 70,
    },
    {
      id: "source",
      sortingField: "source",
      header: "Source",
      cell: (item: VehicleItem) => {
        const src = getVehicleSource(item);
        if (src === 'oem1') {
          return (
            <span
              style={{ cursor: 'pointer' }}
              onClick={() => onSourceFilterChange?.('oem1')}
            >
              <Badge color="severity-medium" data-testid={`source-badge-${item.vehicleId}`}>Off-board</Badge>
            </span>
          );
        }
        return (
          <span
            style={{ cursor: 'pointer' }}
            onClick={() => onSourceFilterChange?.('cms')}
          >
            <Badge color="blue" data-testid={`source-badge-${item.vehicleId}`}>On-board</Badge>
          </span>
        );
      },
      minWidth: 90,
    },
    {
      id: "actions",
      header: "Actions",
      cell: (item: VehicleItem) => {
        // Use the actual VIN from the database, fallback to vehicleId if VIN is not available
        const vehicleIdentifier = item.vin || item.vehicleId || item.name;
        
        return (
          <SpaceBetween direction="horizontal" size="xs">
            <Button
              size="small"
              onClick={() => navigate(`/vehicles/management/${item.vehicleId}`)}
              iconName="view-horizontal"
            >
              View Details
            </Button>
          </SpaceBetween>
        );
      },
      minWidth: 120,
    },
  ];

  const columnDefinitions = rawColumns
    .filter(column => preferences.visibleContent.includes(column.id))
    .map((column) => ({
      ...column,
      ariaLabel: createTableSortLabelFn(column),
    }));

  // Server-side pagination and filtering
  const [filterText, setFilterText] = useState(externalSearchText || '');

  // Debounce: when the user types, notify parent (server-side search) after 400ms.
  // Only fires when onSearchChange is provided and the value actually differs from
  // the parent's current value (avoids feedback loop).
  useEffect(() => {
    if (!onSearchChange) return;
    if (filterText === (externalSearchText || '')) return;
    const t = setTimeout(() => {
      onSearchChange(filterText);
    }, 400);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterText]);
  const [selectedFleet, setSelectedFleet] = useState<string>('all');
  
  // Handle fleet change and notify parent
  const handleFleetChange = (fleetId: string) => {
    setSelectedFleet(fleetId);
    onFleetFilterChange?.(fleetId);
  };
  
  // Filter vehicles client-side for text search only (fleet filtering is server-side).
  // BUT: when onSearchChange is provided, the parent does server-side search and
  // we trust those results — skip the client-side filter to avoid double-filtering
  // bugs (e.g., the server returns 1 vehicle but a stale client-side filter rejects it).
  const textFilteredVehicles = onSearchChange ? vehicles : vehicles.filter((vehicle: VehicleItem) => {
    // Text filter only
    if (!filterText) return true;
    const searchText = filterText.toLowerCase();
    return (
      // VIN/Name column
      (vehicle.vin || vehicle.vehicleId || vehicle.name)?.toLowerCase().includes(searchText) ||
      // Status column
      vehicle.status?.toLowerCase().includes(searchText) ||
      // Make column
      (vehicle.make || vehicle.attributes?.make)?.toLowerCase().includes(searchText) ||
      // Model column
      (vehicle.model || vehicle.attributes?.model)?.toLowerCase().includes(searchText) ||
      // Year column
      (vehicle.year || vehicle.attributes?.year)?.toString().toLowerCase().includes(searchText) ||
      // License Plate column
      (vehicle.licensePlate || vehicle.attributes?.licensePlate)?.toLowerCase().includes(searchText)
    );
  });
  // Source filter (M8: uses getVehicleSource — no literal === 'oem1' compares)
  const filteredVehicles = sourceFilter
    ? textFilteredVehicles.filter((v: VehicleItem) => getVehicleSource(v) === sourceFilter)
    : textFilteredVehicles;

  // Server-side pagination props
  const serverPaginationProps = {
    currentPageIndex: currentPage || 1,
    pagesCount: paginationInfo?.totalPages || Math.ceil(totalVehicleCount / (pageSize || 25)),
    onChange: ({ detail }: any) => {
      console.log('📄 Server pagination change:', detail.currentPageIndex);
      onPageChange?.(detail.currentPageIndex);
    },
    ariaLabels: {
      nextPageLabel: 'Next page',
      previousPageLabel: 'Previous page',
      pageLabel: (pageNumber: number) => `Page ${pageNumber}`,
    },
  };

  let emptyTitle: string;
  let emptyMessage: string;

  if (error) {
    emptyTitle = "Error loading vehicles";
    if (error.name === "403") {
      emptyMessage = "You do not have permission to view vehicles.";
    } else {
      emptyMessage = "An error occurred while loading vehicles.";
    }
  } else {
    emptyTitle = "No vehicles";
    emptyMessage = "No vehicles found.";
  }

  const emptyContent = (
    <Box textAlign="center" color="inherit">
      <b>{emptyTitle}</b>
      <Box padding={{ bottom: "s" }} variant="p" color="inherit">
        {emptyMessage}
      </Box>
      <Button onClick={() => { window.location.href = UI_ROUTES.VEHICLE_CREATE; }}>
        Create Vehicle
      </Button>
    </Box>
  );

  return (
    <Table
      loading={isLoading}
      loadingText="Loading vehicles"
      enableKeyboardNavigation={true}
      selectedItems={selectedItems}
      onSelectionChange={onSelectionChange}
      columnDefinitions={columnDefinitions}
      items={filteredVehicles} // Use filtered vehicles instead of items from useCollection
      selectionType="multi"
      ariaLabels={vehicleTableAriaLabels}
      renderAriaLive={renderAriaLive}
      variant="full-page"
      stickyHeader={true}
      empty={
        filteredVehicles.length === 0 && !filterText ? (
          <TableEmptyState resourceName="Vehicle" />
        ) : filteredVehicles.length === 0 && filterText ? (
          <TableNoMatchState onClearFilter={() => setFilterText("")} />
        ) : (
          emptyContent
        )
      }
      header={
        <Header
          variant="h2"
          counter={paginationInfo ? `(${((currentPage || 1) - 1) * (pageSize || 25) + 1}-${((currentPage || 1) - 1) * (pageSize || 25) + (paginationInfo.returned || vehicles.length)} of ${paginationInfo.total || totalVehicleCount} total)` : getHeaderCounterServerSideText(totalVehicleCount, selectedItems.length > 0 ? selectedItems.length : undefined)}
          actions={
            <SpaceBetween size="xs" direction="horizontal">
              <div style={{ minWidth: '200px' }}>
                <FleetSelector
                  selectedFleet={selectedFleet}
                  onFleetChange={handleFleetChange}
                  label=""
                />
              </div>
              <Button 
                disabled={selectedItems.length !== 1}
                onClick={() => {
                  const selectedItem = selectedItems[0];
                  navigate(`/vehicles/edit?vehicleId=${selectedItem.vehicleId}`);
                }}
              >
                Edit
              </Button>
              <Button 
                disabled={selectedItems.length === 0} 
                onClick={onDelete}
              >
                Delete
              </Button>
              <Button
                disabled={selectedItems.length === 0 || !selectedItems.every((v: VehicleItem) => isOEM1Vehicle(v))}
                data-testid="bulk-unenroll-btn"
                onClick={() => onBulkUnenroll?.(selectedItems)}
              >
                Unenroll
              </Button>
            </SpaceBetween>
          }
        >
          Vehicles
        </Header>
      }
      filter={
        <SpaceBetween size="xs" direction="vertical">
          <TextFilter
            filteringText={filterText}
            onChange={({ detail }) => setFilterText(detail.filteringText)}
            filteringAriaLabel="Filter vehicles"
            filteringPlaceholder="Find vehicles"
            filteringClearAriaLabel="Clear"
            countText={getTextFilterCounterText(filteredVehicles.length)}
          />
          {sourceFilter && (
            <SpaceBetween size="xs" direction="horizontal">
              <span
                data-testid="source-filter-chip"
                style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', cursor: 'pointer' }}
              >
                <Badge color={sourceFilter === 'oem1' ? 'severity-medium' : 'blue'}>
                  {sourceFilter === 'oem1' ? 'Off-board only' : 'On-board only'}
                </Badge>
                <Button
                  variant="icon"
                  iconName="close"
                  ariaLabel="Clear source filter"
                  onClick={() => onSourceFilterChange?.(null)}
                />
              </span>
            </SpaceBetween>
          )}
        </SpaceBetween>
      }
      pagination={<Pagination {...serverPaginationProps} />}
      preferences={
        <TablePreferences
          preferences={preferences}
          onConfirm={(newPreferences) => {
            console.log('🔧 Updating preferences:', newPreferences);
            setPreferences(newPreferences);
            // Notify parent component of page size change
            if (newPreferences.pageSize !== preferences.pageSize) {
              onPageSizeChange?.(newPreferences.pageSize);
            }
          }}
          pageSizeOptions={DEFAULT_PAGE_SIZE_OPTIONS}
          visibleContentOptions={VEHICLE_COLUMNS}
          resourceName="vehicles"
        />
      }
    />
  );
}
