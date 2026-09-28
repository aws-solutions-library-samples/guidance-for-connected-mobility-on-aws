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

export default function GeofenceEventsView() {
  return (
    <Container
      header={
        <Header
          variant="h1"
          description="Monitor geofence events, boundary violations, and location-based alerts for your fleet."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh">
                Refresh
              </Button>
              <Button iconName="download">
                Export Events
              </Button>
            </SpaceBetween>
          }
        >
          Geofence Events
        </Header>
      }
    >
      <SpaceBetween size="l">
        <ColumnLayout columns={3} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Active Geofences</Box>
            <Box variant="awsui-value-large">28</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Events Today</Box>
            <Box variant="awsui-value-large">156</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Violations</Box>
            <Box variant="awsui-value-large">7</Box>
          </div>
        </ColumnLayout>

        <Box textAlign="center" padding="xxl">
          <Box variant="h3" margin={{ bottom: 'm' }}>
            Geofence Event Monitoring
          </Box>
          <Box variant="p" color="text-body-secondary">
            Geofence events and location monitoring will be displayed here.
          </Box>
        </Box>
      </SpaceBetween>
    </Container>
  );
}
