// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// VehicleCostTab: per-vehicle cost breakdown.
// Fabricated rows (hardcoded VEH-1042 header and hardcoded cost-history entries)
// removed per spec 2026-09-02-cms-fleet-intelligence-v1 T5.3. The component reads
// the vehicleId from the URL via useParams and will display live data once the
// /api/v1/fleet-intelligence/cpm endpoint supports per-vehicle grouping.

import React from 'react';
import { useParams } from 'react-router-dom';
import {
  Box,
  Container,
  Header,
  SpaceBetween,
  Table,
} from '@cloudscape-design/components';

const VehicleCostTab: React.FC = () => {
  const { vehicleId } = useParams<{ vehicleId: string }>();

  return (
    <SpaceBetween size="l">
      <Container
        header={
          <Header
            variant="h2"
            description={vehicleId || 'Vehicle'}
          >
            Vehicle Cost Details
          </Header>
        }
      >
        <Box textAlign="center" padding="l" color="text-body-secondary">
          Per-vehicle cost data will be available once the fleet-intelligence
          cost endpoint supports vehicle-level grouping. Vehicle: {vehicleId || '—'}
        </Box>
      </Container>

      <Container header={<Header variant="h2">Cost History</Header>}>
        <Table
          items={[]}
          columnDefinitions={[
            { id: 'date',        header: 'Date',        cell: () => null },
            { id: 'category',    header: 'Category',    cell: () => null },
            { id: 'amount',      header: 'Amount',      cell: () => null },
            { id: 'description', header: 'Description', cell: () => null },
            { id: 'location',    header: 'Location',    cell: () => null },
          ]}
          empty={
            <Box textAlign="center" padding="l" color="text-body-secondary">
              No cost history available
            </Box>
          }
        />
      </Container>
    </SpaceBetween>
  );
};

export default VehicleCostTab;
