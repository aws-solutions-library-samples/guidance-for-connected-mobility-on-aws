// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  Button,
  ColumnLayout,
} from '@cloudscape-design/components';

export default function TripAnalyticsView() {
  return (
    <Container
      header={
        <Header
          variant="h1"
          description="Analyze trip data, route efficiency, and travel patterns to optimize fleet operations."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh">
                Refresh
              </Button>
              <Button iconName="download">
                Export Analytics
              </Button>
            </SpaceBetween>
          }
        >
          Trip Analytics
        </Header>
      }
    >
      <SpaceBetween size="l">
        <ColumnLayout columns={3} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Total Trips</Box>
            <Box variant="awsui-value-large">2,847</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Avg Trip Duration</Box>
            <Box variant="awsui-value-large">45min</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Fuel Efficiency</Box>
            <Box variant="awsui-value-large">28.5 MPG</Box>
          </div>
        </ColumnLayout>

        <Box textAlign="center" padding="xxl">
          <Box variant="h3" margin={{ bottom: 'm' }}>
            Trip Analytics Dashboard
          </Box>
          <Box variant="p" color="text-body-secondary">
            Trip analysis and route optimization insights will be displayed here.
          </Box>
        </Box>
      </SpaceBetween>
    </Container>
  );
}
