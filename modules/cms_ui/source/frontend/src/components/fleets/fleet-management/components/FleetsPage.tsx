// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { 
  Flashbar,
  Grid,
  Container,
  Header,
  Box,
  SpaceBetween
} from "@cloudscape-design/components";
import FleetsTable from "./fleets-table";

export function FleetsPage({
  fleets,
  selectedItems,
  setSelectedItems,
  onEditInit,
  onDeleteInit,
  notifications,
  isLoading,
  error,
}: any) {
  // Calculate fleet metrics
  const totalFleets = fleets.length;
  const totalVehicles = fleets.reduce((sum: number, fleet: any) => sum + (fleet.vehicleCount || 0), 0);
  const activeFleets = fleets.filter((fleet: any) => fleet.status === 'active' || !fleet.status).length;
  const averageFleetSize = totalFleets > 0 ? Math.round(totalVehicles / totalFleets) : 0;

  return (
    <SpaceBetween size="l">
      {notifications.length > 0 && <Flashbar items={notifications} stackItems={true} />}
      
      {/* Fleet Summary Tiles */}
      <Grid gridDefinition={[{ colspan: 3 }, { colspan: 3 }, { colspan: 3 }, { colspan: 3 }]}>
        <Container header={<Header variant="h2">Total Fleets</Header>}>
          <Box variant="h1" color="text-status-info">{totalFleets}</Box>
          <div><Box variant="small" color="text-body-secondary">{activeFleets} active fleets</Box></div>
        </Container>

        <Container header={<Header variant="h2">Total Vehicles</Header>}>
          <Box variant="h1" color="text-status-success">{totalVehicles}</Box>
          <div><Box variant="small" color="text-body-secondary">Across all fleets</Box></div>
        </Container>

        <Container header={<Header variant="h2">Average Fleet Size</Header>}>
          <Box variant="h1" color="text-status-info">{averageFleetSize}</Box>
          <div><Box variant="small" color="text-body-secondary">Vehicles per fleet</Box></div>
          {averageFleetSize > 0 && (
            <div><Box variant="small" color="text-status-success">Optimal range: 15-25</Box></div>
          )}
        </Container>

        <Container header={<Header variant="h2">Fleet Utilization</Header>}>
          <Box variant="h1" color={totalFleets > 0 ? "text-status-success" : "text-body-secondary"}>
            {totalFleets > 0 ? "—" : "—"}
          </Box>
          <div><Box variant="small" color="text-body-secondary">Average across active fleets</Box></div>
        </Container>
      </Grid>

      {/* Fleet Management Table */}
      <Container>
      <FleetsTable
        fleets={fleets}
        selectedItems={selectedItems}
        onSelectionChange={(event: any) =>
          setSelectedItems(event.detail.selectedItems)
        }
        onDelete={onDeleteInit}
        onEdit={onEditInit}
        isLoading={isLoading}
        error={error}
      />
      </Container>
    </SpaceBetween>
  );
}
