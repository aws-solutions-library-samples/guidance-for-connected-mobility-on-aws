// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import {
  Box,
  Button,
  Container,
  Header,
  SpaceBetween,
  StatusIndicator,
  Table,
  TextFilter,
} from "@cloudscape-design/components";

const vehicles: { id: string; type: string; fuel: string; location: string; status: string; idleDays: number; mileage: string; maintenance: string; available: boolean }[] = [];

const VehicleAvailability: React.FC = () => {
  const [filterText, setFilterText] = React.useState("");
  const filtered = vehicles.filter(v =>
    !filterText || v.id.toLowerCase().includes(filterText.toLowerCase()) ||
    v.location.toLowerCase().includes(filterText.toLowerCase()) ||
    v.type.toLowerCase().includes(filterText.toLowerCase()) ||
    v.fuel.toLowerCase().includes(filterText.toLowerCase())
  );

  return (
    <Container header={
      <Header variant="h2" counter={`(${vehicles.filter(v => v.available).length} available)`}
        description="Vehicles currently idle and available for rebalancing. Excludes vehicles in maintenance, under recall, or on active trips.">
        Vehicle Availability
      </Header>
    }>
      <Table
        columnDefinitions={[
          { id: "id", header: "Vehicle", cell: (item) => <Box fontWeight="bold">{item.id}</Box> },
          { id: "type", header: "Type", cell: (item) => item.type },
          { id: "fuel", header: "Fuel", cell: (item) => (
            <StatusIndicator type={item.fuel === "BEV" ? "success" : "info"}>{item.fuel}</StatusIndicator>
          )},
          { id: "location", header: "Current Location", cell: (item) => item.location },
          { id: "idleDays", header: "Days Idle", cell: (item) => (
            <span style={{ color: item.idleDays > 5 ? "#F472B6" : item.idleDays > 3 ? "#FBBF24" : "#BEC8DC" }}>
              {item.idleDays}
            </span>
          )},
          { id: "mileage", header: "Mileage", cell: (item) => item.mileage },
          { id: "maintenance", header: "Maintenance", cell: (item) => (
            <StatusIndicator type={item.maintenance === "Clear" ? "success" : "warning"}>
              {item.maintenance}
            </StatusIndicator>
          )},
          { id: "available", header: "Available", cell: (item) => (
            <StatusIndicator type={item.available ? "success" : "stopped"}>
              {item.available ? "Yes" : "No"}
            </StatusIndicator>
          )},
          { id: "actions", header: "", cell: (item) => item.available ? (
            <Button iconName="send" variant="inline-link">Assign</Button>
          ) : null },
        ]}
        items={filtered}
        filter={
          <TextFilter filteringText={filterText} onChange={({ detail }) => setFilterText(detail.filteringText)}
            filteringPlaceholder="Filter by vehicle, location, type, or fuel" />
        }
        variant="embedded"
        stickyHeader
      />
    </Container>
  );
};

export default VehicleAvailability;
