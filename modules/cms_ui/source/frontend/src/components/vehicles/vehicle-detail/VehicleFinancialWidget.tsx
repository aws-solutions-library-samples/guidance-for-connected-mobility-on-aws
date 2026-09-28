// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import {
  Box,
  ColumnLayout,
  Container,
  Header,
  SpaceBetween,
  StatusIndicator,
} from "@cloudscape-design/components";

interface Props {
  vehicleData: any;
}

const VehicleFinancialWidget: React.FC<Props> = ({ vehicleData }) => {
  const msrp = parseFloat(vehicleData.msrp || 0);
  const currentValue = parseFloat(vehicleData.currentValue || 0);
  const depreciation = parseFloat(vehicleData.totalDepreciation || 0);
  const costPerMile = parseFloat(vehicleData.costPerMile || 0);
  const totalCostYTD = parseFloat(vehicleData.totalCostYTD || 0);
  const annualPremium = parseFloat(vehicleData.annualPremium || 0);
  const monthlyLease = parseFloat(vehicleData.monthlyLease || 0);
  const warrantyActive = vehicleData.warrantyActive === true || vehicleData.warrantyActive === "true";
  const warrantyDays = parseInt(vehicleData.warrantyDaysRemaining || 0);
  const warrantyMiles = parseInt(vehicleData.warrantyMilesRemaining || 0);

  if (!msrp) return null; // No financial data available

  return (
    <Container header={<Header variant="h2">Financial Overview</Header>}>
      <ColumnLayout columns={4} variant="text-grid">
        {/* Acquisition */}
        <SpaceBetween size="xs">
          <Box variant="awsui-key-label">Acquisition</Box>
          <Box>{vehicleData.acquisitionType || 'Unknown'}</Box>
          <Box variant="awsui-key-label">Purchase Date</Box>
          <Box>{vehicleData.purchaseDate || 'N/A'}</Box>
          <Box variant="awsui-key-label">MSRP</Box>
          <Box>${msrp.toLocaleString()}</Box>
          <Box variant="awsui-key-label">Acquisition Cost</Box>
          <Box>${parseFloat(vehicleData.acquisitionCost || 0).toLocaleString()}</Box>
        </SpaceBetween>

        {/* Value & Depreciation */}
        <SpaceBetween size="xs">
          <Box variant="awsui-key-label">Current Value</Box>
          <Box fontWeight="bold">${currentValue.toLocaleString()}</Box>
          <Box variant="awsui-key-label">Total Depreciation</Box>
          <Box color="text-status-error">-${depreciation.toLocaleString()}</Box>
          <Box variant="awsui-key-label">Value Retention</Box>
          <Box>{msrp > 0 ? `${Math.round((currentValue / msrp) * 100)}%` : 'N/A'}</Box>
          <Box variant="awsui-key-label">Cost Per Mile</Box>
          <Box fontWeight="bold">${costPerMile.toFixed(2)}</Box>
        </SpaceBetween>

        {/* Insurance & Lease */}
        <SpaceBetween size="xs">
          <Box variant="awsui-key-label">Insurance</Box>
          <Box>{vehicleData.insuranceProvider || 'N/A'}</Box>
          <Box variant="awsui-key-label">Annual Premium</Box>
          <Box>${annualPremium.toLocaleString()}/yr</Box>
          {monthlyLease > 0 && (
            <>
              <Box variant="awsui-key-label">Lease Provider</Box>
              <Box>{vehicleData.leaseProvider}</Box>
              <Box variant="awsui-key-label">Monthly Lease</Box>
              <Box>${monthlyLease.toLocaleString()}/mo</Box>
            </>
          )}
          {vehicleData.leaseEndDate && (
            <>
              <Box variant="awsui-key-label">Lease End</Box>
              <Box>{vehicleData.leaseEndDate}</Box>
            </>
          )}
        </SpaceBetween>

        {/* Warranty & Registration */}
        <SpaceBetween size="xs">
          <Box variant="awsui-key-label">Warranty Status</Box>
          <StatusIndicator type={warrantyActive ? "success" : "error"}>
            {warrantyActive ? "Active" : "Expired"}
          </StatusIndicator>
          {warrantyActive && (
            <>
              <Box variant="awsui-key-label">Coverage Remaining</Box>
              <Box>{warrantyDays} days / {warrantyMiles.toLocaleString()} mi</Box>
            </>
          )}
          <Box variant="awsui-key-label">Warranty End</Box>
          <Box>{vehicleData.warrantyEndDate || 'N/A'}</Box>
          <Box variant="awsui-key-label">Registration</Box>
          <Box>{vehicleData.registrationCity}, {vehicleData.registrationState}</Box>
          <Box variant="awsui-key-label">Reg. Expiry</Box>
          <Box>{vehicleData.registrationExpiry || 'N/A'}</Box>
        </SpaceBetween>
      </ColumnLayout>

      {/* YTD Summary Bar */}
      <Box margin={{ top: "l" }} padding={{ top: "s" }} variant="div">
        <ColumnLayout columns={5} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Total Cost YTD</Box>
            <Box fontWeight="bold" fontSize="heading-m">${totalCostYTD.toLocaleString()}</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Annual Miles</Box>
            <Box>{parseFloat(vehicleData.annualMiles || 0).toLocaleString()} mi/yr</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Avg MPG</Box>
            <Box>{vehicleData.avgMpg || 'N/A'}</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Vehicle Class</Box>
            <Box>{vehicleData.vehicleClass || 'N/A'}</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Purchase Odometer</Box>
            <Box>{parseFloat(vehicleData.purchaseOdometer || 0).toLocaleString()} mi</Box>
          </div>
        </ColumnLayout>
      </Box>
    </Container>
  );
};

export default VehicleFinancialWidget;
