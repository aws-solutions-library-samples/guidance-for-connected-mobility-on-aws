// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import {
  Container,
  SpaceBetween,
  Box
} from '@cloudscape-design/components';

export default function VehicleModelsView() {
  return (
    <Container>
      <SpaceBetween size="l">
        <Box textAlign="center" padding="xxl">
          <Box variant="h2" margin={{ bottom: 'm' }}>
            Vehicle Models
          </Box>
          <Box variant="p" color="text-body-secondary">
            This page will contain vehicle model management functionality for AWS IoT FleetWise.
          </Box>
        </Box>
      </SpaceBetween>
    </Container>
  );
}
