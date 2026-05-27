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

export default function DriverBehaviorView() {
  return (
    <Container
      header={
        <Header
          variant="h1"
          description="Analyze driver behavior patterns, safety metrics, and performance indicators to improve fleet safety."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh">
                Refresh
              </Button>
              <Button iconName="download">
                Export Report
              </Button>
            </SpaceBetween>
          }
        >
          Driver Behavior
        </Header>
      }
    >
      <SpaceBetween size="l">
        <ColumnLayout columns={3} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Active Drivers</Box>
            <Box variant="awsui-value-large">342</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Safety Score</Box>
            <Box variant="awsui-value-large">87.5</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Incidents</Box>
            <Box variant="awsui-value-large">12</Box>
          </div>
        </ColumnLayout>

        <Box textAlign="center" padding="xxl">
          <Box variant="h3" margin={{ bottom: 'm' }}>
            Driver Behavior Analytics
          </Box>
          <Box variant="p" color="text-body-secondary">
            Driver behavior analysis and safety metrics will be displayed here.
          </Box>
        </Box>
      </SpaceBetween>
    </Container>
  );
}
