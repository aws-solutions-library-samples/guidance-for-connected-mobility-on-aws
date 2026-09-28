// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  Button,
  Grid,
  ColumnLayout,
} from '@cloudscape-design/components';

export default function TelemetryDashboard() {
  return (
    <Container
      header={
        <Header
          variant="h1"
          description="Monitor real-time telemetry data from your connected vehicles with comprehensive analytics and insights."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh">
                Refresh
              </Button>
              <Button iconName="download">
                Export Data
              </Button>
            </SpaceBetween>
          }
        >
          Telemetry Dashboard
        </Header>
      }
    >
      <SpaceBetween size="l">
        <ColumnLayout columns={3} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Active Vehicles</Box>
            <Box variant="awsui-value-large">1,247</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Data Points/Hour</Box>
            <Box variant="awsui-value-large">45.2K</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Average Latency</Box>
            <Box variant="awsui-value-large">125ms</Box>
          </div>
        </ColumnLayout>

        <Grid
          gridDefinition={[
            { colspan: { default: 12, xs: 6 } },
            { colspan: { default: 12, xs: 6 } }
          ]}
        >
          <Container
            header={
              <Header variant="h2">
                Real-time Metrics
              </Header>
            }
          >
            <Box textAlign="center" padding="xxl">
              <Box variant="h3" margin={{ bottom: 'm' }}>
                Telemetry Chart
              </Box>
              <Box variant="p" color="text-body-secondary">
                Real-time telemetry visualization will be displayed here.
              </Box>
            </Box>
          </Container>

          <Container
            header={
              <Header variant="h2">
                Signal Quality
              </Header>
            }
          >
            <Box textAlign="center" padding="xxl">
              <Box variant="h3" margin={{ bottom: 'm' }}>
                Signal Quality Metrics
              </Box>
              <Box variant="p" color="text-body-secondary">
                Signal quality and health indicators will be displayed here.
              </Box>
            </Box>
          </Container>
        </Grid>
      </SpaceBetween>
    </Container>
  );
}
