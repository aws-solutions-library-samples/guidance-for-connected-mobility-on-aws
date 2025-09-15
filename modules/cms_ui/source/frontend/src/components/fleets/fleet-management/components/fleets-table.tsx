// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from "react";
import { useCollection } from "@cloudscape-design/collection-hooks";
import {
  Button,
  Pagination,
  Table,
  TextFilter,
  SpaceBetween,
  Link,
  Box,
  Header,
} from "@cloudscape-design/components";
import { useNavigate } from "react-router-dom";
import ItemState from "../item-state";
import {
  createTableSortLabelFn,
  getHeaderCounterText,
  getTextFilterCounterText,
  renderAriaLive,
} from "@/i18n-strings";
import {
  TableEmptyState,
  TableNoMatchState,
} from "@/components/commons/common-components";
import { fleetTableAriaLabels } from "../i18-strings/table";
import { FleetItem } from "@/types/fleet-types";
import { UI_ROUTES } from "@/utils/constants";
import { TablePreferences, FLEET_COLUMNS, DEFAULT_PAGE_SIZE_OPTIONS } from "@/components/commons/TablePreferences";

const rawColumns = [
  {
    id: "name",
    sortingField: "name",
    header: "Fleet Name",
    cell: (item: FleetItem) => (
      <div>
        <Link href={`/fleets/management/${item.id || item.fleetId}`}>{item.name}</Link>
      </div>
    ),
    minWidth: 180,
  },
  {
    id: "totalVehicles",
    sortingField: "totalVehicles",
    header: "Total Vehicles",
    cell: (item: FleetItem) => item.vehicleCount || item.totalVehicles || 0,
    minWidth: 120,
  },
  {
    id: "connectedVehicles",
    sortingField: "connectedVehicles",
    cell: (item: FleetItem) => item.connectedVehicles || 0,
    header: "Connected",
    minWidth: 120,
  },
  {
    id: "operationalCity",
    sortingField: "operationalCity",
    header: "Operational City",
    cell: (item: FleetItem) => item.operationalCity || "N/A",
    minWidth: 150,
  },
  {
    id: "activeFleetCampaigns",
    sortingField: "activeFleetCampaigns",
    cell: (item: FleetItem) => item.numActiveCampaigns,
    header: "Active Fleet Campaigns",
    minWidth: 120,
  },
];
const columnDefinitions = rawColumns.map((column) => ({
  ...column,
  ariaLabel: createTableSortLabelFn(column),
}));

export default function FleetsTable({
  fleets,
  selectedItems,
  onSelectionChange,
  onEdit,
  onDelete,
  isLoading,
  error,
}: any) {
  // Table preferences state
  const [preferences, setPreferences] = useState({
    pageSize: 25, // Default to 25 items per page
    visibleContent: ['name', 'totalVehicles', 'connectedVehicles', 'operationalCity', 'description', 'status'],
  });

  // Filter columns based on visible content preferences
  const visibleColumnDefinitions = columnDefinitions.filter(column => 
    preferences.visibleContent.includes(column.id)
  );

  const {
    items,
    actions,
    filteredItemsCount,
    collectionProps,
    filterProps,
    paginationProps,
  } = useCollection(fleets, {
    filtering: {
      empty: <TableEmptyState resourceName="Fleet" />,
      noMatch: (
        <TableNoMatchState onClearFilter={() => actions.setFiltering("")} />
      ),
    },
    pagination: { pageSize: preferences.pageSize },
    sorting: { defaultState: { sortingColumn: visibleColumnDefinitions[0] } },
    selection: {},
  });

  const navigate = useNavigate();

  let emptyTitle: string;
  let emptyMessage: string;

  if (error) {
    emptyTitle = "Error loading fleets";
    if (error.name === "403") {
      emptyMessage = "You do not have permission to view fleets.";
    } else {
      emptyMessage = "An error occurred while loading fleets.";
    }
  } else {
    emptyTitle = "No fleets";
    emptyMessage = "No fleets found.";
  }

  const emptyContent = (
    <Box textAlign="center" color="inherit">
      <b>{emptyTitle}</b>
      <Box padding={{ bottom: "s" }} variant="p" color="inherit">
        {emptyMessage}
      </Box>
      <Button onClick={() => navigate(UI_ROUTES.FLEET_CREATE)}>
        Create fleet
      </Button>
    </Box>
  );

  return (
    <Table
      {...collectionProps}
      loading={isLoading}
      loadingText="Loading fleets"
      enableKeyboardNavigation={true}
      selectedItems={selectedItems}
      onSelectionChange={onSelectionChange}
      columnDefinitions={visibleColumnDefinitions}
      items={items}
      selectionType="multi"
      ariaLabels={fleetTableAriaLabels}
      renderAriaLive={renderAriaLive}
      variant="full-page"
      stickyHeader={true}
      empty={emptyContent}
      header={
        <Header
          variant="h2"
          counter={`(${((paginationProps.currentPageIndex - 1) * preferences.pageSize) + 1}-${Math.min(paginationProps.currentPageIndex * preferences.pageSize, filteredItemsCount)} of ${filteredItemsCount} total)`}
          actions={
            <SpaceBetween size="xs" direction="horizontal">
              <Button disabled={selectedItems.length !== 1} onClick={onEdit}>
                Edit
              </Button>
              <Button disabled={selectedItems.length === 0} onClick={onDelete}>
                Delete
              </Button>
            </SpaceBetween>
          }
        >
          Fleets
        </Header>
      }
      filter={
        <TextFilter
          {...filterProps}
          filteringAriaLabel="Filter fleets"
          filteringPlaceholder="Find fleets"
          filteringClearAriaLabel="Clear"
          countText={getTextFilterCounterText(filteredItemsCount)}
        />
      }
      pagination={<Pagination {...paginationProps} />}
      preferences={
        <TablePreferences
          preferences={preferences}
          onConfirm={(newPreferences) => {
            console.log('🔧 Updating fleet preferences:', newPreferences);
            setPreferences(newPreferences);
            // Reset to first page when changing page size
            if (newPreferences.pageSize !== preferences.pageSize) {
              actions.setCurrentPageIndex(1);
            }
          }}
          pageSizeOptions={DEFAULT_PAGE_SIZE_OPTIONS}
          visibleContentOptions={FLEET_COLUMNS}
          resourceName="fleets"
        />
      }
    />
  );
}
