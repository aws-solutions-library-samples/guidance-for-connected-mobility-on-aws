// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import {
  Container,
  SpaceBetween,
  Box
} from '@cloudscape-design/components';

export default function CampaignsView() {
  return (
    <Container>
      <SpaceBetween size="l">
        <Box textAlign="center" padding="xxl">
          <Box variant="h2" margin={{ bottom: 'm' }}>
            Campaigns
          </Box>
          <Box variant="p" color="text-body-secondary">
            This page will contain campaign management functionality for AWS IoT FleetWise data collection.
          </Box>
        </Box>
      </SpaceBetween>
    </Container>
  );
}
