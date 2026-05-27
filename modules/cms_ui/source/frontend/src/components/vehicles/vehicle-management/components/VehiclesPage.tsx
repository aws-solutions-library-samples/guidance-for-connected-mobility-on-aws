// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { Box, Container, Flashbar, Grid, Header, SpaceBetween } from "@cloudscape-design/components";
import VehiclesTable from "./vehicles-table";
import VehicleMapView from "./VehicleMapView";

export function VehiclesPage({
  vehicles,
  totalVehicleCount,
  selectedItems,
  setSelectedItems,
  onDeleteInit,
  notifications,
  isLoading,
  error,
  currentPage,
  pageSize,
  paginationInfo,
  onPageChange,
  onPageSizeChange,
  onFleetFilterChange,
  searchText,
  onSearchChange,
  viewMode = 'table',
}: any) {
  const connectedCount = vehicles.filter((v: any) => v.connectionStatus === 'connected' || v.activityStatus === 'active').length;

  return (
    <SpaceBetween size="l">
      {notifications.length > 0 && <Flashbar items={notifications} stackItems={true} />}

      <Grid gridDefinition={[{ colspan: 3 }, { colspan: 3 }, { colspan: 3 }, { colspan: 3 }]}>
        <Container header={<Header variant="h2">Total Vehicles</Header>}>
          <Box variant="h1" color="text-status-info">{totalVehicleCount}</Box>
          <div><Box variant="small" color="text-body-secondary">Registered in fleet</Box></div>
        </Container>
        <Container header={<Header variant="h2">Connected</Header>}>
          <Box variant="h1" color="text-status-success">{connectedCount}</Box>
          <div><Box variant="small" color="text-body-secondary">Reporting telemetry</Box></div>
          <div><Box variant="small" color="text-status-info">{totalVehicleCount > 0 ? Math.round(connectedCount / totalVehicleCount * 100) : 0}% of fleet</Box></div>
        </Container>
        <Container header={<Header variant="h2">Needs Service</Header>}>
          <Box variant="h1" color="text-status-warning">{Math.max(0, totalVehicleCount - connectedCount)}</Box>
          <div><Box variant="small" color="text-body-secondary">Maintenance required</Box></div>
        </Container>
        <Container header={<Header variant="h2">Avg. Mileage</Header>}>
          <Box variant="h1" color={totalVehicleCount > 0 ? "text-status-info" : "text-body-secondary"}>—</Box>
          <div><Box variant="small" color="text-body-secondary">Miles across fleet</Box></div>
        </Container>
      </Grid>

      {viewMode === 'map' ? (
        <Container>
          <VehicleMapView />
        </Container>
      ) : (
        <Container>
          <VehiclesTable
            vehicles={vehicles}
            totalVehicleCount={totalVehicleCount}
            selectedItems={selectedItems}
            onSelectionChange={(event: any) =>
              setSelectedItems(event.detail.selectedItems)
            }
            onDelete={onDeleteInit}
            isLoading={isLoading}
            error={error}
            currentPage={currentPage}
            pageSize={pageSize}
            paginationInfo={paginationInfo}
            onPageChange={onPageChange}
            onPageSizeChange={onPageSizeChange}
            onFleetFilterChange={onFleetFilterChange}
            searchText={searchText}
            onSearchChange={onSearchChange}
          />
        </Container>
      )}
    </SpaceBetween>
  );
}
