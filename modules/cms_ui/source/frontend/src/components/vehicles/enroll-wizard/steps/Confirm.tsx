// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import {
  SpaceBetween,
  Container,
  Header,
  KeyValuePairs,
  Table,
  Box,
} from '@cloudscape-design/components';
import type { EnrollWizardState } from '../state/reducer';

interface StepConfirmProps {
  state: EnrollWizardState;
}

const StepConfirm: React.FC<StepConfirmProps> = ({ state }) => {
  const capable = state.rows.filter((r) => r.preflightResult?.isCapable !== false);

  return (
    <SpaceBetween size="m">
      <Container header={<Header variant="h3">Enrollment summary</Header>}>
        <KeyValuePairs
          columns={3}
          items={[
            { label: 'Fleet ID', value: state.fleetId || '—' },
            { label: 'SKU', value: state.sku || '—' },
            { label: 'VINs to enroll', value: String(capable.length) },
            { label: 'Pre-flight failures', value: String(state.rows.length - capable.length) },
            { label: 'Client request ID', value: state.clientRequestId },
          ]}
        />
      </Container>

      <Table
        header={<Header variant="h3">Vehicles ({capable.length})</Header>}
        items={capable}
        columnDefinitions={[
          { id: 'vin', header: 'VIN', cell: (r) => r.vin },
          { id: 'driver', header: 'Driver', cell: (r) => r.driverName ?? r.driverId },
          {
            id: 'status',
            header: 'Pre-flight',
            cell: (r) =>
              r.preflightResult?.isCapable === true
                ? 'Capable ✓'
                : r.preflightResult
                  ? 'Not checked'
                  : '—',
          },
        ]}
        empty={<Box textAlign="center">No capable VINs</Box>}
      />
    </SpaceBetween>
  );
};

export default StepConfirm;
