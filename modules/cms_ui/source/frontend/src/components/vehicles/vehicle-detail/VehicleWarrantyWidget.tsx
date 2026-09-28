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

// Mock warranty data keyed by vehicle — in production this comes from DynamoDB
const warrantyByVehicle: Record<string, any[]> = {
  "VEH-0049": [
    { component: "Brake pads (front)", failureType: "DTC C1234", mileage: "38,200", warrantyLimit: "50,000 mi", daysRemaining: 142, claimAmount: "$1,840", status: "Drafted", confidence: 94 },
  ],
  "VEH-0026": [
    { component: "Alternator", failureType: "DTC P0562", mileage: "44,100", warrantyLimit: "50,000 mi", daysRemaining: 89, claimAmount: "$920", status: "Not filed", confidence: 88 },
  ],
  "VEH-0043": [
    { component: "Turbocharger actuator", failureType: "DTC P0299", mileage: "31,800", warrantyLimit: "60,000 mi", daysRemaining: 310, claimAmount: "$2,450", status: "Drafted", confidence: 91 },
    { component: "Exhaust brake valve", failureType: "DTC P0478", mileage: "31,800", warrantyLimit: "60,000 mi", daysRemaining: 310, claimAmount: "$1,680", status: "Denied", confidence: 85 },
  ],
  "VEH-0004": [
    { component: "EGR valve", failureType: "DTC P0401", mileage: "47,200", warrantyLimit: "50,000 mi", daysRemaining: 34, claimAmount: "$680", status: "Not filed", confidence: 82 },
  ],
  "VEH-0008": [
    { component: "Fuel injector #3", failureType: "DTC P0203", mileage: "41,300", warrantyLimit: "50,000 mi", daysRemaining: 98, claimAmount: "$1,240", status: "Submitted", confidence: 90 },
  ],
  "VEH-0047": [
    { component: "DEF pump", failureType: "DTC P20EE", mileage: "36,500", warrantyLimit: "50,000 mi", daysRemaining: 185, claimAmount: "$890", status: "Approved", confidence: 92 },
  ],
  "VEH-0025": [
    { component: "Coolant sensor", failureType: "DTC P0116", mileage: "39,800", warrantyLimit: "50,000 mi", daysRemaining: 120, claimAmount: "$320", status: "Paid", confidence: 95 },
  ],
};

interface Props {
  vehicleId: string;
}

const VehicleWarrantyWidget: React.FC<Props> = ({ vehicleId }) => {
  const claims = warrantyByVehicle[vehicleId] || [];

  if (claims.length === 0) {
    return (
      <Container header={<Header variant="h2">Warranty</Header>}>
        <Box textAlign="center" padding="l">
          <StatusIndicator type="success">No warranty-eligible failures detected for this vehicle</StatusIndicator>
          <Box variant="p" color="text-body-secondary" margin={{ top: "s" }}>
            The agent continuously monitors telemetry for component failures matching warranty coverage rules.
          </Box>
        </Box>
      </Container>
    );
  }

  const totalRecoverable = claims.reduce((s, c) => s + parseFloat(c.claimAmount.replace(/[$,]/g, '')), 0);

  return (
    <SpaceBetween size="l">
      <Container header={
        <Header variant="h2" counter={`(${claims.length})`}
          description={`Est. recoverable: $${totalRecoverable.toLocaleString()}`}
          actions={<Link href="/warranty">View All Fleet Warranty Claims</Link>}>
          Warranty Claims
        </Header>
      }>
        <Table
          columnDefinitions={[
            { id: "component", header: "Component", cell: (item) => item.component },
            { id: "failure", header: "Failure", cell: (item) => <code>{item.failureType}</code>, width: 110 },
            { id: "mileage", header: "Mileage", cell: (item) => item.mileage, width: 80 },
            { id: "limit", header: "Warranty Limit", cell: (item) => item.warrantyLimit, width: 110 },
            { id: "remaining", header: "Coverage Left", cell: (item) => (
              <span style={{ color: item.daysRemaining < 60 ? '#d91515' : item.daysRemaining < 120 ? '#8D6605' : undefined }}>
                {item.daysRemaining} days
              </span>
            ), width: 100 },
            { id: "amount", header: "Claim", cell: (item) => <span style={{ fontWeight: 700 }}>{item.claimAmount}</span>, width: 80 },
            { id: "confidence", header: "Confidence", cell: (item) => (
              <ProgressBar value={item.confidence} additionalInfo={`${item.confidence}%`} variant="key-value" />
            ), width: 120 },
            { id: "status", header: "Status", cell: (item) => (
              <StatusIndicator type={
                item.status === "Paid" ? "success" : item.status === "Approved" ? "success" :
                item.status === "Submitted" ? "in-progress" : item.status === "Drafted" ? "in-progress" :
                item.status === "Denied" ? "error" : "pending"
              }>{item.status}</StatusIndicator>
            ), width: 100 },
            { id: "actions", header: "", cell: (item) => (
              <div style={{ display: 'flex', gap: '4px', flexWrap: 'nowrap' }}>
                {(item.status === "Drafted" || item.status === "Not filed") && <Button variant="primary" iconName="check" />}
                {item.status === "Denied" && <Button iconName="redo" />}
              </div>
            ), width: 70 },
          ]}
          items={claims}
          variant="embedded"
        />
      </Container>
    </SpaceBetween>
  );
};

export default VehicleWarrantyWidget;
