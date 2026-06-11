// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import {
  Container,
  Header,
  Box,
  ColumnLayout,
  SpaceBetween,
  StatusIndicator,
  Table,
} from '@cloudscape-design/components';

const VehicleCostTab: React.FC = () => {
  return (
    <SpaceBetween size="l">
      <Container
        header={
          <Header variant="h2" description="VEH-1042 | 2023 Freightliner Cascadia | 142,380 mi | Region East">
            Vehicle Cost Details
          </Header>
        }
      >
        <ColumnLayout columns={4} variant="text-grid">
          <Box>
            <Box variant="awsui-key-label">Cost/Mile</Box>
            <Box variant="h1">$1.14</Box>
            <StatusIndicator type="warning">Above fleet avg</StatusIndicator>
          </Box>
          <Box>
            <Box variant="awsui-key-label">Maintenance Spend</Box>
            <Box variant="h1">$2,340</Box>
            <Box color="text-body-secondary">MTD</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">Fuel Spend</Box>
            <Box variant="h1">$1,890</Box>
            <Box color="text-body-secondary">MTD</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">vs Fleet Avg</Box>
            <Box variant="h1">+58%</Box>
            <StatusIndicator type="error">Above threshold</StatusIndicator>
          </Box>
        </ColumnLayout>
      </Container>

      <Container header={<Header variant="h2" counter="(8)">Vehicle Cost History</Header>}>
        <Table
          items={[
            { date: 'Mar 22', category: 'Fuel', amount: '$187.50', description: '52.1 gal @ $3.60/gal', location: 'Shell Station Rt 9' },
            { date: 'Mar 20', category: 'Maintenance', amount: '$450.00', description: 'Oil change + filter', location: 'Fleet Service Center' },
            { date: 'Mar 18', category: 'Fuel', amount: '$192.30', description: '53.4 gal @ $3.60/gal', location: 'Shell Station Rt 9' },
            { date: 'Mar 15', category: 'Charging', amount: '$42.80', description: '178 kWh @ $0.24/kWh', location: 'ChargePoint Hub' },
            { date: 'Mar 14', category: 'Fuel', amount: '$178.20', description: '49.5 gal @ $3.60/gal', location: 'BP Station Hwy 12' },
            { date: 'Mar 10', category: 'Maintenance', amount: '$1,890.00', description: 'Brake inspection + partial pad replacement', location: 'Fleet Service Center' },
            { date: 'Mar 8', category: 'Insurance', amount: '$285.00', description: 'Monthly premium', location: 'Progressive Commercial' },
            { date: 'Mar 5', category: 'Fuel', amount: '$183.60', description: '51.0 gal @ $3.60/gal', location: 'Shell Station Rt 9' },
          ]}
          columnDefinitions={[
            { id: 'date', header: 'Date', cell: (item: any) => item.date },
            { id: 'category', header: 'Category', cell: (item: any) => item.category },
            { id: 'amount', header: 'Amount', cell: (item: any) => item.amount },
            { id: 'description', header: 'Description', cell: (item: any) => item.description },
            { id: 'location', header: 'Location', cell: (item: any) => item.location },
          ]}
        />
      </Container>

      <Container header={<Header variant="h2">Cost Breakdown</Header>}>
        <Table
          items={[
            { category: 'Maintenance', amount: '$2,340.00', pct: '42%' },
            { category: 'Fuel', amount: '$741.60', pct: '32%' },
            { category: 'Insurance', amount: '$285.00', pct: '12%' },
            { category: 'Depreciation', amount: '$280.00', pct: '12%' },
            { category: 'Charging', amount: '$42.80', pct: '2%' },
          ]}
          columnDefinitions={[
            { id: 'category', header: 'Category', cell: (item: any) => item.category },
            { id: 'amount', header: 'Amount', cell: (item: any) => item.amount },
            { id: 'pct', header: '% of Total', cell: (item: any) => item.pct },
          ]}
        />
      </Container>

      <Container header={<Header variant="h2">Maintenance Forecast</Header>}>
        <Box padding="l">
          <StatusIndicator type="warning">
            Predicted: Brake pad full replacement needed within 500 miles — estimated cost $1,200.
            Scheduling now saves $1,200 vs emergency roadside repair ($2,400). Confidence: 94%
          </StatusIndicator>
        </Box>
      </Container>
    </SpaceBetween>
  );
};

export default VehicleCostTab;
