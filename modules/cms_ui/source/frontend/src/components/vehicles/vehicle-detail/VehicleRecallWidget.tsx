// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import {
  Box,
  Button,
  Container,
  Header,
  Link,
  ProgressBar,
  SpaceBetween,
  StatusIndicator,
  Table,
} from "@cloudscape-design/components";
import { nhtsaRecalls } from "../../recall-warranty/nhtsaRecallData";

interface Props {
  vehicleId: string;
  make?: string;
  model?: string;
}

const VehicleRecallWidget: React.FC<Props> = ({ vehicleId, make, model }) => {
  // Find recalls affecting this vehicle
  const vehicleRecalls = nhtsaRecalls.filter(r =>
    r.vehicles && r.vehicles.includes(vehicleId)
  );

  if (vehicleRecalls.length === 0) {
    return (
      <Container header={<Header variant="h2">Recalls & Warranty</Header>}>
        <StatusIndicator type="success">No active recalls for this vehicle</StatusIndicator>
      </Container>
    );
  }

  return (
    <SpaceBetween size="l">
      <Container header={
        <Header variant="h2" counter={`(${vehicleRecalls.length})`}
          actions={<Link href="/recall-warranty">View All Fleet Recalls</Link>}>
          Active Recalls
        </Header>
      }>
        <Table
          columnDefinitions={[
            { id: "id", header: "NHTSA #", cell: (item) => (
              <Link href={`https://www.nhtsa.gov/recalls?nhtsaId=${item.id}`} external>{item.id}</Link>
            ), width: 120 },
            { id: "severity", header: "Severity", cell: (item) => (
              <StatusIndicator type={item.severity === "Critical" ? "error" : item.severity === "High" ? "warning" : "info"}>
                {item.severity}
              </StatusIndicator>
            ), width: 90 },
            { id: "component", header: "Component", cell: (item) => item.component.split(':')[0], width: 160 },
            { id: "summary", header: "Summary", cell: (item) => (
              <Box color="text-body-secondary" fontSize="body-s">{item.summary.slice(0, 120)}...</Box>
            )},
            { id: "status", header: "Status", cell: () => (
              <StatusIndicator type="pending">Pending</StatusIndicator>
            ), width: 100 },
            { id: "actions", header: "", cell: () => (
              <Button iconName="calendar" variant="inline-link">Schedule</Button>
            ), width: 100 },
          ]}
          items={vehicleRecalls}
          variant="embedded"
        />
      </Container>
    </SpaceBetween>
  );
};

export default VehicleRecallWidget;
